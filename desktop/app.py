"""Desktop entry point: start a loopback uvicorn server, then show the window.

Usage:
  YiukiSleep.exe                  open the main window
  YiukiSleep.exe --smoke          headless self-check, exit code 0 on success
  YiukiSleep.exe --serve-only     run the server without a window (debugging)
  YiukiSleep.exe --per-user-data  keep data in %LOCALAPPDATA% instead of %PROGRAMDATA%
  YiukiSleep.exe --data-dir PATH  explicit data folder (tests)

Security model (see docs/DESKTOP.md sections 4-5): the server binds 127.0.0.1
only, and every /api/* request must carry the one-shot token that is handed to
the window through the URL query string and stored in sessionStorage.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
import traceback
from datetime import date, timedelta
from pathlib import Path

if __package__ in (None, ""):  # launched as a plain script (python desktop/app.py)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from desktop import runtime  # noqa: E402

APP_TITLE = "Yiuki Sleep"


def _say(text: str) -> None:
    """Print when a console is attached; the windowed exe has no stdout."""
    if sys.stdout is not None:
        print(text)



# --------------------------------------------------------------------------- #
# Argument parsing / backend import
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="YiukiSleep", description="Yiuki Sleep desktop client")
    parser.add_argument("--smoke", action="store_true", help="run the self-check and exit")
    parser.add_argument("--report", default=None, help="write the --smoke report to this file")
    parser.add_argument("--serve-only", action="store_true", help="start the local server, no window")
    parser.add_argument("--port", type=int, default=None, help="fixed port (default: ask the OS)")
    parser.add_argument("--data-dir", default=None, help="override the data folder")
    parser.add_argument("--per-user-data", action="store_true", help="store data per Windows user")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    return parser.parse_args(argv)


def backend_import_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent / "backend"


def ensure_backend_on_path() -> None:
    """Put backend/ first and drop this script's own folder.

    Running ``python desktop/app.py`` puts ``desktop/`` on sys.path[0], where
    ``app.py`` would shadow the backend's ``app`` package (backend/app). The
    window entry point therefore removes its own folder before importing it.
    """
    here = Path(__file__).resolve().parent
    kept: list[str] = []
    for entry in sys.path:
        try:
            if Path(entry or ".").resolve() == here:
                continue
        except OSError:
            pass
        kept.append(entry)
    sys.path[:] = kept
    root = backend_import_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def _prepare_environment(paths: dict[str, Path], *, port: int, token: str) -> None:
    runtime.apply_env(runtime.backend_env(paths, port=port, token=token))


# --------------------------------------------------------------------------- #
# Server lifecycle
# --------------------------------------------------------------------------- #
class LocalServer:
    """Uvicorn running in a daemon thread, so the window keeps the main thread."""

    def __init__(self, port: int) -> None:
        self.port = int(port)
        self._server = None
        self._thread: threading.Thread | None = None

    def start(self, *, timeout: float = 90.0) -> None:
        ensure_backend_on_path()
        import uvicorn

        from app.main import app as fastapi_app  # imported late: env must be set first

        config = uvicorn.Config(
            fastapi_app,
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            access_log=False,
            server_header=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="ssp-uvicorn", daemon=True)
        self._thread.start()
        runtime.wait_for_health(self.port, timeout=timeout)

    def stop(self, *, timeout: float = 10.0) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        self._server = None


# --------------------------------------------------------------------------- #
# Self-check (--smoke)
# --------------------------------------------------------------------------- #
def smoke_workdir() -> Path:
    """Self-check data folder: prefer the gitignored .tmp/ when running from source."""
    repo_root = Path(__file__).resolve().parent.parent
    tmp_dir = repo_root / ".tmp"
    if tmp_dir.is_dir():
        return Path(tempfile.mkdtemp(prefix="smoke-", dir=str(tmp_dir)))
    return Path(tempfile.mkdtemp(prefix="yiuki-smoke-"))


def run_smoke(report_path: str | None = None) -> int:
    report: dict = {"ok": False}
    workdir = smoke_workdir()
    paths = runtime.data_paths(workdir)
    runtime.ensure_dirs(paths)
    token = runtime.new_token()
    port = runtime.claim_port(0)
    _prepare_environment(paths, port=port, token=token)
    server = LocalServer(port)
    try:
        server.start(timeout=90.0)
        base = runtime.build_url(port).rstrip("/")
        status, runtime_body = runtime.request_json(f"{base}/api/runtime", token=token)
        report["runtime"] = {"status": status, **runtime_body}
        status, me = runtime.request_json(f"{base}/api/auth/me", token=token)
        report["me"] = {"status": status, "username": me.get("username"), "is_admin": me.get("is_admin")}
        status, denied = runtime.request_json(f"{base}/api/auth/me")
        report["no_token"] = {"status": status}
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        status, created = runtime.request_json(
            f"{base}/api/records",
            token=token,
            method="POST",
            payload={"record_date": yesterday, "sleep_time": "23:00", "wake_time": "07:00"},
        )
        report["create"] = {"status": status, "id": created.get("id")}
        status, admin = runtime.request_json(f"{base}/admin/", token=token)
        report["admin_page"] = {"status": status}
        report["ok"] = (
            report["runtime"].get("desktop") is True
            and report["me"].get("status") == 200
            and bool(report["me"].get("username"))
            and report["no_token"].get("status") == 403
            and report["create"].get("status") == 201
            and report["admin_page"].get("status") == 404
        )
    except Exception as exc:  # noqa: BLE001 - the self-check reports, never raises
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
    finally:
        server.stop()
        report["data_dir"] = str(workdir)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if report_path:
        Path(report_path).write_text(text + "\n", encoding="utf-8")
    _say(text)
    return 0 if report.get("ok") else 1


# --------------------------------------------------------------------------- #
# Window mode
# --------------------------------------------------------------------------- #
def _show_error(message: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, APP_TITLE, 0x10)
    except Exception:  # noqa: BLE001
        print(message, file=sys.stderr)


def _window_geometry(config: dict) -> tuple[int, int]:
    try:
        width = int(config.get("window_width") or 1180)
        height = int(config.get("window_height") or 820)
    except (TypeError, ValueError):
        return 1180, 820
    return max(900, width), max(640, height)


def run_window(paths: dict[str, Path], config: dict, *, port: int, token: str) -> int:
    try:
        import webview
    except ImportError as exc:
        _show_error(f"pywebview is not installed: {exc}")
        return 1

    server = LocalServer(port)
    try:
        server.start()
    except Exception as exc:  # noqa: BLE001
        _show_error(f"could not start the local service:\n{exc}")
        return 1

    url = f"{runtime.build_url(port)}/?t={token}"
    width, height = _window_geometry(config)
    window = webview.create_window(APP_TITLE, url, width=width, height=height, min_size=(900, 640))

    def _on_closed() -> None:
        server.stop()

    try:
        window.events.closed += _on_closed
    except Exception:  # noqa: BLE001 - older pywebview builds
        pass

    try:
        webview.start()
    finally:
        server.stop()
    return 0


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.version:
        from desktop import __version__

        _say(__version__)
        return 0
    if args.smoke:
        return run_smoke(args.report)

    root = runtime.app_root(per_user=args.per_user_data, override=args.data_dir)
    paths = runtime.data_paths(root)
    runtime.ensure_dirs(paths)
    config = runtime.load_config(paths["config_file"])
    requested = args.port if args.port is not None else int(config.get("port") or 0)
    port = runtime.claim_port(requested)
    token = runtime.new_token()
    _prepare_environment(paths, port=port, token=token)

    if args.serve_only:
        server = LocalServer(port)
        server.start()
        _say(json.dumps({"url": runtime.build_url(port), "data_dir": str(root)}, indent=2))
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
        finally:
            server.stop()
        return 0

    return run_window(paths, config, port=port, token=token)


if __name__ == "__main__":
    sys.exit(main())
