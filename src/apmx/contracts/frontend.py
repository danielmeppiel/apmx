"""Strict leaf parsing and read-only admission; never execute or install."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path

from ..utils.path_security import (
    PathTraversalError,
    ensure_path_within,
    has_symlink_component,
    validate_path_segments,
)
from ..utils.yaml_io import FrontmatterSourceError, load_frontmatter_document
from .models import (
    CheckSpec,
    ContractError,
    ContractLimits,
    ContractSource,
    FileEntry,
    LeafContract,
    LeafPlan,
    Outcome,
    RetainedInput,
    SourceLocation,
)


def _fixed_path(value: object, location: SourceLocation, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or not re.fullmatch(r"[A-Za-z0-9_./ -]+", value)
        or Path(value).is_absolute()
    ):
        raise ContractError(
            f"{field} requires one fixed relative file path, without globs or captures.",
            code="invalid_path",
            location=location,
        )
    try:
        validate_path_segments(value, context=field, reject_empty=True)
    except PathTraversalError as exc:
        raise ContractError(str(exc), code="invalid_path", location=location) from exc
    if any(part.casefold() in {".git", ".apm", "apm_modules"} for part in Path(value).parts):
        raise ContractError("Managed state cannot be a contract artifact.", location=location)
    return value


def _regular(path: Path, root: Path, location: SourceLocation) -> os.stat_result:
    try:
        ensure_path_within(path, root)
        if has_symlink_component(root, path):
            raise ValueError("Symlink paths are unsupported.")
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Expected a regular file.")
        return info
    except (OSError, ValueError) as exc:
        raise ContractError(
            f"Cannot select regular file {path.name}: {exc}",
            code="invalid_file",
            location=location,
        ) from exc


def package_contract_path(root: Path, name: str) -> Path:
    """Confine an explicitly selected package contract without discovering a default."""
    validate_path_segments(name, context="package contract", reject_empty=True)
    if Path(name).is_absolute() or not name.endswith(".contract.md"):
        raise ContractError("Select one package-relative .contract.md file.", code="invalid_source")
    selected = root / name
    ensure_path_within(selected, root)
    if has_symlink_component(root, selected):
        raise ContractError("Selected contract contains a symlink.", code="source_escape")
    return selected


def parse_contract(path: Path, *, limits: ContractLimits | None = None) -> LeafContract:
    """Parse one local agent-only source without resolving files or runtimes."""
    limits = limits or ContractLimits()
    path = path.absolute()
    location = SourceLocation(path, 1)
    if not path.name.endswith(".contract.md"):
        raise ContractError("Expected a local .contract.md file.", location=location)
    _regular(path, path.parent, location)
    try:
        document = load_frontmatter_document(path, max_bytes=limits.source_bytes)
    except FrontmatterSourceError as exc:
        raise ContractError(
            str(exc), code=exc.code, location=SourceLocation(path, exc.line, exc.column)
        ) from exc
    except OSError as exc:
        raise ContractError("Cannot read contract source.", location=location) from exc

    def at(*key: str | int) -> SourceLocation:
        mark = document.locations.get(key)
        while mark is None and key:
            key = key[:-1]
            mark = document.locations.get(key)
        return SourceLocation(path, mark.line, mark.column) if mark else location

    data = document.metadata
    allowed = {"needs", "produces", "verify", "imports", "run", "budget", "sandbox"}
    for key in data:
        if key not in allowed:
            raise ContractError(f"Unknown contract field: {key}.", location=at(key))
    for key in ("run", "budget", "sandbox"):
        if key in data:
            raise ContractError(
                f"{key} is unsupported by the native agent-only contract profile.",
                code="unsupported_control",
                location=at(key),
                outcome=Outcome.UNPROVEN,
            )
    if not document.body.strip() or "\0" in document.body:
        raise ContractError("Contract Markdown body must not be empty.", location=at("$body"))
    needs = data.get("needs", [])
    if isinstance(needs, str):
        needs = [needs]
    if not isinstance(needs, list) or len(needs) > min(16, limits.input_files):
        raise ContractError(
            "needs must be a scalar or a list of at most 16 files.", location=at("needs")
        )
    paths = tuple(
        _fixed_path(value, at("needs", index), "needs") for index, value in enumerate(needs)
    )
    if len({value.casefold() for value in paths}) != len(paths):
        raise ContractError(
            "needs contains duplicate or case-colliding paths.", location=at("needs")
        )
    declared = data.get("produces")
    if isinstance(declared, list):
        if not 1 <= len(declared) <= limits.output_files:
            raise ContractError(
                f"produces must name between one and {limits.output_files} artifact files.",
                location=at("produces"),
            )
        produces = tuple(
            _fixed_path(value, at("produces", index), "produces")
            for index, value in enumerate(declared)
        )
        names = [name.casefold() for name in produces]
        if len(set(names)) != len(names) or any(
            left.startswith(right + "/") or right.startswith(left + "/")
            for index, left in enumerate(names)
            for right in names[index + 1 :]
        ):
            raise ContractError(
                "Produced artifact paths collide or overlap.", location=at("produces")
            )
    else:
        produces = _fixed_path(declared, at("produces"), "produces")
    verify = data.get("verify")
    if not isinstance(verify, dict) or not 1 <= len(verify) <= min(8, limits.check_count):
        raise ContractError(
            "verify must name between one and eight string commands.", location=at("verify")
        )
    checks = []
    for name, command in verify.items():
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
            raise ContractError(
                "Check names must be simple nonempty identifiers.", location=at("verify", name)
            )
        if not isinstance(command, str) or not command.strip() or "\0" in command:
            raise ContractError(
                "Each check must be a nonempty command string.", location=at("verify", name)
            )
        checks.append(CheckSpec(name, command, at("verify", name)))
    imports = data.get("imports", [])
    if not isinstance(imports, list) or len(imports) > limits.resource_files:
        raise ContractError(
            "imports must be a bounded list of installed package/context names.",
            location=at("imports"),
        )
    for name in imports:
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]*", name)
            or name.startswith(("apm_modules/", "_local/"))
        ):
            raise ContractError(
                "An import names a declared skill/dependency, not a storage path or version.",
                location=at("imports"),
            )
        try:
            validate_path_segments(name, context="import", reject_empty=True)
        except PathTraversalError as exc:
            raise ContractError(str(exc), location=at("imports")) from exc
    if len({name.casefold() for name in imports}) != len(imports):
        raise ContractError(
            "imports contains duplicate or case-colliding names.", location=at("imports")
        )
    return LeafContract(
        path=path,
        source_digest=hashlib.sha256(document.raw).hexdigest(),
        body=document.body,
        needs=paths,
        produces=produces,
        checks=tuple(checks),
        imports=tuple(imports),
        locations={str(key[0]): at(*key) for key in document.locations if len(key) == 1},
    )


def admit_caller_policy(project_root: Path, *, limits: ContractLimits) -> None:
    """Require positive no-policy governance at the original caller root."""
    from ..policy.prerequisite import require_no_policy
    from .imports import read_project_manifest

    root = project_root.resolve()
    _, manifest_data, _ = read_project_manifest(root, limits, allow_missing=True)
    require_no_policy(root, manifest_data)
    if os.environ.get("APM_NO_SCRIPTS"):
        raise ContractError(
            "APM_NO_SCRIPTS disables contract checks.",
            code="scripts_disabled",
            outcome=Outcome.UNPROVEN,
        )


def plan_contract(
    path: Path,
    project_root: Path,
    *,
    harness: str,
    model: str | None = None,
    limits: ContractLimits | None = None,
    source: ContractSource | None = None,
    imports_root: Path | None = None,
    apm_backend: Mapping[str, str] | None = None,
    deferred_inputs: tuple[str, ...] = (),
    input_bindings: tuple[RetainedInput, ...] = (),
    chain_outputs: tuple[str, ...] = (),
    input_inventory: tuple[FileEntry, ...] | None = None,
) -> LeafPlan:
    """Resolve a bounded leaf using local reads only; no version/inference probe."""
    from ..runtime.registry import get_runtime_descriptor
    from ..runtime.utils import find_runtime_binary
    from .imports import read_lock, read_project_manifest, resolve_installed_skills

    limits = limits or ContractLimits()
    root = project_root.resolve()
    source_root = source.root if source else root
    if source and (not source_root.is_absolute() or source_root != source_root.resolve()):
        raise ContractError("Package source root must be absolute and normalized.")
    selected = path if path.is_absolute() else source_root / path
    source_location = SourceLocation(selected, 1)
    try:
        validate_path_segments(str(selected), context="contract source", allow_current_dir=True)
        if source:
            validate_path_segments(
                source.contract_relative_path, context="package contract", reject_empty=True
            )
            if Path(source.contract_relative_path).is_absolute():
                raise ValueError("Package contracts require a relative file path.")
            if selected != source_root / source.contract_relative_path:
                raise ValueError("Selected contract differs from prepared source.")
    except (PathTraversalError, ValueError) as exc:
        raise ContractError(str(exc), location=source_location) from exc
    _regular(selected, source_root, source_location)
    contract = parse_contract(selected, limits=limits)
    location = contract.locations.get("produces", source_location)
    occupied = {
        *(value.casefold() for value in contract.needs),
        ("_apmx_source" if source else contract.path.relative_to(root).as_posix().casefold()),
        "apm.yml",
        "apm.lock.yaml",
        "apm.lock",
    }
    from .context_layout import NATIVE_SKILL_ROOTS, is_native_skill_path

    for name in contract.outputs:
        output = root / name
        try:
            ensure_path_within(output, root)
            if has_symlink_component(root, output):
                raise ValueError("Output contains a symlink.")
            if output.exists() and not output.is_file():
                raise ValueError("Output must be a regular file path.")
        except (OSError, ValueError) as exc:
            raise ContractError(str(exc), location=location) from exc
        output_name = name.casefold()
        if (
            output_name in {"checks", "_apmx_context"}
            or output_name.startswith(("checks/", "_apmx_context/"))
            or is_native_skill_path(output_name)
            or any(name.startswith(output_name + "/") for name in NATIVE_SKILL_ROOTS)
            or any(
                output_name == item
                or output_name.startswith(item + "/")
                or item.startswith(output_name + "/")
                for item in occupied
            )
        ):
            raise ContractError(
                "Output overlaps supplied input, source, manifest or checks.", location=location
            )
    size = 0
    supplied = tuple(binding.artifact.relative_path for binding in input_bindings)
    bound_names = {*deferred_inputs, *supplied}
    if len(bound_names) != len(deferred_inputs) + len(supplied) or not bound_names <= set(
        contract.needs
    ):
        raise ContractError(
            "Deferred/bound inputs must be distinct declared needs.", code="invalid_binding"
        )
    from .records import validate_binding

    for binding in input_bindings:
        validate_binding(binding, root, limits)
        size += binding.artifact.size
    for name in contract.needs:
        if name in deferred_inputs or name in supplied:
            continue
        info = _regular(root / name, root, contract.locations.get("needs", source_location))
        size += info.st_size
        if info.st_size > limits.file_bytes or size > limits.input_bytes:
            raise ContractError("Selected inputs exceed the byte limit.", code="input_limit")
    if size > limits.input_bytes:
        raise ContractError("Selected inputs exceed the byte limit.", code="input_limit")
    admit_caller_policy(root, limits=limits)
    _, _, manifest_digest = read_project_manifest(source_root, limits, allow_missing=source is None)
    consumer, _, consumer_manifest_digest = read_project_manifest(root, limits, allow_missing=True)
    consumer_lock, consumer_lock_digest = read_lock(root, limits)
    if imports_root is None:
        has_consumer = bool(
            (consumer.dependencies or {}).get("apm")
            or (consumer.dev_dependencies or {}).get("apm")
            or consumer_lock is not None
        )
        imports_root = (
            root if has_consumer else (source.imports_root or source.root) if source else root
        )
    import_package, _, _ = read_project_manifest(imports_root, limits, allow_missing=True)
    if source:
        from ..install.contract_source_validation import validate_source

        validate_source(source, contract, limits=limits)
    try:
        descriptor = get_runtime_descriptor(harness)
    except ValueError as exc:
        raise ContractError(str(exc), code="unsupported_harness", outcome=Outcome.UNPROVEN) from exc
    if not descriptor.supports_contracts:
        raise ContractError(
            f"Runtime {harness} does not support native contracts.",
            code="unsupported_harness",
            outcome=Outcome.UNPROVEN,
        )
    if model is not None and (not isinstance(model, str) or not model.strip() or "\0" in model):
        raise ContractError("Model must be a nonempty native model identifier.")
    binary = find_runtime_binary(descriptor.binary)
    if binary is None:
        raise ContractError(
            "The selected native runtime executable is missing.", code="runtime_missing"
        )
    executable = Path(binary).resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ContractError("The selected runtime is not executable.", code="runtime_missing")
    skills, lock_digest = resolve_installed_skills(
        contract, imports_root, import_package, limits=limits
    )
    return LeafPlan(
        contract=contract,
        project_root=root,
        executable=executable,
        harness=harness,
        model=model,
        limits=limits,
        imported_skills=skills,
        manifest_digest=manifest_digest,
        lock_digest=lock_digest,
        source=source,
        evidence_root=root / ".apm" / "runs",
        imports_root=imports_root,
        apm_backend=apm_backend or (source.apm_backend if source else None),
        consumer_manifest_digest=consumer_manifest_digest,
        consumer_lock_digest=consumer_lock_digest,
        deferred_inputs=deferred_inputs,
        input_bindings=input_bindings,
        chain_outputs=chain_outputs,
        input_inventory=input_inventory,
    )
