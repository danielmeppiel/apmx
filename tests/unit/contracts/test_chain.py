"""Native graph resolution and real leaf execution with only the producer replaced."""

import json
import ast
import os
import shlex
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from apmx.cli import main
from apmx.contracts import chain, engine, records, resolution, workspace
from apmx.contracts.models import ContractError, ContractLimits, Outcome, ProcessRequest
from apmx.core.contract_logger import ContractLogger
from apmx.runtime.factory import RuntimeFactory

pytestmark = pytest.mark.component


def write_contract(
    root: Path, name: str, needs: tuple[str, ...], output: str, *, check: str = "pass"
) -> Path:
    command = f"{shlex.quote(sys.executable)} -B -c {shlex.quote(check)}"
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nneeds: {json.dumps(needs)}\nproduces: {output}\n"
        f"verify:\n  exact: {json.dumps(command)}\n---\nProduce {output} from the inputs.\n",
        encoding="ascii",
    )
    return path


@pytest.fixture
def caller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    tools = tmp_path / "tools"
    tools.mkdir()
    executable = tools / ("copilot.exe" if os.name == "nt" else "copilot")
    executable.write_bytes(b"Discovery only; never launched.\n")
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    root = tmp_path / "caller"
    root.mkdir()
    monkeypatch.chdir(root)
    (root / "seed.txt").write_bytes(b"seed")
    return root


def two_nodes(root: Path) -> None:
    write_contract(root, "z-first.contract.md", ("seed.txt",), "first.txt")
    write_contract(root, "a-target.contract.md", ("first.txt",), "last.txt")


def prepare(root: Path, *, target: str = "a-target.contract.md", allow: bool = True):
    return resolution.preflight(
        resolution.resolve(Path(target), root),
        root,
        harness="copilot",
        allow_unproven_inputs=allow,
    )


def producer(monkeypatch: pytest.MonkeyPatch, *, body=None):
    calls = []

    def build(plan, snapshot, directory, *, timeout_seconds):
        calls.append((plan, snapshot, directory))
        code = (
            "from pathlib import Path\n"
            f"target = Path({plan.contract.produces!r})\n"
            "target.parent.mkdir(parents=True, exist_ok=True)\n"
            f"target.write_bytes(b'|'.join(Path(p).read_bytes() for p in {plan.contract.needs!r}))\n"
        )
        if body is not None:
            code = body(plan, snapshot, directory)
        completion = json.dumps(
            {"type": "result", "exitCode": 0, "sessionId": "fixture", "usage": {}}
        )
        return ProcessRequest(
            (sys.executable, "-B", "-c", code + f"\nprint({completion!r}, flush=True)\n"),
            snapshot.producer,
            timeout_seconds,
        )

    adapter = Mock()
    adapter.build_contract_request.side_effect = build
    monkeypatch.setattr(RuntimeFactory, "get_runtime_by_name", lambda *a: adapter)
    return calls


def test_graph_is_inferred_not_filename_order(caller: Path) -> None:
    two_nodes(caller)
    graph = resolution.resolve(Path("a-target.contract.md"), caller)
    assert [item.path.name for item in graph.order] == [
        "z-first.contract.md",
        "a-target.contract.md",
    ]
    assert [(edge.name, edge.producer.name) for edge in graph.edges] == [
        ("first.txt", "z-first.contract.md"),
    ]
    write_contract(caller, "a-target.contract.md", ("seed.txt",), "last.txt")
    assert len(resolution.resolve(Path("a-target.contract.md"), caller).order) == 1


def test_preview_is_free_symbolic_and_engine_refuses_it(
    caller: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    two_nodes(caller)
    launch = Mock(side_effect=AssertionError("no native launch in preview"))
    monkeypatch.setattr(RuntimeFactory, "get_runtime_by_name", launch)
    before = {p.name: p.read_bytes() for p in caller.iterdir()}
    result = CliRunner().invoke(main, [str(caller), "--on", "copilot", "--plan"])
    assert result.exit_code == 0, result.output
    assert "2 contracts" in result.output and "from an earlier step" in result.output
    assert "VERIFIED-only" in result.output and "Every run starts fresh" in result.output
    assert "Final outputs: last.txt" in result.output
    assert before == {p.name: p.read_bytes() for p in caller.iterdir()}
    planned = prepare(caller).nodes[-1].plan
    with pytest.raises(ContractError, match="Preview dependencies"):
        engine.run_contract(planned, logger=ContractLogger(), allow_advisory=True)
    launch.assert_not_called()


@pytest.mark.parametrize("allow", (False, True))
def test_actual_leaf_chain_preserves_caller_and_assurance(
    caller: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow: bool,
) -> None:
    two_nodes(caller)
    (caller / "first.txt").write_bytes(b"stale, not an input")
    (caller / "last.txt").write_bytes(b"do not overwrite")
    original = {p.name: p.read_bytes() for p in caller.iterdir()}
    calls = producer(monkeypatch)
    result = chain.run_chain(
        prepare(caller, allow=allow), logger=ContractLogger(), allow_advisory=True
    )
    assert result.outcome == Outcome.UNPROVEN
    assert result.complete is allow
    assert len(calls) == (2 if allow else 1)
    document = json.loads(result.record_path.read_bytes())
    assert document["allow_unproven_inputs"] is allow
    assert document["complete"] is allow
    assert len(document["nodes"]) == 2
    assert result.record_path.name == "record.json"
    assert document["schema"] == "apmx-contract-chain/0.1"
    if allow:
        assert result.runs[-1].artifact.path.read_bytes() == b"seed"
        assert (calls[1][1].root / "first.txt").read_bytes() == b"seed"
        assert (
            calls[1][0].input_bindings[0].record_path
            == result.runs[0].run_directory / "record.json"
        )
        view = Path(document["artifacts"]["root"])
        assert view == result.record_path.parent / "artifacts"
        assert {item["relative_path"] for item in document["artifacts"]["files"]} == {
            "seed.txt",
            "first.txt",
            "last.txt",
        }
        assert all(
            (view / name).read_bytes() == b"seed" for name in ("seed.txt", "first.txt", "last.txt")
        )
    else:
        assert result.stop_reason == "unproven_input"
        assert document["nodes"][1]["state"] == "blocked"
        assert document["artifacts"] is None
        assert not (result.record_path.parent / "artifacts").exists()
    assert original == {p.name: p.read_bytes() for p in caller.iterdir() if p.is_file()}


@pytest.mark.parametrize("fault", ("cycle", "missing", "ambiguous", "case", "prefix", "source"))
def test_invalid_graph_is_refused_before_native(
    caller: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    two_nodes(caller)
    if fault == "cycle":
        write_contract(caller, "z-first.contract.md", ("last.txt",), "first.txt")
    elif fault == "missing":
        (caller / "seed.txt").unlink()
    elif fault == "ambiguous":
        write_contract(caller, "other.contract.md", (), "first.txt")
    elif fault == "case":
        write_contract(caller, "other.contract.md", (), "FIRST.txt")
    elif fault == "prefix":
        write_contract(caller, "a-target.contract.md", ("first.txt",), "first.txt/child")
    else:
        write_contract(caller, "a-target.contract.md", ("first.txt",), "z-first.contract.md")
    calls = producer(monkeypatch)
    with pytest.raises(ContractError):
        prepare(caller)
    assert calls == [] and not (caller / ".apm").exists()


@pytest.mark.parametrize("where", ("source", "input", "output", "checks"))
def test_symlink_cases_refuse(caller: Path, where: str) -> None:
    if os.name == "nt":
        pytest.skip("Creating symlinks requires Windows privileges.")
    two_nodes(caller)
    target = caller / "seed.txt"
    path = {
        "source": "extra.contract.md",
        "input": "input.txt",
        "output": "last.txt",
        "checks": "checks",
    }[where]
    (caller / path).symlink_to(target)
    with pytest.raises(ContractError):
        prepare(caller)


@pytest.mark.parametrize("kind", ("caller", "contract", "checks", "policy"))
def test_mutation_between_leaves_stops_without_another_model(
    caller: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    two_nodes(caller)
    (caller / "checks").mkdir()
    (caller / "checks/fixed.txt").write_bytes(b"trusted")
    calls = producer(monkeypatch)
    original = engine.run_contract

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        path, content = {
            "caller": ("seed.txt", b"changed"),
            "contract": ("a-target.contract.md", b"changed"),
            "checks": ("checks/fixed.txt", b"changed"),
            "policy": ("apm.yml", b"invalid: ["),
        }[kind]
        (caller / path).write_bytes(content)
        return result

    monkeypatch.setattr(engine, "run_contract", changed)
    result = chain.run_chain(prepare(caller), logger=ContractLogger(), allow_advisory=True)
    assert not result.complete and result.outcome == Outcome.HALTED
    assert len(calls) == 1


def test_no_result_reuse_on_second_invocation(
    caller: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    two_nodes(caller)
    calls = producer(monkeypatch)
    first = chain.run_chain(prepare(caller), logger=ContractLogger(), allow_advisory=True)
    second = chain.run_chain(prepare(caller), logger=ContractLogger(), allow_advisory=True)
    assert first.complete and second.complete and len(calls) == 4
    assert {r.run_id for r in first.runs}.isdisjoint(r.run_id for r in second.runs)


def test_graph_limit_and_ignored_harness_context(caller: Path) -> None:
    two_nodes(caller)
    write_contract(caller, ".agents/skills/hidden.contract.md", (), "first.txt")
    assert len(resolution.resolve(Path("a-target.contract.md"), caller).catalog) == 2
    with pytest.raises(ContractError, match="At most"):
        resolution.resolve(
            Path("a-target.contract.md"), caller, limits=ContractLimits(chain_contracts=1)
        )


@pytest.mark.parametrize(
    ("kind", "outcome"),
    [
        ("rejected", Outcome.REJECTED),
        ("undecided", Outcome.UNPROVEN),
        ("missing-output", Outcome.UNPROVEN),
        ("producer-failed", Outcome.HALTED),
    ],
)
def test_actual_failure_classes_block_downstream(
    caller: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    outcome: Outcome,
) -> None:
    two_nodes(caller)
    if kind in ("rejected", "undecided"):
        write_contract(
            caller,
            "z-first.contract.md",
            ("seed.txt",),
            "first.txt",
            check=f"raise SystemExit({1 if kind == 'rejected' else 2})",
        )

    def failed_output(*_: object) -> str:
        return "pass" if kind == "missing-output" else "raise SystemExit(1)"

    calls = producer(
        monkeypatch,
        body=failed_output if kind in ("missing-output", "producer-failed") else None,
    )
    result = chain.run_chain(prepare(caller), logger=ContractLogger(), allow_advisory=True)
    assert result.outcome == outcome and not result.complete and len(calls) == 1
    assert json.loads(result.record_path.read_bytes())["nodes"][1]["state"] == "blocked"


@pytest.mark.parametrize(
    "fault",
    (
        "partial",
        "duplicate",
        "check-name",
        "check-command",
        "subject",
        "resources",
        "raw-exit",
        "bool-status",
        "complete",
        "result",
        "source",
        "imports",
        "manifest",
        "artifact",
        "baseline",
        "transcript",
        "record-json",
        "advisory_consent",
        "handoff_policy",
    ),
)
def test_finalized_record_and_required_assessment_gate(
    caller: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    two_nodes(caller)
    path = caller / "z-first.contract.md"
    path.write_text(path.read_text().replace("---\nProduce", "  second: 'exit 0'\n---\nProduce"))
    calls = producer(monkeypatch)
    original = engine.run_contract

    def tamper(*args, **kwargs):
        result = original(*args, **kwargs)
        first = result.checks[0]
        changed = {
            "check-name": lambda: replace(first, name="other"),
            "check-command": lambda: replace(first, command="exit 0"),
            "subject": lambda: replace(first, subject_digest="0" * 64),
            "resources": lambda: replace(first, resources_digest="0" * 64),
            "raw-exit": lambda: replace(first, process=replace(first.process, returncode=1)),
            "bool-status": lambda: replace(first, normalized=False),
        }
        if fault == "partial":
            result = replace(result, checks=(first,))
        elif fault == "duplicate":
            result = replace(result, checks=(first, first))
        elif fault in changed:
            result = replace(result, checks=(changed[fault](), *result.checks[1:]))
        record = result.run_directory / "record.json"
        data = json.loads(record.read_bytes())
        data["checks"], data["result"] = (
            records._json_value(result.checks),
            records._json_value(result),
        )
        if fault == "complete":
            data["complete"] = False
        elif fault == "result":
            data["result"]["run_id"] = "different"
        elif fault == "source":
            data["source"]["sha256"] = "0" * 64
        elif fault == "imports":
            data["imports"] = [{"name": "invented"}]
        elif fault == "manifest":
            data["consumer_manifest_sha256"] = "0" * 64
        elif fault in ("advisory_consent", "handoff_policy"):
            data[fault] = "different"
        elif fault == "artifact":
            result.artifact.path.chmod(0o600)
            result.artifact.path.write_bytes(b"changed")
        elif fault == "baseline":
            (result.run_directory / "baseline/seed.txt").write_bytes(b"changed")
        elif fault == "transcript":
            (result.run_directory / "transcript.log").write_bytes(b"changed")
        record.write_text(json.dumps(data), encoding="ascii")
        if fault == "record-json":
            record.write_text('{"complete": true, "complete": false}', encoding="ascii")
        return result

    monkeypatch.setattr(engine, "run_contract", tamper)
    result = chain.run_chain(prepare(caller), logger=ContractLogger(), allow_advisory=True)
    assert not result.complete and len(calls) == 1
    assert result.outcome == (Outcome.UNPROVEN if fault == "partial" else Outcome.HALTED)


@pytest.mark.parametrize("changed", ("artifact", "record"))
def test_binding_is_reread_after_assessment_before_next_capture(
    caller: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed: str,
) -> None:
    two_nodes(caller)
    calls = producer(monkeypatch)
    original = records.admit_handoff

    def mutate(*args, **kwargs):
        binding = original(*args, **kwargs)
        path = binding.artifact.path if changed == "artifact" else binding.record_path
        path.chmod(0o600)
        path.write_bytes(path.read_bytes() + b"changed")
        return binding

    monkeypatch.setattr(records, "admit_handoff", mutate)
    result = chain.run_chain(prepare(caller), logger=ContractLogger(), allow_advisory=True)
    assert result.outcome == Outcome.HALTED and len(calls) == 1 and not result.complete


@pytest.mark.parametrize("interrupt", (False, True))
@pytest.mark.parametrize("family", ("leaf", "chain"))
def test_finalization_failure_cannot_announce_completion(
    caller: Path,
    monkeypatch: pytest.MonkeyPatch,
    family: str,
    interrupt: bool,
) -> None:
    two_nodes(caller)
    calls = producer(monkeypatch)
    original = records.atomic_write_text
    failed = False

    def fail_once(path, text, **kwargs):
        nonlocal failed
        original(path, text, **kwargs)
        if not failed and path.parent.parent.name == ("runs" if family == "leaf" else "chains"):
            data = json.loads(text)
            if data.get("complete") is True:
                failed = True
                if interrupt:
                    raise KeyboardInterrupt()
                raise OSError("fsync failed after replacement")

    monkeypatch.setattr(records, "atomic_write_text", fail_once)
    result = CliRunner().invoke(
        main,
        [
            str(caller),
            "--on",
            "copilot",
            "--allow-host-access",
            "--allow-unproven-inputs",
        ],
    )
    assert result.exit_code == 22 and "(complete)" not in result.output
    assert len(calls) == (1 if family == "leaf" else 2)
    record = next((caller / ".apm/chains").glob("*/record.json"))
    assert json.loads(record.read_bytes())["complete"] is False


def test_cancellation_and_consent_are_distinct(
    caller: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    two_nodes(caller)
    calls = producer(monkeypatch)
    without_consent = CliRunner().invoke(
        main,
        [
            str(caller),
            "--on",
            "copilot",
            "--allow-unproven-inputs",
        ],
    )
    assert without_consent.exit_code == 21 and not (caller / ".apm").exists()
    assert calls == []
    monkeypatch.setattr(engine, "run_contract", Mock(side_effect=KeyboardInterrupt()))
    result = chain.run_chain(prepare(caller), logger=ContractLogger(), allow_advisory=True)
    assert result.outcome == Outcome.HALTED and result.stop_reason == "cancelled"
    assert not result.complete and json.loads(result.record_path.read_bytes())["complete"] is False
    invalid = CliRunner().invoke(
        main,
        [
            "a-target.contract.md",
            "--on",
            "copilot",
            "--allow-unproven-inputs",
        ],
    )
    assert invalid.exit_code == 2 and "requires a factory directory" in invalid.output


def test_frozen_inventory_rejects_last_moment_resource_change(
    caller: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    two_nodes(caller)
    calls = producer(monkeypatch)
    original = workspace.capture_workspace

    def changed(plan, directory):
        (caller / "seed.txt").write_bytes(b"changed after preflight")
        return original(plan, directory)

    monkeypatch.setattr(workspace, "capture_workspace", changed)
    result = chain.run_chain(prepare(caller), logger=ContractLogger(), allow_advisory=True)
    assert not result.complete and result.outcome == Outcome.HALTED and calls == []


def test_diamond_runs_common_predecessor_once(
    caller: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    two_nodes(caller)
    write_contract(caller, "other.contract.md", ("first.txt",), "other.txt")
    write_contract(caller, "a-target.contract.md", ("first.txt", "other.txt"), "last.txt")
    calls = producer(monkeypatch)
    result = chain.run_chain(prepare(caller), logger=ContractLogger(), allow_advisory=True)
    assert result.complete and len(calls) == 3
    assert result.runs[-1].artifact.path.read_bytes() == b"seed|seed"


def test_explicit_model_and_full_cli_closure(caller: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    two_nodes(caller)
    calls = producer(monkeypatch)
    result = CliRunner().invoke(
        main,
        [
            str(caller),
            "--on",
            "copilot",
            "--allow-host-access",
            "--allow-unproven-inputs",
            "--model",
            "configured-test-model",
        ],
    )
    assert result.exit_code == 21 and "apmx factory: UNPROVEN (complete)" in result.output
    assert len(calls) == 2 and all(call[0].model == "configured-test-model" for call in calls)


def test_absolute_outside_target_is_a_structured_refusal(caller: Path) -> None:
    outside = write_contract(caller.parent, "outside.contract.md", (), "output.txt")
    result = CliRunner().invoke(main, [str(outside), "--on", "copilot", "--plan"])
    assert result.exit_code == 22 and "Cannot select regular file" in result.output
    assert not (caller / ".apm").exists()


def test_static_chaining_boundary_uses_canonical_owners() -> None:
    """Prevent a subprocess wrapper, task-specific recipe or parallel record parser."""
    for module in (chain, resolution):
        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert imported.isdisjoint({"json", "subprocess"})
        assert "shipping.py" not in source and "review.contract.md" not in source
    calls = {
        ast.unparse(node.func)
        for node in ast.walk(ast.parse(Path(chain.__file__).read_text()))
        if isinstance(node, ast.Call)
    }
    assert {
        "engine.run_contract",
        "records.admit_handoff",
        "records.finalized_input",
        "workspace.inspect_workspace",
    } <= calls


def test_checker_runtime_is_admitted_before_first_model(
    caller: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apmx.contracts import check_command

    two_nodes(caller)
    calls = producer(monkeypatch)
    monkeypatch.setattr(
        check_command,
        "check_argv",
        Mock(side_effect=ContractError("Required check shell is missing.", code="check_shell")),
    )
    with pytest.raises(ContractError, match="shell"):
        prepare(caller)
    assert calls == [] and not (caller / ".apm").exists()


@pytest.mark.parametrize("changed", ("artifact", "record"))
def test_final_target_evidence_is_rechecked_before_aggregate_completion(
    caller: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed: str,
) -> None:
    two_nodes(caller)
    calls = producer(monkeypatch)
    original = records.finalized_input

    def after_assessment(plan, result):
        binding = original(plan, result)
        if result.artifact.relative_path == "last.txt":
            path = binding.record_path if changed == "record" else binding.artifact.path
            path.chmod(0o600)
            path.write_bytes(path.read_bytes() + b"changed after assessment")
        return binding

    monkeypatch.setattr(records, "finalized_input", after_assessment)
    result = chain.run_chain(prepare(caller), logger=ContractLogger(), allow_advisory=True)
    assert result.outcome == Outcome.HALTED and not result.complete and len(calls) == 2


@pytest.mark.parametrize("uppercase", (False, True))
def test_git_tracked_sibling_and_future_outputs_are_never_baseline_inputs(
    caller: Path,
    monkeypatch: pytest.MonkeyPatch,
    uppercase: bool,
) -> None:
    two_nodes(caller)
    write_contract(caller, "sibling.contract.md", ("seed.txt",), "sibling.txt")
    write_contract(caller, "a-target.contract.md", ("first.txt", "sibling.txt"), "last.txt")
    graph_outputs = ("first.txt", "sibling.txt", "last.txt")
    originals = {}
    for name in graph_outputs:
        path = caller / (name.upper() if uppercase else name)
        originals[path] = b"stale " + name.encode("ascii")
        path.write_bytes(originals[path])
    (caller / "unrelated.txt").write_bytes(b"not an aggregate root input")
    workspace.local_git(caller, "init", "--quiet")
    workspace.local_git(caller, "add", "--all")
    calls = producer(monkeypatch)
    result = chain.run_chain(prepare(caller), logger=ContractLogger(), allow_advisory=True)
    assert result.complete and len(calls) == 3
    for plan, snapshot, _ in calls:
        for name in graph_outputs:
            if name in plan.contract.needs:
                assert (snapshot.root / name).read_bytes() == b"seed"
                assert (snapshot.producer / name).read_bytes() == b"seed"
            else:
                assert not (snapshot.root / name).exists()
                if name != plan.contract.produces:
                    assert not (snapshot.producer / name).exists()
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    data = json.loads(result.record_path.read_bytes())
    assert {item["relative_path"] for item in data["artifacts"]["files"]} == {
        *graph_outputs,
        "seed.txt",
    }


@pytest.mark.parametrize(
    "fault",
    (
        "root-origin",
        "write",
        "published-bytes",
        "published-extra",
        pytest.param(
            "published-link",
            marks=pytest.mark.skipif(os.name == "nt", reason="Symlink privileges."),
        ),
    ),
)
def test_view_failure_is_not_a_completed_aggregate(
    caller: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    two_nodes(caller)
    calls = producer(monkeypatch)
    capture = workspace.capture_chain_view
    write = workspace._write

    def fail_write(root, entry, raw):
        if fault == "write" and root.name == "artifacts.pending":
            raise OSError("view storage unavailable")
        return write(root, entry, raw)

    def mutate(directory, completed, bindings):
        assert len(completed) == len(bindings) == 2
        if fault == "root-origin":
            (completed[0][1].run_directory / "baseline/seed.txt").write_bytes(b"tampered")
        view = capture(directory, completed, bindings)
        if fault == "published-bytes":
            path = view.root / "seed.txt"
            path.chmod(0o600)
            path.write_bytes(b"tampered view")
        elif fault == "published-extra":
            (view.root / "not-admitted.txt").write_bytes(b"unrecorded")
        elif fault == "published-link":
            moved = view.root.rename(directory / "moved")
            view.root.symlink_to(moved, target_is_directory=True)
        return view

    monkeypatch.setattr(workspace, "_write", fail_write)
    monkeypatch.setattr(workspace, "capture_chain_view", mutate)
    result = CliRunner().invoke(
        main,
        [
            str(caller),
            "--on",
            "copilot",
            "--allow-host-access",
            "--allow-unproven-inputs",
        ],
    )
    assert result.exit_code == 22 and "(complete)" not in result.output
    assert len(calls) == 2
    record = next((caller / ".apm/chains").glob("*/record.json"))
    assert json.loads(record.read_bytes())["complete"] is False
    if fault in ("root-origin", "write"):
        assert not (record.parent / "artifacts").exists()
    assert (caller / "seed.txt").read_bytes() == b"seed"


def test_view_reads_retained_roots_not_changed_live_caller(
    caller: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    two_nodes(caller)
    producer(monkeypatch)
    capture = workspace.capture_chain_view

    def caller_edited_after_admission(directory, completed, bindings):
        (caller / "seed.txt").write_bytes(b"caller edited after assessment")
        return capture(directory, completed, bindings)

    monkeypatch.setattr(workspace, "capture_chain_view", caller_edited_after_admission)
    result = chain.run_chain(prepare(caller), logger=ContractLogger(), allow_advisory=True)
    assert result.complete
    view = Path(json.loads(result.record_path.read_bytes())["artifacts"]["root"])
    assert (view / "seed.txt").read_bytes() == b"seed"
    assert (caller / "seed.txt").read_bytes() == b"caller edited after assessment"
