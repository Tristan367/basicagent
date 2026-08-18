"""Standalone Chromium app window, so the app feels like a native application.

Reuses the Playwright-managed Chromium already used by the `browser` tool, so
there is no extra dependency. A persistent profile under the data directory
keeps cookies and settings between launches. Blocks until the window is closed,
which is the signal the launcher uses to stop the server.
"""

import asyncio
import subprocess
import sys as _sys

from agent_server.config import DATA_DIR, QUIT_SIGNAL

APP_PROFILE_DIR = DATA_DIR / "app_profile"


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
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(APP_PROFILE_DIR),
            headless=False,
            args=[f"--app={url}", "--disable-features=Translate"],
            no_viewport=True,
        )
        page = context.pages[0] if context.pages else await context.new_page()

        # External links (target="_blank", markdown links, "get a key" links)
        # open a new page in the app window. Send them to the real browser and
        # close the empty tab instead.
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
