#!/bin/sh
# train_preflight.sh <pre|post> - standalone cheap checks, each a past gate failure.
# land_train.sh uses ready.sh to build and report all checks on the combined tree before merging.
# pre:  the checkout was made by runtime/new_worktree.sh (third_party link and cloned build outputs; bare checkouts
#       failed 9 then 11 offline tests, 16.6 gate-min) and the toolchain works (Metal Toolchain lost 149 min).
# post: on the merged train, the publication scan (6 gate stops) and the doc budgets (2 gates) pass.
# Prints "preflight <phase>: OK" or "preflight <phase>: FAIL <reason>" and exits 1 on failure.
set -u
phase=${1:?pre|post}
root=$(cd "$(dirname "$0")/.." && pwd)
py=${TOOLS_PY:-$(cd "$root/.." && pwd)/tools/.venv/bin/python}
fail() { echo "preflight $phase: FAIL $1"; exit 1; }
case $phase in
pre)
    for f in third_party/ffmpeg/lib/libavcodec.a host/audio/build/libhostaudio.a host/shaders/build/libsmtranslate.a; do
        [ -e "$root/$f" ] || fail "$f missing: make the train checkout with runtime/new_worktree.sh"
    done
    [ "$(xcodebuild -showComponent MetalToolchain 2>/dev/null | sed -n 's/^Status: //p')" = installed ] ||
        fail 'Metal Toolchain not installed'
    xcrun --show-sdk-version >/dev/null 2>&1 || fail 'xcrun SDK lookup fails (Command Line Tools vs Xcode version)'
    ;;
post)
    scan=$(mktemp -d /private/tmp/pubscan.XXXXXX)
    out=$("$py" -I "$root/tools/publish/import.py" "$root" "$scan/s" --summary-only 2>&1 | tail -1)
    rm -rf "$scan"
    case $out in *'total=0'*) ;; *) fail "publication scan: $out" ;; esac
    "$py" -I "$root/runtime/doc_budget_test.py" >/dev/null 2>&1 || fail 'doc budgets (runtime/doc_budget_test.py)'
    ;;
*) fail 'phase must be pre or post' ;;
esac
echo "preflight $phase: OK"
