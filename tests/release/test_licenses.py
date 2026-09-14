"""Installed license/NOTICE files survive packaging without source assumptions."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import release


class LicenseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        (self.runtime / "LICENSE.txt").write_text("Python license fixture\n")
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.site = self.root / "site"
        self.site.mkdir()

    def collect(self, distributions):
        with (
            patch.object(release.sys, "base_prefix", str(self.runtime)),
            patch.object(release.sysconfig, "get_path", return_value=str(self.runtime)),
            patch.object(release.importlib.metadata, "distributions", return_value=distributions),
        ):
            release.collect_licenses(self.bundle)

    def test_python_and_distribution_notices_are_copied_and_inventoried(self):
        files = [
            "example-1.dist-info/licenses/LICENSE",
            "example-1.dist-info/licenses/vendor/NOTICE",
            "example-1.dist-info/METADATA",
        ]
        for name in files:
            path = self.site / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
        distribution = SimpleNamespace(
            metadata={"Name": "example", "License-Expression": "MIT"},
            version="1",
            files=files,
            locate_file=lambda name: self.site / name,
        )
        self.collect([distribution])
        notices = self.bundle / "LICENSES"
        self.assertEqual((notices / "Python-LICENSE.txt").read_text(), "Python license fixture\n")
        inventory = json.loads((notices / "manifest.json").read_bytes())
        entry = inventory["distributions"][0]
        self.assertEqual(entry["status"], "files-copied")
        self.assertEqual(len(entry["files"]), 2)
        self.assertEqual(entry["license_metadata"], "MIT")
        self.assertEqual((notices / entry["files"][0]).read_text(), files[0])
        self.assertEqual((notices / entry["files"][1]).read_text(), files[1])
        self.assertFalse(any(path.name == "METADATA" for path in notices.rglob("*")))

    def test_missing_python_license_blocks_packaging(self):
        (self.runtime / "LICENSE.txt").unlink()
        with self.assertRaisesRegex(ValueError, "Python license"):
            self.collect([])

    def test_declared_but_missing_distribution_license_blocks_packaging(self):
        distribution = SimpleNamespace(
            metadata={"Name": "example"},
            version="1",
            files=["LICENSE"],
            locate_file=lambda name: self.site / name,
        )
        with self.assertRaisesRegex(ValueError, "license is missing"):
            self.collect([distribution])

    def test_upstream_metadata_only_and_missing_notices_are_explicit(self):
        distributions = [
            SimpleNamespace(
                metadata={"Name": "metadata-only", "License": "MIT"},
                version="1",
                files=[],
            ),
            SimpleNamespace(metadata={"Name": "no-notice"}, version="2", files=[]),
        ]
        with patch("builtins.print") as warning:
            self.collect(distributions)
        inventory = json.loads((self.bundle / "LICENSES/manifest.json").read_bytes())
        self.assertEqual(
            [item["status"] for item in inventory["distributions"]],
            ["metadata-only", "not-provided"],
        )
        warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
