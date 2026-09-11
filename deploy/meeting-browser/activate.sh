#!/bin/sh
# Activate only the optional browser component; do not join a meeting.
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run this activation script as root.' >&2; exit 1; }
browser_base=/opt/meeting-browser
test -f "$browser_base/provisioned"
test -f "$browser_base/browser.env"
test -f "$browser_base/controller.py"
test -f "$browser_base/join.js"
test -f "$browser_base/browser_auth.py"
test -f "$browser_base/launch.sh"

if ! getent passwd meeting-browser >/dev/null; then
    if getent passwd 1996 >/dev/null || getent group 1996 >/dev/null; then
        echo 'UID/GID 1996 is already in use. Choose a matching unused identity in host and rootfs first.' >&2
        exit 1
    fi
    groupadd --gid 1996 meeting-browser
    useradd --uid 1996 --gid meeting-browser --create-home \
        --home-dir /var/lib/meeting-browser --shell /usr/sbin/nologin meeting-browser
fi
test "$(id -u meeting-browser)" = 1996
test "$(id -g meeting-browser)" = 1996
install -d -o meeting-browser -g meeting-browser -m 0700 \
    /var/lib/meeting-browser /var/lib/meeting-browser/recordings
install -m 0755 "$browser_base/launch.sh" "$browser_base/rootfs/opt/meeting-browser/launch.sh"
install -m 0644 "$browser_base/controller.py" "$browser_base/rootfs/opt/meeting-browser/controller.py"
install -m 0644 "$browser_base/join.js" "$browser_base/rootfs/opt/meeting-browser/join.js"
install -m 0644 "$browser_base/browser_auth.py" "$browser_base/rootfs/opt/meeting-browser/browser_auth.py"
chmod 0600 "$browser_base/browser.env"
chown root:root "$browser_base/browser.env"
install -m 0644 "$browser_base/meeting-browser.service" /etc/systemd/system/meeting-browser.service
systemctl daemon-reload
systemctl enable meeting-browser.service
systemctl restart meeting-browser.service
echo 'Browser service started. Verify its authenticated health endpoint before use.'
