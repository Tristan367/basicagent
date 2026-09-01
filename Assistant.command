#!/bin/bash
# Starting the app on a Mac, by double-clicking. Close the app window to stop it.
#
# Two places to look, because the installer copies the app out of the folder it
# was unzipped into. Somebody who kept the download and double-clicks this copy
# of it should get the app, not a lecture about installing it again.
cd "$(dirname "$0")" || exit 1

app="$PWD"
if [ ! -x "$app/.venv/bin/python" ]; then
    app="$HOME/Library/Application Support/Assistant"
fi

if [ ! -x "$app/.venv/bin/python" ]; then
    echo
    echo "This has not been set up yet. Open \"Install on Mac\" first,"
    echo "wait for it to finish, then run this again."
    echo
    read -r -p "Press return to close." _
    exit 1
fi

cd "$app" || exit 1
.venv/bin/python basicagent.py "$@"
