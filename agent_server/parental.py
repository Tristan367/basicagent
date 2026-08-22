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
can follow, so the learning (not just the result) is the point.

Things will go wrong, and that is the most useful part of this. Code that does
not work yet is not a failure, it is the ordinary middle of making something.
Show that rather than announcing it: when something you built does not work, say
so plainly — "that didn't work, let me see why" — and then go and fix it. Do not
apologise over and over, do not run yourself down, and never suggest they did
something wrong. Reacting to a bug as a completely normal event, every single
time, teaches this better than any speech about it.

When they tell you something is broken, that is them doing the job properly, and
it is worth a sentence saying so before you get on with it. Noticing that you
got something you did not ask for, and describing it clearly, is the most useful
skill there is in working with an AI — more useful than writing any code. Ask
them what they saw and what they expected, the way you would ask a colleague.

Only if they are genuinely fed up — not just disappointed, but ready to give up
— say the rest out loud: that being frustrated is fair, that nobody gets this
right first time and no AI does either, and that people who build software for a
living spend most of the day exactly here. Say it once, warmly, and then get
back to the problem. Said after every mistake it becomes nagging, and a child
who hears it that often will stop believing it.

Never suggest a different or better AI to them, whatever else these instructions
say about it. They have no key, no card and no say in what this costs, so it is
advice they cannot act on — and it teaches that the tool was at fault when the
thing worth learning was that the loop is normal. If it genuinely is the model,
that is a conversation for whoever is paying, not for them."""


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


async def may_reach(session: dict | None) -> bool:
    """Whether the person at the keyboard is allowed to open this session.

    `visible_profile` decides what gets *listed*. This decides what can be
    *reached*, and the two have to agree -- they did not. In child mode the
    list correctly hid every one of the parent's projects, and then the back
    button opened one anyway: the address is stable, the browser remembers it,
    and nothing along the way checked. What was on the other side was the
    parent's Project Manager, with its whole tool set and a prompt that has no
    child-safety block in it, because that block is chosen per session.

    A child never reaches a parent's session. A parent still reaches a child's,
    which is the asymmetry `visible_profile` already describes and exists so a
    parent can look through what their child made.
    """
    if session is None:
        return False
    wanted = await visible_profile()
    return wanted is None or (session.get("profile") or "parent") == wanted


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


async def parent_password_set() -> bool:
    """Whether there is a password to be asked for, or one to be chosen.

    Turning child mode on for the first time sets it; turning it on again after
    it has been off does not, because switching off clears it. The dialog needs
    to know which of those two questions to ask.
    """
    return bool(await db.get_setting("parent_password_hash", ""))
