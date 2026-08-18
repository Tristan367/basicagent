"""System prompt construction.

There is exactly one prompt for project sessions and one for the home (manager)
session, both loaded from files so a developer can tune them without touching
code. A session freezes the rendered text on first use, so editing a file or the
date rolling over cannot change a live conversation's prefix and re-bill it at
the cache-miss rate.
"""

import platform
from datetime import datetime
from pathlib import Path

from agent_server import database as db

_PROJECT = Path(__file__).parent.parent
AGENT_PROMPT = (_PROJECT / "system_prompts" / "agent.md").read_text()
MANAGER_PROMPT = (_PROJECT / "system_prompts" / "manager.md").read_text()

COMPACT_PROMPT = """
Summarise this conversation so another engineer could pick the work up cold.

Preserve: what the user asked for, decisions made and why, every file created or
modified with its path, key code and APIs discovered, commands that were run and
what they returned, errors hit and how they were resolved, and what still
remains to be done.

Drop: tool output that no longer matters, exploration that led nowhere, and
pleasantries. Write plain prose and be specific — names, paths, and line numbers.
"""


# session_id -> rendered environment block, frozen for the life of the process
# so files created mid-session cannot invalidate the prompt cache.
_env_cache: dict[str, str] = {}


def clear_env_cache(session_id: str = ""):
    if session_id:
        _env_cache.pop(session_id, None)
    else:
        _env_cache.clear()


def environment_block(project_dir: str, session_id: str = "") -> str:
    key = session_id or project_dir
    cached = _env_cache.get(key)
    if cached is not None:
        return cached
    lines = [
        f"Working directory: {project_dir}",
        f"Platform: {platform.system()} {platform.release()} ({platform.machine()})",
        f"Year: {datetime.now().astimezone().year}",
    ]
    block = "\n".join(lines)
    _env_cache[key] = block
    return block


def build_system_prompt(kind: str, project_dir: str, session_id: str = "") -> str:
    body = MANAGER_PROMPT if kind == "manager" else AGENT_PROMPT
    block = environment_block(project_dir, session_id)
    if "{{environment_tag}}" in body:
        return body.replace("{{environment_tag}}", block)
    return f"{body}\n\n{block}"


async def session_system_prompt(session: dict) -> str:
    """The system prompt for a session, frozen the first time it is needed."""
    stored = session.get("system_prompt")
    if stored:
        return stored
    prompt = build_system_prompt(
        session.get("kind") or "project", session["project_dir"], session["id"]
    )
    if session.get("profile") == "child":
        from agent_server.parental import CHILD_MODE_BLOCK

        prompt += "\n\n" + CHILD_MODE_BLOCK
    await db.update_session(session["id"], system_prompt=prompt)
    return prompt


async def ensure_home_session() -> dict:
    """Create the home (manager) sessions if they do not exist yet.

    There are two: the parent's (used normally) and the child's (used while
    child mode is on). They are fully separate, so a child never sees or touches
    a parent's projects, and each one's prompt is frozen once with the right
    safety profile — no cache invalidation on toggle.
    """
    from agent_server.config import CHILD_HOME_SESSION_ID, HOME_SESSION_ID, PROJECTS_DIR

    home = await db.get_session(HOME_SESSION_ID)
    if home is None:
        home = await db.create_session(
            name="Home", project_dir=str(PROJECTS_DIR), kind="manager", profile="parent"
        )
    child = await db.get_session(CHILD_HOME_SESSION_ID)
    if child is None:
        await db.create_session(
            name="Home",
            project_dir=str(PROJECTS_DIR / "child"),
            kind="manager",
            profile="child",
            session_id=CHILD_HOME_SESSION_ID,
        )
    return home
