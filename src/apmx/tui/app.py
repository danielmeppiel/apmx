"""Thin Textual prototype: inspect the real resolved contract graph.

Scope (docs/textual-design.md, milestone 1): a selectable card per contract,
grouped into DAG levels from real producer/consumer edges; a detail pane
showing declared needs/produces/checks for the selected contract. No contract
runs here; cards carry only the declared graph, never an invented progress
percentage or execution outcome. Live activity/checks/evidence views and
engine wiring are tracked separately and are not part of this prototype.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.events import Key
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Footer, Header, Log, Static

from ..contracts.models import RunEvent
from ..contracts.resolution import Graph
from ..utils.console import STATUS_SYMBOLS
from .graph import ContractNode, build_nodes, levels

# Card status is presentation-only. It is never computed here from a real
# run; either it stays "pending" (declared-only milestone-1 preview) or it
# is driven by an external caller (chain-level engine wiring, or the
# design-review fixture harness in scripts/termviz) via apply_card_status.
CardStatus = Literal[
    "pending", "running", "checking", "retrying", "passed", "failed", "blocked", "cancelled"
]

_STATUS_SYMBOL: dict[CardStatus, str] = {
    "pending": "[ ]",
    "running": STATUS_SYMBOLS["running"],
    "checking": STATUS_SYMBOLS["running"],
    "retrying": STATUS_SYMBOLS["warning"],
    "passed": STATUS_SYMBOLS["check"],
    "failed": STATUS_SYMBOLS["error"],
    "blocked": STATUS_SYMBOLS["warning"],
    "cancelled": STATUS_SYMBOLS["warning"],
}

_STATUS_LABEL: dict[CardStatus, str] = {
    "pending": "pending",
    "running": "running",
    "checking": "checking",
    "retrying": "retrying",
    "passed": "passed",
    "failed": "failed",
    "blocked": "blocked",
    "cancelled": "cancelled",
}


class CardSelected(Message):
    def __init__(self, identity: str) -> None:
        self.identity = identity
        super().__init__()


class ContractCard(Static, can_focus=True):
    """One selectable contract card. Status defaults to declared-only."""

    DEFAULT_CSS = """
    ContractCard {
        border: round $panel;
        padding: 0 1;
        width: auto;
        min-width: 18;
        height: 3;
        content-align: center middle;
    }
    ContractCard:focus {
        border: round $accent;
        background: $boost;
    }
    ContractCard.-running { border: round $warning; }
    ContractCard.-passed { border: round $success; }
    ContractCard.-failed { border: round $error; }
    """

    status: reactive[CardStatus] = reactive("pending")

    def __init__(self, node: ContractNode) -> None:
        super().__init__(node.identity, markup=False)
        self.node = node

    def on_focus(self) -> None:
        self.post_message(CardSelected(self.node.identity))

    def watch_status(self, status: CardStatus) -> None:
        symbol = _STATUS_SYMBOL[status]
        self.update(f"{symbol} {self.node.identity}")
        self.set_class(status in ("running", "checking", "retrying"), "-running")
        self.set_class(status == "passed", "-passed")
        self.set_class(status in ("failed", "blocked", "cancelled"), "-failed")


class GraphView(VerticalScroll):
    """Left-to-right DAG levels built from real graph edges, not a guess."""

    def __init__(self, graph: Graph) -> None:
        super().__init__()
        self._graph = graph

    def compose(self) -> ComposeResult:
        for level in levels(self._graph):
            row = Horizontal(classes="graph-level")
            with row:
                for node in level:
                    yield ContractCard(node)
            yield row

    def card(self, identity: str) -> ContractCard | None:
        for widget in self.query(ContractCard):
            if widget.node.identity == identity:
                return widget
        return None


def _format_event(event: RunEvent) -> str:
    """Render one RunEvent as a single readable line. Formatting only: the
    event's kind/source/data are never reinterpreted into a new outcome."""
    source = event.source
    kind = event.kind
    if kind == "activity":
        text = event.data.get("text", "")
        label = event.data.get("label")
        prefix = f"{STATUS_SYMBOLS['running']} {source}" + (f"/{label}" if label else "")
        return f"{prefix}: {text}"
    if kind == "check_started":
        return f"{STATUS_SYMBOLS['running']} check {event.data.get('name')} started"
    if kind == "check_finished":
        status = event.data.get("status")
        symbol = STATUS_SYMBOLS["check"] if status == "pass" else STATUS_SYMBOLS["error"]
        return f"{symbol} check {event.data.get('name')} {status}"
    if kind == "diagnostic":
        return f"{STATUS_SYMBOLS['warning']} {event.data.get('message', '')}"
    if kind == "stop_requested":
        return f"{STATUS_SYMBOLS['warning']} stop requested ({event.data.get('reason', 'unknown')})"
    if kind == "stop_observed":
        return (
            f"{STATUS_SYMBOLS['warning']} stop observed (confirmed={event.data.get('confirmed')})"
        )
    if kind == "finished":
        return f"{STATUS_SYMBOLS['check']} finished: {event.data.get('outcome', 'unknown')}"
    return f"{STATUS_SYMBOLS['default']} {kind} ({source})"


class ActivityPane(Vertical):
    """Readable narration/tool/check stream. Auto-follows until the user
    scrolls up; pressing End resumes following, matching plain-mode habits
    of not losing place in output you were reading."""

    def compose(self) -> ComposeResult:
        yield Static("Activity", classes="pane-title")
        yield Log(id="activity-log", max_lines=2000, auto_scroll=True)

    def append(self, line: str) -> None:
        self.query_one("#activity-log", Log).write_line(line)

    def on_key(self, event: Key) -> None:
        log = self.query_one("#activity-log", Log)
        if event.key in ("up", "pageup"):
            log.auto_scroll = False
        elif event.key == "end":
            log.auto_scroll = True
            log.scroll_end()


class DetailPane(Vertical):
    """Declared needs/produces/checks for the selected card; no live status."""

    selected: reactive[ContractNode | None] = reactive(None)

    def compose(self) -> ComposeResult:
        yield Static(id="detail-body", markup=False)

    def watch_selected(self, node: ContractNode | None) -> None:
        body = self.query_one("#detail-body", Static)
        if node is None:
            body.update("Select a contract card to inspect it.")
            return
        status = "pending"
        try:
            card = self.app.query_one(GraphView).card(node.identity)
        except NoMatches:
            card = None
        if card is not None:
            status = _STATUS_LABEL[card.status]
        lines = [
            f"Contract: {node.identity}",
            f"Status: {status}",
            f"Needs: {', '.join(node.needs) if node.needs else 'no declared input files'}",
            f"Produces: {', '.join(node.produces)}",
            f"Checks: {', '.join(node.checks) if node.checks else 'none declared'}",
        ]
        body.update("\n".join(lines))


class DiagnosticsPane(Vertical):
    """Bounded, separate debug stream. Never part of any saved transcript."""

    def compose(self) -> ComposeResult:
        yield Static("Diagnostics (not retained)", classes="pane-title")
        yield Log(id="diagnostics-log", max_lines=200, auto_scroll=True)

    def append(self, line: str) -> None:
        self.query_one("#diagnostics-log", Log).write_line(line)


class FactoryApp(App[None]):
    """Inspect-only prototype for a resolved factory graph.

    ``apply_card_status``/``append_event``/``append_diagnostic`` exist so an
    external driver (future chain-level engine wiring, or the
    scripts/termviz design-review fixture harness) can present status
    without this module ever computing outcomes itself.
    """

    TITLE = "APMX factory (preview)"
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("tab", "focus_next", "Navigate", show=True),
        Binding("shift+tab", "focus_previous", "Navigate back", show=False),
        Binding("d", "toggle_diagnostics", "Diagnostics", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    CSS = """
    .graph-level {
        height: auto;
        margin-bottom: 1;
    }
    .pane-title {
        color: $text-muted;
        padding: 0 1;
    }
    GraphView {
        height: 2fr;
        min-height: 5;
    }
    #detail-pane {
        border-top: solid $panel;
        height: 1fr;
        min-height: 4;
        padding: 0 1;
    }
    #activity-pane {
        border-top: solid $panel;
        height: 1fr;
        min-height: 4;
    }
    #diagnostics-pane {
        border-top: solid $error;
        height: 1fr;
        min-height: 3;
        display: none;
    }
    #diagnostics-pane.-visible {
        display: block;
    }
    #fixture-banner {
        background: $warning;
        color: $text;
        text-align: center;
        display: none;
    }
    #fixture-banner.-visible {
        display: block;
    }
    """

    def __init__(self, graph: Graph, *, fixture_label: str | None = None) -> None:
        super().__init__()
        self._graph = graph
        self._by_identity = {node.identity: node for node in build_nodes(graph)}
        self._fixture_label = fixture_label

    def compose(self) -> ComposeResult:
        yield Header()
        banner = Static(
            f"FIXTURE REPLAY \u2014 {self._fixture_label} \u2014 not a real run",
            id="fixture-banner",
        )
        if self._fixture_label:
            banner.add_class("-visible")
        yield banner
        yield GraphView(self._graph)
        yield DetailPane(id="detail-pane")
        yield ActivityPane(id="activity-pane")
        yield DiagnosticsPane(id="diagnostics-pane")
        yield Footer()

    def on_card_selected(self, message: CardSelected) -> None:
        self.query_one(DetailPane).selected = self._by_identity.get(message.identity)

    def action_toggle_diagnostics(self) -> None:
        self.query_one("#diagnostics-pane").toggle_class("-visible")

    def apply_card_status(self, identity: str, status: CardStatus) -> None:
        card = self.query_one(GraphView).card(identity)
        if card is not None:
            card.status = status
        detail = self.query_one(DetailPane)
        if detail.selected is not None and detail.selected.identity == identity:
            detail.watch_selected(detail.selected)

    def append_event(self, event: RunEvent) -> None:
        self.query_one(ActivityPane).append(_format_event(event))

    def append_diagnostic(self, line: str) -> None:
        self.query_one(DiagnosticsPane).append(line)
