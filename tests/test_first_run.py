"""The path every single user takes once, and a developer never sees again.

The bug that prompted this file: the home assistant was created on a hard-coded
model regardless of what the user had set up. Somebody whose only key was Gemini
-- the free option this app points people at -- opened the app, said hello, and
was told "No API key is set up yet. Add one in Settings." They had just done
that, and nothing in the app would have told them what was wrong.
"""

import pytest

from agent_server.config import CHILD_HOME_SESSION_ID, HOME_SESSION_ID
from agent_server.system_prompt import ensure_home_session


@pytest.fixture
def builtin_providers(monkeypatch):
    """Only the providers that ship with the app.

    `_providers` is module-level, so a custom endpoint registered by another
    test file is still in it here -- and a custom endpoint is free, so it wins
    "cheapest" and the assertions below become about the wrong thing.
    """
    from agent_server import providers

    kept = {k: v for k, v in providers._providers.items() if not k.startswith("custom:")}
    monkeypatch.setattr(providers, "_providers", kept)
    import agent_server.model_catalog as catalog

    monkeypatch.setattr(catalog, "_providers", kept)
    return kept


@pytest.fixture
def only_gemini(db, builtin_providers, monkeypatch):
    """A user who has set up one key, and it is not the default provider's."""
    for name, provider in builtin_providers.items():
        monkeypatch.setattr(provider, "api_key", (lambda n: (lambda: "k" if n == "gemini" else ""))(name))
    return builtin_providers


async def test_the_home_assistant_lands_on_a_provider_the_user_has_a_key_for(db, only_gemini):
    await ensure_home_session()
    home = await db.get_session(HOME_SESSION_ID)
    assert home["provider"] == "gemini", "the home chat was created on a provider with no key"


async def test_both_home_sessions_are_fixed_not_just_the_parents(db, only_gemini):
    await ensure_home_session()
    child = await db.get_session(CHILD_HOME_SESSION_ID)
    assert child["provider"] == "gemini"


async def test_an_existing_install_is_repaired_on_startup(db, only_gemini):
    """The people this bites already have the broken row in their database, so
    creating new sessions correctly is not enough on its own."""
    await ensure_home_session()
    await db.update_session(HOME_SESSION_ID, provider="deepseek", model="deepseek-v4-pro")

    await ensure_home_session()
    home = await db.get_session(HOME_SESSION_ID)
    assert home["provider"] == "gemini", "a broken existing session was left broken"


async def test_a_working_home_session_is_left_alone(db, builtin_providers, monkeypatch):
    """Only ever moved off a provider with no credentials. The user may have
    chosen the one it is on, and moving it under them would be worse than the
    bug."""
    for provider in builtin_providers.values():
        monkeypatch.setattr(provider, "api_key", lambda: "k")
    await ensure_home_session()
    await db.update_session(HOME_SESSION_ID, provider="openrouter", model="x-ai/grok-4.3")

    await ensure_home_session()
    home = await db.get_session(HOME_SESSION_ID)
    assert (home["provider"], home["model"]) == ("openrouter", "x-ai/grok-4.3")


async def test_no_key_at_all_does_not_crash_the_front_door(db, builtin_providers, monkeypatch):
    """With nothing set up the home chat still has to render -- it is where the
    "set up your AI" banner lives, so it cannot be the thing that fails."""
    for provider in builtin_providers.values():
        monkeypatch.setattr(provider, "api_key", lambda: "")
    home = await ensure_home_session()
    assert home is not None and home["id"] == HOME_SESSION_ID


# ── the optional extras, when they are not there ───────────────────────────


def test_a_mistyped_model_path_reports_missing_rather_than_ready(monkeypatch, tmp_path):
    """Taken on trust, a stale TTS_MODEL made the app say read-aloud was ready
    and then fail at the moment somebody pressed play. Saying up front that it
    is not installed is a far better outcome."""
    import importlib

    monkeypatch.setenv("TTS_MODEL", str(tmp_path / "not-here.onnx"))
    monkeypatch.setenv("TTS_VOICES", str(tmp_path / "also-not-here.bin"))
    import agent_server.config as config

    importlib.reload(config)
    try:
        assert config.TTS_MODEL == ""
        assert config.TTS_VOICES == ""
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_the_welcome_still_offers_read_aloud_when_it_is_not_installed():
    """Hiding it would mean somebody who came for exactly this never learns it
    exists. It says what is needed instead, and the preference is saved either
    way, so it starts working the moment the voice is set up."""
    from pathlib import Path

    page = Path("web_ui/templates/chat.html").read_text()
    assert 'value="read_aloud"' in page
    assert "not tts.available" in page, "the unavailable case is not called out"


def test_the_first_run_note_names_what_is_missing_and_offers_to_fix_it():
    from agent_server import setup

    names = {c["name"] for c in setup.detect()}
    assert "Read-aloud (text-to-speech)" in names
    for component in setup.detect():
        assert component["hint"], f"{component['name']} has no hint to act on"


# ── the step-by-step guide ──────────────────────────────────────────────────
#
# The one thing the user has to do somewhere other than in this app, so it is
# the one place a dead end costs us the whole user.


def test_every_address_the_guide_names_is_a_link():
    """Naming an address at somebody is not telling them how to get there. This
    app is usable entirely by voice, and "type aistudio.google.com/apikey" is
    not something a person working by voice can act on."""
    import re

    from agent_server.routes.context import KEY_WALKTHROUGH, link_parts

    for step in KEY_WALKTHROUGH:
        # Any bare domain left in the prose, outside a [text](url) link.
        bare = re.sub(r"\[[^\]]+\]\(https://[^)\s]+\)", "", step["text"])
        found = re.findall(r"\b[a-z0-9-]+\.(?:com|org|net|ai|io|dev)\b", bare, re.I)
        assert not found, f"named but not linked: {found}"
        for para in step["text"].split("\n\n"):
            # The words a reader sees, once the link markers are gone. Splitting
            # must move nothing and drop nothing -- the parts are rendered one
            # after another, so a lost run is a lost sentence.
            plain = re.sub(r"\[([^\]]+)\]\(https://[^)\s]+\)", r"\1", para)
            assert "".join(p["text"] for p in link_parts(para)) == plain


def test_the_link_in_the_first_step_goes_where_the_button_does():
    from agent_server.routes.context import KEY_URL, KEY_WALKTHROUGH, link_parts

    parts = link_parts(KEY_WALKTHROUGH[0]["text"])
    hrefs = [p["href"] for p in parts if p["href"]]
    assert hrefs == [KEY_URL]


def test_prose_with_no_link_in_it_survives_unchanged():
    from agent_server.routes.context import link_parts

    assert link_parts("Click the Create API key button.") == [
        {"text": "Click the Create API key button.", "href": ""}
    ]


def test_the_guide_opens_as_an_overlay_not_a_fold_in_the_column():
    """It was a <details> inside the settings column, which in the panel is
    480px wide -- so the screenshots of a web page came out about a centimetre
    across, which is no use to the person who needed the pictures."""
    import re
    from pathlib import Path

    body = Path("web_ui/templates/settings_body.html").read_text()
    assert 'id="walkthrough-open"' in body
    assert 'id="walkthrough-modal"' in body
    # Comments stripped: they explain what the guide used to be, and that is not
    # what a user meets.
    assert "<details" not in re.sub(r"\{#.*?#\}", "", body, flags=re.S), \
        "the guide is folded into the column again"
    # Outside `#settings-page`: that element carries `container-type`, which
    # makes it a containing block for `position: fixed`, so an overlay inside it
    # would be pinned to the settings column instead of the window.
    page = body[body.index('<div id="settings-page">'):]
    assert page.index('id="walkthrough-modal"') > page.index('id="to-top-btn"')


async def test_saving_the_first_key_fixes_home_without_a_restart(db, builtin_providers,
                                                                 monkeypatch):
    """The repair used to run only at startup, so the very first key of a fresh
    install was saved, accepted, and ignored: back on the front page the user
    said hello and was told "No API key is set up yet. Add one in Settings to
    get started." -- the step they had just finished. Nothing said to restart
    the app, and nobody this app is for would think of it."""
    import httpx

    from agent_server.main import app

    for provider in builtin_providers.values():
        monkeypatch.setattr(provider, "api_key", lambda: "")
    await ensure_home_session()
    await db.update_session(HOME_SESSION_ID, provider="deepseek", model="deepseek-v4-pro")

    # Saving a key is what the user does next, so the key has to become real to
    # the provider exactly as it would after the form writes it.
    monkeypatch.setattr(
        builtin_providers["gemini"], "api_key", lambda: "k"
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/_settings", data={"gemini_api_key": "k"})

    home = await db.get_session(HOME_SESSION_ID)
    assert home["provider"] == "gemini", \
        "the front page still had no working AI until the app was restarted"
