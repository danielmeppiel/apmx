"""Build and verify standalone archives using one platform-independent boundary."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import tarfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "linux-x86_64": "ubuntu-24.04",
    "linux-arm64": "ubuntu-24.04-arm",
    "macos-x86_64": "macos-15-intel",
    "macos-arm64": "macos-15",
    "windows-x86_64": "windows-2025",
}
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_project(root: Path, tag: str | None = None) -> str:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    if project["name"] != "apmx" or not re.fullmatch(VERSION, version):
        raise ValueError("Expected standalone apmx with a stable major.minor.patch version")
    if project.get("scripts", {}).get("apmx") != "apmx.cli:main":
        raise ValueError("Expected apmx.cli:main console entrypoint")
    extras = project.get("optional-dependencies", {})
    if not extras.get("dev") or not any(
        re.match(r"pyinstaller(?:[<=>!~;\[\s]|$)", dep, re.IGNORECASE)
        for dep in extras.get("build", [])
    ):
        raise ValueError("Expected dev and PyInstaller build extras")
    if tag is not None and tag != f"v{version}":
        raise ValueError("Release tag must exactly match the project version")
    return version


def validate_lock(path: Path) -> None:
    packages = tomllib.loads(path.read_text(encoding="utf-8"))["package"]
    if not packages:
        raise ValueError("Empty dependency lock")
    for package in packages:
        source = package["source"]
        local = package["name"] == "apmx" and source == {"editable": "."}
        if not local and source != {"registry": "https://pypi.org/simple"}:
            raise ValueError(f"Non-public dependency source for {package['name']}")
        for item in [*package.get("wheels", []), *([package["sdist"]] if "sdist" in package else [])]:
            url = urlsplit(item["url"])
            if (
                url.scheme != "https"
                or url.netloc != "files.pythonhosted.org"
                or url.username
                or url.password
                or url.query
            ):
                raise ValueError(f"Non-public distribution URL for {package['name']}")


def archive_name(version: str, target: str) -> str:
    if not re.fullmatch(VERSION, version) or target not in TARGETS:
        raise ValueError("Invalid release version or target")
    suffix = ".zip" if target.startswith("windows-") else ".tar.gz"
    return f"apmx-{version}-{target}{suffix}"


def check_bundle(bundle: Path, target: str) -> None:
    executable = "apmx.exe" if target.startswith("windows-") else "apmx"
    for name in (executable, "LICENSE", "NOTICE", "LICENSES/Python-LICENSE.txt", "LICENSES/manifest.json"):
        if not (bundle / name).is_file():
            raise ValueError(f"Missing archive file: {name}")
    if not (bundle / "_internal").is_dir() or not any(
        path.is_file() for path in (bundle / "_internal").rglob("*")
    ):
        raise ValueError("Missing bundled runtime")
    for path in bundle.rglob("*"):
        if not path.resolve().is_relative_to(bundle.resolve()):
            raise ValueError("Bundle symlink escapes archive root")
        if not (path.is_file() or path.is_dir()):
            raise ValueError("Unsupported file in bundle")


def archive_bundle(bundle: Path, output: Path, version: str, target: str) -> Path:
    if bundle.name != f"apmx-{target}":
        raise ValueError("Unexpected bundle directory name")
    check_bundle(bundle, target)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / archive_name(version, target)
    if archive.exists():
        raise ValueError(f"Refusing to overwrite existing archive: {archive}")
    paths = [bundle, *sorted(bundle.rglob("*"))]
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as stream:
            for path in paths:
                if path.is_symlink():
                    raise ValueError("Windows archives must not contain symlinks")
                stream.write(path, path.relative_to(bundle.parent).as_posix())
    else:
        with tarfile.open(archive, "w:gz", dereference=False) as stream:
            for path in paths:
                # Represent every regular file independently, never as a tar hardlink.
                stream.inodes.clear()
                stream.add(path, arcname=path.relative_to(bundle.parent).as_posix(), recursive=False)
    archive.with_name(archive.name + ".sha256").write_text(
        f"{digest(archive)}  {archive.name}\n", encoding="ascii"
    )
    return archive


def verify_archive(archive: Path, expected: str | None = None) -> None:
    actual = digest(archive)
    checksum = archive.with_name(archive.name + ".sha256").read_text(encoding="ascii")
    if checksum != f"{actual}  {archive.name}\n" or (expected is not None and actual != expected):
        raise ValueError(f"Release checksum mismatch: {archive.name}")


def _member_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or ":" in name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in name.rstrip("/").split("/"))
    ):
        raise ValueError(f"Unsafe archive path: {name!r}")
    return path


def _validate_members(entries: list[tuple[str, str | None]], target: str) -> None:
    paths: set[PurePosixPath] = set()
    folded: set[str] = set()
    links: set[PurePosixPath] = set()
    root = f"apmx-{target}"
    for name, link in entries:
        path = _member_path(name)
        if path.parts[0] != root or path in paths:
            raise ValueError("Unexpected archive root or duplicate member")
        if target.startswith("windows") and str(path).casefold() in folded:
            raise ValueError("Case-colliding Windows archive members")
        if target.startswith("windows") and any(
            part != part.rstrip(" .")
            or re.fullmatch(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part, re.IGNORECASE)
            or any(ord(character) < 32 or character in '<>"|?*' for character in part)
            for part in path.parts
        ):
            raise ValueError("Unsafe Windows archive member")
        paths.add(path)
        folded.add(str(path).casefold())
        if link is not None:
            if not link or "\\" in link or ":" in link or PurePosixPath(link).is_absolute():
                raise ValueError("Unsafe archive symlink")
            parts = list(path.parent.parts)
            for part in PurePosixPath(link).parts:
                if part == "..":
                    if len(parts) <= 1:
                        raise ValueError("Archive symlink escapes root")
                    parts.pop()
                elif part != ".":
                    parts.append(part)
            links.add(path)
    if any(parent in links for path in paths for parent in path.parents):
        raise ValueError("Archive member traverses another member's symlink")


def extract_archive(archive: Path, destination: Path) -> Path:
    if destination.exists():
        raise ValueError("Extraction requires a new destination")
    target = next(
        (name for name in TARGETS if archive.name.endswith(name + (".zip" if name.startswith("windows") else ".tar.gz"))),
        None,
    )
    # Tests and manual inspection may use an arbitrary archive filename.
    with zipfile.ZipFile(archive) if archive.suffix == ".zip" else tarfile.open(archive, "r:gz") as stream:
        if isinstance(stream, zipfile.ZipFile):
            members = stream.infolist()
            if any(stat.S_ISLNK(member.external_attr >> 16) for member in members):
                raise ValueError("Zip symlinks are not supported")
            entries = [(member.filename, None) for member in members]
            expanded = sum(member.file_size for member in members)
        else:
            members = stream.getmembers()
            if any(not (member.isfile() or member.isdir() or member.issym()) for member in members):
                raise ValueError("Special files and hardlinks are not supported")
            entries = [(member.name, member.linkname if member.issym() else None) for member in members]
            expanded = sum(member.size for member in members)
        if not entries or len(entries) > 100_000 or expanded > 2 * 1024**3:
            raise ValueError("Archive exceeds extraction limits or is empty")
        if target is None:
            root = _member_path(entries[0][0]).parts[0]
            target = root.removeprefix("apmx-")
            if target not in TARGETS:
                raise ValueError("Unknown archive target")
        _validate_members(entries, target)
        destination.mkdir(parents=True)
        if isinstance(stream, zipfile.ZipFile):
            stream.extractall(destination)
        else:
            stream.extractall(destination, filter="data")
    bundle = destination / f"apmx-{target}"
    check_bundle(bundle, target)
    return bundle


def make_manifest(assets: Path, version: str, commit: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Expected full candidate commit SHA")
    expected = {archive_name(version, target) for target in TARGETS}
    actual = {path.name for path in assets.iterdir()}
    allowed = expected | {name + ".sha256" for name in expected}
    if actual - {"release-manifest.json"} != allowed:
        raise ValueError("Release assets must contain exactly five archives and checksums")
    hashes = {}
    for name in sorted(expected):
        verify_archive(assets / name)
        hashes[name] = digest(assets / name)
    path = assets / "release-manifest.json"
    path.write_text(
        json.dumps(
            {"schema": "apmx-release/1", "version": version, "commit": commit, "archives": hashes},
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    return path


def native_target() -> str:
    system = {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}[platform.system()]
    machine = platform.machine().lower()
    arch = {"amd64": "x86_64", "x86_64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}[machine]
    return f"{system}-{arch}"


def collect_licenses(bundle: Path) -> None:
    """Preserve installed notices; inventory the build-environment superset honestly."""
    destination = bundle / "LICENSES"
    destination.mkdir()
    prefix = Path(sys.base_prefix)
    python_license = next(
        (
            path for path in (
                prefix / "LICENSE.txt",
                Path(sysconfig.get_path("stdlib")) / "LICENSE.txt",
                prefix / "LICENSE",
            )
            if path.is_file()
        ),
        None,
    )
    if python_license is None:
        raise ValueError("The build interpreter's Python license could not be found")
    shutil.copyfile(python_license, destination / "Python-LICENSE.txt")
    runtime_notices = []
    for directory in (prefix / "licenses", prefix / "share/licenses"):
        if directory.is_dir():
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    relative = Path("python-runtime") / directory.relative_to(prefix) / path.relative_to(directory)
                    target = destination / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)
                    runtime_notices.append(relative.as_posix())
    distributions = []
    for distribution in sorted(
        importlib.metadata.distributions(), key=lambda item: item.metadata["Name"].lower(),
    ):
        name = distribution.metadata["Name"]
        normalized = re.sub(r"[-_.]+", "-", name).lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", normalized):
            raise ValueError(f"Unsafe installed distribution name: {name}")
        copied = []
        for source in distribution.files or []:
            source_path = Path(str(source))
            if not (
                source_path.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "COPYRIGHT", "NOTICE"))
                or any(part.lower() in {"licenses", "licences"} for part in source_path.parts)
            ):
                continue
            installed = Path(distribution.locate_file(source))
            if not installed.is_file():
                raise ValueError(f"Declared installed license is missing: {name}/{source}")
            relative = Path(normalized) / f"{len(copied):03d}-{source_path.name}"
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(installed, target)
            copied.append(relative.as_posix())
        metadata_license = distribution.metadata.get("License-Expression") or distribution.metadata.get("License")
        status = "files-copied" if copied else "metadata-only" if metadata_license else "not-provided"
        if status == "not-provided":
            print(f"License notice not provided by installed distribution: {name}", file=sys.stderr)
        distributions.append({
            "name": name, "version": distribution.version, "files": copied,
            "license_metadata": metadata_license, "status": status,
        })
    (destination / "manifest.json").write_text(
        json.dumps({
            "scope": "Installed build-environment distributions; a superset, not an exact bundled dependency audit",
            "python_version": platform.python_version(),
            "python_license": "Python-LICENSE.txt",
            "additional_runtime_notices": runtime_notices,
            "distributions": distributions,
        }, indent=2) + "\n",
        encoding="utf-8",
    )


def build(target: str, output: Path) -> Path:
    if native_target() != target:
        raise ValueError("Build target must match the native runner architecture")
    version = read_project(ROOT)
    validate_lock(ROOT / "uv.lock")
    bundle = output / f"apmx-{target}"
    if bundle.exists():
        raise ValueError("Build destination already exists; use a fresh output directory")
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--distpath", str(output), "--workpath", str(output / "work"),
            str(ROOT / "build/apmx.spec"),
        ],
        cwd=ROOT,
        check=True,
    )
    (output / "apmx").rename(bundle)
    for name in ("LICENSE", "NOTICE"):
        shutil.copyfile(ROOT / name, bundle / name)
    collect_licenses(bundle)
    (bundle / "RELEASE.json").write_text(
        json.dumps({
            "version": version, "target": target,
            "publisher_signed": False, "apple_notarized": False,
            "external_prerequisites": ["Git", "Copilot CLI", "contract-declared checker tools"],
            "test_actor": "hermetic Copilot JSONL protocol fixture, not live inference",
            "license_inventory": "LICENSES/manifest.json",
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    return archive_bundle(bundle, output / "assets", version, target)


def output_values(**values: str) -> None:
    for key, value in values.items():
        print(f"{key}={value}")
    if os.environ.get("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
            for key, value in values.items():
                if "\n" in value or "\r" in value:
                    raise ValueError("Invalid multiline workflow output")
                stream.write(f"{key}={value}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--tag")
    builder = commands.add_parser("build")
    builder.add_argument("--target", choices=TARGETS, required=True)
    builder.add_argument("--output", type=Path, default=ROOT / "dist")
    manifest = commands.add_parser("manifest")
    manifest.add_argument("--assets", type=Path, required=True)
    manifest.add_argument("--version", required=True)
    manifest.add_argument("--commit", required=True)
    extract = commands.add_parser("extract")
    extract.add_argument("--archive", type=Path, required=True)
    extract.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "validate":
        version = read_project(ROOT, args.tag)
        validate_lock(ROOT / "uv.lock")
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        if args.tag:
            tagged = subprocess.check_output(
                ["git", "rev-parse", "--verify", f"refs/tags/{args.tag}^{{commit}}"],
                cwd=ROOT, text=True,
            ).strip()
            if tagged != sha:
                raise ValueError("Candidate HEAD does not match the release tag")
        matrix = json.dumps({"include": [{"target": key, "os": value} for key, value in TARGETS.items()]})
        output_values(version=version, sha=sha, matrix=matrix)
    elif args.command == "build":
        archive = build(args.target, args.output.resolve())
        output_values(archive=str(archive))
    elif args.command == "manifest":
        output_values(manifest_sha=digest(make_manifest(args.assets, args.version, args.commit)))
    else:
        verify_archive(args.archive)
        output_values(bundle=str(extract_archive(args.archive, args.destination)))


if __name__ == "__main__":
    main()
