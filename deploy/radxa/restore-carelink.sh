#!/usr/bin/env bash
# Restore the pre-hackathon active configuration; preserve both applications' data.
set -euo pipefail
snapshot=/var/backups/meeting-intelligence/carelink-before-takeover
test "$(id -u)" = 0
test -f "$snapshot/complete"
caddy validate --config "$snapshot/Caddyfile" --adapter caddyfile
exec 9>/run/lock/meeting-vault.lock
flock 9
rm -f /opt/meeting-intelligence/autostart-enabled
# The station's TLS storage is encrypted. Restore the previous Caddy state so
# Carelink can boot independently of the Mac and the meeting archive key.
if [ -f /etc/systemd/system/caddy.service.d/vault-required.conf ]; then
    mountpoint -q /var/lib/meeting-vault/plain || { printf '%s\n' 'Unlock the meeting vault before restoring Carelink.' >&2; exit 1; }
    caddy_backup=/var/lib/meeting-vault/plain/rollback/caddy-before-encryption.tar.gz
    test -f "$caddy_backup"
    # This archive was created locally from /var/lib/caddy and hash-verified.
    tar -tzf "$caddy_backup" | while IFS= read -r member; do
        case "$member" in var/lib/caddy|var/lib/caddy/*) ;; *) exit 1 ;; esac
        case "/$member/" in */../*) exit 1 ;; esac
    done
    systemctl stop caddy.service
    if mountpoint -q /var/lib/caddy; then umount /var/lib/caddy; fi
    tar -xzf "$caddy_backup" -C /
    rm /etc/systemd/system/caddy.service.d/vault-required.conf
    systemctl daemon-reload
fi
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
if systemctl cat meeting-browser.service >/dev/null 2>&1; then
    systemctl disable --now meeting-browser.service
fi
printf '%s\n' 'Carelink configuration restored. Meeting Station files and recordings remain available.'
