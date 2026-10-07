"""The demo helper prepares environments; it never substitutes agent execution."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import demo

pytestmark = pytest.mark.skipif(os.name == "nt", reason="The demo shell is POSIX-only")
REAL_INSTALL = demo.install


@pytest.fixture
def kit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "demo with spaces"
    private = root / ".demo"
    private.mkdir(parents=True)
    (root / "factory").mkdir()
    (root / "factory/apm.yml").write_text("name: demo\n")
    (private / "seed/src").mkdir(parents=True)
    (private / "seed/request.md").write_text("Change the software.\n")
    (private / "seed/src/app.py").write_text("value = 1\n")
    (private / "native/apmx-test/libexec/apm").mkdir(parents=True)
    (private / "native/apmx-test/apmx").write_bytes(b"native-apmx")
    (private / "native/apmx-test/libexec/apm/apm").write_bytes(b"native-apm")
    (private / "bin").mkdir()
    (private / "bin/apmx").symlink_to("../native/apmx-test/apmx")
    (root / "history").mkdir()
    (private / "logs").mkdir()
    (private / "lock").touch()
    config = {
        "schema": demo.SCHEMA,
        "root": str(root.resolve()),
        "python": sys.executable,
        "checker": "behave==1.3.3",
        "source_commit": "a" * 40,
        "archive_sha256": "b" * 64,
        "binary_sha256": demo.digest(private / "native/apmx-test/apmx"),
        "backend_sha256": demo.digest(private / "native/apmx-test/libexec/apm/apm"),
        "factory": demo.inventory(root / "factory"),
        "seed": demo.inventory(private / "seed"),
        "workspaces": {},
    }
    demo.save(root, config)
    monkeypatch.setattr(demo, "check_python", lambda *args: None)
    monkeypatch.setattr(demo, "check_harnesses", lambda: None)
    monkeypatch.setattr(demo, "install", lambda *args: None)
    monkeypatch.setattr(demo, "preview", lambda *args: None)
    demo.reset(root, list(demo.HARNESSES))
    return root


def test_reset_preserves_evidence_and_recreates_identical_consumers(kit: Path) -> None:
    old = kit / "checkout-copilot"
    (old / ".apm/chains/run").mkdir(parents=True)
    evidence = old / ".apm/chains/run/record.json"
    evidence.write_bytes(b'{"private": "original evidence"}\n')
    (old / "src/app.py").write_text("value = 99\n")
    other = (kit / "checkout-opencode").stat().st_ino

    demo.reset(kit, ["copilot"])

    retained = list((kit / "history").glob("*/checkout-copilot/.apm/chains/run/record.json"))
    assert len(retained) == 1
    assert retained[0].read_bytes() == b'{"private": "original evidence"}\n'
    assert (kit / "checkout-copilot/src/app.py").read_text() == "value = 1\n"
    assert (kit / "checkout-opencode").stat().st_ino == other
    assert demo.load(kit)["workspaces"]["copilot"]["ready"] is True


def test_clean_archives_without_deleting_and_reset_relaunches(kit: Path) -> None:
    demo.clean(kit, list(demo.HARNESSES))
    assert not (kit / "checkout-copilot").exists()
    assert not (kit / "checkout-opencode").exists()
    assert len(list((kit / "history").glob("*/checkout-*"))) == 2
    demo.reset(kit, list(demo.HARNESSES))
    assert demo.load(kit)["workspaces"]["opencode"]["ready"] is True
    assert len(list((kit / "history").glob("*/checkout-*"))) == 2


@pytest.mark.parametrize("operation", [demo.clean, demo.reset])
def test_active_shell_blocks_mutation(kit: Path, operation) -> None:
    with demo.locked(kit, exclusive=False), pytest.raises(ValueError, match="Close"):
        operation(kit, ["copilot"])
    assert (kit / "checkout-copilot").is_dir()
    assert not list((kit / "history").iterdir())


@pytest.mark.parametrize("name", ["checkout-copilot", "history", ".demo"])
def test_refuse_symlinked_owned_directories(kit: Path, name: str) -> None:
    path = kit / name
    saved = kit / (name + "-saved")
    path.rename(saved)
    path.symlink_to(saved, target_is_directory=True)
    with pytest.raises(ValueError, match="directory"):
        demo.clean(kit, ["copilot"])
    assert saved.is_dir()


def test_refuse_replaced_workspace_even_at_the_same_path(kit: Path) -> None:
    workspace = kit / "checkout-copilot"
    workspace.rename(kit / "original")
    workspace.mkdir()
    (workspace / "unrelated").write_text("Do not move me")
    with pytest.raises(ValueError, match="ownership"):
        demo.clean(kit, ["copilot"])
    assert (workspace / "unrelated").read_text() == "Do not move me"


def test_validate_all_workspaces_before_archiving_any(kit: Path) -> None:
    (kit / "checkout-opencode").rename(kit / "untracked")
    with pytest.raises(ValueError, match="workspace"):
        demo.clean(kit, list(demo.HARNESSES))
    assert (kit / "checkout-copilot").is_dir()
    assert not list((kit / "history").iterdir())


@pytest.mark.parametrize("relative", ["factory/apm.yml", ".demo/seed/request.md"])
def test_changed_factory_or_seed_refuses_reset(kit: Path, relative: str) -> None:
    (kit / relative).write_text("Changed input")
    with pytest.raises(ValueError, match="changed"):
        demo.reset(kit, ["copilot"])
    assert (kit / "checkout-copilot").is_dir()


def test_failed_preparation_keeps_history_and_blocks_launch(
    kit: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args) -> None:
        raise ValueError("APM preparation failed")

    monkeypatch.setattr(demo, "install", fail)
    with pytest.raises(ValueError, match="APM preparation"):
        demo.reset(kit, ["copilot"])
    assert len(list((kit / "history").glob("*/checkout-copilot"))) == 1
    assert demo.load(kit)["workspaces"]["copilot"]["ready"] is False
    with pytest.raises(ValueError, match="reset"):
        demo.shell(kit, "copilot")


def test_shell_runs_real_binary_without_hidden_consent_or_models(
    kit: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        with pytest.raises(ValueError, match="Close"):
            demo.clean(kit, ["copilot"])
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(demo.subprocess, "run", run)
    monkeypatch.setenv("PYTHONPATH", "/unrelated/python")
    monkeypatch.setenv("APMX_APM_BACKEND", "/unrelated/apm")
    assert demo.shell(kit, "copilot") == 0
    assert len(calls) == 1
    argv, options = calls[0]
    assert argv == ["/bin/bash", "--noprofile", "--norc", "-i"]
    assert options["cwd"] == kit / "checkout-copilot"
    assert options["env"]["PATH"].split(os.pathsep)[0] == str(kit / ".demo/bin")
    assert options["env"]["PATH"].split(os.pathsep)[1] == str(Path(sys.executable).parent)
    assert "APMX_APM_BACKEND" not in options["env"]
    assert "PYTHONPATH" not in options["env"]
    assert "APM_NO_SCRIPTS" not in options["env"]


def test_inherited_script_bypass_is_not_silently_used(
    kit: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APM_NO_SCRIPTS", "1")
    with pytest.raises(ValueError, match="APM_NO_SCRIPTS"):
        demo.shell(kit, "opencode")


def test_install_uses_only_official_backend_and_scopes_no_scripts(
    kit: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def run(argv, **kwargs):
        manifest = json.loads((kit / "checkout-copilot/apm.yml").read_text())
        assert manifest["name"] == "checkout-demo"
        assert manifest["dependencies"]["apm"] == [str(kit / "factory")]
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(demo.subprocess, "run", run)
    REAL_INSTALL(kit, "copilot")
    argv, options = calls[0]
    assert argv[1:] == [
        "install",
        str(kit / "factory"),
        "--root",
        str(kit / "checkout-copilot"),
        "--only",
        "apm",
        "--target",
        "agent-skills",
        "--no-trust-bin",
    ]
    assert Path(argv[0]) == kit / ".demo/native/apmx-test/libexec/apm/apm"
    assert options["env"]["APM_NO_SCRIPTS"] == "1"
    assert options["timeout"] == 300


def test_prepare_refuses_existing_directory_before_installing(tmp_path: Path) -> None:
    (tmp_path / "important").write_text("Untouched")
    with pytest.raises(ValueError, match="fresh"):
        demo.prepare(tmp_path, Path("missing.tar.gz"), "a" * 64, Path(sys.executable))
    assert (tmp_path / "important").read_text() == "Untouched"


def test_install_preserves_an_existing_consumer_manifest(kit: Path, monkeypatch) -> None:
    manifest = kit / "checkout-copilot/apm.yml"
    original = b"name: existing-project\nversion: 2.0.0\n"
    manifest.write_bytes(original)
    monkeypatch.setattr(
        demo.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0)
    )
    REAL_INSTALL(kit, "copilot")
    assert manifest.read_bytes() == original


@pytest.mark.component
def test_real_backend_prepares_manifest_and_lock_together(kit: Path, monkeypatch) -> None:
    from apmx.install.apm_backend import locate_backend
    from apmx.utils.yaml_io import load_yaml_str

    backend = locate_backend()
    real_run = subprocess.run

    def run(argv, **kwargs):
        return real_run([str(backend), *argv[1:]], **kwargs)

    monkeypatch.setattr(demo.subprocess, "run", run)
    (kit / "factory/apm.yml").write_text("name: demo\nversion: 1.0.0\n")
    skill = kit / "factory/.apm/skills/example/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: example\ndescription: Local fixture.\n---\nUse tests.\n")
    REAL_INSTALL(kit, "copilot")
    manifest = load_yaml_str((kit / "checkout-copilot/apm.yml").read_text())
    assert manifest["name"] == "checkout-demo"
    assert manifest["dependencies"]["apm"] == [str(kit / "factory")]
    assert (kit / "checkout-copilot/apm.lock.yaml").is_file()


def test_moved_or_unmarked_kit_is_not_owned(kit: Path) -> None:
    config = json.loads((kit / ".demo/kit.json").read_text())
    config["root"] = str(kit.parent / "elsewhere")
    demo.save(kit, config)
    with pytest.raises(ValueError, match="location"):
        demo.load(kit)


def test_input_copy_refuses_symlinks(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "link").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="regular"):
        demo.copy_inputs(source, tmp_path / "destination")


def test_prepare_verifies_archive_before_creating_owned_kit(
    kit: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    from scripts import release

    archive = kit.parent / "candidate.tar.gz"
    archive.write_bytes(b"verified archive fixture")
    expected = demo.digest(archive)
    archive.with_name(archive.name + ".sha256").write_text(f"{expected}  {archive.name}\n")
    destination = kit.parent / "new kit with spaces"
    with pytest.raises(ValueError, match="checksum"):
        demo.prepare(destination, archive, "0" * 64, Path(sys.executable))
    assert not destination.exists()

    def extract(source: Path, target: Path) -> Path:
        assert source == archive
        shutil.copytree(kit / ".demo/native", target)
        native = target / "apmx-test"
        (native / "RELEASE.json").write_text(
            json.dumps({"target": release.native_target(), "source_commit": "c" * 40})
        )
        return native

    monkeypatch.setattr(release, "extract_archive", extract)
    monkeypatch.setattr(release, "check_native_notices", lambda *args: None)
    demo.prepare(destination, archive, expected, Path(sys.executable))
    config = demo.load(destination)
    assert config["archive_sha256"] == expected
    assert config["source_commit"] == "c" * 40
    assert config["factory"] == demo.inventory(demo.ROOT / "examples/contracts/software-factory")
    assert (destination / "START-HERE.md").is_file()
    assert (destination / ".demo/bin/apmx").resolve() == destination / ".demo/native/apmx-test/apmx"
    result = subprocess.run(
        [str(destination / "demo"), "--help"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
    assert "copilot" in result.stdout and "reset" in result.stdout


def test_unknown_file_in_unregistered_workspace_is_never_overwritten(kit: Path) -> None:
    demo.clean(kit, ["copilot"])
    (kit / "checkout-copilot").mkdir()
    (kit / "checkout-copilot/notes").write_text("Not owned")
    with pytest.raises(ValueError, match="Unowned"):
        demo.reset(kit, ["copilot"])
    assert (kit / "checkout-copilot/notes").read_text() == "Not owned"


def test_python_command_preserves_virtual_environment_identity(kit: Path) -> None:
    import shutil
    import venv

    checker = kit.parent / "isolated-checkers"
    venv.EnvBuilder(with_pip=False).create(checker)
    config = demo.load(kit)
    config["python"] = str(checker / "bin/python")
    demo.save(kit, config)
    env = demo.environment(kit)
    executable = shutil.which("python3", path=env["PATH"])
    assert executable == str(checker / "bin/python3")
    result = subprocess.run(
        [executable, "-I", "-c", "import sys; print(sys.prefix)"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert Path(result.stdout.strip()) == checker


def test_copy_failure_is_owned_and_a_later_reset_recovers(
    kit: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = kit / "checkout-copilot"
    (old / "valuable-result").write_text("Keep this")

    def fail(*args) -> None:
        raise OSError("Injected copy failure")

    with monkeypatch.context() as patch:
        patch.setattr(demo.shutil, "copy2", fail)
        with pytest.raises(OSError, match="Injected"):
            demo.reset(kit, ["copilot"])
    assert demo.load(kit)["workspaces"]["copilot"]["ready"] is False
    demo.reset(kit, ["copilot"])
    assert demo.load(kit)["workspaces"]["copilot"]["ready"] is True
    preserved = list((kit / "history").glob("*/checkout-copilot/valuable-result"))
    assert len(preserved) == 1 and preserved[0].read_text() == "Keep this"
    assert len(list((kit / "history").glob("*/checkout-copilot"))) == 2
