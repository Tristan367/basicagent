"""Speech to text, via faster-whisper.

One backend on every platform, on purpose. whisper.cpp is faster, but it is a
compiled binary plus an ffmpeg system package -- easy on a developer's Linux
box, awkward to impossible on a locked-down Windows laptop. Supporting both
meant the app quietly behaved differently depending on who ran it, which is the
opposite of what this app is for.

faster-whisper is a plain pip install with wheels for Linux, macOS and Windows.
It decodes the browser's WebM/Opus (or MP4, on Safari) itself, so there is no
transcode step and no temporary file, and it downloads its own model on first
use. The model is shared with `whisper_streaming`, so live dictation and
press-to-talk never load two copies.
"""

import asyncio
import io
import logging
import re

from agent_server.config import (
    FASTER_WHISPER_COMPUTE,
    WHISPER_MODEL_CHOICES,
    stt_available,
    whisper_size,
)

log = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 100 * 1024 * 1024

# whisper.cpp emits these for non-speech audio; they are noise in a text box.
_NOISE = re.compile(
    r"^\s*[\(\[\*][^)\]\*]{0,40}[\)\]\*]\s*$|^\s*(you|thanks for watching[.!]?|thank you[.!]?)\s*$",
    re.IGNORECASE,
)
# STT engines sometimes insert bracket-delimited placeholders (e.g. [BLANK AUDIO],
# [inaudible], [music]). Nobody says brackets aloud, so strip anything inside them.
_BRACKET = re.compile(r"\[[^\]]*\]")


class STTError(RuntimeError):
    pass


def availability() -> dict:
    size = whisper_size()
    return {
        "available": stt_available(),
        "model": size,
        "model_name": next(
            (m["name"] for m in WHISPER_MODEL_CHOICES if m["id"] == size), size
        ),
        "loaded": bool(_models),
        "partial_model": partial_size(),
    }


# ── The model ────────────────────────────────────────────────────────────────

# Loaded models, keyed by size. Two at most in practice: the one the user
# chose, and a quick one for live partials when their choice is slower than
# speech.
_models: dict[str, object] = {}
_lock = asyncio.Lock()

# Live dictation re-transcribes about once a second. "small" needs more like
# 1.6s a pass, so using it for the words that appear as you speak means they
# always trail you. Partials are throwaway feedback -- what matters is the text
# left in the box at the end -- so they are produced by something fast and the
# final pass uses the model the user actually chose.
PARTIAL_MODEL = "base.en"


def partial_size() -> str:
    """The model for words-as-you-speak. The chosen one if it can keep up."""
    chosen = whisper_size()
    return chosen if chosen in ("tiny.en", "base.en") else PARTIAL_MODEL


async def get_model(size: str = ""):
    """A loaded model, loading (and on first run downloading) it if needed."""
    size = size or whisper_size()
    cached = _models.get(size)
    if cached is not None:
        return cached
    async with _lock:
        if size not in _models:
            if not stt_available():
                raise STTError(
                    "Dictation is not installed. Run: pip install faster-whisper"
                )
            from faster_whisper import WhisperModel

            from agent_server.downloads import dictation_installed, whisper_dir

            # A folder if the installer mirrored it here, otherwise the bare
            # name -- which makes faster-whisper fetch it from Hugging Face,
            # the fallback for an install that could not reach the mirror.
            where = str(whisper_dir(size)) if dictation_installed(size) else size
            _models[size] = await asyncio.to_thread(
                WhisperModel, where, device="cpu", compute_type=FASTER_WHISPER_COMPUTE
            )
            log.info("dictation model ready (%s)", size)
    return _models[size]


async def warmup() -> None:
    """Load at startup so the user's first sentence isn't slow.

    Never fatal: the first run needs the network to fetch the model, and the app
    has to start without one.
    """
    if not stt_available():
        return
    for size in dict.fromkeys((whisper_size(), partial_size())):
        try:
            await get_model(size)
        except Exception:
            log.warning("dictation warm-up failed for %s", size, exc_info=True)


async def reload_model() -> None:
    """Drop loaded models so the next use picks up a newly chosen size."""
    async with _lock:
        _models.clear()


# ── Transcription ────────────────────────────────────────────────────────────

def _run(model, data: bytes) -> str:
    # Read straight from memory: faster-whisper handles the browser's container
    # itself, so there is nothing to transcode and no file to clean up.
    segments, _info = model.transcribe(
        io.BytesIO(data), language="en", beam_size=1, vad_filter=True
    )
    return " ".join(segment.text for segment in segments)


async def transcribe(audio: bytes, suffix: str = ".webm") -> str:
    if not audio:
        raise STTError("empty audio")
    if len(audio) > MAX_AUDIO_BYTES:
        raise STTError(f"audio too large ({len(audio):,} bytes)")

    try:
        model = await get_model()
        raw = await asyncio.to_thread(_run, model, audio)
    except STTError:
        raise
    except asyncio.CancelledError:
        raise
    except Exception as e:
        raise STTError(f"could not transcribe: {type(e).__name__}: {e}") from e
    return _clean(raw)


def _clean(raw: str) -> str:
    raw = _BRACKET.sub("", raw)
    lines = [ln.strip() for ln in raw.splitlines()]
    kept = [ln for ln in lines if ln and not _NOISE.match(ln)]
    text = " ".join(kept)
    text = re.sub(r"\s+", " ", text).strip()
    return "" if _NOISE.match(text) else text
