#!/usr/bin/env python3
"""Compact report of a session log folder (report.m: session.txt + perf.tsv), at most ~40 lines.

  $TOOLS_PY -I runtime/session_report.py [session folder]

Default: the newest folder under ~/Library/Logs/NFSMW-port/sessions. Prints the setup, a frame-time table per game-flow
state (the last SUMMARY lines of session.txt; rebuilt from perf.tsv when the run ended before the first one), frames
over budget, the worst 10 hitches with cause and place, GPU-bound vs CPU-bound seconds, thermal / power / display changes,
and "GPU slow while draws unchanged" stretches: race seconds whose gpu_mean is over 2x the median of the seconds with a
similar draw count (+-25%, at least 10 of them, else the race median), merged when under 6 s apart and listed from 4 s long,
with the perf.tsv columns that tell the causes apart (thermal 0-3, low_power, app_active, occluded, fullscreen, gpu_wall =
queue-to-end ms of the command buffers against gpu_mean; logs from before build 1592 have none of those columns).
GPU telemetry adds other-client ms/s, totals of the recorded top clients, and MHz or P-states against the race median.
Verdicts: contention when other-client ms/s is at least 30% of (gpu_mean - baseline) * fps; otherwise clock when
numeric MHz is at most 70% of the race median or the median P-state falls; otherwise undecided.
P-state ordinals are compared only with other P-states, never converted to MHz. Other labels stay unknown.
"""
import math, os, re, sys


def newest():
    root = os.path.expanduser("~/Library/Logs/NFSMW-port/sessions")
    dirs = sorted(d for d in os.listdir(root) if d[:1].isdigit()) if os.path.isdir(root) else []
    if not dirs:
        sys.exit("no session folders in " + root)
    return os.path.join(root, dirs[-1])


def kv(line):
    return dict(re.findall(r"(\w+)=(\S+)", line))


def median(v):
    v = sorted(v)
    return v[len(v) // 2] if v else 0.0


def gpu_number(row, key):
    """Optional telemetry: '-' and P-state labels are unknown, never zero MHz."""
    try:
        value = float(row.get(key, "-"))
        return value if math.isfinite(value) and value >= 0 else None
    except (ValueError, TypeError):
        return None


def gpu_pstate(row):
    """IOReport's active Pn states: higher ordinals indicate higher GPU clocks."""
    match = re.fullmatch(r"state=P(\d+)", row.get("gpu_mhz", ""))
    return int(match.group(1)) if match else None


def gpu_cause(stretch, race_mhz, f, race_state=None):
    others, clocks, states, rises, clients = [], [], [], [], {}
    for row, base in stretch:
        other = gpu_number(row, "gpu_other_ms")
        if other is not None:
            others.append(other)
            # AGX time is ms/s; convert the per-frame rise with this second's FPS.
            rises.append(max(0, f(row, "gpu_mean") - base) * f(row, "fps"))
        mhz = gpu_number(row, "gpu_mhz")
        if mhz is not None and mhz > 0:
            clocks.append(mhz)
        state = gpu_pstate(row)
        if state is not None:
            states.append(state)
        for item in row.get("gpu_top", "-").split(","):
            name, sep, value = item.rpartition(":")
            ms = gpu_number({"v": value}, "v")
            if sep and name and ms is not None:
                clients[name] = clients.get(name, 0) + ms
    other = sum(others) / len(others) if others else None
    mhz = sum(clocks) / len(clocks) if clocks else None
    rise = sum(rises) / len(rises) if rises else None
    state = median(states) if states else None
    verdict = "undecided"
    if other is not None and rise and other >= 0.30 * rise:
        verdict = "contention"
    elif state is not None and race_state is not None and state < race_state:
        verdict = "GPU clock dropped (P%d -> P%d)" % (race_state, state)
    elif mhz is not None and race_mhz and mhz <= 0.70 * race_mhz:
        verdict = "clock"
    top = ",".join("%s:%.1f" % (name, ms) for name, ms in
                   sorted(clients.items(), key=lambda item: (-item[1], item[0]))[:3]) or "-"
    fmt = lambda value: "-" if value is None else "%.1f" % value
    state_text = "; gpu_state median %s vs race median %s" % (
        "-" if state is None else "P%d" % state, "-" if race_state is None else "P%d" % race_state
    ) if state is not None or race_state is not None else ""
    return "; gpu_other_ms mean %s, top other total ms %s; gpu_mhz mean %s vs race median %s%s; verdict %s" % (
        fmt(other), top, fmt(mhz), fmt(race_mhz), state_text, verdict)


def gpu_slow_stretches(rows, f):
    """Race seconds with gpu_mean > 2x the median at a similar draw count, as stretches with the system columns."""
    rs = [r for r in rows if r["state"] == "race" and f(r, "gpu_mean") > 0 and f(r, "draws") > 0]
    if len(rs) < 20:
        return
    all_med = median([f(r, "gpu_mean") for r in rs])
    slow = []
    for r in rs:
        d = f(r, "draws")
        peers = [f(q, "gpu_mean") for q in rs if abs(f(q, "draws") - d) <= 0.25 * d]
        base = median(peers) if len(peers) >= 10 else all_med
        if f(r, "gpu_mean") > 2 * base:
            slow.append((r, base))
    stretches, cur = [], []
    for r, base in slow:
        if cur and f(r, "t") - f(cur[-1][0], "t") >= 6:
            stretches.append(cur)
            cur = []
        cur.append((r, base))
    if cur:
        stretches.append(cur)
    stretches = [c for c in stretches if f(c[-1][0], "t") - f(c[0][0], "t") >= 4 or len(c) >= 4]
    print("GPU slow while draws unchanged (race, gpu_mean > 2x the median at +-25%% draws; session median %.1f ms): %s" % (
        all_med, "none" if not stretches else "%d stretch(es), %d s" % (len(stretches), sum(len(c) for c in stretches))))
    has_cols = "thermal" in rs[0]
    clocks = [v for r in rs if (v := gpu_number(r, "gpu_mhz")) is not None and v > 0]
    race_mhz = median(clocks) if clocks else None
    states = [v for r in rs if (v := gpu_pstate(r)) is not None]
    race_state = median(states) if states else None
    for c in stretches:
        g = [r for r, _ in c]
        line = "  t=%.0f-%.0f (%d s): gpu %.1f ms (x%.1f of %.1f), draws %.0f-%.0f, busy %.1f, fps %.0f" % (
            f(g[0], "t"), f(g[-1], "t"), len(g), sum(f(r, "gpu_mean") for r in g) / len(g),
            sum(f(r, "gpu_mean") / b for r, b in c) / len(c), sum(b for _, b in c) / len(c),
            min(f(r, "draws") for r in g), max(f(r, "draws") for r in g), sum(f(r, "busy_p50") for r in g) / len(g),
            sum(f(r, "fps") for r in g) / len(g))
        if has_cols:
            line += "; thermal max %d, low_power %d, app_active min %d, occluded %d s, fullscreen %d s, gpu_wall %.1f ms" % (
                max(int(r["thermal"]) for r in g), max(int(r["low_power"]) for r in g), min(int(r["app_active"]) for r in g),
                sum(int(r["occluded"]) for r in g), sum(int(r["fullscreen"]) for r in g), sum(f(r, "gpu_wall") for r in g) / len(g))
        else:
            line += "; (no thermal/low_power/active/gpu_wall columns in this log)"
        print(line + gpu_cause(c, race_mhz, f, race_state))


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else newest()
    lines = open(os.path.join(d, "session.txt"), errors="replace").read().splitlines()
    print("session", d)
    for l in lines:
        if l.startswith("setup:") and not l.endswith(":"):
            print("  " + l[7:][:150])
    nd, grab = [], False
    for l in lines:
        if l.startswith("setup: settings"):
            grab = True
        elif grab and l.startswith("  ") and "(none)" not in l:
            nd.append(re.sub(r"\s{2,}", "=", l.strip(), 1).split(" (")[0])
        else:
            grab = False
    if nd:
        print("  non-default: " + ", ".join(nd)[:300])
    rows = []
    p = os.path.join(d, "perf.tsv")
    if os.path.exists(p):
        h, *body = open(p, errors="replace").read().splitlines() or [""]  # a label cut mid-UTF-8 must not abort the report
        h = h.split("\t")
        rows = [dict(zip(h, r.split("\t"))) for r in body if r.count("\t") == len(h) - 1] if len(h) > 1 else []

    def f(r, k):
        return float(r[k])

    print("duration %.0f s, %d perf lines, last frame %s" % (f(rows[-1], "t") if rows else 0, len(rows), rows[-1]["frame"] if rows else "-"))
    if rows and "present_8_3" in rows[0]:
        keys = ("present_8_3", "present_16_7", "present_25", "present_other", "present_dropped")
        counts = [sum(int(r[k]) for r in rows) for k in keys]
        limits = ",".join(sorted({r["frame_limit"] for r in rows}, key=int))
        print("presentation: limits %s (0=Off); shown intervals 8.3/16.7/25/other ms, dropped: %s" % (
            limits, "/".join(map(str, counts))))
        rs_present = [r for r in rows if r["state"] == "race"]
        for limit in sorted({r["frame_limit"] for r in rs_present}, key=int):
            group = [r for r in rs_present if r["frame_limit"] == limit]
            totals = [sum(int(r[k]) for r in group) for k in keys]
            print("  race limit %s: %s across %d perf rows" % (limit, "/".join(map(str, totals)), len(group)))
    summ = {}
    for l in lines:
        if l.startswith("SUMMARY "):
            k = kv(l)
            summ[k["state"]] = k
    print("state       frames   p50    p95    p99    max   over%  hitches  busy p50/p99  gpu p50/p99   (ms)")
    for st, k in summ.items():
        print("%-10s %7s %6s %6s %6s %6s %6s %8s  %5s/%-6s %5s/%-6s" % (st, k["frames"], k["p50"], k["p95"], k["p99"], k["max"], k["over"].rstrip("%"),
              k["hitches"], k["busy_p50"], k["busy_p99"], k["gpu_p50"], k["gpu_p99"]))
    last = max((float(k["t"]) for k in summ.values()), default=-1)
    late = sorted({r["state"] for r in rows if r["state"] not in summ or f(r, "t") > last + 1})
    if late:   # states with no SUMMARY, or seconds after the last one (a run killed between summaries)
        print("(after the last SUMMARY at t=%.0f: per-second figures from perf.tsv)" % last)
        for st in late:
            rs = [r for r in rows if r["state"] == st and f(r, "t") > last + 1] or [r for r in rows if r["state"] == st]
            print("%-10s %5ds fps mean %.1f min %.1f, worst p99 %.1f max %.1f ms, over-budget frames %d" % (st, len(rs), sum(f(r, "fps") for r in rs) / len(rs),
                  min(f(r, "fps") for r in rs), max(f(r, "p99") for r in rs), max(f(r, "max") for r in rs), sum(int(r["over"]) for r in rs)))
    causes = {}
    for k in summ.values():
        for c in k["causes"].split(","):
            n, v = c.split(":")
            causes[n] = causes.get(n, 0) + int(v)
    print("hitches by first cause: " + (", ".join("%s %d" % (n, v) for n, v in causes.items() if v) or "none"))
    hs = []
    for l in lines:
        if " HITCH " in l:
            k = kv(l)
            m = re.match(r"\s*([\d.]+) s\s+f(\d+)", l)
            k["t"], k["f"] = m.group(1), m.group(2)
            hs.append(k)
    more = sum(int(m.group(1)) for l in lines for m in [re.search(r"\((\d+) more hitches", l)] if m)
    print("worst hitches (%d listed, %d more not listed):" % (len(hs), more))
    for k in sorted(hs, key=lambda k: -float(k["ms"]))[:10]:
        print("  t=%7ss f%-6s %6sms busy %s gpu %s sleep %s gil %s drw %s ticks %s %s [%s] +sh %s +pipe %s +defer %s at %s" % (
            k["t"], k["f"], k["ms"], k["busy"], k["gpu"], k["sleep"], k["gil"], k["drawable"], k["ticks"], k["state"], k["causes"],
            k["shaders"], k["pipelines"], k["deferred"], k["pos"]))
    rs = [r for r in rows if r["state"] == "race"]
    if rs:
        gb = sum(1 for r in rs if f(r, "idle_drawable") + f(r, "idle_gpu") > 0.25 * f(r, "p50"))
        print("race seconds waiting on the GPU (drawable+gpu wait > 25%% of a frame): %d of %d (%.0f%%); mean busy %.2f ms, gpu %.2f ms, sleep %.2f ms" % (
            gb, len(rs), 100.0 * gb / len(rs), sum(f(r, "busy_p50") for r in rs) / len(rs), sum(f(r, "gpu_mean") for r in rs) / len(rs),
            sum(f(r, "idle_sleep") for r in rs) / len(rs)))
        print("rss %.0f -> %.0f MB (race)" % (f(rs[0], "rss_mb"), f(rs[-1], "rss_mb")))
        am = [r for r in rs if r.get("dip_sr")]
        if am:  # the device mix per channel (perf.tsv rms_*/dip_*/clip; older logs have none)
            chs = ["fl", "fr", "c", "lfe", "sl", "sr"]
            pw = {c: sum(10 ** (f(r, "rms_" + c) / 10) for r in am) for c in chs}
            tot = sum(pw.values()) or 1
            cut = [r for r in am if min(f(r, "dip_sl"), f(r, "dip_sr")) < -50 and max(f(r, "rms_fl"), f(r, "rms_fr")) > -25]
            print("race mix: power share %s; mean RMS dBFS %s; full-scale samples %d in %d s; rear 50 ms dips < -50 dBFS "
                  "while the fronts play (> -25): %d s%s" % (
                      " ".join("%s %.0f%%" % (c.upper(), 100 * pw[c] / tot) for c in chs),
                      " ".join("%s %.1f" % (c.upper(), 10 * math.log10(pw[c] / len(am)) if pw[c] > 0 else -120) for c in chs),
                      sum(int(r["clip"]) for r in am), sum(int(r["clip"]) > 0 for r in am), len(cut),
                      (" at t=" + ",".join("%.0f" % f(r, "t") for r in cut[:8])) if cut else ""))
    gpu_slow_stretches(rows, f)
    ev =[re.sub(r"\s+", " ", l.strip())[:130] for l in lines if re.search(r" s +f\d+ +(thermal|power|display|audio: .*underrun|stall)", l)]
    print("changes (thermal / power / display / underruns / stalls): " + ("none" if not ev else ""))
    for e in ev[:8]:
        print("  " + e)


if __name__ == "__main__":
    main()
