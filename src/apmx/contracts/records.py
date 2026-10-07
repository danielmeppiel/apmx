"""One local authority for check normalization, outcomes and attempt records."""

import json
import math
import os
from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime
from enum import IntEnum
from pathlib import Path
from uuid import uuid4

from ..utils.atomic_io import atomic_write_text
from ..utils.git_env import redact_git_diagnostic
from ..utils.path_security import has_symlink_component
from .models import (
    Artifact,
    ArtifactSet,
    ArtifactView,
    CapturedInput,
    ChainResult,
    CheckObservation,
    ContractError,
    ContractLimits,
    FileEntry,
    LeafContract,
    LeafPlan,
    Outcome,
    ProcessObservation,
    ProjectSnapshot,
    RecordReference,
    RetainedInput,
    RunResult,
    artifact_files,
)

RUN_SCHEMA = "apm-contract-run/0.3"
CHAIN_SCHEMA = "apmx-contract-chain/0.2"
CONTROLLER_SCHEMA = "apmx-contract-controller/1"
NATIVE_ASSURANCE = {
    "profile": "native-advisory",
    "isolation": "unavailable",
    "certification": "unproven",
}


def normalize_check(process: ProcessObservation, *, integrity_ok: bool = True) -> int:
    """Retain raw exit separately; absence of prose does not weaken exit one."""
    if not integrity_ok or process.error or process.stop_reason or not process.cleanup_confirmed:
        return 2
    return process.returncode if process.returncode in (0, 1, 2) else 2


def reduce_outcome(
    artifact: Artifact | ArtifactSet | None,
    checks: tuple[CheckObservation, ...],
    stop_reason: str | None,
) -> Outcome:
    """Reduce provisional observations; only finalized evidence can earn COMPLETE."""
    if stop_reason:
        return Outcome.HALTED
    if any(check.normalized == 1 for check in checks):
        return Outcome.REJECTED
    return Outcome.UNPROVEN


def native_assurance_limited(result: RunResult) -> bool:
    """Identify passing provisional/COMPLETE observations with host-limited assurance.

    This is not an exact-completeness check; finalized_inputs must validate the
    declared inventory, check identities, producer and retained evidence first.
    """
    return (
        result.outcome in (Outcome.UNPROVEN, Outcome.COMPLETE)
        and result.stop_reason is None
        and result.artifact is not None
        and bool(result.checks)
        and all(check.normalized == 0 for check in result.checks)
    )


def handoff_policy(allow_unproven: bool) -> str:
    return "native-assurance-exception" if allow_unproven else "VERIFIED-only"


def validate_consent_source(source: str | None) -> None:
    if source not in ("flag", "interactive"):
        raise ContractError("Unknown execution consent source.", code="invalid_consent")


def _json_value(value: object) -> object:
    if isinstance(value, IntEnum):
        return {"name": value.name, "exit_code": int(value)}
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _json_value(getattr(value, field.name))
            for field in fields(value)
            if not (
                field.name in {"native_exports", "controller", "budget", "repair"}
                and not getattr(value, field.name)
            )
        }
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _limit_identity(plan: LeafPlan) -> dict:
    """Preserve the scalar limit shape when inventory limits are inapplicable."""
    defaults = ContractLimits()
    omitted = (
        {"output_files", "output_total_bytes"}
        if isinstance(plan.contract.produces, str)
        and plan.limits.output_files == defaults.output_files
        and plan.limits.output_total_bytes == defaults.output_total_bytes
        else set()
    )
    return {
        field.name: getattr(plan.limits, field.name)
        for field in fields(plan.limits)
        if field.name not in omitted
    }


def _allocate_directory(caller: Path, family: str) -> tuple[str, Path]:
    parent = caller / ".apm" / family
    if has_symlink_component(caller, parent):
        raise ContractError("Evidence storage contains a symlink.", code="unsafe_run_directory")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    identity = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:12]
    directory = parent / identity
    directory.mkdir(mode=0o700)
    os.chmod(directory, 0o700)
    return identity, directory


def _same_json(actual: object, expected: object) -> bool:
    """Compare recorded types as well as values (False is not an exit code)."""
    return json.dumps(actual, sort_keys=True, allow_nan=False) == json.dumps(
        _json_value(expected), sort_keys=True, allow_nan=False
    )


def _import_identities(plan: LeafPlan) -> list[dict]:
    return [
        {
            "name": skill.name,
            "sha256": skill.source_digest,
            "lock_identity": skill.lock_identity,
            "assurance": skill.assurance,
            "resolved_commit": skill.resolved_commit,
            "version": skill.version,
            "kind": skill.kind,
            "context_name": skill.context_name,
            "source_relative_path": skill.source_relative_path,
            "resources": [
                {"path": resource.relative_path, "sha256": resource.sha256, "size": resource.size}
                for resource in skill.resources
            ],
            "package_name": skill.package_name,
            "resolved_ref": skill.resolved_ref,
            "verified_package_hash": skill.verified_package_hash,
            "managed_metadata": skill.managed_metadata,
        }
        for skill in plan.imported_skills
    ]


def _package_identity(plan: LeafPlan) -> dict | None:
    source = plan.source
    if source is None:
        return None
    return {
        "root": source.root,
        "contract_relative_path": source.contract_relative_path,
        "package_ref": redact_git_diagnostic(source.package_ref) if source.package_ref else None,
        "resolved_commit": source.resolved_commit,
        "package_hash": source.package_hash,
        "prepared_hash": source.prepared_hash,
        "original_root": source.original_root,
        "assurance": source.assurance,
        "managed_metadata": source.managed_metadata,
    }


def _record_bytes(path: Path, limits: ContractLimits) -> tuple[dict, str]:
    from .workspace import _read

    def unique(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ContractError("Duplicate record field.", code="record_changed")
            result[key] = value
        return result

    raw, entry = _read(path.parent, path.name, limits.file_bytes)

    def finite(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ContractError("Nonfinite record value.", code="record_changed")
        return number

    try:
        data = json.loads(raw, object_pairs_hook=unique, parse_float=finite, parse_constant=finite)
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError(
            "Finalized predecessor record is malformed.", code="record_changed"
        ) from exc
    if not isinstance(data, dict):
        raise ContractError("Expected a finalized object record.", code="record_changed")
    return data, entry.sha256


def _validate_version(data: dict) -> None:
    if data.get("schema") != RUN_SCHEMA:
        raise ContractError(
            "Unsupported retained record version. Historical records remain unchanged; "
            "rerun with current source to obtain a new handoff record.",
            code="record_version",
        )


def _validate_retained_provenance(
    directory: Path,
    data: dict,
    limits: ContractLimits,
) -> None:
    """Check the canonical captured inventory, not merely its descriptive digest fields."""
    from .workspace import _read

    try:
        source = data["source"]
        entries = tuple(FileEntry(**item) for item in source["retained_identities"])
        names = [entry.relative_path for entry in entries]
        if (
            not entries
            or len(entries) > limits.baseline_files
            or any(not isinstance(name, str) for name in names)
            or len(set(names)) != len(names)
            or "contract.contract.md" not in names
            or not _same_json(data["result"]["retained_provenance"], entries)
            or not _same_json(
                source["retained"],
                {name: str(directory / "source" / name) for name in names},
            )
        ):
            raise ValueError("Retained provenance inventory differs from its capture.")
        for entry in entries:
            if type(entry.size) is not int or not 0 <= entry.size <= limits.file_bytes:
                raise ValueError("Retained provenance size is invalid.")
            _, observed = _read(directory / "source", entry.relative_path, entry.size)
            # Capture records the requested read-only mode; Windows reports it differently.
            if not _same_json(_json_value(entry), replace(observed, mode=0o400)):
                raise ValueError("Retained provenance bytes differ from their capture.")
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError(
            "Retained source evidence is missing or changed. Inspect the predecessor record.",
            code="provenance_changed",
        ) from exc


def _retained_artifacts(
    data: dict, directory: Path, limits: ContractLimits
) -> tuple[Artifact, ...]:
    """Validate every member and the whole result, even for a subset consumer."""
    from .workspace import _path, _read, artifact_inventory_digest

    try:
        _validate_version(data)
        raw = data["artifact"]
        if not _same_json(raw, data["result"]["artifact"]):
            raise ValueError("Recorded output observations disagree.")
        inventory = isinstance(raw, dict) and "files" in raw
        if not inventory:
            rows = [raw]
        else:
            if set(raw) != {"files", "sha256"}:
                raise ValueError("Unknown artifact inventory shape.")
            rows = raw["files"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= limits.output_files:
            raise ValueError("Invalid retained artifact inventory.")
        if any(
            not isinstance(row, dict)
            or set(row) != {"relative_path", "path", "sha256", "size"}
            or any(not isinstance(row.get(key), str) for key in ("relative_path", "path", "sha256"))
            for row in rows
        ):
            raise ValueError("Invalid retained artifact identity.")
        files = tuple(Artifact(**{**row, "path": Path(row["path"])}) for row in rows)
        if not 1 <= len(files) <= limits.output_files:
            raise ValueError("Invalid retained artifact count.")
        names = [item.relative_path.casefold() for item in files]
        if len(set(names)) != len(names) or any(
            left.startswith(right + "/") or right.startswith(left + "/")
            for index, left in enumerate(names)
            for right in names[index + 1 :]
        ):
            raise ValueError("Retained artifact names collide.")
        if any(
            type(item.size) is not int or not 0 <= item.size <= limits.output_bytes
            for item in files
        ):
            raise ValueError("Invalid retained artifact size.")
        if sum(item.size for item in files) > limits.output_total_bytes:
            raise ValueError("Retained output inventory exceeds its byte limit.")
        if inventory and raw["sha256"] != artifact_inventory_digest(files):
            raise ValueError("Retained inventory digest changed.")
        for item in files:
            if item.path != _path(directory / "artifacts", item.relative_path):
                raise ValueError("Retained artifact location changed.")
            _, observed = _read(directory / "artifacts", item.relative_path, item.size)
            if (observed.sha256, observed.size) != (item.sha256, item.size):
                raise ValueError("Retained artifact bytes changed.")
        return files
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError(
            "Retained output inventory changed. Inspect its record.", code="artifact_changed"
        ) from exc


def _validate_native_exports(
    data: dict, directory: Path, files: tuple[Artifact, ...], limits: ContractLimits
) -> None:
    """Export observations are ordinary retained evidence, separate from source provenance."""
    from .workspace import _read

    try:
        rows = data["result"].get("native_exports", [])
        if not isinstance(rows, list) or len(rows) > limits.output_files:
            raise ValueError("Invalid native export inventory.")
        outputs = {item.relative_path: item for item in files}
        seen = set()
        for index, row in enumerate(rows, 1):
            entry = FileEntry(**row)
            if (
                entry.relative_path != f"observations/export-{index}.json"
                or type(entry.size) is not int
                or not 0 <= entry.size <= limits.file_bytes
                or entry.mode != 0o400
            ):
                raise ValueError("Invalid retained native export identity.")
            _, observed = _read(directory, entry.relative_path, entry.size)
            if not _same_json(_json_value(entry), replace(observed, mode=0o400)):
                raise ValueError("Native export evidence changed.")
            receipt, digest = _record_bytes(directory / entry.relative_path, limits)
            name = receipt.get("output")
            if (
                digest != entry.sha256
                or receipt.get("schema") != "apmx-git-export/0.1"
                or not isinstance(name, str)
                or name not in outputs
                or name in seen
                or receipt.get("baseline_digest") != data["baseline"]["digest"]
                or receipt.get("patch_sha256") != outputs[name].sha256
                or type(receipt.get("patch_size")) is not int
                or receipt["patch_size"] != outputs[name].size
            ):
                raise ValueError("Export observation does not identify the admitted delivery.")
            seen.add(name)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError(
            "Retained export evidence is missing or changed.", code="export_evidence_changed"
        ) from exc


def validate_binding(binding: RetainedInput, caller: Path, limits: ContractLimits) -> None:
    """Re-read the exact finalized record and retained bytes; never select another run."""
    from .workspace import _path

    record = binding.record_path
    directory = record.parent
    if (
        directory.parent != caller / ".apm/runs"
        or record.name != "record.json"
        or _path(caller, record.relative_to(caller).as_posix()) != record
        or binding.artifact.path != directory / "artifacts" / binding.artifact.relative_path
        or binding.artifact.size > limits.file_bytes
    ):
        raise ContractError(
            "Retained input must belong to this caller's run.", code="invalid_binding"
        )
    data, identity = _record_bytes(record, limits)
    _validate_version(data)
    if identity != binding.record_sha256:
        raise ContractError("Finalized predecessor record changed.", code="record_changed")
    _validate_retained_provenance(directory, data, limits)
    files = _retained_artifacts(data, directory, limits)
    _validate_native_exports(data, directory, files, limits)
    if binding.controller is not None:
        _controller_document(binding.controller, caller, directory.name, limits)
    if not any(_same_json(_json_value(binding.artifact), item) for item in files):
        raise ContractError("Retained predecessor output changed.", code="artifact_changed")


def finalized_inputs(plan: LeafPlan, result: RunResult) -> tuple[RetainedInput, ...]:
    """Validate this owner's persisted assessment against the direct typed leaf result.

    This proves completed observations, not a downstream assurance policy. The
    separate handoff gate below decides whether those observations may advance.
    """
    if (
        plan.contract.budget is not None
        and result.controller is None
        and native_assurance_limited(result)
    ):
        raise ContractError(
            "Budgeted completion requires its finalized controller record; no handoff.",
            code="controller_incomplete",
            outcome=Outcome.UNPROVEN,
        )
    if result.controller is not None and result.outcome is Outcome.COMPLETE:
        plan = _controller_plan(plan, result)
    return _assessed_inputs(plan, result)


def rejected_inputs(plan: LeafPlan, result: RunResult) -> tuple[RetainedInput, ...] | None:
    """Validate a complete rejection for reference only, never for an accepted handoff."""
    if (
        result.outcome is not Outcome.REJECTED
        or result.stop_reason is not None
        or len(result.checks) != len(plan.contract.checks)
        or not result.checks
        or any(
            check.normalized not in (0, 1)
            or type(check.process.returncode) is not int
            or check.process.returncode not in (0, 1)
            or check.process.error
            or check.process.stop_reason
            or check.process.cleanup_confirmed is not True
            for check in result.checks
        )
    ):
        return None
    return _assessed_inputs(plan, result, rejected=True)


def _validate_assessed_outputs(
    contract: LeafContract,
    result: RunResult,
    data: dict,
    resources_digest: str,
    *,
    rejected: bool = False,
) -> None:
    """Validate declarations, producer completion and exact observed checker subjects."""
    expected = tuple((check.name, check.command) for check in contract.checks)
    actual = tuple((check.name, check.command) for check in result.checks)
    if (
        result.artifact is None
        or not result.checks
        or (len(actual) < len(expected) and actual == expected[: len(actual)])
    ):
        raise ContractError(
            "Required output/checks are incomplete; no handoff.",
            code="incomplete_checks",
            outcome=Outcome.UNPROVEN,
        )
    if actual != expected:
        raise ValueError("Unexpected or duplicated required check identities.")
    artifact = result.artifact
    if tuple(item.relative_path for item in artifact_files(artifact)) != contract.outputs:
        raise ValueError("Output differs from the declaration.")
    if isinstance(contract.produces, str) != isinstance(artifact, Artifact):
        raise ValueError("Output version differs from the declaration.")
    if not _same_json(data["checks"], result.checks) or not _same_json(data["artifact"], artifact):
        raise ValueError("Duplicated artifact/check observations differ.")
    producer = ProcessObservation(**data["producer"])
    if (
        type(producer.returncode) is not int
        or normalize_check(producer) != 0
        or producer.cleanup_confirmed is not True
        or result.stop_reason is not None
        or data["native_completion_observed"] is not True
        or not _same_json(data.get("native_reported_exit_code"), None)
        and not _same_json(data.get("native_reported_exit_code"), 0)
    ):
        raise ValueError("Producer completion or cleanup is unconfirmed.")
    for check in result.checks:
        if check.subject_digest != artifact.sha256 or check.resources_digest != resources_digest:
            raise ValueError("A check assessed different bytes.")
        if type(check.normalized) is not int or check.normalized != normalize_check(check.process):
            raise ValueError("Raw and normalized check observations disagree.")
        if check.normalized not in ((0, 1) if rejected else (0,)):
            raise ContractError(
                "Required checks did not all pass; no handoff.",
                code="incomplete_checks",
                outcome=Outcome.UNPROVEN,
            )
        if type(check.process.returncode) is not int or check.process.cleanup_confirmed is not True:
            raise ValueError("Check completion/cleanup is unconfirmed.")


def _assessed_inputs(
    plan: LeafPlan, result: RunResult, *, rejected: bool = False
) -> tuple[RetainedInput, ...]:
    """Share exact evidence validation without sharing the acceptance policy."""
    from .workspace import (
        SELECTION_SCHEMA,
        _digest,
        _read,
        inspect_retained_log,
        inspect_workspace,
        project_inventory,
    )

    try:
        directory = result.run_directory
        if directory.parent != plan.project_root / ".apm/runs" or directory.name != result.run_id:
            raise ValueError("Run identity differs from the caller.")
        data, digest = _record_bytes(directory / "record.json", plan.limits)
        _validate_version(data)
        if not (
            data["profile"] == "native-advisory"
            and data["complete"] is True
            and data["phase"] == "finished"
            and data["run_id"] == result.run_id
            and data["caller_root"] == str(plan.project_root)
            and data["source"]["sha256"] == plan.contract.source_digest
            and data["source"]["path"] == str(plan.contract.path)
            and _same_json(data["result"], replace(result, controller=None))
            and _same_json(data["execution"], result.outcome)
            and _same_json(data["assurance"], NATIVE_ASSURANCE)
            and _same_json(data["source"]["retained_identities"], result.retained_provenance)
            and all(data[key] is None for key in ("child_pid", "child_pgid", "active_check"))
        ):
            raise ValueError("Finalized leaf identity or observations differ.")
        provisional = reduce_outcome(result.artifact, result.checks, result.stop_reason)
        if result.outcome != provisional and not (
            result.outcome is Outcome.COMPLETE and provisional is Outcome.UNPROVEN
        ):
            raise ValueError("Native outcome disagrees with canonical assessment.")
        expected_identity = {
            "manifest_sha256": plan.manifest_digest,
            "lock_sha256": plan.lock_digest,
            "consumer_manifest_sha256": plan.consumer_manifest_digest,
            "consumer_lock_sha256": plan.consumer_lock_digest,
            "harness": plan.harness,
            "executable": str(plan.executable),
            "requested_model": plan.model,
            "input_bindings": plan.input_bindings,
            "imports": _import_identities(plan),
            "limits": _limit_identity(plan),
            "attempt_id": result.run_id + "/1",
            "evidence_root": str(plan.project_root / ".apm/runs"),
            "executable_version": plan.executable_version,
            "apm_backend": plan.apm_backend,
            "advisory_consent": result.consent_source,
            "handoff_policy": result.handoff_policy,
            "policy_status": plan.policy_status,
        }
        if any(
            not _same_json(data[key], value) for key, value in expected_identity.items()
        ) or not _same_json(data["source"]["package"], _package_identity(plan)):
            raise ValueError("Planned source/import/caller identities differ from the record.")
        validate_consent_source(result.consent_source)
        if any(data["controls"][key] != "unavailable" for key in ("isolation", "spend_cap")):
            raise ValueError("Native assurance controls differ from the admitted profile.")
        if result.outcome in (Outcome.REJECTED, Outcome.HALTED) and not (
            rejected and result.outcome is Outcome.REJECTED
        ):
            raise ContractError(
                f"Predecessor stopped: {result.stop_reason or result.outcome.name}. Inspect its record.",
                code=result.stop_reason or "upstream_rejected",
                outcome=result.outcome,
            )
        inventory = plan.input_inventory
        if inventory is None:
            inventory = inspect_workspace(plan)
        baseline = data["baseline"]
        resources = tuple(item for item in inventory if item.relative_path.startswith("checks/"))
        if (
            baseline["root"] != str(directory / "baseline")
            or not _same_json(baseline["files"], inventory)
            or baseline["digest"] != _digest(inventory)
            or baseline["resources_digest"] != _digest(resources)
            or baseline.get("selection_schema") != SELECTION_SCHEMA
            or baseline.get("project_digest") != _digest(project_inventory(plan, inventory))
        ):
            raise ValueError("Recorded captured inputs/resources differ from admission.")
        _validate_assessed_outputs(
            plan.contract, result, data, _digest(resources), rejected=rejected
        )
        for item in inventory:
            _, observed = _read(directory / "baseline", item.relative_path, item.size)
            if observed != item:
                raise ValueError("Retained baseline changed.")
        if not _same_json(
            data["transcript"], inspect_retained_log(directory, plan.limits.transcript_bytes)
        ):
            raise ValueError("Finalized transcript changed.")
        bindings = tuple(
            RetainedInput(item, directory / "record.json", digest, result.controller)
            for item in artifact_files(result.artifact)
        )
        validate_binding(bindings[0], plan.project_root, plan.limits)
        return bindings
    except (ValueError, KeyError, TypeError, RecursionError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError(
            "Finalized leaf evidence is inconsistent. Inspect its record.", code="record_changed"
        ) from exc


def admit_handoffs(
    plan: LeafPlan, result: RunResult, *, allow_unproven: bool
) -> tuple[RetainedInput, ...]:
    """ASF strict gate, with a separately authorized native-assurance exception."""
    bindings = finalized_inputs(plan, result)
    if allow_unproven and native_assurance_limited(result):
        return bindings
    raise ContractError(
        "Native input blocked by the strict VERIFIED-only handoff policy. COMPLETE does not "
        "establish isolation. For trusted local "
        "development, explicitly select --allow-unproven-inputs; this does not certify isolation.",
        code="unproven_input",
        outcome=Outcome.UNPROVEN,
    )


def finalized_input(plan: LeafPlan, result: RunResult) -> RetainedInput:
    """Preserve the scalar receipt API; never select the first multi-file artifact."""
    if not isinstance(plan.contract.produces, str):
        raise ContractError("Multiple artifacts require finalized_inputs.", code="record_version")
    return finalized_inputs(plan, result)[0]


def admit_handoff(plan: LeafPlan, result: RunResult, *, allow_unproven: bool) -> RetainedInput:
    """Preserve scalar handoffs through the complete-result authority."""
    if not isinstance(plan.contract.produces, str):
        raise ContractError("Multiple artifacts require admit_handoffs.", code="record_version")
    return admit_handoffs(plan, result, allow_unproven=allow_unproven)[0]


def _chain_nodes(data: dict) -> list[dict]:
    nodes = data.get("nodes")
    if not isinstance(nodes, list) or any(
        not isinstance(node, dict)
        or not isinstance(node.get("contract"), str)
        or node.get("state") not in ("pending", "running", "completed", "stopped", "blocked")
        or "result" not in node
        for node in nodes
    ):
        raise ContractError("Factory node observations are malformed.", code="record_changed")
    return nodes


def _project_capture(raw: dict) -> ProjectSnapshot:
    """Decode retained selection metadata; the workspace owner validates its bytes."""
    return ProjectSnapshot(
        Path(raw["root"]),
        tuple(FileEntry(**item) for item in raw["files"]),
        raw["digest"],
        raw["original_head"],
        raw["schema"],
    )


def _validate_chain_completion(data: dict, result: ChainResult, nodes: list[dict]) -> None:
    """Match each ordered graph node to its distinct, exact finalized child."""
    from .workspace import validate_project

    try:
        order = data["graph"]["order"]
        if (
            not isinstance(order, list)
            or result.outcome is not Outcome.COMPLETE
            or not result.runs
            or len(nodes) != len(result.runs)
            or len(nodes) != len(order)
            or len({run.run_id for run in result.runs}) != len(result.runs)
            or len({run.run_directory for run in result.runs}) != len(result.runs)
            or len({item["contract"] for item in order}) != len(order)
        ):
            raise ValueError("Factory completion inventory disagrees.")
        caller = Path(data["caller_root"])
        source_root = Path(data["graph"]["root"])
        project = _json_value(data.get("project_capture"))
        if project is not None:
            snapshot = _project_capture(project)
            if snapshot.root != result.record_path.parent / "project":
                raise ValueError("Factory project capture belongs to another invocation.")
            validate_project(snapshot, caller, ContractLimits())
        for expected, node, run in zip(order, nodes, result.runs, strict=True):
            if (
                node["contract"] != expected["contract"]
                or node["state"] != "completed"
                or not _same_json(_json_value(node["result"]), run)
                or run.outcome is not Outcome.COMPLETE
                or run.run_directory != caller / ".apm/runs" / run.run_id
            ):
                raise ValueError("Factory graph/node/result correspondence disagrees.")
            child, digest = _record_bytes(run.run_directory / "record.json", ContractLimits())
            _validate_version(child)
            if (
                child["source"]["path"] != str(source_root / expected["contract"])
                or node["record_sha256"] != digest
                or not _same_json(child["result"], replace(run, controller=None))
                or not _same_json(child["execution"], Outcome.COMPLETE)
                or child["complete"] is not True
                or (child["baseline"].get("selection_schema") is not None and project is None)
                or (
                    project is not None
                    and (
                        child["baseline"]["selection_schema"] != project["schema"]
                        or child["baseline"]["project_digest"] != project["digest"]
                        or child["baseline"]["original_head"] != project["original_head"]
                    )
                )
            ):
                raise ValueError("Factory child identity changed.")
    except (ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError(
            "Factory completion observations disagree.", code="record_changed"
        ) from exc


def _retained_completion(result: RunResult | ChainResult) -> tuple[tuple[Path, str], ...]:
    """Revalidate retained evidence only; preparation sources may already be removed."""
    from .workspace import _path, inspect_artifact_view, inspect_retained_log

    limits = ContractLimits()
    identities = []
    children = result.runs if isinstance(result, ChainResult) else ()
    for current in (*children, result):
        is_chain = isinstance(current, ChainResult)
        path = current.record_path if is_chain else current.run_directory / "record.json"
        caller = path.parents[3]
        _path(caller, path.relative_to(caller).as_posix())
        data, digest = _record_bytes(path, limits)
        if data.get("schema") != (CHAIN_SCHEMA if is_chain else RUN_SCHEMA):
            raise ContractError("Unsupported completion record version.", code="record_version")
        if (
            data.get("complete") is not True
            or data.get("phase") != "finished"
            or not _same_json(
                data.get("result"), current if is_chain else replace(current, controller=None)
            )
            or not _same_json(data.get("execution"), Outcome.COMPLETE)
            or not _same_json(data.get("assurance"), NATIVE_ASSURANCE)
            or not _same_json(
                data.get("transcript"), inspect_retained_log(path.parent, limits.transcript_bytes)
            )
        ):
            raise ContractError(
                "Completion evidence changed during preparation cleanup.", code="record_changed"
            )
        if is_chain:
            _validate_chain_completion(data, current, _chain_nodes(data))
            raw = data["artifacts"]
            view = ArtifactView(
                Path(raw["root"]),
                tuple(FileEntry(**entry) for entry in raw["files"]),
                raw["digest"],
                tuple(
                    CapturedInput(
                        Path(row["source_root"]),
                        row["source_relative_path"],
                        FileEntry(**row["entry"]),
                    )
                    for row in raw["sources"]
                ),
            )
            if view.root != path.parent / "artifacts":
                raise ContractError("Factory artifact location changed.", code="artifact_changed")
            inspect_artifact_view(view)
        else:
            if bool(data.get("controller")) != (current.controller is not None):
                raise ContractError(
                    "Completion controller linkage is missing.", code="record_changed"
                )
            _retained_leaf_content(data, path.parent, limits)
            if current.controller is not None:
                _controller_document(current.controller, caller, current.run_id, limits)
                identities.append((current.controller.path, current.controller.sha256))
        identities.append((path, digest))
    return tuple(identities)


def _retained_leaf_content(data: dict, directory: Path, limits: ContractLimits) -> None:
    """Validate retained bytes independently of now-removed preparation sources."""
    from .workspace import _read, inspect_retained_log

    files = _retained_artifacts(data, directory, limits)
    _validate_retained_provenance(directory, data, limits)
    _validate_native_exports(data, directory, files, limits)
    for row in data["baseline"]["files"]:
        entry = FileEntry(**row)
        _, actual = _read(directory / "baseline", entry.relative_path, entry.size)
        if actual != entry:
            raise ContractError("Retained baseline changed.", code="record_changed")
    if not _same_json(data["transcript"], inspect_retained_log(directory, limits.transcript_bytes)):
        raise ContractError("Retained transcript changed.", code="record_changed")


def _controller_document(
    reference: RecordReference, caller: Path, selected: str, limits: ContractLimits
) -> dict:
    """Bind a selected passing leaf to its immutable, ordered controller history."""
    from .workspace import _path, validate_project

    try:
        path = reference.path
        if path.name != "record.json" or path.parent.parent != caller / ".apm/controllers":
            raise ValueError("Controller is not caller-owned.")
        _path(caller, path.relative_to(caller).as_posix())
        data, digest = _record_bytes(path, limits)
        attempts = data["attempts"]
        if (
            digest != reference.sha256
            or data["schema"] != CONTROLLER_SCHEMA
            or data["controller_id"] != path.parent.name
            or data["caller_root"] != str(caller)
            or data["finalized"] is not True
            or data["phase"] != "finished"
            or data["selected_run_id"] != selected
            or not _same_json(data["execution"], Outcome.COMPLETE)
            or not isinstance(attempts, list)
            or not 1 <= len(attempts) <= data["budget"]["max_attempts"] <= 16
            or len({item["run_id"] for item in attempts}) != len(attempts)
            or attempts[-1]["run_id"] != selected
        ):
            raise ValueError("Controller selection or history changed.")
        project = _project_capture(data["project_capture"])
        if (
            project.root.parent.parent == caller / ".apm/controllers"
            and project.root != path.parent / "project"
        ):
            raise ValueError("Controller capture belongs to another invocation.")
        validate_project(project, caller, limits)
        for index, item in enumerate(attempts, start=1):
            child_path = caller / ".apm/runs" / item["run_id"] / "record.json"
            if (
                item["index"] != index
                or item["record"] != str(child_path)
                or child_path.parent.parent != caller / ".apm/runs"
            ):
                raise ValueError("Controller attempt identity changed.")
            _path(caller, child_path.relative_to(caller).as_posix())
            child, child_digest = _record_bytes(child_path, limits)
            _validate_version(child)
            if (
                child_digest != item["sha256"]
                or child["complete"] is not True
                or child["phase"] != "finished"
                or child["source"]["sha256"] != data["source"]["sha256"]
                or child["source"]["path"] != data["source"]["path"]
                or child["baseline"]["digest"] != data["baseline_digest"]
                or child["baseline"]["project_digest"] != project.digest
                or child["baseline"]["original_head"] != project.original_head
                or child["controller"] != {"record": str(path), "attempt": index}
                or not _same_json(child["execution"], item["outcome"])
                or (
                    index == len(attempts)
                    and (
                        not _same_json(child["result"], data["result"])
                        or not _same_json(child["execution"], Outcome.COMPLETE)
                    )
                )
                or (index < len(attempts) and not _same_json(child["execution"], Outcome.REJECTED))
            ):
                raise ValueError("Controller child observations changed.")
            _retained_leaf_content(child, child_path.parent, limits)
        return data
    except (ValueError, KeyError, TypeError, OSError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError("Controller evidence changed.", code="record_changed") from exc


def _controller_plan(plan: LeafPlan, result: RunResult) -> LeafPlan:
    """Recover the original capture, never substitute the current caller tree."""
    from .workspace import validate_project

    reference = result.controller
    if reference is None:
        raise ContractError("Controller reference is missing.", code="record_changed")
    data = _controller_document(reference, plan.project_root, result.run_id, plan.limits)
    try:
        snapshot = _project_capture(data["project_capture"])
        if (
            not _same_json(data["budget"], plan.contract.budget)
            or (plan.project_snapshot is not None and snapshot != plan.project_snapshot)
            or (
                plan.project_snapshot is None and snapshot.root != reference.path.parent / "project"
            )
        ):
            raise ValueError("Controller capture differs from the admitted invocation.")
        validate_project(snapshot, plan.project_root, plan.limits)
        return replace(plan, project_snapshot=snapshot)
    except (ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError("Controller capture changed.", code="record_changed") from exc


def _decoded_outcome(raw: dict) -> Outcome:
    value = Outcome[raw["name"]]
    if not _same_json(raw, value):
        raise ValueError("Outcome name, type and exit code disagree.")
    return value


def _decoded_run(raw: dict) -> RunResult:
    """Decode values only; the existing completion boundary remains the authority."""
    values = dict(raw)
    artifact = values["artifact"]
    if artifact is not None:
        if "files" in artifact:
            artifact = ArtifactSet(
                **{
                    **artifact,
                    "files": tuple(
                        Artifact(**{**item, "path": Path(item["path"])})
                        for item in artifact["files"]
                    ),
                }
            )
        else:
            artifact = Artifact(**{**artifact, "path": Path(artifact["path"])})
    checks = []
    for check in values["checks"]:
        process = dict(check["process"])
        for key in ("signals", "residual_group"):
            process[key] = tuple(process[key])
        checks.append(CheckObservation(**{**check, "process": ProcessObservation(**process)}))
    values.update(
        run_directory=Path(values["run_directory"]),
        outcome=_decoded_outcome(values["outcome"]),
        artifact=artifact,
        checks=tuple(checks),
        observed_models=tuple(values["observed_models"]),
        retained_provenance=tuple(FileEntry(**item) for item in values["retained_provenance"]),
        native_exports=tuple(FileEntry(**item) for item in values.get("native_exports", ())),
    )
    if values.get("controller") is not None:
        reference = values["controller"]
        values["controller"] = RecordReference(**{**reference, "path": Path(reference["path"])})
    return RunResult(**values)


def _validate_loaded_assessment(
    result: RunResult, *, controller_selected: str | None = None
) -> None:
    from .frontend import parse_contract
    from .workspace import _digest

    directory = result.run_directory
    data, _ = _record_bytes(directory / "record.json", ContractLimits())
    contract = parse_contract(directory / "source/contract.contract.md")
    inventory = tuple(FileEntry(**item) for item in data["baseline"]["files"])
    resources = tuple(item for item in inventory if item.relative_path.startswith("checks/"))
    caller = directory.parents[2]
    if (
        directory != caller / ".apm/runs" / result.run_id
        or data["caller_root"] != str(caller)
        or data["run_id"] != result.run_id
        or data["attempt_id"] != result.run_id + "/1"
        or data["profile"] != "native-advisory"
        or data["source"]["sha256"] != contract.source_digest
        or data["baseline"]["digest"] != _digest(inventory)
        or data["baseline"]["resources_digest"] != _digest(resources)
        or not _same_json(data["advisory_consent"], result.consent_source)
        or any(data[key] is not None for key in ("child_pid", "child_pgid", "active_check"))
        or any(data["controls"][key] != "unavailable" for key in ("isolation", "spend_cap"))
        or bool(contract.budget) != (result.controller is not None)
    ):
        raise ValueError("Retained source, baseline or invocation observations disagree.")
    validate_consent_source(result.consent_source)
    _validate_assessed_outputs(
        contract, result, data, _digest(resources), rejected=result.outcome is Outcome.REJECTED
    )
    if result.controller is not None:
        controller = _controller_document(
            result.controller, caller, controller_selected or result.run_id, ContractLimits()
        )
        if not _same_json(controller["budget"], contract.budget):
            raise ValueError("Retained controller budget differs from its declaration.")


def load_completed_result(path: Path) -> RunResult | ChainResult:
    """Read a finalized local result without resolving sources or changing outcomes.

    This is an integrity-checked local projection, not fresh admission, replay,
    signature verification or a claim of native isolation. Exporters must not
    substitute parsing their own success booleans for this boundary.
    """
    from .workspace import _path

    limits = ContractLimits()
    try:
        path = path.absolute()
        caller = path.parents[3]
        _path(caller, path.relative_to(caller).as_posix())
        data, digest = _record_bytes(path, limits)
        schema = data.get("schema")
        if schema == CHAIN_SCHEMA:
            raw = data["result"]
            result = ChainResult(
                **{
                    **raw,
                    "record_path": Path(raw["record_path"]),
                    "outcome": _decoded_outcome(raw["outcome"]),
                    "runs": tuple(_decoded_run(run) for run in raw["runs"]),
                }
            )
            if result.record_path != path or not 1 <= len(result.runs) <= limits.chain_contracts:
                raise ValueError("Factory record location/count differs.")
        elif schema in (RUN_SCHEMA, CONTROLLER_SCHEMA):
            result = _decoded_run(data["result"])
            if schema == CONTROLLER_SCHEMA:
                result = replace(result, controller=RecordReference(path, digest))
            else:
                if result.run_directory / "record.json" != path:
                    raise ValueError("Leaf record location differs.")
                if data.get("controller") is not None:
                    parent = Path(data["controller"]["record"])
                    if (
                        parent.name != "record.json"
                        or parent.parent.parent != caller / ".apm/controllers"
                    ):
                        raise ValueError("Controller is not caller-owned.")
                    _path(caller, parent.relative_to(caller).as_posix())
                    _, parent_digest = _record_bytes(parent, limits)
                    result = replace(result, controller=RecordReference(parent, parent_digest))
        else:
            raise ContractError("Unsupported retained record version.", code="record_version")
        if result.outcome is not Outcome.COMPLETE:
            raise ContractError(
                "The retained execution is not complete; its outcome remains unchanged.",
                code="incomplete_evidence",
            )
        runs = result.runs if isinstance(result, ChainResult) else (result,)
        for run in runs:
            if (
                run.run_directory != caller / ".apm/runs" / run.run_id
                or run.run_directory.name != run.run_id
            ):
                raise ValueError("Retained child is not owned by this caller.")
            _path(caller, run.run_directory.relative_to(caller).as_posix())
        boundary = CompletionBoundary()
        boundary.capture(result)
        for run in runs:
            _validate_loaded_assessment(run)
        boundary.validate(result)
        return result
    except (OSError, ValueError, KeyError, TypeError, IndexError, RecursionError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError(
            "Retained completion evidence is malformed or inconsistent.", code="record_changed"
        ) from exc


def completed_attempt_history(result: RunResult | ChainResult) -> tuple[RunResult, ...]:
    """Read assessed attempts, including rejections bound to completed controllers.

    Returned rejections remain rejections. This projection cannot authorize a
    handoff or make an unfinished invocation eligible for a completion export.
    """
    path = (
        result.record_path
        if isinstance(result, ChainResult)
        else result.run_directory / "record.json"
    )
    if load_completed_result(path) != result:
        raise ContractError("Retained result changed.", code="record_changed")
    history = []
    try:
        for run in result.runs if isinstance(result, ChainResult) else (result,):
            if run.controller is None:
                history.append(run)
                continue
            controller = _controller_document(
                run.controller, run.run_directory.parents[2], run.run_id, ContractLimits()
            )
            for item in controller["attempts"]:
                data, _ = _record_bytes(Path(item["record"]), ContractLimits())
                attempt = replace(_decoded_run(data["result"]), controller=run.controller)
                _validate_loaded_assessment(attempt, controller_selected=run.run_id)
                history.append(attempt)
        return tuple(history)
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError("Retained attempt history changed.", code="record_changed") from exc


class CompletionBoundary:
    """Seal successful retained identities before, and check them after, teardown."""

    def __init__(self) -> None:
        self._identities: tuple[tuple[Path, str], ...] | None = None

    def capture(self, result: RunResult | ChainResult) -> None:
        if result.outcome is Outcome.COMPLETE:
            try:
                self._identities = _retained_completion(result)
            except (ValueError, KeyError, TypeError) as exc:
                if isinstance(exc, ContractError):
                    raise
                raise ContractError(
                    "Completion evidence is malformed.", code="record_changed"
                ) from exc

    def validate(self, result: RunResult | ChainResult) -> None:
        if result.outcome is not Outcome.COMPLETE:
            return
        try:
            if (
                self._identities is None
                or any(
                    _record_bytes(path, ContractLimits())[1] != digest
                    for path, digest in self._identities
                )
                or _retained_completion(result) != self._identities
            ):
                raise ContractError(
                    "Completion record changed during preparation cleanup.", code="record_changed"
                )
        except (ValueError, KeyError, TypeError) as exc:
            if isinstance(exc, ContractError):
                raise
            raise ContractError("Completion evidence is malformed.", code="record_changed") from exc


class AttemptStore:
    """Private, atomically updated local observations, not protected provenance."""

    def __init__(
        self,
        run_id: str,
        directory: Path,
        data: dict[str, object],
        *,
        retained_provenance: tuple[FileEntry, ...] = (),
    ) -> None:
        self.run_id = run_id
        self.directory = directory
        self.record_path = directory / "record.json"
        self._data = data
        self.retained_provenance = retained_provenance

    @classmethod
    def create(
        cls,
        plan: LeafPlan,
        *,
        consent_source: str = "flag",
        handoff_policy: str | None = None,
    ) -> "AttemptStore":
        """Allocate unique run/attempt identity only after admission."""
        from .workspace import capture_provenance

        validate_consent_source(consent_source)
        expected_parent = plan.project_root / ".apm" / "runs"
        parent = plan.evidence_root or expected_parent
        if parent != expected_parent:
            raise ContractError(
                "Run storage must remain caller-owned .apm/runs.", code="unsafe_run_directory"
            )
        run_id, directory = _allocate_directory(plan.project_root, "runs")
        retained, retained_identities = capture_provenance(plan, directory)
        store = cls(
            run_id,
            directory,
            {
                "schema": RUN_SCHEMA,
                "assurance": dict(NATIVE_ASSURANCE),
                "run_id": run_id,
                "attempt_id": run_id + "/1",
                "created_at": datetime.now(UTC).isoformat(),
                "profile": "native-advisory",
                "advisory_consent": consent_source,
                "handoff_policy": handoff_policy,
                "policy_status": plan.policy_status,
                "provenance": "same-user local observations; not protected or signed",
                "phase": "admitted",
                "complete": False,
                "source": {
                    "path": str(plan.contract.path),
                    "sha256": plan.contract.source_digest,
                    "package": _package_identity(plan),
                    "retained": retained,
                    "retained_identities": retained_identities,
                },
                "caller_root": str(plan.project_root),
                "evidence_root": str(parent),
                "manifest_sha256": plan.manifest_digest,
                "lock_sha256": plan.lock_digest,
                "consumer_manifest_sha256": plan.consumer_manifest_digest,
                "consumer_lock_sha256": plan.consumer_lock_digest,
                "apm_backend": plan.apm_backend,
                "harness": plan.harness,
                "executable": str(plan.executable),
                "executable_version": plan.executable_version,
                "requested_model": plan.model,
                "observed_models": [],
                "input_bindings": plan.input_bindings,
                "imports": _import_identities(plan),
                "limits": _limit_identity(plan),
                "controls": {
                    "isolation": "unavailable",
                    "spend_cap": "unavailable",
                    "process_cleanup": (
                        "assigned Windows Job Object; outside-job processes unobserved"
                        if os.name == "nt"
                        else "original POSIX process group; escaped descendants unobserved"
                    ),
                },
            },
            retained_provenance=retained_identities,
        )
        store._write()
        return store

    def _write(self) -> None:
        atomic_write_text(
            self.record_path,
            json.dumps(_json_value(self._data), ensure_ascii=True, indent=2) + "\n",
            new_file_mode=0o600,
            durable=True,
        )

    def update(self, phase: str, **observations: object) -> None:
        """Commit observations without advertising a completed outcome."""
        self._data.update(observations)
        self._data["phase"] = phase
        self._write()

    def finish(self, result: RunResult) -> None:
        """Persist the final result before any terminal success announcement."""
        from .workspace import inspect_retained_log

        try:
            if (self.directory / "transcript.log").exists():
                self._data["transcript"] = inspect_retained_log(
                    self.directory, ContractLimits().transcript_bytes
                )
            elif result.outcome is Outcome.COMPLETE or native_assurance_limited(result):
                raise ContractError("Final transcript is missing.", code="transcript_missing")
            self._data.update(
                complete=True,
                phase="finished",
                child_pid=None,
                child_pgid=None,
                active_check=None,
                finished_at=datetime.now(UTC).isoformat(),
                result=result,
                execution=result.outcome,
            )
            self._write()
        except (ContractError, OSError, KeyboardInterrupt) as exc:
            self.fail_finalization(result, exc)

    def finalize(self, plan: LeafPlan, result: RunResult) -> RunResult:
        """Issue COMPLETE only through the exact persisted-evidence validator."""
        self.finish(result)
        if not native_assurance_limited(result):
            return result
        try:
            _assessed_inputs(plan, result)
        except ContractError as exc:
            failed = replace(
                result,
                outcome=exc.outcome,
                stop_reason=exc.code if exc.outcome is Outcome.HALTED else None,
            )
            try:
                self.update(
                    "finalization_failed",
                    complete=False,
                    result=failed,
                    execution=failed.outcome,
                    validation_error={"code": exc.code, "message": str(exc)},
                )
            except (OSError, KeyboardInterrupt) as persistence_error:
                self.fail_finalization(failed, persistence_error)
            raise
        except (OSError, KeyboardInterrupt) as exc:
            self.fail_finalization(result, exc)
        completed = replace(result, outcome=Outcome.COMPLETE)
        self.finish(completed)
        try:
            _assessed_inputs(plan, completed)
        except (ContractError, OSError, KeyboardInterrupt) as exc:
            self.fail_finalization(completed, exc)
        return completed

    def fail_finalization(self, result: RunResult | ChainResult, error: BaseException) -> None:
        """Retain the same incomplete state for transcript and record failures."""
        is_chain = isinstance(result, ChainResult)
        code = "chain_finalization_failure" if is_chain else "finalization_failure"
        failed = replace(result, outcome=Outcome.HALTED, stop_reason=code)
        if is_chain:
            failed = replace(failed, complete=False)
        self._data.update(
            complete=False,
            phase="finalization_failed",
            result=failed,
            execution=failed.outcome,
            finalization_error={"type": type(error).__name__, "message": str(error)},
        )
        try:
            self._write()
        except (OSError, KeyboardInterrupt) as repair_error:
            raise ContractError(
                f"Run record finalization failed at {self.record_path}; failure-state "
                "persistence also failed. Treat this invocation as HALTED and do not "
                f"rely on a visible success record. Original failure: {type(error).__name__}: {error}",
                code=code,
            ) from repair_error
        raise ContractError(
            f"Run record finalization failed at {self.record_path}. The attempt is "
            "incomplete; inspect filesystem durability before retrying.",
            code=code,
        ) from error


class ControllerStore(AttemptStore):
    """Link attempts durably; acceptance remains the leaf record owner's decision."""

    @classmethod
    def create_controller(cls, plan: LeafPlan, consent_source: str) -> "ControllerStore":
        validate_consent_source(consent_source)
        if plan.contract.budget is None:
            raise ContractError("A repair controller requires an authored budget.")
        identity, directory = _allocate_directory(plan.project_root, "controllers")
        store = cls(
            identity,
            directory,
            {
                "schema": CONTROLLER_SCHEMA,
                "controller_id": identity,
                "caller_root": str(plan.project_root),
                "source": {"path": str(plan.contract.path), "sha256": plan.contract.source_digest},
                "budget": plan.contract.budget,
                "advisory_consent": consent_source,
                "assurance": dict(NATIVE_ASSURANCE),
                "provenance": "same-user observations; not signed or protected",
                "created_at": datetime.now(UTC).isoformat(),
                "phase": "admitted",
                "finalized": False,
                "attempts": [],
                "selected_run_id": None,
            },
        )
        store._write()
        return store

    def freeze(self, plan: LeafPlan) -> None:
        from .workspace import _digest

        if plan.input_inventory is None or plan.project_snapshot is None:
            raise ContractError("Controller inputs must be captured.", code="unresolved_inputs")
        self.update(
            "captured",
            project_capture=plan.project_snapshot,
            baseline_digest=_digest(plan.input_inventory),
        )

    def attach_attempt(self, attempt: AttemptStore) -> None:
        """Record allocation before any producer starts, including failed finalization."""
        attempts = self._data["attempts"]
        attempts.append(
            {
                "index": len(attempts) + 1,
                "run_id": attempt.run_id,
                "record": str(attempt.record_path),
                "sha256": None,
                "outcome": None,
            }
        )
        self.update("executing")

    def record_attempt(self, result: RunResult, limits: ContractLimits) -> None:
        row = self._data["attempts"][-1]
        if row["run_id"] != result.run_id or result.controller is not None:
            raise ContractError("Controller attempt identity disagrees.", code="record_changed")
        data, digest = _record_bytes(result.run_directory / "record.json", limits)
        if not _same_json(data["result"], result):
            raise ContractError("Controller child result changed.", code="record_changed")
        row.update(sha256=digest, outcome=result.outcome)
        self.update("assessed")

    def finish_controller(self, plan: LeafPlan, result: RunResult, reason: str) -> RunResult:
        if result.outcome is Outcome.COMPLETE:
            _assessed_inputs(plan, result)
        self.update(
            "finished",
            finalized=True,
            finished_at=datetime.now(UTC).isoformat(),
            selected_run_id=result.run_id if result.outcome is Outcome.COMPLETE else None,
            result=result,
            execution=result.outcome,
            stop_reason=reason,
        )
        _, digest = _record_bytes(self.record_path, plan.limits)
        linked = replace(result, controller=RecordReference(self.record_path, digest))
        if result.outcome is Outcome.COMPLETE:
            finalized_inputs(plan, linked)
        return linked

    def abort(self, code: str, error: BaseException) -> None:
        """Never leave a visible selected success after controller persistence fails."""
        try:
            self.update(
                "halted",
                finalized=False,
                selected_run_id=None,
                execution=Outcome.HALTED,
                result=None,
                stop_reason=code,
                error={"type": type(error).__name__, "message": str(error)},
            )
        except (OSError, KeyboardInterrupt) as persistence_error:
            raise ContractError(
                f"Controller failure-state persistence failed at {self.record_path}. "
                "Treat this invocation as HALTED; do not rely on a visible success.",
                code="controller_finalization_failure",
            ) from persistence_error


class ChainStore(AttemptStore):
    """Aggregate observations using the same durable writer as leaf attempts."""

    @classmethod
    def create_chain(
        cls,
        caller: Path,
        graph: dict,
        *,
        allow_unproven: bool,
        allow_host_access: bool,
        consent_source: str = "flag",
    ) -> "ChainStore":
        validate_consent_source(consent_source)
        identity, directory = _allocate_directory(caller, "chains")
        store = cls(
            identity,
            directory,
            {
                "schema": CHAIN_SCHEMA,
                "assurance": dict(NATIVE_ASSURANCE),
                "chain_id": identity,
                "caller_root": str(caller),
                "complete": False,
                "phase": "admitted",
                "graph": graph,
                "allow_unproven_inputs": allow_unproven,
                "allow_host_access": allow_host_access,
                "consent_source": consent_source,
                "handoff_policy": handoff_policy(allow_unproven),
                "provenance": "same-user observations; not signed or protected",
                "cache": "unavailable: native observed read sets are unavailable",
                "spend_cap": "unavailable",
                "nodes": [],
                "artifacts": None,
            },
        )
        store._write()
        return store

    def finish_chain(self, result: ChainResult, nodes: list[dict]) -> None:
        from .workspace import inspect_artifact_view, inspect_retained_log

        try:
            if result.complete:
                view = self._data.get("artifacts")
                if not isinstance(view, ArtifactView) or view.root != self.directory / "artifacts":
                    raise OSError("A complete chain requires its retained artifact view.")
                inspect_artifact_view(view)
            if (self.directory / "transcript.log").exists():
                self._data["transcript"] = inspect_retained_log(
                    self.directory,
                    ContractLimits().transcript_bytes,
                )
            elif result.complete:
                raise OSError("Final chain transcript is missing.")
            if result.complete:
                _validate_chain_completion(self._data, result, nodes)
            self.update(
                "finished",
                complete=result.complete,
                nodes=nodes,
                result=result,
                execution=result.outcome,
            )
            data, _ = _record_bytes(self.record_path, ContractLimits())
            if not _same_json(data, self._data):
                raise ContractError("Final factory record changed.", code="record_changed")
        except (ValueError, KeyError, TypeError, OSError, KeyboardInterrupt) as exc:
            self.fail_finalization(result, exc)


def halt_chain(result: ChainResult | RunResult, reason: str) -> None:
    """Record a preparation-context exit failure before the CLI can announce completion."""
    from .workspace import _path

    is_chain = isinstance(result, ChainResult)
    record_path = result.record_path if is_chain else result.run_directory / "record.json"
    caller = record_path.parents[3]
    _path(caller, record_path.relative_to(caller).as_posix())
    data, _ = _record_bytes(record_path, ContractLimits())
    if is_chain:
        if data.get("schema") != CHAIN_SCHEMA:
            raise ContractError("Unsupported factory record version.", code="record_version")
    else:
        _validate_version(data)
    if not isinstance(data.get("result"), dict) or not _same_json(data["result"], result):
        raise ContractError(
            "Chain record changed before context finalization.", code="record_changed"
        )
    stopped = replace(result, outcome=Outcome.HALTED, stop_reason=reason)
    if is_chain:
        nodes = _chain_nodes(data)
        store = ChainStore(result.chain_id, record_path.parent, data)
        store.finish_chain(replace(stopped, complete=False), nodes)
    else:
        store = AttemptStore(result.run_id, record_path.parent, data)
        store.finish(stopped)


def preparation_failure(
    result: ChainResult | RunResult | None, error: ContractError
) -> ContractError:
    """Keep context-exit failures structured even if recording their stop also fails."""
    if result is None:
        return error
    try:
        halt_chain(result, error.code)
    except (ValueError, KeyError, TypeError, OSError, KeyboardInterrupt):
        path = (
            result.record_path
            if isinstance(result, ChainResult)
            else result.run_directory / "record.json"
        )
        return ContractError(
            f"Preparation failed ({error.code}): {error}. Stop persistence is unconfirmed at {path}. "
            "Do not rely on a visible completed record; inspect storage before retrying.",
            code="chain_finalization_failure"
            if isinstance(result, ChainResult)
            else "finalization_failure",
        )
    return ContractError(str(error), code=error.code)
