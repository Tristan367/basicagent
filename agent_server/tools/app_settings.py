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
    changed = []
    theme = (theme or "").strip().lower()

    # Both spellings, because which one a model writes depends on which side of
    # an ocean its training data came from, and being told "unknown parameter"
    # for that is a silly way to fail.
    wanted = (colour or color or "").strip().lower()
    if wanted:
        if wanted in ("default", "normal", "reset"):
            await db.delete_setting("accent")
            changed.append("the colour back to normal")
        else:
            hex_value = COLOURS.get(wanted, wanted if wanted.startswith("#") else "")
            if len(hex_value) != 7:
                return ToolResult.error(
                    f"'{wanted}' is not a colour I can set. Try one of: "
                    + ", ".join(sorted(set(COLOURS))) + " -- or a #rrggbb value.",
                    "change the colour",
                )
            await db.set_setting("accent", hex_value)
            changed.append(f"the colour {wanted}")
    if theme:
        if theme not in ("light", "dark"):
            return ToolResult.error("theme must be 'light' or 'dark'", "change how it looks")
        await db.set_setting("theme", theme)
        changed.append(f"{theme} mode")

    size = (text_size or "").strip().lower()
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
        if want != now:
            changed.append(f"text size {round(want * 100)}%")
        else:
            changed.append(
                f"text size already as {'big' if want == ZOOM_MAX else 'small'}"
                f" as it goes ({round(want * 100)}%)"
            )

    if not changed:
        return ToolResult.error(
            "nothing to change -- pass theme, text_size, or both", "change how it looks"
        )
    return ToolResult(
        output=f"Done: {', '.join(changed)}. It is already on their screen, so say it is done "
               f"rather than telling them to do anything.",
        title=f"Set {' and '.join(changed)}",
    )


# ── voice and speech ───────────────────────────────────────────────────────

async def set_voice(
    ctx: ToolContext, *, read_aloud=None, voice: str = "", speed=None, volume=None,
    dictation=None, screen_reader=None, **_
) -> ToolResult:
    title = "change voice and speech"
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

    if not changed:
        return ToolResult.error("nothing to change -- pass at least one of them", title)
    return ToolResult(output=f"Done: {', '.join(changed)}.",
                      title=f"Set {', '.join(changed)}")


# ── the sounds it makes ────────────────────────────────────────────────────

async def set_sounds(
    ctx: ToolContext, *, chimes=None, ticking=None, volume=None, **_
) -> ToolResult:
    title = "change the sounds"
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

    if not changed:
        return ToolResult.error("nothing to change -- pass at least one of them", title)
    return ToolResult(output=f"Done: {', '.join(changed)}.",
                      title=f"Set {', '.join(changed)}")


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
