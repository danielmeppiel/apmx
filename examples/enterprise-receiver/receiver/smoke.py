"""Branch-scoped smoke harness: exercises the REAL candidate-execution path
(``write_execution_bundle`` / ``run_execution_bundle`` / ``verify_execution_results``
in ``check.py``) and the REAL ``gate.py`` decision logic, across three ACTUAL
GitHub Actions jobs with real artifact upload/download between them -- never
a single in-process pytest mock of the whole pipeline. See
``.github/workflows/receiver-smoke.yml``, which triggers only on an
explicit-ref push to this implementation branch (never ``workflow_dispatch``,
never the protected demo branch, never ``pull_request``/``pull_request_target``)
and grants only ``contents: read`` -- no ``id-token``, no ``attestations``, no
secrets.

This is validation infrastructure for THIS implementation branch, not a
production receiver policy: it exercises real Docker, the real pinned
execution image, and three real scenarios against the repo's own public
``examples/enterprise-receiver`` fixture app (synthetic, already public,
already used by the real positive/negative demo PRs):

  - ``positive``:   the correct candidate; job C must ACCEPT it.
  - ``tampered``:   a candidate whose observable behavior is simply wrong;
                    job C must REJECT it (ordinary behavioral mismatch).
  - ``early-exit``: an adversarial candidate that calls ``os._exit()``
                    instead of returning, simulating "escape before the
                    check can observe me"; job C must REJECT it too (no
                    observation recorded, not a false pass).

It also exercises receipt tampering directly against ``gate.py``: builds a
forged receipt that claims every scenario was accepted when job C's own
real verdicts say otherwise, and asserts ``evaluate_gate`` rejects it. This
is the same decision function the actual required-check gate jobs use; see
``gate.py``'s module docstring for why GitHub's own skipped/neutral
semantics make this receipt-based check necessary.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_RECEIVER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_RECEIVER_DIR))

import check as receiver_check  # noqa: E402
import gate  # noqa: E402

_APP_RELATIVE = "examples/enterprise-receiver/app/greeting.py"

_POSITIVE_GREETING = b'''"""Toy greeting used by the enterprise-receiver smoke harness."""


def greet(name: str) -> str:
    return f"Hello, {name}!"
'''

_TAMPERED_GREETING = b'''"""Toy greeting used by the enterprise-receiver smoke harness (tampered)."""


def greet(name: str) -> str:
    return f"Yo, {name}!"
'''

# Calls os._exit() instead of returning: simulates a candidate that tries
# to escape before container_driver.py/executor.py can ever observe a
# RESULT line, rather than merely returning a wrong value.
_EARLY_EXIT_GREETING = b'''"""Toy greeting used by the enterprise-receiver smoke harness (early-exit)."""

import os


def greet(name: str) -> str:
    os._exit(1)
'''

_SCENARIOS = {
    "positive": _POSITIVE_GREETING,
    "tampered": _TAMPERED_GREETING,
    "early-exit": _EARLY_EXIT_GREETING,
}
# Scenarios job C's own real verdict must ACCEPT; everything else in
# _SCENARIOS must be REJECTED. Kept explicit (not inferred) so a future
# scenario addition can't silently change expected outcomes.
_EXPECTED_ACCEPTED = {"positive"}


def _base_sha() -> str:
    return receiver_check._run_git(["rev-parse", "HEAD"]).stdout.decode().strip()


def cmd_prepare(args: argparse.Namespace) -> int:
    base_sha = _base_sha()
    bundle_root = args.bundle_root
    bundle_root.mkdir(parents=True, exist_ok=True)
    (bundle_root / "base_sha.txt").write_text(base_sha)
    names = []
    for name, candidate_bytes in _SCENARIOS.items():
        receiver_check.write_execution_bundle(
            base_sha, {_APP_RELATIVE: candidate_bytes}, bundle_root / name
        )
        names.append(name)
    (bundle_root / "scenarios.txt").write_text("\n".join(names) + "\n")
    print(f"prepare: wrote {len(names)} bundle(s) under {bundle_root}")
    return 0


def _scenario_names(bundle_root: Path) -> list[str]:
    text = (bundle_root / "scenarios.txt").read_text()
    return [line.strip() for line in text.splitlines() if line.strip()]


def cmd_execute(args: argparse.Namespace) -> int:
    bundle_root = args.bundle_root
    results_root = args.results_root
    results_root.mkdir(parents=True, exist_ok=True)
    for name in _scenario_names(bundle_root):
        print(f"::group::execute scenario: {name}")
        results = receiver_check.run_execution_bundle(bundle_root / name)
        (results_root / f"{name}.json").write_text(json.dumps(results))
        print(f"::endgroup::")
    print(f"execute: ran {len(_scenario_names(bundle_root))} scenario(s)")
    return 0


def cmd_assess(args: argparse.Namespace) -> int:
    bundle_root = args.bundle_root
    results_root = args.results_root
    base_sha = (bundle_root / "base_sha.txt").read_text().strip()

    scenario_receipts = []
    mismatches = []
    for name in _scenario_names(bundle_root):
        manifest = json.loads((bundle_root / name / "manifest.json").read_text())
        results = json.loads((results_root / f"{name}.json").read_text())
        expected_accept = name in _EXPECTED_ACCEPTED
        try:
            receiver_check.verify_execution_results(base_sha, manifest, results)
            accepted = True
        except receiver_check.ReceiverFailure as exc:
            accepted = False
            print(f"scenario {name!r}: rejected ({exc.policy}: {exc})")
        else:
            print(f"scenario {name!r}: accepted")
        if accepted != expected_accept:
            mismatches.append(
                f"scenario {name!r}: expected accepted={expected_accept}, got {accepted}"
            )
        scenario_receipts.append({"name": name, "accepted": accepted})

    if mismatches:
        for mismatch in mismatches:
            print(f"::error::{mismatch}", file=sys.stderr)

    # Receipt-tampering check: forge a receipt claiming every scenario was
    # accepted (including the ones job C just actually rejected) and assert
    # gate.py's own decision function refuses it -- the gate must trust its
    # OWN comparison against the real per-scenario receipt, never a bare
    # upstream "accepted: true" claim alone.
    forged_receipt = {
        "accepted": True,
        "cases": [{"name": r["name"], "accepted": True} for r in scenario_receipts],
    }
    forged_outcome = gate.evaluate_gate(
        {"prepare": "success", "execute": "success", "assess": "success"},
        forged_receipt,
        expected_case_names=[name for name in _EXPECTED_ACCEPTED],
    )
    if forged_outcome.ok:
        mismatches.append(
            "receipt-tampering check FAILED: gate.evaluate_gate accepted a forged "
            "receipt whose case set does not match the expected (accepted-only) set"
        )
    else:
        print(f"receipt-tampering check: gate correctly rejected forged receipt "
              f"({forged_outcome.reason})")

    # gate.py's receipt contract mirrors production: every NAMED case must
    # itself be accepted, and the name set must exactly match
    # --expected-cases. The intentionally-rejected scenarios (tampered,
    # early-exit) are proof that verify_execution_results correctly
    # rejects them -- they are not "required checks" gate.py should ever
    # see as a passing case, so only the genuinely-accepted scenario(s)
    # go into the receipt gate.py itself evaluates.
    real_receipt_cases = (
        [{"name": name, "accepted": True} for name in sorted(_EXPECTED_ACCEPTED)]
        if not mismatches
        else []
    )
    real_receipt = {"accepted": not mismatches, "cases": real_receipt_cases}
    (args.receipt).write_text(json.dumps(real_receipt, indent=2))

    if mismatches:
        print(f"::error::assess: {len(mismatches)} scenario(s) did not match expectations",
              file=sys.stderr)
        return 1
    print("assess: all scenarios matched expected accept/reject outcomes; "
          "receipt-tampering check passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--bundle-root", type=Path, required=True)
    prepare_parser.set_defaults(func=cmd_prepare)

    execute_parser = subparsers.add_parser("execute")
    execute_parser.add_argument("--bundle-root", type=Path, required=True)
    execute_parser.add_argument("--results-root", type=Path, required=True)
    execute_parser.set_defaults(func=cmd_execute)

    assess_parser = subparsers.add_parser("assess")
    assess_parser.add_argument("--bundle-root", type=Path, required=True)
    assess_parser.add_argument("--results-root", type=Path, required=True)
    assess_parser.add_argument("--receipt", type=Path, required=True)
    assess_parser.set_defaults(func=cmd_assess)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
