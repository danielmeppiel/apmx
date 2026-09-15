"""Guard release permission/token boundaries; actionlint validates YAML syntax."""

import re
import shlex
import unittest
from pathlib import Path

from scripts import promotion
from scripts.release import TARGETS

ROOT = Path(__file__).resolve().parents[2] / ".github/workflows"


class WorkflowPermissionTests(unittest.TestCase):
    def setUp(self):
        self.release = (ROOT / "release.yml").read_text(encoding="utf-8")
        self.ci = (ROOT / "ci.yml").read_text(encoding="utf-8")

    def job(self, name):
        match = re.search(
            rf"^  {re.escape(name)}:\n(.*?)(?=^  \S|\Z)",
            self.release,
            flags=re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, f"Missing job: {name}")
        return match.group(1)

    def test_private_draft_downloads_have_explicit_job_scoped_write_permission(self):
        self.assertRegex(
            self.job("verify-downloaded"),
            r"(?m)^    permissions:\n      contents: write$",
        )
        write_jobs = {
            name
            for name in ("candidate", "build", "draft", "verify-downloaded", "publish")
            if re.search(r"(?m)^      contents: write$", self.job(name))
        }
        self.assertEqual(write_jobs, {"draft", "verify-downloaded", "publish"})

    def test_default_candidate_build_and_native_ci_remain_read_only(self):
        self.assertRegex(self.release, r"(?m)^permissions:\n  contents: read$")
        self.assertNotRegex(self.job("candidate"), r"(?m)^      contents: (?!read$)")
        self.assertRegex(self.job("build"), r"(?m)^    permissions:\n      contents: read$")
        self.assertRegex(self.ci, r"(?m)^permissions:\n  contents: read$")
        self.assertNotRegex(self.ci, r"(?m)^\s+contents: write$")

    def test_verification_token_is_only_exposed_to_the_download_step(self):
        verification = self.job("verify-downloaded")
        prefix, steps = verification.split("    steps:\n", 1)
        self.assertNotRegex(prefix, r"(?:GH_TOKEN|GITHUB_TOKEN):|github\.token|secrets\.")
        self.assertEqual(verification.count("GH_TOKEN:"), 1)
        self.assertNotIn("GITHUB_TOKEN:", verification)
        self.assertNotIn("secrets.", verification)
        for step in re.split(r"(?m)^      - ", steps)[1:]:
            if "\n        id: download\n" in step:
                self.assertIn("        env:\n          GH_TOKEN: ${{ github.token }}\n", step)
                self.assertIn("python -m scripts.promotion download", step)
            else:
                self.assertNotRegex(step, r"GH_TOKEN|GITHUB_TOKEN|github\.token|secrets\.")
            if "uses: actions/checkout@" in step:
                self.assertIn("persist-credentials: false", step)

    def test_publication_still_requires_downloaded_verification(self):
        self.assertIn(
            "    needs: [candidate, draft, verify-downloaded]\n",
            self.job("publish"),
        )
        verification = self.job("verify-downloaded")
        self.assertIn('--manifest-sha "$MANIFEST_SHA"', verification)
        self.assertIn('--assets-fingerprint "$ASSETS_FINGERPRINT"', verification)
        self.assertIn('python scripts/smoke.py --binary "$BUNDLE/$executable"', verification)
        self.assertNotIn("continue-on-error:", verification)

    def test_native_tests_use_provisioned_real_backend_before_source_free_wheel_proof(self):
        steps = re.split(r"(?m)^      - ", self.ci)
        provision = next(step for step in steps if "\n        id: backend\n" in step)
        tests = next(
            step for step in steps if "python -m pytest tests/unit tests/release -q" in step
        )
        self.assertIn(
            'scripts/release.py provision-apm --target "$TARGET" --output dist/apm-backend',
            provision,
        )
        self.assertIn("uv run --frozen --extra dev --extra build", provision)
        self.assertLess(self.ci.index(provision), self.ci.index(tests))
        self.assertIn("source-free built-wheel", tests)
        self.assertIn("APMX_APM_BACKEND: ${{ steps.backend.outputs.backend }}", tests)
        self.assertNotIn("continue-on-error:", tests)
        self.assertEqual(self.ci.count("APMX_APM_BACKEND:"), 1)

    def test_native_tests_opt_into_optional_factory_tools(self) -> None:
        """Exercise optional BDD checks on each platform without a core dependency."""
        before_tests = self.ci.split("python -m pytest tests/unit tests/release -q", 1)[0]
        commands = re.findall(r"(?m)^\s+run: (uv (?:sync|run) .+)$", before_tests)
        self.assertEqual(len(commands), 3)
        for command in commands:
            arguments = shlex.split(command)
            extras = [
                arguments[index + 1]
                for index, value in enumerate(arguments[:-1])
                if value == "--extra"
            ]
            self.assertIn("factory", extras)
            self.assertIn("--frozen", arguments)


class PublicWorkflowTests(unittest.TestCase):
    job = WorkflowPermissionTests.job

    def setUp(self):
        self.release = (ROOT / "native-notice-release.yml").read_text(encoding="utf-8")
        self.builder = (ROOT / "native-notice-build.yml").read_text(encoding="utf-8")

    def test_new_identities_are_manual_or_reusable_only_and_public_standard_runners(self):
        self.assertIn("on:\n  workflow_dispatch:\n", self.release)
        self.assertIn("on:\n  workflow_call:\n", self.builder)
        for text in (self.release, self.builder):
            self.assertNotRegex(text, r"(?m)^  (push|pull_request|schedule):")
            self.assertIn("github.repository == 'danielmeppiel/apmx'", text)
            self.assertIn("github.event.repository.private == false", text)
            self.assertIn("github.ref == 'refs/heads/main'", text)
            self.assertNotIn("self-hosted", text)
            self.assertNotIn("continue-on-error:", text)
        self.assertEqual(
            TARGETS,
            {
                "linux-x86_64": "ubuntu-24.04",
                "linux-arm64": "ubuntu-24.04-arm",
                "macos-x86_64": "macos-15-intel",
                "macos-arm64": "macos-15",
                "windows-x86_64": "windows-2025",
            },
        )

    def test_every_helper_and_build_checkout_uses_trusted_workflow_revision(self):
        for text in (self.release, self.builder):
            for step in re.split(r"(?m)^      - ", text)[1:]:
                if "uses: actions/checkout@" in step:
                    self.assertIn("ref: ${{ github.sha }}", step)
                    self.assertIn("persist-credentials: false", step)
            self.assertIn("scripts/release.py validate --require-native-notices", text)
        self.assertIn('test "$CANDIDATE" = "$GITHUB_SHA"', self.builder)
        self.assertIn('--tag "$RELEASE_TAG"', self.job("candidate"))
        self.assertNotIn("source_ref", self.release + self.builder)
        self.assertNotIn("ref: refs/tags/", self.release)

    def test_prepare_stops_at_verified_receipt_and_publish_never_rebuilds(self):
        for name in ("build", "draft", "verify-downloaded", "verified"):
            self.assertIn("if: inputs.phase == 'prepare'", self.job(name))
        self.assertIn("needs: [candidate, draft, verify-downloaded]", self.job("verified"))
        self.assertIn(promotion.VERIFICATION_ARTIFACT, self.job("verified"))
        self.assertIn(promotion.VERIFICATION_FILE, self.job("verified"))
        self.assertIn("scripts.promotion record --public-release", self.job("verified"))
        publish = self.job("publish")
        self.assertIn("if: inputs.phase == 'publish'", publish)
        self.assertIn("scripts.promotion publish --public-release", publish)
        self.assertIn('--verified-run-id "$VERIFIED_RUN_ID"', publish)
        self.assertIn('--release-id "$RELEASE_ID"', publish)
        self.assertIn('--assets-fingerprint "$ASSETS_FINGERPRINT"', publish)
        self.assertNotIn("release.py build", publish)
        self.assertNotIn("native-notice-build.yml", publish)
        self.assertEqual(self.release.count("uses: ./.github/workflows/native-notice-build.yml"), 1)
        self.assertNotIn("scripts.promotion publish", self.job("verified"))

    def test_downloaded_bytes_run_without_write_token_and_without_application_source(self):
        verification = self.job("verify-downloaded")
        self.assertIn("name: Downloaded ${{ matrix.target }}", verification)
        self.assertIn("sparse-checkout: scripts", verification)
        self.assertIn("--no-install-project", verification)
        self.assertIn('--manifest-sha "$MANIFEST_SHA"', verification)
        self.assertIn('--assets-fingerprint "$ASSETS_FINGERPRINT"', verification)
        prefix, steps = verification.split("    steps:\n", 1)
        self.assertNotRegex(prefix, r"GH_TOKEN|GITHUB_TOKEN|github\.token|secrets\.")
        self.assertEqual(verification.count("GH_TOKEN:"), 1)
        for step in re.split(r"(?m)^      - ", steps)[1:]:
            if "\n        id: download\n" in step:
                self.assertIn("GH_TOKEN: ${{ github.token }}", step)
                self.assertIn("scripts.promotion download --public-release", step)
            else:
                self.assertNotRegex(step, r"GH_TOKEN|GITHUB_TOKEN|github\.token|secrets\.")
        self.assertRegex(self.builder, r"(?m)^permissions:\n  contents: read$")
        self.assertNotIn("contents: write", self.builder)
        self.assertNotIn("GH_TOKEN", self.job("verified"))

    def test_no_actor_binary_uploads_caches_or_unbounded_artifact_retention(self):
        for text in (self.release, self.builder):
            self.assertNotIn("actions/cache@", text)
            self.assertNotIn("hermetic-copilot-windows-x86_64", text)
            for step in re.split(r"(?m)^      - ", text)[1:]:
                if "uses: actions/upload-artifact@" not in step:
                    continue
                self.assertNotIn("smoke-actor", step)
                self.assertNotIn("hermetic-actor", step)
                self.assertIn("if-no-files-found: error", step)
                retention = re.search(r"retention-days: (\d+)", step)
                self.assertIsNotNone(retention)
                self.assertLessEqual(int(retention.group(1)), 7)
                if "name: apmx-${{ matrix.target }}" in step:
                    self.assertEqual(int(retention.group(1)), 1)
        self.assertIn("--build-actor", self.job("verify-downloaded"))

    def test_all_existing_source_native_and_optional_factory_checks_are_retained(self):
        self.assertIn("python -m pytest tests/unit tests/release -q", self.builder)
        self.assertIn('--basetemp "$RUNNER_TEMP/source-tests"', self.builder)
        self.assertIn("APMX_APM_BACKEND: ${{ steps.backend.outputs.backend }}", self.builder)
        before_build = self.builder.split("scripts/release.py build --target", 1)[0]
        commands = re.findall(r"(?m)^\s+run: (uv (?:sync|run) .+)$", before_build)
        for command in commands:
            self.assertIn("--frozen", command)
            self.assertIn("--extra factory", command)
        for text in (self.builder, self.job("verify-downloaded")):
            self.assertIn('python scripts/smoke.py --binary "$BUNDLE/$executable"', text)


if __name__ == "__main__":
    unittest.main()
