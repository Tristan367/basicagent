"""A user-configured OpenAI-compatible endpoint (Ollama, vLLM, LM Studio, ...).

One instance per row in the `custom_endpoints` table, registered under the
provider key `custom:NAME`.
"""

from agent_server.providers.openai_compat import OpenAICompatibleProvider


class CustomOpenAIProvider(OpenAICompatibleProvider):
    """A named custom endpoint. name='my-vllm', base_url='http://box:8000/v1'."""

    def __init__(self, name: str = "", base_url: str = "", api_key: str = ""):
        super().__init__()
        self._name = name
        self.base_url = base_url
        self._api_key = api_key or ""

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
