"""截图识别的可选组件。

依赖（`rapidocr-onnxruntime` + `opencv-python-headless`）只在 P3 的桌面安装包里装；
没装时这里直接报 `import.ocr_unavailable`，文件导入（JSON/CSV/PDF 文本层）不受影响。

策略刻意保守：只做"整图缩放到固定宽度 → RapidOCR 找日期与时间行 → 找分期色带行"，
不做版面检测；识别不出来就返回低置信度记录，交给确认页人工改。
"""

from __future__ import annotations

import re
from datetime import date

from .base import ImportError, ImportRecord, ImportResult

DATE_RE = re.compile(r"(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})")
TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3])\s*[:：]\s*([0-5]\d)(?:\s*[:：]\s*[0-5]\d)?(?!\d)")


def available() -> bool:
    """OCR 依赖是否齐全（安装包里有，开发机可能没有）。"""
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
        from rapidocr_onnxruntime import RapidOCR  # noqa: F401
    except Exception:  # noqa: BLE001 - 依赖缺失或 onnxruntime 载入失败都算不可用
        return False
    return True


def _normalize_date(text: str) -> str | None:
    match = DATE_RE.search(text)
    if not match:
        return None
    year, month, day = match.groups()
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def _times(text: str) -> list[str]:
    return [f"{int(hour):02d}:{int(minute):02d}" for hour, minute in TIME_RE.findall(text)]


def parse_image(data: bytes, filename: str) -> ImportResult:
    """识别一张睡眠 App 截图；识别不出就抛 ImportError（前端提示改用文件导入）。"""
    if not available():
        raise ImportError("import.ocr_unavailable", code="import.ocr_unavailable")

    import cv2
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR

    buffer = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise ImportError("import.bad_image", code="import.bad_image")

    # 统一缩放到 720 宽，既保住小字号又控制识别耗时。
    height, width = image.shape[:2]
    if width and width != 720:
        scale = 720.0 / width
        image = cv2.resize(image, (720, max(1, int(height * scale))), interpolation=cv2.INTER_AREA)

    engine = RapidOCR()
    raw, _ = engine(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    lines = []
    for item in raw or []:
        box, text, score = item[0], item[1], float(item[2])
        top = min(point[1] for point in box)
        lines.append((top, text, score))
    lines.sort(key=lambda row: row[0])

    records: list[ImportRecord] = []
    warnings: list[str] = []
    current_date: str | None = None
    pending: list[str] = []
    matched = 0
    for _, text, score in lines[:400]:
        day = _normalize_date(text)
        if day:
            current_date = day
            pending = []
            matched += 1
        times = _times(text)
        if current_date and times:
            pending.extend(times)
            matched += 1
            if len(pending) >= 2:
                records.append(
                    ImportRecord(
                        date=current_date,
                        sleep_time=pending[0],
                        wake_time=pending[1],
                        stages=None,
                        confidence=round(min(0.85, 0.4 + score * 0.5), 2),
                        source="ocr",
                    )
                )
                current_date = None
                pending = []

    if not records:
        raise ImportError("import.ocr_no_match", code="import.ocr_no_match")
    warnings.append("import.ocrReview")
    if len(lines) > 40:
        warnings.append("import.ocrLongList")
    return ImportResult(records=records, warnings=warnings, source_file=filename)


def parse_bytes(data: bytes, filename: str) -> ImportResult:
    """按文件名分发到图片识别。"""
    lowered = filename.lower()
    if not lowered.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
        raise ImportError(f"import.unsupported_format:{lowered.rsplit('.', 1)[-1]}", code="import.unsupported_format")
    return parse_image(data, filename)


__all__ = ["available", "parse_bytes", "parse_image"]
