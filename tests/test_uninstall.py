"""Getting it off the computer again.

An app somebody cannot remove is one they are right to be wary of installing,
and "just delete the folder" is the wrong answer twice over: it is not
something the person this is built for can do, and it leaves a desktop icon
pointing at nothing, an entry in the Start menu, and about a gigabyte in cache
directories they will never find.

The thing this file is really guarding is the other half: that an uninstall
never quietly takes somebody's work with it. The projects, the settings and the
API keys live somewhere else on purpose, and nothing removes them without being
asked in as many words.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _uninstaller():
    spec = importlib.util.spec_from_file_location(
        "_uninstaller", ROOT / "uninstall.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_there_is_one_at_all():
    assert (ROOT / "uninstall.py").is_file()


def test_it_is_standard_library_only():
    """It has to run when the environment it is about to remove is already
    broken, which is one of the likelier reasons somebody wants it gone."""
    source = (ROOT / "uninstall.py").read_text()
    for line in source.splitlines():
        if line.startswith(("import ", "from ")) and "agent_server" in line:
            raise AssertionError(f"uninstall.py imports the app: {line}")


def test_the_work_is_never_removed_without_being_asked(tmp_path, monkeypatch):
    """The one thing that must not go wrong. Somebody removing the program
    because it annoyed them has not agreed to lose six weeks of a child's
    projects, and they cannot get them back."""
    uninstaller = _uninstaller()
    monkeypatch.setenv("BASICAGENT_DATA_DIR", str(tmp_path / "work"))
    data = uninstaller.data_dir()
    data.mkdir(parents=True)
    (data / "agent.db").write_text("a term's worth of work")

    # The default answer to the question is no, and --keep-data never asks.
    source = (ROOT / "uninstall.py").read_text()
    at = source.index("Also delete your projects")
    assert "default_yes=False" in source[at:at + 200], (
        "deleting somebody's work is the default answer")
    assert "--keep-data" in source

    # And the program's own folder is never the data folder.
    assert uninstaller.data_dir() != uninstaller.ROOT


def test_it_knows_everywhere_the_installer_put_something(monkeypatch):
    """A shortcut left behind points at nothing and is the last thing somebody
    sees of an app they asked to remove."""
    uninstaller = _uninstaller()
    installer = (ROOT / "install.py").read_text()

    for windows, mac in ((True, False), (False, True), (False, False)):
        monkeypatch.setattr(uninstaller, "IS_WIN", windows)
        monkeypatch.setattr(uninstaller, "IS_MAC", mac)
        names = {p.name for p in uninstaller.shortcuts()}
        assert names, (windows, mac)
        for name in names:
            assert name in installer or name.split(".")[0] in installer, name


def test_windows_lists_it_where_people_look_for_it():
    """Settings -> Apps. Somebody who wants this gone goes there first, and if
    it is not listed concludes it cannot be removed."""
    installer = (ROOT / "install.py").read_text()
    uninstaller = (ROOT / "uninstall.py").read_text()
    assert "CurrentVersion" in installer and "Uninstall" in installer
    assert "UninstallString" in installer
    assert "DisplayName" in installer
    # Under HKCU, so it needs no administrator and belongs to this user only.
    assert "HKCU" in installer
    # And the uninstaller takes the entry back out again.
    assert "reg" in uninstaller and "delete" in uninstaller


def test_it_can_delete_the_folder_it_is_running_from():
    """Windows will not remove a directory a running process has open, and
    this process has this one open. Without the handover the last step is the
    one that silently does nothing."""
    source = (ROOT / "uninstall.py").read_text()
    at = source.index("def remove_the_program_itself")
    body = source[at:source.index("\ndef ", at + 10)]
    assert "rmdir" in body and "DETACHED_PROCESS" in body


def test_the_installer_tells_people_it_exists():
    """An uninstaller nobody is told about is not one."""
    installer = (ROOT / "install.py").read_text()
    assert "register_uninstall" in installer
    assert "If you ever want it gone" in installer


def test_an_update_relists_it_too():
    """An update swaps the program's files and never re-runs the install, so
    the uninstaller arrives in the folder while the entry that makes it
    findable does not -- and a folder is not somewhere anybody was ever asked
    to look. Both update routes have to do it, not just the one somebody
    happened to test."""
    import inspect

    from agent_server import updates

    assert "--register-only" in (ROOT / "install.py").read_text()
    source = inspect.getsource(updates)
    assert "_relist_uninstall" in source
    for route in (updates._pull, updates._download_and_swap):
        assert "_relist_uninstall" in inspect.getsource(route), route.__name__
    # And it never fails an update that has already worked.
    assert "contextlib.suppress" in inspect.getsource(updates._relist_uninstall)
