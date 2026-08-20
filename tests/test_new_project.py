"""Making a project without going through the assistant.

The way this app is meant to work is that you say what you want and the Project
Manager sets it up. This is the other way -- for somebody who already knows what
they are doing, or who has a folder of code they want this pointed at.

Offered quietly rather than not at all: last in the menu, dimmer than the
projects above it, and called "empty", which is the whole difference. What it
must not do is become a way around the things that keep a child's world separate.
"""

import httpx
import pytest


@pytest.fixture
async def client(db):
    from agent_server.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def projects_dir(tmp_path, monkeypatch):
    import agent_server.config as config
    import agent_server.routes.sessions as sessions

    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(config, "PROJECTS_DIR", root)
    monkeypatch.setattr(sessions, "PROJECTS_DIR", root, raising=False)
    return root


async def make(client, **body):
    return await client.post("/api/sessions", json=body)


# ── the ordinary case ──────────────────────────────────────────────────────


async def test_a_named_project_gets_a_folder_and_opens(client, projects_dir):
    resp = await make(client, name="My new thing")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "My new thing"
    assert body["id"]
    from pathlib import Path

    assert Path(body["project_dir"]).is_dir()


async def test_a_project_can_be_pointed_at_a_folder_that_already_exists(client, tmp_path):
    """The half that matters to somebody with a codebase already: point this at
    it and work in place. Nothing in it is moved or rewritten."""
    existing = tmp_path / "my-code"
    existing.mkdir()
    (existing / "main.py").write_text("print('hi')")

    resp = await make(client, name="Existing code", folder=str(existing))
    assert resp.status_code == 200
    assert resp.json()["project_dir"] == str(existing.resolve())
    assert (existing / "main.py").read_text() == "print('hi')", "it touched the user's files"


async def test_a_name_is_required(client, projects_dir):
    for name in ("", "   "):
        resp = await make(client, name=name)
        assert resp.status_code == 400


async def test_an_absurd_name_is_refused_rather_than_truncated(client, projects_dir):
    resp = await make(client, name="x" * 400)
    assert resp.status_code == 400


# ── where the folder may be ────────────────────────────────────────────────


async def test_a_folder_that_is_not_there_says_so(client):
    resp = await make(client, name="Nowhere", folder="/definitely/not/here")
    assert resp.status_code == 400
    assert "no folder" in resp.json()["detail"].lower()


async def test_the_whole_home_directory_is_refused(client):
    """A project rooted at home gives every tool in the session the run of the
    machine, which is never what somebody meant by "use a folder I already
    have"."""
    from pathlib import Path

    resp = await make(client, name="Everything", folder=str(Path.home()))
    assert resp.status_code == 400


async def test_the_filesystem_root_is_refused(client):
    resp = await make(client, name="All of it", folder="/")
    assert resp.status_code == 400


async def test_a_name_cannot_climb_out_of_the_projects_folder(client, projects_dir):
    """The name becomes a directory name. `../../etc` must land inside the
    projects folder like anything else."""
    resp = await make(client, name="../../etc")
    assert resp.status_code == 200
    from pathlib import Path

    made = Path(resp.json()["project_dir"]).resolve()
    assert str(made).startswith(str(projects_dir.resolve()))


# ── child mode ─────────────────────────────────────────────────────────────


@pytest.fixture
async def child_mode(db):
    await db.set_setting("child_mode", "1")
    yield
    await db.set_setting("child_mode", "0")


async def test_a_child_cannot_point_a_project_anywhere_they_like(client, child_mode, tmp_path):
    """Exactly what the separation exists to prevent. The option is not offered
    to them, and the endpoint refuses it as well -- a control that is only
    hidden is not a control."""
    somewhere = tmp_path / "grown-up-things"
    somewhere.mkdir()

    resp = await make(client, name="Sneaky", folder=str(somewhere))
    assert resp.status_code == 403


async def test_a_child_can_still_make_an_ordinary_project(client, child_mode, projects_dir):
    resp = await make(client, name="Kid game")
    assert resp.status_code == 200
    assert "/child/" in resp.json()["project_dir"], "it did not land in the child's own area"


async def test_a_childs_project_belongs_to_the_child(client, child_mode, projects_dir, db):
    resp = await make(client, name="Kid game")
    session = await db.get_session(resp.json()["id"])
    assert session["profile"] == "child"


# ── where it is offered ────────────────────────────────────────────────────


def test_it_is_the_last_thing_in_the_menu_not_the_first():
    """"Create new" at the top would read as the obvious route to somebody who
    does not yet know the assistant will do this for them, which is the way this
    app is meant to work."""
    from pathlib import Path

    page = Path("web_ui/templates/base.html").read_text()
    menu = page[page.index('id="sessions-menu"'):]
    menu = menu[:menu.index("</div>")]
    assert menu.index("new-project-btn") > menu.index("nav_sessions"), \
        "the by-hand route sits above the user's own projects"


def test_the_folder_choice_is_not_rendered_in_child_mode():
    from pathlib import Path

    page = Path("web_ui/templates/base.html").read_text()
    block = page[page.index('id="new-project-modal"'):]
    assert "{% if not child_mode %}" in block[:block.index("new_project_where")]
