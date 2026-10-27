#!/usr/bin/env python3
# defaults_check.py - the shipped default of every look/sound setting, read from
# the Setting rows in the sources. Run: tools/.venv/bin/python -I runtime/defaults_check.py   (from the checkout root)
# Result: PASS. Broken run (settings_port.c graphics.water without ".def = 1"): FAIL "graphics.water default pc, want x360".
import glob, re, sys
# value spellings by the array a row names (index 0 first); rows without .values use the lower-cased choice labels
ARR = {"kX360PcV": ["x360", "pc"], "kPcX360V": ["pc", "x360"], "kArrestV": ["pc", "x360"], "kLightDataV": ["pc", "x360"],
       "kDropsV": ["x360", "pc", "pc_before"], "kSpeedBlurV": ["off", "x360", "strong"], "kFitV": ["x360", "x360range", "pc"],
       "kEngSndV": ["retail", "realistic"]}
WANT = {i: "x360" for i in """graphics.postfx graphics.car_paint graphics.car_glass graphics.car_normals graphics.car_flake
    graphics.x360_arrest graphics.grass graphics.trees graphics.water graphics.building_glass graphics.scenery_lights graphics.parallax
    graphics.lighting_data graphics.road_reflection graphics.rain_ripples graphics.raindrops graphics.particles graphics.speed_blur
    graphics.shadow_fit audio.x360_copspeech audio.x360_nisaudio audio.x360_reverb""".split()}
WANT.update({"graphics.tone_curve": "pc", "audio.x360_mix": "on", "audio.x360_music": "on", "audio.engine_sound": "realistic"})
rows = {}
for f in glob.glob("runtime/*.c") + glob.glob("runtime/*.m"):
    if f.endswith("_test.c"): continue
    s = open(f).read()
    for m in re.finditer(r'Setting\s+\w+\s*=\s*\{(.*?)\};', s, re.S):
        b = m.group(1)
        i = re.search(r'\.id\s*=\s*"([^"]+)"', b)
        if not i or i.group(1) not in WANT: continue
        d = re.search(r'\.def\s*=\s*(\d+)', b); v = re.search(r'\.values\s*=\s*(\w+)', b)
        ch = re.search(r'\.choices\s*=\s*(\w+)', b)
        idx = int(d.group(1)) if d else 0
        if f.endswith("x360audio.c") and v: sp = ["off", "on"]   # that file's kPcX360V is saved off / on
        elif v: sp = ARR[v.group(1)]
        elif i.group(1).startswith("audio.x360_") : sp = ["pc", "x360"] if "reverb" in i.group(1) or "nis" in i.group(1) or "cop" in i.group(1) else ["off", "on"]
        else: sp = ["x360", "pc"]   # kRefl rows use kX360PcV through .values; fallback never reached for the table above
        rows[i.group(1)] = sp[idx]
bad = 0
for k, w in sorted(WANT.items()):
    if rows.get(k) != w: print("FAIL %s default %s, want %s" % (k, rows.get(k), w)); bad += 1
# the code that reads the switch when no pref is set must agree (materials.m & co fall back to X360 unless the switch says pc)
print("defaults_check: %s (%d rows, %d wrong)" % ("FAIL" if bad else "PASS", len(WANT), bad)); sys.exit(1 if bad else 0)
