#!/usr/bin/env python3
"""Set this app up on a computer that has never run it.

One command, no questions, and at the end an icon the user can click. That last
part is the point: the person this app is built for cannot open a terminal, so
an install that ends with "now type this" has not finished.

    python3 install.py              everything, including the speech downloads
    python3 install.py --minimal    skip the big optional downloads (about 900 MB)
    python3 install.py --no-shortcut   do not add a desktop icon or a menu entry
    python3 install.py --here       install in this folder, do not move it

Standard library only. It runs before the virtual environment exists, so it
cannot import anything the app depends on.
"""

import contextlib
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
VENV = ROOT / ".venv"
VENV_PY = VENV / ("Scripts" if IS_WIN else "bin") / ("python.exe" if IS_WIN else "python")

# The window the dependencies actually support. The floor is what this codebase
# is written against; the ceiling is real and moves -- kokoro-onnx, which is
# read-aloud, publishes no wheel above it, and pip's answer to that is forty
# lines of "ignored the following versions" ending in a resolver error. Somebody
# whose computer came with a newer Python than the one this was released against
# is the ordinary case a year from now, not an unlucky edge.
MIN_PYTHON = (3, 11)
BELOW_PYTHON = (3, 14)
# Newest first: an install picks the best one it can find rather than the oldest.
FALLBACK_PYTHONS = ["python3.13", "python3.12", "python3.11"]

APP_NAME = "Assistant"

# ── where it ends up living ─────────────────────────────────────────────────
#
# Not where it was unzipped. That is the Downloads folder, and Windows Storage
# Sense -- which plenty of people have switched on without remembering --
# deletes anything there over thirty days old. An app that disappears a month
# after it was installed, taking its desktop icon's target with it, is a
# support call nobody can diagnose and the user cannot describe.
#
# Per-user rather than Program Files. Program Files needs an administrator,
# and asking for one is a UAC prompt at best and a locked-down school laptop at
# worst. This is where VS Code, Slack and Discord put a per-user install, and
# it needs no permission from anybody.


def home_for() -> Path:
    if IS_WIN:
        base = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "Programs" / APP_NAME
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / APP_NAME
    # Beside the data directory but never inside it: deleting one should not
    # be able to take the other with it.
    return Path.home() / ".local" / "opt" / "basicagent"


# ── how it looks while it works ─────────────────────────────────────────────
#
# The person watching this has been told the app is not technical. Ten minutes
# of pip's output is the strongest possible argument that it is. None of what
# follows changes what gets installed; it changes whether somebody stays in the
# room while it happens.

BANNER = r"""
    _              _        _                 _
   /_\   ___ ___  (_) ___  | |_   __ _  _ _  | |_
  / _ \ (_-<(_-<  | |(_-<  |  _| / _` || ' \ |  _|
 /_/ \_\/__//__/ _/ |/__/   \__| \__,_||_||_| \__|
                |__/
"""

SPINNER = "|/-\\"


def _ansi_ok() -> bool:
    """Whether the cursor can be moved about. Windows needs asking first."""
    if not sys.stdout.isatty():
        return False
    if os.getenv("TERM") == "dumb" or os.getenv("NO_COLOR"):
        return False
    if not IS_WIN:
        return True
    try:
        import ctypes

        kernel = ctypes.windll.kernel32
        handle = kernel.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING. Present since Windows 10; if this
        # fails the installer simply prints plain lines instead.
        return bool(kernel.SetConsoleMode(handle, mode.value | 0x0004))
    except (OSError, AttributeError, ImportError, ValueError):
        return False


def _unicode_ok() -> bool:
    try:
        "█░".encode(sys.stdout.encoding or "ascii")
    except (UnicodeEncodeError, LookupError):
        return False
    return True


ANSI = _ansi_ok()
BLOCKS = _unicode_ok()
TICK = "✓" if BLOCKS else "ok"


def _width() -> int:
    try:
        return max(50, min(100, shutil.get_terminal_size((80, 24)).columns))
    except OSError:
        return 80


def _fit(text: str) -> str:
    room = _width() - 1
    if len(text) <= room:
        return text
    return text[: room - 1] + ("…" if BLOCKS else ".")


def _bar(fraction: float, width: int = 30) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    full, empty = ("█", "░") if BLOCKS else ("#", "-")
    return full * filled + empty * (width - filled)


def _tail_path(path: Path, keep: int = 3) -> str:
    """Enough of a path to recognise it, on a line that has no room for all of
    it. The full path is printed in the summary at the end, where there is."""
    parts = path.parts
    if len(parts) <= keep:
        return str(path)
    return os.sep.join(("...", *parts[-keep:]))


def _mins(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    return f"{int(seconds // 60)}m {int(seconds % 60):02d}s"


class Screen:
    """The two live lines at the bottom, and the finished lines above them.

    Falls back to ordinary printing whenever the cursor cannot be moved -- a
    redirected install log, an old Windows console, a CI machine -- because a
    progress bar written into a file is unreadable and this has to work there
    too.
    """

    def __init__(self) -> None:
        self.total = 1.0
        self.done = 0.0
        self.span = 0.0
        self.frac = 0.0
        self.title = ""
        self.detail = ""
        self.began = time.monotonic()
        self.step_began = self.began
        self.shown = False
        self.tick = 0
        self.last_plain = -1
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.ticker = None

    # -- the frame ----------------------------------------------------------

    def overall(self) -> float:
        return min(1.0, (self.done + self.span * max(0.0, min(1.0, self.frac)))
                   / self.total)

    def _erase(self) -> None:
        if ANSI and self.shown:
            sys.stdout.write("\033[2A\r\033[K\n\r\033[K\033[1A")
            self.shown = False

    def _paint(self) -> None:
        percent = self.overall() * 100
        if not ANSI:
            # Every five per cent, on its own line, so a log stays readable.
            step = int(percent // 5)
            if step != self.last_plain:
                self.last_plain = step
                detail = f"  {self.detail}" if self.detail else ""
                print(f"    {percent:3.0f}%  {self.title}{detail}", flush=True)
            return
        self.tick += 1
        spin = SPINNER[self.tick % len(SPINNER)]
        first = _fit(f"  {spin}  {self.title}")
        second = _fit(f"     [{_bar(self.overall())}] {percent:3.0f}%  {self.detail}")
        if self.shown:
            sys.stdout.write("\033[2A")
        sys.stdout.write(f"\r\033[K{first}\n\r\033[K{second}\n")
        sys.stdout.flush()
        self.shown = True

    def _run_ticker(self) -> None:
        while not self.stopping.wait(0.12):
            with self.lock:
                self._paint()

    # -- what the installer calls -------------------------------------------

    def plan(self, weights: list) -> None:
        self.total = float(sum(weights)) or 1.0

    def line(self, text: str = "") -> None:
        """A permanent line, printed above the live area."""
        with self.lock:
            self._erase()
            print(text, flush=True)
            if self.title:
                self._paint()

    def banner(self, subtitle: str) -> None:
        print(BANNER)
        print(f"  {subtitle}")
        print()

    def start(self, title: str, weight: float, detail: str = "") -> None:
        with self.lock:
            self.title, self.detail = title, detail
            self.span, self.frac = float(weight), 0.0
            self.step_began = time.monotonic()
            self._paint()
            if self.ticker is None and ANSI:
                self.stopping.clear()
                self.ticker = threading.Thread(target=self._run_ticker, daemon=True)
                self.ticker.start()

    def progress(self, fraction: float, detail: str | None = None) -> None:
        with self.lock:
            self.frac = fraction
            if detail is not None:
                self.detail = detail

    def finish_step(self, note: str = "") -> None:
        with self.lock:
            took = _mins(time.monotonic() - self.step_began)
            self.done += self.span
            self.span = self.frac = 0.0
            title, self.title, self.detail = self.title, "", ""
            self._erase()
            tail = f"  {note}" if note else ""
            body = f"  {TICK}  {title}{tail}"
            pad = max(1, _width() - 1 - len(body) - len(took))
            print(_fit(f"{body}{' ' * pad}{took}"), flush=True)
            self.last_plain = -1

    def close(self) -> None:
        with self.lock:
            self.stopping.set()
        if self.ticker:
            self.ticker.join(timeout=1.0)
            self.ticker = None
        with self.lock:
            self._erase()


screen = Screen()


def say(message: str = "") -> None:
    screen.line(message)


def _stream(cmd: list, on_line=None, where: Path | None = None) -> tuple:
    """Run something and watch what it says, instead of letting it scroll past.

    Returns (returncode, last few lines) so a failure can still be shown in
    full -- the whole point of hiding the output is that it can be produced
    again when it turns out to have mattered.
    """
    try:
        process = subprocess.Popen(
            [str(c) for c in cmd], cwd=str(where or ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
    except OSError as e:
        # A missing interpreter or an unreadable directory. Returned rather
        # than raised so an optional step -- a voice download, a browser --
        # can decide for itself that it is not worth stopping the install for.
        return 127, [f"could not start {cmd[0]}: {e}"]
    tail = []
    for raw in process.stdout:
        line = raw.rstrip()
        if not line:
            continue
        tail.append(line)
        del tail[:-30]
        if on_line:
            on_line(line)
    process.wait()
    return process.returncode, tail


def run(cmd: list, where: Path | None = None) -> None:
    code, tail = _stream(cmd, where=where)
    if code != 0:
        raise subprocess.CalledProcessError(code, cmd, output="\n".join(tail))


def stop(message: str, fix: str = "") -> None:
    """Fail the way a person can act on: what is wrong, then what to do."""
    say()
    say(f"Stopped: {message}")
    if fix:
        say()
        say(fix)
    sys.exit(1)


# ── the checks worth making before anything is downloaded ───────────────────


def _version_of(python: str) -> tuple | None:
    """What version an interpreter on PATH is, or None if it is not there."""
    found = shutil.which(python)
    if not found:
        return None
    try:
        out = subprocess.run(
            [found, "-c", "import sys; print(*sys.version_info[:2])"],
            capture_output=True, text=True, timeout=20, check=True,
        ).stdout.split()
        return tuple(int(n) for n in out)
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def _suitable(version: tuple | None) -> bool:
    return bool(version) and MIN_PYTHON <= version < BELOW_PYTHON


def check_python() -> None:
    """Make sure the install runs on a Python the dependencies support.

    Not a check so much as a repair. The interpreter somebody types is whatever
    their computer came with, and being told "your Python is too new, go and
    install an old one" is where a non-technical person stops -- so if a
    suitable one is already on this machine, this starts again with it rather
    than explaining the problem.
    """
    if _suitable(sys.version_info[:2]):
        return

    have = ".".join(str(n) for n in sys.version_info[:3])
    low = ".".join(str(n) for n in MIN_PYTHON)
    high = ".".join(str(n) for n in (BELOW_PYTHON[0], BELOW_PYTHON[1] - 1))

    for candidate in FALLBACK_PYTHONS:
        if _suitable(_version_of(candidate)):
            found = shutil.which(candidate)
            say(f"This is Python {have}, which some of the parts do not support yet.")
            say(f"Found {candidate} on this computer; using that instead.")
            say()
            os.execv(found, [found, str(Path(__file__).resolve()), *sys.argv[1:]])

    # `uv` can fetch one without administrator rights, which is the only route
    # that works on a locked-down machine. Only used if it is already here --
    # installing an installer to run an installer is a step too far.
    if shutil.which("uv"):
        say(f"This is Python {have}, which some of the parts do not support yet.")
        say(f"Fetching Python {high} with uv (this needs no admin rights)...")
        try:
            subprocess.run(["uv", "python", "install", high], check=True)
            found = subprocess.run(
                ["uv", "python", "find", high],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            if found and _suitable(_version_of(found)):
                say()
                os.execv(found, [found, str(Path(__file__).resolve()), *sys.argv[1:]])
        except (subprocess.SubprocessError, OSError) as e:
            say(f"  That did not work ({e}).")

    stop(
        f"this app needs Python {low} to {high}, and this is Python {have}.",
        f"Install Python {high} and run this again with it, for example:\n"
        f"    python{high} install.py\n\n"
        "On Ubuntu or Debian:   sudo apt install python" + high + "\n"
        "On a Mac with Homebrew: brew install python@" + high + "\n"
        "On Windows:            https://www.python.org/downloads/\n\n"
        "Or, with no administrator rights at all, install `uv` from\n"
        "https://docs.astral.sh/uv/ and run this again -- it will fetch the\n"
        "right Python by itself.",
    )


def check_venv_module() -> None:
    """Debian and Ubuntu ship a Python that cannot make a virtual environment.

    The error it gives instead is several lines of ensurepip internals, which is
    a bad first impression and tells nobody what to install.
    """
    import importlib.util

    if importlib.util.find_spec("venv") is None:
        stop(
            "this Python cannot create a virtual environment.",
            "On Ubuntu or Debian, install it with:\n"
            "    sudo apt install python3-venv\n"
            "Then run this again.",
        )


def check_space(needed_gb: float) -> None:
    try:
        free = shutil.disk_usage(str(ROOT)).free / 1_000_000_000
    except OSError:
        return
    if free < needed_gb:
        stop(
            f"there is only {free:.1f} GB free here, and this needs about {needed_gb:.0f} GB.",
            "Free some space up and run this again, or use --minimal to skip the\n"
            "speech downloads (about 800 MB of the total).",
        )


def relocate() -> None:
    """Get the app out of the folder it was unzipped into, before building it.

    Runs before the virtual environment is made, and that order is the whole
    trick: a venv records absolute paths in `pyvenv.cfg` and in every script it
    writes, so one that is moved afterwards is quietly broken. Copy first,
    build second, and nothing has to be repaired.

    The download is copied rather than moved -- this script is being executed
    out of it -- so the folder in Downloads is left intact and can be thrown
    away by hand. Any `.venv` already at the destination survives, which makes
    reinstalling over the top of an existing copy fast instead of another
    half-gigabyte.
    """
    global ROOT, VENV, VENV_PY

    if "--here" in sys.argv:
        return
    if (ROOT / ".git").is_dir():
        # A clone. Whoever has one chose where it lives and would not thank
        # anybody for a second copy appearing somewhere else.
        return

    target = home_for()
    if target.resolve() == ROOT:
        return

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            ROOT, target, dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(
                ".venv", ".git", "__pycache__", ".pytest_cache", "*.pyc"),
        )
    except OSError as e:
        screen.line(f"  Could not move it to {target} ({e}).")
        screen.line("  Installing where it is instead. Keep this folder where it is.")
        return

    ROOT = target
    VENV = ROOT / ".venv"
    VENV_PY = VENV / ("Scripts" if IS_WIN else "bin") / (
        "python.exe" if IS_WIN else "python")
    with contextlib.suppress(OSError):
        os.chdir(ROOT)


# ── the install itself ──────────────────────────────────────────────────────


def make_venv() -> None:
    if VENV_PY.exists():
        screen.progress(1.0, "one is already here")
        return
    screen.progress(0.3, "creating it")
    run([sys.executable, "-m", "venv", str(VENV)])
    screen.progress(1.0)


# What each piece is for, in words that mean something to whoever is watching.
# Not documentation: the point is that somebody who was told this app listens
# and talks can see the listening and the talking being installed, rather than
# eighty names they have no way to judge.
PURPOSE = {
    "fastapi": "the app itself",
    "starlette": "the app itself",
    "uvicorn": "runs the app on your computer",
    "jinja2": "the pages you see",
    "markupsafe": "the pages you see",
    "openai": "talking to the AI",
    "httpx": "talking over the internet",
    "httpcore": "talking over the internet",
    "h11": "talking over the internet",
    "certifi": "checking who it is talking to",
    "aiosqlite": "remembering your projects",
    "python-dotenv": "your settings",
    "python-multipart": "files you attach",
    "playwright": "the browser it shows your work in",
    "greenlet": "the browser it shows your work in",
    "pyee": "the browser it shows your work in",
    "numpy": "the number crunching behind the voices",
    "soundfile": "reading and writing sound",
    "cffi": "reading and writing sound",
    "pillow": "pictures",
    "kokoro-onnx": "the read-aloud voices",
    "onnxruntime": "running the voice and hearing models",
    "espeakng-loader": "how the read-aloud voice pronounces things",
    "phonemizer-fork": "how the read-aloud voice pronounces things",
    "faster-whisper": "turning what you say into words",
    "ctranslate2": "turning what you say into words",
    "tokenizers": "turning what you say into words",
    "huggingface-hub": "fetching the speech model",
    "av": "decoding the sound from your microphone",
    "pydantic": "checking data is the shape it should be",
    "pydantic-core": "checking data is the shape it should be",
    "pytest": "the tests",
    "pytest-asyncio": "the tests",
    # onnxruntime drags these in, and they are large enough to sit on screen
    # for a while. Naming them beats a bare word nobody can place.
    "coloredlogs": "part of the voice engine",
    "humanfriendly": "part of the voice engine",
    "flatbuffers": "part of the voice engine",
    "protobuf": "part of the voice engine",
    "sympy": "part of the voice engine",
    "mpmath": "part of the voice engine",
    "uvloop": "makes the app respond quickly",
    "watchfiles": "makes the app respond quickly",
    "websockets": "the live connection to the page you see",
    "hf-xet": "fetching the speech model",
    "tqdm": "fetching the speech model",
}

# Only ever used to move a bar along. Both are deliberate over-estimates of a
# fresh install on this machine, and the bar is clamped below full until pip
# actually finishes, so being wrong makes it slightly pessimistic and never
# makes it lie about being done.
EXPECTED_PACKAGES = 86
EXPECTED_MB = 430.0

PIP_COLLECT = re.compile(r"^\s*Collecting\s+([A-Za-z0-9._-]+)")
# "Downloading numpy-2.5.2-cp313-...whl (16.7 MB)", and the cached equivalent.
# The name comes from the file itself: pip resolves everything before it
# fetches anything, so the last package it mentioned collecting is not the one
# currently coming down the wire.
PIP_FILE = re.compile(
    r"^\s*(?:Downloading|Using cached)\s+([A-Za-z0-9._-]+?)-\d\S*"
    r"\s+\(([\d.]+)\s*([kKMG])B\)\s*$")
PIP_INSTALLING = re.compile(r"^\s*Installing collected packages:\s*(.+)$")


def _describe(name: str) -> str:
    clean = name.lower().replace("_", "-").split("[")[0]
    purpose = PURPOSE.get(clean)
    return f"{clean} — {purpose}" if purpose else clean


def install_dependencies() -> None:
    seen = []
    megabytes = [0.0]
    installing = []
    current = [""]

    def watch(line: str) -> None:
        fetching = PIP_FILE.match(line)
        if fetching and ".metadata" not in line:
            name, amount, unit = fetching.groups()
            if name not in seen:
                seen.append(name)
            megabytes[0] += float(amount) * {"k": 0.001, "K": 0.001,
                                             "M": 1.0, "G": 1024.0}[unit]
            current[0] = _describe(name)
            screen.progress(
                _pip_fraction(seen, megabytes, installing),
                f"{current[0]}   ({megabytes[0]:.0f} MB so far)")
            return
        found = PIP_COLLECT.match(line)
        if found:
            # Still working out what it needs; nothing is downloading yet.
            current[0] = _describe(found.group(1))
            screen.progress(_pip_fraction(seen, megabytes, installing), current[0])
            return
        started = PIP_INSTALLING.match(line)
        if started:
            installing[:] = [n.strip() for n in started.group(1).split(",") if n.strip()]
            screen.progress(_pip_fraction(seen, megabytes, installing),
                            f"putting {len(installing)} pieces in place")

    run([VENV_PY, "-m", "pip", "install", "--upgrade", "pip"])
    screen.progress(0.02, "reading the list")
    code, tail = _stream([VENV_PY, "-m", "pip", "install", "-r", "requirements.txt"],
                         on_line=watch)
    if code != 0:
        screen.close()
        say()
        say("pip could not install what the app needs. It said:")
        say()
        for line in tail[-15:]:
            say(f"    {line}")
        stop("the app's parts could not be installed.",
             "The lines above are pip's own; they usually name what is missing.")
    screen.progress(1.0, f"{len(seen) or len(installing)} pieces installed")


def _pip_fraction(seen: list, megabytes: list, installing: list) -> float:
    """Two guesses at how far along pip is, and the more advanced one wins."""
    by_count = len(seen) / EXPECTED_PACKAGES
    by_size = megabytes[0] / EXPECTED_MB
    guess = max(by_count, by_size)
    if installing:
        guess = max(guess, 0.9)
    return min(0.97, guess)


# Downloading Chromium and being able to start Chromium are two different
# things, and the gap between them is where a fresh Linux machine lands:
# `playwright install` fetches the browser and none of the system libraries it
# links against, so a minimal Debian or a slim container downloads 150 MB
# successfully and then cannot open a window. Fixing that needs a package
# manager and a password -- which the app has neither of, and which the person
# running this installer has both of, right now, in a terminal they already
# have open. So it is checked here, once, while somebody can still act on it.
CHROMIUM_STARTS = (
    "from playwright.sync_api import sync_playwright\n"
    "with sync_playwright() as p:\n"
    "    p.chromium.launch(headless=True).close()\n"
)


def _dependency_hint(stderr: str) -> list:
    """The useful part of Playwright's missing-libraries complaint.

    It knows this machine's package manager and this machine's missing
    packages; nothing here does. So the lines it prints are passed along
    verbatim rather than paraphrased into a command that may not exist on the
    distribution somebody is actually running.
    """
    keep = []
    for raw in (stderr or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("<3"):
            continue
        if ("install" in line or "apt" in line or "dnf" in line or "pacman" in line
                or line.startswith("lib") or "missing" in line.lower()):
            keep.append(line)
    return keep[:8] or ["(it did not say which libraries)"]


def install_browser() -> bool:
    """Chromium, used both for the app's own window and for showing the user
    whatever they have built. Optional in the sense that the app starts without
    it, and not optional in the sense that anybody would want to be without it.
    """
    def watch(line: str) -> None:
        percent = re.search(r"(\d{1,3})%", line)
        if percent:
            screen.progress(int(percent.group(1)) / 100.0, "downloading Chromium")

    screen.progress(0.02, "asking where to get it")
    code, _ = _stream([VENV_PY, "-m", "playwright", "install", "chromium"],
                      on_line=watch)
    if code != 0:
        screen.progress(1.0, "not installed")
        say("     That did not work. The app still runs; it will open in your own")
        say("     browser instead, and building websites will be limited.")
        return False

    screen.progress(0.98, "checking it starts")
    check = subprocess.run(
        [str(VENV_PY), "-c", CHROMIUM_STARTS],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    if check.returncode == 0:
        screen.progress(1.0, "and it starts")
        return True

    if "missing dependencies" in (check.stderr or ""):
        # Linux only, and not because of a check that says so: Chromium on
        # Windows and macOS ships with everything it links against, and
        # Playwright never prints this there. The fix is also not one command
        # -- `playwright install-deps` is Debian and Ubuntu only, and naming it
        # on Arch or Fedora sends somebody to a command that fails -- so what
        # gets printed is Playwright's own answer for this machine rather than
        # a guess made here.
        say()
        say("     The browser downloaded, but this computer does not have the")
        say("     system libraries it needs to run it. This is a minimal Linux")
        say("     install; it does not happen on Windows or a Mac. Playwright says:")
        say()
        for line in _dependency_hint(check.stderr):
            say(f"       {line}")
        say()
        say("     That needs an administrator. Without it the app still runs and")
        say("     still writes code, but it cannot show you a window of your work.")
    else:
        say("     The browser downloaded but would not start here. The app still")
        say("     runs; it will open in your own browser instead.")
    screen.progress(1.0, "downloaded, but it will not start here")
    return False


# Godot is deliberately *not* installed here. Most people who install this app
# will never make a game, and 90 MB downloaded on their behalf on the chance
# that they might is 90 MB spent on nothing. The `game` tool fetches it the
# first time somebody actually asks for a game, which is the only moment it is
# known to be wanted -- and by then the person is already waiting for a game to
# appear, so the download is time they were spending anyway.


def install_speech(which: str) -> None:
    """One of the two big speech downloads. Neither failing is fatal.

    Both are large, and both are the entire interface for somebody who cannot
    see the screen -- so they are installed by default and skipped only when
    asked. Run through the environment just built, so they read the same
    settings the running app will and land where it will look for them.
    """
    def watch(line: str) -> None:
        percent = re.search(r"(\d{1,3})\s*%", line)
        if percent:
            size = re.search(r"\(([^)]*\bMB\b[^)]*)\)", line)
            screen.progress(int(percent.group(1)) / 100.0,
                            size.group(1) if size else "downloading")

    code, tail = _stream([VENV_PY, "-m", "agent_server.downloads", which],
                         on_line=watch)
    if code != 0:
        screen.progress(1.0, "not installed; the app will offer it again later")
        for line in tail[-4:]:
            say(f"     {line}")
        return
    screen.progress(1.0, "ready")


# ── the icon, which is the part that matters ────────────────────────────────


def make_shortcut() -> str:
    """Somewhere to click. Returns a plain description of what was made."""
    try:
        if IS_WIN:
            return _shortcut_windows()
        if IS_MAC:
            return _shortcut_mac()
        return _shortcut_linux()
    except OSError as e:
        return f"could not add a shortcut ({e})"


def _shortcut_linux() -> str:
    apps = Path.home() / ".local" / "share" / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    entry = apps / "basicagent.desktop"
    entry.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        "Comment=Build software by talking to it\n"
        # Quoted: the Desktop Entry spec splits Exec on spaces, and a home
        # directory with a space in it is ordinary on every platform but this one.
        f'Exec="{VENV_PY}" "{ROOT / "basicagent.py"}"\n'
        f"Path={ROOT}\n"
        "Terminal=false\n"
        # One main category only, or the entry shows up twice in the menu.
        "Categories=Development;\n",
        encoding="utf-8",
    )
    entry.chmod(0o755)

    # And on PATH, for anybody who does use a terminal.
    bindir = Path.home() / ".local" / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    link = bindir / "basicagent"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(ROOT / "bin" / "basicagent")

    # And on the desktop, because that is where the download page says it will
    # be and because a menu is not somewhere everybody looks. Marked executable
    # and trusted: without both, GNOME shows it as a text file and KDE asks
    # whether you are sure -- which is not what an icon you were promised
    # should do the first time you click it.
    where = "your applications menu"
    desktop = Path.home() / "Desktop"
    if desktop.is_dir():
        copy = desktop / "basicagent.desktop"
        with contextlib.suppress(OSError):
            copy.write_text(entry.read_text(encoding="utf-8"), encoding="utf-8")
            copy.chmod(0o755)
            subprocess.run(["gio", "set", str(copy), "metadata::trusted", "true"],
                           capture_output=True, check=False)
            where = "your desktop and your applications menu"
    return f"put \"{APP_NAME}\" on {where}"


def _shortcut_mac() -> str:
    """A real .app bundle, not a script that opens a Terminal window.

    A .command works and looks like homework: double-clicking one opens
    Terminal, prints things, and leaves the window sitting there afterwards.
    An .app is what a Mac user expects -- it appears in Launchpad, it can be
    dragged to the Dock, and nothing about it says "script".

    A bundle built on the machine it runs on needs no signing and no notarising:
    Gatekeeper quarantines what was downloaded, not what was made here.
    """
    apps = Path.home() / "Applications"
    bundle = apps / f"{APP_NAME}.app"
    macos = bundle / "Contents" / "MacOS"
    macos.mkdir(parents=True, exist_ok=True)

    (bundle / "Contents" / "Info.plist").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        f'  <key>CFBundleName</key><string>{APP_NAME}</string>\n'
        f'  <key>CFBundleDisplayName</key><string>{APP_NAME}</string>\n'
        '  <key>CFBundleIdentifier</key><string>com.basicagent.assistant</string>\n'
        f'  <key>CFBundleExecutable</key><string>{APP_NAME}</string>\n'
        '  <key>CFBundlePackageType</key><string>APPL</string>\n'
        '  <key>CFBundleShortVersionString</key><string>1.0</string>\n'
        # Otherwise the launcher appears in the Dock as a second, nameless
        # icon beside the browser window it opens.
        '  <key>LSUIElement</key><true/>\n'
        '</dict></plist>\n',
        encoding="utf-8",
    )
    launcher = macos / APP_NAME
    launcher.write_text(
        "#!/bin/bash\n"
        f'cd "{ROOT}" || exit 1\n'
        f'exec "{VENV_PY}" basicagent.py\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)

    where = ["your Applications folder"]
    desktop = Path.home() / "Desktop"
    if desktop.is_dir():
        link = desktop / f"{APP_NAME}.app"
        with contextlib.suppress(OSError):
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(bundle)
            where.append("your desktop")
    return f'put "{APP_NAME}" in ' + " and on ".join(where)


def _shortcut_windows() -> str:
    """A real .lnk on the Desktop and in the Start menu.

    A .bat works and looks like a script: it has a gear icon, it flashes a
    black console window, and somebody reasonably wonders whether they are
    doing something advanced. A .lnk is an icon you double-click, which is what
    was asked for.

    Made through PowerShell's COM object rather than pywin32 -- that would be
    another thing to install before the thing that installs things, and every
    Windows since 7 has PowerShell. If it is locked down, the batch file is
    still written as a fallback, so there is always something to click.
    """
    target = VENV / "Scripts" / "pythonw.exe"
    # pythonw runs with no console window at all. If it is missing (some
    # embedded builds), python.exe still works and merely flashes.
    if not target.exists():
        target = VENV_PY

    made = []
    places = []
    desktop = Path.home() / "Desktop"
    if desktop.is_dir():
        places.append((desktop, "your desktop"))
    start = (Path(os.getenv("APPDATA", Path.home())) / "Microsoft" / "Windows"
             / "Start Menu" / "Programs")
    if start.is_dir():
        places.append((start, "your Start menu"))

    body = (
        "@echo off\r\n"
        f'cd /d "{ROOT}"\r\n'
        f'start "" "{VENV_PY}" basicagent.py\r\n'
    )
    for folder, name in places:
        link = folder / f"{APP_NAME}.lnk"
        script = (
            "$s = (New-Object -ComObject WScript.Shell).CreateShortcut("
            f"'{link}'); "
            f"$s.TargetPath = '{target}'; "
            f"$s.Arguments = '\"{ROOT / 'basicagent.py'}\"'; "
            f"$s.WorkingDirectory = '{ROOT}'; "
            f"$s.Description = 'Build software by talking to it'; "
            "$s.Save()"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, check=False)
        if result.returncode == 0 and link.exists():
            made.append(name)
        else:
            # Always something to click, even on a machine that will not run
            # PowerShell.
            (folder / f"{APP_NAME}.bat").write_text(body, encoding="utf-8")
            made.append(name)

    if not made:
        return "could not find anywhere to put a shortcut"
    return f'put "{APP_NAME}" on ' + " and ".join(made)


# ── what to say at the end ──────────────────────────────────────────────────


def finish(shortcut: str, moved_from: Path | None) -> None:
    screen.close()
    say()
    say("=" * 62)
    say(f"{APP_NAME} is installed.")
    say()
    if shortcut:
        say(f"  I {shortcut}. Click it to start.")
    say(f"  It lives in: {ROOT}")
    if moved_from is not None:
        say()
        say("  It was copied there out of the folder you unzipped, so that a")
        say("  tidy-up of your Downloads can never delete it. You can throw")
        say(f"  away {moved_from.name} whenever you like.")
    say()
    say("  Your projects, settings and keys are kept separately again, in")
    say(f"  {_data_dir_hint()} -- updating or reinstalling never touches them.")
    say()
    say("  The first time it opens it will ask you to connect an AI, and it")
    say("  will walk you through getting a free key from Google if you have")
    say("  not got one. Nothing else needs setting up.")
    say("=" * 62)


def _data_dir_hint() -> str:
    """Where the app will keep the database, without importing the app."""
    if IS_WIN:
        base = Path(os.getenv("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return str(base / "basicagent")


def main() -> None:
    minimal = "--minimal" in sys.argv
    no_shortcut = "--no-shortcut" in sys.argv
    if "-h" in sys.argv or "--help" in sys.argv:
        print(__doc__)
        return

    # Before the banner: this may replace the running process with a different
    # interpreter, and a banner drawn twice looks like the installer restarted.
    check_python()

    screen.banner("Setting up. It only happens once, and it takes a few minutes.")

    steps = [("Checking this computer can run it", 1)]
    if "--here" not in sys.argv and not (ROOT / ".git").is_dir():
        steps.append(("Putting it somewhere permanent", 2))
    steps += [
        ("Making a private Python for the app", 3),
        ("Installing the app's parts", 40),
        ("Installing the browser it shows your work in (Chromium)", 18),
    ]
    if not minimal:
        steps += [
            ("Installing the read-aloud voices (Kokoro)", 16),
            ("Installing the model that hears you (Whisper)", 18),
        ]
    if not no_shortcut:
        steps.append(("Putting an icon where you can click it", 2))
    screen.plan([weight for _, weight in steps])
    pending = dict(steps)

    def step(title):
        screen.start(title, pending[title])

    step("Checking this computer can run it")
    check_venv_module()
    check_space(1.0 if minimal else 2.5)
    screen.finish_step(f"Python {'.'.join(str(n) for n in sys.version_info[:3])}")

    moved_from = None
    if "Putting it somewhere permanent" in pending:
        step("Putting it somewhere permanent")
        was = ROOT
        relocate()
        if was != ROOT:
            moved_from = was
        screen.finish_step(_tail_path(ROOT))

    step("Making a private Python for the app")
    make_venv()
    screen.finish_step()

    step("Installing the app's parts")
    install_dependencies()
    screen.finish_step()

    step("Installing the browser it shows your work in (Chromium)")
    install_browser()
    screen.finish_step()

    if minimal:
        say("  Skipping the speech downloads. Ask the assistant to install them")
        say("  later if you want them -- it knows how.")
    else:
        step("Installing the read-aloud voices (Kokoro)")
        install_speech("read-aloud")
        screen.finish_step()
        step("Installing the model that hears you (Whisper)")
        install_speech("dictation")
        screen.finish_step()

    shortcut = ""
    if not no_shortcut:
        step("Putting an icon where you can click it")
        shortcut = make_shortcut()
        screen.finish_step()
    finish(shortcut, moved_from)


if __name__ == "__main__":
    main()
