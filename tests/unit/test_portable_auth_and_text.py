from pathlib import Path
from types import SimpleNamespace

from apmx.contracts.stream import safe_text
from apmx.deps import git_auth_env


def test_windows_auth_config_does_not_use_global_apm_state(monkeypatch):
    monkeypatch.setattr(git_auth_env, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(git_auth_env, "_EMPTY_CONFIG_DIRECTORY", None)
    try:
        path = Path(git_auth_env.GitAuthEnvBuilder.isolated_global_config_path())
        assert path.read_bytes() == b""
        assert git_auth_env.GitAuthEnvBuilder.isolated_global_config_path() == str(path)
        assert path.parent.name.startswith("apmx-git-config-")
    finally:
        directory = git_auth_env._EMPTY_CONFIG_DIRECTORY
        if directory is not None:
            directory.cleanup()


def test_windows_paths_remain_copyable_after_repeated_safe_rendering():
    for path in (r"C:\Users\caller\saved output.json", r"\\host\share\saved.json"):
        assert safe_text(path) == path
        assert safe_text(safe_text(path)) == path
    assert "\x1b" not in safe_text("unsafe\x1b[31m")
