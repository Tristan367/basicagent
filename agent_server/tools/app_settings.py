"""Letting the home assistant work the settings page on the user's behalf.

The point of this app is that somebody can use it entirely by talking. A
settings page full of checkboxes and sliders is exactly the thing that is out of
reach for the person it is for -- someone who cannot see it, cannot use a mouse,
or would simply never find it. So everything on that page that is safe to hand
over is a tool here, and "make the writing bigger", "read your replies to me",
"turn the ticking off" all work as sentences.

Two things are deliberately NOT here.

API keys. A key pasted into a chat message is a key written into the message
history, sent to whichever model is answering, and included in the next
summary. The assistant can walk somebody through fetching one -- that is what
the walkthrough is for -- but the key itself goes in the box on the settings
page and nowhere else.

Child mode's password. `set_child_mode` starts the change; it cannot finish it.
The password is typed into a dialog by the person at the keyboard, because a
password the assistant has seen is not a password that keeps that assistant's
own conversation locked.
"""

from agent_server import database as db
from agent_server import parental
from agent_server import tts as tts_service
from agent_server.tools.base import ToolContext, ToolResult

# What `zoom` may be set to. The client clamps to the same range, so a value
# outside it would be silently ignored and the assistant would report a change
# that never happened.
ZOOM_MIN, ZOOM_MAX = 0.7, 1.6
ZOOM_STEP = 0.1


def _bounded(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _onoff(value) -> bool | None:
    """A tri-state read of something the model may not have sent at all.

    None means "not mentioned", which has to stay distinct from False -- a tool
    that changes every setting it has a parameter for would turn read-aloud off
    every time somebody asked for a different voice.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return None


async def _flag(key: str, default: str = "0") -> bool:
    return await db.get_setting(key, default) == "1"


# ── reading them back ──────────────────────────────────────────────────────

async def show_settings(ctx: ToolContext, **_) -> ToolResult:
    """Everything the assistant can change, in the words it would use to say it.

    Worth its own tool rather than leaving the model to guess: without it, "make
    it a bit louder" has nothing to be a bit louder *than*, and the model either
    invents a starting point or asks a question it should not have to ask.
    """
    zoom = float(await db.get_setting("zoom", "1") or 1)
    voice = await db.get_setting("tts_voice", "") or tts_service.availability()["default_voice"]
    lines = [
        f"Look: {await db.get_setting('theme', 'dark')} mode, "
        f"text size {round(zoom * 100)}%, "
        f"colour {_colour_name(await db.get_setting('accent', ''))}.",
        f"Read-aloud: {'on' if await _flag('tts_auto') else 'off'}"
        f" (voice {tts_service.voice_label(voice)}, "
        f"speed {await db.get_setting('tts_speed', '1.25')}x, "
        f"volume {round(float(await db.get_setting('tts_volume', '0.75')) * 100)}%).",
        f"Dictation: {'on' if await _flag('stt_enabled', '1') else 'off'}.",
        f"Screen reader mode: {'on' if await _flag('uses_screen_reader') else 'off'}.",
        f"Sounds: chimes {'on' if await _flag('sound_cues', '1') else 'off'}, "
        f"ticking {'on' if await _flag('sound_ticks') else 'off'}, "
        f"volume {round(float(await db.get_setting('sound_volume', '0.4')) * 100)}%.",
        f"Child mode: {'on' if await parental.child_mode_enabled() else 'off'}.",
    ]
    return ToolResult(output="\n".join(lines), title="checked the settings")


# ── saying what actually changed ───────────────────────────────────────────
#
# Every one of these tools used to answer "Done: dark mode." whether the app
# had just gone dark or had been dark for a week. So "make it dark" got the
# same reply either way, and the assistant had no way to notice that the thing
# the user was unhappy about was already switched on -- which is the moment it
# should be saying "it already is, so the problem is something else" instead of
# cheerfully reporting success and leaving them where they started.
#
# Every setting a tool touches is now read before and after, and both are in
# the answer.

VOLUME_KEYS = {"tts_volume", "sound_volume"}
FLAG_LABELS = {
    "tts_auto": "read-aloud", "stt_enabled": "dictation",
    "uses_screen_reader": "screen reader mode",
    "sound_cues": "chimes", "sound_ticks": "ticking",
}
FLAG_DEFAULTS = {"stt_enabled": "1", "sound_cues": "1"}


async def _read(keys) -> dict:
    """What the settings are right now, for the keys a tool is about to touch."""
    return {key: await db.get_setting(key, FLAG_DEFAULTS.get(key, "")) for key in keys}


def _human(key: str, value: str) -> str:
    """One setting, said the way somebody would say it out loud."""
    value = value or ""
    if key in FLAG_LABELS:
        return "on" if value == "1" else "off"
    if key == "theme":
        return f"{value or 'dark'} mode"
    if key == "accent":
        return _colour_name(value)
    if key == "zoom":
        return f"{round(float(value or 1) * 100)}%"
    if key in VOLUME_KEYS:
        return f"{round(float(value or 0) * 100)}%"
    if key == "tts_speed":
        return f"{value or '1.25'}x"
    if key == "tts_voice":
        return tts_service.voice_label(value) if value else "the usual voice"
    if key == "whisper_size":
        from agent_server import config

        return next((c["name"] for c in config.WHISPER_MODEL_CHOICES
                     if c["id"] == value), value or "the usual one")
    if key == "default_model":
        from agent_server.model_catalog import offerable_models

        return next((m["name"] for m in offerable_models() if m["id"] == value),
                    value or "whichever is cheapest")
    return value


def _label(key: str) -> str:
    return FLAG_LABELS.get(key) or {
        "theme": "theme", "accent": "colour", "zoom": "text size",
        "tts_voice": "voice", "tts_speed": "speaking speed",
        "tts_volume": "reading volume", "sound_volume": "sound volume",
        "default_model": "the AI", "whisper_size": "dictation quality",
    }.get(key, key)


def _prose(before: dict, after: dict) -> tuple[list[str], list[str]]:
    """What moved, and what was already where it was asked to be."""
    moved, already = [], []
    for key, was in before.items():
        now = after.get(key, was)
        # Compared as the words, not as the stored values. A setting nobody has
        # ever touched is stored as "" and reads back as its default, so a raw
        # comparison reported "theme: dark mode -> dark mode" as a change the
        # first time anybody asked for the theme it was already on -- which is
        # the exact case this whole thing exists to notice.
        before_said, now_said = _human(key, was), _human(key, now)
        if before_said == now_said:
            already.append(f"{_label(key)} was already {now_said}")
        else:
            moved.append(f"{_label(key)}: {before_said} -> {now_said}")
    return moved, already


async def _done(title: str, before: dict, extra: str = "") -> ToolResult:
    """The answer every set_* tool gives: what it was, what it is, what to say."""
    after = await _read(before)
    moved, already = _prose(before, after)
    lines = []
    if moved:
        lines.append("Changed: " + "; ".join(moved) + ".")
    if already:
        # The useful half. Somebody asking for a thing that is already set is
        # telling you the setting is not their problem.
        lines.append(
            "Already as asked: " + "; ".join(already) + ". "
            "Say so rather than reporting it as a change -- if they asked for "
            "this, what they actually want is something else.")
    if extra:
        lines.append(extra)
    if not moved and not already:
        lines.append("Nothing changed.")
    if moved:
        lines.append("It is already on their screen, so say it is done rather "
                     "than telling them to do anything.")
    return ToolResult(output=" ".join(lines),
                      title=title if moved else f"{title} (no change)")


# ── how it looks ───────────────────────────────────────────────────────────

# The colours somebody asks for by name. Not a full list of anything -- these
# are the words people actually use for "make it blue", picked to stay legible
# against both themes, since the accent is used for text as well as for buttons.
COLOURS = {
    "green": "#557030", "blue": "#2f6fb0", "purple": "#7a5aa8", "violet": "#7a5aa8",
    "pink": "#b4548a", "red": "#b04a44", "orange": "#b86a24", "yellow": "#9a8420",
    "gold": "#9a8420", "teal": "#1f7a72", "turquoise": "#1f7a72", "grey": "#6b7280",
    "gray": "#6b7280", "brown": "#7d5a3c",
}


def _colour_name(accent: str) -> str:
    """The accent said back as the word somebody would use for it."""
    if not accent:
        return "the usual green"
    for name, value in COLOURS.items():
        if value == accent:
            return name
    return accent


async def set_appearance(
    ctx: ToolContext, *, theme: str = "", text_size: str = "", colour: str = "",
    color: str = "", **_
) -> ToolResult:
    theme = (theme or "").strip().lower()
    wanted_colour = (colour or color or "").strip().lower()
    size = (text_size or "").strip().lower()
    touched = ([("accent")] if wanted_colour else []) + \
              (["theme"] if theme else []) + (["zoom"] if size else [])
    if not touched:
        return ToolResult.error(
            "nothing to change -- pass theme, colour, text_size, or any mix",
            "change how it looks")
    before = await _read(touched)
    changed = []

    # Both spellings, because which one a model writes depends on which side of
    # an ocean its training data came from, and being told "unknown parameter"
    # for that is a silly way to fail.
    wanted = wanted_colour
    if wanted:
        if wanted in ("default", "normal", "reset"):
            await db.delete_setting("accent")
        else:
            hex_value = COLOURS.get(wanted, wanted if wanted.startswith("#") else "")
            if len(hex_value) != 7:
                return ToolResult.error(
                    f"'{wanted}' is not a colour I can set. Try one of: "
                    + ", ".join(sorted(set(COLOURS))) + " -- or a #rrggbb value.",
                    "change the colour",
                )
            await db.set_setting("accent", hex_value)
    if theme:
        if theme not in ("light", "dark"):
            return ToolResult.error("theme must be 'light' or 'dark'", "change how it looks")
        await db.set_setting("theme", theme)

    if size:
        now = float(await db.get_setting("zoom", "1") or 1)
        if size in ("bigger", "larger", "up", "increase"):
            want = now + ZOOM_STEP
        elif size in ("smaller", "down", "decrease"):
            want = now - ZOOM_STEP
        elif size in ("reset", "normal", "default"):
            want = 1.0
        else:
            try:
                want = float(size.rstrip("%x "))
            except ValueError:
                return ToolResult.error(
                    "text_size must be 'bigger', 'smaller', 'reset', or a "
                    "percentage like '125'",
                    "change the text size",
                )
            # "125" means 125%, "1.25" means the same thing. Both are things a
            # model reasonably writes, and one of them is off by a hundredfold.
            if want > ZOOM_MAX:
                want = want / 100
        want = round(_bounded(want, ZOOM_MIN, ZOOM_MAX), 2)
        await db.set_setting("zoom", str(want))
        if want == now and want in (ZOOM_MIN, ZOOM_MAX):
            edge = "big" if want == ZOOM_MAX else "small"
            changed.append(f"the text is as {edge} as it goes")

    return await _done(
        "changed how it looks", before,
        extra=("Note: " + "; ".join(changed) + "." ) if changed else "")


# ── voice and speech ───────────────────────────────────────────────────────

async def set_voice(
    ctx: ToolContext, *, read_aloud=None, voice: str = "", speed=None, volume=None,
    dictation=None, screen_reader=None, **_
) -> ToolResult:
    title = "changed voice and speech"
    touched = [key for key, given in (
        ("tts_auto", read_aloud), ("tts_voice", voice.strip() if voice else ""),
        ("tts_speed", speed), ("tts_volume", volume),
        ("stt_enabled", dictation), ("uses_screen_reader", screen_reader),
    ) if given not in (None, "")]
    if not touched:
        return ToolResult.error("nothing to change -- pass at least one of them", title)
    before = await _read(touched)
    changed = []

    if (want := _onoff(read_aloud)) is not None:
        await db.set_setting("tts_auto", "1" if want else "0")
        changed.append("read-aloud " + ("on" if want else "off"))

    if (name := (voice or "").strip()):
        known = {v: label for v, label in tts_service.voice_choices()}
        # Named the way it is spoken about ("Emma", "a British man"), not by its
        # id -- the id is the thing the user has never seen.
        match = next(
            (v for v, label in known.items()
             if name.lower() in (v.lower(), label.lower())),
            None,
        ) or next(
            (v for v, label in known.items() if name.lower() in label.lower()), None
        )
        if match is None:
            return ToolResult.error(
                f"no voice called '{name}'. The choices are: "
                + "; ".join(f"{label}" for _v, label in tts_service.voice_choices()),
                title,
            )
        await db.set_setting("tts_voice", match)
        changed.append(f"voice {known[match]}")

    for key, value, low, high, say in (
        ("tts_speed", speed, 0.5, 2.0, "speaking speed {v}x"),
        ("tts_volume", volume, 0.0, 1.0, "reading volume {p}%"),
    ):
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            return ToolResult.error(f"{key} must be a number", title)
        # A volume asked for as "80" rather than "0.8".
        if high == 1.0 and number > 1:
            number = number / 100
        number = round(_bounded(number, low, high), 2)
        await db.set_setting(key, str(number))
        changed.append(say.format(v=number, p=round(number * 100)))

    if (want := _onoff(dictation)) is not None:
        await db.set_setting("stt_enabled", "1" if want else "0")
        changed.append("dictation " + ("on" if want else "off"))

    if (want := _onoff(screen_reader)) is not None:
        await db.set_setting("uses_screen_reader", "1" if want else "0")
        changed.append("screen reader mode " + ("on" if want else "off"))

    return await _done(title, before)


# ── the sounds it makes ────────────────────────────────────────────────────

async def set_sounds(
    ctx: ToolContext, *, chimes=None, ticking=None, volume=None, **_
) -> ToolResult:
    title = "changed the sounds"
    touched = [key for key, given in (
        ("sound_cues", chimes), ("sound_ticks", ticking), ("sound_volume", volume),
    ) if given is not None]
    if not touched:
        return ToolResult.error("nothing to change -- pass at least one of them", title)
    before = await _read(touched)
    changed = []
    for key, value, say in (
        ("sound_cues", chimes, "chimes"), ("sound_ticks", ticking, "ticking"),
    ):
        if (want := _onoff(value)) is None:
            continue
        await db.set_setting(key, "1" if want else "0")
        changed.append(f"{say} " + ("on" if want else "off"))

    if volume is not None:
        try:
            number = float(volume)
        except (TypeError, ValueError):
            return ToolResult.error("volume must be a number", title)
        if number > 1:
            number = number / 100
        number = round(_bounded(number, 0.0, 1.0), 2)
        await db.set_setting("sound_volume", str(number))
        changed.append(f"sound volume {round(number * 100)}%")

    return await _done(title, before)


# ── child mode ─────────────────────────────────────────────────────────────

async def set_child_mode(ctx: ToolContext, *, on: bool = True, **_) -> ToolResult:
    """Start switching child mode, and hand the rest to the person at the keys.

    Both directions need the parent's password -- one to set it, the other to
    prove they are the parent -- and neither may be typed into the chat. So this
    tool's whole job is to put the right dialog on screen; the endpoints behind
    it are the same ones the settings page uses.
    """
    want = bool(_onoff(on) if not isinstance(on, bool) else on)
    already = await parental.child_mode_enabled()
    if want == already:
        return ToolResult(
            output=f"Child mode is already {'on' if want else 'off'}. Nothing to do.",
            title=f"Child mode already {'on' if want else 'off'}",
        )
    if want and not await parental.parent_password_set():
        note = ("A box is now on screen asking them to choose a parent password. "
                "Child mode switches on once they have set it. Tell them what the "
                "box is for -- it is what turns child mode back OFF later, so it "
                "must be something they will remember.")
    elif want:
        note = ("A box is now on screen asking for the parent password they set "
                "before. Child mode switches on once it is right.")
    else:
        note = ("A box is now on screen asking for the parent password. Child mode "
                "switches off once it is right. If they have forgotten it, the box "
                "has a way through that waits a day.")
    return ToolResult(
        output=note + " Do not ask for the password yourself and never repeat one "
                      "back: it is typed into that box and nowhere else.",
        title=f"Asked to turn child mode {'on' if want else 'off'}",
        action={"kind": "child_mode", "on": want},
    )


# ── which AI, and how well it listens ──────────────────────────────────────
#
# The last two things on the settings page the assistant could not reach. Both
# matter more than their size suggests: the model is what everything costs, and
# the dictation model is the difference between talking to this app and giving
# up on it. Neither is an API key, which is the one thing that stays out of the
# chat -- a key typed into a conversation is a key stored in a conversation.


async def set_model(ctx: ToolContext, *, model: str = "", **_) -> ToolResult:
    """Choose which AI answers, by name, from the ones there is a key for."""
    from agent_server.model_catalog import effective_default_model, offerable_models
    from agent_server.system_prompt import ensure_home_session

    available = offerable_models()
    if not available:
        return ToolResult.error(
            "There is no AI connected yet, so there is nothing to choose between. "
            "Walk them through connecting one in Settings first.",
            "no AI connected")

    def described(entry: dict) -> str:
        return (f"{entry['name']} ({entry['provider_label']}, "
                f"{entry.get('price_label', '')})".replace(", )", ")"))

    wanted = (model or "").strip()
    if not wanted:
        current = effective_default_model(await db.get_all_settings())
        now = next((m for m in available if m["id"] == current), None)
        lines = [f"Using {described(now) if now else current}. Also available:"]
        lines += [f"- {described(m)}" for m in available if m["id"] != current]
        return ToolResult(output="\n".join(lines), title="which AI is in use")

    # By id first, then by name, then by any distinctive word in it -- because
    # what arrives here is whatever the user said out loud, which is "the cheap
    # one", "Gemini", or "deepseek flash", and never an id.
    lowered = wanted.lower()
    match = next((m for m in available if m["id"].lower() == lowered), None)
    if match is None:
        match = next((m for m in available if m["name"].lower() == lowered), None)
    if match is None:
        hits = [m for m in available
                if lowered in m["name"].lower() or lowered in m["id"].lower()]
        if len(hits) == 1:
            match = hits[0]
        elif len(hits) > 1:
            return ToolResult.error(
                f"'{wanted}' matches more than one: "
                + ", ".join(described(m) for m in hits)
                + ". Ask them which.",
                "which one?")
    if match is None:
        return ToolResult.error(
            f"There is no '{wanted}' to switch to. What there is: "
            + ", ".join(described(m) for m in available),
            "no such AI")

    before = await _read(["default_model"])
    # `default_model` is empty until somebody chooses one, and reads back as
    # whatever is cheapest -- so store the model that is actually in use, or
    # "already on it" would never fire on the first change.
    if not before["default_model"]:
        before["default_model"] = effective_default_model(await db.get_all_settings())
    await db.set_setting("default_model", match["id"])
    # The home session is pinned to a model of its own, so without this the
    # change would apply to new projects and not to the conversation the user
    # is having right now -- which reads as it not having worked.
    await ensure_home_session()
    return await _done(
        f"switched to {match['name']}", before,
        extra=f"That is {described(match)}. New projects start on it too; "
              f"projects already open keep the model they were using, so say so "
              f"if they wanted those changed as well.")


async def set_dictation_quality(ctx: ToolContext, *, quality: str = "", **_) -> ToolResult:
    """How well the Talk button listens, traded against how fast it answers."""
    import asyncio

    from agent_server import config
    from agent_server import stt as stt_service

    choices = config.WHISPER_MODEL_CHOICES
    catalogue = ", ".join(f"{c['name']} ({c['note'].split('.')[0].lower()})"
                          for c in choices)

    wanted = (quality or "").strip().lower()
    if not wanted:
        now = next((c for c in choices if c["id"] == config.whisper_size()), None)
        return ToolResult(
            output=f"Dictation is set to {now['name'] if now else config.whisper_size()}. "
                   f"The choices are: {catalogue}.",
            title="dictation quality")

    match = next((c for c in choices
                  if wanted in (c["id"].lower(), c["name"].lower())), None)
    if match is None:
        for word, target in (("accurate", "small.en"), ("best", "small.en"),
                             ("slow", "base.en"), ("faster", "base.en"),
                             ("fast", "base.en"), ("quick", "base.en"),
                             ("fastest", "tiny.en"), ("old", "tiny.en")):
            if word in wanted:
                match = next(c for c in choices if c["id"] == target)
                break
    if match is None:
        return ToolResult.error(
            f"'{quality}' is not one of them. The choices are: {catalogue}.",
            "no such setting")

    before = {"whisper_size": config.whisper_size()}
    if not config.set_whisper_size(match["id"]):
        return await _done("dictation quality", before)
    await db.set_setting("whisper_size", match["id"])
    # Drop the loaded model so the next sentence uses the new one. It reloads
    # in the background rather than making this reply wait on a download.
    await stt_service.reload_model()
    _background.add(task := asyncio.create_task(stt_service.warmup()))
    task.add_done_callback(_background.discard)
    return await _done(
        f"dictation: {match['name']}", before,
        extra=f"{match['note']} ({match['size']}.) The first sentence after this "
              f"may take a moment while it loads.")


# Background reloads, held so they are not garbage-collected mid-flight.
_background: set = set()
