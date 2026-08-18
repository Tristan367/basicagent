"""One module that configures process-wide logging.

Import and call `configure()` early in startup. Everything else in the
codebase gets a module-level `log = logging.getLogger(__name__)` and does
not touch the root logger directly.
"""

import logging
import os
from logging.handlers import RotatingFileHandler

from agent_server.config import DATA_DIR

_configured = False


def configure(level: str | None = None) -> None:
    """Set up the root logger once. Idempotent — calling again is a no-op."""
    global _configured
    if _configured:
        return
    _configured = True

    raw = level or os.getenv("BASICAGENT_LOG_LEVEL", "INFO")
    numeric = _resolve(raw)

    root = logging.getLogger()
    root.setLevel(numeric)

    # stderr — human-readable, no colour, short timestamp
    stderr = logging.StreamHandler()
    stderr.setLevel(numeric)
    stderr.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(stderr)

    # rotating file under the data directory
    file_handler = RotatingFileHandler(
        DATA_DIR / "assistant.log",
        maxBytes=5_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(numeric)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(file_handler)

    # Quieten noisy third-party loggers
    for name in ("httpx", "httpcore", "urllib3", "asyncio",
                 "playwright", "watchfiles", "multipart", "PIL"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _resolve(raw: str) -> int:
    """A level name case-insensitively, defaulting to INFO."""
    name = raw.strip().upper()
    level = getattr(logging, name, None)
    if isinstance(level, int):
        return level
    return logging.INFO


__all__ = ["configure"]
