"""Canonical read-only policy hash-pin parser, without policy fetching."""

import re
from dataclasses import dataclass

ALLOWED_HASH_ALGORITHMS = ("sha256", "sha384", "sha512")
_DEFAULT_HASH_ALGORITHM = "sha256"
_HASH_HEX_LEN = {"sha256": 64, "sha384": 96, "sha512": 128}
_HEX_RE = re.compile(r"^[0-9a-f]+$")


class ProjectPolicyConfigError(ValueError):
    """The caller's policy hash configuration is structurally invalid."""


@dataclass(frozen=True)
class ProjectPolicyHashPin:
    algorithm: str
    digest: str

    @property
    def normalized(self) -> str:
        return f"{self.algorithm}:{self.digest}"


def _strip_algo_prefix(value: str, declared_algo: str) -> str:
    if ":" not in value:
        return value
    algo, _, rest = value.partition(":")
    if algo.lower() != declared_algo:
        raise ProjectPolicyConfigError(
            f"policy.hash prefix '{algo}:' does not match "
            f"hash_algorithm '{declared_algo}' in apm.yml"
        )
    return rest


def parse_project_policy_hash_pin(raw: dict | None) -> ProjectPolicyHashPin | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ProjectPolicyConfigError("policy: block in apm.yml must be a mapping")
    algo_raw = raw.get("hash_algorithm", _DEFAULT_HASH_ALGORITHM)
    if not isinstance(algo_raw, str):
        raise ProjectPolicyConfigError("policy.hash_algorithm in apm.yml must be a string")
    algo = algo_raw.strip().lower()
    if algo not in ALLOWED_HASH_ALGORITHMS:
        allowed = ", ".join(ALLOWED_HASH_ALGORITHMS)
        raise ProjectPolicyConfigError(
            f"policy.hash_algorithm '{algo_raw}' is not supported. Allowed: {allowed}"
        )
    hash_raw = raw.get("hash")
    if hash_raw is None:
        return None
    if not isinstance(hash_raw, str):
        raise ProjectPolicyConfigError(
            "policy.hash in apm.yml must be a string of the form 'sha256:<hex>' or '<hex>'"
        )
    candidate = _strip_algo_prefix(hash_raw.strip(), algo).lower()
    expected_len = _HASH_HEX_LEN[algo]
    if len(candidate) != expected_len or not _HEX_RE.match(candidate):
        raise ProjectPolicyConfigError(
            f"policy.hash in apm.yml is not a valid {algo} digest "
            f"(expected {expected_len} lowercase hex characters)"
        )
    return ProjectPolicyHashPin(algorithm=algo, digest=candidate)
