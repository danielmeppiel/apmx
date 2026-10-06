"""Implicit project selection uses local Git ignore semantics, not a file allowlist."""

import hashlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from apmx.contracts import workspace
from apmx.contracts.models import ContractError, LeafContract, LeafPlan


def plan_at(root: Path) -> LeafPlan:
    contract = root / "build.contract.md"
    contract.write_text("---\nneeds: [request.md]\nproduces: changes.diff\n---\nBuild.\n")
    (root / "request.md").write_text("Change the behavior.\n")
    return LeafPlan(
        LeafContract(
            contract,
            hashlib.sha256(contract.read_bytes()).hexdigest(),
            "Build.",
            ("request.md",),
            "changes.diff",
            (),
        ),
        root,
        Path(sys.executable),
    )


@pytest.mark.parametrize("git", [False, True])
def test_new_source_is_implicit_and_ignored_files_stay_out(tmp_path, git):
    plan = plan_at(tmp_path)
    if git:
        workspace.local_git(tmp_path, "init", "--quiet")
        workspace.local_git(tmp_path, "add", "--", "request.md")
    (tmp_path / ".gitignore").write_text("*.secret\ncache/\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/added.py").write_bytes(b"VALUE = 1\r\n")
    (tmp_path / "src/local.secret").write_text("not captured")
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache/artifact").write_text("not captured")
    names = {entry.relative_path for entry in workspace.inspect_workspace(plan)}
    assert "src/added.py" in names
    assert "src/local.secret" not in names
    assert "cache/artifact" not in names
    assert plan.contract.needs == ("request.md",)
    assert (tmp_path / ".git").exists() is git


@pytest.mark.parametrize("git", [False, True])
def test_nested_ignore_negation_and_project_only_scope(tmp_path, git):
    plan = plan_at(tmp_path)
    if git:
        workspace.local_git(tmp_path, "init", "--quiet")
        excludes = tmp_path / ".git/local-excludes"
        excludes.write_text("visible.py\n")
        workspace.local_git(tmp_path, "config", "core.excludesFile", str(excludes))
    (tmp_path / ".gitignore").write_text("*.tmp\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/.gitignore").write_text("!keep.tmp\n")
    for name in ("visible.py", "src/keep.tmp", "src/drop.tmp"):
        (tmp_path / name).write_text("contents")
    names = {entry.relative_path for entry in workspace.inspect_workspace(plan)}
    assert {"visible.py", "src/keep.tmp"} <= names
    assert "src/drop.tmp" not in names


def test_tracked_modification_survives_ignore_and_deletion_does_not(tmp_path):
    plan = plan_at(tmp_path)
    workspace.local_git(tmp_path, "init", "--quiet")
    (tmp_path / "retained.py").write_text("old")
    (tmp_path / "deleted.py").write_text("old")
    workspace.local_git(tmp_path, "add", "--", "retained.py", "deleted.py")
    (tmp_path / ".gitignore").write_text("*.py\n")
    (tmp_path / "retained.py").write_text("modified")
    (tmp_path / "deleted.py").unlink()
    entries = {entry.relative_path: entry for entry in workspace.inspect_workspace(plan)}
    assert entries["retained.py"].sha256 == hashlib.sha256(b"modified").hexdigest()
    assert "deleted.py" not in entries


@pytest.mark.parametrize("git", [False, True])
def test_managed_state_dependencies_and_native_skills_are_never_implicit(tmp_path, git):
    plan = plan_at(tmp_path)
    if git:
        workspace.local_git(tmp_path, "init", "--quiet")
    excluded = (
        ".apm/runs/old/record.json",
        "apm_modules/dep/data",
        ".venv/lib/data",
        "node_modules/dep/data",
        "src/__pycache__/module.pyc",
        ".agents/skills/unselected/SKILL.md",
    )
    for name in excluded:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("excluded")
    if git:
        workspace.local_git(tmp_path, "add", "--all", "--force")
    names = {entry.relative_path for entry in workspace.inspect_workspace(plan)}
    assert not names.intersection(excluded)


def test_required_ignored_input_is_an_explicit_error(tmp_path):
    plan = plan_at(tmp_path)
    (tmp_path / ".gitignore").write_text("request.md\n")
    with pytest.raises(ContractError, match="excluded") as failure:
        workspace.inspect_workspace(plan)
    assert failure.value.code == "excluded_input"


def test_project_limits_are_not_the_explicit_input_limit(tmp_path):
    plan = plan_at(tmp_path)
    for index in range(20):
        (tmp_path / f"source-{index}.py").write_text("value = 1\n")
    assert len(workspace.inspect_workspace(plan)) >= 22
    with pytest.raises(ContractError) as failure:
        workspace.inspect_workspace(replace(plan, limits=replace(plan.limits, baseline_files=10)))
    assert failure.value.code == "baseline_limit"


def test_selected_symlink_and_nested_repository_refuse(tmp_path):
    if os.name == "nt":
        pytest.skip("Creating symlinks requires Windows privileges.")
    plan = plan_at(tmp_path)
    (tmp_path / "linked.py").symlink_to(tmp_path / "request.md")
    with pytest.raises(ContractError) as failure:
        workspace.inspect_workspace(plan)
    assert failure.value.code == "unsafe_snapshot_path"
    (tmp_path / "linked.py").unlink()
    nested = tmp_path / "nested"
    nested.mkdir()
    workspace.local_git(nested, "init", "--quiet")
    (nested / "source.py").write_text("nested")
    with pytest.raises(ContractError, match="nested|submodule"):
        workspace.inspect_workspace(plan)


def test_stale_outputs_from_other_graph_nodes_are_excluded(tmp_path):
    plan = replace(plan_at(tmp_path), chain_outputs=("specification.md", "review.md"))
    for name in (*plan.chain_outputs, "changes.diff"):
        (tmp_path / name).write_text("stale")
    names = {entry.relative_path for entry in workspace.inspect_workspace(plan)}
    assert not names.intersection((*plan.chain_outputs, "changes.diff"))


def test_factory_freezes_application_before_any_producer(tmp_path, monkeypatch):
    from test_chain import caller, prepare, producer, two_nodes

    from apmx.contracts import chain, engine
    from apmx.core.contract_logger import ContractLogger

    root = caller.__wrapped__(tmp_path, monkeypatch)
    two_nodes(root)
    (root / "extra.py").write_text("original")
    calls = producer(monkeypatch)
    original = engine.run_contract

    def mutate_caller_after_first(*args, **kwargs):
        result = original(*args, **kwargs)
        (root / "extra.py").write_text("caller changed during execution")
        (root / "added-late.py").write_text("not part of admission")
        return result

    monkeypatch.setattr(engine, "run_contract", mutate_caller_after_first)
    result = chain.run_chain(prepare(root), logger=ContractLogger(), allow_advisory=True)
    assert result.complete
    assert len(calls) == 2
    for _, snapshot, _ in calls:
        assert (snapshot.root / "extra.py").read_text() == "original"
        assert not (snapshot.root / "added-late.py").exists()
        assert snapshot.selection_schema == workspace.SELECTION_SCHEMA
    record = json.loads(result.record_path.read_bytes())
    assert record["project_capture"]["schema"] == workspace.SELECTION_SCHEMA
    view = Path(record["artifacts"]["root"])
    assert (view / "extra.py").read_text() == "original"
    assert (root / "extra.py").read_text() == "caller changed during execution"


def test_frozen_project_tampering_stops_next_stage(tmp_path, monkeypatch):
    from test_chain import caller, prepare, producer, two_nodes

    from apmx.contracts import chain, engine
    from apmx.core.contract_logger import ContractLogger

    root = caller.__wrapped__(tmp_path, monkeypatch)
    two_nodes(root)
    calls = producer(monkeypatch)
    original = engine.run_contract

    def tamper_captured_project(plan, **kwargs):
        result = original(plan, **kwargs)
        (plan.project_snapshot.root / "seed.txt").write_text("tampered")
        return result

    monkeypatch.setattr(engine, "run_contract", tamper_captured_project)
    result = chain.run_chain(prepare(root), logger=ContractLogger(), allow_advisory=True)
    assert not result.complete
    assert len(calls) == 1
    assert result.stop_reason == "baseline_changed"


def test_capture_stays_inside_consumer_subdirectory(tmp_path):
    workspace.local_git(tmp_path, "init", "--quiet")
    (tmp_path / "outside.py").write_text("not consumer code")
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    plan = plan_at(consumer)
    (consumer / "local.py").write_text("consumer code")
    workspace.local_git(tmp_path, "add", "--all")
    names = {entry.relative_path for entry in workspace.inspect_workspace(plan)}
    assert "local.py" in names
    assert "outside.py" not in names
    assert not any(name.startswith("../") for name in names)


def test_input_bytes_and_file_bytes_have_distinct_bounds(tmp_path):
    plan = plan_at(tmp_path)
    (tmp_path / "large.py").write_bytes(b"x" * 1024)
    with pytest.raises(ContractError) as failure:
        workspace.inspect_workspace(replace(plan, limits=replace(plan.limits, file_bytes=512)))
    assert failure.value.code == "snapshot_file_limit"
    with pytest.raises(ContractError) as failure:
        workspace.inspect_workspace(replace(plan, limits=replace(plan.limits, baseline_bytes=1000)))
    assert failure.value.code == "baseline_limit"


def test_submodule_refuses_without_materializing_its_source(tmp_path):
    plan = plan_at(tmp_path)
    workspace.local_git(tmp_path, "init", "--quiet")
    workspace.local_git(
        tmp_path,
        "update-index",
        "--add",
        "--cacheinfo",
        "160000,0123456789abcdef0123456789abcdef01234567,submodule",
    )
    with pytest.raises(ContractError, match="submodules"):
        workspace.inspect_workspace(plan)


def test_bound_project_rejects_added_files_and_forged_digest(tmp_path):
    plan = plan_at(tmp_path)
    directory = tmp_path / ".apm/chains/fixture"
    directory.mkdir(parents=True)
    snapshot = workspace.capture_project(plan, directory)
    with pytest.raises(ContractError) as failure:
        workspace.inspect_workspace(
            replace(plan, project_snapshot=replace(snapshot, digest="0" * 64))
        )
    assert failure.value.code == "baseline_changed"
    (snapshot.root / "extra.py").write_text("unrecorded")
    with pytest.raises(ContractError) as failure:
        workspace.inspect_workspace(replace(plan, project_snapshot=snapshot))
    assert failure.value.code == "baseline_changed"


def test_fresh_attempt_copies_reuse_original_project_bytes_and_modes(tmp_path):
    plan = plan_at(tmp_path)
    source = tmp_path / "source.py"
    source.write_bytes(b"original\r\n")
    source.chmod(0o755)
    control = tmp_path / ".apm/runs/controller-fixture"
    control.mkdir(parents=True)
    snapshot = workspace.capture_project(plan, control)
    frozen = replace(plan, project_snapshot=snapshot)
    for index in range(2):
        directory = tmp_path / f".apm/runs/attempt-{index}"
        directory.mkdir()
        attempt = workspace.capture_workspace(frozen, directory)
        assert (attempt.producer / "source.py").read_bytes() == b"original\r\n"
        assert next(
            entry for entry in attempt.files if entry.relative_path == "source.py"
        ).mode == (0o755 if os.name != "nt" else source.stat().st_mode & 0o777)
        assert attempt.project_digest == snapshot.digest
        source.write_bytes(b"changed caller")
    assert source.read_bytes() == b"changed caller"
