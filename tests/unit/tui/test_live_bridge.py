"""Genuine integration tests for the TUI live-execution bridge (milestone 3,
docs/textual-design.md: real supervise_process/engine/chain wiring, not a
replayed fixture). The AI harness call is replaced by a deterministic local
script -- the same hermetic pattern as tests/unit/contracts/test_chain.py's
producer() -- so these run real subprocesses and real checks with no model
spend and no network use, while proving the bridge's event -> card mapping
and cooperative cancellation against the real chain, not a stub of it.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import sys
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest

from apmx.commands.tui_live import LiveFactoryApp, _attach_live, _fire_and_forget, launch_live
from apmx.contracts import resolution
from apmx.contracts.chain import run_chain
from apmx.contracts.models import ChainResult, Outcome, ProcessRequest, RunEvent
from apmx.core.contract_logger import ContractLogger
from apmx.runtime.factory import RuntimeFactory
from apmx.tui.graph import build_nodes

pytestmark = pytest.mark.component


def _write_contract(root: Path, name: str, needs: tuple[str, ...], output: str) -> Path:
    command = f"{shlex.quote(sys.executable)} -B -c {shlex.quote('pass')}"
    path = root / name
    path.write_text(
        f"---\nneeds: {json.dumps(needs)}\nproduces: {output}\n"
        f"verify:\n  exact: {json.dumps(command)}\n---\nProduce {output} from the inputs.\n",
        encoding="ascii",
    )
    return path


@pytest.fixture
def caller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    tools = tmp_path / "tools"
    tools.mkdir()
    executable = tools / ("copilot.exe" if os.name == "nt" else "copilot")
    executable.write_bytes(b"Discovery only; never launched.\n")
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    root = tmp_path / "caller"
    root.mkdir()
    monkeypatch.chdir(root)
    (root / "seed.txt").write_bytes(b"seed")
    _write_contract(root, "z-first.contract.md", ("seed.txt",), "first.txt")
    _write_contract(root, "a-target.contract.md", ("first.txt",), "last.txt")
    return root


def _producer(monkeypatch: pytest.MonkeyPatch) -> list:
    calls: list = []

    def build(plan, snapshot, directory, *, timeout_seconds):
        calls.append(plan)
        code = (
            "from pathlib import Path\n"
            f"target = Path({plan.contract.produces!r})\n"
            "target.parent.mkdir(parents=True, exist_ok=True)\n"
            f"target.write_bytes(b'|'.join(Path(p).read_bytes() for p in {plan.contract.needs!r}))\n"
        )
        completion = json.dumps(
            {"type": "result", "exitCode": 0, "sessionId": "fixture", "usage": {}}
        )
        return ProcessRequest(
            (sys.executable, "-B", "-c", code + f"\nprint({completion!r}, flush=True)\n"),
            snapshot.producer,
            timeout_seconds,
        )

    adapter = Mock()
    adapter.build_contract_request.side_effect = build
    monkeypatch.setattr(RuntimeFactory, "get_runtime_by_name", lambda *a: adapter)
    return calls


def _closure(root: Path):
    graph = resolution.resolve_factory(root)
    return resolution.preflight(graph, root, harness="copilot", allow_unproven_inputs=True)


class _FakeApp:
    """Records what the bridge presents, standing in for a real FactoryApp."""

    def __init__(self) -> None:
        self.status_calls: list[tuple[str, str]] = []
        self.events: list[RunEvent] = []
        self.diagnostics: list[str] = []

    def call_from_thread(self, fn, *args):
        fn(*args)

    def apply_card_status(self, identity: str, status: str) -> None:
        self.status_calls.append((identity, status))

    def append_event(self, event: RunEvent) -> None:
        self.events.append(event)

    def append_diagnostic(self, line: str) -> None:
        self.diagnostics.append(line)


def test_live_run_drives_both_cards_to_passed_and_returns_the_real_result(
    caller: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _producer(monkeypatch)
    closure = _closure(caller)
    identities = tuple(node.identity for node in build_nodes(closure.graph))
    assert identities == ("z-first.contract.md", "a-target.contract.md")

    app = _FakeApp()
    logger = ContractLogger(verbose=False)
    live = _attach_live(logger, identities=identities, app=app, state={"current": None})

    result = run_chain(closure, logger=live, allow_advisory=True, consent_source="flag")

    assert result.outcome is Outcome.COMPLETE
    assert result.runs[-1].artifact.path.read_bytes() == b"seed"
    passed = [identity for identity, status in app.status_calls if status == "passed"]
    assert passed == list(identities)
    running_identities = {identity for identity, status in app.status_calls if status == "running"}
    assert running_identities == set(identities)
    # Each contract's checks genuinely passed here, so a passing
    # check_finished must put the card back to "running", never "retrying"
    # (regression: the bridge used to read None/None from the real engine's
    # CheckObservation-shaped check_finished event and always fell back to
    # "retrying", even when every check passed).
    assert not any(status == "retrying" for _, status in app.status_calls)
    assert any(event.kind == "finished" for event in app.events)
    assert not app.diagnostics


def test_live_run_honours_cooperative_cancellation_between_leaves(
    caller: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _producer(monkeypatch)
    closure = _closure(caller)
    identities = tuple(node.identity for node in build_nodes(closure.graph))

    app = _FakeApp()
    logger = ContractLogger(verbose=False)
    live = _attach_live(logger, identities=identities, app=app, state={"current": None})

    def cancel_requested() -> bool:
        return any(event.kind == "finished" for event in app.events)

    result = run_chain(
        closure,
        logger=live,
        allow_advisory=True,
        consent_source="flag",
        cancel_requested=cancel_requested,
    )

    assert result.outcome is Outcome.HALTED
    assert result.stop_reason == "cancelled"
    # The first leaf genuinely ran and finished before cancellation was observed.
    assert [identity for identity, status in app.status_calls if status == "passed"] == [
        identities[0]
    ]
    # The second leaf was never started: no card ever reached "running" for it.
    assert identities[1] not in [identity for identity, _ in app.status_calls]
    assert result.runs[0].artifact.path.read_bytes() == b"seed"
    assert len(result.runs) == 1


def test_live_run_rejects_cancellation_before_the_first_leaf(
    caller: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _producer(monkeypatch)
    closure = _closure(caller)
    identities = tuple(node.identity for node in build_nodes(closure.graph))
    app = _FakeApp()
    logger = ContractLogger(verbose=False)
    live = _attach_live(logger, identities=identities, app=app, state={"current": None})

    result = run_chain(
        closure,
        logger=live,
        allow_advisory=True,
        consent_source="flag",
        cancel_requested=lambda: True,
    )

    assert result.outcome is Outcome.HALTED
    assert result.stop_reason == "cancelled"
    assert app.status_calls == []
    assert result.runs == ()


def test_native_diagnostic_goes_to_the_bounded_diagnostics_pane_not_activity(
    caller: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mirrors ContractLogger's own _native_diagnostic/_diagnostic split: this
    bounded debug stream must never land in the retained activity feed."""
    app = _FakeApp()
    logger = ContractLogger(verbose=True)
    live = _attach_live(logger, identities=(), app=app, state={"current": None})
    event = RunEvent(
        run_id="r",
        sequence=0,
        elapsed_seconds=0.0,
        kind="native_diagnostic",
        source="harness",
        data={"text": "raw native debug line"},
    )
    live.on_event(event)
    assert app.diagnostics == ["raw native debug line"]
    assert app.events == []


def test_delayed_dispatch_keeps_leaf_and_attempt_identity(
    caller: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dispatch after a later leaf starts must not relabel earlier observations."""
    import apmx.commands.tui_live as bridge

    pending = []
    monkeypatch.setattr(
        bridge, "_fire_and_forget", lambda app, callback, *args: pending.append((callback, args))
    )
    app = _FakeApp()
    logger = _attach_live(
        ContractLogger(), identities=("first", "second"), app=app, state={"current": None}
    )
    first = logger.new_leaf(index=1, count=2, contract=caller / "first.contract.md")
    attempt = first.new_attempt(index=2, count=3)
    attempt.on_event(RunEvent("first-run", 1, 0, "phase", "engine", {"name": "checks"}))
    logger.chain_node(2, 2, caller / "second.contract.md")
    second = logger.new_leaf(index=2, count=2, contract=caller / "second.contract.md")
    second.on_event(RunEvent("second-run", 1, 0, "phase", "engine", {"name": "execution"}))
    for callback, args in pending:
        callback(*args)
    earlier, later = app.events
    assert earlier.context.contract == caller / "first.contract.md"
    assert (earlier.context.attempt, earlier.context.attempt_limit) == (2, 3)
    assert later.context.contract == caller / "second.contract.md"
    assert later.context.attempt == 1


def test_live_factory_app_cancel_binding_sets_the_cooperative_flag(
    caller: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _producer(monkeypatch)
    closure = _closure(caller)
    ready = {"seen_cancel": False}
    # Gates when the background runner is allowed to return (and the app to
    # exit): without this, the runner can notice the first cancel press,
    # return, and have the background thread start exiting the app *before*
    # the test's own second (idempotency) press/pause lands, racing the
    # in-flight pilot interaction against app teardown.
    allow_finish = threading.Event()

    def runner(cancel_requested) -> ChainResult:
        for _ in range(200):
            if cancel_requested():
                ready["seen_cancel"] = True
                break
            import time

            time.sleep(0.01)
        allow_finish.wait(timeout=5)
        return ChainResult(
            chain_id="stub",
            record_path=caller / ".apm" / "stub",
            outcome=Outcome.HALTED if ready["seen_cancel"] else Outcome.COMPLETE,
            complete=False,
            runs=(),
            stop_reason="cancelled" if ready["seen_cancel"] else None,
        )

    app = LiveFactoryApp(closure.graph, runner=runner)

    async def scenario() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.is_cancel_requested() is False
            await pilot.press("c")
            await pilot.pause()
            assert app.is_cancel_requested() is True
            # Pressing again must stay idempotent, not queue a second request.
            await pilot.press("c")
            await pilot.pause()
            # Only now let the runner finish and the app exit: the
            # idempotency check above is fully done against a definitely
            # still-running app.
            allow_finish.set()

    asyncio.run(scenario())
    assert ready["seen_cancel"] is True
    assert app.run_failure is None
    assert app.chain_result is not None
    assert app.chain_result.outcome is Outcome.HALTED


def test_fire_and_forget_does_not_block_when_the_apps_loop_has_already_stopped() -> None:
    """Regression for the windows-x86_64 native CI job stalling for 35
    minutes inside test_live_factory_app_cancel_binding_sets_the_cooperative_flag:
    ``App.call_from_thread`` schedules the callback with
    ``asyncio.run_coroutine_threadsafe`` and then blocks on ``Future.result()``
    with no timeout, which hangs forever if the app's loop has already
    stopped processing by the time the background run thread calls it (the
    exact race that produced the CI stall). This directly reproduces that
    dispatch-after-stop scenario against a real, stopped asyncio loop -- not
    a timing-dependent Textual pilot interaction -- so it fails
    deterministically, not just most of the time, if ``_fire_and_forget``
    regresses back to a blocking wait.
    """

    class _StoppedLoopApp:
        _loop: object = None

    loop = asyncio.new_event_loop()
    runner = threading.Thread(target=loop.run_forever, daemon=True)
    runner.start()
    try:
        while not loop.is_running():
            pass
        loop.call_soon_threadsafe(loop.stop)
        runner.join(timeout=5)
        assert not runner.is_alive(), "setup failed: loop never stopped"
        assert not loop.is_running()

        app = _StoppedLoopApp()
        app._loop = loop
        called: list[str] = []
        finished = threading.Event()

        def call_it() -> None:
            _fire_and_forget(app, called.append, "ran")
            finished.set()

        caller = threading.Thread(target=call_it, daemon=True)
        caller.start()
        caller.join(timeout=5)
        assert finished.is_set(), (
            "_fire_and_forget blocked despite the app's loop already being "
            "stopped -- this is the exact production hang-on-exit bug"
        )
    finally:
        loop.close()


def test_launch_live_disables_terminal_echo_before_the_real_chain_runs(
    caller: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """launch_live() must stop the pre-existing ContractLogger from echoing
    straight to the terminal once Textual owns the screen, while still
    driving the real chain end to end through the real worker-thread path
    App.run() uses.

    ``headless=True`` asks Textual for its own ``HeadlessDriver`` instead of
    a native platform driver: this process has no real console, and unlike
    POSIX's ``LinuxDriver`` (which checks ``os.isatty()`` and falls back to
    a non-blocking ``select()`` read loop that tolerates a non-tty stdin
    fine), Windows' native ``WindowsDriver`` reads real console handles with
    no non-blocking "no console attached" path -- under a non-interactive
    test runner (e.g. GitHub Actions' windows-x86_64 job) that hangs
    indefinitely instead of returning, which is exactly what stalled CI here
    until this fix. ``headless=True`` never reaches production: the real
    ``--tui`` CLI path already refuses to call ``launch_live`` at all unless
    both ends are confirmed real TTYs, so this keeps exercising the exact
    real chain-driving logic this test cares about without depending on a
    native OS console driver's behavior."""
    _producer(monkeypatch)
    closure = _closure(caller)
    logger = ContractLogger(verbose=False)

    result = launch_live(
        closure,
        logger=logger,
        allow_advisory=True,
        consent_source="flag",
        headless=True,
    )

    assert logger._display.enabled is False
    assert result.outcome is Outcome.COMPLETE
    assert result.runs[-1].artifact.path.read_bytes() == b"seed"
