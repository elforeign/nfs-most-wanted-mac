#!/bin/sh
# Builds the shader translator (build/libsmtranslate.a) and its tests, then runs them:
#   ./build.sh          build + all checks
#   ./build.sh lib      library only
# Test data: testdata/ (the game's 108 shaders, extracted from the player's speed.exe; optional) and testdata_synth/
# (16 committed synthetic shaders). Full results are described in README.md.
set -e
cd "$(dirname "$0")"
mkdir -p build
# Every run works in its own private dir and only publishes build/libsmtranslate.a (atomically, by rename).
# Two runs in one checkout (or runtime/build.sh linking the library) used to share build/*.o, build/msl and the
# library, which the next run deleted and re-created: a concurrent link failed with "library not found" and the
# next run passed (reproduced with concurrent builds). Any failing command now ends the script nonzero.
find build -maxdepth 1 -name 'work.*' -mtime +1 -exec rm -rf {} + 2>/dev/null || true
W=build/work.$$
mkdir -p "$W"
trap 'rc=$?; if [ $rc = 0 ]; then rm -rf "$W"; else echo "build.sh FAILED (exit $rc); work dir kept: host/shaders/$W" >&2; fi' EXIT
CXX="clang++ -std=c++17 -O2 -Wall -Wextra"
$CXX -c sm_parse.cpp -o $W/sm_parse.o
$CXX -c sm_msl.cpp -o $W/sm_msl.o
rm -f $W/libsmtranslate.a
ar rcs $W/libsmtranslate.a $W/sm_parse.o $W/sm_msl.o
cp $W/libsmtranslate.a build/.libsmtranslate.a.$$
mv -f build/.libsmtranslate.a.$$ build/libsmtranslate.a
[ "$1" = "lib" ] && exit 0

L=$W/libsmtranslate.a
clang -std=c99 -Wall -Wextra -pedantic -c tests/c_api_test.c -o $W/c_api_test.o
clang++ $W/c_api_test.o $L -o $W/c_api_test
$CXX tests/disasm_check.cpp $L -o $W/disasm_check
$CXX tests/translate_all.cpp $L -o $W/translate_all
OBJC="clang++ -std=c++17 -O1 -Wall -Wextra -fobjc-arc -ffp-contract=off"
$OBJC tests/numeric_check.mm tests/sm_interp.cpp $L -framework Metal -framework Foundation -o $W/numeric_check
$OBJC tests/link_check.mm $L -framework Metal -framework Foundation -o $W/link_check
$OBJC tests/render_check.mm tests/sm_interp.cpp $L -framework Metal -framework Foundation -o $W/render_check

R=../../../render_diff/hdr_dof/pc_effects
# Missing reference: before exit 1, now SKIP/exit 0; a present decoder failure (exit 7) still exits 7.
# The public build (NFS_PUBLIC=1, as Setup runs it) never reads outside its own tree (M4 F7).
if [ "${NFS_PUBLIC:-0}" != 1 ] && [ -d "$R" ]; then
    echo "== decoder vs reference disassembly";  $W/disasm_check testdata "$R"
else
    echo "SKIP decoder vs optional reference disassembly: $R is absent"
fi
$W/disasm_check testdata_synth testdata_synth/ref
echo "== translate";                         $W/translate_all testdata $W/msl
$W/translate_all testdata_synth $W/msl_synth
echo "== xcrun metal -c";                    ./tools/compile_all.sh $W/msl
./tools/compile_all.sh $W/msl_synth
echo "== GPU vs CPU reference";              $W/numeric_check testdata --math fast
$W/numeric_check testdata --math safe
$W/numeric_check testdata --shadow
$W/numeric_check testdata_synth --math fast
echo "== VS/PS pipelines";                   $W/link_check testdata
echo "== end-to-end rendering";              $W/render_check testdata >$W/render.out || { cat $W/render.out; exit 1; }   # no pipe: it would hide the exit status
tail -1 $W/render.out
echo "BUILD_SH PASS"
