#!/bin/sh
# Install a current Debian browser userspace without upgrading the board OS.
set -eu

[ "$(id -u)" = 0 ] || { echo 'Run this provisioning script as root.' >&2; exit 1; }
if [ "${MI_BROWSER_MOUNT_NAMESPACE:-}" != 1 ]; then
    exec unshare --mount --propagation private env MI_BROWSER_MOUNT_NAMESPACE=1 "$0"
fi

browser_base=/opt/meeting-browser
browser_root=$browser_base/rootfs
browser_tools=$browser_base/bootstrap-tools
mkdir -p "$browser_base/bootstrap-cache" "$browser_tools"
cd "$browser_base/bootstrap-cache"
# apt verifies these packages against the host's signed repository metadata.
# Extracting them here leaves the host package database and libc unchanged.
apt-get download debootstrap=1.0.123+deb11u2 debian-archive-keyring
for browser_deb in ./*.deb; do
    dpkg-deb --extract "$browser_deb" "$browser_tools"
done
if [ ! -f "$browser_root/etc/debian_version" ]; then
    DEBOOTSTRAP_DIR="$browser_tools/usr/share/debootstrap" \
        "$browser_tools/usr/sbin/debootstrap" --arch=arm64 --variant=minbase --include=ca-certificates \
        --force-check-gpg \
        --keyring="$browser_tools/usr/share/keyrings/debian-archive-keyring.gpg" \
        bookworm "$browser_root" https://deb.debian.org/debian
fi

# Services must not start while packages are being provisioned.
cat > "$browser_root/usr/sbin/policy-rc.d" <<'POLICY'
#!/bin/sh
exit 101
POLICY
chmod 755 "$browser_root/usr/sbin/policy-rc.d"
cp /etc/resolv.conf "$browser_root/etc/resolv.conf"
cat > "$browser_root/etc/apt/sources.list" <<'SOURCES'
deb https://deb.debian.org/debian bookworm main
deb https://deb.debian.org/debian bookworm-updates main
deb https://security.debian.org/debian-security bookworm-security main
SOURCES
mount -t proc proc "$browser_root/proc"
mount --bind /dev "$browser_root/dev"
chroot "$browser_root" /usr/bin/env DEBIAN_FRONTEND=noninteractive apt-get update
chroot "$browser_root" /usr/bin/env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates chromium chromium-sandbox xvfb pulseaudio pulseaudio-utils \
    dbus-x11 x11vnc novnc python3-websockify python3-websocket ffmpeg xauth fonts-dejavu-core fonts-noto-core

if ! chroot "$browser_root" getent passwd meeting-browser >/dev/null; then
    chroot "$browser_root" useradd --uid 1996 --user-group --create-home \
        --home-dir /var/lib/meeting-browser --shell /usr/sbin/nologin meeting-browser
fi
mkdir -p "$browser_root/recordings" "$browser_root/run/meeting-browser" "$browser_root/opt/meeting-browser"
chroot "$browser_root" chown -R 1996:1996 /var/lib/meeting-browser /run/meeting-browser /recordings
install -m 755 "$browser_base/launch.sh" "$browser_root/opt/meeting-browser/launch.sh"
install -m 644 "$browser_base/join.js" "$browser_root/opt/meeting-browser/join.js"
chroot "$browser_root" dpkg-query -W > "$browser_base/packages.tsv"
chroot "$browser_root" /usr/bin/chromium --version
touch "$browser_base/provisioned"
echo 'Browser userspace provisioned. No meeting has been joined.'
