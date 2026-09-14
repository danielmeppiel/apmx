"""Read-only APM manifest projection for the standalone contract profile."""

from dataclasses import dataclass
from pathlib import Path

from .dependency.reference import DependencyReference
from .dependency.selection import parse_dependency_entry

__all__ = ["KNOWN_TARGET_NAMES", "APMPackage", "DependencyReference", "parse_targets"]

_TARGET_ALIASES = {"vscode": "copilot", "agents": "copilot", "agy": "antigravity"}
KNOWN_TARGET_NAMES = frozenset(
    {
        "copilot",
        "claude",
        "cursor",
        "kiro",
        "opencode",
        "gemini",
        "grok-build",
        "grok-cloud",
        "antigravity",
        "codex",
        "vscode",
        "agents",
        "copilot-app",
        "copilot-cowork",
        "openclaw",
        "agent-skills",
    }
)


def parse_targets(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError("Package targets must be a string or list of nonempty strings.")
    names = tuple(_TARGET_ALIASES.get(item.strip(), item.strip()) for item in value)
    if any(name not in KNOWN_TARGET_NAMES | {"all"} for name in names):
        raise ValueError("Unknown package target.")
    return names


@dataclass
class APMPackage:
    name: str
    version: str
    dependencies: dict | None = None
    dev_dependencies: dict | None = None
    source_path: Path | None = None
    package_path: Path | None = None
    canonical_targets: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, data, *, package_path, source_path=None, create_config=False):
        for key in ("name", "version"):
            if not isinstance(data.get(key), str) or not data[key].strip():
                raise ValueError(f"Manifest {key} must be a nonempty string.")
        if data.get("target") is not None and data.get("targets") is not None:
            raise ValueError("Use either target or targets, not both.")
        if data.get("registries"):
            raise ValueError("Registry configuration is unsupported by standalone contracts.")

        def dependencies(key):
            raw = data.get(key)
            if raw is None:
                return None
            if not isinstance(raw, dict):
                # Malformed decoded fields preserve the manifest ValueError API.
                raise ValueError(f"{key} must be a mapping.")  # noqa: TRY004
            parsed = {}
            for kind, entries in raw.items():
                if not isinstance(entries, list):
                    raise ValueError(f"{key}.{kind} must be a list.")  # noqa: TRY004
                parsed[kind] = (
                    [parse_dependency_entry(entry) for entry in entries]
                    if kind == "apm"
                    else entries
                )
            return parsed

        return cls(
            data["name"],
            data["version"],
            dependencies("dependencies"),
            dependencies("devDependencies"),
            source_path,
            package_path,
            parse_targets(data.get("targets", data.get("target"))),
        )
