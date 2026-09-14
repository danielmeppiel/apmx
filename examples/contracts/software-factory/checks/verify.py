"""Independent checks for one tiny task; the Python subset is not a sandbox."""

import argparse
import ast
import hashlib
import json
import math
import re
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

REQUIREMENTS = {"types", "range", "rates", "errors", "purity"}
PHASES = ("planning", "specification", "build", "test", "review")
BOUNDARIES = (
    -1,
    0,
    1,
    2,
    999,
    1000,
    1001,
    1999,
    2000,
    2001,
    2999,
    3000,
    3001,
    3999,
    4000,
    4001,
    4999,
    5000,
    5001,
    True,
    1.5,
    "1000",
    None,
)
MAX_BYTES = 32 * 1024


class Invalid(ValueError):
    """A candidate failed a stated condition, not an operational failure."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Invalid(message)


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON key.")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise Invalid(f"Non-JSON number: {value}.")


def decode(raw: bytes | str) -> Any:
    def finite(value: str) -> float:
        number = float(value)
        require(math.isfinite(number), "Nonfinite JSON number.")
        return number

    try:
        return json.loads(
            raw, object_pairs_hook=unique_object, parse_constant=reject_constant, parse_float=finite
        )
    except (ValueError, RecursionError) as exc:
        raise Invalid("Invalid JSON: " + str(exc)) from exc


def read_bytes(path: Path) -> bytes:
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode), "Expected a regular file.")
    require(info.st_size <= MAX_BYTES, "File exceeds the example's 32 KiB limit.")
    with path.open("rb") as source:
        raw = source.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES, "File exceeds the example's 32 KiB limit.")
    return raw


def document(directory: Path, name: str) -> Any:
    return decode(read_bytes(directory / name))


def fields(value: Any, expected: set[str]) -> dict[str, Any]:
    require(type(value) is dict and set(value) == expected, f"Expected fields: {sorted(expected)}.")
    return value


def text(value: Any) -> None:
    require(
        type(value) is str and 1 <= len(value.strip()) <= 800, "Expected bounded nonempty text."
    )


def texts(value: Any) -> None:
    require(type(value) is list and 1 <= len(value) <= 8, "Expected 1-8 text entries.")
    for item in value:
        text(item)


def identifiers(value: Any) -> set[str]:
    require(type(value) is list and 1 <= len(value) <= 5, "Expected requirement IDs.")
    require(all(type(item) is str for item in value), "Requirement IDs must be strings.")
    require(len(set(value)) == len(value) and set(value) <= REQUIREMENTS, "Unknown/repeated ID.")
    return set(value)


def oracle(value: Any) -> dict[str, Any]:
    """The single task oracle, independent of generated code/specification/tests."""
    if type(value) is not int:
        return {"raises": "TypeError"}
    if not 1 <= value <= 5000:
        return {"raises": "ValueError"}
    return {"returns": 500 + 200 * ((value - 1) // 1000)}


def observe(function: Callable[[Any], Any], value: Any) -> dict[str, Any]:
    """Observe only expected task exceptions; unexpected errors remain failures."""
    try:
        answer = function(value)
    except (TypeError, ValueError) as exc:
        return {"raises": type(exc).__name__}
    require(type(answer) is int, "The function must return an integer number of cents.")
    return {"returns": answer}


def validate_cases(value: Any, required: tuple[Any, ...]) -> list[dict[str, Any]]:
    require(type(value) is list and 1 <= len(value) <= 32, "Expected 1-32 cases.")
    names: set[str] = set()
    inputs: set[str] = set()
    for case in value:
        fields(case, {"id", "input", "expected", "requirements", "reason"})
        name = case["id"]
        require(
            type(name) is str and re.fullmatch(r"[a-z][a-z0-9-]{0,39}", name) is not None,
            "Case IDs must be short lowercase identifiers.",
        )
        require(name not in names, "Duplicate case ID.")
        names.add(name)
        item = case["input"]
        require(type(item) in (int, float, str, bool, type(None)), "Use scalar JSON inputs.")
        if type(item) in (int, float):
            require(
                abs(item) <= 1_000_000 and (type(item) is int or math.isfinite(item)),
                "Numeric input out of bounds.",
            )
        if type(item) is str:
            require(len(item) <= 64, "String input too long.")
        expected = case["expected"]
        require(type(expected) is dict and len(expected) == 1, "Expected one result or exception.")
        if "returns" in expected:
            require(type(expected["returns"]) is int, "Expected returns must be integers.")
        else:
            require(
                set(expected) == {"raises"} and expected["raises"] in ("TypeError", "ValueError"),
                "Expected exception must be TypeError or ValueError.",
            )
        require(expected == oracle(item), f"Case {name} disagrees with the independent oracle.")
        identifiers(case["requirements"])
        text(case["reason"])
        inputs.add(json.dumps(item))
    require(
        {json.dumps(item) for item in required} <= inputs, "Required boundary/type cases missing."
    )
    return value


def check_plan(directory: Path) -> None:
    plan = fields(document(directory, "plan.json"), {"goal", "steps", "risks", "validation"})
    text(plan["goal"])
    text(plan["validation"])
    texts(plan["risks"])
    require(
        type(plan["steps"]) is list and len(plan["steps"]) == 4, "Plan needs four ordered steps."
    )
    covered: set[str] = set()
    ids: set[str] = set()
    for step, phase in zip(plan["steps"], PHASES[1:], strict=True):
        fields(step, {"id", "phase", "action", "requirements"})
        text(step["id"])
        require(step["id"] not in ids, "Repeated step ID.")
        ids.add(step["id"])
        require(step["phase"] == phase, "Plan phase order differs from the fixed pipeline.")
        text(step["action"])
        covered |= identifiers(step["requirements"])
    require(covered == REQUIREMENTS, "Plan must cover all five requirements.")


def check_spec(directory: Path) -> None:
    spec = fields(
        document(directory, "spec.json"),
        {
            "function",
            "parameter",
            "returns",
            "accepted_type",
            "range",
            "tiers",
            "errors",
            "requirements",
            "cases",
        },
    )
    expected = {
        "function": "shipping_cost",
        "parameter": "weight_grams",
        "returns": "integer cents",
        "accepted_type": "int excluding bool",
        "range": [1, 5000],
        "tiers": [
            {"through": upper, "cents": oracle(upper)["returns"]}
            for upper in range(1000, 5001, 1000)
        ],
        "errors": {"wrong_type": "TypeError", "out_of_range": "ValueError"},
    }
    for key, value in expected.items():
        require(
            json.dumps(spec[key], sort_keys=True) == json.dumps(value, sort_keys=True),
            f"Specification field {key} differs from the fixed task.",
        )
    require(identifiers(spec["requirements"]) == REQUIREMENTS, "Specification coverage incomplete.")
    validate_cases(spec["cases"], (1, 1000, 1001, 5000, 0, 5001, True))


def load_function(directory: Path) -> Callable[[Any], Any]:
    """Validate a finite, branch-only AST before compiling the one fixed function."""
    raw = read_bytes(directory / "shipping.py")
    require(len(raw) <= 8192 and raw.isascii(), "Source must be ASCII and at most 8 KiB.")
    try:
        tree = ast.parse(raw)
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise Invalid("Invalid Python source.") from exc
    nodes = list(ast.walk(tree))
    require(len(nodes) <= 256, "Too many AST nodes.")
    allowed = (
        ast.Module,
        ast.FunctionDef,
        ast.arguments,
        ast.arg,
        ast.If,
        ast.Return,
        ast.Raise,
        ast.Expr,
        ast.Constant,
        ast.Name,
        ast.Load,
        ast.Compare,
        ast.BoolOp,
        ast.And,
        ast.Or,
        ast.Is,
        ast.IsNot,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.Call,
    )
    require(all(isinstance(node, allowed) for node in nodes), "Unsupported Python syntax.")
    body = list(tree.body)
    if body and isinstance(body[0], ast.Expr):
        body.pop(0)
    require(len(body) == 1 and isinstance(body[0], ast.FunctionDef), "Define one function only.")
    function = body[0]
    require(
        function.name == "shipping_cost"
        and not function.decorator_list
        and function.returns is None
        and not function.type_params,
        "Wrong function declaration.",
    )
    args = function.args
    require(
        len(args.args) == 1
        and args.args[0].arg == "weight_grams"
        and args.args[0].annotation is None
        and not args.posonlyargs
        and not args.kwonlyargs
        and args.vararg is None
        and args.kwarg is None
        and not args.defaults,
        "Use exactly shipping_cost(weight_grams), without annotations/defaults.",
    )
    for node in nodes:
        if isinstance(node, ast.FunctionDef):
            require(node is function, "Nested definitions are unsupported.")
        elif isinstance(node, ast.Name):
            require(
                node.id in {"weight_grams", "type", "int", "TypeError", "ValueError"},
                "Unsupported name.",
            )
        elif isinstance(node, ast.Constant):
            require(
                (type(node.value) is int and 0 <= node.value <= 1_000_000)
                or (type(node.value) is str and len(node.value) <= 400),
                "Unsupported literal.",
            )
        elif isinstance(node, ast.Expr):
            require(
                isinstance(node.value, ast.Constant) and type(node.value.value) is str,
                "Only docstrings may be expression statements.",
            )
        elif isinstance(node, ast.Return):
            require(
                isinstance(node.value, ast.Constant) and type(node.value.value) is int,
                "Return literal integer cents.",
            )
        elif isinstance(node, ast.Raise):
            target = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            require(
                isinstance(target, ast.Name)
                and target.id in {"TypeError", "ValueError"}
                and node.cause is None,
                "Raise only TypeError or ValueError.",
            )
        elif isinstance(node, ast.Call):
            require(isinstance(node.func, ast.Name) and not node.keywords, "Unsupported call.")
            if node.func.id == "type":
                require(
                    len(node.args) == 1
                    and isinstance(node.args[0], ast.Name)
                    and node.args[0].id == "weight_grams",
                    "Only type(weight_grams) is allowed.",
                )
            else:
                require(
                    node.func.id in {"TypeError", "ValueError"}
                    and len(node.args) <= 1
                    and all(
                        isinstance(arg, ast.Constant) and type(arg.value) is str
                        for arg in node.args
                    ),
                    "Only literal exception messages are allowed.",
                )
    namespace: dict[str, Any] = {
        "__builtins__": {},
        "type": type,
        "int": int,
        "TypeError": TypeError,
        "ValueError": ValueError,
    }
    exec(compile(tree, "shipping.py", "exec"), namespace)
    return namespace["shipping_cost"]


def check_build(directory: Path) -> None:
    function = load_function(directory)
    for value in (*range(1, 5001), *BOUNDARIES, False, 0.0, "", [], {}):
        require(observe(function, value) == oracle(value), f"Acceptance failed for {value!r}.")


def mutant(name: str, value: Any) -> dict[str, Any]:
    """Four fixed mistakes; generated cases must expose every one."""
    if name == "bool-as-int" and type(value) is bool:
        return oracle(int(value))
    if name == "tier-boundary" and type(value) is int and value in (1000, 2000, 3000, 4000):
        return oracle(value + 1)
    if name == "zero-accepted" and type(value) is int and value == 0:
        return oracle(1)
    if name == "maximum-excluded" and type(value) is int and value == 5000:
        return {"raises": "ValueError"}
    return oracle(value)


def check_tests(directory: Path) -> None:
    tests = fields(document(directory, "tests.json"), {"strategy", "cases"})
    text(tests["strategy"])
    cases = validate_cases(tests["cases"], BOUNDARIES)
    function = load_function(directory)
    for case in cases:
        require(
            observe(function, case["input"]) == case["expected"],
            f"Generated test {case['id']} failed on the captured implementation.",
        )
    for name in ("bool-as-int", "tier-boundary", "zero-accepted", "maximum-excluded"):
        require(
            any(mutant(name, case["input"]) != case["expected"] for case in cases),
            f"Generated cases do not expose mutant {name}.",
        )


def check_review(directory: Path) -> None:
    review = fields(
        document(directory, "review.json"),
        {
            "advisory",
            "assurance",
            "recommendation",
            "summary",
            "findings",
            "limitations",
            "follow_up",
        },
    )
    require(
        review["advisory"] is True and review["assurance"] == "UNPROVEN",
        "Review must be advisory and UNPROVEN.",
    )
    require(review["recommendation"] in ("no_findings", "follow_up"), "Unknown recommendation.")
    text(review["summary"])
    texts(review["limitations"])
    require(
        type(review["follow_up"]) is list and len(review["follow_up"]) <= 8, "Too many follow-ups."
    )
    for item in review["follow_up"]:
        text(item)
    require(type(review["findings"]) is list and len(review["findings"]) <= 8, "Too many findings.")
    for finding in review["findings"]:
        fields(finding, {"file", "line", "severity", "requirements", "detail"})
        require(
            finding["file"] in ("plan.json", "spec.json", "shipping.py", "tests.json"),
            "Finding refers to an unavailable file.",
        )
        lines = len(read_bytes(directory / finding["file"]).splitlines())
        require(
            type(finding["line"]) is int and 1 <= finding["line"] <= lines, "Invalid finding line."
        )
        require(finding["severity"] in ("low", "medium", "high"), "Invalid severity.")
        identifiers(finding["requirements"])
        text(finding["detail"])
    if review["recommendation"] == "no_findings":
        require(not review["findings"], "A no_findings report cannot contain findings.")
    else:
        require(
            bool(review["findings"] or review["follow_up"]), "Explain the recommended follow-up."
        )
    evidence = fields(document(directory, "evidence.json"), {"assurance", "stages"})
    require(
        evidence["assurance"] == "UNPROVEN"
        and type(evidence["stages"]) is list
        and len(evidence["stages"]) == 4,
        "Expected four admitted stages.",
    )
    for entry, phase, name in zip(
        evidence["stages"],
        PHASES[:4],
        (
            "plan.json",
            "spec.json",
            "shipping.py",
            "tests.json",
        ),
        strict=True,
    ):
        fields(entry, {"stage", "output", "sha256", "checks"})
        require(entry["stage"] == phase and entry["output"] == name, "Evidence stage mismatch.")
        require(
            entry["sha256"] == hashlib.sha256(read_bytes(directory / name)).hexdigest(),
            "Evidence does not describe the supplied artifact.",
        )
        require(
            entry["checks"] == [{"name": "contract", "normalized": 0, "raw_exit": 0}],
            "Evidence must state the actual passing check observations.",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=(*PHASES, "quote"))
    parser.add_argument("--directory", type=Path, default=Path.cwd())
    parser.add_argument("--weight", help="For quote: one JSON value, for example 1001 or true.")
    args = parser.parse_args(argv)
    try:
        if args.phase == "quote":
            require(args.weight is not None, "quote requires --weight JSON.")
            require(len(args.weight) <= 128, "Weight JSON is too large.")
            value = decode(args.weight)
            require(type(value) in (int, float, bool, str, type(None)), "Use a scalar weight.")
            check_build(args.directory)
            result = observe(load_function(args.directory), value)
            require(result == oracle(value), "Quote disagrees with the independent oracle.")
            print(json.dumps(result, ensure_ascii=True))
        else:
            request = document(args.directory, "request.json")
            require(
                type(request) is dict
                and request.get("task") == "shipping-cost"
                and type(request.get("requirements")) is dict
                and set(request["requirements"]) == REQUIREMENTS,
                "Invalid fixed request.",
            )
            checks = dict(
                zip(PHASES, (check_plan, check_spec, check_build, check_tests, check_review))
            )
            checks[args.phase](args.directory)
            print(f"{args.phase}: independent example checks passed; not certification.")
        return 0
    except Invalid as exc:
        print("Failed condition: " + str(exc).encode("ascii", "backslashreplace").decode("ascii"))
        return 1
    except OSError as exc:
        print("Incomplete check: " + str(exc).encode("ascii", "backslashreplace").decode("ascii"))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
