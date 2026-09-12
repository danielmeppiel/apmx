"""Archive/provenance unit fixtures; executable acceptance uses real released APM."""

import copy
import io
import json
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import release


def backend_fixture(root: Path, target: str, pin: dict) -> None:
    """Structural bytes only: never executed or reported as genuine APM proof."""
    root.mkdir(parents=True)
    executable = root / pin["assets"][target]["executable"]
    header = (
        b"MZ" if target.startswith("windows-")
        else b"\x7fELF" if target.startswith("linux-") else b"\xcf\xfa\xed\xfe"
    )
    executable.write_bytes(header + b" non-executable unit fixture")
    executable.chmod(0o755)
    license_path = root / f"_internal/apm_cli-{pin['version']}.dist-info/licenses/LICENSE"
    license_path.parent.mkdir(parents=True)
    license_path.write_text("Upstream license fixture\n")
    (root / "_internal/runtime").write_bytes(b"backend runtime fixture")


def add_backend_fixture(bundle: Path, target: str) -> None:
    pin = release.read_backend_pin()
    backend_fixture(bundle / "libexec/apm", target, pin)
    shutil.copyfile(release.BACKEND_PIN, bundle / "apm-backend.json")
    (bundle / "_internal/apmx").mkdir(parents=True)
    shutil.copyfile(release.BACKEND_PIN, bundle / "_internal/apmx/apm-backend.json")
    (bundle / "RELEASE.json").write_text(json.dumps({
        "target": target, "apm_backend_pin_sha256": release.digest(release.BACKEND_PIN),
        "apm_backend": release.backend_provenance(bundle / "libexec/apm", pin, target),
    }))


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pin = release.read_backend_pin()

    def test_pin_requires_exact_five_assets_and_immutable_identity(self):
        for field, value in (
            ("version", "latest"), ("source_commit", "main"),
            ("repository", "untrusted/apm"), ("schema", "unknown"), ("assets", {}),
        ):
            with self.subTest(field=field):
                pin = {**self.pin, field: value}
                path = self.root / "pin.json"
                path.write_text(json.dumps(pin))
                with self.assertRaisesRegex(ValueError, "pin"):
                    release.read_backend_pin(path)
        for field, value in (
            ("root", "../apm"), ("archive", "https://host/file"),
            ("sha256", "not-a-hash"), ("executable", "../apm"),
        ):
            pin = copy.deepcopy(self.pin)
            pin["assets"]["linux-arm64"][field] = value
            path = self.root / "pin.json"
            path.write_text(json.dumps(pin))
            with self.assertRaisesRegex(ValueError, "asset pin"):
                release.read_backend_pin(path)

    def test_bundle_pin_and_provenance_are_verified_for_every_target(self):
        for target in release.TARGETS:
            with self.subTest(target=target):
                bundle = self.root / target
                add_backend_fixture(bundle, target)
                self.assertEqual(release.check_backend_metadata(bundle, target), self.pin)
                executable = bundle / "libexec/apm" / self.pin["assets"][target]["executable"]
                executable.write_bytes(executable.read_bytes() + b"tampered")
                with self.assertRaisesRegex(ValueError, "provenance/hash"):
                    release.check_backend_metadata(bundle, target)

    def test_runtime_pin_cannot_diverge_from_outer_pin(self):
        bundle = self.root / "bundle"
        add_backend_fixture(bundle, "linux-arm64")
        (bundle / "_internal/apmx/apm-backend.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "Runtime APM backend pin"):
            release.check_backend_metadata(bundle, "linux-arm64")

    def test_missing_license_or_script_substitute_is_rejected(self):
        backend = self.root / "backend"
        backend_fixture(backend, "linux-arm64", self.pin)
        executable = backend / "apm"
        original = executable.read_bytes()
        executable.write_text("#!/bin/sh\necho 'pretend APM'\n")
        with self.assertRaisesRegex(ValueError, "native executable"):
            release.check_backend(backend, self.pin, "linux-arm64")
        executable.write_bytes(original)
        (backend / f"_internal/apm_cli-{self.pin['version']}.dist-info/licenses/LICENSE").unlink()
        with self.assertRaisesRegex(ValueError, "upstream license"):
            release.check_backend(backend, self.pin, "linux-arm64")

    def archive_response(self, *, name=None):
        target = "linux-arm64"
        asset = self.pin["assets"][target]
        upstream = self.root / asset["root"]
        backend_fixture(upstream, target, self.pin)
        archive = self.root / asset["archive"]
        with tarfile.open(archive, "w:gz") as stream:
            stream.add(upstream, arcname=name or upstream.name)
        self.pin["assets"][target]["sha256"] = release.digest(archive)
        response = io.BytesIO(archive.read_bytes())
        response.geturl = lambda: "https://release-assets.githubusercontent.com/asset"
        return response

    def test_provision_preserves_complete_onedir_and_probes_explicit_native_path(self):
        response = self.archive_response()
        destination = self.root / "output/apm"
        with (
            patch.object(release, "read_backend_pin", return_value=self.pin),
            patch.object(release, "urlopen", return_value=response) as download,
            patch.object(release, "native_target", return_value="linux-arm64"),
            patch.object(release, "probe_backend", return_value="unit probe") as probe,
        ):
            metadata = release.provision_backend("linux-arm64", destination)
        self.assertIn(
            f"https://github.com/microsoft/apm/releases/download/v{self.pin['version']}/",
            download.call_args.args[0].full_url,
        )
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(probe.call_args.args[0].name, "apm")
        self.assertTrue(probe.call_args.args[0].is_absolute())
        self.assertEqual(metadata["executable_sha256"], release.digest(destination / "apm"))
        self.assertEqual((destination / "_internal/runtime").read_bytes(), b"backend runtime fixture")
        self.assertEqual(list(destination.parent.iterdir()), [destination])
        with self.assertRaisesRegex(ValueError, "new destination"):
            release.provision_backend("linux-arm64", destination)

    def test_download_checksum_is_checked_before_any_extraction(self):
        response = self.archive_response()
        self.pin["assets"]["linux-arm64"]["sha256"] = "0" * 64
        destination = self.root / "output/apm"
        with (
            patch.object(release, "read_backend_pin", return_value=self.pin),
            patch.object(release, "urlopen", return_value=response),
            patch.object(release, "_extract_payload") as extract,
        ):
            with self.assertRaisesRegex(ValueError, "checksum"):
                release.provision_backend("linux-arm64", destination)
        extract.assert_not_called()
        self.assertFalse(destination.exists())
        self.assertEqual(list(destination.parent.iterdir()), [])

    def test_download_rejects_insecure_redirect_and_pinned_unexpected_root(self):
        for insecure in (True, False):
            with self.subTest(insecure=insecure):
                case = self.root / str(insecure)
                case.mkdir()
                response = io.BytesIO()
                with tarfile.open(fileobj=response, mode="w:gz") as stream:
                    stream.addfile(tarfile.TarInfo("unexpected-root"))
                response.seek(0)
                response.geturl = lambda: (
                    "http://host/asset" if insecure else "https://host/asset"
                )
                import hashlib

                pin = copy.deepcopy(self.pin)
                pin["assets"]["linux-arm64"]["sha256"] = hashlib.sha256(response.getvalue()).hexdigest()
                with (
                    patch.object(release, "read_backend_pin", return_value=pin),
                    patch.object(release, "urlopen", return_value=response),
                ):
                    with self.assertRaisesRegex(ValueError, "HTTPS|archive root"):
                        release.provision_backend("linux-arm64", case / "backend")
                self.assertFalse((case / "backend").exists())

    def test_version_probe_requires_both_exact_version_and_source(self):
        for output in ("unrelated 0.30.0", "apm 0.30.0 (fffffff)", "apm 0.0.0 (8c2e0d9)"):
            with patch.object(
                release.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 0, output, ""),
            ):
                with self.assertRaisesRegex(ValueError, "version/source mismatch"):
                    release.probe_backend(self.root / "apm", self.pin)


if __name__ == "__main__":
    unittest.main()
