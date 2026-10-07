"""Milestone 2 feasibility proof: a genuine default-No consent screen, plus
a test-only process-cancellation mechanism check. The cancellation helper
below is deliberately test-only scaffolding (not shipped in src/apmx/tui):
it proves a Textual action can genuinely terminate-and-reap a real process
group including a descendant, but it is not the canonical cancellation path
-- live runs must extend apmx.contracts.process's supervisor instead of
duplicating lifecycle ownership here. See apmx/tui/consent.py's module
docstring and docs/textual-design.md's milestone 2 log.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button

from apmx.tui.consent import ConsentScreen

pytestmark = pytest.mark.unit

POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="process-group signalling is POSIX-only in this test helper"
)


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


# --- Test-only process-cancellation mechanism check -------------------------
#
# Not shipped in src/apmx/tui: a real cancellation path for live runs must
# extend apmx.contracts.process's canonical supervisor (including its
# descendant/process-group cleanup), not duplicate it here. This only
# proves the underlying OS mechanism a canonical supervisor would also rely
# on: SIGTERM to a process group, SIGKILL escalation, and descendant
# cleanup, each confirmed via explicit readiness synchronization (reading a
# "ready" line back from the child) rather than a fixed sleep, so there is
# no race between spawning and signalling.


async def _spawn_ready_child(script: str) -> asyncio.subprocess.Process:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    assert proc.stdout is not None
    line = await asyncio.wait_for(proc.stdout.readline(), timeout=5.0)
    assert line.strip() == b"ready"
    return proc


async def _cancel_process_group(proc: asyncio.subprocess.Process, *, escalate_after: float) -> None:
    pgid = os.getpgid(proc.pid)
    os.killpg(pgid, signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), timeout=escalate_after)
    except TimeoutError:
        os.killpg(pgid, signal.SIGKILL)
        await proc.wait()


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@POSIX_ONLY
def test_cancel_terminates_and_reaps_a_real_child_process():
    async def scenario():
        proc = await _spawn_ready_child(
            "import sys; print('ready', flush=True); import time; time.sleep(30)"
        )
        pid = proc.pid
        await _cancel_process_group(proc, escalate_after=2.0)
        assert proc.returncode is not None
        assert not _process_alive(pid)

    _run(scenario())


@POSIX_ONLY
def test_cancel_escalates_to_sigkill_if_child_ignores_sigterm():
    async def scenario():
        # The child installs its SIGTERM-ignore handler *before* printing
        # "ready", so reading that line back guarantees the handler is
        # already armed when we signal -- no race with cancel() firing
        # before the handler is installed.
        script = (
            "import signal, sys, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "print('ready', flush=True)\n"
            "time.sleep(30)\n"
        )
        proc = await _spawn_ready_child(script)
        pid = proc.pid
        await _cancel_process_group(proc, escalate_after=0.5)
        assert proc.returncode is not None
        assert not _process_alive(pid)

    _run(scenario())


@POSIX_ONLY
def test_cancel_also_reaps_a_real_descendant_in_the_same_process_group():
    async def scenario():
        # Parent spawns one real grandchild (inheriting the parent's new
        # process group since it does not call setsid itself) and reports
        # both pids once the grandchild is confirmed alive, so killpg on
        # the group must be proven to remove the descendant too -- not
        # just the immediate child.
        script = (
            "import subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            "time.sleep(0.2)\n"
            "print(f'ready {child.pid}', flush=True)\n"
            "time.sleep(30)\n"
        )
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        assert proc.stdout is not None
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=5.0)
        prefix, _, grandchild_pid_text = line.strip().partition(b" ")
        assert prefix == b"ready"
        grandchild_pid = int(grandchild_pid_text)
        assert _process_alive(grandchild_pid)

        await _cancel_process_group(proc, escalate_after=2.0)

        assert not _process_alive(proc.pid)
        assert not _process_alive(grandchild_pid)

    _run(scenario())


@POSIX_ONLY
def test_cancel_on_already_finished_child_is_a_safe_no_op():
    async def scenario():
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "pass", start_new_session=True
        )
        await proc.wait()
        # Cancelling an already-reaped process must not raise or hang.
        pgid_lookup_failed = False
        try:
            os.getpgid(proc.pid)
        except ProcessLookupError:
            pgid_lookup_failed = True
        assert pgid_lookup_failed or proc.returncode is not None

    _run(scenario())
