"""Structural binary fixtures; actual archive evidence is retained separately."""

import json
import os
import struct
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import release
from tests.release.test_backend import add_backend_fixture


def macho(*, defined=True, endian="<", bits=64):
    names = b"\0_ffi_call\0_ffi_prep_cif\0"
    header_size = 32 if bits == 64 else 28
    symbol_size = 16 if bits == 64 else 12
    symoff = header_size + 24
    stroff = symoff + 2 * symbol_size
    header = struct.pack(
        endian + ("IiiIIIII" if bits == 64 else "IiiIIII"),
        0xFEEDFACF if bits == 64 else 0xFEEDFACE,
        0x100000C if bits == 64 else 12,
        0,
        6,
        1,
        24,
        0,
        *([0] if bits == 64 else []),
    )
    command = struct.pack(endian + "IIIIII", 2, 24, symoff, 2, stroff, len(names))
    symbols = b"".join(
        struct.pack(
            endian + ("IBBHQ" if bits == 64 else "IBBHI"),
            index,
            0x0F if defined else 0x01,
            1 if defined else 0,
            0,
            0x1000 if defined else 0,
        )
        for index in (1, 11)
    )
    return header + command + symbols + names


class NativeInterpreterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def bundle(self):
        bundle = self.root / "apmx-macos-arm64"
        (bundle / "_internal").mkdir(parents=True)
        (bundle / "LICENSES").mkdir()
        for name in ("apmx", "LICENSE", "NOTICE", "LICENSES/Python-LICENSE.txt"):
            (bundle / name).write_text("Structural fixture, not native execution\n")
        (bundle / "LICENSES/manifest.json").write_text("{}\n")
        add_backend_fixture(bundle, "macos-arm64")
        library = bundle / "_internal/libpython3.12.dylib"
        library.write_bytes(macho())
        return bundle, library

    def test_collection_refuses_unmapped_embedded_ffi_with_only_generic_python_license(self):
        bundle, _ = self.bundle()
        (bundle / release.NATIVE_NOTICE_MANIFEST).unlink()
        with self.assertRaisesRegex(ValueError, "embedded libffi"):
            release.collect_native_notices(bundle, "macos-arm64")

    def test_self_resigned_inventory_cannot_bypass_check_archive_or_download_boundary(self):
        bundle, library = self.bundle()
        manifest = bundle / release.NATIVE_NOTICE_MANIFEST
        inventory = json.loads(manifest.read_bytes())
        inventory["native_files"].append(
            {
                "path": "_internal/libpython3.12.dylib",
                "sha256": release.digest(library),
                "format": "Mach-O",
                "symlink": False,
            }
        )
        inventory["native_files"].sort(key=lambda entry: entry["path"])
        manifest.write_text(json.dumps(inventory))
        metadata = json.loads((bundle / "RELEASE.json").read_bytes())
        metadata["native_notice_manifest_sha256"] = release.digest(manifest)
        (bundle / "RELEASE.json").write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError, "embedded libffi"):
            release.check_bundle(bundle, "macos-arm64")
        with self.assertRaisesRegex(ValueError, "embedded libffi"):
            release.archive_bundle(bundle, self.root / "assets", "0.3.0", "macos-arm64")
        archive = self.root / "apmx-0.3.0-macos-arm64.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            stream.add(bundle, arcname=bundle.name)
        archive.with_name(archive.name + ".sha256").write_text(
            f"{release.digest(archive)}  {archive.name}\n"
        )
        with self.assertRaisesRegex(ValueError, "embedded libffi"):
            release.extract_archive(archive, self.root / "download")

    def test_defined_symbols_not_names_or_undefined_imports_identify_ffi(self):
        for endian in ("<", ">"):
            for bits in (32, 64):
                with self.subTest(endian=endian, bits=bits):
                    self.assertEqual(
                        release.macho_ffi_symbols(macho(endian=endian, bits=bits)),
                        {"ffi_call", "ffi_prep_cif"},
                    )
                    self.assertEqual(
                        release.macho_ffi_symbols(macho(defined=False, endian=endian, bits=bits)),
                        set(),
                    )

    def test_universal_binary_checks_every_slice(self):
        first, second = macho(defined=False), macho()
        for endian in ("<", ">"):
            for bits in (32, 64):
                offset = 8 + 2 * (32 if bits == 64 else 20)
                format_ = endian + ("iiQQII" if bits == 64 else "iiIII")
                trailing = (0, 0) if bits == 64 else (0,)
                universal = (
                    struct.pack(endian + "II", 0xCAFEBABF if bits == 64 else 0xCAFEBABE, 2)
                    + struct.pack(format_, 0x1000007, 0, offset, len(first), *trailing)
                    + struct.pack(
                        format_, 0x100000C, 0, offset + len(first), len(second), *trailing
                    )
                    + first
                    + second
                )
                with self.subTest(endian=endian, bits=bits):
                    self.assertEqual(
                        release.macho_ffi_symbols(universal), {"ffi_call", "ffi_prep_cif"}
                    )

    def test_malformed_native_evidence_refuses_instead_of_looking_like_no_ffi(self):
        valid = macho()
        bad_offset = bytearray(valid)
        struct.pack_into("<I", bad_offset, 40, len(valid) + 100)
        for payload in (valid[:4], valid[:-1], bytes(bad_offset)):
            with (
                self.subTest(payload=payload[:12]),
                self.assertRaisesRegex(ValueError, "Mach-O"),
            ):
                release.macho_ffi_symbols(payload)
        stripped = struct.pack("<IiiIIIII", 0xFEEDFACF, 0x100000C, 0, 6, 0, 0, 0, 0)
        with self.assertRaisesRegex(ValueError, "without symbol evidence"):
            release.macho_ffi_symbols(stripped, require_symbols=True)

    def test_setup_python_binding_checks_actual_base_executable(self):
        actual = Path(release.sys._base_executable).resolve()
        with patch.dict(os.environ, {"APMX_BUILD_PYTHON": str(actual)}):
            identity = release.interpreter_identity()
        self.assertTrue(identity["selection_verified"])
        self.assertEqual(identity["executable_sha256"], release.digest(actual))
        other = self.root / "unselected-python"
        other.write_bytes(actual.read_bytes())
        with (
            patch.dict(os.environ, {"APMX_BUILD_PYTHON": str(other)}),
            self.assertRaisesRegex(ValueError, "selected build interpreter"),
        ):
            release.interpreter_identity()

    def test_unsupported_nonframework_mac_build_is_refused_without_installing_anything(self):
        with (
            patch.object(release.sysconfig, "get_config_var", return_value=""),
            self.assertRaisesRegex(ValueError, "framework"),
        ):
            release.build_interpreter("macos-arm64")

    def test_supported_mac_profile_still_records_actual_identity(self):
        with (
            patch.object(release.sysconfig, "get_config_var", return_value="Python"),
            patch.dict(os.environ, {"APMX_BUILD_PYTHON": release.sys._base_executable}),
        ):
            identity = release.build_interpreter("macos-arm64")
        self.assertEqual(identity["framework"], "Python")
        self.assertEqual(
            identity["executable_sha256"],
            release.digest(Path(release.sys._base_executable).resolve()),
        )

    def test_missing_ci_selection_and_absent_selected_executable_fail_closed(self):
        for environment in (
            {"GITHUB_ACTIONS": "true"},
            {"APMX_BUILD_PYTHON": str(self.root / "missing-python")},
        ):
            with (
                self.subTest(environment=environment),
                patch.dict(os.environ, environment, clear=True),
                self.assertRaisesRegex(ValueError, "selected build interpreter"),
            ):
                release.interpreter_identity()

    def test_mac_archive_verification_is_portable_and_checks_recorded_not_host_profile(self):
        bundle, library = self.bundle()
        library.write_bytes(macho(defined=False))
        manifest = bundle / release.NATIVE_NOTICE_MANIFEST
        manifest.unlink()
        release.collect_native_notices(bundle, "macos-arm64")
        metadata = json.loads((bundle / "RELEASE.json").read_bytes())
        metadata["native_notice_manifest_sha256"] = release.digest(manifest)
        (bundle / "RELEASE.json").write_text(json.dumps(metadata))
        with (
            patch.object(release.platform, "system", return_value="Linux"),
            patch.object(
                release, "build_interpreter", side_effect=AssertionError("host build check")
            ),
            patch.object(
                release, "interpreter_identity", side_effect=AssertionError("host identity")
            ),
        ):
            release.check_bundle(bundle, "macos-arm64")
            archive = release.archive_bundle(bundle, self.root / "assets", "0.3.0", "macos-arm64")
            release.extract_archive(archive, self.root / "extracted")
            for profile in (
                None,
                {**metadata["build_interpreter"], "framework": None},
                {**metadata["build_interpreter"], "selection_verified": False},
            ):
                (bundle / "RELEASE.json").write_text(
                    json.dumps({**metadata, "build_interpreter": profile})
                )
                with self.assertRaisesRegex(ValueError, "recorded build interpreter"):
                    release.check_bundle(bundle, "macos-arm64")


if __name__ == "__main__":
    unittest.main()
