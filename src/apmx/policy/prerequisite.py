"""Fail-closed, read-only no-policy admission from APM's contract profile."""

import os
from pathlib import Path

from ..contracts.models import ContractError, Outcome
from ..contracts.process import local_git


def require_no_policy(root: Path, manifest: dict) -> None:
    def refuse(reason: str) -> None:
        raise ContractError(
            "Native contracts require positively established no-policy governance. "
            + reason + " Configured, disabled and unresolved governance are unsupported.",
            code="policy_unavailable", outcome=Outcome.UNPROVEN,
        )

    if os.environ.get("APM_POLICY_DISABLE") == "1":
        refuse("Policy discovery is disabled.")
    if "policy" in manifest:
        refuse("The caller declares policy configuration.")
    has_git = any(
        (parent / ".git").exists() or (parent / ".git").is_symlink()
        or ((parent / "HEAD").exists() and
            ((parent / "objects").exists() or (parent / "config").exists()))
        for parent in (root, *root.parents)
    )
    if not has_git:
        return
    try:
        remotes = local_git(root, "remote", maximum_bytes=256 * 1024)
    except ContractError:
        refuse("Cannot establish Git remote configuration offline.")
    if remotes.strip():
        refuse("Remote governance cannot be established offline.")
