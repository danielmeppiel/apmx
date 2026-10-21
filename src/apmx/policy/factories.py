"""Before any model call: may this harness and model work in a factory?

``apmx FACTORY`` discovers ``apm-policy.yml`` the way ``apmx audit`` does
(``--policy``, else the repository's file, else the org policy from the Git
remote) and applies its ``factories.harnesses`` and ``factories.models``. A
refusal reuses the policy refusal path: ``policy_blocked`` (or
``policy_unavailable`` for a policy that cannot be loaded), outcome UNPROVEN
(exit 21). The receipt gate (``apmx audit``) checks the same rules again
against what each stage recorded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..audit import policy as policies
from ..contracts.models import ContractError, ContractLimits, Outcome


@dataclass(frozen=True)
class Preflight:
    """The policy verdict for one invocation; ``verdict`` is None when nothing applies."""

    verdict: policies.Verdict | None = None
    source: str | None = None

    def refusal(self) -> ContractError | None:
        if self.verdict is None or self.verdict.status != "fail":
            return None
        return ContractError(
            f"Factory policy refuses this run: {self.verdict.detail} (policy: {self.source}). "
            "Nothing ran and no model was called.",
            code="policy_blocked",
            outcome=Outcome.UNPROVEN,
        )


def probe_version(harness: str, cwd: Path, limits: ContractLimits | None = None) -> str | None:
    """``<harness> --version`` at run start; None when it cannot be observed (never fatal)."""
    from ..contracts import process
    from ..contracts.models import ProcessRequest
    from ..runtime.registry import get_runtime_descriptor
    from ..runtime.utils import find_runtime_binary

    if harness == policies.REPLAY:
        return None
    try:
        binary = find_runtime_binary(get_runtime_descriptor(harness).binary)
    except ValueError:
        return None
    if binary is None:
        return None
    output = bytearray()

    def receive(stream: str, chunk: bytes) -> None:
        if stream == "stdout" and len(output) + len(chunk) <= 4096:
            output.extend(chunk)

    try:
        observation = process.supervise_process(
            ProcessRequest((binary, "--version"), cwd, 15, env=dict(os.environ)),
            on_bytes=receive,
            limits=limits or ContractLimits(),
        )
    except (ContractError, OSError, ValueError):
        return None
    if observation.returncode != 0 or observation.stop_reason or observation.error:
        return None
    return policies.observed_version(output.decode("utf-8", errors="replace"))


def resolve(root: Path, override: str | None) -> policies.Resolution:
    """Discover like ``apmx audit``; a directory ``--policy`` is discovered from there."""
    if override is not None:
        candidate = Path(override).expanduser()
        if candidate.is_dir():
            return policies.resolve(candidate.resolve())
    return policies.resolve(root, override=override)


def check(
    root: Path,
    *,
    harness: str,
    model: str | None,
    version: str | None,
    planning: bool,
    override: str | None = None,
) -> Preflight:
    """Resolve the policy and judge the run; raise only for an unloadable policy."""
    if harness == policies.REPLAY:
        return Preflight()
    resolution = resolve(root, override)
    try:
        if resolution.status == "unloadable":
            raise ContractError(
                f"Factory policy {resolution.detail}. A policy that cannot be loaded never "
                "allows a run.",
                code="policy_unavailable",
                outcome=Outcome.UNPROVEN,
            )
        policy = resolution.policy if resolution.applies else None
        if policy is None or not policy.runs:
            return Preflight(source=policy.source if policy else None)
        verdict = policies.preflight(policy, harness, model, version, version_probed=not planning)
        return Preflight(verdict, policy.source)
    finally:
        resolution.cleanup()
