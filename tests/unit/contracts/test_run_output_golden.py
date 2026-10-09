"""Golden run transcripts: the default story, its widths and terminal profiles.

The engine/chain call sequence is replayed against the real presenter with fixed
event times and a fixed display clock; no native process or model is invoked.
Regenerate after an intentional presentation change with APMX_UPDATE_GOLDEN=1.
"""

import json
import os
from pathlib import Path

import click
import pytest
from test_logger_presentation import _terminal

from apmx.contracts.models import (
    Artifact,
    ArtifactSet,
    ChainResult,
    CheckObservation,
    CheckSpec,
    FileEntry,
    LeafContract,
    LeafPlan,
    Outcome,
    ProcessObservation,
    RepairBudget,
    RunEvent,
    RunResult,
)
from apmx.contracts.resolution import ChainPlan, Edge, Graph, Node
from apmx.core import contract_logger
from apmx.core.contract_logger import ContractLogger

pytestmark = pytest.mark.component

GOLDEN = Path(__file__).parent / "golden" / "run_output"
PROFILES = {
    "redirected": {"mode": "pipe", "width": 80, "verbose": False},
    "tty40": {"mode": "no_color", "width": 40, "verbose": False},
    "tty80": {"mode": "no_color", "width": 80, "verbose": False},
    "tty120": {"mode": "no_color", "width": 120, "verbose": False},
    "verbose": {"mode": "pipe", "width": 80, "verbose": True},
}
FIRST = ("first", ("notes.md",), ("first.json", "second.json"))
SECOND = ("second", ("notes.md", "first.json", "second.json"), "final.json")


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _contract(root: Path, spec, budget=None) -> LeafContract:
    name, needs, produces = spec
    return LeafContract(
        root / f"{name}.contract.md",
        "sha",
        "PRIVATE_PROMPT",
        needs,
        produces,
        (CheckSpec("identity", "python -I checks/check.py"),),
        budget=budget,
    )


def _plan(root: Path, budget=None) -> ChainPlan:
    first, second = _contract(root, FIRST, budget), _contract(root, SECOND)
    edges = tuple(Edge(second.path, name, first.path) for name in first.outputs)
    graph = Graph(root, (second.path,), (first, second), (first, second), edges)
    nodes = tuple(
        Node(LeafPlan(item, root, Path("/not/invoked/copilot"), model="fixture-model"), ())
        for item in (first, second)
    )
    return ChainPlan(graph, nodes, allow_unproven_inputs=True)


def _check(normalized: int) -> CheckObservation:
    return CheckObservation(
        "identity",
        "python -I checks/check.py",
        ProcessObservation(returncode=normalized),
        normalized,
        "subject",
        "resources",
        f"Check 'identity' exited {normalized}.",
    )


def _emit(logger, kind, at=0.0, source="engine", **data):
    logger.on_event(RunEvent("run", 1, at, kind, source, data))


def _attempt(logger, root: Path, spec, run: str, *, normalized: int, agent: float) -> RunResult:
    """One engine attempt in its real event order."""
    name, needs, produces = spec
    outputs = (produces,) if isinstance(produces, str) else produces
    directory = root / ".apm" / "runs" / run
    _emit(
        logger,
        "selected",
        contract=str(root / f"{name}.contract.md"),
        caller_root=str(root),
        produces=", ".join(outputs),
        needs=needs,
        harness="copilot",
        model="fixture-model",
        run_directory=f"factory/.apm/runs/{run}",
    )
    _emit(logger, "phase", name="preflight")
    for item in needs:
        entry = FileEntry(item, "0" * 64, 33, 0o644)
        _emit(logger, "input_captured", entry=entry, origin="application", producer_record="")
    _emit(logger, "workspace_captured", files=len(needs) + 2)
    _emit(logger, "phase", at=0.2, name="execution")
    _emit(logger, "activity", at=0.3, source="harness", text="Hermetic fixture progress.")
    _emit(
        logger,
        "activity",
        at=0.4,
        source="harness",
        text="Tool started: view",
        tool_status="started",
    )
    _emit(logger, "phase", at=0.2 + agent, name="capture")
    _emit(logger, "phase", at=0.3 + agent, name="checks")
    _emit(logger, "check_started", at=0.3 + agent, name="identity", command="python -I check.py")
    verdict = "pass" if normalized == 0 else "reject"
    _emit(
        logger,
        "activity",
        at=0.4 + agent,
        source="checker",
        label="identity",
        text=f"Independent fixture check: {verdict}",
    )
    _emit(logger, "check_finished", at=0.5 + agent, observation=_check(normalized))
    _emit(logger, "phase", at=0.6 + agent, name="record")
    logger.close()
    files = tuple(Artifact(item, directory / "artifacts" / item, "sha", 33) for item in outputs)
    return RunResult(
        run,
        directory,
        Outcome.COMPLETE if normalized == 0 else Outcome.REJECTED,
        files[0] if isinstance(produces, str) else ArtifactSet(files, "sha"),
        (_check(normalized),),
    )


def _receipt(directory: Path, checks: int) -> Path:
    receipt = directory / "receipt"
    (receipt / "checks").mkdir(parents=True)
    statement = {"predicateType": "https://slsa.dev/provenance/v1"}
    (receipt / "provenance.intoto.json").write_text(json.dumps(statement))
    for index in range(checks):
        (receipt / "checks" / f"run-{index + 1}.intoto.json").write_text("{}")
    (receipt / "abom.cdx.json").write_text(json.dumps({"specVersion": "1.5", "components": []}))
    (receipt / "summary.md").write_text("# Receipt\n")
    return receipt


def _factory_pass(logger, root: Path, clock: _Clock) -> None:
    plan = _plan(root)
    logger.factory_started(plan)
    catalog = tuple(node.plan.contract.path for node in plan.nodes)
    runs = []
    for index, (node, spec, agent) in enumerate(
        zip(plan.nodes, (FIRST, SECOND), (4.0, 3.0), strict=True), start=1
    ):
        logger.chain_node(index, 2, node.plan.contract.path, catalog=catalog)
        leaf = logger.new_leaf(index=index, count=2, contract=node.plan.contract.path)
        result = _attempt(leaf, root, spec, f"run-{index}", normalized=0, agent=agent)
        _emit(leaf, "finished", result=result)
        runs.append(result)
    logger.close()
    clock.now = 9.0
    chain = root / ".apm" / "chains" / "chain-1"
    logger.render_result(
        ChainResult("chain-1", chain / "record.json", Outcome.COMPLETE, True, tuple(runs))
    )
    logger.evidence_package(_receipt(chain, checks=2))


def _factory_reject(logger, root: Path, clock: _Clock) -> None:
    budget = RepairBudget(max_attempts=3, max_seconds=600)
    plan = _plan(root, budget)
    logger.factory_started(plan)
    catalog = tuple(node.plan.contract.path for node in plan.nodes)
    first = plan.nodes[0].plan.contract.path
    logger.chain_node(1, 2, first, catalog=catalog)
    leaf = logger.new_leaf(index=1, count=2, contract=first)
    leaf.repair_budget(budget)
    for index, agent in enumerate((5.0, 4.0, 4.0), start=1):
        attempt = leaf.new_attempt(index=index, count=3)
        result = _attempt(attempt, root, FIRST, f"attempt-{index}", normalized=1, agent=agent)
    _emit(attempt, "finished", result=result)
    leaf.repair_finished(
        reason="max_attempts", record=root / ".apm/controllers/c/record.json", attempts=3
    )
    leaf.close()
    logger.chain_stopped(
        "Predecessor stopped: REJECTED. Inspect its record.",
        outcome=Outcome.REJECTED,
        code="upstream_rejected",
    )
    logger.close()
    clock.now = 14.0
    chain = root / ".apm" / "chains" / "chain-1"
    logger.render_result(
        ChainResult(
            "chain-1",
            chain / "record.json",
            Outcome.REJECTED,
            False,
            (result,),
            "upstream_rejected",
        )
    )


def _contract_pass(logger, root: Path, clock: _Clock) -> None:
    result = _attempt(logger, root, FIRST, "run-1", normalized=0, agent=4.0)
    clock.now = 5.0
    logger.render_result(result)
    logger.evidence_package(_receipt(result.run_directory, checks=1))


SCENARIOS = {
    "factory-pass": (_factory_pass, True),
    "factory-reject-retries": (_factory_reject, True),
    "contract-pass": (_contract_pass, False),
}


def _render(tmp_path, monkeypatch, scenario: str, *, mode: str, width: int, verbose: bool) -> str:
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "factory"
    root.mkdir(exist_ok=True)
    clock = _Clock()
    monkeypatch.setattr(contract_logger, "_clock", clock)
    monkeypatch.setattr(ContractLogger, "_audit_available", staticmethod(lambda: True))
    driver, factory = SCENARIOS[scenario]
    with _terminal(monkeypatch, width=width, mode=mode) as terminal:
        logger = ContractLogger(verbose=verbose)
        if factory:
            logger.remember_invocation(
                "factory", model="fixture-model", factory=True, allow_host_access=True
            )
            logger.select_factory_root(root)
            logger.execution_context(factory=True)
        else:
            monkeypatch.chdir(root)
            logger.remember_invocation(
                "first.contract.md", model="fixture-model", allow_host_access=True
            )
        driver(logger, root, clock)
        raw = terminal.text
    # CRLF is the native Windows line ending, not cursor motion; compare logical lines.
    return raw.replace("\r\n", "\n")


@pytest.mark.parametrize("profile", sorted(PROFILES))
@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_run_output_matches_golden_transcript(tmp_path, monkeypatch, scenario, profile):
    raw = _render(tmp_path, monkeypatch, scenario, **PROFILES[profile])
    assert "\x1b" not in raw
    assert all(" " <= character <= "~" or character == "\n" for character in raw)
    path = GOLDEN / f"{scenario}.{profile}.txt"
    if os.environ.get("APMX_UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw, encoding="ascii")
    assert raw == path.read_text(encoding="ascii")


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_styled_terminal_adds_color_only(tmp_path, monkeypatch, scenario):
    raw = _render(tmp_path, monkeypatch, scenario, mode="styled", width=80, verbose=False)
    assert "\x1b[" in raw
    golden = (GOLDEN / f"{scenario}.tty80.txt").read_text(encoding="ascii")
    assert click.unstyle(raw) == golden


@pytest.mark.parametrize("width", [40, 80, 120])
@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_tty_rows_fit_the_width_except_unbreakable_paths(tmp_path, monkeypatch, scenario, width):
    raw = _render(tmp_path, monkeypatch, scenario, mode="no_color", width=width, verbose=False)
    for line in raw.splitlines():
        if len(line) > width:
            # Copyable paths/commands and literal checker output are never re-wrapped.
            if line.startswith("        identity: "):
                continue
            assert line.lstrip().startswith(("Outputs", "Receipt", "Saved", "Next", "More"))
            assert "/" in line or "apmx" in line


def test_default_story_names_the_five_ideas(tmp_path, monkeypatch):
    text = _render(tmp_path, monkeypatch, "factory-pass", mode="pipe", width=80, verbose=False)
    for fact in (
        "[1/2] first    needs notes.md -> produces first.json, second.json",
        "      attempt 1/1  agent 4.0s   checks: [+] identity",
        "      [+] first.json, second.json -> handed to second",
        "[+] COMPLETE   2/2 contracts   2/2 checks   9.0s",
        "Outputs   factory/.apm/chains/chain-1/artifacts/",
        "          provenance  in-toto + SLSA v1",
        "Next      apmx audit factory/.apm/chains/chain-1/receipt",
    ):
        assert fact in text
    for retired in ("Evidence", "Record", "Directory", "Found input", "Working copy", "Running"):
        assert retired not in text


def test_rejection_names_the_check_attempts_tail_and_waiting_contract(tmp_path, monkeypatch):
    text = _render(
        tmp_path, monkeypatch, "factory-reject-retries", mode="pipe", width=80, verbose=False
    )
    assert "budget 3 attempts" in text
    assert [line.strip()[:11] for line in text.splitlines() if "attempt " in line[:14]] == [
        "attempt 1/3",
        "attempt 2/3",
        "attempt 3/3",
    ]
    assert text.count("identity: Independent fixture check: reject") == 1
    assert "[2/2] second   not started: waits on first" in text
    assert "[x] REJECTED   first failed check identity after 3/3 attempts   exit 20" in text
    assert "Saved     factory/.apm/runs/attempt-3/" in text
    assert "Fix the contract or check, then rerun:  apmx ./factory --model fixture-model" in text
    assert "Receipt" not in text and "handed to" not in text


def test_verbose_keeps_todays_detail_behind_the_flag(tmp_path, monkeypatch):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    default = _render(tmp_path / "a", monkeypatch, "factory-pass", **PROFILES["redirected"])
    verbose = _render(tmp_path / "b", monkeypatch, "factory-pass", **PROFILES["verbose"])
    for detail in (
        "[>] Capturing files for Copilot",
        "Found input: notes.md (application; captured 33 bytes)",
        "Working copy: 3 captured files; execution uses copies.",
        "Copilot > Hermetic fixture progress.",
        "Copilot > Tool started: view",
        "Running check: identity - python -I check.py",
        "Check identity > Independent fixture check: pass",
        "[+] PASS identity",
        "Record: factory/.apm/chains/chain-1/record.json",
        "Summary: factory/.apm/chains/chain-1/receipt/summary.md",
    ):
        assert detail in verbose
        assert detail not in default
    for line in default.splitlines():
        assert line in verbose.splitlines()


def test_missing_receipt_degrades_without_noise(tmp_path, monkeypatch):
    text = _render(tmp_path, monkeypatch, "contract-pass", mode="pipe", width=80, verbose=False)
    assert "Receipt   " in text
    with _terminal(monkeypatch, mode="pipe") as terminal:
        ContractLogger().evidence_package(None)
        assert terminal.text == ""
    with _terminal(monkeypatch, mode="pipe") as terminal:
        monkeypatch.setattr(ContractLogger, "_audit_available", staticmethod(lambda: False))
        logger = ContractLogger()
        logger.evidence_package(_receipt(tmp_path / "later", checks=1))
        assert "apmx audit" not in terminal.text
        assert "Next      read " in terminal.text


def test_rerun_hint_keeps_the_users_selection_and_drops_factory_consent(tmp_path):
    logger = ContractLogger()
    logger.remember_invocation("factory", factory=True, allow_host_access=True)
    assert logger._invocation == "apmx ./factory"
    logger.remember_invocation("first.contract.md", harness="opencode", allow_host_access=True)
    assert logger._invocation == "apmx first.contract.md --on opencode --allow-host-access"
    logger.remember_invocation(".", package_ref="org/pkg#v1", model="m")
    assert logger._invocation == "apmx --from 'org/pkg#v1' --model m"
    logger.remember_invocation("dir with space", factory=True)
    assert logger._invocation == "apmx './dir with space'"


def test_append_only_liveness_is_sparse_and_names_the_latest_status(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with _terminal(monkeypatch, mode="pipe") as terminal:
        logger = ContractLogger()
        _emit(logger, "selected", contract="job.contract.md", produces="out.txt", harness="copilot")
        _emit(logger, "phase", at=1, name="execution")
        _emit(logger, "activity", at=2, source="harness", text="Reading the request.")
        for second in (5, 10, 31, 45, 62):
            _emit(logger, "heartbeat", at=second, elapsed_seconds=second)
        text = terminal.text.replace("\r\n", "\n")
    liveness = [line for line in text.splitlines() if "still running" in line]
    assert liveness == [
        "        still running 31s: Reading the request.",
        "        still running 1m02s: Reading the request.",
    ]
    assert "Reading the request." not in text.replace(liveness[0], "").replace(liveness[1], "")


def test_real_no_dependency_receipt_renders_and_its_next_command_verifies(tmp_path, monkeypatch):
    """A real COMPLETE run delivers a receipt; the printed Next command is a passing audit."""
    import re
    import shlex

    from click.testing import CliRunner
    from test_engine import _fake_adapter
    from test_execution_result import _complete_plan

    from apmx.cli import main
    from apmx.utils import console

    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("CI", "true")
    console._reset_console()
    plan = _complete_plan(tmp_path)
    _fake_adapter(monkeypatch, plan)
    monkeypatch.chdir(tmp_path)
    run = CliRunner().invoke(main, ["job.contract.md", "--on", "copilot", "--allow-host-access"])
    assert run.exit_code == 0, run.output
    (receipt,) = tmp_path.glob(".apm/runs/*/receipt")
    tail = run.output[run.output.index("[+] COMPLETE") :]
    tail = re.sub(r"\d{8}T\d{6}Z-[0-9a-f]{12}", "<run>", tail)
    tail = re.sub(r"   [0-9.]+s\n", "   <t>\n", tail, count=1)
    path = GOLDEN / "contract-pass.real-receipt.txt"
    if os.environ.get("APMX_UPDATE_GOLDEN") == "1":
        path.write_text(tail, encoding="ascii")
    assert tail == path.read_text(encoding="ascii")
    (next_line,) = [line for line in run.output.splitlines() if line.startswith("Next      ")]
    command = shlex.split(next_line.removeprefix("Next      "))
    assert command[:2] == ["apmx", "audit"]
    assert Path(command[2]).resolve() == receipt.resolve()
    audit = CliRunner().invoke(main, command[1:])
    assert audit.exit_code == 0, audit.output
    assert audit.output.rstrip().endswith("[+] VALID   (content-bound; not authenticated)")
