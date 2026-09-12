from unittest.mock import Mock

import pytest

from apmx.contracts.frontend import admit_caller_policy
from apmx.contracts.models import ContractError, ContractLimits, Outcome
from apmx.contracts.process import local_git


@pytest.mark.parametrize("policy", ["", "null", "false", "{source: org:acme}", "{sha256: bad}"])
def test_any_governed_caller_refuses_offline(tmp_path, monkeypatch, policy):
    (tmp_path / "apm.yml").write_text(
        "name: governed\nversion: 1.0.0\npolicy: " + policy + "\n"
    )
    transport = Mock(side_effect=AssertionError("Policy admission cannot fetch"))
    monkeypatch.setattr("requests.Session.request", transport)
    with pytest.raises(ContractError) as error:
        admit_caller_policy(tmp_path, limits=ContractLimits())
    assert error.value.outcome == Outcome.UNPROVEN
    transport.assert_not_called()
    assert not (tmp_path / ".apm").exists()


def test_remote_governance_is_unresolved_not_silently_ignored(tmp_path):
    local_git(tmp_path, "init", "--quiet")
    local_git(tmp_path, "remote", "add", "origin", "https://example.invalid/org/repo")
    with pytest.raises(ContractError, match="Remote governance") as error:
        admit_caller_policy(tmp_path, limits=ContractLimits())
    assert error.value.outcome == Outcome.UNPROVEN


def test_explicit_policy_disabling_does_not_bypass_admission(tmp_path, monkeypatch):
    monkeypatch.setenv("APM_POLICY_DISABLE", "1")
    with pytest.raises(ContractError, match="disabled"):
        admit_caller_policy(tmp_path, limits=ContractLimits())


def test_script_disable_still_refuses_checks(tmp_path, monkeypatch):
    monkeypatch.setenv("APM_NO_SCRIPTS", "1")
    with pytest.raises(ContractError) as error:
        admit_caller_policy(tmp_path, limits=ContractLimits())
    assert error.value.code == "scripts_disabled"
