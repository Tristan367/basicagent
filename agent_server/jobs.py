"""Commands that outlive the tool call that started them.

An install, a download, a build: things that take minutes, and things this app's
users hit constantly because half of getting started is fetching something. Run
the old way they held the whole turn -- the assistant sat inside one tool call
saying nothing, the person watching got a spinner and no idea whether anything
was happening, and nothing else could be got on with in the meantime.

So a command that has not finished in a few seconds is handed over. The tool
answers straight away with "this is still going", the assistant gets on with
something else or simply says so out loud, and when the command finishes its
output is put back into the conversation and the assistant is woken to read it.

Two things this deliberately is not. It is not a way to run things in the
background on purpose -- `preview` is that, and it has a stop button. And it is
not fire-and-forget: every job here is collected, reported, and belongs to a
session, so nothing is left running that nobody will ever hear about again.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# How long a command gets to be an ordinary one. Under this, nothing changes:
# the tool waits and returns the output, which is what almost every command
# does and what the assistant reads most easily. Over it, the turn is worth more
# than the wait.
HANDOVER_SECONDS = 3.0


@dataclass
class Job:
    id: str
    session_id: str
    command: str
    title: str
    task: asyncio.Task
    started: float = field(default_factory=time.monotonic)

    @property
    def seconds(self) -> float:
        return time.monotonic() - self.started


_running: dict[str, list[Job]] = {}
_finished: dict[str, list[tuple[Job, object]]] = {}

# Called with a session id when a job finishes and there is something to read.
# Set by the agent module; left unset in tests and in anything that has no loop
# to wake, in which case a job simply waits to be collected.
_waker: Callable[[str], None] | None = None


def set_waker(waker: Callable[[str], None] | None) -> None:
    global _waker
    _waker = waker


def adopt(session_id: str, command: str, title: str, task: asyncio.Task) -> Job:
    """Take over a command the tool has stopped waiting for."""
    job = Job(id=uuid.uuid4().hex[:8], session_id=session_id, command=command,
              title=title, task=task)
    _running.setdefault(session_id, []).append(job)
    task.add_done_callback(lambda t: _settle(job, t))
    return job


def _settle(job: Job, task: asyncio.Task) -> None:
    running = _running.get(job.session_id) or []
    if job in running:
        running.remove(job)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        from agent_server.tools.base import ToolResult

        result = ToolResult.error(
            f"the command was still running and then failed: {error}", job.title)
    else:
        result = task.result()
    _finished.setdefault(job.session_id, []).append((job, result))
    log.info("background command finished session=%s after %.1fs: %s",
             job.session_id, job.seconds, job.title)
    _bell(job.session_id).set()
    if _waker is not None:
        with contextlib.suppress(Exception):
            _waker(job.session_id)


def running(session_id: str) -> list[Job]:
    return list(_running.get(session_id) or [])


def waiting(session_id: str) -> bool:
    """Whether anything is running or finished-but-unread for this session."""
    return bool(_running.get(session_id) or _finished.get(session_id))


def take_finished(session_id: str) -> list[tuple[Job, object]]:
    """Everything that has finished since this was last asked. Empties as it goes."""
    return _finished.pop(session_id, [])


def cancel(session_id: str) -> int:
    """Stop everything this session started. Returns how many were stopped.

    Pressing Stop has to reach these too. A command that carries on after the
    user has stopped the turn is a command nobody asked for any more, and the
    output arriving later would wake a conversation they had walked away from.
    """
    jobs = list(_running.get(session_id) or [])
    for job in jobs:
        job.task.cancel()
    _running.pop(session_id, None)
    _finished.pop(session_id, None)
    _bell(session_id).set()
    return len(jobs)


_landed: dict[str, asyncio.Event] = {}


def _bell(session_id: str) -> asyncio.Event:
    return _landed.setdefault(session_id, asyncio.Event())


async def wait_for_one(session_id: str, abort: asyncio.Event | None = None) -> None:
    """Wait until something finishes, or until there is nothing left to wait for.

    Races the abort, so Stop is not something the user has to press twice: a
    turn parked on a ten-minute download would otherwise ignore it until the
    download finished.
    """
    if _finished.get(session_id) or not _running.get(session_id):
        return
    bell = _bell(session_id)
    bell.clear()
    waiters = [asyncio.ensure_future(bell.wait())]
    if abort is not None:
        waiters.append(asyncio.ensure_future(abort.wait()))
    try:
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for w in waiters:
            w.cancel()
            with contextlib.suppress(BaseException):
                await w


def note(jobs: list[Job]) -> str:
    """What the strip says while a command is still going.

    Written for the person watching, not for the log. The command itself is
    already on screen in the activity above -- repeating it here would put a
    shell line in front of a nine-year-old and call it progress.
    """
    if not jobs:
        return ""
    if len(jobs) == 1:
        return "Still running that command\u2026"
    return f"Still running {len(jobs)} commands\u2026"
