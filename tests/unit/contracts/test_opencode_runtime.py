"""Native startup fixtures and shared-engine checks; no real model execution."""

import json
import sys
from dataclasses import replace
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from test_engine import _plan, _python_check
from test_opencode_stream import frame

from apmx.cli import main
from apmx.contracts import engine
from apmx.contracts.models import (
    BaselineSnapshot,
    ContractError,
    ImportedSkill,
    Outcome,
    ProcessObservation,
    ProcessRequest,
)
from apmx.core.contract_logger import ContractLogger
from apmx.runtime.opencode_runtime import OpenCodeRuntime
from apmx.runtime.registry import get_runtime_descriptor

pytestmark = pytest.mark.component


@pytest.mark.parametrize("harness,label", (("copilot", "Copilot"), ("opencode", "OpenCode")))
def test_package_consent_names_the_selected_harness_before_preparation(
    tmp_path, monkeypatch, harness, label
):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        main, ["--from", "owner/package", "job.contract.md", "--on", harness]
    )
    assert result.exit_code == 21
    assert f"{label} and checks can read or change files" in result.output
    assert not list(tmp_path.iterdir())


def setup(tmp_path, monkeypatch, *, config=None, changed=None, version=b"1.2.24\n"):
    for name in ("OPENCODE_CONFIG_CONTENT", "OPENCODE_CONFIG_DIR", "OPENCODE_PERMISSION"):
        monkeypatch.delenv(name, raising=False)
    plan = replace(_plan(tmp_path, ()), harness="opencode", model=None)
    run = tmp_path / ".apm/runs/fixture"
    snapshot = BaselineSnapshot(
        root=run / "baseline",
        producer=run / "producer",
        files=(),
        digest="base",
        original_head=None,
        synthetic_head="head",
        resources_digest="checks",
    )
    requests = []
    initial = config or {}

    def supervise(request, *, on_bytes, limits):
        requests.append(request)
        args = request.argv[1:]
        if args == ("--version",):
            raw = version
        elif args == ("debug", "paths"):
            raw = f"home       {tmp_path}\nconfig     {tmp_path / 'global-config'}\n".encode()
        else:
            assert args == ("debug", "config")
            effective = dict(initial)
            if "OPENCODE_CONFIG_CONTENT" in request.env:
                profile = json.loads(request.env["OPENCODE_CONFIG_CONTENT"])
                effective.update(profile)
                effective["mcp"] = {
                    name: {**initial.get("mcp", {}).get(name, {}), **value}
                    for name, value in profile["mcp"].items()
                }
                if changed:
                    effective.update(changed)
            raw = json.dumps(effective).encode()
        on_bytes("stderr", b"PRIVATE_DIAGNOSTIC")
        on_bytes("stdout", raw)
        return ProcessObservation(returncode=0)

    monkeypatch.setattr("apmx.contracts.process.supervise_process", supervise)
    return plan, snapshot, run, requests


@pytest.mark.parametrize("model", [None, "github-copilot/gpt-5.6-sol"])
def test_native_profile_reuses_tools_and_does_not_choose_a_model_or_provider(
    tmp_path, monkeypatch, model
):
    plan, snapshot, run, requests = setup(
        tmp_path,
        monkeypatch,
        config={"mcp": {"configured-server": {"environment": {"SECRET": "PRIVATE_VALUE"}}}},
    )
    skill = ImportedSkill(
        name="testing-guidance",
        source_path=tmp_path / "SKILL.md",
        content="selected content",
        source_digest="selected",
        lock_identity="fixture",
        kind="skill",
    )
    plan = replace(plan, model=model, imported_skills=(skill,))
    request = OpenCodeRuntime().build_contract_request(plan, snapshot, run, timeout_seconds=30)
    assert get_runtime_descriptor("opencode").supports_contracts
    assert request.argv[:4] == (str(plan.executable), "run", "--format", "json")
    assert request.argv[4].startswith(plan.contract.body + "\n\nFixed file instructions:")
    assert "Use read to read" in request.argv[4]
    assert "apmx_artifacts_write_file" in request.argv[4]
    assert request.argv[5:7] == ("--agent", "build")
    assert ("--model" in request.argv) is (model is not None)
    if model:
        assert request.argv[-2:] == ("--model", model)
    assert 0 < request.timeout_seconds <= 30
    profile = json.loads(request.env["OPENCODE_CONFIG_CONTENT"])
    assert "model" not in profile and "enabled_providers" not in profile
    assert profile["permission"] == {
        "*": "deny",
        "read": "allow",
        "skill": {"*": "deny", "testing-guidance": "allow"},
        "apmx_artifacts_write_file": "allow",
        "apmx_artifacts_delete_file": "allow",
        "apmx_artifacts_export_changes": "allow",
    }
    assert profile["mcp"]["configured-server"] == {"enabled": False}
    assert profile["mcp"]["apmx_artifacts"]["command"] == [
        sys.executable,
        "-B",
        "-m",
        "apmx.runtime.artifact_mcp",
    ]
    assert profile["skills"] == {"paths": [str(snapshot.producer / ".agents/skills")], "urls": []}
    assert len(requests) == 4
    assert request.env["OPENCODE_DISABLE_PROJECT_CONFIG"] == "true"
    assert request.env["OPENCODE_AUTO_SHARE"] == "false"
    assert "PRIVATE" not in (run / "native-tools/opencode.json").read_text()
    assert "PRIVATE" not in str(request.control_observations)


def test_unselected_global_agents_are_not_invoked_or_copied(tmp_path, monkeypatch):
    plan, snapshot, run, _ = setup(
        tmp_path,
        monkeypatch,
        config={"agent": {"code-reviewer": {"prompt": "PRIVATE_UNSELECTED_PROMPT"}}},
    )
    request = OpenCodeRuntime().build_contract_request(plan, snapshot, run, timeout_seconds=30)
    assert request.argv[5:7] == ("--agent", "build")
    assert "PRIVATE" not in str(request.control_observations)
    assert "PRIVATE" not in request.env["OPENCODE_CONFIG_CONTENT"]
    assert json.loads(request.env["OPENCODE_CONFIG_CONTENT"])["permission"]["*"] == "deny"


@pytest.mark.parametrize(
    "configuration",
    [
        {"plugin": ["unobserved-plugin"]},
        {"instructions": ["unobserved-instruction"]},
        {"agent": {"build": {"prompt": "unobserved"}}},
        {"mode": {"build": {}}},
        {"agent": {"title": {"prompt": "unobserved"}}},
        {"agent": {"summary": {"model": "unselected-provider/model"}}},
        {"agent": {"compaction": {"prompt": "unobserved"}}},
        {"mode": {"title": {}}},
        {"mode": {"summary": {}}},
        {"mode": {"compaction": {}}},
        {"default_agent": "custom"},
        {"permission": {"read": "deny"}},
        {"permission": {"*": "allow"}},
        {"tools": {"read": False}},
        {"mcp": []},
        {"mcp": {"bad name": {}}},
        {"mcp": {"apmx_artifacts": {}}},
    ],
)
def test_unobservable_startup_refuses_before_producer_or_private_tool_setup(
    tmp_path, monkeypatch, configuration
):
    plan, snapshot, run, _ = setup(tmp_path, monkeypatch, config=configuration)
    with pytest.raises(ContractError):
        OpenCodeRuntime().build_contract_request(plan, snapshot, run, timeout_seconds=30)
    assert not (run / "native-tools").exists()


@pytest.mark.parametrize(
    "changed",
    [
        {"permission": {"*": "allow"}},
        {"skills": {"paths": [], "urls": ["https://example.test/skills"]}},
        {"share": "auto"},
        {"plugin": ["managed-plugin"]},
        {"mcp": {}},
    ],
)
def test_effective_overrides_are_not_silently_bypassed(tmp_path, monkeypatch, changed):
    plan, snapshot, run, _ = setup(tmp_path, monkeypatch, changed=changed)
    with pytest.raises(ContractError) as failure:
        OpenCodeRuntime().build_contract_request(plan, snapshot, run, timeout_seconds=30)
    assert failure.value.code == "native_configuration_unobservable"


@pytest.mark.parametrize(
    "variable", ["OPENCODE_PERMISSION", "OPENCODE_CONFIG_DIR", "OPENCODE_CONFIG_CONTENT"]
)
def test_conflicting_environment_is_refused_without_reading_its_value(
    tmp_path, monkeypatch, variable
):
    plan, snapshot, run, requests = setup(tmp_path, monkeypatch)
    monkeypatch.setenv(variable, "PRIVATE_VALUE")
    with pytest.raises(ContractError) as failure:
        OpenCodeRuntime().build_contract_request(plan, snapshot, run, timeout_seconds=30)
    assert "PRIVATE" not in str(failure.value) and not requests


def test_global_prompt_is_not_read_modified_or_hidden(tmp_path, monkeypatch):
    plan, snapshot, run, _ = setup(tmp_path, monkeypatch)
    prompt = tmp_path / "global-config/AGENTS.md"
    prompt.parent.mkdir()
    prompt.write_text("PRIVATE_GLOBAL_INSTRUCTIONS")
    with pytest.raises(ContractError):
        OpenCodeRuntime().build_contract_request(plan, snapshot, run, timeout_seconds=30)
    assert prompt.read_text() == "PRIVATE_GLOBAL_INSTRUCTIONS"
    assert not (run / "native-tools").exists()


@pytest.mark.parametrize(
    "raw",
    [
        b"config relative/path\n",
        b"config /first\nconfig /second\n",
        b"config /bad\x1bpath\n",
        b"config /bad\nunrecognized-path-continuation\n",
        b'{"config":"/not-the-native-format"}\n',
        b"config \xff\n",
    ],
)
def test_native_path_metadata_must_be_unambiguous(tmp_path, monkeypatch, raw):
    plan, snapshot, _, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(OpenCodeRuntime, "_probe", lambda *args: raw)
    with pytest.raises(ContractError):
        OpenCodeRuntime._config_root(plan, snapshot, {}, 0)


@pytest.mark.parametrize("version", [b"1.18.32\n", b"unexpected\n", b""])
def test_untested_native_version_is_not_assumed_compatible(tmp_path, monkeypatch, version):
    plan, snapshot, run, requests = setup(tmp_path, monkeypatch, version=version)
    with pytest.raises(ContractError) as failure:
        OpenCodeRuntime().build_contract_request(plan, snapshot, run, timeout_seconds=30)
    assert failure.value.code == "unsupported_native_version" and len(requests) == 1


@pytest.mark.parametrize(
    "observation,oversized",
    [
        (ProcessObservation(returncode=1), False),
        (ProcessObservation(returncode=0, cleanup_confirmed=False), False),
        (ProcessObservation(returncode=0, stop_reason="timeout"), False),
        (ProcessObservation(returncode=0), True),
    ],
)
def test_startup_probe_failure_or_overflow_never_returns_private_data(
    tmp_path, monkeypatch, observation, oversized
):
    plan, snapshot, run, _ = setup(tmp_path, monkeypatch)

    def supervise(request, *, on_bytes, limits):
        on_bytes("stderr", b"PRIVATE_DIAGNOSTIC")
        on_bytes("stdout", b"PRIVATE_CONFIG" * (24000 if oversized else 1))
        return observation

    monkeypatch.setattr("apmx.contracts.process.supervise_process", supervise)
    with pytest.raises(ContractError) as failure:
        OpenCodeRuntime().build_contract_request(plan, snapshot, run, timeout_seconds=30)
    assert failure.value.code == "native_configuration_failed"
    assert "PRIVATE" not in str(failure.value)
    assert not (run / "native-tools").exists()


@pytest.mark.parametrize(
    "raw", [b'{"model":"first","model":"second"}', b'{"value":NaN}', b"[]", b"invalid"]
)
def test_effective_configuration_requires_strict_json(tmp_path, monkeypatch, raw):
    plan, snapshot, _, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(OpenCodeRuntime, "_probe", lambda *args: raw)
    with pytest.raises(ContractError):
        OpenCodeRuntime._configuration(plan, snapshot, {}, 0)


def test_expired_shared_deadline_starts_no_native_probe(tmp_path, monkeypatch):
    plan, snapshot, run, requests = setup(tmp_path, monkeypatch)
    with pytest.raises(ContractError) as failure:
        OpenCodeRuntime().build_contract_request(plan, snapshot, run, timeout_seconds=0)
    assert failure.value.code == "attempt_deadline" and not requests


@pytest.mark.parametrize(
    "wire,exit_code,expected,checks",
    [
        (frame("step_start") + frame("step_finish", reason="stop"), 0, Outcome.COMPLETE, 1),
        (frame("step_start") + frame("step_finish", reason="stop"), 1, Outcome.HALTED, 0),
        (frame("step_start") + frame("step_finish", reason="tool-calls"), 0, Outcome.HALTED, 0),
        (
            b'{"type":"error","sessionID":"fixture","error":{"name":"APIError"}}\n',
            0,
            Outcome.HALTED,
            0,
        ),
        (b"invalid-json\n", 0, Outcome.HALTED, 0),
    ],
)
@pytest.mark.parametrize("content", [b"hello\n", b"hello\r\n"])
def test_shared_engine_owns_capture_checks_and_completion(
    tmp_path, monkeypatch, wire, exit_code, expected, checks, content
):
    plan = replace(
        _plan(tmp_path, (_python_check("fixed", "assert True"),)),
        harness="opencode",
        model=None,
    )
    (tmp_path / "input.txt").write_bytes(content)
    code = (
        "from pathlib import Path\nimport sys\n"
        "Path('result.txt').write_bytes(Path('input.txt').read_bytes())\n"
        f"sys.stdout.buffer.write({wire!r})\nraise SystemExit({exit_code})\n"
    )

    def request(selected, snapshot, directory, *, timeout_seconds):
        return ProcessRequest((sys.executable, "-c", code), snapshot.producer, timeout_seconds)

    adapter = Mock()
    adapter.build_contract_request.side_effect = request
    monkeypatch.setattr(engine.frontend, "plan_contract", lambda *args, **kwargs: plan)
    monkeypatch.setattr(engine.RuntimeFactory, "get_runtime_by_name", lambda *args: adapter)
    logger = ContractLogger()
    result = engine.run_contract(plan, logger=logger, allow_advisory=True)
    assert result.outcome is expected and len(result.checks) == checks
    assert result.artifact.path.read_bytes() == content
    assert result.observed_models == ()
    assert not (tmp_path / "result.txt").exists()
    transcript = (result.run_directory / "transcript.log").read_text()
    assert "Running OpenCode" in transcript and "Running Copilot" not in transcript
