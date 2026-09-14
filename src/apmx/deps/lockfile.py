"""APM-compatible direct dependency lock records (no deployment engine)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.host_providers import accepted_host_types
from ..models.dependency.identity import normalize_package_repo_url
from ..models.dependency.reference import (
    DependencyReference,
    build_canonical_dependency_string,
    build_dependency_unique_key,
)

_ALLOWED_HOST_TYPES = set(accepted_host_types())
_ALLOWED_EXEC_STATUS = {"deployed", "gated_pending_approval", "denied", "absent"}
SUPPORTED_LOCKFILE_VERSIONS = frozenset({"1", "2"})


@dataclass
class LockFile:
    lockfile_version: str = "1"
    dependencies: dict[str, LockedDependency] = field(default_factory=dict)
    mcp_servers: list[str] = field(default_factory=list)
    lsp_servers: list[str] = field(default_factory=list)

    def add_dependency(self, dependency: LockedDependency) -> None:
        self.dependencies[dependency.get_unique_key()] = dependency

    def get_dependency(self, key: str) -> LockedDependency | None:
        return self.dependencies.get(key)

    def get_all_dependencies(self) -> list[LockedDependency]:
        return sorted(self.dependencies.values(), key=lambda dep: (dep.depth, dep.repo_url))

    @classmethod
    def from_yaml(cls, text: str) -> LockFile:
        from ..utils.yaml_io import load_yaml_str

        data = load_yaml_str(text)
        version = require_supported_lockfile_version(data)
        for name in ("dependencies", "mcp_servers", "lsp_servers"):
            if data.get(name) is not None and not isinstance(data[name], list):
                raise LockfileFormatError(f"Lockfile {name} must be a list.")
        lock = cls(version)
        for entry in data.get("dependencies") or []:
            if not isinstance(entry, dict):
                raise LockfileFormatError("Lockfile dependency must be a mapping.")
            dependency = LockedDependency.from_dict(entry)
            key = dependency.get_unique_key()
            if key in lock.dependencies:
                raise LockfileFormatError("Duplicate dependency identity in lockfile.")
            lock.add_dependency(dependency)
        lock.mcp_servers = data.get("mcp_servers") or []
        lock.lsp_servers = data.get("lsp_servers") or []
        return lock

    def to_yaml(self) -> str:
        from ..utils.yaml_io import yaml_to_str

        return yaml_to_str(
            {
                "lockfile_version": self.lockfile_version,
                "dependencies": [dep.to_dict() for dep in self.get_all_dependencies()],
                "mcp_servers": self.mcp_servers,
                "lsp_servers": self.lsp_servers,
            }
        )

    def write(self, path: Path) -> None:
        from ..utils.atomic_io import atomic_write_text

        atomic_write_text(path, self.to_yaml())

    @classmethod
    def read(cls, path: Path) -> LockFile | None:
        return cls.from_yaml(path.read_text(encoding="utf-8")) if path.exists() else None


def get_lockfile_path(root: Path) -> Path:
    return root / "apm.lock.yaml"


def resolve_lockfile_path_for_read(root: Path, *, read_only: bool = False) -> Path:
    canonical = get_lockfile_path(root)
    legacy = root / "apm.lock"
    return legacy if not canonical.exists() and legacy.exists() else canonical


class LockfileFormatError(ValueError):
    """Raised when a lockfile container does not match its schema."""


class UnsupportedLockfileVersionError(LockfileFormatError):
    """Raised when a lockfile declares a version this client cannot read."""


def require_supported_lockfile_version(data: object) -> str:
    """Return a supported declared version or fail closed."""
    if not isinstance(data, dict):
        raise LockfileFormatError("Lockfile root must be a mapping")
    version = data.get("lockfile_version", "1")
    if not isinstance(version, str) or version not in SUPPORTED_LOCKFILE_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_LOCKFILE_VERSIONS))
        raise UnsupportedLockfileVersionError(
            f"Unsupported lockfile version {version!r}; supported versions: {supported}"
        )
    return version


def _normalize_lockfile_host_type(raw: Any) -> str | None:
    """Validate and normalize the optional lockfile host_type field."""
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("lockfile host_type must be a non-empty string")
    value = raw.strip().lower()
    if value not in _ALLOWED_HOST_TYPES:
        raise ValueError(
            f"Unsupported lockfile host_type: {raw}. Supported values: "
            f"{', '.join(sorted(_ALLOWED_HOST_TYPES))}"
        )
    return value


def _normalize_exec_status(raw: Any) -> str | None:
    """Validate and normalize the optional executable-trust status."""
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("lockfile exec_status must be a non-empty string")
    value = raw.strip()
    if value not in _ALLOWED_EXEC_STATUS:
        raise ValueError(
            f"Unsupported lockfile exec_status: {raw}. Supported values: "
            f"{', '.join(sorted(_ALLOWED_EXEC_STATUS))}"
        )
    return value


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    """Return values without duplicates, preserving first-seen order."""
    return list(dict.fromkeys(values))


@dataclass
class LockedDependency:
    """A resolved dependency with exact commit/version information."""

    repo_url: str
    materialization_repo_url: str | None = None
    host: str | None = None
    host_type: str | None = None
    port: int | None = None  # Non-standard SSH/HTTPS port (e.g. 7999 for Bitbucket DC)
    registry_prefix: str | None = None  # Registry path prefix, e.g. "artifactory/github"
    resolved_commit: str | None = None
    resolved_ref: str | None = None
    version: str | None = None
    virtual_path: str | None = None
    is_virtual: bool = False
    depth: int = 1
    resolved_by: str | None = None
    package_type: str | None = None
    deployed_files: list[str] = field(default_factory=list)
    deployed_file_hashes: dict[str, str] = field(default_factory=dict)
    source: str | None = None  # "local" for local deps, None/absent for remote
    local_path: str | None = None  # Original local path. Direct deps: relative to
    # the project root (``./packages/foo``). Transitive deps: relative to the
    # package that declared them (``../sibling``), anchored via ``resolved_by``
    # (issue #857; see apmx.deps.path_anchoring).
    declaring_parent: str | None = None
    anchored_local_path: str | None = None
    content_hash: str | None = None  # SHA-256 of package file tree
    is_dev: bool = False  # True for devDependencies
    discovered_via: str | None = None  # Marketplace name (provenance)
    marketplace_plugin_name: str | None = None  # Plugin name in marketplace
    source_url: str | None = None  # Canonical marketplace source URL
    source_digest: str | None = None  # sha256 digest of the marketplace manifest
    is_insecure: bool = False  # True when the locked source was http://
    allow_insecure: bool = False  # True when the manifest explicitly allowed HTTP
    skill_subset: list[str] = field(default_factory=list)  # Sorted skill names for SKILL_BUNDLE
    target_subset: list[str] = field(default_factory=list)  # Audit-only consumer target subset

    # Registry resolver fields (design §6.1).
    # Populated when source == "registry"; absent otherwise. resolved_hash is
    # the sole non-negotiable trust anchor on every install — bytes are fetched
    # from resolved_url and re-verified against this digest.
    resolved_url: str | None = None
    resolved_hash: str | None = None

    # Git-source semver resolution fields (issue #1488).
    # Populated when a git-source dependency carried a semver range
    # (e.g. ``^1.2.0``) that the install pipeline resolved against the
    # remote's tags. Lockfile version stays at "2" -- these fields are
    # purely additive and forward-compatible with old readers (which
    # ignore unknown keys via the explicit ``from_dict`` allowlist).
    constraint: str | None = None
    resolved_tag: str | None = None
    resolved_at: str | None = None

    # Declared-license provenance (issue #1777, U6). The SPDX expression the
    # dependency's manifest DECLARED at resolved_commit (apm.yml ``license:``
    # or plugin.json ``license``). This is a passthrough of an author claim --
    # APM never reads the LICENSE file text or concludes a license. Absence
    # means "not declared" (unknown); it is OMITTED from the serialized entry
    # rather than stored as a sentinel, so absence stays distinguishable from
    # an explicit declaration.
    declared_license: str | None = None
    # Resolved executable-trust state (issue #1873). One of ``deployed`` |
    # ``gated_pending_approval`` | ``denied`` | ``absent``, mirroring the
    # resolver ``trust_state``. Absence (``None``) means the package declared
    # no executable primitive; it is OMITTED from the serialized entry so a
    # never-gated package stays distinguishable from an explicitly-cleared one.
    exec_status: str | None = None
    # Package-declared name from the dependency's own apm.yml (issue #1888).
    # SELF-ASSERTED display/inventory metadata only -- NOT an identity anchor.
    # See to_dict/from_dict and the supply-chain boundary note in the lockfile
    # spec. Omitted from the serialized entry when absent.
    name: str | None = None
    # Forward-compat carrier: keys we don't recognise are preserved
    # through a from_dict / to_dict round-trip so an older APM build
    # reading a lockfile written by a newer build doesn't silently drop
    # fields when it re-emits.
    _unknown_fields: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Separate canonical lock identity from materialization spelling."""
        original_repo_url = self.repo_url
        canonical_repo_url = normalize_package_repo_url(
            self.repo_url,
            host=self.host,
            source=self.source,
            registry_prefix=self.registry_prefix,
        )
        materialization_repo_url = self.materialization_repo_url or original_repo_url
        materialization_identity = normalize_package_repo_url(
            materialization_repo_url,
            host=self.host,
            source=self.source,
            registry_prefix=self.registry_prefix,
        )
        if materialization_identity != canonical_repo_url:
            raise ValueError(
                f"materialization_repo_url {materialization_repo_url!r} does not "
                f"identify the same package as repo_url {self.repo_url!r}"
            )
        self.repo_url = canonical_repo_url
        self.materialization_repo_url = (
            materialization_repo_url if materialization_repo_url != canonical_repo_url else None
        )

    def get_unique_key(self) -> str:
        """Returns unique key for this dependency."""
        return build_dependency_unique_key(
            self.repo_url,
            host=self.host,
            source=self.source,
            local_path=self.local_path,
            is_virtual=self.is_virtual,
            virtual_path=self.virtual_path,
            registry_prefix=self.registry_prefix,
            declaring_parent=self.declaring_parent,
            anchored_local_path=self.anchored_local_path,
        )

    def get_canonical_dependency_string(self) -> str:
        """Host-blind canonical key for filesystem / orphan-detection matching.

        Mirrors :meth:`DependencyReference.get_canonical_dependency_string`:
        returns the bare ``repo_url`` (+ ``virtual_path``), never host-qualified,
        so it matches the host-blind ``apm_modules/`` layout. Use
        :meth:`get_unique_key` for the host-qualified lockfile dedup key.
        """
        return build_canonical_dependency_string(
            self.repo_url,
            is_local=(self.source == "local"),
            local_path=self.local_path,
            is_virtual=self.is_virtual,
            virtual_path=self.virtual_path,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for YAML output."""
        result: dict[str, Any] = {"repo_url": self.repo_url}
        if self.materialization_repo_url:
            result["materialization_repo_url"] = self.materialization_repo_url
        if self.name is not None:
            result["name"] = self.name
        if self.host:
            result["host"] = self.host
        if self.host_type:
            result["host_type"] = self.host_type
        if self.port:
            result["port"] = self.port
        if self.registry_prefix:
            result["registry_prefix"] = self.registry_prefix
        if self.resolved_commit:
            result["resolved_commit"] = self.resolved_commit
        if self.resolved_ref:
            result["resolved_ref"] = self.resolved_ref
        if self.version:
            result["version"] = self.version
        if self.virtual_path:
            result["virtual_path"] = self.virtual_path
        if self.is_virtual:
            result["is_virtual"] = self.is_virtual
        if self.depth != 1:
            result["depth"] = self.depth
        if self.resolved_by:
            result["resolved_by"] = self.resolved_by
        if self.package_type:
            result["package_type"] = self.package_type
        if self.deployed_files:
            result["deployed_files"] = sorted(_dedupe_preserving_order(self.deployed_files))
        if self.deployed_file_hashes:
            result["deployed_file_hashes"] = dict(sorted(self.deployed_file_hashes.items()))
        if self.source:
            result["source"] = self.source
        if self.local_path:
            result["local_path"] = self.local_path
        if self.declaring_parent:
            result["declaring_parent"] = self.declaring_parent
        if self.anchored_local_path:
            result["anchored_local_path"] = self.anchored_local_path
        if self.content_hash:
            result["content_hash"] = self.content_hash
        if self.is_dev:
            result["is_dev"] = True
        if self.discovered_via:
            result["discovered_via"] = self.discovered_via
        if self.marketplace_plugin_name:
            result["marketplace_plugin_name"] = self.marketplace_plugin_name
        if self.source_url:
            result["source_url"] = self.source_url
        if self.source_digest:
            result["source_digest"] = self.source_digest
        if self.is_insecure:
            result["is_insecure"] = True
        if self.allow_insecure:
            result["allow_insecure"] = True
        if self.skill_subset:
            result["skill_subset"] = sorted(self.skill_subset)
        if self.target_subset:
            result["target_subset"] = sorted(self.target_subset)
        if self.resolved_url:
            result["resolved_url"] = self.resolved_url
        if self.resolved_hash:
            result["resolved_hash"] = self.resolved_hash
        if self.constraint:
            result["constraint"] = self.constraint
        if self.resolved_tag:
            result["resolved_tag"] = self.resolved_tag
        if self.resolved_at:
            result["resolved_at"] = self.resolved_at
        if self.declared_license:
            result["declared_license"] = self.declared_license
        if self.exec_status:
            result["exec_status"] = self.exec_status
        # Replay forward-compat unknown fields LAST so they never shadow a
        # known field that this build understands.
        for k, v in self._unknown_fields.items():
            result.setdefault(k, v)
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LockedDependency:
        """Deserialize from dict.

        Handles backwards compatibility:
        - Old ``deployed_skills`` lists are migrated to ``deployed_files``
          paths under ``.github/skills/`` and ``.claude/skills/``.
        """
        deployed_files = _dedupe_preserving_order(list(data.get("deployed_files", [])))

        # Migrate legacy deployed_skills -> deployed_files
        old_skills = data.get("deployed_skills", [])
        if old_skills and not deployed_files:
            for skill_name in old_skills:
                deployed_files.append(f".github/skills/{skill_name}/")
                deployed_files.append(f".claude/skills/{skill_name}/")

        # Defensive cast: reject non-numeric or out-of-range ports from tampered lockfiles.
        _p_raw = data.get("port")
        port: int | None = None
        if _p_raw is not None:
            try:
                _p_int = int(_p_raw)
            except (TypeError, ValueError):
                _p_int = None
            if _p_int is not None and 1 <= _p_int <= 65535:
                port = _p_int

        host_type = _normalize_lockfile_host_type(data.get("host_type"))
        exec_status = _normalize_exec_status(data.get("exec_status"))

        # Recognised keys this build knows about. Anything else is captured
        # as ``_unknown_fields`` so a re-emit preserves forward-introduced
        # fields rather than silently dropping them. ``deployed_skills`` is
        # the explicit legacy key handled above; do NOT consider it unknown.
        _known_keys = {
            "repo_url",
            "materialization_repo_url",
            "host",
            "host_type",
            "port",
            "registry_prefix",
            "resolved_commit",
            "resolved_ref",
            "version",
            "virtual_path",
            "is_virtual",
            "depth",
            "resolved_by",
            "package_type",
            "deployed_files",
            "deployed_file_hashes",
            "source",
            "local_path",
            "declaring_parent",
            "anchored_local_path",
            "content_hash",
            "is_dev",
            "discovered_via",
            "marketplace_plugin_name",
            "source_url",
            "source_digest",
            "is_insecure",
            "allow_insecure",
            "skill_subset",
            "target_subset",
            "resolved_url",
            "resolved_hash",
            "constraint",
            "resolved_tag",
            "resolved_at",
            "declared_license",
            "exec_status",
            "name",
            # legacy migration key handled above
            "deployed_skills",
        }
        unknown_fields = {
            k: v
            for k, v in data.items()
            if k not in _known_keys and not DependencyReference.is_transient_provider_field(k)
        }

        return cls(
            repo_url=data["repo_url"],
            materialization_repo_url=data.get("materialization_repo_url"),
            host=data.get("host"),
            host_type=host_type,
            port=port,
            registry_prefix=data.get("registry_prefix"),
            resolved_commit=data.get("resolved_commit"),
            resolved_ref=data.get("resolved_ref"),
            version=data.get("version"),
            virtual_path=data.get("virtual_path"),
            is_virtual=data.get("is_virtual", False),
            depth=data.get("depth", 1),
            resolved_by=data.get("resolved_by"),
            package_type=data.get("package_type"),
            deployed_files=deployed_files,
            deployed_file_hashes=dict(data.get("deployed_file_hashes") or {}),
            source=data.get("source"),
            local_path=data.get("local_path"),
            declaring_parent=data.get("declaring_parent"),
            anchored_local_path=data.get("anchored_local_path"),
            content_hash=data.get("content_hash"),
            is_dev=data.get("is_dev", False),
            discovered_via=data.get("discovered_via"),
            marketplace_plugin_name=data.get("marketplace_plugin_name"),
            source_url=data.get("source_url"),
            source_digest=data.get("source_digest"),
            is_insecure=data.get("is_insecure", False),
            allow_insecure=data.get("allow_insecure", False),
            skill_subset=list(data.get("skill_subset") or []),
            target_subset=list(data.get("target_subset") or []),
            resolved_url=data.get("resolved_url"),
            resolved_hash=data.get("resolved_hash"),
            constraint=data.get("constraint"),
            resolved_tag=data.get("resolved_tag"),
            resolved_at=data.get("resolved_at"),
            declared_license=data.get("declared_license"),
            exec_status=exec_status,
            name=data.get("name"),
            _unknown_fields=unknown_fields,
        )

    @classmethod
    def from_dependency_ref(
        cls,
        dep_ref: DependencyReference,
        resolved_commit: str | None,
        depth: int,
        resolved_by: str | None,
        is_dev: bool = False,
        registry_config=None,
        registry_resolution=None,
        git_semver_resolution=None,
        package_name: str | None = None,
        package_version: str | None = None,
    ) -> LockedDependency:
        """Create from a DependencyReference with resolution info.

        Args:
            dep_ref: The resolved dependency reference.
            resolved_commit: Exact commit SHA that was installed, or ``None``.
            depth: Dependency tree depth.
            resolved_by: Parent repo URL, or ``None`` for direct dependencies.
            is_dev: Whether this is a dev-only dependency.
            registry_config: Optional :class:`~apmx.deps.registry_proxy.RegistryConfig`
                used for this download (Artifactory VCS proxy — pre-existing
                concept, distinct from the new dedicated-registry resolver).
                When provided, ``host`` is set to the pure FQDN and
                ``registry_prefix`` to the URL path prefix.
            registry_resolution: Optional :class:`~apmx.deps.registry.resolver.RegistryResolution`
                produced by the dedicated-registry resolver. When provided,
                ``source`` is set to ``"registry"`` and ``resolved_url`` /
                ``resolved_hash`` / ``version`` are populated from it (the
                trust anchor for re-installs per design §6.1).
            git_semver_resolution: Optional
                :class:`~apmx.deps.git_semver_resolver.GitSemverResolution`
                produced when a git-source dep had a semver range as ``ref:``.
                When provided, ``constraint`` / ``resolved_tag`` /
                ``resolved_at`` are populated and ``resolved_ref`` is set
                to the concrete tag (issue #1488). Mutually exclusive with
                ``registry_resolution``.

        Raises:
            ValueError: When both ``registry_resolution`` and
                ``git_semver_resolution`` are provided. The two resolution
                paths are mutually exclusive: a dependency is either
                registry-sourced (carries ``resolved_url`` / ``resolved_hash``)
                or git-source with a semver range (carries ``constraint`` /
                ``resolved_tag`` / ``resolved_at``). Combining both would
                produce an inconsistent lockfile entry (e.g. ``source=registry``
                while ``resolved_ref`` is overridden to a git tag).
        """
        if registry_resolution is not None and git_semver_resolution is not None:
            raise ValueError(
                "registry_resolution and git_semver_resolution are mutually "
                "exclusive: a dependency is either registry-sourced or a "
                "git-source semver resolution, not both."
            )
        if registry_config is not None:
            host = registry_config.host
            registry_prefix = registry_config.prefix
        else:
            host = dep_ref.host
            registry_prefix = None

        # Determine source: explicit registry resolution wins; else local;
        # else inherit from dep_ref.source (which may be "git" or None).
        if registry_resolution is not None:
            source = "registry"
        elif dep_ref.is_local:
            source = "local"
        else:
            source = None

        # Prefer the concrete resolved identifier for ``resolved_ref`` so that
        # ``build_update_plan`` can detect real version changes by comparing
        # old_ref (locked concrete) vs new_ref (freshly resolved concrete).
        # Registry deps: store the resolved version (e.g. "1.0.3"), not the
        # range ("^1.0.0").  Git-semver deps: store the resolved tag.  Both
        # preserve the original selector in their dedicated fields
        # (``version`` / ``constraint`` respectively).
        if git_semver_resolution is not None:
            resolved_ref_val: str | None = git_semver_resolution.resolved_tag
        elif registry_resolution is not None:
            resolved_ref_val = registry_resolution.version
        else:
            resolved_ref_val = dep_ref.reference

        dep_ref.validate_provider_coordinates()
        if registry_resolution is not None:
            version_value = registry_resolution.version
        elif git_semver_resolution is not None:
            version_value = git_semver_resolution.resolved_version
        elif source != "registry":
            version_value = package_version
        else:
            version_value = None

        canonical_repo_url = normalize_package_repo_url(
            dep_ref.repo_url,
            host=dep_ref.host,
            source="local" if dep_ref.is_local else dep_ref.source,
            registry_prefix=dep_ref.artifactory_prefix,
            is_local=dep_ref.is_local,
            is_marketplace=dep_ref.is_marketplace,
        )
        return cls(
            repo_url=canonical_repo_url,
            materialization_repo_url=(
                dep_ref.repo_url if dep_ref.repo_url != canonical_repo_url else None
            ),
            host=host,
            host_type=dep_ref.host_type,
            port=dep_ref.port,
            registry_prefix=registry_prefix,
            resolved_commit=resolved_commit,
            resolved_ref=resolved_ref_val,
            version=version_value,
            virtual_path=dep_ref.virtual_path,
            is_virtual=dep_ref.is_virtual,
            depth=depth,
            resolved_by=resolved_by,
            source=source,
            local_path=dep_ref.local_path if dep_ref.is_local else None,
            declaring_parent=dep_ref.declaring_parent if dep_ref.is_local else None,
            anchored_local_path=dep_ref.anchored_local_path if dep_ref.is_local else None,
            is_dev=is_dev,
            is_insecure=dep_ref.is_insecure,
            allow_insecure=dep_ref.allow_insecure,
            skill_subset=sorted(dep_ref.skill_subset)
            if isinstance(getattr(dep_ref, "skill_subset", None), list)
            else [],
            target_subset=sorted(dep_ref.target_subset)
            if isinstance(getattr(dep_ref, "target_subset", None), list)
            else [],
            resolved_url=(
                registry_resolution.resolved_url if registry_resolution is not None else None
            ),
            resolved_hash=(
                registry_resolution.resolved_hash if registry_resolution is not None else None
            ),
            constraint=(
                git_semver_resolution.constraint if git_semver_resolution is not None else None
            ),
            resolved_tag=(
                git_semver_resolution.resolved_tag if git_semver_resolution is not None else None
            ),
            resolved_at=(
                git_semver_resolution.resolved_at if git_semver_resolution is not None else None
            ),
            name=package_name,
        )

    def to_dependency_ref(self) -> DependencyReference:
        """Reconstruct a DependencyReference from this locked dependency.

        Registry-sourced deps come back with ``source="registry"`` so the
        install pipeline routes them to the registry resolver. The exact
        locked version is in ``reference`` (the registry resolver still calls
        /versions and the hash-check on download enforces the lockfile's
        intent).
        """
        # Registry deps: prefer the locked exact version over resolved_ref so
        # the resolver picks up the exact-version constraint, not the original
        # range (e.g. ``^1.2.0`` -> ``1.5.3``).
        is_registry = self.source == "registry"
        ref = self.version if (is_registry and self.version) else self.resolved_ref
        return DependencyReference(
            repo_url=self.materialization_repo_url or self.repo_url,
            host=self.host,
            host_type=self.host_type,
            port=self.port,
            reference=ref,
            virtual_path=self.virtual_path,
            is_virtual=self.is_virtual,
            artifactory_prefix=self.registry_prefix,
            is_local=(self.source == "local"),
            local_path=self.local_path,
            declaring_parent=self.declaring_parent,
            anchored_local_path=self.anchored_local_path,
            is_insecure=self.is_insecure,
            allow_insecure=self.allow_insecure,
            source=self.source,
            skill_subset=sorted(self.skill_subset) if self.skill_subset else None,
            target_subset=sorted(self.target_subset) if self.target_subset else None,
        ).with_derived_provider_coordinates()
