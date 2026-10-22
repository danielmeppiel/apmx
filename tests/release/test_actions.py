"""The setup and attest composite actions: verified install, pinned dependencies."""

import hashlib
import importlib.util
import io
import json
import re
import sys
import tarfile
import zipfile
from datetime import UTC
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
    assert "subject-checksums" in attest
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
    assert jobs["check"]["needs"] == ["target", "produce"] and jobs["attest"]["needs"] == "check"
    text = (ROOT / ".github/workflows/run.yml").read_text()
    # Only the produce job sees the agent token or runs the factory with an agent.
    for name in ("target", "check", "attest", "deliver"):
        job = str(jobs[name])
        assert "agent-token" not in job and "copilot" not in job.lower()
    assert text.count("secrets.agent-token") == 1
    # The check job replays outputs; the attest job never checks out the caller's code.
    assert "apmx check" in str(jobs["check"]["steps"])
    for step in jobs["attest"]["steps"]:
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            assert step["with"]["repository"] == "danielmeppiel/apmx"
    assert any(
        step.get("uses") == "./apmx/.github/actions/attest" for step in jobs["attest"]["steps"]
    )


def _secrets(job: dict) -> set[str]:
    return set(re.findall(r"secrets\.([\w-]+)", str(job)))


def test_target_credentials_never_reach_the_agent() -> None:
    """Platform mode: the target credential lives only in jobs that run no agent and no candidate code."""
    run = _workflow("run.yml")
    jobs = run["jobs"]
    declared = (run.get("on") or run[True])["workflow_call"]["secrets"]
    assert set(declared) == {"agent-token", "target-app-private-key", "target-token"}
    assert {name: _secrets(job) for name, job in jobs.items()} == {
        "target": {"target-app-private-key", "target-token"},
        "produce": {"agent-token"},
        "check": set(),
        "attest": set(),
        "deliver": {"target-app-private-key", "target-token"},
    }
    # The agent's job gets the target tree as an artifact: no target checkout, no token.
    produce = jobs["produce"]
    assert produce["needs"] == "target" and produce["permissions"] == {"contents": "read"}
    for step in produce["steps"]:
        uses = str(step.get("uses", ""))
        if uses.startswith("actions/checkout@"):
            assert "token" not in step["with"] and step["with"]["persist-credentials"] is False
            assert step["with"].get("repository") in (None, "danielmeppiel/apmx")
    assert "target-repository" not in str(produce.get("env", {}))
    assert "run_stage.py credentials" in str(produce["steps"])
    # The target job fetches without persisting credentials and runs no apmx and no agent.
    target = jobs["target"]
    (checkout,) = [
        s for s in target["steps"] if str(s.get("uses", "")).startswith("actions/checkout@")
    ]
    assert checkout["with"]["persist-credentials"] is False
    assert "tar --exclude=./.git" in str(target["steps"])
    for name in ("target", "deliver"):
        steps = str(jobs[name]["steps"])
        assert "apmx " not in steps.replace("apmx/", "") and "copilot" not in steps.lower()
        assert "setup" not in steps and "requirements" not in steps
        assert jobs[name]["permissions"] == {"contents": "read"}
    # App tokens are minted per job and scoped to the one target repository.
    minted = {
        name: step["with"]
        for name in ("target", "deliver")
        for step in jobs[name]["steps"]
        if str(step.get("uses", "")).startswith("actions/create-github-app-token@")
    }
    assert minted["target"]["permission-contents"] == "read"
    assert "permission-pull-requests" not in minted["target"]
    assert minted["deliver"]["permission-contents"] == "write"
    assert minted["deliver"]["permission-pull-requests"] == "write"
    for values in minted.values():
        assert values["repositories"] == "${{ steps.split.outputs.name }}"
        assert not any(key.startswith("permission-workflows") for key in values)
    deliver = jobs["deliver"]
    assert deliver["needs"] == ["target", "check", "attest"]
    assert "inputs.target-repository != ''" in deliver["if"]
    assert "run_deliver.py deliver" in str(deliver["steps"])


def _job_text(name: str) -> str:
    """The raw run.yml text of one job (env, with: and run: included)."""
    text = (ROOT / ".github/workflows/run.yml").read_text()
    start = text.index(f"\n  {name}:\n")
    following = re.search(r"\n  [\w-]+:\n", text[start + 1 :])
    return text[start : start + 1 + following.start()] if following else text[start:]


def test_produce_and_check_never_reference_target_secrets() -> None:
    """The agent's job and the replay job hold no App key and no target token, anywhere."""
    for name in ("produce", "check"):
        text = _job_text(name)
        assert "secrets.target-" not in text and "target-app-private-key" not in text, name
        assert "create-github-app-token" not in text and "target-token" not in text, name
    assert "secrets.agent-token" in _job_text("produce")
    assert "secrets.agent-token" not in _job_text("check")
    # Each secret is referenced only by the steps that use it.
    jobs = _workflow("run.yml")["jobs"]
    users = {
        name: [
            step.get("uses", step.get("name", "")).split("@")[0]
            for step in job["steps"]
            if "secrets." in str(step)
        ]
        for name, job in jobs.items()
    }
    assert users == {
        "target": ["actions/create-github-app-token", "actions/checkout"],
        "produce": ["Run the factory (agent; untrusted outputs)"],
        "check": [],
        "attest": [],
        "deliver": [
            "actions/create-github-app-token",
            "Push the delivered files and open or update the pull request (runs no candidate code)",
        ],
    }


def test_deliver_never_executes_candidate_code() -> None:
    """Deliver checks out only apmx's own scripts, installs nothing and runs only run_deliver.py."""
    deliver = _workflow("run.yml")["jobs"]["deliver"]
    actions = {str(step["uses"]).split("@")[0] for step in deliver["steps"] if "uses" in step}
    assert actions == {
        "actions/checkout",
        "actions/create-github-app-token",
        "actions/download-artifact",
        "actions/upload-artifact",
    }
    for step in deliver["steps"]:
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            assert step["with"]["repository"] == "danielmeppiel/apmx"
            assert step["with"]["ref"] == "${{ inputs.apmx-ref }}"
    runs = [step["run"] for step in deliver["steps"] if "run" in step]
    assert len(runs) == 2 and runs[0].lstrip().startswith("{ echo")
    assert runs[1].strip().startswith("python3 apmx/scripts/run_deliver.py deliver")
    for word in ("pip", "npm", "uv ", "make", "setup", "apmx check", "apmx audit", "bash ", "sh "):
        assert word not in " ".join(runs), word


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


def test_published_action_refs_and_defaults_name_this_release() -> None:
    """Documented setup/attest/run.yml refs and their defaults resolve to this release's tag."""
    import tomllib

    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    setup = load_yaml_str((ACTIONS / "setup/action.yml").read_text())
    assert setup["inputs"]["version"]["default"] == version
    run = _workflow("run.yml")
    run_inputs = (run.get("on") or run[True])["workflow_call"]["inputs"]
    assert run_inputs["apmx-version"]["default"] == version
    assert run_inputs["apmx-ref"]["default"] == f"v{version}"
    documents = [
        ROOT / "README.md",
        *(ROOT / "docs").glob("*.md"),
        *(ROOT / "examples/accept-factory-prs").iterdir(),
        *(ROOT / "examples/factory-platform").iterdir(),
    ]
    refs = set()
    for path in documents:
        refs.update(
            re.findall(
                r"danielmeppiel/apmx/\.github/(?:actions/\w+|workflows/run\.yml)@([\w./-]*\w)",
                path.read_text(),
            )
        )
    assert refs and refs <= {f"v{version}", f"refs/tags/v{version}"}


def _predicate():
    spec = importlib.util.spec_from_file_location("apmx_predicate", ACTIONS / "attest/predicate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GOLDEN = ROOT / "tests/unit/contracts/golden/receipt-v2/receipt.intoto.jsonl"


def test_the_bundle_attestation_carries_every_line_in_order() -> None:
    from apmx.audit import verifier

    predicate = _predicate()
    receipt, _ = verifier.inspect(GOLDEN)
    carried = predicate.bundle_predicate(GOLDEN)["lines"]
    assert [verifier.line_digest(verifier.normalized(item)) for item in carried] == (
        receipt.facts["lineDigests"]
    )
    assert predicate.BUNDLE_TYPE == verifier.BUNDLE_TYPE
    attest = (ACTIONS / "attest/action.yml").read_text()
    assert attest.count("actions/attest@") == 2 and verifier.BUNDLE_TYPE in attest


def test_summary_is_a_slsa_vsa_binding_every_line(tmp_path) -> None:
    from datetime import datetime

    from apmx.audit import verifier

    predicate = _predicate()
    receipt, failure = verifier.inspect(GOLDEN)
    assert failure is None
    report = {"facts": receipt.facts}
    claims = {
        "job_workflow_ref": "acme/platform/.github/workflows/run.yml@refs/tags/v1",
        "job_workflow_sha": "c" * 40,
    }
    document = predicate.summary(
        GOLDEN,
        prefix=predicate.prefix_of("app"),
        verifier=predicate.verifier_id(claims),
        resource=predicate.resource_uri({"GITHUB_REPOSITORY": "acme/shop", "GITHUB_SHA": "a" * 40}),
        report=report,
        now=datetime(2026, 10, 21, tzinfo=UTC),
    )
    body = document["predicate"]
    assert document["predicateType"] == "https://slsa.dev/verification_summary/v1"
    assert body["verifier"]["id"] == (
        "https://github.com/acme/platform/.github/workflows/run.yml@" + "c" * 40
    )
    assert body["verificationResult"] == "PASSED" and body["verifiedLevels"] == []
    assert body["policy"]["digest"]["sha256"] == receipt.definition_sha256
    assert [item["digest"]["sha256"] for item in body["inputAttestations"]] == (
        receipt.facts["lineDigests"]
    )
    assert document["subject"][0]["name"] == "app/docs/checkout.md"
    combined = verifier.bundle_bytes([*receipt.statements, verifier.normalized(document)])
    bundle = tmp_path / "receipt.intoto.jsonl"
    bundle.write_bytes(combined)
    parsed, failure = verifier.inspect(bundle)
    assert failure is None, failure
    assert parsed.facts["subjectPrefix"] == "app/"
    with pytest.raises(ValueError, match="unsafe"):
        predicate.prefix_of("../x")
    with pytest.raises(ValueError, match="no job workflow"):
        predicate.verifier_id({"job_workflow_ref": "x", "job_workflow_sha": "y"})
