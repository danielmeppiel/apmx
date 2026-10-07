"""Milestone 2 feasibility proof: genuine default-No consent screen and a
real supervised child process that cancellation actually terminates and
reaps. See apmx.tui.consent module docstring for scope boundaries (no
engine/consent-semantics ownership here; mechanism-only proof).
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button

from apmx.tui.consent import CancellableChild, ConsentScreen

pytestmark = pytest.mark.unit


class _Harness(App[None]):
    def __init__(self, prompt: str = "Allow this run to use host access?") -> None:
        super().__init__()
        self.result: bool | None = None
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        return iter(())

    async def ask(self) -> None:
        self.result = await self.push_screen_wait(ConsentScreen(self._prompt))

    def ask_in_worker(self) -> None:
        self.run_worker(self.ask, exclusive=True)


def _run(coro):
    return asyncio.run(coro)


def test_consent_screen_default_focus_is_decline():
    app = _Harness()

    async def scenario():
        async with app.run_test() as pilot:
            app.push_screen(ConsentScreen("Allow this run to use host access?"))
            await pilot.pause()
            focused = app.screen.focused
            assert isinstance(focused, Button)
            assert focused.id == "decline"

    _run(scenario())


def test_consent_screen_escape_declines_without_pressing_any_button():
    app = _Harness()

    async def scenario():
        async with app.run_test() as pilot:
            app.ask_in_worker()
            await pilot.pause()
            await pilot.press("escape")
            await app.workers.wait_for_complete()
            assert app.result is False

    _run(scenario())


def test_consent_screen_requires_explicit_navigate_and_confirm_to_accept():
    app = _Harness()

    async def scenario():
        async with app.run_test() as pilot:
            app.ask_in_worker()
            await pilot.pause()
            # Default focus is decline; must explicitly tab to accept and
            # press it -- a bare Enter at mount must never accept.
            await pilot.press("tab")
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            assert app.result is True

    _run(scenario())


def test_consent_screen_bare_enter_at_mount_declines_not_accepts():
    app = _Harness()

    async def scenario():
        async with app.run_test() as pilot:
            app.ask_in_worker()
            await pilot.pause()
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            assert app.result is False

    _run(scenario())


def test_cancellable_child_spawns_a_real_process():
    async def scenario():
        child = await CancellableChild.spawn([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            assert child.is_running()
            # Confirm it is a genuine OS process, not a stub.
            os.kill(child.pid, 0)
        finally:
            await child.cancel()

    _run(scenario())


def test_cancel_terminates_and_reaps_a_real_child_process():
    async def scenario():
        child = await CancellableChild.spawn([sys.executable, "-c", "import time; time.sleep(30)"])
        pid = child.pid
        result = await child.cancel()
        assert result == "cancelled"
        assert not child.is_running()
        # The OS process must actually be gone (reaped), not just marked
        # as such in our own bookkeeping.
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    _run(scenario())


def test_cancel_escalates_to_sigkill_if_child_ignores_sigterm():
    async def scenario():
        # A child that traps SIGTERM and refuses to exit must still be
        # reaped via the SIGKILL escalation path, not left running.
        child = await CancellableChild.spawn(
            [
                sys.executable,
                "-c",
                "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
            ]
        )
        pid = child.pid
        result = await child.cancel(escalate_after=0.3)
        assert result == "cancelled"
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    _run(scenario())


def test_cancel_on_already_finished_child_is_a_safe_no_op():
    async def scenario():
        child = await CancellableChild.spawn([sys.executable, "-c", "pass"])
        await child.process.wait()
        result = await child.cancel()
        assert result == "already_finished"

    _run(scenario())
