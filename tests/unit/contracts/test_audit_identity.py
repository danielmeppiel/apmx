"""Identity row: receipts signed in CI, verified against the policy's signer workflows.

`gh attestation verify` is mocked. Its responses are derived from a real
recorded `gh attestation verify --format json` result (cli/cli v2.62.0,
golden/attestation/gh-attestation-verify.recorded.json): the certificate and
envelope shape are real, and the statement and signer identity are rewritten
to an apmx receipt. No test performs live signing or verification.
"""

import copy
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from test_apmx import caller, producer
from test_audit import _no_ambient_policy_or_pull_request, _rows, plain_receipt
from test_audit_defaults import NEW, _audit, _copy_receipt, _definition, _fake_apm

from apmx.audit import identity, policy

__all__ = ["_no_ambient_policy_or_pull_request", "caller", "plain_receipt", "producer"]
pytestmark = pytest.mark.component
RECORDED = Path(__file__).parent / "golden/attestation/gh-attestation-verify.recorded.json"
WORKFLOW = "acme/factories/.github/workflows/run.yml"
SIGNERS = (policy.Signer(WORKFLOW),)
ACTION = Path(__file__).resolve().parents[3] / ".github/actions/attest/predicate.py"


def _attestation(receipt: Path, **change) -> bytes:
    """A real recorded verification result, rewritten to sign this receipt."""
    (result,) = json.loads(RECORDED.read_bytes())
    result = copy.deepcopy(result)
    digest = hashlib.sha256((receipt / "index.json").read_bytes()).hexdigest()
    verified = result["verificationResult"]
    verified["statement"] = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{"name": "index.json", "digest": {"sha256": change.get("subject", digest)}}],
        "predicateType": change.get("predicate_type", identity.PREDICATE_TYPE),
        "predicate": {
            "receipt": {"name": "index.json", "sha256": change.get("receipt_sha", digest)},
            "definitionSha256": change.get("definition", _definition(receipt)),
            "checks": "separate-job",
        },
    }
    certificate = verified["signature"]["certificate"]
    signer = change.get("workflow", WORKFLOW)
    certificate["buildSignerURI"] = f"https://github.com/{signer}@refs/tags/v1"
    certificate["subjectAlternativeName"] = certificate["buildSignerURI"]
    certificate["runnerEnvironment"] = change.get("runner", "github-hosted")
    certificate["issuer"] = change.get("issuer", identity.ISSUER)
    return json.dumps([result]).encode()


class Gh:
    def __init__(self, *responses) -> None:
        self.responses, self.calls = list(responses), []

    def __call__(self, argv):
        self.calls.append(argv)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _verify(receipt: Path, gh: Gh, signers=SIGNERS, repository="acme/app"):
    return identity.verify(
        receipt / "index.json",
        _definition(receipt),
        signers,
        default_repository=repository,
        run=gh,
    )


def test_recorded_fixture_is_real_gh_output() -> None:
    (result,) = json.loads(RECORDED.read_bytes())
    certificate = result["verificationResult"]["signature"]["certificate"]
    assert certificate["issuer"] == identity.ISSUER
    assert certificate["buildSignerURI"].startswith("https://github.com/cli/cli/")
    assert "bundle" in result["attestation"]


def test_matching_attestation_from_an_allowed_workflow_is_signed(plain_receipt) -> None:
    gh = Gh((0, _attestation(plain_receipt), b""))
    result = _verify(plain_receipt, gh, (policy.Signer(WORKFLOW, "refs/tags/v1"),))
    assert result.status == "signed" and result.signer == WORKFLOW
    assert result.detail == f"signed by {WORKFLOW}@refs/tags/v1"
    assert result.notes[0] == "checks: separate job"
    (argv,) = gh.calls
    assert argv[:4] == ["gh", "attestation", "verify", str(plain_receipt / "index.json")]
    flags = dict(zip(argv[4::2], argv[5::2], strict=False))
    assert flags["--repo"] == "acme/app" and flags["--signer-workflow"] == WORKFLOW
    assert flags["--predicate-type"] == identity.PREDICATE_TYPE
    assert flags["--cert-oidc-issuer"] == identity.ISSUER
    assert "--deny-self-hosted-runners" in argv


def test_a_pinned_signer_ref_must_match_the_certificate(plain_receipt) -> None:
    gh = Gh((0, _attestation(plain_receipt), b""))
    result = _verify(plain_receipt, gh, (policy.Signer(WORKFLOW, "refs/heads/main"),))
    assert result.status == "unsigned"
    assert "signed at refs/tags/v1, not refs/heads/main" in result.notes[0]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"subject": "0" * 64}, "subject is not this receipt"),
        ({"receipt_sha": "0" * 64}, "different receipt"),
        ({"definition": "0" * 64}, "different factory definition"),
        ({"workflow": "acme/factories/.github/workflows/other.yml"}, "different workflow"),
        ({"runner": "self-hosted"}, "self-hosted"),
        ({"issuer": "https://example.invalid"}, "GitHub Actions OIDC"),
        ({"predicate_type": "https://slsa.dev/provenance/v1"}, "predicate type"),
    ],
)
def test_attestations_that_do_not_match_are_never_signed(plain_receipt, change, reason) -> None:
    result = _verify(plain_receipt, Gh((0, _attestation(plain_receipt, **change), b"")))
    assert result.status == "unsigned" and reason in result.notes[0]


def test_no_attestation_is_unsigned_and_errors_are_unavailable(plain_receipt) -> None:
    missing = _verify(plain_receipt, Gh((1, b"", b"Error: no attestations found\n")))
    assert missing.status == "unsigned"
    # Recorded from gh 2.102.0 (non-TTY) for an attestation signed by a different workflow.
    other = _verify(plain_receipt, Gh((1, b"", b'\nError: verifying with issuer "sigstore.dev"\n')))
    assert other.status == "unsigned" and "no attestation from this signer" in other.notes[0]
    network = _verify(plain_receipt, Gh((1, b"", b"Error: failed to fetch: timeout\n")))
    assert network.status == "unavailable" and "timeout" in network.notes[0]
    garbled = _verify(plain_receipt, Gh((0, b"not json", b"")))
    assert garbled.status == "unavailable"
    no_gh = _verify(plain_receipt, Gh(FileNotFoundError()))
    assert no_gh.status == "unavailable" and "not installed" in no_gh.detail
    no_repo = _verify(plain_receipt, Gh(), repository=None)
    assert no_repo.status == "unavailable" and "no repository" in no_repo.notes[0]


def test_a_signer_repo_names_where_the_factory_ran(plain_receipt) -> None:
    gh = Gh((0, _attestation(plain_receipt), b""))
    result = _verify(plain_receipt, gh, (policy.Signer(WORKFLOW, None, "acme/factories"),))
    assert result.status == "signed"
    assert gh.calls[0][gh.calls[0].index("--repo") + 1] == "acme/factories"


def test_each_signer_is_tried_until_one_verifies(plain_receipt) -> None:
    other = policy.Signer("acme/other/.github/workflows/x.yml")
    gh = Gh((1, b"", b"no attestations found"), (0, _attestation(plain_receipt), b""))
    result = _verify(plain_receipt, gh, (other, policy.Signer(WORKFLOW)))
    assert result.status == "signed"
    assert [call[call.index("--signer-workflow") + 1] for call in gh.calls] == [
        other.workflow,
        WORKFLOW,
    ]


def test_default_repository_comes_from_actions_or_the_origin(tmp_path) -> None:
    assert identity.repository(tmp_path, {"GITHUB_REPOSITORY": "acme/app"}) == "acme/app"
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:acme/app.git"], cwd=tmp_path, check=True
    )
    assert identity.repository(tmp_path, {}) == "acme/app"


# ------------------------------------------------------------------ apmx audit


def _signed_policy(tmp_path, monkeypatch, plain_receipt, require: bool = True) -> None:
    work = tmp_path / "work"
    _copy_receipt(plain_receipt, work, NEW)
    (work / "apm-policy.yml").write_text(
        "enforcement: block\nfactories:\n"
        f"  require_signed: {'true' if require else 'false'}\n"
        f"  signers:\n    - workflow: {WORKFLOW}\n"
    )
    monkeypatch.chdir(work)
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/app")
    _fake_apm(monkeypatch)


def test_signed_receipt_is_valid_and_authenticated(plain_receipt, tmp_path, monkeypatch) -> None:
    _signed_policy(tmp_path, monkeypatch, plain_receipt)
    receipt = tmp_path / "work/.apm/runs" / NEW / "receipt"
    monkeypatch.setattr(identity, "_gh", Gh((0, _attestation(receipt), b"")))
    result = _audit()
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0].endswith("   signed")
    rows = _rows(result.output)
    assert rows["Identity"] == f"[+] Identity      signed by {WORKFLOW}@refs/tags/v1"
    assert result.output.rstrip().endswith(f"[+] VALID   (authenticated: {WORKFLOW})")
    monkeypatch.setattr(identity, "_gh", Gh((0, _attestation(receipt), b"")))
    report = json.loads(_audit("--format", "json").output)
    assert report["signed"] is True and report["assurance"] == f"authenticated: {WORKFLOW}"
    assert report["facts"]["identity"]["signerRef"] == "refs/tags/v1"


def test_require_signed_rejects_unsigned_and_cannot_pass_unchecked(
    plain_receipt, tmp_path, monkeypatch
) -> None:
    _signed_policy(tmp_path, monkeypatch, plain_receipt)
    monkeypatch.setattr(identity, "_gh", Gh((1, b"", b"no attestations found")))
    result = _audit()
    assert result.exit_code == 1, result.output
    assert _rows(result.output)["Identity"].startswith(
        "[x] Identity      unsigned: no attestation from factories.signers"
    )
    monkeypatch.setattr(identity, "_gh", Gh((1, b"", b"HTTP 503: unavailable")))
    result = _audit()
    assert (
        result.exit_code == 2 and "INCOMPLETE   Identity: signature could not be" in result.output
    )
    result = _audit("--offline")
    assert result.exit_code == 2 and "signature not checked (offline)" in result.output


def test_signers_without_require_signed_only_warn(plain_receipt, tmp_path, monkeypatch) -> None:
    _signed_policy(tmp_path, monkeypatch, plain_receipt, require=False)
    monkeypatch.setattr(identity, "_gh", Gh((1, b"", b"no attestations found")))
    result = _audit()
    assert result.exit_code == 0, result.output
    assert _rows(result.output)["Identity"].startswith("[!] Identity      unsigned: no attestation")


def test_require_signed_without_signers_is_a_policy_failure(
    plain_receipt, tmp_path, monkeypatch
) -> None:
    work = tmp_path / "work"
    _copy_receipt(plain_receipt, work, NEW)
    (work / "apm-policy.yml").write_text("enforcement: block\nfactories:\n  require_signed: true\n")
    monkeypatch.chdir(work)
    _fake_apm(monkeypatch)
    monkeypatch.setattr(identity, "_gh", Gh())
    result = _audit()
    assert result.exit_code == 1
    assert "factories.require_signed is set but factories.signers is empty" in result.output


# ------------------------------------------------------------------ attest action


def _load_predicate_module():
    spec = importlib.util.spec_from_file_location("apmx_attest_predicate", ACTION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _apmx_shim(tmp_path) -> str:
    source = Path(__file__).resolve().parents[3] / "src"
    if os.name == "nt":
        shim = tmp_path / "apmx.cmd"
        shim.write_text(f'@set PYTHONPATH={source}\n@"{sys.executable}" -m apmx %*\n')
    else:
        shim = tmp_path / "apmx"
        shim.write_text(f'#!/bin/sh\nPYTHONPATH="{source}" exec "{sys.executable}" -m apmx "$@"\n')
        shim.chmod(0o755)
    return str(shim)


def test_attest_action_signs_only_a_valid_receipt_and_ignores_pr_context(
    plain_receipt, tmp_path, monkeypatch
) -> None:
    module = _load_predicate_module()
    assert module.PREDICATE_TYPE == identity.PREDICATE_TYPE
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(tmp_path / "missing-event.json"))
    shim = _apmx_shim(tmp_path)
    predicate = module.build(plain_receipt, "separate-job", shim)
    digest = hashlib.sha256((plain_receipt / "index.json").read_bytes()).hexdigest()
    assert predicate["receipt"] == {"name": "index.json", "sha256": digest}
    assert predicate["definitionSha256"] == _definition(plain_receipt)
    assert predicate["outputs"] == ["result.txt"] and predicate["checks"] == "separate-job"
    with pytest.raises(ValueError, match="checks must be"):
        module.build(plain_receipt, "trust-me", shim)
    (plain_receipt / "smuggled.txt").write_text("x")
    with pytest.raises(ValueError, match="refusing to sign"):
        module.build(plain_receipt, "separate-job", shim)
