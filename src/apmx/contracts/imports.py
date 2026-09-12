"""Read-only projection of selected context from official APM lock inventory."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Any

import yaml

from ..deps.lockfile import LockFile, resolve_lockfile_path_for_read
from ..models.apm_package import APMPackage
from ..utils.file_capture import capture_path_stat, open_readonly_nofollow
from ..utils.path_security import ensure_path_within, has_symlink_component
from ..utils.yaml_io import load_yaml_str, loads_frontmatter_document
from .models import (
    ContractError, ContractLimits, ImportedResource, ImportedSkill, LeafContract, SourceLocation,
)


def _read_bytes(path: Path, *, maximum: int, root: Path) -> bytes:
    """Bound selected reads and reject symlinks, special files and observed drift."""
    try:
        ensure_path_within(path, root)
        if has_symlink_component(root, path):
            raise ValueError("Nested symlinks are unsupported.")
        before = capture_path_stat(path)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise ValueError("Expected a bounded regular file.")
        with os.fdopen(open_readonly_nofollow(path), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (
                opened.st_dev, opened.st_ino,
            ):
                raise ValueError("Selected file changed before capture.")
            raw = stream.read(maximum + 1)
            after = capture_path_stat(path)
        if (
            len(raw) > maximum
            or len(raw) != before.st_size
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        ):
            raise ValueError("Selected file changed during capture.")
        return raw
    except (OSError, ValueError) as exc:
        raise ContractError(
            f"Cannot read selected file {path.name}: {exc}",
            code="import_source", location=SourceLocation(path, 1),
        ) from exc


def _package(raw: bytes, root: Path) -> tuple[APMPackage, dict[str, Any]]:
    try:
        data = load_yaml_str(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Manifest must be a mapping.")
        package = APMPackage.from_mapping(
            data, package_path=root, source_path=root, create_config=False
        )
        return package, data
    except (ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        raise ContractError(
            f"Invalid selected manifest: {exc}",
            code="invalid_manifest", location=SourceLocation(root / "apm.yml", 1),
        ) from exc


def read_project_manifest(
    root: Path, limits: ContractLimits, *, allow_missing: bool = False
) -> tuple[APMPackage, dict[str, Any], str | None]:
    manifest = root / "apm.yml"
    if allow_missing and not manifest.exists() and not manifest.is_symlink():
        package = APMPackage.from_mapping(
            {"name": "contract-caller", "version": "0.0.0"},
            package_path=root, source_path=root, create_config=False,
        )
        return package, {}, None
    raw = _read_bytes(manifest, maximum=limits.source_bytes, root=root)
    package, data = _package(raw, root)
    return package, data, hashlib.sha256(raw).hexdigest()


def read_lock(root: Path, limits: ContractLimits) -> tuple[LockFile | None, str | None]:
    path = resolve_lockfile_path_for_read(root, read_only=True)
    if not path.exists() and not path.is_symlink():
        return None, None
    raw = _read_bytes(path, maximum=limits.file_bytes, root=root)
    try:
        lock = LockFile.from_yaml(raw.decode("utf-8"))
    except (ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise ContractError("Existing lockfile is malformed.", code="invalid_lock") from exc
    return lock, hashlib.sha256(raw).hexdigest()


def _context_files(root: Path, limits: ContractLimits) -> tuple[tuple[Path, str, str], ...]:
    from ..install.contract_source_validation import bounded_tree

    files = bounded_tree(root, limits)
    nested_packages = []
    if (root / "apm.yml").exists():
        manifest, _, _ = read_project_manifest(root, limits)
        for section in (manifest.dependencies, manifest.dev_dependencies):
            for dependency in (section or {}).get("apm", []):
                if dependency.is_local:
                    child = root / (dependency.local_path or "")
                    if child.is_relative_to(root):
                        nested_packages.append(child)
    selected = []
    for name in files:
        path = Path(name)
        if any((root / path).is_relative_to(child) for child in nested_packages):
            continue
        kind = None
        if name == "SKILL.md" or (
            path.name == "SKILL.md"
            and (
                len(path.parts) == 3 and path.parts[0] == "skills"
                or len(path.parts) == 4 and path.parts[:2] == (".apm", "skills")
            )
        ):
            kind = "skill"
        elif name.startswith(".apm/instructions/") and name.endswith(".instructions.md"):
            kind = "instruction"
        if kind is None:
            continue
        raw = _read_bytes(root / name, maximum=limits.source_bytes, root=root)
        try:
            document = loads_frontmatter_document(raw, max_bytes=limits.source_bytes)
        except ValueError as exc:
            raise ContractError("Invalid selected context frontmatter.", code="invalid_import") from exc
        if not document.body.strip():
            raise ContractError("Selected context body is empty.", code="invalid_import")
        if kind == "instruction" and document.metadata.get("applyTo") not in (
            None, "*", "**", "**/*"
        ):
            raise ContractError("Scoped instruction activation is unsupported.",
                                code="unsupported_import")
        if any(document.metadata.get(key) for key in (
            "hooks", "mcp", "mcpServers", "lspServers", "agent", "model", "allowed-tools",
        )):
            raise ContractError("Selected context requires unsupported activation.",
                                code="unsupported_import")
        context_name = document.metadata.get("name") or (
            path.parent.name if kind == "skill" else path.name.removesuffix(".instructions.md")
        )
        if not isinstance(context_name, str) or not context_name.strip():
            raise ContractError("Selected context name is invalid.", code="invalid_import")
        selected.append((root / name, kind, context_name))
    return tuple(selected)


def resolve_installed_skills(
    contract: LeafContract,
    project_root: Path,
    package: APMPackage,
    *,
    limits: ContractLimits | None = None,
) -> tuple[tuple[ImportedSkill, ...], str | None]:
    """Select package/context names, never resolve or acquire dependencies.

    The public APM lock model is an inventory/path codec, not a second
    dependency resolver. APM itself owns graph traversal and frozen replay.
    """
    from ..utils.content_hash import verify_package_hash

    limits = limits or ContractLimits()
    lock, lock_digest = read_lock(project_root, limits)
    if not contract.imports:
        return (), lock_digest
    if lock is None:
        raise ContractError("Imports require an installed APM lock inventory.", code="missing_lock")
    if len(lock.dependencies) > limits.resource_files:
        raise ContractError("Import inventory exceeds its package limit.", code="import_limit")
    inventory = []
    for locked in lock.dependencies.values():
        root = locked.to_dependency_ref().get_install_path(project_root / "apm_modules")
        if has_symlink_component(project_root, root):
            raise ContractError("Installed package contains a symlink.", code="import_source")
        if not root.is_dir():
            continue
        manifest_name = None
        if (root / "apm.yml").exists():
            installed, _, _ = read_project_manifest(root, limits)
            manifest_name = installed.name
        if locked.name and manifest_name and locked.name != manifest_name:
            raise ContractError("Installed package name differs from its lock.", code="import_drift")
        aliases = {
            locked.repo_url,
            locked.get_unique_key(),
            manifest_name,
            locked.name,
        }
        if locked.virtual_path:
            aliases.add(f"{locked.repo_url}/{locked.virtual_path}")
        inventory.append((locked, root, aliases, manifest_name))
    result = []
    seen_paths: set[Path] = set()
    seen_names: set[str] = set()
    total = resource_count = 0
    for name in contract.imports:
        matches = [item for item in inventory if name in item[2]]
        if len(matches) != 1:
            raise ContractError(
                "Import must identify exactly one installed APM package.",
                code="ambiguous_import" if matches else "missing_import",
            )
        locked, root, _, package_name = matches[0]
        contexts = _context_files(root, limits)
        if locked.target_subset and not {"all", "copilot", "agent-skills"}.intersection(
            locked.target_subset
        ):
            raise ContractError("Selected package targets exclude this context adapter.",
                                code="unsupported_import")
        if locked.skill_subset and "*" not in locked.skill_subset:
            available = {
                path.parent.name for path, kind, _ in contexts if kind == "skill"
            }
            if not set(locked.skill_subset).issubset(available):
                raise ContractError("Locked skill selection is not installed.",
                                    code="missing_import")
            contexts = tuple(
                item for item in contexts
                if item[1] != "skill" or item[0].parent.name in locked.skill_subset
            )
        if locked.depth == 1:
            from ..drift import detect_ref_change

            declarations = [
                dependency
                for section in (package.dependencies, package.dev_dependencies)
                for dependency in (section or {}).get("apm", [])
                if dependency.get_unique_key() == locked.get_unique_key()
            ]
            if len(declarations) != 1 or detect_ref_change(declarations[0], locked):
                raise ContractError("Manifest and installed lock reference differ.",
                                    code="import_drift")
        elif not locked.resolved_by or not any(
            locked.resolved_by in {entry.get_unique_key(), entry.repo_url}
            for entry in lock.dependencies.values()
        ):
            raise ContractError("Transitive context lacks its parent lock identity.",
                                code="import_drift")
        if not contexts:
            raise ContractError("Selected package has no supported context.", code="missing_import")
        if locked.source == "local":
            from ..install.contract_source_validation import bounded_tree

            declared_origin = Path(locked.local_path or "")
            if locked.declaring_parent and not declared_origin.is_absolute():
                declared_origin = Path(locked.declaring_parent) / declared_origin
            if declared_origin.is_absolute():
                declared_origin = Path(os.path.abspath(declared_origin))
                if has_symlink_component(Path(declared_origin.anchor), declared_origin):
                    raise ContractError("Selected local package contains a symlink.",
                                        code="import_source")
            origin = Path(locked.anchored_local_path or locked.local_path or "")
            if origin.is_absolute() and origin.exists():
                bounded_tree(origin, limits)
        if locked.source != "local":
            if not locked.resolved_commit or not locked.content_hash:
                raise ContractError("Git import needs a locked commit and package hash.",
                                    code="import_drift")
            if not verify_package_hash(root, locked.content_hash):
                raise ContractError("Installed package hash differs from its lock.",
                                    code="import_drift")
        for path, kind, context_name in contexts:
            if path in seen_paths:
                continue
            key = context_name.casefold()
            if key in seen_names:
                raise ContractError("Selected context names collide.", code="ambiguous_import")
            seen_paths.add(path)
            seen_names.add(key)
            raw = _read_bytes(path, maximum=limits.source_bytes, root=root)
            total += len(raw)
            resource_count += 1
            resources = []
            if kind == "skill":
                from ..install.contract_source_validation import bounded_tree

                for relative in bounded_tree(path.parent, limits):
                    if Path(relative).parts[0] not in {"references", "assets", "scripts"}:
                        continue
                    resource_path = path.parent / relative
                    data = _read_bytes(resource_path, maximum=limits.file_bytes, root=root)
                    total += len(data)
                    resource_count += 1
                    resources.append(ImportedResource(
                        resource_path, relative, hashlib.sha256(data).hexdigest(), len(data)
                    ))
                if len(resources) > limits.resource_files:
                    raise ContractError("Skill resources exceed their limit.", code="import_limit")
            if resource_count > limits.resource_files or total > limits.resource_bytes:
                raise ContractError("Selected context exceeds its limit.", code="import_limit")
            result.append(ImportedSkill(
                name=name,
                source_path=path,
                content=raw.decode("utf-8"),
                source_digest=hashlib.sha256(raw).hexdigest(),
                lock_identity=locked.get_unique_key(),
                resolved_commit=locked.resolved_commit,
                verified_package_hash=locked.content_hash if locked.source != "local" else None,
                assurance="observed-local-source" if locked.source == "local" else "locked-package-hash",
                kind=kind,
                version=locked.version,
                context_name=context_name,
                source_relative_path=path.relative_to(root).as_posix(),
                resources=tuple(resources),
                resolved_ref=locked.resolved_ref or locked.resolved_tag,
                package_name=locked.name or package_name,
            ))
    return tuple(result), lock_digest
