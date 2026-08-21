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


def install_dictation(say=print) -> bool:
    """Pull down the dictation model now, rather than during the first sentence.

    faster-whisper fetches its own model the first time it transcribes anything,
    which is a wait of a minute or two with no explanation, at the exact moment
    somebody is finding out whether the microphone works at all.

    Needs the installed dependencies, so unlike the read-aloud fetch this one
    only runs from inside the virtual environment.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        say("  faster-whisper is not installed; skipping dictation.")
        return False
    from agent_server.config import DEFAULT_WHISPER_MODEL, FASTER_WHISPER_COMPUTE

    say(f"Downloading the dictation model ({DEFAULT_WHISPER_MODEL}, about 480 MB)")
    try:
        WhisperModel(DEFAULT_WHISPER_MODEL, device="cpu", compute_type=FASTER_WHISPER_COMPUTE)
    except Exception as e:
        say(f"  Could not download it: {e}")
        say("  Dictation will fetch it the first time it is used instead.")
        return False
    say("Dictation model installed.")
    return True


def main(argv: list[str]) -> int:
    what = argv[1] if len(argv) > 1 else ""
    if what in ("read-aloud", "tts"):
        return 0 if install_read_aloud(force="--force" in argv) else 1
    if what in ("dictation", "stt"):
        return 0 if install_dictation() else 1
    print("usage: python -m agent_server.downloads read-aloud|dictation [--force]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
