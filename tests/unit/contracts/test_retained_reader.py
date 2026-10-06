"""Read-only projections must reuse canonical completion and assessment checks."""

import json
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch
from test_chain import caller, prepare, producer, two_nodes
from test_execution_result import _execute
from test_repair import budgeted_contract, run

from apmx.contracts import chain, records
from apmx.contracts.models import ContractError, Outcome
from apmx.core.contract_logger import ContractLogger

__all__ = ["caller"]
pytestmark = pytest.mark.component


def test_completed_result_round_trip_without_live_sources(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    plan, result = _execute(tmp_path, monkeypatch)
    path = result.run_directory / "record.json"
    original = path.read_bytes()
    plan.contract.path.unlink()
    (tmp_path / "input.txt").unlink()
    assert records.load_completed_result(path) == result
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "fault",
    [
        "schema",
        "missing-check",
        "duplicate-check",
        "raw-exit",
        "producer",
        "consent",
        "boolean-outcome",
        "source-digest",
        "baseline-digest",
        "unfinished",
        "unknown-result-field",
    ],
)
def test_reader_refuses_inconsistent_records_without_rewriting(
    tmp_path: Path, monkeypatch: MonkeyPatch, fault: str
) -> None:
    _, result = _execute(tmp_path, monkeypatch)
    path = result.run_directory / "record.json"
    data = json.loads(path.read_bytes())
    if fault == "schema":
        data["schema"] = "unsupported"
    elif fault == "missing-check":
        data["checks"] = data["result"]["checks"] = []
    elif fault == "duplicate-check":
        data["checks"].append(data["checks"][0])
        data["result"]["checks"] = data["checks"]
    elif fault == "raw-exit":
        data["checks"][0]["process"]["returncode"] = 2
        data["result"]["checks"] = data["checks"]
    elif fault == "producer":
        data["producer"]["returncode"] = None
    elif fault == "consent":
        data["advisory_consent"] = data["result"]["consent_source"] = None
    elif fault == "boolean-outcome":
        data["execution"]["exit_code"] = False
        data["result"]["outcome"] = data["execution"]
    elif fault == "source-digest":
        data["source"]["sha256"] = "0" * 64
    elif fault == "baseline-digest":
        data["baseline"]["digest"] = "0" * 64
    elif fault == "unfinished":
        data["complete"] = False
    else:
        data["result"]["unexpected"] = True
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    with pytest.raises(ContractError):
        records.load_completed_result(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("target", ["artifacts/result.txt", "source/contract.contract.md"])
def test_reader_revalidates_retained_bytes(
    tmp_path: Path, monkeypatch: MonkeyPatch, target: str
) -> None:
    _, result = _execute(tmp_path, monkeypatch)
    path = result.run_directory / target
    path.chmod(0o600)
    path.write_bytes(b"changed")
    with pytest.raises(ContractError):
        records.load_completed_result(result.run_directory / "record.json")


def test_completed_factory_round_trip(caller: Path, monkeypatch: MonkeyPatch) -> None:
    two_nodes(caller)
    producer(monkeypatch)
    result = chain.run_chain(prepare(caller), logger=ContractLogger(), allow_advisory=True)
    assert records.load_completed_result(result.record_path) == result


def test_factory_reader_refuses_external_children_before_reading(
    caller: Path, monkeypatch: MonkeyPatch
) -> None:
    two_nodes(caller)
    producer(monkeypatch)
    result = chain.run_chain(prepare(caller), logger=ContractLogger(), allow_advisory=True)
    data = json.loads(result.record_path.read_bytes())
    external = caller.parent / "unrelated"
    data["result"]["runs"][0]["run_directory"] = str(external)
    result.record_path.write_text(json.dumps(data))
    original = records._record_bytes

    def read(path: Path, limits):
        assert path.parent != external, "Untrusted child escaped the invocation."
        return original(path, limits)

    monkeypatch.setattr(records, "_record_bytes", read)
    with pytest.raises(ContractError):
        records.load_completed_result(result.record_path)


@pytest.mark.parametrize("input_kind", ["leaf", "controller"])
def test_budgeted_reader_keeps_prior_rejection_linkage(
    caller: Path, monkeypatch: MonkeyPatch, input_kind: str
) -> None:
    path = budgeted_contract(
        caller,
        {"max_attempts": 2, "max_seconds": 30},
        check="from pathlib import Path; assert Path('answer.txt').read_text() == 'good'",
    )
    calls = producer(
        monkeypatch,
        body=lambda *_: (
            "from pathlib import Path\n"
            f"Path('answer.txt').write_text({'good' if len(calls) == 2 else 'bad'!r})"
        ),
    )
    result = run(path, caller)
    selected = (
        result.controller.path
        if input_kind == "controller"
        else result.run_directory / "record.json"
    )
    assert records.load_completed_result(selected) == result
    history = records.completed_attempt_history(result)
    assert [attempt.outcome for attempt in history] == [Outcome.REJECTED, Outcome.COMPLETE]
    assert all(attempt.controller == result.controller for attempt in history)
    controller = json.loads(result.controller.path.read_bytes())
    first = Path(controller["attempts"][0]["record"]).parent / "artifacts/answer.txt"
    first.chmod(0o600)
    first.write_bytes(b"altered history")
    with pytest.raises(ContractError):
        records.load_completed_result(selected)
