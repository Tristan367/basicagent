"""The one settings page: API keys, the default model, and a few preferences."""

import asyncio
import os
import sys
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from agent_server import database as db
from agent_server import parental
from agent_server.model_catalog import any_credentials
from agent_server.providers import (
    get_provider,
    get_provider_settings_fields,
    load_custom_endpoint_providers,
)

router = APIRouter()


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
            if f.get("kind") == "password" and "\u2022" in value:
                continue
            await db.set_setting(f["key"], value)
            changed = True
        if changed:
            get_provider(ps["key"]).invalidate_key_cache()

    model = str(form.get("default_model", "")).strip()
    if model:
        await db.set_setting("default_model", model)
        await db.set_setting("custom_model_id", str(form.get("custom_model_id", "")).strip())
    return RedirectResponse("/settings", status_code=303)


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
    if not name or not base_url:
        return RedirectResponse("/settings?error=endpoint", status_code=303)
    if api_key and "\u2022" in api_key:
        existing = await db.get_custom_endpoint(name)
        api_key = existing["api_key"] if existing else ""
    await db.save_custom_endpoint(name, base_url, api_key)
    await load_custom_endpoint_providers()
    return RedirectResponse("/settings", status_code=303)


@router.post("/_settings/custom_endpoint/delete")
async def delete_custom_endpoint(name: str = Form(""), parent_password: str = Form("")):
    if await parental.child_mode_enabled() and not await parental.parent_password_correct(
        parent_password.strip()
    ):
        return RedirectResponse("/settings?error=locked", status_code=303)
    if name.strip():
        await db.delete_custom_endpoint(name.strip())
        await load_custom_endpoint_providers()
    return RedirectResponse("/settings", status_code=303)


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
    if "stt_enabled" in form:
        await db.set_setting("stt_enabled", "1" if form.get("stt_enabled") == "on" else "0")
    if "tts_auto" in form:
        await db.set_setting("tts_auto", "1" if form.get("tts_auto") == "on" else "0")
    if (voice := str(form.get("tts_voice", "")).strip()):
        await db.set_setting("tts_voice", voice)
    if (speed := str(form.get("tts_speed", "")).strip()):
        await db.set_setting("tts_speed", speed)
    if (volume := str(form.get("tts_volume", "")).strip()):
        await db.set_setting("tts_volume", volume)
    return RedirectResponse(request.headers.get("referer") or "/", status_code=303)


# ── Child mode / parental controls ──────────────────────────────────────────


@router.get("/api/child/status")
async def child_status():
    return {
        "enabled": await parental.child_mode_enabled(),
        "has_key": any_credentials(),
        "has_password": bool(await db.get_setting("parent_password_hash", "")),
        "override_remaining": await parental.override_remaining(),
        "override_elapsed": await parental.override_elapsed(),
    }


@router.post("/api/child/enable")
async def child_enable(request: Request):
    data = await request.json()
    password = str(data.get("password", "")).strip()
    if len(password) < 4:
        return {"ok": False, "reason": "password"}
    if not any_credentials():
        return {"ok": False, "reason": "no_key"}
    await db.set_setting("parent_password_hash", parental.hash_password(password))
    await db.set_setting("child_mode", "1")
    await db.delete_setting("child_override_until")
    return {"ok": True}


@router.post("/api/child/disable")
async def child_disable(request: Request):
    data = await request.json()
    password = str(data.get("password", "")).strip()
    if not await parental.parent_password_correct(password):
        return {"ok": False, "reason": "password"}
    await db.set_setting("child_mode", "0")
    await db.delete_setting("parent_password_hash")
    await db.delete_setting("child_override_until")
    return {"ok": True}


@router.post("/api/child/reset")
async def child_reset(request: Request):
    """Set a fresh parent password after a forgot-password timer has elapsed.

    Child mode stays on — the parent just takes back control with a new password.
    """
    data = await request.json()
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
    data = await request.json()
    return {"ok": await parental.parent_password_correct(str(data.get("password", "")).strip())}


@router.get("/api/theme")
async def get_theme():
    from agent_server.config import APP_VERSION, DEFAULT_THEME

    return {
        "theme": await db.get_setting("theme", DEFAULT_THEME),
        "version": APP_VERSION,
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
