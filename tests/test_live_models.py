"""Check the curated model list against what the providers actually serve.

Excluded from the normal run: these hit real APIs and need real keys. Run them
deliberately, after touching the model list or when something 404s:

    pytest -m live tests/test_live_models.py

Only model *listing* is used, which no provider bills for -- nothing here sends
a prompt or spends anything.

The bug that prompted this: the catalogue carried `claude-haiku-4-5`, which
reads like the other Claude 5 ids but does not exist -- Haiku 4.5 only has the
dated form. It was also in the recommended set, so a user whose only key was
a provider would have had it chosen for them automatically, and every new
project would have failed on its first message with a 404 from a menu this app
drew for them.
"""

import httpx
import pytest

from agent_server.config import MODELS
from agent_server.providers import credentials, get_provider

pytestmark = pytest.mark.live


async def _live_ids(provider_key: str) -> set[str] | None:
    """Ids a provider serves right now, or None if we have no key for it."""
    provider = get_provider(provider_key)
    if not provider.has_credentials():
        return None
    key = provider.api_key()

    if provider_key == "gemini":
        url = "https://generativelanguage.googleapis.com/v1beta/openai/models"
        headers = {"Authorization": f"Bearer {key}"}
        strip = "models/"
    elif provider_key == "deepseek":
        url = "https://api.deepseek.com/models"
        headers = {"Authorization": f"Bearer {key}"}
        strip = ""
    else:
        url = "https://openrouter.ai/api/v1/models"
        headers = {}
        strip = ""

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(url, headers=headers)
    response.raise_for_status()
    rows = response.json().get("data", [])
    return {str(r["id"]).removeprefix(strip) for r in rows if r.get("id")}


@pytest.fixture(autouse=True)
async def keys(db):
    """Real keys, from the user's own settings."""
    import agent_server.config as config

    config.DB_PATH = __import__("pathlib").Path.home() / ".local/share/basicagent/agent.db"
    if not config.DB_PATH.exists():
        pytest.skip("no configured install to read keys from")
    credentials.invalidate()
    yield
    credentials.invalidate()


@pytest.mark.parametrize("provider_key", sorted({m["provider"] for m in MODELS}))
async def test_every_curated_model_is_still_served(provider_key):
    live = await _live_ids(provider_key)
    if live is None:
        pytest.skip(f"no key configured for {provider_key}")
    curated = {m["id"] for m in MODELS if m["provider"] == provider_key}
    missing = sorted(curated - live)
    assert not missing, (
        f"{provider_key} no longer serves {missing}. Someone picking one of these "
        f"out of the model menu gets a 404. Available: {sorted(live)[:12]}"
    )


@pytest.mark.parametrize("provider_key", sorted({m["provider"] for m in MODELS}))
async def test_the_key_actually_authenticates(provider_key):
    """A key that is present but rejected looks identical to a working one in
    the UI, right up until the first message fails."""
    live = await _live_ids(provider_key)
    if live is None:
        pytest.skip(f"no key configured for {provider_key}")
    assert live, f"{provider_key} returned an empty model list"


async def test_the_recommended_models_are_all_real():
    """These are the ones the app may choose on the user's behalf, so a dead id
    here breaks a new project before the user has done anything at all."""
    from agent_server.model_catalog import RECOMMENDED_MODELS

    by_provider: dict[str, set[str]] = {}
    for model in MODELS:
        if model["id"] in RECOMMENDED_MODELS:
            by_provider.setdefault(model["provider"], set()).add(model["id"])

    for provider_key, ids in by_provider.items():
        live = await _live_ids(provider_key)
        if live is None:
            continue
        assert not (ids - live), f"recommended but not served: {sorted(ids - live)}"
