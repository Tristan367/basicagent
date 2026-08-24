"""Subagent tool: run one agent loop inside another and return its answer.

There is one kind of subagent, and it can do what the agent that called it can
do. It is not a research agent, a reviewer, or any other named role: what it
does is decided entirely by the prompt it is given, the way an instruction to a
person is. A second kind, with a smaller tool set and a role baked into its
system prompt, would be a second thing to keep in step with the first, and the
parent would have to know which one it was talking to before it could ask for
anything.

It was read-only, which sounds cautious and mostly is not. An agent that can
find the three files that need the same change and cannot make it hands back a
description of the work instead of the work, and the parent then does it again
from scratch off a summary. The one real boundary is kept -- it writes inside
the project and nowhere else -- because that is a safety rule rather than a
preference about how it should work.

The exceptions are the three tools that are not really tools but handles on
something the user can see, of which there is exactly one per project:

* `browser` is a live Chromium keyed by session id, so a subagent driving it is
  driving the *parent's* browser -- the same page, the same history, the same
  login, halfway through whatever the parent was doing with it.
* `preview` is the window the user is looking at. There is one slot per
  project, so a subagent starting something replaces what the parent had
  running, on screen, in front of somebody who is watching it happen and was
  never told a second assistant existed.
* `game` calls `preview`, so it is the same thing wearing a hat.

None of these is about trust. A subagent can rewrite every file in the project;
what it cannot do is take over the one window somebody is watching. Work that
genuinely needs the screen belongs to the assistant the user is talking to.

The subagent keeps its conversation in memory and runs on the parent's model at
low effort.
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

SUBAGENT_PROMPT = """You are a subagent. Another assistant has handed you a \
piece of work and is waiting on your answer.

You can read, change and run things in this project, exactly as the assistant \
that called you can. Do the work it asked for -- do not describe how you would \
do it and hand that back instead.

Two things are yours to respect:

* Change only what the task told you to change. You are working in a project \
somebody else is also working in, and edits you were not asked for will be a \
surprise to them and to the user.
* You have no way to put anything on the user's screen -- no window, no \
browser, no running game. Those belong to the assistant that called you. Do \
the work, and let it show them.
* You cannot write outside this project, and you cannot ask anybody anything. \
Nobody is reading your messages until you finish, so decide and carry on.

Work until the task is done, then reply with what you did and what you found. \
Name files with line numbers. Do not describe your plan."""


def subagent_tools() -> tuple[str, ...]:
    """Everything the calling agent has, less the browser and this tool itself.

    Derived rather than listed, so a tool added to the app reaches subagents
    too. A hand-written list is a list that silently falls behind -- which is
    how this one ended up with six entries and no `edit`.
    """
    from agent_server.tools.registry import MANAGER_TOOLS, TOOLS

    return tuple(
        name for name in TOOLS
        if name not in MANAGER_TOOLS
        and name not in ("browser", "preview", "game", "task")
    )


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
    allowed = subagent_tools()
    tools = tool_schemas(allowed)

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
            result = await execute_tool(name, args, ctx, allowed=allowed)
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
