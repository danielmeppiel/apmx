"""Install a native apmx release archive for GitHub Actions, verified by SHA-256.

Downloads ``apmx-<version>-<target>`` plus its ``.sha256`` file and the
release's ``release-manifest.json``. The archive must hash to the same digest
in both, and to the ``sha256`` input when one is pinned. It is extracted with
path checks into the runner tool cache and put on ``PATH``. Standard library
only; runs on every GitHub-hosted runner's Python.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

VERSION = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?")
REPOSITORY = "danielmeppiel/apmx"
MAX_BYTES = 512 * 1024 * 1024


class SetupError(RuntimeError):
    pass


def target(system: str | None = None, machine: str | None = None) -> str:
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    arch = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(
        machine
    )
    name = {"linux": "linux", "darwin": "macos", "windows": "windows"}.get(system)
    if name is None or arch is None or (name == "windows" and arch != "x86_64"):
        raise SetupError(f"apmx has no release archive for {system}/{machine}")
    return f"{name}-{arch}"


def archive_name(version: str, target_name: str) -> str:
    suffix = ".zip" if target_name.startswith("windows-") else ".tar.gz"
    return f"apmx-{version}-{target_name}{suffix}"


def _fetch(source: str, name: str, destination: Path, token: str | None) -> Path:
    path = destination / name
    if "://" not in source:
        shutil.copyfile(Path(source) / name, path)
        return path
    headers = {"User-Agent": "apmx-setup"}
    if token and source.startswith("https://github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"{source}/{name}", headers=headers)
    with urllib.request.urlopen(request, timeout=120) as response, path.open("wb") as stream:
        total = 0
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_BYTES:
                raise SetupError(f"{name} is larger than {MAX_BYTES} bytes")
            stream.write(chunk)
    return path


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify(archive: Path, checksum: Path, manifest: Path, version: str, pinned: str) -> str:
    """The archive digest must agree with the checksum file, the manifest and any pin."""
    actual = _digest(archive)
    if checksum.read_text(encoding="ascii") != f"{actual}  {archive.name}\n":
        raise SetupError(f"{archive.name}: SHA-256 does not match {checksum.name}")
    release = json.loads(manifest.read_text(encoding="utf-8"))
    if release.get("schema") != "apmx-release/1" or release.get("version") != version:
        raise SetupError("release-manifest.json does not describe this version")
    if (release.get("archives") or {}).get(archive.name) != actual:
        raise SetupError(f"{archive.name}: SHA-256 does not match release-manifest.json")
    if pinned and pinned.lower() != actual:
        raise SetupError(f"{archive.name}: SHA-256 {actual} does not match the pinned sha256")
    return actual


def _safe(name: str, root: str) -> None:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != root:
        raise SetupError(f"refusing archive member outside {root}/: {name}")


def extract(archive: Path, destination: Path, target_name: str) -> Path:
    root = f"apmx-{target_name}"
    destination.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as stream:
            for member in stream.namelist():
                _safe(member, root)
            stream.extractall(destination)
    else:
        with tarfile.open(archive, "r:gz") as stream:
            for member in stream.getmembers():
                _safe(member.name, root)
                if not (member.isfile() or member.isdir() or member.issym()):
                    raise SetupError(f"refusing non-regular archive member: {member.name}")
            # The "data" filter refuses links that resolve outside the destination.
            stream.extractall(destination, filter="data")
    bundle = destination / root
    executable = bundle / ("apmx.exe" if target_name.startswith("windows-") else "apmx")
    if not executable.is_file():
        raise SetupError(f"{archive.name} has no {executable.name}")
    return bundle


def install(
    version: str,
    *,
    source: str,
    work: Path,
    tools: Path,
    pinned: str = "",
    token: str | None = None,
    target_name: str | None = None,
) -> tuple[Path, str]:
    version = version.removeprefix("v")
    if not VERSION.fullmatch(version):
        raise SetupError(f"invalid apmx version {version!r}")
    if pinned and not re.fullmatch(r"[0-9a-fA-F]{64}", pinned):
        raise SetupError("sha256 must be 64 hex characters")
    target_name = target_name or target()
    name = archive_name(version, target_name)
    work.mkdir(parents=True, exist_ok=True)
    archive = _fetch(source, name, work, token)
    checksum = _fetch(source, name + ".sha256", work, token)
    manifest = _fetch(source, "release-manifest.json", work, token)
    digest = verify(archive, checksum, manifest, version, pinned)
    destination = tools / "apmx" / version / target_name
    if destination.exists():
        shutil.rmtree(destination)
    return extract(archive, destination, target_name), digest


def main() -> int:
    version = os.environ["INPUT_VERSION"].strip()
    source = os.environ.get("APMX_SETUP_SOURCE") or (
        f"https://github.com/{REPOSITORY}/releases/download/v{version.removeprefix('v')}"
    )
    runner_temp = Path(os.environ.get("RUNNER_TEMP") or ".")
    temp = runner_temp / "apmx-setup"
    tools = Path(os.environ.get("RUNNER_TOOL_CACHE") or runner_temp / "apmx-tools")
    try:
        bundle, digest = install(
            version,
            source=source,
            work=temp,
            tools=tools,
            pinned=os.environ.get("INPUT_SHA256", "").strip(),
            token=os.environ.get("INPUT_TOKEN") or None,
        )
        executable = bundle / ("apmx.exe" if os.name == "nt" else "apmx")
        shown = subprocess.run(
            [str(executable), "--version"], capture_output=True, text=True, check=False
        ).stdout
        if version.removeprefix("v") not in shown:
            raise SetupError(f"installed apmx reports {shown.strip()!r}, not {version}")
    except (SetupError, OSError, ValueError, KeyError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(f"::error title=apmx setup::{exc}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    with open(os.environ["GITHUB_PATH"], "a", encoding="utf-8") as stream:
        stream.write(f"{bundle}\n")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        stream.write(f"path={bundle}\nsha256={digest}\nversion={version.removeprefix('v')}\n")
    print(f"[+] apmx {version} installed at {bundle} (sha256 {digest})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
