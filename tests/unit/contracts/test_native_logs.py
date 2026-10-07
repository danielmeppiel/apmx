"""Run-owned native logs stream independently of public protocol and outcomes."""

from __future__ import annotations

import os
import sys

import pytest

from apmx.contracts.events import EventEmitter
from apmx.contracts.models import ProcessRequest
from apmx.contracts.native_logs import NativeLogStream
from apmx.contracts.process import supervise_process
from apmx.core.contract_logger import ContractLogger, _Transcript

pytestmark = pytest.mark.component


def test_fragmented_logs_are_live_redacted_and_do_not_consume_evidence_budget(tmp_path, capsys):
    logs = tmp_path / "logs"
    logs.mkdir()
    logger = ContractLogger(verbose=True)
    logger._transcript = _Transcript(1024)
    logger.attach_run("run", tmp_path)
    events = EventEmitter("run", logger.on_event)
    mirror = NativeLogStream(logs, events)
    path = logs / "cli.log"
    secret = "ghp_" + "PRIVATE" * 8
    path.write_text("Starting request\nToken: " + secret[:15])
    mirror.poll()
    assert "Starting request" in capsys.readouterr().out
    with path.open("a") as stream:
        stream.write(secret[15:] + "\n")
        for _ in range(100):
            stream.write("Ordinary debug observation\n")
    mirror.poll()
    output = capsys.readouterr().out
    assert "PRIVATE" not in output and "***" in output
    events.emit("activity", source="checker", label="acceptance", text='{"subject":"retained"}')
    mirror.finish()
    logger.close()
    assert "Starting request" not in (tmp_path / "transcript.log").read_text()
    assert '{"subject":"retained"}' in (tmp_path / "transcript.log").read_text()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory", "non-log"])
def test_does_not_read_redirections_or_unrelated_files(tmp_path, kind):
    logs = tmp_path / "logs"
    logs.mkdir()
    private = tmp_path / "private.txt"
    private.write_text("DO_NOT_STREAM\n")
    path = logs / "cli.log"
    if kind == "symlink":
        path.symlink_to(private)
    elif kind == "hardlink":
        os.link(private, path)
    elif kind == "directory":
        path.mkdir()
    else:
        path = logs / "config.json"
        path.write_text("DO_NOT_STREAM\n")
    observed = []
    mirror = NativeLogStream(logs, EventEmitter("run", observed.append))
    mirror.finish()
    assert not any(item.kind == "native_diagnostic" for item in observed)
    assert private.read_text() == "DO_NOT_STREAM\n"
    if kind != "non-log":
        assert any(item.kind == "diagnostic" for item in observed)


def test_callback_failure_is_not_swallowed_as_a_file_read_problem(tmp_path):
    (tmp_path / "cli.log").write_text("Public debug line\n")

    def broken(event):
        raise OSError("terminal failed")

    mirror = NativeLogStream(tmp_path, EventEmitter("run", broken))
    with pytest.raises(OSError, match="terminal failed"):
        mirror.poll()


def test_byte_limit_omits_partial_tail_and_reports_the_bound(tmp_path, monkeypatch):
    monkeypatch.setattr("apmx.contracts.native_logs._MAX_BYTES", 32)
    (tmp_path / "cli.log").write_text("A complete line\nPRIVATE_PARTIAL_" + "x" * 100)
    observed = []
    mirror = NativeLogStream(tmp_path, EventEmitter("run", observed.append))
    mirror.finish()
    mirror.finish()
    assert [item.data["text"] for item in observed if item.kind == "native_diagnostic"] == [
        "A complete line"
    ]
    assert len([item for item in observed if item.kind == "diagnostic"]) == 1


def test_too_many_files_are_reported_without_unbounded_scanning(tmp_path):
    for index in range(9):
        (tmp_path / f"{index}.log").write_text("Do not select an arbitrary subset\n")
    observed = []
    NativeLogStream(tmp_path, EventEmitter("run", observed.append)).finish()
    assert [item.kind for item in observed] == ["diagnostic"]
    assert "eight-file" in observed[0].data["message"]


def test_empty_file_rotation_cannot_grow_lifetime_state_unboundedly(tmp_path):
    paths = [tmp_path / f"{index}.log" for index in range(8)]
    for path in paths:
        path.touch()
    observed = []
    mirror = NativeLogStream(tmp_path, EventEmitter("run", observed.append))
    mirror.poll()
    assert len(mirror._tails) == 8
    for path in paths:
        path.unlink()
    (tmp_path / "ninth.log").touch()
    mirror.finish()
    assert not mirror._tails
    assert [item.kind for item in observed] == ["diagnostic"]
    assert "lifetime limit" in observed[0].data["message"]


def test_redirected_directory_does_not_flush_old_partial_text_or_read_new_target(tmp_path):
    root = tmp_path / "logs"
    root.mkdir()
    (root / "cli.log").write_text("UNFINISHED_")
    outside = tmp_path / "unrelated"
    outside.mkdir()
    (outside / "cli.log").write_text("DO_NOT_STREAM\n")
    observed = []
    mirror = NativeLogStream(root, EventEmitter("run", observed.append))
    mirror.poll()
    root.rename(tmp_path / "old-logs")
    root.symlink_to(outside, target_is_directory=True)
    mirror.finish()
    assert not any(item.kind == "native_diagnostic" for item in observed)
    assert any("redirected" in item.data["message"] for item in observed)


def test_replaced_file_cannot_supply_a_continuation_to_a_partial_frame(tmp_path):
    path = tmp_path / "cli.log"
    path.write_text("UNFINISHED_")
    observed = []
    mirror = NativeLogStream(tmp_path, EventEmitter("run", observed.append))
    mirror.poll()
    path.rename(tmp_path / "old.txt")
    path.write_text("DO_NOT_STREAM\n")
    mirror.finish()
    assert not any(item.kind == "native_diagnostic" for item in observed)
    assert any("replaced" in item.data["message"] for item in observed)


def test_native_debug_visibility_is_explicit_without_changing_public_output(capsys):
    logger = ContractLogger()
    events = EventEmitter("run", logger.on_event)
    events.emit("native_diagnostic", source="harness", text="Low-level plumbing")
    events.emit("activity", source="harness", text="Reading declared inputs")
    output = capsys.readouterr().out
    assert "Low-level plumbing" not in output and "Reading declared inputs" in output
    logger.close()


def test_native_logs_arrive_before_process_exit_without_touching_stdout_protocol(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    marker = tmp_path / "observed"
    code = (
        "import pathlib,time;"
        f"p=pathlib.Path({str(logs / 'cli.log')!r});"
        "p.write_text('Native debug is live\\n');"
        f"marker=pathlib.Path({str(marker)!r});"
        "\nfor _ in range(100):"
        "\n if marker.exists(): break"
        "\n time.sleep(.02)"
        "\nelse: raise SystemExit(7)"
        "\nprint('public stdout')"
    )
    observed = []

    def event_sink(event):
        observed.append(event)
        if event.kind == "native_diagnostic":
            marker.write_text("observed before exit")

    output = bytearray()
    result = supervise_process(
        ProcessRequest((sys.executable, "-c", code), tmp_path, 5, log_directory=logs),
        on_bytes=lambda stream, value: output.extend(value),
        events=EventEmitter("run", event_sink),
    )
    assert result.returncode == 0 and result.cleanup_confirmed
    assert output == b"public stdout\n" or output == b"public stdout\r\n"
    assert [item.data["text"] for item in observed if item.kind == "native_diagnostic"] == [
        "Native debug is live"
    ]
