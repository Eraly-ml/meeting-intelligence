#!/bin/bash
# Run inside the Debian browser rootfs as UID 1996, with its own runtime directory.
set -euo pipefail
export DISPLAY=:99
export XDG_RUNTIME_DIR=/run/meeting-browser
export PULSE_SERVER=unix:/run/meeting-browser/pulse/native
export PULSE_SINK=meeting_output
export PULSE_SOURCE=meeting_silence.monitor
mkdir -p "$XDG_RUNTIME_DIR" /var/lib/meeting-browser/profile
chmod 700 "$XDG_RUNTIME_DIR"

browser_children=''
browser_cleanup() {
    trap - EXIT INT TERM
    for browser_pid in $browser_children; do kill "$browser_pid" 2>/dev/null || true; done
    wait || true
}
trap browser_cleanup EXIT INT TERM

Xvfb "$DISPLAY" -screen 0 1280x800x24 -nolisten tcp -ac &
browser_children="$browser_children $!"
pulseaudio --daemonize=no --exit-idle-time=-1 --log-target=stderr -n \
    --load="module-native-protocol-unix socket=/run/meeting-browser/pulse/native" \
    --load="module-null-sink sink_name=meeting_output rate=48000 channels=2" \
    --load="module-null-sink sink_name=meeting_silence rate=48000 channels=1" &
browser_children="$browser_children $!"
browser_ready=0
while [ "$browser_ready" -lt 100 ]; do
    if pactl info >/dev/null 2>&1 && [ -S /tmp/.X11-unix/X99 ]; then break; fi
    sleep 0.1
    browser_ready=$((browser_ready + 1))
done
pactl set-default-sink meeting_output
pactl set-default-source meeting_silence.monitor
x11vnc -display "$DISPLAY" -localhost -rfbport 5900 -forever -shared -nopw \
    -noxdamage -quiet &
browser_children="$browser_children $!"
PYTHONPATH=/opt/meeting-browser websockify --auth-plugin=browser_auth.StationAuth \
    --web=/usr/share/novnc 127.0.0.1:6080 127.0.0.1:5900 &
browser_children="$browser_children $!"
dbus-run-session -- chromium --user-data-dir=/var/lib/meeting-browser/profile \
    --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 \
    --no-first-run --no-default-browser-check --disable-dev-shm-usage \
    --disable-background-networking --disable-component-update \
    --disable-sync --disable-translate --password-store=basic \
    --autoplay-policy=no-user-gesture-required \
    --window-size=1280,800 about:blank &
browser_pid=$!
browser_children="$browser_children $browser_pid"
python3 -c 'import socket,time
for attempt in range(150):
    try:
        socket.create_connection(("127.0.0.1",9222),timeout=.2).close(); break
    except OSError: time.sleep(.2)
else: raise SystemExit("Chromium debugging endpoint did not become ready")'
python3 /opt/meeting-browser/controller.py &
browser_children="$browser_children $!"
# Restart the whole isolated session if any essential process exits.
wait -n $browser_children
exit 1
