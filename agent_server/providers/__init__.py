from agent_server.providers.anthropic import AnthropicProvider
from agent_server.providers.base import Provider, StreamEvent
from agent_server.providers.custom_openai import CustomOpenAIProvider
from agent_server.providers.deepseek import DeepSeekProvider
from agent_server.providers.gemini import GeminiProvider
from agent_server.providers.openrouter import OpenRouterProvider

# Insertion order is the order the Settings page lists them in, and the first
# one is what most people will take. Gemini leads because it is the only one
# you can start using without entering a payment method at all -- and because
# for the teachers this app keeps finding its way to, a Google account is
# something they already have. DeepSeek is second: cheapest to run, and better
# at code, but it needs a card.
_providers: dict[str, Provider] = {
    "gemini": GeminiProvider(),
    "deepseek": DeepSeekProvider(),
    "openrouter": OpenRouterProvider(),
    "anthropic": AnthropicProvider(),
}


def get_provider(name: str) -> Provider:
    """The adapter for a provider key, or ValueError.

    An unknown `custom:` key used to fall back to DeepSeek, which silently sent
    the conversation to a different vendor, on a different key, and billed it.
    A missing endpoint is a configuration error and has to say so.
    """
    provider = _providers.get(name)
    if provider is None:
        if name.startswith("custom:"):
            raise ValueError(
                f"No custom endpoint named '{name.removeprefix('custom:')}'. "
                "Add it on the home page, or point this session at another model."
            )
        raise ValueError(f"Unknown provider: {name}")
    return provider


def list_providers() -> list[str]:
    return list(_providers)


def get_provider_settings_fields() -> list[dict]:
    return [{"key": key, "name": p.name, "fields": p.settings_fields()}
            for key, p in _providers.items() if not key.startswith("custom:")]


async def load_custom_endpoint_providers():
    """(Re)register every saved custom endpoint.

    Built into a separate dict and swapped in at the end, so there is never a
    moment when a saved endpoint is missing from the registry. Rebuilding in
    place left a window in which an in-flight turn could not find its provider.
    """
    from agent_server import database as db_async

    fresh = {
        f"custom:{row['name']}": CustomOpenAIProvider(
            row["name"], row["base_url"], row["api_key"]
        )
        for row in await db_async.list_custom_endpoints()
        if row["base_url"]
    }
    for key in [k for k in _providers if k.startswith("custom:")]:
        del _providers[key]
    _providers.update(fresh)


__all__ = [
    "Provider",
    "StreamEvent",
    "_providers",
    "get_provider",
    "get_provider_settings_fields",
    "list_providers",
    "load_custom_endpoint_providers",
]
