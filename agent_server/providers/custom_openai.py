"""A user-configured OpenAI-compatible endpoint (Ollama, vLLM, LM Studio, ...).

One instance per row in the `custom_endpoints` table, registered under the
provider key `custom:NAME`.
"""

import logging
from collections.abc import AsyncIterator

import httpx

from agent_server.providers.base import StreamEvent
from agent_server.providers.openai_compat import OpenAICompatibleProvider

log = logging.getLogger(__name__)


class CustomOpenAIProvider(OpenAICompatibleProvider):
    """A named custom endpoint. name='my-vllm', base_url='http://box:8000/v1'."""

    def __init__(self, name: str = "", base_url: str = "", api_key: str = "",
                 model_id: str = ""):
        super().__init__()
        self._name = name
        self.base_url = base_url
        self._api_key = api_key or ""
        self._model_id = model_id or ""

    @property
    def name(self) -> str:
        return self._name or "Custom"

    def api_key(self) -> str:
        """The key saved alongside the endpoint.

        The base class reads an environment variable and then a `settings` row,
        neither of which a custom endpoint has -- its key lives in its own
        table. This used to inherit that lookup with both names left empty, so
        it queried `settings` for the key `''`, found nothing, cached the empty
        string, and reported "no API key configured" forever. Custom endpoints
        have never been able to authenticate.
        """
        return self._api_key

    def has_credentials(self) -> bool:
        """A URL is enough. A local Ollama or vLLM has no key to give, and the
        OpenAI client requires a non-empty one, so a placeholder is sent."""
        return bool(self.base_url)

    def _get_client(self):
        # openai's client rejects an empty api_key outright, and unauthenticated
        # local servers ignore whatever is sent.
        self._api_key = self._api_key or "not-needed"
        return super()._get_client()

    def invalidate_key_cache(self):
        self._client = None
        self._client_key = ""

    def settings_fields(self) -> list[dict]:
        # Configured on the home page under Custom endpoints, not here.
        return []

    # ── which model ────────────────────────────────────────────────────────
    async def _discover_model(self) -> str:
        """Ask the endpoint what it is serving, and remember the answer.

        One endpoint serves one model. There is no menu to pick from and no way
        to switch without restarting the server behind it, so asking the user
        to type a model id was asking them for something only the endpoint
        knows -- and getting it slightly wrong failed every request.
        """
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                response = await client.get(
                    f"{self.base_url.rstrip('/')}/models", headers=headers
                )
            response.raise_for_status()
            rows = response.json().get("data", [])
        except Exception as e:
            log.info("endpoint %s could not be asked for its model: %s", self._name, e)
            return ""
        model_id = str(rows[0].get("id", "")) if rows else ""
        if model_id:
            from agent_server import database as db

            self._model_id = model_id
            await db.set_custom_endpoint_model(self._name, model_id)
        return model_id

    async def chat_completion(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str,
        thinking_effort: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        # The session stores the endpoint (`custom:my-vllm`) as its model,
        # because to the user the endpoint *is* the model. Substitute the id
        # the server itself uses, discovering it now if it was not running when
        # the endpoint was saved. The name is a last resort: most local servers
        # ignore this field, and one that does not will say so plainly.
        model_id = self._model_id or await self._discover_model() or self._name
        async for event in super().chat_completion(
            messages, tools, model_id, thinking_effort
        ):
            yield event
