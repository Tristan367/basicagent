"""How the assistant talks about a file.

There is one rule and it is the whole section: write the path. It renders as a
link the user can press, which opens the file in their own file manager, so a
path is how you hand somebody a file rather than a chore you hand them.

What this replaced was a section headed "The user cannot get at their own
files" that told the assistant to treat the folder as invisible and NEVER name
a path -- which meant the link existed and could never appear, and a child who
wanted to redraw a sprite in the paint program they already know had no route
at all.
"""

from pathlib import Path

AGENT = Path("system_prompts/agent.md").read_text()
MARKDOWN = Path("web_ui/static/js/markdown.js").read_text()
APP_JS = Path("web_ui/static/js/app.js").read_text()


def _flat(text: str) -> str:
    return " ".join(text.split())


def _section() -> str:
    start = AGENT.index("# Talking about files")
    return AGENT[start:AGENT.index("## Things they attached")]


def test_the_rule_is_to_write_the_path():
    said = _flat(_section()).lower()
    assert "whenever you mention a file, write its path" in said
    assert "shorter" in said, "it does not say which of the two to pick"


def test_it_says_what_the_path_becomes():
    """Without this the assistant has no reason to think a path is useful to
    somebody with no file manager open, and will keep avoiding it."""
    said = _flat(_section()).lower()
    assert "link they can press" in said
    assert "file manager" in said


def test_it_no_longer_forbids_naming_a_file():
    said = _flat(_section()).lower()
    for gone in ("treat the folder as invisible",
                 "never say \"open such-and-such",
                 "the only surface they have"):
        assert gone not in said, f"still says {gone!r}"


def test_it_still_rules_out_sending_them_hunting():
    """The half of the old rule that was right: the link does the navigating,
    so describing a route through folders is never the answer."""
    said = _flat(_section()).lower()
    assert "never describe a route through folders" in said
    assert "never tell them to go looking" in said


def test_the_inline_window_and_pictures_survived_the_rewrite():
    said = _flat(_section())
    assert "src/app.js:12-30" in said
    assert "syntax-highlighted" in said
    assert "shows the picture" in said


# ── the browser half, which has to agree ───────────────────────────────────


def test_the_browser_turns_a_path_into_something_pressable():
    assert "fileRefReplacer" in MARKDOWN
    assert "file-ref" in MARKDOWN or "file-ref" in APP_JS


def test_pressing_one_opens_the_users_own_file_manager():
    assert "revealFile" in APP_JS
    assert "/api/files/reveal" in APP_JS


def test_the_route_behind_it_exists_and_is_not_confined():
    """It opens the user's own file manager on their own computer. Confining it
    would be this app deciding which of their files they may look at."""
    import inspect

    from agent_server.routes import files

    source = inspect.getsource(files.reveal)
    assert "confine=False" in source
