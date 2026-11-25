"""打包产物端到端检查：跑 dist 里的 YiukiSleep.exe，重启后数据还在（P4 补强）。

需要先 `desktop\\build\\build.ps1` 产出 dist\\desktop\\YiukiSleep\\YiukiSleep.exe，
没有产物时整个文件跳过。运行：desktop\\.venv\\Scripts\\python.exe -m pytest tests/test_desktop_frozen.py -q
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "dist" / "desktop" / "YiukiSleep" / "YiukiSleep.exe"
TOKEN = "frozen-test-token"

pytestmark = [
    pytest.mark.desktop,
    pytest.mark.skipif(not EXE.exists(), reason="dist bundle missing; run desktop/build/build.ps1"),
]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(port: int, path: str, *, method: str = "GET", payload: dict | None = None, token: str = TOKEN):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    request.add_header("Accept", "application/json")
    if token:
        request.add_header("X-Local-Token", token)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _upload_json(port: int, path: str, filename: str, payload: dict):
    boundary = "----frozenTestBoundary"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8"),
            b"Content-Type: application/json\r\n\r\n",
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, method="POST")
    request.add_header("Accept", "application/json")
    request.add_header("X-Local-Token", TOKEN)
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _start(work: Path, port: int) -> subprocess.Popen:
    env = {key: value for key, value in os.environ.items() if not key.startswith("SSP_")}
    env.update({"SSP_LOCAL_TOKEN": TOKEN, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    return subprocess.Popen(
        [str(EXE), "--serve-only", "--port", str(port), "--data-dir", str(work)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_health(port: int, proc: subprocess.Popen, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assert proc.poll() is None, f"desktop exe exited early with {proc.returncode}"
        try:
            status, body = _request(port, "/healthz", token="")
        except OSError:
            time.sleep(0.25)
            continue
        if status == 200 and body.get("status") == "ok":
            return
        time.sleep(0.25)
    raise AssertionError("desktop exe did not become healthy in time")


def _stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive
        proc.kill()
        proc.wait(timeout=10)


def test_frozen_app_keeps_data_across_restart() -> None:
    work = ROOT / ".tmp" / f"frozen-{uuid4().hex[:8]}"
    record_date = (date.today() - timedelta(days=2)).isoformat()
    first_port = _free_port()
    proc = _start(work, first_port)
    try:
        _wait_health(first_port, proc)
        # 没有本地令牌的 /api/* 一律 403，打包产物也必须如此。
        status, _ = _request(first_port, "/api/auth/me", token="")
        assert status == 403
        status, created = _request(
            first_port,
            "/api/records",
            method="POST",
            payload={"record_date": record_date, "sleep_time": "23:10", "wake_time": "07:05"},
        )
        assert status == 201, created
        assert created["record_date"] == record_date
    finally:
        _stop(proc)

    # 同一个数据目录再起一次：记录还在，而且同步接口可用（desktop 包已打进产物）。
    second_port = _free_port()
    proc = _start(work, second_port)
    try:
        _wait_health(second_port, proc)
        status, listing = _request(
            second_port, f"/api/records?start_date={record_date}&end_date={record_date}"
        )
        assert status == 200, listing
        assert listing["total"] == 1
        assert listing["items"][0]["record_date"] == record_date
        status, sync_state = _request(second_port, "/api/desktop/sync/state")
        assert status == 200, sync_state
        assert sync_state["bound"] is False

        # multipart 上传依赖（python-multipart）也必须打进产物。
        import_date = (date.today() - timedelta(days=5)).isoformat()
        status, parsed = _upload_json(
            second_port,
            "/api/desktop/import/parse",
            "probe.json",
            {"records": [{"date": import_date, "sleep_time": "22:30", "wake_time": "06:40"}]},
        )
        assert status == 200, parsed
        assert parsed["count"] == 1
        assert parsed["records"][0]["date"] == import_date
        status, committed = _request(
            second_port,
            "/api/desktop/import/commit",
            method="POST",
            payload={"rows": parsed["records"]},
        )
        assert status == 200, committed
        assert committed["created"] == [import_date]
    finally:
        _stop(proc)
        shutil.rmtree(work, ignore_errors=True)
