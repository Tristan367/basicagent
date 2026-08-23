"""Detect which optional pieces are installed, for the first-run setup flow.

The app's Python dependencies are installed by the installer; the components
below are the heavier, optional ones (speech, browser). On first run the manager
AI is told what is missing so it can install the rest for the user.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

from agent_server.config import TTS_MODEL, TTS_VOICES, stt_available

log = logging.getLogger(__name__)

# Chromium is about 150 MB and the machine may be on a poor connection. Long
# enough to be a real download, short enough that a hung mirror does not hold
# a reply open forever.
INSTALL_TIMEOUT = 15 * 60

# The hints below are read by two audiences at once: the user, in a chat message
# on first run, and the assistant, which is expected to act on them. So each one
# says what the piece is for in plain words and then gives the exact command
# that installs it -- a hint the assistant has to paraphrase is a hint it gets
# wrong, and "put two files in a folder" is not something to say to somebody who
# cannot see the screen.
#
# `sys.executable` rather than a guessed path: the server is already running in
# the environment the command has to run in.


def _playwright_cache() -> Path:
    """Where Playwright keeps its browsers on this machine.

    Three different places, and this checked only the Linux one -- so on a Mac
    it reported Chromium missing however many times it had been installed, and
    the manager went on offering to install it forever.
    """
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override and override != "0":
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def chromium_installed() -> bool:
    """Playwright's bundled Chromium is used for both the app window and the
    `browser` tool, so its presence matters to more than just one feature.

    A directory check, which is cheap enough to run every time Settings is
    drawn. It answers "has this been installed", which is what the setup list
    is for. It does NOT promise the launch will work -- only the launch knows
    that -- so the code that opens a window reports its own failure rather than
    asking here first.
    """
    cache = _playwright_cache()
    if not cache.is_dir():
        return False
    return any(p.name.startswith("chromium-") and "headless" not in p.name
               for p in cache.iterdir() if p.is_dir())


def looks_like_missing_browser(e: Exception | str) -> bool:
    """Whether a launch failure was "there is no Chromium here".

    Matched on Playwright's own wording because Playwright does not give this
    a type of its own. Both phrases are checked: the first is what a missing
    executable says, the second is the line it prints telling you the fix.
    """
    text = str(e)
    return "Executable doesn't exist" in text or "playwright install" in text


_install_lock = asyncio.Lock()


async def ensure_chromium() -> bool:
    """Put Chromium back, without anybody being asked to do anything.

    The user has no terminal. Telling them a 150 MB download is missing and
    that somebody else can fetch it is a worse answer than fetching it, and
    the installer already fetches it on the way in -- this is only ever the
    repair after something removed it, which for a cache directory is
    routinely a disk-cleaning tool the user ran for unrelated reasons.

    Behind a lock because the app window, the preview and the `browser` tool
    can all discover it missing within a second of each other, and three
    simultaneous downloads of the same 150 MB would be a remarkable way to
    fix a disk-space problem.
    """
    async with _install_lock:
        if chromium_installed():
            return True
        log.info("Chromium is missing; installing it")
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "playwright", "install", "chromium",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), INSTALL_TIMEOUT)
        except (OSError, TimeoutError) as e:
            log.warning("could not install Chromium: %s", e)
            return False
        if proc.returncode != 0:
            log.warning("could not install Chromium (code %s): %s",
                        proc.returncode, (out or b"").decode(errors="replace")[-500:])
            return False
        log.info("Chromium installed")
        return chromium_installed()


def godot_installed() -> bool:
    """Imported lazily: `godot` reaches for the data directory, and this module
    is imported by the installer's own checks before there is one."""
    from agent_server import godot

    return godot.installed()


def detect() -> list[dict]:
    """One entry per optional component: ``{name, ok, hint}``."""
    tts_ok = bool(TTS_MODEL and TTS_VOICES)
    python = sys.executable
    return [
        {
            "name": "Talking to it (dictation)",
            "ok": stt_available(),
            "hint": f"install with: {python} -m pip install faster-whisper",
        },
        {
            "name": "Reading replies aloud",
            "ok": tts_ok,
            # About 350 MB, so worth saying so before starting it.
            "hint": (
                f"install with: {python} -m agent_server.downloads read-aloud "
                "(about 350 MB, takes a few minutes)"
            ),
        },
        {
            "name": "Showing you websites and apps you build",
            "ok": chromium_installed(),
            "hint": f"install with: {python} -m playwright install chromium",
        },
        {
            # Named for what it lets them do, not for the engine. "Godot is not
            # installed" means nothing to somebody who has never made a game;
            # "you cannot make games yet" is the same fact and is actionable.
            "name": "Making games",
            "ok": godot_installed(),
            "hint": (
                f"install with: {python} -m agent_server.godot install web "
                "(about 90 MB)"
            ),
        },
    ]


def missing() -> list[dict]:
    return [c for c in detect() if not c["ok"]]
