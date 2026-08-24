"""The `draw` tool: put a real picture in the project.

The reply half already existed. An image path on its own line in a reply is
rendered as the picture, with a "Show in folder" button under it that opens the
user's own file manager -- built for screenshots, and exactly what a drawn
picture needs. So this writes a file and returns its path, and everything
downstream is already there.

Where the file goes is the only decision worth making carefully. Nobody asking
for a picture of a dragon says where to put it, so `images/` inside the project
is the answer unless the assistant says otherwise: it is where a web project
expects one, it is a folder the user can find, and it keeps generated pictures
apart from the code.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent_server import imagegen
from agent_server.tools.base import ToolContext, ToolResult

DEFAULT_FOLDER = "images"
MAX_REFERENCE_BYTES = 8 * 1024 * 1024


def _filename(text: str, extension: str) -> str:
    """A filename from what was asked for, when nobody gave one.

    `a friendly dragon breathing fire` becomes `friendly-dragon-breathing.png`
    -- long enough to tell two apart in a folder, short enough to type.
    """
    words = re.findall(r"[a-z0-9]+", text.lower())
    skip = {"a", "an", "the", "of", "with", "and", "for", "in", "on", "to",
            "picture", "image", "drawing", "please", "make", "me"}
    kept = [w for w in words if w not in skip][:4]
    return ("-".join(kept) or "picture") + extension


def _free(folder: Path, name: str) -> Path:
    """A path nothing is using, so a second dragon never eats the first."""
    candidate = folder / name
    if not candidate.exists():
        return candidate
    stem, extension = candidate.stem, candidate.suffix
    for n in range(2, 500):
        candidate = folder / f"{stem}-{n}{extension}"
        if not candidate.exists():
            return candidate
    raise imagegen.ImageError("there are already hundreds of these; "
                              "give the picture a name of its own")


async def draw(
    ctx: ToolContext,
    *,
    prompt: str = "",
    filePath: str = "",
    change: str = "",
    model: str = "",
    **_,
) -> ToolResult:
    title = "drawing a picture"
    # Checked here as well as in `imagegen`, because here it costs nothing and
    # there it is one layer inside the thing that spends money.
    if not (prompt or "").strip():
        return ToolResult.error(
            "say what the picture should be of -- `prompt` was empty", title)
    try:
        chosen = imagegen.pick(model)
    except imagegen.ImageError as e:
        return ToolResult.error(str(e), title)

    reference = b""
    reference_mime = ""
    if change:
        source = ctx.resolve(change)
        if not source.is_file():
            return ToolResult.error(
                f"there is no picture at {change} to change", title)
        if source.stat().st_size > MAX_REFERENCE_BYTES:
            return ToolResult.error(
                f"{change} is too big to send to be changed "
                f"({source.stat().st_size // (1024 * 1024)} MB)", title)
        reference = source.read_bytes()
        reference_mime = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".gif": "image/gif",
        }.get(source.suffix.lower(), "image/png")

    try:
        drawn = await imagegen.draw(prompt, model=chosen, reference=reference,
                                    reference_mime=reference_mime)
    except imagegen.ImageError as e:
        return ToolResult.error(str(e), title)
    except Exception as e:  # a shape nothing anticipated
        return ToolResult.error(
            f"the picture could not be made ({type(e).__name__}). Say so "
            f"plainly rather than trying again the same way.", title)

    # Where it lands. A path given by the assistant wins; otherwise `images/`,
    # named after what was asked for.
    if filePath:
        target = ctx.resolve(filePath)
        # The extension has to match what actually came back -- these models
        # return JPEG about as often as PNG, and a JPEG called .png is a file
        # some tools will refuse and nobody can debug by looking at it.
        if target.suffix.lower() not in (drawn.extension, ".jpeg"):
            target = target.with_suffix(drawn.extension)
    else:
        folder = Path(ctx.project_dir) / DEFAULT_FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        target = _free(folder, _filename(prompt, drawn.extension))

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(drawn.data)
    except OSError as e:
        return ToolResult.error(f"the picture could not be saved: {e}", title)

    shown = str(target)
    size = f"{len(drawn.data) // 1024} KB"
    lines = [
        f"{shown}",
        "",
        f"Drawn by {drawn.model.name} ({size}, about "
        f"${drawn.model.about_each:.2f}).",
    ]
    if drawn.said:
        lines.append(drawn.said)
    lines.append(
        "The path above is on its own line, which is what puts the picture in "
        "front of the user with a button to open its folder -- so put it on "
        "its own line in your reply too, and do not describe the picture to "
        "them as though they cannot see it.")
    if not filePath:
        lines.append(
            "It went in the project's `images` folder because nobody said "
            "where. Move it if the project wants it somewhere else.")
    return ToolResult(output="\n".join(lines), title=f"drew {target.name}")
