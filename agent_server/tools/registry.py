"""Tool registry: schemas, dispatch, and per-session tool selection."""

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from agent_server.tools.base import ToolContext, ToolResult
from agent_server.tools.bash import run_bash
from agent_server.tools.browser import browser as browser_tool
from agent_server.tools.capture import capture
from agent_server.tools.file_ops import edit_file, read_file, write_file
from agent_server.tools.search import glob_search, grep_search
from agent_server.tools.session_manager import (
    create_project,
    delete_project,
    list_projects,
    open_project,
    rename_project,
    set_theme,
)
from agent_server.tools.task import run_task
from agent_server.tools.web import webfetch, websearch

Handler = Callable[..., Awaitable[ToolResult]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Handler
    # Read-only and side-effect free, so several may run at once.
    parallel_safe: bool = field(default=False)
    # Useless without something that can look at an image. Offered only when a
    # `vision` tool is actually registered, because a tool that can only ever
    # answer "I can't see that" wastes a round trip and confuses the model.
    needs_vision: bool = field(default=False)

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


log = logging.getLogger(__name__)

TOOLS: dict[str, Tool] = {}


def register(tool: Tool):
    TOOLS[tool.name] = tool


register(Tool(
    name="read",
    description=(
        "Read a file or directory from the filesystem. Prints a `[path#tag]` header, "
        "then lines as `N: text` with N the 1-indexed line number. Pass that tag and "
        "the line numbers to `edit` to change lines without retyping them. "
        "Only lines shown here may be edited; use offset/limit to reach the rest. "
        "You must read a file before you edit it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "filePath": {"type": "string", "description": "Path to the file"},
            "offset": {"type": "integer", "description": "1-indexed line to start from"},
            "limit": {"type": "integer", "description": "Maximum lines to return (default 2000)"},
        },
        "required": ["filePath"],
    },
    handler=read_file,
    parallel_safe=True,
))

register(Tool(
    name="edit",
    description=(
        "Apply changes to an existing file. Prefer the tagged-line mode: call `read` "
        "first, then pass the tag it printed plus startLine/endLine, with the "
        "replacement in newText. NEVER invent a tag — copy the one you were given. "
        "Fallback: oldString/newString for exact text replacement."
    ),
    parameters={
        "type": "object",
        "properties": {
            "filePath": {"type": "string", "description": "Path to the file"},
            "tag": {"type": "string", "description": "4-char tag from the [path#tag] header of your `read`"},
            "startLine": {"type": "integer", "description": "1-indexed first line to replace"},
            "endLine": {"type": "integer", "description": "1-indexed last line to replace"},
            "newText": {"type": "string", "description": "Replacement lines"},
            "oldString": {"type": "string", "description": "Exact text to replace (fallback)"},
            "newString": {"type": "string", "description": "Replacement text for oldString mode"},
            "replaceAll": {"type": "boolean", "description": "Replace every occurrence"},
        },
        "required": ["filePath"],
    },
    handler=edit_file,
))

register(Tool(
    name="write",
    description=(
        "Create a new file, or overwrite an existing one in full. For changes to an "
        "existing file prefer `edit`. If the file exists you must read it first."
    ),
    parameters={
        "type": "object",
        "properties": {
            "filePath": {"type": "string", "description": "Path to the file"},
            "content": {"type": "string", "description": "Full file contents"},
        },
        "required": ["filePath", "content"],
    },
    handler=write_file,
))

register(Tool(
    name="bash",
    description=(
        "Run a shell command in the project directory. Use for git, builds, tests, and "
        "package managers. Do not use it to read or search files — use read/grep/glob.\n"
        "Long-running processes (dev servers, watchers) must be backgrounded with `&`, "
        "or the call blocks until it times out. sudo does not work here and never will: "
        "there is no way to ask the user for a password."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to run"},
            "timeout": {"type": "integer", "description": "Timeout in milliseconds (default 120000)"},
            "workdir": {"type": "string", "description": "Directory to run in (defaults to project dir)"},
        },
        "required": ["command"],
    },
    handler=run_bash,
))

register(Tool(
    name="grep",
    description=(
        "Search file contents with a regular expression (ripgrep). Prefer this over "
        "reading files to look for something. Search for a distinctive fragment rather "
        "than a whole phrase — an over-specific pattern matching nothing reads like "
        "the code is absent when it is merely spelled differently."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regular expression"},
            "path": {"type": "string", "description": "Directory to search (defaults to project dir)"},
            "include": {"type": "string", "description": "Glob filter, e.g. '*.py'"},
        },
        "required": ["pattern"],
    },
    handler=grep_search,
    parallel_safe=True,
))

register(Tool(
    name="glob",
    description="Find files by name pattern, newest first. Example: 'src/**/*.ts'.",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern"},
            "path": {"type": "string", "description": "Directory to search (defaults to project dir)"},
        },
        "required": ["pattern"],
    },
    handler=glob_search,
    parallel_safe=True,
))

register(Tool(
    name="webfetch",
    description=(
        "Fetch a URL and return its content as readable text. Use it to read "
        "documentation you found with `websearch`, and whenever you are unsure "
        "whether a library's API has changed since your training data."
    ),
    parameters={
        "type": "object",
        "properties": {"url": {"type": "string", "description": "Absolute http(s) URL"}},
        "required": ["url"],
    },
    handler=webfetch,
    parallel_safe=True,
))

register(Tool(
    name="websearch",
    description=(
        "Search the web via DuckDuckGo. No API key required. Returns titles and "
        "links only — follow up with `webfetch` to actually read a result."
    ),
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Search query"}},
        "required": ["query"],
    },
    handler=websearch,
    parallel_safe=True,
))

register(Tool(
    name="task",
    description=(
        "Delegate open-ended research to a read-only subagent that works autonomously "
        "and reports back once. Give it a self-contained prompt; it sees none of this "
        "conversation."
    ),
    parameters={
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "3-5 word label"},
            "prompt": {"type": "string", "description": "Complete instructions"},
        },
        "required": ["description", "prompt"],
    },
    handler=run_task,
    parallel_safe=True,
))

register(Tool(
    name="explore",
    description=(
        "Dispatch a narrow subagent to search the codebase for specific facts — "
        "file locations, class definitions, call sites. Read-only, lighter than `task`."
    ),
    parameters={
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "3-5 word label"},
            "prompt": {"type": "string", "description": "Specific question with expected output format"},
        },
        "required": ["description", "prompt"],
    },
    handler=run_task,
    parallel_safe=True,
))

register(Tool(
    name="capture",
    description=(
        "Screenshot the desktop — for anything that is not a web page. Use `browser` "
        "for web pages. Pass `prompt` to have the capture described in the same call."
    ),
    parameters={
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "What to find out."},
            "region": {"type": "string", "description": "'x,y,w,h' to capture part of the screen"},
            "count": {"type": "integer", "description": "Number of frames, 1-24 (default 1)"},
            "interval_ms": {"type": "integer", "description": "Gap between frames (default 400)"},
        },
        "required": [],
    },
    handler=capture,
    parallel_safe=True,
    needs_vision=True,
))

register(Tool(
    name="browser",
    description=(
        "Drive a real browser to build, inspect and TEST a web UI. Give it a list of "
        "`steps`; each is reported with its outcome. Start with `snapshot` to get the "
        "accessibility tree. Actions: goto, click, fill, press, hover, select, check, "
        "uncheck, upload, scroll, wait, back, forward, reload, resize, snapshot, eval, "
        "network, shoot, record, expect. `expect` is an assertion and fails the call "
        "if it does not hold. `shoot` saves a screenshot; add `ask` to have it "
        "described. `press` without `at` sends the key to whatever has focus, which "
        "is how you test Escape, Tab and keyboard navigation."
    ),
    parameters={
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "description": "Ordered actions to run.",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "goto", "click", "fill", "press", "hover", "select",
                                "check", "uncheck", "upload", "scroll", "wait",
                                "back", "forward", "reload", "resize",
                                "snapshot", "eval", "network", "shoot", "record", "expect",
                            ],
                        },
                        "at": {"type": "string", "description": "What to act on (role=/text=/label= or CSS)"},
                        "url": {"type": "string", "description": "For goto, or expect url"},
                        "text": {"type": "string", "description": "For fill, or expect text"},
                        "key": {"type": "string", "description": "For press, e.g. Enter"},
                        "value": {
                            "type": ["string", "array"],
                            "items": {"type": "string"},
                            "description": "For select: one option, or several for a multi-select",
                        },
                        "js": {"type": "string", "description": "For eval"},
                        "ask": {"type": "string", "description": "On shoot/record: question to answer"},
                        "compare": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "On shoot/record with `ask`: image files to put alongside the "
                                "new frame, so one question spans both (a mockup, or an "
                                "earlier frame). Up to 4."
                            ),
                        },
                        "visible": {"type": "string", "description": "expect: must be visible"},
                        "hidden": {"type": "string", "description": "expect: must be gone"},
                        "count": {
                            "type": "integer",
                            "description": (
                                "expect: how many must match `at`. record: frames. "
                                "network: requests shown. click: 2 for a double-click."
                            ),
                        },
                        "filter": {"type": "string", "description": "On network: URL substring or /regex/"},
                        "console_clean": {"type": "boolean", "description": "expect: no console errors"},
                        "full_page": {"type": "boolean", "description": "shoot: whole scrollable page"},
                        "paths": {"type": "array", "items": {"type": "string"}, "description": "For upload"},
                        "path": {"type": "string", "description": "For upload: a single file"},
                        "ms": {"type": "integer", "description": "For wait"},
                        "interval_ms": {"type": "integer", "description": "For record"},
                        "to": {"type": "string", "description": "For scroll: top, bottom, or pixels"},
                        "until": {"type": "string", "description": "For wait and goto: load|domcontentloaded|networkidle"},
                        "state": {
                            "type": "string",
                            "enum": ["visible", "hidden", "attached", "detached"],
                            "description": "For wait with `at`: what to wait for. Default visible.",
                        },
                        "button": {
                            "type": "string",
                            "enum": ["left", "right", "middle"],
                            "description": "For click. Default left.",
                        },
                        "width": {"type": "integer"},
                        "height": {"type": "integer"},
                        "timeout_ms": {"type": "integer"},
                    },
                    "required": ["action"],
                },
            },
            "width": {"type": "integer", "description": "Viewport width (default 1280)"},
            "height": {"type": "integer", "description": "Viewport height (default 900)"},
            "stop_on_error": {"type": "boolean", "description": "Stop at the first failed step. Default true."},
            "reset": {"type": "boolean", "description": "Throw away cookies and history first."},
        },
        "required": ["steps"],
    },
    handler=browser_tool,
    # Deliberately NOT needs_vision. Everything that makes this tool worth
    # having -- navigating, filling forms, asserting, reading the console, and
    # `snapshot`, which returns the page's accessibility tree as text -- works
    # with no ability to see an image at all. Only the optional `ask` on a
    # screenshot needs vision, and it says so when it is unavailable. Gating the
    # whole tool on vision took a website-building assistant's ability to open
    # the website it had just built.
))

# ── Session manager tools (home session only) ───────────────────────────────

register(Tool(
    name="create_project",
    description=(
        "Start a new project. Give it a friendly name and a short description of what "
        "the user wants to build. The files live in a hidden folder by default; pass "
        "`folder` only if the user asked for a specific place on their computer."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Short, friendly project name"},
            "description": {"type": "string", "description": "What the project is for"},
            "folder": {"type": "string", "description": "Optional explicit folder path"},
        },
        "required": ["name"],
    },
    handler=create_project,
))

register(Tool(
    name="list_projects",
    description="List all of the user's projects.",
    parameters={"type": "object", "properties": {}},
    handler=list_projects,
))

register(Tool(
    name="open_project",
    description="Open an existing project so the user can work in it.",
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Project name"}},
        "required": ["name"],
    },
    handler=open_project,
))

register(Tool(
    name="rename_project",
    description="Rename an existing project.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Current project name"},
            "new_name": {"type": "string", "description": "New project name"},
        },
        "required": ["name", "new_name"],
    },
    handler=rename_project,
))

register(Tool(
    name="delete_project",
    description=(
        "Remove a project from the list. This does NOT delete the user's files — it "
        "only removes the project entry."
    ),
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Project name"}},
        "required": ["name"],
    },
    handler=delete_project,
))

register(Tool(
    name="set_theme",
    description=(
        "Switch the app between light and dark mode. Use when the user asks to "
        "change how the app looks. theme is 'light' or 'dark'."
    ),
    parameters={
        "type": "object",
        "properties": {"theme": {"type": "string", "enum": ["light", "dark"]}},
        "required": ["theme"],
    },
    handler=set_theme,
))

MANAGER_TOOLS = frozenset({
    "create_project", "list_projects", "open_project", "rename_project",
    "delete_project", "set_theme",
})

# Tools the home session may use in addition to the manager tools, so it can
# answer questions and look inside projects.
MANAGER_READ_TOOLS = frozenset({"read", "glob", "grep", "webfetch", "websearch", "bash"})


def allowed_tool_names(session: dict) -> list[str]:
    """The tools a session may call, based on whether it is the home manager."""
    if (session.get("kind") or "project") == "manager":
        return [n for n in TOOLS if n in MANAGER_TOOLS or n in MANAGER_READ_TOOLS]
    return [n for n in TOOLS if n not in MANAGER_TOOLS]


def vision_available() -> bool:
    """Whether anything registered can actually look at an image.

    This app ships no vision tool: describing a picture needs a GPU or a paid
    account it cannot assume. One can be registered by an embedding
    application, and everything gated on this appears the moment it is.
    """
    return "vision" in TOOLS


def tool_schemas(
    names: Iterable[str] | None = None,
    include_vision: bool | None = None,
    exclude: set[str] | None = None,
) -> list[dict]:
    """The schemas to send. `include_vision` defaults to whether vision exists.

    This used to be passed `not provider.supports_vision()`, which was backwards
    twice over: it asked whether the *model* was multimodal (irrelevant -- this
    app never puts an image in a request) and then inverted the answer, so a
    multimodal provider was the one that lost the tools.
    """
    if include_vision is None:
        include_vision = vision_available()
    selected = list(names) if names is not None else list(TOOLS)
    return [
        TOOLS[n].schema()
        for n in selected
        if n in TOOLS
        and (include_vision or not TOOLS[n].needs_vision)
        and (exclude is None or n not in exclude)
    ]


def get_tool(name: str) -> Tool | None:
    return TOOLS.get(name)


async def _subagent_guard(name: str, args: dict, ctx: ToolContext) -> ToolResult | None:
    """Subagents are read-only and must not write outside the project.

    A subagent runs autonomously inside `task`/`explore` and cannot prompt the
    user, so it gets a hard boundary rather than the (absent) permission gate.
    """
    if ctx.subagent_tier <= 0:
        return None
    if name in ("edit", "write"):
        raw = args.get("filePath") or ""
        if not raw:
            return None
        path = ctx.resolve(raw)
        from pathlib import Path

        from agent_server.permissions import is_denied

        try:
            within = path.resolve().is_relative_to(Path(ctx.project_dir).resolve())
        except (OSError, ValueError):
            within = False
        if is_denied(path) or not within:
            return ToolResult.error(
                f"{name} to {path} is outside the project and a subagent cannot write there",
                name,
            )
        return None
    if name == "bash":
        from agent_server.tools.bash import is_read_only

        if not is_read_only(args.get("command", "")):
            return ToolResult.error(
                "subagents may only run read-only commands; ask the parent agent to run this",
                name,
            )
    return None


async def execute_tool(
    name: str,
    args: dict[str, Any],
    ctx: ToolContext,
    allowed: Iterable[str] | None = None,
) -> ToolResult:
    tool = TOOLS.get(name)
    if tool is None:
        known = ", ".join(sorted(TOOLS))
        return ToolResult.error(f"unknown tool '{name}'. Available tools: {known}", name)
    if allowed is not None and name not in allowed:
        return ToolResult.error(f"tool '{name}' is not available in this context", name)

    blocked = await _subagent_guard(name, args, ctx)
    if blocked is not None:
        return blocked

    signature = inspect.signature(tool.handler)
    accepts_kwargs = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()
    )
    if not accepts_kwargs:
        args = {k: v for k, v in args.items() if k in signature.parameters}

    try:
        result = await tool.handler(ctx, **args)
    except TypeError as e:
        return ToolResult.error(f"invalid arguments for '{name}': {e}", name)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        return ToolResult.error(f"{name} failed: {type(e).__name__}: {e}", name)

    if not isinstance(result, ToolResult):
        result = ToolResult(output=str(result))
    if not result.title:
        result.title = name
    if result.is_error:
        log.warning("tool %s failed: %s", name, result.output[:200])
    return result


__all__ = [
    "MANAGER_TOOLS",
    "TOOLS",
    "Tool",
    "ToolContext",
    "ToolResult",
    "allowed_tool_names",
    "execute_tool",
    "get_tool",
    "tool_schemas",
]
