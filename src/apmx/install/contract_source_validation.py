"""Bounded package-source admission shared by preparation and revalidation."""

import os
import stat
from pathlib import Path

from apmx.contracts.models import ContractError, ContractLimits, ContractSource, LeafContract
from apmx.models.dependency.reference import DependencyReference
from apmx.utils.content_hash import compute_package_hash, verify_package_hash
from apmx.utils.path_security import (
    ensure_path_within,
    has_symlink_component,
    is_link_or_reparse,
    validate_path_segments,
)


def package_entry_path(root: Path, name: str) -> Path:
    """Confine an explicit package file/directory selection before acquisition."""
    try:
        validate_path_segments(
            name, context="package entry", reject_empty=True, allow_current_dir=name == "."
        )
        if Path(name).is_absolute():
            raise ValueError("Package entries must be relative.")
        selected = root / name
        ensure_path_within(selected, root)
        if has_symlink_component(root, selected):
            raise ValueError("Package entry contains a symlink.")
    except ValueError as exc:
        raise ContractError(str(exc), code="invalid_source") from exc
    return selected


def resource_root(source: ContractSource) -> Path:
    root = package_entry_path(source.root, source.resource_subdirectory)
    if not root.is_dir():
        raise ContractError("Package resource root is missing.", code="source_changed")
    return root


def bounded_tree(root: Path, limits: ContractLimits) -> tuple[str, ...]:
    """Reject unsafe or unbounded trees before any whole-package hash or copy."""
    if not root.is_dir() or is_link_or_reparse(root):
        raise ContractError("Package source must be a regular directory.", code="invalid_source")
    pending = [root]
    count = total = 0
    files = []
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                count += 1
                if count > limits.baseline_files:
                    raise ContractError(
                        "Package tree exceeds the entry limit.", code="source_limit"
                    )
                path = Path(entry.path)
                if is_link_or_reparse(path):
                    raise ContractError(
                        "Package trees cannot contain symlinks.", code="source_escape"
                    )
                ensure_path_within(path, root)
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    pending.append(path)
                elif stat.S_ISREG(info.st_mode):
                    total += info.st_size
                    if info.st_size > limits.file_bytes or total > limits.baseline_bytes:
                        raise ContractError("Package bytes exceed the limit.", code="source_limit")
                    files.append(path.relative_to(root).as_posix())
                else:
                    raise ContractError("Package contains a special file.", code="invalid_source")
    return tuple(sorted(files))


def source_hash(root: Path, limits: ContractLimits) -> str:
    """Delegate content identity only after bounding the traversed tree."""
    bounded_tree(root, limits)
    return compute_package_hash(root)


def validate_reference(dependency: DependencyReference) -> None:
    """Limit acquisition to ordinary local directories and Git packages."""
    if (
        dependency.source not in {None, "git", "local"}
        or dependency.is_marketplace
        or dependency.is_virtual_file()
        or dependency.is_parent_repo_inheritance
        or dependency.skill_subset
    ):
        raise ContractError(
            "Use a local APM directory or Git package reference; registry, marketplace, "
            "parent inheritance and single-file sources are unsupported.",
            code="unsupported_source",
        )


def validate_source(
    source: ContractSource, contract: LeafContract, *, limits: ContractLimits
) -> None:
    """A prepared source is not trusted: recheck package bytes and supported shape."""
    ensure_path_within(contract.path, source.root)
    ensure_path_within(contract.path, resource_root(source))
    if has_symlink_component(source.root, contract.path):
        raise ContractError("Selected package source contains a symlink.", code="source_escape")
    bounded_tree(source.root, limits)
    expected_hash = source.prepared_hash or source.package_hash
    if expected_hash is None or not verify_package_hash(source.root, expected_hash):
        raise ContractError(
            "Prepared package content changed. Prepare again.", code="source_changed"
        )
    if (
        source.original_root is not None
        and source.original_root != source.root
        and source_hash(source.original_root, limits) != source.package_hash
    ):
        raise ContractError("Original package content changed.", code="source_changed")
