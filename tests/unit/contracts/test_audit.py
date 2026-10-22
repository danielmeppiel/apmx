"""`apmx audit`: row-by-row receipt verification with APM-owned dependency trust."""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner
from test_apmx import _package, _skill, caller, producer
from test_execution_result import _execute

from apmx.audit import command, verifier
from apmx.cli import main
from apmx.contracts import evidence
from apmx.contracts.models import ContractError, Outcome
from apmx.install import apm_backend

__all__ = ["caller", "producer"]
pytestmark = pytest.mark.component
ROOT = Path(__file__).resolve().parents[3]


def _backend_available() -> bool:
    try:
        apm_backend.locate_backend()
    except ContractError:
        return False
    return True


needs_backend = pytest.mark.skipif(
    not _backend_available(), reason="The pinned APM backend is not provisioned."
)


@pytest.fixture(autouse=True)
def _no_ambient_policy_or_pull_request(tmp_path_factory, monkeypatch) -> None:
    """Audit from a directory with no apm-policy.yml, no git remote and no PR event."""
    for name in ("GITHUB_ACTIONS", "GITHUB_EVENT_NAME", "GITHUB_EVENT_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path_factory.mktemp("audit-cwd"))


@pytest.fixture
def plain_receipt(tmp_path, monkeypatch) -> Path:
    _, result = _execute(tmp_path, monkeypatch)
    return evidence.export_package(result.run_directory / "record.json", tmp_path / "receipt")


def _lines(bundle: Path) -> list[dict]:
    return [
        json.loads(base64.b64decode(json.loads(line)["payload"]))
        for line in bundle.read_bytes().splitlines()
    ]


def _edit_line(bundle: Path, index: int, change) -> None:
    """Change one statement and re-encode it canonically, so only semantics can fail."""
    statements = _lines(bundle)
    change(statements[index])
    bundle.chmod(0o600)
    bundle.write_bytes(verifier.bundle_bytes(statements))


@pytest.fixture
def deps_receipt(caller, tmp_path, producer) -> Path:
    """A real COMPLETE run that imported a local-path skill through the pinned APM."""
    package = _package(tmp_path / "job", imports=True)
    _skill(tmp_path / "style")
    shutil.copytree(package, caller, dirs_exist_ok=True)
    (caller / "apm.yml").write_text(
        "name: caller\nversion: 1.0.0\ndependencies:\n  apm: [../style]\n"
    )
    result = CliRunner().invoke(main, ["job.contract.md", "--on", "copilot", "--allow-host-access"])
    assert result.exit_code == Outcome.COMPLETE, result.output
    (receipt,) = list((caller / ".apm/runs").glob("*/receipt.intoto.jsonl"))
    return receipt


def _audit(*args: str):
    return CliRunner().invoke(main, ["audit", *map(str, args)])


def _rows(output: str) -> dict[str, str]:
    rows = {}
    for line in output.splitlines():
        for name in command.DISPLAY:
            if line[4:].startswith(name + " "):
                rows[name] = line
    return rows


def test_dependency_free_receipt_is_valid_offline_and_launches_nothing(
    plain_receipt, monkeypatch
) -> None:
    monkeypatch.setattr(
        apm_backend, "locate_backend", lambda: pytest.fail("APM must not be launched")
    )
    result = _audit(plain_receipt)
    assert result.exit_code == 0, result.output
    rows = _rows(result.output)
    assert list(rows) == list(command.DISPLAY)
    assert rows["Integrity"].startswith("[+] Integrity")
    assert "3 statements intact and well-formed" in rows["Integrity"]
    assert "evidence" in rows["Integrity"]
    assert "Standards" not in result.output
    assert "1 contract, 1 check" in rows["Factory"]
    assert "1/1 passed (in-toto test-result)" in rows["Checks"]
    assert rows["Dependencies"].startswith("[+]") and "0 APM dependencies" in rows["Dependencies"]
    assert "1 file bound: result.txt" in rows["Outputs"]
    assert rows["Identity"].startswith("[!] Identity") and "unsigned" in rows["Identity"]
    assert result.output.rstrip().endswith("[+] VALID   (content-bound; not authenticated)")
    assert result.output.isascii() and "\x1b" not in result.output
    assert "signed" not in result.output.replace("unsigned", "")


@needs_backend
def test_dependency_receipt_is_audited_by_the_pinned_apm(deps_receipt) -> None:
    result = _audit(deps_receipt)
    assert result.exit_code == 0, result.output
    rows = _rows(result.output)
    assert rows["Dependencies"].startswith("[+]")
    assert "1 APM dependency   apm audit --ci: clean" in rows["Dependencies"]
    assert "installed bytes match CycloneDX inventory" in result.output
    assert not list(Path(tempfile.gettempdir()).glob("apmx-audit-*"))


@needs_backend
def test_changed_dependency_content_is_invalid(deps_receipt, tmp_path) -> None:
    skill = tmp_path / "style/SKILL.md"
    skill.write_text(skill.read_text() + "Injected instruction.\n")
    result = _audit(deps_receipt)
    assert result.exit_code == 1, result.output
    assert "[x] Dependencies" in result.output
    assert "reinstalled content differs from the receipt" in result.output
    assert "[x] INVALID   Dependencies:" in result.output


@needs_backend
def test_unreachable_dependency_is_unavailable_never_a_pass(deps_receipt, tmp_path) -> None:
    shutil.rmtree(tmp_path / "style")
    result = _audit(deps_receipt)
    assert result.exit_code == 2, result.output
    assert "apm install --frozen could not reinstall them" in result.output
    assert "INCOMPLETE" in result.output and "VALID" not in result.output.replace("INVALID", "")


@needs_backend
def test_offline_skips_dependencies_with_a_warning(deps_receipt, monkeypatch) -> None:
    monkeypatch.setattr(
        apm_backend, "locate_backend", lambda: pytest.fail("APM must not be launched")
    )
    result = _audit(deps_receipt, "--offline")
    assert result.exit_code == 0, result.output
    assert "[!] Dependencies  not audited (offline): 1 APM dependency" in result.output
    assert "[+] VALID" in result.output


@needs_backend
def test_local_policy_failure_is_invalid(deps_receipt, tmp_path) -> None:
    policy = tmp_path / "policy.yml"
    policy.write_text(
        'name: org\nversion: "1.0"\nenforcement: block\nmanifest:\n  required_fields: [license]\n'
    )
    result = _audit(deps_receipt, "--policy", policy)
    assert result.exit_code == 1, result.output
    assert "apm audit --ci --policy: 1 failing check (required-manifest-fields)" in result.output


@needs_backend
def test_policy_that_cannot_be_loaded_is_unavailable(deps_receipt) -> None:
    result = _audit(deps_receipt, "--policy", "org")
    assert result.exit_code == 2, result.output
    rows = _rows(result.output)
    assert rows["Policy"].startswith("[!] Policy") and "could not be loaded" in rows["Policy"]
    assert "not evaluated (policy could not be loaded)" in rows["Dependencies"]
    assert "INCOMPLETE   Policy:" in result.output


@needs_backend
def test_no_policy_audit_reports_only_baseline_checks(deps_receipt, tmp_path) -> None:
    """A new no-policy check name would otherwise be mistaken for an enforced policy."""
    from apmx.audit.dependencies import _BASELINE_CHECKS

    receipt, failure = verifier.inspect(deps_receipt)
    assert failure is None, failure
    source = tmp_path / "source"
    source.mkdir()
    for name, raw in receipt.apm_files.items():
        (source / name).write_bytes(raw)
    manifest = source / "imports-apm.yml"
    manifest = manifest if manifest.is_file() else source / "apm.yml"
    executable = str(apm_backend.locate_backend())
    env = {**apm_backend.backend_child_env(dict(os.environ)), "NO_COLOR": "1"}

    def checks(stage: Path) -> set[str]:
        audited = subprocess.run(
            [executable, "audit", "--ci", "--no-fail-fast", "-f", "json", "--no-policy"],
            cwd=stage,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert audited.returncode in (0, 1), audited.stderr
        return {item["name"] for item in json.loads(audited.stdout)["checks"]}

    installed = tmp_path / "installed"
    installed.mkdir()
    shutil.copyfile(manifest, installed / "apm.yml")
    shutil.copyfile(source / "apm.lock.yaml", installed / "apm.lock.yaml")
    subprocess.run(
        [
            executable,
            "install",
            "--frozen",
            "--root",
            str(installed),
            "--only",
            "apm",
            "--target",
            "agent-skills",
            "--no-trust-bin",
            "--no-policy",
        ],
        cwd=installed,
        env=env,
        capture_output=True,
        timeout=120,
        check=True,
    )
    orphaned = tmp_path / "orphaned"
    orphaned.mkdir()
    shutil.copyfile(source / "apm.lock.yaml", orphaned / "apm.lock.yaml")
    reported = checks(installed) | checks(orphaned)
    assert "manifest-missing" in reported
    assert reported <= _BASELINE_CHECKS


def _lock(owner: str, digest: str = "a" * 64) -> bytes:
    files = (
        "  deployed_files:\n  - .agents/skills/style/SKILL.md\n"
        f"  deployed_file_hashes:\n    .agents/skills/style/SKILL.md: sha256:{digest}\n"
    )
    return (
        "lockfile_version: '1'\ndependencies:\n"
        f"- repo_url: _local/package\n  local_path: /src/package\n"
        f"{files if owner == 'package' else ''}"
        "- repo_url: _local/style\n  local_path: ./skills/style\n  depth: 2\n"
        f"{files if owner == 'style' else ''}"
    ).encode()


def test_frozen_replay_may_reassign_a_shared_skill_owner() -> None:
    from apmx.audit.dependencies import _drifted

    # APM 0.33.0 attributes a package-bundled transitive skill to either entry.
    assert _drifted(_lock("package"), _lock("style")) == []
    assert _drifted(_lock("package"), _lock("style", "b" * 64)) == ["/src/package"]
    assert _drifted(_lock("style"), _lock("package", "b" * 64)) == ["./skills/style"]
    assert _drifted(_lock("package"), _lock("none")) == ["/src/package"]


def test_policy_and_offline_are_exclusive(plain_receipt) -> None:
    result = _audit(plain_receipt, "--offline", "--policy", "org")
    assert result.exit_code == 2
    assert "--offline can only read a local --policy file" in result.output


def test_tampered_evidence_fails_integrity(plain_receipt) -> None:
    blob = next((plain_receipt.parent / "evidence/sha256").iterdir())
    blob.chmod(0o600)
    blob.write_bytes(b"forged\n")
    result = _audit(plain_receipt)
    assert result.exit_code == 1
    assert "[x] Integrity     Evidence blob" in result.output
    assert "[i] Factory       not evaluated (earlier failure)" in result.output
    assert "[i] Dependencies  not evaluated (receipt invalid; APM not launched)" in result.output
    assert "[x] INVALID   Integrity: Evidence blob" in result.output


def test_evidence_is_optional(plain_receipt) -> None:
    import shutil as _shutil

    _shutil.rmtree(plain_receipt.parent / "evidence")
    result = _audit(plain_receipt, "--format", "json")
    report = json.loads(result.output)
    assert report["result"] == "valid", result.output
    assert report["facts"]["evidence"] == "absent"


def test_noncanonical_line_fails_integrity(plain_receipt) -> None:
    raw = plain_receipt.read_bytes().splitlines()
    envelope = json.loads(raw[0])
    raw[0] = json.dumps(envelope, indent=1).replace("\n", "").encode()
    plain_receipt.chmod(0o600)
    plain_receipt.write_bytes(b"\n".join(raw) + b"\n")
    result = _audit(plain_receipt)
    assert result.exit_code == 1
    assert "not canonical bundle JSON" in result.output


@pytest.mark.parametrize(
    ("index", "change", "row", "reason"),
    [
        (
            0,
            lambda data: data.update(_type="https://example.invalid/Statement"),
            "Integrity",
            "",
        ),
        (0, lambda data: data.update(subject=[]), "Integrity", ""),
        (
            0,
            lambda data: data["predicate"]["buildDefinition"].update(buildType="x"),
            "Factory",
            "Unsupported build type.",
        ),
        (
            0,
            lambda data: data["predicate"]["buildDefinition"]["internalParameters"]["stages"][0][
                "checks"
            ].append({"name": "invented", "command": "true"}),
            "Factory",
            "Recorded checks differ from the definition.",
        ),
        (
            0,
            lambda data: data["subject"][0]["digest"].update(sha256="0" * 64),
            "Outputs",
            "A Test Result names different subjects.",
        ),
        (
            1,
            lambda data: data["predicate"].update(result="FAILED"),
            "Checks",
            "Check acceptance of the final candidate did not pass (FAILED).",
        ),
        (
            1,
            lambda data: data["predicate"]["configuration"][0]["annotations"].update(
                check="renamed"
            ),
            "Checks",
            "Checker invocation omitted or invented",
        ),
        (
            1,
            lambda data: data["subject"][0].update(name="elsewhere.txt"),
            "Outputs",
            "A Test Result names different subjects.",
        ),
        (
            2,
            lambda data: data["predicate"].update(components=[{"name": "no-type"}]),
            "Integrity",
            "CycloneDX 1.5 schema violation",
        ),
        (
            2,
            lambda data: data["subject"][0]["digest"].update(sha256="1" * 64),
            "Outputs",
            "The CycloneDX statement names different subjects.",
        ),
    ],
)
def test_tampering_each_line_kind_names_its_row(plain_receipt, index, change, row, reason) -> None:
    _edit_line(plain_receipt, index, change)
    result = _audit(plain_receipt)
    assert result.exit_code == 1, result.output
    assert f"[x] {row}" in result.output
    assert f"[x] INVALID   {row}: {reason}" in result.output


def test_verification_summary_must_bind_every_other_line(plain_receipt) -> None:
    statements = _lines(plain_receipt)
    subjects = statements[0]["subject"]
    digest = statements[0]["predicate"]["buildDefinition"]["externalParameters"]["factory"][
        "definition"
    ]["sha256"]

    def summary(lines: list[str]) -> dict:
        return verifier.statement(
            subjects,
            verifier.VSA,
            {
                "verifier": {"id": "https://github.com/acme/platform/.github/workflows/run.yml@x"},
                "timeVerified": "2026-10-21T18:52:30Z",
                "resourceUri": "git+https://github.com/acme/shop@" + "a" * 40,
                "policy": {"uri": f"apmx:factory/{digest}", "digest": {"sha256": digest}},
                "inputAttestations": [
                    {"uri": f"receipt.intoto.jsonl#{n}", "digest": {"sha256": value}}
                    for n, value in enumerate(lines, 1)
                ],
                "verificationResult": "PASSED",
                "verifiedLevels": [],
            },
        )

    digests = [verifier.line_digest(item) for item in statements]
    plain_receipt.chmod(0o600)
    plain_receipt.write_bytes(verifier.bundle_bytes([*statements, summary(digests)]))
    report = json.loads(_audit(plain_receipt, "--format", "json").output)
    assert report["result"] == "valid", report
    plain_receipt.write_bytes(verifier.bundle_bytes([*statements, summary(digests[:-1])]))
    report = json.loads(_audit(plain_receipt, "--format", "json").output)
    assert report["reason"].startswith("Integrity: The verification summary's inputAttestations")
    for field, value, reason in (
        ("verifiedLevels", "SLSA_BUILD_LEVEL_3", "verifiedLevels must be a list"),
        ("timeVerified", "yesterday", "invalid timeVerified"),
        ("verificationResult", "OK", "invalid verificationResult"),
    ):
        bad = summary(digests)
        bad["predicate"][field] = value
        plain_receipt.write_bytes(verifier.bundle_bytes([*statements, bad]))
        report = json.loads(_audit(plain_receipt, "--format", "json").output)
        assert report["reason"].startswith("Integrity:") and reason in report["reason"], report


def test_outputs_are_rehashed_only_when_requested(plain_receipt, tmp_path) -> None:
    delivered = tmp_path / "pr-checkout"
    delivered.mkdir()
    artifact = plain_receipt.parent / "outputs/result.txt"
    (delivered / "result.txt").write_bytes(artifact.read_bytes())
    result = _audit(plain_receipt, "--outputs", delivered)
    assert result.exit_code == 0, result.output
    assert "1/1 delivered files match receipt subjects (re-hashed)" in result.output
    (delivered / "result.txt").write_bytes(b"changed after the run\n")
    result = _audit(plain_receipt, "--outputs", delivered)
    assert result.exit_code == 1
    assert (
        "[x] Outputs       result.txt differs from its receipt subject (SHA-256)" in result.output
    )
    (delivered / "result.txt").unlink()
    result = _audit(plain_receipt, "--outputs", delivered)
    assert result.exit_code == 1 and "result.txt is missing from --outputs" in result.output


def test_json_format_is_machine_readable(plain_receipt) -> None:
    result = _audit(plain_receipt, "--format", "json")
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["schema"] == "apmx-audit/2"
    assert (report["result"], report["exitCode"], report["signed"]) == ("valid", 0, False)
    assert [row["name"] for row in report["rows"]] == list(command.DISPLAY)
    assert {row["status"] for row in report["rows"]} == {"pass", "warn", "info"}
    assert report["rows"][0] == {
        "name": "Policy",
        "status": "info",
        "detail": "none found",
        "notes": report["rows"][0]["notes"],
    }
    assert report["facts"]["outputs"] == ["result.txt"]
    assert report["facts"]["apmDependencies"] == 0
    assert report["facts"]["stages"][0]["harness"] == "copilot"
    _edit_line(plain_receipt, 1, lambda data: data["predicate"].update(result="FAILED"))
    report = json.loads(_audit(plain_receipt, "--format", "json").output)
    assert (report["result"], report["exitCode"]) == ("invalid", 1)
    assert report["reason"].startswith(
        "Checks: Check acceptance of the final candidate did not pass"
    )


def test_missing_receipt_is_a_usage_error(tmp_path) -> None:
    result = _audit(tmp_path / "nowhere")
    assert result.exit_code == 2
    assert "No receipt at" in result.output
    report = json.loads(_audit(tmp_path, "--format", "json").output.split("Error:")[0])
    assert (report["result"], report["exitCode"]) == ("error", 2)
    assert _audit().exit_code == 2


def test_audit_is_reserved_but_dot_audit_runs_a_directory(tmp_path, monkeypatch) -> None:
    factory = tmp_path / "audit"
    factory.mkdir()
    (factory / "notes.md").write_text("input\n")
    (factory / "job.contract.md").write_text(
        "---\nneeds: notes.md\nproduces: out.txt\nverify:\n  ok: "
        + json.dumps(f'"{Path(sys.executable).as_posix()}" -c "pass"')
        + "\n---\nWrite out.txt.\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("apmx.runtime.utils.find_runtime_binary", lambda _: sys.executable)
    planned = CliRunner().invoke(main, ["./audit", "--plan"])
    assert planned.exit_code == 0, planned.output
    assert "job" in planned.output
    reserved = CliRunner().invoke(main, ["--plan", "audit"])
    assert reserved.exit_code == 2
    assert "'audit' is reserved" in reserved.output and "./audit" in reserved.output
    routed = CliRunner().invoke(main, ["audit"])
    assert routed.exit_code == 2 and "RECEIPT" in routed.output
    assert "apmx audit RECEIPT" in CliRunner().invoke(main, ["--help"]).output
    help_text = CliRunner().invoke(main, ["audit", "--help"]).output
    for option in ("--outputs", "--policy", "--offline", "--format", "--explain", "--export"):
        assert option in help_text


def test_color_only_on_a_terminal_without_no_color(plain_receipt) -> None:
    audit = command.run_audit(plain_receipt)
    assert "\x1b[" in command.to_text(audit, color=True)
    assert "\x1b[" not in command.to_text(audit, color=False)


def test_independent_wrapper_validates_without_importing_apmx(plain_receipt) -> None:
    probe = (
        "import runpy, sys; sys.argv = sys.argv[1:]; "
        "namespace = runpy.run_path(sys.argv[0]); "
        "print(namespace['verify'](__import__('pathlib').Path(sys.argv[1]))['status']); "
        "print('apmx' in sys.modules)"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            probe,
            str(ROOT / "scripts/verify_evidence.py"),
            str(plain_receipt),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["passed", "False"]
    cli = subprocess.run(
        [sys.executable, str(ROOT / "scripts/verify_evidence.py"), str(plain_receipt)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert cli.returncode == 0, cli.stderr
    assert json.loads(cli.stdout)["status"] == "passed"


def test_bundled_schemas_are_the_pinned_upstream_bytes() -> None:
    for name, expected in verifier.SCHEMAS.items():
        raw = (verifier.BUNDLED_SCHEMAS / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected
    assert (verifier.BUNDLED_SCHEMAS / "LICENSE-CycloneDX.txt").is_file()


def test_explain_replaces_the_summary(plain_receipt) -> None:
    result = _audit(plain_receipt, "--explain")
    assert result.exit_code == 0, result.output
    for heading in ("Factory", "Stages", "Checks", "Dependencies", "Outputs"):
        assert f"\n{heading}\n" in f"\n{result.output}"
    assert "copilot" in result.output and "result.txt" in result.output
    assert (
        "  standards    in-toto Statement v1, SLSA Provenance v1, in-toto Test Result, "
        "CycloneDX 1.5: schema-valid\n" in result.output
    )
    dependencies = _audit(plain_receipt, "--explain", "dependencies")
    assert dependencies.exit_code == 0 and dependencies.output.startswith("Dependencies\n")
    assert _audit(plain_receipt, "--explain", "ingredients").exit_code == 2
    stages = _audit(plain_receipt, "--explain", "stages")
    assert stages.exit_code == 0 and stages.output.startswith("Stages\n")
    assert "Checks" not in stages.output
    assert result.output.isascii()


def test_explain_refuses_an_invalid_receipt(plain_receipt) -> None:
    _edit_line(plain_receipt, 1, lambda data: data["predicate"].update(result="FAILED"))
    result = _audit(plain_receipt, "--explain", "factory")
    assert result.exit_code == 1 and "Checks:" in result.output


def test_export_abom_writes_the_standalone_cyclonedx(plain_receipt, tmp_path) -> None:
    target = tmp_path / "abom.cdx.json"
    result = _audit(plain_receipt, "--export", "abom", "-o", target)
    assert result.exit_code == 0, result.output
    bom = json.loads(target.read_bytes())
    assert (bom["bomFormat"], bom["specVersion"]) == ("CycloneDX", "1.5")
    verifier.schema_validator().validate(bom)
    again = _audit(plain_receipt, "--export", "abom", "-o", target)
    assert again.exit_code == 2 and "refusing to overwrite" in again.output
    stdout = _audit(plain_receipt, "--export", "abom")
    assert json.loads(stdout.output) == bom
