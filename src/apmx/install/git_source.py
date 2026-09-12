"""Bounded direct Git acquisition through the single credential resolver."""

import os
import shutil
from pathlib import Path
from uuid import uuid4

from ..contracts.models import ContractError, ContractLimits, ProcessRequest
from ..contracts.process import supervise_process
from ..core.auth import AuthResolver
from ..core.tls_trust import build_child_tls_env
from ..utils.git_env import get_git_executable
from ..utils.github_host import is_full_commit_sha
from ..utils.path_security import ensure_path_within, has_symlink_component, safe_rmtree
from ..utils.subprocess_env import external_process_env


def download_git(dependency, target: Path):
    from .contract_source_validation import bounded_tree, validate_reference

    validate_reference(dependency)
    if dependency.is_insecure or dependency.artifactory_prefix or dependency.ref_kind == "semver":
        raise ContractError(
            "Acquisition requires an HTTPS/SSH Git repository and a literal revision, not HTTP, "
            "a registry proxy or a version range.", code="unsupported_source",
        )
    url = dependency.to_clone_url()
    resolver = AuthResolver()
    context = resolver.resolve_for_dep(dependency)
    env = build_child_tls_env(external_process_env(resolver.git_env_for_remote(context, url)))
    env.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    git = get_git_executable()
    target.parent.mkdir(parents=True, exist_ok=True)
    private = target.parent / (".apmx-git-" + uuid4().hex)
    private.mkdir(mode=0o700)

    def run(*args):
        output = bytearray()

        def receive(stream, chunk):
            if len(output) + len(chunk) > 512 * 1024:
                raise ContractError("Git acquisition output exceeded its bound.", code="source_limit")
            # Stderr can contain private diagnostics; never surface or retain it.
            if stream == "stdout":
                output.extend(chunk)

        observed = supervise_process(
            ProcessRequest(
                (git, "-c", f"core.hooksPath={os.devnull}", "-c", "core.autocrlf=false",
                 "-c", "core.fsmonitor=false", "-c", "protocol.file.allow=never", *args),
                private, 60, env,
            ),
            on_bytes=receive,
        )
        if observed.stop_reason or observed.returncode != 0 or not observed.cleanup_confirmed:
            raise ContractError(
                "Git package acquisition failed. Check the explicit repository/revision and Git "
                "authentication. Private transport diagnostics were not retained.",
                code="source_acquisition",
            )
        return bytes(output)

    try:
        run("init", "--quiet")
        run("fetch", "--quiet", "--depth=1", "--no-tags", "--", url, dependency.reference or "HEAD")
        revision = run("rev-parse", "--verify", "FETCH_HEAD^{commit}").decode("ascii").strip()
        if not is_full_commit_sha(revision):
            raise ContractError("Git did not resolve a full source commit.", code="unresolved_source")
        run("checkout", "--quiet", "--detach", revision)
        selected = ensure_path_within(private / (dependency.virtual_path or ""), private)
        if has_symlink_component(private, selected):
            raise ContractError("Selected package contains a symlink.", code="source_escape")
        bounded_tree(selected, ContractLimits())
        shutil.copytree(selected, target, ignore=shutil.ignore_patterns(".git"))
        return dependency, revision, target
    finally:
        safe_rmtree(private, target.parent)
