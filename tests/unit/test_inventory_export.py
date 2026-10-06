"""Official inventory export is a no-resolution operation over frozen bytes."""

import hashlib
import json
from pathlib import Path

import pytest

from apmx.contracts.models import ContractError, ProcessObservation
from apmx.install import apm_backend

MANIFEST = b"name: consumer\nversion: 1.0.0\n"
LOCK = b"lockfile_version: '1'\ndependencies: []\n"
BOM = b'{"bomFormat":"CycloneDX","specVersion":"1.5","version":1}\n'


@pytest.fixture
def native_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    executable = tmp_path / "apm"
    executable.write_bytes(b"fixture backend")
    executable.chmod(0o700)
    monkeypatch.setattr(apm_backend, "locate_backend", lambda: executable)
    identity = apm_backend.backend_identity(executable)
    calls = []
    failure = {}

    def supervise(request, *, on_bytes, **kwargs):
        calls.append(request)
        assert request.env["APM_NO_SCRIPTS"] == "1"
        if request.argv[-1] == "--version":
            on_bytes("stdout", (apm_backend.expected_version_output() + "\n").encode())
        else:
            assert request.argv[1:6] == (
                "lock",
                "export",
                "--format",
                "cyclonedx",
                "--output",
            )
            assert (request.cwd / "apm.yml").read_bytes() == MANIFEST
            assert (request.cwd / "apm.lock.yaml").read_bytes() == LOCK
            output = Path(request.argv[6])
            assert output.is_absolute() and output.parent == request.cwd
            output.write_bytes(b"not JSON" if failure.get("bom") else BOM)
            if failure.get("input"):
                (request.cwd / "apm.lock.yaml").write_bytes(b"mutated")
            if failure.get("identity"):
                executable.write_bytes(b"changed backend")
            if failure.get("process"):
                return ProcessObservation(2)
            if failure.get("cleanup"):
                return ProcessObservation(0, cleanup_confirmed=False)
            if failure.get("timeout"):
                return ProcessObservation(None, stop_reason="timeout")
        return ProcessObservation(0)

    monkeypatch.setattr(apm_backend, "supervise_process", supervise)
    return identity, calls, failure


def test_official_export_preserves_exact_bytes_and_uses_private_copy(native_export) -> None:
    identity, calls, _ = native_export
    before = (hashlib.sha256(MANIFEST).hexdigest(), hashlib.sha256(LOCK).hexdigest())
    assert apm_backend.export_cyclonedx(MANIFEST, LOCK, expected_backend=identity) == BOM
    assert len(calls) == 2
    assert not calls[-1].cwd.exists()
    assert before == (hashlib.sha256(MANIFEST).hexdigest(), hashlib.sha256(LOCK).hexdigest())


@pytest.mark.parametrize("fault", ["bom", "input", "identity", "process", "cleanup", "timeout"])
def test_export_refuses_changed_inputs_or_incomplete_delivery(native_export, fault: str) -> None:
    identity, calls, failure = native_export
    failure[fault] = True
    with pytest.raises(ContractError):
        apm_backend.export_cyclonedx(MANIFEST, LOCK, expected_backend=identity)
    assert not calls[-1].cwd.exists()


def test_export_refuses_different_recorded_backend_before_export(native_export) -> None:
    identity, calls, _ = native_export
    identity = {**identity, "executable_sha256": "0" * 64}
    with pytest.raises(ContractError):
        apm_backend.export_cyclonedx(MANIFEST, LOCK, expected_backend=identity)
    assert all(request.argv[-1] == "--version" for request in calls)


def test_real_backend_exports_local_package_lock_without_source_resolution(tmp_path: Path) -> None:
    from apmx.contracts.models import ContractLimits

    stage = tmp_path / "installed"
    stage.mkdir()
    source = Path(__file__).resolve().parents[2] / "examples/contracts/packaged-job"
    identity = apm_backend.install(stage, package_ref=str(source), limits=ContractLimits())
    manifest = (stage / "apm.yml").read_bytes()
    lock = (stage / "apm.lock.yaml").read_bytes()
    raw = apm_backend.export_cyclonedx(manifest, lock, expected_backend=identity)
    document = json.loads(raw)
    assert document["bomFormat"] == "CycloneDX"
    assert document["specVersion"] == "1.5"
    assert document["components"]
    assert (stage / "apm.yml").read_bytes() == manifest
    assert (stage / "apm.lock.yaml").read_bytes() == lock
