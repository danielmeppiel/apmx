"""Exercise frozen local/package jobs outside the checkout, without app imports.

Only Copilot's protocol is simulated. The binary, workspace capture, independent
checker process, retained artifact, record, reduction, and cleanup are real.
Python is an explicit test/checker prerequisite, not the app's runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path, PurePath

if __package__:
    from . import release
else:
    import release

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ROOT = FIXTURES.parents[1]
PRIVATE_MARKERS = ("PRIVATE_REASONING_SENTINEL", "PRIVATE_TOOL_SENTINEL")
PROFILE_DIRECTORIES = ("home", "config", "data", "cache", "appdata", "localappdata", "copilot")
APM_UPDATE_CACHE_FILES = {
    "nt": "home/AppData/Local/apm/cache/last_version_check",
    "posix": "home/.cache/apm/last_version_check",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_local_identity(identity: str, source: Path, *, consumer_lock: bool = False) -> None:
    if not consumer_lock:
        require(identity.startswith("local:"), "Local import lacks canonical APM identity")
    path = Path(identity if consumer_lock else identity.removeprefix("local:"))
    require(
        path.is_absolute() and path.resolve() == source.resolve(),
        "Wrong original local import source identity",
    )


def redact_trace_message(message: str) -> str:
    message = re.sub(r"(?i)\b(?:https?|ssh|git|file)://[^\s'\"<>]+", "<redacted-url>", message)
    message = re.sub(r"(?im)\b(?:authorization|proxy-authorization|cookie|set-cookie):[^\r\n]*", "<redacted-header>", message)
    message = re.sub(
        r"(?i)\b([A-Z0-9_]*(?:TOKEN|PASSWORD|SECRET|CREDENTIAL|PAT)[A-Z0-9_]*|GIT_CONFIG_VALUE_[0-9]+)"
        r"\s*=\s*(?:'[^']*'|\"[^\"]*\"|[^\s]+)",
        r"\1=***", message,
    )
    message = re.sub(
        r"(?<![A-Za-z0-9_])(?:github_pat_[A-Za-z0-9_]{20,}|gh[oprsu]_[A-Za-z0-9_]{6,}|"
        r"gl(?:agent|cbt|ft|pat|ptt|rt|soat)[-_][A-Za-z0-9_-]{6,}|"
        r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}|"
        r"[A-Za-z0-9]{75}AZDO[A-Za-z0-9]{5}|[A-Za-z0-9]{52})(?![A-Za-z0-9_])",
        "***", message,
    )
    message = re.sub(r"(?i)\b(token|password|secret|credential)(\s*[:=]\s*|\s+)\S+", r"\1\2***", message)
    message = re.sub(r"(?im)((?:identity file|enter passphrase for key)\s+)[^\r\n]+", r"\1[REDACTED]", message)
    return message


def git_trace_details(path: Path) -> str:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return "\nGit Trace2: no trace file was emitted by the tested invocation."
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        return "\nGit Trace2: refusing a non-regular or reparse trace path."
    limit = 2 * 1024**2
    with path.open("rb") as stream:
        if info.st_size > limit:
            stream.seek(-limit, os.SEEK_END)
        raw = stream.read(limit)
    if info.st_size > limit:
        raw = raw.partition(b"\n")[2]
    errors: deque[str] = deque(maxlen=8)
    progress: deque[str] = deque(maxlen=8)
    long_paths: deque[str] = deque(maxlen=8)
    malformed = 0
    commands = {"init", "clone", "fetch", "remote", "config", "rev-parse", "ls-remote", "upload-pack", "index-pack", "checkout"}
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if not isinstance(event, dict):
            malformed += 1
            continue
        if event.get("event") == "error":
            message = event.get("msg")
            errors.append(
                redact_trace_message(message)[:1200]
                if isinstance(message, str) else "<error event without a text message>",
            )
        elif event.get("event") == "start":
            argv = event.get("argv")
            command = next(
                (arg for arg in argv if isinstance(arg, str) and arg in commands),
                "git",
            ) if isinstance(argv, list) else "git"
            progress.append(f"start: {command}")
        elif event.get("event") == "exit":
            code = event.get("code")
            progress.append(f"exit: {code if type(code) is int else 'unreported'}")
        elif event.get("event") == "def_param" and event.get("param") == "core.longpaths":
            value = event.get("value")
            normalized = value.casefold() if isinstance(value, str) else ""
            if normalized in {"true", "yes", "on", "1"} or value is True:
                long_paths.append("core.longpaths: true")
            elif normalized in {"false", "no", "off", "0"} or value is False:
                long_paths.append("core.longpaths: false")
            else:
                long_paths.append("core.longpaths: non-boolean value omitted")
    lines = list(long_paths) or ["core.longpaths: not reported"]
    lines += list(errors or progress) or ["No error/start/exit events were emitted."]
    if malformed:
        lines.append(f"Malformed Trace2 lines: {malformed}")
    if info.st_size > limit:
        lines.append("Diagnostics limited to the final 2 MiB of Trace2 data.")
    return "\nGit Trace2 (tested invocation; redacted):\n" + "\n".join(lines)


def build_actor(output: Path) -> None:
    require(os.name == "nt", "Only Windows needs a frozen protocol actor")
    release.interpreter_identity()
    require(not output.exists(), "Actor build requires a fresh output directory")
    with tempfile.TemporaryDirectory(prefix="apmx-actor-build-") as temporary:
        build_root = Path(temporary)
        subprocess.run(
            [
                sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                "--onedir", "--noupx", "--name", "copilot",
                "--distpath", str(build_root / "dist"),
                "--workpath", str(build_root / "work"),
                "--specpath", str(build_root),
                str(FIXTURES / "copilot_actor.py"),
            ],
            check=True,
        )
        shutil.copytree(build_root / "dist/copilot", output)
    require((output / "copilot.exe").is_file(), "Native Windows actor was not built")
    require((output / "_internal").is_dir(), "Native Windows actor runtime was not built")


def copy_actor_bundle(actor: Path, tools: Path) -> None:
    require(actor.read_bytes()[:2] == b"MZ", "Windows fixture must be a native PE executable")
    runtime = actor.parent / "_internal"
    require(runtime.is_dir(), "Windows fixture requires its onedir runtime beside copilot.exe")
    shutil.copyfile(actor, tools / "copilot.exe")
    shutil.copytree(runtime, tools / "_internal")


def isolated_env(root: Path, tools: Path) -> dict[str, str]:
    system_keys = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "SYSTEMDRIVE"}
    env = {key: value for key, value in os.environ.items() if key.upper() in system_keys}
    for name in ("home", "temp", "config", "data", "cache", "appdata", "localappdata", "copilot"):
        (root / name).mkdir()
    env.update({
        "PATH": str(tools) + os.pathsep + env.get("PATH", ""),
        "HOME": str(root / "home"),
        "USERPROFILE": str(root / "home"),
        "APPDATA": str(root / "appdata"),
        "LOCALAPPDATA": str(root / "localappdata"),
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_DATA_HOME": str(root / "data"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "COPILOT_HOME": str(root / "copilot"),
        "TMPDIR": str(root / "temp"),
        "TMP": str(root / "temp"),
        "TEMP": str(root / "temp"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "apmx fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "apmx fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "NO_COLOR": "1",
        "TERM": "dumb",
        "APMX_ACTOR_LOG": str(root / "actor.jsonl"),
    })
    require("PYTHONPATH" not in env and "PYTHONHOME" not in env, "Python source fallback")
    return env


def prepare_tools(root: Path, actor: Path | None) -> Path:
    tools = root / "tools"
    tools.mkdir()
    if os.name == "nt":
        require(actor is not None, "Windows smoke requires an explicit native copilot.exe actor")
        copy_actor_bundle(actor, tools)
    else:
        require(actor is None, "External actor override is only supported on Windows")
        script = tools / "copilot_actor.py"
        shutil.copyfile(FIXTURES / "copilot_actor.py", script)
        wrapper = tools / "copilot"
        wrapper.write_text(
            f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(script))} \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
    return tools


def poison_host_apm(tools: Path, env: dict[str, str]) -> None:
    """An executable refusal sentinel, never an APM installation substitute."""
    decoy = tools / ("apm.exe" if os.name == "nt" else "apm")
    env["APMX_DECOY_APM_LOG"] = str(tools / "host-apm-called")
    if os.name == "nt":
        shutil.copyfile(tools / "copilot.exe", decoy)
    else:
        decoy.write_text(
            '#!/bin/sh\nprintf "host apm selected\\n" > "$APMX_DECOY_APM_LOG"\nexit 97\n',
            encoding="utf-8",
        )
        decoy.chmod(0o755)
    env["APMX_APM_BACKEND"] = str(decoy)
    selected = shutil.which("apm", path=env["PATH"])
    require(selected is not None and Path(selected).resolve() == decoy.resolve(), "APM decoy not first on PATH")


def install_backend_fixture(backend: Path, root: Path, env: dict[str, str]) -> dict:
    source = root / "source"
    skill = source / "skills/bootstrap-style"
    skill.mkdir(parents=True)
    (source / "apm.yml").write_text(
        "name: release-bootstrap\nversion: 0.1.0\ndependencies:\n"
        "  apm:\n    - path: ./skills/bootstrap-style\n",
        encoding="utf-8",
    )
    (skill / "apm.yml").write_text(
        "name: bootstrap-style\nversion: 0.1.0\ndependencies:\n  apm: []\n",
        encoding="utf-8",
    )
    (skill / "SKILL.md").write_text(
        "---\nname: bootstrap-style\ndescription: Real APM install fixture.\n---\n"
        "GENUINE_APM_BOOTSTRAP_SKILL\n",
        encoding="utf-8",
    )
    project = root / "project"
    project.mkdir()
    result = run_binary(
        backend,
        ["install", str(source), "--root", str(project), "--only", "apm",
         "--target", "agent-skills", "--no-trust-bin"],
        root, {**env, "APM_NO_SCRIPTS": "1", "PYINSTALLER_RESET_ENVIRONMENT": "1"},
    )
    require(result.returncode == 0, f"Real APM install failed:\n{result.stdout}\n{result.stderr}")
    lock = project / "apm.lock.yaml"
    require(lock.is_file(), "Real APM install did not create a lockfile")
    installed = list((project / "apm_modules").rglob("SKILL.md"))
    require(
        any(digest(path) == digest(skill / "SKILL.md") for path in installed),
        "Real APM did not install the fixture dependency skill",
    )
    require(not Path(env["APMX_DECOY_APM_LOG"]).exists(), "Host APM decoy was executed")
    return {
        "exit_code": result.returncode, "lock_sha256": digest(lock),
        "skill_sha256": digest(skill / "SKILL.md"),
        "command": ["install", "<owned-source>", "--root", "<owned-project>", "--only", "apm",
                    "--target", "agent-skills", "--no-trust-bin"],
    }


def snapshot(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): digest(path) for path in root.rglob("*") if path.is_file()}


def profile_snapshot(root: Path) -> dict[str, str]:
    return {
        f"{directory}/{Path(path).as_posix()}": sha
        for directory in PROFILE_DIRECTORIES
        for path, sha in snapshot(root / directory).items()
    }


def check_profiles(root: Path, before: dict[str, str], fresh_home: bool) -> list[str]:
    after = profile_snapshot(root)
    changes = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    if fresh_home:
        require(not before, "Fresh-home case must start without profile files")
        require("home/.apm/config.json" in changes, "Real APM bootstrap config was not created")
        allowed = {"home/.apm/config.json", APM_UPDATE_CACHE_FILES[os.name]}
        require(set(changes) <= allowed, f"Unexpected fresh-home activation/write: {changes}")
        require(
            isinstance(json.loads((root / "home/.apm/config.json").read_text(encoding="utf-8")), dict),
            "APM bootstrap config is not a JSON object",
        )
    else:
        require(not changes, f"Ambient profiles changed after APM bootstrap: {changes}")
    return changes


def check_temporary(
    directory: Path, before: dict[str, str], *, fresh_home: bool,
    owned_directory: Path | None, case: str,
) -> list[str]:
    bootstrap = ".apm_empty_gitconfig"
    candidate = directory / bootstrap
    allowed = False
    if os.name == "nt" and fresh_home and bootstrap not in before:
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            info = None
        if info is not None:
            require(
                owned_directory is not None
                and directory == owned_directory == directory.resolve()
                and not directory.is_symlink(),
                "Native empty Git config bootstrap is outside owned temporary storage",
            )
            require(
                stat.S_ISREG(info.st_mode)
                and not stat.S_ISLNK(info.st_mode)
                and not getattr(info, "st_file_attributes", 0) & 0x400
                and info.st_nlink == 1,
                "Native empty Git config bootstrap must be a regular non-reparse file",
            )
            require(info.st_size == 0, "Native empty Git config bootstrap must contain zero bytes")
            allowed = True
    after = snapshot(directory)
    changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    permitted = []
    if allowed and bootstrap in changed:
        require(
            after.get(bootstrap) == hashlib.sha256(b"").hexdigest(),
            "Native empty Git config bootstrap has a nonempty digest",
        )
        changed.remove(bootstrap)
        permitted.append(bootstrap)
    require(
        not changed,
        f"{case}: Temporary producer/package files were not cleaned; "
        f"changed paths ({len(changed)}): {changed[:12]}",
    )
    return permitted


def add_mixed_context(package: Path, env: dict[str, str]) -> dict[str, Path]:
    skill = package / "skills/release-style"
    resources = {
        "references/detail.txt": b"SELECTED_REFERENCE_SENTINEL\n",
        "assets/example.json": b'{"fixture":"SELECTED_ASSET_SENTINEL"}\n',
        "scripts/data_only.py": (
            b"import os\nfrom pathlib import Path\n"
            b"Path(os.environ['APMX_RESOURCE_EXECUTED']).write_text('unexpected activation')\n"
        ),
    }
    for relative, content in resources.items():
        path = skill / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    context = package / "contexts/release-context-package"
    instruction = context / ".apm/instructions/release-guidance.instructions.md"
    instruction.parent.mkdir(parents=True)
    instruction.write_text(
        "---\napplyTo: '**'\n---\nRELEASE_INSTRUCTION_SENTINEL\n", encoding="utf-8",
    )
    contained = context / ".apm/skills/contained-style/SKILL.md"
    contained.parent.mkdir(parents=True)
    contained.write_text(
        (skill / "SKILL.md").read_text(encoding="utf-8").replace(
            "release-style", "contained-style",
        ).replace("RELEASE_SKILL_SENTINEL", "RELEASE_CONTAINED_SKILL_SENTINEL"),
        encoding="utf-8",
    )
    unselected_package = package / "contexts/unselected-package"
    unselected = unselected_package / "SKILL.md"
    unselected.parent.mkdir(parents=True)
    unselected.write_text(
        (skill / "SKILL.md").read_text(encoding="utf-8").replace(
            "release-style", "unselected-style",
        ).replace("RELEASE_SKILL_SENTINEL", "UNSELECTED_SKILL_SENTINEL"),
        encoding="utf-8",
    )
    (context / "apm.yml").write_text(
        "name: release-context-package\nversion: 0.1.0\ndependencies:\n  apm: []\n",
        encoding="utf-8",
    )
    (unselected_package / "apm.yml").write_text(
        "name: unselected-package\nversion: 0.1.0\ndependencies:\n  apm: []\n",
        encoding="utf-8",
    )
    hooks = context / ".github/hooks/unselected.json"
    hooks.parent.mkdir(parents=True)
    hooks.write_text('{"fixture":"UNSELECTED_HOOK_SENTINEL"}\n', encoding="utf-8")
    (package / "apm.yml").write_text(
        (package / "apm.yml").read_text(encoding="utf-8")
        + "    - path: ./contexts/release-context-package\n"
        + "    - path: ./contexts/unselected-package\n",
        encoding="utf-8",
    )
    env["APMX_EXPECT_INSTRUCTION"] = "1"
    env["APMX_CONTEXT_RESOURCE_DIGESTS"] = json.dumps({
        relative: digest(skill / relative) for relative in resources
    })
    env["APMX_RESOURCE_EXECUTED"] = str(package.parent / "resource-was-executed")
    return {relative: skill / relative for relative in resources}


def require_consumer_lock(lock: Path, revision: str) -> str:
    # Check the pinned native lock's Git entry, not a synthetic replacement lock.
    entries = re.split(r"(?m)^- repo_url: ", lock.read_text(encoding="utf-8"))[1:]
    matches = [entry for entry in entries if entry.splitlines()[0] == "fixtures/release-style"]
    require(len(matches) == 1, "Native lock lacks exactly one consumer Git dependency")
    entry = matches[0].split("\ndeployments:", 1)[0]
    for key, value in {
        "name": "release-style", "host": "localhost", "resolved_commit": revision,
        "resolved_ref": "v9", "version": "9.0.0",
    }.items():
        require(
            re.search(rf"(?m)^  {key}: {re.escape(value)}$", entry) is not None,
            f"Native lock did not preserve consumer {key}",
        )
    content_hash = re.search(r"(?m)^  content_hash: (sha256:[0-9a-f]{64})$", entry)
    if content_hash is None:
        raise AssertionError("Missing native content hash")
    return content_hash.group(1)


def consumer_setup_env(env: dict[str, str]) -> dict[str, str]:
    child = {**env, "APM_NO_SCRIPTS": "1", "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    if os.name == "nt":
        count = child.get("GIT_CONFIG_COUNT", "0")
        require(re.fullmatch(r"[0-9]+", count) is not None, "Invalid fixture Git configuration count")
        index = int(count)
        require(index <= len(child) // 2, "Incomplete fixture Git configuration pairs")
        for existing in range(index):
            require(
                f"GIT_CONFIG_KEY_{existing}" in child and f"GIT_CONFIG_VALUE_{existing}" in child,
                "Incomplete fixture Git configuration pairs",
            )
        require(
            f"GIT_CONFIG_KEY_{index}" not in child and f"GIT_CONFIG_VALUE_{index}" not in child,
            "Fixture Git configuration append would overwrite an existing entry",
        )
        child.update({
            "GIT_CONFIG_COUNT": str(index + 1),
            f"GIT_CONFIG_KEY_{index}": "core.longpaths",
            f"GIT_CONFIG_VALUE_{index}": "true",
        })
    return child


def prepare_consumer_lock(
    backend: Path, root: Path, caller: Path, package: Path, env: dict[str, str],
) -> dict:
    origin = root / "consumer-git-origin"
    original_skill = package / "skills/release-style"
    shutil.copytree(original_skill, origin)
    (origin / "apm.yml").write_text("name: release-style\nversion: 9.0.0\n", encoding="utf-8")
    git = shutil.which("git", path=env["PATH"])
    require(git is not None, "Real Git is required for consumer lock precedence proof")
    for args in (["init", "--quiet"], ["add", "."], ["commit", "--quiet", "-m", "Native consumer pin fixture"], ["tag", "v9"]):
        subprocess.run([git, *args], cwd=origin, env=env, capture_output=True, check=True, timeout=30)
    revision = subprocess.check_output(
        [git, "rev-parse", "HEAD"], cwd=origin, env=env, text=True, timeout=10,
    ).strip()
    require(re.fullmatch(r"[0-9a-f]{40}", revision) is not None, "Invalid genuine Git revision")
    transport = root / "git_transport.py"
    transport.write_text(
        "import subprocess, sys\n"
        "if '-G' in sys.argv: raise SystemExit(0)\n"
        f"raise SystemExit(subprocess.call([{git!r}, 'upload-pack', {str(origin)!r}]))\n",
        encoding="utf-8",
    )
    env["GIT_SSH_COMMAND"] = (
        f"{shlex.quote(Path(sys.executable).as_posix())} -B {shlex.quote(transport.as_posix())}"
    )
    env["GIT_SSH_VARIANT"] = "ssh"
    url = "ssh://git@localhost/fixtures/release-style.git"
    advertised = subprocess.run(
        [git, "ls-remote", url, "refs/tags/v9"], cwd=root, env=env,
        capture_output=True, text=True, timeout=30,
        check=False,
    )
    require(
        advertised.returncode == 0 and advertised.stdout.strip() == f"{revision}\trefs/tags/v9",
        f"Genuine Git transport preflight failed:\n{advertised.stdout}\n{advertised.stderr}",
    )
    manifest_text = (
        "name: release-consumer\nversion: 1.0.0\ndependencies:\n  apm:\n"
        f"    - git: {url}\n      ref: v9\n"
        f"    - path: {json.dumps(str(package / 'contexts/release-context-package'))}\n"
    )
    with tempfile.TemporaryDirectory(prefix="ax-") as temporary:
        stage = Path(temporary).resolve()
        (stage / "apm.yml").write_text(manifest_text, encoding="utf-8")
        result = run_binary(
            backend,
            ["install", "--root", str(stage), "--only", "apm", "--target", "agent-skills", "--no-trust-bin"],
            stage, consumer_setup_env(env),
        )
        require(result.returncode == 0, f"Genuine consumer lock generation failed:\n{result.stdout}\n{result.stderr}")
        for name in ("apm.yml", "apm.lock.yaml"):
            original = stage / name
            require(original.is_file(), f"Native consumer generation omitted {name}")
            contents = original.read_text(encoding="utf-8")
            require(
                str(stage) not in contents and stage.as_posix() not in contents,
                f"Native {name} retained a nonportable fixture staging anchor",
            )
            require(not (caller / name).exists(), f"Fixture would overwrite caller {name}")
            shutil.copyfile(original, caller / name)
            require(digest(caller / name) == digest(original), f"Native {name} changed during relocation")
        modules = stage / "apm_modules"
        require(modules.is_dir(), "Native consumer generation omitted installed modules")
        shutil.copytree(modules, caller / "apm_modules")
        require(snapshot(modules) == snapshot(caller / "apm_modules"), "Native modules changed during relocation")
    lock = caller / "apm.lock.yaml"
    require(lock.is_file(), "Real APM did not generate the consumer lock")
    content_hash = require_consumer_lock(lock, revision)
    manifest = package / "apm.yml"
    text = manifest.read_text(encoding="utf-8")
    require(text.count("    - path: ./skills/release-style\n") == 1, "Missing publisher fixture dependency")
    manifest.write_text(
        text.replace(
            "    - path: ./skills/release-style\n",
            f"    - git: {url}\n      ref: publisher-version-does-not-exist\n",
        ),
        encoding="utf-8",
    )
    shutil.rmtree(original_skill)
    return {
        "origin": str(origin), "git_url": url, "resolved_commit": revision,
        "resolved_ref": "v9", "version": "9.0.0", "lock_sha256": digest(lock),
        "content_hash": content_hash,
        "manifest_sha256": digest(caller / "apm.yml"),
        "native_lock_relocated_unchanged": True,
        "caller_path_length": len(str(caller)),
        "setup_longpaths_windows_child_only": os.name == "nt",
        "transport": "hermetic SSH transport serving genuine Git objects; native APM resolution and lock writer",
    }


def run_binary(binary: Path, args: list[str], caller: Path, env: dict[str, str], timeout: float = 90):
    process = subprocess.Popen(
        [str(binary), *args],
        cwd=caller,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output: dict[str, list[str]] = {"stdout": [], "stderr": []}
    errors: list[OSError] = []

    def read(stream, name):
        try:
            for line in stream:
                output[name].append(line)
                if (
                    name == "stdout"
                    and "Hermetic fixture progress." in line
                    and env.get("APMX_STREAM_GATE")
                ):
                    Path(env["APMX_STREAM_GATE"]).write_text("observed before completion\n", encoding="ascii")
        except OSError as error:
            errors.append(error)
        finally:
            stream.close()

    readers = [
        threading.Thread(target=read, args=(process.stdout, "stdout"), daemon=True),
        threading.Thread(target=read, args=(process.stderr, "stderr"), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        if env.get("APMX_CHILD_STOP"):
            Path(env["APMX_CHILD_STOP"]).write_text("smoke watchdog cleanup\n", encoding="ascii")
        raise
    finally:
        for reader in readers:
            reader.join(timeout=5)
    undrained = any(reader.is_alive() for reader in readers)
    if undrained and env.get("APMX_CHILD_STOP"):
        Path(env["APMX_CHILD_STOP"]).write_text("fixture pipe cleanup after failure\n", encoding="ascii")
        for reader in readers:
            reader.join(timeout=5)
    require(not errors, f"Could not drain frozen process output: {errors}")
    require(not undrained, "Frozen process left inherited output pipes open")
    result = subprocess.CompletedProcess(
        [str(binary), *args], process.returncode, "".join(output["stdout"]), "".join(output["stderr"]),
    )
    require(
        all(marker not in result.stdout + result.stderr for marker in PRIVATE_MARKERS),
        "Private tool payload or reasoning was printed",
    )
    return result


def require_actor_transcript(transcript: str, mode: str) -> None:
    require(not any(marker in transcript for marker in PRIVATE_MARKERS), "Private payload retained")
    if mode == "quiet":
        require("Hermetic fixture progress." not in transcript, "Quiet actor invented narration")
        require("Hermetic fixture finished." not in transcript, "Quiet actor invented completion")
    else:
        require(
            transcript.count("Hermetic fixture progress.") == 1,
            "Public progress event was not consumed exactly once",
        )
        finished = transcript.count("Hermetic fixture finished.")
        require(
            finished == (0 if mode in {"halt", "linger"} else 1),
            "Public completion event did not match fixture output behavior",
        )
    view_starts = transcript.count("Tool started: view")
    require(
        view_starts == (0 if mode in {"halt", "linger", "quiet"} else 1),
        "Input tool progress did not match fixture output behavior",
    )
    require(
        transcript.count("Tool completed") == transcript.count("Tool started:"),
        "Tool starts and completions were not consumed consistently",
    )


def child_running(pid: int) -> bool:
    require(pid > 0, "Invalid fixture child PID")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:
                return False
            raise ctypes.WinError(error)
        try:
            state = kernel.WaitForSingleObject(handle, 0)
            require(state in (0, 258), "Could not inspect fixture child termination")
            return state == 258
        finally:
            kernel.CloseHandle(handle)
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "stat="],
        capture_output=True, text=True, timeout=5, check=False,
    )
    require(result.returncode in (0, 1), f"Could not inspect fixture child: {result.stderr}")
    return bool(result.stdout.strip()) and not result.stdout.strip().startswith("Z")


def require_child_cleanup(root: Path) -> None:
    pid = int((root / "child.pid").read_text(encoding="ascii"))
    heartbeat = root / "child.heartbeat"
    before = heartbeat.read_bytes()
    deadline = time.monotonic() + 2
    while child_running(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    require(not child_running(pid), "Fixture descendant remains alive after recorded cleanup")
    time.sleep(0.15)
    require(heartbeat.read_bytes() == before, "Fixture descendant kept writing after recorded cleanup")


def run_case(
    binary: Path, root: Path, actor: Path | None, selection: str, mode: str,
    *, fresh_home: bool = False, mixed_imports: bool = False,
) -> dict:
    with tempfile.TemporaryDirectory(prefix="ax-") as temporary:
        return _run_case(
            binary, root, actor, selection, mode, fresh_home=fresh_home,
            mixed_imports=mixed_imports, temporary_root=Path(temporary).resolve(),
        )


def _run_case(
    binary: Path, root: Path, actor: Path | None, selection: str, mode: str,
    *, fresh_home: bool = False, mixed_imports: bool = False, temporary_root: Path | None = None,
) -> dict:
    root.mkdir()
    require(not root.resolve().is_relative_to(ROOT.resolve()), "Smoke caller must be outside checkout")
    tools = prepare_tools(root, actor)
    env = isolated_env(root, tools)
    if temporary_root is not None:
        env.update({key: str(temporary_root) for key in ("TMPDIR", "TMP", "TEMP")})
    poison_host_apm(tools, env)
    pin = release.check_backend_metadata(binary.parent, release.native_target())
    backend = binary.parent / "libexec/apm" / pin["assets"][release.native_target()]["executable"]
    installation = (
        None if fresh_home else install_backend_fixture(backend, root / "backend-bootstrap", env)
    )
    env["APMX_ACTOR_MODE"] = mode
    env.update({
        "APMX_CHILD_PID": str(root / "child.pid"),
        "APMX_CHILD_HEARTBEAT": str(root / "child.heartbeat"),
        "APMX_CHILD_STOP": str(root / "child.stop"),
    })
    caller = root / "caller"
    if mixed_imports:
        caller = root / ("caller-long-" + "x" * max(1, 160 - len(str(root)) - 13))
    package = root / "package"
    caller.mkdir()
    package.mkdir()
    (caller / "notes.md").write_text('{"source": "caller", "value": 7}\n', encoding="utf-8")
    (package / "notes.md").write_text('{"source": "package-decoy", "value": 100}\n', encoding="utf-8")
    (package / "apm.yml").write_text(
        "name: release-smoke\nversion: 0.1.0\ndependencies:\n  apm: []\n", encoding="utf-8"
    )
    imports = ""
    resources = {}
    if selection == "package":
        skill = package / "skills/release-style"
        skill.mkdir(parents=True)
        (skill / "apm.yml").write_text(
            "name: release-style\nversion: 0.1.0\ndependencies:\n  apm: []\n", encoding="utf-8",
        )
        (skill / "SKILL.md").write_text(
            "---\nname: release-style\ndescription: Hermetic release acceptance fixture.\n---\n"
            "RELEASE_SKILL_SENTINEL\n", encoding="utf-8",
        )
        (package / "apm.yml").write_text(
            "name: release-smoke\nversion: 0.1.0\ndependencies:\n"
            "  apm:\n    - path: ./skills/release-style\n", encoding="utf-8",
        )
        imports = "imports:\n  - release-style\n"
        env["APMX_EXPECT_SKILL"] = "1"
        if mixed_imports:
            resources = add_mixed_context(package, env)
            imports += "  - release-context-package\n"
    source = caller if selection == "local" else package
    (source / "checks").mkdir()
    shutil.copyfile(FIXTURES / "check.py", source / "checks/check.py")
    # Forward slashes preserve the absolute interpreter through POSIX and Git-for-Windows sh.
    interpreter = Path(sys.executable).as_posix()
    checker_command = f'"{interpreter}" -I checks/check.py'
    contract = source / "handoff.contract.md"
    contract.write_text(
        f"---\nneeds: notes.md\nproduces: handoff.json\n{imports}verify:\n"
        f"  identity: {json.dumps(checker_command)}\n---\n"
        "Read notes.md and write its JSON object to handoff.json without changing any values.\n",
        encoding="utf-8",
    )
    consumer_pin = None
    skill_source = package / "skills/release-style"
    if mixed_imports:
        consumer_pin = prepare_consumer_lock(backend, root, caller, package, env)
        skill_source = Path(consumer_pin["origin"])
        resources = {relative: skill_source / relative for relative in resources}
    caller_before = snapshot(caller)
    package_before = snapshot(package)
    profiles_before = profile_snapshot(root)
    temp_directory = Path(env["TMPDIR"])
    temp_before = snapshot(temp_directory)
    args = ["handoff.contract.md"]
    if selection == "package":
        args = ["--from", str(package), *args]
    require(
        not any(
            key.startswith("GIT_CONFIG_KEY_") and value.casefold() == "core.longpaths"
            for key, value in env.items()
        ),
        "Tested apmx process inherited the fixture-only Git long-paths setting",
    )
    trace = None
    if mixed_imports:
        trace = root / "frozen-git-trace.jsonl"
        require(not trace.exists() and not trace.is_symlink(), "Frozen Git trace path already exists")
        env["GIT_TRACE2_EVENT"] = str(trace)
        env["GIT_TRACE2_CONFIG_PARAMS"] = "core.longpaths"
    result = run_binary(
        binary,
        [*args, "--on", "copilot", "--model", "fixture-model", "--allow-host-access"],
        caller, env,
    )
    if mode == "linger":
        try:
            require_child_cleanup(root)
        finally:
            Path(env["APMX_CHILD_STOP"]).write_text("fixture cleanup after observation\n", encoding="ascii")
    code, outcome = {
        "pass": (0, "COMPLETE"), "reject": (20, "REJECTED"), "halt": (22, "HALTED"),
        "quiet": (0, "COMPLETE"), "linger": (22, "HALTED"),
    }[mode]
    require(
        result.returncode == code,
        f"{selection}/{mode}: expected {code}, got {result.returncode}\n{result.stdout}\n{result.stderr}"
        + (git_trace_details(trace) if result.returncode != code and trace is not None else ""),
    )
    records = list(caller.rglob("record.json"))
    require(len(records) == 1, f"Expected exactly one completed record: {records}")
    record = json.loads(records[0].read_bytes())
    run = records[0].parent.resolve()
    require(record["schema"] == "apm-contract-run/0.3", "Record schema")
    require(record["complete"] is True and record["phase"] == "finished", "Incomplete record")
    require(record["profile"] == "native-advisory", "Unexpected assurance profile")
    require(
        Path(record["executable"]).resolve().is_relative_to(tools.resolve()),
        "Run did not select the explicitly identified hermetic actor",
    )
    require(record["result"]["outcome"] == {"name": outcome, "exit_code": code}, "Record outcome")
    require(record["source"]["sha256"] == digest(contract), "Selected source digest mismatch")
    require(Path(record["caller_root"]).resolve() == caller.resolve(), "Caller/source separation")
    require(record["child_pid"] is None and record["active_check"] is None, "Unfinished process state")
    require(record["producer"]["pid"] is not None, "Native producer process was not recorded")
    require(record["producer"]["cleanup_confirmed"] is True, "Producer cleanup unconfirmed")
    require(0 <= record["producer"]["elapsed_seconds"] < 90, "Producer execution was not bounded")
    require(record["producer"]["returncode"] == (7 if mode == "halt" else 0), "Native process exit")
    require(record["native_reported_exit_code"] == (7 if mode == "halt" else 0), "Native envelope exit")
    if mode in {"halt", "linger"}:
        require(bool(record["result"]["stop_reason"]), "Operational halt needs a stop reason")
        require(record["result"]["checks"] == [], "Failed producer must not run checks")
        require(record["artifact"] is None, "Failed producer must not capture an earlier artifact")
        if mode == "linger":
            require(record["producer"]["stop_reason"] == "lingering_children", "Missing lingering-child stop")
    else:
        require(record["result"]["stop_reason"] is None, "Unexpected operational stop")
        artifact = Path(record["artifact"]["path"])
        require(artifact.resolve().is_relative_to(run), "Artifact is outside retained run")
        require(record["artifact"]["sha256"] == digest(artifact), "Artifact digest mismatch")
        require(record["artifact"]["size"] == artifact.stat().st_size, "Artifact size mismatch")
        expected = {"source": "caller", "value": -1 if mode == "reject" else 7}
        require(json.loads(artifact.read_bytes()) == expected, "Wrong output identity")
        checks = record["checks"]
        require(len(checks) == 1, "Expected exactly one independent check")
        checked = checks[0]
        require(record["result"]["checks"] == checks, "Final result lost check observations")
        require(record["result"]["artifact"] == record["artifact"], "Final result changed output identity")
        expected_check = 1 if mode == "reject" else 0
        require(checked["normalized"] == expected_check, "Incorrect check result")
        require(checked["subject_digest"] == digest(artifact), "Checker assessed different bytes")
        require(checked["process"]["pid"] is not None, "Independent checker was not launched")
        require(checked["process"]["cleanup_confirmed"] is True, "Checker cleanup unconfirmed")
        assessments = list((run / "assessments").iterdir())
        require(len(assessments) == 1, "Expected one independent assessment")
        require(
            digest(assessments[0] / "checks/check.py") == digest(FIXTURES / "check.py"),
            "Assessment did not retain the original checker",
        )
        require(
            digest(run / "producer/checks/check.py") != digest(assessments[0] / "checks/check.py"),
            "Producer checker poisoning was not exercised",
        )
        require(digest(assessments[0] / "handoff.json") == digest(artifact), "Assessment output")
    require(snapshot(package) == package_before, "Source package was changed")
    profile_changes = check_profiles(root, profiles_before, fresh_home)
    require(not Path(env["APMX_DECOY_APM_LOG"]).exists(), "Host APM decoy was executed")
    temporary_bootstrap_changes = check_temporary(
        temp_directory, temp_before, fresh_home=fresh_home,
        owned_directory=temporary_root, case=f"{selection}/{mode}",
    )
    require(not snapshot(root / "temp"), "Unused case-local temporary directory was written")
    for path, expected_digest in caller_before.items():
        require(digest(caller / path) == expected_digest, f"Caller file changed: {path}")
    for path in set(snapshot(caller)) - set(caller_before):
        require((caller / path).resolve().is_relative_to(run), f"Unexpected caller write: {path}")
    if selection == "package":
        require(
            record.get("apm_backend") == {
                "version": pin["version"], "source_commit": pin["source_commit"],
                "executable_sha256": digest(backend),
                "pin_sha256": digest(binary.parent / "apm-backend.json"),
            },
            "Package record did not identify the genuine bundled APM backend",
        )
        prepared_root = Path(record["source"]["package"]["root"])
        if prepared_root.resolve() != package.resolve():
            require(not prepared_root.exists(), "Private package not cleaned")
        if mixed_imports:
            require(
                temporary_root is not None
                and prepared_root.resolve().is_relative_to(temporary_root.resolve())
                and not prepared_root.resolve().is_relative_to(caller.resolve()),
                "Long-caller acquisition did not use compact owned temporary staging",
            )
        require(len(record["imports"]) == (3 if mixed_imports else 1), "Unexpected selected import count")
        imported = record["imports"][0]
        require(imported["name"] == "release-style", "Wrong packaged skill")
        if consumer_pin:
            require(imported["lock_identity"] == "localhost/fixtures/release-style", "Consumer Git identity changed")
            require(imported["resolved_commit"] == consumer_pin["resolved_commit"], "Consumer commit was overridden")
            require(imported["resolved_ref"] == consumer_pin["resolved_ref"], "Consumer ref was overridden")
            require(imported["version"] == consumer_pin["version"], "Consumer version was overridden")
            require(imported["verified_package_hash"] == consumer_pin["content_hash"], "Consumer package hash changed")
        else:
            require_local_identity(imported["lock_identity"], skill_source)
        require(imported["sha256"] == digest(skill_source / "SKILL.md"), "Skill digest")
        native_skills_root = run / "producer/.agents/skills"
        require(
            digest(native_skills_root / "release-style/SKILL.md") == imported["sha256"],
            "Native skill discovery bytes differ from the selected import",
        )
        if mixed_imports:
            instruction = package / "contexts/release-context-package/.apm/instructions/release-guidance.instructions.md"
            selected_instruction = record["imports"][1]
            require(selected_instruction["name"] == "release-context-package", "Instruction lost package linkage")
            require_local_identity(
                selected_instruction["lock_identity"], package / "contexts/release-context-package",
                consumer_lock=bool(consumer_pin),
            )
            require(selected_instruction["kind"] == "instruction", "Instruction context kind was lost")
            require(selected_instruction["context_name"] == "release-guidance", "Wrong instruction selected")
            require(selected_instruction["sha256"] == digest(instruction), "Instruction context digest")
            context_root = run / "producer/_apmx_context"
            context_files = [path for path in context_root.rglob("*") if path.is_file()]
            require(context_files, "Selected contexts were not staged for native Copilot")
            require(
                not any(
                    marker in path.read_bytes()
                    for path in (run / "producer").rglob("*") if path.is_file()
                    for marker in (b"UNSELECTED_SKILL_SENTINEL", b"UNSELECTED_HOOK_SENTINEL")
                ),
                "Unselected package content was activated in the producer",
            )
            contained = package / "contexts/release-context-package/.apm/skills/contained-style/SKILL.md"
            selected_skill = record["imports"][2]
            require(selected_skill["name"] == "release-context-package", "Contained skill lost package linkage")
            require_local_identity(
                selected_skill["lock_identity"], package / "contexts/release-context-package",
                consumer_lock=bool(consumer_pin),
            )
            require(selected_skill["kind"] == "skill", "Contained skill context kind was lost")
            require(selected_skill["context_name"] == "contained-style", "Contained skill name mismatch")
            require(selected_skill["sha256"] == digest(contained), "Contained skill digest mismatch")
            for relative, source_resource in resources.items():
                matches = list(native_skills_root.glob(f"*/{relative}"))
                require(len(matches) == 1, f"Expected one staged selected resource: {relative}")
                require(digest(matches[0]) == digest(source_resource), f"Selected resource changed: {relative}")
                require(
                    any(
                        item["path"] == relative
                        and item["sha256"] == digest(source_resource)
                        and item["size"] == source_resource.stat().st_size
                        for item in imported["resources"]
                    ),
                    f"Selected resource provenance missing: {relative}",
                )
            require(not Path(env["APMX_RESOURCE_EXECUTED"]).exists(), "Supporting script executed during import")
        retained = record["source"]["retained"]
        require(digest(Path(retained["contract.contract.md"])) == digest(contract), "Retained contract identity")
        require(digest(Path(retained["apm.lock.yaml"])) == record["lock_sha256"], "Retained package lock identity")
        if consumer_pin:
            require(record["consumer_lock_sha256"] == consumer_pin["lock_sha256"], "Consumer lock record identity")
            require(record["consumer_manifest_sha256"] == consumer_pin["manifest_sha256"], "Consumer manifest record identity")
            require(
                digest(Path(retained["consumer-apm.lock.yaml"])) == consumer_pin["lock_sha256"],
                "Original consumer lock was not retained byte-for-byte",
            )
            for lock_name in ("consumer-apm.lock.yaml", "apm.lock.yaml"):
                require(
                    require_consumer_lock(Path(retained[lock_name]), consumer_pin["resolved_commit"])
                    == consumer_pin["content_hash"],
                    f"{lock_name} changed the consumer's native package hash",
                )
    transcript_path = run / "transcript.log"
    require(record["transcript"]["relative_path"] == "transcript.log", "Transcript identity")
    require(record["transcript"]["sha256"] == digest(transcript_path), "Transcript digest")
    require(record["transcript"]["size"] == transcript_path.stat().st_size, "Transcript size")
    transcript = transcript_path.read_text(encoding="utf-8")
    require_actor_transcript(transcript, mode)
    calls = [json.loads(line) for line in Path(env["APMX_ACTOR_LOG"]).read_text().splitlines()]
    require(sum("-p" in call["argv"] for call in calls) == 1, "Expected exactly one fixture producer")
    return {
        "selection": selection, "mode": mode, "exit_code": result.returncode,
        "actor": "hermetic Copilot JSONL protocol fixture; NOT live model inference",
        "backend_installation": installation,
        "fresh_home": fresh_home, "profile_changes": profile_changes,
        "temporary_bootstrap_changes": temporary_bootstrap_changes,
        "mixed_imports": mixed_imports,
        "consumer_pin": consumer_pin,
        "record": record,
    }


def require_factory_state_file(relative: PurePath) -> None:
    parts = relative.parts
    require(
        len(parts) > 2 and parts[:2] == ("factory", ".apm") and ".." not in parts,
        f"Unexpected factory write: {relative}",
    )


def run_factory_case(binary: Path, root: Path, actor: Path | None) -> dict:
    root.mkdir()
    require(not root.resolve().is_relative_to(ROOT.resolve()), "Factory smoke must be outside checkout")
    tools = prepare_tools(root, actor)
    env = isolated_env(root, tools)
    poison_host_apm(tools, env)
    env.update({"APMX_ACTOR_MODE": "pass", "APMX_FACTORY_SMOKE": "1"})
    caller = root / "caller"
    factory = caller / "factory"
    (factory / "checks").mkdir(parents=True)
    expected = {"source": "caller", "value": 7}
    (factory / "notes.md").write_text(json.dumps(expected) + "\n", encoding="utf-8")
    shutil.copyfile(FIXTURES / "check.py", factory / "checks/check.py")
    for name, inputs, outputs, marker in (
        (
            "first", ["notes.md"], ["first.json", "second.json"],
            "FACTORY_FIXTURE_FIRST",
        ),
        ("second", ["notes.md", "first.json", "second.json"], "final.json", "FACTORY_FIXTURE_SECOND"),
    ):
        paths = outputs if isinstance(outputs, list) else [outputs]
        command = shlex.join([Path(sys.executable).as_posix(), "-I", "checks/check.py", *paths])
        metadata = {"needs": inputs, "produces": outputs, "verify": {"identity": command}}
        (factory / f"{name}.contract.md").write_text(
            "---\n" + json.dumps(metadata) + "\n---\n" + marker + "\n", encoding="utf-8",
        )
    before = snapshot(caller)
    profiles_before = profile_snapshot(root)
    temporary_before = snapshot(Path(env["TMPDIR"]))
    preview = run_binary(binary, ["factory", "--on", "copilot", "--plan"], caller, env)
    require(preview.returncode == 0, f"Frozen factory preview failed: {preview.stdout}\n{preview.stderr}")
    require(not (factory / ".apm").exists(), "Factory preview created execution state")
    require(not Path(env["APMX_ACTOR_LOG"]).exists(), "Factory preview invoked the producer")
    result = run_binary(
        binary,
        ["factory", "--on", "copilot", "--model", "fixture-model",
         "--allow-host-access", "--allow-unproven-inputs"],
        caller, env,
    )
    require(result.returncode == 0, f"Frozen factory failed: {result.stdout}\n{result.stderr}")
    chains = list((factory / ".apm/chains").glob("*/record.json"))
    require(len(chains) == 1, "Expected exactly one factory record")
    record = json.loads(chains[0].read_bytes())
    require(record["complete"] is True, "Factory did not complete")
    require(len(record["nodes"]) == 2, "Factory did not execute both contracts")
    require(all(node["state"] == "completed" for node in record["nodes"]), "Factory node incomplete")
    require(record["allow_host_access"] is True and record["allow_unproven_inputs"] is True,
            "Factory did not retain both explicit permissions")
    view = Path(record["artifacts"]["root"])
    require(view.resolve() == (chains[0].parent / "artifacts").resolve(), "Factory artifact-view identity")
    for name in ("first.json", "second.json", "final.json"):
        require(json.loads((view / name).read_bytes()) == expected, f"Factory delivery mismatch: {name}")
    require(digest(view / "checks/check.py") == digest(FIXTURES / "check.py"), "Factory checker changed")
    leaves = [json.loads(path.read_bytes()) for path in (factory / ".apm/runs").glob("*/record.json")]
    require(len(leaves) == 2, "Expected two factory leaf records")
    require({leaf["schema"] for leaf in leaves} == {"apm-contract-run/0.3"},
            "Factory leaf record version")
    require({isinstance(leaf["artifact"].get("files"), list) for leaf in leaves} == {True, False},
            "Factory did not exercise scalar and multiple-output inventories")
    for leaf in leaves:
        require(leaf["result"]["outcome"] == {"name": "COMPLETE", "exit_code": 0},
                "Factory execution did not complete")
        require(leaf["assurance"]["isolation"] == "unavailable",
                "Factory changed native assurance semantics")
        require(leaf["producer"]["cleanup_confirmed"] is True, "Factory producer cleanup unconfirmed")
        require(leaf["child_pid"] is None and leaf["active_check"] is None, "Factory left active work")
        require(len(leaf["checks"]) == 1, "Factory checker count mismatch")
        check = leaf["checks"][0]
        require(check["normalized"] == 0 and check["process"]["cleanup_confirmed"] is True,
                "Factory did not pass and clean up its independent checker")
    for relative, hashed in before.items():
        require(digest(caller / relative) == hashed, f"Factory source changed: {relative}")
    for relative in set(snapshot(caller)) - set(before):
        require_factory_state_file(Path(relative))
    check_profiles(root, profiles_before, False)
    require(snapshot(Path(env["TMPDIR"])) == temporary_before, "Factory left temporary files")
    for marker in PRIVATE_MARKERS:
        require(marker not in result.stdout + result.stderr, "Factory leaked private fixture content")
    require(not Path(env["APMX_DECOY_APM_LOG"]).exists(), "Factory selected host APM")
    calls = [json.loads(line) for line in Path(env["APMX_ACTOR_LOG"]).read_text().splitlines()]
    require(sum("-p" in call["argv"] for call in calls) == 2, "Factory producer invocation count")
    return {
        "preview_exit": preview.returncode, "exit_code": result.returncode,
        "contracts": 2, "checks": 2, "delivered_files": 3,
        "actor": "hermetic Copilot JSONL protocol fixture; NOT live model inference",
        "record": record,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--version")
    parser.add_argument("--actor", type=Path)
    parser.add_argument("--build-actor", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.build_actor:
        build_actor(args.build_actor.resolve())
        return
    if args.binary is None or args.version is None:
        parser.error("--binary and --version are required")
    release.interpreter_identity()
    binary = args.binary.resolve()
    require(binary.is_file(), "Frozen executable not found")
    header = binary.read_bytes()[:4]
    require(
        header[:2] == b"MZ" or header == b"\x7fELF"
        or header in {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe"},
        "Smoke must receive a native frozen executable, not a source launcher",
    )
    actor = args.actor.resolve() if args.actor else None
    target = release.native_target()
    pin = release.check_backend_metadata(binary.parent, target)
    backend = binary.parent / "libexec/apm" / pin["assets"][target]["executable"]
    backend_version = release.probe_backend(backend, pin, target)
    with tempfile.TemporaryDirectory(prefix="apmx frozen smoke-") as temporary:
        root = Path(temporary).resolve()
        tools = root / "version-tools"
        tools.mkdir()
        probe = root / "probe"
        probe.mkdir()
        env = isolated_env(probe, tools)
        for flag in ("--help", "--version"):
            result = run_binary(binary, [flag], probe, env)
            require(result.returncode == 0, f"{flag}: {result.stdout}\n{result.stderr}")
            if flag == "--version":
                require(args.version in result.stdout, "Wrong released version")
        cases = [
            run_case(binary, root / f"{selection}-{mode}", actor, selection, mode)
            for selection in ("local", "package")
            for mode in ("pass", "reject", "halt")
        ]
        cases += [
            run_case(binary, root / "local-quiet", actor, "local", "quiet"),
            run_case(binary, root / "local-linger", actor, "local", "linger"),
            run_case(binary, root / "package-fresh-home", actor, "package", "pass", fresh_home=True),
            run_case(binary, root / "package-mixed-asf", actor, "package", "pass", mixed_imports=True),
        ]
        factory = run_factory_case(binary, root / "multi-output-factory", actor)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                json.dumps({
                    "binary_sha256": digest(binary), "version": args.version, "cases": cases,
                    "factory": factory,
                    "apm_backend": {
                        **release.backend_provenance(backend.parent, pin, target),
                        "pin_sha256": digest(binary.parent / "apm-backend.json"),
                        "version_output": backend_version,
                    },
                }, indent=2) + "\n",
                encoding="utf-8",
            )
    print("Frozen smoke: 10 local/package cases and one multi-output factory passed with genuine bundled APM; hermetic Copilot, NOT live inference.")


if __name__ == "__main__":
    main()
