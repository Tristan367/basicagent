"""Tool registry: schemas, dispatch, and per-session tool selection."""

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from agent_server.tools.app_settings import (
    set_appearance,
    set_child_mode,
    set_dictation_quality,
    set_model,
    set_sounds,
    set_voice,
    show_settings,
)
from agent_server.tools.base import ToolContext, ToolResult
from agent_server.tools.bash import run_bash
from agent_server.tools.browser import browser as browser_tool
from agent_server.tools.capture import capture
from agent_server.tools.file_ops import edit_file, read_file, write_file
from agent_server.tools.game import game
from agent_server.tools.preview import preview
from agent_server.tools.search import glob_search, grep_search
from agent_server.tools.session_manager import (
    assign_project,
    create_project,
    delete_projects,
    list_projects,
    open_project,
    rename_project,
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
        "Read a file or directory from the filesystem. Prints a `[path]` header, then "
        "lines as `N: text` with N the 1-indexed line number. The numbers are for "
        "you and for talking to the user about the file -- `edit` matches on text, "
        "not on line numbers, so copy the text itself when you change something. "
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
        "Replace exact text in a file you have read. `oldString` must appear in the "
        "file character for character -- copy it from what `read` printed, including "
        "indentation, rather than retyping it from memory.\n"
        "It must also be unique: include a line or two either side until it is, or "
        "pass replaceAll=true to change every occurrence.\n"
        "If it is not found, nothing is written and the file is untouched -- the "
        "usual cause is whitespace, a tab where the file has spaces or a trailing "
        "space you dropped. Look at the text again rather than guessing a variation.\n"
        "You can only edit lines `read` actually displayed. Re-read with an offset to "
        "reach lines you have not seen.\n"
        "Each edit returns the changed region as it now stands, so you can see where "
        "your text landed without re-reading.\n"
        "Several edits to one file in a single batch are fine: each names its own "
        "place, so they do not interfere."
    ),
    parameters={
        "type": "object",
        "properties": {
            "filePath": {"type": "string", "description": "Path to the file"},
            "oldString": {"type": "string", "description": "Exact text to replace, copied from the file"},
            "newString": {"type": "string", "description": "What to put in its place (empty to delete)"},
            "replaceAll": {"type": "boolean", "description": "Replace every occurrence"},
        },
        "required": ["filePath", "oldString", "newString"],
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
        "Hand a piece of work to a subagent that runs on its own and reports back "
        "once. It has every tool you have except `browser`, so it can read, search, "
        "edit, write and run commands -- give it work to do, not only questions to "
        "answer.\n"
        "It sees none of this conversation, so the prompt has to stand alone: what "
        "to do, which files, and what 'done' looks like. **Say exactly what it may "
        "change.** It can edit anything inside the project, so anything you do not "
        "name is something it may touch by accident -- and while it works you cannot "
        "see what it is doing. Do not give it a file you are also editing.\n"
        "It cannot write outside the project and it cannot ask you anything."
    ),
    parameters={
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "3-5 word label"},
            "prompt": {
                "type": "string",
                "description": "Complete instructions, including which files are "
                               "its to change and which are not",
            },
        },
        "required": ["description", "prompt"],
    },
    handler=run_task,
    parallel_safe=True,
))

register(Tool(
    name="game",
    description=(
        "Build a real game, in the Godot engine. Use this for anything that is a "
        "game -- a game hand-written in a browser canvas hits a ceiling within an "
        "afternoon and cannot be rescued afterwards. Only use HTML and JavaScript "
        "when what they asked for is a website that happens to be playful.\n"
        "`new` writes a project that already runs and moves: start every game with "
        "it, and never hand-write project.godot or a .tscn. Then change main.gd. "
        "`check` runs it headless, presses a key and reports what moved -- three "
        "seconds, and it catches the script errors that only appear when the engine "
        "loads the file, so do it after every change. `play` builds it for the "
        "browser and puts it on their screen, exactly like a website; call it again "
        "after every change you want them to see. `export` with linux, windows or "
        "mac makes a standalone game they can give away. `run` starts it natively in "
        "its own window instead, for what a browser cannot do -- then use `capture`."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["new", "check", "play", "export", "run"],
                "description": "Default play.",
            },
            "name": {"type": "string", "description": "For `new`: what to call it"},
            "target": {
                "type": "string",
                "enum": ["web", "linux", "windows", "mac"],
                "description": "For `export`. Default web.",
            },
        },
        "required": [],
    },
    handler=game,
))

register(Tool(
    name="preview",
    description=(
        "Run the project so the USER can see and use it -- the only thing here they "
        "actually look at. `browser` is headless and is for your own checking; this "
        "is different. Give `command` (how the project runs: 'npm run dev', "
        "'python -m http.server 8000', 'python game.py', './build/game') and `url` "
        "when it serves a page, and its window is opened for them.\n"
        "There is ONE running thing per project: starting again stops the old one "
        "and reuses the same window, so the user never accumulates stale tabs. Call "
        "this again after every change you want them to see. Not web-specific -- "
        "omit `url` for anything that draws its own window, then use `capture` to "
        "look at it yourself."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "stop", "status"],
                "description": "Default start. `start` replaces whatever was running.",
            },
            "command": {"type": "string", "description": "How to run it, from the project directory"},
            "url": {"type": "string", "description": "Address it serves, e.g. http://localhost:3000"},
            "wait_ms": {
                "type": "integer",
                "description": "How long to wait for the address to answer (default 20000)",
            },
        },
        "required": [],
    },
    handler=preview,
))

register(Tool(
    name="capture",
    description=(
        "Screenshot the screen and look at it. For anything that is not a web page "
        "-- a game window, a desktop app, an emulator -- because `browser` runs "
        "headless and can only see web pages. Run the thing for real first, then "
        "capture. This is for your own eyes: the user is already looking at their "
        "screen and does not need a picture of it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "region": {"type": "string", "description": "'x,y,w,h' to capture part of the screen"},
            "count": {"type": "integer", "description": "Number of frames, 1-24 (default 1)"},
            "interval_ms": {"type": "integer", "description": "Gap between frames (default 400)"},
        },
        "required": [],
    },
    handler=capture,
    parallel_safe=True,
))

register(Tool(
    name="browser",
    description=(
        "Drive a real browser to build, inspect and TEST a web UI. Give it a list of "
        "`steps`; each is reported with its outcome. Start with `snapshot` to get the "
        "accessibility tree. Actions: goto, click, fill, press, hover, select, check, "
        "uncheck, upload, scroll, wait, back, forward, reload, resize, snapshot, eval, "
        "network, shoot, record, expect. `expect` is an assertion and fails the call "
        "if it does not hold. `press` without `at` sends the key to whatever has "
        "focus, which is how you test Escape, Tab and keyboard navigation. `shoot` "
        "saves a screenshot to a path -- you cannot see it, but writing that path on "
        "a line of its own shows the picture to the user."
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
                        "at": {
                            "type": "string",
                            "description": (
                                "What to act on. `role=button[name=\"Save\"]`, `label=Email`, "
                                "`text=Sign in`, `testid=x`, or a CSS selector. `text=` matches "
                                "a substring, case-insensitively, unless you quote it -- "
                                "`text=\"Sign in\"` is exact. Where several match, the first is "
                                "used. `snapshot` prints the roles and names, so read them off "
                                "it rather than guessing."
                            ),
                        },
                        "url": {"type": "string", "description": "For goto, or expect url"},
                        "text": {"type": "string", "description": "For fill, or expect text"},
                        "key": {"type": "string", "description": "For press, e.g. Enter"},
                        "value": {
                            "type": ["string", "array"],
                            "items": {"type": "string"},
                            "description": "For select: one option, or several for a multi-select",
                        },
                        "js": {
                            "type": "string",
                            "description": (
                                "For eval: JavaScript, run in the page. A single expression "
                                "returns its value; several statements with a `return` are "
                                "fine too. Whatever comes back is JSON-encoded for you, so "
                                "return strings and numbers rather than DOM nodes."
                            ),
                        },
                        "visible": {
                            "type": ["string", "boolean"],
                            "description": (
                                "expect: a selector that must be visible, or true/false "
                                "about `at`."
                            ),
                        },
                        "hidden": {
                            "type": ["string", "boolean"],
                            "description": (
                                "expect: a selector that must not be visible, or true/false "
                                "about `at`. Not the same as absent -- something present but "
                                "off-screen or display:none counts as hidden."
                            ),
                        },
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
    # Everything this tool is for -- navigating, filling forms, asserting,
    # reading the console, and `snapshot`, which returns the page's
    # accessibility tree as text -- is text. Nothing here needs to see an image,
    # which is what makes it usable by every model this app can talk to.
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
            "for_child": {
                "type": "boolean",
                "description": (
                    "Make this the CHILD's project rather than your own. Use when a "
                    "parent is setting something up for their child -- a lesson, or a "
                    "project to hand over. Independent of child mode."
                ),
            },
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
    name="delete_projects",
    description=(
        "Ask the user to confirm removing one or more projects from the list. This "
        "does NOT remove anything by itself and never deletes the user's files — it "
        "puts the names on screen with a button, and the user decides. Use it for "
        "one project or for a hundred; gathering up 'all the ones about cats' is "
        "the whole point. Say what you have lined up; do not claim it is done."
    ),
    parameters={
        "type": "object",
        "properties": {
            "names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exact project names, as `list_projects` gives them",
            },
            "every_one": {
                "type": "boolean",
                "description": "Every project the user has. Ignores `names`.",
            },
        },
    },
    handler=delete_projects,
))

register(Tool(
    name="assign_project",
    description=(
        "Move a project between the child's list and the ordinary one. Whose a "
        "project is and whether child mode is switched on are separate things: a "
        "parent can set a lesson for a teenager without turning any safety locks on. "
        "The project's files stay where they are."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The project's name"},
            "to": {
                "type": "string",
                "enum": ["child", "me"],
                "description": "'child' gives it to them, 'me' takes it back",
            },
        },
        "required": ["name", "to"],
    },
    handler=assign_project,
))

# ── The settings page, as sentences ─────────────────────────────────────────
#
# Everything on that page a user can safely hand over. An API key is the one
# thing that is not here: pasted into a chat it would be written into the
# history, sent to the model, and folded into the next summary.

register(Tool(
    name="show_settings",
    description=(
        "Read back how the app is currently set up: light or dark, text size, "
        "read-aloud and its voice, dictation, sounds, child mode. Check this "
        "before any 'a bit louder/bigger/faster' request, so you are changing "
        "from where it actually is."
    ),
    parameters={"type": "object", "properties": {}},
    handler=show_settings,
))

register(Tool(
    name="set_appearance",
    description=(
        "Change how the app looks. Takes effect on their screen immediately, so "
        "say it is done — never tell them to do anything themselves."
    ),
    parameters={
        "type": "object",
        "properties": {
            "theme": {"type": "string", "enum": ["light", "dark"]},
            "text_size": {
                "type": "string",
                "description": (
                    "'bigger', 'smaller', 'reset', or a percentage like '125'. "
                    "Ranges from 70 to 160."
                ),
            },
            "colour": {
                "type": "string",
                "description": (
                    "The app's accent colour, by name -- blue, purple, pink, red, "
                    "orange, teal, grey, brown, green -- or 'default'. A name it "
                    "does not know comes back with the list."
                ),
            },
        },
    },
    handler=set_appearance,
))

register(Tool(
    name="set_voice",
    description=(
        "Change anything about voice and speech: whether replies are read aloud, "
        "which voice reads them, how fast and how loud, whether the microphone "
        "button is offered, and screen-reader mode. Only what you pass is changed."
    ),
    parameters={
        "type": "object",
        "properties": {
            "read_aloud": {"type": "boolean", "description": "Read replies aloud"},
            "voice": {
                "type": "string",
                "description": (
                    "A voice by name as the user would say it ('Emma', 'a British "
                    "man'). A wrong one comes back with the full list."
                ),
            },
            "speed": {"type": "number", "description": "0.5 to 2.0; 1.25 is normal"},
            "volume": {"type": "number", "description": "0 to 1"},
            "dictation": {"type": "boolean", "description": "Offer the Talk button"},
            "screen_reader": {
                "type": "boolean",
                "description": (
                    "The user has their own screen reader, so the app stays quiet "
                    "and lets it do the talking."
                ),
            },
        },
    },
    handler=set_voice,
))

register(Tool(
    name="set_sounds",
    description=(
        "Change the app's own sound effects: the chime when a job finishes and the "
        "tone when something fails, the ticking while it works, and how loud both "
        "are. Not the reading voice — that is `set_voice`."
    ),
    parameters={
        "type": "object",
        "properties": {
            "chimes": {"type": "boolean", "description": "Finished and failed tones"},
            "ticking": {"type": "boolean", "description": "Ticking while it works"},
            "volume": {"type": "number", "description": "0 to 1"},
        },
    },
    handler=set_sounds,
))

register(Tool(
    name="set_child_mode",
    description=(
        "Ask to switch child mode on or off. Puts a password box on screen and "
        "goes no further — the parent's password is typed there, never into the "
        "chat. Never ask for the password yourself, and never repeat one back."
    ),
    parameters={
        "type": "object",
        "properties": {"on": {"type": "boolean", "description": "True for on"}},
        "required": ["on"],
    },
    handler=set_child_mode,
))

register(Tool(
    name="set_model",
    description=(
        "Change which AI answers, by name -- 'Gemini', 'the cheap one', 'deepseek "
        "flash'. Call it with nothing to read back what is in use and what else "
        "there is, with the price of each, which is what to do when somebody asks "
        "what it costs or what their options are.\n"
        "Only models there is already a key for are offered. This does not touch "
        "keys: connecting a new provider means pasting a key into Settings, which "
        "is the one thing that never happens in a conversation."
    ),
    parameters={
        "type": "object",
        "properties": {
            "model": {
                "type": "string",
                "description": "The AI to switch to, as the user named it. Leave "
                               "it out to read back the current one and the list.",
            },
        },
    },
    handler=set_model,
))

register(Tool(
    name="set_dictation_quality",
    description=(
        "How well the Talk button listens, against how fast it answers: 'most "
        "accurate', 'faster', 'fastest'. For when somebody says dictation is slow "
        "on their computer, or that it keeps mishearing them. Call it with nothing "
        "to read back what is set. Whether the Talk button is offered at all is "
        "`set_voice`, not this."
    ),
    parameters={
        "type": "object",
        "properties": {
            "quality": {
                "type": "string",
                "description": "'most accurate', 'faster', or 'fastest'. Leave it "
                               "out to read back the current setting.",
            },
        },
    },
    handler=set_dictation_quality,
))

MANAGER_TOOLS = frozenset({
    "create_project", "list_projects", "open_project", "rename_project",
    "delete_projects", "assign_project",
    "show_settings", "set_appearance", "set_voice", "set_sounds", "set_child_mode",
    "set_model", "set_dictation_quality",
})

# Tools the home session may use in addition to the manager tools: enough to
# answer a question, look inside a project, and write a file into one.
#
# `write` and `edit` were withheld at first, which was not a boundary -- `bash`
# was already there, so the manager could write a file with `cat` and simply
# did it badly. What it could not do was the thing a parent actually wants:
# sit here and draft a lesson plan into the project, editing it in conversation
# until it says what they meant.
#
# `preview` is deliberately absent. Running a project is the project's own job,
# and the manager has no business starting one from the home screen.
MANAGER_EXTRA_TOOLS = frozenset({
    "read", "glob", "grep", "webfetch", "websearch", "bash", "write", "edit",
})


# Manager tools a child's own home session must not be offered. Withheld rather
# than refused at the handler: a tool in the list is a tool the model will try,
# and spending a round trip to be told no is worse than never seeing it.
#
# `set_child_mode` is here even though it is safe on its own -- it only ever
# raises a password box, and without the password nothing happens. But a child's
# own assistant offering to switch its safety mode off, and putting up a box
# inviting a guess at the password, is not a thing it should do. The button in
# Settings is still there for whoever knows the password.
#
# The rest of the settings tools are NOT withheld. A child asking for bigger
# text or a different voice should get it, the same as anybody.
PARENT_ONLY_TOOLS = frozenset({"assign_project", "set_child_mode"})


def allowed_tool_names(session: dict) -> list[str]:
    """The tools a session may call, based on whether it is the home manager."""
    if (session.get("kind") or "project") == "manager":
        withheld = PARENT_ONLY_TOOLS if session.get("profile") == "child" else frozenset()
        return [
            n for n in TOOLS
            if (n in MANAGER_TOOLS or n in MANAGER_EXTRA_TOOLS) and n not in withheld
        ]
    return [n for n in TOOLS if n not in MANAGER_TOOLS]


def tool_schemas(
    names: Iterable[str] | None = None,
    exclude: set[str] | None = None,
) -> list[dict]:
    """The schemas to send."""
    selected = list(names) if names is not None else list(TOOLS)
    return [
        TOOLS[n].schema()
        for n in selected
        if n in TOOLS and (exclude is None or n not in exclude)
    ]


def get_tool(name: str) -> Tool | None:
    return TOOLS.get(name)


async def _subagent_guard(name: str, args: dict, ctx: ToolContext) -> ToolResult | None:
    """A subagent works inside the project and nowhere else.

    That is the whole rule now. It used to also be forbidden every shell
    command that was not observational, which made it an agent that could
    describe work and not do it -- so the parent read the description and did
    the work again itself, off a summary, having paid for both.

    What is left is a boundary rather than a preference: a subagent runs
    autonomously and cannot prompt anybody, so writing outside the project is
    something nobody would get the chance to stop.
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
