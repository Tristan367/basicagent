"""Standalone Chromium app window, so the app feels like a native application.

Reuses the Playwright-managed Chromium already used by the `browser` tool, so
there is no extra dependency. A persistent profile under the data directory
keeps cookies and settings between launches. Blocks until the window is closed,
which is the signal the launcher uses to stop the server.
"""

import asyncio
import contextlib
import subprocess
import sys as _sys

from agent_server.config import DATA_DIR, QUIT_SIGNAL

APP_PROFILE_DIR = DATA_DIR / "app_profile"

# What the window is, told to the page before any of its own script runs. Two
# things depend on knowing: external links have to be handed to the real
# browser rather than opened in here, and the app's own Quit button is a second
# close button beside the one Windows draws on the frame.
IN_APP = "window.__inApp = true; document.documentElement.classList.add('in-app');"


def _settle_profile() -> None:
    """Turn off Chromium's offers before it has a chance to make one.

    Saving a Gemini key made Chromium offer to remember it as a password --
    which is a browser asking to store an API key in a password manager the
    user does not know they have, inside an app that is not supposed to look
    like a browser at all. There is no launch flag that reliably stops it; the
    setting lives in the profile, so the profile is written before Chromium
    opens it.

    Merged rather than overwritten: the file also holds window position, zoom
    and everything else Chromium remembers between launches.
    """
    import json

    prefs = APP_PROFILE_DIR / "Default" / "Preferences"
    prefs.parent.mkdir(parents=True, exist_ok=True)
    current = {}
    if prefs.is_file():
        try:
            current = json.loads(prefs.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = {}
    profile = current.setdefault("profile", {})
    profile["password_manager_enabled"] = False
    profile["password_manager_leak_detection"] = False
    current["credentials_enable_service"] = False
    autofill = current.setdefault("autofill", {})
    autofill["credit_card_enabled"] = False
    autofill["profile_enabled"] = False
    try:
        prefs.write_text(json.dumps(current), encoding="utf-8")
    except OSError:
        pass


def _open_external(url: str) -> None:
    """Open a URL in the user's real browser, not the app's Chromium window."""
    url = (url or "").strip()
    if not url or url in ("about:blank", "chrome://", "about:"):
        return
    try:
        if _sys.platform == "darwin":
            subprocess.Popen(["open", url])
        elif _sys.platform.startswith("win"):
            import os

            os.startfile(url)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", url])
    except Exception:
        pass


async def _open(url: str) -> None:
    from playwright.async_api import async_playwright

    # Clear any stale quit marker from a previous run.
    try:
        QUIT_SIGNAL.unlink()
    except OSError:
        pass

    APP_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    _settle_profile()
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(APP_PROFILE_DIR),
            headless=False,
            # Dictation is the whole interface for somebody who cannot use a
            # keyboard, and without this getUserMedia was refused before any
            # prompt could be shown -- so pressing Talk did nothing at all, on
            # every fresh install, with no error anywhere.
            permissions=["microphone"],
            args=[
                f"--app={url}",
                "--disable-features=Translate,AutofillServerCommunication",
                # Belt and braces with the profile written above.
                "--disable-save-password-bubble",
                # Publish the accessibility tree unconditionally, so a screen
                # reader works the moment it is switched on. Chromium otherwise
                # waits until it detects assistive technology, which is reliable
                # on Windows and macOS and historically flaky against AT-SPI on
                # Linux. Asking the user to pass a flag is not an option -- the
                # people who need this are the least likely to know it exists,
                # and the cost is a little memory on one small page.
                "--force-renderer-accessibility",
            ],
            no_viewport=True,
        )
        await context.add_init_script(IN_APP)
        page = context.pages[0] if context.pages else await context.new_page()
        # The page above already exists, so its init script has not run.
        with contextlib.suppress(Exception):
            await page.evaluate(IN_APP)

        # External links (target="_blank", markdown links, "get a key" links)
        # open a new page in the app window. Send them to the real browser and
        # close the empty tab instead.
        #
        # A fallback, not the mechanism. Opening a Chromium window and closing
        # it again is visible -- somebody clicking "Open Google AI Studio" saw
        # a second window appear, sit there, and vanish before their real
        # browser opened, which looks like a fault. The page itself now hands
        # these to the server before a window is ever made. This stays for
        # anything that gets past it: a link inside a preview, a window.open
        # from something the assistant built.
        open_tasks = set()

        def on_new_page(new_page):
            async def handle():
                try:
                    await new_page.wait_for_load_state("domcontentloaded", timeout=4000)
                except Exception:
                    pass
                _open_external(new_page.url)
                try:
                    await new_page.close()
                except Exception:
                    pass

            task = asyncio.create_task(handle())
            open_tasks.add(task)
            task.add_done_callback(open_tasks.discard)

        context.on("page", on_new_page)

        closed = asyncio.Event()
        page.on("close", lambda _=None: closed.set())

        async def watch_quit():
            while not closed.is_set():
                if QUIT_SIGNAL.exists():
                    return
                await asyncio.sleep(0.3)

        signal_task = asyncio.create_task(watch_quit())
        close_task = asyncio.create_task(closed.wait())
        # Close when the user closes the window, or the server asks us to.
        await asyncio.wait(
            [signal_task, close_task], return_when=asyncio.FIRST_COMPLETED
        )
        for task in (signal_task, close_task):
            task.cancel()
        await asyncio.gather(signal_task, close_task, return_exceptions=True)
        await context.close()


def launch(url: str) -> None:
    try:
        asyncio.run(_open(url))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    if len(_sys.argv) < 2:
        print("usage: python -m agent_server.desktop <url>", file=_sys.stderr)
        raise SystemExit(1)
    launch(_sys.argv[1])
