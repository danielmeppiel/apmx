"""Exercise independent receipt validation on relocated disposable copies.

The receipt is a run directory holding ``receipt.intoto.jsonl``, ``outputs/``
and ``evidence/``. Each control corrupts one copy and requires the independent
verifier (loaded without APMX) to reject it. Semantic controls re-encode the
changed statement canonically, so they cannot pass on a mere hash failure.
"""

import argparse
import base64
import json
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from google.protobuf.json_format import ParseError
from jsonschema import ValidationError
from verify_evidence import BUNDLE, bundle_bytes, require, sha256, verify


def _statements(bundle: Path) -> list[dict]:
    return [
        json.loads(base64.b64decode(json.loads(line)["payload"]))
        for line in bundle.read_bytes().splitlines()
    ]


def _rewrite(bundle: Path, statements: list[dict]) -> None:
    bundle.chmod(0o600)
    bundle.write_bytes(bundle_bytes(statements))


def _fault(copy: Path, fault: str) -> None:
    bundle = copy / BUNDLE
    statements = _statements(bundle)
    tests = [n for n, item in enumerate(statements) if "test-result" in item["predicateType"]]
    if fault == "evidence-corruption":
        blob = next((copy / "evidence/sha256").iterdir())
        blob.chmod(0o600)
        blob.write_bytes(blob.read_bytes() + b"changed")
        return
    if fault == "noncanonical-line":
        lines = bundle.read_bytes().splitlines()
        lines[0] = json.dumps(json.loads(lines[0]), indent=1).replace("\n", "").encode()
        bundle.chmod(0o600)
        bundle.write_bytes(b"\n".join(lines) + b"\n")
        return
    if fault == "wrong-predicate":
        statements[tests[0]]["predicateType"] = "https://example.invalid/unsupported"
    elif fault == "invalid-test-result":
        statements[tests[0]]["predicate"]["result"] = "SUCCESS"
    elif fault == "missing-builder":
        statements[0]["predicate"]["runDetails"].pop("builder")
    elif fault == "unrelated-check-subject":
        statements[tests[0]]["subject"][0]["digest"]["sha256"] = "0" * 64
    elif fault == "unrelated-production-subject":
        statements[0]["subject"][0]["name"] = "elsewhere/" + statements[0]["subject"][0]["name"]
    elif fault == "definition-drift":
        stage = statements[0]["predicate"]["buildDefinition"]["internalParameters"]["stages"][0]
        stage["checks"] = stage["checks"][1:] + [{"name": "invented", "command": "true"}]
    else:
        del statements[tests[0]]
    _rewrite(bundle, statements)


def controls(package: Path, schemas: Path) -> dict:
    package = package.parent if package.is_file() else package
    original = {
        path.relative_to(package).as_posix(): sha256(path.read_bytes())
        for path in package.rglob("*")
        if path.is_file()
    }
    results = []
    faults = (
        "evidence-corruption",
        "noncanonical-line",
        "wrong-predicate",
        "unrelated-check-subject",
        "invalid-test-result",
        "missing-builder",
        "unrelated-production-subject",
        "definition-drift",
        "omitted-check",
    )
    with TemporaryDirectory(prefix="apmx-independent-controls-") as temporary:
        root = Path(temporary)
        relocated = root / "relocated"
        relocated.mkdir()
        for name in (BUNDLE, "outputs", "evidence"):
            source = package / name
            if source.is_dir():
                shutil.copytree(source, relocated / name)
            else:
                shutil.copyfile(source, relocated / name)
        positive = verify(relocated, schemas, require_capability=True, require_evidence=True)
        results.append({"case": "relocated-without-original-path-resolution", "status": "passed"})
        for fault in faults:
            copy = root / fault
            shutil.copytree(relocated, copy)
            _fault(copy, fault)
            try:
                verify(copy, schemas, require_capability=True)
            except (OSError, ValueError, KeyError, TypeError, ParseError, ValidationError) as exc:
                results.append({"case": fault, "status": "rejected", "reason": str(exc)[:240]})
            else:
                raise AssertionError(f"Corrupt receipt was accepted: {fault}")
    require("apmx" not in sys.modules, "The independent consumer imported APMX.")
    after = {
        path.relative_to(package).as_posix(): sha256(path.read_bytes())
        for path in package.rglob("*")
        if path.is_file()
    }
    require(after == original, "Original evidence changed during disposable controls.")
    return {
        "positive": positive,
        "controls": results,
        "originalUnchanged": True,
        "apmxImported": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("package", type=Path, help="The run directory or its receipt.intoto.jsonl")
    parser.add_argument("--schemas", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(controls(args.package, args.schemas), indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
