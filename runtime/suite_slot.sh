# suite_slot.sh - sourced by offline_tests.sh: at most OFFLINE_SUITE_SLOTS (default 2) whole offline suites at once
# for this user, across every checkout and session (four at once ran the machine at load 20-29 on 18 cores and made
# timing-sensitive tests fail).
# A slot is a file <dir>/slot<n> held open on file descriptor 9 under a non-blocking exclusive flock. The lock belongs
# to the open file description, so the kernel frees it when the suite and every child that inherited fd 9 have exited
# (even after SIGKILL): no pid files, no reclaim, no stale locks, no pid-reuse races. A suite started inside a
# slot-holding one inherits fd 9 and OFFLINE_SUITE_SLOT_HELD=1 and takes no second slot; an orphaned nested suite keeps
# the slot until it ends.
# Directory: $SUITE_SLOT_DIR; else $NFS_RUNS/suite_slots when $NFS_RUNS exists and is writable (the shared runs folder
# of the private build); else ${TMPDIR:-/tmp}/nfsmw-suite-slots (per user; public trees). If the directory or a slot
# file is unusable, or flock itself errors, one line is logged and the suite runs without a slot (no retry loop).
# Proof: suite_slot_test.py.
suite_slot_dir=${SUITE_SLOT_DIR:-}
if [ -z "$suite_slot_dir" ]; then
    if [ -n "${NFS_RUNS:-}" ] && [ -d "$NFS_RUNS" ] && [ -w "$NFS_RUNS" ]; then suite_slot_dir=$NFS_RUNS/suite_slots
    else suite_slot_dir=${TMPDIR:-/tmp}/nfsmw-suite-slots; fi
fi

suite_slot_acquire() {
    [ -z "${OFFLINE_SUITE_SLOT_HELD:-}" ] || return 0
    max=${OFFLINE_SUITE_SLOTS:-2}; said=0; waited=0; t0=$(date +%s)
    py=${TOOLS_PY:-python3}
    while :; do
        n=0
        while [ "$n" -lt "$max" ]; do
            f=$suite_slot_dir/slot$n; n=$((n + 1))
            if ! { mkdir -p "$suite_slot_dir" && : >>"$f"; } 2>/dev/null; then
                echo "offline_tests: cannot use $suite_slot_dir: running without a whole-suite slot" >&2; return 0
            fi
            exec 9>>"$f"
            "$py" -I -c 'import fcntl, sys
try:
    fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError as e:
    sys.exit(1 if e.errno in (11, 35) else 2)' 2>/dev/null
            case $? in
                0) export OFFLINE_SUITE_SLOT_HELD=1
                   if [ "$waited" = 1 ]; then
                       echo "offline_tests: whole-suite slot acquired after $(( $(date +%s) - t0 )) s" >&2
                   fi
                   return 0 ;;
                1) exec 9>&- ;;
                *) exec 9>&-; echo "offline_tests: flock unavailable: running without a whole-suite slot" >&2; return 0 ;;
            esac
        done
        waited=1; now=$(date +%s)
        if [ $((now - said)) -ge 300 ]; then
            echo "offline_tests: waiting for a whole-suite slot ($max in use; $((now - t0)) s so far)" >&2
            said=$now
        fi
        sleep 2
    done
}
