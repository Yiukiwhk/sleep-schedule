"""Runtime helpers for the desktop shell: paths, port, token, health polling.

Standard library only. This module is imported before the backend settings
exist, so it must not import FastAPI or SQLAlchemy.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

APP_FOLDER = "YiukiSleep"

DEFAULT_CONFIG: dict = {
    "port": 0,              # 0 = let the OS pick a free port on every start
    "check_updates": True,
    "window_width": 1180,
    "window_height": 820,
}


# --------------------------------------------------------------------------- #
# Data folders
# --------------------------------------------------------------------------- #
def _program_data() -> Path:
    base = os.environ.get("PROGRAMDATA") or os.environ.get("ALLUSERSPROFILE")
    return Path(base) if base else Path.home() / "AppData" / "Roaming"


def _local_app_data() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) if base else Path.home() / "AppData" / "Local"


def app_root(*, per_user: bool = False, override: str | os.PathLike | None = None) -> Path:
    """Return the folder that holds data, logs and models.

    Default is machine-wide (%PROGRAMDATA%\\YiukiSleep): one profile per PC.
    ``--per-user-data`` switches to %LOCALAPPDATA%\\YiukiSleep; ``--data-dir``
    overrides both (used by tests and by the --smoke self-check).
    """
    if override:
        return Path(override).expanduser().resolve()
    parent = _local_app_data() if per_user else _program_data()
    return (parent / APP_FOLDER).resolve()


def data_paths(root: Path) -> dict[str, Path]:
    """Concrete file paths inside the app folder."""
    root = Path(root)
    data_dir = root / "data"
    log_dir = root / "logs"
    return {
        "root": root,
        "data_dir": data_dir,
        "db_path": data_dir / "app.db",
        "log_dir": log_dir,
        "log_file": log_dir / "app.log",
        "models_dir": root / "models",
        "backup_dir": root / "backup",
        "config_file": root / "config.json",
        "device_file": root / "device.json",
    }


def ensure_dirs(paths: dict[str, Path]) -> None:
    for key in ("root", "data_dir", "log_dir", "models_dir", "backup_dir"):
        Path(paths[key]).mkdir(parents=True, exist_ok=True)


def load_config(path: Path) -> dict:
    config = dict(DEFAULT_CONFIG)
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return config
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key in config and isinstance(value, type(config[key])):
                config[key] = value
    return config


def save_config(path: Path, config: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# Port / token / URL
# --------------------------------------------------------------------------- #
def claim_port(requested: int = 0) -> int:
    """Return a port that is free right now (bind then close)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", int(requested or 0)))
        return int(sock.getsockname()[1])


def new_token() -> str:
    return secrets.token_urlsafe(32)


def build_url(port: int, path: str = "/") -> str:
    return f"http://127.0.0.1:{int(port)}{path}"


# --------------------------------------------------------------------------- #
# HTTP helpers (no requests dependency)
# --------------------------------------------------------------------------- #
def request_json(
    url: str,
    *,
    token: str = "",
    method: str = "GET",
    payload: dict | None = None,
    timeout: float = 15.0,
) -> tuple[int, dict]:
    data = None
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Local-Token"] = token
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:  # 4xx/5xx still carry a JSON body
        body = exc.read()
        status = exc.code
    try:
        parsed = json.loads(body.decode("utf-8")) if body else {}
    except ValueError:
        parsed = {"raw": body.decode("utf-8", "replace")}
    return status, parsed if isinstance(parsed, dict) else {"data": parsed}


def wait_for_health(port: int, *, timeout: float = 60.0, interval: float = 0.2) -> dict:
    """Block until /healthz answers 200; raise RuntimeError with the last error."""
    url = build_url(port, "/healthz")
    deadline = time.monotonic() + timeout
    last_error = "server did not start"
    while time.monotonic() < deadline:
        try:
            status, body = request_json(url, timeout=2.0)
        except (urllib.error.URLError, OSError, ConnectionError) as exc:
            last_error = str(exc)
        else:
            if status == 200 and body.get("status") == "ok":
                return body
            last_error = f"unexpected health response: {status} {body}"
        time.sleep(interval)
    raise RuntimeError(f"local server did not become healthy: {last_error}")


# --------------------------------------------------------------------------- #
# Environment for the backend process
# --------------------------------------------------------------------------- #
def backend_env(paths: dict[str, Path], *, port: int, token: str) -> dict:
    """Environment overrides the backend reads through SSP_* variables."""
    env = {
        "SSP_MODE": "desktop",
        "SSP_HOST": "127.0.0.1",
        "SSP_PORT": str(int(port)),
        "SSP_LOCAL_TOKEN": token,
        "SSP_DATA_DIR": str(paths["data_dir"]),
        "SSP_DB_PATH": str(paths["db_path"]),
        "SSP_LOG_DIR": str(paths["log_dir"]),
        "SSP_LOG_FILE": str(paths["log_file"]),
    }
    if getattr(sys, "frozen", False):
        # PyInstaller --onedir: bundled assets live next to the executable.
        env["SSP_FRONTEND_DIR"] = str(Path(sys._MEIPASS) / "frontend")  # type: ignore[attr-defined]
    return env


def apply_env(env: dict[str, str]) -> None:
    for key, value in env.items():
        os.environ[key] = value
