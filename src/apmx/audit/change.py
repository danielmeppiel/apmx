"""Which receipt, and which change: ``apmx audit`` defaults.

Locally, the receipt is the newest COMPLETE run's ``receipt.intoto.jsonl``
under ``./.apm/``. In a GitHub pull request the PR carries code only: its own
change (merge-base..head) is bound to the delivery a signed verification
summary names. Every file the PR adds or modifies must be a delivered file
with the same SHA-256 at head; every file it deletes must be a recorded
deletion. Freshness: each file the PR touches must have, at the PR base, the
before-image the factory started from, and a recorded base commit must be in
the PR base's history. Nothing is guessed: ambiguity is exit 2, a mismatch is
exit 1.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import verifier

KINDS = ("runs", "chains", "controllers")
REGULAR = ("100644", "100755")
MAX_BYTES = 16 * 1024 * 1024
_RECEIPT = re.compile(
    r"^(?P<root>(?:.+/)?)\.apm/(?:runs|chains|controllers)/[^/]+/receipt\.intoto\.jsonl$"
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
                path
                for path in directory.glob(f"*/{verifier.BUNDLE}")
                if path.is_file() and not path.is_symlink() and not path.parent.is_symlink()
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

    def files(self, revision: str, paths: list[str]) -> dict[str, tuple[str, bytes | None]]:
        """(mode, bytes) of each path at ``revision``; bytes are None for non-regular entries."""
        if not paths:
            return {}
        entries = self.entries(revision, paths)
        regular = {path: entry for path, entry in entries.items() if entry[0] in REGULAR}
        if any(not 0 <= size <= MAX_BYTES for _, _, size in regular.values()):
            raise ChangeUnavailable("a changed file is larger than 16 MiB")
        contents = self.blobs({sha for _, sha, _ in regular.values()})
        return {
            path: (mode, contents[sha] if path in regular else None)
            for path, (mode, sha, _) in entries.items()
        }

    def digests(self, revision: str, paths: list[str]) -> dict[str, str | None]:
        """SHA-256 of each path at ``revision`` (None: not a regular file); absent omitted."""
        return {
            path: None if raw is None else hashlib.sha256(raw).hexdigest()
            for path, (_, raw) in self.files(revision, paths).items()
        }

    def is_ancestor(self, commit: str) -> bool | None:
        """Is ``commit`` in the PR base's history? None when it is not in this checkout."""
        try:
            _git(self.root, "cat-file", "-e", f"{commit}^{{commit}}")
        except ChangeUnavailable:
            return None
        try:
            _git(self.root, "merge-base", "--is-ancestor", commit, self.pull.base)
        except ChangeUnavailable:
            return False
        return True


def workspace_prefix(receipt: Path, root: Path) -> str | None:
    """Repository-relative directory a receipt's outputs are relative to (``X/`` for ``X/.apm/...``)."""
    try:
        relative = receipt.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return None
    match = _RECEIPT.match(relative)
    return match.group("root") if match else None


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'s' if count != 1 else ''}"


def bind(
    change: Change, prefix: str, receipt: verifier.Receipt
) -> tuple[bool, str, list[str], dict[str, object]]:
    """Bind the PR change to a delivery: subjects at head, recorded deletions, fresh base.

    ``prefix`` is the repository directory the delivered files sit under.
    """
    subjects = {prefix + name: digest for name, digest in receipt.subjects.items()}
    deleted = {prefix + name: digest for name, digest in receipt.deleted.items()}
    before = {prefix + name: digest for name, digest in receipt.before.items()}
    changed = dict(change.changed)
    facts: dict[str, object] = {
        "pullRequest": change.pull.number,
        "base": change.pull.base,
        "head": change.pull.head,
        "mergeBase": change.merge_base,
        "changedFiles": len(changed),
        "subjectPrefix": prefix,
    }
    if not changed:
        return False, f"{change.pull.label} changes no files", [], facts
    if len(changed) > 10_000:
        raise ChangeUnavailable("the PR changes more than 10000 files")
    present = sorted(path for path, status in changed.items() if status != "D")
    head = change.digests(change.pull.head, present)
    unbound = []
    for path, status in sorted(changed.items()):
        if status == "D":
            if path not in deleted:
                unbound.append(f"deletes {path}, which the delivery does not delete")
            continue
        if path not in head:
            unbound.append(f"changes {path}, which is missing at head")
        elif head[path] is None:
            unbound.append(f"changes {path} into a symlink, directory or submodule")
        elif path not in subjects:
            unbound.append(f"changes {path}, which is not a delivered file")
        elif head[path] != subjects[path]:
            unbound.append(f"changes {path}, whose bytes differ from the delivered file (SHA-256)")
    if unbound:
        return (
            False,
            f"{change.pull.label} {unbound[0]}"
            + (f" (+{len(unbound) - 1} more)" if len(unbound) > 1 else ""),
            [_ascii(item) for item in unbound[1:6]],
            facts,
        )
    touched = sorted(changed)
    at_base = change.digests(change.pull.base, touched)
    stale = []
    for path in touched:
        if path not in before:
            stale.append(f"{path} has no recorded before-image")
        elif before[path] is None:
            if path in at_base:
                stale.append(f"{path} already exists at base {change.pull.base[:12]}")
        elif at_base.get(path) != before[path]:
            state = "absent" if path not in at_base else "different"
            stale.append(f"{path} is {state} at base {change.pull.base[:12]}")
    notes: list[str] = []
    commit = receipt.base.get("gitCommit")
    if commit:
        ancestor = change.is_ancestor(commit)
        facts["builtOn"] = commit
        if ancestor is None:
            raise ChangeUnavailable(
                f"the recorded base commit {commit[:12]} is not in this checkout; "
                "use actions/checkout with fetch-depth: 0"
            )
        if not ancestor:
            stale.insert(0, f"it was built on {commit[:12]}, which is not in the PR base's history")
    if stale:
        facts["stale"] = stale
        return (
            False,
            f"built on a stale base: {stale[0]}; rerun the factory",
            [_ascii(item) for item in stale[1:6]],
            facts,
        )
    omitted = sorted((set(subjects) | set(deleted)) - set(changed))
    if omitted:
        notes.append(
            f"{_plural(len(omitted), 'delivered change')} not in the PR: "
            + " ".join(omitted[:5])
            + (" ..." if len(omitted) > 5 else "")
        )
    if commit:
        notes.append(f"built on {commit[:12]}, in the PR base's history")
    removed = sum(1 for status in changed.values() if status == "D")
    detail = (
        f"{change.pull.label}: {_plural(len(changed), 'changed file')} = delivered files at head "
        f"{change.pull.head[:12]}"
        + (f" ({removed} recorded deletion{'s' if removed != 1 else ''})" if removed else "")
        + f"; before-images match base {change.pull.base[:12]}"
    )
    facts["bound"] = len(changed)
    return True, detail, notes, facts


def _ascii(text: str) -> str:
    return "".join(char if " " <= char <= "~" else "?" for char in str(text))


def relative_display(path: Path, cwd: Path) -> str:
    try:
        return PurePosixPath(path.resolve().relative_to(cwd.resolve())).as_posix()
    except (ValueError, OSError):
        return str(path)
