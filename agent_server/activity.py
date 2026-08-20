"""What the assistant did between two messages, in words a person can hold.

The assistant works for a minute and a reply appears. Nothing in between says
anything happened, so a conversation reads as ten replies in a row with no hint
that files were written or a website was opened and checked. This is that
minute, made legible.

Tools are grouped into families rather than listed one by one. "Read 4 files" is
something a person takes in at a glance; four lines saying `read` are noise, and
the user is not supposed to know what a tool is in the first place.

The families live here, in one place, and are handed to the browser as JSON.
They are needed twice -- once while a turn is running, where the browser is
counting events as they arrive, and once when a conversation is loaded back from
the database -- and two copies of this table would drift the first time a tool
was added.
"""

from __future__ import annotations

# glyph: a plain character, deliberately. An icon font is another thing to load
# and another thing to fail; these render the same everywhere.
# note: the pitch its sound plays at, so each kind of work is audibly distinct
# for somebody who is listening rather than watching.
FAMILIES: dict[str, dict] = {
    "write":   {"glyph": "✎", "one": "wrote a file",        "many": "wrote {n} files",        "note": 740},
    "run":     {"glyph": "▸", "one": "ran a command",       "many": "ran {n} commands",       "note": 590},
    "look":    {"glyph": "◎", "one": "read a file",         "many": "read {n} files",         "note": 520},
    "web":     {"glyph": "⌁", "one": "looked something up", "many": "looked up {n} things",   "note": 660},
    "think":   {"glyph": "✲", "one": "researched",          "many": "researched {n} things",  "note": 820},
    "see":     {"glyph": "▣", "one": "checked the screen",  "many": "checked {n} times",      "note": 620},
    "project": {"glyph": "❖", "one": "sorted the project",  "many": "sorted the project",     "note": 700},
}

TOOL_FAMILY: dict[str, str] = {
    "write": "write", "edit": "write",
    "bash": "run", "preview": "run",
    "read": "look", "grep": "look", "glob": "look",
    "webfetch": "web", "websearch": "web",
    "task": "think", "explore": "think",
    "browser": "see", "capture": "see",
    "create_project": "project", "open_project": "project", "rename_project": "project",
    "delete_project": "project", "list_projects": "project", "assign_project": "project",
    "set_theme": "project",
}

DEFAULT_FAMILY = "run"


def family_of(tool_name: str) -> str:
    return TOOL_FAMILY.get(tool_name or "", DEFAULT_FAMILY)


def chips(counts: dict[str, int]) -> list[dict]:
    """`{family: n}` as the chips to render, in a stable order.

    Ordered by the family table rather than by when each happened, so the same
    work always reads the same way round and the eye learns where to look.
    """
    out = []
    for family, spec in FAMILIES.items():
        n = counts.get(family, 0)
        if not n:
            continue
        text = spec["one"] if n == 1 else spec["many"].replace("{n}", str(n))
        out.append({"family": family, "glyph": spec["glyph"], "text": text})
    return out


def sentence(counts: dict[str, int]) -> str:
    """The same thing as one spoken line, for anyone who is listening."""
    parts = [c["text"] for c in chips(counts)]
    if not parts:
        return ""
    if len(parts) == 1:
        return f"I {parts[0]}."
    return "I " + ", ".join(parts[:-1]) + " and " + parts[-1] + "."


def _is_working(m: dict) -> bool:
    """Whether this row is the assistant working rather than saying something.

    An assistant turn that only asks for tools has no words in it and is never
    drawn, so it must not break the run either. Without this a turn that reached
    for tools four times produced four separate strips stacked on top of each
    other -- each one true, and the four together much harder to read than the
    one line they should have been.
    """
    if m.get("kind") == "summary":
        return False
    if m.get("role") == "tool":
        return True
    return m.get("role") == "assistant" and not (m.get("content") or "").strip()


def group(messages: list[dict]) -> list[dict]:
    """Fold each stretch of the assistant working into one activity item.

    The boundary between two stretches is a message somebody actually said --
    the user, or the assistant with words in it.
    """
    out: list[dict] = []
    counts: dict[str, int] = {}

    def flush():
        if counts:
            out.append({"kind": "activity", "chips": chips(counts), "sentence": sentence(counts)})
            counts.clear()

    for m in messages:
        if _is_working(m):
            if m.get("role") == "tool":
                family = family_of(m.get("tool_name"))
                counts[family] = counts.get(family, 0) + 1
            # A tool that offers the user a button still has to be drawn, so it
            # survives the fold rather than being swallowed by it.
            if m.get("open_session"):
                flush()
                out.append(m)
            continue
        flush()
        out.append(m)
    flush()
    return out
