"""Post-execution evidence delivery never rewrites finalized execution outcomes."""

import hashlib
import json
from contextlib import contextmanager

import pytest
from click.testing import CliRunner
from test_engine import _fake_adapter
from test_execution_result import _complete_plan

from apmx.cli import main
from apmx.contracts.models import ContractError, ContractSource
from apmx.contracts.records import load_completed_result


@pytest.mark.parametrize("packaged", [False, True])
@pytest.mark.parametrize("failure", [None, "contract", "filesystem", "interrupt"])
def test_both_cli_completion_paths_keep_delivery_separate(
    tmp_path, monkeypatch, packaged, failure
) -> None:
    plan = _complete_plan(tmp_path)
    _fake_adapter(monkeypatch, plan)
    monkeypatch.chdir(tmp_path)
    captured = []

    @contextmanager
    def source(*_, **__):
        yield ContractSource(tmp_path, "job.contract.md")

    monkeypatch.setattr("apmx.cli.prepare_contract_source", source)

    def export(result):
        path = result.run_directory / "record.json"
        captured.append((path, hashlib.sha256(path.read_bytes()).hexdigest()))
        assert load_completed_result(path) == result
        if failure == "contract":
            raise ContractError("Report subject unavailable.", code="check_subject_unavailable")
        if failure == "filesystem":
            raise OSError("private filesystem error")
        if failure == "interrupt":
            raise KeyboardInterrupt
        destination = result.run_directory / "evidence"
        destination.mkdir()
        (destination / "summary.md").write_text("fixture delivered")
        return destination

    monkeypatch.setattr("apmx.contracts.evidence.export_completed", export)
    response = CliRunner().invoke(
        main,
        [
            "job.contract.md",
            "--on",
            "copilot",
            "--allow-host-access",
            *(["--from", "fixture"] if packaged else []),
        ],
    )
    assert response.exit_code == (23 if failure else 0), response.output
    assert len(captured) == 1
    path, digest = captured[0]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    assert json.loads(path.read_bytes())["execution"] == {"name": "COMPLETE", "exit_code": 0}
    assert "Contract COMPLETE" in response.output
    assert ("Evidence delivery failed" in response.output) is bool(failure)
    assert ("Standard package:" in response.output) is (failure is None)
    assert "apmx: HALTED" not in response.output
    assert "private filesystem error" not in response.output


def test_ordinary_no_inventory_run_preserves_default_output(tmp_path, monkeypatch) -> None:
    plan = _complete_plan(tmp_path)
    _fake_adapter(monkeypatch, plan)
    monkeypatch.chdir(tmp_path)
    response = CliRunner().invoke(
        main, ["job.contract.md", "--on", "copilot", "--allow-host-access"]
    )
    assert response.exit_code == 0, response.output
    assert "Standard package:" not in response.output
    assert not list(tmp_path.glob(".apm/runs/*/evidence"))
