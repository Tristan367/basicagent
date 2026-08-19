"""Persistence: message ordering, profile isolation, and the revert path.

Message *order* is the load-bearing property here. The provider wire format
requires each tool result to directly follow the assistant message that asked
for it, so rows are ordered by autoincrement id and never by timestamp — two
messages written inside the same clock tick would otherwise be free to swap and
the request would be rejected as malformed.
"""

import pytest


@pytest.fixture
async def session(db):
    return await db.create_session(name="Test", project_dir="/tmp/test-project")


async def test_messages_keep_insertion_order(db, session):
    for i in range(30):
        await db.add_message(session["id"], "user", f"m{i}")
    rows = await db.get_messages(session["id"])
    assert [r["content"] for r in rows] == [f"m{i}" for i in range(30)]


async def test_tool_result_follows_its_assistant_message(db, session):
    await db.add_message(session["id"], "user", "do it")
    await db.add_message(
        session["id"], "assistant", "",
        tool_calls=[{"id": "c1", "function": {"name": "read", "arguments": "{}"}}],
    )
    await db.add_message(session["id"], "tool", "file contents",
                         tool_call_id="c1", tool_name="read")
    roles = [r["role"] for r in await db.get_messages(session["id"])]
    assert roles == ["user", "assistant", "tool"]


async def test_revert_removes_an_unanswered_user_message(db, session):
    await db.add_message(session["id"], "user", "hello")
    assert await db.revert_last_user_message(session["id"]) is True
    assert await db.get_messages(session["id"]) == []


async def test_revert_leaves_an_answered_message_alone(db, session):
    await db.add_message(session["id"], "user", "hello")
    await db.add_message(session["id"], "assistant", "hi")
    assert await db.revert_last_user_message(session["id"]) is False
    assert len(await db.get_messages(session["id"])) == 2


async def test_revert_on_empty_session_is_a_no_op(db, session):
    assert await db.revert_last_user_message(session["id"]) is False


async def test_projects_are_isolated_by_profile(db):
    """A child must never see, open, or delete a parent's project."""
    await db.create_session(name="Parent work", project_dir="/tmp/p", profile="parent")
    await db.create_session(name="Child work", project_dir="/tmp/c", profile="child")

    parent = [s["name"] for s in await db.list_sessions(profile="parent")]
    child = [s["name"] for s in await db.list_sessions(profile="child")]

    assert "Parent work" in parent and "Child work" not in parent
    assert "Child work" in child and "Parent work" not in child


async def test_lookup_by_name_respects_profile(db):
    await db.create_session(name="Shared", project_dir="/tmp/p", profile="parent")
    assert await db.get_session_by_name("Shared", profile="child") is None
    assert await db.get_session_by_name("Shared", profile="parent") is not None


async def test_manager_sessions_are_not_listed_as_projects(db):
    await db.create_session(name="Home", project_dir="/tmp/h", kind="manager")
    assert await db.list_sessions() == []


async def test_turn_changes_counts_only_the_current_turn(db, session):
    await db.add_message(session["id"], "user", "first")
    await db.add_message(session["id"], "tool", "", tool_name="edit",
                         file_path="/tmp/old.py", diff="+++ a\n+one\n-two\n")
    await db.add_message(session["id"], "user", "second")
    await db.add_message(session["id"], "tool", "", tool_name="edit",
                         file_path="/tmp/new.py", diff="+++ b\n+alpha\n+beta\n-gamma\n")

    changes = await db.get_turn_changes(session["id"])
    assert [f["path"] for f in changes["files"]] == ["/tmp/new.py"]
    assert changes["added"] == 2
    assert changes["removed"] == 1


async def test_deleting_a_session_takes_its_messages(db, session):
    await db.add_message(session["id"], "user", "hello")
    await db.delete_session(session["id"])
    assert await db.get_session(session["id"]) is None
    assert await db.get_messages(session["id"]) == []


async def test_settings_roundtrip(db):
    await db.set_setting("theme", "light")
    assert await db.get_setting("theme") == "light"
    await db.set_setting("theme", "dark")
    assert await db.get_setting("theme") == "dark"
    await db.delete_setting("theme")
    assert await db.get_setting("theme", "fallback") == "fallback"


async def test_update_session_ignores_unknown_columns(db, session):
    """Field filtering is what keeps a PATCH body from writing arbitrary
    columns; without it `profile` would be settable over HTTP and child mode
    would be one request away from being escaped."""
    await db.update_session(session["id"], name="Renamed", profile="child")
    row = await db.get_session(session["id"])
    assert row["name"] == "Renamed"
    assert row["profile"] == "parent"


async def test_compacted_messages_leave_the_live_context(db, session):
    rows = [await db.add_message(session["id"], "user", f"m{i}") for i in range(4)]
    await db.mark_messages_compacted(session["id"], [rows[0]["id"], rows[1]["id"]])
    assert [r["content"] for r in await db.get_messages(session["id"])] == ["m2", "m3"]


async def test_compaction_summary_still_shows_in_history(db, session):
    """The model only keeps the summary, but the user should still be able to
    see that the earlier part of the conversation happened."""
    rows = [await db.add_message(session["id"], "user", f"m{i}") for i in range(3)]
    await db.mark_messages_compacted(session["id"], [rows[0]["id"]])
    await db.add_compaction(session["id"], "they discussed m0", rows[0]["id"],
                            rows[0]["id"], 100, 10)
    kinds = [item["kind"] for item in await db.get_session_history(session["id"])]
    assert "summary" in kinds
