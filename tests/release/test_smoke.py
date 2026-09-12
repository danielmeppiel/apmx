"""Prove fixture behavior independently of the not-yet-built application."""

import json
import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import smoke


class SmokeFixtureTests(unittest.TestCase):
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
            result = subprocess.CompletedProcess([], 0, "apmx 0.1.0", "")
            with (
                patch.object(smoke.tempfile, "TemporaryDirectory", return_value=contextlib.nullcontext(str(alias))),
                patch.object(smoke, "run_binary", return_value=result),
                patch.object(smoke, "run_case", return_value={}) as run,
                patch.object(sys, "argv", ["smoke.py", "--binary", str(binary), "--version", "0.1.0"]),
                patch("builtins.print"),
            ):
                smoke.main()
            self.assertEqual(run.call_count, 6)
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
        for mode, expected in (("pass", 0), ("reject", 1), ("halt", None)):
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
                self.assertEqual(phases, {"commentary", "analysis", "final_answer"})
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


if __name__ == "__main__":
    unittest.main()
