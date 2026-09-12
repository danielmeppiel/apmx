import importlib.util
import os

from click.testing import CliRunner

from apmx.cli import main


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
    assert CliRunner().invoke(main, ["--version"]).output.strip() == "apmx, version 0.1.0"
