"""SSP_MODE 模式切换的 P0 验收：桌面版无登录、无后台、无冷却、数据不删。

配置（SSP_*）在 import 时读取，同一个 pytest 进程里没法改；
所以每个用例都把 FastAPI TestClient 放进子进程跑，结果用 JSON 传回来。
运行：backend\\.venv\\Scripts\\python.exe -m pytest tests/test_desktop_mode.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
# 数据目录放在仓库内的 .tmp/（gitignore）：部分机器上系统临时目录对子进程只读。
WORK_DIR = ROOT / ".tmp"

# 子进程探针：只做 HTTP 调用并把状态码/响应体写进文件。
# 保持 ASCII-only，避免子进程 stdout/文件编码受 zh-CN 代码页影响。
PROBE = r'''
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.environ["SSP_PROBE_BACKEND"])

from fastapi.testclient import TestClient

from app.main import app

today = date.today()


def day(offset):
    return (today - timedelta(days=offset)).isoformat()


result = {}
with TestClient(app) as client:
    result["healthz"] = client.get("/healthz").status_code
    root = client.get("/", follow_redirects=False)
    result["root"] = root.status_code
    result["root_location"] = root.headers.get("location")
    result["user_page"] = client.get("/user/").status_code
    result["admin_page"] = client.get("/admin/").status_code
    result["admin_api"] = client.get("/api/admin/stats").status_code

    me = client.get("/api/auth/me")
    result["me_status"] = me.status_code
    result["me"] = me.json() if me.status_code == 200 else None

    meta = client.get("/api/records/meta")
    result["meta_status"] = meta.status_code
    result["meta"] = meta.json() if meta.status_code == 200 else None

    result["create"] = [
        client.post(
            "/api/records",
            json={"sleep_time": "23:00", "wake_time": "07:00", "record_date": day(1)},
        ).status_code,
        client.post(
            "/api/records",
            json={"sleep_time": "23:30", "wake_time": "07:30", "record_date": day(2)},
        ).status_code,
    ]
    result["login"] = client.post(
        "/api/auth/login", json={"username": "nobody", "password": "User@12345"}
    ).status_code
    result["password"] = client.post(
        "/api/auth/password", json={"current_password": "x", "new_password": "User@12345"}
    ).status_code

    exported = client.get("/api/desktop/export")
    result["export_status"] = exported.status_code
    result["export"] = exported.json() if exported.status_code == 200 else None

    sync_state = client.get("/api/desktop/sync/state")
    result["sync_state_status"] = sync_state.status_code
    result["sync_state"] = sync_state.json() if sync_state.status_code == 200 else None

    offline = client.post(
        "/api/desktop/sync/bind",
        json={
            "server": "http://127.0.0.1:1",
            "username": "someone",
            "password": "Someone#12345",
            "mode": "merge",
        },
    )
    result["sync_offline_status"] = offline.status_code
    result["sync_offline_code"] = offline.json().get("code")

    import_payload = json.dumps(
        {
            "records": [
                {"record_date": day(3), "sleep_time": "22:45", "wake_time": "06:30"},
            ]
        }
    ).encode("utf-8")
    parsed = client.post(
        "/api/desktop/import/parse",
        files={"file": ("import.json", import_payload, "application/json")},
    )
    result["import_parse_status"] = parsed.status_code
    result["import_parse"] = parsed.json() if parsed.status_code == 200 else None
    committed = client.post(
        "/api/desktop/import/commit",
        json={"rows": [{"date": day(3), "sleep_time": "22:45", "wake_time": "06:30"}]},
    )
    result["import_commit_status"] = committed.status_code
    result["import_commit"] = committed.json() if committed.status_code == 200 else None

    wiped = client.post("/api/desktop/wipe")
    result["wipe_status"] = wiped.status_code
    result["wipe"] = wiped.json() if wiped.status_code == 200 else None

    update = client.get("/api/desktop/update")
    result["update_status"] = update.status_code
    result["update"] = update.json() if update.status_code == 200 else None

with open(os.environ["SSP_PROBE_OUT"], "w", encoding="utf-8") as handle:
    json.dump(result, handle, ensure_ascii=False)
'''


def _run_probe(mode: str) -> dict[str, Any]:
    """在干净的子进程里以指定模式启动应用并跑一遍探针。"""
    tmp = WORK_DIR / f"desktop-probe-{mode}-{uuid.uuid4().hex[:8]}"
    tmp.mkdir(parents=True, exist_ok=True)
    out = tmp / "probe.json"
    env = {key: value for key, value in os.environ.items() if not key.startswith("SSP_")}
    manifest = tmp / "latest.json"
    manifest.write_text(
        '{"version": "9.9.9", "min_supported": "0.1.0", "notes": "probe", '
        '"url": "https://example.invalid/probe.exe", "sha256": "' + "0" * 64 + '"}',
        encoding="utf-8",
    )
    env.update(
        {
            "SSP_MODE": mode,
            "SSP_DATA_DIR": str(tmp),
            "SSP_DB_PATH": str(tmp / "app.db"),
            "SSP_LOG_DIR": str(tmp),
            "SSP_LOG_FILE": str(tmp / "app.log"),
            "SSP_PROBE_BACKEND": str(BACKEND),
            "SSP_PROBE_OUT": str(out),
            "SSP_UPDATE_MANIFEST_URL": manifest.as_uri(),
            "PYTHONIOENCODING": "utf-8",
        }
    )
    proc = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert proc.returncode == 0, f"probe failed ({mode}):\n{proc.stderr}\n{proc.stdout}"
    return json.loads(out.read_text(encoding="utf-8"))


@pytest.mark.desktop
def test_desktop_mode_has_no_login_no_admin_and_no_cooldown() -> None:
    result = _run_probe("desktop")

    assert result["healthz"] == 200
    # 桌面版直接返回应用外壳（不 302 到 /user/，保住 URL 里的本地令牌）。
    assert result["root"] == 200
    assert result["root_location"] is None
    assert result["user_page"] == 200
    # 没有后台页面，也没有后台接口。
    assert result["admin_page"] == 404
    assert result["admin_api"] == 404
    # 没有登录：/api/auth/me 直接返回本地档案。
    assert result["me_status"] == 200
    assert result["me"]["username"] == "本机"
    assert result["me"]["is_admin"] is False
    assert result["me"]["is_active"] is True
    # 单机单人：写入不冷却、本机数据不按保留期删除（1200 个月≈不删）。
    assert result["meta_status"] == 200
    assert result["meta"]["write_cooldown_seconds"] == 0
    assert result["meta"]["retention_months"] == 1200
    assert result["create"] == [201, 201]
    # 登录/改密入口按不存在处理。
    assert result["login"] == 404
    assert result["password"] == 404
    # 桌面专属接口：导出、清空、云同步桥。
    assert result["export_status"] == 200
    assert result["export"]["format"] == "yiuki-sleep-export"
    assert len(result["export"]["records"]) == 2
    assert result["sync_state_status"] == 200
    assert result["sync_state"]["bound"] is False
    assert result["sync_offline_status"] == 409
    assert result["sync_offline_code"] == "sync.failed"
    # 导入：先解析文件（只读，不落地），再提交勾选行。
    assert result["import_parse_status"] == 200
    assert result["import_parse"]["count"] == 1
    assert result["import_parse"]["records"][0]["date"]
    assert result["import_commit_status"] == 200
    assert result["import_commit"]["created"]
    assert result["wipe_status"] == 200
    assert result["wipe"]["deleted"]["records"] == 3
    # 更新检查端到端：SSP_UPDATE_MANIFEST_URL 指向本地清单，接口必须报可更新。
    assert result["update_status"] == 200
    assert result["update"]["update_available"] is True
    assert result["update"]["latest"] == "9.9.9"


@pytest.mark.desktop
def test_server_mode_keeps_login_and_admin() -> None:
    result = _run_probe("server")

    assert result["healthz"] == 200
    assert result["root"] in (302, 307)
    assert result["root_location"].endswith("/user/")
    assert result["admin_page"] == 200
    assert result["admin_api"] in (401, 403)
    assert result["me_status"] == 401
    assert result["meta_status"] == 401
    # 登录路由存在（凭据错误是 401，而不是 404）。
    assert result["login"] == 401
    assert result["password"] == 401
    # 桌面专属接口不会出现在服务端实例上（GET 走 SPA 兜底 404，POST 没有路由是 405）。
    assert result["export_status"] == 404
    assert result["sync_state_status"] == 404
    assert result["import_parse_status"] in (404, 405)
    assert result["import_commit_status"] in (404, 405)
    assert result["wipe_status"] in (404, 405)
    assert result["update_status"] == 404
