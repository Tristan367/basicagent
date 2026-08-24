"""Finding out what can draw, on a computer nobody here has seen.

The hand-checked list is Google's, because that is what this app's users will
have. Everything in this file is about the other case: somebody on OpenRouter,
somebody running Stable Diffusion on a box in the corner, somebody using a
vendor that did not exist when this was written. The app has to be able to draw
for them too, and it has to be honest about what it does not know -- an
invented price is worse than an admitted gap, because the person paying for
these is usually not the person asking for them.

The OpenRouter and Google shapes below were copied from the live endpoints, not
imagined.
"""

from __future__ import annotations

import asyncio
import base64

import pytest

from agent_server import imagegen
from agent_server.tools.base import ToolContext
from agent_server.tools.draw import draw


@pytest.fixture(autouse=True)
def clean_cache():
    imagegen.forget_catalogue()
    yield
    imagegen.forget_catalogue()


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(session_id="d", project_dir=str(tmp_path), abort=asyncio.Event())


class FakeProvider:
    def __init__(self, name="Fake", base_url="https://example.test/v1", key="k",
                 credentials=None):
        self.name = name
        self.base_url = base_url
        self._key = key
        # A local server has no key to give and is usable anyway, which is
        # exactly what `CustomOpenAIProvider.has_credentials` says.
        self._usable = bool(key) if credentials is None else credentials

    def has_credentials(self):
        return self._usable

    def api_key(self):
        return self._key


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)
        self.headers = {"content-type": "image/png"}
        self.content = b"\x89PNG" + b"x" * 40

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def fake_httpx(monkeypatch, handler):
    """Answer every request through `handler(method, url, kwargs)`."""
    import httpx

    calls = []

    class Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            calls.append(("GET", url, kw))
            return handler("GET", url, kw)

        async def post(self, url, **kw):
            calls.append(("POST", url, kw))
            return handler("POST", url, kw)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    return calls


def only_providers(monkeypatch, **providers):
    from agent_server import providers as registry

    monkeypatch.setattr(registry, "_providers", dict(providers))


# What the live endpoints actually return, trimmed.
OPENROUTER = {"data": [
    {"id": "openrouter/auto", "name": "Auto Router",
     "architecture": {"output_modalities": ["text", "image"],
                      "input_modalities": ["text", "image"]},
     "pricing": {"prompt": "-1", "completion": "-1"}},
    {"id": "google/gemini-3-pro-image", "name": "Gemini 3 Pro Image",
     "architecture": {"output_modalities": ["text", "image"],
                      "input_modalities": ["text", "image"]},
     "pricing": {"prompt": "0.000002", "image_output": "0.00012"}},
    {"id": "openai/gpt-5-image-mini", "name": "GPT-5 Image Mini",
     "architecture": {"output_modalities": ["text", "image"],
                      "input_modalities": ["text", "image"]},
     "pricing": {"prompt": "0.0000025", "image_output": "0.000008"}},
    {"id": "brand-new-lab/marvel-1", "name": "Marvel 1",
     "architecture": {"output_modalities": ["image"],
                      "input_modalities": ["text"]},
     "pricing": {"prompt": "0.000001", "image_output": "0.00002"}},
    {"id": "anthropic/claude-opus-5", "name": "Claude Opus 5",
     "architecture": {"output_modalities": ["text"],
                      "input_modalities": ["text", "image"]},
     "pricing": {"prompt": "0.000005", "completion": "0.000025"}},
]}

GOOGLE = {"models": [
    {"name": "models/gemini-3.1-flash", "displayName": "Gemini 3.1 Flash",
     "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-2.5-flash-image", "displayName": "Nano Banana",
     "supportedGenerationMethods": ["generateContent", "countTokens"]},
    {"name": "models/gemini-9-flash-image", "displayName": "Nano Banana IX",
     "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/imagen-4.0-generate", "displayName": "Imagen 4",
     "supportedGenerationMethods": ["predict"]},
]}


# ── recognising one by its name, which is all a plain endpoint gives ────────


def test_the_names_that_mean_it_draws():
    for name in ("flux-1.1-pro", "stable-diffusion-xl", "dall-e-3",
                 "gpt-image-1", "gemini-3.1-flash-image", "sdxl-turbo",
                 "seedream-4", "nano-banana", "qwen-image-edit"):
        assert imagegen.looks_like_it_draws(name), name


def test_the_names_that_only_look_like_it():
    """These all have a picture word in them and none of them draws: they read
    pictures, score them, or turn them into numbers. Offering one costs a round
    trip and somebody's confidence."""
    for name in ("clip-image-embed", "image-moderation-latest",
                 "llava-vision-7b", "qwen2-vl-7b", "image-caption-base",
                 "gemini-3.1-flash", "deepseek-chat"):
        assert not imagegen.looks_like_it_draws(name), name


# ── OpenRouter, which answers the question properly ────────────────────────


async def test_a_model_nobody_here_has_heard_of_is_found_anyway(monkeypatch):
    """The whole point. `marvel-1` does not exist, is from a lab that does not
    exist, and is not in any list in this repository -- and it turns up with a
    price and a way to call it, because OpenRouter says it outputs images and
    that is a fact rather than a guess."""
    fake_httpx(monkeypatch, lambda *a: FakeResponse(OPENROUTER))
    only_providers(monkeypatch, openrouter=FakeProvider("OpenRouter"))

    found = await imagegen.catalogue()
    marvel = [m for m in found if m.id == "brand-new-lab/marvel-1"]
    assert marvel, [m.id for m in found]
    assert marvel[0].sure
    assert marvel[0].route == imagegen.CHAT
    assert marvel[0].priced


async def test_a_text_only_model_is_not_offered_as_a_drawing_one(monkeypatch):
    fake_httpx(monkeypatch, lambda *a: FakeResponse(OPENROUTER))
    only_providers(monkeypatch, openrouter=FakeProvider("OpenRouter"))

    found = await imagegen.catalogue()
    assert not [m for m in found if "claude" in m.id]


async def test_the_router_is_left_out(monkeypatch):
    """`openrouter/auto` picks a model per request and prices at "-1". Asking
    it for a picture is a lottery with somebody else's money."""
    fake_httpx(monkeypatch, lambda *a: FakeResponse(OPENROUTER))
    only_providers(monkeypatch, openrouter=FakeProvider("OpenRouter"))

    found = await imagegen.catalogue()
    assert not [m for m in found if m.id.startswith("openrouter/")]


async def test_a_quoted_token_price_becomes_a_price_per_picture(monkeypatch):
    """Aggregators quote per-token; people think per-picture. $0.00012 a token
    times a 1290-token picture is the $0.15 that shows on the bill."""
    fake_httpx(monkeypatch, lambda *a: FakeResponse(OPENROUTER))
    only_providers(monkeypatch, openrouter=FakeProvider("OpenRouter"))

    found = await imagegen.catalogue()
    pro = next(m for m in found if m.id == "google/gemini-3-pro-image")
    assert 0.10 < pro.about_each < 0.20


# ── Google, which does not ─────────────────────────────────────────────────


async def test_a_new_google_model_is_found_by_its_name(monkeypatch):
    """Google's list says nothing about modalities, so the name is the signal.
    `gemini-9-flash-image` is not in any list here and is found anyway."""
    fake_httpx(monkeypatch, lambda *a: FakeResponse(GOOGLE))
    only_providers(monkeypatch, gemini=FakeProvider("Google Gemini"))

    found = await imagegen.catalogue()
    new = [m for m in found if m.id == "gemini-9-flash-image"]
    assert new, [m.id for m in found]
    assert new[0].name == "Nano Banana IX", "the display name is nicer than the id"
    assert new[0].route == imagegen.GEMINI


async def test_a_google_model_that_cannot_be_called_that_way_is_skipped(monkeypatch):
    """Imagen is on `predict`, not `generateContent`. Offering it would be
    offering something this cannot actually ask."""
    fake_httpx(monkeypatch, lambda *a: FakeResponse(GOOGLE))
    only_providers(monkeypatch, gemini=FakeProvider("Google Gemini"))

    found = await imagegen.catalogue()
    assert not [m for m in found if "imagen" in m.id]


async def test_a_found_model_with_no_published_price_says_so(monkeypatch):
    fake_httpx(monkeypatch, lambda *a: FakeResponse(GOOGLE))
    only_providers(monkeypatch, gemini=FakeProvider("Google Gemini"))

    found = await imagegen.catalogue()
    new = next(m for m in found if m.id == "gemini-9-flash-image")
    assert not new.priced
    assert "unknown" in new.note.lower()


# ── a plain endpoint, where everything is a guess ──────────────────────────


async def test_a_local_server_is_offered_as_a_guess(monkeypatch):
    """Somebody running Stable Diffusion behind an OpenAI-compatible proxy. Its
    list says ids and nothing else, so this is guesswork and has to be sold as
    guesswork."""
    fake_httpx(monkeypatch, lambda *a: FakeResponse(
        {"data": [{"id": "sdxl-turbo"}, {"id": "llama-3-8b"}]}))
    only_providers(monkeypatch, **{"custom:box": FakeProvider(
        "box", "http://192.168.0.9:8888/v1", "", credentials=True)})

    found = await imagegen.catalogue()
    assert [m.id for m in found] == ["sdxl-turbo"]
    assert not found[0].sure
    assert found[0].route == imagegen.EITHER
    assert not found[0].priced


async def test_a_guess_is_never_ranked_above_a_certainty(monkeypatch):
    def handler(method, url, kw):
        if "openrouter" in url:
            return FakeResponse(OPENROUTER)
        return FakeResponse({"data": [{"id": "sdxl-turbo"}]})

    fake_httpx(monkeypatch, handler)
    only_providers(monkeypatch, openrouter=FakeProvider("OpenRouter"),
                   **{"custom:box": FakeProvider("box", "http://box/v1", "", credentials=True)})

    found = await imagegen.catalogue()
    assert found[-1].id == "sdxl-turbo"
    assert all(m.sure for m in found[:-1])


# ── the hand-checked half still wins ───────────────────────────────────────


async def test_the_checked_models_come_first(monkeypatch):
    """Somebody with both keys should get the model that has been driven, at
    the price somebody read off a pricing page -- not an aggregator's copy of
    it with a markup and a different name."""
    def handler(method, url, kw):
        return FakeResponse(OPENROUTER if "openrouter" in url else GOOGLE)

    fake_httpx(monkeypatch, handler)
    only_providers(monkeypatch, gemini=FakeProvider("Google Gemini"),
                   openrouter=FakeProvider("OpenRouter"))

    found = await imagegen.catalogue()
    assert found[0].id in imagegen.IMAGE_MODELS_BY_ID


async def test_the_same_model_is_not_listed_twice(monkeypatch):
    """`gemini-3-pro-image` from Google and `google/gemini-3-pro-image` from an
    aggregator are one model. Listed twice, somebody wonders which is the good
    one."""
    def handler(method, url, kw):
        return FakeResponse(OPENROUTER if "openrouter" in url else GOOGLE)

    fake_httpx(monkeypatch, handler)
    only_providers(monkeypatch, gemini=FakeProvider("Google Gemini"),
                   openrouter=FakeProvider("OpenRouter"))

    found = await imagegen.catalogue()
    pro = [m for m in found if m.id.endswith("gemini-3-pro-image")]
    assert len(pro) == 1
    assert pro[0].provider == "gemini", "the checked one, not the aggregator's"


async def test_the_same_model_under_a_second_name_is_not_listed_twice(monkeypatch):
    """Google serves Nano Banana Pro as `gemini-3-pro-image` and again as
    `nano-banana-pro-preview`. The ids have nothing in common; the display name
    is the only thing tying them together, and without it the list showed the
    same model twice -- once with a price and once without. Found live."""
    fake_httpx(monkeypatch, lambda *a: FakeResponse({"models": [
        {"name": "models/nano-banana-pro-preview",
         "displayName": "Nano Banana Pro",
         "supportedGenerationMethods": ["generateContent"]},
    ]}))
    only_providers(monkeypatch, gemini=FakeProvider("Google Gemini"))

    found = await imagegen.catalogue()
    assert [m.name for m in found].count("Nano Banana Pro") == 1
    assert next(m for m in found if m.name == "Nano Banana Pro").priced


async def test_a_provider_that_will_not_answer_breaks_nothing(monkeypatch):
    """Discovery is a nicety. A provider that is down, slow, or rate-limiting
    must not take the drawing this computer can definitely do down with it."""
    def handler(method, url, kw):
        raise RuntimeError("no")

    fake_httpx(monkeypatch, handler)
    only_providers(monkeypatch, gemini=FakeProvider("Google Gemini"))

    found = await imagegen.catalogue()
    assert [m.id for m in found] == [m.id for m in imagegen.IMAGE_MODELS]


async def test_nothing_is_offered_from_a_provider_with_no_key(monkeypatch):
    fake_httpx(monkeypatch, lambda *a: FakeResponse(GOOGLE))
    only_providers(monkeypatch, gemini=FakeProvider("Google Gemini", key=""))

    assert await imagegen.catalogue() == []
    assert imagegen.curated() == []


# ── asking twice does not ask twice ────────────────────────────────────────


async def test_the_answer_is_remembered(monkeypatch):
    calls = fake_httpx(monkeypatch, lambda *a: FakeResponse(GOOGLE))
    only_providers(monkeypatch, gemini=FakeProvider("Google Gemini"))

    await imagegen.catalogue()
    await imagegen.catalogue()
    assert len(calls) == 1


async def test_a_new_key_is_noticed_at_once(monkeypatch):
    """Somebody who has just pasted a key in should not wait a quarter of an
    hour to be told they can draw now -- that is the same "start a new session"
    trap in slower clothing."""
    calls = fake_httpx(monkeypatch, lambda *a: FakeResponse(GOOGLE))
    only_providers(monkeypatch, gemini=FakeProvider("Google Gemini"))

    await imagegen.catalogue()
    imagegen.forget_catalogue()
    await imagegen.catalogue()
    assert len(calls) == 2


async def test_a_provider_appearing_invalidates_the_answer(monkeypatch):
    """No explicit invalidation needed: the answer is remembered against which
    providers had keys, and that set changed."""
    def handler(method, url, kw):
        return FakeResponse(OPENROUTER if "openrouter" in url else GOOGLE)

    fake_httpx(monkeypatch, handler)
    only_providers(monkeypatch, gemini=FakeProvider("Google Gemini"))
    first = await imagegen.catalogue()

    only_providers(monkeypatch, gemini=FakeProvider("Google Gemini"),
                   openrouter=FakeProvider("OpenRouter"))
    second = await imagegen.catalogue()
    assert len(second) > len(first)


# ── choosing one ───────────────────────────────────────────────────────────


async def test_a_bare_name_off_an_aggregator_is_understood(monkeypatch):
    """Somebody types "marvel-1", not "brand-new-lab/marvel-1"."""
    fake_httpx(monkeypatch, lambda *a: FakeResponse(OPENROUTER))
    only_providers(monkeypatch, openrouter=FakeProvider("OpenRouter"))

    chosen = await imagegen.pick("marvel-1")
    assert chosen.id == "brand-new-lab/marvel-1"


async def test_asking_for_one_that_is_not_there_lists_what_is(monkeypatch):
    fake_httpx(monkeypatch, lambda *a: FakeResponse(GOOGLE))
    only_providers(monkeypatch, gemini=FakeProvider("Google Gemini"))

    with pytest.raises(imagegen.ImageError) as e:
        await imagegen.pick("midjourney")
    assert "Nano Banana" in str(e.value)


# ── the three answers to "what can draw?" ──────────────────────────────────


async def test_with_something_certain_it_chooses_rather_than_asking(ctx, monkeypatch):
    """A child asked for a picture, not a menu of models they have never heard
    of. If Google is there, decide for them."""
    fake_httpx(monkeypatch, lambda *a: FakeResponse(GOOGLE))
    only_providers(monkeypatch, gemini=FakeProvider("Google Gemini"))

    result = await draw(ctx)
    assert not result.is_error
    assert "Pick one yourself" in result.output
    assert "not a menu" in result.output


async def test_with_only_guesses_it_asks(ctx, monkeypatch):
    """Nothing here is known to work and nothing knows what it costs. That is
    a decision to put to the person paying, not one to make for them."""
    fake_httpx(monkeypatch, lambda *a: FakeResponse({"data": [{"id": "sdxl-turbo"}]}))
    only_providers(monkeypatch, **{"custom:box": FakeProvider(
        "box", "http://box/v1", "", credentials=True)})

    result = await draw(ctx)
    assert not result.is_error
    assert "not certain" in result.output
    assert "Let them choose" in result.output
    assert "sdxl-turbo" in result.output


async def test_with_nothing_at_all_it_says_what_would_fix_it(ctx, monkeypatch):
    fake_httpx(monkeypatch, lambda *a: FakeResponse({"data": []}))
    only_providers(monkeypatch, **{"custom:box": FakeProvider(
        "box", "http://box/v1", "", credentials=True)})

    result = await draw(ctx)
    assert not result.is_error
    assert "Google key" in result.output and "Settings" in result.output


async def test_an_unknown_price_is_never_dressed_up_as_a_known_one(ctx, monkeypatch):
    fake_httpx(monkeypatch, lambda *a: FakeResponse({"data": [{"id": "sdxl-turbo"}]}))
    only_providers(monkeypatch, **{"custom:box": FakeProvider(
        "box", "http://box/v1", "", credentials=True)})

    result = await draw(ctx)
    assert "$0.00" not in result.output
    assert "nobody here knows what it costs" in result.output


# ── the wire shapes ────────────────────────────────────────────────────────


PIXEL = base64.b64encode(b"\x89PNG" + b"z" * 60).decode()


async def test_a_chat_model_returns_its_picture_beside_the_reply(monkeypatch):
    """The aggregator shape: an ordinary chat request with an image modality,
    and the picture arrives in `message.images`."""
    fake_httpx(monkeypatch, lambda *a: FakeResponse({"choices": [{"message": {
        "content": "here you go",
        "images": [{"image_url": {"url": f"data:image/png;base64,{PIXEL}"}}],
    }}]}))
    only_providers(monkeypatch, openrouter=FakeProvider("OpenRouter"))

    model = imagegen.ImageModel("x/y", "Y", "openrouter", 0.02, route=imagegen.CHAT)
    drawn = await imagegen.draw("a dragon", model=model)
    assert drawn.data.startswith(b"\x89PNG")
    assert drawn.mime == "image/png"
    assert drawn.said == "here you go"


async def test_a_chat_model_that_puts_the_picture_in_the_content_array(monkeypatch):
    """The other spelling. Which one arrives depends on the upstream vendor
    rather than on anything asked for here, so both are read."""
    fake_httpx(monkeypatch, lambda *a: FakeResponse({"choices": [{"message": {
        "content": [
            {"type": "text", "text": "done"},
            {"type": "image_url",
             "image_url": {"url": f"data:image/webp;base64,{PIXEL}"}},
        ],
    }}]}))
    only_providers(monkeypatch, openrouter=FakeProvider("OpenRouter"))

    model = imagegen.ImageModel("x/y", "Y", "openrouter", 0.02, route=imagegen.CHAT)
    drawn = await imagegen.draw("a dragon", model=model)
    assert drawn.extension == ".webp"


async def test_the_generations_shape(monkeypatch):
    fake_httpx(monkeypatch, lambda *a: FakeResponse({"data": [{"b64_json": PIXEL}]}))
    only_providers(monkeypatch, **{"custom:box": FakeProvider(
        "box", "http://box/v1", "", credentials=True)})

    model = imagegen.ImageModel("sdxl", "sdxl", "custom:box", route=imagegen.IMAGES)
    drawn = await imagegen.draw("a dragon", model=model)
    assert drawn.data.startswith(b"\x89PNG")


async def test_a_model_that_cannot_be_given_a_picture_says_so(monkeypatch):
    """`/images/generations` has no way to take one. Quietly drawing something
    new instead is the failure that looks like success."""
    fake_httpx(monkeypatch, lambda *a: FakeResponse({"data": [{"b64_json": PIXEL}]}))
    only_providers(monkeypatch, **{"custom:box": FakeProvider(
        "box", "http://box/v1", "", credentials=True)})

    model = imagegen.ImageModel("sdxl", "sdxl", "custom:box", route=imagegen.IMAGES)
    with pytest.raises(imagegen.ImageError) as e:
        await imagegen.draw("green", model=model, reference=b"old")
    assert "cannot be given one to change" in str(e.value)


async def test_a_guessed_model_tries_both_ways_then_owns_up(monkeypatch):
    """It was a guess from a name. When the guess is wrong, say the guess was
    wrong -- do not report it as though the picture nearly worked."""
    fake_httpx(monkeypatch, lambda *a: FakeResponse({"error": "nope"}, status=404))
    only_providers(monkeypatch, **{"custom:box": FakeProvider(
        "box", "http://box/v1", "", credentials=True)})

    model = imagegen.ImageModel("sdxl", "sdxl", "custom:box", route=imagegen.EITHER,
                                sure=False)
    with pytest.raises(imagegen.ImageError) as e:
        await imagegen.draw("a dragon", model=model)
    assert "was a guess from its name" in str(e.value)


async def test_no_money_stops_the_guessing_instead_of_being_buried(monkeypatch):
    """Found by running it. A guessed model tries two wire shapes, and wrapping
    both failures said "this model does not draw" over the top of the one
    sentence that was true and fixable -- while paying a second time to be told
    the same thing. Money is not a wrong-shape problem, so it ends it."""
    tries = []

    def handler(method, url, kw):
        tries.append(url)
        return FakeResponse(
            {"error": {"message": "Insufficient credits on this account."}},
            status=402)

    fake_httpx(monkeypatch, handler)
    only_providers(monkeypatch, **{"custom:box": FakeProvider(
        "box", "http://box/v1", "", credentials=True)})

    model = imagegen.ImageModel("dream-image-1", "dream-image-1", "custom:box",
                                route=imagegen.EITHER, sure=False)
    with pytest.raises(imagegen.NoFunds) as e:
        await imagegen.draw("a dragon", model=model)
    assert len(tries) == 1, "it paid to be told the same thing twice"
    assert "was a guess from its name" not in str(e.value)
    assert "not enough money" in str(e.value)


async def test_a_picture_returned_as_a_link_is_fetched_now(monkeypatch):
    """Some servers hand back a URL that expires in an hour. Fetching it now is
    the difference between a picture in the project and a dead link in it."""
    fake_httpx(monkeypatch, lambda m, url, kw: FakeResponse(
        {"data": [{"url": "https://cdn.test/pic.png"}]})
        if "generations" in url else FakeResponse({}))
    only_providers(monkeypatch, **{"custom:box": FakeProvider(
        "box", "http://box/v1", "", credentials=True)})

    model = imagegen.ImageModel("sdxl", "sdxl", "custom:box", route=imagegen.IMAGES)
    drawn = await imagegen.draw("a dragon", model=model)
    assert drawn.data.startswith(b"\x89PNG")


# ── running out of money, said so a child is not blamed for it ─────────────


def test_no_funds_is_told_apart_from_a_broken_key():
    said = imagegen._why(402, '{"error":{"message":"insufficient credits"}}')
    assert "not enough money" in said
    assert "parent" in said or "teacher" in said
    assert "not something they can fix themselves" in said


def test_a_billing_refusal_dressed_as_a_403_is_still_a_billing_refusal():
    said = imagegen._why(403, '{"error":{"message":"Billing is not enabled"}}')
    assert "not enough money" in said


def test_a_plain_403_is_still_about_the_key():
    assert "key was refused" in imagegen._why(403, '{"error":{"message":"bad key"}}')


def test_a_withdrawn_model_says_to_ask_for_the_list_again():
    """Model ids are renamed and retired constantly, and this list is now found
    rather than fixed -- so the fix is to look again, not to give up."""
    assert "ask for the list again" in imagegen._why(404, "{}")


# ── an account running low, which is how these are funded ──────────────────


def test_the_number_still_affordable_is_read_out_of_the_refusal():
    """Found live, on a real account: "You requested up to 8192 tokens, but can
    only afford 5310". That number is the difference between a picture and an
    apology."""
    assert imagegen._afford(
        "This request requires more credits, or fewer max_tokens. You "
        "requested up to 8192 tokens, but can only afford 5310.") == 5310
    assert imagegen._afford("something else entirely") == 0


async def test_a_low_balance_gets_one_more_go_at_what_is_left(monkeypatch):
    """A ten-pound top-up two thirds spent is exactly how a child's account
    looks. Refusing a one-cent picture because the *ceiling* was unaffordable
    would be refusing over money that is sitting right there."""
    asked = []

    def handler(method, url, kw):
        if "chat/completions" not in url:
            return FakeResponse(OPENROUTER)
        asked.append(kw["json"]["max_tokens"])
        if len(asked) == 1:
            return FakeResponse(
                {"error": {"message": "You requested up to 8192 tokens, but "
                                      "can only afford 5310."}}, status=402)
        return FakeResponse({"choices": [{"message": {
            "content": "",
            "images": [{"image_url": {"url": f"data:image/png;base64,{PIXEL}"}}],
        }}]})

    fake_httpx(monkeypatch, handler)
    only_providers(monkeypatch, openrouter=FakeProvider("OpenRouter"))

    model = imagegen.ImageModel("x/y", "Y", "openrouter", 0.01, route=imagegen.CHAT)
    drawn = await imagegen.draw("an acorn", model=model)
    assert drawn.data.startswith(b"\x89PNG")
    assert asked == [imagegen.MAX_REPLY_TOKENS, 5310]


async def test_too_little_left_for_a_picture_is_not_tried_anyway(monkeypatch):
    """A truncated picture is billed at the same rate as a good one. Spending
    the last of somebody's money on one is worse than saying so."""
    asked = []

    def handler(method, url, kw):
        if "chat/completions" not in url:
            return FakeResponse(OPENROUTER)
        asked.append(kw["json"]["max_tokens"])
        return FakeResponse(
            {"error": {"message": "You requested up to 8192 tokens, but can "
                                  "only afford 300."}}, status=402)

    fake_httpx(monkeypatch, handler)
    only_providers(monkeypatch, openrouter=FakeProvider("OpenRouter"))

    model = imagegen.ImageModel("x/y", "Y", "openrouter", 0.01, route=imagegen.CHAT)
    with pytest.raises(imagegen.ImageError) as e:
        await imagegen.draw("an acorn", model=model)
    assert len(asked) == 1, "it tried anyway"
    assert "not enough money" in str(e.value)


def test_credit_held_against_something_still_running_is_not_being_broke():
    """Telling somebody to top up an account that has money in it sends them
    to a payment page for nothing."""
    said = imagegen._why(402, '{"error":{"message":"This request would exceed '
                              'your available credits given your current '
                              'in-flight requests."}}')
    assert "clears on its own" in said
    assert "parent" not in said


def test_a_ceiling_is_always_sent(monkeypatch):
    """Without one the aggregator holds credit against the model\'s whole
    context -- 65,536 tokens set aside for a drawing of an acorn, and a real
    account with real money in it refused a one-cent picture."""
    import inspect

    source = inspect.getsource(imagegen._chat)
    assert "max_tokens" in source
