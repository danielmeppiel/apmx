"""scripts/run_deliver.py: how run.yml's deliver job pushes a factory's files to a target."""

import importlib.util
import json
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("run_deliver", ROOT / "scripts/run_deliver.py")
run_deliver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_deliver)

# run.yml's deliver job runs on ubuntu-24.04 only (POSIX paths, symbolic links, file:// remotes).
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the deliver job runs on Linux")


def _run(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def remote(tmp_path) -> tuple[str, str]:
    """A bare target repository with one base commit; returns (url, base)."""
    seed = tmp_path / "seed"
    (seed / "src").mkdir(parents=True)
    (seed / "src/app.py").write_text("print('base')\n")
    (seed / "old.txt").write_text("old\n")
    (seed / ".gitignore").write_text("*.json\n")
    (seed / "linked").symlink_to("/tmp")
    _run(seed, "init", "--quiet", "--initial-branch=main")
    _run(seed, "add", "--all")
    _run(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "base")
    bare = tmp_path / "target.git"
    _run(tmp_path, "clone", "--quiet", "--bare", str(seed), str(bare))
    return bare.as_uri(), _run(seed, "rev-parse", "HEAD")


@pytest.fixture
def delivery(tmp_path) -> Path:
    root = tmp_path / "delivery"
    (root / "src").mkdir(parents=True)
    (root / "src/app.py").write_text("print('delivered')\n")
    (root / "src/handoff.json").write_text("{}\n")
    (root / run_deliver.DELETED).write_text("old.txt\n")
    return root


class FakeGitHub:
    def __init__(self, branches=("main",), open_pulls=()):
        self.branches, self.open_pulls, self.calls = set(branches), list(open_pulls), []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/repos/acme/shop":
            return 200, {"default_branch": "main"}
        if path.startswith("/repos/acme/shop/branches/"):
            name = urllib.parse.unquote(path.removeprefix("/repos/acme/shop/branches/"))
            return (200, {}) if name in self.branches else (404, {})
        if method == "GET" and path.startswith("/repos/acme/shop/pulls?"):
            return 200, self.open_pulls
        if method == "PATCH":
            return 200, {"html_url": "https://github.com/acme/shop/pull/7"}
        if method == "POST":
            return 201, {"html_url": "https://github.com/acme/shop/pull/8"}
        return 404, {"message": "Not Found"}


def _deliver(tmp_path, remote, delivery, api, branch="apmx/run-1", mode="pull-request", ref=""):
    url, base = remote
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return run_deliver.deliver(
        api=api,
        env=run_deliver.git_env(home, "https://github.com", "ghs_secret"),
        remote=url,
        work=tmp_path / "work",
        delivery=delivery,
        bundle=tmp_path / "delivered/delivered.bundle",
        target="acme/shop",
        target_ref=ref,
        base=base,
        branch=branch,
        mode=mode,
        title="apmx: acme/factory#v1",
        body="body",
        message="apmx: deliver",
    )


def test_delivers_exactly_the_files_on_the_base_commit_and_opens_a_pr(
    tmp_path, remote, delivery
) -> None:
    api = FakeGitHub()
    result = _deliver(tmp_path, remote, delivery, api)
    url, base = remote
    bare = f"--git-dir={url.removeprefix('file://')}"
    assert _run(tmp_path, bare, "rev-parse", "refs/heads/apmx/run-1") == result["commit"]
    assert _run(tmp_path, bare, "rev-parse", f"{result['commit']}^") == base
    changes = _run(tmp_path, bare, "diff", "--name-status", base, result["commit"]).splitlines()
    # Ignored by the target's .gitignore, still delivered; the deletion list is never committed.
    assert changes == ["D\told.txt", "M\tsrc/app.py", "A\tsrc/handoff.json"]
    assert result["pull-request-url"] == "https://github.com/acme/shop/pull/8"
    (post,) = [call for call in api.calls if call[0] == "POST"]
    assert post[2] == {
        "title": "apmx: acme/factory#v1",
        "head": "apmx/run-1",
        "base": "main",
        "body": "body",
    }


def test_an_open_pr_from_the_branch_is_updated_and_its_base_follows_target_ref(
    tmp_path, remote, delivery
) -> None:
    api = FakeGitHub(branches=("main", "release/1"), open_pulls=[{"number": 7}])
    result = _deliver(tmp_path, remote, delivery, api, ref="release/1")
    assert result["pull-request-url"].endswith("/pull/7") and result["base-branch"] == "release/1"
    (patch,) = [call for call in api.calls if call[0] == "PATCH"]
    assert patch[1] == "/repos/acme/shop/pulls/7" and patch[2]["base"] == "release/1"
    assert not [call for call in api.calls if call[0] == "POST"]


def test_branch_only_delivery_opens_no_pr(tmp_path, remote, delivery) -> None:
    api = FakeGitHub()
    result = _deliver(tmp_path, remote, delivery, api, mode="branch")
    assert result["pull-request-url"] == "" and not [c for c in api.calls if c[0] != "GET"]


def test_bundle_delivery_needs_no_token_and_pushes_nothing(tmp_path, remote, delivery) -> None:
    url, base = remote
    result = _deliver(tmp_path, remote, delivery, None, mode="bundle")
    bare = f"--git-dir={url.removeprefix('file://')}"
    assert _run(tmp_path, bare, "for-each-ref", "refs/heads/apmx") == ""
    # The bundle carries exactly the delivered commit on top of the base.
    _run(
        tmp_path,
        bare,
        "fetch",
        "--quiet",
        str(tmp_path / "delivered/delivered.bundle"),
        "refs/heads/apmx/run-1",
    )
    assert _run(tmp_path, bare, "rev-parse", "FETCH_HEAD") == result["commit"]
    assert _run(tmp_path, bare, "rev-parse", "FETCH_HEAD^") == base


@pytest.mark.parametrize("mode", ["pull-request", "branch"])
def test_pushing_modes_need_a_token(tmp_path, remote, delivery, mode) -> None:
    with pytest.raises(run_deliver.DeliverError, match="needs a token"):
        _deliver(tmp_path, remote, delivery, None, mode=mode)
    with pytest.raises(run_deliver.DeliverError, match="deliver must be one of"):
        _deliver(tmp_path, remote, delivery, FakeGitHub(), mode="merge")


@pytest.mark.parametrize("branch", ["main", "release/1"])
def test_never_pushes_to_the_base_or_default_branch(tmp_path, remote, delivery, branch) -> None:
    api = FakeGitHub(branches=("main", "release/1"))
    with pytest.raises(run_deliver.DeliverError, match="base or default branch"):
        _deliver(tmp_path, remote, delivery, api, branch=branch, ref="release/1")


@pytest.mark.parametrize(
    ("path", "message"),
    [
        (".git/hooks/post-checkout", "inside .git"),
        ("sub/.GIT/config", "inside .git"),
        (".github/workflows/ci.yml", "workflow file"),
        (".GitHub/Workflows/ci.yml", "workflow file"),
    ],
)
def test_refuses_git_internals_and_workflow_files(tmp_path, path, message) -> None:
    root = tmp_path / "delivery"
    (root / path).parent.mkdir(parents=True)
    (root / path).write_text("x")
    with pytest.raises(run_deliver.DeliverError, match=message):
        run_deliver.delivered_files(root)


def test_refuses_symbolic_links_in_the_delivery_and_writes_through_links_in_the_target(
    tmp_path, remote
) -> None:
    root = tmp_path / "delivery"
    root.mkdir()
    (root / "link").symlink_to("/etc/passwd")
    with pytest.raises(run_deliver.DeliverError, match="symbolic link"):
        run_deliver.delivered_files(root)
    (root / "link").unlink()
    (root / "linked").mkdir()
    (root / "linked/evil").write_text("x")
    with pytest.raises(run_deliver.DeliverError, match="symbolic link at linked"):
        _deliver(tmp_path, remote, root, FakeGitHub())


@pytest.mark.parametrize("line", ["../outside", "/etc/passwd", ".git/config", "a/./b"])
def test_deletions_stay_inside_the_repository(tmp_path, line) -> None:
    root = tmp_path / "delivery"
    root.mkdir()
    (root / run_deliver.DELETED).write_text(line + "\n")
    with pytest.raises(run_deliver.DeliverError):
        run_deliver.deleted_files(root)


def test_an_empty_or_no_op_delivery_is_refused(tmp_path, remote) -> None:
    root = tmp_path / "delivery"
    root.mkdir()
    with pytest.raises(run_deliver.DeliverError, match="empty"):
        _deliver(tmp_path, remote, root, FakeGitHub())
    (root / "src").mkdir()
    (root / "src/app.py").write_text("print('base')\n")
    with pytest.raises(run_deliver.DeliverError, match="changes nothing"):
        _deliver(tmp_path, remote, root, FakeGitHub())


def test_git_runs_without_hooks_or_ambient_configuration(tmp_path) -> None:
    env = run_deliver.git_env(tmp_path, "https://github.com/", "ghs_secret")
    config = {
        env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"]
        for i in range(int(env["GIT_CONFIG_COUNT"]))
    }
    assert config["core.hooksPath"] == "/dev/null" and env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull and env["HOME"] == str(tmp_path)
    assert config["http.https://github.com/.extraheader"].startswith("AUTHORIZATION: basic ")
    assert "ghs_secret" not in json.dumps(env)
    assert "GH_TOKEN" not in env and "GITHUB_TOKEN" not in env


def test_the_pr_body_links_the_run_attestation_and_definition() -> None:
    body = run_deliver.summary(
        factory="acme/checkout-factory#v1.0.0",
        definition="d" * 64,
        target="acme/shop",
        base="b" * 40,
        run_url="https://github.com/acme/factory-platform/actions/runs/9",
        platform="acme/factory-platform",
        attestation="https://github.com/acme/factory-platform/attestations/3",
        receipt="r" * 64,
    )
    assert body.startswith(run_deliver.MARKER)
    for text in (
        "https://github.com/acme/factory-platform/actions/runs/9",
        "https://github.com/acme/factory-platform/attestations/3",
        f"`sha256:{'d' * 64}`",
        f"`acme/shop@{'b' * 40}`",
    ):
        assert text in body
    unsigned = run_deliver.summary(
        factory="x`y",
        definition="",
        target="a/b",
        base="c",
        run_url="u",
        platform="p",
        attestation="",
        receipt="",
    )
    assert "not signed" in unsigned and "`x'y`" in unsigned


def test_main_needs_a_token_and_a_valid_target(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("TARGET", "acme/shop")
    args = ["deliver", "--delivery", str(tmp_path), "--bundle", str(tmp_path / "b")]
    assert run_deliver.main(args) == 1
    assert "no token for the target repository" in capsys.readouterr().err
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("TARGET", "acme/../shop")
    assert run_deliver.main(args) == 1
    assert "owner/name" in capsys.readouterr().err


def test_deliver_only_ever_runs_git() -> None:
    """Every subprocess run_deliver.py starts is git; nothing delivered is executed."""
    import ast

    tree = ast.parse((ROOT / "scripts/run_deliver.py").read_text())
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in {"subprocess", "os"}
        and node.func.attr not in {"walk", "environ"}
    ]
    assert [ast.unparse(call.func) for call in calls] == ["subprocess.run"]
    (command,) = [call.args[0] for call in calls]
    assert isinstance(command, ast.List) and ast.literal_eval(command.elts[0]) == "git"
    assert "shell=True" not in (ROOT / "scripts/run_deliver.py").read_text()


def test_delivered_scripts_and_git_settings_are_never_executed(tmp_path, remote) -> None:
    sentinel = tmp_path / "executed"
    root = tmp_path / "delivery"
    (root / ".githooks").mkdir(parents=True)
    script = f"#!/bin/sh\ntouch {sentinel}\n"
    for name in ("setup.py", "install.sh", ".githooks/pre-commit", ".githooks/pre-push"):
        (root / name).write_text(script)
        (root / name).chmod(0o755)
    (root / ".gitattributes").write_text("* filter=run diff=run\n")
    (root / ".gitmodules").write_text('[submodule "x"]\n  path = x\n  url = ./x\n')
    result = _deliver(tmp_path, remote, root, None, mode="bundle")
    assert result["commit"] and not sentinel.exists()
