"""Argument validation for the `browser` tool.

These run without a real browser: every case here is rejected before a session
is ever opened, which is the property being tested. Validation must happen
before any side effect, because `reset` throws away cookies and history and a
call that was going to be rejected anyway should not cost the model a login.
"""

import asyncio

import pytest

from agent_server.tools.base import ToolContext
from agent_server.tools.browser import MAX_STEPS, browser


@pytest.fixture
def ctx():
    return ToolContext(
        session_id="test-session",
        project_dir="/tmp",
        provider="deepseek",
        model="deepseek-v4-pro",
        abort=asyncio.Event(),
    )


async def test_empty_steps_is_rejected(ctx):
    result = await browser(ctx, steps=[])
    assert result.is_error
    assert "empty" in result.output.lower()


async def test_missing_steps_is_rejected(ctx):
    result = await browser(ctx, steps=None)
    assert result.is_error


async def test_json_string_of_an_empty_list_is_rejected(ctx):
    """A literal "[]" must be caught as empty.

    The emptiness check used to run before the string was parsed, so this got
    through and ran zero steps while reporting no error at all.
    """
    result = await browser(ctx, steps="[]")
    assert result.is_error
    assert "empty" in result.output.lower()


async def test_unparseable_string_is_rejected(ctx):
    result = await browser(ctx, steps="{not json")
    assert result.is_error
    assert "list of objects" in result.output


async def test_wrong_type_is_rejected(ctx):
    result = await browser(ctx, steps={"action": "goto"})
    assert result.is_error
    assert "list of objects" in result.output


async def test_too_many_steps_is_rejected(ctx):
    result = await browser(ctx, steps=[{"action": "snapshot"}] * (MAX_STEPS + 1))
    assert result.is_error
    assert str(MAX_STEPS) in result.output


async def test_reset_does_not_run_when_the_call_is_invalid(ctx, monkeypatch):
    """The whole point of validating first: a rejected call must not have
    already destroyed the browser session on its way to being rejected."""
    from agent_server import browser as engine

    called = False

    async def spy(_session_id):
        nonlocal called
        called = True

    monkeypatch.setattr(engine, "reset_session", spy)
    result = await browser(ctx, steps=[], reset=True)
    assert result.is_error
    assert called is False


# ── the schema has to describe what the code actually reads ─────────────────


def _step_schema() -> dict:
    from agent_server.tools.registry import TOOLS

    return TOOLS["browser"].parameters["properties"]["steps"]["items"]["properties"]


def test_every_key_the_tool_reads_is_in_its_schema():
    """A model can only use what the schema tells it about.

    `goto` read a `wait` key that was never declared, so the documented way to
    wait for the network to settle silently did nothing; `shoot` read `compare`,
    which is the whole before-and-after workflow, and no model could find it.
    Both were invisible because nothing compared the two halves.
    """
    import re
    from pathlib import Path

    source = Path("agent_server/tools/browser.py").read_text()
    read = set(re.findall(r"""step(?:\.get\(|\[)["']([a-z_]+)["']""", source))
    # `wait` is accepted on goto as a spelling of `until`, because a model that
    # has read the action list guesses it. Tolerated, deliberately not offered.
    declared = set(_step_schema()) | {"action", "wait"}
    assert not (read - declared), f"read but never declared: {sorted(read - declared)}"


def test_every_key_in_the_schema_is_read_somewhere():
    """The other direction: a declared key nothing acts on is a promise the
    tool does not keep, and the model spends a call finding that out."""
    import re
    from pathlib import Path

    source = Path("agent_server/tools/browser.py").read_text()
    read = set(re.findall(r"""step(?:\.get\(|\[)["']([a-z_]+)["']""", source))
    # `action` is dispatched on, not read as data.
    unused = set(_step_schema()) - read - {"action"}
    assert not unused, f"declared but never read: {sorted(unused)}"


def test_a_key_press_does_not_need_a_target():
    """Escape, Tab and arrow keys go to whatever has focus -- there is no
    element to name, and keyboard navigation is the thing this app most needs
    to be able to test. Listing `press` as targeted made that branch dead."""
    from agent_server.tools.browser import _TARGETED

    assert "press" not in _TARGETED


# ── streamed tool calls, which not every provider numbers ───────────────────


def test_unnumbered_tool_call_fragments_do_not_collapse_into_one():
    """Gemini sends no `index` on its streamed fragments. Defaulting that to 0
    put every call of a turn in one slot, so a turn asking for two tools ran
    only the second -- with the first one's arguments spliced onto its front."""
    from agent_server.agent import _accumulate

    partials = {}
    _accumulate(partials, [{"index": None, "id": "a", "name": "read", "arguments": '{"f":'}])
    _accumulate(partials, [{"index": None, "id": None, "name": None, "arguments": '"x"}'}])
    _accumulate(partials, [{"index": None, "id": "b", "name": "write", "arguments": '{"g":1}'}])

    assert len(partials) == 2, "two calls were merged into one"
    assert partials[0] == {"id": "a", "name": "read", "arguments": '{"f":"x"}'}
    assert partials[1] == {"id": "b", "name": "write", "arguments": '{"g":1}'}


def test_numbered_fragments_still_work_the_way_they_did():
    from agent_server.agent import _accumulate

    partials = {}
    _accumulate(partials, [
        {"index": 0, "id": "a", "name": "read", "arguments": "{"},
        {"index": 1, "id": "b", "name": "write", "arguments": "{"},
    ])
    _accumulate(partials, [
        {"index": 0, "id": None, "name": None, "arguments": "}"},
        {"index": 1, "id": None, "name": None, "arguments": "}"},
    ])
    assert partials[0]["arguments"] == "{}" and partials[1]["arguments"] == "{}"


# ── assertions written the obvious way ──────────────────────────────────────
#
# `{"action": "expect", "at": "text=Biscuit", "visible": true}` is how the schema
# reads and how a model writes it. The boolean used to be stringified straight
# into the selector, so it waited ten seconds for an element called "True",
# failed on a page where the thing was plainly there, and reported a bare
# timeout titled "expect visible True" -- which named nothing at all. Three
# assertions in one live session failed that way.


def test_a_boolean_visible_is_about_at_not_a_selector():
    """The fix, read off the source: a bool never becomes the thing looked up."""
    import inspect

    from agent_server.tools import browser as tool

    body = inspect.getsource(tool)
    where = body.index("visible = step.get(\"visible\")")
    block = body[where:where + 700]
    assert "isinstance(visible, bool)" in block, "a boolean still reaches the locator"
    assert "isinstance(hidden, bool)" in block


def test_a_failed_assertion_is_titled_with_what_it_looked_for():
    """Every one of them came back "expect visible True"."""
    from agent_server.tools.browser import _label

    said = _label("expect", {"at": "text=Biscuit", "visible": True})
    assert "text=Biscuit" in said
    assert "True" not in said


def test_the_shorthand_still_names_its_own_selector():
    from agent_server.tools.browser import _label

    assert "role=button" in _label("expect", {"visible": "role=button"})


# ── eval, written the way anybody writes JavaScript ─────────────────────────
#
# Playwright evaluates a bare string as an expression, so several statements
# ending in a `return` -- which is what "js" invites and what a person writes --
# came back "SyntaxError: Illegal return statement". A live session lost a call
# to exactly that.


def test_statements_with_a_return_are_wrapped():
    from agent_server.tools.browser import _as_evaluatable

    js = ("const b = document.getElementById('ball'); b.classList.add('go'); "
          "return getComputedStyle(b).left;")
    out = _as_evaluatable(js)
    assert out.startswith("() => {") and out.rstrip().endswith("}")
    assert js in out


def test_a_plain_expression_is_left_alone():
    """Wrapping it would still work, but leaving it be keeps the simple case
    simple and the failure messages readable."""
    from agent_server.tools.browser import _as_evaluatable

    for js in ("document.title", "1 + 1", "document.querySelectorAll('a').length"):
        assert _as_evaluatable(js) == js


def test_an_expression_ending_in_a_semicolon_is_still_an_expression():
    from agent_server.tools.browser import _as_evaluatable

    assert _as_evaluatable("document.title;") == "document.title;"


def test_something_already_a_function_is_not_wrapped_twice():
    from agent_server.tools.browser import _as_evaluatable

    for js in ("() => document.title", "async () => { return 1; }",
               "function () { return 2; }"):
        assert _as_evaluatable(js) == js


def test_several_statements_without_a_return_are_wrapped_too():
    """`a(); b();` is not an expression either."""
    from agent_server.tools.browser import _as_evaluatable

    out = _as_evaluatable("window.scrollTo(0,0); document.body.click();")
    assert out.startswith("() => {")


def test_the_schema_says_what_eval_and_at_actually_do():
    """Both were one-liners -- `"js": "For eval"` -- so their behaviour had to
    be discovered by getting it wrong. `text=` matching in particular is
    substring and case-insensitive unless quoted, which nothing said."""
    from agent_server.tools.registry import TOOLS

    props = TOOLS["browser"].parameters["properties"]["steps"]["items"]["properties"]
    js = props["js"]["description"]
    assert "run in the page" in js
    assert "return" in js, "nothing says statements are allowed"

    at = props["at"]["description"]
    assert "substring" in at and "case-insensitive" in at
    assert "quote" in at, "nothing says how to ask for an exact match"

    # And the booleans that used to be stringified are declared.
    for key in ("visible", "hidden"):
        assert "boolean" in props[key]["type"]


# ── Chromium going missing under it ─────────────────────────────────────────
#
# The browser tool is what the assistant uses to check its own work, so it is
# reached for constantly and in the middle of doing something else. Playwright's
# Chromium is a cache directory: a disk-cleaning tool takes it, and nothing
# tells the app. Everything below is about that moment.

MISSING = Exception(
    "Executable doesn't exist at /home/x/.cache/ms-playwright/chromium-1234/"
    "chrome-linux/chrome\n"
    "Looks like Playwright was just installed or updated.\n"
    "Please run the following command to download new browsers:\n"
    "    playwright install")


class _FakeChromium:
    """A Chromium that is missing until somebody installs it."""

    def __init__(self, present=False):
        self.present = present
        self.launches = 0

    async def launch(self, **_):
        self.launches += 1
        if not self.present:
            raise MISSING
        return _FakeBrowser()


class _FakeBrowser:
    def is_connected(self):
        return True

    async def close(self):
        pass


@pytest.fixture
def playwright_that_lost_chromium(monkeypatch):
    """`browser._playwright` replaced, and the module's globals put back after."""
    from agent_server import browser as browser_mod

    chromium = _FakeChromium(present=False)
    monkeypatch.setattr(browser_mod, "_playwright",
                        type("PW", (), {"chromium": chromium})())
    monkeypatch.setattr(browser_mod, "_browser", None)
    return chromium


async def test_it_installs_chromium_itself_rather_than_reporting_it(
        playwright_that_lost_chromium, monkeypatch):
    """The whole point. Telling the model to run `playwright install chromium`
    made the fix somebody else's turn, and put a shell command into a reply the
    user might well have been shown -- for a 150 MB download that takes under a
    minute and that nobody needs to know about."""
    from agent_server import browser as browser_mod
    from agent_server import setup

    chromium = playwright_that_lost_chromium
    installs = []

    async def fake_install():
        installs.append(True)
        chromium.present = True
        return True

    monkeypatch.setattr(setup, "ensure_chromium", fake_install)

    got = await browser_mod._ensure_browser()
    assert got is not None
    assert installs == [True], "it did not install Chromium"
    assert chromium.launches == 2, "it did not try again after installing"


async def test_the_user_is_never_asked_whether_to_install_it(
        playwright_that_lost_chromium, monkeypatch):
    """Not "shall I download Chromium?" -- there is no version of that question
    a five-year-old can answer, and no answer but yes."""
    from agent_server import browser as browser_mod
    from agent_server import setup

    chromium = playwright_that_lost_chromium

    async def fake_install():
        chromium.present = True
        return True

    monkeypatch.setattr(setup, "ensure_chromium", fake_install)
    await browser_mod._ensure_browser()  # no prompt to answer; it just works


async def test_a_download_that_fails_does_not_hand_the_model_a_shell_command(
        playwright_that_lost_chromium, monkeypatch):
    """Offline is the ordinary reason. The model should say so, not paste an
    install command into the conversation for a user with no terminal."""
    from agent_server import browser as browser_mod
    from agent_server import setup

    async def fake_install():
        return False

    monkeypatch.setattr(setup, "ensure_chromium", fake_install)

    with pytest.raises(browser_mod.BrowserError) as caught:
        await browser_mod._ensure_browser()
    said = str(caught.value)
    assert "playwright install" not in said.lower(), said
    assert "pip" not in said.lower(), said


async def test_a_crash_is_not_mistaken_for_a_missing_install(monkeypatch):
    """Downloading 150 MB in answer to a Chromium that crashed would waste
    minutes and fix nothing, every time."""
    from agent_server import browser as browser_mod
    from agent_server import setup

    class Crashes:
        async def launch(self, **_):
            raise Exception("Target page, context or browser has been closed")

    monkeypatch.setattr(browser_mod, "_playwright",
                        type("PW", (), {"chromium": Crashes()})())
    monkeypatch.setattr(browser_mod, "_browser", None)

    installs = []

    async def fake_install():
        installs.append(True)
        return True

    monkeypatch.setattr(setup, "ensure_chromium", fake_install)

    with pytest.raises(browser_mod.BrowserError):
        await browser_mod._ensure_browser()
    assert installs == [], "it downloaded Chromium because Chromium crashed"


async def test_the_second_launch_failing_is_still_a_clean_error(
        playwright_that_lost_chromium, monkeypatch):
    """Install succeeds, launch still fails -- a partial download, or a machine
    with no shared libraries for it. The model must get the tool's own error,
    not a raw Playwright traceback with a cache path in it."""
    from agent_server import browser as browser_mod
    from agent_server import setup

    async def fake_install():
        return True  # claims success, but Chromium is still not launchable

    monkeypatch.setattr(setup, "ensure_chromium", fake_install)

    with pytest.raises(browser_mod.BrowserError):
        await browser_mod._ensure_browser()


DEPS_MISSING = Exception(
    "Host system is missing dependencies to run browsers.\n"
    "Please install them with the following command:\n"
    "\n"
    "    sudo playwright install-deps\n"
    "\n"
    "Alternatively, use apt:\n"
    "    sudo apt-get install libnss3 libatk1.0-0 libgbm1\n"
    "\n"
    "<3 Playwright Team")


def test_missing_libraries_is_not_mistaken_for_a_missing_browser():
    """The two failures read almost the same and have nothing in common.

    Playwright's missing-libraries message ends in `sudo playwright
    install-deps`, and the missing-browser check matched on the substring
    "playwright install" -- so a machine that was only short of libnss3 was
    read as having no Chromium at all. It then downloaded 150 MB of the
    Chromium it already had and failed in exactly the same way, every time
    anybody asked to see anything. This is the ordinary state of a fresh
    minimal Linux install, which is most of what this app will be put on.
    """
    from agent_server import setup

    assert setup.looks_like_missing_system_libraries(DEPS_MISSING) is True
    assert setup.looks_like_missing_browser(DEPS_MISSING) is False
    assert setup.looks_like_missing_browser(MISSING) is True
    assert setup.looks_like_missing_system_libraries(MISSING) is False


async def test_missing_libraries_downloads_nothing_and_says_what_it_is(monkeypatch):
    """Nobody can fix this from inside the app, so the one useful thing to do
    is name it. Somebody sitting with the user can act on "the system libraries
    are missing" and can act on nothing at all if they are told the browser
    "would not start". Deliberately not a command: `playwright install-deps` is
    Debian and Ubuntu only, and printing it on Arch or Fedora sends whoever is
    helping to something that fails."""
    from agent_server import browser as browser_mod
    from agent_server import setup

    class NoLibraries:
        async def launch(self, **_):
            raise DEPS_MISSING

    monkeypatch.setattr(browser_mod, "_playwright",
                        type("PW", (), {"chromium": NoLibraries()})())
    monkeypatch.setattr(browser_mod, "_browser", None)

    installs = []

    async def fake_install():
        installs.append(True)
        return True

    monkeypatch.setattr(setup, "ensure_chromium", fake_install)

    with pytest.raises(browser_mod.BrowserError) as caught:
        await browser_mod._ensure_browser()
    said = str(caught.value)
    assert installs == [], "it downloaded Chromium to fix a library problem"
    assert "librar" in said, said
    assert "administrator" in said, f"nobody is told who can fix it: {said}"


def test_the_installer_checks_the_browser_actually_starts():
    """A download that succeeds and a browser that runs are different facts.

    The installer is the last moment anybody is in a terminal with a password,
    so it is the only place the library problem can be reported to somebody who
    can do something about it. Found on day one it is one command; found on day
    five it is an app that mysteriously cannot show you anything.
    """
    from pathlib import Path

    source = Path("install.py").read_text()
    assert "missing dependencies" in source, "the installer does not check"
    assert "_dependency_hint" in source, (
        "it does not pass on what Playwright said would fix it")
    # Not a command written here. Playwright knows this machine's package
    # manager and its missing packages; the installer does not, and an
    # apt-only command printed on Arch is worse than no command. (The Python
    # checks elsewhere in the file do name apt, but they say "On Ubuntu or
    # Debian" first, which is the difference.)
    browser_part = source.split("def install_browser")[1].split("\ndef ")[0]
    assert "sudo" not in browser_part, (
        "the installer prescribes a distro-specific fix for the browser")
