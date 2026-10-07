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
    head_sha = _patch_candidate(repo)
    case_dir = repo / "cases" / "positive"
    (case_dir / "evidence").parent.mkdir(parents=True)
    _copy_tree(valid_evidence, case_dir / "evidence")

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
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "tampered")
    head_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    case_dir = repo / "cases" / "tampered"
    case_dir.mkdir(parents=True)
    _copy_tree(valid_evidence, case_dir / "evidence")

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
    head_sha = _patch_candidate(repo)
    case_dir = repo / "cases" / "unapproved"
    case_dir.mkdir(parents=True)
    _copy_tree(unapproved_evidence, case_dir / "evidence")

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
            return subprocess.CompletedProcess(
                args, returncode=returncode, stdout=stdout, stderr=stderr
            )
        return real_run(args, capture_output=capture_output, check=check)

    return fake_run


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
    monkeypatch.setattr(receiver_check.subprocess, "run", _fake_gh_run(0, b"[]", b""))
    receiver_check.check_signer(valid_evidence, base_sha)  # must not raise


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


def _copy_tree(source: Path, destination: Path) -> None:
    import shutil

    shutil.copytree(source, destination)
