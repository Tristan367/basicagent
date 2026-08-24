"""Pointing at part of the page.

Two halves get tested differently. The Python half -- what a pick turns into
for the model, and what happens when nobody picks -- is tested directly. The
JavaScript half runs in a browser and cannot be imported, so it is tested the
way the rest of this project pins cross-language invariants: by reading the
source and asserting on the things that would break silently if they changed.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_server import annotate

# A pick from a plain HTML page: no framework, no source file, everything else.
PLAIN = {
    "tag": "button",
    "id": "buy-wheels",
    "classes": "buy primary",
    "selector": "#buy-wheels",
    "openTag": '<button class="buy primary" id="buy-wheels">',
    "html": '<button class="buy primary" id="buy-wheels">Buy wheels</button>',
    "text": "Buy wheels",
    "attrs": {"type": "button"},
    "rect": {"w": 142, "h": 40, "x": 20, "y": 300},
    "styles": {"fontSize": "14px", "color": "rgb(255, 255, 255)",
               "background": "rgb(37, 99, 235)", "display": "inline-flex"},
    "framework": None,
    "components": [],
    "source": None,
    "url": "http://127.0.0.1:5173/",
}

# React 18, which still had `_debugSource`.
REACT_18 = dict(PLAIN, framework="react",
                components=["BuyButton", "Card", "App"],
                source="/home/kid/shop/src/BuyButton.jsx")

# React 19 / Next: the component chain survives, the file does not.
REACT_19 = dict(PLAIN, framework="react",
                components=["BuyButton"], source=None)


# ── what the model is handed ────────────────────────────────────────────────


def test_the_element_itself_is_always_there():
    """No framework, no file, and it is still a usable description."""
    text = annotate.describe(PLAIN)
    assert '<button class="buy primary" id="buy-wheels">' in text
    assert "Buy wheels" in text
    assert "#buy-wheels" in text
    assert "142×40" in text


def test_a_source_file_is_named_when_there_is_one():
    text = annotate.describe(REACT_18)
    assert "file: /home/kid/shop/src/BuyButton.jsx" in text
    assert "BuyButton inside Card inside App" in text


def test_a_path_inside_the_project_is_shortened_to_fit():
    """React and Vue both hand back an absolute path from the build machine."""
    text = annotate.describe(REACT_18, "/home/kid/shop")
    assert "file: src/BuyButton.jsx" in text
    assert "/home/kid/shop" not in text


def test_a_path_outside_the_project_is_left_alone():
    """Relative to nothing is worse than long. A linked dependency stays whole."""
    outside = dict(REACT_18, source="/usr/lib/node_modules/thing/Button.jsx")
    assert "/usr/lib/node_modules/thing/Button.jsx" in annotate.describe(outside, "/home/kid/shop")


def test_no_source_file_says_search_instead_of_nothing():
    """The React 19 case, which is the one that has to not read as a failure.

    The component name is enough -- `grep -rn BuyButton` ends the search -- so
    the line says to go and look rather than leaving a gap where a path was.
    """
    text = annotate.describe(REACT_19)
    assert "file:" not in text
    assert "search the project for BuyButton" in text


def test_a_source_file_makes_the_search_line_unnecessary():
    assert "search the project for" not in annotate.describe(REACT_18)


def test_the_description_is_flat_text_not_json():
    """It arrives beside a sentence the user typed and must not drown it."""
    text = annotate.describe(PLAIN)
    assert "{" not in text and "}" not in text


# ── what the user sees on the chip ──────────────────────────────────────────


def test_the_chip_says_what_they_clicked():
    assert annotate.summarise(PLAIN) == "Buy wheels"


def test_the_chip_falls_back_to_a_label_then_an_id_then_a_tag():
    no_text = dict(PLAIN, text="")
    assert annotate.summarise(dict(no_text, attrs={"aria-label": "Close"})) == "Close"
    assert annotate.summarise(dict(no_text, attrs={})) == "#buy-wheels"
    assert annotate.summarise(dict(no_text, attrs={}, id="")) == "<button>"


def test_a_long_label_is_cut_so_the_chip_stays_a_chip():
    label = annotate.summarise(dict(PLAIN, text="x" * 200))
    assert len(label) <= 41 and label.endswith("…")


# ── waiting for a pick ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_pick_reaches_whoever_is_waiting():
    async def click_shortly():
        await asyncio.sleep(0.01)
        annotate.deliver("s1", PLAIN)

    task = asyncio.create_task(click_shortly())
    assert (await annotate.wait_for_pick("s1"))["id"] == "buy-wheels"
    await task


@pytest.mark.asyncio
async def test_escape_comes_back_as_nothing_rather_than_hanging():
    async def escape_shortly():
        await asyncio.sleep(0.01)
        annotate.deliver("s2", None)

    task = asyncio.create_task(escape_shortly())
    assert await annotate.wait_for_pick("s2") is None
    await task


@pytest.mark.asyncio
async def test_a_closed_window_releases_the_waiter():
    """`forget` is what a closing preview calls, and it must not strand anyone."""
    async def close_shortly():
        await asyncio.sleep(0.01)
        annotate.forget("s3")

    task = asyncio.create_task(close_shortly())
    assert await annotate.wait_for_pick("s3") is None
    await task


@pytest.mark.asyncio
async def test_arming_twice_cancels_the_first_wait():
    """Pressing the button again means they changed their mind, not a queue."""
    first = asyncio.create_task(annotate.wait_for_pick("s4"))
    await asyncio.sleep(0.01)
    second = asyncio.create_task(annotate.wait_for_pick("s4"))
    await asyncio.sleep(0.01)
    annotate.deliver("s4", PLAIN)
    assert await first is None
    assert (await second)["id"] == "buy-wheels"


@pytest.mark.asyncio
async def test_nothing_is_left_pending_afterwards():
    annotate.deliver("s5", None)          # no waiter: must not explode
    task = asyncio.create_task(annotate.wait_for_pick("s5"))
    await asyncio.sleep(0.01)
    assert annotate.waiting("s5")
    annotate.deliver("s5", PLAIN)
    await task
    assert not annotate.waiting("s5")


# ── the browser half, pinned by reading it ──────────────────────────────────


def test_the_picker_installs_itself_only_once():
    """`add_init_script` runs per frame per navigation; twice would double the
    listeners and pick the same element twice."""
    assert "window.__pickerInstalled" in annotate.PICKER_JS


def test_the_picker_does_nothing_until_armed():
    """It is injected into the user's own app. Inert is the resting state."""
    js = annotate.PICKER_JS
    assert "let armed = false" in js
    assert "if (!armed) return;" in js


def test_arming_and_disarming_are_both_reachable_from_python():
    for name in ("__pickerArm", "__pickerDisarm", "__annotatePick"):
        assert name in annotate.PICKER_JS


def test_the_overlay_cannot_swallow_the_click_it_is_watching_for():
    """The banner and highlight sit over the page. `pointer-events:none` is what
    keeps `elementFromPoint` -- and the click itself -- landing on the page."""
    assert "pointer-events:none" in annotate.PICKER_JS


def test_the_overlay_is_never_itself_picked():
    assert "classList.contains('__pick-ui')" in annotate.PICKER_JS


def test_escape_cancels():
    assert "'Escape'" in annotate.PICKER_JS


def test_the_whole_press_is_swallowed_not_just_the_mousedown():
    """Picking happens on `mousedown`, and `mouseup` and `click` follow from the
    same press. Point at a link without this and the page you were pointing at
    navigates away underneath you."""
    js = annotate.PICKER_JS
    assert "document.addEventListener('mouseup', swallow, true);" in js
    assert "document.addEventListener('click', swallow, true);" in js
    # And they come straight back off, or the next real click is eaten too.
    assert "document.removeEventListener(e.type, swallow, true);" in js


def test_the_highlight_keeps_up_with_scrolling():
    """Scrolling to find the thing you want to click is how pages are used. The
    highlight is in viewport coordinates, so it needs telling."""
    js = annotate.PICKER_JS
    assert "window.addEventListener('scroll', onScroll, true);" in js
    assert "window.removeEventListener('scroll', onScroll, true);" in js


def test_there_is_a_way_through_without_a_mouse():
    """This app is built for people who cannot use one. A picker that needs a
    mouse would be the one feature they are locked out of."""
    js = annotate.PICKER_JS
    for key in ("ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Enter"):
        assert key in js


def test_react_19_removing_debug_source_is_expected_not_assumed():
    """`_debugSource` is read when present and never depended on: the chain is
    collected separately, so React 19 still yields a component name."""
    js = annotate.PICKER_JS
    assert "_debugSource" in js
    assert "out.components.length || out.source" in js


def test_framework_noise_stops_the_walk_rather_than_being_listed_away():
    """The chain out of a Next click is `BuyButton` and then a dozen internals.

    Filtering by name alone can only ever catch the ones seen so far -- Next
    renames them between versions -- so the walk stops at the first framework
    component instead. They all sit above the user's own tree, so the first one
    marks the end of the part anybody cares about.
    """
    js = annotate.PICKER_JS
    assert "verdict === STOP && out.components.length" in js
    # Three answers, not two: a `<div>` fiber is skipped over, not stopped at.
    assert "const SKIP = 0, TAKE = 1, STOP = 2;" in js
    assert js.count("verdict === STOP") == 2  # React and Vue both


def test_the_noise_list_covers_what_was_actually_seen():
    for noise in ("Boundary", "Router", "Provider", "Suspense", "Handler"):
        assert noise in str(annotate._NOISE)


def test_the_noise_list_reaches_the_javascript_as_valid_json():
    """It is interpolated into a script. Python's repr of a tuple of strings is
    not JavaScript, and single quotes inside a `%`-formatted literal would be."""
    assert '"Boundary"' in annotate.PICKER_JS
    assert "'Boundary'" not in annotate.PICKER_JS


# ── nobody gets stranded in picking mode ───────────────────────────────────
#
# Verified end to end in a real window before these were written: cancel from
# the app, Escape in the window, and a completed pick all leave the banner gone
# and the user's own page clickable again. These pin the three exits, because
# the failure they prevent is silent -- a page that looks normal and eats every
# click, in an app whose user has no idea a picking mode exists.


def test_the_page_is_let_go_however_the_pick_ends():
    """A pick that comes back with nothing is Escape, a closed window, or three
    minutes of silence. All three leave a page still armed unless something
    says otherwise, and an armed page swallows every click."""
    import inspect

    from agent_server.routes import sessions

    source = inspect.getsource(sessions.preview_pick)
    after_none = source.split("if picked is None")[1]
    assert "preview.disarm" in after_none, (
        "a pick that returns nothing leaves the page eating clicks")


def test_cancelling_from_the_app_reaches_the_page():
    """The button doubles as a cancel, and the app cancelling only its own
    side would leave the window in picking mode with nothing on screen in the
    app to suggest why the project has stopped responding."""
    import inspect

    from agent_server.routes import sessions

    source = inspect.getsource(sessions.preview_pick_cancel)
    assert "annotate.forget" in source, "the waiter is left hanging"
    assert "preview.disarm" in source, "the page is left armed"


def test_a_successful_pick_disarms_the_page_itself():
    """Not from Python -- from inside the page, at the moment of the click, so
    there is no window in which the next click is also swallowed."""
    from agent_server.annotate import PICKER_JS

    pick_body = PICKER_JS.split("function pick(")[1].split("function ")[0]
    assert "disarm()" in pick_body, "picking something leaves the picker on"


def test_the_banner_says_how_to_get_out():
    """The one instruction that matters, on screen, in the window itself --
    not in the app, which is not where the user is looking."""
    from agent_server.annotate import PICKER_JS

    assert "Esc" in PICKER_JS
    banner = [line for line in PICKER_JS.splitlines() if "textContent" in line
              and "Esc" in line]
    assert banner, "the way out is not written on the banner"


def test_picking_cannot_outlast_the_user_s_patience():
    """A pick nobody ever makes has to end by itself: the window may have been
    walked away from, and the request behind it is holding a connection."""
    from agent_server.annotate import PICK_TIMEOUT

    assert 60 <= PICK_TIMEOUT <= 600, PICK_TIMEOUT
