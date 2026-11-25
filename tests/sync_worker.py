"""Helper process for tests/test_sync_convergence.py.

Runs in one of two roles so the server app and the desktop client never share a
process (both sides read SSP_* environment variables exactly once at import):

  python tests/sync_worker.py server <payload.json>   # prints READY, then serves
  python tests/sync_worker.py client <payload.json>   # prints a JSON list of op results

The payload is JSON; paths inside it are absolute Windows paths.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _clean_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith("SSP_")}


def _activate(env: dict[str, str]) -> None:
    os.environ.clear()
    os.environ.update(env)
    if str(ROOT / "backend") not in sys.path:
        sys.path.insert(0, str(ROOT / "backend"))
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def _server(payload: dict) -> int:
    data_dir = Path(payload["data_dir"])
    env = _clean_env()
    env.update(
        {
            "SSP_MODE": "server",
            "SSP_DATA_DIR": str(data_dir),
            "SSP_DB_PATH": str(data_dir / "app.db"),
            "SSP_LOG_DIR": str(data_dir / "logs"),
            "SSP_LOG_FILE": str(data_dir / "logs" / "app.log"),
            "PYTHONIOENCODING": "utf-8",
        }
    )
    if "grace_hours" in payload:
        env["SSP_SYNC_NEW_ACCOUNT_GRACE_HOURS"] = str(payload["grace_hours"])
    if "account_rate_limit" in payload:
        env["SSP_SYNC_ACCOUNT_RATE_LIMIT"] = str(payload["account_rate_limit"])
    _activate(env)
    import uvicorn  # noqa: PLC0415

    from app.main import app  # noqa: PLC0415

    print("READY", flush=True)
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(payload["port"]),
        log_level="warning",
        access_log=False,
        server_header=False,
    )
    return 0


def _paths(data_dir: Path) -> dict[str, Path]:
    root = data_dir.parent
    return {
        "root": root,
        "data_dir": data_dir,
        "db_path": data_dir / "app.db",
        "backup_dir": root / "backup",
        "device_file": root / "device.json",
    }


def _client(payload: dict) -> int:
    data_dir = Path(payload["data_dir"])
    env = _clean_env()
    env.update(
        {
            "SSP_MODE": "desktop",
            "SSP_HOST": "127.0.0.1",
            "SSP_PORT": "0",
            "SSP_LOCAL_TOKEN": "",
            "SSP_DATA_DIR": str(data_dir),
            "SSP_DB_PATH": str(data_dir / "app.db"),
            "SSP_LOG_DIR": str(data_dir / "logs"),
            "SSP_LOG_FILE": str(data_dir / "logs" / "app.log"),
            "PYTHONIOENCODING": "utf-8",
        }
    )
    _activate(env)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)

    from app import db, models  # noqa: PLC0415
    from app import desktop as app_desktop  # noqa: PLC0415
    from desktop import sync  # noqa: PLC0415

    db.init_db()
    # Every desktop profile exists before the first sync, exactly like the app.
    app_desktop.ensure_local_user()
    paths = _paths(data_dir)
    results: list[dict] = []

    for op in payload["ops"]:
        name = op["op"]
        try:
            if name == "init":
                user = app_desktop.ensure_local_user()
                created = 0
                with db.SessionLocal() as session:
                    existing = {
                        row.record_date
                        for row in session.query(models.SleepRecord).filter(
                            models.SleepRecord.user_id == user.id
                        )
                    }
                    for index in range(int(op["count"])):
                        record_date = (
                            datetime.now().date() - timedelta(days=int(op.get("offset", 0)) + index)
                        ).isoformat()
                        if record_date in existing:
                            continue
                        session.add(
                            models.SleepRecord(
                                user_id=user.id,
                                record_date=record_date,
                                sleep_time="23:00",
                                wake_time="07:00",
                                note=f"{op.get('prefix', 'row')}-{index}",
                            )
                        )
                        created += 1
                    session.commit()
                results.append({"op": name, "created": created})
            elif name == "bind":
                sync.bind(
                    paths,
                    server=op["server"],
                    username=op["username"],
                    password=op["password"],
                    mode=op.get("mode", "merge"),
                    create=bool(op.get("create")),
                )
                results.append({"op": name, "state": sync.state(paths)})
            elif name == "run":
                report = sync.run(paths)
                results.append({"op": name, "report": report, "state": sync.state(paths)})
            elif name == "state":
                results.append({"op": name, "state": sync.state(paths)})
            elif name == "count":
                with db.SessionLocal() as session:
                    user = session.query(models.User).order_by(models.User.id).first()
                    rows = (
                        session.query(models.SleepRecord)
                        .filter(models.SleepRecord.user_id == user.id)
                        .all()
                    )
                    meals = (
                        session.query(models.MealCheck)
                        .filter(models.MealCheck.user_id == user.id)
                        .count()
                    )
                    results.append(
                        {
                            "op": name,
                            "total": len(rows),
                            "alive": sum(1 for row in rows if row.deleted_at is None),
                            "deleted": sum(1 for row in rows if row.deleted_at is not None),
                            "meals": meals,
                        }
                    )
            elif name == "dates":
                with db.SessionLocal() as session:
                    user = session.query(models.User).order_by(models.User.id).first()
                    rows = (
                        session.query(models.SleepRecord)
                        .filter(models.SleepRecord.user_id == user.id)
                        .order_by(models.SleepRecord.record_date.asc())
                        .all()
                    )
                    results.append(
                        {
                            "op": name,
                            "alive": [row.record_date for row in rows if row.deleted_at is None],
                        }
                    )
            elif name == "notes":
                with db.SessionLocal() as session:
                    user = session.query(models.User).order_by(models.User.id).first()
                    rows = (
                        session.query(models.SleepRecord)
                        .filter(models.SleepRecord.user_id == user.id)
                        .all()
                    )
                    results.append({"op": name, "notes": sorted(row.note or "" for row in rows)})
            elif name == "update_note":
                with db.SessionLocal() as session:
                    user = session.query(models.User).order_by(models.User.id).first()
                    row = (
                        session.query(models.SleepRecord)
                        .filter(
                            models.SleepRecord.user_id == user.id,
                            models.SleepRecord.record_date == op["date"],
                        )
                        .one()
                    )
                    row.note = op["note"]
                    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    session.commit()
                results.append({"op": name, "date": op["date"]})
            elif name == "tombstone":
                with db.SessionLocal() as session:
                    user = session.query(models.User).order_by(models.User.id).first()
                    row = (
                        session.query(models.SleepRecord)
                        .filter(
                            models.SleepRecord.user_id == user.id,
                            models.SleepRecord.record_date == op["date"],
                        )
                        .one()
                    )
                    moment = datetime.now(timezone.utc).replace(tzinfo=None)
                    row.deleted_at = moment
                    row.updated_at = moment
                    session.commit()
                results.append({"op": name, "date": op["date"]})
            elif name == "debug":
                with db.SessionLocal() as session:
                    user = session.query(models.User).order_by(models.User.id).first()
                    revs = sorted(
                        int(row.sync_rev or 0)
                        for row in session.query(models.SleepRecord).filter(
                            models.SleepRecord.user_id == user.id
                        )
                    )
                results.append(
                    {
                        "op": name,
                        "binding": sync.load_binding(paths),
                        "revs": revs[:3] + revs[-3:],
                        "count": len(revs),
                    }
                )
            elif name == "add_meal":
                with db.SessionLocal() as session:
                    user = session.query(models.User).order_by(models.User.id).first()
                    session.add(
                        models.MealCheck(
                            user_id=user.id,
                            check_date=op["date"],
                            meal=op.get("meal", "breakfast"),
                            eaten=True,
                            refused=False,
                        )
                    )
                    session.commit()
                results.append({"op": name, "date": op["date"]})
            elif name == "save_plan":
                from app.services.advice_plan import save_plan  # noqa: PLC0415

                user = app_desktop.ensure_local_user()
                today = datetime.now().date()
                plan_payload = {
                    "mode": "slow",
                    "days": int(op.get("days", 21)),
                    "engine": "test",
                    "plan_meta": {
                        "start_date": today.isoformat(),
                        "target_date": (today + timedelta(days=int(op.get("days", 21)))).isoformat(),
                    },
                    "target": {
                        "sleep_time": op["sleep"],
                        "wake_time": op["wake"],
                    },
                    "days_plan": [],
                }
                plan_id = save_plan(db, int(user.id), plan_payload)
                results.append({"op": name, "plan_id": plan_id})
            elif name == "plan":
                with db.SessionLocal() as session:
                    user = session.query(models.User).order_by(models.User.id).first()
                    row = sync.active_plan(session, user.id, models)
                    results.append(
                        {
                            "op": name,
                            "plan": sync.plan_summary(row),
                        }
                    )
            elif name == "plan_state":
                results.append({"op": name, "plan_state": sync.plan_state(paths)})
            elif name == "set_target":
                with db.SessionLocal() as session:
                    user = session.query(models.User).order_by(models.User.id).first()
                    user.target_sleep_time = str(op["sleep"])
                    user.target_wake_time = str(op["wake"])
                    user.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    session.commit()
                results.append({"op": name, "sleep": op["sleep"], "wake": op["wake"]})
            elif name == "targets":
                with db.SessionLocal() as session:
                    user = session.query(models.User).order_by(models.User.id).first()
                    results.append(
                        {
                            "op": name,
                            "targets": {
                                "target_sleep_time": user.target_sleep_time,
                                "target_wake_time": user.target_wake_time,
                            },
                        }
                    )
            elif name == "choose_plan":
                results.append(
                    {
                        "op": name,
                        "state": sync.choose_plan(paths, op["choice"]),
                    }
                )
            else:  # pragma: no cover - guard
                raise SystemExit(f"unknown op: {name}")
        except sync.SyncError as exc:
            results.append({"op": name, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - surfaced to the test assertions
            results.append({"op": name, "error": f"{type(exc).__name__}: {exc}"})

    print(json.dumps(results, ensure_ascii=False), flush=True)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: sync_worker.py <server|client> <payload.json>", file=sys.stderr)
        return 2
    role, payload_path = argv[1], Path(argv[2])
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    if role == "server":
        return _server(payload)
    if role == "client":
        return _client(payload)
    print(f"unknown role: {role}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
