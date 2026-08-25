"""Where the keyboard goes when a dialog closes.

Source-inspection, like the other cross-language checks in this suite: the
behaviour lives in `app.js` and runs in a browser, and what needs defending is
a decision rather than a line of code.
"""

from pathlib import Path

APP_JS = Path(__file__).resolve().parents[1] / "web_ui" / "static" / "js" / "app.js"


def source() -> str:
    return APP_JS.read_text()


def test_closing_a_dialog_does_not_leave_focus_on_the_body():
    """`remembered.focus()` looks like it restores focus and, for a dialog
    opened by pressing a button, it does. Two cases it silently does nothing
    for -- and "nothing" means the body, so the next Tab starts again from the
    top of the document and a screen reader announces none of it:

      * a dialog nothing opened. The welcome appears on load, when the active
        element is the body, so the body is what is remembered and refocused --
        and that is the first thing a new user meets,
      * a button that has since been re-rendered away, leaving a detached node
        that cannot take focus.
    """
    js = source()
    assert "function restoreFocus(" in js
    assert "restoreFocus(ret)" in js, "__closeModal has to go through it"
    assert "remembered !== document.body" in js
    assert "document.contains(remembered)" in js, "a detached trigger is unfocusable"


def test_it_checks_where_focus_landed_rather_than_trusting_the_call():
    """The whole bug was a call that cannot fail. Asking for the result is the
    only way to know it worked."""
    js = source()
    assert "document.activeElement === remembered" in js


def test_there_is_somewhere_to_fall_back_to():
    """The message box, or the main landmark. Both are focusable -- `main` has
    tabindex="-1" for exactly this."""
    js = source()
    start = js.index("function restoreFocus(")
    body = js[start:start + 900]
    assert "chat-textarea" in body
    assert "main-content" in body


def test_the_main_landmark_can_actually_take_focus():
    base = APP_JS.parents[2] / "templates" / "base.html"
    assert '<main id="main-content" tabindex="-1">' in base.read_text()


def test_the_bar_buttons_wear_their_focus_ring_straight():
    """The seam between the left-hand bar buttons used to be a border, which is
    two pixels of the button's own width on the right only -- so the words sat
    two pixels left of centre and the focus ring, which wraps the box rather
    than the words, looked shifted right. Every other control in the app has a
    centred ring, and the odd one out is the one people notice.

    Drawn inside the box now, with the room made on both sides."""
    from pathlib import Path

    css = Path("web_ui/static/css/style.css").read_text()
    at = css.index(".app-bar-side:first-child .app-bar-btn {")
    rule = css[at:css.index("}", at)]
    assert "border-right" not in rule, "the seam is back to being part of the box"
    assert "box-shadow: inset" in rule
    assert "padding: 0 calc(var(--bar-pad) + var(--seam))" in rule

    # Two more things it took a pixel-by-pixel look to find. The focused
    # button's own seam sits just inside the ring on the right, so that edge
    # reads as doubled -- it goes while focused, and the ring does the seam's
    # job. And the ring sits flush rather than a pixel out: these buttons are
    # shoulder to shoulder, so the offset gap lands on the *neighbour's* seam
    # and shows it as a muddy line down one side only.
    assert ".app-bar-side:first-child .app-bar-btn:focus-visible { box-shadow: none; }" in css
    at = css.index(".app-bar-btn:focus-visible, .quit-x:focus-visible {")
    focus_rule = css[at:css.index("}", at)]
    assert "outline-offset: 0" in focus_rule
