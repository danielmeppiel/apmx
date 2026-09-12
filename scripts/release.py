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
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "linux-x86_64": "ubuntu-24.04",
    "linux-arm64": "ubuntu-24.04-arm",
    "macos-x86_64": "macos-15-intel",
    "macos-arm64": "macos-15",
    "windows-x86_64": "windows-2025",
}
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
BACKEND_PIN = ROOT / "src/apmx/apm-backend.json"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_backend_pin(path: Path = BACKEND_PIN) -> dict:
    pin = json.loads(path.read_text(encoding="utf-8"))
    if (
        pin.get("schema") != "apmx-apm-backend/1"
        or pin.get("repository") != "microsoft/apm"
        or not re.fullmatch(VERSION, pin.get("version", ""))
        or not re.fullmatch(r"[0-9a-f]{40}", pin.get("source_commit", ""))
        or set(pin.get("assets", {})) != set(TARGETS)
    ):
        raise ValueError("Invalid APM backend pin")
    for target, asset in pin["assets"].items():
        root = "apm-" + target.replace("macos-", "darwin-")
        windows = target.startswith("windows-")
        if (
            asset.get("root") != root
            or asset.get("archive") != root + (".zip" if windows else ".tar.gz")
            or asset.get("executable") != ("apm.exe" if windows else "apm")
            or not re.fullmatch(r"[0-9a-f]{64}", asset.get("sha256", ""))
            or not isinstance(asset.get("version_output"), str)
            or not asset["version_output"]
            or asset["version_output"] != asset["version_output"].strip()
            or any(ord(character) < 32 for character in asset["version_output"])
        ):
            raise ValueError(f"Invalid APM backend asset pin: {target}")
    return pin


def check_backend(backend: Path, pin: dict, target: str) -> Path:
    executable = backend / pin["assets"][target]["executable"]
    license_path = backend / f"_internal/apm_cli-{pin['version']}.dist-info/licenses/LICENSE"
    if not executable.is_file() or executable.is_symlink() or not license_path.is_file():
        raise ValueError("Missing APM backend executable, runtime or upstream license")
    with executable.open("rb") as stream:
        header = stream.read(4)
    valid_header = (
        header[:2] == b"MZ" if target.startswith("windows-")
        else header == b"\x7fELF" if target.startswith("linux-")
        else header in {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe"}
    )
    if not valid_header:
        raise ValueError("APM backend must be a native executable for the target")
    if os.name != "nt" and not target.startswith("windows-") and not executable.stat().st_mode & 0o111:
        raise ValueError("APM backend is not executable")
    for path in backend.rglob("*"):
        if not path.resolve().is_relative_to(backend.resolve()):
            raise ValueError("APM backend symlink escapes its runtime root")
        if not (path.is_file() or path.is_dir()):
            raise ValueError("Unsupported file in APM backend")
    return executable


def probe_backend(executable: Path, pin: dict, target: str) -> str:
    with tempfile.TemporaryDirectory(prefix="apmx-apm-version-") as temporary:
        system_keys = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "SYSTEMDRIVE"}
        env = {key: value for key, value in os.environ.items() if key.upper() in system_keys}
        env.update({
            key: temporary for key in (
                "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME",
                "XDG_DATA_HOME", "XDG_CACHE_HOME", "TMPDIR", "TMP", "TEMP",
            )
        })
        env.update({"NO_COLOR": "1", "TERM": "dumb", "PYINSTALLER_RESET_ENVIRONMENT": "1"})
        result = subprocess.run(
            [str(executable.resolve()), "--version"], cwd=temporary, env=env,
            capture_output=True, text=True, encoding="utf-8", timeout=30, check=True,
        )
    output = result.stdout.strip()
    if output != pin["assets"][target]["version_output"]:
        raise ValueError(f"APM backend version/source mismatch: {output!r}")
    return output


def backend_provenance(backend: Path, pin: dict, target: str) -> dict:
    executable = check_backend(backend, pin, target)
    asset = pin["assets"][target]
    return {
        "version": pin["version"], "source_commit": pin["source_commit"],
        "target": target, "archive": asset["archive"], "archive_sha256": asset["sha256"],
        "executable": f"libexec/apm/{asset['executable']}",
        "executable_sha256": digest(executable),
    }


def provision_backend(target: str, destination: Path) -> dict:
    """Download verified official bytes, preserving the complete upstream onedir."""
    if destination.exists():
        raise ValueError("APM provisioning requires a new destination")
    pin = read_backend_pin()
    asset = pin["assets"][target]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".apm-download-", dir=destination.parent) as temporary:
        work = Path(temporary)
        archive = work / asset["archive"]
        url = (
            f"https://github.com/{pin['repository']}/releases/download/"
            f"v{pin['version']}/{asset['archive']}"
        )
        request = Request(url, headers={"User-Agent": "apmx-pinned-backend-build"})
        with urlopen(request, timeout=60) as response, archive.open("xb") as stream:
            if urlsplit(response.geturl()).scheme != "https":
                raise ValueError("APM backend download redirected away from HTTPS")
            size = 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 256 * 1024**2:
                    raise ValueError("APM backend download exceeds size limit")
                stream.write(chunk)
        if digest(archive) != asset["sha256"]:
            raise ValueError("APM backend archive checksum mismatch")
        extracted = _extract_payload(archive, work / "extracted", target, asset["root"])
        executable = check_backend(extracted, pin, target)
        if native_target() == target:
            probe_backend(executable, pin, target)
        provenance = backend_provenance(extracted, pin, target)
        extracted.rename(destination)
    return provenance


def check_backend_metadata(bundle: Path, target: str) -> dict:
    pin_path = bundle / "apm-backend.json"
    pin = read_backend_pin(pin_path)
    metadata = json.loads((bundle / "RELEASE.json").read_text(encoding="utf-8"))
    if (
        metadata.get("target") != target
        or metadata.get("apm_backend_pin_sha256") != digest(pin_path)
        or metadata.get("apm_backend") != backend_provenance(bundle / "libexec/apm", pin, target)
    ):
        raise ValueError("Bundled APM backend provenance/hash mismatch")
    packaged_pin = bundle / "_internal/apmx/apm-backend.json"
    if not packaged_pin.is_file() or packaged_pin.read_bytes() != pin_path.read_bytes():
        raise ValueError("Runtime APM backend pin differs from release pin")
    return pin


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
    check_backend_metadata(bundle, target)


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


def _validate_members(
    entries: list[tuple[str, str | None]], target: str, root: str | None = None,
) -> None:
    paths: set[PurePosixPath] = set()
    folded: set[str] = set()
    links: set[PurePosixPath] = set()
    root = root or f"apmx-{target}"
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
    target = next(
        (name for name in TARGETS if archive.name.endswith(name + (".zip" if name.startswith("windows") else ".tar.gz"))),
        None,
    )
    bundle = _extract_payload(archive, destination, target)
    check_bundle(bundle, bundle.name.removeprefix("apmx-"))
    return bundle


def _extract_payload(
    archive: Path, destination: Path, target: str | None, root: str | None = None,
) -> Path:
    if destination.exists():
        raise ValueError("Extraction requires a new destination")
    # Tests and manual inspection may use an arbitrary archive filename.
    with zipfile.ZipFile(archive) if archive.suffix == ".zip" else tarfile.open(archive, "r:gz") as stream:
        if isinstance(stream, zipfile.ZipFile):
            members = stream.infolist()
            if any(stat.S_ISLNK(member.external_attr >> 16) for member in members):
                raise ValueError("Zip symlinks are not supported")
            if any(
                stat.S_IFMT(member.external_attr >> 16) not in (0, stat.S_IFREG, stat.S_IFDIR)
                for member in members
            ):
                raise ValueError("Zip special files are not supported")
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
        root = root or f"apmx-{target}"
        _validate_members(entries, target, root)
        destination.mkdir(parents=True)
        if isinstance(stream, zipfile.ZipFile):
            stream.extractall(destination)
        else:
            stream.extractall(destination, filter="data")
    return destination / root


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
    provenance = provision_backend(target, bundle / "libexec/apm")
    shutil.copyfile(BACKEND_PIN, bundle / "apm-backend.json")
    (bundle / "RELEASE.json").write_text(
        json.dumps({
            "version": version, "target": target,
            "publisher_signed": False, "apple_notarized": False,
            "external_prerequisites": ["Git", "Copilot CLI", "contract-declared checker tools"],
            "test_actor": "hermetic Copilot JSONL protocol fixture, not live inference",
            "license_inventory": "LICENSES/manifest.json",
            "apm_backend_pin_sha256": digest(BACKEND_PIN),
            "apm_backend": provenance,
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
    provision = commands.add_parser("provision-apm")
    provision.add_argument("--target", choices=TARGETS, required=True)
    provision.add_argument("--output", type=Path, required=True)
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
        read_backend_pin()
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
    elif args.command == "provision-apm":
        output = args.output.resolve()
        provenance = provision_backend(args.target, output)
        output_values(backend=str(output / Path(provenance["executable"]).name))
    elif args.command == "manifest":
        output_values(manifest_sha=digest(make_manifest(args.assets, args.version, args.commit)))
    else:
        verify_archive(args.archive)
        output_values(bundle=str(extract_archive(args.archive, args.destination)))


if __name__ == "__main__":
    main()
