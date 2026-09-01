#!/usr/bin/env python3
"""Build what a stranger downloads.

Run by the release workflow, and runnable by hand to see exactly what somebody
will get:

    python3 tools/make_download.py 1.2.3 --out /tmp/build

Three rules decide the shape of it, and all three came from somebody actually
trying to install this:

1. **No wrapper folder inside the zip.** Windows Explorer and macOS Archive
   Utility both create a folder named after the archive, so a zip containing
   one as well lands as a folder inside an identically named folder.

2. **Three things at the top, not twenty.** install.py beside install.sh beside
   pyproject.toml is a dozen wrong answers surrounding the right one, for
   somebody who was promised this was not technical. Everything the app is made
   of goes in `app`.

3. **One installer, not a menu.** A separate download per computer, so the
   folder holds the file for the machine it is sitting on and nothing else. A
   person who does not know whether they have Windows or a Mac -- and there are
   more of them than anybody building software likes to think -- cannot get
   this wrong if there is nothing to get wrong. The page picks for them.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Kept out of the app folder: version control, the release machinery, the
# built environment, and the download page, which is a website and not part of
# the program.
SKIP = shutil.ignore_patterns(
    ".git", ".github", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache",
    "docs", "*.pyc", "staging", "Install on *", "Read me first.txt")

PLATFORMS = {
    "Windows": ("Install on Windows.bat", """  Double-click  Install on Windows

  Windows will say it does not recognise who made this. Choose "More info",
  then "Run anyway" -- see the link below for why, and why that is honest
  rather than alarming.

  If this computer has not got Python, the installer offers to fetch it for
  you. Press Y and leave it alone. It never needs an administrator."""),
    "Mac": ("Install on Mac.command", """  Right-click  Install on Mac  ->  Open  ->  Open

  It has to be right-click the first time. Double-clicking it will be refused,
  because macOS cannot tell who made it -- see the link below for why.

  If this Mac has not got a recent enough Python, the installer offers to fetch
  it and hands it to Apple's own installer, which asks for your password the
  way anything installing software does."""),
    "Linux": ("Install on Linux.sh", """  Run  ./"Install on Linux.sh"   (or: python3 app/install.py)

  If your file manager will not run it, mark it executable first:
  chmod +x "Install on Linux.sh\""""),
}


def readme(version: str, platform: str) -> str:
    what = PLATFORMS[platform][1]
    return f"""Assistant {version}
Made by Tristan Johnson  --  https://tristan367.github.io/basicagent/


WHAT TO DO

{what}

That is the whole instruction. It takes about ten minutes, nearly all of it
downloading, and it only ever happens once.

When it finishes you will have an Assistant icon on your desktop. That is what
you click from then on. You will not need this folder again -- you can delete
it, including from your Downloads, because the installer copies the app
somewhere permanent and tells you where.


IF YOU WANT IT GONE AGAIN

There is an Uninstall in the folder the installer tells you about, and on
Windows it is in Settings under "Apps" like anything else. It asks before it
touches anything you made.


THE WARNING YOU ARE ABOUT TO SEE

Windows and Mac put a notice in front of anything whose publisher they cannot
identify, and identifying yourself means buying a certificate that costs a few
hundred dollars a year. I have not bought one, so the warning is right: this
comes from an unknown publisher. It comes from me.

Why you might believe me anyway, at length and honestly, including the parts
that are not reassuring:

  https://tristan367.github.io/basicagent/#safe


WHAT IS IN THIS FOLDER

  Install on {platform}   the one file you need
  app                everything the app is made of. Nothing in here needs
                     opening, and nothing in here needs understanding.
"""


def build(version: str, out: Path) -> list:
    out.mkdir(parents=True, exist_ok=True)
    app = out / "_app"
    if app.exists():
        shutil.rmtree(app)
    shutil.copytree(ROOT, app, ignore=SKIP)

    made = []
    # The one that names no platform keeps every launcher, for a link made
    # before this existed and for anybody whose browser was guessed wrong.
    for platform in (*PLATFORMS, None):
        stage = out / f"_stage-{platform or 'any'}"
        if stage.exists():
            shutil.rmtree(stage)
        (stage / "app").parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(app, stage / "app")

        wanted = [platform] if platform else list(PLATFORMS)
        for name in wanted:
            launcher = PLATFORMS[name][0]
            shutil.copy2(ROOT / launcher, stage / launcher)
            if not launcher.endswith(".bat"):
                (stage / launcher).chmod(0o755)
        (stage / "Read me first.txt").write_text(
            readme(version, platform or "Windows").replace("\n", "\r\n"),
            encoding="utf-8", newline="")

        name = f"Assistant-Setup-{platform}" if platform else "Assistant-Setup"
        # Zipped from inside, so the archive has no folder of its own. See
        # rule 1 at the top of this file.
        shutil.make_archive(str(out / name), "zip", str(stage))
        shutil.rmtree(stage)
        made.append(out / f"{name}.zip")
    shutil.rmtree(app)
    return made


def check(zips: list) -> None:
    """What a stranger unzips is what they were promised."""
    import zipfile

    for path in zips:
        names = set(zipfile.ZipFile(path).namelist())
        tops = {n.split("/")[0] for n in names}
        assert "app" in tops, f"{path.name}: no app folder"
        assert not any(n.startswith("app/app/") for n in names), \
            f"{path.name}: the zip contains itself"
        assert "install.py" not in tops, f"{path.name}: app internals at the top"
        for needed in ("app/install.py", "app/basicagent.py", "app/uninstall.py",
                       "app/VERSION", "app/requirements.txt"):
            assert needed in names, f"{path.name}: missing {needed}"
        launchers = {n for n in tops if n.startswith("Install on ")}
        assert launchers, f"{path.name}: nothing to double-click"
        if path.stem != "Assistant-Setup":
            platform = path.stem.rsplit("-", 1)[1]
            assert launchers == {PLATFORMS[platform][0]}, \
                f"{path.name}: holds {launchers}, which is a choice to get wrong"
        print(f"  {path.name}  {path.stat().st_size // 1024} KB  "
              f"{len(names)} files  {sorted(tops)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--out", default="dist")
    args = parser.parse_args()
    zips = build(args.version, Path(args.out))
    check(zips)
    return 0


if __name__ == "__main__":
    sys.exit(main())
