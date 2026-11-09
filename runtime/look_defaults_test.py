"""Shipped defaults of three look switches and built-in sign layers: Particles Physical,
Headlamp glass Clear, Traffic detail On. Unset (or empty) switch = the new default; an explicit value
(a saved Retail / Off) still wins.
Checks per switch: switches.tsv column 3, the F10 row's .def, and the production selector compiled and
run with the switch unset, empty, set to the other value and set to the default value.
Production: 0 failures / 12 selector cases + 0 / 6 table cases. Old-defaults control (selectors reverted
to "only an explicit value turns it on/Clear/Physical", rows .def back): 6 / 12 selector cases (unset and
empty fail on each of the three) and 6 / 6 table cases.
Sign-layer controls: 1/8 failures per case for missing bias or ground exclusion,
0/8 for production, each under four obsolete switch values.
Control: --old. Run: tools/.venv/bin/python -I runtime/look_defaults_test.py   (from the checkout root)
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

RT = Path(__file__).resolve().parent
# switch, file holding the Setting row, row anchor, selector source, extraction, harness prelude, default, other
FEATURES = [
    ('NFS_PARTICLES', 'physical', 'retail', 'settings_port.c', 'graphics.particles', 'particles_physical.c',
     r'int particles_physical_enabled\(void\) \{.*?\n\}',
     '#include <stdlib.h>\n#include <string.h>\nstatic int diag_on;\nstatic int postfx_look_variant(const char *v) {(void)v; return 0;}\nstatic int physical = -1;\n',
     'return particles_physical_enabled();'),
    ('NFS_HEADLAMP_GLASS', 'clear', 'retail', 'settings_port.c', 'graphics.headlamp_glass', 'materials.m',
     r'static int headlamp_glass_on\(void\) \{.*?\n\}',
     '#include <stdlib.h>\n#include <string.h>\nstatic int headlamp_glass = -1;\n', 'return headlamp_glass_on();'),
    ('NFS_TRAFFIC_DETAIL', 'on', 'off', 'settings_port.c', 'graphics.traffic_detail', 'traffic_detail.c',
     r'int traffic_detail_on\(void\) \{.*?\n\}',
     '#include <stdlib.h>\n#include <string.h>\nstatic int enabled = -1;\n', 'return traffic_detail_on();'),
]


def tsv_default(name, text):
    for line in text.splitlines():
        cols = line.split('\t')
        if cols[0] == name:
            return cols[2]
    return None


def row_default(src, ident, old=False):
    """Index of the row's .def and the spelling it selects, read from its .values array."""
    row = re.search(r'Setting\s+\w+\s*=\s*\{[^;]*?\.id\s*=\s*"%s".*?\n?[^\n]*?\};' % re.escape(ident), src, re.S)[0]
    d = int(re.search(r'\.def\s*=\s*(\d+)', row)[1])
    if old:
        d = 0         # control: the rows as they were before the decision
    v = re.search(r'\.values\s*=\s*(\w+|\(const char \*const\[\]\)\{[^}]*\})', row)[1]
    if not v.startswith('('):
        v = re.search(r'\b%s\[\]\s*=\s*\{([^}]*)\}' % v, src)[1]
        v = '{' + v + '}'
    names = re.findall(r'"([^"]*)"', v)
    return names[d]


def run_selector(tmp, label, body, prelude, call, env_name, want_default, want_other, mutate):
    src = prelude + body + '\nint main(void) {%s}\n' % call
    if mutate:
        assert '!(e && *e) || ' in src, label
        src = src.replace('!(e && *e) || ', 'e && ')
    c = tmp / (label + ('_old' if mutate else '') + '.c')
    c.write_text(src)
    exe = tmp / c.stem
    subprocess.run(['clang', '-O0', '-w', str(c), '-o', str(exe)], check=True)
    fails = 0
    default_on = want_default
    cases = ((None, 1), ('', 1), (want_other, 0), (default_on, 1))
    for value, want in cases:
        env = {k: v for k, v in os.environ.items() if k != env_name}
        if value is not None:
            env[env_name] = value
        r = subprocess.run([str(exe)], env=env, capture_output=True)
        fails += r.returncode != want
    return fails, len(cases)


def sign_layers(tmp):
    source = (RT / 'decal_depth.m').read_text()
    source = re.sub(r'^#include .*\n', '', source, flags=re.M)
    assert 'NFS_DECAL_DEPTH' not in source
    assert 'NFS_DECAL_DEPTH' not in (RT / 'switches.tsv').read_text()
    render = (RT / 'render.m').read_text()
    assert 'eb.decal_back = decal_depth_tagged(dev.tex[0]);' in render
    assert 'decal_depth_ground(dev.tex[0])' in render
    prelude = r'''#include <stdint.h>
#include <string.h>
static unsigned char memory[128];
static unsigned char *G = memory;
#define M32(at) (*(uint32_t *)(G + (at)))
typedef struct {int decal_back, ground_decal; uint32_t guest;} Obj;
static Obj texture;
static Obj *obj(uint32_t id) { return id == 1 ? &texture : 0; }
static int diag_on;
#define diag_event(...) ((void)0)
'''
    main = r'''int main(void) {
    int failures = 0;
    M32(64 + 0x18) = 1;
    uint32_t hashes[] = {0x452ECD84u, 0xE43D65FBu, 0x12345678u};
    for (unsigned i = 0; i < 3; ++i) {
        M32(0x24) = hashes[i];
        decal_depth_texture(0, 64);
        failures += decal_depth_tagged(1) != (i == 0);
        failures += decal_depth_ground(1) != (i == 1);
    }
    failures += decal_depth_tagged(0) || decal_depth_ground(0);
    float bias = 0.5f, slope = 2;
    decal_depth_offset(&bias, &slope);
    failures += bias != 0.5f + 16 / 16777216.0f || slope != 3;
    return failures;
}
'''
    for label, body, want in (('built-in', source, 0),
            ('missing-bias', source.replace('*bias += BACK_UNITS / 16777216.0f;', '*bias += 0;'), 1),
            ('missing-ground', source.replace('hash == kGroundHash', '0'), 1)):
        c = tmp / 'sign.c'; exe = tmp / 'sign'
        c.write_text(prelude + body + main)
        subprocess.run(['clang', '-Wall', '-Wextra', '-Wno-unused-variable', str(c), '-o', str(exe)], check=True, capture_output=True)
        for value in (None, '', 'off', 'on'):
            env = dict(os.environ); env.pop('NFS_DECAL_DEPTH', None)
            if value is not None: env['NFS_DECAL_DEPTH'] = value
            result = subprocess.run([str(exe)], env=env)
            assert result.returncode == want, (label, value, result.returncode)
        print('sign layers %s: 4 cases, failures per case=%d' % (label, want))


def main():
    tsv = (RT / 'switches.tsv').read_text()
    old_mode = '--old' in sys.argv
    sel_fail = sel_n = tab_fail = tab_n = 0
    with tempfile.TemporaryDirectory(prefix='look-defaults-') as td:
        tmp = Path(td)
        sign_layers(tmp)
        for name, default, other, row_file, ident, sel_file, rx, prelude, call in FEATURES:
            body = re.search(rx, (RT / sel_file).read_text(), re.S)[0]
            label = name.lower()
            f, n = run_selector(tmp, label, body, prelude, call, name, default, other, old_mode)
            sel_fail += f; sel_n += n
            tsv_got = tsv_default(name, tsv)
            if old_mode:
                tsv_got = other      # control: switches.tsv column 3 as it was before the decision
            for what, got in (('switches.tsv', tsv_got),
                              ('F10 row .def', row_default((RT / row_file).read_text(), ident, old_mode))):
                tab_n += 1
                bad = got is None or got.lower() != default
                tab_fail += bad
                if bad:
                    print('FAIL %s %s: %r, want %r' % (name, what, got, default))
    print('look_defaults: selectors %d/%d failures, tables %d/%d failures' % (sel_fail, sel_n, tab_fail, tab_n))
    return 1 if (sel_fail or tab_fail) else 0


if __name__ == '__main__':
    sys.exit(main())
