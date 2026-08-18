"""Chat, streaming, and speech endpoints."""

import re as _re
import uuid as _uuid
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
from agent_server.config import ATTACH_DIR
from agent_server.models import ChatRequest

router = APIRouter(prefix="/api", tags=["chat"])

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
    "Content-Type": "text/event-stream; charset=utf-8",
}


def _stream(session_id: str, request: Request) -> StreamingResponse:
    agent.start_run(session_id)

    async def generator() -> AsyncIterator[str]:
        async for event in agent.subscribe(session_id):
            yield agent.sse(event)

    return StreamingResponse(generator(), media_type="text/event-stream", headers=SSE_HEADERS)


def _attach(session_id: str) -> StreamingResponse:
    async def generator() -> AsyncIterator[str]:
        if agent.active_run(session_id) is None:
            yield agent.sse({"type": "stream_end"})
            return
        async for event in agent.subscribe(session_id, replay=False):
            yield agent.sse(event)

    return StreamingResponse(generator(), media_type="text/event-stream", headers=SSE_HEADERS)


async def _require_session(session_id: str) -> dict:
    session = await db.get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    return session


@router.post("/sessions/{session_id}/chat")
async def chat(session_id: str, request: Request, body: ChatRequest):
    await _require_session(session_id)
    text = body.message.strip()
    if not text:
        raise HTTPException(400, "Message is required")
    if agent.is_running(session_id) and agent.queue_message(session_id, text) is not None:
        return _stream(session_id, request)
    await db.add_message(session_id, "user", text)
    return _stream(session_id, request)


@router.post("/sessions/{session_id}/upload")
async def upload_attachment(session_id: str, file: UploadFile = File(...)):
    """Save a dropped file and return its path, so the client can hand that path
    to the agent in the next message and the agent reads it with its own tools."""
    await _require_session(session_id)
    name = Path(file.filename or "file").name
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name).strip() or "file"
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    target = ATTACH_DIR / f"{_uuid.uuid4().hex[:8]}_{safe}"
    target.write_bytes(data)
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

_link_cache: dict[str, dict] = {}


def _decode(text: str) -> str:
    import html as _html

    return _html.unescape(text or "").strip()


@router.get("/link_preview")
async def link_preview(url: str):
    """Fetch a URL's title/description/image for a small preview card."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"ok": False}
    if url in _link_cache:
        return _link_cache[url]
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            resp = await client.get(
                url, headers={"User-Agent": "Mozilla/5.0 (compatible; Assistant/1.0)"}
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
