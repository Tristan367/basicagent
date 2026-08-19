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
