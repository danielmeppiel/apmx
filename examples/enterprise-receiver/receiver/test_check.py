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
_FIXED_GREETING = make_fixture._PATCHED_GREETING.encode()


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
            }
        )
    )
    (policy_dir / "revoked-capabilities.json").write_text(json.dumps({"lockIdentity": []}))
    app_dir = repo / "examples" / "enterprise-receiver" / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "greeting.py").write_bytes(_BUGGY_GREETING)
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


def _matching_attestation_json(evidence_dir: Path) -> bytes:
    digest = hashlib.sha256((evidence_dir / "index.json").read_bytes()).hexdigest()
    return json.dumps(
        [
            {
                "verificationResult": {
                    "statement": {
                        "predicateType": "https://example.invalid/receiver-ci-assessment/v1",
                        "subject": [{"digest": {"sha256": digest}}],
                        "predicate": {
                            "assessedBy": "receiver-attest.yml",
                            "assessment": "accepted",
                        },
                    }
                }
            }
        ]
    ).encode()


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
        receiver_check.check_signer(valid_evidence, base_sha)
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
    receiver_check.check_signer(valid_evidence, base_sha)  # must not raise


def test_signer_check_rejects_predicate_content_mismatch(tmp_path, monkeypatch, valid_evidence):
    """A validly-signed attestation whose predicate does not actually claim
    acceptance must still be rejected -- a successful gh exit code alone is
    not sufficient (this is the exact "unsigned claims laundering" gap the
    trust-boundary review flagged)."""
    repo = _init_repo(tmp_path, approved_digests=[])
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    digest = hashlib.sha256((valid_evidence / "index.json").read_bytes()).hexdigest()
    mismatched = json.dumps(
        [
            {
                "verificationResult": {
                    "statement": {
                        "predicateType": "https://example.invalid/receiver-ci-assessment/v1",
                        "subject": [{"digest": {"sha256": digest}}],
                        "predicate": {
                            "assessedBy": "receiver-attest.yml",
                            "assessment": "rejected",
                        },
                    }
                }
            }
        ]
    ).encode()
    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    monkeypatch.setattr(receiver_check.subprocess, "run", _fake_gh_run(0, mismatched, b""))
    with pytest.raises(receiver_check.ReceiverFailure) as excinfo:
        receiver_check.check_signer(valid_evidence, base_sha)
    assert excinfo.value.policy == "signer"


def test_signer_check_rejects_subject_digest_mismatch(tmp_path, monkeypatch, valid_evidence):
    """A valid, correctly-worded predicate attached to the WRONG subject digest
    (i.e. it attests to some other evidence package) must be rejected."""
    repo = _init_repo(tmp_path, approved_digests=[])
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    wrong_digest = json.dumps(
        [
            {
                "verificationResult": {
                    "statement": {
                        "predicateType": "https://example.invalid/receiver-ci-assessment/v1",
                        "subject": [{"digest": {"sha256": "0" * 64}}],
                        "predicate": {
                            "assessedBy": "receiver-attest.yml",
                            "assessment": "accepted",
                        },
                    }
                }
            }
        ]
    ).encode()
    monkeypatch.setattr(receiver_check, "_REPO_ROOT", repo)
    monkeypatch.setattr(receiver_check.subprocess, "run", _fake_gh_run(0, wrong_digest, b""))
    with pytest.raises(receiver_check.ReceiverFailure) as excinfo:
        receiver_check.check_signer(valid_evidence, base_sha)
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


def _copy_tree(source: Path, destination: Path) -> None:
    import shutil

    shutil.copytree(source, destination)
