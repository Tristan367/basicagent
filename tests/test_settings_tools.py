"""Working the settings page by talking to it.

The whole point of this app is that somebody can use it entirely by speaking, and
a page of checkboxes and sliders is exactly what is out of reach for the person
it is for. So the assistant can change nearly all of it.

Two things it cannot. **API keys**: pasted into a chat, a key is written into the
message history, sent to whichever model is answering, and folded into the next
summary. **The parent's password**: same reason, and a password the assistant has
seen does not lock that assistant's own conversation.

And two things it can start but not finish -- removing projects, and child mode.
Both are irreversible-ish, and a model that has misheard "delete the old website
ones" builds the wrong list with total confidence. For those the tool ends at a
proposal and the person at the keyboard answers it.
"""

import asyncio

import pytest

from agent_server import database as db
from agent_server.config import CHILD_HOME_SESSION_ID, HOME_SESSION_ID
from agent_server.tools.app_settings import (
    set_appearance,
    set_child_mode,
    set_sounds,
    set_voice,
    show_settings,
)
from agent_server.tools.base import ToolContext
from agent_server.tools.session_manager import create_project, delete_projects


@pytest.fixture
def home(db, tmp_path):
    return ToolContext(
        session_id=HOME_SESSION_ID, project_dir=str(tmp_path), abort=asyncio.Event()
    )


@pytest.fixture
def child(db, tmp_path):
    return ToolContext(
        session_id=CHILD_HOME_SESSION_ID, project_dir=str(tmp_path), abort=asyncio.Event()
    )


# ── how it looks ───────────────────────────────────────────────────────────


async def test_the_theme_can_be_asked_for(home):
    await set_appearance(home, theme="light")
    assert await db.get_setting("theme", "") == "light"


async def test_a_bad_theme_changes_nothing(home):
    await db.set_setting("theme", "dark")
    result = await set_appearance(home, theme="sepia")
    assert result.is_error
    assert await db.get_setting("theme", "") == "dark"


async def test_bigger_and_smaller_move_from_where_it_actually_is(home):
    await db.set_setting("zoom", "1.0")
    await set_appearance(home, text_size="bigger")
    assert float(await db.get_setting("zoom", "")) == pytest.approx(1.1)
    await set_appearance(home, text_size="smaller")
    assert float(await db.get_setting("zoom", "")) == pytest.approx(1.0)


async def test_a_percentage_and_a_multiplier_mean_the_same_thing(home):
    """Both are things a model writes, and one of them is off by a hundredfold."""
    await set_appearance(home, text_size="125")
    assert float(await db.get_setting("zoom", "")) == pytest.approx(1.25)
    await set_appearance(home, text_size="1.25")
    assert float(await db.get_setting("zoom", "")) == pytest.approx(1.25)


async def test_the_text_size_is_held_inside_what_the_page_will_apply(home):
    """The browser clamps to the same range. Outside it, the assistant would
    report a change the user cannot see, which is worse than refusing."""
    await set_appearance(home, text_size="900")
    assert float(await db.get_setting("zoom", "")) == pytest.approx(1.6)
    await set_appearance(home, text_size="10")
    assert float(await db.get_setting("zoom", "")) == pytest.approx(0.7)


async def test_asking_for_nothing_is_an_error_not_a_silent_success(home):
    assert (await set_appearance(home)).is_error


# ── voice and speech ───────────────────────────────────────────────────────


async def test_only_what_was_asked_for_changes(home):
    """The bug this guards: a tool that writes every field it has a parameter
    for turns read-aloud OFF every time somebody asks for a different voice."""
    await db.set_setting("tts_auto", "1")
    await db.set_setting("stt_enabled", "1")
    await set_voice(home, speed=1.5)
    assert await db.get_setting("tts_auto", "") == "1"
    assert await db.get_setting("stt_enabled", "") == "1"
    assert await db.get_setting("tts_speed", "") == "1.5"


async def test_a_voice_can_be_named_the_way_it_is_spoken_about(home):
    """The user has never seen "bf_emma" and never will."""
    result = await set_voice(home, voice="Emma")
    assert not result.is_error
    assert await db.get_setting("tts_voice", "") == "bf_emma"


async def test_a_voice_that_does_not_exist_comes_back_with_the_list(home):
    result = await set_voice(home, voice="Brian Blessed")
    assert result.is_error
    assert "Emma" in result.output, "the model was told no without being told what to say"


async def test_a_volume_asked_for_as_a_percentage_is_understood(home):
    await set_voice(home, volume=80)
    assert float(await db.get_setting("tts_volume", "")) == pytest.approx(0.8)
    await set_sounds(home, volume=0.25)
    assert float(await db.get_setting("sound_volume", "")) == pytest.approx(0.25)


async def test_switches_understand_the_words_a_model_writes(home):
    for value in (True, "true", "on", "yes", "1"):
        await db.set_setting("sound_ticks", "0")
        await set_sounds(home, ticking=value)
        assert await db.get_setting("sound_ticks", "") == "1", value
    for value in (False, "false", "off", "no", "0"):
        await db.set_setting("sound_ticks", "1")
        await set_sounds(home, ticking=value)
        assert await db.get_setting("sound_ticks", "") == "0", value


async def test_the_settings_read_back_in_words_not_keys(home):
    await db.set_setting("theme", "light")
    await db.set_setting("sound_ticks", "1")
    result = await show_settings(home)
    assert "light mode" in result.output
    assert "ticking on" in result.output
    assert "sound_ticks" not in result.output, "raw setting keys leaked into the answer"


# ── the two it may not finish ──────────────────────────────────────────────


async def test_child_mode_only_ever_raises_the_question(home):
    result = await set_child_mode(home, on=True)
    assert not result.is_error
    assert result.action == {"kind": "child_mode", "on": True}
    assert await db.get_setting("child_mode", "0") == "0", "child mode was switched on"
    assert await db.get_setting("parent_password_hash", "") == "", "a password was set"


async def test_child_mode_that_is_already_on_asks_nothing(home):
    await db.set_setting("child_mode", "1")
    result = await set_child_mode(home, on=True)
    assert result.action is None
    assert "already" in result.output


async def test_removing_projects_removes_nothing(home):
    await create_project(home, name="Cat Website")
    await create_project(home, name="Dog Website")
    result = await delete_projects(home, every_one=True)

    assert result.action["kind"] == "delete_projects"
    assert {s["name"] for s in result.action["sessions"]} == {"Cat Website", "Dog Website"}
    assert len(await db.list_sessions(profile=None)) == 2, "a project was actually removed"


async def test_a_name_that_does_not_exist_is_called_out_not_guessed_at(home):
    await create_project(home, name="Cat Website")
    result = await delete_projects(home, names=["Cat Website", "Hamster Website"])
    assert [s["name"] for s in result.action["sessions"]] == ["Cat Website"]
    assert "Hamster Website" in result.output, "the model would report removing it too"


async def test_nothing_matching_is_an_error_rather_than_an_empty_box(home):
    await create_project(home, name="Cat Website")
    result = await delete_projects(home, names=["Hamster Website"])
    assert result.is_error
    assert result.action is None


async def test_the_same_project_named_twice_is_listed_once(home):
    await create_project(home, name="Cat Website")
    result = await delete_projects(home, names=["Cat Website", "cat website"])
    assert len(result.action["sessions"]) == 1


async def test_the_button_is_the_only_thing_that_removes_anything(db, home):
    """The tool proposes; this is what the button in the box calls."""
    from agent_server.routes.sessions import remove_sessions

    await create_project(home, name="Cat Website")
    proposal = await delete_projects(home, every_one=True)
    ids = [s["id"] for s in proposal.action["sessions"]]

    result = await remove_sessions({"ids": ids})
    assert result["removed"] == ["Cat Website"]
    assert await db.list_sessions(profile=None) == []


async def test_the_home_chat_cannot_be_removed_through_the_bulk_route(db):
    """It is the front door. Deleting it left every route leading to Settings
    and nothing leading back."""
    from agent_server.routes.sessions import remove_sessions
    from agent_server.system_prompt import ensure_home_session

    await ensure_home_session()
    result = await remove_sessions({"ids": [HOME_SESSION_ID]})
    assert result["removed"] == []
    assert await db.get_session(HOME_SESSION_ID) is not None


# ── what is withheld from whom ─────────────────────────────────────────────


def test_a_childs_assistant_is_not_offered_its_own_safety_switch():
    """It only raises a password box, so it is safe -- but a child's own
    assistant putting up a box inviting a guess at the parent's password is not
    something it should do."""
    from agent_server.tools.registry import allowed_tool_names

    childs = allowed_tool_names({"kind": "manager", "profile": "child"})
    assert "set_child_mode" not in childs
    # The harmless ones are not withheld. A child asking for bigger text or a
    # different voice should get it, the same as anybody.
    for name in ("set_appearance", "set_voice", "set_sounds", "show_settings"):
        assert name in childs, name


def test_a_project_session_is_not_given_the_app_to_run():
    """The split is the point, not an oversight. A project's assistant builds
    the project; every schema it carries is context spent on every single
    request it makes, and it would spend that on running an app it has never
    been told anything about. Asked to change the voice, it says the Project
    Manager does that -- which is one sentence, and correct."""
    from agent_server.tools.registry import allowed_tool_names

    in_a_project = allowed_tool_names({"kind": "project", "profile": "parent"})
    for name in ("show_settings", "set_appearance", "set_voice", "set_sounds",
                 "set_child_mode", "create_project", "open_project",
                 "delete_projects", "assign_project"):
        assert name not in in_a_project, name
    # What it does have is the coding agent, whole.
    for name in ("read", "write", "edit", "bash", "grep", "glob", "browser",
                 "preview", "capture", "task", "webfetch", "websearch"):
        assert name in in_a_project, name


def test_no_tool_can_touch_an_api_key():
    """A key pasted into a chat is written into the history, sent to whichever
    model is answering, and folded into the next summary. The assistant walks
    people through fetching one; the key itself goes in the box on the page."""
    import json

    from agent_server.tools.registry import TOOLS

    for name, tool in TOOLS.items():
        blob = json.dumps(tool.parameters).lower()
        assert "api_key" not in blob and "apikey" not in blob, name
        assert "password" not in blob, name


# ── the colour ─────────────────────────────────────────────────────────────


async def test_a_colour_can_be_asked_for_by_name(home):
    await set_appearance(home, colour="blue")
    assert await db.get_setting("accent", "") == "#2f6fb0"


async def test_both_spellings_of_colour_work(home):
    """Which one a model writes depends on which side of an ocean its training
    data came from, and "unknown parameter" is a silly way to fail that."""
    await set_appearance(home, color="purple")
    assert await db.get_setting("accent", "") == "#7a5aa8"


async def test_a_colour_it_does_not_know_comes_back_with_the_list(home):
    result = await set_appearance(home, colour="heliotrope")
    assert result.is_error
    assert "purple" in result.output


async def test_the_colour_can_be_put_back(home):
    await set_appearance(home, colour="red")
    await set_appearance(home, colour="default")
    assert await db.get_setting("accent", "") == ""


def test_every_named_colour_is_a_real_hex_value():
    """A malformed one is written straight into an inline style, where it does
    not fail loudly -- it simply does nothing, and the app stays green while the
    assistant says it is now pink."""
    import re

    from agent_server.tools.app_settings import COLOURS

    for name, value in COLOURS.items():
        assert re.fullmatch(r"#[0-9a-f]{6}", value), f"{name} -> {value}"


# ── what the open page is told ─────────────────────────────────────────────


async def test_the_live_settings_carry_everything_the_page_applies(db):
    """The chat re-reads this at the end of every turn and applies what moved.
    A setting the assistant can change that is missing here is one the user has
    to reload to see -- which is not a change anybody asked for."""
    from agent_server.routes.settings import get_theme

    await db.set_setting("accent", "#2f6fb0")
    payload = await get_theme()
    for key in ("theme", "zoom", "accent", "accent_text", "tts_auto", "tts_voice",
                "tts_speed", "tts_volume", "sound_cues", "sound_ticks",
                "sound_volume", "stt_enabled", "child_mode"):
        assert key in payload, key
    # The contrast is worked out on the server so there is one implementation of
    # it; getting it wrong makes the user's own messages unreadable.
    assert payload["accent_text"] in ("#000000", "#ffffff")


# ── everything on the settings page except the key ─────────────────────────


def test_the_manager_can_reach_every_setting_but_the_key():
    """The rule is "everything in Settings besides pasting an API key" -- a key
    typed into a conversation is a key stored in a conversation, and that is
    the one thing that stays on the page.

    Two were missing: which AI answers, which is what everything costs, and how
    well dictation listens, which is the difference between talking to this app
    and giving up on it.
    """
    from agent_server.tools.registry import MANAGER_TOOLS

    for reachable in ("set_appearance", "set_voice", "set_sounds",
                      "set_model", "set_dictation_quality", "set_child_mode",
                      "show_settings"):
        assert reachable in MANAGER_TOOLS, reachable


def test_no_tool_takes_an_api_key():
    """Not an oversight to be fixed later -- the boundary itself."""
    from agent_server.tools.registry import TOOLS

    for name, tool in TOOLS.items():
        properties = tool.parameters.get("properties", {})
        for field in properties:
            assert "api_key" not in field.lower(), f"{name}.{field}"
            assert field.lower() != "key", f"{name}.{field}"


def test_set_appearance_changes_several_things_at_once():
    """"Dark mode and make it blue" is one sentence and should be one call, not
    two round trips with a visible flicker between them."""
    from agent_server.tools.registry import TOOLS

    properties = TOOLS["set_appearance"].parameters["properties"]
    assert {"theme", "colour", "text_size"} <= set(properties)
    assert not TOOLS["set_appearance"].parameters.get("required"), (
        "every field is optional, so any combination is one call")


async def test_asking_which_ai_without_naming_one_reads_the_list_back(db, monkeypatch):
    """Called with nothing it answers "what is it using, and what else is
    there, and what does each cost" -- which is the actual question behind
    "is there a cheaper one?"."""
    from agent_server.tools import app_settings

    monkeypatch.setattr(
        "agent_server.model_catalog.offerable_models",
        lambda: [
            {"id": "cheap-1", "name": "Cheap", "provider_label": "DeepSeek",
             "price_label": "$1.32 per million", "price_out": 1.32},
            {"id": "dear-1", "name": "Dear", "provider_label": "Google",
             "price_label": "free for now", "price_out": 0.0},
        ])
    ctx = _ctx()
    result = await app_settings.set_model(ctx)
    assert not result.is_error
    assert "Cheap" in result.output and "Dear" in result.output
    assert "per million" in result.output, "the price is not said"


async def test_a_model_nobody_has_a_key_for_cannot_be_chosen(db, monkeypatch):
    from agent_server.tools import app_settings

    monkeypatch.setattr(
        "agent_server.model_catalog.offerable_models",
        lambda: [{"id": "cheap-1", "name": "Cheap", "provider_label": "DeepSeek",
                  "price_label": "cheap", "price_out": 1.0}])
    result = await app_settings.set_model(_ctx(), model="gpt-9")
    assert result.is_error
    assert "Cheap" in result.output, "it does not say what there is instead"


async def test_dictation_quality_takes_the_words_somebody_would_use(db):
    from agent_server import config
    from agent_server.tools import app_settings

    before = config.whisper_size()
    try:
        result = await app_settings.set_dictation_quality(_ctx(), quality="faster")
        assert not result.is_error, result.output
        assert config.whisper_size() == "base.en"
    finally:
        config.set_whisper_size(before)


async def test_dictation_quality_with_nothing_reads_it_back(db):
    from agent_server.tools import app_settings

    result = await app_settings.set_dictation_quality(_ctx())
    assert not result.is_error
    assert "Most accurate" in result.output or "Faster" in result.output


def _ctx():
    import asyncio

    from agent_server.tools.base import ToolContext

    return ToolContext(session_id="home", project_dir="/tmp", abort=asyncio.Event())


# ── before and after ───────────────────────────────────────────────────────


async def test_asking_for_a_setting_that_is_already_on_says_so(db):
    """"Make it dark" when it is already dark used to come back "Done: dark
    mode", so the assistant reported success and left the user exactly where
    they were -- and never learned the thing they are unhappy about was already
    switched on. That is the moment to say "it already is", because whatever is
    wrong is something else.
    """
    from agent_server.tools import app_settings

    ctx = _ctx()
    await app_settings.set_appearance(ctx, theme="dark")
    result = await app_settings.set_appearance(ctx, theme="dark")
    assert "already" in result.output.lower(), result.output
    assert "no change" in (result.title or "").lower(), result.title


async def test_a_real_change_says_what_it_was_and_what_it_is_now(db):
    from agent_server.tools import app_settings

    ctx = _ctx()
    await app_settings.set_appearance(ctx, theme="dark")
    result = await app_settings.set_appearance(ctx, theme="light")
    assert "dark mode -> light mode" in result.output, result.output


async def test_a_setting_nobody_has_ever_touched_compares_by_its_default(db):
    """Stored as "" until somebody sets it, and reads back as its default. A
    raw comparison called the first "make it dark" a change from dark to dark,
    which is the exact case this exists to notice."""
    from agent_server.tools import app_settings

    result = await app_settings.set_appearance(_ctx(), theme="dark")
    assert "->" not in result.output, result.output
    assert "already" in result.output.lower()


async def test_several_changes_at_once_are_each_reported(db):
    from agent_server.tools import app_settings

    ctx = _ctx()
    await app_settings.set_appearance(ctx, theme="dark")
    result = await app_settings.set_appearance(ctx, theme="light", text_size="bigger")
    assert "theme:" in result.output and "text size:" in result.output


async def test_a_mix_of_changed_and_already_is_split_apart(db):
    """Because "one of the two things you asked for was already the case" is
    the answer, and a single "Done" cannot say it."""
    from agent_server.tools import app_settings

    ctx = _ctx()
    await app_settings.set_appearance(ctx, theme="dark")
    result = await app_settings.set_appearance(ctx, theme="dark", text_size="bigger")
    lowered = result.output.lower()
    assert "changed:" in lowered and "already as asked:" in lowered, result.output


async def test_the_volume_is_reported_in_the_words_a_person_uses(db):
    """Not 0.4 -> 0.8. Nobody has ever asked for a volume of nought point four."""
    from agent_server.tools import app_settings

    ctx = _ctx()
    await app_settings.set_sounds(ctx, volume=0.4)
    result = await app_settings.set_sounds(ctx, volume=0.8)
    assert "40% -> 80%" in result.output, result.output
