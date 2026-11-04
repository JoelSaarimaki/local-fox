#!/bin/sh
# Builds Local Fox on macOS or Linux into dist/LocalFox (and dist/LocalFox.app on macOS), with the tools of
# ollmcp-generic-tools bundled: the counterpart of build.ps1. Creates the virtual environment .venv with the
# dependencies first if it does not exist, and installs the tools from the given repository (the sibling
# repository by default) into it on every build, so the application has their current version.
#     ./build.sh [path to ollmcp-generic-tools]
set -e
cd "$(dirname "$0")"
TOOLS_REPO="${1:-../ollmcp-generic-tools}"
PYTHON=.venv/bin/python
if [ ! -x "$PYTHON" ]; then
    python3 -m venv .venv
    "$PYTHON" -m pip install --upgrade pip
    "$PYTHON" -m pip install -r requirements.txt
fi
if [ ! -f "$TOOLS_REPO/pyproject.toml" ]; then
    echo "The tools repository was not found: $TOOLS_REPO"
    exit 1
fi
# Its dependencies first, then the package itself again, as its version number does not change with its code.
# Not editable: PyInstaller does not follow the import hooks of an editable install.
"$PYTHON" -m pip install --quiet "$TOOLS_REPO"
"$PYTHON" -m pip install --quiet --no-deps --force-reinstall "$TOOLS_REPO"
# The application's icon, from the logo
if [ "$(uname)" = "Darwin" ]; then ICON=build/local_fox.icns; else ICON=build/local_fox.ico; fi
"$PYTHON" -m local_fox.ui.logo "$ICON"
"$PYTHON" -m PyInstaller --noconfirm --log-level WARN --distpath dist --workpath build packaging/local_fox.spec
if [ "$(uname)" = "Darwin" ]; then
    echo "Built dist/LocalFox.app"
else
    echo "Built dist/LocalFox/LocalFox"
fi
