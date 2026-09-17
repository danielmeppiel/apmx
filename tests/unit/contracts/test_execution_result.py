"""Operational completion is exact retained evidence, never native isolation."""

import hashlib
import json
import sys
from contextlib import contextmanager
from dataclasses import replace

import pytest
from click.testing import CliRunner
from test_chain import prepare, producer, two_nodes, write_contract
from test_engine import _fake_adapter, _plan, _python_check

from apmx.cli import main
from apmx.contracts import chain, engine, records, resolution
from apmx.contracts.models import ContractError, ContractSource, Outcome
from apmx.core.contract_logger import ContractLogger


def _complete_plan(tmp_path):
    plan = _plan(tmp_path, (_python_check("acceptance", "raise SystemExit(0)"),))
    plan.contract.path.write_text(
        "---\nneeds: input.txt\nproduces: result.txt\nverify:\n  acceptance: "
        + json.dumps(plan.contract.checks[0].command)
        + "\n---\nWrite the result.\n"
    )
    return replace(
        plan,
        contract=replace(
            plan.contract,
            source_digest=hashlib.sha256(plan.contract.path.read_bytes()).hexdigest(),
        ),
    )


def _execute(tmp_path, monkeypatch):
    plan = _complete_plan(tmp_path)
    _fake_adapter(monkeypatch, plan)
    return plan, engine.run_contract(plan, logger=ContractLogger(), allow_advisory=True)


def test_real_execution_completes_without_becoming_strictly_trusted(tmp_path, monkeypatch):
    plan, result = _execute(tmp_path, monkeypatch)
    assert result.outcome.name == "COMPLETE"
    assert int(result.outcome) == 0
    record = json.loads((result.run_directory / "record.json").read_bytes())
    assert record["schema"] == "apm-contract-run/0.3"
    assert record["execution"] == {"name": "COMPLETE", "exit_code": 0}
    assert record["assurance"]["isolation"] == "unavailable"
    assert record["native_completion_observed"] is True
    assert records.finalized_inputs(plan, result)
    with pytest.raises(ContractError, match="strict"):
        records.admit_handoffs(plan, result, allow_unproven=False)
    assert records.admit_handoffs(plan, result, allow_unproven=True)


@pytest.mark.parametrize("schema", ["apm-contract-run/0.1", "apm-contract-run/0.2", "unknown"])
def test_legacy_and_unknown_records_fail_closed_without_rewriting(tmp_path, monkeypatch, schema):
    plan, result = _execute(tmp_path, monkeypatch)
    path = result.run_directory / "record.json"
    data = json.loads(path.read_bytes())
    data["schema"] = schema
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    with pytest.raises(ContractError) as error:
        records.finalized_inputs(plan, result)
    assert error.value.code == "record_version"
    assert path.read_bytes() == before
    with pytest.raises(ContractError) as cleanup_error:
        records.halt_chain(result, "source_cleanup")
    assert cleanup_error.value.code == "record_version"
    assert path.read_bytes() == before


@pytest.mark.parametrize("fault", ["missing", "extra", "duplicate", "partial", "raw-zero"])
def test_inexact_checks_never_promote(tmp_path, monkeypatch, fault):
    plan = _plan(
        tmp_path,
        (
            _python_check("first", "raise SystemExit(0)"),
            _python_check("last", "raise SystemExit(0)"),
        ),
    )
    _fake_adapter(monkeypatch, plan)
    original = engine._run_checks

    def altered(*args, **kwargs):
        reason = original(*args, **kwargs)
        checks = args[-1]
        if fault == "missing":
            checks.clear()
        elif fault == "extra":
            checks.append(replace(checks[-1], name="undeclared"))
        elif fault == "duplicate":
            checks.append(checks[-1])
        elif fault == "partial":
            checks.pop()
        else:
            checks[-1] = replace(checks[-1], normalized=2)
        return reason

    monkeypatch.setattr(engine, "_run_checks", altered)
    if fault in ("extra", "duplicate", "partial"):
        with pytest.raises(ContractError):
            engine.run_contract(plan, logger=ContractLogger(), allow_advisory=True)
        path = next((tmp_path / ".apm/runs").glob("*/record.json"))
        data = json.loads(path.read_bytes())
        assert data["complete"] is False
        assert data["phase"] == "finalization_failed"
        assert data["execution"]["exit_code"] != 0
        assert data["validation_error"]["code"]
        if data["execution"]["name"] == "UNPROVEN":
            assert data["result"]["stop_reason"] is None
    else:
        result = engine.run_contract(plan, logger=ContractLogger(), allow_advisory=True)
        assert int(result.outcome) != 0


def test_promotion_persistence_failure_cannot_announce_complete(tmp_path, monkeypatch, capsys):
    write = records.AttemptStore._write

    def fail_complete(self):
        outcome = getattr(self._data.get("result"), "outcome", None)
        if outcome is not None and outcome.name == "COMPLETE":
            raise OSError("final completion sync failed")
        write(self)

    monkeypatch.setattr(records.AttemptStore, "_write", fail_complete)
    with pytest.raises(ContractError):
        _execute(tmp_path, monkeypatch)
    assert "Contract COMPLETE" not in capsys.readouterr().out
    record = next((tmp_path / ".apm/runs").glob("*/record.json"))
    data = json.loads(record.read_bytes())
    assert data["complete"] is False
    assert data["result"]["outcome"]["name"] == "HALTED"


def test_outcome_zero_has_no_legacy_verified_alias():
    assert Outcome(0).name == "COMPLETE"
    assert "VERIFIED" not in Outcome.__members__


def test_raw_numeric_zero_cannot_authorize_a_native_handoff(tmp_path, monkeypatch):
    plan, result = _execute(tmp_path, monkeypatch)
    untyped = replace(result, outcome=0)
    path = result.run_directory / "record.json"
    data = json.loads(path.read_bytes())
    data["result"] = records._json_value(untyped)
    data["execution"] = 0
    path.write_text(json.dumps(data))
    for allowed in (False, True):
        with pytest.raises(ContractError):
            records.admit_handoffs(plan, untyped, allow_unproven=allowed)


@pytest.mark.parametrize("stage", ["imports", "package"])
@pytest.mark.parametrize("filesystem", [False, True])
def test_preparation_cleanup_failure_is_recorded_before_cli_success(
    tmp_path, monkeypatch, stage, filesystem
):
    plan = _plan(tmp_path, (_python_check("acceptance", "raise SystemExit(0)"),))
    _fake_adapter(monkeypatch, plan)
    monkeypatch.chdir(tmp_path)

    def fail():
        if filesystem:
            raise OSError("cleanup failed")
        raise ContractError("cleanup failed", code="source_cleanup")

    @contextmanager
    def imports(*args, **kwargs):
        yield None, None
        if stage == "imports":
            fail()

    @contextmanager
    def package(*args, **kwargs):
        yield ContractSource(tmp_path, "job.contract.md")
        if stage == "package":
            fail()

    monkeypatch.setattr("apmx.install.contract_source.prepare_imports", imports)
    monkeypatch.setattr("apmx.cli.prepare_contract_source", package)
    result = CliRunner().invoke(
        main,
        [
            "job.contract.md",
            "--on",
            "copilot",
            "--allow-host-access",
            *(["--from", "local-fixture"] if stage == "package" else []),
        ],
    )
    assert result.exit_code == 22, result.output
    assert "Contract COMPLETE" not in result.output
    path = next((tmp_path / ".apm/runs").glob("*/record.json"))
    record = json.loads(path.read_bytes())
    assert record["execution"] == {"name": "HALTED", "exit_code": 22}
    assert record["result"]["stop_reason"]


@pytest.mark.parametrize(
    "fault", ["producer", "native-completion", "consent", "assurance", "unknown-outcome"]
)
def test_completion_requires_all_admitted_observations(tmp_path, monkeypatch, fault):
    original = records._record_bytes

    def corrupted(path, limits):
        data, digest = original(path, limits)
        if data.get("phase") == "finished":
            if fault == "producer":
                data["producer"]["cleanup_confirmed"] = False
            elif fault == "native-completion":
                data["native_completion_observed"] = False
            elif fault == "consent":
                data["advisory_consent"] = None
            elif fault == "assurance":
                data["assurance"]["isolation"] = "trusted"
            else:
                data["result"]["outcome"] = {"name": "VERIFIED", "exit_code": 0}
        return data, digest

    monkeypatch.setattr(records, "_record_bytes", corrupted)
    with pytest.raises(ContractError):
        _execute(tmp_path, monkeypatch)
    path = next((tmp_path / ".apm/runs").glob("*/record.json"))
    record = json.loads(path.read_bytes())
    assert record["complete"] is False
    assert record["execution"]["name"] == "HALTED"


@pytest.mark.parametrize("verbose", [False, True])
def test_actual_completed_cli_has_one_disclosure_and_evidence_hierarchy(
    tmp_path, monkeypatch, verbose
):
    plan = _complete_plan(tmp_path)
    _fake_adapter(monkeypatch, plan)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        main,
        [
            "job.contract.md",
            "--on",
            "copilot",
            "--allow-host-access",
            *(["--verbose"] if verbose else []),
        ],
    )
    assert result.exit_code == 0, result.output
    text = result.output
    assert text.count("[i] Execution: local (not sandboxed)") == 1
    assert text.index("host files") < text.index("Contract 1/1:")
    assert text.index("Model usage may cost money") < text.index("Contract 1/1:")
    assert "Produces: result.txt" in text
    assert text.index("Checks:") < text.index("    [+] PASS acceptance")
    assert "[+] Contract COMPLETE" in text
    assert "Contract: 1/1 completed" in text
    assert "Check: 1/1 passed\nEvidence:" in text
    assert "Artifacts: 1 file retained" in text
    assert "Directory: .apm/runs/" in text
    assert "Record: .apm/runs/" in text
    assert ("Copilot > Done" in text) is verbose
    assert "\n\n\n" not in text and text.isascii()
    assert "UNPROVEN" not in text and "production certification" not in text
    assert "stopped" not in text and "Resolve the reported error" not in text


@pytest.mark.parametrize(
    "fault",
    [
        "child",
        "nodes",
        "node-state",
        "record",
        "duplicate-node",
        "reorder",
        "substitute-result",
        "duplicate-run",
    ],
)
def test_chain_finalization_refuses_incoherent_completion(tmp_path, monkeypatch, capsys, fault):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("apmx.runtime.utils.find_runtime_binary", lambda _: sys.executable)
    (tmp_path / "seed.txt").write_text("seed")
    two_nodes(tmp_path)
    producer(monkeypatch)
    original = records.ChainStore.finish_chain

    def corrupted(self, result, nodes):
        if fault == "child":
            result = replace(
                result, runs=(replace(result.runs[0], outcome=Outcome.UNPROVEN), *result.runs[1:])
            )
        elif fault == "nodes":
            nodes = nodes[:-1]
        elif fault == "node-state":
            nodes = [{**nodes[0], "state": "running"}, *nodes[1:]]
        elif fault == "duplicate-node":
            nodes = [nodes[0], nodes[0]]
        elif fault == "reorder":
            nodes = list(reversed(nodes))
        elif fault == "substitute-result":
            nodes = [{**nodes[0], "result": result.runs[1]}, *nodes[1:]]
        elif fault == "duplicate-run":
            result = replace(result, runs=(result.runs[0], result.runs[0]))
        original(self, result, nodes)

    monkeypatch.setattr(records.ChainStore, "finish_chain", corrupted)
    read = records._record_bytes

    def corrupted_record(path, limits):
        data, digest = read(path, limits)
        if fault == "record" and data["schema"] == records.CHAIN_SCHEMA:
            data["execution"] = {"name": "UNKNOWN", "exit_code": 0}
        return data, digest

    monkeypatch.setattr(records, "_record_bytes", corrupted_record)
    with pytest.raises(ContractError) as failure:
        chain.run_chain(prepare(tmp_path), logger=ContractLogger(), allow_advisory=True)
    assert failure.value.code == "chain_finalization_failure"
    assert "Factory COMPLETE" not in capsys.readouterr().out
    path = next((tmp_path / ".apm/chains").glob("*/record.json"))
    data = json.loads(path.read_bytes())
    assert data["complete"] is False and data["phase"] == "finalization_failed"
    assert data["execution"] == {"name": "HALTED", "exit_code": 22}


@pytest.mark.parametrize("verbose", [False, True])
def test_duplicate_contract_basenames_remain_unambiguous(tmp_path, monkeypatch, capsys, verbose):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("apmx.runtime.utils.find_runtime_binary", lambda _: sys.executable)
    (tmp_path / "seed.txt").write_text("seed")
    write_contract(tmp_path, "planning.contract.md", ("seed.txt",), "first.txt")
    write_contract(tmp_path, "contracts/planning.contract.md", ("seed.txt",), "second.txt")
    producer(monkeypatch)
    plan = resolution.preflight(
        resolution.resolve_factory(tmp_path),
        tmp_path,
        harness="copilot",
        allow_unproven_inputs=True,
    )
    logger = ContractLogger(verbose=verbose)
    result = chain.run_chain(plan, logger=logger, allow_advisory=True)
    assert result.outcome is Outcome.COMPLETE
    text = capsys.readouterr().out
    for index, node in enumerate(plan.nodes, 1):
        identity = (
            node.plan.contract.path.relative_to(tmp_path).as_posix().removesuffix(".contract.md")
        )
        assert f"Contract {index}/2: {identity}" in text


@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt])
@pytest.mark.parametrize("stage", ["validation", "first-transcript", "complete-transcript"])
def test_all_finalization_inspection_failures_repair_the_record(
    tmp_path, monkeypatch, capsys, error_type, stage
):
    from apmx.contracts import workspace

    target = records if stage == "validation" else workspace
    name = "finalized_inputs" if stage == "validation" else "inspect_retained_log"
    original = getattr(target, name)
    calls = 0

    def fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == (3 if stage == "complete-transcript" else 1):
            raise error_type("injected inspection failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(target, name, fail)
    with pytest.raises(ContractError) as error:
        _execute(tmp_path, monkeypatch)
    assert error.value.code == "finalization_failure"
    assert isinstance(error.value.__cause__, error_type)
    data = json.loads(next((tmp_path / ".apm/runs").glob("*/record.json")).read_bytes())
    assert data["complete"] is False and data["phase"] == "finalization_failed"
    assert data["execution"] == {"name": "HALTED", "exit_code": 22}
    assert data["result"]["stop_reason"] == "finalization_failure"
    assert "Contract COMPLETE" not in capsys.readouterr().out


def _cli_fixture(tmp_path, monkeypatch, factory):
    monkeypatch.chdir(tmp_path)
    if factory:
        monkeypatch.setattr("apmx.runtime.utils.find_runtime_binary", lambda _: sys.executable)
        (tmp_path / "seed.txt").write_text("seed")
        two_nodes(tmp_path)
        producer(monkeypatch)
        return [".", "--allow-unproven-inputs"]
    _fake_adapter(monkeypatch, _complete_plan(tmp_path))
    return ["job.contract.md"]


@pytest.mark.parametrize("stage", ["imports", "package"])
@pytest.mark.parametrize("fault", ["transcript", "artifact", "record"])
def test_normal_preparation_teardown_cannot_invalidate_completed_evidence(
    tmp_path, monkeypatch, stage, fault
):
    args = _cli_fixture(tmp_path, monkeypatch, False)

    def change():
        directory = next((tmp_path / ".apm/runs").glob("*"))
        if fault == "record":
            path = directory / "record.json"
            data = json.loads(path.read_bytes())
            data["assurance"]["isolation"] = "changed"
            path.write_text(json.dumps(data))
        else:
            path = directory / (
                "transcript.log" if fault == "transcript" else "artifacts/result.txt"
            )
            path.chmod(0o600)
            path.write_bytes(path.read_bytes() + b"changed during successful cleanup")

    @contextmanager
    def imports(*args, **kwargs):
        yield None, None
        if stage == "imports":
            change()

    @contextmanager
    def package(*args, **kwargs):
        yield ContractSource(tmp_path, "job.contract.md")
        change()

    monkeypatch.setattr("apmx.install.contract_source.prepare_imports", imports)
    monkeypatch.setattr("apmx.cli.prepare_contract_source", package)
    result = CliRunner().invoke(
        main,
        [
            *args,
            "--on",
            "copilot",
            "--allow-host-access",
            *(["--from", "fixture"] if stage == "package" else []),
        ],
    )
    assert result.exit_code == 22, result.output
    assert "Contract COMPLETE" not in result.output
    data = json.loads(next((tmp_path / ".apm/runs").glob("*/record.json")).read_bytes())
    assert data["execution"]["name"] == "HALTED"


@pytest.mark.parametrize("failure", [False, True])
def test_factory_complete_headline_waits_for_shared_cleanup(tmp_path, monkeypatch, failure):
    args = _cli_fixture(tmp_path, monkeypatch, True)

    @contextmanager
    def imports(*args, **kwargs):
        yield None, None
        if failure:
            raise ContractError("original cleanup failure", code="source_cleanup")

    monkeypatch.setattr("apmx.install.contract_source.prepare_imports", imports)
    result = CliRunner().invoke(
        main, [*args, "--on", "copilot", "--allow-host-access", "--verbose"]
    )
    assert result.exit_code == (22 if failure else 0), result.output
    assert "Contract COMPLETE" not in result.output
    assert result.output.count("Factory COMPLETE") == (0 if failure else 1)
    assert "PASS exact" in result.output


@pytest.mark.parametrize("factory,fault", [(False, "result"), (True, "result"), (True, "nodes")])
def test_malformed_teardown_record_preserves_original_error_without_overwriting(
    tmp_path, monkeypatch, factory, fault
):
    args = _cli_fixture(tmp_path, monkeypatch, factory)
    malformed = None

    @contextmanager
    def imports(*args, **kwargs):
        nonlocal malformed
        yield None, None
        family = "chains" if factory else "runs"
        path = next((tmp_path / ".apm" / family).glob("*/record.json"))
        data = json.loads(path.read_bytes())
        if fault == "result":
            del data["result"]
        else:
            data["nodes"] = {"malformed": True}
        malformed = json.dumps(data)
        path.write_text(malformed)
        raise ContractError("original cleanup failure", code="source_cleanup")

    monkeypatch.setattr("apmx.install.contract_source.prepare_imports", imports)
    result = CliRunner().invoke(main, [*args, "--on", "copilot", "--allow-host-access"])
    assert result.exit_code == 22, result.output
    assert "original cleanup failure" in result.output
    assert "persistence is unconfirmed" in result.output
    assert "COMPLETE" not in result.output
    family = "chains" if factory else "runs"
    assert next((tmp_path / ".apm" / family).glob("*/record.json")).read_text() == malformed


@pytest.mark.parametrize("fault", ["transcript", "artifact", "child-transcript", "child-record"])
def test_factory_completion_rechecks_retained_evidence_after_cleanup(tmp_path, monkeypatch, fault):
    args = _cli_fixture(tmp_path, monkeypatch, True)

    @contextmanager
    def imports(*args, **kwargs):
        yield None, None
        family = "runs" if fault.startswith("child") else "chains"
        directory = next((tmp_path / ".apm" / family).glob("*"))
        if fault == "artifact":
            path = directory / "artifacts/last.txt"
        elif fault == "child-record":
            path = directory / "record.json"
        else:
            path = directory / "transcript.log"
        path.chmod(0o600)
        path.write_bytes(path.read_bytes() + b"\n ")

    monkeypatch.setattr("apmx.install.contract_source.prepare_imports", imports)
    result = CliRunner().invoke(
        main, [*args, "--on", "copilot", "--allow-host-access", "--verbose"]
    )
    assert result.exit_code == 22, result.output
    assert "COMPLETE" not in result.output
    data = json.loads(next((tmp_path / ".apm/chains").glob("*/record.json")).read_bytes())
    assert data["execution"]["name"] == "HALTED" and data["complete"] is False
