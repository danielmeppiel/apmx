"""The setup and attest composite actions: verified install, pinned dependencies."""

import hashlib
import importlib.util
import io
import json
import re
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from apmx.utils.yaml_io import load_yaml_str

ROOT = Path(__file__).resolve().parents[2]
ACTIONS = ROOT / ".github/actions"


def _installer():
    spec = importlib.util.spec_from_file_location("apmx_setup", ACTIONS / "setup/install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


install = _installer()


def _release(directory: Path, version: str, target: str, members: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    name = install.archive_name(version, target)
    archive = directory / name
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive, "w") as stream:
            for member, data in members.items():
                stream.writestr(member, data)
    else:
        with tarfile.open(archive, "w:gz") as stream:
            for member, data in members.items():
                info = tarfile.TarInfo(member)
                if isinstance(data, tuple):
                    info.type, info.linkname = tarfile.SYMTYPE, data[1]
                    stream.addfile(info)
                    continue
                info.size, info.mode = len(data), 0o755
                stream.addfile(info, io.BytesIO(data))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (directory / f"{name}.sha256").write_text(f"{digest}  {name}\n", encoding="ascii")
    (directory / "release-manifest.json").write_text(
        json.dumps({"schema": "apmx-release/1", "version": version, "archives": {name: digest}})
    )
    return archive


def _install(tmp_path, target="linux-x86_64", **kwargs):
    return install.install(
        "0.5.0",
        source=str(tmp_path / "release"),
        work=tmp_path / "work",
        tools=tmp_path / "tools",
        target_name=target,
        **kwargs,
    )


@pytest.mark.parametrize("target", ["linux-x86_64", "windows-x86_64"])
def test_verified_archive_is_installed(tmp_path, target) -> None:
    executable = "apmx.exe" if target.startswith("windows") else "apmx"
    archive = _release(
        tmp_path / "release", "0.5.0", target, {f"apmx-{target}/{executable}": b"binary"}
    )
    bundle, digest = _install(tmp_path, target)
    assert bundle == tmp_path / "tools/apmx/0.5.0" / target / f"apmx-{target}"
    assert (bundle / executable).read_bytes() == b"binary"
    assert digest == hashlib.sha256(archive.read_bytes()).hexdigest()
    _install(tmp_path, target, pinned=digest.upper())


def test_checksum_manifest_and_pin_must_all_agree(tmp_path) -> None:
    archive = _release(
        tmp_path / "release", "0.5.0", "linux-x86_64", {"apmx-linux-x86_64/apmx": b"x"}
    )
    with pytest.raises(install.SetupError, match="pinned sha256"):
        _install(tmp_path, pinned="0" * 64)
    manifest = tmp_path / "release/release-manifest.json"
    original = manifest.read_text()
    manifest.write_text(original.replace('"0.5.0"', '"0.4.2"'))
    with pytest.raises(install.SetupError, match="does not describe this version"):
        _install(tmp_path)
    manifest.write_text(original)
    archive.write_bytes(archive.read_bytes() + b"tampered")
    with pytest.raises(install.SetupError, match=r"does not match .*\.sha256"):
        _install(tmp_path)


@pytest.mark.parametrize(
    "member", ["../escape", "/etc/passwd", "other-root/apmx", "apmx-linux-x86_64/../../x"]
)
def test_archive_members_outside_the_bundle_are_refused(tmp_path, member) -> None:
    _release(
        tmp_path / "release",
        "0.5.0",
        "linux-x86_64",
        {"apmx-linux-x86_64/apmx": b"x", member: b"evil"},
    )
    with pytest.raises(install.SetupError, match="refusing archive member"):
        _install(tmp_path)


@pytest.mark.skipif(sys.platform == "win32", reason="macOS archive symlinks need POSIX links")
def test_internal_symlinks_install_but_escaping_links_are_refused(tmp_path) -> None:
    root = "apmx-macos-arm64"
    members = {
        f"{root}/apmx": b"x",
        f"{root}/_internal/Python.framework/Python": b"lib",
        f"{root}/_internal/Python": ("link", "Python.framework/Python"),
    }
    _release(tmp_path / "release", "0.5.0", "macos-arm64", members)
    bundle, _ = _install(tmp_path, "macos-arm64")
    assert (bundle / "_internal/Python").read_bytes() == b"lib"
    members[f"{root}/_internal/escape"] = ("link", "../../../../../../etc/passwd")
    _release(tmp_path / "release2", "0.5.0", "macos-arm64", members)
    with pytest.raises(tarfile.TarError):
        install.install(
            "0.5.0",
            source=str(tmp_path / "release2"),
            work=tmp_path / "work2",
            tools=tmp_path / "tools2",
            target_name="macos-arm64",
        )


def test_runner_targets_and_versions() -> None:
    assert install.target("Linux", "x86_64") == "linux-x86_64"
    assert install.target("Linux", "aarch64") == "linux-arm64"
    assert install.target("Darwin", "arm64") == "macos-arm64"
    assert install.target("Windows", "AMD64") == "windows-x86_64"
    with pytest.raises(install.SetupError):
        install.target("Windows", "ARM64")
    with pytest.raises(install.SetupError, match="invalid apmx version"):
        install.install("latest", source=".", work=Path("."), tools=Path("."))


def test_action_archive_names_match_the_release_tooling() -> None:
    spec = importlib.util.spec_from_file_location("apmx_release", ROOT / "scripts/release.py")
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    for target in release.TARGETS:
        assert install.archive_name("0.5.0", target) == release.archive_name("0.5.0", target)
        system, _, machine = target.partition("-")
        os_name = {"linux": "Linux", "macos": "Darwin", "windows": "Windows"}[system]
        assert install.target(os_name, machine) == target


def test_actions_pin_third_party_steps_and_keep_privilege_out_of_setup() -> None:
    for name in ("setup", "attest"):
        action = load_yaml_str((ACTIONS / name / "action.yml").read_text())
        assert action["runs"]["using"] == "composite"
        for step in action["runs"]["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", step["uses"]), step["uses"]
    attest = (ACTIONS / "attest/action.yml").read_text()
    assert "apmx audit --offline" in attest and "predicate.py" in attest
    setup = (ACTIONS / "setup/action.yml").read_text()
    assert "id-token" not in setup and "install.py" in setup


def _workflow(name: str) -> dict:
    return load_yaml_str((ROOT / ".github/workflows" / name).read_text())


def test_run_workflow_keeps_the_agent_away_from_checks_and_signing() -> None:
    run = _workflow("run.yml")
    jobs = run["jobs"]
    assert run["permissions"] == {}
    assert jobs["produce"]["permissions"] == {"contents": "read"}
    assert jobs["check"]["permissions"] == {"contents": "read"}
    assert jobs["attest"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
        "attestations": "write",
    }
    assert jobs["check"]["needs"] == "produce" and jobs["attest"]["needs"] == "check"
    text = (ROOT / ".github/workflows/run.yml").read_text()
    # Only the produce job sees the agent token or runs the factory with an agent.
    for name in ("check", "attest"):
        job = str(jobs[name])
        assert "agent-token" not in job and "copilot" not in job.lower()
    assert text.count("secrets.agent-token") == 1
    # The check job replays outputs; the attest job never checks out the caller's code.
    assert "apmx check" in str(jobs["check"]["steps"])
    for step in jobs["attest"]["steps"]:
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            assert step["with"]["repository"] == "danielmeppiel/apmx"
    assert any(
        step.get("uses") == "./apmx/.github/actions/attest"
        and step["with"]["checks"] == "separate-job"
        for step in jobs["attest"]["steps"]
    )


def test_workflows_pin_every_third_party_action() -> None:
    for path in [*(ROOT / ".github/workflows").glob("*.yml"), *ACTIONS.glob("*/action.yml")]:
        document = load_yaml_str(path.read_text())
        steps = [
            step for job in (document.get("jobs") or {}).values() for step in job.get("steps", [])
        ] + list((document.get("runs") or {}).get("steps", []))
        for step in steps:
            uses = step.get("uses")
            if uses and not uses.startswith("./"):
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", uses), (path.name, uses)
