"""Downloading a project, and serving an attachment back.

The user cannot reach their own project folder -- that is deliberate -- so the
download is their only route to their own work. It has to produce something
they can actually use: a real folder tree, with the git history, and without
the hundreds of megabytes of rebuildable cache that would make it undownloadable.
"""

import io
import zipfile

import pytest
from fastapi import HTTPException

from agent_server.routes import files


@pytest.fixture
async def project(db, tmp_path):
    root = tmp_path / "site"
    (root / "src").mkdir(parents=True)
    (root / "index.html").write_text("<h1>hello</h1>")
    (root / "src" / "app.js").write_text("console.log(1)")
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main")
    (root / "node_modules" / "dep").mkdir(parents=True)
    (root / "node_modules" / "dep" / "big.js").write_text("x" * 10_000)
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "c.pyc").write_bytes(b"\x00\x01")
    session = await db.create_session(name="My Site", project_dir=str(root))
    return session["id"]


async def _entries(session_id) -> list[str]:
    response = await files.export_project(session_id)
    body = b"".join([chunk async for chunk in response.body_iterator])
    return sorted(zipfile.ZipFile(io.BytesIO(body)).namelist())


async def test_the_project_files_are_all_there(project):
    names = await _entries(project)
    assert "index.html" in names
    assert "src/app.js" in names


async def test_the_git_history_comes_too(project):
    """It is what makes the download a project rather than a snapshot, and what
    lets someone pick the work up where it was left."""
    assert ".git/HEAD" in await _entries(project)


@pytest.mark.parametrize("junk", ["node_modules", "__pycache__"])
async def test_rebuildable_caches_are_left_out(project, junk):
    assert not [n for n in await _entries(project) if n.startswith(junk)]


async def test_the_file_is_named_after_the_project(project):
    response = await files.export_project(project)
    assert 'filename="My Site.zip"' in response.headers["Content-Disposition"]
    assert response.media_type == "application/zip"


@pytest.mark.parametrize("name,expected", [
    ("My Site", "My Site"),
    ("../../etc/passwd", "-..-etc-passwd"),
    ("weird/\\:*?<>|chars", "weird--------chars"),
    ("", "project"),
])
def test_the_filename_cannot_escape_or_break(name, expected):
    """It goes into a Content-Disposition header and then onto a filesystem."""
    assert files._safe_name(name) == expected


async def test_an_unknown_session_is_refused(db):
    with pytest.raises(HTTPException) as excinfo:
        await files.export_project("no-such-session")
    assert excinfo.value.status_code == 404


async def test_a_project_whose_folder_is_gone_says_so(db, tmp_path):
    session = await db.create_session(name="Ghost", project_dir=str(tmp_path / "nope"))
    with pytest.raises(HTTPException) as excinfo:
        await files.export_project(session["id"])
    assert excinfo.value.status_code == 404


async def test_an_attachment_outside_the_attachments_folder_is_refused(tmp_path):
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"\x89PNG")
    with pytest.raises(HTTPException) as excinfo:
        await files.attachment(str(outside))
    assert excinfo.value.status_code == 404


async def test_only_images_are_served_back(tmp_path, monkeypatch):
    from agent_server import config

    monkeypatch.setattr(config, "ATTACH_DIR", tmp_path)
    doc = tmp_path / "notes.txt"
    doc.write_text("private")
    with pytest.raises(HTTPException) as excinfo:
        await files.attachment(str(doc))
    # Attachments are only served to redraw a thumbnail. Anything else would
    # turn this into a way to read arbitrary uploaded files over HTTP.
    assert excinfo.value.status_code in (404, 415)
