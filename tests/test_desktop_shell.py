"""桌面壳自检：desktop/app.py --smoke 要在干净的数据目录里全绿（P1 验收）。

运行：backend\\.venv\\Scripts\\python.exe -m pytest tests/test_desktop_shell.py -q
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "desktop" / "app.py"


@pytest.mark.desktop
def test_desktop_smoke_self_check() -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("SSP_")}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run(
        [sys.executable, str(APP), "--smoke"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert proc.returncode == 0, f"smoke failed:\n{proc.stdout}\n{proc.stderr}"

    report = json.loads(proc.stdout[proc.stdout.index("{") :])
    try:
        assert report["ok"] is True, report
        assert report["runtime"]["status"] == 200
        assert report["runtime"]["desktop"] is True
        assert report["me"]["status"] == 200
        assert report["me"]["username"] == "本机"
        assert report["me"]["is_admin"] is False
        # 没有本地令牌的 /api/* 一律 403（回环 + 令牌两道门）。
        assert report["no_token"]["status"] == 403
        assert report["create"]["status"] == 201
        # 桌面版没有后台页面。
        assert report["admin_page"]["status"] == 404
    finally:
        shutil.rmtree(report.get("data_dir", ""), ignore_errors=True)
