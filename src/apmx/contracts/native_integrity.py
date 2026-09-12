"""Verify native inventory while accounting for APM's late cache markers."""

import json
from pathlib import Path
from typing import NoReturn

from apmx.deps.lockfile import LockedDependency, LockFile
from apmx.install.contract_source_validation import bounded_tree
from apmx.utils.content_hash import (
    _EXCLUDED_DIRS, _EXCLUDED_ROOT_FILES, _hash_package_entries,
)
from apmx.utils.github_host import is_full_commit_sha
from apmx.utils.path_security import has_symlink_component
from .imports import _read_bytes
from .models import ContractError, ContractLimits


def verify_inventory_package(
    project_root: Path,
    selected: LockedDependency,
    inventory: LockFile,
    limits: ContractLimits,
    *,
    error_code: str = "import_drift",
) -> tuple[str, ...]:
    """Return only lock-proven managed metadata omitted from the hash preimage.

    APM 0.30 writes each remote root's .apm-pin after recording package hashes.
    A nested virtual package's marker therefore changes its ancestor's tree.
    Ordinary package bytes and the original native expected hashes stay authoritative.
    """
    def reject() -> NoReturn:
        raise ContractError("Installed package hash differs from its native lock.", code=error_code)

    if len(inventory.dependencies) > limits.resource_files:
        reject()
    modules = project_root / "apm_modules"
    roots = [
        (entry, entry.to_dependency_ref().get_install_path(modules))
        for entry in inventory.dependencies.values()
    ]
    checked: dict[str, tuple[str, ...]] = {}
    selected_root = selected.to_dependency_ref().get_install_path(modules)
    if has_symlink_component(project_root, selected_root):
        reject()
    files: dict[str, bytes] = {}
    total = 0
    for relative in bounded_tree(selected_root, limits):
        path = Path(relative)
        if any(part in _EXCLUDED_DIRS for part in path.parts):
            continue
        if len(path.parts) == 1 and path.name in _EXCLUDED_ROOT_FILES:
            continue
        raw = _read_bytes(selected_root / path, maximum=limits.file_bytes, root=selected_root)
        total += len(raw)
        if total > limits.baseline_bytes:
            reject()
        files[relative] = raw

    def verify(entry: LockedDependency, root: Path) -> tuple[str, ...]:
        key = entry.get_unique_key()
        if key in checked:
            return checked[key]
        if not entry.content_hash or has_symlink_component(project_root, root):
            reject()
        prefix = "" if root == selected_root else root.relative_to(selected_root).as_posix() + "/"

        def entries():
            for relative, raw in files.items():
                if relative.startswith(prefix):
                    local = relative[len(prefix):]
                    if local not in _EXCLUDED_ROOT_FILES:
                        yield local, raw

        if _hash_package_entries(entries()) == entry.content_hash:
            checked[key] = ()
            return ()

        managed: set[str] = set()
        seen_roots: set[Path] = set()
        for child, child_root in roots:
            if child_root == root or not child_root.is_relative_to(root):
                continue
            if child.source in {"local", "registry"} or not child.is_virtual:
                continue
            if child_root in seen_roots or not is_full_commit_sha(child.resolved_commit or ""):
                reject()
            seen_roots.add(child_root)
            verify(child, child_root)
            relative = (child_root / ".apm-pin").relative_to(root).as_posix()
            captured_path = prefix + relative
            if captured_path not in files:
                continue
            expected_marker = json.dumps({
                "schema_version": 1, "resolved_commit": child.resolved_commit,
            }).encode("utf-8")
            if files[captured_path] != expected_marker:
                reject()
            managed.add(relative)
        if not managed or _hash_package_entries(
            (relative, raw) for relative, raw in entries() if relative not in managed
        ) != entry.content_hash:
            reject()
        checked[key] = tuple(sorted(managed))
        return checked[key]

    return verify(selected, selected_root)
