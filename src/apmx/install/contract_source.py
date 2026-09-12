"""Prepare package sources through the bundled official APM CLI."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import tempfile

from apmx.contracts.frontend import admit_caller_policy, package_contract_path, parse_contract
from apmx.contracts.imports import (
    _read_bytes, read_lock, read_project_manifest, resolve_installed_skills,
)
from apmx.contracts.models import ContractError, ContractLimits, ContractSource, Outcome
from apmx.contracts.native_integrity import verify_inventory_package
from apmx.install import apm_backend
from apmx.install.contract_source_validation import source_hash, validate_reference
from apmx.models.dependency.reference import DependencyReference
from apmx.models.dependency.selection import (
    DependencySelectionStatus,
    select_manifest_dependency,
)
from apmx.utils.path_security import has_symlink_component, safe_rmtree
from apmx.deps.lockfile import resolve_lockfile_path_for_read


@contextmanager
def _private_root(caller_root: Path, original_root: Path | None) -> Iterator[Path]:
    parent = Path(tempfile.gettempdir()).resolve()
    if parent.is_relative_to(caller_root) or (
        original_root is not None and parent.is_relative_to(original_root)
    ):
        raise ContractError(
            "Temporary package staging must be outside the caller and source.",
            code="source_escape",
        )
    # Native APM adds deep transactional Git paths; do not amplify caller depth.
    directory = Path(tempfile.mkdtemp(prefix="apmx-", dir=parent))
    try:
        yield directory
    finally:
        try:
            safe_rmtree(directory, parent)
        except OSError:
            raise ContractError(
                "Temporary package source cleanup failed. "
                "Inspect any retained run record and resolve filesystem permissions or locks.",
                code="source_cleanup",
            ) from None


def _original_bytes(root: Path, limits: ContractLimits) -> tuple[bytes, bytes | None]:
    manifest = _read_bytes(root / "apm.yml", maximum=limits.source_bytes, root=root)
    path = resolve_lockfile_path_for_read(root, read_only=True)
    lock = _read_bytes(path, maximum=limits.file_bytes, root=root) if path.exists() else None
    return manifest, lock


def _selected_source(stage: Path, requested: DependencyReference, limits: ContractLimits):
    lock, _ = read_lock(stage, limits)
    matches = [] if lock is None else [
        entry for entry in lock.dependencies.values()
        if entry.depth == 1 and (
            entry.to_dependency_ref().get_identity() == requested.get_identity()
            or (
                requested.is_local and entry.source == "local"
                and entry.local_path == requested.local_path
            )
        )
    ]
    if len(matches) != 1:
        raise ContractError(
            "The requested package has no unique installed APM lock identity.",
            code="unresolved_source",
            outcome=Outcome.UNPROVEN,
        )
    locked = matches[0]
    _validate_source_pin(requested, locked)
    root = locked.to_dependency_ref().get_install_path(stage / "apm_modules")
    if not root.is_dir() or has_symlink_component(stage, root):
        raise ContractError("Selected package is not safely installed.", code="source_changed")
    managed_metadata = ()
    if locked.content_hash:
        managed_metadata = verify_inventory_package(
            stage, locked, lock, limits, error_code="source_changed",
        )
    return root.resolve(), locked, managed_metadata


def _validate_source_pin(requested, locked):
    from apmx.drift import detect_ref_change
    from apmx.utils.github_host import is_full_commit_sha

    if detect_ref_change(requested, locked) or (
        not requested.is_local and (
            not is_full_commit_sha(locked.resolved_commit) or not locked.content_hash
        )
    ):
        raise ContractError(
            "Requested source differs from its exact caller lock reference.",
            code="unresolved_source", outcome=Outcome.UNPROVEN,
        )


@contextmanager
def prepare_contract_source(
    package_ref: str,
    contract_relative_path: str,
    *,
    caller_root: Path,
    planning: bool,
    limits: ContractLimits,
) -> Iterator[ContractSource]:
    """Never run APM from the real caller or package checkout."""
    admit_caller_policy(caller_root, limits=limits)
    try:
        requested = DependencyReference.parse(package_ref)
        validate_reference(requested)
        package_contract_path(caller_root, contract_relative_path)
        caller_package, _, caller_manifest_digest = read_project_manifest(
            caller_root, limits, allow_missing=True
        )
        caller_lock, caller_lock_digest = read_lock(caller_root, limits)
        declarations = list((caller_package.dependencies or {}).get("apm", []))
        declarations.extend((caller_package.dev_dependencies or {}).get("apm", []))
        selection = select_manifest_dependency(package_ref, declarations, caller_lock)
        if selection.status == DependencySelectionStatus.AMBIGUOUS:
            raise ContractError("Caller package declaration is ambiguous.", code="unresolved_source")
        if selection.status == DependencySelectionStatus.MATCHED and caller_lock is not None:
            from apmx.models.dependency.selection import parse_dependency_entry

            declared = parse_dependency_entry(selection.manifest_entry)
            locked = caller_lock.get_dependency(declared.get_unique_key())
            if locked is None:
                raise ContractError("Caller source is missing from its lock.", code="invalid_lock")
            _validate_source_pin(requested, locked)
            _validate_source_pin(declared, locked)
            installed = locked.to_dependency_ref().get_install_path(caller_root / "apm_modules")
            if not requested.is_local and (installed.exists() or installed.is_symlink()):
                _selected_source(caller_root, requested, limits)
        original = None
        original_hash = None
        manifest = lock_bytes = None
        if requested.is_local:
            path = Path(requested.local_path or "").expanduser()
            path = path if path.is_absolute() else caller_root / path
            if has_symlink_component(Path(path.anchor), path):
                raise ContractError("Local package contains a symlink.", code="source_escape")
            original = path.resolve()
            original_hash = source_hash(original, limits)
            manifest, lock_bytes = _original_bytes(original, limits)
            read_lock(original, limits)
            parse_contract(package_contract_path(original, contract_relative_path), limits=limits)
            requested = DependencyReference.parse(str(original))
            if planning:
                context_root = caller_root if declarations or caller_lock else original
                context_package, _, _ = read_project_manifest(
                    context_root, limits, allow_missing=True
                )
                contract = parse_contract(
                    package_contract_path(original, contract_relative_path), limits=limits
                )
                try:
                    resolve_installed_skills(contract, context_root, context_package, limits=limits)
                except ContractError as exc:
                    if exc.code not in {"missing_lock", "missing_import"}:
                        raise
                    raise ContractError(
                        "Imported context is unresolved offline; install it explicitly first.",
                        code="unresolved_import", outcome=Outcome.UNPROVEN,
                    ) from exc
                yield ContractSource(
                    original, contract_relative_path, package_ref,
                    package_hash=original_hash, imports_root=original,
                )
                return
        elif planning:
            if selection.status != DependencySelectionStatus.MATCHED or caller_lock is None:
                raise ContractError(
                    "Remote source is unresolved offline; install it explicitly first.",
                    code="unresolved_source", outcome=Outcome.UNPROVEN,
                )
            root, locked, managed_metadata = _selected_source(caller_root, requested, limits)
            yield ContractSource(
                root, contract_relative_path, package_ref, locked.resolved_commit,
                source_hash(root, limits), "locked-package-hash",
                imports_root=caller_root,
                managed_metadata=managed_metadata,
            )
            return
        with _private_root(caller_root, original) as private:
            stage = private / "install"
            stage.mkdir(mode=0o700)
            if caller_manifest_digest is not None:
                frozen = apm_backend.snapshot_manifest(caller_root, stage, limits)
                established, _ = read_lock(stage, limits)
                staged_package, _, _ = read_project_manifest(stage, limits)
                staged_declarations = list((staged_package.dependencies or {}).get("apm", []))
                staged_declarations.extend((staged_package.dev_dependencies or {}).get("apm", []))
                request = str(original) if original else package_ref
                staged_selection = select_manifest_dependency(
                    request, staged_declarations, established
                )
                if staged_selection.status == DependencySelectionStatus.AMBIGUOUS:
                    raise ContractError("Caller package declaration is ambiguous.",
                                        code="unresolved_source")
                if staged_selection.status != DependencySelectionStatus.MATCHED:
                    apm_backend.add_package_request(stage, request, limits)
                    frozen = False
                identity = apm_backend.install(stage, frozen=frozen, limits=limits)
                installed_lock, _ = read_lock(stage, limits)
                apm_backend.require_preserved_pins(established, installed_lock)
            else:
                if caller_lock is not None:
                    raise ContractError("Consumer lock requires its manifest.",
                                        code="invalid_manifest")
                identity = apm_backend.install(
                    stage, package_ref=str(original) if original else package_ref, limits=limits
                )
            _, _, current_manifest = read_project_manifest(caller_root, limits, allow_missing=True)
            _, current_lock = read_lock(caller_root, limits)
            if (current_manifest, current_lock) != (caller_manifest_digest, caller_lock_digest):
                raise ContractError("Consumer declarations changed during preparation.",
                                    code="plan_changed")
            root, locked, managed_metadata = _selected_source(stage, requested, limits)
            parse_contract(package_contract_path(root, contract_relative_path), limits=limits)
            if original and source_hash(original, limits) != original_hash:
                raise ContractError("Original package changed during preparation.",
                                    code="source_changed")
            yield ContractSource(
                root, contract_relative_path, package_ref, locked.resolved_commit,
                original_hash or source_hash(root, limits),
                "observed-local-source" if original else "observed-resolved-source",
                prepared_hash=source_hash(root, limits),
                original_root=original,
                original_manifest=manifest,
                original_lock=lock_bytes,
                imports_root=stage,
                apm_backend=identity,
                managed_metadata=managed_metadata,
            )
    except (ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError("Invalid APM package source.", code="invalid_source") from exc


@contextmanager
def prepare_imports(
    caller_root: Path,
    contract_path: Path,
    *,
    source: ContractSource | None,
    planning: bool,
    limits: ContractLimits,
) -> Iterator[tuple[Path, dict[str, str] | None]]:
    """The consumer manifest/lock wins over a packaged contract's dependencies."""
    admit_caller_policy(caller_root, limits=limits)
    contract = parse_contract(contract_path, limits=limits)
    package, _, manifest_digest = read_project_manifest(caller_root, limits, allow_missing=True)
    lock, _ = read_lock(caller_root, limits)
    declarations = list((package.dependencies or {}).get("apm", []))
    declarations.extend((package.dev_dependencies or {}).get("apm", []))
    consumer = bool(declarations or lock is not None)
    fallback = source.imports_root or source.root if source else caller_root
    if planning or not contract.imports or not consumer:
        yield (
            caller_root if consumer else fallback,
            dict(source.apm_backend) if source and source.apm_backend else None,
        )
        return
    if manifest_digest is None:
        raise ContractError("Consumer lock requires its manifest.", code="invalid_manifest")
    before = _original_bytes(caller_root, limits)
    with _private_root(caller_root, None) as private:
        stage = private / "consumer"
        stage.mkdir(mode=0o700)
        frozen = apm_backend.snapshot_manifest(caller_root, stage, limits)
        identity = apm_backend.install(stage, frozen=frozen, limits=limits)
        if _original_bytes(caller_root, limits) != before:
            raise ContractError("Consumer declarations changed during preparation.",
                                code="plan_changed")
        yield stage, identity
