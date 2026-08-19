"""Shared types for tool implementations."""

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent_server.config import DATA_DIR


@dataclass
class ToolContext:
    """Per-invocation environment handed to every tool."""
    session_id: str
    project_dir: str
    provider: str = "deepseek"
    model: str = "deepseek-v4-pro"
    # Empty means "same model as this session".
    subagent_model: str = ""
    # The parent session's prompt profile, so the subagent can inherit its
    # subagent-prompt and tool configuration.
    prompt_profile: str = "default"
    # Which tier of subagent this is. 0 = first-level subagent, 1 = subsubagent,
    # etc. Incremented each time `task` launches from within a subagent.
    subagent_tier: int = 0
    abort: asyncio.Event = field(default_factory=asyncio.Event)

    def resolve(self, path: str | None) -> Path:
        """Resolve a possibly-relative path against the session's project dir."""
        if not path:
            return Path(self.project_dir)
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = Path(self.project_dir) / p
        return p


@dataclass
class ToolResult:
    """Outcome of a tool call.

    `output` is what the model sees. `title` is a one-line human summary for the
    transcript. `diff` is an optional unified diff rendered inline by the UI --
    it is deliberately not sent to the model, which already knows what it wrote.
    """
    output: str
    is_error: bool = False
    title: str = ""
    diff: str = ""
    # highlight.js language for `code`/`diff`, so the UI can syntax-highlight
    # what the model saw as raw text. Empty means "don't highlight".
    lang: str = ""
    # The code this result is meant to display (e.g. a file read without the
    # [path#tag] header and line numbers). Display-only, never sent to the model.
    code: str = ""
    # 1-indexed line number of the first line in `code`, for the gutter.
    code_start: int = 1
    # Token usage for tools that call a model themselves, so their spend is
    # attributed to the session instead of vanishing.
    usage: dict | None = None
    # When a manager tool creates or opens a session, this carries its id so the
    # UI can offer an "open it" button. Display-only, never sent to the model.
    open_session: str = ""
    # Paths to pictures this call captured. Unlike everything else here they do
    # not go in `output` -- they are sent to the model as pictures, so it can
    # look at what it just did rather than read a path and take it on trust.
    images: list[str] = field(default_factory=list)

    @classmethod
    def error(cls, message: str, title: str = "", usage: dict | None = None) -> "ToolResult":
        return cls(
            output=f"Error: {message}", is_error=True, title=title or "error",
            usage=usage or None,
        )


def unified_diff(before: str, after: str, path: str, context: int = 3) -> str:
    """Compact unified diff for display. Empty when nothing changed."""
    import difflib

    lines = list(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=path,
        tofile=path,
        n=context,
    ))
    if not lines:
        return ""
    # Drop the ---/+++ header; the UI already shows the filename. The @@ hunk
    # headers are kept: they carry the line numbers the UI renders in its gutter.
    body = lines[2:] if len(lines) > 2 and lines[0].startswith("---") else lines
    text = "".join(body)
    if len(text) > 20_000:
        text = text[:20_000] + "\n... [diff truncated]"
    return text


def diff_stats(diff: str) -> tuple[int, int]:
    added = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---"))
    return added, removed


def truncate(text: str, limit: int, note: str = "output", spill: bool = False) -> str:
    """Cut `text` to `limit`, optionally keeping the discarded tail on disk.

    Without a spill the overflow is gone for good, which is how a grep that
    matched slightly too much could hide the one line that mattered. With one,
    the full text is written to a file and the model is told where, so it can go
    and read the part it needs instead of guessing or re-running the tool.

    The result never exceeds `limit`: the marker is measured first and the text
    is cut short to make room. Otherwise a second pass through truncate -- and
    every tool result goes through at least two -- would trim the marker off the
    end and lose the pointer it just wrote.
    """
    if len(text) <= limit:
        return text
    path = _spill(text) if spill else None
    if path:
        # Say what to do, not just what happened. Told only that output was
        # truncated, a model re-runs the tool with a narrower argument and pays
        # for the whole thing twice; told where the rest is, it can `grep` the
        # file or hand it to `explore` and keep the bulk out of context.
        where = (
            f". The full {len(text):,} characters are at {path} -- "
            f"`grep` it, `read` it with offset/limit, or give the path to "
            f"`explore`. Do not re-run this call hoping for less output"
        )
    else:
        where = ""
    marker = f"\n\n... [{note} truncated at {limit:,} characters{where}]"
    # The result must never exceed `limit`, or a second pass -- and every tool
    # result goes through at least two -- trims the marker off the end and
    # loses the pointer it just wrote. When the advice does not fit, keep the
    # path and drop the prose; when even that does not fit, keep the fact.
    if len(marker) >= limit:
        marker = f"\n\n... [truncated; full output at {path}]" if path else "\n\n... [truncated]"
    if len(marker) >= limit:
        marker = "..."
    return text[: max(0, limit - len(marker))] + marker


# Under the data directory with everything else. It was a second, older
# location left behind when the database moved, so spilled output survived a
# clean-up of the data directory and nothing pointed at it.
SPILL_DIR = DATA_DIR / "tool-output"
SPILL_MAX_AGE = 2 * 24 * 60 * 60


def _spill(text: str) -> Path | None:
    """Write an over-long tool output somewhere the model can read it.

    Named by content hash, so re-running the same command reuses one file
    instead of littering. Best effort throughout: a full disk or a read-only
    home must degrade to ordinary truncation, never break the tool call.
    """
    try:
        SPILL_DIR.mkdir(parents=True, exist_ok=True)
        path = SPILL_DIR / f"{hashlib.sha1(text.encode()).hexdigest()[:16]}.txt"
        if path.exists():
            # Reusing the file has to count as using it. Without this the clock
            # keeps running from the first write, so output spilled again on day
            # two would be handed to the model and deleted moments later.
            path.touch()
        else:
            path.write_text(text)
        _prune_spills()
        return path
    except OSError:
        return None


def _prune_spills():
    """Delete anything untouched for two days. These exist for the tool call
    that produced them and the few that follow it; nothing reads them later."""
    cutoff = time.time() - SPILL_MAX_AGE
    for old in SPILL_DIR.glob("*.txt"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass
