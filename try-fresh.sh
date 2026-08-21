#!/usr/bin/env bash
# Run the app exactly as somebody who has just installed it would meet it:
# a brand new database, no key, no projects, the welcome screen on first load.
#
# For handing to someone else to try, and for checking that first run still
# works -- which is the one path a developer never sees again after day one,
# and the only one every single user goes through.
#
#   ./try-fresh.sh              a new empty run, thrown away when you are done
#   ./try-fresh.sh --keep       keep it, so you can come back to the same state
#   ./try-fresh.sh --with-key   copy your own API keys in, to skip the setup step
#
# Nothing here touches your real data. The app's own database lives in
# ~/.local/share/basicagent and is never opened.
set -euo pipefail
cd "$(dirname "$0")"

KEEP=0
WITH_KEY=0
for arg in "$@"; do
  case "$arg" in
    --keep) KEEP=1 ;;
    --with-key) WITH_KEY=1 ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

[ -d .venv ] || { echo "Not installed yet. Run: python3 install.py"; exit 1; }

if [ "$KEEP" = 1 ]; then
  DATA_DIR="${PWD}/.fresh-run"
  mkdir -p "$DATA_DIR"
  echo "Keeping this run in $DATA_DIR"
else
  DATA_DIR="$(mktemp -d -t assistant-fresh-XXXXXX)"
  # Only ever a directory this script made itself, and only on a clean exit or
  # Ctrl-C -- never on the --keep path.
  trap 'rm -rf "$DATA_DIR"' EXIT INT TERM
  echo "A throwaway run in $DATA_DIR (deleted when you close this)"
fi

if [ "$WITH_KEY" = 1 ]; then
  REAL="${XDG_DATA_HOME:-$HOME/.local/share}/basicagent/agent.db"
  if [ -f "$REAL" ]; then
    # Read-only against the real database, and only the key rows. Everything
    # else about the fresh run stays fresh -- no projects, no conversations, no
    # settings, and the welcome screen still appears.
    BASICAGENT_DATA_DIR="$DATA_DIR" .venv/bin/python - "$REAL" <<'PY'
import asyncio, sqlite3, sys
from agent_server import database as db

async def go(real):
    await db.init_db()
    src = sqlite3.connect(f"file:{real}?mode=ro", uri=True)
    rows = src.execute(
        "SELECT key, value FROM settings WHERE key LIKE '%api_key%' AND value != ''"
    ).fetchall()
    src.close()
    for key, value in rows:
        await db.set_setting(key, value)
    await db.close()
    print(f"Copied {len(rows)} API key(s) across. Nothing else came with them.")

asyncio.run(go(sys.argv[1]))
PY
  else
    echo "No existing database to copy a key from; you will be asked to set one up."
  fi
fi

PORT="${PORT:-8230}"
echo
echo "Starting a fresh Assistant on http://127.0.0.1:$PORT"
echo "Press Ctrl-C to stop."
echo
BASICAGENT_DATA_DIR="$DATA_DIR" exec .venv/bin/python -m uvicorn agent_server.main:app \
  --host 127.0.0.1 --port "$PORT"
