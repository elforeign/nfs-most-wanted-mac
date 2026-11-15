"""Production height raster, resident caster decode and rain submission fixture.

Fixed runtime: 0/193; inverted: 54/193 FAIL; stale movement: 4/193 FAIL.
Ignored detail filter: 21/193 FAIL; stale detail: 9/193; stale signature: 4/193.
Core byte equality: 0/120; skipped resume: 4/120 FAIL; unbounded: 60/120 FAIL.
60 s paths at 90 Hz, 8,700 triangles / 5.5 ms deterministic build work:
straight 30/60/100 m/s: uncovered 0/0/0, starts 42/83/200;
curved (180 m radius): uncovered 0/0/0, starts 38/76/187.
All start intervals >=30 m; limits 61/121/201 per path. Pinned round-one
256-step / 16 m lead / 32 m margin control: uncovered 32/1310/3008
straight and 125/1316/2896 curved (8,687 total), six failing paths.
Stop/turn: 0 uncovered; disabled turn bypass: 11 uncovered FAIL.
Pinned old synchronous begin: 1000 starts, 1000/1076 FAIL; stale core: 2/77.
Off allocations/draws: 0/0. All controls run below with sanitizers.
Covered curtains leave at render_draw entry, before lamp/AO recording.
Autodiscovered by runtime/offline_tests.sh; no game or private assets needed.
"""
from pathlib import Path
import hashlib
import os
import re
import subprocess
import tempfile

import sys as _source_sys
_source_sys.path.insert(0, str(Path(__file__).resolve().parent))
from save_file_fixture import mw_source

ROOT = Path(__file__).resolve().parents[1]
RT = ROOT / 'runtime'

# The original metre/four-frame refresh rule, independent of repository history.
OLD_BEGIN = r'''
int rain_cover_begin(RainCoverMap *m, float x, float y, uint64_t frame) {
    if (!isfinite(x) || !isfinite(y)) { m->valid = 0; return 0; }
    /* Snap to texels, refresh immediately after a metre of movement. Keeping
     * the old origin until rebuilding prevents sampling a shifted old map. */
    x = floorf(x * 4) * .25f; y = floorf(y * 4) * .25f;
    if (m->valid && frame >= m->frame && frame - m->frame < 4 &&
        fabsf(x - m->x) < 1 && fabsf(y - m->y) < 1) return 0;
    if (!m->height) {
        m->height = malloc(RAIN_COVER_SIZE * RAIN_COVER_SIZE * sizeof(float));
        if (!m->height) { m->valid = 0; return 0; }
        ++m->allocations;
    }
    m->x = x; m->y = y; m->frame = frame; m->triangles = 0;
    for (unsigned i = 0; i < RAIN_COVER_SIZE * RAIN_COVER_SIZE; ++i) m->height[i] = -INFINITY;
    m->valid = 1; ++m->generation;
    return 1;
}
'''[1:]
OLD_SHA256 = '971b2550dd086be7b21fb334f094763a6fefffeb9982f8c4fa909b4f7df85810'


# Pinned round-one scheduling rule: cap, lead and margin; same caster workload.
ROUND1_SCHEDULE = r'''
    if (build.active && (fabsf(x-back.x) >= 64 || fabsf(y-back.y) >= 64)) build.active = 0;
    if (!build.active && !cover_needs(&map,x,y,dev.frames)) return camera;
    sliced = 1; slice_frame = dev.frames;
    uint64_t start = cover_clock();
    if (!build.active) {
        float distance = hypotf(dx,dy), lead = distance > 0 && distance < 8 ? fminf(24,16/distance) : 0;
        back.valid = 0;
        if (!cover_start(&back,x+dx*lead,y+dy*lead,dev.frames)) return camera;
        back.view = view; back.camera = camera; back.world_detail = world_detail;
        build = (CoverBuild){.active=1,.phase=COVER_CLEAR,.section=M32(0x009AA478u)};
    }
    for (unsigned steps = 0; build.active && steps < 256; ++steps) {
        if (cover_clock()-start >= 150000) break;
        cover_step();
    }
'''[1:]
ROUND1_SHA256 = 'e8eb1d19bc7d100519f29c3ebfc6b99069a5a8beb16ac7956035a8a69e9e4249'


def main():
    source = (RT / 'rain_cover.m').read_text()
    assert (RT / 'rain_cover.h').exists()
    start = source.index('    float distance = hypotf(dx,dy);', source.index('static uint32_t prepare'))
    end = source.index('\n    return camera;\n}', start)
    assert hashlib.sha256(ROUND1_SCHEDULE.encode()).hexdigest() == ROUND1_SHA256
    round1 = source[:start] + ROUND1_SCHEDULE + source[end:]
    mutants = {
        'round1': round1,
        'bad_turn': source.replace('< .5f*distance*hypotf(build_dx,build_dy)',
                                   '< -2*distance*hypotf(build_dx,build_dy)'),
        'fixed': source,
        'inverted': source.replace('> z + RAIN_COVER_CLEARANCE', '< z - RAIN_COVER_CLEARANCE'),
        'stale': source.replace('fabsf(x-m->x) >= RAIN_COVER_SPAN*.5f-RAIN_COVER_MARGIN ||\n'
                                '        fabsf(y-m->y) >= RAIN_COVER_SPAN*.5f-RAIN_COVER_MARGIN', '(x != x || y != y)').replace(
                                    'if (!cover_contains(m,x,y)) return 0;', 'if (m->valid) return INFINITY;'),
        'unfiltered': source.replace(
            'if (((flags & 0x80000) && back.world_detail != 0) ||\n'
            '                ((flags & 0x40000) && back.world_detail == 0 && !(flags & 0x100))) continue;',
            'if (0) continue;'),
        'resume': source.replace('const double *x = r->x, *y = r->y, d = r->d;',
                                 'if (r->active && r->col != r->lx) r->col = r->hx;\n'
                                 '    const double *x = r->x, *y = r->y, d = r->d;'),
        'unbounded': source.replace('r->active && budget--', 'r->active && (budget || 1)'),
        'mirror_prepare': source.replace('if (rain) prepare(rain);', 'prepare(rain);'),
        'stale_scene': source.replace('if (signature != scene_signature)', 'if (0)'),
        'stale_detail': source.replace(' || map.world_detail != world_detail', ''),
    }
    assert all(s != source for name, s in mutants.items() if name != 'fixed')
    with tempfile.TemporaryDirectory(prefix='rain-cover-') as directory:
        tmp = Path(directory)
        assert hashlib.sha256(OLD_BEGIN.encode()).hexdigest() == OLD_SHA256
        old = tmp / 'old_begin.inc'
        old.write_text(OLD_BEGIN)
        for name, body in mutants.items():
            path = tmp / (name + '.m')
            # Deterministic work clock, used only in the compiled fixture.
            body = body.replace('struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t);\n'
                                '    return (uint64_t)t.tv_sec*1000000000u+t.tv_nsec;',
                                'return fixture_clock();')
            body = body.replace('        cover_step();', '        fixture_charge(); cover_step();')
            path.write_text(body)
            binary = tmp / name
            cmd = ['clang', '-O1', '-g', '-fsanitize=address,undefined', '-fobjc-arc',
                   '-Wall', '-Wextra', '-Werror', '-I', str(RT), '-I', str(ROOT / 'recomp'),
                   '-DRAIN_COVER_SYNTHETIC_CLOCK', '-DRAIN_COVER_SOURCE="' + str(path) + '"', str(RT / 'rain_cover_test.m'),
                   '-framework', 'Foundation', '-framework', 'Metal', '-framework', 'QuartzCore', '-o', str(binary)]
            if name in ('fixed', 'stale', 'resume', 'unbounded'):
                core_cmd = cmd[:-1] + [str(binary) + '-core']
                core_cmd[1:1] = ['-DRAIN_COVER_CORE_ONLY', '-DRAIN_COVER_OLD_BEGIN=\"' + str(old) + '\"']
                build = subprocess.run(core_cmd, capture_output=True, text=True)
                assert build.returncode == 0, build.stderr[-5000:]
                run = subprocess.run([str(binary) + '-core'], capture_output=True, text=True)
                print(name + ' core: ' + run.stdout.strip().replace('\n', '; '))
                assert run.returncode == (0 if name == 'fixed' else 1), run.stderr[-5000:]
                if name == 'fixed':
                    broken = subprocess.run([str(binary) + '-core', '--old-rule'], capture_output=True, text=True)
                    print('old refresh core: ' + broken.stdout.strip().replace('\n', '; '))
                    assert broken.returncode == 1, broken.stderr[-5000:]
            if name in ('resume', 'unbounded'):
                continue
            build = subprocess.run(cmd, capture_output=True, text=True)
            assert build.returncode == 0, build.stderr[-5000:]
            run = subprocess.run([str(binary)] + (['--race-path'] if name in ('round1', 'bad_turn') else []), capture_output=True, text=True,
                                 env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'))
            print(name + ': ' + run.stdout.strip().replace('\n', '; '))
            if name == 'round1':
                counts = re.findall(r'race path .*?: uncovered (\d+),', run.stdout)
                assert len(counts) == 6 and all(int(n) > 0 for n in counts), run.stdout[-3000:]
            assert run.returncode == (0 if name == 'fixed' else 1), run.stderr[-5000:]
            if name == 'fixed':
                paths = subprocess.run([str(binary), '--race-path'], capture_output=True, text=True,
                                       env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'))
                print(paths.stdout.strip())
                assert paths.returncode == 0, paths.stderr[-5000:]
        renderer = mw_source(RT / 'render.m')
        entry = renderer.split('void render_draw(', 1)[1].split('static float sign_saved_psc', 1)[0]
        assert 'if (rain_cover_enabled && rain_cover_curtain()) return;' in entry   # before lamp/AO recording
        assert 'if (rain_cover_enabled && rain_cover_ripple(ps->tokens_hash)) {' in renderer
        assert 'rain_cover_render(c, f_0074ACC0_orig);' in (RT / 'materials.m').read_text()
        assert 'if (rain_cover_enabled && rain_cover_lens(rain))' in (RT / 'x360_post.m').read_text()
        assert 'REG(s_rain_cover)' in (RT / 'settings_port.c').read_text()
        assert '00758100    rain_cover.m:' in (ROOT / 'recomp/hooks.txt').read_text()
    print('rain_cover_test PASS')


if __name__ == '__main__':
    main()
