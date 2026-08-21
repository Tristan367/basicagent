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
    "task": "think",
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


def sentence(counts: dict[str, int], failures: int = 0) -> str:
    """The same thing as one spoken line, for anyone who is listening."""
    parts = [c["text"] for c in chips(counts)]
    if not parts:
        return ""
    said = f"I {parts[0]}." if len(parts) == 1 else (
        "I " + ", ".join(parts[:-1]) + " and " + parts[-1] + "."
    )
    if failures:
        said += " One of those failed." if failures == 1 else f" {failures} of those failed."
    return said


def short_label(tool: str, title: str) -> str:
    """What one tool call is called, in the list you get when you open a group.

    A file tool's title is its whole path, elided from the left to fit -- fine
    as a tooltip, useless as a list of what happened. The last segment is the
    part a person recognises, and the "(436 lines)" the tool appended to it is
    worth keeping. Everything else already titles itself well: `bash` gives the
    command, `grep` the pattern, `browser` the steps it ran.
    """
    title = (title or "").strip()
    if tool in _PATH_TOOLS and "/" in title:
        return title.rsplit("/", 1)[-1]
    return title or tool or "did something"


_PATH_TOOLS = {"read", "write", "edit"}

# Every diff in a conversation, held in the page at once, is a lot of page. This
# is a summary of the work rather than the record of it -- the whole thing is in
# the project's own history, which is what git is there for.
MAX_DIFF_CHARS = 6000


def detail(m: dict) -> dict:
    """One tool call, as the expanded view needs it."""
    tool = m.get("tool_name") or ""
    diff = (m.get("diff") or "")
    return {
        "family": family_of(tool),
        "glyph": FAMILIES[family_of(tool)]["glyph"],
        "tool": tool,
        "label": short_label(tool, m.get("tool_title") or ""),
        "full": (m.get("tool_title") or "").strip(),
        "diff": diff[:MAX_DIFF_CHARS],
        "clipped": len(diff) > MAX_DIFF_CHARS,
        "failed": bool(m.get("is_error")),
        "ms": m.get("duration_ms") or 0,
    }


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
    calls: list[dict] = []
    broke_off = False

    def flush():
        nonlocal broke_off
        if counts:
            # A call that failed still counts towards "read 4 files" -- the work
            # was attempted, and a turn where everything failed would otherwise
            # show no chips at all and read as though nothing had happened. But
            # "wrote a file" on its own, when the write was refused, is a lie,
            # so the group says how many did not work and the list inside marks
            # which.
            failed = sum(1 for c in calls if c["failed"])
            out.append({"kind": "activity", "chips": chips(counts),
                        "sentence": sentence(counts, failed), "calls": list(calls),
                        "failures": failed, "broke_off": broke_off})
            counts.clear()
            calls.clear()
            broke_off = False

    for m in messages:
        if _is_working(m):
            if m.get("role") == "tool":
                family = family_of(m.get("tool_name"))
                counts[family] = counts.get(family, 0) + 1
                # Kept alongside the count, so opening the group can say which
                # four files rather than only that there were four.
                calls.append(detail(m))
                # The row this was folded into carries the mark, or the fold
                # would swallow the fact that the user stopped here.
                broke_off = broke_off or bool(m.get("broke_off"))
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
