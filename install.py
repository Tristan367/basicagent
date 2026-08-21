#!/usr/bin/env python3
"""Set this app up on a computer that has never run it.

One command, no questions, and at the end an icon the user can click. That last
part is the point: the person this app is built for cannot open a terminal, so
an install that ends with "now type this" has not finished.

    python3 install.py              everything, including the speech downloads
    python3 install.py --minimal    skip the big downloads (about 1 GB of them)
    python3 install.py --no-shortcut   do not add a desktop icon or a menu entry

Standard library only. It runs before the virtual environment exists, so it
cannot import anything the app depends on.
"""

import os
import shutil
import subprocess
import sys
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


def say(message: str = "") -> None:
    print(message, flush=True)


def run(cmd: list, where: Path = ROOT) -> None:
    say(f"  $ {' '.join(str(c) for c in cmd)}")
    subprocess.run([str(c) for c in cmd], cwd=str(where), check=True)


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
            "voice downloads (about 1 GB of the total).",
        )


# ── the install itself ──────────────────────────────────────────────────────


def make_venv() -> None:
    if VENV_PY.exists():
        say("A Python environment is already here; using it.")
        return
    say("Making a private Python environment for the app...")
    run([sys.executable, "-m", "venv", str(VENV)])


def install_dependencies() -> None:
    say()
    say("Installing what the app needs (a few minutes)...")
    run([VENV_PY, "-m", "pip", "install", "--upgrade", "--quiet", "pip"])
    run([VENV_PY, "-m", "pip", "install", "--quiet", "-r", "requirements.txt"])


def install_browser() -> bool:
    """Chromium, used both for the app's own window and for showing the user
    whatever they have built. Optional in the sense that the app starts without
    it, and not optional in the sense that anybody would want to be without it.
    """
    say()
    say("Installing the browser it uses to show you your work (about 150 MB)...")
    try:
        run([VENV_PY, "-m", "playwright", "install", "chromium"])
        return True
    except subprocess.CalledProcessError:
        say("  That did not work. The app still runs; it will open in your own")
        say("  browser instead, and building websites will be limited.")
        return False


def install_speech() -> None:
    """The read-aloud voices and the dictation model.

    Both are large, and both are the entire interface for somebody who cannot
    see the screen -- so they are installed by default and skipped only when
    asked. Neither failing is fatal.
    """
    # Both through the environment we just built, so they read the same
    # settings the running app will. Neither is allowed to fail the install:
    # a user with no read-aloud still has an app, and can ask for it later.
    for what in ("read-aloud", "dictation"):
        say()
        subprocess.run(
            [str(VENV_PY), "-m", "agent_server.downloads", what],
            cwd=str(ROOT), check=False,
        )


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
    return f"added it to your applications menu as \"{APP_NAME}\""


def _shortcut_mac() -> str:
    """A .command file: double-clicking one runs it, with no bundle to sign."""
    apps = Path.home() / "Applications"
    apps.mkdir(parents=True, exist_ok=True)
    script = apps / f"{APP_NAME}.command"
    script.write_text(
        "#!/bin/bash\n"
        f'cd "{ROOT}"\n'
        f'exec "{VENV_PY}" basicagent.py\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return f'put "{APP_NAME}" in your Applications folder'


def _shortcut_windows() -> str:
    """A .bat on the Desktop and in the Start menu.

    Not a .lnk: making one needs pywin32, which is another thing to install
    before the thing that installs things. A batch file double-clicks the same.
    """
    made = []
    body = (
        "@echo off\r\n"
        f'cd /d "{ROOT}"\r\n'
        f'start "" "{VENV_PY}" basicagent.py\r\n'
    )
    desktop = Path.home() / "Desktop"
    if desktop.is_dir():
        (desktop / f"{APP_NAME}.bat").write_text(body, encoding="utf-8")
        made.append("your desktop")
    start = (Path(os.getenv("APPDATA", Path.home())) / "Microsoft" / "Windows"
             / "Start Menu" / "Programs")
    if start.is_dir():
        (start / f"{APP_NAME}.bat").write_text(body, encoding="utf-8")
        made.append("your Start menu")
    if not made:
        return "could not find anywhere to put a shortcut"
    return f'put "{APP_NAME}" on ' + " and ".join(made)


# ── what to say at the end ──────────────────────────────────────────────────


def finish(shortcut: str) -> None:
    say()
    say("=" * 62)
    say(f"{APP_NAME} is installed.")
    say()
    if shortcut:
        say(f"  I {shortcut}. Click it to start.")
    say(f"  From a terminal: {VENV_PY} {ROOT / 'basicagent.py'}")
    say()
    say("The first time it opens it will ask you to connect an AI, and it will")
    say("walk you through getting a free key from Google if you have not got one.")
    say("Nothing else needs setting up.")
    say("=" * 62)


def main() -> None:
    minimal = "--minimal" in sys.argv
    no_shortcut = "--no-shortcut" in sys.argv
    if "-h" in sys.argv or "--help" in sys.argv:
        say(__doc__)
        return

    say(f"Installing {APP_NAME} into {ROOT}")
    say()
    check_python()
    check_venv_module()
    check_space(1.0 if minimal else 3.0)

    make_venv()
    install_dependencies()
    install_browser()
    if minimal:
        say()
        say("Skipping the speech downloads. Ask the assistant to install them")
        say("later if you want them -- it knows how.")
    else:
        install_speech()

    shortcut = "" if no_shortcut else make_shortcut()
    finish(shortcut)


if __name__ == "__main__":
    main()
