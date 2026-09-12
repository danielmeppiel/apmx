"""Private draft creation, exact-asset download, and gated publication.

Only workflow jobs explicitly holding contents:write may create/publish. Failed
verification leaves a draft for inspection; this helper never deletes or reuses
an existing release. SHA-256 checks are integrity checks, not publisher signing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
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


def gh_json(*args: str):
    return json.loads(subprocess.check_output(["gh", *args], text=True, encoding="utf-8"))


def check_repository(repository: str) -> None:
    if repository != REPOSITORY:
        raise ValueError("Release promotion is restricted to the standalone private repository")
    metadata = gh_json("api", f"repos/{repository}")
    if metadata["private"] is not True:
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
    if len(assets) != 11 or {asset["name"] for asset in assets} != expected_names(version):
        raise ValueError("Draft assets differ from the exact five-platform candidate set")
    if any(asset["state"] != "uploaded" or asset["size"] <= 0 for asset in assets):
        raise ValueError("Draft asset upload is incomplete")
    if expected_assets and asset_fingerprint(assets) != expected_assets:
        raise ValueError("Draft assets were replaced or changed after candidate creation")
    return release, assets


def create_draft(repository: str, assets: Path, version: str, commit: str) -> None:
    check_repository(repository)
    check_candidate(repository, version, commit)
    manifest = make_manifest(assets, version, commit)
    notes = (
        f"Standalone apmx {version}; candidate commit `{commit}`.\n\n"
        "Five native onedir archives include the runtime, LICENSE, NOTICE, and release metadata. "
        "Extract the complete archive; do not move the executable out of its runtime directory.\n\n"
        "Git, Copilot CLI, and contract-declared checker tools are external prerequisites. "
        "The CI actor is an explicitly hermetic Copilot JSONL protocol fixture, NOT live inference. "
        "Passing native checks yield UNPROVEN (21), rejection 20, operational halt 22.\n\n"
        "No trusted publisher signature or Apple notarization is provided. macOS may carry "
        "PyInstaller's local ad-hoc signature; it is not publisher identity. "
        "Checksums prove byte integrity, not publisher authentication. "
        "Do not disable platform security to run these binaries.\n"
    )
    with tempfile.TemporaryDirectory(prefix="apmx-release-notes-") as temporary:
        path = Path(temporary) / "notes.txt"
        path.write_text(notes, encoding="utf-8")
        subprocess.run(
            [
                "gh", "release", "create", f"v{version}", "--repo", repository,
                "--draft", "--verify-tag", "--target", commit, "--title", f"apmx {version}",
                "--notes-file", str(path),
                *[str(assets / name) for name in sorted(expected_names(version))],
            ],
            check=True,
        )
    drafts = gh_json("api", "--paginate", "--slurp", f"repos/{repository}/releases?per_page=100")
    matches = [item for page in drafts for item in page if item["tag_name"] == f"v{version}"]
    if len(matches) != 1:
        raise ValueError("Could not identify the newly created draft release")
    release_id = matches[0]["id"]
    _, uploaded = inspect_draft(repository, release_id, version, commit)
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
    check_repository(args.repository)
    check_candidate(args.repository, args.version, args.commit)
    _, assets = inspect_draft(
        args.repository, args.release_id, args.version, args.commit, args.assets_fingerprint,
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
    inspect_draft(args.repository, args.release_id, args.version, args.commit, args.assets_fingerprint)
    output_values(bundle=str(bundle), archive_sha=digest(archive))


def publish(args) -> None:
    check_repository(args.repository)
    check_candidate(args.repository, args.version, args.commit)
    inspect_draft(args.repository, args.release_id, args.version, args.commit, args.assets_fingerprint)
    # The workflow's needs graph supplies all five successful downloaded-asset checks.
    subprocess.run(
        [
            "gh", "api", "--method", "PATCH",
            f"repos/{args.repository}/releases/{args.release_id}", "-F", "draft=false",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    release = gh_json("api", f"repos/{args.repository}/releases/{args.release_id}")
    if release["draft"] is not False:
        raise ValueError("GitHub did not publish the verified draft")
    output_values(release_url=release["html_url"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("draft", "download", "publish"))
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", REPOSITORY))
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--release-id", type=int)
    parser.add_argument("--manifest-sha")
    parser.add_argument("--assets-fingerprint")
    parser.add_argument("--target", choices=TARGETS)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    if args.command == "draft":
        if args.assets is None:
            parser.error("--assets is required")
        create_draft(args.repository, args.assets.resolve(), args.version, args.commit)
    else:
        if args.release_id is None or args.assets_fingerprint is None:
            parser.error("--release-id and --assets-fingerprint are required")
        if args.command == "download":
            if args.manifest_sha is None or args.target is None or args.destination is None:
                parser.error("--manifest-sha, --target, and --destination are required")
            download_candidate(args)
        else:
            publish(args)


if __name__ == "__main__":
    main()
