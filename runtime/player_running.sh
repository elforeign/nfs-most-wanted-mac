#!/bin/sh
# player_running.sh - exit 0 iff the player's own game runs (stdout: "<pid> <path>" per game), else exit 1.
# The player's game is a process named NFSMW whose executable is inside an NFSMW.app bundle and that no launcher
# started: run_guarded.sh/probe.sh/soak.sh record their game's pid in $NFS_RUNS/slots/<launcher pid> ("<kind> <pid>"),
# so the parent does not matter (the player may open the app, or start it from a terminal). Matching command lines
                                                                                                                  
# matrix waited 17 min on one.
# Env: NFS_RUNS (default <checkout parent>/.runs), PLAYER_RUNNING_GAME (path suffix, default
# /NFSMW.app/Contents/MacOS/NFSMW). Used by solo_matrix.sh, run_guarded.sh and the other launch checks.
# Proof: player_running_test.py (a process whose argv only names the path: not the player; a game started from a
# terminal: the player; copies of the old command-line pattern and the old launchd-parent rule fail).
game=${PLAYER_RUNNING_GAME:-/NFSMW.app/Contents/MacOS/NFSMW}
runs=${NFS_RUNS:-$(cd "$(dirname "$0")/../.." && pwd)/.runs}
ours=" "
for f in "$runs"/slots/*; do
    [ -f "$f" ] || continue
    read -r _ g _ < "$f" 2>/dev/null && ours="$ours$g "
done
players() {
    for p in $(pgrep -x NFSMW 2>/dev/null); do
        case "$ours" in *" $p "*) continue;; esac
        path=$(ps -p "$p" -o comm= 2>/dev/null) || continue
        case "$path" in *"$game") echo "$p $path";; esac
    done
}
found=$(players)
[ -n "$found" ] || exit 1
echo "$found"
