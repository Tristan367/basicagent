"""Which models will accept a picture, learned rather than declared.

There is no way to ask. `/v1/models` returns identifiers and nothing else on
every provider this app talks to; OpenRouter alone publishes input modalities,
which does not help the other four and does not help a custom endpoint at all.

So the previous answer was a hand-written table of model ids, which is wrong the
day a provider ships anything new and cannot be right at all for a model running
on someone's own machine.

This is the other answer: **assume a model can see, send the picture, and
believe the refusal.** A model that cannot take one says so in a 400 before a
single token is billed, and that answer is authoritative in a way a table never
is. The turn is retried immediately without the picture, so the user sees a
reply rather than an error, and the model is remembered so the cost is paid once
ever rather than once a turn.

The failure this is designed around is real and worse than a wasted request:
DeepSeek, handed a path it cannot open, will describe the picture anyway and
admit three turns later that it never saw one. Once the refusal is on record the
model is told, in the message itself, that a picture is there and it cannot see
it -- which is the only thing that stops the invention.
"""

from __future__ import annotations

import json
import logging
import re

log = logging.getLogger(__name__)

SETTING = "models_without_images"

# Populated from the database on first use and kept for the life of the process.
_text_only: set[str] = set()
_loaded = False


async def _ensure_loaded():
    global _loaded
    if _loaded:
        return
    from agent_server import database as db

    raw = await db.get_setting(SETTING, "")
    if raw:
        try:
            stored = json.loads(raw)
            if isinstance(stored, list):
                _text_only.update(str(m) for m in stored)
        except (json.JSONDecodeError, TypeError):
            log.info("could not read %s, starting over", SETTING)
    _loaded = True


async def accepts_images(model: str) -> bool:
    """Whether to put pictures in a request to this model.

    Optimistic by default. Being wrong costs one refused request, once; being
    wrong the other way costs the user every picture they ever attach.
    """
    await _ensure_loaded()
    return (model or "") not in _text_only


async def remember_refusal(model: str):
    """Record that this model will not take a picture."""
    if not model or model in _text_only:
        return
    await _ensure_loaded()
    _text_only.add(model)
    from agent_server import database as db

    await db.set_setting(SETTING, json.dumps(sorted(_text_only)))
    log.info("%s refused a picture; it will not be sent one again", model)


async def forget(model: str = ""):
    """Clear what was learned, for one model or all of them.

    Here because the learning is a guess about somebody else's server. A model
    that gains image support, or a custom endpoint that was simply misconfigured
    on the day, must have a way back that is not editing the database by hand.
    """
    await _ensure_loaded()
    if model:
        _text_only.discard(model)
    else:
        _text_only.clear()
    from agent_server import database as db

    await db.set_setting(SETTING, json.dumps(sorted(_text_only)))


# Matched only against a request that actually carried a picture, which is what
# makes a rule this loose safe: nothing else in the turn is about images.
_REFUSAL = re.compile(
    r"image|multimodal|modalit|vision|content type|content_type|image_url",
    re.IGNORECASE,
)
# ...except the ways an accepted picture can still fail. Marking a model
# text-only because one photograph was too big would lose every later picture
# for a reason that had nothing to do with the model.
_NOT_A_REFUSAL = re.compile(
    r"too large|exceeds|maximum size|max_size|too many|rate limit|timeout|"
    r"too long|payload|dimension|invalid_api_key|authentication|credit|quota",
    re.IGNORECASE,
)


def looks_like_a_refusal(message: str) -> bool:
    """Whether this error means "I do not take pictures" rather than something else."""
    text = message or ""
    if _NOT_A_REFUSAL.search(text):
        return False
    return bool(_REFUSAL.search(text))
