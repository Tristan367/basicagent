"""What a stranger meets, and what went wrong when one did.

Everything here was reported by somebody sitting in front of the app for the
first time. None of it was found by reading the code, and most of it could not
have been: it is the difference between what the code does and what a person
watching it concludes is happening.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "web_ui" / "static" / "js" / "app.js").read_text()
CSS = (ROOT / "web_ui" / "static" / "css" / "style.css").read_text()
BASE = (ROOT / "web_ui" / "templates" / "base.html").read_text()
DESKTOP = (ROOT / "agent_server" / "desktop.py").read_text()


# ── the assistant that agreed and then did nothing ─────────────────────────


def test_a_promise_with_no_tool_call_is_not_the_end_of_the_turn():
    """"Right on, give me a second to spin that up" -- and then the turn ended,
    because saying a thing and doing it are separate acts and only the first
    one happened. From the user's side the assistant agreed cheerfully and then
    nothing occurred, for ever: nothing to click, nothing to wait for, and no
    way to tell it from a crash."""
    from agent_server.agent import _promised_to_act

    for said in ("Right on! Give me just a second to spin that up.",
                 "Sure - I'll create that project for you now.",
                 "On it.",
                 "Let me set that up for you."):
        assert _promised_to_act(said), said


def test_a_question_is_not_a_broken_promise():
    """When the assistant needs something it asks, and being nagged to "do it
    now" instead of waiting for the answer would be worse than the bug."""
    from agent_server.agent import _promised_to_act

    for said in ("What would you like the band's site to be called?",
                 "I'll need to know your band's name first - what is it?",
                 "Done. The site is ready - press Play to see it.",
                 "Let me know if you want it in a different colour."):
        assert not _promised_to_act(said), said


def test_the_nudge_happens_once_and_only_with_nothing_done():
    """Twice would be a loop, and an assistant that did the work and then said
    "I'll tell you when it lands" has kept its word."""
    source = (ROOT / "agent_server" / "agent.py").read_text()
    at = source.index("if (tools and not nudged")
    block = source[at:at + 400]
    assert "not nudged" in block
    assert "nudged = nudge_now = True" in block
    # Said to the model, never written into the conversation the user reads.
    assert '"role": "system", "content": _KEEP_YOUR_WORD' in source


def test_both_assistants_are_told_the_rule_as_well():
    """The nudge is a net. The prompt is the fix."""
    for named in ("manager.md", "agent.md"):
        text = (ROOT / "system_prompts" / named).read_text()
        assert "Say it and do it in the same breath" in text, named


# ── being able to tell it is alive ─────────────────────────────────────────


def test_something_is_on_screen_between_send_and_the_first_word():
    """There was nothing. The thinking row only appears when a model streams
    its reasoning, and most do not - so the whole wait was a blank space under
    your own message, indistinguishable from a request that had failed."""
    assert "function showWaiting()" in APP_JS
    begin = APP_JS[APP_JS.index("function beginTurn()"):]
    assert "showWaiting();" in begin[:400]
    for arrival in ("case 'reasoning':", "case 'content':", "case 'tool_start':"):
        at = APP_JS.index(arrival)
        assert "clearWaiting()" in APP_JS[at:at + 120], arrival
    assert "clearWaiting();" in APP_JS[APP_JS.index("function endTurn()"):][:200]


# ── read-aloud that could not keep up with itself ──────────────────────────


def test_speech_is_synthesised_one_at_a_time():
    """phonemizer is not thread-safe: two calls landing together corrupt each
    other's counters, which surfaces as "number of lines in input and output
    must be equal" and a sentence that is simply never spoken. The page fetches
    the next sentence while the current one plays, so overlapping calls are the
    normal shape of reading a reply aloud, not an edge case. Found by running
    three at once: it failed, and then passed on a retry."""
    tts = (ROOT / "agent_server" / "tts.py").read_text()
    assert "ThreadPoolExecutor(max_workers=1" in tts
    assert "_synth_pool," in tts
    assert "asyncio.to_thread(\n            kokoro.create" not in tts


def test_the_page_keeps_more_than_one_sentence_in_hand():
    """One was enough on a fast machine and nowhere near enough on a laptop:
    the synthesiser sat idle through each sentence's playback and then started
    cold on the next, so every full stop was a wait."""
    assert "const AHEAD = 3;" in APP_JS
    at = APP_JS.index("const AHEAD = 3;")
    assert "i < startIndex + AHEAD" in APP_JS[at:at + 400]


# ── the window it all happens in ───────────────────────────────────────────


def test_the_microphone_is_allowed_before_anybody_presses_talk():
    """Dictation is the entire interface for somebody who cannot use a
    keyboard. Without this getUserMedia was refused before any prompt could be
    shown, so pressing Talk did nothing at all, on every fresh install."""
    assert 'permissions=["microphone"]' in DESKTOP


def test_a_refused_microphone_says_so():
    """The silence was the whole bug: the failure path cancelled and told
    nobody, so "I pressed Talk and nothing happened" was the only symptom."""
    at = APP_JS.index("cancelDictation();\n      const name")
    block = APP_JS[at:at + 900]
    assert "NotAllowedError" in block and "NotReadableError" in block
    assert "showToast" in block and "announce(said)" in block


def test_chromium_is_not_asked_to_remember_an_api_key():
    """Saving a Gemini key made the browser offer to store it as a password -
    a password manager the user does not know they have, inside something that
    is not supposed to look like a browser at all."""
    assert "password_manager_enabled" in DESKTOP
    assert "credentials_enable_service" in DESKTOP
    assert "_settle_profile()" in DESKTOP


def test_a_link_out_goes_straight_to_the_real_browser():
    """It used to open a second bare window of the app, sit there, and vanish
    before the real browser started - which reads as a fault, on the first link
    a new user ever clicks."""
    assert "window.__inApp" in APP_JS
    assert "fetch('/_open'" in APP_JS
    from agent_server.routes import pages

    assert hasattr(pages, "open_outside")
    source = (ROOT / "agent_server" / "routes" / "pages.py").read_text()
    # Only web links: this hands a string to the operating system's "open this"
    # handler, which will act on `file:` and on anything another program has
    # registered a scheme for.
    assert 'parsed.scheme not in ("http", "https")' in source


def test_the_window_says_the_app_and_not_the_page():
    """The frame Windows draws around the app shows this, so a per-page title
    put the word "Settings" where a program name belongs, next to the operating
    system's own close button."""
    assert "<title>Assistant</title>" in BASE
    for named in ("settings.html", "about.html", "chat.html"):
        assert "block title" not in (ROOT / "web_ui" / "templates" / named).read_text(), named


def test_there_is_an_icon_that_is_ours():
    """Chromium's blank-document mark is the icon of "some web page", not of a
    program somebody installed."""
    assert 'href="/static/img/favicon.ico' in BASE
    assert 'href="/static/img/icon.svg' in BASE
    for named in ("favicon.ico", "icon.svg", "icon.png"):
        assert (ROOT / "web_ui" / "static" / "img" / named).is_file(), named


def test_there_are_not_two_close_buttons_in_the_same_corner():
    """Inside the app's own window the operating system already draws one, an
    inch to the right of ours."""
    assert ".in-app .quit-x { display: none; }" in CSS
    assert "in-app" in DESKTOP


# ── dialogs that fitted the screen, and started at the top ─────────────────


def test_a_dialog_never_grows_past_the_window_however_far_it_is_zoomed():
    """`zoom` on <html> scales what is rendered but leaves `vh` measuring the
    unzoomed viewport, so `max-height: 90vh` rendered at 90% of the screen
    times the zoom. At 150% a dialog stood half again taller than the window
    with its top and bottom unreachable however far you scrolled - and somebody
    who has zoomed in has done so because they cannot read it otherwise."""
    assert "--vh: calc(1vh / var(--zoom, 1));" in CSS
    # Declarations only: the definition of --vh itself is the one place a raw
    # `vh` belongs, and the comments explaining all this mention the old values.
    leftover = [line.strip() for line in CSS.splitlines()
                if re.search(r"^[^/*]*:\s*[^;]*\b\d+vh\b", line)
                and "var(--vh)" not in line
                and "--vh:" not in line]
    assert not leftover, f"these still measure the unzoomed viewport: {leftover}"


def test_a_dialog_opens_at_the_top_of_itself():
    """Focusing a control scrolls it into view, and the welcome dialog's first
    focusable is the button at the very bottom - so the first thing anybody
    ever saw of this app was its own dialog already scrolled past, with the
    greeting above the fold."""
    at = APP_JS.index("window.__openModal = function")
    block = APP_JS[at:at + 1200]
    assert "preventScroll: true" in block
    assert "scrollTop = 0" in block


def test_the_scrollbar_stays_inside_the_rounded_corner():
    assert ".welcome-card::-webkit-scrollbar-track" in CSS
    at = CSS.index(".welcome-card::-webkit-scrollbar-track")
    assert "margin:" in CSS[at:at + 200]


def test_the_projects_menu_hangs_square_under_its_button():
    """Measured in a real browser rather than guessed: it sat two pixels left
    and one pixel high of the button it belongs to."""
    at = CSS.index(".sessions-menu {")
    block = CSS[at:CSS.index("}", at)]
    assert "top: 100%;" in block
    assert "left: 0;" in block
    assert "calc(-1 * var(--seam))" not in block
