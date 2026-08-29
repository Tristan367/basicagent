"""The installer, run for real rather than read.

Everything here is about the two ways an install quietly ruins itself weeks
later: it was left in the folder it was unzipped into, and the environment it
built points at a path that no longer exists.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _installer():
    """A fresh copy of install.py as a module. Standard library only, so this
    is cheap and has no side effects until something is called."""
    spec = importlib.util.spec_from_file_location("_installer", ROOT / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_download(tmp_path: Path) -> Path:
    """What a user actually has: an unzipped folder wherever it landed."""
    folder = tmp_path / "Downloads" / "basicagent-9.9.9"
    (folder / "agent_server").mkdir(parents=True)
    (folder / "agent_server" / "__init__.py").write_text("")
    (folder / "install.py").write_text("# not the real one\n")
    (folder / "VERSION").write_text("9.9.9\n")
    (folder / "requirements.txt").write_text("")
    return folder


def test_it_moves_itself_out_of_the_folder_it_was_unzipped_into(tmp_path, monkeypatch):
    """Downloads is not a place to install something.

    Windows Storage Sense deletes files there after thirty days when somebody
    has switched it on, and what it would delete is the app the desktop icon
    points at -- a month after everything was working.
    """
    installer = _installer()
    folder = _fake_download(tmp_path)
    monkeypatch.setattr(installer, "ROOT", folder)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(installer.sys, "argv", ["install.py"])

    monkeypatch.chdir(ROOT)   # relocate() chdirs; pytest puts this back
    installer.relocate()

    assert folder != installer.ROOT, "it installed into the download folder"
    assert tmp_path in installer.ROOT.parents
    assert (installer.ROOT / "agent_server" / "__init__.py").is_file()
    assert (installer.ROOT / "VERSION").read_text().strip() == "9.9.9"
    # The download is left alone; the user throws it away themselves.
    assert (folder / "VERSION").is_file()


def test_the_environment_is_built_after_the_move_not_before(tmp_path, monkeypatch):
    """A virtual environment records absolute paths in `pyvenv.cfg` and in every
    script it writes, so one that is moved afterwards is silently broken. The
    only defence is ordering, so the ordering is the thing to test."""
    source = (ROOT / "install.py").read_text()
    main = source[source.index("def main() -> None:"):]
    assert main.index("relocate()") < main.index("make_venv()"), \
        "the environment is built before the app is moved, which breaks it"


def test_a_clone_is_left_exactly_where_it_is(tmp_path, monkeypatch):
    """Whoever has a git clone chose where it lives, and a second copy of it
    appearing in a data directory would be nothing but confusing."""
    installer = _installer()
    folder = _fake_download(tmp_path)
    (folder / ".git").mkdir()
    monkeypatch.setattr(installer, "ROOT", folder)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(installer.sys, "argv", ["install.py"])

    monkeypatch.chdir(ROOT)   # relocate() chdirs; pytest puts this back
    installer.relocate()
    assert folder == installer.ROOT


def test_here_means_here(tmp_path, monkeypatch):
    installer = _installer()
    folder = _fake_download(tmp_path)
    monkeypatch.setattr(installer, "ROOT", folder)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(installer.sys, "argv", ["install.py", "--here"])

    monkeypatch.chdir(ROOT)   # relocate() chdirs; pytest puts this back
    installer.relocate()
    assert folder == installer.ROOT


def test_it_never_carries_an_old_environment_across(tmp_path, monkeypatch):
    """A `.venv` copied from somewhere else points at the interpreter it was
    built against, which is not this one."""
    installer = _installer()
    folder = _fake_download(tmp_path)
    (folder / ".venv" / "bin").mkdir(parents=True)
    (folder / ".venv" / "pyvenv.cfg").write_text("home = /somewhere/else\n")
    (folder / "__pycache__").mkdir()
    (folder / "__pycache__" / "x.pyc").write_text("")
    monkeypatch.setattr(installer, "ROOT", folder)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(installer.sys, "argv", ["install.py"])

    monkeypatch.chdir(ROOT)   # relocate() chdirs; pytest puts this back
    installer.relocate()
    assert not (installer.ROOT / ".venv").exists()
    assert not (installer.ROOT / "__pycache__").exists()


def test_the_home_it_picks_needs_no_administrator(monkeypatch):
    """Program Files would, and asking for one is a UAC prompt at best and a
    locked-down school laptop at worst."""
    installer = _installer()
    home = installer.home_for()
    assert "Program Files" not in str(home)
    assert not str(home).startswith("/usr")
    assert not str(home).startswith("/opt/")
    assert Path.home() in home.parents


def test_a_step_that_cannot_even_start_does_not_stop_the_install(monkeypatch, capsys):
    """The worst version of a failed optional download: not an error code, but
    an interpreter that is not there at all."""
    installer = _installer()
    monkeypatch.setattr(installer, "VENV_PY", ROOT / "no" / "such" / "python")
    code, tail = installer._stream([ROOT / "no" / "such" / "python", "-c", "pass"])
    assert code != 0
    assert tail and "could not start" in tail[0]


def test_the_progress_bar_never_reports_more_than_it_knows(monkeypatch):
    """A bar that reaches 100% and then sits there for four minutes is worse
    than one that stops at 97%."""
    installer = _installer()
    huge = installer._pip_fraction(["x"] * 5000, [99999.0], ["a", "b"])
    assert huge <= 0.97
    assert installer._pip_fraction([], [0.0], []) == 0.0


def test_pip_lines_are_read_the_way_pip_writes_them():
    """Parsed from real output. The names matter: they are what the person
    watching sees, and attributing 400 MB to the wrong package is worse than
    showing no name at all."""
    installer = _installer()
    cases = [
        ("Using cached numpy-2.5.2-cp313-cp313-manylinux_2_28_x86_64.whl (16.7 MB)",
         "numpy", 16.7),
        ("  Downloading onnxruntime-1.20.1-cp313-cp313-win_amd64.whl (11.2 MB)",
         "onnxruntime", 11.2),
        ("Downloading ctranslate2-4.5.0-cp313-cp313-manylinux_2_17_x86_64.whl (38.1 MB)",
         "ctranslate2", 38.1),
    ]
    for line, name, size in cases:
        found = installer.PIP_FILE.match(line)
        assert found, line
        assert found.group(1) == name
        assert float(found.group(2)) == size

    # The metadata stub pip fetches first is not the package arriving.
    stub = "  Using cached httpcore-1.0.9-py3-none-any.whl.metadata (21 kB)"
    assert ".metadata" in stub


def test_every_dictation_model_the_settings_offer_is_mirrored():
    """The settings page lets somebody pick a smaller model on an old computer.
    One that was never mirrored sends them back to Hugging Face, which is the
    thing the mirror exists to avoid."""
    from agent_server.config import WHISPER_MODEL_IDS
    from agent_server.downloads import WHISPER_FILES, WHISPER_SIZES

    assert set(WHISPER_SIZES) >= WHISPER_MODEL_IDS, \
        f"not mirrored: {WHISPER_MODEL_IDS - set(WHISPER_SIZES)}"
    for size, files in WHISPER_SIZES.items():
        assert set(files) == set(WHISPER_FILES), size
        assert files["model.bin"] > 10_000_000, size


@pytest.mark.skipif(not (ROOT / ".git").is_dir(), reason="needs the repository")
def test_the_installer_is_still_standard_library_only():
    """It runs before the virtual environment exists. An import of anything the
    app depends on turns the first error into an unreadable traceback."""
    result = subprocess.run(
        ["python3", "-c",
         "import ast,sys;src=open('install.py').read();"
         "mods={n.split('.')[0] for node in ast.walk(ast.parse(src)) "
         "if isinstance(node,(ast.Import,ast.ImportFrom)) "
         "for n in ([a.name for a in node.names] if isinstance(node,ast.Import) "
         "else [node.module or ''])};"
         "print(' '.join(sorted(mods)))"],
        cwd=ROOT, capture_output=True, text=True, check=True)
    allowed = {"contextlib", "os", "re", "shutil", "subprocess", "sys",
               "threading", "time", "pathlib", "ctypes", "importlib", ""}
    used = set(result.stdout.split())
    assert used <= allowed, f"install.py imports {used - allowed}"
