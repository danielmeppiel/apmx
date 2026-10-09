"""Prepare an owned POSIX demo kit without wrapping or simulating APMX runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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
EVIDENCE_TOOLS = {
    "verify_evidence.py": "scripts/verify_evidence.py",
    "receipt_verifier.py": "src/apmx/audit/verifier.py",
    "check_evidence_controls.py": "scripts/check_evidence_controls.py",
    "evidence-requirements.txt": "scripts/evidence-requirements.txt",
}
CHAIN_ID = re.compile(r"\d{8}T\d{6}Z-[a-f0-9]{12}")


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
    application = root / f"checkout-{name}"
    manifest = application / "apm.yml"
    if not manifest.exists():
        with manifest.open("x", encoding="ascii") as stream:
            json.dump(
                {
                    "name": "checkout-demo",
                    "version": "1.0.0",
                    "dependencies": {"apm": ["../factory"]},
                },
                stream,
                indent=2,
            )
            stream.write("\n")
    log = root / ".demo/logs" / f"prepare-{name}-{uuid.uuid4().hex}.log"
    env = environment(root)
    env["APM_NO_SCRIPTS"] = "1"
    with log.open("x") as stream:
        log.chmod(0o600)
        result = subprocess.run(
            [
                str(bundle(root) / "libexec/apm/apm"),
                "install",
                "../factory",
                "--root",
                ".",
                "--only",
                "apm",
                "--target",
                "agent-skills",
                "--no-trust-bin",
            ],
            cwd=application,
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


def factory_command(name: str) -> list[str]:
    argv = ["apmx", "--from", "../factory"]
    if name == "opencode":
        argv += ["--on", name, "--model", "github-copilot/gpt-5.6-sol"]
    return argv


def preview(root: Path, name: str) -> None:
    subprocess.run(
        [str(bundle(root) / "apmx"), *factory_command(name)[1:], "--plan"],
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
        command = shlex.join(factory_command(name))
        print(f"\nApplication: {path}")
        print(f"Preview: {command} --plan")
        print(f"Run:     {command}")
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


def evidence_package(root: Path, config: dict, harness: str | None, run: str | None) -> Path:
    """Select a recorded run explicitly; never fall back past an unfinished run."""
    if harness is None:
        matches = [
            name for name in HARNESSES if Path.cwd().is_relative_to(workspace(root, config, name))
        ]
        if len(matches) != 1:
            raise ValueError("Specify copilot or opencode, or run this inside its demo shell.")
        harness = matches[0]
    application = workspace(root, config, harness)
    directory(application / ".apm")
    chains = application / ".apm/chains"
    directory(chains)
    candidates = [path for path in chains.iterdir() if path.name not in SKIP]
    if any(not CHAIN_ID.fullmatch(path.name) for path in candidates):
        raise ValueError("Unrecognized factory run entry; inspect the workspace before presenting.")
    if run is not None:
        if not CHAIN_ID.fullmatch(run):
            raise ValueError("Expected the exact factory run ID, not a path.")
        selected = chains / run
    else:
        if not candidates:
            raise ValueError("No recorded factory run. Run the factory first.")
        latest = max(path.name[:16] for path in candidates)
        matches = [path for path in candidates if path.name[:16] == latest]
        if len(matches) != 1:
            raise ValueError("Concurrent factory runs are ambiguous. Select one with --run ID.")
        (selected,) = matches
    directory(selected)
    package = selected / "receipt"
    if not package.is_dir():
        raise ValueError(
            f"Recorded run {selected.name} has no delivered receipt. "
            "Inspect that run; an earlier success will not be substituted."
        )
    directory(package)
    print(f"Recorded {harness} run: {selected.name}")
    print("This selects a saved run, not an observation of your last terminal command.")
    print(f"Receipt: {package.relative_to(root)}")
    return package


def proof(root: Path, harness: str | None, run: str | None) -> None:
    with locked(root, exclusive=False) as config:
        validate(root, config)
        package = evidence_package(root, config, harness, run)
        documents = {}
        for name in ("index.json", "provenance.intoto.json", "abom.cdx.json"):
            path = package / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError(f"Expected an ordinary bounded evidence document: {name}")
            documents[name] = json.loads(path.read_bytes())
        index = documents["index.json"]
        statement = documents["provenance.intoto.json"]
        definition = index["definition"]["digest"]["sha256"]
        binding = statement["predicate"]["buildDefinition"]["externalParameters"]["definition"]
        print("Recorded claims (not yet independently verified):")
        print(f"  Factory definition SHA-256: {json.dumps(definition, ensure_ascii=True)}")
        print(f"  in-toto envelope: {json.dumps(statement['_type'], ensure_ascii=True)}")
        print(f"  SLSA predicate: {json.dumps(statement['predicateType'], ensure_ascii=True)}")
        print(f"  SLSA factory binding: {json.dumps(binding['digest'], ensure_ascii=True)}")
        print("  ABOM: abom.cdx.json (official APM inventory, a byproduct, not a model input)")
        print(f"  CycloneDX version: {json.dumps(documents['abom.cdx.json']['specVersion'])}")
        print(f"  ABOM SHA-256: {json.dumps(index['inventory']['digest'], ensure_ascii=True)}")
        print(
            f"  Producer statements: {len(index['production'])}; check statements: {len(index['checks'])}"
        )
        print("  Selected outputs and their SHA-256 subjects:")
        for subject in statement["subject"]:
            print("    " + json.dumps(subject, ensure_ascii=True, sort_keys=True))
        print(
            "Hashes bind recorded bytes; this unsigned package does not authenticate its builder."
        )
        print(
            "Next: demo verify (schemas, actual bytes and semantic links, independently of APMX)."
        )


def verify(root: Path, harness: str | None, run: str | None, *, controls: bool) -> None:
    with locked(root, exclusive=False) as config:
        validate(root, config)
        package = evidence_package(root, config, harness, run)
        verifier = config.get("verifier")
        if not isinstance(verifier, dict):
            raise TypeError(
                "Prepare with --verifier-python and --schemas before presenting verification."
            )
        tools = root / ".demo/evidence-tools"
        if inventory(tools) != verifier["tools"]:
            raise ValueError("Independent verifier tools changed; prepare a fresh trusted kit.")
        script = tools / ("check_evidence_controls.py" if controls else "verify_evidence.py")
        entrypoint = (
            "import runpy,sys; from pathlib import Path; "
            "sys.path.insert(0,str(Path(sys.argv[1]).parent)); sys.argv=sys.argv[1:]; "
            "runpy.run_path(sys.argv[0],run_name='__main__')"
        )
        command = [
            verifier["python"],
            "-I",
            "-c",
            entrypoint,
            str(script),
            str(package),
            "--schemas",
            verifier["schemas"],
        ]
        if not controls:
            command.append("--require-capability")
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=180)
        if result.returncode != 0:
            raise ValueError(
                "Independent verification failed: " + json.dumps(result.stderr.strip())
            )
        report = json.loads(result.stdout)
        positive = report["positive"] if controls else report
        if positive["status"] != "passed":
            raise ValueError("The independent consumer did not report successful verification.")
        if controls and (
            report["originalUnchanged"] is not True or report["apmxImported"] is not False
        ):
            raise ValueError("Independent controls did not preserve the required boundary.")
        print(
            f"Independent verification passed: {positive['files']} files, "
            f"{positive['statements']} statements, {positive['capabilities']} capabilities."
        )
        print("Verified factory definition SHA-256: " + positive["definitionSha256"])
        if controls:
            for item in report["controls"]:
                print("  " + json.dumps(item["case"]) + ": " + json.dumps(item["status"]))
            print("Original evidence unchanged; corruption controls used disposable copies.")
        print(
            "Verified content binding, not a signature, authenticated identity or SLSA security level."
        )


def prepare(
    root: Path,
    archive_path: Path,
    expected: str,
    python: Path,
    *,
    verifier_python: Path | None = None,
    schemas: Path | None = None,
) -> None:
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
    if (verifier_python is None) != (schemas is None):
        raise ValueError("Supply --verifier-python and --schemas together.")
    if verifier_python is not None and schemas is not None:
        directory(schemas)
        preflight = (
            "import runpy,sys; from pathlib import Path; "
            "consumer=runpy.run_path(sys.argv[1]); "
            "consumer['schema_validator'](Path(sys.argv[2]))"
        )
        try:
            subprocess.run(
                [
                    str(verifier_python),
                    "-I",
                    "-c",
                    preflight,
                    str(ROOT / "scripts/verify_evidence.py"),
                    str(schemas),
                ],
                capture_output=True,
                check=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError(
                "Independent verifier dependencies or pinned schema cache are not ready. "
                "Follow docs/evidence.md before preparing this kit; no dependencies were installed."
            ) from error
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
    (private / "bin/apmx").symlink_to(Path("../native") / native.name / "apmx")
    shutil.copy2(Path(__file__), private / "demo.py")
    entrypoint = (
        "from pathlib import Path; import runpy,sys; "
        "root=Path(sys.argv[1]).resolve().parent; "
        "sys.argv=[str(root/'.demo/demo.py'),'--root',str(root),*sys.argv[2:]]; "
        "runpy.run_path(sys.argv[0],run_name='__main__')"
    )
    launcher = (
        "#!/bin/sh\nexec "
        + shlex.quote(str(python))
        + " -I -c "
        + shlex.quote(entrypoint)
        + ' "$0" "$@"\n'
    )
    (root / "demo").write_text(launcher, encoding="utf-8")
    (root / "demo").chmod(0o755)
    (private / "bin/demo").symlink_to("../../demo")
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
    if verifier_python is not None:
        tools = private / "evidence-tools"
        tools.mkdir()
        for name, source in EVIDENCE_TOOLS.items():
            shutil.copy2(ROOT / source, tools / name)
        config["verifier"] = {
            "python": str(verifier_python),
            "schemas": str(schemas),
            "tools": inventory(tools),
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
    create.add_argument("--verifier-python", type=Path, help="Existing independent verifier Python")
    create.add_argument("--schemas", type=Path, help="Existing offline pinned schema cache")
    for name in HARNESSES:
        commands.add_parser(name, help=f"Enter the {name} application shell")
    for name in ("reset", "clean"):
        command = commands.add_parser(
            name, help="Archive active work; reset also prepares fresh apps"
        )
        command.add_argument("harness", nargs="?", choices=HARNESSES)
    commands.add_parser("status", help="Check local kit identities and readiness")
    for name in ("proof", "verify"):
        command = commands.add_parser(
            name, help="Inspect or independently verify recorded evidence"
        )
        command.add_argument("harness", nargs="?", choices=HARNESSES)
        command.add_argument("--run", help="Select an exact recorded factory run ID")
        if name == "verify":
            command.add_argument(
                "--controls", action="store_true", help="Test relocated and corrupted copies"
            )
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare(
                args.destination.absolute(),
                args.archive.absolute(),
                args.sha256,
                args.python.expanduser().absolute(),
                verifier_python=(
                    args.verifier_python.expanduser().absolute() if args.verifier_python else None
                ),
                schemas=args.schemas.expanduser().absolute() if args.schemas else None,
            )
        elif args.root is None:
            raise ValueError("Use the prepared kit's './demo' launcher.")
        elif args.command in HARNESSES:
            return shell(args.root, args.command)
        elif args.command == "status":
            status(args.root)
        elif args.command == "proof":
            proof(args.root, args.harness, args.run)
        elif args.command == "verify":
            verify(args.root, args.harness, args.run, controls=args.controls)
        else:
            names = [args.harness] if args.harness else list(HARNESSES)
            (reset if args.command == "reset" else clean)(args.root, names)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        print(f"Demo stopped: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
