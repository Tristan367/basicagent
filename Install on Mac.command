#!/bin/bash
# Setting the app up on a Mac, by double-clicking.
#
# A .command file is what Finder will run on a double-click; a .sh is not. The
# `cd` matters too -- Finder starts it in the user's home folder, not in the
# folder the file is sitting in, so without this it installs nothing anywhere
# useful.
here="$(cd "$(dirname "$0")" && pwd)" || exit 1

# Two layouts, one file: `app` in the download, beside this file in the source
# repository. See the note in "Install on Windows.bat".
app="$here"
[ -f "$here/app/install.py" ] && app="$here/app"
cd "$app" || exit 1

# The Python that comes with macOS is 3.9, which is older than any of this
# supports, and the developer tools are not much newer. So "install Xcode from
# the App Store" -- which is what this used to say -- was a 3 GB download that
# does not even solve the problem. This fetches the official python.org
# installer instead, checks it against the exact SHA-256 of the file the
# CPython release manager signed, and hands it to Apple's own installer.
PYVER="3.13.15"
PYURL="https://www.python.org/ftp/python/$PYVER/python-$PYVER-macos11.pkg"
PYSUM="3b7eaf7f29825f796e8267024435540ddf1f17fc9a97ad58095daa7a75bfdcd3"
FRAMEWORK="/Library/Frameworks/Python.framework/Versions/3.13/bin/python3"

find_python() {
    PY=""
    [ -x "$FRAMEWORK" ] && PY="$FRAMEWORK" && return 0
    command -v python3 >/dev/null 2>&1 && PY="python3" && return 0
    return 1
}

get_python() {
    echo
    echo "  This app is built on Python, and this Mac does not have a version"
    echo "  of it that will do. Python is free and comes from python.org."
    echo
    printf "  Get it now? It is about 70 MB. [Y/n] "
    read -r answer
    case "$answer" in [Nn]*) return 1 ;; esac

    pkg="$(mktemp -d)/python-$PYVER.pkg"
    echo
    echo "  Downloading Python $PYVER from python.org..."
    curl -L --fail -o "$pkg" "$PYURL" || return 1

    echo
    echo "  Checking the download is the file python.org signed..."
    got="$(shasum -a 256 "$pkg" | cut -d' ' -f1)"
    if [ "$got" != "$PYSUM" ]; then
        echo "  It is not. Nothing has been run."
        rm -f "$pkg"
        return 1
    fi
    echo "  It is."
    echo
    echo "  Opening Apple's installer. Click through it -- it will ask for your"
    echo "  password, which is normal for anything that installs software. When"
    echo "  it has finished, come back to this window."
    open -W "$pkg"
    rm -f "$pkg"
    find_python
}

if ! find_python; then
    get_python || PY=""
fi

if [ -z "$PY" ]; then
    echo
    echo "  Python is still not here, so this cannot carry on. Get it from"
    echo "  https://www.python.org/downloads/macos/ -- the one that says"
    echo "  \"macOS 64-bit universal2 installer\" -- then open this file again."
    open "https://www.python.org/downloads/macos/" 2>/dev/null
    read -r -p "Press return to close." _
    exit 1
fi

"$PY" install.py "$@"
status=$?

# Exit code 3 means it found a Python and it was the wrong version -- too new
# for one of the parts, which is the ordinary case on a Mac bought this year.
if [ "$status" -eq 3 ]; then
    if get_python && [ -x "$FRAMEWORK" ]; then
        "$FRAMEWORK" install.py "$@"
        status=$?
    fi
fi

echo
if [ $status -eq 0 ]; then
    echo "Done. You can close this window. Open Assistant from your"
    echo "Applications folder or from your desktop."
else
    echo "Something went wrong above. The message says what."
fi
read -r -p "Press return to close." _
