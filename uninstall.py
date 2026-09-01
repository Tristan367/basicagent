#!/usr/bin/env python3
"""Take it off this computer again.

    python3 uninstall.py            ask about everything, then do it
    python3 uninstall.py --keep-data    leave projects and settings alone
    python3 uninstall.py --all      remove the work as well, without asking
    python3 uninstall.py --yes      do not ask anything

Somebody who cannot install software by hand cannot uninstall it by hand
either, and "just delete the folder" leaves a desktop icon pointing at nothing,
an entry in the Start menu, and about a gigabyte in three cache directories
they will never find. It is also the wrong thing to say to somebody deciding
whether to try this at all: an app you cannot get rid of is one people are
right to be wary of installing.

Two things are removed and one is asked about. The program goes, and the
shortcuts to it go. The projects, settings and API keys are yours, they live
somewhere else on purpose, and nothing here deletes them without being told to.

Standard library only, the same as the installer, so it still runs when the
environment it was going to remove is already broken.
"""

import contextlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
APP_NAME = "Assistant"
REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Assistant"


def data_dir() -> Path:
    """Where the projects and the settings live. Never the same place as the
    program, which is the entire reason an update cannot destroy them."""
    override = os.getenv("BASICAGENT_DATA_DIR")
    if override:
        return Path(override)
    if IS_WIN:
        base = Path(os.getenv("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.getenv("XDG_DATA_HOME")
                    or Path.home() / ".local" / "share")
    return base / "basicagent"


def browser_dir() -> Path | None:
    """Playwright's Chromium, which is 150 MB and is not shared with anything
    else on a machine that only has this. Left alone if something else put it
    there first -- that is what the environment variable means."""
    if os.getenv("PLAYWRIGHT_BROWSERS_PATH"):
        return None
    if IS_WIN:
        return Path(os.getenv("LOCALAPPDATA") or Path.home()) / "ms-playwright"
    if IS_MAC:
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def shortcuts() -> list:
    """Everywhere the installer could have put something to click."""
    found = []
    home = Path.home()
    if IS_WIN:
        start = (Path(os.getenv("APPDATA", home)) / "Microsoft" / "Windows"
                 / "Start Menu" / "Programs")
        for folder in (home / "Desktop", start):
            found += [folder / f"{APP_NAME}.lnk", folder / f"{APP_NAME}.bat"]
    elif IS_MAC:
        found += [home / "Applications" / f"{APP_NAME}.app",
                  home / "Desktop" / f"{APP_NAME}.app"]
    else:
        found += [home / ".local" / "share" / "applications" / "basicagent.desktop",
                  home / "Desktop" / "basicagent.desktop",
                  home / ".local" / "bin" / "basicagent"]
    return found


def _size(path: Path) -> int:
    if path.is_symlink() or path.is_file():
        with contextlib.suppress(OSError):
            return path.stat().st_size
        return 0
    total = 0
    for here, _, files in os.walk(path, onerror=lambda e: None):
        for name in files:
            with contextlib.suppress(OSError):
                total += (Path(here) / name).lstat().st_size
    return total


def _human(size: int) -> str:
    if size > 1_000_000_000:
        return f"{size / 1_000_000_000:.1f} GB"
    if size > 1_000_000:
        return f"{size / 1_000_000:.0f} MB"
    if size > 10_000:
        return f"{size / 1000:.0f} KB"
    # Rounding a database with a week of work in it down to "0 KB" makes the
    # question above it read as though there is nothing there to lose.
    return f"{size} bytes"


def remove(path: Path) -> bool:
    if path.is_symlink():
        with contextlib.suppress(OSError):
            path.unlink()
            return True
        return False
    if not path.exists():
        return False
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink()
    except OSError as e:
        print(f"  Could not remove {path}: {e}")
        return False
    return not path.exists()


def forget_in_settings() -> None:
    """Take the entry out of Windows' Apps list, where the installer put it."""
    if not IS_WIN:
        return
    with contextlib.suppress(Exception):
        subprocess.run(["reg", "delete", f"HKCU\\{REGISTRY_KEY}", "/f"],
                       capture_output=True, check=False)


def ask(question: str, default_yes: bool = True) -> bool:
    prompt = " [Y/n] " if default_yes else " [y/N] "
    try:
        answer = input(question + prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not answer:
        return default_yes
    return answer.startswith("y")


def remove_the_program_itself(where: Path) -> None:
    """Delete the folder this is running out of.

    Windows will not let a directory be removed while a process has it open,
    and this process does -- so on Windows the deletion is handed to a
    detached shell that waits a couple of seconds for Python to exit first.
    Everywhere else it is simply a delete.
    """
    if not IS_WIN:
        remove(where)
        return
    script = (f'ping -n 3 127.0.0.1 >nul & rmdir /s /q "{where}"')
    with contextlib.suppress(OSError):
        subprocess.Popen(["cmd", "/c", script],
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                         | getattr(subprocess, "CREATE_NO_WINDOW", 0))


def main() -> int:
    args = set(sys.argv[1:])
    if {"-h", "--help"} & args:
        print(__doc__)
        return 0
    quiet = "--yes" in args
    take_data = "--all" in args
    keep_data = "--keep-data" in args

    data, browsers = data_dir(), browser_dir()

    print()
    print("=" * 62)
    print(f"Removing {APP_NAME} from this computer.")
    print()
    print(f"  The program            {ROOT}   ({_human(_size(ROOT))})")
    if browsers and browsers.is_dir():
        print(f"  The browser it used    {browsers}   ({_human(_size(browsers))})")
    for path in shortcuts():
        if path.exists() or path.is_symlink():
            print(f"  A shortcut             {path}")
    print()
    if data.is_dir():
        print("  Your projects, settings and keys are in")
        print(f"  {data}   ({_human(_size(data))})")
        print("  These are yours. They are not removed unless you say so.")
    print("=" * 62)
    print()

    if not quiet and not ask("Remove the program?"):
        print("Nothing was changed.")
        return 1

    if data.is_dir() and not keep_data:
        if take_data or (not quiet and ask(
                "Also delete your projects, settings and API keys? "
                "This cannot be undone.", default_yes=False)):
            print(f"  Removing {data}")
            remove(data)
        else:
            print(f"  Keeping your work in {data}")

    for path in shortcuts():
        if (path.exists() or path.is_symlink()) and remove(path):
            print(f"  Removed {path.name}")
    forget_in_settings()

    if browsers and browsers.is_dir() and (
            quiet or take_data or ask(
                f"Remove the browser it downloaded ({_human(_size(browsers))})?")):
        print(f"  Removing {browsers}")
        remove(browsers)

    print(f"  Removing {ROOT}")
    remove_the_program_itself(ROOT)
    print()
    print(f"{APP_NAME} is gone. Thank you for trying it.")
    if data.is_dir():
        print(f"Your work is still in {data}.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
