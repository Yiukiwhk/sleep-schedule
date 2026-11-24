"""导入器的公共数据结构与入口分发。

所有解析器都只负责"把文件变成 ImportRecord 列表"，不碰数据库；落库由 API 层在用户确认后完成。
"""

from __future__ import annotations

import dataclasses
from typing import Any

PARSER_VERSION = "1.0"

# 置信度低于这个值时不默认勾选（前端据此决定复选框初始状态）。
LOW_CONFIDENCE = 0.7


class ImportError(Exception):
    """导入失败时抛出，message 直接展示给用户（i18n key 或中文短句）。"""

    def __init__(self, message: str, *, code: str = "import.failed") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclasses.dataclass(slots=True)
class ImportRecord:
    """一条待确认的作息记录。"""

    date: str
    sleep_time: str
    wake_time: str
    stages: dict[str, Any] | None = None
    confidence: float = 1.0
    source: str = "file"
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "sleep_time": self.sleep_time,
            "wake_time": self.wake_time,
            "stages": self.stages,
            "confidence": round(float(self.confidence), 2),
            "source": self.source,
            "note": self.note,
        }


@dataclasses.dataclass(slots=True)
class ImportResult:
    """一次解析的完整结果。"""

    records: list[ImportRecord]
    warnings: list[str] = dataclasses.field(default_factory=list)
    source_file: str = ""
    parser_version: str = PARSER_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "records": [record.as_dict() for record in self.records],
            "warnings": list(self.warnings),
            "source_file": self.source_file,
            "parser_version": self.parser_version,
            "count": len(self.records),
        }
