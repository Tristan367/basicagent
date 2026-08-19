"""Reading a slice of a file, and the boundary around it.

The path arrives in a chat message written by a model, so it is untrusted
input. Everything here is about the one rule that matters: a peek or a reveal
can only ever touch something inside that session's own project folder.
"""

import pytest
from fastapi import HTTPException

from agent_server.routes import files


@pytest.fixture
async def project(db, tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "app.js").write_text("\n".join(f"line {i}" for i in range(1, 31)))
    (root / "src" / "deep.py").write_text("a = 1\nb = 2\n")
    (root / "binary.bin").write_bytes(b"\x00\x01\x02" * 100)
    outside = tmp_path / "secret.txt"
    outside.write_text("do not read me")
    session = await db.create_session(name="Peek", project_dir=str(root))
    return {"id": session["id"], "root": root, "outside": outside}


async def test_reads_the_requested_line_range(project):
    out = await files.peek(project["id"], "app.js", 3, 7)
    assert out["start"] == 3
    assert out["end"] == 7
    assert out["text"].splitlines() == [f"line {i}" for i in range(3, 8)]
    assert out["total"] == 30
    assert out["lang"] == "javascript"


async def test_a_nested_path_resolves_against_the_project(project):
    out = await files.peek(project["id"], "src/deep.py", 1, 2)
    assert out["lang"] == "python"
    assert out["text"] == "a = 1\nb = 2"


async def test_an_absolute_path_inside_the_project_is_allowed(project):
    out = await files.peek(project["id"], str(project["root"] / "app.js"), 1, 1)
    assert out["text"] == "line 1"


@pytest.mark.parametrize("escape", [
    "../secret.txt",
    "../../secret.txt",
    "src/../../secret.txt",
    "/etc/passwd",
])
async def test_paths_outside_the_project_are_refused(project, escape):
    with pytest.raises(HTTPException) as excinfo:
        await files.peek(project["id"], escape)
    assert excinfo.value.status_code in (403, 404)


async def test_a_symlink_pointing_out_of_the_project_is_refused(project):
    link = project["root"] / "escape.txt"
    try:
        link.symlink_to(project["outside"])
    except OSError:  # pragma: no cover - Windows without privileges
        pytest.skip("symlinks unavailable")
    # Resolution happens before the boundary check precisely so that following
    # the link cannot land outside and still pass.
    with pytest.raises(HTTPException) as excinfo:
        await files.peek(project["id"], "escape.txt")
    assert excinfo.value.status_code == 403


async def test_a_missing_file_is_a_clean_404(project):
    with pytest.raises(HTTPException) as excinfo:
        await files.peek(project["id"], "nope.js")
    assert excinfo.value.status_code == 404


async def test_a_binary_file_is_refused_rather_than_rendered(project):
    with pytest.raises(HTTPException) as excinfo:
        await files.peek(project["id"], "binary.bin")
    assert excinfo.value.status_code == 415


async def test_the_window_is_capped(project):
    """A peek is a window, not a way to pull a whole file into the chat."""
    out = await files.peek(project["id"], "app.js", 1, 10_000)
    assert out["end"] - out["start"] + 1 <= files.MAX_PEEK_LINES


async def test_a_start_past_the_end_of_the_file_says_so(project):
    with pytest.raises(HTTPException) as excinfo:
        await files.peek(project["id"], "app.js", 500)
    assert excinfo.value.status_code == 416
    assert "30" in excinfo.value.detail


async def test_a_zero_or_negative_start_is_clamped(project):
    out = await files.peek(project["id"], "app.js", 0, 2)
    assert out["start"] == 1


async def test_omitting_the_end_reads_to_the_end_of_the_file(project):
    out = await files.peek(project["id"], "app.js", 28, 0)
    assert out["end"] == 30


async def test_an_unknown_session_is_refused(project):
    with pytest.raises(HTTPException) as excinfo:
        await files.peek("no-such-session", "app.js")
    assert excinfo.value.status_code == 404


async def test_reveal_refuses_a_path_outside_the_project(project):
    with pytest.raises(HTTPException) as excinfo:
        await files.reveal({"session_id": project["id"], "path": "../secret.txt"})
    assert excinfo.value.status_code == 403


async def test_reveal_requires_both_arguments(project):
    with pytest.raises(HTTPException):
        await files.reveal({"session_id": project["id"]})
    with pytest.raises(HTTPException):
        await files.reveal({"path": "app.js"})


def test_the_reveal_command_selects_the_file_on_every_platform(monkeypatch, tmp_path):
    """Each platform gets its "select this item" form rather than "open it" --
    the user wants somewhere to look, not an editor guessing at their code."""
    target = tmp_path / "app.js"
    for platform, expected in (
        ("darwin", "-R"),
        ("win32", "/select,"),
        ("linux", "ShowItems"),
    ):
        monkeypatch.setattr(files.sys, "platform", platform)
        assert any(expected in part for part in files._reveal_command(target))
