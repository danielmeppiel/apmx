"""Milestone 2 feasibility prototype: genuine stdin consent + real child-
process cancellation, proven as an isolated mechanism inside Textual.

Scope: this module does not call into ``apmx.contracts.engine``/``chain`` and
does not grant or compute any execution outcome itself — consent and outcome
semantics stay owned by the canonical engine and ``ContractLogger`` (see
docs/textual-design.md). What this proves, genuinely (not as an estimate):

1. A real interactive consent prompt that defaults to declining and only
   proceeds on an explicit keypress selecting "Yes" — never auto-accepted,
   matching the existing CLI's default-No ``--allow-host-access`` gate.
2. A real supervised child process (its own process group via
   ``start_new_session=True``) that a cancel action can genuinely terminate
   and reap — SIGTERM first, escalating to SIGKILL only if the child does
   not exit, then awaiting it so no zombie/leaked process remains and no
   partial output is presented as a completed/authenticated outcome.
"""

from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConsentScreen(ModalScreen[bool]):
    """Default-No consent prompt. Declining (default focus, Escape, or 'n')
    dismisses with ``False`` and never arms anything. Only an explicit "Yes"
    selection dismisses with ``True``."""

    DEFAULT_CSS = """
    ConsentScreen {
        align: center middle;
    }
    #consent-box {
        width: 60;
        height: auto;
        border: round $warning;
        padding: 1 2;
        background: $surface;
    }
    #consent-buttons {
        height: auto;
        padding-top: 1;
        align: center middle;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "decline", "Decline", show=False),
        Binding("n", "decline", "No", show=False),
    ]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="consent-box"):
            yield Static(self._prompt, markup=False)
            with Vertical(id="consent-buttons"):
                yield Button("No (default)", id="decline", variant="error")
                yield Button("Yes, allow this run", id="accept", variant="warning")

    def on_mount(self) -> None:
        # Default focus sits on decline; accept requires an explicit
        # navigate-and-confirm, never a bare Enter-on-mount accept.
        self.query_one("#decline", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "accept")

    def action_decline(self) -> None:
        self.dismiss(False)


@dataclass
class CancellableChild:
    """Wraps one real supervised child process for genuine cancel+reap
    proof. Not the canonical engine's ``process.supervise_process`` — a
    standalone mechanism check for milestone 2 feasibility, swapped for the
    real supervisor when live engine wiring (milestone 3) lands."""

    process: asyncio.subprocess.Process
    _cancelled: bool = False

    @classmethod
    async def spawn(cls, argv: list[str]) -> CancellableChild:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        return cls(process=proc)

    @property
    def pid(self) -> int:
        return self.process.pid

    def is_running(self) -> bool:
        return self.process.returncode is None

    async def cancel(self, *, escalate_after: float = 1.0) -> str:
        """Terminate and reap the real child. Returns 'cancelled' once the
        process is confirmed gone; never leaves it running or zombied, and
        never reports a result as if the run had completed normally."""
        self._cancelled = True
        if self.process.returncode is not None:
            return "already_finished"
        pgid = os.getpgid(self.process.pid)
        os.killpg(pgid, signal.SIGTERM)
        try:
            await asyncio.wait_for(self.process.wait(), timeout=escalate_after)
        except TimeoutError:
            os.killpg(pgid, signal.SIGKILL)
            await self.process.wait()
        return "cancelled"
