"""Anthropic adapter.

The Messages API differs from the OpenAI shape in four ways that each produced
a hard failure here, so the conversion below is the whole point of the module:

1. A tool result is a ``tool_result`` block in a *user* turn, and it must come
   immediately after the assistant turn carrying the matching ``tool_use``.
   Buffering results until the next real user message -- which is what this did
   -- emitted two assistant turns in a row and then both results after the
   fact, which the API rejects outright. Multi-round tool use could not work.
2. Stop reasons are ``end_turn``/``tool_use``/``max_tokens``, not
   ``stop``/``tool_calls``/``length``. Consumers match on the OpenAI vocabulary,
   so subagents saw ``tool_use``, concluded the model had not asked for a tool,
   and returned "subagent returned no answer" every time.
3. ``input_tokens`` excludes cached reads, where OpenAI's ``prompt_tokens``
   includes them. The shared cost function subtracts one from the other, so
   left alone it clamped to zero and billed every cache read as free.
4. Neither an empty assistant turn nor two turns of the same role is accepted.
"""

import json
import logging
from collections.abc import AsyncIterator

import anthropic
from anthropic import AsyncAnthropic

from agent_server.config import model_info
from agent_server.conversation import normalize_tool_calls
from agent_server.providers.base import (
    Provider,
    StreamEvent,
    blank_usage,
    estimate_tokens,
    normalize_finish,
)

log = logging.getLogger(__name__)
# Which thinking parameter a model accepts. Getting this wrong is a 400, and
# the two are mutually exclusive:
#
#   adaptive -- Claude decides when and how deeply to think, and `effort`
#               steers it. On Opus 5 / Sonnet 5 / Fable 5 it is already on.
#   extended -- the older manual form, `type: enabled` with a token budget.
#               Haiku 4.5 is the only current model that takes it.
#
# 8192 output tokens was hardcoded for every model, so answers were cut off at
# a sixteenth of what Opus 5 can produce, and reported as `max_tokens` -- which
# nothing translated, so the output-limit guard never fired either.
ADAPTIVE_THINKING = {
    "claude-fable-5", "claude-mythos-5",
    "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
    "claude-sonnet-5", "claude-sonnet-4-6",
}
EXTENDED_THINKING = {"claude-haiku-4-5", "claude-haiku-4-5-20251001"}
# Thinking cannot be switched off on these at any effort.
THINKING_ALWAYS_ON = {"claude-fable-5", "claude-mythos-5"}
# Opus 5 rejects `thinking: disabled` at these efforts specifically.
NO_DISABLE_AT = {"xhigh", "max"}

# The app's effort vocabulary against Anthropic's. `none` means "do not think",
# which is a thinking setting rather than an effort level; `minimal` has no
# Anthropic equivalent and maps to the nearest one that exists.
EFFORT_ALIASES = {"minimal": "low", "none": "low"}
ANTHROPIC_EFFORTS = {"low", "medium", "high", "xhigh", "max"}

# Budgets for the manual form, keyed by the same effort vocabulary. Anthropic
# requires at least 1024 and strictly less than max_tokens.
THINKING_BUDGETS = {
    "none": 0,
    "minimal": 1_024,
    "low": 4_096,
    "medium": 10_000,
    "high": 20_000,
    "xhigh": 32_000,
    "max": 48_000,
}


class AnthropicProvider(Provider):
    name = "Anthropic"
    env_key = "ANTHROPIC_API_KEY"
    settings_key = "anthropic_api_key"

    async def fetch_model_ids(self) -> list[str]:
        """Model ids this account can reach. Empty on any failure.

        Best-effort, like the other providers': the app must start with the
        network down, and this only widens the set of ids it will accept.
        """
        import httpx

        key = self.api_key()
        if not key:
            return []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                )
                if resp.status_code != 200:
                    return []
                data = resp.json()
        except Exception:
            return []
        rows = data.get("data", []) if isinstance(data, dict) else []
        return [str(r["id"]) for r in rows if isinstance(r, dict) and r.get("id")]


    def __init__(self):
        self._client: AsyncAnthropic | None = None
        self._client_key: str = ""

    def _get_client(self) -> AsyncAnthropic:
        key = self.api_key()
        if self._client is None or self._client_key != key:
            self._client = AsyncAnthropic(api_key=key, max_retries=2, timeout=600.0)
            self._client_key = key
        return self._client

    def invalidate_key_cache(self):
        super().invalidate_key_cache()
        self._client = None
        self._client_key = ""

    def count_tokens(self, messages: list[dict]) -> int:
        return estimate_tokens(messages)

    def _build_kwargs(
        self, messages: list[dict], tools: list[dict], model: str, thinking_effort: str | None
    ) -> dict:
        system = _extract_system(messages)
        max_tokens = model_info(model)["max_output"]

        kwargs: dict = {
            "model": model,
            "messages": _convert_messages(messages),
            "max_tokens": max_tokens,
            "stream": True,
        }

        if system:
            # A cache breakpoint at the end of the system prompt. Without one
            # Anthropic caches nothing, cache_read_input_tokens is always zero,
            # and the whole cache_guard subsystem -- four database columns and
            # a confirmation dialog -- measures a cache that does not exist.
            kwargs["system"] = [{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }]

        if tools:
            converted = [{
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "input_schema": t["function"].get(
                    "parameters", {"type": "object", "properties": {}}
                ),
            } for t in tools]
            # Schemas are identical on every request in a session, so they
            # belong inside the cached prefix alongside the system prompt.
            converted[-1]["cache_control"] = {"type": "ephemeral"}
            kwargs["tools"] = converted

        kwargs.update(_thinking_kwargs(model, thinking_effort, max_tokens))
        return kwargs

    async def chat_completion(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str,
        thinking_effort: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        kwargs = self._build_kwargs(messages, tools, model, thinking_effort)
        usage = blank_usage()
        finish = "stop"

        try:
            async with self._get_client().messages.stream(**kwargs) as stream:
                async for event in stream:
                    kind = event.type

                    if kind == "message_start":
                        # input_tokens only appears here. Reading it from
                        # message_delta instead left prompt_tokens at zero on
                        # every request, so nothing anchored the cache
                        # prediction and spend was understated by the entire
                        # input side.
                        _merge_usage(usage, event.message.usage)

                    elif kind == "content_block_start":
                        block = event.content_block
                        if block.type == "tool_use":
                            yield {
                                "type": "tool_calls",
                                "deltas": [{
                                    "index": event.index,
                                    "id": block.id,
                                    "name": block.name,
                                    "arguments": "",
                                }],
                            }

                    elif kind == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            yield {"type": "content", "text": delta.text}
                        elif delta.type == "thinking_delta":
                            yield {"type": "reasoning", "text": delta.thinking}
                        elif delta.type == "input_json_delta":
                            yield {
                                "type": "tool_calls",
                                "deltas": [{
                                    "index": event.index,
                                    "id": None,
                                    "name": None,
                                    "arguments": delta.partial_json,
                                }],
                            }

                    elif kind == "message_delta":
                        finish = event.delta.stop_reason or finish
                        _merge_usage(usage, event.usage)

        except anthropic.APIStatusError as e:
            yield {"type": "error", "message": _describe(e)}
            return
        except Exception as e:
            log.warning("provider %s request failed", self.name, exc_info=True)
            yield {"type": "error", "message": f"{type(e).__name__}: {e}"}
            return

        # Usage before finish: `finish` is documented as terminal, and yielding
        # it first meant the accounting arrived after the event that ends the
        # turn. Reasoning is billed as output, which is already counted.
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        yield {"type": "usage", "usage": usage}
        yield {"type": "finish", "reason": normalize_finish(finish)}


def _thinking_kwargs(model: str, effort: str | None, max_tokens: int) -> dict:
    """Thinking and effort settings for one model.

    The two families take different parameters and reject each other's, so this
    is a lookup rather than a set of flags. Effort is nested under
    `output_config`; sent at the top level it is simply ignored.
    """
    effort = (effort or "").strip().lower()
    wants_off = effort == "none"

    if model in EXTENDED_THINKING:
        budget = min(THINKING_BUDGETS.get(effort, 0), max_tokens - 1024)
        if wants_off or budget < 1024:
            return {}
        return {"thinking": {"type": "enabled", "budget_tokens": budget}}

    if model not in ADAPTIVE_THINKING:
        # An unrecognised Anthropic model: send nothing rather than guess at a
        # parameter it may reject.
        return {}

    level = EFFORT_ALIASES.get(effort, effort)
    out: dict = {}
    if level in ANTHROPIC_EFFORTS:
        out["output_config"] = {"effort": level}

    if wants_off and model not in THINKING_ALWAYS_ON and level not in NO_DISABLE_AT:
        out["thinking"] = {"type": "disabled"}
    else:
        # `display` defaults to "omitted" on the current models, which returns
        # thinking blocks with an empty body. This app has a reasoning pane, so
        # ask for the summary that fills it.
        out["thinking"] = {"type": "adaptive", "display": "summarized"}
    return out


def _merge_usage(usage: dict, reported) -> None:
    """Fold one Anthropic usage object into the normalised shape.

    Anthropic splits the input side three ways -- uncached, cache reads, cache
    writes -- and `input_tokens` counts only the first. `prompt_tokens` is
    defined as the whole input, so the parts are added back together and the
    cached portion is reported alongside for the hit-rate figure.
    """
    if reported is None:
        return
    fresh = getattr(reported, "input_tokens", 0) or 0
    cache_read = getattr(reported, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(reported, "cache_creation_input_tokens", 0) or 0
    output = getattr(reported, "output_tokens", 0) or 0

    if fresh or cache_read or cache_write:
        usage["prompt_tokens"] = fresh + cache_read + cache_write
        usage["cached_tokens"] = cache_read
        usage["cache_write_tokens"] = cache_write
    if output:
        usage["completion_tokens"] = output


def _describe(e: anthropic.APIStatusError) -> str:
    detail = ""
    try:
        body = e.response.json()
        detail = (body.get("error") or {}).get("message", "")
    except Exception:
        detail = (getattr(e, "message", "") or str(e))[:400]
    return f"Anthropic API error {e.status_code}: {detail or 'unknown error'}"


def _extract_system(messages: list[dict]) -> str:
    """Every system message, joined. Compaction summaries arrive as extra ones."""
    parts = [
        m.get("content") or ""
        for m in messages
        if m.get("role") == "system" and m.get("content")
    ]
    return "\n\n".join(parts)


def _convert_messages(messages: list[dict]) -> list[dict]:
    """OpenAI-shaped rows to Anthropic turns.

    Tool results are flushed the moment a non-tool message follows them, which
    puts them in a user turn directly after the assistant turn that asked --
    the ordering the API requires. Same-role turns are merged and empty ones
    dropped, both of which are rejected outright.
    """
    out: list[dict] = []
    pending_results: list[dict] = []

    def flush_results():
        nonlocal pending_results
        if pending_results:
            _append(out, "user", pending_results)
            pending_results = []

    for m in messages:
        role = m.get("role", "")

        if role == "system":
            continue

        if role == "tool":
            pending_results.append({
                "type": "tool_result",
                "tool_use_id": m.get("tool_call_id") or "unknown",
                "content": m.get("content") or "(no output)",
                "is_error": bool(m.get("is_error")),
            })
            continue

        flush_results()

        if role == "assistant":
            blocks: list[dict] = []
            content = m.get("content") or ""
            if content:
                blocks.append({"type": "text", "text": content})
            for tc in normalize_tool_calls(m.get("tool_calls")) or []:
                fn = tc.get("function", {})
                raw = fn.get("arguments", "{}")
                blocks.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": fn.get("name", ""),
                    "input": raw if isinstance(raw, dict) else _safe_json(raw),
                })
            if blocks:
                _append(out, "assistant", blocks)

        elif role == "user":
            content = m.get("content")
            if isinstance(content, list):
                _append(out, "user", content)
            elif content:
                _append(out, "user", [{"type": "text", "text": content}])

    flush_results()

    # A conversation must begin with a user turn.
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def _append(out: list[dict], role: str, blocks: list[dict]) -> None:
    """Add a turn, merging into the previous one when the role repeats."""
    if not blocks:
        return
    if out and out[-1]["role"] == role:
        out[-1]["content"].extend(blocks)
        return
    out.append({"role": role, "content": list(blocks)})


def _safe_json(s: str) -> dict:
    try:
        parsed = json.loads(s or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
