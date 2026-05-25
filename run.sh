#!/usr/bin/env bash
# Activate the venv and start AnimaDex. Any extra args (e.g. --dev) are
# passed through to `python -m animadex serve`.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    echo "No .venv found -- run ./install.sh first." >&2
    exit 1
fi
# shellcheck disable=SC1091
. .venv/bin/activate
exec python -m animadex serve "$@"
