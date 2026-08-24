"""A browser the agent can drive, one per session.

Replaces two overlapping implementations that did not share state: five
`browser-*` tools sharing one global page, and a `screenshot` tool that
launched a fresh browser for every call and closed it again. So logging in with
`browser-fill` and then taking a `screenshot` produced a shot of a logged-out
page from a different browser, and nothing said which tools were stateful.

The design follows from what an agent actually needs to verify a UI:

* **One context per session.** A flow spans several tool calls, so the browser
  has to remember it is logged in. Per session rather than global, because one
  chat's cookies have no business in another's.
* **Console, page errors and failed requests are always recorded.** "The button
  did nothing" is not a finding; `Uncaught TypeError at app.js:1841` is. The
  old tools could not see any of it.
* **Every frame is written to disk and its path returned.** No model this app
  can talk to is able to look at an image, so a screenshot is worth taking for
  one reason only: the path can be written into a reply, and then the *user*
  sees the picture. That is also why nothing here tries to describe a frame.

Note this tool is *meant* to reach localhost -- verifying the app you are
building is the point -- so it has no private-network guard, unlike `webfetch`.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_server.config import BROWSER_STATE_DIR, CAPTURE_DIR

log = logging.getLogger(__name__)

DEFAULT_VIEWPORT = (1280, 900)
# lines a second. Keep the newest.
MAX_CONSOLE = 300
# requests a second. Keep the newest.
MAX_NETWORK = 500
MAX_FRAMES = 24
# A context nobody has touched for this long is closed. Chromium holds ~100MB.
IDLE_TIMEOUT_SEC = 900


class BrowserError(RuntimeError):
    pass


@dataclass
class ConsoleEntry:
    kind: str          # log | info | warn | error | pageerror | request
    text: str
    location: str = ""

    def render(self) -> str:
        where = f"  ({self.location})" if self.location else ""
        return f"{self.kind:<9} {self.text}{where}"


@dataclass
class NetworkEntry:
    """One HTTP request as seen by the page: method, status, url."""

    method: str
    url: str
    status: str  # "200" style, or "failed"

    def render(self) -> str:
        return f"{self.method:<6} {self.status:<8} {self.url}"


@dataclass
class Session:
    """One browser context, its page, and everything that page has said."""

    context: Any
    page: Any
    console: list[ConsoleEntry] = field(default_factory=list)
    network: list[NetworkEntry] = field(default_factory=list)
    # How much of `console` has already been reported, so each step shows only
    # what is new rather than repeating the whole log every time.
    reported: int = 0
    touched: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def note(self, entry: ConsoleEntry):
        self.console.append(entry)
        if len(self.console) > MAX_CONSOLE:
            dropped = len(self.console) - MAX_CONSOLE
            del self.console[:dropped]
            self.reported = max(0, self.reported - dropped)

    def note_network(self, entry: NetworkEntry):
        self.network.append(entry)
        if len(self.network) > MAX_NETWORK:
            del self.network[: len(self.network) - MAX_NETWORK]

    def fresh(self) -> list[ConsoleEntry]:
        new = self.console[self.reported:]
        self.reported = len(self.console)
        return new

    def errors(self) -> list[ConsoleEntry]:
        return [e for e in self.console if e.kind in ("error", "pageerror", "request")]


_sessions: dict[str, Session] = {}
_playwright: Any = None
_browser: Any = None
_launch_lock = asyncio.Lock()


def _cannot_start(e: Exception) -> str:
    """Why the browser would not start, in words the model can pass on.

    Only two of these are worth telling apart. Missing libraries is the one
    thing here nobody can fix without a password, so it has to be named rather
    than described -- the user cannot act on it themselves, but somebody
    sitting with them can, and only if they are told what it is.
    """
    from agent_server import setup

    if setup.looks_like_missing_system_libraries(e):
        return (
            "Chromium is installed on this computer but cannot run: the "
            "system is missing libraries it needs. This one cannot be fixed "
            "from inside the app -- it needs an administrator to run "
            "`playwright install-deps`. Say that plainly, once, and carry on "
            "without checking pages yourself."
        )
    if setup.looks_like_missing_browser(e):
        return (
            "Chromium is missing and downloading it did not work either -- "
            "most likely this computer is offline. Say so plainly and carry "
            "on without checking pages yourself."
        )
    return f"could not start Chromium: {_brief(e)}"


async def _ensure_browser():
    """One Chromium process, shared by every session's context.

    Contexts are cheap and isolated; browsers are neither. `is_connected` is
    checked because a crashed Chromium previously left every later call
    throwing for the life of the process, with no way to recover.
    """
    global _playwright, _browser
    async with _launch_lock:
        if _browser is not None and _browser.is_connected():
            return _browser
        if _browser is not None:
            _browser = None
        if _playwright is None:
            from playwright.async_api import async_playwright

            _playwright = await async_playwright().start()
        args = ["--no-sandbox", "--disable-dev-shm-usage"]
        try:
            _browser = await _playwright.chromium.launch(headless=True, args=args)
        except Exception as e:
            # Telling the model to run `playwright install chromium` made the
            # fix somebody else's turn, and put a shell command in a reply that
            # a user might well have been shown. Fetching it is a second here
            # and nobody has to know.
            from agent_server import setup

            if setup.looks_like_missing_browser(e) and await setup.ensure_chromium():
                # Guarded: an install can report success and still leave a
                # browser that will not start -- a truncated download, or a
                # machine short of the libraries Chromium links against. Raw,
                # that came back to the model as a Playwright traceback with a
                # cache path in it, which is nothing it can act on.
                try:
                    _browser = await _playwright.chromium.launch(
                        headless=True, args=args)
                    return _browser
                except Exception as second:
                    raise BrowserError(_cannot_start(second)) from second
            raise BrowserError(_cannot_start(e)) from e
        return _browser


async def get_session(session_id: str, width: int = 0, height: int = 0) -> Session:
    existing = _sessions.get(session_id)
    if existing is not None:
        existing.touched = time.monotonic()
        return existing

    browser = await _ensure_browser()
    state = _state_path(session_id)
    kwargs: dict = dict(
        viewport={
            "width": width or DEFAULT_VIEWPORT[0],
            "height": height or DEFAULT_VIEWPORT[1],
        },
        ignore_https_errors=True,
    )
    # A saved login is loaded back when the context is recreated after being
    # reaped, or the app restarted. Missing file is simply a fresh profile.
    if state.exists():
        kwargs["storage_state"] = str(state)
    context = await browser.new_context(**kwargs)
    context.set_default_timeout(10_000)
    page = await context.new_page()
    session = Session(context=context, page=page)
    _wire_listeners(session, page)
    # Pages opened by target=_blank or window.open would otherwise be invisible.
    context.on("page", lambda p: _adopt(session, p))
    _sessions[session_id] = session
    return session


def _adopt(session: Session, page):
    _wire_listeners(session, page)
    session.page = page


def _wire_listeners(session: Session, page):
    def on_console(message):
        kind = message.type
        kind = {"warning": "warn"}.get(kind, kind)
        location = ""
        try:
            loc = message.location
            if loc and loc.get("url"):
                location = f"{loc['url']}:{loc.get('lineNumber', 0)}"
        except Exception:
            log.debug("reading console message location failed", exc_info=True)
            location = ""
        session.note(ConsoleEntry(kind, message.text, location))

    page.on("console", on_console)
    page.on("pageerror", lambda e: session.note(ConsoleEntry("pageerror", str(e))))

    def on_request_failed(request):
        failure = request.failure
        reason = failure.get("errorText", "unknown") if failure else "unknown"
        session.note(ConsoleEntry(
            "request", f"{request.method} {request.url} failed: {reason}"
        ))
        session.note_network(NetworkEntry(request.method, request.url, "failed"))

    page.on("requestfailed", on_request_failed)

    def on_response(response):
        session.note_network(NetworkEntry(
            response.request.method, response.url, str(response.status)
        ))
        if response.status >= 400:
            session.note(ConsoleEntry(
                "request", f"{response.status} {response.request.method} {response.url}"
            ))

    page.on("response", on_response)


def _state_path(session_id: str) -> Path:
    safe = "".join(c for c in session_id if c.isalnum())[:8] or "s"
    return BROWSER_STATE_DIR / f"{safe}.json"


async def close_session(session_id: str):
    session = _sessions.pop(session_id, None)
    if session is None:
        return
    # Save cookies/localStorage first so a login survives the context being
    # reaped or the browser process restarting. A context that never loaded
    # anything has nothing worth writing, but storage_state() is cheap enough.
    try:
        data = await session.context.storage_state()
        _state_path(session_id).write_text(json.dumps(data, ensure_ascii=False))
    except Exception:
        log.debug("saving browser state failed", exc_info=True)
    try:
        await session.context.close()
    except Exception:
        log.debug("closing browser context failed", exc_info=True)


async def reset_session(session_id: str):
    """Close the context and forget its login, for a clean next visit."""
    session = _sessions.pop(session_id, None)
    if session is not None:
        try:
            await session.context.close()
        except Exception:
            log.debug("closing browser context failed", exc_info=True)
    try:
        _state_path(session_id).unlink(missing_ok=True)
    except Exception:
        log.debug("clearing browser state failed", exc_info=True)


async def reap_idle():
    """Close contexts nobody has used lately. Chromium is not free to keep."""
    now = time.monotonic()
    for session_id, session in list(_sessions.items()):
        if now - session.touched > IDLE_TIMEOUT_SEC and not session.lock.locked():
            await close_session(session_id)


async def close_all():
    global _playwright, _browser
    for session_id in list(_sessions):
        await close_session(session_id)
    if _browser is not None:
        try:
            await _browser.close()
        except Exception:
            log.debug("closing browser process failed", exc_info=True)
        _browser = None
    if _playwright is not None:
        try:
            await _playwright.stop()
        except Exception:
            log.debug("stopping playwright failed", exc_info=True)
        _playwright = None


# ── Capture ─────────────────────────────────────────────────────────────────

def _frame_path(session_id: str, index: int) -> Path:
    stamp = time.strftime("%H%M%S")
    safe = "".join(c for c in session_id if c.isalnum())[:8] or "s"
    return CAPTURE_DIR / f"{safe}_{stamp}_{index:03d}.png"


_counters: dict[str, int] = {}


# Prefixes that read naturally off an accessibility snapshot but are not
# selector engines Playwright's string parser knows. `role=` and `text=` are
# native; these are not, and `label=Email` came back as
# 'Unknown engine "label"' rather than doing the obvious thing.
_BY_METHOD = {
    "label": "get_by_label",
    "placeholder": "get_by_placeholder",
    "testid": "get_by_test_id",
    "alt": "get_by_alt_text",
    "title": "get_by_title",
    "name": "get_by_label",
}


def locate(page, at: str):
    """Resolve a target string to a locator.

    The snapshot shows roles and names, so those are what a model reaches for.
    Anything not handled here falls through to Playwright, which covers CSS,
    XPath, `text=`, `role=` and `>>` chaining.
    """
    prefix, sep, rest = at.partition("=")
    method = _BY_METHOD.get(prefix.strip().lower()) if sep else None
    if method:
        value = rest.strip()
        exact = False
        # Quoted means exact, matching how `role=...[name="x"]` reads.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value, exact = value[1:-1], True
        return getattr(page, method)(value, exact=exact)
    return page.locator(at)



async def capture(
    session: Session, session_id: str, *, at: str = "", full_page: bool = False
) -> tuple[str, bytes]:
    """Screenshot the page, or one element of it. Always written to disk."""
    _counters[session_id] = _counters.get(session_id, 0) + 1
    path = _frame_path(session_id, _counters[session_id])
    target = locate(session.page, at) if at else session.page
    kwargs = {} if at else {"full_page": full_page}
    try:
        data = await target.screenshot(**kwargs)
    except Exception as e:
        raise BrowserError(f"could not capture{f' {at}' if at else ''}: {_brief(e)}") from e
    path.write_bytes(data)
    return str(path), data


async def snapshot(session: Session, at: str = "") -> str:
    """The accessibility tree, as YAML.

    This is what makes the tool usable by a model that has never seen the page:
    rather than guessing a CSS selector and failing twice, it reads the roles
    and names here and addresses them directly as `role=button[name="Save"]`.
    """
    locator = locate(session.page, at) if at else session.page.locator("body")
    try:
        return await locator.aria_snapshot()
    except Exception as e:
        raise BrowserError(f"could not snapshot {at or 'body'}: {_brief(e)}") from e


def _brief(e: Exception) -> str:
    """Playwright errors carry a long call log; the first lines are the fact."""
    text = str(e).strip()
    lines = [line for line in text.splitlines() if line.strip()]
    head = []
    for line in lines:
        if line.lstrip().startswith(("Call log:", "- waiting", "-   ", "at ")):
            break
        head.append(line.strip())
        if len(head) >= 3:
            break
    return " ".join(head) or text[:200]
