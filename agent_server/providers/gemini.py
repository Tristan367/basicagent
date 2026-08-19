"""Google Gemini adapter.

Google publishes an OpenAI-compatible endpoint alongside its native one, so
this is the base class with a different URL rather than a whole second wire
format. See https://ai.google.dev/gemini-api/docs/openai .

Gemini matters for this app out of proportion to its capability ranking: the
Flash models have a real free tier, so a non-technical user can get a key from
Google AI Studio and use the app without entering a card anywhere. That is the
difference between "try it this evening" and "give up at the billing page".
"""

from agent_server.providers.openai_compat import OpenAICompatibleProvider


class GeminiProvider(OpenAICompatibleProvider):
    name = "Google Gemini"
    base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
    env_key = "GEMINI_API_KEY"
    settings_key = "gemini_api_key"

    def supports_vision(self) -> bool:
        # Every current Gemini model is natively multimodal.
        return True

    def _build_kwargs(self, messages, tools, model, thinking_effort=None):
        kwargs = super()._build_kwargs(messages, tools, model, thinking_effort)
        # The compatibility layer rejects `stream_options` on some models rather
        # than ignoring it, which fails the whole request. Usage still arrives on
        # the final chunk without it, so it is safe to drop and unsafe to send.
        kwargs.pop("stream_options", None)
        return kwargs

    async def fetch_model_ids(self) -> list[str]:
        """Model ids the account can actually reach right now, best-effort.

        Same contract as the DeepSeek discovery: empty on any failure, because
        the app must start with the network down. The curated list in `config`
        is what the picker shows; this only widens what is accepted.
        """
        import httpx

        key = self.api_key()
        if not key:
            return []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.base_url}models",
                    headers={"Authorization": f"Bearer {key}"},
                )
                if resp.status_code != 200:
                    return []
                data = resp.json()
        except Exception:
            return []
        rows = data.get("data", []) if isinstance(data, dict) else []
        # Ids come back as "models/gemini-3.7-flash"; the chat endpoint wants the
        # bare id, and sending the prefixed form is a 404.
        return [
            str(row["id"]).removeprefix("models/")
            for row in rows
            if isinstance(row, dict) and row.get("id")
        ]
