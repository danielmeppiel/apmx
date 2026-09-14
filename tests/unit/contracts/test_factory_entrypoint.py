"""Directory selection and explicit, invocation-local factory consent."""

import io
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from test_chain import caller, producer, two_nodes, write_contract

from apmx.cli import main
from apmx.contracts import resolution
from apmx.contracts.models import ContractError
from apmx.core.contract_logger import ContractLogger
from apmx.install import contract_source

__all__ = ["caller"]

pytestmark = pytest.mark.component


def document(root: Path) -> dict:
    return json.loads(next((root / ".apm/chains").glob("*/record.json")).read_bytes())


def assert_consent(root: Path, source: str, allow: bool) -> None:
    aggregate = document(root)
    policy = "native-assurance-exception" if allow else "VERIFIED-only"
    assert aggregate["consent_source"] == source
    assert aggregate["handoff_policy"] == policy
    assert aggregate["allow_unproven_inputs"] is allow
    assert aggregate["allow_host_access"] is True
    for path in (root / ".apm/runs").glob("*/record.json"):
        leaf = json.loads(path.read_bytes())
        assert leaf["advisory_consent"] == source
        assert leaf["handoff_policy"] == policy
        assert leaf["result"]["consent_source"] == source
        assert leaf["result"]["handoff_policy"] == policy


def test_whole_catalog_multiple_sinks_share_one_predecessor(caller, monkeypatch):
    two_nodes(caller)
    write_contract(caller, "other.contract.md", ("first.txt",), "other.txt")
    calls = producer(monkeypatch)
    result = CliRunner().invoke(
        main,
        [
            str(caller),
            "--on",
            "copilot",
            "--allow-host-access",
            "--allow-unproven-inputs",
        ],
    )
    assert result.exit_code == 21 and len(calls) == 3, result.output
    assert document(caller)["graph"]["targets"] == ["a-target.contract.md", "other.contract.md"]
    assert [p.contract.produces for p, *_ in calls] == ["first.txt", "last.txt", "other.txt"]
    write_contract(caller, "a-target.contract.md", ("seed.txt",), "last.txt")
    graph = resolution.resolve_factory(caller)
    assert [c.produces for c in graph.order] == ["last.txt", "first.txt", "other.txt"]
    assert len(graph.targets) == 2


@pytest.mark.parametrize("fault", ("empty", "cycle", "disconnected-cycle", "missing", "ambiguous"))
def test_invalid_factory_catalog_refuses_before_confirmation(caller, monkeypatch, fault):
    if fault != "empty":
        two_nodes(caller)
    if fault in ("cycle", "disconnected-cycle"):
        if fault == "cycle":
            write_contract(caller, "z-first.contract.md", ("last.txt",), "first.txt")
        else:
            write_contract(caller, "loop-one.contract.md", ("loop-two.txt",), "loop-one.txt")
            write_contract(caller, "loop-two.contract.md", ("loop-one.txt",), "loop-two.txt")
    elif fault == "missing":
        write_contract(caller, "unused.contract.md", ("missing.txt",), "unused.txt")
    elif fault == "ambiguous":
        write_contract(caller, "other.contract.md", ("seed.txt",), "first.txt")
    calls = producer(monkeypatch)
    confirm = Mock(side_effect=AssertionError("invalid graph must not prompt"))
    install = Mock(side_effect=AssertionError("invalid graph must not prepare"))
    monkeypatch.setattr(ContractLogger, "confirm_factory", confirm)
    monkeypatch.setattr(contract_source, "prepare_imports", install)
    result = CliRunner().invoke(main, [str(caller), "--on", "copilot"])
    assert result.exit_code == 22, result.output
    assert calls == [] and not (caller / ".apm").exists()
    confirm.assert_not_called()
    install.assert_not_called()


def test_selected_directory_is_actual_root_without_global_chdir(caller, monkeypatch):
    root = caller / "factory 'with spaces'"
    root.mkdir()
    (root / "seed.txt").write_bytes(b"FACTORY INPUT")
    two_nodes(root)
    (caller / "apm.yml").write_text("name: unrelated\nversion: 1.0.0\npolicy: {}\n")
    calls = producer(monkeypatch)
    chdir = Mock(side_effect=AssertionError("factory dispatch must not chdir"))
    monkeypatch.setattr(os, "chdir", chdir)
    result = CliRunner().invoke(
        main,
        [
            str(root),
            "--on",
            "copilot",
            "--allow-host-access",
            "--allow-unproven-inputs",
        ],
    )
    assert result.exit_code == 21 and len(calls) == 2, result.output
    assert Path.cwd() == caller and not (caller / ".apm").exists()
    assert document(root)["caller_root"] == str(root)
    assert all(plan.project_root == root for plan, *_ in calls)
    assert (calls[0][1].root / "seed.txt").read_bytes() == b"FACTORY INPUT"
    aggregate = document(root)
    assert (
        "Factory record: " + Path(aggregate["result"]["record_path"]).relative_to(caller).as_posix()
    ) in result.output
    assert (
        "Artifacts: " + Path(aggregate["artifacts"]["root"]).relative_to(caller).as_posix()
        in result.output
    )
    chdir.assert_not_called()


def test_selected_root_policy_precedes_confirmation_and_preparation(caller, monkeypatch):
    two_nodes(caller)
    (caller / "apm.yml").write_text("name: governed\nversion: 1.0.0\npolicy: {}\n")
    monkeypatch.chdir(caller.parent)
    calls = producer(monkeypatch)
    confirm = Mock(side_effect=AssertionError("governed root must not prompt"))
    install = Mock(side_effect=AssertionError("governed root must not prepare"))
    monkeypatch.setattr(ContractLogger, "can_confirm_factory", lambda self: True)
    monkeypatch.setattr(ContractLogger, "confirm_factory", confirm)
    monkeypatch.setattr(contract_source, "prepare_imports", install)
    result = CliRunner().invoke(main, [str(caller), "--on", "copilot"])
    assert result.exit_code == 21 and "policy" in result.output, result.output
    assert calls == [] and not (caller / ".apm").exists()
    confirm.assert_not_called()
    install.assert_not_called()


@pytest.mark.parametrize("answer", ("y\n", "Y\n", "yes\n"))
def test_interactive_yes_records_both_permissions_before_preparation(caller, monkeypatch, answer):
    two_nodes(caller)
    calls = producer(monkeypatch)
    monkeypatch.setattr(ContractLogger, "can_confirm_factory", lambda self: True)
    original_confirm = ContractLogger.confirm_factory
    original_prepare = contract_source.prepare_imports
    confirmed = False

    def confirm(logger):
        nonlocal confirmed
        confirmed = original_confirm(logger)
        return confirmed

    @contextmanager
    def prepare(*args, **kwargs):
        assert confirmed and calls == []
        with original_prepare(*args, **kwargs) as value:
            yield value

    monkeypatch.setattr(ContractLogger, "confirm_factory", confirm)
    monkeypatch.setattr(contract_source, "prepare_imports", prepare)
    result = CliRunner().invoke(main, [str(caller), "--on", "copilot"], input=answer)
    assert result.exit_code == 21 and len(calls) == 2, result.output
    assert result.output.count("Run this factory locally? [y/N]") == 1
    for notice in (
        "host files, network and available logins",
        "required checks all passed",
        "not isolated",
        "remain UNPROVEN",
        "may incur charges",
    ):
        assert notice in result.output
    assert document(caller)["complete"] is True
    assert_consent(caller, "interactive", True)


@pytest.mark.parametrize("answer", ("", "\n", "n\n", "no\n", "maybe\n", "y", "y; execute\n"))
def test_decline_eof_and_incomplete_input_never_prepare_or_run(caller, monkeypatch, answer):
    two_nodes(caller)
    calls = producer(monkeypatch)
    monkeypatch.setattr(ContractLogger, "can_confirm_factory", lambda self: True)
    prepare = Mock(side_effect=AssertionError("declined factory must not prepare"))
    monkeypatch.setattr(contract_source, "prepare_imports", prepare)
    result = CliRunner().invoke(main, [str(caller), "--on", "copilot"], input=answer)
    assert result.exit_code == 21, result.output
    assert result.output.count("Run this factory locally? [y/N]") == 1
    assert calls == [] and not (caller / ".apm").exists()
    prepare.assert_not_called()


def test_interrupted_confirmation_is_structured_halted(caller, monkeypatch):
    two_nodes(caller)
    calls = producer(monkeypatch)
    original = ContractLogger.confirm_factory
    monkeypatch.setattr(ContractLogger, "can_confirm_factory", lambda self: True)
    prepare = Mock(side_effect=AssertionError("interrupted factory must not prepare"))
    monkeypatch.setattr(contract_source, "prepare_imports", prepare)

    def interrupted(logger):
        monkeypatch.setattr(sys.stdin, "readline", Mock(side_effect=KeyboardInterrupt()))
        return original(logger)

    monkeypatch.setattr(ContractLogger, "confirm_factory", interrupted)
    result = CliRunner().invoke(main, [str(caller), "--on", "copilot"])
    assert result.exit_code == 22 and "interrupted" in result.output
    assert calls == [] and not (caller / ".apm").exists()
    prepare.assert_not_called()


@pytest.mark.parametrize("fault", ("hidden-prompt", "eof-error"))
def test_unobservable_or_closed_confirmation_cannot_authorize(caller, monkeypatch, fault):
    two_nodes(caller)
    calls = producer(monkeypatch)
    original = ContractLogger.confirm_factory
    monkeypatch.setattr(ContractLogger, "can_confirm_factory", lambda self: True)

    def unavailable(logger):
        if fault == "hidden-prompt":
            logger._display.disable()
            monkeypatch.setattr(sys.stdin, "readline", Mock(side_effect=AssertionError("no read")))
        else:
            monkeypatch.setattr(sys.stdin, "readline", Mock(side_effect=EOFError()))
        return original(logger)

    monkeypatch.setattr(ContractLogger, "confirm_factory", unavailable)
    result = CliRunner().invoke(main, [str(caller), "--on", "copilot"], input="y\n")
    assert result.exit_code == 21 and calls == [] and not (caller / ".apm").exists()


@pytest.mark.parametrize("stdin_tty", (False, True))
@pytest.mark.parametrize("stdout_tty", (False, True))
@pytest.mark.parametrize("ci", (False, True))
def test_prompt_requires_both_terminal_streams_and_not_ci(
    monkeypatch,
    stdin_tty,
    stdout_tty,
    ci,
):
    class Stream(io.StringIO):
        def __init__(self, terminal):
            super().__init__()
            self.terminal = terminal

        def isatty(self):
            return self.terminal

    monkeypatch.setenv("CI", "true" if ci else "")
    logger = ContractLogger()
    monkeypatch.setattr(sys, "stdin", Stream(stdin_tty))
    monkeypatch.setattr(sys, "stdout", Stream(stdout_tty))
    assert logger.can_confirm_factory() is (stdin_tty and stdout_tty and not ci)


def test_non_terminal_factory_refuses_without_reading_input(caller, monkeypatch):
    two_nodes(caller)
    calls = producer(monkeypatch)
    confirm = Mock(side_effect=AssertionError("pipelines must not prompt"))
    monkeypatch.setattr(ContractLogger, "confirm_factory", confirm)
    result = CliRunner().invoke(main, [str(caller), "--on", "copilot"], input="y\n")
    assert result.exit_code == 21 and "--allow-host-access" in result.output
    assert calls == [] and not (caller / ".apm").exists()
    confirm.assert_not_called()


@pytest.mark.parametrize(
    "host,unproven,count", ((True, True, 2), (True, False, 1), (False, True, 0))
)
def test_explicit_flags_never_prompt_or_imply_other_permission(
    caller, monkeypatch, host, unproven, count
):
    two_nodes(caller)
    calls = producer(monkeypatch)
    monkeypatch.setattr(ContractLogger, "can_confirm_factory", lambda self: True)
    confirm = Mock(side_effect=AssertionError("flagged invocations must not prompt"))
    monkeypatch.setattr(ContractLogger, "confirm_factory", confirm)
    result = CliRunner().invoke(
        main,
        [
            str(caller),
            "--on",
            "copilot",
            *(["--allow-host-access"] if host else []),
            *(["--allow-unproven-inputs"] if unproven else []),
        ],
    )
    assert result.exit_code == 21 and len(calls) == count, result.output
    confirm.assert_not_called()
    if host:
        assert_consent(caller, "flag", unproven)
        assert document(caller)["complete"] is unproven
    else:
        assert not (caller / ".apm").exists()


def test_preview_never_prompts_and_changes_no_input(caller, monkeypatch):
    two_nodes(caller)
    calls = producer(monkeypatch)
    before = {p.name: p.read_bytes() for p in caller.iterdir()}
    can_confirm = Mock(side_effect=AssertionError("preview must not even consider confirmation"))
    monkeypatch.setattr(ContractLogger, "can_confirm_factory", can_confirm)
    result = CliRunner().invoke(main, [str(caller), "--on", "copilot", "--plan"])
    assert result.exit_code == 0 and "Factory preview: 2 contracts" in result.output
    assert calls == [] and before == {p.name: p.read_bytes() for p in caller.iterdir()}
    can_confirm.assert_not_called()


def test_contract_changed_during_confirmation_stops_before_preparation(caller, monkeypatch):
    two_nodes(caller)
    calls = producer(monkeypatch)
    monkeypatch.setattr(ContractLogger, "can_confirm_factory", lambda self: True)

    def changed(logger):
        write_contract(caller, "other.contract.md", ("seed.txt",), "other.txt")
        return True

    prepare = Mock(side_effect=AssertionError("changed graph must not prepare"))
    monkeypatch.setattr(ContractLogger, "confirm_factory", changed)
    monkeypatch.setattr(contract_source, "prepare_imports", prepare)
    result = CliRunner().invoke(main, [str(caller), "--on", "copilot"])
    assert result.exit_code == 22 and "changed during confirmation" in result.output
    assert calls == [] and not (caller / ".apm").exists()
    prepare.assert_not_called()


def test_explicit_leaf_does_not_run_siblings_or_prompt(caller, monkeypatch):
    two_nodes(caller)
    calls = producer(monkeypatch)
    can_confirm = Mock(side_effect=AssertionError("leaf invocation must not prompt"))
    monkeypatch.setattr(ContractLogger, "can_confirm_factory", can_confirm)
    refused = CliRunner().invoke(main, ["z-first.contract.md", "--on", "copilot"])
    assert refused.exit_code == 21 and calls == []
    result = CliRunner().invoke(
        main,
        [
            "z-first.contract.md",
            "--on",
            "copilot",
            "--allow-host-access",
        ],
    )
    assert result.exit_code == 21 and len(calls) == 1, result.output
    assert not (caller / ".apm/chains").exists()
    leaf = json.loads(next((caller / ".apm/runs").glob("*/record.json")).read_bytes())
    assert leaf["advisory_consent"] == "flag" and leaf["handoff_policy"] is None
    can_confirm.assert_not_called()


def test_rejected_chain_option_and_package_factory_have_no_fallback(caller):
    two_nodes(caller)
    help_result = CliRunner().invoke(main, ["--help"])
    assert "--chain" not in help_result.output and "FACTORY_OR_CONTRACT" in help_result.output
    rejected = CliRunner().invoke(main, [str(caller), "--on", "copilot", "--chain"])
    assert rejected.exit_code == 2
    assert "No such option" in rejected.output and "--chain" in rejected.output
    packaged = CliRunner().invoke(main, [".", "--from", str(caller), "--on", "copilot", "--plan"])
    assert packaged.exit_code == 2 and "leaf contracts only" in packaged.output
    assert not (caller / ".apm").exists()


def test_factory_root_symlink_is_not_followed(caller):
    two_nodes(caller)
    link = caller.parent / "alias"
    link.symlink_to(caller, target_is_directory=True)
    with pytest.raises(ContractError, match="without symlinks"):
        resolution.resolve_factory(link)
    result = CliRunner().invoke(main, [str(link), "--on", "copilot", "--plan"])
    assert result.exit_code == 22 and not (caller / ".apm").exists()
