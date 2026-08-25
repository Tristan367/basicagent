"""Send, Stop, and the message typed while the assistant is working.

Send used to be swapped out for Stop while a turn ran. Somebody who has not
noticed that the AI is busy -- which is most people, most of the time -- reaches
for where Send always is, presses a red square, and stops the thing they were
about to talk to. Their message does not send either, and they have no idea what
they did.

So the button under the thumb is always Send, and pressing it mid-turn queues
the message. The accidental press is the harmless one, which is the way round it
should be.
"""

from pathlib import Path

import pytest

from agent_server import agent

CHAT = Path("web_ui/templates/chat.html").read_text()
JS = Path("web_ui/static/js/app.js").read_text()


def test_send_is_never_taken_away():
    """The whole point. A button that moves out from under somebody mid-task is
    a button they will press by mistake."""
    at = JS.index("function beginTurn()")
    body = JS[at:JS.index("\n  /*", at)]
    assert "sendBtn.hidden = true" not in body
    assert "stopBtn.hidden = false" in body


def test_stop_sits_beside_send_not_on_top_of_it():
    at = CHAT.index('id="stop-btn"')
    assert CHAT.index('id="send-btn"') > at, "Stop is still after Send in the row"
    assert "stop-beside" in CHAT


def test_send_says_what_it_will_do_while_the_assistant_is_busy():
    assert "It will go in as soon as the assistant has finished" in JS


def test_escape_never_stops_the_assistant():
    """People press Escape by accident constantly. It closes dialogs, stops
    dictation and stops read-aloud -- and none of those is the work."""
    for at in [i for i in range(len(JS)) if JS.startswith("'Escape'", i)]:
        window = JS[at:at + 400]
        assert "/cancel" not in window, JS[max(0, at - 200):at + 200]


def test_stopping_says_what_it_means():
    """The two questions at that moment are "did I break something?" and "what
    now?". Both are answered on the line itself, so it is still true when they
    scroll back to it tomorrow."""
    said = "Anything it had already finished is kept, and it will not carry on unless you ask."
    assert said in JS
    # And the same words when the conversation is loaded back from the database.
    assert said in Path("web_ui/templates/chat_messages.html").read_text()


# ── a message typed mid-turn is answered, not dropped ──────────────────────


@pytest.fixture(autouse=True)
def empty_queue(monkeypatch):
    monkeypatch.setattr(agent, "_queued", {})


@pytest.fixture
def busy(monkeypatch):
    """A turn in flight. Queuing is refused when nothing is running, which is
    right -- there would be nothing to wait for."""
    monkeypatch.setattr(agent, "active_run", lambda session_id: object())


async def test_a_message_queued_mid_turn_is_answered_when_the_turn_ends(db):
    """It used to be left. The queue is only drained at the top of the loop and
    a plain reply ends the loop, so a message typed mid-turn stayed on screen
    marked as waiting and was never answered -- it reached the model only when
    the user gave up and sent something else, out of order behind it. A dropped
    message is about the worst thing this app can do."""
    import inspect

    source = inspect.getsource(agent._loop)
    ending = source[source.index("if not calls:"):]
    assert "_queued.get(session_id)" in ending
    assert ending.index("_queued.get(session_id)") < ending.index('"type": "done"')


async def test_the_queue_is_drained_in_order(db, busy):
    session = await db.create_session("q", "/tmp", "gemini", "m")
    agent.queue_message(session["id"], "first")
    agent.queue_message(session["id"], "second")

    rows = await agent._flush_queued(session["id"])
    assert len(rows) == 1
    assert rows[0]["content"] == "first\n\nsecond"
    assert not agent._queued.get(session["id"])


async def test_taking_a_queued_message_back_works(db, busy):
    """Somebody who changes their mind before it goes in."""
    session = await db.create_session("q", "/tmp", "gemini", "m")
    queue_id = agent.queue_message(session["id"], "wait, no")
    assert agent.unqueue_message(session["id"], queue_id) == "wait, no"
    assert not await agent._flush_queued(session["id"])


async def test_nothing_is_queued_when_nothing_is_running(db):
    """It would wait for a turn that is not coming."""
    session = await db.create_session("q", "/tmp", "gemini", "m")
    assert agent.queue_message(session["id"], "hello") is None


def test_enter_on_talk_starts_talking():
    """It submitted the form instead, so tabbing to Talk and pressing the
    obvious key sent an empty message and did not turn the microphone on. It
    looked exactly like a dead button -- and for somebody working by keyboard,
    Talk is how they use this app at all."""
    at = JS.index("micBtn.addEventListener('keydown'")
    handler = JS[at:JS.index("});", at)]
    assert "if (!listening) { toggleDictation(); return; }" in handler
    assert "form.requestSubmit()" not in handler


def test_enter_while_talking_stops_and_sends():
    """The half that was already right. You have said your piece, and the
    alternative is tabbing past every control on the row to find Send -- which
    somebody who cannot see the screen has to know is there in the first
    place."""
    at = JS.index("micBtn.addEventListener('keydown'")
    handler = JS[at:JS.index("});", at)]
    assert "sendWhenDictationEnds = true" in handler
    assert "stopDictation()" in handler
