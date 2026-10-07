"""Receiver-controlled candidate check for the enterprise-receiver demo.

This is the *unprivileged*, PR-triggered half of the security story in
``docs/security-story.md``. It is meant to run as a GitHub Actions
``pull_request_target`` job whose own workflow definition and checkout are
fixed to the PR's trusted **base** ref -- never the version of this file (or
the workflow YAML) carried by the PR itself. That is the whole point: a
plain ``pull_request``-triggered workflow executes the workflow file and
control code *from the PR's own tree*, which lets any PR silently replace
``check.py``/its requirements with something that always exits 0 while
keeping the same required-check name. Running the control plane from the
base ref closes that hole; this module must never be executed by a workflow
whose own definition or dependencies are loaded from the PR head.

Everything here reads from two kinds of trusted/untrusted sources:

  * TRUSTED (the PR base ref only): the independent, pinned Evidence Package
    verifier (``scripts/verify_evidence``, imported and reused, not
    duplicated), and the receiver's factory allowlist, candidate binding map
    and signer policy under ``examples/enterprise-receiver/policy/``.
  * UNTRUSTED (the PR head): candidate source file bytes and the evidence
    package the PR adds. These are never executed, imported, or installed --
    only read as bytes via ``git show``/``git ls-tree`` into bounded,
    permission-stripped scratch copies, then hashed, diffed, or schema
    validated. The evidence directory in particular is materialized through
    ``materialized_case_evidence`` with explicit file-count/size bounds and
    no tar extraction, precisely because it is untrusted archive-shaped
    input.

``gh attestation verify`` (stock GitHub CLI) is used to authenticate that
evidence was signed by the pinned, privileged attestation workflow, and the
signed predicate's own claimed fields are independently compared against the
exact values the trusted policy expects -- a successful exit code alone is
not treated as sufficient. The privileged signing boundary
(``receiver-attest.yml``) is a separate workflow that never runs against an
untrusted PR head; see that workflow and ``assess_canonical`` below for what
it actually verifies before it signs.

A signature produced by that boundary only authenticates "this CI assessment
of this exact evidence package (structural integrity, factory policy,
capability revocation) was produced and approved by the pinned receiver
workflow" -- it does not retroactively authenticate whatever an agent
executed locally, it is not a SLSA security level claim, and it is not a
per-PR binding claim (binding is this module's own job, done separately,
per candidate, at verification time).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_RECEIVER_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _RECEIVER_ROOT.parent.parent
_POLICY_DIR_RELATIVE = "examples/enterprise-receiver/policy"

# Bounds applied when materializing an untrusted PR's evidence directory from
# git objects. This is deliberately conservative for a demo-scale package;
# raise it only if real evidence packages this receiver accepts legitimately
# exceed it, not to make a particular PR pass.
_MAX_EVIDENCE_FILES = 2000
_MAX_EVIDENCE_TOTAL_BYTES = 64 * 1024 * 1024

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


def _git_ls_tree_blobs(ref: str, path_prefix: str) -> list[tuple[str, str]]:
    """List (mode, path) for every blob at ``ref`` under ``path_prefix``.

    Non-blob entries (symlinks, gitlinks/submodules) are rejected outright --
    this is untrusted PR content and must never be followed or executed.
    """
    completed = _run_git(["ls-tree", "-r", "-z", ref, "--", path_prefix])
    if completed.returncode != 0:
        raise ReceiverFailure(
            "missing-evidence", f"Unable to list {path_prefix} at {ref}: {completed.stderr!r}"
        )
    entries: list[tuple[str, str]] = []
    for record in completed.stdout.decode().split("\0"):
        if not record:
            continue
        meta, _, path = record.partition("\t")
        mode, obj_type, _blob_sha = meta.split(" ", 2)
        if obj_type != "blob" or mode == "120000":
            raise ReceiverFailure(
                "missing-evidence",
                f"Evidence tree entry {path!r} at {ref} is not a plain file "
                f"(mode={mode}, type={obj_type}); refusing to materialize it.",
            )
        entries.append((mode, path))
    return entries


@contextmanager
def materialized_case_evidence(head_sha: str, case_dir_relative: str):
    """Safely copy a PR's own ``evidence/`` directory out of git objects.

    The PR head is untrusted and its evidence directory is archive-shaped
    input (many files, including nested JSON), so this never does a blind
    ``git archive | tar -x``: every path is listed explicitly via
    ``git ls-tree``, confined to this exact prefix, file count and total
    byte size are bounded, and every byte is written with plain file
    permissions (no symlinks, no executable bits) to a throwaway temporary
    directory that is removed afterwards.
    """
    evidence_prefix = f"{case_dir_relative.rstrip('/')}/evidence"
    blobs = _git_ls_tree_blobs(head_sha, evidence_prefix)
    if not blobs:
        raise ReceiverFailure(
            "missing-evidence",
            f"No evidence files found under {evidence_prefix} at {head_sha}.",
        )
    if len(blobs) > _MAX_EVIDENCE_FILES:
        raise ReceiverFailure(
            "missing-evidence",
            f"Evidence directory has {len(blobs)} files, exceeding the "
            f"bounded limit of {_MAX_EVIDENCE_FILES}.",
        )
    with tempfile.TemporaryDirectory(prefix="receiver-evidence-") as scratch:
        scratch_root = Path(scratch)
        total_bytes = 0
        for _mode, path in blobs:
            relative = PurePosixPath(path).relative_to(evidence_prefix)
            if ".." in relative.parts:
                raise ReceiverFailure("missing-evidence", f"Refusing path traversal: {path!r}")
            completed = _run_git(["show", f"{head_sha}:{path}"])
            if completed.returncode != 0:
                raise ReceiverFailure("missing-evidence", f"Unable to read {path} at {head_sha}")
            total_bytes += len(completed.stdout)
            if total_bytes > _MAX_EVIDENCE_TOTAL_BYTES:
                raise ReceiverFailure(
                    "missing-evidence",
                    f"Evidence directory exceeds the bounded size limit of "
                    f"{_MAX_EVIDENCE_TOTAL_BYTES} bytes.",
                )
            destination = scratch_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(completed.stdout)
            destination.chmod(0o644)
        yield scratch_root


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
    """Authenticate that this exact evidence index was signed by the pinned
    attestation workflow, binding both the signer's identity/ref and the
    content it actually claims to have assessed.

    A zero exit code from ``gh attestation verify`` only proves a valid
    signature from *some* run of the named workflow exists for this exact
    subject digest -- it does not, by itself, prove the signer evaluated the
    claims this receiver cares about. So the parsed ``--format json`` output
    is inspected: the subject digest is independently recomputed (never
    trusting gh's own report of it), the source ref is pinned via
    ``--source-ref`` so only runs triggered from the receiver's own trusted
    branch count, and every field in the policy's ``expectedPredicate`` is
    compared exactly against the signed predicate -- rejecting, for example,
    a validly-signed predicate that never actually claims policy acceptance.
    """
    signers = trusted_json(base_sha, f"{_POLICY_DIR_RELATIVE}/signers.json")
    subject = evidence_dir / "index.json"
    expected_digest = hashlib.sha256(subject.read_bytes()).hexdigest()
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
        "--source-ref",
        signers["sourceRef"],
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
    try:
        results = json.loads(completed.stdout.decode())
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ReceiverFailure(
            "signer", f"gh attestation verify returned unparseable output: {error}"
        ) from error
    expected_predicate = signers.get("expectedPredicate", {})
    for result in results:
        statement = result.get("verificationResult", {}).get("statement", {})
        if statement.get("predicateType") != signers["predicateType"]:
            continue
        subjects = statement.get("subject", [])
        digests = {s.get("digest", {}).get("sha256") for s in subjects}
        if expected_digest not in digests:
            continue
        predicate = statement.get("predicate", {})
        if all(predicate.get(key) == value for key, value in expected_predicate.items()):
            return
    raise ReceiverFailure(
        "signer",
        "No verified attestation matched the exact expected subject digest "
        f"({expected_digest}) and predicate {expected_predicate!r}. A signature "
        "exists but does not authenticate this evidence's actual content/claims.",
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
    with materialized_case_evidence(head_sha, case_dir_relative) as evidence_dir:
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


def assess_canonical(evidence_dir: Path, base_sha: str, schemas_dir: Path) -> dict:
    """The privileged, pre-signing assessment run only on the receiver's own
    trusted branch (never against an untrusted PR), reused unmodified by
    ``receiver-attest.yml`` so the signed predicate's "accepted" claim is
    actually backed by the same factory-allowlist and capability-revocation
    policy this module enforces for every candidate -- not merely a
    structural/schema check.

    Binding and signer checks are deliberately not run here: binding is
    specific to a PR candidate (there is none at sign time) and signer
    verification would be circular before the signature exists.
    """
    summary = check_integrity(evidence_dir, schemas_dir)
    check_factory_policy(summary["definitionSha256"], base_sha)
    check_capability_revocation(evidence_dir, base_sha)
    return summary


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)

    check_case_parser = subparsers.add_parser(
        "check-case", help="Unprivileged candidate check for one PR-introduced case."
    )
    check_case_parser.add_argument("--case", required=True)
    check_case_parser.add_argument("--case-dir", required=True, help="Repo-relative case directory")
    check_case_parser.add_argument("--base-sha", required=True)
    check_case_parser.add_argument("--head-sha", required=True)
    check_case_parser.add_argument("--schemas", type=Path, required=True)
    check_case_parser.add_argument("--skip-signer", action="store_true")

    assess_parser = subparsers.add_parser(
        "assess-canonical",
        help="Privileged pre-signing assessment of the canonical evidence package.",
    )
    assess_parser.add_argument("--evidence-dir", type=Path, required=True)
    assess_parser.add_argument("--base-sha", required=True)
    assess_parser.add_argument("--schemas", type=Path, required=True)

    args = parser.parse_args()

    if args.mode == "assess-canonical":
        try:
            summary = assess_canonical(args.evidence_dir, args.base_sha, args.schemas)
        except ReceiverFailure as failure:
            print(
                json.dumps(
                    {"status": "rejected", "policy": failure.policy, "detail": failure.detail}
                )
            )
            return 1
        print(json.dumps({"status": "accepted", "definitionSha256": summary["definitionSha256"]}))
        return 0

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
