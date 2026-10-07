"""Bounded mirroring of one producer's explicitly selected native log directory."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

from apmx.utils.path_security import is_link_or_reparse

from .events import EventEmitter
from .stream import TEXT_LINE_BYTES, _Lines

_MAX_FILES = 8
_MAX_BYTES = 8 * 1024 * 1024
_CHUNK = 65536


class _ChangedLog(ValueError):
    """Refuse diagnostic redirection without interpreting it as a run outcome."""


@dataclass
class _Tail:
    identity: tuple[int, int]
    lines: _Lines
    offset: int = 0


class NativeLogStream:
    """Read ordinary append-only .log files, never global logs or native config.

    Callbacks stay on the process supervisor's thread. Native diagnostics remain
    untrusted text and never affect native completion or checker outcomes.
    """

    def __init__(self, directory: Path | None, events: EventEmitter | None) -> None:
        self.directory = directory
        self.events = events
        self._identity: tuple[int, int] | None = None
        self._tails: dict[str, _Tail] = {}
        self._disabled: set[str] = set()
        self._notices: set[str] = set()
        self._bytes = 0
        self._closed = False

    def _notice(self, key: str, message: str) -> None:
        if key not in self._notices and self.events is not None:
            self._notices.add(key)
            self.events.emit(
                "diagnostic",
                source="harness",
                severity="warning",
                message=message,
                action="Native public output is still streamed; inspect this run's private logs.",
            )

    def _line(self, value: bytes) -> None:
        if value.strip() and self.events is not None:
            self.events.emit(
                "native_diagnostic",
                source="harness",
                label="native debug",
                text=value.decode("utf-8", errors="backslashreplace"),
                stream="stdout",
            )

    def poll(self) -> int:
        """Drain at most one chunk per file so diagnostics cannot starve pipes."""
        if self.directory is None or self._closed:
            return 0
        if self._bytes >= _MAX_BYTES:
            self._notice("limit", "Native debug mirroring reached its 8 MiB limit.")
            return 0
        try:
            info = self.directory.lstat()
            identity = (info.st_dev, info.st_ino)
            if (
                not stat.S_ISDIR(info.st_mode)
                or is_link_or_reparse(self.directory)
                or self._identity not in (None, identity)
            ):
                raise _ChangedLog("Native debug directory was redirected; mirroring stopped.")
            self._identity = identity
            with os.scandir(self.directory) as entries:
                paths = [Path(item.path) for item in islice(entries, _MAX_FILES + 1)]
            if len(paths) > _MAX_FILES:
                raise _ChangedLog("Native debug directory exceeded its eight-file limit.")
            names = {path.name for path in paths if path.suffix == ".log"}
            if len(names | self._tails.keys() | self._disabled) > _MAX_FILES:
                raise _ChangedLog("Native debug stream exceeded its eight-file lifetime limit.")
        except _ChangedLog as exc:
            self._closed = True
            self._tails.clear()
            self._notice("directory", str(exc))
            return 0
        except OSError:
            self._notice("read", "Native debug files could not be read for live mirroring.")
            return 0
        return sum(self._read(path) for path in sorted(paths) if path.suffix == ".log")

    def _read(self, path: Path) -> int:
        if path.name in self._disabled or self._bytes >= _MAX_BYTES:
            return 0
        tail = self._tails.get(path.name)
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or is_link_or_reparse(path):
                raise _ChangedLog("A non-ordinary native debug file was not mirrored.")
            identity = (info.st_dev, info.st_ino)
            if tail is not None and (tail.identity != identity or info.st_size < tail.offset):
                raise _ChangedLog(
                    "A native debug file was replaced or truncated; mirroring stopped."
                )
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            with os.fdopen(os.open(path, flags), "rb") as stream:
                opened = os.fstat(stream.fileno())
                root = path.parent.lstat()
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or opened.st_nlink != 1
                    or (opened.st_dev, opened.st_ino) != identity
                    or (root.st_dev, root.st_ino) != self._identity
                    or is_link_or_reparse(path)
                ):
                    raise _ChangedLog(
                        "A native debug file changed during opening; it was not read."
                    )
                stream.seek(tail.offset if tail is not None else 0)
                chunk = stream.read(min(_CHUNK, _MAX_BYTES - self._bytes))
        except _ChangedLog as exc:
            self._disabled.add(path.name)
            self._tails.pop(path.name, None)
            self._notice("changed", str(exc))
            return 0
        except OSError:
            self._notice("read", "Native debug files could not be read for live mirroring.")
            return 0
        if tail is None:
            tail = _Tail(
                identity,
                _Lines(
                    TEXT_LINE_BYTES,
                    self._line,
                    lambda: self._notice("line", "An oversized native debug line was omitted."),
                ),
            )
            self._tails[path.name] = tail
        tail.offset += len(chunk)
        self._bytes += len(chunk)
        tail.lines.feed(chunk)
        return len(chunk)

    def finish(self) -> None:
        if self._closed:
            return
        while self.poll():
            pass
        if self._bytes < _MAX_BYTES:
            for tail in self._tails.values():
                tail.lines.finish()
        self._closed = True
