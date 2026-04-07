# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules, collect_data_files
import sys
from pathlib import Path

ONE_FILE = True  # False = onedir, True = onefile

PROJECT_NAME = 'linebot-app'
PROJECT_ROOT = Path('.').resolve()
ENTRY_POINT = 'src/linebot_app/__main__.py'

hidden_imports = [
    'linebot_app',
    'linebot_app.db',
    'linebot_app.repositories',
    'linebot_app.services',
    'fastapi',
    'uvicorn',
    'uvicorn.logging',
    'httpx',
    'linebot.v3',
    'linebot.v3.exceptions',
    'linebot.v3.messaging',
    'linebot.v3.webhook',
    'linebot.v3.webhooks',
]

datas = [
    (str(PROJECT_ROOT / '.env.example'), '.'),
]

binaries = []

a = Analysis(
    [str(PROJECT_ROOT / ENTRY_POINT)],
    pathex=[str(PROJECT_ROOT / 'src')],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludedimports=[],
    noarchive=False,
)

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=None,
)

if ONE_FILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
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
        icon='lioil.ico',
    )
else:
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
        icon='lioil.ico',
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
