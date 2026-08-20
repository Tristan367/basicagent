"""Project session CRUD, used by the settings page and manager tools."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from agent_server import agent, parental
from agent_server import database as db
from agent_server.config import model_info, provider_for_model, split_custom_choice
from agent_server.model_catalog import effective_default_model, offerable_models

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
    "Content-Type": "text/event-stream; charset=utf-8",
}


async def _require(session_id: str) -> dict:
    session = await db.get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    return session


@router.get("")
async def list_sessions():
    return await db.list_sessions(profile=await parental.visible_profile())


@router.post("")
async def create_session(payload: dict):
    """Make a project without going through the assistant.

    The way this app is meant to work is that you say what you want and the
    Project Manager sets it up. This is the other way, for somebody who already
    knows what they are doing or already has a folder of code -- offered quietly
    rather than not at all, because having no way to do it is its own kind of
    rude.
    """
    from pathlib import Path as _Path

    from agent_server.config import PROJECTS_DIR
    from agent_server.tools.session_manager import _git_init, _slug

    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Give the project a name.")
    if len(name) > 120:
        raise HTTPException(400, "That name is too long.")

    profile = await parental.current_profile()
    raw_folder = str(payload.get("folder") or "").strip()

    if raw_folder:
        # Only outside child mode. A child pointing a project at any folder on
        # the machine is exactly what the separation exists to prevent, and the
        # option is not offered to them in the first place.
        if profile == "child":
            raise HTTPException(403, "Choosing a folder is not available in child mode.")
        folder = _Path(raw_folder).expanduser()
        try:
            folder = folder.resolve()
        except OSError as e:
            raise HTTPException(400, "That folder path could not be read.") from e
        if not folder.is_dir():
            raise HTTPException(400, "There is no folder at that path.")
        # A project rooted at home or at the filesystem root gives every tool in
        # the session the run of the machine, which is never what was meant.
        if folder == _Path.home() or folder.parent == folder:
            raise HTTPException(400, "Pick a folder inside your home directory, not the whole of it.")
    else:
        base = PROJECTS_DIR / "child" if profile == "child" else PROJECTS_DIR
        folder = base / _slug(name)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise HTTPException(500, f"Could not make a folder for it: {e}") from e

    await _git_init(folder)

    model = effective_default_model(await db.get_all_settings())
    if model.startswith("custom:"):
        endpoint, model_id = split_custom_choice(model)
        provider, model = endpoint, (model_id or endpoint)
    else:
        provider = provider_for_model(model)

    session = await db.create_session(
        name=name, project_dir=str(folder), provider=provider, model=model,
        kind="project", profile=profile,
    )
    return {"ok": True, "id": session["id"], "name": session["name"],
            "project_dir": session["project_dir"]}


@router.get("/status")
async def sessions_status():
    """Per-session activity for the Projects dropdown: is an AI working now, and
    what is the newest message, so the client can show working/unread dots."""
    sessions = await db.list_sessions(profile=await parental.visible_profile())
    last = await db.last_messages()
    return [
        {
            "id": s["id"],
            "name": s["name"],
            "running": agent.is_running(s["id"]),
            "last_message_id": (last.get(s["id"]) or {}).get("id", 0),
            "last_role": (last.get(s["id"]) or {}).get("role", ""),
        }
        for s in sessions
    ]


@router.get("/{session_id}")
async def get_session(session_id: str):
    return await _require(session_id)


@router.get("/{session_id}/models")
async def session_models(session_id: str):
    """The models the user can switch this session to, plus the switch cost."""
    session = await _require(session_id)
    usage = await db.get_session_usage(session_id)
    models = []
    for m in offerable_models():
        models.append({
            "id": m["id"],
            "name": m["name"],
            # Which account it comes through. OpenRouter resells most of what
            # the first-party keys offer, so the name alone does not identify
            # one -- "Claude Opus 5" can legitimately appear twice.
            "provider_label": m.get("provider_label", ""),
            "price_label": m["price_label"],
            "recommended": bool(m.get("recommended")),
            "price_in": m.get("price_in_miss", 0.0),
        })
    return {
        "current_model": session["model"],
        "context_tokens": usage["context"],
        "models": models,
    }


@router.post("/{session_id}/model")
async def switch_model(session_id: str, request: Request, payload: dict):
    """Switch the session's model.

    Whether to summarise the conversation first is decided by price: a raw
    switch re-uploads the whole context to the new model at its cache-miss
    rate, while compacting first re-summarises the older part on the current
    model and only uploads the summary plus recent tail. Whichever is cheaper
    wins, so a tiny conversation switches straight across and a large one
    moving to an expensive model is summarised first.

    The response is a stream so the client can say "Compacting conversation…"
    and then "Switching to <model>…" instead of sitting on a blank wait.
    """
    import logging

    log = logging.getLogger("agent_server.sessions")
    await _require(session_id)
    from agent_server import parental

    if await parental.child_mode_enabled() and not await parental.parent_password_correct(
        str(payload.get("parent_password", "")).strip()
    ):
        raise HTTPException(403, "Parent password required")
    # Not while it is working. Switching may summarise the conversation first,
    # and summarising rewrites the same messages the turn in flight is still
    # appending to -- and either way the reply arrives from the old model while
    # the session claims to be on the new one.
    if agent.is_running(session_id):
        raise HTTPException(
            409, "Wait for this project to finish what it is doing, then change the AI."
        )
    model = (payload.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "Model required")

    display_name = model_info(model).get("name", model)

    async def generator():
        from agent_server.compaction import compact_session, estimate_switch_costs

        plan = await estimate_switch_costs(session_id, model)
        log.info(
            "switch model session=%s model=%s compact=%s direct=$%.4f compact=$%.4f",
            session_id, model, plan["compact"], plan["direct_cost"], plan["compact_cost"],
        )

        if plan["compact"]:
            yield agent.sse({"type": "switch_status", "phase": "compacting"})
            try:
                result = await compact_session(session_id)
                if result and not result.get("ok"):
                    log.info("switch model compaction skipped: %s", result.get("reason"))
            except Exception as e:
                # Compaction is best-effort; switching still works without it.
                log.warning("switch model compaction failed: %s", e)

        yield agent.sse({"type": "switch_status", "phase": "switching", "name": display_name})

        if model.startswith("custom:"):
            # `custom:llm1` is the endpoint as a whole; `custom:llm1/some-model`
            # is one model on it. Either way the provider column holds the
            # endpoint, and nothing here was typed by the user.
            endpoint, model_id = split_custom_choice(model)
            await db.update_session(session_id, provider=endpoint,
                                    model=model_id or endpoint)
        else:
            await db.update_session(session_id, provider=provider_for_model(model), model=model)
        log.info("switch model done session=%s model=%s", session_id, model)
        yield agent.sse({"type": "switch_done", "model": model})

    return StreamingResponse(generator(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.patch("/{session_id}")
async def update_session(session_id: str, payload: dict):
    await _require(session_id)
    updates = {}
    if (name := (payload.get("name") or "").strip()):
        updates["name"] = name
    if "description" in payload:
        updates["description"] = (payload.get("description") or "").strip() or None
    if not updates:
        return await _require(session_id)
    return await db.update_session(session_id, **updates)


@router.delete("/{session_id}")
async def delete_session(session_id: str):
    session = await _require(session_id)
    # The home chat is the front door and is never offered in the list, so
    # nothing should be asking -- but deleting it left every route redirecting
    # to Settings with nothing leading back, so it is worth saying no out loud.
    if session.get("kind") == "manager":
        raise HTTPException(400, "The home chat cannot be deleted.")
    agent.request_abort(session_id)
    # Before the row goes: afterwards there is nothing left that knows this
    # project was running, and the server it started would hold its port until
    # the app is closed -- which the user has no way to do anything about.
    from agent_server import preview

    await preview.stop(session_id)
    await db.delete_session(session_id)
    agent.forget_session(session_id)
    return {"ok": True}


# ── Play and stop ───────────────────────────────────────────────────────────
#
# The agent is meant to keep the project running, and mostly does. But it will
# forget, and the user is then looking at a chat that says the game is ready
# with no game anywhere -- and no terminal to start one from. So the same
# machinery gets a button.


@router.get("/{session_id}/preview")
async def preview_state(session_id: str):
    """What the Play button should show: is there anything to play, and is it on."""
    from agent_server import preview

    session = await _require(session_id)
    return {
        "command": session.get("preview_command") or "",
        "url": session.get("preview_url") or "",
        "running": preview.is_running(session_id),
        "busy": agent.is_running(session_id),
    }


@router.post("/{session_id}/preview/start")
async def preview_start(session_id: str):
    """Run what the assistant last ran, from the user's own hand.

    Deliberately not "run whatever you send me": the command comes from the
    session row, so this endpoint cannot be talked into running something the
    assistant never chose.
    """
    from agent_server import preview

    session = await _require(session_id)
    command = (session.get("preview_command") or "").strip()
    if not command:
        raise HTTPException(409, "Nothing has been set up to run for this project yet.")
    try:
        await preview.start(
            session_id, command, (session.get("preview_url") or "").strip(),
            session["project_dir"], confine=await parental.child_mode_enabled(),
        )
    except preview.PreviewError as e:
        raise HTTPException(500, str(e).splitlines()[0]) from e
    return {"ok": True, "running": preview.is_running(session_id)}


@router.post("/{session_id}/preview/stop")
async def preview_stop(session_id: str):
    from agent_server import preview

    await _require(session_id)
    await preview.stop(session_id)
    return {"ok": True, "running": False}
