import importlib.util
import os
import tomllib
from importlib.metadata import version
from pathlib import Path

from click.testing import CliRunner

from apmx.cli import main
from apmx.version import get_version


def test_no_experimental_activation_or_global_apm_state(tmp_path, monkeypatch):
    caller = tmp_path / "caller"
    home = tmp_path / "home"
    caller.mkdir()
    home.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("APM_CONFIG_DIR", str(home / "forbidden-apm-config"))
    monkeypatch.setattr("apmx.runtime.utils.find_runtime_binary", lambda _: os.sys.executable)
    (caller / "job.contract.md").write_text(
        "---\nproduces: out.txt\nverify: {ok: 'true'}\n---\nWrite output.\n"
    )
    result = CliRunner().invoke(main, ["job.contract.md", "--on", "copilot", "--plan"])
    assert result.exit_code == 0, result.output
    assert not list(home.iterdir())
    assert not (caller / ".apm").exists()
    assert "experimental" not in result.output


def test_namespace_entrypoints():
    assert importlib.util.find_spec("apmx") is not None
    assert CliRunner().invoke(main, ["--version"]).output.strip() == "apmx, version 0.4.1"


def test_release_version_matches_source_distribution_and_public_lock():
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    packages = [package for package in lock["package"] if package["name"] == "apmx"]
    assert len(packages) == 1 and packages[0]["source"] == {"editable": "."}
    assert project["version"] == get_version() == version("apmx") == packages[0]["version"]
