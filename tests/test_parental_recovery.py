"""Getting back in when the parent has forgotten the password.

The lock has to be real or it is not worth having, and it has to be escapable
or the family loses their own app. Both halves matter and the second is the one
nobody tests: a parent who set a password in March, turned child mode on, and
cannot remember it in September is not a rare case, it is the ordinary end of a
password nobody types.

So there is a way out that takes a day. Long enough that a child who wants the
lock gone has to want it for twenty-four hours, which in practice means they
ask; short enough that a parent is never permanently shut out of the machine
they own.

Written after a long run of changes elsewhere, to check this still works.
"""

from __future__ import annotations

import time

import pytest

from agent_server import database as db
from agent_server import parental
from agent_server.routes import settings as routes


class _Body:
    def __init__(self, data):
        self._data = data

    async def json(self):
        return self._data


@pytest.fixture
async def keyed(db, monkeypatch):
    """Child mode needs a working AI before it will turn on."""
    monkeypatch.setattr(routes, "any_credentials", lambda: True)
    return db


async def enable(password="hunter2"):
    return await routes.child_enable(_Body({"password": password}))


# ── the lock itself ────────────────────────────────────────────────────────


async def test_turning_it_on_sets_the_password(keyed):
    assert (await enable())["ok"] is True
    assert await parental.child_mode_enabled()
    assert await parental.parent_password_set()
    assert await parental.parent_password_correct("hunter2")


async def test_it_will_not_turn_on_without_an_ai(db, monkeypatch):
    """Child mode with no key is a locked door in front of an empty room: the
    child cannot use the app and cannot be told why."""
    monkeypatch.setattr(routes, "any_credentials", lambda: False)
    assert (await enable())["ok"] is False
    assert not await parental.child_mode_enabled()


async def test_a_password_has_to_be_worth_something(keyed):
    assert (await enable("x"))["ok"] is False
    assert not await parental.child_mode_enabled()


async def test_the_wrong_password_does_not_turn_it_off(keyed):
    await enable()
    assert (await routes.child_disable(_Body({"password": "guess"})))["ok"] is False
    assert await parental.child_mode_enabled(), "child mode came off with a guess"


async def test_the_right_password_turns_it_off_and_forgets_itself(keyed):
    """Switching off clears the password, so turning it on again asks for a new
    one rather than silently reusing one nobody remembers choosing."""
    await enable()
    assert (await routes.child_disable(_Body({"password": "hunter2"})))["ok"] is True
    assert not await parental.child_mode_enabled()
    assert not await parental.parent_password_set()


# ── the way out ────────────────────────────────────────────────────────────


async def test_asking_for_a_reset_starts_a_day_long_clock(keyed):
    await enable()
    result = await routes.child_forgot()
    assert result["ok"] is True
    assert result["override_remaining"] == parental.OVERRIDE_SECONDS
    assert 23 * 3600 < await parental.override_remaining() <= 24 * 3600
    assert not await parental.override_elapsed()


async def test_the_lock_holds_until_the_clock_runs_out(keyed):
    """Otherwise it is not a wait, it is a button that turns the lock off."""
    await enable()
    await routes.child_forgot()
    assert await parental.child_mode_enabled()
    assert await parental.parent_password_correct("hunter2")
    assert (await routes.child_disable(_Body({"password": "guess"})))["ok"] is False
    assert await parental.child_mode_enabled()


async def test_after_a_day_child_mode_simply_ends(keyed):
    """Not "now choose a new password". At the moment the wait is up, whoever
    is at the keyboard can type one -- and after watching a countdown for
    twenty-four hours that is most likely the child. A lock that hands its key
    to whoever waited is not a lock. The wait is the protection."""
    await enable()
    await routes.child_forgot()
    await db.set_setting("child_override_until", str(int(time.time()) - 1))

    assert await parental.child_mode_enabled() is False
    assert not await parental.parent_password_set(), "the old password survived"


async def test_it_ends_without_anybody_asking_it_to(keyed):
    """No button to find, no page to be on. Every check for "is child mode on"
    goes through the release, so there is no path that can observe it still on
    after its time -- including a child who simply leaves the app open."""
    await enable()
    await routes.child_forgot()
    await db.set_setting("child_override_until", str(int(time.time()) - 1))

    # Nothing here is a settings page or a button; this is the app going about
    # its business.
    assert await parental.current_profile() == "parent"
    assert await parental.visible_profile() is None
    assert await db.get_setting("child_mode", "0") == "0"


async def test_the_clock_is_spent_once_it_has_fired(keyed):
    """A used wait left lying around is a permanent skeleton key: child mode
    turned back on would end again the moment anything looked at it."""
    await enable()
    await routes.child_forgot()
    await db.set_setting("child_override_until", str(int(time.time()) - 1))
    await parental.child_mode_enabled()

    assert await db.get_setting("child_override_until", "") == ""
    assert await parental.override_remaining() == 0

    await enable("afresh")
    assert await parental.child_mode_enabled(), "it ended again straight away"


async def test_a_corrupt_clock_is_not_an_unlocked_door(keyed):
    """Whatever ends up in that row, child mode may only end because a real
    wait really ran out."""
    await enable()
    for rubbish in ("soon", "NaN", "-", "2026-01-01", "9e99999"):
        await db.set_setting("child_override_until", rubbish)
        assert await parental.override_remaining() == 0
        assert await parental.override_elapsed() is False, rubbish
        assert await parental.child_mode_enabled() is True, rubbish
    assert await parental.parent_password_correct("hunter2")


async def test_turning_child_mode_on_again_clears_a_pending_clock(keyed):
    """A parent who found the password and switched off should not have a timer
    from last week quietly maturing behind them."""
    await enable()
    await routes.child_forgot()
    await routes.child_disable(_Body({"password": "hunter2"}))
    await enable("second")

    assert await parental.override_remaining() == 0
    assert not await parental.override_elapsed()


async def test_switching_off_clears_it_too(keyed):
    await enable()
    await routes.child_forgot()
    await routes.child_disable(_Body({"password": "hunter2"}))
    assert await db.get_setting("child_override_until", "") == ""


async def test_there_is_no_password_step_left_to_hijack(keyed):
    """The route that offered to set a new password is gone, not merely
    unused. It was the whole flaw: a box a child could type into after waiting
    a day."""
    assert not hasattr(routes, "child_reset")
    from pathlib import Path

    body = Path("web_ui/templates/settings_body.html").read_text()
    assert "child-reset" not in body
    assert "switch itself off" in body


# ── what the lock is actually guarding ─────────────────────────────────────


async def test_the_locked_settings_stay_locked(keyed):
    """Everything the password gates, checked in one place, because each of
    these is a separate door and they have been added at different times."""
    from agent_server.routes import sessions as session_routes

    await enable()

    # The house rules.
    assert (await routes.child_note_save(_Body({"note": "anything"})))["ok"] is False
    # A custom endpoint, which is a way to point the app at any AI at all.
    response = await routes.save_custom_endpoint(
        name="sneaky", base_url="http://example.test/v1", parent_password="")
    assert "error=locked" in response.headers["location"]
    # And the model, including one queued for later.
    session = await db.create_session("p", "/tmp", "gemini", "m", profile="child")
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        await session_routes.switch_model(
            session["id"], None, {"model": "gemini-3.5-flash-lite", "how": "later"})
    assert not (await db.get_session(session["id"]))["pending_model"]


async def test_a_child_cannot_reach_a_parents_project(keyed):
    await enable()
    theirs = await db.create_session("Parent's", "/tmp", "gemini", "m", profile="parent")
    mine = await db.create_session("Mine", "/tmp", "gemini", "m", profile="child")
    assert not await parental.may_reach(theirs)
    assert await parental.may_reach(mine)


async def test_a_parent_can_still_reach_a_childs_project(db):
    """Deliberately not symmetric: a parent has to be able to open what their
    child made, look through it, and set up a lesson in it."""
    await db.set_setting("child_mode", "0")
    theirs = await db.create_session("Theirs", "/tmp", "gemini", "m", profile="child")
    assert await parental.may_reach(theirs)
