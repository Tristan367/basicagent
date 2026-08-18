"""Conversation compaction.

The hard constraint: an assistant message carrying `tool_calls` and the `tool`
messages answering it form one atomic unit. Compacting part of that unit leaves
either dangling tool calls or orphaned results, and every subsequent request in
the session fails with a 400. The previous implementation sliced at a fixed
offset and could split a group; this one only ever cuts on a group boundary.
"""

from agent_server import database as db
from agent_server.config import model_info
from agent_server.conversation import (
    build_messages,
    normalize_tool_calls,
    pending_tool_calls,
)
from agent_server.providers import get_provider
from agent_server.system_prompt import COMPACT_PROMPT, session_system_prompt

# Work kept verbatim at the tail so recent context survives compaction. A
# summary alone loses the concrete detail the model is actively working with --
# exact identifiers, file contents it just read, the wording of the last
# instruction -- so compaction always leaves a real window in place.
#
# The window is a token budget, not a count of turns. It used to be a floor of
# four whole turns, which silently disabled compaction once turns got long: a
# single request can now run for dozens of tool rounds, and four of those came
# to 74,000 tokens in one real session, so the early return fired every time and
# nothing was ever summarised.
KEEP_MIN_UNITS = 2
KEEP_TAIL_TOKENS = 24_000


def group_messages(rows: list[dict]) -> list[list[dict]]:
    """Split a transcript into the smallest units that are safe to cut between.

    The hard constraint is only that an assistant message carrying `tool_calls`
    stays with the `tool` messages answering it. One unit is therefore one
    round -- that assistant message and its results -- not a whole turn.

    Grouping by turn made a long agent run atomic, so a 74,000-token turn could
    never be compacted at all. Rounds inside a turn are safe to cut between:
    each is closed by its own results.
    """
    groups: list[list[dict]] = []
    current: list[dict] = []

    def flush():
        nonlocal current
        if current:
            groups.append(current)
            current = []

    for row in rows:
        role = row["role"]
        if role == "tool":
            # Always belongs with the assistant round that requested it.
            if current:
                current.append(row)
            else:
                groups.append([row])
            continue

        flush()
        current = [row]
        if role != "assistant" or not normalize_tool_calls(row.get("tool_calls")):
            # Nothing is coming to answer this one, so the unit is already closed.
            flush()

    flush()
    return groups


def split_for_compaction(
    rows: list[dict], keep_tail_tokens: int = KEEP_TAIL_TOKENS
) -> tuple[list[dict], list[dict]]:
    """Return (messages_to_summarise, messages_to_keep) cut on a unit boundary."""
    groups = group_messages(rows)
    if len(groups) <= KEEP_MIN_UNITS:
        return [], rows

    # Grow the kept window backwards from the end until it fills the budget,
    # always stopping on a unit boundary, and always leaving at least one unit
    # to summarise. The budget is what decides; the minimum only stops a huge
    # final round from leaving no verbatim context at all.
    keep = 0
    total = 0
    for group in reversed(groups):
        cost = sum(r.get("token_count") or 0 for r in group)
        if keep >= KEEP_MIN_UNITS and total + cost > keep_tail_tokens:
            break
        if keep >= len(groups) - 1:
            break
        keep += 1
        total += cost

    head = groups[:-keep]
    tail = groups[-keep:]

    # Never keep a leading orphan: the kept window must not start with a tool result.
    while tail and tail[0] and tail[0][0]["role"] == "tool":
        head.append(tail.pop(0))
    if not tail:
        return [], rows

    return [m for g in head for m in g], [m for g in tail for m in g]


async def _summariser_messages(
    session: dict, rows: list[dict], to_compact: list[dict], instructions: str
) -> list[dict]:
    """Ask for the summary on top of the conversation that is already cached.

    Flattening the transcript into a fresh request re-buys, at the cache-miss
    rate, tokens that were already paid for once. Continuing the real
    conversation reuses the prefix the last turn already established: measured
    on a 106,000-token session, the fresh call billed 24,284 uncached tokens
    while continuing billed 58, which is 26x cheaper despite sending four times
    as much. It is also the fuller picture, because the flattened rendering
    truncates every message to 4,000 characters.

    The fallback stays for the cases where continuing is not possible: an open
    tool call that a user message cannot legally follow, or a conversation too
    large for the window.
    """
    fallback = [
        {"role": "system", "content": instructions},
        {"role": "user", "content": render_transcript(to_compact)},
    ]
    _, open_calls = pending_tool_calls(rows)
    if open_calls:
        return fallback

    provider = get_provider(session["provider"])
    live = build_messages(
        await session_system_prompt(session),
        await db.get_compactions(session["id"]),
        rows,
    )
    ask = {"role": "user", "content": instructions}
    if provider.count_tokens(live + [ask]) > _context_limit(session) * 0.9:
        return fallback
    return live + [ask]


def _context_limit(session: dict) -> int:
    return model_info(session.get("model", ""))["context"]


async def drop_closed_reasoning(kept: list[dict]) -> int:
    """Stop echoing reasoning for tool turns the user has already moved past.

    It is only required while a turn is open. Measured on a real session it is
    around 5% of the retained tail -- small next to the tool output, but it buys
    nothing, and compaction is the one moment when rewriting the prefix is free
    because it is being rewritten anyway.
    """
    last_user = max(
        (i for i, r in enumerate(kept) if r["role"] == "user"), default=-1
    )
    freed = 0
    for row in kept[:last_user]:
        if row["role"] != "assistant" or not row.get("reasoning_content"):
            continue
        if row.get("send_reasoning", 1) == 0:
            continue
        await db.update_message(row["id"], send_reasoning=0)
        row["send_reasoning"] = 0
        freed += len(row["reasoning_content"]) // 4
    return freed


def render_transcript(rows: list[dict], per_message_limit: int = 4000) -> str:
    lines: list[str] = []
    for row in rows:
        role = row["role"]
        content = (row.get("content") or "").strip()
        calls = normalize_tool_calls(row.get("tool_calls"))
        if calls:
            names = ", ".join(
                f"{c['function']['name']}({c['function']['arguments'][:200]})" for c in calls
            )
            lines.append(f"[assistant called tools] {names}")
        if not content:
            continue
        if len(content) > per_message_limit:
            content = content[:per_message_limit] + " ...[truncated]"
        label = f"tool:{row.get('tool_name') or '?'}" if role == "tool" else role
        lines.append(f"[{label}] {content}")
    return "\n\n".join(lines)


async def should_offer_compaction(session_id: str) -> bool:
    usage = await db.get_session_usage(session_id)
    return bool(usage["threshold"]) and usage["context"] >= usage["threshold"]


# How small a summary compresses its source down to, for switch-cost estimates.
# Real summaries run ~10-20% of the source; 15% plus a floor is a fair guess.
SUMMARY_RATIO = 0.15
MIN_SUMMARY_TOKENS = 50
# Only compact when it saves at least this much, so a near-tie (tiny
# conversation, expensive target) still switches straight across instead of
# paying the summarisation delay for a fraction of a cent.
MIN_SWITCH_SAVINGS = 0.01


async def estimate_switch_costs(session_id: str, new_model_id: str) -> dict:
    """Compare a raw model switch against compacting the conversation first.

    A raw switch re-uploads the whole live context to the new model at its
    cache-miss rate. Compacting first summarises the older part on the current
    (already-cached, often cheaper) model, then uploads only the summary plus
    the recent tail. Small conversations switch straight across; a large one
    moving to an expensive model is cheaper to summarise first.
    """
    from agent_server.config import model_info

    session = await db.get_session(session_id)
    current = model_info(session.get("model", "")) if session else model_info("")
    target = model_info(new_model_id)

    usage = await db.get_session_usage(session_id)
    context = usage["context"]

    rows = await db.get_messages(session_id)
    to_compact, _kept = split_for_compaction(rows)
    head_tokens = sum(r.get("token_count") or 0 for r in to_compact)
    summary_est = max(int(head_tokens * SUMMARY_RATIO), MIN_SUMMARY_TOKENS) if head_tokens else 0

    direct_cost = context * target["price_in_miss"] / 1_000_000

    # Summarising continues the real conversation, so the input is mostly a
    # cache hit on the current model; the output is the summary itself.
    summarise_cost = (
        context * current["price_in_hit"] + summary_est * current["price_out"]
    ) / 1_000_000
    post_switch_tokens = max(context - head_tokens, 0) + summary_est
    compact_cost = (
        summarise_cost + post_switch_tokens * target["price_in_miss"] / 1_000_000
    )

    compact = bool(to_compact) and compact_cost < direct_cost - MIN_SWITCH_SAVINGS

    return {
        "compact": compact,
        "context_tokens": context,
        "head_tokens": head_tokens,
        "summary_est": summary_est,
        "direct_cost": direct_cost,
        "compact_cost": compact_cost,
    }


async def compact_session(
    session_id: str,
    manual_summary: str = "",
    extra_instructions: str = "",
    prompt_override: str = "",
) -> dict:
    """Summarise the older part of a conversation. See compact_session_events."""
    result = {}
    async for event in compact_session_events(
        session_id, manual_summary, extra_instructions, prompt_override
    ):
        if event["type"] == "compact_done":
            result = event["result"]
    return result


async def compact_session_events(
    session_id: str,
    manual_summary: str = "",
    extra_instructions: str = "",
    prompt_override: str = "",
):
    """Summarise the older part of a conversation, streaming the summary.

    Summarising a long transcript takes a while, so the text is emitted as it
    arrives rather than leaving the user watching a spinner.

    `prompt_override` replaces the saved compaction prompt for this run only,
    and `extra_instructions` is appended to it, so neither requires editing the
    saved prompt permanently.
    """
    def fail(reason):
        return {"type": "compact_done", "result": {"ok": False, "reason": reason}}

    session = await db.get_session(session_id)
    if session is None:
        yield fail("Session not found")
        return

    rows = await db.get_messages(session_id)
    to_compact, kept = split_for_compaction(rows)
    if not to_compact:
        yield fail("Not enough completed turns to compact yet.")
        return

    provider = get_provider(session["provider"])

    if manual_summary.strip():
        summary = manual_summary.strip()
    else:
        if not provider.has_credentials():
            yield fail("No API key configured.")
            return
        instructions = prompt_override.strip() or COMPACT_PROMPT
        if extra_instructions.strip():
            instructions += f"\n\nAdditional instructions for this summary:\n{extra_instructions.strip()}"

        messages = await _summariser_messages(session, rows, to_compact, instructions)
        summary = ""
        async for event in provider.chat_completion(
            messages=messages,
            tools=[],
            model=session["model"],
            thinking_effort="low",
        ):
            if event["type"] == "content":
                summary += event["text"]
                yield {"type": "compact_delta", "text": event["text"]}
            elif event["type"] == "error":
                yield fail(event["message"])
                return
        summary = summary.strip()
        if not summary:
            yield fail("The model returned an empty summary.")
            return

    original_tokens = sum(r.get("token_count") or 0 for r in to_compact)
    compressed_tokens = provider.count_tokens([{"role": "system", "content": summary}])

    await db.add_compaction(
        session_id=session_id,
        summary_text=summary,
        range_start=to_compact[0]["id"],
        range_end=to_compact[-1]["id"],
        original_tokens=original_tokens,
        compressed_tokens=compressed_tokens,
    )
    await db.mark_messages_compacted(session_id, [r["id"] for r in to_compact])

    # The retained tail is the whole cost of a compacted session, so trim what
    # it does not need while the prefix is being rebuilt regardless.
    reasoning_freed = await drop_closed_reasoning(kept)

    # Compaction rewrites the prefix anyway, so this is the cheap moment to
    # adopt a shared prompt that changed while the conversation was running.
    pending = session.get("pending_system_prompt")
    if pending:
        await db.update_session(
            session_id, system_prompt=pending, pending_system_prompt=None
        )

    yield {
        "type": "compact_done",
        "result": {
            "ok": True,
            "compacted": len(to_compact),
            "kept": len(kept),
            "reasoning_freed": reasoning_freed,
            "original_tokens": original_tokens,
            "compressed_tokens": compressed_tokens,
            "summary": summary,
        },
    }
