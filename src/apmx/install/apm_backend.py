"""Official APM owns acquisition; this adapter owns only invocation and staging."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from copy import deepcopy
from pathlib import Path

from apmx.contracts.imports import _read_bytes, read_project_manifest
from apmx.contracts.models import ContractError, ContractLimits, ProcessRequest
from apmx.contracts.process import supervise_process
from apmx.core.tls_trust import build_child_tls_env
from apmx.deps.lockfile import LockFile, resolve_lockfile_path_for_read
from apmx.models.dependency.selection import parse_dependency_entry
from apmx.utils.path_security import has_symlink_component
from apmx.utils.yaml_io import dump_yaml, load_yaml_str

PIN_PATH = Path(__file__).resolve().parents[1] / "apm-backend.json"


def locate_backend() -> Path:
    """Never consult PATH or source overrides in a frozen release."""
    name = "apm.exe" if os.name == "nt" else "apm"
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
        candidate = root / "libexec" / "apm" / name
        if has_symlink_component(root, candidate):
            raise ContractError("The bundled APM path contains a symlink.",
                                code="apm_backend_missing")
    elif override := os.environ.get("APMX_APM_BACKEND"):
        candidate = Path(override)
        if not candidate.is_absolute():
            raise ContractError(
                "APMX_APM_BACKEND must be an absolute provisioned executable path.",
                code="apm_backend_missing",
            )
    else:
        candidate = Path(__file__).resolve().parents[3] / "dist" / "apm-backend" / name
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise ContractError(
            "The bundled APM backend is missing or not executable. Reinstall apmx; "
            "source users must provision the pinned backend with scripts/release.py provision-apm.",
            code="apm_backend_missing",
        )
    return candidate.resolve()


def backend_identity(executable: Path) -> dict[str, str]:
    try:
        raw = PIN_PATH.read_bytes()
        pin = json.loads(raw)
        if (
            pin["schema"] != "apmx-apm-backend/1"
            or pin["repository"] != "microsoft/apm"
            or not isinstance(pin["version"], str)
            or not isinstance(pin["source_commit"], str)
            or len(pin["source_commit"]) != 40
        ):
            raise ValueError("Invalid backend pin.")
        with executable.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        return {
            "version": pin["version"],
            "source_commit": pin["source_commit"],
            "executable_sha256": digest,
            "pin_sha256": hashlib.sha256(raw).hexdigest(),
        }
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ContractError(
            "The bundled APM identity cannot be established. Reinstall apmx.",
            code="apm_backend_identity",
        ) from exc


def expected_version_output(target: str | None = None) -> str:
    """Use the pinned platform's actual output, not a cross-platform prefix match."""
    if target is None:
        system = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}.get(platform.system())
        machine = platform.machine().lower()
        architecture = {"arm64": "arm64", "aarch64": "arm64",
                        "x86_64": "x86_64", "amd64": "x86_64"}.get(machine)
        target = f"{system}-{architecture}"
    try:
        pin = json.loads(PIN_PATH.read_bytes())
        output = pin["assets"][target]["version_output"]
        consistent = f"Agent Package Manager (APM) CLI version {pin['version']}"
        if target != "windows-x86_64":
            consistent += f" ({pin['source_commit'][:7]})"
        if output != consistent:
            raise ValueError("Platform version output disagrees with backend identity.")
        return output
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ContractError(
            "The native platform has no pinned APM version identity.",
            code="apm_backend_identity",
        ) from exc


def backend_child_env(environ: dict[str, str]) -> dict[str, str]:
    env = build_child_tls_env(environ)
    env["APM_NO_SCRIPTS"] = "1"
    env["APM_PROGRESS"] = "never"
    if sys.platform == "win32":
        raw_count = env.get("GIT_CONFIG_COUNT", "0")
        if not raw_count.isascii() or not raw_count.isdecimal():
            raise ContractError("Invalid process Git configuration.", code="apm_backend_environment")
        digits = raw_count.lstrip("0") or "0"
        if len(digits) > len(str(len(env))):
            raise ContractError("Invalid process Git configuration.", code="apm_backend_environment")
        count = int(digits)
        if count > len(env) // 2 or any(
            f"GIT_CONFIG_KEY_{index}" not in env or f"GIT_CONFIG_VALUE_{index}" not in env
            for index in range(count)
        ):
            raise ContractError("Incomplete process Git configuration.", code="apm_backend_environment")
        if f"GIT_CONFIG_KEY_{count}" in env or f"GIT_CONFIG_VALUE_{count}" in env:
            raise ContractError("Inconsistent process Git configuration.", code="apm_backend_environment")
        env[f"GIT_CONFIG_KEY_{count}"] = "core.longpaths"
        env[f"GIT_CONFIG_VALUE_{count}"] = "true"
        env["GIT_CONFIG_COUNT"] = str(count + 1)
    return env


def install(
    stage: Path,
    *,
    package_ref: str | None = None,
    frozen: bool = False,
    limits: ContractLimits,
) -> dict[str, str]:
    """Run in an owned source/deploy root with normal APM auth/config semantics.

    APM may bootstrap its user config/cache. No HOME/credential rewriting or
    provider-policy overrides are used. Raw child output never enters records.
    """
    if frozen and package_ref is not None:
        raise ContractError("A frozen install cannot add packages.", code="invalid_lock")
    executable = locate_backend()
    identity = backend_identity(executable)
    argv = [str(executable), "install"]
    if package_ref is not None:
        argv.append(package_ref)
    argv.extend((
        "--root", str(stage), "--only", "apm",
        "--target", "agent-skills", "--no-trust-bin",
    ))
    if frozen:
        argv.append("--frozen")
    env = backend_child_env(dict(os.environ))
    version_output = bytearray()
    oversized = False

    def receive_version(stream: str, chunk: bytes) -> None:
        nonlocal oversized
        if stream != "stdout" or oversized:
            return
        if len(version_output) + len(chunk) > 1024:
            oversized = True
            version_output.clear()
        else:
            version_output.extend(chunk)

    version = supervise_process(
        ProcessRequest((str(executable), "--version"), stage, 15, env=env),
        on_bytes=receive_version,
        limits=limits,
    )
    expected = expected_version_output()
    if (
        version.returncode != 0 or version.stop_reason or version.error
        or not version.cleanup_confirmed or oversized
        or version_output.decode("utf-8", errors="replace").removesuffix("\n").removesuffix("\r")
        != expected
    ):
        raise ContractError(
            "The provisioned APM version/source does not match the bundled pin.",
            code="apm_backend_identity",
        )
    observed = supervise_process(
        ProcessRequest(tuple(argv), stage, limits.attempt_seconds, env=env),
        on_bytes=lambda _stream, _chunk: None,
        limits=limits,
    )
    if (
        observed.returncode != 0
        or observed.stop_reason
        or observed.error
        or not observed.cleanup_confirmed
    ):
        raise ContractError(
            "Official APM could not prepare the selected dependencies. Check the manifest, "
            "lock, package access and APM configuration. No producer was launched.",
            code="apm_install_failed",
        )
    if backend_identity(executable) != identity:
        raise ContractError("APM backend changed during preparation.", code="apm_backend_changed")
    return identity


def snapshot_manifest(original: Path, stage: Path, limits: ContractLimits) -> bool:
    """Anchor local declarations in an owned snapshot, without resolving versions.

    Frozen replay receives the same coordinate substitution in its inventory;
    all unknown native lock fields are preserved for APM to interpret.
    """
    _, manifest, _ = read_project_manifest(original, limits)
    data = deepcopy(manifest)
    substitutions: dict[str, str] = {}
    for field in ("dependencies", "devDependencies"):
        section = data.get(field) or {}
        entries = section.get("apm", [])
        for index, entry in enumerate(entries):
            dependency = parse_dependency_entry(entry)
            if not dependency.is_local:
                continue
            path = Path(dependency.local_path or "").expanduser()
            anchored = path if path.is_absolute() else original / path
            lexical = Path(os.path.abspath(anchored))
            if has_symlink_component(Path(lexical.anchor), lexical):
                raise ContractError("Local dependency contains a symlink.", code="import_source")
            absolute = str(anchored.resolve())
            substitutions[dependency.local_path] = absolute
            if isinstance(entry, dict):
                entries[index] = {**entry, "path": absolute}
            else:
                entries[index] = absolute
    dump_yaml(data, stage / "apm.yml")
    lock_path = resolve_lockfile_path_for_read(original, read_only=True)
    if not lock_path.exists():
        return False
    raw = _read_bytes(lock_path, maximum=limits.file_bytes, root=original)
    try:
        LockFile.from_yaml(raw.decode("utf-8"))
        lock = load_yaml_str(raw.decode("utf-8"))
        for entry in lock.get("dependencies", []):
            if entry.get("source") == "local" and not entry.get("declaring_parent"):
                path = entry.get("local_path")
                if path in substitutions:
                    entry["local_path"] = substitutions[path]
            parent = entry.get("resolved_by")
            if parent in substitutions:
                entry["resolved_by"] = substitutions[parent]
        dump_yaml(lock, stage / "apm.lock.yaml")
    except (ValueError, TypeError, KeyError) as exc:
        raise ContractError("Existing lockfile is malformed.", code="invalid_lock") from exc
    return True


def add_package_request(stage: Path, package_ref: str, limits: ContractLimits) -> None:
    """Add a declaration, not a resolved lock entry; native APM resolves the graph."""
    _, data, _ = read_project_manifest(stage, limits)
    dependencies = data.get("dependencies")
    if dependencies is None:
        dependencies = data["dependencies"] = {}
    dependencies.setdefault("apm", []).append(package_ref)
    dump_yaml(data, stage / "apm.yml")


def require_preserved_pins(before: LockFile | None, after: LockFile | None) -> None:
    """Adding a contract may extend a graph, never silently replace consumer pins."""
    if before is None:
        return
    fields = (
        "repo_url", "host", "host_type", "port", "registry_prefix", "source",
        "virtual_path", "is_virtual", "resolved_commit", "resolved_ref",
        "version", "name", "content_hash", "constraint", "resolved_tag",
        "resolved_url", "resolved_hash", "local_path", "anchored_local_path",
        "skill_subset", "target_subset",
    )
    for key, established in before.dependencies.items():
        observed = after.get_dependency(key) if after else None
        if observed is None or any(
            getattr(established, name) != getattr(observed, name) for name in fields
        ):
            raise ContractError(
                "Official APM could not preserve an established consumer lock identity.",
                code="import_drift",
            )
