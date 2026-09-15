"""Installed license/NOTICE files survive packaging without source assumptions."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import release
from tests.release.test_backend import add_backend_fixture


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

    def test_archiving_rejects_uninventoried_libffi_in_each_redistributed_runtime(self):
        for runtime in ("_internal", "libexec/apm/_internal"):
            with self.subTest(runtime=runtime):
                bundle = self.root / runtime.replace("/", "-") / "apmx-linux-x86_64"
                bundle.mkdir(parents=True)
                (bundle / "_internal").mkdir()
                (bundle / "LICENSES").mkdir()
                for name in (
                    "apmx",
                    "LICENSE",
                    "NOTICE",
                    "LICENSES/Python-LICENSE.txt",
                    "LICENSES/manifest.json",
                ):
                    (bundle / name).write_text("Structural unit fixture, not native proof\n")
                add_backend_fixture(bundle, "linux-x86_64")
                (bundle / runtime / "libffi.so.8").write_bytes(b"Uninventoried library fixture")
                with self.assertRaisesRegex(ValueError, "libffi|native notice"):
                    release.archive_bundle(
                        bundle, bundle.parent / "assets", "0.3.0", "linux-x86_64"
                    )

    def native_bundle(self, target):
        bundle = self.root / f"apmx-{target}"
        (bundle / "_internal").mkdir(parents=True)
        (bundle / "LICENSES").mkdir()
        for name in ("apmx", "LICENSE", "NOTICE", "LICENSES/Python-LICENSE.txt"):
            (bundle / name).write_text("Structural unit fixture, not native proof\n")
        (bundle / "LICENSES/manifest.json").write_text("{}\n")
        add_backend_fixture(bundle, target)
        payload = b"\x7fELF structural libffi unit fixture; never executed"
        identities = copy.deepcopy(release.LIBFFI_IDENTITIES)
        for runtime in ("_internal", "libexec/apm/_internal"):
            library = bundle / runtime / "libffi.so.8"
            library.write_bytes(payload)
        identities[target]["sha256"] = release.digest(bundle / "_internal/libffi.so.8")
        (bundle / release.NATIVE_NOTICE_MANIFEST).unlink()
        return bundle, identities

    def bind_native_manifest(self, bundle):
        metadata = json.loads((bundle / "RELEASE.json").read_text())
        metadata["native_notice_manifest_sha256"] = release.digest(
            bundle / release.NATIVE_NOTICE_MANIFEST
        )
        (bundle / "RELEASE.json").write_text(json.dumps(metadata))

    def test_reviewed_grants_and_each_actual_runtime_identity_survive_archive_round_trip(self):
        for target in release.LIBFFI_IDENTITIES:
            with self.subTest(target=target):
                bundle, identities = self.native_bundle(target)
                with patch.object(release, "LIBFFI_IDENTITIES", identities):
                    manifest = release.collect_native_notices(bundle, target)
                    self.bind_native_manifest(bundle)
                    inventory = json.loads(manifest.read_bytes())
                    components = inventory["required_components"]
                    self.assertEqual(
                        {entry["runtime"] for entry in components}, {"apmx", "bundled-apm"}
                    )
                    self.assertEqual(len(components), 2)
                    for component in components:
                        self.assertEqual(
                            component["sha256"], release.digest(bundle / component["path"])
                        )
                        self.assertEqual(component["license_expression"], "MIT")
                        for notice in component["notices"]:
                            copied = bundle / "LICENSES" / notice["path"]
                            self.assertEqual(release.digest(copied), notice["sha256"])
                    archive = release.archive_bundle(bundle, self.root / "assets", "0.3.0", target)
                    extracted = release.extract_archive(archive, self.root / f"extracted-{target}")
                    self.assertEqual(
                        (extracted / release.NATIVE_NOTICE_MANIFEST).read_bytes(),
                        manifest.read_bytes(),
                    )

    def test_missing_or_truncated_copyright_and_permission_grants_fail_verification(self):
        target = "linux-x86_64"
        bundle, identities = self.native_bundle(target)
        with patch.object(release, "LIBFFI_IDENTITIES", identities):
            release.collect_native_notices(bundle, target)
            self.bind_native_manifest(bundle)
            for name in release.LIBFFI_NOTICES:
                path = bundle / "LICENSES/native/libffi" / name
                original = path.read_bytes()
                for replacement in (None, b"MIT\n", b"Copyright without permission grant\n"):
                    with self.subTest(name=name, replacement=replacement):
                        if replacement is None:
                            path.unlink()
                        else:
                            path.write_bytes(replacement)
                        with self.assertRaisesRegex(ValueError, "required libffi notice"):
                            release.check_bundle(bundle, target)
                        path.write_bytes(original)

    def test_rewriting_manifest_hashes_cannot_bless_a_modified_required_notice(self):
        target = "linux-arm64"
        bundle, identities = self.native_bundle(target)
        with patch.object(release, "LIBFFI_IDENTITIES", identities):
            manifest = release.collect_native_notices(bundle, target)
            inventory = json.loads(manifest.read_text())
            notice = inventory["required_components"][0]["notices"][0]
            path = bundle / "LICENSES" / notice["path"]
            path.write_text("Unrelated permission grant\n")
            for component in inventory["required_components"]:
                component["notices"][0]["sha256"] = release.digest(path)
            manifest.write_text(json.dumps(inventory))
            self.bind_native_manifest(bundle)
            with self.assertRaisesRegex(ValueError, "inventory differs"):
                release.check_bundle(bundle, target)

    def test_added_changed_or_unmapped_runtime_bytes_fail_notice_inventory(self):
        target = "linux-arm64"
        bundle, identities = self.native_bundle(target)
        with patch.object(release, "LIBFFI_IDENTITIES", identities):
            release.collect_native_notices(bundle, target)
            self.bind_native_manifest(bundle)
            library = bundle / "libexec/apm/_internal/libffi.so.8"
            original = library.read_bytes()
            library.write_bytes(original + b" changed")
            with self.assertRaisesRegex(ValueError, "Unsupported Linux libffi"):
                release.check_bundle(bundle, target)
            library.write_bytes(original)
            added = bundle / "libexec/apm/_internal/libffi.so.8.1.4"
            added.write_bytes(original)
            with self.assertRaisesRegex(ValueError, "inventory differs"):
                release.check_bundle(bundle, target)
            added.unlink()
            manifest = bundle / release.NATIVE_NOTICE_MANIFEST
            manifest.write_text("{")
            with self.assertRaisesRegex(ValueError, "Malformed native notice inventory"):
                release.check_bundle(bundle, target)

    def test_dependency_consumer_is_not_mislabeled_as_an_embedded_libffi_copy(self):
        bundle = self.root / "consumer"
        extension = bundle / "libexec/apm/_internal/python3.12/lib-dynload/_ctypes.so"
        extension.parent.mkdir(parents=True)
        extension.write_bytes(b"\x7fELF undefined ffi_call, DT_NEEDED libffi.so.8 fixture")
        inventory = release.native_inventory(bundle, "linux-arm64")
        self.assertEqual(inventory["required_components"], [])
        self.assertEqual(len(inventory["native_files"]), 1)
        self.assertEqual(
            inventory["native_files"][0]["path"], extension.relative_to(bundle).as_posix()
        )

    def test_reviewed_source_notices_cannot_be_replaced_before_collection(self):
        with (
            patch.object(release, "ROOT", self.root),
            self.assertRaisesRegex(ValueError, "reviewed libffi notice source"),
        ):
            release.validate_notice_sources()


if __name__ == "__main__":
    unittest.main()
