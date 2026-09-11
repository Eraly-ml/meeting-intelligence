#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
hub_env="${MEETINGBOX_ENV:-backend/.env}"
if [ ! -f "$hub_env" ]; then
    printf 'Create backend/.env from backend/.env.example and set MEETINGBOX_TOKEN first.\n' >&2
    exit 1
fi
umask 077
exec .venv/bin/python -m uvicorn meetingbox.main:create_app --factory --env-file "$hub_env" --host "${MEETINGBOX_BIND:-127.0.0.1}" --port "${MEETINGBOX_PORT:-8000}" --workers 1
