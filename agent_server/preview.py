"""Running the user's project for them, in a window they can actually use.

The user does not have a terminal. If the thing they asked for is not running,
it does not exist -- so starting it is the assistant's job, and this is the
machinery that makes that possible rather than aspirational.

**One slot per project.** Start again and the old one is stopped first. That
rule is the whole design, and it exists because of the failure it prevents: an
assistant that opens a window every time it finishes a change leaves the user
with thirty of them by the evening, none obviously the current build.

**The app holds the handle, not the agent.** An agent that ran `xdg-open` has
no way to close what it opened; telling it to "close the old tab first" asks
for something it cannot do, and it will say it did. So the process is spawned
here, in its own process group -- `npm run dev` is a shell that spawns node,
and killing only the shell leaves the port held by an orphan -- and the window
is a page this module owns and *navigates* rather than reopening.

Nothing here is web-specific. A command is a command: Next, Vite, Django, a
Godot binary, a Python script with a window of its own. `url` is optional, and
when it is absent the project simply runs with no browser attached.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent_server.config import DATA_DIR

log = logging.getLogger(__name__)

PREVIEW_PROFILES = DATA_DIR / "preview_profiles"
PREVIEW_LOG_DIR = DATA_DIR / "preview_logs"

# Addresses that count as "this machine" when a child's preview is confined.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]", ""}

# How long a `stop` waits for a polite shutdown before it stops asking.
GRACE_SECONDS = 4.0
# The tail kept from a run's output. Enough to hold a stack trace and the line
# above it, which is what says why a server would not start.
MAX_LOG_CHARS = 6000
# Longest `start` will wait for a URL to begin answering.
MAX_WAIT_MS = 60_000


class PreviewError(RuntimeError):
    pass


@dataclass
class Slot:
    session_id: str
    command: str
    url: str = ""
    cwd: str = ""
    process: object = None
    log_path: Path | None = None
    started_at: float = field(default_factory=time.monotonic)

    def running(self) -> bool:
        return bool(self.process) and self.process.returncode is None

    def output(self) -> str:
        if not self.log_path or not self.log_path.exists():
            return ""
        try:
            text = self.log_path.read_text(errors="replace")
        except OSError:
            return ""
        return text[-MAX_LOG_CHARS:]


_slots: dict[str, Slot] = {}
_lock = asyncio.Lock()

# One browser window per project, each with its own kept profile. A window that
# is navigated is a window that does not become a thirty-first tab.
_playwright = None
_contexts: dict[str, object] = {}


# ── the process ─────────────────────────────────────────────────────────────


async def _spawn(session_id: str, command: str, cwd: str) -> Slot:
    PREVIEW_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = PREVIEW_LOG_DIR / f"{_safe(session_id)}.log"
    handle = log_path.open("wb")
    try:
        # A new process group, so the whole tree can be signalled. Without it,
        # stopping `npm run dev` kills the shell and leaves node holding the
        # port -- and the next start fails on an address already in use, which
        # reads as a bug in the project rather than in this.
        kwargs = {"start_new_session": True} if os.name != "nt" else {
            "creationflags": getattr(__import__("subprocess"), "CREATE_NEW_PROCESS_GROUP", 0)
        }
        process = await asyncio.create_subprocess_shell(
            command, cwd=cwd or None, stdout=handle, stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL, **kwargs,
        )
    except Exception as e:
        handle.close()
        raise PreviewError(f"could not start it: {e}") from e
    finally:
        with contextlib.suppress(Exception):
            handle.close()

    return Slot(session_id=session_id, command=command, cwd=cwd,
                process=process, log_path=log_path)


async def _kill(slot: Slot):
    process = slot.process
    if not process or process.returncode is not None:
        return
    # SIGKILL does not exist on Windows, where `_signal_group` uses taskkill
    # for both passes anyway.
    hard = getattr(signal, "SIGKILL", signal.SIGTERM)
    for sig, wait in ((signal.SIGTERM, GRACE_SECONDS), (hard, 2.0)):
        _signal_group(process, sig)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=wait)
            return
    log.warning("preview for %s would not stop", slot.session_id)


def _signal_group(process, sig):
    """Signal the whole tree, falling back to the one process we know about."""
    try:
        if os.name == "nt":  # pragma: no cover - platform specific
            import subprocess

            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)],
                           capture_output=True, check=False)
            return
        os.killpg(os.getpgid(process.pid), sig)
    except (ProcessLookupError, PermissionError, OSError):
        with contextlib.suppress(ProcessLookupError, OSError):
            process.send_signal(sig)


# ── the window ──────────────────────────────────────────────────────────────


def _profile_dir(session_id: str) -> Path:
    """One browser profile per project, kept between runs.

    Per project rather than shared, because a login for one has no business in
    another -- and kept rather than thrown away, because an app with a sign-in
    that makes you sign in again on every single launch is exhausting to build.
    Cookies, saved passwords and bookmarks all survive.
    """
    return PREVIEW_PROFILES / _safe(session_id)


def _is_local(url: str) -> bool:
    """Whether an address belongs to this machine."""
    from urllib.parse import urlparse

    if not url or url.startswith(("about:", "data:", "blob:", "chrome-error:")):
        return True
    parsed = urlparse(url)
    if parsed.scheme in ("file", ""):
        return True
    host = (parsed.hostname or "").lower()
    return host in LOCAL_HOSTS or host.endswith(".localhost")


def is_this_machine(url: str) -> bool:
    """Whether an address is one this project could be serving.

    The public half of `_is_local`, minus the schemes that are not addresses at
    all. Used to decide whether a link in a reply is a way into the user's own
    project -- in which case pressing it should behave like pressing Play --
    or a link out to the web.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    return host in LOCAL_HOSTS or host.endswith(".localhost")


async def _launch(session_id: str, url: str, confine: bool):
    """An ordinary browser window, pointed at the project.

    Ordinary on purpose. A chromeless `--app` frame was tried and reverted:
    testing a web app means using it the way a visitor would, and that needs
    the back and forward buttons, the history and the bookmarks. Taking the
    whole browser away to stop a child wandering off took those from everyone.

    What stops the wandering is `_confine` instead, which refuses to load a
    page from anywhere but this machine -- and refuses it however the address
    arrived, typed into the bar included. The restriction belongs on where the
    window may go, not on whether it has buttons.
    """
    from playwright.async_api import async_playwright

    global _playwright
    if _playwright is None:
        _playwright = await async_playwright().start()

    directory = _profile_dir(session_id)
    directory.mkdir(parents=True, exist_ok=True)
    context = await _playwright.chromium.launch_persistent_context(
        str(directory),
        headless=False,
        no_viewport=True,
        args=["--disable-features=Translate"],
    )
    _contexts[session_id] = context

    if confine:
        await _confine(context)
    return context


async def _confine(context):
    """Refuse to load a page from anywhere but this machine.

    Only in child mode, and only for documents -- a page may still fetch a font
    or a script from wherever it likes, because blocking those breaks ordinary
    development for no safety anyone gains. What it stops is the window itself
    becoming a way onto the open web, which is the thing a parent is trusting
    this app not to be.

    Not applied outside child mode: signing in to something is a normal part of
    building it, and those flows leave the origin by design.
    """

    async def guard(route, request):
        if request.resource_type == "document" and not _is_local(request.url):
            log.info("preview blocked a page from %s", request.url)
            await route.abort()
            return
        await route.continue_()

    await context.route("**/*", guard)

    # A popup is a new page, and a new page needs the same guard or it is a way
    # around it. Routed rather than closed: a sign-in that opens in a second
    # window is a normal thing for a project to do, and it is still confined.
    async def on_page(page):
        with contextlib.suppress(Exception):
            await page.route("**/*", guard)

    context.on("page", lambda page: asyncio.get_running_loop().create_task(on_page(page)))


async def _show(session_id: str, url: str, confine: bool = False):
    """Point this project's window at `url`, opening one only if there isn't one.

    Navigating the window that is already there, rather than opening another,
    is the entire reason this is the app's job rather than the agent's.
    """
    context = _contexts.get(session_id)
    page = _live_page(context)

    if page is not None:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.bring_to_front()
            return
        except Exception as e:
            # Closed by the user, or crashed. Open a fresh one rather than
            # reporting a failure they have already dealt with.
            log.info("preview window for %s was unusable (%s)", session_id, _brief(e))
            await _close_window(session_id)

    try:
        context = await _launch(session_id, url, confine)
    except Exception as e:
        _contexts.pop(session_id, None)
        raise PreviewError(
            "the project is running, but a window could not be opened to show it "
            f"({_brief(e)}). Tell the user the address to visit."
        ) from e

    page = _live_page(context)
    if page is not None:
        with contextlib.suppress(Exception):
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.bring_to_front()


def _live_page(context):
    if context is None:
        return None
    for page in context.pages:
        if not page.is_closed():
            return page
    return None


async def _close_window(session_id: str):
    context = _contexts.pop(session_id, None)
    if context is not None:
        with contextlib.suppress(Exception):
            await context.close()


async def reload_window(session_id: str) -> bool:
    """Show the current files without restarting the process.

    What "make it show the new version" almost always means: a dev server has
    already picked the change up, and a static one serves whatever is on disk.
    Cheaper than a restart and it does not throw away whatever the page was in
    the middle of.
    """
    page = _live_page(_contexts.get(session_id))
    if page is None:
        return False
    try:
        await page.reload(wait_until="domcontentloaded", timeout=30_000)
        return True
    except Exception as e:
        log.info("could not reload the preview for %s: %s", session_id, _brief(e))
        return False


# ── waiting for it to come up ───────────────────────────────────────────────


async def _wait_for(url: str, slot: Slot, timeout_ms: int) -> bool:
    """Poll until the address answers, or the process dies, or time runs out.

    Answering at all is the bar, not answering with a 200: a fresh Next project
    with no index route serves a 404 and is entirely up.
    """
    import httpx

    deadline = time.monotonic() + min(timeout_ms, MAX_WAIT_MS) / 1000
    async with httpx.AsyncClient(timeout=2.0, follow_redirects=False) as client:
        while time.monotonic() < deadline:
            if not slot.running():
                return False
            with contextlib.suppress(Exception):
                await client.get(url)
                return True
            await asyncio.sleep(0.25)
    return False


# ── what the tool calls ─────────────────────────────────────────────────────


async def start(session_id: str, command: str, url: str = "", cwd: str = "",
                wait_ms: int = 20_000, confine: bool = False) -> str:
    """Run the project, replacing whatever this project was running before."""
    async with _lock:
        # The window deliberately stays open across a restart. Closing it and
        # opening another is exactly the flicker-and-new-window behaviour this
        # whole module exists to avoid -- the user is often *looking* at it
        # while the rebuild happens, and it should simply become the new build.
        await _stop_locked(session_id, close_window=False)

        slot = await _spawn(session_id, command, cwd)
        _slots[session_id] = slot

        # A command that fails immediately is the common case -- a typo, a
        # missing dependency -- and its output is the answer.
        await asyncio.sleep(0.6)
        if not slot.running():
            output = slot.output().strip()
            _slots.pop(session_id, None)
            raise PreviewError(
                f"it exited straight away (code {slot.process.returncode}).\n"
                f"{output or '(it printed nothing)'}"
            )

        if not url:
            return (
                f"Running: {command}\nNo address given, so no window was opened. "
                "It is running in the background; use `capture` if you need to see "
                "a window it drew itself."
            )

        slot.url = url
        if not await _wait_for(url, slot, wait_ms):
            if not slot.running():
                output = slot.output().strip()
                _slots.pop(session_id, None)
                raise PreviewError(
                    f"it started and then stopped (code {slot.process.returncode}).\n"
                    f"{output or '(it printed nothing)'}"
                )
            return (
                f"Running: {command}\nIt is still running but {url} has not answered "
                f"yet. Recent output:\n{slot.output().strip() or '(nothing yet)'}"
            )

        try:
            await _show(session_id, url, confine)
        except PreviewError as e:
            # The project is up. Failing the call because a window would not
            # open would have the assistant report a broken build and go
            # looking for a bug that is not there.
            return f"Running: {command}\n{e}"
        return f"Running: {command}\nThe user is now looking at {url}."


async def show(session_id: str, url: str, confine: bool = False):
    """Point this project's window at an address, without touching the process.

    For a link the user pressed in a reply, where the project is already up.
    """
    async with _lock:
        await _show(session_id, url, confine)


async def stop(session_id: str) -> str:
    async with _lock:
        stopped = await _stop_locked(session_id, close_window=True)
    return "Stopped." if stopped else "Nothing was running."


async def _stop_locked(session_id: str, close_window: bool) -> bool:
    slot = _slots.pop(session_id, None)
    if close_window:
        await _close_window(session_id)
    if slot is None:
        return False
    await _kill(slot)
    return True


def status(session_id: str) -> str:
    slot = _slots.get(session_id)
    if slot is None:
        return "Nothing is running for this project."
    if not slot.running():
        code = slot.process.returncode
        return (
            f"`{slot.command}` has stopped on its own (code {code}). Recent "
            f"output:\n{slot.output().strip() or '(it printed nothing)'}"
        )
    where = f" at {slot.url}" if slot.url else ""
    seconds = int(time.monotonic() - slot.started_at)
    return f"`{slot.command}` has been running{where} for {seconds}s."


def is_running(session_id: str) -> bool:
    slot = _slots.get(session_id)
    return bool(slot and slot.running())


async def close_all():
    """Stop every preview. The app quitting must not leave servers behind."""
    async with _lock:
        for session_id in list(_slots):
            await _stop_locked(session_id, close_window=True)
        for session_id in list(_contexts):
            await _close_window(session_id)
    global _playwright
    if _playwright is not None:
        with contextlib.suppress(Exception):
            await _playwright.stop()
        _playwright = None


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)[:64] or "session"


def _brief(e: Exception) -> str:
    text = str(e).strip().splitlines()
    return text[0][:200] if text else type(e).__name__
