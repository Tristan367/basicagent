"""Text-to-speech via Kokoro, running on the CPU through onnxruntime.

Deliberately off the GPU. Read-aloud runs constantly for a user who works by
ear, so it must not compete for the GPU with anything the user is building; an
82M model on a normal CPU renders comfortably ahead of playback.

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
        "voice_choices": voice_choices(),
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


# The prefix on a Kokoro voice id is not decoration: `af` is American female,
# `bm` British male. Shown as a name and an accent, because "af_aoede" tells a
# user nothing and a screen reader pronounces it as letters.
_ACCENTS = {"a": "American", "b": "British"}
_GENDERS = {"f": "female", "m": "male"}


def voice_label(voice: str) -> str:
    """"af_aoede" -> "Aoede (American, female)"."""
    prefix, _, name = (voice or "").partition("_")
    pretty = " ".join(part.capitalize() for part in name.replace("_", " ").split()) or voice
    if len(prefix) == 2 and prefix[0] in _ACCENTS and prefix[1] in _GENDERS:
        return f"{pretty} ({_ACCENTS[prefix[0]]}, {_GENDERS[prefix[1]]})"
    return pretty or voice


def voice_choices() -> list[tuple[str, str]]:
    """`(id, label)` for every offerable voice, ordered the way they read."""
    return sorted(((v, voice_label(v)) for v in voices()), key=lambda pair: pair[1])


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
            "text-to-speech unavailable: the read-aloud voices are not installed. "
            "Install them with `python -m agent_server.downloads read-aloud`, or "
            "point TTS_MODEL and TTS_VOICES at your own copies."
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
# `*` and `~~` emphasis is unambiguous. `_` is not: it is also the joint in
# every snake_case name a coding assistant writes, and treating those as
# italics silently deleted them -- "agent_server/tts.py and web_ui/app.js" was
# read out as "agentserver/tts.py and webui/app.js", and MAX_RETRY_COUNT as
# MAXRETRYCOUNT. Underscore emphasis therefore requires a non-word character on
# each side, which is what CommonMark says anyway.
_EMPHASIS = re.compile(r"(\*\*|\*|~~)(?=\S)(.+?)(?<=\S)\1", re.S)
_EMPHASIS_UNDER = re.compile(r"(?<![\w`])(__|_)(?=\S)(.+?)(?<=\S)\1(?![\w`])", re.S)

# Anything whose only job is to be looked at. Pictographs, dingbats, flags,
# skin tones, the joiners that glue them together, and the variation selector
# that turns a plain glyph into a coloured one. A screen reader announces these
# by name, at length, which is its own decision to make; read aloud in the
# middle of a sentence they are just noise.
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF"     # pictographs, emoticons, transport, symbols
    "\U0001F1E6-\U0001F1FF"      # regional indicators (flags)
    "\u2600-\u27BF"             # misc symbols and dingbats: ✅ ✨ ✔ ➜
    "\u2B00-\u2BFF"             # arrows and stars used as emoji
    "\uFE00-\uFE0F"             # variation selectors
    "\u200D"                     # zero-width joiner
    "\u20E3]+"                   # keycap combiner
)
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
            out[-1] = _ended(out[-1]) + " " + line.strip()
        fresh = False
    # And the last line of every block, for the same reason: a bullet or a
    # heading that stops without punctuation is read with the intonation of a
    # sentence still going, straight into the next one.
    return [_ended(b) for b in out]


# Everything that already tells a reader to pause. A colon and a semicolon
# count -- they become full stops further down the pipeline -- and so does a
# comma, which is a breath rather than a stop but is a mark the writer chose.
_PAUSE_MARKS = (".", "!", "?", ":", ";", ",")


def _ended(line: str) -> str:
    """The line with a full stop added, unless it already pauses."""
    stripped = line.rstrip()
    if not stripped or stripped.endswith(_PAUSE_MARKS):
        return stripped
    # A closing quote or bracket after the mark still counts: `("like this.")`
    if stripped.rstrip("\"')]\u201d\u2019").endswith(_PAUSE_MARKS):
        return stripped
    return stripped + "."


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
    text = _EMOJI.sub("", text)
    text = _EMPHASIS.sub(r"\2", text)
    text = _EMPHASIS_UNDER.sub(r"\2", text)
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

# How a file extension is actually said out loud, which is not a rule you can
# derive -- ".py" is "pie" but ".js" is "J S", and both are simply what people
# say. Without this the phonemiser guesses, and it guesses badly: ".js" comes
# out "jiss".
#
# Only the ones that are said as letters, or as a word other than they look,
# are listed. Anything absent falls through to the generic rule below and is
# read as written, which is already right for ".json" and ".java".
_EXTENSIONS = {
    "js": "J S", "jsx": "J S X", "ts": "T S", "tsx": "T S X", "mjs": "M J S",
    "py": "pie", "rb": "R B", "rs": "R S", "go": "go", "php": "P H P",
    "cs": "C sharp", "cpp": "C plus plus", "cc": "C C", "hpp": "H P P",
    "sh": "S H", "bash": "bash", "ps1": "P S 1",
    "md": "M D", "rst": "R S T", "txt": "text", "csv": "C S V", "tsv": "T S V",
    "html": "H T M L", "htm": "H T M", "css": "C S S", "scss": "S C S S",
    "xml": "X M L", "yml": "yaml", "sql": "sequel", "ini": "I N I",
    "png": "P N G", "jpg": "J peg", "jpeg": "J peg", "gif": "gif", "svg": "S V G",
    "pdf": "P D F", "zip": "zip", "wav": "wav", "mp3": "M P 3", "mp4": "M P 4",
    "db": "D B", "log": "log", "env": "env", "cfg": "config", "conf": "config",
}

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

    # A dot between two words is part of a name, and a name is what most of a
    # reply in this app is about: main.py, app.js, example.com. Left alone the
    # phonemiser runs the two halves together into one unsayable word.
    #
    # After the decimal rule, which has already spent the dots between digits,
    # and after the abbreviations, which are the other dots that are not this.
    (re.compile(r"\.(" + "|".join(sorted(_EXTENSIONS, key=len, reverse=True)) + r")\b", re.I),
     lambda m: " dot " + _EXTENSIONS[m.group(1).lower()]),
    (re.compile(r"(?<=\w)\.(?=[A-Za-z][\w-]{0,11}\b)"), " dot "),

    (re.compile(r"~\s*(?=\d)"), "about "),
    (re.compile(r"#(?=\d)"), "number "),
    (re.compile(r"(\d)\s*[x\u00d7]\b"), r"\1 times"),
    (re.compile(r"\b(\d+)\s*/\s*(" + _UNITS + r")s?\b", re.I), r"\1 per \2"),
    (re.compile(r"(\d)\s*/\s*(\d)"), r"\1 \2"),
    (re.compile(r"(\d)\s*-\s*(?=\d)"), r"\1 to "),
    (re.compile(r"(\d)\s*%"), r"\1 percent"),
    (re.compile(r"(?<=[\ds])/M\b"), " per million"),

    # A file reference the way this app writes them: `src/app.js:120-140`. The
    # colon is the one below that would otherwise become a full stop, so it is
    # spent here on something more useful.
    (re.compile(r"(?<=[A-Za-z]):\s*(\d+ to \d+)\b"), r", lines \1"),
    (re.compile(r"(?<=[A-Za-z]):\s*(\d+)\b"), r", line \1"),

    # Identifiers. An underscore is a word joint, not a thing to say, and a
    # camelCase hump is a word boundary nobody can hear.
    (re.compile(r"(?<=\w)_(?=\w)"), " "),
    (re.compile(r"(?<=[a-z0-9])(?=[A-Z][a-z])"), " "),

    # Paths. Said aloud, a separator is "slash" -- dropping it turns
    # "src/app.js" into something indistinguishable from ordinary prose.
    (re.compile(r"/\.(?=[A-Za-z])"), "/dot "),
    (re.compile(r"~/"), "home/"),
    (re.compile(r"(?<=[\w.])/(?=[\w.])"), " slash "),

    (re.compile(r"(?<=\w)@(?=\w)"), " at "),
    (re.compile(r"[^\S\n]*!=[^\S\n]*"), " is not "),
    (re.compile(r"[^\S\n]*<=[^\S\n]*"), " is at most "),
    (re.compile(r"[^\S\n]*>=[^\S\n]*"), " is at least "),
    (re.compile(r"[^\S\n]*(?:->|=>)[^\S\n]*"), " becomes "),
    (re.compile(r"(?<![!<>=])[^\S\n]*={1,2}[^\S\n]*(?=[\w\"'])"), " equals "),
    (re.compile(r"\s*&&\s*"), " and "),
    (re.compile(r"\s*\|\|\s*"), " or "),

    # A hash, a token, a commit id. Forty characters read out one at a time is
    # a minute of someone's life and tells them nothing.
    (re.compile(r"\b(?=[0-9a-f]{12,}\b)[0-9a-f]*[0-9][0-9a-f]*\b", re.I), "a long code"),

    # Horizontal whitespace only in these: \s would swallow the blank lines
    # that separate one list item from the next, welding the whole reply into
    # a single breathless block.
    #
    # A dash between clauses gets a full stop rather than a comma. It is doing
    # the job of one -- the sentence restarts after it -- and a comma there is
    # read as a breath rather than a stop.
    (re.compile(r"[^\S\n]*[\u2014\u2013][^\S\n]*"), ". "),
    (re.compile(r"[^\S\n]*--[^\S\n]*"), ". "),
    (re.compile(r"[^\S\n]*\u2192[^\S\n]*"), " becomes "),
    (re.compile(r"[^\S\n]*&[^\S\n]*"), " and "),

    # A colon and a semicolon are both full stops to the ear: what follows is
    # a new thought, and neither mark produces any pause on its own. Not
    # between digits, which is a time.
    (re.compile(r"(?<!\d):+(?!\d)[^\S\n]*"), ". "),
    (re.compile(r"[^\S\n]*;[^\S\n]*"), ". "),
    (re.compile(r"\u2026|\.\.\."), "."),
    (re.compile(r"([.!?])\1+"), r"\1"),
    (re.compile(r"[^\S\n]{2,}"), " "),
]


_AFTER_STOP = re.compile(r"([.!?]\s+)([a-z])")


def normalise(text: str) -> str:
    """Rewrite symbols and shorthand into what they are meant to sound like."""
    for pattern, repl in _SPOKEN:
        text = pattern.sub(repl, text)
    # The full stops added above land mid-sentence, where the next word is
    # lowercase -- "here's the thing: it works" becomes "the thing. it works".
    # The splitter below only recognises a sentence that starts like one, so
    # without this the two halves stay welded into a single utterance and the
    # pause that was just bought is never spent.
    return _AFTER_STOP.sub(lambda m: m.group(1) + m.group(2).upper(), text)


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


# ── Language a child should not hear read back ──────────────────────────────
#
# This runs only in child mode, and only on the way to the voice. It is not a
# guard against the assistant swearing -- it has been told not to and will not.
# It is for the thing a child *will* try: typing a word and pressing play to
# hear the app say it. The voice simply skips it.
#
# Word boundaries are load-bearing, not tidiness. A child doing schoolwork says
# class, pass, assignment, assassin, grape, Uranus, cockatoo, Scunthorpe,
# analysis, and a substring match mangles every one of them. Anything added
# here belongs in the "must survive" list in the tests as well.
_PROFANITY = (
    "fuck", "fucking", "fucked", "fucker", "fuckers", "motherfucker", "motherfuckers",
    "shit", "shits", "shitty", "shitting", "bullshit", "shithead",
    "bitch", "bitches", "bitching",
    "cunt", "cunts",
    "dick", "dicks", "dickhead", "cock", "cocks", "prick", "pricks",
    "asshole", "assholes", "arsehole", "arseholes", "arse", "arses",
    "bastard", "bastards", "damn", "damned", "goddamn", "goddamned",
    "piss", "pissed", "pissing", "crap", "crappy",
    "slut", "sluts", "whore", "whores", "wanker", "wankers", "twat", "twats",
    "bollocks", "bugger", "buggers", "nigger", "niggers", "faggot", "faggots",
    "retard", "retards", "retarded",
)

# `ass` and `hell` are handled apart from the list above because both are
# ordinary words in the surroundings a child meets them in -- an ass is a
# donkey in every fable ever written, and hell appears in half of English
# literature. Only the insults are caught.
_EXTRA_PROFANITY = (
    r"\bass(?=\s*(?:hole|hat|wipe)\b)",
    r"\bwhat the hell\b",
    r"\bhell no\b",
    r"\bgo to hell\b",
)

_SWEARING = re.compile(
    "|".join([rf"\b(?:{'|'.join(sorted(_PROFANITY, key=len, reverse=True))})\b", *_EXTRA_PROFANITY]),
    re.IGNORECASE,
)
# Left behind after a word is lifted out: a doubled space, or a space pushed up
# against the punctuation that followed the word.
_GAP = re.compile(r"[ \t]{2,}")
_ORPHANED = re.compile(r"\s+([,.;:!?])")
# "Is it broken, damn?" loses the word and is left holding ",?" -- a comma the
# sentence no longer needs, right where the voice wants a clean ending.
_DANGLING = re.compile(r"[,;:]+(?=\s*[.!?])")
# The same at either end: "damn, go away" would otherwise open on a comma.
_EDGES = re.compile(r"^[\s,;:]+|[\s,;:]+$")


def without_swearing(text: str) -> str:
    """Take the swearing out, leaving a sentence that still reads as one.

    Removed rather than bleeped or replaced with a marker: a beep is a reward,
    and a child testing whether they can make the app swear has found out that
    they nearly can. Silence is a duller answer and a truer one.

    The punctuation stays. "Go away, damn it." keeps its full stop, so the
    voice still lands the end of the sentence instead of running into the next.
    """
    if not text:
        return text
    cleaned = _SWEARING.sub("", text)
    if cleaned == text:
        return text
    cleaned = _ORPHANED.sub(r"\1", _GAP.sub(" ", cleaned))
    cleaned = _DANGLING.sub("", cleaned)
    # Trailing punctuation that actually ends the sentence is kept; only the
    # commas left hanging by the removal are trimmed.
    tail = ""
    while cleaned and cleaned[-1] in ".!?":
        tail = cleaned[-1] + tail
        cleaned = cleaned[:-1]
    return (_EDGES.sub("", cleaned) + tail).strip()


def plan(text: str, clean: bool = False) -> list[str]:
    """The sentences to speak. `clean` takes out swearing first, for child mode."""
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
    if clean:
        text = without_swearing(text)
    spoken = sentences(normalise(to_prose(text)))
    # A line that was nothing but swearing is now nothing at all, and a spoken
    # chunk with no letters in it renders as a click.
    return [s for s in spoken if any(c.isalnum() for c in s)]


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
