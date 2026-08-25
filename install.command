#!/bin/bash
# Setting the app up on a Mac, by double-clicking.
#
# A .command file is what Finder will run on a double-click; a .sh is not. The
# `cd` matters too -- Finder starts it in the user's home folder, not in the
# folder the file is sitting in, so without this it installs nothing anywhere
# useful.
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
    cat <<'MSG'

Python is not installed on this Mac yet.

The easiest way: open the App Store, search for Xcode, and install it -- that
brings Python with it. Or download it from https://www.python.org/downloads/

Then double-click this file again.

MSG
    read -r -p "Press return to close." _
    exit 1
fi

python3 install.py "$@"
status=$?
echo
if [ $status -eq 0 ]; then
    echo "Done. You can close this window and double-click Assistant.command to start."
else
    echo "Something went wrong above. The message says what."
fi
read -r -p "Press return to close." _
