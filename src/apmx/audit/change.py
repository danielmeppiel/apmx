"""Which receipt, and which change: ``apmx audit`` defaults.

Locally, the receipt is the newest COMPLETE run under ``./.apm/``. In a
GitHub pull request, it is the receipt the PR adds, and the PR's own change
(merge-base..head) is bound to that receipt automatically: every file the PR
changes must be a receipt output (or an unchanged recorded input) with the
same SHA-256 at head. Nothing is guessed: ambiguity is exit 2, a mismatch
is exit 1.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

KINDS = ("runs", "chains", "controllers")
_RECEIPT = re.compile(
    r"^(?P<root>(?:.+/)?)\.apm/(?:runs|chains|controllers)/[^/]+/receipt/index\.json$"
)
_EVENTS = ("pull_request", "pull_request_target")
_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


class ChangeUnavailable(ValueError):
    """The PR change could not be established (missing commits, no git): exit 2."""


@dataclass(frozen=True)
class PullRequest:
    base: str
    head: str
    number: int | None = None
    repository: str | None = None

    @property
    def label(self) -> str:
        return f"PR #{self.number}" if self.number else "PR"


def pull_request(env: dict[str, str] | None = None) -> PullRequest | None:
    """The GitHub Actions pull request this audit runs for, if any."""
    env = os.environ if env is None else env
    if env.get("GITHUB_ACTIONS") != "true" or env.get("GITHUB_EVENT_NAME") not in _EVENTS:
        return None
    try:
        event = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_bytes())
        pr = event["pull_request"]
        base, head = pr["base"]["sha"], pr["head"]["sha"]
    except (KeyError, OSError, ValueError, TypeError):
        return None
    if not (
        isinstance(base, str)
        and _SHA.fullmatch(base)
        and isinstance(head, str)
        and _SHA.fullmatch(head)
    ):
        return None
    number = pr.get("number") if isinstance(pr.get("number"), int) else None
    return PullRequest(base, head, number, env.get("GITHUB_REPOSITORY"))


def latest_local(cwd: Path) -> tuple[Path, int] | None:
    """Newest receipt under ``cwd/.apm`` by run id (ids start with a UTC timestamp)."""
    found = []
    for kind in KINDS:
        directory = cwd / ".apm" / kind
        if directory.is_dir() and not directory.is_symlink():
            found += [
                path.parent
                for path in directory.glob("*/receipt/index.json")
                if path.is_file() and not path.parent.is_symlink()
            ]
    if not found:
        return None
    found.sort(key=lambda path: (path.parent.name, path.stat().st_mtime))
    return found[-1], len(found)


def _git(root: Path, *args: str, binary: bytes | None = None) -> bytes:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=root, input=binary, capture_output=True, check=False, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ChangeUnavailable(f"git is unavailable ({type(exc).__name__})") from exc
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise ChangeUnavailable(f"git {args[0]} failed: {message[-1] if message else 'no output'}")
    return completed.stdout


@dataclass
class Change:
    """The PR's files: what changed since the merge-base, and their bytes at head."""

    pull: PullRequest
    root: Path
    merge_base: str
    changed: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path, pull: PullRequest) -> Change:
        for name, sha in (("base", pull.base), ("head", pull.head)):
            try:
                _git(root, "cat-file", "-e", f"{sha}^{{commit}}")
            except ChangeUnavailable as exc:
                raise ChangeUnavailable(
                    f"the PR {name} commit {sha[:12]} is not in this checkout; "
                    "use actions/checkout with fetch-depth: 0"
                ) from exc
        merge_base = _git(root, "merge-base", pull.base, pull.head).decode().strip()
        raw = _git(root, "diff", "--name-status", "--no-renames", "-z", merge_base, pull.head)
        parts = raw.decode("utf-8", errors="surrogateescape").split("\0")
        changed = {parts[i + 1]: parts[i] for i in range(0, len(parts) - 1, 2) if parts[i]}
        return cls(pull, root, merge_base, changed)

    def receipts(self) -> list[str]:
        """Receipt directories (repository-relative) the PR adds or changes."""
        return sorted(
            path.removesuffix("/index.json")
            for path, status in self.changed.items()
            if status != "D" and _RECEIPT.match(path)
        )

    def head_digests(self, paths: list[str]) -> dict[str, str]:
        """SHA-256 of each path at head; absent paths are omitted."""
        if not paths:
            return {}
        listing = _git(self.root, "ls-tree", "-z", self.pull.head, "--", *paths)
        blobs = {}
        for entry in listing.decode("utf-8", errors="surrogateescape").split("\0"):
            if not entry:
                continue
            meta, _, path = entry.partition("\t")
            mode, kind, sha = meta.split()
            if kind == "blob" and mode in ("100644", "100755"):
                blobs[path] = sha
        if not blobs:
            return {}
        out = _git(
            self.root, "cat-file", "--batch", binary="\n".join(blobs.values()).encode() + b"\n"
        )
        by_blob: dict[str, str] = {}
        offset = 0
        while offset < len(out):
            header_end = out.index(b"\n", offset)
            sha, _, size = out[offset:header_end].decode().split()
            start = header_end + 1
            by_blob[sha] = hashlib.sha256(out[start : start + int(size)]).hexdigest()
            offset = start + int(size) + 1
        return {path: by_blob[sha] for path, sha in blobs.items()}


def workspace_prefix(receipt: Path, root: Path) -> str | None:
    """Repository-relative directory a receipt's outputs are relative to (``X/`` for ``X/.apm/...``)."""
    try:
        relative = receipt.resolve().relative_to(root.resolve()).as_posix() + "/index.json"
    except (ValueError, OSError):
        return None
    match = _RECEIPT.match(relative)
    return match.group("root") if match else None


def bind(
    change: Change,
    prefix: str,
    outputs: dict[str, str],
    inputs: dict[str, set[str]],
) -> tuple[bool, str, list[str], dict[str, object]]:
    """Bind the PR change to the receipt: outputs match at head; nothing else changed.

    ``outputs`` and ``inputs`` are receipt-relative paths (relative to
    ``prefix``) with their SHA-256. The receipt's own ``.apm/`` state under
    ``prefix`` is excluded from the change.
    """
    state = f"{prefix}.apm/"
    relevant = {
        path: status for path, status in change.changed.items() if not path.startswith(state)
    }
    names = sorted({prefix + name for name in outputs} | set(relevant))
    head = change.head_digests(names)
    facts: dict[str, object] = {
        "pullRequest": change.pull.number,
        "base": change.pull.base,
        "head": change.pull.head,
        "mergeBase": change.merge_base,
    }
    for name, digest in sorted(outputs.items()):
        actual = head.get(prefix + name)
        if actual is None:
            return False, f"{prefix}{name} is a receipt output but is missing at head", [], facts
        if actual != digest:
            return (
                False,
                f"{prefix}{name} at head differs from its receipt subject (SHA-256)",
                [],
                facts,
            )
    unbound = []
    bound_inputs = 0
    for path, status in sorted(relevant.items()):
        relative = path[len(prefix) :] if path.startswith(prefix) else None
        if relative is not None and relative in outputs:
            continue
        if (
            relative is not None
            and status != "D"
            and head.get(path) is not None
            and head[path] in inputs.get(relative, set())
        ):
            bound_inputs += 1
            continue
        unbound.append(("deletes " if status == "D" else "changes ") + path)
    facts["changedFiles"] = len(relevant)
    if unbound:
        return (
            False,
            f"{change.pull.label} {unbound[0]}, which the receipt does not bind"
            + (f" (+{len(unbound) - 1} more)" if len(unbound) > 1 else ""),
            [_ascii(item) for item in unbound[1:6]],
            facts,
        )
    detail = (
        f"{change.pull.label}: {len(outputs)}/{len(outputs)} outputs match at head "
        f"{change.pull.head[:12]}; {len(relevant)} changed file{'s' if len(relevant) != 1 else ''} bound"
    )
    notes = (
        [f"{bound_inputs} changed file(s) are unmodified recorded inputs"] if bound_inputs else []
    )
    return True, detail, notes, facts


def receipt_inputs(index: dict) -> dict[str, set[str]]:
    """Baseline files the selected attempts read, by receipt-relative path."""
    inputs: dict[str, set[str]] = {}
    for attempt in index.get("attempts", []):
        if not attempt.get("selected"):
            continue
        prefix = f"attempts/{attempt['runId']}/baseline/"
        for name, identity in index["files"].items():
            if name.startswith(prefix):
                inputs.setdefault(name[len(prefix) :], set()).add(identity["sha256"])
    return inputs


def _ascii(text: str) -> str:
    return "".join(char if " " <= char <= "~" else "?" for char in str(text))


def relative_display(path: Path, cwd: Path) -> str:
    try:
        return PurePosixPath(path.resolve().relative_to(cwd.resolve())).as_posix()
    except (ValueError, OSError):
        return str(path)
