"""Strict-only projection of the canonical transport selector.

The standalone contract profile does not enable cross-protocol retries.
Git's retained network boundary independently validates the effective URL
again with the actual credential environment before any network operation.
"""

from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit

from ..models.dependency.reference import DependencyReference
from ..utils.git_env import (
    configured_git_url_policy,
    git_url_has_authorization,
    resolve_git_url_rewrite,
    validate_resolved_git_url_rewrite,
)


class ProtocolPreference(Enum):
    NONE = "none"
    SSH = "ssh"
    HTTPS = "https"

    @classmethod
    def from_str(cls, value: str | None) -> "ProtocolPreference":
        if not value:
            return cls.NONE
        value = value.strip().lower()
        if value == "ssh":
            return cls.SSH
        if value in ("https", "http"):
            return cls.HTTPS
        return cls.NONE


def initial_transport_scheme(
    dep_ref: DependencyReference | None,
    cli_pref: ProtocolPreference = ProtocolPreference.NONE,
) -> str:
    explicit = (getattr(dep_ref, "explicit_scheme", None) or "").lower()
    if explicit:
        return explicit
    return "ssh" if cli_pref == ProtocolPreference.SSH else "https"


@dataclass(frozen=True)
class TransportAttempt:
    scheme: str
    requested_url: str
    effective_url: str


class TransportSelector:
    def select(
        self,
        dep_ref: DependencyReference,
        cli_pref: ProtocolPreference,
        *,
        candidate_url: str,
    ) -> TransportAttempt:
        rewrites, headers = configured_git_url_policy()
        candidate = candidate_url
        effective = resolve_git_url_rewrite(candidate, rewrites)
        if (
            candidate.endswith(".git")
            and effective
            and effective.endswith(".git.git")
            and not any(prefix == candidate for _, prefix in rewrites)
        ):
            unsuffixed = candidate.removesuffix(".git")
            rewritten = resolve_git_url_rewrite(unsuffixed, rewrites)
            if rewritten is not None and effective == f"{rewritten}.git":
                candidate, effective = unsuffixed, rewritten
        if effective is None:
            return TransportAttempt(
                initial_transport_scheme(dep_ref, cli_pref), candidate, candidate
            )
        validate_resolved_git_url_rewrite(
            candidate,
            effective,
            has_authorization=git_url_has_authorization(effective, headers),
        )
        scheme = urlsplit(effective).scheme.lower() or (
            "ssh" if "@" in effective.split(":", 1)[0] else "file"
        )
        return TransportAttempt(scheme, candidate, effective)
