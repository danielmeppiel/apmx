"""Guard release permission/token boundaries; actionlint validates YAML syntax."""

import re
import unittest
from pathlib import Path


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
            name for name in ("candidate", "build", "draft", "verify-downloaded", "publish")
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
        self.assertIn("--manifest-sha \"$MANIFEST_SHA\"", verification)
        self.assertIn("--assets-fingerprint \"$ASSETS_FINGERPRINT\"", verification)
        self.assertIn("python scripts/smoke.py --binary \"$BUNDLE/$executable\"", verification)
        self.assertNotIn("continue-on-error:", verification)

    def test_native_tests_use_provisioned_real_backend_before_source_free_wheel_proof(self):
        steps = re.split(r"(?m)^      - ", self.ci)
        provision = next(step for step in steps if "\n        id: backend\n" in step)
        tests = next(step for step in steps if "python -m pytest tests/unit tests/release -q" in step)
        self.assertIn('scripts/release.py provision-apm --target "$TARGET" --output dist/apm-backend', provision)
        self.assertIn("uv run --frozen --extra dev --extra build", provision)
        self.assertLess(self.ci.index(provision), self.ci.index(tests))
        self.assertIn("source-free built-wheel", tests)
        self.assertIn("APMX_APM_BACKEND: ${{ steps.backend.outputs.backend }}", tests)
        self.assertNotIn("continue-on-error:", tests)
        self.assertEqual(self.ci.count("APMX_APM_BACKEND:"), 1)


if __name__ == "__main__":
    unittest.main()
