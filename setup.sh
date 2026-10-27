#!/bin/sh
# Offline entry point. The release payload supplies Python and its pinned wheels.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PY=${TOOLS_PY:-}
if [ -z "$PY" ]; then
    for candidate in "$ROOT/deps/python/bin/python3" "$ROOT/python/bin/python3" "$ROOT/.venv/bin/python3"; do
        if [ -x "$candidate" ]; then PY=$candidate; break; fi
    done
fi
if [ -z "$PY" ] || [ ! -x "$PY" ]; then
    echo "Setup's Python is missing. Download Setup again, or set TOOLS_PY to a Python with capstone, numpy and Pillow." >&2
    exit 2
fi
exec "$PY" -I "$ROOT/tools/setup.py" "$@"
