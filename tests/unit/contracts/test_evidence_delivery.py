"""Post-execution evidence delivery never rewrites finalized execution outcomes."""

import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace

import pytest
from click.testing import CliRunner
from test_engine import _fake_adapter, _plan, _python_check
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
        destination = result.run_directory / "receipt"
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
    assert "[+] COMPLETE" in response.output
    assert ("Receipt export failed" in response.output) is bool(failure)
    assert ("\nReceipt   " in response.output) is (failure is None)
    assert "apmx: HALTED" not in response.output
    assert "private filesystem error" not in response.output


def test_ordinary_run_without_dependencies_delivers_a_receipt(tmp_path, monkeypatch) -> None:
    plan = _complete_plan(tmp_path)
    _fake_adapter(monkeypatch, plan)
    monkeypatch.chdir(tmp_path)
    response = CliRunner().invoke(
        main, ["job.contract.md", "--on", "copilot", "--allow-host-access"]
    )
    assert response.exit_code == 0, response.output
    receipts = list(tmp_path.glob(".apm/runs/*/receipt"))
    assert len(receipts) == 1
    relative = receipts[0].relative_to(tmp_path).as_posix()
    assert f"\nReceipt   {relative}/\n" in response.output
    assert "          inventory   CycloneDX 1.5 (0 components)\n" in response.output
    assert f"\nNext      apmx audit {relative}\n" in response.output
    assert not list(tmp_path.glob(".apm/runs/*/evidence"))
    bom = json.loads((receipts[0] / "abom.cdx.json").read_bytes())
    assert bom["components"] == []
    index = json.loads((receipts[0] / "index.json").read_bytes())
    assert index["schema"] == "apmx-evidence-package/1"


def test_rejected_run_gets_no_receipt_but_keeps_its_saved_attempt(tmp_path, monkeypatch) -> None:
    plan = _plan(tmp_path, (_python_check("acceptance", "raise SystemExit(1)"),))
    plan.contract.path.write_text(
        "---\nneeds: input.txt\nproduces: result.txt\nverify:\n  acceptance: "
        + json.dumps(plan.contract.checks[0].command)
        + "\n---\nWrite the result.\n"
    )
    digest = hashlib.sha256(plan.contract.path.read_bytes()).hexdigest()
    plan = replace(plan, contract=replace(plan.contract, source_digest=digest))
    _fake_adapter(monkeypatch, plan)
    monkeypatch.chdir(tmp_path)
    response = CliRunner().invoke(
        main, ["job.contract.md", "--on", "copilot", "--allow-host-access"]
    )
    assert response.exit_code == 20, response.output
    assert "Receipt" not in response.output and "apmx audit" not in response.output
    runs = list(tmp_path.glob(".apm/runs/*"))
    assert len(runs) == 1 and (runs[0] / "record.json").is_file()
    assert not (runs[0] / "receipt").exists() and not (runs[0] / "evidence").exists()
