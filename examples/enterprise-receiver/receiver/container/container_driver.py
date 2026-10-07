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
import subprocess
import sys
from pathlib import Path

# Explicit, not relying on Python's implicit script-directory sys.path
# prepending: this script is normally invoked as a real top-level script
# (``python3 /harness/container_driver.py``) where that would suffice, but
# some test/invocation contexts run it under ``-I`` (isolated mode), which
# explicitly suppresses that automatic behavior. The explicit insert below
# is correct and idempotent in every invocation context, and makes
# ``bounded_io.py`` importable as long as it is shipped into the exact
# same minimal, candidate-inaccessible harness directory as this file (see
# ``run_bundle.py``'s harness-directory construction), never the full
# bundle (which also holds ``manifest.json``'s ``expectedOutput`` and other
# checks' candidate bytes).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bounded_io import BoundedReadResult, run_process_bounded  # noqa: E402

_EXECUTOR_PATH = Path(__file__).resolve().parent / "executor.py"

# Bounds enforced WHILE reading (not after a full, unbounded capture), so a
# compromised executor cannot force this driver to buffer an unbounded
# amount of data before the bound is ever checked.
_MAX_EXECUTOR_OUTPUT_BYTES = 16384


def _parse_result(result: BoundedReadResult) -> dict:
    # A line's own content is never sufficient: a process that printed a
    # perfectly well-formed RESULT: line and then hung past the deadline,
    # or exited nonzero afterward, or exceeded its output bound, is exactly
    # as untrusted as one that printed nothing -- checked BEFORE ever
    # touching ``result.stdout``'s content.
    if result.overflowed:
        return {"ok": False, "error": "executor output exceeded the bound"}
    if result.timed_out:
        return {"ok": False, "error": "executor did not exit before the deadline"}
    if result.returncode != 0:
        return {
            "ok": False,
            "error": f"executor exited with nonzero status {result.returncode}",
        }
    text = result.stdout.decode(errors="replace")
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
        # A single call bounding the WHOLE write-input / read-output /
        # wait-for-exit lifecycle under one shared deadline. A plain
        # blocking ``proc.stdin.write(...)`` here would run BEFORE any
        # read-side deadline's clock even starts: an executor that never
        # reads its stdin (stalled, or deliberately not reading it) could
        # then block this driver indefinitely, with the intended
        # ``timeout_seconds`` bound never actually applying.
        result = run_process_bounded(
            proc,
            timeout_seconds,
            _MAX_EXECUTOR_OUTPUT_BYTES,
            stdin_payload=json.dumps(executor_payload).encode(),
        )
        observed.append(_parse_result(result))

    sys.stdout.write("CASES:" + json.dumps(observed) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
