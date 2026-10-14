"""Receiver policy over an audited apmx receipt: pure decisions, no git, no I/O.

``apmx audit`` (``src/apmx/audit/verifier.py``) decides whether a receipt is
internally consistent and content-bound: Integrity, Standards, Factory,
Checks and Outputs. It deliberately does not decide whether *this* receiver
trusts that factory or the capabilities it used. Those two decisions are
receiver policy and live here:

  * ``evaluate_factory_allowlist``: the receipt's audited definition digest
    must be on the approved factory list.
  * ``evaluate_capability_revocation``: no audited capability binding may
    match a revoked ``(purl, bodySha256)`` pair or a revoked ``lockIdentity``.

Both functions take only a parsed policy document and facts the audit has
already established, never raw receipt files, git refs or workflow context.
Where the policy comes from (the PR's trusted base ref) and which facts are
fed in stay in ``check.py``. This separation is deliberate: it is the shape
the deferred "slice 5" of the apmx CLI plan (``apmx audit --policy`` for
factory identity) could adopt without change.
"""

from __future__ import annotations

import re

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


class PolicyViolation(ValueError):
    """A receipt that the audit accepted but this receiver's policy rejects."""

    def __init__(self, control: str, message: str) -> None:
        super().__init__(message)
        self.control = control


def evaluate_factory_allowlist(policy: dict, definition_sha256: str) -> None:
    allowed = policy.get("definitionSha256", [])
    if not isinstance(allowed, list) or definition_sha256 not in allowed:
        raise PolicyViolation(
            "factory",
            f"Receipt definition {definition_sha256} is not on the receiver's "
            "approved-factories allowlist.",
        )


def revoked_capability_pairs(entries: object) -> set[tuple[str, str]]:
    """Validate every structured revocation selector; never drop a malformed one.

    A typo'd or missing digest in an intended deny rule must fail closed
    (reject the whole policy) rather than quietly narrowing the revocation
    set and turning an intended deny into an accidental allow. An empty list
    stays valid, so legacy ``lockIdentity``-only policies keep working.
    """
    if not isinstance(entries, list):
        raise PolicyViolation(
            "capability-revoked",
            f"revoked-capabilities.json 'capabilities' must be an array, got: {entries!r}",
        )
    pairs: set[tuple[str, str]] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise PolicyViolation(
                "capability-revoked",
                f"revoked-capabilities.json capabilities[{index}] is not an object: {entry!r}",
            )
        purl = entry.get("purl")
        body_sha256 = entry.get("bodySha256")
        if not isinstance(purl, str) or not purl:
            raise PolicyViolation(
                "capability-revoked",
                f"revoked-capabilities.json capabilities[{index}] has a missing or "
                f"non-string 'purl' selector field: {entry!r}",
            )
        # fullmatch, not match with "$": "$" also matches before a trailing "\n".
        if not isinstance(body_sha256, str) or not _SHA256_HEX.fullmatch(body_sha256):
            raise PolicyViolation(
                "capability-revoked",
                f"revoked-capabilities.json capabilities[{index}] has a missing, "
                "non-string, or malformed (not exactly 64 lowercase hex characters) "
                f"'bodySha256' selector field: {entry!r}",
            )
        pairs.add((purl, body_sha256))
    return pairs


def evaluate_capability_revocation(policy: dict, bindings: list[dict]) -> None:
    """Reject any audited capability binding that incident response revoked.

    ``capabilities`` holds exact ``(purl, bodySha256)`` pairs. They stay
    constant when the same capability content is reinstalled from another
    checkout path, so they are the portable selector. ``lockIdentity`` is a
    coarse legacy selector: for a local-path dependency it is the declared
    path, which differs between checkouts even for identical bytes.
    """
    revoked_pairs = revoked_capability_pairs(policy.get("capabilities", []))
    revoked_identities = set(policy.get("lockIdentity", []))
    for binding in bindings:
        lock_identity = binding.get("lockIdentity")
        purl = binding.get("purl")
        body_sha256 = binding.get("bodySha256")
        if lock_identity in revoked_identities:
            raise PolicyViolation(
                "capability-revoked",
                f"Capability {binding.get('capability')} (lockIdentity="
                f"{lock_identity}) has been revoked by receiver incident response.",
            )
        if purl and body_sha256 and (purl, body_sha256) in revoked_pairs:
            raise PolicyViolation(
                "capability-revoked",
                f"Capability {binding.get('capability')} (purl={purl}, "
                f"bodySha256={body_sha256}) has been revoked by receiver incident response.",
            )
