# Hardware validation — 2026-09-11

Tested on a Radxa Cubie A7A with 6 GB RAM and Debian 11, connected over the
private LAN to an Apple M5 Mac with 16 GB unified memory.

## Checks completed

- 17 Mac worker tests, including the station-to-worker multipart contract,
  authentication, durable jobs, bounded uploads, citations and Unicode exports.
- 53 Python station/native-foundation checks, including disconnected-worker
  recovery and cancellation/retry races.
- Go API/config/server tests, including the station-mode guard that disables
  legacy inference and external-provider routes.
- Frontend TypeScript production build with `VITE_MEETING_STATION=true`, and
  ESLint on changed frontend files.
- Native macOS 15 prototype build, ad-hoc signature check and standalone
  repository/network/audio checks. Full Xcode is not installed on this Mac.

The isolated Playwright browser created the station account, paired its separate
station token, uploaded a synthetic WAV, opened the transcript, loaded the full
archived audio and downloaded a valid PDF. Authenticated audio range requests
returned HTTP 206. JSON, CSV and PDF were cached on the board. The browser's
resource entries contained no external application assets.

With the Mac worker stopped, the board accepted a new transcript and displayed
it as queued. The existing PDF remained downloadable, byte-identical to the
online-worker download. After restarting the worker, the queued transcript
completed automatically. Browser reload and a subsequent board-service restart
retained the archive.

## Real model smoke test

A locally synthesized two-voice recording lasted **16.636 seconds**. With
multilingual Whisper base, Sherpa ONNX diarization and local Qwen3.5 4B, it
produced five timestamped segments, two anonymous speaker groups and a PDF.
The direct worker run took about **34 seconds**; the browser-to-board run took
about **29 seconds** under different warm-cache conditions. These are smoke
measurements, not throughput benchmarks.

The final task reflected the later Monday deadline and changed owner. Whisper
base misheard the spoken name “Timur” as “Timma”; the report preserved that ASR
text. Name accuracy, mixed-language accuracy and diarization quality need
representative meeting audio. Source checks mark unsupported items for review;
they do not establish factual correctness of every generated sentence.

The MP3 and M4A encodings of the same fixture also decoded and produced five
timestamped Whisper segments. No real microphone or system-meeting audio was
captured for these tests.

Actual Metal inference also completed under `deploy/mac/inference-local.sb`.
Separate connection probes allowed this Mac's loopback/interface addresses,
blocked direct connections to the Radxa and a public IP, and allowed a Radxa
request into a temporary Mac server with a successful response. This constrains
direct outbound sockets, not delegated DNS/IPC. A physical WAN-disconnected test
and long/concurrent meetings remain unverified.

The isolated browser accepted the existing Caddy test certificate. Its service
worker registration reported a certificate-trust error; ordinary application
flows worked, but PWA installation/offline caching was not validated. The system
trust store was not changed.

## Carelink preservation

Both private archives passed full gzip CRC and saved SHA-256 verification.
Each contains 14,036 regular files, with per-file SHA-256 manifests. The
consistent archive was taken while the Carelink gateway was stopped and the
gateway was restarted afterward. These are application/configuration backups,
not disk images.

The rollback exercise started Carelink successfully, returned HTTP 200 from its
original health endpoint and restored a byte-identical original Caddyfile.
Meeting Station was then reactivated. Carelink code/data, firewall, SSH and OS
remain in place. The original configuration snapshot is never overwritten by
subsequent station activations.

Generated recordings, reports, browser logs, model hashes, credentials and
Carelink backups remain in ignored private workspace directories.
