"""Model catalogue: which models are available, what they cost, and sensible defaults."""

from agent_server.config import DEFAULT_MODEL, MODELS
from agent_server.providers import _providers, get_provider

# Models we steer beginners toward — cheap-ish and genuinely good. A model not in
# this set is still selectable, it just isn't the one the app will ever pick for
# them by default (so a new user can't accidentally land on the most expensive).
RECOMMENDED_MODELS = {
    "deepseek-v4-pro",
    "claude-haiku-4-5",
    "claude-sonnet-5",
    "claude-opus-5",
}


def price_label(price_out: float) -> str:
    """The price in dollars per million output tokens, compact enough for a
    dropdown. Showing the real number (rather than a "cheap/expensive" bucket)
    lets the user compare models directly."""
    if price_out <= 0:
        return "your own"
    return f"${price_out:g}/M"


def offerable_models() -> list[dict]:
    """Models whose provider has credentials (plus custom endpoints), sorted
    cheapest-first and annotated with a price label and a recommended flag."""
    offered = []
    for model in MODELS:
        try:
            provider = get_provider(model["provider"])
        except ValueError:
            continue
        if provider.has_credentials():
            offered.append({
                **model,
                "price_label": price_label(model.get("price_out", 0.0)),
                "recommended": model["id"] in RECOMMENDED_MODELS,
            })

    for key, provider in _providers.items():
        if key.startswith("custom:") and provider.has_credentials():
            offered.append({
                "id": key,
                "name": f"{provider.name} (custom endpoint)",
                "provider": key,
                "needs_model_id": True,
                "price_out": 0.0,
                "price_label": "your own",
                "recommended": False,
            })

    offered.sort(key=lambda m: m.get("price_out", 0.0))
    return offered


def any_credentials() -> bool:
    return any(p.has_credentials() for p in _providers.values())


def recommended_default_model() -> str:
    """The model new projects should start on.

    The cheapest *recommended* model the user actually has credentials for,
    falling back to the cheapest available. It is deliberately never the most
    expensive — a beginner with only a Claude key should land on Haiku, not
    Fable 5, without ever thinking about it.
    """
    available = offerable_models()
    recommended = [m for m in available if m.get("recommended")]
    pool = sorted(recommended or available, key=lambda m: m.get("price_out", 0.0))
    if pool:
        return pool[0]["id"]
    return DEFAULT_MODEL


def effective_default_model(settings: dict) -> str:
    """The user's chosen default if it is still usable, else the recommended one."""
    available_ids = {m["id"] for m in offerable_models()}
    stored = (settings.get("default_model") or "").strip()
    if stored in available_ids:
        return stored
    return recommended_default_model()
