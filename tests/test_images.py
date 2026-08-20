"""Pictures reaching the model, which is the point of taking one.

The bar for every test here is "would this have caught the bug where the app
shipped a screenshot tool whose output nothing could look at". So they check
what actually goes on the wire, not that a function returns something.
"""

import base64
import json

import pytest
from PIL import Image

from agent_server import images as pictures
from agent_server.conversation import build_messages, to_api_message


def make(path, size=(80, 60), colour=(200, 30, 30), mode="RGB"):
    Image.new(mode, size, colour + ((255,) if mode == "RGBA" else ())).save(path)
    return str(path)


@pytest.fixture
def shot(tmp_path):
    return make(tmp_path / "shot.png")


# ── encoding ───────────────────────────────────────────────────────────────


def test_a_picture_becomes_base64_a_provider_will_accept(shot):
    media_type, payload = pictures.encode(shot)
    assert media_type == "image/png"
    assert base64.b64decode(payload)[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_huge_picture_is_shrunk_before_it_is_sent(tmp_path):
    """Cost is about area/750 tokens, so a 4K photo is ~11,000 tokens of one
    picture, and past a few megabytes the request is refused outright."""
    big = make(tmp_path / "big.png", size=(4000, 3000))
    _, payload = pictures.encode(big)
    with Image.open(tmp_path / "big.png") as original:
        assert original.size == (4000, 3000)
    data = base64.b64decode(payload)
    import io

    with Image.open(io.BytesIO(data)) as sent:
        assert max(sent.size) <= pictures.MAX_EDGE
        assert sent.size[0] * sent.size[1] <= pictures.MAX_PIXELS


def test_a_small_picture_is_not_enlarged(shot):
    import io

    _, payload = pictures.encode(shot)
    with Image.open(io.BytesIO(base64.b64decode(payload))) as sent:
        assert sent.size == (80, 60)


def test_a_photograph_goes_to_jpeg_rather_than_an_enormous_png(tmp_path):
    """PNG is right for a screenshot -- flat colour, sharp text -- and wrong for
    a photograph, where it is several times larger for nothing anyone can see."""
    import random

    random.seed(0)
    noisy = Image.new("RGB", (1400, 1000))
    noisy.putdata([(random.randrange(256), random.randrange(256), random.randrange(256))
                   for _ in range(1400 * 1000)])
    path = tmp_path / "photo.png"
    noisy.save(path)
    media_type, _ = pictures.encode(path)
    assert media_type == "image/jpeg"


def test_transparency_does_not_lose_the_picture(tmp_path):
    """JPEG has no alpha channel, so saving an RGBA image as one raises and the
    picture is silently dropped unless it is flattened first."""
    import random

    random.seed(1)
    img = Image.new("RGBA", (1400, 1000))
    img.putdata([(random.randrange(256), random.randrange(256), random.randrange(256), 255)
                 for _ in range(1400 * 1000)])
    path = tmp_path / "alpha.png"
    img.save(path)
    assert pictures.encode(path) is not None


def test_a_sideways_phone_photo_is_turned_the_right_way_up(tmp_path):
    """A phone writes the sensor's pixels plus a flag saying which way up they
    were. Ignore the flag and every portrait photo arrives on its side -- and
    the model does not say "this is sideways", it reads the picture wrong."""
    import io

    img = Image.new("RGB", (90, 30), (10, 10, 200))
    exif = img.getexif()
    exif[274] = 6  # rotate 90° clockwise
    path = tmp_path / "portrait.jpg"
    img.save(path, exif=exif)

    _, payload = pictures.encode(path)
    with Image.open(io.BytesIO(base64.b64decode(payload))) as sent:
        assert sent.size == (30, 90), "the rotation flag was ignored"


@pytest.mark.parametrize("broken", ["not-an-image.png", "gone.png"])
def test_an_unreadable_picture_does_not_take_the_turn_down(tmp_path, broken):
    path = tmp_path / broken
    if "not-an-image" in broken:
        path.write_text("<html>404</html>")
    assert pictures.encode(path) is None
    assert pictures.data_url(path) is None


# ── which models may be sent one ───────────────────────────────────────────


async def test_every_model_is_assumed_to_see_until_it_says_otherwise(db):
    """There is no way to ask. `/v1/models` returns identifiers and nothing else
    on every provider here, so the old answer was a hand-written table -- wrong
    the day a provider ships anything new, and never right for a model on
    someone's own machine."""
    from agent_server import image_support

    await image_support.forget()
    assert await image_support.accepts_images("deepseek-v4-pro")
    assert await image_support.accepts_images("something-nobody-has-heard-of")


async def test_a_refusal_is_remembered_so_it_is_paid_for_once(db):
    from agent_server import image_support

    await image_support.forget()
    await image_support.remember_refusal("deepseek-v4-pro")
    assert not await image_support.accepts_images("deepseek-v4-pro")
    assert await image_support.accepts_images("claude-opus-5"), "it marked the wrong model"

    # And it survives a restart, which is the point of writing it down.
    image_support._loaded = False
    image_support._text_only.clear()
    assert not await image_support.accepts_images("deepseek-v4-pro")

    await image_support.forget("deepseek-v4-pro")
    assert await image_support.accepts_images("deepseek-v4-pro")


@pytest.mark.parametrize("message, is_refusal", [
    ("Invalid content type: image_url is not supported by this model", True),
    ("This model does not support image input", True),
    ("modality 'image' is not accepted", True),
    ("multimodal input is unavailable", True),
    # Ways an accepted picture still fails. Marking the model text-only for
    # these would lose every later picture for a reason that was never the
    # model's.
    ("The image is too large, maximum size is 5MB", False),
    ("image dimensions exceed the limit", False),
    ("rate limit exceeded", False),
    ("invalid_api_key", False),
    ("Connection timeout", False),
])
def test_only_an_actual_refusal_teaches_it_anything(message, is_refusal):
    from agent_server import image_support

    assert image_support.looks_like_a_refusal(message) is is_refusal


# ── what goes on the wire ──────────────────────────────────────────────────


def test_an_attached_picture_is_sent_as_a_picture(shot):
    row = {"role": "user", "content": "what is wrong here?", "images": json.dumps([shot])}
    msg = to_api_message(row, sees_images=True)
    kinds = [part["type"] for part in msg["content"]]
    assert kinds == ["text", "image_url"]
    assert msg["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_a_model_that_cannot_see_is_told_so_where_the_picture_was(shot):
    """In the message, not the system prompt: whether pictures work depends on
    the model, and the model can be changed halfway through a conversation."""
    row = {"role": "user", "content": "what is wrong here?", "images": json.dumps([shot])}
    msg = to_api_message(row, sees_images=False)
    assert isinstance(msg["content"], str)
    assert "shot.png" in msg["content"] and "cannot see" in msg["content"]
    assert "Do not guess" in msg["content"]


def test_a_screenshot_a_tool_took_reaches_the_model(shot):
    """A tool result has to stay a plain string -- the OpenAI-compatible
    providers reject content parts on a `tool` message -- so the frames follow
    it as a user turn instead."""
    rows = [
        {"role": "user", "content": "check the page"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "browser", "arguments": "{}"}}]},
        {"role": "tool", "content": "1. shoot ok", "tool_call_id": "c1",
         "images": json.dumps([shot])},
    ]
    built = build_messages("sys", [], rows, sees_images=True)
    assert [m["role"] for m in built] == ["system", "user", "assistant", "tool", "user"]
    assert built[-1]["content"][-1]["type"] == "image_url"


def test_parallel_tool_results_are_not_split_apart_by_the_pictures(shot):
    """`sanitize` requires tool results to sit consecutively after the assistant
    turn that asked for them. Emitting a picture turn between two of them
    dropped the second call's result entirely."""
    rows = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "browser", "arguments": "{}"}},
            {"id": "b", "type": "function", "function": {"name": "capture", "arguments": "{}"}}]},
        {"role": "tool", "content": "one", "tool_call_id": "a", "images": json.dumps([shot])},
        {"role": "tool", "content": "two", "tool_call_id": "b", "images": json.dumps([shot])},
    ]
    built = build_messages("sys", [], rows, sees_images=True)
    assert [m["role"] for m in built] == ["system", "user", "assistant", "tool", "tool", "user"]
    assert sum(1 for p in built[-1]["content"] if p["type"] == "image_url") == 2


def test_old_pictures_stop_being_resent(shot):
    """Every picture in the history is re-sent on every turn. Left unbounded, an
    agent that checks its work visually a dozen times pays for twenty thousand
    tokens of stale screenshots for the rest of the session."""
    rows = [{"role": "user", "content": f"look {i}", "images": json.dumps([shot])}
            for i in range(12)]
    built = build_messages("sys", [], rows, sees_images=True)
    with_pictures = [m for m in built if isinstance(m.get("content"), list)]
    assert len(with_pictures) == 8
    assert "scrolled out" in built[1]["content"]


def test_nothing_is_sent_as_a_picture_when_the_model_cannot_see(shot):
    rows = [{"role": "tool", "content": "shot", "tool_call_id": "x",
             "images": json.dumps([shot])}]
    built = build_messages("sys", [], rows, sees_images=False)
    assert all(isinstance(m["content"], str) for m in built)
