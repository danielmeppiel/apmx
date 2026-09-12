from dataclasses import replace
from unittest.mock import Mock

import pytest

from apmx.contracts.models import ContractError
from apmx.contracts.process import local_git, supervise_process
from apmx.install.git_source import download_git
from apmx.models.dependency.reference import DependencyReference
from apmx.utils.subprocess_env import external_process_env


@pytest.mark.parametrize("reference", [
    "owner/repo#v1", "https://github.com/owner/repo#v1", "owner/repo#1.0.0",
])
def test_remote_acquisition_uses_auth_owner_and_exact_git_bytes(tmp_path, monkeypatch, reference):
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
    expected = local_git(repository, "rev-parse", "HEAD").decode().strip()
    resolver = Mock()
    resolver.git_env_for_remote.return_value = external_process_env()
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
    resolver.resolve_for_remote.assert_called_once_with(
        "github.com", "https://github.com/owner/repo", "owner", port=None, host_type=None,
    )
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
    assert not list(tmp_path.iterdir())
