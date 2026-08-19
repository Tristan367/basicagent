"""Child mode: password hashing and profile routing.

The password gates model switching and API keys, and there is a deliberate way
out if a parent forgets it. Both halves need to hold: the hash must not be
reversible or comparable in constant-visible time, and the profile split must
keep a child's projects and a parent's completely separate.
"""

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
