# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


root = Path(SPECPATH)
package = root / "plan_obfuscator"

datas = [
    (str(package / "templates"), "plan_obfuscator/templates"),
    (str(package / "static"), "plan_obfuscator/static"),
    (str(package / "migrations"), "plan_obfuscator/migrations"),
]

hiddenimports = (
    collect_submodules("sqlglot.dialects")
    + collect_submodules(
        "alembic", filter=lambda name: not name.startswith("alembic.testing")
    )
    + collect_submodules("uvicorn")
)

a = Analysis(
    [str(root / "launcher.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PIL", "pytest"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PlanObfuscator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
