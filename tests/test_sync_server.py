"""Unit tests for the desktop sync client's server handling.

Runs without network access: the HTTP layer is replaced by a scripted fake.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from desktop import sync  # noqa: E402

pytestmark = pytest.mark.desktop


def test_empty_server_means_the_official_one() -> None:
    assert sync.normalize_server("") == "https://yiuki.me"
    assert sync.normalize_server("   ") == "https://yiuki.me"


def test_missing_scheme_defaults_to_https() -> None:
    assert sync.normalize_server("yiuki.me") == "https://yiuki.me"
    assert sync.normalize_server("example.com/sleep") == "https://example.com/sleep"


def test_trailing_slashes_are_trimmed() -> None:
    assert sync.normalize_server("https://yiuki.me/") == "https://yiuki.me"
    assert sync.normalize_server("https://example.com/sleep//") == "https://example.com/sleep"


def test_plain_http_is_kept_for_self_hosted_servers() -> None:
    assert sync.normalize_server("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    assert sync.normalize_server("http://192.168.1.20:8011/") == "http://192.168.1.20:8011"


def test_other_schemes_are_rejected() -> None:
    with pytest.raises(sync.SyncError):
        sync.normalize_server("ftp://example.com")
    with pytest.raises(sync.SyncError):
        sync.normalize_server("file:///c:/tmp")


def test_http_client_uses_the_normalized_server() -> None:
    assert sync.HttpClient("").server == "https://yiuki.me"
    assert sync.HttpClient("example.com/").server == "https://example.com"


class _FakeClient:
    """Scripted stand-in for sync.HttpClient: records the server and answers by path."""

    servers: list[str] = []
    responses: dict[str, tuple[int, dict]] = {}

    def __init__(self, server: str) -> None:
        _FakeClient.servers.append(server)

    def request(self, method: str, path: str, payload=None, *, headers=None):
        return _FakeClient.responses.get(path, (404, {"detail": "error.not_found"}))


@pytest.fixture()
def paths(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "app"
    (root / "data").mkdir(parents=True)
    return {"root": root, "data_dir": root / "data", "device_file": root / "device.json"}


def test_bind_with_blank_server_targets_the_official_one(monkeypatch, paths) -> None:
    _FakeClient.servers = []
    _FakeClient.responses = {
        "/api/auth/login": (200, {"ok": True}),
        "/api/sync/devices": (200, {"device_id": 7, "device_token": "tok-123"}),
    }
    monkeypatch.setattr(sync, "HttpClient", _FakeClient)

    binding = sync.bind(
        paths, server="", username="tyq", password="secret123", mode="merge"
    )

    assert _FakeClient.servers == ["https://yiuki.me"]
    assert binding["server"] == "https://yiuki.me"
    assert sync.state(paths)["bound"] is True
    assert sync.state(paths)["server"] == "https://yiuki.me"


def test_bind_keeps_a_self_hosted_server(monkeypatch, paths) -> None:
    _FakeClient.servers = []
    _FakeClient.responses = {
        "/api/auth/login": (200, {"ok": True}),
        "/api/sync/devices": (200, {"device_id": 8, "device_token": "tok-456"}),
    }
    monkeypatch.setattr(sync, "HttpClient", _FakeClient)

    binding = sync.bind(
        paths, server="example.com", username="me", password="secret123", mode="merge"
    )

    assert _FakeClient.servers == ["https://example.com"]
    assert binding["server"] == "https://example.com"


def test_bind_explains_a_server_without_the_sync_api(monkeypatch, paths) -> None:
    _FakeClient.servers = []
    _FakeClient.responses = {"/api/auth/login": (404, {"detail": "error.not_found"})}
    monkeypatch.setattr(sync, "HttpClient", _FakeClient)

    with pytest.raises(sync.SyncError) as excinfo:
        sync.bind(paths, server="https://example.com", username="me", password="x1234567", mode="merge")
    assert "同步接口" in str(excinfo.value)
    assert sync.state(paths)["bound"] is False


def test_bind_reports_bad_credentials(monkeypatch, paths) -> None:
    _FakeClient.servers = []
    _FakeClient.responses = {
        "/api/auth/login": (401, {"detail": "error.invalid_credentials"})
    }
    monkeypatch.setattr(sync, "HttpClient", _FakeClient)

    with pytest.raises(sync.SyncError) as excinfo:
        sync.bind(paths, server="", username="tyq", password="wrong", mode="merge")
    assert "invalid_credentials" in str(excinfo.value)
