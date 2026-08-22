"""Making games, with the same shape as making a website.

The web flow in this app works because nothing is asked of the user: they say
what they want, it gets built, and it appears in front of them. Godot can have
exactly that shape, and almost none of what is needed is code -- it is knowing
four things that are each a wasted afternoon to find out:

* **The editor is one file.** No install, no package manager. Download, unzip,
  run. Running a project needs nothing else at all -- `godot --path .` starts
  it, full Vulkan, no export step.

* **Export templates come as one 1.3 GB archive** and the documentation says
  you need the GUI to install them. Both are true and neither matters: the
  archive is a zip, GitHub serves byte ranges on release assets, so the web
  template can be pulled out of it in 10 MB. Every platform is separately
  reachable the same way -- Windows is 38 MB, and a child on Linux can hand a
  friend a `.exe`.

* **Web export needs cross-origin isolation headers**, unless threads are
  turned off in the preset, in which case it needs nothing and works on any
  plain static host. For a 2D game the difference is invisible, and "works when
  you send it to your mum" is worth far more than threads.

* **The export must not be written inside the project.** Godot re-imports its
  own build output on the next scan and the build gets steadily stranger.

Standard library only, and deliberately runnable on its own:

    python -m agent_server.godot install [web|linux|windows|mac ...]
    python -m agent_server.godot new <folder> ["Game name"]
    python -m agent_server.godot export <folder> web|linux|windows|mac
    python -m agent_server.godot check <folder>
    python -m agent_server.godot where
"""

from __future__ import annotations

import io
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

from agent_server.paths import data_dir

# Pinned, like the speech models: a release tag rather than `latest`, so an
# upstream change cannot alter what this version of the app installs.
VERSION = "4.7.2"
RELEASE = f"{VERSION}-stable"
BASE = f"https://github.com/godotengine/godot/releases/download/{RELEASE}"
TEMPLATES_ARCHIVE = f"{BASE}/Godot_v{RELEASE}_export_templates.tpz"

USER_AGENT = "BasicAgent-godot"

# The editor, per platform. The name inside the zip differs from the zip's own
# name, so it is found by pattern after unpacking rather than assumed.
EDITORS = {
    ("Linux", "x86_64"): f"Godot_v{RELEASE}_linux.x86_64.zip",
    ("Linux", "aarch64"): f"Godot_v{RELEASE}_linux.arm64.zip",
    ("Darwin", "x86_64"): f"Godot_v{RELEASE}_macos.universal.zip",
    ("Darwin", "arm64"): f"Godot_v{RELEASE}_macos.universal.zip",
    ("Windows", "AMD64"): f"Godot_v{RELEASE}_win64.exe.zip",
}

# Which files inside the templates archive each target needs. Release only:
# a debug template doubles the download to produce a build nobody ships.
TARGETS = {
    "web": ["templates/web_nothreads_release.zip"],
    "linux": ["templates/linux_release.x86_64"],
    "windows": ["templates/windows_release_x86_64.exe",
                "templates/windows_release_x86_64_console.exe"],
    "mac": ["templates/macos.zip"],
}
ALWAYS = ["templates/version.txt"]


def home() -> Path:
    return data_dir() / "godot"


def editor_dir() -> Path:
    return home() / RELEASE


def templates_dir() -> Path:
    """Where Godot itself looks. `XDG_DATA_HOME` is pointed here when it runs,
    so this never touches an install the user already has of their own."""
    return home() / "templates" / "godot" / "export_templates" / f"{VERSION}.stable"


def binary() -> Path | None:
    """The editor executable, or None if it has not been installed."""
    directory = editor_dir()
    if not directory.is_dir():
        return None
    for candidate in sorted(directory.iterdir()):
        if candidate.is_dir() and candidate.suffix == ".app":  # macOS bundle
            inner = candidate / "Contents" / "MacOS" / "Godot"
            if inner.is_file():
                return inner
        if candidate.is_file() and candidate.name.startswith("Godot") \
                and candidate.suffix not in (".zip", ".part"):
            return candidate
    return None


def installed() -> bool:
    return binary() is not None


def targets_installed() -> list[str]:
    """Which export targets can be built right now."""
    present = templates_dir()
    if not present.is_dir():
        return []
    have = {p.name for p in present.iterdir()}
    return [name for name, files in TARGETS.items()
            if all(Path(f).name in have for f in files)]


# ── reading part of a remote zip ───────────────────────────────────────────


class _RemoteFile(io.RawIOBase):
    """A seekable file backed by HTTP range requests.

    So the templates archive can be opened as a zip and read from selectively.
    The whole point of this module's install being tolerable: 10 MB instead of
    1.3 GB, and a Windows build for the price of a photo album.
    """

    def __init__(self, url: str):
        self.url = url
        self.pos = 0
        request = urllib.request.Request(url, method="HEAD",
                                         headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=60) as response:
            self.size = int(response.headers["Content-Length"])

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        else:
            self.pos = self.size + offset
        return self.pos

    def readinto(self, buffer) -> int:
        want = len(buffer)
        if want == 0 or self.pos >= self.size:
            return 0
        end = min(self.pos + want, self.size) - 1
        request = urllib.request.Request(
            self.url,
            headers={"Range": f"bytes={self.pos}-{end}", "User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
        buffer[:len(data)] = data
        self.pos += len(data)
        return len(data)


# ── installing ─────────────────────────────────────────────────────────────


def _human(n: float) -> str:
    return f"{n / 1_000_000:.0f} MB"


def install_editor(say=print, force: bool = False) -> bool:
    if installed() and not force:
        say(f"Godot {VERSION} is already here.")
        return True

    key = (platform.system(), platform.machine())
    asset = EDITORS.get(key)
    if asset is None:
        say(f"No Godot build published for {key[0]} {key[1]}.")
        return False

    directory = editor_dir()
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / "godot.zip"
    say(f"Downloading Godot {VERSION} (about 80 MB)...")
    try:
        request = urllib.request.Request(f"{BASE}/{asset}",
                                         headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=120) as response, \
                open(archive, "wb") as out:
            shutil.copyfileobj(response, out)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(directory)
    except Exception as e:
        say(f"  Could not install Godot: {e}")
        return False
    finally:
        archive.unlink(missing_ok=True)

    found = binary()
    if found is None:
        say("  The download unpacked but no Godot binary was in it.")
        return False
    found.chmod(0o755)
    say(f"Godot {VERSION} installed.")
    return True


def install_targets(names: list[str], say=print) -> bool:
    """Pull just the export templates asked for out of the 1.3 GB archive."""
    wanted: list[str] = list(ALWAYS)
    for name in names:
        if name not in TARGETS:
            say(f"  No such export target: {name}")
            return False
        wanted += TARGETS[name]

    missing = [f for f in wanted if not (templates_dir() / Path(f).name).is_file()]
    if not missing:
        say(f"Export templates for {', '.join(names)} are already here.")
        return True

    destination = templates_dir()
    destination.mkdir(parents=True, exist_ok=True)
    say(f"Fetching export templates for {', '.join(names)}...")
    try:
        remote = io.BufferedReader(_RemoteFile(TEMPLATES_ARCHIVE), buffer_size=1 << 16)
        with zipfile.ZipFile(remote) as archive:
            for name in missing:
                data = archive.read(name)
                target = destination / Path(name).name
                partial = target.with_suffix(target.suffix + ".part")
                partial.write_bytes(data)
                shutil.move(str(partial), str(target))
                say(f"  {Path(name).name} ({_human(len(data))})")
    except Exception as e:
        say(f"  Could not fetch the export templates: {e}")
        return False
    say("Export templates installed.")
    return True


def install(names: list[str] | None = None, say=print, force: bool = False) -> bool:
    """Godot plus the export targets asked for. Web by default -- it is the one
    that puts a game in the app's own window and the one that makes it something
    you can send to somebody."""
    if not install_editor(say, force=force):
        return False
    return install_targets(names or ["web"], say)


# ── a project that already works ───────────────────────────────────────────

PROJECT_GODOT = """\
config_version=5

[application]
config/name="{name}"
run/main_scene="res://main.tscn"
config/features=PackedStringArray("{version}", "Forward Plus")

[display]
window/size/viewport_width=640
window/size/viewport_height=360
window/stretch/mode="canvas_items"

[rendering]
; Desktop gets the full renderer. Browsers have no Vulkan, so the web build --
; and only the web build -- drops to the compatibility one. Same project.
renderer/rendering_method="forward_plus"
renderer/rendering_method.web="gl_compatibility"
"""

MAIN_TSCN = """\
[gd_scene load_steps=3 format=3]

[ext_resource type="Script" path="res://main.gd" id="1"]
[ext_resource type="Script" path="res://debug.gd" id="2"]

[node name="Main" type="Node2D"]
script = ExtResource("1")

[node name="Debug" type="Node" parent="."]
script = ExtResource("2")
"""

MAIN_GD = '''\
extends Node2D

# Everything is built here in code rather than laid out in the editor. That is
# a deliberate choice for this app: scene files carry generated resource ids
# that are painful to write by hand and easy to get subtly wrong, and code is
# the thing that can be explained to whoever is watching it being made.

var player: ColorRect
var speed := 220.0


func _ready() -> void:
\tvar background := ColorRect.new()
\tbackground.color = Color(0.09, 0.11, 0.18)
\tbackground.size = Vector2(640, 360)
\tadd_child(background)

\tplayer = ColorRect.new()
\tplayer.color = Color(1.0, 0.45, 0.2)
\tplayer.size = Vector2(40, 40)
\tplayer.position = Vector2(300, 160)
\tadd_child(player)

\tvar label := Label.new()
\tlabel.text = "Use the arrow keys"
\tlabel.position = Vector2(16, 12)
\tadd_child(label)


func _process(delta: float) -> void:
\tvar direction := Vector2.ZERO
\tif Input.is_action_pressed("ui_right"): direction.x += 1
\tif Input.is_action_pressed("ui_left"): direction.x -= 1
\tif Input.is_action_pressed("ui_down"): direction.y += 1
\tif Input.is_action_pressed("ui_up"): direction.y -= 1
\tplayer.position += direction * speed * delta
'''

DEBUG_GD = '''\
extends Node

# How the assistant checks its own work, and nothing the player ever sees.
#
# In a browser it publishes the game's state to the page, so a test can press a
# key and read back where things actually are instead of guessing from pixels.
# Run headless with `--selftest` and it presses a key itself and prints the
# result, which is the same check with no browser involved.
#
# Delete this node and its line in main.tscn before shipping if you like; it
# does nothing in a release build that is not being looked at.

var main: Node2D
var frames := 0
var start_x := 0.0
var testing := false


func state() -> Dictionary:
\t# Add anything worth checking here. Whatever this returns is what the
\t# assistant can see from outside the game.
\treturn {
\t\t"x": main.player.position.x,
\t\t"y": main.player.position.y,
\t\t"speed": main.speed,
\t}


func _ready() -> void:
\tmain = get_parent()
\ttesting = "--selftest" in OS.get_cmdline_user_args()
\tif not testing:
\t\treturn
\tawait get_tree().process_frame
\tstart_x = main.player.position.x
\tprint("TEST start_x=", start_x)
\t_press(KEY_RIGHT, true)


func _press(key: int, down: bool) -> void:
\tvar event := InputEventKey.new()
\tevent.keycode = key
\tevent.physical_keycode = key
\tevent.pressed = down
\tInput.parse_input_event(event)


func _process(_delta: float) -> void:
\tif OS.has_feature("web"):
\t\tJavaScriptBridge.eval("window.__game = " + JSON.stringify(state()) + ";", true)
\tif not testing:
\t\treturn
\tframes += 1
\tif frames == 30:
\t\t_press(KEY_RIGHT, false)
\t\tvar moved: float = main.player.position.x - start_x
\t\tprint("TEST moved=", moved, " -> ", "PASS" if moved > 10 else "FAIL")
\t\tget_tree().quit()
'''

# One preset per target. `thread_support=false` on web is what makes the build
# work with no special server headers, which is what makes it something you can
# put anywhere and send to anybody.
PRESETS = """\
[preset.0]

name="Web"
platform="Web"
runnable=true
custom_features=""
export_filter="all_resources"
export_path="../build/web/index.html"

[preset.0.options]

variant/extensions_support=false
variant/thread_support=false
html/export_icon=true
html/canvas_resize_policy=2
html/focus_canvas_on_start=true

[preset.1]

name="Linux"
platform="Linux"
runnable=true
custom_features=""
export_filter="all_resources"
export_path="../build/linux/{slug}.x86_64"

[preset.1.options]

binary_format/embed_pck=true
binary_format/architecture="x86_64"

[preset.2]

name="Windows Desktop"
platform="Windows Desktop"
runnable=true
custom_features=""
export_filter="all_resources"
export_path="../build/windows/{slug}.exe"

[preset.2.options]

binary_format/embed_pck=true
binary_format/architecture="x86_64"

[preset.3]

name="macOS"
platform="macOS"
runnable=true
custom_features=""
export_filter="all_resources"
export_path="../build/mac/{slug}.zip"

[preset.3.options]

binary_format/architecture="universal"
"""

PRESET_NAMES = {"web": "Web", "linux": "Linux",
                "windows": "Windows Desktop", "mac": "macOS"}


def _slug(name: str) -> str:
    """A filename from a game's name. This ends up on the thing they give away,
    so `franks-game` rather than `frank-s-game`: an apostrophe is dropped, not
    turned into a word break, and runs of punctuation collapse to one dash."""
    out: list[str] = []
    for ch in name.strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in "'\u2019":
            continue
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-") or "game"


def new_project(folder: Path, name: str = "Game", say=print) -> bool:
    """Write a project that runs before anything has been asked of it.

    Starting from nothing means the first thing that happens is a blank window
    or an error, and a scene file written from memory is the single most likely
    thing to be subtly wrong. Starting from something that moves means the first
    change is a change rather than a construction.
    """
    project = folder / "game"
    project.mkdir(parents=True, exist_ok=True)
    (project / "project.godot").write_text(
        PROJECT_GODOT.format(name=name, version=".".join(VERSION.split(".")[:2])),
        encoding="utf-8")
    (project / "main.tscn").write_text(MAIN_TSCN, encoding="utf-8")
    (project / "main.gd").write_text(MAIN_GD, encoding="utf-8")
    (project / "debug.gd").write_text(DEBUG_GD, encoding="utf-8")
    (project / "export_presets.cfg").write_text(
        PRESETS.format(slug=_slug(name)), encoding="utf-8")
    # Outside the project, because Godot re-imports whatever is inside it.
    (folder / "build").mkdir(exist_ok=True)
    say(f"Made a Godot project in {project}")
    say(f"Run it with:    {binary() or 'godot'} --path {project}")
    return True


# ── running, exporting, checking ───────────────────────────────────────────


def _env() -> dict:
    """Godot's own data directory pointed at ours, so the templates this app
    installed are the ones it finds -- and so none of this disturbs a Godot the
    user installed themselves."""
    env = dict(os.environ)
    env["XDG_DATA_HOME"] = str(home() / "templates")
    if platform.system() == "Darwin":
        env["HOME"] = str(home() / "templates")
    return env


def _run(args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    godot = binary()
    if godot is None:
        raise FileNotFoundError("Godot is not installed. Run: "
                                "python -m agent_server.godot install")
    return subprocess.run([str(godot), *args], env=_env(),
                          capture_output=True, text=True, timeout=timeout)


# A script that fails to parse does not stop the engine. It prints the parse
# error, loads an empty scene, and then sits there for as long as you let it --
# so the self-test, whose whole job is to notice a broken script, was the one
# thing that hung on one. Measured: forty-five seconds and still going.
#
# `--quit-after` counts frames and is a backstop the running project cannot
# talk its way out of. A working self-test finishes in about thirty frames and
# quits itself; a broken one now comes back in seconds with the parse error,
# which is the answer that was wanted.
QUIT_AFTER_FRAMES = 900
CHECK_TIMEOUT = 120


def run_command(project: Path) -> str:
    """What to hand `preview` so the app owns the running game."""
    godot = binary()
    return f'"{godot}" --path "{project}"' if godot else ""


def export(project: Path, target: str, say=print) -> bool:
    if target not in PRESET_NAMES:
        say(f"No such target: {target}. One of: {', '.join(PRESET_NAMES)}")
        return False
    if target not in targets_installed():
        say(f"The {target} export template is not installed yet; fetching it.")
        if not install_targets([target], say):
            return False

    out = project.parent / "build" / target
    out.mkdir(parents=True, exist_ok=True)
    say(f"Exporting {target}...")
    try:
        done = _run(["--headless", "--path", str(project),
                     "--export-release", PRESET_NAMES[target]])
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        say(f"  {e}")
        return False

    # Godot's headless export exits 0 even when it produced nothing, and prints
    # a shutdown error on the way out that means nothing. What was written is
    # the only honest signal.
    made = [p for p in out.rglob("*") if p.is_file()]
    if not made:
        say("  Nothing was written. Godot said:")
        say((done.stdout + done.stderr)[-1200:])
        return False
    size = sum(p.stat().st_size for p in made)
    say(f"Exported to {out} ({len(made)} files, {_human(size)})")
    return True


def check(project: Path, say=print) -> bool:
    """Run the project headless with its own self-test and report what it said."""
    try:
        done = _run(
            ["--headless", "--path", str(project),
             "--quit-after", str(QUIT_AFTER_FRAMES), "--", "--selftest"],
            timeout=CHECK_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        say(f"  {e}")
        return False
    output = done.stdout + done.stderr
    lines = [ln for ln in output.splitlines()
             if ln.startswith("TEST") or "SCRIPT ERROR" in ln or "ERROR:" in ln]
    for line in lines:
        say(line)
    if not lines:
        say("The project ran and the self-test said nothing.")
    return "FAIL" not in output and "SCRIPT ERROR" not in output


# ── command line ───────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    what = argv[1] if len(argv) > 1 else ""
    rest = argv[2:]

    if what == "install":
        names = [a for a in rest if not a.startswith("-")] or ["web"]
        return 0 if install(names, force="--force" in rest) else 1
    if what == "where":
        found = binary()
        print(found or "(not installed)")
        print(f"export targets: {', '.join(targets_installed()) or '(none)'}")
        return 0 if found else 1
    if what == "new" and rest:
        name = rest[1] if len(rest) > 1 else "Game"
        return 0 if new_project(Path(rest[0]).expanduser(), name) else 1
    if what == "export" and len(rest) >= 2:
        return 0 if export(Path(rest[0]).expanduser(), rest[1]) else 1
    if what == "check" and rest:
        return 0 if check(Path(rest[0]).expanduser()) else 1

    print(__doc__.split("Standard library only")[-1].strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
