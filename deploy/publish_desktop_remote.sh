#!/usr/bin/env bash
# Install the desktop release artefacts onto the site.
#
# Runs on the server as the deploy user; needs SUDO_PASS (plain text password of
# a sudo-capable account) and STAGE (directory holding *.exe + latest.json).
set -euo pipefail

STAGE="${STAGE:-/tmp/sleep-desktop}"
WEBROOT="/var/www/sleep-schedule"

if [ ! -f "$STAGE/latest.json" ]; then
    echo "latest.json not found in $STAGE"; exit 1
fi
if ! ls "$STAGE"/SleepSchedule-setup-*.exe >/dev/null 2>&1; then
    echo "no SleepSchedule-setup-*.exe in $STAGE"; exit 1
fi

sudo_run() { echo "$PASS" | sudo -S -p '' "$@"; }
PASS="${SUDO_PASS:-}"
if [ -z "$PASS" ]; then
    echo "SUDO_PASS is empty"; exit 1
fi

sudo_run mkdir -p "$WEBROOT/desktop"
sudo_run cp -f "$STAGE"/*.exe "$STAGE"/latest.json "$WEBROOT/desktop/"
sudo_run chown www-data:www-data "$WEBROOT/desktop" "$WEBROOT/desktop"/* || true
sudo_run chmod 755 "$WEBROOT/desktop"
sudo_run chmod 644 "$WEBROOT/desktop"/*
ls -l "$WEBROOT/desktop"
echo "installed into $WEBROOT/desktop"
