"""Commands that take longer than a moment.

An install, a download, a build. Run the old way they held the entire turn: the
assistant said nothing for two minutes, and the person watching had a spinner
and no way to tell whether anything was happening at all. For somebody who has
never watched a terminal, that is indistinguishable from broken.

So a command still going after a few seconds is handed over. The tool answers
at once, the assistant can say "that's downloading, I'll tell you when it's
done", and it keeps that promise: the turn stays open, the output comes back
into the conversation, and the assistant reads it and replies.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_server import jobs
from agent_server.tools.base import ToolContext
from agent_server.tools.bash import run_bash


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(jobs, "_running", {})
    monkeypatch.setattr(jobs, "_finished", {})
    monkeypatch.setattr(jobs, "_landed", {})
    yield
    for session in list(jobs._running):
        jobs.cancel(session)


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(session_id="bg", project_dir=str(tmp_path),
                       abort=asyncio.Event())


# ── the ordinary case is unchanged ─────────────────────────────────────────


async def test_a_quick_command_still_just_returns_its_output(ctx):
    """Almost everything finishes instantly, and for those nothing changes --
    the output in the same breath is what the assistant reads most easily."""
    result = await run_bash(ctx, command="echo hello")
    assert "hello" in result.output
    assert not jobs.waiting("bg")


async def test_a_failure_is_still_a_failure(ctx):
    result = await run_bash(ctx, command="exit 3")
    assert result.is_error
    assert "exit code 3" in result.output


# ── the slow case is handed over ───────────────────────────────────────────


async def test_a_slow_command_answers_at_once_and_carries_on(ctx):
    result = await run_bash(ctx, command="sleep 5; echo done", wait=0.2)
    assert not result.is_error
    assert "still running" in result.output
    assert jobs.running("bg"), "nobody is looking after it"


async def test_the_assistant_is_told_not_to_sit_there(ctx):
    """The failure this is meant to prevent is silence. An assistant that hands
    a command over and then says nothing has made things worse, not better."""
    result = await run_bash(ctx, command="sleep 5", wait=0.2)
    assert "Tell the user what is happening" in result.output
    assert "Do not sit silently" in result.output


async def test_it_is_told_not_to_poll(ctx):
    """Left to itself a model will run `ps`, or start the same command again,
    or ask the user to check. All three are worse than waiting."""
    result = await run_bash(ctx, command="sleep 5", wait=0.2)
    assert "you do not need to check, poll, or run it again" in result.output


async def test_the_output_comes_back_when_it_lands(ctx):
    await run_bash(ctx, command="sleep 0.4; echo forty-two", wait=0.05)
    await jobs.wait_for_one("bg")
    landed = jobs.take_finished("bg")
    assert len(landed) == 1
    job, result = landed[0]
    assert "forty-two" in result.output
    assert job.command.startswith("sleep")


async def test_a_command_that_fails_later_still_reports(ctx):
    await run_bash(ctx, command="sleep 0.3; exit 7", wait=0.05)
    await jobs.wait_for_one("bg")
    _job, result = jobs.take_finished("bg")[0]
    assert result.is_error
    assert "exit code 7" in result.output


async def test_two_at_once_both_come_back(ctx):
    """The other half of not blocking: a turn can have two things going."""
    await run_bash(ctx, command="sleep 0.3; echo first", wait=0.05)
    await run_bash(ctx, command="sleep 0.5; echo second", wait=0.05)
    assert len(jobs.running("bg")) == 2

    for _ in range(2):
        await jobs.wait_for_one("bg")
        await asyncio.sleep(0.3)
    outputs = " ".join(r.output for _j, r in jobs.take_finished("bg"))
    assert "first" in outputs and "second" in outputs


# ── stopping means stopping ────────────────────────────────────────────────


async def test_stop_reaches_what_was_handed_over(ctx):
    """A command still running after the user has stopped the turn is one
    nobody asked for any more, and its output landing later would wake a
    conversation they had walked away from."""
    await run_bash(ctx, command="sleep 30", wait=0.05)
    assert jobs.cancel("bg") == 1
    assert not jobs.waiting("bg")


async def test_the_agent_cancels_them_when_stop_is_pressed():
    import inspect

    from agent_server import agent

    source = inspect.getsource(agent.request_abort)
    assert "jobs.cancel(session_id)" in source


async def test_waiting_for_one_gives_up_when_stop_is_pressed(ctx):
    """Otherwise a turn parked on a ten-minute download ignores Stop until the
    download finishes, and the user presses it again, and again."""
    await run_bash(ctx, command="sleep 30", wait=0.05)
    abort = asyncio.Event()

    async def press():
        await asyncio.sleep(0.1)
        abort.set()

    presser = asyncio.create_task(press())
    await asyncio.wait_for(jobs.wait_for_one("bg", abort), timeout=3)
    await presser
    assert abort.is_set()


async def test_waiting_returns_at_once_when_nothing_is_running():
    await asyncio.wait_for(jobs.wait_for_one("nobody"), timeout=1)


# ── how it reaches the conversation and the screen ─────────────────────────


def test_a_finished_command_becomes_a_note_not_an_orphan():
    """It is stored as a tool result with no call id, because nothing asked for
    it at that moment. The wire format has nowhere to put an unanswered one and
    `sanitize` would rightly drop it as corruption -- so it becomes what it
    actually is."""
    from agent_server.conversation import to_api_message

    row = {"role": "tool", "content": "it finished", "tool_call_id": ""}
    assert to_api_message(row) == {"role": "system", "content": "it finished"}


def test_a_real_tool_result_is_untouched():
    from agent_server.conversation import to_api_message

    row = {"role": "tool", "content": "ok", "tool_call_id": "call_1"}
    assert to_api_message(row)["role"] == "tool"


def test_the_turn_stays_open_rather_than_ending_and_being_woken():
    """A woken turn is often over in half a second, so anything that has to
    notice one starting -- a poller, a reconnect -- loses the race and the
    reply lands only in the database. One continuous run has no race in it."""
    import inspect

    from agent_server import agent

    source = inspect.getsource(agent._loop)
    assert "jobs.waiting(session_id)" in source
    assert "await jobs.wait_for_one(session_id, abort)" in source


def test_the_strip_does_not_put_a_shell_line_in_front_of_a_child():
    """The command is already on screen in the activity above. Repeating it
    here would be showing a nine-year-old a shell line and calling it
    progress."""
    said = jobs.note([jobs.Job("1", "s", "curl -fsSL https://x | sh", "curl", None)])
    assert said == "Still running that command…"
    assert "curl" not in said


def test_the_page_says_something_while_it_waits():
    """The user has just been told "I'll say when it's done". Without this the
    app looks like it finished and went quiet, which is the exact impression
    the whole feature exists to prevent."""
    from pathlib import Path

    js = Path("web_ui/static/js/app.js").read_text()
    assert "case 'waiting':" in js
    at = js.index("case 'waiting':")
    block = js[at:at + 900]
    assert "setLiveWork" in block
    # And the next reply starts its own bubble, the way it is stored.
    assert "closeSegment()" in block
