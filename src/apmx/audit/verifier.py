"""Validate an APMX receipt bundle (``receipt.intoto.jsonl``) with pinned upstream tooling.

This module is the single owner of the receipt format and of its structural
verification. It imports only the standard library and the pinned upstream
in-toto, protobuf and jsonschema packages, never other APMX modules, so that
``scripts/verify_evidence.py`` can load it by path and stay independent of APMX.

A receipt is one in-toto bundle: one DSSE envelope per line, each wrapping an
in-toto Statement v1 (``payloadType`` ``application/vnd.in-toto+json``).
Locally ``signatures`` is empty; in CI the same statements are signed with
Sigstore and stored in GitHub's attestation store. Lines, in order:

- one SLSA Provenance v1 for the delivery;
- one in-toto Test Result v0.1 per check of the final candidate;
- one CycloneDX 1.5 BOM (``https://cyclonedx.org/bom``), inline;
- optionally one SLSA Verification Summary v1 binding every other line by
  SHA-256 (``inputAttestations``).

Every statement has the same subjects: the delivered files. Bulky material is
kept out of the bundle in ``evidence/sha256/<hex>``, content-addressed and
optional: each blob present is checked against the digest that names it.

Checks run in report rows, each stopping at its first failure:

- Integrity: envelopes, line digests, the VSA's inputAttestations and evidence blobs.
- Standards: in-toto Statement v1, SLSA Provenance v1, in-toto Test Result,
  CycloneDX 1.5 and SLSA VSA v1 schema validity.
- Factory: build type, factory definition, stages and capability bindings agree.
- Checks: every declared check of the final candidate is present and PASSED.
- Outputs: every statement names the same delivered files; before-images,
  creations and deletions are consistent.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
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
PAYLOAD_TYPE = "application/vnd.in-toto+json"
PROVENANCE = "https://slsa.dev/provenance/v1"
TEST_RESULT = "https://in-toto.io/attestation/test-result/v0.1"
CYCLONEDX = "https://cyclonedx.org/bom"
VSA = "https://slsa.dev/verification_summary/v1"
# The attestation store's carrier for a whole bundle: {"lines": [statement, ...]}.
BUNDLE_TYPE = "https://github.com/danielmeppiel/apmx/attestation/receipt-bundle/v2"
PREDICATE_TYPES = (PROVENANCE, TEST_RESULT, CYCLONEDX, VSA)
BUILD_TYPE = "https://github.com/danielmeppiel/apmx/build/factory/v2"
BUNDLE = "receipt.intoto.jsonl"
OUTPUTS = "outputs"
EVIDENCE = "evidence"
SCHEMA_COMMIT = "1ce97b2a7b8cf2429da248560d2aa671c6bce74a"
SCHEMAS = {
    "bom-1.5.schema.json": "2d956c1d05c092695457a91f3b5c57c749793c013ec224a0935807cfc8ae4480",
    "spdx.schema.json": "4b345e2329f209f34e960ae2a8e7cb46a166907e6a45e94978565925dc47b359",
    "jsf-0.82.schema.json": "8bae002c25e723db7ee1f26afde680ae1a2b1a8f6b4b4b0fd65dc3becb090aae",
}
BUNDLED_SCHEMAS = Path(__file__).resolve().parent / "schemas"
ROWS = ("Integrity", "Standards", "Factory", "Checks", "Outputs")
ASSURANCE = "Unsigned content binding and format compatibility, not authenticated attestation."
MAX_BUNDLE = 16 * 1024 * 1024
MAX_LINES = 4096
MAX_FILE = 16 * 1024 * 1024
_HEX = re.compile(r"[0-9a-f]{64}")
_GIT = re.compile(r"[0-9a-f]{40}")
_STAGE_KEYS = {
    "contract",
    "contractSha256",
    "harness",
    "harnessVersion",
    "requestedModel",
    "observedModels",
    "attempts",
    "budget",
    "checks",
    "outputs",
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


# --------------------------------------------------------------------------- format


def canonical(document: object) -> bytes:
    """Sorted, compact, ASCII JSON without a final newline: the bundle's only encoding."""
    return json.dumps(
        document, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def plain(value: object) -> object:
    """JSON values as every JSON implementation round-trips them: integral floats become ints."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [plain(item) for item in value]
    return value


def statement(subjects: list[dict], predicate_type: str, predicate: dict) -> dict:
    """An in-toto Statement v1 with subjects in name order, in round-trip-stable values."""
    return plain(
        {
            "_type": STATEMENT,
            "subject": sorted(
                ({"name": item["name"], "digest": dict(item["digest"])} for item in subjects),
                key=lambda item: item["name"],
            ),
            "predicateType": predicate_type,
            "predicate": predicate,
        }
    )


def envelope_line(document: dict) -> bytes:
    """One unsigned DSSE envelope line (without its newline) wrapping ``document``."""
    payload = base64.b64encode(canonical(document)).decode("ascii")
    return canonical({"payload": payload, "payloadType": PAYLOAD_TYPE, "signatures": []})


def line_digest(document: dict) -> str:
    """SHA-256 of a statement's canonical bundle line: what a VSA's inputAttestations name."""
    return sha256(envelope_line(document))


def normalized(document: dict) -> dict:
    """A statement as signed elsewhere (for example by actions/attest), in bundle form."""
    require(isinstance(document, dict), "Statement is not an object.")
    require(
        set(document) == {"_type", "subject", "predicateType", "predicate"},
        "Statement has unexpected fields.",
    )
    for item in document["subject"]:
        require(
            isinstance(item, dict) and set(item) == {"name", "digest"},
            "Subject has unexpected fields.",
        )
    return statement(document["subject"], document["predicateType"], document["predicate"])


def blob_path(digest: str) -> str:
    return f"{EVIDENCE}/sha256/{digest}"


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


def safe_path(name: object) -> bool:
    """A relative POSIX path that cannot escape, name Git internals or receipt state."""
    if not isinstance(name, str) or not name or len(name) > 4096:
        return False
    parts = name.split("/")
    return (
        PurePosixPath(name).as_posix() == name
        and not name.startswith("/")
        and not any(ord(char) < 32 or char in '\\:"' for char in name)
        and not any(part in ("", ".", "..") for part in parts)
        and not any(part.casefold() in (".git", ".gitmodules") for part in parts)
        and parts[0].casefold() != ".apm"
    )


def read(root: Path, name: str, limit: int = MAX_FILE) -> bytes:
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
    require(stat.S_ISREG(info.st_mode) and info.st_size <= limit, "Unbounded file.")
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


def _digest(item: dict) -> str:
    require(isinstance(item, dict), "Invalid resource descriptor.")
    digest = item.get("digest")
    require(
        isinstance(digest, dict) and set(digest) == {"sha256"}, "Expected exact SHA-256 digest."
    )
    require(_HEX.fullmatch(str(digest["sha256"])) is not None, "Invalid SHA-256 digest.")
    return digest["sha256"]


def subjects_of(document: dict) -> dict[str, str]:
    subjects = document["subject"]
    require(isinstance(subjects, list) and bool(subjects), "Statement has no subjects.")
    result = {}
    for item in subjects:
        require(safe_path(item.get("name")), f"Unsafe subject path: {item.get('name')!r}")
        require(item["name"] not in result, "Duplicate statement subjects.")
        result[item["name"]] = _digest(item)
    return result


def role(item: dict) -> str | None:
    annotations = item.get("annotations")
    return annotations.get("apmx.role") if isinstance(annotations, dict) else None


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


# --------------------------------------------------------------------------- receipt


@dataclass
class Receipt:
    """Structural facts established row by row; later rows rely on earlier ones."""

    path: Path | None
    lines: list[bytes] = field(default_factory=list)
    evidence: Path | None = None
    statements: list[dict] = field(default_factory=list)
    digests: list[str] = field(default_factory=list)
    signatures: int = 0
    provenance: dict = field(default_factory=dict)
    tests: list[dict] = field(default_factory=list)
    bom: dict = field(default_factory=dict)
    vsa: dict | None = None
    definition: dict | None = None
    stages: list[dict] = field(default_factory=list)
    bindings: list[dict] = field(default_factory=list)
    apm_files: dict[str, bytes] = field(default_factory=dict)
    subjects: dict[str, str] = field(default_factory=dict)
    before: dict[str, str | None] = field(default_factory=dict)
    deleted: dict[str, str] = field(default_factory=dict)
    base: dict = field(default_factory=dict)
    details: dict[str, str] = field(default_factory=dict)
    facts: dict[str, object] = field(default_factory=dict)

    @property
    def definition_sha256(self) -> str:
        return self.provenance["predicate"]["buildDefinition"]["externalParameters"]["factory"][
            "definition"
        ]["sha256"]

    def blob(self, digest: str) -> bytes | None:
        """Evidence bytes named by ``digest``, or None when this receipt does not carry them."""
        if self.evidence is None:
            return None
        path = self.evidence / "sha256" / digest
        if not path.exists() and not path.is_symlink():
            return None
        raw = read(self.evidence, f"sha256/{digest}")
        require(sha256(raw) == digest, f"Evidence blob {digest[:12]}.. does not match its name.")
        return raw


def references(receipt: Receipt) -> list[str]:
    """Every evidence digest the bundle names (definition, outputs, logs, inputs, attempts)."""
    found: list[str] = []
    predicate = receipt.provenance["predicate"]
    build, details = predicate["buildDefinition"], predicate["runDetails"]
    found.append(receipt.definition_sha256)
    for item in build.get("resolvedDependencies", []):
        if role(item) in ("input", "before-image") and "sha256" in item.get("digest", {}):
            found.append(item["digest"]["sha256"])
    for item in details.get("byproducts", []):
        found.append(_digest(item))
    for stage in build["internalParameters"]["stages"]:
        found += [_digest(item) for item in stage["outputs"]]
    for test in receipt.tests:
        url = test["predicate"].get("url")
        if isinstance(url, str) and url.startswith(f"{EVIDENCE}/sha256/"):
            found.append(url.rsplit("/", 1)[1])
        found += [_digest(item) for item in test["predicate"]["configuration"]]
    return list(dict.fromkeys(found))


def _parse_lines(receipt: Receipt) -> None:
    require(0 < len(receipt.lines) <= MAX_LINES, "The bundle has no statements or too many.")
    for number, line in enumerate(receipt.lines, 1):
        envelope = document(line)
        require(
            isinstance(envelope, dict)
            and set(envelope) == {"payload", "payloadType", "signatures"},
            f"Line {number} is not a DSSE envelope.",
        )
        require(
            envelope["payloadType"] == PAYLOAD_TYPE, f"Line {number} has an unexpected payloadType."
        )
        require(isinstance(envelope["signatures"], list), f"Line {number}: invalid signatures.")
        try:
            payload = base64.b64decode(envelope["payload"], validate=True)
        except (binascii.Error, TypeError) as exc:
            raise ValueError(f"Line {number}: payload is not base64.") from exc
        content = document(payload)
        require(isinstance(content, dict), f"Line {number}: payload is not a statement.")
        require(
            canonical(content) == payload, f"Line {number}: payload is not canonical bundle JSON."
        )
        require(
            line == canonical(envelope), f"Line {number}: envelope is not canonical bundle JSON."
        )
        receipt.signatures += len(envelope["signatures"])
        receipt.statements.append(content)
        receipt.digests.append(line_digest(content))


def _integrity(receipt: Receipt) -> None:
    _parse_lines(receipt)
    kinds = [item.get("predicateType") for item in receipt.statements]
    require(kinds.count(PROVENANCE) == 1, "Expected exactly one SLSA provenance statement.")
    require(kinds.count(CYCLONEDX) == 1, "Expected exactly one CycloneDX statement.")
    require(kinds.count(VSA) <= 1, "More than one verification summary.")
    require(set(kinds) <= set(PREDICATE_TYPES), "Unsupported predicate type in the bundle.")
    receipt.provenance = receipt.statements[kinds.index(PROVENANCE)]
    receipt.bom = receipt.statements[kinds.index(CYCLONEDX)]["predicate"]
    receipt.tests = [item for item in receipt.statements if item["predicateType"] == TEST_RESULT]
    others = [d for d, kind in zip(receipt.digests, kinds, strict=True) if kind != VSA]
    detail = f"{len(receipt.statements)} statements; line digests computed"
    if VSA in kinds:
        receipt.vsa = receipt.statements[kinds.index(VSA)]
        named = [_digest(item) for item in receipt.vsa["predicate"]["inputAttestations"]]
        require(
            sorted(named) == sorted(others),
            "The verification summary's inputAttestations do not bind exactly the other lines.",
        )
        detail += "; the VSA binds every other line"
    receipt.facts["lineDigests"] = others
    if receipt.evidence is not None and receipt.evidence.is_dir():
        require(not receipt.evidence.is_symlink(), "Evidence directory is a symlink.")
        store = receipt.evidence / "sha256"
        for count, path in enumerate(store.iterdir() if store.is_dir() else (), 1):
            require(count <= 100_000, "Unbounded evidence store.")
            require(_HEX.fullmatch(path.name) is not None, f"Unexpected evidence file {path.name}.")
            receipt.blob(path.name)
    else:
        receipt.evidence = None
    wanted = references(receipt) if receipt.statements else []
    present = [digest for digest in wanted if receipt.blob(digest) is not None]
    receipt.facts["evidence"] = (
        "absent" if not present else "complete" if len(present) == len(wanted) else "partial"
    )
    receipt.facts["evidenceBlobs"] = len(present)
    detail += (
        f"; evidence {len(present)}/{len(wanted)} blobs verified"
        if receipt.evidence is not None
        else "; evidence not present (optional)"
    )
    receipt.details["Integrity"] = detail


_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")


def _summary(predicate: dict, number: int) -> None:
    """SLSA VSA v1, checked against the specification's shape.

    The pinned in-toto-attestation 0.9.3 binding declares ``verifiedLevels`` as
    a scalar, contrary to the specification (an array); 0.10.0 fixed it. The
    fields are therefore checked here rather than with that binding.
    """
    known = {
        "verifier",
        "timeVerified",
        "resourceUri",
        "policy",
        "inputAttestations",
        "verificationResult",
        "verifiedLevels",
        "dependencyLevels",
        "slsaVersion",
    }
    require(isinstance(predicate, dict) and set(predicate) <= known, "Unknown VSA field.")
    verifier = predicate.get("verifier")
    require(
        isinstance(verifier, dict)
        and set(verifier) <= {"id", "version"}
        and isinstance(verifier.get("id"), str)
        and bool(verifier["id"]),
        f"Line {number}: the verification summary names no verifier.",
    )
    require(
        isinstance(predicate.get("timeVerified"), str)
        and _TIME.fullmatch(predicate["timeVerified"]) is not None,
        f"Line {number}: invalid timeVerified.",
    )
    require(
        isinstance(predicate.get("resourceUri"), str) and bool(predicate["resourceUri"]),
        f"Line {number}: the verification summary names no resourceUri.",
    )
    policy = predicate.get("policy")
    require(
        isinstance(policy, dict)
        and set(policy) <= {"uri", "digest"}
        and isinstance(policy.get("uri"), str),
        f"Line {number}: invalid VSA policy.",
    )
    for item in [policy, *(predicate.get("inputAttestations") or [])]:
        require(isinstance(item, dict) and set(item) <= {"uri", "digest"}, "Invalid VSA reference.")
        _digest(item)
    require(isinstance(predicate.get("inputAttestations"), list), "VSA inputAttestations missing.")
    require(
        predicate.get("verificationResult") in {"PASSED", "FAILED"},
        f"Line {number}: invalid verificationResult.",
    )
    levels = predicate.get("verifiedLevels")
    require(
        isinstance(levels, list) and all(isinstance(level, str) for level in levels),
        f"Line {number}: verifiedLevels must be a list of strings.",
    )


def _standards(receipt: Receipt, schemas: Path | None) -> None:
    for number, content in enumerate(receipt.statements, 1):
        proto = ParseDict(content, StatementProto(), ignore_unknown_fields=False)
        Statement.copy_from_pb(proto).validate()
        require(
            content["_type"] == STATEMENT and bool(content["subject"]),
            f"Line {number}: invalid in-toto Statement v1.",
        )
        subjects_of(content)
        predicate = content["predicate"]
        kind = content["predicateType"]
        if kind == PROVENANCE:
            ParseDict(predicate, Provenance(), ignore_unknown_fields=False)
        elif kind == TEST_RESULT:
            ParseDict(predicate, TestResult(), ignore_unknown_fields=False)
            require(
                predicate.get("result") in {"PASSED", "FAILED", "WARNED"},
                f"Line {number}: invalid Test Result value.",
            )
            require(
                isinstance(predicate.get("configuration"), list) and predicate["configuration"],
                f"Line {number}: Test Result has no configuration.",
            )
        elif kind == CYCLONEDX:
            schema_validator(schemas).validate(predicate)
            require(
                predicate.get("bomFormat") == "CycloneDX" and predicate.get("specVersion") == "1.5",
                "Unexpected CycloneDX version.",
            )
        else:
            _summary(predicate, number)
    receipt.facts.update(
        provenanceStatements=1,
        testResultStatements=len(receipt.tests),
        signedSummary=receipt.vsa is not None,
    )
    receipt.details["Standards"] = (
        "in-toto Statement v1, SLSA Provenance v1, in-toto Test Result, CycloneDX 1.5"
        + (", SLSA VSA v1" if receipt.vsa is not None else "")
        + ": schema-valid"
    )


def _stage(stage: dict) -> None:
    require(isinstance(stage, dict) and _STAGE_KEYS <= set(stage), "Incomplete stage record.")
    require(_HEX.fullmatch(str(stage["contractSha256"])) is not None, "Invalid stage contract.")
    require(isinstance(stage["contract"], str) and stage["contract"], "Unnamed stage contract.")
    require(isinstance(stage["harness"], str) and stage["harness"], "Stage has no harness.")
    for key in ("harnessVersion", "requestedModel"):
        require(stage[key] is None or isinstance(stage[key], str), f"Invalid stage {key}.")
    require(
        isinstance(stage["observedModels"], list)
        and all(isinstance(model, str) for model in stage["observedModels"]),
        "Invalid stage observedModels.",
    )
    require(
        isinstance(stage["attempts"], int) and stage["attempts"] >= 1, "Invalid stage attempts."
    )
    require(
        isinstance(stage["checks"], list)
        and all(
            isinstance(check, dict) and set(check) == {"name", "command"}
            for check in stage["checks"]
        ),
        "Invalid stage checks.",
    )
    require(isinstance(stage["outputs"], list), "Invalid stage outputs.")
    for item in stage["outputs"]:
        require(safe_path(item.get("name")), "Unsafe stage output path.")
        _digest(item)


def _bind_capabilities(receipt: Receipt, require_capability: bool) -> None:
    dependencies = receipt.provenance["predicate"]["buildDefinition"]["resolvedDependencies"]
    bindings = []
    for item in dependencies:
        if role(item) != "capability":
            continue
        annotations = item["annotations"]
        binding = {
            "capability": item["name"],
            "bodySha256": _digest(item),
            "purl": item["uri"],
            "bomRef": annotations["bomRef"],
            "commit": annotations.get("commit"),
            "packageHash": annotations.get("packageHash"),
            "registryHash": annotations.get("registryHash"),
            "resources": annotations.get("resources", []),
        }
        matches = [
            component
            for component in receipt.bom.get("components", [])
            if component.get("purl") == binding["purl"]
            and component.get("bom-ref") == binding["bomRef"]
        ]
        require(len(matches) == 1, "Capability ABOM binding missing or ambiguous.")
        parsed = urlparse(binding["purl"])
        pins = tuple(
            pin
            for pin in (binding["commit"], binding["packageHash"], binding["registryHash"])
            if pin is not None
        )
        require(
            parsed.scheme == "pkg"
            and not (parsed.netloc or parsed.query or parsed.fragment)
            and (parsed.path.rpartition("@")[2] in pins if pins else "@" not in parsed.path),
            "Capability inventory pin mismatch.",
        )
        bindings.append(binding)
    require(not require_capability or bool(bindings), "No real capability binding demonstrated.")
    if receipt.definition is not None:
        capabilities = [
            cap for stage in receipt.definition["stages"] for cap in stage["capabilities"]
        ]
        require(
            {(cap["name"], cap["sha256"]) for cap in capabilities}
            == {(binding["capability"], binding["bodySha256"]) for binding in bindings},
            "Capability binding omitted or invented.",
        )
        for binding in bindings:
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


def _factory(receipt: Receipt, require_capability: bool) -> None:
    predicate = receipt.provenance["predicate"]
    build, details = predicate["buildDefinition"], predicate["runDetails"]
    require(build.get("buildType") == BUILD_TYPE, "Unsupported build type.")
    require(bool(details.get("builder", {}).get("id")), "Missing SLSA builder.")
    factory = build["externalParameters"]["factory"]
    require(
        isinstance(factory, dict) and {"package", "commit", "definition"} <= set(factory),
        "Incomplete externalParameters.factory.",
    )
    digest = _digest({"digest": factory["definition"]})
    stages = build["internalParameters"]["stages"]
    require(isinstance(stages, list) and bool(stages), "No stages recorded.")
    for stage in stages:
        _stage(stage)
    raw = receipt.blob(digest)
    if raw is not None:
        definition = document(raw)
        require(
            isinstance(definition, dict) and definition.get("schema") == "apmx-definition/1",
            "Unsupported definition.",
        )
        require(
            [item["contractSha256"] for item in definition["stages"]]
            == [stage["contractSha256"] for stage in stages],
            "Recorded stages differ from the factory definition.",
        )
        for item, stage in zip(definition["stages"], stages, strict=True):
            require(
                item["checks"] == stage["checks"], "Recorded checks differ from the definition."
            )
            require(
                set(item["produces"]) == {output["name"] for output in stage["outputs"]},
                "Definition output mismatch.",
            )
        receipt.definition = definition
    for item in build["resolvedDependencies"]:
        if role(item) in ("apm-manifest", "apm-lock", "apm-imports-manifest"):
            require("content" in item, "APM inventory input must be inline.")
            content = base64.b64decode(item["content"], validate=True)
            require(sha256(content) == _digest(item), "Inline APM input differs from its digest.")
            receipt.apm_files[item["name"]] = content
    if receipt.vsa is not None:
        policy = receipt.vsa["predicate"].get("policy") or {}
        require(
            (policy.get("digest") or {}).get("sha256") == digest,
            "The verification summary's policy is not this factory definition.",
        )
    _bind_capabilities(receipt, require_capability)
    receipt.stages = stages
    checks = sum(len(stage["checks"]) for stage in stages)
    receipt.facts.update(
        definitionSha256=digest,
        factoryPackage=factory["package"],
        factoryCommit=factory["commit"],
        contracts=len(stages),
        declaredChecks=checks,
        capabilities=len(receipt.bindings),
        apmDependencies=(
            len(receipt.definition.get("resolvedDependencies", []))
            if receipt.definition is not None
            else len(receipt.bom.get("components", []))
        ),
        definitionRetained=receipt.definition is not None,
    )
    package = f"  {factory['package']}" if factory["package"] else ""
    receipt.details["Factory"] = (
        f"definition sha256:{digest[:12]}..{package}  {len(stages)} "
        f"contract{'s' if len(stages) != 1 else ''}, {checks} check{'s' if checks != 1 else ''}"
    )


def _checks(receipt: Receipt) -> None:
    declared = [
        (stage["contract"], check["name"], check["command"])
        for stage in receipt.stages
        for check in stage["checks"]
    ]
    observed = []
    passed = 0
    for test in receipt.tests:
        predicate = test["predicate"]
        configuration = predicate["configuration"]
        labels = set()
        for item in configuration:
            _digest(item)
            annotations = item.get("annotations") or {}
            labels.add(
                (
                    annotations.get("stage"),
                    annotations.get("check"),
                    annotations.get("command"),
                    annotations.get("factoryDefinition"),
                )
            )
        require(len(labels) == 1, "Test Result configuration names more than one check.")
        stage, name, command, definition = labels.pop()
        require(
            definition == f"sha256:{receipt.definition_sha256}",
            "Check configuration names another factory definition.",
        )
        observed.append((stage, name, command))
        require(
            not (predicate["result"] == "PASSED" and predicate.get("failedTests")),
            "Passed check contradicts failures.",
        )
        require(
            predicate["result"] == "PASSED",
            f"Check {name} of the final candidate did not pass ({predicate['result']}).",
        )
        passed += 1
        url = predicate.get("url")
        if url is not None:
            require(
                isinstance(url, str)
                and url.startswith(f"{EVIDENCE}/sha256/")
                and _HEX.fullmatch(url.rsplit("/", 1)[1]) is not None,
                "Test Result url is not an evidence reference.",
            )
            raw = receipt.blob(url.rsplit("/", 1)[1])
            if raw is not None and raw.startswith(b"{"):
                report = document(raw)
                require(
                    isinstance(report, dict) and report.get("status") == "passed",
                    f"Check {name}: its raw report disagrees with PASSED.",
                )
    require(
        sorted(observed) == sorted(declared),
        "Checker invocation omitted or invented: Test Results differ from the declared checks.",
    )
    rejected = sum(
        1
        for item in receipt.provenance["predicate"]["runDetails"].get("byproducts", [])
        if role(item) == "rejected-attempt"
    )
    receipt.facts.update(checksPassed=passed, checks=len(declared), rejectedAttempts=rejected)
    detail = f"{passed}/{len(declared)} passed (in-toto test-result)"
    if rejected:
        detail += f"; {rejected} earlier rejected attempt{'s' if rejected != 1 else ''} retained"
    receipt.details["Checks"] = detail


def vsa_prefix(vsa: dict, subjects: dict[str, str]) -> str:
    """The repository directory a VSA places the delivered files under ('' for the root)."""
    names = subjects_of(vsa)
    require(len(names) == len(subjects), "The VSA names a different set of files.")
    prefixes = set()
    for name, digest in names.items():
        matches = [
            local
            for local, local_digest in subjects.items()
            if local_digest == digest and (name == local or name.endswith("/" + local))
        ]
        require(len(matches) >= 1, f"VSA subject {name} is not a delivered file.")
        prefixes.add(name[: len(name) - len(matches[0])])
    require(len(prefixes) == 1, "The VSA places delivered files under different directories.")
    prefix = prefixes.pop()
    require(
        {prefix + name: digest for name, digest in subjects.items()} == names,
        "The VSA subjects differ from the delivered files.",
    )
    return prefix


def _outputs(receipt: Receipt) -> None:
    subjects = subjects_of(receipt.provenance)
    for test in receipt.tests:
        require(subjects_of(test) == subjects, "A Test Result names different subjects.")
    bom = next(item for item in receipt.statements if item["predicateType"] == CYCLONEDX)
    require(subjects_of(bom) == subjects, "The CycloneDX statement names different subjects.")
    prefix = vsa_prefix(receipt.vsa, subjects) if receipt.vsa is not None else ""
    dependencies = receipt.provenance["predicate"]["buildDefinition"]["resolvedDependencies"]
    bases = [item for item in dependencies if role(item) == "base"]
    require(len(bases) == 1, "Expected exactly one base descriptor.")
    base = bases[0]
    digest = base.get("digest", {})
    require(
        set(digest) <= {"gitTree", "gitCommit"}
        and all(_GIT.fullmatch(str(value)) for value in digest.values()),
        "Invalid base descriptor.",
    )
    annotations = base.get("annotations", {})
    created = annotations.get("created", [])
    deleted = annotations.get("deleted", [])
    require(isinstance(created, list) and isinstance(deleted, list), "Invalid base annotations.")
    before: dict[str, str | None] = {}
    for item in dependencies:
        if role(item) == "before-image":
            require(safe_path(item["name"]) and item["name"] not in before, "Invalid before-image.")
            before[item["name"]] = _digest(item)
    for name in created:
        require(safe_path(name) and name not in before, "Invalid created path.")
        before[name] = None
    removed = {}
    for item in deleted:
        require(
            isinstance(item, dict)
            and set(item) == {"path", "beforeDigest"}
            and safe_path(item["path"])
            and item["path"] not in subjects
            and item["path"] not in removed,
            "Invalid deletion record.",
        )
        require(
            before.get(item["path"]) == item["beforeDigest"] and item["beforeDigest"] is not None,
            "A deletion has no matching before-image.",
        )
        removed[item["path"]] = item["beforeDigest"]
    require(
        set(subjects) | set(removed) == set(before),
        "Before-images must cover exactly the delivered and deleted files.",
    )
    receipt.subjects, receipt.before, receipt.deleted = subjects, before, removed
    receipt.base = {
        "uri": base.get("uri"),
        "gitCommit": digest.get("gitCommit"),
        "gitTree": digest.get("gitTree"),
    }
    names = sorted(subjects)
    receipt.facts.update(
        outputs=names, deleted=sorted(removed), base=receipt.base, subjectPrefix=prefix
    )
    detail = f"{len(names)} file{'s' if len(names) != 1 else ''} bound: " + " ".join(names[:6])
    if len(names) > 6:
        detail += f" (+{len(names) - 6} more)"
    if removed:
        detail += f"; {len(removed)} deleted"
    receipt.details["Outputs"] = detail


def delivered_outputs(receipt: Receipt) -> dict[str, str]:
    """Delivered file paths, relative to the factory workspace, with their digests."""
    return dict(receipt.subjects)


def load_lines(raw: bytes) -> list[bytes]:
    require(len(raw) <= MAX_BUNDLE, "The bundle is larger than 16 MiB.")
    require(raw.endswith(b"\n"), "The bundle does not end with a newline.")
    lines = raw[:-1].split(b"\n")
    require(all(lines), "The bundle has an empty line.")
    return lines


def bundle_bytes(statements: list[dict]) -> bytes:
    return b"".join(envelope_line(item) + b"\n" for item in statements)


def inspect_statements(
    statements: list[dict],
    *,
    evidence: Path | None = None,
    schemas: Path | None = None,
    require_capability: bool = False,
    path: Path | None = None,
) -> tuple[Receipt, Failure | None]:
    """Verify statements obtained elsewhere (for example from an attestation store)."""
    receipt = Receipt(path, [envelope_line(item) for item in statements], evidence)
    return _inspect(receipt, schemas, require_capability)


def inspect(
    path: Path,
    schemas: Path | None = None,
    *,
    evidence: Path | None = None,
    require_capability: bool = False,
) -> tuple[Receipt, Failure | None]:
    """Evaluate rows in order; stop at the first failure and return what was established.

    ``path`` is a bundle file or a directory containing ``receipt.intoto.jsonl``;
    evidence defaults to the ``evidence/`` directory next to the bundle.
    """
    path = Path(path)
    if path.is_dir():
        path = path / BUNDLE
    receipt = Receipt(path, evidence=evidence if evidence is not None else path.parent / EVIDENCE)
    try:
        with _row("Integrity"):
            receipt.lines = load_lines(read(path.parent, path.name, MAX_BUNDLE))
    except Failure as failure:
        return receipt, failure
    return _inspect(receipt, schemas, require_capability)


def _inspect(
    receipt: Receipt, schemas: Path | None, require_capability: bool
) -> tuple[Receipt, Failure | None]:
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


def verify(
    path: Path,
    schemas: Path | None = None,
    *,
    require_capability: bool = False,
    require_evidence: bool = False,
) -> dict:
    """Upstream syntax plus explicitly scoped normative/profile/byte-binding checks."""
    receipt, failure = inspect(path, schemas, require_capability=require_capability)
    if failure is not None:
        raise failure
    if require_evidence and receipt.facts["evidence"] != "complete":
        raise Failure("Integrity", f"Evidence is {receipt.facts['evidence']}, not complete.")
    return {
        "status": "passed",
        "statements": len(receipt.statements),
        "outputs": len(receipt.subjects),
        "capabilities": len(receipt.bindings),
        "definitionSha256": receipt.definition_sha256,
        "evidence": receipt.facts["evidence"],
        "assurance": ASSURANCE,
    }


def main(argv: list[str] | None = None) -> int:
    """Independent command-line verifier; validation is offline unless schemas are fetched."""
    import argparse
    import sys
    from urllib.request import urlopen

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("bundle", type=Path, help="receipt.intoto.jsonl or its directory")
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
    parser.add_argument(
        "--require-evidence",
        action="store_true",
        help="Also require every referenced evidence blob to be present.",
    )
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
                verify(
                    args.bundle,
                    args.schemas,
                    require_capability=args.require_capability,
                    require_evidence=args.require_evidence,
                ),
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
