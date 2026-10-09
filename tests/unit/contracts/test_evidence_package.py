"""Standards export is a read-only delivery, separate from execution acceptance."""

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest
from test_chain import caller, producer
from test_execution_result import _execute
from test_repair import budgeted_contract, run

from apmx.contracts import evidence, records
from apmx.contracts.models import ContractError

__all__ = ["caller"]
pytestmark = pytest.mark.component


def test_run_without_dependencies_exports_an_explicit_empty_inventory(
    tmp_path, monkeypatch
) -> None:
    _, result = _execute(tmp_path, monkeypatch)
    path = result.run_directory / "record.json"
    before = path.read_bytes()
    target = evidence.export_package(path, tmp_path / "receipt")
    assert path.read_bytes() == before
    assert records.load_completed_result(path) == result
    bom = json.loads((target / "abom.cdx.json").read_bytes())
    assert (bom["bomFormat"], bom["specVersion"]) == ("CycloneDX", "1.5")
    assert bom["components"] == [] and bom["dependencies"] == []
    properties = {item["name"]: item["value"] for item in bom["metadata"]["properties"]}
    assert properties["apmx:apm-dependencies"] == "0"
    assert "no APM dependencies" in properties["apmx:inventory-scope"]
    assert "timestamp" not in bom["metadata"] and "serialNumber" not in bom
    definition = json.loads((target / "definition.json").read_bytes())
    assert definition["resolvedDependencies"] == []
    assert json.loads((target / "capability-bindings.json").read_bytes()) == []
    summary = (target / "summary.md").read_text()
    assert summary.startswith("# APMX receipt")
    assert "APM dependencies: 0 (CycloneDX inventory lists zero components)" in summary
    index = json.loads((target / "index.json").read_bytes())
    for name, identity in index["files"].items():
        assert hashlib.sha256((target / name).read_bytes()).hexdigest() == identity["sha256"]
    assert {"provenance.intoto.json", "abom.cdx.json", "definition.json", "summary.md"} <= set(
        index["files"]
    )
    assert list((target / "checks").glob("*.intoto.json"))


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


def test_package_statements_bind_exact_files_and_relocate(tmp_path, monkeypatch) -> None:
    _, result = _execute(tmp_path, monkeypatch)
    path = result.run_directory / "record.json"
    before = path.read_bytes()
    bom = b'{"bomFormat":"CycloneDX","specVersion":"1.5","version":1,"components":[]}\n'
    monkeypatch.setattr(evidence, "_inventory", lambda *_: (bom, [], []))
    target = tmp_path / "package"
    evidence.export_package(path, target)
    assert path.read_bytes() == before
    assert (target / "abom.cdx.json").read_bytes() == bom
    package = json.loads((target / "index.json").read_bytes())
    assert package["schema"] == "apmx-evidence-package/1"
    for name, identity in package["files"].items():
        assert hashlib.sha256((target / name).read_bytes()).hexdigest() == identity["sha256"]
    provenance = json.loads((target / "provenance.intoto.json").read_bytes())
    assert provenance["_type"] == evidence.STATEMENT
    assert provenance["predicateType"] == evidence.PROVENANCE
    assert provenance["subject"][0]["digest"]["sha256"] == result.artifact.sha256
    checks = list((target / "checks").glob("*.intoto.json"))
    assert len(checks) == 1
    check = json.loads(checks[0].read_bytes())
    assert check["predicate"]["result"] == "PASSED"
    assert "passedTests" not in check["predicate"]
    assert check["subject"] == provenance["subject"]
    assert not list(target.rglob("transcript.log"))
    relocated = tmp_path / "elsewhere"
    shutil.copytree(target, relocated)
    for document in (provenance, check):
        for item in document["subject"]:
            assert (
                hashlib.sha256((relocated / item["name"]).read_bytes()).hexdigest()
                == item["digest"]["sha256"]
            )


def test_existing_destination_is_never_overwritten(tmp_path, monkeypatch) -> None:
    _, result = _execute(tmp_path, monkeypatch)
    target = tmp_path / "package"
    target.mkdir()
    (target / "user.txt").write_bytes(b"keep")
    with pytest.raises(ContractError):
        evidence.export_package(result.run_directory / "record.json", target)
    assert (target / "user.txt").read_bytes() == b"keep"


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
    )
    data["source"]["path"] = "/another/checkout/job.contract.md"
    path.write_text(json.dumps(data))
    assert evidence._definition((result,), []) == first
    contract = result.run_directory / "source/contract.contract.md"
    contract.chmod(0o600)
    contract.write_bytes(contract.read_bytes() + b"\nDifferent goal.\n")
    assert evidence._definition((result,), []) != first


def test_export_failure_does_not_publish_partial_package_or_change_result(
    tmp_path, monkeypatch
) -> None:
    _, result = _execute(tmp_path, monkeypatch)
    path = result.run_directory / "record.json"
    before = path.read_bytes()
    monkeypatch.setattr(evidence, "_inventory", lambda *_: (b"fixture", [], []))

    def fail(*_):
        raise ContractError("Retained report unavailable.", code="check_subject_unavailable")

    monkeypatch.setattr(evidence, "_report_subjects", fail)
    with pytest.raises(ContractError):
        evidence.export_package(path, tmp_path / "package")
    assert not (tmp_path / "package").exists()
    assert not list(tmp_path.glob(".apmx-evidence-*"))
    assert path.read_bytes() == before


def test_rejected_attempts_survive_export_without_private_diagnostics(caller, monkeypatch) -> None:
    path = budgeted_contract(
        caller,
        {"max_attempts": 2, "max_seconds": 30},
        check=(
            "from pathlib import Path; "
            "print('PRIVATE_' + 'CHECK_DIAGNOSTIC'); "
            "assert Path('answer.txt').read_text() == 'good'"
        ),
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
    retained = json.loads((result.run_directory / "record.json").read_bytes())
    assert "PRIVATE_CHECK_DIAGNOSTIC" in json.dumps(retained["baseline"]["repair"])
    monkeypatch.setattr(evidence, "_inventory", lambda *_: (b"fixture", [], []))
    target = evidence.export_package(controller, caller / "package")
    assert controller.read_bytes() == before
    index = json.loads((target / "index.json").read_bytes())
    assert [(row["outcome"], row["selected"]) for row in index["attempts"]] == [
        ("REJECTED", False),
        ("COMPLETE", True),
    ]
    assert len(index["production"]) == len(index["checks"]) == 2
    for row in index["attempts"]:
        projection = json.loads((target / f"attempts/{row['runId']}/execution.json").read_bytes())
        assert "baseline.repair.diagnostics" in projection["omitted"]
        assert "PRIVATE_CHECK_DIAGNOSTIC" not in json.dumps(projection)
    assert not list(target.rglob("transcript.log"))
    assert not list(target.rglob("record.json"))
    check_outcomes = [
        json.loads((target / ref["name"]).read_bytes())["predicate"]["result"]
        for ref in index["checks"]
    ]
    assert check_outcomes == ["FAILED", "PASSED"]


@pytest.mark.parametrize("fault", ["write", "publish", "changed-source"])
def test_export_failure_does_not_mutate_canonical_completion(tmp_path, monkeypatch, fault) -> None:
    _, result = _execute(tmp_path, monkeypatch)
    record = result.run_directory / "record.json"
    before = record.read_bytes()
    monkeypatch.setattr(evidence, "_inventory", lambda *_: (b"fixture", [], []))

    def failure(*_, **__):
        raise OSError("injected delivery error")

    if fault == "write":
        monkeypatch.setattr(evidence.os, "fsync", failure)
    elif fault == "publish":
        monkeypatch.setattr(evidence.os, "rename", failure)
    else:
        original = evidence._report_subjects

        def mutate(*args):
            output = original(*args)
            result.artifact.path.chmod(0o600)
            result.artifact.path.write_bytes(b"changed during export")
            return output

        monkeypatch.setattr(evidence, "_report_subjects", mutate)
    with pytest.raises(ContractError):
        evidence.export_package(record, tmp_path / "package")
    assert record.read_bytes() == before
    assert not (tmp_path / "package").exists()
    assert not list(tmp_path.glob(".apmx-evidence-*"))


def _independent_verifier():
    script = Path(__file__).resolve().parents[3] / "scripts/verify_evidence.py"
    spec = importlib.util.spec_from_file_location("verify_evidence_under_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, module.BUNDLED_SCHEMAS


def test_independent_verifier_accepts_a_dependency_free_receipt(tmp_path, monkeypatch) -> None:
    verifier, schemas = _independent_verifier()
    _, result = _execute(tmp_path, monkeypatch)
    target = evidence.export_package(result.run_directory / "record.json", tmp_path / "receipt")
    report = verifier.verify(target, schemas)
    assert report["status"] == "passed" and report["capabilities"] == 0
    (target / "abom.cdx.json").chmod(0o600)
    (target / "abom.cdx.json").write_bytes(b'{"bomFormat":"CycloneDX"}\n')
    with pytest.raises(ValueError, match="File hash mismatch"):
        verifier.verify(target, schemas)
