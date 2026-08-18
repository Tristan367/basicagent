"""Streaming dictation backed by whisper-server (whisper.cpp).

Whisper's architecture is non-streaming, so this is a sliding re-transcription:
the accumulated audio is re-transcribed every couple of seconds and the result
is pushed out as a partial. whisper runs far faster than realtime (especially on
the GPU), so the re-transcription keeps up.

To stop latency growing with a long utterance, the buffer is bounded the way
whisper.cpp's own stream example bounds it: audio older than a fixed window is
finalised and trimmed, so each re-transcription only covers the recent tail.
The cut uses whisper's segment timestamps, and enough trailing audio is kept
under review that whisper can still revise the last sentence or two.
"""

from __future__ import annotations

import asyncio
import io
import os
import re
import wave

import httpx
import numpy as np

from agent_server.config import (
    WHISPER_SERVER_BIN,
    WHISPER_SERVER_PORT,
    whisper_model,
    whisper_streaming_available,
)

SAMPLE_RATE = 16000
# Re-transcribe only once this much NEW audio has accumulated. Kept low so the
# first words appear quickly instead of waiting for a whole sentence.
STEP_SECONDS = 1.0
# A pause this long (with speech before it) commits the sentence and adds a
# period, so dictation can stay on and each burst of talking becomes a sentence.
PAUSE_SECONDS = float(os.getenv("CODEAGENT_DICTATION_PAUSE", "10"))
# Audio older than this is committed and trimmed, so the re-transcription only
# ever covers the recent tail. Sized to keep the last couple of sentences under
# review, since whisper sometimes revises them when more context arrives. Ten
# seconds matches whisper.cpp's own stream example window (--length 10000).
COMMIT_DELAY_SEC = float(os.getenv("CODEAGENT_DICTATION_COMMIT_DELAY", "10"))

# whisper emits these for silence/background noise; they read as garbage in the
# transcript. The first arm catches the special-event tokens ([BLANK_AUDIO],
# [MUSIC], [INAUDIBLE], and the lowercase [silence]); the second catches sound
# descriptions such as "(wind blowing)" and "(music)".
_NOISE = re.compile(
    r"\[[A-Za-z_ ]+\]"         # whisper's event tokens, upper- or lowercase
    r"|\([a-z]+(?: [a-z]+)*\)"  # lowercase sound descriptions
)
# A standalone "--" is whisper's way of writing an em-dash; dictation doesn't
# want it. Word-boundary anchored so a flag like --help survives.
_DASH = re.compile(r"(?<!\S)--(?!\S)")
# whisper sometimes runs a sentence straight into the next ("done.Next").
_MISSING_SPACE = re.compile(r"([.!?])([A-Z])")
# whisper sometimes leaves a space before punctuation ("even ?").
_SPACE_PUNCT = re.compile(r"\s+([,.;:!?])")


def _clean(text: str) -> str:
    text = _NOISE.sub(" ", text)
    text = _DASH.sub(" ", text)
    text = _MISSING_SPACE.sub(r"\1 \2", text)
    text = _SPACE_PUNCT.sub(r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


class WhisperStreamingError(RuntimeError):
    pass


class WhisperServer:
    """A persistent whisper-server process with the model already loaded."""

    def __init__(self) -> None:
        self.proc: asyncio.subprocess.Process | None = None
        self.client: httpx.AsyncClient | None = None
        self.url = f"http://127.0.0.1:{WHISPER_SERVER_PORT}/inference"

    async def start(self) -> None:
        if self.proc is not None:
            return
        if not whisper_streaming_available():
            raise WhisperStreamingError("whisper-server is not installed")
        self.proc = await asyncio.create_subprocess_exec(
            WHISPER_SERVER_BIN,
            "-m", whisper_model(),
            "--host", "127.0.0.1",
            "--port", str(WHISPER_SERVER_PORT),
            "-l", "en",
            # Skip the language-probability pass: it re-runs detection on every
            # request and doubles transcription time for a field we never read.
            "-nlp",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        # Wait for the HTTP server to answer (the model loads in a few seconds).
        async with httpx.AsyncClient() as probe:
            for _ in range(150):
                if self.proc.returncode is not None:
                    raise WhisperStreamingError("whisper-server exited during startup")
                try:
                    # Any HTTP status means the socket is up; GET is 404 by design.
                    await probe.get(self.url, timeout=0.5)
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(0.2)
            else:
                raise WhisperStreamingError("whisper-server did not become ready")
        self.client = httpx.AsyncClient(timeout=60)

    async def transcribe(self, wav_bytes: bytes) -> tuple[str, list[dict]]:
        """Return ``(cleaned_text, segments)`` for one wav.

        ``segments`` is a list of ``{"start", "end", "text"}`` with times in
        seconds, from whisper's ``verbose_json`` output -- used to cut the audio
        buffer at a word/sentence boundary when committing.
        """
        if self.client is None:
            await self.start()
        assert self.client is not None
        resp = await self.client.post(
            self.url,
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data={"response_format": "verbose_json", "temperature": "0.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        text = _clean(" ".join(str(data.get("text", "")).split()))
        segments = []
        for seg in data.get("segments", []):
            try:
                start = float(seg.get("start", 0.0))
                end = float(seg.get("end", 0.0))
            except (TypeError, ValueError):
                continue
            segments.append(
                {"start": start, "end": end, "text": _clean(str(seg.get("text", "")))}
            )
        return text, segments

    async def shutdown(self) -> None:
        if self.client is not None:
            await self.client.aclose()
            self.client = None
        if self.proc is not None and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except TimeoutError:
                self.proc.kill()
        self.proc = None


_server: WhisperServer | None = None
_lock = asyncio.Lock()


async def get_server() -> WhisperServer:
    global _server
    if _server is None:
        async with _lock:
            if _server is None:
                _server = WhisperServer()
                await _server.start()
    return _server


async def shutdown() -> None:
    global _server
    if _server is not None:
        await _server.shutdown()
        _server = None


async def restart() -> None:
    """Stop the server so the next session starts with the current model."""
    await shutdown()


class WhisperSession:
    """One continuous dictation session.

    Re-transcribes the in-progress audio every couple of seconds for live
    feedback, and -- so the user can leave dictation on and just talk -- detects
    a long pause and commits the speech before it as a finished sentence (adding
    a period if whisper did not). Committed sentences are dropped from the audio
    buffer, so the re-transcription never grows without bound.
    """

    def __init__(self, server: WhisperServer) -> None:
        self.server = server
        self._buf = bytearray()          # audio since the last committed sentence
        self._finalized: list[str] = []  # committed sentences
        self._silence = 0.0              # seconds of trailing silence
        self._speech = False             # whether the current buffer has speech
        self._peak = 0.0                 # slowly-decaying recent RMS
        self._last_transcribed = 0
        self.busy = False

    def append(self, samples: np.ndarray) -> None:
        self._buf.extend(samples.astype(np.float32).tobytes())
        if not samples.size:
            return
        rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
        # Adaptive silence: anything well under the recent speech level counts as
        # a pause, with a floor so a genuinely quiet mic still has a threshold.
        self._peak = max(self._peak * 0.999, rms)
        if rms < max(0.008, self._peak * 0.2):
            self._silence += samples.size / SAMPLE_RATE
        else:
            self._silence = 0.0
            self._speech = True

    @property
    def new_seconds(self) -> float:
        return (len(self._buf) // 4 - self._last_transcribed) / SAMPLE_RATE

    @property
    def should_finalize(self) -> bool:
        return self._speech and self._silence >= PAUSE_SECONDS

    def finalized_text(self) -> str:
        return " ".join(self._finalized)

    def _to_wav(self) -> bytes:
        samples = np.frombuffer(self._buf, dtype=np.float32)
        pcm16 = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm16.tobytes())
        return out.getvalue()

    async def current_partial(self) -> str:
        """Transcribe the in-progress buffer; empty if nothing has been said.

        Finalises and trims any audio old enough to be stable, so the buffer
        never grows past the review window and each transcription stays fast.
        Returns only the text still under review.
        """
        if not self._speech:
            return ""
        text, segments = await self.server.transcribe(self._to_wav())
        buf_sec = len(self._buf) // 4 / SAMPLE_RATE
        cutoff = buf_sec - COMMIT_DELAY_SEC
        commit_idx = 0
        for i, seg in enumerate(segments):
            if seg["end"] <= cutoff:
                commit_idx = i + 1
            else:
                break
        if commit_idx:
            committed = [s["text"] for s in segments[:commit_idx] if s["text"]]
            if committed:
                self._finalized.append(" ".join(committed))
            trim = min(int(segments[commit_idx - 1]["end"] * SAMPLE_RATE), len(self._buf) // 4)
            if trim > 0:
                del self._buf[: trim * 4]
            remaining = " ".join(s["text"] for s in segments[commit_idx:] if s["text"])
        else:
            remaining = text
        self._last_transcribed = len(self._buf) // 4
        return remaining

    @staticmethod
    def _ensure_period(text: str) -> str:
        text = text.strip()
        if text and text[-1] not in ".!?":
            text += "."
        return text

    async def commit_pause(self) -> bool:
        """Finalize the buffer as a finished sentence and reset it."""
        text = self._ensure_period(await self.current_partial())
        if text:
            self._finalized.append(text)
        self._reset()
        return bool(text)

    async def finalize(self) -> str:
        """Commit whatever remains and return the whole utterance."""
        text = self._ensure_period(await self.current_partial())
        if text:
            self._finalized.append(text)
        self._reset()
        return self.finalized_text()

    def _reset(self) -> None:
        self._buf = bytearray()
        self._silence = 0.0
        self._speech = False
        self._peak = 0.0
        self._last_transcribed = 0
