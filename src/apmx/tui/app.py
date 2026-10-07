"""Textual prototype: inspect and follow the real resolved contract graph.

Scope (docs/textual-design.md): a selectable card per contract, grouped into
DAG levels from real producer/consumer edges and explicitly labeled with the
real dependency that connects them; a detail pane showing declared
needs/produces/checks plus those same real dependencies for the selected
contract; separate Activity/Checks/Evidence/Diagnostics views built only from
canonical ``RunEvent`` payloads the real engine already emits. No contract
runs here and no outcome is ever computed here; cards carry only the status
an external caller (``tui_live.py``, or the design-review fixture harness)
hands them through ``apply_card_status``/``append_event``.
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
from textual.widgets import Footer, Header, Input, Log, Static, TabbedContent, TabPane

from ..contracts.models import RunEvent, artifact_files
from ..contracts.resolution import Graph
from ..utils.console import STATUS_SYMBOLS
from .graph import ContractNode, DependencyEdge, build_nodes, dependencies_of, levels

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


def _display_name(identity: str) -> str:
    """A human label for a contract identity: strip the suffix, title-case
    the words. ``node.identity`` (the real file-derived identity) is always
    used for matching/lookups; this is presentation text only."""
    stem = identity.removesuffix(".contract.md")
    words = stem.replace("_", " ").replace("-", " ").split()
    return " ".join(word.capitalize() for word in words) if words else identity


class CardSelected(Message):
    def __init__(self, identity: str) -> None:
        self.identity = identity
        super().__init__()


class ContractCard(Static, can_focus=True):
    """One selectable contract card: human label and explicit status word.
    Real upstream dependencies are never invented here -- they are named on
    the connector line above the card's level and in full in the detail
    pane once selected, so the card itself stays compact enough for a wide,
    short DAG overview rather than a tall column of verbose text."""

    DEFAULT_CSS = """
    ContractCard {
        border: round $panel-lighten-2;
        padding: 0 1;
        width: auto;
        min-width: 22;
        height: auto;
        min-height: 4;
        content-align: left top;
    }
    ContractCard:focus {
        border: heavy $accent;
        background: $boost;
    }
    ContractCard.-running { border: heavy $warning; }
    ContractCard.-passed { border: solid $success; }
    ContractCard.-failed { border: double $error; }
    """

    status: reactive[CardStatus] = reactive("pending")

    def __init__(self, node: ContractNode, dependencies: tuple[DependencyEdge, ...] = ()) -> None:
        super().__init__(markup=False)
        self.node = node
        self._dependencies = dependencies
        self._refresh_text()

    def on_focus(self) -> None:
        self.post_message(CardSelected(self.node.identity))

    def watch_status(self, status: CardStatus) -> None:
        self._refresh_text()
        self.set_class(status in ("running", "checking", "retrying"), "-running")
        self.set_class(status == "passed", "-passed")
        self.set_class(status in ("failed", "blocked", "cancelled"), "-failed")

    def _refresh_text(self) -> None:
        symbol = _STATUS_SYMBOL[self.status]
        label = _STATUS_LABEL[self.status]
        name = _display_name(self.node.identity)
        self.update(f"{symbol} {name}\n{label}")


class GraphView(VerticalScroll):
    """Left-to-right DAG levels built from real graph edges, with an explicit
    connector line naming what each level actually depends on -- never a
    dependency implied only by card placement."""

    def __init__(self, graph: Graph) -> None:
        super().__init__()
        self._graph = graph

    def compose(self) -> ComposeResult:
        all_levels = levels(self._graph)
        dependencies = dependencies_of(self._graph)
        for index, level in enumerate(all_levels):
            row = Horizontal(classes="graph-level")
            with row:
                for node in level:
                    yield ContractCard(node, dependencies.get(node.identity, ()))
            yield row
            if index < len(all_levels) - 1:
                text = _connector_text(all_levels[index + 1], dependencies)
                if text:
                    yield Static(text, classes="graph-connector", markup=False)

    def card(self, identity: str) -> ContractCard | None:
        for widget in self.query(ContractCard):
            if widget.node.identity == identity:
                return widget
        return None


def _connector_text(
    next_level: tuple[ContractNode, ...], dependencies: dict[str, tuple[DependencyEdge, ...]]
) -> str:
    """Explicit real edges feeding the next level, named by the declared
    file each one carries. Renders nothing for roots with no producers."""
    lines = []
    for node in next_level:
        edges = dependencies.get(node.identity, ())
        if not edges:
            continue
        parts = ", ".join(f"{edge.name} \u2190 {_display_name(edge.producer)}" for edge in edges)
        lines.append(f"\u21b3 {_display_name(node.identity)} needs {parts}")
    return "\n".join(lines)


def _check_finished_fields(event: RunEvent) -> tuple[object, str]:
    """Read the check name/outcome from a ``check_finished`` event.

    The real engine (``contracts/engine.py``) emits this event with an
    ``observation`` ``CheckObservation`` (see ``core/contract_logger.py``'s
    own ``_check_finished``), not flat ``name``/``status`` keys. Fixture
    replay (``tui/fixtures.py``) still uses the flat shape, so both are
    read here; the outcome is never recomputed, only unwrapped.
    """
    observation = event.data.get("observation")
    if observation is not None:
        name = getattr(observation, "name", None)
        status = "pass" if getattr(observation, "normalized", None) == 0 else "fail"
        return name, status
    return event.data.get("name"), event.data.get("status")


# Phase names the real engine emits (contracts/engine.py / core/contract_logger.py
# "_phase"), mapped to the same human wording ContractLogger already uses.
_PHASE_LABEL: dict[str, str] = {
    "preflight": "Capturing files",
    "execution": "Running the producer",
    "capture": "Saving output",
    "checks": "Running checks",
    "record": "Saving results",
}


def _format_event(event: RunEvent) -> str:
    """Render one RunEvent as a readable narration line, using the same
    canonical fields ContractLogger already reads -- never a reinterpreted
    outcome, and never a bare ``kind (source)`` fallback for a known shape."""
    source = event.source
    kind = event.kind
    if kind == "selected":
        identity = event.data.get("contract_relative_path") or event.data.get("contract")
        harness = event.data.get("harness", "copilot")
        model = event.data.get("model", "default model")
        return f"{STATUS_SYMBOLS['running']} Selected {identity} \u2014 {harness} / {model}"
    if kind == "phase":
        name = event.data.get("name")
        label = _PHASE_LABEL.get(name, name) if isinstance(name, str) else "unknown phase"
        return f"{STATUS_SYMBOLS['running']} {label}"
    if kind == "activity":
        text = event.data.get("text", "")
        label = event.data.get("label")
        attribution = {"harness": "Producer", "checker": "Check"}.get(source, source)
        if label:
            attribution = f"{attribution}/{label}"
        return f"{STATUS_SYMBOLS['running']} {attribution}: {text}"
    if kind == "metadata":
        return f"{STATUS_SYMBOLS['default']} {event.data.get('text', '')}"
    if kind == "process_started":
        pid = event.data.get("pid", "unknown")
        pgid = event.data.get("pgid", "unknown")
        return f"{STATUS_SYMBOLS['default']} Managed child started (pid={pid}, pgid={pgid})"
    if kind == "skill_loaded":
        return f"{STATUS_SYMBOLS['default']} Loaded skill: {event.data.get('name', '')}"
    if kind == "diagnostic":
        return f"{STATUS_SYMBOLS['warning']} {event.data.get('message', '')}"
    if kind == "input_captured":
        return _format_evidence_event(event)
    if kind == "workspace_captured":
        return _format_evidence_event(event)
    if kind == "check_started":
        return _format_check_event(event)
    if kind == "check_finished":
        return _format_check_event(event)
    if kind == "stop_requested":
        return f"{STATUS_SYMBOLS['warning']} Stop requested ({event.data.get('reason', 'unknown')})"
    if kind == "stop_observed":
        if event.data.get("confirmed") is True:
            return f"{STATUS_SYMBOLS['check']} Managed processes stopped"
        return f"{STATUS_SYMBOLS['warning']} Stop unconfirmed; a child may still be running"
    if kind == "finished":
        result = event.data.get("result")
        outcome = getattr(result, "outcome", None)
        label = getattr(outcome, "name", "unknown")
        return f"{STATUS_SYMBOLS['check']} Finished: {label}"
    if kind == "heartbeat":
        elapsed = event.data.get("elapsed_seconds", event.elapsed_seconds)
        elapsed = elapsed if isinstance(elapsed, (int, float)) else event.elapsed_seconds
        return f"{STATUS_SYMBOLS['default']} Still running; {elapsed:.0f}s elapsed"
    return f"{STATUS_SYMBOLS['default']} {kind} ({source})"


def _format_check_event(event: RunEvent) -> str:
    """Check command/outcome text for the dedicated Checks view."""
    if event.kind == "check_started":
        name = event.data.get("name", "unknown")
        command = event.data.get("command", "")
        suffix = f" \u2014 {command}" if command else ""
        return f"{STATUS_SYMBOLS['running']} {name} started{suffix}"
    name, status = _check_finished_fields(event)
    symbol = STATUS_SYMBOLS["check"] if status == "pass" else STATUS_SYMBOLS["error"]
    label = {"pass": "PASS", "fail": "FAIL"}.get(status, "INCOMPLETE")
    return f"{symbol} {name}: {label}"


def _format_evidence_event(event: RunEvent) -> str:
    """Captured-input text for the dedicated Evidence view."""
    if event.kind == "input_captured":
        entry = event.data.get("entry")
        origin = event.data.get("origin", "unknown origin")
        if entry is not None:
            relative = getattr(entry, "relative_path", "?")
            size = getattr(entry, "size", "?")
            return f"Input: {relative} ({origin}; {size} bytes)"
        return f"Input captured ({origin})"
    if event.kind == "workspace_captured":
        files = event.data.get("files", "?")
        return f"Working copy: {files} captured files"
    return f"{STATUS_SYMBOLS['default']} {event.kind}"


def _format_finished_evidence(event: RunEvent) -> str:
    """The saved-artifact listing for a finished run, read straight from the
    recorded ``RunResult`` -- never a path this module invents."""
    result = event.data.get("result")
    if result is None:
        return "Finished: no recorded result."
    outcome = getattr(getattr(result, "outcome", None), "name", "unknown")
    files = artifact_files(getattr(result, "artifact", None))
    lines = [f"Outcome: {outcome}", f"Artifacts: {len(files)} file(s)"]
    for item in files:
        lines.append(f"  {item.relative_path}")
    directory = getattr(result, "run_directory", None)
    if directory is not None:
        lines.append(f"Record directory: {directory}")
    return "\n".join(lines)


class _FilterableLogPane(Vertical):
    """A titled Log that keeps full history so a search filter can narrow
    the view and later fully restore it, instead of discarding scrollback."""

    PANE_TITLE: ClassVar[str] = "Pane"
    LOG_ID: ClassVar[str] = "log"
    # Explicit empty-state text, never a bare heading. This is never claimed
    # as a pass/fail outcome -- an empty pane means nothing has been
    # observed yet, which stays distinct from an observed-and-passed state.
    EMPTY_TEXT: ClassVar[str] = "No observations yet."

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._history: list[str] = []
        self._query = ""

    def compose(self) -> ComposeResult:
        yield Static(self.PANE_TITLE, classes="pane-title")
        yield Log(id=self.LOG_ID, max_lines=2000, auto_scroll=True)

    def on_mount(self) -> None:
        self.query_one(f"#{self.LOG_ID}", Log).write_line(self.EMPTY_TEXT)

    def append(self, line: str) -> None:
        if not self._history:
            self.query_one(f"#{self.LOG_ID}", Log).clear()
        self._history.append(line)
        if not self._query or self._query in line.lower():
            self.query_one(f"#{self.LOG_ID}", Log).write_line(line)

    def apply_filter(self, query: str) -> None:
        self._query = query.lower()
        log = self.query_one(f"#{self.LOG_ID}", Log)
        log.clear()
        if not self._history:
            log.write_line(self.EMPTY_TEXT)
            return
        for line in self._history:
            if not self._query or self._query in line.lower():
                log.write_line(line)


class ActivityPane(_FilterableLogPane):
    """Readable narration stream: phases, producer/checker text, diagnostics
    and the final outcome line. Auto-follows until the user scrolls up;
    pressing End resumes following, matching plain-mode habits of not
    losing place in output you were reading."""

    PANE_TITLE = "Activity"
    LOG_ID = "activity-log"

    def on_key(self, event: Key) -> None:
        log = self.query_one(f"#{self.LOG_ID}", Log)
        if event.key in ("up", "pageup"):
            log.auto_scroll = False
        elif event.key == "end":
            log.auto_scroll = True
            log.scroll_end()


class ChecksPane(_FilterableLogPane):
    """Check command/outcome stream, separate from general narration."""

    PANE_TITLE = "Checks"
    LOG_ID = "checks-log"
    EMPTY_TEXT = "No checks observed yet."


class EvidencePane(_FilterableLogPane):
    """Captured inputs and saved artifacts, as the engine actually recorded
    them -- never an invented output path."""

    PANE_TITLE = "Evidence"
    LOG_ID = "evidence-log"
    EMPTY_TEXT = "No evidence captured yet -- status remains unknown, not verified."


class DetailPane(Vertical):
    """Declared needs/produces/checks and real dependencies for the selected
    card; status reflects whatever the card currently shows, never a second
    computation of it."""

    selected: reactive[ContractNode | None] = reactive(None)

    def __init__(
        self, *args: object, dependencies: dict[str, tuple[DependencyEdge, ...]], **kwargs: object
    ) -> None:
        super().__init__(*args, **kwargs)
        self._dependencies = dependencies

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
        edges = self._dependencies.get(node.identity, ())
        depends_on = (
            ", ".join(f"{_display_name(e.producer)} (needs {e.name})" for e in edges)
            if edges
            else "nothing (entry point)"
        )
        lines = [
            f"Contract: {node.identity}",
            f"Status: {status}",
            f"Depends on: {depends_on}",
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
        Binding("a", "show_tab('activity-tab')", "Activity", show=True),
        Binding("k", "show_tab('checks-tab')", "Checks", show=True),
        Binding("e", "show_tab('evidence-tab')", "Evidence", show=True),
        Binding("slash", "toggle_search", "Search", show=True),
        Binding("escape", "clear_search", "Clear search", show=False),
        Binding("d", "toggle_diagnostics", "Diagnostics", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    CSS = """
    .graph-level {
        height: auto;
        margin-bottom: 1;
    }
    .graph-connector {
        color: $text-muted;
        padding: 0 1;
        height: auto;
    }
    .pane-title {
        color: $text-muted;
        padding: 0 1;
    }
    Header {
        text-style: bold;
    }
    GraphView {
        width: 2fr;
        min-width: 28;
        height: 1fr;
    }
    #graph-row {
        height: 1fr;
        min-height: 9;
    }
    #detail-pane {
        width: 1fr;
        min-width: 22;
        border-top: solid $panel;
        border-left: solid $panel;
        height: 1fr;
        min-height: 9;
        padding: 0 1;
    }
    #lower-tabs {
        border-top: solid $panel;
        height: 2fr;
        min-height: 10;
    }
    #activity-pane, #checks-pane, #evidence-pane {
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
    #search-input {
        display: none;
        border: round $accent;
    }
    #search-input.-visible {
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
        self._dependencies = dependencies_of(graph)
        self._fixture_label = fixture_label
        self._total_contracts = len(self._by_identity)
        self._passed_count = 0
        self._failed_count = 0
        self._counted: set[str] = set()
        self._phase_name: str | None = None
        self._harness_name: str | None = None
        self._model_name: str | None = None
        self._elapsed: float = 0.0
        self._auto_follow = True
        # Identity of a card `_follow()` is programmatically focusing. `Widget.focus()`
        # defers the actual focus change via `app.call_later`, so the resulting
        # `CardSelected` message always arrives on a *later* message-pump tick --
        # comparing identities here (rather than a boolean reset synchronously
        # around `card.focus()`) is what correctly distinguishes our own
        # auto-follow focus change from a real manual one, regardless of that
        # deferral.
        self._programmatic_focus: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        banner = Static(
            f"FIXTURE REPLAY \u2014 {self._fixture_label} \u2014 not a real run",
            id="fixture-banner",
        )
        if self._fixture_label:
            banner.add_class("-visible")
        yield banner
        with Horizontal(id="graph-row"):
            yield GraphView(self._graph)
            yield DetailPane(id="detail-pane", dependencies=self._dependencies)
        with TabbedContent(initial="activity-tab", id="lower-tabs"):
            with TabPane("Activity", id="activity-tab"):
                yield ActivityPane(id="activity-pane")
            with TabPane("Checks", id="checks-tab"):
                yield ChecksPane(id="checks-pane")
            with TabPane("Evidence", id="evidence-tab"):
                yield EvidencePane(id="evidence-pane")
        # ``can_focus=False`` while hidden: Textual's initial-auto-focus picks
        # the first focusable descendant in DOM order regardless of
        # ``display: none``, so an unconditionally-focusable hidden search
        # box can silently steal focus (and swallow every subsequent key as
        # text) before the user ever opens it -- exactly what toggling it
        # focusable in lockstep with "-visible" here prevents.
        search_input = Input(
            placeholder="Search current view (type to filter, Esc to clear)",
            id="search-input",
        )
        search_input.can_focus = False
        yield search_input
        yield DiagnosticsPane(id="diagnostics-pane")
        yield Footer()

    def on_mount(self) -> None:
        nodes = build_nodes(self._graph)
        if nodes:
            self.query_one(DetailPane).selected = nodes[0]
            # Explicit initial focus rather than relying on Textual's
            # first-focusable-descendant heuristic, whose candidate can
            # change with unrelated layout/DOM-nesting edits -- a real
            # bug this surfaced once already (see commit history).
            card = self.query_one(GraphView).card(nodes[0].identity)
            if card is not None:
                card.focus()
        self._refresh_header()

    def on_card_selected(self, message: CardSelected) -> None:
        self.query_one(DetailPane).selected = self._by_identity.get(message.identity)
        if self._programmatic_focus == message.identity:
            self._programmatic_focus = None
        else:
            self._auto_follow = False

    def action_toggle_diagnostics(self) -> None:
        self.query_one("#diagnostics-pane").toggle_class("-visible")

    def action_show_tab(self, tab_id: str) -> None:
        self.query_one(TabbedContent).active = tab_id

    def action_toggle_search(self) -> None:
        search = self.query_one("#search-input", Input)
        search.toggle_class("-visible")
        visible = search.has_class("-visible")
        search.can_focus = visible
        if visible:
            search.focus()
        else:
            search.value = ""
            self._apply_search("")

    def action_clear_search(self) -> None:
        search = self.query_one("#search-input", Input)
        if search.has_class("-visible"):
            search.remove_class("-visible")
            search.can_focus = False
            search.value = ""
            self._apply_search("")

    def on_input_changed(self, message: Input.Changed) -> None:
        if message.input.id == "search-input":
            self._apply_search(message.value)

    def _apply_search(self, query: str) -> None:
        pane_by_tab = {
            "activity-tab": ActivityPane,
            "checks-tab": ChecksPane,
            "evidence-tab": EvidencePane,
        }
        active = self.query_one(TabbedContent).active
        pane_cls = pane_by_tab.get(active)
        if pane_cls is not None:
            self.query_one(pane_cls).apply_filter(query)

    def _follow(self, identity: str) -> None:
        """Keep the detail pane and keyboard focus on whatever contract is
        currently active, unless the user has already focused a card
        themselves -- a manual selection always wins over auto-follow."""
        if not self._auto_follow:
            return
        card = self.query_one(GraphView).card(identity)
        if card is None or card.has_focus:
            return
        self._programmatic_focus = identity
        card.focus()

    def _refresh_header(self) -> None:
        harness = self._harness_name or "engine"
        phase = _PHASE_LABEL.get(self._phase_name, self._phase_name) if self._phase_name else None
        parts = [
            f"{harness}",
            f"{self._passed_count}/{self._total_contracts} passed",
        ]
        if self._failed_count:
            parts.append(f"{self._failed_count} failed")
        parts.append(f"phase: {phase or 'starting'}")
        parts.append(f"{self._elapsed:.0f}s")
        self.sub_title = " \u00b7 ".join(parts)

    def apply_card_status(self, identity: str, status: CardStatus) -> None:
        card = self.query_one(GraphView).card(identity)
        if card is not None:
            card.status = status
        detail = self.query_one(DetailPane)
        if detail.selected is not None and detail.selected.identity == identity:
            detail.watch_selected(detail.selected)
        if status in ("running", "checking", "retrying"):
            self._follow(identity)
        if status in ("passed", "failed", "blocked", "cancelled") and identity not in self._counted:
            self._counted.add(identity)
            if status == "passed":
                self._passed_count += 1
            else:
                self._failed_count += 1
        self._refresh_header()

    def append_event(self, event: RunEvent) -> None:
        if event.elapsed_seconds:
            self._elapsed = max(self._elapsed, event.elapsed_seconds)
        if event.kind == "selected":
            harness = event.data.get("harness")
            model = event.data.get("model")
            if isinstance(harness, str):
                self._harness_name = harness
            if isinstance(model, str):
                self._model_name = model
        elif event.kind == "phase":
            name = event.data.get("name")
            if isinstance(name, str):
                self._phase_name = name
        if event.kind in ("check_started", "check_finished"):
            self.query_one(ChecksPane).append(_format_check_event(event))
        elif event.kind in ("input_captured", "workspace_captured"):
            self.query_one(EvidencePane).append(_format_evidence_event(event))
        elif event.kind == "finished":
            self.query_one(ActivityPane).append(_format_event(event))
            self.query_one(EvidencePane).append(_format_finished_evidence(event))
        else:
            self.query_one(ActivityPane).append(_format_event(event))
        self._refresh_header()

    def append_diagnostic(self, line: str) -> None:
        self.query_one(DiagnosticsPane).append(line)
