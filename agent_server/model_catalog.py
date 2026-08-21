"""Model catalogue: which models are available, what they cost, and sensible defaults."""

from agent_server.config import DEFAULT_MODEL, MODELS
from agent_server.providers import _providers, get_provider

# Models we steer beginners toward — cheap-ish and genuinely good. A model not in
# this set is still selectable, it just isn't the one the app will ever pick for
# them by default (so a new user can't accidentally land on the most expensive).
#
# Deliberately excludes the top of each vendor's range. `recommended_default_model`
# picks the cheapest entry here that the user has a key for, so anything listed is
# something we are willing to start a beginner on unprompted.
RECOMMENDED_MODELS = {
    "deepseek-v4-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.7-flash",
}


def price_label(price_out: float, free_tier: bool = False) -> str:
    """The price in dollars per million output tokens, compact enough for a
    dropdown. Showing the real number (rather than a "cheap/expensive" bucket)
    lets the user compare models directly.

    A free allowance is called out because it is the single fact most likely to
    change a beginner's mind about whether they can use the app at all.
    """
    if price_out <= 0:
        return "your own"
    if free_tier:
        # The allowance is a few hundred requests a day and it resets, which is
        # enough to build something rather than merely sample it -- so "free"
        # unqualified, and the price after it for anyone who outgrows it. An
        # earlier "free to try" undersold the one option that costs nothing.
        return f"free, then ${price_out:g}/M"
    return f"${price_out:g}/M"


# Shown after the model's name, so two entries for the same model are
# distinguishable. OpenRouter resells most of what the first-party providers
# offer, so "Claude Opus 5" can legitimately appear twice at different prices
# and against different accounts.
PROVIDER_LABEL = {
    "gemini": "Google",
    "deepseek": "DeepSeek",
    "openrouter": "OpenRouter",
}


def offerable_models() -> list[dict]:
    """Models whose provider has credentials (plus custom endpoints), sorted
    free-first then cheapest-first, annotated with a price label, the provider
    it comes through, and a recommended flag."""
    offered = []
    for model in MODELS:
        try:
            provider = get_provider(model["provider"])
        except ValueError:
            continue
        if provider.has_credentials():
            offered.append({
                **model,
                "price_label": price_label(
                    model.get("price_out", 0.0), model.get("free_tier", False)
                ),
                "provider_label": PROVIDER_LABEL.get(model["provider"], model["provider"]),
                "recommended": model["id"] in RECOMMENDED_MODELS,
            })

    # Custom endpoints. One entry each, under the name its owner gave it: to
    # this app the endpoint *is* the model, because whatever is loaded over
    # there is what answers.
    #
    # We used to enumerate what `/v1/models` reported and offer each as its own
    # choice. That read the list as a menu, which it is not. It is Unsloth's
    # habit of listing its variants; vLLM and llama.cpp serve one thing and name
    # it however they like, and none of them will load something else because we
    # asked. So picking a second entry changed the id in the request body and
    # nothing at the far end -- the same model replied under a different name,
    # which is worse than not offering the choice at all. (Ollama would honour
    # it, but a picker that works on one server in three is not a picker.)
    for key, provider in _providers.items():
        if not key.startswith("custom:") or not provider.has_credentials():
            continue
        offered.append({
            "id": key,
            "name": provider.name,
            "provider_label": "your own computer",
            "provider": key,
            "price_out": 0.0,
            "price_label": "your own",
            "recommended": False,
        })

    # Cheapest first, by what it actually costs.
    #
    # A free allowance used to jump the queue, which put Gemini above a model
    # costing a ninth as much per token. That was defensible when the free tier
    # was generous; Google no longer publishes its limits and they have been cut
    # hard, and a coding turn spends several requests -- so "free" now means
    # "free until it stops", which is not worth the top of the list. It is still
    # said on the label, where someone can weigh it for themselves.
    offered.sort(key=lambda m: m.get("price_out", 0.0))
    return offered


def any_credentials() -> bool:
    return any(p.has_credentials() for p in _providers.values())


def recommended_default_model() -> str:
    """The model to start on: the cheapest thing the user can actually reach.

    Cheapest full stop, not cheapest-of-the-ones-we-like. Somebody who has not
    chosen a model has not agreed to spend anything either, and the difference
    between the cheapest option and the dearest here is more than fortyfold --
    a bill nobody warned them about is the one surprise this app must never
    spring on the sort of person it is for.

    A custom endpoint sorts to the front at zero, which is right: a model on
    their own computer costs nothing to run and was set up deliberately.

    They can pick something else whenever they like, and the picker shows the
    price beside every name so the choice is an informed one.
    """
    available = offerable_models()
    if not available:
        return DEFAULT_MODEL
    return min(available, key=lambda m: m.get("price_out", 0.0))["id"]


def effective_default_model(settings: dict) -> str:
    """The user's chosen default if it is still usable, else the recommended one."""
    available_ids = {m["id"] for m in offerable_models()}
    stored = (settings.get("default_model") or "").strip()
    if stored in available_ids:
        return stored
    return recommended_default_model()
