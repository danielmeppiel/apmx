"""Trusted, minimal single-purpose executor. Spawned fresh by
``container_driver.py`` via ``subprocess.Popen`` (never ``fork``/
``multiprocessing``) specifically so the candidate code it imports shares NO
address space, open file descriptors, or Python object graph with the
driver that spawned it -- only this process's own stdin/stdout, which this
script fully controls from outside candidate reach.

Protocol: read one JSON payload from stdin (candidate source text, the
module/function to call, and one bare input value), import the candidate
source as a throwaway module, call the named function with that input, and
write exactly one ``RESULT:{...}`` line to this process's real stdout
containing the raw observed return value (bounded in size) or a
distinguished error -- nothing else. Before importing candidate code, this
process's own fd 0/1/2 are redirected to /dev/null, so none of the
candidate's own ``print()``/``os.write(1, ...)`` calls (however it tries,
including direct raw-fd writes that bypass ``sys.stdout``) can reach the
real channel back to the driver; the saved real stdout fd is only restored,
and only ever written to by this trusted script's own code, after the call
completes or raises.

If candidate code calls ``os._exit()`` (or otherwise crashes the whole
process) during import or execution, this process terminates immediately,
before the ``RESULT:`` line is ever written -- the driver sees no result
line at all for that case. The driver (and, independently, the receiver's
own job C) MUST treat a missing/malformed/duplicated result line as an
explicit failure, never as an ambiguous or accidental pass.

This script decides nothing about pass/fail and never sees the receiver's
expected output -- it only ever reports what the candidate actually
returned, as inert data for the trusted host to compare out-of-band. A
well-formed ``RESULT:`` line is black-box OBSERVED BEHAVIOR, not an
authenticated claim; nothing here (or in ``container_driver.py``) should
ever be read as self-certifying a pass.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile

# Caps how large a JSON-encoded observed value this script will ever try to
# report, so a candidate that returns an enormous value cannot flood the
# pipe back to the driver (which itself also bounds how much it reads).
_MAX_OBSERVED_JSON_BYTES = 8192


def main() -> int:
    payload = json.loads(sys.stdin.buffer.read())
    candidate_source = payload["candidateSource"]
    module_name = payload["module"]
    function_name = payload["function"]
    call_input = payload["input"]

    real_stdout_fd = os.dup(1)
    devnull_fd = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull_fd, 0)
    os.dup2(devnull_fd, 1)
    os.dup2(devnull_fd, 2)
    try:
        with tempfile.TemporaryDirectory(prefix="candidate-") as scratch:
            candidate_path = os.path.join(scratch, f"{module_name}.py")
            with open(candidate_path, "w", encoding="utf-8") as handle:
                handle.write(candidate_source)
            spec = importlib.util.spec_from_file_location(module_name, candidate_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            function = getattr(module, function_name)
            observed = function(call_input)
        result: dict = {"ok": True, "observed": observed}
    except BaseException as error:  # noqa: BLE001 - every failure mode must be reported
        result = {"ok": False, "error": repr(error)}

    try:
        encoded = json.dumps(result)
    except (TypeError, ValueError):
        encoded = json.dumps({"ok": False, "error": "observed value not JSON-serializable"})
    if len(encoded) > _MAX_OBSERVED_JSON_BYTES:
        encoded = json.dumps({"ok": False, "error": "observed value exceeded the size bound"})

    os.write(real_stdout_fd, ("RESULT:" + encoded + "\n").encode())
    os.close(real_stdout_fd)
    os.close(devnull_fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
