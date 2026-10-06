"""Unsigned standards projections of canonical retained execution observations."""

import hashlib
import json
import os
import shlex
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote, urlparse

from ..deps.lockfile import LockedDependency, LockFile
from ..install.apm_backend import export_cyclonedx
from ..utils.path_security import has_symlink_component
from . import check_subjects, records
from .frontend import parse_contract
from .models import ChainResult, ContractError, ContractLimits, Outcome, RunResult, artifact_files
from .workspace import _path, _read

STATEMENT = "https://in-toto.io/Statement/v1"
PROVENANCE = "https://slsa.dev/provenance/v1"
TEST_RESULT = "https://in-toto.io/attestation/test-result/v0.1"
BUILD_TYPE = "https://github.com/danielmeppiel/apmx/build/contract/v1"
BUILDER = "https://github.com/danielmeppiel/apmx"


def _json(document: object) -> bytes:
    return (
        json.dumps(
            document, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode("ascii")


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _matches_component(component: dict, dependency: LockedDependency) -> bool:
    """Check the pinned backend's inventory mapping, without synthesizing a BOM."""
    parts = [part for part in dependency.repo_url.split("/") if part]
    domain = parts.pop(0).lower() if parts and "." in parts[0] else ""
    name = "/".join(parts) or dependency.repo_url
    short = quote(name.rsplit("/", 1)[-1], safe="")
    kind, identity, version = "generic", short, dependency.content_hash
    if urlparse(dependency.resolved_url or "").scheme == "oci":
        kind, version = "oci", dependency.resolved_hash or dependency.content_hash
    elif dependency.source != "local" and dependency.resolved_commit:
        forge = dependency.host_type or {
            "github.com": "github",
            "gitlab.com": "gitlab",
            "bitbucket.org": "bitbucket",
        }.get(domain)
        if forge in ("github", "gitlab", "bitbucket"):
            kind = forge
            identity = "/".join(quote(part, safe="") for part in name.split("/"))
        version = dependency.resolved_commit
    purl = urlparse(component.get("purl", ""))
    expected = f"{kind}/{identity}" + (f"@{version}" if version else "")
    return (
        component.get("name") == name
        and purl.scheme == "pkg"
        and purl.path == expected
        and not (purl.netloc or purl.query or purl.fragment)
        and component.get("bom-ref") == component.get("purl")
    )


def _inventory(runs: tuple[RunResult, ...]) -> tuple[bytes, list[dict], list[dict]]:
    """Bind official inventory to frozen locks and exact selected capability identities."""
    documents = [
        records._record_bytes(run.run_directory / "record.json", ContractLimits())[0]
        for run in runs
    ]
    first = documents[0]
    if (
        not first.get("lock_sha256")
        or not first.get("manifest_sha256")
        or not first.get("apm_backend")
    ):
        raise ContractError(
            "This execution has no retained official APM inventory; no complete package was exported.",
            code="inventory_missing",
        )
    if any(
        row.get(key) != first[key]
        for row in documents
        for key in ("lock_sha256", "manifest_sha256", "apm_backend")
    ):
        raise ContractError(
            "The invocation has inconsistent inventory snapshots.", code="inventory_changed"
        )
    source = runs[0].run_directory / "source"
    manifest, _ = _read(source, "apm.yml", ContractLimits().file_bytes)
    lock_raw, _ = _read(source, "apm.lock.yaml", ContractLimits().file_bytes)
    if _hash(manifest) != first["manifest_sha256"] or _hash(lock_raw) != first["lock_sha256"]:
        raise ContractError("Retained inventory bytes changed.", code="inventory_changed")
    lock = LockFile.from_yaml(lock_raw.decode("utf-8"))
    raw = export_cyclonedx(manifest, lock_raw, expected_backend=first["apm_backend"])
    bom = json.loads(raw)
    component_ids = [component.get("bom-ref") for component in bom.get("components", [])]
    if any(not isinstance(ref, str) or not ref for ref in component_ids) or len(
        component_ids
    ) != len(set(component_ids)):
        raise ContractError(
            "The official ABOM has missing or duplicate component identities. "
            "Inspect the retained lock for overlapping package references.",
            code="inventory_binding",
        )
    dependencies = []
    for dep in lock.get_all_dependencies():
        local = dep.source == "local"
        dependencies.append(
            {
                "repository": None if local else dep.repo_url,
                "name": dep.name,
                "source": dep.source,
                "commit": dep.resolved_commit,
                "version": dep.version,
                "contentHash": dep.content_hash,
                "virtualPath": dep.virtual_path,
                "registryHash": dep.resolved_hash,
                "skillSubset": sorted(dep.skill_subset),
            }
        )
    dependencies.sort(key=lambda item: _json(item))
    bindings = []
    for row in documents:
        for skill in row["imports"]:
            dep = lock.get_dependency(skill["lock_identity"])
            if dep is None or (
                dep.resolved_commit != skill["resolved_commit"]
                or dep.content_hash != skill["verified_package_hash"]
            ):
                raise ContractError(
                    "Selected capability differs from its retained lock.", code="inventory_changed"
                )
            matches = [
                component
                for component in bom.get("components", [])
                if _matches_component(component, dep)
            ]
            if len(matches) != 1:
                raise ContractError(
                    "The official ABOM cannot identify this selected capability unambiguously.",
                    code="inventory_binding",
                )
            binding = {
                "capability": skill["name"],
                "lockIdentity": skill["lock_identity"],
                "commit": dep.resolved_commit,
                "registryHash": dep.resolved_hash,
                "packageHash": dep.content_hash,
                "bodySha256": skill["sha256"],
                "resources": skill["resources"],
                "bomRef": matches[0].get("bom-ref"),
                "purl": matches[0]["purl"],
                "scope": "Package inventory; selected capability bound by the retained lock and content.",
            }
            if binding not in bindings:
                bindings.append(binding)
    return raw, dependencies, bindings


def _definition(runs: tuple[RunResult, ...], dependencies: list[dict]) -> dict:
    stages = []
    for run in runs:
        data, _ = records._record_bytes(run.run_directory / "record.json", ContractLimits())
        contract = parse_contract(run.run_directory / "source/contract.contract.md")
        resources = [
            {key: row[key] for key in ("relative_path", "sha256", "size")}
            for row in data["baseline"]["files"]
            if row["relative_path"].startswith("checks/")
        ]
        stages.append(
            {
                "contractSha256": contract.source_digest,
                "needs": list(contract.needs),
                "produces": list(contract.outputs),
                "checks": [
                    {"name": check.name, "command": check.command} for check in contract.checks
                ],
                "resources": resources,
                "capabilities": [
                    {
                        key: skill[key]
                        for key in (
                            "name",
                            "sha256",
                            "resolved_commit",
                            "version",
                            "kind",
                            "context_name",
                            "source_relative_path",
                            "resources",
                            "verified_package_hash",
                            "managed_metadata",
                        )
                    }
                    for skill in data["imports"]
                ],
                "limits": data["limits"],
                "budget": records._json_value(contract.budget),
                "selectionSchema": data["baseline"].get("selection_schema"),
            }
        )
    return {"schema": "apmx-definition/1", "stages": stages, "resolvedDependencies": dependencies}


class _Package:
    """One bounded writer for digest-addressed, package-relative references."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.files: dict[str, dict] = {}
        self.total = 0

    def write(self, name: str, raw: bytes) -> dict:
        identity = {"sha256": _hash(raw), "size": len(raw)}
        if name in self.files:
            if self.files[name] != identity:
                raise ContractError("Evidence output collision.", code="evidence_collision")
        else:
            self.total += len(raw)
            if self.total > 4 * ContractLimits().baseline_bytes:
                raise ContractError(
                    "Evidence export exceeds its byte limit.", code="evidence_limit"
                )
            path = _path(self.root, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            self.files[name] = identity
        return {"name": name, "digest": {"sha256": identity["sha256"]}}

    def document(self, name: str, document: object) -> dict:
        return self.write(name, _json(document))

    def copy(self, root: Path, entry: dict, name: str) -> dict:
        raw, actual = _read(root, entry["relative_path"], entry["size"])
        if actual.sha256 != entry["sha256"] or actual.size != entry["size"]:
            raise ContractError("Retained bytes changed during export.", code="record_changed")
        return self.write(name, raw)


def _statement(subjects: list[dict], predicate_type: str, predicate: dict) -> dict:
    return {
        "_type": STATEMENT,
        "subject": subjects,
        "predicateType": predicate_type,
        "predicate": predicate,
    }


def _copy_attempt(package: _Package, run: RunResult) -> tuple[dict, list[dict], list[dict]]:
    data, digest = records._record_bytes(run.run_directory / "record.json", ContractLimits())
    prefix = f"attempts/{run.run_id}"
    materials = []
    for family, entries in (
        ("baseline", data["baseline"]["files"]),
        ("source", data["source"]["retained_identities"]),
    ):
        for entry in entries:
            materials.append(
                package.copy(
                    run.run_directory / family, entry, f"{prefix}/{family}/{entry['relative_path']}"
                )
            )
    subjects = []
    for item in artifact_files(run.artifact):
        subjects.append(
            package.copy(
                run.run_directory / "artifacts",
                {"relative_path": item.relative_path, "sha256": item.sha256, "size": item.size},
                f"{prefix}/artifacts/{item.relative_path}",
            )
        )
    observation = {
        "schema": "apmx-execution-projection/1",
        "originalRecordSha256": digest,
        "runId": run.run_id,
        "outcome": run.outcome.name,
        "harness": data["harness"],
        "requestedModel": run.requested_model,
        "observedModels": list(run.observed_models),
        "startedOn": data["created_at"],
        "finishedOn": data["finished_at"],
        "assurance": data["assurance"],
        "consentSource": run.consent_source,
        "handoffPolicy": run.handoff_policy,
        "controllerSha256": run.controller.sha256 if run.controller else None,
        "checks": [
            {
                "name": check.name,
                "command": check.command,
                "normalized": check.normalized,
                "returncode": check.process.returncode,
                "cleanupConfirmed": check.process.cleanup_confirmed,
                "elapsedSeconds": check.process.elapsed_seconds,
            }
            for check in run.checks
        ],
        "omitted": ["raw transcript", "baseline.repair.diagnostics", "absolute execution paths"],
    }
    package.document(f"{prefix}/execution.json", observation)
    return data, materials, subjects


def _report_subjects(
    package: _Package, run: RunResult, check_index: int, data: dict, subjects: list[dict]
) -> tuple[list[dict], dict | None, str]:
    check = run.checks[check_index]
    raw, _ = _read(run.run_directory, "transcript.log", ContractLimits().transcript_bytes)
    report = check_subjects.read_report(raw.decode("utf-8"), check.name)
    if report is None:
        if any(
            word
            in {
                "checks/documents.py",
                "checks/acceptance.py",
                "checks/regression.py",
                "checks/documentation.py",
                "checks/documented_checkout.py",
            }
            for word in shlex.split(check.command)
        ):
            raise ContractError(
                "This check requires its actual subject report; it is missing or truncated.",
                code="check_subject_unavailable",
            )
        return (
            subjects,
            None,
            "Suite-level invocation over the declared artifacts; no per-test claims.",
        )
    if report.get("status") != ("passed" if check.normalized == 0 else "failed"):
        raise ContractError(
            "Report status disagrees with the canonical check.", code="check_subject_unavailable"
        )
    with TemporaryDirectory(prefix="apmx-check-subject-") as temporary:
        source = Path(temporary) / "source"
        source.mkdir()
        for family, entries in (
            ("baseline", data["baseline"]["files"]),
            ("artifacts", [records._json_value(item) for item in artifact_files(run.artifact)]),
        ):
            for entry in entries:
                name = entry["relative_path"]
                content, actual = _read(run.run_directory / family, name, entry["size"])
                if actual.sha256 != entry["sha256"]:
                    raise ContractError("Retained subject changed.", code="record_changed")
                target = _path(source, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        if report["schema"] == "software-factory-document/2":
            names = check_subjects.document_subject(
                report, source, tuple(item.relative_path for item in artifact_files(run.artifact))
            )
            matched = [
                subject
                for subject in subjects
                if subject["name"] == f"attempts/{run.run_id}/artifacts/{names[0]}"
            ]
            scope = "Document format and reference consistency only; no semantic certification."
        else:
            reported = report["subject"]
            resources = {
                row["relative_path"]: {"sha256": row["sha256"], "size": row["size"]}
                for row in data["baseline"]["files"]
                if row["relative_path"].startswith("checks/")
            }
            if check_subjects.inventory_digest(resources) != reported["checks"]:
                raise ContractError(
                    "Report checker resources disagree.", code="check_subject_unavailable"
                )
            target = Path(temporary) / "candidate"
            check_subjects.materialize_candidate(reported, source, target)
            prefix = f"candidates/{reported['candidate']}"
            matched = [
                package.copy(target, {**identity, "relative_path": name}, f"{prefix}/{name}")
                for name, identity in sorted(reported["candidate_files"].items())
            ]
            package.document(f"{prefix}/inventory.json", reported["candidate_files"])
            scope = "Observed checks over the reconstructed candidate files; not a correctness guarantee."
    return matched, report, scope


def _test_results(report: dict | None) -> dict:
    """Only explicitly reported cases, never inferred counts or fabricated test names."""
    if report is None or "observed" not in report:
        return {}
    result: dict[str, list[str]] = {}

    def visit(rows: list, prefix: str = "", depth: int = 0) -> None:
        if not isinstance(rows, list) or len(rows) > 1000 or depth > 8:
            raise ContractError(
                "Invalid reported test inventory.", code="check_subject_unavailable"
            )
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                raise ContractError("Invalid reported case.", code="check_subject_unavailable")
            name = prefix + row["id"]
            key = {"passed": "passedTests", "failed": "failedTests"}.get(row.get("status"))
            if key is None:
                raise ContractError("Incomplete reported case.", code="check_subject_unavailable")
            result.setdefault(key, []).append(name)
            if "observed" in row:
                visit(row["observed"], name + "/", depth + 1)

    visit(report["observed"])
    return result


def export_completed(result: RunResult | ChainResult) -> Path | None:
    """Existing invocations export when they captured official inventory; plain runs stay plain."""
    if result.outcome is not Outcome.COMPLETE:
        return None
    runs = result.runs if isinstance(result, ChainResult) else (result,)
    documents = [
        records._record_bytes(run.run_directory / "record.json", ContractLimits())[0]
        for run in runs
    ]
    if all(row.get("lock_sha256") is None for row in documents):
        return None
    record = (
        result.record_path
        if isinstance(result, ChainResult)
        else result.controller.path
        if result.controller
        else result.run_directory / "record.json"
    )
    return export_package(record, record.parent / "evidence")


def export_package(record_path: Path, destination: Path) -> Path:
    """Publish one new portable package atomically; never modify the canonical outcome."""
    destination = destination.absolute()
    if destination.exists() or has_symlink_component(Path(destination.anchor), destination):
        raise ContractError(
            "Evidence destination exists or contains a symlink.", code="evidence_destination"
        )
    result = records.load_completed_result(record_path)
    selected = result.runs if isinstance(result, ChainResult) else (result,)
    history = records.completed_attempt_history(result)
    boundary = records.CompletionBoundary()
    boundary.capture(result)
    try:
        bom, dependencies, capability_bindings = _inventory(selected)
        definition = _definition(selected, dependencies)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".apmx-evidence-", dir=destination.parent) as temporary:
            staging = Path(temporary) / "package"
            staging.mkdir(mode=0o700)
            package = _Package(staging)
            definition_ref = package.document("definition.json", definition)
            bom_ref = package.write("abom.cdx.json", bom)
            package.document("capability-bindings.json", capability_bindings)
            all_outputs = []
            production_refs = []
            check_refs = []
            rows = []
            for run in history:
                data, materials, subjects = _copy_attempt(package, run)
                if run in selected:
                    all_outputs.extend(subjects)
                production = _statement(
                    subjects,
                    PROVENANCE,
                    {
                        "buildDefinition": {
                            "buildType": BUILD_TYPE,
                            "externalParameters": {
                                "definition": definition_ref,
                                "contractSha256": data["source"]["sha256"],
                            },
                            "resolvedDependencies": materials,
                        },
                        "runDetails": {
                            "builder": {"id": BUILDER, "version": {"harness": data["harness"]}},
                            "metadata": {
                                "invocationId": run.run_id,
                                "startedOn": data["created_at"],
                                "finishedOn": data["finished_at"],
                            },
                            "byproducts": [
                                bom_ref,
                                {
                                    "name": f"attempts/{run.run_id}/execution.json",
                                    "digest": {
                                        "sha256": package.files[
                                            f"attempts/{run.run_id}/execution.json"
                                        ]["sha256"]
                                    },
                                },
                            ],
                        },
                    },
                )
                production_refs.append(
                    package.document(f"provenance/{run.run_id}.intoto.json", production)
                )
                for ci, check in enumerate(run.checks):
                    assessed, report, scope = _report_subjects(package, run, ci, data, subjects)
                    configuration = package.document(
                        f"checks/{run.run_id}-{ci + 1}.configuration.json",
                        {
                            "name": check.name,
                            "command": check.command,
                            "scope": scope,
                            "definition": definition_ref,
                            "materials": materials,
                        },
                    )
                    predicate = {
                        "result": "PASSED" if check.normalized == 0 else "FAILED",
                        "configuration": [configuration],
                        **_test_results(report),
                    }
                    if predicate["result"] == "PASSED" and predicate.get("failedTests"):
                        raise ContractError(
                            "Passed report contains failures.", code="check_subject_unavailable"
                        )
                    if report is not None:
                        package.document(
                            f"checks/{run.run_id}-{ci + 1}.report.json",
                            {key: value for key, value in report.items() if key != "detail"},
                        )
                    check_refs.append(
                        package.document(
                            f"checks/{run.run_id}-{ci + 1}.intoto.json",
                            _statement(assessed, TEST_RESULT, predicate),
                        )
                    )
                rows.append(
                    f"| {run.run_id} | {run.outcome.name} | {data['harness']} | {len(run.checks)} |"
                )
            if len(history) == 1:
                primary = production
            else:
                primary = _statement(
                    all_outputs,
                    PROVENANCE,
                    {
                        "buildDefinition": {
                            "buildType": BUILD_TYPE,
                            "externalParameters": {"definition": definition_ref},
                            "resolvedDependencies": production_refs,
                        },
                        "runDetails": {
                            "builder": {"id": BUILDER},
                            "metadata": {
                                "invocationId": result.chain_id
                                if isinstance(result, ChainResult)
                                else result.controller.path.parent.name
                            },
                            "byproducts": [bom_ref],
                        },
                    },
                )
            package.document("provenance.intoto.json", primary)
            summary = (
                "# APMX execution evidence\n\n"
                "Unsigned local observations, not authenticated attestation or a sandbox.\n\n"
                f"Definition SHA-256: `{definition_ref['digest']['sha256']}`\n\n"
                "[Definition](definition.json) | [CycloneDX inventory](abom.cdx.json) | "
                "[Production provenance](provenance.intoto.json) | [File index](index.json)\n\n"
                "| Attempt | Recorded outcome | Harness | Checks |\n| --- | --- | --- | --- |\n"
                + "\n".join(rows)
                + "\n\n"
                "Artifacts retain separate producer statements, including code and documentation patches. "
                "Check statements bind assessed documents or reconstructed candidate files. "
                "Generic checks are suite-level observations without fabricated cases.\n\n"
                "Raw transcripts, repair diagnostic excerpts and raw canonical record copies are omitted. "
                "Execution projections retain original-record digests, not signatures. "
                "Source, baseline, manifest and lock files are included verbatim and may contain private "
                "project information or paths: review before sharing.\n"
            )
            package.write("summary.md", summary.encode("ascii"))
            index = {
                "schema": "apmx-evidence-package/1",
                "definition": definition_ref,
                "inventory": bom_ref,
                "production": production_refs,
                "checks": check_refs,
                "attempts": [
                    {
                        "runId": run.run_id,
                        "selected": run in selected,
                        "outcome": run.outcome.name,
                    }
                    for run in history
                ],
                "files": package.files,
                "omissions": [
                    "raw transcripts",
                    "baseline.repair.diagnostics",
                    "raw canonical records",
                ],
                "assurance": "unsigned same-user local observations",
            }
            for name, identity in package.files.items():
                raw, _ = _read(staging, name, identity["size"])
                if _hash(raw) != identity["sha256"]:
                    raise ContractError(
                        "Exported evidence changed before publication.",
                        code="evidence_delivery_failed",
                    )
            with (staging / "index.json").open("xb") as stream:
                stream.write(_json(index))
                stream.flush()
                os.fsync(stream.fileno())
            boundary.validate(result)
            if destination.exists():
                raise ContractError(
                    "Evidence destination appeared during export.", code="evidence_destination"
                )
            os.rename(staging, destination)
        return destination
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError(
            "Evidence delivery failed; the canonical execution outcome was not changed.",
            code="evidence_delivery_failed",
        ) from exc
