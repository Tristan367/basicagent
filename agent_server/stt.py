"""Speech-to-text via whisper.cpp.

Browser MediaRecorder produces WebM/Opus (or MP4 on Safari); whisper.cpp wants
16 kHz mono PCM WAV, so audio is transcoded with ffmpeg first. Both steps run in
a subprocess off the event loop.
"""

import asyncio
import re
import tempfile
from pathlib import Path

from agent_server.config import FFMPEG_BIN, WHISPER_BIN, stt_available, whisper_model

TRANSCODE_TIMEOUT = 60
TRANSCRIBE_TIMEOUT = 300
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
    return {
        "available": stt_available(),
        "whisper": WHISPER_BIN or "",
        "model": Path(whisper_model()).name if whisper_model() else "",
        "model_path": whisper_model(),
        "ffmpeg": FFMPEG_BIN or "",
    }


async def transcribe(audio: bytes, suffix: str = ".webm") -> str:
    if not stt_available():
        missing = [
            n for n, v in
            (("whisper-cli", WHISPER_BIN), ("whisper model", whisper_model()), ("ffmpeg", FFMPEG_BIN))
            if not v
        ]
        raise STTError(f"speech-to-text unavailable, missing: {', '.join(missing)}")
    if not audio:
        raise STTError("empty audio")
    if len(audio) > MAX_AUDIO_BYTES:
        raise STTError(f"audio too large ({len(audio):,} bytes)")

    with tempfile.TemporaryDirectory(prefix="codeagent-stt-") as tmp:
        raw = Path(tmp) / f"input{suffix or '.webm'}"
        wav = Path(tmp) / "audio.wav"
        raw.write_bytes(audio)

        await _run(
            [str(FFMPEG_BIN), "-nostdin", "-hide_banner", "-loglevel", "error",
             "-i", str(raw), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
             "-f", "wav", "-y", str(wav)],
            TRANSCODE_TIMEOUT, "ffmpeg",
        )
        if not wav.exists() or wav.stat().st_size < 1024:
            return ""

        stdout = await _run(
            [str(WHISPER_BIN), "-m", str(whisper_model()), "-f", str(wav),
             "--no-timestamps", "--no-prints", "--language", "en",
             "--threads", str(min(8, _cpu_count()))],
            TRANSCRIBE_TIMEOUT, "whisper",
        )

    return _clean(stdout)


async def _run(cmd: list[str], timeout: int, label: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        # wait_for cancelled communicate() but left the child running.
        try:
            proc.kill()
            await proc.wait()
        except (ProcessLookupError, AttributeError):
            pass
        raise STTError(f"{label} timed out after {timeout}s") from None
    except FileNotFoundError:
        raise STTError(f"{label} not found: {cmd[0]}") from None

    if proc.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise STTError(f"{label} failed: {detail[-1] if detail else f'exit {proc.returncode}'}")
    return stdout.decode("utf-8", errors="replace")


def _clean(raw: str) -> str:
    raw = _BRACKET.sub("", raw)
    lines = [ln.strip() for ln in raw.splitlines()]
    kept = [ln for ln in lines if ln and not _NOISE.match(ln)]
    text = " ".join(kept)
    text = re.sub(r"\s+", " ", text).strip()
    return "" if _NOISE.match(text) else text


def _cpu_count() -> int:
    import os

    return os.cpu_count() or 4
