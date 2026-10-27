#!/bin/sh
# run_guarded.sh <seconds> <diag dir> [NAME=value ...] - one game run of this checkout with the unattended-run rules
# built in: BOOT_BACKGROUND=1 (window behind, never active: the user may be playing), NFS_AUDIO=null NFS_RUMBLE=off NFS_PAD_TRIGGERS=off NFS_PAD_LIGHT=off NFS_FULLSCREEN=0 BOOT_DIAG_SHOT_EVERY=0 (each overridable by an argument),
# a scratch BOOT_USERDATA (<diag dir>.user), BOOT_DIAG=<diag dir>, and a kill guard (SIGTERM after <seconds>,
# SIGKILL 5 s later) as a second layer: the game enforces BOOT_DEADLINE=<seconds + 3> itself (clean exit, hard exit 5 s
# later) and exits when this script is gone, so a killed caller can't leave an orphaned game. stdout/stderr go to <diag dir>.out. BOOT_BIN picks the binary (default runtime/boot).
# Guarded game launches use this script, soak.sh or probe.sh.
# A machine-wide game slot (runtime/game_slot.sh: at most 2 games, NFS_SLOT=solo for timing) is taken before the launch;
# while it waits, <diag dir>.out doesn't exist yet.
secs=${1:?seconds}; diag=${2:?diag dir}; shift 2
# One argument per setting: "A=1 B=2" as one argument (zsh doesn't split an unquoted $VAR) sets A to "1 B=2" and
# drops B (a malformed BOOT_RACE hung the race start at frame ~110); a diag dir with a space is the same mistake.
case $diag in *" "*) echo "run_guarded: diag dir \"$diag\" has a space (settings in the path?)" >&2; exit 2;; esac
for a in "$@"; do
    case $a in *=*" "[A-Za-z_]*=*) echo "run_guarded: one argument holds several settings: \"$a\" (pass each NAME=value separately)" >&2; exit 2;; esac
    # The launcher sets these itself; BOOT_DIAG=1 once sent a run's logs to ./1 (2026-10-23).
    case $a in BOOT_DIAG=*|BOOT_USERDATA=*) echo "run_guarded: \"$a\": the launcher sets ${a%%=*} itself (logs go to the diag dir argument)" >&2; exit 2;; esac
done
cd "$(dirname "$0")/.."
# The user's NFSMW.app (process name NFSMW; test runs are runtime/boot) means they are playing: wait, never launch beside
# it (a concurrent gate started a game during the user's session because its pgrep check wasn't enforced).
if pgrep -x NFSMW >/dev/null; then
    echo "run_guarded: the user's NFSMW.app is running: waiting until it closes" >&2
    while pgrep -x NFSMW >/dev/null; do sleep 30; done
fi
. runtime/game_slot.sh
slot_note=$diag
# BEGIN offline cleanup checks
pid= guard=
cleanup() {
    trap '' TERM INT HUP
    if [ -n "$guard" ]; then
        kill -TERM "$guard" 2>/dev/null
        wait "$guard" 2>/dev/null
        guard=
    fi
    if [ -n "$pid" ]; then
        kill -TERM "$pid" 2>/dev/null
        left=50
        while kill -0 "$pid" 2>/dev/null && [ "$left" -gt 0 ]; do
            sleep 0.1
            left=$((left - 1))
        done
        kill -KILL "$pid" 2>/dev/null
        wait "$pid" 2>/dev/null
        pid=
    fi
    slot_release "${rc:-}"
}
trap cleanup EXIT
trap 'rc=143; exit 143' TERM INT HUP
# END offline cleanup checks
slot_acquire run
rm -rf "$diag"; mkdir -p "$diag" "$diag.user"
env BOOT_BACKGROUND=1 NFS_AUDIO=null NFS_RUMBLE=off NFS_PAD_TRIGGERS=off NFS_PAD_LIGHT=off NFS_FULLSCREEN=0 BOOT_DIAG_SHOT_EVERY=0 BOOT_USERDATA="$diag.user" BOOT_DIAG="$diag" BOOT_DEADLINE=$((secs + 3)) "$@" \
  "${BOOT_BIN:-runtime/boot}" "${NFS_GAME:-$(cd .. && pwd)/Need For Speed Most Wanted Black Edition}" > "$diag.out" 2>&1 &
pid=$!
slot_started $pid
# A stoppable guard: terminating just a shell running foreground sleep would leave that sleep orphaned.
(
    timer=
    trap '[ -z "$timer" ] || { kill "$timer" 2>/dev/null; wait "$timer" 2>/dev/null; }; exit 0' TERM INT HUP
    sleep "$secs" & timer=$!; wait "$timer"; timer=
    kill -TERM "$pid" 2>/dev/null
    sleep 5 & timer=$!; wait "$timer"; timer=
    kill -KILL "$pid" 2>/dev/null
) &
guard=$!
wait "$pid"; rc=$?; pid=
kill "$guard" 2>/dev/null
wait "$guard" 2>/dev/null; guard=
slot_release $rc
echo "run_guarded: exit $rc ($( [ $rc = 143 ] && echo "stopped by the guard" || { [ $rc = 124 ] && echo "stopped by its own deadline" || echo "exited by itself"; }))"
