"""Strict Behave configuration and complete scenario-row report validation."""

from pathlib import Path
from typing import Any

from software_factory_cases import REQUIRED
from software_factory_support import Invalid, rows_status, run_driver


def expected_scenarios(root: Path) -> dict[str, Any]:
    try:
        import behave
        from behave.parser import parse_file
    except ImportError as exc:
        raise Invalid(
            "Optional Behave is missing. Install the factory extra (Behave==1.3.3) "
            "in the Python environment running this check."
        ) from exc
    if behave.__version__ != "1.3.3":
        raise Invalid("This example requires optional Behave==1.3.3.")
    try:
        feature_path = root / "checks/features/free-shipping.feature"
        feature = parse_file(str(feature_path))
        scenarios = {
            scenario.name: {
                "file": feature_path.resolve(),
                "line": scenario.line,
                "steps": [
                    {"name": step.name, "keyword": step.keyword, "line": step.line}
                    for step in scenario.steps
                ],
            }
            for scenario in feature.walk_scenarios()
        }
    except Exception as exc:
        raise Invalid("Supplied Gherkin could not be parsed into complete scenario rows.") from exc
    identifiers = [name.partition(" -- @")[0] for name in scenarios]
    if sorted(identifiers) != sorted(REQUIRED):
        raise Invalid("Supplied Gherkin does not contain every required example row exactly once.")
    return scenarios


def location_matches(location: Any, file: Path, line: int, report_root: Path) -> bool:
    if not isinstance(location, str):
        return False
    name, separator, number = location.rpartition(":")
    return bool(
        separator and number == str(line) and name and (report_root / name).resolve() == file
    )


def validate_report(
    report: Any, expected: dict[str, Any], report_root: Path
) -> tuple[int, list[dict[str, Any]]]:
    if not isinstance(report, list) or len(report) != 1:
        raise Invalid("Expected one complete Behave feature report.")
    feature = report[0]
    if not isinstance(feature, dict) or not isinstance(feature.get("elements"), list):
        raise Invalid("Malformed Behave feature report.")
    elements = feature["elements"]
    if any(
        not isinstance(element, dict) or not isinstance(element.get("name"), str)
        for element in elements
    ):
        raise Invalid("Malformed Behave scenario report.")
    names = [element["name"] for element in elements]
    if sorted(names) != sorted(expected):
        raise Invalid("Behave omitted, duplicated or filtered required example rows.")
    rows = []
    for element in elements:
        scenario = expected[element["name"]]
        steps = element.get("steps")
        if (
            element.get("type") != "scenario"
            or not location_matches(
                element.get("location"), scenario["file"], scenario["line"], report_root
            )
            or not isinstance(steps, list)
            or len(steps) != len(scenario["steps"])
        ):
            raise Invalid("Behave scenario or step identities are incomplete.")
        statuses = []
        for actual, trusted in zip(steps, scenario["steps"]):
            if not isinstance(actual, dict) or not isinstance(actual.get("result"), dict):
                raise Invalid("Malformed Behave step or result object.")
            if any(
                actual.get(key) != trusted[key] for key in ("name", "keyword")
            ) or not location_matches(
                actual.get("location"), scenario["file"], trusted["line"], report_root
            ):
                raise Invalid("Behave steps do not match the supplied acceptance scenario.")
            status = actual["result"].get("status")
            if status not in ("passed", "failed", "skipped"):
                raise Invalid("A step was undefined, pending, errored or unexecuted.")
            statuses.append(status)
        status = element.get("status")
        if status == "passed":
            if any(item != "passed" for item in statuses):
                raise Invalid("A passing scenario contains an unexecuted step.")
        elif status == "failed":
            if "failed" not in statuses or "skipped" in statuses[: statuses.index("failed")]:
                raise Invalid("A failed scenario lacks an observed assertion failure.")
        else:
            raise Invalid("A scenario was skipped, undefined, pending or unexecuted.")
        rows.append({"id": element["name"].partition(" -- @")[0], "status": status})
    code = rows_status(rows, REQUIRED)
    if feature.get("status") != ("passed" if code == 0 else "failed"):
        raise Invalid("Behave feature and scenario results disagree.")
    return code, rows


def inspect(root: Path, candidate: Path, area: Path) -> tuple[int, list[dict[str, Any]]]:
    expected = expected_scenarios(root)
    returncode, report = run_driver(root, candidate, area, "software_factory_bdd_driver.py")
    code, rows = validate_report(report, expected, area)
    if returncode != code:
        raise Invalid("Behave process and complete report disagree.")
    return code, rows
