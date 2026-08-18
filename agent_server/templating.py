"""The Jinja environment and the filters registered on it."""

from datetime import UTC, datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates

from agent_server.config import APP_VERSION

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = BASE_DIR / "web_ui" / "templates"
STATIC_DIR = BASE_DIR / "web_ui" / "static"

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.globals["version"] = APP_VERSION


def _parse(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone()


def humantime(value: str) -> str:
    dt = _parse(value)
    if dt is None:
        return value
    delta = datetime.now(UTC) - dt.astimezone(UTC)
    secs = delta.total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    if secs < 604800:
        return f"{int(secs // 86400)}d ago"
    return dt.strftime("%b %-d, %Y")


def clocktime(value: str) -> str:
    dt = _parse(value)
    if dt is None:
        return value
    return dt.strftime("%-I:%M %p").lower().replace("am", "AM").replace("pm", "PM")


def tildepath(value: str) -> str:
    if not value:
        return value
    home = str(Path.home())
    if value == home:
        return "~"
    if value.startswith(home + "/"):
        return "~" + value[len(home):]
    return value


templates.env.filters["humantime"] = humantime
templates.env.filters["clocktime"] = clocktime
templates.env.filters["tildepath"] = tildepath
