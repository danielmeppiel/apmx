"""Line-oriented presentation for contract plans and run observations."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import sys
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, ClassVar

from apmx.contracts.events import (
    HEARTBEAT_SECONDS,
    ApmInstallEvent,
    ApmOutputEvent,
    PreparationEvent,
)
from apmx.contracts.models import (
    ChainResult,
    CheckObservation,
    ContractError,
    ContractLimits,
    FileEntry,
    LeafPlan,
    Outcome,
    RepairBudget,
    RunEvent,
    RunResult,
    artifact_files,
)
from apmx.contracts.stream import TEXT_LINE_BYTES, safe_text
from apmx.utils import console
from apmx.utils.paths import portable_link_relpath, portable_relpath

if TYPE_CHECKING:
    from rich.status import Status

    from apmx.contracts.resolution import ChainPlan, Graph

# Display-only clock; tests replace it to make elapsed-time text deterministic.
_clock = time.monotonic
# Append-only output reports liveness at most this often during quiet work.
LIVENESS_SECONDS = 30
# Block bodies align under the contract name that follows "[i/N] ".
BLOCK = 6
TAIL_LINES = 5


def _seconds(value: float) -> str:
    value = max(0.0, value)
    if value < 10:
        return f"{value:.1f}s"
    if value < 60:
        return f"{value:.0f}s"
    minutes, seconds = divmod(round(value), 60)
    return f"{minutes}m{seconds:02d}s"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


class _ProseDisplay:
    """Readable ASCII typography without rewriting literal code or path-like tokens."""

    _punctuation = str.maketrans(
        {
            "\u2018": "'",
            "\u2019": "'",
            "\u201a": "'",
            "\u201b": "'",
            "\u201c": '"',
            "\u201d": '"',
            "\u201e": '"',
            "\u201f": '"',
            "\u00ab": '"',
            "\u00bb": '"',
            "\u2039": "'",
            "\u203a": "'",
            "\u2010": "-",
            "\u2011": "-",
            "\u2012": "-",
            "\u2013": "-",
            "\u2014": "--",
            "\u2015": "--",
            "\u2026": "...",
            "\u00a0": " ",
            "\u202f": " ",
        }
    )

    def __init__(self) -> None:
        self.fence: tuple[str, int] | None = None
        self.inline = 0

    def render(self, text: str) -> str:
        return "\n".join(self._line(line) for line in text.split("\n"))

    def _line(self, text: str) -> str:
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", text.removesuffix("\r"))
        if self.fence:
            if (
                fence
                and fence[1][0] == self.fence[0]
                and len(fence[1]) >= self.fence[1]
                and not fence[2].strip(" \t")
            ):
                self.fence = None
            return text
        if not self.inline and text[:4].expandtabs(4).startswith("    "):
            return text
        if fence and not self.inline:
            self.fence = fence[1][0], len(fence[1])
            return text
        parts = []
        for segment in re.split(r"(`+)", text):
            if segment.startswith("`"):
                if len(segment) == self.inline:
                    self.inline = 0
                elif not self.inline:
                    self.inline = len(segment)
                parts.append(segment)
            elif self.inline:
                parts.append(segment)
            else:
                parts.append(
                    "".join(
                        token
                        if re.search(r"[/\\]|\.\w", token)
                        else token.translate(self._punctuation)
                        for token in re.split(r"([ \t]+)", segment)
                    )
                )
        return "".join(parts)


class _Transcript:
    """Bounded beginning/tail retention with exact omitted byte/line counts."""

    def __init__(self, limit: int) -> None:
        # Reserve room for the ASCII omission marker, even at the retention cap.
        self.budget = max(0, limit - 256)
        self.head: list[bytes] = []
        self.tail: deque[bytes] = deque()
        self.head_bytes = 0
        self.tail_bytes = 0
        self.omitted_bytes = 0
        self.omitted_lines = 0
        self.head_full = False

    def append(self, line: str) -> None:
        encoded = (line + "\n").encode("ascii")
        if not self.head_full and self.head_bytes + len(encoded) <= self.budget // 2:
            self.head.append(encoded)
            self.head_bytes += len(encoded)
            return
        self.head_full = True
        self.tail.append(encoded)
        self.tail_bytes += len(encoded)
        while self.tail and self.head_bytes + self.tail_bytes > self.budget:
            removed = self.tail.popleft()
            self.tail_bytes -= len(removed)
            self.omitted_bytes += len(removed)
            self.omitted_lines += 1

    def write(self, target: BinaryIO) -> None:
        target.writelines(self.head)
        if self.omitted_lines:
            target.write(
                (
                    f"[i] Transcript truncated: {self.omitted_lines} lines / "
                    f"{self.omitted_bytes} bytes omitted between beginning and tail.\n"
                ).encode("ascii")
            )
        target.writelines(self.tail)


class _Role(str, Enum):
    START = "start"
    INFO = "info"
    NOTICE = "notice"
    HEADING = "heading"
    WARNING = "warning"
    ERROR = "error"
    SUCCESS = "success"
    DETAIL = "detail"
    EXTERNAL = "external"
    TITLE = "title"


class _Visibility(Enum):
    ALWAYS = "always"
    VERBOSE = "verbose"
    RETAINED = "retained"


class _Layout(Enum):
    LITERAL = "literal"
    PROSE = "prose"


class _Level(IntEnum):
    HEADING = 0
    BODY = 2
    DETAIL = 4


class _Boundary(Enum):
    EMPTY = "empty"
    CONTENT = "content"
    GAP = "gap"


@dataclass(frozen=True)
class _StepContext:
    index: int
    count: int
    contract: Path


@dataclass(frozen=True)
class _StopContext:
    outcome: Outcome
    reason: str
    code: str


@dataclass(frozen=True)
class _DisplayLine:
    """Sanitized human text, independent of retention and visibility policy."""

    text: str
    role: _Role = _Role.INFO
    source: str = ""
    level: int = _Level.BODY
    accent: str = ""
    layout: _Layout = _Layout.LITERAL
    dim_remainder: bool = False
    hang: int = 0
    # Summary rows wrap between words only, so paths and names stay copyable.
    keep_words: bool = False


@dataclass(frozen=True)
class _RoleStyle:
    symbol: str
    color: str


class _ContractDisplay:
    """Invocation-scoped screen state only; never reads or derives run evidence."""

    _roles: ClassVar[dict[_Role, _RoleStyle]] = {
        _Role.START: _RoleStyle("running", "cyan"),
        _Role.INFO: _RoleStyle("", "default"),
        _Role.NOTICE: _RoleStyle("info", "cyan"),
        _Role.HEADING: _RoleStyle("", "cyan"),
        _Role.WARNING: _RoleStyle("warning", "yellow"),
        _Role.ERROR: _RoleStyle("error", "red"),
        _Role.SUCCESS: _RoleStyle("check", "green"),
        _Role.DETAIL: _RoleStyle("", "dim"),
        _Role.EXTERNAL: _RoleStyle("", "dim cyan"),
        _Role.TITLE: _RoleStyle("", "cyan"),
    }
    _outcomes: ClassVar[dict[Outcome, _Role]] = {
        Outcome.COMPLETE: _Role.SUCCESS,
        Outcome.UNPROVEN: _Role.WARNING,
        Outcome.REJECTED: _Role.ERROR,
        Outcome.HALTED: _Role.ERROR,
    }

    def __init__(self) -> None:
        self.enabled = True
        self.status: Status | None = None
        self.revision = 0
        self._boundary = _Boundary.EMPTY
        self.disclosure_shown = False
        self.identity_shown = False
        self.models_shown: set[str] = set()
        self.started = _clock()

    @classmethod
    def outcome_role(cls, outcome: Outcome) -> _Role:
        return cls._outcomes[outcome]

    @classmethod
    def marker(cls, role: _Role, source: str = "") -> str:
        symbol = cls._roles[role].symbol
        return console.STATUS_SYMBOLS[symbol] + " " if symbol and not source else ""

    def gap(self) -> None:
        if self._boundary is _Boundary.CONTENT:
            self._echo(_DisplayLine("", level=_Level.HEADING))

    def emit(
        self,
        line: _DisplayLine,
        *,
        visibility: _Visibility = _Visibility.ALWAYS,
        verbose: bool = False,
    ) -> None:
        if visibility is _Visibility.RETAINED or (
            visibility is _Visibility.VERBOSE and not verbose
        ):
            return
        if not line.text and not line.source:
            self.gap()
            return
        self._echo(line)

    def _echo(self, line: _DisplayLine) -> None:
        if not self.enabled:
            return
        role = _Role.EXTERNAL if line.source and line.role is _Role.INFO else line.role
        style = self._roles[role]
        prefix = " " * line.level + self.marker(role, line.source)
        if line.source:
            prefix += f"{line.source} > "
        text = prefix + line.text
        accent_length = len(prefix) + len(line.accent)
        if role in {_Role.DETAIL, _Role.HEADING}:
            accent_length = len(text)
        capabilities = console.terminal_capabilities()
        try:
            console._rich_echo(
                text,
                color=style.color,
                bold=role is _Role.HEADING or bool(line.accent),
                propagate_broken_pipe=True,
                plain=not capabilities.styled,
                natural_wrap=True,
                accent_length=accent_length,
                body_style="dim" if line.dim_remainder else "default",
                hanging_indent=len(prefix) + line.hang if line.layout is _Layout.PROSE else None,
                capabilities=capabilities,
                break_long_words=not line.keep_words,
            )
        except BrokenPipeError:
            self.disable()
            return
        self.revision += 1
        self._boundary = _Boundary.CONTENT if line.text or line.source else _Boundary.GAP

    def animates(self) -> bool:
        from apmx.utils.install_tui import should_animate

        rich_console = console._get_console()
        return (
            self.enabled
            and console.terminal_capabilities().styled
            and should_animate()
            and rich_console is not None
            and rich_console.is_terminal
        )

    def start_activity(self, message: str | Callable[[], str]) -> None:
        """Animate one transient line; a callable label is re-read on every refresh."""
        if not self.animates():
            self.stop_activity()
            return
        from rich.text import Text

        if callable(message):
            label = _LiveLabel(message, self._roles[_Role.INFO].color)
        else:
            label = Text(
                message + "...",
                style=self._roles[_Role.INFO].color,
                no_wrap=True,
                overflow="ellipsis",
            )
        try:
            if self.status is None:
                self.status = console._get_console().status(
                    label,
                    spinner="line",
                    spinner_style=self._roles[_Role.START].color,
                    refresh_per_second=8,
                )
                self.status.start()
            else:
                self.status.update(label)
            self.revision += 1
        except BrokenPipeError:
            self.disable()

    def stop_activity(self) -> None:
        status, self.status = self.status, None
        if status is not None:
            try:
                status.stop()
            except BrokenPipeError:
                self.disable()

    def disable(self) -> None:
        self.enabled = False
        self.stop_activity()
        console.silence_broken_pipe()


class _LiveLabel:
    """Rich renderable for the in-place attempt line; ASCII-truncated to one row."""

    def __init__(self, render: Callable[[], str], color: str) -> None:
        self.render = render
        self.color = color

    def __rich__(self):
        from rich.text import Text

        width = max(8, console.terminal_capabilities().width - 3)
        text = self.render()
        if len(text) > width:
            text = text[: width - 3].rstrip() + "..."
        return Text(text, style=self.color, no_wrap=True, overflow="crop")


@dataclass
class _AttemptView:
    """Display-only summary of one attempt; outcomes stay with the engine/record owners."""

    index: int
    count: int
    agent_clock: float | None = None
    agent_started: float | None = None
    agent_seconds: float | None = None
    checks: list[tuple[str, int]] = field(default_factory=list)
    tails: dict[str, deque[str]] = field(default_factory=dict)
    notes: list[tuple[str, str]] = field(default_factory=list)
    status: str = ""
    stage: str = "preparing"
    summarized: bool = False
    liveness: float = 0.0


@dataclass
class _LeafView:
    """One contract block shared by its leaf logger and every attempt logger."""

    name: str = ""
    header_shown: bool = False
    budget: RepairBudget | None = None
    last: _AttemptView | None = None
    repair_stop: str = ""


@dataclass
class _FactoryNode:
    index: int
    name: str
    needs: tuple[str, ...]
    outputs: tuple[str, ...]
    budget: RepairBudget | None
    producers: tuple[Path, ...]
    consumers: tuple[Path, ...]
    leaf: _LeafView = field(default_factory=_LeafView)
    started: bool = False
    complete: bool = False


@dataclass
class _FactoryView:
    """Admitted graph shape for display: order, waits-on and handed-to lines."""

    label: str
    nodes: dict[Path, _FactoryNode]
    width: int
    failed: tuple[str, RunResult, _LeafView] | None = None
    pending: tuple[str, tuple[Path, ...]] | None = None


@dataclass(frozen=True)
class _EvidenceFragment:
    line: int
    offset: int
    text: str
    line_bytes: int


@dataclass(frozen=True)
class _EvidenceExcerpt:
    fragments: tuple[_EvidenceFragment, ...]
    omitted_bytes: int
    omitted_lines: int
    partial_lines: int


class _CheckEvidence:
    """Bounded sanitized stdout, including the beginning/tail of one huge line.

    Budgets count ASCII diagnostic bytes including logical newlines. Source
    prefixes, omission notices and terminal wrapping are not diagnostic bytes.
    """

    PENDING_BYTES = 8192
    PENDING_LINES = 32
    EXCERPT_BYTES = 1024
    EXCERPT_LINES = 8

    def __init__(self, name: str) -> None:
        self.name = name
        self.total_bytes = 0
        self.total_lines = 0
        self._head: list[_EvidenceFragment] = []
        self._tail: deque[_EvidenceFragment] = deque()
        self._head_bytes = 0
        self._tail_bytes = 0
        self._head_full = False

    def append(self, sanitized: str) -> None:
        text = sanitized + "\n"
        size = len(text)
        number = self.total_lines
        self.total_lines += 1
        self.total_bytes += size
        taken = 0
        half_bytes, half_lines = self.PENDING_BYTES // 2, self.PENDING_LINES // 2
        if not self._head_full:
            taken = min(size, half_bytes - self._head_bytes)
            if taken:
                self._head.append(_EvidenceFragment(number, 0, text[:taken], size))
                self._head_bytes += taken
            self._head_full = (
                taken < size or self._head_bytes == half_bytes or len(self._head) == half_lines
            )
        if taken < size:
            offset = max(taken, size - half_bytes)
            fragment = _EvidenceFragment(number, offset, text[offset:], size)
            self._tail.append(fragment)
            self._tail_bytes += len(fragment.text)
        while len(self._tail) > half_lines:
            self._tail_bytes -= len(self._tail.popleft().text)
        while self._tail_bytes > half_bytes:
            first = self._tail.popleft()
            removed = min(len(first.text), self._tail_bytes - half_bytes)
            self._tail_bytes -= removed
            if removed < len(first.text):
                self._tail.appendleft(
                    replace(first, offset=first.offset + removed, text=first.text[removed:])
                )

    @property
    def pending_bytes(self) -> int:
        return self._head_bytes + self._tail_bytes

    @property
    def pending_lines(self) -> int:
        return len(self._head) + len(self._tail)

    @staticmethod
    def _take(fragments: tuple[_EvidenceFragment, ...], *, tail: bool) -> list[_EvidenceFragment]:
        budget = _CheckEvidence.EXCERPT_BYTES // 2
        selected = []
        for fragment in reversed(fragments) if tail else fragments:
            size = min(budget, len(fragment.text))
            offset = len(fragment.text) - size if tail else 0
            selected.append(
                replace(
                    fragment,
                    offset=fragment.offset + offset,
                    text=fragment.text[offset : offset + size],
                )
            )
            budget -= size
            if not budget or len(selected) == _CheckEvidence.EXCERPT_LINES // 2:
                break
        return list(reversed(selected)) if tail else selected

    def excerpt(self) -> _EvidenceExcerpt:
        pending = tuple(self._head) + tuple(self._tail)
        if self.pending_bytes <= self.EXCERPT_BYTES and len(pending) <= self.EXCERPT_LINES:
            chosen = list(pending)
        else:
            chosen = self._take(pending, tail=False) + self._take(pending, tail=True)
        merged: list[_EvidenceFragment] = []
        for fragment in sorted(chosen, key=lambda item: (item.line, item.offset)):
            if merged and fragment.line == merged[-1].line:
                previous = merged[-1]
                overlap = previous.offset + len(previous.text) - fragment.offset
                if overlap >= 0:
                    merged[-1] = replace(previous, text=previous.text + fragment.text[overlap:])
                    continue
            merged.append(fragment)
        line_sizes: dict[int, tuple[int, int]] = {}
        for fragment in merged:
            kept, total = line_sizes.get(fragment.line, (0, fragment.line_bytes))
            line_sizes[fragment.line] = kept + len(fragment.text), total
        return _EvidenceExcerpt(
            tuple(merged),
            self.total_bytes - sum(len(item.text) for item in merged),
            self.total_lines - len(line_sizes),
            sum(kept < total for kept, total in line_sizes.values()),
        )


class ContractLogger:
    """Semantic/evidence owner, composing the invocation's human presenter.

    close() freezes the transcript *before* the record owner hashes it. A later
    finished event renders the already-recorded result without touching logs.
    """

    def __init__(
        self,
        verbose: bool = False,
        *,
        _display: _ContractDisplay | None = None,
        _step: _StepContext | None = None,
    ) -> None:
        self.verbose = verbose
        self._display = _display if _display is not None else _ContractDisplay()
        self._step = _step
        self._check_evidence: _CheckEvidence | None = None
        self._chain_stop: _StopContext | None = None
        self._transcript = _Transcript(ContractLimits().transcript_bytes)
        self._file: BinaryIO | None = None
        self._run_id: str | None = None
        self._run_directory: Path | None = None
        self._closed = False
        self._finished = False
        self._last_phase: str | None = None
        self._last_evidence_activity = 0.0
        self._last_human_activity = 0.0
        self._caller_root = Path.cwd()
        self._display_root: Path | None = None
        self._factory_contract_count = 0
        self._produces = "saved output"
        self._harness = "Copilot"
        self._activity_label = "Working"
        self._checks_heading_shown = False
        self._preparation_notice_shown = False
        self._apm_paths: tuple[tuple[str, str], ...] = ()
        # Message identities are admitted by the decoder's bounded correlation table.
        self._prose: dict[int, _ProseDisplay] = {}
        self._leaf = _LeafView()
        self._attempt: _AttemptView | None = None
        self._factory: _FactoryView | None = None
        self._invocation: str | None = None

    def remember_invocation(
        self,
        selection: str,
        *,
        package_ref: str | None = None,
        harness: str = "copilot",
        model: str | None = None,
        factory: bool = False,
        allow_host_access: bool = False,
    ) -> None:
        """Keep the user's own selection so Next lines can name a copyable rerun."""
        parts = ["apmx"]
        if package_ref:
            parts += ["--from", package_ref]
            if selection not in {"", "."}:
                parts.append(selection)
        else:
            if factory and not (Path(selection).is_absolute() or selection.startswith((".", "~"))):
                selection = "./" + selection
            parts.append(selection)
        if harness != "copilot":
            parts += ["--on", harness]
        if model:
            parts += ["--model", model]
        if allow_host_access and not factory:
            # Factories ask again interactively; a single contract only accepts the flag.
            parts.append("--allow-host-access")
        self._invocation = safe_text(shlex.join(parts))

    def start_activity(self, message: str, *, announce: bool = True) -> None:
        """Animate quiet work using the install spinner, never in retained logs."""
        if self._closed:
            return
        self._activity_label = message
        animate = self._display.animates()
        if announce:
            self._write(message, severity="start", detail=animate)
        self._display.start_activity(safe_text(message))

    def stop_activity(self) -> None:
        """Restore the terminal before reporting an error or a final result."""
        self._display.stop_activity()

    @property
    def transcript_metadata(self) -> dict[str, int | str]:
        """Return owner-counted retention metadata, finalized by close().

        Each access returns a fresh snapshot; no transcript content is read.
        Omission counts refer to sanitized transcript bytes and logical lines.
        """
        return {
            "omitted_bytes": self._transcript.omitted_bytes,
            "omitted_lines": self._transcript.omitted_lines,
            "retention": "bounded-beginning-tail",
            "redaction": "best-effort",
        }

    def attach_run(self, run_id: str, run_directory: Path) -> None:
        """Exclusively create a private transcript in the admitted run directory."""
        if self._run_id is not None or self._closed:
            raise RuntimeError("A contract logger can only attach one run.")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(run_directory / "transcript.log", flags, 0o600)
        self._file = os.fdopen(descriptor, "wb")
        self._run_id = run_id
        self._run_directory = run_directory

    def close(self) -> None:
        """Flush once; errors propagate so recording cannot announce success."""
        if self._closed:
            return
        self._closed = True
        self.stop_activity()
        self._flush_check_evidence(completion_observed=False)
        if self._file is not None:
            try:
                self._transcript.write(self._file)
                self._file.flush()
                os.fsync(self._file.fileno())
            finally:
                self._file.close()

    def _write(
        self,
        message: str,
        *,
        severity: str = "info",
        detail: bool = False,
        attribution: str | None = None,
        accent: str = "",
        indent: int = 2,
        dim_remainder: bool = False,
        retained_only: bool = False,
        display_message: str | None = None,
        display: _DisplayLine | None = None,
        layout: _Layout = _Layout.LITERAL,
        retained_limit: int | None = None,
    ) -> None:
        """Retain the canonical logical line before any human-only transformation."""
        text = safe_text(message)
        source = safe_text(attribution, limit=256) if attribution else ""
        role = _Role(severity)
        marker = self._display.marker(role, source)
        if not self._closed:
            retained_prefix = f"{source} (untrusted) > " if source else ""
            retained = text if retained_limit is None else safe_text(message, limit=retained_limit)
            self._transcript.append(" " * indent + marker + retained_prefix + retained)
        visibility = (
            _Visibility.RETAINED
            if retained_only
            else _Visibility.VERBOSE
            if detail
            else _Visibility.ALWAYS
        )
        level = _Level.DETAIL if role is _Role.DETAIL else indent
        if display is not None:
            display = replace(display, level=display.level + self._block_offset(display.level))
        self._display.emit(
            display
            if display is not None
            else _DisplayLine(
                safe_text(display_message) if display_message is not None else text,
                role=role,
                source=source,
                level=level + self._block_offset(level),
                accent=safe_text(accent),
                layout=layout,
                dim_remainder=dim_remainder,
            ),
            visibility=visibility,
            verbose=self.verbose,
        )

    def _block_offset(self, level: int) -> int:
        """Inside a contract block, indented lines align under the block body."""
        return BLOCK - _Level.BODY if level and self._leaf.header_shown else 0

    def _show(self, line: _DisplayLine, *, verbose_only: bool = False) -> None:
        """Terminal-only summary line: never retained, never part of a record hash."""
        self._display.emit(
            replace(
                line, text=safe_text(line.text), accent=safe_text(line.accent), keep_words=True
            ),
            visibility=_Visibility.VERBOSE if verbose_only else _Visibility.ALWAYS,
            verbose=self.verbose,
        )

    def _retained_gap(self) -> None:
        """Preserve existing transcript separators; screen spacing is deduplicated."""
        if not self._closed:
            self._transcript.append("")
        self._display.gap()

    def _path(self, path: Path | str) -> str:
        """Keep saved paths copyable relative to the original caller, not cwd."""
        path = Path(path)
        if not path.is_absolute():
            path = self._caller_root / path
        return portable_relpath(path, self._display_root or self._caller_root)

    def on_event(self, event: RunEvent) -> None:
        """Consume the conductor's ordered stream; never derive an outcome."""
        handlers = {
            "selected": self._selected,
            "input_captured": self._input_captured,
            "workspace_captured": self._workspace_captured,
            "phase": self._phase,
            "activity": self._activity,
            "native_diagnostic": self._native_diagnostic,
            "skill_loaded": self._skill_loaded,
            "diagnostic": self._diagnostic,
            "metadata": self._metadata,
            "process_started": self._process_started,
            "stop_requested": self._stop_requested,
            "stop_observed": self._stop_observed,
            "check_started": self._check_started,
            "check_finished": self._check_finished,
            "finished": self._result,
            "heartbeat": self._heartbeat,
        }
        handler = handlers.get(event.kind)
        if handler is not None:
            revision = self._display.revision
            handler(event)
            if event.kind != "heartbeat":
                # Retained liveness never depends on verbosity, terminal or animation.
                if event.kind not in {"metadata", "process_started"}:
                    self._last_evidence_activity = event.elapsed_seconds
                if self._display.revision != revision:
                    self._last_human_activity = event.elapsed_seconds

    def on_preparation(self, event: PreparationEvent) -> None:
        """Retain pre-run facts in the same bounded transcript later attached by the engine."""
        if isinstance(event, ApmInstallEvent):
            subject = "packages" if event.scope == "package" else "project imports"
            if event.phase == "completed":
                self.stop_activity()
                self._write(f"{subject.capitalize()} ready.", severity="success")
                return
            self._apm_paths = (
                ((str(event.directory), "<temporary workspace>"),) if event.directory else ()
            )
            if event.package_ref and Path(event.package_ref).is_absolute():
                label = portable_link_relpath(event.package_ref, self._caller_root)
                self._apm_paths += ((event.package_ref, label or event.package_ref),)
            message = f"Installing {subject} with APM {event.version}"
            self._write(message, severity="start")
            self.start_activity(message, announce=False)
            if not self._preparation_notice_shown:
                self._write(
                    "Temporary workspace; your project files are unchanged.",
                    severity="detail",
                )
                self._preparation_notice_shown = True
            if event.frozen:
                self._write("Using locked versions.", severity="notice")
            self._write(
                "Running: apm install (in a temporary workspace)",
                severity="detail",
                detail=True,
            )
            options = "--only apm --target agent-skills --no-trust-bin"
            if event.frozen:
                options += " --frozen"
            if event.verbose:
                options += " --verbose"
            self._write(
                f"APM options: {options}",
                severity="detail",
                detail=True,
            )
            return
        if isinstance(event, ApmOutputEvent):
            self._apm_output(event)
            return
        if not event.imports:
            return
        packages = ", ".join(dict.fromkeys(item.name for item in event.imports))
        self._write(f"Imported {packages}", severity="notice")
        for item in event.imports:
            package = item.package_name or item.name
            name = item.context_name or item.name
            path = item.source_relative_path or item.source_path.name
            origin = path if name == package else f"from {package}; {path}"
            self._write(
                f"{item.kind.capitalize()}: {name} ({origin})",
                severity="detail",
                detail=True,
            )

    def _apm_output(self, event: ApmOutputEvent) -> None:
        text = event.text.strip()
        if text.startswith("[x]") or re.search(
            r"\b(error|failed|failure|fatal|denied)\b", text, re.IGNORECASE
        ):
            severity = "error"
        elif (
            event.overflow
            or text.startswith("[!]")
            or re.search(r"\b(warning|warn)\b", text, re.IGNORECASE)
        ):
            severity = "warning"
        else:
            severity = "info"
        detail = (
            event.stream == "stdout"
            and severity == "info"
            and text.startswith(
                (
                    "[*] Created apm.yml",
                    "[i] Targets",
                    "[*] Updated apm.yml",
                    "[>] Installing ",
                    "[+] ",
                    "|-- ",
                    "[i] Added apm_modules/",
                    "[i] Skipped inactive experimental resolver ",
                    "lockfile reconciliation.",
                    "+- To include it",
                    "for this install.",
                    "Added ",
                    "Parsed apm.yml:",
                    "Resolved dependency tree:",
                    "Phase:",
                    "Copilot native registration:",
                    "[#] Perf:",
                    "Generated apm.lock.yaml",
                )
            )
        )
        displayed = event.text
        if not self.verbose:
            for original, label in self._apm_paths:
                displayed = displayed.replace(original, label)
            if displayed.startswith("[>] Resolving ") and displayed.endswith("..."):
                reference = displayed[len("[>] Resolving ") : -3]
                if Path(reference).is_absolute():
                    relative = portable_link_relpath(reference, self._caller_root)
                    if relative is not None:
                        displayed = f"[>] Resolving {relative}..."
        self._write(
            event.text,
            attribution="APM stderr" if event.stream == "stderr" else "APM",
            severity="detail" if detail else severity,
            detail=detail,
            display_message=displayed,
        )

    @staticmethod
    def _field(event: RunEvent, name: str, default: str = "unknown") -> str:
        value = event.data.get(name)
        return value if isinstance(value, str) else default

    def _job_identity(self, source: Path | str, relative: str = "") -> str:
        """Prefer the selected contract's stable path over package-copy paths."""
        if relative:
            return relative
        identity = self._path(source)
        return Path(source).name if Path(identity).is_absolute() else identity

    def _selected(self, event: RunEvent) -> None:
        self.execution_context()
        self._harness = self._harness_label(self._field(event, "harness", "copilot"))
        caller = self._field(event, "caller_root", "")
        if caller:
            self._caller_root = Path(caller)
        self._produces = self._field(event, "produces", "saved output")
        source = self._field(event, "contract")
        relative = self._field(event, "contract_relative_path", "")
        package = self._field(event, "package_ref", "")
        identity = self._job_identity(source, relative)
        model = self._field(event, "model", "default model")
        if self._preparation_notice_shown:
            self._retained_gap()
        if self._attempt is None:
            self._attempt = _AttemptView(1, 1)
        self._leaf.last = self._attempt
        needs = event.data.get("needs", ())
        needs = needs if isinstance(needs, tuple) else ()
        name = identity.removesuffix(".contract.md")
        if not self._leaf.header_shown:
            self._leaf.name = self._leaf.name or Path(name).name
            if self._step is None:
                self._display.gap()
                self._show(
                    _DisplayLine(
                        f"Contract  {identity}   "
                        f"{self._field(event, 'harness', 'copilot')} / {model}",
                        role=_Role.TITLE,
                        accent=f"Contract  {identity}",
                        level=0,
                        layout=_Layout.PROSE,
                        hang=len("Contract  "),
                    )
                )
                self._display.gap()
                self._show(self._block_header(name, needs, self._produces, self._leaf.budget))
            else:
                self._show(
                    _DisplayLine(
                        self._flow(needs, self._produces),
                        level=BLOCK,
                        layout=_Layout.PROSE,
                    )
                )
            self._leaf.header_shown = True
        if self._step is None:
            self._write(f"Contract 1/1: {name}", severity="heading", indent=0, retained_only=True)
        # The block header already shows these facts; the transcript keeps them verbatim.
        self._write(f"Produces: {self._produces}", retained_only=True)
        self._write(
            "Needs: " + (", ".join(needs) if needs else "no declared input files"),
            retained_only=True,
        )
        if not self._display.identity_shown:
            self._display.identity_shown = True
            self._write(
                f"Harness: {self._field(event, 'harness', 'copilot')} / {model}",
                retained_only=True,
            )
            if model != "default model":
                self._display.models_shown.add(model)
        self._write(f"Source: {self._path(source)}", severity="detail", detail=True)
        if package:
            self._write(f"Package: {package}", severity="detail", detail=True)
        self._write(f"Run: {event.run_id}", severity="detail", detail=True)
        self._write(
            f"Record directory: {self._field(event, 'run_directory')}",
            severity="detail",
            detail=True,
        )

    def _input_captured(self, event: RunEvent) -> None:
        entry = event.data.get("entry")
        if not isinstance(entry, FileEntry):
            raise TypeError("input_captured requires an admitted FileEntry.")
        self._write(
            f"Found input: {entry.relative_path} "
            f"({self._field(event, 'origin')}; captured {entry.size} bytes)",
            detail=True,
        )
        self._write(f"Input SHA-256: {entry.sha256}", severity="detail", detail=True)
        record = self._field(event, "producer_record", "")
        if record:
            self._write(f"Input record: {self._path(record)}", severity="detail", detail=True)

    def _workspace_captured(self, event: RunEvent) -> None:
        self._write(
            f"Working copy: {event.data['files']} captured files; execution uses copies.",
            detail=True,
        )

    def _phase(self, event: RunEvent) -> None:
        phase = self._field(event, "name")
        if phase == self._last_phase:
            return
        self._last_phase = phase
        if phase == "checks":
            self._checks_heading()
        message = {
            "preflight": f"Capturing files for {self._harness}",
            "execution": f"Running {self._harness}",
            "capture": "Saving output",
            "checks": f"Checking {self._produces}",
            "record": "Saving results",
        }.get(phase)
        attempt = self._attempt
        if attempt is not None:
            if phase == "execution" and attempt.agent_started is None:
                attempt.agent_started = event.elapsed_seconds
                attempt.agent_clock = _clock()
            elif attempt.agent_started is not None and attempt.agent_seconds is None:
                attempt.agent_seconds = event.elapsed_seconds - attempt.agent_started
            attempt.stage = {
                "preflight": "preparing",
                "execution": "agent",
                "capture": "saving output",
                "checks": "checking",
                "record": "saving results",
            }.get(phase, attempt.stage)
        if phase == "record":
            self.stop_activity()
            self._summarize_attempt()
            if message:
                self._write(message, severity="start", detail=True)
            return
        if message:
            self._activity_label = message
            self._display.start_activity(
                self._live_label if attempt is not None else safe_text(message)
            )
            self._write(message, severity="start", detail=True)

    def _attribution(self, event: RunEvent) -> str:
        source = {"harness": self._harness, "checker": "Check"}.get(event.source, event.source)
        label = self._field(event, "label", "")
        stream = self._field(event, "stream", "")
        parts = [source]
        if label:
            parts.append(label)
        if stream == "stderr":
            parts.append("stderr")
        return " ".join(parts)

    def _skill_loaded(self, event: RunEvent) -> None:
        self._write(
            f"Loaded skill: {self._field(event, 'name')}",
            severity="notice",
            attribution=self._attribution(event),
        )

    def _native_diagnostic(self, event: RunEvent) -> None:
        """Keep native debug noise out of the checker evidence retention budget."""
        self._display.emit(
            _DisplayLine(
                safe_text(self._field(event, "text"), limit=TEXT_LINE_BYTES),
                source=f"{self._harness} native debug",
                role=_Role.DETAIL,
                level=_Level.DETAIL,
                layout=_Layout.LITERAL,
            ),
            visibility=_Visibility.VERBOSE,
            verbose=self.verbose,
        )

    def _activity(self, event: RunEvent) -> None:
        text = self._field(event, "text", "")
        tool_status = self._field(event, "tool_status", "")
        severity = "error" if tool_status == "failed" else "detail" if tool_status else "info"
        stderr = self._field(event, "stream", "") == "stderr"
        checker_stdout = event.source == "checker" and not stderr
        source = self._attribution(event)
        if checker_stdout:
            name = self._field(event, "label", "")
            if self._check_evidence is not None and self._check_evidence.name != name:
                self._flush_check_evidence(completion_observed=False)
            if self._check_evidence is None:
                self._check_evidence = _CheckEvidence(name)
            if not self._check_evidence.total_lines:
                self._display.emit(
                    _DisplayLine(
                        safe_text(f"Check {name} stdout:"),
                        role=_Role.DETAIL,
                        level=self._block_offset(_Level.BODY) + _Level.BODY,
                    ),
                    visibility=_Visibility.VERBOSE,
                    verbose=self.verbose,
                )
            self._check_evidence.append(safe_text(text))
        if self._attempt is not None:
            if event.source == "checker":
                tail = self._attempt.tails.setdefault(
                    self._field(event, "label", ""), deque(maxlen=TAIL_LINES)
                )
                tail.extend(line for line in safe_text(text).split("\n") if line.strip())
            elif text.strip() and not stderr:
                self._attempt.status = safe_text(text.strip().splitlines()[-1], limit=256)
        displayed = None
        if event.data.get("prose") is True:
            identifier = event.data.get("prose_group")
            if isinstance(identifier, int):
                formatter = self._prose.get(identifier)
                if formatter is None:
                    formatter = self._prose[identifier] = _ProseDisplay()
            else:
                formatter = _ProseDisplay()
            displayed = formatter.render(text)
        self._write(
            text,
            severity=severity,
            attribution=source,
            accent=text if tool_status == "failed" else "",
            display_message=displayed,
            detail=not (stderr and event.source != "checker"),
            layout=_Layout.PROSE if not tool_status else _Layout.LITERAL,
            retained_limit=TEXT_LINE_BYTES if checker_stdout else None,
            display=(
                _DisplayLine(
                    safe_text(text, limit=TEXT_LINE_BYTES),
                    role=_Role.DETAIL,
                    source=safe_text(source, limit=256),
                    level=_Level.DETAIL,
                )
                if checker_stdout
                else None
            ),
        )

    def _metadata(self, event: RunEvent) -> None:
        model = event.data.get("model")
        already_shown = isinstance(model, str) and model in self._display.models_shown
        if isinstance(model, str):
            self._display.models_shown.add(model)
        self._write(
            self._field(event, "text", ""),
            severity="detail",
            detail=not isinstance(model, str),
            attribution=self._attribution(event),
            retained_only=event.data.get("retained_only") is True or already_shown,
        )

    def _diagnostic(self, event: RunEvent) -> None:
        severity = self._field(event, "severity", "info")
        if severity not in {"info", "warning", "error"}:
            severity = "info"
        self._write(
            self._field(event, "message"),
            severity=severity,
            attribution=self._attribution(event) if event.source != "engine" else None,
        )
        action = self._field(event, "action", "")
        if action:
            self._write(
                action,
                attribution=self._attribution(event) if event.source != "engine" else None,
            )

    def _process_started(self, event: RunEvent) -> None:
        pid = event.data.get("pid")
        pgid = event.data.get("pgid")
        self._write(
            f"Managed child started: pid={pid if isinstance(pid, int) else 'unknown'}, "
            f"pgid={pgid if isinstance(pgid, int) else 'unknown'}",
            severity="detail",
            detail=True,
        )

    def _stop_requested(self, event: RunEvent) -> None:
        self.start_activity("Stopping processes", announce=False)
        self._write(
            "Stop requested; waiting for managed processes.",
            severity="warning",
        )
        self._write(f"Stop reason: {self._field(event, 'reason')}", severity="detail", detail=True)

    def _stop_observed(self, event: RunEvent) -> None:
        if event.data.get("confirmed") is True:
            self._write("Managed process group stopped; looking for output.")
            self._write("Escaped descendants are unobserved.", severity="detail", detail=True)
        else:
            self._write(
                "Stop unconfirmed; a child may still be running. Inspect before retrying.",
                severity="error",
            )

    def _check_started(self, event: RunEvent) -> None:
        self._flush_check_evidence(completion_observed=False)
        self._check_evidence = _CheckEvidence(self._field(event, "name"))
        self._checks_heading()
        name = self._field(event, "name")
        command = self._field(event, "command", "")
        self._write(f"Running check: {name}" + (f" - {command}" if command else ""), detail=True)
        self._activity_label = f"Checking {self._produces} ({name})"
        if self._attempt is not None:
            self._attempt.stage = f"checking {safe_text(name, limit=256)}"
            self._display.start_activity(self._live_label)
        else:
            self.start_activity(self._activity_label, announce=False)

    def _checks_heading(self) -> None:
        if not self._checks_heading_shown:
            self._checks_heading_shown = True
            self._write("Checks:", severity="heading", detail=True)

    def _check_finished(self, event: RunEvent) -> None:
        observation = event.data.get("observation")
        if not isinstance(observation, CheckObservation):
            raise TypeError("check_finished requires a CheckObservation.")
        if self._check_evidence is not None and self._check_evidence.name != observation.name:
            self._flush_check_evidence(completion_observed=False)
        status, severity = {
            0: ("PASS", "success"),
            1: ("FAIL", "error"),
        }.get(observation.normalized, ("INCOMPLETE", "warning"))
        raw = observation.process.returncode
        raw_text = "no exit status" if raw is None else f"raw exit {raw}"
        summary = f"{status} {observation.name}"
        # Inside an attempt the verdict is folded into the attempt line.
        self._write(
            summary, severity=severity, accent=summary, indent=4, detail=self._attempt is not None
        )
        self._write(f"Check {observation.name}: {raw_text}", severity="detail", detail=True)
        if self._attempt is not None:
            self._attempt.checks.append(
                (safe_text(observation.name, limit=256), observation.normalized)
            )
        if observation.normalized != 0:
            if observation.normalized == 2:
                reason = self._incomplete_check_reason(observation)
                if self._attempt is not None:
                    self._attempt.notes.append(
                        (safe_text(observation.name, limit=256), safe_text(reason))
                    )
                self._write(
                    f"apmx: check '{observation.name}': {reason}",
                    detail=self._attempt is not None,
                    display=_DisplayLine(
                        safe_text(reason),
                        level=_Level.DETAIL,
                        layout=_Layout.PROSE,
                    ),
                )
            self._write(
                f"apmx: check '{observation.name}': {observation.reason}",
                severity="detail",
                detail=True,
            )
        if observation.normalized != 0:
            self._flush_check_evidence(completion_observed=True)
        else:
            self._check_evidence = None

    def _flush_check_evidence(self, *, completion_observed: bool) -> None:
        """Close live output without replaying already-displayed checker lines."""
        evidence, self._check_evidence = self._check_evidence, None
        if evidence is None or not evidence.total_lines:
            return
        name = safe_text(evidence.name, limit=256)
        if not completion_observed:
            self._display.emit(
                _DisplayLine(f"Check {name}: completion was not observed.", level=_Level.DETAIL)
            )

    @staticmethod
    def _incomplete_check_reason(observation: CheckObservation) -> str:
        """Explain observed process facts without inventing checker testimony."""
        process = observation.process
        if process.error:
            return process.error
        if process.stop_reason:
            return {
                "timeout": "The check exceeded its time limit.",
                "attempt_deadline": "The run exceeded its time limit.",
                "cancelled": "The check was interrupted.",
            }.get(process.stop_reason, f"The check stopped ({process.stop_reason}).")
        if not process.cleanup_confirmed:
            return "Process cleanup could not be confirmed."
        if process.returncode is None:
            return "No exit status was observed."
        if process.returncode < 0:
            return f"The check was terminated by signal {-process.returncode}."
        if process.returncode == 0:
            return observation.reason
        return f"The check exited with status {process.returncode}."

    def _heartbeat(self, event: RunEvent) -> None:
        elapsed = event.data.get("elapsed_seconds", event.elapsed_seconds)
        if not isinstance(elapsed, (int, float)):
            return
        message = f"{self._activity_label} -- still running; {elapsed:.0f}s elapsed."
        if elapsed - self._last_evidence_activity >= HEARTBEAT_SECONDS:
            self._write(message, retained_only=True)
            self._last_evidence_activity = elapsed
        if self._display.status is not None:
            return
        attempt = self._attempt
        if not self.verbose and attempt is not None:
            # Append-only output: a sparse, completed liveness line instead of animation.
            if elapsed - attempt.liveness >= LIVENESS_SECONDS:
                attempt.liveness = elapsed
                self._show(
                    _DisplayLine(
                        f"still running {_seconds(elapsed)}: {self._live_status(attempt)}",
                        role=_Role.DETAIL,
                        level=BLOCK + 2,
                        layout=_Layout.PROSE,
                    )
                )
            return
        # Hidden metadata/tool/stdout events must not starve the human heartbeat.
        # This display-only clock cannot change transcript bytes or record hashes.
        if elapsed - self._last_human_activity >= HEARTBEAT_SECONDS:
            self._display.emit(
                _DisplayLine(safe_text(message), level=self._block_offset(_Level.BODY) + 2)
            )
            self._last_human_activity = elapsed

    def _live_status(self, attempt: _AttemptView) -> str:
        if attempt.stage == "agent":
            return attempt.status or "working"
        return attempt.stage

    def _live_label(self) -> str:
        """In-place attempt line on an interactive terminal; recomputed per refresh."""
        attempt = self._attempt
        if attempt is None:
            return safe_text(self._activity_label)
        text = f"attempt {attempt.index}/{attempt.count}"
        if attempt.agent_clock is not None:
            agent = (
                attempt.agent_seconds
                if attempt.agent_seconds is not None
                else _clock() - attempt.agent_clock
            )
            text += f"  agent {_seconds(agent)}"
        return f"{text}   {self._live_status(attempt)}"

    def _summarize_attempt(self) -> None:
        """One completed line per attempt: the loop and its per-check verdicts."""
        attempt = self._attempt
        if attempt is None or attempt.summarized:
            return
        attempt.summarized = True
        text = f"attempt {attempt.index}/{attempt.count}"
        if attempt.agent_seconds is not None:
            text += f"  agent {_seconds(attempt.agent_seconds)}"
        if attempt.checks:
            marks = {0: "[+]", 1: "[x]"}
            text += "   checks: " + " ".join(
                f"{marks.get(normalized, '[!]')} {name}" for name, normalized in attempt.checks
            )
        else:
            text += "   checks: not run"
        self._show(_DisplayLine(text, level=BLOCK, layout=_Layout.PROSE))

    def _emit_tails(self, attempt: _AttemptView | None) -> None:
        """Explain a failed attempt with the last lines of its failing checks."""
        if attempt is None or self.verbose:
            return
        for name, normalized in attempt.checks:
            if normalized == 0:
                continue
            for line in attempt.tails.get(name, ()):
                self._show(_DisplayLine(f"{name}: {line}", role=_Role.DETAIL, level=BLOCK + 2))
        for name, reason in attempt.notes:
            self._show(
                _DisplayLine(
                    f"{name}: {reason}", level=BLOCK + 2, layout=_Layout.PROSE, hang=len(name) + 2
                )
            )

    @staticmethod
    def _flow(needs: tuple[str, ...], produces: str) -> str:
        return ("needs " + ", ".join(needs) + " -> " if needs else "") + f"produces {produces}"

    def _block_header(
        self,
        name: str,
        needs: tuple[str, ...],
        produces: str,
        budget: RepairBudget | None,
        *,
        order: str = "",
        width: int = 0,
    ) -> _DisplayLine:
        title = f"{order} {name}" if order else name
        text = title.ljust(max(width, len(title)) + 3) + self._flow(needs, produces)
        if budget is not None:
            text += f"   budget {_plural(budget.max_attempts, 'attempt')}"
        return _DisplayLine(
            text,
            role=_Role.TITLE,
            accent=title,
            level=0,
            layout=_Layout.PROSE,
            hang=BLOCK if order else len(title) + 3,
        )

    def _outputs_line(self, result: RunResult) -> str:
        return ", ".join(item.relative_path for item in artifact_files(result.artifact))

    def _result(self, event: RunEvent) -> None:
        if self._finished:
            return
        result = event.data.get("result")
        if not isinstance(result, RunResult):
            raise TypeError("finished requires a recorded RunResult.")
        self._finished = True
        self.stop_activity()
        self._flush_check_evidence(completion_observed=False)
        self._summarize_attempt()
        factory = self._factory if self._step is not None else None
        node = factory.nodes.get(self._step.contract) if factory and self._step else None
        if result.outcome is Outcome.COMPLETE:
            outputs = self._outputs_line(result)
            if node is not None:
                node.complete = True
            if factory is not None and node is not None and node.consumers:
                # Shown only after the chain owner admits the handoff (next chain_node).
                factory.pending = (outputs, node.consumers)
            elif outputs:
                self._show(
                    _DisplayLine(outputs, role=_Role.SUCCESS, level=BLOCK, layout=_Layout.PROSE)
                )
        else:
            self._emit_tails(self._leaf.last)
            if factory is not None:
                factory.failed = (self._leaf.name or "contract", result, self._leaf)
        if self._step is None:
            self._final_leaf(result)
        files = artifact_files(result.artifact)
        self._write(
            f"Record: {self._path(result.run_directory / 'record.json')}",
            severity="detail",
            detail=True,
        )
        for item in files:
            self._write(f"Artifact: {self._path(item.path)}", severity="detail", detail=True)
        for model in result.observed_models:
            if model not in self._display.models_shown:
                self._write(f"Observed execution model: {model}", severity="detail", detail=True)
                self._display.models_shown.add(model)
        if not result.observed_models:
            self._write("Observed execution model: unknown", severity="detail", detail=True)
        if result.stop_reason:
            self._write(f"Stop reason: {result.stop_reason}", severity="detail", detail=True)
        self._write(
            f"Logs: {self._path(result.run_directory / 'transcript.log')}",
            severity="detail",
            detail=True,
        )
        self._write(
            "Logs may contain sensitive data. Review before sharing.",
            severity="detail",
            detail=True,
        )

    def _headline(self, outcome: Outcome, text: str) -> None:
        self._display.gap()
        self._show(
            _DisplayLine(
                f"{outcome.name}   {text}",
                role=self._display.outcome_role(outcome),
                accent=outcome.name,
                level=0,
                layout=_Layout.PROSE,
            )
        )

    def _labelled(self, label: str, lines: list[str], *, prose: bool = False) -> None:
        """Aligned final-block rows; paths stay literal so they remain copyable."""
        for index, text in enumerate(lines):
            head = label.ljust(10) if index == 0 else " " * 10
            self._show(
                _DisplayLine(
                    head + text,
                    accent=label if index == 0 else "",
                    level=0,
                    layout=_Layout.PROSE if prose and index else _Layout.LITERAL,
                    hang=10,
                )
            )

    def _directory(self, path: Path) -> str:
        return self._path(path).rstrip("/") + "/"

    def _final_leaf(self, result: RunResult) -> None:
        """Final block for a single contract: outcome, then Outputs or Saved/Next."""
        elapsed = _seconds(_clock() - self._display.started)
        if result.outcome is Outcome.COMPLETE:
            checks = sum(check.normalized == 0 for check in result.checks)
            self._headline(
                result.outcome,
                f"1/1 contract   {checks}/{len(result.checks)} "
                f"{'check' if len(result.checks) == 1 else 'checks'}   {elapsed}",
            )
            self._outputs(result.run_directory / "artifacts", artifact_files(result.artifact))
            return
        self._headline(
            result.outcome,
            f"{self._failure_summary(self._leaf.name or 'contract', result, self._leaf)}   "
            f"exit {int(result.outcome)}",
        )
        self._saved_and_next(self._saved_directory(result), result)

    def _outputs(self, directory: Path, files: tuple) -> None:
        if not files:
            return
        self._display.gap()
        names = [item.relative_path for item in files]
        self._labelled("Outputs", [self._directory(directory), "  ".join(names)], prose=True)

    @staticmethod
    def _saved_directory(result: RunResult) -> Path:
        return result.run_directory

    def _failure_summary(self, name: str, result: RunResult, leaf: _LeafView) -> str:
        """Name what failed using recorded checks; never re-derive the outcome."""
        attempt = leaf.last
        if result.outcome is Outcome.REJECTED:
            failed = [check.name for check in result.checks if check.normalized == 1]
            text = (
                f"{name} failed {'check' if len(failed) == 1 else 'checks'} " + ", ".join(failed)
                if failed
                else f"{name} was rejected"
            )
            if attempt is not None:
                text += (
                    f" after {attempt.index}/{attempt.count} "
                    f"{'attempt' if attempt.count == 1 else 'attempts'}"
                )
            stop = {
                "no_progress": "same output again",
                "not_retryable": "not retryable",
            }.get(leaf.repair_stop)
            return text + (f" ({stop})" if stop else "")
        reason, _ = self._result_explanation(result)
        return f"{name}: {reason.rstrip('.')}"

    def _saved_and_next(self, saved: Path | None, result: RunResult | None) -> None:
        self._display.gap()
        if saved is not None:
            self._labelled("Saved", [f"{self._directory(saved)}   (attempt files + logs)"])
        if result is not None and result.outcome is Outcome.REJECTED:
            action = "Fix the contract or check, then rerun:"
        elif result is not None:
            action = self._result_explanation(result)[1]
            action = action.removesuffix(" before retrying.").removesuffix(" before rerunning.")
            action = f"{action.rstrip('.')}, then rerun:"
        else:
            action = "Resolve the reported problem, then rerun:"
        rows = [(action, self._invocation or "")]
        if self._invocation and not self.verbose:
            rows.append(("More detail:", f"{self._invocation} --verbose"))
        width = max(len(label) for label, _ in rows)
        self._labelled(
            "Next",
            [f"{label.ljust(width)}  {command}".rstrip() for label, command in rows],
        )

    def _result_explanation(self, result: RunResult) -> tuple[str, str]:
        """Explain the recorded outcome; never promote or downgrade it here."""
        if result.outcome == Outcome.REJECTED:
            return (
                "Contract checks found a problem.",
                "Review the failed checks and saved output before retrying.",
            )
        if result.outcome == Outcome.UNPROVEN:
            if result.artifact is None:
                return (
                    "The declared output could not be checked.",
                    f"Review the contract output path and {self._harness} diagnostics"
                    + " before retrying.",
                )
            return (
                "Checks could not establish a result.",
                "Review incomplete checks and their prerequisites before retrying.",
            )
        if result.outcome == Outcome.COMPLETE:
            return "", ""
        return {
            "cancelled": ("Run interrupted.", "Review any saved output before rerunning."),
            "producer_failed": (
                f"{self._harness} did not complete successfully.",
                f"Review {self._harness} diagnostics and logs before retrying.",
            ),
            "native_reported_failure": (
                f"{self._harness} reported a failure.",
                f"Review {self._harness} diagnostics and logs before retrying.",
            ),
            "native_protocol_error": (
                f"{self._harness} output could not be interpreted.",
                f"Review {self._harness} diagnostics and logs before retrying.",
            ),
            "native_completion_unobserved": (
                f"{self._harness} completion was not observed.",
                f"Review {self._harness} diagnostics and logs before retrying.",
            ),
            "attempt_deadline": (
                "The run exceeded its time limit.",
                "Review the contract workload before retrying.",
            ),
            "timeout": (
                "The process exceeded its time limit.",
                "Review the contract workload before retrying.",
            ),
            "producer_stop_unconfirmed": (
                f"{self._harness} may still be running.",
                "Inspect the reported process before retrying.",
            ),
            "checker_stop_unconfirmed": (
                "A check may still be running.",
                "Inspect the reported process before retrying.",
            ),
        }.get(
            result.stop_reason or "",
            (
                "The run stopped before it could finish.",
                "Resolve the reported error before retrying.",
            ),
        )

    def render_plan(self, plan: LeafPlan, inventory: tuple[FileEntry, ...]) -> None:
        """Show the admitted surface without printing source bodies or prompts."""
        self._harness = self._harness_label(plan.harness)
        relative = plan.source.contract_relative_path if plan.source else ""
        source = (
            (plan.source.original_root or plan.source.root) / relative
            if plan.source
            else plan.contract.path
        )
        identity = self._job_identity(source, relative)
        self._write(
            f"Preview: {identity} -> {plan.contract.output_label}", severity="heading", indent=0
        )
        self._write(f"{self._harness} / {plan.model or 'default model'}", severity="detail")
        self._write("Nothing will execute or download.")
        for name in plan.contract.needs:
            self._write(f"Input: {name}")
        self._write("Checks: " + ", ".join(check.name for check in plan.contract.checks))
        for skill in plan.imported_skills:
            self._write(f"Imported skill: {skill.name}")
        self._write(
            f"Time limits: run {plan.limits.attempt_seconds:g}s; "
            f"each check {plan.limits.check_seconds:g}s"
        )
        self.repair_budget(plan.contract.budget, preview=True)
        self._write("Complete execution returns COMPLETE (0); this does not certify isolation.")
        self._write("To run, use apmx with --allow-host-access and without --plan.")
        self._write(f"Source: {self._path(source)}", severity="detail", detail=True)
        if plan.source and plan.source.package_ref:
            self._write(f"Package: {plan.source.package_ref}", severity="detail", detail=True)
        self._write(
            f"Requested model: {plan.model or 'default model'}", severity="detail", detail=True
        )
        self._write(f"Native executable: {plan.executable}", severity="detail", detail=True)
        self._write(
            f"Baseline: {len(inventory)} files, {sum(item.size for item in inventory)} bytes",
            severity="detail",
            detail=True,
        )
        for check in plan.contract.checks:
            self._write(f"Check {check.name}: {check.command}", severity="detail", detail=True)
        for skill in plan.imported_skills:
            identity = f"Source identity: {skill.lock_identity}; {skill.assurance}"
            if skill.assurance == "observed-local-source":
                identity += ", not a cryptographic pin"
            self._write(identity, severity="detail", detail=True)
            self._write(
                f"Observed source SHA-256: {skill.source_digest}", severity="detail", detail=True
            )
            if skill.resolved_commit:
                self._write(
                    f"Resolved commit: {skill.resolved_commit}", severity="detail", detail=True
                )
        self._write(f"Policy: {plan.policy_status}", severity="detail", detail=True)

    def render_error(self, error: ContractError) -> None:
        """Render a pre-admission refusal without inventing a run or a success."""
        self.stop_activity()
        self._flush_check_evidence(completion_observed=False)
        self._display.gap()
        headline = f"apmx: {error.outcome.name}"
        self._write(
            headline,
            severity=self._display.outcome_role(error.outcome),
            accent=headline,
            indent=0,
        )
        self._write(str(error))
        self._write(f"Reason: {error.code}", severity="detail", detail=True)
        if error.location is not None:
            self._write(
                f"Source: {self._path(error.location.path)}:{error.location.line}:{error.location.column}"
            )

    def new_leaf(self, *, index: int, count: int, contract: Path) -> ContractLogger:
        """Share only the screen; each leaf gets immutable context and private evidence."""
        leaf = ContractLogger(
            verbose=self.verbose,
            _display=self._display,
            _step=_StepContext(index, count, contract),
        )
        leaf._display_root = self._display_root
        leaf._invocation = self._invocation
        leaf._factory = self._factory
        node = self._factory.nodes.get(contract) if self._factory else None
        if node is not None:
            leaf._leaf = node.leaf
        return leaf

    def new_attempt(self, *, index: int, count: int) -> ContractLogger:
        """Give each attempt a private transcript while retaining the factory step."""
        self._write(f"Attempt {index} of {count}", severity="info", detail=True)
        attempt = ContractLogger(verbose=self.verbose, _display=self._display, _step=self._step)
        attempt._display_root = self._display_root
        attempt._invocation = self._invocation
        attempt._factory = self._factory
        attempt._leaf = self._leaf
        attempt._attempt = _AttemptView(index, count)
        return attempt

    def repair_budget(self, budget: RepairBudget | None, *, preview: bool = False) -> None:
        """Disclose authored execution bounds; a run shows them in the contract header."""
        if budget is not None:
            if not preview:
                self._leaf.budget = budget
            self._write(
                f"Attempts: up to {budget.max_attempts} "
                f"{'attempt' if budget.max_attempts == 1 else 'attempts'}, "
                f"{budget.max_seconds:g}s total for execution and checks. "
                "Only rejected outputs can be retried; this is not a model spending limit.",
                detail=not preview,
            )

    def repair_finished(self, *, reason: str, record: Path, attempts: int) -> None:
        """Expose the durable link to all attempts, without another outcome decision."""
        self._write(
            {
                "complete": f"Accepted on attempt {attempts}.",
                "not_retryable": "Stopped: this failure cannot be retried automatically.",
                "no_progress": "Stopped: another attempt produced the same rejected output.",
                "max_attempts": f"Stopped after {attempts} attempts: the output is still rejected.",
            }[reason],
            detail=True,
        )
        self._leaf.repair_stop = reason
        self._write(f"Attempts record: {self._path(record)}", detail=True)

    def select_factory_root(self, root: Path) -> None:
        self._display_root = self._caller_root
        self._caller_root = root

    def can_confirm_factory(self) -> bool:
        """The prompt uses stdout; both it and stdin must be interactive."""
        return console.can_confirm_factory()

    @staticmethod
    def _harness_label(name: str) -> str:
        return {"copilot": "Copilot", "opencode": "OpenCode"}.get(name, name)

    def render_factory_work(
        self, graph: Graph, *, project_root: Path, harness: str = "copilot"
    ) -> None:
        self.stop_activity()
        self._harness = self._harness_label(harness)
        count = self._factory_contract_count = len(graph.order)
        artifacts = sum(len(contract.outputs) for contract in graph.order)
        checks = sum(len(contract.checks) for contract in graph.order)
        self._write(f"Factory: {self._path(graph.root)}", severity="heading", indent=0)
        self._write(
            f"{count} {'contract' if count == 1 else 'contracts'} / "
            f"{artifacts} {'artifact' if artifacts == 1 else 'artifacts'} / "
            f"{checks} planned {'check' if checks == 1 else 'checks'}",
            layout=_Layout.PROSE,
        )
        catalog = tuple(contract.path for contract in graph.order)
        for index, contract in enumerate(graph.order, start=1):
            self._preview_node(index, count, contract.path, catalog=catalog)
            self._write("Produces: " + ", ".join(contract.outputs))
            self._write("Checks: " + ", ".join(check.name for check in contract.checks))
            self.repair_budget(contract.budget, preview=True)
            self._write(f"Source: {self._path(contract.path)}", severity="detail", detail=True)
            for value in contract.needs:
                kind = (
                    "from an earlier step" if value in graph.inputs(contract) else "starting file"
                )
                self._write(f"Input: {value} ({kind})")
            for check in contract.checks:
                self._write(f"Check {check.name}: {check.command}", severity="detail", detail=True)
        self._display.gap()
        self._write(
            f"Evidence: will be saved under {self._path(project_root / '.apm')}/",
            indent=0,
        )
        self._write(
            "Required checks must pass before dependent work starts.",
            indent=0,
            layout=_Layout.PROSE,
        )
        self._display.gap()

    def confirm_factory(self) -> bool:
        """Ask once, default NO, without conflating EOF with interruption."""
        self.execution_context(factory=True)
        subject = (
            "this contract"
            if self._factory_contract_count == 1
            else f"these {self._factory_contract_count} contracts"
        )
        return self._confirm(f"Run {subject} with {self._harness}? [y/N]")

    def confirm_package_preparation(self, package_ref: str) -> bool:
        """Authorize acquisition only; factory execution still needs its own consent."""
        self._write(f"Factory source: {package_ref}", severity="heading", indent=0)
        self._write(
            "APMX will load the factory and use APM to install its dependencies in a temporary "
            "workspace. This can use host files, download packages and use available logins. "
            "No agent or check runs yet; you will inspect "
            "the steps and approve execution separately. Load only factories you trust.",
            layout=_Layout.PROSE,
            indent=0,
        )
        return self._confirm("Load this factory and install its dependencies? [y/N]")

    def _confirm(self, prompt: str) -> bool:
        self._write(prompt, accent=prompt, indent=0)
        if not self._display.enabled:
            return False
        try:
            answer = sys.stdin.readline(32)
        except EOFError:
            return False
        return answer.endswith("\n") and answer.strip().casefold() in {"y", "yes"}

    def _contract_identity(self, contract: Path, catalog: tuple[Path, ...]) -> str:
        if (
            catalog
            and sum(path.name.casefold() == contract.name.casefold() for path in catalog) == 1
        ):
            identity = contract.name
        else:
            try:
                identity = contract.relative_to(self._caller_root).as_posix()
            except ValueError:
                identity = self._path(contract)
        return identity.removesuffix(".contract.md")

    def _preview_node(
        self, index: int, count: int, contract: Path, *, catalog: tuple[Path, ...] = ()
    ) -> None:
        self._display.gap()
        identity = self._contract_identity(contract, catalog)
        self._write(f"Contract {index}/{count}: {identity}", severity="heading", indent=0)

    def factory_started(self, plan: ChainPlan) -> None:
        """Show the admitted graph once: name, size and agent, before any contract runs."""
        catalog = tuple(node.plan.contract.path for node in plan.nodes)
        names = {path: self._contract_identity(path, catalog) for path in catalog}
        nodes: dict[Path, _FactoryNode] = {}
        for index, node in enumerate(plan.nodes, start=1):
            contract = node.plan.contract
            producers = tuple(
                dict.fromkeys(e.producer for e in plan.graph.edges if e.consumer == contract.path)
            )
            consumers = tuple(
                dict.fromkeys(e.consumer for e in plan.graph.edges if e.producer == contract.path)
            )
            nodes[contract.path] = _FactoryNode(
                index,
                names[contract.path],
                contract.needs,
                contract.outputs,
                contract.budget,
                producers,
                consumers,
                _LeafView(name=names[contract.path]),
            )
        root = plan.graph.root
        source = plan.nodes[0].plan.source
        label = source.package_ref if source and source.package_ref else root.name
        order = len(f"[{len(nodes)}/{len(nodes)}]")
        self._factory = _FactoryView(
            safe_text(label or self._path(root), limit=256),
            nodes,
            order + 1 + max((len(name) for name in names.values()), default=0),
        )
        first = plan.nodes[0].plan
        self._harness = self._harness_label(first.harness)
        title = f"Factory  {self._factory.label}"
        self._display.gap()
        self._show(
            _DisplayLine(
                f"{title}   {_plural(len(nodes), 'contract')}   "
                f"{first.harness} / {first.model or 'default model'}",
                role=_Role.TITLE,
                accent=title,
                level=0,
                layout=_Layout.PROSE,
                hang=len("Factory  "),
            )
        )

    def _flush_handoff(self, *, admitted: bool) -> None:
        """Producer-side handoff line, claimed only after the chain owner admitted it."""
        factory = self._factory
        if factory is None or factory.pending is None:
            return
        outputs, consumers = factory.pending
        factory.pending = None
        names = ", ".join(factory.nodes[path].name for path in consumers if path in factory.nodes)
        text = outputs + (f" -> handed to {names}" if admitted and names else "")
        self._show(_DisplayLine(text, role=_Role.SUCCESS, level=BLOCK, layout=_Layout.PROSE))

    def chain_node(
        self, index: int, count: int, contract: Path, *, catalog: tuple[Path, ...] = ()
    ) -> None:
        self._flush_handoff(admitted=True)
        self._display.gap()
        identity = self._contract_identity(contract, catalog)
        self._write(
            f"Contract {index}/{count}: {identity}",
            severity="heading",
            indent=0,
            retained_only=True,
        )
        node = self._factory.nodes.get(contract) if self._factory else None
        if node is None:
            self._show(
                _DisplayLine(
                    f"[{index}/{count}] {identity}",
                    role=_Role.TITLE,
                    accent=f"[{index}/{count}] {identity}",
                    level=0,
                )
            )
            return
        node.started = True
        node.leaf.header_shown = True
        self._show(
            self._block_header(
                node.name,
                node.needs,
                ", ".join(node.outputs),
                node.budget,
                order=f"[{index}/{count}]",
                width=self._factory.width,
            )
        )

    def execution_context(self, *, factory: bool = False) -> None:
        """Disclose the invocation's local execution profile before consent or action."""
        if self._display.disclosure_shown:
            return
        self._display.disclosure_shown = True
        self._write("Execution: local (not sandboxed)", severity="notice", indent=0)
        if factory:
            self._write(
                "Agents and checks can access host files, network and available logins.",
                layout=_Layout.PROSE,
            )
            self._write(
                "Package dependencies may be installed; model usage may cost money.",
                layout=_Layout.PROSE,
            )
            self._write("Run only contracts you trust.")
        else:
            self._write(
                "Agents and checks can use host files, network and available logins.",
                layout=_Layout.PROSE,
            )
            self._write(
                "Model usage may cost money. Run only contracts you trust.", layout=_Layout.PROSE
            )
        self._display.gap()

    def render_chain_plan(self, plan: ChainPlan) -> None:
        self.render_factory_work(
            plan.graph,
            project_root=plan.nodes[0].plan.project_root,
            harness=plan.nodes[0].plan.harness,
        )
        self._write(f"{plan.nodes[0].plan.harness} / {plan.nodes[0].plan.model or 'default model'}")
        self._write("Nothing will execute or download. Dependency resolution uses no model calls.")
        policy = (
            "fully checked local outputs (--allow-unproven-inputs); assurance remains unproven"
            if plan.allow_unproven_inputs
            else "strict VERIFIED-only; native outputs block"
        )
        self._write(f"Handoff policy: {policy}")
        self._write("Every run starts fresh; APMX does not cap model charges.")
        self._write(
            f"Limits: {plan.nodes[0].plan.limits.chain_contracts} discovered contracts; "
            + (
                "retries only within the explicit per-contract budgets above."
                if any(node.plan.contract.budget is not None for node in plan.nodes)
                else "one attempt per step, no retries."
            )
        )
        self._write(
            "Without --plan or consent flags, an interactive factory invocation asks for "
            "confirmation before execution."
        )
        if not plan.allow_unproven_inputs:
            self._write(
                "For trusted local automation, explicitly add --allow-host-access and "
                "--allow-unproven-inputs to permit fully checked native handoffs. "
                "COMPLETE does not certify isolation."
            )

    def chain_stopped(self, reason: str, *, outcome: Outcome, code: str) -> None:
        self._chain_stop = _StopContext(outcome, reason, code)
        self.stop_activity()
        self._write(reason, severity="warning", indent=0, retained_only=True)
        factory = self._factory
        if factory is None:
            self._display.gap()
            self._show(
                _DisplayLine(
                    reason,
                    role=self._display.outcome_role(outcome),
                    level=0,
                    layout=_Layout.PROSE,
                )
            )
            return
        self._flush_handoff(admitted=False)
        count = len(factory.nodes)
        for node in factory.nodes.values():
            if node.started:
                continue
            waiting = [
                factory.nodes[path].name
                for path in node.producers
                if path in factory.nodes and not factory.nodes[path].complete
            ]
            order = f"[{node.index}/{count}]"
            title = f"{order} {node.name}"
            self._display.gap()
            self._show(
                _DisplayLine(
                    title.ljust(factory.width + 3)
                    + (
                        "not started: waits on " + ", ".join(waiting)
                        if waiting
                        else "not started: factory stopped"
                    ),
                    role=_Role.TITLE,
                    accent=title,
                    dim_remainder=True,
                    level=0,
                    layout=_Layout.PROSE,
                    hang=BLOCK,
                )
            )

    def render_chain_result(self, result: ChainResult) -> None:
        self.stop_activity()
        self._flush_handoff(admitted=result.complete)
        elapsed = _seconds(_clock() - self._display.started)
        directory = result.record_path.parent
        if result.complete:
            completed = len(result.runs)
            checks = sum(len(run.checks) for run in result.runs)
            passed = sum(check.normalized == 0 for run in result.runs for check in run.checks)
            self._headline(
                result.outcome,
                f"{completed}/{completed} {'contract' if completed == 1 else 'contracts'}   "
                f"{passed}/{checks} {'check' if checks == 1 else 'checks'}   {elapsed}",
            )
            files = tuple(item for run in result.runs for item in artifact_files(run.artifact))
            self._outputs(directory / "artifacts", files)
        else:
            factory = self._factory
            failed = factory.failed if factory is not None else None
            context = self._chain_stop
            if failed is not None:
                name, run, leaf = failed
                summary = self._failure_summary(name, run, leaf)
                saved: Path | None = self._saved_directory(run)
                explained: RunResult | None = run
            else:
                summary = (
                    context.reason
                    if context is not None and context.code == result.stop_reason
                    else "Factory stopped before all selected steps completed."
                ).rstrip(".")
                saved = directory
                explained = None
            self._headline(result.outcome, f"{summary}   exit {int(result.outcome)}")
            self._saved_and_next(saved, explained)
            self._write(
                f"Stop reason: {result.stop_reason}", severity="detail", detail=True, indent=0
            )
        self._write(f"Record: {self._path(result.record_path)}", severity="detail", detail=True)

    def render_result(self, result: ChainResult | RunResult) -> None:
        """Render only after command preparation contexts have finalized."""
        if isinstance(result, ChainResult):
            self.render_chain_result(result)
        else:
            self._result(RunEvent(result.run_id, 0, 0, "finished", "engine", {"result": result}))

    @staticmethod
    def _receipt_facts(path: Path) -> list[str]:
        """Name only the standards whose documents the export actually wrote."""
        facts = []
        try:
            provenance = json.loads((path / "provenance.intoto.json").read_bytes())
            kind = str(provenance.get("predicateType", ""))
            facts.append(
                "provenance  in-toto" + (" + SLSA v1" if "slsa.dev/provenance/v1" in kind else "")
            )
        except (OSError, ValueError, AttributeError):
            pass
        checks = (
            sorted((path / "checks").glob("*.intoto.json")) if (path / "checks").is_dir() else []
        )
        if checks:
            facts.append(f"checks      in-toto test-result ({len(checks)})")
        try:
            inventory = json.loads((path / "abom.cdx.json").read_bytes())
            version = inventory.get("specVersion")
            components = inventory.get("components", [])
            text = "inventory   CycloneDX" + (f" {version}" if isinstance(version, str) else "")
            if isinstance(components, list):
                text += f" ({_plural(len(components), 'component')})"
            facts.append(text)
        except (OSError, ValueError, AttributeError):
            pass
        return [safe_text(item) for item in facts]

    @staticmethod
    def _audit_available() -> bool:
        """Only suggest `apmx audit` once this build ships the verifier."""
        return importlib.util.find_spec("apmx.audit") is not None

    def evidence_package(self, path: Path | None) -> None:
        """Report a delivered standards receipt without changing recorded execution."""
        if path is None:
            self._write(
                "Receipt: not exported.",
                severity="detail",
                detail=True,
                indent=0,
            )
            return
        self._labelled(
            "Receipt",
            [
                self._directory(path),
                *self._receipt_facts(path),
                "unsigned: binds content, not identity; review before sharing",
            ],
            prose=True,
        )
        self._display.gap()
        if self._audit_available():
            self._labelled("Next", [f"apmx audit {shlex.quote(self._path(path))}"])
        elif (path / "summary.md").is_file():
            self._labelled("Next", [f"read {self._path(path / 'summary.md')}"])
        for label, name in (
            ("Summary", "summary.md"),
            ("Factory definition", "definition.json"),
            ("Production (in-toto / SLSA v1)", "provenance.intoto.json"),
            ("Dependency ABOM (CycloneDX 1.5)", "abom.cdx.json"),
            ("Check results (in-toto)", "checks"),
            ("SHA-256 file index", "index.json"),
        ):
            self._write(
                f"{label}: {self._path(path / name)}", severity="detail", detail=True, indent=0
            )

    def evidence_delivery_failed(self, reason: str) -> None:
        """A failed export cannot relabel an already finalized COMPLETE record."""
        self._display.gap()
        self._write("Receipt export failed (command exit 23).", severity="error", indent=0)
        self._write(reason)
        self._write(
            "Recorded execution remains COMPLETE; export did not rewrite its record or outputs."
        )
        self._write(
            "Inspect the saved run, then retry the read-only export described in "
            "docs/evidence.md; no model rerun is required."
        )
