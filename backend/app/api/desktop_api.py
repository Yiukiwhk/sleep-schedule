"""Desktop-only endpoints: full local export and local data wipe."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db, write_session
from ..deps import current_user
from ..errors import ApiError
from ..models import AdvicePlan, MealCheck, SleepRecord, User, utcnow
from ..version import VERSION
from .records import serialize_record

router = APIRouter(tags=["desktop"])

EXPORT_FORMAT = "yiuki-sleep-export"
EXPORT_VERSION = 1


def _record_row(record: SleepRecord) -> dict:
    payload = serialize_record(record)
    row = payload.model_dump(mode="json")
    # The export is a portable snapshot: drop identifiers that only make sense
    # inside one database, keep the fields the importer needs.
    for key in ("id", "user_id", "username"):
        row.pop(key, None)
    return row


def _user_row(user: User) -> dict:
    return {
        "target_sleep_time": user.target_sleep_time,
        "target_wake_time": user.target_wake_time,
        "language": user.language,
        "theme": user.theme,
        "meal_breakfast": user.meal_breakfast,
        "meal_lunch": user.meal_lunch,
        "meal_dinner": user.meal_dinner,
        "meal_anchor": user.meal_anchor,
    }


@router.get("/desktop/export")
def export_local_data(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Return one JSON document holding everything the user can back up."""
    records = (
        db.execute(
            select(SleepRecord)
            .where(SleepRecord.user_id == user.id)
            .order_by(SleepRecord.record_date.asc(), SleepRecord.id.asc())
        )
        .scalars()
        .all()
    )
    checks = (
        db.execute(
            select(MealCheck)
            .where(MealCheck.user_id == user.id)
            .order_by(MealCheck.check_date.asc(), MealCheck.meal.asc())
        )
        .scalars()
        .all()
    )
    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": utcnow().isoformat(timespec="seconds") + "Z",
        "settings": _user_row(user),
        "records": [_record_row(record) for record in records],
        "meal_checks": [
            {
                "check_date": check.check_date,
                "meal": check.meal,
                "eaten": check.eaten,
                "refused": check.refused,
            }
            for check in checks
        ],
    }


@router.post("/desktop/wipe")
def wipe_local_data(user: User = Depends(current_user)) -> dict:
    """Delete every row owned by the local profile; the profile itself stays."""
    targets = (
        ("records", SleepRecord),
        ("meal_checks", MealCheck),
        ("advice_plans", AdvicePlan),
    )
    deleted: dict[str, int] = {}
    with write_session() as session:
        for label, model in targets:
            result = session.execute(delete(model).where(model.user_id == user.id))
            deleted[label] = int(result.rowcount or 0)
    return {"ok": True, "deleted": deleted}


# --------------------------------------------------------------------------- #
# Cloud sync bridge: the desktop shell owns the network client, the backend
# only exposes it to the local page (see docs/DESKTOP.md sections 6 and 7).
# --------------------------------------------------------------------------- #
class BindIn(BaseModel):
    # 留空表示使用官方服务器；自建服务器填自己的地址（见 desktop.sync.normalize_server）。
    server: str = Field(default="", max_length=200)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)
    mode: str = Field(default="merge", pattern="^(merge|cloud)$")


class CreateIn(BaseModel):
    server: str = Field(default="", max_length=200)
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=200)


def _sync() -> Any:
    try:
        from desktop import sync as sync_client  # noqa: PLC0415 - optional at runtime
    except ImportError as exc:  # pragma: no cover - only the packaged app has it
        raise ApiError(503, "error.server", code="error.server") from exc
    return sync_client


def _paths() -> dict[str, Path]:
    root = settings.DATA_DIR.parent
    return {
        "root": root,
        "data_dir": settings.DATA_DIR,
        "db_path": settings.DB_PATH,
        "log_dir": settings.LOG_DIR,
        "backup_dir": root / "backup",
        "device_file": root / "device.json",
    }


def _guard(callable_: Any, *args: Any, **kwargs: Any) -> Any:
    sync_client = _sync()
    try:
        return callable_(sync_client, *args, **kwargs)
    except sync_client.SyncError as exc:
        raise ApiError(409, str(exc), code="sync.failed") from exc


@router.get("/desktop/sync/state")
def sync_state() -> dict:
    return _guard(lambda module, paths: module.state(paths), _paths())


@router.post("/desktop/sync/bind")
def sync_bind(payload: BindIn) -> dict:
    def action(module: Any, paths: dict[str, Path]) -> dict:
        module.bind(
            paths,
            server=payload.server,
            username=payload.username,
            password=payload.password,
            mode=payload.mode,
        )
        return module.state(paths)

    return _guard(action, _paths())


@router.post("/desktop/sync/create")
def sync_create(payload: CreateIn) -> dict:
    def action(module: Any, paths: dict[str, Path]) -> dict:
        module.bind(
            paths,
            server=payload.server,
            username=payload.username,
            password=payload.password,
            mode="merge",
            create=True,
        )
        return module.state(paths)

    return _guard(action, _paths())


@router.post("/desktop/sync/run")
def sync_run() -> dict:
    def action(module: Any, paths: dict[str, Path]) -> dict:
        try:
            return module.run(paths)
        except module.SyncError as exc:
            module.record_error(paths, str(exc))
            raise

    return _guard(action, _paths())


@router.post("/desktop/sync/unbind")
def sync_unbind() -> dict:
    def action(module: Any, paths: dict[str, Path]) -> dict:
        module.unbind(paths)
        return module.state(paths)

    return _guard(action, _paths())


# --------------------------------------------------------------------------- #
# Import bridge: parsing runs in the desktop process, and nothing is written
# until the user confirms the preview table (docs/DESKTOP.md section 8).
# --------------------------------------------------------------------------- #
MAX_IMPORT_BYTES = 10 * 1024 * 1024


class ImportRow(BaseModel):
    date: str = Field(min_length=10, max_length=10)
    sleep_time: str = Field(min_length=5, max_length=5)
    wake_time: str = Field(min_length=5, max_length=5)
    note: str | None = Field(default=None, max_length=200)


class ImportCommitIn(BaseModel):
    rows: list[ImportRow] = Field(default_factory=list, max_length=1000)


def _importers() -> Any:
    try:
        from desktop import importers  # noqa: PLC0415 - only the desktop app has it
    except ImportError as exc:  # pragma: no cover
        raise ApiError(503, "error.server", code="error.server") from exc
    return importers


@router.post("/desktop/import/parse")
async def parse_import_file(file: UploadFile = File(...)) -> dict:
    """Parse an uploaded JSON/CSV/PDF/screenshot into previewable rows."""
    importers = _importers()
    data = await file.read()
    if len(data) > MAX_IMPORT_BYTES:
        raise ApiError(400, "import.too_large", code="import.too_large")
    if not data:
        raise ApiError(400, "import.empty_file", code="import.empty_file")
    try:
        result = importers.parse_bytes(data, file.filename or "upload")
    except importers.ImportError as exc:
        # detail 必须是纯 i18n key，前端据此翻译；具体原因已经在 key 里区分。
        raise ApiError(400, exc.code, code=exc.code) from exc
    payload = result.as_dict()
    payload["ocr_available"] = importers.ocr_available()
    return payload


@router.post("/desktop/import/commit")
def commit_import(
    payload: ImportCommitIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Insert the rows the user kept; per-row failures are reported, not raised."""
    from . import records as records_api

    rows = payload.rows[:1000]
    created: list[str] = []
    skipped: list[dict] = []
    violations: list[dict] = []

    existing = set(
        db.execute(
            select(SleepRecord.record_date).where(SleepRecord.user_id == user.id)
        )
        .scalars()
        .all()
    )

    prepared: list[SleepRecord] = []
    for row in rows:
        try:
            record_date = records_api._check_record_date(row.date)  # noqa: SLF001
            wake_date = records_api._check_wake_date(  # noqa: SLF001
                record_date, None, row.sleep_time, row.wake_time
            )
            meal_date = records_api._check_meal_date(  # noqa: SLF001
                record_date, wake_date, None
            )
            records_api._check_no_overlap(  # noqa: SLF001
                db, int(user.id), record_date, row.sleep_time, wake_date, row.wake_time
            )
        except ApiError as exc:
            violations.append({"date": row.date, "code": exc.code or exc.detail, "message": exc.detail})
            continue
        except ValueError:
            violations.append({"date": row.date, "code": "error.invalid_input", "message": "error.invalid_input"})
            continue
        if record_date in existing:
            skipped.append({"date": record_date, "code": "import.already_exists"})
            continue
        existing.add(record_date)
        prepared.append(
            SleepRecord(
                user_id=user.id,
                sleep_time=row.sleep_time,
                wake_time=row.wake_time,
                record_date=record_date,
                wake_date=wake_date,
                meal_date=meal_date,
                note=row.note,
            )
        )

    if prepared:
        with write_session() as session:
            for record in prepared:
                session.add(record)
                created.append(record.record_date)
    return {
        "ok": True,
        "created": created,
        "skipped": skipped,
        "violations": violations,
    }


def _updater() -> Any:
    try:
        from desktop import updater  # noqa: PLC0415 - only the desktop app has it
    except ImportError as exc:  # pragma: no cover
        raise ApiError(503, "error.server", code="error.server") from exc
    return updater


@router.get("/desktop/update")
def check_update() -> dict:
    """Read the published manifest; never raises for network problems."""
    updater = _updater()
    return updater.check(VERSION).as_dict()
