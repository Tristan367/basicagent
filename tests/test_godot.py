"""Making games: the four traps, pinned so nobody walks back into them.

None of what makes this work is clever code. It is four pieces of knowledge,
each of which costs an afternoon to rediscover, and each of which is a single
line somewhere that a well-meaning edit could quietly undo:

1. the export goes *outside* the project, or Godot re-imports its own build,
2. threads are off in the web build, or it needs cross-origin isolation
   headers and works on nowhere you would want to send it,
3. the desktop build keeps the real renderer even though the web build cannot,
4. and the templates are pulled a la carte, because the archive they ship in
   is 1.3 GB and the piece needed from it is 10 MB.

Nothing here downloads anything or runs the engine: those are checked by
running them, and what is checked here is that the recipe still says what it
has to say.
"""

import asyncio

import pytest

from agent_server import godot
from agent_server.tools.base import ToolContext


@pytest.fixture
def project(tmp_path):
    godot.new_project(tmp_path, "Frank's Game", say=lambda *a: None)
    return tmp_path


# ── the four traps ─────────────────────────────────────────────────────────


def test_the_build_goes_outside_the_project(project):
    """Godot scans its project folder and imports what it finds. An export
    written inside it is re-imported into the next export, and the build gets
    steadily stranger for a reason nothing reports."""
    presets = (project / "game" / "export_presets.cfg").read_text()
    for line in presets.splitlines():
        if line.startswith("export_path="):
            assert line.split("=", 1)[1].strip('"').startswith("../build/"), line
    assert not (project / "game" / "build").exists()
    assert (project / "build").is_dir()


def test_the_web_build_does_not_need_special_server_headers():
    """With threads on, a Godot web build needs COOP and COEP headers or it
    fails before the first frame -- which rules out most places a child would
    put a game, and every plain file server. Off, it works anywhere, and for a
    2D game the difference is invisible."""
    assert "variant/thread_support=false" in godot.PRESETS
    assert godot.TARGETS["web"] == ["templates/web_nothreads_release.zip"]


def test_the_desktop_build_keeps_the_real_renderer():
    """Browsers have no Vulkan, so the web build has to drop to compatibility.
    Letting that decide the whole project would quietly make every game a
    lowest-common-denominator one -- the override is per-platform for a
    reason."""
    assert 'renderer/rendering_method="forward_plus"' in godot.PROJECT_GODOT
    assert 'renderer/rendering_method.web="gl_compatibility"' in godot.PROJECT_GODOT


def test_templates_are_named_one_at_a_time():
    """They are published as a single 1.3 GB archive. Every target here is one
    or two files inside it, fetched by byte range -- 10 MB for web, 38 for
    Windows. A change that pulled the archive whole would still work, and would
    make installing this a twenty-minute wait nobody would sit through."""
    for name, files in godot.TARGETS.items():
        assert files, name
        assert len(files) <= 2, f"{name} pulls {len(files)} files"
        for f in files:
            assert f.startswith("templates/"), f


def test_a_broken_script_cannot_hold_the_check_open():
    """The one thing `check` exists to catch was the one thing it hung on.

    A GDScript parse error does not stop the engine: it prints the error, loads
    an empty scene, and sits there. Measured at forty-five seconds and still
    running, against a subprocess timeout of ten minutes -- so a child breaking
    their game meant their assistant disappeared for ten minutes and came back
    with nothing.

    `--quit-after` counts frames and the running project cannot talk its way
    out of it. With it: six seconds, and the parse error is the answer.
    """
    assert godot.QUIT_AFTER_FRAMES > 30, "must outlast the self-test's own 30 frames"
    assert godot.CHECK_TIMEOUT <= 120, "the backstop behind the backstop"

    import inspect

    source = inspect.getsource(godot.check)
    assert "--quit-after" in source
    assert "timeout=CHECK_TIMEOUT" in source
    # Export is a different path -- it never runs the game -- and legitimately
    # takes longer, so it keeps the generous timeout.
    assert "--quit-after" not in inspect.getsource(godot.export)


# ── the scaffold ───────────────────────────────────────────────────────────


def test_a_new_project_has_everything_it_needs_to_run(project):
    game = project / "game"
    for name in ("project.godot", "main.tscn", "main.gd", "debug.gd",
                 "export_presets.cfg"):
        assert (game / name).is_file(), name


def test_the_scene_file_is_small_enough_to_be_right(project):
    """A .tscn carries generated resource ids. The scaffold exists so no model
    ever writes one from memory, and it stays a scene with two scripts on it so
    that everything interesting lives in code instead."""
    scene = (project / "game" / "main.tscn").read_text()
    assert scene.count("[node ") == 2
    assert "res://main.gd" in scene and "res://debug.gd" in scene


def test_the_debug_node_is_how_the_assistant_checks_its_own_work(project):
    debug = (project / "game" / "debug.gd").read_text()
    # Readable from a browser test...
    assert "JavaScriptBridge.eval" in debug
    assert "window.__game" in debug
    # ...and from a headless run with no browser at all.
    assert "--selftest" in debug
    assert "Input.parse_input_event" in debug
    assert "PASS" in debug and "FAIL" in debug


def test_the_self_test_stays_out_of_the_way_unless_asked(project):
    """It quits the game after thirty frames. Left running by default that is
    a game that closes itself two seconds after a child presses play."""
    debug = (project / "game" / "debug.gd").read_text()
    assert 'testing = "--selftest" in OS.get_cmdline_user_args()' in debug
    assert "if not testing:" in debug


def test_every_export_target_has_a_preset_to_match():
    assert set(godot.TARGETS) == set(godot.PRESET_NAMES)
    for label in godot.PRESET_NAMES.values():
        assert f'name="{label}"' in godot.PRESETS, label


def test_the_project_name_reaches_the_files(tmp_path):
    godot.new_project(tmp_path, "Frank's Game", say=lambda *a: None)
    assert 'config/name="Frank\'s Game"' in (tmp_path / "game" / "project.godot").read_text()
    assert "franks-game" in (tmp_path / "game" / "export_presets.cfg").read_text()


def test_a_name_that_is_all_punctuation_still_produces_a_filename(tmp_path):
    assert godot._slug("!!!") == "game"
    assert godot._slug("  Space  Invaders  ") == "space-invaders"
    assert godot._slug("Frank's Game") == "franks-game", "an apostrophe is not a word break"


# ── where things live ──────────────────────────────────────────────────────


def test_godot_keeps_to_its_own_corner_of_the_data_directory():
    """Never the user's own Godot. `XDG_DATA_HOME` is redirected when it runs,
    so an install of this app cannot disturb templates they installed
    themselves, or be disturbed by them."""
    assert godot.home().name == "godot"
    assert str(godot.templates_dir()).startswith(str(godot.home()))
    env = godot._env()
    assert env["XDG_DATA_HOME"] == str(godot.home() / "templates")


def test_the_release_is_pinned():
    """As with the speech models: `latest` would mean an upstream release could
    change what an install of this version pulls down, and a project made with
    one Godot does not necessarily open in the next."""
    assert godot.RELEASE.endswith("-stable")
    assert godot.VERSION in godot.TEMPLATES_ARCHIVE
    assert "latest" not in godot.TEMPLATES_ARCHIVE


# ── the tool ───────────────────────────────────────────────────────────────


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(session_id="s", project_dir=str(tmp_path), abort=asyncio.Event())


async def test_without_godot_it_fetches_it_rather_than_explaining(ctx, monkeypatch):
    """"Ask the Project Manager to install a 90 MB download" is four things to
    understand and a conversation with a different assistant, in answer to
    "make me a game". Every one of those is somewhere a child stops."""
    from agent_server.tools import game as game_mod

    installed = [False]
    fetched = []

    def fake_install(names, say):
        fetched.append(names)
        installed[0] = True
        return True

    monkeypatch.setattr(godot, "installed", lambda: installed[0])
    monkeypatch.setattr(godot, "install", fake_install)
    monkeypatch.setattr(godot, "new_project", lambda *a, **k: True)

    result = await game_mod.game(ctx, action="new")
    assert not result.is_error, result.output
    assert fetched == [["web"]], "it should have fetched Godot itself"
    assert "Project Manager" not in result.output


async def test_a_download_that_fails_is_said_plainly(ctx, monkeypatch):
    """The one thing left to say once fetching it did not work."""
    from agent_server.tools import game as game_mod

    monkeypatch.setattr(godot, "installed", lambda: False)
    monkeypatch.setattr(godot, "install", lambda names, say: False)

    result = await game_mod.game(ctx, action="new")
    assert result.is_error
    assert "offline" in result.output
    assert "Project Manager" not in result.output, "that is not the fix any more"


async def test_the_download_does_not_block_every_other_project(ctx, monkeypatch):
    """`godot.install` is ordinary blocking code. Awaited directly it would
    stop the whole app, streaming replies included, for a 90 MB download."""
    import inspect

    from agent_server.tools import game as game_mod

    source = inspect.getsource(game_mod._install_godot)
    assert "asyncio.to_thread" in source
    assert "async with _install_lock" in source, "two projects, one unpack"


async def test_godot_is_never_downloaded_by_the_test_suite(ctx, monkeypatch):
    """A guard on the tests themselves. The first version of the auto-install
    left this path live, and running the suite quietly started an 80 MB
    download from Godot's release server."""
    from agent_server.tools import game as game_mod

    monkeypatch.setattr(godot, "installed", lambda: False)

    def explode(*a, **k):
        raise AssertionError("a test reached the real downloader")

    monkeypatch.setattr(godot, "install_editor", explode)
    monkeypatch.setattr(godot, "install_targets", explode)
    monkeypatch.setattr(godot, "install", lambda names, say: False)

    result = await game_mod.game(ctx, action="new")
    assert result.is_error


async def test_changing_a_game_that_does_not_exist_says_to_start_one(ctx, monkeypatch):
    from agent_server.tools.game import game

    monkeypatch.setattr(godot, "installed", lambda: True)
    result = await game(ctx, action="check")
    assert result.is_error
    assert "'new'" in result.output


async def test_it_will_not_start_a_second_game_over_the_first(ctx, monkeypatch):
    from agent_server.tools.game import game

    monkeypatch.setattr(godot, "installed", lambda: True)
    monkeypatch.setattr(godot, "binary", lambda: None)
    await game(ctx, action="new", name="First")
    result = await game(ctx, action="new", name="Second")
    assert not result.is_error
    assert "already a game" in result.output


async def test_an_unknown_target_is_refused_by_name(ctx, monkeypatch):
    from agent_server.tools.game import game

    monkeypatch.setattr(godot, "installed", lambda: True)
    monkeypatch.setattr(godot, "binary", lambda: None)
    await game(ctx, action="new")
    result = await game(ctx, action="export", target="nintendo")
    assert result.is_error
    assert "web, linux, windows or mac" in result.output


def test_building_a_game_is_not_the_project_managers_job():
    """The manager runs the app; a project's own assistant builds the thing.
    `game` belongs to the second."""
    from agent_server.tools.registry import MANAGER_TOOLS, TOOLS, allowed_tool_names

    assert "game" in TOOLS
    assert "game" not in MANAGER_TOOLS
    assert "game" in allowed_tool_names({"kind": "project"})
    assert "game" not in allowed_tool_names({"kind": "manager"})


def test_the_browser_knows_what_to_say_while_a_game_is_building():
    """Both status tables have drifted before. A tool the browser has never
    heard of shows a bare "Working..." where every other one says what it is
    doing."""
    from pathlib import Path

    from agent_server.activity import TOOL_FAMILY

    assert TOOL_FAMILY["game"] == "run"
    app_js = Path("web_ui/static/js/app.js").read_text()
    assert "game: 'Building the game'" in app_js
    assert "game: 'run'" in app_js


def test_the_environment_only_mentions_godot_when_it_is_there(monkeypatch, tmp_path):
    """A path in the prompt for a binary that is not installed is worse than
    silence: the assistant tries, fails, and tells the user their game is
    impossible."""
    from agent_server import system_prompt

    monkeypatch.setattr(system_prompt, "_env_cache", {})
    monkeypatch.setattr(godot, "binary", lambda: None)
    assert "Godot" not in system_prompt.environment_block(str(tmp_path), "a")

    monkeypatch.setattr(system_prompt, "_env_cache", {})
    monkeypatch.setattr(godot, "binary", lambda: tmp_path / "Godot")
    monkeypatch.setattr(godot, "targets_installed", lambda: ["web"])
    said = system_prompt.environment_block(str(tmp_path), "b")
    assert "Godot" in said and "web" in said

    # And never to the manager, which does not build anything.
    monkeypatch.setattr(system_prompt, "_env_cache", {})
    assert "Godot" not in system_prompt.environment_block(str(tmp_path), "c", manager=True)
