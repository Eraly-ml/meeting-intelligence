# Mac inference worker

This is the production Mac service used by the Scriberr Meeting Intelligence UI and the Radxa station bridge. It retains the fork's `/v1/jobs` contract, durable jobs, rich protocol schema and JSON/CSV/PDF exports. The separate `engine/` service is an earlier compatibility implementation; do not run both services on port 8765.

Run on Python 3.11+ (provisioned development environment: `.local/mac-venv`, Python 3.12). From the repository root:

```sh
python3 -m venv .venv-worker
.venv-worker/bin/pip install -e 'mac-worker[local,test]'
cp mac-worker/.env.example .env
.venv-worker/bin/meeting-worker
```

Configure all paths in `.env`. Generate a long random `MI_API_TOKEN`; the service refuses an empty or short token. Bind `MI_BIND_HOST` to the Mac's LAN address for the Radxa. The station stores original recordings and proxies requests to this service; keep the Mac awake for processing. HTTP bearer authentication does not encrypt LAN traffic; use the station's configured HTTPS or private encrypted transport as needed.

The default ASR profile is **whisper-cpp** for all languages. `MI_WHISPER_BINARY` accepts an absolute local executable path, `MI_WHISPER_MODEL` a downloaded multilingual GGML model, and `MI_FFMPEG_BINARY` any compatible local ffmpeg executable (including imageio's executable). A multilingual `base` model is a small initial profile, not a quality guarantee for Kazakh, Russian, mixed speech, or overlapping speakers. The existing `shyngys`, `mlx-distil-whisper`, and `gigaam` profiles remain optional and need separately installed libraries and pre-provisioned model directories. Runtime model downloads are disabled.

Start separately installed Ollama with cloud features disabled before starting the worker:

```sh
OLLAMA_NO_CLOUD=1 OLLAMA_HOST=127.0.0.1:11434 OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=1 ollama serve
```

During provisioning only, pull `qwen3.5:4b`. Its Ollama Q4_K_M download is approximately 3.4 GB; working memory includes context, runtime, speech models and macOS. The Mac is an M5 with 16 GB RAM, so the worker serializes whole jobs. Ollama stays on loopback; each processing run checks local GGUF metadata and rejects cloud-backed models, redirects, and proxy environment variables. [Ollama model](https://ollama.com/library/qwen3.5:4b), [offline controls](https://docs.ollama.com/faq).

For actual speaker diarization, set `MI_ENABLE_DIARIZATION=true`, `MI_DIARIZATION_BACKEND=sherpa-onnx` and the two local ONNX paths. Install the `local` optional dependencies. Official Sherpa model downloads are available without a runtime account:

- [Segmentation archive](https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2), including model files and license.
- [TitaNet small embedding model](https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/nemo_en_titanet_small.onnx).
- [Official model documentation](https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/models.html).

Diarization clusters anonymous voices over the whole recording. It cannot identify names. Transcript spans that overlap multiple voices similarly remain unassigned; this is an uncertainty rule, not a calibrated confidence score. Accuracy and multilingual robustness need real meeting validation. Missing resources produce an explicit unavailable error when diarization is requested. The optional `pyannote` backend requires a pre-provisioned local pipeline directory. No cloud diarization fallback exists.

`GET /health` is public liveness only. Authenticated `GET /v1/capabilities` reports installed files/packages and configured profiles; Qwen readiness is checked at inference time. A model file being present does not establish its accuracy or runtime compatibility.

The authenticated job API is unchanged:

```text
POST /v1/jobs                       multipart manifest_json + exactly one audio or transcript
GET  /v1/jobs                       list jobs
GET  /v1/jobs/{uuid}                 status
POST /v1/jobs/{uuid}/retry           failed/cancelled jobs
POST /v1/jobs/{uuid}/cancel          cooperative cancellation
GET  /v1/jobs/{uuid}/result          rich JobResult
GET  /v1/jobs/{uuid}/export/{format} json, csv, pdf
```

`Idempotency-Key` must be a UUID. Replaying the same key, normalized manifest and source SHA256 returns the existing job; changing content returns 409. Accepted audio extensions: MP3, WAV, M4A, WebM, OGG, CAF, FLAC. Uploads default to 512 MiB and recordings to four hours. Temporary uploads are removed after rejection. Original sources remain until an explicit retention operation.

One process owns the SQLite directory and one consumer processes its durable queue. Restart preserves queued jobs and marks interrupted jobs retryable. API health and status continue responding during model work. Whisper, ffmpeg and Sherpa subprocesses have time limits; cancellation lets the active inference finish, and a retry cannot race it. Results are atomically written before completion is published.

Qwen progressively reconciles the transcript in chronological windows, carrying the previous protocol so later owners/deadlines can replace earlier ones. No transcript is silently truncated. Excessive accumulated report/evidence size fails visibly or flags individual evidence as unavailable. Final cited topics, decisions, questions, tasks and risks receive a second local semantic check against their excerpts; unsupported claims are marked `source_check=failed` and `review_status=needs_review`. Summary prose is not independently fact-verified. Generated human-confirmation flags are removed, and calendar dates absent as explicit ISO dates in evidence are cleared. Unsupported ISO dates in generated prose are replaced with a visible review marker; original transcript text is preserved. These checks reduce unsupported claims; they do not guarantee factual accuracy.

PDF rendering uses a local Unicode TTF via `MI_PDF_FONT`, or available Arial/DejaVu fallback. Text is escaped before formatting, and exports never fetch fonts or web assets. CSV formula-like values are escaped. Model licenses and any redistributed font licenses must travel with provisioned assets.

Run tests with:

```sh
.local/mac-venv/bin/python -m pytest mac-worker/tests -q
```

Automated validation uses local fake executable/model transports for auth, upload bounds, idempotency, persistence, recovery, nonblocking health, transcript timestamps, semantic review and Unicode exports. Actual model quality, full meeting capture and WAN-disconnected operation require hardware checks.

A hardware smoke test on the M5 processed 16.636 seconds of two-voice synthesized English audio into five timestamped segments, two anonymous speaker labels and a PDF in 34.1 seconds. Whisper base misheard the name “Timur” as “Timma”; the report retained that transcription error. A separate text test retained the corrected owner Timur and Monday deadline. These short fixtures establish the connected pipeline, not accuracy on real meetings, Kazakh quality, or hour-long throughput.
