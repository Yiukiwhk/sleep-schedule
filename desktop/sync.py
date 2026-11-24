"""云同步客户端（docs/DESKTOP.md 第 6、7 节）。

职责：
  * 绑定/解绑云端账号：登录远端账号拿会话 cookie → 申请设备令牌 → 用 DPAPI 存本机；
  * push：把本地"脏行"（sync_rev == 0 或本地 updated_at 比 sync_updated_at 新）推上去；
  * pull：按游标增量拉取，按 LWW 落到本地库（墓碑不复活）；
  * 每次真正同步前把本地数据导出成 JSON 快照，保留最近 10 份。

这里只依赖标准库 + 本仓库的 backend 模型；HTTP 用 urllib（带 cookie jar）。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

CLIENT_VERSION = "0.1.0"
SCHEMA_VERSION = "1"
MAX_PUSH_ROWS = 300
BACKUP_KEEP = 10
TIMEOUT = 20.0


class SyncError(Exception):
    """同步失败；message 直接展示给用户（已翻译的短句或服务端原文）。"""


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class HttpClient:
    def __init__(self, server: str) -> None:
        self.server = (server or "").rstrip("/")
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        url = self.server + path
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=body, method=method)
        request.add_header("Accept", "application/json")
        request.add_header("X-Client", "desktop")
        request.add_header("X-Client-Version", CLIENT_VERSION)
        request.add_header("X-Schema-Version", SCHEMA_VERSION)
        if body is not None:
            request.add_header("Content-Type", "application/json; charset=utf-8")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with self.opener.open(request, timeout=TIMEOUT) as response:
                return response.status, _decode(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, _decode(exc.read())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SyncError(f"网络不可用：{exc}") from exc


def _decode(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {"data": data}


# --------------------------------------------------------------------------- #
# Binding
# --------------------------------------------------------------------------- #
def binding_path(paths: dict[str, Path]) -> Path:
    root = Path(paths.get("root") or Path(paths["data_dir"]).parent)
    return root / "device.json"


def load_binding(paths: dict[str, Path]) -> dict[str, Any] | None:
    from . import security

    data = security.load_device_token(binding_path(paths))
    if not data or not data.get("token"):
        return None
    return data


def save_binding(paths: dict[str, Path], data: dict[str, Any]) -> None:
    from . import security

    security.save_device_token(binding_path(paths), data)


def clear_binding(paths: dict[str, Path]) -> None:
    from . import security

    security.clear_device_token(binding_path(paths))


def state(paths: dict[str, Path]) -> dict[str, Any]:
    binding = load_binding(paths)
    if binding is None:
        return {"bound": False, "server": "", "username": "", "last_sync_at": "", "last_error": ""}
    return {
        "bound": True,
        "server": binding.get("server", ""),
        "username": binding.get("username", ""),
        "last_sync_at": binding.get("last_sync_at", ""),
        "last_error": binding.get("last_error", ""),
        "cursor": int(binding.get("cursor") or 0),
    }


def bind(
    paths: dict[str, Path],
    *,
    server: str,
    username: str,
    password: str,
    mode: str,
    create: bool = False,
) -> dict[str, Any]:
    """登录（或先建号再登录）远端账号并申请设备令牌。"""
    client = HttpClient(server)
    if create:
        status, body = client.request(
            "POST", "/api/sync/account", {"username": username, "password": password}
        )
        if status != 200:
            raise SyncError(body.get("detail") or body.get("message") or "创建云端账号失败")
    status, body = client.request(
        "POST", "/api/auth/login", {"username": username, "password": password, "remember": True}
    )
    if status != 200:
        raise SyncError(body.get("detail") or "登录失败")
    status, body = client.request("POST", "/api/sync/devices", {"name": "desktop"})
    if status != 200 or not body.get("device_token"):
        raise SyncError(body.get("detail") or "申请设备令牌失败")
    binding = {
        "v": 1,
        "server": server.rstrip("/"),
        "username": username,
        "device_id": body.get("device_id"),
        "token": body["device_token"],
        "cursor": 0,
        "last_sync_at": "",
        "last_error": "",
    }
    if mode == "cloud":
        # "只拉云端"：清掉游标从头拉，本地数据在 pull 之后按 LWW 合并。
        binding["cursor"] = 0
    save_binding(paths, binding)
    return binding


def unbind(paths: dict[str, Path], *, revoke: bool = True) -> None:
    binding = load_binding(paths)
    if binding and revoke:
        client = HttpClient(binding.get("server", ""))
        try:
            client.request(
                "DELETE",
                f"/api/sync/devices/{int(binding.get('device_id') or 0)}",
                headers={"X-Device-Token": binding.get("token", "")},
            )
        except SyncError:
            pass
    clear_binding(paths)


# --------------------------------------------------------------------------- #
# Local database helpers
# --------------------------------------------------------------------------- #
def _import_backend() -> tuple[Any, Any]:
    from app import db, models  # noqa: PLC0415 - backend path is set at runtime

    return db, models


def _iso_ms(moment: datetime | None = None) -> str:
    current = moment or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    return current.strftime("%Y-%m-%dT%H:%M:%S.") + f"{current.microsecond // 1000:03d}Z"


def _parse(text: Any) -> datetime | None:
    value = str(text or "").strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _dirty(row: Any) -> bool:
    if not row.client_row_id:
        return True
    if not row.sync_updated_at:
        return True
    stamp = _parse(row.sync_updated_at)
    local = getattr(row, "updated_at", None)
    if stamp is None or local is None:
        return True
    return local > stamp


def _payload_for(table: str, row: Any) -> dict[str, Any]:
    if table == "sleep_records":
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
    if table == "meal_checks":
        return {
            "check_date": row.check_date,
            "meal": row.meal,
            "eaten": bool(row.eaten),
            "refused": bool(row.refused),
        }
    return {
        "target_sleep_time": row.target_sleep_time,
        "target_wake_time": row.target_wake_time,
        "language": row.language,
        "theme": row.theme,
        "meal_breakfast": bool(row.meal_breakfast),
        "meal_lunch": bool(row.meal_lunch),
        "meal_dinner": bool(row.meal_dinner),
        "meal_anchor": bool(row.meal_anchor),
    }


def collect_push_rows(session: Any, user_id: int, models: Any) -> list[dict[str, Any]]:
    """收集需要推送的行：新建的（sync_rev == 0）或本地改过的。"""
    rows: list[dict[str, Any]] = []
    for row in session.query(models.SleepRecord).filter(models.SleepRecord.user_id == user_id):
        if _dirty(row):
            if not row.client_row_id:
                row.client_row_id = uuid.uuid4().hex
            if not row.sync_updated_at:
                row.sync_updated_at = _iso_ms(row.updated_at)
            rows.append(_row(table="sleep_records", row=row, models=models))
    for row in session.query(models.MealCheck).filter(models.MealCheck.user_id == user_id):
        if _dirty(row):
            if not row.client_row_id:
                row.client_row_id = uuid.uuid4().hex
            if not row.sync_updated_at:
                row.sync_updated_at = _iso_ms(row.updated_at)
            rows.append(_row(table="meal_checks", row=row, models=models))
    user = session.get(models.User, user_id)
    if user is not None:
        settings_row = _settings_row(session, user, models)
        if _dirty_settings(settings_row, user):
            stamp = _iso_ms(user.updated_at)
            rows.append(
                {
                    "table": "user_settings",
                    "client_row_id": settings_row.client_row_id,
                    "updated_at": stamp,
                    "deleted_at": None,
                    "client_id": "desktop-settings",
                    "payload": _payload_for("settings", user),
                }
            )
    return rows


def _dirty_settings(row: Any, user: Any) -> bool:
    """设置行没同步过（sync_rev == 0）或用户资料比上次同步更新，就算脏。"""
    if int(getattr(row, "sync_rev", 0) or 0) == 0:
        return True
    stamp = _parse(getattr(row, "sync_updated_at", None))
    local = getattr(user, "updated_at", None)
    if stamp is None or local is None:
        return True
    return local > stamp


def _row(*, table: str, row: Any, models: Any) -> dict[str, Any]:
    deleted = getattr(row, "deleted_at", None)
    # A dirty row must travel with its *local* modification time, otherwise the
    # server sees the old synced timestamp and rejects the change as stale.
    stamp = row.sync_updated_at
    local = getattr(row, "updated_at", None)
    current = _parse(stamp)
    if local is not None and (current is None or local > current):
        stamp = _iso_ms(local)
    stamp = stamp or _iso_ms()
    return {
        "table": table,
        "client_row_id": row.client_row_id,
        "updated_at": stamp,
        "deleted_at": _iso_ms(deleted) if deleted else None,
        "client_id": "desktop-" + (row.client_row_id or "")[:8],
        "payload": _payload_for(table, row),
    }


def apply_pull_row(session: Any, user: Any, item: dict[str, Any], models: Any) -> str:
    """把远端一行按 LWW 落到本地；返回 applied / stale。"""
    table = item.get("table")
    cid = str(item.get("client_row_id") or "")
    incoming = _parse(item.get("updated_at"))
    deleted = _parse(item.get("deleted_at"))
    if table == "sleep_records":
        row = (
            session.query(models.SleepRecord)
            .filter(models.SleepRecord.user_id == user.id, models.SleepRecord.client_row_id == cid)
            .one_or_none()
        )
        if row is None and item.get("payload", {}).get("record_date"):
            row = (
                session.query(models.SleepRecord)
                .filter(
                    models.SleepRecord.user_id == user.id,
                    models.SleepRecord.record_date == item["payload"]["record_date"],
                )
                .one_or_none()
            )
    elif table == "meal_checks":
        row = (
            session.query(models.MealCheck)
            .filter(models.MealCheck.user_id == user.id, models.MealCheck.client_row_id == cid)
            .one_or_none()
        )
        if row is None:
            payload = item.get("payload", {})
            row = (
                session.query(models.MealCheck)
                .filter(
                    models.MealCheck.user_id == user.id,
                    models.MealCheck.check_date == payload.get("check_date"),
                    models.MealCheck.meal == payload.get("meal"),
                )
                .one_or_none()
            )
    else:
        row = _settings_row(session, user, models)
    current = _current_stamp(table, row, user)
    newer = current is None or (incoming is not None and incoming > current) or (
        incoming is not None and current is not None and incoming == current and deleted is not None
    )
    if not newer:
        return "stale"
    if row is None:
        row = _make_row(table, user, cid, incoming, models)
        session.add(row)
        session.flush()
    row.client_row_id = cid
    row.sync_updated_at = _iso_ms(incoming)
    row.sync_rev = int(item.get("rev") or 0)
    row.deleted_at = deleted
    if deleted is None:
        _apply_payload(table, row, item.get("payload") or {}, user)
    # 本地时间戳对齐远端，避免刚拉下来的行又被当成"脏行"推回去。
    if hasattr(row, "updated_at") and incoming is not None:
        row.updated_at = incoming
    elif table == "user_settings" and incoming is not None:
        user.updated_at = incoming
    return "applied"


def _current_stamp(table: str, row: Any, user: Any) -> datetime | None:
    """本地这一行的有效时间戳；设置行还没同步过时用用户资料的更新时间。"""
    if row is None:
        return None
    stamp = _parse(getattr(row, "sync_updated_at", None))
    if table == "user_settings" and int(getattr(row, "sync_rev", 0) or 0) == 0:
        return _parse(getattr(user, "updated_at", None))
    return stamp


def _settings_row(session: Any, user: Any, models: Any) -> Any:
    row = (
        session.query(models.SyncSettings)
        .filter(models.SyncSettings.user_id == user.id)
        .one_or_none()
    )
    if row is None:
        row = models.SyncSettings(
            user_id=user.id,
            client_row_id=uuid.uuid4().hex,
            sync_updated_at=_iso_ms(getattr(user, "updated_at", None)),
            sync_rev=0,
        )
        session.add(row)
        session.flush()
    return row


def _make_row(table: str, user: Any, cid: str, incoming: Any, models: Any) -> Any:
    if table == "sleep_records":
        return models.SleepRecord(
            user_id=user.id,
            client_row_id=cid,
            record_date="1970-01-01",
            sleep_time="00:00",
            wake_time="00:00",
            meal_breakfast=False,
            meal_lunch=False,
            meal_dinner=False,
        )
    if table == "meal_checks":
        return models.MealCheck(
            user_id=user.id,
            client_row_id=cid,
            check_date="1970-01-01",
            meal="breakfast",
            eaten=False,
            refused=False,
        )
    return models.SyncSettings(user_id=user.id, client_row_id=cid)


def _apply_payload(table: str, row: Any, payload: dict[str, Any], user: Any = None) -> None:
    if table == "sleep_records":
        row.record_date = str(payload.get("record_date") or row.record_date)
        row.sleep_time = str(payload.get("sleep_time") or row.sleep_time)
        row.wake_time = str(payload.get("wake_time") or row.wake_time)
        row.wake_date = payload.get("wake_date") or None
        row.meal_date = payload.get("meal_date") or None
        row.meal_breakfast = bool(payload.get("meal_breakfast", True))
        row.meal_lunch = bool(payload.get("meal_lunch", True))
        row.meal_dinner = bool(payload.get("meal_dinner", True))
        row.note = payload.get("note") or None
    elif table == "meal_checks":
        row.check_date = str(payload.get("check_date") or row.check_date)
        row.meal = str(payload.get("meal") or row.meal)
        row.eaten = bool(payload.get("eaten"))
        row.refused = bool(payload.get("refused"))
    elif table in ("settings", "user_settings"):
        # 设置既落在 SyncSettings.payload（用于同步比较），也写回 User 行（界面读这里）。
        row.payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        owner = user if user is not None else getattr(row, "user", None)
        if owner is None:
            return
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
                setattr(owner, key, payload[key])


# --------------------------------------------------------------------------- #
# Sync run
# --------------------------------------------------------------------------- #
def snapshot(paths: dict[str, Path], backup_dir: Path | None = None) -> Path | None:
    """同步前导出一份本地数据快照（保留最近 BACKUP_KEEP 份）。"""
    db, models = _import_backend()
    target_dir = Path(backup_dir or paths.get("backup_dir") or Path(paths["data_dir"]).parent / "backup")
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = target_dir / f"sync-{stamp}.json"
    with db.SessionLocal() as session:  # type: ignore[attr-defined]
        user = session.query(models.User).order_by(models.User.id).first()
        if user is None:
            return None
        records = [
            {
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
            for row in session.query(models.SleepRecord).filter(models.SleepRecord.user_id == user.id)
        ]
        meals = [
            {
                "check_date": row.check_date,
                "meal": row.meal,
                "eaten": bool(row.eaten),
                "refused": bool(row.refused),
            }
            for row in session.query(models.MealCheck).filter(models.MealCheck.user_id == user.id)
        ]
    target.write_text(
        json.dumps(
            {"format": "yiuki-sleep-backup", "version": 1, "exported_at": _iso_ms(), "records": records, "meal_checks": meals},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    snapshots = sorted(target_dir.glob("sync-*.json"))
    for old in snapshots[:-BACKUP_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass
    return target


def run(paths: dict[str, Path], *, make_snapshot: bool = True) -> dict[str, Any]:
    """一次完整同步：先备份，再 push，再 pull，最后把游标写回绑定文件。"""
    binding = load_binding(paths)
    if binding is None:
        raise SyncError("尚未绑定云端账号")
    db, models = _import_backend()
    if make_snapshot:
        snapshot(paths)
    client = HttpClient(binding.get("server", ""))
    headers = {"X-Device-Token": binding.get("token", "")}
    cursor = int(binding.get("cursor") or 0)
    pushed = 0
    with db.SessionLocal() as session:
        user = session.query(models.User).order_by(models.User.id).first()
        if user is None:
            raise SyncError("本机还没有本地账号")
        rows = collect_push_rows(session, user.id, models)
        session.commit()
    for index in range(0, len(rows), MAX_PUSH_ROWS):
        batch = rows[index : index + MAX_PUSH_ROWS]
        status, body = client.request(
            "POST", "/api/sync/push", {"cursor": cursor, "client_id": "desktop", "rows": batch}, headers=headers
        )
        if status != 200 or not body.get("ok"):
            problems = body.get("violations") or []
            if problems:
                first = problems[0]
                raise SyncError(f"{first.get('code')}: {first.get('message')}")
            raise SyncError(body.get("detail") or "推送失败")
        pushed += len(batch)
        # The push reply cursor is a server-side position that may be ahead of
        # this device's pull position, so it must never advance the pull cursor.
        _mark_pushed(batch, body.get("results") or [], models)
    pulled = 0
    while True:
        status, body = client.request(
            "GET", f"/api/sync/pull?cursor={cursor}", headers=headers
        )
        if status != 200 or not body.get("ok"):
            raise SyncError(body.get("detail") or "拉取失败")
        items = body.get("rows") or []
        if items:
            with db.SessionLocal() as session:
                user = session.query(models.User).order_by(models.User.id).first()
                for item in items:
                    apply_pull_row(session, user, item, models)
                session.commit()
            pulled += len(items)
        cursor = max(cursor, int(body.get("cursor") or cursor))
        if not body.get("has_more"):
            break
    binding["cursor"] = cursor
    binding["last_sync_at"] = _iso_ms()
    binding["last_error"] = ""
    save_binding(paths, binding)
    return {"ok": True, "pushed": pushed, "pulled": pulled, "cursor": cursor}


def _mark_pushed(rows: list[dict[str, Any]], results: list[dict[str, Any]], models: Any) -> None:
    """按 push 结果回填 sync_rev，避免同一行反复推送。"""
    db, _ = _import_backend()
    revs = {
        (item.get("table"), item.get("client_row_id")): int(item.get("rev") or 0)
        for item in results
        if item.get("status") == "applied"
    }
    if not revs:
        return
    mapping = {
        "sleep_records": models.SleepRecord,
        "meal_checks": models.MealCheck,
        "user_settings": models.SyncSettings,
    }
    with db.SessionLocal() as session:
        for row in rows:
            table = row["table"]
            model = mapping.get(table)
            if model is None:
                continue
            rev = revs.get((table, row["client_row_id"]))
            if not rev:
                continue
            found = (
                session.query(model)
                .filter(model.client_row_id == row["client_row_id"])
                .one_or_none()
            )
            if found is not None:
                found.sync_rev = rev
                found.sync_updated_at = row["updated_at"]
                if table == "user_settings":
                    # Align the profile timestamp too, otherwise the millisecond
                    # truncation keeps making the settings row look dirty.
                    owner = session.get(models.User, found.user_id)
                    if owner is not None:
                        owner.updated_at = _parse(row["updated_at"]) or owner.updated_at
                elif hasattr(found, "updated_at"):
                    found.updated_at = _parse(row["updated_at"]) or found.updated_at
        session.commit()


def record_error(paths: dict[str, Path], message: str) -> None:
    """把最近一次同步失败原因写进绑定文件，供设置页显示。"""
    binding = load_binding(paths)
    if binding is None:
        return
    binding["last_error"] = str(message or "")[:300]
    save_binding(paths, binding)


def sleep_brief() -> None:
    time.sleep(0)
