"""Per-stage harness and model facts recorded by a receipt.

``factory_stages`` is the small read interface over the provenance's
``internalParameters.stages[]``: one entry per contract of the delivery, with
the harness, its reported version, the requested model and every model the
harness reported across all attempts of that contract (rejected ones
included). In a receipt of record written by ``apmx check`` these values were
reported by the producer job (``reportedBy: "producer"``); the checks were
rerun by ``apmx check``. They are facts, not policy: v0.7 enforces no model
or harness rule.
"""

from __future__ import annotations

from . import verifier

KEYS = (
    "contract",
    "harness",
    "harnessVersion",
    "requestedModel",
    "observedModels",
    "attempts",
    "budget",
)


def factory_stages(receipt: verifier.Receipt) -> list[dict]:
    """``[{contract, harness, harnessVersion, requestedModel, observedModels, attempts, budget}]``."""
    stages = receipt.provenance["predicate"]["buildDefinition"]["internalParameters"]["stages"]
    result = []
    for stage in stages:
        item = {key: stage.get(key) for key in KEYS}
        item["observedModels"] = list(stage.get("observedModels") or [])
        for key in ("reportedBy", "checkedBy"):
            if key in stage:
                item[key] = stage[key]
        result.append(item)
    return result
