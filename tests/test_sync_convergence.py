"""End-to-end convergence test for the desktop cloud sync (docs/DESKTOP.md 7).

Two "machines" are two temporary data directories driven by
tests/sync_worker.py subprocesses; the remote server is a third subprocess that
runs the real app in server mode. Everything talks real HTTP.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "tests" / "sync_worker.py"
WORK_DIR = ROOT / ".tmp"
PASSWORD = "Desktop#12345"
USERNAME = "sync-user"


def _utf8_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("SSP_")}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_health(port: int, timeout: float = 40.0) -> None:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/healthz"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.15)
    raise AssertionError(f"server on port {port} never became healthy")


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    base = WORK_DIR / f"sync-{uuid.uuid4().hex[:8]}"
    data_dir = base / "server"
    data_dir.mkdir(parents=True)
    port = _free_port()
    payload = base / "server.json"
    payload.write_text(
        json.dumps(
            {
                "data_dir": str(data_dir),
                "port": port,
                "grace_hours": 0,
                "account_rate_limit": 50,
            }
        ),
        encoding="utf-8",
    )
    proc = subprocess.Popen(
        [sys.executable, str(WORKER), "server", str(payload)],
        cwd=str(ROOT),
        env=_utf8_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        assert proc.stdout is not None
        first = proc.stdout.readline().strip()
        assert first == "READY", f"server failed to start: {first} {proc.stderr.read() if proc.stderr else ''}"
        _wait_health(port)
        yield {"base": base, "port": port, "url": f"http://127.0.0.1:{port}"}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - safety net
            proc.kill()


def _run_client(server_info, name: str, ops: list[dict]) -> list[dict]:
    base = server_info["base"]
    # Same layout as the real shell: <machine>/data/app.db + <machine>/device.json
    data_dir = base / name / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    payload = base / f"{name}-ops.json"
    payload.write_text(json.dumps({"data_dir": str(data_dir), "ops": ops}), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(WORKER), "client", str(payload)],
        cwd=str(ROOT),
        env=_utf8_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert proc.returncode == 0, f"client {name} failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _op(results: list[dict], name: str, index: int = 0) -> dict:
    matches = [item for item in results if item["op"] == name]
    return matches[index]


def _bind(server_info, *, create: bool = False, user: str = USERNAME) -> dict:
    return {
        "op": "bind",
        "server": server_info["url"],
        "username": user,
        "password": PASSWORD,
        "mode": "merge",
        "create": create,
    }


def _expect_bound(results: list[dict], index: int) -> None:
    assert "state" in results[index], results[index]
    assert results[index]["state"]["bound"] is True


@pytest.mark.desktop
def test_two_machines_converge_and_push_is_idempotent(server):
    machine_a = server["base"] / "machine-a"
    machine_b = server["base"] / "machine-b"
    assert not machine_a.exists() and not machine_b.exists()

    results = _run_client(
        server,
        "machine-a",
        [
            {"op": "init", "count": 50, "offset": 0, "prefix": "alpha"},
            _bind(server, create=True),
            {"op": "run"},
            {"op": "count"},
        ],
    )
    assert results[1]["state"]["bound"] is True
    assert "report" in results[2], results[2]
    assert results[2]["report"]["ok"] is True
    # 50 records plus the local settings row.
    assert results[2]["report"]["pushed"] >= 50
    assert results[3]["alive"] == 50

    # A second run right away must not push the same rows again.
    again = _run_client(server, "machine-a", [{"op": "run"}, {"op": "count"}])
    assert again[0]["report"]["pushed"] == 0
    assert again[1]["alive"] == 50

    # Machine B logs into the same cloud account and pulls what A uploaded.
    results = _run_client(
        server,
        "machine-b",
        [_bind(server), {"op": "run"}, {"op": "count"}],
    )
    assert results[0]["state"]["bound"] is True
    assert "alive" in results[2], results
    assert results[2]["alive"] == 50

    # B adds 50 more, both sides converge on 100.
    results = _run_client(
        server,
        "machine-b",
        [
            {"op": "init", "count": 50, "offset": 50, "prefix": "beta"},
            {"op": "run"},
            {"op": "count"},
        ],
    )
    assert results[2]["alive"] == 100

    merged = _run_client(server, "machine-a", [{"op": "run"}, {"op": "count"}])
    assert merged[0]["report"]["pulled"] >= 50, merged
    assert merged[1]["alive"] == 100

    # No duplicate rows on the server: a third push round reports nothing new.
    last = _run_client(server, "machine-b", [{"op": "run"}, {"op": "count"}])
    assert last[0]["report"]["pushed"] == 0
    assert last[1]["alive"] == 100


@pytest.mark.desktop
def test_tombstone_wins_and_never_resurrects(server):
    user = "tomb-user"
    dates = _run_client(
        server,
        "tomb-a",
        [
            {"op": "init", "count": 5, "offset": 200, "prefix": "tomb"},
            _bind(server, create=True, user=user),
            {"op": "run"},
            {"op": "dates"},
        ],
    )[3]["alive"]
    assert len(dates) == 5

    _run_client(server, "tomb-b", [_bind(server, user=user), {"op": "run"}, {"op": "count"}])
    _run_client(server, "tomb-a", [{"op": "tombstone", "date": dates[0]}, {"op": "run"}])

    pulled = _run_client(
        server, "tomb-b", [{"op": "run"}, {"op": "dates"}, {"op": "count"}]
    )
    assert dates[0] not in pulled[1]["alive"]
    assert pulled[2]["alive"] == 4

    # Second sync must not bring the tombstoned row back.
    again = _run_client(server, "tomb-b", [{"op": "run"}, {"op": "count"}])
    assert again[1]["alive"] == 4


@pytest.mark.desktop
def test_newer_note_wins_on_both_sides(server):
    user = "lww-user"
    machine = _run_client(
        server,
        "lww-a",
        [
            {"op": "init", "count": 3, "offset": 400, "prefix": "lww"},
            _bind(server, create=True, user=user),
            {"op": "run"},
            {"op": "dates"},
        ],
    )
    dates = machine[3]["alive"]
    _run_client(server, "lww-b", [_bind(server, user=user), {"op": "run"}])

    updated = _run_client(
        server,
        "lww-a",
        [
            {"op": "update_note", "date": dates[0], "note": "edited-on-a"},
            {"op": "run"},
            {"op": "notes"},
        ],
    )
    assert "edited-on-a" in updated[2]["notes"]

    received = _run_client(server, "lww-b", [{"op": "run"}, {"op": "notes"}])
    assert "edited-on-a" in received[1]["notes"]


@pytest.mark.desktop
def test_offline_bind_reports_clear_error_and_cloud_restore(server):
    offline = _run_client(
        server,
        "offline",
        [
            {
                "op": "bind",
                "server": "http://127.0.0.1:1",
                "username": USERNAME,
                "password": PASSWORD,
                "mode": "merge",
                "create": False,
            }
        ],
    )
    assert "error" in offline[0]
    assert offline[0]["error"]

    restored = _run_client(
        server,
        "machine-c",
        [_bind(server), {"op": "run"}, {"op": "count"}],
    )
    assert restored[2]["alive"] == 100
    assert restored[0]["state"]["bound"] is True


@pytest.mark.desktop
def test_meal_check_travels_with_its_record(server):
    user = "meal-user"
    dates = _run_client(
        server,
        "meal-a",
        [
            {"op": "init", "count": 2, "offset": 600, "prefix": "meal"},
            _bind(server, create=True, user=user),
            {"op": "run"},
            {"op": "dates"},
        ],
    )[3]["alive"]
    _run_client(server, "meal-b", [_bind(server, user=user), {"op": "run"}])

    pushed = _run_client(
        server,
        "meal-a",
        [
            {"op": "add_meal", "date": dates[0], "meal": "breakfast"},
            {"op": "run"},
        ],
    )
    assert "report" in pushed[1], pushed
    pulled = _run_client(server, "meal-b", [{"op": "run"}, {"op": "count"}])
    assert pulled[1]["meals"] >= 1, [pushed, pulled]
