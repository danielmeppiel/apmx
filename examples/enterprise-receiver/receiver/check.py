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
import os
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
# Per-blob cap, enforced from trusted git object metadata (git cat-file
# --batch-check) BEFORE any blob's content is read into memory. Without
# this, a single PR-supplied blob near the total cap would still be fully
# read into a subprocess capture_output buffer first, making the total-byte
# enforcement after-the-fact rather than byte-bounded.
_MAX_EVIDENCE_BLOB_BYTES = 8 * 1024 * 1024
# Raw ``git ls-tree -z`` output itself is bounded defensively: a PR cannot
# make the *content* of any blob larger than the cap above, but an adversary
# could in principle still try to inflate the listing's own output (e.g. an
# enormous number of long paths) ahead of the file-count check below, since
# that check only runs after the full listing is decoded. This caps the
# subprocess capture itself.
_MAX_LS_TREE_OUTPUT_BYTES = 16 * 1024 * 1024

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


def git_blob_bytes(ref: str, relative_path: str) -> bytes:
    """Read a candidate file's bytes at an arbitrary ref (e.g. the PR head).

    Distinct from ``trusted_bytes`` in name (not in mechanism) to keep
    "this ref's content is policy-trusted" separate from "this is the
    specific candidate content we are about to independently re-check" --
    callers of this function must already have hash-bound the result via
    ``check_binding``/``git_blob_sha256`` before treating it as the real
    repo's actual candidate.
    """
    completed = _run_git(["show", f"{ref}:{relative_path}"])
    if completed.returncode != 0:
        raise ReceiverFailure(
            "binding", f"Candidate binding references a path missing at {ref}: {relative_path}"
        )
    return completed.stdout


def git_changed_paths(base_sha: str, head_sha: str) -> set[str]:
    completed = _run_git(["diff", "--name-only", f"{base_sha}..{head_sha}"])
    if completed.returncode != 0:
        raise ReceiverFailure("binding", f"Unable to diff {base_sha}..{head_sha}")
    return {line.strip() for line in completed.stdout.decode().splitlines() if line.strip()}


def _git_ls_tree_blobs(ref: str, path_prefix: str) -> list[tuple[str, str, str]]:
    """List (mode, blob_sha, path) for every blob at ``ref`` under ``path_prefix``.

    Non-blob entries (symlinks, gitlinks/submodules) are rejected outright --
    this is untrusted PR content and must never be followed or executed. The
    blob sha is retained (not discarded) so callers can look up each blob's
    trusted-metadata size via ``git cat-file --batch-check`` before reading
    any content.
    """
    completed = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "ls-tree", "-r", "-z", ref, "--", path_prefix],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ReceiverFailure(
            "missing-evidence", f"Unable to list {path_prefix} at {ref}: {completed.stderr!r}"
        )
    if len(completed.stdout) > _MAX_LS_TREE_OUTPUT_BYTES:
        raise ReceiverFailure(
            "missing-evidence",
            f"Evidence tree listing for {path_prefix} at {ref} exceeds the bounded "
            f"output size of {_MAX_LS_TREE_OUTPUT_BYTES} bytes before it is even parsed.",
        )
    entries: list[tuple[str, str, str]] = []
    for record in completed.stdout.decode().split("\0"):
        if not record:
            continue
        meta, _, path = record.partition("\t")
        mode, obj_type, blob_sha = meta.split(" ", 2)
        if obj_type != "blob" or mode == "120000":
            raise ReceiverFailure(
                "missing-evidence",
                f"Evidence tree entry {path!r} at {ref} is not a plain file "
                f"(mode={mode}, type={obj_type}); refusing to materialize it.",
            )
        entries.append((mode, blob_sha, path))
    return entries


def _git_blob_sizes(blob_shas: list[str]) -> dict[str, int]:
    """Look up each blob's size from trusted git object metadata alone.

    This uses ``git cat-file --batch-check``, which never reads blob
    *content* -- only object headers -- so a blob's size can be checked and
    rejected before any of its bytes are ever loaded into memory via
    ``git show``/``capture_output``.
    """
    if not blob_shas:
        return {}
    completed = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "cat-file", "--batch-check=%(objectname) %(objectsize)"],
        input="\n".join(blob_shas).encode() + b"\n",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ReceiverFailure(
            "missing-evidence", f"Unable to read blob metadata: {completed.stderr!r}"
        )
    sizes: dict[str, int] = {}
    for line in completed.stdout.decode().splitlines():
        if not line or line.endswith("missing"):
            continue
        sha, _, size = line.partition(" ")
        sizes[sha] = int(size)
    return sizes


@contextmanager
def materialized_case_evidence(head_sha: str, case_dir_relative: str):
    """Safely copy a PR's own ``evidence/`` directory out of git objects.

    The PR head is untrusted and its evidence directory is archive-shaped
    input (many files, including nested JSON), so this never does a blind
    ``git archive | tar -x``: every path is listed explicitly via
    ``git ls-tree``, every blob's SIZE is checked from trusted git object
    metadata (``git cat-file --batch-check``) and rejected BEFORE any
    content is read, file count and total byte size are bounded, and every
    byte is written with plain file permissions (no symlinks, no executable
    bits) to a throwaway temporary directory that is removed afterwards.
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
    sizes = _git_blob_sizes([blob_sha for _mode, blob_sha, _path in blobs])
    total_bytes = 0
    for _mode, blob_sha, path in blobs:
        size = sizes.get(blob_sha)
        if size is None:
            raise ReceiverFailure("missing-evidence", f"Unable to size blob for {path!r}.")
        if size > _MAX_EVIDENCE_BLOB_BYTES:
            raise ReceiverFailure(
                "missing-evidence",
                f"Evidence file {path!r} is {size} bytes, exceeding the bounded "
                f"per-file limit of {_MAX_EVIDENCE_BLOB_BYTES} bytes; refusing to "
                "read its content at all.",
            )
        total_bytes += size
        if total_bytes > _MAX_EVIDENCE_TOTAL_BYTES:
            raise ReceiverFailure(
                "missing-evidence",
                f"Evidence directory exceeds the bounded size limit of "
                f"{_MAX_EVIDENCE_TOTAL_BYTES} bytes (checked from object metadata, "
                "before reading any content).",
            )
    with tempfile.TemporaryDirectory(prefix="receiver-evidence-") as scratch:
        scratch_root = Path(scratch)
        for _mode, _blob_sha, path in blobs:
            relative = PurePosixPath(path).relative_to(evidence_prefix)
            if ".." in relative.parts:
                raise ReceiverFailure("missing-evidence", f"Refusing path traversal: {path!r}")
            completed = _run_git(["show", f"{head_sha}:{path}"])
            if completed.returncode != 0:
                raise ReceiverFailure("missing-evidence", f"Unable to read {path} at {head_sha}")
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


_EXECUTION_TIMEOUT_SECONDS = 60


def _required_checks(base_sha: str) -> list[dict]:
    return trusted_json(base_sha, f"{_POLICY_DIR_RELATIVE}/required-checks.json").get("checks", [])


def execute_receiver_checks(
    base_sha: str, candidate_bytes_by_real_path: dict[str, bytes]
) -> list[dict]:
    """Independently RE-RUN the receiver's own required checks against the
    candidate's ACTUAL bytes, with no signing credentials and no trust in
    the evidence's own self-reported results.

    This is the missing unprivileged execution boundary: ``verify_evidence
    .verify()``'s ``"status": "passed"`` only certifies that the Evidence
    Package's recorded claims are internally CONSISTENT (its own
    ``_check_statement`` explicitly accepts a recorded "FAILED" or "WARNED"
    checker result as a valid, consistent claim) -- it never independently
    re-executes anything, so it cannot by itself prove the candidate really
    satisfies any requirement. Likewise ``check_case``/``assess_canonical``'s
    other checks (integrity, factory, binding, capability-revocation) only
    bind *identity and provenance*, never *behavior*. This function is the
    one place that actually proves behavior, by running the RECEIVER's own
    trusted test (read only from the base ref, never the untrusted evidence
    or PR tree) against the real candidate bytes in a throwaway scratch
    directory.

    Deliberately does NOT execute the untrusted evidence's own embedded
    ``checks/run_check.py`` -- that script is PR/evidence-controlled content
    with no real-repo binding, so a malicious or buggy PR could ship a
    rigged script that always reports success. The only code this function
    executes is: (a) the candidate's own bytes, which are inert data until
    imported by (b) the receiver's own pinned test module.

    Must be invoked from a zero-permission context with no attestation
    signing credentials and no policy write access; the candidate bytes
    given here must never be trusted beyond "this looks like the right path
    to test", since this function's entire purpose is to find out whether
    they are actually acceptable.
    """
    results = []
    for entry in _required_checks(base_sha):
        name = entry["name"]
        candidate_bytes = candidate_bytes_by_real_path.get(entry["candidatePath"])
        if candidate_bytes is None:
            results.append(
                {
                    "name": name,
                    "passed": False,
                    "detail": f"No candidate bytes supplied for {entry['candidatePath']!r}.",
                }
            )
            continue
        test_bytes = trusted_bytes(base_sha, entry["testPath"])
        with tempfile.TemporaryDirectory(prefix="receiver-execute-") as scratch:
            scratch_root = Path(scratch)
            candidate_dest = scratch_root / entry["candidateScratchRelative"]
            candidate_dest.parent.mkdir(parents=True, exist_ok=True)
            candidate_dest.write_bytes(candidate_bytes)
            test_dest = scratch_root / entry["testScratchRelative"]
            test_dest.parent.mkdir(parents=True, exist_ok=True)
            test_dest.write_bytes(test_bytes)
            node_id = f"{entry['testScratchRelative']}::{entry['testFunction']}"
            # Prefer an independently-pinned execution interpreter
            # (scripts/execution-requirements.txt, installed into its own
            # venv by the "execute" step of receiver-candidate-check.yml)
            # over the evidence-verification interpreter, so the untrusted
            # candidate's test run never shares a dependency set with the
            # CycloneDX/in-toto verification code. Falls back to the
            # current interpreter for local/unit-test runs.
            execution_python = os.environ.get("APMX_EXECUTION_PYTHON", sys.executable)
            try:
                completed = subprocess.run(
                    [execution_python, "-m", "pytest", "-q", node_id],
                    cwd=scratch_root,
                    capture_output=True,
                    timeout=_EXECUTION_TIMEOUT_SECONDS,
                    check=False,
                    env={"PATH": os.environ.get("PATH", "")},
                )
                passed = completed.returncode == 0
                detail = completed.stdout.decode(errors="replace")[-4000:]
            except subprocess.TimeoutExpired:
                passed = False
                detail = (
                    f"Execution of {node_id} exceeded the "
                    f"{_EXECUTION_TIMEOUT_SECONDS}s receiver timeout."
                )
        results.append({"name": name, "passed": passed, "detail": detail})
    return results


def check_execution_boundary(base_sha: str, results: list[dict]) -> None:
    required = {entry["name"] for entry in _required_checks(base_sha)}
    observed = {result["name"]: result for result in results}
    missing = required - observed.keys()
    if missing:
        raise ReceiverFailure(
            "execution",
            f"No independent execution result was recorded for required check(s): "
            f"{sorted(missing)}. The receiver-required behavioral check(s) were never "
            "actually re-run against the candidate's real bytes.",
        )
    failed = {
        name: result.get("detail", "")
        for name, result in observed.items()
        if name in required and not result.get("passed")
    }
    if failed:
        raise ReceiverFailure(
            "execution",
            "Independent re-execution of receiver-required check(s) against the "
            f"candidate's actual bytes failed: {sorted(failed)}. A signed assessment "
            "or structurally-valid evidence package never overrides this: the "
            "candidate must actually pass the receiver's own test.",
        )


def check_signer(evidence_dir: Path, base_sha: str, definition_sha256: str) -> None:
    """Authenticate that this exact evidence index was signed by the pinned
    attestation workflow, AT an approved signer revision, binding the
    signer's identity/ref, the specific evidence content it claims to have
    assessed, and the specific set of checks it claims to have run.

    A zero exit code from ``gh attestation verify`` only proves a valid
    signature from *some* run of the named workflow exists for this exact
    subject digest -- it does not, by itself, prove the signer evaluated the
    claims this receiver cares about, nor that it ran at a revision this
    receiver has actually reviewed and approved. So the parsed
    ``--format json`` output is inspected in full:

      * the subject digest is independently recomputed (never trusting gh's
        own report of it);
      * the source ref is pinned via ``--source-ref`` so only runs triggered
        from the receiver's own trusted branch count;
      * the signer's own COMMIT DIGEST (``signature.certificate
        .sourceRepositoryDigest`` -- a Fulcio/sigstore certificate SAN field
        populated directly from the OIDC token at signing time, which the
        signed predicate's own content cannot forge) is checked against an
        explicit allowlist in policy, so an older or weaker revision of the
        signer workflow on the SAME ref cannot sign the same accepted
        predicate. A workflow-path-plus-mutable-ref pin alone is NOT
        equivalent to this: ``signerWorkflow``/``sourceRef`` only say "some
        commit on this ref", not "a commit this receiver has reviewed".
        Revision trust is never read from the predicate/evidence itself --
        only from the certificate the verifier independently checks;
      * every field in the policy's ``expectedPredicate`` is compared
        exactly against the signed predicate (rejecting a validly-signed
        predicate that never actually claims policy acceptance);
      * the signed ``definitionSha256`` must equal the specific evidence
        digest independently computed by this check (not merely "some
        generic accepted predicate" -- it must be THIS evidence's own
        definition); and
      * the signed ``assessedChecks`` must be a superset of the policy's
        ``requiredAssessedChecks`` (rejecting a signature that is silent
        about, e.g., the independent execution boundary).
    """
    signers = trusted_json(base_sha, f"{_POLICY_DIR_RELATIVE}/signers.json")
    approved_source_digests = set(signers.get("approvedSignerSourceDigests", []))
    if not approved_source_digests:
        raise ReceiverFailure(
            "signer",
            "signers.json has no approvedSignerSourceDigests; refusing to trust any "
            "signer revision until the bootstrap commit-digest allowlist is populated.",
        )
    required_assessed_checks = set(signers.get("requiredAssessedChecks", []))
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
        certificate = (
            result.get("verificationResult", {}).get("signature", {}).get("certificate", {})
        )
        source_digest = certificate.get("sourceRepositoryDigest")
        if source_digest not in approved_source_digests:
            continue
        predicate = statement.get("predicate", {})
        if not all(predicate.get(key) == value for key, value in expected_predicate.items()):
            continue
        if predicate.get("definitionSha256") != definition_sha256:
            continue
        assessed_checks = set(predicate.get("assessedChecks", []))
        if required_assessed_checks and not required_assessed_checks.issubset(assessed_checks):
            continue
        return
    raise ReceiverFailure(
        "signer",
        "No verified attestation matched the exact expected subject digest "
        f"({expected_digest}), an approved signer commit digest, predicate "
        f"{expected_predicate!r}, definitionSha256 {definition_sha256}, and required "
        f"assessedChecks {sorted(required_assessed_checks)}. A signature exists but "
        "does not authenticate this evidence's actual content/claims/revision.",
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
        # Independent unprivileged re-execution: binding already proved the
        # evidence's recorded candidate hash matches the PR head's actual
        # tree, so it is now safe to read the real candidate bytes at
        # head_sha and re-prove behavior, not merely recorded/self-reported
        # claims (see execute_receiver_checks' docstring).
        binding_map = trusted_json(base_sha, f"{_POLICY_DIR_RELATIVE}/binding-map.json")
        candidate_bytes_by_real_path = {
            real_path: git_blob_bytes(head_sha, real_path)
            for real_path in binding_map.get("candidate", {}).values()
        }
        execution_results = execute_receiver_checks(base_sha, candidate_bytes_by_real_path)
        check_execution_boundary(base_sha, execution_results)
        checks.append("execution")
        if not skip_signer:
            check_signer(evidence_dir, base_sha, summary["definitionSha256"])
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
    verification would be circular before the signature exists. The
    independent execution boundary IS run here (sourcing candidate bytes
    directly from the canonical evidence package's own recorded artifacts,
    since there is no PR head to read from), so the signed predicate's
    "accepted" claim also proves the behavioral check actually passed, not
    merely that the evidence's own self-report was internally consistent.
    """
    summary = check_integrity(evidence_dir, schemas_dir)
    check_factory_policy(summary["definitionSha256"], base_sha)
    check_capability_revocation(evidence_dir, base_sha)
    index = _evidence_index(evidence_dir)
    run_id = _run_id(index)
    binding_map = trusted_json(base_sha, f"{_POLICY_DIR_RELATIVE}/binding-map.json")
    candidate_bytes_by_real_path = {
        real_path: (
            evidence_dir / "attempts" / run_id / "artifacts" / evidence_relative
        ).read_bytes()
        for evidence_relative, real_path in binding_map.get("candidate", {}).items()
    }
    execution_results = execute_receiver_checks(base_sha, candidate_bytes_by_real_path)
    check_execution_boundary(base_sha, execution_results)
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
