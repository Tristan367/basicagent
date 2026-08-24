"""Stop, when the provider has gone quiet.

Found by hanging a stub provider -- it accepts the request and then sends
nothing, ever -- and pressing Stop. Nothing happened. The button stayed as
Stop, Send never came back, and reloading the page brought back the same stuck
state, because the state was real: the turn was genuinely still running and
would be for the ten minutes of the client's timeout.

The cause was that the abort check sat *inside* the loop over the provider's
events, so it could only run between two of them. No events, no checks. The
loop was parked inside a single `await` and never got the chance to look.

It is not an exotic failure. A proxy holding a connection open, a provider
under load, a captive-portal wifi that swallowed the socket after the request
had already left -- on a laptop that is an ordinary week. And the person this
app is for cannot open a terminal, does not know what a server is, and was
told the app never needs closing.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from agent_server.agent import _stoppable


async def _never_answers():
    """A provider that accepted the request and then went silent."""
    await asyncio.sleep(3600)
    yield {"type": "content", "text": "never reached"}


async def _answers_slowly():
    for word in ("one ", "two ", "three "):
        await asyncio.sleep(0.05)
        yield {"type": "content", "text": word}


async def _answers_at_once():
    yield {"type": "content", "text": "hello"}
    yield {"type": "finish", "reason": "stop"}


async def test_stop_ends_a_stream_that_has_sent_nothing():
    """The whole bug. Without this the wait is ten minutes long."""
    abort = asyncio.Event()

    async def press_stop():
        await asyncio.sleep(0.05)
        abort.set()

    asyncio.get_running_loop().create_task(press_stop())

    got = []
    async with asyncio.timeout(5):
        async for event in _stoppable(_never_answers(), abort):
            got.append(event)
    assert got == [], "a silent stream should end with nothing, not hang"


async def test_stop_ends_a_stream_that_is_part_way_through():
    """The ordinary case: they have seen a few words and changed their mind."""
    abort = asyncio.Event()
    got = []
    async with asyncio.timeout(5):
        async for event in _stoppable(_answers_slowly(), abort):
            got.append(event)
            abort.set()
    assert len(got) == 1, f"it kept going after Stop: {got}"


async def test_a_stream_nobody_stops_arrives_whole():
    """The fix must not cost anything in the normal case, which is every turn."""
    abort = asyncio.Event()
    got = [e async for e in _stoppable(_answers_at_once(), abort)]
    assert [e["type"] for e in got] == ["content", "finish"]


async def test_stopping_closes_the_stream_rather_than_leaving_it_open():
    """An abandoned generator holds its socket until the timeout expires. On a
    provider billed by the minute that is somebody's money, and on a laptop it
    is a connection that stays half-open through a sleep."""
    closed = []

    async def watched():
        try:
            await asyncio.sleep(3600)
            yield {"type": "content", "text": "x"}
        finally:
            closed.append(True)

    abort = asyncio.Event()

    async def press_stop():
        await asyncio.sleep(0.05)
        abort.set()

    asyncio.get_running_loop().create_task(press_stop())
    async with asyncio.timeout(5):
        async for _ in _stoppable(watched(), abort):
            pass
    assert closed == [True], "the provider stream was left open"


async def test_an_already_pressed_stop_sends_nothing_at_all():
    """Stop pressed while the request was still going out."""
    abort = asyncio.Event()
    abort.set()
    got = [e async for e in _stoppable(_answers_at_once(), abort)]
    assert got == []


def test_the_turn_loop_actually_uses_it():
    """The old code read `async for event in provider.chat_completion(...)`
    with the abort check inside, which looks correct and is not. Pinned here
    because the broken version is the one anybody would write."""
    from agent_server import agent

    source = inspect.getsource(agent._loop)
    assert "_stoppable(" in source, "the provider stream is unguarded again"
    assert "async for event in provider.chat_completion" not in source


def test_a_silence_in_the_middle_of_a_reply_does_not_last_ten_minutes():
    """Nobody may be watching. A turn that is never coming back must end on
    its own, or the session is held by it until the app restarts.

    Two different limits, and they were the same number before: a whole reply
    may fairly take ten minutes, and any single gap inside one may not.
    """
    from agent_server.providers.openai_compat import _timeouts

    timeout = _timeouts()
    assert timeout.read is not None and timeout.read <= 300, (
        f"a gap of {timeout.read}s counts as still working")
    assert timeout.read >= 60, "a slow model would be cut off mid-thought"
    assert timeout.connect is not None and timeout.connect <= 60


@pytest.mark.parametrize("module", ["openai_compat", "openrouter"])
def test_every_client_gets_the_same_limits(module):
    """OpenRouter builds its own client, so it missed the first fix."""
    import importlib

    source = inspect.getsource(
        importlib.import_module(f"agent_server.providers.{module}"))
    assert "_timeouts()" in source, f"{module} sets its own timeout"
    assert "timeout=600.0" not in source
