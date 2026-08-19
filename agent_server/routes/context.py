"""Context builders shared by the page and settings routes."""

from agent_server import database as db
from agent_server import parental, whisper_streaming
from agent_server import tts as tts_service
from agent_server.config import (
    CHILD_HOME_SESSION_ID,
    DEFAULT_THEME,
    HOME_SESSION_ID,
    MODELS_BY_ID,
    WHISPER_MODEL_CHOICES,
    contrast_text,
    whisper_size,
)
from agent_server.model_catalog import (
    any_credentials,
    effective_default_model,
    offerable_models,
)
from agent_server.stt import availability as stt_availability

# Plain-language blurbs for the providers a non-technical user has to choose
# between. The point is to let them pick without knowing what any of these are.
#
# `key_hint` is what that provider's keys actually start with. Every box used
# to say "sk-..." regardless, so someone pasting a Gemini key (which begins
# AIza) had good reason to think they had fetched the wrong thing.
PROVIDER_INFO = {
    "gemini": {
        "description": (
            "Google's AI, and the easiest place to start: the everyday Gemini "
            "models come with a free allowance, so you can use this app properly "
            "without entering a card anywhere. If you already have a Google "
            "account you are most of the way there. Friendly, creative, and very "
            "good with pictures."
        ),
        "key_hint": "AIza\u2026",
        "get_key_url": "https://aistudio.google.com/apikey",
        "get_key_label": "get a free key",
    },
    "deepseek": {
        "description": (
            "The cheapest paid option, and the best of these at writing code — "
            "this app was built with it. You can work for hours and still spend "
            "less than a dollar, but it does need a card on file."
        ),
        "key_hint": "sk-\u2026",
        "get_key_url": "https://platform.deepseek.com",
        "get_key_label": "get a key",
    },
    "openrouter": {
        "description": (
            "One account that gives you a huge menu of different AIs from many "
            "companies, all in one place. Handy if you want to try lots of models, "
            "but you pay a little extra for the convenience."
        ),
        "key_hint": "sk-or-\u2026",
        "get_key_url": "https://openrouter.ai",
        "get_key_label": "get a key",
    },
    "anthropic": {
        "description": (
            "Claude — some of the smartest, most careful AIs you can get. "
            "Excellent quality, but much more expensive: work that costs cents on "
            "DeepSeek could cost many dollars here. Worth it if you want the very "
            "best and don't mind the price."
        ),
        "key_hint": "sk-ant-\u2026",
        "get_key_url": "https://platform.claude.com",
        "get_key_label": "get a key",
    },
}


def mask_key(value: str) -> str:
    """A key shown the way keys are shown everywhere: ends visible, middle not.

    A field that renders completely empty when a key is saved reads as "my key
    is gone" for the second or two it takes to find the placeholder explaining
    otherwise. Showing the ends removes that doubt, and is enough to tell two
    keys apart or to spot something that is not a key at all.
    """
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= 12:
        return value[:2] + "\u2026" + value[-2:] if len(value) > 6 else "\u2026"
    return f"{value[:6]}\u2026{value[-4:]}"


def _theme(settings: dict[str, str]) -> str:
    return settings.get("theme") or DEFAULT_THEME


async def _chat_context(session: dict) -> dict:
    messages = await db.get_session_history(session["id"])
    profile = session.get("profile") or parental.profile_for_session(session["id"])
    is_home = session["id"] in (HOME_SESSION_ID, CHILD_HOME_SESSION_ID)
    settings = await db.get_all_settings()
    return {
        "session": session,
        "messages": messages,
        "compactions": await db.get_compactions(session["id"]),
        "model_display": MODELS_BY_ID.get(session.get("model", ""), {}).get("name", session.get("model", "")),
        "is_home": is_home,
        "is_settings": False,
        "has_key": any_credentials(),
        "theme": _theme(settings),
        "accent": settings.get("accent", ""),
        "accent_text": contrast_text(settings.get("accent", "")) if settings.get("accent") else "",
        "show_welcome": settings.get("welcome_seen") != "1",
        "recent_projects": (await db.list_sessions(profile=profile))[:5] if is_home else [],
        "nav_sessions": await db.list_sessions(profile=profile),
        "stt": stt_availability(),
        "stt_streaming": whisper_streaming.whisper_streaming_available(),
        "stt_enabled": settings.get("stt_enabled", "1") != "0",
        "child_mode": settings.get("child_mode", "0") == "1",
        "tts": tts_service.availability(),
        "tts_auto": settings.get("tts_auto", "0") == "1",
        "tts_voice": settings.get("tts_voice", tts_service.availability()["default_voice"]),
        "tts_speed": settings.get("tts_speed", "1.25"),
        "tts_volume": settings.get("tts_volume", "0.75"),
        # On by default: a chime after a slow job, and a distinct tone on
        # failure, are welcome whether or not you can see the screen.
        "sound_cues": settings.get("sound_cues", "1") == "1",
        # Off by default: a repeating tick is reassurance for someone working
        # by ear and a distraction for everyone else.
        "sound_ticks": settings.get("sound_ticks", "0") == "1",
        "sound_volume": settings.get("sound_volume", "0.4"),
        # Answered in the welcome flow. There is no way to detect a screen
        # reader from a page, so this is the only signal we have.
        "uses_screen_reader": settings.get("uses_screen_reader", "0") == "1",
    }


async def _settings_context() -> dict:
    settings = await db.get_all_settings()
    provider_settings = []
    from agent_server.providers import get_provider_settings_fields

    for ps in get_provider_settings_fields():
        fields = []
        for f in ps["fields"]:
            raw = settings.get(f["key"], "")
            is_pw = f.get("kind") == "password"
            fields.append(dict(f, has_value=bool(raw) and is_pw,
                               masked=mask_key(raw) if is_pw else ""))
        provider_settings.append({
            "name": ps["name"],
            "key": ps["key"],
            "fields": fields,
            "info": PROVIDER_INFO.get(ps["key"], {}),
        })

    return {
        "settings": settings,
        "is_home": False,
        "is_settings": True,
        "theme": _theme(settings),
        "accent": settings.get("accent", ""),
        "accent_text": contrast_text(settings.get("accent", "")) if settings.get("accent") else "",
        "provider_settings": provider_settings,
        "custom_endpoints": [
            # `key_is_url` surfaces a key that is a copy of the address. It is
            # refused on the way in now, but a row saved before that check
            # existed would otherwise sit there looking configured and 401 on
            # every message.
            dict(ep, masked=mask_key(ep["api_key"]),
                 key_is_url=bool(ep["api_key"].strip())
                 and ep["api_key"].strip() == ep["base_url"].strip())
            for ep in await db.list_custom_endpoints()
        ],
        "models": offerable_models(),
        "default_model": effective_default_model(settings),
        "has_key": any_credentials(),
        "sessions": await db.list_sessions(profile=await parental.current_profile()),
        "nav_sessions": await db.list_sessions(profile=await parental.current_profile()),
        "stt": stt_availability(),
        "tts": tts_service.availability(),
        "whisper_models": WHISPER_MODEL_CHOICES,
        "whisper_size": whisper_size(),
        "child_mode": settings.get("child_mode", "0") == "1",
        "override_remaining": await parental.override_remaining(),
        "override_elapsed": await parental.override_elapsed(),
    }
