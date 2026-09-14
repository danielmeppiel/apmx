"""Bounded private working-file operations and deterministic Git artifact export."""

import hashlib
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ..utils.atomic_io import atomic_write_text
from ..utils.path_security import is_link_or_reparse
from . import workspace
from .frontend import _fixed_path
from .models import ContractError, ContractLimits, FileEntry, SourceLocation
from .process import local_git

PROTECTED_ROOTS = frozenset(
    {
        ".git",
        ".apm",
        "apm_modules",
        "checks",
        "_apmx_context",
        "_apmx_source",
        ".agents",
        ".github",
        ".claude",
        ".copilot",
        ".cursor",
        ".codex",
        ".opencode",
        ".vscode",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "node_modules",
    }
)
PROTECTED_FILES = frozenset(
    {
        "apm.yml",
        "apm.lock",
        "apm.lock.yaml",
        ".mcp.json",
        ".gitmodules",
        ".gitattributes",
        ".gitignore",
    }
)


@dataclass(frozen=True)
class ExportContext:
    baseline: Path
    producer: Path
    directory: Path
    files: tuple[FileEntry, ...]
    outputs: tuple[str, ...]
    contract: str
    limits: ContractLimits

    def protected(self, name: str) -> bool:
        parts = Path(name).parts
        return (
            any(part.casefold() in PROTECTED_ROOTS for part in parts)
            or any(part.casefold() in PROTECTED_FILES for part in parts)
            or name.casefold() == self.contract.casefold()
        )

    def path(self, name: object) -> tuple[str, Path]:
        relative = _fixed_path(name, SourceLocation(self.directory, 1), "working file")
        if self.protected(relative):
            raise ContractError(
                "Protected runner or check files cannot be edited.", code="protected_file"
            )
        return relative, workspace._path(self.producer, relative)


@contextmanager
def _writable_copy(root: Path, name: str, mode: int | None) -> Iterator[None]:
    """Permit Windows replacement/deletion without changing captured file modes."""
    if mode is None or mode & stat.S_IWUSR:
        yield
        return
    target = workspace._path(root, name)
    target.chmod(mode | stat.S_IWUSR)
    try:
        yield
    finally:
        target = workspace._path(root, name)
        if target.exists():
            target.chmod(mode)


def write_file(context: ExportContext, name: object, content: object) -> dict:
    """Atomically replace bounded private text; no shell or extension-based capability."""
    relative, target = context.path(name)
    if not isinstance(content, str):
        raise ContractError("write_file requires text content.", code="tool_arguments")
    raw = content.encode("utf-8")
    maximum = (
        context.limits.output_bytes if relative in context.outputs else context.limits.file_bytes
    )
    if len(raw) > maximum:
        raise ContractError("Working file exceeds its byte limit.", code="tool_file_limit")
    original_mode = None
    if target.exists():
        _, original = workspace._read(context.producer, relative, maximum)
        original_mode = original.mode
    target.parent.mkdir(parents=True, exist_ok=True)
    context.path(relative)
    with _writable_copy(context.producer, relative, original_mode):
        atomic_write_text(
            target, content, normalize_line_endings=False, new_file_mode=0o644, durable=True
        )
    _, observed = workspace._read(context.producer, relative, maximum)
    if observed.sha256 != hashlib.sha256(raw).hexdigest():
        raise ContractError("Working file changed during publication.", code="tool_file_changed")
    return {"path": relative, "sha256": observed.sha256, "size": observed.size}


def delete_file(context: ExportContext, name: object) -> dict:
    """Remove one regular private working file, never a directory or caller file."""
    relative, target = context.path(name)
    _, original = workspace._read(context.producer, relative, context.limits.file_bytes)
    context.path(relative)
    with _writable_copy(context.producer, relative, original.mode):
        target.unlink()
    return {"deleted": relative}


def _base(context: ExportContext) -> dict[str, tuple[bytes, FileEntry]]:
    values = {}
    for entry in context.files:
        raw, observed = workspace._read(context.baseline, entry.relative_path, entry.size)
        if observed != entry:
            raise ContractError("Captured export baseline changed.", code="export_baseline_changed")
        if (
            not context.protected(entry.relative_path)
            and entry.relative_path not in context.outputs
        ):
            values[entry.relative_path] = (raw, entry)
    return values


def _working(context: ExportContext) -> dict[str, tuple[bytes, FileEntry]]:
    values = {}
    pending = [context.producer]
    count = total = 0
    excluded = {name.casefold() for name in context.outputs}
    originals = {entry.relative_path for entry in context.files}
    while pending:
        with os.scandir(pending.pop()) as entries:
            for item in entries:
                count += 1
                if count > context.limits.baseline_files:
                    raise ContractError(
                        "Export workspace exceeds the file limit.", code="export_limit"
                    )
                path = Path(item.path)
                name = path.relative_to(context.producer).as_posix()
                if item.name.casefold() == ".git" and path.parent != context.producer:
                    raise ContractError(
                        "Nested Git repositories/submodules cannot be exported.", code="export_type"
                    )
                if context.protected(name) or name.casefold() in excluded:
                    continue
                if is_link_or_reparse(path):
                    raise ContractError(
                        "Export cannot include symlinks or reparse points.", code="export_type"
                    )
                if item.is_dir(follow_symlinks=False):
                    if name in originals:
                        raise ContractError(
                            "An original regular file became a directory.", code="export_type"
                        )
                    pending.append(path)
                    continue
                context.path(name)
                raw, entry = workspace._read(context.producer, name, context.limits.file_bytes)
                total += entry.size
                if total > context.limits.baseline_bytes:
                    raise ContractError(
                        "Export workspace exceeds the byte limit.", code="export_limit"
                    )
                values[name] = (raw, entry)
    names = {name.casefold() for name in values}
    if len(names) != len(values):
        raise ContractError("Export paths collide by case.", code="export_path")
    return values


def export_changes(context: ExportContext, output: object) -> dict:
    """Export real files from a fresh trusted index, never the producer's Git state."""
    name, _ = context.path(output)
    if name not in context.outputs:
        raise ContractError(
            "Export must target an explicitly declared artifact.", code="undeclared_output"
        )
    base, current = _base(context), _working(context)
    changed = []
    for path in sorted(base.keys() | current.keys()):
        before, after = base.get(path), current.get(path)
        if before == after:
            continue
        if before and after and before[1].mode != after[1].mode:
            raise ContractError("Git mode changes are unsupported.", code="export_mode")
        for value in (before, after):
            if value is not None:
                raw, entry = value
                if entry.mode & ~0o777 or (
                    after is value and before is None and entry.mode & 0o111
                ):
                    raise ContractError("Unsupported Git file mode.", code="export_mode")
                try:
                    raw.decode("utf-8")
                except UnicodeError as exc:
                    raise ContractError(
                        "Binary Git changes are unsupported; publish binary files as artifacts instead.",
                        code="export_binary",
                    ) from exc
                if b"\0" in raw:
                    raise ContractError(
                        "Binary Git changes are unsupported; publish binary files as artifacts instead.",
                        code="export_binary",
                    )
        changed.append(path)
    with tempfile.TemporaryDirectory(prefix="export-", dir=context.directory) as temporary:
        parent = Path(temporary)
        repository, template = parent / "tree", parent / "template"
        repository.mkdir()
        template.mkdir()
        for raw, entry in base.values():
            workspace._write(repository, entry, raw)
        workspace._initialize_git(repository, template)
        base_tree = local_git(repository, "rev-parse", "HEAD^{tree}").decode("ascii").strip()
        for path in changed:
            if path in base:
                with _writable_copy(repository, path, base[path][1].mode):
                    workspace._path(repository, path).unlink()
            if path in current:
                raw, entry = current[path]
                workspace._write(repository, entry, raw)
        local_git(repository, "add", "--all", "--force", "--", ".")
        result_tree = local_git(repository, "write-tree").decode("ascii").strip()
        patch = local_git(
            repository,
            "diff",
            "--cached",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--full-index",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            "HEAD",
            "--",
            ".",
            maximum_bytes=context.limits.output_bytes,
        )
    if _base(context) != base or _working(context) != current:
        raise ContractError("Working files changed during export.", code="export_drift")
    receipt = {
        "schema": "apmx-git-export/0.1",
        "output": name,
        "patch_sha256": hashlib.sha256(patch).hexdigest(),
        "patch_size": len(patch),
        "baseline_digest": workspace._digest(context.files),
        "base_digest": workspace._digest(tuple(value[1] for _, value in sorted(base.items()))),
        "result_digest": workspace._digest(tuple(value[1] for _, value in sorted(current.items()))),
        "base_tree": base_tree,
        "result_tree": result_tree,
        "changed_files": changed,
    }
    write_file(context, name, patch.decode("utf-8"))
    return receipt
