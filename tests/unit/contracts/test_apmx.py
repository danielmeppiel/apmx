"""CLI/source admission and genuine APM package preparation.

Old copied-downloader tests are replaced by backend invocation unit tests and
real APM graph/replay tests in test_apm_backend.py and test_native_imports.py.
The synthetic Git lock below tests read-only admission, not network acquisition.
"""

import hashlib
import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from apmx.cli import main
from apmx.contracts import frontend, workspace
from apmx.contracts.models import (
    ContractError, ContractLimits, Outcome, ProcessObservation, ProcessRequest,
)
from apmx.contracts.records import AttemptStore
from apmx.deps.lockfile import LockedDependency, LockFile
from apmx.install import contract_source
from apmx.models.dependency.reference import DependencyReference
from apmx.utils.content_hash import compute_package_hash

pytestmark = pytest.mark.component


@pytest.fixture
def caller(tmp_path, monkeypatch):
    root = tmp_path / "caller"
    root.mkdir()
    workspace.local_git(root, "init", "--quiet")
    (root / "notes.md").write_text("CALLER INPUT\n")
    monkeypatch.chdir(root)
    monkeypatch.delenv("APM_NO_SCRIPTS", raising=False)
    monkeypatch.delenv("APM_POLICY_DISABLE", raising=False)
    monkeypatch.setattr("apmx.runtime.utils.find_runtime_binary", lambda _: sys.executable)
    return root


def _package(root, *, imports=False):
    root.mkdir()
    manifest = "name: packaged-job\nversion: 1.0.0\n"
    if imports:
        manifest += "dependencies:\n  apm:\n    - ../style\n"
    (root / "apm.yml").write_text(manifest)
    (root / "notes.md").write_text("PACKAGE INPUT MUST NOT WIN\n")
    (root / "checks").mkdir()
    (root / "checks/check.py").write_text(
        "from pathlib import Path\n"
        "assert Path('result.txt').read_bytes() == Path('notes.md').read_bytes()\n"
    )
    header = (
        "needs: notes.md\nproduces: result.txt\nverify:\n"
        f"  content: '{sys.executable} checks/check.py'\n"
    )
    if imports:
        header += "imports: [style]\n"
    (root / "job.contract.md").write_text(
        "---\n" + header + "---\nWrite result.txt from notes.md.\n"
    )
    return root


def _skill(root):
    root.mkdir()
    (root / "apm.yml").write_text("name: style\nversion: 1.0.0\n")
    (root / "SKILL.md").write_text(
        "---\nname: style\ndescription: concise style\n---\nWrite concisely.\n"
    )


def _prepare(package, caller, *, planning=False):
    return contract_source.prepare_contract_source(
        str(package), "job.contract.md", caller_root=caller,
        planning=planning, limits=ContractLimits(),
    )


@pytest.fixture
def producer(monkeypatch):
    """Real local child and checks, never native model inference."""
    def build_request(plan, snapshot, directory, *, timeout_seconds):
        return ProcessRequest(
            (sys.executable, "-c",
             "from pathlib import Path; "
             "Path('result.txt').write_bytes(Path('notes.md').read_bytes()); "
             """print('{"type":"result","exitCode":0,"sessionId":"fixture","usage":{}}')"""),
            snapshot.producer,
            timeout_seconds,
        )

    adapter = Mock()
    adapter.build_contract_request.side_effect = build_request
    monkeypatch.setattr(
        "apmx.contracts.engine.RuntimeFactory.get_runtime_by_name", lambda *args: adapter
    )
    return adapter.build_contract_request


@pytest.mark.parametrize("verbose", [False, True])
def test_package_apm_preparation_is_visible_and_retained(caller, tmp_path, producer, verbose):
    package = _package(tmp_path / "job", imports=True)
    _skill(tmp_path / "style")
    result = CliRunner().invoke(main, [
        "--from", str(package), "job.contract.md", "--on", "copilot", "--allow-host-access",
        *(["--verbose"] if verbose else []),
    ])
    assert result.exit_code == Outcome.UNPROVEN, result.output
    assert "content: passed" in result.output
    producer.assert_called_once()
    transcript = next((caller / ".apm/runs").glob("*/transcript.log")).read_text()
    assert "APM > [>] Resolving" in result.output
    assert "APM (untrusted) > [*] Installed 2 APM dependencies" in transcript
    for output in (result.output, transcript):
        phases = [
            "Installing packages with APM 0.30.0",
            "Packages ready.",
            "Using style",
            "Preparing files for Copilot",
            "Running Copilot",
        ]
        assert [output.index(phase) for phase in phases] == sorted(
            output.index(phase) for phase in phases
        )
        assert output.count(phases[0]) == 1
        assert output.count(phases[1]) == 1
    details = (
        "Running: apm install (in a temporary workspace)",
        "APM options: --only apm --target agent-skills --no-trust-bin",
        "Skill: style (SKILL.md)",
    )
    for detail in details:
        assert (detail in result.output) is verbose
        assert detail in transcript
    assert ("APM > Phase:" in result.output) is verbose
    assert ("--no-trust-bin --verbose" in transcript) is verbose
    assert "shape" not in result.output


@pytest.mark.parametrize("packaged", [False, True])
@pytest.mark.parametrize("frozen", [False, True])
@pytest.mark.parametrize("verbose", [False, True])
def test_consumer_preparation_is_separately_scoped_and_retained(
    caller, tmp_path, producer, monkeypatch, packaged, frozen, verbose,
):
    from apmx.install import apm_backend

    package = _package(tmp_path / "job", imports=True)
    _skill(tmp_path / "style")
    if not packaged:
        shutil.copytree(package, caller, dirs_exist_ok=True)
    (caller / "apm.yml").write_text(
        "name: caller\nversion: 1.0.0\ndependencies:\n  apm: [../style]\n"
    )
    if frozen:
        apm_backend.install(caller, limits=ContractLimits())
    before = (caller / "apm.yml").read_bytes()
    install = Mock(wraps=apm_backend.install)
    monkeypatch.setattr(apm_backend, "install", install)
    result = CliRunner().invoke(main, [
        "job.contract.md", "--on", "copilot", "--allow-host-access",
        *(["--from", str(package)] if packaged else []),
        *(["--verbose"] if verbose else []),
    ])
    assert result.exit_code == Outcome.UNPROVEN, result.output
    assert "content: passed" in result.output
    producer.assert_called_once()
    assert install.call_count == 1 + int(packaged)
    assert install.call_args.kwargs["scope"] == "consumer"
    assert install.call_args.kwargs["frozen"] is frozen
    assert (caller / "apm.yml").read_bytes() == before
    transcript = next((caller / ".apm/runs").glob("*/transcript.log")).read_text()
    for output in (result.output, transcript):
        started = "Installing project imports with APM 0.30.0"
        completed = "Project imports ready."
        assert output.count(started) == output.count(completed) == 1
        assert output.index(started) < output.index(completed) < output.index("Using style")
        assert output.count("Packages ready.") == int(packaged)
        if packaged:
            assert output.index("Packages ready.") < output.index(started)
        assert output.count("Using style") == 1
        assert ("Using locked versions." in output) is frozen
        assert output.count("Temporary workspace; your project files are unchanged.") == 1
    options = [line for line in transcript.splitlines() if "APM options:" in line]
    assert len(options) == 1 + int(packaged)
    assert ("--frozen" in options[-1]) is frozen
    assert result.output.count("Running: apm install") == (1 + int(packaged)) * int(verbose)


@pytest.mark.parametrize("selection", ["local", "package", "remote"])
@pytest.mark.parametrize("verbose", [False, True])
def test_offline_cli_never_reports_an_install(caller, tmp_path, monkeypatch, selection, verbose):
    package = _package(tmp_path / "job")
    if selection == "local":
        shutil.copytree(package, caller, dirs_exist_ok=True)
    install = Mock(side_effect=AssertionError("Offline planning invoked APM"))
    monkeypatch.setattr("apmx.install.apm_backend.install", install)
    ref = str(package) if selection == "package" else "org/job#v1"
    result = CliRunner().invoke(main, [
        "job.contract.md", "--on", "copilot", "--plan",
        *(["--from", ref] if selection != "local" else []),
        *(["--verbose"] if verbose else []),
    ])
    assert result.exit_code == (Outcome.UNPROVEN if selection == "remote" else 0), result.output
    install.assert_not_called()
    assert "with APM" not in result.output
    assert "Packages ready" not in result.output
    assert "Running: apm install" not in result.output
    assert not (caller / ".apm/runs").exists()


@pytest.mark.parametrize("verbose", [False, True])
def test_local_run_without_imports_does_not_claim_apm_ran(
    caller, tmp_path, producer, monkeypatch, verbose,
):
    shutil.copytree(_package(tmp_path / "job"), caller, dirs_exist_ok=True)
    install = Mock(side_effect=AssertionError("No imports need APM"))
    monkeypatch.setattr("apmx.install.apm_backend.install", install)
    result = CliRunner().invoke(main, [
        "job.contract.md", "--on", "copilot", "--allow-host-access",
        *(["--verbose"] if verbose else []),
    ])
    assert result.exit_code == Outcome.UNPROVEN, result.output
    assert "content: passed" in result.output
    install.assert_not_called()
    producer.assert_called_once()
    transcript = next((caller / ".apm/runs").glob("*/transcript.log")).read_text()
    for output in (result.output, transcript):
        assert "with APM" not in output
        assert "Running: apm install" not in output
        assert "APM >" not in output
        assert "Preparing files for Copilot" in output


@pytest.mark.parametrize("packaged", [False, True])
@pytest.mark.parametrize("failure", ["exit", "version", "cancelled", "interrupt"])
@pytest.mark.parametrize("verbose", [False, True])
def test_failed_preparation_never_launches_producer_or_claims_success(
    caller, tmp_path, producer, monkeypatch, packaged, failure, verbose,
):
    from apmx.install import apm_backend

    package = _package(tmp_path / "job", imports=True)
    _skill(tmp_path / "style")
    if not packaged:
        shutil.copytree(package, caller, dirs_exist_ok=True)
    monkeypatch.setenv("GITHUB_TOKEN", "PRIVATE_AUTH_SENTINEL")

    def failed(request, *, on_bytes, **kwargs):
        on_bytes("stderr", b"Authorization: Bearer PRIVATE_STDERR_SENTINEL\x1b]52;c;injection\x07\n")
        if request.argv[1] == "--version" and failure != "version":
            on_bytes("stdout", apm_backend.expected_version_output().encode() + b"\n")
            return ProcessObservation(0)
        on_bytes("stdout", b"https://user:PRIVATE_STDOUT_SENTINEL@example.test/repo\n")
        if failure == "interrupt":
            raise KeyboardInterrupt
        if failure == "cancelled":
            return ProcessObservation(None, stop_reason="cancelled")
        return ProcessObservation(1)

    supervisor = Mock(side_effect=failed)
    monkeypatch.setattr(apm_backend, "supervise_process", supervisor)
    result = CliRunner().invoke(main, [
        "job.contract.md", "--on", "copilot", "--allow-host-access",
        *(["--from", str(package)] if packaged else []),
        *(["--verbose"] if verbose else []),
    ])
    assert result.exit_code == Outcome.HALTED, result.output
    producer.assert_not_called()
    assert supervisor.call_count == (1 if failure == "version" else 2)
    assert ("with APM" in result.output) is (failure != "version")
    assert "[+]" not in result.output
    assert "ready." not in result.output
    assert "Using style" not in result.output
    assert "Preparing files for Copilot" not in result.output
    assert "PRIVATE" not in result.output
    assert "\x1b" not in result.output
    assert not (caller / ".apm/runs").exists()


@pytest.mark.parametrize("args,code", [
    (["--help"], 0), (["--version"], 0), ([], 2),
    (["job", "--on", "copilot"], 2), (["job.contract.md"], 2),
    (["job.contract.md", "extra", "--on", "copilot"], 2),
])
def test_cli_explicit_selection(args, code):
    result = CliRunner().invoke(main, args)
    assert result.exit_code == code, result.output
    assert not isinstance(result.exception, (ImportError, AttributeError))


@pytest.mark.parametrize("harness", ["codex", "unknown"])
@pytest.mark.parametrize("verbose", [False, True])
def test_cli_harness_admission_belongs_to_frontend(caller, monkeypatch, harness, verbose):
    (caller / "job.contract.md").write_text(
        "---\nproduces: result.txt\nverify: {content: 'true'}\n---\nWrite.\n"
    )
    binary = Mock(side_effect=AssertionError("Unsupported harness cannot launch"))
    monkeypatch.setattr("apmx.runtime.utils.find_runtime_binary", binary)
    args = ["job.contract.md", "--on", harness, "--plan"] + (["--verbose"] if verbose else [])
    result = CliRunner().invoke(main, args)
    assert result.exit_code == Outcome.UNPROVEN, result.output
    assert harness in result.output
    assert ("unsupported_harness" in result.output) is verbose
    binary.assert_not_called()
    assert not (caller / ".apm").exists()


def test_manifestless_local_plan_is_read_only(caller):
    (caller / "job.contract.md").write_text(
        "---\nneeds: notes.md\nproduces: result.txt\nverify: {content: 'true'}\n---\nWrite.\n"
    )
    before = sorted(path.relative_to(caller).as_posix() for path in caller.rglob("*"))
    result = CliRunner().invoke(main, ["job.contract.md", "--on", "copilot", "--plan"])
    assert result.exit_code == 0, result.output
    assert before == sorted(path.relative_to(caller).as_posix() for path in caller.rglob("*"))


def test_malformed_caller_manifest_blocks_package(caller, tmp_path):
    package = _package(tmp_path / "job")
    (caller / "apm.yml").write_text("[invalid\n")
    result = CliRunner().invoke(main, [
        "--from", str(package), "job.contract.md", "--on", "copilot", "--plan",
    ])
    assert result.exit_code == 22
    assert "manifest" in result.output.lower()
    assert not (caller / ".apm").exists()


def test_package_plan_maps_caller_and_resources(caller, tmp_path):
    package = _package(tmp_path / "job")
    before = compute_package_hash(package)
    with _prepare(package, caller, planning=True) as source:
        plan = frontend.plan_contract(Path("job.contract.md"), caller, harness="copilot", source=source)
        entries = {item.relative_path: item for item in workspace.inspect_workspace(plan)}
        assert plan.project_root == caller
        assert plan.evidence_root == caller / ".apm/runs"
        assert entries["notes.md"].sha256 == hashlib.sha256((caller / "notes.md").read_bytes()).hexdigest()
        assert "_apmx_source/contract.contract.md" in entries
        assert "checks/check.py" in entries
        assert "apm.yml" not in entries
    assert compute_package_hash(package) == before
    assert not (caller / ".apm").exists()


@pytest.mark.parametrize("collision", ["checks/extra.py", "Checks/extra.py", "_apmx_source/anything"])
def test_caller_source_collisions_refused(caller, tmp_path, collision):
    package = _package(tmp_path / "job")
    target = caller / collision
    target.parent.mkdir()
    target.write_text("caller")
    with _prepare(package, caller) as source:
        plan = frontend.plan_contract(Path("job.contract.md"), caller, harness="copilot", source=source)
        with pytest.raises(ContractError, match="collide"):
            workspace.inspect_workspace(plan)


@pytest.mark.parametrize("path", ["../job.contract.md", "/job.contract.md", "checks/../job.contract.md"])
def test_package_source_escape_refused(caller, tmp_path, path):
    package = _package(tmp_path / "job")
    with pytest.raises(ContractError):
        with contract_source.prepare_contract_source(
            str(package), path, caller_root=caller, planning=True, limits=ContractLimits()
        ):
            pytest.fail("Escaping source admitted")


def test_package_symlink_refused(caller, tmp_path):
    package = _package(tmp_path / "job")
    (package / "escape").symlink_to(caller / "notes.md")
    with pytest.raises(ContractError, match="symlink"):
        with _prepare(package, caller):
            pytest.fail("Symlink source admitted")


@pytest.mark.parametrize("variable", ["APM_POLICY_DISABLE", "APM_NO_SCRIPTS"])
def test_no_policy_gate_precedes_remote_acquisition(caller, monkeypatch, variable):
    acquire = Mock(side_effect=AssertionError("network acquisition"))
    monkeypatch.setattr("apmx.install.apm_backend.install", acquire)
    monkeypatch.setenv(variable, "1")
    result = CliRunner().invoke(main, [
        "--from", "org/job", "job.contract.md", "--on", "copilot", "--allow-host-access",
    ])
    assert result.exit_code == 21, result.output
    acquire.assert_not_called()


def test_remote_plan_never_initializes_backend(caller, monkeypatch):
    acquire = Mock(side_effect=AssertionError("offline network"))
    monkeypatch.setattr("apmx.install.apm_backend.install", acquire)
    result = CliRunner().invoke(main, [
        "--from", "org/job#v1", "job.contract.md", "--on", "copilot", "--plan",
    ])
    assert result.exit_code == 21, result.output
    assert "unresolved" in result.output.lower()
    acquire.assert_not_called()
    assert not list(caller.glob(".apmx-source-*"))


def test_missing_direct_local_skill_materializes_native_transitive(caller, tmp_path):
    package = _package(tmp_path / "job", imports=True)
    _skill(tmp_path / "style")
    original = compute_package_hash(package)
    with _prepare(package, caller) as source:
        plan = frontend.plan_contract(Path("job.contract.md"), caller, harness="copilot", source=source)
        assert len(plan.imported_skills) == 1
        assert plan.imported_skills[0].name == "style"
        lock = LockFile.read(source.imports_root / "apm.lock.yaml")
        assert len(lock.dependencies) == 2
        assert sorted(item.depth for item in lock.dependencies.values()) == [1, 2]
        store = AttemptStore.create(plan)
        retained = json.loads(store.record_path.read_text())["source"]["retained"]
        assert Path(retained["apm.yml"]).read_bytes() == (package / "apm.yml").read_bytes()
        assert Path(retained["apm.lock.yaml"]).read_bytes() == (source.imports_root / "apm.lock.yaml").read_bytes()
        prepared = source.root
    assert not prepared.exists()
    assert Path(retained["contract.contract.md"]).is_file()
    assert compute_package_hash(package) == original
    assert not (package / "apm_modules").exists()
    assert not (caller / "apm.yml").exists()


def test_missing_direct_skill_plan_is_unresolved(caller, tmp_path):
    package = _package(tmp_path / "job", imports=True)
    _skill(tmp_path / "style")
    with pytest.raises(ContractError) as error:
        with _prepare(package, caller, planning=True):
            pytest.fail("Missing import planned")
    assert error.value.outcome == Outcome.UNPROVEN


@pytest.mark.parametrize("extra", ["companion.txt", "nested/SKILL.md"])
def test_unselected_companions_are_not_context(caller, tmp_path, extra):
    package = _package(tmp_path / "job", imports=True)
    skill = tmp_path / "style"
    _skill(skill)
    path = skill / extra
    path.parent.mkdir(exist_ok=True)
    path.write_text("UNSELECTED_SENTINEL")
    with _prepare(package, caller) as source:
        plan = frontend.plan_contract(Path("job.contract.md"), caller, harness="copilot", source=source)
        assert len(plan.imported_skills) == 1
        assert "UNSELECTED_SENTINEL" not in plan.imported_skills[0].content
        assert not plan.imported_skills[0].resources


def test_package_drift_revalidated_before_capture(caller, tmp_path):
    package = _package(tmp_path / "job")
    with _prepare(package, caller) as source:
        plan = frontend.plan_contract(Path("job.contract.md"), caller, harness="copilot", source=source)
        (package / "checks/check.py").write_text("raise SystemExit(0)\n")
        with pytest.raises(ContractError, match="changed"):
            workspace.inspect_workspace(plan)


def test_evidence_root_cannot_be_rebound(caller, tmp_path):
    package = _package(tmp_path / "job")
    with _prepare(package, caller) as source:
        plan = frontend.plan_contract(Path("job.contract.md"), caller, harness="copilot", source=source)
        with pytest.raises(ContractError, match="caller-owned"):
            AttemptStore.create(replace(plan, evidence_root=package / ".apm/runs"))


@pytest.fixture
def locked_remote_source(caller, tmp_path):
    package = _package(tmp_path / "download")
    dependency = DependencyReference.parse("org/repo#v1")
    installed = dependency.get_install_path(caller / "apm_modules")
    shutil.copytree(package, installed)
    (caller / "apm.yml").write_text(
        "name: caller\nversion: 1.0.0\ndependencies:\n  apm: [org/repo#v1]\n"
    )
    lock = LockFile()
    locked = LockedDependency.from_dependency_ref(dependency, "a" * 40, depth=1, resolved_by=None)
    locked.content_hash = compute_package_hash(installed)
    lock.add_dependency(locked)
    lock.write(caller / "apm.lock.yaml")
    return installed, lock


def test_installed_remote_plan_is_exact_and_read_only(caller, locked_remote_source, monkeypatch):
    installed, _ = locked_remote_source
    backend = Mock(side_effect=AssertionError("offline acquisition"))
    monkeypatch.setattr("apmx.install.apm_backend.install", backend)
    with contract_source.prepare_contract_source(
        "org/repo#v1", "job.contract.md", caller_root=caller, planning=True, limits=ContractLimits()
    ) as source:
        assert source.root == installed
        assert source.resolved_commit == "a" * 40
        assert source.assurance == "locked-package-hash"
    backend.assert_not_called()


@pytest.mark.parametrize("planning", [True, False])
@pytest.mark.parametrize("tamper", ["installed_hash", "declared_ref", "requested_ref", "lock_commit", "lock_missing"])
def test_remote_caller_pin_drift_never_falls_back(caller, locked_remote_source, monkeypatch, planning, tamper):
    installed, lock = locked_remote_source
    requested = "org/repo#v1"
    if tamper == "installed_hash":
        (installed / "notes.md").write_text("TAMPERED\n")
    elif tamper == "declared_ref":
        manifest = caller / "apm.yml"
        manifest.write_text(manifest.read_text().replace("#v1", "#v2"))
    elif tamper == "requested_ref":
        requested = "org/repo#v2"
    elif tamper == "lock_commit":
        lock.get_dependency("org/repo").resolved_commit = "unknown"
        lock.write(caller / "apm.lock.yaml")
    else:
        LockFile().write(caller / "apm.lock.yaml")
    backend = Mock(side_effect=AssertionError("Drift cannot trigger fresh acquisition"))
    monkeypatch.setattr("apmx.install.apm_backend.install", backend)
    before = compute_package_hash(caller)
    with pytest.raises(ContractError):
        with contract_source.prepare_contract_source(
            requested, "job.contract.md", caller_root=caller, planning=planning, limits=ContractLimits()
        ):
            pytest.fail("Caller source drift admitted")
    backend.assert_not_called()
    assert compute_package_hash(caller) == before


@pytest.mark.parametrize("extra", ["scripts: {start: echo nope}", "dependencies: {mcp: [server]}"])
def test_package_activation_is_not_forwarded(caller, tmp_path, extra):
    package = _package(tmp_path / "job")
    with (package / "apm.yml").open("a") as stream:
        stream.write(extra + "\n")
    with _prepare(package, caller) as source:
        plan = frontend.plan_contract(Path("job.contract.md"), caller, harness="copilot", source=source)
        assert not plan.imported_skills
        assert not (source.imports_root / ".mcp.json").exists()
        assert not (source.imports_root / ".github").exists()
    assert not (caller / ".apm").exists()


def test_missing_package_manifest_refuses(caller, tmp_path):
    package = _package(tmp_path / "job")
    (package / "apm.yml").unlink()
    with pytest.raises(ContractError, match=r"apm\.yml"):
        with _prepare(package, caller):
            pytest.fail("Manifestless package admitted")


def test_local_import_symlink_refuses(caller, tmp_path):
    package = _package(tmp_path / "job", imports=True)
    _skill(tmp_path / "real-style")
    (tmp_path / "style").symlink_to(tmp_path / "real-style", target_is_directory=True)
    with pytest.raises(ContractError, match="symlink"):
        with _prepare(package, caller) as source:
            frontend.plan_contract(Path("job.contract.md"), caller, harness="copilot", source=source)


def test_package_lock_is_not_rewritten_as_consumer_lock(caller, tmp_path):
    package = _package(tmp_path / "job", imports=True)
    _skill(tmp_path / "style")
    lock = LockFile()
    lock.add_dependency(LockedDependency.from_dependency_ref(
        DependencyReference.parse("../style"), None, depth=2, resolved_by="parent/repo"
    ))
    lock.write(package / "apm.lock.yaml")
    original = compute_package_hash(package)
    with _prepare(package, caller) as source:
        plan = frontend.plan_contract(Path("job.contract.md"), caller, harness="copilot", source=source)
        assert plan.imported_skills
    assert compute_package_hash(package) == original


def test_package_cleanup_failure_halts_after_completed_invocation(caller, tmp_path, monkeypatch):
    package = _package(tmp_path / "package", imports=True)
    _skill(tmp_path / "style")
    invoked = []
    def completed(ctx, *args, **kwargs):
        invoked.append(kwargs["source"].root)
        ctx.exit(int(Outcome.UNPROVEN))
    monkeypatch.setattr("apmx.cli.invoke_contract", completed)
    monkeypatch.setattr(contract_source, "safe_rmtree", Mock(side_effect=PermissionError("private fixture path")))
    result = CliRunner().invoke(main, [
        "job.contract.md", "--from", str(package), "--on", "copilot", "--allow-host-access",
    ])
    assert invoked
    assert result.exit_code == Outcome.HALTED, result.output
    assert "Temporary package source cleanup failed" in result.output
    assert "private fixture path" not in result.output
