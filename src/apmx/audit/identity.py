"""Identity row: is this receipt signed by a workflow the policy trusts?

Receipts are signed in CI with a GitHub artifact attestation (Sigstore,
keyless, OIDC) over the SHA-256 of the receipt's ``index.json``, which binds
every other receipt file. ``apmx audit`` finds that attestation by digest,
like ``npm audit signatures``. Nobody passes it a signature.

Verification is delegated to ``gh attestation verify`` (stock GitHub CLI,
preinstalled on GitHub-hosted runners). It checks the Sigstore bundle, the
transparency log and the Fulcio certificate. It is pinned to the signer
workflow in ``factories.signers``, the apmx predicate type, and GitHub-hosted
runners. apmx then re-checks the verified statement independently: subject
digest, receipt digest, definition digest, OIDC issuer, signer workflow, its
``@ref`` and its ``workflow_sha`` when pinned. Anything unexpected is treated
as unsigned or unavailable, never as signed.

The signer's commit is the certificate's Build Signer Digest (Fulcio extension
OID 1.3.6.1.4.1.57264.1.10, the OIDC ``job_workflow_sha`` claim), reported by
``gh`` as ``buildSignerDigest``. For a workflow called by an annotated tag it
is the tag object's SHA (``git rev-parse <tag>``), not the commit it points to.
A ``workflow_sha`` matches that digest directly, or the commit the tag object
names, resolved read-only with ``gh api repos/<owner>/<repo>/git/tags/<sha>``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import verifier

PREDICATE_TYPE = "https://github.com/danielmeppiel/apmx/attestation/receipt/v1"
ISSUER = "https://token.actions.githubusercontent.com"
_NOT_FOUND = ("no attestations found", "no matching attestations", "http 404")
# Attestations exist but none satisfies the signer constraints. gh reports this,
# when not on a terminal, only as `Error: verifying with issuer "sigstore.dev"`
# (observed with gh 2.102.0). Treating it as "no matching attestation" can only
# reject a receipt, never accept one.
_MISMATCH = ("verifying with issuer", "verification failed", "failed to verify")
_GITHUB = re.compile(r"(?:https://|git@|ssh://git@)github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$")
_GIT_SHA = re.compile(r"[0-9a-f]{40}")

Runner = Callable[[list[str]], tuple[int, bytes, bytes]]


@dataclass
class Identity:
    """``signed``, ``unsigned``, ``mismatch`` (signed at a commit the policy does not pin),
    ``unresolved`` (the signer's tag object could not be resolved to compare with
    ``workflow_sha``) or ``unavailable`` (could not check). Only ``signed`` passes."""

    status: str
    detail: str
    signer: str | None = None
    notes: list[str] = field(default_factory=list)
    facts: dict[str, object] = field(default_factory=dict)


def _gh(argv: list[str]) -> tuple[int, bytes, bytes]:
    completed = subprocess.run(argv, capture_output=True, check=False, timeout=120)
    return completed.returncode, completed.stdout, completed.stderr


def repository(root: Path, env: dict[str, str] | None = None) -> str | None:
    """Where attestations are stored by default: this repository."""
    env = os.environ if env is None else env
    if re.fullmatch(r"[\w.-]+/[\w.-]+", env.get("GITHUB_REPOSITORY", "")):
        return env["GITHUB_REPOSITORY"]
    try:
        completed = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = _GITHUB.match(completed.stdout.decode("utf-8", errors="replace").strip())
    return match.group(1) if completed.returncode == 0 and match else None


def _ascii(text: str, limit: int = 200) -> str:
    text = " ".join(str(text).split())[:limit]
    return "".join(char if " " <= char <= "~" else "?" for char in text)


def _workflow(signer_workflow: str) -> str:
    return signer_workflow.removeprefix("github.com/").lower()


def _check(result: dict, *, digest: str, definition: str, workflow: str) -> tuple[str | None, str]:
    """Re-check one verified attestation; return (signer ref, reason it does not match)."""
    verified = result.get("verificationResult") or {}
    statement = verified.get("statement") or {}
    if statement.get("predicateType") != PREDICATE_TYPE:
        return None, "unexpected predicate type"
    subjects = {
        (item.get("digest") or {}).get("sha256")
        for item in statement.get("subject") or []
        if isinstance(item, dict)
    }
    if digest not in subjects:
        return None, "subject is not this receipt's index.json"
    predicate = statement.get("predicate") or {}
    if (predicate.get("receipt") or {}).get("sha256") != digest:
        return None, "predicate names a different receipt"
    if predicate.get("definitionSha256") != definition:
        return None, "predicate names a different factory definition"
    certificate = (verified.get("signature") or {}).get("certificate") or {}
    if certificate.get("issuer") != ISSUER:
        return None, "certificate was not issued for GitHub Actions OIDC"
    if certificate.get("runnerEnvironment") != "github-hosted":
        return None, "signed on a self-hosted runner"
    uri = str(certificate.get("buildSignerURI") or "")
    prefix = f"https://github.com/{_workflow(workflow)}@"
    if not uri.lower().startswith(prefix):
        return None, "signed by a different workflow"
    return uri[len(prefix) :], ""


def verify(
    index: Path,
    definition_sha256: str,
    signers,
    *,
    default_repository: str | None,
    run: Runner | None = None,
) -> Identity:
    """Try each allowed signer; the first independently re-checked attestation wins."""
    run = run or _gh
    digest = verifier.sha256(index.read_bytes())
    facts: dict[str, object] = {"receiptSha256": digest, "predicateType": PREDICATE_TYPE}
    unavailable: list[str] = []
    mismatches: list[str] = []
    wrong: list[tuple[str, dict[str, object], str]] = []
    unresolved: list[str] = []
    tags: dict[tuple[str, str], str | None | _Unresolved] = {}
    for signer in signers:
        repo = signer.repo or default_repository
        if repo is None:
            unavailable.append(f"{signer.workflow}: no repository to look up attestations in")
            continue
        argv = [
            "gh",
            "attestation",
            "verify",
            str(index),
            "--repo",
            repo,
            "--signer-workflow",
            signer.workflow,
            "--predicate-type",
            PREDICATE_TYPE,
            "--cert-oidc-issuer",
            ISSUER,
            "--deny-self-hosted-runners",
            "--format",
            "json",
        ]
        try:
            code, out, err = run(argv)
        except FileNotFoundError:
            return Identity(
                "unavailable",
                "signature not checked: the GitHub CLI (gh) is not installed",
                facts=facts,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            unavailable.append(f"{signer.workflow}: gh failed ({type(exc).__name__})")
            continue
        text = err.decode("utf-8", errors="replace")
        if code != 0:
            if any(marker in text.lower() for marker in _NOT_FOUND):
                continue
            if any(marker in text.lower() for marker in _MISMATCH):
                mismatches.append(f"{signer.workflow}: no attestation from this signer verified")
                continue
            last = [line for line in text.splitlines() if line.strip()]
            unavailable.append(f"{signer.workflow}: {_ascii(last[-1] if last else 'gh failed')}")
            continue
        try:
            results = json.loads(out)
        except ValueError:
            unavailable.append(f"{signer.workflow}: gh returned unparseable output")
            continue
        for result in results if isinstance(results, list) else []:
            ref, reason = _check(
                result, digest=digest, definition=definition_sha256, workflow=signer.workflow
            )
            if ref is not None and signer.ref is not None and ref != signer.ref:
                ref, reason = None, f"signed at {ref}, not {signer.ref}"
            if ref is None:
                mismatches.append(f"{signer.workflow}: attestation ignored ({reason})")
                continue
            predicate = result["verificationResult"]["statement"]["predicate"]
            checks = predicate.get("checks")
            certificate = result["verificationResult"]["signature"]["certificate"]
            raw = str(certificate.get("buildSignerDigest") or "").lower()
            sha = raw if _GIT_SHA.fullmatch(raw) else None
            found = {
                "signer": signer.workflow,
                "signerRef": ref,
                "signerDigest": certificate.get("buildSignerDigest"),
                "signerSha": sha,
                "signerCommit": None,
                "requiredSignerSha": signer.workflow_sha,
                "signerPin": _pin(signer),
                "sourceRepository": certificate.get("sourceRepositoryURI"),
                "run": certificate.get("runInvocationURI"),
            }
            at = sha[:12] if sha else None
            if signer.workflow_sha is not None and sha != signer.workflow_sha:
                target = None
                if sha is not None:
                    key = (signer.workflow, sha)
                    if key not in tags:
                        tags[key] = _tag_target(signer.workflow, sha, run)
                    target = tags[key]
                if isinstance(target, _Unresolved):
                    unresolved.append(
                        f"{signer.workflow}: could not resolve signer digest {sha[:12]} "
                        f"to a commit ({target.reason})"
                    )
                    continue
                if target is not None:
                    at = f"{target[:12]} (tag object {sha[:12]})"
                    found["signerCommit"] = target
                if target != signer.workflow_sha:
                    required = signer.workflow_sha[:12]
                    detail = (
                        f"signed by {_ascii(signer.workflow)} at {at or 'an unknown commit'}, "
                        f"policy requires {required}"
                    )
                    wrong.append((detail, found, ref))
                    continue
            facts.update(found, checks=checks)
            notes = [f"run {_ascii(certificate.get('runInvocationURI') or 'unknown')}"]
            if checks:
                notes.insert(0, f"checks: {_ascii(checks).replace('-', ' ')}")
            if signer.workflow_sha is None and signer.ref is not None:
                notes.append(
                    f"[!] signer pinned by mutable ref {_ascii(signer.ref, 80)}; pin workflow_sha"
                )
            elif signer.workflow_sha is None:
                notes.append("[!] signer not pinned (any ref); pin workflow_sha")
            detail = f"signed by {_ascii(signer.workflow)}@{_ascii(ref, 80)}"
            return Identity(
                "signed",
                f"{detail} at {at}" if at else detail,
                signer=signer.workflow,
                notes=notes,
                facts=facts,
            )
    if unresolved:
        return Identity(
            "unresolved",
            "signer commit could not be checked against workflow_sha",
            notes=[*unresolved, *unavailable][:5],
            facts=facts,
        )
    if unavailable:
        return Identity(
            "unavailable",
            "signature could not be checked",
            notes=[*unavailable, *mismatches, *(detail for detail, _, _ in wrong)][:5],
            facts=facts,
        )
    if wrong:
        detail, found, ref = wrong[0]
        facts.update(found)
        notes = [f"certificate: {_ascii(found['signer'])}@{_ascii(ref, 80)}"]
        return Identity("mismatch", detail, notes=notes, facts=facts)
    return Identity(
        "unsigned",
        "unsigned: no attestation from factories.signers",
        notes=mismatches[:5],
        facts=facts,
    )


@dataclass(frozen=True)
class _Unresolved:
    reason: str


def _tag_target(workflow: str, sha: str, run: Runner) -> str | None | _Unresolved:
    """The commit an annotated tag object ``sha`` names in the workflow's repository.

    ``None`` when ``sha`` is not a tag object there (GitHub answers 404, as for a
    commit). Read-only GitHub REST lookup through ``gh api``; anything else
    unexpected is unresolved, never a match.
    """
    parts = _workflow(workflow).split("/")
    if len(parts) < 3 or parts[2] != ".github":
        return _Unresolved("signer workflow is not on github.com")
    repo = f"{parts[0]}/{parts[1]}"
    try:
        code, out, err = run(["gh", "api", f"repos/{repo}/git/tags/{sha}"])
    except (OSError, subprocess.SubprocessError) as exc:
        return _Unresolved(f"gh failed ({type(exc).__name__})")
    text = err.decode("utf-8", errors="replace")
    if code != 0:
        if "http 404" in text.lower():
            return None
        last = [line for line in text.splitlines() if line.strip()]
        return _Unresolved(_ascii(last[-1] if last else "gh failed", 120))
    try:
        tag = json.loads(out)
    except ValueError:
        return _Unresolved("gh returned unparseable output")
    target = (tag.get("object") or {}) if isinstance(tag, dict) else {}
    commit = str(target.get("sha") or "").lower()
    if tag.get("sha") != sha or target.get("type") != "commit" or not _GIT_SHA.fullmatch(commit):
        return _Unresolved("unexpected tag object")
    return commit


def _pin(signer) -> str:
    if signer.workflow_sha is not None:
        return "workflow_sha"
    return "ref" if signer.ref is not None else "none"
