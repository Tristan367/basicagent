"""Reaching the message box without a mouse.

This app is for people who cannot use a mouse, so the first thing a keyboard
lands on matters more here than almost anywhere. There are two skip links; the
first one is the first focusable thing on the page and is what a screen reader
announces before anything else.

It used to point at the microphone. That reads as the right choice -- "Skip to
Talk", and dictation is how a blind user prefers to work -- but the mic sits
*after* the textarea in the composer, so from there Tab walked forward through
read-draft, attach, read-aloud, model and send, then wrapped to the top of the
page without ever landing in the box. Measured on a page with four messages on
it: seventy-seven tabs the long way round, and no forward route at all from
where the skip link had just put you.

Nothing is lost by landing in the box instead: the mic is one Tab away, and a
screen reader reads "Message -- type or speak your message", which offers both.
"""

from pathlib import Path

BASE = Path("web_ui/templates/base.html").read_text()
CHAT = Path("web_ui/templates/chat.html").read_text()


def _skip_link() -> str:
    start = BASE.index('id="skip-link"')
    return BASE[start:BASE.index("</a>", start)]


def test_the_first_skip_link_goes_to_the_message_box():
    link = _skip_link()
    assert "#chat-textarea" in link
    assert "#mic-btn" not in link, "back to the dead end"


def test_the_settings_page_keeps_its_own_target():
    """There is no message box there, and sending them to one would be worse
    than not offering a skip link at all."""
    assert "#main-content" in _skip_link()


def test_the_message_box_comes_before_the_mic():
    """The whole reason the old target was a trap. If these ever swap, the mic
    becomes reachable only by tabbing backwards, and this test is the warning."""
    assert CHAT.index('id="chat-textarea"') < CHAT.index('id="mic-btn"')


def test_dictation_is_one_step_from_where_you_land():
    """Between the textarea and the mic there must be nothing else focusable,
    or "press Tab once for the microphone" stops being true."""
    between = CHAT[CHAT.index('id="chat-textarea"'):CHAT.index('id="mic-btn"')]
    # Trim the mic's own opening tag off the end -- the slice stops at its id
    # attribute, so `<button type="button" ` is still hanging on it.
    between = between[:between.rindex("<")]
    for control in ("<button", "<input", "<textarea", "<select", "tabindex"):
        assert control not in between, f"{control} now sits between them"


def test_there_is_still_a_way_past_a_long_conversation():
    """Every message carries its own copy and read-aloud buttons, so a long
    conversation is a hundred stops before the composer. The second skip link
    is the whole answer to that and must keep pointing at the box."""
    assert 'id="skip-conversation"' in CHAT
    start = CHAT.index('id="skip-conversation"')
    assert '#chat-textarea' in CHAT[start - 200:start + 200]


def test_the_box_says_you_can_speak_into_it_too():
    """What replaces landing on the mic: the destination itself has to mention
    dictation, because that is all a screen reader will read out."""
    box = CHAT[CHAT.index('id="chat-textarea"'):]
    box = box[:box.index("</div>")]
    assert 'aria-label="Message"' in box
    assert "speak" in box.lower()
