"""Desktop entry point: start a loopback uvicorn server, then show the window.

Usage:
  YiukiSleep.exe                  open the main window
  YiukiSleep.exe --smoke          headless self-check, exit code 0 on success
  YiukiSleep.exe --serve-only     run the server without a window (debugging)
  YiukiSleep.exe --per-user-data  keep data in %LOCALAPPDATA% instead of %PROGRAMDATA%
  YiukiSleep.exe --machine-data   force %PROGRAMDATA% even for a portable copy
  YiukiSleep.exe --data-dir PATH  explicit data folder (tests)

Data folder default: an installed copy (under Program Files) uses the shared
%PROGRAMDATA%\YiukiSleep profile; a portable copy uses %LOCALAPPDATA%, because
a standard user cannot write into a %PROGRAMDATA% folder that it merely created.

Security model (see docs/DESKTOP.md sections 4-5): the server binds 127.0.0.1
only, and every /api/* request must carry the one-shot token that is handed to
the window through the URL query string and stored in sessionStorage.
"""

from __future__ import annotations

import argparse
import json
import os
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


def _ensure_std_streams() -> None:
    """Give the windowed exe usable streams.

    A console=False build launched from Explorer inherits no std handles, so
    ``sys.stdout`` / ``sys.stderr`` are ``None``; libraries that call
    ``.isatty()`` or ``print()`` would then crash before the window opens.
    Redirecting them to the null device keeps those calls harmless while the
    real diagnostics go to ``%PROGRAMDATA%\\YiukiSleep\\logs\\app.log``.
    """
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))



# --------------------------------------------------------------------------- #
# Argument parsing / backend import
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="YiukiSleep", description="Yiuki Sleep desktop client")
    parser.add_argument("--smoke", action="store_true", help="run the self-check and exit")
    parser.add_argument("--check-gui", action="store_true", help="check pywebview/.NET and exit")
    parser.add_argument("--report", default=None, help="write the --smoke/--check-gui report to this file")
    parser.add_argument("--serve-only", action="store_true", help="start the local server, no window")
    parser.add_argument("--port", type=int, default=None, help="fixed port (default: ask the OS)")
    parser.add_argument("--data-dir", default=None, help="override the data folder")
    parser.add_argument("--per-user-data", action="store_true", help="store data per Windows user")
    parser.add_argument("--machine-data", action="store_true", help="force the shared ProgramData folder")
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
    if not getattr(sys, "frozen", False):
        # backend/app imports the desktop package lazily (sync/import/update);
        # running this file as a script does not put the repo root on sys.path.
        repo = here.parent
        if str(repo) not in sys.path:
            sys.path.insert(1, str(repo))


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
            # A windowed (console=False) exe started from Explorer has sys.stdout /
            # sys.stderr set to None, and uvicorn's default logging config calls
            # sys.stdout.isatty() -> AttributeError; disabling its logging config
            # keeps the file logger configured by app.main as the single sink.
            log_config=None,
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
def _module_present(name: str) -> bool:
    """True when the module can be imported in this process (frozen or not)."""
    try:
        __import__(name)
    except Exception:  # noqa: BLE001 - only the boolean matters here
        return False
    return True


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
        # The window URL must be the app page, not a 404 from the SPA fallback.
        status, page = runtime.request_json(runtime.build_window_url(port, token))
        report["window_page"] = {"status": status, "html": "raw" in page}
        # The backend imports desktop.sync lazily; a 503 here means the desktop
        # package did not make it into the bundle.
        status, sync_state = runtime.request_json(f"{base}/api/desktop/sync/state", token=token)
        report["sync_state"] = {"status": status, "bound": sync_state.get("bound")}
        # pythonnet reaches .NET through cffi; if the build forgets it the window
        # dies with "No module named 'cffi'" (see --check-gui).
        report["cffi"] = _module_present("cffi") and _module_present("_cffi_backend")
        report["ok"] = (
            report["runtime"].get("desktop") is True
            and report["me"].get("status") == 200
            and bool(report["me"].get("username"))
            and report["no_token"].get("status") == 403
            and report["create"].get("status") == 201
            and report["admin_page"].get("status") == 404
            and report["window_page"].get("status") == 200
            and report["window_page"].get("html") is True
            and report["sync_state"].get("status") == 200
            and report["sync_state"].get("bound") is False
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


def _load_webview_backend() -> tuple[bool, str]:
    """Import pywebview's Windows backend (pythonnet -> .NET Framework host).

    The host refuses to load managed DLLs from folders Windows maps to a
    "network" zone, which is what a sandboxed checkout under Documents looks
    like; installed builds under %PROGRAMFILES% or %LOCALAPPDATA% are fine.
    """
    runtime_name = os.environ.get("PYTHONNET_RUNTIME") or "netfx"
    try:
        import webview  # noqa: F401
        import webview.platforms.winforms  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - reported by the caller
        return False, f"{type(exc).__name__}: {exc}"
    return True, runtime_name


def run_gui_check(report_path: str | None = None) -> int:
    """Load pywebview + its Windows backend without opening a window.

    The bundled app uses .NET through pythonnet; a missing native dependency
    (cffi, the runtime DLL) only shows up here, so the packaged-app test runs
    this before it trusts the window.
    """
    report: dict = {"ok": False}
    ok, detail = _load_webview_backend()
    report["ok"] = ok
    if ok:
        report["backend"] = "winforms"
        report["dotnet"] = detail
    else:
        report["error"] = detail
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
    ready, detail = _load_webview_backend()
    if not ready:
        hint = ""
        if "Python.Runtime" in detail:
            hint = (
                "\n\nThis folder cannot host the .NET runtime. Install the app (the setup "
                "puts it under %PROGRAMFILES%\\YiukiSleep) or move the whole folder to "
                "%LOCALAPPDATA%\\Programs\\YiukiSleep."
            )
        _show_error("could not load the window backend:\n" + detail + hint)
        return 1
    import webview

    server = LocalServer(port)
    try:
        server.start()
    except Exception as exc:  # noqa: BLE001
        hint = ""
        if isinstance(exc, PermissionError) or "Errno 13" in str(exc):
            hint = (
                "\n\nThe data folder is not writable. Start the app with --per-user-data "
                "or install it with the setup so %PROGRAMDATA% gets the right permissions."
            )
        _show_error(f"could not start the local service:\n{exc}{hint}")
        return 1

    url = runtime.build_window_url(port, token)
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
    except Exception as exc:  # noqa: BLE001 - a windowed exe has no console
        # Without this the process would die silently (console=False).
        _show_error("could not open the window:\n" + f"{type(exc).__name__}: {exc}")
        return 1
    finally:
        server.stop()
    return 0


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def _exe_dir() -> Path:
    """Folder the running copy lives in (frozen: next to the exe)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _data_dir_error(root: Path, problem: str) -> str:
    return (
        "could not prepare the data folder:\n"
        f"{root}\n{problem}\n\n"
        "The app needs a writable folder. Either\n"
        "  * install it with YiukiSleep-setup-*.exe (the setup grants access to %PROGRAMDATA%),\n"
        "  * start it with --per-user-data to keep data under %LOCALAPPDATA%\\YiukiSleep, or\n"
        "  * start it with --data-dir PATH to use a folder you can write to."
    )


def main(argv: list[str] | None = None) -> int:
    _ensure_std_streams()
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.version:
        from desktop import __version__

        _say(__version__)
        return 0
    if args.smoke:
        return run_smoke(args.report)
    if args.check_gui:
        return run_gui_check(args.report)

    root = runtime.app_root(
        per_user=args.per_user_data,
        machine=args.machine_data,
        override=args.data_dir,
        exe_dir=_exe_dir(),
    )
    paths = runtime.data_paths(root)
    try:
        runtime.ensure_dirs(paths)
        problem = runtime.probe_writable(root)
    except OSError as exc:  # noqa: BLE001 - turned into a dialog below
        problem = str(exc)
    if problem:
        _show_error(_data_dir_error(root, problem))
        return 2
    config = runtime.load_config(paths["config_file"])
    requested = args.port if args.port is not None else int(config.get("port") or 0)
    port = runtime.claim_port(requested)
    # Automation (and the packaged-app test) may pin the token; normally it is
    # fresh on every start and never leaves the process + the window URL.
    token = os.environ.get("SSP_LOCAL_TOKEN", "").strip() or runtime.new_token()
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
