#!/usr/bin/env bash
# Create the GUI virtual environment. Run from anywhere:
#
#   bash gui/bootstrap.sh
#
# This never touches the system Python and never installs instrument drivers.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
venv="$here/.venv"

PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "error: no '$PYTHON' on PATH. Install Python 3.10 or newer, or set PYTHON=..." >&2
    exit 1
fi

ver="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
major="${ver%%.*}"
minor="${ver##*.}"
if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 10 ]; }; then
    echo "error: Python $ver is too old; PySide6 needs 3.10 or newer" >&2
    exit 1
fi

echo "creating venv at $venv (python $ver)"
"$PYTHON" -m venv "$venv"

"$venv/bin/python" -m pip install --upgrade pip >/dev/null
"$venv/bin/python" -m pip install -r "$here/requirements.txt"

echo
echo "done. launch the GUI with:"
echo "  $venv/bin/python -m dvorak_gui"
echo "or simply:"
echo "  bash $here/run.sh"
