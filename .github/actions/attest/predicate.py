"""Prepare what run.yml's attest job signs: a SLSA VSA v1 and the receipt bundle.

Standard library only. ``summary BUNDLE`` audits the bundle with ``apmx audit
--offline`` from an empty directory (no repository policy or PR binding can
change what is signed), requires complete evidence, and writes two predicates
over the delivered files at their repository paths (``--prefix``):

- a SLSA Verification Summary v1: ``verifier.id`` is the signing workflow at
  its commit (from this job's OIDC token), ``policy`` is the factory
  definition digest, ``inputAttestations`` are the SHA-256 of every bundle
  line, ``verificationResult`` is PASSED and ``verifiedLevels`` is empty: apmx
  claims no SLSA level;
- the receipt bundle itself (``BUNDLE_TYPE``: ``{"lines": [statement, ...]}``),
  so ``apmx audit`` can fetch the statements by a delivered file's digest and a
  PR needs no receipt file. Signing it asserts nothing by itself: the summary
  binds each line by digest. (GitHub's attestation store refuses a SLSA
  provenance whose ``buildType`` it does not know, so the provenance cannot be
  stored as a statement of its own.)
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

VSA = "https://slsa.dev/verification_summary/v1"
BUNDLE_TYPE = "https://github.com/danielmeppiel/apmx/attestation/receipt-bundle/v2"
PAYLOAD_TYPE = "application/vnd.in-toto+json"
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_SHA = re.compile(r"[0-9a-f]{40}")


def _canonical(document: object) -> bytes:
    return json.dumps(
        document, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def lines(bundle: Path) -> list[tuple[bytes, dict]]:
    """(line bytes, statement) for each line; refuses anything but unsigned canonical lines."""
    found = []
    for line in bundle.read_bytes().splitlines():
        envelope = json.loads(line)
        if (
            _canonical(envelope) != line
            or envelope.get("payloadType") != PAYLOAD_TYPE
            or envelope.get("signatures") != []
        ):
            raise ValueError("the bundle is not an apmx receipt (canonical unsigned lines)")
        found.append((line, json.loads(base64.b64decode(envelope["payload"], validate=True))))
    if not found:
        raise ValueError("the bundle is empty")
    return found


def _checksums(subjects: list[dict], prefix: str = "") -> str:
    rows = []
    for item in subjects:
        name = prefix + item["name"]
        if "\n" in name or not re.fullmatch(r"[0-9a-f]{64}", item["digest"]["sha256"]):
            raise ValueError(f"unsupported subject {name!r}")
        rows.append(f"{item['digest']['sha256']}  {name}\n")
    return "".join(rows)


def bundle_predicate(bundle: Path) -> dict:
    """The receipt's statements, in line order, as one attestable predicate."""
    return {"lines": [document for _, document in lines(bundle)]}


def _oidc_claims() -> dict:
    """This job's OIDC claims: who will sign, at which commit (job_workflow_ref/sha)."""
    url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not token:
        raise ValueError("no OIDC token: the job needs id-token: write")
    request = urllib.request.Request(
        url + "&audience=apmx-verifier", headers={"Authorization": f"bearer {token}"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        value = json.loads(response.read(64 * 1024))["value"]
    payload = value.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def verifier_id(claims: dict) -> str:
    workflow = str(claims.get("job_workflow_ref", "")).split("@", 1)[0]
    sha = str(claims.get("job_workflow_sha", "")).lower()
    if not workflow.count("/.github/workflows/") == 1 or not _SHA.fullmatch(sha):
        raise ValueError("the OIDC token names no job workflow and commit")
    return f"https://github.com/{workflow}@{sha}"


def _audit(bundle: Path, apmx: str) -> dict:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("GITHUB_ACTIONS", "GITHUB_EVENT_NAME", "GITHUB_EVENT_PATH")
    }
    env["NO_COLOR"] = "1"
    with tempfile.TemporaryDirectory(prefix="apmx-attest-") as neutral:
        completed = subprocess.run(
            [apmx, "audit", str(bundle.resolve()), "--offline", "--format", "json"],
            cwd=neutral,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    try:
        report = json.loads(completed.stdout)
    except ValueError as exc:
        raise ValueError(f"apmx audit produced no report: {completed.stderr.strip()}") from exc
    if completed.returncode != 0 or report.get("result") != "valid":
        raise ValueError(f"refusing to sign: apmx audit says {report.get('reason') or report}")
    if report["facts"].get("evidence") != "complete":
        raise ValueError("refusing to sign: the receipt of record's evidence is not complete")
    return report


def prefix_of(value: str) -> str:
    value = value.strip().strip("/")
    if value in ("", "."):
        return ""
    if any(part in ("", ".", "..") for part in value.split("/")) or "\\" in value:
        raise ValueError(f"unsafe delivery directory {value!r}")
    return value + "/"


def summary(
    bundle: Path,
    *,
    prefix: str,
    verifier: str,
    resource: str,
    report: dict,
    now: datetime | None = None,
) -> dict:
    entries = lines(bundle)
    if any(document["predicateType"] == VSA for _, document in entries):
        raise ValueError("the bundle already carries a verification summary")
    digests = [hashlib.sha256(line).hexdigest() for line, _ in entries]
    if digests != report["facts"]["lineDigests"]:
        raise ValueError("the audited line digests differ from the bundle")
    definition = report["facts"]["definitionSha256"]
    subjects = entries[0][1]["subject"]
    moment = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [
            {"name": prefix + item["name"], "digest": {"sha256": item["digest"]["sha256"]}}
            for item in subjects
        ],
        "predicateType": VSA,
        "predicate": {
            "verifier": {"id": verifier},
            "timeVerified": moment,
            "resourceUri": resource,
            "policy": {"uri": f"apmx:factory/{definition}", "digest": {"sha256": definition}},
            "inputAttestations": [
                {"uri": f"receipt.intoto.jsonl#{n}", "digest": {"sha256": digest}}
                for n, digest in enumerate(digests, 1)
            ],
            "verificationResult": "PASSED",
            "verifiedLevels": [],
        },
    }


def resource_uri(env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    repository = env.get("APMX_BASE_REPOSITORY") or env.get("GITHUB_REPOSITORY", "")
    commit = (env.get("APMX_BASE_COMMIT") or env.get("GITHUB_SHA", "")).lower()
    if not _REPO.fullmatch(repository) or not _SHA.fullmatch(commit):
        raise ValueError("no repository and commit to name as the verified resource")
    return f"git+https://github.com/{repository}@{commit}"


def _outputs(values: dict[str, str]) -> None:
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        stream.writelines(f"{key}={value}\n" for key, value in values.items())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    many = commands.add_parser("summary")
    many.add_argument("bundle", type=Path)
    many.add_argument("--prefix", default="")
    many.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        bundle = args.bundle / "receipt.intoto.jsonl" if args.bundle.is_dir() else args.bundle
        apmx = shutil.which("apmx")
        if apmx is None:
            raise ValueError("apmx is not on PATH; run the setup action first")
        report = _audit(bundle, apmx)
        document = summary(
            bundle,
            prefix=prefix_of(args.prefix),
            verifier=verifier_id(_oidc_claims()),
            resource=resource_uri(),
            report=report,
        )
        args.output.mkdir(parents=True, exist_ok=True)
        predicate = args.output / "predicate.json"
        subjects = args.output / "subjects.txt"
        receipt = args.output / "bundle.json"
        predicate.write_text(json.dumps(document["predicate"], sort_keys=True), encoding="ascii")
        subjects.write_text(_checksums(document["subject"]), encoding="utf-8")
        receipt.write_text(json.dumps(bundle_predicate(bundle), sort_keys=True), encoding="ascii")
        _outputs(
            {
                "predicate": str(predicate),
                "bundle": str(receipt),
                "subjects": str(subjects),
                "definition": report["facts"]["definitionSha256"],
            }
        )
        print(
            f"[+] receipt is VALID with complete evidence; signing a PASSED verification "
            f"summary of {len(document['subject'])} file(s) for definition "
            f"{report['facts']['definitionSha256'][:12]}.."
        )
        return 0
    except (ValueError, KeyError, OSError) as exc:
        print(f"::error title=apmx attest::{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
