"""Prepare an owned POSIX demo kit without wrapping or simulating APMX runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tomllib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = "apmx-demo-kit/1"
HARNESSES = ("copilot", "opencode")
ROOT = Path(__file__).resolve().parents[1]
SKIP = {"__pycache__", ".DS_Store"}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def directory(path: Path) -> None:
    """Reject redirection at every kit-owned directory boundary."""
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"Expected an ordinary owned directory: {path}")


def inventory(root: Path) -> dict[str, str]:
    directory(root)
    result = {}
    for path in sorted(root.rglob("*")):
        if any(part in SKIP for part in path.relative_to(root).parts):
            continue
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError(f"Demo inputs must be regular files: {path}")
        result[path.relative_to(root).as_posix()] = digest(path)
    return result


def copy_inputs(source: Path, destination: Path) -> None:
    """Copy only the inventoried ordinary inputs, never installed dependencies."""
    destination.mkdir()
    populate_inputs(source, destination)


def populate_inputs(source: Path, destination: Path) -> None:
    """Populate a fresh directory whose ownership has already been established."""
    directory(destination)
    if any(destination.iterdir()):
        raise ValueError("Input copy requires an empty destination.")
    files = inventory(source)
    for relative in files:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
    if inventory(destination) != files:
        raise ValueError("Demo inputs changed while being copied; do not use this kit.")


def save(root: Path, config: dict) -> None:
    temporary = root / ".demo" / f"kit-{uuid.uuid4().hex}.json"
    with temporary.open("x", encoding="ascii") as stream:
        json.dump(config, stream, indent=2, ensure_ascii=True)
        stream.write("\n")
    temporary.chmod(0o600)
    temporary.replace(root / ".demo/kit.json")


def load(root: Path) -> dict:
    directory(root)
    directory(root / ".demo")
    path = root / ".demo/kit.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("Not an owned demo kit: missing ordinary .demo/kit.json.")
    config = json.loads(path.read_text(encoding="ascii"))
    if not isinstance(config, dict):
        raise TypeError("Invalid demo kit metadata; preserve this kit for inspection.")
    if config.get("schema") != SCHEMA or config.get("root") != str(root.resolve()):
        raise ValueError("Demo kit location/identity changed; prepare a new kit instead.")
    if not isinstance(config.get("workspaces"), dict):
        raise TypeError("Invalid demo workspace registry; preserve this kit for inspection.")
    return config


@contextmanager
def locked(root: Path, *, exclusive: bool) -> Iterator[dict]:
    """Hold a shared lease for shells and an exclusive lease for mutations."""
    if os.name != "posix":
        raise ValueError("The demo kit currently supports macOS/Linux terminals only.")
    import fcntl

    load(root)
    fd = os.open(root / ".demo/lock", os.O_RDWR | os.O_NOFOLLOW)
    try:
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        try:
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(
                "Close all demo shells with 'exit' before reset/clean; another demo action is active."
            ) from error
        yield load(root)
    finally:
        os.close(fd)


def bundle(root: Path) -> Path:
    native = root / ".demo/native"
    directory(native)
    entries = list(native.iterdir())
    if len(entries) != 1:
        raise ValueError("Expected one verified native bundle; prepare a new kit.")
    directory(entries[0])
    return entries[0]


def check_python(python: Path, requirement: str) -> None:
    """Check the declared factory prerequisite without installing anything."""
    name, separator, version = requirement.partition("==")
    if name != "behave" or not separator or not version:
        raise ValueError("Expected an exact Behave factory prerequisite in pyproject.toml.")
    try:
        subprocess.run(
            [
                str(python),
                "-I",
                "-c",
                (
                    "import behave,importlib.metadata,sys; "
                    "assert sys.version_info >= (3,12), 'Python 3.12+ required'; "
                    "assert behave.__version__ == importlib.metadata.version('behave') "
                    "== sys.argv[1], "
                    "'Wrong Behave version'"
                ),
                version,
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(
            f"Checker Python must provide Python 3.12+ and {requirement}. "
            "Install the factory extra in an isolated environment, then prepare again."
        ) from error


def check_harnesses() -> None:
    missing = [name for name in ("git", *HARNESSES) if shutil.which(name) is None]
    if missing:
        raise ValueError(
            f"Missing native tools: {', '.join(missing)}. Install them before preparation."
        )


def environment(root: Path) -> dict[str, str]:
    """Expose genuine tools in this child only; never change login/profile files."""
    env = os.environ.copy()
    for key in ("APMX_APM_BACKEND", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PROMPT_COMMAND"):
        env.pop(key, None)
    checker_bin = Path(load(root)["python"]).parent
    env["PATH"] = os.pathsep.join((str(root / ".demo/bin"), str(checker_bin), env.get("PATH", "")))
    return env


def validate(root: Path, config: dict) -> None:
    directory(root / "history")
    directory(root / ".demo/bin")
    directory(root / ".demo/logs")
    if inventory(root / "factory") != config["factory"]:
        raise ValueError("Factory changed; preserve this kit and prepare a new one.")
    if inventory(root / ".demo/seed") != config["seed"]:
        raise ValueError("Original application seed changed; prepare a new kit.")
    native = bundle(root)
    if (
        digest(native / "apmx") != config["binary_sha256"]
        or digest(native / "libexec/apm/apm") != config["backend_sha256"]
        or (root / ".demo/bin/apmx").resolve() != (native / "apmx").resolve()
    ):
        raise ValueError("Demo executable/tool identity changed; prepare a new kit.")
    python3 = shutil.which("python3", path=environment(root)["PATH"])
    if python3 is None or Path(python3).parent != Path(config["python"]).parent:
        raise ValueError(
            "Checker Python was shadowed; prepare with an ordinary checker environment."
        )
    check_python(Path(python3), config["checker"])


def workspace(root: Path, config: dict, name: str) -> Path:
    if name not in HARNESSES:
        raise ValueError("Choose copilot or opencode.")
    path = root / f"checkout-{name}"
    entry = config["workspaces"].get(name)
    if entry is None:
        if path.exists() or path.is_symlink():
            raise ValueError(f"Unowned workspace: {path}. Refusing to move or replace it.")
    else:
        directory(path)
        info = path.stat()
        if [info.st_dev, info.st_ino] != entry["identity"]:
            raise ValueError(f"Workspace ownership changed: {path}. Refusing to move it.")
    return path


def archive(root: Path, config: dict, names: list[str]) -> None:
    paths = [(name, workspace(root, config, name)) for name in names]
    existing = [(name, path) for name, path in paths if path.exists()]
    if not existing:
        return
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = root / "history" / f"{stamp}-{uuid.uuid4().hex[:8]}"
    destination.mkdir(mode=0o700)
    for name, path in existing:
        path.rename(destination / path.name)
        del config["workspaces"][name]
        save(root, config)
    print(f"Previous applications and evidence preserved: {destination}")


def install(root: Path, name: str) -> None:
    """Only official APM owns capability acquisition and materialization."""
    log = root / ".demo/logs" / f"prepare-{name}-{uuid.uuid4().hex}.log"
    env = environment(root)
    env["APM_NO_SCRIPTS"] = "1"
    with log.open("x") as stream:
        log.chmod(0o600)
        result = subprocess.run(
            [
                str(bundle(root) / "libexec/apm/apm"),
                "install",
                str(root / "factory"),
                "--root",
                str(root / f"checkout-{name}"),
                "--only",
                "apm",
                "--target",
                "agent-skills",
                "--no-trust-bin",
            ],
            cwd=root,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=300,
            check=False,
        )
    if result.returncode:
        raise ValueError(
            f"APM preparation failed for {name}. Inspect the private log: {log}. "
            f"Then retry './demo reset {name}'."
        )


def preview(root: Path, name: str) -> None:
    argv = [str(bundle(root) / "apmx"), "--from", "../factory", ".", "--on", name, "--plan"]
    if name == "opencode":
        argv += ["--model", "github-copilot/gpt-5.6-sol"]
    subprocess.run(
        argv,
        cwd=root / f"checkout-{name}",
        env=environment(root),
        check=True,
        timeout=60,
    )


def reset(root: Path, names: list[str]) -> None:
    with locked(root, exclusive=True) as config:
        validate(root, config)
        archive(root, config, names)
        for name in names:
            path = workspace(root, config, name)
            path.mkdir()
            info = path.stat()
            config["workspaces"][name] = {"identity": [info.st_dev, info.st_ino], "ready": False}
            save(root, config)
            populate_inputs(root / ".demo/seed", path)
            print(f"Preparing {name} with official APM (no model calls)...", flush=True)
            install(root, name)
            preview(root, name)
            config["workspaces"][name]["ready"] = True
            save(root, config)
        print("Ready. Use './demo copilot' or './demo opencode'.")


def clean(root: Path, names: list[str]) -> None:
    with locked(root, exclusive=True) as config:
        validate(root, config)
        archive(root, config, names)
    print("Active applications cleared; history retained. Use './demo reset' to relaunch.")


def shell(root: Path, name: str) -> int:
    with locked(root, exclusive=False) as config:
        validate(root, config)
        path = workspace(root, config, name)
        if not config["workspaces"].get(name, {}).get("ready"):
            raise ValueError(f"Demo is not prepared. Run './demo reset {name}' first.")
        if os.environ.get("APM_NO_SCRIPTS"):
            raise ValueError("Unset APM_NO_SCRIPTS before launching; execution must retain checks.")
        check_harnesses()
        env = environment(root)
        env["PS1"] = f"{name} $ "
        env["BASH_SILENCE_DEPRECATION_WARNING"] = "1"
        model = " --model github-copilot/gpt-5.6-sol" if name == "opencode" else ""
        print(f"\nApplication: {path}")
        print(f"Preview: apmx --from ../factory . --on {name}{model} --plan")
        print(f"Run:     apmx --from ../factory . --on {name}{model}")
        print("APMX requests interactive consent. No permissions or models are injected.")
        print("Use 'exit' before resetting. Keep runs in the foreground.\n", flush=True)
        return subprocess.run(
            ["/bin/bash", "--noprofile", "--norc", "-i"],
            cwd=path,
            env=env,
            check=False,
        ).returncode


def status(root: Path) -> None:
    with locked(root, exclusive=False) as config:
        validate(root, config)
        print(f"Native candidate: {config['source_commit']}")
        print(f"Kit: {root}")
        for name in HARNESSES:
            path = workspace(root, config, name)
            ready = config["workspaces"].get(name, {}).get("ready", False)
            print(f"{name}: {'prepared' if ready else 'needs reset'} - {path}")
        print(f"Previous runs: {root / 'history'}")
        print("Presence of native CLIs is not proof of login; authenticate through each CLI.")


def prepare(root: Path, archive_path: Path, expected: str, python: Path) -> None:
    """Create a fresh, outside-Git kit from a trusted archive and current examples."""
    if root.exists() or root.is_symlink():
        raise ValueError(
            "Preparation requires a fresh destination; existing files are never replaced."
        )
    if os.name != "posix":
        raise ValueError("The demo kit currently supports macOS/Linux terminals only.")
    if __package__:
        from . import release
    else:
        import release

    check_harnesses()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    (requirement,) = project["project"]["optional-dependencies"]["factory"]
    check_python(python, requirement)
    release.verify_archive(archive_path, expected)
    parent = root.parent
    directory(parent)
    probe = subprocess.run(
        ["git", "-C", str(parent), "rev-parse", "--show-toplevel"],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if probe.returncode == 0:
        raise ValueError("Choose a demo destination outside Git; never bypass a project's policy.")
    if probe.returncode != 128:
        raise ValueError("Cannot determine whether the destination is outside Git.")
    root.mkdir(mode=0o700)
    private = root / ".demo"
    private.mkdir(mode=0o700)
    native = release.extract_archive(archive_path, private / "native")
    release.check_native_notices(native, native.name.removeprefix("apmx-"))
    metadata = json.loads((native / "RELEASE.json").read_text())
    if metadata["target"] != release.native_target():
        raise ValueError(
            "Archive targets another platform; prepare with the matching native archive."
        )
    copy_inputs(ROOT / "examples/contracts/software-factory", root / "factory")
    copy_inputs(ROOT / "examples/contracts/checkout-project", private / "seed")
    (root / "history").mkdir(mode=0o700)
    (private / "logs").mkdir(mode=0o700)
    (private / "lock").touch(mode=0o600)
    (private / "bin").mkdir()
    (private / "bin/apmx").symlink_to(native / "apmx")
    shutil.copy2(Path(__file__), private / "demo.py")
    launcher = (
        "#!/bin/sh\nexec "
        + shlex.quote(str(python))
        + " -I "
        + shlex.quote(str(private / "demo.py"))
        + " --root "
        + shlex.quote(str(root))
        + ' "$@"\n'
    )
    (root / "demo").write_text(launcher, encoding="utf-8")
    (root / "demo").chmod(0o755)
    (private / "bin/demo").symlink_to(root / "demo")
    shutil.copy2(ROOT / "docs/demo.md", root / "START-HERE.md")
    config = {
        "schema": SCHEMA,
        "root": str(root.resolve()),
        "python": str(python),
        "checker": requirement,
        "source_commit": metadata["source_commit"],
        "archive_sha256": expected,
        "binary_sha256": digest(native / "apmx"),
        "backend_sha256": digest(native / "libexec/apm/apm"),
        "factory": inventory(root / "factory"),
        "seed": inventory(private / "seed"),
        "workspaces": {},
    }
    save(root, config)
    reset(root, list(HARNESSES))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("prepare", help="Prepare a fresh kit; does not invoke models")
    create.add_argument("destination", type=Path)
    create.add_argument("--archive", type=Path, required=True)
    create.add_argument("--sha256", required=True, help="Trusted expected archive SHA-256")
    create.add_argument("--python", type=Path, required=True, help="Checker environment Python")
    for name in HARNESSES:
        commands.add_parser(name, help=f"Enter the {name} application shell")
    for name in ("reset", "clean"):
        command = commands.add_parser(
            name, help="Archive active work; reset also prepares fresh apps"
        )
        command.add_argument("harness", nargs="?", choices=HARNESSES)
    commands.add_parser("status", help="Check local kit identities and readiness")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare(
                args.destination.absolute(),
                args.archive.absolute(),
                args.sha256,
                args.python.expanduser().absolute(),
            )
        elif args.root is None:
            raise ValueError("Use the prepared kit's './demo' launcher.")
        elif args.command in HARNESSES:
            return shell(args.root, args.command)
        elif args.command == "status":
            status(args.root)
        else:
            names = [args.harness] if args.harness else list(HARNESSES)
            (reset if args.command == "reset" else clean)(args.root, names)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        print(f"Demo stopped: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
