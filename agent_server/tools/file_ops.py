"""File reading and editing tools."""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from agent_server.permissions import is_denied
from agent_server.tools.base import ToolContext, ToolResult, diff_stats, truncate, unified_diff

MAX_READ_BYTES = 2_000_000
DEFAULT_LIMIT = 2000
MAX_LINE_CHARS = 2000

BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".pdf", ".zip",
    ".gz", ".tar", ".bz2", ".xz", ".7z", ".exe", ".dll", ".so", ".dylib",
    ".class", ".jar", ".pyc", ".o", ".a", ".wasm", ".mp3", ".mp4", ".mov",
    ".wav", ".ogg", ".woff", ".woff2", ".ttf", ".sqlite", ".db",
}

# File extension -> highlight.js language. Keys are lowercase with the dot.
# Unknown extensions fall back to "" so the UI just shows plain text.
_EXT_LANG = {
    ".py": "python", ".pyw": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".json": "json", ".jsonc": "json",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash", ".fish": "bash",
    ".html": "xml", ".htm": "xml", ".xml": "xml", ".svg": "xml",
    ".css": "css", ".scss": "css", ".sass": "css", ".less": "css",
    ".md": "markdown", ".markdown": "markdown",
    ".go": "go", ".rs": "rust", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin",
    ".sql": "sql", ".yaml": "yaml", ".yml": "yaml",
    ".toml": "ini", ".ini": "ini", ".cfg": "ini", ".conf": "ini",
    ".dockerfile": "dockerfile", ".rb": "ruby", ".php": "php",
    ".cs": "csharp", ".swift": "swift", ".scala": "scala",
    ".lua": "lua", ".r": "r", ".pl": "perl", ".vim": "vim",
    ".makefile": "makefile", ".cmake": "cmake", ".gradle": "gradle",
    ".graphql": "graphql", ".proto": "protobuf", ".diff": "diff", ".patch": "diff",
    ".nix": "nix", ".hs": "haskell", ".ex": "elixir", ".exs": "elixir",
    ".erl": "erlang", ".clj": "clojure", ".dart": "dart",
    ".tf": "hcl", ".tfvars": "hcl",
}


def lang_for_path(path: Path) -> str:
    """highlight.js language for a file, so reads/diffs render highlighted."""
    name = path.name.lower()
    if name == "dockerfile":
        return "dockerfile"
    if name == "makefile":
        return "makefile"
    return _EXT_LANG.get(path.suffix.lower(), "")

# Files the model has read this session; `edit`/`write` require a prior read so
# the model cannot blindly clobber a file it has never seen.
_read_files: dict[str, set[str]] = {}

# UTF-8 BOM as raw bytes.
_BOM = b"\xef\xbb\xbf"


def _detect_line_ending(text: str) -> str:
    """Return the dominant line ending: ``\\r\\n`` or ``\\n``."""
    crlf = text.count("\r\n")
    lf_only = text.count("\n") - crlf
    return "\r\n" if crlf > lf_only else "\n"


def _read_file_text(path: Path) -> tuple[str, bool, str]:
    """Read *path* and return ``(content, has_bom, line_ending)``.

    ``content`` has any leading UTF-8 BOM stripped and all line endings
    normalised to ``\\n`` so edits operate on a canonical form.
    """
    raw = path.read_bytes()
    has_bom = raw.startswith(_BOM)
    if has_bom:
        raw = raw[len(_BOM):]
    text = raw.decode("utf-8")
    line_ending = _detect_line_ending(text)
    if line_ending == "\r\n":
        text = text.replace("\r\n", "\n")
    return text, has_bom, line_ending


def _write_file_text(path: Path, content: str, has_bom: bool, line_ending: str):
    """Write *content* to *path*, prepending a BOM and converting line endings
    back to what the file originally used."""
    if line_ending == "\r\n":
        content = content.replace("\n", "\r\n")
    data = content.encode("utf-8")
    if has_bom:
        data = _BOM + data
    path.write_bytes(data)


def _normalise_for_tag(content: str) -> str:
    """Ignore trailing whitespace and line endings when fingerprinting.

    A CRLF file, or one the reader trimmed for display, would otherwise produce
    a tag that never matches what was shown.
    """
    return "\n".join(line.rstrip(" \t\r") for line in content.splitlines())


def file_tag(content: str) -> str:
    """4-hex fingerprint of the whole file.

    One tag per file, not one hash per line. Per-line hashes cost about six
    characters of every line -- some 2,500 tokens on a 2,000-line read, on every
    read, forever -- and they answer the wrong question. The risk is not "did
    line 40 change", it is "did the file shift so line 40 is now something
    else", and a per-line hash is satisfied by any duplicate line elsewhere in
    the file. Every `}` and every blank line collides. A whole-file tag makes
    any drift anywhere invalidate every anchor, which is the conservative
    answer, and the error then says to re-read.
    """
    return hashlib.blake2b(_normalise_for_tag(content).encode(), digest_size=2).hexdigest()


@dataclass
class Snapshot:
    """What a session was actually shown of a file, and when."""

    tag: str
    content: str
    seen: set[int]  # 1-based line numbers displayed, not merely present


# (session_id, resolved path) -> Snapshot
_snapshots: dict[tuple[str, str], Snapshot] = {}


def _record_snapshot(session_id: str, path: Path, content: str, seen: set[int]) -> str:
    tag = file_tag(content)
    key = (session_id, str(path))
    previous = _snapshots.get(key)
    # Reading a second window of the same unchanged file adds to what has been
    # seen rather than replacing it, so a two-part read can be edited as one.
    if previous is not None and previous.tag == tag:
        seen = previous.seen | seen
    _snapshots[key] = Snapshot(tag=tag, content=content, seen=seen)
    return tag


def clear_read_cache(session_id: str = ""):
    """Release the read-tracking for a session, or all of them."""
    if session_id:
        _read_files.pop(session_id, None)
    else:
        _read_files.clear()


def mark_read(session_id: str, path: Path):
    _read_files.setdefault(session_id, set()).add(str(path))


def has_read(session_id: str, path: Path) -> bool:
    return str(path) in _read_files.get(session_id, set())


async def read_file(
    ctx: ToolContext,
    *,
    filePath: str,
    offset: int = 0,
    limit: int = DEFAULT_LIMIT,
    **_,
) -> ToolResult:
    path = ctx.resolve(filePath)
    title = _title_path(path)

    if not path.exists():
        suggestion = _suggest(path)
        return ToolResult.error(f"file not found: {path}{suggestion}", title)
    if path.is_dir():
        try:
            entries = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir())
        except PermissionError:
            return ToolResult.error(f"permission denied reading directory: {path}", title)
        return ToolResult(
            output=f"{path} is a directory. Contents:\n" + "\n".join(entries[:200]),
            title=_title_path(path),
        )
    if path.suffix.lower() in BINARY_SUFFIXES:
        return ToolResult.error(f"cannot read binary file as text: {path}", title)
    if path.stat().st_size > MAX_READ_BYTES:
        return ToolResult.error(
            f"file too large ({path.stat().st_size:,} bytes). Use offset/limit or grep.", title
        )

    try:
        content, _bom, _le = _read_file_text(path)
    except Exception as e:
        return ToolResult.error(f"reading file: {e}", title)

    if "\x00" in content[:8192]:
        return ToolResult.error(f"cannot read binary file as text: {path}", title)

    lines = content.splitlines()
    total = len(lines)
    if not total:
        return ToolResult(output=f"(file is empty: {path})", title=title)

    limit = max(1, limit or DEFAULT_LIMIT)
    start = max(0, (offset - 1) if offset and offset > 0 else 0)
    if start >= total:
        return ToolResult.error(f"offset {offset} is past end of file ({total} lines)", title)
    end = min(total, start + limit)

    numbered = []
    code_lines = []
    for idx in range(start, end):
        line = lines[idx]
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS] + "... [line truncated]"
        numbered.append(f"{idx + 1}: {line}")
        code_lines.append(line)

    tag = _record_snapshot(ctx.session_id, path, content, set(range(start + 1, end + 1)))

    # The tag goes in a header, once, rather than on every line. `edit` requires
    # it back, which is what proves the edit is anchored to this reading of the
    # file and not to a guess or to a stale one.
    header = f"[{_display(path, ctx)}#{tag}]"
    output = header + "\n" + "\n".join(numbered)
    if end < total:
        output += (
            f"\n\n... ({total - end:,} more lines not shown; continue with "
            f"offset={end + 1}. Lines you have not been shown cannot be edited.)"
        )

    mark_read(ctx.session_id, path)
    return ToolResult(
        output=output,
        title=f"{title} ({total} lines)",
        # Display-only: the file's contents without the header or line numbers,
        # so the UI can syntax-highlight it. `output` above stays model-facing.
        code="\n".join(code_lines),
        code_start=start + 1,
        lang=lang_for_path(path),
    )


def _shift_seen(snapshot, start: int, replaced: int, inserted: int) -> set[int]:
    """Carry the seen-line set across an edit.

    The replaced span stays seen -- the caller just wrote it -- and everything
    below it moves. Recomputing from scratch would forget the rest of a file
    that was read in two windows.
    """
    if snapshot is None:
        return set(range(start, start + max(inserted, 1)))
    shift = inserted - replaced
    end = start + replaced - 1
    moved = {n if n < start else n + shift for n in snapshot.seen if n < start or n > end}
    return moved | set(range(start, start + max(inserted, 1)))


def _check_anchor(
    session_id: str, path: Path, content: str, tag: str, start: int, end: int
) -> str:
    """Why this edit must not be applied, or "" if it may be.

    Three distinct failures, told apart because the fix differs:
      - no tag at all, or lines without one
      - a tag that is not this file's current state
      - lines the caller was never shown
    """
    if not tag:
        return (
            "no tag given. `read` prints one as [path#tag] above the lines; pass "
            "it back so the edit is anchored to what you actually saw."
        )
    if not start:
        return "startLine is required with a tag."

    lines = content.splitlines()
    if start < 1 or start > len(lines):
        return f"startLine {start} is outside {path}, which has {len(lines)} lines."
    if end and end < start:
        # Swapping these silently deleted a span the caller never named.
        return (
            f"endLine {end} is above startLine {start}. Give them in the order "
            "they appear."
        )
    if end and end > len(lines):
        return f"endLine {end} is past the end of {path} ({len(lines)} lines)."

    snapshot = _snapshots.get((session_id, str(path)))
    current = file_tag(content)

    if snapshot is None:
        return (
            f"no read of {path} in this session to anchor to. Read it, then use "
            "the tag it prints."
        )
    if tag != current:
        # Distinguish a tag that was never real from one the file has outgrown.
        # The first means the tag was invented or copied from another file; the
        # second means someone edited underneath us. Same symptom, different fix.
        if tag == snapshot.tag:
            return (
                f"{path} has changed since you read it (tag was {tag}, now "
                f"{current}). Read it again -- the line numbers you have may no "
                "longer point at the same code."
            )
        return (
            f"tag {tag} is not a tag this session was given for {path}. The "
            f"current one is {current}. Do not construct or guess a tag: read "
            "the file and copy the one in the header."
        )

    unseen = sorted(n for n in range(start, (end or start) + 1) if n not in snapshot.seen)
    if unseen:
        shown = f"{min(snapshot.seen)}-{max(snapshot.seen)}" if snapshot.seen else "none"
        return (
            f"lines {unseen[0]}-{unseen[-1]} were not shown to you (you have seen "
            f"{shown}). Editing a line you have not read is guessing. Re-read with "
            f"offset={unseen[0]} first."
        )
    return ""


async def edit_file(
    ctx: ToolContext,
    *,
    filePath: str,
    oldString: str = "",
    newString: str = "",
    replaceAll: bool = False,
    tag: str = "",
    startLine: int = 0,
    endLine: int = 0,
    newText: str = "",
    **_,
) -> ToolResult:
    path = ctx.resolve(filePath)
    title = _title_path(path)

    if is_denied(path):
        return ToolResult.error(f"refusing to edit a protected system path: {path}", title)
    if not path.exists():
        return ToolResult.error(f"file not found: {path}. Use `write` to create it.", title)
    if not path.is_file():
        return ToolResult.error(f"not a file: {path}", title)
    if not has_read(ctx.session_id, path):
        return ToolResult.error(
            f"you must read {path} before editing it", title
        )

    try:
        content, has_bom, line_ending = _read_file_text(path)
    except Exception as e:
        return ToolResult.error(f"reading file: {e}", title)

    # ── tagged-line mode: anchor edits on the tag from the read that showed them ──
    if tag or startLine:
        problem = _check_anchor(ctx.session_id, path, content, tag, startLine, endLine)
        if problem:
            return ToolResult.error(problem, title)

        lines = content.splitlines()
        start_idx = startLine - 1
        end_idx = (endLine or startLine) - 1

        replaced_lines = end_idx - start_idx + 1
        replacement_lines = len(newText.splitlines()) if newText else 0
        new_lines = (
            lines[:start_idx] + (newText.splitlines() if newText else []) + lines[end_idx + 1:]
        )
        updated = "\n".join(new_lines)
        if content.endswith("\n"):
            updated += "\n"

        try:
            _write_file_text(path, updated, has_bom, line_ending)
        except Exception as e:
            return ToolResult.error(f"writing file: {e}", title)

        # The tag has changed, so re-snapshot and hand the new one back. Without
        # this every edit would force a re-read before the next one.
        shift = replacement_lines - replaced_lines
        seen = _shift_seen(_snapshots.get((ctx.session_id, str(path))), start_idx + 1, replaced_lines, replacement_lines)
        new_tag = _record_snapshot(ctx.session_id, path, updated, seen)

        diff = unified_diff(content, updated, _display(path, ctx))
        summary = (
            f"Edited {path}: replaced {replaced_lines} line"
            f"{'s' if replaced_lines != 1 else ''} at {startLine}"
            + (f"-{endLine}" if endLine and endLine != startLine else "")
            + (f" with {replacement_lines}" if replacement_lines != replaced_lines else "")
            + f".\n[{_display(path, ctx)}#{new_tag}] <- use this tag for your next edit"
        )
        if shift:
            summary += (
                f"\nLines below {startLine} have moved by {shift:+d}; the numbers above "
                "are already adjusted."
            )
        return ToolResult(
            output=summary, title=title,
            diff=diff, lang=lang_for_path(path),
        )

    # ── exact-string mode ──
    if not oldString:
        return ToolResult.error("provide oldString, or tag with startLine", title)
    if oldString == newString:
        return ToolResult.error("oldString and newString are identical", title)

    count = content.count(oldString)
    if count == 0:
        return ToolResult.error(
            f"oldString not found in {path}. The file may have changed since you read it; "
            "read it again and match the exact text including indentation.",
            title,
        )
    if count > 1 and not replaceAll:
        return ToolResult.error(
            f"found {count} occurrences of oldString in {path}. "
            "Add surrounding context to make it unique, or pass replaceAll=true.",
            title,
        )

    updated = content.replace(oldString, newString) if replaceAll else content.replace(oldString, newString, 1)
    try:
        _write_file_text(path, updated, has_bom, line_ending)
    except Exception as e:
        return ToolResult.error(f"writing file: {e}", title)

    replaced = count if replaceAll else 1
    line_no = content[: content.index(oldString)].count("\n") + 1
    diff = unified_diff(content, updated, _display(path, ctx))
    return ToolResult(
        output=f"Edited {path} ({replaced} replacement{'s' if replaced != 1 else ''} at line ~{line_no}).",
        title=title,
        diff=diff,
        lang=lang_for_path(path),
    )


async def write_file(ctx: ToolContext, *, filePath: str, content: str, **_) -> ToolResult:
    path = ctx.resolve(filePath)
    title = _title_path(path)

    if is_denied(path):
        return ToolResult.error(f"refusing to write a protected system path: {path}", title)

    existed = path.exists()
    if existed and path.is_dir():
        return ToolResult.error(f"{path} is a directory", title)
    if existed and not has_read(ctx.session_id, path):
        return ToolResult.error(
            f"{path} already exists and you have not read it. Read it first so you do "
            "not discard existing content, or use `edit` for a targeted change.",
            title,
        )

    previous = ""
    if existed:
        try:
            previous, has_bom, line_ending = _read_file_text(path)
        except Exception:
            previous = ""
            has_bom = False
            line_ending = "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            _write_file_text(path, content, has_bom, line_ending)
        except Exception as e:
            return ToolResult.error(f"writing file: {e}", title)
    else:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except Exception as e:
            return ToolResult.error(f"writing file: {e}", title)

    mark_read(ctx.session_id, path)
    verb = "Overwrote" if existed else "Created"
    lines = len(content.splitlines())
    diff = unified_diff(previous, content, _display(path, ctx))
    added, removed = diff_stats(diff)
    summary = f"{title} (+{added}/-{removed})" if existed else f"{title} ({lines} lines)"
    return ToolResult(
        output=f"{verb} {path} ({lines} lines).",
        title=summary,
        # `diff` feeds the change-summary only. The inline block renders `code`
        # as plain content -- a write is the whole file, so nothing is "added"
        # against a previous version worth colouring green.
        diff=diff,
        code=content,
        code_start=1,
        lang=lang_for_path(path),
    )


def _display(path: Path, ctx: ToolContext) -> str:
    try:
        return str(path.relative_to(ctx.project_dir))
    except ValueError:
        return str(path)


def _title_path(path: Path) -> str:
    """Full absolute path, left-truncated so the filename always shows."""
    full = str(path)
    if len(full) > 60:
        return "\u2026" + full[-59:]
    return full


def _suggest(path: Path) -> str:
    """If the parent exists, hint at similarly-named siblings."""
    parent = path.parent
    if not parent.is_dir():
        return ""
    import difflib

    try:
        names = [p.name for p in parent.iterdir()]
    except OSError:
        return ""
    close = difflib.get_close_matches(path.name, names, n=3, cutoff=0.6)
    return f"\nDid you mean: {', '.join(close)}" if close else ""


__all__ = ["edit_file", "has_read", "mark_read", "read_file", "truncate", "write_file"]
