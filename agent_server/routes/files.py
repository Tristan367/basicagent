"""Reading a slice of a file for the chat, and revealing one in the OS.

The user has no file manager open and no idea where the project lives — that is
deliberate. But two things still need to reach across:

* the assistant writing `src/app.js:12-30` should show those lines in the chat,
  so it can point at code without paying to paste it, and
* a user who *does* want the file should be able to click that path and have
  their own file manager open on it.

Both are scoped to the session's project directory. Nothing outside it can be
read or revealed, whatever path arrives.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

from agent_server import database as db

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/files", tags=["files"])

# A peek is a window into a file, not a way to read one. Anything longer is a
# sign the assistant should have summarised instead.
MAX_PEEK_LINES = 200
# Guards against a "text" file that is really a 2GB log or a binary.
MAX_PEEK_BYTES = 2_000_000

EXT_LANG = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".jsx": "javascript", ".html": "xml", ".htm": "xml",
    ".css": "css", ".json": "json", ".md": "markdown", ".sh": "bash",
    ".bash": "bash", ".yml": "yaml", ".yaml": "yaml", ".toml": "ini",
    ".ini": "ini", ".sql": "sql", ".rs": "rust", ".go": "go", ".java": "java",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cs": "csharp",
    ".rb": "ruby", ".php": "php", ".swift": "swift", ".kt": "kotlin",
}


async def _resolve(session_id: str, raw: str) -> Path:
    """Turn a path from a chat message into a real path inside the project.

    The path comes from model output, so it is untrusted: it is resolved and
    then checked to be inside the project directory, which stops `../../` and a
    symlink pointing out of the tree alike.
    """
    session = await db.get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    root = Path(session["project_dir"]).expanduser().resolve()

    candidate = Path(raw.strip()).expanduser()
    target = candidate if candidate.is_absolute() else root / candidate
    try:
        target = target.resolve()
    except OSError as e:
        raise HTTPException(400, "That path cannot be read") from e

    if target != root and root not in target.parents:
        raise HTTPException(403, "That file is outside this project")
    return target


@router.get("/peek")
async def peek(session_id: str, path: str, start: int = 1, end: int = 0):
    """Return a slice of a text file, for the inline window in the chat."""
    target = await _resolve(session_id, path)
    if not target.is_file():
        raise HTTPException(404, "No such file")
    if target.stat().st_size > MAX_PEEK_BYTES:
        raise HTTPException(413, "That file is too big to show here")

    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise HTTPException(400, f"Could not read it: {e}") from e
    if "\x00" in text[:4096]:
        raise HTTPException(415, "That looks like a program, not text")

    lines = text.splitlines()
    start = max(1, start)
    end = len(lines) if end <= 0 else min(end, len(lines))
    if start > len(lines):
        raise HTTPException(416, f"That file only has {len(lines)} lines")
    end = min(end, start + MAX_PEEK_LINES - 1)

    return {
        "path": str(target),
        "name": target.name,
        "start": start,
        "end": end,
        "total": len(lines),
        "lang": EXT_LANG.get(target.suffix.lower(), ""),
        "text": "\n".join(lines[start - 1:end]),
    }


def _reveal_command(target: Path) -> list[str]:
    """The platform's "show me this in the file manager" command.

    Each of these selects the file within its folder rather than opening the
    file itself, which is what the user actually wants: somewhere to look, not
    an application guessing how to edit their code.
    """
    if sys.platform == "darwin":
        return ["open", "-R", str(target)]
    if sys.platform.startswith("win"):
        # /select, needs the argument attached, and explorer wants backslashes.
        return ["explorer", f"/select,{target}"]
    # Freedesktop: supported by Nautilus, Dolphin, Thunar and others. Falls back
    # to opening the containing folder below if the file manager lacks it.
    return ["dbus-send", "--session", "--print-reply",
            "--dest=org.freedesktop.FileManager1",
            "/org/freedesktop/FileManager1",
            "org.freedesktop.FileManager1.ShowItems",
            f"array:string:file://{target}", "string:"]


@router.post("/reveal")
async def reveal(payload: dict):
    """Open the user's own file manager on a file inside the project."""
    session_id = str(payload.get("session_id", "")).strip()
    raw = str(payload.get("path", "")).strip()
    if not session_id or not raw:
        raise HTTPException(400, "session_id and path are required")

    target = await _resolve(session_id, raw)
    if not target.exists():
        raise HTTPException(404, "No such file or folder")

    async def run(cmd: list[str]) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            return await asyncio.wait_for(proc.wait(), timeout=5) == 0
        except (OSError, TimeoutError):
            return False

    if await run(_reveal_command(target)):
        return {"ok": True, "path": str(target)}

    # Fall back to just opening the containing folder. Less precise, but on a
    # desktop with no file manager registered for ShowItems it is the
    # difference between "nothing happened" and a window appearing.
    folder = target if target.is_dir() else target.parent
    opener = {"darwin": "open"}.get(sys.platform, "xdg-open")
    if sys.platform.startswith("win"):
        try:
            os.startfile(str(folder))  # type: ignore[attr-defined]
            return {"ok": True, "path": str(folder), "fallback": True}
        except OSError:
            pass
    elif await run([opener, str(folder)]):
        return {"ok": True, "path": str(folder), "fallback": True}

    log.info("could not reveal %s on %s", target, sys.platform)
    raise HTTPException(
        501, "This computer has no file manager the app can open."
    )
