"""Receipt writer: one in-toto bundle per COMPLETE run, plus outputs and evidence.

Every COMPLETE run, chain or repair controller gets, next to its record:

- ``receipt.intoto.jsonl``: one unsigned DSSE envelope per line, each an
  in-toto Statement v1: a SLSA Provenance v1 for the delivery, one in-toto
  Test Result v0.1 per check of the final candidate and a CycloneDX 1.5 BOM.
  Every statement has the same subjects: the delivered files at their project
  paths (for patch outputs, the files the patches produce).
- ``outputs/``: those delivered files.
- ``evidence/sha256/<hex>``: content-addressed, deduplicated material the
  statements name by digest (definition, inputs, stage outputs, transcripts,
  rejected attempts, checker files and raw check reports).

The export neither runs a model nor reruns checks. The format and its
verification live in :mod:`apmx.audit.verifier`.
"""

import base64
import hashlib
import json
import os
import shlex
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote, urlparse

from ..audit import verifier as bundle
from ..deps.lockfile import LockedDependency, LockFile
from ..install.apm_backend import export_cyclonedx
from ..utils.path_security import has_symlink_component
from . import check_subjects, patches, records
from .frontend import parse_contract
from .models import ChainResult, ContractError, ContractLimits, Outcome, RunResult, artifact_files
from .workspace import _path, _read

BUILDER = "https://github.com/danielmeppiel/apmx"
ENV_PRODUCER = "APMX_REPLAY_PRODUCER"
ENV_BASE_REPOSITORY = "APMX_BASE_REPOSITORY"
ENV_BASE_COMMIT = "APMX_BASE_COMMIT"
REPORTED_CHECKERS = {
    "checks/documents.py",
    "checks/acceptance.py",
    "checks/regression.py",
    "checks/documentation.py",
    "checks/documented_checkout.py",
}


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


def lock_dependencies(lock: LockFile) -> list[dict]:
    """The definition's normalized projection of a retained APM lock."""
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
    return dependencies


def _empty_inventory() -> bytes:
    """A truthful CycloneDX 1.5 inventory for an execution that installed no APM packages."""
    return _json(
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "version": 1,
            "metadata": {
                "tools": [{"vendor": "APMX", "name": "apmx receipt"}],
                "properties": [
                    {"name": "apmx:apm-dependencies", "value": "0"},
                    {
                        "name": "apmx:inventory-scope",
                        "value": "No APM lock was retained: this execution installed and "
                        "selected no APM dependencies.",
                    },
                ],
            },
            "components": [],
            "dependencies": [],
        }
    )


def _inventory(runs: tuple[RunResult, ...]) -> tuple[bytes, list[dict], list[dict]]:
    """Bind official inventory to frozen locks and exact selected capability identities."""
    documents = [
        records._record_bytes(run.run_directory / "record.json", ContractLimits())[0]
        for run in runs
    ]
    if all(row.get("lock_sha256") is None for row in documents):
        if any(row.get("imports") for row in documents):
            raise ContractError(
                "Selected capabilities have no retained APM lock; no receipt was exported.",
                code="inventory_missing",
            )
        return _empty_inventory(), [], []
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
    dependencies = lock_dependencies(lock)
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


class _Store:
    """Bounded, deduplicated ``evidence/sha256/<hex>`` writer."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.blobs: dict[str, int] = {}
        self.total = 0

    def put(self, raw: bytes) -> str:
        digest = _hash(raw)
        if digest not in self.blobs:
            self.total += len(raw)
            if self.total > 4 * ContractLimits().baseline_bytes:
                raise ContractError("Receipt export exceeds its byte limit.", code="evidence_limit")
            path = self.root / "sha256" / digest
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_new(path, raw)
            self.blobs[digest] = len(raw)
        return digest

    def descriptor(self, name: str, raw: bytes, role: str | None = None) -> dict:
        digest = self.put(raw)
        annotations = {"evidence": bundle.blob_path(digest)}
        if role is not None:
            annotations["apmx.role"] = role
        return {"name": name, "digest": {"sha256": digest}, "annotations": annotations}


def _write_new(path: Path, raw: bytes, mode: int = 0o644) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(mode)


def _retained(root: Path, entry: dict) -> bytes:
    raw, actual = _read(root, entry["relative_path"], entry["size"])
    if actual.sha256 != entry["sha256"] or actual.size != entry["size"]:
        raise ContractError("Retained bytes changed during export.", code="record_changed")
    return raw


def _git_mode(mode: int) -> str:
    return "100755" if mode & 0o111 else "100644"


def git_tree(files: dict[str, tuple[str, bytes]]) -> str:
    """Git's tree object id for these (mode, bytes) files, as ``git write-tree`` gives."""
    root: dict = {}
    for name, (mode, raw) in files.items():
        node = root
        *parents, leaf = name.split("/")
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = (mode, patches.blob_id(raw))

    def tree(node: dict) -> str:
        entries = []
        for name, value in node.items():
            if isinstance(value, dict):
                entries.append((name + "/", b"40000 " + name.encode() + b"\0", tree(value)))
            else:
                entries.append((name, f"{value[0]} {name}".encode() + b"\0", value[1]))
        body = b"".join(head + bytes.fromhex(sha) for _, head, sha in sorted(entries))
        return hashlib.sha1(b"tree %d\0" % len(body) + body, usedforsecurity=False).hexdigest()

    return tree(root)


def _base_files(result: RunResult | ChainResult, first: RunResult) -> dict[str, tuple[str, bytes]]:
    """The project files the invocation started from."""
    if isinstance(result, ChainResult):
        data, _ = records._record_bytes(result.record_path, ContractLimits())
        capture = data.get("project_capture")
        if isinstance(capture, dict) and isinstance(capture.get("files"), list):
            root = result.record_path.parent / "project"
            return {
                entry["relative_path"]: (_git_mode(entry["mode"]), _retained(root, entry))
                for entry in capture["files"]
            }
    data, _ = records._record_bytes(first.run_directory / "record.json", ContractLimits())
    root = first.run_directory / "baseline"
    return {
        entry["relative_path"]: (_git_mode(entry["mode"]), _retained(root, entry))
        for entry in data["baseline"]["files"]
    }


def _baseline(run: RunResult, data: dict) -> dict[str, tuple[str, bytes]]:
    return {
        entry["relative_path"]: (
            _git_mode(entry["mode"]),
            _retained(run.run_directory / "baseline", entry),
        )
        for entry in data["baseline"]["files"]
    }


def _artifacts(run: RunResult) -> dict[str, bytes]:
    return {
        item.relative_path: _retained(
            run.run_directory / "artifacts",
            {"relative_path": item.relative_path, "sha256": item.sha256, "size": item.size},
        )
        for item in artifact_files(run.artifact)
    }


def _patch(name: str, raw: bytes, baseline: dict[str, tuple[str, bytes]]) -> patches.Patch | None:
    """A patch output: export format, with every pre-image exactly the attempt's baseline."""
    sections = patches.parse(raw)
    if not sections:
        return None
    for item in sections:
        if patches.unsafe(item.path):
            return None
        before = baseline.get(item.path)
        if item.old is None:
            if before is not None:
                return None
        elif before is None or patches.blob_id(before[1]) != item.old or before[0] != item.mode:
            return None
    return patches.Patch(name, raw, sections)


def _delivery(
    selected: tuple[RunResult, ...],
    documents: dict[str, dict],
    base: dict[str, tuple[str, bytes]],
) -> tuple[dict[str, tuple[str, bytes]], dict[str, str | None]]:
    """Delivered files (mode, bytes) and the before-image digest of every touched path."""
    found = []
    for run in selected:
        data = documents[run.run_id]
        baseline = _baseline(run, data)
        for name, raw in _artifacts(run).items():
            patch = _patch(name, raw, baseline)
            if patch is not None:
                found.append((patch, baseline))
    before: dict[str, str | None] = {}
    if found:
        state: dict[str, tuple[str, bytes] | None] = {}
        for patch, baseline in found:
            for path in patch.paths:
                if path not in state:
                    state[path] = baseline.get(path)
                    before[path] = _hash(state[path][1]) if state[path] is not None else None
            try:
                state.update(patches.apply({path: state[path] for path in patch.paths}, patch))
            except patches.Mismatch as exc:
                raise ContractError(
                    f"Patch outputs cannot be combined: {exc}", code="evidence_delivery_failed"
                ) from exc
        delivered = {path: value for path, value in state.items() if value is not None}
        before = {
            path: digest
            for path, digest in before.items()
            if path in delivered or digest is not None
        }
        return delivered, before
    delivered: dict[str, tuple[str, bytes]] = {}
    for run in selected:
        for name, raw in _artifacts(run).items():
            if not bundle.safe_path(name):
                raise ContractError(
                    f"Output {name} cannot be delivered at a project path.",
                    code="evidence_delivery_failed",
                )
            if name in delivered and delivered[name][1] != raw:
                raise ContractError(
                    f"Two contracts deliver different bytes as {name}.",
                    code="evidence_delivery_failed",
                )
            delivered[name] = ("100644", raw)
            existing = base.get(name)
            before[name] = _hash(existing[1]) if existing is not None else None
    return delivered, before


def _contract_name(run: RunResult, data: dict) -> str:
    package = (data.get("source") or {}).get("package") or {}
    relative = package.get("contract_relative_path") or Path(data["source"]["path"]).name
    return Path(relative).name.removesuffix(".md").removesuffix(".contract")


def _producer_stages() -> tuple[str | None, dict[str, dict]]:
    """Per-contract producer observations an ``apmx check`` replay carries forward."""
    location = os.environ.get(ENV_PRODUCER)
    if not location:
        return None, {}
    try:
        data = json.loads(Path(location).read_bytes())
        stages = {
            str(stage["contractSha256"]): {
                "harness": str(stage["harness"]),
                "harnessVersion": stage.get("harnessVersion"),
                "requestedModel": stage.get("requestedModel"),
                "observedModels": [str(model) for model in stage.get("observedModels", [])],
            }
            for stage in data["stages"]
        }
        return str(data["receiptSha256"]), stages
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ContractError(
            "The replayed producer's stage record is unreadable.", code="evidence_delivery_failed"
        ) from exc


def _stages(
    selected: tuple[RunResult, ...],
    history: tuple[RunResult, ...],
    documents: dict[str, dict],
    store: _Store,
) -> tuple[list[dict], dict[str, str], str | None]:
    """Per-stage facts; with an ``apmx check`` replay, the producer's digest too."""
    producer_digest, producer = _producer_stages()
    stages, names, replayed = [], {}, False
    for run in selected:
        data = documents[run.run_id]
        contract = parse_contract(run.run_directory / "source/contract.contract.md")
        same = [
            other
            for other in history
            if records._record_bytes(other.run_directory / "record.json", ContractLimits())[0][
                "source"
            ]["sha256"]
            == contract.source_digest
        ]
        observed: list[str] = []
        for other in same:
            observed += [model for model in other.observed_models if model not in observed]
        stage = {
            "contract": _contract_name(run, data),
            "contractSha256": contract.source_digest,
            "harness": data["harness"],
            "harnessVersion": data.get("executable_version"),
            "requestedModel": run.requested_model,
            "observedModels": observed,
            "attempts": len(same),
            "budget": records._json_value(contract.budget),
            "checks": [{"name": check.name, "command": check.command} for check in contract.checks],
            "outputs": [
                {"name": name, "digest": {"sha256": store.put(raw)}}
                for name, raw in sorted(_artifacts(run).items())
            ],
        }
        if data["harness"] == "replay" and producer_digest is not None:
            reported = producer.get(contract.source_digest)
            if reported is None:
                raise ContractError(
                    "The replayed producer did not record this contract.",
                    code="evidence_delivery_failed",
                )
            stage.update(reported, reportedBy="producer", checkedBy="apmx check (replay)")
            replayed = True
        names[run.run_id] = stage["contract"]
        stages.append(stage)
    return stages, names, producer_digest if replayed else None


def _check_log(run: RunResult, name: str) -> bytes:
    raw, _ = _read(run.run_directory, "transcript.log", ContractLimits().transcript_bytes)
    prefix = f"  Check {name} "
    lines = [
        line for line in raw.decode("utf-8", "replace").splitlines() if line.startswith(prefix)
    ]
    return ("\n".join(lines) + "\n").encode("utf-8") if lines else b""


def _report(run: RunResult, check_index: int, data: dict) -> tuple[dict | None, str]:
    """The check's validated report (or None for a suite-level check) and its scope."""
    check = run.checks[check_index]
    raw, _ = _read(run.run_directory, "transcript.log", ContractLimits().transcript_bytes)
    report = check_subjects.read_report(raw.decode("utf-8"), check.name)
    if report is None:
        if any(word in REPORTED_CHECKERS for word in shlex.split(check.command)):
            raise ContractError(
                "This check requires its actual subject report; it is missing or truncated.",
                code="check_subject_unavailable",
            )
        return None, "Suite-level invocation over the declared artifacts; no per-test claims."
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
                content = _retained(run.run_directory / family, entry)
                target = _path(source, entry["relative_path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        if report["schema"] == "software-factory-document/2":
            check_subjects.document_subject(
                report, source, tuple(item.relative_path for item in artifact_files(run.artifact))
            )
            return (
                report,
                "Document format and reference consistency only; no semantic certification.",
            )
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
        check_subjects.materialize_candidate(reported, source, Path(temporary) / "candidate")
    return (
        report,
        "Observed checks over the reconstructed candidate files; not a correctness guarantee.",
    )


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


def _check_statement(
    run: RunResult,
    index: int,
    data: dict,
    stage: str,
    definition: str,
    subjects: list[dict],
    store: _Store,
) -> dict:
    check = run.checks[index]
    report, scope = _report(run, index, data)
    annotations = {
        "check": check.name,
        "command": check.command,
        "stage": stage,
        "factoryDefinition": f"sha256:{definition}",
        "scope": scope,
    }
    resources = {
        row["relative_path"]: row
        for row in data["baseline"]["files"]
        if row["relative_path"].startswith("checks/")
    }
    named = [word for word in shlex.split(check.command) if word in resources]
    configuration = []
    for name in dict.fromkeys(named):
        digest = store.put(_retained(run.run_directory / "baseline", resources[name]))
        configuration.append(
            {"name": name, "digest": {"sha256": digest}, "annotations": dict(annotations)}
        )
    if not configuration:
        source = _retained(
            run.run_directory / "source",
            next(
                row
                for row in data["source"]["retained_identities"]
                if row["relative_path"] == "contract.contract.md"
            ),
        )
        configuration.append(
            {
                "name": "contract.contract.md",
                "digest": {"sha256": store.put(source)},
                "annotations": dict(annotations),
            }
        )
    predicate = {
        "result": "PASSED" if check.normalized == 0 else "FAILED",
        "configuration": configuration,
        **_test_results(report),
    }
    if predicate["result"] == "PASSED" and predicate.get("failedTests"):
        raise ContractError("Passed report contains failures.", code="check_subject_unavailable")
    log = (
        _json({key: value for key, value in report.items() if key != "detail"})
        if report is not None
        else _check_log(run, check.name)
    )
    if log:
        predicate["url"] = bundle.blob_path(store.put(log))
    return bundle.statement(subjects, bundle.TEST_RESULT, predicate)


def _attempt_record(run: RunResult, data: dict, store: _Store) -> bytes:
    """A rejected (or otherwise unselected) attempt, by digest: outputs, checks, models."""
    checks = []
    for check in run.checks:
        log = _check_log(run, check.name)
        checks.append(
            {
                "name": check.name,
                "command": check.command,
                "result": "PASSED" if check.normalized == 0 else "FAILED",
                "log": {"sha256": store.put(log)} if log else None,
            }
        )
    return _json(
        {
            "schema": "apmx-attempt/2",
            "runId": run.run_id,
            "contractSha256": data["source"]["sha256"],
            "outcome": run.outcome.name,
            "harness": data["harness"],
            "harnessVersion": data.get("executable_version"),
            "requestedModel": run.requested_model,
            "observedModels": list(run.observed_models),
            "startedOn": data["created_at"],
            "finishedOn": data["finished_at"],
            "outputs": [
                {"name": name, "digest": {"sha256": store.put(raw)}}
                for name, raw in sorted(_artifacts(run).items())
            ],
            "checks": checks,
        }
    )


def _factory(first: dict, definition: str) -> dict:
    package = (first.get("source") or {}).get("package") or {}
    return {
        "package": package.get("package_ref"),
        "commit": package.get("resolved_commit"),
        "definition": {"sha256": definition},
    }


def _base_descriptor(
    base: dict[str, tuple[str, bytes]], before: dict[str, str | None], deleted: dict[str, str]
) -> dict:
    digest = {"gitTree": git_tree(base)}
    descriptor: dict = {"name": "base"}
    repository = os.environ.get(ENV_BASE_REPOSITORY, "")
    commit = os.environ.get(ENV_BASE_COMMIT, "").lower()
    if (
        bundle._GIT.fullmatch(commit)
        and bundle.safe_path(repository)
        and repository.count("/") == 1
    ):
        descriptor["uri"] = f"git+https://github.com/{repository}@{commit}"
        digest["gitCommit"] = commit
    descriptor["digest"] = digest
    descriptor["annotations"] = {
        "apmx.role": "base",
        "created": sorted(path for path, value in before.items() if value is None),
        "deleted": [{"path": path, "beforeDigest": deleted[path]} for path in sorted(deleted)],
    }
    return descriptor


def _apm_inputs(run: RunResult, data: dict) -> list[dict]:
    roles = {
        "apm.yml": "apm-manifest",
        "apm.lock.yaml": "apm-lock",
        "apm.lock": "apm-lock",
        "imports-apm.yml": "apm-imports-manifest",
    }
    found = []
    for entry in data["source"]["retained_identities"]:
        name = entry["relative_path"]
        if name in roles:
            raw = _retained(run.run_directory / "source", entry)
            found.append(
                {
                    "name": name,
                    "digest": {"sha256": _hash(raw)},
                    "content": base64.b64encode(raw).decode("ascii"),
                    "annotations": {"apmx.role": roles[name]},
                }
            )
    return found


def _request_inputs(
    selected: tuple[RunResult, ...], documents: dict[str, dict], store: _Store
) -> list[dict]:
    """Caller-provided inputs: what a stage needs that no selected stage produces."""
    produced = {name for run in selected for name in _artifacts(run)}
    found: dict[str, dict] = {}
    for run in selected:
        contract = parse_contract(run.run_directory / "source/contract.contract.md")
        entries = {row["relative_path"]: row for row in documents[run.run_id]["baseline"]["files"]}
        for name in contract.needs:
            if name in produced or name in found or name not in entries:
                continue
            raw = _retained(run.run_directory / "baseline", entries[name])
            found[name] = store.descriptor(name, raw, "input")
    return [found[name] for name in sorted(found)]


RECEIPT_FILE = bundle.BUNDLE


def export_completed(result: RunResult | ChainResult) -> Path | None:
    """Every COMPLETE invocation gets a receipt; REJECTED/HALTED roots never do."""
    if result.outcome is not Outcome.COMPLETE:
        return None
    record = (
        result.record_path
        if isinstance(result, ChainResult)
        else result.controller.path
        if result.controller
        else result.run_directory / "record.json"
    )
    return export_package(record, record.parent)


def export_package(record_path: Path, destination: Path) -> Path:
    """Publish ``receipt.intoto.jsonl``, ``outputs/`` and ``evidence/`` into ``destination``.

    Never modifies the canonical outcome; refuses to overwrite an earlier export.
    Returns the bundle path.
    """
    destination = destination.absolute()
    targets = [destination / name for name in (bundle.BUNDLE, bundle.OUTPUTS, bundle.EVIDENCE)]
    if any(path.exists() or path.is_symlink() for path in targets) or has_symlink_component(
        Path(destination.anchor), destination
    ):
        raise ContractError(
            "Receipt destination exists or contains a symlink.", code="evidence_destination"
        )
    result = records.load_completed_result(record_path)
    selected = result.runs if isinstance(result, ChainResult) else (result,)
    history = records.completed_attempt_history(result)
    boundary = records.CompletionBoundary()
    boundary.capture(result)
    try:
        documents = {
            run.run_id: records._record_bytes(run.run_directory / "record.json", ContractLimits())[
                0
            ]
            for run in history
        }
        bom_raw, dependencies, capability_bindings = _inventory(selected)
        definition_raw = _json(_definition(selected, dependencies))
        definition = _hash(definition_raw)
        base = _base_files(result, selected[0])
        delivered, before = _delivery(selected, documents, base)
        if not delivered:
            raise ContractError(
                "The completed run delivered no files.", code="evidence_delivery_failed"
            )
        deleted = {
            path: digest for path, digest in before.items() if path not in delivered and digest
        }
        subjects = [
            {"name": path, "digest": {"sha256": _hash(raw)}}
            for path, (_, raw) in sorted(delivered.items())
        ]
        destination.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".apmx-receipt-", dir=destination) as temporary:
            staging = Path(temporary)
            store = _Store(staging / bundle.EVIDENCE)
            definition_ref = store.descriptor("definition.json", definition_raw, "definition")
            stages, names, producer = _stages(selected, history, documents, store)
            dependencies_out = [_base_descriptor(base, before, deleted)]
            for path in sorted(before):
                if before[path] is None:
                    continue
                raw = (
                    base[path][1] if path in base and _hash(base[path][1]) == before[path] else None
                )
                if raw is None:
                    for run in selected:
                        entry = next(
                            (
                                row
                                for row in documents[run.run_id]["baseline"]["files"]
                                if row["relative_path"] == path and row["sha256"] == before[path]
                            ),
                            None,
                        )
                        if entry is not None:
                            raw = _retained(run.run_directory / "baseline", entry)
                            break
                if raw is not None:
                    store.put(raw)
                dependencies_out.append(
                    {
                        "name": path,
                        "digest": {"sha256": before[path]},
                        "annotations": {"apmx.role": "before-image"},
                    }
                )
            dependencies_out += _request_inputs(selected, documents, store)
            for binding in capability_bindings:
                dependencies_out.append(
                    {
                        "name": binding["capability"],
                        "uri": binding["purl"],
                        "digest": {"sha256": binding["bodySha256"]},
                        "annotations": {
                            "apmx.role": "capability",
                            "lockIdentity": binding["lockIdentity"],
                            "commit": binding["commit"],
                            "registryHash": binding["registryHash"],
                            "packageHash": binding["packageHash"],
                            "bomRef": binding["bomRef"],
                            "resources": binding["resources"],
                        },
                    }
                )
            dependencies_out += _apm_inputs(selected[0], documents[selected[0].run_id])
            if producer is not None:
                dependencies_out.append(
                    {
                        "name": f"producer {bundle.BUNDLE}",
                        "digest": {"sha256": producer},
                        "annotations": {"apmx.role": "producer-receipt"},
                    }
                )
            byproducts = [definition_ref]
            for run in history:
                transcript, _ = _read(
                    run.run_directory, "transcript.log", ContractLimits().transcript_bytes
                )
                byproducts.append(
                    store.descriptor(f"transcripts/{run.run_id}.log", transcript, "transcript")
                )
                if run not in selected:
                    byproducts.append(
                        store.descriptor(
                            f"attempts/{run.run_id}.json",
                            _attempt_record(run, documents[run.run_id], store),
                            "rejected-attempt",
                        )
                    )
            first, last = documents[selected[0].run_id], documents[selected[-1].run_id]
            backend = first.get("apm_backend") or {}
            from apmx.version import get_version

            version = {"apmx": get_version()}
            if isinstance(backend, dict) and backend.get("version"):
                version["apm"] = str(backend["version"])
            invocation = (
                result.chain_id
                if isinstance(result, ChainResult)
                else result.controller.path.parent.name
                if result.controller
                else result.run_id
            )
            provenance = bundle.statement(
                subjects,
                bundle.PROVENANCE,
                {
                    "buildDefinition": {
                        "buildType": bundle.BUILD_TYPE,
                        "externalParameters": {"factory": _factory(first, definition)},
                        "internalParameters": {"stages": stages},
                        "resolvedDependencies": dependencies_out,
                    },
                    "runDetails": {
                        "builder": {"id": BUILDER, "version": version},
                        "metadata": {
                            "invocationId": invocation,
                            "startedOn": first["created_at"],
                            "finishedOn": last["finished_at"],
                        },
                        "byproducts": byproducts,
                    },
                },
            )
            statements = [provenance]
            for run in selected:
                for index in range(len(run.checks)):
                    statements.append(
                        _check_statement(
                            run,
                            index,
                            documents[run.run_id],
                            names[run.run_id],
                            definition,
                            subjects,
                            store,
                        )
                    )
            statements.append(bundle.statement(subjects, bundle.CYCLONEDX, json.loads(bom_raw)))
            outputs = staging / bundle.OUTPUTS
            for path, (mode, raw) in delivered.items():
                target = _path(outputs, path)
                target.parent.mkdir(parents=True, exist_ok=True)
                _write_new(target, raw, 0o755 if mode == "100755" else 0o644)
            _write_new(staging / bundle.BUNDLE, bundle.bundle_bytes(statements))
            parsed, failure = bundle.inspect(staging / bundle.BUNDLE)
            if failure is not None:
                raise ContractError(
                    f"The exported receipt does not verify ({failure.row}: {failure}).",
                    code="evidence_delivery_failed",
                )
            if parsed.facts["evidence"] != "complete":
                raise ContractError(
                    "Exported evidence is incomplete.", code="evidence_delivery_failed"
                )
            boundary.validate(result)
            if any(path.exists() or path.is_symlink() for path in targets):
                raise ContractError(
                    "Receipt destination appeared during export.", code="evidence_destination"
                )
            for name in (bundle.OUTPUTS, bundle.EVIDENCE, bundle.BUNDLE):
                os.rename(staging / name, destination / name)
        return destination / bundle.BUNDLE
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        if isinstance(exc, ContractError):
            raise
        for name in (bundle.OUTPUTS, bundle.EVIDENCE):
            path = destination / name
            if (
                path.is_dir()
                and not path.is_symlink()
                and not (destination / bundle.BUNDLE).exists()
            ):
                shutil.rmtree(path, ignore_errors=True)
        raise ContractError(
            "Receipt delivery failed; the canonical execution outcome was not changed.",
            code="evidence_delivery_failed",
        ) from exc
