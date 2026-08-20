"""The model catalogue: routing, pricing labels, and the beginner default.

The rule this file exists to protect is "a beginner never lands on the most
expensive model by accident", plus the routing bug where a model id whose
provider was not in the curated table fell through to DeepSeek and got sent to
the wrong vendor on the wrong key.
"""

import pytest

from agent_server import config
from agent_server.config import (
    MODELS,
    MODELS_BY_ID,
    contrast_text,
    model_info,
    provider_for_model,
    resolve_model_choice,
)
from agent_server.model_catalog import RECOMMENDED_MODELS, price_label


def test_every_model_has_the_fields_pricing_needs():
    for m in MODELS:
        assert m["id"] and m["name"] and m["provider"]
        assert m["context"] > 0
        for field in ("price_in_hit", "price_in_miss", "price_out"):
            assert isinstance(m[field], int | float), (m["id"], field)
            assert m[field] >= 0


def test_model_ids_are_unique():
    ids = [m["id"] for m in MODELS]
    assert len(ids) == len(set(ids))


def test_cache_hit_never_costs_more_than_a_miss():
    """A hit priced above a miss would make compaction look like a saving when
    it is a cost, and the switch-model estimator would pick the wrong branch."""
    for m in MODELS:
        assert m["price_in_hit"] <= m["price_in_miss"], m["id"]


def test_every_model_routes_to_a_registered_provider():
    from agent_server.providers import _providers

    for m in MODELS:
        assert m["provider"] in _providers, m["id"]


def test_recommended_models_all_exist():
    for model_id in RECOMMENDED_MODELS:
        assert model_id in MODELS_BY_ID, model_id


def test_recommended_excludes_the_priciest_model():
    """`recommended_default_model` picks the cheapest recommended model, but the
    set is also the answer to "would we start a beginner here unprompted?", so
    the top of the range must stay out of it regardless of sort order."""
    priciest = max(MODELS, key=lambda m: m["price_out"])
    assert priciest["id"] not in RECOMMENDED_MODELS


def test_provider_for_model_uses_the_curated_table():
    assert provider_for_model("deepseek-v4-pro") == "deepseek"
    assert provider_for_model("gemini-3.7-flash") == "gemini"
    assert provider_for_model("openai/gpt-5-mini") == "openrouter"


def test_discovered_model_routes_to_the_provider_that_advertised_it():
    """A model id discovered at startup must go back to its own provider.

    Falling through to DEFAULT_PROVIDER sent a Gemini id to DeepSeek — a real
    request, to the wrong vendor, billed to the wrong key.
    """
    config.register_dynamic_models("gemini", ["gemini-experimental-xyz"])
    try:
        assert provider_for_model("gemini-experimental-xyz") == "gemini"
        assert config.is_known_model("gemini-experimental-xyz")
    finally:
        config.DYNAMIC_MODELS.pop("gemini-experimental-xyz", None)


def test_unknown_model_falls_back_without_raising():
    assert provider_for_model("not-a-real-model") == config.DEFAULT_PROVIDER
    info = model_info("not-a-real-model")
    assert info["priced"] is False
    assert info["context"] > 0


def test_resolve_model_choice_rejects_unknown():
    with pytest.raises(ValueError):
        resolve_model_choice("definitely-not-a-model")


def test_a_custom_endpoint_is_its_own_model():
    """One endpoint serves one model and you cannot switch without restarting
    the server behind it, so the endpoint's name is the only name the user
    should ever need. Asking them to also type a model id was asking for
    something only the endpoint knows -- and slightly wrong meant every request
    failed."""
    assert resolve_model_choice("custom:my-box") == ("custom:my-box", "custom:my-box")


def test_price_label_calls_out_a_free_tier():
    assert price_label(3.75, free_tier=True).startswith("free")
    assert price_label(3.75) == "$3.75/M"
    assert price_label(0.0) == "your own"


@pytest.mark.parametrize("colour,expected", [
    ("#ffffff", "#000000"),
    ("#000000", "#ffffff"),
    ("#ffff00", "#000000"),
    ("#1a1a2e", "#ffffff"),
])
def test_contrast_text_stays_readable(colour, expected):
    assert contrast_text(colour) == expected


def test_contrast_text_survives_nonsense():
    assert contrast_text("") == "#ffffff"
    assert contrast_text("not a colour") == "#ffffff"


def test_no_model_here_costs_enough_to_frighten_someone():
    """Claude was removed on purpose. It is excellent and it is many times the
    price of everything else here, and the person this app is for has no way to
    know that until the bill arrives -- which is the one surprise an app aimed
    at people who are nervous about money must not spring.

    Anyone who does want it can still reach it through OpenRouter or a custom
    endpoint; it simply is not the thing a beginner is shown and can pick by
    accident."""
    from agent_server.config import MODELS

    assert not any(m["id"].startswith("claude") for m in MODELS)
    assert not any(m["provider"] == "anthropic" for m in MODELS)
    # And nothing on offer is wildly out of step with the rest.
    dearest = max(m["price_out"] for m in MODELS)
    cheapest = min(m["price_out"] for m in MODELS)
    assert dearest / cheapest < 50, "something here is orders of magnitude pricier than the rest"


# ── which model somebody lands on before they have chosen ──────────────────


def test_the_default_is_the_cheapest_thing_they_can_reach(monkeypatch):
    """Cheapest full stop, not cheapest-of-the-ones-we-like. Somebody who has
    not picked a model has not agreed to spend anything either."""
    from agent_server import model_catalog, providers

    kept = {k: v for k, v in providers._providers.items() if not k.startswith("custom:")}
    monkeypatch.setattr(model_catalog, "_providers", kept)
    for provider in kept.values():
        monkeypatch.setattr(provider, "api_key", lambda: "k")

    offered = model_catalog.offerable_models()
    chosen = model_catalog.recommended_default_model()
    assert chosen == min(offered, key=lambda m: m["price_out"])["id"]
    assert chosen == "deepseek-v4-flash", "the cheapest model in the catalogue moved"


def test_a_model_on_their_own_computer_wins(monkeypatch):
    """It costs nothing to run and they set it up deliberately, so it is both
    the cheapest option and the one they most likely meant."""
    from agent_server import model_catalog, providers

    class Local:
        name = "llm-box"

        def has_credentials(self):
            return True

        def served_models(self):
            return ["qwen3-coder"]

    kept = {k: v for k, v in providers._providers.items() if not k.startswith("custom:")}
    for provider in kept.values():
        monkeypatch.setattr(provider, "api_key", lambda: "k")
    kept["custom:box"] = Local()
    monkeypatch.setattr(model_catalog, "_providers", kept)

    assert model_catalog.recommended_default_model() == "custom:box"


def test_nothing_set_up_still_returns_something_usable(monkeypatch):
    from agent_server import model_catalog, providers
    from agent_server.config import DEFAULT_MODEL

    kept = {k: v for k, v in providers._providers.items() if not k.startswith("custom:")}
    monkeypatch.setattr(model_catalog, "_providers", kept)
    for provider in kept.values():
        monkeypatch.setattr(provider, "api_key", lambda: "")
    assert model_catalog.recommended_default_model() == DEFAULT_MODEL


def test_the_list_is_ordered_by_what_it_costs(monkeypatch):
    """A free allowance used to jump the queue, which put a model above one
    costing a ninth as much per token. Google no longer publishes those limits
    and they have been cut hard, so "free" now means "free until it stops" --
    not worth the top of the list. The label still says it."""
    from agent_server import model_catalog, providers

    kept = {k: v for k, v in providers._providers.items() if not k.startswith("custom:")}
    monkeypatch.setattr(model_catalog, "_providers", kept)
    for provider in kept.values():
        monkeypatch.setattr(provider, "api_key", lambda: "k")

    offered = model_catalog.offerable_models()
    prices = [m["price_out"] for m in offered]
    assert prices == sorted(prices), "the list is not cheapest-first"

    free = [m for m in offered if m.get("free_tier")]
    assert free, "no free-tier model to check the label on"
    # Said plainly. The allowance is a few hundred requests a day and it resets,
    # which is enough to build something -- hedging it ("free to try") undersold
    # the only option that costs nothing, which is the one that decides whether
    # somebody nervous about money gets to see the app at all.
    assert all(m["price_label"].startswith("free") for m in free)
    assert not any("free to try" in m["price_label"] for m in offered)
