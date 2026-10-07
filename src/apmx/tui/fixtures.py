"""Labeled synthetic replay fixtures for the terminal design-review loop.

Every line here is invented content for visual iteration (docs/textual-design.md,
"Mandatory terminal design-review loop"). It is built from the real ``RunEvent``
shape in ``contracts/events.py``/``contracts/models.py`` so the activity feed
looks like genuine engine output, but it is never presented as a captured run:
the UI always shows a persistent "FIXTURE REPLAY" banner while a fixture
script is driving it. No model calls, no network use, no retained evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..contracts.models import RunEvent

CardStatus = Literal[
    "pending", "running", "checking", "retrying", "passed", "failed", "blocked", "cancelled"
]


@dataclass(frozen=True)
class CardStatusChange:
    identity: str
    status: CardStatus


@dataclass(frozen=True)
class Step:
    delay: float
    payload: RunEvent | CardStatusChange


def _event(kind: str, source: str = "engine", **data: object) -> RunEvent:
    return RunEvent(
        run_id="fixture", sequence=0, elapsed_seconds=0.0, kind=kind, source=source, data=data
    )


def branched_chain_script() -> tuple[Step, ...]:
    """A fork/join timeline: plan -> {spec, design} -> build -> {docs, tests} -> review."""
    steps: list[Step] = []

    def add(delay: float, payload: RunEvent | CardStatusChange) -> None:
        steps.append(Step(delay, payload))

    add(0.0, CardStatusChange("plan.contract.md", "running"))
    add(
        0.3,
        _event("activity", source="harness", text="Reading request.md and repository context..."),
    )
    add(
        0.6,
        _event(
            "activity",
            source="harness",
            text="Drafting plan.md with Goals, Changes, Validation, Risks.",
        ),
    )
    add(0.4, CardStatusChange("plan.contract.md", "checking"))
    add(
        0.2,
        _event(
            "check_started",
            source="engine",
            name="plan-sections",
            command="python3 checks/documents.py planning plan.md",
        ),
    )
    add(
        0.5,
        _event(
            "activity",
            source="checker",
            label="plan-sections",
            stream="stdout",
            text="plan.md: Goal present",
        ),
    )
    add(
        0.2,
        _event(
            "activity",
            source="checker",
            label="plan-sections",
            stream="stdout",
            text="plan.md: Changes present",
        ),
    )
    add(0.2, _event("check_finished", source="engine", name="plan-sections", status="pass"))
    add(0.1, CardStatusChange("plan.contract.md", "passed"))
    add(0.3, CardStatusChange("spec.contract.md", "running"))
    add(0.3, CardStatusChange("design.contract.md", "running"))
    add(0.5, _event("activity", source="harness", text="Turning plan.md into specification.md..."))
    add(
        0.5,
        _event("activity", source="harness", text="Drafting design.md with module boundaries..."),
    )
    add(0.4, CardStatusChange("spec.contract.md", "passed"))
    add(0.4, CardStatusChange("design.contract.md", "passed"))
    add(0.3, CardStatusChange("build.contract.md", "running"))
    add(
        0.5,
        _event(
            "activity",
            source="harness",
            text="Implementing changes.diff against specification.md and design.md...",
        ),
    )
    add(
        0.4,
        _event(
            "activity",
            source="harness",
            stream="stderr",
            text="warning: unused import 'os' in src/pricing.py (non-blocking lint note)",
        ),
    )
    add(0.3, CardStatusChange("build.contract.md", "checking"))
    add(
        0.2,
        _event(
            "check_started",
            source="engine",
            name="checkout-tests",
            command="behave checks/checkout.feature",
        ),
    )
    add(
        0.5,
        _event(
            "activity",
            source="checker",
            label="checkout-tests",
            stream="stdout",
            text="Scenario: Free delivery at 5000 cents ... FAILED",
        ),
    )
    add(0.2, _event("check_finished", source="engine", name="checkout-tests", status="fail"))
    add(0.1, CardStatusChange("build.contract.md", "retrying"))
    add(
        0.4,
        _event(
            "diagnostic",
            source="engine",
            severity="warning",
            message="Attempt 1 of 3 rejected; repairing with unchanged inputs.",
        ),
    )
    add(
        0.5,
        _event(
            "activity",
            source="harness",
            text="Repairing changes.diff: fix free-delivery threshold comparison.",
        ),
    )
    add(0.3, CardStatusChange("build.contract.md", "checking"))
    add(
        0.2,
        _event(
            "check_started",
            source="engine",
            name="checkout-tests",
            command="behave checks/checkout.feature",
        ),
    )
    add(
        0.4,
        _event(
            "activity",
            source="checker",
            label="checkout-tests",
            stream="stdout",
            text="Scenario: Free delivery at 5000 cents ... PASSED",
        ),
    )
    add(0.2, _event("check_finished", source="engine", name="checkout-tests", status="pass"))
    add(0.1, CardStatusChange("build.contract.md", "passed"))
    add(0.3, CardStatusChange("docs.contract.md", "running"))
    add(0.3, CardStatusChange("tests.contract.md", "running"))
    add(
        0.5,
        _event(
            "activity",
            source="harness",
            text="Writing documentation.md from the accepted changes.diff...",
        ),
    )
    add(0.5, _event("activity", source="harness", text="Writing additional regression tests.md..."))
    add(0.4, CardStatusChange("docs.contract.md", "passed"))
    add(0.4, CardStatusChange("tests.contract.md", "passed"))
    add(0.3, CardStatusChange("review.contract.md", "running"))
    add(
        0.5,
        _event(
            "activity",
            source="harness",
            text="Compiling review.md across every retained artifact...",
        ),
    )
    add(0.3, CardStatusChange("review.contract.md", "checking"))
    add(
        0.2,
        _event(
            "check_started",
            source="engine",
            name="review-format-references",
            command="python3 checks/documents.py review review.md",
        ),
    )
    add(
        0.3,
        _event("check_finished", source="engine", name="review-format-references", status="pass"),
    )
    add(0.1, CardStatusChange("review.contract.md", "passed"))
    add(0.2, _event("finished", source="engine", outcome="COMPLETE"))
    return tuple(steps)


def cancellation_script() -> tuple[Step, ...]:
    """Exercise stop handling: request, unconfirmed child, confirmed stop."""
    return (
        Step(0.0, _event("stop_requested", source="engine", reason="cancelled")),
        Step(
            0.4,
            _event(
                "activity",
                source="harness",
                text="Stopping processes; waiting for managed children.",
            ),
        ),
        Step(0.5, _event("stop_observed", source="engine", confirmed=True)),
        Step(0.1, CardStatusChange("build.contract.md", "blocked")),
    )
