#!/bin/sh
# Run as root over SSH; read the vault password on stdin, never from argv/env.
set -eu
exec 9>/run/lock/meeting-vault.lock
flock 9
if [ "${1:-}" = --automatic ]; then
    test -f /opt/meeting-intelligence/autostart-enabled || exit 0
    test ! -f /opt/meeting-intelligence/autostart-paused || exit 0
fi
plain=/var/lib/meeting-vault/plain
cipher=/var/lib/meeting-vault/cipher
if ! mountpoint -q "$plain"; then
    /usr/local/bin/gocryptfs -q -allow_other -nosyslog -passfile /dev/stdin "$cipher" "$plain" 9>&- >/dev/null 2>&1
fi
test "$(findmnt -n -o FSTYPE "$plain")" = fuse.gocryptfs
for name in meeting-station meeting-intelligence meeting-browser caddy; do
    test -d "$plain/$name"
    if ! mountpoint -q "/var/lib/$name"; then
        mount --bind "$plain/$name" "/var/lib/$name"
    fi
done
test -d "$plain/config"
if ! mountpoint -q /etc/meeting-intelligence; then
    mount --bind "$plain/config" /etc/meeting-intelligence
fi
systemctl start meeting-station scriberr-station caddy meeting-browser
rm -f /opt/meeting-intelligence/autostart-paused
printf '%s\n' 'Archive unlocked. Station, meeting browser and inference link started.'
