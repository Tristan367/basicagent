"""A project with a back end and a front end, which is most real web work.

Found by building one and running it against the real tools. The app runs one
thing per project; a modern web project is two processes, so the obvious route
is `bash` with `&` for the API and `preview` for the page. That works, once,
and then the API is a process nothing in this app can reach: not the Stop
button, not closing the project, not deleting it. The user has no terminal, so
it holds its port until they restart the computer -- and the next start fails
with "address already in use", which reads as a bug in their project.

It bit the stress harness itself two checks after the one that created it,
which is as good a demonstration as any.

The capability was already there: `preview` runs its command in a process group
of its own, so `api & frontend` is two servers that start and stop together.
Nothing said so, and the `bash` description actively taught the other way.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from agent_server.tools.registry import TOOLS


def test_preview_says_a_whole_stack_is_one_command():
    described = TOOLS["preview"].description
    assert "two servers" in described or "two calls" in described, described
    assert "&" in described, "the way to do it is not shown"
    assert "stop together" in described, (
        "nothing says the second server is stopped too, which is the point")


def test_bash_no_longer_teaches_the_thing_that_makes_orphans():
    """It used to read: "Long-running processes (dev servers, watchers) must be
    backgrounded with `&`" -- which is exactly how a server ends up outliving
    everything that could stop it."""
    described = TOOLS["bash"].description
    assert "must be backgrounded with `&`" not in described
    assert "belongs in `preview`" in described
    assert "nothing can stop it" in described or "nothing can stop" in described


def test_the_note_fires_at_the_moment_somebody_does_it_anyway():
    """A description is read once at the start of a session; this arrives in
    the tool result, at the moment the mistake is made, which is the only time
    it can still be undone cheaply."""
    from agent_server.tools import bash

    # `_execute` is the half that waits for a command and formats what came
    # back; `run_bash` is now the half that decides whether to wait at all.
    source = inspect.getsource(bash._execute)
    assert "left a background process running" in source
    assert "preview" in source.split("left a background process running")[1][:600], (
        "the note does not say what to do instead")


def test_the_agent_is_told_before_it_starts():
    prompt = Path("system_prompts/agent.md").read_text()
    assert "one `preview` command, not two" in prompt
    assert "address already in use" in prompt, (
        "the symptom is not named, so it will not be recognised when it happens")


def test_preview_runs_its_command_in_a_group_so_both_die():
    """The whole fix rests on this: `a & b` is two processes in one group, and
    the group is what gets signalled. Without it only the shell dies and the
    backgrounded half keeps the port."""
    from agent_server import preview

    source = inspect.getsource(preview._spawn)
    assert "processes.spawn_kwargs()" in source
    kill = inspect.getsource(preview._kill)
    assert "signal_tree" in kill
