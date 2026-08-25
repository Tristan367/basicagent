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
like it to.

Two halves, and the split is the point:

* A **hand-checked list** of models somebody has actually driven, with prices
  somebody has actually read off a pricing page. Google's, today, because
  Google's free text tier is what most of this app's users will have.
* **Discovery**, which asks every provider with a key what it has that can draw
  and believes the answer. A model released tomorrow, from a company nobody
  here has heard of, shows up in the list without a line of code changing.

The hand-checked half exists so the common case is good: a child with a Google
key gets a model that is known to work at a price that is known to be right,
chosen for them, with nothing to decide. Discovery exists so the uncommon case
is possible at all rather than being told "this app only does Google".

Nothing here decides where a file goes or what it is called. That is the tool's
business, and the tool's business is the project's shape.
"""

from __future__ import annotations

import base64
import logging
import re
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Long enough for a slow model on a slow connection, short enough that a stuck
# request does not hold a turn open past anybody's patience.
TIMEOUT = 180.0

# Asking a provider what it has is a different kind of request: it happens
# while somebody is waiting to hear whether a picture is even possible, and an
# answer of "we could not find out" arrives quickly enough to be useful.
LOOKUP_TIMEOUT = 12.0

# The biggest picture worth carrying around inside a project a child is
# building. Well past what any of these models returns today, and here so that
# a future model returning something enormous is refused rather than written.
MAX_BYTES = 25 * 1024 * 1024

MIME_EXT = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
    "image/gif": ".gif",
}

# How a model is reached. The wire shape, not the vendor -- three of these
# cover everything reachable today, and a new vendor almost always arrives on
# one of them rather than inventing a fourth.
GEMINI = "gemini"        # Google's native generateContent, IMAGE modality
CHAT = "openai-chat"     # /chat/completions with modalities: ["image","text"]
IMAGES = "openai-images"  # /images/generations, the DALL-E shape
EITHER = "openai-either"  # try both, for an endpoint that has not said which

# Image models are billed per picture, but aggregators quote a per-token price
# for the tokens a picture comes to. Google publishes 1290 output tokens for a
# 1024px image, and that number reproduces the published per-picture prices to
# the cent, so it is the multiplier used to turn a quoted token price back into
# what one picture costs. It is why every price here is said as "about".
TOKENS_PER_PICTURE = 1290

# The ceiling on a picture-making reply. Comfortably above a picture plus a
# sentence -- the largest of these seen is about 4,000 tokens -- and far below
# the model's context. See the note where it is used.
MAX_REPLY_TOKENS = 8192

# The floor. Below this a picture does not fit, and a truncated one is billed
# at the same rate as a good one, so there is nothing to be gained by trying.
MIN_REPLY_TOKENS = 4096


@dataclass(frozen=True)
class ImageModel:
    """One model that can draw, and what it costs to ask it to.

    `about_each` is dollars for one picture, zero when nobody has told us. A
    price of zero means unknown and must be said as unknown -- inventing one is
    worse than admitting it, because the person paying is often not the person
    asking.
    """

    id: str
    name: str
    provider: str
    about_each: float = 0.0
    note: str = ""
    # Whether it can be given a picture and asked to change it, rather than
    # only asked for a new one. The difference between "draw me a dragon" and
    # "make the dragon green", and the second is what people actually want.
    edits: bool = True
    route: str = GEMINI
    # True when we know it draws: it is on the hand-checked list, or its
    # provider said so in so many words. False when we are going by the look of
    # its name, which is a guess and has to be offered as one.
    sure: bool = True

    @property
    def priced(self) -> bool:
        return self.about_each > 0


# Newest first; the first one whose provider has a key is the one used when
# nobody says otherwise.
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


class NoFunds(ImageError):
    """The account cannot pay for this, and a person has to go and fix that.

    Its own class because it is the only failure here whose remedy is somebody
    opening a website with a card, rather than the app trying something
    different. The tool that catches this puts the steps on the screen.
    """


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


# ── recognising a picture model by its name ─────────────────────────────────
#
# A last resort, for an endpoint that lists what it serves and says nothing
# about what the things do -- which is every plain OpenAI-compatible server,
# including whatever somebody is running on the box in the corner. Anything
# found this way is marked `sure=False` and offered as a guess, so a wrong
# guess costs a sentence rather than money.

_DRAWS = re.compile(
    r"(^|[-_/.])("
    r"image|imagen|dall-?e|gpt-image|flux|stable-?diffusion|sd-?xl|sd3"
    r"|banana|midjourney|ideogram|seedream|seededit|recraft|photon|firefly"
    r"|kandinsky|playground-v|janus|hidream|wan\d|qwen-image|grok-image"
    r"|nano-?banana|dreamshaper|pixart|aura-?flow|lumina"
    r")",
    re.I,
)

# Names that contain one of the words above and still cannot draw: they read
# pictures, score them, or turn them into numbers.
_DOES_NOT_DRAW = re.compile(
    r"(embed|rerank|moderat|guard|caption|classif|ocr|detect|vision|-vl($|[-_])"
    r"|score|reward|judge|clip)",
    re.I,
)


def looks_like_it_draws(model_id: str) -> bool:
    if _DOES_NOT_DRAW.search(model_id):
        return False
    return bool(_DRAWS.search(model_id))


# ── which of them this computer can actually reach ──────────────────────────


def _with_key() -> list[tuple[str, object]]:
    """Every provider that has credentials, as (key, provider)."""
    from agent_server.providers import _providers

    out = []
    for name, provider in list(_providers.items()):
        try:
            if provider.has_credentials():
                out.append((name, provider))
        except Exception:  # a half-configured custom endpoint
            continue
    return out


def curated() -> list[ImageModel]:
    """The hand-checked models whose provider has a key on this machine.

    Synchronous and free: no network, no waiting. This is the fast answer to
    "can this computer draw at all", and on the overwhelmingly common setup --
    a Google key and nothing else -- it is also the complete answer.
    """
    reachable = {name for name, _ in _with_key()}
    return [m for m in IMAGE_MODELS if m.provider in reachable]


# Discovery costs a request or two, and what a provider serves does not change
# between one question and the next. Cached against the set of keys in play, so
# adding a key mid-conversation is noticed immediately rather than in a quarter
# of an hour.
_CACHE_TTL = 900.0
_cache: tuple[str, float, list[ImageModel]] | None = None


def _fingerprint() -> str:
    return "|".join(sorted(name for name, _ in _with_key()))


def forget_catalogue() -> None:
    """Drop what discovery found. Called when a key changes."""
    global _cache
    _cache = None


def _dedupe_keys(model: ImageModel) -> tuple[str, str]:
    """What makes two entries the same model wearing different labels.

    Two ways in, because vendors use both. `gemini-3-pro-image` from Google and
    `google/gemini-3-pro-image` from an aggregator differ only by a prefix; but
    Google also serves that same model as `nano-banana-pro-preview`, an id with
    nothing in common with the first, and the only thing tying them together is
    that both are called "Nano Banana Pro". So the name counts too.

    Listing one model twice -- once with a price and once without -- invites
    somebody to wonder which is the good one, and there is no good answer.
    """
    tail = model.id.rsplit("/", 1)[-1].lower()
    return (re.sub(r"[-_](preview|latest|beta|exp)(-\d+)?$", "", tail),
            model.name.strip().lower())


def _order(model: ImageModel) -> tuple:
    """Hand-checked first, then certain, then dearest -- which is best-first.

    Price stands in for quality because among picture models it genuinely does,
    and because it is the number the person paying cares about either way.
    Unpriced models sink to the bottom: recommending one means recommending a
    number nobody can see.
    """
    return (
        0 if model.id in IMAGE_MODELS_BY_ID else 1,
        0 if model.sure else 1,
        0 if model.priced else 1,
        -model.about_each,
        model.name.lower(),
    )


async def catalogue(refresh: bool = False) -> list[ImageModel]:
    """Everything on this computer that can draw, or looks like it can.

    The hand-checked list first, then whatever the providers admit to. Never
    raises: a provider that will not answer contributes nothing, and the
    hand-checked half still stands.
    """
    global _cache

    fingerprint = _fingerprint()
    fresh = _cache and time.monotonic() - _cache[1] < _CACHE_TTL
    if not refresh and fresh and _cache[0] == fingerprint:
        return list(_cache[2])

    found: list[ImageModel] = list(curated())
    seen: set[str] = set()
    for model in found:
        seen.update(_dedupe_keys(model))

    for name, provider in _with_key():
        try:
            discovered = await _discover(name, provider)
        except Exception as e:  # discovery is a nicety; it never breaks drawing
            log.info("could not ask %s what it can draw: %s", name, e)
            continue
        for model in discovered:
            keys = _dedupe_keys(model)
            if seen.intersection(keys):
                continue
            seen.update(keys)
            found.append(model)

    found.sort(key=_order)
    _cache = (fingerprint, time.monotonic(), found)
    return list(found)


async def _discover(name: str, provider) -> list[ImageModel]:
    if name == "gemini":
        return await _discover_gemini(provider)
    if name == "openrouter":
        return await _discover_openrouter(provider)
    return await _discover_openai_like(name, provider)


async def _discover_gemini(provider) -> list[ImageModel]:
    """Google's own list.

    It says nothing about modalities, so the name is the signal -- but Google
    names every one of these `...-image`, and the list hands over a display
    name ("Nano Banana") that is nicer than the id. Prices are not published
    here, so anything found this way that is not on the hand-checked list is
    offered without one.
    """
    import httpx

    key = provider.api_key()
    if not key:
        return []
    async with httpx.AsyncClient(timeout=LOOKUP_TIMEOUT) as client:
        response = await client.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": key, "pageSize": 200},
        )
    if response.status_code != 200:
        return []
    out = []
    for row in response.json().get("models", []):
        model_id = str(row.get("name", "")).removeprefix("models/")
        if not model_id or not looks_like_it_draws(model_id):
            continue
        if "generateContent" not in (row.get("supportedGenerationMethods") or []):
            continue
        out.append(ImageModel(
            id=model_id,
            name=row.get("displayName") or model_id,
            provider="gemini",
            about_each=0.0,
            note="Found on your Google account. Google does not publish a "
                 "price here, so this one's cost is unknown.",
            route=GEMINI,
            # Google's naming is a convention rather than a promise, but it has
            # held across every model they have shipped, and the endpoint is
            # the one we already drive.
            sure=True,
        ))
    return out


async def _discover_openrouter(provider) -> list[ImageModel]:
    """The one place that answers the question properly.

    Every model carries `output_modalities`, so "can it draw" is a fact rather
    than a guess, and `pricing.image_output` gives a real number. This is the
    future-proofing that matters: a model from a company nobody has heard of,
    released this morning, appears here with a price and works.
    """
    import httpx

    async with httpx.AsyncClient(timeout=LOOKUP_TIMEOUT) as client:
        response = await client.get("https://openrouter.ai/api/v1/models")
    if response.status_code != 200:
        return []
    out = []
    for row in response.json().get("data", []):
        model_id = str(row.get("id") or "")
        architecture = row.get("architecture") or {}
        if "image" not in (architecture.get("output_modalities") or []):
            continue
        # The routers pick a model per request and price at "-1". Asking one for
        # a picture is a lottery with somebody else's money.
        if model_id.startswith("openrouter/"):
            continue
        pricing = row.get("pricing") or {}
        try:
            each = float(pricing.get("image_output") or 0) * TOKENS_PER_PICTURE
        except (TypeError, ValueError):
            each = 0.0
        out.append(ImageModel(
            id=model_id,
            name=row.get("name") or model_id,
            provider="openrouter",
            about_each=max(each, 0.0),
            note="Through OpenRouter.",
            edits="image" in (architecture.get("input_modalities") or []),
            route=CHAT,
            sure=True,
        ))
    return out


async def _discover_openai_like(name: str, provider) -> list[ImageModel]:
    """Anything else that speaks OpenAI: DeepSeek, a local server, a new vendor.

    These list ids and nothing else, so this is guesswork by name and says so.
    A local Stable Diffusion behind an OpenAI-compatible proxy turns up here,
    and so would a vendor that shipped last week.
    """
    import httpx

    base = (getattr(provider, "base_url", "") or "").rstrip("/")
    if not base:
        return []
    key = provider.api_key()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    async with httpx.AsyncClient(timeout=LOOKUP_TIMEOUT) as client:
        response = await client.get(f"{base}/models", headers=headers)
    if response.status_code != 200:
        return []
    rows = response.json().get("data", [])
    out = []
    for row in rows if isinstance(rows, list) else []:
        model_id = str((row or {}).get("id") or "")
        if not model_id or not looks_like_it_draws(model_id):
            continue
        out.append(ImageModel(
            id=model_id,
            name=model_id,
            provider=name,
            about_each=0.0,
            note=f"On {getattr(provider, 'name', name)}. Its name says it "
                 f"draws; nothing here has confirmed that, and its price is "
                 f"not published.",
            route=EITHER,
            sure=False,
        ))
    return out


async def pick(wanted: str = "") -> ImageModel:
    """The model to use: the one asked for, or the best one reachable."""
    reachable = await catalogue()
    if not reachable:
        raise ImageError(
            "nothing on this computer can make pictures. Google's models can, "
            "and the key is the same one used for everything else -- so this "
            "needs a Google key in Settings, which the Project Manager can "
            "walk them through.")
    wanted = (wanted or "").strip().lower()
    if wanted:
        for model in reachable:
            if wanted in (model.id.lower(), model.name.lower()):
                return model
        # A bare id off an aggregator -- "flux-1.1-pro" for
        # "black-forest-labs/flux-1.1-pro" -- is what somebody would type.
        for model in reachable:
            if model.id.lower().rsplit("/", 1)[-1] == wanted:
                return model
        raise ImageError(
            f"there is no picture model called '{wanted}'. There is: "
            + ", ".join(m.name for m in reachable))
    return reachable[0]


# ── drawing ─────────────────────────────────────────────────────────────────


async def draw(prompt: str, *, model: ImageModel, reference: bytes = b"",
               reference_mime: str = "") -> Drawn:
    """Ask for a picture. `reference` turns it into an edit of that picture."""
    if not prompt.strip():
        raise ImageError("say what the picture should be of")
    if model.route == GEMINI:
        return await _gemini(prompt, model, reference, reference_mime)
    if model.route == CHAT:
        return await _chat(prompt, model, reference, reference_mime)
    if model.route == IMAGES:
        return await _images(prompt, model, reference)
    if model.route == EITHER:
        # Nobody told us which shape this endpoint speaks, so try the one most
        # of them use for pictures and fall back to the other. Both failing is
        # reported as both failing: the model was a guess, and the guess was
        # wrong, which is worth saying rather than dressing up.
        #
        # Money is the exception, and it took a live run to notice. An account
        # that cannot pay says so on the first attempt, and asking a second way
        # gets the same refusal for a second fee -- while the "it was a guess
        # and it does not draw" wrapper buried the one sentence that was true
        # and actionable. So a money refusal ends it here, unchanged.
        try:
            return await _images(prompt, model, reference)
        except NoFunds:
            raise
        except ImageError as first:
            try:
                return await _chat(prompt, model, reference, reference_mime)
            except NoFunds:
                raise
            except ImageError as second:
                raise ImageError(
                    f"{model.name} was a guess from its name and it does not "
                    f"draw the way either kind of picture model does. Asking "
                    f"for a picture failed ({first}) and asking in a chat "
                    f"failed ({second}). Tell them plainly that this model "
                    f"cannot be used for pictures and offer another from the "
                    f"list.") from second
    raise ImageError(f"{model.provider} cannot make pictures")


def _endpoint(provider_name: str) -> tuple[str, dict]:
    """Base URL and headers for a provider, ready for a raw request."""
    from agent_server.providers import get_provider

    provider = get_provider(provider_name)
    base = (getattr(provider, "base_url", "") or "").rstrip("/")
    if not base:
        raise ImageError(f"{provider_name} has no address to send a picture "
                         f"request to")
    key = provider.api_key()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if provider_name == "openrouter":
        from agent_server.providers.openrouter import _ATTRIBUTION

        headers.update(_ATTRIBUTION)
    return base, headers


def _data_url(raw: bytes, mime: str) -> str:
    return f"data:{mime or 'image/png'};base64,{base64.b64encode(raw).decode()}"


def _from_data_url(url: str) -> tuple[bytes, str]:
    """Bytes and mime out of a `data:` URL, which is how these come back."""
    if not url.startswith("data:"):
        raise ImageError("the picture came back as a link rather than a "
                         "picture, which this does not know how to fetch")
    header, _, payload = url.partition(",")
    mime = header[5:].split(";")[0] or "image/png"
    try:
        raw = base64.b64decode(payload)
    except Exception as e:
        raise ImageError("the picture came back damaged and could not be "
                         "saved") from e
    return raw, mime


def _checked(raw: bytes, mime: str, model: ImageModel, said: str,
             usage: dict | None = None) -> Drawn:
    if not raw:
        raise ImageError("an empty picture came back")
    if len(raw) > MAX_BYTES:
        raise ImageError("the picture came back far larger than expected and "
                         "was not saved")
    return Drawn(data=raw, mime=mime or "image/png", model=model,
                 said=said.strip(), usage=usage or {})


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
        raise _failure(response.status_code, response.text)

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
        return _checked(raw, blob.get("mimeType") or blob.get("mime_type") or "",
                        model, said, data.get("usageMetadata") or {})

    # Text and no picture is nearly always a refusal, and the text says why.
    raise ImageError(_refused(said))


async def _chat(prompt: str, model: ImageModel, reference: bytes,
                reference_mime: str) -> Drawn:
    """A picture asked for in a chat request, which is how aggregators do it.

    `modalities: ["image", "text"]` on an ordinary `/chat/completions` call,
    and the picture comes back beside the reply. It is the shape OpenRouter
    uses, which means it is the shape that reaches every image model OpenRouter
    has -- including the ones that do not exist yet.
    """
    import httpx

    base, headers = _endpoint(model.provider)

    content: list[dict] = [{"type": "text", "text": prompt}]
    if reference:
        content.insert(0, {
            "type": "image_url",
            "image_url": {"url": _data_url(reference, reference_mime)},
        })

    body = {
        "model": model.id,
        "messages": [{"role": "user", "content": content}],
        "modalities": ["image", "text"],
        # Not a limit anybody needs -- a picture and a sentence about it come
        # to a few thousand tokens. It is here because aggregators hold credit
        # against whatever the reply *might* cost, and with no ceiling that is
        # the model's whole context. A real account with real money in it was
        # refused a one-cent picture on those grounds, with a message about
        # credits that read as "you are broke" when the truth was "we set aside
        # sixty-five thousand tokens for a drawing of an acorn".
        "max_tokens": MAX_REPLY_TOKENS,
    }
    async def send() -> object:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                return await client.post(f"{base}/chat/completions",
                                         headers=headers, json=body)
        except httpx.TimeoutException as e:
            raise ImageError("the picture took too long and was given up "
                             "on") from e
        except httpx.HTTPError as e:
            raise ImageError(f"could not reach {model.provider}: "
                             f"{type(e).__name__}") from e

    response = await send()

    # An account running low is refused for the ceiling, not for the picture --
    # the reply is held against the whole allowance and the picture would have
    # cost a fraction of it. This is a ten-pound top-up two thirds spent, which
    # is exactly how these accounts are funded, so it is worth one more go at
    # what is actually left rather than a flat no. Only above a floor where a
    # picture still fits: a truncated one is billed the same as a good one.
    if response.status_code != 200:
        afford = _afford(response.text)
        if MIN_REPLY_TOKENS <= afford < body["max_tokens"]:
            log.info("retrying %s within the %d tokens left", model.id, afford)
            body["max_tokens"] = afford
            response = await send()

    if response.status_code != 200:
        raise _failure(response.status_code, response.text)

    try:
        message = response.json()["choices"][0]["message"]
    except (ValueError, KeyError, IndexError) as e:
        raise ImageError("the answer came back in a shape this did not "
                         "understand, and no picture with it") from e

    said = message.get("content")
    said = said if isinstance(said, str) else ""

    # Two spellings in the wild: a separate `images` list, or image parts mixed
    # into the content array. Both are read, because which one arrives depends
    # on the upstream vendor rather than on anything asked for here.
    for image in message.get("images") or []:
        url = ((image or {}).get("image_url") or {}).get("url") or ""
        if url:
            raw, mime = _from_data_url(url)
            return _checked(raw, mime, model, said)
    if isinstance(message.get("content"), list):
        for part in message["content"]:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                said += part.get("text") or ""
            url = ((part.get("image_url") or {}).get("url")
                   if part.get("type") == "image_url" else "")
            if url:
                raw, mime = _from_data_url(url)
                return _checked(raw, mime, model, said)

    raise ImageError(_refused(said))


async def _images(prompt: str, model: ImageModel, reference: bytes) -> Drawn:
    """The `/images/generations` shape: one prompt in, one picture out.

    What a local server or a plain OpenAI-compatible endpoint is most likely to
    offer. It has no way to be handed an existing picture, so an edit asked of
    one of these says so instead of quietly drawing something new.
    """
    import httpx

    if reference:
        raise ImageError(
            f"{model.name} can draw a new picture but cannot be given one to "
            f"change. Draw a fresh one describing what is wanted, or use a "
            f"model that edits.")

    base, headers = _endpoint(model.provider)
    body = {"model": model.id, "prompt": prompt, "n": 1,
            "response_format": "b64_json"}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(f"{base}/images/generations",
                                         headers=headers, json=body)
    except httpx.TimeoutException as e:
        raise ImageError("the picture took too long and was given up on") from e
    except httpx.HTTPError as e:
        raise ImageError(f"could not reach {model.provider}: "
                         f"{type(e).__name__}") from e

    if response.status_code != 200:
        raise _failure(response.status_code, response.text)

    try:
        first = response.json()["data"][0]
    except (ValueError, KeyError, IndexError) as e:
        raise ImageError("the answer came back in a shape this did not "
                         "understand, and no picture with it") from e

    if first.get("b64_json"):
        try:
            raw = base64.b64decode(first["b64_json"])
        except Exception as e:
            raise ImageError("the picture came back damaged and could not be "
                             "saved") from e
        return _checked(raw, "image/png", model, first.get("revised_prompt") or "")

    url = first.get("url") or ""
    if url.startswith("data:"):
        raw, mime = _from_data_url(url)
        return _checked(raw, mime, model, first.get("revised_prompt") or "")
    if url:
        # Some servers hand back a link that expires in an hour. Fetching it
        # now is the difference between a picture in the project and a dead
        # link in it.
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                fetched = await client.get(url)
            fetched.raise_for_status()
        except Exception as e:
            raise ImageError("the picture was made but could not be "
                             "downloaded") from e
        return _checked(fetched.content,
                        fetched.headers.get("content-type", "").split(";")[0],
                        model, first.get("revised_prompt") or "")

    raise ImageError("no picture came back and nothing said why")


def _afford(text: str) -> int:
    """How many tokens the account can still pay for, if it said.

    Aggregators refuse against the ceiling asked for rather than the reply
    likely to arrive, and the refusal carries the real number: "You requested
    up to 8192 tokens, but can only afford 5310". That number is the difference
    between a picture and an apology.
    """
    match = re.search(r"afford\D{0,20}?(\d{2,7})", text or "", re.I)
    return int(match.group(1)) if match else 0


def _refused(said: str) -> str:
    return ((said.strip() or "no picture came back and nothing said why")
            + " -- this is the model declining rather than a fault, so change "
              "what was asked for rather than trying the same thing again.")


def _detail(text: str) -> str:
    """The provider's own sentence, dug out of whatever it wrapped it in."""
    try:
        import json

        body = json.loads(text)
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            return error.get("message", "") or text[:200]
        if isinstance(error, str):
            return error
    except Exception:
        pass
    return text[:200]


def _kind(status: int, detail: str) -> str:
    """What sort of refusal this is, decided once.

    `funds` is the one that matters, because it is the only one where the fix
    is a person doing something on a website rather than the app trying
    differently. It has to be told apart from a key that is wrong (a different
    website, a different fix) and from credit merely held against a request
    still running (no fix at all -- it clears).
    """
    lower = (detail or "").lower()
    broke = ("insufficient" in lower or "credit" in lower or "balance" in lower
             or "billing" in lower or "payment" in lower or "quota" in lower)
    if broke and "in-flight" in lower:
        return "waiting"
    if status == 402 or (status in (400, 403) and broke):
        return "funds"
    # Pictures have no free allowance on the tiers this app's users are on, so
    # a 429 on an image model is nearly always "this account has never been set
    # up to pay for pictures" rather than "you have been going too fast". Both
    # end at the same website, so both are `funds` -- the wording below is what
    # keeps the difference.
    if status == 429:
        return "funds"
    if status in (401, 403):
        return "key"
    if status == 404:
        return "gone"
    if status == 400 and "modalit" in lower:
        return "modality"
    return "other"


def _why(status: int, text: str) -> str:
    """An HTTP failure, in words worth passing on."""
    detail = _detail(text)
    kind = _kind(status, detail)

    if kind == "waiting":
        # Not out of money -- reserved against something already running. It
        # comes back on its own, and telling somebody to top up an account that
        # has money in it sends them to a payment page for nothing.
        return ("this picture would cost more than is free on the account "
                "right this second, because something else is still running "
                "against it. It clears on its own. Wait for whatever else is "
                f"going to finish, then try once more. ({detail[:160]})")
    if kind == "funds" and status == 429:
        return ("no more pictures right now -- the allowance is used up. "
                "Pictures are billed separately from text, so this happens "
                "while ordinary replies still work, and on most accounts "
                "there is no free allowance for pictures at all. It may come "
                "back on its own in a few minutes; if it does not, the account "
                f"needs money on it. ({detail[:160]})")
    if kind == "funds":
        return ("there is not enough money on this account for pictures. "
                "Pictures are paid for separately from replies, so ordinary "
                "chat can keep working while this does not. Whoever set the "
                "account up has to add funds -- for a child that is a parent "
                "or a teacher, and it is not something they can fix "
                f"themselves. ({detail[:160]})")
    if kind == "key":
        return ("the key was refused for pictures. Making pictures often has "
                "to be switched on for an account separately from text, and a "
                "key that chats happily can still be told no here. "
                f"({detail[:160]})")
    if kind == "gone":
        return ("that model is not there. It may have been renamed or "
                f"withdrawn -- ask for the list again. ({detail[:160]})")
    if kind == "modality":
        return f"that model cannot return a picture. ({detail[:160]})"
    return f"the picture was refused ({status}). {detail[:200]}"


def _failure(status: int, text: str) -> ImageError:
    """The refusal as an exception of the right sort.

    Money is its own class because the tool does something different with it:
    it puts the steps for fixing it on the screen. Everything else is prose.
    """
    message = _why(status, text)
    if _kind(status, _detail(text)) == "funds":
        return NoFunds(message)
    return ImageError(message)


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
