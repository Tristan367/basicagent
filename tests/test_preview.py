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
    from. The stop has to happen before the row goes."""
    from agent_server import database
    from agent_server.tools.base import ToolContext
    from agent_server.tools.session_manager import create_project, delete_project

    await database.init_db()
    ctx = ToolContext(session_id="home", project_dir=project, abort=asyncio.Event())
    await create_project(ctx, name="Doomed")
    session = await database.get_session_by_name("Doomed", profile=None)

    port = free_port()
    before = running_servers()
    await preview.start(session["id"], f"PORT={port} python preview_server.py",
                        f"http://127.0.0.1:{port}", project)
    assert running_servers() == before + 1

    await delete_project(ctx, name="Doomed")
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
