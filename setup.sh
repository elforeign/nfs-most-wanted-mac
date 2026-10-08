#!/bin/sh
# Setup kit: builds the native Mac app from YOUR OWN copy of Need for Speed: Most Wanted (2005, PC, Black Edition 1.3).
# Usage: ./setup.sh "/path/to/Need for Speed Most Wanted" [options]   (./setup.sh --help for the options)
set -eu
cd "$(dirname "$0")"
if [ "$(uname -m)" != arm64 ]; then echo "This port needs an Apple Silicon Mac."; exit 1; fi
if ! xcode-select -p >/dev/null 2>&1; then
    echo "The Xcode Command Line Tools are needed. Run:  xcode-select --install   then run this setup again."; exit 1
fi
PYTHON=""
# Python 3.11 or 3.12, running natively on Apple Silicon. Every pinned build tool ships ready-made for these two
# (numpy 2.0.2 has none for 3.13 or later, and building it from source fails). An Intel-only Python (for example an
# old Homebrew in /usr/local) runs under Rosetta, and everything it starts would too: the Command Line Tools on
# macOS 27 have no Intel half.
SUITABLE='import platform, sys; sys.exit(not (3, 11) <= sys.version_info[:2] <= (3, 12) or platform.machine() != "arm64")'
for p in python3.12 python3.11 python3; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c "$SUITABLE" 2>/dev/null; then
        PYTHON=$p; break
    fi
done
if [ -z "$PYTHON" ]; then
    # No suitable Python on this Mac: use a standalone Python in the setup's own cache (pinned download, not installed system-wide).
    CACHE="$HOME/Library/NFSMW-Native-Setup"; PBS="$CACHE/python"
    URL="https://github.com/astral-sh/python-build-standalone/releases/download/20261003/cpython-3.12.15%2B20261003-aarch64-apple-darwin-install_only.tar.gz"
    SUM="316a463172740e71d8dca1f2730784e325f3f720941137b5d674d5801a632213"
    if [ ! -x "$PBS/bin/python3" ]; then
        echo "Python 3.11 or 3.12 not found: downloading a standalone Python 3.12 into $CACHE"
        mkdir -p "$CACHE"; curl -fL --retry 3 -o "$CACHE/python.tar.gz" "$URL"
        if [ "$(shasum -a 256 "$CACHE/python.tar.gz" | cut -d' ' -f1)" != "$SUM" ]; then echo "The Python download did not match its pinned SHA-256; not used."; rm -f "$CACHE/python.tar.gz"; exit 1; fi
        tar -xzf "$CACHE/python.tar.gz" -C "$CACHE"; rm -f "$CACHE/python.tar.gz"
    fi
    PYTHON="$PBS/bin/python3"
fi
if [ ! -f kit/tools/setup.py ]; then echo "The kit folder is missing: clone the whole repository again." >&2; exit 1; fi
# Checking the game folder needs nothing from the internet: no environment, no downloads.
for a in "$@"; do
    if [ "$a" = "--check-only" ]; then exec "$PYTHON" tools/setup_kit/setup.py "$@"; fi
done
# An environment an earlier run made with another Python (Intel, or 3.13 and later) is made again with this one.
if [ -x .venv/bin/python ] && ! .venv/bin/python -c "$SUITABLE" 2>/dev/null; then
    echo "Making the Python environment again (an earlier run used a Python these build tools do not support)."
    rm -rf .venv
fi
if [ ! -x .venv/bin/python ]; then "$PYTHON" -m venv .venv; fi
mkdir -p build
# Ready-made packages only: a package that would have to be compiled stops here with a clear reason instead.
if ! .venv/bin/python -m pip install --disable-pip-version-check --retries 8 --timeout 30 --only-binary=:all: \
        -r kit/requirements-dev.txt > build/pip.log 2>&1; then
    cp build/pip.log build/setup.log   # what "Show the log" in the Setup app opens
    echo
    tail -n 12 build/pip.log
    echo
    if grep -q -i -E "establish a new connection|Max retries exceeded|Temporary failure in name resolution|nodename nor servname|ProxyError|SSLError|Connection(Reset|Refused)Error|Read timed out" build/pip.log; then
        echo "SETUP STOPPED: the setup could not download its build tools from the Python package index (pypi.org)."
        echo "Check the internet connection. A VPN, proxy or firewall app (for example Little Snitch or LuLu) may be"
        echo "blocking Python or the Setup app: allow them to connect, or pause it, and run the setup again."
    else
        echo "SETUP STOPPED: the setup could not install its build tools (details above and in build/pip.log)."
        echo "Please report it on the project's GitHub page with that file."
    fi
    exit 1
fi
exec .venv/bin/python tools/setup_kit/setup.py "$@"
