"""Deliver a factory run to a target repository (.github/workflows/run.yml, deliver job).

Standard library only, like run_stage.py. It runs no candidate code: it
copies the delivered bytes onto the target's base commit with git (no hooks,
no filters, no global or system configuration) and writes that commit as a
git bundle. Depending on the mode it then pushes one branch (`branch`) and
opens or updates one pull request through the GitHub REST API
(`pull-request`), or stops at the bundle (`bundle`, no token needed).

The delivery directory mirrors the target repository's root. An optional
`.apmx-deleted` file at its root lists paths to delete, one per line; it is
read, never committed. Deliver refuses paths inside `.git` and under
`.github/workflows/`, symbolic links, and writes through a symbolic link in
the target, and never pushes to the pull request's base branch or the
target's default branch.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path, PurePosixPath

DELETED = ".apmx-deleted"
MARKER = "<!-- apmx-deliver -->"
_REPOSITORY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
MODES = ("pull-request", "branch", "bundle")
_AUTHOR = ("apmx factory platform", "apmx-factory-platform@users.noreply.github.com")


class DeliverError(Exception):
    pass


def repository(value: str) -> str:
    if not _REPOSITORY.fullmatch(value) or ".." in value:
        raise DeliverError(f"target repository must be owner/name: {value!r}")
    return value


def _relative(value: str, what: str) -> str:
    path = PurePosixPath(value)
    parts = path.parts
    raw = value.split("/")
    if not value or path.is_absolute() or "\\" in value or {"", ".", ".."} & set(raw):
        raise DeliverError(f"{what} must be a relative path in the repository: {value!r}")
    if any(part.casefold() == ".git" for part in parts):
        raise DeliverError(f"{what} is inside .git: {value}")
    if [part.casefold() for part in parts[:2]] == [".github", "workflows"]:
        raise DeliverError(
            f"{what} is a workflow file: {value}. Deliver never writes .github/workflows/ "
            "(a delivered workflow would run in the target repository with its secrets)"
        )
    return path.as_posix()


def delivered_files(delivery: Path) -> dict[str, Path]:
    """Every regular file to deliver, by its path in the target repository."""
    files: dict[str, Path] = {}
    for directory, names, filenames in os.walk(delivery):
        here = Path(directory)
        for name in [*names, *filenames]:
            if (here / name).is_symlink():
                raise DeliverError(f"the delivery contains a symbolic link: {name}")
        for name in filenames:
            path = here / name
            relative = path.relative_to(delivery).as_posix()
            if relative == DELETED:
                continue
            files[_relative(relative, "delivered file")] = path
    return dict(sorted(files.items()))


def deleted_files(delivery: Path) -> list[str]:
    listing = delivery / DELETED
    if not listing.is_file():
        return []
    lines = listing.read_text(encoding="utf-8").splitlines()
    return sorted({_relative(line.strip(), "deleted file") for line in lines if line.strip()})


def git_env(home: Path, server: str, token: str | None) -> dict[str, str]:
    """A git environment with no hooks, global or system configuration, or prompts."""
    config = {
        "core.hooksPath": "/dev/null",
        "core.autocrlf": "false",
        "core.fsmonitor": "false",
        "commit.gpgsign": "false",
        "protocol.allow": "never",
        "protocol.https.allow": "always",
        "protocol.file.allow": "always",
    }
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        config[f"http.{server.rstrip('/')}/.extraheader"] = f"AUTHORIZATION: basic {basic}"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "LANG": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": str(len(config)),
    }
    for index, (key, value) in enumerate(config.items()):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


def _git(env: dict[str, str], cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        lines = (completed.stderr or completed.stdout).strip().splitlines()
        raise DeliverError(f"git {args[0]} failed: {lines[-1] if lines else 'no output'}")
    return completed.stdout.strip()


def _no_link_on_the_way(work: Path, relative: str) -> Path:
    path = work
    for part in PurePosixPath(relative).parts:
        path = path / part
        if path.is_symlink():
            raise DeliverError(
                f"{relative}: the target has a symbolic link at {path.relative_to(work)}"
            )
    return path


def build_commit(
    env: dict[str, str],
    work: Path,
    remote: str,
    base: str,
    files: dict[str, Path],
    deleted: list[str],
    message: str,
) -> str:
    """Commit the delivered files on top of the base commit; return the new commit."""
    if not _COMMIT.fullmatch(base):
        raise DeliverError(f"base commit must be a 40-hex commit SHA: {base!r}")
    work.mkdir(parents=True, exist_ok=True)
    _git(env, work, "init", "--quiet")
    _git(env, work, "fetch", "--quiet", "--no-tags", "--depth=1", remote, base)
    _git(env, work, "checkout", "--quiet", "--detach", "FETCH_HEAD")
    if _git(env, work, "rev-parse", "HEAD") != base:
        raise DeliverError(f"fetched {remote} at a commit other than {base}")
    for relative, source in files.items():
        target = _no_link_on_the_way(work, relative)
        if target.is_dir():
            raise DeliverError(f"{relative} is a directory in the target")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    for relative in deleted:
        target = work / relative
        if relative in files or not target.is_file():
            raise DeliverError(f"deleted file is not a file at the base commit: {relative}")
        _no_link_on_the_way(work, relative).unlink()
    _git(env, work, "add", "--all", "--force", "--", ".")
    if not _git(env, work, "status", "--porcelain"):
        raise DeliverError("the delivery changes nothing at the base commit")
    name, email = _AUTHOR
    identity = {
        **env,
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }
    _git(identity, work, "commit", "--quiet", "--no-verify", "--message", message)
    return _git(env, work, "rev-parse", "HEAD")


class GitHub:
    """Minimal GitHub REST client; the token never leaves the Authorization header."""

    def __init__(self, token: str, api: str = "https://api.github.com") -> None:
        self._token = token
        self._api = api.rstrip("/")

    def __call__(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self._api + path, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._token}")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read() or b"null")
            except ValueError:
                payload = None
            return exc.code, payload


Api = Callable[..., tuple[int, object]]


def _ok(api: Api, method: str, path: str, body: dict | None = None) -> object:
    status, payload = api(method, path, body)
    if status >= 300:
        message = payload.get("message") if isinstance(payload, dict) else payload
        raise DeliverError(f"GitHub API {method} {path.split('?')[0]}: HTTP {status} {message}")
    return payload


def base_branch(api: Api, repo: str, ref: str) -> tuple[str, str]:
    """The pull request's base branch and the target's default branch.

    The base is target-ref when it names a branch; otherwise (empty, a tag or a
    commit) it is the default branch.
    """
    default = _ok(api, "GET", f"/repos/{repo}")["default_branch"]
    if ref:
        status, _ = api("GET", f"/repos/{repo}/branches/{urllib.parse.quote(ref, safe='')}")
        if status == 200:
            return ref, default
    return default, default


def open_or_update(
    api: Api, repo: str, branch: str, base: str, title: str, body: str
) -> tuple[str, str]:
    """Open a pull request from branch, or update the open one; return (action, url)."""
    owner = repo.split("/")[0]
    head = urllib.parse.quote(f"{owner}:{branch}", safe="")
    existing = _ok(api, "GET", f"/repos/{repo}/pulls?state=open&head={head}")
    if existing:
        number = existing[0]["number"]
        pull = _ok(
            api,
            "PATCH",
            f"/repos/{repo}/pulls/{number}",
            {"title": title, "body": body, "base": base},
        )
        return "updated", pull["html_url"]
    pull = _ok(
        api,
        "POST",
        f"/repos/{repo}/pulls",
        {"title": title, "head": branch, "base": base, "body": body},
    )
    return "opened", pull["html_url"]


def _code(value: str) -> str:
    return "`" + value.replace("`", "'").replace("\n", " ") + "`"


def summary(
    *,
    factory: str,
    definition: str,
    target: str,
    base: str,
    run_url: str,
    platform: str,
    attestation: str,
    receipt: str,
) -> str:
    """The pull request body: where these bytes came from and what vouches for them."""
    rows = [
        ("Factory", _code(factory)),
        ("Factory definition", _code(f"sha256:{definition}") if definition else "unknown"),
        ("Built on", _code(f"{target}@{base}")),
        ("Platform run", run_url),
        ("Attestation", attestation or "none: this run was not signed (`attest: false`)"),
    ]
    if receipt:
        rows.append(("Signed receipt", _code(f"sha256:{receipt}")))
    table = "\n".join(f"| {name} | {value} |" for name, value in rows)
    return (
        f"{MARKER}\n## apmx factory delivery\n\n| | |\n| --- | --- |\n{table}\n\n"
        f"This pull request contains only the delivered files. The `apmx audit` gate "
        f"verifies them against the attestation stored in `{platform}`; nothing in it "
        "was run by the platform's delivery job.\n"
    )


def deliver(
    *,
    api: Api | None,
    env: dict[str, str],
    remote: str,
    work: Path,
    delivery: Path,
    bundle: Path,
    target: str,
    target_ref: str,
    base: str,
    branch: str,
    mode: str,
    title: str,
    body: str,
    message: str,
) -> dict[str, str]:
    if mode not in MODES:
        raise DeliverError(f"deliver must be one of {', '.join(MODES)}: {mode!r}")
    files, deleted = delivered_files(delivery), deleted_files(delivery)
    if not files and not deleted:
        raise DeliverError("the delivery is empty")
    _git(env, work.parent, "check-ref-format", "--branch", branch)
    into = ""
    if mode != "bundle":
        if api is None:
            raise DeliverError(f"deliver: {mode} needs a token for the target repository")
        into, default = base_branch(api, target, target_ref)
        if branch in {into, default}:
            raise DeliverError(f"refusing to push to {branch}: it is the base or default branch")
    commit = build_commit(env, work, remote, base, files, deleted, message)
    _git(env, work, "branch", "--force", branch, "HEAD")
    bundle.parent.mkdir(parents=True, exist_ok=True)
    _git(env, work, "bundle", "create", "--quiet", str(bundle), f"refs/heads/{branch}", f"^{base}")
    result = {"commit": commit, "branch": branch, "base-branch": into, "pull-request-url": ""}
    if mode == "bundle":
        print(f"Wrote {commit} on {target}@{base[:12]} as refs/heads/{branch} in {bundle.name}")
        return result
    _git(env, work, "push", "--quiet", "--force", remote, f"HEAD:refs/heads/{branch}")
    print(f"Pushed {commit} to {target}:{branch} (base {base[:12]} on {into})")
    if mode == "pull-request":
        action, url = open_or_update(api, target, branch, into, title, body)
        result["pull-request-url"] = url
        print(f"{action.capitalize()} {url}")
    return result


def _outputs(values: dict[str, str]) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            stream.writelines(f"{key}={value}\n" for key, value in values.items())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    delivering = commands.add_parser("deliver")
    delivering.add_argument("--delivery", type=Path, required=True)
    delivering.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args(argv)
    env = os.environ
    try:
        token = env.get("GH_TOKEN", "")
        mode = env.get("DELIVER", "pull-request")
        if mode != "bundle" and not token:
            raise DeliverError(
                "no token for the target repository: pass target-app-client-id and the "
                "target-app-private-key secret (or the target-token secret), or deliver: bundle"
            )
        target = repository(env["TARGET"])
        server = env.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
        platform = env.get("GITHUB_REPOSITORY", "")
        run_url = f"{server}/{platform}/actions/runs/{env.get('GITHUB_RUN_ID', '')}"
        if env.get("GITHUB_RUN_ATTEMPT", "1") != "1":
            run_url += f"/attempts/{env['GITHUB_RUN_ATTEMPT']}"
        factory, base = env.get("FACTORY", ""), env.get("BASE_COMMIT", "")
        definition = env.get("DEFINITION", "")
        body = summary(
            factory=factory,
            definition=definition,
            target=target,
            base=base,
            run_url=run_url,
            platform=platform,
            attestation=env.get("ATTESTATION_URL", ""),
            receipt=env.get("RECEIPT_SHA256", ""),
        )
        message = (
            f"apmx: deliver {factory}\n\nApmx-Run: {run_url}\n"
            f"Apmx-Definition: sha256:{definition}\nApmx-Base: {target}@{base}\n"
        )
        with tempfile.TemporaryDirectory() as scratch:
            home = Path(scratch) / "home"
            home.mkdir()
            result = deliver(
                api=GitHub(token, env.get("GITHUB_API_URL", "https://api.github.com"))
                if token
                else None,
                env=git_env(home, server, token),
                remote=f"{server}/{target}.git",
                work=Path(scratch) / "target",
                delivery=args.delivery,
                bundle=args.bundle,
                target=target,
                target_ref=env.get("TARGET_REF", ""),
                base=base,
                branch=env.get("BRANCH") or f"apmx/run-{env.get('GITHUB_RUN_ID', 'local')}",
                mode=mode,
                title=env.get("TITLE") or f"apmx: {factory}",
                body=body,
                message=message,
            )
        _outputs(result)
        summary_path = env.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as stream:
                link = result["pull-request-url"] or f"`{result['branch']}` ({mode})"
                stream.write(f"{body}\nDelivered to {target}: {link}\n")
    except (DeliverError, KeyError, OSError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
