# Earlier inference-engine prototype

This directory is retained as an earlier compatibility implementation. It is not the Mac service used by the current Radxa/Scriberr deployment. Run [`mac-worker/`](../mac-worker/README.md) for ASR, diarization, Qwen, verification and exports, and [`station/`](../station/README.md) for the board archive and browser API.

Do not run this prototype alongside the canonical worker on port 8765. Current configuration, service paths and Carelink restoration instructions are in [the deployment runbook](../docs/RUNBOOK.md).
