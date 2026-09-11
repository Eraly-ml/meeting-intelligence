#!/usr/bin/env python3
"""Copy a verified, application-consistent Carelink backup over an existing SSH master.

No password is written to disk. Backup contents may contain private application data;
keep the destination outside version control. Carelink is restarted after the short
consistent-snapshot window, including when a backup fails.
"""
import argparse
import datetime
import getpass
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import tarfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--control", required=True)
    parser.add_argument("--known-hosts", required=True)
    parser.add_argument("--destination", required=True)
    args = parser.parse_args()
    destination = Path(args.destination).resolve()
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.umask(0o077)
    password = getpass.getpass("Board sudo password: ").encode() + b"\n"
    ssh = ["ssh", "-S", str(Path(args.control).resolve()), "-o", "BatchMode=yes", "-o",
           "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + str(Path(args.known_hosts).resolve()), args.host]

    def sudo(script, output=None):
        command = "sudo -S -p '' bash -c " + shlex.quote(script)
        result = subprocess.run(ssh + [command], input=password, stdout=output or subprocess.PIPE,
                                stderr=subprocess.PIPE)
        if result.returncode:
            (destination / "last-error.txt").write_bytes(result.stderr)
            raise RuntimeError("Remote backup command failed; details saved privately in last-error.txt")
        return result.stdout

    inventory = {
        "system.txt": "hostname; uname -a; cat /etc/os-release; free -h; df -hT; lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT; findmnt",
        "packages.txt": "dpkg-query -W -f='${binary:Package} ${Version}\\n'",
        "services.txt": "systemctl list-unit-files --no-pager; systemctl --no-pager --type=service --state=running",
        "carelink-units.txt": "systemctl cat carelink-gateway.service carelink-firewall.service caddy.service",
        "network.txt": "ip -brief address; ip route; ss -lntup; nft list ruleset 2>/dev/null; iptables-save 2>/dev/null; true",
        "audio.txt": "arecord -l 2>&1; true",
    }
    for filename, script in inventory.items():
        with (destination / filename).open("wb") as output:
            sudo(script, output)
    original = {}
    for unit in ["carelink-gateway.service", "carelink-firewall.service", "caddy.service"]:
        result = sudo("systemctl show " + unit + " -p ActiveState -p UnitFileState").decode()
        original[unit] = dict(line.split("=", 1) for line in result.splitlines() if "=" in line)
    manifest = {"created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "host": args.host,
                "services": original, "archives": {}, "complete": False}
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    archive_command = """set -euo pipefail
cd /
paths=()
for path in etc opt home root usr/local var/lib/carelink var/lib/caddy config; do
    if [ -e "$path" ]; then paths+=("$path"); fi
done
tar --numeric-owner --xattrs --acls -cpf - "${paths[@]}" | gzip -1
"""

    def copy_and_verify(name):
        archive = destination / name
        partial = archive.with_suffix(archive.suffix + ".partial")
        print("Copying " + name, flush=True)
        with partial.open("wb") as output:
            sudo(archive_command, output)
            output.flush()
            os.fsync(output.fileno())
        # Read every regular file, validating decompression and tar structure without extraction.
        entries = []
        with tarfile.open(partial, "r:gz") as source:
            for member in source:
                if member.isfile():
                    digest = hashlib.sha256()
                    stream = source.extractfile(member)
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                    entries.append({"path": member.name, "size": member.size, "sha256": digest.hexdigest()})
        if not any(item["path"].startswith("opt/carelink/") for item in entries):
            raise RuntimeError("Archive does not contain Carelink application files")
        if not any(item["path"].startswith("var/lib/carelink/") for item in entries):
            raise RuntimeError("Archive does not contain Carelink application data")
        partial.rename(archive)
        digest = hashlib.sha256()
        with archive.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        (destination / (name + ".files.json")).write_text(json.dumps(entries, indent=2) + "\n")
        manifest["archives"][name] = {"bytes": archive.stat().st_size, "files": len(entries), "sha256": digest.hexdigest()}
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print("Verified {}: {} files, {} bytes".format(name, len(entries), archive.stat().st_size), flush=True)

    copy_and_verify("before-stop.tar.gz")
    restart = original["carelink-gateway.service"].get("ActiveState") == "active"
    try:
        sudo("systemctl stop carelink-gateway.service")
        copy_and_verify("carelink-consistent.tar.gz")
    finally:
        if restart:
            sudo("systemctl start carelink-gateway.service")
    manifest["complete"] = True
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    (destination / "SHA256SUMS").write_text("".join("{}  {}\n".format(info["sha256"], name) for name, info in manifest["archives"].items()))
    print("Backup complete; original Carelink service state restored. " + str(destination), flush=True)


if __name__ == "__main__":
    main()
