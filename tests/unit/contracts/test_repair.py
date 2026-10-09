"""Bounded repair uses real attempt/check records, never a second acceptance oracle."""

import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from test_chain import caller, prepare, producer, write_contract
from test_engine import _python_check

from apmx.cli import main
from apmx.contracts import chain, engine, frontend, records, repair
from apmx.contracts.git_export import ExportContext, export_changes, write_file
from apmx.contracts.models import ContractError, ContractLimits, Outcome, ProcessObservation
from apmx.core.contract_logger import ContractLogger

__all__ = ["caller"]
pytestmark = pytest.mark.component


def budgeted_contract(
    root: Path, budget: object, *, check: str = "pass", name: str = "repair.contract.md"
) -> Path:
    path = write_contract(root, name, ("seed.txt",), "answer.txt", check=check)
    path.write_text(
        path.read_text().replace("---\n", f"---\nbudget: {json.dumps(budget)}\n", 1),
        encoding="ascii",
    )
    return path


def test_budget_is_an_explicit_bounded_authoring_contract(caller: Path) -> None:
    path = budgeted_contract(caller, {"max_attempts": 3, "max_seconds": 600})
    contract = frontend.parse_contract(path)
    assert contract.budget.max_attempts == 3
    assert contract.budget.max_seconds == 600
    plain = write_contract(caller, "plain.contract.md", ("seed.txt",), "plain.txt")
    assert frontend.parse_contract(plain).budget is None


@pytest.mark.parametrize(
    "budget",
    [
        None,
        False,
        [],
        {},
        {"max_attempts": 2},
        {"max_seconds": 30},
        {"max_attempts": True, "max_seconds": 30},
        {"max_attempts": 0, "max_seconds": 30},
        {"max_attempts": 17, "max_seconds": 30},
        {"max_attempts": 2.0, "max_seconds": 30},
        {"max_attempts": 2, "max_seconds": False},
        {"max_attempts": 2, "max_seconds": 0},
        {"max_attempts": 2, "max_seconds": 86401},
        {"max_attempts": 2, "max_seconds": "30"},
        {"usd": 1},
        {"max_attempts": 2, "max_seconds": 30, "cost": 1},
    ],
)
def test_invalid_budget_is_rejected_before_execution(caller: Path, budget: object) -> None:
    with pytest.raises(ContractError) as rejected:
        frontend.parse_contract(budgeted_contract(caller, budget))
    assert rejected.value.code == "invalid_budget"
    assert rejected.value.location.line >= 2


@pytest.mark.parametrize("harness", ("copilot", "opencode"))
def test_rejected_candidate_repairs_against_frozen_original_inputs(
    caller: Path, monkeypatch: pytest.MonkeyPatch, harness: str
) -> None:
    path = budgeted_contract(
        caller,
        {"max_attempts": 3, "max_seconds": 30},
        check="from pathlib import Path; assert Path('answer.txt').read_text() == 'good'",
    )
    produced = []

    def body(plan, snapshot, directory):
        assert (snapshot.root / "seed.txt").read_bytes() == b"seed"
        value = "bad" if not produced else "good"
        if produced:
            assert snapshot.repair is not None
            assert snapshot.repair.attempt == 2
            assert (snapshot.producer / ".apm/repair/answer.txt").read_text() == "bad"
            assert "exact" in snapshot.repair.diagnostics
        else:
            (caller / "seed.txt").write_bytes(b"caller changed after initial capture")
        produced.append(value)
        return f"from pathlib import Path\nPath('answer.txt').write_text({value!r})"

    calls = producer(monkeypatch, body=body)
    plan = frontend.plan_contract(path, caller, harness=harness)
    result = engine.run_contract(plan, logger=ContractLogger(), allow_advisory=True)
    assert result.outcome is Outcome.COMPLETE
    assert len(calls) == 2 and produced == ["bad", "good"]
    assert result.controller is not None
    assert records.load_completed_result(result.controller.path) == result
    assert records.load_completed_result(result.run_directory / "record.json") == result
    controller = json.loads(result.controller.path.read_bytes())
    assert controller["schema"] == "apmx-contract-controller/1"
    assert controller["selected_run_id"] == result.run_id
    assert len(controller["attempts"]) == 2
    first = json.loads(Path(controller["attempts"][0]["record"]).read_bytes())
    assert first["result"]["outcome"]["name"] == "REJECTED"
    assert first["attempt_id"].endswith("/1")
    assert records.finalized_inputs(plan, result)[0].artifact.path.read_text() == "good"
    assert not (caller / "answer.txt").exists()


@pytest.mark.parametrize("exit_code,expected", [(1, 2), (2, 1), (127, 1)])
@pytest.mark.parametrize("harness", ("copilot", "opencode"))
def test_only_complete_candidate_rejections_are_retryable(
    caller: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int, expected: int, harness: str
) -> None:
    path = budgeted_contract(
        caller, {"max_attempts": 3, "max_seconds": 30}, check=f"raise SystemExit({exit_code})"
    )
    calls = producer(monkeypatch)
    result = engine.run_contract(
        frontend.plan_contract(path, caller, harness=harness),
        logger=ContractLogger(),
        allow_advisory=True,
    )
    assert len(calls) == expected
    assert result.outcome is not Outcome.COMPLETE
    controller = json.loads(result.controller.path.read_bytes())
    assert controller["stop_reason"] == ("no_progress" if exit_code == 1 else "not_retryable")


def run(path: Path, caller: Path, *, harness: str = "copilot", **options):
    return engine.run_contract(
        frontend.plan_contract(path, caller, harness=harness, **options),
        logger=ContractLogger(),
        allow_advisory=True,
    )


def test_attempt_exhaustion_retains_all_distinct_rejections(
    caller: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = budgeted_contract(
        caller, {"max_attempts": 3, "max_seconds": 30}, check="raise SystemExit(1)"
    )
    calls = producer(
        monkeypatch,
        body=lambda *_: f"from pathlib import Path\nPath('answer.txt').write_text('{len(calls)}')",
    )
    result = run(path, caller)
    data = json.loads(result.controller.path.read_bytes())
    assert len(calls) == 3
    assert result.outcome is Outcome.REJECTED
    assert data["stop_reason"] == "max_attempts" and data["selected_run_id"] is None
    assert [
        (Path(row["record"]).parent / "artifacts/answer.txt").read_text()
        for row in data["attempts"]
    ] == ["1", "2", "3"]
    with pytest.raises(ContractError) as denied:
        records.finalized_inputs(frontend.plan_contract(path, caller, harness="copilot"), result)
    assert denied.value.outcome is Outcome.REJECTED


def test_operational_error_takes_precedence_over_another_rejection(
    caller: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = budgeted_contract(
        caller, {"max_attempts": 3, "max_seconds": 30}, check="raise SystemExit(1)"
    )
    command = json.dumps(_python_check("operational", "raise SystemExit(127)").command)
    path.write_text(
        path.read_text().replace("\n---\nProduce", f"\n  operational: {command}\n---\nProduce"),
        encoding="ascii",
    )
    calls = producer(monkeypatch)
    result = run(path, caller)
    assert result.outcome is Outcome.REJECTED
    assert [item.normalized for item in result.checks] == [1, 2]
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["missing", "protocol", "cancelled", "cleanup"])
@pytest.mark.parametrize("harness", ("copilot", "opencode"))
def test_operational_producer_failures_never_retry(
    caller: Path, monkeypatch: pytest.MonkeyPatch, failure: str, harness: str
) -> None:
    path = budgeted_contract(caller, {"max_attempts": 3, "max_seconds": 30})
    code = (
        'print(\'{"type":"result","exitCode":"invalid"}\', flush=True)'
        if failure == "protocol"
        else "pass"
    )
    calls = producer(monkeypatch, body=lambda *_: code)
    if failure in ("cancelled", "cleanup"):
        supervise = engine.process.supervise_process
        observation = ProcessObservation(
            returncode=0,
            stop_reason="cancelled" if failure == "cancelled" else None,
            cleanup_confirmed=failure != "cleanup",
        )

        def native_failure(request, **kwargs):
            if request.argv[0] == sys.executable:
                return observation
            return supervise(request, **kwargs)

        monkeypatch.setattr(engine.process, "supervise_process", native_failure)
    result = run(path, caller, harness=harness)
    assert len(calls) == 1
    assert result.outcome is (Outcome.UNPROVEN if failure == "missing" else Outcome.HALTED)
    assert json.loads(result.controller.path.read_bytes())["stop_reason"] == "not_retryable"


@pytest.mark.parametrize("harness", ("copilot", "opencode"))
def test_one_shared_deadline_reaches_the_real_producer_supervisor(
    caller: Path, monkeypatch: pytest.MonkeyPatch, harness: str
) -> None:
    path = budgeted_contract(caller, {"max_attempts": 3, "max_seconds": 1.5})
    calls = producer(monkeypatch, body=lambda *_: "import time; time.sleep(10)")
    result = run(path, caller, harness=harness)
    assert len(calls) == 1
    assert result.outcome is Outcome.HALTED
    child = json.loads((result.run_directory / "record.json").read_bytes())
    assert child["producer"]["stop_reason"] == "timeout"
    assert child["producer"]["cleanup_confirmed"] is True
    assert child["producer"]["elapsed_seconds"] < 5


@pytest.mark.parametrize("harness", ("copilot", "opencode"))
def test_check_timeout_never_becomes_candidate_repair(
    caller: Path, monkeypatch: pytest.MonkeyPatch, harness: str
) -> None:
    path = budgeted_contract(
        caller, {"max_attempts": 3, "max_seconds": 30}, check="import time; time.sleep(10)"
    )
    calls = producer(monkeypatch)
    result = run(path, caller, harness=harness, limits=replace(ContractLimits(), check_seconds=0.1))
    assert len(calls) == 1
    assert result.checks[0].process.stop_reason == "timeout"
    assert result.checks[0].normalized == 2 and result.outcome is Outcome.UNPROVEN


@pytest.mark.parametrize("failure", ["deadline", "cancelled"])
def test_between_attempt_stop_has_a_durable_unselected_controller(
    caller: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    path = budgeted_contract(
        caller, {"max_attempts": 3, "max_seconds": 30}, check="raise SystemExit(1)"
    )
    calls = producer(monkeypatch)
    clock = [time.monotonic()]
    monkeypatch.setattr(repair, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    diagnostics = repair._diagnostics

    def stop_between(result, plan):
        if failure == "cancelled":
            raise KeyboardInterrupt
        clock[0] += 31
        return diagnostics(result, plan)

    monkeypatch.setattr(repair, "_diagnostics", stop_between)
    with pytest.raises(ContractError) as stopped:
        run(path, caller)
    assert stopped.value.code == ("cancelled" if failure == "cancelled" else "budget_deadline")
    assert len(calls) == 1
    data = json.loads(next((caller / ".apm/controllers").glob("*/record.json")).read_bytes())
    assert data["phase"] == "halted" and data["finalized"] is False
    assert data["selected_run_id"] is None and len(data["attempts"]) == 1
    assert Path(data["attempts"][0]["record"]).is_file()


@pytest.mark.parametrize("target", ["artifact", "record", "source", "checks"])
def test_changed_evidence_or_acceptance_never_launches_a_repair(
    caller: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    checks = caller / "checks"
    checks.mkdir()
    (checks / "reject.py").write_text("raise SystemExit(1)\n", encoding="ascii")
    path = budgeted_contract(
        caller,
        {"max_attempts": 3, "max_seconds": 30},
        check="import runpy; runpy.run_path('checks/reject.py')",
    )
    calls = producer(monkeypatch)
    admitted = records.rejected_inputs

    def tamper(plan, result):
        previous = admitted(plan, result)
        if previous is not None:
            selected = {
                "artifact": previous[0].artifact.path,
                "record": previous[0].record_path,
                "source": path,
                "checks": checks / "reject.py",
            }[target]
            mode = selected.stat().st_mode & 0o777
            selected.chmod(mode | 0o200)
            raw = selected.read_bytes()
            selected.write_bytes(b"X" * len(raw) if target == "artifact" else raw + b"\nchanged")
            selected.chmod(mode)
        return previous

    monkeypatch.setattr(records, "rejected_inputs", tamper)
    if target == "source":
        with pytest.raises(ContractError, match="plan_changed"):
            run(path, caller)
    else:
        result = run(path, caller)
        assert result.outcome is Outcome.HALTED
        assert result.stop_reason in {"artifact_changed", "record_changed", "plan_changed"}
    assert len(calls) == 1


def test_document_rejection_uses_the_same_controller(
    caller: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = budgeted_contract(
        caller,
        {"max_attempts": 2, "max_seconds": 30},
        check="from pathlib import Path; assert '\\n## Usage\\n' in Path('answer.txt').read_text()",
    )
    calls = producer(
        monkeypatch,
        body=lambda *_: (
            "from pathlib import Path\nPath('answer.txt').write_text("
            + repr("# Guide\n" if len(calls) == 1 else "# Guide\n\n## Usage\nUse the API.\n")
            + ")"
        ),
    )
    result = run(path, caller)
    assert len(calls) == 2 and result.outcome is Outcome.COMPLETE
    assert "## Usage" in result.artifact.path.read_text()


@pytest.mark.parametrize("allow", [False, True])
def test_factory_handoff_keeps_controller_and_strict_assurance_policy(
    caller: Path, monkeypatch: pytest.MonkeyPatch, allow: bool
) -> None:
    budgeted_contract(
        caller,
        {"max_attempts": 2, "max_seconds": 30},
        name="first.contract.md",
        check="from pathlib import Path; assert Path('answer.txt').read_text() == 'good'",
    )
    write_contract(caller, "a-target.contract.md", ("answer.txt",), "last.txt")
    calls = producer(
        monkeypatch,
        body=lambda plan, *_: (
            f"from pathlib import Path\nPath({plan.contract.produces!r}).write_text("
            + repr("bad" if len(calls) == 1 else "good")
            + ")"
        ),
    )
    result = chain.run_chain(
        prepare(caller, allow=allow), logger=ContractLogger(), allow_advisory=True
    )
    assert len(calls) == (3 if allow else 2)
    assert result.outcome is (Outcome.COMPLETE if allow else Outcome.UNPROVEN)
    assert result.runs[0].controller is not None
    if allow:
        assert calls[-1][0].input_bindings[0].controller == result.runs[0].controller
        boundary = records.CompletionBoundary()
        boundary.capture(result)
        boundary.validate(result)
        reference = result.runs[0].controller
        reference.path.write_bytes(reference.path.read_bytes() + b" ")
        with pytest.raises(ContractError, match="changed"):
            boundary.validate(result)
    else:
        assert result.stop_reason == "unproven_input"


def test_unbudgeted_records_and_launch_count_remain_unchanged(
    caller: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_contract(
        caller, "plain.contract.md", ("seed.txt",), "answer.txt", check="raise SystemExit(1)"
    )
    calls = producer(monkeypatch)
    result = run(path, caller)
    assert result.outcome is Outcome.REJECTED and len(calls) == 1
    assert not (caller / ".apm/controllers").exists()
    data = json.loads((result.run_directory / "record.json").read_bytes())
    assert "controller" not in data and "controller" not in data["result"]
    assert "repair" not in data["baseline"]


def test_preview_discloses_budget_without_execution(
    caller: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    budgeted_contract(caller, {"max_attempts": 3, "max_seconds": 600})
    calls = producer(monkeypatch)
    result = CliRunner().invoke(main, [str(caller), "--on", "copilot", "--plan"])
    assert result.exit_code == 0, result.output
    assert "budget 3 attempts, 600s" in result.output
    assert "one attempt per step, no retries" not in result.output
    assert not calls and not (caller / ".apm").exists()


@pytest.mark.parametrize("owner", ["controller", "attempt"])
def test_failed_persistence_never_selects_or_announces_success(
    caller: Path, monkeypatch: pytest.MonkeyPatch, capsys, owner: str
) -> None:
    path = budgeted_contract(caller, {"max_attempts": 3, "max_seconds": 30})
    calls = producer(monkeypatch)
    write = records.AttemptStore._write

    def fail_finish(store):
        write(store)
        controlled = isinstance(store, records.ControllerStore)
        if controlled == (owner == "controller") and store._data["phase"] == "finished":
            raise OSError("injected durable-write failure")

    monkeypatch.setattr(records.AttemptStore, "_write", fail_finish)
    with pytest.raises(ContractError):
        run(path, caller)
    data = json.loads(next((caller / ".apm/controllers").glob("*/record.json")).read_bytes())
    assert len(calls) == 1 and len(data["attempts"]) == 1
    assert data["finalized"] is False and data["selected_run_id"] is None
    assert data["execution"]["name"] == "HALTED"
    assert "COMPLETE" not in capsys.readouterr().out


@pytest.mark.parametrize("target", ["record", "baseline", "artifact", "transcript", "project"])
def test_prior_rejection_remains_part_of_completion_integrity(
    caller: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    path = budgeted_contract(
        caller,
        {"max_attempts": 2, "max_seconds": 30},
        check="from pathlib import Path; assert Path('answer.txt').read_text() == 'good'",
    )
    calls = producer(
        monkeypatch,
        body=lambda *_: (
            "from pathlib import Path\nPath('answer.txt').write_text("
            + repr("bad" if len(calls) == 1 else "good")
            + ")"
        ),
    )
    result = run(path, caller)
    boundary = records.CompletionBoundary()
    boundary.capture(result)
    first = calls[0][2]
    if target == "project":
        selected = result.controller.path.parent / "project/seed.txt"
    else:
        selected = (
            first
            / {
                "record": "record.json",
                "baseline": "baseline/seed.txt",
                "artifact": "artifacts/answer.txt",
                "transcript": "transcript.log",
            }[target]
        )
    mode = selected.stat().st_mode & 0o777
    selected.chmod(mode | 0o200)
    selected.write_bytes(selected.read_bytes() + b"\nchanged")
    selected.chmod(mode)
    with pytest.raises(ContractError):
        boundary.validate(result)


def test_partial_multi_file_delivery_is_not_retried(
    caller: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = budgeted_contract(caller, {"max_attempts": 3, "max_seconds": 30})
    path.write_text(
        path.read_text().replace("produces: answer.txt", "produces: [answer.txt, report.md]"),
        encoding="ascii",
    )
    calls = producer(
        monkeypatch,
        body=lambda *_: "from pathlib import Path\nPath('answer.txt').write_text('only')",
    )
    result = run(path, caller)
    assert result.outcome is Outcome.UNPROVEN and result.artifact is None
    assert len(calls) == 1 and not result.checks


def test_each_repair_exports_a_complete_patch_against_original_baseline(
    caller: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (caller / "seed.txt").write_bytes(b"original\n")
    path = budgeted_contract(
        caller,
        {"max_attempts": 2, "max_seconds": 30},
        check=(
            "from pathlib import Path; from apmx.contracts.process import local_git; "
            "local_git(Path.cwd(), 'apply', '--', 'changes.diff'); "
            "assert Path('seed.txt').read_bytes() == b'good\\n'"
        ),
    )
    path.write_text(
        path.read_text().replace("produces: answer.txt", "produces: changes.diff"), encoding="ascii"
    )

    def body(plan, snapshot, directory):
        assert (snapshot.producer / "seed.txt").read_bytes() == b"original\n"
        context = ExportContext(
            snapshot.root,
            snapshot.producer,
            directory,
            snapshot.files,
            plan.contract.outputs,
            path.name,
            plan.limits,
        )
        write_file(context, "seed.txt", "bad\n" if len(calls) == 1 else "good\n")
        with pytest.raises(ContractError):
            write_file(context, ".apm/repair/changes.diff", "not writable")
        receipt = export_changes(context, "changes.diff")
        assert receipt["changed_files"] == ["seed.txt"]
        return "pass"

    calls = producer(monkeypatch, body=body)
    result = run(path, caller)
    assert result.outcome is Outcome.COMPLETE and len(calls) == 2
    patch = result.artifact.path.read_bytes()
    assert b"-original\n" in patch and b"+good\n" in patch
    assert b"-bad\n" not in patch and b".apm/repair" not in patch
    assert (caller / "seed.txt").read_bytes() == b"original\n"


def test_budgeted_passing_leaf_cannot_bypass_its_controller(
    caller: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = budgeted_contract(caller, {"max_attempts": 1, "max_seconds": 30})
    producer(monkeypatch)
    result = run(path, caller)
    bare_attempt = replace(result, controller=None)
    plan = frontend.plan_contract(path, caller, harness="copilot")
    with pytest.raises(ContractError, match="controller"):
        records.admit_handoffs(plan, bare_attempt, allow_unproven=True)
    with pytest.raises(ContractError):
        records.CompletionBoundary().capture(bare_attempt)
