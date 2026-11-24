# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Yiuki Sleep desktop shell (onedir).

Build:  pyinstaller --noconfirm --clean desktop/build/app.spec --distpath dist/desktop --workpath build/pyinstaller
The bundle contains the shell (desktop/app.py), the backend package (backend/app)
and the static frontend (frontend/); user data never lives in the bundle.
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent.parent  # desktop/build -> repo root

hidden = [
    "app.main",
    # uvicorn loads its loop/protocol/lifespan implementations dynamically.
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    # pywebview picks its GUI backend at runtime (EdgeChromium on Windows).
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
]

a = Analysis(
    [str(ROOT / "desktop" / "app.py")],
    pathex=[str(ROOT), str(ROOT / "backend"), str(ROOT / "desktop")],
    binaries=[],
    datas=[(str(ROOT / "frontend"), "frontend")],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "cv2", "rapidocr_onnxruntime"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="YiukiSleep",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="YiukiSleep",
)
