"""Making everything bigger or smaller, and the things that got it wrong.

The app has its own zoom, because a user who needs larger text should not have
to know that their browser has a keyboard shortcut -- and in the app window
there is no menu to find it in either. It is CSS `zoom` on the root element,
which is real layout zoom: text is laid out and drawn at the new size rather
than being drawn small and stretched, so it stays sharp.

What that does *not* do is change the window. Every length in the stylesheet
scales; the viewport does not. So anything that compares a number against the
window -- a media query, a hand-typed pixel offset meant to line up with a
border -- is asking the wrong question the moment somebody zooms. Each test here
is one place that went wrong.

These read the source rather than a running browser: the property being checked
is that the code does not contain the mistake, and a browser cannot tell you
which of two identical-looking layouts came from the right rule.
"""

from pathlib import Path

import pytest

CSS = Path("web_ui/static/css/style.css")
APP_JS = Path("web_ui/static/js/app.js")
SETTINGS_JS = Path("web_ui/static/js/settings.js")


@pytest.fixture(scope="module")
def css():
    """The rules, with the comments taken out.

    The comments here explain the mistakes below in the words the tests use, so
    a test asking whether a mistake is still present finds its own description
    of it and fails."""
    import re

    return re.sub(r"/\*.*?\*/", "", CSS.read_text(), flags=re.S)


@pytest.fixture(scope="module")
def app_js():
    return APP_JS.read_text()


# ── the number beside the buttons ──────────────────────────────────────────


def test_the_zoom_the_panel_shows_is_read_rather_than_assumed(app_js):
    """Settings arrives as markup fetched long after the zoom was applied, so
    its "100%" is whatever the template hard-coded. Set to 80% and restarted,
    the panel said 100% until you pressed a button, at which point it jumped to
    the truth -- so the one number telling you what the setting is was wrong
    exactly when you went to look at it."""
    assert "window.__showZoom" in app_js
    assert "__showZoom" in SETTINGS_JS.read_text(), \
        "the panel never corrects the number it was rendered with"


# ── the conversation's width ───────────────────────────────────────────────


def test_zooming_out_does_not_narrow_the_conversation(css, app_js):
    """Everything scaling includes the column the words are in, so at 80% the
    conversation sat in a thin ribbon with two thirds of the screen as empty
    gutter. Below 100% the column is widened by as much as the zoom shrinks it,
    which leaves the reading width on the glass where it was."""
    assert "--zoom-widen" in app_js, "nothing computes the correction"
    assert "1 / Math.min(z, 1)" in app_js, \
        "the correction is not clamped to zoom-out only"
    block = css[css.index(".chat-scroll-inner {"):]
    block = block[:block.index("}")]
    assert "var(--zoom-widen" in block


def test_zooming_in_still_closes_the_gutters_up(app_js):
    """The other half: above 100% the column keeps its own width and the gutters
    give way, which is what somebody zooming in is asking for."""
    line = next(ln for ln in app_js.splitlines() if "--zoom-widen" in ln and "setProperty" in ln)
    assert "Math.min(z, 1)" in line, "above 100% it would keep widening"


# ── lines that are supposed to meet ────────────────────────────────────────


def test_the_projects_menu_is_not_positioned_by_hand_typed_pixels(css):
    """It hung off the bar by `top: calc(100% + 2px)` against a border that was
    separately `2px`. Under zoom those two stopped being equal and a hairline
    seam opened and closed between the menu and the bar as you zoomed. One
    length now, and the menu sits on the bar's border rather than below it, so
    rounding changes an overlap instead of opening a gap."""
    block = css[css.index(".sessions-menu {"):]
    block = block[:block.index("}")]
    assert "var(--seam)" in block
    assert "100% + 2px" not in block and "left: -2px" not in block


def test_the_bar_and_its_menus_share_one_line_width(css):
    """The bar's bottom edge, the dividers between its buttons and the menu's
    own border are the same line as far as a reader is concerned, so they are
    the same value here."""
    assert "--seam:" in css
    for rule in (".app-bar {", ".app-bar-side:first-child .app-bar-btn"):
        block = css[css.index(rule):]
        block = block[:block.index("}") + 1]
        assert "var(--seam)" in block, f"{rule} still hard-codes its border"


# ── decisions about how wide the page is ───────────────────────────────────


def test_the_gutter_button_asks_about_the_page_not_the_window(css, app_js):
    """`@media (min-width: 1180px)` measures the window. At 130% on a 1400px
    window the page is 1077px wide and has no gutter, but the media query still
    saw 1400 and left the settings button in the corner -- where the composer's
    own controls, grown by the same zoom, ended up underneath it."""
    assert "min-width: 1180px" not in css, "the gutter decision is a media query again"
    assert "wide-gutter" in css and "wide-gutter" in app_js
    assert "innerWidth / window.__readZoom()" in app_js


def test_without_the_script_the_gear_stays_somewhere_it_fits(css):
    """The fallback half of that: `wide-gutter` is only ever added, so if the
    script never runs the gear stays on the bar, which works at every width."""
    assert ".settings-fab {\n    display: none;" in css, \
        "the corner button shows by default, so a page with no script gets two gears"


def test_the_separator_above_new_project_is_its_own_line(css):
    """It was a border on the button, so it followed the button's rounded
    corners and curled up at both ends -- which read as a stray shadow under a
    button that was not there."""
    block = css[css.index(".sessions-new {"):]
    block = block[:block.index("}")]
    assert "border-top" not in block
    assert ".sessions-sep {" in css
    assert '<hr class="sessions-sep">' in Path("web_ui/templates/base.html").read_text()


# ── the way home ───────────────────────────────────────────────────────────


def test_the_house_stands_up_straight():
    """Drawn by hand it closed with `z` from the foot of the left wall back to
    the eave -- two different x values, so the whole left side leaned."""
    import re
    from pathlib import Path

    page = Path("web_ui/templates/base.html").read_text()
    roof = re.search(r'd="M([\d.]+) [\d.]+ 12 [\d.]+l([\d.]+) ', page)
    assert roof, "the roof is not drawn eave-apex-eave any more"
    left, run = float(roof.group(1)), float(roof.group(2))
    assert 12 - left == run, "the eaves are not the same distance from the middle"

    foot = re.search(r"H([\d.]+)a2 2 0 0 1-2 ?-2Z", page)
    assert foot, "the left wall does not end in the corner it should"
    assert float(foot.group(1)) - 2 == left, "the close is a diagonal, not a wall"


def test_the_house_is_the_colour_of_the_words_beside_it(css):
    """Picked out in the accent it read as a third state on a bar that already
    uses that colour for one thing: where you are."""
    block = css[css.index(".app-bar-btn.home-btn {"):]
    block = block[:block.index(".title-home")]
    assert "var(--accent)" not in block


def test_the_house_is_on_the_page_it_points_at_too():
    """Everywhere else it is the way back; on the home page it is where you
    are, so it takes the heading's colour rather than the button's."""
    from pathlib import Path

    page = Path("web_ui/templates/base.html").read_text()
    title = page[page.index('<h1 class="app-bar-title">'):page.index("</h1>")]
    assert "{% if is_home %}" in title
    assert 'class="title-home"' in title
    # Decoration: the heading already says Project Manager.
    assert 'aria-hidden="true"' in title


def test_the_door_stands_on_the_floor_rather_than_through_it():
    """A round cap adds half a stroke past the point it stops at, so the
    doorposts hung below the wall the house stands on."""
    import re
    from pathlib import Path

    page = Path("web_ui/templates/base.html").read_text()
    doors = re.findall(r'stroke-linecap="(\w+)"\s*\n\s*d="M9\.4 21v', page)
    assert doors, "the door is not drawn where it was"
    assert set(doors) == {"butt"}, f"round caps are back: {doors}"


# ── the settings panel's shadow ────────────────────────────────────────────
#
# Not a zoom bug, but the same shape of mistake: a value that looks right in
# isolation and is wrong against the thing next to it.


def test_the_panel_shadow_is_clipped_off_the_app_bar(css):
    """A 42px blur on the panel reached up over the bar and greyed it -- in
    light mode from 245 to 228 just above the seam. The shadow moved to a
    pseudo-element so `clip-path` can cut it flat along the panel's top edge
    without also clipping the panel's contents."""
    import re

    panel = re.search(r"\.settings-panel \{(.*?)\}", css, re.S).group(1)
    assert "box-shadow" not in panel, \
        "the shadow is back on the panel itself, where it bleeds over the app bar"

    shadow = re.search(r"\.settings-panel::after \{(.*?)\}", css, re.S).group(1)
    assert "box-shadow" in shadow
    # First inset is the top: it has to be zero, so nothing shows above the
    # panel. A negative value there lets the bleed straight back through.
    top = re.search(r"clip-path:\s*inset\(\s*(-?\d+)", shadow).group(1)
    assert top == "0", f"the shadow is clipped {top}px above the panel, not at its edge"


def test_the_app_bar_stays_under_the_settings_panel(css):
    """Raising the bar above the panel also clips the bleed, and breaks the
    walkthrough overlay: it is rendered inside the panel, so it cannot paint
    above the panel's own layer whatever its z-index says, and the bar covers
    it."""
    import re

    bar = re.search(r"\.app-bar \{(.*?)\}", css, re.S).group(1)
    panel = re.search(r"\.settings-panel \{(.*?)\}", css, re.S).group(1)
    bar_z = int(re.search(r"z-index:\s*(\d+)", bar).group(1))
    panel_z = int(re.search(r"z-index:\s*(\d+)", panel).group(1))
    assert bar_z < panel_z, "the app bar would cover the walkthrough overlay"


def test_the_projects_menu_is_lifted_over_the_panel_while_it_is_open(css):
    """The menu hangs out of the bar and so is stuck in the bar's layer. With
    Settings open, pressing Projects lit the button and dropped a menu behind
    the panel, where nobody could see it."""
    assert '.app-bar:has(#sessions-btn[aria-expanded="true"])' in css
