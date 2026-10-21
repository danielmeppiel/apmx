"""Receipt v2 writer: one in-toto bundle, delivered outputs and content-addressed evidence."""

import base64
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest
from test_chain import caller, producer
from test_execution_result import _execute
from test_repair import budgeted_contract, run

from apmx.audit import verifier
from apmx.contracts import evidence, records
from apmx.contracts.models import ContractError

__all__ = ["caller"]
pytestmark = pytest.mark.component
GOLDEN = Path(__file__).resolve().parent / "golden/receipt-v2"


def _statements(bundle: Path) -> list[dict]:
    return [
        json.loads(base64.b64decode(json.loads(line)["payload"]))
        for line in bundle.read_bytes().splitlines()
    ]


def test_definition_digest_semantics_match_the_real_v1_receipt() -> None:
    """Policy pins keep working: the digest is still SHA-256 of the canonical definition."""
    raw = (GOLDEN / "definition.json").read_bytes()
    assert evidence._json(json.loads(raw)) == raw
    assert (
        hashlib.sha256(raw).hexdigest()
        == "0bec0549b9af1e093c5204610163203ca6a58ea7602ad1021cdd381c042b039e"
    )
    statements = _statements(GOLDEN / "receipt.intoto.jsonl")
    factory = statements[0]["predicate"]["buildDefinition"]["externalParameters"]["factory"]
    assert factory["definition"]["sha256"] == hashlib.sha256(raw).hexdigest()


def test_golden_five_stage_bundle_has_the_v2_shape() -> None:
    """The real v0.6.0 checkout run, exported as v2: 11 lines, same subjects everywhere."""
    bundle = GOLDEN / "receipt.intoto.jsonl"
    statements = _statements(bundle)
    kinds = [item["predicateType"] for item in statements]
    assert kinds == [verifier.PROVENANCE] + [verifier.TEST_RESULT] * 9 + [verifier.CYCLONEDX]
    names = [[s["name"] for s in item["subject"]] for item in statements]
    assert names[0] == [
        "docs/checkout.md",
        "src/checkout.py",
        "src/pricing.py",
        "tests/test_free_shipping.py",
    ]
    assert all(item == names[0] for item in names)
    provenance = statements[0]["predicate"]
    build = provenance["buildDefinition"]
    assert build["buildType"] == "https://github.com/danielmeppiel/apmx/build/factory/v2"
    assert build["externalParameters"]["factory"]["package"] == (
        "danielmeppiel/apmx-demo-factory#v1.0.0"
    )
    stages = build["internalParameters"]["stages"]
    assert [stage["contract"] for stage in stages] == [
        "planning",
        "specification",
        "build",
        "documentation",
        "review",
    ]
    assert stages[2]["observedModels"] == ["gpt-6.1-sol"]
    assert stages[2]["budget"] == {"max_attempts": 3, "max_seconds": 600}
    roles = [verifier.role(item) for item in build["resolvedDependencies"]]
    assert roles.count("base") == 1 and roles.count("before-image") == 3
    base = build["resolvedDependencies"][0]
    assert base["annotations"]["created"] == ["tests/test_free_shipping.py"]
    assert base["annotations"]["deleted"] == []
    test = statements[3]["predicate"]
    assert test["configuration"][0]["name"] == "checks/acceptance.py"
    assert test["configuration"][0]["annotations"]["check"] == "shipping-examples"
    assert test["url"].startswith("evidence/sha256/")
    receipt, failure = verifier.inspect(bundle)
    assert failure is None, failure
    assert receipt.facts["evidence"] == "absent"


def test_run_without_dependencies_exports_bundle_outputs_and_evidence(
    tmp_path, monkeypatch
) -> None:
    _, result = _execute(tmp_path, monkeypatch)
    path = result.run_directory / "record.json"
    before = path.read_bytes()
    bundle = evidence.export_package(path, result.run_directory)
    assert bundle == result.run_directory / "receipt.intoto.jsonl"
    assert path.read_bytes() == before
    assert records.load_completed_result(path) == result
    for name in ("definition.json", "index.json", "summary.md", "abom.cdx.json", "receipt"):
        assert not (result.run_directory / name).exists()
    statements = _statements(bundle)
    assert [item["predicateType"] for item in statements] == [
        verifier.PROVENANCE,
        verifier.TEST_RESULT,
        verifier.CYCLONEDX,
    ]
    for line in bundle.read_bytes().splitlines():
        envelope = json.loads(line)
        assert envelope["payloadType"] == "application/vnd.in-toto+json"
        assert envelope["signatures"] == []
    bom = statements[-1]["predicate"]
    assert (bom["bomFormat"], bom["specVersion"]) == ("CycloneDX", "1.5")
    assert bom["components"] == []
    properties = {item["name"]: item["value"] for item in bom["metadata"]["properties"]}
    assert properties["apmx:apm-dependencies"] == "0"
    subject = statements[0]["subject"]
    assert subject == [
        {"name": result.artifact.relative_path, "digest": {"sha256": result.artifact.sha256}}
    ]
    output = result.run_directory / "outputs" / result.artifact.relative_path
    assert hashlib.sha256(output.read_bytes()).hexdigest() == result.artifact.sha256
    for blob in (result.run_directory / "evidence/sha256").iterdir():
        assert hashlib.sha256(blob.read_bytes()).hexdigest() == blob.name
    definition = statements[0]["predicate"]["buildDefinition"]["externalParameters"]["factory"]
    raw = (
        result.run_directory / "evidence/sha256" / definition["definition"]["sha256"]
    ).read_bytes()
    assert json.loads(raw)["resolvedDependencies"] == []
    receipt, failure = verifier.inspect(bundle)
    assert failure is None and receipt.facts["evidence"] == "complete"


def test_harness_and_models_are_recorded_per_stage(tmp_path, monkeypatch) -> None:
    _, result = _execute(tmp_path, monkeypatch)
    bundle = evidence.export_package(result.run_directory / "record.json", result.run_directory)
    stage = _statements(bundle)[0]["predicate"]["buildDefinition"]["internalParameters"]["stages"][
        0
    ]
    assert set(stage) >= {
        "contract",
        "harness",
        "harnessVersion",
        "requestedModel",
        "observedModels",
        "attempts",
        "budget",
    }
    assert stage["harness"] == "copilot" and stage["attempts"] == 1
    assert "reportedBy" not in stage


def test_selected_capabilities_without_a_retained_lock_refuse_a_receipt(
    tmp_path, monkeypatch
) -> None:
    _, result = _execute(tmp_path, monkeypatch)
    original = records._record_bytes

    def with_imports(path, limits):
        data, digest = original(path, limits)
        return {**data, "imports": [{"name": "unlocked"}]}, digest

    monkeypatch.setattr(records, "_record_bytes", with_imports)
    with pytest.raises(ContractError) as error:
        evidence._inventory((result,))
    assert error.value.code == "inventory_missing"


def test_empty_inventory_is_deterministic() -> None:
    assert evidence._empty_inventory() == evidence._empty_inventory()


@pytest.mark.parametrize("name", ["receipt.intoto.jsonl", "outputs", "evidence"])
def test_existing_receipt_is_never_overwritten(tmp_path, monkeypatch, name) -> None:
    _, result = _execute(tmp_path, monkeypatch)
    target = tmp_path / "package"
    (target / name).parent.mkdir(parents=True, exist_ok=True)
    (target / name).write_bytes(b"keep")
    with pytest.raises(ContractError):
        evidence.export_package(result.run_directory / "record.json", target)
    assert (target / name).read_bytes() == b"keep"


def test_definition_excludes_runtime_clocks_paths_but_binds_goals(tmp_path, monkeypatch) -> None:
    _, result = _execute(tmp_path, monkeypatch)
    first = evidence._definition((result,), [])
    path = result.run_directory / "record.json"
    data = json.loads(path.read_bytes())
    data.update(
        harness="opencode",
        requested_model="different-model",
        observed_models=["observed-model"],
        created_at="2099-01-01T00:00:00Z",
        executable="/other/runtime",
        executable_version="9.9.9",
    )
    data["source"]["path"] = "/another/checkout/job.contract.md"
    path.write_text(json.dumps(data))
    assert evidence._definition((result,), []) == first
    contract = result.run_directory / "source/contract.contract.md"
    contract.chmod(0o600)
    contract.write_bytes(contract.read_bytes() + b"\nDifferent goal.\n")
    assert evidence._definition((result,), []) != first


@pytest.mark.parametrize("fault", ["write", "publish", "report", "changed-source"])
def test_export_failure_does_not_publish_or_mutate_completion(tmp_path, monkeypatch, fault) -> None:
    _, result = _execute(tmp_path, monkeypatch)
    record = result.run_directory / "record.json"
    before = record.read_bytes()

    def failure(*_, **__):
        raise OSError("injected delivery error")

    if fault == "write":
        monkeypatch.setattr(evidence.os, "fsync", failure)
    elif fault == "publish":
        monkeypatch.setattr(evidence.os, "rename", failure)
    elif fault == "report":

        def refuse(*_):
            raise ContractError("Retained report unavailable.", code="check_subject_unavailable")

        monkeypatch.setattr(evidence, "_report", refuse)
    else:
        original = evidence._report

        def mutate(*args):
            output = original(*args)
            result.artifact.path.chmod(0o600)
            result.artifact.path.write_bytes(b"changed during export")
            return output

        monkeypatch.setattr(evidence, "_report", mutate)
    with pytest.raises(ContractError):
        evidence.export_package(record, tmp_path / "package")
    assert record.read_bytes() == before
    for name in ("receipt.intoto.jsonl", "outputs", "evidence"):
        assert not (tmp_path / "package" / name).exists()
    assert not list((tmp_path / "package").glob(".apmx-receipt-*"))


def test_rejected_attempts_become_byproducts_in_evidence(caller, monkeypatch) -> None:
    path = budgeted_contract(
        caller,
        {"max_attempts": 2, "max_seconds": 30},
        check="from pathlib import Path; assert Path('answer.txt').read_text() == 'good'",
    )
    calls = producer(
        monkeypatch,
        body=lambda *_: (
            "from pathlib import Path\n"
            f"Path('answer.txt').write_text({'good' if len(calls) == 2 else 'bad'!r})"
        ),
    )
    result = run(path, caller)
    controller = result.controller.path
    before = controller.read_bytes()
    bundle = evidence.export_package(controller, caller / "package")
    assert controller.read_bytes() == before
    statements = _statements(bundle)
    assert [item["predicateType"] for item in statements].count(verifier.TEST_RESULT) == 1
    provenance = statements[0]["predicate"]
    stage = provenance["buildDefinition"]["internalParameters"]["stages"][0]
    assert stage["attempts"] == 2
    rejected = [
        item
        for item in provenance["runDetails"]["byproducts"]
        if verifier.role(item) == "rejected-attempt"
    ]
    assert len(rejected) == 1
    record = json.loads(
        (caller / "package/evidence/sha256" / rejected[0]["digest"]["sha256"]).read_bytes()
    )
    assert record["outcome"] == "REJECTED"
    assert [check["result"] for check in record["checks"]] == ["FAILED"]
    (output,) = record["outputs"]
    bad = caller / "package/evidence/sha256" / output["digest"]["sha256"]
    assert bad.read_bytes() == b"bad"
    assert (caller / "package/outputs/answer.txt").read_bytes() == b"good"
    receipt, failure = verifier.inspect(bundle)
    assert failure is None and receipt.facts["rejectedAttempts"] == 1


def test_git_tree_matches_git_write_tree(tmp_path) -> None:
    import subprocess

    files = {
        "a.txt": ("100644", b"one\n"),
        "dir/b.py": ("100755", b"#!/bin/sh\n"),
        "dir/sub/c": ("100644", b""),
        "dir.txt": ("100644", b"sorts after dir/"),
    }
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    for name, (mode, raw) in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        target.chmod(0o755 if mode == "100755" else 0o644)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    # Windows Git ignores the file system's executable bit; set the mode explicitly.
    subprocess.run(["git", "update-index", "--chmod=+x", "dir/b.py"], cwd=repo, check=True)
    tree = subprocess.run(
        ["git", "write-tree"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert evidence.git_tree(files) == tree


def test_base_commit_from_ci_is_recorded(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APMX_BASE_REPOSITORY", "acme/shop")
    monkeypatch.setenv("APMX_BASE_COMMIT", "a" * 40)
    _, result = _execute(tmp_path, monkeypatch)
    bundle = evidence.export_package(result.run_directory / "record.json", result.run_directory)
    base = _statements(bundle)[0]["predicate"]["buildDefinition"]["resolvedDependencies"][0]
    assert base["uri"] == f"git+https://github.com/acme/shop@{'a' * 40}"
    assert base["digest"]["gitCommit"] == "a" * 40 and len(base["digest"]["gitTree"]) == 40


def _independent_verifier():
    script = Path(__file__).resolve().parents[3] / "scripts/verify_evidence.py"
    spec = importlib.util.spec_from_file_location("verify_evidence_under_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, module.BUNDLED_SCHEMAS


def test_independent_verifier_accepts_a_dependency_free_receipt(tmp_path, monkeypatch) -> None:
    independent, schemas = _independent_verifier()
    _, result = _execute(tmp_path, monkeypatch)
    bundle = evidence.export_package(result.run_directory / "record.json", tmp_path / "receipt")
    report = independent.verify(bundle, schemas, require_evidence=True)
    assert report["status"] == "passed" and report["capabilities"] == 0
    relocated = tmp_path / "elsewhere"
    shutil.copytree(tmp_path / "receipt", relocated)
    assert independent.verify(relocated, schemas)["status"] == "passed"
    blob = next((relocated / "evidence/sha256").iterdir())
    blob.chmod(0o600)
    blob.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="does not match its name"):
        independent.verify(relocated, schemas)
