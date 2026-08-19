"""Child mode / parental controls.

"Child mode" is two things:

1. A small, extra block of the system prompt that tells the model it is talking
   to a child and must keep everything safe, kind, and a learning experience.
2. A parent password that gates the settings a child shouldn't change (switching
   model, editing API keys) and turning child mode itself off.

The password is hashed (PBKDF2 + per-password salt), never stored in plain text.
If the parent forgets it there is always a way out: a "forgot password" request
starts a 24-hour timer after which child mode unlocks with no password.
"""

from __future__ import annotations

import hashlib
import secrets
import time

from agent_server import database as db
from agent_server.config import CHILD_HOME_SESSION_ID, HOME_SESSION_ID

OVERRIDE_SECONDS = 24 * 60 * 60

# Appended to the system prompt while child mode is on.
CHILD_MODE_BLOCK = """\n
You are talking to a child. Keep everything safe, kind, and simple, and never do
anything dangerous, illegal, harmful, or inappropriate (or encourage it). If
asked for something inappropriate, politely decline and suggest they ask a
parent or trusted adult.

Treat every exchange as a learning experience. Explain what you are doing and
why in plain words, and use each task as a chance to teach the ideas behind it —
not the low-level details of writing code, but the concepts a thoughtful builder
needs: what a browser does, what a compiler or an app is, why you structure
things a certain way, and how to decide between options. The goal is someone who
understands the big picture of how software is made and how to get the best out
of an AI coding assistant, not someone who can write a for-loop from memory.

When helping with homework or schoolwork, never just give the answer. Guide them
to work it out for themselves with questions, hints, and worked examples they
can follow, so the learning (not just the result) is the point."""


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return salt.hex() + "$" + digest.hex()


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or "$" not in stored:
        return False
    salt_hex, digest_hex = stored.split("$", 1)
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return secrets.compare_digest(digest, expected)


async def child_mode_enabled() -> bool:
    return await db.get_setting("child_mode", "0") == "1"


async def current_profile() -> str:
    """Which profile is active right now: 'child' while child mode is on."""
    return "child" if await child_mode_enabled() else "parent"


async def visible_profile() -> str | None:
    """Which projects may be listed right now. None means all of them.

    The two directions are deliberately not symmetric. A child sees only their
    own projects, which is the whole point of the separation. Ordinary mode
    sees everything, including the child's -- a parent has to be able to open
    what their child made, look through it, and set up a lesson in it, and
    hiding it would mean the only way to reach it is to become the child.
    """
    return "child" if await child_mode_enabled() else None


def profile_for_session(session_id: str) -> str:
    """The profile a session belongs to, from its well-known id."""
    return "child" if session_id == CHILD_HOME_SESSION_ID else "parent"


async def current_home_id() -> str:
    """The home (manager) session for the active profile."""
    return CHILD_HOME_SESSION_ID if await child_mode_enabled() else HOME_SESSION_ID


async def override_remaining() -> int:
    """Seconds until the forgot-password override elapses (0 if none/past)."""
    raw = await db.get_setting("child_override_until", "")
    if not raw:
        return 0
    try:
        return max(0, int(raw) - int(time.time()))
    except ValueError:
        return 0


async def override_elapsed() -> bool:
    """True once a requested forgot-password override has run its course."""
    raw = await db.get_setting("child_override_until", "")
    if not raw:
        return False
    try:
        return int(time.time()) >= int(raw)
    except ValueError:
        return False


async def parent_password_correct(password: str) -> bool:
    return verify_password(password, await db.get_setting("parent_password_hash", ""))
