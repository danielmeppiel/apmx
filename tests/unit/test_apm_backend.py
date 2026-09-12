"""The bundled backend is explicit, immutable in identity, and never found on PATH."""

import hashlib
import json
import sys
from pathlib import Path
import shutil

import pytest

from apmx.contracts.models import ContractError


def test_source_backend_requires_absolute_explicit_path(tmp_path, monkeypatch):
    from apmx.install.apm_backend import locate_backend

    monkeypatch.setenv("APMX_APM_BACKEND", "apm")
    with pytest.raises(ContractError, match="absolute"):
        locate_backend()


def test_frozen_backend_ignores_override_and_path(tmp_path, monkeypatch):
    from apmx.install.apm_backend import locate_backend

    launcher = tmp_path / "apmx"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(launcher))
    monkeypatch.setenv("APMX_APM_BACKEND", str(tmp_path / "evil"))
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(ContractError, match="bundled"):
        locate_backend()


def test_backend_identity_records_exact_bytes(tmp_path, monkeypatch):
    from apmx.install.apm_backend import backend_identity

    binary = tmp_path / "apm"
    binary.write_bytes(b"backend")
    pin = tmp_path / "apm-backend.json"
    pin.write_text(json.dumps({
        "schema": "apmx-apm-backend/1",
        "repository": "microsoft/apm",
        "version": "0.30.0",
        "source_commit": "8c2e0d9c352e2ed0e8c56b40063a63e1dd4a1937",
        "assets": {},
    }))
    monkeypatch.setattr("apmx.install.apm_backend.PIN_PATH", pin)
    identity = backend_identity(binary)
    assert identity["executable_sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
    assert identity["pin_sha256"] == hashlib.sha256(pin.read_bytes()).hexdigest()
    assert identity["version"] == "0.30.0"


def test_real_backend_installs_transitive_context_without_source_writes(tmp_path, monkeypatch):
    from apmx.install.apm_backend import install, locate_backend
    from apmx.install.contract_source_validation import source_hash
    from apmx.contracts.models import ContractLimits

    # Missing provisioning is a failure, never a mocked or skipped native proof.
    assert locate_backend().is_file()
    limits = ContractLimits()
    source = Path(__file__).resolve().parents[2] / "examples/contracts/packaged-job"
    before = source_hash(source, limits)
    stage = tmp_path / "stage"
    stage.mkdir()
    identity = install(stage, package_ref=str(source), limits=limits)
    assert identity["version"] == "0.30.0"
    assert source_hash(source, limits) == before
    assert (stage / "apm.lock.yaml").is_file()
    skills = list((stage / "apm_modules").rglob("SKILL.md"))
    assert skills
    assert not (stage / ".github").exists()
    assert not (stage / ".copilot").exists()
    assert not (stage / ".mcp.json").exists()


def test_real_backend_replays_anchored_consumer_lock(tmp_path):
    from apmx.install.apm_backend import install, snapshot_manifest
    from apmx.contracts.models import ContractLimits

    limits = ContractLimits()
    caller = tmp_path / "caller"
    caller.mkdir()
    fixture = Path(__file__).resolve().parents[2] / "examples/contracts/packaged-job"
    package = tmp_path / "package"
    shutil.copytree(fixture, package)
    (caller / "apm.yml").write_text(
        "name: consumer\nversion: 1.0.0\ndependencies:\n  apm:\n    - ../package\n"
    )
    install(caller, limits=limits)
    before = {name: (caller / name).read_bytes() for name in ("apm.yml", "apm.lock.yaml")}
    stage = tmp_path / "replay"
    stage.mkdir()
    assert snapshot_manifest(caller, stage, limits)
    install(stage, frozen=True, limits=limits)
    assert list((stage / "apm_modules").rglob("SKILL.md"))
    assert all((caller / name).read_bytes() == raw for name, raw in before.items())
