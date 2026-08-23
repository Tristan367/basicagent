"""The `game` tool: build something in Godot without knowing any of this.

A tool rather than four shell commands in the prompt, for the same reason
`preview` is one. The commands would have to name this app's own Python and its
own folder, which a project's agent has no business knowing -- and every step
here has a way to get it wrong that costs an afternoon and looks, from the
outside, like the engine being broken.

What it hides:

* the scaffold, so a game never starts from an empty folder and a scene file
  written from memory (the resource ids in one are generated, and a wrong one
  fails quietly),
* the export templates, fetched a la carte the first time a target is asked
  for rather than as the 1.3 GB archive they are published in,
* the build going *outside* the project, because Godot re-imports whatever is
  inside it,
* threads being off in the web build, so it needs no special server headers
  and works anywhere it is sent,
* and the web export being served and shown, which is the whole point.
"""

import asyncio
from pathlib import Path

from agent_server import godot
from agent_server.tools.base import ToolContext, ToolResult

ACTIONS = ("new", "check", "play", "export", "run")
SHAREABLE = {"linux", "windows", "mac"}


def _project(ctx: ToolContext) -> Path:
    return Path(ctx.project_dir) / "game"


# One install at a time. Two projects both asked to make a game inside a minute
# of each other would otherwise both start unpacking the same editor into the
# same folder, and the loser corrupts the winner.
_install_lock = asyncio.Lock()


async def _install_godot(say) -> bool:
    """Put Godot on the machine, off the event loop.

    `godot.install` is ordinary blocking code -- it downloads and unpacks --
    and running it here directly would stop every other project in the app,
    the streaming replies included, for the length of a 90 MB download.
    """
    async with _install_lock:
        if godot.installed():
            return True
        say("Godot was not installed, so I am fetching it (about 90 MB).")
        try:
            return await asyncio.to_thread(godot.install, ["web"], say)
        except Exception as e:
            say(f"The download did not finish: {e}")
            return False


async def game(
    ctx: ToolContext,
    *,
    action: str = "play",
    name: str = "",
    target: str = "web",
    **_,
) -> ToolResult:
    action = (action or "play").strip().lower()
    target = (target or "web").strip().lower()
    said: list[str] = []

    def say(line: str = "") -> None:
        said.append(str(line))

    if action not in ACTIONS:
        return ToolResult.error(
            f"unknown action '{action}'. Use {', '.join(ACTIONS)}.", "game")

    # Fetched here rather than reported. "Ask the Project Manager to install a
    # 90 MB download" is four things to understand and a conversation with a
    # different assistant, in answer to "make me a game" -- and every one of
    # those is somewhere a child stops. It takes under a minute, and the first
    # thing they hear back can be about the game.
    if not godot.installed() and not await _install_godot(say):
        return ToolResult.error(
            "\n".join(said) + "\n\nGodot could not be downloaded, so there is no "
            "way to make a game yet. Tell them plainly that the download failed "
            "-- most likely this computer is offline -- and that it will work "
            "again when it is back on the internet.",
            "game",
        )

    project = _project(ctx)

    # ── start one ──────────────────────────────────────────────────────────
    if action == "new":
        if project.exists() and (project / "project.godot").exists():
            return ToolResult(
                output=f"There is already a game in {project}. Change it rather "
                       f"than starting again.",
                title="game already here")
        godot.new_project(Path(ctx.project_dir), name or "Game", say)
        say("")
        say("It already runs and moves. Change main.gd to make it theirs; "
            "debug.gd is yours and the player never sees it.")
        return ToolResult(output="\n".join(said), title="new game")

    if not (project / "project.godot").exists():
        return ToolResult.error(
            f"There is no game in {project} yet. Use action 'new' first.", "game")

    # ── check your own work ────────────────────────────────────────────────
    if action == "check":
        ok = godot.check(project, say)
        body = "\n".join(said) or "It ran and said nothing."
        if ok:
            return ToolResult(output=body, title="game checks out")
        return ToolResult.error(body, "game check failed")

    # ── a build to give away ───────────────────────────────────────────────
    if action == "export":
        if target not in godot.PRESET_NAMES:
            return ToolResult.error(
                f"unknown target '{target}'. Use web, linux, windows or mac.", "game")
        if not godot.export(project, target, say):
            return ToolResult.error("\n".join(said), f"export {target} failed")
        if target in SHAREABLE:
            say("")
            say("That is a real, standalone game. Offer to put it somewhere they "
                "can get at it -- they cannot reach the folder themselves.")
        return ToolResult(output="\n".join(said), title=f"exported {target}")

    # ── put it in front of them ────────────────────────────────────────────
    # `play` builds for the browser and shows it in their window, which is the
    # same thing that happens when they ask for a website. `run` starts it
    # natively instead, in a window of its own, for anything a browser cannot
    # do -- and then `capture` is how you look at it.
    if action == "run":
        command = godot.run_command(project)
        from agent_server.tools.preview import preview

        result = await preview(ctx, action="start", command=command, url="",
                               wait_ms=8000, pickable=False)
        if result.is_error:
            return result
        return ToolResult(
            output=result.output + "\n\nIt is running in its own window. Use "
                                   "`capture` to see it.",
            title="running the game")

    if not godot.export(project, "web", say):
        return ToolResult.error("\n".join(said), "could not build the game")

    build = Path(ctx.project_dir) / "build" / "web"
    port = 8300 + (abs(hash(ctx.session_id)) % 400)
    command = f'python3 -m http.server {port} --bind 127.0.0.1 -d "{build}"'
    from agent_server.tools.preview import preview

    result = await preview(ctx, action="start", command=command,
                           url=f"http://127.0.0.1:{port}/index.html", wait_ms=15000,
                           pickable=False)
    if result.is_error:
        return result
    say("")
    say(result.output)
    say("")
    say("It is on their screen and they can play it now. Call this again after "
        "every change. `check` is faster if you only want to know it still works.")
    return ToolResult(output="\n".join(said), title="game is playable")
