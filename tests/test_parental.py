"""Child mode: password hashing and profile routing.

The password gates model switching and API keys, and there is a deliberate way
out if a parent forgets it. Both halves need to hold: the hash must not be
reversible or comparable in constant-visible time, and the profile split must
keep a child's projects and a parent's completely separate.
"""

import pytest
from fastapi import HTTPException

from agent_server.config import CHILD_HOME_SESSION_ID, HOME_SESSION_ID
from agent_server.parental import hash_password, profile_for_session, verify_password


def test_password_roundtrip():
    stored = hash_password("hunter2")
    assert verify_password("hunter2", stored) is True


def test_wrong_password_rejected():
    stored = hash_password("hunter2")
    assert verify_password("hunter3", stored) is False
    assert verify_password("", stored) is False


def test_password_is_not_stored_in_plain_text():
    stored = hash_password("hunter2")
    assert "hunter2" not in stored


def test_same_password_hashes_differently_each_time():
    """Per-password salt, so two parents choosing the same password do not get
    matching rows, and a stolen database cannot be attacked in one pass."""
    assert hash_password("hunter2") != hash_password("hunter2")


def test_verify_handles_missing_or_corrupt_records():
    assert verify_password("x", None) is False
    assert verify_password("x", "") is False
    assert verify_password("x", "no-dollar-sign") is False
    assert verify_password("x", "nothex$nothex") is False


def test_profile_routing_separates_child_from_parent():
    assert profile_for_session(CHILD_HOME_SESSION_ID) == "child"
    assert profile_for_session(HOME_SESSION_ID) == "parent"
    assert profile_for_session("some-project-id") == "parent"


def test_child_and_parent_home_sessions_are_distinct():
    assert CHILD_HOME_SESSION_ID != HOME_SESSION_ID


# ── reaching a session, as opposed to seeing it listed ──────────────────────
#
# The list was filtered by profile from the start. Nothing else was, and the
# list is not how anybody arrives at a page a second time -- the back button
# is, and the address bar, and whatever the browser decided to remember. In
# child mode every one of those opened the parent's Project Manager: full tool
# set, the parent's projects, and a prompt with no child-safety block in it,
# because that block is chosen once per session when the prompt is frozen.
#
# Found by asking for the address directly while child mode was on and getting
# a 200 back.


@pytest.fixture
async def two_profiles(db):
    parent = await db.create_session(name="Parent's", project_dir="/tmp/p",
                                     profile="parent")
    child = await db.create_session(name="Child's", project_dir="/tmp/c",
                                    profile="child")
    return parent, child


async def test_a_child_cannot_reach_a_parents_session(db, two_profiles):
    from agent_server import parental

    parent, child = two_profiles
    await db.set_setting("child_mode", "1")
    try:
        assert await parental.may_reach(child) is True
        assert await parental.may_reach(parent) is False
    finally:
        await db.set_setting("child_mode", "0")


async def test_a_parent_can_still_reach_a_childs_session(db, two_profiles):
    """The asymmetry is deliberate and `visible_profile` already describes it:
    a parent has to be able to open what their child made."""
    from agent_server import parental

    parent, child = two_profiles
    assert await parental.may_reach(parent) is True
    assert await parental.may_reach(child) is True


async def test_every_door_into_a_session_checks_the_profile(db, two_profiles):
    """Not the guard itself -- the places that have to call it. A guard that
    one route forgets is the same hole with more code in front of it."""
    from agent_server.routes import chat as chat_routes
    from agent_server.routes import files as file_routes
    from agent_server.routes import pages, sessions

    parent, _ = two_profiles
    await db.set_setting("child_mode", "1")
    try:
        with pytest.raises(HTTPException) as e:
            await sessions._require(parent["id"])
        assert e.value.status_code == 404

        with pytest.raises(HTTPException) as e:
            await chat_routes._require_session(parent["id"])
        assert e.value.status_code == 404, "the door that actually matters"

        with pytest.raises(HTTPException):
            await file_routes.export_project(parent["id"])

        sent = await pages.session_page(None, parent["id"])
        assert sent.status_code == 303, "the page did not send them home"
    finally:
        await db.set_setting("child_mode", "0")


async def test_a_child_reaches_their_own_session_through_the_same_doors(db, two_profiles):
    from agent_server.routes import chat as chat_routes
    from agent_server.routes import sessions

    _, child = two_profiles
    await db.set_setting("child_mode", "1")
    try:
        assert (await sessions._require(child["id"]))["id"] == child["id"]
        assert (await chat_routes._require_session(child["id"]))["id"] == child["id"]
    finally:
        await db.set_setting("child_mode", "0")
