"""Project session CRUD, used by the settings page and manager tools."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from agent_server import agent, parental
from agent_server import database as db
from agent_server.config import (
    knows_model,
    model_info,
    provider_for_model,
    split_custom_choice,
)
from agent_server.model_catalog import effective_default_model, offerable_models

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

# Said wherever child mode stops somebody reaching the web. Never just "that is
# not allowed": the person reading it is quite often the grown-up who turned it
# on last week and has forgotten, and the conclusion they must not reach is that
# the app is broken. So: what stopped it, and where the switch is.
CHILD_MODE_BLOCKED = (
    "That goes out to the internet, and child mode is on, so it stays on this "
    "computer. Turn child mode off in Settings, under Parental controls, and "
    "links will open normally again."
)

_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
    "Content-Type": "text/event-stream; charset=utf-8",
}


async def _require(session_id: str) -> dict:
    session = await db.get_session(session_id)
    # Out of profile is reported as missing rather than forbidden: a child does
    # not need to be told that a session they may not open is there.
    if session is None or not await parental.may_reach(session):
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
    from agent_server.tools.session_manager import _git_init, _slug, clean_name

    name = clean_name(str(payload.get("name") or ""))
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
            raise HTTPException(
                403,
                "Child mode is on, so a project can only go in the usual place. Turn "
                "it off in Settings, under Parental controls, to choose a folder.",
            )
        folder = _Path(raw_folder).expanduser()
        try:
            folder = folder.resolve()
        except OSError as e:
            raise HTTPException(400, "That folder path could not be read.") from e
        # A project rooted at home or at the filesystem root gives every tool in
        # the session the run of the machine, which is never what was meant.
        if folder == _Path.home() or folder.parent == folder:
            raise HTTPException(400, "Pick a folder inside your home directory, not the whole of it.")
        if not folder.is_dir():
            if folder.exists():
                raise HTTPException(400, "There is a file at that path, not a folder.")
            if not folder.parent.is_dir():
                raise HTTPException(
                    400, f"There is no folder at {folder}, and nothing at "
                         f"{folder.parent} to make one in."
                )
            # A missing folder is asked about, never assumed. It is far more
            # often a typo in a path than a folder somebody meant to create --
            # and quietly making `/home/me/Porject` leaves them with an empty
            # project next to their real one and no idea why it is empty.
            #
            # 409 is the signal to ask; the answer comes back as `make_folder`.
            if not payload.get("make_folder"):
                raise HTTPException(409, f"There is no folder at {folder} yet.")
            try:
                folder.mkdir()
            except OSError as e:
                raise HTTPException(400, f"That folder could not be made: {e}") from e
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
        # A custom endpoint is offered as `custom:name`, and that is what a
        # session on it stores. Sessions from before the picker stopped listing
        # each reported model store the model id instead, and would show no tick
        # against anything; the provider column still names the endpoint.
        "current_model": (session["provider"]
                          if session["provider"].startswith("custom:")
                          else session["model"]),
        "context_tokens": usage["context"],
        # A switch the user has already chosen and not paid for yet, so the
        # menu can say so. Without it, choosing "later" and coming back an hour
        # later shows nothing at all -- and a choice that leaves no trace is a
        # choice people reasonably assume did not happen.
        "pending_model": session.get("pending_model") or "",
        "pending_name": (model_info(session["pending_model"]).get(
            "name", session["pending_model"]) if session.get("pending_model") else ""),
        "models": models,
    }


@router.get("/{session_id}/model/quote")
async def quote_model_switch(session_id: str, model: str = ""):
    """What moving this conversation to another AI would cost, before doing it.

    The app used to decide this silently: cheaper of the two wins, no mention
    of either number. That is the right default and the wrong amount of
    information -- a switch part-way through a long conversation can cost more
    than a day's ordinary use, and it arrived as a surprise on a bill nobody
    was reading. So the numbers come out first, and the choice is the user's.
    """
    from agent_server.compaction import estimate_switch_costs

    session = await _require(session_id)
    if not knows_model(model):
        raise HTTPException(400, "I do not recognise that model.")

    plan = await estimate_switch_costs(session_id, model)
    pending = session.get("pending_model") or ""
    return {
        "model": model,
        "name": model_info(model).get("name", model),
        "context_tokens": plan["context_tokens"],
        "direct_cost": plan["direct_cost"],
        "compact_cost": plan["compact_cost"],
        # Whether tidying up is even on the table. A short conversation has
        # nothing old enough to summarise, so offering it would be offering a
        # button that does nothing.
        "can_tidy": plan["head_tokens"] > 0 and plan["compact_cost"] < plan["direct_cost"],
        "pending_model": pending,
        "pending_name": model_info(pending).get("name", pending) if pending else "",
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
    # Refuse rather than guess. An id this app cannot place gets written to the
    # session with DEFAULT_PROVIDER attached, and the next message comes back
    # "No API key is set up yet. Add one in Settings" -- about a key that was
    # never the problem, on a project that was working a moment ago.
    if not knows_model(model):
        raise HTTPException(
            400,
            "I do not recognise that model, so I have left this project on the "
            "one it was using. Pick one from the list instead.",
        )

    display_name = model_info(model).get("name", model)

    # How the user chose to pay for it. "now" moves immediately and re-sends
    # the conversation; "tidy" summarises first and sends less; "later" queues
    # it for the next compaction, which rebuilds the prefix anyway and so costs
    # nothing extra. Anything else means the caller did not choose, and the app
    # picks whichever of the first two is cheaper -- the behaviour before there
    # was a choice to make.
    how = str(payload.get("how", "")).strip().lower()

    if how == "later":
        await db.set_pending_model(session_id, model)
        log.info("switch model queued session=%s model=%s", session_id, model)

        async def queued():
            yield agent.sse({"type": "switch_queued", "model": model,
                             "name": display_name})

        return StreamingResponse(queued(), media_type="text/event-stream",
                                 headers=_SSE_HEADERS)

    async def generator():
        from agent_server.compaction import compact_session, estimate_switch_costs

        plan = await estimate_switch_costs(session_id, model)
        log.info(
            "switch model session=%s model=%s how=%s compact=%s direct=$%.4f compact=$%.4f",
            session_id, model, how or "auto", plan["compact"],
            plan["direct_cost"], plan["compact_cost"],
        )
        tidy = plan["compact"] if how not in ("now", "tidy") else how == "tidy"

        # A switch chosen now replaces one that was waiting, rather than
        # leaving it to fire later and move the session a second time.
        await db.set_pending_model(session_id, "")

        if tidy:
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
    from agent_server.tools.session_manager import clean_name

    await _require(session_id)
    updates = {}
    if (name := clean_name(str(payload.get("name") or ""))):
        updates["name"] = name
    if "description" in payload:
        updates["description"] = (payload.get("description") or "").strip() or None
    if not updates:
        return await _require(session_id)
    return await db.update_session(session_id, **updates)


@router.post("/remove")
async def remove_sessions(payload: dict):
    """Remove several projects at once, once the user has said yes.

    The assistant can gather up "all the ones about cats" and put the names on
    screen (`delete_projects`), but it stops there: this is what the button in
    that box calls. Nothing here is reachable from the model.

    One project that cannot be removed does not stop the rest -- with a hundred
    of them, failing the lot because one row has already gone is the worse
    outcome by far. What was removed and what was not both come back.
    """
    ids = [str(i) for i in (payload.get("ids") or [])]
    removed, kept = [], []
    for session_id in ids:
        session = await db.get_session(session_id)
        if session is None or session.get("kind") == "manager":
            kept.append(session_id)
            continue
        agent.request_abort(session_id)
        from agent_server import preview

        await preview.stop(session_id)
        await db.delete_session(session_id)
        agent.forget_session(session_id)
        removed.append(session["name"])
    return {"ok": True, "removed": removed, "kept": len(kept)}


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
        "pickable": preview.can_pick(session_id),
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
            pickable=bool(session.get("preview_pickable", 1)),
        )
    except preview.PreviewError as e:
        raise HTTPException(500, str(e).splitlines()[0]) from e
    return {"ok": True, "running": preview.is_running(session_id)}


@router.post("/{session_id}/pick")
async def preview_pick(session_id: str):
    """Let the user click part of the running page, and say what they clicked.

    Held open rather than polled. The gap between pressing the button and
    finding the thing is however long it takes to find the thing, and a poll
    that is fast enough not to feel laggy is a poll running all afternoon for
    a feature used twice.
    """
    from agent_server import annotate, preview

    session = await _require(session_id)
    if not preview.can_pick(session_id):
        raise HTTPException(409, "There is nothing open to point at.")
    try:
        await preview.arm(session_id)
    except preview.PreviewError as e:
        raise HTTPException(409, str(e).splitlines()[0]) from e

    picked = await annotate.wait_for_pick(session_id)
    if picked is None:
        # Escape, a closed window, or three minutes of nothing. All the same
        # to the person who is now looking at the app again.
        await preview.disarm(session_id)
        return {"picked": False}
    return {
        "picked": True,
        "label": annotate.summarise(picked),
        "description": annotate.describe(picked, session.get("project_dir") or ""),
    }


@router.post("/{session_id}/pick/cancel")
async def preview_pick_cancel(session_id: str):
    """Called when the user gives up from the app's side rather than the page's."""
    from agent_server import annotate, preview

    await _require(session_id)
    annotate.forget(session_id)
    await preview.disarm(session_id)
    return {"ok": True}


@router.post("/{session_id}/preview/stop")
async def preview_stop(session_id: str):
    from agent_server import preview

    await _require(session_id)
    await preview.stop(session_id)
    return {"ok": True, "running": False}


# ── A link the user pressed in a reply ──────────────────────────────────────
#
# The assistant writes "your site is running at http://localhost:8123", which is
# an ordinary thing to write and a perfectly reasonable thing to press. Pressing
# it opened the user's normal browser, which -- if the project had since been
# stopped -- showed a connection error and nothing else. Somebody who is not
# technical has no way to know that the page is fine and the server is off, let
# alone that the fix is to press Play first.
#
# So a link to this machine is not a link. It is another way of saying "show me
# my project", and it does what Play does: starts it if it is not up, and puts
# it in the project's own window. No rule in the system prompt needed, and the
# assistant can write the address whenever it likes.


@router.post("/{session_id}/open-link")
async def open_link(session_id: str, payload: dict):
    from agent_server import preview
    from agent_server.routes.files import open_in_browser

    session = await _require(session_id)
    url = str(payload.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "That is not an address this app can open.")

    child = await parental.child_mode_enabled()

    if not preview.is_this_machine(url):
        # Out to the web. In child mode there is nowhere for it to go: the
        # project window refuses anything off this machine however the address
        # arrives, and handing it to the real browser would walk straight round
        # the one thing a parent is trusting this app about.
        if child:
            # Say which switch, and where it is. A grown-up who set child mode
            # last week and has forgotten will otherwise sit there deciding the
            # app is broken -- and the one thing they must not conclude is that
            # links do not work here.
            raise HTTPException(403, CHILD_MODE_BLOCKED)
        if await open_in_browser(url):
            return {"ok": True, "where": "browser"}
        raise HTTPException(501, "This computer has no browser the app could open.")

    # This machine: the project. Start it first if it is not already up, which
    # is the whole point -- otherwise this is a connection error with no
    # explanation attached.
    started = False
    if not preview.is_running(session_id):
        command = (session.get("preview_command") or "").strip()
        if not command:
            raise HTTPException(
                409,
                "Nothing is running at that address yet, and this project has no "
                "way to start recorded. Ask the assistant to run it.",
            )
        try:
            await preview.start(
                session_id, command, url, session["project_dir"], confine=child,
            )
            started = True
        except preview.PreviewError as e:
            raise HTTPException(500, str(e).splitlines()[0]) from e
    else:
        try:
            await preview.show(session_id, url, confine=child)
        except preview.PreviewError as e:
            raise HTTPException(500, str(e).splitlines()[0]) from e

    return {"ok": True, "where": "project", "started": started,
            "running": preview.is_running(session_id)}
