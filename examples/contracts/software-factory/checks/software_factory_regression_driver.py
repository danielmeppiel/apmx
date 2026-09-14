"""Run exactly one regression suite in a fresh candidate interpreter."""

import argparse
import importlib
import io
import json
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from software_factory_cases import CASES


class ObservedResult(unittest.TextTestResult):
    """Record test identities and reject skipped or unexpectedly successful tests."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.rows: list[dict[str, str]] = []

    def stopTest(self, test: unittest.TestCase) -> None:
        problem = [item[0].id() for group in (self.failures, self.errors) for item in group]
        skipped = [item[0].id() for item in self.skipped]
        incomplete = [item.id() for item in self.unexpectedSuccesses]
        incomplete.extend(item[0].id() for item in self.expectedFailures)
        status = "passed"
        if test.id() in problem or any(item.startswith(test.id() + " ") for item in problem):
            status = "failed"
        if test.id() in skipped + incomplete or any(
            item.startswith(test.id() + " ") for item in skipped
        ):
            status = "incomplete"
        self.rows.append({"id": test.id(), "status": status})
        super().stopTest(test)


def check_case(functions: tuple[Any, Any], case: tuple[Any, ...]) -> dict[str, str]:
    identifier, subtotal, fee, total = case
    try:
        if isinstance(fee, str):
            for function in functions:
                try:
                    function(subtotal)
                except (TypeError, ValueError) as exc:
                    assert type(exc).__name__ == fee
                else:
                    raise AssertionError("Expected a rejected subtotal.")
        else:
            charge, quote = functions[0](subtotal), functions[1](subtotal)
            assert type(charge) is int and charge == fee
            assert type(quote) is dict and quote == {
                "subtotal_cents": subtotal,
                "delivery_cents": fee,
                "total_cents": total,
            }
            assert all(type(value) is int for value in quote.values())
    except AssertionError:
        return {"id": identifier, "status": "failed"}
    except Exception:  # noqa: BLE001 - Unexpected candidate errors are operational evidence.
        return {"id": identifier, "status": "error"}
    return {"id": identifier, "status": "passed"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("suite", choices=("original", "generated"))
    args = parser.parse_args()
    candidate, destination = args.candidate, args.destination
    sys.path.insert(0, str(candidate))
    acceptance = []
    if args.suite == "original":
        functions = (
            importlib.import_module("src.pricing").delivery_fee,
            importlib.import_module("src.checkout").checkout,
        )
        acceptance = [check_case(functions, case) for case in CASES]
    filename = "test_checkout.py" if args.suite == "original" else "test_free_shipping.py"
    suite = unittest.defaultTestLoader.discover(str(candidate / "tests"), pattern=filename)
    result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=ObservedResult).run(suite)
    report = {
        "suite": args.suite,
        "acceptance": acceptance,
        "tests": result.rows,
        "tests_run": result.testsRun,
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "expected_failures": len(result.expectedFailures),
        "unexpected_successes": len(result.unexpectedSuccesses),
    }
    destination.write_text(json.dumps(report), encoding="ascii")
    if result.errors or any(row["status"] == "error" for row in acceptance):
        return 2
    return int(not result.wasSuccessful() or any(row["status"] == "failed" for row in acceptance))


if __name__ == "__main__":
    raise SystemExit(main())
