"""Private native-tool configuration and capture of actual export observations."""

import hashlib
import json
import sys
from pathlib import Path

from ..contracts import records, workspace
from ..contracts.git_export import ExportContext
from ..contracts.models import (
    Artifact,
    ArtifactSet,
    BaselineSnapshot,
    ContractError,
    ContractLimits,
    FileEntry,
    LeafPlan,
    artifact_files,
)
from ..utils.atomic_io import atomic_write_text

SERVER = "apmx_artifacts"
TOOLS = ("write_file", "delete_file", "export_changes")
NATIVE_TOOL_NAMES = tuple(f"{SERVER}-{name}" for name in TOOLS)
SERVER_ENV = "APMX_INTERNAL_ARTIFACT_SERVER"


def save_json(path: Path, value: dict) -> None:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=True, sort_keys=True) + "\n",
        new_file_mode=0o600,
        durable=True,
    )


def configure(plan: LeafPlan, snapshot: BaselineSnapshot, run_directory: Path) -> Path:
    """Create a session-local plugin; never install or alter native user configuration."""
    root = run_directory / "native-tools"
    root.mkdir(mode=0o700, parents=True)
    config = root / "config.json"
    contract = plan.contract.path.relative_to(
        plan.source.root if plan.source else plan.project_root
    ).as_posix()
    save_json(
        config,
        {
            "schema": "apmx-artifact-tools/0.1",
            "baseline": str(snapshot.root),
            "producer": str(snapshot.producer),
            "directory": str(root),
            "files": records._json_value(snapshot.files),
            "outputs": plan.contract.outputs,
            "contract": contract,
            "limits": records._json_value(plan.limits),
        },
    )
    save_json(
        root / "state.json",
        {"active": None, "failed": False, "error": None, "exports": [], "operations": 0},
    )
    frozen = getattr(sys, "frozen", False)
    environment = {SERVER_ENV: str(config)}
    if not frozen:
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
    manifest = root / "plugin" / ".github" / "plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    save_json(
        manifest,
        {
            "name": "apmx-artifact-tools",
            "version": "0.1.0",
            "mcpServers": {
                SERVER: {
                    "type": "local",
                    "command": sys.executable,
                    "args": [] if frozen else ["-B", "-m", "apmx.runtime.artifact_mcp"],
                    "env": environment,
                    "tools": list(TOOLS),
                },
            },
        },
    )
    return root / "plugin"


def load_context(config: Path) -> ExportContext:
    data, _ = records._record_bytes(config, ContractLimits())
    try:
        if data["schema"] != "apmx-artifact-tools/0.1":
            raise ValueError("Unknown tool configuration.")
        context = ExportContext(
            Path(data["baseline"]),
            Path(data["producer"]),
            Path(data["directory"]),
            tuple(FileEntry(**entry) for entry in data["files"]),
            tuple(data["outputs"]),
            data["contract"],
            ContractLimits(**data["limits"]),
        )
        run = context.directory.parent
        if (
            config != context.directory / "config.json"
            or context.directory != run / "native-tools"
            or context.baseline != run / "baseline"
            or context.producer != run / "producer"
            or run.parent.name != "runs"
            or run.parent.parent.name != ".apm"
            or not context.outputs
            or len(context.outputs) > context.limits.output_files
        ):
            raise ValueError("Tool configuration does not identify its captured attempt.")
        return context
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError(
            "Invalid private artifact-tool configuration.", code="tool_configuration"
        ) from exc


def capture_exports(
    directory: Path,
    artifact: Artifact | ArtifactSet | None,
    snapshot: BaselineSnapshot,
    limits: ContractLimits,
) -> tuple[FileEntry, ...]:
    """Fail closed on unfinished operations, and bind actual Git receipts to captured bytes."""
    root = directory / "native-tools"
    state, _ = records._record_bytes(root / "state.json", limits)
    if (
        set(state) != {"active", "failed", "error", "exports", "operations"}
        or state["active"] is not None
        or state["failed"] is not False
        or state["error"] is not None
        or type(state["operations"]) is not int
        or not 0 <= state["operations"] <= 64
        or not isinstance(state["exports"], list)
        or len(state["exports"]) > limits.output_files
    ):
        error = (
            state.get("error") if isinstance(state.get("error"), str) else "incomplete_operation"
        )
        raise ContractError(
            f"Native artifact operation failed or did not finish ({error}); cleanup is not established. "
            "Inspect the tool failure before starting a new attempt.",
            code="artifact_tool_incomplete",
        )
    files = {item.relative_path: item for item in artifact_files(artifact)}
    retained = []
    seen = set()
    for index, item in enumerate(state["exports"], 1):
        expected = f"export-{index}.json"
        if (
            not isinstance(item, dict)
            or set(item) != {"name", "sha256"}
            or item["name"] != expected
        ):
            raise ContractError(
                "Invalid export observation inventory.", code="export_evidence_changed"
            )
        raw, entry = workspace._read(root, expected, limits.file_bytes)
        receipt, digest = records._record_bytes(root / expected, limits)
        output = receipt.get("output")
        if (
            entry.sha256 != item["sha256"]
            or digest != entry.sha256
            or receipt.get("schema") != "apmx-git-export/0.1"
            or not isinstance(output, str)
            or output in seen
            or output not in files
            or receipt.get("patch_sha256") != files[output].sha256
            or type(receipt.get("patch_size")) is not int
            or receipt["patch_size"] != files[output].size
            or receipt.get("baseline_digest") != snapshot.digest
        ):
            raise ContractError(
                "Exported artifact or its original observation changed.",
                code="export_evidence_changed",
            )
        seen.add(output)
        observation = FileEntry(
            f"observations/{expected}", hashlib.sha256(raw).hexdigest(), len(raw), 0o400
        )
        workspace._write(directory, observation, raw)
        retained.append(observation)
    return tuple(retained)
