"""scripts/run_stage.py: how run.yml stages a local or package factory and its check requirements."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("run_stage", ROOT / "scripts/run_stage.py")
run_stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_stage)


@pytest.fixture
def caller(tmp_path):
    root = tmp_path / "caller"
    (root / "factory/checks").mkdir(parents=True)
    (root / "factory/checks/requirements.txt").write_text("six==1.17.0\n")
    (root / "factory/.apm").mkdir()
    (root / ".git").mkdir()
    (root / "app").mkdir()
    (root / "app/notes.md").write_text("x\n")
    return root


def test_a_local_factory_directory_is_copied_without_git_or_apm_state(caller, tmp_path) -> None:
    temp = tmp_path / "temp"
    values = run_stage.stage(caller, "factory", ".", temp)
    assert values == {
        "APMX_RUN_ROOT": str(temp / "factory"),
        "APMX_FACTORY_REF": "",
        "APMX_DELIVERY": "factory",
    }
    assert (temp / "factory/checks/requirements.txt").is_file()
    assert not (temp / "factory/.apm").exists()


def test_a_package_factory_runs_against_a_copy_of_the_workspace(caller, tmp_path) -> None:
    temp = tmp_path / "temp"
    values = run_stage.stage(caller, "owner/factory#v1", "app", temp)
    assert values == {
        "APMX_RUN_ROOT": str(temp / "workspace"),
        "APMX_FACTORY_REF": "owner/factory#v1",
        "APMX_DELIVERY": "app",
    }
    assert (temp / "workspace/notes.md").is_file()
    root = run_stage.stage(caller, "owner/factory#v1", ".", tmp_path / "temp2")["APMX_RUN_ROOT"]
    assert not (Path(root) / ".git").exists()


@pytest.mark.parametrize(
    ("factory", "workspace", "message"),
    [
        ("owner/factory", ".", "must pin its ref"),
        ("missing", ".", "not a directory in this repository"),
        ("../outside", ".", "inside the factory"),
        ("owner/factory#v1", "../outside", "inside the factory"),
        ("owner/factory#v1", "missing", "workspace is not a directory"),
    ],
)
def test_staging_refuses_unpinned_packages_and_escapes(
    caller, tmp_path, factory, workspace, message
):
    with pytest.raises(run_stage.StageError, match=message):
        run_stage.stage(caller, factory, workspace, tmp_path / "temp")


@pytest.mark.parametrize(
    ("ref", "owner", "repo", "path", "pin"),
    [
        ("owner/repo#v1", "owner", "repo", "", "v1"),
        ("github.com/owner/repo/sub/dir#0123abc", "owner", "repo", "/sub/dir", "0123abc"),
        ("https://github.com/owner/repo.git#refs/tags/v1", "owner", "repo", "", "refs/tags/v1"),
    ],
)
def test_github_package_references_parse(ref, owner, repo, path, pin) -> None:
    match = run_stage._GITHUB.match(ref)
    assert (match["owner"], match["repo"], match["path"], match["ref"]) == (owner, repo, path, pin)


def test_requirements_must_stay_inside_the_factory(caller) -> None:
    with pytest.raises(run_stage.StageError, match="inside the factory"):
        run_stage.requirements(caller / "factory", "", "../app/notes.md", caller)
    with pytest.raises(run_stage.StageError, match="not a file inside the factory"):
        run_stage.requirements(caller / "factory", "", "checks/missing.txt", caller)
    with pytest.raises(run_stage.StageError, match="GitHub package reference"):
        run_stage.package_file("https://example.com/x/y#v1", "checks/requirements.txt", caller)


def _receipt(tmp_path: Path, digest: str) -> Path:
    receipt = tmp_path / "receipt"
    receipt.mkdir(parents=True)
    resource = {"relative_path": "checks/requirements.txt", "sha256": digest, "size": 1}
    definition = {"stages": [{"resources": [resource]}, {"resources": [resource]}]}
    (receipt / "definition.json").write_text(json.dumps(definition))
    return receipt


def test_verify_requires_the_definition_to_record_the_installed_file(caller, tmp_path) -> None:
    installed = caller / "factory/checks/requirements.txt"
    digest = hashlib.sha256(installed.read_bytes()).hexdigest()
    assert run_stage.verify(_receipt(tmp_path / "a", digest), "checks/requirements.txt", installed)
    with pytest.raises(run_stage.StageError, match="ref moved"):
        run_stage.verify(_receipt(tmp_path / "b", "0" * 64), "checks/requirements.txt", installed)
    with pytest.raises(run_stage.StageError, match="not a checker resource"):
        run_stage.verify(_receipt(tmp_path / "c", digest), "requirements.txt", installed)
