"""This app cannot see images, and must not pretend otherwise.

Nothing here builds a request that carries an image: every provider sends
messages whose content is a plain string. So an attached photo reaches the model
as a path, and a screenshot the agent takes is a file nobody in the conversation
can look at.

That was true before too, but the code did not say so. It shipped a `capture`
tool and an `ask` option on screenshots which dispatched to a `vision` tool by
name -- a tool this repository never contained, and which only ever resolved on
one developer's own machine. On every real install those two features could only
answer "no vision tool is installed", after the model had spent a round trip
finding out.

These tests are the tombstone. If image support is ever built properly, it will
be by putting image parts in the request, and these should be rewritten to check
*that* -- not by reviving a tool that dispatches to something absent.
"""

from pathlib import Path

import pytest

from agent_server.tools.registry import TOOLS

SOURCE = [p for p in Path("agent_server").rglob("*.py")]


def test_no_tool_dispatches_to_a_tool_that_does_not_exist():
    """The specific failure: `execute_tool("vision", ...)` for a name never
    registered. A tool calling another tool by string is only safe if something
    guarantees the target exists, and nothing did."""
    referenced = set()
    for path in SOURCE:
        for line in path.read_text().splitlines():
            if "execute_tool(" in line and '"' in line:
                name = line.split('execute_tool(', 1)[1].split('"')
                if len(name) > 1:
                    referenced.add(name[1])
    assert referenced <= set(TOOLS), f"dispatches to unregistered tools: {referenced - set(TOOLS)}"


def test_nothing_in_the_app_mentions_a_vision_tool():
    """Including comments. The comments were the most misleading part: they
    described gating behaviour for a capability that was never present."""
    guilty = [str(p) for p in SOURCE if "vision" in p.read_text().lower()]
    assert not guilty, f"still refer to vision: {guilty}"


def test_the_browser_tool_offers_nothing_that_needs_eyes():
    """`ask` and `compare` on a screenshot were the whole of it, and both are
    gone. Everything left -- snapshot, expect, network, the console -- is text,
    which is what makes this tool work on a model like DeepSeek."""
    step = TOOLS["browser"].parameters["properties"]["steps"]["items"]["properties"]
    assert "ask" not in step and "compare" not in step


def test_a_screenshot_is_still_worth_taking():
    """Not for the model -- for the user. `shoot` returns a path, and a path on
    its own line renders as a picture in the chat, so the agent can show someone
    the website it just built without being able to see it itself."""
    actions = TOOLS["browser"].parameters["properties"]["steps"]["items"]["properties"]["action"]
    assert "shoot" in actions["enum"]


@pytest.mark.parametrize("prompt", ["agent.md", "manager.md"])
def test_the_model_is_told_it_cannot_see(prompt):
    """Otherwise it invents a description of a screenshot it was handed, and the
    user -- who can see the picture -- has no reason to doubt it."""
    text = Path("system_prompts", prompt).read_text().lower()
    assert "cannot see pictures" in text or "can't see pictures" in text
