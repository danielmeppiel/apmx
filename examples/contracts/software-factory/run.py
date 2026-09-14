"""Five ordered contracts, exact retained handoffs, and no success certification."""

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evidence import (
    Admission,
    Stop,
    admit,
    child,
    components,
    decode,
    demand,
    encode,
    identify,
    read,
    sha,
    write_new,
)

EXAMPLE = Path(__file__).resolve().parent
ATTEMPT_WAIT = 1260
CLEANUP_WAIT = 15


@dataclass(frozen=True)
class Stage:
    name: str
    needs: tuple[str, ...]
    output: str


STAGES = (
    Stage("planning", ("request.json",), "plan.json"),
    Stage("specification", ("request.json", "plan.json"), "spec.json"),
    Stage("build", ("request.json", "plan.json", "spec.json"), "shipping.py"),
    Stage("test", ("request.json", "spec.json", "shipping.py"), "tests.json"),
    Stage(
        "review",
        ("request.json", "plan.json", "spec.json", "shipping.py", "tests.json", "evidence.json"),
        "review.json",
    ),
)


def checker_command(stage: Stage, python: str) -> str:
    """Bind only the trusted interpreter token, using apmx's POSIX shell language."""
    return f"{shlex.quote(python.replace(chr(92), '/'))} -I -B checks/verify.py {stage.name}"


def contract_bytes(stage: Stage, python: str) -> tuple[bytes, bytes, str]:
    template = read(EXAMPLE, f"contracts/{stage.name}.contract.md")
    original = f"  contract: python3 -I -B checks/verify.py {stage.name}"
    command = checker_command(stage, python)
    text = template.decode("ascii").replace("\r\n", "\n")
    demand(text.splitlines().count(original) == 1, "Trusted checker template changed.")
    rendered = text.replace(original + "\n", "  contract: " + json.dumps(command) + "\n", 1)
    return template, rendered.encode("ascii"), command


def interrupt_and_wait(process: subprocess.Popen[bytes]) -> None:
    """Give apmx time to clean up; stopping a leader is not descendant containment."""
    try:
        process.send_signal(signal.SIGINT if os.name == "posix" else signal.CTRL_BREAK_EVENT)
    except OSError:
        # It may have exited before the interrupt. Still wait; never continue the chain.
        pass
    try:
        process.wait(timeout=CLEANUP_WAIT)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            raise Stop(
                "Wrapper stop unconfirmed; inspect processes before starting another run."
            ) from exc


def execute(argv: list[str], caller: Path) -> int:
    """Launch once and stream progress; no shell strings, retries, or unbounded pipes."""
    options: dict[str, Any] = {}
    if os.name == "posix":
        options["start_new_session"] = True
    else:
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(
        argv,
        cwd=caller,
        stdin=subprocess.DEVNULL,
        stdout=sys.stderr,
        stderr=sys.stderr,
        shell=False,
        **options,
    )
    try:
        return process.wait(timeout=ATTEMPT_WAIT)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        interrupt_and_wait(process)
        raise Stop(
            "Invocation interrupted/timed out. Chain stopped; inspect the record and processes. "
            "Descendant cleanup is not certified."
        ) from exc


def new_workspace(path: Path) -> Path:
    path = path.expanduser().absolute()
    demand(".." not in path.parts, "Choose a normalized workspace path.")
    components(path)
    demand(not path.exists(), "Workspace already exists; choose a new explicit path.")
    demand(
        not any(character in str(path) for character in "*?[](){}\r\n\0"),
        "Workspace path cannot represent an exact native output permission.",
    )
    for parent in path.parents:
        demand(
            not (parent / ".git").exists()
            and not (parent / ".git").is_symlink()
            and not (
                (parent / "HEAD").exists()
                and ((parent / "objects").exists() or (parent / "config").exists())
            ),
            "Choose a workspace outside every existing Git repository; do not remove its remotes.",
        )
    path.mkdir(parents=True, mode=0o700)
    return path.resolve()


def save_ledger(workspace: Path, ledger: dict[str, Any]) -> None:
    raw = encode(ledger)
    write_new(workspace, "factory-run.json.new", raw)
    destination = child(workspace, "factory-run.json")
    os.replace(workspace / "factory-run.json.new", destination)
    demand(read(workspace, "factory-run.json", 2 * 1024 * 1024) == raw, "Ledger write failed.")


def stage_entry(
    workspace: Path,
    stage: Stage,
    admission: Admission,
    template: bytes,
    contract: bytes,
    inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "stage": stage.name,
        "exit_code": 21,
        "output": stage.output,
        "record": admission.record_path.relative_to(workspace).as_posix(),
        "record_sha256": admission.record_digest,
        "artifact": admission.artifact_path.relative_to(workspace).as_posix(),
        "sha256": sha(admission.raw),
        "size": len(admission.raw),
        "template_sha256": sha(template),
        "executed_contract_sha256": sha(contract),
        "checks": admission.checks,
        "inputs": [item for item in inputs if item["relative_path"] in stage.needs],
    }


def run_factory(
    workspace_path: Path,
    executable: Path,
    *,
    allow_host_access: bool,
    model: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """This fixed teaching example is not a reusable workflow engine."""
    ledger: dict[str, Any] = {
        "schema": "apmx-software-factory-example/1",
        "complete": False,
        "assurance": "UNPROVEN",
        "stages": [],
        "stop": None,
        "review": None,
    }
    workspace: Path | None = None
    current = "preflight"
    try:
        if not allow_host_access:
            raise Stop(
                "Explicit --allow-host-access is required. Native execution/checks use the host.",
                21,
            )
        executable = executable.expanduser().absolute()
        demand(
            executable.is_file() and os.access(executable, os.X_OK), "apmx executable is missing."
        )
        demand(
            os.name != "nt" or executable.suffix.lower() == ".exe", "Windows needs native apmx.exe."
        )
        demand(
            model is None or bool(model.strip()) and "\0" not in model, "Invalid model argument."
        )
        workspace = new_workspace(workspace_path)
        ledger["workspace"] = str(workspace)
        save_ledger(workspace, ledger)
        payloads = {"request.json": read(EXAMPLE, "request.json")}
        checker = read(EXAMPLE, "checks/verify.py")
        for index, stage in enumerate(STAGES, start=1):
            current = stage.name
            caller = child(workspace, f"stages/{index:02d}-{stage.name}")
            caller.mkdir(parents=True, mode=0o700)
            if stage.name == "review":
                payloads["evidence.json"] = encode(
                    {
                        "assurance": "UNPROVEN",
                        "stages": [
                            {key: entry[key] for key in ("stage", "output", "sha256", "checks")}
                            for entry in ledger["stages"]
                        ],
                    }
                )
            template, contract, command = contract_bytes(stage, str(Path(sys.executable).resolve()))
            files = {name: payloads[name] for name in stage.needs}
            files.update({"job.contract.md": contract, "checks/verify.py": checker})
            for name, raw in files.items():
                write_new(caller, name, raw)
            identities = identify(caller, files)
            argv = [str(executable), "job.contract.md", "--on", "copilot", "--allow-host-access"]
            if model is not None:
                argv.extend(("--model", model))
            print(f"[*] {index}/5 {stage.name}: one contract, one fresh caller.", file=sys.stderr)
            exit_code = execute(argv, caller)
            admission = admit(caller, stage.output, command, identities, exit_code, model)
            payloads[stage.output] = admission.raw
            ledger["stages"].append(
                stage_entry(
                    workspace,
                    stage,
                    admission,
                    template,
                    contract,
                    identities,
                )
            )
            save_ledger(workspace, ledger)
            print(f"[i] {stage.name}: checks passed; UNPROVEN/21 retained.", file=sys.stderr)
        review = decode(payloads["review.json"])
        demand(
            type(review) is dict
            and review.get("advisory") is True
            and review.get("assurance") == "UNPROVEN"
            and review.get("recommendation") in ("no_findings", "follow_up"),
            "Advisory review is inconsistent.",
        )
        ledger["review"] = review
        for name, raw in payloads.items():
            write_new(workspace, "result/" + name, raw)
        ledger["complete"] = True
        save_ledger(workspace, ledger)
        print(
            "[!] Five phases completed; UNPROVEN. Review is advisory, not certification.",
            file=sys.stderr,
        )
        print(encode({"advisory_review": review}).decode("ascii"), file=sys.stderr)
        return 21, ledger
    except (Stop, OSError, KeyboardInterrupt) as exc:
        code = exc.exit_code if isinstance(exc, Stop) else 22
        ledger["stop"] = {
            "stage": current,
            "exit_code": code,
            "reason": str(exc) or "Interrupted; inspect any retained evidence.",
        }
        ledger["complete"] = False
        if workspace is not None:
            if (workspace / "factory-run.json.new").exists():
                raise
            save_ledger(workspace, ledger)
        return code, ledger


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Requires Python 3.12, Git, and authenticated native Copilot. Even completion exits 21.",
    )
    parser.add_argument(
        "--apmx", type=Path, required=True, help="Path to a trusted current apmx build."
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        required=True,
        help="New explicit directory outside existing Git repositories; never reused.",
    )
    parser.add_argument(
        "--allow-host-access",
        action="store_true",
        help="Permit native execution and independent checks on this host.",
    )
    parser.add_argument(
        "--model", help="Optional native model; otherwise preserve its configured default."
    )
    args = parser.parse_args(argv)
    try:
        code, result = run_factory(
            args.workspace,
            args.apmx,
            allow_host_access=args.allow_host_access,
            model=args.model,
        )
        print(encode(result).decode("ascii"), end="")
        return code
    except (OSError, Stop, KeyboardInterrupt) as exc:
        error = (
            "Interrupted; ledger persistence is unconfirmed. Inspect retained evidence and processes."
            if isinstance(exc, KeyboardInterrupt)
            else str(exc)
        )
        print(
            json.dumps({"complete": False, "exit_code": 22, "error": error}, ensure_ascii=True),
            file=sys.stderr,
        )
        return 22


if __name__ == "__main__":
    raise SystemExit(main())
