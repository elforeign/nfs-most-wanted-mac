#!/bin/sh
# owner_hold.sh [label] - block while the player's own game runs, then stay quiet a grace period before returning, so
# unattended suites and GPU checks never share the GPU with a play session. Called before every offline test
                                                                     
# Why per test and with a grace: a hold only before a 20-minute suite let it start 5 s after one play session ended;
# the player relaunched 2.5 min later and every GPU pass in the game ran 3-5x slower for the rest of the suite.
# The player's game is an app-bundle executable whose parent is launchd (started from Finder/Dock/open). Games that
# tests or guarded runs start have another parent, so a suite never waits on its own launches.
# Env: OWNER_HOLD=0 skips; OWNER_HOLD_GAME (path suffix, default /NFSMW.app/Contents/MacOS/NFSMW);
# OWNER_HOLD_POLL (s, default 30); OWNER_HOLD_GRACE (s after the game exits, default 300).
# Proof: owner_hold_test.py (fake orphaned game -> waits, logs; gone -> returns; --broken skips the check and fails).
[ "${OWNER_HOLD:-1}" = 1 ] || exit 0
game=${OWNER_HOLD_GAME:-/NFSMW.app/Contents/MacOS/NFSMW}
poll=${OWNER_HOLD_POLL:-30}; grace=${OWNER_HOLD_GRACE:-300}

running() {
    # comm holds the executable path without arguments; match the suffix literally, spaces included.
    ps -axo ppid=,comm= | awk -v game="$game" '
        { ppid = $1; sub(/^[ \t]*[0-9]+[ \t]+/, "") }
        ppid == 1 && length($0) >= length(game) && substr($0, length($0) - length(game) + 1) == game { found = 1 }
        END { exit !found }'
}

waited=0
while running; do
    [ $waited = 1 ] || echo "owner_hold: player's game running, ${1:-check} waits ($(date +%H:%M:%S))" >&2
    waited=1
    while running; do sleep "$poll"; done
    # Quiet period after the game exits; a relaunch inside it keeps holding.
    q=0; while [ $q -lt "$grace" ] && ! running; do sleep "$poll"; q=$((q + poll)); done
done
[ $waited = 0 ] || echo "owner_hold: ${1:-check} resumes ($(date +%H:%M:%S))" >&2
exit 0
