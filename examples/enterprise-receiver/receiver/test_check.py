"""Unit tests for the enterprise-receiver candidate check (``check.py``).

These tests build small, throwaway git repositories on disk (mirroring the
real ``examples/enterprise-receiver/...`` layout) and exercise the public
policy functions directly, so each receiver control -- integrity, factory
allowlist, candidate binding, capability revocation, signer -- is proven to
reject the negative case it claims to catch and accept the matching positive
case. The real evidence fixtures are generated once via
``fixtures/make_fixture.py`` (the genuine apmx contract engine), not
hand-authored, per the project's fixture-labeling requirement.
"""

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_EXAMPLE_ROOT = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_EXAMPLE_ROOT / "fixtures"))

import check as receiver_check
import make_fixture

_POLICY_RELATIVE = "examples/enterprise-receiver/policy"
_APP_RELATIVE = "examples/enterprise-receiver/app/greeting.py"
_BUGGY_GREETING = (_EXAMPLE_ROOT / "app" / "greeting.py").read_bytes()
_APPROVED_SIGNER_DIGEST = "a" * 40
_UNAPPROVED_SIGNER_DIGEST = "b" * 40
_FIXED_GREETING = make_fixture._PATCHED_GREETING.encode()
_TEST_EXECUTION_IMAGE = (
    "python:3.12-slim@sha256:2b4f19dae3a777dfc3b76730bda1e82e1f66ab2a2686fa93ca78edbfb4f04ffe"
)


def _fake_passing_execution_results(bundle_dir: Path) -> list[dict]:
    """A canned "every case matched" execution result, used to monkeypatch
    ``run_execution_bundle`` in tests that are not themselves about the
    execution boundary (binding/factory/capability-revocation/signer) --
    this is explicitly NOT a substitute for ``test_real_docker_execution_*``
    below, which actually invokes the real container pipeline end-to-end.
    """
    manifest = json.loads((bundle_dir / "manifest.json").read_bytes())
    results = []
    for entry in manifest["checks"]:
        if entry["candidateMissing"]:
            results.append(
                {
                    "name": entry["name"],
                    "candidateSha256": None,
                    "imageDigestUsed": manifest["executionImage"],
                    "cases": [],
                }
            )
            continue
        results.append(
            {
                "name": entry["name"],
                "candidateSha256": entry["expectedCandidateSha256"],
                "imageDigestUsed": manifest["executionImage"],
                "cases": [
                    {"ok": True, "observed": case["expectedOutput"]} for case in entry["cases"]
                ],
            }
        )
    return results


def _fake_failing_execution_results(bundle_dir: Path) -> list[dict]:
    """Like ``_fake_passing_execution_results`` but every case reports the
    wrong observed value -- used to prove the signer/assessment layers
    still reject an execution result that does not match, without
    depending on a real container actually producing wrong output."""
    manifest = json.loads((bundle_dir / "manifest.json").read_bytes())
    results = []
    for entry in manifest["checks"]:
        results.append(
            {
                "name": entry["name"],
                "candidateSha256": entry.get("expectedCandidateSha256"),
                "imageDigestUsed": manifest["executionImage"],
                "cases": [{"ok": True, "observed": "wrong value"} for _ in entry["cases"]],
            }
        )
    return results


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


def _init_repo(tmp_path: Path, *, approved_digests: list[str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Receiver Test")
    policy_dir = repo / _POLICY_RELATIVE
    policy_dir.mkdir(parents=True)
    (policy_dir / "approved-factories.json").write_text(
        json.dumps({"definitionSha256": approved_digests})
    )
    (policy_dir / "binding-map.json").write_text(
        json.dumps(
            {
                "baseline": {"greeting.py": _APP_RELATIVE},
                "candidate": {"candidate/greeting.py": _APP_RELATIVE},
            }
        )
    )
    (policy_dir / "signers.json").write_text(
        json.dumps(
            {
                "repo": "example/example",
                "signerWorkflow": "example/example/.github/workflows/x.yml",
                "predicateType": "https://example.invalid/receiver-ci-assessment/v1",
                "sourceRef": "refs/heads/examples/enterprise-receiver",
                "expectedPredicate": {
                    "assessedBy": "receiver-attest.yml",
                    "assessment": "accepted",
                },
                "requiredAssessedChecks": ["integrity", "factory", "capability-revocation"],
                "approvedSignerSourceDigests": [_APPROVED_SIGNER_DIGEST],
            }
        )
    )
    (policy_dir / "revoked-capabilities.json").write_text(json.dumps({"lockIdentity": []}))
    (policy_dir / "required-checks.json").write_text(
        json.dumps(
            {
                "executionImage": _TEST_EXECUTION_IMAGE,
                "checks": [
                    {
                        "name": "greeting-salutation",
                        "candidatePath": _APP_RELATIVE,
                        "candidateScratchRelative": "candidate_module",
                        "entrypoint": {"module": "candidate_module", "function": "greet"},
                        "cases": [{"input": "World", "expectedOutput": "Hello, World!"}],
                    }
                ],
            }
        )
    )
    app_dir = repo / "examples" / "enterprise-receiver" / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "greeting.py").write_bytes(_BUGGY_GREETING)
    checks_dir = app_dir / "checks"
    checks_dir.mkdir()
    (checks_dir / "test_greeting.py").write_bytes(
        (_EXAMPLE_ROOT / "app" / "checks" / "test_greeting.py").read_bytes()
    )
    # The container isolation scripts must themselves be readable from the
    # trusted base ref (write_execution_bundle reads them via
    # trusted_bytes), exactly mirroring the real repo's own layout.
    container_dir = repo / "examples" / "enterprise-receiver" / "receiver" / "container"
    container_dir.mkdir(parents=True)
    (container_dir / "container_driver.py").write_bytes(
        (_HERE / "container" / "container_driver.py").read_bytes()
    )
    (container_dir / "executor.py").write_bytes((_HERE / "container" / "executor.py").read_bytes())
    (container_dir / "run_bundle.py").write_bytes(
        (_HERE / "container" / "run_bundle.py").read_bytes()
    )
    (container_dir / "bounded_io.py").write_bytes(
        (_HERE / "container" / "bounded_io.py").read_bytes()
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _patch_candidate(repo: Path) -> str:
    (repo / _APP_RELATIVE).write_bytes(_FIXED_GREETING)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "candidate")
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture(scope="module")
def valid_evidence(tmp_path_factory) -> Path:
    destination = tmp_path_factory.mktemp("evidence") / "package"
    make_fixture.make_fixture(destination, variant="approved")
    return destination


@pytest.fixture(scope="module")
def unapproved_evidence(tmp_path_factory) -> Path:
    destination = tmp_path_factory.mktemp("evidence-unapproved") / "package"
    make_fixture.make_fixture(destination, variant="rogue-factory")
    return destination


@pytest.fixture()
def schemas_dir() -> Path:
    import os

    probe = os.environ.get("APMX_EVIDENCE_SCHEMAS")
    if probe:
        return Path(probe)
    pytest.skip("APMX_EVIDENCE_SCHEMAS not set; offline schema cache unavailable")


def _evidence_digest(evidence_dir: Path) -> str:
    index = json.loads((evidence_dir / "index.json").read_bytes())
    return index["definition"]["digest"]["sha256"]


def test_positive_case_is_accepted(tmp_path, monkeypatch, valid_evidence, schemas_dir):
    digest = _evidence_digest(valid_evidence)
    repo = _init_repo(tmp_path, approved_digests=[digest])
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _patch_candidate(repo)
    case_dir = repo / "cases" / "positive"
    _copy_tree(valid_evidence, case_dir / "evidence")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add case evidence")
    head_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    # This test is about binding/factory/signer policy wiring, not the
    # execution boundary itself (covered separately below, including a real
    # end-to-end container test); faking a passing observation here avoids
    # requiring a docker daemon for every policy-level test.
    monkeypatch.setattr(receiver_check, "run_execution_bundle", _fake_passing_execution_results)
    result = receiver_check.check_case(
        case="positive",
        case_dir_relative="cases/positive",
        base_sha=base_sha,
        head_sha=head_sha,
        schemas_dir=schemas_dir,
        skip_signer=True,
    )
    assert result.definition_sha256 == digest
    assert "binding" in result.checks


def test_tampered_patch_is_rejected(tmp_path, monkeypatch, valid_evidence, schemas_dir):
    digest = _evidence_digest(valid_evidence)
    repo = _init_repo(tmp_path, approved_digests=[digest])
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / _APP_RELATIVE).write_text(
        '"""Toy greeting."""\n\n\ndef greet(name: str) -> str:\n    return f"Yo, {name}!"\n'
    )
    case_dir = repo / "cases" / "tampered"
    _copy_tree(valid_evidence, case_dir / "evidence")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "tampered")
    head_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    with pytest.raises(receiver_check.ReceiverFailure) as excinfo:
        receiver_check.check_case(
            case="tampered",
            case_dir_relative="cases/tampered",
            base_sha=base_sha,
            head_sha=head_sha,
            schemas_dir=schemas_dir,
            skip_signer=True,
        )
    assert excinfo.value.policy == "binding"


def test_unapproved_factory_is_rejected(
    tmp_path, monkeypatch, valid_evidence, unapproved_evidence, schemas_dir
):
    approved_digest = _evidence_digest(valid_evidence)
    repo = _init_repo(tmp_path, approved_digests=[approved_digest])
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _patch_candidate(repo)
    case_dir = repo / "cases" / "unapproved"
    _copy_tree(unapproved_evidence, case_dir / "evidence")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add unapproved case evidence")
    head_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    with pytest.raises(receiver_check.ReceiverFailure) as excinfo:
        receiver_check.check_case(
            case="unapproved",
            case_dir_relative="cases/unapproved",
            base_sha=base_sha,
            head_sha=head_sha,
            schemas_dir=schemas_dir,
            skip_signer=True,
        )
    assert excinfo.value.policy == "factory"


def test_missing_evidence_is_rejected(tmp_path, monkeypatch, schemas_dir):
    repo = _init_repo(tmp_path, approved_digests=[])
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    head_sha = _patch_candidate(repo)

    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    with pytest.raises(receiver_check.ReceiverFailure) as excinfo:
        receiver_check.check_case(
            case="missing",
            case_dir_relative="cases/missing",
            base_sha=base_sha,
            head_sha=head_sha,
            schemas_dir=schemas_dir,
            skip_signer=True,
        )
    assert excinfo.value.policy == "missing-evidence"


def _fake_gh_run(returncode: int, stdout: bytes, stderr: bytes):
    real_run = subprocess.run

    def fake_run(args, capture_output, check):
        if args[0] == "gh":
            assert "--predicate-type" in args
            assert "https://example.invalid/receiver-ci-assessment/v1" in args
            assert "--source-ref" in args
            assert "refs/heads/examples/enterprise-receiver" in args
            return subprocess.CompletedProcess(
                args, returncode=returncode, stdout=stdout, stderr=stderr
            )
        return real_run(args, capture_output=capture_output, check=check)

    return fake_run


def _attestation_json(
    evidence_dir: Path,
    *,
    source_digest: str = _APPROVED_SIGNER_DIGEST,
    assessment: str = "accepted",
    subject_digest: str | None = None,
    definition_sha256: str | None = None,
    assessed_checks: tuple[str, ...] = ("integrity", "factory", "capability-revocation"),
) -> bytes:
    digest = (
        subject_digest or hashlib.sha256((evidence_dir / "index.json").read_bytes()).hexdigest()
    )
    if definition_sha256 is None:
        definition_sha256 = _evidence_digest(evidence_dir)
    return json.dumps(
        [
            {
                "verificationResult": {
                    "statement": {
                        "predicateType": "https://example.invalid/receiver-ci-assessment/v1",
                        "subject": [{"digest": {"sha256": digest}}],
                        "predicate": {
                            "assessedBy": "receiver-attest.yml",
                            "assessment": assessment,
                            "definitionSha256": definition_sha256,
                            "assessedChecks": list(assessed_checks),
                        },
                    },
                    "signature": {"certificate": {"sourceRepositoryDigest": source_digest}},
                }
            }
        ]
    ).encode()


def _matching_attestation_json(evidence_dir: Path) -> bytes:
    return _attestation_json(evidence_dir)


def test_signer_check_rejects_when_gh_fails(tmp_path, monkeypatch, valid_evidence):
    repo = _init_repo(tmp_path, approved_digests=[])
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    monkeypatch.setattr(
        receiver_check.subprocess,
        "run",
        _fake_gh_run(1, b"", b"no attestations found"),
    )
    with pytest.raises(receiver_check.ReceiverFailure) as excinfo:
        receiver_check.check_signer(valid_evidence, base_sha, _evidence_digest(valid_evidence))
    assert excinfo.value.policy == "signer"


def test_signer_check_accepts_when_gh_succeeds(tmp_path, monkeypatch, valid_evidence):
    repo = _init_repo(tmp_path, approved_digests=[])
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    monkeypatch.setattr(
        receiver_check.subprocess,
        "run",
        _fake_gh_run(0, _matching_attestation_json(valid_evidence), b""),
    )
    # must not raise
    receiver_check.check_signer(valid_evidence, base_sha, _evidence_digest(valid_evidence))


def test_signer_check_rejects_predicate_content_mismatch(tmp_path, monkeypatch, valid_evidence):
    """A validly-signed attestation whose predicate does not actually claim
    acceptance must still be rejected -- a successful gh exit code alone is
    not sufficient (this is the exact "unsigned claims laundering" gap the
    trust-boundary review flagged)."""
    repo = _init_repo(tmp_path, approved_digests=[])
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    mismatched = _attestation_json(valid_evidence, assessment="rejected")
    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    monkeypatch.setattr(receiver_check.subprocess, "run", _fake_gh_run(0, mismatched, b""))
    with pytest.raises(receiver_check.ReceiverFailure) as excinfo:
        receiver_check.check_signer(valid_evidence, base_sha, _evidence_digest(valid_evidence))
    assert excinfo.value.policy == "signer"


def test_signer_check_rejects_subject_digest_mismatch(tmp_path, monkeypatch, valid_evidence):
    """A valid, correctly-worded predicate attached to the WRONG subject digest
    (i.e. it attests to some other evidence package) must be rejected."""
    repo = _init_repo(tmp_path, approved_digests=[])
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    wrong_digest = _attestation_json(valid_evidence, subject_digest="0" * 64)
    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    monkeypatch.setattr(receiver_check.subprocess, "run", _fake_gh_run(0, wrong_digest, b""))
    with pytest.raises(receiver_check.ReceiverFailure) as excinfo:
        receiver_check.check_signer(valid_evidence, base_sha, _evidence_digest(valid_evidence))
    assert excinfo.value.policy == "signer"


def test_signer_check_rejects_unapproved_signer_revision(tmp_path, monkeypatch, valid_evidence):
    """A validly-signed attestation from a real, legitimately-authenticated
    run of the signer workflow -- but at a commit/revision this receiver has
    not actually approved -- must still be rejected. This requires no live
    second signer identity: it is purely a policy-side allowlist check, so
    this exercises it directly against a verifier-success payload carrying
    an unapproved sourceRepositoryDigest (the live-identity/certificate
    cryptography itself is exercised separately by the other signer tests,
    which all use the approved digest)."""
    repo = _init_repo(tmp_path, approved_digests=[])
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    unapproved = _attestation_json(valid_evidence, source_digest=_UNAPPROVED_SIGNER_DIGEST)
    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    monkeypatch.setattr(receiver_check.subprocess, "run", _fake_gh_run(0, unapproved, b""))
    with pytest.raises(receiver_check.ReceiverFailure) as excinfo:
        receiver_check.check_signer(valid_evidence, base_sha, _evidence_digest(valid_evidence))
    assert excinfo.value.policy == "signer"


def test_signer_check_rejects_empty_allowlist(tmp_path, monkeypatch, valid_evidence):
    """A signers.json with no approvedSignerSourceDigests must fail closed
    even when gh attestation verify would otherwise succeed -- this is the
    deliberate pre-bootstrap state."""
    repo = _init_repo(tmp_path, approved_digests=[])
    policy_path = repo / _POLICY_RELATIVE / "signers.json"
    policy = json.loads(policy_path.read_text())
    policy["approvedSignerSourceDigests"] = []
    policy_path.write_text(json.dumps(policy))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "empty allowlist")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    monkeypatch.setattr(
        receiver_check.subprocess,
        "run",
        _fake_gh_run(0, _matching_attestation_json(valid_evidence), b""),
    )
    with pytest.raises(receiver_check.ReceiverFailure) as excinfo:
        receiver_check.check_signer(valid_evidence, base_sha, _evidence_digest(valid_evidence))
    assert excinfo.value.policy == "signer"


def test_signer_check_rejects_definition_sha256_mismatch(tmp_path, monkeypatch, valid_evidence):
    """A validly-signed, correctly-worded, approved-revision attestation
    that nonetheless signs a DIFFERENT definitionSha256 than the one this
    receiver independently computed for this evidence must be rejected --
    otherwise a signature accepted for one definition could be replayed
    against unrelated evidence sharing the same generic predicate shape."""
    repo = _init_repo(tmp_path, approved_digests=[])
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    wrong_definition = _attestation_json(valid_evidence, definition_sha256="f" * 64)
    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    monkeypatch.setattr(receiver_check.subprocess, "run", _fake_gh_run(0, wrong_definition, b""))
    with pytest.raises(receiver_check.ReceiverFailure) as excinfo:
        receiver_check.check_signer(valid_evidence, base_sha, _evidence_digest(valid_evidence))
    assert excinfo.value.policy == "signer"


def test_signer_check_rejects_missing_assessed_checks(tmp_path, monkeypatch, valid_evidence):
    """A signature that never actually claims to have run a
    receiver-required assessed check (e.g. it omits "execution") must be
    rejected, even though every other field matches."""
    repo = _init_repo(tmp_path, approved_digests=[])
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    policy_path = repo / _POLICY_RELATIVE / "signers.json"
    policy = json.loads(policy_path.read_text())
    policy["requiredAssessedChecks"] = [
        "integrity",
        "factory",
        "capability-revocation",
        "execution",
    ]
    policy_path.write_text(json.dumps(policy))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "require execution")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    incomplete = _attestation_json(valid_evidence)  # omits "execution"
    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    monkeypatch.setattr(receiver_check.subprocess, "run", _fake_gh_run(0, incomplete, b""))
    with pytest.raises(receiver_check.ReceiverFailure) as excinfo:
        receiver_check.check_signer(valid_evidence, base_sha, _evidence_digest(valid_evidence))
    assert excinfo.value.policy == "signer"


def test_capability_revocation_rejects_revoked_identity(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path, approved_digests=[])
    policy_path = repo / _POLICY_RELATIVE / "revoked-capabilities.json"
    policy_path.write_text(json.dumps({"lockIdentity": ["pkg@deadbeef"]}))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "revoke")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / "capability-bindings.json").write_text(
        json.dumps([{"capability": "danger-skill", "lockIdentity": "pkg@deadbeef"}])
    )

    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    with pytest.raises(receiver_check.ReceiverFailure) as excinfo:
        receiver_check.check_capability_revocation(evidence_dir, base_sha)
    assert excinfo.value.policy == "capability-revoked"


def test_capability_revocation_allows_unrevoked_identity(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path, approved_digests=[])
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / "capability-bindings.json").write_text(
        json.dumps([{"capability": "safe-skill", "lockIdentity": "pkg@still-trusted"}])
    )
    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    receiver_check.check_capability_revocation(evidence_dir, base_sha)  # must not raise


def test_candidate_cannot_override_the_control_plane(tmp_path, monkeypatch, valid_evidence):
    """The PR head commits a tampered copy of the receiver's own approved-
    factories policy at the SAME repo path the real policy lives at. If
    check_factory_policy ever read from head_sha instead of base_sha, this
    tampered copy (which approves everything) would silently defeat the
    gate. Trusted reads must always resolve to the base commit regardless
    of what the PR's own tree contains at that path."""
    repo = _init_repo(tmp_path, approved_digests=[])  # nothing approved on the base
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    digest = _evidence_digest(valid_evidence)
    (repo / _POLICY_RELATIVE / "approved-factories.json").write_text(
        json.dumps({"definitionSha256": [digest]})
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "PR tries to rewrite the policy it is judged against")
    head_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    with pytest.raises(receiver_check.ReceiverFailure) as excinfo:
        receiver_check.check_factory_policy(digest, base_sha)
    assert excinfo.value.policy == "factory"
    # Confirm the tampered policy really is reachable at head_sha (i.e. the
    # rejection above is because reads are pinned to base_sha, not because
    # the tampered commit silently failed to land).
    assert digest in receiver_check.trusted_json(
        head_sha, f"{_POLICY_RELATIVE}/approved-factories.json"
    ).get("definitionSha256", [])


def test_materialized_case_evidence_rejects_symlinks(tmp_path, monkeypatch):
    """A PR could add a symlink under its own evidence/ directory pointing
    anywhere on the runner's filesystem; it must never be followed."""
    repo = _init_repo(tmp_path, approved_digests=[])
    case_dir = repo / "cases" / "symlink-attempt"
    evidence_dir = case_dir / "evidence"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "escape").symlink_to("/etc/passwd")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "symlink attempt")
    head_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    with (
        pytest.raises(receiver_check.ReceiverFailure) as excinfo,
        receiver_check.materialized_case_evidence(head_sha, "cases/symlink-attempt"),
    ):
        pass
    assert excinfo.value.policy == "missing-evidence"


def test_materialized_case_evidence_enforces_file_count_bound(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path, approved_digests=[])
    case_dir = repo / "cases" / "too-many-files"
    evidence_dir = case_dir / "evidence"
    evidence_dir.mkdir(parents=True)
    for index in range(5):
        (evidence_dir / f"file-{index}.txt").write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "many files")
    head_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    monkeypatch.setattr(receiver_check, "_MAX_EVIDENCE_FILES", 2)
    with (
        pytest.raises(receiver_check.ReceiverFailure) as excinfo,
        receiver_check.materialized_case_evidence(head_sha, "cases/too-many-files"),
    ):
        pass
    assert excinfo.value.policy == "missing-evidence"


def test_assess_canonical_runs_factory_and_capability_checks(
    tmp_path, monkeypatch, valid_evidence, schemas_dir
):
    """The privileged pre-signing assessment (reused unmodified by
    receiver-attest.yml) must actually run factory-allowlist and
    capability-revocation policy, not just structural verification -- this
    is the fix for the predicate-claim-overreach blocker."""
    digest = _evidence_digest(valid_evidence)
    repo = _init_repo(tmp_path, approved_digests=[])  # not approved
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    monkeypatch.setattr(receiver_check, "run_execution_bundle", _fake_passing_execution_results)

    with pytest.raises(receiver_check.ReceiverFailure) as excinfo:
        receiver_check.assess_canonical(valid_evidence, base_sha, schemas_dir)
    assert excinfo.value.policy == "factory"

    (repo / _POLICY_RELATIVE / "approved-factories.json").write_text(
        json.dumps({"definitionSha256": [digest]})
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "approve")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    summary = receiver_check.assess_canonical(valid_evidence, base_sha, schemas_dir)
    assert summary["definitionSha256"] == digest


def test_trusted_execution_image_requires_pinned_digest(tmp_path, monkeypatch):
    """A floating tag (no @sha256:...) must never be accepted as
    executionImage -- the whole container-isolation trust story depends on
    the base image itself being pinned, not just the candidate bytes."""
    repo = _init_repo(tmp_path, approved_digests=[])
    policy_path = repo / _POLICY_RELATIVE / "required-checks.json"
    policy = json.loads(policy_path.read_text())
    policy["executionImage"] = "python:3.12-slim"
    policy_path.write_text(json.dumps(policy))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "unpin image")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    with pytest.raises(receiver_check.ReceiverFailure) as excinfo:
        receiver_check._trusted_execution_image(base_sha)
    assert excinfo.value.policy == "execution"


def _write_execution_bundle_for(
    tmp_path, monkeypatch, repo: Path, base_sha: str, candidate_bytes: bytes
):
    bundle_dir = tmp_path / "bundle"
    manifest = receiver_check.write_execution_bundle(
        base_sha, {_APP_RELATIVE: candidate_bytes}, bundle_dir
    )
    return bundle_dir, manifest


def test_verify_execution_results_passes_for_matching_observation(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path, approved_digests=[])
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    bundle_dir, manifest = _write_execution_bundle_for(
        tmp_path, monkeypatch, repo, base_sha, _FIXED_GREETING
    )
    results = _fake_passing_execution_results(bundle_dir)
    receiver_check.verify_execution_results(base_sha, manifest, results)  # must not raise


def test_verify_execution_results_rejects_wrong_observed_output(tmp_path, monkeypatch):
    """The receiver-required test is actually held against the real
    candidate's observed behavior, not merely consulted structurally: a
    reported observed value that does not match policy's expectedOutput
    must fail, even if every other check (integrity/factory/binding/
    capability-revocation) never inspects behavior at all."""
    repo = _init_repo(tmp_path, approved_digests=[])
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    bundle_dir, manifest = _write_execution_bundle_for(
        tmp_path, monkeypatch, repo, base_sha, _BUGGY_GREETING
    )
    results = _fake_failing_execution_results(bundle_dir)
    with pytest.raises(receiver_check.ReceiverFailure) as excinfo:
        receiver_check.verify_execution_results(base_sha, manifest, results)
    assert excinfo.value.policy == "execution"


def test_verify_execution_results_rejects_missing_candidate(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path, approved_digests=[])
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    bundle_dir = tmp_path / "bundle"
    manifest = receiver_check.write_execution_bundle(base_sha, {}, bundle_dir)
    results = _fake_passing_execution_results(bundle_dir)
    with pytest.raises(receiver_check.ReceiverFailure) as excinfo:
        receiver_check.verify_execution_results(base_sha, manifest, results)
    assert excinfo.value.policy == "execution"


def test_verify_execution_results_rejects_image_digest_mismatch(tmp_path, monkeypatch):
    """A result claiming to have used a different image digest than the
    one policy pins must never be trusted -- this is what stops a
    compromised job B from silently substituting an unpinned/unapproved
    execution environment."""
    repo = _init_repo(tmp_path, approved_digests=[])
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    bundle_dir, manifest = _write_execution_bundle_for(
        tmp_path, monkeypatch, repo, base_sha, _FIXED_GREETING
    )
    results = _fake_passing_execution_results(bundle_dir)
    results[0]["imageDigestUsed"] = "python:3.12-slim@sha256:" + "0" * 64
    with pytest.raises(receiver_check.ReceiverFailure) as excinfo:
        receiver_check.verify_execution_results(base_sha, manifest, results)
    assert excinfo.value.policy == "execution"


def test_verify_execution_results_rejects_candidate_sha_mismatch(tmp_path, monkeypatch):
    """A result reporting a candidateSha256 that does not match the
    independently-computed expected digest must never be trusted -- it may
    mean the execution job never actually exercised the real candidate
    bytes."""
    repo = _init_repo(tmp_path, approved_digests=[])
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    bundle_dir, manifest = _write_execution_bundle_for(
        tmp_path, monkeypatch, repo, base_sha, _FIXED_GREETING
    )
    results = _fake_passing_execution_results(bundle_dir)
    results[0]["candidateSha256"] = "f" * 64
    with pytest.raises(receiver_check.ReceiverFailure) as excinfo:
        receiver_check.verify_execution_results(base_sha, manifest, results)
    assert excinfo.value.policy == "execution"


def test_verify_execution_results_rejects_incomplete_case_set(tmp_path, monkeypatch):
    """A result reporting fewer cases than policy declares (e.g. the
    executor bailed out partway, or the container timed out mid-run) must
    never be silently treated as a partial pass."""
    repo = _init_repo(tmp_path, approved_digests=[])
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    bundle_dir, manifest = _write_execution_bundle_for(
        tmp_path, monkeypatch, repo, base_sha, _FIXED_GREETING
    )
    results = _fake_passing_execution_results(bundle_dir)
    results[0]["cases"] = []
    with pytest.raises(receiver_check.ReceiverFailure) as excinfo:
        receiver_check.verify_execution_results(base_sha, manifest, results)
    assert excinfo.value.policy == "execution"


def test_verify_execution_results_rejects_missing_required_check(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path, approved_digests=[])
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    bundle_dir, manifest = _write_execution_bundle_for(
        tmp_path, monkeypatch, repo, base_sha, _FIXED_GREETING
    )
    with pytest.raises(receiver_check.ReceiverFailure) as excinfo:
        receiver_check.verify_execution_results(base_sha, manifest, [])
    assert excinfo.value.policy == "execution"


_CONTAINER_DIR = _HERE / "container"


def _run_container_driver(payload: dict, timeout: float = 15.0) -> dict:
    """Invoke the EXACT file that is shipped into the real execution
    container, as a real OS subprocess (no docker daemon required for this
    protocol-level regression -- the same driver/executor pair runs
    identically whether or not the surrounding process also has
    `--network none`/`--read-only`/cgroup namespace isolation applied by
    `docker run`, which is a separate, orthogonal layer validated by the
    docker-gated end-to-end test below)."""
    completed = subprocess.run(
        [sys.executable, "-I", "-S", str(_CONTAINER_DIR / "container_driver.py")],
        input=json.dumps(payload).encode(),
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    text = completed.stdout.decode()
    matching = [line for line in text.splitlines() if line.startswith("CASES:")]
    assert len(matching) == 1, text
    return json.loads(matching[0][len("CASES:") :])


def test_container_driver_reports_correct_observed_output():
    source = make_fixture._PATCHED_GREETING
    payload = {
        "candidateSource": source,
        "entrypoint": {"module": "candidate_module", "function": "greet"},
        "cases": [{"input": "World"}],
    }
    cases = _run_container_driver(payload)
    assert cases == [{"ok": True, "observed": "Hello, World!"}]


def test_container_driver_treats_os_exit_bypass_as_explicit_failure():
    """A candidate that calls os._exit(0) during import must never be
    confused with a passing result: the executor process dies before
    writing its RESULT line, and the driver must report this as an
    explicit, unambiguous failure."""
    source = "import os\nos._exit(0)\n\ndef greet(name):\n    return f'Hello, {name}!'\n"
    payload = {
        "candidateSource": source,
        "entrypoint": {"module": "candidate_module", "function": "greet"},
        "cases": [{"input": "World"}],
    }
    cases = _run_container_driver(payload)
    assert cases == [{"ok": False, "error": "executor produced no RESULT line"}]


def test_container_driver_rejects_forged_result_via_raw_fd_write():
    """A candidate that writes a forged ``RESULT:`` line directly to raw fd
    1 during import, hoping to be believed before the real post-call write
    happens, must never be trusted: the executor redirects its own fd 0/1/2
    to /dev/null BEFORE importing candidate code, so the forged write never
    reaches the real channel."""
    source = (
        "import os\n"
        'os.write(1, b\'RESULT:{"ok": true, "observed": "FORGED"}\\n\')\n'
        "\n"
        "def greet(name):\n"
        "    return f'Hello, {name}!'\n"
    )
    payload = {
        "candidateSource": source,
        "entrypoint": {"module": "candidate_module", "function": "greet"},
        "cases": [{"input": "World"}],
    }
    cases = _run_container_driver(payload)
    # The forged write landed in /dev/null; only the genuine post-call
    # result (via the saved real stdout fd) is ever observed.
    assert cases == [{"ok": True, "observed": "Hello, World!"}]


def test_container_driver_rejects_fd_spray_forgery_as_ambiguous():
    """A candidate that sprays the same forged RESULT line across every
    low file descriptor, hoping to hit whichever fd the saved real-stdout
    duplicate landed on, must still never be trusted merely by picking the
    first or last matching line -- any ambiguity (more than one RESULT
    line) is treated as an explicit failure."""
    source = (
        "import os\n"
        "for fd in range(10):\n"
        "    try:\n"
        '        os.write(fd, b\'RESULT:{"ok": true, "observed": "FORGED"}\\n\')\n'
        "    except OSError:\n"
        "        pass\n"
        "\n"
        "def greet(name):\n"
        "    return f'Hello, {name}!'\n"
    )
    payload = {
        "candidateSource": source,
        "entrypoint": {"module": "candidate_module", "function": "greet"},
        "cases": [{"input": "World"}],
    }
    cases = _run_container_driver(payload)
    observed = cases[0]
    # Either the spray missed the real fd entirely (single genuine RESULT
    # line, correct observed value) or it hit it (ambiguous, explicit
    # failure) -- a silently-accepted forged "FORGED" value is the only
    # outcome that would indicate a real break.
    assert observed in (
        {"ok": True, "observed": "Hello, World!"},
        {"ok": False, "error": "executor produced more than one RESULT line"},
    )


def test_container_driver_rejects_oversized_response():
    """A candidate whose return value is too large to encode within the
    executor's own output bound must be reported as an explicit error, not
    silently truncated into something that happens to look like a pass."""
    source = "def greet(name):\n    return 'x' * 100000\n"
    payload = {
        "candidateSource": source,
        "entrypoint": {"module": "candidate_module", "function": "greet"},
        "cases": [{"input": "World"}],
    }
    cases = _run_container_driver(payload)
    assert cases == [{"ok": False, "error": "observed value exceeded the size bound"}]


def test_run_bundle_module_imports_cleanly_via_spec_from_file_location():
    """``check.py`` loads ``run_bundle.py`` via ``importlib.util.spec_from_
    file_location`` (see ``_run_bundle_module``), which does NOT add the
    loaded file's own directory to ``sys.path`` automatically -- unlike a
    genuine top-level script invocation. This proves run_bundle.py's own
    explicit ``sys.path.insert`` makes its sibling ``bounded_io`` import
    succeed in exactly this loader context too, not only when run directly
    as ``python3 run_bundle.py``."""
    module = receiver_check._run_bundle_module()
    assert hasattr(module, "run_execution_bundle")
    assert hasattr(module, "read_process_bounded")


import importlib.util as _importlib_util  # noqa: E402

_bounded_io_spec = _importlib_util.spec_from_file_location(
    "_test_bounded_io", _CONTAINER_DIR / "bounded_io.py"
)
assert _bounded_io_spec is not None and _bounded_io_spec.loader is not None
bounded_io = _importlib_util.module_from_spec(_bounded_io_spec)
sys.modules[_bounded_io_spec.name] = bounded_io
_bounded_io_spec.loader.exec_module(bounded_io)


def _spawn(code: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_bounded_io_rejects_one_byte_then_hang():
    """The core regression this module exists to fix: a process that
    writes a single byte (just enough to make ``selectors.select()``
    report readiness) and then hangs WITHOUT closing its output must still
    be caught by the deadline -- a buffered ``file.read(n)`` call would
    block waiting to accumulate a full chunk and blow straight through the
    intended timeout. Guarded by an external, independent wall-clock bound
    (never relying solely on the code under test to enforce its own
    timeout) so a regression here fails fast instead of hanging the suite."""
    proc = _spawn(
        "import sys, time\n"
        "sys.stdout.buffer.write(b'x')\n"
        "sys.stdout.buffer.flush()\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()
    try:
        result = bounded_io.read_process_bounded(
            proc, timeout_seconds=1.0, max_stdout_bytes=4096
        )
    finally:
        proc.kill()
        proc.wait(timeout=5)
    elapsed = time.monotonic() - started
    assert elapsed < 10.0, "a 1s-timeout read must never take anywhere near 60s"
    assert result.timed_out is True
    assert result.overflowed is False
    assert result.stdout == b"x"
    assert result.trustworthy is False


def test_bounded_io_rejects_valid_line_then_nonzero_exit():
    """A well-formed, complete output line is never sufficient on its own:
    a process that writes it and then exits with a nonzero status must be
    reported as untrustworthy, not accepted merely because the line's
    content looked fine."""
    proc = _spawn(
        "import sys\n"
        "sys.stdout.buffer.write(b'RESULT:{\"ok\": true}\\n')\n"
        "sys.stdout.buffer.flush()\n"
        "sys.exit(3)\n"
    )
    result = bounded_io.read_process_bounded(proc, timeout_seconds=5.0, max_stdout_bytes=4096)
    assert result.stdout == b'RESULT:{"ok": true}\n'
    assert result.timed_out is False
    assert result.overflowed is False
    assert result.returncode == 3
    assert result.trustworthy is False


def test_bounded_io_rejects_valid_line_then_hang_past_deadline():
    """A process that writes its complete, well-formed line and THEN hangs
    (rather than exiting) must still be rejected: the line's presence in
    ``stdout`` does not make it trustworthy once ``timed_out`` is set."""
    proc = _spawn(
        "import sys, time\n"
        "sys.stdout.buffer.write(b'RESULT:{\"ok\": true}\\n')\n"
        "sys.stdout.buffer.flush()\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()
    try:
        result = bounded_io.read_process_bounded(
            proc, timeout_seconds=1.0, max_stdout_bytes=4096
        )
    finally:
        proc.kill()
        proc.wait(timeout=5)
    elapsed = time.monotonic() - started
    assert elapsed < 10.0
    assert result.stdout == b'RESULT:{"ok": true}\n'
    assert result.timed_out is True
    assert result.trustworthy is False


_run_bundle_spec = _importlib_util.spec_from_file_location(
    "_test_run_bundle", _CONTAINER_DIR / "run_bundle.py"
)
assert _run_bundle_spec is not None and _run_bundle_spec.loader is not None
run_bundle = _importlib_util.module_from_spec(_run_bundle_spec)
sys.modules[_run_bundle_spec.name] = run_bundle
_run_bundle_spec.loader.exec_module(run_bundle)


def test_run_bundle_harness_dir_contains_only_expected_files(tmp_path):
    """The harness directory bind-mounted into the execution container
    must contain EXACTLY the three trusted scripts -- never
    ``manifest.json`` (which holds every case's ``expectedOutput``), never
    any check's ``candidate.bin``, never anything else the bundle
    directory happens to also hold."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    for name in ("container_driver.py", "executor.py", "bounded_io.py"):
        (bundle_dir / name).write_text(f"# {name}\n")
    (bundle_dir / "manifest.json").write_text('{"expectedOutput": "secret"}')
    check_dir = bundle_dir / "some-check"
    check_dir.mkdir()
    (check_dir / "candidate.bin").write_bytes(b"candidate bytes")

    scratch_root = tmp_path / "scratch"
    harness_dir = run_bundle._build_harness_dir(bundle_dir, scratch_root)

    produced = {p.name for p in harness_dir.iterdir()}
    assert produced == {"container_driver.py", "executor.py", "bounded_io.py"}


def test_run_bundle_uses_explicit_bind_mount_not_named_volume(tmp_path, monkeypatch):
    """Docker's ``-v NAME:/path`` short form treats ``NAME`` as a NAMED
    VOLUME (not a host bind mount) whenever it contains no path separator
    -- silently mounting an empty, Docker-managed volume instead of the
    intended host directory. This proves the actual invocation uses the
    unambiguous ``--mount type=bind,source=<absolute path>,...`` form."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    for name in ("container_driver.py", "executor.py", "bounded_io.py"):
        (bundle_dir / name).write_bytes((_CONTAINER_DIR / name).read_bytes())
    check_dir = bundle_dir / "greeting"
    check_dir.mkdir()
    (check_dir / "candidate.bin").write_bytes(_FIXED_GREETING)
    manifest = {
        "executionImage": _TEST_EXECUTION_IMAGE,
        "checks": [
            {
                "name": "greeting",
                "candidateMissing": False,
                "entrypoint": {"module": "candidate_module", "function": "greet"},
                "cases": [{"input": "World", "expectedOutput": "Hello, World!"}],
            }
        ],
    }
    (bundle_dir / "manifest.json").write_text(json.dumps(manifest))

    captured_args = {}
    real_popen = subprocess.Popen

    class _FakeProc:
        def __init__(self):
            self.stdin = _FakeStdin()
            self.stdout = None
            self.stderr = None
            self.returncode = 0

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

    class _FakeStdin:
        def write(self, data):
            pass

        def close(self):
            pass

    def fake_popen(args, **kwargs):
        if args and args[0] == "docker" and "run" in args:
            captured_args["args"] = args
            return _FakeProc()
        return real_popen(args, **kwargs)

    monkeypatch.setattr(run_bundle.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        run_bundle,
        "read_process_bounded",
        lambda *a, **k: bounded_io.BoundedReadResult(
            stdout=b'CASES:[{"ok": true, "observed": "Hello, World!"}]',
            stderr=b"",
            overflowed=False,
            timed_out=False,
            returncode=0,
        ),
    )
    cleaned_up = []
    monkeypatch.setattr(
        run_bundle, "_cleanup_container", lambda name: cleaned_up.append(name)
    )

    run_bundle.run_execution_bundle(bundle_dir)

    args = captured_args["args"]
    assert "--mount" in args
    mount_value = args[args.index("--mount") + 1]
    assert mount_value.startswith("type=bind,source=/"), mount_value
    assert mount_value.endswith(",destination=/harness,readonly"), mount_value
    assert not any(a == "-v" for a in args), "must never use the ambiguous -v short form"
    assert "--name" in args
    container_name = args[args.index("--name") + 1]
    assert container_name.startswith("apmx-exec-")
    assert cleaned_up == [container_name]


def test_run_bundle_cleans_up_container_even_on_timeout(tmp_path, monkeypatch):
    """Killing the local `docker` CLI client process does not stop the
    actual container inside the daemon -- only an explicit, scoped
    `docker rm -f <name>` against its own unique name does. This proves
    cleanup runs even when the container run itself is treated as timed
    out."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    for name in ("container_driver.py", "executor.py", "bounded_io.py"):
        (bundle_dir / name).write_bytes((_CONTAINER_DIR / name).read_bytes())
    check_dir = bundle_dir / "greeting"
    check_dir.mkdir()
    (check_dir / "candidate.bin").write_bytes(_FIXED_GREETING)
    manifest = {
        "executionImage": _TEST_EXECUTION_IMAGE,
        "checks": [
            {
                "name": "greeting",
                "candidateMissing": False,
                "entrypoint": {"module": "candidate_module", "function": "greet"},
                "cases": [{"input": "World", "expectedOutput": "Hello, World!"}],
            }
        ],
    }
    (bundle_dir / "manifest.json").write_text(json.dumps(manifest))

    real_popen = subprocess.Popen

    class _FakeProc:
        def __init__(self):
            self.stdin = _FakeStdin()
            self.stdout = None
            self.stderr = None
            self.returncode = None

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

    class _FakeStdin:
        def write(self, data):
            pass

        def close(self):
            pass

    def fake_popen(args, **kwargs):
        if args and args[0] == "docker" and "run" in args:
            return _FakeProc()
        return real_popen(args, **kwargs)

    monkeypatch.setattr(run_bundle.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        run_bundle,
        "read_process_bounded",
        lambda *a, **k: bounded_io.BoundedReadResult(
            stdout=b"", stderr=b"", overflowed=False, timed_out=True, returncode=None
        ),
    )
    cleaned_up = []
    monkeypatch.setattr(
        run_bundle, "_cleanup_container", lambda name: cleaned_up.append(name)
    )

    results = run_bundle.run_execution_bundle(bundle_dir)

    assert len(cleaned_up) == 1
    assert cleaned_up[0].startswith("apmx-exec-")
    # The timed-out run must be recorded as a failed case, never a silent
    # drop or an accidental pass.
    assert results[0]["cases"][0]["matched"] is False


def _docker_daemon_reachable() -> bool:
    import shutil

    if shutil.which("docker") is None:
        return False
    try:
        completed = subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


@pytest.mark.skipif(
    not _docker_daemon_reachable(), reason="no reachable docker daemon in this environment"
)
def test_real_docker_execution_positive_and_tampering(tmp_path, monkeypatch):
    """The real end-to-end path: an actual `docker run` of the pinned,
    network-isolated, read-only, non-root container image, for both a
    correct candidate (must be accepted) and a tampered/buggy candidate
    (must be rejected) -- not a subprocess-level mock of either outcome."""
    repo = _init_repo(tmp_path, approved_digests=[])
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)

    positive_bundle = tmp_path / "bundle-positive"
    manifest = receiver_check.write_execution_bundle(
        base_sha, {_APP_RELATIVE: _FIXED_GREETING}, positive_bundle
    )
    results = receiver_check.run_execution_bundle(positive_bundle)
    receiver_check.verify_execution_results(base_sha, manifest, results)  # must not raise

    tampered_bundle = tmp_path / "bundle-tampered"
    tampered_manifest = receiver_check.write_execution_bundle(
        base_sha, {_APP_RELATIVE: _BUGGY_GREETING}, tampered_bundle
    )
    tampered_results = receiver_check.run_execution_bundle(tampered_bundle)
    with pytest.raises(receiver_check.ReceiverFailure) as excinfo:
        receiver_check.verify_execution_results(base_sha, tampered_manifest, tampered_results)
    assert excinfo.value.policy == "execution"


def test_materialized_case_evidence_rejects_oversized_blob_without_reading_it(
    tmp_path, monkeypatch
):
    """Byte-bounding must come from trusted git object METADATA, not from
    reading content and checking afterwards: this proves an oversized blob
    is rejected via ``_git_blob_sizes`` alone, with ``git show`` (the actual
    content read) never invoked for that blob."""
    repo = _init_repo(tmp_path, approved_digests=[])
    case_dir = repo / "cases" / "oversized"
    evidence_dir = case_dir / "evidence"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "big.bin").write_bytes(b"x" * 4096)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "oversized blob")
    head_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    monkeypatch.setattr(receiver_check, "_MAX_EVIDENCE_BLOB_BYTES", 1024)

    real_run_git = receiver_check._run_git

    def spying_run_git(args, **kwargs):
        if args and args[0] == "show" and args[1].endswith("big.bin"):
            pytest.fail("git show must never be called to read an oversized blob's content")
        return real_run_git(args, **kwargs)

    monkeypatch.setattr(receiver_check, "_run_git", spying_run_git)
    with (
        pytest.raises(receiver_check.ReceiverFailure) as excinfo,
        receiver_check.materialized_case_evidence(head_sha, "cases/oversized"),
    ):
        pass
    assert excinfo.value.policy == "missing-evidence"


def _copy_tree(source: Path, destination: Path) -> None:
    import shutil

    shutil.copytree(source, destination)
