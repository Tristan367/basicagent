"""Resolving a provider's API key.

Two provider modules each carried a byte-identical copy of this,
including a cache dict of their own. Two caches for one concept meant clearing
one did not clear the other, and a fix applied to one never reached the other.

The lookup is synchronous because `api_key()` is, while the rest of the
database layer is async. It is cached after the first hit so the blocking read
happens once per key per process, and `prime()` lets startup fill the cache
from the async connection so in practice it never happens at all.
"""

import os
import sqlite3

# settings_key -> key. Only successful lookups are stored: caching a failure
# meant a locked or briefly-unreadable database left the provider reporting
# "no API key configured" for the rest of the process, with a key plainly
# saved in the UI and nothing logged anywhere to say why.
_cache: dict[str, str] = {}


def prime(settings: dict[str, str]) -> None:
    """Seed the cache from settings already loaded over the async connection."""
    for key, value in settings.items():
        if value and value.strip():
            _cache[key] = value.strip()


def invalidate(settings_key: str = "") -> None:
    if settings_key:
        _cache.pop(settings_key, None)
    else:
        _cache.clear()


def resolve(env_key: str, settings_key: str) -> str:
    """The key for a provider: environment first, then the settings table.

    The environment wins so a key can be forced without touching the database.
    """
    if env_key:
        from_env = os.getenv(env_key, "").strip()
        if from_env:
            return from_env
    if not settings_key:
        return ""
    if settings_key in _cache:
        return _cache[settings_key]
    value = _read_setting(settings_key)
    if value:
        _cache[settings_key] = value
    return value


def _read_setting(settings_key: str) -> str:
    from agent_server.config import DB_PATH

    if not DB_PATH.exists():
        return ""
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5)
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (settings_key,)
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        # Not cached, so the next call retries rather than being stuck on "".
        return ""
    return (row[0] if row and row[0] else "").strip()
