"""Translation between stored messages and the provider wire format.

This module owns the single most failure-prone part of the agent: assembling a
message array that the DeepSeek/OpenAI API will actually accept. The rules it
enforces, all of which produce hard 400s when violated:

1. `tool_calls` must be ``{"id", "type": "function", "function": {"name", "arguments"}}``.
   The compact ``{"id", "name", "arguments"}`` shape is rejected with
   *"missing field `type`"*.
2. Every ``tool_call_id`` in an assistant message must be answered by exactly one
   ``role: "tool"`` message, otherwise the API returns *"An assistant message with
   'tool_calls' must be followed by tool messages responding to each 'tool_call_id'"*.
3. In thinking mode, ``reasoning_content`` on an assistant message that made a tool
   call must be echoed back verbatim, or the API returns *"The `reasoning_content`
   in the thinking mode must be passed back to the API"*.
   See https://api-docs.deepseek.com/guides/thinking_mode#tool-calls
"""

import json
from typing import Any


def normalize_tool_calls(raw: Any) -> list[dict]:
    """Coerce any stored tool-call shape into the canonical wire format.

    Accepts the canonical nested form and the legacy flat ``{id, name, arguments}``
    form written by earlier versions of this app, so old sessions keep working.
    """
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(raw, list):
        return []

    out: list[dict] = []
    for tc in raw:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function")
        if isinstance(fn, dict):
            name = fn.get("name") or ""
            arguments = fn.get("arguments")
        else:
            name = tc.get("name") or ""
            arguments = tc.get("arguments")
        if arguments is None:
            arguments = "{}"
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments)
        if not name:
            continue
        out.append({
            "id": tc.get("id") or "",
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        })
    return out


def parse_arguments(tool_call: dict) -> dict:
    """Best-effort JSON decode of a tool call's arguments."""
    raw = tool_call.get("function", {}).get("arguments") or "{}"
    try:
        args = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return args if isinstance(args, dict) else {}


def tool_call_name(tool_call: dict) -> str:
    return tool_call.get("function", {}).get("name", "")


def to_api_message(row: dict) -> dict:
    """Convert one stored message row into a wire message."""
    role = row["role"]
    msg: dict[str, Any] = {"role": role, "content": row.get("content") or ""}

    if role == "assistant":
        tool_calls = normalize_tool_calls(row.get("tool_calls"))
        if tool_calls:
            msg["tool_calls"] = tool_calls
            # Thinking mode rejects an *open* tool turn -- one the model is
            # being asked to continue -- unless every assistant message in it
            # carries its reasoning back. Measured against the live API: with
            # the turn still open, dropping it on any of them returns 400,
            # including when only the most recent keeps it. Once a later user
            # message closes the turn, none of them need it, and compaction
            # marks them so.
            if row.get("reasoning_content") and (row.get("send_reasoning", 1) != 0):
                msg["reasoning_content"] = row["reasoning_content"]
        # Assistant messages without tool calls always terminate a turn, and the
        # API neither needs nor uses their reasoning. Dropping it shrinks the
        # context, and the rule is keyed on an immutable property of the row, so
        # the prompt prefix stays byte-stable and remains cacheable.
    elif role == "tool":
        msg["tool_call_id"] = row.get("tool_call_id") or ""

    return msg


def pending_tool_calls(rows: list[dict]) -> tuple[dict | None, list[dict]]:
    """Find tool calls from the most recent assistant turn that have no result yet.

    Returns ``(assistant_row, [unanswered_tool_calls])``. This is how the agent
    resumes after pausing for a permission prompt: rather than holding state in
    memory, the outstanding work is derived from what is missing in the database,
    which also makes the flow crash-safe across restarts.
    """
    for i in range(len(rows) - 1, -1, -1):
        row = rows[i]
        if row["role"] != "assistant":
            continue
        calls = normalize_tool_calls(row.get("tool_calls"))
        if not calls:
            return None, []
        answered = {
            r.get("tool_call_id")
            for r in rows[i + 1:]
            if r["role"] == "tool" and r.get("tool_call_id")
        }
        return row, [c for c in calls if c["id"] not in answered]
    return None, []


def sanitize(messages: list[dict]) -> list[dict]:
    """Drop structurally invalid sequences the API would reject.

    Guards against corruption from interrupted runs: assistant messages whose
    tool calls were never answered, and orphaned tool results whose originating
    assistant message was compacted away.
    """
    out: list[dict] = []
    i = 0
    while i < len(messages):
        msg = messages[i]

        if msg["role"] == "tool":
            # Only valid directly after an assistant turn that requested this id.
            prev = out[-1] if out else None
            valid = False
            if prev and prev["role"] in ("assistant", "tool"):
                for j in range(len(out) - 1, -1, -1):
                    if out[j]["role"] == "assistant":
                        ids = {tc["id"] for tc in out[j].get("tool_calls", [])}
                        valid = msg.get("tool_call_id") in ids
                        break
                    if out[j]["role"] != "tool":
                        break
            if valid:
                out.append(msg)
            i += 1
            continue

        if msg["role"] == "assistant" and msg.get("tool_calls"):
            requested = {tc["id"] for tc in msg["tool_calls"]}
            answered = set()
            j = i + 1
            while j < len(messages) and messages[j]["role"] == "tool":
                answered.add(messages[j].get("tool_call_id"))
                j += 1
            if not requested.issubset(answered):
                # Unresolved turn: keep any prose, drop the dangling calls.
                if (msg.get("content") or "").strip():
                    out.append({"role": "assistant", "content": msg["content"]})
                i = j
                continue

        out.append(msg)
        i += 1

    # An empty assistant message with no tool calls carries no information.
    return [
        m for m in out
        if not (m["role"] == "assistant" and not m.get("tool_calls") and not (m.get("content") or "").strip())
    ]


# A model has no sense of elapsed time, and users assume it does -- they come
# back a week later and say "carry on with that", or ask why it does not know
# it is a new day. Below an hour is not worth saying; a pause that short is
# just someone getting a cup of tea.
MIN_GAP_SECONDS = 3600


def elapsed_note(previous: str, current: str) -> str:
    """How long passed between two messages, in words, or "" if not long."""
    from datetime import datetime

    try:
        was = datetime.fromisoformat(previous)
        now = datetime.fromisoformat(current)
    except (TypeError, ValueError):
        return ""
    seconds = (now - was).total_seconds()
    if seconds < MIN_GAP_SECONDS:
        return ""

    hours = seconds / 3600
    if hours < 2:
        return "about an hour later"
    if hours < 24:
        return f"{round(hours)} hours later"
    days = hours / 24
    if days < 2:
        return "the next day"
    if days < 14:
        return f"{round(days)} days later"
    weeks = days / 7
    if weeks < 9:
        return f"{round(weeks)} weeks later"
    months = days / 30
    if months < 18:
        return f"{round(months)} months later"
    return f"{round(days / 365)} years later"


def build_messages(
    system_prompt: str,
    compactions: list[dict],
    rows: list[dict],
) -> list[dict]:
    """Assemble the full request payload for a turn."""
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    for c in compactions:
        messages.append({
            "role": "system",
            "content": f"[Summary of earlier conversation]\n{c['summary_text']}",
        })

    previous_at = ""
    for row in rows:
        message = to_api_message(row)
        # Marked on the wire only; the stored message is untouched, so the note
        # never appears in the user's own bubble as though they had typed it.
        if row.get("role") == "user" and previous_at:
            note = elapsed_note(previous_at, row.get("created_at") or "")
            if note and isinstance(message.get("content"), str):
                message = {**message, "content": f"({note})\n{message['content']}"}
        previous_at = row.get("created_at") or previous_at
        messages.append(message)
    return sanitize(messages)
