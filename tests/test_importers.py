"""导入器的单元测试：文件解析 + OCR（依赖装了就跑真识别，没装则断言降级行为）。"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from desktop import importers  # noqa: E402
from desktop.importers.base import ImportError as ImportFailure  # noqa: E402

pytestmark = pytest.mark.desktop


def _parse(data: bytes, name: str):
    return importers.parse_bytes(data, name)


def test_json_export_shape_is_parsed() -> None:
    payload = {
        "format": "yiuki-sleep-export",
        "version": 1,
        "records": [
            {"record_date": "2026-11-20", "sleep_time": "23:10", "wake_time": "07:05"},
            {"record_date": "2026-11-21", "sleep_time": "00:30", "wake_time": "08:00", "note": "晚睡"},
        ],
    }
    result = _parse(json.dumps(payload).encode("utf-8"), "backup.json")
    assert [record.date for record in result.records] == ["2026-11-20", "2026-11-21"]
    assert result.records[0].sleep_time == "23:10"
    assert result.records[1].note == "晚睡"
    assert all(record.confidence >= 0.9 for record in result.records)


def test_json_device_shape_and_duplicate_dates() -> None:
    payload = {
        "data": [
            {"date": "2026/11/19", "sleepTime": "22:40", "wakeTime": "6:50"},
            {"date": "2026-11-19", "sleepTime": "23:00", "wakeTime": "07:00"},
            {"date": "2026年11月18日", "sleepTime": "01:00", "wakeTime": "09:00"},
        ]
    }
    result = _parse(json.dumps(payload).encode("utf-8"), "device.json")
    assert len(result.records) == 2
    assert result.records[0].date == "2026-11-19"
    assert result.records[0].wake_time == "06:50"
    assert any(warning.startswith("import.duplicateDates") for warning in result.warnings)


def test_csv_with_stage_columns() -> None:
    csv_text = "date,sleep,wake,deep,light,rem\n2026-11-17,23:05,07:15,95,210,80\n"
    result = _parse(csv_text.encode("utf-8"), "sleep.csv")
    assert len(result.records) == 1
    record = result.records[0]
    assert record.date == "2026-11-17"
    assert record.stages == {"deep": 95, "light": 210, "rem": 80}
    assert record.confidence == pytest.approx(0.9)


def test_csv_with_chinese_headers_and_bom() -> None:
    csv_text = "\ufeff日期,入睡,起床\n2026-11-16,22:30,06:40\n"
    result = _parse(csv_text.encode("utf-8"), "sleep.csv")
    assert result.records[0].date == "2026-11-16"
    assert result.records[0].sleep_time == "22:30"


def test_bad_rows_are_reported_as_warnings() -> None:
    csv_text = "date,sleep,wake\n2026-11-15,23:00,07:00\nnot-a-date,23:00,07:00\n"
    result = _parse(csv_text.encode("utf-8"), "sleep.csv")
    assert len(result.records) == 1
    assert any(warning.startswith("import.skippedRows") for warning in result.warnings)


def test_unsupported_format_and_bad_json() -> None:
    with pytest.raises(ImportFailure) as info:
        _parse(b"hello", "notes.txt")
    assert info.value.code == "import.unsupported_format"

    with pytest.raises(ImportFailure) as info:
        _parse(b"{not json", "broken.json")
    assert info.value.code == "import.bad_json"

    with pytest.raises(ImportFailure) as info:
        _parse(b'{"records": []}', "empty.json")
    assert info.value.code == "import.no_records"


def test_pdf_without_text_layer_asks_for_screenshot() -> None:
    # 一个只有空白页的最小 PDF：pypdf 能读，但抽不出文字层。
    pdf = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF\n"
    )
    with pytest.raises(ImportFailure) as info:
        _parse(pdf, "scan.pdf")
    assert info.value.code in ("import.needs_screenshot", "import.bad_pdf", "import.pdf_unavailable")


def test_ocr_degrades_cleanly_when_dependencies_are_missing() -> None:
    if importers.ocr_available():
        pytest.skip("OCR dependencies installed; the next test covers the enabled path")
    with pytest.raises(ImportFailure) as info:
        _parse(b"\x89PNG\r\n\x1a\nbroken", "screenshot.png")
    assert info.value.code == "import.ocr_unavailable"


def _sample_screenshot() -> bytes:
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    ImageFont = pytest.importorskip("PIL.ImageFont")
    image = Image.new("RGB", (1440, 840), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 64)
    except OSError:  # pragma: no cover - font missing
        font = ImageFont.load_default()
    draw.text((80, 80), "2026-11-20", fill=(20, 20, 20), font=font)
    draw.text((120, 360), "23:10", fill=(20, 20, 20), font=font)
    draw.text((840, 360), "07:05", fill=(20, 20, 20), font=font)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def test_ocr_reads_a_screenshot_when_dependencies_are_installed() -> None:
    if not importers.ocr_available():
        pytest.skip("OCR dependencies not installed")
    result = _parse(_sample_screenshot(), "screenshot.png")
    assert result.records
    assert result.records[0].source == "ocr"
    assert result.records[0].date == "2026-11-20"
    assert "import.ocrReview" in result.warnings
