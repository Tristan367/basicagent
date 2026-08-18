"""Static configuration. Runtime-mutable settings live in the `settings` DB table.

This app is the accessibility-first sibling of the full CodeAgent: the same
backend, one hard-coded prompt, no per-session knobs. Everything the user would
otherwise have to decide is defaulted or derived here.
"""

import os
import shutil
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# The name shown in the UI. Kept in one place so a real name can be swapped in
# once it is chosen, without touching templates or the launcher.
APP_NAME = "Assistant"

# "dark" or "light". Dark is the default; the user chooses on first run and can
# change it in Settings (or by asking the assistant).
DEFAULT_THEME = "dark"

# Changes on every process start, so a restart forces the browser to re-fetch
# the CSS/JS instead of serving cached copies.
APP_VERSION = str(int(time.time()))


def contrast_text(hex_color: str) -> str:
    """Return black or white, whichever reads better on the given color.

    Used to keep the user's own chat bubble readable no matter which accent
    colour they pick (a white accent needs black text, and so on).
    """
    try:
        c = (hex_color or "").lstrip("#")
        r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return "#ffffff"
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#000000" if luminance > 0.6 else "#ffffff"


def _default_data_dir() -> Path:
    """User data lives outside the checkout, in the XDG data location.

    The database holds API keys and every conversation, so it must never live in
    the working tree where a `git clean -xdf` could take it. XDG is also where a
    backup tool will find it.
    """
    if os.name == "nt":
        base = Path(os.getenv("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "basicagent"


DATA_DIR = Path(os.getenv("BASICAGENT_DATA_DIR") or _default_data_dir())
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.getenv("BASICAGENT_DB") or DATA_DIR / "agent.db")

# Projects the session manager creates live here by default. A hidden folder on
# purpose: the user should never have to think about where their files went, and
# putting them in an obvious place invites the user to rearrange them and break
# the sessions. The manager can still put a project anywhere the user asks for.
PROJECTS_DIR = Path(os.getenv("BASICAGENT_PROJECTS_DIR") or DATA_DIR / "projects")
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

# The well-known id of the home session (the session-manager AI). Every other
# session is a project the manager created.
HOME_SESSION_ID = "home"

# The home session used while child mode is on. Child mode keeps its own
# manager and projects, so a child can't see (or delete) a parent's work and
# the child-safety prompt never has to be re-applied to existing sessions.
CHILD_HOME_SESSION_ID = "home-child"

# A marker the server writes when the user quits from the UI. The desktop
# launcher polls for it and closes the window.
QUIT_SIGNAL = DATA_DIR / "quit.signal"

_TMP = Path(tempfile.gettempdir())
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR") or _TMP / "basicagent_uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Files dropped into the chat are saved here and their path is handed to the
# agent, so it can read them with its normal tools. Lives under DATA_DIR (not
# /tmp) so a path given to the AI stays valid after a restart.
ATTACH_DIR = DATA_DIR / "attachments"
ATTACH_DIR.mkdir(parents=True, exist_ok=True)

# Frames written by `browser` and `capture`, read back by `vision`.
CAPTURE_DIR = Path(os.getenv("BASICAGENT_CAPTURE_DIR") or _TMP / "basicagent_captures")
CAPTURE_DIR.mkdir(parents=True, exist_ok=True)

# Cookies/localStorage saved by `browser`, so a login survives a browser restart.
BROWSER_STATE_DIR = Path(os.getenv("BASICAGENT_BROWSER_STATE_DIR") or DATA_DIR / "browser_state")
BROWSER_STATE_DIR.mkdir(parents=True, exist_ok=True)

# ── Models ──────────────────────────────────────────────────────────────────
DEFAULT_PROVIDER = "deepseek"
DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_THINKING_EFFORT = "high"

REASONING_EFFORTS = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]

MODELS = [
    {
        "id": "deepseek-v4-pro",
        "name": "DeepSeek V4 Pro",
        "provider": "deepseek",
        "context": 1_000_000,
        "price_in_hit": 0.003625,
        "price_in_miss": 0.435,
        "price_out": 0.87,
    },
    {
        "id": "deepseek-v4-flash",
        "name": "DeepSeek V4 Flash",
        "provider": "deepseek",
        "context": 1_000_000,
        "price_in_hit": 0.0028,
        "price_in_miss": 0.14,
        "price_out": 0.28,
    },
    {
        "id": "anthropic/claude-sonnet-4-20250514",
        "name": "Claude Sonnet 4",
        "provider": "openrouter",
        "context": 200_000,
        "price_in_hit": 1.25,
        "price_in_miss": 3.0,
        "price_out": 15.0,
    },
    {
        "id": "openai/gpt-4.1",
        "name": "GPT-4.1",
        "provider": "openrouter",
        "context": 1_000_000,
        "price_in_hit": 1.25,
        "price_in_miss": 2.0,
        "price_out": 8.0,
    },
    {
        "id": "google/gemini-2.5-pro",
        "name": "Gemini 2.5 Pro",
        "provider": "openrouter",
        "context": 1_000_000,
        "price_in_hit": 0.25,
        "price_in_miss": 1.25,
        "price_out": 10.0,
    },
    {
        "id": "meta-llama/llama-4-maverick",
        "name": "Llama 4 Maverick",
        "provider": "openrouter",
        "context": 1_000_000,
        "price_in_hit": 0.15,
        "price_in_miss": 0.20,
        "price_out": 0.60,
    },
    {
        "id": "claude-fable-5",
        "name": "Claude Fable 5",
        "provider": "anthropic",
        "context": 1_000_000,
        "max_output": 128_000,
        "price_in_hit": 1.0,
        "price_in_miss": 10.0,
        "price_out": 50.0,
    },
    {
        "id": "claude-opus-5",
        "name": "Claude Opus 5",
        "provider": "anthropic",
        "context": 1_000_000,
        "max_output": 128_000,
        "price_in_hit": 0.5,
        "price_in_miss": 5.0,
        "price_out": 25.0,
    },
    {
        "id": "claude-sonnet-5",
        "name": "Claude Sonnet 5",
        "provider": "anthropic",
        "context": 1_000_000,
        "max_output": 128_000,
        "price_in_hit": 0.3,
        "price_in_miss": 3.0,
        "price_out": 15.0,
    },
    {
        "id": "claude-haiku-4-5",
        "name": "Claude Haiku 4.5",
        "provider": "anthropic",
        "context": 200_000,
        "max_output": 64_000,
        "price_in_hit": 0.1,
        "price_in_miss": 1.0,
        "price_out": 5.0,
    },
]

MODELS_BY_ID = {m["id"]: m for m in MODELS}

DYNAMIC_DEEPSEEK_MODELS: list[str] = []


def register_dynamic_deepseek_models(ids: list[str]) -> None:
    for mid in ids:
        if mid and mid not in MODELS_BY_ID and mid not in DYNAMIC_DEEPSEEK_MODELS:
            DYNAMIC_DEEPSEEK_MODELS.append(mid)


def is_known_model(model_id: str) -> bool:
    return model_id in MODELS_BY_ID or model_id in DYNAMIC_DEEPSEEK_MODELS


UNKNOWN_MODEL = {
    "context": 131_072,
    "max_output": 8_192,
    "price_in_hit": 0.0,
    "price_in_miss": 0.0,
    "price_out": 0.0,
    "priced": False,
}

DEFAULT_MAX_OUTPUT = 8_192


def model_info(model_id: str) -> dict:
    entry = MODELS_BY_ID.get(model_id)
    if not entry:
        return {**UNKNOWN_MODEL, "id": model_id}
    return {"max_output": DEFAULT_MAX_OUTPUT, **entry, "priced": True}


def provider_for_model(model_id: str) -> str:
    entry = MODELS_BY_ID.get(model_id)
    return entry["provider"] if entry else DEFAULT_PROVIDER


def resolve_model_choice(choice: str, custom_model: str = "") -> tuple[str, str]:
    """Turn the model picker's value into a (provider, model) pair."""
    choice = (choice or "").strip()
    if choice.startswith("custom:"):
        model = custom_model.strip()
        if not model:
            raise ValueError("Type the model id the custom endpoint expects.")
        return choice, model
    if not is_known_model(choice):
        raise ValueError(f"Unknown model: {choice}")
    return provider_for_model(choice), choice


# Compaction runs automatically when a session's live context passes this many
# tokens. There is no UI for it; the user should never have to know it happens.
COMPACT_THRESHOLD_TOKENS = int(os.getenv("COMPACT_THRESHOLD_TOKENS", "262144"))
MIN_COMPACT_THRESHOLD = 4096

MAX_TOOL_RESULT_CHARS = int(os.getenv("MAX_TOOL_RESULT_CHARS", "50000"))

# ── Subagents ────────────────────────────────────────────────────────────────
# The `task`/`explore` tools still exist (the agent is a full coding agent), but
# there is no hierarchy to configure: one level, read-only, cheap model.
SUBAGENT_MAX_ROUNDS = int(os.getenv("SUBAGENT_MAX_ROUNDS", "20"))
SUBAGENT_TIMEOUT = int(os.getenv("SUBAGENT_TIMEOUT", "600"))
SUBAGENT_EFFORT = os.getenv("SUBAGENT_EFFORT", "low")

# ── webfetch ─────────────────────────────────────────────────────────────────
WEBFETCH_TIMEOUT = int(os.getenv("WEBFETCH_TIMEOUT", "30"))
WEBFETCH_MAX_BYTES = int(os.getenv("WEBFETCH_MAX_BYTES", "5000000"))
# Block requests to the local machine and private networks by default.
WEBFETCH_ALLOW_PRIVATE = os.getenv("WEBFETCH_ALLOW_PRIVATE", "0") == "1"

# ── Vision ──────────────────────────────────────────────────────────────────
VISION_MAX_PIXELS = int(os.getenv("VISION_MAX_PIXELS", str(1600 * 1600)))

# ── Speech to text (whisper.cpp) ─────────────────────────────────────────────
WHISPER_BIN = os.getenv("WHISPER_BIN") or shutil.which("whisper-cli") or shutil.which("whisper")


def _find_whisper_model() -> str:
    if os.getenv("WHISPER_MODEL"):
        return os.getenv("WHISPER_MODEL", "")
    candidates = [
        Path.home() / "opt/whisper.cpp/models/ggml-base.en.bin",
        Path.home() / "models/stt/ggml-base.en.bin",
        Path.home() / "opt/whisper.cpp/models/ggml-tiny.en.bin",
        Path.home() / "models/stt/ggml-tiny.en-q8_0.bin",
        Path.home() / "models/stt/ggml-tiny.en-q4_1.bin",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return ""


_whisper_model = _find_whisper_model()


def whisper_model() -> str:
    return _whisper_model


def set_whisper_model(value: str) -> None:
    global _whisper_model
    _whisper_model = (value or "").strip() or _find_whisper_model()


FFMPEG_BIN = os.getenv("FFMPEG_BIN") or shutil.which("ffmpeg")
WHISPER_SERVER_BIN = os.getenv("WHISPER_SERVER_BIN") or shutil.which("whisper-server")
WHISPER_SERVER_PORT = int(os.getenv("WHISPER_SERVER_PORT", "8177"))


def stt_available() -> bool:
    return bool(WHISPER_BIN and whisper_model() and FFMPEG_BIN)


def whisper_streaming_available() -> bool:
    return bool(WHISPER_SERVER_BIN and whisper_model())


# ── Text to speech (Kokoro) ──────────────────────────────────────────────────
def _find_tts_model() -> str:
    if os.getenv("TTS_MODEL"):
        return os.getenv("TTS_MODEL", "")
    candidates = [
        Path.home() / "models/tts/kokoro-v1.0.onnx",
        Path.home() / "models/tts/kokoro-v1.0.fp16.onnx",
        Path.home() / "models/tts/kokoro-v1.0.int8.onnx",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return ""


def _find_tts_voices() -> str:
    if os.getenv("TTS_VOICES"):
        return os.getenv("TTS_VOICES", "")
    path = Path.home() / "models/tts/voices-v1.0.bin"
    return str(path) if path.exists() else ""


TTS_MODEL = _find_tts_model()
TTS_VOICES = _find_tts_voices()
TTS_DEFAULT_VOICE = os.getenv("TTS_DEFAULT_VOICE", "af_aoede")
