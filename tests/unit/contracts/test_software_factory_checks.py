"""Real patch-aware application checks with positive and independent negative controls."""

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_software_factory_support import (
    EXAMPLE,
    GENERATED_TESTS,
    authored_patch,
    factory,
    invoke,
    outputs,
    seed,
)

__all__ = ["factory"]
pytestmark = pytest.mark.component
BDD = pytest.mark.skipif(
    importlib.util.find_spec("behave") is None,
    reason="Optional example integration: install apmx[factory] (Behave==1.3.3).",
)


@pytest.fixture
def candidate(tmp_path: Path) -> Path:
    root = seed(tmp_path / "relocated factory")
    for name, raw in outputs(tmp_path).items():
        (root / name).write_bytes(raw)
    return root


def test_authored_patch_cleans_git_objects_without_changing_the_patch(tmp_path: Path) -> None:
    neighbor = tmp_path / "keep.txt"
    neighbor.write_bytes(b"not owned by the patch fixture")
    first = authored_patch(tmp_path)
    assert not (tmp_path / "software-factory-patch-good").exists()
    assert authored_patch(tmp_path) == first
    assert not (tmp_path / "software-factory-patch-good").exists()
    assert neighbor.read_bytes() == b"not owned by the patch fixture"


@pytest.mark.parametrize(
    ("document", "artifact"),
    [
        ("planning", "plan.md"),
        ("specification", "specification.md"),
        ("implementation", "implementation.md"),
        ("review", "review.md"),
    ],
)
def test_markdown_checks_report_their_limited_scope(
    candidate: Path, document: str, artifact: str
) -> None:
    before = (candidate / artifact).read_bytes()
    code, report = invoke(candidate, "documents.py", document, artifact)
    assert code == 0 and report["status"] == "passed"
    assert report["sha256"] == hashlib.sha256(before).hexdigest()
    assert report["scope"].startswith("Document sections only")
    assert (candidate / artifact).read_bytes() == before


def test_markdown_checks_do_not_pretend_to_establish_semantics(candidate: Path) -> None:
    (candidate / "specification.md").write_text(
        "## Behavior\nAn intentionally wrong rule.\n## Interface\nA wrong interface.\n"
        "## Acceptance\nNot correctness evidence.\n",
        encoding="ascii",
    )
    assert invoke(candidate, "documents.py", "specification", "specification.md")[0] == 0
    (candidate / "specification.md").write_text("No sections.\n", encoding="ascii")
    assert invoke(candidate, "documents.py", "specification", "specification.md")[0] == 1


@pytest.mark.parametrize("checker", ["regression.py", pytest.param("acceptance.py", marks=BDD)])
def test_candidate_is_applied_and_tested_in_one_invocation(candidate: Path, checker: str) -> None:
    before = {
        path.relative_to(candidate).as_posix(): path.read_bytes()
        for path in candidate.rglob("*")
        if path.is_file()
    }
    code, report = invoke(candidate, checker, "changes.diff")
    assert code == 0, report
    assert report["status"] == "passed" and len(report["required"]) == 14
    assert all(row["status"] == "passed" for row in report["observed"])
    subject = report["subject"]
    assert subject["patch"] == hashlib.sha256(before["changes.diff"]).hexdigest()
    assert subject["base"] != subject["candidate"]
    assert len(subject["base_files"]) == 4 and len(subject["candidate_files"]) == 5
    assert not list(candidate.glob(".software-factory-check-*"))
    after = {
        path.relative_to(candidate).as_posix(): path.read_bytes()
        for path in candidate.rglob("*")
        if path.is_file()
    }
    assert before == after


@pytest.mark.parametrize("variant", ("baseline", "boundary"))
@pytest.mark.parametrize("checker", ["regression.py", pytest.param("acceptance.py", marks=BDD)])
def test_supplied_acceptance_rejects_baseline_and_off_by_one_even_with_vacuous_generated_tests(
    candidate: Path, tmp_path: Path, variant: str, checker: str
) -> None:
    tests = GENERATED_TESTS.replace(
        "self.assertEqual(delivery_fee(5000), 0)", "self.assertTrue(True)"
    )
    tests = tests.replace('self.assertEqual(checkout(5000)["total_cents"], 5000)', "pass")
    tests = tests.replace('self.assertEqual(checkout(5001)["delivery_cents"], 0)', "pass")
    raw = authored_patch(tmp_path, variant, tests)
    (candidate / "changes.diff").write_bytes(raw)
    code, report = invoke(candidate, checker, "changes.diff")
    assert code == 1, report
    assert report["status"] == "failed"
    assert {"id": "Free delivery at 5000 cents", "status": "failed"} in report["observed"]


@pytest.mark.parametrize("checker", ["regression.py", pytest.param("acceptance.py", marks=BDD)])
@pytest.mark.parametrize(
    "fault", ("missing-patch", "invalid-patch", "wrong-base", "protected", "mode")
)
def test_invalid_patch_subject_is_operational_not_passing(
    candidate: Path, checker: str, fault: str
) -> None:
    patch = candidate / "changes.diff"
    if fault == "missing-patch":
        patch.unlink()
    elif fault == "invalid-patch":
        patch.write_bytes(b"not a patch\n")
    elif fault == "wrong-base":
        (candidate / "src/pricing.py").write_bytes(b"wrong baseline\n")
    elif fault == "protected":
        patch.write_bytes(patch.read_bytes().replace(b"src/pricing.py", b"checks/steps.py"))
    else:
        patch.write_bytes(
            patch.read_bytes().replace(b"new file mode 100644", b"new file mode 100755")
        )
    code, report = invoke(candidate, checker, "changes.diff")
    assert code == 2 and report["status"] == "error", report
    assert not list(candidate.glob(".software-factory-check-*"))


@pytest.mark.parametrize(
    "tests",
    [
        "import unittest\n",
        GENERATED_TESTS.replace(
            "class FreeShippingTests", "@unittest.skip('not yet')\nclass FreeShippingTests"
        ),
        GENERATED_TESTS.replace(
            "    def test_threshold", "    @unittest.expectedFailure\n    def test_threshold"
        ),
        GENERATED_TESTS
        + "\ndef load_tests(loader, tests, pattern):\n    return unittest.TestSuite()\n",
    ],
)
def test_empty_skipped_expected_failure_or_filtered_generated_tests_are_incomplete(
    candidate: Path, tmp_path: Path, tests: str
) -> None:
    (candidate / "changes.diff").write_bytes(authored_patch(tmp_path, "incomplete", tests))
    code, report = invoke(candidate, "regression.py", "changes.diff")
    assert code == 2 and report["status"] == "error", report


def test_missing_optional_behave_has_actionable_status_two(
    candidate: Path,
    factory: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import builtins

    original_import = builtins.__import__

    def missing(name: str, *args: object, **kwargs: object) -> object:
        if name == "behave" or name.startswith("behave."):
            raise ImportError("deliberately unavailable optional tool")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    monkeypatch.chdir(candidate)
    assert (
        factory.support.execute(
            "acceptance", factory.cases.REQUIRED, factory.bdd.inspect, ["changes.diff"]
        )
        == 2
    )
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "error" and "Behave==1.3.3" in report["detail"]


@BDD
def test_external_behave_configuration_and_environment_cannot_filter_required_examples(
    candidate: Path,
) -> None:
    for name in ("behave.ini", ".behaverc", "setup.cfg", "tox.ini"):
        (candidate / name).write_text(
            "[behave]\ntags = @does-not-exist\nwip = true\n", encoding="ascii"
        )
    environment = {
        **os.environ,
        "BEHAVE_TAGS": "@does-not-exist",
        "BEHAVE_STAGE": "missing",
        "BEHAVE_CONFIG": str(candidate / "behave.ini"),
        "BEHAVE_USERDATA": "candidate=/nonexistent",
    }
    code, report = invoke(candidate, "acceptance.py", "changes.diff", env=environment)
    assert code == 0, report
    assert len(report["observed"]) == 14


@pytest.mark.parametrize(
    "status", ("skipped", "undefined", "pending", "pending_warn", "untested", "error")
)
def test_incomplete_scenario_rows_never_normalize_to_pass(
    factory: SimpleNamespace, status: str
) -> None:
    with pytest.raises(factory.support.Invalid):
        factory.support.rows_status([{"id": "required", "status": status}], ("required",))


@pytest.mark.parametrize(
    "rows",
    ([], [{"id": "other", "status": "passed"}], [{"id": "required", "status": "passed"}] * 2),
)
def test_empty_filtered_or_duplicate_reports_are_incomplete(
    factory: SimpleNamespace, rows: list[dict[str, str]]
) -> None:
    with pytest.raises(factory.support.Invalid):
        factory.support.rows_status(rows, ("required",))


@pytest.mark.parametrize("name", ("src/pricing.py", "changes.diff"))
def test_symlink_inputs_are_rejected_before_application(candidate: Path, name: str) -> None:
    target = candidate / name
    other = candidate / "actual-file"
    other.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(other)
    except OSError:
        pytest.skip("Symlink creation requires additional privileges on this host.")
    code, report = invoke(candidate, "regression.py", "changes.diff")
    assert code == 2 and "Symlink" in report["detail"]


def test_example_does_not_contain_an_external_scheduler_or_evidence_owner() -> None:
    assert not (EXAMPLE / "run.py").exists()
    assert not (EXAMPLE / "evidence.py").exists()
    assert not (EXAMPLE / "shipping.py").exists()


@BDD
def test_threshold_is_a_concrete_supplied_scenario_not_only_an_outline(
    factory: SimpleNamespace,
) -> None:
    expected = factory.bdd.expected_scenarios(EXAMPLE)
    scenario = expected["Free delivery at 5000 cents"]
    assert [step["name"] for step in scenario["steps"]] == [
        "a subtotal of 5000",
        "I request pricing and checkout",
        "delivery is 0 cents and the total is 5000 cents",
    ]


@pytest.fixture
def behave_report(factory: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> tuple[dict, list]:
    """One supplied outline row, including its trusted step identities."""
    monkeypatch.setattr(factory.bdd, "REQUIRED", ("required",))
    name = "required -- @1.1"
    steps = [
        {"keyword": "Given", "name": "input", "line": 3},
        {"keyword": "When", "name": "call", "line": 4},
        {"keyword": "Then", "name": "expected result", "line": 5},
    ]
    file = Path.cwd() / "checks/features/free-shipping.feature"
    expected = {name: {"file": file, "line": 10, "steps": steps}}
    report = [
        {
            "status": "passed",
            "elements": [
                {
                    "type": "scenario",
                    "name": name,
                    "location": f"{file}:10",
                    "status": "passed",
                    "steps": [
                        {
                            **step,
                            "location": f"{file}:{step['line']}",
                            "result": {"status": "passed"},
                        }
                        for step in steps
                    ],
                }
            ],
        }
    ]
    return expected, report


def test_complete_behave_report_is_accepted(
    factory: SimpleNamespace,
    behave_report: tuple[dict, list],
) -> None:
    expected, report = behave_report
    assert factory.bdd.validate_report(report, expected, Path.cwd()) == (
        0,
        [{"id": "required", "status": "passed"}],
    )


def test_behave_assertion_failure_with_remaining_steps_skipped_is_behavior_failure(
    factory: SimpleNamespace,
    behave_report: tuple[dict, list],
) -> None:
    expected, report = behave_report
    report[0]["status"] = "failed"
    scenario = report[0]["elements"][0]
    scenario["status"] = "failed"
    scenario["steps"][1]["result"]["status"] = "failed"
    scenario["steps"][2]["result"]["status"] = "skipped"
    assert factory.bdd.validate_report(report, expected, Path.cwd())[0] == 1


@pytest.mark.parametrize(
    "fault",
    (
        "empty",
        "filtered",
        "duplicate",
        "missing-step",
        "different-step",
        "different-line",
        "skipped",
        "undefined",
        "pending",
        "pending_warn",
        "untested",
        "wip-pending",
    ),
)
def test_strict_behave_report_refuses_incomplete_or_misattributed_results(
    factory: SimpleNamespace,
    behave_report: tuple[dict, list],
    fault: str,
) -> None:
    expected, original = behave_report
    report = copy.deepcopy(original)
    element = report[0]["elements"][0]
    if fault == "empty":
        report = []
    elif fault == "filtered":
        report[0]["elements"] = []
    elif fault == "duplicate":
        report[0]["elements"].append(copy.deepcopy(element))
    elif fault == "missing-step":
        element["steps"].pop()
    elif fault == "different-step":
        element["steps"][0]["name"] = "not the supplied step"
    elif fault == "different-line":
        element["location"] = element["location"].replace(":10", ":11")
    else:
        element["steps"][0]["result"]["status"] = fault
    with pytest.raises(factory.support.Invalid):
        factory.bdd.validate_report(report, expected, Path.cwd())


@pytest.mark.parametrize("name", ("src/pricing.py", "changes.diff", "checks/documents.py"))
def test_subject_tampering_during_check_is_operational(
    candidate: Path,
    factory: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    name: str,
) -> None:
    def mutate(root: Path, _candidate: Path, _area: Path) -> tuple[int, list]:
        target = root / name
        target.write_bytes(target.read_bytes() + b"\n# changed\n")
        return 0, []

    monkeypatch.chdir(candidate)
    assert factory.support.execute("mutation", (), mutate, ["changes.diff"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_optional_bdd_missing_in_actual_isolated_python_process(candidate: Path) -> None:
    process = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(candidate / "checks/acceptance.py"),
            "changes.diff",
        ],
        cwd=candidate,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert process.returncode == 2, process.stderr
    report = json.loads(process.stdout)
    assert report["status"] == "error" and "Optional Behave is missing" in report["detail"]


def test_missing_git_is_operational_with_setup_guidance(candidate: Path) -> None:
    code, report = invoke(
        candidate, "regression.py", "changes.diff", env={**os.environ, "PATH": ""}
    )
    assert code == 2 and "Install Git" in report["detail"]


@BDD
@pytest.mark.parametrize("fault", ("undefined", "pending"))
def test_real_behave_undefined_or_pending_steps_are_incomplete(candidate: Path, fault: str) -> None:
    steps = candidate / "checks/features/steps/checkout_steps.py"
    text = steps.read_text()
    if fault == "undefined":
        text = text.replace("I request pricing and checkout", "an undefined step name")
    else:
        text = text.replace(
            "    context.results = []",
            "    from behave.api.pending_step import StepNotImplementedError\n"
            "    raise StepNotImplementedError('not implemented')",
        )
    steps.write_text(text, encoding="ascii")
    code, report = invoke(candidate, "acceptance.py", "changes.diff")
    assert code == 2 and report["status"] == "error", report


@pytest.mark.parametrize("name", ("../changes.diff", "/changes.diff", "checks/../changes.diff"))
def test_patch_path_must_be_relative_and_contained(candidate: Path, name: str) -> None:
    code, report = invoke(candidate, "regression.py", name)
    assert code == 2 and report["status"] == "error"


@pytest.mark.windows_compat
@pytest.mark.parametrize("checker", ["regression.py", pytest.param("acceptance.py", marks=BDD)])
def test_matching_crlf_baseline_and_patch_preserve_byte_identity(
    candidate: Path,
    tmp_path: Path,
    checker: str,
) -> None:
    names = ("src/__init__.py", "src/pricing.py", "src/checkout.py", "tests/test_checkout.py")
    for name in names:
        path = candidate / name
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    patch = authored_patch(tmp_path, "crlf", crlf=True)
    (candidate / "changes.diff").write_bytes(patch)
    code, report = invoke(candidate, checker, "changes.diff")
    assert code == 0, report
    assert report["subject"]["patch"] == hashlib.sha256(patch).hexdigest()
    assert (
        report["subject"]["base_files"]["src/pricing.py"]["sha256"]
        == hashlib.sha256((candidate / "src/pricing.py").read_bytes()).hexdigest()
    )


@BDD
@pytest.mark.parametrize("leaking_mock", (False, True))
def test_generated_import_time_mock_cannot_hide_an_original_regression(
    candidate: Path, tmp_path: Path, leaking_mock: bool
) -> None:
    tests = GENERATED_TESTS
    if leaking_mock:
        tests = (
            "from unittest.mock import patch\n"
            "patch('src.pricing.SMALL_ORDER_FEE_CENTS', 500).start()\n" + tests
        )
    patch = authored_patch(tmp_path, "small-order", tests)
    (candidate / "changes.diff").write_bytes(patch)
    assert invoke(candidate, "acceptance.py", "changes.diff")[0] == 0
    code, report = invoke(candidate, "regression.py", "changes.diff")
    assert code == 1, report
    assert {"id": "test_checkout.CheckoutTests.test_small_order", "status": "failed"} in report[
        "observed"
    ]
    assert all(
        row["status"] == "passed"
        for row in report["observed"]
        if row["id"].startswith("test_free_shipping.")
    )
    assert report["subject"]["patch"] == hashlib.sha256(patch).hexdigest()


def test_generated_mocks_and_imports_are_isolated_from_original_tests(
    candidate: Path, tmp_path: Path
) -> None:
    tests = (
        "import sys\n"
        "from unittest.mock import patch\n"
        "assert 'test_checkout' not in sys.modules\n"
        "patch('src.pricing.DELIVERY_FEE_CENTS', 777).start()\n" + GENERATED_TESTS
    )
    (candidate / "changes.diff").write_bytes(authored_patch(tmp_path, "isolated-mock", tests))
    code, report = invoke(candidate, "regression.py", "changes.diff")
    assert code == 0 and len(report["observed"]) == 20, report
    assert all(row["status"] == "passed" for row in report["observed"])


@BDD
@pytest.mark.parametrize(
    ("location", "value"),
    [
        ("report[0]", None),
        ("report[0]['elements']", None),
        ("report[0]['elements']", {}),
        ("report[0]['elements'][0]", None),
        ("report[0]['elements'][0]", []),
        ("report[0]['elements'][0]['name']", None),
        ("report[0]['elements'][0]['steps']", {}),
        ("report[0]['elements'][0]['steps'][0]", None),
        ("report[0]['elements'][0]['steps'][0]", []),
        ("report[0]['elements'][0]['steps'][0]", "invalid"),
        ("report[0]['elements'][0]['steps'][0]", 1),
        ("report[0]['elements'][0]['steps'][0]", False),
        ("report[0]['elements'][0]['steps'][0]['result']", None),
        ("report[0]['elements'][0]['steps'][0]['result']", []),
        ("report[0]['elements'][0]['steps'][0]['result']", "invalid"),
        ("report[0]['elements'][0]['steps'][0]['result']", 0),
        ("report[0]['elements'][0]['steps'][0]['result']", False),
        ("report[0]['elements'][0]['steps'][0]['result']", {}),
        ("report[0]['elements'][0]['steps'][0]['result']['status']", None),
        ("report[0]['elements'][0]['steps'][0]['result']['status']", []),
    ],
)
def test_malformed_behave_shapes_return_structured_operational_error_in_subprocess(
    candidate: Path, location: str, value: object
) -> None:
    driver = candidate / "checks/software_factory_bdd_driver.py"
    original = driver.with_name("software_factory_bdd_driver_original.py")
    original.write_bytes(driver.read_bytes())
    driver.write_text(
        "import json\nimport runpy\nimport sys\nfrom pathlib import Path\n"
        "try:\n"
        f"    runpy.run_path(str(Path(__file__).with_name({original.name!r})), "
        "run_name='__main__')\n"
        "except SystemExit as exc:\n"
        "    assert exc.code == 0\n"
        "destination = Path(sys.argv[2])\n"
        "report = json.loads(destination.read_bytes())\n"
        f"{location} = {value!r}\n"
        "destination.write_text(json.dumps(report), encoding='ascii')\n",
        encoding="ascii",
    )
    process = subprocess.run(
        [sys.executable, "-I", "-B", str(candidate / "checks/acceptance.py"), "changes.diff"],
        cwd=candidate,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == 2, process.stderr.decode(errors="replace")[-600:]
    assert 0 < len(process.stdout) < 128 * 1024
    report = json.loads(process.stdout)
    assert report["status"] == "error" and report["subject"]["patch"]
    assert not process.stderr
