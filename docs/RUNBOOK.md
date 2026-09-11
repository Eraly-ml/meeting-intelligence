# Deployment and restoration runbook

This runbook describes the actual systemd deployment on the existing Debian 11 board. The upstream Docker instructions below the project introduction in the main README apply to ordinary Scriberr, not this hackathon station.

## Hardware and addresses

| Device/service | Current configuration |
|---|---|
| Meeting station | Radxa Cubie A7A, 6 GB RAM, Debian 11 CLI |
| Browser address (private demo LAN) | `http://192.168.8.57/meeting-intelligence` |
| HTTPS browser address | `https://radxa-cubie-a7a.local/meeting-intelligence` |
| Radxa LAN address | `192.168.8.57` |
| Mac worker | MacBook Air M5, 16 GB unified memory, `192.168.8.82:8765` |
| Ollama | Mac loopback, `127.0.0.1:11434` |
| Station bridge | Radxa loopback, `127.0.0.1:8766` |
| Go/embedded frontend | Radxa loopback, `127.0.0.1:8081` |

These IPs are current LAN leases. Reserve them in the router or update the Mac bind address and `MI_STATION_WORKER_URL` when the network changes. The station requires a literal private IP for the Mac destination. The browser connects to the Radxa and never needs to enter a Mac IP. If the Radxa lease changes, also update the explicit HTTP address in the Caddy configuration and Go `ALLOWED_ORIGINS`.

Caddy uses the board's existing local certificate authority. Clients must trust that authority to open HTTPS without a certificate prompt; distribute only its public root certificate. An mDNS-capable client should resolve `radxa-cubie-a7a.local` to the board. The explicit `http://192.168.8.57` entry point works without mDNS or certificate setup and does not redirect to HTTPS. It is an unencrypted fallback for the private demo LAN: login credentials, pairing tokens and meeting data travel over that LAN connection. Prefer the HTTPS hostname once its certificate is trusted. The IP address is not the configured HTTPS hostname.

If Chrome shows `ERR_ADDRESS_UNREACHABLE` while a direct request from the Mac
reaches the station, check **System Settings → Privacy & Security → Local
Network** and enable the browser's access, then fully quit and reopen the
browser. macOS controls this permission separately for each app; see
[Apple's Local Network settings](https://support.apple.com/en-gb/guide/mac-help/mchla4f49138/mac).
A successful command-line or isolated test-browser request does not establish
that the user's normal browser has this permission.

## Carelink backup before activation

The saved private backup directory is `backups/carelink-20260911/` in this Mac workspace. It contains:

- `carelink-consistent.tar.gz`: the consistent application/configuration snapshot taken for restoration.
- `before-stop.tar.gz`: the earlier pre-stop snapshot.
- `SHA256SUMS`, per-archive file manifests, and system/service/package inventories.

Archive checksums and the consistent snapshot's file manifest were verified during the takeover work. To check the archive checksums again from the workspace:

```sh
cd backups/carelink-20260911
shasum -a 256 -c SHA256SUMS
```

This backup includes private application configuration and data. It is excluded from Git, as are `.local/` runtime secrets, local databases and model files. It is **not a full disk image** and does not replace an OS/media backup. Carelink's original files are also retained on the board; routine rollback uses those files without unpacking an archive over the live OS.

Activation captures the original Caddy configuration and Carelink service state in `/var/backups/meeting-intelligence/carelink-before-takeover/`. The activation script requires that an off-board backup has already been checked. It starts and health-checks the new services before switching routing and disabling `carelink-gateway.service`.

## Build the station release on the Mac

The UI has a compile-time station flag and Go has a separate runtime station flag. Both are required for the board to remain an interface/archive appliance without bootstrapping upstream transcription software.

Use the project build script:

```sh
./scripts/build-station.sh
```

Its frontend step runs `VITE_MEETING_STATION=true npm run build` in `web/frontend`, copies `dist` to the Go embed directory `internal/web/dist`, and cross-compiles Go for Linux ARM64 with `CGO_ENABLED=0`. The embedded directory must exist before compiling or testing Go. Ordinary `npm run build` preserves the upstream Scriberr interface, so it must not replace the station build by accident.

No Docker daemon is needed on the Radxa. The station service uses a Python virtual environment compatible with Debian 11's Python 3.9. Build and dependency downloads occur while preparing the release; keep those separate from an offline demo.

## Mac inference service

The canonical service is `mac-worker/`. Follow [its setup instructions](../mac-worker/README.md) for dependency and model provisioning. The older `engine/` and native-client `backend/` are optional prototypes and are not part of this deployed pipeline.

The current provisioned launch helper is:

```sh
python3 scripts/run-provisioned-mac.py ollama
```

Run the worker separately:

```sh
python3 scripts/run-provisioned-mac.py worker
```

This helper uses the private workspace `.local/` environment and installed models. The per-user LaunchAgent labels are `com.meeting-intelligence.ollama` and `com.meeting-intelligence.worker`; the deployment installs them only after the actual services have been checked. Do not start a second foreground instance when its LaunchAgent already owns the port.

The provisioned launcher applies `deploy/mac/inference-local.sb`: the worker and Ollama can initiate direct IP connections only to this Mac, including its own LAN interfaces. The Radxa can still connect to the worker and receive responses. This supplements application URL restrictions and offline model settings; it is not a blanket DNS/IPC audit. Apple's `sandbox-exec` mechanism is deprecated, so validate it after macOS upgrades. The ordinary manual worker command does not apply this extra process policy.

The Mac configuration uses a private worker token, a LAN bind address for port 8765, local ffmpeg/Whisper/ONNX paths, and `MI_OLLAMA_URL=http://127.0.0.1:11434`. Ollama uses `OLLAMA_NO_CLOUD=1`, one parallel request and one loaded model. Runtime model downloads are disabled. Keep the Mac awake while processing; the board retains queued sources if the Mac sleeps or disconnects.

The selected ASR model is multilingual whisper.cpp large-v3-turbo q5 at `models/whisper/ggml-large-v3-turbo-q5_0.bin`. Base remains downloaded as a smaller fallback. Turbo corrected a name error in the local comparison; see [validation](VALIDATION.md) for timings and limits. Qwen3.5 4B runs locally on the M5. Other optional ASR profiles still need comparison on this hardware. Speaker separation is available only when the local diarization dependencies and models are present.

## Board installation and configuration

Stage the built application, a `meeting_intelligence_station-*.whl`, `deploy/radxa/`, and the two private environment files on the board. [install-station.sh](../deploy/radxa/install-station.sh) installs that staged release; it requires the dedicated `meeting-station` user and a provisioned `/opt/meeting-intelligence/venv` with its dependencies already present. The installer and [activation script](../deploy/radxa/activate-station.sh) are separate so all artifacts and configuration can be checked before changing the active application.

```sh
sudo /path/to/staged-release/deploy/radxa/install-station.sh /path/to/staged-release
```

| Path | Purpose |
|---|---|
| `/opt/meeting-intelligence/bin/scriberr` | Cross-compiled Go server with embedded station UI |
| `/opt/meeting-intelligence/venv` | Station Python environment |
| `/etc/meeting-intelligence/scriberr.env` | Go station settings and private account/session configuration |
| `/etc/meeting-intelligence/station.env` | Station token, separate Mac token, worker IP, archive settings |
| `/etc/meeting-intelligence/Caddyfile.station` | Prepared station routing |
| `/var/lib/meeting-intelligence` | Scriberr account database and local session state |
| `/var/lib/meeting-station` | Original sources, SQLite queue, results and exports |

`scriberr.env` must set `MI_STATION_MODE=true`, bind the Go service to `127.0.0.1:8081`, retain `SECURE_COOKIES=true`, and set `ALLOWED_ORIGINS=https://radxa-cubie-a7a.local,http://192.168.8.57`. `station.env` must bind to `127.0.0.1:8766`, use `/var/lib/meeting-station`, and set `MI_STATION_WORKER_URL=http://192.168.8.82:8765`. `MI_STATION_WORKER_TOKEN` matches the Mac's `MI_API_TOKEN`; `MI_STATION_TOKEN` is a different random token shared with station browsers. Keep the actual values in the private environment files, not in this repository or URLs.

The service units are [meeting-station.service](../deploy/radxa/meeting-station.service) and [scriberr-station.service](../deploy/radxa/scriberr-station.service). Both run as `meeting-station` with separate state directories. The bridge user also belongs to `audio` for an attached ALSA microphone. Recording availability requires `arecord` and an actual capture device; selecting Record in the UI starts the Radxa microphone, not the browser microphone.

The [Caddy configuration](../deploy/radxa/Caddyfile.station.example) retains `radxa-cubie-a7a.local` and its existing internal CA, and adds the explicit private HTTP origin above. `/api/meeting-worker/*` goes to the bridge with its prefix removed. Other browser requests go to the Go service. Only the HTTP site's Go proxy removes the `Secure` cookie attribute so browser login and refresh work on that origin; HTTPS cookies keep it. Caddy supports response header replacements through [`header_down`](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#headers). The station scripts retain Carelink's files, networking and SSH configuration.

After installation and configuration checks, run the staged activation script as root on the board. Inspect service status without printing secrets:

```sh
sudo /opt/meeting-intelligence/activate-station.sh
sudo systemctl status meeting-station.service scriberr-station.service caddy.service --no-pager
sudo journalctl -u meeting-station.service -u scriberr-station.service -n 60 --no-pager
```

Open either configured browser address, create or use the Scriberr station account, then pair the meeting archive with the **station token**. HTTP and HTTPS have separate browser sessions, so sign in and pair again when changing origins. Pairing checks the authenticated capabilities endpoint. Public `/health` endpoints report process liveness only. The browser's Connection control can disconnect its tab session.

## Online meeting browser

The optional [meeting-browser deployment](../deploy/meeting-browser/README.md) installs a separate Debian 12 filesystem with current ARM64 Chromium under `/opt/meeting-browser/rootfs`; it does not upgrade Debian 11 on the host. `meeting-browser.service` runs as a separate non-root account. Its profile and captured audio remain under `/var/lib/meeting-browser`.

Set `MI_BROWSER_TOKEN` in `/opt/meeting-browser/browser.env` and the matching `MI_STATION_BROWSER_TOKEN` in the station environment. Use a different random token from the station and Mac tokens. The controller, DevTools, VNC and relay ports stay on loopback. The station provides a narrowly scoped, short-lived HttpOnly viewer cookie; it does not put credentials in the viewer URL.

In the station interface, choose **Join online meeting**, paste the link, and select **Open meeting on Radxa**. Complete joining through the displayed Radxa browser as **Meeting Station (recording)**, with its microphone and camera off. The host may need to admit the participant, and some meeting policies require Google/Microsoft/Zoom sign-in. Enter account credentials yourself in the remote browser; they are not part of the station pairing form. Use the trusted HTTPS station origin for account sign-in.

After admission, select **Start meeting recording**. When finished, **Stop & process meeting** finalizes the full incoming-audio WAV and queues Mac processing. Leave the call using the meeting platform's own controls. A disconnected viewer does not stop recording. **Reconnect browser view** renews access; **Finish saved recording** retries an interrupted archive import. Capture is bounded to four hours or the configured size limit and one browser call at a time.

The browser join path needs internet access to the meeting platform. Transcription, diarization, Qwen and report generation remain local. Telegram is not required; it would be an optional internet-based control channel, not a way to join all meeting platforms.

## Acceptance checks and known limits

These steps are the acceptance procedure; they are not a claim that every step has already passed on the hardware.

1. Upload a prepared text transcript with a changed decision, an unnamed owner and a missing deadline. Verify the report and its source links, including any review flags.
2. Upload actual MP3, WAV and M4A recordings, including Russian, Kazakh, English and mixed speech. Compare the transcript and speaker changes with the source; record model, duration, latency and errors.
3. Stop the Mac worker, upload another source to the board, and confirm it stays in the archive. Restart the worker and verify processing continues without duplicate jobs.
4. If a board microphone is attached, record and stop a real session. Check the complete archived audio and a transcript from the resulting job.
5. Open the transcript, follow evidence links, load audio once, and seek from a timestamp. Check anonymous speaker labels rather than assuming they are identities.
6. Download JSON, CSV and PDF. Confirm Cyrillic/Kazakh glyphs, action-item columns, owners/deadlines and review flags. Completed results and exports should remain available when the Mac is offline.
7. Restart services during queued work and test explicit retry after failed inference. A completed UI job must have all exports durably cached on the board.
8. Disconnect WAN while retaining the office LAN and repeat a complete audio-to-report run. Inspect browser/runtime traffic for external requests. **A full WAN-disconnected validation has not yet been established.**
9. With WAN restored, join a real hosted meeting through the Radxa browser. Confirm that the participant is admitted, record spoken test decisions, and compare the archived sound, timestamps and final report. Opening a link or recording a synthetic tone does not establish successful meeting participation.

The system serializes Mac inference, and board recording is processed after stopping. Meeting detection, live transcript updates from station recording, and name-based speaker identification are not implemented in the deployed station. ASR accuracy, diarization quality, concurrency throughput and long-session behavior require representative tests rather than extrapolation from a short smoke recording.

Automated checks are available in `station/tests`, `mac-worker/tests`, and the Go packages. Frontend validation uses the TypeScript production build and ESLint; browser acceptance is a separate check. See the service READMEs for their exact test commands.

## Restore Carelink after the hackathon

The rollback script is [deploy/radxa/restore-carelink.sh](../deploy/radxa/restore-carelink.sh), copied to `/opt/meeting-intelligence/restore-carelink.sh` on the board. Run it there:

```sh
sudo /opt/meeting-intelligence/restore-carelink.sh
```

It validates and restores the saved pre-takeover Caddy file, restores the captured Carelink gateway enabled/active state, and disables the two meeting station services plus the optional meeting-browser service. The existing Carelink code and data are used in place. Meeting recordings, browser profile and the hackathon installation are retained for export or another deployment; neither application is erased.

Check Carelink's original HTTPS interface and its gateway service after rollback. If the original board files or storage have been damaged independently, use the private consistent backup and its manifests for a separate recovery; the configuration rollback script does not perform disk-image restoration.
