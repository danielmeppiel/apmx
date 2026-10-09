"""Shared, dependency-free bounded process I/O used by both the trusted
in-container driver (``container_driver.py``) and the trusted host-side
execution job (``run_bundle.py``). Both must write to and read from an
untrusted process under an identical, verified-correct discipline, and
both previously shared the SAME subtle bugs this module exists to fix
once, rather than twice:

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
``overflowed``, ``timed_out``, ``read_error`` and ``returncode``) before
parsing or accepting ANY content from ``stdout``/``stderr`` -- the content
itself is never sufficient.

Two further, equally important bounded-wait subtleties this module
enforces, both found by live re-review of an earlier version that lacked
them:

1. A process that closes its stdout/stderr (producing EOF, which ends the
   select-and-read loop below) is NOT the same as a process that has
   actually exited. An adversarial or merely buggy process can close its
   output streams immediately and then keep running for arbitrarily long.
   The subsequent ``proc.wait()`` must therefore remain bounded by the
   SAME original deadline the read loop used -- never a fresh, deadline-
   free ``wait_grace_seconds`` budget -- or such a process could run for
   up to ``wait_grace_seconds`` of completely unaccounted-for extra time,
   then exit 0, and be reported ``trustworthy``. ``wait_grace_seconds`` is
   reserved exclusively for giving an already-killed process a moment to
   actually die, never as additional allowed execution time.
2. An unexpected ``OSError`` from ``os.read`` (anything other than the
   expected, transient ``InterruptedError``/EINTR) is a genuine read
   failure, not a success-shaped EOF. Silently treating it as "the process
   closed its stream" would let a read failure look identical to a clean,
   trustworthy completion. It is recorded as ``read_error`` and is always
   fail-closed via ``trustworthy``.
"""

from __future__ import annotations

import selectors
import subprocess
import time
from dataclasses import dataclass
from os import read as os_read
from os import set_blocking as os_set_blocking
from os import write as os_write

_CHUNK_BYTES = 4096


@dataclass(frozen=True)
class BoundedReadResult:
    stdout: bytes
    stderr: bytes
    overflowed: bool
    timed_out: bool
    returncode: int | None
    read_error: bool = False

    @property
    def trustworthy(self) -> bool:
        """Whether anything this process wrote is even eligible to be
        parsed. All conditions are independently necessary -- a process
        that stayed within its byte bound and exited zero, but only after
        the deadline (``timed_out``), is NOT trustworthy; nor is one that
        exited zero and on time but exceeded the byte bound
        (``overflowed``); nor is one whose output could not be read
        cleanly (``read_error``); nor is one that produced a perfectly
        bounded, on-time, well-formed line and then exited nonzero."""
        return (
            not self.overflowed
            and not self.timed_out
            and not self.read_error
            and self.returncode == 0
        )


def write_stdin_bounded(proc: subprocess.Popen, payload: bytes, deadline: float) -> bool:
    """Write the entire ``payload`` to ``proc.stdin`` without ever blocking
    past ``deadline``, then close it. A plain blocking ``file.write()`` of
    a payload larger than the pipe's kernel buffer (commonly 64KiB) would
    block indefinitely if the child process is not yet reading its stdin
    (e.g. still starting up, or deliberately stalling) -- bypassing every
    downstream read-side deadline entirely, since that deadline's clock
    would not even start until this call returned. This function instead
    puts the pipe in non-blocking mode and uses the exact same
    ``selectors``-based, deadline-bounded discipline used for reading.

    Returns ``True`` only if the complete payload was written and stdin
    was successfully closed before the deadline. Returns ``False`` if the
    deadline passed first, or the pipe broke early (the child exited or
    closed its own stdin without reading everything) -- in either case the
    caller MUST treat this exactly like a hung/timed-out process (kill it,
    report ``timed_out``), never silently proceed as though the child
    received a complete, trustworthy payload."""
    assert proc.stdin is not None
    fd = proc.stdin.fileno()
    os_set_blocking(fd, False)
    view = memoryview(payload)
    offset = 0
    total = len(view)
    selector = selectors.DefaultSelector()
    selector.register(proc.stdin, selectors.EVENT_WRITE)
    try:
        while offset < total:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            events = selector.select(timeout=min(remaining, 0.25))
            if not events:
                continue
            try:
                written = os_write(fd, view[offset : offset + _CHUNK_BYTES])
            except BlockingIOError:
                continue
            except (BrokenPipeError, OSError):
                return False
            offset += written
    finally:
        selector.close()
    try:
        proc.stdin.close()
    except OSError:
        return False
    return True


def read_process_bounded(
    proc: subprocess.Popen,
    timeout_seconds: float,
    max_stdout_bytes: int,
    max_stderr_bytes: int = 0,
    wait_grace_seconds: float = 5.0,
    deadline: float | None = None,
) -> BoundedReadResult:
    """Read ``proc.stdout`` (and, if ``max_stderr_bytes`` > 0 and the
    process has a piped stderr, ``proc.stderr`` too) incrementally,
    enforcing the byte bound and the deadline WHILE reading -- never only
    after an unbounded capture -- then waits for the process to actually
    exit, STILL bounded by that same deadline (see module docstring), and
    records its real ``returncode``. A compromised process cannot force
    this function to buffer unbounded data, cannot make a
    forged/duplicated/hung-after-writing result line look trustworthy, and
    cannot gain extra, deadline-free runtime merely by closing its output
    streams early: every one of those conditions is captured in the
    returned ``BoundedReadResult`` for the caller to check explicitly.

    ``deadline`` (an absolute ``time.monotonic()`` value), when given,
    overrides the deadline normally derived from ``timeout_seconds`` --
    used by ``run_process_bounded`` so a preceding bounded stdin write and
    this read share exactly ONE deadline, never two independent budgets."""
    if deadline is None:
        deadline = time.monotonic() + timeout_seconds
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_total = 0
    stderr_total = 0
    overflowed = False
    timed_out = False
    read_error = False

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
                except InterruptedError:
                    # EINTR: a transient, expected signal interruption.
                    # The fd's readiness is unaffected; simply retry on
                    # the next selector iteration rather than treating
                    # this as any kind of failure or EOF.
                    continue
                except OSError:
                    # Any OTHER OSError (EBADF, EIO, ...) is a genuine,
                    # unexpected read failure -- never a silent,
                    # success-shaped EOF. Recorded and fail-closed via
                    # `trustworthy`, exactly like an overflow.
                    read_error = True
                    stop = True
                    break
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

    # The read loop above only guarantees we stop reading once EITHER the
    # process produced EOF on every tracked stream, OR the deadline/an
    # overflow/a read error ended it early. It does NOT guarantee the
    # process has actually exited yet (see module docstring point 1): a
    # process that closed its output streams while continuing to run must
    # still be bounded by the SAME deadline here, not granted a fresh,
    # unrelated `wait_grace_seconds` budget as extra execution time.
    if not timed_out:
        remaining = deadline - time.monotonic()
        try:
            proc.wait(timeout=max(remaining, 0.0))
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()

    if timed_out:
        # Cleanup-only grace, after the process has already been killed
        # and already counts as timed out -- never credited as additional
        # allowed execution time.
        try:
            proc.wait(timeout=wait_grace_seconds)
        except subprocess.TimeoutExpired:
            pass

    return BoundedReadResult(
        stdout=b"".join(stdout_chunks),
        stderr=b"".join(stderr_chunks),
        overflowed=overflowed,
        timed_out=timed_out,
        read_error=read_error,
        returncode=proc.returncode,
    )


def run_process_bounded(
    proc: subprocess.Popen,
    timeout_seconds: float,
    max_stdout_bytes: int,
    max_stderr_bytes: int = 0,
    wait_grace_seconds: float = 5.0,
    stdin_payload: bytes | None = None,
) -> BoundedReadResult:
    """Bound the ENTIRE input-write / output-read / exit-wait lifecycle of
    ``proc`` under exactly ONE shared deadline derived from
    ``timeout_seconds`` -- never a separate, unbounded blocking write
    followed by an independently-bounded read, which would let a child
    that simply never reads its stdin hang this call indefinitely before
    the read-side deadline's clock even starts.

    If ``stdin_payload`` is given and cannot be fully written and closed
    before the deadline, the process is killed and the result is reported
    ``timed_out`` -- it never silently proceeds to read as though the
    child received a complete, trustworthy payload."""
    deadline = time.monotonic() + timeout_seconds
    if stdin_payload is not None:
        wrote_all = write_stdin_bounded(proc, stdin_payload, deadline)
        if not wrote_all:
            proc.kill()
            try:
                proc.wait(timeout=wait_grace_seconds)
            except subprocess.TimeoutExpired:
                pass
            return BoundedReadResult(
                stdout=b"",
                stderr=b"",
                overflowed=False,
                timed_out=True,
                read_error=False,
                returncode=proc.returncode,
            )
    return read_process_bounded(
        proc,
        timeout_seconds,
        max_stdout_bytes,
        max_stderr_bytes,
        wait_grace_seconds,
        deadline=deadline,
    )
