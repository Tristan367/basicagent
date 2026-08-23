"""The `preview` tool: run the project so the user can actually use it.

Distinct from `bash`, which is where you run a build or a test, and distinct
from `browser`, which is headless and is for checking your own work. This is
the only thing here the *user* sees.
"""

from agent_server import preview as runner
from agent_server.tools.base import ToolContext, ToolResult


async def preview(
    ctx: ToolContext,
    *,
    action: str = "start",
    command: str = "",
    url: str = "",
    wait_ms: int = 20_000,
    # Not in the schema the model sees, and deliberately: whether pointing at
    # part of the page means anything is a fact about what is being run, not a
    # decision for the assistant to make. Only the `game` tool sets it.
    pickable: bool = True,
    **_,
) -> ToolResult:
    action = (action or "start").strip().lower()

    if action == "status":
        return ToolResult(output=runner.status(ctx.session_id), title="preview status")

    if action == "stop":
        return ToolResult(output=await runner.stop(ctx.session_id), title="stopped")

    if action != "start":
        return ToolResult.error(
            f"unknown action '{action}'. Use start, stop or status.", "preview"
        )

    if not command.strip():
        return ToolResult.error(
            "`command` is what runs the project, e.g. 'npm run dev' or "
            "'python game.py'. Give it with `url` when the project serves a page.",
            "preview",
        )

    # In child mode the window is confined to this machine. Outside it, signing
    # in to a third party is a normal part of building something, and those
    # flows leave the origin by design.
    from agent_server.parental import child_mode_enabled

    try:
        output = await runner.start(
            ctx.session_id, command.strip(), url.strip(), ctx.project_dir, wait_ms,
            confine=await child_mode_enabled(), pickable=pickable,
        )
    except runner.PreviewError as e:
        return ToolResult.error(str(e), "preview")

    # Remembered on the project, not just in memory, so the Play button still
    # works tomorrow -- and after the app has been closed and reopened, when
    # the user comes back to a game they made and simply wants to play it.
    from agent_server import database as db

    await db.update_session(
        ctx.session_id, preview_command=command.strip(), preview_url=url.strip(),
        preview_pickable=1 if pickable else 0,
    )
    return ToolResult(output=output, title=_title(command, url))


def _title(command: str, url: str) -> str:
    return f"{command[:40]}{f' -> {url}' if url else ''}"[:60]
