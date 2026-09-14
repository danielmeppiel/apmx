"""Rendering recovery must not hide I/O, programming, or operation failures."""

from unittest.mock import Mock

import pytest
from rich.errors import ConsoleError, LiveError, MarkupError, NotRenderableError, StyleError

from apmx.utils import console

pytestmark = pytest.mark.component


@pytest.fixture(autouse=True)
def reset_console(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    console._reset_console()
    yield
    console._reset_console()


@pytest.mark.parametrize("error", [OSError("private fixture path"), ValueError("private width")])
def test_console_initialization_failure_is_reported_without_exception_content(
    monkeypatch, caplog, error
):
    monkeypatch.setattr(console, "Console", Mock(side_effect=error))
    assert console._get_console() is None
    assert "Rich console initialization failed" in caplog.text
    assert type(error).__name__ in caplog.text
    assert "Using basic terminal output" in caplog.text
    assert "private" not in caplog.text


@pytest.mark.parametrize("error", [TypeError("defect"), RuntimeError("defect")])
def test_console_initialization_does_not_swallow_unexpected_defects(monkeypatch, error):
    monkeypatch.setattr(console, "Console", Mock(side_effect=error))
    with pytest.raises(type(error)) as raised:
        console._get_console()
    assert raised.value is error


@pytest.mark.parametrize(
    "error",
    [
        ConsoleError("private fixture content"),
        StyleError("private fixture content"),
        UnicodeEncodeError("ascii", "\u00e9", 0, 1, "private fixture content"),
    ],
)
def test_expected_rich_failure_preserves_literal_fallback_text(monkeypatch, capsys, caplog, error):
    rich = Mock()
    rich.print.side_effect = error
    monkeypatch.setattr(console, "_get_console", lambda: rich)
    console._rich_echo("  Copilot > literal [green] content", accent_length=12)
    assert capsys.readouterr().out == "  Copilot > literal [green] content\n"
    assert "Rich text rendering failed" in caplog.text
    assert "Using basic text output" in caplog.text
    assert "private" not in caplog.text


@pytest.mark.parametrize(
    "error", [BrokenPipeError("pipe closed"), OSError("unwritable"), TypeError("defect")]
)
def test_pipe_io_and_programming_errors_never_replay_payload(monkeypatch, caplog, error):
    rich = Mock()
    rich.print.side_effect = error
    monkeypatch.setattr(console, "_get_console", lambda: rich)
    echo = Mock()
    monkeypatch.setattr(console.click, "echo", echo)
    with pytest.raises(type(error)) as raised:
        console._rich_echo("must not be replayed")
    assert raised.value is error
    echo.assert_not_called()
    if isinstance(error, OSError) and not isinstance(error, BrokenPipeError):
        assert "Output cannot be written" in caplog.text
    else:
        assert not caplog.records


def test_rich_fallback_keeps_contract_accent_and_dim_suffix(monkeypatch, caplog):
    monkeypatch.delenv("NO_COLOR")
    rich = Mock()
    rich.print.side_effect = StyleError("invalid style")
    monkeypatch.setattr(console, "_get_console", lambda: rich)
    echo = Mock()
    monkeypatch.setattr(console.click, "echo", echo)
    console._rich_echo(
        "[!] UNPROVEN  1s",
        color="yellow",
        accent_length=12,
        body_style="dim",
        capabilities=console.TerminalCapabilities(console.TerminalMode.STYLED_TTY, 80),
    )
    assert echo.call_args.args[0] == "\x1b[33m[!] UNPROVEN\x1b[0m\x1b[2m  1s\x1b[0m"
    assert "Rich text rendering failed" in caplog.text


def test_panel_rendering_failure_reports_and_keeps_plain_content(monkeypatch, capsys, caplog):
    rich = Mock()
    rich.print.side_effect = MarkupError("private markup")
    monkeypatch.setattr(console, "_get_console", lambda: rich)
    console._rich_panel("literal content", title="Title")
    assert "literal content" in capsys.readouterr().out
    assert "Rich panel rendering failed" in caplog.text
    assert "private markup" not in caplog.text


@pytest.mark.parametrize("error", [BrokenPipeError(), OSError(), RuntimeError()])
def test_panel_does_not_mask_pipe_io_or_programming_errors(monkeypatch, error):
    rich = Mock()
    rich.print.side_effect = error
    monkeypatch.setattr(console, "_get_console", lambda: rich)
    echo = Mock()
    monkeypatch.setattr(console.click, "echo", echo)
    with pytest.raises(type(error)) as raised:
        console._rich_panel("content")
    assert raised.value is error
    echo.assert_not_called()


def test_invalid_table_renderables_report_unavailability(monkeypatch, caplog):
    monkeypatch.setattr(console, "Table", Mock(side_effect=NotRenderableError("private value")))
    assert console._create_files_table([]) is None
    assert "Table rendering is unavailable" in caplog.text
    assert "private value" not in caplog.text


def test_table_programming_errors_are_not_silently_dropped(monkeypatch):
    error = TypeError("unexpected construction failure")
    monkeypatch.setattr(console, "Table", Mock(side_effect=error))
    with pytest.raises(TypeError) as raised:
        console._create_files_table([])
    assert raised.value is error


@pytest.mark.parametrize("stage", ["construction", "start"])
def test_spinner_startup_fallback_enters_body_once_and_cleans_partial_start(
    monkeypatch, capsys, caplog, stage
):
    rich, status = Mock(), Mock()
    rich.status.return_value = status
    error = LiveError("private startup details")
    if stage == "construction":
        rich.status.side_effect = error
    else:
        status.start.side_effect = error
    monkeypatch.setattr(console, "_get_console", lambda: rich)
    entries = []
    with console.show_download_spinner("fixture/repo") as active:
        entries.append(active)
    assert entries == [None]
    assert status.stop.call_count == int(stage == "start")
    assert capsys.readouterr().out == "Downloading fixture/repo...\n"
    assert "Spinner startup failed" in caplog.text
    assert "private startup details" not in caplog.text


@pytest.mark.parametrize(
    "error", [ValueError("body"), OSError("body"), RuntimeError("body"), ConsoleError("body")]
)
def test_spinner_never_catches_or_retries_the_wrapped_operation(monkeypatch, caplog, error):
    rich, status = Mock(), Mock()
    rich.status.return_value = status
    monkeypatch.setattr(console, "_get_console", lambda: rich)
    echo = Mock()
    monkeypatch.setattr(console.click, "echo", echo)
    with pytest.raises(type(error)) as raised, console.show_download_spinner("fixture/repo"):
        raise error
    assert raised.value is error
    status.start.assert_called_once()
    status.stop.assert_called_once()
    echo.assert_not_called()
    assert not caplog.records


def test_spinner_cleanup_reports_expected_failure_without_masking_body_exception(
    monkeypatch, caplog
):
    rich, status = Mock(), Mock()
    rich.status.return_value = status
    status.stop.side_effect = LiveError("private cleanup detail")
    monkeypatch.setattr(console, "_get_console", lambda: rich)
    original = ValueError("operation failed")
    with pytest.raises(ValueError) as raised, console.show_download_spinner("fixture/repo"):
        raise original
    assert raised.value is original
    status.stop.assert_called_once()
    assert "Spinner cleanup failed" in caplog.text
    assert "private cleanup detail" not in caplog.text


def test_spinner_startup_pipe_error_is_not_retried_as_plain_output(monkeypatch, caplog):
    rich, status = Mock(), Mock()
    rich.status.return_value = status
    status.start.side_effect = BrokenPipeError()
    monkeypatch.setattr(console, "_get_console", lambda: rich)
    echo = Mock()
    monkeypatch.setattr(console.click, "echo", echo)
    with pytest.raises(BrokenPipeError), console.show_download_spinner("fixture/repo"):
        pytest.fail("Failed spinner entry cannot start the operation")
    echo.assert_not_called()
    assert not caplog.records
