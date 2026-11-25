"""云同步协议实现（见 docs/DESKTOP.md 第 7 节）。

纯函数 + SQLAlchemy 会话操作，不含 FastAPI 依赖；HTTP 路由在 api/sync_api.py。
同步的三张表：
  * sleep_records —— 业务键 (user_id, record_date)
  * meal_checks   —— 业务键 (user_id, check_date, meal)
  * user_settings —— 每用户一行，payload 是设置 JSON

行标识用客户端生成的 client_row_id（UUID v4，永不变）；冲突按 sync_updated_at
做 LWW，相等时比客户端 client_id 字符串大者胜；墓碑（deleted_at 非空）优先。
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import date, datetime, timezone
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..version import MIN_SUPPORTED, SCHEMA_VERSION, VERSION

SYNC_TABLES = ("sleep_records", "meal_checks", "user_settings", "advice_plans")
MAX_ROWS_PER_PUSH = 500
MAX_PAYLOAD_BYTES = 64 * 1024
TOMBSTONE_DAYS = 180
MAX_PAST_DAYS = 3650

DATE_RE = r"^\d{4}-\d{2}-\d{2}$"
TIME_RE = r"^\d{2}:\d{2}$"


# --------------------------------------------------------------------------- #
# Time helpers
# --------------------------------------------------------------------------- #
def utc_iso_ms(moment: datetime | None = None) -> str:
    """UTC ISO8601 带毫秒，形如 2026-01-02T03:04:05.678Z（naive 时间按 UTC 解释）。"""
    current = moment or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    return current.strftime("%Y-%m-%dT%H:%M:%S.") + f"{current.microsecond // 1000:03d}Z"


def parse_iso_ms(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_newer(incoming: datetime | None, current: datetime | None, *, tie: int = 0) -> bool:
    """LWW：incoming 严格更新，或同刻且 tie > 0 时胜出。"""
    if current is None:
        return True
    if incoming is None:
        return False
    if incoming > current:
        return True
    if incoming < current:
        return False
    return tie > 0


def parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def parse_time(value: Any) -> str | None:
    text = str(value or "").strip()
    if len(text) != 5 or text[2] != ":":
        return None
    if not (text[:2].isdigit() and text[3:].isdigit()):
        return None
    if int(text[:2]) > 23 or int(text[3:]) > 59:
        return None
    return text


# --------------------------------------------------------------------------- #
# Row payloads
# --------------------------------------------------------------------------- #
def record_payload(row: models.SleepRecord) -> dict[str, Any]:
    return {
        "record_date": row.record_date,
        "sleep_time": row.sleep_time,
        "wake_time": row.wake_time,
        "wake_date": row.wake_date,
        "meal_date": row.meal_date,
        "meal_breakfast": bool(row.meal_breakfast),
        "meal_lunch": bool(row.meal_lunch),
        "meal_dinner": bool(row.meal_dinner),
        "note": row.note,
    }


def meal_payload(row: models.MealCheck) -> dict[str, Any]:
    return {
        "check_date": row.check_date,
        "meal": row.meal,
        "eaten": bool(row.eaten),
        "refused": bool(row.refused),
    }


def settings_payload(user: models.User) -> dict[str, Any]:
    return {
        "target_sleep_time": user.target_sleep_time,
        "target_wake_time": user.target_wake_time,
        "language": user.language,
        "theme": user.theme,
        "meal_breakfast": bool(user.meal_breakfast),
        "meal_lunch": bool(user.meal_lunch),
        "meal_dinner": bool(user.meal_dinner),
        "meal_anchor": bool(user.meal_anchor),
    }


def plan_payload(row: models.AdvicePlan) -> dict[str, Any]:
    """当前活跃方案的完整快照。只同步"正在使用"的那一份，历史快照留在本机。"""
    try:
        plan = json.loads(row.payload) if row.payload else {}
    except (TypeError, ValueError):
        plan = {}
    return {
        "mode": row.mode,
        "days": int(row.days),
        "start_date": row.start_date,
        "target_date": row.target_date,
        "target_sleep_time": row.target_sleep_time,
        "target_wake_time": row.target_wake_time,
        "engine": row.engine,
        "replans": int(row.replans or 0),
        "postponed_days": int(row.postponed_days or 0),
        "plan": plan,
    }


def tombstone_at(row: Any) -> str | None:
    return utc_iso_ms(row.deleted_at) if row.deleted_at else None


# --------------------------------------------------------------------------- #
# Validation (DESKTOP.md 7.4)
# --------------------------------------------------------------------------- #
def _violation(code: str, table: str, client_row_id: str, message: str) -> dict[str, str]:
    return {"code": code, "table": table, "client_row_id": client_row_id, "message": message}


def _check_date(value: Any, *, today: date, future_days: int = 1) -> bool:
    parsed = parse_date(value)
    if parsed is None:
        return False
    return (today - parsed).days <= MAX_PAST_DAYS and (parsed - today).days <= future_days


def validate_rows(
    rows: list[dict[str, Any]],
    *,
    today: date,
    known_record_dates: set[str] | None = None,
) -> list[dict[str, str]]:
    """逐行校验；返回空列表表示整批可以落地。"""
    violations: list[dict[str, str]] = []
    if len(rows) > MAX_ROWS_PER_PUSH:
        violations.append(_violation("sync.too_many_rows", "", "", f"单次最多 {MAX_ROWS_PER_PUSH} 行"))
        return violations
    seen: set[tuple[str, str]] = set()
    for row in rows:
        table = str(row.get("table") or "")
        cid = str(row.get("client_row_id") or "")
        if table not in SYNC_TABLES:
            violations.append(_violation("sync.unknown_table", table, cid, "不支持的表"))
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            violations.append(_violation("sync.bad_payload", table, cid, "payload 必须是对象"))
            continue
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_PAYLOAD_BYTES:
            violations.append(
                _violation("sync.payload_too_large", table, cid, "单行数据超过 64KB")
            )
            continue
        key = (table, cid)
        if key in seen:
            violations.append(_violation("sync.duplicate_row", table, cid, "同一批里重复的行"))
            continue
        seen.add(key)
        if table == "sleep_records":
            violations.extend(_validate_record(payload, cid, today, parse_date, parse_time))
        elif table == "meal_checks":
            violations.extend(
                _validate_meal(payload, cid, today, parse_date, known_record_dates or set())
            )
        elif table == "advice_plans":
            violations.extend(_validate_plan(payload, cid, parse_date))
        else:
            violations.extend(_validate_settings(payload, cid))
    return violations


def _validate_record(payload: dict, cid: str, today: date, parse_date, parse_time) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []
    record_date = payload.get("record_date")
    if not _check_date(record_date, today=today):
        problems.append(_violation("sync.bad_date", "sleep_records", cid, "record_date 超出允许范围"))
        return problems
    if parse_time(payload.get("sleep_time")) is None or parse_time(payload.get("wake_time")) is None:
        problems.append(_violation("sync.bad_time", "sleep_records", cid, "sleep_time/wake_time 必须是 HH:MM"))
    wake_date = payload.get("wake_date")
    if wake_date not in (None, ""):
        parsed_wake = parse_date(wake_date)
        if parsed_wake is None:
            problems.append(_violation("sync.bad_date", "sleep_records", cid, "wake_date 不是日期"))
        else:
            if parsed_wake > today:
                problems.append(_violation("sync.wake_in_future", "sleep_records", cid, "wake_date 不能是未来"))
            if parsed_wake < parse_date(record_date):
                problems.append(_violation("sync.wake_before_record", "sleep_records", cid, "wake_date 早于 record_date"))
    note = payload.get("note")
    if note is not None and len(str(note)) > 200:
        problems.append(_violation("sync.note_too_long", "sleep_records", cid, "备注最多 200 字"))
    return problems


def _validate_meal(payload: dict, cid: str, today: date, parse_date, known_record_dates: set[str]) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []
    check_date = payload.get("check_date")
    if not _check_date(check_date, today=today):
        problems.append(_violation("sync.bad_date", "meal_checks", cid, "check_date 超出允许范围"))
        return problems
    if payload.get("meal") not in ("breakfast", "lunch", "dinner"):
        problems.append(_violation("sync.bad_meal", "meal_checks", cid, "meal 必须是 breakfast/lunch/dinner"))
    if known_record_dates and str(check_date) not in known_record_dates:
        problems.append(_violation("sync.meal_without_record", "meal_checks", cid, "这一天还没有睡眠记录"))
    return problems


def _validate_settings(payload: dict, cid: str) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []
    for key in ("target_sleep_time", "target_wake_time"):
        value = payload.get(key)
        if value in (None, ""):
            continue
        text = str(value)
        if len(text) != 5 or text[2] != ":" or not text[:2].isdigit() or not text[3:].isdigit():
            problems.append(_violation("sync.bad_time", "user_settings", cid, f"{key} 必须是 HH:MM"))
    if payload.get("language") not in (None, "zh", "en"):
        problems.append(_violation("sync.bad_settings", "user_settings", cid, "language 只能是 zh/en"))
    if payload.get("theme") not in (None, "light", "dark"):
        problems.append(_violation("sync.bad_settings", "user_settings", cid, "theme 只能是 light/dark"))
    return problems


def _validate_plan(payload: dict, cid: str, parse_date) -> list[dict[str, str]]:
    """方案快照：只校验结构，内容由引擎自己解释（换引擎时不做过度限制）。"""
    problems: list[dict[str, str]] = []
    plan = payload.get("plan")
    if not isinstance(plan, dict) or not plan:
        problems.append(_violation("sync.bad_plan", "advice_plans", cid, "plan 必须是非空对象"))
        return problems
    start = payload.get("start_date")
    if parse_date(start) is None:
        problems.append(_violation("sync.bad_date", "advice_plans", cid, "start_date 不是日期"))
    target = payload.get("target_date")
    if target not in (None, "") and parse_date(target) is None:
        problems.append(_violation("sync.bad_date", "advice_plans", cid, "target_date 不是日期"))
    for key in ("target_sleep_time", "target_wake_time"):
        text = str(payload.get(key) or "")
        if len(text) != 5 or text[2] != ":" or not text[:2].isdigit() or not text[3:].isdigit():
            problems.append(_violation("sync.bad_time", "advice_plans", cid, f"{key} 必须是 HH:MM"))
    for key in ("mode", "engine"):
        text = str(payload.get(key) or "")
        if not text or len(text) > 16:
            problems.append(_violation("sync.bad_plan", "advice_plans", cid, f"{key} 长度不对"))
    return problems


# --------------------------------------------------------------------------- #
# Apply (push)
# --------------------------------------------------------------------------- #
def _append_log(session: Session, user_id: int, table: str, client_row_id: str, op: str) -> int:
    entry = models.SyncLog(
        user_id=user_id, table_name=table, client_row_id=client_row_id, op=op
    )
    session.add(entry)
    session.flush()
    return int(entry.rev)


# --------------------------------------------------------------------------- #
# Server-side writes
# --------------------------------------------------------------------------- #
def stamp_row(
    session: Session, user_id: int, table: str, row: Any, *, op: str = "upsert"
) -> int:
    """给"不是经由同步接口"落库的行打上同步标记。

    网页/桌面本地改一条记录时也必须推进 rev，否则 pull 的 `rev > cursor`
    永远查不到它——桌面端就会一直上报 pushed/pulled 0。op 用 "upsert" 或
    "delete"（删除走墓碑，见 records.py），和同步接口写日志的口径保持一致。
    """
    if not row.client_row_id:
        row.client_row_id = uuid4().hex
    row.sync_updated_at = utc_iso_ms()
    rev = _append_log(session, user_id, table, row.client_row_id, op)
    row.sync_rev = rev
    return rev


def touch_settings(session: Session, user: models.User, *, op: str = "upsert") -> int:
    """把用户设置写进 user_settings 行（每用户一行，payload 是设置 JSON）。"""
    row = session.execute(
        select(models.SyncSettings).where(models.SyncSettings.user_id == user.id)
    ).scalar_one_or_none()
    if row is None:
        row = models.SyncSettings(user_id=user.id, client_row_id=uuid4().hex, sync_rev=0)
        session.add(row)
        session.flush()
    row.payload = json.dumps(settings_payload(user), ensure_ascii=False, sort_keys=True)
    return stamp_row(session, user.id, "user_settings", row, op=op)


def backfill_missing(session: Session) -> dict[str, int]:
    """给同步功能上线前就存在的行补 rev/时间戳，并给每个用户补一行设置。

    幂等：只处理 sync_rev == 0 的行、还没有 SyncSettings 的用户。没有这一步，
    历史数据（比如线上 52 条记录）的 rev 全是 0，`rev > cursor` 一条也拉不到。
    """
    counts = {"sleep_records": 0, "meal_checks": 0, "user_settings": 0, "advice_plans": 0}
    for row in session.execute(
        select(models.SleepRecord).where(models.SleepRecord.sync_rev == 0)
    ).scalars():
        stamp_row(session, int(row.user_id), "sleep_records", row)
        counts["sleep_records"] += 1
    for row in session.execute(
        select(models.MealCheck).where(models.MealCheck.sync_rev == 0)
    ).scalars():
        stamp_row(session, int(row.user_id), "meal_checks", row)
        counts["meal_checks"] += 1
    for row in session.execute(
        select(models.AdvicePlan).where(
            models.AdvicePlan.sync_rev == 0,
            models.AdvicePlan.is_active.is_(True),
            models.AdvicePlan.deleted_at.is_(None),
        )
    ).scalars():
        stamp_row(session, int(row.user_id), "advice_plans", row)
        counts["advice_plans"] += 1
    for user in session.execute(select(models.User)).scalars():
        exists = session.execute(
            select(models.SyncSettings.id).where(models.SyncSettings.user_id == user.id)
        ).scalar_one_or_none()
        if exists is None:
            touch_settings(session, user)
            counts["user_settings"] += 1
    return counts


def _find_record(session: Session, user_id: int, cid: str, payload: dict) -> models.SleepRecord | None:
    row = session.execute(
        select(models.SleepRecord).where(
            models.SleepRecord.user_id == user_id, models.SleepRecord.client_row_id == cid
        )
    ).scalar_one_or_none()
    if row is None and payload.get("record_date"):
        row = session.execute(
            select(models.SleepRecord).where(
                models.SleepRecord.user_id == user_id,
                models.SleepRecord.record_date == str(payload["record_date"]),
            )
        ).scalar_one_or_none()
        if row is not None and not row.client_row_id:
            row.client_row_id = cid
    return row


def _find_meal(session: Session, user_id: int, cid: str, payload: dict) -> models.MealCheck | None:
    row = session.execute(
        select(models.MealCheck).where(
            models.MealCheck.user_id == user_id, models.MealCheck.client_row_id == cid
        )
    ).scalar_one_or_none()
    if row is None:
        row = session.execute(
            select(models.MealCheck).where(
                models.MealCheck.user_id == user_id,
                models.MealCheck.check_date == str(payload.get("check_date")),
                models.MealCheck.meal == str(payload.get("meal")),
            )
        ).scalar_one_or_none()
        if row is not None and not row.client_row_id:
            row.client_row_id = cid
    return row


def apply_rows(
    session: Session,
    user: models.User,
    rows: Iterable[dict[str, Any]],
    *,
    today: date,
) -> tuple[list[dict[str, Any]], int]:
    """把校验通过的行落到库；返回 (逐行结果, 本次最大 rev)。"""
    results: list[dict[str, Any]] = []
    cursor = 0
    for row in rows:
        table = str(row["table"])
        cid = str(row["client_row_id"])
        incoming = parse_iso_ms(row.get("updated_at"))
        deleted = parse_iso_ms(row.get("deleted_at"))
        payload = row.get("payload") or {}
        if table == "sleep_records":
            existing = _find_record(session, user.id, cid, payload)
        elif table == "meal_checks":
            existing = _find_meal(session, user.id, cid, payload)
        elif table == "advice_plans":
            existing = _find_plan(session, user.id, cid)
        else:
            existing = session.execute(
                select(models.SyncSettings).where(models.SyncSettings.user_id == user.id)
            ).scalar_one_or_none()
        current = parse_iso_ms(getattr(existing, "sync_updated_at", None)) if existing else None
        # 墓碑优先：同一时刻墓碑胜出，因此删除时给 tie 加 1。
        wins = is_newer(incoming, current, tie=1 if deleted else 0)
        if not wins:
            results.append({"table": table, "client_row_id": cid, "status": "stale"})
            continue
        if deleted is not None:
            if existing is None:
                existing = _new_stub(session, user.id, table, cid)
            existing.deleted_at = deleted
            if table == "advice_plans":
                existing.is_active = False
            existing.sync_updated_at = utc_iso_ms(incoming)
            rev = _append_log(session, user.id, table, cid, "delete")
        else:
            if existing is None:
                existing = _new_stub(session, user.id, table, cid)
            _apply_payload(table, existing, payload)
            if table == "user_settings":
                # 设置既要进同步行，也要写回 User（网页端读的是 User 行）。
                # 用当前写会话里的 User，调用方传进来的可能属于另一个会话。
                owner = session.get(models.User, user.id)
                if owner is not None:
                    _apply_settings_to_user(owner, payload)
            existing.deleted_at = None
            if table == "advice_plans":
                # 方案是"每用户一份活跃快照"：落地的这份变活跃，其余置非活跃。
                for other in session.execute(
                    select(models.AdvicePlan).where(
                        models.AdvicePlan.user_id == user.id,
                        models.AdvicePlan.is_active.is_(True),
                        models.AdvicePlan.id != existing.id,
                    )
                ).scalars():
                    other.is_active = False
                existing.is_active = True
            existing.sync_updated_at = utc_iso_ms(incoming)
            rev = _append_log(session, user.id, table, cid, "upsert")
        existing.client_row_id = cid
        existing.sync_rev = rev
        cursor = max(cursor, rev)
        results.append({"table": table, "client_row_id": cid, "status": "applied", "rev": rev})
    return results, cursor


def _new_stub(session: Session, user_id: int, table: str, cid: str) -> Any:
    if table == "sleep_records":
        stub = models.SleepRecord(
            user_id=user_id,
            client_row_id=cid,
            record_date="1970-01-01",
            sleep_time="00:00",
            wake_time="00:00",
            meal_breakfast=False,
            meal_lunch=False,
            meal_dinner=False,
        )
    elif table == "meal_checks":
        stub = models.MealCheck(
            user_id=user_id,
            client_row_id=cid,
            check_date="1970-01-01",
            meal="breakfast",
            eaten=False,
            refused=False,
        )
    elif table == "advice_plans":
        stub = models.AdvicePlan(
            user_id=user_id,
            client_row_id=cid,
            is_active=False,
            mode="slow",
            days=1,
            start_date="1970-01-01",
            target_date="1970-01-01",
            target_sleep_time="00:00",
            target_wake_time="00:00",
            engine="stub",
            payload="{}",
        )
    else:
        stub = models.SyncSettings(user_id=user_id, client_row_id=cid)
    session.add(stub)
    session.flush()
    return stub


def _apply_payload(table: str, row: Any, payload: dict) -> None:
    if table == "sleep_records":
        row.record_date = str(payload.get("record_date"))
        row.sleep_time = str(payload.get("sleep_time"))
        row.wake_time = str(payload.get("wake_time"))
        row.wake_date = payload.get("wake_date") or None
        row.meal_date = payload.get("meal_date") or None
        row.meal_breakfast = bool(payload.get("meal_breakfast", True))
        row.meal_lunch = bool(payload.get("meal_lunch", True))
        row.meal_dinner = bool(payload.get("meal_dinner", True))
        row.note = payload.get("note") or None
    elif table == "meal_checks":
        row.check_date = str(payload.get("check_date"))
        row.meal = str(payload.get("meal"))
        row.eaten = bool(payload.get("eaten"))
        row.refused = bool(payload.get("refused"))
    elif table == "advice_plans":
        row.mode = str(payload.get("mode") or "slow")
        row.days = int(payload.get("days") or 1)
        row.start_date = str(payload.get("start_date"))
        row.target_date = str(payload.get("target_date") or payload.get("start_date"))
        row.target_sleep_time = str(payload.get("target_sleep_time") or "00:00")
        row.target_wake_time = str(payload.get("target_wake_time") or "00:00")
        row.engine = str(payload.get("engine") or "sync")
        row.replans = int(payload.get("replans") or 0)
        row.postponed_days = int(payload.get("postponed_days") or 0)
        row.payload = json.dumps(payload.get("plan") or {}, ensure_ascii=False, default=str)
    else:
        row.payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _apply_settings_to_user(user: models.User, payload: dict[str, Any]) -> None:
    """把设置行里的字段写回服务端 User，网页端与后续 pull 才看得到。"""
    for key in (
        "target_sleep_time",
        "target_wake_time",
        "language",
        "theme",
        "meal_breakfast",
        "meal_lunch",
        "meal_dinner",
        "meal_anchor",
    ):
        if key in payload and payload[key] is not None:
            setattr(user, key, payload[key])


def _find_plan(session: Session, user_id: int, cid: str) -> models.AdvicePlan | None:
    """方案按"每用户一份活跃快照"处理：先按 client_row_id 找，再退回当前活跃行。"""
    row = session.execute(
        select(models.AdvicePlan).where(
            models.AdvicePlan.user_id == user_id,
            models.AdvicePlan.client_row_id == cid,
        )
    ).scalars().first()
    if row is not None:
        return row
    return session.execute(
        select(models.AdvicePlan)
        .where(models.AdvicePlan.user_id == user_id, models.AdvicePlan.is_active.is_(True))
        .order_by(models.AdvicePlan.id.desc())
    ).scalars().first()


def _current_client_id(session: Session, user_id: int, table: str, cid: str) -> str:
    """服务端不记录"哪个客户端写的"；tie-break 用请求里带的行级 client_id。"""
    return ""


# --------------------------------------------------------------------------- #
# Pull
# --------------------------------------------------------------------------- #
def pull_rows(
    session: Session, user: models.User, *, cursor: int, limit: int = MAX_ROWS_PER_PUSH
) -> tuple[list[dict[str, Any]], int, bool]:
    if cursor < 0:
        cursor = 0
    gathered: list[tuple[int, dict[str, Any]]] = []
    for row in session.execute(
        select(models.SleepRecord)
        .where(models.SleepRecord.user_id == user.id, models.SleepRecord.sync_rev > cursor)
        .order_by(models.SleepRecord.sync_rev)
        .limit(limit)
    ).scalars():
        gathered.append(
            (row.sync_rev, _row_payload("sleep_records", row, record_payload(row)))
        )
    for row in session.execute(
        select(models.MealCheck)
        .where(models.MealCheck.user_id == user.id, models.MealCheck.sync_rev > cursor)
        .order_by(models.MealCheck.sync_rev)
        .limit(limit)
    ).scalars():
        gathered.append((row.sync_rev, _row_payload("meal_checks", row, meal_payload(row))))
    settings_row = session.execute(
        select(models.SyncSettings).where(
            models.SyncSettings.user_id == user.id, models.SyncSettings.sync_rev > cursor
        )
    ).scalar_one_or_none()
    if settings_row is not None:
        payload = json.loads(settings_row.payload) if settings_row.payload else {}
        gathered.append((settings_row.sync_rev, _row_payload("user_settings", settings_row, payload)))
    for row in session.execute(
        select(models.AdvicePlan)
        .where(models.AdvicePlan.user_id == user.id, models.AdvicePlan.sync_rev > cursor)
        .order_by(models.AdvicePlan.sync_rev)
        .limit(limit)
    ).scalars():
        gathered.append((row.sync_rev, _row_payload("advice_plans", row, plan_payload(row))))
    gathered.sort(key=lambda item: item[0])
    page = gathered[:limit]
    new_cursor = page[-1][0] if page else cursor
    has_more = len(gathered) > limit
    return [item[1] for item in page], new_cursor, has_more


def active_plan_row(session: Session, user: models.User) -> dict[str, Any] | None:
    """当前活跃方案的一行（给桌面端在绑定阶段做"用哪份方案"的选择）。"""
    row = session.execute(
        select(models.AdvicePlan)
        .where(
            models.AdvicePlan.user_id == user.id,
            models.AdvicePlan.is_active.is_(True),
            models.AdvicePlan.deleted_at.is_(None),
        )
        .order_by(models.AdvicePlan.id.desc())
    ).scalars().first()
    if row is None:
        return None
    return _row_payload("advice_plans", row, plan_payload(row))


def active_settings_row(session: Session, user: models.User) -> dict[str, Any] | None:
    """当前设置行（目标作息等），给桌面端"选云端方案同时改目标作息"用。"""
    row = session.execute(
        select(models.SyncSettings).where(models.SyncSettings.user_id == user.id)
    ).scalars().first()
    if row is None or row.deleted_at is not None:
        return None
    return _row_payload("user_settings", row, settings_payload(user))


def _row_payload(table: str, row: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "table": table,
        "client_row_id": row.client_row_id or "",
        "updated_at": row.sync_updated_at or "",
        "deleted_at": tombstone_at(row),
        "payload": payload,
        "rev": int(row.sync_rev),
    }


# --------------------------------------------------------------------------- #
# Device tokens
# --------------------------------------------------------------------------- #
def new_device_token() -> str:
    return secrets.token_urlsafe(32)


def device_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def status_payload(user: models.User, *, now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc).replace(tzinfo=None)
    fresh = (current - user.created_at).total_seconds() < 24 * 3600
    return {
        "ok": True,
        "server_version": VERSION,
        "min_client": MIN_SUPPORTED,
        "schema_version": SCHEMA_VERSION,
        "quota": {"rows_per_push": MAX_ROWS_PER_PUSH, "payload_bytes": MAX_PAYLOAD_BYTES},
        "new_account_grace": fresh,
        "violations": [],
    }
