#!/usr/bin/env bash
# Restore the pre-hackathon active configuration; preserve both applications' data.
set -euo pipefail
snapshot=/var/backups/meeting-intelligence/carelink-before-takeover
test "$(id -u)" = 0
test -f "$snapshot/complete"
caddy validate --config "$snapshot/Caddyfile" --adapter caddyfile
if [ "$(cat "$snapshot/gateway.enabled")" = enabled ]; then
    systemctl enable carelink-gateway.service
fi
if [ "$(cat "$snapshot/gateway.active")" = active ]; then
    systemctl start carelink-gateway.service
    for attempt in {1..30}; do
        if curl --fail --silent http://127.0.0.1:8080/health >/dev/null; then break; fi
        sleep 1
    done
    curl --fail --silent http://127.0.0.1:8080/health >/dev/null
fi
cp -a "$snapshot/Caddyfile" /etc/caddy/Caddyfile
if [ "$(cat "$snapshot/caddy.active")" = active ]; then
    systemctl reload-or-restart caddy.service
fi
systemctl disable --now scriberr-station.service meeting-station.service
printf '%s\n' 'Carelink configuration restored. Meeting Station files and recordings remain available.'
