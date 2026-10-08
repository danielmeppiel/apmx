"""Read-only, bounded inspection actions for explicitly selected retained files."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static, TextArea

from ..contracts.stream import safe_text
from ..utils.file_capture import open_readonly_nofollow
from ..utils.path_security import has_symlink_component

PREVIEW_BYTES = 24 * 1024
PREVIEW_LINES = 300


def display_text(text: str, *, limit: int = PREVIEW_BYTES) -> str:
    """Share diagnostic redaction/escaping; only layout newlines survive."""
    return "\n".join(safe_text(line, limit=4096) for line in text[:limit].splitlines())


def read_preview(path: Path, *, root: Path | None = None) -> str:
    """No symlink/special-file following or interpretation of terminal escape bytes."""
    if has_symlink_component(root or path.parent, path):
        raise OSError("Preview refused: the retained path is now a symbolic link.")
    descriptor = open_readonly_nofollow(path)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise OSError("Preview refused: not a regular retained file.")
        raw = stream.read(PREVIEW_BYTES + 1)
    if b"\0" in raw:
        return "Binary file; no text preview. Use the retained path to inspect it externally."
    lines = raw[:PREVIEW_BYTES].decode("utf-8", errors="replace").splitlines()
    text = display_text("\n".join(lines[:PREVIEW_LINES]))
    if len(raw) > PREVIEW_BYTES or len(lines) > PREVIEW_LINES:
        text += "\n[Preview truncated; retained file is unchanged.]"
    return text or "(empty file)"


class InspectionScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    InspectionScreen { align: center middle; }
    #inspection { width: 95%; height: 90%; border: solid $accent; background: $surface; }
    #inspection-title { height: auto; max-height: 4; padding: 0 1; }
    #inspection-text { height: 1fr; border: none; }
    #inspection-help { height: 1; color: $text-muted; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "Back", priority=True),
    ]

    def __init__(self, title: str, text: str) -> None:
        super().__init__()
        self.heading = display_text(title)
        self.text = display_text(text)

    def compose(self) -> ComposeResult:
        with Vertical(id="inspection"):
            yield Static(self.heading, id="inspection-title", markup=False)
            yield TextArea(self.text, read_only=True, id="inspection-text", show_line_numbers=True)
            yield Static(
                "Esc back | Read-only; produced != passed != applied", id="inspection-help"
            )

    def action_close(self) -> None:
        self.dismiss(None)
