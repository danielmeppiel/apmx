"""Retained package-resolution bytes must survive receipt issuance and reuse."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_chain import caller, producer
from test_chain_sources import package, prepared, private_preparation

from apmx.contracts import engine, records, workspace
from apmx.contracts.models import (
    ContractError,
    ContractLimits,
    FileEntry,
    LeafPlan,
    Outcome,
    RetainedInput,
    RunResult,
)
from apmx.core.contract_logger import ContractLogger
from apmx.deps.lockfile import LockFile

__all__ = ["caller", "private_preparation"]

pytestmark = pytest.mark.component

PROVENANCE_FILES = (
    "apm.yml",
    "apm.lock.yaml",
    "imports-apm.yml",
    "consumer-apm.yml",
    "consumer-apm.lock.yaml",
    "package-resolution-apm.lock.yaml",
    "original-apm.yml",
    "original-apm.lock.yaml",
    "contract.contract.md",
)


@pytest.fixture
def retained_package(
    caller: Path,
    monkeypatch: pytest.MonkeyPatch,
    private_preparation: None,
) -> tuple[LeafPlan, RunResult, RetainedInput]:
    root = package(caller)
    LockFile().write(root / "apm.lock.yaml")
    (caller / "apm.yml").write_text("name: consumer\nversion: 1.0.0\n", encoding="ascii")
    LockFile().write(caller / "apm.lock.yaml")
    calls = producer(monkeypatch)
    with prepared(root, caller) as closure:
        node = closure.nodes[0]
        leaf = replace(node.plan, input_inventory=node.inventory)
        result = engine.run_contract(leaf, logger=ContractLogger(), allow_advisory=True)
        receipt = records.admit_handoff(leaf, result, allow_unproven=True)
    assert len(calls) == 1 and not leaf.source.root.exists()
    data = json.loads(receipt.record_path.read_bytes())
    assert set(data["source"]["retained"]) == set(PROVENANCE_FILES)
    return leaf, result, receipt


@pytest.mark.parametrize("reuse", (False, True))
@pytest.mark.parametrize("fault", ("missing", "changed"))
def test_every_retained_metadata_copy_is_required(
    retained_package: tuple[LeafPlan, RunResult, RetainedInput],
    monkeypatch: pytest.MonkeyPatch,
    reuse: bool,
    fault: str,
) -> None:
    """Inject faults only at the canonical read boundary; leave all bytes intact."""
    leaf, result, receipt = retained_package
    source = result.run_directory / "source"
    before = {name: (source / name).read_bytes() for name in PROVENANCE_FILES}
    original = workspace._read
    for selected in PROVENANCE_FILES:
        observed = []

        def affected(
            root: Path, name: str, maximum: int, *, selected=selected, observed=observed
        ) -> tuple[bytes, FileEntry]:
            if root == source and name == selected:
                observed.append(name)
                if fault == "missing":
                    raise FileNotFoundError(name)
                raw, entry = original(root, name, maximum)
                altered = raw + b"\nchanged"
                return altered, replace(
                    entry,
                    sha256=hashlib.sha256(altered).hexdigest(),
                    size=len(altered),
                )
            return original(root, name, maximum)

        with monkeypatch.context() as patch:
            patch.setattr(workspace, "_read", affected)
            with pytest.raises(ContractError) as rejected:
                if reuse:
                    records.validate_binding(receipt, leaf.project_root, leaf.limits)
                else:
                    records.admit_handoff(leaf, result, allow_unproven=True)
            assert rejected.value.outcome == Outcome.HALTED
            assert observed == [selected], selected
        records.validate_binding(receipt, leaf.project_root, leaf.limits)
    assert before == {name: (source / name).read_bytes() for name in PROVENANCE_FILES}


@pytest.mark.parametrize(
    "fault", ("removed", "removed-both", "duplicate", "retargeted", "wrong-hash")
)
def test_record_cannot_redefine_captured_provenance(
    retained_package: tuple[LeafPlan, RunResult, RetainedInput],
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    leaf, result, _ = retained_package
    original = records._record_bytes

    def altered(path: Path, limits: ContractLimits) -> tuple[dict, str]:
        data, digest = original(path, limits)
        source = data["source"]
        identities = source["retained_identities"]
        entry = next(item for item in identities if item["relative_path"] == "original-apm.yml")
        if fault in ("removed", "removed-both"):
            identities.remove(entry)
            del source["retained"]["original-apm.yml"]
            if fault == "removed-both":
                data["result"]["retained_provenance"] = identities
        elif fault == "duplicate":
            identities.append(entry.copy())
        elif fault == "retargeted":
            source["retained"]["original-apm.yml"] = str(leaf.project_root / "apm.yml")
        else:
            entry["sha256"] = "0" * 64
        return data, digest

    monkeypatch.setattr(records, "_record_bytes", altered)
    with pytest.raises(ContractError):
        records.admit_handoff(leaf, result, allow_unproven=True)


@pytest.mark.parametrize("reuse", (False, True))
@pytest.mark.parametrize("fault", ("missing", "changed"))
def test_actual_retained_metadata_drift_refuses(
    retained_package: tuple[LeafPlan, RunResult, RetainedInput],
    reuse: bool,
    fault: str,
) -> None:
    """Alter only this test's copies, preserving size for the changed-byte case."""
    leaf, result, receipt = retained_package
    for name in PROVENANCE_FILES:
        path = result.run_directory / "source" / name
        raw = path.read_bytes()
        saved = path.with_name(name + ".saved")
        try:
            if fault == "missing":
                path.rename(saved)
            else:
                path.chmod(0o600)
                path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
            with pytest.raises(ContractError) as rejected:
                if reuse:
                    records.validate_binding(receipt, leaf.project_root, leaf.limits)
                else:
                    records.admit_handoff(leaf, result, allow_unproven=True)
            assert rejected.value.outcome == Outcome.HALTED
        finally:
            if fault == "missing":
                saved.rename(path)
            else:
                path.write_bytes(raw)
                path.chmod(0o400)
        records.validate_binding(receipt, leaf.project_root, leaf.limits)


def test_windows_readonly_mode_reporting_preserves_valid_receipt(
    retained_package: tuple[LeafPlan, RunResult, RetainedInput],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leaf, result, receipt = retained_package
    original = workspace._read

    def windows_mode(root: Path, name: str, maximum: int) -> tuple[bytes, FileEntry]:
        raw, entry = original(root, name, maximum)
        if root == result.run_directory / "source":
            entry = replace(entry, mode=0o444)
        return raw, entry

    monkeypatch.setattr(workspace, "_read", windows_mode)
    assert records.admit_handoff(leaf, result, allow_unproven=True) == receipt
    records.validate_binding(receipt, leaf.project_root, leaf.limits)
