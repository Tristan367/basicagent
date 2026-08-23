"""Static configuration. Runtime-mutable settings live in the `settings` DB table.

This app is the accessibility-first sibling of a full power-user coding agent:
the same backend, one hard-coded prompt, no per-session knobs. Everything the
user would otherwise have to decide is defaulted or derived here.
"""

import os
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv

from agent_server import paths

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# The name shown in the UI. Kept in one place so a real name can be swapped in
# once it is chosen, without touching templates or the launcher.
APP_NAME = "Assistant"

# How the app identifies itself to other machines: the User-Agent it sends and
# the attribution OpenRouter shows on the user's own dashboard. Separate from
# APP_NAME because "Assistant" is a fine thing to call it on screen and a
# useless thing to see in a server log.
APP_SLUG = "BasicAgent"
APP_URL = "https://github.com/Tristan367/BasicCodingAgent"
USER_AGENT = f"Mozilla/5.0 (compatible; {APP_SLUG}/1.0)"

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


# User data lives outside the checkout, in the XDG data location. The rule is in
# `paths` rather than here because the installer needs it too, and it runs before
# this module can be imported at all -- before dotenv, before fastapi.
DATA_DIR = paths.data_dir()
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

# Screenshots written by `browser`. Their paths go into replies, where the web
# UI turns them into pictures for the user.
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
    # DeepSeek charges two rates: peak (01:00-04:00 and 06:00-10:00 UTC on
    # weekdays) and off-peak everywhere else, at exactly half. There is one
    # number here, and it is the peak one, on the same principle as the
    # OpenRouter note below -- a bill that comes in under what this app said is
    # a relief, and one that comes in over it is the thing this app must never
    # do to somebody. Off-peak, which is most of the week, the real cost is
    # half of what is shown.
    {
        "id": "deepseek-v4-pro",
        "name": "DeepSeek V4 Pro",
        "provider": "deepseek",
        "context": 1_000_000,
        "price_in_hit": 0.044,
        "price_in_miss": 1.32,
        "price_out": 3.96,
    },
    {
        "id": "deepseek-v4-flash",
        "name": "DeepSeek V4 Flash",
        "provider": "deepseek",
        "context": 1_000_000,
        "price_in_hit": 0.014,
        "price_in_miss": 0.44,
        "price_out": 1.32,
    },
    # Google, first-party. The Flash models have a free tier, which matters a
    # lot for this app's audience: someone can try the whole thing without
    # entering a payment method anywhere.
    {
        "id": "gemini-3.5-flash-lite",
        "name": "Gemini 3.5 Flash Lite",
        "provider": "gemini",
        "context": 1_000_000,
        "free_tier": True,
        "price_in_hit": 0.03,
        "price_in_miss": 0.30,
        "price_out": 2.50,
    },
    {
        "id": "gemini-3.7-flash",
        "name": "Gemini 3.7 Flash",
        "provider": "gemini",
        "context": 1_000_000,
        "free_tier": True,
        "price_in_hit": 0.075,
        "price_in_miss": 0.75,
        "price_out": 3.75,
    },
    {
        "id": "gemini-3.1-pro-preview",
        "name": "Gemini 3.1 Pro",
        "provider": "gemini",
        "context": 1_000_000,
        "price_in_hit": 0.20,
        "price_in_miss": 2.00,
        "price_out": 12.00,
    },
    # OpenRouter is the "one key, everything" option. Kept deliberately short:
    # models the first-party providers above do not already cover. Its cache-hit
    # price is set equal to the miss price because OpenRouter does not publish a
    # single cached rate per model -- guessing low would understate the bill,
    # and that is the one direction this app must never be wrong in.
    {
        "id": "meta-llama/llama-4-maverick",
        "name": "Llama 4 Maverick",
        "provider": "openrouter",
        "context": 1_000_000,
        "price_in_hit": 0.20,
        "price_in_miss": 0.20,
        "price_out": 0.80,
    },
    {
        "id": "openai/gpt-5.6-luna",
        "name": "GPT-5.6 Luna",
        "provider": "openrouter",
        "context": 1_000_000,
        "price_in_hit": 0.20,
        "price_in_miss": 0.20,
        "price_out": 1.20,
    },
    {
        "id": "openai/gpt-5-mini",
        "name": "GPT-5 Mini",
        "provider": "openrouter",
        "context": 400_000,
        "price_in_hit": 0.25,
        "price_in_miss": 0.25,
        "price_out": 2.00,
    },
    {
        "id": "x-ai/grok-4.3",
        "name": "Grok 4.3",
        "provider": "openrouter",
        "context": 1_000_000,
        "price_in_hit": 1.25,
        "price_in_miss": 1.25,
        "price_out": 2.50,
    },
]

MODELS_BY_ID = {m["id"]: m for m in MODELS}


# Model ids a provider advertised at startup that aren't in the curated list
# above. They never appear in the picker -- an unlabelled, unpriced id is not a
# choice a non-technical user can make -- but they are accepted if something
# else selects one, so a newly released model isn't rejected as "unknown".
DYNAMIC_MODELS: dict[str, str] = {}


def register_dynamic_models(provider: str, ids: list[str]) -> None:
    for mid in ids:
        if mid and mid not in MODELS_BY_ID:
            DYNAMIC_MODELS.setdefault(mid, provider)


def is_known_model(model_id: str) -> bool:
    return model_id in MODELS_BY_ID or model_id in DYNAMIC_MODELS


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
    if entry:
        return entry["provider"]
    # A discovered id knows its own provider. Falling straight through to
    # DEFAULT_PROVIDER used to send, say, a Gemini model id to DeepSeek.
    return DYNAMIC_MODELS.get(model_id, DEFAULT_PROVIDER)


def knows_model(model_id: str) -> bool:
    """Whether this id can be placed with a provider, rather than guessed at.

    `provider_for_model` has to return something, so an id it has never seen
    gets DEFAULT_PROVIDER. That guess is silent, and when it is wrong the
    session it was written to stops working: the next thing the user sees is
    "No API key is set up yet. Add one in Settings", which sends them to fix a
    key that was never the problem. Anything that is about to *store* a choice
    should ask this first and refuse rather than guess.
    """
    return bool(model_id) and (
        model_id.startswith("custom:")
        or model_id in MODELS_BY_ID
        or model_id in DYNAMIC_MODELS
    )


def split_custom_choice(choice: str) -> tuple[str, str]:
    """Split a custom-endpoint picker value into (endpoint key, model id).

    The picker offers plain `custom:llm1` -- the endpoint, which is as much as
    this app can choose. `custom:llm1/Qwen3-Coder` is the older form, from when
    each reported model was listed separately, and is still accepted so a
    session or a saved default from before that change keeps working.

    Endpoint names cannot contain a slash, so the first one splits it; anything
    after that belongs to the model id, which often has its own.
    """
    body = choice.removeprefix("custom:")
    name, _, model_id = body.partition("/")
    return f"custom:{name}", model_id


def resolve_model_choice(choice: str) -> tuple[str, str]:
    """Turn the model picker's value into a (provider, model) pair."""
    choice = (choice or "").strip()
    if choice.startswith("custom:"):
        # Nothing here is typed by the user. Either the endpoint named the
        # model in its own list, or it serves one and the provider asks which
        # when the request goes out.
        endpoint, model_id = split_custom_choice(choice)
        return endpoint, model_id or endpoint
    if not is_known_model(choice):
        raise ValueError(f"Unknown model: {choice}")
    return provider_for_model(choice), choice


# Compaction runs automatically when a session's live context passes this many
# tokens. There is no UI for it; the user should never have to know it happens.
#
# Only the fallback for a model nothing is known about. `compact_threshold_for`
# below works it out from the model's own numbers, because a flat figure is
# wrong in both directions: 262,144 against a million-token window compacts four
# times more often than it needs to, throwing away history and paying for
# summaries to free room that was never scarce.
COMPACT_THRESHOLD_TOKENS = int(os.getenv("COMPACT_THRESHOLD_TOKENS", "262144"))
# Above the system prompt, which is ~3,500 tokens and which compaction can
# never touch: a threshold at or below that floor is one every turn is over.
MIN_COMPACT_THRESHOLD = 16_384

# What has to still fit above the threshold when compaction fires: one more
# round. That is the model's whole output ceiling plus the tool results that
# round might return -- both of which land *after* the check has already passed.
#
# `max_output` varies more than tenfold between models, so a flat percentage is
# wrong at both ends: wasteful for a model that can only emit 8K, and unsafe for
# one that can emit 128K, where a single maximum-length reply overruns the space
# left for it.
TOOL_ROUND_HEADROOM_TOKENS = int(os.getenv("TOOL_ROUND_HEADROOM_TOKENS", "120000"))


def compact_threshold_for(model_id: str) -> int:
    """When a session on this model should be summarised.

    Everything the window holds, less the room one more round needs.
    """
    info = model_info(model_id)
    context = int(info.get("context") or 0)
    if not context:
        return COMPACT_THRESHOLD_TOKENS

    # The tool headroom is capped at a fifth of the window as well as at a fixed
    # figure. A small model cannot reserve 120,000 tokens for one round -- taken
    # literally it left a 131,072-token model compacting at 4,096, which is
    # every single turn. And never below half the window, because a threshold
    # that low is compaction as a permanent state rather than an occasional
    # event.
    tool_room = min(TOOL_ROUND_HEADROOM_TOKENS, context // 5)
    reserve = int(info.get("max_output") or DEFAULT_MAX_OUTPUT) + tool_room
    return max(MIN_COMPACT_THRESHOLD, context // 2, context - reserve)

MAX_TOOL_RESULT_CHARS = int(os.getenv("MAX_TOOL_RESULT_CHARS", "50000"))

# ── Subagents ────────────────────────────────────────────────────────────────
# The `task` tool still exists (the agent is a full coding agent), but
# there is no hierarchy to configure: one level, read-only, cheap model.
SUBAGENT_MAX_ROUNDS = int(os.getenv("SUBAGENT_MAX_ROUNDS", "20"))
SUBAGENT_TIMEOUT = int(os.getenv("SUBAGENT_TIMEOUT", "600"))
SUBAGENT_EFFORT = os.getenv("SUBAGENT_EFFORT", "low")

# ── webfetch ─────────────────────────────────────────────────────────────────
WEBFETCH_TIMEOUT = int(os.getenv("WEBFETCH_TIMEOUT", "30"))
WEBFETCH_MAX_BYTES = int(os.getenv("WEBFETCH_MAX_BYTES", "5000000"))
# Block requests to the local machine and private networks by default.
WEBFETCH_ALLOW_PRIVATE = os.getenv("WEBFETCH_ALLOW_PRIVATE", "0") == "1"

# ── Dictation (faster-whisper) ───────────────────────────────────────────────
# One backend on every platform, deliberately. whisper.cpp is faster, but it is
# a compiled binary plus an ffmpeg system package, so it is available on a
# developer's Linux box and awkward-to-impossible on a locked-down Windows
# laptop -- which meant the app behaved differently depending on who ran it.
# faster-whisper is a plain pip install with wheels for Linux, macOS and
# Windows, decodes the browser's WebM/Opus and MP4 itself, and fetches its own
# model. Same behaviour everywhere is worth more here than raw speed.
#
# "int8" keeps it quick on an ordinary CPU without a meaningful accuracy loss.
FASTER_WHISPER_COMPUTE = os.getenv("FASTER_WHISPER_COMPUTE", "int8")

# The most accurate of the three, and the default. It needs no GPU: measured on
# CPU only, per utterance, "small" costs about 2.0s limited to two threads and
# 1.4s on four. (The advice that a bigger model needs a GPU is about `medium`
# and `large`, which are five to ten times the size; `small` is 244M
# parameters.) Getting the words right first time matters more here than
# shaving a second, because the person dictating may not be able to see the
# mistake to correct it.
DEFAULT_WHISPER_MODEL = "small.en"

# Whisper pads every clip to 30 seconds internally, so cost is dominated by one
# fixed encoder pass and barely moves with how long you spoke: on two threads,
# "small" takes 1.62s for a 1.7s utterance and 2.02s for a 9.3s one. More than
# four CPU threads buys nothing.
#
# The other two exist only as an escape hatch for genuinely old hardware, which
# this app's users are more likely than most to be running. Nobody should need
# to come here: the default is the right answer on any machine from the last
# decade or so.
WHISPER_MODEL_CHOICES = [
    {
        "id": "small.en",
        "name": "Most accurate",
        "note": "The best at names, unusual words, and messy speech. Use this "
                "unless dictation feels slow.",
        "size": "about 480 MB",
    },
    {
        "id": "base.en",
        "name": "Faster",
        "note": "About three times quicker, and makes more mistakes. Worth trying "
                "on an older computer.",
        "size": "about 145 MB",
    },
    {
        "id": "tiny.en",
        "name": "Fastest",
        "note": "For a very old or very slow computer. Expect to correct it often.",
        "size": "about 75 MB",
    },
]
WHISPER_MODEL_IDS = {m["id"] for m in WHISPER_MODEL_CHOICES}

# Runtime-selected; the `settings` row wins, and this is primed from it at
# startup so the synchronous callers below do not need the database.
_whisper_size = os.getenv("FASTER_WHISPER_MODEL", DEFAULT_WHISPER_MODEL)


def whisper_size() -> str:
    return _whisper_size if _whisper_size in WHISPER_MODEL_IDS else DEFAULT_WHISPER_MODEL


def set_whisper_size(value: str) -> bool:
    """Choose the dictation model. True when it actually changed."""
    global _whisper_size
    value = (value or "").strip()
    if value not in WHISPER_MODEL_IDS or value == _whisper_size:
        return False
    _whisper_size = value
    return True


def stt_available() -> bool:
    """Whether dictation can run at all.

    The import is not attempted here -- it is heavy -- so this only reports
    whether the package is installed.
    """
    from importlib.util import find_spec

    try:
        return find_spec("faster_whisper") is not None
    except (ImportError, ValueError):
        return False


def whisper_streaming_available() -> bool:
    """Live dictation uses the same model as everything else, so if dictation
    works at all, words can appear as they are spoken."""
    return stt_available()


# ── Text to speech (Kokoro) ──────────────────────────────────────────────────
# Where the installer puts the read-aloud voices, and where older installs put
# them by hand. Both are searched, so upgrading does not mean fetching a third
# of a gigabyte again.
_TTS_DIRS = [paths.models_dir(), paths.LEGACY_TTS_DIR]


def _find_tts_model() -> str:
    # Checked for existence like the rest. Taken on trust, a stale or mistyped
    # TTS_MODEL made the app report that read-aloud was ready and then fail at
    # the moment somebody pressed play -- which is a worse outcome than saying
    # up front that it is not installed.
    override = os.getenv("TTS_MODEL", "")
    if override:
        return override if Path(override).exists() else ""
    names = ["kokoro-v1.0.onnx", "kokoro-v1.0.fp16.onnx", "kokoro-v1.0.int8.onnx"]
    for directory in _TTS_DIRS:
        for name in names:
            if (directory / name).exists():
                return str(directory / name)
    return ""


def _find_tts_voices() -> str:
    override = os.getenv("TTS_VOICES", "")
    if override:
        return override if Path(override).exists() else ""
    for directory in _TTS_DIRS:
        if (directory / "voices-v1.0.bin").exists():
            return str(directory / "voices-v1.0.bin")
    return ""


TTS_MODEL = _find_tts_model()
TTS_VOICES = _find_tts_voices()
TTS_DEFAULT_VOICE = os.getenv("TTS_DEFAULT_VOICE", "af_aoede")
