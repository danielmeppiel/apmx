"""`apmx audit`: row-by-row receipt verification with APM-owned ingredient trust."""

import hashlib
import json
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


@pytest.fixture
def plain_receipt(tmp_path, monkeypatch) -> Path:
    _, result = _execute(tmp_path, monkeypatch)
    return evidence.export_package(result.run_directory / "record.json", tmp_path / "receipt")


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
    (receipt,) = list((caller / ".apm/runs").glob("*/receipt"))
    return receipt


def _audit(*args: str):
    return CliRunner().invoke(main, ["audit", *map(str, args)])


def _rewrite(receipt: Path, name: str, raw: bytes) -> None:
    """Change one file and refresh every declared hash, so only semantics can fail."""
    path = receipt / name
    path.chmod(0o600)
    path.write_bytes(raw)
    index = json.loads((receipt / "index.json").read_bytes())
    digest = hashlib.sha256(raw).hexdigest()
    index["files"][name] = {"sha256": digest, "size": len(raw)}
    for group in ("production", "checks"):
        for item in index[group]:
            if item["name"] == name:
                item["digest"]["sha256"] = digest
    for key in ("definition", "inventory"):
        if index[key]["name"] == name:
            index[key]["digest"]["sha256"] = digest
    (receipt / "index.json").chmod(0o600)
    (receipt / "index.json").write_text(json.dumps(index))


def _edit_json(receipt: Path, name: str, change) -> None:
    data = json.loads((receipt / name).read_bytes())
    change(data)
    _rewrite(receipt, name, json.dumps(data).encode())


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
    assert rows["Integrity"].startswith("[+] Integrity") and "match index.json" in rows["Integrity"]
    assert "in-toto Statement v1, SLSA Provenance v1, CycloneDX 1.5" in rows["Standards"]
    assert "1 contract, 1 check" in rows["Factory"]
    assert "1/1 passed (in-toto test-result)" in rows["Checks"]
    assert rows["Ingredients"].startswith("[+]") and "0 APM dependencies" in rows["Ingredients"]
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
    assert rows["Ingredients"].startswith("[+]")
    assert "1 APM dependency   apm audit --ci: clean" in rows["Ingredients"]
    assert "installed bytes match CycloneDX inventory" in result.output
    assert not list(Path(tempfile.gettempdir()).glob("apmx-audit-*"))


@needs_backend
def test_changed_dependency_content_is_invalid(deps_receipt, tmp_path) -> None:
    skill = tmp_path / "style/SKILL.md"
    skill.write_text(skill.read_text() + "Injected instruction.\n")
    result = _audit(deps_receipt)
    assert result.exit_code == 1, result.output
    assert "[x] Ingredients" in result.output
    assert "reinstalled content differs from the receipt" in result.output
    assert "[x] INVALID   Ingredients:" in result.output


@needs_backend
def test_unreachable_dependency_is_unavailable_never_a_pass(deps_receipt, tmp_path) -> None:
    shutil.rmtree(tmp_path / "style")
    result = _audit(deps_receipt)
    assert result.exit_code == 2, result.output
    assert "apm install --frozen could not reinstall them" in result.output
    assert "INCOMPLETE" in result.output and "VALID" not in result.output.replace("INVALID", "")


@needs_backend
def test_offline_skips_ingredients_with_a_warning(deps_receipt, monkeypatch) -> None:
    monkeypatch.setattr(
        apm_backend, "locate_backend", lambda: pytest.fail("APM must not be launched")
    )
    result = _audit(deps_receipt, "--offline")
    assert result.exit_code == 0, result.output
    assert "[!] Ingredients   not audited (offline): 1 APM dependency" in result.output
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
def test_policy_that_is_not_enforced_is_unavailable(deps_receipt) -> None:
    result = _audit(deps_receipt, "--policy", "org")
    assert result.exit_code == 2, result.output
    assert "policy org was not enforced" in result.output


def test_policy_and_offline_are_exclusive(plain_receipt) -> None:
    result = _audit(plain_receipt, "--offline", "--policy", "org")
    assert result.exit_code == 2
    assert "cannot be combined with --offline" in result.output


def test_tampered_output_fails_integrity(plain_receipt) -> None:
    (artifact,) = list(plain_receipt.glob("attempts/*/artifacts/result.txt"))
    artifact.chmod(0o600)
    artifact.write_bytes(b"forged\n")
    result = _audit(plain_receipt)
    assert result.exit_code == 1
    assert "[x] Integrity     File hash mismatch: attempts/" in result.output
    assert "[i] Standards     not evaluated (earlier failure)" in result.output
    assert "[i] Ingredients   not evaluated (receipt invalid; APM not launched)" in result.output
    assert "[x] INVALID   Integrity: File hash mismatch" in result.output


def test_tampered_index_fails_integrity(plain_receipt) -> None:
    index = json.loads((plain_receipt / "index.json").read_bytes())
    index["files"]["summary.md"]["sha256"] = "0" * 64
    (plain_receipt / "index.json").chmod(0o600)
    (plain_receipt / "index.json").write_text(json.dumps(index))
    result = _audit(plain_receipt)
    assert result.exit_code == 1
    assert "File hash mismatch: summary.md" in result.output


def test_unindexed_file_fails_integrity(plain_receipt) -> None:
    (plain_receipt / "extra.txt").write_text("smuggled")
    result = _audit(plain_receipt)
    assert result.exit_code == 1
    assert "Unindexed or missing files." in result.output


@pytest.mark.parametrize(
    ("name", "change", "row", "reason"),
    [
        (
            "provenance.intoto.json",
            lambda data: data.update(_type="https://example.invalid/Statement"),
            "Standards",
            "Wrong statement type",
        ),
        (
            "provenance.intoto.json",
            lambda data: data.update(subject=[]),
            "Standards",
            "",
        ),
        (
            "abom.cdx.json",
            lambda data: data.update(components=[{"name": "no-type"}]),
            "Standards",
            "CycloneDX 1.5 schema violation",
        ),
        (
            "provenance.intoto.json",
            lambda data: data["predicate"]["buildDefinition"].update(buildType="x"),
            "Factory",
            "Unsupported build-type mapping.",
        ),
    ],
)
def test_semantically_invalid_documents_name_their_row(
    plain_receipt, name, change, row, reason
) -> None:
    _edit_json(plain_receipt, name, change)
    result = _audit(plain_receipt)
    assert result.exit_code == 1, result.output
    assert f"[x] {row}" in result.output
    assert f"[x] INVALID   {row}: {reason}" in result.output


def test_failed_check_statement_cannot_be_presented_as_complete(plain_receipt) -> None:
    (check,) = [
        p.relative_to(plain_receipt).as_posix() for p in plain_receipt.glob("checks/*.intoto.json")
    ]
    _edit_json(plain_receipt, check, lambda data: data["predicate"].update(result="FAILED"))
    result = _audit(plain_receipt)
    assert result.exit_code == 1
    assert "[x] Checks" in result.output


def test_outputs_are_rehashed_only_when_requested(plain_receipt, tmp_path) -> None:
    delivered = tmp_path / "pr-checkout"
    delivered.mkdir()
    (artifact,) = list(plain_receipt.glob("attempts/*/artifacts/result.txt"))
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
    assert report["schema"] == "apmx-audit/1"
    assert (report["result"], report["exitCode"], report["signed"]) == ("valid", 0, False)
    assert [row["name"] for row in report["rows"]] == list(command.DISPLAY)
    assert {row["status"] for row in report["rows"]} == {"pass", "warn"}
    assert report["facts"]["outputs"] == ["result.txt"]
    assert report["facts"]["apmDependencies"] == 0
    (plain_receipt / "extra.txt").write_text("smuggled")
    report = json.loads(_audit(plain_receipt, "--format", "json").output)
    assert (report["result"], report["exitCode"]) == ("invalid", 1)
    assert report["reason"] == "Integrity: Unindexed or missing files."


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
    planned = CliRunner().invoke(main, ["./audit", "--plan"])
    assert planned.exit_code == 0, planned.output
    assert "job" in planned.output
    reserved = CliRunner().invoke(main, ["--plan", "audit"])
    assert reserved.exit_code == 2
    assert "'audit' is reserved" in reserved.output and "./audit" in reserved.output
    routed = CliRunner().invoke(main, ["audit"])
    assert routed.exit_code == 2 and "RECEIPT_DIR" in routed.output
    assert "apmx audit RECEIPT_DIR" in CliRunner().invoke(main, ["--help"]).output
    help_text = CliRunner().invoke(main, ["audit", "--help"]).output
    for option in ("--outputs", "--policy", "--offline", "--format"):
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
    assert (verifier.BUNDLED_SCHEMAS / "LICENSE").is_file()
