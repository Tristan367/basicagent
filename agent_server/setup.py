"""Detect which optional pieces are installed, for the first-run setup flow.

The app's Python dependencies are installed by the installer; the components
below are the heavier, optional ones (speech, browser). On first run the manager
AI is told what is missing so it can install the rest for the user.
"""

import os
import sys
from pathlib import Path

from agent_server.config import TTS_MODEL, TTS_VOICES, stt_available

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
