"""OpenRouter adapter. The OpenAI SDK against OpenRouter's base URL."""

from openai import AsyncOpenAI

from agent_server.config import APP_SLUG, APP_URL
from agent_server.providers.openai_compat import OpenAICompatibleProvider, _timeouts

# Sent on every request. OpenRouter uses these for attribution and for
# rate-limit tiering; without them requests are treated as anonymous. `X-Title`
# is what the user sees against their own spend on openrouter.ai, so it has to
# be this app's name and not the project it was forked from.
_ATTRIBUTION = {
    "HTTP-Referer": APP_URL,
    "X-Title": APP_SLUG,
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
                timeout=_timeouts(),
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
