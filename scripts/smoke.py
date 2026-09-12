"""Exercise frozen local/package jobs outside the checkout, without app imports.

Only Copilot's protocol is simulated. The binary, workspace capture, independent
checker process, retained artifact, record, reduction, and cleanup are real.
Python is an explicit test/checker prerequisite, not the app's runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ROOT = FIXTURES.parents[1]
PRIVATE_MARKERS = ("PRIVATE_REASONING_SENTINEL", "PRIVATE_TOOL_SENTINEL")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_actor(output: Path) -> None:
    require(os.name == "nt", "Only Windows needs a frozen protocol actor")
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--onefile", "--noupx", "--name", "copilot",
            "--distpath", str(output),
            "--workpath", str(output / "work"),
            "--specpath", str(output),
            str(FIXTURES / "copilot_actor.py"),
        ],
        check=True,
    )
    require((output / "copilot.exe").is_file(), "Native Windows actor was not built")


def isolated_env(root: Path, tools: Path) -> dict[str, str]:
    system_keys = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "SYSTEMDRIVE"}
    env = {key: value for key, value in os.environ.items() if key.upper() in system_keys}
    for name in ("home", "temp", "config", "data", "cache", "appdata", "localappdata", "copilot"):
        (root / name).mkdir()
    env.update({
        "PATH": str(tools) + os.pathsep + env.get("PATH", ""),
        "HOME": str(root / "home"),
        "USERPROFILE": str(root / "home"),
        "APPDATA": str(root / "appdata"),
        "LOCALAPPDATA": str(root / "localappdata"),
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_DATA_HOME": str(root / "data"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "COPILOT_HOME": str(root / "copilot"),
        "TMPDIR": str(root / "temp"),
        "TMP": str(root / "temp"),
        "TEMP": str(root / "temp"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "apmx fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "apmx fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "NO_COLOR": "1",
        "TERM": "dumb",
        "APMX_ACTOR_LOG": str(root / "actor.jsonl"),
    })
    require("PYTHONPATH" not in env and "PYTHONHOME" not in env, "Python source fallback")
    return env


def prepare_tools(root: Path, actor: Path | None) -> Path:
    tools = root / "tools"
    tools.mkdir()
    if os.name == "nt":
        require(actor is not None, "Windows smoke requires an explicit native copilot.exe actor")
        require(actor.read_bytes()[:2] == b"MZ", "Windows fixture must be a native PE executable")
        shutil.copyfile(actor, tools / "copilot.exe")
    else:
        require(actor is None, "External actor override is only supported on Windows")
        script = tools / "copilot_actor.py"
        shutil.copyfile(FIXTURES / "copilot_actor.py", script)
        wrapper = tools / "copilot"
        wrapper.write_text(
            f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(script))} \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
    return tools


def snapshot(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): digest(path) for path in root.rglob("*") if path.is_file()}


def run_binary(binary: Path, args: list[str], caller: Path, env: dict[str, str]):
    result = subprocess.run(
        [str(binary), *args],
        cwd=caller,
        env=env,
        input="",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        check=False,
    )
    require(
        all(marker not in result.stdout + result.stderr for marker in PRIVATE_MARKERS),
        "Private tool payload or reasoning was printed",
    )
    return result


def run_case(binary: Path, root: Path, actor: Path | None, selection: str, mode: str) -> dict:
    root.mkdir()
    require(not root.resolve().is_relative_to(ROOT.resolve()), "Smoke caller must be outside checkout")
    tools = prepare_tools(root, actor)
    env = isolated_env(root, tools)
    env["APMX_ACTOR_MODE"] = mode
    caller = root / "caller"
    package = root / "package"
    caller.mkdir()
    package.mkdir()
    (caller / "notes.md").write_text('{"source": "caller", "value": 7}\n', encoding="utf-8")
    (package / "notes.md").write_text('{"source": "package-decoy", "value": 100}\n', encoding="utf-8")
    (package / "apm.yml").write_text(
        "name: release-smoke\nversion: 0.1.0\ndependencies:\n  apm: []\n", encoding="utf-8"
    )
    source = caller if selection == "local" else package
    (source / "checks").mkdir()
    shutil.copyfile(FIXTURES / "check.py", source / "checks/check.py")
    # Forward slashes preserve the absolute interpreter through POSIX sh and cmd.
    interpreter = Path(sys.executable).as_posix()
    checker_command = f'"{interpreter}" -I checks/check.py'
    contract = source / "handoff.contract.md"
    contract.write_text(
        "---\nneeds: notes.md\nproduces: handoff.json\nverify:\n"
        f"  identity: {json.dumps(checker_command)}\n---\n"
        "Read notes.md and write its JSON object to handoff.json without changing any values.\n",
        encoding="utf-8",
    )
    caller_before = snapshot(caller)
    package_before = snapshot(package)
    home_before = snapshot(root / "home")
    temp_before = snapshot(root / "temp")
    args = ["handoff.contract.md"]
    if selection == "package":
        args = ["--from", str(package), *args]
    result = run_binary(
        binary,
        [*args, "--on", "copilot", "--model", "fixture-model", "--allow-host-access"],
        caller, env,
    )
    code, outcome = {"pass": (21, "UNPROVEN"), "reject": (20, "REJECTED"), "halt": (22, "HALTED")}[mode]
    require(
        result.returncode == code,
        f"{selection}/{mode}: expected {code}, got {result.returncode}\n{result.stdout}\n{result.stderr}",
    )
    records = list(caller.rglob("record.json"))
    require(len(records) == 1, f"Expected exactly one completed record: {records}")
    record = json.loads(records[0].read_bytes())
    run = records[0].parent.resolve()
    require(record["schema"] in {"apm-contract-run/0.1", "apmx-contract-run/0.1"}, "Record schema")
    require(record["complete"] is True and record["phase"] == "finished", "Incomplete record")
    require(record["profile"] == "native-advisory", "Unexpected assurance profile")
    require(
        Path(record["executable"]).resolve().is_relative_to(tools.resolve()),
        "Run did not select the explicitly identified hermetic actor",
    )
    require(record["result"]["outcome"] == {"name": outcome, "exit_code": code}, "Record outcome")
    require(record["source"]["sha256"] == digest(contract), "Selected source digest mismatch")
    require(Path(record["caller_root"]).resolve() == caller.resolve(), "Caller/source separation")
    require(record["child_pid"] is None and record["active_check"] is None, "Unfinished process state")
    require(record["producer"]["pid"] is not None, "Native producer process was not recorded")
    require(record["producer"]["cleanup_confirmed"] is True, "Producer cleanup unconfirmed")
    require(0 <= record["producer"]["elapsed_seconds"] < 90, "Producer execution was not bounded")
    require(record["producer"]["returncode"] == (7 if mode == "halt" else 0), "Native process exit")
    require(record["native_reported_exit_code"] == (7 if mode == "halt" else 0), "Native envelope exit")
    require(
        "Hermetic fixture progress." in result.stdout and "Hermetic fixture finished." in result.stdout,
        "Public phase/delta narration was not surfaced",
    )
    if mode == "halt":
        require(bool(record["result"]["stop_reason"]), "Operational halt needs a stop reason")
        require(record["result"]["checks"] == [], "Failed producer must not run checks")
        require(record["artifact"] is None, "Failed producer must not capture an earlier artifact")
    else:
        require(record["result"]["stop_reason"] is None, "Unexpected operational stop")
        artifact = Path(record["artifact"]["path"])
        require(artifact.resolve().is_relative_to(run), "Artifact is outside retained run")
        require(record["artifact"]["sha256"] == digest(artifact), "Artifact digest mismatch")
        require(record["artifact"]["size"] == artifact.stat().st_size, "Artifact size mismatch")
        expected = {"source": "caller", "value": 7 if mode == "pass" else -1}
        require(json.loads(artifact.read_bytes()) == expected, "Wrong output identity")
        checks = record["checks"]
        require(len(checks) == 1, "Expected exactly one independent check")
        checked = checks[0]
        require(record["result"]["checks"] == checks, "Final result lost check observations")
        require(record["result"]["artifact"] == record["artifact"], "Final result changed output identity")
        require(checked["normalized"] == (0 if mode == "pass" else 1), "Incorrect check result")
        require(checked["subject_digest"] == digest(artifact), "Checker assessed different bytes")
        require(checked["process"]["pid"] is not None, "Independent checker was not launched")
        require(checked["process"]["cleanup_confirmed"] is True, "Checker cleanup unconfirmed")
        assessments = list((run / "assessments").iterdir())
        require(len(assessments) == 1, "Expected one independent assessment")
        require(
            digest(assessments[0] / "checks/check.py") == digest(FIXTURES / "check.py"),
            "Assessment did not retain the original checker",
        )
        require(
            digest(run / "producer/checks/check.py") != digest(assessments[0] / "checks/check.py"),
            "Producer checker poisoning was not exercised",
        )
        require(digest(assessments[0] / "handoff.json") == digest(artifact), "Assessment output")
    require(snapshot(package) == package_before, "Source package was changed")
    require(snapshot(root / "home") == home_before, "Ambient profile was changed")
    require(snapshot(root / "temp") == temp_before, "Temporary producer/package files were not cleaned")
    for path, expected_digest in caller_before.items():
        require(digest(caller / path) == expected_digest, f"Caller file changed: {path}")
    for path in set(snapshot(caller)) - set(caller_before):
        require((caller / path).resolve().is_relative_to(run), f"Unexpected caller write: {path}")
    if selection == "package":
        prepared_root = Path(record["source"]["package"]["root"])
        if prepared_root.resolve() != package.resolve():
            require(not prepared_root.exists(), "Private package not cleaned")
    transcript = (run / "transcript.log").read_text(encoding="utf-8")
    require(not any(marker in transcript for marker in PRIVATE_MARKERS), "Private payload retained")
    calls = [json.loads(line) for line in Path(env["APMX_ACTOR_LOG"]).read_text().splitlines()]
    require(sum("-p" in call["argv"] for call in calls) == 1, "Expected exactly one fixture producer")
    return {
        "selection": selection, "mode": mode, "exit_code": result.returncode,
        "actor": "hermetic Copilot JSONL protocol fixture; NOT live model inference",
        "record": record,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--version")
    parser.add_argument("--actor", type=Path)
    parser.add_argument("--build-actor", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.build_actor:
        build_actor(args.build_actor.resolve())
        return
    if args.binary is None or args.version is None:
        parser.error("--binary and --version are required")
    binary = args.binary.resolve()
    require(binary.is_file(), "Frozen executable not found")
    header = binary.read_bytes()[:4]
    require(
        header[:2] == b"MZ" or header == b"\x7fELF"
        or header in {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe"},
        "Smoke must receive a native frozen executable, not a source launcher",
    )
    actor = args.actor.resolve() if args.actor else None
    with tempfile.TemporaryDirectory(prefix="apmx-frozen-smoke-") as temporary:
        root = Path(temporary).resolve()
        tools = root / "version-tools"
        tools.mkdir()
        probe = root / "probe"
        probe.mkdir()
        env = isolated_env(probe, tools)
        for flag in ("--help", "--version"):
            result = run_binary(binary, [flag], probe, env)
            require(result.returncode == 0, f"{flag}: {result.stdout}\n{result.stderr}")
            if flag == "--version":
                require(args.version in result.stdout, "Wrong released version")
        cases = [
            run_case(binary, root / f"{selection}-{mode}", actor, selection, mode)
            for selection in ("local", "package")
            for mode in ("pass", "reject", "halt")
        ]
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                json.dumps({"binary_sha256": digest(binary), "version": args.version, "cases": cases}, indent=2) + "\n",
                encoding="utf-8",
            )
    print("Frozen smoke: 6 local/package cases passed; hermetic protocol, NOT live inference.")


if __name__ == "__main__":
    main()
