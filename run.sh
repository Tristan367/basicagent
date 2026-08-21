#!/usr/bin/env bash
# Start the Assistant server.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8220}"
[ -d .venv ] || { echo "Not installed yet. Run: python3 install.py"; exit 1; }

case "${HOST:-127.0.0.1}" in
  127.0.0.1|localhost|::1) ;;
  *)
    echo "WARNING: listening on ${HOST}, not just this computer. There is no" >&2
    echo "password on this app and it can run any command your account can." >&2
    ;;
esac

if command -v lsof >/dev/null 2>&1 && lsof -ti:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is already in use (another app is running there)." >&2
  echo "Stop it first, or set PORT to something else." >&2
  exit 1
fi

exec .venv/bin/python -m uvicorn agent_server.main:app \
  --host "${HOST:-127.0.0.1}" --port "$PORT" "$@"
