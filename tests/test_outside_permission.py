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


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(permissions, "_pending", {})
    monkeypatch.setattr(permissions, "_answers", {})
    permissions.set_asker(None)
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


async def test_child_mode_refuses_instead_of_asking(db, ctx, tmp_path):
    """The person at the keyboard is a child, the file is very often their
    parent's, and a dialog is an invitation to press yes."""
    (tmp_path / "taxes.txt").write_text("private")
    await db.set_setting("child_mode", "1")
    seen = []
    permissions.set_asker(answers("always", seen))

    result = await execute_tool("read", {"filePath": str(tmp_path / "taxes.txt")}, ctx)
    assert result.is_error
    assert not seen, "a child was offered the choice"
    assert "child mode is on" in result.output
    assert "a grown-up can turn child mode off" in result.output
    assert await parental.child_mode_enabled()


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
    body = js[at:at + 2200]
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
