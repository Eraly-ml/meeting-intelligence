# Deployment runbook

## 1. MacBook Air M5

Install Homebrew, Python 3.11+, ffmpeg and Ollama. Then:

```bash
cd mac-worker
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[mlx,gigaam,test]'
cp .env.example .env
ollama pull qwen3.5:4b-q4_K_M
meeting-worker
```

Before setting `MI_HF_OFFLINE=true`, prefetch the selected ASR checkpoints once. For the first integration test use `MI_HF_OFFLINE=false`; after the weights are cached, switch it back to `true` and disconnect Internet to prove locality.

For Radxa access set `MI_BIND_HOST=0.0.0.0`, a random `MI_API_TOKEN`, and `MI_ALLOWED_ORIGINS=http://radxa.local:8080`. Do this only on a trusted private network.

## 2. Radxa Cubie A7A

Install a 64-bit Debian/Ubuntu image, Docker Engine and Compose. Store Docker data on NVMe/SSD rather than microSD.

```bash
cd deploy/radxa
docker compose up -d --build
```

Open `http://radxa.local:8080/meeting-intelligence`, enter `http://macbook.local:8765` and the worker token, then test the connection.

## 3. Acceptance test

1. Submit one short prepared transcript; verify decisions and evidence quotes.
2. Submit 5–10 minutes each of Kazakh, Russian, English, and KK/RU code-switching.
3. Compare Shyngys and GigaAM on the same KK/RU clips; keep the lower normalized WER/CER profile.
4. Check an explicit decision, a rejected proposal, a changed deadline, an unnamed assignee, and a missing deadline.
5. Open JSON, CSV and PDF; verify Cyrillic/Kazakh glyphs and CSV columns.
6. Restart the Mac worker mid-job; verify the job becomes retryable.
7. Disconnect WAN and repeat a full run.

## Practical memory profiles

- 16 GB unified memory: start with Qwen 2B or 4B Q4, one job at a time; close other heavy apps.
- 24 GB or more: Qwen 4B Q4 is the normal target with ASR models loaded sequentially.
- Radxa: 8 GB is workable for UI/database; 16 GB is preferable. It is not the primary LLM/ASR compute node.

Exact throughput must be measured on the actual Mac and meeting audio; the code deliberately serializes heavy jobs to avoid memory pressure on a fanless Air.
