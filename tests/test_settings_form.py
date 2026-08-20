"""Checkbox handling on the settings form.

A browser omits an unticked checkbox from the submission entirely, so "off" and
"not on this form" arrive looking identical. Treating both as "leave it alone"
meant every checkbox on the settings page could be switched on and never off
again -- including read-aloud, which is the one a user is most likely to want
to undo in a hurry.
"""

import httpx
import pytest
from starlette.datastructures import FormData

from agent_server.routes.settings import _checkbox


@pytest.fixture
async def client(db):
    """The app over ASGI, without its lifespan.

    Deliberately not the real startup: that warms a dictation model and a voice
    model, which is a great deal of work to ask of a test that wants to know
    whether a template renders. `db` has already made the database.
    """
    from agent_server.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _form(*pairs):
    return FormData(list(pairs))


def test_hidden_twin_alone_means_unticked():
    assert _checkbox(_form(("tts_auto", "off")), "tts_auto") is False


def test_hidden_twin_plus_checkbox_means_ticked():
    form = _form(("tts_auto", "off"), ("tts_auto", "on"))
    assert _checkbox(form, "tts_auto") is True


def test_order_of_the_pair_does_not_matter():
    """The hidden field's position in the DOM must not decide the answer."""
    assert _checkbox(_form(("x", "on"), ("x", "off")), "x") is True
    assert _checkbox(_form(("x", "off"), ("x", "on")), "x") is True


def test_absent_field_is_none_not_false():
    """A form that never carried this setting must leave it untouched, rather
    than silently switching it off."""
    assert _checkbox(_form(("something_else", "on")), "tts_auto") is None
    assert _checkbox(_form(), "tts_auto") is None


def test_a_bare_checkbox_still_reads_as_ticked():
    """Belt and braces: a form without the hidden twin should not read a
    ticked box as unticked."""
    assert _checkbox(_form(("tts_auto", "on")), "tts_auto") is True


# ── Settings as a panel, not a page ────────────────────────────────────────


async def test_the_settings_body_renders_on_its_own(client):
    """The panel fetches this and drops it into a conversation, so it must not
    carry a page around it -- no <html>, no app bar, no second <h1>."""
    resp = await client.get("/settings/body")
    assert resp.status_code == 200
    body = resp.text
    assert '<div id="settings-page">' in body
    assert "<html" not in body.lower() and "<body" not in body.lower()
    assert "app-bar" not in body


async def test_the_page_and_the_panel_show_the_same_controls(client):
    """Both render the one template. If they ever drift, somebody has edited a
    copy -- and the page is the fallback a user reaches for when the panel is
    broken, so it cannot be the stale one."""
    page = (await client.get("/settings")).text
    body = (await client.get("/settings/body")).text
    for control in ('name="tts_voice"', 'name="uses_screen_reader"', 'id="restart-btn"',
                    'id="settings-page"', "Parental controls"):
        assert control in body, f"{control} missing from the panel"
        assert control in page, f"{control} missing from the page"


async def test_the_settings_page_still_exists(client):
    """It is the fallback when the panel cannot load, and the API key lives on
    it -- the one screen a user cannot afford to lose."""
    resp = await client.get("/settings")
    assert resp.status_code == 200
    assert "settings.js" in resp.text
