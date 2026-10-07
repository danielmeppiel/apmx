"""Trusted container entrypoint. Runs INSIDE the disposable, network-
isolated, read-only, non-root container image this receiver pins by digest
-- never candidate-supplied, always shipped by the trusted prepare stage
(job A) and bind-mounted read-only alongside the candidate bytes it is
asked to exercise.

Reads one JSON payload from stdin: candidate source text, the declared
entrypoint (module/function), and the bare input values to call it with,
one per declared case. For EACH case, spawns a completely separate OS
process (``executor.py`` -- see its own docstring for why a fresh
``subprocess.Popen`` rather than a forked/threaded Python function is
required) and reads back exactly one structured result line from that
process's own stdout, bounding how much is ever read so neither a flood of
output nor a hung process can block this driver indefinitely.

This script NEVER sees the receiver's expected answer and NEVER decides
pass/fail -- it only ever reports the candidate's raw, bounded, observed
output as untrusted black-box behavior. The actual assertion against
policy's declared ``expectedOutput`` happens entirely outside this
container, in the receiver's own trusted job B/C logic (see
``run_execution_bundle``/``verify_execution_results`` in
``receiver/check.py``); nothing this script emits should ever be read as
self-certifying a pass.
"""

from __future__ import annotations

import json
import selectors
import subprocess
import sys
import time
from pathlib import Path

_EXECUTOR_PATH = Path(__file__).resolve().parent / "executor.py"

# Bounds enforced WHILE reading (not after a full, unbounded capture), so a
# compromised executor cannot force this driver to buffer an unbounded
# amount of data before the bound is ever checked.
_MAX_EXECUTOR_OUTPUT_BYTES = 16384
_CHUNK_BYTES = 4096


def _read_bounded(proc: subprocess.Popen, timeout_seconds: float) -> tuple[bytes, bool]:
    """Read ``proc.stdout`` incrementally, stopping (and killing the
    process) the instant either the byte bound or the deadline is hit --
    bounding happens during the read loop itself, never only afterward."""
    deadline = time.monotonic() + timeout_seconds
    chunks: list[bytes] = []
    total = 0
    overflowed = False
    selector = selectors.DefaultSelector()
    assert proc.stdout is not None
    selector.register(proc.stdout, selectors.EVENT_READ)
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                proc.kill()
                break
            events = selector.select(timeout=min(remaining, 0.25))
            if not events:
                if proc.poll() is not None:
                    break
                continue
            chunk = proc.stdout.read(_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_EXECUTOR_OUTPUT_BYTES:
                overflowed = True
                proc.kill()
                break
            chunks.append(chunk)
    finally:
        selector.close()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    return b"".join(chunks), overflowed


def _parse_result(stdout: bytes, overflowed: bool) -> dict:
    if overflowed:
        return {"ok": False, "error": "executor output exceeded the bound"}
    text = stdout.decode(errors="replace")
    prefix = "RESULT:"
    matching = [line for line in text.splitlines() if line.startswith(prefix)]
    if not matching:
        # Covers a crash, a timeout, and deliberately an os._exit()-during-
        # import style bypass: all of these terminate the executor before
        # it ever writes its one result line, so they are indistinguishable
        # from each other here and MUST be treated as an explicit failure,
        # never an ambiguous or accidental pass.
        return {"ok": False, "error": "executor produced no RESULT line"}
    if len(matching) > 1:
        # A single well-formed process writes exactly one RESULT line; more
        # than one is ambiguous/forged and must never be trusted by picking
        # "the first" or "the last" one.
        return {"ok": False, "error": "executor produced more than one RESULT line"}
    try:
        parsed = json.loads(matching[0][len(prefix) :])
    except json.JSONDecodeError:
        return {"ok": False, "error": "executor RESULT line was not valid JSON"}
    if not isinstance(parsed, dict) or "ok" not in parsed:
        return {"ok": False, "error": "executor RESULT line had an unexpected shape"}
    return parsed


def main() -> int:
    payload = json.loads(sys.stdin.buffer.read())
    candidate_source = payload["candidateSource"]
    entrypoint = payload["entrypoint"]
    cases = payload["cases"]
    timeout_seconds = payload.get("timeoutSeconds", 20)

    observed = []
    for case in cases:
        executor_payload = {
            "candidateSource": candidate_source,
            "module": entrypoint["module"],
            "function": entrypoint["function"],
            "input": case["input"],
        }
        proc = subprocess.Popen(
            [sys.executable, "-I", "-S", str(_EXECUTOR_PATH)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert proc.stdin is not None
        try:
            proc.stdin.write(json.dumps(executor_payload).encode())
            proc.stdin.close()
        except BrokenPipeError:
            pass
        stdout, overflowed = _read_bounded(proc, timeout_seconds)
        observed.append(_parse_result(stdout, overflowed))

    sys.stdout.write("CASES:" + json.dumps(observed) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
