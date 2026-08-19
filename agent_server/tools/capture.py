"""The `capture` tool: screenshot the screen so the agent can look at it.

This is for the agent, not the user -- the user is already looking at their own
screen and does not need a picture of it. It exists because `browser` drives a
headless Chromium and therefore sees web pages and nothing else. A game in
Godot, a desktop app, an emulator: the only way to check those is to run the
thing for real and look at the screen it drew.

The frames come back as pictures, so "does the player sprite actually appear"
is a question the agent can answer instead of assert.
"""

from agent_server.tools.base import ToolContext, ToolResult

# Returned to the model as pictures in one call. `count` may be higher -- the
# extra frames are still written to disk and named in the output, which is what
# makes "capture ten frames and tell me when the animation stutters" work
# without paying for ten pictures.
MAX_IMAGES = 4


async def capture(
    ctx: ToolContext,
    *,
    region: str = "",
    count: int = 1,
    interval_ms: int = 400,
    **_,
) -> ToolResult:
    from agent_server import capture as screen

    try:
        paths = await screen.grab(region, count=count, interval_ms=interval_ms)
    except screen.CaptureError as e:
        return ToolResult.error(str(e), "capture")

    plural = "s" if len(paths) != 1 else ""
    listing = "\n".join(f"  {i + 1}. {p}" for i, p in enumerate(paths))
    body = f"Captured {len(paths)} frame{plural}:\n{listing}"
    shown = paths[:MAX_IMAGES]
    if len(paths) > len(shown):
        body += (
            f"\n\nThe first {len(shown)} are attached as pictures. The rest are on "
            "disk at the paths above; capture again to look at a different moment."
        )
    return ToolResult(output=body, title=f"{len(paths)} frame{plural}", images=shown)
