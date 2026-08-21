"""FastAPI application entry point."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from agent_server import agent
from agent_server import database as db
from agent_server.config import DATA_DIR, DB_PATH
from agent_server.database import close as close_db
from agent_server.database import init_db
from agent_server.providers import load_custom_endpoint_providers
from agent_server.routes import chat, files, pages, sessions, settings, tts
from agent_server.system_prompt import ensure_home_session
from agent_server.templating import STATIC_DIR

log = logging.getLogger(__name__)

WELCOME = (
    "Hi! I'm your coding assistant. You can just talk to me in plain English — "
    "tell me what you'd like to build, and I'll start a project for it and get to work. "
    "You don't need to know anything technical. If you're not sure where to begin, "
    "just ask me a question or describe an idea and we'll figure it out together."
)

CHILD_WELCOME = (
    "Hi there! I'm your coding buddy. Tell me what you'd like to make — a game, a "
    "website, or a story — and we'll build it together and I'll teach you how it works "
    "along the way. I can also help with your homework. What are you excited to create?"
)


async def _reap_browsers():
    from agent_server import browser

    while True:
        await asyncio.sleep(120)
        try:
            await browser.reap_idle()
        except Exception:
            log.warning("reaping idle browsers failed", exc_info=True)


async def _discover_models():
    """Ask each provider that can list models what it currently offers.

    Best-effort and never fatal: the curated catalogue in `config` is what the
    picker shows, and this only widens the set of ids the app will accept, so a
    model released after this version shipped still works if something selects it.
    """
    from agent_server import config
    from agent_server.providers import get_provider

    for key in ("deepseek", "gemini"):
        try:
            provider = get_provider(key)
            if not provider.has_credentials():
                continue
            live = await provider.fetch_model_ids()
            if not live:
                continue
            config.register_dynamic_models(key, live)
            # A curated id the provider no longer serves is a 404 the user
            # meets by picking it out of a menu we drew for them. Say so here,
            # where it is one line in the log, rather than there.
            gone = [
                m["id"] for m in config.MODELS
                if m["provider"] == key and m["id"] not in live
            ]
            if gone:
                log.warning("%s no longer offers: %s", key, ", ".join(gone))
        except Exception:
            log.warning("%s model discovery failed", key, exc_info=True)


async def _warm_whisper():
    from agent_server import stt as stt_service
    from agent_server import whisper_streaming

    # The portable fallback loads (and on first run downloads) its own model, so
    # warm it here rather than making the user wait on their first sentence.
    await stt_service.warmup()

    if not whisper_streaming.whisper_streaming_available():
        return
    try:
        await whisper_streaming.get_server()
        log.info("whisper-server ready for streaming dictation")
    except Exception:
        log.warning("whisper-server warm-up failed", exc_info=True)


async def _warm_tts():
    from agent_server import tts as tts_service

    try:
        await tts_service.warmup()
        if tts_service.availability()["available"]:
            log.info("text-to-speech model ready")
    except Exception:
        log.warning("text-to-speech warm-up failed", exc_info=True)


async def _seed_home():
    """Create the home sessions, greet the user, and flag missing pieces once."""
    home = await ensure_home_session()
    if not await db.get_messages(home["id"]):
        await db.add_message(home["id"], "assistant", WELCOME)

        from agent_server import setup

        missing = setup.missing()
        if missing:
            # Names only. The commands that install each one are in this
            # assistant's own instructions, where they belong -- reading a shell
            # command aloud to somebody who cannot see the screen is noise, and
            # it is not a thing they were ever going to type.
            lines = [
                "One quick note: I had a look at this computer, and a couple of parts "
                "aren't set up yet:",
            ]
            for component in missing:
                lines.append(f"- {component['name']}")
            lines.append(
                "Everything else works without them. Just say the word and I'll install "
                "whichever ones you want."
            )
            await db.add_message(home["id"], "assistant", "\n".join(lines))

    from agent_server.config import CHILD_HOME_SESSION_ID

    child = await db.get_session(CHILD_HOME_SESSION_ID)
    if child and not await db.get_messages(child["id"]):
        await db.add_message(child["id"], "assistant", CHILD_WELCOME)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from agent_server.logging_setup import configure

    configure()
    log.info("assistant starting: data=%s db=%s", DATA_DIR, DB_PATH.name)
    await init_db()
    from agent_server.providers import credentials

    all_settings = await db.get_all_settings()
    credentials.prime(all_settings)
    # The dictation model size lives in `settings`; prime the synchronous
    # accessor with it before anything asks which model to load.
    from agent_server import config as _config

    _config.set_whisper_size(all_settings.get('whisper_size', ''))
    await load_custom_endpoint_providers()
    await _discover_models()
    await _seed_home()

    reaper = asyncio.create_task(_reap_browsers())
    whisper_warmup = asyncio.create_task(_warm_whisper())
    tts_warmup = asyncio.create_task(_warm_tts())

    yield

    reaper.cancel()
    whisper_warmup.cancel()
    tts_warmup.cancel()
    from agent_server import preview, whisper_streaming
    from agent_server.tools import browser

    await agent.shutdown()
    await whisper_streaming.shutdown()
    await browser.close_browser()
    await preview.close_all()
    await close_db()


app = FastAPI(title="Assistant", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(pages.router)
app.include_router(chat.router)
app.include_router(files.router)
app.include_router(sessions.router)
app.include_router(tts.router)
app.include_router(settings.router)
