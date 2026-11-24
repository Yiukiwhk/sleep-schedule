"""导入器包：文件（JSON/CSV/PDF）与截图（可选 OCR）。

`parse_bytes()` 是唯一入口，按扩展名分发；返回的 `ImportResult` 直接给确认页渲染。
"""

from __future__ import annotations

from . import base, files, ocr
from .base import ImportError, ImportRecord, ImportResult


def parse_bytes(data: bytes, filename: str) -> ImportResult:
    """解析上传的文件或截图。"""
    lowered = filename.lower()
    if lowered.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
        return ocr.parse_bytes(data, filename)
    return files.parse_bytes(data, filename)


def ocr_available() -> bool:
    """截图识别依赖是否就绪。"""
    return ocr.available()


__all__ = [
    "ImportError",
    "ImportRecord",
    "ImportResult",
    "base",
    "files",
    "ocr",
    "ocr_available",
    "parse_bytes",
]
