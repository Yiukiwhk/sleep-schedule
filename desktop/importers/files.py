"""文件导入：JSON / CSV / PDF 文本层。

这些解析器只做"读文件 → 结构化记录"，不做 OCR、不写库；截图识别在 ocr.py。
"""

from __future__ import annotations

import builtins
import csv
import io
import json
import re
from pathlib import Path

from .base import ImportError, ImportRecord, ImportResult

# 本模块的 ImportError 是业务异常，捕获标准库的导入失败要用内建名称。
_MissingModule = builtins.ImportError

DATE_RE = re.compile(r"(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})")
TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3])\s*[:：]\s*([0-5]\d)(?:\s*[:：]\s*[0-5]\d)?(?!\d)")

DATE_KEYS = ("record_date", "date", "day", "日期")
SLEEP_KEYS = ("sleep_time", "sleepTime", "sleep", "bedtime", "入睡时间", "入睡")
WAKE_KEYS = ("wake_time", "wakeTime", "wake", "getup", "起床时间", "起床")
NOTE_KEYS = ("note", "备注")
STAGE_KEYS = {"deep": "deep", "light": "light", "rem": "rem"}


def normalize_date(value: object) -> str | None:
    """把各种日期写法折成 YYYY-MM-DD。"""
    text = str(value).strip()
    if not text:
        return None
    match = DATE_RE.search(text)
    if not match:
        return None
    year, month, day = match.groups()
    try:
        from datetime import date

        parsed = date(int(year), int(month), int(day))
    except ValueError:
        return None
    return parsed.isoformat()


def normalize_time(value: object) -> str | None:
    """把各种时间写法折成 HH:MM（24 小时制）。"""
    text = str(value).strip()
    if not text:
        return None
    match = TIME_RE.search(text)
    if not match:
        return None
    hour, minute = match.groups()
    return f"{int(hour):02d}:{int(minute):02d}"


def _pick(row: dict, keys: tuple[str, ...]):
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for key in keys:
        candidate = lowered.get(key.lower())
        if candidate not in (None, ""):
            return candidate
    return None


def _record_from_mapping(row: dict, *, source: str, confidence: float) -> ImportRecord | None:
    day = normalize_date(_pick(row, DATE_KEYS))
    sleep = normalize_time(_pick(row, SLEEP_KEYS))
    wake = normalize_time(_pick(row, WAKE_KEYS))
    if not day or not sleep or not wake:
        return None
    stages = None
    for key, label in STAGE_KEYS.items():
        raw = row.get(key)
        if raw in (None, ""):
            continue
        try:
            minutes = int(float(raw))
        except (TypeError, ValueError):
            continue
        stages = stages or {}
        stages[label] = minutes
    note = _pick(row, NOTE_KEYS)
    return ImportRecord(
        date=day,
        sleep_time=sleep,
        wake_time=wake,
        stages=stages,
        confidence=confidence,
        source=source,
        note=str(note)[:200] if note else None,
    )


def _dedupe(records: list[ImportRecord], warnings: list[str]) -> list[ImportRecord]:
    seen: dict[str, ImportRecord] = {}
    duplicates = 0
    for record in records:
        if record.date in seen:
            duplicates += 1
            continue
        seen[record.date] = record
    if duplicates:
        warnings.append(f"import.duplicateDates:{duplicates}")
    return list(seen.values())


def _parse_json_text(text: str, source_file: str) -> ImportResult:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ImportError(f"import.bad_json:{exc.msg}", code="import.bad_json") from exc

    rows: list = []
    warnings: list[str] = []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        for key in ("records", "data", "items", "rows"):
            value = payload.get(key)
            if isinstance(value, list):
                rows = value
                break
        else:
            single = _record_from_mapping(payload, source="json", confidence=0.95)
            if single is None:
                raise ImportError("import.no_records", code="import.no_records")
            rows = []
            records = [single]
            return ImportResult(records=records, warnings=[], source_file=source_file)
    else:
        raise ImportError("import.no_records", code="import.no_records")

    records: list[ImportRecord] = []
    skipped = 0
    for row in rows:
        if not isinstance(row, dict):
            skipped += 1
            continue
        # 本应用导出的结构把三餐放在 record 里，这里只关心时间与日期。
        record = _record_from_mapping(row, source="json", confidence=0.95)
        if record is None:
            skipped += 1
            continue
        records.append(record)
    if skipped:
        warnings.append(f"import.skippedRows:{skipped}")
    if not records:
        raise ImportError("import.no_records", code="import.no_records")
    return ImportResult(records=_dedupe(records, warnings), warnings=warnings, source_file=source_file)


def _parse_csv_text(text: str, source_file: str) -> ImportResult:
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ImportError("import.empty_file", code="import.empty_file")

    records: list[ImportRecord] = []
    warnings: list[str] = []
    skipped = 0
    for row in reader:
        record = _record_from_mapping(dict(row), source="csv", confidence=0.9)
        if record is None:
            skipped += 1
            continue
        records.append(record)
    if skipped:
        warnings.append(f"import.skippedRows:{skipped}")
    if not records:
        raise ImportError("import.no_records", code="import.no_records")
    return ImportResult(records=_dedupe(records, warnings), warnings=warnings, source_file=source_file)


def _parse_pdf_bytes(data: bytes, source_file: str) -> ImportResult:
    try:
        from pypdf import PdfReader
    except _MissingModule as exc:  # pragma: no cover - 桌面版装了 pypdf
        raise ImportError("import.pdf_unavailable", code="import.pdf_unavailable") from exc

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 - pypdf 的异常类型很杂
        raise ImportError(f"import.bad_pdf:{exc}", code="import.bad_pdf") from exc

    text = "\n".join(pages).strip()
    if len(text) < 20:
        raise ImportError("import.needs_screenshot", code="import.needs_screenshot")

    records: list[ImportRecord] = []
    warnings: list[str] = []
    current_date: str | None = None
    pending_times: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        day = normalize_date(line)
        if day:
            current_date = day
            pending_times = []
        times = [f"{int(h):02d}:{int(m):02d}" for h, m in TIME_RE.findall(line)]
        if current_date and times:
            pending_times.extend(times)
            if len(pending_times) >= 2:
                records.append(
                    ImportRecord(
                        date=current_date,
                        sleep_time=pending_times[0],
                        wake_time=pending_times[1],
                        stages=None,
                        confidence=0.55,
                        source="pdf",
                    )
                )
                current_date = None
                pending_times = []

    if not records:
        raise ImportError("import.no_records", code="import.no_records")
    warnings.append("import.lowConfidencePdf")
    return ImportResult(records=_dedupe(records, warnings), warnings=warnings, source_file=source_file)


def parse_bytes(data: bytes, filename: str) -> ImportResult:
    """按扩展名解析上传的内容。"""
    suffix = Path(filename).suffix.lower()
    name = Path(filename).name
    if suffix == ".json":
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("gb18030", errors="replace")
        return _parse_json_text(text, name)
    if suffix == ".csv":
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("gb18030", errors="replace")
        return _parse_csv_text(text, name)
    if suffix == ".pdf":
        return _parse_pdf_bytes(data, name)
    raise ImportError(f"import.unsupported_format:{suffix or '?'}", code="import.unsupported_format")


def parse_file(path: Path) -> ImportResult:
    """读本地文件并解析（给脚本/测试用，桌面端走上传接口）。"""
    return parse_bytes(Path(path).read_bytes(), Path(path).name)
