"""The bundled backend is explicit, immutable in identity, and never found on PATH."""

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

from apmx.contracts.events import ApmInstallEvent, ApmOutputEvent
from apmx.contracts.models import ContractError


def test_source_backend_requires_absolute_explicit_path(tmp_path, monkeypatch):
    from apmx.install.apm_backend import locate_backend

    monkeypatch.setenv("APMX_APM_BACKEND", "apm")
    with pytest.raises(ContractError, match="absolute"):
        locate_backend()


def test_windows_backend_longpaths_is_child_scoped_and_preserves_config(monkeypatch):
    from apmx.install.apm_backend import backend_child_env

    monkeypatch.setattr(sys, "platform", "win32")
    original = {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "http.https://example.test/.extraheader",
        "GIT_CONFIG_VALUE_0": "Authorization: sensitive-fixture",
        "GIT_CONFIG_KEY_1": "core.longpaths",
        "GIT_CONFIG_VALUE_1": "false",
        "GIT_SSH_COMMAND": "original-ssh-command",
        "HOME": "original-home",
        "APM_NO_SCRIPTS": "original-refusal",
    }
    before = original.copy()
    child = backend_child_env(original)
    assert original == before
    assert child["GIT_CONFIG_COUNT"] == "3"
    assert child["GIT_CONFIG_KEY_2"] == "core.longpaths"
    assert child["GIT_CONFIG_VALUE_2"] == "true"
    for key, value in original.items():
        if key not in {"GIT_CONFIG_COUNT", "APM_NO_SCRIPTS"}:
            assert child[key] == value


@pytest.mark.parametrize(
    "configuration",
    [
        {"GIT_CONFIG_COUNT": "sensitive-invalid"},
        {"GIT_CONFIG_COUNT": "-1"},
        {"GIT_CONFIG_COUNT": "9999999999999999999999"},
        {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraheader"},
        {"GIT_CONFIG_KEY_0": "http.extraheader", "GIT_CONFIG_VALUE_0": "sensitive-orphan"},
    ],
)
def test_windows_backend_rejects_invalid_process_config_without_disclosure(
    monkeypatch,
    configuration,
):
    from apmx.install.apm_backend import backend_child_env

    monkeypatch.setattr(sys, "platform", "win32")
    before = configuration.copy()
    with pytest.raises(ContractError) as rejected:
        backend_child_env(configuration)
    assert rejected.value.code == "apm_backend_environment"
    assert "sensitive" not in str(rejected.value)
    assert configuration == before


def test_posix_backend_does_not_change_git_configuration(monkeypatch):
    from apmx.install.apm_backend import backend_child_env

    monkeypatch.setattr(sys, "platform", "darwin")
    original = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.longpaths",
        "GIT_CONFIG_VALUE_0": "false",
    }
    child = backend_child_env(original)
    assert {key: value for key, value in child.items() if key.startswith("GIT_CONFIG_")} == original


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
    pin.write_text(
        json.dumps(
            {
                "schema": "apmx-apm-backend/1",
                "repository": "microsoft/apm",
                "version": "0.30.0",
                "source_commit": "8c2e0d9c352e2ed0e8c56b40063a63e1dd4a1937",
                "assets": {},
            }
        )
    )
    monkeypatch.setattr("apmx.install.apm_backend.PIN_PATH", pin)
    identity = backend_identity(binary)
    assert identity["executable_sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
    assert identity["pin_sha256"] == hashlib.sha256(pin.read_bytes()).hexdigest()
    assert identity["version"] == "0.30.0"


def test_real_backend_installs_transitive_context_without_source_writes(tmp_path, monkeypatch):
    from apmx.contracts.models import ContractLimits
    from apmx.install.apm_backend import install, locate_backend
    from apmx.install.contract_source_validation import source_hash

    # Missing provisioning is a failure, never a mocked or skipped native proof.
    assert locate_backend().is_file()
    limits = ContractLimits()
    source = Path(__file__).resolve().parents[2] / "examples/contracts/packaged-job"
    before = source_hash(source, limits)
    stage = tmp_path / "stage"
    stage.mkdir()
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("CLICOLOR_FORCE", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)
    events = []
    identity = install(
        stage,
        package_ref=str(source),
        limits=limits,
        verbose=True,
        on_preparation=events.append,
    )
    output = [event.text for event in events if isinstance(event, ApmOutputEvent)]
    assert any("[>] Resolving " in line for line in output)
    assert any("Phase:" in line for line in output)
    assert all("\x1b" not in line for line in output)
    assert identity["version"] == "0.30.0"
    assert source_hash(source, limits) == before
    assert (stage / "apm.lock.yaml").is_file()
    skills = list((stage / "apm_modules").rglob("SKILL.md"))
    assert skills
    assert not (stage / ".github").exists()
    assert not (stage / ".copilot").exists()
    assert not (stage / ".mcp.json").exists()


def test_real_backend_replays_anchored_consumer_lock(tmp_path):
    from apmx.contracts.models import ContractLimits
    from apmx.install.apm_backend import install, snapshot_manifest

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


@pytest.mark.parametrize(
    "section",
    [
        "",
        "dependencies: null\n",
        "dependencies: {}\n",
        "dependencies: {apm: []}\n",
    ],
)
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


@pytest.mark.parametrize(
    "section",
    [
        "dependencies: invalid",
        "dependencies: []",
        "dependencies: {apm: null}",
        "dependencies: {apm: invalid}",
        "dependencies: {apm: {name: invalid}}",
    ],
)
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


def test_native_staging_does_not_inherit_caller_path_length(tmp_path):
    from apmx.install.contract_source import _private_root

    caller = tmp_path / ("deep-caller-" * 12)
    caller.mkdir()
    with _private_root(caller, None) as stage:
        assert not stage.is_relative_to(caller)
        assert len(str(stage)) < len(str(caller))
        assert stage.is_dir()
    assert not stage.exists()


def test_native_staging_cleans_up_after_failure(tmp_path):
    from apmx.install.contract_source import _private_root

    with (
        pytest.raises(RuntimeError, match="fixture failure"),
        _private_root(tmp_path, None) as stage,
    ):
        (stage / "partial-install").write_text("partial")
        raise RuntimeError("fixture failure")
    assert not stage.exists()


@pytest.mark.parametrize("inside", ["caller", "source"])
def test_native_staging_refuses_temporary_parent_inside_originals(tmp_path, monkeypatch, inside):
    from apmx.install.contract_source import _private_root

    caller = tmp_path / "caller"
    source = tmp_path / "source"
    caller.mkdir()
    source.mkdir()
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path / inside))
    with pytest.raises(ContractError) as rejected, _private_root(caller, source):
        pytest.fail("Unsafe temporary parent was accepted")
    assert rejected.value.code == "source_escape"
    assert not list(caller.iterdir())
    assert not list(source.iterdir())


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
        on_bytes("stdout", b"https://user:PRIVATE_INSTALL_OUTPUT_MUST_NOT_ESCAPE@example.test/repo")
        on_bytes("stderr", b"Authorization: Bearer PRIVATE_AUTH_OUTPUT_MUST_NOT_ESCAPE")
        return install_observation or ProcessObservation(0)

    monkeypatch.setattr(apm_backend, "supervise_process", supervise)
    return requests


def test_native_invocation_preserves_auth_and_suppresses_activation_child_only(
    tmp_path, monkeypatch
):
    import os

    from apmx.contracts.models import ContractLimits
    from apmx.install.apm_backend import install

    requests = _unit_backend(tmp_path, monkeypatch)
    monkeypatch.setenv("APM_NO_SCRIPTS", "original-value")
    monkeypatch.setenv("APM_GIT_PROTOCOL", "ssh")
    monkeypatch.setenv("APM_ALLOW_PROTOCOL_FALLBACK", "0")
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-token-not-real")
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("CLICOLOR_FORCE", "1")
    monkeypatch.setenv("PY_COLORS", "1")
    monkeypatch.setenv("TERM", "xterm-256color")
    before = dict(os.environ)
    install(tmp_path, package_ref="org/repo/jobs#v1", limits=ContractLimits())
    assert dict(os.environ) == before
    request = requests[-1]
    assert request.cwd == tmp_path
    assert request.argv[1:] == (
        "install",
        "org/repo/jobs#v1",
        "--root",
        str(tmp_path),
        "--only",
        "apm",
        "--target",
        "agent-skills",
        "--no-trust-bin",
    )
    assert request.env["APM_NO_SCRIPTS"] == "1"
    assert request.env["APM_PROGRESS"] == "never"
    assert request.env["NO_COLOR"] == "1"
    assert request.env["TERM"] == "dumb"
    assert request.env["COLUMNS"] == "4096"
    assert not {"FORCE_COLOR", "CLICOLOR_FORCE", "PY_COLORS"} & request.env.keys()
    for name in ("HOME", "GITHUB_TOKEN", "APM_GIT_PROTOCOL", "APM_ALLOW_PROTOCOL_FALLBACK"):
        assert request.env.get(name) == before.get(name)
    assert "--no-policy" not in request.argv
    assert "--trust-transitive-mcp" not in request.argv


@pytest.mark.parametrize("verbose", [False, True])
def test_verbose_requests_native_diagnostics_without_a_second_install(
    tmp_path, monkeypatch, verbose
):
    from apmx.contracts.models import ContractLimits
    from apmx.install.apm_backend import install

    requests = _unit_backend(tmp_path, monkeypatch)
    install(tmp_path, limits=ContractLimits(), verbose=verbose)
    assert ("--verbose" in requests[-1].argv) is verbose
    assert len(requests) == 2


def test_native_install_line_reaches_logger_before_install_returns(tmp_path, monkeypatch, capsys):
    from apmx.contracts.models import ContractLimits, ProcessObservation
    from apmx.core.contract_logger import ContractLogger
    from apmx.install import apm_backend

    _unit_backend(tmp_path, monkeypatch)
    original = apm_backend.supervise_process

    def supervise(request, *, on_bytes, limits):
        if request.argv[1] == "--version":
            return original(request, on_bytes=on_bytes, limits=limits)
        on_bytes("stdout", b"[>] Resolving ./skills/handoff-style...\n")
        assert "APM > [>] Resolving ./skills/handoff-style..." in capsys.readouterr().out
        on_bytes("stderr", b"[!] Package retry needed\n")
        assert "APM stderr > [!] Package retry needed" in capsys.readouterr().out
        return ProcessObservation(0)

    monkeypatch.setattr(apm_backend, "supervise_process", supervise)
    logger = ContractLogger()
    apm_backend.install(tmp_path, limits=ContractLimits(), on_preparation=logger.on_preparation)
    logger.attach_run("run", tmp_path)
    logger.close()
    assert "APM (untrusted) > [>] Resolving" in (tmp_path / "transcript.log").read_text()


def test_frozen_native_replay_never_adds_positional_packages(tmp_path, monkeypatch):
    from apmx.contracts.models import ContractLimits
    from apmx.install.apm_backend import install

    requests = _unit_backend(tmp_path, monkeypatch)
    install(tmp_path, frozen=True, limits=ContractLimits())
    assert requests[-1].argv[-1] == "--frozen"
    with pytest.raises(ContractError, match="cannot add"):
        install(tmp_path, frozen=True, package_ref="org/repo", limits=ContractLimits())
    assert len(requests) == 2


@pytest.mark.parametrize("failure", ["exit", "timeout", "cleanup", "spawn", "cancelled"])
def test_native_failures_halt_without_leaking_output(tmp_path, monkeypatch, failure):
    from apmx.contracts.models import ContractLimits, ProcessObservation
    from apmx.install.apm_backend import install

    observation = {
        "exit": ProcessObservation(1),
        "timeout": ProcessObservation(None, stop_reason="deadline"),
        "cleanup": ProcessObservation(0, cleanup_confirmed=False),
        "spawn": ProcessObservation(None, error="private host error"),
        "cancelled": ProcessObservation(None, stop_reason="cancelled"),
    }[failure]
    _unit_backend(tmp_path, monkeypatch, install_observation=observation)
    events = []
    with pytest.raises(ContractError) as error:
        install(tmp_path, limits=ContractLimits(), on_preparation=events.append)
    assert [event.phase for event in events if isinstance(event, ApmInstallEvent)] == ["started"]
    assert error.value.code == "apm_install_failed"
    assert "PRIVATE_AUTH_OUTPUT" not in str(error.value)
    assert "private host" not in str(error.value)


def test_wrong_backend_version_refuses_before_install(tmp_path, monkeypatch):
    from apmx.contracts.models import ContractLimits
    from apmx.install.apm_backend import install

    requests = _unit_backend(tmp_path, monkeypatch, version=b"APM wrong version\n")
    events = []
    with pytest.raises(ContractError, match="does not match"):
        install(tmp_path, limits=ContractLimits(), on_preparation=events.append)
    assert len(requests) == 1
    assert not events


@pytest.mark.parametrize(
    "scope,frozen,package_ref",
    [
        ("package", False, "https://user:PRIVATE_REQUEST@example.test/repo?token=PRIVATE_QUERY"),
        ("package", False, None),
        ("package", True, None),
        ("consumer", False, None),
        ("consumer", True, None),
    ],
)
def test_preparation_events_describe_only_validated_install_facts(
    tmp_path,
    monkeypatch,
    scope,
    frozen,
    package_ref,
):
    from apmx.contracts.models import ContractLimits
    from apmx.contracts.stream import safe_text
    from apmx.install.apm_backend import install

    requests = _unit_backend(tmp_path, monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "PRIVATE_AUTH_SENTINEL")
    events = []
    identity = install(
        tmp_path,
        package_ref=package_ref,
        frozen=frozen,
        limits=ContractLimits(),
        scope=scope,
        on_preparation=lambda event: events.append((event, len(requests))),
    )
    assert [(event, count) for event, count in events if isinstance(event, ApmInstallEvent)] == [
        (
            ApmInstallEvent(
                "started",
                scope,
                identity["version"],
                frozen,
                package_ref is not None,
                tmp_path,
                package_ref,
            ),
            1,
        ),
        (
            ApmInstallEvent(
                "completed",
                scope,
                identity["version"],
                frozen,
                package_ref is not None,
                tmp_path,
                package_ref,
            ),
            2,
        ),
    ]
    assert "PRIVATE" not in safe_text(repr(events))
    assert len([event for event, _ in events if isinstance(event, ApmOutputEvent)]) == 2
    assert len(requests) == 2  # One version probe and one install, not a logging probe.


@pytest.mark.parametrize("failure", ["changed", "interrupted"])
def test_post_probe_failure_never_emits_install_completion(tmp_path, monkeypatch, failure):
    from apmx.contracts.models import ContractLimits
    from apmx.install import apm_backend

    _unit_backend(tmp_path, monkeypatch)
    if failure == "changed":
        identity = apm_backend.backend_identity(apm_backend.locate_backend())
        from unittest.mock import Mock

        monkeypatch.setattr(
            apm_backend,
            "backend_identity",
            Mock(
                side_effect=[
                    identity,
                    {**identity, "executable_sha256": "changed"},
                ]
            ),
        )
        exception = ContractError
    else:
        supervise = apm_backend.supervise_process

        def interrupted(request, **kwargs):
            if request.argv[1] == "install":
                raise KeyboardInterrupt
            return supervise(request, **kwargs)

        monkeypatch.setattr(apm_backend, "supervise_process", interrupted)
        exception = KeyboardInterrupt
    events = []
    with pytest.raises(exception):
        apm_backend.install(tmp_path, limits=ContractLimits(), on_preparation=events.append)
    assert [event.phase for event in events if isinstance(event, ApmInstallEvent)] == ["started"]


@pytest.mark.parametrize(
    "target",
    [
        "linux-x86_64",
        "linux-arm64",
        "macos-x86_64",
        "macos-arm64",
        "windows-x86_64",
    ],
)
def test_version_output_is_exact_platform_pin(target):
    from apmx.install.apm_backend import expected_version_output

    expected = "Agent Package Manager (APM) CLI version 0.30.0"
    if target != "windows-x86_64":
        expected += " (8c2e0d9)"
    assert expected_version_output(target) == expected


@pytest.mark.parametrize("target", ["macos-arm64", "windows-x86_64"])
def test_other_platform_version_shape_does_not_satisfy_pin(tmp_path, monkeypatch, target):
    from apmx.contracts.models import ContractLimits
    from apmx.install import apm_backend

    expected = apm_backend.expected_version_output(target)
    wrong = (
        apm_backend.expected_version_output("windows-x86_64")
        if target == "macos-arm64"
        else apm_backend.expected_version_output("macos-arm64")
    )
    requests = _unit_backend(tmp_path, monkeypatch, version=wrong.encode())
    monkeypatch.setattr(apm_backend, "expected_version_output", lambda: expected)
    with pytest.raises(ContractError, match="does not match"):
        apm_backend.install(tmp_path, limits=ContractLimits())
    assert len(requests) == 1


@pytest.mark.parametrize("field", ["resolved_commit", "resolved_ref", "version", "content_hash"])
def test_adding_root_cannot_replace_existing_consumer_pin(field):
    from dataclasses import replace

    from apmx.deps.lockfile import LockedDependency, LockFile
    from apmx.install.apm_backend import require_preserved_pins

    locked = LockedDependency(
        repo_url="fixture/context",
        resolved_commit="a" * 40,
        resolved_ref="v1",
        version="1.0.0",
        content_hash="sha256:" + "a" * 64,
    )
    before = LockFile()
    before.add_dependency(locked)
    after = LockFile()
    after.add_dependency(replace(locked, **{field: "changed"}))
    with pytest.raises(ContractError, match="preserve"):
        require_preserved_pins(before, after)


@pytest.mark.parametrize("padding", [" ", "\n", "arbitrary-prefix"])
def test_version_probe_rejects_padded_output(tmp_path, monkeypatch, padding):
    from apmx.contracts.models import ContractLimits
    from apmx.install import apm_backend

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
