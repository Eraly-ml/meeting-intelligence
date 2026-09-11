# Mac worker

Native macOS inference service for AI Meeting Intelligence. Run it outside Docker so MLX, Metal and PyTorch MPS remain available.

## Development setup

```bash
cd mac-worker
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
ollama pull qwen3.5:4b-q4_K_M
meeting-worker
```

The default bind address is loopback. Set a strong `MI_API_TOKEN`, configure a trusted local network, and then change `MI_BIND_HOST` before connecting Radxa.

Text input already exercises the protocol and export pipeline. Install one of the optional extras before audio use. Model files must be prefetched before setting `MI_HF_OFFLINE=true`.

```bash
pip install -e '.[mlx]'
```

Example text job:

```bash
curl -X POST http://127.0.0.1:8765/v1/jobs \
  -H 'Authorization: Bearer local-token' \
  -H 'Idempotency-Key: demo-001' \
  -F 'manifest_json={"meeting_id":"demo","title":"Demo","language_mode":"kk_ru"}' \
  -F 'transcript=Айбек подготовит отчёт к пятнице.'
```

Poll `/v1/jobs/demo-001`, then fetch `/v1/jobs/demo-001/result`.
