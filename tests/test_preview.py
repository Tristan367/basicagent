"""Running the user's project, and stopping it again properly.

Every test here spawns a real process and then checks the operating system,
not a flag this module set. Process supervision that believes its own
bookkeeping is the kind that leaves a port held by an orphan and reports
success.

No window is opened: `_show` is replaced, because this machine has a display
and the thing under test is the process tree.
"""

import asyncio
import os
import socket
import subprocess
import sys
import textwrap

import pytest
from fastapi import HTTPException

from agent_server import preview

SERVER = textwrap.dedent("""
    import http.server, socketserver, os
    V, PORT = os.environ.get("V", "1"), int(os.environ["PORT"])
    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"
        def do_GET(s):
            s.send_response(200); s.end_headers(); s.wfile.write(f"BUILD {V}".encode())
        def log_message(s, *a): pass
    class S(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True
    with S(("127.0.0.1", PORT), H) as httpd:
        httpd.serve_forever()
""")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def running_servers() -> int:
    """Ask the operating system, not this module."""
    out = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
    return sum(1 for line in out.splitlines() if "preview_server.py" in line and "ps -eo" not in line)


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "preview_server.py").write_text(SERVER)

    async def no_window(session_id, url, confine=False):
        pass

    monkeypatch.setattr(preview, "_show", no_window)
    monkeypatch.setattr(preview, "PREVIEW_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(preview, "PREVIEW_PROFILES", tmp_path / "profiles")
    return str(tmp_path)


@pytest.fixture(autouse=True)
async def _no_leaks():
    yield
    await preview.close_all()


pytestmark = pytest.mark.skipif(
    os.name == "nt" or sys.platform == "darwin",
    reason="process-group signalling is checked against ps on Linux",
)


def fetch(port: int) -> str:
    import httpx

    return httpx.get(f"http://127.0.0.1:{port}", timeout=5).text


async def test_a_project_runs_and_serves_what_was_asked_for(project):
    port = free_port()
    before = running_servers()
    out = await preview.start(
        "s", f"V=1 PORT={port} python preview_server.py", f"http://127.0.0.1:{port}", project
    )
    assert "Running" in out
    assert running_servers() == before + 1
    assert fetch(port) == "BUILD 1"


async def test_stopping_takes_the_whole_process_tree(project):
    """`npm run dev` is a shell that spawns node. Killing only the shell leaves
    node holding the port, and the next start fails on an address already in
    use -- which reads as a bug in the user's project rather than in this."""
    port = free_port()
    before = running_servers()
    await preview.start(
        "s", f"sh -c 'V=1 PORT={port} python preview_server.py'",
        f"http://127.0.0.1:{port}", project,
    )
    assert running_servers() == before + 1

    await preview.stop("s")
    await asyncio.sleep(0.4)
    assert running_servers() == before, "the grandchild outlived the shell"


async def test_starting_again_replaces_rather_than_accumulates(project):
    """One running thing per project. Without this the user ends the evening
    with thirty of them and no idea which is the current build."""
    port = free_port()
    before = running_servers()
    for version in (1, 2, 3):
        await preview.start(
            "s", f"V={version} PORT={port} python preview_server.py",
            f"http://127.0.0.1:{port}", project,
        )
        assert running_servers() == before + 1
        assert fetch(port) == f"BUILD {version}"


async def test_two_projects_each_keep_their_own(project):
    a, b = free_port(), free_port()
    before = running_servers()
    await preview.start("one", f"V=1 PORT={a} python preview_server.py",
                        f"http://127.0.0.1:{a}", project)
    await preview.start("two", f"V=2 PORT={b} python preview_server.py",
                        f"http://127.0.0.1:{b}", project)
    assert running_servers() == before + 2
    assert (fetch(a), fetch(b)) == ("BUILD 1", "BUILD 2")

    await preview.stop("one")
    await asyncio.sleep(0.4)
    assert running_servers() == before + 1
    assert fetch(b) == "BUILD 2", "stopping one project stopped the other"


async def test_a_command_that_fails_reports_what_it_printed(project):
    """The output is the answer -- a missing module, a syntax error, a port in
    use. Reporting only "it did not start" makes the assistant guess."""
    with pytest.raises(preview.PreviewError) as caught:
        await preview.start("s", "python -c \"import nonexistent_module_xyz\"", "", project)
    assert "nonexistent_module_xyz" in str(caught.value)


async def test_a_url_that_never_answers_does_not_kill_a_running_project(project):
    """A server can be up and the address wrong. Stopping it would throw away a
    process that is working, and the recent output is what identifies the
    mistake."""
    port = free_port()
    out = await preview.start(
        "s", f"V=1 PORT={port} python preview_server.py",
        "http://127.0.0.1:9/", project, wait_ms=1200,
    )
    assert "has not answered" in out
    assert preview.is_running("s")


async def test_status_says_what_is_running(project):
    port = free_port()
    assert "Nothing is running" in preview.status("s")
    await preview.start("s", f"V=1 PORT={port} python preview_server.py",
                        f"http://127.0.0.1:{port}", project)
    assert "preview_server.py" in preview.status("s")


async def test_closing_the_app_leaves_nothing_behind(project):
    """A server that outlives the app holds a port the user cannot free,
    because they do not have a terminal to kill it from."""
    port = free_port()
    before = running_servers()
    await preview.start("s", f"V=1 PORT={port} python preview_server.py",
                        f"http://127.0.0.1:{port}", project)
    assert running_servers() == before + 1

    await preview.close_all()
    await asyncio.sleep(0.4)
    assert running_servers() == before


async def test_deleting_a_project_stops_what_it_was_running(project, tmp_path, monkeypatch):
    """Afterwards nothing knows the project existed, so its server would hold
    the port until the app closes -- and the user has no terminal to kill it
    from. The stop has to happen before the row goes.

    Through the bulk-remove route, which is where removal actually happens: the
    assistant's `delete_projects` only ever proposes a list, and the button in
    that box calls this."""
    from agent_server import database
    from agent_server.routes.sessions import remove_sessions
    from agent_server.tools.base import ToolContext
    from agent_server.tools.session_manager import create_project

    await database.init_db()
    ctx = ToolContext(session_id="home", project_dir=project, abort=asyncio.Event())
    await create_project(ctx, name="Doomed")
    session = await database.get_session_by_name("Doomed", profile=None)

    port = free_port()
    before = running_servers()
    await preview.start(session["id"], f"PORT={port} python preview_server.py",
                        f"http://127.0.0.1:{port}", project)
    assert running_servers() == before + 1

    await remove_sessions({"ids": [session["id"]]})
    await asyncio.sleep(0.4)
    assert running_servers() == before, "the project was deleted but its server lived on"
    await database.close()


# ── the button in the chat ──────────────────────────────────────────────────


def test_play_and_stop_are_one_button():
    """They used to be two, side by side in the row of composer tools, and both
    were on screen at once -- so half the time you were looking at a Stop button
    for something that was not running. One control now, which says which of the
    two it is."""
    from pathlib import Path

    page = Path("web_ui/templates/chat.html").read_text()
    assert page.count('id="play-fab"') == 1
    assert 'id="play-btn"' not in page and 'id="play-stop-btn"' not in page


def test_play_is_not_another_triangle_in_the_composer():
    """Play was a triangle, and "read my message aloud" two buttons along was
    the same triangle. Play has left the row; what stays must not be one."""
    from pathlib import Path

    page = Path("web_ui/templates/chat.html").read_text()
    tools = page[page.index('class="composer-tools"'):page.index('id="send-btn"')]
    assert "play-fab" not in tools, "Play is back in the composer row"
    draft = tools[tools.index('id="read-draft-btn"'):]
    draft = draft[:draft.index("</button>")]
    assert "M8 5v14l11-7z" not in draft, "read-my-message is a play triangle again"


# ── a link the user pressed in a reply ──────────────────────────────────────
#
# "Your site is running at http://localhost:8123" is an ordinary thing for the
# assistant to write and a perfectly reasonable thing to press. It used to open
# the user's normal browser, which -- if the project had since been stopped --
# showed a connection error and nothing else. Somebody who is not technical has
# no way to know the page is fine and the server is off, let alone that the fix
# is to find Play and press that first.


def test_an_address_on_this_machine_is_recognised():
    from agent_server import preview

    for url in ("http://localhost:8123", "http://127.0.0.1:5173/index.html",
                "http://[::1]:3000", "http://my-app.localhost:8080"):
        assert preview.is_this_machine(url), url


def test_an_address_out_on_the_web_is_not():
    from agent_server import preview

    for url in ("https://google.com", "http://example.com:8123",
                "https://raw.githubusercontent.com/x/y"):
        assert not preview.is_this_machine(url), url


def test_things_that_are_not_addresses_are_not_this_machine():
    """`_is_local` says yes to about: and data: because it is answering "may
    the window load this"; this one is answering "is this the project", and the
    answers are different."""
    from agent_server import preview

    for url in ("about:blank", "data:text/html,hi", "file:///etc/passwd", "", "javascript:x"):
        assert not preview.is_this_machine(url), url


@pytest.fixture
async def linked(db, tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    session = await db.create_session(name="Linked", project_dir=str(root))
    await db.update_session(session["id"], preview_command="run-me",
                            preview_url="http://localhost:8123")
    return session["id"]


@pytest.fixture
async def child_linked(db, tmp_path):
    """A project belonging to the child, which is the only kind they can open.

    Worth spelling out rather than reusing `linked` with the setting flipped:
    while child mode is on, a parent's project is not reachable at all, so a
    test that made one and then turned child mode on was checking the link
    rules against a session the child could never have got to in the first
    place.
    """
    root = tmp_path / "childproj"
    root.mkdir()
    session = await db.create_session(name="Child's", project_dir=str(root),
                                      profile="child")
    await db.update_session(session["id"], preview_command="run-me",
                            preview_url="http://localhost:8123")
    return session["id"]


async def test_pressing_a_project_link_starts_it_when_it_is_not_running(linked, monkeypatch):
    """The whole point. Otherwise this is a connection error with no
    explanation attached."""
    from agent_server import preview
    from agent_server.routes import sessions

    started = {}

    async def fake_start(session_id, command, url, cwd, confine=False, **kw):
        started.update(session_id=session_id, command=command, url=url, confine=confine)
        return "ok"

    monkeypatch.setattr(preview, "start", fake_start)
    monkeypatch.setattr(preview, "is_running", lambda sid: False)

    out = await sessions.open_link(linked, {"url": "http://localhost:8123/about.html"})
    assert out["started"] is True
    assert started["command"] == "run-me"
    assert started["url"] == "http://localhost:8123/about.html", \
        "it went to the address that was pressed, not the project's front page"


async def test_pressing_a_project_link_while_it_runs_just_shows_it(linked, monkeypatch):
    """No restart. The user is often looking at the window while they press."""
    from agent_server import preview
    from agent_server.routes import sessions

    shown = {}
    started = []

    async def fake_show(session_id, url, confine=False):
        shown.update(session_id=session_id, url=url)

    async def fake_start(*a, **k):
        started.append(a)
        return "ok"

    monkeypatch.setattr(preview, "show", fake_show)
    monkeypatch.setattr(preview, "start", fake_start)
    monkeypatch.setattr(preview, "is_running", lambda sid: True)

    out = await sessions.open_link(linked, {"url": "http://localhost:8123/"})
    assert out["started"] is False
    assert shown["url"] == "http://localhost:8123/"
    assert not started, "it restarted a project that was already running"


async def test_a_project_link_with_nothing_to_start_says_so(db, tmp_path, monkeypatch):
    from agent_server import preview
    from agent_server.routes import sessions

    session = await db.create_session(name="Nothing", project_dir=str(tmp_path))
    monkeypatch.setattr(preview, "is_running", lambda sid: False)
    with pytest.raises(HTTPException) as excinfo:
        await sessions.open_link(session["id"], {"url": "http://localhost:9999/"})
    assert excinfo.value.status_code == 409


async def test_a_link_out_to_the_web_goes_to_the_users_own_browser(linked, monkeypatch):
    from agent_server.routes import files, sessions

    opened = []

    async def fake_open(url):
        opened.append(url)
        return True

    monkeypatch.setattr(files, "open_in_browser", fake_open)
    out = await sessions.open_link(linked, {"url": "https://example.com/docs"})
    assert out["where"] == "browser"
    assert opened == ["https://example.com/docs"]


async def test_in_child_mode_a_link_out_to_the_web_goes_nowhere(child_linked, db, monkeypatch):
    """The project window refuses anything off this machine however the address
    arrives. Handing it to the real browser instead would walk straight around
    the one thing a parent is trusting this app about."""
    from agent_server.routes import files, sessions

    opened = []

    async def fake_open(url):
        opened.append(url)
        return True

    monkeypatch.setattr(files, "open_in_browser", fake_open)
    await db.set_setting("child_mode", "1")
    try:
        with pytest.raises(HTTPException) as excinfo:
            await sessions.open_link(child_linked, {"url": "https://example.com"})
        assert excinfo.value.status_code == 403
        assert not opened, "it opened the web anyway"
    finally:
        await db.set_setting("child_mode", "0")


async def test_a_child_pressing_their_own_project_link_still_works(child_linked, db, monkeypatch):
    from agent_server import preview
    from agent_server.routes import sessions

    confined = {}

    async def fake_start(session_id, command, url, cwd, confine=False, **kw):
        confined["confine"] = confine
        return "ok"

    monkeypatch.setattr(preview, "start", fake_start)
    monkeypatch.setattr(preview, "is_running", lambda sid: False)
    await db.set_setting("child_mode", "1")
    try:
        out = await sessions.open_link(child_linked, {"url": "http://localhost:8123/"})
        assert out["ok"] is True
        assert confined["confine"] is True, "the window was opened unconfined for a child"
    finally:
        await db.set_setting("child_mode", "0")


async def test_something_that_is_not_a_web_address_is_refused(linked):
    from agent_server.routes import sessions

    for url in ("file:///etc/passwd", "javascript:alert(1)", "", "ftp://x"):
        with pytest.raises(HTTPException) as excinfo:
            await sessions.open_link(linked, {"url": url})
        assert excinfo.value.status_code == 400


# ── saying why, when child mode is the reason ───────────────────────────────
#
# The person who meets one of these is as likely to be the grown-up who turned
# child mode on last week and forgot as the child it is for. "Not allowed" leaves
# them deciding the app is broken, which is the one conclusion neither of them
# should reach.


async def test_the_refusal_names_child_mode_and_where_to_turn_it_off(child_linked, db, monkeypatch):
    from agent_server.routes import files, sessions

    monkeypatch.setattr(files, "open_in_browser", lambda url: None)
    await db.set_setting("child_mode", "1")
    try:
        with pytest.raises(HTTPException) as excinfo:
            await sessions.open_link(child_linked, {"url": "https://example.com"})
    finally:
        await db.set_setting("child_mode", "0")
    said = excinfo.value.detail.lower()
    assert "child mode" in said
    assert "settings" in said and "parental controls" in said


def test_the_confined_window_shows_a_page_rather_than_a_connection_error():
    """`route.abort()` left somebody looking at the browser's own "this site
    can't be reached" for a site that is perfectly fine, with nothing anywhere
    to connect it to a setting they turned on."""
    import inspect

    from agent_server import preview

    guard = inspect.getsource(preview._confine)
    assert "route.fulfill" in guard
    assert "_blocked_page" in guard


def test_the_blocked_page_says_which_address_and_which_switch():
    import re

    from agent_server import preview

    page = preview._blocked_page("https://example.com/x")
    assert "https://example.com/x" in page
    # Whitespace flattened: the source wraps its sentences, and a line break
    # between two words is not a difference the reader sees.
    low = re.sub(r"\s+", " ", page).lower()
    assert "child mode" in low
    assert "settings" in low and "parental controls" in low


def test_the_blocked_page_cannot_be_used_to_put_markup_on_itself():
    """The address comes from whatever the page tried to navigate to, which is
    not ours."""
    from agent_server import preview

    page = preview._blocked_page('https://x/"><script>alert(1)</script>')
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


# ── Pointing at part of what is running ─────────────────────────────────────
#
# The pointing itself is a browser thing and lives in `test_annotate.py`. What
# is here is the rule that decides whether the button exists at all, which is
# the whole of the promise: present when it works, absent when it does not,
# and never present-but-apologetic.


async def test_a_web_project_can_be_pointed_at():
    assert preview.Slot("s", "cmd").pickable is True


async def test_a_game_says_it_cannot_be_pointed_at():
    """One canvas with the whole world painted inside it. Clicking it would
    answer "you pointed at the canvas" every single time."""
    slot = preview.Slot("s", "cmd", pickable=False)
    assert slot.pickable is False


async def test_nothing_running_means_nothing_to_point_at():
    assert preview.can_pick("nobody-here") is False


async def test_a_project_with_no_window_open_cannot_be_pointed_at(project):
    """`_show` is stubbed here, so the process runs with no window. The button
    must go by whether there is a page, not by whether the server is up."""
    port = free_port()
    env = dict(os.environ, PORT=str(port))
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "preview_server.py", cwd=project, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        slot = preview.Slot("s", "cmd", pickable=True, process=proc)
        preview._slots["s"] = slot
        assert preview.can_pick("s") is False
    finally:
        preview._slots.pop("s", None)
        proc.terminate()
        await proc.wait()


async def test_arming_a_window_that_is_not_there_says_so():
    with pytest.raises(preview.PreviewError, match="window is not open"):
        await preview.arm("no-such-project")


async def test_the_game_tool_opts_out_of_pointing():
    """Not a judgement the assistant makes per call: whether pointing means
    anything is a fact about what is being run."""
    import inspect

    from agent_server.tools import game

    source = inspect.getsource(game)
    starts = source.count("preview(ctx, action=\"start\"")
    assert starts == 2, "the game tool opens a window in two places"
    assert source.count("pickable=False") == 2, "and neither can be pointed at"


async def test_pointing_survives_the_app_being_restarted(db, tmp_path):
    """The Play button works tomorrow because the command is on the session
    row. The pointing button has to agree with it, or a game reopened from the
    row would offer a picker that answers "the canvas"."""
    root = tmp_path / "game"
    root.mkdir()
    session = await db.create_session(name="Game", project_dir=str(root))
    await db.update_session(session["id"], preview_command="serve",
                            preview_url="http://127.0.0.1:8300", preview_pickable=0)
    again = await db.get_session(session["id"])
    assert again["preview_pickable"] == 0


async def test_a_web_project_defaults_to_pointable_without_being_told(db, tmp_path):
    """Every project made before this column existed, and every one the
    assistant starts without mentioning it."""
    root = tmp_path / "site"
    root.mkdir()
    session = await db.create_session(name="Site", project_dir=str(root))
    assert (await db.get_session(session["id"]))["preview_pickable"] == 1
