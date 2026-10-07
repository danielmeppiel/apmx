"""Shared, dependency-free bounded process-output reader used by both the
trusted in-container driver (``container_driver.py``) and the trusted
host-side execution job (``run_bundle.py``). Both must read untrusted
process output under an identical, verified-correct discipline, and both
previously shared the SAME subtle bug this module exists to fix once,
rather than twice:

A ``selectors``-based readiness check only tells you a read of AT LEAST
ONE BYTE will not block -- it does NOT mean a full chunk-sized read is
available. A plain ``file.read(n)`` on Python's default buffered PIPE
wrapper (``io.BufferedReader``) internally loops issuing further raw reads
until it has accumulated the full ``n`` bytes requested (or hit EOF) before
returning -- so a process that writes ONE byte and then hangs can still
make that call block past the intended deadline, even though the selector
correctly reported readiness. This module always issues a single raw
``os.read(fd, n)`` syscall per ready event instead, which returns whatever
is immediately available (including fewer bytes than requested, including
exactly the one byte a stalling process wrote), preserving the bounded-wait
semantics the selector loop is actually supposed to provide.

A well-formed, parseable output LINE is never, by itself, sufficient
grounds to accept anything a monitored process wrote: a process that
printed its one expected line and then hung past the deadline, or that
exited with a nonzero status sometime after printing it, is exactly as
untrusted as one that printed nothing at all, or one that printed the
line twice, or one that kept writing until the byte bound was hit. Callers
MUST check ``BoundedReadResult.trustworthy`` (which folds in
``overflowed``, ``timed_out``, and ``returncode``) before parsing or
accepting ANY content from ``stdout``/``stderr`` -- the content itself is
never sufficient.
"""

from __future__ import annotations

import selectors
import subprocess
import time
from dataclasses import dataclass
from os import read as os_read

_CHUNK_BYTES = 4096


@dataclass(frozen=True)
class BoundedReadResult:
    stdout: bytes
    stderr: bytes
    overflowed: bool
    timed_out: bool
    returncode: int | None

    @property
    def trustworthy(self) -> bool:
        """Whether anything this process wrote is even eligible to be
        parsed. All three conditions are independently necessary -- a
        process that stayed within its byte bound and exited zero, but
        only after the deadline (``timed_out``), is NOT trustworthy; nor
        is one that exited zero and on time but exceeded the byte bound
        (``overflowed``); nor is one that produced a perfectly bounded,
        on-time, well-formed line and then exited nonzero."""
        return not self.overflowed and not self.timed_out and self.returncode == 0


def read_process_bounded(
    proc: subprocess.Popen,
    timeout_seconds: float,
    max_stdout_bytes: int,
    max_stderr_bytes: int = 0,
    wait_grace_seconds: float = 5.0,
) -> BoundedReadResult:
    """Read ``proc.stdout`` (and, if ``max_stderr_bytes`` > 0 and the
    process has a piped stderr, ``proc.stderr`` too) incrementally,
    enforcing the byte bound and the deadline WHILE reading -- never only
    after an unbounded capture -- then waits for the process to actually
    exit and records its real ``returncode``. A compromised process cannot
    force this function to buffer unbounded data, nor can it make a
    forged/duplicated/hung-after-writing result line look trustworthy:
    every one of those conditions is captured in the returned
    ``BoundedReadResult`` for the caller to check explicitly."""
    deadline = time.monotonic() + timeout_seconds
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_total = 0
    stderr_total = 0
    overflowed = False
    timed_out = False

    assert proc.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ, data="stdout")
    track_stderr = max_stderr_bytes > 0 and proc.stderr is not None
    if track_stderr:
        selector.register(proc.stderr, selectors.EVENT_READ, data="stderr")

    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                proc.kill()
                break
            events = selector.select(timeout=min(remaining, 0.25))
            if not events:
                continue
            stop = False
            for key, _mask in events:
                fileobj = key.fileobj
                stream = key.data
                try:
                    chunk = os_read(fileobj.fileno(), _CHUNK_BYTES)
                except OSError:
                    chunk = b""
                if not chunk:
                    # EOF on this stream only -- the other stream (if any)
                    # may still have data pending.
                    selector.unregister(fileobj)
                    continue
                if stream == "stdout":
                    stdout_total += len(chunk)
                    if stdout_total > max_stdout_bytes:
                        overflowed = True
                        stop = True
                        break
                    stdout_chunks.append(chunk)
                else:
                    stderr_total += len(chunk)
                    if stderr_total > max_stderr_bytes:
                        overflowed = True
                        stop = True
                        break
                    stderr_chunks.append(chunk)
            if stop:
                proc.kill()
                break
    finally:
        selector.close()

    try:
        proc.wait(timeout=wait_grace_seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=wait_grace_seconds)
        except subprocess.TimeoutExpired:
            pass

    return BoundedReadResult(
        stdout=b"".join(stdout_chunks),
        stderr=b"".join(stderr_chunks),
        overflowed=overflowed,
        timed_out=timed_out,
        returncode=proc.returncode,
    )
