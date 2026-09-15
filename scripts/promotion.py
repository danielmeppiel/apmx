"""Draft creation, exact-asset download, and explicit verified publication.

Only workflow jobs explicitly holding contents:write may create/publish. Failed
verification leaves a draft for inspection; this helper never deletes or reuses
an existing release. SHA-256 checks are integrity checks, not publisher signing.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

from scripts.release import (
    TARGETS,
    VERSION,
    archive_name,
    digest,
    extract_archive,
    make_manifest,
    output_values,
    verify_archive,
)

REPOSITORY = "danielmeppiel/apmx"
PUBLIC_WORKFLOW = ".github/workflows/native-notice-release.yml"
VERIFICATION_ARTIFACT = "verified-native-draft"
VERIFICATION_FILE = "verified-draft.json"
VERIFICATION_SCHEMA = "apmx-verified-native-draft/1"


def gh_json(*args: str):
    return json.loads(subprocess.check_output(["gh", *args], text=True, encoding="utf-8"))


def check_repository(repository: str, *, public_release: bool = False) -> None:
    if repository != REPOSITORY:
        raise ValueError("Release promotion is restricted to the standalone repository")
    metadata = gh_json("api", f"repos/{repository}")
    if public_release:
        if (
            metadata.get("private") is not False
            or metadata.get("full_name") != REPOSITORY
            or metadata.get("fork") is not False
            or metadata.get("default_branch") != "main"
        ):
            raise ValueError("Explicit public promotion requires the standalone non-fork main repository")
    elif metadata["private"] is not True:
        raise ValueError("Refusing to publish outside a private repository")


def check_candidate(repository: str, version: str, commit: str) -> None:
    if not re.fullmatch(VERSION, version) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Invalid candidate version or commit")
    reference = gh_json("api", f"repos/{repository}/git/ref/tags/v{version}")["object"]
    for _ in range(4):
        if reference["type"] == "commit":
            if reference["sha"] != commit:
                raise ValueError("Release tag moved away from the verified candidate")
            return
        if reference["type"] != "tag":
            break
        reference = gh_json("api", f"repos/{repository}/git/tags/{reference['sha']}")["object"]
    raise ValueError("Tag does not resolve to the candidate commit")


def expected_names(version: str) -> set[str]:
    names = {archive_name(version, target) for target in TARGETS}
    return names | {name + ".sha256" for name in names} | {"release-manifest.json"}


def asset_fingerprint(assets: list[dict]) -> str:
    identities = [
        {key: asset.get(key) for key in ("id", "name", "size", "updated_at", "digest")}
        for asset in assets
    ]
    serialized = json.dumps(sorted(identities, key=lambda row: row["name"]), sort_keys=True).encode()
    return hashlib.sha256(serialized).hexdigest()


def inspect_draft(
    repository: str, release_id: int, version: str, commit: str, expected_assets: str | None = None,
    *, public_release: bool = False,
) -> tuple[dict, list[dict]]:
    release = gh_json("api", f"repos/{repository}/releases/{release_id}")
    assets = gh_json("api", f"repos/{repository}/releases/{release_id}/assets?per_page=100")
    if (
        release["draft"] is not True
        or release["tag_name"] != f"v{version}"
        or release["target_commitish"] != commit
        or release["id"] != release_id
    ):
        raise ValueError("Release is not the expected private draft candidate")
    if public_release and release.get("prerelease") is not True:
        raise ValueError("Public experimental candidate must remain a prerelease")
    if len(assets) != 11 or {asset["name"] for asset in assets} != expected_names(version):
        raise ValueError("Draft assets differ from the exact five-platform candidate set")
    if any(asset["state"] != "uploaded" or asset["size"] <= 0 for asset in assets):
        raise ValueError("Draft asset upload is incomplete")
    if expected_assets and asset_fingerprint(assets) != expected_assets:
        raise ValueError("Draft assets were replaced or changed after candidate creation")
    return release, assets


def release_notes(version: str, commit: str) -> str:
    return (
        f"Standalone apmx {version}; candidate commit `{commit}`.\n\n"
        "## What's new since v0.2.0\n\n"
        "- Run a factory directory without writing orchestration: APMX derives execution "
        "order from the contracts' declared file dependencies.\n"
        "- Deliver multiple output files together. Handoffs require every declared file, "
        "complete passing checks and an intact recorded inventory; the aggregate artifact "
        "view retains admitted outputs and the original required inputs/checks.\n"
        "- Export real patches from private source edits with the bounded artifact tools, "
        "without changing the original checkout.\n"
        "- Discover selected imported skills through native Copilot skill discovery, "
        "with clearer package-preparation progress and bounded terminal evidence.\n"
        "- Try the software-factory example with optional Gherkin/Behave acceptance checks "
        "and separate regression checks. Its standalone checker now supports deeply nested "
        "Windows file paths with child-only Git configuration.\n\n"
        "## Compatibility and migration\n\n"
        "**BREAKING: imported skill metadata.** Previously accepted imported skills can "
        "now fail admission without a nonempty `name` and `description` in `SKILL.md`. "
        "Names must be 1-64 lowercase letters/digits separated by single hyphens, with "
        "no leading or trailing hyphen. Selected context names must not collide "
        "case-insensitively; unsupported activation metadata is rejected.\n\n"
        "Update the authored source package (for example, `name: handoff-style` with "
        "`description: Write concise handoffs.`), then select its updated revision through "
        "the normal dependency workflow, keeping `apm.yml` and its lock coherent. "
        "Do not patch generated `apm_modules` copies or edit lock digests by hand. "
        f"[Migration guide](https://github.com/{REPOSITORY}/blob/v{version}/"
        "docs/install.md#migrating-from-v020-to-v030).\n\n"
        "Scalar contracts and single-contract invocations remain supported. Scalar run "
        "records retain `apm-contract-run/0.1`; multiple-output inventories use "
        "`apm-contract-run/0.2`. Existing saved runs are not rewritten.\n\n"
        "Factory execution asks for consent interactively. Automation must pass "
        "`--allow-host-access --allow-unproven-inputs`; these flags do not admit failed "
        "or incomplete handoffs. Native execution is not a sandbox, and complete passing "
        "checks still yield **UNPROVEN (21)**, not VERIFIED.\n\n"
        "## Installation and assurance limits\n\n"
        "Five native onedir archives include the runtime, LICENSE, NOTICE, release metadata, "
        "and the pinned official APM 0.30.0 backend with its complete runtime under libexec/apm. "
        "Extract the complete archive; do not move the executable out of its runtime directory.\n\n"
        "Git, Copilot CLI, and contract-declared checker tools are external prerequisites. "
        "The CI actor is an explicitly hermetic Copilot JSONL protocol fixture, NOT live inference. "
        "Passing native checks yield UNPROVEN (21), rejection 20, operational halt 22.\n\n"
        "No trusted publisher signature or Apple notarization is provided. macOS may carry "
        "PyInstaller's local ad-hoc signature; it is not publisher identity. "
        "Checksums prove byte integrity, not publisher authentication. "
        "Do not disable platform security to run these binaries.\n"
    )


def check_public_bundle(bundle: Path, version: str, commit: str) -> None:
    metadata = json.loads((bundle / "RELEASE.json").read_text(encoding="utf-8"))
    if metadata.get("version") != version or metadata.get("source_commit") != commit:
        raise ValueError("Native archive does not identify the reviewed source candidate")


def create_draft(
    repository: str, assets: Path, version: str, commit: str, *, public_release: bool = False,
) -> None:
    check_repository(repository, public_release=public_release)
    check_candidate(repository, version, commit)
    manifest = make_manifest(assets, version, commit)
    if public_release:
        for target in TARGETS:
            with tempfile.TemporaryDirectory(prefix="apmx-notice-preflight-") as temporary:
                bundle = extract_archive(
                    assets / archive_name(version, target), Path(temporary) / "extracted",
                )
                check_public_bundle(bundle, version, commit)
    notes = release_notes(version, commit)
    if public_release:
        notes = "**EXPERIMENTAL prerelease.** Native notices and exact downloaded bytes are gated; this is not production certification.\n\n" + notes
    with tempfile.TemporaryDirectory(prefix="apmx-release-notes-") as temporary:
        path = Path(temporary) / "notes.txt"
        path.write_text(notes, encoding="utf-8")
        subprocess.run(
            [
                "gh", "release", "create", f"v{version}", "--repo", repository,
                "--draft", "--verify-tag", "--target", commit, "--title", f"apmx {version}",
                "--notes-file", str(path),
                *(["--prerelease", "--latest=false"] if public_release else []),
                *[str(assets / name) for name in sorted(expected_names(version))],
            ],
            check=True,
        )
    drafts = gh_json("api", "--paginate", "--slurp", f"repos/{repository}/releases?per_page=100")
    matches = [item for page in drafts for item in page if item["tag_name"] == f"v{version}"]
    if len(matches) != 1:
        raise ValueError("Could not identify the newly created draft release")
    release_id = matches[0]["id"]
    _, uploaded = inspect_draft(
        repository, release_id, version, commit, public_release=public_release,
    )
    output_values(
        release_id=str(release_id), manifest_sha=digest(manifest),
        assets_fingerprint=asset_fingerprint(uploaded),
    )


def download_asset(repository: str, asset: dict, destination: Path) -> None:
    with destination.open("xb") as stream:
        subprocess.run(
            [
                "gh", "api", f"repos/{repository}/releases/assets/{asset['id']}",
                "--header", "Accept: application/octet-stream",
            ],
            stdout=stream, check=True,
        )
    if destination.stat().st_size != asset["size"]:
        raise ValueError("Downloaded asset size differs from candidate metadata")


def download_candidate(args) -> None:
    check_repository(args.repository, public_release=args.public_release)
    check_candidate(args.repository, args.version, args.commit)
    _, assets = inspect_draft(
        args.repository, args.release_id, args.version, args.commit, args.assets_fingerprint,
        public_release=args.public_release,
    )
    if args.destination.exists():
        raise ValueError("Asset download requires a fresh destination")
    args.destination.mkdir(parents=True)
    lookup = {asset["name"]: asset for asset in assets}
    filename = archive_name(args.version, args.target)
    for name in ("release-manifest.json", filename, filename + ".sha256"):
        download_asset(args.repository, lookup[name], args.destination / name)
    manifest_path = args.destination / "release-manifest.json"
    if digest(manifest_path) != args.manifest_sha:
        raise ValueError("Downloaded manifest differs from the build-anchored candidate")
    manifest = json.loads(manifest_path.read_bytes())
    if (
        manifest["schema"] != "apmx-release/1"
        or manifest["commit"] != args.commit
        or manifest["version"] != args.version
        or set(manifest["archives"]) != {archive_name(args.version, target) for target in TARGETS}
    ):
        raise ValueError("Downloaded manifest identifies a different candidate")
    archive = args.destination / filename
    verify_archive(archive, manifest["archives"][filename])
    bundle = extract_archive(archive, args.destination / "extracted")
    if args.public_release:
        check_public_bundle(bundle, args.version, args.commit)
    inspect_draft(
        args.repository, args.release_id, args.version, args.commit, args.assets_fingerprint,
        public_release=args.public_release,
    )
    output_values(bundle=str(bundle), archive_sha=digest(archive))


def write_verification(args) -> None:
    if (
        not args.public_release
        or args.repository != REPOSITORY
        or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
        or os.environ.get("GITHUB_SHA") != args.commit
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
        or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
        or os.environ.get("GITHUB_WORKFLOW_REF") != f"{REPOSITORY}/{PUBLIC_WORKFLOW}@refs/heads/main"
    ):
        raise ValueError("Verification receipt requires the trusted public preparation workflow")
    if (
        not re.fullmatch(VERSION, args.version)
        or not re.fullmatch(r"[0-9a-f]{40}", args.commit)
        or args.release_id <= 0
        or not re.fullmatch(r"[0-9a-f]{64}", args.manifest_sha)
        or not re.fullmatch(r"[0-9a-f]{64}", args.assets_fingerprint)
    ):
        raise ValueError("Invalid verified candidate identity")
    run_id = int(os.environ["GITHUB_RUN_ID"])
    attempt = int(os.environ["GITHUB_RUN_ATTEMPT"])
    if run_id <= 0 or attempt <= 0:
        raise ValueError("Invalid preparation workflow run identity")
    receipt = {
        "schema": VERIFICATION_SCHEMA, "repository": args.repository,
        "version": args.version, "commit": args.commit, "release_id": args.release_id,
        "manifest_sha": args.manifest_sha, "assets_fingerprint": args.assets_fingerprint,
        "workflow": PUBLIC_WORKFLOW, "run_id": run_id, "run_attempt": attempt,
    }
    with args.receipt.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    output_values(
        receipt=str(args.receipt), release_id=str(args.release_id),
        assets_fingerprint=args.assets_fingerprint, manifest_sha=args.manifest_sha,
    )


def check_verified_receipt(args) -> dict:
    run_id = args.verified_run_id
    if type(run_id) is not int or run_id <= 0:
        raise ValueError("Public publication requires a verified preparation run")
    run = gh_json("api", f"repos/{args.repository}/actions/runs/{run_id}")
    if (
        run.get("id") != run_id or run.get("status") != "completed"
        or run.get("conclusion") != "success" or run.get("event") != "workflow_dispatch"
        or run.get("path") != PUBLIC_WORKFLOW or run.get("head_sha") != args.commit
        or run.get("head_branch") != "main"
        or run.get("repository", {}).get("full_name") != REPOSITORY
        or run.get("head_repository", {}).get("full_name") != REPOSITORY
        or type(run.get("run_attempt")) is not int or run["run_attempt"] <= 0
    ):
        raise ValueError("Preparation run is not the successful trusted source workflow")
    jobs = gh_json(
        "api", f"repos/{args.repository}/actions/runs/{run_id}/attempts/{run['run_attempt']}/jobs?per_page=100",
    )
    if jobs.get("total_count") != len(jobs.get("jobs", [])):
        raise ValueError("Incomplete preparation job evidence")
    for target in TARGETS:
        matches = [job for job in jobs["jobs"] if job.get("name") == f"Downloaded {target}"]
        if len(matches) != 1 or matches[0].get("conclusion") != "success":
            raise ValueError(f"Missing successful downloaded-byte verification: {target}")
    artifacts = gh_json("api", f"repos/{args.repository}/actions/runs/{run_id}/artifacts?per_page=100")
    if artifacts.get("total_count") != len(artifacts.get("artifacts", [])):
        raise ValueError("Incomplete verification artifact evidence")
    matches = [
        item for item in artifacts["artifacts"] if item.get("name") == VERIFICATION_ARTIFACT
    ]
    if len(matches) != 1:
        raise ValueError("Expected exactly one verified draft receipt")
    artifact = matches[0]
    if (
        artifact.get("expired") is not False
        or type(artifact.get("id")) is not int or artifact["id"] <= 0
        or type(artifact.get("size_in_bytes")) is not int
        or not 0 < artifact["size_in_bytes"] <= 65536
        or not isinstance(artifact.get("digest"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.get("digest", ""))
        or artifact.get("workflow_run", {}).get("id") != run_id
        or artifact.get("workflow_run", {}).get("head_sha") != args.commit
    ):
        raise ValueError("Invalid or expired verified draft receipt artifact")
    payload = subprocess.check_output(
        ["gh", "api", f"repos/{args.repository}/actions/artifacts/{artifact['id']}/zip"],
    )
    if (
        len(payload) != artifact["size_in_bytes"]
        or "sha256:" + hashlib.sha256(payload).hexdigest() != artifact["digest"]
    ):
        raise ValueError("Verified draft receipt transfer/hash mismatch")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entries = archive.infolist()
        if len(entries) != 1 or entries[0].filename != VERIFICATION_FILE or entries[0].file_size > 16384:
            raise ValueError("Invalid verified draft receipt archive")
        receipt = json.loads(archive.read(VERIFICATION_FILE))
    if not isinstance(receipt, dict) or not isinstance(receipt.get("manifest_sha"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", receipt.get("manifest_sha", ""),
    ):
        raise ValueError("Invalid verified draft manifest anchor")
    expected = {
        "schema": VERIFICATION_SCHEMA, "repository": args.repository,
        "version": args.version, "commit": args.commit, "release_id": args.release_id,
        "manifest_sha": receipt["manifest_sha"], "assets_fingerprint": args.assets_fingerprint,
        "workflow": PUBLIC_WORKFLOW, "run_id": run_id, "run_attempt": run["run_attempt"],
    }
    if receipt != expected:
        raise ValueError("Verified draft receipt identifies a different candidate or attempt")
    return receipt


def publish(args) -> None:
    check_repository(args.repository, public_release=args.public_release)
    check_candidate(args.repository, args.version, args.commit)
    if args.public_release:
        check_verified_receipt(args)
    inspect_draft(
        args.repository, args.release_id, args.version, args.commit, args.assets_fingerprint,
        public_release=args.public_release,
    )
    subprocess.run(
        [
            "gh", "api", "--method", "PATCH",
            f"repos/{args.repository}/releases/{args.release_id}", "-F", "draft=false",
            *(["-f", "make_latest=false"] if args.public_release else []),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    release = gh_json("api", f"repos/{args.repository}/releases/{args.release_id}")
    if release["draft"] is not False or (args.public_release and release.get("prerelease") is not True):
        raise ValueError("GitHub did not publish the verified draft")
    output_values(release_url=release["html_url"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("draft", "download", "record", "publish"))
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", REPOSITORY))
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--release-id", type=int)
    parser.add_argument("--manifest-sha")
    parser.add_argument("--assets-fingerprint")
    parser.add_argument("--target", choices=TARGETS)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--public-release", action="store_true")
    parser.add_argument("--verified-run-id", type=int)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    if args.command == "draft":
        if args.assets is None:
            parser.error("--assets is required")
        create_draft(
            args.repository, args.assets.resolve(), args.version, args.commit,
            public_release=args.public_release,
        )
    else:
        if args.release_id is None or args.assets_fingerprint is None:
            parser.error("--release-id and --assets-fingerprint are required")
        if args.command == "record":
            if args.receipt is None or args.manifest_sha is None:
                parser.error("--receipt and --manifest-sha are required")
            write_verification(args)
        elif args.command == "download":
            if args.manifest_sha is None or args.target is None or args.destination is None:
                parser.error("--manifest-sha, --target, and --destination are required")
            download_candidate(args)
        else:
            publish(args)


if __name__ == "__main__":
    main()
