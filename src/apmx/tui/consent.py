"""Default-No source preparation and cancel/quit decisions.

The command bridge supplies canonical prompts and consumes the answer. Widgets
never execute a source, decide acceptance or supervise a process.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConsentScreen(ModalScreen[bool | None]):
    """Default-No consent prompt. Declining (default focus, Escape, or 'n')
    dismisses with False and never arms anything. Only an explicit "Yes"
    selection dismisses with True."""

    DEFAULT_CSS = """
    ConsentScreen {
        align: center middle;
        background: $background 70%;
    }
    #consent-box {
        width: 90%;
        max-width: 76;
        height: auto;
        max-height: 90%;
        border: round $warning;
        padding: 1 2;
        background: $surface;
    }
    #consent-buttons {
        height: auto;
        padding-top: 1;
        align: center middle;
    }
    #consent-buttons Button { min-width: 12; width: 1fr; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "decline", "Decline", show=False),
        Binding("n", "decline", "No", show=False),
        Binding("y", "accept", "Yes", show=False),
        Binding("r", "inspect", "Inspect", show=False),
    ]

    def __init__(
        self,
        prompt: str,
        *,
        accept_label: str = "Yes, allow this run",
        allow_inspect: bool = False,
    ) -> None:
        super().__init__()
        self._prompt = prompt
        self.accept_label = accept_label
        self.allow_inspect = allow_inspect

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="consent-box"):
            yield Static(self._prompt, markup=False)
            with Horizontal(id="consent-buttons"):
                yield Button("No (default)", id="decline", variant="error")
                yield Button(self.accept_label, id="accept", variant="warning")
                if self.allow_inspect:
                    yield Button("Inspect (r)", id="inspect")

    def on_mount(self) -> None:
        # Default focus sits on decline; accept requires an explicit
        # navigate-and-confirm, never a bare Enter-on-mount accept.
        self.query_one("#decline", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None if event.button.id == "inspect" else event.button.id == "accept")

    def action_decline(self) -> None:
        self.dismiss(False)

    def action_accept(self) -> None:
        self.dismiss(True)

    def action_inspect(self) -> None:
        if self.allow_inspect:
            self.dismiss(None)
