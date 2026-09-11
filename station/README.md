# Radxa meeting station bridge

This is the Python 3.9 service deployed on the Radxa Cubie A7A. The custom Meeting Station interface is embedded in the Go server. The station saves source recordings and a SQLite job queue locally, sends work to the Mac over the private LAN, then archives the transcript, protocol, JSON, CSV, PDF and ICS exports on the board. A disconnected Mac leaves jobs queued with visible errors and automatic backoff; accepted uploads remain on the Radxa.

The Mac runs `mac-worker/` on port 8765. The station runs on loopback port 8766. Caddy exposes the station as the same-origin `/api/meeting-worker/*` path, removing that prefix before forwarding. The browser needs only a station pairing token; the separate Mac worker token stays in the station environment.

## Install on Debian 11

```sh
python3 -m venv .venv
.venv/bin/pip install ./station
cp station/.env.example station.env
# Set the private Mac IP and both tokens in station.env, then load it:
set -a
. ./station.env
set +a
.venv/bin/meeting-station
```

Use a literal private LAN IP and HTTPS in `MI_STATION_WORKER_URL`, with the pinned CA, client certificate and client key paths from `.env.example`. LAN HTTP and incomplete TLS configuration are refused, as are public addresses, arbitrary DNS names, redirects and environment proxies. Both tokens must contain at least 16 characters. Set `MI_STATION_WORKER_TOKEN` to the Mac's `MI_API_TOKEN`. Use one process: a filesystem lock prevents competing workers from sharing the archive. The deployed data and credentials reside in an encrypted vault; see [security and recovery](../docs/SECURITY.md).

After dependencies and model weights are installed, uploads, inference and reports need only the LAN. Joining Google Meet, Zoom or Teams requires internet access to that meeting provider. No external inference request is performed by this station.

## API

`GET /health` is public liveness only. Control and archive routes require `Authorization: Bearer <station-token>`. The embedded browser view uses a separate short-lived cookie, described below; that cookie cannot authorize control or archive routes.

| Route | Behavior |
|---|---|
| `GET /v1/capabilities` | Mac readiness plus station microphone availability, upload limit and connection state |
| `POST /v1/jobs` | Multipart `manifest_json` and exactly one `audio` or `transcript`; optional UUID `Idempotency-Key` |
| `GET /v1/jobs` | Durable board archive; supports `limit` and `offset` |
| `GET /v1/jobs/{id}` | Current job and visible processing errors |
| `POST /v1/jobs/{id}/retry` | Retry a failed or cancelled job |
| `POST /v1/jobs/{id}/cancel` | Durable cooperative cancellation; source is retained |
| `GET /v1/jobs/{id}/result` | Locally cached transcript and protocol |
| `GET /v1/jobs/{id}/export/{pdf,json,csv,ics}` | Locally archived download, available without the Mac |
| `GET /v1/jobs/{id}/audio` | Original source audio; authenticated range requests are supported |
| `POST /v1/recordings/start` | Explicit ALSA microphone recording; JSON title/language/diarization options |
| `POST /v1/recordings/{id}/stop` | Finalize the full WAV recording and enqueue Mac processing |
| `GET /v1/browser/status` | Isolated Chromium availability, opened page status, active capture and retained recordings |
| `POST /v1/browser/open` | Navigate a validated HTTPS Meet, Zoom or Teams link; body `{ "url": "…" }` |
| `POST /v1/browser/session` | Issue a 30-minute viewing cookie and return the same-origin `viewer_path` |
| `GET /v1/browser/view/{asset}` | Authenticated local noVNC assets; viewing cookie required |
| `WS /v1/browser/view/websockify` | Authenticated remote browser controls; viewing cookie and matching Origin required |
| `POST /v1/browser/recordings/start` | Record browser output; same title/language/diarization options as microphone recording |
| `POST /v1/browser/recordings/{id}/stop` | Finalize browser WAV, archive it on the station and enqueue Mac processing; safe to retry |

Accepted audio: MP3, WAV, M4A, WebM, OGG, CAF and FLAC. The default cap is 512 MiB, enforced on streamed request bytes and the actual source file. Text input is capped at 2 MiB. Acceptance follows a flushed source file and committed SQLite row. Reusing an idempotency key with changed source bytes or manifest returns 409.

The board microphone requires `arecord`, an ALSA capture device, and the service user's access to that device. The API reports unavailable when none is detected. Capture begins only after the explicit start endpoint. Each WAV is mono 16 kHz PCM, capped by a duration derived from the upload limit. A clean service stop finalizes active capture; an interrupted recording is retained with a retryable failure.

## Meeting browser

The optional `deploy/meeting-browser/` service provides current Chromium in an isolated Debian 12 root filesystem. It leaves the board's Debian 11 installation and preserved Carelink files intact. Its controller listens only on `127.0.0.1:8770`; set `MI_STATION_BROWSER_TOKEN` to the same secret as its `MI_BROWSER_TOKEN`. This is a separate credential from the station pairing and Mac worker tokens. Without it, the station reports the meeting browser as unavailable while uploads remain usable.

Open the meeting link, use the embedded browser to join as **Meeting Station**, and wait for host admission if required. Opening a link is reported as `opened`, never as proof that the participant joined. Host policies or provider sign-in requirements can require a person to finish joining; Meet, Zoom and Teams are supported link formats, not a promise of automatic admission on every meeting.

Start browser recording explicitly after admission. FFmpeg captures only the browser's PulseAudio output monitor, in mono 16 kHz PCM, for at most four hours or 512 MiB. The browser has a silent microphone source so it does not feed the meeting back into itself. Capturing speech and assigning anonymous speakers can produce errors; review the transcript and evidence before treating a report as final.

The viewing cookie is HttpOnly, SameSite Strict, scoped to the view path, and Secure on HTTPS. The API returns a viewer URL containing only a nonsecret WebSocket path. The UI renews its session while open. The station checks WebSocket Origin against its own origin, then forwards to authenticated loopback websockify using the private browser token. It never passes that token to the user's browser. Raw websockify rejects browser Origin headers and requests without that token.

## State and recovery

`queued → preprocessing → transcribing → diarizing (optional) → extracting → validating → exporting → completed`

`recording` is a station-only pre-upload state. A job becomes `completed` only after all four exports and its result are present on the board. Failure and cancellation preserve the source. An interrupted forward request is replayed with the same UUID and content hash, so the Mac can acknowledge it without duplicate inference. Export downloads use fixed local paths, never paths returned by a remote worker.

Browser captures first remain in `/var/lib/meeting-browser/recordings/` with UUID names and status metadata. Stopping imports and validates the WAV before making it available to the Mac queue. If import fails or the station restarts, the job stays in `recording` with `BROWSER_IMPORT_PENDING`; use **Stop and archive** again to recover the retained audio. A confirmed abnormal capture exit remains visible as a failed job even if a readable partial WAV was archived. Browser originals are retained after successful import and require deliberate archive management; the service never silently deletes them.

Back up the entire `MI_STATION_DATA_DIR` with the service stopped (or use SQLite's online backup API for its database). It contains `station.sqlite3`, `sources/`, `exports/`, and temporary `incoming/` files. Do not remove Carelink backups when managing this separate archive.

## Validation

```sh
python -m pytest station/tests -q
```

The automated checks exercise authentication, streamed size limits, UUID and content idempotency, offline queue recovery, locally cached exports, cancellation/retry, private addresses, browser URL restrictions, view-cookie isolation, same-origin WebSockets, and capture-to-archive recovery. Actual meeting admission, microphone capture and model accuracy require hardware acceptance tests.
