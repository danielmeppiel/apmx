"""Milestone 2 feasibility prototype: a genuine stdin consent widget.

Scope: this module does not call into apmx.contracts.engine/chain/process
and does not grant or compute any execution outcome itself; consent and
outcome semantics stay owned by the canonical engine and ContractLogger
(see docs/textual-design.md). What this proves, genuinely (not as an
estimate): a real interactive consent prompt that defaults to declining and
only proceeds on an explicit keypress selecting "Yes" -- never
auto-accepted, matching the existing CLI's default-No --allow-host-access
gate. Wiring this screen to the real admission flow
(logger.confirm_factory()) is tracked separately as live-execution wiring,
not done by this module.

A real supervised-child cancellation mechanism was prototyped alongside
this screen and deliberately removed from shipping source after review: it
was POSIX-only with no Windows path, and duplicated lifecycle ownership
that belongs to apmx.contracts.process's canonical supervisor (including
descendant/process-group cleanup). Real cancellation for live runs must
extend that canonical supervisor, not add a second one here. See
docs/textual-design.md's milestone 2 log for the full note.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConsentScreen(ModalScreen[bool]):
    """Default-No consent prompt. Declining (default focus, Escape, or 'n')
    dismisses with False and never arms anything. Only an explicit "Yes"
    selection dismisses with True."""

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
