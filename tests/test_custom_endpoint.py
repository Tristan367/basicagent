"""Custom endpoints: the key that is not a key, and the model nobody types.

Two bugs live here.

The first cost a user their key four times over. An endpoint was saved whose
`api_key` column held its own `base_url`, character for character -- so every
message came back 401, and the settings page reported "saved, but it answered
401", which reads as "your key is wrong" when the key was never the problem.
The save path could not be made to do it in a test or a scripted browser, which
is exactly why the guard is an equality check rather than another theory: two
fields of one submission being byte-identical has no legitimate reading,
whoever put the value there.

The second was asking the user for a model id at all. A custom endpoint serves
one model, chosen by whoever started the server; there is no menu to pick from
and no way to switch without restarting it. The endpoint's name is the model.
"""

from typing import ClassVar

import pytest

from agent_server.routes.settings import _check_endpoint, save_custom_endpoint


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"data": []}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _Client:
    """Stands in for httpx.AsyncClient. Records what it was asked."""

    calls: ClassVar[list] = []
    response = _Response()

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        type(self).calls.append((url, headers or {}))
        return type(self).response


@pytest.fixture
def endpoint_http(monkeypatch):
    import httpx

    _Client.calls = []
    _Client.response = _Response(200, {"data": [{"id": "qwen3-coder-next-80b"}]})
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return _Client


# ── the key that was the address ───────────────────────────────────────────


async def test_a_key_identical_to_the_address_is_refused(db, endpoint_http):
    url = "http://192.168.0.9:8888/v1"
    await db.save_custom_endpoint("llm1", url, "the-real-key", "m")

    response = await save_custom_endpoint(name="llm1", base_url=url, api_key=url)

    assert "error=key_is_address" in response.headers["location"]
    row = await db.get_custom_endpoint("llm1")
    assert row["api_key"] == "the-real-key", "the good key must survive a bad save"


async def test_a_key_that_merely_looks_urlish_is_saved(db, endpoint_http):
    """The guard replaced a heuristic that read the *shape* of the value and
    refused anything URL-like. It blocked a real key -- a local rig's key can
    be any string at all -- and blocking someone from entering a working key is
    worse than the mistake it was trying to prevent."""
    key = "http-relay-8888-v1-token"
    await save_custom_endpoint(
        name="llm1", base_url="http://192.168.0.9:8888/v1", api_key=key
    )
    row = await db.get_custom_endpoint("llm1")
    assert row["api_key"] == key


async def test_an_empty_key_box_keeps_the_saved_key(db, endpoint_http):
    """The box shows the ends of the saved key rather than its value, so
    leaving it alone has to mean leaving the key alone."""
    await db.save_custom_endpoint("llm1", "http://box:8888/v1", "keep-me", "m")
    await save_custom_endpoint(name="llm1", base_url="http://box:9999/v1", api_key="")
    row = await db.get_custom_endpoint("llm1")
    assert row["api_key"] == "keep-me"
    assert row["base_url"] == "http://box:9999/v1"


async def test_the_settings_page_flags_a_row_saved_before_the_guard(db):
    """A row already holding the address as its key predates the check. Left
    alone it looks configured and fails on every message."""
    from agent_server.routes.context import _settings_context

    url = "http://192.168.0.9:8888/v1"
    await db.save_custom_endpoint("llm1", url, url, "")
    rows = (await _settings_context())["custom_endpoints"]
    assert rows[0]["key_is_url"] is True

    await db.save_custom_endpoint("llm1", url, "a-real-key", "")
    rows = (await _settings_context())["custom_endpoints"]
    assert rows[0]["key_is_url"] is False


async def test_an_endpoint_with_no_key_is_not_flagged(db):
    """A local Ollama or vLLM has no key to give, and empty is not the address."""
    from agent_server.routes.context import _settings_context

    await db.save_custom_endpoint("ollama", "http://localhost:11434/v1", "", "")
    rows = (await _settings_context())["custom_endpoints"]
    assert rows[0]["key_is_url"] is False


# ── the model nobody types ─────────────────────────────────────────────────


async def test_saving_asks_the_endpoint_which_model_it_serves(db, endpoint_http):
    await save_custom_endpoint(
        name="llm1", base_url="http://192.168.0.9:8888/v1", api_key="k"
    )
    url, headers = endpoint_http.calls[0]
    assert url == "http://192.168.0.9:8888/v1/models"
    assert headers["Authorization"] == "Bearer k"
    row = await db.get_custom_endpoint("llm1")
    assert row["model_id"] == "qwen3-coder-next-80b"


async def test_an_endpoint_that_was_switched_off_can_be_asked_later(db, endpoint_http):
    """Saving an endpoint while the rig is off must still work; the model id is
    discovered on first use instead."""
    from agent_server.providers.custom_openai import CustomOpenAIProvider

    endpoint_http.response = _Response(503)
    await save_custom_endpoint(
        name="llm1", base_url="http://192.168.0.9:8888/v1", api_key="k"
    )
    assert (await db.get_custom_endpoint("llm1"))["model_id"] == ""

    endpoint_http.response = _Response(200, {"data": [{"id": "qwen3-coder-next-80b"}]})
    provider = CustomOpenAIProvider("llm1", "http://192.168.0.9:8888/v1", "k", "")
    assert await provider._discover_model() == "qwen3-coder-next-80b"
    assert (await db.get_custom_endpoint("llm1"))["model_id"] == "qwen3-coder-next-80b"


async def test_the_endpoint_key_is_never_sent_as_a_model_name(db, endpoint_http):
    """The session stores `custom:llm1` as its model because to the user the
    endpoint is the model. Sending that string to the server as a model name is
    what used to make every request fail."""
    import agent_server.providers.openai_compat as compat
    from agent_server.providers.custom_openai import CustomOpenAIProvider

    provider = CustomOpenAIProvider("llm1", "http://box:8888/v1", "k", "served-model")
    sent = {}

    async def fake_chat(self, messages, tools, model, thinking_effort=None):
        sent["model"] = model
        return
        yield  # pragma: no cover -- makes this an async generator

    original = compat.OpenAICompatibleProvider.chat_completion
    compat.OpenAICompatibleProvider.chat_completion = fake_chat
    try:
        async for _ in provider.chat_completion([], [], "custom:llm1"):
            pass
    finally:
        compat.OpenAICompatibleProvider.chat_completion = original

    assert sent["model"] == "served-model"


# ── what the endpoint said ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status, payload, expected",
    [
        (200, {"data": [{"id": "a"}, {"id": "b"}]}, "ok2"),
        (401, {}, "auth401"),
        (403, {}, "auth403"),
        (404, {}, "http404"),
    ],
)
async def test_the_check_reports_what_happened_not_a_verdict(
    db, endpoint_http, status, payload, expected
):
    """A 401 usually means the key, but some servers answer that way to any
    path they do not recognise -- so the message says what came back rather
    than declaring the key wrong and sending someone hunting."""
    endpoint_http.response = _Response(status, payload)
    result, _ = await _check_endpoint("http://box:8888/v1", "k")
    assert result == expected


# ── a box running more than one model ──────────────────────────────────────


async def _offered(db, models):
    from agent_server.model_catalog import offerable_models
    from agent_server.providers import load_custom_endpoint_providers

    await db.save_custom_endpoint(
        "llm1", "http://192.168.0.9:8888/v1", "k", models[0] if models else "", models
    )
    await load_custom_endpoint_providers()
    return [m for m in offerable_models() if m["provider"].startswith("custom:")]


async def test_one_model_is_listed_under_the_endpoint_name(db):
    """The user named the box; to them the box is the model."""
    offered = await _offered(db, ["Qwen3-Coder-Next-UD-Q3_K_XL"])
    assert [(m["id"], m["name"]) for m in offered] == [("custom:llm1", "llm1")]


async def test_several_models_are_still_one_entry(db):
    """An endpoint that lists its variants is still one choice.

    We used to offer each reported model separately. But that list is not a
    menu -- it is Unsloth's habit of naming what it holds, and vLLM or
    llama.cpp will not swap what they have loaded because we asked. Picking a
    second entry changed the id in the request body and nothing at the far end,
    so the same model answered under a different name."""
    served = ["Qwen3-Coder-Next-UD-Q3_K_XL", "flux2-dev-Q4_K_S", "Qwen3.5-4B-UD-Q4_K_XL"]
    offered = await _offered(db, served)
    assert [(m["id"], m["name"]) for m in offered] == [("custom:llm1", "llm1")]
    assert offered[0]["provider_label"] == "your own computer"


async def test_an_endpoint_that_never_answered_is_still_offered(db):
    """Saving with the rig switched off must not remove it from the picker."""
    offered = await _offered(db, [])
    assert [m["id"] for m in offered] == ["custom:llm1"]


def test_the_picker_value_splits_into_endpoint_and_model():
    from agent_server.config import resolve_model_choice, split_custom_choice

    assert split_custom_choice("custom:llm1") == ("custom:llm1", "")
    assert split_custom_choice("custom:llm1/Qwen3-Coder") == ("custom:llm1", "Qwen3-Coder")
    # Model ids carry their own slashes; only the first one separates.
    assert split_custom_choice("custom:llm1/org/model-v2") == ("custom:llm1", "org/model-v2")

    assert resolve_model_choice("custom:llm1") == ("custom:llm1", "custom:llm1")
    assert resolve_model_choice("custom:llm1/Qwen3-Coder") == ("custom:llm1", "Qwen3-Coder")


async def test_a_chosen_model_is_sent_as_itself(db, endpoint_http):
    """The provider substitutes an id only when the session picked the endpoint
    as a whole. A session that picked one model must send exactly that."""
    import agent_server.providers.openai_compat as compat
    from agent_server.providers.custom_openai import CustomOpenAIProvider

    provider = CustomOpenAIProvider(
        "llm1", "http://box:8888/v1", "k", "first-model", ["first-model", "other-model"]
    )
    sent = {}

    async def fake_chat(self, messages, tools, model, thinking_effort=None):
        sent["model"] = model
        return
        yield  # pragma: no cover -- makes this an async generator

    original = compat.OpenAICompatibleProvider.chat_completion
    compat.OpenAICompatibleProvider.chat_completion = fake_chat
    try:
        async for _ in provider.chat_completion([], [], "other-model"):
            pass
    finally:
        compat.OpenAICompatibleProvider.chat_completion = original

    assert sent["model"] == "other-model"


async def test_a_custom_endpoint_is_named_on_the_model_button(db, tmp_path):
    """The button used to print the provider key, so the picker offered "llm1"
    and the button beside it read "custom:llm1"."""
    from agent_server import providers
    from agent_server.providers.custom_openai import CustomOpenAIProvider
    from agent_server.routes.context import _chat_context, _model_display

    providers._providers["custom:llm1"] = CustomOpenAIProvider("llm1", "http://box:8888/v1")
    try:
        assert _model_display({"provider": "custom:llm1", "model": "custom:llm1"}) == "llm1"
        # A session from before the picker stopped listing each reported model
        # stores the model id, which is no better to look at.
        assert _model_display(
            {"provider": "custom:llm1", "model": "Qwen3-Coder-Next-UD-Q3_K_XL"}
        ) == "llm1"

        # And the page is actually wired to it, not to the raw model column.
        session = await db.create_session(
            name="Biscuit", project_dir=str(tmp_path), kind="project",
            provider="custom:llm1", model="custom:llm1",
        )
        assert (await _chat_context(session))["model_display"] == "llm1"
    finally:
        del providers._providers["custom:llm1"]
