"""Prove fixture behavior independently of the not-yet-built application."""

import json
import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import smoke
from tests.release.test_backend import add_backend_fixture


class SmokeFixtureTests(unittest.TestCase):
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
                patch.object(smoke.tempfile, "TemporaryDirectory", return_value=contextlib.nullcontext(str(alias))),
                patch.object(smoke, "run_binary", return_value=result),
                patch.object(smoke, "run_case", return_value={}) as run,
                patch.object(sys, "argv", ["smoke.py", "--binary", str(binary), "--version", "0.1.0"]),
                patch("builtins.print"),
            ):
                smoke.main()
            self.assertEqual(run.call_count, 9)
            for call in run.call_args_list:
                self.assertEqual(call.args[1], call.args[1].resolve())

    def test_environment_does_not_copy_credentials_or_python_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict(os.environ, {
                "GITHUB_TOKEN": "not-a-real-token",
                "GH_TOKEN": "not-a-real-token",
                "PYTHONPATH": "/old/apm/src",
                "PYTHONHOME": "/old/python",
                "VIRTUAL_ENV": "/app-installed",
                "COPILOT_HOME": "/real/profile",
            }):
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
                    [sys.executable, "-I", str(smoke.FIXTURES / "copilot_actor.py"), "-p", "fixture"],
                    cwd=root, env=env, capture_output=True, text=True, check=False, timeout=15,
                )
                self.assertEqual(result.returncode, 7 if mode == "halt" else 0)
                events = [json.loads(line) for line in result.stdout.splitlines()]
                self.assertEqual(events[-1]["type"], "result")
                self.assertEqual(events[-1]["exitCode"], result.returncode)
                self.assertEqual(events[-1]["sessionId"], "hermetic-fixture")
                phases = {
                    event["data"]["phase"] for event in events
                    if event["type"] == "assistant.message_start"
                }
                self.assertEqual(phases, set() if mode == "quiet" else {"commentary", "analysis", "final_answer"})
                if mode == "quiet":
                    self.assertEqual(len(events), 1)
                if mode == "halt":
                    self.assertFalse((root / "handoff.json").exists())
                else:
                    self.assertNotEqual((root / "checks/check.py").read_bytes(), baseline)
                    checked = subprocess.run(
                        [sys.executable, "-I", str(smoke.FIXTURES / "check.py")],
                        cwd=root, env=env, capture_output=True, check=False, timeout=15,
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
                cwd=root, env=env, capture_output=True, text=True, check=False, timeout=15,
            )
            self.assertEqual(result.returncode, 0)
            calls = [json.loads(line) for line in Path(env["APMX_ACTOR_LOG"]).read_text().splitlines()]
            self.assertEqual(calls[0]["argv"], ["-p", prompt])
            self.assertFalse((root / "SHELL_INJECTION_SENTINEL").exists())

    def test_source_launcher_cannot_substitute_for_frozen_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            launcher = Path(temporary) / "apmx"
            launcher.write_text("#!/bin/sh\necho fake 0.1.0\n")
            launcher.chmod(0o755)
            with patch.object(sys, "argv", [
                "smoke.py", "--binary", str(launcher), "--version", "0.1.0",
            ]):
                with self.assertRaisesRegex(AssertionError, "native frozen"):
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
            result = smoke.run_binary(Path(sys.executable), ["-I", "-c", code], root, env, timeout=5)
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
                cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
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
            env.update({
                "APMX_ACTOR_MODE": "linger",
                "APMX_CHILD_PID": str(root / "child.pid"),
                "APMX_CHILD_HEARTBEAT": str(root / "child.heartbeat"),
                "APMX_CHILD_STOP": str(root / "child.stop"),
            })
            try:
                result = subprocess.run(
                    [sys.executable, "-I", str(smoke.FIXTURES / "copilot_actor.py"), "-p", "fixture"],
                    cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=20, check=False,
                )
                self.assertEqual(result.returncode, 0)
                pid = int((root / "child.pid").read_text())
                self.assertTrue(smoke.child_running(pid))
                with self.assertRaisesRegex(AssertionError, "remains alive"):
                    smoke.require_child_cleanup(root)
            finally:
                (root / "child.stop").write_text("fixture cleanup\n")
                deadline = time.monotonic() + 5
                while (root / "child.pid").exists() and smoke.child_running(int((root / "child.pid").read_text())):
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
                patch.object(smoke.release, "check_backend_metadata", return_value=smoke.release.read_backend_pin()),
                patch.object(smoke, "install_backend_fixture", return_value={}),
                patch.object(smoke, "run_binary", side_effect=RuntimeError("stop before binary")) as run,
            ):
                with self.assertRaisesRegex(RuntimeError, "stop before binary"):
                    smoke.run_case(Path("unrun-apmx"), root, None, "package", "pass")
            package = root / "package"
            self.assertIn("path: ./skills/release-style", (package / "apm.yml").read_text())
            self.assertIn("imports:\n  - release-style", (package / "handoff.contract.md").read_text())
            self.assertIn("RELEASE_SKILL_SENTINEL", (package / "skills/release-style/SKILL.md").read_text())
            self.assertIn("apm: []", (package / "skills/release-style/apm.yml").read_text())
            self.assertEqual(run.call_args.args[3]["APMX_EXPECT_SKILL"], "1")
            self.assertFalse((root / "caller/apm.yml").exists())

    @unittest.skipIf(os.name == "nt", "Windows sentinel reuses the native fixture actor")
    def test_host_apm_decoy_is_executable_refusal_not_fake_installation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            tools = smoke.prepare_tools(root, None)
            env = smoke.isolated_env(root, tools)
            smoke.poison_host_apm(tools, env)
            result = subprocess.run(
                [str(tools / "apm"), "install"], env=env, capture_output=True, timeout=5,
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
                "home/.copilot/installed_plugins.json", "config/mcp.json",
                "copilot/hooks.json", "appdata/services.json",
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
            with patch.object(smoke, "run_binary", return_value=result):
                with self.assertRaisesRegex(AssertionError, "did not create a lockfile"):
                    smoke.install_backend_fixture(Path("unrun-backend"), root / "missing", env)


if __name__ == "__main__":
    unittest.main()
