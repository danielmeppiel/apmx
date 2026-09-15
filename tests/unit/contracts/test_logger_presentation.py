"""Offline contract display/evidence invariants. No native executable is invoked."""

import hashlib
import io
import sys
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path
from unittest.mock import Mock

import click
import pytest
from rich.console import Console

from apmx.contracts.models import (
    ChainResult,
    CheckObservation,
    Outcome,
    ProcessObservation,
    RunEvent,
    RunResult,
)
from apmx.core.contract_logger import (
    ContractLogger,
    _CheckEvidence,
    _DisplayLine,
    _Layout,
    _Role,
)
from apmx.utils import console

pytestmark = pytest.mark.component


@pytest.fixture(autouse=True)
def plain_policy(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("CI", "false")
    monkeypatch.setenv("APM_PROGRESS", "never")
    console._reset_console()
    yield
    console._reset_console()


def _event(logger, kind, *, at=0.0, source="engine", **data):
    logger.on_event(RunEvent("run", 1, at, kind, source, data))


def _check(name="document", normalized=0, raw=0):
    return CheckObservation(
        name,
        "not executed",
        ProcessObservation(returncode=raw),
        normalized,
        "subject",
        "resources",
        "The supplied subject or check resources changed.",
    )


def _run(directory, *, outcome=Outcome.UNPROVEN):
    return RunResult("run", directory, outcome, None, (_check(),))


class _Output(io.TextIOWrapper):
    tty = True

    def isatty(self):
        return self.tty


@dataclass
class _Capture:
    raw: io.BytesIO
    stream: _Output
    rich: Console
    encoding: str

    @property
    def text(self):
        return self.raw.getvalue().decode(self.encoding)


@contextmanager
def _terminal(
    monkeypatch, *, width=80, mode="styled", encoding="ascii", fallback=False, newline=None
):
    raw = io.BytesIO()
    stream = _Output(raw, encoding=encoding, errors="strict", write_through=True, newline=newline)
    stream.tty = mode != "pipe"
    rich = Console(
        file=stream,
        force_terminal=stream.tty,
        width=width,
        color_system="standard",
        no_color=mode == "no_color",
    )
    capture = _Capture(raw, stream, rich, encoding)
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", stream)
        patch.setattr(console, "_get_console", lambda: None if fallback else rich)
        patch.setattr(console, "_console_stderr", False)
        patch.setenv("COLUMNS", str(width))
        patch.setenv("TERM", "dumb" if mode == "dumb" else "xterm-256color")
        patch.setenv("CI", "true" if mode == "ci" else "false")
        if mode == "no_color":
            patch.setenv("NO_COLOR", "")
        else:
            patch.delenv("NO_COLOR", raising=False)
        yield capture


@pytest.mark.parametrize(
    "lines",
    [
        ["BEGIN-" + "x" * 100_000 + "-END"],
        [f"line-{index:05d}-" + "x" * 256 for index in range(10_000)],
        [""] * 100,
        ["x" * 8191],
        ["short", "last"],
    ],
)
def test_check_evidence_has_exact_bounded_head_tail_accounting(lines):
    evidence = _CheckEvidence("document")
    for text in lines:
        evidence.append(text)
        assert evidence.pending_bytes <= 8192
        assert evidence.pending_lines <= 32
    excerpt = evidence.excerpt()
    assert len(excerpt.fragments) <= 8
    kept = sum(len(item.text) for item in excerpt.fragments)
    assert kept <= 1024
    assert evidence.total_bytes == sum(len(text) + 1 for text in lines)
    assert excerpt.omitted_bytes == evidence.total_bytes - kept
    represented = {}
    for item in excerpt.fragments:
        original = lines[item.line] + "\n"
        assert item.text == original[item.offset : item.offset + len(item.text)]
        represented[item.line] = represented.get(item.line, 0) + len(item.text)
    assert excerpt.omitted_lines == len(lines) - len(represented)
    assert excerpt.partial_lines == sum(
        kept_bytes < len(lines[number]) + 1 for number, kept_bytes in represented.items()
    )
    assert excerpt.fragments[0].line == 0
    assert excerpt.fragments[0].offset == 0
    assert excerpt.fragments[-1].line == len(lines) - 1
    assert excerpt.fragments[-1].text.endswith("\n")


@pytest.mark.parametrize("verbose", [False, True])
def test_stdout_flood_cannot_hide_or_replay_stderr_and_does_not_certify_json(
    tmp_path,
    capsys,
    verbose,
):
    logger = ContractLogger(verbose=verbose)
    logger.attach_run("run", tmp_path)
    _event(logger, "check_started", name="document")
    secret = "ghp_" + "PRIVATE" * 8
    first = '{"status":"passed","token":"' + secret + '"} BEGIN-' + "x" * 20_000
    _event(logger, "activity", source="checker", label="document", text=first)
    for index in range(100):
        _event(
            logger,
            "activity",
            source="checker",
            label="document",
            text=f"line-{index:03d}-" + "x" * 256,
        )
    _event(
        logger,
        "activity",
        source="checker",
        label="document",
        stream="stderr",
        text="Immediate stderr diagnostic",
    )
    early = capsys.readouterr().out
    assert early.count("Immediate stderr diagnostic") == 1
    assert ("BEGIN-" in early) is verbose
    _event(logger, "activity", source="checker", label="document", text="Meaningful final detail")
    # Raw zero and contradictory stdout cannot override normalized incomplete.
    _event(logger, "check_finished", observation=_check(normalized=2, raw=0))
    logger.close()
    late = capsys.readouterr().out
    transcript = (tmp_path / "transcript.log").read_text()
    assert "[!] document: incomplete" in late
    assert "[+] document: passed" not in early + late
    assert "The supplied subject or check resources changed." in late
    assert "Immediate stderr diagnostic" not in late
    assert (late.count("stdout excerpt:") == 1) is not verbose
    if not verbose:
        assert late.index("[!] document: incomplete") < late.index("stdout excerpt:")
        assert "sanitized bytes omitted" in late
        assert "Meaningful final detail" in late
        assert len(late) < 2200
    assert "PRIVATE" not in early + late + transcript
    assert transcript.count("Meaningful final detail") == 1
    assert transcript.count("Immediate stderr diagnostic") == 1
    assert "Check document: raw exit 0" in transcript
    assert "stdout excerpt:" not in transcript
    assert logger._check_evidence is None


def test_missing_completion_and_identity_changes_flush_once_without_inventing_results(
    tmp_path, capsys
):
    logger = ContractLogger()
    logger.attach_run("run", tmp_path)
    _event(logger, "check_started", name="first")
    _event(logger, "activity", source="checker", label="first", text="First stdout")
    _event(
        logger, "activity", source="checker", label="first", stream="stderr", text="First stderr"
    )
    _event(logger, "check_started", name="second")
    _event(logger, "activity", source="checker", label="second", text="Second stdout")
    # A completion for the wrong identity cannot consume the second check's bytes.
    _event(logger, "check_finished", observation=_check("first"))
    _event(logger, "check_started", name="third")
    _event(logger, "activity", source="checker", label="third", text="Third stdout")
    logger.close()
    digest = hashlib.sha256((tmp_path / "transcript.log").read_bytes()).hexdigest()
    logger.close()
    output = capsys.readouterr().out
    for name in ("first", "second", "third"):
        assert output.count(f"Check {name}: completion was not observed.") == 1
        assert output.count(f"Check {name} stdout excerpt:") == 1
    assert output.count("First stderr") == 1
    assert "second: incomplete" not in output
    assert "third: failed" not in output
    assert logger._check_evidence is None
    assert hashlib.sha256((tmp_path / "transcript.log").read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("normalized", [0, 1, 2])
def test_new_check_of_same_name_cannot_reuse_previous_evidence(capsys, normalized):
    logger = ContractLogger()
    for message in ("OLD stdout", "NEW stdout"):
        _event(logger, "check_started", name="same")
        _event(logger, "activity", source="checker", label="same", text=message)
        _event(logger, "check_finished", observation=_check("same", normalized))
        output = capsys.readouterr().out
        assert (message in output) is (normalized != 0)
        if message.startswith("NEW"):
            assert "OLD stdout" not in output
        assert logger._check_evidence is None


def _replay(logger):
    _event(
        logger,
        "selected",
        contract="job.contract.md",
        produces="output.txt",
        model="model",
        run_directory="fixed-evidence-root",
    )
    _event(logger, "phase", name="execution")
    _event(
        logger, "activity", at=4, source="harness", text="Tool started: view", tool_status="started"
    )
    _event(
        logger, "metadata", at=4.5, source="harness", text="Retained telemetry", retained_only=True
    )
    _event(logger, "heartbeat", at=5, elapsed_seconds=5)
    _event(logger, "phase", at=6, name="checks")
    _event(logger, "check_started", at=6, name="document")
    _event(
        logger, "activity", at=10, source="checker", label="document", text='{"status":"passed"}'
    )
    _event(
        logger,
        "activity",
        at=10.2,
        source="checker",
        label="document",
        stream="stderr",
        text="Observed stderr",
    )
    _event(logger, "heartbeat", at=16, elapsed_seconds=16)
    _event(logger, "check_finished", at=17, observation=_check(normalized=2))
    _event(logger, "phase", at=18, name="record")


def test_transcript_is_identical_across_visibility_layout_encoding_and_finalization(
    tmp_path, monkeypatch
):
    transcripts = []
    outputs = {}
    for verbose in (False, True):
        for mode in ("styled", "no_color", "pipe", "ci", "dumb"):
            directory = tmp_path / f"{verbose}-{mode}"
            directory.mkdir()
            with _terminal(monkeypatch, width=40, mode=mode, encoding="cp1252") as terminal:
                logger = ContractLogger(verbose=verbose)
                logger.attach_run("run", directory)
                _replay(logger)
                logger.close()
                path = directory / "transcript.log"
                frozen = path.read_bytes()
                _event(logger, "finished", at=20, result=_run(directory))
                logger.close()
                assert path.read_bytes() == frozen
                transcripts.append(frozen)
                outputs[verbose, mode] = click.unstyle(terminal.text)
    assert len(set(transcripts)) == 1
    text = transcripts[0].decode("ascii")
    assert "  Copilot (untrusted) > Tool started: view\n" in text
    assert '  Check document (untrusted) > {"status":"passed"}\n' in text
    assert "  Check document stderr (untrusted) > Observed stderr\n" in text
    assert text.count('{"status":"passed"}') == 1
    assert "raw exit 0" in text and "[!] document: incomplete" in text
    assert "still running; 5s" not in text
    assert "still running; 16s" in text
    assert "stdout excerpt:" not in text and "apmx: UNPROVEN" not in text
    assert "still running; 5s elapsed" in outputs[False, "pipe"]
    assert "still running; 5s elapsed" not in outputs[True, "pipe"]


def test_hidden_stdout_and_retained_telemetry_do_not_starve_human_heartbeat(capsys):
    logger = ContractLogger()
    _event(logger, "check_started", name="document")
    for at in (4, 8, 12):
        _event(logger, "activity", at=at, source="checker", label="document", text="hidden stdout")
        _event(logger, "metadata", at=at + 0.5, text="retained", retained_only=True)
        _event(logger, "heartbeat", at=at + 1, elapsed_seconds=at + 1)
    output = capsys.readouterr().out
    assert "still running; 5s elapsed" in output
    assert "still running; 13s elapsed" in output
    assert "hidden stdout" not in output and "retained" not in output


def test_animation_and_verbose_visibility_cannot_change_retained_liveness(tmp_path, monkeypatch):
    transcripts = []
    for animate in (False, True):
        for verbose in (False, True):
            directory = tmp_path / f"{animate}-{verbose}"
            directory.mkdir()
            with _terminal(monkeypatch) as terminal:
                monkeypatch.setenv("APM_PROGRESS", "always" if animate else "never")
                status = Mock()
                monkeypatch.setattr(terminal.rich, "status", Mock(return_value=status))
                logger = ContractLogger(verbose=verbose)
                logger.attach_run("run", directory)
                _event(logger, "phase", name="execution")
                _event(logger, "heartbeat", at=5, elapsed_seconds=5)
                _event(logger, "metadata", at=9, text="Visible only in verbose")
                _event(logger, "heartbeat", at=10, elapsed_seconds=10)
                logger.close()
                assert status.start.call_count == int(animate)
                assert status.stop.call_count == int(animate)
                transcripts.append((directory / "transcript.log").read_bytes())
    assert len(set(transcripts)) == 1
    assert transcripts[0].count(b"still running") == 2


@pytest.mark.parametrize("verbose", [False, True])
def test_routine_tools_are_verbose_but_failures_and_stderr_are_immediate(capsys, verbose):
    logger = ContractLogger(verbose=verbose)
    for status, text in (
        ("started", "Tool started: view"),
        ("completed", "Tool completed"),
        ("failed", "Tool failed"),
    ):
        _event(logger, "activity", source="harness", text=text, tool_status=status)
    _event(
        logger,
        "activity",
        source="harness",
        stream="stderr",
        text="Native stderr observation",
        tool_status="started",
    )
    output = capsys.readouterr().out
    assert ("Tool started: view" in output) is verbose
    assert ("Tool completed" in output) is verbose
    assert "Tool failed" in output
    assert "Copilot stderr > Native stderr observation" in output


def test_factory_and_leaves_share_only_screen_state_with_immutable_step_context(
    tmp_path,
    capsys,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    factory = ContractLogger()
    factory.chain_node(1, 2, Path("one.contract.md"))
    first = factory.new_leaf(index=1, count=2, contract=Path("one.contract.md"))
    _event(first, "selected", contract="one.contract.md", produces="one.txt")
    first.close()
    _event(first, "finished", result=_run(tmp_path / "first"))
    factory.chain_node(2, 2, Path("two.contract.md"))
    second = factory.new_leaf(index=2, count=2, contract=Path("two.contract.md"))
    _event(second, "selected", contract="two.contract.md", produces="two.txt")
    second.close()
    _event(second, "finished", result=_run(tmp_path / "second"))
    factory.close()
    result = ChainResult(
        "chain",
        tmp_path / "record.json",
        Outcome.UNPROVEN,
        True,
        (_run(tmp_path / "first"), _run(tmp_path / "second")),
    )
    factory.render_chain_result(result)
    output = capsys.readouterr().out
    assert "\n\nStep 2/2:" in output
    assert "\n\n[!] apmx factory: UNPROVEN (complete)\n" in output
    assert "\n\n\n" not in output
    assert output.endswith("  Factory record: record.json\n\n")
    assert "Job:" not in output
    assert "  Output: one.txt\n" in output and "  Output: two.txt\n" in output
    assert "2 contracts completed; 2 checks passed." in output
    assert factory._display is first._display is second._display
    assert first._transcript is not second._transcript
    assert first._step.index == 1 and second._step.index == 2
    with pytest.raises(FrozenInstanceError):
        first._step.index = 9
    first._display.disable()
    assert not factory._display.enabled and not second._display.enabled


@pytest.mark.parametrize(
    ("outcome", "complete", "code", "symbol", "sgr"),
    [
        (Outcome.UNPROVEN, True, None, "[!]", "33"),
        (Outcome.UNPROVEN, False, "unproven_input", "[!]", "33"),
        (Outcome.REJECTED, False, "upstream_rejected", "[x]", "31"),
        (Outcome.HALTED, False, "cancelled", "[x]", "31"),
    ],
)
def test_aggregate_style_follows_owner_and_stopped_runs_are_not_counted_as_completed(
    tmp_path,
    monkeypatch,
    outcome,
    complete,
    code,
    symbol,
    sgr,
):
    with _terminal(monkeypatch) as terminal:
        logger = ContractLogger(verbose=True)
        logger.attach_run("chain", tmp_path)
        if not complete:
            logger.chain_stopped("Handoff was not admitted.", outcome=outcome, code=code)
        logger.close()
        frozen = (tmp_path / "transcript.log").read_bytes()
        logger.render_chain_result(
            ChainResult(
                "chain",
                tmp_path / "record.json",
                outcome,
                complete,
                (_run(tmp_path),),
                code,
            )
        )
        assert (tmp_path / "transcript.log").read_bytes() == frozen
        raw = terminal.text
    output = click.unstyle(raw)
    assert f"\x1b[1;{sgr}m{symbol} apmx factory: {outcome.name}\x1b[0m" in raw
    assert "VERIFIED" not in output
    if complete:
        assert "1 contract completed; 1 check passed." in output
    else:
        assert f"{symbol} Handoff was not admitted." in output
        assert f"Stop reason: {code}" in output
        assert "1 contract completed" not in output
        # Retention keeps the original warning marker, independently of display role.
        assert frozen == b"[!] Handoff was not admitted.\n"


@pytest.mark.parametrize("width", [40, 80, 120])
@pytest.mark.parametrize("encoding", ["ascii", "cp1252"])
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("newline", [None, "\n", "\r\n"])
def test_color_free_tty_keeps_prose_layout_and_literal_paths(
    monkeypatch,
    width,
    encoding,
    fallback,
    newline,
):
    prose = (
        "I am reading the authoritative request and specification before making the bounded edits."
    )
    path = "Output: .apm/runs/" + "long-component/" * 12 + "artifact.json"
    rendered = {}
    for mode in ("styled", "no_color", "pipe", "ci", "dumb"):
        with _terminal(
            monkeypatch,
            width=width,
            mode=mode,
            encoding=encoding,
            fallback=fallback,
            newline=newline,
        ) as terminal:
            logger = ContractLogger()
            _event(logger, "activity", source="harness", text=prose)
            logger._write(path)
            raw = terminal.text
        # CRLF is a line ending, not cursor motion. Click may rewrap ASCII
        # streams with native newlines; lone CR must still never reach a TTY.
        text = raw.replace("\r\n", "\n")
        assert "\r" not in text
        rendered[mode] = click.unstyle(text)
        assert rendered[mode].endswith("  " + path + "\n")
        if mode != "styled":
            assert "\x1b" not in raw
        assert all(ord(character) < 128 for character in raw)
    assert rendered["styled"] == rendered["no_color"]
    for mode in ("pipe", "ci", "dumb"):
        assert rendered[mode].startswith("  Copilot > " + prose + "\n")
    lines = rendered["no_color"].splitlines()[:-1]
    assert all(len(line) <= width for line in lines)
    assert all(line.startswith(" " * len("  Copilot > ")) for line in lines[1:])


def test_terminal_width_is_refreshed_and_literal_controls_remain_sanitized(monkeypatch):
    with _terminal(monkeypatch, width=120, mode="no_color") as terminal:
        logger = ContractLogger()
        message = (
            "A long line that should wrap after resizing the current terminal to forty columns."
        )
        _event(logger, "activity", source="harness", text=message)
        before = terminal.text
        terminal.rich.width = 40
        _event(logger, "activity", source="harness", text=message)
        after = terminal.text[len(before) :]
        _event(logger, "activity", source="harness", text="literal [green] \r \x1b[31m \u202e")
        output = terminal.text
    assert len(before.splitlines()) == 1
    assert len(after.splitlines()) > 1
    assert "\x1b" not in output
    assert "\r" not in output.replace("\r\n", "\n")
    assert all(token in output for token in ("[green]", r"\r", r"\x1b[31m", r"\u202e"))


def test_wrapping_does_not_drop_the_end_of_a_full_line_accent(monkeypatch):
    with _terminal(monkeypatch, width=40) as terminal:
        printed = Mock(wraps=terminal.rich.print)
        monkeypatch.setattr(terminal.rich, "print", printed)
        logger = ContractLogger()
        logger._display.emit(
            _DisplayLine(
                "Diagnostic metadata that is long enough to require several wrapped rows.",
                role=_Role.DETAIL,
                layout=_Layout.PROSE,
            )
        )
        rendered = printed.call_args.args[0]
        assert "\n" in rendered.plain
        assert rendered.get_style_at_offset(terminal.rich, len(rendered) - 1).dim is True


def test_broken_pipe_disables_shared_screen_but_leaves_finish_private_transcripts(
    tmp_path,
    monkeypatch,
):
    root = ContractLogger()
    one_dir, two_dir = tmp_path / "one", tmp_path / "two"
    one_dir.mkdir()
    two_dir.mkdir()
    one = root.new_leaf(index=1, count=2, contract=Path("one.contract.md"))
    two = root.new_leaf(index=2, count=2, contract=Path("two.contract.md"))
    one.attach_run("one", one_dir)
    two.attach_run("two", two_dir)
    writes = []

    def broken(*args, **kwargs):
        writes.append(args)
        raise BrokenPipeError

    monkeypatch.setattr(console, "_rich_echo", broken)
    _event(one, "activity", source="harness", text="First retained line")
    _event(two, "activity", source="harness", text="Second retained line")
    one.close()
    two.close()
    assert len(writes) == 1
    assert "First retained line" in (one_dir / "transcript.log").read_text()
    assert "Second retained line" in (two_dir / "transcript.log").read_text()
    assert not root._display.enabled
