"""Provider implementations for OpenAI-compatible APIs.

DeepSeek extends this with thinking-mode support; OpenRouter and custom
OpenAI-compatible endpoints use the base class directly.
"""

import asyncio
import logging
from collections.abc import AsyncIterator

import openai
from openai import AsyncOpenAI

from agent_server.providers.base import (
    Provider,
    StreamEvent,
    blank_usage,
    estimate_tokens,
    normalize_finish,
)

log = logging.getLogger(__name__)

# A whole reply may legitimately take ten minutes; three minutes of complete
# silence in the middle of one is not slow, it is gone. The flat 600-second
# timeout could not tell those apart, so a connection that had been accepted
# and then abandoned -- a proxy holding it open, wifi that went away after the
# request left -- held the turn for the full ten minutes with nothing arriving.
# Now the reply gets its ten minutes and each individual silence gets three.
def _timeouts():
    import httpx

    return httpx.Timeout(600.0, connect=30.0, read=180.0)


class OpenAICompatibleProvider(Provider):
    """Base for any OpenAI-compatible API (DeepSeek, OpenRouter, custom, etc.)."""

    base_url: str = ""  # set by subclass

    def __init__(self):
        self._client: AsyncOpenAI | None = None
        self._client_key: str = ""

    def _get_client(self) -> AsyncOpenAI:
        key = self.api_key()
        if self._client is None or self._client_key != key:
            self._client = AsyncOpenAI(api_key=key, base_url=self.base_url,
                                       max_retries=2, timeout=_timeouts())
            self._client_key = key
        return self._client

    def count_tokens(self, messages: list[dict]) -> int:
        return estimate_tokens(messages)

    # ── streaming ──────────────────────────────────────────────────────────
    def _build_kwargs(self, messages: list[dict], tools: list[dict], model: str,
                      thinking_effort: str | None = None) -> dict:
        """Build the kwargs for the chat completion call. Override to add
        provider-specific params (e.g. thinking mode for DeepSeek)."""
        kwargs: dict = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        return kwargs

    async def chat_completion(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str,
        thinking_effort: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        kwargs = self._build_kwargs(messages, tools, model, thinking_effort)

        try:
            stream = await self._get_client().chat.completions.create(**kwargs)
        except openai.APIStatusError as e:
            yield {"type": "error", "message": _describe(e, self.name)}
            return
        except Exception as e:
            log.warning("provider %s request failed", self.name, exc_info=True)
            yield {"type": "error", "message": f"{type(e).__name__}: {e}"}
            return

        finish_reason = None
        try:
            async for chunk in stream:
                if getattr(chunk, "usage", None):
                    yield {"type": "usage", "usage": _usage(chunk.usage)}

                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta

                if delta is not None:
                    # OpenRouter reports reasoning as `reasoning`; DeepSeek and
                    # the OpenAI-compatible convention use `reasoning_content`.
                    # Only checking the latter silently discarded every
                    # reasoning token from anything routed through OpenRouter.
                    reasoning = (
                        getattr(delta, "reasoning_content", None)
                        or getattr(delta, "reasoning", None)
                    )
                    if reasoning:
                        yield {"type": "reasoning", "text": reasoning}
                    if delta.content:
                        yield {"type": "content", "text": delta.content}
                    if delta.tool_calls:
                        yield {
                            "type": "tool_calls",
                            "deltas": [
                                {
                                    "index": tc.index,
                                    "id": tc.id,
                                    "name": tc.function.name if tc.function else None,
                                    "arguments": tc.function.arguments if tc.function else None,
                                }
                                for tc in delta.tool_calls
                            ],
                        }

                if choice.finish_reason:
                    finish_reason = choice.finish_reason
        except asyncio.CancelledError:
            raise
        except openai.APIStatusError as e:
            yield {"type": "error", "message": _describe(e, self.name)}
            return
        except Exception as e:
            log.warning("provider %s stream failed", self.name, exc_info=True)
            yield {"type": "error", "message": f"Stream failed: {type(e).__name__}: {e}"}
            return
        finally:
            # Covers the abort path too. `agent._loop` breaks out of its own
            # iteration when the user stops a run, which abandons this
            # generator without cancelling it, leaving the HTTP response and
            # its connection to be closed by the garbage collector.
            await _aclose(stream)

        yield {"type": "finish", "reason": normalize_finish(finish_reason)}


def _usage(u) -> dict:
    prompt_details = getattr(u, "prompt_tokens_details", None)
    completion_details = getattr(u, "completion_tokens_details", None)
    usage = blank_usage()
    usage.update(
        prompt_tokens=u.prompt_tokens or 0,
        completion_tokens=u.completion_tokens or 0,
        total_tokens=u.total_tokens or 0,
        cached_tokens=getattr(prompt_details, "cached_tokens", 0) or 0,
        reasoning_tokens=getattr(completion_details, "reasoning_tokens", 0) or 0,
    )
    return usage


async def _aclose(stream) -> None:
    try:
        await stream.close()
    except Exception:
        log.debug("closing stream failed", exc_info=True)


def _describe(e: openai.APIStatusError, name: str = "API") -> str:
    """What the user reads when the model cannot be reached.

    This string goes straight onto the screen, unedited, in an app whose whole
    premise is that the person using it does not know what an API is. It used
    to say `deepseek API error 401: Invalid API key provided`, which names no
    problem they can act on and no way to act on it.

    So: what happened, in words, and what to do about it. The Project Manager
    can change any of these settings by being asked out loud, which is a much
    better instruction than "go to Settings" for someone who cannot see the
    screen. The provider's own words are kept only where they carry something
    the sentence above cannot -- a refused request usually says why.
    """
    detail = ""
    try:
        body = e.response.json()
        detail = body.get("error", {}).get("message", "")
    except Exception:
        log.debug("reading API error detail failed", exc_info=True)
        detail = (getattr(e, "message", "") or str(e))[:400]

    status = getattr(e, "status_code", 0)
    ask = "Ask the Project Manager and it will sort it out."

    if status in (401, 403):
        return (f"{name} would not accept the key. It may have been typed with a "
                f"character missing, or it may have been turned off at their end. "
                f"{ask}")
    if status == 429:
        return (f"{name} is asking us to slow down -- either too many messages at "
                f"once, or the free allowance for today is used up. Waiting a "
                f"minute usually fixes the first; the second needs a different "
                f"model or an account with credit on it. {ask}")
    if status == 404:
        return (f"{name} does not have the model this project is set to use. It "
                f"has probably been renamed or retired. {ask}")
    if status == 400:
        return (f"{name} refused that request." + (f" It said: {detail}" if detail else "")
                + f" {ask}")
    if status and status >= 500:
        return (f"{name} is having trouble at their end -- nothing is wrong with "
                f"this app or with what you said. It is usually worth trying "
                f"again in a minute.")
    return (f"{name} could not be reached"
            + (f": {detail}" if detail else f" (error {status or 'unknown'})") + f". {ask}")
