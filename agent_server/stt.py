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
        "loaded": _model is not None,
    }


# ── The model ────────────────────────────────────────────────────────────────

_model = None
_model_size = ""
_lock = asyncio.Lock()


async def get_model():
    """The loaded model, loading (and on first run downloading) it if needed.

    Shared with `whisper_streaming`: a second copy would double the memory for
    no benefit, and the two are never used for different sizes at once.
    """
    global _model, _model_size
    size = whisper_size()
    if _model is not None and _model_size == size:
        return _model
    async with _lock:
        if _model is None or _model_size != size:
            if not stt_available():
                raise STTError(
                    "Dictation is not installed. Run: pip install faster-whisper"
                )
            from faster_whisper import WhisperModel

            _model = await asyncio.to_thread(
                WhisperModel, size, device="cpu", compute_type=FASTER_WHISPER_COMPUTE
            )
            _model_size = size
            log.info("dictation model ready (%s)", size)
    return _model


async def warmup() -> None:
    """Load at startup so the user's first sentence isn't slow.

    Never fatal: the first run needs the network to fetch the model, and the app
    has to start without one.
    """
    if not stt_available():
        return
    try:
        await get_model()
    except Exception:
        log.warning("dictation warm-up failed", exc_info=True)


async def reload_model() -> None:
    """Drop the loaded model so the next use picks up a newly chosen size."""
    global _model, _model_size
    async with _lock:
        _model = None
        _model_size = ""


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
