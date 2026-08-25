"""What the agent may reach, and the one question it stops to ask.

Work inside a project is never asked about -- the user should not have to
answer "may I read your own file?" forty times a day, and one who is asked that
learns only to press yes. What guards the rest is small and deliberate:

* a deny-list of system paths nothing may ever write to,
* the destructive-command guard in `tools/bash.py` (`rm -rf /` and friends),
* and, below, the question that goes on the screen when a file tool reaches
  outside the project.

All three are short on purpose. A long list starts refusing ordinary work, and
what it protects against is a confused model rather than a hostile one.
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

# Never writable, under any circumstances.
DENIED_PREFIXES = (
    "/proc", "/sys", "/dev", "/boot", "/etc/shadow", "/etc/sudoers",
)


def is_denied(path: Path) -> bool:
    try:
        text = str(path.resolve())
    except OSError:
        text = str(path)
    return text.startswith(DENIED_PREFIXES)


# ── reaching outside the project ────────────────────────────────────────────
#
# A project's files are the AI's business and it works in them without being
# asked. Anything else on the computer is not, and this is where that line is
# drawn.
#
# It is a real line and it is worth being clear about what it does and does not
# cover. The file tools -- read, edit, write -- go through here, and that is how
# the assistant reads and writes files essentially all of the time. A shell
# command can still reach the filesystem; policing every command is not
# something this app can do honestly, and pretending otherwise would be worse
# than the gap. What this buys is that the ordinary path is visible and the
# user decides.
#
# In child mode the question is the same and the yes costs the parent password.
# Not a wall: a child with photos in their downloads folder, or an old project
# to build on, is asking for something entirely reasonable, and walling them
# into one folder would teach them the app is broken rather than careful.
# Approving file access is a normal part of using these tools, and watching a
# grown-up do it is the lesson. Saying *no* never costs anything -- stopping
# something must always be the cheap answer.

ALLOWED_KEY = "allowed_folders"

# Long enough that somebody who wandered off mid-task can come back to it, short
# enough that a turn cannot sit open forever waiting for a person who has closed
# the laptop. Running out is a refusal, which is the safe way round.
ASK_TIMEOUT = 15 * 60

# Set by the agent module, so a request can be put on the screen. Unset in
# tests and anywhere with no user attached, where the answer is simply no.
_asker: Callable[[str, dict], None] | None = None
_pending: dict[str, dict] = {}
_answers: dict[str, asyncio.Future] = {}


def set_asker(asker: Callable[[str, dict], None] | None) -> None:
    global _asker
    _asker = asker


async def allowed_folders() -> list[str]:
    import json

    from agent_server import database as db

    try:
        saved = json.loads(await db.get_setting(ALLOWED_KEY, "") or "[]")
    except ValueError:
        return []
    return [str(f) for f in saved] if isinstance(saved, list) else []


async def allow_folder(folder: Path) -> None:
    """Remember that this folder is fine, so nobody is asked about it again."""
    import json

    from agent_server import database as db

    folders = await allowed_folders()
    text = str(folder)
    if text not in folders:
        folders.append(text)
        await db.set_setting(ALLOWED_KEY, json.dumps(folders))


async def never_asked() -> list[Path]:
    """Folders nobody is ever asked about, because asking would be noise.

    Each of these is somewhere the user has either already said yes to or has
    no idea exists, and a dialog about it teaches only that dialogs are things
    you click past.

    * The temporary folder. Every program on the computer uses it, this app
      uses it constantly, and nobody who is not technical has ever put anything
      private there -- most have never heard of it. "May I read /tmp/tmp8fz2?"
      is a question with no useful answer.
    * Where attachments land. Somebody who has just dropped a photo into the
      chat has consented in the plainest way there is; asking again is asking
      them to confirm what they just did.
    * Their own projects. Reaching a game they made last month is not reaching
      out of the app, and needing permission to look at your own earlier work
      would be a nuisance with nothing on the other side of it. In child mode
      this is the child's own projects folder and not the parent's, which is
      the same line the project list already draws.
    """
    import tempfile

    from agent_server import parental
    from agent_server.config import ATTACH_DIR, PROJECTS_DIR

    # Both, because they are not always the same thing: `gettempdir` follows
    # TMPDIR, which macOS sets to a per-user folder under /var, while plenty of
    # tools write to /tmp regardless.
    folders = [Path(tempfile.gettempdir()), Path("/tmp"), ATTACH_DIR]  # noqa: S108
    if await parental.child_mode_enabled():
        folders.append(PROJECTS_DIR / "child")
    else:
        folders.append(PROJECTS_DIR)
    return folders


async def already_allowed(path: Path) -> bool:
    known = [str(f) for f in await never_asked()] + await allowed_folders()
    for folder in known:
        try:
            if path.resolve().is_relative_to(Path(folder).resolve()):
                return True
        except (OSError, ValueError):
            continue
    return False


def inside(path: Path, project_dir: str) -> bool:
    try:
        return path.resolve().is_relative_to(Path(project_dir).resolve())
    except (OSError, ValueError):
        return False


def pending(session_id: str) -> dict | None:
    """A question already on the screen, so a reloaded page can ask it again."""
    return _pending.get(session_id)


def answer(session_id: str, request_id: str, reply: str) -> bool:
    """The user's decision, from the dialog. True if it was still being asked."""
    waiting = _answers.get(session_id)
    current = _pending.get(session_id) or {}
    if waiting is None or current.get("id") != request_id:
        return False
    _pending.pop(session_id, None)
    _answers.pop(session_id, None)
    if not waiting.done():
        waiting.set_result(reply)
    return True


def forget(session_id: str) -> None:
    """Drop an unanswered question, because the turn it belonged to is over."""
    waiting = _answers.pop(session_id, None)
    _pending.pop(session_id, None)
    if waiting is not None and not waiting.done():
        waiting.set_result("no")


async def ask(session_id: str, path: Path, verb: str) -> str:
    """Put the question on the screen and wait. "once", "always", or "no".

    No screen, no answer: with nothing registered to display the question there
    is nobody to answer it, and the honest result is a refusal rather than a
    turn that hangs.
    """
    if _asker is None:
        return "no"
    from agent_server import parental

    request = {
        "id": uuid.uuid4().hex[:8],
        "path": str(path),
        "folder": str(path.parent),
        "verb": verb,
        "name": path.name,
        # Whether saying yes will cost the parent password. The dialog needs to
        # know so it can say so before the button is pressed rather than after
        # -- a child pressing "yes" and then meeting a password box has been
        # told no in the most annoying way available.
        "locked": await parental.child_mode_enabled(),
    }
    waiting: asyncio.Future = asyncio.get_running_loop().create_future()
    _pending[session_id] = request
    _answers[session_id] = waiting
    with contextlib.suppress(Exception):
        _asker(session_id, request)
    try:
        reply = await asyncio.wait_for(asyncio.shield(waiting), timeout=ASK_TIMEOUT)
    except (TimeoutError, asyncio.CancelledError):
        reply = "no"
    finally:
        _pending.pop(session_id, None)
        _answers.pop(session_id, None)
    if reply == "always":
        await allow_folder(path.parent)
    return reply
