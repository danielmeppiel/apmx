"""Deterministic producer tools and real Git; no live model calls."""

import io
import json
import os
import stat
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from test_artifacts import make_plan
from test_chain import caller, write_contract

from apmx.cli import main
from apmx.contracts import chain, records, workspace
from apmx.contracts.git_export import delete_file, export_changes, write_file
from apmx.contracts.models import ContractError, Outcome, ProcessRequest
from apmx.contracts.process import local_git
from apmx.core.contract_logger import ContractLogger
from apmx.runtime import artifact_tools
from apmx.runtime.artifact_mcp import ArtifactServer
from apmx.runtime.factory import RuntimeFactory
from apmx.utils import atomic_io

__all__ = ["caller"]

pytestmark = pytest.mark.component


@pytest.fixture
def prepared(caller):
    (caller / "src").mkdir()
    (caller / "src/calc.py").write_bytes(b"def answer():\r\n    return 1\r\n")
    (caller / "obsolete.py").write_bytes(b"old = True\n")
    (caller / "checks").mkdir()
    (caller / "checks/original.py").write_bytes(b"assert True\n")
    write_contract(
        caller,
        "job.contract.md",
        ("src/calc.py", "obsolete.py"),
        '["changes.diff", "implementation.md"]',
    )
    plan = make_plan(caller).nodes[0].plan
    store = records.AttemptStore.create(plan)
    snapshot = workspace.capture_workspace(plan, store.directory)
    artifact_tools.configure(plan, snapshot, store.directory)
    context = artifact_tools.load_context(store.directory / "native-tools/config.json")
    return plan, snapshot, store, context


def test_git_export_uses_actual_files_not_producer_index(prepared):
    plan, snapshot, store, context = prepared
    original = (plan.project_root / "src/calc.py").read_bytes()
    write_file(context, "src/calc.py", "def answer():\r\n    return 2\r\n")
    write_file(context, "tests/new test.py", "assert True\n")
    write_file(context, "implementation.md", "Proposed code update.\n")
    delete_file(context, "obsolete.py")
    (snapshot.producer / ".git/index").write_bytes(b"not a Git index")
    receipt = export_changes(context, "changes.diff")
    patch = (snapshot.producer / "changes.diff").read_bytes()
    assert b"+    return 2\r\n" in patch
    assert b"new file mode 100644" in patch and b"deleted file mode 100644" in patch
    assert set(receipt["changed_files"]) == {"src/calc.py", "tests/new test.py", "obsolete.py"}
    assert receipt["base_tree"] != receipt["result_tree"]
    for excluded in (b"implementation.md", b"checks/", b"job.contract.md", b".git/index"):
        assert excluded not in patch
    artifact = workspace.capture_output(
        snapshot, plan.contract.produces, store.directory, plan.limits
    )
    candidate = workspace.prepare_check_workspace(snapshot, artifact, store.directory, "apply")
    local_git(candidate, "apply", "--", "changes.diff")
    assert (candidate / "src/calc.py").read_bytes() == b"def answer():\r\n    return 2\r\n"
    assert not (candidate / "obsolete.py").exists()
    assert (plan.project_root / "src/calc.py").read_bytes() == original
    assert (snapshot.root / "src/calc.py").read_bytes() == original


@pytest.mark.parametrize(
    "name",
    [
        "../outside",
        "/absolute",
        r"C:\outside",
        "checks/original.py",
        ".git/index",
        ".apm/state",
        "apm.yml",
        "_apmx_context/skill.md",
        "job.contract.md",
    ],
)
def test_working_tools_refuse_escape_and_protected_paths(prepared, name):
    context = prepared[3]
    with pytest.raises(ContractError):
        write_file(context, name, "not permitted")
    with pytest.raises(ContractError):
        delete_file(context, name)


@pytest.mark.parametrize(
    "fault", ["binary", "nul", "mode", "link", "submodule", "directory", "undeclared", "baseline"]
)
def test_export_refuses_unsupported_or_changed_source(prepared, fault):
    _, snapshot, _, context = prepared
    source = snapshot.producer / "src/calc.py"
    output = "changes.diff"
    if fault in {"binary", "nul"}:
        source.write_bytes(b"\xff" if fault == "binary" else b"\0")
    elif fault == "mode":
        if os.name == "nt":
            pytest.skip("POSIX executable-mode changes")
        source.chmod(0o755)
    elif fault == "link":
        if os.name == "nt":
            pytest.skip("Symlink privileges")
        source.unlink()
        source.symlink_to(snapshot.root / "src/calc.py")
    elif fault == "submodule":
        (snapshot.producer / "src/nested/.git").mkdir(parents=True)
    elif fault == "directory":
        source.unlink()
        source.mkdir()
    elif fault == "undeclared":
        output = "not-declared.diff"
    else:
        (snapshot.root / "src/calc.py").write_bytes(b"changed baseline")
    with pytest.raises(ContractError):
        export_changes(context, output)
    assert not (snapshot.producer / "changes.diff").exists()


def test_export_is_repeatably_deterministic_and_binary_inputs_are_not_a_global_ban(prepared):
    _, snapshot, _, context = prepared
    # Unchanged opaque inputs can accompany text changes; changed binary source cannot.
    entry = workspace.FileEntry("image.bin", "unused", 2, 0o644)
    for root in (snapshot.root, snapshot.producer):
        (root / entry.relative_path).write_bytes(b"\xff\0")
    _, actual = workspace._read(snapshot.root, "image.bin", 2)
    context = replace(context, files=(*context.files, actual))
    write_file(context, "src/calc.py", "answer = 2\n")
    first = export_changes(context, "changes.diff")
    raw = (snapshot.producer / "changes.diff").read_bytes()
    second = export_changes(context, "changes.diff")
    assert first == second and (snapshot.producer / "changes.diff").read_bytes() == raw
    assert b"image.bin" not in raw


def test_atomic_tool_publication_failure_keeps_original(prepared, monkeypatch):
    context = prepared[3]
    path = context.producer / "src/calc.py"
    raw = path.read_bytes()
    monkeypatch.setattr(
        "apmx.utils.atomic_io._replace_atomic_file", Mock(side_effect=OSError("storage failed"))
    )
    with pytest.raises(OSError):
        write_file(context, "src/calc.py", "replacement\n")
    assert path.read_bytes() == raw
    assert not list(path.parent.glob("apm-atomic-*"))


@pytest.fixture
def windows_readonly(monkeypatch):
    """Exercise Windows read-only destination failures on every test platform."""
    replace_file = atomic_io._replace_atomic_file
    unlink_file = Path.unlink
    operations = []

    def writable(path: Path) -> None:
        if path.exists() and not path.stat().st_mode & stat.S_IWUSR:
            raise PermissionError("Windows refuses a read-only destination")

    def replace_destination(source: str, destination: Path) -> None:
        writable(destination)
        operations.append(("replace", destination))
        replace_file(source, destination)

    def unlink_destination(path: Path, missing_ok: bool = False) -> None:
        writable(path)
        operations.append(("unlink", path))
        unlink_file(path, missing_ok=missing_ok)

    monkeypatch.setattr(atomic_io, "_replace_atomic_file", replace_destination)
    monkeypatch.setattr(Path, "unlink", unlink_destination)
    return operations


def readonly_context(prepared):
    context = prepared[3]
    name = "src/calc.py"
    for root in (context.baseline, context.producer):
        (root / name).chmod(stat.S_IRUSR)
    _, original = workspace._read(context.baseline, name, context.limits.file_bytes)
    return replace(
        context,
        files=tuple(original if item.relative_path == name else item for item in context.files),
    )


@pytest.mark.windows_compat
@pytest.mark.parametrize("operation", ("overwrite", "delete"))
def test_readonly_private_edits_and_export_preserve_baseline(prepared, windows_readonly, operation):
    context = readonly_context(prepared)
    name = "src/calc.py"
    before = workspace._read(context.baseline, name, context.limits.file_bytes)
    if operation == "overwrite":
        write_file(context, name, "def answer():\r\n    return 2\r\n")
        assert workspace._read(context.producer, name, context.limits.file_bytes)[1].mode == (
            before[1].mode
        )
    else:
        delete_file(context, name)
        assert not (context.producer / name).exists()
    receipt = export_changes(context, "changes.diff")
    assert receipt["changed_files"] == [name]
    patch = (context.producer / "changes.diff").read_bytes()
    assert b"old mode " not in patch and b"\nnew mode " not in patch
    assert workspace._read(context.baseline, name, context.limits.file_bytes) == before
    assert any(action == "unlink" and "tree" in path.parts for action, path in windows_readonly)


@pytest.mark.windows_compat
def test_failed_readonly_replacement_restores_original_bytes_and_permissions(prepared, monkeypatch):
    context = readonly_context(prepared)
    before = workspace._read(context.producer, "src/calc.py", context.limits.file_bytes)
    monkeypatch.setattr(
        atomic_io, "_replace_atomic_file", Mock(side_effect=OSError("storage failed"))
    )
    with pytest.raises(OSError, match="storage failed"):
        write_file(context, "src/calc.py", "replacement\n")
    assert workspace._read(context.producer, "src/calc.py", context.limits.file_bytes) == before
    assert not list((context.producer / "src").glob("apm-atomic-*"))


@pytest.mark.windows_compat
@pytest.mark.parametrize("operation", ("overwrite", "delete"))
def test_readonly_upstream_source_can_be_changed_without_mutating_its_receipt(
    caller, monkeypatch, windows_readonly, operation
):
    write_contract(caller, "generate.contract.md", ("seed.txt",), "source.py")
    write_contract(
        caller,
        "modify.contract.md",
        ("source.py",),
        '["changes.diff", "implementation.md"]',
        check="from pathlib import Path; assert Path('changes.diff').stat().st_size",
    )
    calls = []

    def build(selected, snapshot, directory, *, timeout_seconds):
        calls.append(snapshot)
        controls = {}
        if selected.contract.outputs == ("source.py",):
            code = "from pathlib import Path; Path('source.py').write_bytes(b'answer = 1\\n')"
        else:
            assert not (snapshot.producer / "source.py").stat().st_mode & stat.S_IWUSR
            artifact_tools.configure(selected, snapshot, directory)
            server = ArtifactServer(
                artifact_tools.load_context(directory / "native-tools/config.json")
            )
            arguments = {"path": "source.py"}
            tool = "delete_file"
            if operation == "overwrite":
                arguments["content"] = "answer = 2\n"
                tool = "write_file"
            assert not invoke(server, tool, arguments).get("isError")
            assert not invoke(
                server, "write_file", {"path": "implementation.md", "content": "Changed source.\n"}
            ).get("isError")
            assert not invoke(server, "export_changes", {"output": "changes.diff"}).get("isError")
            code = "pass"
            controls = {"artifact_tools": {"server": artifact_tools.SERVER}}
        completion = json.dumps(
            {"type": "result", "exitCode": 0, "sessionId": "fixture", "usage": {}}
        )
        return ProcessRequest(
            (sys.executable, "-B", "-c", code + f"\nprint({completion!r}, flush=True)\n"),
            snapshot.producer,
            timeout_seconds,
            control_observations=controls,
        )

    runtime = Mock()
    runtime.build_contract_request.side_effect = build
    monkeypatch.setattr(RuntimeFactory, "get_runtime_by_name", lambda *args: runtime)
    result = chain.run_chain(make_plan(caller), logger=ContractLogger(), allow_advisory=True)
    assert result.complete and result.outcome == Outcome.UNPROVEN
    assert len(calls) == 2
    upstream = result.runs[0].artifact.path
    assert upstream.read_bytes() == b"answer = 1\n"
    assert not upstream.stat().st_mode & stat.S_IWUSR
    assert (calls[1].root / "source.py").read_bytes() == b"answer = 1\n"
    assert not (calls[1].root / "source.py").stat().st_mode & stat.S_IWUSR
    assert not (caller / "source.py").exists()
    assert len(result.runs[1].native_exports) == 1
    candidate = caller.parent / "applied"
    candidate.mkdir()
    (candidate / "source.py").write_bytes(upstream.read_bytes())
    patch = next(
        item for item in result.runs[1].artifact.files if item.relative_path == "changes.diff"
    )
    local_git(candidate, "apply", "--", str(patch.path))
    if operation == "overwrite":
        assert (candidate / "source.py").read_bytes() == b"answer = 2\n"
    else:
        assert not (candidate / "source.py").exists()


def invoke(server, name, arguments):
    return server.call({"name": name, "arguments": arguments})


def test_stdio_protocol_calls_real_tools_and_captures_receipts(prepared):
    plan, snapshot, store, context = prepared
    requests = [
        {"jsonrpc": "2.0", "id": 0, "method": "server/discover"},
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "_meta": {"progressToken": 3},
                "name": "write_file",
                "arguments": {"path": "src/calc.py", "content": "answer = 2\n"},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "export_changes", "arguments": {"output": "changes.diff"}},
        },
    ]
    source = io.BytesIO(b"".join(json.dumps(request).encode() + b"\n" for request in requests))
    destination = io.BytesIO()
    server = ArtifactServer(context)
    assert server.serve(source, destination) == 0
    replies = [json.loads(line) for line in destination.getvalue().splitlines()]
    assert replies[0]["error"]["code"] == -32601
    assert {tool["name"] for tool in replies[2]["result"]["tools"]} == set(artifact_tools.TOOLS)
    assert all(not item["result"].get("isError") for item in replies[-2:])
    invoke(server, "write_file", {"path": "implementation.md", "content": "Report.\n"})
    artifact = workspace.capture_output(
        snapshot, plan.contract.produces, store.directory, plan.limits
    )
    captured = artifact_tools.capture_exports(store.directory, artifact, snapshot, plan.limits)
    assert len(captured) == 1
    receipt = json.loads((store.directory / captured[0].relative_path).read_bytes())
    assert receipt["patch_sha256"] == artifact.files[0].sha256
    assert receipt["baseline_digest"] == snapshot.digest


@pytest.mark.parametrize(
    "frame",
    [b"not-json\n", b"{}\n", b'{"jsonrpc":"2.0","id":1,"id":2,"method":"ping"}\n', b"[" * 2000],
)
def test_protocol_errors_fail_closed(prepared, frame):
    server = ArtifactServer(prepared[3])
    assert server.serve(io.BytesIO(frame), io.BytesIO()) == 2
    assert server.state["failed"] is True


@pytest.mark.parametrize("fault", ["arguments", "unknown-tool", "limit", "cancelled", "publish"])
def test_tool_errors_or_inflight_cancellation_prevent_later_admission(prepared, monkeypatch, fault):
    plan, snapshot, store, context = prepared
    server = ArtifactServer(context)
    if fault == "cancelled":
        monkeypatch.setattr(
            "apmx.runtime.artifact_mcp.write_file", Mock(side_effect=KeyboardInterrupt)
        )
        with pytest.raises(KeyboardInterrupt):
            invoke(server, "write_file", {"path": "src/calc.py", "content": "x"})
    else:
        if fault == "limit":
            server.state["operations"] = 64
        if fault == "publish":
            monkeypatch.setattr(
                "apmx.runtime.artifact_mcp.write_file", Mock(side_effect=OSError("failed"))
            )
        response = invoke(
            server,
            "unknown" if fault == "unknown-tool" else "write_file",
            {"path": "src/calc.py"}
            if fault == "arguments"
            else {"path": "src/calc.py", "content": "x"},
        )
        assert response["isError"] is True
    with pytest.raises(ContractError, match="did not finish"):
        artifact_tools.capture_exports(store.directory, None, snapshot, plan.limits)


def test_frozen_dispatch_uses_bundled_executable_without_a_public_flag(prepared, monkeypatch):
    plan, snapshot, store, _ = prepared
    second = store.directory / "second"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    plugin = artifact_tools.configure(plan, snapshot, second)
    manifest = json.loads((plugin / ".github/plugin/plugin.json").read_bytes())
    bridge = manifest["mcpServers"][artifact_tools.SERVER]
    assert bridge["command"] == sys.executable and bridge["args"] == []
    assert "PYTHONPATH" not in bridge["env"]
    called = Mock(return_value=0)
    monkeypatch.setattr("apmx.runtime.artifact_mcp.run_server", called)
    monkeypatch.setenv(artifact_tools.SERVER_ENV, str(second / "native-tools/config.json"))
    assert CliRunner().invoke(main, []).exit_code == 0
    called.assert_called_once()
    monkeypatch.delenv(artifact_tools.SERVER_ENV)
    help_result = CliRunner().invoke(main, ["--help"])
    assert help_result.exit_code == 0
    assert "artifact-server" not in help_result.output


@pytest.mark.parametrize("fault", ["none", "patch", "receipt", "active", "failed"])
def test_real_engine_captures_export_evidence_and_refuses_tampering(caller, monkeypatch, fault):
    (caller / "source.py").write_bytes(b"answer = 1\n")
    write_contract(
        caller, "job.contract.md", ("source.py",), '["changes.diff", "implementation.md"]'
    )
    plan = make_plan(caller)

    def build(selected, snapshot, directory, *, timeout_seconds):
        artifact_tools.configure(selected, snapshot, directory)
        config = directory / "native-tools/config.json"
        code = (
            "import json\nfrom pathlib import Path\n"
            "from apmx.runtime.artifact_mcp import ArtifactServer\n"
            "from apmx.runtime.artifact_tools import load_context, save_json\n"
            f"server = ArtifactServer(load_context(Path({str(config)!r})))\n"
            "def call(name, arguments):\n"
            "    assert not server.call({'name':name,'arguments':arguments}).get('isError')\n"
            "call('write_file', {'path':'source.py','content':'answer = 2\\n'})\n"
            "call('write_file', {'path':'implementation.md','content':'Updated answer.\\n'})\n"
            "call('export_changes', {'output':'changes.diff'})\n"
        )
        if fault == "patch":
            code += "Path('changes.diff').write_bytes(b'changed after export')\n"
        elif fault == "receipt":
            code += "server.context.directory.joinpath('export-1.json').write_bytes(b'{}')\n"
        elif fault in {"active", "failed"}:
            code += (
                f"server.state[{fault!r}] = {'True' if fault == 'failed' else repr('export_changes')}\n"
                "server.save()\n"
            )
        code += f"print({json.dumps({'type': 'result', 'exitCode': 0, 'sessionId': 'deterministic', 'usage': {}})!r}, flush=True)\n"
        return ProcessRequest(
            (sys.executable, "-B", "-c", code),
            snapshot.producer,
            timeout_seconds,
            env={
                **os.environ,
                "PYTHONPATH": str(Path(artifact_tools.__file__).resolve().parents[2]),
            },
            control_observations={"artifact_tools": {"server": artifact_tools.SERVER}},
        )

    runtime = Mock()
    runtime.build_contract_request.side_effect = build
    monkeypatch.setattr(RuntimeFactory, "get_runtime_by_name", lambda *args: runtime)
    result = chain.run_chain(plan, logger=ContractLogger(), allow_advisory=True)
    if fault != "none":
        assert not result.complete and result.outcome == Outcome.HALTED
        assert not result.runs[0].checks
        return
    assert result.complete and result.outcome == Outcome.UNPROVEN
    leaf = result.runs[0]
    assert len(leaf.native_exports) == 1
    bindings = records.finalized_inputs(plan.nodes[0].plan, leaf)
    metadata = leaf.run_directory / leaf.native_exports[0].relative_path
    raw = metadata.read_bytes()
    metadata.chmod(0o600)
    for mutation in ("missing", "changed"):
        if mutation == "missing":
            metadata.unlink()
        else:
            metadata.write_bytes(raw + b" ")
        with pytest.raises(ContractError):
            records.admit_handoffs(plan.nodes[0].plan, leaf, allow_unproven=True)
        with pytest.raises(ContractError):
            records.validate_binding(bindings[1], caller, plan.nodes[0].plan.limits)


def test_server_write_limit_and_undeclared_export_are_reported(prepared):
    context = replace(prepared[3], limits=replace(prepared[3].limits, file_bytes=2))
    with pytest.raises(ContractError, match="byte limit"):
        write_file(context, "src/calc.py", "longer")
    server = ArtifactServer(prepared[3])
    response = invoke(server, "export_changes", {"output": "not-declared"})
    assert response["isError"] and server.state["error"] == "undeclared_output"
