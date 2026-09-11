#!/usr/bin/env bash
# Run on the Radxa as root AFTER verifying a consistent off-board Carelink backup.
# Package, environments, service units and station Caddyfile must be installed first.
set -euo pipefail
snapshot=/var/backups/meeting-intelligence/carelink-before-takeover
test "$(id -u)" = 0
test -f /etc/meeting-intelligence/Caddyfile.station
test -x /opt/meeting-intelligence/bin/scriberr
test -x /opt/meeting-intelligence/venv/bin/meeting-station
if [ ! -e "$snapshot" ]; then
    install -d -m 700 "$snapshot"
    cp -a /etc/caddy/Caddyfile "$snapshot/Caddyfile"
    systemctl is-active carelink-gateway.service > "$snapshot/gateway.active" || true
    systemctl is-enabled carelink-gateway.service > "$snapshot/gateway.enabled" || true
    systemctl is-active caddy.service > "$snapshot/caddy.active" || true
    touch "$snapshot/complete"
fi
test -f "$snapshot/complete"
caddy validate --config /etc/meeting-intelligence/Caddyfile.station --adapter caddyfile
systemctl daemon-reload
rollback() {
    rm -f /opt/meeting-intelligence/autostart-enabled
    if systemctl cat meeting-browser.service >/dev/null 2>&1; then
        systemctl disable --now meeting-browser.service || true
    fi
    cp -a "$snapshot/Caddyfile" /etc/caddy/Caddyfile
    systemctl reload caddy.service || true
    if [ "$(cat "$snapshot/gateway.active")" = active ]; then
        systemctl start carelink-gateway.service || true
    fi
    if [ "$(cat "$snapshot/gateway.enabled")" = enabled ]; then
        systemctl enable carelink-gateway.service || true
    fi
}
# An upgrade may already have Caddy pointing at the station, so failures during
# restart or readiness checks must restore Carelink too.
trap rollback ERR
# Reload the installed release even when this is an upgrade of an active station.
# Only our isolated services restart; the Carelink gateway stays up until health checks pass.
systemctl restart meeting-station.service scriberr-station.service
for attempt in {1..30}; do
    if curl --fail --silent http://127.0.0.1:8766/health >/dev/null && \
       curl --fail --silent http://127.0.0.1:8081/health >/dev/null; then
        break
    fi
    sleep 1
done
curl --fail --silent http://127.0.0.1:8766/health >/dev/null
curl --fail --silent http://127.0.0.1:8081/health >/dev/null
systemctl disable --now carelink-gateway.service
install -o root -g root -m 644 /etc/meeting-intelligence/Caddyfile.station /etc/caddy/Caddyfile
systemctl reload caddy.service
systemctl enable meeting-station.service scriberr-station.service
trap - ERR
if [ -f /opt/meeting-browser/provisioned ] && \
   [ -f /opt/meeting-browser/browser.env ] && \
   systemctl cat meeting-browser.service >/dev/null 2>&1; then
    systemctl enable --now meeting-browser.service
fi
touch /opt/meeting-intelligence/autostart-enabled
rm -f /opt/meeting-intelligence/autostart-paused
printf '%s\n' 'Meeting Station active. Carelink code, data, firewall and SSH configuration are preserved.'
