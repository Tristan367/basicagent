"""Finding out that a new version exists, and putting it on.

The people this app is for cannot update it. There is no terminal in their
life, `git pull` is not a sentence they will ever type, and "download the new
one and copy it over the old one" is four chances to lose their projects. If
updating is not one button, it does not happen -- and an app that never updates
is one that keeps every bug it shipped with.

So: it asks GitHub once a day whether there is a newer release, says so quietly
if there is, and does the whole thing itself when told to.

Two ways in, because there are two ways people will have got this:

* A git clone, which is how anyone technical will have it. `git pull` with
  `--ff-only`, so a repository somebody has been poking at is never rewritten
  underneath them -- it refuses and says so instead.
* A folder unzipped from a release, which is how everybody else will have it.
  The new zip is fetched, unpacked somewhere else entirely, checked for signs
  of life, and only then swapped in.

Nothing here ever touches the data directory. Projects, settings and API keys
live in `~/.local/share/basicagent` (or the platform equivalent), and an update
replaces the program beside it without ever opening it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent

# Where releases come from. One place, because a mismatch between what the app
# checks and what the download page offers is a bug nobody would ever spot.
OWNER = "Tristan367"
REPO = "basicagent"
RELEASES_API = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{OWNER}/{REPO}/releases"

# Once a day. Often enough that a fix lands within a day of being published,
# rare enough that nobody's machine is talking to GitHub while they work.
CHECK_EVERY = 24 * 60 * 60
CHECK_KEY = "update_last_check"
FOUND_KEY = "update_found"

# Long enough for a slow connection on a bad day; short enough that a hung
# check cannot hold anything up. Nothing waits on this.
TIMEOUT = 20.0


def current() -> str:
    """This copy's version, from the VERSION file beside the code."""
    try:
        return (ROOT / "VERSION").read_text().strip()
    except OSError:
        return "0.0.0"


def _parts(version: str) -> tuple:
    """A version as numbers, so 1.10.0 sorts above 1.9.0 rather than below."""
    cleaned = (version or "").strip().lstrip("vV").split("+")[0].split("-")[0]
    out = []
    for piece in cleaned.split("."):
        try:
            out.append(int(piece))
        except ValueError:
            out.append(0)
    while len(out) < 3:
        out.append(0)
    return tuple(out[:3])


def newer(candidate: str, than: str) -> bool:
    return _parts(candidate) > _parts(than)


def from_git() -> bool:
    """Whether this is a clone, which decides how it updates."""
    return (ROOT / ".git").is_dir()


@dataclass
class Release:
    version: str
    notes: str
    url: str
    zip_url: str

    def as_dict(self) -> dict:
        return {"version": self.version, "notes": self.notes, "url": self.url}


async def look(force: bool = False) -> Release | None:
    """Ask GitHub what the newest release is, at most once a day.

    Never raises and never blocks anything important. No network, no GitHub, a
    rate limit, a firewall at a school: all of them mean "no news", which is
    the same as the common case and needs no explaining to anybody.
    """
    from agent_server import database as db

    if not force:
        last = await db.get_setting(CHECK_KEY, "")
        try:
            if last and time.time() - float(last) < CHECK_EVERY:
                saved = await db.get_setting(FOUND_KEY, "")
                return _remembered(saved)
        except ValueError:
            pass

    import httpx

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(
                RELEASES_API,
                headers={"Accept": "application/vnd.github+json"},
            )
        if response.status_code != 200:
            log.info("update check: GitHub said %s", response.status_code)
            await db.set_setting(CHECK_KEY, str(time.time()))
            return None
        data = response.json()
    except Exception as e:
        log.info("update check failed, which is not an error: %s", e)
        return None

    await db.set_setting(CHECK_KEY, str(time.time()))
    found = Release(
        version=str(data.get("tag_name") or "").lstrip("vV"),
        notes=str(data.get("body") or "").strip(),
        url=str(data.get("html_url") or RELEASES_PAGE),
        zip_url=str(data.get("zipball_url") or ""),
    )
    if not found.version or not newer(found.version, current()):
        await db.delete_setting(FOUND_KEY)
        return None
    await db.set_setting(FOUND_KEY, json.dumps(
        {"version": found.version, "notes": found.notes[:4000],
         "url": found.url, "zip_url": found.zip_url}))
    log.info("update available: %s (this is %s)", found.version, current())
    return found


def _remembered(saved: str) -> Release | None:
    if not saved:
        return None
    try:
        data = json.loads(saved)
    except ValueError:
        return None
    if not newer(str(data.get("version", "")), current()):
        return None
    return Release(version=data["version"], notes=data.get("notes", ""),
                   url=data.get("url", RELEASES_PAGE),
                   zip_url=data.get("zip_url", ""))


# ── putting it on ───────────────────────────────────────────────────────────


class UpdateError(RuntimeError):
    """Something went wrong, said in words the user can act on."""


async def apply() -> str:
    """Fetch the new version and put it in place. Returns what happened.

    Does not restart: that is the caller's job, and it wants to answer the
    request first so the page is not waiting on a server that has gone.
    """
    if from_git():
        return await asyncio.to_thread(_pull)
    return await _download_and_swap()


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=str(cwd or ROOT), capture_output=True,
                          text=True, timeout=600, check=False)


def _pull() -> str:
    """A clone: fast-forward only, then bring the dependencies up to date.

    `--ff-only` on purpose. Somebody with local changes -- and the person most
    likely to have them is whoever is reading this -- gets told plainly rather
    than having a merge performed on their behalf by a button.
    """
    if not shutil.which("git"):
        raise UpdateError(
            "This copy came from git, but git is not installed any more, so it "
            "cannot update itself. Reinstalling from the download page will "
            "work.")

    fetched = _run(["git", "fetch", "--quiet", "origin"])
    if fetched.returncode != 0:
        raise UpdateError(f"Could not reach the update server: "
                          f"{(fetched.stderr or '').strip()[:200]}")

    pulled = _run(["git", "merge", "--ff-only", "FETCH_HEAD"])
    if pulled.returncode != 0:
        raise UpdateError(
            "This copy has changes of its own, so it was left exactly as it "
            "is rather than merged into. Sort the changes out and try again, "
            f"or reinstall. ({(pulled.stderr or '').strip()[:160]})")

    return _refresh_dependencies() or "Updated."


def _venv_python() -> Path:
    inside = ROOT / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    exe = inside / ("python.exe" if os.name == "nt" else "python")
    return exe if exe.exists() else Path(sys.executable)


def _refresh_dependencies() -> str:
    """Install whatever the new version needs. Slow, and worth waiting for.

    A version that added a dependency and did not install it starts, fails on
    the first import, and looks exactly like a broken update -- which, to the
    person looking at it, it is.

    Three ways of doing it, because there are three ways the environment may
    have been made. `python -m pip` is the normal one and is what the installer
    produces. An environment built by `uv` has no pip in it at all, which is
    not exotic -- it is what any developer on this project will be running, and
    it is how this was found. And `ensurepip` can put pip into an environment
    that simply lost it.
    """
    python = str(_venv_python())
    attempts = [[python, "-m", "pip", "install", "--quiet", "-r", "requirements.txt"]]
    if shutil.which("uv"):
        attempts.append(["uv", "pip", "install", "--python", python,
                         "-r", "requirements.txt"])
    attempts.append([python, "-m", "ensurepip", "--upgrade"])

    last = ""
    for index, attempt in enumerate(attempts):
        result = _run(attempt)
        if result.returncode == 0:
            # `ensurepip` only installs pip; the actual install still has to run.
            if attempt[-1] == "--upgrade":
                return _refresh_dependencies()
            return "Updated."
        last = (result.stderr or result.stdout or "").strip()
        log.info("dependency install attempt %d failed: %s", index + 1, last[:200])

    # The code is already in place by this point, which changes what to say.
    # "The update failed" is wrong and frightening: what failed is the last
    # step, and if this version added nothing new the app will start perfectly.
    raise UpdateError(
        "The new version is in place, but the check for extra pieces it might "
        "need could not run. Restart the app -- it will very likely be fine. "
        f"If it is not, reinstalling from the download page will fix it. "
        f"({last[:160]})")


async def _download_and_swap() -> str:
    """A folder from a zip: fetch the new one, check it, then swap it in."""
    import httpx

    from agent_server import database as db

    saved = _remembered(await db.get_setting(FOUND_KEY, ""))
    if saved is None or not saved.zip_url:
        raise UpdateError("There is nothing to update to just now.")

    staging = Path(tempfile.mkdtemp(prefix="basicagent-update-"))
    try:
        try:
            async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
                response = await client.get(saved.zip_url)
            response.raise_for_status()
        except Exception as e:
            raise UpdateError(f"The new version could not be downloaded: "
                              f"{type(e).__name__}") from e

        archive = staging / "new.zip"
        archive.write_bytes(response.content)
        try:
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(staging / "unpacked")
        except zipfile.BadZipFile as e:
            raise UpdateError("The download arrived damaged. Try again.") from e

        # GitHub wraps everything in one folder named after the commit.
        unpacked = staging / "unpacked"
        inner = [p for p in unpacked.iterdir() if p.is_dir()]
        source = inner[0] if len(inner) == 1 else unpacked

        # Signs of life before anything is replaced. Half an app copied over a
        # working one is the worst outcome available here.
        for needed in ("agent_server", "requirements.txt", "VERSION"):
            if not (source / needed).exists():
                raise UpdateError(
                    "The download does not look like this app, so nothing was "
                    "changed.")

        await asyncio.to_thread(_swap_in, source)
        return await asyncio.to_thread(_refresh_dependencies)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# Never replaced, whatever a release contains. The virtual environment is this
# machine's, and the data directory is the user's -- though that lives
# elsewhere entirely and is only listed here as belt and braces.
KEEP = {".venv", ".git", "data", "projects"}


def _swap_in(source: Path) -> None:
    for item in source.iterdir():
        if item.name in KEEP:
            continue
        target = ROOT / item.name
        try:
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
        except OSError as e:
            raise UpdateError(
                f"Part of the update could not be written ({item.name}): {e}. "
                f"The app may need reinstalling from the download page.") from e
