"""Short invocations reuse explicit source, workspace and consent admission."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from test_chain import caller, producer, two_nodes
from test_chain_sources import nested_factory, package, private_preparation

from apmx.cli import main
from apmx.commands.contracts import invoke_contract
from apmx.contracts.models import ContractError
from apmx.core.contract_logger import ContractLogger

__all__ = ["caller", "private_preparation"]
pytestmark = pytest.mark.component


@pytest.mark.parametrize("harness", [None, "copilot", "opencode"])
def test_package_root_and_copilot_are_explicit_defaults(
    caller: Path, monkeypatch: pytest.MonkeyPatch, harness: str | None
) -> None:
    root = package(caller)
    calls = producer(monkeypatch)
    result = CliRunner().invoke(
        main,
        ["--from", str(root), "--plan", *(["--on", harness] if harness else [])],
    )
    assert result.exit_code == 0, result.output
    assert "2 contracts / 2 artifacts / 2 planned checks" in result.output
    assert f"{harness or 'copilot'} / default model" in result.output
    assert calls == [] and not (caller / ".apm").exists()


@pytest.mark.parametrize(
    "answers,executions",
    [("n\n", 0), ("\n", 0), ("", 0), ("y\nn\n", 0), ("y\n", 0), ("y\ny\n", 2)],
)
def test_short_package_invocation_keeps_both_real_consent_gates(
    caller: Path, monkeypatch: pytest.MonkeyPatch, answers: str, executions: int
) -> None:
    root = package(caller)
    calls = producer(monkeypatch)
    monkeypatch.setattr(ContractLogger, "can_confirm_factory", lambda self: True)
    result = CliRunner().invoke(main, ["--from", str(root)], input=answers)
    assert result.exit_code == (0 if executions else 21), result.output
    assert len(calls) == executions
    assert "Load this factory and install its dependencies? [y/N]" in result.output
    if executions:
        assert all(plan.harness == "copilot" and plan.model is None for plan, *_ in calls)
        assert (caller / ".apm/chains").is_dir()
    else:
        assert not (caller / ".apm/chains").exists()


@pytest.mark.parametrize("planning", [False, True])
@pytest.mark.parametrize(
    "reference",
    [
        "example/factory#0123456789abcdef0123456789abcdef01234567",
        "https://github.com/example/factory.git#v1.0.0",
        "git@github.com:example/factory.git#v1.0.0",
    ],
)
def test_remote_package_shorthand_uses_the_existing_preparation_owner(
    caller: Path, monkeypatch: pytest.MonkeyPatch, planning: bool, reference: str
) -> None:
    prepare = Mock(side_effect=ContractError("Preparation owner reached.", code="test_stop"))
    monkeypatch.setattr("apmx.cli.prepare_contract_source", prepare)
    result = CliRunner().invoke(main, [reference, "--plan" if planning else "--allow-host-access"])
    assert result.exit_code == 22 and "Preparation owner reached." in result.output
    assert prepare.call_args.args == (reference, ".")
    assert prepare.call_args.kwargs["factory"] is True
    assert prepare.call_args.kwargs["caller_root"] == caller
    assert prepare.call_args.kwargs["planning"] is planning


def test_remote_shorthand_cannot_prepare_without_consent(
    caller: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare = Mock(side_effect=AssertionError("No host access was authorized"))
    monkeypatch.setattr("apmx.cli.prepare_contract_source", prepare)
    result = CliRunner().invoke(main, ["example/factory"])
    assert result.exit_code == 21, result.output
    prepare.assert_not_called()


@pytest.mark.parametrize(
    "selection",
    [
        [],
        ["missing"],
        ["./missing"],
        ["../missing/factory"],
        [r"..\missing\factory"],
        ["~/missing"],
    ],
)
def test_missing_selection_or_local_path_never_becomes_a_remote_fetch(
    caller: Path, monkeypatch: pytest.MonkeyPatch, selection: list[str]
) -> None:
    prepare = Mock(side_effect=AssertionError("Invalid selection must not acquire"))
    monkeypatch.setattr("apmx.cli.prepare_contract_source", prepare)
    result = CliRunner().invoke(main, [*selection, "--plan"])
    assert result.exit_code == 2, result.output
    prepare.assert_not_called()
    assert not (caller / ".apm").exists()


def test_existing_directory_named_like_a_package_keeps_local_root_semantics(
    caller: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = caller / "example/factory"
    root.mkdir(parents=True)
    (root / "seed.txt").write_bytes(b"local")
    two_nodes(root)
    prepare = Mock(side_effect=AssertionError("An existing directory is not a remote package"))
    invoke = Mock(wraps=invoke_contract)
    monkeypatch.setattr("apmx.cli.prepare_contract_source", prepare)
    monkeypatch.setattr("apmx.cli.invoke_contract", invoke)
    result = CliRunner().invoke(main, ["example/factory", "--plan"])
    assert result.exit_code == 0, result.output
    assert invoke.call_args.kwargs["factory_root"] == root
    assert invoke.call_args.kwargs["harness"] == "copilot"
    assert invoke.call_args.kwargs["model"] is None
    prepare.assert_not_called()
    assert not (caller / ".apm").exists()


def test_existing_non_contract_file_is_not_reinterpreted_as_a_package(
    caller: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (caller / "example").mkdir()
    (caller / "example/README.md").write_text("Not a factory.", encoding="ascii")
    prepare = Mock(side_effect=AssertionError("Local files must not trigger package acquisition"))
    monkeypatch.setattr("apmx.cli.prepare_contract_source", prepare)
    result = CliRunner().invoke(main, ["example/README.md", "--plan"])
    assert result.exit_code == 2 and "selected local entry" in result.output
    prepare.assert_not_called()


@pytest.mark.parametrize("selection", [["example/factory"], ["--from", "../package"]])
def test_shorthand_preserves_caller_policy_before_preparation(
    caller: Path, monkeypatch: pytest.MonkeyPatch, selection: list[str]
) -> None:
    monkeypatch.setenv("APM_NO_SCRIPTS", "1")
    prepare = Mock(side_effect=AssertionError("Policy must precede preparation"))
    monkeypatch.setattr("apmx.cli.prepare_contract_source", prepare)
    result = CliRunner().invoke(main, [*selection, "--plan"])
    assert result.exit_code == 21 and "APM_NO_SCRIPTS" in result.output
    prepare.assert_not_called()


def test_root_default_does_not_guess_a_valid_nested_factory(
    caller: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = nested_factory(caller)
    calls = producer(monkeypatch)
    result = CliRunner().invoke(main, ["--from", str(root), "--plan"])
    assert result.exit_code == 22, result.output
    assert calls == [] and not (caller / ".apm").exists()


def test_explicit_empty_package_entry_is_not_an_omitted_entry(
    caller: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = package(caller)
    calls = producer(monkeypatch)
    result = CliRunner().invoke(main, ["--from", str(root), "", "--plan"])
    assert result.exit_code == 22, result.output
    assert calls == [] and not (caller / ".apm").exists()


def test_explicit_leaf_accepts_default_harness_without_becoming_a_factory(
    caller: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    two_nodes(caller)
    invoke = Mock(wraps=invoke_contract)
    monkeypatch.setattr("apmx.cli.invoke_contract", invoke)
    result = CliRunner().invoke(main, ["z-first.contract.md", "--plan"])
    assert result.exit_code == 0, result.output
    assert invoke.call_args.kwargs["factory_root"] is None
    assert invoke.call_args.kwargs["harness"] == "copilot"


def test_help_explains_default_harness_and_package_entry() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "[default: copilot]" in " ".join(result.output.split())
    assert "Usage: apmx [OPTIONS] [FACTORY_OR_CONTRACT_OR_PACKAGE]" in result.output
    assert "package root" in result.output and "--from" in result.output
