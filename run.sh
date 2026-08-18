#!/usr/bin/env bash
# Start the Assistant server.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8220}"
[ -d .venv ] || { echo "No .venv found. Run: uv venv && uv pip install -r requirements.txt"; exit 1; }

if command -v lsof >/dev/null 2>&1 && lsof -ti:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is already in use (another app is running there)." >&2
  echo "Stop it first, or set PORT to something else." >&2
  exit 1
fi

exec .venv/bin/python -m uvicorn agent_server.main:app \
  --host "${HOST:-127.0.0.1}" --port "$PORT" "$@"
