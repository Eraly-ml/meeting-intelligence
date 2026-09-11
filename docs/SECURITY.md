# Deployed security and recovery

Audited on 11 September 2026. These controls apply to the deployed `station/`
and `mac-worker/` pipeline. The native and older backend prototypes are separate.

| Boundary | Active protection |
| --- | --- |
| Browser → Cubie | HTTPS with a private station CA. HTTP GET/HEAD redirects; other HTTP methods receive 426. Secure/HttpOnly refresh and viewer cookies; same-origin CSP; API responses use `no-store`. Login bearer tokens and pairing tokens persist only in the tab session. |
| Cubie → Mac | Mutual TLS plus a separate random bearer token. The Cubie verifies the pinned CA and the Mac IP certificate; the Mac requires a client certificate with the correct purpose. Private LAN destinations only, no redirects or environment proxies, no HTTP fallback. |
| Active station data | gocryptfs 2.6.1 encrypted contents and filenames: recordings, transcripts, exports, SQLite databases, Chromium profile, station credentials and Caddy keys. Multipart temporary files use the encrypted data directory. |
| Mac data and recovery copies | FileVault is enabled. Private files have restricted permissions and are excluded from Git. Runtime inference runs under the existing outbound-IP sandbox, with loopback Ollama and installed models. |
| SSH | Ed25519 key authentication required for `radxa`. The default password was replaced with a random recovery/sudo password; a separate key login was verified before and after the change. |
| Previously exposed credentials | Station, worker, browser-controller and JWT secrets were rotated after encryption. Original account username/password remain unchanged. Existing sessions need a fresh login and pairing. |

The Cubie kernel lacks dm-crypt, so this deployment uses a reversible FUSE vault
instead of reformatting the board. Its encrypted backing directory is
`/var/lib/meeting-vault/cipher`; its unlocked view is
`/var/lib/meeting-vault/plain`. Bind mounts preserve the application's existing
paths. Service startup guards prevent operation while the vault is locked. The
board uses RAM-backed zram swap, with no disk swap observed during the audit.

The vault password is **not stored on the board**. It is sent on stdin over pinned
SSH when unlocking. The browser CA was regenerated inside encrypted storage;
only the public root certificate is trusted on the Mac. The worker CA private
key stays on the Mac. The certificate creation script never overwrites an
existing identity directory.

## Use and recovery

Open **https://192.168.8.57/meeting-intelligence**. This Mac trusts the station CA
for TLS. A different client needs the public CA installed through a trusted
channel. Never distribute a private key or disable certificate verification.
The mDNS hostname also has a certificate, but the literal IP avoids mDNS issues.

The installed Mac login agent now watches for the Cubie after a reboot and
automatically unlocks it over pinned SSH, then starts all four station services.
The Mac must have completed FileVault login and remain running on the LAN. The
agent prevents idle sleep; it does not override lid closure or a deliberate sleep.
For manual recovery, run from this workspace on the Mac:

```sh
python3 scripts/station-vault.py unlock
```

To stop the station and lock its data:

```sh
python3 scripts/station-vault.py lock
```

Unlock starts the archive, web server, Mac bridge and meeting browser. A deliberate
lock pauses automatic unlock until an explicit unlock, including across reboot.
The Carelink restore script disables station autostart. The station cannot serve
its website while its TLS keys are locked.

Unattended unlock trusts the board's pinned SSH identity and the logged-in Mac.
Compromise of either device or the board's unencrypted OS/SSH host key remains
outside this protection boundary. Use a deliberate vault lock when automatic
unlock is not wanted.

Keep these **private, Git-ignored** files on the FileVault-protected Mac:

| File | Purpose |
| --- | --- |
| `.local/security/vault-password` | Archive unlock credential; losing it loses access to the encrypted archive. |
| `.local/security/board-admin` | SSH private key. |
| `.local/security/board-sudo-password` | Board account recovery/sudo password; the old `radxa` password is retired. |
| `.local/station-secrets.json` | Current station pairing, worker, browser and JWT credentials plus the existing app account password. |
| `.local/security/worker-pki/` | Worker CA and device identities. Only the client key/certificate and public CA belong on the board. |
| `.local/security/station-browser-ca-v2.crt` | Public browser CA, safe to distribute after fingerprint verification. |

Worker device certificates expire after 90 days. Renew them before expiry; do not
solve expiry by disabling verification. The server certificate includes the Mac
IP, so a DHCP change requires reissuing it and updating the configured address.
Reserve both LAN addresses. `scripts/create-worker-pki.py` provisions a fresh
identity set for an installation; renewals and CA changes need a coordinated
switch on both devices.

Consistent snapshots and file manifests were verified before migration. Recovery
archives exist under `.local/security/` on the Mac and
`/var/lib/meeting-vault/plain/rollback/` inside the Cubie vault. Plaintext migration
copies and retired deployment credential files are removed only after verifying
their hashes against those copies. Keep an additional encrypted recovery copy
on separate media before relying on this as a long-term archive.

Carelink code and the original private `backups/carelink-20260911/` snapshots remain
untouched. To restore Carelink, unlock the vault and follow [RUNBOOK.md](RUNBOOK.md).
The restore script also restores the old Caddy state and removes Caddy's vault
guard so Carelink can reboot independently. SSH retains key authentication. Do
not run the station unlock/start command after restoring Carelink until explicitly
reactivating the station.

## Verified boundaries and remaining limits

Live checks accepted a correctly authenticated device and rejected clients with
no certificate. Tests also rejected untrusted CAs, wrong server names, and a
server certificate used as a client identity. HTTPS login, archive reads and
exports passed in Chrome with normal certificate verification. A complete
120-second synthetic recording finished in 62.567 seconds through HTTPS, mutual
TLS, local inference and encrypted archival. A lock/unlock test confirmed that
services do not start while locked, a wrong vault password is rejected, the Mac
link reconnects and the cached PDF survives byte-for-byte. Retired station tokens
are rejected. All 106 worker/station tests pass after the startup/join/audio changes.
An actual board reboot recovered HTTPS, the encrypted archive, the Mac connection
and the browser without intervention in 80.745 seconds from the reboot request.
The archived PDF remained byte-identical.

Encryption protects against LAN interception and reading the locked archive.
It does not protect data from an administrator or malware on an unlocked device.
Local loopback hops and in-memory processing are not separately encrypted. The
Cubie OS, metadata in system logs, file sizes and pre-existing Carelink files are
outside the station archive encryption boundary. Deleting old files cannot prove
forensic erasure of previous eMMC blocks; no whole-device encryption claim is made.
Browser/player memory and intentionally downloaded exports are controlled by the
client device. The Mac's FileVault protects local exports at rest.

Google Meet/Zoom/Teams require the provider's internet services. That optional
capture path is not a fully offline meeting. Upload/text processing uses local
models and local assets; physical WAN-disconnection acceptance remains pending.
This is a tested hackathon hardening pass, not an independent penetration test.

Implementation references: [HTTPX TLS contexts and client certificates](https://www.python-httpx.org/advanced/ssl/),
[gocryptfs design](https://nuetzlich.net/gocryptfs/),
[gocryptfs release 2.6.1](https://github.com/rfjakob/gocryptfs/releases/tag/v2.6.1),
[Apple FileVault](https://support.apple.com/guide/security/volume-encryption-with-filevault-sec4c6dc1b6e/web).
