"""Exercise independent Evidence Package validation on relocated disposable copies."""

import argparse
import json
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from google.protobuf.json_format import ParseError
from jsonschema import ValidationError
from verify_evidence import document, read, require, sha256, verify


def _write(root: Path, name: str, data: object) -> bytes:
    raw = (
        json.dumps(data, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n"
    ).encode("ascii")
    (root / name).write_bytes(raw)
    return raw


def _edit(root: Path, name: str, data: dict, index: dict) -> None:
    """Refresh declared hashes so semantic controls cannot pass on a mere hash failure."""
    raw = _write(root, name, data)
    index["files"][name] = {"sha256": sha256(raw), "size": len(raw)}
    for group in ("production", "checks"):
        for descriptor in index[group]:
            if descriptor["name"] == name:
                descriptor["digest"]["sha256"] = sha256(raw)
    _write(root, "index.json", index)


def controls(package: Path, schemas: Path) -> dict:
    original = {
        path.relative_to(package).as_posix(): sha256(path.read_bytes())
        for path in package.rglob("*")
        if path.is_file()
    }
    results = []
    faults = (
        "artifact-corruption",
        "missing-reference",
        "wrong-predicate",
        "unrelated-check-subject",
        "invalid-test-result",
        "missing-builder",
        "unrelated-production-subject",
        "omitted-check",
    )
    with TemporaryDirectory(prefix="apmx-independent-controls-") as temporary:
        root = Path(temporary)
        relocated = root / "relocated"
        shutil.copytree(package, relocated)
        positive = verify(relocated, schemas, require_capability=True)
        results.append({"case": "relocated-without-original-path-resolution", "status": "passed"})
        for fault in faults:
            copy = root / fault
            shutil.copytree(relocated, copy)
            index = document(read(copy, "index.json"))
            check_name = index["checks"][0]["name"]
            production_name = "provenance.intoto.json"
            check = document(read(copy, check_name))
            production = document(read(copy, production_name))
            if fault == "artifact-corruption":
                target = copy / production["subject"][0]["name"]
                target.write_bytes(target.read_bytes() + b"changed")
            elif fault == "missing-reference":
                (copy / check["subject"][0]["name"]).unlink()
            elif fault == "wrong-predicate":
                check["predicateType"] = "https://example.invalid/unsupported"
                _edit(copy, check_name, check, index)
            elif fault == "unrelated-check-subject":
                check["subject"] = [index["inventory"]]
                _edit(copy, check_name, check, index)
            elif fault == "invalid-test-result":
                check["predicate"]["result"] = "SUCCESS"
                _edit(copy, check_name, check, index)
            elif fault == "missing-builder":
                production["predicate"]["runDetails"].pop("builder")
                _edit(copy, production_name, production, index)
            elif fault == "unrelated-production-subject":
                production["subject"] = [index["inventory"]]
                _edit(copy, production_name, production, index)
            else:
                index["checks"] = index["checks"][1:]
                del index["files"][check_name]
                (copy / check_name).unlink()
                _write(copy, "index.json", index)
            try:
                verify(copy, schemas, require_capability=True)
            except (OSError, ValueError, KeyError, TypeError, ParseError, ValidationError) as exc:
                results.append({"case": fault, "status": "rejected", "reason": str(exc)[:240]})
            else:
                raise AssertionError(f"Corrupt package was accepted: {fault}")
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--schemas", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(controls(args.package, args.schemas), indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
