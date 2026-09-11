#!/usr/bin/env bash
# Install a staged release. This does not switch the website or stop Carelink.
# stage contains build/scriberr-linux-arm64, station.whl, deploy/radxa/, and
# private station.env + scriberr.env. The isolated Python venv must exist first.
set -euo pipefail
test "$(id -u)" = 0
stage="${1:?Provide the absolute staged release directory}"
test -x /opt/meeting-intelligence/venv/bin/python
test -f "$stage/build/scriberr-linux-arm64"
id meeting-station >/dev/null
install -d -m 755 /opt/meeting-intelligence/bin
install -d -m 750 -o root -g meeting-station /etc/meeting-intelligence
/opt/meeting-intelligence/venv/bin/python -m pip install --no-deps --force-reinstall "$stage"/meeting_intelligence_station-*.whl
install -m 755 "$stage/build/scriberr-linux-arm64" /opt/meeting-intelligence/bin/scriberr.new
mv /opt/meeting-intelligence/bin/scriberr.new /opt/meeting-intelligence/bin/scriberr
for name in station scriberr; do
    install -o root -g root -m 600 "$stage/$name.env" "/etc/meeting-intelligence/$name.env"
done
for name in meeting-station scriberr-station; do
    install -m 644 "$stage/deploy/radxa/$name.service" "/etc/systemd/system/$name.service"
done
install -m 644 "$stage/deploy/radxa/Caddyfile.station.example" /etc/meeting-intelligence/Caddyfile.station
install -m 755 "$stage/deploy/radxa/restore-carelink.sh" /opt/meeting-intelligence/restore-carelink.sh
install -m 755 "$stage/deploy/radxa/activate-station.sh" /opt/meeting-intelligence/activate-station.sh
systemctl daemon-reload
printf '%s\n' 'Release installed. Run /opt/meeting-intelligence/activate-station.sh after checking the off-board backup.'
