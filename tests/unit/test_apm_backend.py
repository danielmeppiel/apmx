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


@pytest.mark.parametrize("section", [
    "", "dependencies: null\n", "dependencies: {}\n", "dependencies: {apm: []}\n",
])
def test_add_package_request_accepts_empty_dependencies(tmp_path, section):
    from apmx.contracts.models import ContractLimits
    from apmx.install.apm_backend import add_package_request, snapshot_manifest
    from apmx.utils.yaml_io import load_yaml

    caller = tmp_path / "caller"
    stage = tmp_path / "stage"
    caller.mkdir()
    stage.mkdir()
    manifest = caller / "apm.yml"
    manifest.write_text("name: caller\nversion: 1.0.0\n" + section)
    before = manifest.read_bytes()
    limits = ContractLimits()
    assert not snapshot_manifest(caller, stage, limits)
    add_package_request(stage, "example/contract", limits)
    assert load_yaml(stage / "apm.yml")["dependencies"]["apm"] == ["example/contract"]
    assert manifest.read_bytes() == before


@pytest.mark.parametrize("section", [
    "dependencies: invalid", "dependencies: []", "dependencies: {apm: null}",
    "dependencies: {apm: invalid}", "dependencies: {apm: {name: invalid}}",
])
def test_add_package_request_rejects_invalid_dependencies(tmp_path, section):
    from apmx.contracts.models import ContractLimits
    from apmx.install.apm_backend import add_package_request

    manifest = tmp_path / "apm.yml"
    manifest.write_text("name: caller\nversion: 1.0.0\n" + section)
    before = manifest.read_bytes()
    with pytest.raises(ContractError) as rejected:
        add_package_request(tmp_path, "example/contract", ContractLimits())
    assert rejected.value.code == "invalid_manifest"
    assert manifest.read_bytes() == before


def test_frozen_backend_cannot_escape_through_symlink(tmp_path, monkeypatch):
    from apmx.install.apm_backend import locate_backend

    (tmp_path / "libexec").mkdir()
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "libexec/apm").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "apmx"))
    with pytest.raises(ContractError, match="symlink"):
        locate_backend()


def _unit_backend(tmp_path, monkeypatch, *, install_observation=None, version=b""):
    """Unit invocation seam only; native proof lives in the real-backend tests."""
    from apmx.contracts.models import ProcessObservation
    from apmx.install import apm_backend

    binary = tmp_path / "apm"
    binary.write_bytes(b"unit executable identity")
    binary.chmod(0o755)
    monkeypatch.setenv("APMX_APM_BACKEND", str(binary))
    requests = []

    def supervise(request, *, on_bytes, limits):
        requests.append(request)
        if request.argv[1] == "--version":
            on_bytes("stdout", version or (apm_backend.expected_version_output() + "\n").encode())
            return ProcessObservation(0)
        on_bytes("stderr", b"PRIVATE_AUTH_OUTPUT_MUST_NOT_ESCAPE")
        return install_observation or ProcessObservation(0)

    monkeypatch.setattr(apm_backend, "supervise_process", supervise)
    return requests


def test_native_invocation_preserves_auth_and_suppresses_activation_child_only(tmp_path, monkeypatch):
    import os
    from apmx.install.apm_backend import install
    from apmx.contracts.models import ContractLimits

    requests = _unit_backend(tmp_path, monkeypatch)
    monkeypatch.setenv("APM_NO_SCRIPTS", "original-value")
    monkeypatch.setenv("APM_GIT_PROTOCOL", "ssh")
    monkeypatch.setenv("APM_ALLOW_PROTOCOL_FALLBACK", "0")
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-token-not-real")
    before = dict(os.environ)
    install(tmp_path, package_ref="org/repo/jobs#v1", limits=ContractLimits())
    assert dict(os.environ) == before
    request = requests[-1]
    assert request.cwd == tmp_path
    assert request.argv[1:] == (
        "install", "org/repo/jobs#v1", "--root", str(tmp_path),
        "--only", "apm", "--target", "agent-skills", "--no-trust-bin",
    )
    assert request.env["APM_NO_SCRIPTS"] == "1"
    for name in ("HOME", "GITHUB_TOKEN", "APM_GIT_PROTOCOL", "APM_ALLOW_PROTOCOL_FALLBACK"):
        assert request.env.get(name) == before.get(name)
    assert "--no-policy" not in request.argv
    assert "--trust-transitive-mcp" not in request.argv


def test_frozen_native_replay_never_adds_positional_packages(tmp_path, monkeypatch):
    from apmx.install.apm_backend import install
    from apmx.contracts.models import ContractLimits

    requests = _unit_backend(tmp_path, monkeypatch)
    install(tmp_path, frozen=True, limits=ContractLimits())
    assert requests[-1].argv[-1] == "--frozen"
    with pytest.raises(ContractError, match="cannot add"):
        install(tmp_path, frozen=True, package_ref="org/repo", limits=ContractLimits())
    assert len(requests) == 2


@pytest.mark.parametrize("failure", ["exit", "timeout", "cleanup", "spawn"])
def test_native_failures_halt_without_leaking_output(tmp_path, monkeypatch, failure):
    from apmx.install.apm_backend import install
    from apmx.contracts.models import ContractLimits, ProcessObservation

    observation = {
        "exit": ProcessObservation(1),
        "timeout": ProcessObservation(None, stop_reason="deadline"),
        "cleanup": ProcessObservation(0, cleanup_confirmed=False),
        "spawn": ProcessObservation(None, error="private host error"),
    }[failure]
    _unit_backend(tmp_path, monkeypatch, install_observation=observation)
    with pytest.raises(ContractError) as error:
        install(tmp_path, limits=ContractLimits())
    assert error.value.code == "apm_install_failed"
    assert "PRIVATE_AUTH_OUTPUT" not in str(error.value)
    assert "private host" not in str(error.value)


def test_wrong_backend_version_refuses_before_install(tmp_path, monkeypatch):
    from apmx.install.apm_backend import install
    from apmx.contracts.models import ContractLimits

    requests = _unit_backend(tmp_path, monkeypatch, version=b"APM wrong version\n")
    with pytest.raises(ContractError, match="does not match"):
        install(tmp_path, limits=ContractLimits())
    assert len(requests) == 1


@pytest.mark.parametrize("target", [
    "linux-x86_64", "linux-arm64", "macos-x86_64", "macos-arm64", "windows-x86_64",
])
def test_version_output_is_exact_platform_pin(target):
    from apmx.install.apm_backend import expected_version_output

    expected = "Agent Package Manager (APM) CLI version 0.30.0"
    if target != "windows-x86_64":
        expected += " (8c2e0d9)"
    assert expected_version_output(target) == expected


@pytest.mark.parametrize("target", ["macos-arm64", "windows-x86_64"])
def test_other_platform_version_shape_does_not_satisfy_pin(tmp_path, monkeypatch, target):
    from apmx.install import apm_backend
    from apmx.contracts.models import ContractLimits

    expected = apm_backend.expected_version_output(target)
    wrong = (
        apm_backend.expected_version_output("windows-x86_64")
        if target == "macos-arm64" else apm_backend.expected_version_output("macos-arm64")
    )
    requests = _unit_backend(tmp_path, monkeypatch, version=wrong.encode())
    monkeypatch.setattr(apm_backend, "expected_version_output", lambda: expected)
    with pytest.raises(ContractError, match="does not match"):
        apm_backend.install(tmp_path, limits=ContractLimits())
    assert len(requests) == 1


@pytest.mark.parametrize("field", ["resolved_commit", "resolved_ref", "version", "content_hash"])
def test_adding_root_cannot_replace_existing_consumer_pin(field):
    from dataclasses import replace
    from apmx.deps.lockfile import LockFile, LockedDependency
    from apmx.install.apm_backend import require_preserved_pins

    locked = LockedDependency(
        repo_url="fixture/context", resolved_commit="a" * 40, resolved_ref="v1",
        version="1.0.0", content_hash="sha256:" + "a" * 64,
    )
    before = LockFile()
    before.add_dependency(locked)
    after = LockFile()
    after.add_dependency(replace(locked, **{field: "changed"}))
    with pytest.raises(ContractError, match="preserve"):
        require_preserved_pins(before, after)


@pytest.mark.parametrize("padding", [" ", "\n", "arbitrary-prefix"])
def test_version_probe_rejects_padded_output(tmp_path, monkeypatch, padding):
    from apmx.install import apm_backend
    from apmx.contracts.models import ContractLimits

    output = (padding + apm_backend.expected_version_output() + "\n").encode()
    requests = _unit_backend(tmp_path, monkeypatch, version=output)
    with pytest.raises(ContractError, match="does not match"):
        apm_backend.install(tmp_path, limits=ContractLimits())
    assert len(requests) == 1


def test_version_output_cannot_disagree_with_record_identity(tmp_path, monkeypatch):
    from apmx.install import apm_backend

    pin = json.loads(apm_backend.PIN_PATH.read_bytes())
    pin["version"] = "0.31.0"
    path = tmp_path / "pin.json"
    path.write_text(json.dumps(pin))
    monkeypatch.setattr(apm_backend, "PIN_PATH", path)
    with pytest.raises(ContractError, match="no pinned"):
        apm_backend.expected_version_output("windows-x86_64")
