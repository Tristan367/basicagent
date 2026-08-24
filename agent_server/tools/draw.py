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

from agent_server import imagegen, money
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


def _price(model) -> str:
    """What one picture costs, or an honest admission that nobody knows.

    A made-up price is worse than no price. The person who pays for these is
    usually not the person asking for them, and "about 4p" said confidently
    about a model charging 40 is how a child spends somebody else's money.
    """
    if model.priced:
        return f"about ${model.about_each:.2f} a picture"
    return "price not published, so nobody here knows what it costs"


async def _no_funds(model, why: str) -> ToolResult:
    """The account cannot pay for pictures, and here is exactly how to fix it.

    The only failure here whose remedy is a person on a website with a card, so
    it is the only one that gets more than a sentence. Two deliveries of the
    same answer, on purpose:

    The assistant is told the steps so it can talk somebody through them, which
    matters because this app is used by people who cannot read a screen and are
    being read to.

    And the app puts them on screen itself, because these have to be exactly
    right. An assistant retelling the Google flow will one day say
    console.cloud.google.com -- a real page, a plausible one, and not where the
    button is -- and a parent will lose an evening to it before deciding the
    whole thing is beyond them.
    """
    reachable = await imagegen.catalogue()
    steps = money.advice(model.provider, reachable)
    return ToolResult(
        output=(
            f"{why}\n\n{steps}\n\n"
            "Say this in your own words, but keep the address and the amount "
            "exactly as they are -- they are on the screen too, and the two "
            "disagreeing is worse than either alone. It is not their fault and "
            "nothing is broken; everything else about the app still works, and "
            "the pictures will work the moment the account has money on it."
        ),
        is_error=True,
        title="no funds for pictures",
        action=money.panel(model.provider, reachable),
    )


async def _listing() -> ToolResult:
    """What can draw on this computer, found rather than assumed.

    Three different answers, because three different situations need three
    different things said. Something known-good is here: choose one and get on
    with it. Only guesses are here: offer them as guesses and let the user
    decide whether to gamble. Nothing at all: say what would fix it.
    """
    found = await imagegen.catalogue()
    sure = [m for m in found if m.sure]
    guesses = [m for m in found if not m.sure]

    if not found:
        return ToolResult(
            output="Nothing on this computer can make pictures yet, and "
                   "nothing it can reach looks like it could. Google's models "
                   "can, and they use the same key as everything else -- so "
                   "this needs a Google key in Settings. Tell them that "
                   "plainly; the Project Manager walks people through getting "
                   "one.",
            title="nothing can draw yet")

    lines: list[str] = []
    if sure:
        # Tested-first rather than strictly cheapest-first, and it says so:
        # the top of this list is what gets chosen when nobody chooses, and
        # that should be a model somebody has actually driven.
        lines.append("These can make pictures. The ones this app has been "
                     "tested against come first:")
        lines += [f"- {m.name} -- {_price(m)}" + (f". {m.note}" if m.note else "")
                  for m in sure]
        lines.append("")
        lines.append(
            "Pick one yourself. Weigh the price against what the picture is "
            "for -- a sprite or a background does not need the dear one, and "
            "text inside a picture does. They asked for a picture, not a menu, "
            "so do not make them choose between models they have never heard "
            "of.")
    if guesses:
        lines.append("")
        lines.append(
            "These *might* also draw. Their names say so and nothing has "
            "confirmed it:" if sure else
            "Nothing here is known to make pictures, but these look like they "
            "might, going by their names:")
        lines += [f"- {m.name} -- {_price(m)}" + (f". {m.note}" if m.note else "")
                  for m in guesses]
        if not sure:
            lines.append("")
            lines.append(
                "Say which ones you found, that you are not certain any of "
                "them draws, and that nobody here knows what they charge. Let "
                "them choose one to try, and pass its name as `model`. If it "
                "does not work, say so and stop rather than working down the "
                "list spending their money.")

    lines.append("")
    lines.append(
        "Every picture is charged, and charged even on a free tier where "
        "ordinary replies are not. Before the first one in a conversation, say "
        "what it will cost and wait for them to say yes. After that, carry on "
        "-- asking twenty times is its own kind of rude. Call this again with "
        "a `prompt` to actually draw.")
    return ToolResult(output="\n".join(lines).strip(), title="what can draw")


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

    # Called with nothing: what can draw, and what each one costs.
    #
    # This exists because the tool list is frozen when a session starts. Hiding
    # the tool when no key could draw meant that adding a Google key halfway
    # through a conversation did nothing until a new one was started -- and
    # "start a new session" is not a sentence to say to somebody who does not
    # know what a session is. It is always here now, and asking it is how you
    # find out where you stand, mid-conversation, without spending anything.
    if not (prompt or "").strip() and not change:
        return await _listing()

    try:
        chosen = await imagegen.pick(model)
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
    except imagegen.NoFunds as e:
        return await _no_funds(chosen, str(e))
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
    cost = (f"about ${drawn.model.about_each:.2f}" if drawn.model.priced
            else "cost not published")
    lines = [
        f"{shown}",
        "",
        f"Drawn by {drawn.model.name} ({size}, {cost}).",
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
