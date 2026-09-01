"""Fetching the speech models, from the installer or from the running app.

Read-aloud is not a nice extra in this app -- for somebody who cannot see the
screen it is the whole interface -- and until this module existed the only
instruction anywhere was "put kokoro-v1.0.onnx and voices-v1.0.bin in
~/models/tts". Nobody this app is built for can act on that sentence, and the
first-run note that promised the assistant would sort it out was not backed by
anything except the assistant guessing a URL.

Standard library only, apart from `paths`, and nothing here needs the app to be
importable: fetching the voices has to work on an install where something else
is broken, since it is the half of the app a blind user actually meets.

Runnable on its own, which is both how the installer fetches them and how the
assistant installs them for somebody who skipped that step or whose download
failed:

    python -m agent_server.downloads read-aloud
    python -m agent_server.downloads dictation
"""

from __future__ import annotations

import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

from agent_server.paths import LEGACY_TTS_DIR, models_dir

# Kokoro's own release assets. Pinned to a release tag rather than `latest`, so
# an upstream change cannot alter what an install of this version pulls down.
KOKORO_BASE = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
)
READ_ALOUD_FILES = [
    ("kokoro-v1.0.onnx", f"{KOKORO_BASE}/kokoro-v1.0.onnx", 325_532_387),
    ("voices-v1.0.bin", f"{KOKORO_BASE}/voices-v1.0.bin", 28_214_398),
]

USER_AGENT = "BasicAgent-installer"


def read_aloud_installed() -> bool:
    return all(find_read_aloud_file(name) for name, _url, _size in READ_ALOUD_FILES)


def find_read_aloud_file(name: str) -> Path | None:
    """Where a read-aloud file is, if it is anywhere.

    The data directory is what the installer writes to now. `~/models/tts` is
    checked as well so an install from before that change keeps working without
    downloading a third of a gigabyte again.
    """
    for directory in (models_dir(), LEGACY_TTS_DIR):
        candidate = directory / name
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def _human(n: int) -> str:
    return f"{n / 1_000_000:.0f} MB"


def _download(url: str, target: Path, expected: int, say) -> None:
    """One file, to a temporary name, moved into place only once it is whole.

    A part-finished download left under the real name is worse than no download
    at all: the app finds the file, reports that read-aloud is ready, and fails
    at the moment somebody presses play.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        total = int(response.headers.get("Content-Length") or expected)
        done = 0
        last_shown = -1
        with open(partial, "wb") as out:
            while chunk := response.read(1024 * 256):
                out.write(chunk)
                done += len(chunk)
                percent = int(done * 100 / total) if total else 0
                # Only when the number actually changes, so this stays readable
                # in a log file as well as in a terminal.
                if percent != last_shown and percent % 5 == 0:
                    last_shown = percent
                    say(f"    {percent}%  ({_human(done)} of {_human(total)})")
    if total and partial.stat().st_size < total * 0.99:
        partial.unlink(missing_ok=True)
        raise OSError(f"{target.name} arrived incomplete; not installing it")
    shutil.move(str(partial), str(target))


def install_read_aloud(say=print, force: bool = False) -> bool:
    """Fetch the read-aloud voices. True if they are installed when this returns."""
    if read_aloud_installed() and not force:
        say("Read-aloud is already installed.")
        return True

    destination = models_dir()
    total = sum(size for _n, _u, size in READ_ALOUD_FILES)
    say(f"Downloading the read-aloud voices ({_human(total)}) to {destination}")
    for name, url, size in READ_ALOUD_FILES:
        if find_read_aloud_file(name) and not force:
            say(f"  {name} is already here.")
            continue
        say(f"  {name} ({_human(size)})")
        try:
            _download(url, destination / name, size, say)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            say(f"  Could not download {name}: {e}")
            say("  Read-aloud will be unavailable. Everything else works.")
            return False
    say("Read-aloud installed.")
    return True


# The dictation models, mirrored onto this project's own releases.
#
# faster-whisper fetches these from Hugging Face by itself, and that turned out
# to be a dependency worth removing. The repositories are public and ungated,
# but an anonymous download is rate-limited, and what the library reports when
# it is refused is a message about needing a token -- which is unanswerable
# advice for somebody who has never heard of Hugging Face and is halfway
# through installing a thing they were told was not technical. It is also one
# more host to be blocked by a school filter.
#
# So the same files are copied to a release here, exactly as the read-aloud
# voices already were, and fetched with the same plain download that reports
# its progress. Pinned to a tag, so what an install pulls down cannot change
# underneath it. If this mirror is ever unreachable, faster-whisper's own
# download still runs as a fallback.
WHISPER_BASE = (
    "https://github.com/Tristan367/basicagent/releases/download/models-v1"
)
WHISPER_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")

# Exact byte counts of the mirrored files. A download that arrives shorter than
# this is a captive portal or a truncated transfer, not a model.
WHISPER_SIZES = {
    "tiny.en": {"config.json": 2317, "model.bin": 75_537_502,
                "tokenizer.json": 2_128_466, "vocabulary.txt": 422_309},
    "base.en": {"config.json": 2227, "model.bin": 145_216_508,
                "tokenizer.json": 2_128_466, "vocabulary.txt": 422_309},
    "small.en": {"config.json": 2657, "model.bin": 483_545_366,
                 "tokenizer.json": 2_128_466, "vocabulary.txt": 422_309},
}


def whisper_dir(size: str) -> Path:
    """Where a dictation model lives once it is here."""
    return models_dir() / f"whisper-{size}"


def dictation_installed(size: str) -> bool:
    """Whether every file of that model is present and the right length."""
    expected = WHISPER_SIZES.get(size)
    if not expected:
        return False
    folder = whisper_dir(size)
    return all(
        (folder / name).is_file() and (folder / name).stat().st_size == length
        for name, length in expected.items()
    )


def install_dictation(say=print, size: str = "", force: bool = False) -> bool:
    """Pull down the dictation model now, rather than during the first sentence.

    Otherwise faster-whisper fetches it the first time it transcribes anything:
    a wait of a minute or two with no explanation, at the exact moment somebody
    is finding out whether the microphone works at all.

    Unlike the read-aloud voices this does not need the virtual environment --
    it is four files over HTTP -- which means it can be retried by hand later
    on an install where something else went wrong.
    """
    if not size:
        try:
            from agent_server.config import whisper_size

            size = whisper_size()
        except Exception:
            size = "small.en"

    expected = WHISPER_SIZES.get(size)
    if not expected:
        say(f"  There is no mirrored model called {size}.")
        return _dictation_from_upstream(say, size)

    if dictation_installed(size) and not force:
        say("The dictation model is already installed.")
        return True

    folder = whisper_dir(size)
    total = sum(expected.values())
    say(f"Downloading the dictation model ({size}, {_human(total)}) to {folder}")
    for name in WHISPER_FILES:
        target = folder / name
        length = expected[name]
        if target.is_file() and target.stat().st_size == length and not force:
            continue
        # Only the big one is worth announcing; the other three are instant.
        if length > 1_000_000:
            say(f"  {name} ({_human(length)})")
        try:
            _download(f"{WHISPER_BASE}/faster-whisper-{size}--{name}",
                      target, length, say)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            say(f"  Could not download {name}: {e}")
            return _dictation_from_upstream(say, size)
    say("Dictation model installed.")
    return True


def _dictation_from_upstream(say, size: str) -> bool:
    """The old route, kept as a fallback when the mirror cannot be reached.

    faster-whisper's own download, which needs the virtual environment and may
    be rate-limited -- but a rate limit that lifts in an hour is better than no
    dictation at all, and this is the path that was working before the mirror
    existed.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        say("  Dictation will fetch its model the first time it is used instead.")
        return False
    from agent_server.config import FASTER_WHISPER_COMPUTE

    say("  Trying the original source instead...")
    try:
        WhisperModel(size, device="cpu", compute_type=FASTER_WHISPER_COMPUTE)
    except Exception as e:
        say(f"  That did not work either: {e}")
        say("  Dictation will try again the first time it is used. Everything")
        say("  else works; you can type instead of talking until then.")
        return False
    say("Dictation model installed.")
    return True


def dictation_sizes_needed(chosen: str = "") -> list:
    """Every speech model the app will actually load, not just the chosen one.

    Live dictation shows words as you speak by re-transcribing about once a
    second, which the chosen model is usually too slow to keep up with -- so a
    small fast one does that job and the chosen one produces the final text.
    Installing only the chosen one therefore left the app fetching the other
    from Hugging Face at first launch: a silent 145 MB, from the one host that
    rate-limits anonymous downloads, at the moment somebody first presses the
    microphone button. Which is the failure this mirror exists to prevent.
    """
    from agent_server.stt import partial_size

    if not chosen:
        try:
            from agent_server.config import whisper_size

            chosen = whisper_size()
        except Exception:
            chosen = "small.en"
    return list(dict.fromkeys([chosen, partial_size(chosen)]))


def main(argv: list[str]) -> int:
    what = argv[1] if len(argv) > 1 else ""
    if what in ("read-aloud", "tts"):
        return 0 if install_read_aloud(force="--force" in argv) else 1
    if what in ("dictation", "stt"):
        asked = next((a for a in argv[2:] if not a.startswith("-")), "")
        force = "--force" in argv
        ok = True
        for size in dictation_sizes_needed(asked):
            ok = install_dictation(size=size, force=force) and ok
        return 0 if ok else 1
    print("usage: python -m agent_server.downloads read-aloud|dictation [--force]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
