"""Portable check dispatch; Windows uses a direct native executable, never cmd."""

import os
import shlex
import shutil
from pathlib import Path

from .models import ContractError


def check_argv(command: str) -> tuple[str, ...]:
    if os.name != "nt":
        shell = shutil.which("sh")
        if not shell:
            raise ContractError("Required check shell 'sh' was not found on PATH.", code="check_shell_missing")
        return shell, "-c", command
    # POSIX quoting is an explicit portable contract convention. Forward slashes
    # in quoted Windows paths work with native executables and avoid cmd parsing.
    try:
        words = shlex.split(command, posix=True)
    except ValueError as exc:
        raise ContractError("Malformed Windows check command quoting.", code="invalid_check_command") from exc
    if not words:
        raise ContractError("Empty check command.", code="invalid_check_command")
    if any(word in {"|", "||", "&", "&&", ";", ">", ">>", "<"} for word in words):
        raise ContractError("Windows checks require one direct executable, not shell syntax.", code="unsupported_check_shell")
    executable = shutil.which(words[0])
    if executable is None or Path(executable).suffix.lower() != ".exe":
        raise ContractError("Windows checks require a native .exe on PATH or an explicit .exe path.", code="check_executable_missing")
    return executable, *words[1:]
