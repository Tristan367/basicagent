"""What `glob` matches, which was wrong in both directions.

`fnmatch` has no idea what `**` means -- it compiles to a regex wanting a
literal slash, so `**/*.py` missed every file at the top of the tree. The agent
globbed for a file it had written one turn earlier and concluded it was not
there. And `fnmatch`'s `*` crosses a directory separator, so `src/*.py` also
returned `src/deep/nested.py`, which is the opposite mistake.
"""

import asyncio

import pytest

from agent_server.tools.base import ToolContext
from agent_server.tools.search import glob_search


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "src" / "deep").mkdir(parents=True)
    for rel in ("root.py", "README.md", "src/mid.py", "src/deep/low.py", "src/notes.md"):
        (tmp_path / rel).write_text("x")
    return tmp_path


async def found(tree, pattern):
    ctx = ToolContext(session_id="g", project_dir=str(tree), abort=asyncio.Event())
    result = await glob_search(ctx, pattern=pattern)
    if "No files matching" in result.output:
        return []
    return sorted(result.output.splitlines())


@pytest.mark.parametrize("pattern, expected", [
    # `**` spans any number of directories INCLUDING NONE. This is the one that
    # made the agent believe a file it had just written did not exist.
    ("**/*.py", ["root.py", "src/deep/low.py", "src/mid.py"]),
    ("**/*.md", ["README.md", "src/notes.md"]),
    ("src/**/*.py", ["src/deep/low.py", "src/mid.py"]),
    # ...and `*` stops at a separator, so this must NOT reach into `deep/`.
    ("src/*.py", ["src/mid.py"]),
    ("src/deep/*.py", ["src/deep/low.py"]),
    # A bare pattern means "by name, at any depth", which is what a person means.
    ("*.py", ["root.py", "src/deep/low.py", "src/mid.py"]),
    ("low.py", ["src/deep/low.py"]),
    ("r*.py", ["root.py"]),
    # Braces still expand.
    ("*.{py,md}", ["README.md", "root.py", "src/deep/low.py", "src/mid.py", "src/notes.md"]),
])
async def test_patterns_match_what_a_person_would_expect(tree, pattern, expected):
    assert await found(tree, pattern) == sorted(expected)


async def test_a_pattern_matching_nothing_says_so_rather_than_erroring(tree):
    assert await found(tree, "*.rs") == []


async def test_a_character_class_still_works(tree):
    assert await found(tree, "[rR]*.py") == ["root.py"]


async def test_an_unbalanced_bracket_does_not_explode(tree):
    """A model writes a broken pattern eventually. Matching nothing is fine;
    raising is not."""
    assert await found(tree, "[unclosed*.py") == []
