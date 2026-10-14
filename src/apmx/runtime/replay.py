"""Replay producer for ``apmx check``: deliver recorded outputs instead of running an agent.

``apmx check`` runs a factory on a fresh machine that never ran the agent. For
each contract, this producer writes the output bytes another run recorded for
that exact contract digest into the producer workspace and exits. Capture,
the factory's own checks, the record and the receipt then run unchanged, so
the receipt of record is produced where the agent never ran. The receipt
labels the harness ``replay``: it never claims to have observed a model.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path, PurePosixPath

ENV_BUNDLE = "APMX_REPLAY_BUNDLE"
ENV_INTERNAL = "APMX_INTERNAL_REPLAY"
MAX_FILE = 16 * 1024 * 1024


class ReplayRuntime:
    """Builds the replay producer request; the plan's executable is apmx itself."""

    def build_contract_request(self, plan, snapshot, run_directory, *, timeout_seconds):
        from ..contracts.models import ContractError, Outcome, ProcessRequest

        bundle = os.environ.get(ENV_BUNDLE)
        if not bundle or not Path(bundle).is_dir():
            raise ContractError(
                "The replay producer only runs under `apmx check`.",
                code="unsupported_harness",
                outcome=Outcome.UNPROVEN,
            )
        env = dict(os.environ)
        if getattr(sys, "frozen", False):
            command: tuple[str, ...] = (str(plan.executable),)
            env[ENV_INTERNAL] = "1"
        else:
            command = (str(plan.executable), "-B", "-m", "apmx.runtime.replay")
            # The child must import this same apmx, whatever the caller's environment.
            source = str(Path(__file__).resolve().parents[2])
            env["PYTHONPATH"] = os.pathsep.join(filter(None, (source, env.get("PYTHONPATH"))))
        return ProcessRequest(
            argv=(*command, plan.contract.source_digest),
            cwd=snapshot.producer,
            timeout_seconds=timeout_seconds,
            env=env,
        )


def _emit(kind: str, **data) -> None:
    print(json.dumps({"type": kind, "data": data}), flush=True)


def _relative(root: Path, path: Path) -> str:
    relative = PurePosixPath(path.relative_to(root).as_posix())
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError(f"unsafe recorded output path: {relative}")
    return relative.as_posix()


def replay(bundle: Path, digest: str, workspace: Path) -> list[str]:
    """Copy the recorded outputs of contract ``digest`` into ``workspace``."""
    source = bundle / digest
    if len(digest) != 64 or not source.is_dir() or source.is_symlink():
        raise ValueError(f"no recorded outputs for contract sha256:{digest[:12]}..")
    written = []
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"recorded output {path.name} is a symlink")
        if path.is_dir():
            continue
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
            raise ValueError(f"recorded output {path.name} is not a bounded regular file")
        name = _relative(source, path)
        target = workspace
        for part in PurePosixPath(name).parts[:-1]:
            target = target / part
            if target.is_symlink():
                raise ValueError(f"workspace path for {name} contains a symlink")
        target.mkdir(parents=True, exist_ok=True)
        destination = target / PurePosixPath(name).name
        if destination.is_symlink():
            raise ValueError(f"workspace output {name} is a symlink")
        destination.write_bytes(path.read_bytes())
        written.append(name)
    if not written:
        raise ValueError(f"no recorded outputs for contract sha256:{digest[:12]}..")
    return written


def main(argv: list[str] | None = None) -> int:
    """Speak the minimal Copilot JSON protocol the contract decoder expects."""
    argv = sys.argv[1:] if argv is None else argv
    started = time.monotonic()
    try:
        names = replay(Path(os.environ[ENV_BUNDLE]), argv[0], Path.cwd())
        text = f"Replayed {len(names)} recorded output(s): {', '.join(names)}"
        code = 0
    except (KeyError, IndexError, OSError, ValueError) as exc:
        text, code = f"Replay failed: {exc}", 1
    _emit("assistant.message", messageId="replay", content=text, phase="final_answer")
    print(
        json.dumps(
            {
                "type": "result",
                "exitCode": code,
                "sessionId": "apmx-replay",
                "usage": {"seconds": round(time.monotonic() - started, 3)},
            }
        ),
        flush=True,
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
