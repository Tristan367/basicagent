"""Subagent tool: run a focused, read-only agent loop and return its answer.

One level of subagents, no hierarchy to configure. The subagent keeps its
conversation in memory and runs on the parent's model at low effort, with a
read-only tool set.
"""

import asyncio

from agent_server.config import (
    MAX_TOOL_RESULT_CHARS,
    SUBAGENT_EFFORT,
    SUBAGENT_MAX_ROUNDS,
    SUBAGENT_TIMEOUT,
)
from agent_server.conversation import normalize_tool_calls, parse_arguments, tool_call_name
from agent_server.tools.base import ToolContext, ToolResult, truncate

SUBAGENT_PROMPT = """You are a research subagent. Investigate and report back. \
Your tools are read-only. Work autonomously until you can fully answer the task, \
then reply with your findings. Include concrete file paths with line numbers and \
relevant code snippets. Do not ask questions or describe your plan."""

# A subagent may read, search, and run read-only shell commands.
SUBAGENT_TOOLS = ("read", "glob", "grep", "webfetch", "websearch", "bash")


async def run_task(ctx: ToolContext, *, description: str, prompt: str, count: int = 1, **_) -> ToolResult:
    title = description[:70]
    try:
        return await asyncio.wait_for(_run(ctx, prompt, title), timeout=SUBAGENT_TIMEOUT)
    except TimeoutError:
        return ToolResult.error(f"subagent timed out after {SUBAGENT_TIMEOUT}s", title)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        return ToolResult.error(f"subagent failed: {type(e).__name__}: {e}", title)


async def _run(ctx: ToolContext, prompt: str, title: str) -> ToolResult:
    from agent_server.providers import get_provider
    from agent_server.tools.registry import execute_tool, tool_schemas

    provider = get_provider(ctx.provider)
    tools = tool_schemas(SUBAGENT_TOOLS)

    messages: list[dict] = [
        {"role": "system", "content": f"{SUBAGENT_PROMPT}\n\nWorking directory: {ctx.project_dir}"},
        {"role": "user", "content": prompt},
    ]

    usage_total: dict = {}

    for _round in range(SUBAGENT_MAX_ROUNDS):
        if ctx.abort.is_set():
            return ToolResult.error("cancelled", title, usage_total)

        content = ""
        reasoning = ""
        partials: dict[int, dict] = {}
        finish = "stop"

        async for event in provider.chat_completion(
            messages=messages, tools=tools, model=ctx.model, thinking_effort=SUBAGENT_EFFORT
        ):
            if ctx.abort.is_set():
                return ToolResult.error("cancelled", title, usage_total)
            etype = event["type"]
            if etype == "content":
                content += event["text"]
            elif etype == "reasoning":
                reasoning += event["text"]
            elif etype == "tool_calls":
                _accumulate(partials, event["deltas"])
            elif etype == "usage":
                for key, value in (event["usage"] or {}).items():
                    if isinstance(value, (int, float)):
                        usage_total[key] = usage_total.get(key, 0) + value
            elif etype == "error":
                return ToolResult.error(event["message"], title, usage_total)
            elif etype == "finish":
                finish = event["reason"]

        calls = normalize_tool_calls([partials[i] for i in sorted(partials)])

        assistant: dict = {"role": "assistant", "content": content}
        if reasoning:
            assistant["reasoning_content"] = reasoning
        if calls:
            assistant["tool_calls"] = calls
        messages.append(assistant)

        if finish != "tool_calls" or not calls:
            if content.strip():
                return ToolResult(output=content.strip(), title=title, usage=usage_total or None)
            return ToolResult.error("subagent returned no answer", title, usage_total)

        for call in calls:
            name = tool_call_name(call)
            args = parse_arguments(call)
            result = await execute_tool(name, args, ctx, allowed=SUBAGENT_TOOLS)
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": truncate(result.output, MAX_TOOL_RESULT_CHARS // 2, spill=True),
            })

    return ToolResult.error(
        f"subagent exceeded {SUBAGENT_MAX_ROUNDS} rounds without answering", title, usage_total
    )


def _accumulate(partials: dict[int, dict], deltas: list[dict]):
    for d in deltas:
        idx = d.get("index", 0)
        slot = partials.setdefault(idx, {"id": "", "name": "", "arguments": ""})
        if d.get("id"):
            slot["id"] = d["id"]
        if d.get("name"):
            slot["name"] = d["name"]
        if d.get("arguments"):
            slot["arguments"] += d["arguments"]
