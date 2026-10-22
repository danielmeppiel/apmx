"""`apmx check`: re-check a producer's outputs where no agent ran, and write the receipt of record."""

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from test_apmx import _package, caller
from test_audit import _lines, _no_ambient_policy_or_pull_request

from apmx.audit import verifier
from apmx.cli import main
from apmx.contracts.models import ProcessRequest

__all__ = ["_no_ambient_policy_or_pull_request", "caller"]
pytestmark = pytest.mark.component


def _fake_agent(monkeypatch, content: bytes | None = None) -> None:
    """The producer job's agent: writes result.txt; never model inference."""
    script = (
        "from pathlib import Path; "
        + (
            f"Path('result.txt').write_bytes({content!r}); "
            if content is not None
            else "Path('result.txt').write_bytes(Path('notes.md').read_bytes()); "
        )
        + """print('{"type":"result","exitCode":0,"sessionId":"fixture","usage":{}}')"""
    )

    class Agent:
        def build_contract_request(self, plan, snapshot, directory, *, timeout_seconds):
            return ProcessRequest(
                (sys.executable, "-c", script), snapshot.producer, timeout_seconds
            )

    original = __import__("apmx.runtime.factory", fromlist=["RuntimeFactory"]).RuntimeFactory
    real = original.get_runtime_by_name
    monkeypatch.setattr(
        original,
        "get_runtime_by_name",
        staticmethod(lambda name, model=None: Agent() if name == "copilot" else real(name, model)),
    )


@pytest.fixture
def produced(caller, tmp_path, monkeypatch):
    """A producer job's factory run and receipt, plus a separate trusted checkout."""
    factory = _package(tmp_path / "factory")
    checkout = tmp_path / "checkout"
    shutil.copytree(factory, checkout)
    _fake_agent(monkeypatch)
    run = CliRunner().invoke(main, [str(factory), "--on", "copilot", "--allow-host-access"])
    assert run.exit_code == 0, run.output
    (receipt,) = _receipts(factory)
    return receipt, checkout


def _receipts(root: Path) -> list[Path]:
    return list((root / ".apm").glob("*/*/receipt.intoto.jsonl"))


def _check(*args):
    return CliRunner().invoke(main, ["check", *map(str, args)])


def _definition(receipt: Path) -> str:
    parsed, failure = verifier.inspect(receipt)
    assert failure is None, failure
    return parsed.definition_sha256


def test_check_replays_outputs_and_writes_the_receipt_of_record(produced) -> None:
    producer, checkout = produced
    result = _check(producer, checkout)
    assert result.exit_code == 0, result.output
    assert "no agent runs here" in result.output and "Replay" in result.output
    (receipt,) = _receipts(checkout)
    assert f"[+] Checked here: receipt of record {receipt}" in result.output
    assert _definition(receipt) == _definition(producer)
    (run,) = list((checkout / ".apm/runs").glob("*/record.json"))
    assert json.loads(run.read_bytes())["harness"] == "replay"
    provenance = _lines(receipt)[0]["predicate"]["buildDefinition"]
    (stage,) = provenance["internalParameters"]["stages"]
    (reported,) = _lines(producer)[0]["predicate"]["buildDefinition"]["internalParameters"][
        "stages"
    ]
    assert stage["harness"] == reported["harness"] == "copilot"
    assert stage["observedModels"] == reported["observedModels"]
    assert stage["harnessVersion"] == reported["harnessVersion"]
    assert (stage["reportedBy"], stage["checkedBy"]) == ("producer", "apmx check (replay)")
    (dependency,) = [
        item
        for item in provenance["resolvedDependencies"]
        if verifier.role(item) == "producer-receipt"
    ]
    assert dependency["digest"]["sha256"] == hashlib.sha256(producer.read_bytes()).hexdigest()
    output = receipt.parent / "outputs/result.txt"
    assert output.read_bytes() == (checkout / "notes.md").read_bytes()
    audit = CliRunner().invoke(main, ["audit", str(receipt)])
    assert audit.exit_code == 0, audit.output
    stages = CliRunner().invoke(main, ["audit", str(receipt), "--explain", "stages"])
    assert "reported by the producer" in stages.output


def test_check_reruns_the_checks_instead_of_trusting_the_producer(produced) -> None:
    """A self-consistent producer receipt claiming a pass for wrong bytes is rejected."""
    producer, checkout = produced
    _forge_output(producer, b"forged by a compromised agent job\n")
    assert verifier.inspect(producer)[1] is None
    result = _check(producer, checkout)
    assert result.exit_code != 0, result.output
    assert "REJECTED" in result.output
    assert not _receipts(checkout)


def _forge_output(bundle: Path, raw: bytes) -> None:
    """Replace result.txt everywhere the producer bundle names it, keeping it consistent."""
    new = hashlib.sha256(raw).hexdigest()
    old = (bundle.parent / "outputs/result.txt").read_bytes()
    before = hashlib.sha256(old).hexdigest()
    (bundle.parent / "evidence/sha256" / new).write_bytes(raw)
    statements = json.loads(json.dumps(_lines(bundle)).replace(before, new))
    bundle.chmod(0o600)
    bundle.write_bytes(verifier.bundle_bytes(statements))


def test_a_different_factory_definition_withdraws_the_receipt(produced) -> None:
    producer, checkout = produced
    check = checkout / "checks/check.py"
    check.write_text(check.read_text() + "# reviewed change\n")
    result = _check(producer, checkout)
    assert result.exit_code == 1, result.output
    assert "differs from the producer's" in result.output
    assert not _receipts(checkout)


def test_invalid_producer_receipt_is_refused(produced) -> None:
    producer, checkout = produced
    producer.chmod(0o600)
    producer.write_bytes(producer.read_bytes() + b"not json\n")
    result = _check(producer, checkout)
    assert result.exit_code == 1 and "producer receipt is invalid (Integrity" in result.output
    assert not (checkout / ".apm").exists()


def test_replay_runs_only_under_apmx_check(produced) -> None:
    _, checkout = produced
    result = CliRunner().invoke(main, [str(checkout), "--on", "replay", "--allow-host-access"])
    assert result.exit_code != 0
    assert "only runs under `apmx check`" in result.output


def test_check_is_a_reserved_word(tmp_path) -> None:
    result = CliRunner().invoke(main, ["--plan", "check"])
    assert (
        result.exit_code == 2
        and "'check' is reserved" in result.output
        and "./check" in result.output
    )
    assert "PRODUCER_RECEIPT" in CliRunner().invoke(main, ["check", "--help"]).output
    assert _check(tmp_path / "missing").exit_code == 2


@pytest.fixture
def packaged(caller, tmp_path, monkeypatch):
    """A producer job that ran a factory package against the caller, plus a fresh workspace."""
    (tmp_path / "source").mkdir()
    package = _package(tmp_path / "source/factory")
    _fake_agent(monkeypatch)
    run = CliRunner().invoke(
        main, ["--from", str(package), "--on", "copilot", "--allow-host-access"]
    )
    assert run.exit_code == 0, run.output
    (receipt,) = _receipts(caller)
    workspace = tmp_path / "workspace"
    shutil.copytree(caller, workspace, ignore=shutil.ignore_patterns(".apm"))
    monkeypatch.chdir(workspace)
    return receipt, package, workspace


def test_check_from_a_package_writes_the_receipt_of_record_in_the_workspace(packaged) -> None:
    producer, package, workspace = packaged
    result = _check(producer, "--from", package)
    assert result.exit_code == 0, result.output
    (receipt,) = _receipts(workspace)
    assert f"[+] Checked here: receipt of record {receipt}" in result.output
    assert _definition(receipt) == _definition(producer)
    stage = _lines(receipt)[0]["predicate"]["buildDefinition"]["internalParameters"]["stages"][0]
    assert stage["reportedBy"] == "producer"


def test_check_from_a_different_package_withdraws_the_receipt(packaged, tmp_path) -> None:
    producer, package, workspace = packaged
    other = tmp_path / "other/factory"
    shutil.copytree(package, other)
    (other / "checks/check.py").write_text((other / "checks/check.py").read_text() + "# drift\n")
    result = _check(producer, "--from", other)
    assert result.exit_code == 1, result.output
    assert "differs from the producer's" in result.output and "same package ref" in result.output
    assert not _receipts(workspace)


def test_check_without_from_refuses_a_package_reference(produced) -> None:
    producer, _ = produced
    result = _check(producer, "owner/factory@v1")
    assert result.exit_code == 2 and "--from PACKAGE_REF" in result.output
