#!/usr/bin/env python3
"""Recover the station after boot over pinned SSH; archive keys stay on the Mac."""
import argparse
import ipaddress
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
PROBE = '''if test ! -f /opt/meeting-intelligence/autostart-enabled; then printf disabled;
elif test -f /opt/meeting-intelligence/autostart-paused; then printf paused;
elif ! mountpoint -q /var/lib/meeting-station; then printf locked;
else for unit in meeting-station scriberr-station caddy meeting-browser; do
    if ! systemctl is-active --quiet "$unit"; then printf stopped; exit 0; fi
done; printf ready; fi'''


def ssh_command(host, root=ROOT):
    address = str(ipaddress.ip_address(host))
    return ['ssh', '-i', str(root / '.local/security/board-admin'), '-o', 'IdentitiesOnly=yes',
        '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=3',
        '-o', 'ServerAliveInterval=3', '-o', 'ServerAliveCountMax=2',
        '-o', 'UserKnownHostsFile=' + str(root / '.local/ssh_known_hosts'), 'radxa@' + address]


def operate(action, host, *, automatic=False, root=ROOT):
    security = root / '.local/security'
    password = (security / 'board-sudo-password').read_bytes().strip() + b'\n'
    if action == 'unlock':
        password += (security / 'vault-password').read_bytes()
    command = 'sudo -S -p "" /opt/meeting-intelligence/' + action + '-vault.sh'
    if automatic:
        command += ' --automatic'
    result = subprocess.run(ssh_command(host, root) + [command], input=password,
        capture_output=True, timeout=55, check=True)
    return result.stdout.decode().strip()


def ensure_ready(host, root=ROOT):
    result = subprocess.run(ssh_command(host, root) + [PROBE], capture_output=True, timeout=15, check=True)
    state = result.stdout.decode().strip()
    if state in {'locked', 'stopped'}:
        operate('unlock', host, automatic=True, root=root)
        return 'started'
    if state not in {'ready', 'disabled', 'paused'}:
        raise RuntimeError('Unrecognized station startup state')
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('lock', 'unlock', 'watch', 'ensure'))
    parser.add_argument('--host', default='192.168.8.57')
    args = parser.parse_args()
    ipaddress.ip_address(args.host)
    if args.action in {'lock','unlock'}:
        print(operate(args.action, args.host))
        return
    previous = None
    while True:
        try:
            state = ensure_ready(args.host)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            state = 'waiting for station'
        if state != previous:
            print('Station startup: ' + state, flush=True)
            previous = state
        if args.action == 'ensure':
            if state == 'waiting for station':
                raise SystemExit(1)
            return
        time.sleep(10)


if __name__ == '__main__':
    main()
