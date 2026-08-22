"""The home (manager) chat, project chats, and the settings page."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from agent_server import database as db
from agent_server import parental
from agent_server.routes.context import _chat_context, _settings_context
from agent_server.templating import templates

router = APIRouter()


@router.get("/")
async def index(request: Request):
    session = await db.get_session(await parental.current_home_id())
    if session is None:
        # Build it rather than bounce. The home chat is the front door, and if
        # it is missing -- deleted, or lost with a half-restored database --
        # redirecting to Settings made every route lead to Settings and
        # nothing lead back, until someone thought to restart the app. Nobody
        # who needs this app would think to restart the app.
        from agent_server.system_prompt import ensure_home_session

        await ensure_home_session()
        session = await db.get_session(await parental.current_home_id())
        if session is None:
            return RedirectResponse("/settings", status_code=303)
    return templates.TemplateResponse(
        request=request, name="chat.html", context=await _chat_context(session)
    )


@router.get("/sessions/{session_id}")
async def session_page(request: Request, session_id: str):
    session = await db.get_session(session_id)
    # A child following a remembered address lands back home rather than in a
    # parent's project. See `parental.may_reach`.
    if session is None or not await parental.may_reach(session):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request=request, name="chat.html", context=await _chat_context(session)
    )


@router.get("/sessions/{session_id}/body")
async def session_body(request: Request, session_id: str):
    """The conversation with no page around it.

    Fetched when the browser needs to rebuild the log -- after a turn it was not
    watching, or one it re-attached to. It renders the same template the page
    does, from the same context, because the alternative is a second renderer
    that drifts: there was one, and it drew the messages with none of the tool
    work or thinking between them, so finishing a turn made the account of that
    turn disappear.
    """
    session = await db.get_session(session_id)
    if session is None or not await parental.may_reach(session):
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse(
        request=request, name="chat_messages.html", context=await _chat_context(session)
    )


@router.get("/settings")
async def settings_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="settings.html", context=await _settings_context()
    )


@router.get("/settings/body")
async def settings_body(request: Request):
    """The Settings controls with no page around them.

    Fetched by the panel so Settings can open over a conversation instead of
    replacing it. The page at `/settings` renders the same template and stays
    exactly as it was: it is the one screen a user cannot afford to lose -- the
    API key lives there -- so it remains reachable even if the panel breaks.
    """
    return templates.TemplateResponse(
        request=request, name="settings_body.html", context=await _settings_context()
    )
