"""One kind of subagent, and it can do the work rather than describe it.

There was one kind before too, but it was crippled in two directions at once:
its tool list was six hand-written entries with no `edit` in it, and its system
prompt told it it was a "research subagent" whose tools were read-only. So the
only thing it could return was a description of work, and the parent then did
that work again itself, from a summary, having paid for both halves.

A role belongs in the prompt the parent writes, not in the tool set or in a
second kind of subagent to keep in step with the first. The one thing that
stays a hard boundary is where it may write, because a subagent cannot ask
anybody anything and nobody is watching while it runs.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_server.tools.base import ToolContext
from agent_server.tools.registry import MANAGER_TOOLS, TOOLS, execute_tool
from agent_server.tools.task import SUBAGENT_PROMPT, subagent_tools


@pytest.fixture
def sub_ctx(tmp_path):
    """A context that looks like one a subagent runs in."""
    ctx = ToolContext(session_id="s", project_dir=str(tmp_path), abort=asyncio.Event())
    ctx.subagent_tier = 1
    return ctx


def test_a_subagent_can_change_files():
    """The point of the change. An agent that can find the three files needing
    the same edit and cannot make it is an agent that hands back homework."""
    tools = subagent_tools()
    for needed in ("read", "edit", "write", "bash", "grep", "glob"):
        assert needed in tools, f"a subagent cannot {needed}"


def test_a_subagent_has_everything_the_parent_has_except_the_browser():
    """Derived from the registry rather than listed, so a tool added to the app
    reaches subagents too. The old list was six entries written by hand, and it
    had fallen behind by five."""
    parent = {n for n in TOOLS if n not in MANAGER_TOOLS}
    assert set(subagent_tools()) == parent - {"browser", "preview", "game", "task"}


@pytest.mark.parametrize("held_back", ["browser", "preview", "game"])
def test_a_subagent_cannot_take_over_the_screen(held_back):
    """Not caution -- collision. Each of these is a handle on something there
    is exactly one of per project, and the user is watching it.

    The browser is a live Chromium keyed by session id, so a subagent driving
    it is driving the parent's own: same page, same login, mid-task. `preview`
    is the window on screen, and starting one replaces what the parent had
    running, in front of somebody who was never told a second assistant
    existed. `game` calls `preview`, so it is the same thing wearing a hat.

    A subagent can still rewrite every file in the project. What it cannot do
    is show anybody anything -- that belongs to the assistant being talked to.
    """
    assert held_back not in subagent_tools()


def test_a_subagent_cannot_call_a_subagent():
    """One level, so a runaway costs one budget rather than a tree of them."""
    assert "task" not in subagent_tools()


def test_the_prompt_gives_it_a_job_and_not_a_personality():
    """What it does comes from what the parent asked for. A role baked in here
    is a role that argues with the instructions it was given."""
    lowered = SUBAGENT_PROMPT.lower()
    assert "read-only" not in lowered, "the prompt still says it cannot change anything"
    assert "research subagent" not in lowered, "it has a role baked in again"


def test_the_prompt_says_what_it_may_and_may_not_change():
    """It can edit anything in the project, so it has to be told to stay inside
    what it was asked to do -- somebody else is working in here."""
    lowered = SUBAGENT_PROMPT.lower()
    assert "only what the task" in lowered or "change only" in lowered
    assert "outside this project" in lowered


def test_the_tool_description_tells_the_parent_to_name_the_files():
    """The parent is the one who can prevent a collision, and only if it knows
    to. It cannot see what the subagent is doing while it runs."""
    description = TOOLS["task"].description.lower()
    assert "edit" in description, "the parent is not told the subagent can write"
    assert "change" in description
    assert "browser" in description, "the one exception is not mentioned"


# ── the boundary that stayed ────────────────────────────────────────────────


async def test_a_subagent_still_cannot_write_outside_the_project(sub_ctx, tmp_path):
    outside = tmp_path.parent / "not-mine.txt"
    result = await execute_tool(
        "write", {"filePath": str(outside), "content": "hello"}, sub_ctx)
    assert result.is_error
    assert "outside the project" in result.output
    assert not outside.exists()


async def test_a_subagent_can_write_inside_the_project(sub_ctx, tmp_path):
    inside = tmp_path / "mine.txt"
    result = await execute_tool(
        "write", {"filePath": str(inside), "content": "hello"}, sub_ctx)
    assert not result.is_error, result.output
    assert inside.read_text() == "hello"


async def test_a_subagent_can_run_a_command_that_does_something(sub_ctx, tmp_path):
    """It was allowed `ls` and refused `npm install`, which is not a coherent
    position for something that can edit the files npm installs against."""
    result = await execute_tool(
        "bash", {"command": "echo built > out.txt"}, sub_ctx)
    assert not result.is_error, result.output
    assert (tmp_path / "out.txt").exists()


async def test_the_destructive_guard_still_applies_to_a_subagent(sub_ctx):
    """Being allowed to run commands is not being allowed to run that one."""
    result = await execute_tool("bash", {"command": "rm -rf /"}, sub_ctx)
    assert result.is_error
    assert "destructive" in result.output


def test_the_prompt_says_it_cannot_show_anybody_anything():
    """So it reports back rather than trying and finding the tool absent."""
    lowered = SUBAGENT_PROMPT.lower()
    assert "user's screen" in lowered or "on the user" in lowered


def test_nothing_calls_the_old_read_only_gate():
    """Removed rather than left switched off, so it cannot be revived by
    somebody reading the code and assuming it was load-bearing."""
    source = Path("agent_server/tools/registry.py").read_text()
    assert "may only run read-only commands" not in source
