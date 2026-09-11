#!/bin/sh
# Stop the station before unmounting; busy mounts fail instead of forced data loss.
set -eu
exec 9>/run/lock/meeting-vault.lock
flock 9
# A deliberate lock stays locked until explicitly unlocked, including reboot.
touch /opt/meeting-intelligence/autostart-paused
systemctl stop caddy meeting-station scriberr-station meeting-browser
for path in /var/lib/meeting-station /var/lib/meeting-intelligence /var/lib/meeting-browser /var/lib/caddy /etc/meeting-intelligence; do
    if mountpoint -q "$path"; then umount "$path"; fi
done
if mountpoint -q /var/lib/meeting-vault/plain; then
    fusermount -u /var/lib/meeting-vault/plain
fi
printf '%s\n' 'Archive locked. The unlock key is not stored on the board.'
