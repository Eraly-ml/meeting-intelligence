#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
repo_dir="$PWD"
go_binary="${GO_BINARY:-$repo_dir/.local/go/bin/go}"
if [ ! -x "$go_binary" ]; then go_binary=go; fi
(cd web/frontend && npm ci && VITE_MEETING_STATION=true npm run build)
mkdir -p internal/web/dist build
# Remove only generated embedded assets so old bundles cannot remain reachable.
python3 - <<'PY'
import shutil
from pathlib import Path
target = Path('internal/web/dist')
for entry in target.iterdir():
    if entry.is_dir(): shutil.rmtree(entry)
    else: entry.unlink()
shutil.copytree('web/frontend/dist', target, dirs_exist_ok=True)
PY
export GOCACHE="${GOCACHE:-$repo_dir/.local/go-cache}"
export GOMODCACHE="${GOMODCACHE:-$repo_dir/.local/go-mod}"
CGO_ENABLED=0 GOOS=linux GOARCH=arm64 "$go_binary" build -trimpath \
    -ldflags='-s -w' -o build/scriberr-linux-arm64 ./cmd/server
printf '%s\n' 'Built build/scriberr-linux-arm64 with the station browser interface.'
