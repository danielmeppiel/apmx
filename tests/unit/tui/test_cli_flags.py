"""CLI flag validation for --tui/--no-tui: the eligibility gate around the
Textual app, now covering both the --plan-only preview and milestone-3 live
execution (see docs/textual-design.md). These are usage-contract tests, not
UI tests -- see tests/unit/tui/test_app.py for the widget itself.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from apmx.cli import main

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def private_preparation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep real governance discovery from reading this host's actual
    environment -- the same isolation tests/unit/contracts/test_chain_sources.py
    uses for any CLI invocation that reaches real preparation/governance."""
    scratch = tmp_path / "scratch"
    home = tmp_path / "home"
    scratch.mkdir()
    home.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    for key in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(key, str(scratch))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))


def _write_contract(root: Path, name: str, needs: tuple[str, ...], output: str) -> Path:
    command = f"{shlex.quote(sys.executable)} -B -c {shlex.quote('pass')}"
    path = root / name
    path.write_text(
        f"---\nneeds: {json.dumps(needs)}\nproduces: {output}\n"
        f"verify:\n  exact: {json.dumps(command)}\n---\nProduce {output} from the inputs.\n",
        encoding="ascii",
    )
    return path


@pytest.fixture
def caller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    tools = tmp_path / "tools"
    tools.mkdir()
    executable = tools / ("copilot.exe" if os.name == "nt" else "copilot")
    executable.write_bytes(b"Discovery only; never launched.\n")
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    root = tmp_path / "caller"
    root.mkdir()
    monkeypatch.chdir(root)
    (root / "seed.txt").write_bytes(b"seed")
    _write_contract(root, "target.contract.md", ("seed.txt",), "out.txt")
    return root


def test_tui_and_no_tui_together_is_rejected(caller: Path) -> None:
    result = CliRunner().invoke(
        main, [str(caller / "target.contract.md"), "--on", "copilot", "--plan", "--tui", "--no-tui"]
    )
    assert result.exit_code != 0
    assert "either --tui or --no-tui" in result.output


def test_tui_without_plan_on_single_file_requires_a_factory(caller: Path) -> None:
    """--tui no longer requires --plan (milestone 3: live execution), but it
    still needs a factory directory -- a bare contract file has no graph to
    present."""
    result = CliRunner().invoke(
        main,
        [
            str(caller / "target.contract.md"),
            "--on",
            "copilot",
            "--tui",
            "--allow-host-access",
        ],
    )
    assert result.exit_code != 0
    assert "needs a factory directory" in result.output


def test_tui_with_plan_and_no_tty_refuses_with_unproven_message(caller: Path) -> None:
    """Headless CliRunner invocation has no real TTY, so tui_eligible() must
    refuse rather than silently falling back to plain output."""
    result = CliRunner().invoke(
        main, [str(caller / "target.contract.md"), "--on", "copilot", "--plan", "--tui"]
    )
    assert result.exit_code != 0
    assert "UNPROVEN" in result.output


def test_tui_live_with_factory_and_no_tty_refuses_with_unproven_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The no-``--plan`` live path (milestone 3) must refuse exactly like the
    ``--plan`` preview path when there is no real TTY on both ends, instead of
    silently trying to launch Textual against a headless CliRunner process."""
    tools = tmp_path / "tools"
    tools.mkdir()
    executable = tools / ("copilot.exe" if os.name == "nt" else "copilot")
    executable.write_bytes(b"Discovery only; never launched.\n")
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    caller_root = tmp_path / "caller"
    caller_root.mkdir()
    (caller_root / "seed.txt").write_bytes(b"seed")
    monkeypatch.chdir(caller_root)
    factory = tmp_path / "factory"
    factory.mkdir()
    (factory / "apm.yml").write_text("name: tui-live\nversion: 1.0.0\n", encoding="ascii")
    _write_contract(factory, "target.contract.md", ("seed.txt",), "out.txt")
    result = CliRunner().invoke(
        main,
        [
            "--from",
            str(factory),
            "--on",
            "copilot",
            "--tui",
            "--allow-host-access",
            "--allow-unproven-inputs",
        ],
    )
    assert result.exit_code != 0
    assert "UNPROVEN" in result.output
    assert "needs an interactive terminal" in result.output


def test_no_tui_alone_runs_normally(caller: Path) -> None:
    result = CliRunner().invoke(
        main, [str(caller / "target.contract.md"), "--on", "copilot", "--plan", "--no-tui"]
    )
    assert result.exit_code == 0, result.output
    assert "Preview: target.contract.md -> out.txt" in result.output
