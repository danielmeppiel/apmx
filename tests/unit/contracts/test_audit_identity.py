"""Identity row: a SLSA VSA signed in CI, verified against the policy's signer workflows.

`gh attestation verify` is mocked. Its responses are derived from a real
recorded `gh attestation verify --format json` result (cli/cli v2.62.0,
golden/attestation/gh-attestation-verify.recorded.json): the certificate and
envelope shape are real, and the statement and signer identity are rewritten
to an apmx verification summary. golden/attestation/apmx-demo-shop-run-v0.6.0.*
keeps the real certificate of run.yml@v0.6.0 (danielmeppiel/apmx-demo-shop run
39651797454), whose signer digest is an annotated tag object. No test performs
live signing or verification.
"""

import base64
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

from apmx.audit import command, identity, policy, verifier

__all__ = ["_no_ambient_policy_or_pull_request", "caller", "plain_receipt", "producer"]
pytestmark = pytest.mark.component
RECORDED = Path(__file__).parent / "golden/attestation/gh-attestation-verify.recorded.json"
SHOP = Path(__file__).parent / "golden/attestation/apmx-demo-shop-run-v0.6.0"
WORKFLOW = "acme/factories/.github/workflows/run.yml"
SIGNERS = (policy.Signer(WORKFLOW),)
# buildSignerDigest of the recorded cli/cli certificate.
RECORDED_SHA = "cd3f2ad064fbeca17d330e321fef0591eaa0fea5"
# run.yml@v0.6.0: v0.6.0 is an annotated tag; its tag object is what the certificate reports.
V060_TAG_OBJECT = "367ca846435d34f2e89f870335eeb709f3954c49"
V060_COMMIT = "cadfa207e4645306199ddb85271c5f3e13340924"
ACTION = Path(__file__).resolve().parents[3] / ".github/actions/attest/predicate.py"


def _summary(receipt: Path, *, signer: str = WORKFLOW, sha: str = RECORDED_SHA, **change) -> dict:
    """A PASSED SLSA VSA v1 over this receipt's delivered files, binding its lines."""
    parsed, failure = verifier.inspect(receipt)
    assert failure is None, failure
    subjects = [
        {"name": name, "digest": {"sha256": digest}} for name, digest in parsed.subjects.items()
    ]
    if "subject" in change:
        subjects = [{"name": "result.txt", "digest": {"sha256": change["subject"]}}]
    lines = list(parsed.facts["lineDigests"])
    if "lines" in change:
        lines = change["lines"]
    definition = change.get("definition", parsed.definition_sha256)
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": subjects,
        "predicateType": change.get("predicate_type", identity.PREDICATE_TYPE),
        "predicate": {
            "verifier": {"id": change.get("verifier", f"https://github.com/{signer}@{sha}")},
            "timeVerified": "2026-10-21T18:52:30Z",
            "resourceUri": "git+https://github.com/acme/app@" + "a" * 40,
            "policy": {"uri": f"apmx:factory/{definition}", "digest": {"sha256": definition}},
            "inputAttestations": [
                {"uri": f"receipt.intoto.jsonl#{n}", "digest": {"sha256": digest}}
                for n, digest in enumerate(lines, 1)
            ],
            "verificationResult": change.get("result", "PASSED"),
            "verifiedLevels": [],
        },
    }


def _signed(result: dict, statement: dict) -> dict:
    result["verificationResult"]["statement"] = statement
    envelope = result["attestation"]["bundle"]["dsseEnvelope"]
    envelope["payload"] = base64.b64encode(json.dumps(statement).encode()).decode()
    return result


def _attestation(receipt: Path, **change) -> bytes:
    """A real recorded verification result, rewritten to sign this receipt's summary."""
    (result,) = json.loads(RECORDED.read_bytes())
    result = copy.deepcopy(result)
    signer = change.pop("workflow", WORKFLOW)
    runner = change.pop("runner", "github-hosted")
    issuer = change.pop("issuer", identity.ISSUER)
    _signed(result, _summary(receipt, **change))
    certificate = result["verificationResult"]["signature"]["certificate"]
    certificate["buildSignerURI"] = f"https://github.com/{signer}@refs/tags/v1"
    certificate["subjectAlternativeName"] = certificate["buildSignerURI"]
    certificate["runnerEnvironment"] = runner
    certificate["issuer"] = issuer
    return json.dumps([result]).encode()


LISTED = (0, json.dumps({"attestations": [{"bundle": {"fixture": True}}]}).encode(), b"")


class Gh:
    """Scripted gh: listing the store's bundles always answers; other calls are scripted."""

    def __init__(self, *responses, listed=LISTED) -> None:
        self.responses, self.calls, self.listed = list(responses), [], listed

    def __call__(self, argv):
        if argv[:2] == ["gh", "api"] and "/attestations/" in argv[2]:
            return self.listed
        self.calls.append(argv)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _verify(receipt: Path, gh: Gh, signers=SIGNERS, repository="acme/app"):
    parsed, failure = verifier.inspect(receipt)
    assert failure is None, failure
    return identity.verify(
        receipt.parent / "outputs/result.txt",
        command._bundle_binding(parsed),
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
    assert result.detail == f"signed by {WORKFLOW}@refs/tags/v1 at {RECORDED_SHA[:12]}"
    assert result.notes[-1] == "[!] signer pinned by mutable ref refs/tags/v1; pin workflow_sha"
    assert result.summary["predicateType"] == verifier.VSA
    (argv,) = gh.calls
    artifact = str(plain_receipt.parent / "outputs/result.txt")
    assert argv[:4] == ["gh", "attestation", "verify", artifact]
    assert "--bundle" in argv
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


def test_real_run_yml_v0_6_0_pins_by_commit_or_tag_object() -> None:
    """Unmodified recorded output: run.yml@v0.6.0 signs as the annotated tag's object SHA.

    A commit pin resolves that tag object with a recorded, trimmed `gh api
    repos/danielmeppiel/apmx/git/tags/<sha>` response (tagger omitted).
    """
    (result,) = json.loads((SHOP.parent / f"{SHOP.name}.recorded.json").read_bytes())
    certificate = result["verificationResult"]["signature"]["certificate"]
    run_yml = "danielmeppiel/apmx/.github/workflows/run.yml"
    assert certificate["buildSignerURI"] == f"https://github.com/{run_yml}@refs/tags/v0.6.0"
    assert certificate["buildSignerDigest"] == V060_TAG_OBJECT
    index = SHOP.parent / f"{SHOP.name}.index.json"
    summary = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [
            {
                "name": "index.json",
                "digest": {"sha256": hashlib.sha256(index.read_bytes()).hexdigest()},
            }
        ],
        "predicateType": verifier.VSA,
        "predicate": {
            "verifier": {"id": f"https://github.com/{run_yml}@{V060_TAG_OBJECT}"},
            "verificationResult": "PASSED",
        },
    }
    recorded = json.dumps([_signed(result, summary)]).encode()
    tag = (SHOP.parent / "apmx-v0.6.0-git-tag.recorded.json").read_bytes()

    def check(sha, *api):
        signer = policy.Signer(run_yml, "refs/tags/v0.6.0", "danielmeppiel/apmx-demo-shop", sha)
        gh = Gh((0, recorded, b""), *api)
        verified = identity.verify(
            index, lambda _: None, (signer,), default_repository=None, run=gh
        )
        return verified, gh

    by_commit, gh = check(V060_COMMIT, (0, tag, b""))
    assert by_commit.status == "signed"
    assert by_commit.detail == (
        f"signed by {run_yml}@refs/tags/v0.6.0 at cadfa207e464 (tag object 367ca846435d)"
    )
    assert gh.calls[1] == ["gh", "api", f"repos/danielmeppiel/apmx/git/tags/{V060_TAG_OBJECT}"]
    assert by_commit.facts["signerSha"] == V060_TAG_OBJECT
    assert by_commit.facts["signerCommit"] == by_commit.facts["requiredSignerSha"] == V060_COMMIT
    assert not any(note.startswith("[!]") for note in by_commit.notes)

    by_tag_object, gh = check(V060_TAG_OBJECT)
    assert by_tag_object.status == "signed" and len(gh.calls) == 1
    assert by_tag_object.detail == f"signed by {run_yml}@refs/tags/v0.6.0 at 367ca846435d"
    assert by_tag_object.facts["signerPin"] == "workflow_sha"

    other, _ = check("f" * 40, (0, tag, b""))
    assert other.status == "mismatch"
    assert other.detail == (
        f"signed by {run_yml} at cadfa207e464 (tag object 367ca846435d), "
        "policy requires ffffffffffff"
    )
    unresolved, _ = check(V060_COMMIT, (1, b"", b"error connecting to api.github.com\n"))
    assert unresolved.status == "unresolved"
    assert "could not resolve signer digest 367ca846435d" in unresolved.notes[0]
    # A response for another object, or a tag of a tag, is never a match.
    forged = json.loads(tag)
    forged["object"]["type"] = "tag"
    nested, _ = check(V060_COMMIT, (0, json.dumps(forged).encode(), b""))
    assert nested.status == "unresolved" and "unexpected tag object" in nested.notes[0]


NOT_A_TAG = (1, b'{"message":"Not Found","status":"404"}', b"gh: Not Found (HTTP 404)\n")


def test_workflow_sha_must_equal_the_certificate_build_signer_digest(plain_receipt) -> None:
    pinned = policy.Signer(WORKFLOW, workflow_sha=RECORDED_SHA)
    signed = _verify(plain_receipt, Gh((0, _attestation(plain_receipt), b"")), (pinned,))
    assert signed.status == "signed"
    assert signed.detail == f"signed by {WORKFLOW}@refs/tags/v1 at {RECORDED_SHA[:12]}"
    assert signed.notes == [f"run {_run_uri()}"]
    other = policy.Signer(WORKFLOW, workflow_sha="0" * 40)
    gh = Gh((0, _attestation(plain_receipt), b""), NOT_A_TAG)
    wrong = _verify(plain_receipt, gh, (other,))
    assert gh.calls[1] == ["gh", "api", f"repos/acme/factories/git/tags/{RECORDED_SHA}"]
    assert wrong.status == "mismatch"
    assert (
        wrong.detail == f"signed by {WORKFLOW} at {RECORDED_SHA[:12]}, policy requires 000000000000"
    )
    assert wrong.facts["signerSha"] == RECORDED_SHA and wrong.facts["requiredSignerSha"] == "0" * 40
    assert wrong.facts["signerCommit"] is None
    # Another allowed entry that matches still signs; a mismatch is not sticky.
    gh = Gh((0, _attestation(plain_receipt), b""), NOT_A_TAG, (0, _attestation(plain_receipt), b""))
    assert _verify(plain_receipt, gh, (other, pinned)).status == "signed"
    # A network failure on another entry keeps the result unavailable, never a verdict.
    gh = Gh((0, _attestation(plain_receipt), b""), NOT_A_TAG, (1, b"", b"Error: timeout\n"))
    flaky = policy.Signer("acme/other/.github/workflows/x.yml")
    assert _verify(plain_receipt, gh, (other, flaky)).status == "unavailable"


def test_unpinned_signer_warns_and_reports_its_commit(plain_receipt) -> None:
    result = _verify(plain_receipt, Gh((0, _attestation(plain_receipt), b"")))
    assert result.status == "signed"
    assert result.notes[-1] == "[!] signer not pinned (any ref); pin workflow_sha"
    assert result.facts["signerSha"] == RECORDED_SHA and result.facts["signerPin"] == "none"


def _run_uri() -> str:
    (result,) = json.loads(RECORDED.read_bytes())
    return result["verificationResult"]["signature"]["certificate"]["runInvocationURI"]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"subject": "0" * 64}, "subject is not this file"),
        ({"lines": ["0" * 64]}, "binds different statements"),
        ({"definition": "0" * 64}, "different factory definition"),
        ({"result": "FAILED"}, "not PASSED"),
        ({"verifier": "https://github.com/acme/x/.github/workflows/y.yml@1"}, "verifier.id"),
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
    empty = _verify(plain_receipt, Gh(listed=(1, b"", b"gh: Not Found (HTTP 404)\n")))
    assert empty.status == "unsigned"
    down = _verify(plain_receipt, Gh(listed=(1, b"", b"gh: HTTP 502: Bad Gateway\n")))
    assert down.status == "unavailable" and "502" in down.notes[0]


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


def _signed_policy(
    tmp_path, monkeypatch, plain_receipt, require: bool = True, signer: str = ""
) -> None:
    work = tmp_path / "work"
    _copy_receipt(plain_receipt, work, NEW)
    (work / "apm-policy.yml").write_text(
        "enforcement: block\nfactories:\n"
        f"  require_signed: {'true' if require else 'false'}\n"
        f"  signers:\n    - workflow: {WORKFLOW}\n{signer}"
    )
    monkeypatch.chdir(work)
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/app")
    _fake_apm(monkeypatch)


def test_signed_receipt_is_valid_and_authenticated(plain_receipt, tmp_path, monkeypatch) -> None:
    _signed_policy(tmp_path, monkeypatch, plain_receipt)
    receipt = tmp_path / "work/.apm/runs" / NEW / "receipt.intoto.jsonl"
    monkeypatch.setattr(identity, "_gh", Gh((0, _attestation(receipt), b"")))
    result = _audit()
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0].endswith("   signed")
    rows = _rows(result.output)
    assert rows["Identity"] == (
        f"[+] Identity      signed by {WORKFLOW}@refs/tags/v1 at {RECORDED_SHA[:12]}"
    )
    assert result.output.rstrip().endswith(f"[+] VALID   (authenticated: {WORKFLOW})")
    monkeypatch.setattr(identity, "_gh", Gh((0, _attestation(receipt), b"")))
    report = json.loads(_audit("--format", "json").output)
    assert report["signed"] is True and report["assurance"] == f"authenticated: {WORKFLOW}"
    assert report["facts"]["identity"]["signerRef"] == "refs/tags/v1"


def test_audit_workflow_sha_pin_passes_and_reports_facts(
    plain_receipt, tmp_path, monkeypatch
) -> None:
    _signed_policy(
        tmp_path, monkeypatch, plain_receipt, signer=f"      workflow_sha: {RECORDED_SHA}\n"
    )
    receipt = tmp_path / "work/.apm/runs" / NEW / "receipt.intoto.jsonl"
    monkeypatch.setattr(identity, "_gh", Gh((0, _attestation(receipt), b"")))
    result = _audit()
    assert result.exit_code == 0, result.output
    assert "[!]" not in result.output.split("Identity", 1)[1]
    monkeypatch.setattr(identity, "_gh", Gh((0, _attestation(receipt), b"")))
    report = json.loads(_audit("--format", "json").output)
    facts = report["facts"]["identity"]
    assert facts["status"] == "signed" and facts["signerPin"] == "workflow_sha"
    assert facts["signerSha"] == facts["requiredSignerSha"] == RECORDED_SHA


def test_audit_rejects_a_receipt_signed_at_another_workflow_sha(
    plain_receipt, tmp_path, monkeypatch
) -> None:
    pin = f"      workflow_sha: {'a' * 40}\n"
    # Rejected even without require_signed: the trusted workflow signed at the wrong commit.
    _signed_policy(tmp_path, monkeypatch, plain_receipt, require=False, signer=pin)
    receipt = tmp_path / "work/.apm/runs" / NEW / "receipt.intoto.jsonl"
    monkeypatch.setattr(identity, "_gh", Gh((0, _attestation(receipt), b""), NOT_A_TAG))
    result = _audit()
    assert result.exit_code == 1, result.output
    assert _rows(result.output)["Identity"] == (
        f"[x] Identity      signed by {WORKFLOW} at {RECORDED_SHA[:12]}, policy requires aaaaaaaaaaaa"
    )
    assert "   unsigned" in result.output.splitlines()[0]
    monkeypatch.setattr(identity, "_gh", Gh((0, _attestation(receipt), b""), NOT_A_TAG))
    report = json.loads(_audit("--format", "json").output)
    assert report["signed"] is False and report["facts"]["identity"]["status"] == "mismatch"
    assert report["facts"]["identity"]["signerSha"] == RECORDED_SHA
    # factories.enforcement: warn stages the rollout, as for every factories rule.
    policy_file = tmp_path / "work/apm-policy.yml"
    policy_file.write_text(policy_file.read_text() + "  enforcement: warn\n")
    monkeypatch.setattr(identity, "_gh", Gh((0, _attestation(receipt), b""), NOT_A_TAG))
    result = _audit()
    assert result.exit_code == 0, result.output
    assert _rows(result.output)["Identity"].startswith("[!] Identity      signed by")


def test_audit_accepts_the_commit_of_a_tag_object_and_never_passes_unresolved(
    plain_receipt, tmp_path, monkeypatch
) -> None:
    commit = "c" * 40
    # Unresolvable is INCOMPLETE (exit 2) even without require_signed: never a pass.
    _signed_policy(
        tmp_path,
        monkeypatch,
        plain_receipt,
        require=False,
        signer=f"      workflow_sha: {commit}\n",
    )
    receipt = tmp_path / "work/.apm/runs" / NEW / "receipt.intoto.jsonl"
    tag = json.loads((SHOP.parent / "apmx-v0.6.0-git-tag.recorded.json").read_bytes())
    tag["sha"], tag["object"]["sha"] = RECORDED_SHA, commit
    resolved = (0, json.dumps(tag).encode(), b"")
    monkeypatch.setattr(identity, "_gh", Gh((0, _attestation(receipt), b""), resolved))
    result = _audit()
    assert result.exit_code == 0, result.output
    assert _rows(result.output)["Identity"] == (
        f"[+] Identity      signed by {WORKFLOW}@refs/tags/v1 at cccccccccccc "
        f"(tag object {RECORDED_SHA[:12]})"
    )
    offline = (1, b"", b"error connecting to api.github.com\n")
    monkeypatch.setattr(identity, "_gh", Gh((0, _attestation(receipt), b""), offline))
    result = _audit()
    assert result.exit_code == 2, result.output
    assert _rows(result.output)["Identity"].startswith(
        "[!] Identity      signer commit could not be checked against workflow_sha"
    )
    assert "INCOMPLETE   Identity:" in result.output


def test_audit_warns_when_a_signer_is_pinned_only_by_a_mutable_ref(
    plain_receipt, tmp_path, monkeypatch
) -> None:
    work = tmp_path / "work"
    _copy_receipt(plain_receipt, work, NEW)
    (work / "apm-policy.yml").write_text(
        "factories:\n  require_signed: true\n  enforcement: block\n"
        f"  signers:\n    - workflow: {WORKFLOW}@refs/tags/v1\n"
    )
    monkeypatch.chdir(work)
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/app")
    _fake_apm(monkeypatch)
    receipt = work / ".apm/runs" / NEW / "receipt.intoto.jsonl"
    monkeypatch.setattr(identity, "_gh", Gh((0, _attestation(receipt), b"")))
    result = _audit()
    assert result.exit_code == 0, result.output
    assert _rows(result.output)["Identity"].startswith("[+] Identity      signed by")
    assert (
        " " * 18 + "[!] signer pinned by mutable ref refs/tags/v1; pin workflow_sha"
    ) in result.output.splitlines()
    monkeypatch.setattr(identity, "_gh", Gh((0, _attestation(receipt), b"")))
    report = json.loads(_audit("--format", "json").output)
    assert report["facts"]["identity"]["signerPin"] == "ref"
    assert report["facts"]["identity"]["requiredSignerSha"] is None


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
    assert module.VSA == identity.PREDICATE_TYPE
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(tmp_path / "missing-event.json"))
    shim = _apmx_shim(tmp_path)
    report = module._audit(plain_receipt, shim)
    assert report["facts"]["definitionSha256"] == _definition(plain_receipt)
    assert report["facts"]["evidence"] == "complete"
    document = module.summary(
        plain_receipt,
        prefix="",
        verifier=f"https://github.com/{WORKFLOW}@{RECORDED_SHA}",
        resource="git+https://github.com/acme/app@" + "a" * 40,
        report=report,
    )
    assert [item["name"] for item in document["subject"]] == ["result.txt"]
    blob = next((plain_receipt.parent / "evidence/sha256").iterdir())
    blob.chmod(0o600)
    blob.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="refusing to sign"):
        module._audit(plain_receipt, shim)
    blob.unlink()
    with pytest.raises(ValueError, match="evidence is not complete"):
        module._audit(plain_receipt, shim)
