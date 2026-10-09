"""Execution must explain its work live, not only retain it for later inspection."""

import json
from pathlib import Path

import pytest

from apmx.contracts.events import EventEmitter
from apmx.contracts.models import CheckObservation, ProcessObservation, RepairBudget
from apmx.core.contract_logger import ContractLogger

pytestmark = pytest.mark.component


@pytest.mark.parametrize("harness", ["copilot", "opencode"])
def test_public_activity_and_passing_check_output_are_live_with_verbose(
    capsys: pytest.CaptureFixture[str], harness: str
) -> None:
    logger = ContractLogger(verbose=True)
    events = EventEmitter("run", logger.on_event)
    events.emit("selected", contract="build.contract.md", harness=harness, produces="changes.diff")
    capsys.readouterr()
    events.emit("activity", source="harness", text="Reading the requested implementation.")
    assert "Reading the requested implementation." in capsys.readouterr().out
    events.emit("check_started", name="acceptance", command="python checks/acceptance.py")
    events.emit("activity", source="checker", label="acceptance", text="Threshold example passed.")
    live = capsys.readouterr().out
    assert "python checks/acceptance.py" in live
    assert "Threshold example passed." in live
    assert "PASS acceptance" not in live
    events.emit(
        "check_finished",
        observation=CheckObservation(
            "acceptance",
            "python checks/acceptance.py",
            ProcessObservation(0),
            0,
            "subject",
            "checks",
            "Check exited 0.",
        ),
    )
    finished = capsys.readouterr().out
    assert "PASS acceptance" in finished
    assert "Threshold example passed." not in finished
    events.emit("phase", name="record")
    assert "attempt 1/1   checks: [+] acceptance" in capsys.readouterr().out
    logger.close()


def test_default_output_folds_activity_into_one_attempt_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    logger = ContractLogger()
    events = EventEmitter("run", logger.on_event)
    events.emit("selected", contract="build.contract.md", produces="changes.diff")
    capsys.readouterr()
    events.emit("phase", name="execution")
    events.emit("activity", source="harness", text="Reading the requested implementation.")
    # The latest agent status feeds the live line; it is not printed as a log line.
    assert logger._live_label().endswith("Reading the requested implementation.")
    events.emit("check_started", name="acceptance", command="python checks/acceptance.py")
    events.emit("activity", source="checker", label="acceptance", text="Threshold example passed.")
    assert capsys.readouterr().out == ""
    events.emit(
        "check_finished",
        observation=CheckObservation(
            "acceptance",
            "python checks/acceptance.py",
            ProcessObservation(0),
            0,
            "subject",
            "checks",
            "Check exited 0.",
        ),
    )
    events.emit("phase", name="record")
    assert capsys.readouterr().out.startswith("      attempt 1/1  agent ")
    logger.close()


def test_attempt_wording_explains_the_bound_and_first_pass_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    logger = ContractLogger(verbose=True)
    logger.repair_budget(RepairBudget(max_attempts=3, max_seconds=600))
    attempt = logger.new_attempt(index=1, count=3)
    attempt.close()
    logger.repair_finished(reason="complete", record=tmp_path / "record.json", attempts=1)
    text = capsys.readouterr().out
    assert "Attempts: up to 3" in text and "600s" in text
    assert "Attempt 1 of 3" in text and "Accepted on attempt 1." in text
    assert "Repair budget" not in text and "Repair controller" not in text
    logger.close()


def test_delivered_evidence_names_the_actual_standards(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Nothing written yet: the receipt names no standard it cannot show.
    ContractLogger().evidence_package(tmp_path)
    empty = capsys.readouterr().out
    assert empty.startswith("Receipt   ")
    for value in ("SLSA", "in-toto", "CycloneDX"):
        assert value not in empty
    (tmp_path / "checks").mkdir()
    statement = {"predicateType": "https://slsa.dev/provenance/v1"}
    (tmp_path / "provenance.intoto.json").write_text(json.dumps(statement))
    for index in (1, 2):
        (tmp_path / "checks" / f"run-{index}.intoto.json").write_text("{}")
    (tmp_path / "abom.cdx.json").write_text(json.dumps({"specVersion": "1.5", "components": []}))
    logger = ContractLogger(verbose=True)
    logger.evidence_package(tmp_path)
    text = capsys.readouterr().out
    assert "Evidence" not in text and "Standard package:" not in text
    for line in (
        "          provenance  in-toto + SLSA v1\n",
        "          checks      in-toto test-result (2)\n",
        "          inventory   CycloneDX 1.5 (0 components)\n",
        "unsigned",
    ):
        assert line in text
    for name in ("provenance.intoto.json", "abom.cdx.json", "definition.json", "index.json"):
        assert name in text
    logger.close()
