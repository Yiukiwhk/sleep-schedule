"""桌面壳数据目录选择：安装版用 ProgramData，便携版用 LocalAppData。

The 0.1.0 portable build defaulted to %PROGRAMDATA%\\YiukiSleep, which a
standard user can create but not write into (the app then died on app.log with
"Permission denied"). These tests pin the split and the writability probe.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from desktop import runtime  # noqa: E402

pytestmark = pytest.mark.desktop


@pytest.fixture()
def fake_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    program_files = tmp_path / "Program Files"
    local = tmp_path / "Local"
    shared = tmp_path / "ProgramData"
    for folder in (program_files, local, shared):
        folder.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PROGRAMFILES", str(program_files))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("PROGRAMDATA", str(shared))
    return program_files, local, shared


def test_installed_copy_defaults_to_program_data(fake_roots) -> None:
    program_files, _local, shared = fake_roots
    exe_dir = program_files / "YiukiSleep"
    exe_dir.mkdir()
    assert runtime.is_installed_location(exe_dir) is True
    assert runtime.default_root_mode(exe_dir) == "machine"
    assert runtime.app_root(exe_dir=exe_dir) == (shared / runtime.APP_FOLDER).resolve()


def test_portable_copy_defaults_to_local_app_data(fake_roots) -> None:
    _program_files, local, _shared = fake_roots
    exe_dir = local / "Programs" / "YiukiSleep"
    exe_dir.mkdir(parents=True)
    assert runtime.is_installed_location(exe_dir) is False
    assert runtime.default_root_mode(exe_dir) == "user"
    assert runtime.app_root(exe_dir=exe_dir) == (local / runtime.APP_FOLDER).resolve()


def test_explicit_flags_win_over_the_default(fake_roots) -> None:
    program_files, local, shared = fake_roots
    portable = local / "Programs" / "YiukiSleep"
    portable.mkdir(parents=True)
    assert runtime.app_root(per_user=True, exe_dir=portable) == (local / runtime.APP_FOLDER).resolve()
    assert runtime.app_root(machine=True, exe_dir=portable) == (shared / runtime.APP_FOLDER).resolve()
    installed = program_files / "YiukiSleep"
    installed.mkdir()
    assert runtime.app_root(per_user=True, exe_dir=installed) == (local / runtime.APP_FOLDER).resolve()
    override = local / "custom"
    assert runtime.app_root(override=override, exe_dir=installed) == override.resolve()


def test_per_user_and_machine_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError):
        runtime.app_root(per_user=True, machine=True)


def test_probe_writable_detects_an_unwritable_folder(tmp_path: Path) -> None:
    writable = tmp_path / "ok"
    assert runtime.probe_writable(writable) is None
    assert (writable / ".write-probe").exists() is False

    if os.name != "nt":
        pytest.skip("read-only folders are enforced differently outside Windows")
    unwritable = tmp_path / "blocked"
    unwritable.mkdir()
    # A file where the folder should be: mkdir fails on both platforms.
    file_as_dir = tmp_path / "file-not-dir"
    file_as_dir.write_text("x", encoding="ascii")
    assert runtime.probe_writable(file_as_dir) is not None


def test_window_url_has_a_single_slash_before_the_token() -> None:
    """0.1.0 opened `//?t=...`, which the SPA fallback answers with a 404 JSON page."""
    url = runtime.build_window_url(51234, "abc-DEF_123")
    assert url == "http://127.0.0.1:51234/?t=abc-DEF_123"
    assert "//?" not in url
    assert runtime.build_url(51234) == "http://127.0.0.1:51234/"
