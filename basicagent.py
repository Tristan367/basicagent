#!/usr/bin/env python3
"""Cross-platform launcher for the Assistant.

Starts the server, opens the app (a Chromium app window when available, otherwise
the system browser), and stops the server when the window closes. Works the same
on Linux, macOS and Windows.

Usage:
    python basicagent.py            start and open the app
    python basicagent.py --browser  start and open in the system browser
    python basicagent.py --no-open  start the server only
"""

import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
IS_WIN = os.name == "nt"
# The `.exe` is not decoration. Windows will happily *run* "python" without it,
# because CreateProcess adds the extension itself -- but os.path.exists does
# not, so the check below said the app had never been installed on every
# Windows machine there has ever been. The desktop icon starts pythonw, which
# has no console, so what that looked like from outside was a shortcut that
# flashed an hourglass and then did nothing at all, with no error anywhere.
VENV_PY = os.path.join(ROOT, ".venv", "Scripts" if IS_WIN else "bin",
                       "python.exe" if IS_WIN else "python")


# ── having somewhere to say things, when there is nowhere to say them ───────
#
# The Windows shortcut runs pythonw.exe, which is Python with no console
# attached. That is what stops a black window sitting behind the app for as
# long as it is open -- and it also means sys.stdout and sys.stderr are None,
# so every print() in this file is either thrown away or an AttributeError
# nobody will ever see. Which is precisely the situation in which somebody
# needs to be told something.
#
# So: a log file when there is no console, and a real dialog box for the few
# messages that are worth interrupting somebody about.

LOG = None


def log_file():
    """Where to write when there is no console. None when there is one."""
    global LOG
    if LOG is not None or (sys.stdout is not None and sys.stderr is not None):
        return LOG
    if IS_WIN:
        base = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                            "basicagent")
    else:
        base = os.path.join(os.environ.get("XDG_DATA_HOME")
                            or os.path.join(os.path.expanduser("~"), ".local", "share"),
                            "basicagent")
    try:
        os.makedirs(base, exist_ok=True)
        LOG = open(os.path.join(base, "start.log"), "a",  # noqa: SIM115
                   encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        return None
    if sys.stdout is None:
        sys.stdout = LOG
    if sys.stderr is None:
        sys.stderr = LOG
    print(f"\n--- started {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
    return LOG


def child_output() -> dict:
    """Where a child process's output should go, and whether it gets a window.

    A console program started by a console-less parent gets a console of its
    own on Windows -- a black box that appears beside the app and stays there.
    """
    where = log_file()
    extra = {}
    if where is not None:
        extra = {"stdout": where, "stderr": where}
    if IS_WIN and where is not None:
        extra["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return extra


def tell(message: str) -> None:
    """Something the user has to see, whether or not there is a console."""
    print(message)
    if IS_WIN and LOG is not None:
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "Assistant", 0x10)
        except Exception:
            pass


PORT = os.environ.get("PORT", "8220")
HOST = os.environ.get("HOST", "127.0.0.1")
URL = f"http://{HOST}:{PORT}"


# Anything that is not the loopback address. There is no login on this app and
# there is not going to be one: it is a single-user tool that runs shell
# commands as whoever started it, so reaching it over a network is reaching a
# terminal on their computer. Someone will still do it -- to use the app from a
# tablet, which is a perfectly reasonable thing to want -- so this says plainly
# what they have done rather than refusing.
LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}


def warn_if_exposed() -> None:
    if HOST in LOOPBACK:
        return
    print()
    print("!" * 70)
    print(f"  WARNING: this is listening on {HOST}, not just on this computer.")
    print()
    print("  There is no password on it, and it can run any command your")
    print("  account can. Anyone who can reach this address can use it.")
    print()
    print("  If you want to use it from another device, put it behind a VPN or")
    print("  an SSH tunnel rather than opening it to a network directly.")
    print("!" * 70)
    print()


def alive() -> bool:
    try:
        urllib.request.urlopen(URL, timeout=1)
        return True
    except Exception:
        return False


def wait_up() -> bool:
    for _ in range(80):
        if alive():
            return True
        time.sleep(0.25)
    return False


def server_command() -> list[str]:
    return [VENV_PY, "-m", "uvicorn", "agent_server.main:app", "--host", HOST, "--port", PORT]


def open_app_window() -> bool:
    """Open the app's own window. True once it has been opened and closed again.

    The returncode is the whole point. `subprocess.run` only raises when the
    command cannot be started, and this one always starts -- it is our own
    Python -- so a window that failed to open came back as a clean return and
    the caller fell through to "Window closed", having never opened one.
    Somebody whose Chromium had gone double-clicked the app and got nothing at
    all: no window, no error, and no terminal to find out from.
    """
    try:
        return subprocess.run(
            [VENV_PY, "-m", "agent_server.desktop", URL], cwd=ROOT,
            **child_output()
        ).returncode == 0
    except Exception:
        return False


def repair_browser_and_retry() -> bool:
    """Fetch Chromium again and have another go at the window.

    Playwright keeps it in a cache directory, and a cache directory is the
    first thing a "free up disk space" tool empties -- so the app losing its
    own window is a thing that happens to people who did nothing wrong. The
    only two people who could fix it are the user, who has no terminal, and
    this function.
    """
    print("The app's window needs a piece that has gone missing.")
    print("Fetching it now (about 150 MB, a few minutes). Nothing else to do.")
    try:
        got = subprocess.run(
            [VENV_PY, "-m", "playwright", "install", "chromium"], cwd=ROOT,
            **child_output()
        ).returncode == 0
    except Exception:
        got = False
    if not got:
        print("That did not work -- this computer may be offline.")
        return False
    return open_app_window()


def open_in_browser_blocking() -> None:
    import webbrowser

    webbrowser.open(URL)
    print(f"Assistant running at {URL}. Press Ctrl-C to stop.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""

    if mode in ("-h", "--help"):
        print(__doc__)
        return

    log_file()

    if not os.path.exists(VENV_PY):
        tell("Assistant is not finished installing.\n\n"
             "Run the installer again (\"Install on Windows\" in the folder you "
             "downloaded), let it finish, then click this icon again.")
        sys.exit(1)

    warn_if_exposed()

    proc = None
    if alive():
        print(f"Assistant already running at {URL}")
    else:
        proc = subprocess.Popen(server_command(), cwd=ROOT, **child_output())
        if not wait_up():
            tell("Assistant could not start.\n\n"
                 f"The server did not come up at {URL}."
                 + (f"\n\nThere is a log at {LOG.name}" if LOG else ""))
            if proc:
                proc.terminate()
            sys.exit(1)

    try:
        if mode == "--no-open":
            print(f"Assistant running at {URL}. Press Ctrl-C to stop.")
            while True:
                time.sleep(3600)
        elif mode == "--browser":
            open_in_browser_blocking()
        else:
            # Open the Chromium app window; desktop.py blocks until it closes.
            #
            # The returncode is the whole point of this. `subprocess.run` only
            # raises when the command cannot be started at all, and this one
            # always starts -- it is our own Python -- so a window that failed
            # to open came back as a clean return and this fell straight
            # through to "Window closed", having never opened one. Somebody
            # whose Chromium had gone (a disk cleaner reaching into ~/.cache is
            # all it takes) double-clicked the app and got nothing whatsoever:
            # no window, no error, no clue. The fallback below was written for
            # exactly that person and could never fire.
            if not open_app_window() and not repair_browser_and_retry():
                print("The app's own window would not open, so it is in your "
                      "browser instead.")
                open_in_browser_blocking()
            else:
                print("Window closed; stopping the server.")
    except KeyboardInterrupt:
        # Ctrl-C: shut down quietly, with no traceback.
        print()
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()


if __name__ == "__main__":
    main()
