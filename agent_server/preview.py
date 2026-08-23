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

from agent_server import annotate
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
    # Whether "point at something" makes sense for what is in the window. A
    # web page: yes. A game, which is one `<canvas>` with everything painted
    # inside it, would hand back that canvas every time and mean nothing.
    pickable: bool = True
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

    # Installed once, for every page and every frame this window ever loads,
    # and inert until something arms it. Doing it here rather than per-page is
    # what makes it survive a navigation -- the user clicks through to another
    # page of their own app and pointing still works.
    with contextlib.suppress(Exception):
        await context.expose_binding(
            "__annotatePick",
            lambda _source, payload: annotate.deliver(session_id, payload),
        )
        await context.add_init_script(annotate.PICKER_JS)

    if confine:
        await _confine(context)
    return context


def _blocked_page(url: str) -> str:
    """What the project's window shows instead of a page from the open web.

    Deliberately not a scolding. The person reading it is as likely to be the
    grown-up who switched child mode on and forgotten as the child it is for,
    and the conclusion neither of them should reach is that something is broken.
    """
    from html import escape

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Not this one</title><style>
:root {{ color-scheme: dark light; }}
body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
       background: #17191c; color: #e8eaed; padding: 24px;
       font: 16px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
main {{ max-width: 34rem; }}
h1 {{ font-size: 1.5rem; margin: 0 0 14px; }}
p {{ margin: 0 0 14px; color: #c6cbd1; }}
code {{ background: #23262b; border-radius: 6px; padding: 2px 7px;
        font-size: 0.9em; overflow-wrap: anywhere; }}
</style></head><body><main>
<h1>This one stays off the internet</h1>
<p>Child mode is on, so this window only opens things on this computer — and
<code>{escape(url)}</code> is out on the web.</p>
<p>If you are the grown-up here: turn child mode off in Settings, under Parental
controls, and this window will go anywhere again.</p>
</main></body></html>"""


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
            # A page saying what happened, rather than the browser's own "this
            # site can't be reached". Aborting left somebody looking at a
            # connection error for a site that is perfectly fine, with nothing
            # anywhere to connect it to a setting they turned on last week.
            try:
                await route.fulfill(
                    status=200, content_type="text/html; charset=utf-8",
                    body=_blocked_page(request.url),
                )
            except Exception:
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
        # A missing Chromium is a thing to go and fix, not a thing to report.
        # The user has no terminal, and "ask somebody to download 150 MB" is a
        # worse answer than downloading it. One retry: if it fails twice the
        # problem is not the browser.
        from agent_server import setup

        if setup.looks_like_missing_browser(e) and await setup.ensure_chromium():
            log.info("reinstalled Chromium; opening the window again")
            try:
                context = await _launch(session_id, url, confine)
            except Exception as second:
                _contexts.pop(session_id, None)
                raise PreviewError(_no_window(second)) from second
        else:
            raise PreviewError(_no_window(e)) from e

    page = _live_page(context)
    if page is not None:
        with contextlib.suppress(Exception):
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.bring_to_front()


def _no_window(e: Exception) -> str:
    """Why no window appeared, in words the assistant can pass straight on.

    The missing-browser case is worth telling apart from every other launch
    failure, because it is the one with a fix -- and because it is not exotic.
    Playwright keeps Chromium in a cache directory, and a cache directory is
    the first thing any "free up disk space" tool empties.
    """
    from agent_server import setup

    detail = _brief(e)
    if setup.looks_like_missing_browser(e):
        # Reached only when putting it back has already been tried and failed,
        # which in practice means there is no way onto the internet from here.
        return (
            "the project is running, but the browser this app uses to show "
            "people their work is missing, and downloading it again did not "
            "work either -- most likely this computer is offline. Say that "
            "plainly, and that they can see it meanwhile at the address above "
            "in their own browser."
        )
    return (
        "the project is running, but a window could not be opened to show it "
        f"({detail}). Tell the user the address to visit."
    )


def _live_page(context):
    if context is None:
        return None
    for page in context.pages:
        if not page.is_closed():
            return page
    return None


async def _close_window(session_id: str):
    # Anyone waiting on a pick is waiting on a window that is about to not
    # exist. Told now, they get "cancelled"; left alone, they get three
    # minutes of nothing.
    annotate.forget(session_id)
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
                wait_ms: int = 20_000, confine: bool = False,
                pickable: bool = True) -> str:
    """Run the project, replacing whatever this project was running before."""
    async with _lock:
        # The window deliberately stays open across a restart. Closing it and
        # opening another is exactly the flicker-and-new-window behaviour this
        # whole module exists to avoid -- the user is often *looking* at it
        # while the rebuild happens, and it should simply become the new build.
        await _stop_locked(session_id, close_window=False)

        slot = await _spawn(session_id, command, cwd)
        slot.pickable = pickable
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


# ── pointing at something in the window ─────────────────────────────────────


def can_pick(session_id: str) -> bool:
    """Whether the button that says "point at something" should be there.

    It should be there when pointing would work and gone when it would not,
    and there is no third state. A button that appears and then explains why
    it cannot help you is worse than no button, especially for the person this
    app is for -- so a game simply has no such button, the way a machine with
    no camera has no camera button.
    """
    slot = _slots.get(session_id)
    if slot is None or not slot.running() or not slot.pickable:
        return False
    return _live_page(_contexts.get(session_id)) is not None


async def arm(session_id: str) -> None:
    """Put the window in front and let the user click something in it."""
    page = _live_page(_contexts.get(session_id))
    if page is None:
        raise PreviewError("the project's window is not open.")
    with contextlib.suppress(Exception):
        await page.bring_to_front()
    # Every frame, not just the main one: an app that renders part of itself
    # in an iframe is still an app someone wants to point at.
    armed = 0
    for frame in page.frames:
        try:
            await frame.evaluate("() => window.__pickerArm && window.__pickerArm()")
            armed += 1
        except Exception:
            continue  # cross-origin, or gone mid-navigation
    if not armed:
        raise PreviewError("the page in the window would not respond.")


async def disarm(session_id: str) -> None:
    """Take the crosshair away again, wherever the user got to."""
    page = _live_page(_contexts.get(session_id))
    if page is None:
        return
    for frame in page.frames:
        with contextlib.suppress(Exception):
            await frame.evaluate("() => window.__pickerDisarm && window.__pickerDisarm()")


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
