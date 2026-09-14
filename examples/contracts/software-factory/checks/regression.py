"""Apply changes.diff and run fixed cases plus existing and generated tests."""

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from software_factory_cases import REQUIRED
from software_factory_support import Invalid, execute, rows_status, run_driver

EXISTING = {
    "test_checkout.CheckoutTests.test_small_order",
    "test_checkout.CheckoutTests.test_zero",
    "test_checkout.CheckoutTests.test_negative",
    "test_checkout.CheckoutTests.test_invalid_types",
}


def validate_suite(report: Any, suite: str, returncode: int) -> tuple[int, list[dict[str, Any]]]:
    if not isinstance(report, dict) or report.get("suite") != suite:
        raise Invalid("Missing or misattributed regression suite report.")
    tests = report.get("tests")
    if (
        not isinstance(tests, list)
        or not 1 <= len(tests) <= 200
        or any(
            not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]
            for row in tests
        )
    ):
        raise Invalid("Regression tests are missing, empty or malformed.")
    counters = ("tests_run", "errors", "skipped", "expected_failures", "unexpected_successes")
    if any(type(report.get(key)) is not int or report[key] < 0 for key in counters):
        raise Invalid("Regression result counters are missing or invalid.")
    identifiers = [row["id"] for row in tests]
    if len(set(identifiers)) != len(identifiers) or report["tests_run"] != len(tests):
        raise Invalid("Regression test identities or execution counts are incomplete.")
    rows = report.get("acceptance")
    if suite == "original":
        if set(identifiers) != EXISTING:
            raise Invalid("Original regression test identities are incomplete.")
        code = rows_status(rows, REQUIRED)
    else:
        if rows != [] or not all(
            identifier.startswith("test_free_shipping.") for identifier in identifiers
        ):
            raise Invalid("Generated tests cannot supply original regression evidence.")
        code = 0
    if any(
        report.get(key) != 0
        for key in ("errors", "skipped", "expected_failures", "unexpected_successes")
    ):
        raise Invalid("Regression tests were errored, skipped, pending or incomplete.")
    test_code = rows_status(tests, tuple(identifiers))
    code = max(code, test_code)
    if returncode != code:
        raise Invalid("Regression process and complete report disagree.")
    return code, [*rows, *tests]


def inspect(root: Path, candidate: Path, area: Path) -> tuple[int, list[dict[str, Any]]]:
    code = 0
    rows = []
    for suite in ("original", "generated"):
        returncode, report = run_driver(
            root, candidate, area, "software_factory_regression_driver.py", suite
        )
        observed_code, observed_rows = validate_suite(report, suite, returncode)
        code = max(code, observed_code)
        rows.extend(observed_rows)
    return code, rows


if __name__ == "__main__":
    raise SystemExit(execute("regression", REQUIRED, inspect))
