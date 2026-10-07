"""Generate the enterprise-receiver positive FIXTURE Evidence Package.

This is a FIXTURE generator, not a live demonstration transcript. It drives the
*real* apmx contract engine and the *real* Evidence Package export
(``apmx.contracts.evidence.export_package``) end to end, over a deliberately
tiny public application, so the resulting package is a genuine, reproducible
product of APMX's own code paths rather than a hand-authored JSON sample.

The only thing stood in for is the model backend: apmx normally drives a
live Copilot/OpenCode runtime, which needs network/model credentials that are
not available (and should not be committed) in this public repository. In its
place we substitute a small, fully deterministic child process -- the exact
technique apmx's own unit tests use (see ``tests/unit/contracts/test_engine.py
::_fake_adapter``) -- that writes the fixed greeting and reports a normal
completion. Everything downstream (baseline capture, checker execution,
in-toto/SLSA statements, CycloneDX export, package assembly) is real apmx code
running for real against real files on disk.

No private evidence, transcript, or credential is read or published by this
script. Output is written only under the path given on the command line.
"""

import argparse
import hashlib
import json
import shlex
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "src"))

import pytest  # noqa: E402

from apmx.contracts import engine, evidence  # noqa: E402
from apmx.contracts.models import (  # noqa: E402
    CheckSpec,
    ContractLimits,
    LeafContract,
    LeafPlan,
)
from apmx.core.contract_logger import ContractLogger  # noqa: E402

_FIXED_BOM = (
    b'{"bomFormat":"CycloneDX","specVersion":"1.5","version":1,"components":[]}\n'
)

_APP_DIR = Path(__file__).resolve().parent.parent / "app"

_PATCHED_GREETING = '''"""Toy greeting used by the enterprise-receiver demo. Fixture app, not production code."""


def greet(name: str) -> str:
    return f"Hello, {name}!"
'''

_CANDIDATE_RELATIVE_PATH = "candidate/greeting.py"

def _contract_body(variant: str) -> str:
    instruction = (
        "Write a full replacement for greeting.py at "
        + _CANDIDATE_RELATIVE_PATH
        + ' using a full salutation: "Hello, {name}!" while keeping the module'
        " docstring and signature."
    )
    if variant != "approved":
        # A distinct, self-consistent contract body from an otherwise-unapproved
        # factory identity. The produced evidence is internally valid and its
        # check genuinely passes; only the factory/definition identity differs,
        # which is what the receiver's factory allowlist must catch on its own.
        instruction += f"\n\nFactory variant label: {variant} (not on the receiver allowlist)."
    return (
        "---\nneeds: greeting.py\nproduces: "
        + _CANDIDATE_RELATIVE_PATH
        + "\nverify:\n  acceptance: "
        + json.dumps(f"{shlex.quote(sys.executable)} checks/run_check.py {_CANDIDATE_RELATIVE_PATH}")
        + "\n---\n"
        + instruction
        + "\n"
    )


def _build_plan(project_root: Path, variant: str) -> LeafPlan:
    contract_body = _contract_body(variant)
    (project_root / "apm.yml").write_text(
        "name: enterprise-receiver-fixture\nversion: 0.0.0\n", encoding="utf-8"
    )
    (project_root / "greeting.py").write_bytes((_APP_DIR / "greeting.py").read_bytes())
    checks_dir = project_root / "checks"
    checks_dir.mkdir()
    (checks_dir / "test_greeting.py").write_bytes(
        (_APP_DIR / "checks" / "test_greeting.py").read_bytes()
    )
    (checks_dir / "run_check.py").write_text(
        "import importlib.util\n"
        "import sys\n"
        "from pathlib import Path\n\n"
        "candidate = Path(sys.argv[1])\n"
        "spec = importlib.util.spec_from_file_location('greeting', candidate)\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "assert module.greet('World') == 'Hello, World!', module.greet('World')\n"
        "print('acceptance: greet() uses a full salutation')\n",
        encoding="utf-8",
    )
    source = project_root / "job.contract.md"
    source.write_text(contract_body, encoding="utf-8")
    return LeafPlan(
        contract=LeafContract(
            path=source,
            source_digest=hashlib.sha256(source.read_bytes()).hexdigest(),
            body=contract_body,
            needs=("greeting.py",),
            produces=_CANDIDATE_RELATIVE_PATH,
            checks=(
                CheckSpec(
                    "acceptance",
                    f"{shlex.quote(sys.executable)} checks/run_check.py {_CANDIDATE_RELATIVE_PATH}",
                ),
            ),
        ),
        project_root=project_root,
        executable=Path(sys.executable),
        model="gpt-6-astra",
        limits=ContractLimits(),
    )


def _install_fake_adapter(monkeypatch: pytest.MonkeyPatch, plan: LeafPlan) -> None:
    """Deterministic stand-in for a live model backend; see module docstring."""
    assistant_event = json.dumps(
        {
            "type": "assistant.message",
            "data": {
                "messageId": "fixture-message",
                "content": "Patched the greeting to use a full salutation.",
                "model": "gpt-6-astra",
            },
        }
    )
    completed_event = json.dumps(
        {"type": "result", "exitCode": 0, "sessionId": "fixture-session", "usage": {}}
    )
    code = (
        "from pathlib import Path\n"
        f"out = Path({_CANDIDATE_RELATIVE_PATH!r})\n"
        "out.parent.mkdir(parents=True, exist_ok=True)\n"
        f"out.write_text({_PATCHED_GREETING!r})\n"
        f"print({assistant_event!r}, flush=True)\n"
        f"print({completed_event!r}, flush=True)\n"
        "raise SystemExit(0)\n"
    )

    def build_request(selected, snapshot, directory, *, timeout_seconds):
        from apmx.contracts.models import ProcessRequest

        return ProcessRequest(
            argv=(sys.executable, "-c", code), cwd=snapshot.producer, timeout_seconds=timeout_seconds
        )

    adapter = Mock()
    adapter.build_contract_request.side_effect = build_request
    monkeypatch.setattr(engine.frontend, "plan_contract", lambda *args, **kwargs: plan)
    monkeypatch.setattr(engine.RuntimeFactory, "get_runtime_by_name", lambda *args, **kwargs: adapter)


def make_fixture(destination: Path, variant: str = "approved") -> dict:
    with TemporaryDirectory(prefix="apmx-enterprise-receiver-fixture-") as temporary:
        project_root = Path(temporary) / "project"
        project_root.mkdir()
        plan = _build_plan(project_root, variant)
        with pytest.MonkeyPatch.context() as monkeypatch:
            _install_fake_adapter(monkeypatch, plan)
            monkeypatch.setattr(evidence, "_inventory", lambda *_: (_FIXED_BOM, [], []))
            logger = ContractLogger()
            result = engine.run_contract(plan, logger=logger, allow_advisory=True)
            if result.outcome.name != "COMPLETE":
                raise SystemExit(f"Fixture contract did not complete: {result.outcome.name}")
            package_path = evidence.export_package(
                result.run_directory / "record.json", destination
            )
    manifest = json.loads((package_path / "index.json").read_bytes())
    return {
        "package": str(package_path),
        "files": sorted(manifest["files"]),
        "definitionSha256": manifest["definition"]["digest"]["sha256"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="New directory for the Evidence Package")
    parser.add_argument(
        "--variant",
        default="approved",
        help="Factory-identity label. 'approved' matches the receiver allowlist; "
        "any other value produces self-consistent evidence from a different, "
        "unapproved factory definition (used for the factory-policy negative case).",
    )
    args = parser.parse_args()
    summary = make_fixture(args.destination.resolve(), args.variant)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
