"""System prompt construction.

There is exactly one prompt for project sessions and one for the home (manager)
session, both loaded from files so a developer can tune them without touching
code. A session freezes the rendered text on first use, so editing a file or the
date rolling over cannot change a live conversation's prefix and re-bill it at
the cache-miss rate.
"""

import logging
import platform
import sys
from datetime import datetime
from pathlib import Path

from agent_server import database as db

log = logging.getLogger(__name__)

_PROJECT = Path(__file__).parent.parent
AGENT_PROMPT = (_PROJECT / "system_prompts" / "agent.md").read_text()
MANAGER_PROMPT = (_PROJECT / "system_prompts" / "manager.md").read_text()

# Every line below answers something a model actually did instead of
# summarising: carried on the conversation, narrated what it was about to do,
# reached for a tool, refused because the request looked like a code change, or
# expanded rather than compressed. Written against observed failures, not taste.
COMPACT_PROMPT = """
You are compressing the earlier part of a conversation so it can be dropped from
the context window. What you write replaces those messages permanently — anything
you leave out is gone for good.

Write only the summary. No preamble, no sign-off, and no remarks about the
conversation or about the act of summarising it. This is not your turn in the
conversation: do not answer anything in it, do not address the user, and do not
call tools, even though they are available to you.

Keep, and keep exactly as they were written:
- Anything the user asked you to remember, and every name, identifier, path,
  number, URL, or version they gave you.
- What the user is trying to achieve, and any constraint or preference stated.
- Decisions taken, and the reason for each.
- Every file created, modified, or deleted, with its full path and what changed.
- Commands run and what they returned, failures especially.
- Errors hit, and whether each was resolved or is still open.
- What is left to do.

Leave out tool output that no longer matters, searches that found nothing,
reasoning that led nowhere, and pleasantries.

Be concrete: names, paths, and line numbers rather than descriptions of them.
Plain prose or short lists, whichever is clearer.

The result must be substantially shorter than what it replaces — that is the
whole point — and its length should match how much actually happened. If very
little happened, say so in a sentence or two and stop. Never decline, and never
answer with nothing.
"""

# Sent only when the first attempt came back empty, which small models do
# surprisingly often on a long transcript.
RETRY_NUDGE = """
You returned nothing. Write the summary now, as plain text, starting with the
first sentence of it. Even a few lines is better than an empty reply.
"""


# session_id -> rendered environment block, frozen for the life of the process
# so files created mid-session cannot invalidate the prompt cache.
_env_cache: dict[str, str] = {}


def clear_env_cache(session_id: str = ""):
    if session_id:
        _env_cache.pop(session_id, None)
    else:
        _env_cache.clear()


def environment_block(project_dir: str, session_id: str = "", manager: bool = False) -> str:
    key = ("manager:" if manager else "") + (session_id or project_dir)
    cached = _env_cache.get(key)
    if cached is not None:
        return cached
    lines = [
        f"Working directory: {project_dir}",
        f"Platform: {platform.system()} {platform.release()} ({platform.machine()})",
        f"Year: {datetime.now().astimezone().year}",
    ]
    if manager:
        # Only the manager. It is the one that installs the app's own optional
        # pieces, and the commands for that need a real interpreter path -- the
        # working directory above is the projects folder, not the app's. A
        # project's agent has no business knowing where the app itself lives.
        lines += [
            f"This app's folder: {_PROJECT}",
            f"This app's Python: {sys.executable}",
        ]
    else:
        # Only a project's agent, and only when it is actually there. A game is
        # the one thing this app can build that needs a tool it did not come
        # with, and the difference between "Godot is installed at <path>" and
        # nothing at all is the difference between making a game and explaining
        # why you cannot. One line, and absent the rest of the time.
        from agent_server import godot

        found = godot.binary()
        if found:
            targets = ", ".join(godot.targets_installed()) or "none yet"
            lines.append(f"Godot {godot.VERSION}: {found}  (export targets: {targets})")
    block = "\n".join(lines)
    _env_cache[key] = block
    return block


def build_system_prompt(kind: str, project_dir: str, session_id: str = "") -> str:
    body = MANAGER_PROMPT if kind == "manager" else AGENT_PROMPT
    block = environment_block(project_dir, session_id, manager=kind == "manager")
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

    provider, model = await _home_model()

    home = await db.get_session(HOME_SESSION_ID)
    if home is None:
        home = await db.create_session(
            name="Home", project_dir=str(PROJECTS_DIR), kind="manager", profile="parent",
            provider=provider, model=model,
        )
    child = await db.get_session(CHILD_HOME_SESSION_ID)
    if child is None:
        await db.create_session(
            name="Home",
            project_dir=str(PROJECTS_DIR / "child"),
            kind="manager",
            profile="child",
            provider=provider, model=model,
            session_id=CHILD_HOME_SESSION_ID,
        )

    for session_id in (HOME_SESSION_ID, CHILD_HOME_SESSION_ID):
        await _repair_home_model(session_id, provider, model)
    return await db.get_session(HOME_SESSION_ID)


async def _home_model() -> tuple[str, str]:
    """The provider and model the home assistant should be running on.

    Whatever the user's default is, resolved to something they hold a key for.
    """
    from agent_server.config import provider_for_model, split_custom_choice
    from agent_server.model_catalog import effective_default_model

    model = effective_default_model(await db.get_all_settings())
    if model.startswith("custom:"):
        endpoint, model_id = split_custom_choice(model)
        return endpoint, model_id or endpoint
    return provider_for_model(model), model


async def _repair_home_model(session_id: str, provider: str, model: str):
    """Move the home assistant onto a provider the user actually has a key for.

    It used to be created on a hard-coded model regardless of what the user had
    set up. Somebody whose only key was Gemini -- which is the free option this
    app points people at -- opened the app, said hello, and was told "No API key
    is set up yet. Add one in Settings." They had just done that. There is
    nothing in the app that would have told them what was wrong.

    Only ever moved off a provider with no credentials. A working home session
    is left exactly where it is, because the user may have chosen it.
    """
    from agent_server.providers import get_provider

    session = await db.get_session(session_id)
    if session is None:
        return
    try:
        current = get_provider(session["provider"])
    except ValueError:
        current = None
    if current is not None and current.has_credentials():
        return
    if session["provider"] == provider and session["model"] == model:
        return
    await db.update_session(session_id, provider=provider, model=model)
    clear_env_cache(session_id)
    log.info("home session %s moved to %s/%s (no key for %s)",
             session_id, provider, model, session["provider"])
