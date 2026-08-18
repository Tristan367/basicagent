"""Text-to-speech via Kokoro, running on the CPU through onnxruntime.

Deliberately off the GPU. The whole point of a local voice here is that it costs
nothing the game work needs, so the 3060 is never touched; an 82M model on
sixteen cores runs comfortably ahead of playback.

The model is loaded once and kept. Loading costs about half a second, which is
tolerable on the first play of a session and intolerable on every chunk of a
streamed one.
"""

import asyncio
import io
import re

import numpy as np
import soundfile as sf

from agent_server.config import TTS_DEFAULT_VOICE, TTS_MODEL, TTS_VOICES

# English only. The model ships 54 voices across eight languages, and offering
# a Mandarin voice for an English transcript is a trap rather than a feature.
ENGLISH_PREFIXES = ("af_", "am_", "bf_", "bm_")

MAX_TEXT_CHARS = 40_000
SPEED_RANGE = (0.5, 2.0)

_kokoro = None
_load_lock = asyncio.Lock()


class TTSError(RuntimeError):
    pass


def availability() -> dict:
    return {
        "available": bool(TTS_MODEL and TTS_VOICES),
        "model": TTS_MODEL.rsplit("/", 1)[-1] if TTS_MODEL else "",
        "voices": voices(),
        "default_voice": TTS_DEFAULT_VOICE,
    }


async def warmup() -> None:
    """Load the model at startup so the first reply speaks instantly.

    Kokoro's first load takes about half a second; doing it here means the user
    never feels it. A no-op when the models are not installed.
    """
    if not (TTS_MODEL and TTS_VOICES):
        return
    try:
        await _model()
    except TTSError:
        pass


def voices() -> list[str]:
    if not _kokoro:
        # Known ahead of loading the model, so the settings page can render
        # without paying for a model load it may never use.
        return sorted(_STATIC_VOICES)
    return sorted(v for v in _kokoro.get_voices() if v.startswith(ENGLISH_PREFIXES))


_STATIC_VOICES = [
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore",
    "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael",
    "am_onyx", "am_puck", "am_santa",
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
]


async def _model():
    global _kokoro
    if _kokoro is not None:
        return _kokoro
    if not (TTS_MODEL and TTS_VOICES):
        raise TTSError(
            "text-to-speech unavailable: no Kokoro model found. Put "
            "kokoro-v1.0.onnx and voices-v1.0.bin in ~/models/tts, or set "
            "TTS_MODEL and TTS_VOICES."
        )
    async with _load_lock:
        if _kokoro is None:  # another request may have won the race
            from kokoro_onnx import Kokoro
            _kokoro = await asyncio.to_thread(Kokoro, TTS_MODEL, TTS_VOICES)
    return _kokoro


# ── Markdown to something worth hearing ─────────────────────────────────────

_FENCED = re.compile(r"^[ \t]*(```|~~~).*?(^[ \t]*\1[ \t]*$|\Z)", re.S | re.M)
_TABLE_ROW = re.compile(r"^[ \t]*\|.*$", re.M)
_RULE = re.compile(r"^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$", re.M)
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]*", re.M)
_QUOTE = re.compile(r"^[ \t]*>[ \t]?", re.M)
_BULLET = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+")
_IS_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]+")
_EMPHASIS = re.compile(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1", re.S)
_INLINE_CODE = re.compile(r"`([^`]+)`")
_BARE_URL = re.compile(r"<?https?://\S+>?")
_BLANKS = re.compile(r"\n{3,}")


def _blocks(text: str) -> list[str]:
    """Group lines into blocks, undoing soft wrapping.

    Only a blank line, a heading or a list marker really starts something new;
    everything else is joined back onto the line above. When two lines are
    joined, the break between them is spoken as a period unless the earlier
    line already ends with sentence punctuation, so a run of bare lines is read
    with a pause after each one instead of as one breathless run-on.
    """
    out: list[str] = []
    fresh = True
    for line in text.split("\n"):
        if not line.strip():
            fresh = True
            continue
        starts_block = bool(_BULLET.match(line) or _IS_HEADING.match(line))
        line = _BULLET.sub("", line, count=1)
        if fresh or starts_block or not out:
            out.append(line.strip())
        else:
            if not out[-1].rstrip().endswith((".", "!", "?")):
                out[-1] += "."
            out[-1] += " " + line.strip()
        fresh = False
    return out


def to_prose(text: str) -> str:
    """Strip the parts of a reply that are read rather than listened to.

    Fenced code goes entirely and silently: hearing a diff is useless, and
    announcing every block interrupts the sentence it sits inside. Inline code
    keeps its text but loses the backticks, because an identifier mid-sentence
    usually is the sentence.
    """
    text = _FENCED.sub("\n\n", text)
    text = _TABLE_ROW.sub("", text)
    text = _RULE.sub("", text)
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _BARE_URL.sub("a link", text)
    text = _QUOTE.sub("", text)
    text = _EMPHASIS.sub(r"\2", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = "\n\n".join(_blocks(text))
    text = _HEADING.sub("", text)
    return _BLANKS.sub("\n\n", text).strip()


# ── Saying it the way a person would ────────────────────────────────────────

# espeak reads a surprising amount of ordinary technical prose literally. These
# were all confirmed against the phonemiser rather than guessed at: "3.3" came
# out as "three. three" with a full stop in the middle, "vs" was spelled V-S,
# and "~" was pronounced "tilde".
_UNITS = r"(?:day|week|month|year|hour|minute|second|turn|round|level|cast|use|kill|rest)"

_SPOKEN = [
    # Abbreviations first: they contain the dots the decimal rule looks for.
    (re.compile(r"\be\.g\.(?=\s|$)", re.I), "for example"),
    (re.compile(r"\bi\.e\.(?=\s|$)", re.I), "that is"),
    (re.compile(r"\betc\.?(?=\s|$)", re.I), "et cetera"),
    (re.compile(r"\bvs\.?(?=\s|$)", re.I), "versus"),
    (re.compile(r"\bw/(?=\s)"), "with"),
    (re.compile(r"\baka\b", re.I), "also known as"),

    # Money before the decimal rule, so the unit lands after the number.
    (re.compile(r"\$(\d+(?:\.\d+)?)"), r"\1 dollars"),

    # "3.3" is two numbers to espeak unless the dot is spoken.
    (re.compile(r"(\d)\.(\d)"), r"\1 point \2"),

    (re.compile(r"~\s*(?=\d)"), "about "),
    (re.compile(r"#(?=\d)"), "number "),
    (re.compile(r"(\d)\s*[x\u00d7]\b"), r"\1 times"),
    (re.compile(r"\b(\d+)\s*/\s*(" + _UNITS + r")s?\b", re.I), r"\1 per \2"),
    (re.compile(r"(\d)\s*/\s*(\d)"), r"\1 \2"),
    (re.compile(r"(\d)\s*-\s*(?=\d)"), r"\1 to "),
    # Horizontal whitespace only in these: \s would swallow the blank lines
    # that separate one list item from the next, welding the whole reply into
    # a single breathless block.
    (re.compile(r"[^\S\n]*[\u2014\u2013][^\S\n]*"), ", "),   # em/en dash -> a pause
    (re.compile(r"[^\S\n]*--[^\S\n]*"), ", "),
    (re.compile(r"[^\S\n]*\u2192[^\S\n]*"), " becomes "),
    (re.compile(r"[^\S\n]*&[^\S\n]*"), " and "),
    (re.compile(r"[^\S\n]{2,}"), " "),
]


def normalise(text: str) -> str:
    """Rewrite symbols and shorthand into what they are meant to sound like."""
    for pattern, repl in _SPOKEN:
        text = pattern.sub(repl, text)
    return text


# Splitting on every full stop mangles these. Python's lookbehind must be
# fixed width, so rather than one clever pattern the dots that are not sentence
# ends are swapped for a sentinel, the split runs, and the dots come back.
_ABBREV = re.compile(
    r"\b(e\.g|i\.e|etc|vs|Mr|Mrs|Ms|Dr|Prof|St|approx|Fig|No|cf|al)\.", re.I
)
_INITIAL = re.compile(r"\b([A-Z])\.")          # J. Smith
_DECIMAL = re.compile(r"(\d)\.(?=\d)")         # 1.0, 3.88
_DOT = "\x01"

_SENTENCE_END = re.compile(
    r"([.!?]+[\"')\]]*)"        # the terminator, with any closing quote
    r"\s+"
    r"(?=[A-Z\"'(\[])"          # next sentence starts like one
)


def sentences(text: str) -> list[str]:
    """Split prose into speakable sentences.

    Paragraph and line breaks are hard boundaries regardless of punctuation,
    which is what makes a bulleted list read as separate items instead of one
    breathless run-on.
    """
    out: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        safe = _DECIMAL.sub(r"\1" + _DOT, _INITIAL.sub(r"\1" + _DOT,
                            _ABBREV.sub(r"\1" + _DOT, para)))
        parts = _SENTENCE_END.sub("\\1\x00", safe).split("\x00")
        out.extend(p.replace(_DOT, ".").strip() for p in parts if p.strip())
    return out


def plan(text: str) -> list[str]:
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
    return sentences(normalise(to_prose(text)))


# ── Synthesis ───────────────────────────────────────────────────────────────

# The native rate Kokoro renders at. We resample to this on the way out so the
# WAV header and the audio always agree; the value lives in the database as
# `tts_tone` so it could be changed without a code change.
TTS_SAMPLE_RATE = 24_000


def _resample(audio: np.ndarray, in_rate: int, out_rate: int) -> tuple[np.ndarray, int]:
    """Downsample speech to a lower sample rate.

    A windowed-sinc low-pass runs first so nothing above the new Nyquist
    frequency folds back down into the band we keep (aliasing), then the sample
    count is interpolated to the new rate.
    """
    a = np.asarray(audio, dtype=np.float32)
    if out_rate >= in_rate or a.size < 2:
        return a, in_rate
    taps = 65
    n = np.arange(taps) - (taps - 1) / 2
    fc = (out_rate / 2) / in_rate
    h = 2 * fc * np.sinc(2 * fc * n)
    h *= np.hamming(taps)
    h /= h.sum()
    a = np.convolve(a, h, mode="same").astype(np.float32)
    n_out = max(1, round(len(a) * out_rate / in_rate))
    xs = np.linspace(0, len(a) - 1, n_out)
    a = np.interp(xs, np.arange(len(a), dtype=np.float64), a).astype(np.float32)
    return a, out_rate


async def synth(text: str, voice: str = "", speed: float = 1.0, sample_rate: int | None = None) -> bytes:
    """Render one chunk to a WAV, ready to hand straight to an audio element."""
    text = text.strip()
    if not text:
        raise TTSError("nothing to speak")
    kokoro = await _model()

    known = set(kokoro.get_voices())
    if voice not in known:
        voice = TTS_DEFAULT_VOICE if TTS_DEFAULT_VOICE in known else min(known)
    speed = min(max(float(speed), SPEED_RANGE[0]), SPEED_RANGE[1])

    try:
        audio, rate = await asyncio.to_thread(
            kokoro.create, text, voice=voice, speed=speed, lang="en-us"
        )
    except Exception as e:
        raise TTSError(f"synthesis failed: {type(e).__name__}: {e}") from e

    audio = pad_edges(audio, rate)
    target = int(sample_rate) if sample_rate else TTS_SAMPLE_RATE
    audio, rate = _resample(audio, rate, target)
    return encode_wav(audio, rate)


# Kokoro starts a clip on the first phoneme with no run-up at all -- measured
# lead-in is 0ms against a 120ms tail. The browser applies a short ramp when an
# element starts playing, so that ramp lands straight on the first consonant and
# swallows it. A little silence in front moves the ramp somewhere harmless, and
# the over-long tail is trimmed to pay for it, so the gap between chunks ends up
# slightly shorter than before rather than longer.
HEAD_PAD_MS = 30
TAIL_KEEP_MS = 50


def pad_edges(audio: np.ndarray, rate: int) -> np.ndarray:
    a = np.asarray(audio, dtype=np.float32)
    if not a.size:
        return a
    env = np.abs(a)
    loud = np.nonzero(env > max(env.max() * 0.02, 1e-4))[0]
    if loud.size:
        a = a[: min(len(a), int(loud[-1]) + rate * TAIL_KEEP_MS // 1000)]
    lead = np.zeros(rate * HEAD_PAD_MS // 1000, dtype=np.float32)
    return np.concatenate([lead, a])


def encode_wav(audio: np.ndarray, rate: int) -> bytes:
    """16-bit PCM in a WAV container.

    Kokoro leaves a few dB of headroom, so there is nothing to normalise and
    doing so would only pump the level between chunks. The clamp is a guard
    against a rogue sample, not a limiter.
    """
    audio = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    buf = io.BytesIO()
    sf.write(buf, audio, rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()
