# Architecture

The hackathon deployment uses a **Radxa Cubie A7A with 6 GB RAM and Debian 11 CLI** as the meeting station, and a **MacBook Air M5 with 16 GB unified memory** as the inference worker. The native Mac client targets macOS 15 or later. The deployed browser is the custom Meeting Station interface, with selected server foundations retained from the open-source Scriberr repository.

```text
Browser on the office LAN
       │ HTTPS · private certificate authority
       ▼
Radxa Cubie A7A · 192.168.8.57
https://192.168.8.57
       │
       ├─ /meeting-intelligence
       │      Go + embedded React UI · 127.0.0.1:8081
       │      Meeting Station account authentication
       │
       └─ /api/meeting-worker/*
              Python station bridge · 127.0.0.1:8766
              station token authentication
              ├─ USB/ALSA microphone → complete WAV recording
              ├─ isolated Chromium → meeting playback → complete WAV
              ├─ audio/text uploads → original sources on disk
              ├─ SQLite archive and persistent forwarding queue
              └─ cached transcript, protocol, JSON, CSV, PDF and ICS
                         │
                         │ mutual TLS · pinned private CA + worker Bearer token
                         ▼
MacBook Air M5 · 192.168.8.84:8765
       Python worker · one inference job at a time
              ├─ ffmpeg → local ASR → optional speaker diarization
              ├─ Qwen → chronological protocol reconciliation
              ├─ evidence checks and semantic verification
              └─ Unicode JSON/CSV/PDF and RFC 5545 ICS generation
                         │
                         ▼
              Ollama · 127.0.0.1:11434
              local Qwen3.5 4B weights
```

The browser sends requests to its own station origin. It does not need the Mac address or its worker secret. Caddy strips `/api/meeting-worker` before forwarding to the station. The Mac worker token remains in the station's private environment; the browser's separate station token stays in `sessionStorage` and is sent in an Authorization header, never in a URL.

## Responsibilities and storage

| Component | Runs on | Owns |
|---|---|---|
| Existing Caddy | Radxa | HTTPS, existing hostname and local certificate authority, same-origin routing |
| Go + React | Radxa | Login, meeting interface, archive navigation, playback and downloads |
| Station bridge | Radxa | Original recordings, durable queue, board microphone capture, cached results and exports |
| Isolated Chromium + PulseAudio | Radxa | Online meeting participation and capture of incoming meeting sound |
| Mac worker | Mac | Audio normalization, ASR, optional diarization, Qwen orchestration, verification, export generation |
| Ollama | Mac loopback | Local Qwen inference only |

On the board, the application is installed under `/opt/meeting-intelligence`; private configuration is in `/etc/meeting-intelligence`. The station archive is `/var/lib/meeting-station` and Scriberr account data is `/var/lib/meeting-intelligence`. `meeting-station.service` and `scriberr-station.service` run as the dedicated `meeting-station` user. Docker is not installed or required for this deployment.

`VITE_MEETING_STATION=true` builds the custom branded login, meeting archive and evidence-backed report workspace, while omitting the upstream transcription, YouTube, cloud-provider and browser-recorder navigation. `MI_STATION_MODE=true` also disables the corresponding Go endpoints and prevents Go from initializing transcription adapters or downloading their environments. A normal upstream-compatible build retains the original features; these two flags define the Radxa appliance build.

## Recording and queue behavior

The **Record microphone** action uses the microphone attached to the Radxa through ALSA, so the Mac browser's microphone permission and secure-context support are not part of this capture path. The station reports whether a capture device is available. Recording begins only on an explicit start action, saves the full WAV on the board, and is sent for inference after stopping. Recording duration is bounded by the configured upload size. Live ASR during board recording is not implemented.

**Join & record** uses Chromium inside a separate Debian 12 filesystem on the Radxa; the host remains Debian 11. A dedicated non-root service owns its encrypted profile and PulseAudio playback sink. The controller disables microphone/camera, enters the participant name and requests admission. It watches call controls to distinguish waiting, joined, blocked and ended states. Host/account policies still apply. Automatic entry into the supplied Google Meet was verified; Zoom/Teams live admission remains unproven. The noVNC view is available for initial account setup or troubleshooting. No Telegram account is required.

Online recording captures the PulseAudio playback monitor into a full WAV, beginning before the join request. Call end or Leave & process finalizes the source; a background reconciler imports it into the durable station queue even when the UI is closed. Failed imports retain the source and retry. Failed admission is recorded as a failed capture rather than a normal report. One browser meeting is supported at a time, with transcription after stopping. Meeting traffic uses the provider's internet services; saved recordings and inference stay on the Radxa and Mac.

Imported MP3, WAV, M4A, WebM, CAF, OGG and FLAC files follow the same queue. The browser upload form offers MP3, WAV, M4A, WebM and CAF; the API additionally accepts OGG and FLAC. Existing text transcripts can skip ASR. Upload acknowledgement follows a flushed source file and a committed database row. A UUID idempotency key and source hash prevent replayed uploads from creating duplicate jobs.

```text
recording (board only) → queued → preprocessing → transcribing
                                      → diarizing (optional)
                                      → extracting → validating
                                      → exporting → completed
```

The Radxa can accept multiple jobs while the Mac processes them serially. A disconnected Mac leaves sources and jobs on the station, with visible status and automatic retry backoff. Interrupted Mac inference becomes a retryable failure. Cancellation preserves the source and lets an active model call finish safely. The station marks a meeting complete only after the result and its JSON, CSV, PDF and calendar exports are cached on the board; those remain accessible without the Mac.

The UI polls status and archive metadata. Audio loads automatically with authentication when a meeting is selected, without re-downloading on every status update. Transcripts show timestamps and anonymous speaker labels. Evidence links open the cited transcript and seek the player. PDFs include the full transcript; an empty extraction is marked for review and starts the transcript on the first page.

## Local models and what readiness means

The worker uses full multilingual **Whisper large-v3**, and **large-v3-turbo q5** for explicitly English input based on the available fixture. **Qwen3.5 4B** runs through loopback Ollama. Recognition probabilities are retained as review signals, not correctness scores; claims citing uncertain speech remain in review. See [accuracy measurements and missing evidence](ACCURACY.md).

Diarization uses local Sherpa ONNX segmentation and speaker-embedding models when provisioned and requested. It distinguishes anonymous voices; it cannot identify participants by name. Uncertain overlapping spans remain unassigned. The Mac serializes heavy jobs to fit its 16 GB memory; performance and accuracy must be measured on representative meetings.

Capabilities distinguish a reachable worker from installed speech-model resources. The UI does not label a connected worker as a fully validated model pipeline. Qwen local-weight metadata is checked at inference time. Reports preserve source references and separately expose `source_check` and `review_status`, including `needs_review`. Generated output cannot claim human confirmation. Executive summaries copy supported structured facts after citation and semantic checks, with aligned source references shown in the UI. Unchecked generated summary prose is discarded and cannot enter chronological reconciliation. If semantic verification is disabled, the worker leaves the summary empty. These checks still depend on imperfect ASR and model judgments; they do not establish complete accuracy.

## Offline operation and preserved Carelink system

All runtime inference uses installed binaries and local model files. Ollama binds to loopback with cloud features disabled. The station refuses public worker destinations, redirects and environment HTTP proxies. The station UI loads its fonts and assets locally, without Google Fonts or CDN requests. Downloads during provisioning are separate from runtime inference. During the exact two-minute run, the optional online browser was stopped and process-level socket inspection found only the Radxa–Mac worker connection plus loopback Ollama on the Mac, with no established external peer on the Radxa. A physically WAN-disconnected run remains outstanding.

Carelink is preserved for restoration. Its consistent application/configuration backup is stored privately on this Mac at `backups/carelink-20260911/`, with checksums and file manifests. This is an application/configuration backup, **not a bootable disk image**. The board's existing Carelink code and data remain in place while its service and active Caddy routing are switched for the hackathon. The base OS remains; SSH now requires keys, and the station has a new private TLS authority. The old Caddy state is preserved for rollback. See [the runbook](RUNBOOK.md) for activation and rollback.


## Encryption boundary

HTTPS protects browser actions; mutual TLS authenticates both devices. The Radxa archive, browser profile, account database, credentials and Caddy keys live in a gocryptfs vault. Its password stays on the FileVault-enabled Mac and is sent only through pinned SSH when unlocking. Local loopback connections remain within each device. Services fail closed while the archive is locked. The OS filesystem itself is not encrypted. See [security and recovery](SECURITY.md).
