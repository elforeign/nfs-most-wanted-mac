#!/bin/sh
# compile_all.sh [msl_dir] - compiles every generated .metal with the Metal compiler
# (xcrun -sdk macosx metal -c), in parallel. Prints failures; exit 1 if any failed.
DIR=${1:-build/msl}
LOG=$DIR/compile.log
if [ "$2" = "--one" ]; then
  f=$3
                                                                                                                         
  if xcrun -sdk macosx metal -fmodules-cache-path="$DIR/.modcache" -c "$f" -o "$f.air" 2> "$f.err"; then
    if [ -s "$f.err" ]; then echo "WARN $f"; else echo "OK $f"; fi
  else echo "FAIL $f"; fi
  exit 0
fi
# No "xcrun --kill-cache" here: it deletes the per-user xcrun tool-lookup db that every other xcrun on the machine
# (concurrent builds, runtime tests) is reading, and nothing needs it (the db rebuilds itself).
total=$(find "$DIR" -name '*.metal' | wc -l | tr -d ' ')
find "$DIR" -name '*.metal' | sort | xargs -P 8 -n 1 "$0" "$DIR" --one > "$LOG"
ok=$(grep -c '^OK' "$LOG"); warn=$(grep -c '^WARN' "$LOG"); fail=$(grep -c '^FAIL' "$LOG")
echo "metal compile: $ok ok, $warn with warnings, $fail failed (log: $LOG)"
grep '^FAIL' "$LOG" | head -20
# every file must have reported exactly one result line (a killed/crashed child reports none), and there must be files
[ "$total" -gt 0 ] && [ $((ok + warn + fail)) = "$total" ] || { echo "metal compile: FAIL expected $total results, got $((ok + warn + fail))"; exit 1; }
[ "$fail" = 0 ]
