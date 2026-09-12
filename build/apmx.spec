# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
analysis = Analysis(
    [str(root / "build/entrypoint.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=collect_data_files("apmx") + collect_data_files(
        "apmx", include_py_files=True, includes=["core/_child_tls/*.py"],
    ) + copy_metadata("apmx") + [
        (str(root / "pyproject.toml"), "."),
    ],
    hiddenimports=collect_submodules("apmx") + collect_submodules("rich._unicode_data"),
    hookspath=[],
    runtime_hooks=[],
    excludes=["apm_cli", "pytest", "PyInstaller", "tkinter"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="apmx",
    console=True,
    debug=False,
    strip=False,
    upx=False,
    codesign_identity=None,
    entitlements_file=None,
)
COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    name="apmx",
    strip=False,
    upx=False,
)
