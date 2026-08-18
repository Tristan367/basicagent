"""Image handling for uploads and captures.

Nothing here talks to a vision model. Describing an image is a custom tool --
a shell script the user supplies, because it needs a GPU or a paid account
this app cannot assume. What is left is decoding, downscaling and describing
the file itself, which uploads and `capture` both need regardless.

`normalize_image` re-encodes everything as PNG before it is sent anywhere:
browsers routinely hand over a WebP named ``.jpg``, and some vision backends
reject WebP outright, so trusting the filename would fail.
"""

import asyncio
import io
import logging
from pathlib import Path

from agent_server.config import (
    VISION_MAX_PIXELS,
)

log = logging.getLogger(__name__)



class ImageError(RuntimeError):
    pass


# ── Image normalisation ─────────────────────────────────────────────────────

def normalize_image(data: bytes) -> bytes:
    """Decode any Pillow-supported image and re-encode as PNG.

    Handles the WebP problem, strips alpha (which some vision models mishandle),
    applies EXIF rotation so phone photos are upright, and caps the pixel count
    so a 48MP photo does not blow up the request.
    """
    from PIL import Image, ImageOps

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as e:
        raise ImageError(f"could not decode image: {e}") from e

    image = ImageOps.exif_transpose(image)
    if image.mode not in ("RGB", "L"):
        if image.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", image.size, (255, 255, 255))
            converted = image.convert("RGBA")
            background.paste(converted, mask=converted.split()[-1])
            image = background
        else:
            image = image.convert("RGB")

    pixels = image.width * image.height
    if pixels > VISION_MAX_PIXELS:
        scale = (VISION_MAX_PIXELS / pixels) ** 0.5
        image = image.resize(
            (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
            Image.LANCZOS,
        )

    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def describe_image_file(path: str | Path) -> str:
    """Human-readable dimensions, for tool output."""
    try:
        from PIL import Image

        with Image.open(Path(path).expanduser()) as im:
            return f"{im.width}x{im.height} {im.format}"
    except Exception:
        log.debug("reading image dimensions failed", exc_info=True)
        return "image"


async def normalize_in_thread(data: bytes) -> bytes:
    """Decode and re-encode off the event loop. A 48MP upload takes a while."""
    return await asyncio.to_thread(normalize_image, data)
