"""Changing a file, and why it no longer asks for a fingerprint back.

`edit` used to anchor on a `[path#tag]` fingerprint plus a line range. `read`
printed the tag; every edit had to pass it back; every write rotated it.

That failed in the field in the worst possible way. A model read a stylesheet,
got tag `16a8`, and -- correctly, and exactly as the prompt encourages -- issued
eight edits in one batch, every one carrying the tag it had just been given. The
first succeeded and rotated the tag to `0713`. The other seven were rejected for
carrying "the wrong" tag, and told: "Do not construct or guess a tag." It had
guessed nothing. Seven calls' work thrown away, and the model went hunting for a
mistake in its own reasoning that was never there.

There were no tests here at all, which is how that shipped. These are the
properties the replacement has to keep: an edit lands where its text is, several
edits in one batch do not interfere, a miss changes nothing and says why, and
nothing can be edited that was never actually shown.
"""

import asyncio
from pathlib import Path

import pytest

from agent_server.tools.base import ToolContext
from agent_server.tools.file_ops import clear_read_cache, edit_file, read_file

CSS = """\
.footer {
    background: #090705;
    color: #6b5e4a;
}

.form-note {
    color: #6b5e4a;
    text-align: center;
}

.booking-form {
    background: #1c1812;
    padding: 2rem;
}
"""


@pytest.fixture
def ctx(tmp_path):
    clear_read_cache()
    yield ToolContext(session_id="s", project_dir=str(tmp_path), abort=asyncio.Event())
    clear_read_cache()


@pytest.fixture
def styles(tmp_path):
    path = tmp_path / "styles.css"
    path.write_text(CSS)
    return path


# ── the failure that started this ──────────────────────────────────────────


async def test_a_batch_of_edits_to_one_file_all_land(ctx, styles):
    """The exact shape that lost seven calls. One read, then several edits in
    one go: each names its own place, so the first one landing cannot invalidate
    the others."""
    await read_file(ctx, filePath=str(styles))

    edits = [
        ("background: #090705;", "background: var(--bg-footer);"),
        ("color: #6b5e4a;\n    text-align: center;",
         "color: var(--text-faint);\n    text-align: center;"),
        ("background: #1c1812;", "background: var(--bg-card);"),
    ]
    results = await asyncio.gather(*[
        edit_file(ctx, filePath=str(styles), oldString=old, newString=new)
        for old, new in edits
    ])

    assert not any(r.is_error for r in results), [r.output for r in results if r.is_error]
    after = styles.read_text()
    assert "var(--bg-footer)" in after
    assert "var(--text-faint)" in after
    assert "var(--bg-card)" in after
    assert "#090705" not in after


async def test_the_old_arguments_get_an_answer_that_names_what_changed(ctx, styles):
    """A conversation that predates the change is full of calls in the old
    shape, and a transcript is the strongest few-shot prompt there is -- so the
    model will keep making them however clear the schema is. "oldString is
    required" is true and reads as though the call was merely malformed."""
    await read_file(ctx, filePath=str(styles))
    result = await edit_file(
        ctx, filePath=str(styles), tag="16a8", startLine=1, endLine=3, newText="x",
    )
    assert result.is_error
    said = result.output
    assert "no longer takes" in said
    assert "tag" in said and "startLine" in said
    assert "oldString" in said
    # And that the history is not a guide.
    assert "ignore them" in said
    assert styles.read_text() == CSS, "it wrote something anyway"


def test_read_no_longer_prints_a_tag():
    """The tag was only ever there to be passed back."""
    import inspect

    from agent_server.tools import file_ops

    source = inspect.getsource(file_ops.read_file)
    # The comment left behind says why it is gone; what matters is that nothing
    # is put in the output any more.
    body = "\n".join(ln for ln in source.splitlines() if not ln.lstrip().startswith("#"))
    assert "#tag" not in body
    assert not hasattr(file_ops, "file_tag"), "the model-facing tag is back"


def test_the_tool_no_longer_offers_the_old_arguments():
    from agent_server.tools.registry import TOOLS

    props = TOOLS["edit"].parameters["properties"]
    assert set(props) == {"filePath", "oldString", "newString", "replaceAll"}
    assert TOOLS["edit"].parameters["required"] == ["filePath", "oldString", "newString"]


# ── a miss changes nothing, and says which kind of miss ────────────────────


async def test_text_that_is_not_there_writes_nothing(ctx, styles):
    await read_file(ctx, filePath=str(styles))
    result = await edit_file(
        ctx, filePath=str(styles), oldString="color: #ffffff;", newString="x")
    assert result.is_error
    assert "not found" in result.output
    assert "indentation" in result.output, "it does not name the usual cause"
    assert styles.read_text() == CSS


async def test_a_file_changed_underneath_says_so_instead(ctx, styles):
    """Same symptom, different problem, different fix: re-read, rather than
    look harder at text that was never going to match."""
    await read_file(ctx, filePath=str(styles))
    styles.write_text(CSS.replace("#090705", "#000000"))

    result = await edit_file(
        ctx, filePath=str(styles), oldString="background: #090705;", newString="x")
    assert result.is_error
    assert "changed on disk" in result.output
    assert "Re-read" in result.output


async def test_text_that_appears_twice_is_refused_until_it_is_made_unique(ctx, styles):
    await read_file(ctx, filePath=str(styles))
    result = await edit_file(
        ctx, filePath=str(styles), oldString="color: #6b5e4a;", newString="x")
    assert result.is_error
    assert "2 occurrences" in result.output
    assert "replaceAll" in result.output
    assert styles.read_text() == CSS


async def test_replace_all_takes_every_one(ctx, styles):
    await read_file(ctx, filePath=str(styles))
    result = await edit_file(
        ctx, filePath=str(styles), oldString="color: #6b5e4a;",
        newString="color: var(--faint);", replaceAll=True)
    assert not result.is_error
    assert "#6b5e4a" not in styles.read_text()


# ── you can only change what you were shown ────────────────────────────────


async def test_a_file_never_read_cannot_be_edited(ctx, styles):
    result = await edit_file(
        ctx, filePath=str(styles), oldString="background: #090705;", newString="x")
    assert result.is_error
    assert "not read" in result.output
    assert styles.read_text() == CSS


async def test_lines_never_shown_cannot_be_edited(ctx, tmp_path):
    """The one guarantee the tag scheme had that plain matching does not:
    matching text proves where an edit lands, not that anyone looked at it.
    Reading the first 20 lines and then replacing something at line 300 is
    still editing blind."""
    big = tmp_path / "big.py"
    big.write_text("\n".join(f"line {i}" for i in range(1, 401)) + "\n")
    await read_file(ctx, filePath=str(big), limit=20)

    result = await edit_file(ctx, filePath=str(big), oldString="line 300", newString="x")
    assert result.is_error
    assert "never shown" in result.output
    assert "offset=300" in result.output, "it does not say how to see them"
    assert "line 300" in big.read_text()


async def test_a_second_window_adds_to_what_has_been_seen(ctx, tmp_path):
    """A file read in two parts is one file, and editing across both is fine."""
    big = tmp_path / "big.py"
    big.write_text("\n".join(f"line {i}" for i in range(1, 401)) + "\n")
    await read_file(ctx, filePath=str(big), limit=20)
    await read_file(ctx, filePath=str(big), offset=290, limit=20)

    result = await edit_file(ctx, filePath=str(big), oldString="line 300", newString="changed")
    assert not result.is_error, result.output
    assert "changed" in big.read_text()


# ── what comes back ────────────────────────────────────────────────────────


async def test_an_edit_shows_where_it_landed(ctx, styles):
    """The diff a tool returns is display-only and never reaches the model, so
    an edit in the wrong place used to be invisible until the next read -- which
    is exactly when it is most expensive to find."""
    await read_file(ctx, filePath=str(styles))
    result = await edit_file(
        ctx, filePath=str(styles), oldString="background: #090705;",
        newString="background: var(--bg-footer);")
    assert not result.is_error
    assert "The file now reads:" in result.output
    assert "var(--bg-footer)" in result.output
    # Numbered, and post-edit, so nobody has to do the arithmetic.
    assert "2: " in result.output


async def test_edits_can_follow_each_other_without_rereading(ctx, styles):
    """The echoed region counts as seen, and the lines below an edit are
    carried across the shift."""
    await read_file(ctx, filePath=str(styles))
    first = await edit_file(
        ctx, filePath=str(styles), oldString="background: #090705;",
        newString="background: var(--a);\n    outline: none;")
    assert not first.is_error
    second = await edit_file(
        ctx, filePath=str(styles), oldString="background: #1c1812;",
        newString="background: var(--b);")
    assert not second.is_error, second.output
    assert "var(--b)" in styles.read_text()


async def test_an_edit_that_says_nothing_new_is_refused(ctx, styles):
    await read_file(ctx, filePath=str(styles))
    result = await edit_file(
        ctx, filePath=str(styles), oldString="color: #6b5e4a;", newString="color: #6b5e4a;")
    assert result.is_error
    assert "identical" in result.output


def test_there_is_one_kind_of_subagent():
    """`explore` was `task` with a different name, the same handler and the same
    two parameters -- a second entry in every schema, for nothing. A subagent
    that should leave things alone is told so in its prompt."""
    from agent_server.tools.registry import TOOLS

    assert "explore" not in TOOLS
    assert "task" in TOOLS
    for path in ("system_prompts/agent.md", "agent_server/activity.py"):
        assert "explore" not in Path(path).read_text(), path


def test_read_does_not_document_a_mechanism_that_no_longer_exists():
    """It still said "pass that tag and the line numbers to `edit`" long after
    `edit` stopped taking either. Caught by asking the model what confused it,
    which is a better bug-finder than reading the file again."""
    from agent_server.tools.registry import TOOLS

    said = TOOLS["read"].description
    assert "tag" not in said
    assert "line numbers to `edit`" not in said
    # And it says what the numbers are actually for.
    assert "matches on text" in said
