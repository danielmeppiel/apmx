"""Which receipt, and which change: ``apmx audit`` defaults.

Locally, the receipt is the newest COMPLETE run under ``./.apm/``. In a
GitHub pull request, it is the receipt the PR adds, and the PR's own change
(merge-base..head) is bound to that receipt automatically: every file the PR
changes must be a receipt output (or an unchanged recorded input) with the
same SHA-256 at head, or a file a patch output touches, whose head bytes must
equal that patch applied to the merge-base (see :mod:`apmx.audit.patches`).
Nothing is guessed: ambiguity is exit 2, a mismatch is exit 1.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import patches as patching

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

    def entries(self, revision: str, paths: list[str]) -> dict[str, tuple[str, str, int]]:
        """(mode, object id, size) of each exact path at ``revision``; absent paths are omitted."""
        wanted, found = set(paths), {}
        ordered = sorted(wanted)
        for start in range(0, len(ordered), 200):
            listing = _git(
                self.root, "ls-tree", "-z", "-l", revision, "--", *ordered[start : start + 200]
            )
            for entry in listing.decode("utf-8", errors="surrogateescape").split("\0"):
                if not entry:
                    continue
                meta, _, path = entry.partition("\t")
                mode, _, sha, size = meta.split()
                if path in wanted:
                    found[path] = (mode, sha, int(size) if size.isdigit() else -1)
        return found

    def blobs(self, shas: set[str]) -> dict[str, bytes]:
        """Bytes of these blob ids (each at most 16 MiB)."""
        if not shas:
            return {}
        out = _git(
            self.root, "cat-file", "--batch", binary="\n".join(sorted(shas)).encode() + b"\n"
        )
        found: dict[str, bytes] = {}
        offset = 0
        while offset < len(out):
            header_end = out.index(b"\n", offset)
            sha, kind, size = out[offset:header_end].decode().split()
            if kind != "blob":
                raise ChangeUnavailable(f"object {sha[:12]} is not a blob")
            start = header_end + 1
            found[sha] = out[start : start + int(size)]
            offset = start + int(size) + 1
        return found

    def head_digests(self, paths: list[str]) -> dict[str, str]:
        """SHA-256 of each path at head; absent paths are omitted."""
        if not paths:
            return {}
        blobs = {
            path: sha
            for path, (mode, sha, _) in self.entries(self.pull.head, paths).items()
            if mode in patching.REGULAR
        }
        contents = self.blobs(set(blobs.values()))
        return {path: hashlib.sha256(contents[sha]).hexdigest() for path, sha in blobs.items()}


def workspace_prefix(receipt: Path, root: Path) -> str | None:
    """Repository-relative directory a receipt's outputs are relative to (``X/`` for ``X/.apm/...``)."""
    try:
        relative = receipt.resolve().relative_to(root.resolve()).as_posix() + "/index.json"
    except (ValueError, OSError):
        return None
    match = _RECEIPT.match(relative)
    return match.group("root") if match else None


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'s' if count != 1 else ''}"


def _apply(
    change: Change,
    prefix: str,
    outputs: dict[str, str],
    applied: list[patching.Patch],
    touched: dict[str, str],
) -> str | None:
    """Replay the applied patches on the merge-base; the first mismatch with head, or None."""
    for patch in applied:
        for path in patch.paths:
            if patching.unsafe(path):
                return (
                    f"{patch.name} touches {path}, which audit never applies "
                    "(absolute, '..', .git, .gitmodules or .apm path)"
                )
            if path in outputs:
                return f"{patch.name} touches {prefix}{path}, which is also a receipt output"
            touched[prefix + path] = patch.name
    if len(touched) > patching.MAX_FILES:
        raise ChangeUnavailable(f"the patches touch more than {patching.MAX_FILES} files")
    if len({path.casefold() for path in touched}) != len(touched):
        return "the applied patches touch paths that differ only by case"
    paths = sorted(touched)
    base = change.entries(change.merge_base, paths)
    now = change.entries(change.pull.head, paths)
    regular = [entry for entry in (*base.values(), *now.values()) if entry[0] in patching.REGULAR]
    if any(not 0 <= size <= patching.MAX_BYTES for _, _, size in regular):
        raise ChangeUnavailable("a file the patches touch is larger than 16 MiB")
    contents = change.blobs({sha for _, sha, _ in regular})
    try:
        result = patching.apply(base, contents, prefix, applied, change.merge_base)
    except patching.Mismatch as exc:
        return str(exc)
    for path in paths:
        name, expected = touched[path], result[path[len(prefix) :]]
        actual, before = now.get(path), base.get(path)
        if actual is not None and actual[0] not in patching.REGULAR:
            return f"{path} at head is not a regular file (symlink, directory or submodule)"
        if expected is None:
            if actual is not None:
                return f"{name} deletes {path}, but it is still at head"
            continue
        if actual is None:
            return f"{path} is missing at head; {name} applied to base produces it"
        if contents[actual[1]] != expected[1]:
            if before is not None and before[1] == actual[1]:
                return f"{change.pull.label} does not apply {name} to {path} (unchanged from base)"
            return f"{path} at head differs from {name} applied to base"
        if actual[0] != expected[0]:
            return (
                f"{path} at head has mode {actual[0]}; {name} applied to base gives {expected[0]}"
            )
    return None


def bind(
    change: Change,
    prefix: str,
    outputs: dict[str, str],
    inputs: dict[str, set[str]],
    patches: dict[str, patching.Patch] | None = None,
) -> tuple[bool, str, list[str], dict[str, object]]:
    """Bind the PR change to the receipt: outputs match at head; nothing else changed.

    ``outputs`` and ``inputs`` are receipt-relative paths (relative to
    ``prefix``) with their SHA-256. The receipt's own ``.apm/`` state under
    ``prefix`` is excluded from the change.

    A patch output (``patches``) the PR does not commit must be applied
    instead: it is committed when the PR adds or modifies ``<prefix><name>``
    or head already has it with the receipt's digest; otherwise the files it
    touches must equal it applied to the merge-base. Once any patch is
    applied, the other outputs are optional, but any the PR changes must
    match. With no applied patch every output must be at head, as before.
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
    candidates = {name: patch for name, patch in (patches or {}).items() if name in outputs}
    applied = [
        patch
        for name, patch in candidates.items()
        if relevant.get(prefix + name, "D") == "D" and head.get(prefix + name) != outputs[name]
    ]
    applied_names = {patch.name for patch in applied}
    if candidates:
        facts["patches"] = [
            {
                "name": name,
                "delivery": "applied" if name in applied_names else "committed",
                "files": [prefix + path for path in patch.paths],
            }
            for name, patch in candidates.items()
        ]
    touched: dict[str, str] = {}
    if applied:
        failure = _apply(change, prefix, outputs, applied, touched)
        facts["appliedFiles"] = sorted(touched)
        if failure is not None:
            return False, failure, [], facts
    committed, omitted = 0, []
    for name, digest in sorted(outputs.items()):
        if name in applied_names:
            continue
        actual = head.get(prefix + name)
        if applied and relevant.get(prefix + name, "D") == "D":
            if actual == digest:
                committed += 1
            else:
                omitted.append(name)
            continue
        if actual is None:
            return False, f"{prefix}{name} is a receipt output but is missing at head", [], facts
        if actual != digest:
            return (
                False,
                f"{prefix}{name} at head differs from its receipt subject (SHA-256)",
                [],
                facts,
            )
        committed += 1
    hints = {prefix + path: name for name, patch in candidates.items() for path in patch.paths}
    unbound, notes = [], []
    bound_inputs = 0
    for path, status in sorted(relevant.items()):
        relative = path[len(prefix) :] if path.startswith(prefix) else None
        if path in touched:
            continue
        if relative in outputs and relative not in applied_names and status != "D":
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
        if path in hints and len(notes) < 5:
            notes.append(f"{path} is in {hints[path]}, which this PR commits rather than applies")
    facts["changedFiles"] = len(relevant)
    if unbound:
        return (
            False,
            f"{change.pull.label} {unbound[0]}, which the receipt does not bind"
            + (f" (+{len(unbound) - 1} more)" if len(unbound) > 1 else ""),
            notes + [_ascii(item) for item in unbound[1:6]],
            facts,
        )
    if applied:
        facts["committedOutputs"] = committed
        detail = (
            f"{change.pull.label}: {' + '.join(patch.name for patch in applied)} applied to base "
            f"reproduce{'s' if len(applied) == 1 else ''} head ({_plural(len(touched), 'file')})"
        )
        if committed:
            detail += f"; {committed} other output{'s' if committed != 1 else ''} match"
        if omitted:
            notes.append(
                f"{_plural(len(omitted), 'output')} not in the PR (kept in the receipt): "
                + " ".join(omitted[:5])
                + (" ..." if len(omitted) > 5 else "")
            )
    else:
        detail = (
            f"{change.pull.label}: {len(outputs)}/{len(outputs)} outputs match at head "
            f"{change.pull.head[:12]}; {_plural(len(relevant), 'changed file')} bound"
        )
    if bound_inputs:
        notes.append(f"{bound_inputs} changed file(s) are unmodified recorded inputs")
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
