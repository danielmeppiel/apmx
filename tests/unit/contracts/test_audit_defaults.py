"""`apmx audit` with no flags: receipt selection, the PR gate and discovered policy."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from test_apmx import caller, producer
from test_audit import (
    _no_ambient_policy_or_pull_request,
    _rows,
    deps_receipt,
    needs_backend,
    plain_receipt,
)

from apmx.audit import policy as policies

__all__ = [
    "_no_ambient_policy_or_pull_request",
    "caller",
    "deps_receipt",
    "plain_receipt",
    "producer",
]
pytestmark = pytest.mark.component
OLD = "20200101T000000Z-aaaaaaaaaaaa"
NEW = "20300101T000000Z-bbbbbbbbbbbb"


def _audit(*args: str):
    from click.testing import CliRunner

    from apmx.cli import main

    return CliRunner().invoke(main, ["audit", *map(str, args)])


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _definition(receipt: Path) -> str:
    from apmx.audit import verifier

    parsed, failure = verifier.inspect(receipt)
    assert failure is None, failure
    return parsed.definition_sha256


def _copy_receipt(receipt: Path, root: Path, run_id: str) -> Path:
    """Copy a run's receipt (bundle, outputs and evidence) to another run directory."""
    target = root / ".apm/runs" / run_id
    target.mkdir(parents=True)
    shutil.copyfile(receipt, target / "receipt.intoto.jsonl")
    for name in ("outputs", "evidence"):
        shutil.copytree(receipt.parent / name, target / name)
    return target / "receipt.intoto.jsonl"


def _fake_apm(monkeypatch, enforcement: str = "block") -> list:
    """APM loads any policy file; apmx reads its own keys from that same file."""
    calls = []

    def status(root, source):
        calls.append(source)
        return {"outcome": "found", "source": f"file:{source}", "enforcement": enforcement}

    monkeypatch.setattr(policies, "_apm_status", status)
    return calls


@pytest.fixture
def pull(tmp_path, plain_receipt, monkeypatch):
    """A repository whose PR delivers the factory's output: code only, no receipt."""

    def build(*, policy_base=None, policy_head=None, extra=None, output=None, directory="repo"):
        repo = tmp_path / directory
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "core.autocrlf", "false")
        (repo / "README.md").write_text("app\n")
        if policy_base is not None:
            (repo / "apm-policy.yml").write_text(policy_base)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "base")
        base = _git(repo, "rev-parse", "HEAD")
        _git(repo, "switch", "-qc", "factory")
        artifact = plain_receipt.parent / "outputs/result.txt"
        (repo / "result.txt").write_bytes(output if output is not None else artifact.read_bytes())
        for name, text in (extra or {}).items():
            (repo / name).write_text(text)
        if policy_head is not None:
            (repo / "apm-policy.yml").write_text(policy_head)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "factory output")
        head = _git(repo, "rev-parse", "HEAD")
        event = tmp_path / "event.json"
        event.write_text(
            json.dumps(
                {"pull_request": {"number": 7, "base": {"sha": base}, "head": {"sha": head}}}
            )
        )
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
        monkeypatch.chdir(repo)
        return repo, base, head

    return build


# ------------------------------------------------------------------ local defaults


def test_no_arguments_audits_the_newest_complete_run(plain_receipt, tmp_path, monkeypatch) -> None:
    work = tmp_path / "work"
    _copy_receipt(plain_receipt, work, OLD)
    _copy_receipt(plain_receipt, work, NEW)
    (work / ".apm/runs/20250101T000000Z-cccccccccccc").mkdir(parents=True)
    monkeypatch.chdir(work)
    result = _audit()
    assert result.exit_code == 0, result.output
    first = result.output.splitlines()[0]
    assert first.startswith(
        f"Receipt   .apm/runs/{NEW}/receipt.intoto.jsonl   (newest of 2 COMPLETE runs)"
    )
    assert "1/1 delivered files match receipt subjects (re-hashed)" in result.output
    rows = _rows(result.output)
    assert rows["Policy"] == "[i] Policy        none found"
    assert rows["Revocation"].startswith("[i] Revocation    no policy")
    assert result.output.rstrip().endswith("[+] VALID   (content-bound; not authenticated)")


def test_no_receipt_under_dot_apm_is_exit_2(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = _audit()
    assert result.exit_code == 2 and "No receipt under ./.apm/" in result.output
    report = json.loads(_audit("--format", "json").output.split("Error:")[0])
    assert (report["result"], report["exitCode"]) == ("error", 2)


# ------------------------------------------------------------------ discovered policy


def _local_policy(tmp_path, monkeypatch, plain_receipt, text: str) -> Path:
    work = tmp_path / "work"
    _copy_receipt(plain_receipt, work, NEW)
    (work / "apm-policy.yml").write_text(text)
    monkeypatch.chdir(work)
    return work


def test_factory_allowlist_from_the_repository_policy(plain_receipt, tmp_path, monkeypatch) -> None:
    digest = _definition(plain_receipt)
    work = _local_policy(
        tmp_path,
        monkeypatch,
        plain_receipt,
        f"enforcement: block\nfactories:\n  allow: ['acme/job@sha256:{digest}']\n",
    )
    calls = _fake_apm(monkeypatch)
    result = _audit()
    assert result.exit_code == 0, result.output
    rows = _rows(result.output)
    assert rows["Policy"] == (
        "[+] Policy        apm-policy.yml (repository)   factories enforcement block"
    )
    assert f"sha256:{digest[:12]}.. on factories.allow (acme/job)" in rows["Factory"]
    assert calls == [str((work / "apm-policy.yml").resolve())]
    (work / "apm-policy.yml").write_text(
        f"enforcement: block\nfactories:\n  allow: ['sha256:{'0' * 64}']\n"
    )
    result = _audit()
    assert result.exit_code == 1, result.output
    assert _rows(result.output)["Factory"].startswith("[x] Factory       definition sha256:")
    assert "INVALID   Factory:" in result.output and "not on factories.allow" in result.output
    assert "not evaluated (receipt invalid" in _rows(result.output)["Ingredients"]


def test_require_signed_is_a_gate_that_top_level_warn_cannot_soften(
    plain_receipt, tmp_path, monkeypatch
) -> None:
    work = _local_policy(
        tmp_path,
        monkeypatch,
        plain_receipt,
        "enforcement: warn\nfactories:\n  require_signed: true\n",
    )
    _fake_apm(monkeypatch)
    result = _audit()
    assert result.exit_code == 1, result.output
    assert _rows(result.output)["Identity"].startswith(
        "[x] Identity      factories.require_signed is set but factories.signers is empty"
    )
    (work / "apm-policy.yml").write_text(
        "factories:\n  enforcement: warn\n  require_signed: true\n"
    )
    result = _audit()
    assert result.exit_code == 0, result.output
    assert _rows(result.output)["Identity"].startswith("[!] Identity      factories.require_signed")
    (work / "apm-policy.yml").write_text("enforcement: off\nfactories:\n  require_signed: true\n")
    result = _audit()
    assert result.exit_code == 0, result.output
    rows = _rows(result.output)
    assert rows["Policy"] == "[i] Policy        apm-policy.yml (repository)   factories off"
    assert "top-level enforcement: off disables factories rules" in result.output
    assert rows["Identity"] == "[!] Identity      unsigned: proves content binding, not who ran it"


def test_unloadable_policy_is_incomplete_never_a_pass(plain_receipt, tmp_path, monkeypatch) -> None:
    _local_policy(tmp_path, monkeypatch, plain_receipt, "factories:\n  require_sigend: true\n")
    _fake_apm(monkeypatch)
    result = _audit()
    assert result.exit_code == 2, result.output
    rows = _rows(result.output)
    assert rows["Policy"].startswith("[!] Policy        apm-policy.yml (repository) could not be")
    assert "INCOMPLETE   Policy:" in result.output
    report = json.loads(_audit("--format", "json").output)
    assert (report["result"], report["exitCode"]) == ("incomplete", 2)


@needs_backend
def test_the_bundled_apm_resolves_the_policy_file(plain_receipt, tmp_path, monkeypatch) -> None:
    """Real seam: APM loads a policy carrying apmx's new sections and rejects a broken one."""
    digest = _definition(plain_receipt)
    work = _local_policy(
        tmp_path,
        monkeypatch,
        plain_receipt,
        f"name: repo\nversion: '1.0'\n"
        f"factories:\n  allow: ['sha256:{digest}']\n"
        f"  revoked: [{{purl: 'pkg:github/acme/x@1', bodySha256: '{'0' * 64}'}}]\n",
    )
    result = _audit()
    assert result.exit_code == 0, result.output
    rows = _rows(result.output)
    assert rows["Policy"].startswith("[+] Policy        apm-policy.yml (repository)")
    assert "on factories.allow" in rows["Factory"] and rows["Revocation"].startswith("[+]")
    (work / "apm-policy.yml").write_text("name: [\n")
    result = _audit()
    assert result.exit_code == 2 and "could not be loaded: APM:" in result.output


@needs_backend
def test_revoked_capability_body_is_rejected_offline(deps_receipt, tmp_path) -> None:
    from apmx.audit import verifier

    parsed, failure = verifier.inspect(deps_receipt)
    assert failure is None, failure
    (binding,) = parsed.bindings
    policy = tmp_path / "revoked.yml"
    policy.write_text(
        "factories:\n  revoked:\n"
        f"    - purl: '{binding['purl']}'\n      bodySha256: '{binding['bodySha256']}'\n"
    )
    result = _audit(deps_receipt, "--offline", "--policy", policy)
    assert result.exit_code == 1, result.output
    row = _rows(result.output)["Revocation"]
    assert (
        row
        == f"[x] Revocation    capability {binding['capability']} is revoked (factories.revoked)"
    )
    assert f"purl={binding['purl']}" in result.output


def test_sarif_reports_failing_and_warning_rows(plain_receipt, tmp_path, monkeypatch) -> None:
    _local_policy(tmp_path, monkeypatch, plain_receipt, "factories:\n  require_signed: true\n")
    _fake_apm(monkeypatch)
    result = _audit("--format", "sarif")
    assert result.exit_code == 1, result.output
    sarif = json.loads(result.output)
    assert sarif["version"] == "2.1.0"
    (run,) = sarif["runs"]
    levels = {item["ruleId"]: item["level"] for item in run["results"]}
    assert levels == {"apmx-audit/identity": "error"}
    assert run["properties"] == {"result": "invalid", "exitCode": 1}
    assert {rule["id"] for rule in run["tool"]["driver"]["rules"]} >= {"apmx-audit/factory"}


# ------------------------------------------------------------------ pull requests

SIGNERS = (
    "factories:\n  signers:\n"
    "    - workflow: acme/platform/.github/workflows/run.yml\n      repo: acme/platform\n"
)


def _store(monkeypatch, receipt: Path):
    from test_audit_gate import _store as store_of

    from apmx.audit import identity

    store = store_of(receipt)
    monkeypatch.setattr(identity, "_gh", store)
    return store


def test_pull_request_is_gated_from_the_attestation_store(pull, plain_receipt, monkeypatch) -> None:
    pull(policy_base=SIGNERS)
    _fake_apm(monkeypatch)
    _store(monkeypatch, plain_receipt)
    result = _audit()
    assert result.exit_code == 0, result.output
    first = result.output.splitlines()[0]
    assert first.startswith("Receipt   attestations for PR #7   (looked up by file digest)")
    assert first.endswith("   signed")
    row = _rows(result.output)["Outputs"]
    assert row.startswith("[+] Outputs       PR #7: 1 changed file = delivered files at head")
    report = json.loads(_audit("--format", "json").output)
    assert report["facts"]["change"]["bound"] is True


def test_pull_request_without_signers_is_incomplete(pull) -> None:
    pull()
    result = _audit()
    assert result.exit_code == 2, result.output
    assert "carries code only" in result.output and "factories.signers" in result.output


def test_pull_request_with_an_explicit_bundle_binds_its_change(pull, plain_receipt) -> None:
    pull()
    result = _audit(plain_receipt, "--offline")
    assert result.exit_code == 0, result.output
    assert _rows(result.output)["Outputs"].startswith("[+] Outputs       PR #7: 1 changed file")


def test_pull_request_change_the_delivery_does_not_bind_is_invalid(pull, plain_receipt) -> None:
    pull(extra={"backdoor.py": "import os\n"})
    result = _audit(plain_receipt, "--offline")
    assert result.exit_code == 1, result.output
    assert "PR #7 changes backdoor.py, which is not a delivered file" in result.output


def test_pull_request_output_edited_after_the_run_is_invalid(pull, plain_receipt) -> None:
    pull(output=b"edited after the run\n")
    result = _audit(plain_receipt, "--offline")
    assert result.exit_code == 1, result.output
    assert "result.txt, whose bytes differ from the delivered file" in result.output


def test_pull_request_commits_missing_from_a_shallow_checkout_is_exit_2(pull, tmp_path) -> None:
    pull()
    event = json.loads((tmp_path / "event.json").read_text())
    event["pull_request"]["base"]["sha"] = "c" * 40
    (tmp_path / "event.json").write_text(json.dumps(event))
    result = _audit()
    assert result.exit_code == 2 and "fetch-depth: 0" in result.output


def test_pull_request_cannot_loosen_its_own_policy(pull, plain_receipt, monkeypatch) -> None:
    strict = SIGNERS + f"  allow: ['sha256:{'0' * 64}']\n"
    loose = SIGNERS + f"  allow: ['sha256:{_definition(plain_receipt)}']\n"
    _, base, _ = pull(policy_base=strict, policy_head=loose)
    _fake_apm(monkeypatch)
    _store(monkeypatch, plain_receipt)
    result = _audit()
    assert result.exit_code == 1, result.output
    rows = _rows(result.output)
    assert f"apm-policy.yml (repository, PR base {base[:12]})" in rows["Policy"]
    assert rows["Identity"].startswith("[x] Identity") and (
        "(apm-policy.yml is not a delivered file)" in rows["Identity"]
    )


def test_a_pull_request_from_the_store_is_checked_against_the_base_policy(
    pull, plain_receipt, monkeypatch
) -> None:
    pull(policy_base=SIGNERS + f"  allow: ['sha256:{'0' * 64}']\n")
    _fake_apm(monkeypatch)
    _store(monkeypatch, plain_receipt)
    result = _audit()
    assert result.exit_code == 1, result.output
    rows = _rows(result.output)
    assert rows["Factory"].startswith("[x] Factory") and "not on factories.allow" in rows["Factory"]


def test_explicit_outputs_overrides_the_pull_request_binding(pull, plain_receipt) -> None:
    repo, _, _ = pull(extra={"unrelated.md": "x\n"})
    result = _audit(plain_receipt, "--outputs", repo, "--offline")
    assert result.exit_code == 0, result.output
    assert "1/1 delivered files match receipt subjects (re-hashed)" in result.output
