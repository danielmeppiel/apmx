"""Per-stage harness and model facts of a receipt, for the factory policy gate.

``factory_stages`` returns one dict per contract in definition order:
``{contract, harness, harnessVersion, requestedModel, observedModels, attempts,
budget}``. This is the shape SLSA ``internalParameters.stages[]`` carries in the
v2 receipt; this body reads the v1 receipt's ``attempts/<run>/execution.json``.
``observedModels`` is the union over every attempt, rejected ones included:
a model that touched the work counts.
"""

from __future__ import annotations

from . import verifier


def factory_stages(receipt: verifier.Receipt) -> list[dict]:
    by_contract: dict[str, list[dict]] = {}
    for run_id, execution in receipt.executions.items():
        statement = receipt.statements.get(f"provenance/{run_id}.intoto.json", {})
        digest = (
            statement.get("predicate", {})
            .get("buildDefinition", {})
            .get("externalParameters", {})
            .get("contractSha256")
        )
        by_contract.setdefault(str(digest), []).append(execution)
    stages = []
    for stage in receipt.definition.get("stages", []):
        digest = stage.get("contractSha256")
        attempts = by_contract.get(str(digest), [])
        last = attempts[-1] if attempts else {}
        observed: list[str] = []
        for attempt in attempts:
            observed += [m for m in attempt.get("observedModels") or () if m not in observed]
        harnesses = {attempt.get("harness") for attempt in attempts}
        versions = {attempt.get("harnessVersion") for attempt in attempts}
        requested = {attempt.get("requestedModel") for attempt in attempts}
        stages.append(
            {
                "contract": last.get("contract") or f"sha256:{str(digest)[:12]}..",
                # Attempts of one stage share one invocation; disagreement is never hidden.
                "harness": last.get("harness") if len(harnesses) == 1 else "mixed",
                "harnessVersion": last.get("harnessVersion") if len(versions) == 1 else None,
                "requestedModel": last.get("requestedModel") if len(requested) == 1 else None,
                "observedModels": observed,
                "attempts": len(attempts),
                "budget": stage.get("budget"),
            }
        )
    return stages
