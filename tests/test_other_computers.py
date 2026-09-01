"""Windows and macOS, which is where almost everybody actually is.

This is written on Linux and tested on Linux, and every one of these bugs is
invisible here. `os.killpg` exists, `python3` is on PATH, `/` is the separator,
and nothing complains -- right up until somebody who is not a developer double
clicks the icon on the computer they own, which is a Windows one.

Two kinds of check, because the two kinds of bug are different:

* **Simulated.** Force `os.name` to `"nt"` and run the code, with the Windows
  side of it stubbed. Catches the class this file was written for: a POSIX-only
  call reached on a platform that has never heard of it, in a cleanup path
  nobody exercises.
* **Read.** Search the source for things that cannot work there -- a bare
  `python3`, a hard-coded `/`, an `os.killpg` outside the one module allowed to
  say it. Cruder, and the only thing that catches a line nothing has run yet.

Neither replaces running it on Windows. Both stop the same bug being written
twice.
"""

from __future__ import annotations

import inspect
import os
import subprocess
from pathlib import Path

import pytest

from agent_server import processes

SOURCE = sorted(Path("agent_server").rglob("*.py"))


def code_only(path: Path) -> str:
    """A file with its comments and docstrings taken out.

    Both of these tests search for a forbidden string, and every one of them
    is also a perfectly good thing to *write about* -- this file is full of
    sentences containing `os.killpg`, and so is the module that explains why
    nothing else may call it. Searching raw text made the prose the bug.
    """
    import ast
    import io
    import tokenize

    text = path.read_text()
    drop = set()
    try:
        tree = ast.parse(text)
    except SyntaxError:  # pragma: no cover - would fail the suite elsewhere
        return text
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            drop.update(range(body[0].lineno, (body[0].end_lineno or body[0].lineno) + 1))
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.COMMENT:
            drop.add(token.start[0])
    return "\n".join(line for n, line in enumerate(text.splitlines(), 1)
                      if n not in drop)


class _Process:
    """A process that is running, and remembers how it was told to stop."""

    def __init__(self, pid=4321, returncode=None):
        self.pid = pid
        self.returncode = returncode
        self.signalled = []
        self.killed = False

    def send_signal(self, sig):
        self.signalled.append(sig)

    def kill(self):
        self.killed = True


# ── stopping things, which is where the POSIX calls hide ────────────────────


def test_stopping_a_process_on_windows_does_not_reach_for_killpg(monkeypatch):
    """`os.killpg`, `os.getpgid` and `signal.SIGKILL` do not exist on Windows.

    The `bash` tool called all three and caught `(ProcessLookupError,
    PermissionError, OSError)`, which does not include the AttributeError that
    a missing `os.killpg` raises. So on Windows the app's answer to "that
    command took too long" was a crash inside the cleanup path.
    """
    ran = []
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: ran.append(a[0]) or subprocess.CompletedProcess(a[0], 0))
    # The real attributes, removed the way Windows does not have them.
    monkeypatch.delattr(os, "killpg", raising=False)
    monkeypatch.delattr(os, "getpgid", raising=False)

    processes.kill_tree(_Process())
    assert ran, "nothing was run to stop it"
    assert ran[0][0] == "taskkill", ran
    assert "/T" in ran[0], "it does not stop the children, only the shell"


def test_stopping_a_process_on_posix_signals_the_whole_group(monkeypatch):
    seen = {}
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(os, "getpgid", lambda pid: 999)
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: seen.update(pgid=pgid, sig=sig))

    processes.signal_tree(_Process(), processes.SOFT)
    assert seen == {"pgid": 999, "sig": processes.SOFT}


def test_a_process_that_cannot_be_grouped_is_still_stopped(monkeypatch):
    """The fallback matters: one dead shell beats a command that runs forever."""
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(os, "getpgid", lambda pid: (_ for _ in ()).throw(ProcessLookupError))
    process = _Process()
    processes.signal_tree(process, processes.SOFT)
    assert process.signalled == [processes.SOFT]


def test_stopping_something_already_dead_is_quiet():
    processes.kill_tree(_Process(returncode=0))
    processes.kill_tree(None)


def test_starting_a_process_asks_for_a_group_on_both(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    assert processes.spawn_kwargs() == {"start_new_session": True}
    monkeypatch.setattr(os, "name", "nt")
    windows = processes.spawn_kwargs()
    assert "start_new_session" not in windows, (
        "start_new_session is POSIX-only and does nothing on Windows")
    assert "creationflags" in windows


@pytest.mark.parametrize("module", ["agent_server/tools/bash.py", "agent_server/preview.py"])
def test_nothing_else_signals_a_process_by_hand(module):
    """Both had their own copy and the copies had drifted -- `preview` knew
    about Windows and `bash` did not. One implementation, so the next fix
    cannot land in only one of them."""
    source = code_only(Path(module))
    for posix_only in ("os.killpg", "os.getpgid", "signal.SIGKILL", "start_new_session"):
        assert posix_only not in source, (
            f"{module} handles processes itself again ({posix_only}); "
            "use agent_server.processes")


# ── commands this app writes itself ─────────────────────────────────────────


def test_no_tool_writes_python3_into_a_command():
    """There is no `python3` on Windows. The command is `python`, or `py`, or
    a full path, depending on how it was installed -- so a game built fine and
    then would not open, with `'python3' is not recognized` in a preview log
    nobody reads.

    Only commands this app composes. What the model writes is its own business:
    it is told the platform in its instructions and can see for itself.
    """
    for path in SOURCE:
        for line in code_only(path).splitlines():
            if "python3" in line:
                pytest.fail(f"{path}: {line.strip()}")


def test_the_commands_it_writes_quote_the_interpreter():
    """`sys.executable` on Windows goes through AppData, and on a Mac through
    Application Support -- both of which have spaces in them, and an unquoted
    path with a space is two arguments."""
    from agent_server.tools import game

    source = inspect.getsource(game)
    assert 'f\'"{sys.executable}"' in source or '"{sys.executable}"' in source, (
        "the interpreter path goes into a shell command unquoted")


# ── paths ───────────────────────────────────────────────────────────────────


def test_the_data_directory_is_the_right_one_on_each_computer(monkeypatch):
    """`Path()` decides which flavour it is from `os.name` when it is called,
    so faking the platform also breaks the type -- hence the pure path, which
    can be built for a computer that is not this one."""
    import pathlib

    from agent_server import paths

    monkeypatch.delenv("BASICAGENT_DATA_DIR", raising=False)
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(paths, "Path", pathlib.PureWindowsPath)
    monkeypatch.setenv("APPDATA", r"C:\Users\Someone\AppData\Roaming")
    where = str(paths.data_dir())
    assert "AppData" in where and where.endswith("basicagent"), where

    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(paths, "Path", pathlib.Path)
    monkeypatch.setenv("XDG_DATA_HOME", "/home/someone/.local/share")
    assert str(paths.data_dir()) == "/home/someone/.local/share/basicagent"


def test_the_browser_cache_is_looked_for_in_three_places(monkeypatch):
    """It was looked for in the Linux one only, so a Mac reported Chromium
    missing however many times it had been installed."""
    import sys

    from agent_server import setup

    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    monkeypatch.setattr(sys, "platform", "darwin")
    assert "Library/Caches" in str(setup._playwright_cache())

    import pathlib

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(setup, "Path", pathlib.PureWindowsPath)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\Someone\AppData\Local")
    assert "AppData" in str(setup._playwright_cache())

    monkeypatch.setattr(setup, "Path", pathlib.Path)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "name", "posix")
    assert ".cache" in str(setup._playwright_cache())


# ── what the assistant is told about the computer it is on ──────────────────


def test_the_model_is_told_which_computer_this_is():
    """Half of portability is not code. A model that knows it is on Windows
    writes `dir` instead of `ls` by itself; one that does not, cannot."""
    from agent_server.system_prompt import environment_block

    block = environment_block("/tmp/project", session_id="t")
    assert "Platform:" in block


# ── the destructive-command guard, on the computer most people have ─────────


DESTROYS_THE_MACHINE = [
    r"del /f /s /q C:\*",
    r"del /s /q %USERPROFILE%\*",
    r"rd /s /q C:\Windows",
    r"rmdir /s /q C:\Users",
    r"rd /s /q %SystemRoot%",
    r"format C: /y",
    "Remove-Item -Recurse -Force C:\\",
    r"Remove-Item -Recurse -Force $env:USERPROFILE",
]

ORDINARY_AND_CORRECT = [
    r"rd /s /q build",
    r"del /q *.log",
    r"del /s /q dist\*",
    r"Remove-Item -Recurse -Force node_modules",
    r"rmdir /s /q .\out",
    "npm run build",
    "git status",
    r"format-table",           # a PowerShell cmdlet, not the disk one
    r"echo C:\Windows",
]


@pytest.mark.parametrize("command", DESTROYS_THE_MACHINE)
def test_the_guard_covers_windows_too(command):
    """The whole guard was POSIX-shaped: `rm -rf /`, `/dev/sda`, a fork bomb
    written in shell. Not one of those can be typed on Windows, where the shell
    is cmd and the commands are `del`, `rd` and `format` -- so on Windows this
    protected nobody, and the people it did not protect are the ones who cannot
    notice what went wrong or put it back."""
    from agent_server.tools.bash import danger_reason

    assert danger_reason(command), f"allowed: {command}"


@pytest.mark.parametrize("command", ORDINARY_AND_CORRECT)
def test_the_guard_does_not_get_in_the_way_on_windows(command):
    """A guard that fires on `rd /s /q build` is a guard that gets switched
    off. Deleting a build folder is the most ordinary thing there is."""
    from agent_server.tools.bash import danger_reason

    assert danger_reason(command) is None, f"refused: {command}"


# ── getting it onto the computer in the first place ─────────────────────────


def test_windows_has_something_to_double_click():
    """The instruction was `python3 install.py`.

    There is no `python3` on Windows, so step one of the install was a command
    that does not exist -- for the people least able to work out why, on the
    platform nearly all of them are on. Working out whether this machine calls
    it `python`, `py`, or a path inside AppData is exactly the kind of thing
    this app exists so that nobody has to do.
    """
    script = Path("Install on Windows.bat")
    assert script.exists(), "there is no Windows installer"
    text = script.read_text()
    assert "py -3 install.py" in text, "it does not use the Python launcher"
    assert "python install.py" in text, "no fallback for a Store install"
    commands = [ln for ln in text.splitlines() if not ln.strip().startswith("rem")]
    assert "python3" not in "\n".join(commands), (
        "python3 does not exist on Windows")
    assert "pause" in text, (
        "double-clicked from Explorer the window closes before anything can "
        "be read")


def test_the_readme_does_not_tell_windows_users_to_run_python3():
    readme = Path("README.md").read_text()
    windows_part = readme.split("**Windows**")[1].split("**Mac or Linux**")[0]
    assert "python3" not in windows_part, windows_part
    assert "Install on Windows" in windows_part


# ── the icon on the desktop, which is the only thing most people ever use ───


def test_the_launcher_looks_for_a_file_windows_will_admit_exists():
    """The bug this is named after: `.venv\\Scripts\\python` with no `.exe`.

    Windows will happily *run* that, because CreateProcess appends the
    extension itself -- but `os.path.exists` does not, so the launcher's check
    for "is this installed yet" was False on every Windows machine there has
    ever been, and it exited before doing anything. The shortcut runs
    pythonw.exe, which has no console, so what that looked like from the
    outside was an icon that showed an hourglass for a second and then nothing
    at all: no window, no error, nowhere to look.
    """
    source = (Path("basicagent.py")).read_text()
    at = source.index("VENV_PY = ")
    line = source[at:source.index("\n\n", at)]
    assert '"python.exe" if IS_WIN else "python"' in line, line


def test_there_is_somewhere_for_a_message_to_go_when_there_is_no_console():
    """pythonw.exe leaves sys.stdout and sys.stderr as None, so every print in
    the launcher is either thrown away or an AttributeError nobody will ever
    see -- which is exactly the moment somebody needs to be told something.

    Two consequences, both checked here: there is a log file, and the few
    messages worth interrupting a person about reach an actual dialog box.
    """
    source = (Path("basicagent.py")).read_text()
    assert "sys.stdout is None" in source and "sys.stderr is None" in source
    assert "MessageBoxW" in source
    # And it happens before the first thing that would want to say something.
    body = source[source.index("def main("):]
    assert body.index("log_file()") < body.index("os.path.exists(VENV_PY)")


def test_children_of_a_windowless_launcher_do_not_open_windows_of_their_own():
    """A console program started by a console-less parent gets a console of
    its own on Windows: a black box that appears beside the app and stays
    there for as long as it is open. The whole reason for pythonw was that it
    does not do that."""
    source = (Path("basicagent.py")).read_text()
    assert "CREATE_NO_WINDOW" in source
    # Every child of the launcher, not just the first one that was noticed.
    assert source.count("**child_output()") >= 3
