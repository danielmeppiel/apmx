"""Build the distribution and import it without editable source resolution."""

import json
import site
import subprocess
import sys
import zipfile
from pathlib import Path


def test_wheel_runs_without_checkout_or_apm(tmp_path):
    root = Path(__file__).resolve().parents[2]
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    built = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "from setuptools.build_meta import build_wheel; import sys; build_wheel(sys.argv[1])",
            str(wheels),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    (wheel,) = wheels.glob("*.whl")
    installed = tmp_path / "installed"
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert any(name == "apmx/__main__.py" for name in names)
        assert "apmx/apm-backend.json" in names
        assert (
            archive.read("apmx/apm-backend.json")
            == (root / "src/apmx/apm-backend.json").read_bytes()
        )
        assert not any(name.startswith("apm_cli/") for name in names)
        for filename in ("LICENSE", "NOTICE"):
            (entry,) = [name for name in names if name.endswith(f"/{filename}")]
            assert archive.read(entry) == (root / filename).read_bytes()
        (license_entry,) = [name for name in names if name.endswith("/LICENSE")]
        assert b"Apache License" in archive.read(license_entry)
        (notice_entry,) = [name for name in names if name.endswith("/NOTICE")]
        notices = archive.read(notice_entry)
        assert b"Copyright (c) 2026 Daniel Meppiel." in notices
        assert b"Copyright (c) Microsoft Corporation." in notices
        assert b"Permission is hereby granted, free of charge" in notices
        assert not any(name.startswith("WIP/") or "/WIP/" in name for name in names)
        archive.extractall(installed)

    script = """
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import runpy
import sys
sys.path[:0] = [sys.argv[1], *json.loads(sys.argv[2])]
import apmx
assert Path(apmx.__file__).is_relative_to(Path(sys.argv[1]))
assert importlib.util.find_spec("apm_cli") is None
dist = importlib.metadata.distribution("apmx")
assert dist.metadata["License-Expression"] == "Apache-2.0 AND MIT"
assert any(ep.name == "apmx" and ep.value == "apmx.cli:main" for ep in dist.entry_points)
assert not any("apm-cli" in req.lower() or "apm_cli" in req.lower() for req in dist.requires or ())
assert (Path(apmx.__file__).parent / "core/_child_tls/_apm_tls_bootstrap.py").is_file()
from apmx.install.apm_backend import PIN_PATH, locate_backend
from apmx.contracts.models import ContractError
pin = json.loads(PIN_PATH.read_text())
assert pin["schema"] == "apmx-apm-backend/1"
assert pin["version"] == "0.30.0"
assert pin["source_commit"] == "8c2e0d9c352e2ed0e8c56b40063a63e1dd4a1937"
os.environ.pop("APMX_APM_BACKEND", None)
os.environ["PATH"] = str(Path.cwd())
try:
    locate_backend()
except ContractError as exc:
    assert exc.code == "apm_backend_missing"
else:
    raise AssertionError("Source-free wheel found an unprovisioned backend")
sys.argv = ["apmx", "--version"]
runpy.run_module("apmx", run_name="__main__")
"""
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-I",
            "-S",
            "-c",
            script,
            str(installed),
            json.dumps(site.getsitepackages()),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "apmx, version 0.4.1"
