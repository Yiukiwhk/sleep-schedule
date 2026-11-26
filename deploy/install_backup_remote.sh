#!/usr/bin/env bash
# 在服务器上安装每日数据库备份（由 deploy/install_backup.ps1 上传后执行）。
# 前置：/tmp/sleep-backup 里有 backup_db.py；SUDO_PASS 已导出。
set -euo pipefail

APP_DIR=/opt/sleep-schedule
BACKUP_DIR=/var/backups/sleep-schedule
CRON=/etc/cron.d/sleep-schedule-backup
LOG=/var/log/sleep-schedule-backup.log

sudo_run() { echo "$PASS" | sudo -S -p '' "$@"; }
PASS="${SUDO_PASS:?SUDO_PASS is required}"

echo "== install daily sqlite backup =="
sudo_run install -m 755 -o sleepapp -g sleepapp -d "$BACKUP_DIR"
sudo_run install -m 644 -o sleepapp -g sleepapp /tmp/sleep-backup/backup_db.py "$APP_DIR/deploy/backup_db.py"
sudo_run touch "$LOG"
sudo_run chown sleepapp:sleepapp "$LOG"

# 先把内容写成普通文件，再用 install 落位。不要用 `... | sudo_run tee`：
# sudo -S 从 stdin 读密码，tee 会读到同一条流，把密码写进 cron 文件（已出过事故）。
printf '%s\n' \
  'SHELL=/bin/sh' \
  'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin' \
  '# 每天 04:17 UTC 备份 app.db（保留 14 份），见 deploy/backup_db.py' \
  '17 4 * * * sleepapp /opt/sleep-schedule/.venv/bin/python /opt/sleep-schedule/deploy/backup_db.py >> /var/log/sleep-schedule-backup.log 2>&1' \
  > /tmp/sleep-backup/cron.new
sudo_run install -m 644 -o root -g root /tmp/sleep-backup/cron.new "$CRON"
rm -f /tmp/sleep-backup/cron.new
sudo_run grep -q 'backup_db.py' "$CRON" || { echo "!! cron file does not point at backup_db.py"; exit 1; }
sudo_run grep -q 'sudo' "$CRON" && { echo "!! cron file contains a password line"; exit 1; } || true

echo "== run it once so we know it works =="
sudo_run -u sleepapp /opt/sleep-schedule/.venv/bin/python "$APP_DIR/deploy/backup_db.py" | tee /tmp/sleep-backup/run.log

echo "== state =="
sudo_run ls -l "$BACKUP_DIR"
echo "ok backup installed: $CRON"
