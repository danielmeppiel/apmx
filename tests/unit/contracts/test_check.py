"""`apmx check`: re-check a producer's outputs where no agent ran, and write the receipt of record."""

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from test_apmx import _package, caller
from test_audit import _no_ambient_policy_or_pull_request, _rewrite

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
    return [path.parent for path in (root / ".apm").glob("*/*/receipt/index.json")]


def _check(*args):
    return CliRunner().invoke(main, ["check", *map(str, args)])


def _definition(receipt: Path) -> str:
    return json.loads((receipt / "index.json").read_bytes())["definition"]["digest"]["sha256"]


def test_check_replays_outputs_and_writes_the_receipt_of_record(produced) -> None:
    producer, checkout = produced
    result = _check(producer, checkout)
    assert result.exit_code == 0, result.output
    assert "no agent runs here" in result.output and "Replay" in result.output
    (receipt,) = _receipts(checkout)
    assert f"[+] Checked here: receipt of record {receipt}" in result.output
    assert _definition(receipt) == _definition(producer)
    (execution,) = list(receipt.glob("attempts/*/execution.json"))
    record = json.loads(execution.read_bytes())
    assert record["harness"] == "replay" and record["observedModels"] == []
    assert record["checks"][0]["normalized"] == 0
    (output,) = list(receipt.glob("attempts/*/artifacts/result.txt"))
    assert output.read_bytes() == (checkout / "notes.md").read_bytes()
    audit = CliRunner().invoke(main, ["audit", str(receipt)])
    assert audit.exit_code == 0, audit.output


def test_check_reruns_the_checks_instead_of_trusting_the_producer(produced) -> None:
    """A producer receipt claiming a pass for wrong bytes is rejected by the check of record."""
    producer, checkout = produced
    (name,) = [
        n
        for n in json.loads((producer / "index.json").read_bytes())["files"]
        if n.endswith("/artifacts/result.txt")
    ]
    _forge_output(producer, name, b"forged by a compromised agent job\n")
    result = _check(producer, checkout)
    assert result.exit_code != 0, result.output
    assert "REJECTED" in result.output
    assert not _receipts(checkout)


def _forge_output(receipt: Path, name: str, raw: bytes) -> None:
    """Rewrite an output and its statement subjects so the receipt stays self-consistent."""
    new = hashlib.sha256(raw).hexdigest()
    _rewrite(receipt, name, raw)

    def visit(node):
        if isinstance(node, dict):
            if node.get("name") == name and isinstance(node.get("digest"), dict):
                node["digest"]["sha256"] = new
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    for path in sorted(receipt.rglob("*.intoto.json")):
        document = json.loads(path.read_bytes())
        before = json.dumps(document)
        visit(document)
        if json.dumps(document) != before:
            _rewrite(receipt, path.relative_to(receipt).as_posix(), json.dumps(document).encode())


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
    (producer / "smuggled.txt").write_text("x")
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
