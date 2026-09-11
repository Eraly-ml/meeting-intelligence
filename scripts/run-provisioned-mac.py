#!/usr/bin/env python3
"""Run one local service using workspace-provisioned binaries and private settings.

Usage: python3 scripts/run-provisioned-mac.py worker|ollama
See mac-worker/README.md for provisioning on another Mac.
"""
import json
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parent.parent
os.chdir(root)
os.umask(0o077)
if len(sys.argv) != 2 or sys.argv[1] not in ("worker", "ollama"):
    raise SystemExit("Usage: run-provisioned-mac.py worker|ollama")
if sys.argv[1] == "worker":
    config = json.loads((root / ".local/worker-env.json").read_text())
    if not all(key.startswith("MI_") and isinstance(value, str) for key, value in config.items()):
        raise SystemExit("Worker environment must contain string MI_ settings only")
    os.environ.update(config)
    binary = root / ".local/mac-venv/bin/meeting-worker"
    arguments = [str(binary)]
else:
    os.environ.update({"OLLAMA_HOST": "127.0.0.1:11434", "OLLAMA_NO_CLOUD": "1",
                       "OLLAMA_MODELS": str(root / "models/ollama"),
                       "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_MAX_LOADED_MODELS": "1"})
    binary = root / ".local/ollama/ollama"
    arguments = [str(binary), "serve"]
if not binary.is_file():
    raise SystemExit("Missing provisioned executable: " + str(binary))
sandbox = Path("/usr/bin/sandbox-exec")
if not sandbox.is_file():
    raise SystemExit("This provisioned launcher requires macOS sandbox-exec; review the local network policy before changing the launcher")
os.execv(str(sandbox), [str(sandbox), "-f", str(root / "deploy/mac/inference-local.sb"), *arguments])
