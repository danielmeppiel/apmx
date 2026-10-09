"""Validate an unsigned APMX receipt with pinned upstream tooling.

This module is the single owner of structural receipt verification. It imports
only the standard library and the pinned upstream in-toto, protobuf and
jsonschema packages, never other APMX modules, so that
``scripts/verify_evidence.py`` can load it by path and stay independent of APMX.

Checks run in report rows, each stopping at its first failure:

- Integrity: ``index.json`` hashes, exact file membership and descriptor links.
- Standards: in-toto Statement v1, SLSA Provenance v1, in-toto Test Result and
  CycloneDX 1.5 schema validity.
- Factory: definition, producer statements and capability bindings agree.
- Checks: every recorded check invocation is present and consistent.
- Outputs: production subjects bind exactly the delivered artifacts.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from google.protobuf.json_format import ParseDict, ParseError
from in_toto_attestation.predicates.provenance.v1.provenance_pb2 import Provenance
from in_toto_attestation.predicates.test_result.v0.test_result_pb2 import TestResult
from in_toto_attestation.v1.statement import Statement
from in_toto_attestation.v1.statement_pb2 import Statement as StatementProto
from jsonschema import FormatChecker, ValidationError, validators
from referencing import Registry, Resource

STATEMENT = "https://in-toto.io/Statement/v1"
PROVENANCE = "https://slsa.dev/provenance/v1"
TEST_RESULT = "https://in-toto.io/attestation/test-result/v0.1"
BUILD_TYPE = "https://github.com/danielmeppiel/apmx/build/contract/v1"
SCHEMA_COMMIT = "1ce97b2a7b8cf2429da248560d2aa671c6bce74a"
SCHEMAS = {
    "bom-1.5.schema.json": "2d956c1d05c092695457a91f3b5c57c749793c013ec224a0935807cfc8ae4480",
    "spdx.schema.json": "4b345e2329f209f34e960ae2a8e7cb46a166907e6a45e94978565925dc47b359",
    "jsf-0.82.schema.json": "8bae002c25e723db7ee1f26afde680ae1a2b1a8f6b4b4b0fd65dc3becb090aae",
}
BUNDLED_SCHEMAS = Path(__file__).resolve().parent / "schemas"
ROWS = ("Integrity", "Standards", "Factory", "Checks", "Outputs")
ASSURANCE = "Unsigned content binding and format compatibility, not authenticated attestation."
REPORTED_CHECKERS = {
    "checks/documents.py",
    "checks/acceptance.py",
    "checks/regression.py",
    "checks/documentation.py",
    "checks/documented_checkout.py",
}


class Failure(ValueError):
    """A verification failure attributed to the report row that found it."""

    def __init__(self, row: str, message: str) -> None:
        super().__init__(message)
        self.row = row


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def unique(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for name, value in pairs:
        require(name not in result, "Duplicate JSON field.")
        result[name] = value
    return result


def document(raw: bytes) -> object:
    return json.loads(
        raw,
        object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON value.")),
    )


def read(root: Path, name: str) -> bytes:
    relative = PurePosixPath(name)
    require(
        isinstance(name, str)
        and bool(name)
        and not relative.is_absolute()
        and relative.as_posix() == name
        and "\\" not in name
        and not any(part in ("", ".", "..") for part in name.split("/"))
        and ":" not in name,
        "A reference is not package-relative.",
    )
    path = root
    for part in relative.parts:
        path = path / part
        require(not path.is_symlink(), "Symlink reference refused.")
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_size <= 16 * 1024 * 1024, "Unbounded file.")
    raw = path.read_bytes()
    require(len(raw) == info.st_size, "File changed while reading.")
    return raw


def schema_validator(root: Path | None = None):
    """CycloneDX 1.5 validator from the hash-pinned schemas; bundled unless overridden."""
    root = BUNDLED_SCHEMAS if root is None else root
    resources = []
    documents = {}
    for name, expected in SCHEMAS.items():
        raw = read(root, name)
        require(sha256(raw) == expected, f"Published schema hash differs: {name}")
        data = document(raw)
        documents[name] = data
        resources.append((data["$id"], Resource.from_contents(data)))
    schema = documents["bom-1.5.schema.json"]
    validator = validators.validator_for(schema)
    validator.check_schema(schema)
    return validator(
        schema, registry=Registry().with_resources(resources), format_checker=FormatChecker()
    )


def descriptor(root: Path, item: dict) -> bytes:
    require(isinstance(item, dict), "Invalid resource descriptor.")
    require(set(item.get("digest", {})) == {"sha256"}, "Expected exact SHA-256 descriptor.")
    require(re.fullmatch(r"[0-9a-f]{64}", item["digest"]["sha256"]) is not None, "Invalid digest.")
    raw = read(root, item["name"])
    require(sha256(raw) == item["digest"]["sha256"], f"Descriptor hash differs: {item['name']}")
    return raw


def _artifacts(index: dict, run_id: str) -> dict[str, str]:
    prefix = f"attempts/{run_id}/artifacts/"
    return {
        name: identity["sha256"]
        for name, identity in index["files"].items()
        if name.startswith(prefix)
    }


def _subjects(statement: dict) -> dict[str, str]:
    subjects = statement["subject"]
    require(
        len({item["name"] for item in subjects}) == len(subjects), "Duplicate statement subjects."
    )
    return {item["name"]: item["digest"]["sha256"] for item in subjects}


def inventory_hash(files: dict) -> str:
    return sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    )


def verify_tree(root: Path, prefix: str, files: dict) -> None:
    require(isinstance(files, dict), "Missing file inventory.")
    for name, identity in files.items():
        raw = read(root, prefix + "/" + name)
        require(
            sha256(raw) == identity["sha256"] and len(raw) == identity["size"], "Inventory differs."
        )


@contextmanager
def _row(name: str) -> Iterator[None]:
    """Attribute any malformed or failing input to the row being evaluated."""
    try:
        yield
    except Failure:
        raise
    except ValidationError as exc:
        raise Failure(name, f"CycloneDX 1.5 schema violation: {exc.message}") from exc
    except ParseError as exc:
        raise Failure(name, f"Not a valid in-toto/SLSA protobuf document: {exc}") from exc
    except KeyError as exc:
        raise Failure(name, f"Malformed receipt: missing field {exc}.") from exc
    except (TypeError, AttributeError, IndexError) as exc:
        raise Failure(name, "Malformed receipt: unexpected document structure.") from exc
    except OSError as exc:
        raise Failure(name, f"Receipt file unreadable: {exc.strerror or exc}.") from exc
    except (ValueError, RecursionError) as exc:
        raise Failure(name, str(exc) or "Invalid receipt content.") from exc


@dataclass
class Receipt:
    """Structural facts established row by row; later rows rely on earlier ones."""

    root: Path
    index: dict = field(default_factory=dict)
    definition: dict = field(default_factory=dict)
    bom: dict = field(default_factory=dict)
    bindings: list = field(default_factory=list)
    statements: dict[str, dict] = field(default_factory=dict)
    executions: dict[str, dict] = field(default_factory=dict)
    selected_outputs: dict[str, str] = field(default_factory=dict)
    details: dict[str, str] = field(default_factory=dict)
    facts: dict[str, object] = field(default_factory=dict)


def _integrity(receipt: Receipt) -> None:
    root = receipt.root
    index = document(read(root, "index.json"))
    require(isinstance(index, dict), "index.json is not an object.")
    require(index["schema"] == "apmx-evidence-package/1", "Unsupported receipt profile.")
    require(isinstance(index["files"], dict) and bool(index["files"]), "Empty receipt inventory.")
    for name, expected in index["files"].items():
        raw = read(root, name)
        require(
            sha256(raw) == expected["sha256"] and len(raw) == expected["size"],
            f"File hash mismatch: {name}",
        )
    actual = set()
    for count, path in enumerate(root.rglob("*"), 1):
        require(
            count <= 100_000 and not path.is_symlink(), "Unbounded or symlink-containing receipt."
        )
        if not path.is_dir():
            actual.add(path.relative_to(root).as_posix())
    require(actual == set(index["files"]) | {"index.json"}, "Unindexed or missing files.")
    descriptor(root, index["definition"])
    descriptor(root, index["inventory"])
    refs = index["production"] + index["checks"]
    names = [ref["name"] for ref in refs]
    require(len(names) == len(set(names)), "Duplicate statement references.")
    require(
        set(names) | {"provenance.intoto.json"}
        == {n for n in index["files"] if n.endswith(".intoto.json")},
        "Statement index is incomplete.",
    )
    for ref in refs:
        descriptor(root, ref)
    attempts = index["attempts"]
    require(len({item["runId"] for item in attempts}) == len(attempts), "Duplicate attempts.")
    for attempt in attempts:
        require(
            f"attempts/{attempt['runId']}/execution.json" in index["files"],
            "Attempt execution projection is not indexed.",
        )
    receipt.index = index
    receipt.details["Integrity"] = f"all {len(index['files'])} files match index.json (SHA-256)"


def _standards(receipt: Receipt, schemas: Path | None) -> None:
    root, index = receipt.root, receipt.index
    bom = document(descriptor(root, index["inventory"]))
    schema_validator(schemas).validate(bom)
    require(
        bom.get("bomFormat") == "CycloneDX" and bom.get("specVersion") == "1.5",
        "Unexpected CycloneDX version.",
    )
    names = [ref["name"] for ref in index["production"] + index["checks"]]
    provenance = tests = 0
    for name in [*names, "provenance.intoto.json"]:
        statement = document(read(root, name))
        proto = ParseDict(statement, StatementProto(), ignore_unknown_fields=True)
        Statement.copy_from_pb(proto).validate()
        require(
            statement["_type"] == STATEMENT and bool(statement["subject"]),
            f"Invalid in-toto Statement v1 envelope: {name}",
        )
        predicate = statement["predicate"]
        if statement["predicateType"] == PROVENANCE:
            ParseDict(predicate, Provenance(), ignore_unknown_fields=True)
            provenance += 1
        else:
            require(
                statement["predicateType"] == TEST_RESULT, f"Unsupported predicate type: {name}"
            )
            ParseDict(predicate, TestResult(), ignore_unknown_fields=True)
            require(
                predicate.get("result") in {"PASSED", "FAILED", "WARNED"},
                f"Invalid Test Result value: {name}",
            )
            tests += 1
        receipt.statements[name] = statement
    receipt.bom = bom
    receipt.facts.update(provenanceStatements=provenance, testResultStatements=tests)
    receipt.details["Standards"] = (
        "in-toto Statement v1, SLSA Provenance v1, CycloneDX 1.5: schema-valid"
    )


def _bind_capabilities(receipt: Receipt, require_capability: bool) -> None:
    root, definition, bom = receipt.root, receipt.definition, receipt.bom
    bindings = document(read(root, "capability-bindings.json"))
    require(isinstance(bindings, list), "Invalid capability bindings.")
    require(not require_capability or bool(bindings), "No real capability binding demonstrated.")
    capabilities = [cap for stage in definition["stages"] for cap in stage["capabilities"]]
    require(
        {(cap["name"], cap["sha256"]) for cap in capabilities}
        == {(binding["capability"], binding["bodySha256"]) for binding in bindings},
        "Capability binding omitted or invented.",
    )
    for binding in bindings:
        matches = [
            item
            for item in bom.get("components", [])
            if item.get("purl") == binding["purl"] and item.get("bom-ref") == binding["bomRef"]
        ]
        require(len(matches) == 1, "Capability ABOM binding missing or ambiguous.")
        parsed = urlparse(binding["purl"])
        pins = tuple(
            pin
            for pin in (binding["commit"], binding["packageHash"], binding.get("registryHash"))
            if pin is not None
        )
        require(
            parsed.scheme == "pkg"
            and not (parsed.netloc or parsed.query or parsed.fragment)
            and (parsed.path.rpartition("@")[2] in pins if pins else "@" not in parsed.path),
            "Capability inventory pin mismatch.",
        )
        require(
            any(
                cap["name"] == binding["capability"]
                and cap["sha256"] == binding["bodySha256"]
                and cap["verified_package_hash"] == binding["packageHash"]
                and cap["resolved_commit"] == binding["commit"]
                and cap["resources"] == binding["resources"]
                for cap in capabilities
            ),
            "Selected capability differs from the definition.",
        )
    receipt.bindings = bindings


def _producer(receipt: Receipt, name: str, statement: dict) -> None:
    root, index, definition = receipt.root, receipt.index, receipt.definition
    build = statement["predicate"]["buildDefinition"]
    details = statement["predicate"]["runDetails"]
    require(build.get("buildType") == BUILD_TYPE, "Unsupported build-type mapping.")
    require(bool(details.get("builder", {}).get("id")), "Missing SLSA builder.")
    require(
        build["externalParameters"]["definition"] == index["definition"],
        "Production definition drift.",
    )
    for item in build["resolvedDependencies"] + details.get("byproducts", []):
        descriptor(root, item)
    require(
        index["inventory"] in details.get("byproducts", []),
        "ABOM is not bound as an inventory byproduct.",
    )
    require(
        index["inventory"] not in build["resolvedDependencies"],
        "ABOM misrepresented as a model input.",
    )
    if name == "provenance.intoto.json":
        return
    run_id = name.removeprefix("provenance/").removesuffix(".intoto.json")
    contract_digest = sha256(read(root, f"attempts/{run_id}/source/contract.contract.md"))
    require(
        build["externalParameters"]["contractSha256"] == contract_digest,
        "Producer contract drift.",
    )
    stages = [stage for stage in definition["stages"] if stage["contractSha256"] == contract_digest]
    require(len(stages) == 1, "Definition does not identify the producer contract.")
    stage = stages[0]
    observation = receipt.executions[run_id]
    require(
        stage["checks"]
        == [{"name": c["name"], "command": c["command"]} for c in observation["checks"]],
        "Recorded checks differ from the definition.",
    )
    require(
        set(stage["produces"])
        == {
            item.removeprefix(f"attempts/{run_id}/artifacts/") for item in _artifacts(index, run_id)
        },
        "Definition output mismatch.",
    )
    for resource in stage["resources"]:
        raw = read(root, f"attempts/{run_id}/baseline/{resource['relative_path']}")
        require(
            sha256(raw) == resource["sha256"] and len(raw) == resource["size"],
            "Definition checker resource mismatch.",
        )
    for capability in stage["capabilities"]:
        prefix = f"attempts/{run_id}/baseline/.agents/skills/{capability['name']}"
        require(
            sha256(read(root, prefix + "/SKILL.md")) == capability["sha256"],
            "Capability body mismatch.",
        )
        for resource in capability["resources"]:
            raw = read(root, prefix + "/" + resource["path"])
            require(
                sha256(raw) == resource["sha256"] and len(raw) == resource["size"],
                "Capability resource mismatch.",
            )


def _factory(receipt: Receipt, require_capability: bool) -> None:
    root, index = receipt.root, receipt.index
    definition = document(descriptor(root, index["definition"]))
    require(
        isinstance(definition, dict) and definition["schema"] == "apmx-definition/1",
        "Unsupported definition.",
    )
    receipt.definition = definition
    _bind_capabilities(receipt, require_capability)
    expected_production = set()
    for attempt in index["attempts"]:
        run_id = attempt["runId"]
        observation = document(read(root, f"attempts/{run_id}/execution.json"))
        require(
            observation["runId"] == run_id and observation["outcome"] == attempt["outcome"],
            "Attempt outcome drift.",
        )
        require(
            attempt["outcome"] in ("COMPLETE", "REJECTED"), "Unfinished attempt is not eligible."
        )
        require(type(attempt["selected"]) is bool, "Invalid selection.")
        if attempt["selected"]:
            require(attempt["outcome"] == "COMPLETE", "Rejected attempt selected as complete.")
        receipt.executions[run_id] = observation
        expected_production.add(f"provenance/{run_id}.intoto.json")
    require(
        expected_production == {ref["name"] for ref in index["production"]},
        "Producer invocation omitted or invented.",
    )
    for name, statement in receipt.statements.items():
        if statement["predicateType"] == PROVENANCE:
            _producer(receipt, name, statement)
    stages = definition["stages"]
    checks = sum(len(stage["checks"]) for stage in stages)
    digest = index["definition"]["digest"]["sha256"]
    receipt.facts.update(
        definitionSha256=digest,
        contracts=len(stages),
        declaredChecks=checks,
        capabilities=len(receipt.bindings),
        apmDependencies=len(definition.get("resolvedDependencies", [])),
    )
    receipt.details["Factory"] = (
        f"definition sha256:{digest[:12]}..  {len(stages)} "
        f"contract{'s' if len(stages) != 1 else ''}, {checks} check{'s' if checks != 1 else ''}"
    )


def _check_statement(root: Path, name: str, statement: dict, index: dict) -> None:
    predicate = statement["predicate"]
    require(bool(predicate.get("configuration")), "Missing Test Result configuration.")
    configurations = [document(descriptor(root, ref)) for ref in predicate["configuration"]]
    require(len(configurations) == 1, "Expected one recorded invocation.")
    config = configurations[0]
    require(config["definition"] == index["definition"], "Checker definition drift.")
    for ref in config["materials"]:
        descriptor(root, ref)
    stem = name.removeprefix("checks/").removesuffix(".intoto.json")
    run_id, ordinal = stem.rsplit("-", 1)
    execution = document(read(root, f"attempts/{run_id}/execution.json"))
    check = execution["checks"][int(ordinal) - 1]
    require(
        config["name"] == check["name"] and config["command"] == check["command"],
        "Check configuration drift.",
    )
    expected = "PASSED" if check["normalized"] == 0 else "FAILED"
    require(
        check["normalized"] in (0, 1)
        and type(check["normalized"]) is int
        and check["returncode"] == check["normalized"]
        and check["cleanupConfirmed"] is True
        and predicate["result"] == expected,
        "Unsupported or incomplete checker claim.",
    )
    require(
        not (
            expected == "PASSED" and (predicate.get("failedTests") or predicate.get("warnedTests"))
        ),
        "Passed check contradicts failures.",
    )
    for item in statement["subject"]:
        descriptor(root, item)
    report_name = name.removesuffix(".intoto.json") + ".report.json"
    if not (root / report_name).exists():
        require(
            not set(shlex.split(config["command"])) & REPORTED_CHECKERS,
            "Required checker report omitted.",
        )
        require(
            not any(predicate.get(key) for key in ("passedTests", "failedTests", "warnedTests")),
            "Unreported test cases.",
        )
        require(_subjects(statement) == _artifacts(index, run_id), "Generic checker subject drift.")
        return
    report = document(read(root, report_name))
    require(
        report["status"] == ("passed" if expected == "PASSED" else "failed"), "Report result drift."
    )
    if report["schema"] == "software-factory-document/2":
        require(len(statement["subject"]) == 1, "Document subject is ambiguous.")
        require(
            statement["subject"][0]["digest"]["sha256"] == report["sha256"],
            "Document subject mismatch.",
        )
        require(
            statement["subject"][0]["name"] in _artifacts(index, run_id),
            "Document is not this attempt's artifact.",
        )
        for reference, identity in report.get("references", {}).items():
            for family in ("baseline", "artifacts"):
                path = f"attempts/{run_id}/{family}/{reference}"
                if (root / path).exists():
                    raw = read(root, path)
                    require(
                        sha256(raw) == identity["sha256"] and len(raw) == identity["size"],
                        "Document reference drift.",
                    )
                    break
            else:
                raise ValueError("Document reference unavailable.")
    else:
        require(report["schema"] == "software-factory-check/1", "Unsupported report profile.")
        subject = report["subject"]
        require(inventory_hash(subject["base_files"]) == subject["base"], "Base digest drift.")
        require(
            inventory_hash(subject["candidate_files"]) == subject["candidate"],
            "Candidate digest drift.",
        )
        verify_tree(root, f"attempts/{run_id}/baseline", subject["base_files"])
        verify_tree(root, f"candidates/{subject['candidate']}", subject["candidate_files"])
        resources = {
            name.removeprefix(f"attempts/{run_id}/baseline/"): identity
            for name, identity in index["files"].items()
            if name.startswith(f"attempts/{run_id}/baseline/checks/")
        }
        require(inventory_hash(resources) == subject["checks"], "Report checker-resource drift.")
        actual = {ref["name"]: ref["digest"]["sha256"] for ref in statement["subject"]}
        wanted = {
            f"candidates/{subject['candidate']}/{n}": i["sha256"]
            for n, i in subject["candidate_files"].items()
        }
        require(actual == wanted, "Actual tested subject differs from the report.")
        for field_name, file in (
            ("patch", "changes.diff"),
            ("documentation_patch", "documentation.diff"),
        ):
            if field_name not in subject:
                continue
            hashes = [
                sha256(read(root, f"attempts/{run_id}/{family}/{file}"))
                for family in ("baseline", "artifacts")
                if (root / f"attempts/{run_id}/{family}/{file}").exists()
            ]
            require(hashes == [subject[field_name]], "Patch binding is absent or ambiguous.")
    observed = {"passedTests": [], "failedTests": []}

    def cases(rows: list, prefix: str = "") -> None:
        for row in rows:
            key = {"passed": "passedTests", "failed": "failedTests"}[row["status"]]
            label = prefix + row["id"]
            observed[key].append(label)
            cases(row.get("observed", []), label + "/")

    cases(report.get("observed", []))
    for key, values in observed.items():
        require(
            predicate.get(key, []) == values, "Test case observations were invented or omitted."
        )


def _checks(receipt: Receipt) -> None:
    root, index = receipt.root, receipt.index
    expected = set()
    for run_id, observation in receipt.executions.items():
        expected.update(
            f"checks/{run_id}-{i + 1}.intoto.json" for i in range(len(observation["checks"]))
        )
    require(
        expected == {ref["name"] for ref in index["checks"]},
        "Checker invocation omitted or invented.",
    )
    selected = {item["runId"] for item in index["attempts"] if item["selected"]}
    passed = total = 0
    for ref in index["checks"]:
        name = ref["name"]
        statement = receipt.statements[name]
        _check_statement(root, name, statement, index)
        run_id = name.removeprefix("checks/").removesuffix(".intoto.json").rsplit("-", 1)[0]
        if run_id in selected:
            total += 1
            if statement["predicate"]["result"] == "PASSED":
                passed += 1
            else:
                raise ValueError(f"Completed attempt has a failing check: {name}")
    rejected = len(receipt.executions) - len(selected)
    receipt.facts.update(checksPassed=passed, checks=total, rejectedAttempts=rejected)
    detail = f"{passed}/{total} passed (in-toto test-result)"
    if rejected:
        detail += f"; {rejected} earlier rejected attempt{'s' if rejected != 1 else ''} retained"
    receipt.details["Checks"] = detail


def delivered_outputs(receipt: Receipt) -> dict[str, str]:
    """Selected artifact paths, relative to the delivered outputs, with their digests."""
    outputs: dict[str, str] = {}
    for name, digest in receipt.selected_outputs.items():
        relative = name.split("/artifacts/", 1)[1]
        require(
            outputs.get(relative, digest) == digest,
            f"Two contracts deliver different bytes as {relative}.",
        )
        outputs[relative] = digest
    return outputs


def _outputs(receipt: Receipt) -> None:
    index = receipt.index
    selected = {}
    for attempt in index["attempts"]:
        if attempt["selected"]:
            selected.update(_artifacts(index, attempt["runId"]))
    for name, statement in receipt.statements.items():
        if statement["predicateType"] != PROVENANCE:
            continue
        if name == "provenance.intoto.json":
            require(_subjects(statement) == selected, "Selected production subject drift.")
        else:
            run_id = name.removeprefix("provenance/").removesuffix(".intoto.json")
            require(
                _subjects(statement) == _artifacts(index, run_id),
                "Producer artifact subject drift.",
            )
        for item in statement["subject"]:
            descriptor(receipt.root, item)
    receipt.selected_outputs = selected
    names = sorted(delivered_outputs(receipt))
    receipt.facts["outputs"] = names
    receipt.details["Outputs"] = (
        f"{len(names)} file{'s' if len(names) != 1 else ''} bound: " + " ".join(names)
    )


def inspect(
    root: Path, schemas: Path | None = None, *, require_capability: bool = False
) -> tuple[Receipt, Failure | None]:
    """Evaluate rows in order; stop at the first failure and return what was established."""
    receipt = Receipt(Path(root))
    steps = (
        ("Integrity", lambda: _integrity(receipt)),
        ("Standards", lambda: _standards(receipt, schemas)),
        ("Factory", lambda: _factory(receipt, require_capability)),
        ("Checks", lambda: _checks(receipt)),
        ("Outputs", lambda: _outputs(receipt)),
    )
    for name, step in steps:
        try:
            with _row(name):
                step()
        except Failure as failure:
            return receipt, failure
    return receipt, None


def verify(root: Path, schemas: Path | None = None, *, require_capability: bool = False) -> dict:
    """Upstream syntax plus explicitly scoped normative/profile/byte-binding checks."""
    receipt, failure = inspect(root, schemas, require_capability=require_capability)
    if failure is not None:
        raise failure
    return {
        "status": "passed",
        "files": len(receipt.index["files"]),
        "statements": len(receipt.statements),
        "capabilities": len(receipt.bindings),
        "definitionSha256": receipt.index["definition"]["digest"]["sha256"],
        "assurance": ASSURANCE,
    }


def main(argv: list[str] | None = None) -> int:
    """Independent command-line verifier; validation is offline unless schemas are fetched."""
    import argparse
    import sys
    from urllib.request import urlopen

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("package", type=Path)
    parser.add_argument(
        "--schemas",
        type=Path,
        help="Pinned CycloneDX schema directory (default: the copy bundled with this verifier).",
    )
    parser.add_argument(
        "--fetch-schemas",
        action="store_true",
        help="Fetch three hash-pinned upstream schemas into --schemas; otherwise offline.",
    )
    parser.add_argument("--require-capability", action="store_true")
    args = parser.parse_args(argv)
    if args.schemas is None and (args.fetch_schemas or not BUNDLED_SCHEMAS.is_dir()):
        parser.error("--schemas is required here")
    try:
        if args.fetch_schemas:
            args.schemas.mkdir(parents=True, exist_ok=True)
            for name, expected in SCHEMAS.items():
                url = (
                    "https://raw.githubusercontent.com/CycloneDX/specification/"
                    f"{SCHEMA_COMMIT}/schema/{name}"
                )
                with urlopen(url, timeout=30) as response:
                    raw = response.read(2 * 1024 * 1024)
                require(sha256(raw) == expected, f"Downloaded schema hash differs: {name}")
                (args.schemas / name).write_bytes(raw)
        print(
            json.dumps(
                verify(args.package, args.schemas, require_capability=args.require_capability),
                sort_keys=True,
            )
        )
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        RecursionError,
        ParseError,
        ValidationError,
    ) as exc:
        print(
            json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=True), file=sys.stderr
        )
        return 1
