"""Same-source package graphs through genuine APM preparation; no live inference."""

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from test_chain import caller, producer, two_nodes

from apmx.cli import main
from apmx.contracts import chain, engine, resolution
from apmx.contracts.models import ContractError, ContractLimits
from apmx.core.contract_logger import ContractLogger
from apmx.install import apm_backend, contract_source

__all__ = ["caller"]

pytestmark = pytest.mark.component


@pytest.fixture(autouse=True)
def private_preparation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def package(caller: Path) -> Path:
    root = caller.parent / "package"
    root.mkdir()
    (root / "apm.yml").write_text("name: chain-source\nversion: 1.0.0\n", encoding="ascii")
    (root / "seed.txt").write_bytes(b"package input must not win")
    two_nodes(root)
    return root


@contextmanager
def prepared(root: Path, caller: Path, *, planning: bool = False, allow: bool = True):
    """Retain prepared-source backend coverage without a public package-factory API."""
    limits = ContractLimits()
    with contract_source.prepare_contract_source(
        str(root),
        "a-target.contract.md",
        caller_root=caller,
        planning=planning,
        limits=limits,
    ) as source:
        graph = resolution.resolve(Path("a-target.contract.md"), caller, source=source)
        with contract_source.prepare_imports(
            caller,
            source.root / source.contract_relative_path,
            source=source,
            planning=planning,
            limits=limits,
        ) as (imports_root, backend):
            yield resolution.preflight(
                graph,
                caller,
                harness="copilot",
                source=source,
                imports_root=imports_root,
                apm_backend=backend,
                allow_unproven_inputs=allow,
            )


def test_package_preview_is_same_source_and_offline(
    caller: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = package(caller)
    install = Mock(side_effect=AssertionError("offline must not invoke APM"))
    monkeypatch.setattr(apm_backend, "install", install)
    calls = producer(monkeypatch)
    before = {
        p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    with prepared(root, caller, planning=True) as plan:
        assert len(plan.nodes) == 2 and plan.nodes[1].plan.deferred_inputs == ("first.txt",)
    assert calls == [] and not (caller / ".apm").exists()
    assert before == {
        p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    install.assert_not_called()
    unknown = CliRunner().invoke(
        main,
        [
            "a-target.contract.md",
            "--from",
            "example/uninstalled",
            "--on",
            "copilot",
            "--plan",
        ],
    )
    assert unknown.exit_code == 21 and "unresolved offline" in unknown.output
    install.assert_not_called()


@pytest.mark.parametrize("allow", (False, True))
def test_real_prepared_package_chain_keeps_original_caller(
    caller: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow: bool,
) -> None:
    root = package(caller)
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    calls = producer(monkeypatch)
    with prepared(root, caller, allow=allow) as plan:
        result = chain.run_chain(plan, logger=ContractLogger(), allow_advisory=True)
    assert int(result.outcome) == 21 and len(calls) == (2 if allow else 1)
    assert (
        json.loads(next((caller / ".apm/chains").glob("*/record.json")).read_bytes())["complete"]
        is allow
    )
    for plan, snapshot, directory in calls:
        assert plan.project_root == caller and plan.source.original_root == root
        assert not plan.source.root.exists()
        assert (directory / "source/contract.contract.md").is_file()
        if plan.contract.produces == "first.txt":
            assert (snapshot.root / "seed.txt").read_bytes() == b"seed"
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}
    assert (caller / "seed.txt").read_bytes() == b"seed"
    assert not (caller / "first.txt").exists() and not (caller / "last.txt").exists()


@pytest.mark.parametrize("mutation", ("original-source", "caller-policy"))
def test_prepared_graph_does_not_erase_mutation_or_original_policy(
    caller: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    root = package(caller)
    calls = producer(monkeypatch)
    original = engine.run_contract

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if mutation == "original-source":
            (root / "a-target.contract.md").write_bytes(b"changed original")
        else:
            monkeypatch.setenv("APM_NO_SCRIPTS", "1")
        return result

    monkeypatch.setattr(engine, "run_contract", changed)
    with prepared(root, caller) as plan:
        result = chain.run_chain(plan, logger=ContractLogger(), allow_advisory=True)
    assert int(result.outcome) in (21, 22) and len(calls) == 1
    data = json.loads(next((caller / ".apm/chains").glob("*/record.json")).read_bytes())
    assert data["complete"] is False and data["nodes"][1]["state"] == "blocked"
    if mutation == "caller-policy":
        assert os.environ["APM_NO_SCRIPTS"] == "1"


@pytest.mark.parametrize("persistence_failure", (False, True))
def test_import_preparation_cleanup_failure_marks_aggregate_incomplete(
    caller: Path,
    monkeypatch: pytest.MonkeyPatch,
    persistence_failure: bool,
) -> None:
    two_nodes(caller)
    calls = producer(monkeypatch)
    original = contract_source.prepare_imports

    @contextmanager
    def failed_cleanup(*args, **kwargs):
        with original(*args, **kwargs) as source:
            yield source
        raise ContractError("Source cleanup failed.", code="source_cleanup")

    monkeypatch.setattr(contract_source, "prepare_imports", failed_cleanup)
    if persistence_failure:
        from apmx.contracts import records

        monkeypatch.setattr(records, "halt_chain", Mock(side_effect=OSError("storage unavailable")))
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
    assert result.exit_code == 22 and len(calls) == 2, result.output
    assert "apmx factory: UNPROVEN (complete)" not in result.output
    data = json.loads(next((caller / ".apm/chains").glob("*/record.json")).read_bytes())
    if persistence_failure:
        assert "persistence is unconfirmed" in result.output
    else:
        assert data["complete"] is False
        assert data["result"]["stop_reason"] == "source_cleanup"


@pytest.mark.parametrize("interactive", (False, True))
@pytest.mark.parametrize("drift", (False, True))
def test_upstream_imports_are_prepared_once_before_any_model(
    caller: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: bool,
    interactive: bool,
) -> None:
    two_nodes(caller)
    style = caller.parent / "style"
    style.mkdir()
    (style / "apm.yml").write_text("name: style\nversion: 1.0.0\n", encoding="ascii")
    (style / "SKILL.md").write_text(
        "---\nname: style\ndescription: Test style\n---\nWrite concise output.\n",
        encoding="ascii",
    )
    (caller / "apm.yml").write_text(
        "name: consumer\nversion: 1.0.0\ndependencies:\n  apm: [../style]\n",
        encoding="ascii",
    )
    contract = caller / "z-first.contract.md"
    contract.write_text(contract.read_text().replace("verify:\n", "imports: [style]\nverify:\n"))
    confirmed = False
    original_confirm = ContractLogger.confirm_factory
    original_install = apm_backend.install

    def confirm(logger):
        nonlocal confirmed
        confirmed = original_confirm(logger)
        return confirmed

    def install_after_consent(*args, **kwargs):
        assert confirmed if interactive else not confirmed
        return original_install(*args, **kwargs)

    monkeypatch.setattr(ContractLogger, "can_confirm_factory", lambda self: interactive)
    monkeypatch.setattr(ContractLogger, "confirm_factory", confirm)
    install = Mock(side_effect=install_after_consent)
    monkeypatch.setattr(apm_backend, "install", install)
    calls = producer(monkeypatch)
    original = engine.run_contract

    def executed(plan, **kwargs):
        assert install.call_count == 1
        result = original(plan, **kwargs)
        if drift and plan.imported_skills:
            skill = plan.imported_skills[0].source_path
            skill.chmod(0o600)
            skill.write_bytes(b"changed prepared context")
        return result

    monkeypatch.setattr(engine, "run_contract", executed)
    manifest = (caller / "apm.yml").read_bytes()
    monkeypatch.chdir(caller.parent)
    result = CliRunner().invoke(
        main,
        [
            str(caller),
            "--on",
            "copilot",
            *([] if interactive else ["--allow-host-access", "--allow-unproven-inputs"]),
        ],
        input="y\n",
    )
    assert len(calls) == (1 if drift else 2), result.output
    assert result.exit_code == (22 if drift else 21), result.output
    assert calls[0][0].imported_skills[0].name == "style"
    assert (caller / "apm.yml").read_bytes() == manifest
    assert not (caller / "apm.lock.yaml").exists()
    data = json.loads(next((caller / ".apm/chains").glob("*/record.json")).read_bytes())
    assert data["complete"] is not drift
    assert data["consent_source"] == ("interactive" if interactive else "flag")


def test_package_contract_remains_a_single_leaf(caller, monkeypatch):
    root = package(caller)
    calls = producer(monkeypatch)
    confirm = Mock(side_effect=AssertionError("packaged leaf cannot prompt"))
    monkeypatch.setattr(ContractLogger, "can_confirm_factory", confirm)
    result = CliRunner().invoke(
        main,
        [
            "z-first.contract.md",
            "--from",
            str(root),
            "--on",
            "copilot",
            "--allow-host-access",
        ],
    )
    assert result.exit_code == 21 and len(calls) == 1, result.output
    assert calls[0][0].source.original_root == root
    assert (calls[0][1].root / "seed.txt").read_bytes() == b"seed"
    assert not (caller / ".apm/chains").exists()
    confirm.assert_not_called()
