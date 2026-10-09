"""Bounded orchestration around the unchanged single-attempt acceptance boundary."""

import json
import time
from dataclasses import replace

from ..core.contract_logger import ContractLogger
from . import records, workspace
from .events import EventEmitter
from .models import ContractError, LeafPlan, Outcome, RepairContext, RunResult

DIAGNOSTIC_BYTES = 12 * 1024


def _diagnostics(result: RunResult, plan: LeafPlan) -> str:
    raw, _ = workspace._read(result.run_directory, "transcript.log", plan.limits.transcript_bytes)
    return json.dumps(
        {
            "checks": [
                {"name": check.name, "exit_code": check.process.returncode, "reason": check.reason}
                for check in result.checks
            ],
            "transcript_tail": raw[-DIAGNOSTIC_BYTES:].decode("utf-8", errors="replace"),
        },
        ensure_ascii=True,
    )


def run_repair(
    plan: LeafPlan,
    *,
    logger: ContractLogger,
    allow_advisory: bool,
    consent_source: str,
    allow_unproven_inputs: bool | None,
    announce_result: bool,
) -> RunResult:
    """Retry only validated candidate rejection; retain every independent attempt."""
    from .engine import run_attempt

    budget = plan.contract.budget
    if budget is None:
        raise ContractError("Repair requires an explicit budget.", code="invalid_budget")
    logger.repair_budget(budget)
    store = records.ControllerStore.create_controller(plan, consent_source)
    deadline = time.monotonic() + budget.max_seconds
    try:
        project = plan.project_snapshot or workspace.capture_project(plan, store.directory)
        plan = replace(plan, project_snapshot=project)
        inventory = workspace.inspect_workspace(plan)
        if plan.input_inventory is not None and plan.input_inventory != inventory:
            raise ContractError("Admitted inputs changed before repair.", code="plan_changed")
        plan = replace(plan, input_inventory=inventory)
        store.freeze(plan)
        context = RepairContext(store.record_path, 1)
        seen = set()
        for index in range(1, budget.max_attempts + 1):
            if time.monotonic() >= deadline:
                raise ContractError(
                    "The shared repair execution/check allowance expired.", code="budget_deadline"
                )
            attempt_logger = logger.new_attempt(index=index, count=budget.max_attempts)
            try:
                result = run_attempt(
                    plan,
                    logger=attempt_logger,
                    allow_advisory=allow_advisory,
                    consent_source=consent_source,
                    allow_unproven_inputs=allow_unproven_inputs,
                    announce_result=False,
                    shared_deadline=deadline,
                    repair_context=context,
                    on_created=store.attach_attempt,
                )
            finally:
                attempt_logger.close()
            store.record_attempt(result, plan.limits)
            reason = "complete"
            if result.outcome is not Outcome.COMPLETE:
                previous = records.rejected_inputs(plan, result)
                signature = result.artifact.sha256 if result.artifact is not None else None
                if previous is None:
                    reason = "not_retryable"
                elif signature in seen:
                    reason = "no_progress"
                elif index == budget.max_attempts:
                    reason = "max_attempts"
                else:
                    seen.add(signature)
                    context = RepairContext(
                        store.record_path, index + 1, previous, _diagnostics(result, plan)
                    )
                    continue
            result = store.finish_controller(plan, result, reason)
            if announce_result:
                EventEmitter(result.run_id, attempt_logger.on_event).emit("finished", result=result)
                logger.repair_finished(reason=reason, record=store.record_path, attempts=index)
            return result
        raise ContractError("Repair budget contained no attempts.", code="invalid_budget")
    except (ContractError, OSError, KeyboardInterrupt) as exc:
        code = (
            exc.code
            if isinstance(exc, ContractError)
            else ("cancelled" if isinstance(exc, KeyboardInterrupt) else "filesystem_error")
        )
        store.abort(code, exc)
        raise ContractError(
            f"Repair stopped ({code}); inspect {store.record_path}. {exc}",
            code=code,
            outcome=exc.outcome if isinstance(exc, ContractError) else Outcome.HALTED,
        ) from exc
