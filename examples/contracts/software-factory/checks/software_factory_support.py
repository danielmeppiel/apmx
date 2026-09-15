"""Portable candidate materialization shared by acceptance and regression.

This is an example checker, not a scheduler, runtime receipt owner or sandbox.
It consumes captured files, never imports APMX, and emits observations on stdout.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

MAX_FILE = 128 * 1024
BASE_FILES = ("src/__init__.py", "src/pricing.py", "src/checkout.py", "tests/test_checkout.py")
CHANGED_FILES = ("src/pricing.py", "src/checkout.py", "tests/test_free_shipping.py")


class Invalid(Exception):
    """Missing, unsupported or incomplete check input or execution."""


def read_file(root: Path, name: str, limit: int = MAX_FILE) -> bytes:
    """Read a bounded regular file without following symlink components."""
    path = Path(name)
    if path.is_absolute() or not path.parts or any(p in ("..", ".") for p in path.parts):
        raise Invalid("Expected a safe relative file path.")
    target = root
    for part in path.parts:
        target = target / part
        if target.is_symlink():
            raise Invalid(f"Symlink input is not supported: {name}")
    metadata = target.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
        raise Invalid(f"Expected a regular file of at most {limit} bytes: {name}")
    raw = target.read_bytes()
    if len(raw) > limit:
        raise Invalid(f"File exceeded its size limit: {name}")
    return raw


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def inventory(root: Path, names: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    result = {}
    for name in sorted(names):
        raw = read_file(root, name)
        result[name] = {"sha256": digest(raw), "size": len(raw)}
    return result


def tree_hash(files: dict[str, dict[str, Any]]) -> str:
    return digest(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("ascii"))


def check_inventory(root: Path) -> dict[str, dict[str, Any]]:
    if any(path.is_symlink() for path in (root / "checks").rglob("*")):
        raise Invalid("Supplied check resources must not contain symlinks.")
    names = tuple(
        path.relative_to(root).as_posix()
        for path in sorted((root / "checks").rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    )
    if not names or len(names) > 32:
        raise Invalid("The supplied check resource inventory is empty or too large.")
    return inventory(root, names)


def clean_environment(home: Path) -> dict[str, str]:
    """Do not let ambient Git, Python or Behave selection change the check."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("GIT_", "BEHAVE_", "PYTHON"))
    }
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        XDG_CONFIG_HOME=str(home),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CEILING_DIRECTORIES=str(home),
        GIT_TERMINAL_PROMPT="0",
    )
    return env


def patch_paths(raw: bytes) -> tuple[str, ...]:
    """Accept only regular text edits to the three requested application files."""
    try:
        text = raw.decode("ascii")
    except UnicodeError as exc:
        raise Invalid("This example expects an ASCII text patch.") from exc
    sections = re.split(r"(?m)^diff --git ", text)
    if sections[0] or len(sections) != len(CHANGED_FILES) + 1:
        raise Invalid("Patch must change both source files and add the requested test file.")
    paths = []
    for section in sections[1:]:
        lines = section.splitlines()
        header = re.fullmatch(r"a/([a-zA-Z0-9_./-]+) b/\1", lines[0])
        if not header or header[1] not in CHANGED_FILES or header[1] in paths:
            raise Invalid("Patch contains an unsafe, duplicate or protected path.")
        name = header[1]
        paths.append(name)
        addition = name == "tests/test_free_shipping.py"
        before = "--- /dev/null" if addition else f"--- a/{name}"
        after = f"+++ b/{name}"
        position = 1
        if addition:
            if lines[position] != "new file mode 100644":
                raise Invalid("The generated regression test must be a new regular file.")
            position += 1
        if re.fullmatch(r"index [0-9a-f]+\.\.[0-9a-f]+(?: 100644)?", lines[position]):
            position += 1
        if lines[position : position + 2] != [before, after]:
            raise Invalid("Patch headers do not describe the declared regular-file edits.")
        position += 2
        if position == len(lines):
            raise Invalid("Patch has no text change hunks.")
        while position < len(lines):
            hunk = re.fullmatch(r"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@.*", lines[position])
            if not hunk:
                raise Invalid("Patch contains unsupported text outside a change hunk.")
            old, new = (int(count) if count is not None else 1 for count in hunk.groups())
            position += 1
            while old or new:
                if position >= len(lines) or not lines[position]:
                    raise Invalid("Incomplete patch hunk.")
                prefix = lines[position][0]
                if prefix not in (" ", "+", "-"):
                    raise Invalid("Unsupported patch hunk content.")
                old -= int(prefix in (" ", "-"))
                new -= int(prefix in (" ", "+"))
                if min(old, new) < 0:
                    raise Invalid("Patch hunk lengths do not match its content.")
                position += 1
                if position < len(lines) and lines[position] == r"\ No newline at end of file":
                    position += 1
    return tuple(paths)


def materialize(root: Path, patch: str, area: Path) -> tuple[Path, dict[str, Any]]:
    """Apply these exact patch bytes to matching supplied baseline files."""
    raw = read_file(root, patch)
    patch_paths(raw)
    base = inventory(root, BASE_FILES)
    checks = check_inventory(root)
    candidate = area / "candidate"
    candidate.mkdir()
    for name in BASE_FILES:
        path = candidate / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(read_file(root, name))
    exported = area / "captured.diff"
    exported.write_bytes(raw)
    git = shutil.which("git")
    if git is None:
        raise Invalid("Git is missing. Install Git and rerun this check.")
    command = [
        git,
        "-c",
        "core.longpaths=true",
        "-c",
        "core.autocrlf=false",
        "-c",
        "core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol",
        "apply",
        "--whitespace=error-all",
    ]
    for options in (["--check"], []):
        result = subprocess.run(
            [*command, *options, str(exported)],
            cwd=candidate,
            env=clean_environment(area),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
        if result.returncode:
            raise Invalid("Patch does not apply cleanly to the supplied baseline.")
    names = tuple(sorted(set(BASE_FILES) | set(CHANGED_FILES)))
    actual = {
        path.relative_to(candidate).as_posix()
        for path in candidate.rglob("*")
        if not path.is_dir() or path.is_symlink()
    }
    if actual != set(names):
        raise Invalid("Patch changed files outside the candidate application.")
    files = inventory(candidate, names)
    for name in names:
        read_file(candidate, name).decode("ascii")
    return candidate, {
        "base": tree_hash(base),
        "patch": digest(raw),
        "candidate": tree_hash(files),
        "checks": tree_hash(checks),
        "base_files": base,
        "candidate_files": files,
    }


def run_driver(
    root: Path, candidate: Path, area: Path, script: str, *arguments: str
) -> tuple[int, Any]:
    """Run trusted code with bounded time and a bounded structured report."""
    report = area / f"result-{uuid.uuid4().hex}.json"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(root / "checks" / script),
            str(candidate),
            str(report),
            *arguments,
        ],
        cwd=area,
        env=clean_environment(area),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=45,
        check=False,
    )
    raw = read_file(area, report.name)
    try:
        return result.returncode, json.loads(raw)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise Invalid("The check report is not valid bounded JSON.") from exc


def rows_status(rows: list[dict[str, Any]], required: tuple[str, ...]) -> int:
    """Require every expected row once; incomplete execution never passes."""
    if not isinstance(rows, list) or len(rows) != len(required):
        raise Invalid("Missing or extra required scenario rows.")
    if any(not isinstance(row, dict) for row in rows):
        raise Invalid("Invalid scenario row report.")
    if sorted(row.get("id", "") for row in rows) != sorted(required):
        raise Invalid("Required scenario identities do not match the observed report.")
    if any(row.get("status") not in ("passed", "failed") for row in rows):
        raise Invalid("A required scenario was skipped, undefined, pending or unexecuted.")
    return int(any(row["status"] == "failed" for row in rows))


def execute(
    name: str,
    required: tuple[str, ...],
    inspect: Callable[[Path, Path, Path], tuple[int, list[dict[str, Any]]]],
    argv: list[str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=f"Apply a captured patch and run {name}.")
    parser.add_argument("patch", help="Relative patch artifact, normally changes.diff")
    args = parser.parse_args(argv)
    root = Path.cwd().resolve()
    area = root / f".software-factory-check-{uuid.uuid4().hex}"
    evidence: dict[str, Any] = {
        "schema": "software-factory-check/1",
        "check": name,
        "required": list(required),
        "observed": [],
        "subject": None,
        "status": "error",
    }
    code = 2
    try:
        area.mkdir(mode=0o700)
        candidate, subject = materialize(root, args.patch, area)
        evidence["subject"] = subject
        code, rows = inspect(root, candidate, area)
        evidence["observed"] = rows
        if code not in (0, 1, 2):
            raise Invalid("Unexpected check exit status.")
        if code == 2:
            raise Invalid("The check could not complete every required test.")
        if (
            tree_hash(inventory(candidate, tuple(subject["candidate_files"])))
            != subject["candidate"]
        ):
            raise Invalid("Candidate files changed while checks were running.")
        if inventory(root, BASE_FILES) != subject["base_files"]:
            raise Invalid("Supplied baseline changed while checks were running.")
        if digest(read_file(root, args.patch)) != subject["patch"]:
            raise Invalid("Patch artifact changed while checks were running.")
        if tree_hash(check_inventory(root)) != subject["checks"]:
            raise Invalid("Supplied check resources changed while checks were running.")
        evidence["status"] = "passed" if code == 0 else "failed"
    except (
        Invalid,
        OSError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        subprocess.SubprocessError,
    ) as exc:
        code = 2
        evidence["status"] = "error"
        evidence["detail"] = str(exc).encode("ascii", "backslashreplace").decode("ascii")[:600]
    finally:
        if area.exists():
            try:
                shutil.rmtree(area)
            except OSError:
                code = 2
                evidence.update(
                    status="error", detail="Could not remove the private check workspace."
                )
    print(json.dumps(evidence, sort_keys=True, ensure_ascii=True))
    return code
