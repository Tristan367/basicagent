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
VENV_PY = os.path.join(ROOT, ".venv", "Scripts" if IS_WIN else "bin", "python")

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

    if not os.path.exists(VENV_PY):
        print("No .venv found. Run:  python install.py")
        sys.exit(1)

    warn_if_exposed()

    proc = None
    if alive():
        print(f"Assistant already running at {URL}")
    else:
        proc = subprocess.Popen(server_command(), cwd=ROOT)
        if not wait_up():
            print(f"Server did not come up at {URL}. Check the log.", file=sys.stderr)
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
            opened = False
            try:
                opened = subprocess.run(
                    [VENV_PY, "-m", "agent_server.desktop", URL], cwd=ROOT
                ).returncode == 0
            except Exception:
                opened = False
            if not opened:
                print("The app's own window would not open, so it is in your "
                      "browser instead.")
                print("To get the window back, open Settings inside the app and "
                      "ask the Project Manager to finish setting up.")
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
