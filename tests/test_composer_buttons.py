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


def test_enter_on_talk_does_what_the_button_says_and_nothing_else():
    """It stopped *and sent*, which is wrong twice over. The button says Stop.
    People press it because somebody has walked into the room, or because they
    said the wrong thing, or because they want to think -- and every one of
    those ended with half a sentence sent to an AI that started working on it.

    There was never a problem to solve. Shift+Tab reaches the message box and
    Enter sends from there, which is how every keyboard user already moves
    around a form."""
    at = JS.index("micBtn.addEventListener('keydown'")
    handler = JS[at:JS.index("});", at)]
    assert "toggleDictation()" in handler
    assert "requestSubmit" not in handler, "Stop sends again"
    assert "sendWhenDictationEnds" not in JS, "the machinery for it is still there"


def test_only_the_message_box_sends_on_enter():
    """One control sends, and it is the one you type into. Anything else that
    grew a send on Enter would be the same mistake wearing a different label."""
    senders = [i for i in range(len(JS)) if JS.startswith("requestSubmit", i)]
    assert len(senders) == 1, "something else submits the form on a key"
    before = JS[max(0, senders[0] - 400):senders[0]]
    assert "textarea.addEventListener('keydown'" in before
