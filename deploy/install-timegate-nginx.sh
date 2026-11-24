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
HOME_FILE="/var/www/shaafsaad.com/index.html"
HOME_BACKUP_FILE="$HOME_FILE.bak-before-timegate"

cp "$SITE_FILE" "$BACKUP_FILE"
cp "$HOME_FILE" "$HOME_BACKUP_FILE"
temporary_file="$(mktemp)"
awk -v snippet="$SNIPPET_FILE" '
    /# TIMEGATE-ROUTE-BEGIN/ {
        replacing = 1
        next
    }
    /# TIMEGATE-ROUTE-END/ {
        replacing = 0
        next
    }
    replacing {
        next
    }
    /^[[:space:]]*location = \/timegate[[:space:]]/ && !legacy_inserted {
        legacy = 1
        next
    }
    legacy && /^[[:space:]]*location \/ai\// {
        legacy = 0
    }
    legacy {
        next
    }
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
install -m 0644 "$SCRIPT_DIR/site/index.html" /var/www/shaafsaad.com/index.html

if ! nginx -t; then
    install -m 0644 "$BACKUP_FILE" "$SITE_FILE"
    install -m 0644 "$HOME_BACKUP_FILE" "$HOME_FILE"
    echo "Nginx validation failed; the original site and homepage were restored." >&2
    exit 1
fi

systemctl reload nginx
echo "TimeGate preview is available at /timegate/ after Open WebUI login."
