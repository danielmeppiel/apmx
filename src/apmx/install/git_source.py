"""Bounded direct Git acquisition through the single credential resolver."""

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from ..contracts.models import ContractError, ContractLimits, ProcessObservation, ProcessRequest
from ..contracts.process import local_git, supervise_process
from ..core.auth import AuthResolver
from ..core.tls_trust import build_child_tls_env
from ..deps.transport_selection import (
    ProtocolPreference, TransportSelector, initial_transport_scheme,
)
from ..utils.git_env import (
    GitUrlRewriteError, GitUrlRewriteProbeError,
    get_git_executable, git_network_env, git_subprocess_env,
)
from ..models.dependency.host_virtual import dependency_repository_owner
from ..models.dependency.reference import DependencyReference
from ..marketplace.semver import parse_semver
from ..utils.github_host import (
    build_ado_ssh_url, build_ssh_url, default_host,
    is_full_commit_sha, is_ado_auth_failure_signal,
)
from ..utils.path_security import ensure_path_within, has_symlink_component, safe_rmtree


@dataclass(frozen=True)
class _FetchResult:
    observation: ProcessObservation
    auth_failure: bool = False
    ado_auth_failure: bool = False
    failure: ContractError | None = None


class _AuthenticationFailure(Exception):
    """Only an owner-classified signal, never private transport diagnostics."""


def _source_error() -> ContractError:
    return ContractError(
        "Git package acquisition failed. Check the explicit repository/revision and Git "
        "authentication. Private transport diagnostics were not retained.",
        code="source_acquisition",
    )


def download_git(
    dependency: DependencyReference, target: Path,
) -> tuple[DependencyReference, str, Path]:
    from .contract_source_validation import bounded_tree, validate_reference

    validate_reference(dependency)
    is_range = dependency.ref_kind == "semver" and parse_semver(dependency.reference) is None
    if dependency.is_insecure or dependency.artifactory_prefix or is_range:
        raise ContractError(
            "Acquisition requires an HTTPS/SSH Git repository and a literal revision, not HTTP, "
            "a registry proxy or a version range.", code="unsupported_source",
        )
    preference = ProtocolPreference.from_str(os.environ.get("APM_GIT_PROTOCOL"))
    url = dependency.to_clone_url()
    if initial_transport_scheme(dependency, preference) == "ssh":
        if dependency.is_azure_devops():
            url = build_ado_ssh_url(
                dependency.ado_organization, dependency.ado_project, dependency.ado_repo,
                host="ssh.dev.azure.com" if dependency.host == "dev.azure.com" else dependency.host,
            )
        else:
            url = build_ssh_url(
                dependency.host or default_host(), dependency.repo_url,
                port=dependency.port, user=dependency.ssh_user or "git",
            )
    git = get_git_executable()
    target.parent.mkdir(parents=True, exist_ok=True)
    private = target.parent / (".apmx-git-" + uuid4().hex)
    private.mkdir(mode=0o700)
    checkout = private / "checkout"
    deadline = time.monotonic() + 120
    resolver = AuthResolver()

    def remaining() -> float:
        value = deadline - time.monotonic()
        if value <= 0:
            raise ContractError("Git acquisition deadline expired.", code="source_deadline")
        return min(60, value)

    def fetch(env: dict[str, str]) -> _FetchResult:
        diagnostics = bytearray()
        received = 0

        def receive(stream: str, chunk: bytes) -> None:
            nonlocal received
            received += len(chunk)
            if received > 512 * 1024:
                raise ContractError("Git acquisition output exceeded its bound.", code="source_limit")
            if stream == "stderr":
                diagnostics.extend(chunk)
        try:
            network = build_child_tls_env(git_network_env(url, env, worktree=checkout))
            observed = supervise_process(
                ProcessRequest(
                    (git, "-c", f"core.hooksPath={os.devnull}", "-c", "core.autocrlf=false",
                     "-c", "core.fsmonitor=false", "-c", "protocol.file.allow=never",
                     "fetch", "--quiet", "--depth=1", "--no-tags", "--", url,
                     dependency.reference or "HEAD"),
                    checkout, remaining(), network,
                ),
                on_bytes=receive,
            )
            if observed.error or observed.stop_reason or not observed.cleanup_confirmed:
                return _FetchResult(observed)
            text = diagnostics.decode("utf-8", errors="replace")
            return _FetchResult(
                observed,
                observed.returncode != 0 and resolver.is_public_github_auth_failure(
                    RuntimeError(text)
                ),
                observed.returncode != 0 and is_ado_auth_failure_signal(text),
            )
        except ContractError as exc:
            return _FetchResult(ProcessObservation(None, error=exc.code), failure=exc)
        except (GitUrlRewriteError, GitUrlRewriteProbeError):
            failure = ContractError(
                "Git URL rewrite or credential-origin safety could not be established.",
                code="source_transport",
            )
            return _FetchResult(ProcessObservation(None, error=failure.code), failure=failure)
        finally:
            diagnostics.clear()

    try:
        attempt = TransportSelector().select(dependency, preference, candidate_url=url)
        if attempt.scheme not in {"https", "ssh"}:
            raise ContractError("Git rewrite selects an unsupported transport.", code="unsupported_source")
        url = attempt.requested_url
        checkout.mkdir(mode=0o700)
        empty_config = private / "empty.gitconfig"
        empty_config.touch(mode=0o600, exist_ok=False)
        base = git_subprocess_env()
        base.update(
            GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=str(empty_config),
        )
        local_git(checkout, "init", "--quiet", "--template=", timeout_seconds=remaining())
        host = dependency.host or default_host()
        owner = dependency_repository_owner(dependency)
        if attempt.scheme == "https" and resolver.uses_public_github_anonymous_first(
            host, port=dependency.port, host_type=dependency.host_type,
        ):
            def public_attempt(_token: str | None, env: dict[str, str]) -> _FetchResult:
                result = fetch(env)
                if result.auth_failure:
                    raise _AuthenticationFailure("Authentication failed.")
                return result

            try:
                result = resolver.try_with_fallback(
                    host, public_attempt, org=owner, port=dependency.port,
                    path=dependency.repo_url, host_type=dependency.host_type,
                    unauth_first=True, base_env=base,
                )
            except _AuthenticationFailure:
                raise _source_error() from None
        elif attempt.scheme == "ssh":
            env = resolver.build_native_git_credential_env(
                resolver.classify_host(host, port=dependency.port, host_type=dependency.host_type),
                attempt.effective_url, base_env=base,
            )
            result = fetch(env)
        else:
            context = resolver.resolve(
                host, owner, port=dependency.port, host_type=dependency.host_type,
                path=dependency.repo_url, remote_url=attempt.effective_url,
            )
            env = resolver.git_env_for_remote(context, url, base_env=base)
            if (
                dependency.is_azure_devops()
                and urlsplit(attempt.effective_url).scheme == "https"
            ):
                fallback = resolver.execute_with_bearer_fallback(
                    dependency,
                    lambda: fetch(env),
                    lambda bearer: fetch(resolver.build_ado_bearer_git_env(
                        context, bearer, url, base_env=base,
                    )),
                    lambda outcome: outcome.ado_auth_failure,
                ).outcome
                if not isinstance(fallback, _FetchResult):
                    raise ContractError("Git auth returned an invalid result.", code="source_acquisition")
                result = fallback
            else:
                result = fetch(env)
        if result.failure is not None:
            raise result.failure from None
        observed = result.observation
        if (
            observed.error or observed.stop_reason or observed.returncode != 0
            or not observed.cleanup_confirmed
        ):
            raise _source_error()
        revision = local_git(
            checkout, "rev-parse", "--verify", "FETCH_HEAD^{commit}", timeout_seconds=remaining(),
        ).decode("ascii").strip()
        if not is_full_commit_sha(revision):
            raise ContractError("Git did not resolve a full source commit.", code="unresolved_source")
        if is_full_commit_sha(dependency.reference or "") and revision.lower() != dependency.reference.lower():
            raise ContractError("Git resolved a different source commit.", code="source_mismatch")
        local_git(checkout, "checkout", "--quiet", "--detach", revision, timeout_seconds=remaining())
        selected = ensure_path_within(checkout / (dependency.virtual_path or ""), checkout)
        if has_symlink_component(checkout, selected):
            raise ContractError("Selected package contains a symlink.", code="source_escape")
        bounded_tree(selected, ContractLimits())
        shutil.copytree(selected, target, ignore=shutil.ignore_patterns(".git"))
        return dependency, revision, target
    except (GitUrlRewriteError, GitUrlRewriteProbeError):
        raise ContractError(
            "Git URL rewrite or credential-origin safety could not be established.",
            code="source_transport",
        ) from None
    finally:
        safe_rmtree(private, target.parent)
