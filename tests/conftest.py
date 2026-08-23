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


LOOPBACK = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}


@pytest.fixture(autouse=True)
def _no_internet(request, monkeypatch):
    """Nothing in this suite reaches the internet unless it says it does.

    Written after a test suite that took seventeen seconds started taking three
    minutes, because a change made the `game` tool install Godot on demand and
    a test walked straight into the real downloader and pulled 80 MB off a
    release server. Nothing failed. It just quietly became a suite that needs
    the internet, downloads most of a gigabyte over a day's work, and breaks on
    a train.

    Loopback stays open: several tests spawn a real server and then really do
    fetch from it, which is the point of them. Anything marked `live` is asking
    for a real provider and pays for it, and is deselected by default anyway.
    """
    if request.node.get_closest_marker("live"):
        return

    import socket

    real_connect = socket.socket.connect

    def guarded(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else None
        if host is not None and host not in LOOPBACK:
            raise AssertionError(
                f"a test tried to reach {host}. Stub it, or mark the test "
                "`live` if it genuinely needs the network."
            )
        return real_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded)


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
