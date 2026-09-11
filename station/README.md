# Radxa meeting station bridge

This is the Python 3.9 service deployed on the Radxa Cubie A7A. Scriberr continues to serve the Go/React interface. The station saves source recordings and a SQLite job queue locally, sends work to the Mac over the private LAN, then archives the transcript, protocol, JSON, CSV and PDF exports on the board. A disconnected Mac leaves jobs queued with visible errors and automatic backoff; accepted uploads remain on the Radxa.

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

Use a literal private LAN IP in `MI_STATION_WORKER_URL`. Public addresses, arbitrary DNS names, redirects, and environment HTTP proxies are refused. Both tokens must contain at least 16 characters. Set `MI_STATION_WORKER_TOKEN` to the Mac's `MI_API_TOKEN`. Use one process: a filesystem lock prevents competing workers from sharing the archive.

After dependencies and model weights are installed, runtime needs only the LAN. No model download or external inference request is performed by this station.

## API

`GET /health` is public liveness only. All other routes require `Authorization: Bearer <station-token>`.

| Route | Behavior |
|---|---|
| `GET /v1/capabilities` | Mac readiness plus station microphone availability, upload limit and connection state |
| `POST /v1/jobs` | Multipart `manifest_json` and exactly one `audio` or `transcript`; optional UUID `Idempotency-Key` |
| `GET /v1/jobs` | Durable board archive; supports `limit` and `offset` |
| `GET /v1/jobs/{id}` | Current job and visible processing errors |
| `POST /v1/jobs/{id}/retry` | Retry a failed or cancelled job |
| `POST /v1/jobs/{id}/cancel` | Durable cooperative cancellation; source is retained |
| `GET /v1/jobs/{id}/result` | Locally cached transcript and protocol |
| `GET /v1/jobs/{id}/export/{pdf,json,csv}` | Locally archived download, available without the Mac |
| `GET /v1/jobs/{id}/audio` | Original source audio; authenticated range requests are supported |
| `POST /v1/recordings/start` | Explicit ALSA microphone recording; JSON title/language/diarization options |
| `POST /v1/recordings/{id}/stop` | Finalize the full WAV recording and enqueue Mac processing |

Accepted audio: MP3, WAV, M4A, WebM, OGG, CAF and FLAC. The default cap is 512 MiB, enforced on streamed request bytes and the actual source file. Text input is capped at 2 MiB. Acceptance follows a flushed source file and committed SQLite row. Reusing an idempotency key with changed source bytes or manifest returns 409.

The board microphone requires `arecord`, an ALSA capture device, and the service user's access to that device. The API reports unavailable when none is detected. Capture begins only after the explicit start endpoint. Each WAV is mono 16 kHz PCM, capped by a duration derived from the upload limit. A clean service stop finalizes active capture; an interrupted recording is retained with a retryable failure.

## State and recovery

`queued → preprocessing → transcribing → diarizing (optional) → extracting → validating → exporting → completed`

`recording` is a station-only pre-upload state. A job becomes `completed` only after all three exports and its result are present on the board. Failure and cancellation preserve the source. An interrupted forward request is replayed with the same UUID and content hash, so the Mac can acknowledge it without duplicate inference. Export downloads use fixed local paths, never paths returned by a remote worker.

Back up the entire `MI_STATION_DATA_DIR` with the service stopped (or use SQLite's online backup API for its database). It contains `station.sqlite3`, `sources/`, `exports/`, and temporary `incoming/` files. Do not remove Carelink backups when managing this separate archive.

## Validation

```sh
python -m pytest station/tests -q
```

The automated checks exercise authentication, streamed size limits, UUID and content idempotency, offline queue recovery, locally cached results and exports, durable cancellation/retry, private-address restrictions and redirects. Real microphone capture and model accuracy require hardware acceptance tests.
