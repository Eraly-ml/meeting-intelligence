#!/usr/bin/env python3
"""Provision a private, offline worker CA and narrowly scoped TLS identities.

Keep the output directory on the Mac's encrypted disk. Only ca.crt,
station.crt and station.key go to the Cubie; never copy ca.key there.
Existing identities are never overwritten. No network requests are made.
"""
import argparse
import ipaddress
import os
from pathlib import Path
import subprocess


def provision(directory: Path, address: str):
    address = str(ipaddress.ip_address(address))
    os.umask(0o077)
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)

    def openssl(*args):
        subprocess.run(["openssl", *args], cwd=directory, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    (directory / "ca.cnf").write_text("""[req]
distinguished_name = dn
x509_extensions = ca
prompt = no
[dn]
CN = Meeting Station private worker CA
[ca]
basicConstraints = critical,CA:TRUE,pathlen:0
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
""")
    openssl("req", "-x509", "-newkey", "rsa:3072", "-nodes", "-sha256", "-days", "3650",
            "-keyout", "ca.key", "-out", "ca.crt", "-config", "ca.cnf")
    for name, purpose in (("worker", "serverAuth"), ("station", "clientAuth")):
        extension = "\nsubjectAltName = IP:" + address if name == "worker" else ""
        (directory / (name + ".cnf")).write_text(
            "basicConstraints = critical,CA:FALSE\nkeyUsage = critical,digitalSignature,keyEncipherment\n"
            "extendedKeyUsage = " + purpose + "\nsubjectKeyIdentifier = hash\nauthorityKeyIdentifier = keyid,issuer" + extension + "\n")
        openssl("req", "-new", "-newkey", "rsa:2048", "-nodes", "-sha256", "-subj",
                "/CN=Meeting Station " + name, "-keyout", name + ".key", "-out", name + ".csr")
        openssl("x509", "-req", "-in", name + ".csr", "-CA", "ca.crt", "-CAkey", "ca.key",
                "-CAcreateserial", "-sha256", "-days", "90", "-extfile", name + ".cnf", "-out", name + ".crt")
    openssl("verify", "-CAfile", "ca.crt", "-purpose", "sslserver", "worker.crt")
    openssl("verify", "-CAfile", "ca.crt", "-purpose", "sslclient", "station.crt")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--worker-ip", required=True)
    args = parser.parse_args()
    provision(args.directory.resolve(), args.worker_ip)
    print("Created worker identities; keep the CA key offline and renew leaf certificates within 90 days.")
