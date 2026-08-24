"""Telling somebody how to put money on the account.

"Insufficient credits" is a true sentence that helps nobody. The person who has
to act on it is usually not the person reading it, has never seen a billing
console, does not know which of Google's several sites they are meant to be on,
and has no idea whether the right answer is five dollars or five hundred. Left
there, the picture never gets made and the child concludes the app is broken.

Everything here is about that gap: the exact address, the exact button, the
exact amount, and what the amount buys. The Google flow below was read off
Google's own billing documentation in August 2026 -- prepay, $10 minimum, and a
spend page where a limit can be set -- not recalled.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_server import imagegen, money
from agent_server.tools.base import ToolContext
from agent_server.tools.draw import draw


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(session_id="m", project_dir=str(tmp_path), abort=asyncio.Event())


# ── the answer to "how much?" ──────────────────────────────────────────────


def test_ten_dollars_is_counted_in_pictures_not_dollars():
    """A parent asked for money wants to know what it buys. "Ten dollars" means
    nothing on its own; "about 250 pictures" is a decision they can make."""
    span = money.pictures_for(10.0, imagegen.IMAGE_MODELS)
    assert span
    fewest, most = span
    assert fewest == int(10 / 0.14)
    assert most == int(10 / 0.039)
    assert fewest < most


def test_both_ends_are_given_because_the_price_varies_sevenfold():
    """The dearest model is three and a half times the cheapest. One number
    would be a number somebody could reasonably feel misled by."""
    said = money._count(10.0, imagegen.IMAGE_MODELS)
    assert "71" in said and "256" in said
    assert "cheapest" in said


def test_no_prices_means_no_arithmetic():
    """Guessing here is guessing about somebody else's money."""
    unpriced = [imagegen.ImageModel("x", "X", "custom:box")]
    assert money.pictures_for(10.0, unpriced) is None
    assert money._count(10.0, unpriced) == ""


# ── the answer to "where?" ─────────────────────────────────────────────────


def test_google_sends_people_to_the_page_with_the_button_on_it():
    """Not console.cloud.google.com. That is a real page, a plausible one, and
    not where the button is -- and an evening lost to it is how somebody
    decides the whole thing is beyond them."""
    where = money.where("gemini")
    assert where.url == "https://aistudio.google.com/apikey"
    assert "console.cloud.google.com" not in " ".join(where.steps)


def test_google_says_prepay_and_says_the_minimum():
    """Prepay is money in and spent down; postpay is a bill arriving later. For
    a child's account those are very different things, and Google offers both
    at the same moment."""
    steps = " ".join(money.where("gemini").steps)
    assert "Prepay" in steps
    assert "$10" in steps
    assert "smallest amount" in steps


def test_google_mentions_the_limit_that_stops_it_running_away():
    """The question behind "how much should I put in" is usually "how much can
    this cost me". A cap is the real answer to that one."""
    steps = " ".join(money.where("gemini").steps)
    assert "aistudio.google.com/spend" in steps
    assert "cannot run up a bill" in steps


def test_every_provider_that_can_draw_knows_where_its_money_goes():
    for provider in {m.provider for m in imagegen.IMAGE_MODELS}:
        assert provider in money.TOP_UPS, provider


def test_a_provider_nobody_here_has_heard_of_still_gets_an_answer():
    """The whole point of finding models rather than listing them is that the
    provider may be one nobody here has seen. "Sign in and look for Billing" is
    something a person can act on; silence is not."""
    where = money.where("custom:box")
    assert where.steps
    assert "box" in where.steps[0]
    assert "Billing" in " ".join(where.steps)


# ── how it reaches the screen ──────────────────────────────────────────────


def test_the_steps_carry_real_links_not_addresses_to_type():
    """This app is used by people working entirely by voice. Naming an address
    at somebody who cannot easily type is not telling them how to get there."""
    panel = money.panel("gemini", imagegen.IMAGE_MODELS)
    hrefs = [part["href"] for step in panel["steps"] for part in step
             if part["href"]]
    assert "https://aistudio.google.com/apikey" in hrefs
    assert "https://aistudio.google.com/spend" in hrefs


async def test_running_out_of_money_is_answered_with_instructions(ctx, monkeypatch):
    async def broke(prompt, **kw):
        raise imagegen.NoFunds("there is not enough money on this account")

    async def catalogue(refresh=False):
        return list(imagegen.IMAGE_MODELS)

    monkeypatch.setattr(imagegen, "draw", broke)
    monkeypatch.setattr(imagegen, "catalogue", catalogue)

    result = await draw(ctx, prompt="a dragon")
    assert result.is_error
    # The assistant's copy: the words, so it can talk somebody through them.
    assert "aistudio.google.com/apikey" in result.output
    assert "$10" in result.output
    assert "71" in result.output
    # The app's copy: the same thing as data, for the screen.
    assert result.action and result.action["kind"] == "add_funds"
    assert result.action["url"] == "https://aistudio.google.com/apikey"
    assert result.action["count"]


async def test_the_assistant_is_told_not_to_improve_on_the_address(ctx, monkeypatch):
    """Its copy and the screen's copy disagreeing is worse than either alone."""
    async def broke(prompt, **kw):
        raise imagegen.NoFunds("no money")

    async def catalogue(refresh=False):
        return list(imagegen.IMAGE_MODELS)

    monkeypatch.setattr(imagegen, "draw", broke)
    monkeypatch.setattr(imagegen, "catalogue", catalogue)

    result = await draw(ctx, prompt="a dragon")
    assert "exactly as they are" in result.output
    assert "not their fault" in result.output


async def test_the_steps_match_the_provider_that_refused(ctx, monkeypatch):
    """Somebody drawing through OpenRouter must not be sent to Google."""
    openrouter = imagegen.ImageModel("x/y", "Y", "openrouter", 0.02,
                                     route=imagegen.CHAT)

    async def broke(prompt, **kw):
        raise imagegen.NoFunds("no money")

    async def catalogue(refresh=False):
        return [openrouter]

    monkeypatch.setattr(imagegen, "draw", broke)
    monkeypatch.setattr(imagegen, "catalogue", catalogue)

    result = await draw(ctx, prompt="a dragon")
    assert result.action["url"] == "https://openrouter.ai/settings/credits"
    assert "aistudio" not in result.output


# ── telling a money problem from every other kind ──────────────────────────


def test_a_money_refusal_is_its_own_kind_of_failure():
    assert isinstance(imagegen._failure(402, '{"error":{"message":"insufficient"}}'),
                      imagegen.NoFunds)


def test_no_free_allowance_for_pictures_is_a_money_problem_too():
    """A free Google tier has no picture allowance at all, so a 429 on an image
    model is almost never "you went too fast" -- it is "this account has never
    been set up to pay for pictures". Same website, same fix."""
    assert isinstance(imagegen._failure(429, "{}"), imagegen.NoFunds)


def test_a_bad_key_is_not_a_money_problem():
    """A different website and a different fix. Sending somebody to a payment
    page for a mistyped key wastes an evening and ten dollars."""
    failure = imagegen._failure(403, '{"error":{"message":"API key not valid"}}')
    assert isinstance(failure, imagegen.ImageError)
    assert not isinstance(failure, imagegen.NoFunds)


def test_credit_held_against_something_running_is_not_a_money_problem():
    """It clears on its own. A payment page would be a payment page for
    nothing."""
    failure = imagegen._failure(
        402, '{"error":{"message":"exceeds available credits given your '
             'current in-flight requests"}}')
    assert not isinstance(failure, imagegen.NoFunds)


def test_the_card_is_not_dressed_up_as_an_error():
    """Nothing is broken and a child must not be left thinking they broke it.
    Checked in the stylesheet because that is where the decision lives."""
    from pathlib import Path

    css = Path("web_ui/static/css/style.css").read_text()
    block = css[css.index(".fund-card {"):css.index(".fund-title")]
    assert "--danger" not in block


# ── how the card behaves, decided in the front end ─────────────────────────


def _appendFunds() -> str:
    from pathlib import Path

    js = Path("web_ui/static/js/app.js").read_text()
    start = js.index("function appendFunds(")
    return js[start:js.index("\n  function ", start + 10)]


def test_the_card_goes_into_the_conversation_not_into_a_dialog():
    """A dialog is for a question. This is not a question -- it is a set of
    instructions somebody may need to read twice, show to a parent who is not
    in the room yet, or scroll back to tomorrow. Dismissing it should not be
    the only way to carry on using the app."""
    body = _appendFunds()
    assert "messages.appendChild" in body
    assert "__openModal" not in body


def test_the_front_end_asks_a_grown_up_when_a_child_is_looking():
    """A child cannot fix this and must not be left feeling they broke it."""
    body = _appendFunds()
    assert "child-mode" in body
    assert "grown-up" in body


def test_every_link_opens_beside_the_app_rather_than_over_it():
    """Navigating away from a conversation to reach a billing page loses the
    conversation."""
    body = _appendFunds()
    assert "'_blank'" in body
    assert "noopener" in body


def test_the_card_is_built_from_text_never_from_markup():
    """The steps are ours, but they arrive over the wire like anything else,
    and there is no reason for this to be the one place that could put markup
    on the page."""
    body = _appendFunds()
    assert "innerHTML" not in body
    assert "textContent" in body


def test_the_turn_ends_by_offering_it():
    from pathlib import Path

    js = Path("web_ui/static/js/app.js").read_text()
    assert "'add_funds'" in js
    assert "appendFunds(action)" in js
