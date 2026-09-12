"""Genuine APM virtual-package inventories and fail-closed metadata projection."""

import json
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from apmx.contracts.imports import read_lock
from apmx.contracts.models import ContractError, ContractLimits
from apmx.contracts.native_integrity import verify_inventory_package
from apmx.contracts.workspace import local_git
from apmx.install.apm_backend import install
from apmx.install.contract_source import _selected_source
from apmx.models.dependency.reference import DependencyReference
from apmx.utils.content_hash import compute_package_hash
from apmx.utils.yaml_io import dump_yaml

LIMITS = ContractLimits()
EXAMPLE = Path(__file__).resolve().parents[3] / "examples/contracts/packaged-job"


@pytest.fixture(scope="module")
def native_virtual_inventory(tmp_path_factory):
    fixture = tmp_path_factory.mktemp("virtual-git")
    repo = fixture / "repo"
    shutil.copytree(EXAMPLE, repo / "packages/job")
    local_git(repo, "init", "--quiet")
    local_git(repo, "add", ".")
    local_git(repo, "commit", "--quiet", "-m", "Native virtual package fixture")
    revision = local_git(repo, "rev-parse", "HEAD").decode().strip()
    git = shutil.which("git")
    assert git
    transport = fixture / "transport.py"
    transport.write_text(
        "import subprocess, sys\n"
        "if '-G' in sys.argv: raise SystemExit(0)\n"
        f"raise SystemExit(subprocess.call([{git!r}, 'upload-pack', {str(repo)!r}]))\n"
    )
    declaration = {
        "git": "ssh://git@localhost/fixtures/virtual.git",
        "path": "packages/job",
        "ref": revision,
    }
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv(
            "GIT_SSH_COMMAND",
            f"{shlex.quote(Path(sys.executable).as_posix())} -B {shlex.quote(transport.as_posix())}",
        )
        environment.setenv("GIT_SSH_VARIANT", "ssh")
        with tempfile.TemporaryDirectory(prefix="ax-") as temporary:
            stage = Path(temporary)
            dump_yaml({
                "name": "virtual-consumer", "version": "1.0.0",
                "dependencies": {"apm": [declaration]},
            }, stage / "apm.yml")
            install(stage, limits=LIMITS)
            yield stage, declaration


@pytest.fixture
def inventory(native_virtual_inventory, tmp_path):
    original, declaration = native_virtual_inventory
    stage = tmp_path / "installed"
    shutil.copytree(original, stage)
    lock, _ = read_lock(stage, LIMITS)
    assert lock is not None
    parent = next(dep for dep in lock.dependencies.values() if dep.name == "packaged-handoff")
    child = next(dep for dep in lock.dependencies.values() if dep.name == "handoff-style")
    root = parent.to_dependency_ref().get_install_path(stage / "apm_modules")
    return stage, declaration, lock, parent, child, root


def test_genuine_virtual_inventory_preserves_native_hash_authority(inventory):
    stage, declaration, lock, parent, child, root = inventory
    assert compute_package_hash(root) != parent.content_hash
    expected = ("skills/handoff-style/.apm-pin",)
    assert verify_inventory_package(stage, parent, lock, LIMITS) == expected
    assert verify_inventory_package(stage, child, lock, LIMITS) == ()
    selected, locked, metadata = _selected_source(
        stage, DependencyReference.parse_from_dict(declaration), LIMITS,
    )
    assert selected == root
    assert locked.content_hash == parent.content_hash
    assert metadata == expected


@pytest.mark.parametrize("relative", [
    "contracts/handoff.contract.md",
    "checks/check_handoff.py",
    "skills/handoff-style/SKILL.md",
    "unlisted/.apm-pin",
])
def test_native_marker_projection_rejects_changed_package_bytes(inventory, relative):
    stage, _, lock, parent, _, root = inventory
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((target.read_bytes() if target.exists() else b"") + b"\ntampered")
    with pytest.raises(ContractError) as rejected:
        verify_inventory_package(stage, parent, lock, LIMITS)
    assert rejected.value.code == "import_drift"


@pytest.mark.parametrize("change", ["schema", "commit", "extra", "format"])
def test_native_marker_projection_rejects_unproved_metadata(inventory, change):
    stage, _, lock, parent, child, root = inventory
    marker = root / "skills/handoff-style/.apm-pin"
    payload = {"schema_version": 1, "resolved_commit": child.resolved_commit}
    if change == "schema":
        payload["schema_version"] = 2
    elif change == "commit":
        payload["resolved_commit"] = "0" * 40
    elif change == "extra":
        payload["untrusted"] = "extra"
    marker.write_text(json.dumps(payload, indent=2 if change == "format" else None))
    with pytest.raises(ContractError):
        verify_inventory_package(stage, parent, lock, LIMITS)


def test_native_marker_projection_requires_child_inventory_entry(inventory):
    stage, _, lock, parent, child, _ = inventory
    del lock.dependencies[child.get_unique_key()]
    with pytest.raises(ContractError):
        verify_inventory_package(stage, parent, lock, LIMITS)
