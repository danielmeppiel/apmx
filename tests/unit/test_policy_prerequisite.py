from unittest.mock import Mock

import pytest

from apmx.contracts.frontend import admit_caller_policy
from apmx.contracts.models import ContractError, ContractLimits, Outcome
from apmx.contracts.process import local_git
from apmx.policy.prerequisite import require_no_policy


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


@pytest.mark.parametrize("policy,code", [
    (None, "policy_unavailable"),
    ({}, "policy_unavailable"),
    ({"fetch_failure_default": "block"}, "policy_blocked"),
    ({"hash": "sha256:" + "a" * 64}, "policy_blocked"),
    ({"hash": "invalid"}, "policy_blocked"),
    ({"hash_algorithm": "sha1"}, "policy_blocked"),
    (False, "policy_blocked"),
])
def test_policy_outcome_codes_precede_any_git_probe(tmp_path, monkeypatch, policy, code):
    git = Mock(side_effect=AssertionError("Configured policy must refuse before probing"))
    monkeypatch.setattr("apmx.policy.prerequisite.local_git", git)
    with pytest.raises(ContractError) as error:
        require_no_policy(tmp_path, {"policy": policy})
    assert error.value.code == code
    assert error.value.outcome == Outcome.UNPROVEN
    git.assert_not_called()


def test_policy_remote_probe_has_its_own_five_second_bound(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    git = Mock(return_value=b"")
    monkeypatch.setattr("apmx.policy.prerequisite.local_git", git)
    require_no_policy(tmp_path, {})
    assert git.call_args.kwargs["timeout_seconds"] == 5
    git.return_value = b"upstream\n"
    with pytest.raises(ContractError):
        require_no_policy(tmp_path, {})
    git.side_effect = ContractError("timeout", code="baseline_git_failed")
    with pytest.raises(ContractError):
        require_no_policy(tmp_path, {})
