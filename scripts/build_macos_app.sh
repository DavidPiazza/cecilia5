#!/bin/sh
# Build dist/Cecilia5.app with PyInstaller from the project's virtual env.
#
# First time setup:
#   python3.13 -m venv .venv
#   .venv/bin/pip install -r requirements.txt -r requirements-build.txt
set -e
cd "$(dirname "$0")/.."

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
    echo "No .venv found, see the comments at the top of this script." >&2
    exit 1
fi

rm -rf build dist
"$PY" -m PyInstaller --noconfirm --clean Cecilia5.spec

# Ad-hoc sign the whole bundle so macOS accepts it on Apple silicon.
codesign --force --deep --sign - dist/Cecilia5.app

echo "Built dist/Cecilia5.app"
