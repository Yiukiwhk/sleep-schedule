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
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

from . import __version__

CLIENT_VERSION = __version__
SCHEMA_VERSION = "1"
MAX_PUSH_ROWS = 300
BACKUP_KEEP = 10
TIMEOUT = 20.0


DEFAULT_SERVER = "https://yiuki.me"


class SyncError(Exception):
    """同步失败；message 直接展示给用户（已翻译的短句或服务端原文）。"""


def normalize_server(value: str) -> str:
    """把用户填的服务器地址规整成 https://host[:port][/path]。

    留空时返回官方服务器；没写协议的补 ``https://``；自建服务器可以带子路径
    （例如挂在 ``https://example.com/sleep`` 下），末尾斜杠统一去掉。
    """
    text = (value or "").strip()
    if not text:
        return DEFAULT_SERVER
    if "://" not in text:
        text = "https://" + text
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise SyncError("服务器地址要以 http:// 或 https:// 开头")
    return text.rstrip("/")


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class HttpClient:
    def __init__(self, server: str) -> None:
        self.server = normalize_server(server)
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
    target = normalize_server(server)
    client = HttpClient(target)
    if create:
        status, body = client.request(
            "POST", "/api/sync/account", {"username": username, "password": password}
        )
        if status == 404:
            raise SyncError("服务器上没有同步接口，请确认地址，或先更新服务器")
        if status != 200:
            raise SyncError(body.get("detail") or body.get("message") or "创建云端账号失败")
    status, body = client.request(
        "POST", "/api/auth/login", {"username": username, "password": password, "remember": True}
    )
    if status == 404:
        raise SyncError("服务器上没有同步接口，请确认地址，或先更新服务器")
    if status == 401:
        raise SyncError(body.get("detail") or "error.invalid_credentials")
    if status != 200:
        raise SyncError(body.get("detail") or f"登录失败（HTTP {status}）")
    status, body = client.request("POST", "/api/sync/devices", {"name": "desktop"})
    if status == 404:
        raise SyncError("服务器版本太旧：没有设备令牌接口，请先更新服务器")
    if status != 200 or not body.get("device_token"):
        raise SyncError(body.get("detail") or "申请设备令牌失败")
    binding = {
        "v": 1,
        "server": target,
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


def plan_summary(row: Any) -> dict[str, Any]:
    """给界面看的方案摘要（不含完整 payload）。"""
    if row is None:
        return {"available": False}
    if isinstance(row, dict):
        payload = row.get("payload") or {}
    else:
        try:
            payload = json.loads(row.payload) if row.payload else {}
        except (TypeError, ValueError):
            payload = {}
    days = payload.get("days")
    if days is None:
        days = getattr(row, "days", 0)
    return {
        "available": True,
        "start_date": payload.get("start_date") or getattr(row, "start_date", ""),
        "target_date": payload.get("target_date") or getattr(row, "target_date", ""),
        "days": int(days or 0),
        "target_sleep_time": payload.get("target_sleep_time")
        or getattr(row, "target_sleep_time", ""),
        "target_wake_time": payload.get("target_wake_time")
        or getattr(row, "target_wake_time", ""),
        "updated_at": payload.get("updated_at")
        or getattr(row, "sync_updated_at", "")
        or "",
    }


def local_plan_summary(paths: dict[str, Path]) -> dict[str, Any]:
    db, models = _import_backend()
    with db.SessionLocal() as session:  # type: ignore[attr-defined]
        user = session.query(models.User).order_by(models.User.id).first()
        if user is None:
            return {"available": False}
        return plan_summary(active_plan(session, user.id, models))


def local_targets(paths: dict[str, Path]) -> dict[str, str]:
    """本机目标作息（给"选云端方案时要一起改目标作息"的提示用）。"""
    db, models = _import_backend()
    with db.SessionLocal() as session:  # type: ignore[attr-defined]
        user = session.query(models.User).order_by(models.User.id).first()
        if user is None:
            return {}
        return {
            key: value
            for key, value in (
                ("target_sleep_time", user.target_sleep_time),
                ("target_wake_time", user.target_wake_time),
            )
            if value
        }


def cloud_plan(client: HttpClient, binding: dict[str, Any]) -> dict[str, Any] | None:
    """云端当前活跃方案（一行 pull 格式），没有则 None。"""
    status, body = client.request(
        "GET", "/api/sync/plan", headers={"X-Device-Token": binding.get("token", "")}
    )
    if status == 404:
        return None
    if status != 200 or not body.get("ok") or not body.get("available"):
        return None
    row = body.get("row")
    return row if isinstance(row, dict) else None


def cloud_settings(client: HttpClient, binding: dict[str, Any]) -> dict[str, Any] | None:
    """云端当前设置行（含目标作息），老服务器没有这个接口时返回 None。"""
    status, body = client.request(
        "GET", "/api/sync/settings", headers={"X-Device-Token": binding.get("token", "")}
    )
    if status != 200 or not body.get("ok") or not body.get("available"):
        return None
    row = body.get("row")
    return row if isinstance(row, dict) else None


def _settings_targets(row: dict[str, Any] | None) -> dict[str, str]:
    """云端设置里的目标作息；没设过的键直接不返回，避免"空 vs 有"反复弹窗。"""
    if not row:
        return {}
    payload = row.get("payload") or {}
    return {
        key: value
        for key, value in (
            ("target_sleep_time", payload.get("target_sleep_time")),
            ("target_wake_time", payload.get("target_wake_time")),
        )
        if value
    }


def plan_state(paths: dict[str, Path]) -> dict[str, Any]:
    """本机与云端的"方案 + 目标作息"对比；任一项不同就 needs_choice=True。"""
    local = local_plan_summary(paths)
    local_target = local_targets(paths)
    cloud: dict[str, Any] = {"available": False}
    cloud_target: dict[str, str] = {}
    binding = load_binding(paths)
    if binding is not None:
        client = HttpClient(binding.get("server", ""))
        try:
            row = cloud_plan(client, binding)
        except SyncError:
            row = None
        if row is not None:
            cloud = plan_summary(row)
        try:
            cloud_target = _settings_targets(cloud_settings(client, binding))
        except SyncError:
            cloud_target = {}
    plan_diff = bool(
        local.get("available")
        and cloud.get("available")
        and (
            local.get("start_date") != cloud.get("start_date")
            or local.get("target_sleep_time") != cloud.get("target_sleep_time")
            or local.get("target_wake_time") != cloud.get("target_wake_time")
        )
    )
    target_diff = bool(
        local_target
        and cloud_target
        and local_target != cloud_target
    )
    return {
        "local": local,
        "cloud": cloud,
        "local_target": local_target,
        "cloud_target": cloud_target,
        "needs_choice": plan_diff or target_diff,
    }


def choose_plan(paths: dict[str, Path], choice: str) -> dict[str, Any]:
    """用户选"本机 / 云端"后落地，再跑一次同步把两边对齐。

    ``cloud``：方案与目标作息都改成云端的（服务端设置行一起强制落地）。
    ``local``：本机保持现状，把方案与目标作息都标成"刚改过"，由下一次 push 覆盖云端。
    """
    binding = load_binding(paths)
    if binding is None:
        raise SyncError("尚未绑定云端账号")
    db, models = _import_backend()
    client = HttpClient(binding.get("server", ""))
    if choice == "cloud":
        row = cloud_plan(client, binding)
        if row is None:
            raise SyncError("云端还没有方案")
        settings_row = cloud_settings(client, binding)
        with db.SessionLocal() as session:  # type: ignore[attr-defined]
            user = session.query(models.User).order_by(models.User.id).first()
            if user is None:
                raise SyncError("本机还没有本地账号")
            apply_pull_row(session, user, row, models, force=True)
            if settings_row is not None:
                apply_pull_row(session, user, settings_row, models, force=True)
            session.commit()
    elif choice == "local":
        with db.SessionLocal() as session:  # type: ignore[attr-defined]
            user = session.query(models.User).order_by(models.User.id).first()
            if user is None:
                raise SyncError("本机还没有本地账号")
            row = active_plan(session, user.id, models)
            if row is None:
                raise SyncError("本机还没有方案")
            # 让本机方案与目标作息都看起来"刚改过"，下一次 push 就会覆盖云端。
            row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            user.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            session.commit()
    else:
        raise SyncError("未知的方案选择")
    run(paths, make_snapshot=False)
    return plan_state(paths)



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
    if table == "advice_plans":
        try:
            plan = json.loads(row.payload) if row.payload else {}
        except ValueError:
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


def collect_push_rows(
    session: Any, user_id: int, models: Any, *, floor: str | None = None
) -> list[dict[str, Any]]:
    """收集需要推送的行：新建的（sync_rev == 0）或本地改过的。

    floor 是服务端可保存的最早业务日期；更早的记录只留在本机，不参与同步。
    """
    rows: list[dict[str, Any]] = []
    for row in session.query(models.SleepRecord).filter(models.SleepRecord.user_id == user_id):
        if floor and (row.record_date or "") < floor:
            continue
        if _dirty(row):
            if not row.client_row_id:
                row.client_row_id = uuid.uuid4().hex
            if not row.sync_updated_at:
                row.sync_updated_at = _iso_ms(row.updated_at)
            rows.append(_row(table="sleep_records", row=row, models=models))
    for row in session.query(models.MealCheck).filter(models.MealCheck.user_id == user_id):
        if floor and (row.check_date or "") < floor:
            continue
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
    plan = active_plan(session, user_id, models)
    if plan is not None and _dirty(plan):
        if not plan.client_row_id:
            plan.client_row_id = uuid.uuid4().hex
        rows.append(_row(table="advice_plans", row=plan, models=models))
    return rows


def active_plan(session: Any, user_id: int, models: Any) -> Any:
    """本机当前活跃的方案（历史快照不参与同步）。"""
    return (
        session.query(models.AdvicePlan)
        .filter(
            models.AdvicePlan.user_id == user_id,
            models.AdvicePlan.is_active.is_(True),
            models.AdvicePlan.deleted_at.is_(None),
        )
        .order_by(models.AdvicePlan.id.desc())
        .first()
    )


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


def apply_pull_row(
    session: Any, user: Any, item: dict[str, Any], models: Any, *, force: bool = False
) -> str:
    """把远端一行按 LWW 落到本地；返回 applied / stale。

    ``force`` 用于用户在"本机方案 / 云端方案"里明确选了云端：此时不管本地
    时间戳更新与否，都以云端这一行为准。
    """
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
    elif table == "advice_plans":
        row = (
            session.query(models.AdvicePlan)
            .filter(models.AdvicePlan.user_id == user.id, models.AdvicePlan.client_row_id == cid)
            .one_or_none()
        )
        if row is None:
            row = active_plan(session, user.id, models)
    elif table in ("settings", "user_settings"):
        row = _settings_row(session, user, models)
    else:
        # 新版本会同步方案等新表；旧客户端拉到不认识的行就直接跳过。
        return "ignored"
    current = _current_stamp(table, row, user)
    newer = force or current is None or (incoming is not None and incoming > current) or (
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
    if table == "advice_plans":
        # 每台机器只保留一份活跃方案：刚落地的这份变活跃，其余置非活跃。
        for other in (
            session.query(models.AdvicePlan)
            .filter(
                models.AdvicePlan.user_id == user.id,
                models.AdvicePlan.is_active.is_(True),
                models.AdvicePlan.id != row.id,
            )
            .all()
        ):
            other.is_active = False
        row.is_active = deleted is None
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
    if table == "advice_plans":
        return models.AdvicePlan(
            user_id=user.id,
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
    elif table == "advice_plans":
        row.mode = str(payload.get("mode") or "slow")
        row.days = int(payload.get("days") or 1)
        row.start_date = str(payload.get("start_date") or row.start_date)
        row.target_date = str(payload.get("target_date") or row.target_date)
        row.target_sleep_time = str(payload.get("target_sleep_time") or row.target_sleep_time)
        row.target_wake_time = str(payload.get("target_wake_time") or row.target_wake_time)
        row.engine = str(payload.get("engine") or row.engine)
        row.replans = int(payload.get("replans") or 0)
        row.postponed_days = int(payload.get("postponed_days") or 0)
        row.payload = json.dumps(payload.get("plan") or {}, ensure_ascii=False, default=str)
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
    # 服务端保留期之外的数据不参与同步（服务器会清理，推上去也会被跳过）。
    status, status_body = client.request("GET", "/api/sync/status", headers=headers)
    floor = status_body.get("retention_floor") if status == 200 else None
    with db.SessionLocal() as session:
        user = session.query(models.User).order_by(models.User.id).first()
        if user is None:
            raise SyncError("本机还没有本地账号")
        rows = collect_push_rows(session, user.id, models, floor=floor)
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
                raise SyncError(first.get("message") or first.get("code") or "推送失败")
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
    skipped = {
        (item.get("table"), item.get("client_row_id"))
        for item in results
        if item.get("status") == "skipped"
    }
    if not revs and not skipped:
        return
    mapping = {
        "sleep_records": models.SleepRecord,
        "meal_checks": models.MealCheck,
        "user_settings": models.SyncSettings,
        "advice_plans": models.AdvicePlan,
    }
    with db.SessionLocal() as session:
        for row in rows:
            table = row["table"]
            model = mapping.get(table)
            if model is None:
                continue
            rev = revs.get((table, row["client_row_id"]))
            if not rev:
                if (table, row["client_row_id"]) in skipped:
                    found = (
                        session.query(model)
                        .filter(model.client_row_id == row["client_row_id"])
                        .one_or_none()
                    )
                    if found is not None:
                        found.sync_updated_at = row["updated_at"]
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
