"""Screen capture for things that are not web pages.

Playwright drives Chromium, so it can see a browser and nothing else. A native
game, a desktop app, an emulator or a terminal needs the operating system's own
capture, and every platform does that differently -- and on Linux, every
compositor does it differently again.

Rather than assume, backends are probed in order and the first that works is
used. When none does, the error names the exact package to install for the
platform that was actually detected, because "screen capture unavailable" is
not something a user can act on.
"""

import asyncio
import os
import platform
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from agent_server.config import CAPTURE_DIR

MAX_FRAMES = 24
TIMEOUT_SEC = 20


class CaptureError(RuntimeError):
    pass


@dataclass(frozen=True)
class Backend:
    name: str
    binary: str
    # Built with (output_path, region) -> argv. `region` is "x,y,w,h" or "".
    build: object
    # Whether it can capture a sub-rectangle without help.
    regions: bool = False
    install: str = ""


def _grim(path: str, region: str) -> list[str]:
    if not region:
        return ["grim", path]
    x, y, w, h = region.split(",")
    return ["grim", "-g", f"{x},{y} {w}x{h}", path]


def _spectacle(path: str, region: str) -> list[str]:
    return ["spectacle", "-b", "-n", "-f", "-o", path]


def _gnome(path: str, region: str) -> list[str]:
    return ["gnome-screenshot", "-f", path]


def _maim(path: str, region: str) -> list[str]:
    if not region:
        return ["maim", path]
    x, y, w, h = region.split(",")
    return ["maim", "-g", f"{w}x{h}+{x}+{y}", path]


def _scrot(path: str, region: str) -> list[str]:
    return ["scrot", "-o", path]


def _imagemagick(path: str, region: str) -> list[str]:
    if not region:
        return ["import", "-window", "root", path]
    x, y, w, h = region.split(",")
    return ["import", "-window", "root", "-crop", f"{w}x{h}+{x}+{y}", path]


def _ffmpeg_x11(path: str, region: str) -> list[str]:
    display = os.getenv("DISPLAY", ":0")
    args = ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab"]
    if region:
        x, y, w, h = region.split(",")
        args += ["-video_size", f"{w}x{h}", "-i", f"{display}+{x},{y}"]
    else:
        args += ["-i", display]
    return [*args, "-frames:v", "1", path]


def _screencapture(path: str, region: str) -> list[str]:
    return ["screencapture", "-x", *(["-R", region] if region else []), path]


# PowerShell is always present on Windows, so no package to install.
_PS_SCRIPT = (
    "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
    "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
    "$m=New-Object System.Drawing.Bitmap($b.Width,$b.Height);"
    "$g=[System.Drawing.Graphics]::FromImage($m);"
    "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$m.Size);"
    "$m.Save('{path}',[System.Drawing.Imaging.ImageFormat]::Png)"
)


def _powershell(path: str, region: str) -> list[str]:
    return ["powershell", "-NoProfile", "-Command", _PS_SCRIPT.format(path=path)]


_LINUX_WAYLAND = [
    Backend("grim", "grim", _grim, regions=True,
            install="grim (and slurp for regions) -- wlroots compositors: Hyprland, Sway, river"),
    Backend("spectacle", "spectacle", _spectacle, install="spectacle -- KDE Plasma"),
    Backend("gnome-screenshot", "gnome-screenshot", _gnome, install="gnome-screenshot -- GNOME"),
]
_LINUX_X11 = [
    Backend("maim", "maim", _maim, regions=True, install="maim"),
    Backend("imagemagick", "import", _imagemagick, regions=True, install="imagemagick"),
    Backend("scrot", "scrot", _scrot, install="scrot"),
    Backend("ffmpeg", "ffmpeg", _ffmpeg_x11, regions=True, install="ffmpeg"),
]
_MAC = [Backend("screencapture", "screencapture", _screencapture, regions=True)]
_WINDOWS = [Backend("powershell", "powershell", _powershell)]


def _candidates() -> tuple[list[Backend], str]:
    """Backends worth trying here, and a description of 'here' for errors."""
    system = platform.system()
    if system == "Darwin":
        return _MAC, "macOS"
    if system == "Windows":
        return _WINDOWS, "Windows"
    if system != "Linux":
        return [], system or "this platform"

    session = (os.getenv("XDG_SESSION_TYPE") or "").lower()
    desktop = os.getenv("XDG_CURRENT_DESKTOP") or os.getenv("DESKTOP_SESSION") or "unknown"
    if session == "wayland" or os.getenv("WAYLAND_DISPLAY"):
        # No X11 fallback here. Those tools are usually installed and usually
        # *succeed* under XWayland -- writing a fully black image, because the
        # compositor never composites real windows into the X root. Measured
        # on Hyprland: ffmpeg x11grab exits 0 and produces 100% #000000. A
        # backend that silently returns nothing is worse than no backend, so
        # Wayland gets Wayland tools or an error naming what to install.
        return _LINUX_WAYLAND, f"Linux/Wayland ({desktop})"
    return _LINUX_X11, f"Linux/X11 ({desktop})"


def available() -> Backend | None:
    for backend in _candidates()[0]:
        if shutil.which(backend.binary):
            return backend
    return None


def unavailable_message() -> str:
    backends, where = _candidates()
    if not backends:
        return f"Screen capture is not supported on {where}."
    options = []
    for backend in backends:
        if backend.install and backend.install not in options:
            options.append(backend.install)
    listed = "\n".join(f"  - {o}" for o in options)
    return (
        f"No screen capture tool found for {where}. Install one of:\n{listed}\n"
        "Web pages do not need this -- use the `browser` tool for those."
    )


async def grab(region: str = "", *, count: int = 1, interval_ms: int = 400) -> list[str]:
    """Capture the screen. Returns the paths written."""
    backend = available()
    if backend is None:
        raise CaptureError(unavailable_message())
    if region and not backend.regions:
        raise CaptureError(
            f"{backend.name} cannot capture a region on this platform. "
            "Capture the whole screen and crop, or install a backend that can."
        )

    count = max(1, min(int(count or 1), MAX_FRAMES))
    interval_ms = max(0, min(int(interval_ms or 0), 5_000))
    stamp = time.strftime("%H%M%S")
    paths: list[str] = []

    for index in range(count):
        if index:
            await asyncio.sleep(interval_ms / 1000)
        path = CAPTURE_DIR / (
            f"screen_{stamp}_{index:02d}.png" if count > 1 else f"screen_{stamp}.png"
        )
        await _run(backend.build(str(path), region), backend)
        if not path.exists() or path.stat().st_size == 0:
            raise CaptureError(
                f"{backend.name} exited cleanly but wrote nothing. On Wayland this "
                "usually means the compositor denied the request."
            )
        if index == 0 and _is_blank(path):
            raise CaptureError(
                f"{backend.name} produced a blank image. The capture succeeded as far "
                "as the tool is concerned, so this is the compositor refusing to hand "
                "over the screen rather than an error it reported.\n"
                + unavailable_message()
            )
        paths.append(str(path))
    return paths


def _is_blank(path: Path) -> bool:
    """A single flat colour, which is what a refused capture looks like.

    Checked because the failure is otherwise invisible: the command exits 0,
    the file is a valid PNG, and the only symptom is a model being sent a black
    rectangle and asked what is wrong with the game.
    """
    try:
        from PIL import Image

        with Image.open(path) as im:
            low, high = im.convert("L").getextrema()
            return low == high
    except Exception:
        return False


async def _run(argv: list[str], backend: Backend):
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        raise CaptureError(f"could not run {backend.binary}: {e}") from e
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_SEC)
    except TimeoutError as e:
        proc.kill()
        await proc.wait()
        raise CaptureError(
            f"{backend.name} did not finish within {TIMEOUT_SEC}s. A portal "
            "permission dialog may be waiting for a click."
        ) from e
    if proc.returncode != 0:
        detail = stderr.decode("utf-8", "replace").strip().splitlines()
        raise CaptureError(
            f"{backend.name} failed: {detail[-1] if detail else f'exit {proc.returncode}'}"
        )
