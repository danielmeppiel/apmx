"""Fail-closed required-check gate, run as its own job with ``if: always()``
in both ``receiver-candidate-check.yml`` and ``receiver-attest.yml``.

This job -- never ``assess``/``sign`` directly -- is the one whose check run
must be configured as the actual required status check on the protected
branch. The reason is a GitHub platform fact, not a design preference:
per GitHub's branch-protection docs, a required status check is satisfied
by a **success, skipped, OR neutral** conclusion, not success alone. A
downstream job whose own ``needs:`` points at an upstream job that failed
is reported with conclusion ``skipped`` -- which GitHub branch protection
treats as SATISFYING the required check, not blocking it. Relying on
"assess/sign will just be skipped if prepare/execute fails, and that will
be reported as an unsatisfied required check" is therefore actively wrong
and would let a PR merge past a failed/skipped assessment.

The fix: a separate job that ALWAYS runs (``if: always()``, so it is never
itself skipped regardless of any upstream job's outcome) and explicitly
inspects each upstream job's own reported `needs.<job>.result` string,
exiting nonzero -- an actual job FAILURE, which genuinely blocks a required
check -- unless every one of them is exactly ``"success"``. It additionally
requires a "complete validated receipt": a JSON artifact the assess/sign
job itself produced recording exactly which cases it accepted, so a
required-check pass can never be achieved merely by the upstream jobs
reporting success while having silently skipped or dropped some of the
cases they were supposed to assess.

This module intentionally contains ZERO GitHub Actions YAML conditional
logic -- the actual gating decision lives here, in pure, directly
pytest-testable Python, specifically because YAML `if:`/`needs.*.result`
expressions cannot themselves be unit tested. The workflow's own gate step
does nothing but pass through `needs.*.result` strings and a receipt file
path to this module's CLI and surface its exit code.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

# The exact, and ONLY, upstream conclusion that may ever satisfy the gate.
# Anything else -- "failure", "cancelled", "skipped", "neutral", a typo, a
# future GitHub-added conclusion string this module doesn't yet know about
# -- is fail-closed: rejected, not ignored.
_REQUIRED_RESULT = "success"


@dataclass(frozen=True)
class GateOutcome:
    ok: bool
    reason: str


def evaluate_gate(
    job_results: dict[str, str],
    receipt: dict | None,
    expected_case_names: list[str] | None = None,
) -> GateOutcome:
    """Pure decision function -- no file IO, no subprocess, no GitHub
    Actions context -- so every combination of outcomes is directly
    testable.

    ``job_results`` maps each upstream job name (e.g. "prepare", "execute",
    "assess" or "sign") to its own GitHub Actions ``needs.<job>.result``
    string. ALL of them must be exactly "success".

    ``receipt`` is the parsed JSON artifact the assess/sign job itself
    produced (``None`` if the artifact was missing/unreadable, which is
    itself fail-closed, never treated as "no cases to check"). It must be a
    dict with ``"accepted": True`` at the top level.

    ``expected_case_names``, when given (the multi-case candidate-check
    workflow; omitted for the single-canonical-package attest workflow),
    is the complete list of case names job A actually prepared. The
    receipt's own ``"cases"`` list (each entry a dict with ``"name"`` and
    ``"accepted"``) must name EXACTLY that same set -- no fewer (a
    silently dropped case), no more (a forged/duplicated entry) -- and
    every one of them must itself be ``"accepted": True``.
    """
    for job_name, result in sorted(job_results.items()):
        if result != _REQUIRED_RESULT:
            return GateOutcome(
                ok=False,
                reason=f"upstream job {job_name!r} did not succeed (result={result!r})",
            )

    if receipt is None:
        return GateOutcome(ok=False, reason="no readable receipt artifact was found")
    if not isinstance(receipt, dict):
        return GateOutcome(ok=False, reason="receipt artifact was not a JSON object")
    if receipt.get("accepted") is not True:
        return GateOutcome(
            ok=False,
            reason=f"receipt did not record acceptance (accepted={receipt.get('accepted')!r})",
        )

    if expected_case_names is not None:
        cases = receipt.get("cases")
        if not isinstance(cases, list):
            return GateOutcome(ok=False, reason="receipt was missing its per-case 'cases' list")
        receipt_names = set()
        for entry in cases:
            if not isinstance(entry, dict) or "name" not in entry:
                return GateOutcome(ok=False, reason="receipt contained a malformed case entry")
            receipt_names.add(entry["name"])
            if entry.get("accepted") is not True:
                return GateOutcome(
                    ok=False,
                    reason=f"receipt case {entry['name']!r} was not recorded as accepted",
                )
        expected_names = set(expected_case_names)
        if receipt_names != expected_names:
            missing = expected_names - receipt_names
            extra = receipt_names - expected_names
            return GateOutcome(
                ok=False,
                reason=(
                    "receipt's case set did not exactly match the cases job A prepared "
                    f"(missing={sorted(missing)}, unexpected={sorted(extra)})"
                ),
            )

    return GateOutcome(ok=True, reason="all upstream jobs succeeded and the receipt is complete")


def _load_receipt(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _load_expected_case_names(path: Path | None) -> list[str] | None:
    if path is None:
        return None
    try:
        text = path.read_text()
    except OSError:
        return []
    names = []
    for line in text.splitlines():
        if not line.strip():
            continue
        # Matches prepare's own tab-separated "name\tcase_dir" format
        # (bundles/cases.txt) -- see receiver-candidate-check.yml.
        names.append(line.split("\t", 1)[0])
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--job-result",
        action="append",
        default=[],
        metavar="NAME=RESULT",
        required=True,
        help="One upstream job's needs.<job>.result, as NAME=RESULT. Repeatable.",
    )
    parser.add_argument("--receipt", type=Path, default=None)
    parser.add_argument(
        "--expected-cases",
        type=Path,
        default=None,
        help="Path to the tab-separated case list job A produced (bundles/cases.txt); "
        "omit for the single-canonical-package attest workflow.",
    )
    args = parser.parse_args(argv)

    job_results = {}
    for item in args.job_result:
        if "=" not in item:
            parser.error(f"--job-result must be NAME=RESULT, got {item!r}")
        name, _, result = item.partition("=")
        job_results[name] = result

    receipt = _load_receipt(args.receipt)
    expected_case_names = _load_expected_case_names(args.expected_cases)

    outcome = evaluate_gate(job_results, receipt, expected_case_names)
    if outcome.ok:
        print(f"gate: PASS ({outcome.reason})")
        return 0
    print(f"::error::gate: FAIL ({outcome.reason})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
