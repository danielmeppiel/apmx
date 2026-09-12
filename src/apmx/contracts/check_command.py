"""Canonical sh -c check dispatch, including Git for Windows' native sh.exe."""

import os
import shutil
from pathlib import Path

from .models import ContractError


def find_check_shell() -> str | None:
    shell = shutil.which("sh.exe" if os.name == "nt" else "sh")
    if shell:
        return shell
    if os.name == "nt":
        git = shutil.which("git.exe")
        if git:
            executable = Path(git).resolve()
            for candidate in (
                executable.parent / "sh.exe",
                executable.parent.parent / "bin/sh.exe",
                executable.parent.parent / "usr/bin/sh.exe",
            ):
                if candidate.is_file():
                    return str(candidate)
    return None


def check_argv(command: str) -> tuple[str, ...]:
    shell = find_check_shell()
    if shell is None:
        raise ContractError(
            "Required check shell 'sh' was not found. On Windows install Git for Windows "
            "with sh.exe available on PATH or alongside its Git installation.",
            code="check_shell_missing",
        )
    return shell, "-c", command
