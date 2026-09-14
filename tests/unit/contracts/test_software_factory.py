"""Exact host handoffs and failure gates, including real source CLI integration."""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from apmx.cli import main as apmx_main
from apmx.contracts.frontend import parse_contract
from apmx.contracts.models import ProcessRequest
from apmx.runtime.factory import RuntimeFactory
from apmx.utils import console

from test_software_factory_support import EXAMPLE, Modules, RecordCase, fixture_copilot, outputs
from test_software_factory_support import factory as factory
from test_software_factory_support import record_case as record_case

pytestmark = pytest.mark.component


def test_exact_retained_output_not_stale_caller_file(
    factory: Modules, record_case: RecordCase
) -> None:
    stale = record_case.caller / "plan.json"
    stale.write_bytes(b"stale caller output")
    result = record_case.admit(factory)
    assert result.raw == outputs(factory)["plan.json"]
    assert result.artifact_path == record_case.run / "artifacts/plan.json"
    assert stale.read_bytes() == b"stale caller output"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", "apm-contract-run/9"),
        ("profile", "verified"),
        ("complete", False),
        ("complete", 1),
        ("phase", "execution"),
        ("run_id", "old-run"),
        ("attempt_id", "other/1"),
        ("caller_root", "/outside"),
        ("evidence_root", "/outside"),
        ("result.run_id", "old-run"),
        ("result.run_directory", "/outside"),
        ("result.outcome.name", "VERIFIED"),
        ("result.outcome.exit_code", 0),
        ("result.stop_reason", "timeout"),
        ("producer.returncode", 1),
        ("producer.returncode", False),
        ("producer.error", "failure"),
        ("producer.stop_reason", "timeout"),
        ("producer.cleanup_confirmed", False),
        ("child_pid", 1234),
        ("child_pgid", 1234),
        ("active_check", "contract"),
        ("native_reported_exit_code", 1),
        ("native_reported_exit_code", False),
        ("requested_model", "unsolicited-override"),
        ("controls.isolation", "sandboxed"),
        ("controls.spend_cap", "enforced"),
        ("source.path", "/outside/job.contract.md"),
        ("source.sha256", "0" * 64),
        ("source.retained.contract.contract.md", "SPECIAL"),
        ("source.retained_identities", []),
        ("baseline.files", []),
        ("baseline.root", "/outside"),
        ("baseline.producer", "/outside"),
        ("baseline.digest", "0" * 64),
        ("baseline.resources_digest", "0" * 64),
        ("artifact.relative_path", "../outside"),
        ("artifact.path", "/outside"),
        ("artifact.sha256", "0" * 64),
        ("artifact.size", 1),
        ("artifact.size", True),
        ("checks.0.name", "different"),
        ("checks.0.command", "exit 0"),
        ("checks.0.subject_digest", "0" * 64),
        ("checks.0.resources_digest", "0" * 64),
        ("checks.0.normalized", False),
        ("checks.0.normalized", 1),
        ("checks.0.normalized", 7),
        ("checks.0.process.returncode", 127),
        ("checks.0.process.error", "failure"),
        ("checks.0.process.cleanup_confirmed", False),
        ("checks.0.process.stop_reason", "timeout"),
        ("transcript.relative_path", "../outside"),
        ("transcript.sha256", "0" * 64),
        ("transcript.size", True),
    ],
)
def test_inconsistent_record_is_never_admitted(
    factory: Modules,
    record_case: RecordCase,
    field: str,
    value: Any,
) -> None:
    if value == "SPECIAL":
        record_case.record["source"]["retained"]["contract.contract.md"] = "/outside"
    else:
        target = record_case.record
        parts = field.split(".")
        for part in parts[:-1]:
            target = target[int(part)] if isinstance(target, list) else target[part]
        target[int(parts[-1]) if isinstance(target, list) else parts[-1]] = value
    record_case.save()
    with pytest.raises(factory.evidence.Stop) as error:
        record_case.admit(factory)
    assert error.value.exit_code == 22


@pytest.mark.parametrize(
    "field",
    [
        "schema",
        "result",
        "producer",
        "source",
        "baseline",
        "controls",
        "checks",
        "artifact",
        "requested_model",
        "transcript",
        "child_pid",
    ],
)
def test_missing_required_record_field_fails_closed(
    factory: Modules,
    record_case: RecordCase,
    field: str,
) -> None:
    del record_case.record[field]
    record_case.save()
    with pytest.raises(factory.evidence.Stop):
        record_case.admit(factory)


@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        b'{"schema":1,"schema":2}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":1e9999}',
        b"null",
        b"[]",
        b"\xff",
        b"x" * (2 * 1024 * 1024 + 1),
    ],
)
def test_malformed_or_oversized_records_fail_closed(
    factory: Modules,
    record_case: RecordCase,
    raw: bytes,
) -> None:
    (record_case.run / "record.json").write_bytes(raw)
    with pytest.raises(factory.evidence.Stop):
        record_case.admit(factory)


@pytest.mark.parametrize("which", ("empty", "extra", "different-top-copy"))
def test_exact_nonempty_check_set_is_required(
    factory: Modules,
    record_case: RecordCase,
    which: str,
) -> None:
    if which == "different-top-copy":
        record_case.record["checks"] = []
    else:
        checks = [] if which == "empty" else record_case.record["checks"] * 2
        record_case.record["checks"] = record_case.record["result"]["checks"] = checks
    record_case.save()
    with pytest.raises(factory.evidence.Stop):
        record_case.admit(factory)


def test_incomplete_21_does_not_count_as_pass(factory: Modules, record_case: RecordCase) -> None:
    record_case.record["checks"][0]["normalized"] = 2
    record_case.record["checks"][0]["process"]["returncode"] = 127
    record_case.save()
    with pytest.raises(factory.evidence.Stop) as error:
        record_case.admit(factory)
    assert error.value.exit_code == 21


def test_missing_artifact_21_does_not_use_stale_output(
    factory: Modules, record_case: RecordCase
) -> None:
    record_case.record["artifact"] = record_case.record["result"]["artifact"] = None
    record_case.record["checks"] = record_case.record["result"]["checks"] = []
    (record_case.caller / "plan.json").write_bytes(b"stale")
    record_case.save()
    with pytest.raises(factory.evidence.Stop) as error:
        record_case.admit(factory)
    assert error.value.exit_code == 21


@pytest.mark.parametrize("code", (0, 2, -15, 19, 99))
def test_unknown_or_zero_execution_exits_do_not_certify(
    factory: Modules,
    record_case: RecordCase,
    code: int,
) -> None:
    with pytest.raises(factory.evidence.Stop) as error:
        record_case.admit(factory, code)
    assert error.value.exit_code == 22


@pytest.mark.parametrize(
    "name",
    (
        "artifacts/plan.json",
        "baseline/request.json",
        "baseline/checks/verify.py",
        "source/contract.contract.md",
        "transcript.log",
    ),
)
def test_changed_retained_bytes_refuse(
    factory: Modules, record_case: RecordCase, name: str
) -> None:
    (record_case.run / name).write_bytes(b"changed")
    with pytest.raises(factory.evidence.Stop):
        record_case.admit(factory)


def test_changed_original_input_refuses(factory: Modules, record_case: RecordCase) -> None:
    (record_case.caller / "request.json").write_bytes(b"changed")
    with pytest.raises(factory.evidence.Stop):
        record_case.admit(factory)


def test_conflicting_artifact_copies_refuse(factory: Modules, record_case: RecordCase) -> None:
    record_case.record["result"]["artifact"] = dict(record_case.record["artifact"], size=999)
    record_case.save()
    with pytest.raises(factory.evidence.Stop, match="Conflicting"):
        record_case.admit(factory)


def test_changed_file_during_read_refuses(
    factory: Modules,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "file").write_bytes(b"data")
    original = os.fstat
    calls = 0

    def changed(descriptor: int) -> Any:
        nonlocal calls
        info = original(descriptor)
        calls += 1
        return SimpleNamespace(
            st_mode=info.st_mode,
            st_dev=info.st_dev,
            st_ino=info.st_ino,
            st_size=info.st_size,
            st_mtime_ns=info.st_mtime_ns + calls,
            st_ctime_ns=info.st_ctime_ns,
        )

    with monkeypatch.context() as scoped:
        scoped.setattr(factory.evidence.os, "fstat", changed)
        with pytest.raises(factory.evidence.Stop, match="changed"):
            factory.evidence.read(tmp_path, "file")


def test_windows_creation_and_change_times_use_matched_apis(
    factory: Modules,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "file"
    target.write_bytes(b"unchanged\r\nbytes")
    original_lstat, original_fstat = Path.lstat, os.fstat
    descriptors: list[int] = []

    def metadata(info: os.stat_result, ctime: int) -> Any:
        return SimpleNamespace(
            st_mode=info.st_mode,
            st_dev=info.st_dev,
            st_ino=info.st_ino,
            st_size=info.st_size,
            st_mtime_ns=info.st_mtime_ns,
            st_ctime_ns=ctime,
        )

    def creation_time(path: Path) -> Any:
        info = original_lstat(path)
        return metadata(info, 100) if path == target else info

    def change_time(descriptor: int) -> Any:
        descriptors.append(descriptor)
        return metadata(original_fstat(descriptor), 200)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "lstat", creation_time)
        scoped.setattr(factory.evidence.os, "fstat", change_time)
        assert factory.evidence.read(tmp_path, "file") == b"unchanged\r\nbytes"
    assert len(descriptors) == 3
    assert descriptors[0] == descriptors[1] and descriptors[2] != descriptors[0]


@pytest.mark.parametrize("observation", (2, 3), ids=("after-read", "named-path"))
@pytest.mark.parametrize(
    "field",
    ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"),
)
def test_each_capture_identity_field_must_stay_stable(
    factory: Modules,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    observation: int,
    field: str,
) -> None:
    (tmp_path / "file").write_bytes(b"data")
    original = os.fstat
    calls = 0

    def changed(descriptor: int) -> Any:
        nonlocal calls
        info = original(descriptor)
        calls += 1
        values = {
            key: getattr(info, key)
            for key in ("st_mode", "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        }
        if calls == observation:
            values[field] += 1
        return SimpleNamespace(**values)

    with monkeypatch.context() as scoped:
        scoped.setattr(factory.evidence.os, "fstat", changed)
        with pytest.raises(factory.evidence.Stop, match="changed"):
            factory.evidence.read(tmp_path, "file")


@pytest.mark.skipif(os.name != "nt", reason="Native Windows path/descriptor metadata regression.")
def test_native_windows_rewritten_binary_file(factory: Modules, tmp_path: Path) -> None:
    target = tmp_path / "file"
    target.write_bytes(b"first")
    raw = b"rewritten\r\n\x00\x1a\nbytes"
    target.write_bytes(raw)
    os.utime(target, ns=(1700000000000000000, 1700000000000000000))
    assert factory.evidence.read(tmp_path, "file") == raw


def test_windows_reparse_attribute_refuses_without_following(
    factory: Modules,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "reparse"
    target.write_bytes(b"data")
    original = Path.lstat

    def reparse(path: Path) -> Any:
        info = original(path)
        if path == target:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(factory.evidence.Stop, match="reparse"):
        factory.evidence.read(tmp_path, "reparse")


def test_extra_run_is_ambiguity_not_latest(factory: Modules, record_case: RecordCase) -> None:
    (record_case.run.parent / "20260914T110000Z-abcdef012345").mkdir()
    with pytest.raises(factory.evidence.Stop, match="one run"):
        record_case.admit(factory)


@pytest.mark.skipif(os.name == "nt", reason="Creating symlinks can require Windows privileges.")
def test_symlinked_artifact_and_ancestor_refuse(factory: Modules, record_case: RecordCase) -> None:
    artifact = record_case.run / "artifacts/plan.json"
    original = artifact.read_bytes()
    artifact.unlink()
    outside = record_case.caller.parent / "outside.json"
    outside.write_bytes(original)
    artifact.symlink_to(outside)
    with pytest.raises(factory.evidence.Stop, match="Symlink"):
        record_case.admit(factory)
    link = record_case.caller.parent / "linked"
    link.symlink_to(record_case.caller, target_is_directory=True)
    with pytest.raises(factory.evidence.Stop, match="Symlink"):
        factory.evidence.read(link, "request.json")


@pytest.mark.parametrize("name", ("../outside", "/outside", "a//b", "a/./b", "a\\b", "C:/outside"))
def test_unsafe_relative_paths_refuse(factory: Modules, tmp_path: Path, name: str) -> None:
    with pytest.raises(factory.evidence.Stop):
        factory.evidence.child(tmp_path, name)


def test_byte_copy_cannot_overwrite(factory: Modules, tmp_path: Path) -> None:
    factory.evidence.write_new(tmp_path, "owned", b"first")
    with pytest.raises(FileExistsError):
        factory.evidence.write_new(tmp_path, "owned", b"second")
    assert (tmp_path / "owned").read_bytes() == b"first"


def test_existing_or_git_workspace_is_refused(factory: Modules, tmp_path: Path) -> None:
    with pytest.raises(factory.evidence.Stop, match="already exists"):
        factory.driver.new_workspace(tmp_path)
    (tmp_path / ".git").mkdir()
    with pytest.raises(factory.evidence.Stop, match="Git"):
        factory.driver.new_workspace(tmp_path / "fresh")
    assert not (tmp_path / "fresh").exists()


def test_no_consent_launches_nothing(
    factory: Modules, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    execute = Mock(side_effect=AssertionError("must not run"))
    monkeypatch.setattr(factory.driver, "execute", execute)
    code, ledger = factory.driver.run_factory(
        tmp_path / "fresh", Path(sys.executable), allow_host_access=False
    )
    assert code == 21 and ledger["complete"] is False
    assert not (tmp_path / "fresh").exists()
    execute.assert_not_called()


def test_host_cli_is_noninteractive_without_consent(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(EXAMPLE / "run.py"),
            "--apmx",
            sys.executable,
            "--workspace",
            str(tmp_path / "fresh"),
        ],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 21
    document = json.loads(result.stdout)
    assert document["complete"] is False and document["stages"] == []
    assert not (tmp_path / "fresh").exists()


def test_help_does_not_need_workspace_or_launches() -> None:
    result = subprocess.run(
        [sys.executable, "-B", str(EXAMPLE / "run.py"), "--help"],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0
    assert b"completion exits 21" in b" ".join(result.stdout.split())


@pytest.mark.parametrize(
    "python", ("/a path/python", "/a'quote/python", "C:\\Program Files\\Python\\python.exe")
)
@pytest.mark.parametrize("newline", (b"\n", b"\r\n"), ids=("LF", "CRLF"))
def test_checker_quoting_and_real_parser_coherence(
    factory: Modules,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    python: str,
    newline: bytes,
) -> None:
    source = tmp_path / "templates"
    (source / "contracts").mkdir(parents=True)
    monkeypatch.setattr(factory.driver, "EXAMPLE", source)
    for stage in factory.driver.STAGES:
        name = f"contracts/{stage.name}.contract.md"
        original = (EXAMPLE / name).read_bytes().replace(b"\r\n", b"\n")
        template = original.replace(b"\n", newline)
        (source / name).write_bytes(template)
        retained, raw, command = factory.driver.contract_bytes(stage, python)
        assert retained == template == (source / name).read_bytes()
        expected = original.replace(
            f"  contract: python3 -I -B checks/verify.py {stage.name}\n".encode("ascii"),
            ("  contract: " + json.dumps(command) + "\n").encode("ascii"),
            1,
        )
        assert raw == expected
        path = tmp_path / f"{stage.name}.contract.md"
        path.write_bytes(raw)
        contract = parse_contract(path)
        assert contract.needs == stage.needs and contract.produces == stage.output
        assert [(item.name, item.command) for item in contract.checks] == [("contract", command)]
        assert shlex.split(command) == [
            python.replace("\\", "/"),
            "-I",
            "-B",
            "checks/verify.py",
            stage.name,
        ]
        assert not contract.imports


@pytest.mark.parametrize("failure", ("timeout", "cancel", "lingering"))
def test_timeout_and_cancellation_wait_then_fail_closed(
    factory: Modules,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    process = Mock()
    initial = (
        KeyboardInterrupt() if failure == "cancel" else subprocess.TimeoutExpired("apmx", 1260)
    )
    process.wait.side_effect = (
        [initial, subprocess.TimeoutExpired("apmx", 15), 0]
        if failure == "lingering"
        else [initial, 21]
    )
    launch = Mock(return_value=process)
    monkeypatch.setattr(factory.driver.subprocess, "Popen", launch)
    with pytest.raises(factory.evidence.Stop) as error:
        factory.driver.execute(["native-apmx", "job.contract.md"], tmp_path)
    assert error.value.exit_code == 22
    process.send_signal.assert_called_once()
    assert process.wait.call_args_list[1].kwargs == {"timeout": 15}
    assert process.kill.call_count == (1 if failure == "lingering" else 0)
    assert launch.call_args.kwargs["shell"] is False


def test_unconfirmed_leader_stop_is_not_reported_as_cleanup(
    factory: Modules,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process = Mock()
    process.wait.side_effect = subprocess.TimeoutExpired("apmx", 1)
    monkeypatch.setattr(factory.driver.subprocess, "Popen", Mock(return_value=process))
    with pytest.raises(factory.evidence.Stop, match="stop unconfirmed"):
        factory.driver.execute(["native-apmx"], tmp_path)
    process.kill.assert_called_once()


def test_failed_ledger_write_has_no_success_shaped_fallback(
    factory: Modules,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(factory.driver.os, "replace", Mock(side_effect=OSError("storage failed")))
    with pytest.raises(OSError):
        factory.driver.run_factory(tmp_path / "fresh", Path(sys.executable), allow_host_access=True)
    assert not (tmp_path / "fresh/factory-run.json").exists()


def test_interrupt_with_pending_ledger_returns_structured_halted(
    factory: Modules,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    replace = Mock(side_effect=KeyboardInterrupt())
    execute = Mock(side_effect=AssertionError("must not run"))
    monkeypatch.setattr(factory.driver.os, "replace", replace)
    monkeypatch.setattr(factory.driver, "execute", execute)
    workspace = tmp_path / "fresh"
    try:
        code = factory.driver.main(
            [
                "--workspace",
                str(workspace),
                "--apmx",
                sys.executable,
                "--allow-host-access",
            ]
        )
    except KeyboardInterrupt:
        pytest.fail("Pending-ledger interruption must return structured HALTED22, not escape.")
    captured = capsys.readouterr()
    assert code == 22 and captured.out == ""
    result = json.loads(captured.err)
    assert result["complete"] is False and result["exit_code"] == 22
    assert "persistence" in result["error"] and "unconfirmed" in result["error"]
    assert (workspace / "factory-run.json.new").is_file()
    assert not (workspace / "factory-run.json").exists()
    replace.assert_called_once()
    execute.assert_not_called()


def source_launcher(
    factory: Modules,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str | None = None,
) -> list[tuple[list[str], Path]]:
    """Use actual source CLI/admission/engine/checks/records; substitute only producer execution."""
    fixture_copilot(tmp_path, monkeypatch)
    produced = outputs(factory)
    calls: list[tuple[list[str], Path]] = []

    class Producer:
        def build_contract_request(
            self,
            plan: Any,
            snapshot: Any,
            directory: Path,
            *,
            timeout_seconds: float,
        ) -> ProcessRequest:
            output = plan.contract.produces
            raw = produced[output]
            at_failure = output == "shipping.py"
            if at_failure and failure == "rejected":
                raw = raw.replace(b"return 500", b"return 501")
            write = (
                ""
                if at_failure and failure == "incomplete"
                else f"Path({output!r}).write_bytes({raw!r})\n"
            )
            code = 7 if at_failure and failure == "halted" else 0
            completion = json.dumps(
                {"type": "result", "exitCode": code, "sessionId": "source-fixture", "usage": {}}
            )
            program = (
                "from pathlib import Path\n"
                + write
                + f"print({completion!r}, flush=True)\n"
                + f"raise SystemExit({code})\n"
            )
            return ProcessRequest(
                argv=(sys.executable, "-B", "-c", program),
                cwd=snapshot.producer,
                timeout_seconds=min(timeout_seconds, 20),
            )

    def launch(argv: list[str], caller: Path) -> int:
        calls.append((argv, caller))
        with monkeypatch.context() as scoped:
            scoped.chdir(caller)
            scoped.setattr(RuntimeFactory, "get_runtime_by_name", lambda *args: Producer())
            console._reset_console()
            result = CliRunner().invoke(apmx_main, argv[1:])
            console._reset_console()
        if result.exit_code not in (20, 21, 22):
            pytest.fail(f"Unexpected source CLI failure: {result.output}\n{result.exception}")
        return result.exit_code

    monkeypatch.setattr(factory.driver, "execute", launch)
    return calls


def test_five_phase_chain_through_real_source_cli(
    factory: Modules,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = {
        path.relative_to(EXAMPLE).as_posix(): path.read_bytes()
        for path in EXAMPLE.rglob("*")
        if path.is_file()
    }
    calls = source_launcher(factory, monkeypatch, tmp_path)
    workspace = tmp_path / "fresh caller with spaces"
    code, ledger = factory.driver.run_factory(
        workspace, Path(sys.executable), allow_host_access=True
    )
    assert code == 21 and ledger["complete"] is True and ledger["assurance"] == "UNPROVEN", ledger
    assert len(calls) == 5 and len({caller for _, caller in calls}) == 5
    assert all("--model" not in argv for argv, _ in calls)
    assert ledger["review"]["recommendation"] == "follow_up"
    assert ledger["review"]["findings"][0]["detail"] == "Confirm the example rates before real use."
    expected = outputs(factory)
    for stage, entry, (_, caller) in zip(
        factory.driver.STAGES, ledger["stages"], calls, strict=True
    ):
        record = json.loads((workspace / entry["record"]).read_bytes())
        assert record["result"]["outcome"] == {"name": "UNPROVEN", "exit_code": 21}
        assert (workspace / entry["artifact"]).read_bytes() == expected[stage.output]
        assert not (caller / stage.output).exists()
        for name in stage.needs:
            identity = next(
                item for item in record["baseline"]["files"] if item["relative_path"] == name
            )
            assert identity["sha256"] == factory.evidence.sha((caller / name).read_bytes())
            if name in expected:
                assert (caller / name).read_bytes() == expected[name]
    assert (workspace / "result/shipping.py").read_bytes() == expected["shipping.py"]
    assert json.loads((workspace / "factory-run.json").read_bytes()) == ledger
    assert (
        factory.checks.main(["quote", "--directory", str(workspace / "result"), "--weight", "1001"])
        == 0
    )
    after = {
        path.relative_to(EXAMPLE).as_posix(): path.read_bytes()
        for path in EXAMPLE.rglob("*")
        if path.is_file()
    }
    assert before == after


@pytest.mark.parametrize(
    ("failure", "expected"), [("rejected", 20), ("incomplete", 21), ("halted", 22)]
)
def test_source_failure_stops_at_build(
    factory: Modules,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected: int,
) -> None:
    calls = source_launcher(factory, monkeypatch, tmp_path, failure)
    workspace = tmp_path / "fresh"
    code, ledger = factory.driver.run_factory(
        workspace, Path(sys.executable), allow_host_access=True, model="explicit-fixture-model"
    )
    assert code == expected and ledger["complete"] is False, ledger
    assert ledger["stop"]["stage"] == "build"
    assert len(calls) == 3 and len(ledger["stages"]) == 2
    assert all(argv[-2:] == ["--model", "explicit-fixture-model"] for argv, _ in calls)
    assert not (workspace / "result").exists()


def test_no_record_stops_instead_of_reusing_prior_stage(
    factory: Modules,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = Mock(return_value=21)
    monkeypatch.setattr(factory.driver, "execute", calls)
    code, ledger = factory.driver.run_factory(
        tmp_path / "fresh", Path(sys.executable), allow_host_access=True
    )
    assert code == 21 and ledger["complete"] is False
    assert calls.call_count == 1 and ledger["stages"] == []


def test_broken_progress_pipe_stops_before_launch(
    factory: Modules,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = Mock(return_value=21)
    monkeypatch.setattr(factory.driver, "execute", calls)
    with monkeypatch.context() as scoped:
        scoped.setattr("builtins.print", Mock(side_effect=BrokenPipeError("closed")))
        code, ledger = factory.driver.run_factory(
            tmp_path / "fresh",
            Path(sys.executable),
            allow_host_access=True,
        )
    assert code == 22 and ledger["complete"] is False
    calls.assert_not_called()


def test_future_caller_collision_is_not_reused(
    factory: Modules,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = source_launcher(factory, monkeypatch, tmp_path)
    original = factory.driver.execute

    def collide(argv: list[str], caller: Path) -> int:
        code = original(argv, caller)
        future = caller.parent / "02-specification"
        future.mkdir()
        (future / "plan.json").write_bytes(b"outside stage data")
        return code

    monkeypatch.setattr(factory.driver, "execute", collide)
    workspace = tmp_path / "fresh"
    code, ledger = factory.driver.run_factory(
        workspace,
        Path(sys.executable),
        allow_host_access=True,
    )
    assert code == 22 and ledger["complete"] is False and len(calls) == 1
    assert (workspace / "stages/02-specification/plan.json").read_bytes() == b"outside stage data"


def test_policy_disable_is_not_silently_removed(
    factory: Modules,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = source_launcher(factory, monkeypatch, tmp_path)
    monkeypatch.setenv("APM_POLICY_DISABLE", "1")
    code, ledger = factory.driver.run_factory(
        tmp_path / "fresh", Path(sys.executable), allow_host_access=True
    )
    assert code == 21 and ledger["complete"] is False and len(calls) == 1
    assert os.environ["APM_POLICY_DISABLE"] == "1"


def test_fake_record_narration_is_not_a_locator(
    factory: Modules,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record_case: RecordCase,
) -> None:
    def narrate(argv: list[str], caller: Path) -> int:
        print(f"  Record: {record_case.run / 'record.json'}")
        return 21

    monkeypatch.setattr(factory.driver, "execute", narrate)
    code, ledger = factory.driver.run_factory(
        tmp_path / "other", Path(sys.executable), allow_host_access=True
    )
    assert code == 21 and ledger["complete"] is False and ledger["stages"] == []
