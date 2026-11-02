#!/bin/sh
# Asset-free macOS/Linux build. --stage creates a minimal Docker build context.
set -eu
cd "$(dirname "$0")"
cc=${CC:-cc}
if [ "${1:-}" = --stage ]; then
    [ "$#" = 2 ] || { echo 'usage: roam_relay_build.sh --stage EMPTY_DIRECTORY' >&2; exit 2; }
    mkdir -p "$2"
    stage=$(cd "$2" && pwd)
    [ -z "$(ls -A "$stage")" ] || { echo 'stage must be empty' >&2; exit 2; }
    # Compiler dependency discovery follows transitive quoted headers, including newly added relay headers.
    # Relative paths keep this independent of checkout location and exclude system headers/assets.
    deps=$($cc -std=c11 -D_DEFAULT_SOURCE -D_DARWIN_C_SOURCE -MM roam_relay_main.c roam_core.c roam_auth.c roam_moderation.c roam_relay.c)
    files=$(printf '%s\n' "$deps" | sed 's/^[^:]*://; s/\\$//' | tr '\n' ' ')
    for file in $files; do
        # Apple clang emits SDK metadata even with -MM; it is not a local header.
        case "$file" in /*/MacOSX*.sdk/SDKSettings.json) continue ;; esac
        case "$file" in
            roam_relay_main.c|roam_core.c|roam_auth.c|roam_moderation.c|roam_relay.c|*.h) ;;
            *) echo "unexpected dependency: $file" >&2; exit 1 ;;
        esac
        case "$file" in /*|*..*) echo 'dependency escapes relay directory' >&2; exit 1 ;; esac
        mkdir -p "$stage/$(dirname "$file")"
        cp "$file" "$stage/$file"
    done
    cp roam_relay_build.sh "$stage/build.sh"
    cp relay/Dockerfile "$stage/Dockerfile"
    # A protocol-aware, loopback-only smoke peer; never part of the production image.
    cp roam_relay_cli_test.py "$stage/cli_test.py"
    echo "relay source stage: $stage"
    exit 0
fi
[ "$#" = 1 ] || { echo 'usage: roam_relay_build.sh OUTPUT | --stage EMPTY_DIRECTORY' >&2; exit 2; }
case $(uname -s) in
    Darwin) libs='-framework Security -framework CoreFoundation' ;;
    Linux) libs='-lm -lcrypto' ;;
    *) echo 'supported platforms: macOS and Linux' >&2; exit 2 ;;
esac
# Fixed translation-unit list: never pull in the game, generated tables or game-facing frameworks.
$cc -std=c11 -D_DEFAULT_SOURCE -D_DARWIN_C_SOURCE -O2 -Wall -Wextra -Werror \
    roam_relay_main.c roam_core.c roam_auth.c roam_moderation.c roam_relay.c $libs -o "$1"
echo "relay build: $(uname -s)/$(uname -m); $($cc --version | head -1)"
