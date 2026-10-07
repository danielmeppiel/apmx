"""Standalone, dependency-free host-side executor for a prepared execution
bundle. This file -- and ONLY this file, plus the bundle artifact job A
produced -- is everything the disposable, zero-permission "execute" job
(job B in receiver-candidate-check.yml/receiver-attest.yml) ever needs:
no repository checkout, no ``scripts/verify_evidence`` import, no git
invocation, no secrets. It depends on nothing beyond the Python standard
library (plus the sibling ``bounded_io.py`` shipped into the same bundle
directory) so it can run on a bare `ubuntu-24.04` runner with only Docker
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
`noexec,nosuid` tmpfs for `/tmp`) against the pinned, digest-referenced
image, feeds `container_driver.py` its JSON payload over stdin (candidate
bytes travel over stdin as a string field, NEVER as a mounted file), and
reads its single `CASES:[...]` line back with the SAME bounded-while-
reading discipline used inside the container itself (see
``bounded_io.read_process_bounded``).

The ONLY thing ever bind-mounted into the container is a purpose-built,
per-run MINIMAL harness directory containing exactly ``container_driver.py``,
``executor.py``, and ``bounded_io.py`` -- never this bundle directory
itself, which also holds ``manifest.json`` (containing every case's
``expectedOutput``), this very file, and every OTHER check's
``candidate.bin``. Mounting the whole bundle would hand a compromised,
network-isolated-but-still-adversarial container read access to the exact
answers it is being tested against, and to candidate bytes belonging to
checks it has no business seeing. The harness directory's path is always
resolved to an absolute path and passed via an explicit
``--mount type=bind,source=...,destination=/harness,readonly`` flag --
never the short ``-v NAME:/path`` form, which Docker's CLI treats as a
NAMED VOLUME (not a host bind mount) whenever the given source has no path
separator, silently mounting an anonymous, empty, Docker-managed volume
instead of the intended host directory.

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
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

# This script is always invoked as ``python3 <bundle_dir>/run_bundle.py``
# (a real top-level script execution, whether that is job B's actual
# invocation, this module's own ``main()`` below, or ``check.py``'s
# local/test-only ``importlib``-based delegation) OR loaded via
# ``importlib.util.spec_from_file_location`` (which does NOT prepend the
# loaded file's own directory to ``sys.path``). The explicit insert below
# makes the sibling ``bounded_io.py`` importable in every one of those
# cases, not just the ones where Python would have added it automatically.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bounded_io import BoundedReadResult, run_process_bounded

# Bounds enforced WHILE reading the execution container's own stdout (not
# only after a full, unbounded capture) -- see bounded_io.read_process_bounded.
# A compromised container cannot force this job to buffer an unbounded
# amount of data before the bound is ever checked.
_MAX_CONTAINER_STDOUT_BYTES = 65536
# Docker CLI/daemon diagnostics (image pull failures, mount errors) land on
# stderr; a bounded amount is retained for operator diagnostics on failure,
# but is NEVER treated as, or substituted for, a pass/fail signal.
_MAX_CONTAINER_STDERR_BYTES = 4096
_EXECUTION_TIMEOUT_SECONDS = 20
_CONTAINER_RUN_TIMEOUT_SECONDS = _EXECUTION_TIMEOUT_SECONDS + 15
_DOCKER_RM_TIMEOUT_SECONDS = 15


def _parse_driver_output(result: BoundedReadResult) -> list[dict] | None:
    # Exactly as in container_driver.py: a line's own content is never
    # sufficient. A process that printed a well-formed CASES: line and then
    # hung past the deadline, or exited nonzero afterward, or exceeded its
    # output bound, is exactly as untrusted as one that printed nothing.
    if not result.trustworthy:
        return None
    text = result.stdout.decode(errors="replace")
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


def _build_harness_dir(bundle_dir: Path, scratch_root: Path) -> Path:
    """Build the MINIMAL, candidate-inaccessible directory that is the
    ONLY thing ever bind-mounted into the execution container: exactly the
    trusted ``container_driver.py``/``executor.py``/``bounded_io.py`` the
    prepare stage (job A) shipped into this bundle -- never
    ``manifest.json`` (which holds every case's ``expectedOutput``), never
    this file, never any check's ``candidate.bin``, and never the bundle
    directory itself."""
    harness_dir = scratch_root / "harness"
    harness_dir.mkdir(parents=True, exist_ok=True)
    for name in ("container_driver.py", "executor.py", "bounded_io.py"):
        shutil.copy2(bundle_dir / name, harness_dir / name)
    return harness_dir


def _cleanup_container(container_name: str) -> str | None:
    """Unconditionally remove the container by its own unique, owned name,
    regardless of whether this job killed the `docker` CLI client process,
    the container exited on its own, or `--rm` already cleaned it up.
    Killing the CLI process alone does NOT guarantee the container itself
    (a separate process tree supervised by the daemon, not a child of the
    CLI) stops running -- only an explicit `docker rm -f` against its own
    name does. This is scoped to exactly the one container this run
    created (never a broad `docker kill`/`prune` sweep, which could affect
    unrelated containers on a shared runner).

    Returns ``None`` when the container is verified gone -- either this
    call removed it, or Docker reports "no such container" (meaning
    `--rm`, or a prior call to this same function, already won the race:
    genuinely fine, not a failure). Returns a short diagnostic string for
    every OTHER outcome (a nonzero exit for any other reason, or the
    `docker rm` invocation itself timing out/erroring) -- these are
    DISTINCT from "already removed" and must never be silently swallowed
    as though cleanup definitely succeeded; the caller records this
    diagnostic for operator visibility (it never affects any check's
    pass/fail verdict, which depends solely on `case_results`)."""
    try:
        proc = subprocess.run(
            ["docker", "rm", "--force", container_name],
            capture_output=True,
            timeout=_DOCKER_RM_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"docker rm --force {container_name} timed out"
    except OSError as exc:
        return f"docker rm --force {container_name} failed to run: {exc}"
    if proc.returncode == 0:
        return None
    stderr_text = proc.stderr.decode(errors="replace")
    if "no such container" in stderr_text.lower():
        # The container was already gone (``--rm`` or a prior cleanup call
        # won the race) -- an explicit, verified-absent outcome, not a
        # failure.
        return None
    return f"docker rm --force {container_name} exited {proc.returncode}: {stderr_text.strip()}"


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
        try:
            # STRICT decode: the source text handed to the container must be
            # the exact text that hashes to candidate_sha256. A lossy
            # ``errors="replace"`` decode here would silently repair invalid
            # UTF-8 into different, executable bytes than the ones that were
            # actually hashed and bound into the result -- recorded digest
            # and executed content must never diverge. Non-UTF-8 candidate
            # source is refused, not "fixed", before Docker ever runs.
            candidate_source = candidate_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            results.append(
                {
                    "name": name,
                    "candidateSha256": candidate_sha256,
                    "imageDigestUsed": image,
                    "cases": [],
                    "error": f"candidate source is not valid UTF-8, refusing to execute: {error}",
                }
            )
            continue
        payload = {
            "candidateSource": candidate_source,
            "entrypoint": entry["entrypoint"],
            "cases": [{"input": case["input"]} for case in entry["cases"]],
            "timeoutSeconds": _EXECUTION_TIMEOUT_SECONDS,
        }
        container_name = f"apmx-exec-{uuid.uuid4().hex}"
        diagnostic: str | None = None
        observed_cases: list[dict] | None = None
        with tempfile.TemporaryDirectory(prefix="apmx-harness-") as scratch:
            harness_dir = _build_harness_dir(bundle_dir, Path(scratch))
            args = [
                "docker",
                "run",
                "--rm",
                "-i",
                "--name",
                container_name,
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
                "--mount",
                f"type=bind,source={harness_dir.resolve()},destination=/harness,readonly",
                image,
                "python3",
                "/harness/container_driver.py",
            ]
            try:
                proc = subprocess.Popen(
                    args,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                # One call bounds the whole write-input / read-output /
                # wait-for-exit lifecycle under a single shared deadline
                # (see bounded_io.run_process_bounded's docstring): a
                # blocking stdin write issued before this call could let a
                # container that is slow to start, or never reads stdin at
                # all, block well past `_CONTAINER_RUN_TIMEOUT_SECONDS`
                # with no bound whatsoever on that phase.
                result = run_process_bounded(
                    proc,
                    _CONTAINER_RUN_TIMEOUT_SECONDS,
                    _MAX_CONTAINER_STDOUT_BYTES,
                    _MAX_CONTAINER_STDERR_BYTES,
                    stdin_payload=json.dumps(payload).encode(),
                )
                observed_cases = _parse_driver_output(result)
                if observed_cases is None:
                    reason = (
                        "timed out"
                        if result.timed_out
                        else "output exceeded the bound"
                        if result.overflowed
                        else f"exited with status {result.returncode}"
                        if result.returncode != 0
                        else "produced no single well-formed CASES line"
                    )
                    stderr_tail = result.stderr.decode(errors="replace").strip()
                    diagnostic = f"container run {reason}" + (
                        f"; stderr: {stderr_tail}" if stderr_tail else ""
                    )
            finally:
                # Unconditional, bounded, scoped-to-this-one-container
                # cleanup regardless of which branch above was taken.
                # Its own failure is distinct from the run's own
                # diagnostic and must never be silently dropped: a
                # `docker rm` failure (permission error, unreachable
                # daemon, etc.) is operator-relevant evidence that this
                # job may be leaking a container, even when the
                # candidate's own execution looked fine.
                cleanup_diagnostic = _cleanup_container(container_name)
                if cleanup_diagnostic is not None:
                    diagnostic = (
                        diagnostic + "; " + cleanup_diagnostic if diagnostic else cleanup_diagnostic
                    )

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
        result_entry = {
            "name": name,
            "candidateSha256": candidate_sha256,
            "imageDigestUsed": image,
            "cases": case_results,
        }
        if diagnostic is not None:
            # Diagnostic context only -- never consulted by any pass/fail
            # decision, which is derived exclusively from `case_results`
            # above.
            result_entry["diagnostic"] = diagnostic
        results.append(result_entry)
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
