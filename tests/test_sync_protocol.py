"""云同步协议验收（docs/DESKTOP.md 第 7 节）。

settings 在 import 时就固化（见 app/config.py），所以每个用例都在子进程里跑：
子进程用临时数据目录起一个服务端模式的 app，按协议走一遍 push/pull/LWW/墓碑/校验，
把所有检查点写进 JSON 报告；主进程只负责断言报告。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable
WORK_DIR = ROOT / ".tmp"

PROBE = r'''
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.environ["SSP_PROBE_BACKEND"])

from fastapi.testclient import TestClient
from sqlalchemy import text

from app import models
from app.core.security import hash_password
from app.db import write_session
from app.main import app


def iso(offset_minutes=0, delta_days=0):
    moment = datetime.now(timezone.utc) + timedelta(minutes=offset_minutes, days=delta_days)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + ("%03dZ" % (moment.microsecond // 1000))


def main():
    checks = {}
    day = (date.today() - timedelta(days=2)).isoformat()
    with TestClient(app) as client:
        with write_session() as session:
            session.add(
                models.User(
                    username="probe-user",
                    password_hash=hash_password("Passw0rd!43"),
                    is_admin=False,
                    is_active=True,
                )
            )
        login = client.post(
            "/api/auth/login",
            json={"username": "probe-user", "password": "Passw0rd!43", "remember": True},
        )
        checks["login"] = login.status_code == 200
        device = client.post("/api/sync/devices", json={"name": "probe"})
        checks["device"] = device.status_code == 200
        token = device.json().get("device_token", "")
        headers = {
            "X-Device-Token": token,
            "X-Client": "desktop",
            "X-Client-Version": "0.1.0",
            "X-Schema-Version": "1",
        }
        status = client.get("/api/sync/status", headers=headers)
        checks["status"] = status.status_code == 200 and status.json().get("ok") is True
        checks["status_version"] = bool(status.json().get("server_version"))
        cid = "row-sleep-1"
        row = {
            "table": "sleep_records",
            "client_row_id": cid,
            "updated_at": iso(-5),
            "payload": {
                "record_date": day,
                "sleep_time": "23:10",
                "wake_time": "07:05",
                "wake_date": day,
                "meal_date": day,
                "meal_breakfast": True,
                "meal_lunch": False,
                "meal_dinner": True,
                "note": "probe",
            },
        }
        push = client.post("/api/sync/push", json={"cursor": 0, "client_id": "c1", "rows": [row]}, headers=headers)
        body = push.json()
        checks["push_ok"] = push.status_code == 200 and body.get("ok") is True
        checks["push_applied"] = body.get("results", [{}])[0].get("status") == "applied"
        cursor = body.get("cursor", 0)
        checks["push_cursor"] = cursor > 0
        again = client.post("/api/sync/push", json={"cursor": cursor, "client_id": "c1", "rows": [row]}, headers=headers)
        checks["idempotent"] = again.json().get("results", [{}])[0].get("status") == "stale"
        bad = dict(row)
        bad["client_row_id"] = "row-bad"
        bad["payload"] = dict(row["payload"], record_date="1999-01-01")
        rejected = client.post("/api/sync/push", json={"cursor": cursor, "rows": [bad]}, headers=headers)
        codes = [item.get("code") for item in rejected.json().get("violations", [])]
        checks["reject_bad_date"] = rejected.json().get("ok") is False and "sync.bad_date" in codes
        meal = {
            "table": "meal_checks",
            "client_row_id": "row-meal-1",
            "updated_at": iso(-4),
            "payload": {"check_date": day, "meal": "lunch", "eaten": True, "refused": False},
        }
        meal_push = client.post("/api/sync/push", json={"cursor": cursor, "rows": [meal]}, headers=headers)
        checks["meal_after_record"] = meal_push.json().get("ok") is True
        meal_bad = dict(meal, client_row_id="row-meal-2")
        meal_bad["payload"] = dict(meal["payload"], check_date="2020-02-02")
        meal_rejected = client.post("/api/sync/push", json={"cursor": cursor, "rows": [meal_bad]}, headers=headers)
        meal_codes = [item.get("code") for item in meal_rejected.json().get("violations", [])]
        checks["meal_without_record"] = "sync.meal_without_record" in meal_codes
        pull = client.get("/api/sync/pull", params={"cursor": 0}, headers=headers)
        pulled = pull.json().get("rows", [])
        checks["pull_count"] = len(pulled) == 2
        checks["pull_has_sleep"] = any(item["table"] == "sleep_records" for item in pulled)
        tomb = dict(row)
        tomb["updated_at"] = iso(1)
        tomb["deleted_at"] = iso(1)
        deleted = client.post("/api/sync/push", json={"cursor": cursor, "rows": [tomb]}, headers=headers)
        checks["tombstone"] = deleted.json().get("results", [{}])[0].get("status") == "applied"
        pull2 = client.get("/api/sync/pull", params={"cursor": cursor}, headers=headers)
        rows2 = pull2.json().get("rows", [])
        checks["tombstone_pulled"] = any(item.get("deleted_at") for item in rows2)
        old = dict(row)
        old["updated_at"] = iso(-30)
        old["payload"] = dict(row["payload"], note="older edit")
        stale = client.post("/api/sync/push", json={"cursor": cursor, "rows": [old]}, headers=headers)
        checks["tombstone_wins"] = stale.json().get("results", [{}])[0].get("status") == "stale"
        account_blocked = client.post("/api/sync/account", json={"username": "desk-user", "password": "Passw0rd!43"})
        checks["account_requires_client"] = account_blocked.status_code == 403
        account = client.post(
            "/api/sync/account",
            json={"username": "desk-user", "password": "Passw0rd!43"},
            headers={"X-Client": "desktop"},
        )
        checks["account_created"] = account.status_code == 200 and account.json().get("ok") is True
        me = client.post(
            "/api/auth/login",
            json={"username": "desk-user", "password": "Passw0rd!43", "remember": False},
        )
        checks["account_login"] = me.status_code == 200
        no_token = client.get("/api/sync/status")
        checks["requires_token"] = no_token.status_code == 401

        # Rows written by the website (not by /api/sync/push) must show up in a pull,
        # otherwise a fresh desktop sees "pushed 0 / pulled 0" forever.
        from app.services import sync_engine

        relogin = client.post(
            "/api/auth/login",
            json={"username": "probe-user", "password": "Passw0rd!43", "remember": True},
        )
        checks["relogin_probe_user"] = relogin.status_code == 200
        probe_id = int(client.get("/api/auth/me").json().get("id") or 0)
        checks["probe_id"] = probe_id > 0
        legacy_day = (date.today() - timedelta(days=300)).isoformat()
        with write_session() as session:
            legacy = models.SleepRecord(
                user_id=probe_id,
                record_date=legacy_day,
                sleep_time="22:00",
                wake_time="06:00",
                note="legacy-row",
                sync_rev=0,
            )
            session.add(legacy)
            session.flush()
            checks["legacy_rev_zero"] = int(legacy.sync_rev or 0) == 0
        web_day = (date.today() - timedelta(days=1)).isoformat()
        created = client.post(
            "/api/records",
            json={
                "sleep_time": "20:00",
                "wake_time": "23:30",
                "record_date": web_day,
                "note": "from-web",
            },
        )
        checks["web_create"] = created.status_code == 201
        record_id = created.json().get("id")
        before_backfill = client.get("/api/sync/pull", params={"cursor": 0}, headers=headers).json().get("rows", [])
        checks["web_row_pulled"] = any(
            item.get("payload", {}).get("note") == "from-web" for item in before_backfill
        )
        checks["legacy_hidden"] = not any(
            item.get("payload", {}).get("note") == "legacy-row" for item in before_backfill
        )
        with write_session() as session:
            counts = sync_engine.backfill_missing(session)
        checks["backfill_records"] = counts.get("sleep_records", 0) >= 1
        checks["backfill_settings"] = counts.get("user_settings", 0) >= 1
        after_backfill = client.get("/api/sync/pull", params={"cursor": 0}, headers=headers).json().get("rows", [])
        checks["legacy_pulled"] = any(
            item.get("payload", {}).get("note") == "legacy-row" for item in after_backfill
        )
        removed = client.delete("/api/records/%s" % record_id)
        checks["web_delete"] = removed.status_code == 200
        after_delete = client.get("/api/sync/pull", params={"cursor": 0}, headers=headers).json().get("rows", [])
        checks["delete_tombstone_pulled"] = any(
            item.get("payload", {}).get("note") == "from-web" and item.get("deleted_at")
            for item in after_delete
        )
        listed = client.get("/api/records", params={"start_date": "", "end_date": ""}).json()
        checks["delete_hidden"] = not any(
            item.get("note") == "from-web" for item in listed.get("items", [])
        )
        checks["delete_total"] = listed.get("total") == 1

        # 保留策略：过期墓碑被物理删除、活的记录不受影响；sync_log 只保留最近 N 天。
        from app.services import retention
        from app.models import SleepRecord as RecordModel, SyncLog

        old = datetime.utcnow() - timedelta(days=400)
        with write_session() as session:
            session.execute(
                text(
                    "UPDATE sleep_records SET deleted_at = :old "
                    "WHERE deleted_at IS NOT NULL"
                ),
                {"old": old},
            )
            session.add(
                SyncLog(
                    user_id=probe_id,
                    table_name="sleep_records",
                    client_row_id="stale-log-row",
                    op="upsert",
                    changed_at=old,
                )
            )
            session.flush()
        checks["tombstones_purged"] = retention.purge_old_tombstones() >= 1
        checks["live_record_kept"] = retention.count_user_records(probe_id) == 1
        checks["sync_log_trimmed"] = retention.trim_sync_log(days=90) >= 1
        with write_session() as session:
            checks["sync_log_still_monotonic"] = (
                session.execute(
                    text("SELECT sql FROM sqlite_master WHERE name='sync_log'")
                ).scalar()
                or ""
            ).upper().find("AUTOINCREMENT") >= 0
    out = os.environ.get("SSP_PROBE_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(checks, handle, ensure_ascii=False)
    failed = [key for key, value in checks.items() if not value]
    if failed:
        print("FAILED " + ",".join(failed))
        return 1
    print("SYNC-PROBE-OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def _run_probe() -> dict:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    workdir = WORK_DIR / f"sync-probe-{uuid.uuid4().hex[:8]}"
    workdir.mkdir()
    out_file = workdir / "report.json"
    env = {key: value for key, value in os.environ.items() if not key.startswith("SSP_")}
    env.update(
        {
            "SSP_MODE": "server",
            "SSP_DATA_DIR": str(workdir / "data"),
            "SSP_DB_PATH": str(workdir / "data" / "app.db"),
            "SSP_LOG_DIR": str(workdir / "logs"),
            "SSP_LOG_FILE": str(workdir / "logs" / "app.log"),
            "SSP_PROBE_BACKEND": str(ROOT / "backend"),
            "SSP_PROBE_OUT": str(out_file),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
    )
    proc = subprocess.run(
        [PYTHON, "-c", PROBE],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")
    assert "SYNC-PROBE-OK" in (proc.stdout or "")
    return json.loads(out_file.read_text(encoding="utf-8"))


@pytest.mark.desktop
def test_sync_protocol() -> None:
    report = _run_probe()
    failed = [key for key, value in report.items() if not value]
    assert failed == [], f"failed checks: {failed}"
