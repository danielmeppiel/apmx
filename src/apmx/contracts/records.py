"""One local authority for check normalization, outcomes and attempt records."""

import json
import math
import os
from dataclasses import fields, is_dataclass, replace
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from uuid import uuid4

from ..utils.atomic_io import atomic_write_text
from ..utils.git_env import redact_git_diagnostic
from ..utils.path_security import has_symlink_component
from .models import (
    Artifact,
    CheckObservation,
    ContractError,
    ContractLimits,
    LeafPlan,
    Outcome,
    ProcessObservation,
    RunResult,
    RetainedInput,
    ChainResult,
    ArtifactView,
    FileEntry,
)


def normalize_check(process: ProcessObservation, *, integrity_ok: bool = True) -> int:
    """Retain raw exit separately; absence of prose does not weaken exit one."""
    if not integrity_ok or process.error or process.stop_reason or not process.cleanup_confirmed:
        return 2
    return process.returncode if process.returncode in (0, 1, 2) else 2


def reduce_outcome(
    artifact: Artifact | None,
    checks: tuple[CheckObservation, ...],
    stop_reason: str | None,
) -> Outcome:
    """Reduce native leaf results without claiming an enforced host boundary."""
    if stop_reason:
        return Outcome.HALTED
    if any(check.normalized == 1 for check in checks):
        return Outcome.REJECTED
    return Outcome.UNPROVEN


def native_assurance_limited(result: RunResult) -> bool:
    """Identify completed passing checks whose only limit is the native host."""
    return (
        result.outcome == Outcome.UNPROVEN
        and result.stop_reason is None
        and result.artifact is not None
        and bool(result.checks)
        and all(check.normalized == 0 for check in result.checks)
    )


def handoff_policy(allow_unproven: bool) -> str:
    return "native-assurance-exception" if allow_unproven else "VERIFIED-only"


def validate_consent_source(source: str) -> None:
    if source not in ("flag", "interactive"):
        raise ContractError("Unknown execution consent source.", code="invalid_consent")


def _json_value(value: object) -> object:
    if isinstance(value, IntEnum):
        return {"name": value.name, "exit_code": int(value)}
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _allocate_directory(caller: Path, family: str) -> tuple[str, Path]:
    parent = caller / ".apm" / family
    if has_symlink_component(caller, parent):
        raise ContractError("Evidence storage contains a symlink.", code="unsafe_run_directory")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    identity = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:12]
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


def validate_binding(binding: RetainedInput, caller: Path, limits: ContractLimits) -> None:
    """Re-read the exact finalized record and retained bytes; never select another run."""
    from .workspace import _read, _path

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
    if identity != binding.record_sha256:
        raise ContractError("Finalized predecessor record changed.", code="record_changed")
    _validate_retained_provenance(directory, data, limits)
    _, artifact = _read(directory / "artifacts", binding.artifact.relative_path, limits.file_bytes)
    if (artifact.sha256, artifact.size) != (binding.artifact.sha256, binding.artifact.size):
        raise ContractError("Retained predecessor output changed.", code="artifact_changed")


def finalized_input(plan: LeafPlan, result: RunResult) -> RetainedInput:
    """Validate this owner's persisted assessment against the direct typed leaf result.

    This proves completed observations, not a downstream assurance policy. The
    separate handoff gate below decides whether those observations may advance.
    """
    from .workspace import _digest, _read, inspect_retained_log, inspect_workspace

    try:
        directory = result.run_directory
        if directory.parent != plan.project_root / ".apm/runs" or directory.name != result.run_id:
            raise ValueError("Run identity differs from the caller.")
        data, digest = _record_bytes(directory / "record.json", plan.limits)
        if not (
            data["schema"] == "apm-contract-run/0.1"
            and data["profile"] == "native-advisory"
            and data["complete"] is True
            and data["phase"] == "finished"
            and data["run_id"] == result.run_id
            and data["caller_root"] == str(plan.project_root)
            and data["source"]["sha256"] == plan.contract.source_digest
            and data["source"]["path"] == str(plan.contract.path)
            and _same_json(data["result"], result)
            and _same_json(data["source"]["retained_identities"], result.retained_provenance)
            and all(data[key] is None for key in ("child_pid", "child_pgid", "active_check"))
        ):
            raise ValueError("Finalized leaf identity or observations differ.")
        if result.outcome != reduce_outcome(result.artifact, result.checks, result.stop_reason):
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
            "limits": plan.limits,
            "attempt_id": result.run_id + "/1",
            "evidence_root": str(plan.project_root / ".apm/runs"),
            "executable_version": plan.executable_version,
            "apm_backend": plan.apm_backend,
            "advisory_consent": result.consent_source,
            "handoff_policy": result.handoff_policy,
        }
        if any(
            not _same_json(data[key], value) for key, value in expected_identity.items()
        ) or not _same_json(data["source"]["package"], _package_identity(plan)):
            raise ValueError("Planned source/import/caller identities differ from the record.")
        if any(data["controls"][key] != "unavailable" for key in ("isolation", "spend_cap")):
            raise ValueError("Native assurance controls differ from the admitted profile.")
        if result.outcome in (Outcome.REJECTED, Outcome.HALTED):
            raise ContractError(
                f"Predecessor stopped: {result.stop_reason or result.outcome.name}. Inspect its record.",
                code=result.stop_reason or "upstream_rejected",
                outcome=result.outcome,
            )
        expected = tuple((check.name, check.command) for check in plan.contract.checks)
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
        if artifact.relative_path != plan.contract.produces:
            raise ValueError("Output differs from the declaration.")
        if not _same_json(data["checks"], result.checks) or not _same_json(
            data["artifact"], artifact
        ):
            raise ValueError("Duplicated artifact/check observations differ.")
        producer = ProcessObservation(**data["producer"])
        if (
            type(producer.returncode) is not int
            or normalize_check(producer) != 0
            or producer.cleanup_confirmed is not True
            or result.stop_reason is not None
            or not _same_json(data.get("native_reported_exit_code"), None)
            and not _same_json(data.get("native_reported_exit_code"), 0)
        ):
            raise ValueError("Producer completion or cleanup is unconfirmed.")
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
        ):
            raise ValueError("Recorded captured inputs/resources differ from admission.")
        for check in result.checks:
            if check.subject_digest != artifact.sha256 or check.resources_digest != _digest(
                resources
            ):
                raise ValueError("A check assessed different bytes.")
            if type(check.normalized) is not int or check.normalized != normalize_check(
                check.process
            ):
                raise ValueError("Raw and normalized check observations disagree.")
            if check.normalized != 0:
                raise ContractError(
                    "Required checks did not all pass; no handoff.",
                    code="incomplete_checks",
                    outcome=Outcome.UNPROVEN,
                )
            if (
                type(check.process.returncode) is not int
                or check.process.cleanup_confirmed is not True
            ):
                raise ValueError("Check completion/cleanup is unconfirmed.")
        for item in inventory:
            _, observed = _read(directory / "baseline", item.relative_path, item.size)
            if observed != item:
                raise ValueError("Retained baseline changed.")
        if not _same_json(
            data["transcript"], inspect_retained_log(directory, plan.limits.transcript_bytes)
        ):
            raise ValueError("Finalized transcript changed.")
        binding = RetainedInput(artifact, directory / "record.json", digest)
        validate_binding(binding, plan.project_root, plan.limits)
        return binding
    except (ValueError, KeyError, TypeError, RecursionError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError(
            "Finalized leaf evidence is inconsistent. Inspect its record.", code="record_changed"
        ) from exc


def admit_handoff(plan: LeafPlan, result: RunResult, *, allow_unproven: bool) -> RetainedInput:
    """ASF strict gate, with a separately authorized native-assurance exception."""
    binding = finalized_input(plan, result)
    if result.outcome == Outcome.VERIFIED:
        return binding
    if allow_unproven and native_assurance_limited(result):
        return binding
    raise ContractError(
        "UNPROVEN input blocked by the strict VERIFIED-only handoff policy. For trusted local "
        "development, explicitly select --allow-unproven-inputs; this does not certify isolation.",
        code="unproven_input",
        outcome=Outcome.UNPROVEN,
    )


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
                "schema": "apm-contract-run/0.1",
                "run_id": run_id,
                "attempt_id": run_id + "/1",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "profile": "native-advisory",
                "advisory_consent": consent_source,
                "handoff_policy": handoff_policy,
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
                "limits": plan.limits,
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

        if (self.directory / "transcript.log").exists():
            self._data["transcript"] = inspect_retained_log(
                self.directory, ContractLimits().transcript_bytes
            )
        elif result.outcome == Outcome.VERIFIED or native_assurance_limited(result):
            raise ContractError("Final transcript is missing.", code="transcript_missing")
        self._data.update(
            complete=True,
            phase="finished",
            child_pid=None,
            child_pgid=None,
            active_check=None,
            finished_at=datetime.now(timezone.utc).isoformat(),
            result=result,
        )
        try:
            self._write()
        except (OSError, KeyboardInterrupt) as exc:
            self.fail_finalization(result, exc)

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
        )
        try:
            self._write()
        except (OSError, KeyboardInterrupt) as repair_error:
            raise ContractError(
                f"Run record finalization failed at {self.record_path}; failure-state "
                "persistence also failed. Treat this invocation as HALTED and do not "
                "rely on a visible success record.",
                code=code,
            ) from repair_error
        raise ContractError(
            f"Run record finalization failed at {self.record_path}. The attempt is "
            "incomplete; inspect filesystem durability before retrying.",
            code=code,
        ) from error


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
                "schema": "apmx-contract-chain/0.1",
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
        from .workspace import inspect_retained_log, inspect_artifact_view

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
            self.update("finished", complete=result.complete, nodes=nodes, result=result)
        except (ContractError, OSError, KeyboardInterrupt) as exc:
            self.fail_finalization(result, exc)


def halt_chain(result: ChainResult, reason: str) -> None:
    """Record a preparation-context exit failure before the CLI can announce completion."""
    from .workspace import _path

    caller = result.record_path.parents[3]
    _path(caller, result.record_path.relative_to(caller).as_posix())
    data, _ = _record_bytes(result.record_path, ContractLimits())
    if not _same_json(data["result"], result):
        raise ContractError(
            "Chain record changed before context finalization.", code="record_changed"
        )
    store = ChainStore(result.chain_id, result.record_path.parent, data)
    store.record_path = result.record_path
    stopped = replace(result, complete=False, outcome=Outcome.HALTED, stop_reason=reason)
    store.finish_chain(stopped, data["nodes"])


def preparation_failure(result: ChainResult | None, error: ContractError) -> ContractError:
    """Keep context-exit failures structured even if recording their stop also fails."""
    if result is None:
        return error
    try:
        halt_chain(result, error.code)
    except (ValueError, OSError, KeyboardInterrupt):
        return ContractError(
            f"Preparation failed and chain stop persistence is unconfirmed at {result.record_path}. "
            "Do not rely on a visible completed record; inspect storage before retrying.",
            code="chain_finalization_failure",
        )
    return ContractError(str(error), code=error.code)
