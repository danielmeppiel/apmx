"""Real official APM owns graphs; apmx projects only selected context."""

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from apmx.contracts.frontend import parse_contract, plan_contract
from apmx.contracts.models import ContractError, ContractLimits
from apmx.contracts.records import AttemptStore
from apmx.contracts.workspace import capture_workspace
from apmx.contracts.workspace import local_git
from apmx.install.apm_backend import backend_child_env, install, locate_backend
from apmx.install.contract_source import prepare_contract_source, prepare_imports
from apmx.install.contract_source_validation import source_hash
from apmx.utils.git_env import redact_git_diagnostic

EXAMPLE = Path(__file__).resolve().parents[3] / "examples/contracts/packaged-job"
LIMITS = ContractLimits()


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    caller = tmp_path / "caller"
    caller.mkdir()
    package = tmp_path / "package"
    shutil.copytree(EXAMPLE, package)
    monkeypatch.delenv("APM_NO_SCRIPTS", raising=False)
    monkeypatch.delenv("APM_POLICY_DISABLE", raising=False)
    monkeypatch.setattr("apmx.runtime.utils.find_runtime_binary", lambda _: sys.executable)
    (caller / "notes.md").write_text("CALLER INPUT\n")
    return caller, package


def _contract(package):
    return next(package.rglob("*.contract.md")).relative_to(package).as_posix()


def test_native_package_import_record_and_retained_bytes(fixture):
    caller, package = fixture
    before = source_hash(package, LIMITS)
    with prepare_contract_source(
        str(package), _contract(package), caller_root=caller, planning=False, limits=LIMITS
    ) as source:
        plan = plan_contract(Path(_contract(package)), caller, harness="copilot", source=source)
        assert plan.apm_backend["version"] == "0.30.0"
        assert len(plan.imported_skills) == 1
        assert plan.imported_skills[0].version == "0.1.0"
        store = AttemptStore.create(plan)
        record = json.loads(store.record_path.read_text())
        assert record["imports"][0]["version"] == "0.1.0"
        lock = Path(record["source"]["retained"]["apm.lock.yaml"])
        assert b"apm_version: 0.30.0" in lock.read_bytes()
        assert hashlib.sha256(lock.read_bytes()).hexdigest() == record["lock_sha256"]
        assert "depth: 2" in lock.read_text()
    assert source_hash(package, LIMITS) == before


def test_consumer_lock_wins_for_packaged_contract(fixture, tmp_path):
    caller, package = fixture
    override = tmp_path / "override"
    shutil.copytree(package / "skills/handoff-style", override)
    manifest = override / "apm.yml"
    manifest.write_text(manifest.read_text().replace("0.1.0", "9.0.0"))
    (caller / "apm.yml").write_text(
        "name: consumer\nversion: 1.0.0\ndependencies:\n  apm:\n    - ../override\n"
    )
    install(caller, limits=LIMITS)
    before = {name: (caller / name).read_bytes() for name in ("apm.yml", "apm.lock.yaml")}
    with prepare_contract_source(
        str(package), _contract(package), caller_root=caller, planning=False, limits=LIMITS
    ) as source:
        assert not source.imports_root.is_relative_to(caller)
        with prepare_imports(
            caller, source.root / _contract(package), source=source, planning=False, limits=LIMITS
        ) as (root, identity):
            assert not root.is_relative_to(caller)
            plan = plan_contract(
                Path(_contract(package)), caller, harness="copilot", source=source,
                imports_root=root, apm_backend=identity,
            )
            assert {item.version for item in plan.imported_skills} == {"9.0.0"}
            assert all("override" in item.lock_identity for item in plan.imported_skills)
    assert all((caller / name).read_bytes() == raw for name, raw in before.items())


def test_multiple_package_context_and_resources_exclude_decoys(fixture, tmp_path):
    caller, package = fixture
    style = package / "skills/handoff-style"
    rules = tmp_path / "rules"
    (rules / ".apm/instructions").mkdir(parents=True)
    (rules / "apm.yml").write_text("name: house-rules\nversion: 2.0.0\n")
    # Reuse existing primitive body: this regression tests selection, not prompt design.
    (rules / ".apm/instructions/style.instructions.md").write_bytes(
        (style / "SKILL.md").read_bytes().replace(b"name: handoff-style", b"name: house-guidance")
    )
    (style / "references").mkdir()
    (style / "references/marker.txt").write_text("resource marker")
    (style / "hooks").mkdir()
    (style / "hooks/decoy.json").write_text('{"not":"selected"}')
    (caller / "apm.yml").write_text(
        f"name: consumer\nversion: 1.0.0\ndependencies:\n  apm:\n"
        f"    - {style}\n    - {rules}\n"
    )
    source = caller / "work.contract.md"
    original = (package / _contract(package)).read_text()
    source.write_text(original.replace("  - handoff-style", "  - handoff-style\n  - house-rules"))
    assert len(parse_contract(source).imports) == 2
    with prepare_imports(caller, source, source=None, planning=False, limits=LIMITS) as (root, backend):
        plan = plan_contract(source, caller, harness="copilot", imports_root=root, apm_backend=backend)
        assert {item.kind for item in plan.imported_skills} == {"skill", "instruction"}
        store = AttemptStore.create(plan)
        snapshot = capture_workspace(plan, store.directory)
        assert (snapshot.producer / "_apmx_context/import-1/references/marker.txt").read_text() == "resource marker"
        assert not list((snapshot.producer / "_apmx_context").rglob("decoy.json"))
        assert plan.imported_skills[0].resources[0].relative_path == "references/marker.txt"


@pytest.mark.parametrize("imports", ["[missing]", "[handoff-style@^1]", "[handoff-style, handoff-style]"])
def test_missing_versioned_duplicate_context_refuses_before_producer(fixture, imports):
    caller, package = fixture
    source = package / _contract(package)
    source.write_text(source.read_text().replace("imports:\n  - handoff-style", f"imports: {imports}"))
    with pytest.raises(ContractError):
        with prepare_contract_source(
            str(package), _contract(package), caller_root=caller, planning=False, limits=LIMITS
        ) as prepared:
            plan_contract(Path(_contract(package)), caller, harness="copilot", source=prepared)
    assert not (caller / ".apm/runs").exists()


def test_malformed_consumer_lock_never_invokes_backend(fixture, monkeypatch):
    caller, package = fixture
    (caller / "apm.lock.yaml").write_text("dependencies: [\n")
    def forbidden(*args, **kwargs):
        pytest.fail("native acquisition ran before lock admission")
    monkeypatch.setattr("apmx.install.apm_backend.install", forbidden)
    with pytest.raises(ContractError, match="malformed"):
        with prepare_contract_source(
            str(package), _contract(package), caller_root=caller, planning=False, limits=LIMITS
        ):
            pytest.fail("invalid lock admitted")


def test_package_name_not_skill_symbol_is_the_import_binding(fixture):
    caller, package = fixture
    skill = package / "skills/handoff-style/SKILL.md"
    skill.write_text(skill.read_text().replace("name: handoff-style", "name: separate-skill-name"))
    with prepare_contract_source(
        str(package), _contract(package), caller_root=caller, planning=False, limits=LIMITS
    ) as source:
        plan = plan_contract(Path(_contract(package)), caller, harness="copilot", source=source)
        assert plan.imported_skills[0].name == "handoff-style"
        assert plan.imported_skills[0].context_name == "separate-skill-name"
        contract = source.root / _contract(package)
        contract.write_text(contract.read_text().replace("  - handoff-style", "  - separate-skill-name"))
        updated = replace(source, prepared_hash=source_hash(source.root, LIMITS))
        with pytest.raises(ContractError, match="one installed APM package"):
            plan_contract(Path(_contract(package)), caller, harness="copilot", source=updated)


def test_unselected_local_packages_are_not_copied_from_tracked_caller(fixture):
    caller, package = fixture
    selected = caller / "packages/selected"
    unselected = caller / "packages/unselected"
    shutil.copytree(package / "skills/handoff-style", selected)
    shutil.copytree(package / "skills/handoff-style", unselected)
    (unselected / "apm.yml").write_text("name: unselected-package\nversion: 1.0.0\n")
    (unselected / "SKILL.md").write_text(
        (unselected / "SKILL.md").read_text() + "\nUNSELECTED_PACKAGE_SENTINEL\n"
    )
    (caller / "apm.yml").write_text(
        "name: caller\nversion: 1.0.0\ndependencies:\n  apm:\n"
        "    - ./packages/selected\n    - ./packages/unselected\n"
    )
    contract = caller / "work.contract.md"
    contract.write_bytes((package / _contract(package)).read_bytes())
    local_git(caller, "init", "--quiet")
    local_git(caller, "add", ".")
    with prepare_imports(caller, contract, source=None, planning=False, limits=LIMITS) as (root, backend):
        plan = plan_contract(contract, caller, harness="copilot", imports_root=root, apm_backend=backend)
        store = AttemptStore.create(plan)
        snapshot = capture_workspace(plan, store.directory)
        assert not (snapshot.producer / "packages").exists()
        for path in snapshot.producer.rglob("*"):
            if path.is_file() and ".git" not in path.parts:
                assert b"UNSELECTED_PACKAGE_SENTINEL" not in path.read_bytes()


def test_aggregate_resources_are_bounded(fixture):
    caller, package = fixture
    references = package / "skills/handoff-style/references"
    references.mkdir()
    for name in ("one.txt", "two.txt"):
        (references / name).write_text("data")
    with prepare_contract_source(
        str(package), _contract(package), caller_root=caller, planning=False, limits=LIMITS
    ) as source:
        with pytest.raises(ContractError, match="limit"):
            plan_contract(
                Path(_contract(package)), caller, harness="copilot", source=source,
                limits=replace(LIMITS, resource_files=2),
            )


def test_selected_unsupported_activation_refuses(fixture):
    caller, package = fixture
    skill = package / "skills/handoff-style/SKILL.md"
    skill.write_text(skill.read_text().replace("name: handoff-style", "name: handoff-style\nmodel: other"))
    with prepare_contract_source(
        str(package), _contract(package), caller_root=caller, planning=False, limits=LIMITS
    ) as source:
        with pytest.raises(ContractError, match="unsupported activation"):
            plan_contract(Path(_contract(package)), caller, harness="copilot", source=source)


def test_declared_relative_contract_source_has_one_native_identity(fixture):
    caller, package = fixture
    (caller / "apm.yml").write_text(
        "name: consumer\nversion: 1.0.0\ndependencies:\n  apm:\n    - ../package\n"
    )
    install(caller, limits=LIMITS)
    before = {name: (caller / name).read_bytes() for name in ("apm.yml", "apm.lock.yaml")}
    with prepare_contract_source(
        "../package", _contract(package), caller_root=caller, planning=False, limits=LIMITS
    ) as source:
        assert source.original_root == package
        assert source.root.is_dir()
        plan = plan_contract(Path(_contract(package)), caller, harness="copilot", source=source)
        assert plan.imported_skills
    assert all((caller / name).read_bytes() == data for name, data in before.items())


def test_consumer_pins_precede_unavailable_publisher_graph(fixture, tmp_path, monkeypatch):
    caller, package = fixture
    repo = tmp_path / "native-git-context"
    shutil.copytree(package / "skills/handoff-style", repo)
    (repo / "apm.yml").write_text("name: handoff-style\nversion: 9.0.0\n")
    local_git(repo, "init", "--quiet")
    local_git(repo, "add", ".")
    local_git(repo, "commit", "--quiet", "-m", "Native Git context fixture")
    local_git(repo, "tag", "v9")
    revision = local_git(repo, "rev-parse", "HEAD").decode().strip()
    git = shutil.which("git")
    assert git
    wrapper = tmp_path / "git_transport.py"
    wrapper.write_text(
        "import subprocess, sys\n"
        "if '-G' in sys.argv: raise SystemExit(0)\n"
        f"raise SystemExit(subprocess.call([{git!r}, 'upload-pack', {str(repo)!r}]))\n"
    )
    # A hermetic SSH transport serves real Git objects. Neither APM nor its
    # resolver, lockfile, checkout, or installed context is mocked.
    monkeypatch.setenv(
        "GIT_SSH_COMMAND",
        f"{shlex.quote(Path(sys.executable).as_posix())} -B {shlex.quote(wrapper.as_posix())}",
    )
    monkeypatch.setenv("GIT_SSH_VARIANT", "ssh")
    url = "ssh://git@localhost/fixtures/handoff-style.git"
    transport = subprocess.run(
        [git, "ls-remote", url, "refs/tags/v9"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert transport.returncode == 0, redact_git_diagnostic(transport.stderr)
    assert revision in transport.stdout
    (caller / "apm.yml").write_text(
        "name: consumer\nversion: 1.0.0\ndependencies:\n  apm:\n"
        f"    - git: {url}\n      ref: v9\n"
    )
    trace = tmp_path / "native-git-trace.jsonl"
    with tempfile.TemporaryDirectory(prefix="apmx-fixture-") as temporary:
        generation_root = Path(temporary)
        shutil.copy2(caller / "apm.yml", generation_root / "apm.yml")
        generated = subprocess.run(
            [
                str(locate_backend()), "install", "--root", str(generation_root),
                "--only", "apm", "--target", "agent-skills", "--no-trust-bin",
            ],
            cwd=generation_root,
            env={**backend_child_env(dict(os.environ)), "GIT_TRACE2_EVENT": str(trace)},
            capture_output=True, text=True, timeout=120, check=False,
        )
        errors = []
        if trace.exists():
            for line in trace.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if event.get("event") == "error":
                    errors.append(str(event.get("msg", "")))
        assert generated.returncode == 0, redact_git_diagnostic(
            generated.stdout + generated.stderr + "\n".join(errors)
        )
        for name in ("apm.yml", "apm.lock.yaml"):
            shutil.copy2(generation_root / name, caller / name)
        shutil.copytree(generation_root / "apm_modules", caller / "apm_modules")
    (package / "apm.yml").write_text(
        "name: packaged-handoff\nversion: 0.1.0\ndependencies:\n  apm:\n"
        f"    - git: {url}\n      ref: publisher-version-does-not-exist\n"
    )
    before = {name: (caller / name).read_bytes() for name in ("apm.yml", "apm.lock.yaml")}
    source_before = source_hash(package, LIMITS)
    with prepare_contract_source(
        str(package), _contract(package), caller_root=caller, planning=False, limits=LIMITS
    ) as source:
        assert not source.imports_root.is_relative_to(caller)
        assert source.imports_root.name.startswith("apmx-")
        with prepare_imports(
            caller, source.root / _contract(package), source=source, planning=False, limits=LIMITS
        ) as (root, identity):
            assert not root.is_relative_to(caller)
            assert root.name.startswith("apmx-")
            plan = plan_contract(
                Path(_contract(package)), caller, harness="copilot", source=source,
                imports_root=root, apm_backend=identity,
            )
            assert len(plan.imported_skills) == 1
            assert plan.imported_skills[0].resolved_commit == revision
            assert plan.imported_skills[0].version == "9.0.0"
            assert plan.imported_skills[0].verified_package_hash
    assert source_hash(package, LIMITS) == source_before
    assert all((caller / name).read_bytes() == data for name, data in before.items())


def test_native_source_bootstrap_accepts_null_consumer_dependencies(fixture, monkeypatch):
    from contextlib import contextmanager
    import apmx.install.contract_source as preparation

    caller, package = fixture
    manifest = caller / "apm.yml"
    manifest.write_text("name: consumer\nversion: 1.0.0\ndependencies: null\n")
    before = manifest.read_bytes()
    source_before = source_hash(package, LIMITS)
    stages = []
    private_root = preparation._private_root
    @contextmanager
    def observe_root(*args):
        with private_root(*args) as root:
            stages.append(root)
            yield root
    monkeypatch.setattr(preparation, "_private_root", observe_root)
    with prepare_contract_source(
        str(package), _contract(package), caller_root=caller, planning=False, limits=LIMITS
    ) as source:
        assert source.root.is_dir()
        assert source.apm_backend["version"] == "0.30.0"
        assert source.imports_root == stages[0]
        assert (source.imports_root / "apm.lock.yaml").is_file()
    assert manifest.read_bytes() == before
    assert not (caller / "apm.lock.yaml").exists()
    assert source_hash(package, LIMITS) == source_before
