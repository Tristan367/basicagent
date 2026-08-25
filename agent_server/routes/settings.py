"""The one settings page: API keys, the default model, and a few preferences."""

import asyncio
import logging
import os
import sys
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from agent_server import database as db
from agent_server import imagegen, parental
from agent_server.model_catalog import any_credentials
from agent_server.providers import (
    get_provider,
    get_provider_settings_fields,
    load_custom_endpoint_providers,
)

router = APIRouter()
log = logging.getLogger(__name__)

# Strong references to fire-and-forget tasks, so they are not garbage
# collected mid-flight.
_background: set = set()


@router.post("/_settings")
async def save_settings(request: Request):
    form = await request.form()
    if await parental.child_mode_enabled():
        password = str(form.get("parent_password", "")).strip()
        if not await parental.parent_password_correct(password):
            return RedirectResponse("/settings?error=locked", status_code=303)
    for ps in get_provider_settings_fields():
        changed = False
        for f in ps["fields"]:
            if f["key"] not in form:
                continue
            value = str(form.get(f["key"], "")).strip()
            # An empty password box means "leave the saved key alone", because
            # the box is deliberately rendered empty rather than pre-filled.
            # (It used to be pre-filled with dots and skipped if a dot came
            # back -- which silently discarded a key typed on the end of them.)
            if f.get("kind") == "password" and not value:
                continue
            await db.set_setting(f["key"], value)
            changed = True
        if changed:
            get_provider(ps["key"]).invalidate_key_cache()
            # A new key may bring picture models with it, and the list of those
            # is cached for a quarter of an hour. Somebody who has just pasted
            # a key in should not have to wait that out to be told they can
            # draw now.
            imagegen.forget_catalogue()

    model = str(form.get("default_model", "")).strip()
    if model:
        await db.set_setting("default_model", model)
    await _repoint_home()
    return RedirectResponse("/settings", status_code=303)


async def _repoint_home() -> None:
    """Move the home assistant onto whatever the user can now reach.

    The home session's provider is decided when the session is built, and the
    repair that moves it off a provider with no key ran only at startup. So the
    first key of a fresh install was saved, accepted, and ignored: the user went
    back to the front page, said hello, and was told "No API key is set up yet.
    Add one in Settings to get started." -- which is the step they had just
    finished. Nothing on screen said to restart the app, and nobody this app is
    for would think of it.

    Cheap and idempotent, and it only ever moves a session that cannot work
    where it is, so a deliberate choice is never overridden.
    """
    from agent_server.system_prompt import ensure_home_session

    await ensure_home_session()


@router.post("/_settings/custom_endpoint")
async def save_custom_endpoint(
    name: str = Form(""),
    base_url: str = Form(""),
    api_key: str = Form(""),
    parent_password: str = Form(""),
):
    if await parental.child_mode_enabled() and not await parental.parent_password_correct(
        parent_password.strip()
    ):
        return RedirectResponse("/settings?error=locked", status_code=303)
    name = name.strip()
    base_url = base_url.strip()
    api_key = api_key.strip()
    if not name or not base_url:
        return RedirectResponse("/settings?error=endpoint", status_code=303)
    if not base_url.startswith(("http://", "https://")):
        return RedirectResponse("/settings?error=endpoint_url", status_code=303)
    # A slash separates the endpoint from the model in a picker value, so a
    # name with one in it produces a menu entry that cannot be resolved back:
    # "my/box" splits into the endpoint "my", which does not exist, and every
    # message fails. Refused here rather than mangled, because the name is how
    # the user recognises their own machine.
    if "/" in name:
        return RedirectResponse("/settings?error=endpoint_name", status_code=303)

    # What actually arrived over the wire, so this never has to be guessed at
    # again. Lengths and the equality, never the key itself.
    log.info(
        "custom endpoint save name=%r url_len=%d key_len=%d key_is_url=%s",
        name, len(base_url), len(api_key), api_key == base_url,
    )

    # An exact copy of the address in the key box is never a key someone meant
    # to save, whoever put it there -- a paste into the wrong box, or a browser
    # filling a field it decided was a login. Refusing it is not the old "that
    # looks like a web address" guess, which read the *shape* of a value and
    # blocked a perfectly good key: two fields of one submission being
    # byte-identical has no legitimate reading.
    if api_key and api_key == base_url:
        return RedirectResponse("/settings?error=key_is_address", status_code=303)

    # The key box shows the ends of the saved key rather than its value, so
    # "nothing typed" means keep what is there. To remove a key, remove the
    # endpoint -- an endpoint without its key is not a thing anyone wants.
    if not api_key:
        existing = await db.get_custom_endpoint(name)
        api_key = existing["api_key"] if existing else ""

    # Say whether it actually works, rather than guessing from the shape of
    # what was typed. Asking the endpoint is both certain and more useful: it
    # catches a wrong key, a wrong address, and a server that is not running.
    # The same answer carries the model id, so nobody has to type that either.
    status, models = await _check_endpoint(base_url, api_key)
    await db.save_custom_endpoint(name, base_url, api_key,
                                  models[0] if models else "", models)
    await load_custom_endpoint_providers()
    await _repoint_home()
    return RedirectResponse(f"/settings?checked={status}", status_code=303)


async def _check_endpoint(base_url: str, api_key: str) -> tuple[str, list[str]]:
    """Ask an endpoint for its model list.

    Returns a short status word and every model id it reports. A box serving
    one is named by the endpoint; a box serving several offers them all in the
    picker. Either way nothing is typed.
    """
    import httpx

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(f"{base_url.rstrip('/')}/models", headers=headers)
    except httpx.HTTPError:
        return "unreachable", []
    # The real status, not a verdict. A 401 usually means the key, but some
    # servers answer that way to any path they do not recognise, so saying
    # "the key is wrong" outright sends people hunting for the wrong problem.
    if response.status_code in (401, 403):
        return f"auth{response.status_code}", []
    if response.status_code >= 400:
        return f"http{response.status_code}", []
    try:
        rows = response.json().get("data", [])
    except ValueError:
        return "error", []
    models = [str(r["id"]) for r in rows if r.get("id")]
    return f"ok{len(models)}", models


@router.post("/_settings/custom_endpoint/delete")
async def delete_custom_endpoint(name: str = Form(""), parent_password: str = Form("")):
    if await parental.child_mode_enabled() and not await parental.parent_password_correct(
        parent_password.strip()
    ):
        return RedirectResponse("/settings?error=locked", status_code=303)
    if name.strip():
        await db.delete_custom_endpoint(name.strip())
        await load_custom_endpoint_providers()
        # Removing the endpoint the home assistant was running on leaves it
        # pointing at a provider that no longer exists.
        await _repoint_home()
    return RedirectResponse("/settings", status_code=303)


def _checkbox(form, name: str) -> bool | None:
    """Whether a checkbox is ticked, or None if this form did not carry it.

    A browser omits an unticked checkbox entirely, so "absent" and "off" look
    identical and `name in form` reads both as "leave it alone" -- every
    checkbox on the settings page could be switched on and never off again.
    Each one is therefore paired with a hidden field of the same name, so the
    field is always submitted and its presence means "this form owns this
    setting". Order is not relied on: the tick is whichever value says on.
    """
    values = [str(v) for v in form.getlist(name)]
    if not values:
        return None
    return "on" in values


def _local_referer(request: Request) -> str:
    """The page the form was on, if it was a page of this app; else home."""
    from urllib.parse import urlparse

    referer = request.headers.get("referer") or ""
    try:
        parsed = urlparse(referer)
    except ValueError:
        return "/"
    if (parsed.scheme or parsed.netloc) and parsed.netloc != request.url.netloc:
        return "/"
    path = parsed.path or "/"
    if not path.startswith("/") or path.startswith("//"):
        return "/"
    return path + (f"?{parsed.query}" if parsed.query else "")


def _number(raw: str, low: float, high: float) -> str | None:
    """A slider value, clamped, or None if it is not a number at all.

    These went into the database as whatever text arrived. A value of "abc" or
    "1e999" then made `float()` raise (or produce infinity) in the read-aloud
    status endpoint, which 500'd on every request from then on -- read-aloud
    broken for good, with no way to fix it from a page that never offered the
    bad value in the first place.
    """
    try:
        value = float(raw.strip())
    except (TypeError, ValueError):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return f"{min(high, max(low, value)):g}"


@router.post("/_settings/prefs")
async def save_prefs(request: Request):
    form = await request.form()
    if "theme" in form and str(form.get("theme", "")) in ("light", "dark"):
        await db.set_setting("theme", str(form.get("theme")))
    if "accent" in form:
        accent = str(form.get("accent", "")).strip()
        if accent.startswith("#") and len(accent) == 7:
            await db.set_setting("accent", accent)
        else:
            await db.delete_setting("accent")
    if "welcome_seen" in form:
        await db.set_setting("welcome_seen", "1" if form.get("welcome_seen") == "on" else "0")
    for key in ("stt_enabled", "tts_auto", "sound_cues", "sound_ticks", "uses_screen_reader"):
        ticked = _checkbox(form, key)
        if ticked is not None:
            await db.set_setting(key, "1" if ticked else "0")
    if (voice := str(form.get("tts_voice", "")).strip()):
        await db.set_setting("tts_voice", voice)
    for key, low, high in (
        ("tts_speed", 0.5, 2.0), ("tts_volume", 0.0, 1.0), ("sound_volume", 0.0, 1.0),
        # The browser applies and remembers this itself; the copy here exists so
        # the assistant can be asked to make the writing bigger. Same bounds as
        # `__applyZoom`, or the two would disagree about what was set.
        ("zoom", 0.7, 1.6),
    ):
        if key in form:
            value = _number(str(form.get(key, "")), low, high)
            if value is not None:
                await db.set_setting(key, value)
    if (size := str(form.get("whisper_size", "")).strip()):
        from agent_server import config
        from agent_server import stt as stt_service

        if config.set_whisper_size(size):
            await db.set_setting("whisper_size", size)
            # Drop the loaded model so the next sentence uses the new one; it
            # reloads (and downloads, the first time) in the background rather
            # than making this request wait on it.
            await stt_service.reload_model()
            task = asyncio.create_task(stt_service.warmup())
            _background.add(task)
            task.add_done_callback(_background.discard)
    # Back where they came from -- but only inside this app. The Referer is
    # attacker-settable, and handing it straight to a redirect will send the
    # user anywhere at all.
    return RedirectResponse(_local_referer(request), status_code=303)


# ── Child mode / parental controls ──────────────────────────────────────────


async def _body(request: Request) -> dict:
    """The JSON body as a dict, whatever arrived.

    `await request.json()` returns whatever the body parsed to -- a list, a
    string, a number, null -- and calling .get() on any of those raised, so a
    malformed request crashed the endpoint rather than being refused.
    """
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


@router.get("/api/child/status")
async def child_status():
    return {
        "enabled": await parental.child_mode_enabled(),
        "has_key": any_credentials(),
        "has_password": bool(await db.get_setting("parent_password_hash", "")),
        "override_remaining": await parental.override_remaining(),
        "override_elapsed": await parental.override_elapsed(),
    }


async def _close_previews():
    """Shut every open project window when child mode is switched either way.

    A preview window decides whether it is confined at the moment it opens. Turn
    child mode on with one already open and it stays as it was -- an unconfined
    browser, sitting on the screen the child is about to be handed. Closing them
    is the whole fix: the next Play opens under whichever rules now apply.

    Best-effort. Failing to close a window must not fail switching modes, which
    is the more important of the two.
    """
    import contextlib

    from agent_server import preview

    with contextlib.suppress(Exception):
        await preview.close_all()


@router.post("/api/child/enable")
async def child_enable(request: Request):
    data = await _body(request)
    password = str(data.get("password", "")).strip()
    if len(password) < 4:
        return {"ok": False, "reason": "password"}
    if not any_credentials():
        return {"ok": False, "reason": "no_key"}
    await db.set_setting("parent_password_hash", parental.hash_password(password))
    await db.set_setting("child_mode", "1")
    await db.delete_setting("child_override_until")
    await _close_previews()
    return {"ok": True}


@router.post("/api/child/disable")
async def child_disable(request: Request):
    data = await _body(request)
    password = str(data.get("password", "")).strip()
    if not await parental.parent_password_correct(password):
        return {"ok": False, "reason": "password"}
    await db.set_setting("child_mode", "0")
    await db.delete_setting("parent_password_hash")
    await db.delete_setting("child_override_until")
    await _close_previews()
    return {"ok": True}


@router.post("/api/child/reset")
async def child_reset(request: Request):
    """Set a fresh parent password after a forgot-password timer has elapsed.

    Child mode stays on — the parent just takes back control with a new password.
    """
    data = await _body(request)
    password = str(data.get("password", "")).strip()
    if not await parental.override_elapsed():
        return {"ok": False, "reason": "waiting"}
    if len(password) < 4:
        return {"ok": False, "reason": "password"}
    await db.set_setting("parent_password_hash", parental.hash_password(password))
    await db.delete_setting("child_override_until")
    return {"ok": True}


@router.post("/api/child/forgot")
async def child_forgot():
    await db.set_setting("child_override_until", str(int(time.time()) + parental.OVERRIDE_SECONDS))
    return {"ok": True, "override_remaining": parental.OVERRIDE_SECONDS}


@router.post("/api/child/verify")
async def child_verify(request: Request):
    data = await _body(request)
    return {"ok": await parental.parent_password_correct(str(data.get("password", "")).strip())}


@router.post("/api/child/note/save")
async def child_note_save(request: Request):
    data = await _body(request)
    if await parental.child_mode_enabled() and not await parental.parent_password_correct(
        str(data.get("password", "")).strip()
    ):
        return {"ok": False, "reason": "password"}
    note = str(data.get("note", ""))
    if len(note) > parental.NOTE_MAX_CHARS:
        return {"ok": False, "reason": "too_long"}
    await parental.set_parent_note(note)
    return {"ok": True, "saved": bool(note.strip())}


@router.get("/api/theme")
async def get_theme():
    """Everything the open page can change about itself without reloading.

    Still called `theme` because that is all it carried at first. The list grew
    when the assistant was given the settings page to work: it can now be asked
    to turn read-aloud on or make the text bigger, and a change the user has to
    reload to see is not a change they asked for. The chat re-reads this at the
    end of every turn and applies whatever moved.
    """
    from agent_server.config import APP_VERSION, DEFAULT_THEME, contrast_text

    return {
        "theme": await db.get_setting("theme", DEFAULT_THEME),
        "version": APP_VERSION,
        "zoom": await db.get_setting("zoom", ""),
        "accent": await db.get_setting("accent", ""),
        # Worked out here rather than in the browser: getting the contrast wrong
        # makes the user's own messages unreadable, and one implementation of it
        # is enough.
        "accent_text": (contrast_text(accent)
                        if (accent := await db.get_setting("accent", "")) else ""),
        "tts_auto": await db.get_setting("tts_auto", "0") == "1",
        "tts_voice": await db.get_setting("tts_voice", ""),
        "tts_speed": await db.get_setting("tts_speed", "1.25"),
        "tts_volume": await db.get_setting("tts_volume", "0.75"),
        "sound_cues": await db.get_setting("sound_cues", "1") == "1",
        "sound_ticks": await db.get_setting("sound_ticks", "0") == "1",
        "sound_volume": await db.get_setting("sound_volume", "0.4"),
        "stt_enabled": await db.get_setting("stt_enabled", "1") == "1",
        "child_mode": await parental.child_mode_enabled(),
    }


# ── Quit / restart ─────────────────────────────────────────────────────────


def _server_args() -> tuple[str, str]:
    host, port = "127.0.0.1", "8220"
    for i, arg in enumerate(sys.argv):
        if arg == "--host" and i + 1 < len(sys.argv):
            host = sys.argv[i + 1]
        elif arg == "--port" and i + 1 < len(sys.argv):
            port = sys.argv[i + 1]
    return host, port


async def _graceful_shutdown():
    from agent_server import agent, whisper_streaming
    from agent_server.database import close as close_db
    from agent_server.tools import browser

    await agent.shutdown()
    await whisper_streaming.shutdown()
    await browser.close_browser()
    await close_db()


_background: set = set()


def _spawn(coro):
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


@router.post("/api/quit")
async def quit_app():
    from agent_server.config import QUIT_SIGNAL

    try:
        QUIT_SIGNAL.touch()
    except OSError:
        pass

    async def _quit():
        await asyncio.sleep(0.4)
        try:
            await _graceful_shutdown()
        except Exception:
            pass
        os._exit(0)

    _spawn(_quit())
    return {"ok": True}


@router.post("/api/restart")
async def restart_app():
    async def _restart():
        await asyncio.sleep(0.4)
        try:
            await _graceful_shutdown()
        except Exception:
            pass
        host, port = _server_args()
        os.execv(
            sys.executable,
            [sys.executable, "-m", "uvicorn", "agent_server.main:app",
             "--host", host, "--port", str(port)],
        )

    _spawn(_restart())
    return {"ok": True}
