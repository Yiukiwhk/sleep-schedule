"""云同步 HTTP 接口（见 docs/DESKTOP.md 第 7 节）。

鉴权：设备令牌（``X-Device-Token``），服务端只存 sha256 哈希。
绑定与解绑用普通用户会话（桌面端先用账号密码登录，再申请设备令牌）。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from ..core.security import client_ip
from ..db import get_db, write_session
from ..deps import current_user
from ..errors import ApiError
from ..services import sync_engine
from ..version import MIN_SUPPORTED, SCHEMA_VERSION, VERSION

router = APIRouter(prefix="/sync", tags=["sync"])

ACCOUNT_DAILY_GRACE_HOURS = settings.SYNC_NEW_ACCOUNT_GRACE_HOURS
ACCOUNT_RATE_LIMIT = settings.SYNC_ACCOUNT_RATE_LIMIT
ACCOUNT_RATE_WINDOW_HOURS = 1


class DeviceCreateIn(BaseModel):
    name: str = Field(default="desktop", max_length=64)


class DeviceCreateOut(BaseModel):
    device_id: int
    device_token: str


class PushRow(BaseModel):
    table: str
    client_row_id: str = Field(min_length=1, max_length=64)
    updated_at: str = ""
    deleted_at: str | None = None
    client_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class PushIn(BaseModel):
    cursor: int = 0
    client_id: str = Field(default="", max_length=64)
    rows: list[PushRow] = Field(default_factory=list)


class AccountIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=72)


def _utcnow() -> datetime:
    from ..models import utcnow

    return utcnow()


def device_user(request: Request, db: Session = Depends(get_db)) -> models.User:
    token = request.headers.get("X-Device-Token", "").strip()
    if not token:
        raise ApiError(401, "error.not_authenticated", code="error.not_authenticated")
    hashed = sync_engine.device_token_hash(token)
    device = db.execute(
        select(models.SyncDevice).where(models.SyncDevice.token_hash == hashed)
    ).scalar_one_or_none()
    if device is None or device.revoked_at is not None:
        raise ApiError(401, "error.not_authenticated", code="error.not_authenticated")
    user = db.get(models.User, device.user_id)
    if user is None or not user.is_active:
        raise ApiError(401, "error.not_authenticated", code="error.not_authenticated")
    device.last_used_at = _utcnow()
    db.commit()
    return user


def _check_client_headers(request: Request) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    version = request.headers.get("X-Client-Version", "").strip()
    if version and _version_tuple(version) < _version_tuple(MIN_SUPPORTED):
        violations.append(
            {
                "code": "sync.client_too_old",
                "table": "",
                "client_row_id": "",
                "message": f"客户端版本 {version} 低于 {MIN_SUPPORTED}",
            }
        )
    schema = request.headers.get("X-Schema-Version", "").strip()
    if schema and schema != str(SCHEMA_VERSION):
        violations.append(
            {
                "code": "sync.schema_mismatch",
                "table": "",
                "client_row_id": "",
                "message": f"schema {schema} 与服务端 {SCHEMA_VERSION} 不一致",
            }
        )
    return violations


def _version_tuple(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in text.split(".")[:3]:
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits or 0))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _grace_violation(
    user: models.User, db: Session, request: Request | None = None
) -> list[dict[str, str]]:
    """桌面端新建的云端账号 24 小时内不能同步（DESKTOP.md 第 13 节防滥用）。

    只有通过 /api/sync/account 建出来的账号才受限制：建号时写一条
    ``sync_account_created`` 系统日志，这里按日志判断，普通账号不受影响。

    例外：建号后 1 小时内、从同一个 IP 发起同步放行——这是「新建账号后立刻
    绑定并同步」的正常路径；换个 IP 或超过 1 小时仍按 24 小时宽限期处理。
    """
    if ACCOUNT_DAILY_GRACE_HOURS <= 0:
        return []
    now = _utcnow()
    since = now - timedelta(hours=ACCOUNT_DAILY_GRACE_HOURS)
    created = db.execute(
        select(models.SystemLog.ip, models.SystemLog.created_at)
        .where(
            models.SystemLog.user_id == user.id,
            models.SystemLog.event == "sync_account_created",
            models.SystemLog.created_at >= since,
        )
        .order_by(models.SystemLog.id.desc())
    ).first()
    if created is None:
        return []
    created_ip, created_at = created
    if request is not None and created_ip and created_at is not None:
        same_ip = client_ip(request) == created_ip
        if same_ip and (now - created_at) <= timedelta(hours=1):
            return []
    return [
        {
            "code": "sync.account_too_new",
            "table": "",
            "client_row_id": "",
            "message": "新账号 24 小时内暂时不能同步",
        }
    ]


@router.post("/devices", response_model=DeviceCreateOut)
def create_device(
    payload: DeviceCreateIn,
    request: Request,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
) -> DeviceCreateOut:
    token = sync_engine.new_device_token()
    with write_session() as session:
        count = session.execute(
            select(func.count(models.SyncDevice.id)).where(
                models.SyncDevice.user_id == user.id, models.SyncDevice.revoked_at.is_(None)
            )
        ).scalar_one()
        if count >= 20:
            raise ApiError(429, "error.rate_limited", code="error.rate_limited")
        device = models.SyncDevice(
            user_id=user.id,
            name=(payload.name or "desktop")[:64],
            token_hash=sync_engine.device_token_hash(token),
        )
        session.add(device)
        session.flush()
        device_id = int(device.id)
    return DeviceCreateOut(device_id=device_id, device_token=token)


@router.delete("/devices/{device_id}")
def delete_device(
    device_id: int,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    with write_session() as session:
        device = session.get(models.SyncDevice, device_id)
        if device is None or device.user_id != user.id:
            raise ApiError(404, "error.not_found", code="error.not_found")
        device.revoked_at = _utcnow()
    return {"ok": True}


@router.get("/status")
def sync_status(
    request: Request,
    user: models.User = Depends(device_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    violations = _check_client_headers(request)
    status = sync_engine.status_payload(user)
    violations.extend(_grace_violation(user, db, request))
    return {**status, "violations": violations}


@router.post("/push")
def sync_push(
    payload: PushIn,
    request: Request,
    user: models.User = Depends(device_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    violations = _check_client_headers(request) + _grace_violation(user, db, request)
    if violations:
        return {"ok": False, "violations": violations, "cursor": payload.cursor}
    rows = [row.model_dump() for row in payload.rows]
    with write_session() as session:
        known = set(
            session.execute(
                select(models.SleepRecord.record_date).where(
                    models.SleepRecord.user_id == user.id
                )
            ).scalars()
        )
        pending_dates = {
            str(row["payload"].get("record_date"))
            for row in rows
            if row["table"] == "sleep_records" and row["payload"].get("record_date")
        }
        problems = sync_engine.validate_rows(
            rows, today=date.today(), known_record_dates=known | pending_dates
        )
        if problems:
            return {"ok": False, "violations": problems, "cursor": payload.cursor}
        results, new_cursor = sync_engine.apply_rows(session, user, rows, today=date.today())
        session.commit()
    return {
        "ok": True,
        "results": results,
        "cursor": new_cursor or payload.cursor,
        "server_version": VERSION,
    }


@router.get("/pull")
def sync_pull(
    request: Request,
    cursor: int = 0,
    user: models.User = Depends(device_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    violations = _check_client_headers(request) + _grace_violation(user, db, request)
    if violations:
        return {"ok": False, "violations": violations, "rows": [], "cursor": cursor}
    rows, new_cursor, has_more = sync_engine.pull_rows(db, user, cursor=cursor)
    return {"ok": True, "rows": rows, "cursor": new_cursor, "has_more": has_more}


@router.get("/plan")
def sync_plan(
    request: Request,
    user: models.User = Depends(device_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """云端当前活跃方案（桌面端绑定后用它和本机方案对比，让用户选一份）。"""
    violations = _check_client_headers(request) + _grace_violation(user, db, request)
    if violations:
        return {"ok": False, "violations": violations, "available": False}
    row = sync_engine.active_plan_row(db, user)
    return {"ok": True, "available": row is not None, "row": row}


@router.post("/account")
def create_sync_account(
    payload: AccountIn,
    request: Request,
) -> dict[str, Any]:
    if request.headers.get("X-Client", "").strip() != "desktop":
        raise ApiError(403, "error.forbidden", code="error.forbidden")
    ip = client_ip(request)
    with write_session() as session:
        since = _utcnow() - timedelta(hours=ACCOUNT_RATE_WINDOW_HOURS)
        recent = session.execute(
            select(func.count(models.LoginAttempt.id)).where(
                models.LoginAttempt.username == f"sync-new:{ip}",
                models.LoginAttempt.created_at >= since,
            )
        ).scalar_one()
        if recent >= ACCOUNT_RATE_LIMIT:
            raise ApiError(429, "error.rate_limited", code="error.rate_limited")
        session.add(
            models.LoginAttempt(username=f"sync-new:{ip}", ip=ip or None, success=True)
        )
        taken = session.execute(
            select(models.User.id).where(models.User.username == payload.username)
        ).scalar_one_or_none()
        if taken is not None:
            raise ApiError(409, "error.username_taken", code="error.username_taken")
        from ..core.security import hash_password

        user = models.User(
            username=payload.username,
            password_hash=hash_password(payload.password),
            is_admin=False,
            is_active=True,
        )
        session.add(user)
        session.flush()
        username = user.username
        session.add(
            models.SystemLog(
                level="INFO",
                event="sync_account_created",
                message=f"sync account created: {username}",
                user_id=user.id,
                username=username,
                ip=ip or None,
            )
        )
    return {"ok": True, "username": username}
