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


def stored_images(row: dict) -> list[str]:
    """The picture paths on a message row, however they were stored."""
    raw = row.get("images")
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    return [str(p) for p in raw] if isinstance(raw, list) else []


def image_parts(paths: list[str]) -> list[dict]:
    """Picture paths as OpenAI content parts, skipping any that cannot be read.

    Every provider this app talks to takes the OpenAI shape, so this is what
    goes on the wire unchanged. A provider with its own spelling for a picture
    would translate here, on the way out.
    """
    from agent_server import images as pictures

    parts = []
    for path in paths:
        url = pictures.data_url(path)
        if url:
            parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def describe_unseen(paths: list[str]) -> str:
    """What goes in place of a picture the model has no way of looking at.

    Written as a fact the model can act on rather than an apology, because the
    thing it must not do is guess. It sits at the point of use instead of in
    the system prompt: whether pictures work depends on the model, and the
    model can be changed halfway through a conversation.
    """
    from pathlib import Path as _Path

    names = ", ".join(_Path(p).name for p in paths) or "a picture"
    plural = "pictures" if len(paths) > 1 else "a picture"
    return (
        f"[{plural.capitalize()} here ({names}), which you cannot see: the model "
        "you are running as does not accept images. Tell the user that plainly "
        "and ask them to describe it in words. Do not guess at what it shows.]"
    )


def to_api_message(row: dict, sees_images: bool = False) -> dict:
    """Convert one stored message row into a wire message.

    `sees_images` is the current model's capability, not the message's: a
    conversation that started on DeepSeek and moved to Claude should show
    Claude the pictures it can now see.
    """
    role = row["role"]
    msg: dict[str, Any] = {"role": role, "content": row.get("content") or ""}

    pictures = stored_images(row)
    if pictures and role in ("user", "tool"):
        text = msg["content"]
        if not sees_images:
            note = describe_unseen(pictures)
            msg["content"] = f"{text}\n\n{note}" if text else note
        elif role == "user":
            # A tool result has to stay a plain string -- the OpenAI-compatible
            # providers reject parts on a `tool` message -- so those are picked
            # up by `build_messages` and sent as a user turn just after.
            parts: list[dict] = [{"type": "text", "text": text}] if text else []
            parts.extend(image_parts(pictures))
            if len(parts) > (1 if text else 0):
                msg["content"] = parts

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


# How many messages carrying pictures keep them. A screenshot is on the order
# of 1,500 tokens and every one of them is re-sent on every turn, so an agent
# that checks its own work visually a dozen times would otherwise be paying for
# twenty thousand tokens of stale screenshots for the rest of the session.
#
# Older ones become a line of text saying a picture was there. This does move
# the cache boundary each time the limit is crossed, which is a real cost --
# but it is paid once per new picture, against re-sending every old one forever.
MAX_PICTURES_IN_CONTEXT = 8

_DROPPED = (
    "[A picture was here. It has scrolled out of what is kept in view -- take it "
    "again if you still need to look at it.]"
)


# Told to the model on every turn when the setting is on, as a system message
# after the frozen prompt rather than inside it. The setting can be changed at
# any time and a session's prompt is frozen the first time it is used, so
# putting it there means a user who turns it on today is still being written for
# as though they could see, in every conversation they already had.
#
# What this note does NOT say is "do not open a preview". A screen reader reads
# a browser window perfectly well -- that is what it is for -- so a blind user
# gets more out of the thing running than out of a description of it.
SCREEN_READER_NOTE = """[This user works with a screen reader and does not see \
the screen. Two things follow:

1. Never write anything that assumes sight. No "as you can see", no "it looks \
like this", no pointing at a thing by where it sits. Name it.
2. Whatever you build, they will meet through a screen reader, so it has to \
survive one: every control reachable by Tab and usable by Enter or Space, a real \
label on each, headings in order, and images with alt text. Check it rather than \
assuming it -- `browser` can Tab through a page and tell you what it found.

Open the preview as you would for anyone. They can read it.]"""


def build_messages(
    system_prompt: str,
    compactions: list[dict],
    rows: list[dict],
    sees_images: bool = False,
    screen_reader: bool = False,
) -> list[dict]:
    """Assemble the full request payload for a turn."""
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if screen_reader:
        messages.append({"role": "system", "content": SCREEN_READER_NOTE})
    for c in compactions:
        messages.append({
            "role": "system",
            "content": f"[Summary of earlier conversation]\n{c['summary_text']}",
        })

    # Only meaningful when pictures are being sent at all. On a text-only model
    # every row would otherwise be marked "scrolled out of view -- take it
    # again", which is the wrong explanation and, for something the user
    # attached, an impossible instruction. Those rows want `describe_unseen`.
    keep = _recent_picture_rows(rows) if sees_images else None

    # A tool result cannot carry a picture: the OpenAI-compatible providers take
    # a plain string on a `tool` message and nothing else. So captured frames
    # follow as a user turn instead -- but only once the whole run of tool
    # results is out, because `sanitize` requires them to be consecutive and
    # would drop the second of two parallel calls if a user turn split them.
    pending: list[str] = []

    def flush():
        if not pending:
            return
        parts = image_parts(pending)
        pending.clear()
        if not parts:
            return
        label = "Here is what that captured." if len(parts) == 1 else \
            f"Here are the {len(parts)} frames that were captured."
        messages.append({"role": "user", "content": [{"type": "text", "text": label}, *parts]})

    previous_at = ""
    for index, row in enumerate(rows):
        pictures = stored_images(row)
        if pictures and keep is not None and index not in keep:
            row = {**row, "images": None,
                   "content": f"{row.get('content') or ''}\n\n{_DROPPED}".strip()}
            pictures = []

        if row.get("role") != "tool":
            flush()

        message = to_api_message(row, sees_images)
        # Marked on the wire only; the stored message is untouched, so the note
        # never appears in the user's own bubble as though they had typed it.
        if row.get("role") == "user" and previous_at:
            note = elapsed_note(previous_at, row.get("created_at") or "")
            if note and isinstance(message.get("content"), str):
                message = {**message, "content": f"({note})\n{message['content']}"}
        previous_at = row.get("created_at") or previous_at
        messages.append(message)

        if pictures and sees_images and row.get("role") == "tool":
            pending.extend(pictures)
    flush()
    return sanitize(messages)


def _recent_picture_rows(rows: list[dict]) -> set[int]:
    """Indexes of the last `MAX_PICTURES_IN_CONTEXT` rows that carry pictures."""
    found = [i for i, row in enumerate(rows) if stored_images(row)]
    return set(found[-MAX_PICTURES_IN_CONTEXT:])
