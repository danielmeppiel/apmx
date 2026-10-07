"""Receiver-controlled candidate check for the enterprise-receiver demo.

This is the *unprivileged*, PR-triggered half of the security story in
``docs/security-story.md``. It runs read-only against the PR head, using only
trust anchors read from the PR's trusted base ref (never from the PR's own,
possibly-tampered tree):

  * the independent, pinned Evidence Package verifier (``scripts/verify_evidence``,
    imported and reused, not duplicated),
  * the receiver's factory allowlist, candidate binding map and signer policy
    under ``examples/enterprise-receiver/policy/`` as they exist at the PR
    *base* commit,
  * ``gh attestation verify`` (stock GitHub CLI) to authenticate that the
    evidence was signed by the pinned, privileged attestation workflow.

No step here executes anything from the PR head other than reading bytes
(candidate source file content, evidence package files) that are then hashed
and compared. The privileged signing boundary (``receiver-attest.yml``) is a
separate workflow that never runs against an untrusted PR head.

A signature produced by that boundary only authenticates "this CI assessment,
of this exact definition and binding, was produced and approved by the
pinned receiver workflow" -- it does not retroactively authenticate whatever
an agent executed locally, and it is not a SLSA security level claim.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_RECEIVER_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _RECEIVER_ROOT.parent.parent
_POLICY_DIR_RELATIVE = "examples/enterprise-receiver/policy"

sys.path.insert(0, str(_REPO_ROOT / "scripts"))
import verify_evidence


class ReceiverFailure(Exception):
    """Raised with the specific receiver policy control that rejected the PR."""

    def __init__(self, policy: str, message: str) -> None:
        super().__init__(f"[{policy}] {message}")
        self.policy = policy
        self.detail = message


@dataclass(frozen=True)
class CaseResult:
    case: str
    definition_sha256: str
    checks: tuple[str, ...]


def _run_git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(_REPO_ROOT), *args],
        capture_output=True,
        check=False,
    )


def trusted_bytes(ref: str, relative_path: str) -> bytes:
    """Read a file's bytes from a trusted ref (the PR base), never the PR's own tree."""
    completed = _run_git(["show", f"{ref}:{relative_path}"])
    if completed.returncode != 0:
        raise ReceiverFailure(
            "policy-missing",
            f"Trusted ref {ref} has no {relative_path}: {completed.stderr.decode()}",
        )
    return completed.stdout


def trusted_json(ref: str, relative_path: str) -> dict:
    return json.loads(trusted_bytes(ref, relative_path))


def git_blob_sha256(ref: str, relative_path: str) -> str:
    completed = _run_git(["show", f"{ref}:{relative_path}"])
    if completed.returncode != 0:
        raise ReceiverFailure(
            "binding", f"Candidate binding references a path missing at {ref}: {relative_path}"
        )
    return hashlib.sha256(completed.stdout).hexdigest()


def git_changed_paths(base_sha: str, head_sha: str) -> set[str]:
    completed = _run_git(["diff", "--name-only", f"{base_sha}..{head_sha}"])
    if completed.returncode != 0:
        raise ReceiverFailure("binding", f"Unable to diff {base_sha}..{head_sha}")
    return {line.strip() for line in completed.stdout.decode().splitlines() if line.strip()}


def _evidence_index(evidence_dir: Path) -> dict:
    index_path = evidence_dir / "index.json"
    if not index_path.is_file():
        raise ReceiverFailure("missing-evidence", f"No index.json under {evidence_dir}")
    return json.loads(index_path.read_bytes())


def _run_id(index: dict) -> str:
    attempts = index.get("attempts") or []
    selected = [a["runId"] for a in attempts if a.get("selected")]
    if len(selected) != 1:
        raise ReceiverFailure("binding", "Evidence does not name exactly one selected attempt.")
    return selected[0]


def check_integrity(evidence_dir: Path, schemas_dir: Path) -> dict:
    if not evidence_dir.is_dir():
        raise ReceiverFailure("missing-evidence", f"No evidence directory at {evidence_dir}")
    try:
        return verify_evidence.verify(evidence_dir, schemas_dir)
    except ValueError as error:
        raise ReceiverFailure("integrity", str(error)) from error


def check_factory_policy(definition_sha256: str, base_sha: str) -> None:
    policy = trusted_json(base_sha, f"{_POLICY_DIR_RELATIVE}/approved-factories.json")
    allowed = set(policy.get("definitionSha256", []))
    if definition_sha256 not in allowed:
        raise ReceiverFailure(
            "factory",
            f"Evidence definition {definition_sha256} is not on the receiver's "
            "approved-factories allowlist.",
        )


def check_binding(
    evidence_dir: Path, index: dict, base_sha: str, head_sha: str, case_dir_relative: str
) -> None:
    binding_map = trusted_json(base_sha, f"{_POLICY_DIR_RELATIVE}/binding-map.json")
    run_id = _run_id(index)
    files = index["files"]
    accounted: set[str] = set()

    for evidence_relative, real_path in binding_map.get("baseline", {}).items():
        full_name = f"attempts/{run_id}/baseline/{evidence_relative}"
        expected = files.get(full_name)
        if expected is None:
            raise ReceiverFailure(
                "binding", f"Evidence has no recorded baseline material {full_name}."
            )
        accounted.add(full_name)
        actual = git_blob_sha256(base_sha, real_path)
        if actual != expected["sha256"]:
            raise ReceiverFailure(
                "binding",
                f"Baseline mismatch for {real_path} at {base_sha}: evidence recorded "
                f"{expected['sha256']}, the PR base tree has {actual}. The recorded "
                "evidence no longer matches the PR's actual base content.",
            )

    for evidence_relative, real_path in binding_map.get("candidate", {}).items():
        full_name = f"attempts/{run_id}/artifacts/{evidence_relative}"
        expected = files.get(full_name)
        if expected is None:
            raise ReceiverFailure(
                "binding", f"Evidence has no recorded candidate artifact {full_name}."
            )
        accounted.add(full_name)
        actual = git_blob_sha256(head_sha, real_path)
        if actual != expected["sha256"]:
            raise ReceiverFailure(
                "binding",
                f"Candidate mismatch for {real_path} at {head_sha}: evidence recorded "
                f"{expected['sha256']}, the PR head tree has {actual}. The patch was "
                "changed after the evidence was captured (stale/tampered patch).",
            )

    del accounted  # every mapped entry was checked above; unmapped baseline/artifact
    # files are internal contract scaffolding (apm.yml, job.contract.md, check
    # resources) and are not part of the real-repo candidate being bound.

    allowed_prefixes = tuple(sorted(binding_map.get("candidate", {}).values()))
    changed = git_changed_paths(base_sha, head_sha)
    out_of_scope = {
        path
        for path in changed
        if not (
            path.startswith(case_dir_relative.rstrip("/") + "/")
            or any(path == prefix for prefix in allowed_prefixes)
        )
    }
    if out_of_scope:
        raise ReceiverFailure(
            "binding",
            f"PR changes files outside the bound candidate scope and its own case "
            f"directory: {sorted(out_of_scope)}",
        )


def check_capability_revocation(evidence_dir: Path, base_sha: str) -> None:
    bindings_path = evidence_dir / "capability-bindings.json"
    bindings = json.loads(bindings_path.read_bytes()) if bindings_path.is_file() else []
    if not bindings:
        return
    revoked = set(
        trusted_json(base_sha, f"{_POLICY_DIR_RELATIVE}/revoked-capabilities.json").get(
            "lockIdentity", []
        )
    )
    for binding in bindings:
        if binding.get("lockIdentity") in revoked:
            raise ReceiverFailure(
                "capability-revoked",
                f"Capability {binding.get('capability')} (lockIdentity="
                f"{binding.get('lockIdentity')}) has been revoked by receiver incident response.",
            )


def check_signer(evidence_dir: Path, base_sha: str) -> None:
    signers = trusted_json(base_sha, f"{_POLICY_DIR_RELATIVE}/signers.json")
    subject = evidence_dir / "index.json"
    args = [
        "gh",
        "attestation",
        "verify",
        str(subject),
        "--repo",
        signers["repo"],
        "--signer-workflow",
        signers["signerWorkflow"],
        "--predicate-type",
        signers["predicateType"],
        "--format",
        "json",
    ]
    completed = subprocess.run(args, capture_output=True, check=False)
    if completed.returncode != 0:
        raise ReceiverFailure(
            "signer",
            "gh attestation verify failed for the evidence index: "
            + completed.stderr.decode(errors="replace").strip(),
        )


def check_case(
    *,
    case: str,
    case_dir_relative: str,
    base_sha: str,
    head_sha: str,
    schemas_dir: Path,
    skip_signer: bool = False,
) -> CaseResult:
    evidence_dir = _REPO_ROOT / case_dir_relative / "evidence"
    summary = check_integrity(evidence_dir, schemas_dir)
    index = _evidence_index(evidence_dir)
    check_factory_policy(summary["definitionSha256"], base_sha)
    check_binding(evidence_dir, index, base_sha, head_sha, case_dir_relative)
    check_capability_revocation(evidence_dir, base_sha)
    checks = ["integrity", "factory", "binding", "capability-revocation"]
    if not skip_signer:
        check_signer(evidence_dir, base_sha)
        checks.append("signer")
    return CaseResult(
        case=case, definition_sha256=summary["definitionSha256"], checks=tuple(checks)
    )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True)
    parser.add_argument("--case-dir", required=True, help="Repo-relative case directory")
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--schemas", type=Path, required=True)
    parser.add_argument("--skip-signer", action="store_true")
    args = parser.parse_args()
    try:
        result = check_case(
            case=args.case,
            case_dir_relative=args.case_dir,
            base_sha=args.base_sha,
            head_sha=args.head_sha,
            schemas_dir=args.schemas,
            skip_signer=args.skip_signer,
        )
    except ReceiverFailure as failure:
        print(
            json.dumps({"status": "rejected", "policy": failure.policy, "detail": failure.detail})
        )
        return 1
    print(
        json.dumps(
            {
                "status": "accepted",
                "case": result.case,
                "definitionSha256": result.definition_sha256,
                "checks": list(result.checks),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
