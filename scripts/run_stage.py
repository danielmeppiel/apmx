"""Stage a factory for .github/workflows/run.yml (produce and check jobs).

Standard library only: run.yml also runs with the native apmx release, which
has no importable apmx package.

    stage         Copy what apmx runs out of the caller's Git checkout. A local
                  factory directory is copied whole; for a package factory
                  (an APM package reference), the caller's workspace is copied
                  and apmx runs the package against it with --from.
    requirements  Install the factory's checks-requirements file into a fresh
                  virtualenv and put its python3 first on PATH.
    verify        Require the installed requirements file to be the one the
                  receipt's factory definition records.

apmx refuses native runs inside a Git repository with remotes (fail-closed
no-policy prerequisite), so the factory or workspace runs from a plain copy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

_GITHUB = re.compile(
    r"^(?:https://)?(?:github\.com/)?(?P<owner>[A-Za-z0-9-]+)/(?P<repo>[A-Za-z0-9._-]+?)"
    r"(?:\.git)?(?P<path>(?:/[A-Za-z0-9._-]+)*)(?:#(?P<ref>[A-Za-z0-9._/-]+))?$"
)


class StageError(Exception):
    pass


def _inside(root: Path, relative: str, what: str) -> Path:
    parts = PurePosixPath(relative).parts
    if PurePosixPath(relative).is_absolute() or ".." in parts:
        raise StageError(f"{what} must be a relative path inside the factory: {relative}")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise StageError(f"{what} must be a relative path inside the factory: {relative}")
    return path


def _env(**values: str) -> None:
    with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as stream:
        for key, value in values.items():
            if "\n" in value:
                raise StageError(f"{key} contains a newline")
            stream.write(f"{key}={value}\n")


def stage(caller: Path, factory: str, workspace: str, temp: Path) -> dict[str, str]:
    """Copy the factory (local) or the workspace (package) and describe the run."""
    ignore = shutil.ignore_patterns(".git", ".apm")
    local = _inside(caller, factory, "factory") if factory not in ("", ".") else caller.resolve()
    if local.is_dir():
        root = temp / "factory"
        shutil.copytree(local, root, ignore=ignore)
        return {"APMX_RUN_ROOT": str(root), "APMX_FACTORY_REF": "", "APMX_DELIVERY": factory}
    if "#" not in factory:
        raise StageError(
            f"factory is not a directory in this repository: {factory}. A package factory "
            "must pin its ref (owner/repo[/path]#tag-or-commit)."
        )
    source = _inside(caller, workspace, "workspace") if workspace not in ("", ".") else caller
    if not source.is_dir():
        raise StageError(f"workspace is not a directory in this repository: {workspace}")
    root = temp / "workspace"
    shutil.copytree(source, root, ignore=ignore)
    return {"APMX_RUN_ROOT": str(root), "APMX_FACTORY_REF": factory, "APMX_DELIVERY": workspace}


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.DEVNULL)


def package_file(ref: str, relative: str, scratch: Path) -> Path:
    """Fetch one file of a GitHub package at its ref (the ref apmx resolves too)."""
    match = _GITHUB.match(ref)
    if not match:
        raise StageError(
            "checks-requirements with a package factory needs a GitHub package reference "
            f"(owner/repo[/path][#ref]): {ref}"
        )
    clone = scratch / "package"
    clone.mkdir(parents=True)
    _git("init", "--quiet", cwd=clone)
    url = f"https://github.com/{match['owner']}/{match['repo']}.git"
    _git("fetch", "--quiet", "--depth", "1", url, match["ref"] or "HEAD", cwd=clone)
    _git("checkout", "--quiet", "FETCH_HEAD", cwd=clone)
    package = clone / match["path"].lstrip("/") if match["path"] else clone
    return _inside(package, relative, "checks-requirements")


def requirements(root: Path, ref: str, relative: str, temp: Path) -> Path:
    """Install the requirements file into a fresh venv; return the file installed."""
    if ref:
        target = temp / "checks-requirements.txt"
        with tempfile.TemporaryDirectory(dir=temp) as scratch:
            source = package_file(ref, relative, Path(scratch))
            if not source.is_file():
                raise StageError(f"checks-requirements is not a file in {ref}: {relative}")
            shutil.copyfile(source, target)
    else:
        target = _inside(root, relative, "checks-requirements")
        if not target.is_file():
            raise StageError(f"checks-requirements is not a file inside the factory: {relative}")
    venv = temp / "checks-python"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    args = ["--disable-pip-version-check", "--no-input", "--quiet", "-r", str(target)]
    if "--hash=" in target.read_text(encoding="utf-8"):
        args.append("--require-hashes")
    subprocess.run([str(venv / "bin/python"), "-m", "pip", "install", *args], check=True)
    with open(os.environ["GITHUB_PATH"], "a", encoding="utf-8") as stream:
        stream.write(f"{venv / 'bin'}\n")
    return target


def verify(receipt: Path, relative: str, installed: Path) -> str:
    """The installed file must be the checker resource the definition records."""
    definition = json.loads((receipt / "definition.json").read_bytes())
    recorded = {
        resource["sha256"]
        for stage_ in definition["stages"]
        for resource in stage_["resources"]
        if resource["relative_path"] == PurePosixPath(relative).as_posix()
    }
    actual = hashlib.sha256(installed.read_bytes()).hexdigest()
    if not recorded:
        raise StageError(
            f"checks-requirements {relative} is not a checker resource of this factory "
            "definition (resources are the files under checks/)"
        )
    if recorded != {actual}:
        raise StageError(
            f"checks-requirements {relative} (sha256:{actual[:12]}..) is not the file the "
            "factory definition records; the package ref moved during the run"
        )
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    staging = commands.add_parser("stage")
    staging.add_argument("--caller", type=Path, required=True)
    staging.add_argument("--factory", required=True)
    staging.add_argument("--workspace", default=".")
    installing = commands.add_parser("requirements")
    installing.add_argument("--file", required=True)
    checking = commands.add_parser("verify")
    checking.add_argument("--receipt", type=Path, required=True)
    checking.add_argument("--file", required=True)
    args = parser.parse_args(argv)
    temp = Path(os.environ["RUNNER_TEMP"])
    try:
        if args.command == "stage":
            values = stage(args.caller, args.factory, args.workspace, temp)
            _env(**values)
            kind = (
                "package " + values["APMX_FACTORY_REF"] if values["APMX_FACTORY_REF"] else "local"
            )
            print(f"Factory: {kind}; running in {values['APMX_RUN_ROOT']}")
        elif args.command == "requirements":
            root = Path(os.environ["APMX_RUN_ROOT"])
            installed = requirements(root, os.environ.get("APMX_FACTORY_REF", ""), args.file, temp)
            _env(APMX_CHECKS_REQUIREMENTS=str(installed))
            print(f"Installed {args.file} into a fresh virtualenv for the checks")
        else:
            digest = verify(args.receipt, args.file, Path(os.environ["APMX_CHECKS_REQUIREMENTS"]))
            print(f"{args.file} sha256:{digest[:12]}.. is the factory definition's own file")
    except (StageError, subprocess.CalledProcessError, OSError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
