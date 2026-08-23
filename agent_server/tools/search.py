"""Code search: grep (ripgrep) and glob."""

import asyncio
import os
import re
import shutil
from pathlib import Path

from agent_server.config import MAX_TOOL_RESULT_CHARS
from agent_server.tools.base import ToolContext, ToolResult, truncate

MAX_MATCHES = 300
MAX_GLOB_RESULTS = 300
IGNORED_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", "dist", "build", ".next", ".ruff_cache", "target",
}


async def grep_search(
    ctx: ToolContext,
    *,
    pattern: str,
    path: str | None = None,
    include: str | None = None,
    **_,
) -> ToolResult:
    search_dir = ctx.resolve(path)
    title = f"'{pattern[:60]}'"

    if not pattern or not pattern.strip():
        return ToolResult.error("empty pattern — nothing to search for", title)
    if not search_dir.exists():
        return ToolResult.error(f"path not found: {search_dir}", title)
    if not shutil.which("rg"):
        # Nothing installs ripgrep -- not the installer, not requirements.txt --
        # so on a machine that happens not to have it this tool used to fail
        # outright, with an Arch-only shell command and a suggestion to go and
        # use `grep -rn` instead, which Windows does not have either. Meanwhile
        # the prompt tells the model to prefer this tool over shell grep. So it
        # searched in Python instead: slower on a big tree, right everywhere.
        lines = await asyncio.to_thread(
            _python_grep, pattern, search_dir, include)
        if lines is None:
            return ToolResult.error(f"invalid pattern: {pattern}", title)
        return _report(lines, pattern, search_dir, title)

    cmd = ["rg", "--line-number", "--no-heading", "--color=never",
           "--max-count", "50", "--max-columns", "400", "--smart-case"]
    for d in IGNORED_DIRS:
        cmd += ["--glob", f"!{d}/"]
    if include:
        cmd += ["--glob", include]
    cmd += ["--regexp", pattern, str(search_dir)]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    except TimeoutError:
        # wait_for cancelled communicate() but left ripgrep running.
        try:
            proc.kill()
            await proc.wait()
        except (ProcessLookupError, AttributeError):
            pass
        return ToolResult.error("search timed out after 60s", title)
    except Exception as e:
        return ToolResult.error(f"search failed: {e}", title)

    if proc.returncode not in (0, 1):
        return ToolResult.error(
            stderr.decode("utf-8", errors="replace").strip() or "ripgrep failed", title
        )

    return _report(stdout.decode("utf-8", errors="replace").splitlines(),
                   pattern, search_dir, title)


def _report(lines: list[str], pattern: str, search_dir: Path, title: str) -> ToolResult:
    """Both search paths end here, so both are counted and cut the same way."""
    if not lines:
        return ToolResult(output=f"No matches for '{pattern}' in {search_dir}", title=f"{title} (0)")

    shown = lines[:MAX_MATCHES]
    output = "\n".join(shown)
    if len(lines) > MAX_MATCHES:
        output += f"\n\n... and {len(lines) - MAX_MATCHES:,} more matches. Narrow the pattern or set `include`."
    files = len({ln.split(":", 1)[0] for ln in shown})
    return ToolResult(
        output=truncate(output, MAX_TOOL_RESULT_CHARS, spill=True),
        title=f"{title} ({len(lines)} matches in {files} files)",
    )


# Ripgrep's defaults, reproduced because the caller and the model both expect
# them: `file:line:text`, case-insensitive until the pattern has a capital in
# it, and a cap per file so one minified bundle cannot fill the whole answer.
MAX_PER_FILE = 50
MAX_COLUMNS = 400
# Files a text search has no business reading. Guessed by extension first,
# which is cheap, and then by looking for a NUL byte, which is what ripgrep
# does and catches everything the list misses.
BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tiff", ".pdf",
    ".zip", ".gz", ".bz2", ".xz", ".7z", ".tar", ".jar", ".war", ".class",
    ".so", ".dylib", ".dll", ".exe", ".bin", ".o", ".a", ".pyc", ".pyo",
    ".mp3", ".mp4", ".wav", ".ogg", ".webm", ".mov", ".avi", ".flac",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".db", ".sqlite", ".sqlite3",
}
MAX_FILE_BYTES = 8 * 1024 * 1024


def _python_grep(pattern: str, root: Path, include: str | None) -> list[str] | None:
    """`rg` in Python. None if the pattern will not compile.

    Deliberately the same shape of answer rather than a better one: a tool that
    behaves differently depending on what happens to be installed is worse than
    one that is merely slower.
    """
    flags = 0 if any(c.isupper() for c in pattern) else re.IGNORECASE
    try:
        rx = re.compile(pattern, flags)
    except re.error:
        return None

    globs = _expand_braces(include) if include else []
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            # `_matches` is the same rule `glob` uses, so `include` means here
            # exactly what it means there.
            if globs and not any(_matches(relative, name, g) for g in globs):
                continue
            if path.suffix.lower() in BINARY_EXTS:
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                blob = path.read_bytes()
            except OSError:
                continue
            if b"\x00" in blob[:8192]:
                continue
            hits = 0
            for number, line in enumerate(
                    blob.decode("utf-8", errors="replace").splitlines(), 1):
                if len(line) > MAX_COLUMNS or not rx.search(line):
                    continue
                out.append(f"{path}:{number}:{line}")
                hits += 1
                if hits >= MAX_PER_FILE:
                    break
            # Everything past the cap is thrown away by `_report` anyway, and
            # walking a whole repository after that is time nobody gets back.
            if len(out) > MAX_MATCHES * 2:
                return out
    return out




def _expand_braces(pattern: str) -> list[str]:
    """Turn `*.{js,css}` into `*.js` and `*.css`.

    fnmatch has no brace expansion, so a pattern using one matched nothing and
    came back as "No files matching" -- indistinguishable from a correct
    pattern over an empty tree, and the model would move on believing the files
    were not there.
    """
    start = pattern.find("{")
    if start == -1:
        return [pattern]
    depth = 0
    for i in range(start, len(pattern)):
        if pattern[i] == "{":
            depth += 1
        elif pattern[i] == "}":
            depth -= 1
            if depth == 0:
                head, body, tail = pattern[:start], pattern[start + 1:i], pattern[i + 1:]
                out = []
                for choice in body.split(","):
                    out.extend(_expand_braces(head + choice + tail))
                return out
    return [pattern]  # unbalanced; leave it alone and let it match literally


# fnmatch is the wrong matcher for a path pattern, in both directions.
#
# It has no idea what `**` means: `**/*.py` compiles to a regex wanting a
# literal slash, so it misses every file at the top of the tree -- the agent
# globbed for a file it had written a turn earlier and concluded it was not
# there. And its `*` happily crosses a directory separator, so `src/*.py` also
# returned `src/deep/nested.py`, which is the opposite mistake.
#
# So: translate the pattern properly. `**/` spans any number of directories
# including none, `*` and `?` stop at a separator, and character classes are
# passed through.
_GLOB_CACHE: dict[str, "re.Pattern[str]"] = {}


def _compile_glob(pattern: str) -> "re.Pattern[str]":
    cached = _GLOB_CACHE.get(pattern)
    if cached is not None:
        return cached

    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern[i:i + 3] == "**/":
                # Any depth, including none, so `**/x.py` finds `x.py` at the root.
                out.append("(?:[^/]+/)*")
                i += 3
                continue
            if pattern[i:i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
            i += 1
            continue
        if c == "?":
            out.append("[^/]")
            i += 1
            continue
        if c == "[":
            j = i + 1
            if j < n and pattern[j] in "!^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j >= n:
                out.append(re.escape(c))
                i += 1
                continue
            body = pattern[i + 1:j]
            if body and body[0] in "!^":
                body = "^" + body[1:]
            out.append("[" + body.replace("\\", "\\\\") + "]")
            i = j + 1
            continue
        out.append(re.escape(c))
        i += 1

    compiled = re.compile("".join(out) + r"\Z")
    _GLOB_CACHE[pattern] = compiled
    return compiled


def _matches(rel: str, name: str, pattern: str) -> bool:
    """Whether this file answers the pattern.

    A pattern with no separator in it is matched against the file's name at any
    depth -- `*.py` meaning "every Python file" is what everyone expects and
    what the model writes. A pattern that does contain one is a path pattern and
    is matched as written.
    """
    rx = _compile_glob(pattern)
    if "/" in pattern:
        return bool(rx.match(rel))
    return bool(rx.match(name) or rx.match(rel))


async def glob_search(ctx: ToolContext, *, pattern: str, path: str | None = None, **_) -> ToolResult:
    search_dir = ctx.resolve(path)
    title = f"'{pattern}'"

    if not search_dir.is_dir():
        return ToolResult.error(f"directory not found: {search_dir}", title)

    patterns = _expand_braces(pattern)

    def _walk() -> list[tuple[float, str]]:
        results: list[tuple[float, str]] = []
        for root, dirs, files in os.walk(search_dir):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
            for name in files:
                full = Path(root) / name
                rel = str(full.relative_to(search_dir))
                if any(_matches(rel, name, p) for p in patterns):
                    try:
                        results.append((full.stat().st_mtime, rel))
                    except OSError:
                        continue
            if len(results) > 5000:
                break
        return results

    try:
        matches = await asyncio.to_thread(_walk)
    except Exception as e:
        return ToolResult.error(f"glob failed: {e}", title)

    if not matches:
        return ToolResult(output=f"No files matching '{pattern}' under {search_dir}", title=f"{title} (0)")

    # Most-recently-modified first: usually what the model is looking for.
    matches.sort(key=lambda t: t[0], reverse=True)
    names = [m[1] for m in matches[:MAX_GLOB_RESULTS]]
    output = "\n".join(names)
    if len(matches) > MAX_GLOB_RESULTS:
        output += f"\n\n... and {len(matches) - MAX_GLOB_RESULTS:,} more"
    return ToolResult(output=output, title=f"{title} ({len(matches)} files)")
