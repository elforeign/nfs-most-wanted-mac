# game_slot.sh - sourced by the game launchers (run_guarded.sh, probe.sh, soak.sh): at most NFS_SLOT_MAX (default 2)
# games at once machine-wide, across every checkout and session, and a ledger line per launch. Replaces the
# "pgrep before launching" rule, which two launchers starting together could both pass.
#   slot_acquire <kind>   blocks until a slot is free (one "waiting" line on stderr, then one every 5 min). With
#                         NFS_SLOT=solo it waits until no other game runs and keeps every other launcher out (timing:
#                         soaks, A/B pairs). Gives up after NFS_SLOT_WAIT seconds (default 1800): exit 75.
#   slot_started <pid>    after the launch: the game's pid, so the count tells this launcher's game from others.
#   slot_release [rc]     frees the slot and appends the ledger line (idempotent; launchers also call it on exit).
# Games started outside the launchers (the user's NFSMW.app, runtime/boot from a terminal) count against the limit but
# can't be held off: the ledger records how many other games ran at the start and end of each launch, so a timing
# number taken next to one shows it.
# State in $NFS_RUNS (default <checkout parent>/.runs, outside every checkout): slots/<launcher pid> = "<kind> <game
# pid>", ledger.tsv = start, waited s, ran s, kind, slot mode, branch, checkout, exit, other games at start / end, diag.
NFS_RUNS=${NFS_RUNS:-$(cd .. && pwd)/.runs}
slot_dir=$NFS_RUNS/slots
slot_max=${NFS_SLOT_MAX:-2}
slot_held=

# pids of running games: runtime/boot and its copies (boot.soak, boot.play...), the bundled app
slot_games() {
    ps -A -o pid= -o comm= | awk '{ p = $1; $1 = ""; n = $0; sub(/.*\//, "", n); if (n ~ /^(boot(\..+)?|NFSMW)$/) print p }'
}
# with the mutex held: sets slot_n (slots in use, counting other games) and slot_solo (a solo run holds one)
slot_count() {
    slot_n=0; slot_solo=0; ours=" "
    for f in "$slot_dir"/*; do
        [ -f "$f" ] || continue
        h=${f##*/}
        if kill -0 "$h" 2>/dev/null && ps -p "$h" -o command= | grep -q -E 'run_guarded|probe\.sh|soak\.sh'; then
            read k g m < "$f"
            slot_n=$((slot_n + 1)); ours="$ours$g "
            [ "$m" = solo ] && slot_solo=1
        else
            rm -f "$f"                                       # its launcher is gone
        fi
    done
    slot_others=0
    for p in $(slot_games); do case "$ours" in *" $p "*) ;; *) slot_others=$((slot_others + 1));; esac; done
    slot_n=$((slot_n + slot_others))
}
slot_lock() {
    while ! mkdir "$NFS_RUNS/mutex" 2>/dev/null; do
        o=$(cat "$NFS_RUNS/mutex/pid" 2>/dev/null)
        if [ -n "$o" ] && ! kill -0 "$o" 2>/dev/null; then rm -rf "$NFS_RUNS/mutex"
        elif [ -z "$o" ] && [ $(( $(date +%s) - $(stat -f %m "$NFS_RUNS/mutex" 2>/dev/null || date +%s) )) -gt 10 ]; then rm -rf "$NFS_RUNS/mutex"
        else sleep 0.2; fi
    done
    echo $$ > "$NFS_RUNS/mutex/pid"
}
slot_unlock() { rm -rf "$NFS_RUNS/mutex"; }

slot_acquire() {
    slot_kind=$1; mode=${NFS_SLOT:-shared}
    mkdir -p "$slot_dir"
    t0=$(date +%s); said=0
    while :; do
        slot_lock
        slot_count
        if [ "$mode" = solo ]; then free=$([ "$slot_n" = 0 ] && echo 1)
        else free=$([ "$slot_solo" = 0 ] && [ "$slot_n" -lt "$slot_max" ] && echo 1); fi
        if [ -n "$free" ]; then
            echo "$slot_kind - $mode" > "$slot_dir/$$"
            slot_unlock
            break
        fi
        slot_unlock
        now=$(date +%s)
        if [ $((now - t0)) -ge "${NFS_SLOT_WAIT:-1800}" ]; then
            echo "game_slot: no game slot after $((now - t0)) s ($slot_n in use$([ "$slot_solo" = 1 ] && echo ", a solo run")): giving up" >&2
            exit 75
        fi
        if [ $((now - said)) -ge 300 ]; then
            echo "game_slot: waiting for a $mode game slot ($slot_n of $slot_max in use$([ "$slot_solo" = 1 ] && echo ", a solo run"))" >&2
            said=$now
        fi
        sleep 2
    done
    slot_held=1; slot_mode=$mode; slot_waited=$(( $(date +%s) - t0 )); slot_t1=$(date +%s); slot_start=$(date '+%Y-%m-%dT%H:%M:%S')
    slot_others0=$slot_others
}
slot_started() { [ -n "$slot_held" ] && echo "$slot_kind $1 $slot_mode" > "$slot_dir/$$"; }
slot_release() {
    [ -n "$slot_held" ] || return 0
    slot_held=
    rm -f "$slot_dir/$$"
    slot_lock; slot_count; slot_unlock
    branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo -)
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$slot_start" "$slot_waited" $(( $(date +%s) - slot_t1 )) "$slot_kind" \
        "$slot_mode" "$branch" "${PWD##*/}" "${1:--}" "$slot_others0" "$slot_others" "${slot_note:--}" >> "$NFS_RUNS/ledger.tsv"
}
