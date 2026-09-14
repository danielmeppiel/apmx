"""Fail-closed, read-only no-policy admission from APM's contract profile."""

import os
from pathlib import Path

from ..contracts.models import ContractError, Outcome
from ..contracts.process import local_git
from .project_config import ProjectPolicyConfigError, parse_project_policy_hash_pin


def require_no_policy(root: Path, manifest: dict) -> None:
    def refuse(reason: str, *, code: str = "policy_unavailable") -> None:
        raise ContractError(
            "Native contracts require positively established no-policy governance. "
            + reason
            + " Configured, disabled and unresolved governance are unsupported.",
            code=code,
            outcome=Outcome.UNPROVEN,
        )

    if os.environ.get("APM_POLICY_DISABLE") == "1":
        refuse("Policy discovery is disabled.")
    try:
        pin = parse_project_policy_hash_pin(manifest.get("policy"))
    except ProjectPolicyConfigError:
        refuse("The caller's policy hash configuration is malformed.", code="policy_blocked")
    if pin is not None:
        refuse("The caller's policy hash cannot be verified offline.", code="policy_blocked")
    if "policy" in manifest:
        policy = manifest["policy"]
        code = (
            "policy_blocked"
            if isinstance(policy, dict) and policy.get("fetch_failure_default") == "block"
            else "policy_unavailable"
        )
        refuse("The caller declares policy configuration.", code=code)
    has_git = any(
        (parent / ".git").exists()
        or (parent / ".git").is_symlink()
        or (
            (parent / "HEAD").exists()
            and ((parent / "objects").exists() or (parent / "config").exists())
        )
        for parent in (root, *root.parents)
    )
    if not has_git:
        return
    try:
        remotes = local_git(root, "remote", maximum_bytes=256 * 1024, timeout_seconds=5)
    except ContractError:
        refuse("Cannot establish Git remote configuration offline.")
    if remotes.strip():
        refuse("Remote governance cannot be established offline.")
