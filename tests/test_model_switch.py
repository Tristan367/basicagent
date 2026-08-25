"""Changing AI part-way through a conversation, and what it costs.

The new model has never seen any of the conversation, so the whole thing goes
up again at its rate. On a long one that is more than a day's ordinary use, and
it used to happen silently -- the app picked whichever route was cheaper and
told nobody either number, so the bill was the first anyone heard of it.

Three ways to pay it now, and the cheapest is free: wait for the conversation
to be summarised on its own, which rebuilds and re-sends the prefix anyway.
"""

from __future__ import annotations

import pytest

from agent_server import compaction
from agent_server import database as db
from agent_server.routes import sessions as routes


@pytest.fixture
def with_models(monkeypatch):
    """A machine with a Google key, so the picker has something in it.

    Without this every test that needs a second model to switch to skips, and a
    file of skipped tests defends nothing.
    """
    from agent_server import providers

    keep = {k: v for k, v in providers._providers.items()
            if not k.startswith("custom:")}
    monkeypatch.setattr(providers, "_providers", keep)
    for name, provider in keep.items():
        monkeypatch.setattr(provider, "api_key",
                            (lambda n: (lambda: "k" if n == "gemini" else ""))(name))
    return keep


@pytest.fixture
async def long_session(db, with_models):
    """A conversation with enough in it for the numbers to be real."""
    session = await db.create_session(name="s", project_dir="/tmp",
                                      provider="gemini", model="gemini-3.5-flash-lite")
    for i in range(14):
        await db.add_message(session["id"], "user", f"question {i} " * 300)
        await db.add_message(session["id"], "assistant", f"answer {i} " * 900)
    return session


def _a_model(current: str) -> str:
    from agent_server.model_catalog import offerable_models

    for m in offerable_models():
        if m["id"] != current and not m["id"].startswith("custom:"):
            return m["id"]
    pytest.skip("no second model to switch to")


# ── the number comes out before anything happens ───────────────────────────


async def test_the_quote_says_what_each_route_costs(long_session):
    target = _a_model(long_session["model"])
    quote = await routes.quote_model_switch(long_session["id"], model=target)
    assert quote["context_tokens"] > 0
    assert quote["direct_cost"] > 0
    assert quote["compact_cost"] > 0
    assert quote["name"]


async def test_shortening_first_is_only_offered_when_it_is_cheaper(long_session):
    """A button that costs more than the one above it is a trap, and one that
    does nothing at all is worse."""
    target = _a_model(long_session["model"])
    quote = await routes.quote_model_switch(long_session["id"], model=target)
    if quote["can_tidy"]:
        assert quote["compact_cost"] < quote["direct_cost"]


async def test_a_conversation_is_never_quoted_as_free_when_it_is_not(db, with_models):
    """The measured context comes from the last reply's usage, and a session
    with messages and no reply yet measures zero. Quoted as-is that reads
    "free", which is the one wrong answer to give somebody about to spend
    money."""
    session = await db.create_session(name="s", project_dir="/tmp",
                                      provider="gemini", model="gemini-3.5-flash-lite")
    for _ in range(10):
        await db.add_message(session["id"], "user", "a long question " * 400)
    assert (await db.get_session_usage(session["id"]))["context"] == 0

    plan = await compaction.estimate_switch_costs(session["id"],
                                                  _a_model(session["model"]))
    assert plan["context_tokens"] > 1000
    assert plan["direct_cost"] > 0


async def test_an_unknown_model_is_not_quoted(long_session):
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        await routes.quote_model_switch(long_session["id"], model="not-a-model")


# ── switching later, which is the free one ─────────────────────────────────


async def test_choosing_later_changes_nothing_yet(long_session):
    target = _a_model(long_session["model"])
    await routes.switch_model(long_session["id"], None,
                              {"model": target, "how": "later"})
    after = await db.get_session(long_session["id"])
    assert after["model"] == long_session["model"], "it switched anyway"
    assert after["pending_model"] == target


async def test_the_waiting_switch_happens_at_the_next_summary(long_session):
    """The moment it is free: the prefix is being rebuilt and re-sent anyway,
    so the new model reads it instead of the old one and nothing is paid
    twice."""
    target = _a_model(long_session["model"])
    await db.set_pending_model(long_session["id"], target)

    name = await compaction.apply_pending_model(long_session["id"])
    after = await db.get_session(long_session["id"])
    assert after["model"] == target
    assert name
    assert not after["pending_model"], "it would switch again next time"


async def test_a_summary_reports_the_switch_it_carried_out(db):
    """A reply arriving from a different AI with no announcement is the sort of
    thing people notice and mistrust. The user asked for this an hour ago."""
    import inspect

    source = inspect.getsource(compaction.compact_session_events)
    assert "apply_pending_model" in source
    assert "switched_to" in source


async def test_a_withdrawn_model_does_not_wait_forever(long_session):
    """Cleared even when it cannot be honoured. Otherwise it sits there failing
    quietly after every compaction until the end of time."""
    await db.set_pending_model(long_session["id"], "some-model-that-was-retired")
    assert await compaction.apply_pending_model(long_session["id"]) == ""
    assert not (await db.get_session(long_session["id"]))["pending_model"]


async def test_switching_now_cancels_one_that_was_waiting(long_session):
    """Otherwise the session moves once when asked and again at the next
    summary, and the second move is a surprise."""
    first = _a_model(long_session["model"])
    await db.set_pending_model(long_session["id"], first)

    response = await routes.switch_model(long_session["id"], None,
                                         {"model": first, "how": "now"})
    async for _ in response.body_iterator:
        pass
    assert not (await db.get_session(long_session["id"]))["pending_model"]


async def test_nothing_waiting_is_a_no_op(long_session):
    assert await compaction.apply_pending_model(long_session["id"]) == ""


# ── a queued switch is still a switch ──────────────────────────────────────


def test_a_waiting_switch_cannot_be_set_over_plain_http():
    """In child mode a model change is exactly what the parent password gates.
    `update_session` filters against SESSION_FIELDS because its arguments can
    come from a PATCH body, so a queued switch listed there would have made the
    lock one request away from pointless -- the same reasoning that keeps
    `profile` out of it."""
    assert "pending_model" not in db.SESSION_FIELDS


async def test_the_menu_says_what_is_waiting(long_session):
    """A choice that leaves no trace is a choice people reasonably assume did
    not happen."""
    target = _a_model(long_session["model"])
    await db.set_pending_model(long_session["id"], target)
    listing = await routes.session_models(long_session["id"])
    assert listing["pending_model"] == target
    assert listing["pending_name"]


# ── what the menu says before anything is clicked ──────────────────────────


def test_the_menu_warns_before_the_click_rather_than_after():
    """A menu that looks like a free preference setting teaches people it is
    one."""
    from pathlib import Path

    js = Path("web_ui/static/js/app.js").read_text()
    at = js.index("function renderModelMenu(")
    body = js[at:at + 2500]
    assert "model-menu-note" in body
    assert "costs something" in body


def test_the_dialog_offers_three_routes_not_a_yes_and_a_no():
    """"Switch or don't" makes somebody who simply prefers the other model
    either pay or give up, when waiting costs nothing at all."""
    from pathlib import Path

    html = Path("web_ui/templates/chat.html").read_text()
    for choice in ("model-choice-now", "model-choice-tidy", "model-choice-later"):
        assert choice in html
    assert "model-switch-confirm" not in html, "the old yes/no is still there"


def test_the_cost_is_said_in_money_people_use():
    """Four decimal places is what the arithmetic produces and not what anybody
    can read."""
    from pathlib import Path

    js = Path("web_ui/static/js/app.js").read_text()
    at = js.index("function money(")
    body = js[at:at + 700]
    assert "under a cent" in body
    assert "' cent'" in body and "' cents'" in body


def test_the_switch_is_announced_when_it_finally_happens():
    """The user queued this an hour ago and carried on working. A reply
    arriving from a different AI with nothing said is the sort of thing people
    notice and mistrust -- and the client had no handler for the event at all,
    so it would have landed in silence."""
    from pathlib import Path

    js = Path("web_ui/static/js/app.js").read_text()
    assert "case 'compacted':" in js
    assert "announceSwitch(ev.switched_to)" in js
    at = js.index("function announceSwitch(")
    body = js[at:at + 900]
    # In the transcript, not a status line: somebody scrolling back tomorrow to
    # work out why the replies changed character should find it.
    assert "messages.appendChild" in body
    # And the button in the composer stops naming the old one.
    assert "model-label" in body


async def test_the_quote_says_how_far_off_the_free_option_is(long_session):
    """"Switch later" read as homework -- do I have to come back and do this
    myself? Saying how much further the conversation can run before it happens
    is the difference between waiting deliberately and wondering."""
    target = _a_model(long_session["model"])
    quote = await routes.quote_model_switch(long_session["id"], model=target)
    assert quote["tokens_until_shortened"] >= 0
    usage = await db.get_session_usage(long_session["id"])
    assert quote["tokens_until_shortened"] <= usage["threshold"]


def test_the_free_option_says_what_happens_rather_than_when_to_come_back():
    """Nothing here is for the user to remember."""
    from pathlib import Path

    html = Path("web_ui/templates/chat.html").read_text()
    at = html.index('id="model-choice-later"')
    block = html[at:at + 900]
    assert "Switch automatically" in block
    assert "Nothing for you to\n                        remember" in block \
        or "Nothing for you to remember" in " ".join(block.split())
    assert "costs nothing" in block
