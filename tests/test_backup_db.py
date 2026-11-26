"""`deploy/backup_db.py` 的离线测试（备份能恢复、保留份数、坏文件不留）。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy"))

import backup_db  # noqa: E402


def _make_db(tmp_path: Path, rows: int = 3) -> None:
    conn = sqlite3.connect(tmp_path / "app.db")
    conn.execute("create table records (id integer primary key, note text)")
    for index in range(rows):
        conn.execute("insert into records (note) values (?)", (f"row-{index}",))
    conn.commit()
    conn.close()


def _run(tmp_path: Path, *, keep: int = 2) -> int:
    return backup_db.main(
        [
            "--db", str(tmp_path / "app.db"),
            "--dest", str(tmp_path / "out"),
            "--keep", str(keep),
        ]
    )


def test_backup_copies_every_row(tmp_path):
    _make_db(tmp_path)
    assert _run(tmp_path) == 0
    files = sorted((tmp_path / "out").glob("app-*.db"))
    assert len(files) == 1
    assert not list((tmp_path / "out").glob("app-*.db-wal"))
    assert not list((tmp_path / "out").glob("app-*.db-shm"))
    conn = sqlite3.connect(files[0])
    try:
        assert conn.execute("select count(*) from records").fetchone()[0] == 3
    finally:
        conn.close()


def test_backup_keeps_only_requested_number(tmp_path):
    _make_db(tmp_path)
    for _ in range(4):
        assert _run(tmp_path, keep=2) == 0
    files = sorted((tmp_path / "out").glob("app-*.db"))
    assert len(files) == 2


def test_missing_database_is_an_error_and_leaves_nothing(tmp_path):
    assert _run(tmp_path) == 1
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").glob("*.db"))
