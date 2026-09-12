"""Build the distribution and import it without editable source resolution."""

import json
from pathlib import Path
import site
import subprocess
import sys
import zipfile


def test_wheel_runs_without_checkout_or_apm(tmp_path):
    root = Path(__file__).resolve().parents[2]
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    built = subprocess.run(
        [
            sys.executable, "-c",
            "from setuptools.build_meta import build_wheel; "
            "import sys; build_wheel(sys.argv[1])",
            str(wheels),
        ],
        cwd=root, capture_output=True, text=True, timeout=120,
    )
    assert built.returncode == 0, built.stderr
    wheel, = wheels.glob("*.whl")
    installed = tmp_path / "installed"
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert any(name == "apmx/__main__.py" for name in names)
        assert not any(name.startswith("apm_cli/") for name in names)
        assert any(name.endswith("/LICENSE") for name in names)
        assert any(name.endswith("/NOTICE") for name in names)
        archive.extractall(installed)

    script = """
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import runpy
import sys
sys.path[:0] = [sys.argv[1], *json.loads(sys.argv[2])]
import apmx
assert Path(apmx.__file__).is_relative_to(Path(sys.argv[1]))
assert importlib.util.find_spec("apm_cli") is None
dist = importlib.metadata.distribution("apmx")
assert any(ep.name == "apmx" and ep.value == "apmx.cli:main" for ep in dist.entry_points)
assert not any("apm-cli" in req.lower() or "apm_cli" in req.lower() for req in dist.requires or ())
assert (Path(apmx.__file__).parent / "core/_child_tls/_apm_tls_bootstrap.py").is_file()
sys.argv = ["apmx", "--version"]
runpy.run_module("apmx", run_name="__main__")
"""
    result = subprocess.run(
        [
            sys.executable, "-I", "-S", "-c", script, str(installed),
            json.dumps(site.getsitepackages()),
        ],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "apmx, version 0.1.0"
