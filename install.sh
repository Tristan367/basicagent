#!/usr/bin/env bash
# Thin wrapper that delegates to the cross-platform Python installer.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec python3 install.py
