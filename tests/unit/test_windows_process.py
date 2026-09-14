"""Portable supervisor tests and real Win32 job/pipe lifecycle regressions."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from apmx.contracts import process_windows as windows
from apmx.contracts.events import EventEmitter
from apmx.contracts.models import ContractLimits, ProcessRequest
from apmx.utils import subprocess_env


class FakeJob:
    pid = 123
    returncode = None

    def __init__(self, request):
        self.request = request
        self.chunks = {"stdout": [b"out", None], "stderr": [b"err", None]}
        self.terminated = 0
        self.closed = False
        self.running = False
        self.residual = False
        self.unclean = False

    def poll(self):
        if not self.running:
            self.returncode = 0
        return self.returncode

    def active_processes(self):
        return int(self.running or self.residual or self.unclean)

    def read(self, stream):
        chunks = self.chunks[stream]
        return chunks.pop(0) if chunks else None

    def terminate(self):
        self.terminated += 1
        self.running = False
        self.residual = False

    def close(self):
        self.closed = True


@pytest.fixture
def managed(monkeypatch):
    clock = [0.0]
    jobs = []

    def create(request):
        job = FakeJob(request)
        jobs.append(job)
        return job

    monkeypatch.setattr(windows, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(windows, "_JobProcess", create)
    monkeypatch.setattr(
        windows,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock[0],
            sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        ),
    )
    return jobs, clock


def request(timeout=1.0):
    return ProcessRequest(("python.exe",), Path.cwd(), timeout)


def test_success_preserves_streams_and_observation_interface(managed):
    output, starts, events = [], [], []
    observation = windows.supervise_process(
        request(),
        on_bytes=lambda *args: output.append(args),
        on_started=lambda *args: starts.append(args),
        events=EventEmitter("run", events.append),
    )
    assert output == [("stdout", b"out"), ("stderr", b"err")]
    assert starts == [(123, None)]
    assert observation.returncode == 0
    assert observation.pgid is None
    assert observation.cleanup_confirmed
    assert observation.stop_reason is None
    assert [event.kind for event in events] == ["process_started"]
    assert managed[0][0].closed and not managed[0][0].terminated


def test_natural_descendant_shutdown_uses_existing_cleanup_budget(managed, monkeypatch):
    original = windows._JobProcess

    def create(req):
        job = original(req)
        job.active_processes = lambda: int(managed[1][0] < 3.0)
        return job

    monkeypatch.setattr(windows, "_JobProcess", create)
    observation = windows.supervise_process(request(10), on_bytes=lambda *args: None)
    assert observation.cleanup_confirmed
    assert observation.stop_reason is None
    assert not observation.signals
    assert 3.0 <= managed[1][0] < 6.0


@pytest.mark.parametrize("callback", ["bytes", "started", "event"])
def test_callback_exception_cleans_job_before_propagating(managed, callback):
    def fail(*args):
        raise RuntimeError("observer failed")

    with pytest.raises(RuntimeError, match="observer failed"):
        windows.supervise_process(
            request(),
            on_bytes=fail if callback == "bytes" else lambda *args: None,
            on_started=fail if callback == "started" else None,
            events=EventEmitter("run", fail) if callback == "event" else None,
        )
    assert managed[0][0].closed
    assert managed[0][0].terminated == 1


@pytest.mark.parametrize("reason", ["timeout", "lingering_children", "cancelled"])
def test_bounded_stop_lifecycle(managed, monkeypatch, reason):
    original = windows._JobProcess

    def create(req):
        job = original(req)
        job.running = reason != "lingering_children"
        job.residual = reason == "lingering_children"
        return job

    monkeypatch.setattr(windows, "_JobProcess", create)
    events = []

    def started(*args):
        if reason == "cancelled":
            raise KeyboardInterrupt

    observation = windows.supervise_process(
        request(0.1 if reason == "timeout" else 1),
        on_bytes=lambda *args: None,
        on_started=started,
        events=EventEmitter("run", events.append),
        limits=ContractLimits(cleanup_seconds=0.1),
    )
    assert observation.stop_reason == reason
    assert observation.cleanup_confirmed
    assert observation.signals == ("TerminateJobObject",)
    assert events[-1].kind == "stop_observed"
    assert events[-1].data["confirmed"] is True
    assert managed[0][0].closed
    assert managed[1][0] < 0.5
    if reason == "lingering_children":
        assert observation.residual_group == (
            {"inspection": "owned_windows_job", "active_processes": 1},
        )


def test_unconfirmed_cleanup_stays_unconfirmed(managed, monkeypatch):
    original = windows._JobProcess

    def create(req):
        job = original(req)
        job.unclean = True
        return job

    monkeypatch.setattr(windows, "_JobProcess", create)
    observation = windows.supervise_process(
        request(0.1),
        on_bytes=lambda *args: None,
        limits=ContractLimits(cleanup_seconds=0.1),
    )
    assert not observation.cleanup_confirmed
    assert managed[0][0].closed
    assert managed[1][0] < 0.3


def test_zero_timeout_does_not_spawn(managed):
    observation = windows.supervise_process(request(0), on_bytes=lambda *args: None)
    assert observation.stop_reason == "timeout"
    assert not managed[0]


def test_native_startup_failure_does_not_invent_cleanup_confirmation(managed, monkeypatch):
    def fail(req):
        raise OSError("job assignment denied")

    monkeypatch.setattr(windows, "_JobProcess", fail)
    observation = windows.supervise_process(request(), on_bytes=lambda *args: None)
    assert "job assignment denied" in observation.error
    assert not observation.cleanup_confirmed


def test_heartbeat_callback_failure_cleans_running_job(managed, monkeypatch):
    original = windows._JobProcess

    def create(req):
        job = original(req)
        job.running = True
        return job

    def emit(event):
        if event.kind == "heartbeat":
            raise RuntimeError("heartbeat failed")

    monkeypatch.setattr(windows, "_JobProcess", create)
    with pytest.raises(RuntimeError, match="heartbeat failed"):
        windows.supervise_process(
            request(10),
            on_bytes=lambda *args: None,
            events=EventEmitter("run", emit),
        )
    assert managed[0][0].terminated
    assert managed[0][0].closed


def test_stop_callback_failure_occurs_after_termination(managed):
    def started(*args):
        managed[0][0].running = True

    def emit(event):
        if event.kind == "stop_requested":
            assert managed[0][0].terminated
            raise RuntimeError("stop failed")

    with pytest.raises(RuntimeError, match="stop failed"):
        windows.supervise_process(
            request(0.1),
            on_bytes=lambda *args: None,
            on_started=started,
            events=EventEmitter("run", emit),
        )
    assert managed[0][0].closed


@pytest.mark.parametrize("duration", [float("nan"), float("inf")])
def test_nonfinite_timeout_does_not_spawn(managed, duration):
    assert windows.supervise_process(request(duration), on_bytes=lambda *args: None).error
    assert not managed[0]


@pytest.mark.parametrize("executable", ["shim.cmd", "SHIM.BAT", "python", ""])
def test_rejects_shell_shims_without_resolution(monkeypatch, executable):
    def unexpected(*args):
        pytest.fail("unsafe argv should be refused before resolution")

    monkeypatch.setattr(windows.shutil, "which", unexpected)
    with pytest.raises(ValueError, match="native .exe"):
        windows._command(ProcessRequest((executable,), Path.cwd(), 1))


def test_arguments_use_crt_quoting_without_shell(monkeypatch):
    monkeypatch.setattr(windows.shutil, "which", lambda selected: selected)
    argv = (r"C:\Program Files\python.exe", "a b", 'x"y', "&echo nope", "%PATH%")
    executable, command, environment = windows._command(ProcessRequest(argv, Path.cwd(), 1))
    assert executable == argv[0]
    assert command == subprocess.list2cmdline(argv)
    assert environment is not None
    assert environment.endswith("\0\0")


def test_command_length_counts_utf16_units(monkeypatch):
    monkeypatch.setattr(windows.shutil, "which", lambda selected: selected)
    with pytest.raises(ValueError, match="Windows limit"):
        windows._command(ProcessRequest(("python.exe", "\U0001f600" * 17000), Path.cwd(), 1))


def test_explicit_environment_is_not_inherited(monkeypatch):
    monkeypatch.setattr(windows.shutil, "which", lambda selected: selected)
    _, _, environment = windows._command(
        ProcessRequest(
            ("python.exe",),
            Path.cwd(),
            1,
            env={"Z": "last", "A": "first"},
        )
    )
    assert environment == "A=first\0Z=last\0\0"


@pytest.mark.parametrize("inherit", [False, True])
def test_frozen_environment_sanitizes_loader_variables_without_mutation(monkeypatch, inherit):
    base = {
        "LD_LIBRARY_PATH": "bundle",
        "LD_LIBRARY_PATH_ORIG": "user-libraries",
        "DYLD_LIBRARY_PATH": "bundle",
        "DYLD_FRAMEWORK_PATH": "bundle",
        "PATH": "native-tools",
    }
    original = base.copy()
    monkeypatch.setattr(windows.shutil, "which", lambda selected: selected)
    monkeypatch.setattr(subprocess_env.sys, "frozen", True, raising=False)
    if inherit:
        monkeypatch.setattr(subprocess_env.os, "environ", base)
    req = ProcessRequest(("python.exe",), Path.cwd(), 1, env=None if inherit else base)
    _, _, environment = windows._command(req)
    assert environment == "LD_LIBRARY_PATH=user-libraries\0PATH=native-tools\0\0"
    assert base == original


class FakeAPI:
    """Emulate Win32 handle ownership/startup without loading a Windows DLL."""

    def __init__(self, fail_assignment=False):
        self.calls = []
        self.next_handle = 20
        self.fail_assignment = fail_assignment
        self.available = 2 * windows._CHUNK_BYTES

    @staticmethod
    def check(result):
        if not result:
            raise OSError("native operation failed")
        return result

    def CreateJobObjectW(self, *args):
        self.calls.append(("job",))
        return 10

    def SetInformationJobObject(self, job, kind, info, size):
        assert kind == 9
        assert info._obj.basic.flags == windows._KILL_ON_CLOSE
        self.calls.append(("kill_on_close",))
        return True

    def CreatePipe(self, read, write, security, size):
        assert security._obj.inherit
        assert size == windows._CHUNK_BYTES
        read._obj.value, write._obj.value = self.next_handle, self.next_handle + 1
        self.next_handle += 2
        return True

    def SetHandleInformation(self, handle, mask, flags):
        assert mask == 1 and flags == 0
        return True

    def CreateFileW(self, *args):
        assert args[0] == "NUL"
        return 30

    def InitializeProcThreadAttributeList(self, attributes, count, flags, size):
        size._obj.value = 64
        return attributes is not None

    def UpdateProcThreadAttribute(self, attrs, flags, kind, handles, size, *args):
        assert kind == 0x20002
        assert list(handles) == [30, 21, 23]
        self.calls.append(("handle_list",))
        return True

    def CreateProcessW(self, executable, command, pa, ta, inherit, flags, env, cwd, si, pi):
        assert flags & 0x4  # Suspended even before assignment.
        assert flags & 0x80000  # Explicit inherited-handle list.
        assert flags & 0x400  # Unicode environment.
        assert inherit
        assert si._obj.startup.stdin == 30
        assert si._obj.startup.stdout == 21
        assert si._obj.startup.stderr == 23
        pi._obj.process, pi._obj.thread, pi._obj.pid = 40, 41, 123
        self.calls.append(("create_suspended",))
        return True

    def AssignProcessToJobObject(self, job, process):
        assert (job, process) == (10, 40)
        self.calls.append(("assign",))
        return not self.fail_assignment

    def ResumeThread(self, thread):
        assert thread == 41
        self.calls.append(("resume",))
        return 1

    def DeleteProcThreadAttributeList(self, attrs):
        self.calls.append(("delete_attributes",))

    def TerminateProcess(self, handle, code):
        self.calls.append(("terminate_process", handle))
        return True

    def CloseHandle(self, handle):
        self.calls.append(("close", handle))
        return True

    def SetDllDirectoryW(self, directory):
        self.calls.append(("dll_directory", directory))
        return True

    def PeekNamedPipe(self, handle, buffer, size, read, available, remaining):
        available._obj.value = self.available
        return True

    def ReadFile(self, handle, buffer, size, count, overlapped):
        assert size == windows._CHUNK_BYTES
        count._obj.value = size
        return True


@pytest.fixture
def native_mock(monkeypatch):
    api = FakeAPI()
    monkeypatch.setattr(windows, "_WinAPI", lambda: api)
    monkeypatch.setattr(windows.shutil, "which", lambda selected: selected)
    return api


def test_native_setup_assigns_suspended_child_before_resume(native_mock):
    child = windows._JobProcess(request())
    names = [call[0] for call in native_mock.calls]
    assert names.index("kill_on_close") < names.index("create_suspended")
    assert names.index("handle_list") < names.index("create_suspended")
    assert names.index("create_suspended") < names.index("assign") < names.index("resume")
    assert child.pid == 123
    child.close()
    closed = [call[1] for call in native_mock.calls if call[0] == "close"]
    assert sorted(closed) == [10, 20, 21, 22, 23, 30, 40, 41]


def test_assignment_failure_never_resumes_child_and_closes_every_handle(native_mock):
    native_mock.fail_assignment = True
    with pytest.raises(OSError, match="native operation failed"):
        windows._JobProcess(request())
    assert ("resume",) not in native_mock.calls
    assert ("terminate_process", 40) in native_mock.calls
    assert ("delete_attributes",) in native_mock.calls
    closed = [call[1] for call in native_mock.calls if call[0] == "close"]
    assert sorted(closed) == [10, 20, 21, 22, 23, 30, 40, 41]


def test_interrupt_immediately_after_creation_still_owns_suspended_child(native_mock, monkeypatch):
    original = native_mock.CreateProcessW

    def interrupted(*args):
        original(*args)
        raise KeyboardInterrupt

    monkeypatch.setattr(native_mock, "CreateProcessW", interrupted)
    with pytest.raises(KeyboardInterrupt):
        windows._JobProcess(request())
    assert ("resume",) not in native_mock.calls
    assert ("terminate_process", 40) in native_mock.calls
    closed = [call[1] for call in native_mock.calls if call[0] == "close"]
    assert sorted(closed) == [10, 20, 21, 22, 23, 30, 40, 41]


def test_native_pipe_read_is_bounded_and_does_not_wait_for_data(native_mock):
    child = windows._JobProcess(request())
    try:
        assert len(child.read("stdout")) == windows._CHUNK_BYTES
        native_mock.available = 0
        assert child.read("stderr") == b""
    finally:
        child.close()


@pytest.fixture
def frozen_windows(monkeypatch):
    monkeypatch.setattr(windows, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", r"C:\bundle\_internal", raising=False)


def test_frozen_spawn_restores_dll_directory_before_resume(native_mock, frozen_windows):
    assert windows._DLL_SEARCH_LOCK is subprocess_env._DLL_SEARCH_LOCK
    child = windows._JobProcess(request())
    try:
        calls = native_mock.calls
        clear = calls.index(("dll_directory", None))
        create = calls.index(("create_suspended",))
        restore = calls.index(("dll_directory", sys._MEIPASS))
        resume = calls.index(("resume",))
        assert clear < create < restore < resume
    finally:
        child.close()


@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt])
def test_frozen_spawn_failure_restores_dll_directory(
    native_mock, frozen_windows, monkeypatch, failure
):
    def fail(*args):
        raise failure("spawn failed")

    monkeypatch.setattr(native_mock, "CreateProcessW", fail)
    with pytest.raises(failure, match="spawn failed"):
        windows._JobProcess(request())
    assert [call for call in native_mock.calls if call[0] == "dll_directory"] == [
        ("dll_directory", None),
        ("dll_directory", sys._MEIPASS),
    ]
    assert not any(call[0] == "resume" for call in native_mock.calls)


def test_frozen_dll_restore_failure_cleans_suspended_child(
    native_mock, frozen_windows, monkeypatch
):
    def set_directory(directory):
        return directory is None

    monkeypatch.setattr(native_mock, "SetDllDirectoryW", set_directory)
    with pytest.raises(OSError, match="native operation failed"):
        windows._JobProcess(request())
    assert ("terminate_process", 40) in native_mock.calls
    assert ("resume",) not in native_mock.calls
    closed = [call[1] for call in native_mock.calls if call[0] == "close"]
    assert sorted(closed) == [10, 20, 21, 22, 23, 30, 40, 41]


def test_nonfrozen_spawn_does_not_change_dll_directory(native_mock, monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    child = windows._JobProcess(request())
    child.close()
    assert not any(call[0] == "dll_directory" for call in native_mock.calls)


def test_frozen_dll_search_lock_wait_is_bounded(native_mock, frozen_windows, monkeypatch):
    attempts = []

    class BusyLock:
        def acquire(self, *, timeout):
            attempts.append(timeout)
            return False

        def release(self):
            pytest.fail("Unacquired lock must not be released")

    monkeypatch.setattr(windows, "_DLL_SEARCH_LOCK", BusyLock())
    with (
        pytest.raises(OSError, match="Timed out waiting"),
        windows._external_dll_search(native_mock, 0.25),
    ):
        pytest.fail("Busy DLL-search lock must prevent spawning")
    assert attempts == [0.25]
    assert not any(call[0] == "dll_directory" for call in native_mock.calls)


def test_frozen_dll_clear_failure_releases_lock_without_spawning(
    native_mock, frozen_windows, monkeypatch
):
    acquired = []

    class TrackedLock:
        def acquire(self, *, timeout):
            acquired.append(True)
            return True

        def release(self):
            assert acquired.pop()

    def fail(directory):
        assert acquired == [True]
        assert directory is None
        return False

    monkeypatch.setattr(windows, "_DLL_SEARCH_LOCK", TrackedLock())
    monkeypatch.setattr(native_mock, "SetDllDirectoryW", fail)
    with pytest.raises(OSError, match="native operation failed"):
        windows._JobProcess(request())
    assert not acquired
    assert ("create_suspended",) not in native_mock.calls


def test_frozen_missing_bundle_directory_refuses_spawn(native_mock, frozen_windows, monkeypatch):
    monkeypatch.delattr(sys, "_MEIPASS")
    with pytest.raises(OSError, match="bundle DLL directory is unavailable"):
        windows._JobProcess(request())
    assert ("create_suspended",) not in native_mock.calls


windows_only = pytest.mark.skipif(os.name != "nt", reason="requires native Win32 APIs")


def native_request(code, timeout=5):
    return ProcessRequest((sys.executable, "-c", code), Path.cwd(), timeout)


@windows_only
def test_native_drains_both_large_pipes_and_returns_exit_code():
    totals = {"stdout": 0, "stderr": 0}

    def receive(stream, chunk):
        assert len(chunk) <= windows._CHUNK_BYTES
        totals[stream] += len(chunk)

    observation = windows.supervise_process(
        native_request(
            "import os; [(os.write(1, b'x'*65536), os.write(2, b'y'*65536)) for _ in range(32)]"
        ),
        on_bytes=receive,
    )
    assert observation.error is None
    assert observation.returncode == 0
    assert observation.cleanup_confirmed
    assert totals == {"stdout": 32 * 65536, "stderr": 32 * 65536}


@windows_only
def test_native_arguments_are_literal_and_stdin_is_closed():
    arguments = ("a b", 'x"y', "&echo nope", "%PATH%", "", "trailing\\", "\u00e9")
    output = bytearray()
    req = ProcessRequest(
        (
            sys.executable,
            "-c",
            "import json, sys; assert sys.stdin.read() == ''; print(json.dumps(sys.argv[1:]))",
            *arguments,
        ),
        Path.cwd(),
        5,
    )
    observation = windows.supervise_process(
        req, on_bytes=lambda stream, chunk: output.extend(chunk)
    )
    assert observation.returncode == 0 and observation.cleanup_confirmed
    assert json.loads(output) == list(arguments)


@windows_only
def test_native_exit_code_259_is_not_misidentified_as_running():
    observation = windows.supervise_process(
        native_request("import sys; sys.exit(259)"),
        on_bytes=lambda *args: None,
    )
    assert observation.returncode == 259
    assert observation.cleanup_confirmed
    assert observation.stop_reason is None


@windows_only
def test_native_timeout_kills_owned_job():
    observation = windows.supervise_process(
        native_request("import time; time.sleep(60)", timeout=0.1),
        on_bytes=lambda *args: None,
        limits=ContractLimits(cleanup_seconds=2),
    )
    assert observation.stop_reason == "timeout"
    assert observation.cleanup_confirmed
    assert observation.elapsed_seconds < 4


@windows_only
def test_native_leader_exit_with_descendant_is_cleaned():
    observation = windows.supervise_process(
        native_request(
            "import subprocess, sys; subprocess.Popen("
            "[sys.executable, '-c', 'import time; time.sleep(60)'])"
        ),
        on_bytes=lambda *args: None,
        limits=ContractLimits(cleanup_seconds=2),
    )
    assert observation.returncode == 0
    assert observation.stop_reason == "lingering_children"
    assert observation.cleanup_confirmed
    assert observation.residual_group


@windows_only
@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
@pytest.mark.parametrize("callback", ["started", "bytes"])
def test_native_callback_failure_leaves_no_owned_processes(monkeypatch, failure, callback):
    jobs = []
    original = windows._JobProcess

    class ObservedJob(original):
        def close(self):
            if self.assigned:
                jobs.append((self.poll(), self.active_processes()))
            super().close()

    monkeypatch.setattr(windows, "_JobProcess", ObservedJob)

    def fail(*args):
        raise failure("stop")

    req = native_request(
        "import subprocess, sys, time; subprocess.Popen("
        "[sys.executable, '-c', 'import time; time.sleep(60)']); "
        "print('descendant started', flush=True); time.sleep(60)"
    )
    callbacks = {
        "on_bytes": fail if callback == "bytes" else lambda *args: None,
        "on_started": fail if callback == "started" else None,
    }
    if failure is RuntimeError:
        with pytest.raises(RuntimeError, match="stop"):
            windows.supervise_process(req, **callbacks)
    else:
        observation = windows.supervise_process(req, **callbacks)
        assert observation.stop_reason == "cancelled"
        assert observation.cleanup_confirmed
    assert len(jobs) == 1
    assert jobs[0][0] is not None and jobs[0][1] == 0
