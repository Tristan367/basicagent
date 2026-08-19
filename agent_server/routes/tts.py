"""Text-to-speech: turn an assistant reply into audio the browser can play."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from agent_server import database as db
from agent_server import tts as tts_service

router = APIRouter(prefix="/api/tts", tags=["tts"])


class PlanBody(BaseModel):
    text: str


class SpeakBody(BaseModel):
    text: str
    voice: str = ""
    speed: float = 1.0


@router.get("/status")
async def tts_status():
    status = tts_service.availability()
    status["voice"] = await db.get_setting("tts_voice", status["default_voice"])
    status["speed"] = float(await db.get_setting("tts_speed", "1.25"))
    status["volume"] = float(await db.get_setting("tts_volume", "0.75"))
    status["tone"] = float(await db.get_setting("tts_tone", str(tts_service.TTS_SAMPLE_RATE)))
    return status


@router.post("/plan")
async def tts_plan(body: PlanBody):
    """Split a reply into the sentences that will be spoken.

    The client drives chunking, because only it knows how much audio is still
    buffered, so it needs the sentence list up front rather than a fixed
    server-side carve-up.
    """
    return {"sentences": tts_service.plan(body.text)}


async def _tone() -> int:
    """The output sample rate, from the `tts_tone` setting.

    Defaults to Kokoro's own 24 kHz. There is no UI for this: it exists so a
    lower rate can be set directly in the database for someone who finds the
    full-band voice harsh, without needing a code change.
    """
    try:
        return int(float(await db.get_setting("tts_tone", str(tts_service.TTS_SAMPLE_RATE))))
    except (TypeError, ValueError):
        return tts_service.TTS_SAMPLE_RATE


@router.post("/speak")
async def tts_speak(body: SpeakBody):
    try:
        audio = await tts_service.synth(body.text, body.voice, body.speed, await _tone())
    except tts_service.TTSError as e:
        raise HTTPException(400, str(e)) from e
    # no-store: these are regenerated freely and there is no point filling the
    # browser cache with one entry per sentence of every reply.
    return Response(
        content=audio,
        media_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )


def _friendly_voice(voice: str) -> str:
    """"af_bella" -> "Bella", so the preview can introduce itself by name."""
    name = (voice or "").rsplit("/", 1)[-1]
    if "_" in name:
        name = name.split("_", 1)[1]
    name = name.replace("_", " ").strip()
    if not name:
        return "this voice"
    return " ".join(part.capitalize() for part in name.split())


@router.get("/preview")
async def tts_preview(voice: str = "", speed: float = 1.0):
    """A short spoken sample, so the user can audition a voice."""
    sample = f"Hi, my name is {_friendly_voice(voice)}. This is what my voice sounds like."
    try:
        audio = await tts_service.synth(sample, voice, speed, await _tone())
    except tts_service.TTSError as e:
        raise HTTPException(400, str(e)) from e
    return Response(
        content=audio,
        media_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )
