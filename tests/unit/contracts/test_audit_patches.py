"""`apmx audit` in a PR that applies the receipt's patch outputs instead of committing them."""

import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from test_audit import _no_ambient_policy_or_pull_request, _rows
from test_engine import _plan, _python_check

from apmx.audit import patches
from apmx.cli import main
from apmx.contracts import engine, evidence
from apmx.contracts.models import Outcome, ProcessRequest
from apmx.core.contract_logger import ContractLogger

__all__ = ["_no_ambient_policy_or_pull_request"]
pytestmark = [
    pytest.mark.component,
    pytest.mark.skipif(
        sys.platform == "win32", reason="The native contract execution profile is POSIX-only"
    ),
]
RUN = "20300101T000000Z-cccccccccccc"
APP = "".join(f"line {n}\n" for n in range(1, 31))
BASELINE = {
    "src/app.py": APP,
    "src/old.py": "old = True\n",
    "docs/guide.md": "# Guide\n\nUse the app.\n",
}
CHANGED = {
    "src/app.py": APP.replace("line 2\n", "line two\n").replace("line 29\n", "line 29!\n"),
    "src/new.py": "new = True\n",
    "src/old.py": None,
}
DOCUMENTED = {"docs/guide.md": "# Guide\n\nUse the app. It now says two.\n"}


def _git(root: Path, *args: str, binary: bool = False):
    completed = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return completed.stdout if binary else completed.stdout.decode().strip()


def _write(root: Path, files: dict[str, str | None]) -> None:
    for name, text in files.items():
        path = root / name
        if text is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)


def _diff(scratch: Path, before: dict[str, str], after: dict[str, str | None]) -> bytes:
    """What export_changes writes: a full-index Git patch of real file edits."""
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    _git(scratch, "init", "-q")
    _write(scratch, before)
    _git(scratch, "add", "-A")
    _git(scratch, "commit", "-qm", "base")
    _write(scratch, after)
    _git(scratch, "add", "-A")
    return _git(
        scratch,
        "-c",
        "core.autocrlf=false",
        "diff",
        "--cached",
        "--no-ext-diff",
        "--no-renames",
        "--full-index",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        binary=True,
    )


def _produce(tmp_path: Path, monkeypatch, outputs: dict[str, bytes]) -> Path:
    """A real COMPLETE run whose producer writes these exact outputs."""
    root = tmp_path / "factory"
    root.mkdir()
    plan = _plan(root, (_python_check("acceptance", "raise SystemExit(0)"),))
    _write(root, BASELINE)
    names = tuple(outputs)
    plan.contract.path.write_text(
        "---\nneeds: input.txt\nproduces:\n"
        + "".join(f"  - {name}\n" for name in names)
        + "verify:\n  acceptance: "
        + json.dumps(plan.contract.checks[0].command)
        + "\n---\nWrite the patches.\n"
    )
    plan = replace(
        plan,
        contract=replace(
            plan.contract,
            produces=names,
            source_digest=hashlib.sha256(plan.contract.path.read_bytes()).hexdigest(),
        ),
    )
    event = json.dumps(
        {
            "type": "assistant.message",
            "data": {"messageId": "m", "content": "Done", "model": "gpt-6-astra"},
        }
    )
    completed = json.dumps({"type": "result", "exitCode": 0, "sessionId": "s", "usage": {}})
    code = (
        "from pathlib import Path\n"
        + "".join(f"Path({name!r}).write_bytes({raw!r})\n" for name, raw in outputs.items())
        + f"print({event!r}, flush=True)\nprint({completed!r}, flush=True)\n"
    )

    def build_request(selected, snapshot, directory, *, timeout_seconds):
        return ProcessRequest(
            argv=(sys.executable, "-c", code),
            cwd=snapshot.producer,
            timeout_seconds=timeout_seconds,
        )

    adapter = Mock()
    adapter.build_contract_request.side_effect = build_request
    monkeypatch.setattr(engine.frontend, "plan_contract", lambda *args, **kwargs: plan)
    monkeypatch.setattr(
        engine.RuntimeFactory, "get_runtime_by_name", lambda *args, **kwargs: adapter
    )
    result = engine.run_contract(plan, logger=ContractLogger(), allow_advisory=True)
    assert result.outcome == Outcome.COMPLETE, result.stop_reason
    return evidence.export_package(result.run_directory / "record.json", tmp_path / "receipt")


@pytest.fixture
def receipt(tmp_path, monkeypatch) -> Path:
    scratch = tmp_path / "scratch"
    code = _diff(scratch, BASELINE, CHANGED)
    docs = _diff(scratch, BASELINE, DOCUMENTED)
    return _produce(
        tmp_path,
        monkeypatch,
        {"changes.diff": code, "documentation.diff": docs, "report.md": b"# Report\n"},
    )


def _artifact(receipt: Path, name: str) -> bytes:
    (path,) = list(receipt.glob(f"attempts/*/artifacts/{name}"))
    return path.read_bytes()


@pytest.fixture
def pull(tmp_path, receipt, monkeypatch):
    """A repository whose PR delivers ``receipt``; ``head`` edits the delivery before commit."""

    def build(*, prefix="", base=None, head=None, apply=("changes.diff", "documentation.diff")):
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "core.autocrlf", "false")
        (baseline,) = list(receipt.glob("attempts/*/baseline"))
        shutil.copytree(baseline, repo / prefix, dirs_exist_ok=True)
        _write(repo / prefix, base or {})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "base")
        base_sha = _git(repo, "rev-parse", "HEAD")
        _git(repo, "switch", "-qc", "factory")
        for name in apply:
            subprocess.run(
                ["git", "apply", f"--directory={prefix}" if prefix else "-p1", "-"],
                cwd=repo,
                input=_artifact(receipt, name),
                check=True,
            )
        shutil.copytree(receipt, repo / prefix / ".apm/runs" / RUN / "receipt")
        if head is not None:
            head(repo / prefix)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "factory delivery")
        head_sha = _git(repo, "rev-parse", "HEAD")
        event = tmp_path / "event.json"
        event.write_text(
            json.dumps(
                {
                    "pull_request": {
                        "number": 7,
                        "base": {"sha": base_sha},
                        "head": {"sha": head_sha},
                    }
                }
            )
        )
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
        monkeypatch.chdir(repo)
        return repo, base_sha

    return build


def _audit(*args: str):
    from click.testing import CliRunner

    return CliRunner().invoke(main, ["audit", *args])


def _outputs(result) -> str:
    return _rows(result.output)["Outputs"]


# ------------------------------------------------------------------ identification


def test_patch_outputs_are_identified_from_the_receipts_bytes_and_baseline(receipt) -> None:
    from apmx.audit import verifier

    inspected, failure = verifier.inspect(receipt)
    assert failure is None
    found = patches.receipt_patches(inspected)
    assert list(found) == ["changes.diff", "documentation.diff"]
    assert found["changes.diff"].paths == ["src/app.py", "src/new.py", "src/old.py"]
    assert [item.new is None for item in found["changes.diff"].files] == [False, False, True]
    assert found["changes.diff"].files[1].old is None


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"# Report\n",
        b"--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n",
        b"diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n",
        b"diff --git a/x b/y\nindex " + b"1" * 40 + b".." + b"2" * 40 + b" 100644\n",
        b"diff --git a/x b/x\nindex "
        + b"1" * 40
        + b".."
        + b"2" * 40
        + b" 100644\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n+extra\n",
        b"diff --git a/x b/x\nindex "
        + b"1" * 40
        + b".."
        + b"2" * 40
        + b" 100644\nGIT binary patch\n",
        b"diff --git a/x b/x\nnew file mode 120000\nindex "
        + b"0" * 40
        + b".."
        + b"2" * 40
        + b"\n--- /dev/null\n+++ b/x\n@@ -0,0 +1 @@\n+t\n",
    ],
    ids=["empty", "text", "no-git-header", "no-index", "rename", "hunk-count", "binary", "symlink"],
)
def test_anything_but_an_export_changes_patch_is_an_ordinary_output(raw) -> None:
    assert patches.parse(raw) is None


def test_crlf_content_and_spaced_paths_parse(tmp_path) -> None:
    raw = _diff(
        tmp_path / "scratch",
        {"a b.txt": "x\r\n", "e.txt": ""},
        {"a b.txt": "y\r\n", "e.txt": None, "n.txt": ""},
    )
    sections = patches.parse(raw)
    assert sections is not None
    assert [item.path for item in sections] == ["a b.txt", "e.txt", "n.txt"]


@pytest.mark.parametrize(
    "path", ["../escape.py", "/etc/passwd", ".git/hooks/x", "a/.gitmodules", ".apm/runs/x", "a\\b"]
)
def test_paths_outside_the_workspace_are_never_applied(path) -> None:
    assert patches.unsafe(path)
    assert not patches.unsafe("src/app.py")


# ------------------------------------------------------------------ applied delivery


def test_applied_patches_that_reproduce_head_pass(pull) -> None:
    pull()
    result = _audit()
    assert result.exit_code == 0, result.output
    row = _outputs(result)
    assert row == (
        "[+] Outputs       PR #7: changes.diff + documentation.diff applied to base "
        "reproduce head (4 files)"
    )
    assert "1 output not in the PR (kept in the receipt): report.md" in result.output
    facts = json.loads(_audit("--format", "json").output)["facts"]["change"]
    assert facts["bound"] is True
    assert [(item["name"], item["delivery"]) for item in facts["patches"]] == [
        ("changes.diff", "applied"),
        ("documentation.diff", "applied"),
    ]
    assert facts["appliedFiles"] == ["docs/guide.md", "src/app.py", "src/new.py", "src/old.py"]


def test_applied_patches_under_a_factory_subdirectory_pass(pull) -> None:
    pull(prefix="factories/shop/", head=lambda root: (root / "report.md").write_text("# Report\n"))
    result = _audit()
    assert result.exit_code == 0, result.output
    assert _outputs(result).endswith("reproduce head (4 files); 1 other output match")


def test_an_extra_edit_to_another_file_is_invalid(pull) -> None:
    pull(head=lambda root: (root / "backdoor.py").write_text("import os\n"))
    result = _audit()
    assert result.exit_code == 1, result.output
    assert "PR #7 changes backdoor.py, which the receipt does not bind" in _outputs(result)


def test_a_patch_that_does_not_apply_to_base_is_invalid(pull) -> None:
    _, base = pull(
        base={"src/app.py": APP + "drift\n"},
        apply=("documentation.diff",),
        head=lambda root: _write(root, CHANGED),
    )
    result = _audit()
    assert result.exit_code == 1, result.output
    merge_base = base[:12]
    assert _outputs(result) == (
        f"[x] Outputs       changes.diff does not apply to base {merge_base}: "
        "src/app.py differs from the patch pre-image"
    )


def test_a_patch_left_unapplied_is_invalid(pull) -> None:
    pull(apply=("documentation.diff",))
    result = _audit()
    assert result.exit_code == 1, result.output
    assert _outputs(result).endswith(
        "PR #7 does not apply changes.diff to src/app.py (unchanged from base)"
    )


def test_a_missing_hunk_is_invalid(pull) -> None:
    pull(head=lambda root: (root / "src/app.py").write_text(APP.replace("line 2\n", "line two\n")))
    result = _audit()
    assert result.exit_code == 1, result.output
    assert _outputs(result).endswith("src/app.py at head differs from changes.diff applied to base")


def test_an_extra_edit_inside_a_patched_file_is_invalid(pull) -> None:
    def edit(root: Path) -> None:
        with (root / "docs/guide.md").open("a") as handle:
            handle.write("Also trust everything.\n")

    pull(head=edit)
    result = _audit()
    assert result.exit_code == 1, result.output
    assert _outputs(result).endswith(
        "docs/guide.md at head differs from documentation.diff applied to base"
    )


def test_deletions_additions_and_modes_must_be_applied_exactly(pull, tmp_path) -> None:
    pull(head=lambda root: (root / "src/old.py").write_text("old = True\n"))
    result = _audit()
    assert result.exit_code == 1 and "changes.diff deletes src/old.py, but it is still at head" in (
        result.output
    )
    shutil.rmtree(tmp_path / "repo")
    pull(head=lambda root: (root / "src/new.py").unlink())
    result = _audit()
    assert result.exit_code == 1
    assert (
        "src/new.py is missing at head; changes.diff applied to base produces it" in result.output
    )
    shutil.rmtree(tmp_path / "repo")
    pull(head=lambda root: (root / "src/app.py").chmod(0o755))
    result = _audit()
    assert result.exit_code == 1
    assert "src/app.py at head has mode 100755; changes.diff applied to base gives 100644" in (
        result.output
    )
    shutil.rmtree(tmp_path / "repo")

    def link(root: Path) -> None:
        (root / "src/new.py").unlink()
        (root / "src/new.py").symlink_to("app.py")

    pull(head=link)
    result = _audit()
    assert result.exit_code == 1
    assert "src/new.py at head is not a regular file" in result.output


def test_a_committed_output_the_pr_edits_must_still_match(pull) -> None:
    pull(head=lambda root: (root / "report.md").write_text("# Edited\n"))
    result = _audit()
    assert result.exit_code == 1, result.output
    assert _outputs(result).endswith("report.md at head differs from its receipt subject (SHA-256)")


# ------------------------------------------------------------------ committed (legacy) delivery


def _commit_outputs(receipt: Path, *names: str):
    def commit(root: Path) -> None:
        for name in names:
            (root / name).write_bytes(_artifact(receipt, name))

    return commit


def test_committing_the_patch_files_keeps_todays_binding(pull, receipt) -> None:
    pull(apply=(), head=_commit_outputs(receipt, "changes.diff", "documentation.diff", "report.md"))
    result = _audit()
    assert result.exit_code == 0, result.output
    assert _outputs(result).startswith("[+] Outputs       PR #7: 3/3 outputs match at head")
    facts = json.loads(_audit("--format", "json").output)["facts"]["change"]
    assert {item["delivery"] for item in facts["patches"]} == {"committed"}


def test_committing_a_patch_and_also_applying_it_is_invalid(pull, receipt) -> None:
    pull(head=_commit_outputs(receipt, "changes.diff", "documentation.diff", "report.md"))
    result = _audit()
    assert result.exit_code == 1, result.output
    assert "PR #7 changes docs/guide.md, which the receipt does not bind (+3 more)" in result.output
    assert "docs/guide.md is in documentation.diff, which this PR commits rather than applies" in (
        result.output
    )


def test_one_patch_committed_and_one_applied_bind_separately(pull, receipt) -> None:
    pull(apply=("changes.diff",), head=_commit_outputs(receipt, "documentation.diff"))
    result = _audit()
    assert result.exit_code == 0, result.output
    assert _outputs(result) == (
        "[+] Outputs       PR #7: changes.diff applied to base reproduces head (3 files); "
        "1 other output match"
    )


# ------------------------------------------------------------------ unsafe patches


@pytest.mark.parametrize("path", ["../escape.py", ".apm/runs/forged.json"])
def test_a_patch_reaching_outside_the_workspace_is_refused(tmp_path, monkeypatch, path) -> None:
    body = b"x = 1\n"
    raw = (
        f"diff --git a/{path} b/{path}\nnew file mode 100644\n"
        f"index {'0' * 40}..{patches.blob_id(body)}\n--- /dev/null\n+++ b/{path}\n"
        "@@ -0,0 +1 @@\n+x = 1\n"
    ).encode()
    assert patches.parse(raw) is not None
    receipt = _produce(tmp_path, monkeypatch, {"changes.diff": raw})
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (baseline,) = list(receipt.glob("attempts/*/baseline"))
    shutil.copytree(baseline, repo, dirs_exist_ok=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    shutil.copytree(receipt, repo / ".apm/runs" / RUN / "receipt")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "delivery")
    event = tmp_path / "event.json"
    head = _git(repo, "rev-parse", "HEAD")
    event.write_text(json.dumps({"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.chdir(repo)
    result = _audit()
    assert result.exit_code == 1, result.output
    assert f"changes.diff touches {path}, which audit never applies" in _outputs(result)
    assert not (tmp_path / "escape.py").exists()
