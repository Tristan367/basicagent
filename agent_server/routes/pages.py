"""The home (manager) chat, project chats, and the settings page."""

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from agent_server import database as db
from agent_server import parental
from agent_server.routes.context import _chat_context, _settings_context
from agent_server.templating import templates

router = APIRouter()


@router.get("/")
async def index(request: Request):
    session = await db.get_session(await parental.current_home_id())
    if session is None:
        return RedirectResponse("/settings", status_code=303)
    return templates.TemplateResponse(
        request=request, name="chat.html", context=await _chat_context(session)
    )


@router.get("/sessions/{session_id}")
async def session_page(request: Request, session_id: str):
    session = await db.get_session(session_id)
    if session is None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request=request, name="chat.html", context=await _chat_context(session)
    )


@router.get("/settings")
async def settings_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="settings.html", context=await _settings_context()
    )
