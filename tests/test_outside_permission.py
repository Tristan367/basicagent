"""Reaching for a file outside the project.

Inside a project the AI works without asking, and that is the whole design of
this app: somebody who could not judge "may I read src/main.js?" should not be
asked it forty times a day. Outside the project is a different question, with a
different answer, and it belongs to the person whose computer it is.

The line is real and it is worth being exact about where it falls. The file
tools go through it, which is how the assistant reads and writes files
essentially all of the time. A shell command can still reach the filesystem --
policing every command is not something this app can do honestly.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_server import parental, permissions
from agent_server.tools.base import ToolContext
from agent_server.tools.registry import execute_tool

# Captured before the fixture below stubs it out, so the tests about what is on
# the list can put the real one back.
REAL_NEVER_ASKED = permissions.never_asked


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(permissions, "_pending", {})
    monkeypatch.setattr(permissions, "_answers", {})
    permissions.set_asker(None)
    # Every "outside" file in this file lives under pytest's tmp_path, which is
    # inside the temp folder -- and the temp folder is one of the places nobody
    # is ever asked about. Emptied here so these tests are about the asking;
    # what is on that list has its own tests below.
    async def nothing():
        return []

    monkeypatch.setattr(permissions, "never_asked", nothing)
    yield
    permissions.set_asker(None)


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "mine.txt").write_text("this is mine")
    return folder


@pytest.fixture
def ctx(project):
    return ToolContext(session_id="p", project_dir=str(project), abort=asyncio.Event())


def answers(reply: str, seen: list):
    """Stand in for the person at the keyboard."""
    def asker(session_id, request):
        seen.append(request)
        permissions.answer(session_id, request["id"], reply)
    return asker


# ── inside the project, nothing changes ────────────────────────────────────


async def test_its_own_files_are_never_asked_about(db, ctx, project):
    """The app is for people who could not answer the question. Asking it about
    their own project would be asking it constantly, and the answer would stop
    being read by Wednesday."""
    seen = []
    permissions.set_asker(answers("no", seen))
    result = await execute_tool("read", {"filePath": "mine.txt"}, ctx)
    assert not result.is_error
    assert "this is mine" in result.output
    assert not seen


async def test_a_path_that_climbs_out_is_still_out(db, ctx, project, tmp_path):
    """`../../etc/hosts` is outside the project however it is spelled."""
    (tmp_path / "elsewhere.txt").write_text("not yours")
    seen = []
    permissions.set_asker(answers("no", seen))
    result = await execute_tool("read", {"filePath": "../elsewhere.txt"}, ctx)
    assert result.is_error
    assert seen, "it was not asked about"


# ── outside it, the person decides ─────────────────────────────────────────


async def test_yes_once_opens_it_and_asks_again_next_time(db, ctx, tmp_path):
    (tmp_path / "notes.txt").write_text("hello from outside")
    seen = []
    permissions.set_asker(answers("once", seen))

    first = await execute_tool("read", {"filePath": str(tmp_path / "notes.txt")}, ctx)
    assert not first.is_error
    assert "hello from outside" in first.output

    await execute_tool("read", {"filePath": str(tmp_path / "notes.txt")}, ctx)
    assert len(seen) == 2, "once meant once, so it should have asked again"


async def test_always_remembers_the_folder(db, ctx, tmp_path):
    """A project that reads twenty files out of one folder must ask once. Being
    asked twenty times is how people learn to approve without reading."""
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    seen = []
    permissions.set_asker(answers("always", seen))

    await execute_tool("read", {"filePath": str(tmp_path / "a.txt")}, ctx)
    await execute_tool("read", {"filePath": str(tmp_path / "b.txt")}, ctx)
    assert len(seen) == 1
    assert str(tmp_path) in await permissions.allowed_folders()


async def test_no_refuses_and_says_so_as_an_answer(db, ctx, tmp_path):
    """A model handed a bare failure tries another way. It has to be told this
    was a decision."""
    (tmp_path / "private.txt").write_text("private")
    permissions.set_asker(answers("no", []))

    result = await execute_tool("read", {"filePath": str(tmp_path / "private.txt")}, ctx)
    assert result.is_error
    assert "did not allow" in result.output
    assert "an answer, not an obstacle" in result.output
    assert "private" not in result.output.replace("private.txt", "")


async def test_writing_outside_is_asked_about_too(db, ctx, tmp_path):
    seen = []
    permissions.set_asker(answers("no", seen))
    result = await execute_tool(
        "write", {"filePath": str(tmp_path / "new.txt"), "content": "x"}, ctx)
    assert result.is_error
    assert seen and seen[0]["verb"] == "write to"
    assert not (tmp_path / "new.txt").exists(), "it wrote the file anyway"


# ── the three cases where nobody is asked ──────────────────────────────────


async def test_child_mode_asks_rather_than_walls_them_in(db, ctx, tmp_path):
    """A child with photos in their downloads folder, or an old project to
    build on, is asking for something entirely reasonable. Walling them into
    one folder would teach them the app is broken rather than careful --  and
    approving file access is a normal part of using these tools, which is
    itself the lesson."""
    (tmp_path / "notes-from-school.txt").write_text("my homework")
    await db.set_setting("child_mode", "1")
    seen = []
    permissions.set_asker(answers("once", seen))

    result = await execute_tool("read", {"filePath": str(tmp_path / "notes-from-school.txt")}, ctx)
    assert not result.is_error, result.output
    assert seen, "it did not even ask"
    assert seen[0]["locked"] is True, "the dialog was not told a password is needed"


async def test_a_subagent_cannot_ask(db, project, tmp_path):
    """It runs on its own with nobody watching, so there is nobody to ask."""
    (tmp_path / "x.txt").write_text("x")
    ctx = ToolContext(session_id="p", project_dir=str(project),
                      subagent_tier=1, abort=asyncio.Event())
    seen = []
    permissions.set_asker(answers("always", seen))

    result = await execute_tool("read", {"filePath": str(tmp_path / "x.txt")}, ctx)
    assert result.is_error
    assert not seen
    assert "cannot ask permission" in result.output


async def test_with_no_screen_the_answer_is_no(db, ctx, tmp_path):
    """Nothing registered to show the question means nobody to answer it. A
    refusal is honest; a turn that hangs forever is not."""
    (tmp_path / "y.txt").write_text("y")
    permissions.set_asker(None)
    result = await execute_tool("read", {"filePath": str(tmp_path / "y.txt")}, ctx)
    assert result.is_error


async def test_the_system_paths_are_refused_without_asking(db, ctx):
    """Nothing may write there whatever anybody says, so offering a choice
    would be offering one that does not exist."""
    seen = []
    permissions.set_asker(answers("always", seen))
    result = await execute_tool(
        "write", {"filePath": "/proc/sysrq-trigger", "content": "b"}, ctx)
    assert result.is_error
    assert not seen
    assert "part of the operating system" in result.output


# ── the question a person has to answer ────────────────────────────────────


def test_the_dialog_names_the_file_and_says_what_would_happen():
    """It has to be answerable by somebody who has no idea whether it is safe."""
    html = Path("web_ui/templates/chat.html").read_text()
    at = html.index('id="permission-modal"')
    box = html[at:at + 3000]
    assert "outside your project" in box
    assert 'id="permission-path"' in box
    for choice in ("permission-once", "permission-always", "permission-no"):
        assert choice in box
    # The recommendation is made out loud rather than left to be guessed at.
    assert "recommended" in box
    # And it does not pretend to know that the file is harmless.
    assert "it could be something private" in box
    assert "your call and not ours" in box


def test_enter_reaches_the_safest_answer_not_the_recommended_one():
    js = Path("web_ui/static/js/app.js").read_text()
    at = js.index("function askPermission(")
    body = js[at:js.index("async function recoverPermission(")]
    assert "__openModal(modal, document.getElementById('permission-once'))" in body


def test_a_reloaded_page_asks_again():
    """The turn is still sitting inside the tool call waiting. Losing the
    question would leave the app frozen with nothing to explain why."""
    js = Path("web_ui/static/js/app.js").read_text()
    assert "async function recoverPermission()" in js
    assert "recoverPermission();" in js


def test_the_question_does_not_outlive_its_turn(db):
    """Left behind, the next page load would ask about a file nothing is
    waiting to open any more."""
    import inspect

    from agent_server import agent

    assert "permissions.forget(session_id)" in inspect.getsource(agent.run)


# ── the folders nobody is ever asked about ─────────────────────────────────


@pytest.fixture
def real_list(monkeypatch):
    monkeypatch.setattr(permissions, "never_asked", REAL_NEVER_ASKED)


async def test_the_temp_folder_is_never_asked_about(db, real_list):
    """Every program on the computer uses it, this app uses it constantly, and
    nobody who is not technical has ever put anything private there -- most
    have never heard of it. "May I read /tmp/tmp8fz2?" is a question with no
    useful answer, and a question with no useful answer teaches people that
    these boxes are things you click past."""
    import tempfile

    assert await permissions.already_allowed(Path(tempfile.gettempdir()) / "x.txt")
    assert await permissions.already_allowed(Path("/tmp/anything/at/all.txt"))


async def test_an_attachment_is_never_asked_about(db, real_list):
    """Somebody who has just dropped a photo into the chat has consented in the
    plainest way there is. Asking again is asking them to confirm what they
    just did."""
    from agent_server.config import ATTACH_DIR

    assert await permissions.already_allowed(ATTACH_DIR / "ab12_holiday.jpg")


async def test_their_own_projects_are_never_asked_about(db, real_list):
    """Reaching a game they made last month is not reaching out of the app."""
    from agent_server.config import PROJECTS_DIR

    await db.set_setting("child_mode", "0")
    assert await permissions.already_allowed(PROJECTS_DIR / "old-game" / "main.js")


async def test_a_child_reaches_their_own_old_projects_but_not_a_parents(db, real_list):
    """The same line the project list already draws: a child sees their own
    work and not their parent's."""
    from agent_server.config import PROJECTS_DIR

    # Asserted against the list itself rather than through `already_allowed`,
    # because in a test run the whole data directory sits inside the temp
    # folder -- which is itself on the list, and would say yes to everything.
    await db.set_setting("child_mode", "1")
    assert PROJECTS_DIR / "child" in await permissions.never_asked()
    assert PROJECTS_DIR not in await permissions.never_asked()

    await db.set_setting("child_mode", "0")
    assert PROJECTS_DIR in await permissions.never_asked()


async def test_somewhere_real_is_still_asked_about(db, real_list):
    """The list is short on purpose. A downloads folder is a real folder with
    real files in it and it gets a real question."""
    assert not await permissions.already_allowed(Path.home() / "Downloads" / "x.png")


# ── in child mode, yes costs the parent password ───────────────────────────


async def a_child_session() -> str:
    from agent_server import database as adb

    session = await adb.create_session("p", "/tmp/p", "gemini", "m", profile="child")
    return session["id"]


async def test_saying_yes_in_child_mode_needs_a_grown_up(db):
    from fastapi import HTTPException

    from agent_server.routes.chat import answer_permission

    sid = await a_child_session()
    await db.set_setting("child_mode", "1")
    await db.set_setting("parent_password_hash", parental.hash_password("hunter2"))

    with pytest.raises(HTTPException) as refused:
        await answer_permission(sid, {"id": "x", "answer": "always"})
    assert refused.value.status_code == 403


async def test_saying_no_in_child_mode_costs_nothing(db):
    """Stopping something must always be the cheap answer. A child who wants to
    say no and is asked for a password has been told they may not refuse."""
    from agent_server.routes.chat import answer_permission

    sid = await a_child_session()
    await db.set_setting("child_mode", "1")
    await db.set_setting("parent_password_hash", parental.hash_password("hunter2"))

    result = await answer_permission(sid, {"id": "x", "answer": "no"})
    assert result == {"ok": False}  # nothing was being asked; it did not refuse us


async def test_the_right_password_is_accepted(db, monkeypatch):
    from agent_server.routes.chat import answer_permission

    sid = await a_child_session()
    await db.set_setting("child_mode", "1")
    await db.set_setting("parent_password_hash", parental.hash_password("hunter2"))
    monkeypatch.setattr(permissions, "_pending", {sid: {"id": "x"}})
    monkeypatch.setattr(permissions, "_answers",
                        {sid: asyncio.get_running_loop().create_future()})

    result = await answer_permission(
        sid, {"id": "x", "answer": "once", "parent_password": "hunter2"})
    assert result == {"ok": True}


def test_the_dialog_is_told_a_password_is_coming():
    """A child pressing yes and only then meeting a password box has been told
    no in the most annoying way available."""
    js = Path("web_ui/static/js/app.js").read_text()
    at = js.index("function askPermission(")
    body = js[at:js.index("async function recoverPermission(")]
    assert "request.locked" in body
    assert "permission-lock" in body
    # And "no" is not behind it.
    assert "getElementById('permission-no').onclick = () => send('no')" in body
