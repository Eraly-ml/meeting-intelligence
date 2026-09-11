# Architecture

The hackathon deployment uses a **Radxa Cubie A7A with 6 GB RAM and Debian 11 CLI** as the meeting station, and a **MacBook Air M5 with 16 GB unified memory** as the inference worker. The native Mac client targets macOS 15 or later, but the deployed station interface is the browser application in this Scriberr fork.

```text
Browser on the office LAN
       │ HTTPS · existing Caddy local certificate authority
       ▼
Radxa Cubie A7A · 192.168.8.57
https://radxa-cubie-a7a.local
       │
       ├─ /meeting-intelligence
       │      Go + embedded React UI · 127.0.0.1:8081
       │      Scriberr account authentication
       │
       └─ /api/meeting-worker/*
              Python station bridge · 127.0.0.1:8766
              station token authentication
              ├─ USB/ALSA microphone → complete WAV recording
              ├─ audio/text uploads → original sources on disk
              ├─ SQLite archive and persistent forwarding queue
              └─ cached transcript, protocol, JSON, CSV and PDF
                         │
                         │ private LAN · separate worker Bearer token
                         ▼
MacBook Air M5 · 192.168.8.82:8765
       Python worker · one inference job at a time
              ├─ ffmpeg → local ASR → optional speaker diarization
              ├─ Qwen → chronological protocol reconciliation
              ├─ evidence checks and semantic verification
              └─ Unicode JSON/CSV/PDF generation
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
| Mac worker | Mac | Audio normalization, ASR, optional diarization, Qwen orchestration, verification, export generation |
| Ollama | Mac loopback | Local Qwen inference only |

On the board, the application is installed under `/opt/meeting-intelligence`; private configuration is in `/etc/meeting-intelligence`. The station archive is `/var/lib/meeting-station` and Scriberr account data is `/var/lib/meeting-intelligence`. `meeting-station.service` and `scriberr-station.service` run as the dedicated `meeting-station` user. Docker is not installed or required for this deployment.

`VITE_MEETING_STATION=true` builds a browser interface that opens the meeting archive and omits the upstream transcription, YouTube, cloud-provider and browser-recorder navigation. `MI_STATION_MODE=true` also disables the corresponding Go endpoints and prevents Go from initializing transcription adapters or downloading their environments. A normal Scriberr build retains the upstream features; these two flags define the Radxa appliance build.

## Recording and queue behavior

Station recording uses the microphone attached to the Radxa through ALSA, so a browser's microphone permission and secure-context support are not part of this capture path. The station reports whether a capture device is available. The recording begins only on an explicit start action, saves the full WAV on the board, and is sent for inference after stopping. Recording duration is bounded by the configured upload size. Live ASR during this board recording is not implemented.

Imported MP3, WAV, M4A, WebM, CAF, OGG and FLAC files follow the same queue. The browser upload form offers MP3, WAV, M4A, WebM and CAF; the API additionally accepts OGG and FLAC. Existing text transcripts can skip ASR. Upload acknowledgement follows a flushed source file and a committed database row. A UUID idempotency key and source hash prevent replayed uploads from creating duplicate jobs.

```text
recording (board only) → queued → preprocessing → transcribing
                                      → diarizing (optional)
                                      → extracting → validating
                                      → exporting → completed
```

The Radxa can accept multiple jobs while the Mac processes them serially. A disconnected Mac leaves sources and jobs on the station, with visible status and automatic retry backoff. Interrupted Mac inference becomes a retryable failure. Cancellation preserves the source and lets an active model call finish safely. The station marks a meeting complete only after the result and all three export files are cached on the board; those remain accessible without the Mac.

The UI polls status and archive metadata. Audio is fetched with authentication only when the user loads that recording, and is not re-downloaded on every status update. Transcripts show timestamps and anonymous speaker labels. Evidence links open the cited transcript passages; an available audio player can seek to the source timestamp.

## Local models and what readiness means

The provisioned profile uses multilingual **whisper.cpp large-v3-turbo q5** for ASR and **Qwen3.5 4B** through local Ollama. Turbo corrected a name error from base on the local synthetic fixture, with about 829 MiB peak ASR process footprint; base remains a smaller fallback. This does not establish accuracy for Kazakh, Russian, mixed-language speech or overlapping voices. Optional Shyngys, MLX Distil-Whisper and GigaAM adapters require their own installed libraries and local model directories. They have not been benchmarked in this deployment.

Diarization uses local Sherpa ONNX segmentation and speaker-embedding models when provisioned and requested. It distinguishes anonymous voices; it cannot identify participants by name. Uncertain overlapping spans remain unassigned. The Mac serializes heavy jobs to fit its 16 GB memory; performance and accuracy must be measured on representative meetings.

Capabilities distinguish a reachable worker from installed speech-model resources. The UI does not label a connected worker as a fully validated model pipeline. Qwen local-weight metadata is checked at inference time. Reports preserve source references and separately expose `source_check` and `review_status`, including `needs_review`. Generated output cannot claim human confirmation. Source and semantic checks reduce unsupported extraction, but summary prose is not independently verified.

## Offline operation and preserved Carelink system

All runtime inference uses installed binaries and local model files. Ollama binds to loopback with cloud features disabled. The station refuses public worker destinations, redirects and environment HTTP proxies. The station UI loads its fonts and assets locally, without Google Fonts or CDN requests. Downloads during provisioning are separate from runtime inference. A full WAN-disconnected acceptance run still needs to be recorded; configuration and automated tests alone are not evidence that this hardware passed it.

Carelink is preserved for restoration. Its consistent application/configuration backup is stored privately on this Mac at `backups/carelink-20260911/`, with checksums and file manifests. This is an application/configuration backup, **not a bootable disk image**. The board's existing Carelink code and data remain in place while its service and active Caddy routing are switched for the hackathon. SSH, the OS and existing certificate authority are retained. See [the runbook](RUNBOOK.md) for activation and rollback.
