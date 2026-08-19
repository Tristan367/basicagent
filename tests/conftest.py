"""Test fixtures.

`agent_server.config` creates its data directories at import time, so the data
dir has to be redirected *before* anything imports it. Doing that here, at the
top of conftest, is the only place early enough — a fixture would already be too
late and the suite would scribble in the developer's real
``~/.local/share/basicagent``.
"""

import os
import tempfile
from pathlib import Path

_TMP_DATA = Path(tempfile.mkdtemp(prefix="basicagent-tests-"))
os.environ["BASICAGENT_DATA_DIR"] = str(_TMP_DATA)
os.environ["BASICAGENT_DB"] = str(_TMP_DATA / "test.db")

import pytest  # noqa: E402


@pytest.fixture
async def db():
    """A clean database per test, on its own file."""
    import agent_server.database as database

    await database.close()
    db_path = _TMP_DATA / f"t{os.getpid()}_{next(_counter)}.db"
    database.DB_PATH = db_path
    # `config.DB_PATH` is read independently by the synchronous credential
    # lookup, so both have to move or the two disagree about which file is live.
    import agent_server.config as config

    config.DB_PATH = db_path
    await database.init_db()
    try:
        yield database
    finally:
        await database.close()
        db_path.unlink(missing_ok=True)


def _count():
    n = 0
    while True:
        yield n
        n += 1


_counter = _count()
