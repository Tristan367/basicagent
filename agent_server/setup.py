"""Detect which optional pieces are installed, for the first-run setup flow.

The app's Python dependencies are installed by the installer; the components
below are the heavier, optional ones (speech, browser). On first run the manager
AI is told what is missing so it can install the rest for the user.
"""

import shutil
from pathlib import Path

from agent_server.config import (
    FFMPEG_BIN,
    TTS_MODEL,
    TTS_VOICES,
    WHISPER_BIN,
    WHISPER_SERVER_BIN,
    whisper_model,
)


def chromium_installed() -> bool:
    """Playwright's bundled Chromium is used for both the app window and the
    `browser` tool, so its presence matters to more than just one feature."""
    cache = Path.home() / ".cache" / "ms-playwright"
    if not cache.is_dir():
        return False
    return any(p.name.startswith("chromium-") and "headless" not in p.name
               for p in cache.iterdir() if p.is_dir())


def detect() -> list[dict]:
    """One entry per optional component: ``{name, ok, hint}``."""
    stt_ok = bool(WHISPER_BIN and whisper_model() and FFMPEG_BIN)
    streaming_ok = bool(WHISPER_SERVER_BIN and whisper_model())
    tts_ok = bool(TTS_MODEL and TTS_VOICES)
    return [
        {
            "name": "Microphone (speech-to-text)",
            "ok": stt_ok,
            "hint": "needs whisper-cli, a whisper model, and ffmpeg",
        },
        {
            "name": "Live dictation",
            "ok": streaming_ok,
            "hint": "needs whisper-server and a whisper model",
        },
        {
            "name": "Read-aloud (text-to-speech)",
            "ok": tts_ok,
            "hint": "needs kokoro-v1.0.onnx and voices-v1.0.bin in ~/models/tts",
        },
        {
            "name": "Web browser",
            "ok": chromium_installed(),
            "hint": "needs Playwright's Chromium (`playwright install chromium`)",
        },
    ]


def missing() -> list[dict]:
    return [c for c in detect() if not c["ok"]]


def render_report() -> str:
    lines = []
    for c in detect():
        lines.append(f"- {c['name']}: {'installed' if c['ok'] else 'MISSING (' + c['hint'] + ')'}")
    return "\n".join(lines)


def command_available(cmd: str) -> bool:
    return shutil.which(cmd) is not None
