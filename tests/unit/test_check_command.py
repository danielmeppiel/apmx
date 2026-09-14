from types import SimpleNamespace

import pytest

from apmx.contracts import check_command
from apmx.contracts.models import ContractError


def windows(monkeypatch):
    monkeypatch.setattr(check_command, "os", SimpleNamespace(name="nt"))


def test_windows_check_preserves_quoted_path_and_compound_shell_command(monkeypatch):
    windows(monkeypatch)
    shell = "C:/Program Files/Git/bin/sh.exe"
    command = '"C:/Program Files/Python/python.exe" -I checks/check.py && test -f handoff.json'
    monkeypatch.setattr(check_command.shutil, "which", lambda value: shell)
    assert check_command.check_argv(command) == (shell, "-c", command)


def test_windows_missing_shell_fails_clearly(monkeypatch):
    windows(monkeypatch)
    monkeypatch.setattr(check_command.shutil, "which", lambda value: None)
    with pytest.raises(ContractError, match="Git for Windows"):
        check_command.check_argv("true")


def test_windows_discovers_git_shell_when_only_git_cmd_is_on_path(tmp_path, monkeypatch):
    windows(monkeypatch)
    git = tmp_path / "Program Files/Git/cmd/git.exe"
    shell = tmp_path / "Program Files/Git/bin/sh.exe"
    git.parent.mkdir(parents=True)
    shell.parent.mkdir(parents=True)
    git.touch()
    shell.touch()
    monkeypatch.setattr(
        check_command.shutil, "which", lambda value: str(git) if value == "git.exe" else None
    )
    assert check_command.check_argv("true") == (str(shell), "-c", "true")
