"""Whose project is whose, and who can see it.

The two ideas this file defends are separate on purpose: **whose** a project is
(the `profile` on the session) and **whether child mode is switched on** (a
global setting). Conflating them means a parent cannot set a lesson for a
fourteen-year-old without also putting the safety locks on, which is not what
they asked for.
"""

import asyncio

import pytest

from agent_server import database as db
from agent_server.config import CHILD_HOME_SESSION_ID, HOME_SESSION_ID
from agent_server.tools.base import ToolContext
from agent_server.tools.session_manager import (
    assign_project,
    create_project,
    delete_projects,
    list_projects,
)


def ctx_for(session_id: str, tmp_path) -> ToolContext:
    return ToolContext(
        session_id=session_id, project_dir=str(tmp_path), abort=asyncio.Event()
    )


# Both take `db`: it gives each test its own database file and, just as
# importantly, closes the connection afterwards. aiosqlite's worker thread is
# not a daemon, so a suite that leaves one open never exits.
@pytest.fixture
def parent(db, tmp_path):
    return ctx_for(HOME_SESSION_ID, tmp_path)


@pytest.fixture
def child(db, tmp_path):
    return ctx_for(CHILD_HOME_SESSION_ID, tmp_path)


async def owner_of(name: str) -> str:
    session = await db.get_session_by_name(name, profile=None)
    assert session is not None, f"no project named {name!r}"
    return session["profile"]


async def test_a_parent_can_make_a_project_that_belongs_to_the_child(parent):
    """The lesson case. Nothing about child mode is involved."""
    result = await create_project(parent, name="Volcano lesson", for_child=True)
    assert not result.is_error
    assert await owner_of("Volcano lesson") == "child"


async def test_a_parents_own_projects_stay_their_own(parent):
    await create_project(parent, name="Tax spreadsheet")
    assert await owner_of("Tax spreadsheet") == "parent"


async def test_the_childs_manager_cannot_make_a_project_for_anyone_else(child):
    """`for_child=False` from the child's own manager must not produce a project
    in the parent's list -- that would be a way out of the separation."""
    await create_project(child, name="My racing game", for_child=False)
    assert await owner_of("My racing game") == "child"


async def test_a_project_can_be_handed_over_and_taken_back(parent):
    await create_project(parent, name="Fractions")
    assert await owner_of("Fractions") == "parent"

    await assign_project(parent, name="Fractions", to="child")
    assert await owner_of("Fractions") == "child"

    await assign_project(parent, name="Fractions", to="me")
    assert await owner_of("Fractions") == "parent"


async def test_handing_over_does_not_move_the_files(parent):
    """Moving the folder would break the project's git history and whatever
    `preview` was told to run, to change a label."""
    await create_project(parent, name="Solar system")
    before = (await db.get_session_by_name("Solar system", profile=None))["project_dir"]
    await assign_project(parent, name="Solar system", to="child")
    after = (await db.get_session_by_name("Solar system", profile=None))["project_dir"]
    assert before == after


async def test_a_child_cannot_move_projects_at_all(child, parent):
    await create_project(parent, name="Locked away")
    result = await assign_project(child, name="Locked away", to="child")
    assert result.is_error
    assert await owner_of("Locked away") == "parent"


async def test_the_parent_sees_the_childs_projects_and_the_child_does_not_see_theirs(
    parent, child
):
    await create_project(parent, name="Grown up thing")
    await create_project(parent, name="Homework thing", for_child=True)

    seen_by_parent = (await list_projects(parent)).output
    assert "Grown up thing" in seen_by_parent
    assert "Homework thing" in seen_by_parent
    assert "[the child's]" in seen_by_parent, "the parent cannot tell them apart"

    seen_by_child = (await list_projects(child)).output
    assert "Homework thing" in seen_by_child
    assert "Grown up thing" not in seen_by_child


async def test_a_child_cannot_delete_a_project_that_is_not_theirs(parent, child):
    """The child's manager sees only the child's projects, so a parent's is not
    a name it can even reach -- and the tool proposes rather than deletes, so a
    proposal is as far as anything gets in either case."""
    await create_project(parent, name="Important work")
    result = await delete_projects(child, names=["Important work"])
    assert result.is_error
    assert result.action is None, "a child was offered a button to remove it"
    assert await db.get_session_by_name("Important work", profile=None) is not None


async def test_whose_a_project_is_cannot_be_changed_over_http(db, parent):
    """`update_session` takes its arguments from a PATCH body, so anything it
    accepts is writable from outside. If `profile` were in that set, child mode
    would be one request away from being escaped -- which is why moving a
    project goes through `set_session_profile` instead."""
    await create_project(parent, name="Sensitive")
    session = await db.get_session_by_name("Sensitive", profile=None)

    await db.update_session(session["id"], name="Still mine", profile="child")
    after = await db.get_session(session["id"])
    assert after["name"] == "Still mine"
    assert after["profile"] == "parent", "profile was writable through update_session"


async def test_set_session_profile_refuses_anything_it_does_not_know(db, parent):
    await create_project(parent, name="Bounded")
    session = await db.get_session_by_name("Bounded", profile=None)
    with pytest.raises(ValueError):
        await db.set_session_profile(session["id"], "administrator")
