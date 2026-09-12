from dataclasses import replace
from unittest.mock import Mock
from urllib.parse import urlsplit

import pytest

from apmx.contracts.models import ContractError, ProcessObservation
from apmx.contracts.process import local_git, supervise_process
from apmx.core.auth import AuthContext, AuthResolver
from apmx.install.git_source import download_git
from apmx.models.dependency.reference import DependencyReference


@pytest.fixture(autouse=True)
def no_user_git_config(tmp_path, monkeypatch):
    config = tmp_path / "global.gitconfig"
    config.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def git_package(tmp_path):
    repository = tmp_path / "git fixture"
    repository.mkdir()
    local_git(repository, "init", "--quiet")
    (repository / "apm.yml").write_text("name: fixture\nversion: 1.0.0\n")
    (repository / "job.contract.md").write_text(
        "---\nproduces: out\nverify: {ok: 'true'}\n---\nWrite.\n"
    )
    local_git(repository, "add", ".")
    local_git(repository, "commit", "--quiet", "-m", "fixture")
    local_git(repository, "tag", "v1")
    local_git(repository, "tag", "1.0.0")
    local_git(repository, "tag", "-a", "annotated", "-m", "Annotated fixture revision")
    expected = local_git(repository, "rev-parse", "HEAD").decode().strip()
    return repository, expected


@pytest.mark.parametrize("reference", [
    "owner/repo#v1", "https://github.com/owner/repo#v1", "owner/repo#1.0.0",
    "owner/repo#annotated",
])
def test_remote_acquisition_uses_auth_owner_and_exact_git_bytes(
    tmp_path, monkeypatch, reference, git_package,
):
    repository, expected = git_package
    resolver = AuthResolver()
    resolver.resolve = Mock(side_effect=AssertionError("Public acquisition must be anonymous"))
    resolver.resolve_for_remote = Mock(side_effect=AssertionError("No eager credential lookup"))
    monkeypatch.setattr("apmx.install.git_source.AuthResolver", lambda: resolver)
    seen = []

    def transport(request, **kwargs):
        seen.append(request)
        argv = list(request.argv)
        # Substitute only the remote endpoint with a local protocol fixture.
        # Everything else, including git/process/checkouts/copying, remains real.
        if "fetch" in argv:
            argv[argv.index("protocol.file.allow=never")] = "protocol.file.allow=always"
            argv[argv.index("https://github.com/owner/repo")] = repository.as_uri()
        return supervise_process(replace(request, argv=tuple(argv)), **kwargs)

    monkeypatch.setattr("apmx.install.git_source.supervise_process", transport)
    dep = DependencyReference.parse(reference)
    target = tmp_path / "downloaded package"
    result = download_git(dep, target)
    assert result == (dep, expected, target)
    assert (target / "job.contract.md").read_bytes() == (repository / "job.contract.md").read_bytes()
    assert not (target / ".git").exists()
    assert not list(tmp_path.glob(".apmx-git-*"))
    resolver.resolve_for_remote.assert_not_called()
    resolver.resolve.assert_not_called()
    assert all(request.env["GIT_TERMINAL_PROMPT"] == "0" for request in seen)
    assert all("--depth=1" in request.argv for request in seen if "fetch" in request.argv)


@pytest.mark.parametrize("reference", [
    "http://github.com/owner/repo#v1",
    "owner/repo#^1.0.0",
])
def test_unsupported_transport_refuses_before_credentials(tmp_path, monkeypatch, reference):
    resolver = Mock(side_effect=AssertionError("Credential lookup must not occur"))
    monkeypatch.setattr("apmx.install.git_source.AuthResolver", resolver)
    with pytest.raises(ContractError, match="Acquisition requires"):
        download_git(DependencyReference.parse(reference), tmp_path / "package")
    resolver.assert_not_called()
    assert not (tmp_path / "package").exists()
    assert not list(tmp_path.glob(".apmx-git-*"))


def test_private_https_retries_only_after_auth_failure(tmp_path, monkeypatch, git_package):
    repository, expected = git_package
    resolver = AuthResolver()
    resolver.resolve = Mock(return_value=AuthContext(
        "fixture-token", "gh-auth-token", "unknown", resolver.classify_host("github.com"), {},
    ))
    monkeypatch.setattr("apmx.install.git_source.AuthResolver", lambda: resolver)
    attempts = []

    def transport(request, **kwargs):
        attempts.append(request)
        if len(attempts) == 1:
            resolver.resolve.assert_not_called()
            kwargs["on_bytes"]("stderr", b"fatal: Authentication failed")
            return ProcessObservation(128)
        argv = list(request.argv)
        argv[argv.index("protocol.file.allow=never")] = "protocol.file.allow=always"
        argv[argv.index("https://github.com/owner/repo")] = repository.as_uri()
        return supervise_process(replace(request, argv=tuple(argv)), **kwargs)

    monkeypatch.setattr("apmx.install.git_source.supervise_process", transport)
    result = download_git(DependencyReference.parse("owner/repo#v1"), tmp_path / "package")
    assert result[1] == expected
    assert len(attempts) == 2
    resolver.resolve.assert_called_once_with(
        "github.com", "owner", port=None, host_type=None, path="owner/repo",
    )
    assert all("fixture-token" not in str(request.argv) for request in attempts)
    assert not list(tmp_path.glob(".apmx-git-*"))


@pytest.mark.parametrize("diagnostic", [
    b"SSL certificate problem: certificate verify failed",
    b"Connection timed out",
    b"HTTP 403: API rate limit exceeded",
])
def test_non_auth_failure_never_resolves_credentials(tmp_path, monkeypatch, diagnostic):
    resolver = AuthResolver()
    resolver.resolve = Mock(side_effect=AssertionError("No credential retry permitted"))
    monkeypatch.setattr("apmx.install.git_source.AuthResolver", lambda: resolver)

    def transport(request, **kwargs):
        kwargs["on_bytes"]("stderr", diagnostic)
        return ProcessObservation(128)

    supervised = Mock(side_effect=transport)
    monkeypatch.setattr("apmx.install.git_source.supervise_process", supervised)
    with pytest.raises(ContractError) as error:
        download_git(DependencyReference.parse("owner/repo#v1"), tmp_path / "package")
    assert error.value.code == "source_acquisition"
    assert diagnostic.decode() not in str(error.value)
    resolver.resolve.assert_not_called()
    assert supervised.call_count == 1


def test_rewrite_downgrade_refuses_before_credentials_or_network(tmp_path, monkeypatch):
    (tmp_path / "global.gitconfig").write_text(
        '[url "http://github.com/"]\n    insteadOf = https://github.com/\n'
    )
    resolver = Mock(side_effect=AssertionError("No credential owner needed"))
    # Construction itself is inert; neither resolving nor spawning may occur.
    owner = AuthResolver()
    owner.resolve = resolver
    monkeypatch.setattr("apmx.install.git_source.AuthResolver", lambda: owner)
    network = Mock(side_effect=AssertionError("Unsafe transport must not run"))
    monkeypatch.setattr("apmx.install.git_source.supervise_process", network)
    with pytest.raises(ContractError) as error:
        download_git(DependencyReference.parse("owner/repo#v1"), tmp_path / "package")
    assert error.value.code == "source_transport"
    resolver.assert_not_called()
    network.assert_not_called()


def test_explicit_ssh_preserves_user_port_and_does_not_resolve_http_token(tmp_path, monkeypatch):
    resolver = AuthResolver()
    resolver._resolve_token = Mock(side_effect=AssertionError("SSH must use native credentials"))
    monkeypatch.setattr("apmx.install.git_source.AuthResolver", lambda: resolver)
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-http-token")
    network = Mock(return_value=ProcessObservation(128))
    monkeypatch.setattr("apmx.install.git_source.supervise_process", network)
    with pytest.raises(ContractError):
        download_git(
            DependencyReference.parse("ssh://alice@git.example.test:2222/org/repo#v1"),
            tmp_path / "package",
        )
    request = network.call_args.args[0]
    url = urlsplit(request.argv[-2])
    assert (url.scheme, url.username, url.hostname, url.port) == (
        "ssh", "alice", "git.example.test", 2222,
    )
    assert "fixture-http-token" not in request.env.values()
    resolver._resolve_token.assert_not_called()


def test_nondefault_https_port_and_repo_path_reach_auth_owner(tmp_path, monkeypatch):
    resolver = AuthResolver()
    resolver.resolve = Mock(return_value=AuthContext(
        None, "none", "unknown", resolver.classify_host("git.example.test", port=8443), {},
    ))
    monkeypatch.setattr("apmx.install.git_source.AuthResolver", lambda: resolver)
    monkeypatch.setattr(
        "apmx.install.git_source.supervise_process", Mock(return_value=ProcessObservation(128)),
    )
    with pytest.raises(ContractError):
        download_git(
            DependencyReference.parse("https://git.example.test:8443/org/repo#v1"),
            tmp_path / "package",
        )
    resolver.resolve.assert_called_once_with(
        "git.example.test", "org", port=8443, host_type=None,
        path="org/repo", remote_url="https://git.example.test:8443/org/repo",
    )


@pytest.mark.parametrize("host", ["github.com", "acme.ghe.com"])
def test_cross_origin_rewrite_cannot_receive_resolved_credentials(tmp_path, monkeypatch, host):
    (tmp_path / "global.gitconfig").write_text(
        f'[url "https://{host}:9443/"]\n    insteadOf = https://{host}/\n'
    )
    resolver = AuthResolver()
    resolver.resolve = Mock(return_value=AuthContext(
        "fixture-token", "GITHUB_TOKEN", "unknown", resolver.classify_host(host), {},
    ))
    resolver._token_manager.resolve_credential_from_gh_cli = Mock(
        side_effect=AssertionError("A rewrite refusal cannot retry credentials")
    )
    monkeypatch.setattr("apmx.install.git_source.AuthResolver", lambda: resolver)

    def transport(request, **kwargs):
        kwargs["on_bytes"]("stderr", b"Authentication failed")
        return ProcessObservation(128)

    network = Mock(side_effect=transport)
    monkeypatch.setattr("apmx.install.git_source.supervise_process", network)
    with pytest.raises(ContractError) as error:
        download_git(DependencyReference.parse(f"https://{host}/owner/repo#v1"), tmp_path / "package")
    assert error.value.code == "source_transport"
    assert network.call_count == (1 if host == "github.com" else 0)
    assert resolver.resolve.call_count == 1


def test_stderr_counts_toward_total_acquisition_output_bound(tmp_path, monkeypatch):
    resolver = AuthResolver()
    resolver.resolve = Mock(side_effect=AssertionError("Output limit is not an auth failure"))
    monkeypatch.setattr("apmx.install.git_source.AuthResolver", lambda: resolver)

    def transport(request, **kwargs):
        kwargs["on_bytes"]("stdout", b"a" * (256 * 1024))
        kwargs["on_bytes"]("stderr", b"b" * (256 * 1024 + 1))
        raise AssertionError("The combined bound should already have stopped acquisition")

    monkeypatch.setattr("apmx.install.git_source.supervise_process", transport)
    with pytest.raises(ContractError) as error:
        download_git(DependencyReference.parse("owner/repo#v1"), tmp_path / "package")
    assert error.value.code == "source_limit"
    resolver.resolve.assert_not_called()
