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

with open(os.environ["SSP_PROBE_OUT"], "w", encoding="utf-8") as handle:
    json.dump(result, handle, ensure_ascii=False)
'''


def _run_probe(mode: str) -> dict[str, Any]:
    """在干净的子进程里以指定模式启动应用并跑一遍探针。"""
    tmp = WORK_DIR / f"desktop-probe-{mode}-{uuid.uuid4().hex[:8]}"
    tmp.mkdir(parents=True, exist_ok=True)
    out = tmp / "probe.json"
    env = {key: value for key, value in os.environ.items() if not key.startswith("SSP_")}
    env.update(
        {
            "SSP_MODE": mode,
            "SSP_DATA_DIR": str(tmp),
            "SSP_DB_PATH": str(tmp / "app.db"),
            "SSP_LOG_DIR": str(tmp),
            "SSP_LOG_FILE": str(tmp / "app.log"),
            "SSP_PROBE_BACKEND": str(BACKEND),
            "SSP_PROBE_OUT": str(out),
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
    # 单机单人：写入不冷却、数据不按 6 个月清理。
    assert result["meta_status"] == 200
    assert result["meta"]["write_cooldown_seconds"] == 0
    assert result["meta"]["retention_months"] == 1200
    assert result["create"] == [201, 201]
    # 登录/改密入口按不存在处理。
    assert result["login"] == 404
    assert result["password"] == 404


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
