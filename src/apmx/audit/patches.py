"""Patch outputs a pull request applies instead of committing.

A receipt output is a *patch output* when its receipt-indexed bytes (never the
PR's copy) are a ``git diff --full-index --no-renames`` text patch, the format
``export_changes`` writes, and every file it modifies or deletes has, as its
pre-image, the exact bytes the producing attempt captured in its baseline.
Anything else is an ordinary output, bound by SHA-256 as before.

An applied patch is replayed with ``git apply`` (no fuzz, no 3-way) onto the
merge-base content of the files it touches, in a temporary directory outside
the checkout. The result must equal head for every touched file.
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

from . import verifier

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


def receipt_patches(receipt: verifier.Receipt) -> dict[str, Patch]:
    """Patch outputs of the selected attempts, in attempt order, from the receipt's bytes."""
    index, files = receipt.index, receipt.index["files"]
    owners: dict[str, list[str]] = {}
    for attempt in index["attempts"]:
        if attempt["selected"]:
            prefix = f"attempts/{attempt['runId']}/artifacts/"
            for name in files:
                if name.startswith(prefix):
                    owners.setdefault(name[len(prefix) :], []).append(attempt["runId"])
    found: dict[str, Patch] = {}
    for name, runs in owners.items():
        if len(runs) != 1:
            continue
        run_id = runs[0]
        raw = verifier.read(receipt.root, f"attempts/{run_id}/artifacts/{name}")
        if verifier.sha256(raw) != files[f"attempts/{run_id}/artifacts/{name}"]["sha256"]:
            continue
        sections = parse(raw)
        if sections and all(_matches_baseline(receipt, run_id, item) for item in sections):
            found[name] = Patch(name, raw, sections)
    return found


def _matches_baseline(receipt: verifier.Receipt, run_id: str, item: FilePatch) -> bool:
    key = f"attempts/{run_id}/baseline/{item.path}"
    if item.old is None:
        return key not in receipt.index["files"]
    if key not in receipt.index["files"] or unsafe(item.path):
        return False
    return blob_id(verifier.read(receipt.root, key)) == item.old


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


def apply(
    entries: dict[str, tuple[str, str, int]],
    contents: dict[str, bytes],
    prefix: str,
    patches: list[Patch],
    base: str,
) -> dict[str, tuple[str, bytes] | None]:
    """Replay ``patches`` in order onto the base files; return each touched path's result.

    ``entries`` maps repository paths at the merge-base to (mode, object id,
    size); ``contents`` maps those object ids to bytes. Results are keyed by
    workspace-relative path: (mode, bytes), or None when the patches delete it.
    """
    state: dict[str, tuple[str, bytes]] = {}
    touched = {path for patch in patches for path in patch.paths}
    for path in touched:
        entry = entries.get(prefix + path)
        if entry is None:
            continue
        mode, sha, _ = entry
        if mode not in REGULAR:
            raise Mismatch(f"{prefix}{path} at merge-base {base[:12]} is not a regular file")
        state[path] = (mode, contents[sha])
    with tempfile.TemporaryDirectory(prefix="apmx-audit-") as temporary:
        root = Path(temporary).resolve()
        work = root / "work"
        work.mkdir()
        env = _environment(str(root))
        for path, (mode, raw) in state.items():
            target = _target(work, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            target.chmod(0o755 if mode == "100755" else 0o644)
        for patch in patches:
            for item in patch.files:
                current = state.get(item.path)
                if item.old is None and current is not None:
                    raise Mismatch(
                        f"{patch.name} does not apply to base {base[:12]}: it adds "
                        f"{prefix}{item.path}, which already exists"
                    )
                if item.old is not None and (
                    current is None or current[0] != item.mode or blob_id(current[1]) != item.old
                ):
                    raise Mismatch(
                        f"{patch.name} does not apply to base {base[:12]}: "
                        f"{prefix}{item.path} differs from the patch pre-image"
                    )
            failure = _git_apply(work, patch.raw, env)
            if failure is not None:
                raise Mismatch(f"{patch.name} does not apply to base {base[:12]}: {failure}")
            for item in patch.files:
                target = _target(work, item.path)
                if item.new is None:
                    if os.path.lexists(target):
                        raise Mismatch(f"{patch.name} applied to base keeps {item.path}")
                    state.pop(item.path, None)
                    continue
                info = target.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                    raise Mismatch(f"{patch.name} applied to base gives no regular {item.path}")
                raw = target.read_bytes()
                if blob_id(raw) != item.new:
                    raise Mismatch(
                        f"{patch.name} applied to base does not give its recorded "
                        f"post-image of {item.path}"
                    )
                state[item.path] = (item.mode, raw)
        if _walk(work) != set(state):
            raise Mismatch("applying the patches touched files their headers do not name")
    return {path: state.get(path) for path in touched}
