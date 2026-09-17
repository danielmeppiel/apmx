"""Opaque complete-result delivery through the actual leaf engine and factory."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_chain import caller, producer, write_contract

from apmx.contracts import chain, frontend, records, resolution, workspace
from apmx.contracts.models import (
    Artifact,
    ArtifactSet,
    ContractError,
    ContractLimits,
    Outcome,
    artifact_files,
)
from apmx.core.contract_logger import ContractLogger

__all__ = ["caller"]

pytestmark = pytest.mark.component


def make_plan(root: Path, *, allow: bool = True):
    return resolution.preflight(
        resolution.resolve_factory(root),
        root,
        harness="copilot",
        allow_unproven_inputs=allow,
    )


def script(payloads: dict[str, bytes], *, missing: str | None = None):
    def build(plan, snapshot, directory):
        return (
            "from pathlib import Path\n"
            "Path('scratch.txt').write_bytes(b'not an artifact')\n"
            + "\n".join(
                f"Path({name!r}).parent.mkdir(parents=True, exist_ok=True); Path({name!r}).write_bytes({payloads[name]!r})"
                for name in plan.contract.outputs
                if name != missing
            )
        )

    return build


@pytest.mark.parametrize("allow", [True, False])
def test_mixed_binary_subset_fanout_uses_shared_producer_once(caller, monkeypatch, allow):
    values = {
        "changes.diff": b"exact diff\r\n",
        "implementation.md": b"report\n",
        "app.exe": b"MZ\0\xff\x80",
        "app.dmg": b"\xfe\x00\x81",
        "left.md": b"left",
        "right.md": b"right",
    }
    names = tuple(list(values)[:4])
    write_contract(caller, "z-plan.contract.md", ("seed.txt",), json.dumps(names))
    write_contract(caller, "b-check.contract.md", ("app.exe", "implementation.md"), "left.md")
    write_contract(caller, "a-review.contract.md", ("app.dmg", "changes.diff"), "right.md")
    for name in names:
        (caller / name).write_bytes(b"stale caller bytes")
    calls = producer(monkeypatch, body=script(values))
    result = chain.run_chain(
        make_plan(caller, allow=allow), logger=ContractLogger(), allow_advisory=True
    )
    assert result.outcome == (Outcome.COMPLETE if allow else Outcome.UNPROVEN)
    assert result.complete is allow
    assert len(calls) == (3 if allow else 1)
    delivery = result.runs[0].artifact
    assert isinstance(delivery, ArtifactSet)
    assert [item.relative_path for item in delivery.files] == list(names)
    assert delivery.sha256 == workspace.artifact_inventory_digest(delivery.files)
    assert all(check.subject_digest == delivery.sha256 for check in result.runs[0].checks)
    document = json.loads((result.runs[0].run_directory / "record.json").read_bytes())
    assert document["schema"] == "apm-contract-run/0.3"
    assert set(document["artifact"]) == {"files", "sha256"}
    assert all((caller / name).read_bytes() == b"stale caller bytes" for name in names)
    if allow:
        for selected, snapshot, _ in calls[1:]:
            for name in selected.contract.needs:
                assert (snapshot.root / name).read_bytes() == values[name]
            assert not (snapshot.root / "scratch.txt").exists()
            for name in set(names) - set(selected.contract.needs):
                assert not (snapshot.root / name).exists()
        view = result.record_path.parent / "artifacts"
        assert all((view / name).read_bytes() == raw for name, raw in values.items())
        assert not (view / "scratch.txt").exists()


@pytest.mark.parametrize("output", ["note.md", ["note.md"]])
def test_scalar_and_single_member_list_have_explicit_record_shapes(caller, monkeypatch, output):
    write_contract(caller, "job.contract.md", ("seed.txt",), json.dumps(output))
    producer(monkeypatch, body=script({"note.md": b"\xff\0\r\n"}))
    plan = make_plan(caller)
    result = chain.run_chain(plan, logger=ContractLogger(), allow_advisory=True)
    assert result.complete
    scalar = isinstance(output, str)
    assert isinstance(result.runs[0].artifact, Artifact if scalar else ArtifactSet)
    record = json.loads((result.runs[0].run_directory / "record.json").read_bytes())
    assert record["schema"] == "apm-contract-run/0.3"
    assert "native_exports" not in record["result"]
    assert ("output_files" not in record["limits"]) is scalar
    assert artifact_files(result.runs[0].artifact)[0].path.read_bytes() == b"\xff\0\r\n"
    if not scalar:
        with pytest.raises(ContractError, match="Multiple artifacts"):
            records.finalized_input(plan.nodes[0].plan, result.runs[0])


@pytest.mark.parametrize("fault", ["missing", "failed", "incomplete"])
def test_no_member_of_an_incomplete_result_advances(caller, monkeypatch, fault):
    write_contract(
        caller,
        "z.contract.md",
        ("seed.txt",),
        '["one.md", "two.md"]',
        check="raise SystemExit(1)"
        if fault == "failed"
        else "raise SystemExit(2)"
        if fault == "incomplete"
        else "pass",
    )
    write_contract(caller, "a.contract.md", ("one.md",), "final.md")
    calls = producer(
        monkeypatch,
        body=script(
            {"one.md": b"one", "two.md": b"two", "final.md": b"final"},
            missing="two.md" if fault == "missing" else None,
        ),
    )
    result = chain.run_chain(make_plan(caller), logger=ContractLogger(), allow_advisory=True)
    assert not result.complete and len(calls) == 1
    assert not (result.record_path.parent / "artifacts").exists()
    if fault == "missing":
        assert result.runs[0].artifact is None
        assert not (result.runs[0].run_directory / "artifacts").exists()


@pytest.mark.parametrize("missing", [False, True])
def test_subset_receipt_revalidates_the_other_retained_artifact(caller, monkeypatch, missing):
    write_contract(caller, "job.contract.md", ("seed.txt",), '["first.md", "other.exe"]')
    producer(monkeypatch, body=script({"first.md": b"ok", "other.exe": b"\xff\0"}))
    plan = make_plan(caller)
    result = chain.run_chain(plan, logger=ContractLogger(), allow_advisory=True)
    bindings = records.finalized_inputs(plan.nodes[0].plan, result.runs[0])
    other = bindings[1].artifact.path
    other.chmod(0o600)
    if missing:
        other.unlink()
    else:
        other.write_bytes(b"corrupt")
    with pytest.raises(ContractError):
        records.validate_binding(bindings[0], caller, ContractLimits())
    with pytest.raises(ContractError):
        records.admit_handoffs(plan.nodes[0].plan, result.runs[0], allow_unproven=True)


@pytest.mark.parametrize(
    "declaration",
    [
        [],
        ["x", "x"],
        ["x", "X"],
        ["x", "x/y"],
        ["../x"],
        ["/x"],
        ["checks/x"],
        ["x/", "y"],
        [True],
        [1],
        [None],
        [["nested"]],
        ["x"] * 17,
    ],
)
def test_list_declarations_keep_path_collision_and_count_guards(caller, declaration):
    contract = write_contract(caller, "job.contract.md", (), json.dumps(declaration))
    with pytest.raises(ContractError):
        frontend.plan_contract(contract, caller, harness="copilot")


@pytest.mark.parametrize("fault", ["per-file", "combined", "count", "write"])
def test_complete_capture_limits_and_atomic_publication(caller, monkeypatch, fault):
    write_contract(caller, "job.contract.md", ("seed.txt",), '["one", "two"]')
    plan = make_plan(caller).nodes[0].plan
    directory = caller / "owned-attempt"
    directory.mkdir()
    snapshot = workspace.capture_workspace(plan, directory)
    (snapshot.producer / "one").write_bytes(b"abc")
    (snapshot.producer / "two").write_bytes(b"def")
    limits = replace(
        plan.limits,
        output_bytes=2 if fault == "per-file" else 4,
        output_total_bytes=5 if fault == "combined" else 8,
        output_files=1 if fault == "count" else 2,
    )
    original = workspace._write

    def write(root, entry, raw):
        if fault == "write" and entry.relative_path == "two":
            raise OSError("publication failed")
        original(root, entry, raw)

    monkeypatch.setattr(workspace, "_write", write)
    with pytest.raises((ContractError, OSError)):
        workspace.capture_output(snapshot, plan.contract.produces, directory, limits)
    assert not (directory / "artifacts").exists()
    assert (snapshot.producer / "one").read_bytes() == b"abc"


@pytest.mark.parametrize(
    "fault",
    ["unknown-version", "missing-member", "wrong-type", "inventory", "subject", "missing-check"],
)
def test_strict_complete_record_shape_and_check_subject(caller, monkeypatch, fault):
    write_contract(caller, "job.contract.md", ("seed.txt",), '["one.md", "two.exe"]')
    producer(monkeypatch, body=script({"one.md": b"one", "two.exe": b"\xff"}))
    plan = make_plan(caller)
    chain_result = chain.run_chain(plan, logger=ContractLogger(), allow_advisory=True)
    result = chain_result.runs[0]
    path = result.run_directory / "record.json"
    data = json.loads(path.read_bytes())
    if fault == "unknown-version":
        data["schema"] = "apm-contract-run/9.9"
    elif fault == "missing-member":
        data["artifact"]["files"].pop()
        data["result"]["artifact"] = data["artifact"]
    elif fault == "wrong-type":
        data["artifact"]["files"][0]["relative_path"] = 123
        data["result"]["artifact"] = data["artifact"]
    elif fault == "inventory":
        data["artifact"]["sha256"] = "0" * 64
        data["result"]["artifact"] = data["artifact"]
    else:
        checks = (
            ()
            if fault == "missing-check"
            else (replace(result.checks[0], subject_digest=result.artifact.files[0].sha256),)
        )
        result = replace(result, checks=checks)
        data["result"] = records._json_value(result)
        data["checks"] = records._json_value(checks)
    path.write_text(json.dumps(data), encoding="ascii")
    with pytest.raises(ContractError):
        records.finalized_inputs(plan.nodes[0].plan, result)
    if fault not in {"subject", "missing-check"}:
        with pytest.raises(ContractError):
            records._retained_artifacts(data, result.run_directory, plan.nodes[0].plan.limits)
