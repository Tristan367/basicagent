"""Argument validation for the `browser` tool.

These run without a real browser: every case here is rejected before a session
is ever opened, which is the property being tested. Validation must happen
before any side effect, because `reset` throws away cookies and history and a
call that was going to be rejected anyway should not cost the model a login.
"""

import asyncio

import pytest

from agent_server.tools.base import ToolContext
from agent_server.tools.browser import MAX_STEPS, browser


@pytest.fixture
def ctx():
    return ToolContext(
        session_id="test-session",
        project_dir="/tmp",
        provider="deepseek",
        model="deepseek-v4-pro",
        abort=asyncio.Event(),
    )


async def test_empty_steps_is_rejected(ctx):
    result = await browser(ctx, steps=[])
    assert result.is_error
    assert "empty" in result.output.lower()


async def test_missing_steps_is_rejected(ctx):
    result = await browser(ctx, steps=None)
    assert result.is_error


async def test_json_string_of_an_empty_list_is_rejected(ctx):
    """A literal "[]" must be caught as empty.

    The emptiness check used to run before the string was parsed, so this got
    through and ran zero steps while reporting no error at all.
    """
    result = await browser(ctx, steps="[]")
    assert result.is_error
    assert "empty" in result.output.lower()


async def test_unparseable_string_is_rejected(ctx):
    result = await browser(ctx, steps="{not json")
    assert result.is_error
    assert "list of objects" in result.output


async def test_wrong_type_is_rejected(ctx):
    result = await browser(ctx, steps={"action": "goto"})
    assert result.is_error
    assert "list of objects" in result.output


async def test_too_many_steps_is_rejected(ctx):
    result = await browser(ctx, steps=[{"action": "snapshot"}] * (MAX_STEPS + 1))
    assert result.is_error
    assert str(MAX_STEPS) in result.output


async def test_reset_does_not_run_when_the_call_is_invalid(ctx, monkeypatch):
    """The whole point of validating first: a rejected call must not have
    already destroyed the browser session on its way to being rejected."""
    from agent_server import browser as engine

    called = False

    async def spy(_session_id):
        nonlocal called
        called = True

    monkeypatch.setattr(engine, "reset_session", spy)
    result = await browser(ctx, steps=[], reset=True)
    assert result.is_error
    assert called is False


# ── the schema has to describe what the code actually reads ─────────────────


def _step_schema() -> dict:
    from agent_server.tools.registry import TOOLS

    return TOOLS["browser"].parameters["properties"]["steps"]["items"]["properties"]


def test_every_key_the_tool_reads_is_in_its_schema():
    """A model can only use what the schema tells it about.

    `goto` read a `wait` key that was never declared, so the documented way to
    wait for the network to settle silently did nothing; `shoot` read `compare`,
    which is the whole before-and-after workflow, and no model could find it.
    Both were invisible because nothing compared the two halves.
    """
    import re
    from pathlib import Path

    source = Path("agent_server/tools/browser.py").read_text()
    read = set(re.findall(r"""step(?:\.get\(|\[)["']([a-z_]+)["']""", source))
    # `wait` is accepted on goto as a spelling of `until`, because a model that
    # has read the action list guesses it. Tolerated, deliberately not offered.
    declared = set(_step_schema()) | {"action", "wait"}
    assert not (read - declared), f"read but never declared: {sorted(read - declared)}"


def test_every_key_in_the_schema_is_read_somewhere():
    """The other direction: a declared key nothing acts on is a promise the
    tool does not keep, and the model spends a call finding that out."""
    import re
    from pathlib import Path

    source = Path("agent_server/tools/browser.py").read_text()
    read = set(re.findall(r"""step(?:\.get\(|\[)["']([a-z_]+)["']""", source))
    # `action` is dispatched on, not read as data.
    unused = set(_step_schema()) - read - {"action"}
    assert not unused, f"declared but never read: {sorted(unused)}"


def test_a_key_press_does_not_need_a_target():
    """Escape, Tab and arrow keys go to whatever has focus -- there is no
    element to name, and keyboard navigation is the thing this app most needs
    to be able to test. Listing `press` as targeted made that branch dead."""
    from agent_server.tools.browser import _TARGETED

    assert "press" not in _TARGETED
