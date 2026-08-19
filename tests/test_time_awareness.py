"""Telling the assistant how much time has passed.

A model has no sense of elapsed time, and users assume it does: they come back
a week later and say "carry on with that", or wonder why it does not know it is
a new day. The note is added to the outgoing request only -- never to the
stored message -- so it cannot appear in the user's own bubble as though they
had typed it.
"""

import pytest

from agent_server.conversation import build_messages, elapsed_note

BASE = "2026-08-19T10:00:00+00:00"


def _at(iso, role="user", content="carry on"):
    return {"role": role, "content": content, "created_at": iso}


@pytest.mark.parametrize("later,expected", [
    ("2026-08-19T10:05:00+00:00", ""),                 # a moment
    ("2026-08-19T10:59:00+00:00", ""),                 # still under an hour
    ("2026-08-19T11:20:00+00:00", "about an hour later"),
    ("2026-08-19T16:00:00+00:00", "6 hours later"),
    ("2026-08-20T11:00:00+00:00", "the next day"),
    ("2026-08-22T10:00:00+00:00", "3 days later"),
    ("2026-09-09T10:00:00+00:00", "3 weeks later"),
    ("2027-04-19T10:00:00+00:00", "8 months later"),
    ("2029-08-19T10:00:00+00:00", "3 years later"),
])
def test_gaps_are_described_the_way_a_person_would(later, expected):
    assert elapsed_note(BASE, later) == expected


def test_a_short_pause_says_nothing():
    """Someone making a cup of tea is not a gap worth mentioning, and a note on
    every message would be noise the model has to read past every turn."""
    assert elapsed_note(BASE, "2026-08-19T10:30:00+00:00") == ""


@pytest.mark.parametrize("bad", ["", "not-a-date", None])
def test_unparseable_timestamps_are_silent(bad):
    assert elapsed_note(bad, BASE) == ""
    assert elapsed_note(BASE, bad) == ""


def test_time_never_runs_backwards_into_a_note():
    assert elapsed_note("2026-08-22T10:00:00+00:00", BASE) == ""


def test_the_note_is_attached_to_the_outgoing_user_message():
    rows = [
        _at(BASE, content="build me a website"),
        _at(BASE, role="assistant", content="done"),
        _at("2026-08-22T10:00:00+00:00", content="carry on"),
    ]
    messages = build_messages("system", [], rows)
    last = messages[-1]
    assert last["role"] == "user"
    assert last["content"].startswith("(3 days later)\n")
    assert last["content"].endswith("carry on")


def test_the_stored_row_is_not_modified():
    """The note exists only on the wire. Writing it into the row would put
    words in the user's mouth in their own message bubble."""
    rows = [
        _at(BASE, content="first"),
        _at("2026-08-22T10:00:00+00:00", content="second"),
    ]
    build_messages("system", [], rows)
    assert rows[1]["content"] == "second"


def test_the_first_message_of_a_conversation_is_never_annotated():
    rows = [_at(BASE, content="hello")]
    assert build_messages("system", [], rows)[-1]["content"] == "hello"


def test_only_user_messages_are_annotated():
    rows = [
        _at(BASE, content="hello"),
        _at("2026-08-22T10:00:00+00:00", role="assistant", content="a reply"),
    ]
    assert build_messages("system", [], rows)[-1]["content"] == "a reply"
