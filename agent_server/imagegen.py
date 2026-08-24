"""Making a picture, for the projects that need one.

A game needs a sprite; a website needs a header; a school project needs a
diagram of the water cycle. Every one of those currently stops the same way --
the assistant writes `<img src="hero.png">` and nothing is there, or it draws a
grey rectangle and calls it a placeholder, and the person who wanted a picture
of a dragon gets a grey rectangle.

Kept apart from the chat providers on purpose. Image models are reached
differently: Google's are on `generateContent` with an IMAGE modality and are
*not* available on the `/chat/completions` compatibility endpoint this app
talks to for text, so this cannot ride the existing path however much one would
like it to. Structured as a small registry keyed by provider so that adding a
second one is a function and a row, because more of these are coming.

Nothing here decides where a file goes or what it is called. That is the tool's
business, and the tool's business is the project's shape.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Long enough for a slow model on a slow connection, short enough that a stuck
# request does not hold a turn open past anybody's patience.
TIMEOUT = 180.0

# The biggest picture worth carrying around inside a project a child is
# building. Well past what any of these models returns today, and here so that
# a future model returning something enormous is refused rather than written.
MAX_BYTES = 25 * 1024 * 1024

MIME_EXT = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
    "image/gif": ".gif",
}


@dataclass(frozen=True)
class ImageModel:
    """One model that can draw, and what it costs to ask it to.

    `about_each` is dollars for one picture, said the way the price is
    published rather than derived from tokens -- these are billed per image,
    not per token, whatever the usage numbers come back saying.
    """

    id: str
    name: str
    provider: str
    about_each: float
    note: str = ""
    # Whether it can be given a picture and asked to change it, rather than
    # only asked for a new one. The difference between "draw me a dragon" and
    # "make the dragon green", and the second is what people actually want.
    edits: bool = True


# Newest first within a provider; the first one whose provider has a key is the
# one used when nobody says otherwise.
IMAGE_MODELS: list[ImageModel] = [
    ImageModel("gemini-3-pro-image", "Nano Banana Pro", "gemini", 0.14,
               "The best of them at text inside a picture and at fine detail."),
    ImageModel("gemini-3.1-flash-image", "Nano Banana", "gemini", 0.067,
               "Quick, cheap, and good enough for almost everything."),
    ImageModel("gemini-3.1-flash-lite-image", "Nano Banana Lite", "gemini", 0.04,
               "The cheapest. Fine for sprites, icons and backgrounds."),
    ImageModel("gemini-2.5-flash-image", "Nano Banana (older)", "gemini", 0.039),
]

IMAGE_MODELS_BY_ID = {m.id: m for m in IMAGE_MODELS}


class ImageError(RuntimeError):
    """Something went wrong that the assistant should say out loud."""


@dataclass
class Drawn:
    data: bytes
    mime: str
    model: ImageModel
    said: str = ""
    usage: dict = field(default_factory=dict)

    @property
    def extension(self) -> str:
        return MIME_EXT.get(self.mime, ".png")


# ── which of them this computer can actually reach ──────────────────────────


def available() -> list[ImageModel]:
    """The image models whose provider has a key on this machine.

    Same rule as the chat models: nothing is offered that cannot be used, so
    the assistant is never in the position of promising a picture and then
    explaining an authentication error to a nine-year-old.
    """
    from agent_server.providers import get_provider

    out = []
    for model in IMAGE_MODELS:
        try:
            provider = get_provider(model.provider)
        except ValueError:
            continue
        if provider.has_credentials():
            out.append(model)
    return out


def can_draw() -> bool:
    return bool(available())


def pick(wanted: str = "") -> ImageModel:
    """The model to use: the one asked for, or the best one reachable."""
    reachable = available()
    if not reachable:
        raise ImageError(
            "no AI on this computer can make pictures. Google's can, and the "
            "key is the same one used for everything else -- so this needs a "
            "Google key in Settings, which the Project Manager can walk them "
            "through.")
    wanted = (wanted or "").strip().lower()
    if wanted:
        for model in reachable:
            if wanted in (model.id.lower(), model.name.lower()):
                return model
        raise ImageError(
            f"there is no picture model called '{wanted}'. There is: "
            + ", ".join(f"{m.name} (about ${m.about_each:.2f} a picture)"
                        for m in reachable))
    return reachable[0]


# ── drawing ─────────────────────────────────────────────────────────────────


async def draw(prompt: str, *, model: ImageModel, reference: bytes = b"",
               reference_mime: str = "") -> Drawn:
    """Ask for a picture. `reference` turns it into an edit of that picture."""
    if not prompt.strip():
        raise ImageError("say what the picture should be of")
    if model.provider == "gemini":
        return await _gemini(prompt, model, reference, reference_mime)
    raise ImageError(f"{model.provider} cannot make pictures")


async def _gemini(prompt: str, model: ImageModel, reference: bytes,
                  reference_mime: str) -> Drawn:
    """Google, on the native endpoint.

    Deliberately not the OpenAI-compatible one this app uses for chat: image
    models are not served on `/chat/completions` there, only on a separate
    `/images/generations`, and that one cannot take a reference picture. The
    native call does both with the same request.
    """
    import httpx

    from agent_server.providers import get_provider

    key = get_provider("gemini").api_key()
    if not key:
        raise ImageError("there is no Google key saved, so nothing can draw yet")

    parts: list[dict] = [{"text": prompt}]
    if reference:
        # The picture first, then what to do to it -- which is the order the
        # model is documented to expect and, as it happens, the order somebody
        # would say it in.
        parts.insert(0, {"inlineData": {
            "mimeType": reference_mime or "image/png",
            "data": base64.b64encode(reference).decode(),
        }})

    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model.id}:generateContent")
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(url, params={"key": key}, json=body)
    except httpx.TimeoutException as e:
        raise ImageError("the picture took too long and was given up on") from e
    except httpx.HTTPError as e:
        raise ImageError(f"could not reach Google: {type(e).__name__}") from e

    if response.status_code != 200:
        raise ImageError(_why(response.status_code, response.text))

    try:
        data = response.json()
        candidate = data["candidates"][0]
        returned = candidate["content"]["parts"]
    except (ValueError, KeyError, IndexError) as e:
        blocked = _blocked(response)
        raise ImageError(blocked or "the answer came back in a shape this did "
                                    "not understand, and no picture with it") from e

    said = ""
    for part in returned:
        if "text" in part:
            said += part["text"]
        blob = part.get("inlineData") or part.get("inline_data")
        if not blob:
            continue
        raw = base64.b64decode(blob.get("data", ""))
        if not raw:
            continue
        if len(raw) > MAX_BYTES:
            raise ImageError("the picture came back far larger than expected "
                             "and was not saved")
        return Drawn(data=raw, mime=blob.get("mimeType") or blob.get("mime_type")
                     or "image/png", model=model, said=said.strip(),
                     usage=data.get("usageMetadata") or {})

    # Text and no picture is nearly always a refusal, and the text says why.
    raise ImageError(
        (said.strip() or "no picture came back and nothing said why")
        + " -- this is the model declining rather than a fault, so change what "
          "was asked for rather than trying the same thing again.")


def _why(status: int, text: str) -> str:
    """An HTTP failure, in words worth passing on."""
    detail = ""
    try:
        import json

        detail = (json.loads(text).get("error") or {}).get("message", "")
    except Exception:
        detail = text[:200]
    if status == 429:
        return ("Google will not make any more pictures right now -- the "
                "allowance is used up. Pictures are billed separately from "
                "text, so this can happen while ordinary replies still work. "
                f"({detail[:160]})")
    if status in (401, 403):
        return ("Google refused the key for pictures. Making pictures often "
                "has to be switched on for a project separately from text. "
                f"({detail[:160]})")
    if status == 400 and "modalit" in detail.lower():
        return ("that model cannot return a picture. "
                f"({detail[:160]})")
    return f"Google said no ({status}). {detail[:200]}"


def _blocked(response) -> str:
    """Whether the request was refused by a safety filter, and what to say."""
    try:
        data = response.json()
    except Exception:
        return ""
    feedback = data.get("promptFeedback") or {}
    if feedback.get("blockReason"):
        return ("the picture was refused before it was drawn "
                f"({feedback['blockReason']}). Say so plainly and offer to "
                "draw something else -- do not try the same words again.")
    return ""
