"""CLI entry point for the preview-only Textual prototype."""

from __future__ import annotations

import sys

from ..contracts.resolution import Graph
from ..utils.console import TerminalMode, terminal_capabilities


def tui_eligible() -> bool:
    """Require a genuine interactive terminal on both ends, like consent prompts do."""
    return (
        terminal_capabilities().mode is not TerminalMode.STREAM
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    )


def launch_preview(graph: Graph) -> None:
    """Run the inspect-only prototype app for one resolved, unexecuted graph."""
    from .app import FactoryApp

    FactoryApp(graph).run()
