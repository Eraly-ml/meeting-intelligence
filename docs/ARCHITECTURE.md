# Architecture

```text
┌──────────────────── Radxa Cubie A7A ────────────────────┐
│ Browser → Scriberr/Meeting Intelligence UI              │
│ Docker: Go API + React UI + SQLite + original recordings│
└───────────────────────┬─────────────────────────────────┘
                        │ private LAN, HTTP + bearer token
                        │ audio upload / status / results
┌───────────────────────▼ MacBook Air M5 ─────────────────┐
│ Native FastAPI worker (outside Docker)                   │
│ ffmpeg → language route → ASR → Qwen → evidence check    │
│             │                  │                         │
│  KK/RU: shyngys Whisper       Ollama Qwen3.5 4B Q4      │
│  EN: MLX Distil-Whisper                                  │
│  optional benchmark: GigaAM                              │
│ SQLite queue + JSON/CSV/PDF exports                      │
└─────────────────────────────────────────────────────────┘
```

## Trust boundary

Neither service needs Internet after images and model weights are prefetched. Bind the worker only to the trusted LAN, use a long random bearer token, allow only the Radxa UI origin, and block port 8765 at the router/WAN boundary. The Mac does not use swap as model storage: SSD can hold weights and macOS may page memory, but inference still requires sufficient unified RAM.

## Model routing

| Input | Default | Reason |
|---|---|---|
| Kazakh/Russian/mixed | `shyngys879/kazakh-whisper-large-v3-turbo` | User-validated for KK and RU |
| English | `mlx-community/distil-whisper-large-v3` | Native MLX path on Apple Silicon |
| Protocol generation | `qwen3.5:4b-q4_K_M` | Small multilingual structured-output model |
| ASR benchmark | `ai-sage/GigaAM-Multilingual@large_ctc` | Optional comparison, not automatic default |

The 2B Qwen profile is a low-memory fallback. GigaChat Audio 10B is intentionally excluded from the MVP because it duplicates ASR + LLM responsibilities and has substantially larger weights.

## Durable job lifecycle

`queued → preprocessing → transcribing → extracting → validating → exporting → completed`

Failures retain their source and manifest in SQLite and can be retried. A restart marks interrupted jobs failed instead of silently losing them. Cancellation is cooperative so model runtimes are not killed while Metal/MPS owns memory.
