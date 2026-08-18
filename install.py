#!/usr/bin/env python3
"""Cross-platform installer for the Assistant.

Sets up a Python virtualenv, installs the dependencies and the bundled Chromium
(used for the app window and the browser tool). The optional speech pieces
(dictation and read-aloud) are handled by the assistant on first run.

Usage:
    python install.py
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
IS_WIN = os.name == "nt"
VENV = os.path.join(ROOT, ".venv")
VENV_PY = os.path.join(VENV, "Scripts" if IS_WIN else "bin", "python")


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)


def main() -> None:
    if not os.path.exists(VENV_PY):
        run([sys.executable, "-m", "venv", VENV])
    run([VENV_PY, "-m", "pip", "install", "--upgrade", "pip"])
    run([VENV_PY, "-m", "pip", "install", "-r", "requirements.txt"])
    try:
        run([VENV_PY, "-m", "playwright", "install", "chromium"])
    except subprocess.CalledProcessError:
        print("    (Chromium install skipped; the browser tool will be unavailable)")

    print()
    print("Done. Start the app with:")
    print("    python basicagent.py")
    print()
    print("The first time you open it, add an API key in Settings, and the assistant")
    print("will offer to install any missing speech pieces (dictation and read-aloud).")


if __name__ == "__main__":
    main()
