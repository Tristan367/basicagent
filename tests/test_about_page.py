"""The page for somebody deciding whether to let their child near this.

It is the one piece of writing in the app aimed at a person who has never used
one of these and is not sure it is a good idea. The rule the whole page is
written under is that nothing in it may be a comforting thing that is not true
-- a parent reassured by something false is worse off than one who was never
reassured, because they stop looking.

So the tests are about that rule as much as about the page existing.
"""

from pathlib import Path

PAGE = Path("web_ui/templates/about.html").read_text()
# The page is prose, wrapped for reading rather than for grepping, so a
# sentence in it is nearly always split across two lines. Assertions about what
# it says go against this.
SAID = " ".join(PAGE.split())


async def test_it_is_reachable_before_anything_is_set_up(db, monkeypatch):
    """The question it answers -- "should I let my child near this?" -- comes
    before "how do I get a key?", and being answered in that order is the whole
    reason it exists."""
    from agent_server.routes import context, pages

    monkeypatch.setattr(context, "any_credentials", lambda: False)
    response = await pages.about(_Request())
    assert response.status_code == 200


async def test_it_carries_the_whole_page_context(db):
    """A hand-built context with only what the page itself needs was a 500 the
    first time it was loaded, because every page in this app wears the same bar.
    It would be a 500 again the next time the bar grew a field."""
    from agent_server.routes import pages

    response = await pages.about(_Request())
    for needed in ("stt", "nav_sessions", "child_mode"):
        assert needed in response.context_data if hasattr(response, "context_data") \
            else needed in response.context


def test_settings_shows_the_way_in_before_any_setting():
    """Somebody deciding whether to trust this needs it answered before "light
    or dark mode"."""
    body = Path("web_ui/templates/settings_body.html").read_text()
    assert 'href="/about"' in body
    assert body.index('href="/about"') < body.index("Appearance")


# ── the promises the page makes ────────────────────────────────────────────


def test_it_says_where_the_permission_line_actually_falls():
    """It covers the file tools, which is how the assistant handles files
    essentially all of the time. It does not cover every shell command. Drawing
    the reader a bigger line than exists would be the exact kind of comfortable
    falsehood this page exists to avoid."""
    assert "Files outside your project need your say-so" in SAID
    assert "allow everything in that folder from then on" in SAID
    assert "It does not cover every shell command" in SAID
    assert "only a grown-up can say yes" in SAID
    assert "Saying no never does" in SAID
    # And the short list of places it never asks about, which is the other half
    # of not training people to click past it.
    assert "temporary folder" in SAID


def test_it_says_plainly_that_nothing_asks_permission():
    """Every other tool like this interrupts constantly. Somebody who expects
    that and does not get it should have been told, not left to notice."""
    assert "It does not stop to ask permission" in SAID
    assert "cannot run the handful of commands that" in SAID
    assert "for work inside your own" in SAID


def test_it_explains_forgetting_without_jargon():
    """Compaction is the one piece of machinery a user actually sees happen,
    and "context window" means nothing to them."""
    # The analogy is amnesia, not a library. Somebody who cannot form new
    # memories and copes with a stack of notes they re-read every morning is
    # what this actually is; a library you can only see one shelf of invites
    # the obvious question of why they cannot just fetch another book.
    assert "lost the ability" in SAID and "form new memories" in SAID
    assert "stack of notes" in SAID
    assert "Earlier conversation was summarized" in SAID
    assert "context window" not in SAID


def test_it_keeps_the_bad_parts_in_the_same_voice_as_the_good():
    for honest in ("energy and real water", "can be used to do harm",
                   "research is young and argued over"):
        assert honest in SAID, honest


def test_it_does_not_pretend_the_argument_is_settled():
    """Confident people disagree about the long run. A page that says otherwise
    is doing the thing it just told the reader to distrust."""
    assert "Thoughtful people do disagree about the long run" in SAID


def test_it_names_the_risk_that_is_actually_real():
    assert "prompt injection" in SAID
    assert "it is bad at working out who is telling it" in SAID
    assert "people problems are the kind we have always had" in SAID


def test_it_teaches_the_two_habits_that_matter():
    """Out-of-date knowledge and confident wrongness are the two things that
    bite every new user, and both have a one-sentence fix."""
    assert "look it up on the web first" in SAID
    assert "How could we check that?" in SAID


def test_it_ends_on_what_the_app_is_for():
    assert "AI lets us be more human" in SAID
    assert "Two futures" in SAID


class _Request:
    """The least a TemplateResponse needs to render."""

    url = None

    def __init__(self):
        self.scope = {"type": "http", "headers": [], "app": None}

    def __getattr__(self, name):
        return None


def test_it_says_this_app_was_built_the_same_way():
    """Not a demonstration of what the technique might one day manage. The
    technique, running."""
    assert "This app was built this way too" in SAID
    assert "It is the technique, running." in SAID


def test_it_names_the_way_out_when_somebody_outgrows_it():
    """It would be a strange kind of honesty to explain how all this works and
    then not mention that there is a better tool for somebody building
    constantly. The point is to get a family started, not to keep them."""
    assert "When you outgrow this" in SAID
    assert "Claude Code" in SAID
    assert "professional learning curve" in SAID
    assert "not to hold your family" in SAID


def test_it_says_the_barrier_was_years_of_building_not_only_years_of_learning():
    assert "cost years twice over" in SAID
    assert "shipped something half-finished" in SAID
