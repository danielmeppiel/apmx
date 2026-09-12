"""Bounded native Windows supervision; no shell, reader threads, or output queues.

The child is created suspended and assigned to a kill-on-close Job object before
its first thread runs. Cleanup confirmation describes that job, not unrelated
processes or work delegated to an external service. Callbacks run synchronously
and must return promptly, as in the POSIX adapter.

Frozen launches sanitize the child environment and temporarily clear the bundle
DLL directory under the shared spawn lock, restoring it before any callbacks.
"""

import contextlib
import ctypes
import math
import ntpath
import os
import shutil
import subprocess
import sys
import threading
import time
from ctypes import wintypes

from apmx.utils.subprocess_env import _DLL_SEARCH_LOCK, external_process_env

from .events import HEARTBEAT_SECONDS, EventEmitter
from .models import (
    ByteSink,
    ContractLimits,
    ProcessObservation,
    ProcessRequest,
    StartedSink,
)

_CHUNK_BYTES = 65536
_POLL_SECONDS = 0.01
_MAX_COMMAND_CHARS = 32767
_KILL_ON_CLOSE = 0x2000


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.DWORD),
        ("descriptor", wintypes.LPVOID),
        ("inherit", wintypes.BOOL),
    ]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("reserved", wintypes.LPWSTR),
        ("desktop", wintypes.LPWSTR),
        ("title", wintypes.LPWSTR),
        ("x", wintypes.DWORD),
        ("y", wintypes.DWORD),
        ("x_size", wintypes.DWORD),
        ("y_size", wintypes.DWORD),
        ("x_chars", wintypes.DWORD),
        ("y_chars", wintypes.DWORD),
        ("fill", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("show", wintypes.WORD),
        ("reserved_size", wintypes.WORD),
        ("reserved_bytes", ctypes.POINTER(wintypes.BYTE)),
        ("stdin", wintypes.HANDLE),
        ("stdout", wintypes.HANDLE),
        ("stderr", wintypes.HANDLE),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("startup", _StartupInfo), ("attributes", wintypes.LPVOID)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("process", wintypes.HANDLE),
        ("thread", wintypes.HANDLE),
        ("pid", wintypes.DWORD),
        ("tid", wintypes.DWORD),
    ]


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_longlong),
        ("job_time", ctypes.c_longlong),
        ("flags", wintypes.DWORD),
        ("min_working_set", ctypes.c_size_t),
        ("max_working_set", ctypes.c_size_t),
        ("active_process_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in ("reads", "writes", "other", "read_bytes", "write_bytes", "other_bytes")
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits),
        ("io", _IoCounters),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("user_time", ctypes.c_longlong),
        ("kernel_time", ctypes.c_longlong),
        ("period_user_time", ctypes.c_longlong),
        ("period_kernel_time", ctypes.c_longlong),
        ("page_faults", wintypes.DWORD),
        ("total_processes", wintypes.DWORD),
        ("active_processes", wintypes.DWORD),
        ("terminated_processes", wintypes.DWORD),
    ]


class _WinAPI:
    """Explicit signatures preserve 64-bit handles on Windows Python."""

    def __init__(self) -> None:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = wintypes.HANDLE
        pointer = wintypes.LPVOID
        dword = wintypes.DWORD
        size_pointer = ctypes.POINTER(ctypes.c_size_t)
        signatures = {
            "CreateJobObjectW": (handle, [pointer, wintypes.LPCWSTR]),
            "SetInformationJobObject": (wintypes.BOOL, [handle, ctypes.c_int, pointer, dword]),
            "QueryInformationJobObject": (
                wintypes.BOOL, [handle, ctypes.c_int, pointer, dword, pointer]
            ),
            "AssignProcessToJobObject": (wintypes.BOOL, [handle, handle]),
            "TerminateJobObject": (wintypes.BOOL, [handle, wintypes.UINT]),
            "TerminateProcess": (wintypes.BOOL, [handle, wintypes.UINT]),
            "CreatePipe": (
                wintypes.BOOL,
                [ctypes.POINTER(handle), ctypes.POINTER(handle), pointer, dword],
            ),
            "SetHandleInformation": (wintypes.BOOL, [handle, dword, dword]),
            "CreateFileW": (
                handle, [wintypes.LPCWSTR, dword, dword, pointer, dword, dword, handle]
            ),
            "InitializeProcThreadAttributeList": (
                wintypes.BOOL, [pointer, dword, dword, size_pointer]
            ),
            "UpdateProcThreadAttribute": (
                wintypes.BOOL,
                [pointer, dword, ctypes.c_size_t, pointer, ctypes.c_size_t, pointer, pointer],
            ),
            "DeleteProcThreadAttributeList": (None, [pointer]),
            "CreateProcessW": (
                wintypes.BOOL,
                [
                    wintypes.LPCWSTR, wintypes.LPWSTR, pointer, pointer, wintypes.BOOL,
                    dword, pointer, wintypes.LPCWSTR, pointer, ctypes.POINTER(_ProcessInfo),
                ],
            ),
            "ResumeThread": (dword, [handle]),
            "WaitForSingleObject": (dword, [handle, dword]),
            "GetExitCodeProcess": (wintypes.BOOL, [handle, ctypes.POINTER(dword)]),
            "PeekNamedPipe": (
                wintypes.BOOL, [handle, pointer, dword, pointer, ctypes.POINTER(dword), pointer]
            ),
            "ReadFile": (
                wintypes.BOOL, [handle, pointer, dword, ctypes.POINTER(dword), pointer]
            ),
            "CloseHandle": (wintypes.BOOL, [handle]),
            "SetDllDirectoryW": (wintypes.BOOL, [wintypes.LPCWSTR]),
        }
        for name, (result, arguments) in signatures.items():
            function = getattr(kernel, name)
            function.restype = result
            function.argtypes = arguments
            setattr(self, name, function)

    @staticmethod
    def check(result: object) -> object:
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())
        return result


@contextlib.contextmanager
def _external_dll_search(api: _WinAPI, timeout_seconds: float):
    """Keep PyInstaller's process-wide DLL directory out of the child's loader.

    Use the same lock as other external-process launchers. No callbacks execute
    while the DLL directory is cleared, and even a failed spawn restores it.
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        yield
        return
    bundle = getattr(sys, "_MEIPASS", None)
    if not isinstance(bundle, str) or not bundle or "\0" in bundle:
        raise OSError("The frozen Windows bundle DLL directory is unavailable.")
    if not _DLL_SEARCH_LOCK.acquire(timeout=min(timeout_seconds, threading.TIMEOUT_MAX)):
        raise OSError("Timed out waiting for the frozen Windows DLL search lock.")
    try:
        api.check(api.SetDllDirectoryW(None))
        try:
            yield
        finally:
            api.check(api.SetDllDirectoryW(bundle))
    finally:
        _DLL_SEARCH_LOCK.release()


def _command(request: ProcessRequest) -> tuple[str, str, str]:
    if not request.argv:
        raise ValueError("Native argv must contain non-NUL strings.")
    characters = 0
    for argument in request.argv:
        if not isinstance(argument, str):
            raise ValueError("Native argv must contain non-NUL strings.")
        characters += len(argument) + 1
        if characters > _MAX_COMMAND_CHARS:
            raise ValueError("The native command line exceeds the Windows limit.")
        if "\0" in argument:
            raise ValueError("Native argv must contain non-NUL strings.")
    selected = request.argv[0]
    if ntpath.splitext(selected)[1].lower() != ".exe":
        raise ValueError("Windows managed execution requires a native .exe, not a .cmd/.bat shim.")
    executable = shutil.which(selected)
    if executable is None or ntpath.splitext(executable)[1].lower() != ".exe":
        raise ValueError("The selected native .exe could not be resolved.")
    command = subprocess.list2cmdline((executable, *request.argv[1:]))
    # The Win32 limit counts UTF-16 code units, including the terminating NUL.
    if len(command.encode("utf-16-le")) // 2 + 1 > _MAX_COMMAND_CHARS:
        raise ValueError("The native command line exceeds the Windows limit.")
    entries = []
    characters = 1
    for key, value in external_process_env(request.env).items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("Invalid native environment entry.")
        characters += len(key) + len(value) + 2
        if characters > _MAX_COMMAND_CHARS:
            raise ValueError("The native environment exceeds the managed limit.")
        if not key or "=" in key or "\0" in key or "\0" in value:
            raise ValueError("Invalid native environment entry.")
        entries.append(f"{key}={value}")
    environment = "\0".join(sorted(entries, key=str.upper)) + "\0\0"
    if len(environment.encode("utf-16-le")) // 2 > _MAX_COMMAND_CHARS:
        raise ValueError("The native environment exceeds the managed limit.")
    return executable, command, environment


class _JobProcess:
    def __init__(self, request: ProcessRequest) -> None:
        executable, command, environment = _command(request)
        self.api = _WinAPI()
        self.handles: list[int] = []
        self.pipes: dict[str, int] = {}
        self.job = None
        self.process = None
        self.info = _ProcessInfo()
        self.assigned = False
        self.returncode: int | None = None
        try:
            self._start(request, executable, command, environment)
        except BaseException:
            self.close()
            raise

    def _own(self, handle: int) -> int:
        self.handles.append(handle)
        return handle

    def _release(self, handle: int) -> None:
        self.api.check(self.api.CloseHandle(handle))
        self.handles.remove(handle)

    def _start(
        self, request: ProcessRequest, executable: str, command: str, environment: str
    ) -> None:
        api = self.api
        self.job = self._own(api.check(api.CreateJobObjectW(None, None)))
        job_limits = _ExtendedLimits()
        job_limits.basic.flags = _KILL_ON_CLOSE
        api.check(api.SetInformationJobObject(
            self.job, 9, ctypes.byref(job_limits), ctypes.sizeof(job_limits)
        ))
        security = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), None, True)
        writes = []
        for stream in ("stdout", "stderr"):
            read, write = wintypes.HANDLE(), wintypes.HANDLE()
            api.check(api.CreatePipe(ctypes.byref(read), ctypes.byref(write),
                                     ctypes.byref(security), _CHUNK_BYTES))
            self.pipes[stream] = self._own(read.value)
            writes.append(self._own(write.value))
            api.check(api.SetHandleInformation(read, 1, 0))
        stdin = api.CreateFileW("NUL", 0x80000000, 3, ctypes.byref(security), 3, 0x80, None)
        if stdin == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        self._own(stdin)
        startup = _StartupInfoEx()
        startup.startup.cb = ctypes.sizeof(startup)
        startup.startup.flags = 0x100  # STARTF_USESTDHANDLES
        startup.startup.stdin = stdin
        startup.startup.stdout, startup.startup.stderr = writes
        size = ctypes.c_size_t()
        api.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        if not 0 < size.value <= _CHUNK_BYTES:
            raise OSError("Could not size the native handle inheritance list.")
        attributes = ctypes.create_string_buffer(size.value)
        api.check(api.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)))
        try:
            inherited = (wintypes.HANDLE * 3)(stdin, *writes)
            api.check(api.UpdateProcThreadAttribute(
                attributes, 0, 0x20002, inherited, ctypes.sizeof(inherited), None, None
            ))
            startup.attributes = ctypes.cast(attributes, wintypes.LPVOID)
            env_buffer = ctypes.create_unicode_buffer(environment)
            # CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT.
            with _external_dll_search(api, request.timeout_seconds):
                api.check(api.CreateProcessW(
                    executable, ctypes.create_unicode_buffer(command), None, None, True,
                    0x4 | 0x400 | 0x80000, env_buffer, str(request.cwd),
                    ctypes.byref(startup), ctypes.byref(self.info),
                ))
            self.process = self._own(self.info.process)
            thread = self._own(self.info.thread)
            self.pid = self.info.pid
            api.check(api.AssignProcessToJobObject(self.job, self.process))
            self.assigned = True
            for handle in (stdin, *writes):
                self._release(handle)
            if api.ResumeThread(thread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            self.info.thread = None
            self._release(thread)
        finally:
            api.DeleteProcThreadAttributeList(attributes)

    def poll(self) -> int | None:
        if self.returncode is None:
            result = self.api.WaitForSingleObject(self.process, 0)
            if result == 0:
                code = wintypes.DWORD()
                self.api.check(self.api.GetExitCodeProcess(self.process, ctypes.byref(code)))
                self.returncode = code.value
            elif result != 258:  # WAIT_TIMEOUT
                raise ctypes.WinError(ctypes.get_last_error())
        return self.returncode

    def active_processes(self) -> int:
        accounting = _Accounting()
        self.api.check(self.api.QueryInformationJobObject(
            self.job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None
        ))
        return accounting.active_processes

    def read(self, stream: str) -> bytes | None:
        handle = self.pipes[stream]
        available = wintypes.DWORD()
        if not self.api.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
            if ctypes.get_last_error() in (109, 232, 233):
                self._release(handle)
                del self.pipes[stream]
                return None
            raise ctypes.WinError(ctypes.get_last_error())
        if not available.value:
            return b""
        buffer = ctypes.create_string_buffer(min(available.value, _CHUNK_BYTES))
        count = wintypes.DWORD()
        self.api.check(self.api.ReadFile(
            handle, buffer, len(buffer), ctypes.byref(count), None
        ))
        return buffer.raw[:count.value]

    def terminate(self) -> None:
        self.api.check(self.api.TerminateJobObject(self.job, 1))

    def close(self) -> None:
        # A Python interrupt can arrive immediately after CreateProcessW returns,
        # before its output handles have been copied into the ownership list.
        if self.info.process and self.process is None:
            self.process = self.info.process
        for handle in (self.info.process, self.info.thread):
            if handle and handle not in self.handles:
                self._own(handle)
        # An assignment failure leaves only a suspended leader, never a running tree.
        if self.process is not None and not self.assigned:
            self.api.TerminateProcess(self.process, 1)
        # Closing the non-inherited, sole job handle kills any remaining job members.
        if self.job in self.handles:
            self.api.CloseHandle(self.job)
            self.handles.remove(self.job)
        for handle in reversed(self.handles):
            self.api.CloseHandle(handle)
        self.handles.clear()
        self.info.process = self.info.thread = None


def supervise_process(
    request: ProcessRequest,
    *,
    on_bytes: ByteSink,
    on_started: StartedSink | None = None,
    events: EventEmitter | None = None,
    limits: ContractLimits | None = None,
) -> ProcessObservation:
    """Observe an owned Windows job with bounded pipe draining and cleanup.

    ``pgid`` is always None: a Windows job is not a POSIX process group. Signals
    records the actual TerminateJobObject operation, not invented POSIX signals.
    No native fallback runs if safe pre-resume job assignment is unavailable.
    """
    limits = limits or ContractLimits()
    started = time.monotonic()
    if os.name != "nt":
        return ProcessObservation(None, error="The native Windows adapter requires Windows.")
    if not math.isfinite(request.timeout_seconds) or not math.isfinite(limits.cleanup_seconds):
        return ProcessObservation(None, error="Process deadlines must be finite.")
    if request.timeout_seconds <= 0:
        return ProcessObservation(None, stop_reason="timeout")
    if limits.cleanup_seconds <= 0:
        return ProcessObservation(None, error="Process cleanup allowance must be positive.")
    try:
        child = _JobProcess(request)
    except ValueError as exc:
        return ProcessObservation(None, error=f"Could not start the selected process: {exc}")
    except OSError as exc:
        # Startup can fail after CreateProcess but before assignment/resumption.
        # Handles were closed, but no job-empty observation was obtained.
        return ProcessObservation(
            None, error=f"Could not start the selected process: {exc}", cleanup_confirmed=False
        )
    stop_reason = None
    stop_started = None
    leader_exited_at = None
    next_heartbeat = started + HEARTBEAT_SECONDS
    cleanup_confirmed = False
    signals: list[str] = []
    residual_group: tuple[dict[str, object], ...] = ()
    pending = {"stdout", "stderr"}

    def stop(reason: str, now: float) -> None:
        nonlocal stop_reason, stop_started
        stop_reason = reason
        if stop_started is None:
            stop_started = now
            # Terminate before invoking observers, which may themselves fail.
            child.terminate()
            signals.append("TerminateJobObject")
            if events is not None:
                events.emit("stop_requested", reason=reason)

    try:
        try:
            if on_started is not None:
                on_started(child.pid, None)
            if events is not None:
                events.emit("process_started", pid=child.pid, pgid=None)
        except KeyboardInterrupt:
            stop("cancelled", time.monotonic())
        while True:
            try:
                now = time.monotonic()
                returncode = child.poll()
                active = child.active_processes()
                if returncode is not None and leader_exited_at is None:
                    leader_exited_at = now
                if returncode is not None and active == 0 and not pending:
                    cleanup_confirmed = True
                    break
                if stop_started is None:
                    if now - started >= request.timeout_seconds:
                        stop("timeout", now)
                    elif (
                        leader_exited_at is not None
                        and active
                        and now - leader_exited_at >= min(0.5, limits.cleanup_seconds / 4)
                    ):
                        residual_group = ({"inspection": "owned_windows_job",
                                           "active_processes": active},)
                        stop("lingering_children", now)
                    elif now >= next_heartbeat and returncode is None:
                        next_heartbeat = now + HEARTBEAT_SECONDS
                        if events is not None:
                            events.emit("heartbeat", pid=child.pid)
                if stop_started is not None and now - stop_started >= limits.cleanup_seconds:
                    cleanup_confirmed = child.poll() is not None and child.active_processes() == 0
                    break
                drained = False
                for stream in ("stdout", "stderr"):
                    if stream not in pending:
                        continue
                    chunk = child.read(stream)
                    if chunk is None:
                        pending.remove(stream)
                    elif chunk:
                        drained = True
                        on_bytes(stream, chunk)
                if not drained:
                    time.sleep(_POLL_SECONDS)
            except KeyboardInterrupt:
                stop("cancelled", time.monotonic())
    finally:
        try:
            if not cleanup_confirmed:
                # Callback/native failures propagate only after a bounded cleanup attempt.
                deadline = (
                    time.monotonic() + limits.cleanup_seconds
                    if stop_started is None else stop_started + limits.cleanup_seconds
                )
                with contextlib.suppress(OSError, KeyboardInterrupt):
                    child.terminate()
                    while time.monotonic() < deadline:
                        if child.poll() is not None and child.active_processes() == 0:
                            break
                        time.sleep(_POLL_SECONDS)
        finally:
            child.close()
    if events is not None and stop_reason is not None:
        events.emit("stop_observed", confirmed=cleanup_confirmed, reason=stop_reason)
    return ProcessObservation(
        child.returncode,
        pid=child.pid,
        pgid=None,
        elapsed_seconds=time.monotonic() - started,
        stop_reason=stop_reason,
        cleanup_confirmed=cleanup_confirmed,
        signals=tuple(signals),
        residual_group=residual_group,
    )
