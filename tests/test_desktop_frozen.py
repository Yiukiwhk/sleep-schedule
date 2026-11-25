"""打包产物端到端检查：跑 dist 里的 YiukiSleep.exe，重启后数据还在（P4 补强）。

需要先 `desktop\\build\\build.ps1` 产出 dist\\desktop\\YiukiSleep\\YiukiSleep.exe，
没有产物时整个文件跳过。运行：desktop\\.venv\\Scripts\\python.exe -m pytest tests/test_desktop_frozen.py -q
"""

from __future__ import annotations

import io
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


def _sample_screenshot() -> bytes | None:
    """Build a fake sleep-app screenshot for the OCR path (needs Pillow)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:  # pragma: no cover - only on a stripped-down venv
        return None
    image = Image.new("RGB", (1440, 840), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 64)
    except OSError:  # pragma: no cover - font missing
        font = ImageFont.load_default()
    draw.text((80, 80), "2026-11-20", fill=(20, 20, 20), font=font)
    draw.text((120, 360), "23:10", fill=(20, 20, 20), font=font)
    draw.text((840, 360), "07:05", fill=(20, 20, 20), font=font)
    for index, color in enumerate([(120, 40, 160), (40, 120, 200), (80, 180, 120)]):
        top = 600 + index * 60
        draw.rectangle([120, top, 120 + (index + 2) * 180, top + 36], fill=color)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()

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


def _upload(port: int, path: str, filename: str, data: bytes, content_type: str):
    boundary = "----frozenTestBoundary"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8"),
            f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
            data,
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, method="POST")
    request.add_header("Accept", "application/json")
    request.add_header("X-Local-Token", TOKEN)
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _upload_json(port: int, path: str, filename: str, payload: dict):
    return _upload(port, path, filename, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json")


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

        # OCR 是可选组件：装进了产物就顺带验一张截图，没装则跳过。
        screenshot = _sample_screenshot() if parsed.get("ocr_available") else None
        if screenshot:
            status, ocr_result = _upload(
                second_port, "/api/desktop/import/parse", "shot.png", screenshot, "image/png"
            )
            assert status == 200, ocr_result
            assert ocr_result["count"] >= 1
            assert ocr_result["records"][0]["source"] == "ocr"
    finally:
        _stop(proc)
        shutil.rmtree(work, ignore_errors=True)


def _run_report(work: Path, flag: str, name: str) -> dict:
    report = work / f"{name}.json"
    proc = subprocess.run(
        [str(EXE), flag, "--report", str(report)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
    )
    assert report.exists(), f"{flag} produced no report (rc={proc.returncode}): {proc.stdout}{proc.stderr}"
    return json.loads(report.read_text(encoding="utf-8"))


def test_frozen_smoke_reports_cffi_and_gui_backend() -> None:
    """0.1.0 shipped without cffi, so the window died on .NET startup.

    `--smoke` is the quick service check; `--check-gui` loads pywebview's
    Windows backend (cffi + pythonnet + the .NET host) without opening a window.
    """
    work = ROOT / ".tmp" / f"frozen-gui-{uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        smoke = _run_report(work, "--smoke", "smoke")
        assert smoke["ok"] is True, smoke
        assert smoke["cffi"] is True, "cffi/_cffi_backend missing from the bundle"

        gui = _run_report(work, "--check-gui", "gui")
        if not gui.get("ok"):
            error = str(gui.get("error", ""))
            if "Failed to resolve Python.Runtime.Loader.Initialize" in error:
                pytest.skip(
                    "this checkout path is treated as a network zone by the .NET Framework host, "
                    "which refuses to load managed DLLs from it; the same bundle passes from C:\\Temp "
                    "and from %PROGRAMFILES%"
                )
            pytest.fail(f"window backend did not load: {error}")
        assert gui["backend"] == "winforms"
        assert gui["dotnet"] in ("netfx", "coreclr")
    finally:
        shutil.rmtree(work, ignore_errors=True)
