"""Rerun the fixed Gherkin and regression suites on the code-plus-docs candidate."""

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from regression import inspect as inspect_regression
from software_factory_bdd import inspect as inspect_acceptance
from software_factory_support import Invalid, execute

REQUIRED = ("acceptance", "regression")


def inspect(root: Path, candidate: Path, area: Path) -> tuple[int, list[dict[str, Any]]]:
    status = 0
    observed = []
    for name, check in zip(REQUIRED, (inspect_acceptance, inspect_regression), strict=True):
        code, rows = check(root, candidate, area)
        if code not in (0, 1):
            raise Invalid(f"The combined candidate's {name} suite did not complete.")
        status = max(status, code)
        observed.append(
            {"id": name, "status": "passed" if code == 0 else "failed", "observed": rows}
        )
    return status, observed


if __name__ == "__main__":
    raise SystemExit(execute("documented-checkout", REQUIRED, inspect, documentation=True))
