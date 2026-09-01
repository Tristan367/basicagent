"""The installer, run for real rather than read.

Everything here is about the two ways an install quietly ruins itself weeks
later: it was left in the folder it was unzipped into, and the environment it
built points at a path that no longer exists.
"""

from __future__ import annotations

import importlib.util
import re
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
    huge = installer._pip_fraction(["x"] * 5000, [], [99999.0], ["a", "b"])
    assert huge <= 0.97
    assert installer._pip_fraction([], [], [0.0], []) == 0.0


def test_a_second_install_still_looks_like_something_happening():
    """Running it again over an existing copy downloads nothing and unpacks
    nothing, so every line the progress relies on goes quiet and the bar sat at
    nought and then jumped to done. Reported by the first person to reinstall.
    Pip does say "Requirement already satisfied" for each one, so that counts.
    """
    installer = _installer()
    line = "Requirement already satisfied: numpy in ./.venv/lib (2.5.2)"
    found = installer.PIP_HAVE_IT.match(line)
    assert found and found.group(1) == "numpy"
    moving = installer._pip_fraction([], ["a"] * 30, [0.0], [])
    assert 0.2 < moving < 0.8, moving


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


# ── being able to see it working ───────────────────────────────────────────
#
# Reported from a real install: the bar sat at 42% for minutes with the words
# "putting 65 pieces in place" under it and no way to tell a slow step from a
# hung one. Both causes turned out to be things pip does when nobody is
# looking, and both are worked around rather than lived with.


def test_pip_is_asked_for_progress_it_would_otherwise_withhold():
    """Pip draws no bar when its output is read by a program rather than shown
    to a person -- which is exactly this case -- so a 200 MB download is one
    line and then several silent minutes. `--progress-bar raw` gets plain byte
    counts instead, which are no use to a human and ideal here."""
    installer = _installer()
    source = (ROOT / "install.py").read_text()
    assert '"--progress-bar", "raw"' in source
    found = installer.PIP_BYTES.match("Progress 262144 of 23136817")
    assert found and found.group(1) == "262144" and found.group(2) == "23136817"


def test_the_silent_part_of_pip_is_watched_rather_than_guessed():
    """Pip says nothing at all while it unpacks what it downloaded, and on
    Windows that is the slow half -- an antivirus reads every file as it
    lands. So the packages appearing in the environment get counted."""
    installer = _installer()
    assert callable(installer.site_packages)
    assert hasattr(installer.screen, "watch")


def test_the_step_shows_how_long_it_has_been_going(monkeypatch):
    """Four minutes and forty seconds look identical when neither is shown,
    and one of them is the moment somebody decides it has hung."""
    installer = _installer()
    monkeypatch.setattr(installer, "ANSI", True)
    painted = []
    try:
        installer.screen.start("Installing the pieces", 40)
        installer.screen.progress(0.4, "numpy")
        monkeypatch.setattr(installer.sys.stdout, "write", painted.append)
        installer.screen._paint()
    finally:
        # start() runs a thread that repaints until told to stop. Left going,
        # it writes over whatever test happens to be running next.
        installer.screen.close()
    shown = "".join(painted)
    assert "Installing the pieces" in shown
    assert "numpy" in shown
    assert re.search(r"\d+s", shown), shown


def test_the_tick_is_not_a_character_windows_cannot_draw():
    """A console that encodes U+2713 perfectly well and then draws an empty
    box, because Windows' own console font has no dingbats in it. The one
    character meaning "this worked" was the one that looked like breakage."""
    source = (ROOT / "install.py").read_text()
    assert 'if BLOCKS and not IS_WIN else "OK"' in source


def test_what_is_being_installed_is_named_in_words(tmp_path):
    """An educational app that says "installing the app's parts" for four
    minutes has taught nobody anything. The big ones say what they are for."""
    installer = _installer()
    for package in ("faster-whisper", "onnxruntime", "kokoro-onnx", "playwright",
                    "numpy", "openai"):
        assert package in installer.PURPOSE, package
        assert installer._describe(package) != package
    # And an unknown package is still named rather than hidden.
    assert installer._describe("something-new") == "something-new"


def test_the_whole_plan_is_shown_before_any_of_it_starts(capsys):
    """Seven named steps make a slow fourth step legible as the fourth of
    seven. One step at a time makes it the end of the world."""
    installer = _installer()
    installer.screen.plan(["Checking this computer can run it",
                           "Installing the pieces"], [1, 40])
    shown = capsys.readouterr().out
    assert "1. checking this computer can run it" in shown
    assert "2. installing the pieces" in shown


def test_the_installer_fetches_every_model_the_app_will_load():
    """Not just the one in the settings.

    Live dictation shows words as you speak by re-transcribing about once a
    second, and the chosen model is usually too slow for that -- so a small
    fast one does that job. Installing only the chosen one left the app
    fetching the other from Hugging Face at first launch: a silent 145 MB,
    from the one host that rate-limits anonymous downloads, at the moment
    somebody first presses the microphone button. Which is the exact failure
    the mirror was built to prevent, moved from install time to first use.
    """
    from agent_server.downloads import WHISPER_SIZES, dictation_sizes_needed

    needed = dictation_sizes_needed("small.en")
    assert "base.en" in needed, needed
    for size in needed:
        assert size in WHISPER_SIZES, size
    # A choice that is already fast enough needs nothing extra.
    assert dictation_sizes_needed("tiny.en") == ["tiny.en"]
