"""Where this app keeps things, decided in one place.

Deliberately importable with nothing but the standard library, and importing
nothing from the rest of the package. The installer runs before the virtual
environment exists -- before `dotenv`, before `fastapi` -- and still has to put
the downloaded models exactly where the running app will look for them. A second
copy of this rule in the installer is a rule that drifts, and the symptom is a
300 MB download landing somewhere the app never checks.
"""

import os
from pathlib import Path


def data_dir() -> Path:
    """The user's data: the database, projects, logs, downloaded models.

    Outside the checkout on purpose. The database holds API keys and every
    conversation, so it must survive a `git clean -xdf`, and this is where a
    backup tool will look for it.

    Read at call time rather than at import, so an override in `.env` still
    counts once the app has loaded it.
    """
    override = os.getenv("BASICAGENT_DATA_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        base = Path(os.getenv("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "basicagent"


def models_dir() -> Path:
    """Speech models the installer fetches.

    Under the data directory so that everything this app downloaded is in one
    place and removing it removes all of it. `~/models/tts` is still searched
    for the read-aloud voices, because that is where older installs put them.
    """
    return data_dir() / "models"


LEGACY_TTS_DIR = Path.home() / "models" / "tts"
