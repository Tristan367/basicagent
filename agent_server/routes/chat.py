"""Chat, streaming, and speech endpoints."""

import re as _re
import uuid as _uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import numpy as np
from fastapi import (
    APIRouter,
    File,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse, StreamingResponse

from agent_server import agent, whisper_streaming
from agent_server import database as db
from agent_server import stt as stt_service
from agent_server.config import ATTACH_DIR, USER_AGENT
from agent_server.models import ChatRequest

router = APIRouter(prefix="/api", tags=["chat"])

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
    "Content-Type": "text/event-stream; charset=utf-8",
}


def _stream(session_id: str) -> StreamingResponse:
    """Watch the session's current run. Starting one is the caller's job."""

    async def generator() -> AsyncIterator[str]:
        async for event in agent.subscribe(session_id):
            yield agent.sse(event)

    return StreamingResponse(generator(), media_type="text/event-stream", headers=SSE_HEADERS)


def _attach(session_id: str) -> StreamingResponse:
    """Everything this run has said so far, then the rest of it as it happens.

    With `replay=False` a browser that reloaded mid-turn got only the events
    still to come, and the round it was in the middle of -- which is not saved
    until it finishes -- was simply lost. The client knows which of these it has
    already, because the run marks the points where it saved.
    """

    async def generator() -> AsyncIterator[str]:
        if agent.active_run(session_id) is None:
            yield agent.sse({"type": "stream_end"})
            return
        async for event in agent.subscribe(session_id, replay=True, since_last_save=True):
            yield agent.sse(event)

    return StreamingResponse(generator(), media_type="text/event-stream", headers=SSE_HEADERS)


async def _require_session(session_id: str) -> dict:
    from agent_server import parental

    session = await db.get_session(session_id)
    # See `parental.may_reach`. This is the door that mattered: talking to a
    # session is what makes reaching it worth anything.
    if session is None or not await parental.may_reach(session):
        raise HTTPException(404, "Session not found")
    return session


@router.post("/sessions/{session_id}/chat")
async def chat(session_id: str, request: Request, body: ChatRequest):
    await _require_session(session_id)
    text = body.message.strip()
    if not text:
        raise HTTPException(400, "Message is required")

    # The session is claimed before the message is stored, and nothing awaits
    # in between. Checking "is it busy?" and then awaiting the write left a
    # window in which a second send saw an idle session too, so one tap of
    # Send that the browser retried put the same message in the conversation
    # twice and paid to have it read twice.
    claimed = agent.claim_turn(session_id)
    if claimed is None:
        # Already working: this joins the turn in flight at its next boundary
        # rather than starting a second one alongside it.
        if agent.queue_message(session_id, text) is not None:
            return _stream(session_id)
        raise HTTPException(409, "This project is already working.")
    handle, abort = claimed
    try:
        await db.add_message(session_id, "user", text, images=_pictures(body.images))
    except Exception:
        agent.release_turn(session_id, handle, abort)
        raise
    agent.start_claimed_run(session_id, handle, abort)
    return _stream(session_id)


MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024

# Pictures sent with one message. Someone dropping a folder of forty holiday
# photos on the chat should not turn into a sixty-thousand-token request they
# did not ask for and cannot see coming.
MAX_PICTURES_PER_MESSAGE = 8


def _pictures(paths: list[str]) -> list[str]:
    """Keep the ones that are really pictures that really exist.

    The client says which attachments were images, and it is right about that
    -- but it is also the one part of this app a mistake can reach from
    outside, so the claim is checked rather than trusted. A path that has been
    deleted between attaching and sending is dropped here instead of failing
    the turn later.
    """
    from agent_server import images as pictures

    kept = []
    for raw in paths[:MAX_PICTURES_PER_MESSAGE]:
        path = Path(str(raw)).expanduser()
        if pictures.is_image(path) and path.is_file():
            kept.append(str(path))
    return kept


@router.post("/sessions/{session_id}/upload")
async def upload_attachment(session_id: str, file: UploadFile = File(...)):
    """Save a dropped file and return its path, so the client can hand that path
    to the agent in the next message and the agent reads it with its own tools.

    Written in chunks against a limit rather than read whole. There was no
    limit at all, and the whole file went into memory first: dropping a video
    on the chat -- which someone will do, because dropping things on the chat
    is how this app works -- took the server down with it.
    """
    await _require_session(session_id)
    name = Path(file.filename or "file").name
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name).strip() or "file"
    target = ATTACH_DIR / f"{_uuid.uuid4().hex[:8]}_{safe}"

    written = 0
    try:
        with target.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_ATTACHMENT_BYTES:
                    raise HTTPException(
                        413,
                        f"That file is too big to attach (the limit is "
                        f"{MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB).",
                    )
                out.write(chunk)
        if not written:
            raise HTTPException(400, "Empty file")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return {"ok": True, "path": str(target), "name": name}


@router.post("/sessions/{session_id}/queue")
async def queue(session_id: str, payload: dict):
    await _require_session(session_id)
    text = (payload.get("message") or "").strip()
    if not text:
        return JSONResponse({"ok": False, "reason": "Empty message"}, status_code=400)
    queue_id = agent.queue_message(session_id, text)
    if queue_id is None:
        return JSONResponse({"ok": False, "reason": "Nothing is running"}, status_code=409)
    return {"ok": True, "queue_id": queue_id}


@router.delete("/sessions/{session_id}/queue/{queue_id}")
async def unqueue(session_id: str, queue_id: str):
    await _require_session(session_id)
    text = agent.unqueue_message(session_id, queue_id)
    if text is None:
        return JSONResponse({"ok": False, "reason": "Already sent"}, status_code=409)
    return {"ok": True, "message": text}


@router.get("/sessions/{session_id}/attach")
async def attach(session_id: str):
    await _require_session(session_id)
    return _attach(session_id)


@router.post("/sessions/{session_id}/cancel")
async def cancel(session_id: str):
    return {"ok": agent.request_abort(session_id)}


@router.get("/sessions/{session_id}/state")
async def state(session_id: str):
    await _require_session(session_id)
    return {"running": agent.is_running(session_id)}


@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str):
    await _require_session(session_id)
    return await db.get_session_history(session_id)


# ── Link previews ───────────────────────────────────────────────────────────

_OG = _re.compile(
    r'<meta[^>]+(?:property|name)=["\'](og:[a-z:]+)["\'][^>]*content=["\']([^"\']*)["\']',
    _re.IGNORECASE,
)
_TITLE = _re.compile(r"<title[^>]*>([^<]*)</title>", _re.IGNORECASE)
_DESC = _re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]*content=["\']([^"\']*)["\']',
    _re.IGNORECASE,
)

# Bounded, oldest-out. The server is long-lived and a chat can accumulate an
# unlimited number of distinct links, so an unbounded dict here is a slow leak.
_link_cache: OrderedDict[str, dict] = OrderedDict()
_LINK_CACHE_MAX = 256


def _decode(text: str) -> str:
    import html as _html

    return _html.unescape(text or "").strip()


async def _is_public_host(host: str) -> bool:
    """Whether a hostname resolves only to addresses out on the internet.

    Every address the name resolves to is checked, not just the first, because
    a name that answers with one public address and one private one is the
    ordinary way this is got around.
    """
    import asyncio
    import ipaddress
    import socket

    if not host:
        return False
    try:
        # The loop's resolver, not socket's: a plain getaddrinfo blocks every
        # other request on this process for as long as the lookup takes.
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, None, proto=socket.IPPROTO_TCP
        )
    except (OSError, UnicodeError):
        return False
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast
                or address.is_unspecified):
            return False
    return bool(infos)


@router.get("/link_preview")
async def link_preview(url: str):
    """Fetch a URL's title/description/image for a small preview card."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"ok": False}
    # Previews are fetched automatically from links the *model* wrote, so this
    # is the server following a URL nobody chose to visit. Off the local
    # network only: otherwise it is a way to reach the router's admin page, a
    # cloud metadata service, or -- once this is the teacher's machine serving
    # a classroom -- anything else on the school network, and report back what
    # it found in the page title.
    if not await _is_public_host(parsed.hostname or ""):
        return {"ok": False}
    if url in _link_cache:
        _link_cache.move_to_end(url)
        return _link_cache[url]
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            resp = await client.get(
                url, headers={"User-Agent": USER_AGENT}
            )
        body = resp.text[:200_000]
    except Exception:
        return {"ok": False}

    og = {k.lower(): _decode(v) for k, v in _OG.findall(body)}
    title = og.get("og:title") or ""
    if not title:
        m = _TITLE.search(body)
        if m:
            title = _decode(m.group(1))
    description = og.get("og:description") or ""
    if not description:
        m = _DESC.search(body)
        if m:
            description = _decode(m.group(1))
    image = og.get("og:image", "")
    result = {
        "ok": bool(title),
        "title": title[:200],
        "description": description[:300],
        "image": image,
        "site": parsed.netloc,
        "url": url,
    }
    if result["ok"]:
        _link_cache[url] = result
        while len(_link_cache) > _LINK_CACHE_MAX:
            _link_cache.popitem(last=False)
    return result


# ── Speech to text ──────────────────────────────────────────────────────────

@router.get("/stt/status")
async def stt_status():
    status = stt_service.availability()
    status["streaming"] = whisper_streaming.whisper_streaming_available()
    return status


@router.post("/stt")
async def transcribe(audio: UploadFile = File(...)):
    suffix = Path(audio.filename or "").suffix or ".webm"
    data = await audio.read()
    try:
        text = await stt_service.transcribe(data, suffix)
    except stt_service.STTError as e:
        raise HTTPException(400, str(e)) from e
    return {"text": text}


@router.websocket("/stt/stream")
async def stt_stream(websocket: WebSocket):
    """Live dictation: 16 kHz mono float32 PCM in, partial hypotheses out."""
    await websocket.accept()
    try:
        server = await whisper_streaming.get_server()
    except whisper_streaming.WhisperStreamingError as e:
        await websocket.send_json({"error": str(e)})
        await websocket.close()
        return

    session = whisper_streaming.WhisperSession(server)
    try:
        should_finalize = True
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                should_finalize = False
                break
            if message.get("text") is not None:
                break
            if message.get("bytes") is not None:
                samples = np.frombuffer(message["bytes"], dtype=np.float32)
                if samples.size:
                    session.append(samples)
                    if session.busy:
                        continue
                    session.busy = True
                    try:
                        if session.should_finalize:
                            await session.commit_pause()
                            await websocket.send_json({"text": session.finalized_text(), "partial": True})
                        elif session.new_seconds >= whisper_streaming.STEP_SECONDS:
                            partial = await session.current_partial()
                            text = (session.finalized_text() + " " + partial).strip()
                            if text:
                                await websocket.send_json({"text": text, "partial": True})
                    except Exception:
                        pass
                    finally:
                        session.busy = False
        if should_finalize:
            try:
                final = await session.finalize()
                await websocket.send_json({"text": final, "partial": False})
            except Exception:
                await websocket.send_json({"text": session.finalized_text(), "partial": False})
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
