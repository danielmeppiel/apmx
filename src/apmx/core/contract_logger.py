"""Line-oriented presentation for contract plans and run observations."""

from __future__ import annotations

import os
import re
import sys
from collections import deque
from dataclasses import dataclass, replace
from enum import Enum, IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, ClassVar

from apmx.contracts import records
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
    RunEvent,
    RunResult,
    artifact_files,
)
from apmx.contracts.stream import safe_text
from apmx.utils import console
from apmx.utils.paths import portable_link_relpath, portable_relpath

if TYPE_CHECKING:
    from rich.status import Status

    from apmx.contracts.resolution import ChainPlan, Graph


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
    level: _Level = _Level.BODY
    accent: str = ""
    layout: _Layout = _Layout.LITERAL
    dim_remainder: bool = False


@dataclass(frozen=True)
class _RoleStyle:
    symbol: str
    color: str


class _ContractDisplay:
    """Invocation-scoped screen state only; never reads or derives run evidence."""

    _roles: ClassVar[dict[_Role, _RoleStyle]] = {
        _Role.START: _RoleStyle("running", "cyan"),
        _Role.INFO: _RoleStyle("", "default"),
        _Role.NOTICE: _RoleStyle("info", "blue"),
        _Role.HEADING: _RoleStyle("", "default"),
        _Role.WARNING: _RoleStyle("warning", "yellow"),
        _Role.ERROR: _RoleStyle("error", "red"),
        _Role.SUCCESS: _RoleStyle("check", "green"),
        _Role.DETAIL: _RoleStyle("", "dim"),
        _Role.EXTERNAL: _RoleStyle("", "dim cyan"),
    }
    _outcomes: ClassVar[dict[Outcome, _Role]] = {
        Outcome.VERIFIED: _Role.SUCCESS,
        Outcome.UNPROVEN: _Role.WARNING,
        Outcome.REJECTED: _Role.ERROR,
        Outcome.HALTED: _Role.ERROR,
    }

    def __init__(self) -> None:
        self.enabled = True
        self.status: Status | None = None
        self.revision = 0
        self._boundary = _Boundary.EMPTY

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
                hanging_indent=len(prefix) if line.layout is _Layout.PROSE else None,
                capabilities=capabilities,
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

    def start_activity(self, message: str) -> None:
        if not self.animates():
            self.stop_activity()
            return
        from rich.text import Text

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
        self._produces = "saved output"
        self._activity_label = "Working"
        self._checks_heading_shown = False
        self._preparation_notice_shown = False
        self._apm_paths: tuple[tuple[str, str], ...] = ()
        # Message identities are admitted by the decoder's bounded correlation table.
        self._prose: dict[int, _ProseDisplay] = {}

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
    ) -> None:
        """Retain the canonical logical line before any human-only transformation."""
        text = safe_text(message)
        source = safe_text(attribution, limit=256) if attribution else ""
        role = _Role(severity)
        marker = self._display.marker(role, source)
        if not self._closed:
            retained_prefix = f"{source} (untrusted) > " if source else ""
            self._transcript.append(" " * indent + marker + retained_prefix + text)
        visibility = (
            _Visibility.RETAINED
            if retained_only
            else _Visibility.VERBOSE
            if detail
            else _Visibility.ALWAYS
        )
        self._display.emit(
            display
            if display is not None
            else _DisplayLine(
                safe_text(display_message) if display_message is not None else text,
                role=role,
                source=source,
                level=_Level.DETAIL if role is _Role.DETAIL else _Level(indent),
                accent=safe_text(accent),
                layout=layout,
                dim_remainder=dim_remainder,
            ),
            visibility=visibility,
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
            "phase": self._phase,
            "activity": self._activity,
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
        self._write(
            f"Job: {identity} -> {self._produces}",
            severity="heading",
            indent=0,
            display=(
                _DisplayLine(safe_text(f"Output: {self._produces}"))
                if self._step is not None
                else None
            ),
        )
        self._write(f"Copilot / {model}", severity="detail")
        self._write("Running on your machine (not sandboxed).")
        self._write(f"Source: {self._path(source)}", severity="detail", detail=True)
        if package:
            self._write(f"Package: {package}", severity="detail", detail=True)
        self._write(f"Requested model: {model}", severity="detail", detail=True)
        self._write(f"Run: {event.run_id}", severity="detail", detail=True)
        self._write(
            f"Record directory: {self._field(event, 'run_directory')}",
            severity="detail",
            detail=True,
        )
        self._retained_gap()

    def _phase(self, event: RunEvent) -> None:
        phase = self._field(event, "name")
        if phase == self._last_phase:
            return
        self._last_phase = phase
        if phase == "checks":
            self._checks_heading()
        message = {
            "preflight": "Preparing files for Copilot",
            "execution": "Running Copilot",
            "capture": "Saving output",
            "checks": f"Checking {self._produces}",
            "record": "Saving results",
        }.get(phase)
        if message:
            self.start_activity(message, announce=phase != "checks")

    def _attribution(self, event: RunEvent) -> str:
        source = {"harness": "Copilot", "checker": "Check"}.get(event.source, event.source)
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
                    _DisplayLine(safe_text(f"Check {name} stdout:"), role=_Role.DETAIL),
                    visibility=_Visibility.VERBOSE,
                    verbose=self.verbose,
                )
            self._check_evidence.append(safe_text(text))
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
            detail=not stderr and (checker_stdout or bool(tool_status and tool_status != "failed")),
            layout=_Layout.PROSE if not tool_status else _Layout.LITERAL,
            display=(
                _DisplayLine(
                    safe_text(text),
                    role=_Role.DETAIL,
                    source=safe_text(source, limit=256),
                    level=_Level.DETAIL,
                )
                if checker_stdout
                else None
            ),
        )

    def _metadata(self, event: RunEvent) -> None:
        self._write(
            self._field(event, "text", ""),
            severity="detail",
            detail=True,
            attribution=self._attribution(event),
            retained_only=event.data.get("retained_only") is True,
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
        self.start_activity(
            f"Checking {self._produces} ({self._field(event, 'name')})",
            announce=False,
        )

    def _checks_heading(self) -> None:
        if not self._checks_heading_shown:
            self._checks_heading_shown = True
            self._retained_gap()
            self._write(f"apmx: checking {self._produces}", severity="heading", indent=0)

    def _check_finished(self, event: RunEvent) -> None:
        observation = event.data.get("observation")
        if not isinstance(observation, CheckObservation):
            raise TypeError("check_finished requires a CheckObservation.")
        if self._check_evidence is not None and self._check_evidence.name != observation.name:
            self._flush_check_evidence(completion_observed=False)
        status, severity = {
            0: ("passed", "success"),
            1: ("failed", "error"),
        }.get(observation.normalized, ("incomplete", "warning"))
        raw = observation.process.returncode
        raw_text = "no exit status" if raw is None else f"raw exit {raw}"
        summary = f"{observation.name}: {status}"
        self._write(summary, severity=severity, accent=summary)
        self._write(f"Check {observation.name}: {raw_text}", severity="detail", detail=True)
        if observation.normalized != 0:
            if observation.normalized == 2:
                self._write(
                    f"apmx: check '{observation.name}': {self._incomplete_check_reason(observation)}",
                    display=_DisplayLine(
                        safe_text(self._incomplete_check_reason(observation)),
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
        """Replay stdout to the terminal only; stderr was already visible."""
        evidence, self._check_evidence = self._check_evidence, None
        if evidence is None or not evidence.total_lines:
            return
        name = safe_text(evidence.name, limit=256)
        if not completion_observed:
            self._display.emit(
                _DisplayLine(f"Check {name}: completion was not observed.", level=_Level.DETAIL)
            )
        if self.verbose:
            return
        excerpt = evidence.excerpt()
        self._display.emit(_DisplayLine(f"Check {name} stdout excerpt:", role=_Role.DETAIL))
        if excerpt.omitted_bytes:
            self._display.emit(
                _DisplayLine(
                    f"[... sanitized bytes omitted: {excerpt.omitted_bytes}; "
                    f"whole lines omitted: {excerpt.omitted_lines}; "
                    f"partial lines: {excerpt.partial_lines}; beginning/tail follow ...]",
                    role=_Role.DETAIL,
                    level=_Level.DETAIL,
                    layout=_Layout.PROSE,
                )
            )
        for fragment in excerpt.fragments:
            self._display.emit(
                _DisplayLine(
                    fragment.text.removesuffix("\n"),
                    source=f"Check {name}",
                    level=_Level.DETAIL,
                    layout=_Layout.LITERAL,
                )
            )
        if self._run_directory is not None:
            self._display.emit(
                _DisplayLine(
                    safe_text(f"Logs: {self._path(self._run_directory / 'transcript.log')}"),
                    role=_Role.DETAIL,
                    level=_Level.DETAIL,
                )
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
        # Hidden metadata/tool/stdout events must not starve the human heartbeat.
        # This display-only clock cannot change transcript bytes or record hashes.
        if (
            self._display.status is None
            and elapsed - self._last_human_activity >= HEARTBEAT_SECONDS
        ):
            self._display.emit(_DisplayLine(safe_text(message)))
            self._last_human_activity = elapsed

    def _result(self, event: RunEvent) -> None:
        if self._finished:
            return
        result = event.data.get("result")
        if not isinstance(result, RunResult):
            raise TypeError("finished requires a recorded RunResult.")
        self._finished = True
        self.stop_activity()
        self._flush_check_evidence(completion_observed=False)
        self._retained_gap()
        headline = f"apmx: {result.outcome.name}"
        self._write(
            f"{headline}  {event.elapsed_seconds:.1f}s",
            severity=self._display.outcome_role(result.outcome),
            accent=headline,
            indent=0,
            dim_remainder=True,
        )
        self._result_explanation(result)
        if result.artifact is not None:
            for item in artifact_files(result.artifact):
                self._write(f"Output: {self._path(item.path)}")
        else:
            self._write("No output was saved.")
        self._write(f"Record: {self._path(result.run_directory / 'record.json')}")
        self._write(
            "Observed execution model: "
            + (", ".join(result.observed_models) if result.observed_models else "unknown"),
            severity="detail",
            detail=True,
        )
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

    def _result_explanation(self, result: RunResult) -> None:
        """Explain the recorded outcome; never promote or downgrade it here."""
        if result.outcome == Outcome.VERIFIED:
            self._write("Contract checks passed.")
        elif result.outcome == Outcome.REJECTED:
            self._write("Contract checks found a problem.")
            self._write("Review the failed checks and saved output before retrying.")
        elif result.outcome == Outcome.UNPROVEN:
            if records.native_assurance_limited(result):
                self._write("Contract checks passed; this run was not sandboxed.")
            elif result.artifact is None:
                self._write("The declared output could not be checked.")
                self._write(
                    "Review the contract output path and Copilot diagnostics before retrying."
                )
            else:
                self._write("Checks could not establish a result.")
                self._write("Review incomplete checks and their prerequisites before retrying.")
        else:
            reason, action = {
                "cancelled": ("Run interrupted.", "Review any saved output before rerunning."),
                "producer_failed": (
                    "Copilot did not complete successfully.",
                    "Review Copilot diagnostics and logs before retrying.",
                ),
                "native_reported_failure": (
                    "Copilot reported a failure.",
                    "Review Copilot diagnostics and logs before retrying.",
                ),
                "native_protocol_error": (
                    "Copilot output could not be interpreted.",
                    "Review Copilot diagnostics and logs before retrying.",
                ),
                "native_completion_unobserved": (
                    "Copilot completion was not observed.",
                    "Review Copilot diagnostics and logs before retrying.",
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
                    "Copilot may still be running.",
                    "Inspect the reported process before retrying.",
                ),
                "checker_stop_unconfirmed": (
                    "A check may still be running.",
                    "Inspect the reported process before retrying.",
                ),
            }.get(
                result.stop_reason,
                (
                    "The run stopped before it could finish.",
                    "Resolve the reported error before retrying.",
                ),
            )
            self._write(reason)
            self._write(action)

    def render_plan(self, plan: LeafPlan, inventory: tuple[FileEntry, ...]) -> None:
        """Show the admitted surface without printing source bodies or prompts."""
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
        self._write(f"Copilot / {plan.model or 'default model'}", severity="detail")
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
        self._write("Even with passing checks, a run returns UNPROVEN because it is not sandboxed.")
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
        return leaf

    def select_factory_root(self, root: Path) -> None:
        self._display_root = self._caller_root
        self._caller_root = root

    def can_confirm_factory(self) -> bool:
        """The prompt uses stdout; both it and stdin must be interactive."""
        return console.can_confirm_factory()

    def render_factory_work(self, graph: Graph) -> None:
        self.stop_activity()
        self._write(f"Factory: {self._path(graph.root)}", severity="heading", indent=0)
        self._write(
            f"Resolved {len(graph.order)} contracts; their file dependencies determine order."
        )
        for index, contract in enumerate(graph.order, start=1):
            self._write(
                f"{index}. {contract.path.relative_to(graph.root).as_posix()} -> {contract.output_label}"
            )
            self._write("Checks: " + ", ".join(check.name for check in contract.checks), indent=4)
        outputs = [
            name
            for contract in graph.order
            if contract.path in graph.targets
            for name in contract.outputs
        ]
        self._write("Final outputs: " + ", ".join(outputs))

    def confirm_factory(self) -> bool:
        """Ask once, default NO, without conflating EOF with interruption."""
        self._write("Copilot and checks can use host files, network and available logins.")
        self._write("Package dependencies may be installed before execution.")
        self._write("Only outputs whose required checks all passed can move to another step.")
        self._write("Local execution is not isolated; results remain UNPROVEN.")
        self._write("Model calls may incur charges under your configured account.")
        self._write("Run this factory locally? [y/N]", indent=0)
        if not self._display.enabled:
            return False
        try:
            answer = sys.stdin.readline(32)
        except EOFError:
            return False
        return answer.endswith("\n") and answer.strip().casefold() in {"y", "yes"}

    def chain_node(self, index: int, count: int, contract: Path) -> None:
        self._display.gap()
        self._write(f"Step {index}/{count}: {self._path(contract)}", severity="heading", indent=0)

    def render_chain_plan(self, plan: ChainPlan) -> None:
        self._write(f"Factory preview: {len(plan.nodes)} contracts", severity="heading", indent=0)
        self._write(f"{plan.nodes[0].plan.harness} / {plan.nodes[0].plan.model or 'default model'}")
        self._write("Nothing will execute or download. Dependency resolution uses no model calls.")
        policy = (
            "fully checked local outputs (--allow-unproven-inputs); still UNPROVEN"
            if plan.allow_unproven_inputs
            else "strict VERIFIED-only; UNPROVEN inputs block"
        )
        self._write(f"Handoff policy: {policy}")
        terminals = [
            node.plan.contract.output_label
            for node in plan.nodes
            if node.plan.contract.path in plan.graph.targets
        ]
        self._write("Final outputs: " + ", ".join(terminals))
        for index, node in enumerate(plan.nodes, start=1):
            contract = node.plan.contract
            name = contract.path.relative_to(plan.graph.root).as_posix()
            self._write(f"{index}. {name} -> {contract.output_label}")
            for value in contract.needs:
                kind = (
                    "from an earlier step"
                    if value in node.plan.deferred_inputs
                    else "starting file"
                )
                self._write(f"Input: {value} ({kind})", indent=4)
            self._write("Checks: " + ", ".join(check.name for check in contract.checks), indent=4)
        self._write("Every run starts fresh; APMX does not cap model charges.")
        self._write(
            f"Limits: {plan.nodes[0].plan.limits.chain_contracts} discovered contracts; "
            "one attempt per step, no retries."
        )
        self._write(
            "Without --plan or consent flags, an interactive factory invocation asks for "
            "confirmation before execution."
        )
        if not plan.allow_unproven_inputs:
            self._write(
                "For trusted local automation, explicitly add --allow-host-access and "
                "--allow-unproven-inputs to permit fully checked native handoffs. "
                "Native results remain UNPROVEN."
            )

    def chain_stopped(self, reason: str, *, outcome: Outcome, code: str) -> None:
        self._chain_stop = _StopContext(outcome, reason, code)
        self.stop_activity()
        self._display.gap()
        self._write(
            reason,
            severity="warning",
            indent=0,
            display=_DisplayLine(
                safe_text(reason),
                role=self._display.outcome_role(outcome),
                level=_Level.HEADING,
                layout=_Layout.PROSE,
            ),
        )

    def render_chain_result(self, result: ChainResult) -> None:
        self.stop_activity()
        self._display.gap()
        headline = f"apmx factory: {result.outcome.name}"
        self._write(
            f"{headline} ({'complete' if result.complete else 'stopped'})",
            severity=self._display.outcome_role(result.outcome),
            accent=headline,
            dim_remainder=True,
            indent=0,
        )
        if result.complete:
            completed = len(result.runs)
            passed = sum(check.normalized == 0 for run in result.runs for check in run.checks)
            self._display.emit(
                _DisplayLine(
                    f"{completed} contract{'s' if completed != 1 else ''} completed; "
                    f"{passed} check{'s' if passed != 1 else ''} passed."
                )
            )
            self._write(
                "All selected checks completed; no isolation or production certification.",
                display_message="Local execution was not isolated. No production certification.",
                layout=_Layout.PROSE,
            )
            self._write(f"Artifacts: {self._path(result.record_path.parent / 'artifacts')}")
        else:
            context = self._chain_stop
            reason = (
                context.reason
                if context is not None and context.code == result.stop_reason
                else "Factory stopped before all selected steps completed."
            )
            self._write(
                f"Stop: {result.stop_reason}. Inspect the record before starting another run.",
                display_message=reason,
                layout=_Layout.PROSE,
            )
            self._display.emit(
                _DisplayLine(
                    "Inspect the factory record and reported diagnostics before starting another run.",
                    layout=_Layout.PROSE,
                )
            )
            self._display.emit(
                _DisplayLine(
                    safe_text(f"Stop reason: {result.stop_reason}"),
                    role=_Role.DETAIL,
                    level=_Level.DETAIL,
                ),
                visibility=_Visibility.VERBOSE,
                verbose=self.verbose,
            )
        self._write(f"Factory record: {self._path(result.record_path)}")
        self._display.gap()
