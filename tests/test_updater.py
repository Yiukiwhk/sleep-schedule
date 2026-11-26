"""Offline tests for desktop.updater (manifest parsing, version compare, errors)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from desktop import updater  # noqa: E402

pytestmark = pytest.mark.desktop


def _serve(payload: dict, *, raw: bytes | None = None):
    """Start a throwaway HTTP server that answers every GET with the payload."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            body = raw if raw is not None else json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence test output
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}/latest.json"


def test_parse_version_and_is_newer():
    assert updater.parse_version("0.1.0") == (0, 1, 0)
    assert updater.parse_version("1.10.3-beta") == (1, 10, 3)
    assert updater.parse_version("nonsense") == (0, 0, 0)
    assert updater.is_newer("0.2.0", "0.1.0")
    assert not updater.is_newer("0.1.0", "0.1.0")
    assert not updater.is_newer("0.0.9", "0.1.0")


def test_check_reports_new_version_and_warnings():
    payload = {
        "version": "0.2.0",
        "min_supported": "0.2.0",
        "notes": "faster sync",
        "url": "http://example.com/setup.exe",
        "sha256": "not-a-sha",
        "size": "1234",
    }
    server, url = _serve(payload)
    try:
        info = updater.check("0.1.0", url=url)
    finally:
        server.shutdown()
    assert info.ok
    assert info.update_available
    assert info.latest == "0.2.0"
    assert info.size == 1234
    assert "update.url_not_https" in info.warnings
    assert "update.bad_sha256" in info.warnings


def test_check_current_version_is_not_an_update_and_https_is_clean():
    payload = {"version": "0.1.0", "url": "https://yiuki.me/desktop/x.exe", "sha256": "0" * 64}
    server, url = _serve(payload)
    try:
        info = updater.check("0.1.0", url=url)
    finally:
        server.shutdown()
    assert info.ok
    assert not info.update_available
    assert info.warnings == []


def test_check_network_failure_is_reported_not_raised():
    info = updater.check("0.1.0", url="http://127.0.0.1:1/latest.json", timeout=2.0)
    assert not info.ok
    assert info.error
    assert not info.update_available


def test_check_rejects_manifest_without_version():
    server, url = _serve({"notes": "broken"})
    try:
        info = updater.check("0.1.0", url=url)
    finally:
        server.shutdown()
    assert not info.ok
    assert "version" in info.error


def test_manifest_with_utf8_bom_is_still_readable():
    """回归：build.ps1 曾用 `Set-Content -Encoding utf8` 写出带 BOM 的 latest.json，
    json.loads 不认 BOM，更新检查就会静默失败。发布端现在写无 BOM，客户端也兜住。"""
    payload = {"version": "0.3.0", "url": "https://yiuki.me/desktop/x.exe", "sha256": "0" * 64}
    body = b"\xef\xbb\xbf" + json.dumps(payload).encode("utf-8")
    server, url = _serve({}, raw=body)
    try:
        info = updater.check("0.2.2", url=url)
    finally:
        server.shutdown()
    assert info.ok, info.error
    assert info.latest == "0.3.0"
    assert info.update_available
