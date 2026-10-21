"""Post-apply files of patch outputs: an internal step of the receipt writer.

A selected output is a *patch output* when its bytes are a
``git diff --full-index --no-renames`` text patch, the format
``export_changes`` writes, and every file it modifies or deletes has, as its
pre-image, the exact bytes the producing attempt captured in its baseline.
The receipt's subjects are then the files the patches produce, at their
project paths, rather than the patch itself.

Patches are replayed with ``git apply`` (no fuzz, no 3-way) in a temporary
directory outside any checkout; the result must reproduce every post-image
the patch header records.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

ZERO = "0" * 40
EMPTY_BLOB = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
REGULAR = ("100644", "100755")
MAX_FILES = 10_000
MAX_BYTES = 16 * 1024 * 1024
_INDEX = re.compile(r"index ([0-9a-f]{40})\.\.([0-9a-f]{40})(?: (100644|100755))?")
_HUNK = re.compile(r"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@(?: .*)?")


class Mismatch(ValueError):
    """The PR does not apply a patch output exactly: exit 1 with this reason."""


@dataclass(frozen=True)
class FilePatch:
    """One file section; paths are relative to the factory workspace."""

    path: str
    old: str | None
    new: str | None
    mode: str


@dataclass(frozen=True)
class Patch:
    name: str
    raw: bytes
    files: tuple[FilePatch, ...]

    @property
    def paths(self) -> list[str]:
        return [item.path for item in self.files]


def blob_id(raw: bytes) -> str:
    """Git's SHA-1 object id for these bytes, as ``--full-index`` records it."""
    return hashlib.sha1(b"blob %d\0" % len(raw) + raw, usedforsecurity=False).hexdigest()


def _header_path(rest: str) -> str | None:
    """``a/P b/P`` with the same P on both sides (no renames); git quotes odd names."""
    if rest.startswith('"') or (len(rest) - 5) % 2:
        return None
    path = rest[2 : 2 + (len(rest) - 5) // 2]
    return path if path and rest == f"a/{path} b/{path}" else None


def _hunks(lines: list[str], i: int) -> int | None:
    """Consume one or more hunks by their line counts; return the next index."""
    count = 0
    while i < len(lines) and lines[i].startswith("@@ "):
        match = _HUNK.fullmatch(lines[i])
        if match is None:
            return None
        old = int(match.group(1) or 1)
        new = int(match.group(2) or 1)
        i += 1
        while old > 0 or new > 0:
            if i >= len(lines):
                return None
            kind = lines[i][:1]
            if kind == " ":
                old, new = old - 1, new - 1
            elif kind == "-":
                old -= 1
            elif kind == "+":
                new -= 1
            elif kind != "\\":
                return None
            if old < 0 or new < 0:
                return None
            i += 1
        if i < len(lines) and lines[i].startswith("\\ "):
            i += 1
        count += 1
    return i if count else None


def parse(raw: bytes) -> tuple[FilePatch, ...] | None:
    """File sections of an ``export_changes``-format patch, or None if it is not one."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not text.startswith("diff --git ") or not text.endswith("\n"):
        return None
    lines = text.split("\n")[:-1]
    files: list[FilePatch] = []
    seen: set[str] = set()
    i = 0
    while i < len(lines):
        if not lines[i].startswith("diff --git "):
            return None
        path = _header_path(lines[i][len("diff --git ") :])
        if path is None or path in seen or len(files) >= MAX_FILES:
            return None
        seen.add(path)
        i += 1
        created = deleted = None
        if i < len(lines) and lines[i].startswith(("new file mode ", "deleted file mode ")):
            kind, _, mode = lines[i].rpartition(" ")
            if mode not in REGULAR:
                return None
            created, deleted = (mode, None) if kind == "new file mode" else (None, mode)
            i += 1
        match = _INDEX.fullmatch(lines[i]) if i < len(lines) else None
        if match is None:
            return None
        old, new, mode = match.groups()
        i += 1
        if created:
            if old != ZERO or new == ZERO or mode:
                return None
            entry = FilePatch(path, None, new, created)
        elif deleted:
            if old == ZERO or new != ZERO or mode:
                return None
            entry = FilePatch(path, old, None, deleted)
        else:
            if ZERO in (old, new) or old == new or not mode:
                return None
            entry = FilePatch(path, old, new, mode)
        tab = "\t" if " " in path else ""
        before = "/dev/null" if created else f"a/{path}{tab}"
        after = "/dev/null" if deleted else f"b/{path}{tab}"
        if i < len(lines) and lines[i].startswith("--- "):
            if lines[i : i + 2] != [f"--- {before}", f"+++ {after}"]:
                return None
            i = _hunks(lines, i + 2)
            if i is None:
                return None
        elif (
            (created and new != EMPTY_BLOB)
            or (deleted and old != EMPTY_BLOB)
            or not (created or deleted)
        ):
            return None
        files.append(entry)
    return tuple(files)


def unsafe(path: str) -> bool:
    """Absolute, escaping, Git-internal or receipt-state paths are never applied."""
    parts = path.split("/")
    return (
        not path
        or path.startswith("/")
        or any(ord(char) < 32 or char in '\\:"' for char in path)
        or any(part in ("", ".", "..") for part in parts)
        or any(part.casefold() in (".git", ".gitmodules") for part in parts)
        or parts[0].casefold() == ".apm"
    )


def _environment(ceiling: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CEILING_DIRECTORIES=ceiling,
        GIT_TERMINAL_PROMPT="0",
    )
    return env


def _git_apply(work: Path, raw: bytes, env: dict[str, str]) -> str | None:
    """Strict ``git apply`` in a plain directory; the failure reason, or None."""
    try:
        completed = subprocess.run(
            [
                "git",
                "-c",
                "core.autocrlf=false",
                "-c",
                "core.safecrlf=false",
                "-c",
                "apply.whitespace=nowarn",
                "-c",
                "apply.ignoreWhitespace=no",
                "apply",
                "--whitespace=nowarn",
                "-p1",
                "-",
            ],
            cwd=work,
            input=raw,
            env=env,
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError(f"git apply is unavailable ({type(exc).__name__})") from exc
    if completed.returncode == 0:
        return None
    lines = completed.stderr.decode("utf-8", errors="replace").strip().splitlines()
    return lines[-1] if lines else f"git apply exited {completed.returncode}"


def _target(work: Path, path: str) -> Path:
    target = work
    for part in path.split("/"):
        target = target / part
    return target


def _walk(work: Path) -> set[str]:
    found = set()
    for count, path in enumerate(work.rglob("*"), 1):
        if count > MAX_FILES * 4 or path.is_symlink():
            raise Mismatch("applying the patches produced a symlink or too many files")
        if not path.is_dir():
            found.add(path.relative_to(work).as_posix())
    return found


def apply(state: dict[str, tuple[str, bytes]], patch: Patch) -> dict[str, tuple[str, bytes] | None]:
    """Replay one patch onto ``state`` (the current (mode, bytes) of the files it touches).

    Returns each touched path's result: (mode, bytes), or None when deleted.
    """
    for item in patch.files:
        current = state.get(item.path)
        if unsafe(item.path):
            raise Mismatch(f"{patch.name} touches {item.path}, which is never applied")
        if item.old is None and current is not None:
            raise Mismatch(f"{patch.name} adds {item.path}, which already exists")
        if item.old is not None and (
            current is None or current[0] != item.mode or blob_id(current[1]) != item.old
        ):
            raise Mismatch(f"{patch.name}: {item.path} differs from the patch pre-image")
    with tempfile.TemporaryDirectory(prefix="apmx-patch-") as temporary:
        root = Path(temporary).resolve()
        work = root / "work"
        work.mkdir()
        env = _environment(str(root))
        present = {path: value for path, value in state.items() if value is not None}
        for path in patch.paths:
            if path in present:
                mode, raw = present[path]
                target = _target(work, path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                target.chmod(0o755 if mode == "100755" else 0o644)
        failure = _git_apply(work, patch.raw, env)
        if failure is not None:
            raise Mismatch(f"{patch.name} does not apply: {failure}")
        result: dict[str, tuple[str, bytes] | None] = {}
        for item in patch.files:
            target = _target(work, item.path)
            if item.new is None:
                if os.path.lexists(target):
                    raise Mismatch(f"{patch.name} keeps {item.path}, which it deletes")
                result[item.path] = None
                continue
            info = target.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                raise Mismatch(f"{patch.name} gives no regular {item.path}")
            raw = target.read_bytes()
            if blob_id(raw) != item.new:
                raise Mismatch(f"{patch.name} does not give its recorded post-image of {item.path}")
            result[item.path] = (item.mode, raw)
        if _walk(work) != {path for path, value in result.items() if value is not None}:
            raise Mismatch(f"{patch.name} touched files its headers do not name")
    return result
