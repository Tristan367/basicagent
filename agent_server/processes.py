"""Starting a process so it can be stopped, and stopping it on any computer.

Two places need this -- the `bash` tool, which has to kill a command that ran
over its time, and `preview`, which has to stop whatever the project is running
when the user presses Stop. Both had their own copy, and the copies had drifted:
`preview` knew about Windows and `bash` did not, so on Windows every timed-out
command raised `AttributeError: module 'os' has no attribute 'killpg'` from
inside the kill path -- the app's answer to "that took too long" was a crash.

The rules, once:

* A process must be started in a group of its own, or stopping it kills only
  the shell and leaves the real work behind. `npm run dev` is the everyday
  case: kill the shell and node keeps the port, and the next start fails on an
  address already in use, which reads as a bug in the project rather than here.
* Stopping means the whole tree, not the one process whose id we happen to
  hold.
* Nothing here may raise. It is called from cleanup paths, where the process is
  frequently already gone -- a race this cannot win and does not need to.
"""

import contextlib
import logging
import os
import signal
import subprocess

log = logging.getLogger(__name__)

# Windows has no SIGKILL. It has no signals worth the name at all: `taskkill`
# is the whole story there, and it is equally final either way.
HARD = getattr(signal, "SIGKILL", signal.SIGTERM)
SOFT = signal.SIGTERM


def spawn_kwargs() -> dict:
    """How to start a process so that `signal_tree` can reach its children.

    POSIX puts it in a new session; Windows in a new process group, which is
    also what stops a Ctrl-C in a console this app was launched from taking
    the user's project down with it.
    """
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def signal_tree(process, sig=HARD) -> None:
    """Signal a process and everything it started. Never raises.

    The fallback is the single process. It is worth having: a process that
    could not be put in a group of its own still has to be stoppable, and one
    dead shell is better than a command that runs forever.
    """
    if process is None or getattr(process, "returncode", None) is not None:
        return
    try:
        if os.name == "nt":  # pragma: no cover - platform specific
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)],
                           capture_output=True, check=False)
            return
        os.killpg(os.getpgid(process.pid), sig)
        return
    except Exception:
        # Deliberately everything. `os.killpg` and `os.getpgid` do not exist on
        # Windows at all, so a narrow `except OSError` -- which is what both
        # copies of this had -- lets an AttributeError straight out of a
        # cleanup path. That is the bug this module was written for.
        pass
    with contextlib.suppress(Exception):
        process.send_signal(sig)


def kill_tree(process) -> None:
    """Stop it now, with no grace period. Never raises."""
    signal_tree(process, HARD)
    with contextlib.suppress(Exception):
        if getattr(process, "returncode", None) is None:
            process.kill()
