"""Workspace regressions: scope, lifecycle, safe inspection and real command coordination."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from contextlib import contextmanager

import click
import pytest
from click.testing import CliRunner
from test_live_bridge import _closure, _producer, caller
from textual.widgets import DataTable, Input, Static, Tree

from apmx.cli import main
from apmx.commands.tui_live import LiveFactoryApp, _fire_and_forget
from apmx.contracts.models import ContractError, EventContext, Outcome, RunEvent
from apmx.core.contract_logger import ContractLogger
from apmx.tui.app import ActivityPane, ContractCard, EvidencePane, FactoryApp
from apmx.tui.consent import ConsentScreen
from apmx.tui.inspection import PREVIEW_BYTES, read_preview
from apmx.tui.state import EVENT_LIMIT

__all__ = ["caller"]
pytestmark = pytest.mark.component


async def eventually(predicate, pilot, *, turns=200):
    for _ in range(turns):
        await pilot.pause(0.02)
        if predicate():
            return
    raise AssertionError("UI/command did not reach expected state")


def event(contract, kind="activity", *, attempt=1, run="one", **data):
    return RunEvent(
        run,
        1,
        0,
        kind,
        "engine",
        data,
        EventContext(contract.path, 1, attempt, 3),
    )


def test_manual_selection_scopes_history_and_does_not_move_search_focus(caller, monkeypatch):
    closure = _closure(caller)
    first, second = closure.graph.order
    app = FactoryApp(closure.graph)

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.append_event(event(first, text="earlier only"))
            app.append_event(event(first, attempt=2, run="retry", text="earlier retry"))
            app.append_event(event(second, run="later", text="later only"))
            app.select(first.path.name)
            await pilot.press("slash")
            await pilot.press("e", "a", "r")
            search = app.query_one(Input)
            app.apply_card_status(second.path.name, "running")
            app.append_event(event(second, run="later", text="later still running"))
            await pilot.pause()
            app._refresh_views()
            assert app.focused is search
            assert search.value == "ear"
            assert app.selected_identity == first.path.name
            assert app.active_identity == second.path.name
            lines = "\n".join(app.query_one(ActivityPane)._history)
            assert "earlier only" in lines and "earlier retry" in lines
            assert "later only" not in lines and "later still" not in lines
            app.action_toggle_follow()
            assert app.selected_identity == second.path.name
            assert app.focused is search
            assert search.value == "ear"

    asyncio.run(asyncio.wait_for(scenario(), timeout=30))


def test_event_history_and_pane_storage_are_bounded(caller):
    closure = _closure(caller)
    app = FactoryApp(closure.graph)
    first = closure.graph.order[0]

    async def scenario():
        async with app.run_test() as pilot:
            for index in range(EVENT_LIMIT + 100):
                app.append_event(event(first, text=f"line {index}"))
            app._refresh_views()
            assert len(app.observations.events) == EVENT_LIMIT
            assert app.observations.omitted == 100
            assert len(app.query_one(ActivityPane)._history) <= EVENT_LIMIT
            assert len(app.observations.attempts) == 1
            await pilot.pause()

    asyncio.run(scenario())


@pytest.mark.parametrize("size", [(120, 40), (80, 24), (60, 20)])
def test_graph_tabs_and_inspection_have_real_geometry(caller, size):
    app = FactoryApp(_closure(caller).graph)

    async def scenario():
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            graph = app.query_one("#graph-row")
            tabs = app.query_one("#lower-tabs")
            assert graph.region.height >= 5
            assert tabs.region.bottom <= size[1] - 1
            assert tabs.region.height >= 7
            assert graph.region.height > tabs.region.height if size == (120, 40) else True
            for key, name in [
                ("a", "activity-pane"),
                ("k", "checks-pane"),
                ("o", "outputs-pane"),
                ("e", "evidence-pane"),
            ]:
                await pilot.press(key)
                pane = app.query_one("#" + name)
                assert pane.region.height >= 3
                assert pane.region.bottom <= size[1]

    asyncio.run(scenario())


@pytest.mark.parametrize("reduced", [True, False])
def test_motion_only_changes_active_card_without_focus_or_geometry_effect(caller, reduced):
    app = FactoryApp(_closure(caller).graph, reduced_motion=reduced)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            card = app.query(ContractCard).first()
            app.apply_card_status(card.node.identity, "running")
            before = str(card.render())
            geometry = card.region
            focus = app.focused
            app._tick()
            assert (before == str(card.render())) is reduced
            assert card.region == geometry
            assert app.focused is focus
            app.apply_card_status(card.node.identity, "cancelled")
            final = str(card.render())
            app._tick()
            assert str(card.render()) == final
            assert card.started is None

    asyncio.run(scenario())


@pytest.mark.parametrize("key,expected", [("y", True), ("n", False), ("enter", False)])
def test_consent_keyboard_matches_advertised_prompt(caller, key, expected):
    app = FactoryApp(_closure(caller).graph)
    decisions = []

    async def scenario():
        async with app.run_test() as pilot:
            app.push_screen(ConsentScreen("Run? [y/N]"), decisions.append)
            await pilot.pause()
            await pilot.press(key)
            assert decisions == [expected]

    asyncio.run(scenario())


def test_preview_escapes_controls_redacts_secrets_and_bounds_reads(tmp_path):
    path = tmp_path / "output.txt"
    path.write_bytes(b"\x1b]52;c;owned\x07\nhello\x00")
    assert "Binary file" in read_preview(path)
    path.write_bytes(b"\x1b[31mred\x07\n" + b"x" * (PREVIEW_BYTES + 30))
    text = read_preview(path)
    assert "\x1b" not in text and "\x07" not in text
    assert "\\x1b" in text and "truncated" in text
    assert len(text) <= PREVIEW_BYTES + 100


def test_preview_refuses_replaced_parent_link(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    external = tmp_path / "outside"
    external.mkdir()
    (external / "secret").write_text("must not read")
    try:
        (root / "artifacts").symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not permit symlink creation")
    with pytest.raises(OSError, match="symbolic link"):
        read_preview(root / "artifacts" / "secret", root=root)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="Named FIFO creation is POSIX-only")
def test_preview_refuses_special_files_without_waiting_for_a_writer(tmp_path):
    fifo = tmp_path / "output"
    os.mkfifo(fifo)
    with pytest.raises(OSError, match="regular"):
        read_preview(fifo)


@pytest.mark.parametrize("delivery_failure", [False, True])
def test_canonical_completion_finishes_before_persistent_results(
    caller, monkeypatch, delivery_failure
):
    """Real CLI/chain/checks/records; block export to prove Finalizing vs Explore."""
    _producer(monkeypatch)
    monkeypatch.setattr("apmx.tui.entry.tui_eligible", lambda: True)
    entered = threading.Event()
    release = threading.Event()
    observed = {}

    def export(result):
        entered.set()
        assert release.wait(5)
        assert result.record_path.exists()
        if delivery_failure:
            raise ContractError("deliberate evidence refusal", code="inventory_invalid")

    monkeypatch.setattr("apmx.contracts.evidence.export_completed", export)

    def launch(**kwargs):
        app = LiveFactoryApp(
            command=kwargs["command"], planning=kwargs["planning"], source=kwargs["source"]
        )
        # Pilot otherwise waits on Textual's stopped animator after q exits.
        app.animation_level = "none"

        async def scenario():
            async with app.run_test() as pilot:
                await eventually(entered.is_set, pilot)
                await pilot.pause()
                assert app.lifecycle == "Finalizing"
                assert app.command_code is None
                assert app.final_result.outcome is Outcome.COMPLETE
                release.set()
                await eventually(lambda: app.command_code is not None, pilot)
                assert app.lifecycle == "Explore results"
                assert app.is_running
                assert len(app.outputs) == 2
                assert len(app.query_one(DataTable).columns) == 5
                assert app.final_result.outcome is Outcome.COMPLETE
                summary = str(app.query_one("#result-summary", Static).render())
                assert "Execution: COMPLETE" in summary
                assert "Evidence:" in summary
                if delivery_failure:
                    assert "FAILED (exit 23)" in summary
                assert app.check_action("cancel_run", ()) is False
                observed["record"] = app.final_result.record_path
                observed["delivery"] = app.delivery_state
                await pilot.press("q")

        asyncio.run(asyncio.wait_for(scenario(), timeout=30))
        app._worker.join(5)
        assert not app._worker.is_alive()
        return app.command_code

    monkeypatch.setattr("apmx.commands.tui_live.launch_workspace", launch)
    result = CliRunner().invoke(
        main, [str(caller), "--tui", "--allow-host-access", "--allow-unproven-inputs"]
    )
    release.set()
    assert result.exit_code == (23 if delivery_failure else 0), (result.output, result.exception)
    assert (
        "FAILED (exit 23)" in observed["delivery"]
        if delivery_failure
        else "Not applicable" in observed["delivery"]
    )
    record = json.loads(observed["record"].read_text())
    assert record["result"]["outcome"]["name"] == "COMPLETE"


def test_default_no_preparation_and_execution_do_not_run_producer(caller, monkeypatch):
    calls = _producer(monkeypatch)
    monkeypatch.setattr("apmx.tui.entry.tui_eligible", lambda: True)
    monkeypatch.setattr(ContractLogger, "can_confirm_factory", lambda self: True)
    decisions = []

    def launch(**kwargs):
        app = LiveFactoryApp(command=kwargs["command"], source=kwargs["source"])

        async def scenario():
            async with app.run_test() as pilot:
                await eventually(lambda: isinstance(app.screen, ConsentScreen), pilot)
                decisions.append(app.lifecycle)
                await pilot.press("y")
                await eventually(lambda: app.lifecycle == "Approve execution", pilot)
                assert not isinstance(app.screen, ConsentScreen)
                assert app.query_one("#graph-row").region.height >= 5
                assert len(app.query(ContractCard)) == 2
                assert app.focused.id == "execution-no"
                await pilot.press("enter")
                await eventually(lambda: app.command_code is not None, pilot)
                assert app.command_code == 21
                assert not app.outputs
                assert str(app.query_one("#output-path", Static).render()) == "No outputs captured."
                await pilot.press("q")

        asyncio.run(scenario())
        app._worker.join(5)
        return app.command_code

    monkeypatch.setattr("apmx.commands.tui_live.launch_workspace", launch)
    result = CliRunner().invoke(main, [str(caller), "--tui"])
    assert result.exit_code == 21, (result.output, result.exception)
    assert decisions == ["Approve preparation"]
    assert not calls
    assert not (caller / ".apm/runs").exists()


def test_evidence_refresh_preserves_cursor_without_opening_inspection(caller):
    app = FactoryApp(_closure(caller).graph)

    async def scenario():
        async with app.run_test() as pilot:
            app.action_show_tab("evidence-tab")
            await pilot.pause()
            pane = app.query_one(EvidencePane)
            pane.replace_groups([("Records", [("First", "detail")])])
            tree = pane.query_one(Tree)
            tree.root.children[0].expand()
            await pilot.pause()
            tree.move_cursor(tree.root.children[0].children[0])
            await pilot.pause()
            screen = app.screen
            pane.replace_groups([("Records", [("First", "detail"), ("Second", "new")])])
            await pilot.pause()
            assert app.screen is screen
            assert str(tree.cursor_node.label) == "First"
            assert tree.root.children[0].is_expanded

    asyncio.run(scenario())


@pytest.mark.parametrize("key,expected", [("y", True), ("n", False), ("enter", False)])
def test_graph_visible_execution_consent_keys(caller, key, expected):
    response = threading.Event()
    answer = [False]
    app = LiveFactoryApp(_closure(caller).graph, command=lambda *_: response.wait(5))

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            app.request_consent("Run these 2 contracts? [y/N]", response, answer)
            await pilot.pause()
            assert app.query_one("#graph-row").region.height >= 5
            assert app.focused.id == "execution-no"
            await pilot.press(key)
            assert response.is_set()
            assert answer == [expected]

    asyncio.run(scenario())


def test_background_dispatch_creates_consent_in_textual_context(caller):
    done = threading.Event()
    app = FactoryApp(_closure(caller).graph)

    async def scenario():
        async with app.run_test() as pilot:

            def dispatch():
                _fire_and_forget(app, app.push_screen, ConsentScreen("Thread-created screen"))
                done.set()

            thread = threading.Thread(target=dispatch)
            thread.start()
            await eventually(lambda: isinstance(app.screen, ConsentScreen), pilot)
            assert done.is_set()
            await pilot.press("n")
            thread.join(1)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["record_changed", "source_cleanup"])
def test_teardown_or_validation_failure_never_publishes_factory_complete(
    caller, monkeypatch, failure
):
    from apmx.contracts.records import CompletionBoundary
    from apmx.install import contract_source

    _producer(monkeypatch)
    monkeypatch.setattr("apmx.tui.entry.tui_eligible", lambda: True)
    original_prepare = contract_source.prepare_imports
    original_capture = CompletionBoundary.capture
    original_validate = CompletionBoundary.validate
    counts = {"capture": 0, "validate": 0, "export": 0}
    records = []

    def capture(boundary, result):
        counts["capture"] += 1
        original_capture(boundary, result)

    def validate(boundary, result):
        counts["validate"] += 1
        original_validate(boundary, result)

    def export(result):
        counts["export"] += 1
        raise AssertionError("Invalid completion must not reach export")

    @contextmanager
    def prepare(*args, **kwargs):
        with original_prepare(*args, **kwargs) as prepared:
            yield prepared
        record = next((caller / ".apm/chains").glob("*/record.json"))
        records.append(record)
        data = json.loads(record.read_text())
        assert data["result"]["outcome"]["name"] == "COMPLETE"
        if failure == "record_changed":
            data["unexpected_teardown_write"] = True
            record.write_text(json.dumps(data))
        else:
            raise ContractError("Preparation cleanup failed", code="source_cleanup")

    monkeypatch.setattr(contract_source, "prepare_imports", prepare)
    monkeypatch.setattr(CompletionBoundary, "capture", capture)
    monkeypatch.setattr(CompletionBoundary, "validate", validate)
    monkeypatch.setattr("apmx.contracts.evidence.export_completed", export)

    def launch(**kwargs):
        app = LiveFactoryApp(command=kwargs["command"])
        app.animation_level = "none"

        async def scenario():
            async with app.run_test() as pilot:
                await eventually(lambda: app.command_code is not None, pilot)
                assert app.command_code == 22
                assert app.final_result is None
                assert len(app.outputs) == 2
                assert app.delivery_path is None
                summary = str(app.query_one("#result-summary", Static).render())
                assert "Execution: COMPLETE" not in summary
                assert "No validated factory result" in summary
                assert not any(card.started for card in app.query(ContractCard))
                await pilot.press("q")

        asyncio.run(asyncio.wait_for(scenario(), timeout=30))
        app._worker.join(5)
        assert not app._worker.is_alive()
        return app.command_code

    monkeypatch.setattr("apmx.commands.tui_live.launch_workspace", launch)
    result = CliRunner().invoke(
        main, [str(caller), "--tui", "--allow-host-access", "--allow-unproven-inputs"]
    )
    assert result.exit_code == 22, (result.output, result.exception)
    assert counts == {"capture": 1, "validate": int(failure == "record_changed"), "export": 0}
    assert json.loads(records[0].read_text())["result"]["outcome"]["name"] == "HALTED"


@pytest.mark.parametrize("complete_before_answer", [False, True])
def test_quit_confirmation_is_not_lost_when_finalization_finishes(caller, complete_before_answer):
    release = threading.Event()

    def command(presentation, cancel):
        presentation.phase("Finalizing")
        assert release.wait(5)
        raise click.exceptions.Exit(23)

    app = LiveFactoryApp(_closure(caller).graph, command=command)
    app.animation_level = "none"

    async def scenario():
        async with app.run_test() as pilot:
            await eventually(lambda: app.lifecycle == "Finalizing", pilot)
            app.action_quit()
            await pilot.pause()
            assert isinstance(app.screen, ConsentScreen)
            if complete_before_answer:
                release.set()
                await eventually(lambda: app.command_code == 23, pilot)
            app.screen.dismiss(True)
            await pilot.pause()
            if not complete_before_answer:
                assert app.is_cancel_requested()
                assert app.is_running
                release.set()
            await eventually(lambda: not app.is_running, pilot)
            assert app.command_code == 23

    try:
        asyncio.run(asyncio.wait_for(scenario(), timeout=15))
    finally:
        release.set()
        app._worker.join(5)
    assert not app._worker.is_alive()
