"""Patch outputs become post-apply subjects; a code-only PR is gated by signed attestations."""

import base64
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

from apmx.audit import command, identity, policy, verifier
from apmx.audit.change import Change, PullRequest
from apmx.cli import main
from apmx.contracts import engine, evidence, patches
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


WORKFLOW = "acme/platform/.github/workflows/run.yml"
SIGNER_SHA = "c" * 40


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


def _statements(bundle: Path) -> list[dict]:
    return [
        json.loads(base64.b64decode(json.loads(line)["payload"]))
        for line in bundle.read_bytes().splitlines()
    ]


# ------------------------------------------------------------------ writer: post-apply files


def test_patch_outputs_become_post_apply_subjects_with_before_images(receipt) -> None:
    parsed, failure = verifier.inspect(receipt)
    assert failure is None, failure
    expected = {
        name: hashlib.sha256(text.encode()).hexdigest()
        for name, text in {**CHANGED, **DOCUMENTED}.items()
        if text is not None
    }
    assert parsed.subjects == expected
    assert parsed.deleted == {"src/old.py": hashlib.sha256(b"old = True\n").hexdigest()}
    assert parsed.before["src/new.py"] is None
    assert parsed.before["src/app.py"] == hashlib.sha256(APP.encode()).hexdigest()
    outputs = receipt.parent / "outputs"
    assert sorted(p.relative_to(outputs).as_posix() for p in outputs.rglob("*") if p.is_file()) == [
        "docs/guide.md",
        "src/app.py",
        "src/new.py",
    ]
    stage = parsed.stages[0]
    assert {item["name"] for item in stage["outputs"]} == {
        "changes.diff",
        "documentation.diff",
        "report.md",
    }
    assert "report.md" not in parsed.subjects


def test_patches_that_do_not_combine_refuse_the_receipt(tmp_path, monkeypatch) -> None:
    scratch = tmp_path / "scratch"
    first = _diff(scratch, BASELINE, {"src/app.py": APP + "one\n"})
    second = _diff(scratch, BASELINE, {"src/app.py": APP + "two\n"})
    with pytest.raises(Exception, match="cannot be combined"):
        _produce(tmp_path, monkeypatch, {"a.diff": first, "b.diff": second})


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


# ------------------------------------------------------------------ the PR


@pytest.fixture
def pull(tmp_path, receipt, monkeypatch):
    """A repository whose PR delivers ``receipt``'s outputs (code only, no receipt)."""

    def build(*, prefix="", base=None, head=None):
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "core.autocrlf", "false")
        _write(repo / prefix, BASELINE)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "base")
        fork = _git(repo, "rev-parse", "HEAD")
        _git(repo, "switch", "-qc", "factory")
        shutil.copytree(receipt.parent / "outputs", repo / prefix, dirs_exist_ok=True)
        (repo / prefix / "src/old.py").unlink()
        if head is not None:
            head(repo / prefix)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "factory delivery")
        head_sha = _git(repo, "rev-parse", "HEAD")
        base_sha = fork
        if base is not None:
            _git(repo, "switch", "-q", "main")
            _write(repo / prefix, base)
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", "base moved")
            base_sha = _git(repo, "rev-parse", "HEAD")
            _git(repo, "switch", "-q", "factory")
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
        return Change.load(repo, PullRequest(base_sha, head_sha, 7, "acme/shop"))

    return build


def _audit(*args: str):
    from click.testing import CliRunner

    return CliRunner().invoke(main, ["audit", *map(str, args)])


def test_explicit_bundle_binds_a_code_only_pr(pull, receipt) -> None:
    pull()
    result = _audit(receipt, "--offline")
    assert result.exit_code == 0, result.output
    row = _rows(result.output)["Outputs"]
    assert row.startswith("[+] Outputs       PR #7: 4 changed files = delivered files at head")
    assert "(1 recorded deletion)" in row and "before-images match base" in row


def test_a_file_the_delivery_does_not_name_is_unbound(pull, receipt) -> None:
    pull(head=lambda root: (root / "src/extra.py").write_text("smuggled = True\n"))
    result = _audit(receipt, "--offline")
    assert result.exit_code == 1, result.output
    assert "PR #7 changes src/extra.py, which is not a delivered file" in result.output


def test_edited_delivered_bytes_are_unbound(pull, receipt) -> None:
    pull(head=lambda root: (root / "src/app.py").write_text("tampered\n"))
    result = _audit(receipt, "--offline")
    assert result.exit_code == 1, result.output
    assert "src/app.py, whose bytes differ from the delivered file" in result.output


def test_an_unrecorded_deletion_is_unbound(pull, receipt) -> None:
    pull(head=lambda root: (root / "docs/guide.md").unlink())
    result = _audit(receipt, "--offline")
    assert result.exit_code == 1, result.output
    assert "deletes docs/guide.md, which the delivery does not delete" in result.output


def test_stale_base_is_refused(pull, receipt) -> None:
    pull(base={"src/app.py": APP + "moved on main\n"})
    result = _audit(receipt, "--offline")
    assert result.exit_code == 1, result.output
    assert "built on a stale base: src/app.py is different at base" in result.output


def test_a_created_file_that_now_exists_at_base_is_stale(pull, receipt) -> None:
    pull(base={"src/new.py": "someone else\n"})
    result = _audit(receipt, "--offline")
    assert result.exit_code == 1, result.output
    assert "src/new.py already exists at base" in result.output


def test_unrelated_base_progress_is_fresh(pull, receipt) -> None:
    pull(base={"README.md": "unrelated\n"})
    result = _audit(receipt, "--offline")
    assert result.exit_code == 0, result.output


# ------------------------------------------------------------------ the gate (attestation store)


def _summary(receipt: Path, *, prefix: str = "", result: str = "PASSED") -> dict:
    statements = _statements(receipt)
    digest = statements[0]["predicate"]["buildDefinition"]["externalParameters"]["factory"][
        "definition"
    ]["sha256"]
    return verifier.statement(
        [
            {"name": prefix + item["name"], "digest": item["digest"]}
            for item in statements[0]["subject"]
        ],
        verifier.VSA,
        {
            "verifier": {"id": f"https://github.com/{WORKFLOW}@{SIGNER_SHA}"},
            "timeVerified": "2026-10-21T18:52:30Z",
            "resourceUri": "git+https://github.com/acme/shop@" + "a" * 40,
            "policy": {"uri": f"apmx:factory/{digest}", "digest": {"sha256": digest}},
            "inputAttestations": [
                {"uri": f"receipt.intoto.jsonl#{n}", "digest": {"sha256": verifier.line_digest(s)}}
                for n, s in enumerate(statements, 1)
            ],
            "verificationResult": result,
            "verifiedLevels": [],
        },
    )


class Store:
    """A fake attestation store: ``gh api`` lists bundles, ``gh attestation verify --bundle``."""

    def __init__(self, documents: list[dict], workflow: str = WORKFLOW) -> None:
        self.documents = documents
        self.workflow = workflow
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> tuple[int, bytes, bytes]:
        self.calls.append(argv)
        if argv[:2] == ["gh", "api"]:
            digest = argv[2].split("sha256:", 1)[1].split("?", 1)[0]
            listed = [
                {"bundle": {"fixture": item}}
                for item in self.documents
                if digest in {s["digest"]["sha256"] for s in item["subject"]}
            ]
            if not listed:
                return 1, b'{"message":"Not Found","status":"404"}', b"gh: Not Found (HTTP 404)\n"
            return 0, json.dumps({"attestations": listed}).encode(), b""
        assert argv[:3] == ["gh", "attestation", "verify"]
        assert "--bundle" in argv and "--deny-self-hosted-runners" in argv
        kind = argv[argv.index("--predicate-type") + 1]
        wanted = argv[argv.index("--signer-workflow") + 1]
        bundle = Path(argv[argv.index("--bundle") + 1])
        documents = [json.loads(line)["fixture"] for line in bundle.read_text().splitlines()]
        found = [
            {
                "attestation": {
                    "bundle": {
                        "dsseEnvelope": {
                            "payload": base64.b64encode(json.dumps(item).encode()).decode()
                        }
                    }
                },
                "verificationResult": {
                    "statement": item,
                    "signature": {
                        "certificate": {
                            "issuer": identity.ISSUER,
                            "runnerEnvironment": "github-hosted",
                            "buildSignerURI": f"https://github.com/{self.workflow}@refs/heads/main",
                            "buildSignerDigest": SIGNER_SHA,
                            "runInvocationURI": "https://github.com/acme/platform/actions/runs/1",
                        }
                    },
                },
            }
            for item in documents
            if item["predicateType"] == kind
        ]
        if not found:
            return 1, b"", f"Error: no attestations found with predicate type: {kind}".encode()
        if wanted != self.workflow:
            return 1, b"", b'Error: verifying with issuer "sigstore.dev"'
        return 0, json.dumps(found).encode(), b""

    def kinds(self) -> list[str]:
        return [
            call[call.index("--predicate-type") + 1]
            for call in self.calls
            if call[:3] == ["gh", "attestation", "verify"]
        ]


def _resolution(**extra) -> policy.Resolution:
    rules = policy.FactoryPolicy(
        source="test",
        signers=(policy.Signer(WORKFLOW, repo="acme/platform", **extra),),
        present=True,
    )
    return policy.Resolution("found", "test policy", policy=rules)


def _gate(change, store, **extra):
    return command.run_gate(change, policy=_resolution(**extra), offline=False, run=store)


def _carrier(receipt: Path, prefix: str = "") -> dict:
    """The receipt bundle as run.yml attests it: one statement over the delivered files."""
    statements = _statements(receipt)
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [
            {"name": prefix + item["name"], "digest": item["digest"]}
            for item in statements[0]["subject"]
        ],
        "predicateType": verifier.BUNDLE_TYPE,
        "predicate": {"lines": statements},
    }


def _store(receipt: Path, **summary) -> Store:
    return Store([_carrier(receipt, summary.get("prefix", "")), _summary(receipt, **summary)])


def test_gate_passes_a_code_only_pr_from_the_attestation_store(pull, receipt) -> None:
    store = _store(receipt)
    audit = _gate(pull(), store, workflow_sha=SIGNER_SHA)
    assert audit.result == "valid", command.to_text(audit)
    assert audit.signer == WORKFLOW
    assert audit.rows["Identity"].status == "pass"
    assert audit.rows["Outputs"].detail.startswith("PR #7: 4 changed files = delivered files")
    assert store.kinds() == [verifier.VSA, verifier.BUNDLE_TYPE]
    lists = [call for call in store.calls if call[:2] == ["gh", "api"]]
    assert lists and all(call[2].startswith("repos/acme/platform/attestations/") for call in lists)


def test_gate_with_a_workspace_prefix(pull, receipt) -> None:
    store = _store(receipt, prefix="app/")
    audit = _gate(pull(prefix="app/"), store)
    assert audit.result == "valid", command.to_text(audit)
    assert audit.facts["subjectPrefix"] == "app/"


def test_gate_refuses_a_pr_without_a_verification_summary(pull, receipt) -> None:
    audit = _gate(pull(), Store([_carrier(receipt)]))
    assert audit.result == "invalid"
    assert "no PASSED verification summary" in audit.rows["Identity"].detail


def test_gate_refuses_a_summary_from_another_signer(pull, receipt) -> None:
    store = Store(
        [_carrier(receipt), _summary(receipt)], workflow="evil/x/.github/workflows/run.yml"
    )
    audit = _gate(pull(), store)
    assert audit.result == "invalid"
    assert audit.rows["Identity"].status == "fail"


def test_gate_refuses_a_failed_summary(pull, receipt) -> None:
    audit = _gate(pull(), _store(receipt, result="FAILED"))
    assert audit.result == "invalid" and audit.rows["Identity"].status == "fail"


def test_gate_refuses_a_summary_that_does_not_cover_a_changed_file(pull, receipt) -> None:
    change = pull(head=lambda root: (root / "src/extra.py").write_text("x = 1\n"))
    store = _store(receipt)
    store.documents[-1]["subject"].append(
        {"name": "unrelated", "digest": {"sha256": hashlib.sha256(b"x = 1\n").hexdigest()}}
    )
    audit = _gate(change, store)
    assert audit.result == "invalid"
    assert audit.rows["Identity"].status == "fail"


def test_gate_refuses_a_signer_at_another_commit(pull, receipt, monkeypatch) -> None:
    monkeypatch.setattr(identity, "_tag_target", lambda *_: None)
    audit = _gate(pull(), _store(receipt), workflow_sha="d" * 40)
    assert audit.result == "invalid"
    assert "policy requires dddddddddddd" in audit.rows["Identity"].detail


def test_gate_refuses_a_tampered_input_attestation(pull, receipt) -> None:
    store = _store(receipt)
    store.documents[0]["predicate"]["lines"][1]["predicate"]["result"] = "PASSED "
    audit = _gate(pull(), store)
    assert audit.result == "invalid"
    assert audit.rows["Integrity"].detail.startswith("input attestation receipt.intoto.jsonl#2")


def test_gate_refuses_a_stale_base(pull, receipt) -> None:
    audit = _gate(pull(base={"docs/guide.md": "moved\n"}), _store(receipt))
    assert audit.result == "invalid"
    assert audit.rows["Outputs"].detail.startswith("built on a stale base: docs/guide.md")


def test_gate_without_signers_is_incomplete(pull, receipt) -> None:
    resolution = policy.Resolution("none", "none found")
    audit = command.run_gate(pull(), policy=resolution, run=_store(receipt))
    assert (audit.result, audit.exit_code) == ("incomplete", 2)
    assert "factories.signers" in audit.rows["Identity"].detail


def test_gate_is_the_default_in_a_pull_request(pull, receipt, monkeypatch) -> None:
    pull()
    (Path.cwd() / "apm-policy.yml").write_text(
        f"factories:\n  signers:\n    - workflow: {WORKFLOW}\n      repo: acme/platform\n"
    )
    store = _store(receipt)
    monkeypatch.setattr(identity, "_gh", store)
    result = _audit("--offline")
    assert result.exit_code == 2, result.output
    assert "signature not checked (offline)" in result.output


def _with_base_commit(receipt: Path, commit: str) -> None:
    statements = _statements(receipt)
    base = statements[0]["predicate"]["buildDefinition"]["resolvedDependencies"][0]
    base["digest"]["gitCommit"] = commit
    base["uri"] = f"git+https://github.com/acme/shop@{commit}"
    receipt.chmod(0o600)
    receipt.write_bytes(verifier.bundle_bytes(statements))


def test_a_recorded_base_commit_must_be_in_the_pr_base_history(pull, receipt) -> None:
    change = pull()
    _with_base_commit(receipt, change.merge_base)
    result = _audit(receipt, "--offline")
    assert result.exit_code == 0, result.output
    assert f"built on {change.merge_base[:12]}, in the PR base's history" in result.output
    _git(change.root, "switch", "-qc", "elsewhere", change.merge_base)
    (change.root / "other.txt").write_text("x\n")
    _git(change.root, "add", "-A")
    _git(change.root, "commit", "-qm", "unrelated")
    unrelated = _git(change.root, "rev-parse", "HEAD")
    _git(change.root, "switch", "-q", "factory")
    _with_base_commit(receipt, unrelated)
    result = _audit(receipt, "--offline")
    assert result.exit_code == 1, result.output
    assert f"it was built on {unrelated[:12]}, which is not in the PR base's history" in (
        result.output
    )
    _with_base_commit(receipt, "e" * 40)
    result = _audit(receipt, "--offline")
    assert result.exit_code == 2, result.output
    assert "is not in this checkout" in result.output
