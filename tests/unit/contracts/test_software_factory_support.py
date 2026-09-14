"""Shared authored fixtures for the example; none are live model outputs."""

import importlib.util
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

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
    driver: ModuleType
    evidence: ModuleType
    checks: ModuleType


def load(name: str, path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def factory(monkeypatch: pytest.MonkeyPatch) -> Modules:
    evidence = load("evidence", EXAMPLE / "evidence.py", monkeypatch)
    driver = load("software_factory_driver", EXAMPLE / "run.py", monkeypatch)
    checks = load("software_factory_checks", EXAMPLE / "checks/verify.py", monkeypatch)
    return Modules(driver, evidence, checks)


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
            "summary": "The fixed task passes; a real shipping product needs business review.",
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
        **{name: factory.evidence.encode(value) for name, value in documents.items()},
        "shipping.py": SOURCE.encode("ascii"),
    }


@dataclass
class RecordCase:
    caller: Path
    run: Path
    command: str
    identities: list[dict[str, Any]]
    record: dict[str, Any]

    def save(self) -> None:
        (self.run / "record.json").write_text(json.dumps(self.record), encoding="ascii")

    def admit(self, factory: Modules, exit_code: int = 21) -> Any:
        return factory.evidence.admit(
            self.caller,
            "plan.json",
            self.command,
            self.identities,
            exit_code,
            None,
        )


@pytest.fixture
def record_case(factory: Modules, tmp_path: Path) -> RecordCase:
    evidence = factory.evidence
    caller = tmp_path / "caller"
    caller.mkdir()
    _, contract, command = factory.driver.contract_bytes(factory.driver.STAGES[0], sys.executable)
    files = {
        "request.json": (EXAMPLE / "request.json").read_bytes(),
        "job.contract.md": contract,
        "checks/verify.py": (EXAMPLE / "checks/verify.py").read_bytes(),
    }
    for name, raw in files.items():
        evidence.write_new(caller, name, raw)
    identities = evidence.identify(caller, files)
    run = caller / ".apm/runs/20260914T100000Z-012345abcdef"
    run.mkdir(parents=True)
    for name, raw in files.items():
        evidence.write_new(run, "baseline/" + name, raw)
    raw = outputs(factory)["plan.json"]
    evidence.write_new(run, "artifacts/plan.json", raw)
    evidence.write_new(run, "source/contract.contract.md", contract)
    evidence.write_new(run, "transcript.log", b"fixture observations\n")
    resources = [item for item in identities if item["relative_path"].startswith("checks/")]
    process = {"returncode": 0, "error": None, "stop_reason": None, "cleanup_confirmed": True}
    artifact = {
        "relative_path": "plan.json",
        "path": str(run / "artifacts/plan.json"),
        "sha256": evidence.sha(raw),
        "size": len(raw),
    }
    check = {
        "name": "contract",
        "command": command,
        "normalized": 0,
        "process": process.copy(),
        "subject_digest": artifact["sha256"],
        "resources_digest": evidence.digest_entries(resources),
    }
    record = {
        "schema": "apm-contract-run/0.1",
        "profile": "native-advisory",
        "complete": True,
        "phase": "finished",
        "run_id": run.name,
        "attempt_id": run.name + "/1",
        "caller_root": str(caller),
        "evidence_root": str(run.parent),
        "source": {
            "path": str(caller / "job.contract.md"),
            "sha256": evidence.sha(contract),
            "retained": {"contract.contract.md": str(run / "source/contract.contract.md")},
            "retained_identities": [
                {
                    "relative_path": "contract.contract.md",
                    "sha256": evidence.sha(contract),
                    "size": len(contract),
                    "mode": 0o400,
                }
            ],
        },
        "baseline": {
            "root": str(run / "baseline"),
            "producer": str(run / "producer"),
            "files": identities,
            "digest": evidence.digest_entries(identities),
            "resources_digest": evidence.digest_entries(resources),
        },
        "controls": {"isolation": "unavailable", "spend_cap": "unavailable"},
        "producer": process.copy(),
        "native_reported_exit_code": 0,
        "requested_model": None,
        "child_pid": None,
        "child_pgid": None,
        "active_check": None,
        "artifact": artifact,
        "checks": [check],
        "transcript": {
            "relative_path": "transcript.log",
            "sha256": evidence.sha(b"fixture observations\n"),
            "size": len(b"fixture observations\n"),
        },
        "result": {
            "run_id": run.name,
            "run_directory": str(run),
            "outcome": {"name": "UNPROVEN", "exit_code": 21},
            "artifact": artifact,
            "checks": [check],
            "stop_reason": None,
        },
    }
    case = RecordCase(caller, run, command, identities, record)
    case.save()
    return case


def fixture_copilot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Supply executable discovery only; the source-CLI test never launches it."""
    tools = tmp_path / "tools"
    tools.mkdir()
    executable = tools / ("copilot.exe" if os.name == "nt" else "copilot")
    executable.write_bytes(b"Native producer substituted by the test adapter.\n")
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
