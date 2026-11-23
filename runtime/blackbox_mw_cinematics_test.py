"""Asset-free MW movie and NIS lifecycle proof through F10 and hang exports.
Missing lifecycle edges across F10 and hang: main 10, NIS feed removed 6, fixed 0.
Covers runtime/blackbox_mw_cinematics.c, runtime/blackbox_mw_cinematics.h,
runtime/blackbox_mw.c, runtime/blackbox.c, runtime/movies.c, runtime/diag.c,
runtime/runtime_sources.json, recomp/hooks.txt, runtime/docs/blackbox.md
and runtime/docs/diagnostics.md.
"""
import os
from pathlib import Path
import runpy
import statistics
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parent
CORE = runpy.run_path(str(R / 'blackbox_test.py'))
FE = runpy.run_path(str(R / 'blackbox_mw_fe_test.py'))

FIXTURE = r'''
static uint8_t memory[0x00990000];
uint8_t *G = memory;
enum { PLAYER = 0x2000, ACTIVITY = 0x4000, SCENE = 0x5000 };
static unsigned native_calls, audio_calls;
static uint32_t playing_player;
static int shape_is_ours;
static struct { int active; uint32_t player; } mv;

static unsigned arg(Cpu *c, int n) { return M32(c->esp + 4u * (n + 1)); }
static char *gptr(uint32_t address) { return (char *)G + address; }
static uint32_t galloc(unsigned size) { (void)size; abort(); }
static void movie_close(const char *why) { (void)why; }
static int host_play(Cpu *c, uint32_t self) { (void)c; (void)self; return 0; }
static void ds_set_movie_playing(int playing) {
    assert(playing == (audio_calls == 0));
    ++audio_calls;
}
static uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *args) {
    (void)c; (void)fn; (void)n; (void)args; abort();
}
static void f_00542A40_orig(Cpu *c) {
    assert(c->ecx == PLAYER);
    ++native_calls;
    M32(PLAYER + 0x11C) = 5;
}
static void f_00517610_orig(Cpu *c) {
    assert(c->ecx == PLAYER);
    ++native_calls;
    M32(PLAYER + 0x11C) = 0;
}
static void f_00525E60_orig(Cpu *c) {
    assert(c->ecx == PLAYER);
    ++native_calls;
}
static void f_006F51F0_orig(Cpu *c) {
    assert(c->ecx == ACTIVITY + 0x48 && arg(c, 1) == SCENE);
    ++native_calls;
    M32(ACTIVITY + 0x2F4) = 3;
    M32(ACTIVITY + 0x394) = 0x8A0E85A6;
}
static void f_006F5750_orig(Cpu *c) {
    assert(c->ecx == ACTIVITY);
    ++native_calls;
    M32(ACTIVITY + 0x2F4) = 5;
    M32(ACTIVITY + 0x39C) = SCENE;
}
static void f_006EA5A0_orig(Cpu *c) {
    assert(c->ecx == ACTIVITY);
    ++native_calls;
}
static uint32_t nis_activity, nis_hash;
static int nis_playing;
static char nis_name[64];
static unsigned nis_meta, nis_play, nis_actor[16];
static void breaker_diag_load(Cpu *c) { f_006F51F0_orig(c); }
static void diag_event(const char *fmt, ...) { (void)fmt; }
static void diag_timeline(const char *fmt, ...) { (void)fmt; }
#include "wrappers.inc"

static void step(uint64_t frame) {
    virtual_ns += 10000000;
    frame_now = frame;
    fixture_frame(frame, 3);
}
#ifdef BENCH
#define blackbox_game_frame frame_without_cinematics
#include "adapter_without.inc"
#undef blackbox_game_frame

static double time_frames(void (*adapter)(uint64_t, uint32_t, const BlackboxTiming *)) {
    const unsigned frames = 500000;
    uint64_t start = clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
    for (unsigned i = 0; i < frames; ++i) {
        virtual_ns += 4166667;
        adapter(i, 3, NULL);
    }
    return (double)(clock_gettime_nsec_np(CLOCK_UPTIME_RAW) - start) / frames;
}

static void bench(int active) {
    M32(0x0091CB10) = PLAYER;
    if (active) {
        M32(PLAYER + 0x11C) = 5;
        M32(0x009885C8) = ACTIVITY + 0x48;
        M32(ACTIVITY + 0x2F4) = 5;
    }
    time_frames(frame_without_cinematics);
    time_frames(blackbox_game_frame);
    for (int i = 0; i < 9; ++i) {
        double before, after;
        if (i % 2) {
            after = time_frames(blackbox_game_frame);
            before = time_frames(frame_without_cinematics);
        } else {
            before = time_frames(frame_without_cinematics);
            after = time_frames(blackbox_game_frame);
        }
        printf("%.3f %.3f\n", before, after);
    }
}
#endif

int main(int argc, char **argv) {
#ifdef BENCH
    if (argc == 2) { bench(!strcmp(argv[1], "active")); return 0; }
#else
    (void)argc; (void)argv;
#endif
    Cpu c = {.ecx = PLAYER, .esp = 0x1000};
    strcpy((char *)G + PLAYER + 0x18, "MOVIES\\intro_english_ntsc.vp6");
    strcpy((char *)G + SCENE, "IntroNisBL12");
    M32(0x0091CB10) = PLAYER;
    step(1);
    f_00542A40(&c);
    M32(PLAYER + 0x120) = 5;
    M8(PLAYER + 0x129) = 1;
    step(2);
    virtual_ns += 1000000;
    f_00517610(&c);
    M32(0x0091CB10) = 0;

    /* All three NIS calls occur between frame samples. */
    M32(0x009885C8) = ACTIVITY + 0x48;
    c.ecx = ACTIVITY + 0x48;
    M32(c.esp + 8) = SCENE;
    f_006F51F0(&c);
    c.ecx = ACTIVITY;
    f_006F5750(&c);
    f_006EA5A0(&c);
    M32(0x009885C8) = 0;
    assert(native_calls == 5 && audio_calls == 2);

    virtual_ns += 1000000;
    fixture_menu(1);
    virtual_ns += 80000000000ull;
    c.ecx = PLAYER;
    f_00525E60(&c);
    assert(native_calls == 6 && audio_calls == 3);
    FILE *f = fopen("blackbox.txt", "w"); assert(f);
    fixture_f10(f);
    fclose(f);
    fixture_menu(0);
    diag_report("hang_1.txt", "fixture");

    /* A long-lived active NIS sample remains visible outside the event window. */
    M32(0x009885C8) = ACTIVITY + 0x48;
    step(3);
    virtual_ns += 31000000000ull;
    step(4);
    f = fopen("context.txt", "w"); assert(f);
    blackbox_export(f, BB_HANG);
    fclose(f);
}
'''

EDGES = ('MW MOVIE edge=play ', 'MW MOVIE edge=stop ',
         'MW NIS edge=load ', 'MW NIS edge=play ', 'MW NIS edge=unload ')


def main():
    function = CORE['function']
    movie = (R / 'movies.c').read_text()
    diag = (R / 'diag.c').read_text()
    wrappers = movie[movie.index('#define SKIP_MOVIES'):movie.index('// MoviePlayer fields')]
    wrappers += '\n'.join(function(movie, 'void ' + name + '(Cpu *c) {')
                           for name in ('f_00542A40', 'f_00517610', 'f_00525E60'))
    wrappers += '\n'.join(function(diag, 'void ' + name + '(Cpu *c) {')
                           for name in ('f_006F51F0', 'f_006F5750', 'f_006EA5A0'))
    assert any(line.startswith('006EA5A0 ') for line in
               (R.parent / 'recomp/hooks.txt').read_text().splitlines())
    env = dict(os.environ)
    for key in ('NFS_BLACKBOX', 'BOOT_DIAG', 'BOOT_NIS'):
        env.pop(key, None)
    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch)
        adapter = (R / 'blackbox_mw.c').read_text()
        (work / 'adapter.inc').write_text(adapter)
        (work / 'hooks.inc').write_text(CORE['hooks']('mw'))
        (work / 'wrappers.inc').write_text(wrappers)
        source = work / 'fixture.c'
        source.write_text(FE['PRELUDE'] + FIXTURE)
        binary = work / 'fixture'
        cmd = ['clang', '-std=gnu11', '-O2', '-Werror', '-I' + str(R),
               '-I' + str(R.parent / 'recomp'), str(source),
               str(R / 'blackbox_mw_fe.c'), '-o', str(binary)]
        tap = R / 'blackbox_mw_cinematics.c'
        broken = work / 'without_nis.c'
        nis_hooks = ('blackbox_cinematic_edge(BB_NIS, edge, &s, valid);',
                     'blackbox_record_cinematic(BB_NIS, &nis, nis_valid, frame, now);')
        text = tap.read_text()
        for hook in nis_hooks:
            assert text.count(hook) == 1
            text = text.replace(hook, '')
        broken.write_text(text)

        for label, feed, expected in (('NIS feed removed', broken, 6), ('fixed', tap, 0)):
            subprocess.run(cmd + [str(feed), '-fsanitize=address,undefined'], check=True)
            subprocess.run([binary], cwd=work, env=env, check=True, timeout=15)
            outputs = [(work / name).read_text() for name in ('blackbox.txt', 'fixture-hang_1.txt')]
            missing = sum(edge not in report for report in outputs for edge in EDGES)
            print(f'{label}: {missing} missing lifecycle edges / 2 exports')
            assert missing == expected

        assert 'trigger=F10 snapshot=menu' in outputs[0]
        assert 'trigger=hang snapshot=live' in outputs[1]
        assert 'MW MOVIE edge=destroy ' not in outputs[0]
        assert 'MW MOVIE edge=destroy ' in outputs[1]
        for report in outputs:
            assert all(report.count(edge) == 1 for edge in EDGES)
            positions = [report.index(edge) for edge in EDGES]
            assert positions == sorted(positions)
            assert 'MW MOVIE edge=state valid=1 player=00002000 state=5 next=5 paused=1 filename_hash=FC4D7F04' in report
            assert ' 0.001000 f2 MW NIS edge=load' in report
            assert 'scene_hash=8A0E85A6' in report and 'scene=00005000' in report
            assert 'IntroNisBL12' not in report and 'intro_english_ntsc.vp6' not in report
        context = (work / 'context.txt').read_text()
        assert 'MW NIS edge=state valid=1 activity=00004000 state=5' in context
        assert all(edge not in context for edge in EDGES)

        subprocess.run([binary], cwd=work, env=dict(env, NFS_BLACKBOX='0'), check=True, timeout=15)
        assert all((work / name).read_text().count('blackbox v1 disabled') == 1
                   for name in ('blackbox.txt', 'fixture-hang_1.txt'))

        if sys.argv[1:] == ['--bench']:
            hook = '    blackbox_mw_cinematics(frame, now);\n'
            assert adapter.count(hook) == 1
            (work / 'adapter_without.inc').write_text(adapter.replace(hook, ''))
            subprocess.run(cmd + [str(tap), '-DBENCH'], check=True)
            for case in ('idle player', 'active'):
                batches = subprocess.check_output([binary, case], env=env, cwd=work, text=True, timeout=30)
                pairs = [tuple(map(float, row.split())) for row in batches.splitlines()]
                before, after = map(statistics.median, zip(*pairs))
                delta = [on - off for off, on in pairs]
                print(f'{case}: {before:.1f} -> {after:.1f} ns/frame, '
                      f'paired median +{statistics.median(delta):.1f} ({min(delta):+.1f} to {max(delta):+.1f})')


if __name__ == '__main__':
    main()
