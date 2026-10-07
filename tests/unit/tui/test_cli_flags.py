"""CLI flag validation for --tui/--no-tui: the eligibility gate that keeps
the Textual prototype opt-in and --plan-only until milestone 2+ (see
docs/textual-design.md). These are usage-contract tests, not UI tests --
see tests/unit/tui/test_app.py for the widget itself.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from apmx.cli import main

pytestmark = pytest.mark.unit


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


def test_tui_without_plan_is_rejected_with_actionable_message(caller: Path) -> None:
    result = CliRunner().invoke(
        main, [str(caller / "target.contract.md"), "--on", "copilot", "--tui"]
    )
    assert result.exit_code != 0
    assert "--tui currently requires --plan" in result.output
    assert "docs/textual-design.md" in result.output


def test_tui_with_plan_and_no_tty_refuses_with_unproven_message(caller: Path) -> None:
    """Headless CliRunner invocation has no real TTY, so tui_eligible() must
    refuse rather than silently falling back to plain output."""
    result = CliRunner().invoke(
        main, [str(caller / "target.contract.md"), "--on", "copilot", "--plan", "--tui"]
    )
    assert result.exit_code != 0
    assert "UNPROVEN" in result.output


def test_no_tui_alone_runs_normally(caller: Path) -> None:
    result = CliRunner().invoke(
        main, [str(caller / "target.contract.md"), "--on", "copilot", "--plan", "--no-tui"]
    )
    assert result.exit_code == 0, result.output
    assert "Preview: target.contract.md -> out.txt" in result.output
