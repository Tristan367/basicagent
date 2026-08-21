"""The install, and the things about it that quietly stop being true.

None of this is exercised by using the app, which is exactly why it rots: the
developer installed it once, a year ago, and never meets the path again. Every
user meets it exactly once, and it is the only part where failing means they
never see the app at all.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(name: str) -> str:
    return (ROOT / name).read_text()


# ── which Python this runs on ───────────────────────────────────────────────


def _installer_bounds() -> tuple[tuple, tuple]:
    source = _read("install.py")
    found = {}
    for key in ("MIN_PYTHON", "BELOW_PYTHON"):
        match = re.search(rf"^{key} = \((\d+), (\d+)\)$", source, re.M)
        assert match, f"{key} is no longer a plain literal in install.py"
        found[key] = (int(match.group(1)), int(match.group(2)))
    return found["MIN_PYTHON"], found["BELOW_PYTHON"]


def test_the_installer_and_the_package_agree_on_which_python_works():
    """kokoro-onnx -- which is read-aloud -- publishes no wheel above the
    ceiling, and pip says so in forty lines of resolver output ending in an
    error. Two copies of that number is one copy that goes stale, and the
    symptom is an install that dies on a computer bought this year."""
    low, below = _installer_bounds()
    declared = re.search(
        r'requires-python = ">=(\d+\.\d+),<(\d+\.\d+)"', _read("pyproject.toml")
    )
    assert declared, "pyproject no longer declares a Python range"
    assert declared.group(1) == f"{low[0]}.{low[1]}"
    assert declared.group(2) == f"{below[0]}.{below[1]}"


def test_the_readme_names_the_versions_that_actually_work():
    """The first instruction anybody follows. Naming a version the install then
    refuses is the worst possible first minute."""
    low, below = _installer_bounds()
    readme = _read("README.md")
    for minor in range(low[1], below[1]):
        assert f"{low[0]}.{minor}" in readme, f"{low[0]}.{minor} is supported but unmentioned"
    assert f"{below[0]}.{below[1]}" not in readme, "the README names an unsupported version"


def test_a_python_that_is_too_new_is_repaired_rather_than_reported():
    """The interpreter somebody types is whatever their computer came with.
    "Your Python is too new, go and install an old one" is where a
    non-technical person stops."""
    source = _read("install.py")
    block = source[source.index("def check_python("):source.index("def check_venv_module(")]
    assert "FALLBACK_PYTHONS" in block, "it does not look for another interpreter"
    assert "os.execv" in block, "it never actually starts again with one"
    assert "uv" in block, "the no-admin-rights route is not offered"


# ── what the installer promises ─────────────────────────────────────────────


def test_the_install_ends_with_something_to_click():
    """The person this app is for cannot open a terminal, so an install that
    ends with "now type this" has not finished."""
    source = _read("install.py")
    for platform_fn in ("_shortcut_linux", "_shortcut_mac", "_shortcut_windows"):
        assert f"def {platform_fn}(" in source, platform_fn
    assert "basicagent.py" in source, "the shortcut does not launch anything"
    assert (ROOT / "basicagent.py").is_file()


def test_nothing_fatal_hangs_off_an_optional_download():
    """A user whose read-aloud download failed still has an app. Letting the
    installer die there would leave them with a half-built environment and no
    idea which half."""
    source = _read("install.py")
    block = source[source.index("def install_speech("):source.index("# ── the icon")]
    assert "check=False" in block, "a failed speech download aborts the install"


# ── the settings a developer is told about ──────────────────────────────────


def test_every_setting_the_example_env_names_is_real():
    """A variable in `.env.example` that nothing reads is worse than no
    documentation: somebody sets it, nothing happens, and there is no way to
    tell that from a bug. Two had already stopped existing."""
    text = _read(".env.example")
    named = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]{3,})=", text, re.M))
    assert named, "the example file no longer names anything"

    haystack = "\n".join(
        p.read_text() for p in [
            *(ROOT / "agent_server").rglob("*.py"),
            ROOT / "basicagent.py", ROOT / "run.sh",
        ]
    )
    for name in sorted(named):
        assert name in haystack, f"{name} is documented but nothing reads it"


# ── the one thing that must not be casually switched on ─────────────────────


def test_every_launcher_says_something_when_it_is_not_on_loopback():
    """There is no login on this app and there is not going to be one: it runs
    shell commands as whoever started it, so reaching it over a network is
    reaching a terminal on their computer. Someone will still do it, to use the
    app from a tablet, which is a fair thing to want."""
    for launcher in ("basicagent.py", "run.sh"):
        source = _read(launcher)
        assert "127.0.0.1" in source, f"{launcher} does not default to loopback"
        assert "WARNING" in source, f"{launcher} exposes it silently"
