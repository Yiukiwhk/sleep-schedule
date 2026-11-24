"""Windows DPAPI helpers: keep the cloud device token encrypted at rest.

The token is the only long-lived secret the desktop app stores; it lives in
``device.json`` as base64 of a DPAPI blob (current-user scope, so another
Windows account on the same PC cannot read it even though the database folder
is machine-wide).
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

ENTROPY = b"yiuki-sleep/device-token/v1"


class DpapiError(RuntimeError):
    """DPAPI is unavailable or the blob could not be (un)protected."""


def _load_windows_api():
    if os.name != "nt":
        raise DpapiError("DPAPI is only available on Windows")
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def _blob(data: bytes, keepalive: list):
        buffer = ctypes.create_string_buffer(data, len(data))
        keepalive.append(buffer)
        blob = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        return blob

    return ctypes, wintypes, crypt32, kernel32, DataBlob, _blob


def protect(data: bytes) -> bytes:
    """Encrypt bytes for the current Windows user (CryptProtectData)."""
    ctypes, wintypes, crypt32, kernel32, DataBlob, _blob = _load_windows_api()
    keepalive: list = []
    blob_in = _blob(bytes(data), keepalive)
    entropy = _blob(ENTROPY, keepalive)
    blob_out = DataBlob()
    ok = crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        "yiuki-sleep",
        ctypes.byref(entropy),
        None,
        None,
        0,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise DpapiError(f"CryptProtectData failed: {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def unprotect(blob: bytes) -> bytes:
    """Decrypt bytes produced by :func:`protect` for the current Windows user."""
    ctypes, wintypes, crypt32, kernel32, DataBlob, _blob = _load_windows_api()
    keepalive: list = []
    blob_in = _blob(bytes(blob), keepalive)
    entropy = _blob(ENTROPY, keepalive)
    blob_out = DataBlob()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        ctypes.byref(entropy),
        None,
        None,
        0,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise DpapiError(f"CryptUnprotectData failed: {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def save_device_token(path: Path, payload: dict) -> None:
    """Persist the device binding (server url, username, token) encrypted."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    document = {"v": 1, "dpapi": base64.b64encode(protect(raw)).decode("ascii")}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(document, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_device_token(path: Path) -> dict | None:
    """Return the stored binding, or None when absent / unreadable."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        raw = unprotect(base64.b64decode(document["dpapi"]))
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, KeyError, DpapiError):
        return None
    return payload if isinstance(payload, dict) else None


def clear_device_token(path: Path) -> None:
    try:
        Path(path).unlink()
    except OSError:
        pass
