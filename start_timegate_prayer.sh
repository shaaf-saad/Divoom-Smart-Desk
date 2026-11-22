#!/usr/bin/env bash

set -u

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/divoom-timegate"
LOG_FILE="$STATE_DIR/dashboard.log"
mkdir -p "$STATE_DIR"

exec 9>"$STATE_DIR/launcher.lock"
flock -n 9 || exit 0

cd "$APP_DIR"
export DISPLAY="${DISPLAY:-:0}"
export PYTHONUNBUFFERED=1

while true; do
    "$APP_DIR/.venv/bin/python" "$APP_DIR/timegate_prayer_app.py" >>"$LOG_FILE" 2>&1
    exit_code=$?
    if [ "$exit_code" -eq 0 ]; then
        exit 0
    fi
    printf '%s Dashboard exited with status %s; restarting in 10 seconds.\n' \
        "$(date --iso-8601=seconds)" "$exit_code" >>"$LOG_FILE"
    sleep 10
done
