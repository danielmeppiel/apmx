"""Trusted, minimal single-purpose executor. Spawned fresh by
``container_driver.py`` via ``subprocess.Popen`` (never ``fork``/
``multiprocessing``) specifically so the candidate code it imports shares NO
address space, open file descriptors, or Python object graph with the
DRIVER process. This does NOT mean the real stdout fd is outside the
CANDIDATE's own reach: the candidate's code is ``exec_module``-ed directly
into THIS process -- it shares this same process, the same open file
descriptor table, and the same Python call stack as the code performing the
fd-0/1/2 redirection below. A sufficiently adversarial candidate CAN read
``real_stdout_fd``'s value via stack-frame introspection
(``sys._getframe()``/``f_back``) or simply enumerate and probe this
process's own open descriptors, and CAN then write directly to that fd
itself, bypassing the devnull redirection entirely. Redirecting fd 0/1/2
before import raises the bar against trivial accidental interference (a
candidate's ordinary ``print()``/``os.write(1, ...)``) but is NOT a
same-process sandboxing guarantee against a deliberately adversarial
candidate, and must never be described or relied on as one.

The property that actually makes this safe is enforced OUTSIDE this
process entirely: this executor runs disposably inside the digest-pinned,
network-isolated, read-only, non-root container, and NOTHING it ever
writes -- however it writes it, forged, duplicated, flooded, or delayed
past a deadline -- is trusted by anything outside the container. A
candidate that discovers and forges the real stdout fd can at best cause
``container_driver.py`` to see an extra, duplicated, or malformed
``RESULT:`` line (all of which it explicitly rejects as failures, never
picking "a" line to trust), or a line claiming an arbitrary ``observed``
value (which the receiver's trusted, out-of-sandbox comparison against
``expectedOutput`` -- a value this executor and the candidate it runs
NEVER see -- still has to actually match to count as a pass). Duplicated,
hung-past-deadline, or output-flooding behavior can never become
"passed": see ``container_driver.py``'s and ``run_bundle.py``'s bounded,
trustworthy-gated readers.

Protocol: read one JSON payload from stdin (candidate source text, the
module/function to call, and one bare input value), import the candidate
source as a throwaway module, call the named function with that input, and
write exactly one ``RESULT:{...}`` line to this process's real stdout
containing the raw observed return value (bounded in size) or a
distinguished error -- nothing else. Before importing candidate code, this
process's own fd 0/1/2 are redirected to /dev/null as a best-effort measure
against ACCIDENTAL interference; see above for why this is not, by itself,
a trust boundary against deliberate adversarial behavior.

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
