"""Persistent read-only factory workspace, driven by canonical observations."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar, Literal

from rich.text import Text
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Key
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Input,
    Log,
    Static,
    TabbedContent,
    TabPane,
    Tree,
)

from ..contracts.models import ChainResult, CheckObservation, RunEvent, artifact_files
from ..contracts.resolution import Graph
from .graph import ContractNode, DependencyEdge, build_nodes, dependencies_of, levels
from .inspection import InspectionScreen, display_text, read_preview
from .state import EVENT_LIMIT, AttemptView, Observations, OutputItem

CardStatus = Literal[
    "pending",
    "running",
    "checking",
    "retrying",
    "passed",
    "failed",
    "blocked",
    "cancelled",
    "unassessed",
]
ACTIVE = ("running", "checking", "retrying")
_STATUS_SYMBOL = {
    "pending": "[ ]",
    "running": "[>]",
    "checking": "[>]",
    "retrying": "[~]",
    "passed": "[+]",
    "failed": "[x]",
    "blocked": "[!]",
    "cancelled": "[-]",
    "unassessed": "[?]",
}
_PHASE_LABEL = {
    "preflight": "Capturing inputs",
    "execution": "Producer",
    "capture": "Saving output",
    "checks": "Independent checks",
    "record": "Saving records",
}


def _display_name(identity: str) -> str:
    return identity.removesuffix(".contract.md").replace("_", " ").replace("-", " ").title()


def _check_finished_fields(event: RunEvent) -> tuple[object, str]:
    observation = event.data.get("observation")
    if isinstance(observation, CheckObservation):
        status = {0: "pass", 1: "fail"}.get(observation.normalized, "incomplete")
        return observation.name, status
    return event.data.get("name"), str(event.data.get("status", "incomplete"))


def _format_check_event(event: RunEvent) -> str:
    if event.kind == "check_started":
        return f"[>] {event.data.get('name')}: started - {event.data.get('command', '')}"
    name, status = _check_finished_fields(event)
    return f"{'[+]' if status == 'pass' else '[!]'} {name}: {status.upper()}"


def _format_event(event: RunEvent) -> str:
    data = event.data
    if event.kind == "phase":
        name = str(data.get("name", "unknown"))
        return _PHASE_LABEL.get(name, name)
    if event.kind == "selected":
        return f"Selected {data.get('contract_relative_path') or data.get('contract')} / {data.get('harness')} / {data.get('model') or 'default model'}"
    if event.kind == "activity":
        return f"{event.source}/{data.get('label') or 'output'}: {data.get('text', '')}"
    if event.kind in ("diagnostic", "native_diagnostic"):
        return str(data.get("message", data.get("text", "")))
    if event.kind == "metadata":
        return str(data.get("text", ""))
    if event.kind == "heartbeat":
        return f"Still running; {event.elapsed_seconds:.0f}s elapsed (not model progress)"
    if event.kind.startswith("check_"):
        return _format_check_event(event)
    if event.kind in ("finished", "attempt_finished"):
        result = data.get("result")
        return f"Recorded attempt: {getattr(getattr(result, 'outcome', None), 'name', 'unknown')}"
    if event.kind == "process_started":
        return f"Managed process started: pid={data.get('pid')} pgid={data.get('pgid')}"
    if event.kind == "stop_observed":
        return "Managed processes stopped" if data.get("confirmed") else "Process stop UNCONFIRMED"
    if event.kind == "stop_requested":
        return f"Stop requested: {data.get('reason')}"
    if event.kind == "skill_loaded":
        return f"Native-reported skill invocation: {data.get('name')} (unverified)"
    if event.kind == "input_captured":
        entry = data.get("entry")
        return f"Captured input: {getattr(entry, 'relative_path', '?')} / {data.get('origin')}"
    if event.kind == "workspace_captured":
        return f"Captured working copy: {data.get('files')} files"
    return f"{event.kind} ({event.source})"


class CardSelected(Message):
    def __init__(self, identity: str) -> None:
        self.identity = identity
        super().__init__()


class ContractCard(Static, can_focus=True):
    DEFAULT_CSS = """
    ContractCard { border: solid $panel-lighten-2; padding: 0 1; width: 1fr;
        min-width: 21; height: 5; }
    ContractCard:focus { border: heavy $accent; }
    ContractCard.-selected { background: $boost; }
    ContractCard.-running { border: heavy $warning; }
    ContractCard.-passed { border: solid $success; }
    ContractCard.-failed { border: double $error; }
    """
    status: reactive[CardStatus] = reactive("pending")

    def __init__(self, node: ContractNode, dependencies: tuple[DependencyEdge, ...] = ()) -> None:
        super().__init__(markup=False)
        self.node = node
        self.dependencies = dependencies
        self.attempt: AttemptView | None = None
        self.started: float | None = None
        self.frame = 0
        self.motion = True
        self._refresh_text()

    def on_focus(self) -> None:
        self.post_message(CardSelected(self.node.identity))

    def on_click(self) -> None:
        self.post_message(CardSelected(self.node.identity))

    def watch_status(self, status: CardStatus) -> None:
        if status in ACTIVE and self.started is None:
            self.started = time.monotonic()
        elif status not in ACTIVE:
            self.started = None
        self.set_class(status in ACTIVE, "-running")
        self.set_class(status == "passed", "-passed")
        self.set_class(status in ("failed", "blocked", "cancelled"), "-failed")
        self._refresh_text()

    def tick(self) -> None:
        if self.status in ACTIVE:
            self.frame += 1
            self._refresh_text()

    def _refresh_text(self) -> None:
        symbol = _STATUS_SYMBOL[self.status]
        if self.status in ACTIVE and self.motion:
            symbol = f"[{'|/-\\'[self.frame % 4]}]"
        attempt = self.attempt
        elapsed = max(
            attempt.elapsed if attempt else 0,
            time.monotonic() - self.started if self.started is not None else 0,
        )
        state = self.status
        if self.status in ACTIVE:
            phase = _PHASE_LABEL.get(attempt.phase, attempt.phase) if attempt else "Starting"
            state = f"{phase} {elapsed:.0f}s"
        checks = sum(value is not None for value in attempt.checks.values()) if attempt else 0
        passed = (
            sum(value is not None and value.normalized == 0 for value in attempt.checks.values())
            if attempt
            else 0
        )
        produced = len(artifact_files(attempt.result.artifact)) if attempt and attempt.result else 0
        index = f"{attempt.index}/{attempt.limit}" if attempt else f"-/{self.node.attempt_limit}"
        selected = "* " if self.has_class("-selected") else ""
        if self.is_mounted and self.app.size.width < 100:
            self.update(
                display_text(
                    f"{selected}{symbol} {_display_name(self.node.identity)} | {state}\n"
                    f"Checks {passed}/{checks}/{len(self.node.checks)} | Try {index} | Out {produced}/{len(self.node.produces)}"
                )
            )
            return
        self.update(
            display_text(
                f"{selected}{symbol} {_display_name(self.node.identity)} | {state}\n"
                f"Checks {passed} pass / {checks} seen / {len(self.node.checks)} declared\n"
                f"Attempt {index} | Outputs {produced}/{len(self.node.produces)} captured"
            )
        )


class GraphView(VerticalScroll):
    def __init__(self, graph: Graph | None) -> None:
        super().__init__(id="graph")
        self.graph = graph

    def compose(self) -> ComposeResult:
        if self.graph is None:
            yield Static(
                "Source not loaded.\nNo resolved graph or captured inventory yet.",
                id="unresolved",
                markup=False,
            )
            return
        dependencies = dependencies_of(self.graph)
        for depth, level in enumerate(levels(self.graph)):
            if depth:
                for node in level:
                    edges = dependencies.get(node.identity, ())
                    for edge in edges:
                        yield Static(
                            display_text(
                                f"To {_display_name(edge.consumer)}: {edge.name} <- {_display_name(edge.producer)}"
                            ),
                            classes="graph-connector",
                            markup=False,
                        )
            with Horizontal(classes="graph-level"):
                for node in level:
                    yield ContractCard(node, dependencies.get(node.identity, ()))

    def card(self, identity: str) -> ContractCard | None:
        return next(
            (card for card in self.query(ContractCard) if card.node.identity == identity), None
        )


class DetailPane(VerticalScroll):
    selected: reactive[ContractNode | None] = reactive(None)

    def __init__(self, *, dependencies: dict[str, tuple[DependencyEdge, ...]], **kwargs) -> None:
        super().__init__(**kwargs)
        self.dependencies = dependencies

    def compose(self) -> ComposeResult:
        yield Static(id="detail-body", markup=False)

    def watch_selected(self, node: ContractNode | None) -> None:
        self.query_one("#detail-body", Static).update(self.text())

    def text(self) -> str:
        node = self.selected
        if node is None:
            return "Select a contract to inspect its declared requirements."
        card = self.app.query_one(GraphView).card(node.identity)
        upstream = self.dependencies.get(node.identity, ())
        lines = [
            f"Contract: {node.identity}",
            f"Status: {card.status if card else 'pending'}",
            "",
            "DECLARED",
            "Needs: " + (", ".join(node.needs) or "none"),
            "Produces: " + ", ".join(node.produces),
            "Checks: " + (", ".join(node.checks) or "none"),
            "",
            "ARTIFACT DEPENDENCIES",
        ]
        lines.extend(f"{e.producer} --{e.name}--> this" for e in upstream)
        if not upstream:
            lines.append("Entry point (no producer dependencies)")
        graph = self.app.query_one(GraphView).graph
        if graph:
            for edges in dependencies_of(graph).values():
                lines.extend(
                    f"this --{e.name}--> {e.consumer}" for e in edges if e.producer == node.identity
                )
            contract = next(item for item in graph.order if item.path == node.path)
            lines += ["", "PACKAGE / SKILL IMPORTS (not graph edges)"]
            lines.extend(str(item) for item in contract.imports)
            if not contract.imports:
                lines.append("None declared")
        if card and card.attempt:
            attempt = card.attempt
            lines += [
                "",
                f"OBSERVED attempt {attempt.index}/{attempt.limit}",
                f"Captured inputs: {len(attempt.inputs)}",
                f"Check observations: {sum(v is not None for v in attempt.checks.values())}",
            ]
        return display_text("\n".join(lines))


class _FilterableLogPane(Vertical):
    PANE_TITLE: ClassVar[str] = "Activity"
    LOG_ID: ClassVar[str] = "activity-log"
    EMPTY_TEXT: ClassVar[str] = "No observations yet."
    TAIL: ClassVar[bool] = True

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._history: deque[str] = deque(maxlen=EVENT_LIMIT)
        self._query = ""

    def compose(self) -> ComposeResult:
        yield Static(self.PANE_TITLE, classes="pane-title", markup=False)
        yield Log(id=self.LOG_ID, max_lines=EVENT_LIMIT, auto_scroll=self.TAIL)

    def on_mount(self) -> None:
        self.replace_lines(())

    def append(self, line: str) -> None:
        self._history.append(display_text(line))
        if not self._query or self._query in line.lower():
            self.query_one(Log).write_line(display_text(line))

    def replace_lines(self, lines: Iterable[str]) -> None:
        self._history.clear()
        self._history.extend(display_text(line) for line in lines)
        self.apply_filter(self._query)

    def apply_filter(self, query: str) -> None:
        self._query = query.lower()
        log = self.query_one(Log)
        old_scroll = log.scroll_y
        log.clear()
        visible = [line for line in self._history if not self._query or self._query in line.lower()]
        log.write_lines(
            visible or [self.EMPTY_TEXT if not self._query else "No matching observations."]
        )
        if not log.auto_scroll:
            log.scroll_to(y=old_scroll, animate=False)
        else:
            log.call_after_refresh(log.scroll_end, animate=False)

    def on_key(self, event: Key) -> None:
        log = self.query_one(Log)
        if event.key in ("up", "pageup", "home"):
            log.auto_scroll = False
        elif event.key == "end":
            log.auto_scroll = True
            log.scroll_end()


class ActivityPane(_FilterableLogPane):
    pass


class ChecksPane(_FilterableLogPane):
    PANE_TITLE = "Observed checks by attempt | i inspect checks"
    LOG_ID = "checks-log"
    EMPTY_TEXT = "No checks observed. Declared checks are not a pass."
    TAIL = False


class EvidencePane(Vertical):
    """Expandable read model; hashes and long paths appear on explicit inspection."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.groups = ()
        self._query = ""
        self._rendered = None

    def compose(self) -> ComposeResult:
        yield Static("Evidence | arrows expand / Enter inspect | unsigned", classes="pane-title")
        yield Tree("No captured evidence", id="evidence-tree")

    def replace_groups(self, groups) -> None:
        self.groups = tuple((title, tuple(items)) for title, items in groups)
        self.apply_filter(self._query)

    def apply_filter(self, query: str) -> None:
        self._query = query.lower()
        current = self.groups, self._query
        if current == self._rendered:
            return
        self._rendered = current
        tree = self.query_one(Tree)
        expanded = {str(node.label) for node in tree.root.children if node.is_expanded}
        selected = str(tree.cursor_node.label) if tree.cursor_node else None
        tree.clear()
        tree.root.set_label("Recorded evidence (inventory != consumption)")
        tree.root.expand()
        for title, items in self.groups:
            matching = [
                (label, detail)
                for label, detail in items
                if not self._query or self._query in (title + label + detail).lower()
            ]
            if not matching:
                continue
            group = tree.root.add(
                Text(display_text(title)), expand=title in expanded or bool(self._query)
            )
            for label, detail in matching:
                node = group.add_leaf(Text(display_text(label)), data=(label, detail))
                if str(node.label) == selected:
                    tree.call_after_refresh(tree.move_cursor, node)
            if str(group.label) == selected:
                tree.call_after_refresh(tree.move_cursor, group)

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if event.node.data:
            title, text = event.node.data
            self.app.push_screen(InspectionScreen(title, text))


class DiagnosticsPane(_FilterableLogPane):
    PANE_TITLE = "Diagnostics (not retained)"
    LOG_ID = "diagnostics-log"


class OutputsPane(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("Retained deliverables | produced != passed != applied", classes="pane-title")
        yield DataTable(id="output-table", cursor_type="row", zebra_stripes=True)
        yield Static("No output captured yet.", id="output-path", markup=False)


class FactoryApp(App[None]):
    TITLE = "APMX"
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("a", "show_tab('activity-tab')", "Activity"),
        Binding("k", "show_tab('checks-tab')", "Checks"),
        Binding("o", "show_tab('outputs-tab')", "Outputs"),
        Binding("e", "show_tab('evidence-tab')", "Evidence"),
        Binding("s", "toggle_scope", "Scope", show=False),
        Binding("f", "toggle_follow", "Follow", show=False),
        Binding("g", "toggle_graph", "Graph"),
        Binding("i", "inspect", "Inspect", show=False),
        Binding("p", "preview_output", "Preview"),
        Binding("y", "copy_path", "Copy path", show=False),
        Binding("l", "open_location", "Location", show=False),
        Binding("b", "evidence_location", "Evidence location", show=False),
        Binding("slash", "toggle_search", "Search"),
        Binding("escape", "clear_search", "Back", show=False),
        Binding("d", "toggle_diagnostics", "Diagnostics", show=False),
        Binding("m", "toggle_motion", "Motion", show=False),
        Binding("x", "expand_activity", "Expand", show=False),
        Binding("q", "quit", "Quit"),
    ]
    CSS = """
    Screen { background: $background; }
    #workspace-header { height: 1; background: $panel; text-style: bold; padding: 0 1; }
    #selection-bar { height: 2; color: $text-muted; padding: 0 1; }
    #result-summary { height: auto; max-height: 5; padding: 0 1; display: none; background: $panel; }
    #result-summary.-visible { display: block; }
    #approval { height: auto; max-height: 8; border-top: solid $warning; display: none; }
    #approval.-visible { display: block; }
    #approval-text { height: auto; max-height: 4; padding: 0 1; }
    #approval-buttons { height: 3; }
    #approval-buttons Button { height: 3; width: 1fr; min-width: 12; }
    Screen.-approving #lower-tabs { display: none; }
    Screen.-approving #graph-row { height: 1fr; max-height: 100%; }
    #review-banner { height: auto; max-height: 5; padding: 0 1; border-bottom: solid $panel; }
    #review-banner.-hidden { display: none; }
    #fixture-banner { height: 1; background: $warning; display: none; }
    #fixture-banner.-visible { display: block; }
    #graph-row { height: 2fr; min-height: 5; max-height: 70%; }
    GraphView { width: 2fr; min-width: 25; height: 1fr; }
    .graph-level { height: auto; }
    .graph-connector { height: auto; max-height: 2; color: $text-muted; padding: 0 1; }
    #detail-pane { width: 1fr; min-width: 22; height: 1fr; border-left: solid $panel; padding: 0 1; }
    #lower-tabs { height: 1fr; min-height: 7; border-top: solid $panel; }
    .pane-title { height: 1; color: $text-muted; }
    ActivityPane, ChecksPane, EvidencePane, OutputsPane { height: 1fr; }
    #output-table { height: 1fr; min-height: 2; }
    #output-path { height: auto; max-height: 3; color: $text-muted; }
    #evidence-tree { height: 1fr; }
    #diagnostics-pane { height: 7; display: none; border-top: solid $warning; }
    #diagnostics-pane.-visible { display: block; }
    #search-input { display: none; height: 3; }
    #search-input.-visible { display: block; }
    Screen.-compact ContractCard { height: 4; }
    Screen.-compact .graph-level { layout: vertical; }
    Screen.-compact ContractCard { width: 1fr; }
    Screen.-compact .graph-connector { display: none; }
    Screen.-narrow #detail-pane { display: none; }
    Screen.-narrow GraphView { width: 1fr; }
    Screen.-narrow .graph-level { layout: vertical; }
    Screen.-narrow ContractCard { width: 1fr; height: 4; }
    Screen.-explore #graph-row { display: none; }
    Screen.-expanded #graph-row { display: none; }
    """

    def __init__(
        self,
        graph: Graph | None = None,
        *,
        fixture_label: str | None = None,
        reduced_motion: bool = False,
        source: str = "",
    ) -> None:
        super().__init__()
        self._graph = graph
        self._fixture_label = fixture_label
        self._by_identity = {n.identity: n for n in build_nodes(graph)} if graph else {}
        self._dependencies = dependencies_of(graph) if graph else {}
        self.observations = Observations()
        self.selected_identity: str | None = None
        self.active_identity: str | None = None
        self.whole_factory = True
        self._auto_follow = True
        self.reduced_motion = reduced_motion
        self.lifecycle = "Declared preview" if graph else "Review source"
        self.source = source or str(graph.root if graph else "")
        self.messages: deque[str] = deque(maxlen=200)
        self.outputs: list[OutputItem] = []
        self.selected_output: OutputItem | None = None
        self.final_result: ChainResult | None = None
        self.command_code: int | None = None
        self.delivery_path: Path | None = None
        self.delivery_state = "Not delivered yet"
        self._return_focus = None
        self._initial_focus = True
        self._views_dirty = True
        self._mounted_ready = False

    def compose(self) -> ComposeResult:
        yield Static("APMX | " + self.lifecycle, id="workspace-header", markup=False)
        yield Static(id="selection-bar", markup=False)
        yield Static(id="result-summary", markup=False)
        yield Static(
            display_text(
                f"Source: {self.source}\nGraph unresolved; inputs not captured. Preparation may install packages.\nNo agent or check runs before execution approval."
            ),
            id="review-banner",
            markup=False,
            classes="-hidden" if self._graph else "",
        )
        yield Static(
            f"FIXTURE REPLAY - {self._fixture_label} - not a real run",
            id="fixture-banner",
            classes="-visible" if self._fixture_label else "",
        )
        with Horizontal(id="graph-row"):
            yield GraphView(self._graph)
            yield DetailPane(id="detail-pane", dependencies=self._dependencies)
        with TabbedContent(initial="activity-tab", id="lower-tabs"):
            with TabPane("Activity", id="activity-tab"):
                yield ActivityPane(id="activity-pane")
            with TabPane("Checks", id="checks-tab"):
                yield ChecksPane(id="checks-pane")
            with TabPane("Outputs", id="outputs-tab"):
                yield OutputsPane(id="outputs-pane")
            with TabPane("Evidence", id="evidence-tab"):
                yield EvidencePane(id="evidence-pane")
        with Vertical(id="approval"):
            yield Static(id="approval-text", markup=False)
            with Horizontal(id="approval-buttons"):
                yield Button(
                    Text("No [n] (default)"), id="execution-no", variant="error", disabled=True
                )
                yield Button(
                    Text("Yes, execute [y]"), id="execution-yes", variant="warning", disabled=True
                )
        search = Input(placeholder="Filter current scope; Esc restores focus", id="search-input")
        search.can_focus = False
        yield search
        yield DiagnosticsPane(id="diagnostics-pane")
        yield Footer()

    def on_mount(self) -> None:
        if self._mounted_ready:
            return
        self._mounted_ready = True
        self.query_one("#output-table", DataTable).add_columns(
            "File", "Producer", "Attempt", "Outcome", "Bytes"
        )
        if self._by_identity:
            self.select(next(iter(self._by_identity)), manual=False)
            card = self.query_one(GraphView).card(self.selected_identity)
            if card:
                card.focus()
        self._refresh_header()
        self.set_interval(1 / 3, self._tick)
        self.on_resize()

    def on_resize(self) -> None:
        self.screen_stack[0].set_class(self.size.width < 100, "-compact")
        self.screen_stack[0].set_class(self.size.width < 72, "-narrow")
        for card in self.query(ContractCard):
            card._refresh_text()

    async def set_graph(self, graph: Graph) -> None:
        if self._graph == graph:
            return
        self._graph = graph
        self._by_identity = {n.identity: n for n in build_nodes(graph)}
        self._dependencies = dependencies_of(graph)
        old = self.query_one(GraphView)
        await old.remove()
        await self.query_one("#graph-row").mount(GraphView(graph), before=0)
        self.query_one(DetailPane).dependencies = self._dependencies
        self.query_one("#review-banner").add_class("-hidden")
        if self._by_identity:
            self.select(next(iter(self._by_identity)), manual=False)
        self._refresh_header()

    def on_card_selected(self, message: CardSelected) -> None:
        self.select(message.identity, manual=not self._initial_focus)
        self._initial_focus = False

    def select(self, identity: str, *, manual: bool = True) -> None:
        if identity not in self._by_identity:
            return
        self.selected_identity = identity
        if manual:
            self._auto_follow = False
            self.whole_factory = False
        for card in self.query(ContractCard):
            card.set_class(card.node.identity == identity, "-selected")
            card._refresh_text()
        self.query_one(DetailPane).selected = self._by_identity[identity]
        self._views_dirty = True
        self._refresh_views()
        self._refresh_header()

    def _refresh_header(self) -> None:
        passed = sum(card.status == "passed" for card in self.query(ContractCard))
        code = f" | exit {self.command_code}" if self.command_code is not None else ""
        self.query_one("#workspace-header", Static).update(
            display_text(
                f"APMX | {self.lifecycle} | {passed}/{len(self._by_identity)} passed{code}"
            )
        )
        selected = _display_name(self.selected_identity) if self.selected_identity else "none"
        active = _display_name(self.active_identity) if self.active_identity else "none"
        self.query_one("#selection-bar", Static).update(
            display_text(
                f"Active: {active} | Selected: {selected}\n"
                f"Scope: {'Whole factory' if self.whole_factory else 'Selected contract'} [s] | "
                f"Follow: {'on' if self._auto_follow else 'off / pinned'} [f] | * Selected | Ctrl+p"
            )
        )

    def _tick(self) -> None:
        for card in self.query(ContractCard):
            card.motion = not self.reduced_motion
            card.tick()
        if self._views_dirty:
            self._refresh_views()

    def set_phase(self, phase: str) -> None:
        self.lifecycle = phase
        self._refresh_header()

    def append_message(self, text: str) -> None:
        self.messages.append(display_text(text))
        self._views_dirty = True

    def apply_card_status(self, identity: str, status: CardStatus) -> None:
        card = self.query_one(GraphView).card(identity)
        if card:
            card.motion = not self.reduced_motion
            card.status = status
        if status in ACTIVE:
            self.active_identity = identity
            if self._auto_follow:
                # Selection is not keyboard focus, viewport position or log tail-follow.
                self.select(identity, manual=False)
        elif self.active_identity == identity:
            self.active_identity = None
        detail = self.query_one(DetailPane)
        if detail.selected:
            detail.watch_selected(detail.selected)
        self._refresh_header()

    def append_event(self, event: RunEvent) -> None:
        identity = next(
            (n.identity for n in self._by_identity.values() if n.path == event.context.contract),
            None,
        )
        attempt = self.observations.append(identity, event)
        if identity and attempt:
            card = self.query_one(GraphView).card(identity)
            if card:
                card.attempt = attempt
                card._refresh_text()
            detail = self.query_one(DetailPane)
            if detail.selected and detail.selected.identity == identity:
                detail.watch_selected(detail.selected)
        self._views_dirty = True

    def append_diagnostic(self, line: str) -> None:
        self.query_one(DiagnosticsPane).append(line)

    def _attempts(self) -> list[AttemptView]:
        return [
            a
            for a in self.observations.attempts.values()
            if self.whole_factory or a.identity == self.selected_identity
        ]

    def _check_lines(self, *, details: bool = False) -> list[str]:
        lines = []
        for attempt in self._attempts():
            lines.append(f"{attempt.identity} | Attempt {attempt.index}/{attempt.limit}")
            for name, check in attempt.checks.items():
                if check is None:
                    lines.append(f"  [>] {name}: running; no result observed")
                else:
                    status = {0: "PASS", 1: "FAIL"}.get(check.normalized, "INCOMPLETE")
                    lines.append(
                        f"  {name}: {status} | exit {check.process.returncode} | {check.reason}"
                    )
                    if details:
                        lines.append(f"    Command: {check.command}")
                        lines.append(
                            f"    Cleanup: {'confirmed' if check.process.cleanup_confirmed else 'UNCONFIRMED'}"
                        )
            if not attempt.checks:
                lines.append("  No checks observed.")
        return lines

    def _evidence_groups(self) -> list[tuple[str, list[tuple[str, str]]]]:
        groups = []
        if self.whole_factory:
            groups += [
                (
                    "Factory definition",
                    [
                        (
                            "Source and declared graph",
                            "\n".join(
                                [
                                    self.source,
                                    "Artifact edges are separate from package/skill dependencies.",
                                ]
                                + [
                                    f"{n.identity}: needs {n.needs}; produces {n.produces}; checks {n.checks}"
                                    for n in self._by_identity.values()
                                ]
                            ),
                        )
                    ],
                ),
                (
                    "Outcome / delivery / signature",
                    [
                        (
                            "Execution: "
                            + (
                                self.final_result.outcome.name
                                if self.final_result
                                else "not finalized"
                            ),
                            str(self.final_result.record_path)
                            if self.final_result
                            else self.lifecycle,
                        ),
                        (
                            "Delivery: " + self.delivery_state,
                            str(self.delivery_path or "No exported package"),
                        ),
                        (
                            "Signature: unsigned local records",
                            "No authenticated builder identity or model-consumption claim. Produced != passed != applied.",
                        ),
                    ],
                ),
            ]
        for a in self._attempts():
            prefix = f"{_display_name(a.identity)} / attempt {a.index}/{a.limit}"
            groups.append(
                (
                    f"{prefix} / Inputs ({len(a.inputs)} captured)",
                    [
                        (
                            entry.relative_path,
                            f"Origin: {origin}\nBytes: {entry.size}\nSHA-256: {entry.sha256}",
                        )
                        for entry, origin in a.inputs
                    ]
                    or [
                        ("Not captured yet", "Declared requirements are not captured observations.")
                    ],
                )
            )
            groups.append(
                (
                    f"{prefix} / Checks ({len(a.checks)} observed)",
                    [
                        (
                            name
                            + ": "
                            + (
                                "running"
                                if check is None
                                else {0: "PASS", 1: "FAIL"}.get(check.normalized, "INCOMPLETE")
                            ),
                            "No result yet."
                            if check is None
                            else f"Command: {check.command}\nExit: {check.process.returncode}\nReason: {check.reason}\nCleanup confirmed: {check.process.cleanup_confirmed}\nSubject: {check.subject_digest}\nResources: {check.resources_digest}",
                        )
                        for name, check in a.checks.items()
                    ]
                    or [("No check observations", "No pass or failure observed.")],
                )
            )
            if a.result:
                result = a.result
                groups.append(
                    (
                        f"{prefix} / Artifacts ({len(artifact_files(result.artifact))})",
                        [
                            (
                                f.relative_path,
                                f"Retained: {f.path}\nBytes: {f.size}\nSHA-256: {f.sha256}\nOutcome: {result.outcome.name}; never automatically applied",
                            )
                            for f in artifact_files(result.artifact)
                        ]
                        or [
                            (
                                "No complete output captured",
                                "Inspect records for partial/unassessed work.",
                            )
                        ],
                    )
                )
                groups.append(
                    (
                        f"{prefix} / Records and capabilities",
                        [
                            (
                                "Record / transcript paths",
                                f"{result.run_directory / 'record.json'}\n{result.run_directory / 'transcript.log'}\nUse Ctrl+p: Read retained record / transcript.",
                            ),
                            (
                                "Execution observations",
                                f"Outcome: {result.outcome.name}\nStop: {result.stop_reason or 'none'}\nRequested model: {result.requested_model or 'default'}\nObserved models: {result.observed_models}\nConsent: {result.consent_source}; handoff: {result.handoff_policy}",
                            ),
                            (
                                "Retained source/capability inventory (not proof of use)",
                                "\n".join(
                                    f"{f.relative_path}\n  {f.size} bytes / SHA-256:{f.sha256}"
                                    for f in result.retained_provenance
                                )
                                or "No retained capability inventory",
                            ),
                        ],
                    )
                )
        return groups

    def _refresh_views(self) -> None:
        self._views_dirty = False
        lines = list(self.messages) if self.whole_factory else []
        for identity, event in self.observations.events:
            if event.kind == "finished":
                continue
            if self.whole_factory or (identity is not None and identity == self.selected_identity):
                prefix = f"{identity or 'factory'} / a{event.context.attempt}"
                lines.append(f"{prefix}: {_format_event(event)}")
        if self.observations.omitted:
            lines.insert(
                0,
                f"[{self.observations.omitted} older UI events omitted; full retained transcripts in Evidence]",
            )
        self.query_one(ActivityPane).replace_lines(lines)
        self.query_one(ChecksPane).replace_lines(self._check_lines())
        self.query_one(EvidencePane).replace_groups(self._evidence_groups())
        items = [
            OutputItem(a.identity, a.index, artifact, a.result)
            for a in self._attempts()
            if a.result
            for artifact in artifact_files(a.result.artifact)
        ]
        if items != self.outputs:
            prior = self.selected_output.key if self.selected_output else None
            self.outputs = items
            table = self.query_one("#output-table", DataTable)
            table.clear()
            for item in items:
                table.add_row(
                    display_text(item.artifact.relative_path),
                    _display_name(item.identity),
                    str(item.attempt),
                    item.result.outcome.name,
                    str(item.artifact.size),
                )
            if items:
                index = next((i for i, item in enumerate(items) if item.key == prior), 0)
                table.move_cursor(row=index)
                self._select_output(index)
            else:
                self.selected_output = None
                self.query_one("#output-path", Static).update("No output captured in this scope.")

    def _select_output(self, index: int) -> None:
        if 0 <= index < len(self.outputs):
            self.selected_output = self.outputs[index]
            self.query_one("#output-path", Static).update(
                display_text(
                    f"{self.selected_output.artifact.path}\np preview | y copy path | l open location"
                )
            )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._select_output(event.cursor_row)

    def show_result(self, result: ChainResult) -> None:
        self.final_result = result
        for card in self.query(ContractCard):
            if card.status == "pending":
                self.apply_card_status(card.node.identity, "blocked")
            elif card.status in ACTIVE:
                self.apply_card_status(
                    card.node.identity,
                    "cancelled" if result.stop_reason == "cancelled" else "failed",
                )
        self.active_identity = None
        self._views_dirty = True

    def finish_command(self, code: int, *, planning: bool = False) -> None:
        self.command_code = code
        if self.delivery_state == "Not delivered yet":
            self.delivery_state = "Not delivered"
        if not planning and self.final_result is None:
            for card in self.query(ContractCard):
                if card.status in ACTIVE:
                    self.apply_card_status(card.node.identity, "unassessed")
                elif card.status == "pending":
                    self.apply_card_status(card.node.identity, "blocked")
            self.active_identity = None
        self.lifecycle = "Explore plan" if planning and code == 0 else "Explore results"
        if not planning:
            self.screen_stack[0].add_class("-explore")
            self.whole_factory = True
            self.action_show_tab("outputs-tab" if self.final_result else "activity-tab")
            outcome = (
                self.final_result.outcome.name
                if self.final_result
                else "No validated factory result"
            )
            summary = self.query_one("#result-summary", Static)
            summary.add_class("-visible")
            summary.update(
                display_text(
                    f"Execution: {outcome} | Evidence: {self.delivery_state}\n"
                    f"Package: {self.delivery_path or 'none delivered'}\n"
                    "g Graph / select contracts | p Preview output | b Evidence location | Ctrl+p actions"
                )
            )
        self._refresh_views()
        self._refresh_header()

    def action_toggle_scope(self) -> None:
        self.whole_factory = not self.whole_factory
        self._refresh_views()
        self._refresh_header()

    def action_toggle_follow(self) -> None:
        self._auto_follow = not self._auto_follow
        if self._auto_follow and self.active_identity:
            self.select(self.active_identity, manual=False)
        self._refresh_header()

    def action_toggle_graph(self) -> None:
        self.screen_stack[0].toggle_class("-explore")

    def action_toggle_motion(self) -> None:
        self.reduced_motion = not self.reduced_motion
        self.notify("Reduced motion: " + ("on" if self.reduced_motion else "off"))

    def action_expand_activity(self) -> None:
        self.screen_stack[0].toggle_class("-expanded")

    def action_show_tab(self, tab_id: str) -> None:
        self.query_one("#lower-tabs", TabbedContent).active = tab_id

    def action_toggle_diagnostics(self) -> None:
        self.query_one("#diagnostics-pane").toggle_class("-visible")

    def action_toggle_search(self) -> None:
        search = self.query_one("#search-input", Input)
        if search.has_class("-visible"):
            self.action_clear_search()
            return
        self._return_focus = self.focused
        search.add_class("-visible")
        search.can_focus = True
        search.focus()

    def action_clear_search(self) -> None:
        search = self.query_one("#search-input", Input)
        search.remove_class("-visible")
        search.can_focus = False
        search.value = ""
        for pane in self.query(_FilterableLogPane):
            pane.apply_filter("")
        self.query_one(EvidencePane).apply_filter("")
        if self._return_focus is not None:
            self._return_focus.focus()

    def on_input_changed(self, message: Input.Changed) -> None:
        if message.input.id == "search-input":
            for pane in self.query(_FilterableLogPane):
                pane.apply_filter(message.value)
            self.query_one(EvidencePane).apply_filter(message.value)

    def action_inspect(self) -> None:
        active = self.query_one("#lower-tabs", TabbedContent).active
        if active == "outputs-tab":
            self.action_preview_output()
        elif active == "checks-tab":
            self.push_screen(
                InspectionScreen(
                    "Checks / current scope", "\n".join(self._check_lines(details=True))
                )
            )
        elif active == "evidence-tab":
            self.query_one("#evidence-tree", Tree).focus()
        else:
            self.push_screen(
                InspectionScreen("Selected contract", self.query_one(DetailPane).text())
            )

    def action_preview_output(self) -> None:
        if self.selected_output is None:
            self.notify("No retained output selected.", severity="warning")
            return
        item = self.selected_output
        try:
            text = read_preview(item.artifact.path, root=item.result.run_directory)
        except OSError as exc:
            self.notify(display_text(str(exc)), severity="error")
            return
        self.push_screen(
            InspectionScreen(
                f"{item.artifact.relative_path} | {item.identity} / attempt {item.attempt} | {item.result.outcome.name}\n{item.artifact.path}",
                text,
            )
        )

    def action_inspect_record(self, *, transcript: bool = False) -> None:
        attempts = self._attempts()
        if not transcript and self.whole_factory and self.final_result:
            path = self.final_result.record_path
            root = path.parent
        elif attempts and attempts[-1].directory:
            root = attempts[-1].directory
            path = root / ("transcript.log" if transcript else "record.json")
        else:
            self.notify("No retained record in this scope yet.", severity="warning")
            return
        try:
            text = read_preview(path, root=root)
        except OSError as exc:
            self.notify(display_text(str(exc)), severity="error")
            return
        self.push_screen(InspectionScreen(str(path), text))

    def action_copy_path(self) -> None:
        if self.selected_output:
            self.copy_to_clipboard(str(self.selected_output.artifact.path))
            self.notify("Path sent to terminal clipboard (OSC 52); terminal support required.")
        else:
            self.notify("Select a retained output first.", severity="warning")

    def action_open_location(self) -> None:
        if self.selected_output:
            self.open_url(self.selected_output.artifact.path.parent.as_uri())
            self.notify("Location requested in the system opener; external support required.")
        else:
            self.notify("Select a retained output first.", severity="warning")

    def action_evidence_location(self) -> None:
        if self.delivery_path:
            self.open_url(self.delivery_path.as_uri())
            self.notify("Evidence location requested in system opener; support required.")
        else:
            self.notify(
                "No evidence package delivered. Inspect Evidence for its separate status.",
                severity="warning",
            )

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        yield from super().get_system_commands(screen)
        yield SystemCommand(
            "Inspect factory source",
            "Full source and entry identity",
            lambda: self.push_screen(InspectionScreen("Factory source", self.source)),
        )
        yield SystemCommand(
            "Open evidence package location",
            "Actual exported package, not a retained deliverable",
            self.action_evidence_location,
        )
        for node in self._by_identity.values():
            yield SystemCommand(
                f"Select {_display_name(node.identity)}",
                node.identity,
                lambda identity=node.identity: self.select(identity),
            )
        for title, help_text, action in [
            ("Toggle scope", "Whole factory / Selected contract", self.action_toggle_scope),
            (
                "Follow running",
                "Selection follows; keyboard focus stays put",
                self.action_toggle_follow,
            ),
            ("Inspect selected", "Contract, checks, evidence or output", self.action_inspect),
            ("Preview output", "Bounded safe read-only preview", self.action_preview_output),
            ("Copy output path", "Terminal clipboard support required", self.action_copy_path),
            (
                "Open output location",
                "System opener, never runs the output",
                self.action_open_location,
            ),
            ("Search observations", "Filter current scope", self.action_toggle_search),
            (
                "Show graph / results",
                "Return to graph without losing selection",
                self.action_toggle_graph,
            ),
            (
                "Expand / collapse activity",
                "Temporarily use the main workspace",
                self.action_expand_activity,
            ),
            (
                "Read retained record",
                "Bounded preview of actual capabilities and record fields",
                self.action_inspect_record,
            ),
            (
                "Read retained transcript",
                "Selected scope, latest attempt; bounded preview of fuller retained log",
                lambda: self.action_inspect_record(transcript=True),
            ),
            (
                "Reduce motion",
                "Static running indicator with phase and elapsed time",
                self.action_toggle_motion,
            ),
        ]:
            yield SystemCommand(title, help_text, action)
