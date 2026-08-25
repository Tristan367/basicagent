#!/bin/bash
# Starting the app on a Mac, by double-clicking. Close the app window to stop it.
cd "$(dirname "$0")" || exit 1

if [ ! -x ".venv/bin/python" ]; then
    echo
    echo "This has not been set up yet. Double-click install.command first,"
    echo "wait for it to finish, then run this again."
    echo
    read -r -p "Press return to close." _
    exit 1
fi

.venv/bin/python basicagent.py "$@"
