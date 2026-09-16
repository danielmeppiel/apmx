"""Prove fixture behavior independently of the not-yet-built application."""

import contextlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import SimpleNamespace
from unittest.mock import patch

from scripts import smoke
from tests.release.test_backend import add_backend_fixture


class SmokeFixtureTests(unittest.TestCase):
    def test_factory_write_boundary_uses_real_path_flavor(self):
        for relative in (
            PureWindowsPath(
                r"factory\.apm\runs\20260915T232133Z-501a98a0f053\producer\.git\refs\heads\master"
            ),
            PureWindowsPath("factory/.apm/chains/id/artifacts/first.json"),
            PurePosixPath("factory/.apm/runs/id/record.json"),
            PurePosixPath(r"factory/.apm/runs/literal\backslash"),
            Path("factory/.apm/runs/native-path"),
        ):
            with self.subTest(relative=relative):
                smoke.require_factory_state_file(relative)

    def test_factory_write_boundary_rejects_outside_paths_and_posix_backslash_impostors(self):
        for relative in (
            PureWindowsPath(r"factory\.apm-old\state"),
            PureWindowsPath(r"C:\factory\.apm\state"),
            PureWindowsPath(r"\\server\share\factory\.apm\state"),
            PureWindowsPath(r"factory\.apm\..\unowned"),
            PureWindowsPath(r"factory\.apm"),
            PurePosixPath("/factory/.apm/state"),
            PurePosixPath("factory/.apm-old/state"),
            PurePosixPath("factory/.apm/../unowned"),
            PurePosixPath("factory/.apm"),
            PurePosixPath(r"factory\.apm\runs\impostor"),
            PurePosixPath(r"factory/.apm\state/impostor"),
            PurePosixPath("unowned"),
        ):
            with (
                self.subTest(relative=relative),
                self.assertRaisesRegex(AssertionError, "Unexpected factory write"),
            ):
                smoke.require_factory_state_file(relative)

    def test_canonical_local_identity_requires_original_absolute_package_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            original = Path(temporary).resolve() / "original-source"
            smoke.require_local_identity(f"local:{original}", original)
            smoke.require_local_identity(str(original), original, consumer_lock=True)
            with self.assertRaises(AssertionError):
                smoke.require_local_identity(str(original) + "-decoy", original, consumer_lock=True)
            for identity in (
                "./original-source",
                "local:original-source",
                f"local:{original}-decoy",
            ):
                with self.assertRaises(AssertionError):
                    smoke.require_local_identity(identity, original)

    def test_windows_actor_build_is_onedir_with_runtime_and_no_build_debris(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary).resolve() / "actor"

            def freeze(command, **kwargs):
                self.assertIn("--onedir", command)
                self.assertNotIn("--onefile", command)
                self.assertTrue(kwargs["check"])
                bundle = Path(command[command.index("--distpath") + 1]) / "copilot"
                (bundle / "_internal").mkdir(parents=True)
                (bundle / "copilot.exe").write_bytes(b"MZ fixture")
                (bundle / "_internal/python312.dll").write_bytes(b"runtime fixture")

            with (
                patch.object(smoke, "os", SimpleNamespace(name="nt")),
                patch.object(smoke.subprocess, "run", side_effect=freeze),
            ):
                smoke.build_actor(output)
            self.assertEqual({path.name for path in output.iterdir()}, {"copilot.exe", "_internal"})
            self.assertEqual((output / "_internal/python312.dll").read_bytes(), b"runtime fixture")

    def test_windows_actor_copy_requires_and_preserves_full_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            actor = root / "actor"
            actor.mkdir()
            executable = actor / "copilot.exe"
            executable.write_bytes(b"MZ fixture")
            tools = root / "tools"
            tools.mkdir()
            with self.assertRaisesRegex(AssertionError, "onedir runtime"):
                smoke.copy_actor_bundle(executable, tools)
            (actor / "_internal").mkdir()
            (actor / "_internal/python312.dll").write_bytes(b"runtime fixture")
            smoke.copy_actor_bundle(executable, tools)
            self.assertEqual((tools / "copilot.exe").read_bytes(), executable.read_bytes())
            self.assertEqual((tools / "_internal/python312.dll").read_bytes(), b"runtime fixture")

    @unittest.skipIf(os.name == "nt", "Covers the macOS /var temporary-directory symlink")
    def test_main_normalizes_temporary_caller_before_package_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            actual = root / "actual"
            actual.mkdir()
            alias = root / "alias"
            alias.symlink_to(actual, target_is_directory=True)
            binary = root / "apmx"
            binary.write_bytes(b"MZ fixture header")
            add_backend_fixture(root, smoke.release.native_target())
            result = subprocess.CompletedProcess([], 0, "apmx 0.1.0", "")
            with (
                patch.object(smoke.release, "probe_backend", return_value="unit fixture"),
                patch.object(
                    smoke.tempfile,
                    "TemporaryDirectory",
                    return_value=contextlib.nullcontext(str(alias)),
                ),
                patch.object(smoke, "run_binary", return_value=result),
                patch.object(smoke, "run_case", return_value={}) as run,
                patch.object(smoke, "run_factory_case", return_value={}) as factory,
                patch.object(
                    sys, "argv", ["smoke.py", "--binary", str(binary), "--version", "0.1.0"]
                ),
                patch("builtins.print"),
            ):
                smoke.main()
            self.assertEqual(run.call_count, 10)
            factory.assert_called_once()
            self.assertEqual(factory.call_args.args[1], factory.call_args.args[1].resolve())
            for call in run.call_args_list:
                self.assertEqual(call.args[1], call.args[1].resolve())

    def test_factory_fixture_exercises_real_source_dispatch_before_frozen_matrix(self):
        """The CI invocation uses frozen bytes; this test checks the fixture against source."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            actor = None
            if os.name == "nt":
                smoke.build_actor(root / "native-actor")
                actor = root / "native-actor/copilot.exe"
            run = smoke.run_binary

            def source(binary, arguments, caller, environment, timeout=90):
                return run(
                    Path(sys.executable),
                    ["-B", "-m", "apmx", *arguments],
                    caller,
                    environment,
                    timeout,
                )

            with patch.object(smoke, "run_binary", side_effect=source):
                result = smoke.run_factory_case(Path(sys.executable), root / "factory", actor)
            self.assertEqual(result["preview_exit"], 0)
            self.assertEqual(result["exit_code"], 21)
            self.assertEqual(
                (result["contracts"], result["checks"], result["delivered_files"]), (2, 2, 3)
            )

    def test_environment_does_not_copy_credentials_or_python_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict(
                os.environ,
                {
                    "GITHUB_TOKEN": "not-a-real-token",
                    "GH_TOKEN": "not-a-real-token",
                    "PYTHONPATH": "/old/apm/src",
                    "PYTHONHOME": "/old/python",
                    "VIRTUAL_ENV": "/app-installed",
                    "COPILOT_HOME": "/real/profile",
                },
            ):
                env = smoke.isolated_env(root, root / "tools")
            for key in ("GITHUB_TOKEN", "GH_TOKEN", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
                self.assertNotIn(key, env)
            self.assertEqual(env["COPILOT_HOME"], str(root / "copilot"))

    def test_actor_and_independent_checker_distinguish_pass_rejection_and_halt(self):
        for mode, expected in (("pass", 0), ("reject", 1), ("halt", None), ("quiet", 0)):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "checks").mkdir()
                shutil.copyfile(smoke.FIXTURES / "check.py", root / "checks/check.py")
                baseline = (root / "checks/check.py").read_bytes()
                (root / "notes.md").write_text('{"source": "caller", "value": 7}\n')
                env = smoke.isolated_env(root, root / "tools")
                env["APMX_ACTOR_MODE"] = mode
                result = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        str(smoke.FIXTURES / "copilot_actor.py"),
                        "-p",
                        "fixture",
                    ],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=15,
                )
                self.assertEqual(result.returncode, 7 if mode == "halt" else 0)
                events = [json.loads(line) for line in result.stdout.splitlines()]
                self.assertEqual(events[-1]["type"], "result")
                self.assertEqual(events[-1]["exitCode"], result.returncode)
                self.assertEqual(events[-1]["sessionId"], "hermetic-fixture")
                phases = {
                    event["data"]["phase"]
                    for event in events
                    if event["type"] == "assistant.message_start"
                }
                self.assertEqual(
                    phases, set() if mode == "quiet" else {"commentary", "analysis", "final_answer"}
                )
                if mode == "quiet":
                    self.assertEqual(len(events), 1)
                if mode == "halt":
                    self.assertFalse((root / "handoff.json").exists())
                else:
                    self.assertNotEqual((root / "checks/check.py").read_bytes(), baseline)
                    checked = subprocess.run(
                        [sys.executable, "-I", str(smoke.FIXTURES / "check.py")],
                        cwd=root,
                        env=env,
                        capture_output=True,
                        check=False,
                        timeout=15,
                    )
                    self.assertEqual(checked.returncode, expected)

    @unittest.skipIf(os.name == "nt", "Windows uses a separately built native actor")
    def test_posix_wrapper_preserves_prompt_as_one_argument(self):
        with tempfile.TemporaryDirectory(prefix="apmx fixture ") as temporary:
            root = Path(temporary)
            tools = smoke.prepare_tools(root, None)
            env = smoke.isolated_env(root, tools)
            env["APMX_ACTOR_MODE"] = "pass"
            (root / "checks").mkdir()
            (root / "checks/check.py").write_text("fixture baseline\n")
            (root / "notes.md").write_text('{"source": "caller", "value": 7}\n')
            prompt = 'fixture "quoted"; touch SHELL_INJECTION_SENTINEL; $(echo unused)'
            result = subprocess.run(
                [str(tools / "copilot"), "-p", prompt],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            self.assertEqual(result.returncode, 0)
            calls = [
                json.loads(line) for line in Path(env["APMX_ACTOR_LOG"]).read_text().splitlines()
            ]
            self.assertEqual(calls[0]["argv"], ["-p", prompt])
            self.assertFalse((root / "SHELL_INJECTION_SENTINEL").exists())

    def test_source_launcher_cannot_substitute_for_frozen_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            launcher = Path(temporary) / "apmx"
            launcher.write_text("#!/bin/sh\necho fake 0.1.0\n")
            launcher.chmod(0o755)
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "smoke.py",
                        "--binary",
                        str(launcher),
                        "--version",
                        "0.1.0",
                    ],
                ),
                self.assertRaisesRegex(AssertionError, "native frozen"),
            ):
                smoke.main()

    def test_live_reader_acknowledges_progress_before_child_completion(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            env = smoke.isolated_env(root, root / "tools")
            env["APMX_STREAM_GATE"] = str(root / "gate")
            code = (
                "import os,time; from pathlib import Path; "
                "print('Hermetic fixture progress.',flush=True); "
                "deadline=time.monotonic()+2\n"
                "while not Path(os.environ['APMX_STREAM_GATE']).exists():\n"
                " if time.monotonic()>deadline: raise RuntimeError('output was buffered')\n"
                " time.sleep(.01)\n"
                "print('completed after acknowledgement',flush=True)\n"
            )
            result = smoke.run_binary(
                Path(sys.executable), ["-I", "-c", code], root, env, timeout=5
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("completed after acknowledgement", result.stdout)
            self.assertTrue((root / "gate").is_file())

    def test_live_actor_waits_for_narration_acknowledgement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            env = smoke.isolated_env(root, root / "tools")
            env["APMX_ACTOR_MODE"] = "pass"
            env["APMX_STREAM_GATE"] = str(root / "gate")
            (root / "notes.md").write_text('{"source": "caller", "value": 7}\n')
            (root / "checks").mkdir()
            process = subprocess.Popen(
                [sys.executable, "-I", str(smoke.FIXTURES / "copilot_actor.py"), "-p", "fixture"],
                cwd=root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                for _ in range(3):
                    event = json.loads(process.stdout.readline())
                    self.assertEqual(event["data"]["messageId"], "commentary")
                self.assertIsNone(process.poll())
                self.assertFalse((root / "handoff.json").exists())
                (root / "gate").write_text("observed\n")
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertEqual(json.loads(stdout.splitlines()[-1])["type"], "result")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                process.stdout.close()
                process.stderr.close()

    def test_lingering_fixture_is_detected_and_fixture_stop_cleans_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            env = smoke.isolated_env(root, root / "tools")
            env.update(
                {
                    "APMX_ACTOR_MODE": "linger",
                    "APMX_CHILD_PID": str(root / "child.pid"),
                    "APMX_CHILD_HEARTBEAT": str(root / "child.heartbeat"),
                    "APMX_CHILD_STOP": str(root / "child.stop"),
                }
            )
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        str(smoke.FIXTURES / "copilot_actor.py"),
                        "-p",
                        "fixture",
                    ],
                    cwd=root,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=20,
                    check=False,
                )
                self.assertEqual(result.returncode, 0)
                pid = int((root / "child.pid").read_text())
                self.assertTrue(smoke.child_running(pid))
                with self.assertRaisesRegex(AssertionError, "remains alive"):
                    smoke.require_child_cleanup(root)
            finally:
                (root / "child.stop").write_text("fixture cleanup\n")
                deadline = time.monotonic() + 5
                while (root / "child.pid").exists() and smoke.child_running(
                    int((root / "child.pid").read_text())
                ):
                    if time.monotonic() >= deadline:
                        self.fail("Fixture child did not honor its cleanup marker")
                    time.sleep(0.05)
            smoke.require_child_cleanup(root)

    def test_packaged_smoke_prepares_one_self_contained_skill_and_explicit_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "case"
            tools = root.parent / "structural-fixture-tools"
            tools.mkdir()
            with (
                patch.object(smoke, "prepare_tools", return_value=tools),
                patch.object(smoke, "poison_host_apm"),
                patch.object(
                    smoke.release,
                    "check_backend_metadata",
                    return_value=smoke.release.read_backend_pin(),
                ),
                patch.object(smoke, "install_backend_fixture", return_value={}),
                patch.object(
                    smoke, "run_binary", side_effect=RuntimeError("stop before binary")
                ) as run,
                self.assertRaisesRegex(RuntimeError, "stop before binary"),
            ):
                smoke.run_case(Path("unrun-apmx"), root, None, "package", "pass")
            package = root / "package"
            self.assertIn("path: ./skills/release-style", (package / "apm.yml").read_text())
            self.assertIn(
                "imports:\n  - release-style", (package / "handoff.contract.md").read_text()
            )
            self.assertIn(
                "RELEASE_SKILL_SENTINEL", (package / "skills/release-style/SKILL.md").read_text()
            )
            self.assertIn("apm: []", (package / "skills/release-style/apm.yml").read_text())
            self.assertEqual(run.call_args.args[3]["APMX_EXPECT_SKILL"], "1")
            self.assertNotIn("GIT_TRACE2_EVENT", run.call_args.args[3])
            self.assertFalse((root / "caller/apm.yml").exists())

    def test_git_trace_errors_are_bounded_and_redact_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            trace = Path(temporary) / "trace.jsonl"
            message = (
                "Filename too long: native Git staging/path\n"
                "https://fixture-user:fixture-password@example.invalid/repo?token=url-secret#fragment\n"
                "Authorization: Bearer header-secret\n"
                "GITHUB_TOKEN=environment-secret\n"
                "token labelled-secret\n"
                "ghp_fixtureToken123\n"
                "identity file '/private/fixture-key'\n"
            )
            trace.write_text(
                json.dumps(
                    {
                        "event": "def_param",
                        "param": "http.extraHeader",
                        "value": "secret-auth-setting",
                    }
                )
                + "\n"
                + json.dumps({"event": "def_param", "param": "core.longpaths", "value": "true"})
                + "\n"
                + "\n".join(
                    json.dumps({"event": "error", "msg": f"problem-{index}: {message}"})
                    for index in range(20)
                )
            )
            detail = smoke.git_trace_details(trace)
            self.assertIn("core.longpaths: true", detail)
            self.assertNotIn("secret-auth-setting", detail)
            self.assertIn("Filename too long", detail)
            self.assertIn("problem-19:", detail)
            self.assertNotIn("problem-0:", detail)
            self.assertLess(len(detail), 11000)
            for secret in (
                "fixture-user",
                "fixture-password",
                "url-secret",
                "header-secret",
                "environment-secret",
                "labelled-secret",
                "ghp_fixtureToken123",
                "/private/fixture-key",
            ):
                self.assertNotIn(secret, detail)

    def test_git_trace_fallback_omits_arguments_and_reports_missing_or_truncated_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            trace = Path(temporary) / "trace.jsonl"
            self.assertIn("no trace file", smoke.git_trace_details(trace))
            trace.write_text(
                json.dumps(
                    {"event": "start", "argv": ["git", "-c", "secret-value", "clone", "secret-url"]}
                )
                + "\n"
                + json.dumps({"event": "exit", "code": 128})
                + "\nmalformed"
            )
            detail = smoke.git_trace_details(trace)
            self.assertIn("start: clone", detail)
            self.assertIn("exit: 128", detail)
            self.assertIn("Malformed Trace2 lines: 1", detail)
            self.assertNotIn("secret-value", detail)
            self.assertNotIn("secret-url", detail)
            trace.write_bytes(
                b"x" * (2 * 1024**2 + 100)
                + b"\n"
                + json.dumps({"event": "error", "msg": "final native error"}).encode()
                + b"\n"
            )
            detail = smoke.git_trace_details(trace)
            self.assertIn("final native error", detail)
            self.assertIn("final 2 MiB", detail)

    def test_mixed_trace_is_only_set_for_actual_app_and_surfaces_its_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "case"
            tools = root.parent / "tools"
            tools.mkdir()
            seed_environments = []

            def seed(backend, case, caller, package, env):
                seed_environments.append(dict(env))
                return {"origin": str(package / "skills/release-style")}

            def fail_app(binary, args, caller, env):
                trace = Path(env["GIT_TRACE2_EVENT"])
                self.assertEqual(env["GIT_TRACE2_CONFIG_PARAMS"], "core.longpaths")
                self.assertEqual(trace.parent, root)
                self.assertFalse(trace.is_relative_to(caller))
                self.assertFalse(trace.is_relative_to(Path(env["TMPDIR"])))
                for directory in smoke.PROFILE_DIRECTORIES:
                    self.assertFalse(trace.is_relative_to(root / directory))
                self.assertFalse(
                    any(
                        key.startswith("GIT_CONFIG_KEY_") and value == "core.longpaths"
                        for key, value in env.items()
                    )
                )
                trace.write_text(
                    json.dumps(
                        {
                            "event": "error",
                            "msg": "actual frozen native failure; token fixture-secret",
                        }
                    )
                    + "\n"
                )
                return subprocess.CompletedProcess([], 22, "HALTED", "")

            with (
                patch.object(smoke, "prepare_tools", return_value=tools),
                patch.object(smoke, "poison_host_apm"),
                patch.object(
                    smoke.release,
                    "check_backend_metadata",
                    return_value=smoke.release.read_backend_pin(),
                ),
                patch.object(smoke, "install_backend_fixture", return_value={}),
                patch.object(smoke, "prepare_consumer_lock", side_effect=seed),
                patch.object(smoke, "run_binary", side_effect=fail_app),
                self.assertRaisesRegex(AssertionError, "actual frozen native failure") as caught,
            ):
                smoke.run_case(Path("unrun-app"), root, None, "package", "pass", mixed_imports=True)
            self.assertNotIn("fixture-secret", str(caught.exception))
            self.assertEqual(len(seed_environments), 1)
            self.assertNotIn("GIT_TRACE2_EVENT", seed_environments[0])
            self.assertNotIn("GIT_TRACE2_CONFIG_PARAMS", seed_environments[0])

    @unittest.skipIf(os.name == "nt", "Windows sentinel reuses the native fixture actor")
    def test_host_apm_decoy_is_executable_refusal_not_fake_installation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            tools = smoke.prepare_tools(root, None)
            env = smoke.isolated_env(root, tools)
            smoke.poison_host_apm(tools, env)
            result = subprocess.run(
                [str(tools / "apm"), "install"],
                env=env,
                capture_output=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(result.returncode, 97)
            self.assertTrue(Path(env["APMX_DECOY_APM_LOG"]).is_file())
            self.assertEqual(env["APMX_APM_BACKEND"], str(tools / "apm"))

    def test_fresh_home_allowlist_rejects_plugin_hook_and_profile_activation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            smoke.isolated_env(root, root / "tools")
            config = root / "home/.apm/config.json"
            config.parent.mkdir()
            config.write_text("{}")
            self.assertEqual(smoke.check_profiles(root, {}, True), ["home/.apm/config.json"])
            before = smoke.profile_snapshot(root)
            self.assertEqual(smoke.check_profiles(root, before, False), [])
            for relative in (
                "home/.copilot/installed_plugins.json",
                "config/mcp.json",
                "copilot/hooks.json",
                "appdata/services.json",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("unselected activation")
                with self.assertRaisesRegex(AssertionError, "activation/write"):
                    smoke.check_profiles(root, {}, True)
                with self.assertRaisesRegex(AssertionError, "Ambient profiles changed"):
                    smoke.check_profiles(root, before, False)
                path.unlink()

    def test_backend_install_requires_lock_and_genuine_dependency_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {"APMX_DECOY_APM_LOG": str(root / "decoy")}
            result = subprocess.CompletedProcess([], 0, "pretend success", "")
            with (
                patch.object(smoke, "run_binary", return_value=result),
                self.assertRaisesRegex(AssertionError, "did not create a lockfile"),
            ):
                smoke.install_backend_fixture(Path("unrun-backend"), root / "missing", env)

    def test_windows_bootstrap_uses_home_appdata_cache_not_redirected_localappdata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            smoke.isolated_env(root, root / "tools")
            config = root / "home/.apm/config.json"
            config.parent.mkdir()
            config.write_text("{}")
            cache = root / "home/AppData/Local/apm/cache/last_version_check"
            cache.parent.mkdir(parents=True)
            cache.write_text("pinned APM Windows update cache")
            with patch.object(smoke, "os", SimpleNamespace(name="nt")):
                changes = smoke.check_profiles(root, {}, True)
                self.assertEqual(
                    set(changes),
                    {
                        "home/.apm/config.json",
                        "home/AppData/Local/apm/cache/last_version_check",
                    },
                )
                wrong = root / "localappdata/apm/cache/last_version_check"
                wrong.parent.mkdir(parents=True)
                wrong.write_text("not the pinned cache path")
                with self.assertRaisesRegex(AssertionError, "activation/write"):
                    smoke.check_profiles(root, {}, True)
                wrong.unlink()
                unix = root / "home/.cache/apm/last_version_check"
                unix.parent.mkdir(parents=True)
                unix.write_text("not the pinned Windows cache path")
                with self.assertRaisesRegex(AssertionError, "activation/write"):
                    smoke.check_profiles(root, {}, True)

    def test_windows_fresh_temp_bootstrap_allows_only_exact_owned_empty_regular_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            config = root / ".apm_empty_gitconfig"
            config.write_bytes(b"")
            with patch.object(smoke, "os", SimpleNamespace(name="nt")):
                changes = smoke.check_temporary(
                    root, {}, fresh_home=True, owned_directory=root, case="unit"
                )
                self.assertEqual(changes, [".apm_empty_gitconfig"])
                self.assertEqual(config.read_bytes(), b"")
                with self.assertRaisesRegex(AssertionError, "outside owned"):
                    smoke.check_temporary(
                        root, {}, fresh_home=True, owned_directory=root / "different", case="unit"
                    )
                with self.assertRaisesRegex(AssertionError, "not cleaned"):
                    smoke.check_temporary(
                        root, {}, fresh_home=False, owned_directory=root, case="unit"
                    )
                config.write_bytes(b"unexpected Git configuration")
                with self.assertRaisesRegex(AssertionError, "zero bytes"):
                    smoke.check_temporary(
                        root, {}, fresh_home=True, owned_directory=root, case="unit"
                    )
                before = smoke.snapshot(root)
                config.write_bytes(b"")
                with self.assertRaisesRegex(AssertionError, "not cleaned"):
                    smoke.check_temporary(
                        root, before, fresh_home=True, owned_directory=root, case="unit"
                    )
                other = root / "unexpected"
                other.write_bytes(b"")
                with self.assertRaisesRegex(AssertionError, "not cleaned"):
                    smoke.check_temporary(
                        root, {}, fresh_home=True, owned_directory=root, case="unit"
                    )
                other.unlink()
                config.unlink()
                nested = root / "nested/.apm_empty_gitconfig"
                nested.parent.mkdir()
                nested.write_bytes(b"")
                with self.assertRaisesRegex(AssertionError, "not cleaned"):
                    smoke.check_temporary(
                        root, {}, fresh_home=True, owned_directory=root, case="unit"
                    )
                nested.unlink()
                config.mkdir()
                with self.assertRaisesRegex(AssertionError, "regular non-reparse"):
                    smoke.check_temporary(
                        root, {}, fresh_home=True, owned_directory=root, case="unit"
                    )
                config.rmdir()
            config.write_bytes(b"")
            with (
                patch.object(smoke, "os", SimpleNamespace(name="posix")),
                self.assertRaisesRegex(AssertionError, "not cleaned"),
            ):
                smoke.check_temporary(root, {}, fresh_home=True, owned_directory=root, case="unit")

    def test_windows_temp_bootstrap_rejects_reparse_attribute(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / ".apm_empty_gitconfig").write_bytes(b"")
            info = SimpleNamespace(
                st_mode=stat.S_IFREG | 0o600,
                st_size=0,
                st_file_attributes=0x400,
                st_nlink=1,
            )
            with (
                patch.object(smoke, "os", SimpleNamespace(name="nt")),
                patch.object(Path, "lstat", return_value=info),
                self.assertRaisesRegex(AssertionError, "regular non-reparse"),
            ):
                smoke.check_temporary(root, {}, fresh_home=True, owned_directory=root, case="unit")

    @unittest.skipIf(os.name == "nt", "Windows symlink creation requires extra host privileges")
    def test_windows_temp_bootstrap_rejects_symlink_to_empty_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            target = parent / "preexisting"
            target.write_bytes(b"")
            root = parent / "owned"
            root.mkdir()
            (root / ".apm_empty_gitconfig").symlink_to(target)
            with (
                patch.object(smoke, "os", SimpleNamespace(name="nt")),
                self.assertRaisesRegex(AssertionError, "regular non-reparse"),
            ):
                smoke.check_temporary(root, {}, fresh_home=True, owned_directory=root, case="unit")
            self.assertEqual(target.read_bytes(), b"")

    def test_mixed_context_fixture_selects_packages_not_primitive_symbols(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "case"
            tools = root.parent / "tools"
            tools.mkdir()
            with (
                patch.object(smoke, "prepare_tools", return_value=tools),
                patch.object(smoke, "poison_host_apm"),
                patch.object(
                    smoke.release,
                    "check_backend_metadata",
                    return_value=smoke.release.read_backend_pin(),
                ),
                patch.object(smoke, "install_backend_fixture", return_value={}),
                patch.object(
                    smoke,
                    "prepare_consumer_lock",
                    return_value={
                        "origin": str(root / "package/skills/release-style"),
                    },
                ),
                patch.object(
                    smoke, "run_binary", side_effect=RuntimeError("stop before binary")
                ) as run,
                self.assertRaisesRegex(RuntimeError, "stop before binary"),
            ):
                smoke.run_case(
                    Path("unrun-apmx"), root, None, "package", "pass", mixed_imports=True
                )
            contract = (root / "package/handoff.contract.md").read_text()
            self.assertIn("  - release-context-package\n", contract)
            self.assertNotIn("  - release-guidance\n", contract)
            self.assertNotIn("  - contained-style\n", contract)
            self.assertNotIn("  - unselected-package\n", contract)
            env = run.call_args.args[3]
            resources = json.loads(env["APMX_CONTEXT_RESOURCE_DIGESTS"])
            self.assertEqual(
                set(resources),
                {"references/detail.txt", "assets/example.json", "scripts/data_only.py"},
            )
            self.assertFalse(Path(env["APMX_RESOURCE_EXECUTED"]).exists())

    def test_native_consumer_lock_assertion_rejects_changed_ref_version_or_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / "apm.lock.yaml"
            revision = "a" * 40
            content = (
                "lockfile_version: '1'\ndependencies:\n"
                "- repo_url: fixtures/release-style\n"
                "  name: release-style\n  host: localhost\n"
                f"  resolved_commit: {revision}\n"
                "  resolved_ref: v9\n  version: 9.0.0\n"
                f"  content_hash: sha256:{'b' * 64}\n"
            )
            lock.write_text(content)
            smoke.require_consumer_lock(lock, revision)
            for before, after in (
                ("v9", "publisher-version-does-not-exist"),
                ("9.0.0", "1.0.0"),
                ("localhost", "otherhost"),
                (revision, "c" * 40),
                ("fixtures/release-style", "different/release-style"),
            ):
                lock.write_text(content.replace(before, after))
                with self.assertRaises(AssertionError):
                    smoke.require_consumer_lock(lock, revision)

    def test_each_frozen_case_gets_compact_owned_temporary_storage(self):
        for mixed in (False, True):
            with self.subTest(mixed=mixed), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve() / ("long-caller-" + "x" * 100)

                def observe(*args, expected_root=root, **kwargs):
                    path = kwargs["temporary_root"]
                    self.assertTrue(path.is_dir())
                    self.assertFalse(path.is_relative_to(expected_root))
                    self.assertLess(len(str(path)), len(str(expected_root)))
                    return {"temporary_root": path}

                with patch.object(smoke, "_run_case", side_effect=observe):
                    result = smoke.run_case(
                        Path("unrun-app"), root, None, "package", "pass", mixed_imports=mixed
                    )
                self.assertFalse(result["temporary_root"].exists())

    def test_windows_long_paths_apply_only_to_native_consumer_setup_child(self):
        env = {
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "",
            "GIT_CONFIG_KEY_1": "http.extraHeader",
            "GIT_CONFIG_VALUE_1": "fixture-header",
            "GIT_SSH_COMMAND": "fixture-ssh",
            "GIT_CONFIG_GLOBAL": "fixture-config",
        }
        before = dict(env)
        with patch.object(smoke, "os", SimpleNamespace(name="nt")):
            child = smoke.consumer_setup_env(env)
        self.assertEqual(env, before)
        self.assertEqual(child["GIT_CONFIG_COUNT"], "3")
        self.assertEqual(child["GIT_CONFIG_KEY_2"], "core.longpaths")
        self.assertEqual(child["GIT_CONFIG_VALUE_2"], "true")
        for key, value in before.items():
            if key != "GIT_CONFIG_COUNT":
                self.assertEqual(child[key], value)
        self.assertEqual(child["APM_NO_SCRIPTS"], "1")
        self.assertEqual(child["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        with patch.object(smoke, "os", SimpleNamespace(name="posix")):
            posix = smoke.consumer_setup_env(env)
        self.assertEqual(posix["GIT_CONFIG_COUNT"], "2")
        self.assertNotIn("GIT_CONFIG_KEY_2", posix)
        with patch.object(smoke, "os", SimpleNamespace(name="nt")):
            fresh = smoke.consumer_setup_env({})
            self.assertEqual(fresh["GIT_CONFIG_COUNT"], "1")
            self.assertEqual(fresh["GIT_CONFIG_KEY_0"], "core.longpaths")
            with self.assertRaisesRegex(AssertionError, "configuration count"):
                smoke.consumer_setup_env({"GIT_CONFIG_COUNT": "invalid"})
            for invalid in (
                {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "credential.helper"},
                {"GIT_CONFIG_COUNT": "999999999"},
                {"GIT_CONFIG_KEY_0": "credential.helper", "GIT_CONFIG_VALUE_0": "fixture-helper"},
                {"GIT_CONFIG_VALUE_0": "orphaned-fixture-value"},
            ):
                before_invalid = dict(invalid)
                with self.assertRaisesRegex(AssertionError, "configuration"):
                    smoke.consumer_setup_env(invalid)
                self.assertEqual(invalid, before_invalid)

    def test_frozen_gate_rejects_leaked_setup_long_paths_before_app_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "case"
            tools = root.parent / "tools"
            tools.mkdir()

            def leak_setting(tools, env):
                env.update(
                    {
                        "GIT_CONFIG_COUNT": "1",
                        "GIT_CONFIG_KEY_0": "core.longpaths",
                        "GIT_CONFIG_VALUE_0": "true",
                    }
                )

            with (
                patch.object(smoke, "prepare_tools", return_value=tools),
                patch.object(smoke, "poison_host_apm", side_effect=leak_setting),
                patch.object(
                    smoke.release,
                    "check_backend_metadata",
                    return_value=smoke.release.read_backend_pin(),
                ),
                patch.object(smoke, "install_backend_fixture", return_value={}),
                patch.object(smoke, "run_binary") as run,
                self.assertRaisesRegex(AssertionError, "inherited the fixture-only"),
            ):
                smoke.run_case(Path("unrun-app"), root, None, "local", "pass")
            run.assert_not_called()

    def test_consumer_fixture_relocates_native_outputs_but_not_activation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            package = root / "package"
            skill = package / "skills/release-style"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("structural unit fixture")
            (package / "apm.yml").write_text(
                "dependencies:\n  apm:\n    - path: ./skills/release-style\n",
            )
            caller = root / ("long-caller-" + "x" * 100)
            caller.mkdir()
            revision = "a" * 40
            stages = []
            native_locks = []

            def native_output(binary, args, stage, env):
                self.assertEqual(args[args.index("--root") + 1], str(stage))
                self.assertFalse(stage.is_relative_to(caller))
                self.assertLess(len(str(stage)), len(str(caller)))
                stages.append(stage)
                lock = (
                    "dependencies:\n- repo_url: fixtures/release-style\n"
                    "  name: release-style\n  host: localhost\n"
                    f"  resolved_commit: {revision}\n"
                    "  resolved_ref: v9\n  version: 9.0.0\n"
                    f"  content_hash: sha256:{'b' * 64}\n"
                )
                native_locks.append(lock)
                (stage / "apm.lock.yaml").write_text(lock)
                (stage / "apm_modules").mkdir()
                (stage / "apm_modules/bytes").write_bytes(b"native output unit fixture")
                (stage / ".agents").mkdir()
                (stage / ".agents/activation").write_text("must not relocate")
                return subprocess.CompletedProcess([], 0, "", "")

            with (
                patch.object(smoke.shutil, "which", return_value="git"),
                patch.object(
                    smoke.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(
                        [],
                        0,
                        f"{revision}\trefs/tags/v9\n",
                        "",
                    ),
                ),
                patch.object(smoke.subprocess, "check_output", return_value=revision + "\n"),
                patch.object(smoke, "run_binary", side_effect=native_output),
            ):
                proof = smoke.prepare_consumer_lock(
                    Path("unrun-backend"), root, caller, package, {"PATH": ""}
                )
            self.assertEqual((caller / "apm.lock.yaml").read_text(), native_locks[0])
            self.assertEqual(
                (caller / "apm_modules/bytes").read_bytes(), b"native output unit fixture"
            )
            self.assertFalse((caller / ".agents").exists())
            self.assertTrue(proof["native_lock_relocated_unchanged"])
            self.assertFalse(stages[0].exists())
            self.assertIn("publisher-version-does-not-exist", (package / "apm.yml").read_text())

    def test_mixed_actor_rejects_unselected_prompt_and_changed_supporting_resource(self):
        for failure in (None, "unselected", "resource"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                env = smoke.isolated_env(root, root / "tools")
                env["APMX_ACTOR_MODE"] = "pass"
                package = root / "package"
                skill = package / "skills/release-style"
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text("RELEASE_SKILL_SENTINEL\n")
                (package / "apm.yml").write_text("dependencies:\n  apm: []\n")
                resources = smoke.add_mixed_context(package, env)
                for relative, source in resources.items():
                    target = root / ".agents/skills/release-style" / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                (root / "checks").mkdir()
                (root / "notes.md").write_text('{"source":"caller","value":7}')
                shutil.copyfile(skill / "SKILL.md", root / ".agents/skills/release-style/SKILL.md")
                contained = root / ".agents/skills/contained-style"
                contained.mkdir()
                (contained / "SKILL.md").write_text("RELEASE_CONTAINED_SKILL_SENTINEL")
                prompt = "RELEASE_INSTRUCTION_SENTINEL"
                if failure == "unselected":
                    prompt += " UNSELECTED_SKILL_SENTINEL"
                if failure == "resource":
                    (root / ".agents/skills/release-style/references/detail.txt").write_text(
                        "changed"
                    )
                result = subprocess.run(
                    [sys.executable, "-I", str(smoke.FIXTURES / "copilot_actor.py"), "-p", prompt],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(result.returncode == 0, failure is None, result.stderr)
                self.assertFalse(Path(env["APMX_RESOURCE_EXECUTED"]).exists())


if __name__ == "__main__":
    unittest.main()
