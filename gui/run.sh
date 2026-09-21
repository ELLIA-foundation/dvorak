#!/usr/bin/env bash
# Launch the Dvorak analysis GUI, bootstrapping the venv on first use.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
venv="$here/.venv"

if [ ! -x "$venv/bin/python" ]; then
    echo "no venv yet, bootstrapping..."
    bash "$here/bootstrap.sh"
fi

cd "$here"
exec "$venv/bin/python" -m dvorak_gui "$@"
