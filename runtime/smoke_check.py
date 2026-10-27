# triggers (I4, 2026-10-22): pass = "triggers PASS(analog, simultaneous, release)" (gate 9b4b460); broken (the old
# pressure zeroing restored in pad_apply_layout) = "triggers FAIL: physical 0.00/1.00: DI 32768/32768, gas/brake 0/0, want 1.00/0.00".
# Launch reopen (0g): 7 offline fixtures passed: on/off accepted; extra open, interim PCM/stereo, duplicate result,
# first-stream mismatch and lost parked reuse rejected. Full game smoke remains a separate game check.
"""smoke_check.py <diag dir> - the checks on runtime/smoke.sh's launch (runtime/README.md, "Audio and pad smoke").

Prints "SMOKE pad PASS|FAIL: ...", "SMOKE audio PASS|FAIL: ..." and a last "SUMMARY ..." line (gate.sh's line); exit 1
if a check fails. Reads events.log (dinput / pad / scripted pad / dsound lines) and fake_tv.log (media.cpp FakeRx).

pad: the simulated DualSense connected after the game's gamepad enumeration (the hot-plug case), then held Cross raised
  the car's speed (PlayStation binding set), and after the switch to an Xbox pad held A raised it again (Xbox binding
  set, PS2 layout). Limits: Cross >= 15 m/s at release and >= 10 m/s gained; A >= 5 m/s gained.
  Physical trigger pairs enter BEFORE layout: gas/brake within 0.01 of RT/LT, DI raw within 2 of
  32767.5*(1+pressure). 0/0.30 must give 0.30/0, 0.50/0.75 -> 0.75/0.50, 1/1 -> 1/1, release -> 0/0.
  RT-only 0/1 gains >= 1 m/s, then native DualSense Cross gains >= 0.5 m/s after the layout/type swaps.
  Old code fails "physical 0.00/0.30": it digitizes gas to 1 and clears DI pressures (or combined Z cancels pairs).
  These are expected values; measured positive/negative I4 game runs remain a separate game check.
audio: the first DirectSound device chose Dolby TrueHD automatically on the fake HDMI TV; the TV saw the initial output plus
  exactly one intentional launch reopen (one output with the switch off); every later device still kept its output; the stream it received had no
  bad bursts, decode or timing errors, and decoded bit-exact to what the mixer wrote; it delivered >= 97 % of 48,000
  frames a second on the final stream; in the race (the last 5 seconds) FL, FR >= 0.01 RMS and C, SL, SR >= 0.001 (Snd's discrete 5.1;
  LFE is silent in this game); <= 0.1 % of samples at full scale (the mixer clamps there).
Measured (2026-10-21) and the negative runs: runtime/README.md, "Audio and pad smoke".
"""
import os
import re
import sys

PAD_GAS_MIN, PAD_GAS_GAIN, PAD_XBOX_GAIN = 15.0, 10.0, 5.0
RATE_MIN = 0.97
RMS_FRONT, RMS_REST = 0.01, 0.001
CLIP_MAX = 0.001


def read(path):
    try:
        with open(path, errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return []


def frame_of(line):
    m = re.match(r"f(\d+) ", line)
    return int(m.group(1)) if m else -1


def check_pad(ev):
    enum = next((frame_of(l) for l in ev if "dinput: gamepad enumerated" in l), None)
    enum_empty = any("dinput: gamepad enumerated (no controller yet" in l for l in ev)
    conn = next((frame_of(l) for l in ev if "pad: connected:" in l), None)
    if enum is None:
        return False, "the game never enumerated a gamepad", "pad FAIL(no gamepad)"
    if conn is None:
        return False, "the simulated pad never connected", "pad FAIL(no pad)"
    if not enum_empty:
        return False, "the pad was already there at the game's enumeration: hot-plug not tested", "pad FAIL(no hot-plug)"
    segs, ident, cur = [], "?", None
    for l in ev:
        m = re.search(r"pad: identity Controller \(([^)]*)\)", l)
        if m:
            ident = "DualSense" if "DualSense" in m.group(1) else "Xbox" if "XBOX" in m.group(1) else m.group(1)
        m = re.search(r"scripted pad:(\S+) down \(car (-?[\d.]+) m/s\)", l)
        if m:
            cur = [ident, m.group(1), float(m.group(2)), None]
        m = re.search(r"scripted pad up \(car (-?[\d.]+) m/s\)", l)
        if m and cur:
            cur[3] = float(m.group(1))
            segs.append(cur)
            cur = None
    if len(segs) < 3:
        return False, f"{len(segs)} pad presses with a release logged (want 3: Cross, A, Cross again)", "pad FAIL(presses)"
    (i1, _, d1, u1), (i2, _, d2, u2), (i3, _, d3, u3) = segs[:3]
    txt = (f"{i1} hot-plugged at f{conn} (game enumerated at f{enum}): Cross gas {d1:.1f} -> {u1:.1f} m/s; "
           f"{i2} A gas {d2:.1f} -> {u2:.1f} m/s; {i3} Cross again {d3:.1f} -> {u3:.1f} m/s")
    short = f"pad {i1} {d1:.0f}->{u1:.0f} m/s, {i2} {d2:.0f}->{u2:.0f} m/s (hot-plugged)"
    bad = []
    if i1 != "DualSense" or i2 != "Xbox" or i3 != "DualSense":
        bad.append(f"pad types {i1}/{i2}/{i3}, want DualSense/Xbox/DualSense")
    if u1 < PAD_GAS_MIN or u1 - d1 < PAD_GAS_GAIN:
        bad.append(f"Cross: want >= {PAD_GAS_MIN:.0f} m/s and +{PAD_GAS_GAIN:.0f}")
    if u2 - d2 < PAD_XBOX_GAIN:
        bad.append(f"A: want +{PAD_XBOX_GAIN:.0f} m/s")
    if u3 - d3 < 0.5:
        bad.append("Cross after swap: want +0.5 m/s")
    if bad:
        return False, txt + " (" + "; ".join(bad) + ")", "pad FAIL(" + "; ".join(bad) + ")"
    return True, txt, short


def check_triggers(ev):
    # Ordered held samples (release events between them may add extra 0/0 samples).
    cases = [(0, 1), (0, 0), (0, .20), (0, .30), (1, 0), (.50, .75), (1, 1), (0, 0)]
    samples = []
    for line in ev:
        m = re.search(r"padtriggers: sample (\d+) physical ([\d.]+)/([\d.]+) DI (\d+)/(\d+) "
                      r"gas (-?[\d.]+) brake (-?[\d.]+) native (\d+) layout (\w+) baseline (\d+)/(\d+)", line)
        if m:
            step, lt, rt, z, rz, gas, brake, native, layout, base_z, base_rz = m.groups()
            samples.append((int(step), float(lt), float(rt), int(z), int(rz), float(gas), float(brake), int(native), layout, int(base_z), int(base_rz)))
    # Each padtriggers down increments to an odd step; intervening even steps are physical releases.
    for index, (lt, rt) in enumerate(cases):
        step = 2 * index + 1
        sample = next((s for s in samples if s[0] == step), None)
        if sample is None:
            return False, f"missing physical {lt:.2f}/{rt:.2f} sample {step}", "triggers FAIL(missing sample)"
        _, got_lt, got_rt, z, rz, gas, brake, native, layout, base_z, base_rz = sample
        if (abs(got_lt - lt) > .001 or abs(got_rt - rt) > .001 or native or layout != "nonps_xbox" or
                abs(z - 32767.5 * (1 + lt)) > 2 or abs(rz - 32767.5 * (1 + rt)) > 2 or
                abs(gas - rt) > .01 or abs(brake - lt) > .01 or base_z != 32768 or base_rz != 32768):
            return False, (f"physical {lt:.2f}/{rt:.2f}: DI {z}/{rz}, gas/brake {gas:.4f}/{brake:.4f}, "
                           f"want {rt:.2f}/{lt:.2f} (+/-0.01), native {native}, layout {layout}, baseline {base_z}/{base_rz}"), "triggers FAIL(values)"
    # Check the final actual release, as well as the explicit held 0/0 pair.
    release = next((s for s in samples if s[0] == 16), None)
    if release is None or any(abs(v) > .01 for v in (release[1], release[2], release[5], release[6])) or any(abs(v - 32768) > 2 for v in release[3:5]):
        return False, "final physical release must give DI 32768/32768 and gas/brake 0/0", "triggers FAIL(release)"
    down = up = None
    for line in ev:
        m = re.search(r"scripted padtriggers:0/1:45 down \(car (-?[\d.]+) m/s\)", line)
        if m:
            down = float(m.group(1))
        m = re.search(r"scripted padtriggers up \(car (-?[\d.]+) m/s\)", line)
        if m and down is not None:
            up = float(m.group(1))
            break
    if down is None or up is None or up - down < 1:
        return False, f"RT-only acceleration {down} -> {up}, want +1 m/s", "triggers FAIL(acceleration)"
    return True, f"8 physical pairs match DI and pre-Fetch gas/brake (+/-0.01), release 0/0; RT {down:.1f}->{up:.1f} m/s", "triggers PASS(analog, simultaneous, release)"


def fields(line):
    """fake_tv.log 'sec'/'close' line -> dict of numbers (rms and peak as lists)"""
    t = line.split()
    d = {"t": float(t[t.index("t") + 1])}
    for k in ("bursts", "frames", "junk", "badtype", "decode_errors", "timing_errors", "exact", "mismatched", "clipped",
              "nonfinite"):
        d[k] = int(t[t.index(k) + 1])
    r, p = t.index("rms"), t.index("peak")
    d["rms"] = [float(x) for x in t[r + 1:p]]
    d["peak"] = [float(x) for x in t[p + 1:]]
    d["mode"] = t[2]
    return d


def check_audio(ev, tv):
    created = [l for l in ev if re.search(r"dsound: device \d+ created", l)]
    if not created:
        return False, "no DirectSound device created", "audio FAIL(no device)"
    first = created[0]
    if not ("Dolby TrueHD" in first and "(auto:" in first and "fake HDMI TV" in first):
        why = re.sub(r"^f\d+ t\d+ dsound: ", "", first)[:160]
        return False, f"the automatic choice was not Dolby TrueHD on the fake HDMI TV: {why}", "audio FAIL(auto choice)"
    kept = sum("kept the output of device" in l for l in created[1:])
    opens = [l for l in tv if l.startswith("open ")]
    all_secs = [fields(l) for l in tv if l.startswith("sec ")]
    # Receiver times restart at zero on the intentional launch reopen. Measure delivery/race on its final stream.
    final_id = opens[-1].split()[1] if opens else "?"
    secs = [fields(l) for l in tv if l.startswith(f"sec {final_id} ")]
    bad = []
    launch = [l for l in ev if "audio: launch reopen " in l]
    done = len(launch) == 1 and "launch reopen done" in launch[0] and "format truehd" in launch[0]
    # The smoke race starts with BOOT_RACE (no front end), where the reopen is not eligible and logs skipped:paused;
    # the front-end path (done after ~1.8 s, two TrueHD opens) was shown in a front-end fake-TV launch (2026-10-23).
    disabled = len(launch) == 1 and ("launch reopen skipped:disabled" in launch[0] or "launch reopen skipped:paused" in launch[0])
    expected_opens = 2 if done else 1
    if not (done or disabled):
        bad.append("missing/invalid launch reopen result")
    if len(opens) != expected_opens or any(" truehd " not in o for o in opens):
        bad.append(f"{len(opens)} outputs opened on the fake TV, want {expected_opens} TrueHD ({'; '.join(o[5:60] for o in opens[:4])})")
    if kept != len(created) - 1:
        bad.append(f"{len(created) - 1 - kept} of {len(created) - 1} later devices did not keep the output")
    if len(secs) < 8:
        return False, f"{len(secs)} seconds received on the fake TV, want >= 8", "audio FAIL(no stream)"
    mid = secs[1:-1]   # the first and last second may be partial
    tot = {k: sum(s[k] for s in all_secs) for k in ("frames", "junk", "badtype", "decode_errors", "timing_errors", "exact",
                                                 "mismatched", "nonfinite", "clipped")}
    errs = {k: tot[k] for k in ("junk", "badtype", "decode_errors", "timing_errors", "mismatched", "nonfinite") if tot[k]}
    if errs:
        bad.append("stream errors " + ", ".join(f"{k} {v}" for k, v in errs.items()))
    if secs[0]["mode"] == "truehd" and tot["exact"] == 0:
        bad.append("nothing decoded bit-exact")
    span = mid[-1]["t"] - secs[0]["t"]
    rate = sum(s["frames"] for s in mid) / span if span > 0 else 0
    if rate < RATE_MIN * 48000:
        bad.append(f"{rate:.0f} frames/s delivered, want >= {RATE_MIN * 48000:.0f}")
    race = mid[-5:]
    rms = [sum(s["rms"][k] for s in race) / len(race) for k in range(6)] if all(len(s["rms"]) == 6 for s in race) else []
    names = ["FL", "FR", "C", "LFE", "SL", "SR"]
    if not rms:
        bad.append("not 6 channels")
    else:
        lo = [names[k] for k in (0, 1) if rms[k] < RMS_FRONT] + [names[k] for k in (2, 4, 5) if rms[k] < RMS_REST]
        if lo:
            bad.append("silent in the race: " + " ".join(f"{names[k]} {rms[k]:.4f}" for k in range(6) if names[k] in lo))
    rf = sum(s["frames"] for s in race)
    clip = sum(s["clipped"] for s in race) / (rf * 6) if rf else 0
    if clip > CLIP_MAX:
        bad.append(f"{clip * 100:.2f} % of samples at full scale")
    rtxt = " ".join(f"{names[k]} {rms[k]:.3f}" for k in range(6)) if rms else "?"
    txt = (f"auto -> Dolby TrueHD on the fake HDMI TV, {len(opens)} output for {len(created)} devices; "
           f"{tot['frames']} frames received, {tot['exact']} bit-exact, {sum(errs.values())} errors, {rate:.0f} frames/s; "
           f"race rms {rtxt}; {clip * 100:.3f} % clipped")
    short = (f"audio truehd auto, {len(opens)} open/{len(created)} devices, {tot['exact']} frames bit-exact, "
             f"{rate / 1000:.1f} kHz, race rms FL {rms[0] if rms else 0:.3f} SR {rms[5] if rms else 0:.3f}")
    if bad:
        return False, txt + " (" + "; ".join(bad) + ")", "audio FAIL(" + "; ".join(bad)[:150] + ")"
    return True, txt, short


def main():
    d = sys.argv[1]
    ev = read(os.path.join(d, "events.log"))
    tv = read(os.path.join(d, "fake_tv.log"))
    ok = True
    summary = []
    for name, (good, txt, short) in (("pad", check_pad(ev)), ("triggers", check_triggers(ev)), ("audio", check_audio(ev, tv))):
        print(f"SMOKE {name} {'PASS' if good else 'FAIL'}: {txt}")
        ok &= good
        summary.append(short)
    print("SUMMARY " + "; ".join(summary))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
