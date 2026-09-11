# MeetingBox — optional native-client prototype

This document describes the earlier Swift client and text-only hub. They are retained for development and are not the deployed Radxa/Scriberr station. For the current system, use [the station runbook](RUNBOOK.md). Implementation and validation notes below apply to this prototype.

A native Mac client and a private meeting hub. The Mac records and transcribes audio with local whisper.cpp; the hub turns transcripts into reports with a locally installed Qwen model. No cloud inference, automatic model downloads, analytics, or external web assets are part of the application runtime.

This repository is a working MVP foundation. The confirmed targets are **macOS 15+** for the client and **Radxa Cubie A7A** for the hub. Live device capture, real model accuracy, and board performance still need testing on your hardware. The hub runs Python 3.9+ on macOS or Linux; the actual board is now confirmed as 6 GB RAM and Debian 11, while this prototype's hub performance remains unmeasured. Full Xcode is optional for the SwiftPM build, and required for XcodeBuildMCP workflows.

## What is implemented

- Manual full-session capture of system audio and microphone, saved as separate local CAF tracks; audio is converted into 12-second WAV chunks independently of inference and networking.
- Local whisper.cpp execution, stable transcript IDs, persisted chunks and results, safe retries, and a final transcription drain before the meeting ends.
- MP3, WAV, M4A file import and CAF recovery import using AVFoundation on the Mac. Imported originals are copied locally.
- Native transcript/report UI, source timestamps, JSON export, configuration, and pairing token storage in macOS Keychain.
- Offline meeting creation, an on-disk transcript outbox, idempotent replay, contiguous acknowledgments, and WebSocket report updates.
- Authenticated FastAPI hub, SQLite WAL storage, one durable Qwen worker, coalesced provisional reports, final reconciliation, source evidence validation, history/search, and JSON/Markdown export.
- Local-only Ollama endpoint checks, cloud model rejection, disabled redirects and environment proxies, bounded input/output, durable retries, and restart recovery.

Speaker labels identify audio sources: `local`, `remote`, or `unknown`. They do not identify individual remote participants. Use headphones to avoid microphone echo duplicating remote speech.

## Run the hub

Install Python dependencies once while online, or provision them from a local wheelhouse:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e 'backend[test]'
cp backend/.env.example backend/.env
```

Generate a token with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`, put it in `backend/.env` as `MEETINGBOX_TOKEN`, and enter the same token in the Mac app. Keep `backend/.env` private; it is ignored by Git. The root `.env` is reserved for the canonical Mac worker.

Install Ollama and download your chosen Qwen model during provisioning. `qwen2.5:3b` is the configurable starting model; its memory use and latency have not been measured on your Radxa Cubie A7A. Benchmark this optional hub profile before selecting it for a separate demo. The app never pulls a model automatically.

Start the separately installed Ollama service with these environment variables (stop any existing instance first, or apply them to its service configuration):

```sh
OLLAMA_NO_CLOUD=1 OLLAMA_HOST=127.0.0.1:11434 OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=1 ollama serve
```

In another terminal, during provisioning only:

```sh
ollama pull qwen2.5:3b
sh scripts/run-hub.sh
```

The hub binds to `127.0.0.1:8000` by default. For the office appliance, bind explicitly to its LAN interface or use `MEETINGBOX_BIND=0.0.0.0 sh scripts/run-hub.sh` on your trusted demo LAN. Configure the Mac with `http://<private-ip>:8000` or an existing `.local` hostname. MeetingBox does not register a Bonjour hostname itself.

The Bearer token protects application access but does not encrypt HTTP. Use HTTPS with a trusted local certificate for transport encryption. This MVP uses one office token, so paired clients share access to hub meetings; it has no per-user authorization. The hub's file lock enforces one process/worker per database.

## Build and run the Mac app

Install the Swift toolchain/Apple command-line tools. Build [whisper.cpp](https://github.com/ggml-org/whisper.cpp) with Metal support and download a multilingual model such as `base` during provisioning. Set the executable path and the downloaded `ggml-*.bin` path in the app. For example, the upstream build uses:

```sh
git clone https://github.com/ggml-org/whisper.cpp.git /tmp/meetingbox-whisper
cd /tmp/meetingbox-whisper
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j 4
sh models/download-ggml-model.sh base
```

Keep the resulting executable, its libraries, and model in a durable local location. Return to this repository and build:

```sh
sh scripts/build-mac.sh
open build/MeetingBox.app
```

Configure the hub address/token, Whisper binary/model paths, and language (`auto`, `en`, `ru`, etc.) in Settings. Click **Record meeting** and allow microphone and Screen & System Audio Recording access. Start audio in Meet/Zoom/Teams; the client captures system audio, including other applications. Screen pixels are not saved. Click **Stop recording** to flush the final chunks and finalize after all transcript segments reach the hub.

The release script creates an ad hoc signed `.app`; distribution signing and notarization are not configured. Launch the bundled app for permission testing instead of the bare Swift executable. macOS permission changes may require restarting the app.

Local data lives under `~/Library/Application Support/MeetingBox/Meetings/<meeting-id>/`. **Show local files** opens the exact folder. Recording/transcription continues through a hub outage. On reconnect, missing segments replay. If transcription fails, fix the local tool configuration and choose **Retry transcription**. If the app crashes before audio ingestion finishes, it refuses to claim a complete transcript; reimport the original file or each saved CAF track into a new meeting. Automatic recovery/merging of interrupted tracks is not implemented.

## Verify

```sh
.venv/bin/python -m pytest backend/tests -q
sh scripts/check-mac-core.sh
sh scripts/build-mac.sh
```

The standalone Swift checks work with Command Line Tools. With full Xcode selected, also run `swift test --package-path macos --scratch-path build/swift --disable-sandbox` for the Swift Testing/XCTest suites. The development Mac lacks those test frameworks, so that suite could not run here.

Automated tests exercise persistence, duplicate/conflicting delivery, missing sequences, queue restart, model failures, authorization, local endpoint restrictions, report evidence, audio conversion, and timestamps. Model transport in tests is simulated; passing tests does not establish Qwen quality or Whisper accuracy.

For a hardware demo, use a two-minute file containing a changed owner/deadline. Verify the final report preserves the correction and cites its transcript segment. Then repeat with the hub offline during recording, restore the connection, and confirm identical transcript counts and finalization. If the hub database is rebuilt, use **Sync all meetings** to restore previously completed history. Finally run 1, 3, and 5 simultaneous clients while measuring transcription backlog, report latency, RAM, temperature, and dropped audio. See [architecture and benchmark guidance](MEETINGBOX_ARCHITECTURE.md).

## Scope and tools

The current upload mode transcribes files on the Mac. **Standalone board audio upload/transcription, semantic RAG, speaker diarization, automatic meeting detection, encrypted storage, and a browser dashboard are not implemented.** The hub has transcript keyword search; long reports use progressive chronological reconciliation and fail visibly if their accumulated state exceeds the configured model context.

XcodeBuildMCP, Context7, and Sosumi were not callable in the build session. [Tooling setup](tooling.md) contains verified configuration examples; they have not been installed or connected automatically. Playwright is reserved for a future browser dashboard. Development documentation lookups use the internet; the provisioned product runtime uses the Mac and your local hub.

The hub board is confirmed as Radxa Cubie A7A. It uses an Allwinner A733 with two Cortex-A76 and six Cortex-A55 CPU cores; Radxa lists multiple RAM configurations. The current station has 6 GB RAM and Debian 11. NPU compatibility and concurrent performance for this optional native-client hub remain unverified. CPU inference is the baseline for this implementation. [Official board specifications](https://docs.radxa.com/en/cubie/a7a).
