"""Reading a slice of a file for the chat, and revealing one in the OS.

The user has no file manager open and no idea where the project lives — that is
deliberate. But two things still need to reach across:

* the assistant writing `src/app.js:12-30` should show those lines in the chat,
  so it can point at code without paying to paste it, and
* a user who *does* want the file should be able to click that path and have
  their own file manager open on it.

Reading is confined to the project folder: it happens automatically, from a
path a model wrote, with nobody looking. Revealing is not, because that is a
person clicking a link about their own computer.
"""

import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

from agent_server import database as db
from agent_server import parental

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/files", tags=["files"])

# A peek is a window into a file, not a way to read one. Anything longer is a
# sign the assistant should have summarised instead.
MAX_PEEK_LINES = 200
# Guards against a "text" file that is really a 2GB log or a binary.
MAX_PEEK_BYTES = 2_000_000

IMAGE_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
}

EXT_LANG = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".jsx": "javascript", ".html": "xml", ".htm": "xml",
    ".css": "css", ".json": "json", ".md": "markdown", ".sh": "bash",
    ".bash": "bash", ".yml": "yaml", ".yaml": "yaml", ".toml": "ini",
    ".ini": "ini", ".sql": "sql", ".rs": "rust", ".go": "go", ".java": "java",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cs": "csharp",
    ".rb": "ruby", ".php": "php", ".swift": "swift", ".kt": "kotlin",
}


async def _resolve(session_id: str, raw: str, confine: bool = True) -> Path:
    """Turn a path from a chat message into a real path.

    A relative path is taken against the project folder, which is where the
    assistant is working and so what it means when it writes `src/app.js`.

    `confine` is the difference between the two things this module does.
    Reading a slice of a file happens automatically, from text a model wrote,
    with no one looking — so it stays inside the project. Opening a file
    manager is a person clicking a link about their own computer, and confining
    that would be this app deciding which of the user's files they are allowed
    to look at. It has no business doing that.
    """
    session = await db.get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    root = Path(session["project_dir"]).expanduser().resolve()

    # ValueError as well as OSError: a NUL byte in the path raises
    # "embedded null byte" from the C call, which is not an OSError, so a
    # path with one in it crashed the request instead of being refused.
    try:
        candidate = Path(raw.strip()).expanduser()
        target = candidate if candidate.is_absolute() else root / candidate
        target = target.resolve()
    except (OSError, ValueError) as e:
        raise HTTPException(400, "That path cannot be read") from e

    if confine and target != root and root not in target.parents:
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


# Skipped when packing up a project: caches and dependency folders that are
# large, machine-specific, and rebuildable. `.git` is deliberately kept -- it is
# the project's history, and it is what makes the copy a real project rather
# than a snapshot.
EXPORT_SKIP_DIRS = {
    "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", ".next", ".cache",
    ".DS_Store", ".idea", ".vscode",
}
EXPORT_MAX_BYTES = 500 * 1024 * 1024


def _safe_name(name: str) -> str:
    """A project name turned into a filename that is safe to hand to a browser.

    Path separators become dashes, so nothing can traverse; leading dots are
    dropped as well, since a name like "..-..-etc" is alarming to receive and a
    leading dot would make it a hidden file on the user's machine.
    """
    cleaned = "".join(c if c.isalnum() or c in " ._-" else "-" for c in name)
    cleaned = cleaned.strip().lstrip(".").strip()
    return (cleaned or "project")[:60]


@router.get("/export/{session_id}")
async def export_project(session_id: str):
    """Download the whole project as a zip.

    The user's work should never be trapped inside this app. They may want to
    put a website on a real server, hand it to someone, or simply keep it --
    and they cannot go and find the folder themselves, because it is
    deliberately somewhere they never see.
    """
    import io
    import zipfile

    from fastapi.responses import StreamingResponse

    session = await db.get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    root = Path(session["project_dir"]).expanduser().resolve()
    if not root.is_dir():
        raise HTTPException(404, "This project has no folder yet")

    buffer = io.BytesIO()
    total = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(root.rglob("*")):
            if any(part in EXPORT_SKIP_DIRS for part in path.relative_to(root).parts):
                continue
            if not path.is_file() or path.is_symlink():
                continue
            try:
                total += path.stat().st_size
                if total > EXPORT_MAX_BYTES:
                    raise HTTPException(413, "This project is too large to download.")
                archive.write(path, path.relative_to(root).as_posix())
            except OSError:
                # One unreadable file should not lose the user the other 200.
                log.info("skipped %s while exporting", path, exc_info=True)

    buffer.seek(0)
    name = _safe_name(session["name"]) + ".zip"
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/attachment")
async def attachment(path: str):
    """Serve a file the user attached, so its thumbnail survives a reload.

    Strictly confined to the attachments directory. A blob URL made when the
    file was dropped dies with the page, so without this a restored attachment
    would show a generic icon and its preview would be blank.
    """
    from fastapi.responses import FileResponse

    from agent_server.config import ATTACH_DIR

    root = ATTACH_DIR.resolve()
    try:
        target = Path(path).expanduser().resolve()
    except (OSError, ValueError) as e:
        raise HTTPException(400, "Bad path") from e
    if root not in target.parents or not target.is_file():
        raise HTTPException(404, "No such attachment")

    media = IMAGE_TYPES.get(target.suffix.lower())
    if not media:
        raise HTTPException(415, "Not an image")
    return FileResponse(target, media_type=media)


@router.get("/image")
async def project_image(session_id: str, path: str):
    """Serve an image from inside the project, so the assistant can show one.

    Confined to the project folder, like `peek` and for the same reason: the
    path comes from model output and is followed without anyone looking at it.
    """
    from fastapi.responses import FileResponse

    target = await _resolve(session_id, path)
    media = IMAGE_TYPES.get(target.suffix.lower())
    if not media:
        raise HTTPException(415, "Not an image")
    if not target.is_file():
        raise HTTPException(404, "No such image")
    return FileResponse(target, media_type=media)


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

    # Not confined: it is the user's own computer and their own file manager.
    target = await _resolve(session_id, raw, confine=False)
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


# ── Choosing a folder ────────────────────────────────────────────────────────
#
# Typing a path is a thing you can only do if you already know it, which is the
# opposite of who this app is for. The desktop already has a folder chooser and
# the user already knows how to drive it, so ask the desktop for one and take
# the path it gives back.
#
# This is the same trick as revealing a file above: the app and the desktop are
# on the same computer, so the server is the one that can reach it.

# How long to leave the dialog open. Long enough for somebody to go and look for
# the folder, short enough that a dialog nobody ever answers -- opened on a
# screen they cannot see, say -- does not leave a process behind forever.
PICKER_TIMEOUT = 300


def _picker_command(start: Path) -> list[str] | None:
    """The platform's "choose a folder" dialog, or None if there isn't one."""
    if sys.platform == "darwin":
        return ["osascript", "-e",
                'POSIX path of (choose folder with prompt "Choose a folder for your project")']
    if sys.platform.startswith("win"):
        # -STA because the folder dialog is a WinForms control and will not open
        # on a multi-threaded apartment.
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$d.Description = 'Choose a folder for your project';"
            "if ($d.ShowDialog() -eq 'OK') { Write-Output $d.SelectedPath }"
        )
        return ["powershell", "-NoProfile", "-STA", "-Command", script]
    # Freedesktop. zenity is GNOME's and is on most desktops; kdialog is KDE's;
    # yad and qarma are drop-in replacements people install when neither is.
    if shutil.which("zenity"):
        return ["zenity", "--file-selection", "--directory",
                "--title=Choose a folder for your project", f"--filename={start}/"]
    if shutil.which("kdialog"):
        return ["kdialog", "--getexistingdirectory", str(start),
                "--title", "Choose a folder for your project"]
    for alt in ("yad", "qarma"):
        if shutil.which(alt):
            return [alt, "--file-selection", "--directory",
                    "--title=Choose a folder for your project", f"--filename={start}/"]
    return None


@router.get("/folder-picker")
async def folder_picker_available():
    """Whether asking for a folder would do anything.

    The button is hidden when it would not, the same as the camera: a control
    that cannot work is worse than no control, because pressing it teaches you
    nothing about why.
    """
    return {"available": _picker_command(Path.home()) is not None}


@router.post("/folder-picker")
async def folder_picker():
    """Open the desktop's folder chooser and return what the user picked."""
    if await parental.current_profile() == "child":
        raise HTTPException(403, "Choosing a folder is not available in child mode.")

    cmd = _picker_command(Path.home())
    if cmd is None:
        raise HTTPException(501, "This computer has no folder chooser the app can open.")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as e:
        log.info("folder chooser would not start: %s", e)
        raise HTTPException(501, "The folder chooser would not open.") from e

    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=PICKER_TIMEOUT)
    except TimeoutError:
        proc.kill()
        # Not an error the user should see a red message about: they walked away
        # from a dialog. The box they typed into is still there and still works.
        return {"ok": True, "path": ""}

    # Cancel is a non-zero exit with nothing on stdout, and is not a failure.
    path = out.decode("utf-8", "replace").strip().splitlines()
    chosen = path[0].strip() if path else ""
    if not chosen:
        return {"ok": True, "path": ""}
    return {"ok": True, "path": str(Path(chosen))}
