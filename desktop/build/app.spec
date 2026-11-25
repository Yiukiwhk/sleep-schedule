# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Yiuki Sleep desktop shell (onedir).

Build:  pyinstaller --noconfirm --clean desktop/build/app.spec --distpath dist/desktop --workpath build/pyinstaller
The bundle contains the shell (desktop/app.py), the backend package (backend/app)
and the static frontend (frontend/); user data never lives in the bundle.
"""

import importlib.util
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).resolve().parent.parent  # desktop/build -> repo root


def _installed(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


# Screenshot import is optional: when the OCR stack is installed in the build
# venv it ships inside the bundle, otherwise the UI reports import.ocr_unavailable.
OCR = _installed("rapidocr_onnxruntime")

hidden = [
    "app.main",
    # The backend imports these lazily (only the desktop app has them), so the
    # analysis cannot see them; without them sync/import/update return 503.
    "desktop.sync",
    "desktop.updater",
    "desktop.security",
    "desktop.importers",
    "desktop.importers.base",
    "desktop.importers.files",
    "desktop.importers.ocr",
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
    # clr_loader talks to the .NET host through cffi, which it imports inside a
    # try/except; without these the window dies with "No module named 'cffi'".
    "cffi",
    "_cffi_backend",
]

datas = [(str(ROOT / "frontend"), "frontend")]

if OCR:
    hidden += [
        "cv2",
        "numpy",
        "onnxruntime",
        "onnxruntime.capi._pybind_state",
        "rapidocr_onnxruntime",
        "yaml",
        "shapely",
        "pyclipper",
        "six",
    ]
    # rapidocr ships its ONNX models and config yaml as package data.
    datas += collect_data_files("rapidocr_onnxruntime")

a = Analysis(
    [str(ROOT / "desktop" / "app.py")],
    pathex=[str(ROOT), str(ROOT / "backend"), str(ROOT / "desktop")],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

if OCR:
    # We only decode still images, never video: the bundled FFmpeg backend is
    # 27 MB of dead weight in the installer.
    a.binaries = [item for item in a.binaries if "opencv_videoio_ffmpeg" not in item[0].lower()]

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
