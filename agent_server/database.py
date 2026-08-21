"""SQLite persistence layer.

One long-lived aiosqlite connection guarded by a write lock. Rows are ordered by
the autoincrement `id`, never by `created_at`, because the wire format requires
tool results to directly follow the assistant message that requested them.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime

import aiosqlite

from agent_server.config import DB_PATH, HOME_SESSION_ID

_conn: aiosqlite.Connection | None = None
_write_lock = asyncio.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    project_dir TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'deepseek',
    model TEXT NOT NULL DEFAULT 'deepseek-v4-pro',
    thinking_effort TEXT,
    kind TEXT NOT NULL DEFAULT 'project',
    system_prompt TEXT,
    compact_threshold INTEGER,
    profile TEXT NOT NULL DEFAULT 'parent',
    created_at TEXT NOT NULL,
    last_active_at TEXT NOT NULL,
    is_archived INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    reasoning_content TEXT,
    tool_calls TEXT,
    tool_call_id TEXT,
    tool_name TEXT,
    is_error INTEGER DEFAULT 0,
    token_count INTEGER,
    usage TEXT,
    diff TEXT,
    tool_title TEXT,
    duration_ms INTEGER,
    file_path TEXT,
    lang TEXT,
    code TEXT,
    code_start INTEGER DEFAULT 1,
    send_reasoning INTEGER DEFAULT 1,
    open_session TEXT,
    created_at TEXT NOT NULL,
    is_compacted INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS compactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    summary_text TEXT NOT NULL,
    message_range_start INTEGER,
    message_range_end INTEGER,
    original_token_count INTEGER,
    compressed_token_count INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS custom_endpoints (
    name TEXT PRIMARY KEY,
    base_url TEXT NOT NULL,
    api_key TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
CREATE INDEX IF NOT EXISTS idx_compactions_session ON compactions(session_id, id);
"""

# Columns added after the original schema shipped, applied idempotently.
MIGRATIONS: list[tuple[str, str, str]] = [
    ("sessions", "system_prompt", "TEXT"),
    ("sessions", "compact_threshold", "INTEGER"),
    ("sessions", "kind", "TEXT NOT NULL DEFAULT 'project'"),
    ("sessions", "description", "TEXT"),
    ("sessions", "profile", "TEXT NOT NULL DEFAULT 'parent'"),
    # How this project runs, so the Play button still works tomorrow -- and
    # after the app has been restarted, when nothing is in memory any more.
    ("sessions", "preview_command", "TEXT"),
    ("sessions", "preview_url", "TEXT"),
    ("messages", "reasoning_content", "TEXT"),
    ("messages", "tool_name", "TEXT"),
    ("messages", "is_error", "INTEGER DEFAULT 0"),
    ("messages", "usage", "TEXT"),
    ("messages", "diff", "TEXT"),
    ("messages", "tool_title", "TEXT"),
    ("messages", "duration_ms", "INTEGER"),
    # Set on the last thing said before the user pressed Stop, so the break
    # survives a reload. Without it, a stopped turn looked exactly like a
    # finished one and the next thing anybody did was wait for a reply that
    # was never coming.
    ("messages", "broke_off", "INTEGER NOT NULL DEFAULT 0"),
    ("messages", "file_path", "TEXT"),
    ("messages", "send_reasoning", "INTEGER DEFAULT 1"),
    ("messages", "lang", "TEXT"),
    ("messages", "code", "TEXT"),
    ("messages", "code_start", "INTEGER DEFAULT 1"),
    ("messages", "open_session", "TEXT"),
    # JSON list of paths to pictures that belong to this message: what the
    # user attached, or what a tool captured. Sent as pictures, not as text.
    ("messages", "images", "TEXT"),
    # Discovered by asking the endpoint, never typed. See save_custom_endpoint.
    ("custom_endpoints", "model_id", "TEXT NOT NULL DEFAULT ''"),
    ("custom_endpoints", "models", "TEXT NOT NULL DEFAULT ''"),
]


def _now() -> str:
    return datetime.now(UTC).isoformat()


_connect_lock = asyncio.Lock()


async def connect() -> aiosqlite.Connection:
    global _conn
    if _conn is not None:
        return _conn
    async with _connect_lock:
        if _conn is None:
            _conn = await aiosqlite.connect(str(DB_PATH))
            _conn.row_factory = aiosqlite.Row
            await _conn.execute("PRAGMA journal_mode=WAL")
            await _conn.execute("PRAGMA foreign_keys=ON")
            await _conn.execute("PRAGMA busy_timeout=5000")
            await _conn.execute("PRAGMA synchronous=NORMAL")
    return _conn


async def close():
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None


async def init_db():
    db = await connect()
    await db.executescript(SCHEMA)
    for table, column, decl in MIGRATIONS:
        cur = await db.execute(f"PRAGMA table_info({table})")
        existing = {r[1] for r in await cur.fetchall()}
        if column not in existing:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    await db.commit()


async def _fetchone(sql: str, params: tuple = ()) -> dict | None:
    db = await connect()
    cur = await db.execute(sql, params)
    row = await cur.fetchone()
    return dict(row) if row else None


async def _fetchall(sql: str, params: tuple = ()) -> list[dict]:
    db = await connect()
    cur = await db.execute(sql, params)
    return [dict(r) for r in await cur.fetchall()]


async def _execute(sql: str, params: tuple = ()) -> int:
    db = await connect()
    async with _write_lock:
        cur = await db.execute(sql, params)
        await db.commit()
        return cur.lastrowid or 0


# ── Sessions ────────────────────────────────────────────────────────────────

SESSION_FIELDS = {
    "name", "description", "project_dir", "provider", "model", "thinking_effort",
    "kind", "system_prompt", "compact_threshold", "is_archived",
    "preview_command", "preview_url",
}
# `profile` is deliberately NOT in that set, and a test defends the omission.
# `update_session` is reachable from a PATCH body, so anything listed there can
# be written over HTTP -- and `profile` is what keeps a child's projects apart
# from everyone else's. Changing it goes through `set_session_profile` below,
# which no route calls.


async def create_session(
    name: str,
    project_dir: str,
    provider: str = "deepseek",
    model: str = "deepseek-v4-pro",
    thinking_effort: str | None = None,
    kind: str = "project",
    description: str = "",
    profile: str = "parent",
    session_id: str | None = None,
) -> dict:
    sid = session_id or (HOME_SESSION_ID if kind == "manager" else uuid.uuid4().hex[:8])
    now = _now()
    await _execute(
        "INSERT INTO sessions (id, name, description, project_dir, provider, model,"
        " thinking_effort, kind, profile, created_at, last_active_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (sid, name, description or None, project_dir, provider, model,
         thinking_effort, kind, profile, now, now),
    )
    session = await get_session(sid)
    assert session is not None
    return session


async def get_session(session_id: str) -> dict | None:
    return await _fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))


async def get_session_by_name(name: str, profile: str | None = None) -> dict | None:
    if profile is None:
        return await _fetchone(
            "SELECT * FROM sessions WHERE name = ? AND is_archived = 0"
            " ORDER BY last_active_at DESC LIMIT 1",
            (name,),
        )
    return await _fetchone(
        "SELECT * FROM sessions WHERE name = ? AND is_archived = 0 AND profile = ?"
        " ORDER BY last_active_at DESC LIMIT 1",
        (name, profile),
    )


async def list_sessions(profile: str | None = None, archived: bool = False) -> list[dict]:
    if profile is None:
        return await _fetchall(
            "SELECT * FROM sessions WHERE is_archived = ? AND kind != 'manager'"
            " ORDER BY last_active_at DESC",
            (1 if archived else 0,),
        )
    return await _fetchall(
        "SELECT * FROM sessions WHERE is_archived = ? AND kind != 'manager' AND profile = ?"
        " ORDER BY last_active_at DESC",
        (1 if archived else 0, profile),
    )


async def update_session(session_id: str, **kwargs) -> dict | None:
    updates = {k: v for k, v in kwargs.items() if k in SESSION_FIELDS}
    if not updates:
        return await get_session(session_id)
    clause = ", ".join(f"{k} = ?" for k in updates)
    await _execute(
        f"UPDATE sessions SET {clause} WHERE id = ?",
        (*updates.values(), session_id),
    )
    return await get_session(session_id)


async def set_session_profile(session_id: str, profile: str) -> dict | None:
    """Move a project between the child's list and the ordinary one.

    Separate from `update_session` on purpose. That one filters against
    `SESSION_FIELDS` because its arguments can come from a request body; this
    one is called only by the manager's `assign_project` tool, and putting
    `profile` in the generic set would have made child mode one PATCH away
    from being escaped.

    The frozen system prompt goes with it. A session's prompt is rendered once
    and stored, so a project the parent had already talked in kept the adult
    prompt after being handed over -- the child-safety block is chosen at
    freezing time, and nothing re-chose it. Clearing the column here means the
    next turn rebuilds the prompt for whoever now owns the project, and the
    frozen text can never disagree with the profile beside it. It costs one
    cache miss on a project that changes hands, which is rare and worth it.
    """
    if profile not in ("parent", "child"):
        raise ValueError(f"unknown profile {profile!r}")
    await _execute(
        "UPDATE sessions SET profile = ?, system_prompt = NULL WHERE id = ?",
        (profile, session_id),
    )
    return await get_session(session_id)


async def touch_session(session_id: str):
    await _execute("UPDATE sessions SET last_active_at = ? WHERE id = ?", (_now(), session_id))


async def delete_session(session_id: str):
    db = await connect()
    async with _write_lock:
        for table in ("messages", "compactions"):
            await db.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))
        await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        await db.commit()


# ── Custom endpoints ────────────────────────────────────────────────────────


async def list_custom_endpoints() -> list[dict]:
    return await _fetchall("SELECT * FROM custom_endpoints ORDER BY name")


async def get_custom_endpoint(name: str) -> dict | None:
    return await _fetchone("SELECT * FROM custom_endpoints WHERE name = ?", (name,))


async def save_custom_endpoint(name: str, base_url: str, api_key: str = "",
                               model_id: str = "", models: list[str] | None = None):
    """Save an endpoint.

    Nobody types a model name. The endpoint is asked at save time, and asked
    again on first use if it was not running then. `model_id` is what to send
    when nothing more specific is chosen; `models` is everything it listed, so
    a box running several can offer them all in the picker.
    """
    await _execute(
        "INSERT INTO custom_endpoints"
        " (name, base_url, api_key, model_id, models, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?)"
        " ON CONFLICT(name) DO UPDATE SET base_url = excluded.base_url,"
        " api_key = excluded.api_key, model_id = excluded.model_id,"
        " models = excluded.models, updated_at = excluded.updated_at",
        (name, base_url, api_key, model_id, json.dumps(models or []), _now(), _now()),
    )


async def set_custom_endpoint_models(name: str, models: list[str]):
    """Record what an endpoint served, discovered when it first answered."""
    await _execute(
        "UPDATE custom_endpoints SET model_id = ?, models = ? WHERE name = ?",
        (models[0] if models else "", json.dumps(models), name),
    )


def endpoint_models(row: dict) -> list[str]:
    """The model ids an endpoint reported, or [] if it has not been asked."""
    try:
        return [str(m) for m in json.loads(row.get("models") or "[]")]
    except (TypeError, ValueError):
        return []


async def delete_custom_endpoint(name: str):
    await _execute("DELETE FROM custom_endpoints WHERE name = ?", (name,))


# ── Messages ────────────────────────────────────────────────────────────────

async def add_message(
    session_id: str,
    role: str,
    content: str = "",
    *,
    reasoning_content: str | None = None,
    tool_calls: list[dict] | None = None,
    tool_call_id: str | None = None,
    tool_name: str | None = None,
    is_error: bool = False,
    token_count: int | None = None,
    usage: dict | None = None,
    diff: str = "",
    tool_title: str = "",
    duration_ms: int = 0,
    file_path: str = "",
    lang: str = "",
    code: str = "",
    code_start: int = 1,
    open_session: str = "",
    images: list[str] | None = None,
) -> dict:
    msg_id = await _execute(
        "INSERT INTO messages (session_id, role, content, reasoning_content, tool_calls,"
        " tool_call_id, tool_name, is_error, token_count, usage, diff, tool_title,"
        " duration_ms, file_path, lang, code, code_start, open_session, images, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            session_id,
            role,
            content or "",
            reasoning_content,
            json.dumps(tool_calls) if tool_calls else None,
            tool_call_id,
            tool_name,
            1 if is_error else 0,
            token_count,
            json.dumps(usage) if usage else None,
            diff or None,
            tool_title or None,
            duration_ms or None,
            file_path or None,
            lang or None,
            code or None,
            code_start if code_start else 1,
            open_session or None,
            json.dumps(images) if images else None,
            _now(),
        ),
    )
    await touch_session(session_id)
    row = await _fetchone("SELECT * FROM messages WHERE id = ?", (msg_id,))
    assert row is not None
    return row


async def get_messages(session_id: str) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM messages WHERE session_id = ? AND is_compacted = 0 ORDER BY id ASC",
        (session_id,),
    )


async def get_session_history(session_id: str) -> list[dict]:
    """Messages and compaction summaries interleaved for display.

    Compacted messages are collapsed into a visible summary note, so the user
    can still see what was discussed earlier even though the model only keeps
    the summary plus the recent tail.
    """
    messages = await _fetchall(
        "SELECT * FROM messages WHERE session_id = ? AND is_compacted = 0 ORDER BY id ASC",
        (session_id,),
    )
    compactions = await _fetchall(
        "SELECT * FROM compactions WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
    )
    items: list[dict] = [{"kind": "message", "pos": m["id"], **m} for m in messages]
    for c in compactions:
        pos = c.get("message_range_start") or c.get("id") or 0
        items.append({"kind": "summary", "pos": pos, **c})
    items.sort(key=lambda x: (x["pos"], 0 if x["kind"] == "summary" else 1))
    return items


async def last_messages() -> dict[str, dict]:
    """The newest message for every session, keyed by session id.

    Used to flag which sessions have a reply the user hasn't seen yet.
    """
    rows = await _fetchall(
        "SELECT m.session_id AS session_id, m.id AS id, m.role AS role,"
        " m.created_at AS created_at"
        " FROM messages m"
        " JOIN (SELECT session_id, MAX(id) AS mid FROM messages GROUP BY session_id) x"
        "   ON m.id = x.mid"
    )
    return {r["session_id"]: r for r in rows}


async def revert_last_user_message(session_id: str) -> bool:
    """Remove the newest message when it is a user message nobody answered.

    Called when a turn fails before the assistant produced anything, so a
    message the AI never actually received doesn't linger in the history as if
    it had gone through. Safe to call at any time: it does nothing once the
    assistant has replied.
    """
    rows = await _fetchall(
        "SELECT id, role FROM messages WHERE session_id = ? AND is_compacted = 0"
        " ORDER BY id DESC LIMIT 1",
        (session_id,),
    )
    if not rows or rows[0]["role"] != "user":
        return False
    db = await connect()
    async with _write_lock:
        await db.execute("DELETE FROM messages WHERE id = ?", (rows[0]["id"],))
        await db.commit()
    return True


MESSAGE_FIELDS = {"content", "send_reasoning"}


async def update_message(message_id: int, **fields):
    fields = {k: v for k, v in fields.items() if k in MESSAGE_FIELDS}
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    await _execute(
        f"UPDATE messages SET {sets} WHERE id = ?", (*fields.values(), message_id)
    )


async def get_turn_changes(session_id: str) -> dict:
    """Aggregate file changes made since the last user message, for the summary
    shown when a turn finishes."""
    rows = await _fetchall(
        "SELECT id FROM messages WHERE session_id = ? AND role = 'user' ORDER BY id DESC LIMIT 1",
        (session_id,),
    )
    since = rows[0]["id"] if rows else 0
    edits = await _fetchall(
        "SELECT file_path, diff, tool_name FROM messages"
        " WHERE session_id = ? AND id > ? AND diff IS NOT NULL AND file_path IS NOT NULL"
        " ORDER BY id ASC",
        (session_id, since),
    )
    by_file: dict[str, dict] = {}
    for row in edits:
        entry = by_file.setdefault(
            row["file_path"], {"path": row["file_path"], "added": 0, "removed": 0}
        )
        diff = row["diff"] or ""
        entry["added"] += sum(
            1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")
        )
        entry["removed"] += sum(
            1 for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---")
        )
    files = list(by_file.values())
    return {
        "files": files,
        "added": sum(f["added"] for f in files),
        "removed": sum(f["removed"] for f in files),
    }


async def mark_messages_compacted(session_id: str, message_ids: list[int]):
    if not message_ids:
        return
    placeholders = ",".join("?" * len(message_ids))
    await _execute(
        f"UPDATE messages SET is_compacted = 1 WHERE session_id = ? AND id IN ({placeholders})",
        (session_id, *message_ids),
    )


CACHE_WRITE_MULTIPLIER = 1.25


def _price(usage_json: str, pricing: dict) -> tuple[dict, float]:
    try:
        u = json.loads(usage_json)
    except (json.JSONDecodeError, TypeError):
        return {}, 0.0
    if not pricing.get("priced"):
        return u, 0.0
    cached = u.get("cached_tokens", 0) or 0
    written = u.get("cache_write_tokens", 0) or 0
    prompt = u.get("prompt_tokens", 0) or 0
    completion = u.get("completion_tokens", 0) or 0
    reasoning = u.get("reasoning_tokens", 0) or 0
    if reasoning and reasoning <= completion:
        reasoning = 0
    uncached = max(prompt - cached - written, 0)
    cost = (
        cached * pricing["price_in_hit"]
        + written * pricing["price_in_miss"] * CACHE_WRITE_MULTIPLIER
        + uncached * pricing["price_in_miss"]
        + (completion + reasoning) * pricing["price_out"]
    ) / 1_000_000
    return u, cost


async def get_session_usage(session_id: str) -> dict:
    """Token totals, spend, and live context size for one session."""
    from agent_server.config import model_info

    session = await get_session(session_id)
    pricing = model_info((session or {}).get("model", ""))
    rows = await _fetchall(
        "SELECT usage FROM messages WHERE session_id = ? AND usage IS NOT NULL",
        (session_id,),
    )

    totals = {"input": 0, "cached": 0, "output": 0, "reasoning": 0, "cost": 0.0, "requests": 0}
    for row in rows:
        u, cost = _price(row["usage"], pricing)
        if not u:
            continue
        totals["requests"] += 1
        totals["input"] += u.get("prompt_tokens", 0) or 0
        totals["cached"] += u.get("cached_tokens", 0) or 0
        totals["output"] += u.get("completion_tokens", 0) or 0
        totals["reasoning"] += u.get("reasoning_tokens", 0) or 0
        totals["cost"] += cost

    last = await _fetchone(
        "SELECT usage FROM messages WHERE session_id = ? AND usage IS NOT NULL"
        " AND role = 'assistant' AND is_compacted = 0 ORDER BY id DESC LIMIT 1",
        (session_id,),
    )
    stale = False
    if last:
        newest_compaction = await _fetchone(
            "SELECT created_at FROM compactions WHERE session_id = ?"
            " ORDER BY id DESC LIMIT 1",
            (session_id,),
        )
        last_row = await _fetchone(
            "SELECT created_at FROM messages WHERE session_id = ? AND usage IS NOT NULL"
            " AND role = 'assistant' AND is_compacted = 0 ORDER BY id DESC LIMIT 1",
            (session_id,),
        )
        if newest_compaction and last_row:
            stale = newest_compaction["created_at"] > last_row["created_at"]

    context = 0
    if last and not stale:
        u, _ = _price(last["usage"], {})
        context = u.get("prompt_tokens", 0) or 0
    if not context:
        row = await _fetchone(
            "SELECT COALESCE(SUM(token_count), 0) AS total FROM messages"
            " WHERE session_id = ? AND is_compacted = 0",
            (session_id,),
        )
        summaries = await _fetchone(
            "SELECT COALESCE(SUM(compressed_token_count), 0) AS total FROM compactions"
            " WHERE session_id = ?",
            (session_id,),
        )
        context = (row or {}).get("total", 0) + (summaries or {}).get("total", 0)

    totals["context"] = context
    from agent_server.config import compact_threshold_for

    totals["threshold"] = (
        (session or {}).get("compact_threshold")
        or compact_threshold_for((session or {}).get("model", ""))
    )
    totals["max_context"] = pricing["context"]
    totals["priced"] = pricing["priced"]
    totals["percent"] = round(100 * context / totals["threshold"], 1) if totals["threshold"] else 0
    return totals


# ── Compactions ─────────────────────────────────────────────────────────────

async def add_compaction(
    session_id: str,
    summary_text: str,
    range_start: int,
    range_end: int,
    original_tokens: int,
    compressed_tokens: int,
) -> int:
    return await _execute(
        "INSERT INTO compactions (session_id, summary_text, message_range_start,"
        " message_range_end, original_token_count, compressed_token_count, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (session_id, summary_text, range_start, range_end, original_tokens,
         compressed_tokens, _now()),
    )


async def get_compactions(session_id: str) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM compactions WHERE session_id = ? ORDER BY id ASC", (session_id,)
    )


# ── Settings ────────────────────────────────────────────────────────────────

async def get_setting(key: str, default: str = "") -> str:
    row = await _fetchone("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else default


async def set_setting(key: str, value: str):
    await _execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?,?,?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, _now()),
    )


async def get_all_settings() -> dict[str, str]:
    rows = await _fetchall("SELECT key, value FROM settings")
    return {r["key"]: r["value"] for r in rows}


async def delete_setting(key: str):
    await _execute("DELETE FROM settings WHERE key = ?", (key,))


async def mark_interrupted(session_id: str) -> int:
    """Remember that the user stopped the assistant after the latest message.

    On the row rather than as a row of its own: a message of its own would be
    another thing the model has to be told to ignore, and this is not something
    anybody said. It is a mark on where the conversation was when it was cut.
    """
    row = await _fetchone(
        "SELECT id FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT 1",
        (session_id,),
    )
    if row is None:
        return 0
    await _execute("UPDATE messages SET broke_off = 1 WHERE id = ?", (row["id"],))
    return row["id"]
