"""Pilot-driven smoke tests for FactoryApp: the Textual prototype itself,
not just its pure graph helpers. Catches the class of bug a plain `import`
check cannot (e.g. focusing a card previously crashed the app because
CardSelected was not a real Textual Message -- see docs/textual-design.md
"Mandatory terminal design-review loop").
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from apmx.contracts.resolution import resolve_factory
from apmx.tui.app import ContractCard, DetailPane, FactoryApp
from apmx.tui.fixtures import CardStatusChange, branched_chain_script, cancellation_script

pytestmark = pytest.mark.unit

EXAMPLE_FACTORY = Path(__file__).resolve().parents[3] / "examples/contracts/branched-demo"


def _branched_graph(tmp_path):
    caller = tmp_path / "branched-demo"
    shutil.copytree(EXAMPLE_FACTORY, caller)
    return resolve_factory(caller)


def _run(coro):
    return asyncio.run(coro)


def test_app_composes_one_card_per_contract(tmp_path):
    graph = _branched_graph(tmp_path)
    app = FactoryApp(graph)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            assert len(app.query(ContractCard)) == len(graph.catalog)

    _run(scenario())


def test_focusing_a_card_selects_it_without_crashing(tmp_path):
    graph = _branched_graph(tmp_path)
    app = FactoryApp(graph)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            card = app.query(ContractCard).first()
            card.focus()
            await pilot.pause()
            selected = app.query_one(DetailPane).selected
            assert selected is not None
            assert selected.identity == card.node.identity

    _run(scenario())


def test_apply_card_status_updates_card_and_open_detail_pane(tmp_path):
    graph = _branched_graph(tmp_path)
    app = FactoryApp(graph)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            card = app.query(ContractCard).first()
            card.focus()
            await pilot.pause()
            app.apply_card_status(card.node.identity, "passed")
            await pilot.pause()
            assert card.status == "passed"
            body = app.query_one(DetailPane).query_one("#detail-body")
            assert "Status: passed" in str(body.render())

    _run(scenario())


def test_pending_and_running_cards_use_distinct_symbols(tmp_path):
    """Regression for the design-review-loop defect where every card showed
    the same "[>]" glyph regardless of whether it was pending or running."""
    graph = _branched_graph(tmp_path)
    app = FactoryApp(graph)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            pending = app.query(ContractCard).first()
            pending_text = str(pending.render())
            app.apply_card_status(pending.node.identity, "running")
            await pilot.pause()
            running_text = str(pending.render())
            assert pending_text != running_text

    _run(scenario())


def test_fixture_replay_script_drives_app_without_crashing(tmp_path):
    """The labeled replay fixture used for the design-review loop must be
    directly playable against the real app and real graph without errors."""
    graph = _branched_graph(tmp_path)
    app = FactoryApp(graph, fixture_label="test replay")

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            for step in (*branched_chain_script(), *cancellation_script()):
                if isinstance(step.payload, CardStatusChange):
                    app.apply_card_status(step.payload.identity, step.payload.status)
                else:
                    app.append_event(step.payload)
            await pilot.pause()

    _run(scenario())


def test_format_event_reads_real_engine_check_finished_shape():
    """Regression: the real engine (``contracts/engine.py``) emits
    ``check_finished`` as ``observation=CheckObservation(...)``, not the
    flat ``name``/``status`` kwargs the fixtures use. Before this fix,
    ``_format_event`` always read ``None``/``None`` for a genuine live run
    and ``tui_live.py`` misclassified every passing check as "retrying"."""
    from apmx.contracts.models import CheckObservation, ProcessObservation, RunEvent
    from apmx.tui.app import _check_finished_fields, _format_event

    passing = CheckObservation(
        name="plan-shape",
        command="python checks/always_pass.py",
        process=ProcessObservation(returncode=0),
        normalized=0,
        subject_digest="deadbeef",
        resources_digest="deadbeef",
        reason="",
    )
    event = RunEvent(
        run_id="run",
        sequence=1,
        elapsed_seconds=0.0,
        kind="check_finished",
        source="engine",
        data={"observation": passing},
    )
    name, status = _check_finished_fields(event)
    assert (name, status) == ("plan-shape", "pass")
    assert "plan-shape" in _format_event(event)
    assert "None" not in _format_event(event)

    failing = CheckObservation(
        name="build-shape",
        command="python checks/always_pass.py",
        process=ProcessObservation(returncode=1),
        normalized=1,
        subject_digest="deadbeef",
        resources_digest="deadbeef",
        reason="nonzero exit",
    )
    fail_event = RunEvent(
        run_id="run",
        sequence=2,
        elapsed_seconds=0.0,
        kind="check_finished",
        source="engine",
        data={"observation": failing},
    )
    assert _check_finished_fields(fail_event) == ("build-shape", "fail")

    # Fixture replay's flat shape must keep working unchanged.
    fixture_event = RunEvent(
        run_id="run",
        sequence=3,
        elapsed_seconds=0.0,
        kind="check_finished",
        source="engine",
        data={"name": "checkout-tests", "status": "pass"},
    )
    assert _check_finished_fields(fixture_event) == ("checkout-tests", "pass")
