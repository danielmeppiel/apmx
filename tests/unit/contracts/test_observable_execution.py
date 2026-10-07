"""Execution must explain its work live, not only retain it for later inspection."""

from pathlib import Path

import pytest

from apmx.contracts.events import EventEmitter
from apmx.contracts.models import CheckObservation, ProcessObservation, RepairBudget
from apmx.core.contract_logger import ContractLogger

pytestmark = pytest.mark.component


@pytest.mark.parametrize("harness", ["copilot", "opencode"])
def test_public_activity_and_passing_check_output_are_live_by_default(
    capsys: pytest.CaptureFixture[str], harness: str
) -> None:
    logger = ContractLogger()
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
    logger.close()


def test_attempt_wording_explains_the_bound_and_first_pass_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    logger = ContractLogger()
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
    logger = ContractLogger()
    logger.evidence_package(tmp_path)
    text = capsys.readouterr().out
    assert "Evidence package:" in text
    assert "Standard package:" not in text
    for value in ("SLSA", "in-toto", "CycloneDX", "ABOM", "SHA-256", "unsigned"):
        assert value in text
    for name in ("provenance.intoto.json", "abom.cdx.json", "definition.json", "index.json"):
        assert name in text
    logger.close()
