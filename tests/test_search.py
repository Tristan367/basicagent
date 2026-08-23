"""Searching a project, with and without ripgrep on the machine.

Nothing installs ripgrep -- not the installer, not requirements.txt -- so
whether this tool shells out or searches in Python is decided by what happens
to be on the computer. That makes the interesting test a differential one: the
same searches through both engines, and the answers have to match. A tool that
behaves differently depending on what is installed is worse than a slow one.
"""

from __future__ import annotations

import asyncio
import shutil

import pytest

from agent_server.tools.base import ToolContext
from agent_server.tools.search import grep_search

HAS_RG = shutil.which("rg") is not None
needs_rg = pytest.mark.skipif(not HAS_RG, reason="ripgrep is not installed here")


@pytest.fixture
def tree(tmp_path):
    """A small project with the awkward bits a real one has."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "import os\n"
        "def widget():\n"
        "    return 'Widget'\n"
        "# TODO: tidy this\n"
    )
    (tmp_path / "src" / "app.js").write_text(
        "function widget() { return 'widget'; }\n"
        "// TODO: and this\n"
    )
    (tmp_path / "README.md").write_text("A Widget factory.\n")

    # Everything below is something the search has to leave alone.
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("widget widget widget\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "COMMIT_EDITMSG").write_text("widget\n")
    # A real PNG: the header, then NUL bytes everywhere, which is how both
    # engines know not to read it. Written with them here because a "PNG" made
    # only of printable bytes is a text file, and ripgrep rightly searches it --
    # the differential test below caught exactly that and was right to.
    (tmp_path / "logo.png").write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00" + b"widget\x00" * 10)
    (tmp_path / "data.bin").write_bytes(b"widget\x00widget")
    return tmp_path


def ctx_for(path):
    return ToolContext(session_id="s", project_dir=str(path), abort=asyncio.Event())


async def run(tree, monkeypatch, *, engine: str, **kwargs):
    """Run one search through a named engine."""
    if engine == "python":
        monkeypatch.setattr(shutil, "which", lambda name: None)
    result = await grep_search(ctx_for(tree), **kwargs)
    return result


def hits(result) -> set[str]:
    """The matches, as `file:line`, with the tmp prefix taken off."""
    out = set()
    for line in result.output.splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[1].isdigit():
            out.add(parts[0].rsplit("/", 1)[-1] + ":" + parts[1])
    return out


# ── the two engines have to agree ───────────────────────────────────────────

CASES = [
    {"pattern": "widget"},                       # smart case: matches Widget too
    {"pattern": "Widget"},                       # capital: case-sensitive
    {"pattern": "TODO"},
    {"pattern": "widget", "include": "*.py"},
    {"pattern": "widget", "include": "*.{py,js}"},
    {"pattern": "def .*:"},                      # a real regex
    {"pattern": "nothing-is-here"},
]


@needs_rg
@pytest.mark.parametrize("case", CASES, ids=lambda c: str(c))
async def test_both_engines_give_the_same_answer(tree, monkeypatch, case):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(shutil, "which", lambda name: None)
        in_python = await grep_search(ctx_for(tree), **case)
    with_rg = await grep_search(ctx_for(tree), **case)
    assert hits(in_python) == hits(with_rg), (
        f"{case}\npython: {in_python.output}\nrg: {with_rg.output}")


# ── and each has to be right on its own ─────────────────────────────────────


@pytest.mark.parametrize("engine", ["python", "rg"])
async def test_it_finds_what_is_there(tree, monkeypatch, engine):
    if engine == "rg" and not HAS_RG:
        pytest.skip("no ripgrep")
    got = hits(await run(tree, monkeypatch, engine=engine, pattern="widget"))
    assert {"app.py:3", "app.js:1", "README.md:1"} <= got


@pytest.mark.parametrize("engine", ["python", "rg"])
async def test_it_leaves_the_noise_alone(tree, monkeypatch, engine):
    """node_modules and .git are most of the bytes in a real project and none
    of the answer. A search that includes them buries the four real hits."""
    if engine == "rg" and not HAS_RG:
        pytest.skip("no ripgrep")
    result = await run(tree, monkeypatch, engine=engine, pattern="widget")
    assert "node_modules" not in result.output
    assert ".git" not in result.output


@pytest.mark.parametrize("engine", ["python", "rg"])
async def test_it_does_not_read_pictures_at_you(tree, monkeypatch, engine):
    """A PNG with the word in its bytes is not a match anybody wants, and the
    binary it prints wrecks the rest of the output."""
    if engine == "rg" and not HAS_RG:
        pytest.skip("no ripgrep")
    result = await run(tree, monkeypatch, engine=engine, pattern="widget")
    assert "logo.png" not in result.output
    assert "data.bin" not in result.output


@pytest.mark.parametrize("engine", ["python", "rg"])
async def test_a_capital_letter_makes_it_case_sensitive(tree, monkeypatch, engine):
    """Ripgrep's smart-case, which the model writes patterns expecting."""
    if engine == "rg" and not HAS_RG:
        pytest.skip("no ripgrep")
    got = hits(await run(tree, monkeypatch, engine=engine, pattern="Widget"))
    assert "app.py:3" in got
    assert "app.js:1" not in got, "lowercase 'widget' should not have matched"


@pytest.mark.parametrize("engine", ["python", "rg"])
async def test_nothing_found_says_so_rather_than_looking_broken(
        tree, monkeypatch, engine):
    if engine == "rg" and not HAS_RG:
        pytest.skip("no ripgrep")
    result = await run(tree, monkeypatch, engine=engine, pattern="zzz-not-here")
    assert not result.is_error
    assert "No matches" in result.output


# ── the fallback specifically ───────────────────────────────────────────────


async def test_without_ripgrep_it_still_works(tree, monkeypatch):
    """It used to fail outright here, with `pacman -S ripgrep` -- an Arch-only
    command -- and a suggestion to use `grep -rn`, which Windows also lacks.
    Meanwhile the prompt tells the model to prefer this tool over shell grep."""
    monkeypatch.setattr(shutil, "which", lambda name: None)
    result = await grep_search(ctx_for(tree), pattern="widget")
    assert not result.is_error
    assert "app.py" in result.output
    assert "pacman" not in result.output
    assert "not installed" not in result.output


async def test_a_pattern_that_will_not_compile_is_a_clean_error(tree, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    result = await grep_search(ctx_for(tree), pattern="widget(")
    assert result.is_error
    assert "invalid pattern" in result.output


async def test_an_unreadable_file_is_skipped_not_fatal(tree, monkeypatch):
    """One file with the wrong permissions must not lose the other matches."""
    monkeypatch.setattr(shutil, "which", lambda name: None)
    locked = tree / "src" / "locked.py"
    locked.write_text("widget\n")
    locked.chmod(0o000)
    try:
        result = await grep_search(ctx_for(tree), pattern="widget")
        assert not result.is_error
        assert "app.py" in result.output
    finally:
        locked.chmod(0o644)


async def test_a_broken_symlink_is_skipped(tree, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    (tree / "src" / "dangling.py").symlink_to(tree / "src" / "gone.py")
    result = await grep_search(ctx_for(tree), pattern="widget")
    assert not result.is_error
    assert "app.py" in result.output


async def test_one_enormous_file_cannot_fill_the_whole_answer(tmp_path, monkeypatch):
    """A minified bundle matching on every line would otherwise be the entire
    result, and the file the model wanted would be off the end."""
    monkeypatch.setattr(shutil, "which", lambda name: None)
    (tmp_path / "bundle.js").write_text("widget\n" * 5000)
    (tmp_path / "real.js").write_text("widget\n")
    result = await grep_search(ctx_for(tmp_path), pattern="widget")
    from agent_server.tools.search import MAX_PER_FILE

    per_file = [ln for ln in result.output.splitlines() if "bundle.js" in ln]
    assert len(per_file) <= MAX_PER_FILE
    assert "real.js" in result.output, "the small file was pushed off the end"


async def test_the_search_does_not_block_the_event_loop(tree, monkeypatch):
    """Walking a big tree in Python is slow enough to matter. On the loop it
    would freeze every other project's reply while it ran."""
    import inspect

    from agent_server.tools import search

    assert "asyncio.to_thread" in inspect.getsource(search.grep_search)
