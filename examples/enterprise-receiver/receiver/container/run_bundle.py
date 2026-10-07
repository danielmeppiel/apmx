"""Standalone, dependency-free host-side executor for a prepared execution
bundle. This file -- and ONLY this file, plus the bundle artifact job A
produced -- is everything the disposable, zero-permission "execute" job
(job B in receiver-candidate-check.yml/receiver-attest.yml) ever needs:
no repository checkout, no ``scripts/verify_evidence`` import, no git
invocation, no secrets. It depends on nothing beyond the Python standard
library so it can run on a bare `ubuntu-24.04` runner with only Docker
preinstalled.

``check.py``'s own ``run_execution_bundle`` delegates to this module (via
``importlib``, not a textual copy) so there is exactly one implementation
of this logic; job B instead runs this file directly as
``python3 <bundle_dir>/run_bundle.py --bundle-dir <bundle_dir> --results
<path>``, using the trusted copy job A placed in the bundle (mirroring how
``container_driver.py``/``executor.py`` are shipped), never a copy
checked out from the PR or any other untrusted source.

For every required check, invokes `docker run` with a fully locked-down
flag set (`--network none --read-only --cap-drop ALL --security-opt
no-new-privileges --user 65532:65532`, resource/pids/cpu limits, a small
`noexec,nosuid` tmpfs for `/tmp`, and a READ-ONLY bind mount containing only
the bundle's own trusted scripts and the untrusted candidate bytes -- no
Docker socket, no host workspace, no runner credentials, no expected
outputs) against the pinned, digest-referenced image, feeds
`container_driver.py` its JSON payload over stdin, and reads its single
`CASES:[...]` line back with the SAME incremental, bounded-read discipline
used inside the container itself (see `_read_bounded`).

This function performs its own best-effort, host-side (never candidate-
reachable) comparison against `expectedOutput` purely so a clearly broken
candidate fails fast in this job's own logs. It never authenticates
anything by virtue of the container emitting a well-formed response, and
its own verdict is NEVER trusted downstream: the receiver's privileged
"assess" job (job C) independently re-derives every `matched` result from
the raw `observed` values recorded here, and treats this job's own pass/
fail conclusions as advisory only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import selectors
import subprocess
import time
from pathlib import Path

# Bounds enforced WHILE reading the execution container's own stdout (not
# only after a full, unbounded capture) -- see _read_bounded. A compromised
# container cannot force this job to buffer an unbounded amount of data
# before the bound is ever checked.
_MAX_CONTAINER_OUTPUT_BYTES = 65536
_CHUNK_BYTES = 4096
_EXECUTION_TIMEOUT_SECONDS = 20
_CONTAINER_RUN_TIMEOUT_SECONDS = _EXECUTION_TIMEOUT_SECONDS + 15


def _read_bounded(proc: subprocess.Popen, timeout_seconds: float) -> tuple[bytes, bool]:
    """Read ``proc.stdout`` incrementally, stopping (and killing the
    process) the instant either the byte bound or the deadline is hit --
    the bound is enforced during the read loop itself, never only after a
    full, unbounded capture."""
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
            if total > _MAX_CONTAINER_OUTPUT_BYTES:
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


def _parse_driver_output(stdout: bytes, overflowed: bool) -> list[dict] | None:
    if overflowed:
        return None
    text = stdout.decode(errors="replace")
    prefix = "CASES:"
    matching = [line for line in text.splitlines() if line.startswith(prefix)]
    if len(matching) != 1:
        # Zero lines covers a crash/timeout/early-exit; more than one is
        # ambiguous/forged. Neither is ever trusted by picking "a" line.
        return None
    try:
        parsed = json.loads(matching[0][len(prefix) :])
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    return parsed


def run_execution_bundle(bundle_dir: Path) -> list[dict]:
    """See module docstring."""
    manifest = json.loads((bundle_dir / "manifest.json").read_bytes())
    image = manifest["executionImage"]
    results = []
    for entry in manifest["checks"]:
        name = entry["name"]
        if entry["candidateMissing"]:
            results.append(
                {
                    "name": name,
                    "candidateSha256": None,
                    "imageDigestUsed": image,
                    "cases": [],
                    "error": "no candidate bytes supplied",
                }
            )
            continue
        candidate_bytes = (bundle_dir / name / "candidate.bin").read_bytes()
        # Computed from the bytes exactly as downloaded, before this
        # candidate's own code has ever run anywhere -- immune to any
        # self-mutation attempt during container execution.
        candidate_sha256 = hashlib.sha256(candidate_bytes).hexdigest()
        payload = {
            "candidateSource": candidate_bytes.decode("utf-8", errors="replace"),
            "entrypoint": entry["entrypoint"],
            "cases": [{"input": case["input"]} for case in entry["cases"]],
            "timeoutSeconds": _EXECUTION_TIMEOUT_SECONDS,
        }
        args = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "65532:65532",
            "--pids-limit",
            "64",
            "--memory",
            "256m",
            "--cpus",
            "0.5",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            "-v",
            f"{bundle_dir}:/harness:ro",
            image,
            "python3",
            "/harness/container_driver.py",
        ]
        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert proc.stdin is not None
        try:
            proc.stdin.write(json.dumps(payload).encode())
            proc.stdin.close()
        except BrokenPipeError:
            pass
        stdout, overflowed = _read_bounded(proc, _CONTAINER_RUN_TIMEOUT_SECONDS)
        observed_cases = _parse_driver_output(stdout, overflowed)
        case_results = []
        if observed_cases is None or len(observed_cases) != len(entry["cases"]):
            # Incomplete test set: crash, timeout, overflow, or a case
            # count mismatch. Every declared case is recorded as failed --
            # never silently dropped or treated as "not applicable".
            for case in entry["cases"]:
                case_results.append(
                    {
                        "input": case["input"],
                        "expectedOutput": case["expectedOutput"],
                        "observed": None,
                        "ok": False,
                        "matched": False,
                    }
                )
        else:
            for case, observed in zip(entry["cases"], observed_cases):
                matched = (
                    observed.get("ok") is True
                    and observed.get("observed") == case["expectedOutput"]
                )
                case_results.append(
                    {
                        "input": case["input"],
                        "expectedOutput": case["expectedOutput"],
                        "observed": observed.get("observed"),
                        "ok": observed.get("ok", False),
                        "matched": matched,
                    }
                )
        results.append(
            {
                "name": name,
                "candidateSha256": candidate_sha256,
                "imageDigestUsed": image,
                "cases": case_results,
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    results = run_execution_bundle(args.bundle_dir)
    args.results.write_text(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
