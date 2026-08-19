"""Turning a picture on disk into something a model can be sent.

An image reaches a model as base64 inside the request, so every picture in this
app -- one the user attached, a screenshot the agent took to check its own work
-- passes through here first.

Three things have to happen on the way, and skipping any of them is a failure
that looks like something else:

* **Downscale.** Cost is roughly area/750 tokens, so an untouched 4K phone
  photo is about 11,000 tokens of a single picture, and providers reject the
  request outright past a few megabytes. Capped at `MAX_PIXELS`, which is the
  most detail worth paying for on a screenshot of a user interface.
* **Respect the EXIF rotation.** A phone writes the sensor's pixels and a flag
  saying which way up they were. Ignore the flag and every photo taken in
  portrait arrives on its side -- and the model does not say "this is
  sideways", it just reads the picture wrong.
* **Pick the format by what the picture is.** Screenshots are flat colour and
  sharp text: PNG keeps the text legible and compresses well. Photographs are
  not, and PNG makes them enormous, so those go to JPEG.
"""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path

log = logging.getLogger(__name__)

SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}

# Anthropic's own guidance is a long edge of 1568 and no more than ~1.15M
# pixels; the OpenAI-compatible providers are looser. Sticking to the tighter
# of the two means one encoded image works everywhere.
MAX_EDGE = 1568
MAX_PIXELS = 1_150_000
# Past this a PNG is not paying for its own sharpness and the picture is
# photographic, so it goes to JPEG instead.
PNG_BUDGET = 1_200_000
JPEG_QUALITY = 85
# Refused rather than sent. Well under every provider's limit, and by this
# point the picture has already been downscaled, so hitting it means something
# is wrong rather than large.
MAX_ENCODED_BYTES = 4 * 1024 * 1024


def is_image(path: str | Path) -> bool:
    """Whether this path looks like a picture, by name alone.

    Deliberately not by reading the file: this is called on paths that may not
    exist yet, and the cost of being wrong is one failed encode that is already
    handled.
    """
    return Path(path).suffix.lower() in SUFFIXES


def encode(path: str | Path) -> tuple[str, str] | None:
    """`(media_type, base64_data)` for a picture, or None if it cannot be read.

    Returns None rather than raising for anything that goes wrong -- a
    truncated download, a `.png` that is really HTML, a file deleted between
    being attached and being sent. A missing picture must not take the whole
    turn down with it; the caller substitutes a note saying it could not be
    read, which the model can act on and an exception is not.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:  # pragma: no cover - Pillow is a hard dependency
        log.warning("Pillow is not installed, so pictures cannot be sent")
        return None

    try:
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img) or img
            img = _fit(img)
            keeps_alpha = img.mode in ("RGBA", "LA", "P")
            data, media_type = _render(img, keeps_alpha)
    except Exception as e:
        log.warning("could not read the picture %s: %s", path, e)
        return None

    if len(data) > MAX_ENCODED_BYTES:
        log.warning("picture %s is %d bytes after downscaling, skipping", path, len(data))
        return None
    return media_type, base64.b64encode(data).decode("ascii")


def data_url(path: str | Path) -> str | None:
    """The same thing as the `data:` URL the OpenAI-compatible shape wants."""
    encoded = encode(path)
    if encoded is None:
        return None
    media_type, payload = encoded
    return f"data:{media_type};base64,{payload}"


def _fit(img):
    """Shrink to within both limits, keeping the aspect ratio. Never enlarges."""
    width, height = img.size
    if not width or not height:
        raise ValueError("image has no size")

    scale = min(1.0, MAX_EDGE / max(width, height))
    if width * height * scale * scale > MAX_PIXELS:
        scale = (MAX_PIXELS / (width * height)) ** 0.5
    if scale >= 1.0:
        return img

    from PIL import Image

    return img.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.LANCZOS)


def _render(img, keeps_alpha: bool) -> tuple[bytes, str]:
    """PNG when it is cheap, JPEG when it is not.

    A screenshot is the common case here and PNG is right for it: the text in
    a user interface is exactly what the model is being asked to read, and JPEG
    smears it. Photographs take the other branch, where PNG would be several
    times larger for no legibility anyone can see.
    """
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    if buffer.tell() <= PNG_BUDGET:
        return buffer.getvalue(), "image/png"

    if keeps_alpha:
        # JPEG has no alpha, and pasting onto white is what a viewer would show
        # anyway. Without this the save raises and the picture is dropped.
        from PIL import Image

        flat = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        flat.paste(rgba, mask=rgba.split()[-1])
        img = flat
    elif img.mode != "RGB":
        img = img.convert("RGB")

    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    return buffer.getvalue(), "image/jpeg"
