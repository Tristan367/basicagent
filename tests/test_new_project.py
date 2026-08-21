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
    assert "{% if not child_mode %}" in block[:block.index("new-project-own-folder")]


def test_using_your_own_folder_is_off_until_you_ask_for_it():
    """The dialog used to offer two radio buttons, the first of which said it
    would "put its files somewhere sensible for me" -- which reads as the app
    scattering your work somewhere you will never find it again. Now there is
    one unticked box, and nothing is said about where the files go unless you
    ask to choose."""
    import re
    from pathlib import Path

    page = Path("web_ui/templates/base.html").read_text()
    block = page[page.index('id="new-project-modal"'):]
    block = block[:block.index("new-project-error")]
    # The comments explain why the old wording went; only what a user can read
    # is being checked here.
    block = re.sub(r"\{#.*?#\}", "", block, flags=re.S)

    box = re.search(r'<input type="checkbox" id="new-project-own-folder"[^>]*>', block)
    assert box, "the folder choice is not a single checkbox"
    assert "checked" not in box.group(0), "using your own folder is on by default"
    assert "somewhere sensible" not in block


# ── a folder that is not there yet ─────────────────────────────────────────
#
# Naming a folder that does not exist is sometimes how somebody starts a
# project, and much more often a typo in a path. So it is asked about, never
# assumed: quietly creating "Porject" leaves them with an empty project beside
# their real one and no idea why it is empty.


async def test_a_missing_folder_is_a_question_not_a_new_folder(client, tmp_path):
    wanted = tmp_path / "brand-new-thing"
    resp = await make(client, name="Brand new", folder=str(wanted))
    assert resp.status_code == 409
    assert not wanted.exists(), "it made the folder without asking"
    assert str(wanted) in resp.json()["detail"]


async def test_saying_yes_makes_it(client, tmp_path):
    wanted = tmp_path / "brand-new-thing"
    resp = await make(client, name="Brand new", folder=str(wanted), make_folder=True)
    assert resp.status_code == 200
    assert wanted.is_dir()
    assert resp.json()["project_dir"] == str(wanted.resolve())


async def test_saying_yes_to_a_typo_still_only_makes_one_level(client, tmp_path):
    """The answer is to that folder, not to a whole path of them."""
    deep = tmp_path / "not" / "there"
    resp = await make(client, name="Typo", folder=str(deep), make_folder=True)
    assert resp.status_code == 400
    assert not (tmp_path / "not").exists()


async def test_only_one_level_is_made(client, tmp_path):
    """A path with a typo halfway along should come back as a question, not as
    five new directories nobody asked for."""
    deep = tmp_path / "not" / "there" / "at" / "all"
    resp = await make(client, name="Typo", folder=str(deep))
    assert resp.status_code == 400
    assert not deep.exists()
    assert not (tmp_path / "not").exists()


async def test_a_file_where_a_folder_should_be_says_so(client, tmp_path):
    afile = tmp_path / "notes.txt"
    afile.write_text("hello")
    resp = await make(client, name="Confused", folder=str(afile))
    assert resp.status_code == 400
    assert "file" in resp.json()["detail"].lower()
    assert afile.read_text() == "hello"


async def test_the_home_directory_is_still_refused_even_though_it_exists(client):
    """The order matters: the "make it if it is missing" branch must not run
    before the guard that says home and / are never a project root."""
    from pathlib import Path

    for where in (Path.home(), Path("/")):
        resp = await make(client, name="Everything", folder=str(where))
        assert resp.status_code == 400


# ── what a project may be called ───────────────────────────────────────────
#
# Somebody naming a project is not naming a file and should not have to think
# like one. Turning "Mum's holiday photos" into "mums-holiday-photos" on screen
# does not read as tidying up -- it reads as the app being broken, to exactly
# the person this app is for.


def test_a_name_keeps_its_spaces_capitals_and_punctuation():
    from agent_server.tools.session_manager import clean_name

    for name in ("Mum's holiday photos", "Space Invaders 2", "Tristan & Anthony",
                 "Recipe book (2026)", "Ålesund trip", "my game 🎮"):
        assert clean_name(name) == name


def test_the_ends_are_trimmed_and_runs_of_space_collapse():
    from agent_server.tools.session_manager import clean_name

    assert clean_name("  My   website  ") == "My website"
    assert clean_name("A\tb\nc") == "A b c"


def test_the_characters_that_would_make_a_mess_of_a_path_go():
    from agent_server.tools.session_manager import clean_name

    assert clean_name("../../etc") == ".. .. etc"
    assert clean_name("a/b\\c") == "a b c"
    assert clean_name("bell\x07here") == "bell here"


def test_a_name_of_nothing_but_space_is_still_nothing():
    from agent_server.tools.session_manager import clean_name

    assert clean_name("   ") == ""
    assert clean_name("///") == ""


async def test_the_name_you_typed_is_the_name_you_get(client, projects_dir):
    resp = await make(client, name="  Mum's   holiday photos ")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Mum's holiday photos"


async def test_the_folder_is_slugged_even_though_the_name_is_not(client, projects_dir):
    """The folder is a separate question, and the user never sees it."""
    resp = await make(client, name="Mum's holiday photos")
    assert resp.json()["name"] == "Mum's holiday photos"
    assert resp.json()["project_dir"].endswith("mum-s-holiday-photos")
