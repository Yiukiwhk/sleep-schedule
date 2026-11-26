#!/usr/bin/env python3
"""按日备份生产数据库（服务端专用，只用标准库）。

在服务器上由 /etc/cron.d/sleep-schedule-backup 每天调用一次：

    /opt/sleep-schedule/.venv/bin/python /opt/sleep-schedule/deploy/backup_db.py

做三件事：
  1. 用 sqlite3 的在线备份 API 复制 `app.db`（在 WAL 模式下也是安全一致快照）；
  2. 对新文件跑 `PRAGMA integrity_check`，不通过就删掉并退出 1；
  3. 只保留最近 KEEP 份，按文件名时间排序删除更早的。

参数都有默认值，也支持 `--db` / `--dest` / `--keep` 覆盖。输出一行摘要给 cron 日志。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = "/var/lib/sleep-schedule/app.db"
DEFAULT_DEST = "/var/backups/sleep-schedule"
DEFAULT_KEEP = 14


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _backup(db_path: Path, target: Path) -> None:
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        dest = sqlite3.connect(target)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()


def _integrity_ok(path: Path) -> bool:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    finally:
        conn.close()
    return bool(row) and row[0] == "ok"


def _prune(dest: Path, keep: int) -> list[str]:
    # 按修改时间排序（同一秒内生成的文件名排序不可靠，见上面的 -1/-2 后缀）。
    files = sorted(dest.glob("app-*.db"), key=lambda item: (item.stat().st_mtime, item.name))
    removed = files[: max(0, len(files) - keep)]
    for path in removed:
        path.unlink(missing_ok=True)
    return [path.name for path in removed]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="daily sqlite backup with retention")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--dest", default=DEFAULT_DEST)
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    dest = Path(args.dest)
    if not db_path.exists():
        print(f"backup failed: {db_path} does not exist", file=sys.stderr)
        return 1
    dest.mkdir(parents=True, exist_ok=True)

    target = dest / f"app-{_stamp()}.db"
    # Two runs inside the same second (manual + cron) must not overwrite each other.
    suffix = 1
    base = target
    while target.exists():
        target = base.with_name(f"{base.stem}-{suffix}{base.suffix}")
        suffix += 1
    try:
        _backup(db_path, target)
    except sqlite3.Error as exc:
        target.unlink(missing_ok=True)
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1

    if not _integrity_ok(target):
        target.unlink(missing_ok=True)
        print("backup failed: integrity_check did not return ok", file=sys.stderr)
        return 1

    # SQLite 在只读校验时会留下空的 -shm / -wal；备份目录只该有 .db 文件。
    for suffix in ("-shm", "-wal"):
        Path(str(target) + suffix).unlink(missing_ok=True)

    # 先记大小再清理：prune 按 mtime 排序，理论上不会删掉刚写的这份，
    # 但把顺序定死就不依赖这个假设。
    size = target.stat().st_size
    removed = _prune(dest, max(1, args.keep))
    print(f"backup ok: {target.name} ({size} bytes), pruned {len(removed)} old file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
