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
            try:
                subprocess.run([VENV_PY, "-m", "agent_server.desktop", URL], cwd=ROOT)
            except Exception:
                open_in_browser_blocking()
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
