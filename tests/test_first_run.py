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
def only_gemini(db, monkeypatch):
    """A user who has set up one key, and it is not the default provider's."""
    from agent_server.providers import _providers

    for name, provider in _providers.items():
        monkeypatch.setattr(provider, "api_key", (lambda n: (lambda: "k" if n == "gemini" else ""))(name))
    return _providers


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


async def test_a_working_home_session_is_left_alone(db, monkeypatch):
    """Only ever moved off a provider with no credentials. The user may have
    chosen the one it is on, and moving it under them would be worse than the
    bug."""
    from agent_server.providers import _providers

    for provider in _providers.values():
        monkeypatch.setattr(provider, "api_key", lambda: "k")
    await ensure_home_session()
    await db.update_session(HOME_SESSION_ID, provider="openrouter", model="x-ai/grok-4.3")

    await ensure_home_session()
    home = await db.get_session(HOME_SESSION_ID)
    assert (home["provider"], home["model"]) == ("openrouter", "x-ai/grok-4.3")


async def test_no_key_at_all_does_not_crash_the_front_door(db, monkeypatch):
    """With nothing set up the home chat still has to render -- it is where the
    "set up your AI" banner lives, so it cannot be the thing that fails."""
    from agent_server.providers import _providers

    for provider in _providers.values():
        monkeypatch.setattr(provider, "api_key", lambda: "")
    home = await ensure_home_session()
    assert home is not None and home["id"] == HOME_SESSION_ID
