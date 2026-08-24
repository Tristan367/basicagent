"""Things that broke when the app was deliberately handled badly.

Every test here started as a probe that found something: a crash, a guard that
could be stepped around, a value the app would accept and then choke on later.
They are grouped by what an ordinary user would notice.
"""

import asyncio
from pathlib import Path

import pytest

from agent_server.routes.settings import _local_referer, _number, save_custom_endpoint
from agent_server.tools.bash import danger_reason, is_read_only

# ── the destructive-command guard ──────────────────────────────────────────


@pytest.mark.parametrize("command", [
    "rm -rf /",
    "rm -rf '/'",
    'rm -rf "/"',
    "rm -rf ~",
    "rm -rf $HOME",
    "rm -rf '/usr'",
    "sudo rm -rf /",
    "/bin/rm -rf /",
    "rm --recursive --force /",
    ":(){ :|:& };:",
    "dd if=/dev/zero of=/dev/sda",
])
def test_the_catastrophes_are_refused(command):
    """Quoting is the interesting half: the tokens were split with str.split,
    which keeps the quotes, so `rm -rf "/"` was the token '"/"' -- not the
    string '/' -- and went straight through. Models quote paths by habit."""
    assert danger_reason(command) is not None


def test_the_users_home_directory_is_protected_when_spelled_out():
    """`~` and `$HOME` were covered; the expanded path was not, and a model
    that has just run `pwd` writes the expanded path."""
    import os

    assert danger_reason(f"rm -rf {os.path.expanduser('~')}") is not None


@pytest.mark.parametrize("command", [
    "rm -rf build/", "rm -rf node_modules", 'rm -rf "src/old"',
    "rm -rf /tmp/mine", "git clean -fd", "dd if=in of=out",
])
def test_ordinary_work_is_not_refused(command):
    """The guard fires on catastrophes only. One false refusal on a normal
    `rm -rf build/` and the model starts working around the tool."""
    assert danger_reason(command) is None


# ── what a subagent may run ────────────────────────────────────────────────


@pytest.mark.parametrize("command", [
    "ls\nrm -rf build",     # a newline separates commands exactly like ;
    "cat a & rm b",         # & both backgrounds and separates
    "cat <(rm -rf x)",      # process substitution runs a command of its own
    "ls #\nrm -rf x",
])
def test_a_subagent_cannot_smuggle_a_write_past_the_read_only_check(command):
    """Subagents are allowed observational commands only. The check listed
    `;`, `&&` and `||` but not a newline, so the first line decided the verdict
    for everything after it."""
    assert is_read_only(command) is False


@pytest.mark.parametrize("command", [
    "ls -la", "git status", "git log --oneline", "grep -r pat .",
    "cat file.txt", "ls | head -5", "find . -name '*.py'",
])
def test_reading_things_is_still_allowed(command):
    assert is_read_only(command) is True


# ── values the app would accept and then choke on ──────────────────────────


@pytest.mark.parametrize("raw", ["abc", "", "  ", "NaN", "1e999", "-inf", "nan"])
def test_a_slider_value_that_is_not_a_number_is_refused(raw):
    """These went into the database as text. Read-aloud then did float() on
    them at status time and 500'd on every request from then on -- broken for
    good, from a page that never offered the bad value."""
    assert _number(raw, 0.5, 2.0) is None


@pytest.mark.parametrize("raw, expected", [
    ("999999", "2"), ("-9", "0.5"), ("1.4", "1.4"), ("2.0", "2"),
])
def test_a_slider_value_out_of_range_is_clamped(raw, expected):
    assert _number(raw, 0.5, 2.0) == expected


class _Req:
    def __init__(self, referer, netloc="localhost:8000"):
        self.headers = {"referer": referer} if referer else {}
        self.url = type("U", (), {"netloc": netloc})()


@pytest.mark.parametrize("referer", [
    "https://evil.example/phish",
    "//evil.example/phish",
    "http://localhost:8000@evil.example/",
    "",
])
def test_saving_a_preference_cannot_send_you_off_the_app(referer):
    """The form redirects back to where it came from, and Referer is set by
    whoever sent the request."""
    assert _local_referer(_Req(referer)) == "/"


def test_saving_a_preference_returns_you_to_the_page_you_were_on():
    assert _local_referer(_Req("http://localhost:8000/settings?a=1")) == "/settings?a=1"


# ── an endpoint name that breaks the model picker ──────────────────────────


async def test_an_endpoint_name_with_a_slash_is_refused(db):
    """A picker value is `custom:<name>/<model>`, so a name with a slash makes
    an entry that cannot be resolved back: "my/box" splits into the endpoint
    "my", which does not exist, and every message fails."""
    response = await save_custom_endpoint(
        name="my/box", base_url="http://box:8888/v1", api_key="k"
    )
    assert "error=endpoint_name" in response.headers["location"]
    assert await db.get_custom_endpoint("my/box") is None


# ── two sends at once ──────────────────────────────────────────────────────


async def test_a_double_tap_on_send_stores_one_message(db, monkeypatch):
    """Claiming the session and storing the message straddled an await, so two
    sends landing together both saw an idle session: the message went into the
    conversation twice and was paid for twice, and the second run handle
    replaced the first, leaving the live reply streaming to a handle nobody was
    subscribed to while the user watched "This session is already working."
    """
    import agent_server.agent as agent
    from agent_server.models import ChatRequest
    from agent_server.routes.chat import chat

    session = await db.create_session("D", "/tmp", "gemini", "gemini-3.7-flash")

    async def fake_loop(session, provider, ctx, abort):
        await asyncio.sleep(0.05)
        yield {"type": "content", "text": "reply"}

    class Provider:
        name = "fake"

        def has_credentials(self):
            return True

    monkeypatch.setattr(agent, "_loop", fake_loop)
    monkeypatch.setattr(agent, "get_provider", lambda key: Provider())

    body = ChatRequest(message="hello there")
    await asyncio.gather(chat(session["id"], None, body), chat(session["id"], None, body))
    await asyncio.sleep(0.15)

    rows = await db.get_messages(session["id"])
    assert [r["content"] for r in rows if r["role"] == "user"] == ["hello there"]

    events = [e["type"] async for e in agent.subscribe(session["id"])]
    assert "content" in events, f"the reply must reach the page, got {events}"
    assert not agent.is_running(session["id"]), "the claim must be given back"


@pytest.mark.parametrize("provider_answer, expect", [
    (None, "Session not found"),
    (False, "No API key"),
])
async def test_a_turn_that_cannot_start_gives_the_session_back(
    db, monkeypatch, provider_answer, expect
):
    """A claim left behind marks the session busy forever, and nothing can be
    sent to it again -- the project looks permanently stuck."""
    import agent_server.agent as agent

    class Provider:
        name = "fake"

        def has_credentials(self):
            return bool(provider_answer)

    monkeypatch.setattr(agent, "get_provider", lambda key: Provider())
    if provider_answer is None:
        session_id = "no-such-session"
    else:
        session_id = (await db.create_session("N", "/tmp", "gemini", "gemini-3.7-flash"))["id"]

    agent.start_run(session_id)
    messages = [e.get("message", "") async for e in agent.subscribe(session_id)]
    assert any(expect in m for m in messages), messages
    assert not agent.is_running(session_id)


# ── paths that are not paths ───────────────────────────────────────────────


async def test_a_null_byte_in_a_path_is_refused_not_crashed(db):
    """`Path.resolve` raises ValueError, not OSError, on an embedded NUL, so
    the one `except OSError` around it missed and the request 500'd."""
    from fastapi import HTTPException

    from agent_server.routes.files import _resolve

    session = await db.create_session("P", "/tmp", "gemini", "gemini-3.7-flash")
    with pytest.raises(HTTPException) as caught:
        await _resolve(session["id"], "a\x00b")
    assert caught.value.status_code == 400


async def test_reading_stays_inside_the_project(db, tmp_path):
    """`peek` follows a path out of model output with nobody looking at it."""
    from fastapi import HTTPException

    from agent_server.routes.files import _resolve

    project = tmp_path / "proj"
    project.mkdir()
    session = await db.create_session("P", str(project), "gemini", "gemini-3.7-flash")
    for escape in ["../../../etc/passwd", "/etc/passwd", "/proc/self/environ",
                   "~/.ssh/id_rsa", "../"]:
        with pytest.raises(HTTPException) as caught:
            await _resolve(session["id"], escape)
        assert caught.value.status_code == 403, escape


# ── a JSON body that is not what the route expected ────────────────────────


@pytest.mark.parametrize("body", [None, [], "a string", 12, {"wrong": 1}])
async def test_a_malformed_password_request_is_refused_not_crashed(db, body):
    """`await request.json()` returns whatever the body parsed to, and .get()
    on a list or a string raises."""
    from agent_server.routes.settings import _body

    class Req:
        async def json(self):
            return body

    assert await _body(Req()) == (body if isinstance(body, dict) else {})


# ── the server following a link the model wrote ────────────────────────────


@pytest.mark.parametrize("host", [
    "localhost", "127.0.0.1", "169.254.169.254", "10.0.0.1",
    "192.168.0.1", "0.0.0.0", "[::1]", "",
])
async def test_link_previews_do_not_reach_the_local_network(host):
    """Previews are fetched automatically from links the model wrote, so the
    server follows a URL nobody chose to visit. On the teacher's machine that
    would be a way to read the school network's title tags."""
    from agent_server.routes.chat import _is_public_host

    assert await _is_public_host(host.strip("[]")) is False


# ── ways to end up somewhere with no way out ───────────────────────────────


async def test_the_home_chat_cannot_be_deleted(db):
    """It is the front door and is never offered in any list, but deleting it
    left `/` redirecting to Settings and nothing leading back -- for good,
    until someone thought to restart the app."""
    from fastapi import HTTPException

    from agent_server.routes.sessions import delete_session
    from agent_server.system_prompt import ensure_home_session

    home = await ensure_home_session()
    with pytest.raises(HTTPException) as caught:
        await delete_session(home["id"])
    assert caught.value.status_code == 400
    assert await db.get_session(home["id"]) is not None


async def test_a_missing_home_chat_is_rebuilt_rather_than_redirected(db):
    """Whatever removed it -- a stray request, a half-restored database -- the
    front door builds itself again instead of bouncing the user to Settings."""
    from agent_server.config import HOME_SESSION_ID
    from agent_server.routes.pages import index
    from agent_server.system_prompt import ensure_home_session

    await ensure_home_session()
    await db.delete_session(HOME_SESSION_ID)
    assert await db.get_session(HOME_SESSION_ID) is None

    class Req:
        def __init__(self):
            self.scope = {"type": "http"}
            self.headers = {}

    await index(Req())
    assert await db.get_session(HOME_SESSION_ID) is not None


async def test_a_project_whose_folder_vanished_gets_it_back(db, tmp_path):
    """Something outside the app can remove the folder -- a cleanup tool, a
    synced directory. Every tool then fails with an error about a path the user
    has never seen and cannot go and look at."""
    from agent_server.routes.context import _chat_context

    project = tmp_path / "proj"
    project.mkdir()
    session = await db.create_session("P", str(project), "gemini", "gemini-3.7-flash")
    project.rmdir()
    assert not project.exists()

    await _chat_context(session)
    assert project.is_dir()


async def test_the_model_cannot_be_switched_mid_turn(db, monkeypatch):
    """Switching may summarise the conversation first, and summarising rewrites
    the same messages the turn in flight is still appending to."""
    from fastapi import HTTPException

    import agent_server.agent as agent
    from agent_server.routes.sessions import switch_model

    session = await db.create_session("S", "/tmp", "gemini", "gemini-3.7-flash")
    monkeypatch.setitem(agent._aborts, session["id"], asyncio.Event())
    try:
        with pytest.raises(HTTPException) as caught:
            await switch_model(session["id"], None, {"model": "gemini-3.5-flash-lite"})
        assert caught.value.status_code == 409
    finally:
        agent._aborts.pop(session["id"], None)
    assert (await db.get_session(session["id"]))["model"] == "gemini-3.7-flash"


# ── an attachment nobody could have meant to send ──────────────────────────


async def test_a_huge_attachment_is_refused_rather_than_swallowed(db, tmp_path, monkeypatch):
    """The whole file was read into memory with no limit at all. Dropping a
    video on the chat -- and dropping things on the chat is how this app works
    -- took the server down with it."""
    from fastapi import HTTPException

    import agent_server.routes.chat as chat_routes

    monkeypatch.setattr(chat_routes, "ATTACH_DIR", tmp_path)
    monkeypatch.setattr(chat_routes, "MAX_ATTACHMENT_BYTES", 1024)
    session = await db.create_session("A", str(tmp_path), "gemini", "gemini-3.7-flash")

    class Upload:
        filename = "huge.bin"

        def __init__(self):
            self.left = 4096

        async def read(self, size=-1):
            if not self.left:
                return b""
            chunk = b"x" * min(size if size > 0 else self.left, self.left)
            self.left -= len(chunk)
            return chunk

    with pytest.raises(HTTPException) as caught:
        await chat_routes.upload_attachment(session["id"], Upload())
    assert caught.value.status_code == 413
    assert not list(tmp_path.iterdir()), "the partial file must not be left behind"


async def test_an_attachment_cannot_be_written_outside_the_attachments_folder(
    db, tmp_path, monkeypatch
):
    import agent_server.routes.chat as chat_routes

    monkeypatch.setattr(chat_routes, "ATTACH_DIR", tmp_path)
    session = await db.create_session("A", str(tmp_path), "gemini", "gemini-3.7-flash")

    class Upload:
        filename = "../../../etc/passwd"

        def __init__(self):
            self.done = False

        async def read(self, size=-1):
            if self.done:
                return b""
            self.done = True
            return b"hello"

    result = await chat_routes.upload_attachment(session["id"], Upload())
    assert Path(result["path"]).parent == tmp_path
    assert "passwd" in Path(result["path"]).name
    assert ".." not in result["path"]


async def test_an_empty_file_is_refused_in_a_sentence(db, tmp_path, monkeypatch):
    """An empty file is an ordinary thing to have -- a download that stopped
    part-way, a document made and not yet typed into -- and the message the
    server gives goes straight to the status bar the user is reading. It said
    "Empty file", which names nothing, suggests nothing, and reads like
    something has gone wrong with the app rather than with the file.
    """
    from fastapi import HTTPException

    import agent_server.routes.chat as chat_routes

    monkeypatch.setattr(chat_routes, "ATTACH_DIR", tmp_path)
    session = await db.create_session("A", str(tmp_path), "gemini", "gemini-3.7-flash")

    class Upload:
        filename = "notes.txt"

        async def read(self, size=-1):
            return b""

    with pytest.raises(HTTPException) as caught:
        await chat_routes.upload_attachment(session["id"], Upload())
    said = caught.value.detail
    assert caught.value.status_code == 400
    assert "notes.txt" in said, f"it does not say which file: {said}"
    assert said.rstrip().endswith("."), f"not a sentence: {said}"
    assert len(said.split()) > 4, f"still a log line: {said}"
    assert not list(tmp_path.iterdir()), "an empty file was left behind"
