"""Unit tests for gate.py -- the fail-closed required-check aggregator.

These tests exist specifically because the gate's whole design rationale is
that GitHub's own `if:`/`needs.*.result` YAML cannot be unit tested, so the
decision logic was deliberately extracted into pure, directly testable
Python (see gate.py's module docstring). Every combination exercised here
corresponds to a real GitHub Actions scenario the gate job must handle
correctly: a genuinely passing run, each individual upstream job
failing/skipped/cancelled, a missing or malformed receipt, and a receipt
whose case set doesn't exactly match what job A actually prepared.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import gate


ALL_SUCCESS = {"prepare": "success", "execute": "success", "assess": "success"}
THREE_CASES = ["case-a", "case-b", "case-c"]


def _receipt(*, accepted: bool, cases: list[dict] | None = None) -> dict:
    receipt: dict = {"accepted": accepted}
    if cases is not None:
        receipt["cases"] = cases
    return receipt


def _full_accepted_cases(names: list[str]) -> list[dict]:
    return [{"name": name, "accepted": True} for name in names]


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_gate_passes_when_all_jobs_succeed_and_receipt_is_complete():
    outcome = gate.evaluate_gate(
        ALL_SUCCESS,
        _receipt(accepted=True, cases=_full_accepted_cases(THREE_CASES)),
        expected_case_names=THREE_CASES,
    )
    assert outcome.ok is True


def test_gate_passes_for_single_canonical_package_with_no_expected_cases():
    # The attest workflow's sign job has no multi-case list to compare.
    outcome = gate.evaluate_gate(
        {"prepare": "success", "execute": "success", "sign": "success"},
        _receipt(accepted=True),
        expected_case_names=None,
    )
    assert outcome.ok is True


# ---------------------------------------------------------------------------
# Upstream job result failures -- the core "skipped satisfies required
# check" bug this gate exists to close.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_result", ["failure", "skipped", "cancelled", "neutral", ""])
@pytest.mark.parametrize("which_job", ["prepare", "execute", "assess"])
def test_gate_fails_closed_when_any_upstream_job_is_not_success(which_job, bad_result):
    job_results = dict(ALL_SUCCESS)
    job_results[which_job] = bad_result
    outcome = gate.evaluate_gate(
        job_results,
        _receipt(accepted=True, cases=_full_accepted_cases(THREE_CASES)),
        expected_case_names=THREE_CASES,
    )
    assert outcome.ok is False
    assert which_job in outcome.reason
    assert repr(bad_result) in outcome.reason


def test_gate_fails_when_assess_is_skipped_because_execute_failed():
    # The exact real-world scenario: execute fails, so assess is reported
    # skipped by GitHub (its own `needs: execute` upstream failed) -- which
    # branch protection would otherwise treat as a satisfied required check.
    job_results = {"prepare": "success", "execute": "failure", "assess": "skipped"}
    outcome = gate.evaluate_gate(job_results, None, expected_case_names=THREE_CASES)
    assert outcome.ok is False


# ---------------------------------------------------------------------------
# Receipt-level failures
# ---------------------------------------------------------------------------


def test_gate_fails_when_receipt_is_missing():
    outcome = gate.evaluate_gate(ALL_SUCCESS, None, expected_case_names=THREE_CASES)
    assert outcome.ok is False
    assert "no readable receipt" in outcome.reason


def test_gate_fails_when_receipt_is_not_a_dict():
    outcome = gate.evaluate_gate(ALL_SUCCESS, ["not", "a", "dict"], expected_case_names=None)  # type: ignore[arg-type]
    assert outcome.ok is False
    assert "JSON object" in outcome.reason


def test_gate_fails_when_receipt_accepted_is_false():
    outcome = gate.evaluate_gate(
        ALL_SUCCESS,
        _receipt(accepted=False, cases=_full_accepted_cases(THREE_CASES)),
        expected_case_names=THREE_CASES,
    )
    assert outcome.ok is False
    assert "did not record acceptance" in outcome.reason


def test_gate_fails_when_receipt_accepted_is_truthy_but_not_literal_true():
    # "accepted": 1 or "true" must not be treated the same as True.
    outcome = gate.evaluate_gate(
        ALL_SUCCESS,
        {"accepted": 1, "cases": _full_accepted_cases(THREE_CASES)},
        expected_case_names=THREE_CASES,
    )
    assert outcome.ok is False


def test_gate_fails_when_cases_list_is_missing_for_multi_case_workflow():
    outcome = gate.evaluate_gate(
        ALL_SUCCESS,
        {"accepted": True},
        expected_case_names=THREE_CASES,
    )
    assert outcome.ok is False
    assert "per-case" in outcome.reason


def test_gate_fails_when_a_case_is_silently_dropped():
    outcome = gate.evaluate_gate(
        ALL_SUCCESS,
        _receipt(accepted=True, cases=_full_accepted_cases(["case-a", "case-b"])),
        expected_case_names=THREE_CASES,
    )
    assert outcome.ok is False
    assert "case-c" in outcome.reason


def test_gate_fails_when_a_forged_extra_case_is_present():
    outcome = gate.evaluate_gate(
        ALL_SUCCESS,
        _receipt(
            accepted=True,
            cases=_full_accepted_cases(THREE_CASES + ["case-forged"]),
        ),
        expected_case_names=THREE_CASES,
    )
    assert outcome.ok is False
    assert "case-forged" in outcome.reason


def test_gate_fails_when_one_case_entry_itself_is_not_accepted():
    cases = _full_accepted_cases(THREE_CASES)
    cases[1] = {"name": "case-b", "accepted": False}
    outcome = gate.evaluate_gate(
        ALL_SUCCESS,
        _receipt(accepted=True, cases=cases),
        expected_case_names=THREE_CASES,
    )
    assert outcome.ok is False
    assert "case-b" in outcome.reason


def test_gate_fails_when_a_case_entry_is_malformed():
    outcome = gate.evaluate_gate(
        ALL_SUCCESS,
        _receipt(accepted=True, cases=["not-a-dict"]),
        expected_case_names=THREE_CASES,
    )
    assert outcome.ok is False
    assert "malformed" in outcome.reason


def test_gate_fails_when_receipt_contains_a_duplicate_case_name():
    # The exact reproduced bug: a receipt that lists one real, accepted
    # case TWICE and omits a different expected case must still be
    # rejected -- a naive `set(names)` comparison would silently fold the
    # duplicate away and see the same cardinality as the fully-correct
    # set, wrongly passing.
    cases = _full_accepted_cases(["case-a", "case-a", "case-b"])
    outcome = gate.evaluate_gate(
        ALL_SUCCESS,
        _receipt(accepted=True, cases=cases),
        expected_case_names=THREE_CASES,
    )
    assert outcome.ok is False
    assert "duplicate" in outcome.reason
    assert "case-a" in outcome.reason


def test_gate_fails_when_job_results_is_empty():
    # A workflow misconfiguration that supplies zero `--job-result` facts
    # at all must never vacuously pass merely because there is nothing to
    # disagree with.
    outcome = gate.evaluate_gate({}, _receipt(accepted=True), expected_case_names=None)
    assert outcome.ok is False
    assert "no upstream job results" in outcome.reason


# ---------------------------------------------------------------------------
# File-loading helpers
# ---------------------------------------------------------------------------


def test_load_receipt_returns_none_for_missing_file(tmp_path):
    assert gate._load_receipt(tmp_path / "does-not-exist.json") is None


def test_load_receipt_returns_none_for_malformed_json(tmp_path):
    path = tmp_path / "receipt.json"
    path.write_text("{not valid json")
    assert gate._load_receipt(path) is None


def test_load_receipt_returns_none_when_path_is_none():
    assert gate._load_receipt(None) is None


def test_load_expected_case_names_parses_tab_separated_bundles_cases(tmp_path):
    path = tmp_path / "cases.txt"
    path.write_text("case-a\tbundles/case-a\ncase-b\tbundles/case-b\n")
    assert gate._load_expected_case_names(path) == ["case-a", "case-b"]


def test_load_expected_case_names_raises_for_missing_file(tmp_path):
    # Fail-closed: a file EXPLICITLY supplied via --expected-cases but
    # missing/unreadable must be distinguishable from "no file was
    # supplied at all" (which legitimately returns None for the
    # single-canonical-package workflow) -- collapsing both into an empty
    # list would let a missing cases.txt degrade into "zero cases
    # expected", which an empty receipt could then trivially satisfy.
    with pytest.raises(gate.ExpectedCaseLoadError):
        gate._load_expected_case_names(tmp_path / "missing.txt")


def test_load_expected_case_names_raises_for_empty_file(tmp_path):
    path = tmp_path / "cases.txt"
    path.write_text("")
    with pytest.raises(gate.ExpectedCaseLoadError):
        gate._load_expected_case_names(path)


def test_load_expected_case_names_returns_none_when_path_is_none():
    assert gate._load_expected_case_names(None) is None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_exits_zero_and_prints_pass_on_success(tmp_path, capsys):
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(_receipt(accepted=True, cases=_full_accepted_cases(THREE_CASES))))
    cases_path = tmp_path / "cases.txt"
    cases_path.write_text("".join(f"{name}\tbundles/{name}\n" for name in THREE_CASES))

    rc = gate.main(
        [
            "--job-result", "prepare=success",
            "--job-result", "execute=success",
            "--job-result", "assess=success",
            "--receipt", str(receipt_path),
            "--expected-cases", str(cases_path),
        ]
    )
    assert rc == 0
    assert "gate: PASS" in capsys.readouterr().out


def test_cli_exits_nonzero_and_prints_fail_on_failure(tmp_path, capsys):
    rc = gate.main(
        [
            "--job-result", "prepare=success",
            "--job-result", "execute=failure",
            "--job-result", "assess=skipped",
            "--receipt", str(tmp_path / "missing.json"),
        ]
    )
    assert rc == 1
    assert "gate: FAIL" in capsys.readouterr().err


def test_cli_rejects_malformed_job_result_argument():
    with pytest.raises(SystemExit):
        gate.main(["--job-result", "not-an-equals-sign"])


def test_cli_requires_at_least_one_job_result_argument():
    with pytest.raises(SystemExit):
        gate.main([])


def test_cli_fails_closed_when_expected_cases_file_is_missing(tmp_path, capsys):
    # The exact reproduced bug: job A's prepare step failed to produce
    # bundles/cases.txt (file absent), while an otherwise-complete,
    # vacuously-accepted empty receipt was recorded. Without this fix the
    # gate treated "file not found" as "zero cases expected" and the empty
    # receipt trivially satisfied that, producing a wrongly PASS result.
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(_receipt(accepted=True, cases=[])))
    rc = gate.main(
        [
            "--job-result", "prepare=success",
            "--job-result", "execute=success",
            "--job-result", "assess=success",
            "--receipt", str(receipt_path),
            "--expected-cases", str(tmp_path / "does-not-exist.txt"),
        ]
    )
    assert rc == 1
    assert "gate: FAIL" in capsys.readouterr().err


def test_cli_as_actual_subprocess_fails_closed_end_to_end(tmp_path):
    # Exercises the real entry point (`python3 gate.py ...`) exactly as the
    # workflow YAML invokes it, including process exit code -- not just the
    # in-process `main()` return value.
    gate_path = Path(__file__).resolve().parent / "gate.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(gate_path),
            "--job-result", "prepare=success",
            "--job-result", "execute=success",
            "--job-result", "assess=cancelled",
            "--receipt", str(tmp_path / "missing.json"),
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    assert "gate: FAIL" in completed.stderr
