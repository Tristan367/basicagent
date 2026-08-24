"""Making pictures: what it does when it works, and when it cannot.

The reply half of this already existed and was built for screenshots -- an
image path on its own line renders as the picture, with a button under it that
opens the user's own file manager. So the tool writes a file and returns a
path, and everything the user sees was already there.

Verified live against a real Google key before these were written: a picture
generated (402 KB JPEG), and the same picture handed back with "make the robot
bright green" returning the same robot, same pose, green. These pin the parts
that do not need the network.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_server import imagegen
from agent_server.tools.base import ToolContext
from agent_server.tools.draw import _filename, _free, draw


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(session_id="d", project_dir=str(tmp_path), abort=asyncio.Event())


@pytest.fixture
def drew(monkeypatch):
    """A model that always returns the same small JPEG."""
    calls = []

    async def fake(prompt, *, model, reference=b"", reference_mime=""):
        calls.append({"prompt": prompt, "model": model, "reference": reference})
        return imagegen.Drawn(data=b"\xff\xd8\xff" + b"x" * 900,
                              mime="image/jpeg", model=model)

    monkeypatch.setattr(imagegen, "draw", fake)
    monkeypatch.setattr(imagegen, "available",
                        lambda: [imagegen.IMAGE_MODELS[1]])
    return calls


# ── where the file goes and what it is called ──────────────────────────────


async def test_a_picture_lands_somewhere_the_user_can_find(ctx, drew, tmp_path):
    """Nobody asking for a dragon says where to put it."""
    result = await draw(ctx, prompt="a friendly cartoon dragon breathing fire")
    assert not result.is_error, result.output
    made = tmp_path / "images"
    files = list(made.iterdir())
    assert len(files) == 1
    assert files[0].name == "friendly-cartoon-dragon-breathing.jpg"


async def test_the_extension_follows_what_actually_came_back(ctx, drew, tmp_path):
    """These models return JPEG about as often as PNG. A JPEG called .png is a
    file some tools refuse and nobody can debug by looking at it -- and the
    live check returned exactly that: image/jpeg for a prompt with no format
    in it at all."""
    result = await draw(ctx, prompt="a dragon", filePath="art/hero.png")
    assert not result.is_error, result.output
    assert (tmp_path / "art" / "hero.jpg").is_file()
    assert not (tmp_path / "art" / "hero.png").exists()


async def test_a_second_dragon_does_not_eat_the_first(ctx, drew, tmp_path):
    for _ in range(3):
        await draw(ctx, prompt="a dragon")
    names = sorted(p.name for p in (tmp_path / "images").iterdir())
    assert names == ["dragon-2.jpg", "dragon-3.jpg", "dragon.jpg"]


def test_a_name_is_made_from_what_was_asked_for():
    assert _filename("a friendly cartoon dragon breathing fire", ".png") == \
        "friendly-cartoon-dragon-breathing.png"
    assert _filename("!!!", ".png") == "picture.png"
    assert _filename("please make me a picture of a cat", ".jpg") == "cat.jpg"


def test_a_folder_full_of_pictures_does_not_loop_forever(tmp_path):
    for n in list(range(2, 500)):
        (tmp_path / f"x-{n}.png").write_bytes(b"")
    (tmp_path / "x.png").write_bytes(b"")
    with pytest.raises(imagegen.ImageError):
        _free(tmp_path, "x.png")


# ── changing one that already exists ───────────────────────────────────────


async def test_changing_a_picture_sends_the_old_one_along(ctx, drew, tmp_path):
    """"Make the dragon green" is what people mean the second time, and it only
    works if the first dragon goes with the request."""
    original = tmp_path / "images" / "dragon.png"
    original.parent.mkdir()
    original.write_bytes(b"\x89PNG\r\n\x1a\n" + b"old")
    result = await draw(ctx, prompt="make it green", change="images/dragon.png")
    assert not result.is_error, result.output
    assert drew[0]["reference"].startswith(b"\x89PNG"), "the old picture was not sent"


async def test_changing_a_picture_that_is_not_there_says_so(ctx, drew):
    result = await draw(ctx, prompt="make it green", change="images/nope.png")
    assert result.is_error
    assert "no picture at" in result.output


# ── what it says back ──────────────────────────────────────────────────────


async def test_the_path_is_on_its_own_line(ctx, drew):
    """That is what turns it into a picture in front of the user rather than a
    sentence about a file."""
    result = await draw(ctx, prompt="a dragon")
    assert result.output.splitlines()[0].endswith(".jpg")
    assert result.output.splitlines()[1] == ""


async def test_it_says_what_the_picture_cost(ctx, drew):
    """Pictures are billed per image and are dear next to a reply. Somebody
    asking for twenty of them should be able to find out from the transcript
    what that came to."""
    result = await draw(ctx, prompt="a dragon")
    assert "$" in result.output


async def test_the_assistant_is_told_not_to_describe_it(ctx, drew):
    result = await draw(ctx, prompt="a dragon")
    assert "cannot see it" in result.output or "looking at it" in result.output


# ── when it cannot ─────────────────────────────────────────────────────────


def test_the_tool_is_there_whether_or_not_anything_can_draw_yet():
    """It was withheld when no key could draw, which sounds tidy and is not:
    the tool list is frozen when a session starts, so adding a Google key
    halfway through a conversation did nothing until a new one was started.
    "Start a new session" is not a sentence to say to somebody who does not
    know what a session is."""
    from agent_server.tools.registry import allowed_tool_names

    assert "draw" in allowed_tool_names({"kind": "project"})


def test_the_project_manager_does_not_draw():
    """It has no project to put a picture in."""
    from agent_server.tools.registry import allowed_tool_names

    assert "draw" not in allowed_tool_names({"kind": "manager"})


async def test_with_no_key_it_says_what_would_fix_it(ctx, monkeypatch):
    monkeypatch.setattr(imagegen, "available", list)
    result = await draw(ctx, prompt="a dragon")
    assert result.is_error
    assert "Google" in result.output and "Settings" in result.output


async def test_an_empty_prompt_never_reaches_the_thing_that_charges(ctx, monkeypatch):
    """It means "tell me what can draw" now, not "draw nothing" -- and either
    way no request goes out, because a blank prompt billed as a picture is the
    worst possible answer to a model that slipped."""
    monkeypatch.setattr(imagegen, "available", lambda: [imagegen.IMAGE_MODELS[1]])
    sent = []

    async def fake(prompt, **kw):
        sent.append(prompt)
        raise AssertionError("should never have been called")

    monkeypatch.setattr(imagegen, "draw", fake)
    result = await draw(ctx, prompt="   ")
    assert not result.is_error
    assert "Nano Banana" in result.output
    assert not sent, "an empty prompt was sent to be billed"


def test_a_quota_failure_is_told_apart_from_a_broken_key():
    """Pictures are billed separately from text, so this is the one failure
    that happens while ordinary replies still work -- and reading it as a bad
    key sends somebody to Settings to fix something that is not wrong."""
    assert "allowance is used up" in imagegen._why(429, "{}")
    assert "refused the key" in imagegen._why(403, "{}")


def test_a_refusal_is_not_reported_as_a_fault():
    """The model declining to draw something is an answer, not an error to
    retry -- and retrying the same words spends money to be refused again."""
    import inspect

    source = inspect.getsource(imagegen._gemini)
    assert "declining rather than a fault" in source


def test_every_model_offered_says_what_it_costs():
    for model in imagegen.IMAGE_MODELS:
        assert model.about_each > 0, model.id
        assert model.name and model.provider


# ── finding out what can draw, without spending anything ───────────────────


async def test_calling_it_with_nothing_lists_what_can_draw(ctx, drew):
    """The answer to "can you make me a picture?" and to "how much?", in one
    call that costs nothing."""
    result = await draw(ctx)
    assert not result.is_error
    assert "Nano Banana" in result.output
    assert "$" in result.output


async def test_the_list_warns_that_pictures_are_billed(ctx, drew):
    """Text is free on Google's lower tiers and pictures are not, which is
    exactly the surprise this app must never spring on somebody."""
    result = await draw(ctx)
    assert "charged" in result.output
    assert "wait for them to say yes" in result.output


async def test_asking_with_no_key_is_an_answer_not_an_error(ctx, monkeypatch):
    """"Nothing can draw yet" is information. Returned as an error it reads as
    a fault, and a model that has just been handed an error tries again."""
    monkeypatch.setattr(imagegen, "available", list)
    result = await draw(ctx)
    assert not result.is_error
    assert "Google key" in result.output and "Settings" in result.output


def test_the_description_says_to_ask_before_spending():
    from agent_server.tools.registry import TOOLS

    described = TOOLS["draw"].description
    assert "no arguments first" in described
    assert "charged" in described
    assert "wait for a yes" in described
