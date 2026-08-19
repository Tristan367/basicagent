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

    try:
        output = await runner.start(
            ctx.session_id, command.strip(), url.strip(), ctx.project_dir, wait_ms
        )
    except runner.PreviewError as e:
        return ToolResult.error(str(e), "preview")
    return ToolResult(output=output, title=_title(command, url))


def _title(command: str, url: str) -> str:
    return f"{command[:40]}{f' -> {url}' if url else ''}"[:60]
