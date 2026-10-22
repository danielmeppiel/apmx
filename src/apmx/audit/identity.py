"""Identity: was this delivery verified and signed by a workflow the policy trusts?

In CI, ``run.yml``'s attest job signs a SLSA Verification Summary (VSA v1)
whose subjects are the delivered files, and the receipt bundle over the same
files, with GitHub artifact attestations (Sigstore, keyless, OIDC). ``apmx audit`` finds
them by a delivered file's digest, like ``npm audit signatures``. Nobody
passes it a signature.

The bundles stored for a digest are listed with ``gh api`` and verified with
``gh attestation verify --bundle`` (stock GitHub CLI, preinstalled on
GitHub-hosted runners). It checks the Sigstore bundle, the transparency log
and the Fulcio certificate. It is pinned to the signer
workflow in ``factories.signers``, the predicate type, and GitHub-hosted
runners. apmx then re-checks the verified statement independently: subjects,
PASSED result, the caller's binding (files, statements, definition), OIDC
issuer, signer workflow, its ``@ref``, its ``workflow_sha`` when pinned, and
that the VSA's ``verifier.id`` names the signing workflow at the signing
commit. Anything unexpected is treated as unsigned or unavailable, never as
signed.

The signer's commit is the certificate's Build Signer Digest (Fulcio extension
OID 1.3.6.1.4.1.57264.1.10, the OIDC ``job_workflow_sha`` claim), reported by
``gh`` as ``buildSignerDigest``. For a workflow called by an annotated tag it
is the tag object's SHA (``git rev-parse <tag>``), not the commit it points to.
A ``workflow_sha`` matches that digest directly, or the commit the tag object
names, resolved read-only with ``gh api repos/<owner>/<repo>/git/tags/<sha>``.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import verifier

PREDICATE_TYPE = verifier.VSA
BUNDLE_TYPE = verifier.BUNDLE_TYPE
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
Accept = Callable[[dict], str | None]


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
    summary: dict | None = None
    repository: str | None = None
    signer_digest: str | None = None


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


def statement_of(result: dict) -> dict:
    """The signed in-toto statement: the DSSE payload itself when gh returns the bundle."""
    envelope = ((result.get("attestation") or {}).get("bundle") or {}).get("dsseEnvelope") or {}
    payload = envelope.get("payload")
    if isinstance(payload, str):
        try:
            document = json.loads(base64.b64decode(payload, validate=True))
        except ValueError:
            document = None
        if isinstance(document, dict):
            return document
    return (result.get("verificationResult") or {}).get("statement") or {}


def _certificate(result: dict, workflow: str) -> tuple[str | None, str]:
    certificate = ((result.get("verificationResult") or {}).get("signature") or {}).get(
        "certificate"
    ) or {}
    if certificate.get("issuer") != ISSUER:
        return None, "certificate was not issued for GitHub Actions OIDC"
    if certificate.get("runnerEnvironment") != "github-hosted":
        return None, "signed on a self-hosted runner"
    uri = str(certificate.get("buildSignerURI") or "")
    prefix = f"https://github.com/{_workflow(workflow)}@"
    if not uri.lower().startswith(prefix):
        return None, "signed by a different workflow"
    return uri[len(prefix) :], ""


def _check(result: dict, *, digest: str, accept: Accept, workflow: str) -> tuple[str | None, str]:
    """Re-check one verified summary; return (signer ref, reason it does not match)."""
    statement = statement_of(result)
    if statement.get("predicateType") != PREDICATE_TYPE:
        return None, "unexpected predicate type"
    subjects = {
        (item.get("digest") or {}).get("sha256")
        for item in statement.get("subject") or []
        if isinstance(item, dict)
    }
    if digest not in subjects:
        return None, "subject is not this file"
    predicate = statement.get("predicate") or {}
    if predicate.get("verificationResult") != "PASSED":
        return None, "verification summary is not PASSED"
    ref, reason = _certificate(result, workflow)
    if ref is None:
        return None, reason
    certificate = result["verificationResult"]["signature"]["certificate"]
    signed = str(certificate.get("buildSignerDigest") or "").lower()
    expected = f"https://github.com/{_workflow(workflow)}@{signed}"
    if str((predicate.get("verifier") or {}).get("id") or "").lower() != expected:
        return None, "verifier.id does not name the signing workflow at its commit"
    reason = accept(statement)
    if reason is not None:
        return None, reason
    return ref, ""


def _attestations(
    artifact: Path, repo: str, workflow: str, predicate_type: str, run: Runner
) -> tuple[str, object]:
    """(``found``, results) | (``none``, None) | (``mismatch``, None) | (``unavailable``, reason).

    The bundles stored for the artifact's digest are listed unfiltered (GitHub's
    list API refuses ``predicate_type`` filters for some standard URIs, such as
    the SLSA VSA's), then verified locally with ``gh attestation verify
    --bundle``, which enforces the predicate type, signer and issuer.
    """
    digest = verifier.sha256(artifact.read_bytes())
    code, out, err = run(["gh", "api", f"repos/{repo}/attestations/sha256:{digest}?per_page=100"])
    if code != 0:
        text = (err + out).decode("utf-8", errors="replace").lower()
        if "http 404" in text or '"status":"404"' in text.replace(" ", ""):
            return "none", None
        last = [line for line in err.decode("utf-8", errors="replace").splitlines() if line.strip()]
        return "unavailable", _ascii(last[-1] if last else "gh api failed")
    try:
        listed = json.loads(out)["attestations"]
        bundles = [item["bundle"] for item in listed if isinstance(item.get("bundle"), dict)]
    except (ValueError, KeyError, TypeError, AttributeError):
        return "unavailable", "gh api returned unparseable attestations"
    if not bundles:
        return "none", None
    with tempfile.TemporaryDirectory(prefix="apmx-attestations-") as temporary:
        path = Path(temporary) / "bundles.jsonl"
        path.write_text("".join(json.dumps(item) + "\n" for item in bundles), encoding="utf-8")
        argv = [
            "gh",
            "attestation",
            "verify",
            str(artifact),
            "--repo",
            repo,
            "--bundle",
            str(path),
            "--signer-workflow",
            workflow,
            "--predicate-type",
            predicate_type,
            "--cert-oidc-issuer",
            ISSUER,
            "--deny-self-hosted-runners",
            "--format",
            "json",
        ]
        code, out, err = run(argv)
    text = err.decode("utf-8", errors="replace")
    if code != 0:
        if any(marker in text.lower() for marker in _NOT_FOUND):
            return "none", None
        if any(marker in text.lower() for marker in _MISMATCH):
            return "mismatch", None
        last = [line for line in text.splitlines() if line.strip()]
        return "unavailable", _ascii(last[-1] if last else "gh failed")
    try:
        results = json.loads(out)
    except ValueError:
        return "unavailable", "gh returned unparseable output"
    return "found", results if isinstance(results, list) else []


def statements(
    artifact: Path,
    identity: Identity,
    predicate_types: tuple[str, ...],
    *,
    run: Runner | None = None,
) -> list[dict]:
    """Statements the same signer, at the same commit, attested about ``artifact``.

    Raises ``ValueError`` when the store cannot be read; the caller binds the
    statements to the verification summary's ``inputAttestations`` by digest.
    """
    run = run or _gh
    found: list[dict] = []
    for predicate_type in predicate_types:
        status, results = _attestations(
            artifact, str(identity.repository), str(identity.signer), predicate_type, run
        )
        if status in ("none", "mismatch"):
            continue
        if status != "found":
            raise ValueError(f"attestations could not be fetched ({results})")
        for result in results:
            ref, _ = _certificate(result, str(identity.signer))
            certificate = result["verificationResult"]["signature"]["certificate"]
            if ref is None or (
                str(certificate.get("buildSignerDigest") or "").lower() != identity.signer_digest
            ):
                continue
            document = statement_of(result)
            if document.get("predicateType") == predicate_type:
                found.append(document)
    return found


def verify(
    artifact: Path,
    accept: Accept,
    signers,
    *,
    default_repository: str | None,
    run: Runner | None = None,
) -> Identity:
    """Find a PASSED verification summary about ``artifact`` from an allowed signer.

    ``accept`` re-checks the summary's binding (the files it covers, the
    statements it names); it returns why a summary does not match, or None.
    The first independently re-checked summary wins.
    """
    run = run or _gh
    digest = verifier.sha256(artifact.read_bytes())
    facts: dict[str, object] = {"artifactSha256": digest, "predicateType": PREDICATE_TYPE}
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
        try:
            status, results = _attestations(artifact, repo, signer.workflow, PREDICATE_TYPE, run)
        except FileNotFoundError:
            return Identity(
                "unavailable",
                "signature not checked: the GitHub CLI (gh) is not installed",
                facts=facts,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            unavailable.append(f"{signer.workflow}: gh failed ({type(exc).__name__})")
            continue
        if status == "none":
            continue
        if status == "mismatch":
            mismatches.append(f"{signer.workflow}: no attestation from this signer verified")
            continue
        if status == "unavailable":
            unavailable.append(f"{signer.workflow}: {results}")
            continue
        for result in results if isinstance(results, list) else []:
            ref, reason = _check(result, digest=digest, accept=accept, workflow=signer.workflow)
            if ref is not None and signer.ref is not None and ref != signer.ref:
                ref, reason = None, f"signed at {ref}, not {signer.ref}"
            if ref is None:
                mismatches.append(f"{signer.workflow}: attestation ignored ({reason})")
                continue
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
            facts.update(found)
            notes = [f"run {_ascii(certificate.get('runInvocationURI') or 'unknown')}"]
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
                summary=statement_of(result),
                repository=repo,
                signer_digest=raw,
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
