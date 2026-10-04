#!/bin/bash
set -euo pipefail

app_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
project_dir="$(dirname -- "$app_dir")"
if [[ -x "$project_dir/.venv/bin/python" ]]; then
    pixelkit_python="$project_dir/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    pixelkit_python="$(command -v python3)"
else
    echo "Install Python 3.10 or newer. See the PixelKit README."
    exit 1
fi

if ! "$pixelkit_python" -c 'import PyQt6, pixelkit' >/dev/null 2>&1; then
    echo "Install the source dependencies first. From the PixelKit folder, run:"
    echo "  python3 -m venv .venv"
    echo '  .venv/bin/python -m pip install -e .'
    echo "  brew install imagemagick"
    exit 1
fi
exec "$pixelkit_python" -m pixelkit "$@"
