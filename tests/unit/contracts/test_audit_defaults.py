"""`apmx audit` with no flags: receipt selection, PR change binding and discovered policy."""

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
    return json.loads((receipt / "index.json").read_bytes())["definition"]["digest"]["sha256"]


def _attempt(receipt: Path) -> Path:
    (attempt,) = list((receipt / "attempts").iterdir())
    return attempt


def _copy_receipt(receipt: Path, root: Path, run_id: str) -> Path:
    target = root / ".apm/runs" / run_id / "receipt"
    shutil.copytree(receipt, target)
    return target


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
    """A repository whose PR adds the factory's output and its receipt."""

    def build(*, policy_base=None, policy_head=None, extra=None, output=None, receipts=1):
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "core.autocrlf", "false")
        shutil.copytree(_attempt(plain_receipt) / "baseline", repo, dirs_exist_ok=True)
        if policy_base is not None:
            (repo / "apm-policy.yml").write_text(policy_base)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "base")
        base = _git(repo, "rev-parse", "HEAD")
        _git(repo, "switch", "-qc", "factory")
        artifact = _attempt(plain_receipt) / "artifacts/result.txt"
        (repo / "result.txt").write_bytes(output if output is not None else artifact.read_bytes())
        for index in range(receipts):
            _copy_receipt(plain_receipt, repo, NEW[:-1] + str(index))
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
    assert first.startswith(f"Receipt   .apm/runs/{NEW}/receipt   (newest of 2 COMPLETE runs)")
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
    assert _rows(result.output)["Identity"].startswith("[x] Identity      unsigned, but")
    (work / "apm-policy.yml").write_text(
        "factories:\n  enforcement: warn\n  require_signed: true\n"
    )
    result = _audit()
    assert result.exit_code == 0, result.output
    assert _rows(result.output)["Identity"].startswith("[!] Identity      unsigned, but")
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
    (binding,) = json.loads((deps_receipt / "capability-bindings.json").read_bytes())
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


def test_pull_request_binds_its_change_to_the_receipt_it_adds(pull) -> None:
    pull()
    result = _audit()
    assert result.exit_code == 0, result.output
    assert "   (added by PR #7)" in result.output.splitlines()[0]
    row = _rows(result.output)["Outputs"]
    assert row.startswith("[+] Outputs       PR #7: 1/1 outputs match at head")
    assert "1 changed file bound" in row
    report = json.loads(_audit("--format", "json").output)
    assert report["facts"]["change"]["bound"] is True


def test_pull_request_change_the_receipt_does_not_bind_is_invalid(pull) -> None:
    pull(extra={"backdoor.py": "import os\n"})
    result = _audit()
    assert result.exit_code == 1, result.output
    assert "PR #7 changes backdoor.py, which the receipt does not bind" in result.output


def test_pull_request_output_edited_after_the_run_is_invalid(pull) -> None:
    pull(output=b"edited after the run\n")
    result = _audit()
    assert result.exit_code == 1, result.output
    assert "result.txt at head differs from its receipt subject" in result.output


def test_pull_request_without_a_receipt_or_with_several_is_exit_2(pull, tmp_path) -> None:
    pull(receipts=0)
    result = _audit()
    assert result.exit_code == 2 and "PR #7 adds no receipt" in result.output
    shutil.rmtree(tmp_path / "repo")
    pull(receipts=2)
    result = _audit()
    assert result.exit_code == 2 and "adds 2 receipts" in result.output


def test_pull_request_commits_missing_from_a_shallow_checkout_is_exit_2(pull, tmp_path) -> None:
    pull()
    event = json.loads((tmp_path / "event.json").read_text())
    event["pull_request"]["base"]["sha"] = "c" * 40
    (tmp_path / "event.json").write_text(json.dumps(event))
    result = _audit()
    assert result.exit_code == 2 and "fetch-depth: 0" in result.output


def test_pull_request_cannot_loosen_its_own_policy(pull, plain_receipt, monkeypatch) -> None:
    strict = f"enforcement: block\nfactories:\n  allow: ['sha256:{'0' * 64}']\n"
    loose = f"enforcement: block\nfactories:\n  allow: ['sha256:{_definition(plain_receipt)}']\n"
    _, base, _ = pull(policy_base=strict, policy_head=loose)
    _fake_apm(monkeypatch)
    result = _audit()
    assert result.exit_code == 1, result.output
    rows = _rows(result.output)
    assert f"apm-policy.yml (repository, PR base {base[:12]})" in rows["Policy"]
    assert rows["Factory"].startswith("[x] Factory") and "not on factories.allow" in rows["Factory"]
    assert "changes apm-policy.yml, which the receipt does not bind" in rows["Outputs"]


def test_explicit_outputs_overrides_the_pull_request_binding(pull, tmp_path) -> None:
    repo, _, _ = pull(extra={"unrelated.md": "x\n"})
    result = _audit("--outputs", repo)
    assert result.exit_code == 0, result.output
    assert "1/1 delivered files match receipt subjects (re-hashed)" in result.output
