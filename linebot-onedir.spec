# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

PROJECT_NAME = "linebot-app"
PROJECT_ROOT = Path(".").resolve()
ENTRY_POINT = PROJECT_ROOT / "src" / "linebot_app" / "__main__.py"

icon_param = "lioil.ico" if os.path.exists("lioil.ico") else None

hidden_imports = [
    "linebot_app",
    "linebot_app.db",
    "linebot_app.repositories",
    "linebot_app.services",
    "fastapi",
    "uvicorn",
    "uvicorn.logging",
    "pystray",
    "httpx",
    "linebot.v3",
    "linebot.v3.exceptions",
    "linebot.v3.messaging",
    "linebot.v3.webhook",
    "linebot.v3.webhooks",
]

datas = [
    (str(PROJECT_ROOT / ".env.example"), "."),
]
if icon_param:
    datas.append((str(PROJECT_ROOT / icon_param), "."))

a = Analysis(
    [str(ENTRY_POINT)],
    pathex=[str(PROJECT_ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=PROJECT_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_param,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=PROJECT_NAME,
)
