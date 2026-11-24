#!/usr/bin/env bash

set -euo pipefail

if [ "${EUID}" -ne 0 ]; then
    echo "Run with sudo: sudo ./deploy/install-timegate-nginx.sh" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SITE_FILE="/etc/nginx/sites-available/shaafsaad.com"
SNIPPET_FILE="$SCRIPT_DIR/nginx/timegate-location.conf"
BACKUP_FILE="$SITE_FILE.bak-before-timegate"

if grep -q 'location = /timegate' "$SITE_FILE"; then
    echo "TimeGate Nginx locations are already installed."
    nginx -t
    systemctl reload nginx
    exit 0
fi

cp "$SITE_FILE" "$BACKUP_FILE"
temporary_file="$(mktemp)"
awk -v snippet="$SNIPPET_FILE" '
    /^[[:space:]]*location = \/ai[[:space:]]/ && !inserted {
        while ((getline line < snippet) > 0) {
            print line
        }
        close(snippet)
        inserted = 1
    }
    { print }
    END {
        if (!inserted) {
            exit 2
        }
    }
' "$SITE_FILE" > "$temporary_file"
install -m 0644 "$temporary_file" "$SITE_FILE"
rm -f "$temporary_file"

if ! nginx -t; then
    install -m 0644 "$BACKUP_FILE" "$SITE_FILE"
    echo "Nginx validation failed; the original site file was restored." >&2
    exit 1
fi

systemctl reload nginx
echo "TimeGate preview is available at /timegate/ after Open WebUI login."
