"""Finding out there is a new version, and putting it on.

The people this app is for cannot update it themselves. There is no terminal in
their life, `git pull` is not a sentence they will ever type, and "download the
new one and copy it over the old one" is four chances to lose their work. If
updating is not one button it does not happen, and an app that never updates
keeps every bug it shipped with -- which matters most for the people least able
to work around one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_server import updates


@pytest.fixture(autouse=True)
def no_calls(monkeypatch):
    """Nothing here reaches GitHub or moves a file on this machine."""
    monkeypatch.setattr(updates, "ROOT", Path("/nonexistent-app-root"))
    yield


# ── which of two versions is newer ─────────────────────────────────────────


def test_versions_compare_as_numbers_not_as_text():
    """The one that bites: 1.10.0 is newer than 1.9.0, and as text it is not.
    Get it wrong and the tenth release of a series silently stops offering
    itself to anybody."""
    assert updates.newer("1.10.0", "1.9.0")
    assert not updates.newer("1.9.0", "1.10.0")
    assert updates.newer("2.0.0", "1.99.99")


def test_the_v_prefix_makes_no_difference():
    """Tags are written `v1.0.1` and the file says `1.0.1`. They are the same
    fact and have to compare as one."""
    assert not updates.newer("v1.0.0", "1.0.0")
    assert updates.newer("v1.0.1", "1.0.0")


def test_the_same_version_is_not_an_update():
    assert not updates.newer("1.0.0", "1.0.0")


def test_rubbish_does_not_look_like_an_update():
    """A malformed tag must never read as newer, or everybody is told to update
    forever and the button does nothing."""
    for bad in ("", "latest", "nightly", "v", "one.two.three"):
        assert not updates.newer(bad, "1.0.0"), bad


def test_this_copy_knows_its_own_version():
    """Read from the file the release workflow checks against the tag."""
    real = Path(__file__).resolve().parent.parent / "VERSION"
    assert real.is_file(), "there is no VERSION file to release against"
    text = real.read_text().strip()
    assert updates._parts(text) >= (1, 0, 0), text


# ── asking, and not asking too often ───────────────────────────────────────


async def test_a_recent_answer_is_reused(db, monkeypatch):
    """Nobody's machine should be talking to GitHub while they work."""
    import time

    called = []

    async def boom(*a, **k):
        called.append(1)
        raise AssertionError("it asked again")

    await db.set_setting(updates.CHECK_KEY, str(time.time()))
    await db.set_setting(updates.FOUND_KEY, json.dumps(
        {"version": "9.9.9", "notes": "", "url": "u", "zip_url": "z"}))
    monkeypatch.setattr(updates, "current", lambda: "1.0.0")

    found = await updates.look()
    assert found and found.version == "9.9.9"
    assert not called


async def test_no_network_is_not_an_error(db, monkeypatch):
    """No wifi, a school firewall, GitHub having a bad morning: all of them
    mean "no news", which is the same shape as the common case."""
    import httpx

    class Dead:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): raise httpx.ConnectError("no")

    monkeypatch.setattr(httpx, "AsyncClient", Dead)
    assert await updates.look(force=True) is None


async def test_an_older_release_is_not_offered(db, monkeypatch):
    import httpx

    class Fake:
        status_code = 200
        @staticmethod
        def json():
            return {"tag_name": "v0.9.0", "body": "old", "html_url": "u",
                    "zipball_url": "z"}

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return Fake()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr(updates, "current", lambda: "1.0.0")
    assert await updates.look(force=True) is None
    assert await db.get_setting(updates.FOUND_KEY, "") == ""


async def test_a_newer_release_is_remembered(db, monkeypatch):
    """So the answer survives a restart without asking again."""
    import httpx

    class Fake:
        status_code = 200
        @staticmethod
        def json():
            return {"tag_name": "v1.2.0", "body": "Fixed the thing.",
                    "html_url": "https://example.test/r", "zipball_url": "https://z"}

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return Fake()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr(updates, "current", lambda: "1.0.0")

    found = await updates.look(force=True)
    assert found and found.version == "1.2.0"
    assert "Fixed the thing." in found.notes
    saved = json.loads(await db.get_setting(updates.FOUND_KEY, ""))
    assert saved["version"] == "1.2.0"


# ── what an update may and may not touch ───────────────────────────────────


def test_an_update_never_replaces_the_environment_or_the_repository():
    """The virtual environment belongs to this machine and the repository to
    whoever cloned it. A release containing either must not overwrite them."""
    assert ".venv" in updates.KEEP
    assert ".git" in updates.KEEP


def test_the_users_work_lives_somewhere_an_update_cannot_reach():
    """Not a rule in the updater -- a fact about where things are. Projects,
    settings and API keys are in the data directory, which is not inside the
    app folder at all."""
    from agent_server.config import DATA_DIR

    app = Path(__file__).resolve().parent.parent
    assert not str(DATA_DIR.resolve()).startswith(str(app.resolve()) + "/"), (
        f"the data directory is inside the app folder ({DATA_DIR}), so an "
        f"update could destroy somebody's projects"
    )


def test_a_clone_and_a_zip_update_differently():
    """`git pull` on a folder that was never a clone fails in a way nobody
    could act on, and unpacking a zip over a clone throws away their work."""
    import inspect

    source = inspect.getsource(updates.apply)
    assert "from_git()" in source


def test_a_clone_with_local_changes_is_left_alone():
    """Fast-forward only. A merge performed on somebody's behalf by a button is
    how you lose an afternoon's work you had not committed."""
    import inspect

    assert "--ff-only" in inspect.getsource(updates._pull)


def test_the_new_version_is_checked_before_anything_is_replaced():
    """Half an app copied over a working one is the worst outcome available."""
    import inspect

    source = inspect.getsource(updates._download_and_swap)
    assert 'for needed in ("agent_server", "requirements.txt", "VERSION")' in source
    assert source.index("does not look like this app") < source.index("_swap_in")


def test_dependencies_are_installed_after_an_update():
    """A version that added a dependency and did not install it starts, fails
    on the first import, and looks exactly like a broken update."""
    import inspect

    assert "_refresh_dependencies" in inspect.getsource(updates._pull)
    assert "_refresh_dependencies" in inspect.getsource(updates._download_and_swap)


# ── the pipeline that produces the thing ───────────────────────────────────

APP = Path(__file__).resolve().parent.parent


def test_the_release_workflow_refuses_a_mismatched_tag():
    """The tag and VERSION are two statements of one fact. If they disagree,
    either nobody is ever told about an update or everybody is told forever."""
    flow = (APP / ".github" / "workflows" / "release.yml").read_text()
    assert 'test "$tag" = "$file"' in flow
    assert "needs: check" in flow, "it could publish without the tests passing"


def test_the_release_runs_the_tests_first():
    flow = (APP / ".github" / "workflows" / "release.yml").read_text()
    assert "pytest" in flow and "ruff check" in flow


def test_the_download_page_points_at_the_newest_release():
    """So it never needs editing when a release is cut -- and so it cannot go
    stale pointing at a version from March."""
    page = (APP / "docs" / "index.html").read_text()
    assert "releases/latest" in page


def test_the_page_tells_each_platform_what_to_double_click():
    page = (APP / "docs" / "index.html").read_text()
    for named in ("install.bat", "install.command", "install.py"):
        assert named in page, named


def test_the_page_says_a_shortcut_appears():
    """The complaint that started this: it read as though you double-click a
    batch file every single time you want the app. You do not -- the installer
    puts an icon on the desktop -- but nothing said so, and an instruction that
    ends at the installer is one somebody will follow forever."""
    said = " ".join((APP / "docs" / "index.html").read_text().split())
    assert "Once, and then never again" in said
    assert "you will have an Assistant icon on your desktop" in said
    assert "You do not need the installer, the folder, or this page ever again" in said


def test_everything_the_page_promises_actually_exists():
    """A download page naming a file that is not in the zip is the first thing
    somebody meets and the last thing they try."""
    for named in ("install.bat", "Assistant.bat", "install.command",
                  "Assistant.command", "install.py", "basicagent.py"):
        assert (APP / named).is_file(), named


def test_the_mac_launchers_are_executable():
    """Finder will not run a .command that is not, and the error it gives is
    no help at all."""
    import os

    for named in ("install.command", "Assistant.command"):
        assert os.access(APP / named, os.X_OK), named


# ── being told, somewhere people actually look ─────────────────────────────


def test_the_notice_reaches_people_who_never_open_settings():
    """Most people open Settings about twice a year. A fix could otherwise sit
    unclaimed for months on the machine that most needed it."""
    js = (APP / "web_ui" / "static" / "js" / "app.js").read_text()
    assert "async function offerUpdate()" in js
    assert "offerUpdate();" in js


def test_it_is_said_once_per_version_and_not_again():
    """Said on every load it becomes wallpaper, and the one that matters goes
    unread with all the others."""
    js = (APP / "web_ui" / "static" / "js" / "app.js").read_text()
    at = js.index("async function offerUpdate()")
    body = js[at:js.index("\n  function ", at)]
    assert "localStorage.getItem(key) === info.update.version" in body
    assert "localStorage.setItem(key, info.update.version)" in body
    # And it is a line in the conversation, not something to dismiss before
    # you can carry on.
    assert "messages.appendChild(wrap)" in body
    assert "__openModal" not in body


def test_settings_says_nothing_when_there_is_nothing_to_say():
    """An app that tells you it is up to date every time you look has taught
    you to ignore it."""
    body = (APP / "web_ui" / "templates" / "settings_body.html").read_text()
    at = body.index('id="update-card"')
    assert "hidden" in body[at:at + 200]


def test_the_download_does_not_contain_itself():
    """The staging folder is inside the folder being copied, so rsync has to
    exclude it -- the first build of this shipped a hollow copy of its own name
    inside the zip, which is a strange first thing to meet."""
    flow = (APP / ".github" / "workflows" / "release.yml").read_text()
    assert '--exclude "/$staging"' in flow
    assert 'test ! -e "$staging/$staging"' in flow


def test_the_workflow_checks_the_zip_before_publishing_it():
    """A download page promising `Assistant.command` and a zip without one is
    the first thing somebody meets and the last thing they try. Checked where
    the zip is made, not only where the page is written."""
    flow = (APP / ".github" / "workflows" / "release.yml").read_text()
    for named in ("install.py", "basicagent.py", "VERSION", "requirements.txt",
                  "install.bat", "Assistant.bat", "install.command",
                  "Assistant.command"):
        assert named in flow, named
    assert "missing from the zip" in flow


def test_installing_the_dependencies_has_more_than_one_way_to_do_it():
    """Found by pressing the button rather than by reading the code. An
    environment built by `uv` has no pip in it at all -- which is not exotic,
    it is what anybody working on this project is running -- so `python -m pip`
    failed and every update would have died at the last step."""
    import inspect

    source = inspect.getsource(updates._refresh_dependencies)
    assert '"-m", "pip"' in source
    assert 'shutil.which("uv")' in source
    assert "ensurepip" in source


def test_a_failure_at_the_last_step_says_the_code_is_already_in_place():
    """By then it is. "The update failed" is both wrong and frightening: what
    failed is the check for extra pieces, and if this version added none the
    app will start perfectly."""
    import inspect

    source = inspect.getsource(updates._refresh_dependencies)
    assert "The new version is in place" in source
    assert "Restart the app" in source


# ── something to click, on each of the three ───────────────────────────────


def test_windows_gets_a_real_shortcut_not_a_batch_file():
    """A .bat has a gear icon and flashes a black console window, and somebody
    reasonably wonders whether they are doing something advanced. A .lnk is an
    icon you double-click. The batch file stays as a fallback for a machine
    that will not run PowerShell, so there is always something to click."""
    src = (APP / "install.py").read_text()
    at = src.index("def _shortcut_windows()")
    body = src[at:src.index("\n\n\n", at)]
    assert "WScript.Shell" in body and ".lnk" in body
    assert "pythonw.exe" in body, "it would flash a console window"
    assert '.bat"' in body, "nothing to fall back on"


def test_mac_gets_an_app_not_a_terminal_script():
    """A .command opens Terminal, prints things, and leaves the window sitting
    there. An .app appears in Launchpad and can be dragged to the Dock, and a
    bundle built on the machine it runs on needs no signing."""
    src = (APP / "install.py").read_text()
    at = src.index("def _shortcut_mac()")
    body = src[at:src.index("\n\n\n", at)]
    assert ".app" in body and "Info.plist" in body
    assert "CFBundleExecutable" in body
    assert "LSUIElement" in body, "it would sit in the Dock as a nameless second icon"


def test_every_platform_gets_something_on_the_desktop():
    src = (APP / "install.py").read_text()
    for func in ("_shortcut_windows", "_shortcut_mac", "_shortcut_linux"):
        at = src.index(f"def {func}()")
        body = src[at:src.index("\n\n\n", at)]
        assert "Desktop" in body or "applications" in body, func
