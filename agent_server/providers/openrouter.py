"""OpenRouter adapter. The OpenAI SDK against OpenRouter's base URL."""

from openai import AsyncOpenAI

from agent_server.providers.openai_compat import OpenAICompatibleProvider

# Sent on every request. OpenRouter uses these for attribution and for
# rate-limit tiering; without them requests are treated as anonymous.
_ATTRIBUTION = {
    "HTTP-Referer": "https://github.com/local/codeagent",
    "X-Title": "CodeAgent",
}


class OpenRouterProvider(OpenAICompatibleProvider):
    name = "OpenRouter"
    base_url = "https://openrouter.ai/api/v1"
    env_key = "OPENROUTER_API_KEY"
    settings_key = "openrouter_api_key"

    def _get_client(self) -> AsyncOpenAI:
        key = self.api_key()
        if self._client is None or self._client_key != key:
            self._client = AsyncOpenAI(
                api_key=key,
                base_url=self.base_url,
                max_retries=2,
                timeout=600.0,
                default_headers=_ATTRIBUTION,
            )
            self._client_key = key
        return self._client

    def _build_kwargs(self, messages, tools, model, thinking_effort=None):
        kwargs = super()._build_kwargs(messages, tools, model, thinking_effort)
        # Without this OpenRouter omits its normalised accounting, so
        # cached_tokens comes back zero whatever the upstream actually did and
        # every request looks like a full cache miss.
        kwargs["extra_body"] = {
            **kwargs.get("extra_body", {}),
            "usage": {"include": True},
        }
        return kwargs
