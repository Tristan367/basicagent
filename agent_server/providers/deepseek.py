"""DeepSeek adapter (OpenAI-compatible endpoint).

Behaviour is pinned to https://api-docs.deepseek.com/guides/thinking_mode :

* Thinking mode is on by default and toggled via ``extra_body={"thinking": ...}``.
* Effort is the top-level ``reasoning_effort`` param.
* ``temperature``/``top_p``/penalties are silently ignored in thinking mode.
"""

from agent_server.config import DEFAULT_THINKING_EFFORT, REASONING_EFFORTS
from agent_server.providers.openai_compat import OpenAICompatibleProvider


class DeepSeekProvider(OpenAICompatibleProvider):
    name = "DeepSeek"
    base_url = "https://api.deepseek.com"
    env_key = "DEEPSEEK_API_KEY"
    settings_key = "deepseek_api_key"

    def _build_kwargs(self, messages, tools, model, thinking_effort=None):
        kwargs = super()._build_kwargs(messages, tools, model, thinking_effort)
        effort = thinking_effort or DEFAULT_THINKING_EFFORT
        if effort not in REASONING_EFFORTS:
            effort = DEFAULT_THINKING_EFFORT
        kwargs["reasoning_effort"] = effort
        kwargs["extra_body"] = {"thinking": {"type": "disabled" if effort == "none" else "enabled"}}
        return kwargs

    async def fetch_model_ids(self) -> list[str]:
        """Model ids the DeepSeek /models endpoint advertises right now.

        Empty on any failure or missing key: model discovery is best-effort, and
        the app must not fail to start because the network is down or the
        account changed. Returns the raw ids; the caller merges them with the
        hand-configured list.
        """
        import httpx

        key = self.api_key()
        if not key:
            return []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.base_url}/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
                if resp.status_code != 200:
                    return []
                data = resp.json()
        except Exception:
            return []
        rows = data.get("data", []) if isinstance(data, dict) else []
        return [
            str(row["id"])
            for row in rows
            if isinstance(row, dict) and row.get("id")
        ]
