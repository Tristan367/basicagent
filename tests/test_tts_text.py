"""Turning a written reply into something worth hearing.

This runs on every reply when read-aloud is on, for people who work by ear --
so a mistake here is not cosmetic. It is the difference between "I edited
agent underscore server slash tee tee ess dot pie" and a sentence.

Every case below came from reading a real reply out loud and hearing it go
wrong.
"""

import pytest

from agent_server.tts import normalise, plan, to_prose


def spoken(text: str) -> str:
    """What the synthesiser is finally handed, as one string."""
    return " ".join(plan(text))


# ── names, which most of a reply in this app is about ──────────────────────


@pytest.mark.parametrize("written, said", [
    ("main.py", "main dot pie"),
    ("app.js", "app dot J S"),
    ("styles.css", "styles dot C S S"),
    ("index.html", "index dot H T M L"),
    ("notes.md", "notes dot M D"),
    ("query.sql", "query dot sequel"),
    ("photo.jpg", "photo dot J peg"),
    ("component.tsx", "component dot T S X"),
    # Not in the table, and right without it.
    ("data.json", "data dot json"),
    ("Main.java", "Main dot java"),
    ("example.com", "example dot com"),
    ("utils.helpers", "utils dot helpers"),
])
def test_a_filename_is_said_the_way_people_say_it(written, said):
    """There is no rule to derive this from: ".py" is "pie" and ".js" is
    "J S", and both are simply what people say. Left alone the phonemiser runs
    the halves together into one unsayable word."""
    assert normalise(written) == said


def test_a_path_keeps_its_separators():
    """Dropping them makes "src/app.js" indistinguishable from prose."""
    assert normalise("src/app.js") == "src slash app dot J S"
    assert normalise("~/.config/app.toml") == "home slash dot config slash app dot toml"


def test_a_number_is_still_a_number_not_a_filename():
    """The decimal rule runs first and spends those dots, so nothing is left
    for the filename rule to find."""
    assert normalise("3.2") == "3 point 2"
    assert normalise("version 1.10") == "version 1 point 10"


def test_an_abbreviation_is_not_a_filename():
    assert normalise("e.g. this") == "for example this"
    assert normalise("i.e. that") == "that is that"
    assert "dot" not in normalise("etc.")


# ── identifiers ────────────────────────────────────────────────────────────


def test_an_underscore_in_a_name_is_not_italics():
    """The emphasis rule treated `_` as a mark, so it deleted the joint in
    every snake_case name: "agent_server/tts.py and web_ui/app.js" was read as
    "agentserver ... webui", and MAX_RETRY_COUNT as MAXRETRYCOUNT."""
    said = spoken("I edited agent_server/tts.py and web_ui/app.js")
    assert "agent server" in said and "web ui" in said
    assert "agentserver" not in said and "webui" not in said


def test_underscore_emphasis_still_works_around_a_word():
    assert to_prose("this is _important_ now").strip() == "this is important now."


def test_a_camel_case_hump_is_a_word_boundary_nobody_can_hear():
    assert normalise("getUserName") == "get User Name"


def test_a_long_hash_is_not_read_out_letter_by_letter():
    """Forty characters at one a second is a minute of someone's life."""
    said = normalise("commit a3f9c21b8e4d5567aa01bb99ff please")
    assert said == "commit a long code please"


# ── pauses ─────────────────────────────────────────────────────────────────


def test_a_colon_becomes_a_full_stop():
    """A colon produces no pause of its own, and what follows it is a new
    thought."""
    assert plan("Here's the thing: it works") == ["Here's the thing.", "It works."]


def test_a_time_is_not_a_colon_to_be_replaced():
    assert "10:30" in normalise("meet at 10:30 today")


def test_a_semicolon_becomes_a_full_stop():
    assert plan("I ran the tests; then committed") == ["I ran the tests.", "Then committed."]


def test_a_dash_between_clauses_becomes_a_full_stop():
    """It is doing the job of one -- the sentence restarts after it -- and a
    comma there is read as a breath rather than a stop."""
    assert plan("Next steps — I'll run the tests") == ["Next steps.", "I'll run the tests."]
    assert plan("Next steps -- I'll run the tests") == ["Next steps.", "I'll run the tests."]


def test_a_line_break_without_punctuation_becomes_a_full_stop():
    assert plan("First line\nSecond line") == ["First line.", "Second line."]


def test_a_line_that_already_pauses_does_not_get_a_second_mark():
    assert plan("First line,\nsecond line") == ["First line, second line."]
    assert plan("First line.\nSecond line.") == ["First line.", "Second line."]


def test_every_block_ends_on_a_pause():
    """A bullet or a heading that stops without punctuation is read with the
    intonation of a sentence still going, straight into the next one."""
    assert plan("## A heading\n\n- one\n- two") == ["A heading.", "One.", "Two."]


def test_a_colon_does_not_weld_a_list_onto_the_line_above_it():
    """The colon rule ate the blank line after it, which is the one thing the
    block splitter depends on."""
    said = plan("I changed these:\n\n- the first\n- the second")
    assert said == ["I changed these.", "The first.", "The second."]


# ── things that should not be spoken at all ────────────────────────────────


@pytest.mark.parametrize("text", [
    "It works now \U0001F389",
    "Done ✅",
    "Nice ✨ work",
    "\U0001F469‍\U0001F4BB coding",
    "Flag \U0001F1EC\U0001F1E7 here",
])
def test_emoji_are_not_read_out(text):
    said = spoken(text)
    assert all(ord(c) < 0x2500 for c in said), said


def test_code_blocks_are_not_read_out():
    said = spoken("Here it is:\n\n```python\ndef foo():\n    return 1\n```\n\nDone")
    assert "def foo" not in said and "return" not in said


def test_a_url_becomes_the_word_link():
    assert "a link" in spoken("read https://example.com/docs for more")
    assert "https" not in spoken("read https://example.com/docs for more")


# ── symbols ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("written, said", [
    ("50%", "50 percent"),
    ("$5", "5 dollars"),
    ("$0.28/M", "0 point 28 dollars per million"),
    ("hi@example.com", "hi at example dot com"),
    ("x != y", "x is not y"),
    ("count >= 3", "count is at least 3"),
    ("count <= 3", "count is at most 3"),
    ("old.py -> new.py", "old dot pie becomes new dot pie"),
    ("a && b", "a and b"),
    ("a || b", "a or b"),
    ("Wait… no", "Wait. No"),
])
def test_symbols_are_said_rather_than_skipped(written, said):
    assert normalise(written) == said


def test_a_comparison_is_not_mistaken_for_an_assignment():
    """`!=` has to be spent before the bare `=` rule, or "a != b" comes out as
    "a exclamation-mark equals b"."""
    assert "!" not in normalise("if a != b then")


def test_a_file_reference_says_which_lines():
    """This app's own convention: the assistant is told to write
    `src/app.js:120-140`, so the colon there is worth more than a full stop."""
    assert normalise("see src/app.js:120-140 now") == \
        "see src slash app dot J S, lines 120 to 140 now"
    assert normalise("see app.js:12 now") == "see app dot J S, line 12 now"


# ── the whole thing, on a reply of the shape this app produces ─────────────


def test_a_realistic_reply_reads_as_sentences():
    said = plan(
        "## What I changed ✅\n\n"
        "I edited `agent_server/tts.py`:\n\n"
        "- Version 3.2 is now the default — it was 1.10 before\n"
        "- See src/app.js:120-140 for the loop\n\n"
        "Here's the thing: it works now \U0001F389\n"
    )
    assert said == [
        "What I changed.",
        "I edited agent server slash tts dot pie.",
        "Version 3 point 2 is now the default.",
        "It was 1 point 10 before.",
        "See src slash app dot J S, lines 120 to 140 for the loop.",
        "Here's the thing.",
        "It works now.",
    ]


def test_nothing_comes_out_empty_or_all_whitespace():
    for text in ["", "   ", "\n\n", "\U0001F389", "```\ncode\n```", "---", "|a|b|"]:
        assert all(s.strip() for s in plan(text)), repr(text)


def test_sentences_never_returns_a_bare_mark():
    """A chunk of pure punctuation renders as a click or a beat of silence."""
    for s in plan("Wait — — what? Yes: yes."):
        assert any(c.isalnum() for c in s), repr(s)


# ── swearing, in child mode only ───────────────────────────────────────────
#
# Not a guard against the assistant: it has been told it is talking to a child
# and will not swear. This is for the thing a child *will* try -- typing a word
# and pressing play to hear the app say it back.


from agent_server.tts import without_swearing  # noqa: E402


@pytest.mark.parametrize("written, said", [
    ("what the fuck", "what the"),
    ("this is shit", "this is"),
    ("you stupid bitch", "you stupid"),
    ("oh damn", "oh"),
    ("fuck fuck fuck", ""),
])
def test_swearing_is_lifted_out_rather_than_bleeped(written, said):
    """A beep is a reward -- it tells a child they nearly got there. Silence is
    a duller answer and a truer one."""
    assert without_swearing(written) == said


def test_the_sentence_still_ends_properly():
    """Losing the full stop runs the sentence into the next one, which is
    exactly the fault the rest of this module exists to prevent."""
    assert without_swearing("Go away, damn it.") == "Go away, it."
    assert without_swearing("Well shit.") == "Well."
    assert without_swearing("Is it broken, damn?") == "Is it broken?"


def test_a_line_that_was_only_swearing_is_not_spoken_as_a_click():
    assert plan("shit", clean=True) == []
    assert plan("Hello.\n\nfuck\n\nGoodbye.", clean=True) == ["Hello.", "Goodbye."]


@pytest.mark.parametrize("word", [
    # Every one of these is mangled by substring matching, and a child doing
    # schoolwork will use all of them. This list is as load-bearing as the
    # word list itself.
    "class", "classic", "pass", "passage", "assignment", "assessment", "assassin",
    "assume", "assist", "bass", "compass", "embassy", "grass", "mass", "glass",
    "grape", "scrape", "Uranus", "cockatoo", "cockpit", "peacock", "shuttlecock",
    "Scunthorpe", "analysis", "titanium", "buttercup", "button", "shiitake",
    "hello", "shell", "shelter", "hellenic", "dickens", "Dickinson", "crapaud",
    "assemble", "association", "brassica", "molasses", "casserole", "cassette",
])
def test_ordinary_words_a_child_uses_survive(word):
    sentence = f"I wrote about the {word} today."
    assert without_swearing(sentence) == sentence, f"{word!r} was mangled"


def test_a_donkey_is_still_a_donkey_and_hell_is_still_a_place():
    """Both are ordinary words in the surroundings a child meets them in: an
    ass is a donkey in every fable ever written, and hell is in half of English
    literature. Only the insults are caught."""
    assert without_swearing("the ass carried the load") == "the ass carried the load"
    assert without_swearing("Dante wrote about hell") == "Dante wrote about hell"
    assert "hole" not in without_swearing("do not be an asshole")


def test_nothing_is_filtered_outside_child_mode():
    """`clean` is off by default, so an adult's own reading is left alone."""
    assert plan("Well shit.") == ["Well shit."]
