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
import urllib.parse
import urllib.request
from pathlib import Path

APP_FOLDER = "SleepSchedule"
# 0.2.2 及更早用 YiukiSleep 存数据；升级时一次性搬过去（见 migrate_legacy_root）。
LEGACY_APP_FOLDER = "YiukiSleep"

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


def program_files_dirs() -> list[Path]:
    """Every Program Files root the process knows about."""
    roots: list[Path] = []
    for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432"):
        value = os.environ.get(name)
        if value:
            roots.append(Path(value))
    return roots


def is_installed_location(exe_dir: Path) -> bool:
    """True when the running copy lives under a Program Files folder."""
    try:
        exe_dir = Path(exe_dir).resolve()
    except OSError:
        return False
    for base in program_files_dirs():
        try:
            base = base.resolve()
        except OSError:
            continue
        if exe_dir == base or base in exe_dir.parents:
            return True
    return False


def default_root_mode(exe_dir: Path) -> str:
    """Machine-wide data only for installed copies, per-user for portable ones.

    A portable copy cannot fix ACLs on %PROGRAMDATA%, so a standard user could
    create the folder but never write into it (Errno 13 on app.log). Installed
    copies get those ACLs from the setup, so they can share one profile per PC.
    """
    return "machine" if is_installed_location(exe_dir) else "user"


def app_root(
    *,
    per_user: bool = False,
    machine: bool = False,
    override: str | os.PathLike | None = None,
    exe_dir: Path | None = None,
) -> Path:
    """Return the folder that holds data, logs and models.

    Installed copies default to machine-wide (%PROGRAMDATA%\\YiukiSleep): one
    profile per PC. Portable copies default to %LOCALAPPDATA%\\YiukiSleep, and
    ``--per-user-data`` / ``--machine-data`` force either side; ``--data-dir``
    overrides everything (tests and the --smoke self-check).
    """
    if override:
        return Path(override).expanduser().resolve()
    if per_user and machine:
        raise ValueError("per_user and machine are mutually exclusive")
    if per_user:
        parent = _local_app_data()
    elif machine:
        parent = _program_data()
    else:
        mode = default_root_mode(exe_dir) if exe_dir else "machine"
        parent = _program_data() if mode == "machine" else _local_app_data()
    return (parent / APP_FOLDER).resolve()


def migrate_legacy_root(root: Path) -> Path | None:
    """把 0.2.2 及更早的 YiukiSleep 数据目录搬到新名字下（一次性、best effort）。

    只有新目录里还没有 app.db、而旧目录里有的时候才搬；搬完保留旧目录不动，
    万一拷贝中途失败，用户下次启动还能重试，也不会丢数据。
    """
    import shutil

    root = Path(root)
    legacy = root.parent / LEGACY_APP_FOLDER
    if legacy == root or not (legacy / "data" / "app.db").exists():
        return None
    if (root / "data" / "app.db").exists():
        return None
    shutil.copytree(legacy, root, dirs_exist_ok=True)
    return legacy


def probe_writable(root: Path) -> str | None:
    """Return an error string when ``root`` cannot hold files, else None."""
    root = Path(root)
    probe = root / ".write-probe"
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
    except OSError as exc:
        try:
            probe.unlink()
        except OSError:
            pass
        return str(exc)
    return None


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


def build_window_url(port: int, token: str) -> str:
    """URL the webview opens: exactly one slash before the query.

    `http://127.0.0.1:<port>//?t=...` is a different path ("//"), which the SPA
    fallback answers with a 404 JSON document - that is why 0.1.0 opened a window
    showing {"detail": "error.not_found"} instead of the app.
    """
    return f"{build_url(port, '/')}?t={urllib.parse.quote(token, safe='')}"


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
