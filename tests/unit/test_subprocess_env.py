from unittest.mock import Mock
import sys
from types import SimpleNamespace
import ctypes

from apmx.contracts.models import ProcessRequest
from apmx.contracts.process import supervise_process
from apmx.utils.git_env import git_subprocess_env
from apmx.utils import subprocess_env


def test_process_request_repr_does_not_disclose_environment(tmp_path):
    request = ProcessRequest(("git", "fetch"), tmp_path, 5, {"SECRET": "private-value"})
    assert "private-value" not in repr(request)


def test_frozen_child_restores_original_loader_paths(monkeypatch):
    monkeypatch.setattr(subprocess_env.sys, "frozen", True, raising=False)
    env = {
        "LD_LIBRARY_PATH": "/bundle",
        "LD_LIBRARY_PATH_ORIG": "/user-libs",
        "DYLD_LIBRARY_PATH": "/bundle",
        "KEEP": "ordinary",
    }
    assert subprocess_env.external_process_env(env) == {
        "LD_LIBRARY_PATH": "/user-libs", "KEEP": "ordinary",
    }
    assert env["LD_LIBRARY_PATH"] == "/bundle"


def test_external_auth_probe_uses_loader_helper(monkeypatch):
    monkeypatch.setattr(subprocess_env.sys, "frozen", True, raising=False)
    run = Mock()
    monkeypatch.setattr(subprocess_env.subprocess, "run", run)
    subprocess_env.run_external(
        ["gh", "auth", "token"], env={"LD_LIBRARY_PATH": "/bundle", "SAFE": "value"}, timeout=5,
    )
    assert run.call_args.kwargs["env"] == {"SAFE": "value"}
    assert run.call_args.kwargs["timeout"] == 5


def test_repeated_git_env_preparation_restores_loader_once(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess_env.sys, "frozen", True, raising=False)
    monkeypatch.setattr(subprocess_env.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/user-libs")
    env = git_subprocess_env(git_subprocess_env())
    output = bytearray()
    with subprocess_env.external_dll_search():
        result = supervise_process(
            ProcessRequest(
                (sys.executable, "-c", "import os; print(os.environ['LD_LIBRARY_PATH'])"),
                tmp_path, 10, env,
            ),
            on_bytes=lambda stream, chunk: output.extend(chunk) if stream == "stdout" else None,
        )
    assert result.returncode == 0
    assert result.cleanup_confirmed
    assert bytes(output).strip() == b"/user-libs"


def test_empty_windows_dll_directory_is_not_a_failed_query(monkeypatch):
    monkeypatch.setattr(subprocess_env, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(subprocess_env.sys, "frozen", True, raising=False)
    kernel = SimpleNamespace(
        GetDllDirectoryW=Mock(side_effect=[1, 0]),
        SetDllDirectoryW=Mock(return_value=True),
    )
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=kernel), raising=False)
    monkeypatch.setattr(ctypes, "set_last_error", Mock(), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", Mock(return_value=0), raising=False)
    with subprocess_env.external_dll_search():
        pass
    assert kernel.SetDllDirectoryW.call_count == 2
    assert all(call.args == (None,) for call in kernel.SetDllDirectoryW.call_args_list)
