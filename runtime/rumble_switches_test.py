#!/usr/bin/env python3
"""F10 Controls > Rumble on cobbles and Prop hits rumble by impact: the production feedback.c model; no pad, no game.

Rumble on cobbles (controls.rumble_surfaces, NFS_RUMBLE_SURFACES, default On): cobble, stone and railroad add the
port's texture (light motor 8 -> 16 Hz flutter, faint heavy pulse, both rising with speed); asphalt and concrete do
not; =x360 (or Off in the menu) leaves the console's curves only.
Prop hits rumble by impact (controls.rumble_props, NFS_RUMBLE_PROPS, default On): a non-vehicle contact rumbles by
how much it changed my velocity (minimum 0.25 for 0.10 s), so a cardboard box at 50 m/s is a nudge, not the X360's
full-strength 1 s speed ramp; =x360 (or Off) restores the console's formula. Vehicle hits are the same either way.

The harness includes the real feedback.c with a fake guest (surface collections, the simable table, velocities and
IVehicle queries answered by call_guest) and reads the model's oscillators, collision envelope and the motor levels
it hands rumble.m (feedback_levels, sampled every 16 ms over 1 s). X360E_FB_* come from a fixed test header (the
real one is generated from the player's default.xex); every expected number is computed from those macros.
The F10 rows' wiring (switch name, default, live apply) is read from settings_port.c.
Broken controls: one-line removals in feedback.c that must fail their own feature. Exit 0 pass, 1 fail, 3 skip.
Slot-zero byte goldens from the original singleton: defaults 3a46a848f422aee1, x360 22d241e51eb8a84c.
Proof: production 63/63 checks pass; all 6 broken controls fail their feature checks.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]

ENGINE = '''// test values, not the console's: the checks are written in terms of these macros
#define X360E_FB_SHIFT_GOOD 0.5f
#define X360E_FB_SHIFT_PERFECT 0.8f
#define X360E_FB_SHIFT_TIME 0.2f
#define X360E_FB_NOS_TIME 0.5f
#define X360E_FB_NOS_END 0.2f
#define X360E_FB_DAMAGE_END 0.3f
#define X360E_FB_GEAR_TIME 0.1f
#define X360E_FB_GEAR_LEVEL 0.4f
#define X360E_FB_HIT_SOFT 3.0f
#define X360E_FB_HIT_SLOPE 0.1f
#define X360E_FB_WORLD_SLOPE 0.05f
#define X360E_FB_GROUND_SLOPE 0.1f
#define X360E_FB_RAMP_LO 10.0f
#define X360E_FB_RAMP_HI 40.0f
#define X360E_FB_SAME_DOT 0.9f
'''

HARNESS = r'''
#define _DEFAULT_SOURCE
#include <stdarg.h>
#include <sys/mman.h>
#include FEEDBACK_SOURCE

uint8_t *G;
int diag_on;
const char *diag_dir = "/nonexistent";
const char *game_dir = "/nonexistent";
static uint64_t clock_ns = 1000000000ull;
uint64_t host_ticks(void) { return clock_ns; }
void rumble_stop_slot(unsigned slot) { (void)slot; rumble_stop(); }
void rumble_stop(void) {}
void rumble_suppress(int on) { (void)on; }
void diag_event(const char *fmt, ...) { (void)fmt; }
void diag_event_ring(const char *fmt, ...) { (void)fmt; }
static uint32_t heap = 0x01000000u;
uint32_t galloc(uint32_t n) { uint32_t p = heap; heap += (n + 15) & ~15u; memset(G + p, 0, n); return p; }
uint32_t host_fn(HostFn fn, const char *name) { (void)fn; (void)name; abort(); }
void padfx_suppress(unsigned reason, int on) { (void)reason; (void)on; }
void padfx_stop(void) {}
void padfx_poll(void) {}
int padfx_road_wanted(void) { return 0; }
int padfx_wanted(void) { return 0; }
void padfx_game_tick(Cpu *c, uint32_t player) { (void)c; (void)player; abort(); }
void f_006EE490_orig(Cpu *c) { (void)c; abort(); }
void f_006EE400_orig(Cpu *c) { (void)c; abort(); }

// ---- the fake guest
#define SURF_CLASS_OBJ 0x00200000u
static const uint32_t kCobble = 0x00300010u, kStone = 0x00300020u, kRail = 0x00300030u;   // collections
static const uint32_t kAsphalt = 0x00300040u, kConcrete = 0x00300050u;
static uint32_t surface(uint32_t coll) { uint32_t s = galloc(64); M32(s + 4) = coll; return s; }
#define VEL_FN 0x00AA0000u
#define NODE(h) (0x00210000u + 16 * (h))
#define SIM(h) (0x00220000u + 0x100 * (h))
static float vel[8][3];
static int is_vehicle[8];
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *a) {
    (void)n;
    if (fn == 0x00455960u) {                                     // Attrib::Class::GetCollection(key)
        return a[0] == kRoughKeys[0] ? kCobble : a[0] == kRoughKeys[1] ? kStone : a[0] == kRoughKeys[2] ? kRail : 0;
    }
    if (fn == FN_GET_ATTRIBUTE) return 0;                         // no game curves: only the port's texture is measured
    if (fn == FN_KEYED_SEARCH) return a[2] && a[2] < 8 ? NODE(a[2]) : 0;
    if (fn == VEL_FN) { int h = (int)((c->ecx - 0x00220000u) / 0x100); memcpy(G + a[0], vel[h], 12); return 0; }
    if (fn == FN_QUERY_VEHICLE) { int h = (int)((c->ecx - 0x00220000u) / 0x100); return (uint32_t)is_vehicle[h]; }
    fprintf(stderr, "unexpected guest call %08X\n", fn); abort();
}
static void guest_init(void) {
    G = mmap(NULL, 32u << 20, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0);
    if (G == MAP_FAILED) exit(2);
    M32(SURFACE_CLASS) = SURF_CLASS_OBJ;
    M32(SIMABLES) = 0x00230000u; M32(SIMABLE_COUNT) = 8;
    for (uint32_t h = 1; h < 8; ++h) {
        uint32_t vt = 0x00240000u + 0x100 * h;
        M32(NODE(h)) = h; M32(NODE(h) + 4) = SIM(h); M32(SIM(h)) = vt; M32(vt + SIMABLE_LINEAR_VELOCITY) = VEL_FN;
    }
}

// ---- checks, per feature
enum { F_SURF, F_PROPS, NF };
static const char *const fname[NF] = {"surfaces", "props"};
static int checks[NF], fails[NF];
#define CHECK(f, x) do { ++checks[f]; if (!(x)) { ++fails[f]; fprintf(stderr, "FAIL %s line %d: %s\n", fname[f], __LINE__, #x); } } while (0)
#define NEAR(a, b) (fabsf((a) - (b)) < 1e-4f)

static uint64_t fixture_digest = 14695981039346656037ull;
static void digest(const void *data, size_t n) {
    const unsigned char *p = data;
    for (size_t i = 0; i < n; ++i) fixture_digest = (fixture_digest ^ p[i]) * 1099511628211ull;
}
static Cpu frame_cpu(void) { Cpu c; memset(&c, 0, sizeof c); c.esp = 0x00F00000u; return c; }
static void push3(Cpu *c, uint32_t a0, uint32_t a1, uint32_t a2) { M32(c->esp + 4) = a0; M32(c->esp + 8) = a1; M32(c->esp + 12) = a2; }
// One DoFFB tick on a surface at speed x (m/s), all four wheels; then 1 s of pad reads. Returns the peak motor levels.
static void drive(uint32_t surf, float x, float *heavy, float *light, Osc *light_osc) {
    memset(feedback_states[0].model.env, 0, sizeof feedback_states[0].model.env);                               // no collision envelope left from earlier checks
    Cpu c = frame_cpu();
    fb_resume(&c); c = frame_cpu();
    fb_begin_frame(&c);
    for (int w = 0; w < 4; ++w) {
        c = frame_cpu(); uint32_t xb; memcpy(&xb, &x, 4); push3(&c, w < 2, surf, xb);
        fb_speed(&c);
    }
    c = frame_cpu(); fb_end(&c);
    if (light_osc) *light_osc = feedback_states[0].model.osc[2];
    *heavy = *light = 0;
    for (int i = 0; i < 64; ++i) {
        float l, r; clock_ns += 16000000ull; feedback_levels(&l, &r);
        digest(&l, sizeof l); digest(&r, sizeof r);
        *heavy = fmaxf(*heavy, l); *light = fmaxf(*light, r);
    }
}
static float lerp_curve(const float r[8], int field, float x) {   // field 1 amp, 2 offset (curve_into's interpolation)
    float t = fminf((x - r[3]) / (r[7] - r[3]), 1.0f);
    return r[field] + (r[field + 4] - r[field]) * t;
}
static void surfaces_on(const char *when) {
    uint32_t cob = surface(kCobble), stone = surface(kStone), rail = surface(kRail), asph = surface(kAsphalt), conc = surface(kConcrete);
    float h, l; Osc q;
    drive(asph, 20, &h, &l, &q);
    CHECK(F_SURF, h == 0 && l == 0);                               // asphalt: the game's curves only (none here)
    drive(conc, 20, &h, &l, &q);
    CHECK(F_SURF, h == 0 && l == 0);                               // concrete is ordinary road: left alone
    drive(cob, 20, &h, &l, &q);
    CHECK(F_SURF, NEAR(q.amp, lerp_curve(kRoughLight, 1, 20)) && NEAR(q.offset, lerp_curve(kRoughLight, 2, 20)));
    CHECK(F_SURF, l > 0.10f && l <= lerp_curve(kRoughLight, 1, 20) + lerp_curve(kRoughLight, 2, 20) + 1e-4f);
    CHECK(F_SURF, h > 0 && h < 0.07f && h < l);                    // mostly the light motor, a faint heavy pulse
    float l20 = l;
    drive(cob, 40, &h, &l, &q);
    CHECK(F_SURF, l > l20);                                        // rising with speed
    drive(cob, 2, &h, &l, &q);
    CHECK(F_SURF, l == 0);                                         // nothing below 3 m/s
    drive(stone, 20, &h, &l, &q); CHECK(F_SURF, l > 0.10f);
    drive(rail, 20, &h, &l, &q); CHECK(F_SURF, l > 0.10f);
    printf("surfaces %s: light motor peak at 20 m/s %.3f, at 40 m/s %.3f; asphalt/concrete 0\n", when, l20, (drive(cob, 40, &h, &l, &q), l));
}
static void surfaces_x360(void) {
    uint32_t cob = surface(kCobble), stone = surface(kStone);
    float h, l;
    drive(cob, 20, &h, &l, NULL); CHECK(F_SURF, h == 0 && l == 0);
    drive(stone, 40, &h, &l, NULL); CHECK(F_SURF, h == 0 && l == 0);
}

// A collision: me (handle 1) hits handle 2. type 1 car/object; my velocity before -> after, the other's after.
static Env collide(int type, const float before[3], const float after[3], const float other_after[3], int vehicle) {
    static uint32_t info;
    if (!info) info = galloc(0x80);
    memset(G + info, 0, 0x80);
    M32(info + 0x1C) = (uint32_t)type; M32(info + 0x3C) = 1; M32(info + 0x4C) = 2;
    memcpy(G + info + 0x50, before, 12); memcpy(G + info + 0x60, (float[3]){0, 0, 0}, 12);
    memcpy(vel[1], after, 12); memcpy(vel[2], other_after, 12); is_vehicle[2] = vehicle;
    feedback_states[0].model.active = 1; feedback_states[0].model.env[0] = (Env){0};
    Cpu c = frame_cpu(); uint32_t esp = c.esp; M32(c.esp + 4) = info; M32(c.esp + 8) = 1;
    fb_collision(&c);
    CHECK(F_PROPS, c.esp == esp + 12);                             // RET(0, 2)
    digest(&feedback_states[0].model.env[0], sizeof feedback_states[0].model.env[0]);
    return feedback_states[0].model.env[0];
}
static float ramp(float speed) { return fminf(fmaxf((speed - X360E_FB_RAMP_LO) / (X360E_FB_RAMP_HI - X360E_FB_RAMP_LO), 0), 1); }
static void props(int on) {
    // A cardboard box at 50 m/s: my velocity changes by 1 m/s, the box flies off ahead at 60 m/s.
    Env e = collide(1, (float[3]){50, 0, 0}, (float[3]){49, 0, 0}, (float[3]){60, 0, 0}, 0);
    if (on) {
        float s = 1.0f * X360E_FB_WORLD_SLOPE;
        CHECK(F_PROPS, NEAR(e.start, fmaxf(s, 0.25f)) && NEAR(e.dur, fmaxf(s, 0.10f)));
        printf("props by impact: box at 50 m/s -> strength %.2f for %.2f s\n", e.start, e.dur);
    } else {
        CHECK(F_PROPS, NEAR(e.start, ramp(49)) && NEAR(e.dur, ramp(49)));   // the console: my speed's ramp, full for 1 s
        printf("props x360: box at 50 m/s -> strength %.2f for %.2f s\n", e.start, e.dur);
    }
    // A lamp post: my velocity changes by 30 m/s.
    e = collide(1, (float[3]){30, 0, 0}, (float[3]){0, 0, 0}, (float[3]){0, 0, 0}, 0);
    if (on) CHECK(F_PROPS, NEAR(e.start, fminf(30 * X360E_FB_WORLD_SLOPE, 1)) && NEAR(e.dur, fminf(30 * X360E_FB_WORLD_SLOPE, 1)));
    else CHECK(F_PROPS, NEAR(e.start, fminf((30 - X360E_FB_HIT_SOFT) * X360E_FB_HIT_SLOPE, 1)));
    // A traffic car side-swipe (vehicle): the same either way, the 0.45 s fade for a full hit.
    e = collide(1, (float[3]){30, 0, 0}, (float[3]){20, 0, 0}, (float[3]){5, 0, 0}, 1);
    float v = fminf((10 - X360E_FB_HIT_SOFT) * X360E_FB_HIT_SLOPE, 1.0f);
    CHECK(F_PROPS, NEAR(e.start, v) && NEAR(e.dur, v * 0.45f));
    // A wall (world contact): untouched by the switch.
    e = collide(2, (float[3]){30, 0, 0}, (float[3]){10, 0, 0}, (float[3]){0, 0, 0}, 0);
    CHECK(F_PROPS, NEAR(e.start, fminf(20 * X360E_FB_WORLD_SLOPE, 1)));
}

int main(int argc, char **argv) {
    guest_init();
    if (argc < 2) return 2;
    if (!strcmp(argv[1], "defaults")) {           // no switches: both On
        surfaces_on("default"); props(1);
        feedback_set_surfaces(0); surfaces_x360();  // F10 Off (X360), live
        feedback_set_props(0); props(0);
        feedback_set_surfaces(1); surfaces_on("menu on"); feedback_set_props(1); props(1);
    } else if (!strcmp(argv[1], "x360")) {        // NFS_RUMBLE_SURFACES=x360 NFS_RUMBLE_PROPS=x360 at start-up
        surfaces_x360(); props(0);
        feedback_set_surfaces(1); surfaces_on("menu on after x360 start");
    } else return 2;
    printf("single-pad fixture bytes: %016llx\n", (unsigned long long)fixture_digest);
    int total = 0, bad = 0;
    for (int f = 0; f < NF; ++f) {
        printf("%s %s %d/%d\n", argv[1], fname[f], checks[f] - fails[f], checks[f]);
        total += checks[f]; bad += fails[f];
    }
    printf("%s: %s (%d checks, %d failures)\n", argv[1], bad ? "FAIL" : "PASS", total, bad);
    return bad ? 1 : 0;
}
'''

ENV = {'defaults': {}, 'x360': {'NFS_RUMBLE_SURFACES': 'x360', 'NFS_RUMBLE_PROPS': 'x360'}}
CONTROLS = [
    ('surfaces', 'no port texture on rough paving',
     'if (f->model.active && rough_on() && is_rough(c, surf)) { curve_into(kRoughHeavy, x, &f->model.osc[1]); curve_into(kRoughLight, x, &f->model.osc[2]); }', ''),
    ('surfaces', 'texture on every road (concrete, asphalt)', 'return mine && (mine == coll[0] || mine == coll[1] || mine == coll[2]);',
     'return 1;'),
    ('surfaces', 'switch ignored (always on)', 'static int rough_on(void) { if (rough_sw < 0) rough_sw = sw_on("NFS_RUMBLE_SURFACES"); return rough_sw; }',
     'static int rough_on(void) { return 1; }'),
    ('props', 'props by the X360 speed ramp', 'if (props_on() && !vehicle_hit) {', 'if (0) {'),
    ('props', 'no minimum for small props', 'v = fmaxf(strength, kPropHitMinStrength);', 'v = strength;'),
    ('props', 'live menu change ignored', 'void feedback_set_props(int on) { props_sw = on != 0;', 'void feedback_set_props(int on) { (void)on;'),
]


def rows_wired():
    """The F10 rows: switch, default On, a live apply that reaches feedback.c."""
    s = (ROOT / 'runtime/settings_port.c').read_text()
    ok = 0
    for row, env, fn in [('controls.rumble_surfaces', 'NFS_RUMBLE_SURFACES', 'feedback_set_surfaces'),
                         ('controls.rumble_props', 'NFS_RUMBLE_PROPS', 'feedback_set_props')]:
        m = re.search(r'static Setting \w+ = \{\.id = "%s".*?\};' % re.escape(row), s, re.S)
        assert m, row
        body = m.group(0)
        apply = re.search(r'\.apply = (\w+)', body).group(1)
        assert '.env = "%s"' % env in body and '.def = 1' in body, row
        assert re.search(r'static void %s\(int v\) \{ %s\(v\); \}' % (apply, fn), s), row + ' apply'
        ok += 1
    return ok


def compiler():
    for cc in ('clang', 'cc'):
        if shutil.which(cc):
            return cc
    return None


def build(cc, tmp, source, name):
    d = tmp / name
    (d / 'gen').mkdir(parents=True)
    (d / 'gen/x360_engine.inc').write_text(ENGINE)
    (d / 'feedback.c').write_text(source)
    (d / 'harness.c').write_text(HARNESS)
    exe = d / 'test'
    base = [cc, '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Wno-unused-function',
            '-DFEEDBACK_SOURCE="%s"' % (d / 'feedback.c'), '-I' + str(d), '-I' + str(ROOT / 'runtime'),
            '-I' + str(ROOT / 'recomp'), str(d / 'harness.c'), '-o', str(exe), '-lm']
    for san in (['-fsanitize=address,undefined', '-fno-sanitize-recover=all'], []):
        p = subprocess.run(base[:1] + san + base[1:], capture_output=True, text=True)
        if p.returncode == 0:
            return exe, bool(san)
    raise SystemExit('build failed (%s):\n%s' % (name, p.stderr[-4000:]))


def run(exe, case):
    env = {k: v for k, v in os.environ.items() if not k.startswith('NFS_RUMBLE') and k != 'NFS_TEST_SURFACE'}
    env.update(ENV[case]); env.update(HOME=str(exe.parent), ASAN_OPTIONS='detect_leaks=0')
    p = subprocess.run([str(exe), case], capture_output=True, text=True, env=env, timeout=60)
    per = {}
    for line in p.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and re.fullmatch(r'\d+/\d+', parts[2]):
            ok, n = map(int, parts[2].split('/'))
            per[parts[1]] = (ok, n)
    return p, per


def main():
    cc = compiler()
    if not cc:
        print('SKIP: no C compiler')
        return 3
    print('F10 rows wired: %d/2 (switch, default On, live apply into feedback.c)' % rows_wired())
    production = (ROOT / 'runtime/feedback.c').read_text()
    with tempfile.TemporaryDirectory(prefix='rumble-switches-') as t:
        tmp = Path(t)
        exe, sanitized = build(cc, tmp, production, 'production')
        print('C instrumentation: ' + ('ASan + UBSan' if sanitized else 'plain (sanitizer build unavailable)'))
        total = 0
        for case in ENV:
            p, per = run(exe, case)
            sys.stdout.write(p.stdout)
            if p.returncode != 0:
                sys.stderr.write(p.stderr[-4000:])
                print('RUMBLE SWITCHES FAIL: production %s' % case)
                return 1
            expected = {'defaults': '3a46a848f422aee1', 'x360': '22d241e51eb8a84c'}[case]
            assert 'single-pad fixture bytes: ' + expected in p.stdout, 'slot-zero fixture bytes changed'
            total += sum(n for _, n in per.values())
        caught = 0
        for i, (feature, why, old, new) in enumerate(CONTROLS):
            assert production.count(old) == 1, 'control no longer applies: ' + why
            exe_b, control_sanitized = build(cc, tmp, production.replace(old, new), 'broken%d' % i)
            assert control_sanitized == sanitized, 'control instrumentation differs: ' + why
            bad = 0
            for case in ENV:
                p, per = run(exe_b, case)
                ok, n = per.get(feature, (0, 0))
                if p.returncode != 0 and (n == 0 or ok < n):
                    bad += (n - ok) if n else 1
            print('control %-8s %-44s %s' % (feature, why, 'FAILS %d check(s), as it must' % bad if bad else 'PASSED: not caught'))
            caught += bad > 0
        print('RUMBLE SWITCHES %s: %d checks, %d/%d broken controls caught%s' % (
            'PASS' if caught == len(CONTROLS) else 'FAIL', total, caught, len(CONTROLS),
            '' if sanitized else ' (no sanitizer runtime on this host)'))
        return 0 if caught == len(CONTROLS) else 1


if __name__ == '__main__':
    sys.exit(main())
