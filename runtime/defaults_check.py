#!/usr/bin/env python3
# defaults_check.py - the shipped default of every look/sound setting, read from
# the Setting rows in the sources. Run: tools/.venv/bin/python -I runtime/defaults_check.py   (from the checkout root)
# Result: PASS. Broken run (settings_port.c graphics.water without ".def = 1"): FAIL "graphics.water default pc, want x360".
# Shadow fade: production 21 cases/0 failures; reverted unset-Off FAIL 1/21;
# ignored off FAIL 1/21; removed fit guard FAIL 6/21; registry On PASS, Off FAIL 1.
import glob, re, sys
from pathlib import Path
import subprocess
import tempfile

def shadow_fade_defaults():
    source = Path('runtime/x360.m').read_text()
    fragment = re.search(r'static int shadow_x360_fade = -1;\nint x360_shadow_edge\(void\) \{.*?\n\}', source, re.S)[0]
    prelude = '#include <stdlib.h>\n#include <stdio.h>\n#include <string.h>\nstatic int fit;\nstatic void shadow_options(void) {}\n'
    main = r'''
int main(void) {
    const char *values[] = {NULL, "off", "0", "on", "1", "", "invalid"};
    const int enabled[] = {1, 0, 0, 1, 1, 0, 0};
    int bad = 0, cases = 0;
    for (fit = 0; fit <= 2; ++fit) {
        for (int i = 0; i < 7; ++i) {
            if (values[i]) setenv("NFS_SHADOW_X360_FADE", values[i], 1);
            else unsetenv("NFS_SHADOW_X360_FADE");
            shadow_x360_fade = -1;
            bad += x360_shadow_edge() != (fit == 2 && enabled[i]);
            ++cases;
        }
    }
    printf("shadow fade defaults: cases=%d failures=%d\n", cases, bad);
    return bad ? 1 : 0;
}
'''
    mutations = [
        ('production', fragment, 0, 0),
        ('old unset-Off', fragment.replace('!e || !strcmp(e, "on") || !strcmp(e, "1")',
                                            'e && (!strcmp(e, "on") || !strcmp(e, "1"))'), 1, 1),
        ('ignored off', fragment.replace('!e || !strcmp(e, "on")', '!e || !strcmp(e, "off") || !strcmp(e, "on")'), 1, 1),
        ('removed fit guard', fragment.replace('return fit == 2 && shadow_x360_fade;',
                                              'return shadow_x360_fade;'), 1, 6),
    ]
    with tempfile.TemporaryDirectory(prefix='shadow-fade-defaults-') as tmp:
        path, exe = Path(tmp) / 'defaults.c', Path(tmp) / 'defaults'
        for name, text, expected_rc, failures in mutations:
            path.write_text(prelude + text + main)
            built = subprocess.run(['clang', '-Wall', '-Werror', str(path), '-o', str(exe)], capture_output=True, text=True)
            assert built.returncode == 0, built.stderr[-2000:]
            checked = subprocess.run([str(exe)], capture_output=True, text=True)
            print(name + ': ' + checked.stdout.strip())
            if checked.returncode != expected_rc or checked.stdout.strip() != f'shadow fade defaults: cases=21 failures={failures}':
                return 1
    def registry_failure(text):
        row = next(line.split('\t') for line in text.splitlines() if line.startswith('NFS_SHADOW_X360_FADE\t'))
        return int(row[2] != 'on')
    registry = Path('runtime/switches.tsv').read_text()
    broken = registry.replace('NFS_SHADOW_X360_FADE\topt\ton\t', 'NFS_SHADOW_X360_FADE\topt\toff\t')
    assert registry_failure(broken) == 1
    failure = registry_failure(registry)
    print(f'shadow fade registry: failures={failure}; old-default control=1')
    return failure

# Scene prewarm: unset/0/1 = 1/0/1 (3/3); old unset-Off and ignored-0 controls each fail 1/3.
def scene_prewarm_defaults():
    source = Path('runtime/pipeline_cache.m').read_text()
    fragment = re.search(r'static int enabled\(void\) \{[^\n]+\}', source)[0]
    prelude = '#include <stdlib.h>\n#include <stdio.h>\n'
    main = r'''
int main(void) {
    int bad=0;
    unsetenv("NFS_PSO_PREWARM"); bad+=enabled()!=1;
    setenv("NFS_PSO_PREWARM","0",1); bad+=enabled()!=0;
    setenv("NFS_PSO_PREWARM","1",1); bad+=enabled()!=1;
    printf("scene prewarm defaults: cases=3 failures=%d\n",bad);
    return bad?1:0;
}
'''
    with tempfile.TemporaryDirectory(prefix='prewarm-defaults-') as tmp:
        path = Path(tmp) / 'defaults.c'
        exe = Path(tmp) / 'defaults'
        for name, text, expected in [
            ('production', fragment, 0),
            ('old unset-Off', fragment.replace('return !s || atoi(s);', 'return s && atoi(s);'), 1),
            ('ignored explicit 0', fragment.replace('return !s || atoi(s);', '(void)s; return 1;'), 1),
        ]:
            path.write_text(prelude + text + main)
            built = subprocess.run(['clang', '-Wall', '-Werror', str(path), '-o', str(exe)], capture_output=True, text=True)
            assert built.returncode == 0, built.stderr[-2000:]
            checked = subprocess.run([str(exe)], capture_output=True, text=True)
            print(name + ': ' + checked.stdout.strip())
            if checked.returncode != expected:
                return 1
    def registry_failure(text):
        row = next(line.split('\t') for line in text.splitlines() if line.startswith('NFS_PSO_PREWARM\t'))
        return int(row[2] != '1')
    registry = Path('runtime/switches.tsv').read_text()
    broken_registry = registry.replace('NFS_PSO_PREWARM\tinternal\t1\t', 'NFS_PSO_PREWARM\tinternal\t0\t')
    assert registry_failure(broken_registry) == 1
    failure = registry_failure(registry)
    print(f'scene prewarm registry: failures={failure}; old-default control=1')
    return failure
# value spellings by the array a row names (index 0 first); rows without .values use the lower-cased choice labels
ARR = {"kX360PcV": ["x360", "pc"], "kPcX360V": ["pc", "x360"], "kArrestV": ["pc", "x360"], "kLightDataV": ["pc", "x360"],
       "kDropsV": ["x360", "pc", "pc_before"], "kSpeedBlurV": ["off", "x360", "strong"], "kFitV": ["x360", "x360range", "pc"],
       "kEngSndV": ["retail", "realistic"]}
WANT = {i: "x360" for i in """graphics.postfx graphics.car_paint graphics.car_glass graphics.car_normals graphics.car_flake
    graphics.x360_arrest graphics.grass graphics.trees graphics.water graphics.building_glass graphics.scenery_lights graphics.parallax
    graphics.lighting_data graphics.road_reflection graphics.rain_ripples graphics.raindrops graphics.speed_blur
    graphics.shadow_fit audio.x360_copspeech audio.x360_nisaudio audio.x360_reverb""".split()}
WANT.update({"graphics.particles": "physical",   # was retail; inline .values array
             "graphics.tone_curve": "pc", "audio.x360_mix": "on", "audio.x360_music": "on", "audio.engine_sound": "realistic"})
rows = {}
for f in glob.glob("runtime/*.c") + glob.glob("runtime/*.m"):
    if f.endswith("_test.c"): continue
    s = open(f).read()
    for m in re.finditer(r'Setting\s+\w+\s*=\s*\{(.*?)\};', s, re.S):
        b = m.group(1)
        i = re.search(r'\.id\s*=\s*"([^"]+)"', b)
        if not i or i.group(1) not in WANT: continue
        d = re.search(r'\.def\s*=\s*(\d+)', b); v = re.search(r'\.values\s*=\s*(\w+)', b)
        iv = re.search(r'\.values\s*=\s*\(const char \*const\[\]\)\{([^}]*)\}', b)
        ch = re.search(r'\.choices\s*=\s*(\w+)', b)
        idx = int(d.group(1)) if d else 0
        if iv: sp = re.findall(r'"([^"]*)"', iv.group(1))
        elif f.endswith("x360audio.c") and v: sp = ["off", "on"]   # that file's kPcX360V is saved off / on
        elif v: sp = ARR[v.group(1)]
        elif i.group(1).startswith("audio.x360_") : sp = ["pc", "x360"] if "reverb" in i.group(1) or "nis" in i.group(1) or "cop" in i.group(1) else ["off", "on"]
        else: sp = ["x360", "pc"]   # kRefl rows use kX360PcV through .values; fallback never reached for the table above
        rows[i.group(1)] = sp[idx]
bad = scene_prewarm_defaults() + shadow_fade_defaults()
for k, w in sorted(WANT.items()):
    if rows.get(k) != w: print("FAIL %s default %s, want %s" % (k, rows.get(k), w)); bad += 1
# the code that reads the switch when no pref is set must agree (materials.m & co fall back to X360 unless the switch says pc)
print("defaults_check: %s (%d rows, %d wrong)" % ("FAIL" if bad else "PASS", len(WANT), bad)); sys.exit(1 if bad else 0)
