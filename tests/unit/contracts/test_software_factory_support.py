"""Authored task fixtures; no duplicate host scheduler or record authority."""

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

EXAMPLE = Path(__file__).resolve().parents[3] / "examples/contracts/software-factory"
SOURCE = """def shipping_cost(weight_grams):
    if type(weight_grams) is not int:
        raise TypeError("integer required")
    if weight_grams < 1 or weight_grams > 5000:
        raise ValueError("outside supported range")
    if weight_grams <= 1000:
        return 500
    if weight_grams <= 2000:
        return 700
    if weight_grams <= 3000:
        return 900
    if weight_grams <= 4000:
        return 1100
    return 1300
"""
pytestmark = pytest.mark.component


@dataclass
class Modules:
    checks: ModuleType


@pytest.fixture
def factory(monkeypatch: pytest.MonkeyPatch) -> Modules:
    spec = importlib.util.spec_from_file_location("factory_checks", EXAMPLE / "checks/verify.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return Modules(module)


def outputs(factory: Modules) -> dict[str, bytes]:
    checks = factory.checks
    requirements = sorted(checks.REQUIREMENTS)
    cases = [
        {
            "id": f"case-{index}",
            "input": value,
            "expected": checks.oracle(value),
            "requirements": requirements,
            "reason": "Boundary or invalid-type partition.",
        }
        for index, value in enumerate(checks.BOUNDARIES)
    ]
    documents = {
        "plan.json": {
            "goal": "Implement the fixed shipping quote interface.",
            "steps": [
                {
                    "id": phase,
                    "phase": phase,
                    "action": "Implement this explicit phase.",
                    "requirements": requirements,
                }
                for phase in checks.PHASES[1:]
            ],
            "risks": ["Check inclusive boundaries and booleans."],
            "validation": "Use fixed acceptance and independent mutation checks.",
        },
        "spec.json": {
            "function": "shipping_cost",
            "parameter": "weight_grams",
            "returns": "integer cents",
            "accepted_type": "int excluding bool",
            "range": [1, 5000],
            "tiers": [
                {"through": upper, "cents": checks.oracle(upper)["returns"]}
                for upper in range(1000, 5001, 1000)
            ],
            "errors": {"wrong_type": "TypeError", "out_of_range": "ValueError"},
            "requirements": requirements,
            "cases": cases,
        },
        "tests.json": {
            "strategy": "Partition every tier boundary and invalid type.",
            "cases": cases,
        },
        "review.json": {
            "advisory": True,
            "assurance": "UNPROVEN",
            "recommendation": "follow_up",
            "summary": "The implementation matches the fixed task; real pricing needs business review.",
            "findings": [
                {
                    "file": "shipping.py",
                    "line": 1,
                    "severity": "low",
                    "requirements": ["rates"],
                    "detail": "Confirm the example rates before real use.",
                }
            ],
            "limitations": ["Host execution is not sandboxed."],
            "follow_up": ["Have the business owner review pricing before actual use."],
        },
    }
    return {
        **{
            name: (json.dumps(value, indent=2) + "\n").encode("ascii")
            for name, value in documents.items()
        },
        "shipping.py": SOURCE.encode("ascii"),
    }
