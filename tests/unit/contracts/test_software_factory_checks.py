"""Task checks assess generated software independently; no live inference."""

import json
from pathlib import Path

import pytest

from test_software_factory_support import EXAMPLE, SOURCE, Modules, outputs
from test_software_factory_support import factory as factory

pytestmark = pytest.mark.component


@pytest.fixture
def candidate(factory: Modules, tmp_path: Path) -> Path:
    files = outputs(factory)
    files["request.json"] = (EXAMPLE / "request.json").read_bytes()
    files["evidence.json"] = factory.evidence.encode(
        {
            "assurance": "UNPROVEN",
            "stages": [
                {
                    "stage": stage.name,
                    "output": stage.output,
                    "sha256": factory.evidence.sha(files[stage.output]),
                    "checks": [{"name": "contract", "normalized": 0, "raw_exit": 0}],
                }
                for stage in factory.driver.STAGES[:4]
            ],
        }
    )
    for name, raw in files.items():
        (tmp_path / name).write_bytes(raw)
    return tmp_path


@pytest.mark.parametrize("phase", ("planning", "specification", "build", "test", "review"))
def test_authored_valid_artifacts_pass_without_modifying_inputs(
    factory: Modules,
    candidate: Path,
    phase: str,
) -> None:
    before = {path.name: path.read_bytes() for path in candidate.iterdir()}
    assert factory.checks.main([phase, "--directory", str(candidate)]) == 0
    assert before == {path.name: path.read_bytes() for path in candidate.iterdir()}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, {"returns": 500}),
        (1000, {"returns": 500}),
        (1001, {"returns": 700}),
        (2000, {"returns": 700}),
        (2001, {"returns": 900}),
        (3001, {"returns": 1100}),
        (4001, {"returns": 1300}),
        (5000, {"returns": 1300}),
        (0, {"raises": "ValueError"}),
        (-1, {"raises": "ValueError"}),
        (5001, {"raises": "ValueError"}),
        (True, {"raises": "TypeError"}),
        (False, {"raises": "TypeError"}),
        (1.0, {"raises": "TypeError"}),
        ("1", {"raises": "TypeError"}),
        (None, {"raises": "TypeError"}),
    ],
)
def test_oracle_has_fixed_independent_business_expectations(
    factory: Modules,
    value: object,
    expected: dict[str, object],
) -> None:
    assert factory.checks.oracle(value) == expected


def test_all_integer_partitions_match_rates(factory: Modules) -> None:
    for lower, upper, cents in (
        (1, 1000, 500),
        (1001, 2000, 700),
        (2001, 3000, 900),
        (3001, 4000, 1100),
        (4001, 5000, 1300),
    ):
        assert all(
            factory.checks.oracle(value) == {"returns": cents} for value in range(lower, upper + 1)
        )


@pytest.mark.parametrize(
    "source",
    [
        SOURCE.replace("<= 1000", "< 1000"),
        SOURCE.replace("return 500", "return 501"),
        SOURCE.replace("> 5000", ">= 5000"),
        SOURCE.replace("< 1", "< 0"),
        SOURCE.replace("if type(weight_grams) is not int:", "if type(weight_grams) is int:"),
    ],
)
def test_fixed_acceptance_rejects_bugs_even_without_generated_tests(
    factory: Modules,
    candidate: Path,
    source: str,
) -> None:
    (candidate / "shipping.py").write_text(source, encoding="ascii")
    (candidate / "tests.json").unlink()
    assert factory.checks.main(["build", "--directory", str(candidate)]) == 1


@pytest.mark.parametrize(
    "source",
    [
        "import os\n" + SOURCE,
        SOURCE + "\nopen('outside', 'w')\n",
        SOURCE.replace("return 500", "return 499 + 1"),
        SOURCE.replace("return 500", "return int('500')"),
        SOURCE.replace("return 500", "return weight_grams.__class__"),
        SOURCE.replace("return 500", "return [500][0]"),
        SOURCE.replace("return 500", "while True:\n            pass"),
        SOURCE.replace("return 500", "return True"),
        SOURCE.replace("return 500", "return -1"),
        SOURCE.replace("return 500", "return 1000001"),
        SOURCE.replace("return 500", "return (x for x in [])"),
        SOURCE.replace("weight_grams):", "weight_grams=1):"),
        SOURCE.replace("weight_grams):", "weight_grams: int):"),
        "@type\n" + SOURCE,
        SOURCE + "\ndef other():\n    return 1\n",
        SOURCE.replace("return 500", "def inner():\n            return 1\n        return 500"),
        SOURCE.replace('TypeError("integer required")', "TypeError(weight_grams)"),
        SOURCE.replace("type(weight_grams)", "type(int)"),
        SOURCE.replace('TypeError("integer required")', 'ValueError("x") from TypeError'),
        SOURCE.replace('TypeError("integer required")', "TypeError(message='x')"),
        "# " + "x" * 8192 + "\n" + SOURCE,
        SOURCE + "\n# \u00e9\n",
    ],
)
def test_unsupported_source_is_refused_before_execution(
    factory: Modules,
    candidate: Path,
    source: str,
) -> None:
    (candidate / "shipping.py").write_text(source, encoding="utf-8")
    with pytest.raises(factory.checks.Invalid):
        factory.checks.load_function(candidate)
    assert not (candidate / "outside").exists()


def test_deep_source_is_bounded(factory: Modules, candidate: Path) -> None:
    (candidate / "shipping.py").write_text(
        "def shipping_cost(weight_grams):\n"
        + "    if weight_grams == 1:\n        return 500\n" * 100,
        encoding="ascii",
    )
    with pytest.raises(factory.checks.Invalid, match="AST nodes"):
        factory.checks.load_function(candidate)


@pytest.mark.parametrize(
    "raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e9999}', b"{", b"\xff"]
)
def test_json_is_strict(factory: Modules, raw: bytes) -> None:
    with pytest.raises(factory.checks.Invalid):
        factory.checks.decode(raw)


def test_wrong_generated_expectations_fail(factory: Modules, candidate: Path) -> None:
    tests = json.loads((candidate / "tests.json").read_bytes())
    tests["cases"][0]["expected"] = {"returns": 500}
    (candidate / "tests.json").write_text(json.dumps(tests), encoding="ascii")
    assert factory.checks.main(["test", "--directory", str(candidate)]) == 1


@pytest.mark.parametrize("value", [True, 1000, 0, 5000])
def test_required_mutant_counterexamples_cannot_be_removed(
    factory: Modules,
    candidate: Path,
    value: object,
) -> None:
    tests = json.loads((candidate / "tests.json").read_bytes())
    tests["cases"] = [
        case for case in tests["cases"] if json.dumps(case["input"]) != json.dumps(value)
    ]
    (candidate / "tests.json").write_text(json.dumps(tests), encoding="ascii")
    assert factory.checks.main(["test", "--directory", str(candidate)]) == 1


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("bool-as-int", True),
        ("tier-boundary", 1000),
        ("zero-accepted", 0),
        ("maximum-excluded", 5000),
    ],
)
def test_each_fixed_mutant_is_exposed(factory: Modules, name: str, value: object) -> None:
    assert factory.checks.mutant(name, value) != factory.checks.oracle(value)


def test_plan_cannot_skip_a_phase_or_requirement(factory: Modules, candidate: Path) -> None:
    plan = json.loads((candidate / "plan.json").read_bytes())
    plan["steps"][0]["phase"] = "build"
    (candidate / "plan.json").write_text(json.dumps(plan), encoding="ascii")
    assert factory.checks.main(["planning", "--directory", str(candidate)]) == 1


def test_specification_cannot_change_fixed_task(factory: Modules, candidate: Path) -> None:
    spec = json.loads((candidate / "spec.json").read_bytes())
    spec["tiers"][0]["cents"] = 100
    (candidate / "spec.json").write_text(json.dumps(spec), encoding="ascii")
    assert factory.checks.main(["specification", "--directory", str(candidate)]) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("advisory", False),
        ("assurance", "VERIFIED"),
        ("recommendation", "production_ready"),
        (
            "findings",
            [
                {
                    "file": "../outside",
                    "line": 1,
                    "severity": "low",
                    "requirements": ["purity"],
                    "detail": "Wrong file.",
                }
            ],
        ),
        (
            "findings",
            [
                {
                    "file": "shipping.py",
                    "line": 9999,
                    "severity": "low",
                    "requirements": ["purity"],
                    "detail": "Wrong line.",
                }
            ],
        ),
    ],
)
def test_review_does_not_certify_or_accept_bad_references(
    factory: Modules,
    candidate: Path,
    field: str,
    value: object,
) -> None:
    review = json.loads((candidate / "review.json").read_bytes())
    review[field] = value
    (candidate / "review.json").write_text(json.dumps(review), encoding="ascii")
    assert factory.checks.main(["review", "--directory", str(candidate)]) == 1


def test_review_evidence_is_bound_to_supplied_bytes(factory: Modules, candidate: Path) -> None:
    evidence = json.loads((candidate / "evidence.json").read_bytes())
    evidence["stages"][0]["sha256"] = "0" * 64
    (candidate / "evidence.json").write_text(json.dumps(evidence), encoding="ascii")
    assert factory.checks.main(["review", "--directory", str(candidate)]) == 1


def test_malformed_review_evidence_is_a_failed_condition(factory: Modules, candidate: Path) -> None:
    evidence = json.loads((candidate / "evidence.json").read_bytes())
    evidence["stages"][0] = {}
    (candidate / "evidence.json").write_text(json.dumps(evidence), encoding="ascii")
    assert factory.checks.main(["review", "--directory", str(candidate)]) == 1


def test_large_numeric_case_is_refused_without_float_overflow(
    factory: Modules, candidate: Path
) -> None:
    tests = json.loads((candidate / "tests.json").read_bytes())
    tests["cases"][0]["input"] = 10**1000
    (candidate / "tests.json").write_text(json.dumps(tests), encoding="ascii")
    assert factory.checks.main(["test", "--directory", str(candidate)]) == 1


def test_quote_executes_validated_function_and_reports_typed_result(
    factory: Modules,
    candidate: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert factory.checks.main(["quote", "--directory", str(candidate), "--weight", "1001"]) == 0
    assert json.loads(capsys.readouterr().out) == {"returns": 700}
    assert factory.checks.main(["quote", "--directory", str(candidate), "--weight", "true"]) == 0
    assert json.loads(capsys.readouterr().out) == {"raises": "TypeError"}


def test_missing_files_are_incomplete_not_passing(factory: Modules, tmp_path: Path) -> None:
    assert factory.checks.main(["build", "--directory", str(tmp_path)]) == 2


def test_malformed_candidate_is_a_failed_condition(factory: Modules, candidate: Path) -> None:
    (candidate / "plan.json").write_text("{", encoding="ascii")
    assert factory.checks.main(["planning", "--directory", str(candidate)]) == 1
