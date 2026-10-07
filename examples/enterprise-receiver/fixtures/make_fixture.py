"""Generate the enterprise-receiver positive FIXTURE Evidence Package.

This is a FIXTURE generator, not a live demonstration transcript. It drives the
*real* apmx contract engine and the *real* Evidence Package export
(``apmx.contracts.evidence.export_package``) end to end, over a deliberately
tiny public application, so the resulting package is a genuine, reproducible
product of APMX's own code paths rather than a hand-authored JSON sample.

Exactly ONE thing is deliberately stood in, and it is disclosed here (an
earlier revision of this docstring also named the capability inventory as
mocked; that is no longer true -- see below):

1. The model backend. apmx normally drives a live Copilot/OpenCode runtime,
   which needs network/model credentials that are not available (and should
   not be committed) in this public repository. In its place we substitute a
   small, fully deterministic child process -- the exact technique apmx's own
   unit tests use (see ``tests/unit/contracts/test_engine.py::_fake_adapter``)
   -- that writes the fixed greeting and reports a normal completion. This is
   the "hermetic producer": a test double for the one component that cannot
   run here without live credentials. Nothing else about the capability
   inventory, binding, or check execution is substituted.

2. The capability inventory (CycloneDX BOM) is now REAL, not mocked. The
   fixture project genuinely declares a dependency (``dependencies.apm`` in
   its generated ``apm.yml``) on ``full-salutation-style``, a real public
   skill committed in this repository at
   ``examples/enterprise-receiver/app/skills/full-salutation-style``. That
   dependency is installed with the real, pinned ``apm`` backend binary
   (``apmx.install.apm_backend.install``, the exact production code path
   production contracts use -- no network access, since it is a local-path
   dependency resolved on disk), producing a genuine ``apm.lock.yaml`` and an
   installed copy under ``apm_modules/``. The contract's ``imports:``
   frontmatter names that real dependency, so ``apmx.contracts.imports
   .resolve_installed_skills`` -- again, real production code, not a stub --
   binds it to a genuine ``ImportedSkill`` identity (its real lock identity
   and real SKILL.md content hash). ``apmx.contracts.evidence._inventory`` and
   ``apm_backend.export_cyclonedx`` then run completely unmocked, producing a
   real, nonempty CycloneDX document and a real ``capability-bindings.json``
   entry with this capability's actual ``purl`` and content ``bodySha256``.
   This runs the real engine against the real official APM inventory path,
   but it is still produced by this fixture's HERMETIC PRODUCER -- a local,
   offline project built for this generator, not a live Copilot/OpenCode
   harness execution against an arbitrary real-world repository. The
   recorded ``lockIdentity`` for a local-path dependency is the dependency's
   own DECLARED source path string (apmx's local-dependency unique key), not
   a stable public identity -- the same real skill content installed via a
   different declared path (e.g. a different checkout location) gets a
   different ``lockIdentity`` even though it is byte-identical; the
   receiver's capability-revocation policy therefore matches on the portable
   ``(purl, bodySha256)`` pair, not on ``lockIdentity`` alone. A public
   claim that this fixture's capability-revocation check runs against a
   real APM capability inventory is accurate. What remains out of scope
   for this fixture alone is a genuine, currently-deployed *production*
   capability incident (see ``receiver/test_check.py``'s
   ``test_capability_revocation_*`` tests for the accept/reject proof
   using this exact real purl/bodySha256 pair).

Everything downstream of the model-backend substitution (baseline capture,
checker execution, in-toto/SLSA statements, CycloneDX export, package
assembly) is real apmx code running for real against real files on disk.

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

import pytest

from apmx.contracts import engine, evidence
from apmx.contracts.imports import read_project_manifest, resolve_installed_skills
from apmx.contracts.models import (
    CheckSpec,
    ContractLimits,
    LeafContract,
    LeafPlan,
)
from apmx.core.contract_logger import ContractLogger
from apmx.install import apm_backend

_APP_DIR = Path(__file__).resolve().parent.parent / "app"

# The real, public capability this fixture's project genuinely depends on and
# imports (see point 2 of the module docstring). Resolved as a local-path APM
# dependency: no network access, but a real install/lock/content-hash cycle
# through production apm_backend/resolve_installed_skills code.
_CAPABILITY_SKILL_NAME = "full-salutation-style"
_CAPABILITY_SKILL_DIR = _APP_DIR / "skills" / _CAPABILITY_SKILL_NAME

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
        + f"\nimports: [{_CAPABILITY_SKILL_NAME}]"
        + "\nverify:\n  acceptance: "
        + json.dumps(
            f"{shlex.quote(sys.executable)} checks/run_check.py {_CANDIDATE_RELATIVE_PATH}"
        )
        + "\n---\n"
        + instruction
        + "\n"
    )


def _build_plan(project_root: Path, variant: str, skill_source_dir: Path | None = None) -> LeafPlan:
    contract_body = _contract_body(variant)
    # lockIdentity is apmx's local-path dependency unique key, which is the
    # DECLARED path string itself (see
    # apmx.models.dependency.reference.DependencyReference.get_unique_key):
    # it is not derived from the project_root or install destination. Two
    # calls that both declare the same fixed `_CAPABILITY_SKILL_DIR` path
    # therefore always produce the same lockIdentity, even though each call
    # gets a fresh temporary project_root. To genuinely exercise "same real
    # skill content installed from a different checkout/path still matches
    # the receiver's capability-revocation pair", callers must pass a
    # distinct `skill_source_dir` (a copy of the real skill's content at a
    # different filesystem location) so the declared path -- and hence
    # lockIdentity -- actually differs, while the skill's own name/content
    # (and therefore its real purl/bodySha256) stay identical.
    dependency_path = skill_source_dir if skill_source_dir is not None else _CAPABILITY_SKILL_DIR
    (project_root / "apm.yml").write_text(
        "name: enterprise-receiver-fixture\nversion: 0.0.0\n"
        "dependencies:\n  apm:\n    - path: " + str(dependency_path) + "\n",
        encoding="utf-8",
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

    limits = ContractLimits()
    # Real install: clones nothing (local-path dependency), but runs the
    # actual pinned apm backend binary end to end -- real lock, real
    # apm_modules/ copy, real content hashes. See module docstring point 2.
    backend_identity = apm_backend.install(project_root, limits=limits)
    manifest_digest = hashlib.sha256((project_root / "apm.yml").read_bytes()).hexdigest()
    lock_digest = hashlib.sha256((project_root / "apm.lock.yaml").read_bytes()).hexdigest()

    contract = LeafContract(
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
        imports=(_CAPABILITY_SKILL_NAME,),
    )
    # Real resolution: binds the contract's declared import to the real
    # installed skill via its real lock entry -- production code, no stub.
    package, _, _ = read_project_manifest(project_root, limits)
    imported_skills, _ = resolve_installed_skills(contract, project_root, package)
    if not imported_skills:
        raise SystemExit(
            f"Fixture capability import {_CAPABILITY_SKILL_NAME!r} did not resolve to an "
            "installed skill; the real apm install/lock step must have failed silently."
        )

    return LeafPlan(
        contract=contract,
        project_root=project_root,
        executable=Path(sys.executable),
        model="gpt-6-astra",
        limits=limits,
        imported_skills=imported_skills,
        manifest_digest=manifest_digest,
        lock_digest=lock_digest,
        apm_backend=backend_identity,
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
            argv=(sys.executable, "-c", code),
            cwd=snapshot.producer,
            timeout_seconds=timeout_seconds,
        )

    adapter = Mock()
    adapter.build_contract_request.side_effect = build_request
    monkeypatch.setattr(engine.frontend, "plan_contract", lambda *args, **kwargs: plan)
    monkeypatch.setattr(
        engine.RuntimeFactory, "get_runtime_by_name", lambda *args, **kwargs: adapter
    )


def make_fixture(
    destination: Path, variant: str = "approved", skill_source_dir: Path | None = None
) -> dict:
    with TemporaryDirectory(prefix="apmx-enterprise-receiver-fixture-") as temporary:
        project_root = Path(temporary) / "project"
        project_root.mkdir()
        plan = _build_plan(project_root, variant, skill_source_dir=skill_source_dir)
        with pytest.MonkeyPatch.context() as monkeypatch:
            _install_fake_adapter(monkeypatch, plan)
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
