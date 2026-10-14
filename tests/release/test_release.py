"""Stdlib-only tests for the standalone release boundary."""

import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
import unittest.mock
import zipfile
from pathlib import Path

from scripts.release import (
    TARGETS,
    archive_bundle,
    digest,
    extract_archive,
    make_manifest,
    read_project,
    validate_lock,
    verify_archive,
)
from tests.release.test_backend import add_backend_fixture


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def bundle(self, target):
        bundle = self.root / ("apmx-" + target)
        bundle.mkdir()
        executable = bundle / ("apmx.exe" if target.startswith("windows") else "apmx")
        executable.write_bytes(b"frozen test placeholder")
        executable.chmod(0o755)
        (bundle / "_internal").mkdir()
        (bundle / "_internal/runtime").write_bytes(b"runtime")
        (bundle / "LICENSE").write_text("MIT\n")
        (bundle / "NOTICE").write_text("Upstream attribution\n")
        (bundle / "LICENSES").mkdir()
        (bundle / "LICENSES/Python-LICENSE.txt").write_text("Python license fixture\n")
        (bundle / "LICENSES/manifest.json").write_text("{}\n")
        add_backend_fixture(bundle, target)
        return bundle

    def test_round_trip_all_targets_and_checksums(self):
        for target in TARGETS:
            with self.subTest(target=target):
                bundle = self.bundle(target)
                archive = archive_bundle(bundle, self.root / "assets", "0.1.0", target)
                verify_archive(archive)
                extracted = extract_archive(archive, self.root / ("extract-" + target))
                self.assertEqual((extracted / "LICENSE").read_text(), "MIT\n")
                self.assertEqual((extracted / "_internal/runtime").read_bytes(), b"runtime")
                self.assertEqual(
                    (extracted / "libexec/apm/_internal/runtime").read_bytes(),
                    b"backend runtime fixture",
                )
                if os.name != "nt" and not target.startswith("windows"):
                    self.assertTrue((extracted / "apmx").stat().st_mode & 0o111)
                archive.write_bytes(archive.read_bytes() + b"tampered")
                with self.assertRaisesRegex(ValueError, "checksum"):
                    verify_archive(archive)

    @unittest.skipIf(os.name == "nt", "Native Windows bundles use link-free ZIP archives")
    def test_internal_symlinks_survive_tar_round_trip(self):
        bundle = self.bundle("macos-arm64")
        (bundle / "_internal/link").symlink_to("runtime")
        archive = archive_bundle(bundle, self.root / "assets", "0.1.0", "macos-arm64")
        extracted = extract_archive(archive, self.root / "extract")
        self.assertTrue((extracted / "_internal/link").is_symlink())
        self.assertEqual((extracted / "_internal/link").read_bytes(), b"runtime")

    def test_tar_rejects_escape_links_special_files_and_duplicates_before_writing(self):
        for name, link, kind in (
            ("../escape", "", tarfile.REGTYPE),
            ("/absolute", "", tarfile.REGTYPE),
            ("apmx-linux-x86_64/link", "../../escape", tarfile.SYMTYPE),
            ("apmx-linux-x86_64/link", "/etc/passwd", tarfile.SYMTYPE),
            ("apmx-linux-x86_64/link", "runtime", tarfile.LNKTYPE),
            ("apmx-linux-x86_64/device", "", tarfile.CHRTYPE),
            ("apmx-linux-x86_64/../escape", "", tarfile.REGTYPE),
            ("apmx-linux-x86_64\\escape", "", tarfile.REGTYPE),
        ):
            with self.subTest(name=name, kind=kind):
                archive = self.root / "unsafe.tar.gz"
                with tarfile.open(archive, "w:gz") as stream:
                    entry = tarfile.TarInfo(name)
                    entry.type, entry.linkname = kind, link
                    stream.addfile(entry, io.BytesIO())
                destination = self.root / "extract"
                with self.assertRaises(ValueError):
                    extract_archive(archive, destination)
                self.assertFalse(destination.exists())

    def test_zip_rejects_traversal_symlinks_and_case_collisions(self):
        for names in (
            ["../escape"],
            ["apmx-windows-x86_64/C:escape"],
            ["apmx-windows-x86_64/a", "apmx-windows-x86_64/A"],
            ["apmx-windows-x86_64/../escape"],
            ["apmx-windows-x86_64/NUL.txt"],
            ["apmx-windows-x86_64/name."],
            ["apmx-windows-x86_64/name "],
        ):
            archive = self.root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as stream:
                for name in names:
                    stream.writestr(name, b"bad")
            with self.assertRaises(ValueError):
                extract_archive(archive, self.root / "extract")
        with zipfile.ZipFile(archive, "w") as stream:
            info = zipfile.ZipInfo("apmx-windows-x86_64/link")
            info.external_attr = 0o120777 << 16
            stream.writestr(info, b"../../escape")
        with self.assertRaises(ValueError):
            extract_archive(archive, self.root / "extract")

    def test_tar_rejects_duplicate_members_and_writes_through_links(self):
        for names in (
            [("apmx-linux-arm64/a", None), ("apmx-linux-arm64/a", None)],
            [("apmx-linux-arm64/link", "."), ("apmx-linux-arm64/link/file", None)],
        ):
            archive = self.root / "unsafe.tar.gz"
            with tarfile.open(archive, "w:gz") as stream:
                for name, link in names:
                    entry = tarfile.TarInfo(name)
                    if link is not None:
                        entry.type, entry.linkname = tarfile.SYMTYPE, link
                    stream.addfile(entry, io.BytesIO())
            with self.assertRaises(ValueError):
                extract_archive(archive, self.root / "extract")
            self.assertFalse((self.root / "extract").exists())

    def test_archive_requires_license_notice_runtime_and_target_executable(self):
        for filename in (
            "LICENSE",
            "NOTICE",
            "LICENSES/Python-LICENSE.txt",
            "LICENSES/manifest.json",
            "_internal/runtime",
            "apmx",
        ):
            root = self.root / filename.replace("/", "-")
            root.mkdir()
            bundle = root / "apmx-linux-x86_64"
            bundle.mkdir()
            for path in (
                "LICENSE",
                "NOTICE",
                "LICENSES/Python-LICENSE.txt",
                "LICENSES/manifest.json",
                "_internal/runtime",
                "apmx",
            ):
                destination = bundle / path
                destination.parent.mkdir(exist_ok=True)
                destination.write_text("content")
            (bundle / filename).unlink()
            with self.assertRaises(ValueError):
                archive_bundle(bundle, root / "assets", "0.1.0", "linux-x86_64")

    def test_manifest_requires_exactly_five_verified_archives(self):
        assets = self.root / "assets"
        assets.mkdir()
        with self.assertRaisesRegex(ValueError, "assets"):
            make_manifest(assets, "0.1.0", "a" * 40)
        for target in TARGETS:
            archive_bundle(self.bundle(target), assets, "0.1.0", target)
        path = make_manifest(assets, "0.1.0", "a" * 40)
        manifest = json.loads(path.read_text())
        self.assertEqual(manifest["commit"], "a" * 40)
        self.assertEqual(len(manifest["archives"]), 5)
        for archive, expected in manifest["archives"].items():
            self.assertEqual(digest(assets / archive), expected)
        (assets / "unexpected.txt").write_text("not a release asset")
        with self.assertRaisesRegex(ValueError, "assets"):
            make_manifest(assets, "0.1.0", "a" * 40)

    def test_project_requires_standalone_interface_and_matching_stable_tag(self):
        project = self.root / "pyproject.toml"
        project.write_text(
            '[project]\nname = "apmx"\nversion = "0.1.0"\n'
            '[project.scripts]\napmx = "apmx.cli:main"\n'
            '[project.optional-dependencies]\ndev = ["pytest"]\nbuild = ["pyinstaller"]\n'
        )
        self.assertEqual(read_project(self.root, "v0.1.0"), "0.1.0")
        for tag in ("v0.2.0", "main", "v0.1.0;echo", "v01.1.0"):
            with self.assertRaises(ValueError):
                read_project(self.root, tag)
        project.write_text(project.read_text().replace('name = "apmx"', 'name = "apm"'))
        with self.assertRaises(ValueError):
            read_project(self.root)

    def test_lock_only_accepts_public_index_and_local_project(self):
        lock = self.root / "uv.lock"
        lock.write_text(
            'version = 1\n[[package]]\nname = "apmx"\nsource = { editable = "." }\n'
            '[[package]]\nname = "click"\nsource = { registry = "https://pypi.org/simple" }\n'
            'wheels = [{url = "https://files.pythonhosted.org/packages/click.whl"}]\n'
        )
        validate_lock(lock)
        for old, new in (
            ("https://pypi.org/simple", "https://private.example/simple"),
            ("https://pypi.org/simple", "https://secret@pypi.org/simple"),
            ("https://files.pythonhosted.org/packages/click.whl", "https://private.example/a"),
            ('editable = "."', 'editable = "../apm"'),
            ('registry = "https://pypi.org/simple"', 'git = "https://github.com/a/b"'),
        ):
            original = lock.read_text()
            lock.write_text(original.replace(old, new))
            with self.assertRaises(ValueError):
                validate_lock(lock)
            lock.write_text(original)

    def test_checksum_rejects_filename_substitution(self):
        archive = archive_bundle(
            self.bundle("linux-arm64"), self.root / "assets", "0.1.0", "linux-arm64"
        )
        checksum = archive.with_name(archive.name + ".sha256")
        checksum.write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  other.tar.gz\n")
        with self.assertRaises(ValueError):
            verify_archive(archive)


if __name__ == "__main__":
    unittest.main()


class RenameRetryTest(unittest.TestCase):
    """Windows AV/indexer handles on fresh files are retried, boundedly, then fail closed."""

    def _flaky(self, failures: int):
        from scripts import release

        calls = []
        real = Path.rename

        def rename(path, target):
            calls.append(path)
            if len(calls) <= failures:
                raise PermissionError(5, "Access is denied", str(path))
            return real(path, target)

        return release, calls, rename

    def test_transient_windows_permission_error_is_retried_then_succeeds(self):
        release, calls, rename = self._flaky(2)
        slept = []
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary, "extracted")
            source.mkdir()
            destination = Path(temporary, "apm")
            with unittest.mock.patch.object(Path, "rename", rename):
                release.rename_with_retry(source, destination, windows=True, sleep=slept.append)
            self.assertTrue(destination.is_dir())
            self.assertFalse(source.exists())
        self.assertEqual(len(calls), 3)
        self.assertEqual(slept, list(release.RENAME_DELAYS[:2]))

    def test_exhausted_retries_raise_the_original_error(self):
        release, calls, rename = self._flaky(100)
        slept = []
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary, "extracted")
            source.mkdir()
            with (
                unittest.mock.patch.object(Path, "rename", rename),
                self.assertRaises(PermissionError) as raised,
            ):
                release.rename_with_retry(
                    source, Path(temporary, "apm"), windows=True, sleep=slept.append
                )
            self.assertTrue(source.is_dir())
        self.assertEqual(raised.exception.errno, 5)
        self.assertEqual(len(calls), len(release.RENAME_DELAYS) + 1)
        self.assertEqual(slept, list(release.RENAME_DELAYS))

    def test_other_platforms_and_errors_are_never_retried(self):
        release, calls, rename = self._flaky(1)
        slept = []
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary, "extracted")
            source.mkdir()
            with (
                unittest.mock.patch.object(Path, "rename", rename),
                self.assertRaises(PermissionError),
            ):
                release.rename_with_retry(
                    source, Path(temporary, "apm"), windows=False, sleep=slept.append
                )
            with self.assertRaises(FileNotFoundError):
                release.rename_with_retry(
                    Path(temporary, "missing"),
                    Path(temporary, "x"),
                    windows=True,
                    sleep=slept.append,
                )
        self.assertEqual((len(calls), slept), (1, []))
