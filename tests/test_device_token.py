"""Device token storage tests.

Windows stores the token with DPAPI; the non-Windows branch is a plain fallback
that exists only so the sync tests can run on CI. Both must round-trip, and the
DPAPI document must not contain the token in clear text.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from desktop import security  # noqa: E402

pytestmark = pytest.mark.desktop

PAYLOAD = {"server": "https://yiuki.me", "username": "tyq", "token": "secret-token"}


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
def test_windows_document_is_encrypted(tmp_path):
    path = tmp_path / "device.json"
    security.save_device_token(path, PAYLOAD)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert "dpapi" in document
    assert "secret-token" not in path.read_text(encoding="utf-8")
    assert security.load_device_token(path) == PAYLOAD


def test_plain_fallback_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(security, "_dpapi_available", lambda: False)
    path = tmp_path / "device.json"
    security.save_device_token(path, PAYLOAD)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert "plain" in document
    assert base64.b64decode(document["plain"]).startswith(b"{")
    assert security.load_device_token(path) == PAYLOAD


def test_missing_and_corrupt_files_return_none(tmp_path):
    assert security.load_device_token(tmp_path / "missing.json") is None
    broken = tmp_path / "broken.json"
    broken.write_text("not json", encoding="utf-8")
    assert security.load_device_token(broken) is None
