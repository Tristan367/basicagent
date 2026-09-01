#!/usr/bin/env bash
# Setting the app up on Linux.
#
#     ./"Install on Linux.sh"
#
# Or, if the file manager will not run it: python3 app/install.py
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Two layouts, one file: `app` in the download, beside this file in the source
# repository. See the note in "Install on Windows.bat".
app="$here"
[ -f "$here/app/install.py" ] && app="$here/app"
cd "$app"

if ! command -v python3 >/dev/null 2>&1; then
    echo
    echo "Python is not installed here. On Ubuntu or Debian:"
    echo "    sudo apt install python3 python3-venv"
    echo
    exit 1
fi

exec python3 install.py "$@"
