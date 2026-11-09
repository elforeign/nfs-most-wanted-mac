#!/usr/bin/env python3
"""Live picture review regressions; production C/ObjC with counted resources and fake windows.

32 rebuilds: original 33 live cubes / 51,904,512 bytes, fixed 1 / 1,572,864 bytes.
Space entry then Borderless: original Native (1), fixed Borderless (0); state checks original 3 failures, fixed 0.
Player set: original 1 persistent write, fixed 0; public diagnostics: original 1, fixed 0.
Original fragments are embedded and SHA-256 checked; no game, display or GPU is opened.
"""
from pathlib import Path
import hashlib
import subprocess
import tempfile

RT = Path(__file__).resolve().parent
ROOT = RT.parent

# Original reset body, before the managed backup's reference was released; retired route calls omitted.
OLD_RESET = '''    ++resets;
    envcube_live_reconfigure();
    if (diag_on) diag_event("live picture: reset %u (requests %u)", resets, requests);
'''
OLD_MODE = '''void user32_fullscreen_mode_changed(void) {
    Win *w = game_window();
    app_fullscreen_mode_reset();
    if (!w || !w->ns) return;
    if (w->native_transition) { fprintf(stderr, "boot: full screen mode change ignored while the window is changing Space\\n"); return; }
    if ((w->ns.styleMask & NSWindowStyleMaskFullScreen) && !app_native_fullscreen()) {   // out of the Space first, then borderless
        w->native_transition = 1; w->reenter = 1;
        app_request_fullscreen_space(w->ns, 0);
        return;
    }
    ns_apply_mode(w);
    if (!w->native_transition) ns_mode_changed(w);
}'''
OLD_CALLBACKS = '''- (void)windowDidEnterFullScreen:(NSNotification *)n {
    (void)n; Win *w = win(self.hwnd); if (!w) return;
    w->native_transition = 0; w->full = 1;
    if (app_bundled) app_pref_set("fullscreen", "1");
    ns_mode_changed(w);
}
- (void)windowDidExitFullScreen:(NSNotification *)n {
    (void)n; Win *w = win(self.hwnd); if (!w) return;
    w->native_transition = 0; w->full = 0;
    ns_apply_mode(w);
    if (w->reenter) { w->reenter = 0; w->full = 1; ns_apply_mode(w); }   // F10 Full screen mode: Native -> Borderless
    else if (app_bundled) app_pref_set("fullscreen", "0");
    ns_mode_changed(w);
}
- (void)windowDidFailToEnterFullScreen:(NSWindow *)window {
    (void)window; Win *w = win(self.hwnd); if (!w) return;
    w->native_transition = 0; w->full = 0; ns_apply_mode(w); ns_mode_changed(w);
    fprintf(stderr, "boot: native full screen entry failed\\n");
}
- (void)windowDidFailToExitFullScreen:(NSWindow *)window {
    (void)window; Win *w = win(self.hwnd); if (!w) return;
    w->native_transition = 0; w->full = 1; w->reenter = 0; ns_mode_changed(w);
    fprintf(stderr, "boot: native full screen exit failed\\n");
}'''
OLD_SET = '''        char id[96], *eq = strchr(tok + 4, '=');
        Setting *row = NULL;
        if (eq && (size_t)(eq - tok - 4) < sizeof id) {
            snprintf(id, sizeof id, "%.*s", (int)(eq - tok - 4), tok + 4);
            row = settings_find(id);
        }
        int v = -1;
        if (row) { for (int i = 0; i < row->nchoices || (row->type == SET_TOGGLE && i < 2); ++i) {
            char sp[32]; const char *l = row->values ? row->values[i] : row->choices ? row->choices[i] : i ? "on" : "off";
            snprintf(sp, sizeof sp, "%s", l); if (!strcasecmp(sp, eq + 1)) { v = i; break; } } }
        int changed = row && v >= 0 ? settings_set(row, v) : -1;
        if (diag_on) diag_event("script: %s -> %d", tok, changed);
        ++step; step_start = 0;
'''
BASE_SHA256 = '8e1fd26ace27861ae155da66bc4e27b02e2d3e91fb767e647515e5b44c7b7d4c'


def function(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


def compile_run(tmp, name, source, args=(), flags=(), objc=False):
    path = tmp / (name + ('.m' if objc else '.c'))
    path.write_text(source)
    exe = tmp / name
    command = ['clang', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
               '-g', '-fsanitize=address,undefined', '-I', str(ROOT / 'recomp'),
               '-I', str(RT), *flags, str(path), '-o', str(exe)]
    if objc:
        command += ['-fobjc-arc', '-framework', 'Cocoa']
    compiled = subprocess.run(command, capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    result = subprocess.run([str(exe), *args], capture_output=True, text=True)
    assert result.returncode in (0, 1), result.stdout + result.stderr
    assert 'Sanitizer' not in result.stderr, result.stderr
    print(name + ': ' + result.stdout.strip(), flush=True)
    return result


ALLOC_PRE = r'''
#include <assert.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include "d3d9_internal.h"
uint8_t *G;
int diag_on;
static unsigned releases, retired, faults, checks;
static size_t live_bytes;
static unsigned live_cubes;
static Obj resources[64];
static void *backings[64 * 6];
static unsigned next_backing, next_object;
Device dev;
#define CHECK(x) do { ++checks; if (!(x)) ++faults; } while (0)
void diag_event(const char *fmt, ...) { (void)fmt; }
int settings_get(const char *id) { (void)id; return -1; }
void envcube_live_reconfigure(void) {}
int x360_post_ssao_enabled(void) { return 0; }
void user32_fullscreen_mode_changed(void) {}
Obj *obj(uint32_t guest) {
    for (unsigned i = 0; i < next_object; ++i) if (resources[i].guest == guest) return &resources[i];
    return NULL;
}
#define THIS obj(arg(c, 0))
static void mat_bone_reset(void) {}
static void envcube_forget(Obj *o) { (void)o; }
void render_release(Obj *o) { (void)o; }
static void obj_retire(Obj *o) { (void)o; ++retired; --live_cubes; }
void gfree(uint32_t token) {
    if (token) { assert(backings[token]); free(backings[token]); backings[token] = NULL; live_bytes -= 256 * 256 * 4; }
}
'''
ALLOC_POST = r'''
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *args) {
    assert(fn == 0x12345678 && n == 1);
    CHECK(M32(0x00982A64u) == 0);
    ++releases;
    c->esp -= 8; M32(c->esp + 4) = args[0];
    m_Release(c);
    c->eax = 0xdeadbeef; c->ecx = 0xbad; // Reset must preserve the caller even for a clobbering COM method.
    return c->eax;
}
static uint32_t create_cube(void) {
    Obj *o = &resources[next_object++];
    o->guest = 0x1000 + next_object * 16; o->refs = 1; o->kind = K_CUBE;
    M32(o->guest) = 0x800; M32(0x808) = 0x12345678;
    for (int i = 0; i < 6; ++i) {
        unsigned token = ++next_backing;
        backings[token] = malloc(256 * 256 * 4); assert(backings[token]);
        o->mem[i * 16] = token; live_bytes += 256 * 256 * 4;
    }
    ++live_cubes;
    return o->guest;
}
int main(void) {
    G = calloc(1, 0x1000000);
    Cpu c = {.eax = 42, .ecx = 99, .esp = 0x80000}, saved = c;
    M32(0x00982A64u) = create_cube();
    live_picture_on_reset(&c); // ordinary game Reset must retain the backup
    CHECK(releases == 0 && live_cubes == 1);
    for (unsigned i = 0; i < 32; ++i) {
        live_picture_request("test"); live_picture_request("coalesced");
        CHECK(G[0x00982C39u] == 1);
        G[0x00982C39u] = 0;
        live_picture_on_reset(&c);
        live_picture_on_reset(&c); // game's two Reset calls must not double-release
        CHECK(memcmp(&c, &saved, sizeof c) == 0);
        M32(0x00982A64u) = create_cube(); // 006BD4B0 recreates after Reset, overwriting the old pointer
        CHECK(live_cubes == 1 && live_bytes == 1572864);
    }
    CHECK(releases == 32 && retired == 32);
    printf("rebuilds=32 live=%u bytes=%zu releases=%u checks=%u failures=%u\n",
        live_cubes, live_bytes, releases, checks, faults);
    // A second owner (bound resource/effect) retains its reference after the live rebuild releases the game reference.
    Obj *held = obj(M32(0x00982A64u)); held->refs = 2;
    live_picture_request("held"); live_picture_on_reset(&c);
    CHECK(held->refs == 1 && held->mem[0] != 0);
    obj_release(held);
    unsigned before = releases;
    M32(0x00982A64u) = 0;
    live_picture_request("empty"); live_picture_on_reset(&c);
    CHECK(releases == before);
    for (unsigned i = 0; i < next_object; ++i) while (resources[i].refs > 0) obj_release(&resources[i]);
    CHECK(live_bytes == 0 && live_cubes == 0);
    free(G);
    return faults ? 1 : 0;
}
'''

MODE_PRE = r'''
#import <Cocoa/Cocoa.h>
#include <stdio.h>
#include <stdlib.h>
@interface WindowSpy : NSObject
@property NSWindowStyleMask styleMask;
@property NSWindowCollectionBehavior collectionBehavior;
@property NSSize contentAspectRatio;
- (void)setContentSize:(NSSize)size;
- (void)center;
- (void)makeFirstResponder:(id)view;
- (void)setFrame:(NSRect)frame display:(BOOL)display;
@end
@implementation WindowSpy
- (void)setContentSize:(NSSize)size { (void)size; }
- (void)center {}
- (void)makeFirstResponder:(id)view { (void)view; }
- (void)setFrame:(NSRect)frame display:(BOOL)display { (void)frame; (void)display; }
@end
@interface AppSpy : NSObject
@property NSApplicationPresentationOptions presentationOptions;
@end
@implementation AppSpy
@end
static AppSpy *application;
#define NSApp application
typedef struct {
    int native_transition, full, visible, cw, ch, reenter, mode_pending;
    WindowSpy *ns;
    id view;
} Win;
static Win record;
static int native, requested = -1, toggles, saved_full, checks, faults;
static int app_bundled = 1;
static const NSWindowStyleMask windowed_style = NSWindowStyleMaskTitled;
#define CHECK(x) do { ++checks; if (!(x)) { ++faults; printf("FAIL line=%d %s\n", __LINE__, #x); } } while (0)
static Win *win(unsigned hwnd) { (void)hwnd; return &record; }
static Win *game_window(void) { return &record; }
static int app_native_fullscreen(void) { return native; }
static void app_fullscreen_mode_reset(void) {}
static void app_pref_set(const char *key, const char *value) { (void)key; saved_full = atoi(value); }
static NSRect ns_content_rect(const Win *w) { return NSMakeRect(0, 0, w->cw, w->ch); }
static int ns_fullscreen(const Win *w) { return w->full; }
static void ns_mode_changed(Win *w) { (void)w; }
static int app_request_fullscreen_space(WindowSpy *window, int full) {
    (void)window;
    CHECK(requested == -1);
    requested = full; ++toggles;
    return 1;
}
static void ns_reconcile_mode(Win *w);
@interface Delegate : NSObject
@property unsigned hwnd;
- (void)windowDidEnterFullScreen:(NSNotification *)n;
- (void)windowDidExitFullScreen:(NSNotification *)n;
- (void)windowDidFailToEnterFullScreen:(NSWindow *)window;
- (void)windowDidFailToExitFullScreen:(NSWindow *)window;
@end
'''
MODE_POST = r'''
static Delegate *delegate;
static void start(int is_native, int full) {
    record = (Win){.full = full, .visible = 1, .cw = 1280, .ch = 720, .ns = [WindowSpy new]};
    native = is_native; requested = -1; toggles = 0; saved_full = full;
    record.ns.styleMask = is_native && full ? NSWindowStyleMaskFullScreen : full ? NSWindowStyleMaskBorderless : windowed_style;
}
static void choose(int mode) { native = mode; user32_fullscreen_mode_changed(); }
static void finish(void) {
    CHECK(requested != -1);
    if (requested == -1) return;
    int entering = requested; requested = -1;
    if (entering) { record.ns.styleMask |= NSWindowStyleMaskFullScreen; [delegate windowDidEnterFullScreen:nil]; }
    else { record.ns.styleMask &= ~NSWindowStyleMaskFullScreen; [delegate windowDidExitFullScreen:nil]; }
}
static int owns_space(void) { return !!(record.ns.styleMask & NSWindowStyleMaskFullScreen); }
static void settled(int space, int full) {
    CHECK(owns_space() == space); CHECK(record.full == full);
    CHECK(!record.native_transition && !record.mode_pending && !record.reenter && requested == -1);
}
int main(void) { @autoreleasepool {
    application = [AppSpy new]; delegate = [Delegate new];
    start(0, 1); choose(1); choose(0); finish(); if (requested != -1) finish();
    printf("entry_then_borderless: native=%d toggles=%d\n", owns_space(), toggles);
    settled(0, 1); CHECK(saved_full == 1 && toggles == 2);
    start(0, 1); choose(1); choose(0); choose(1); finish(); settled(1, 1); CHECK(toggles == 1);
    start(1, 1); choose(0); choose(1); finish(); if (requested != -1) finish();
    settled(1, 1); CHECK(toggles == 2);
    start(1, 1); choose(0); choose(1); choose(0); finish(); settled(0, 1); CHECK(toggles == 1);
    start(1, 1); record.native_transition = 1; requested = 0; choose(0); finish(); settled(0, 0);
    start(0, 0); choose(1); settled(0, 0); choose(0); settled(0, 0); CHECK(toggles == 0);
    start(0, 1); choose(1); choose(0); requested = -1; [delegate windowDidFailToEnterFullScreen:nil]; settled(0, 1);
    start(0, 1); choose(1); requested = -1; [delegate windowDidFailToEnterFullScreen:nil]; settled(0, 0);
    start(1, 1); choose(0); choose(1); requested = -1; [delegate windowDidFailToExitFullScreen:nil]; settled(1, 1);
    start(1, 1); choose(0); requested = -1; [delegate windowDidFailToExitFullScreen:nil]; settled(1, 1);
    printf("space_state: checks=%d failures=%d\n", checks, faults);
    return faults ? 1 : 0;
} }
'''

SET_PRE = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <stdarg.h>
#include <stdint.h>
#include <unistd.h>
#include "settings.h"
static int diag_on, writes, logs, sets, finds;
static const char *const values[] = {"sharp", "pc"};
static Setting row = {.id = "graphics.look_reflections", .type = SET_CHOICE, .values = values, .nchoices = 2};
Setting *settings_find(const char *id) { ++finds; return !strcmp(id, row.id) ? &row : NULL; }
int settings_set(Setting *s, int value) {
    ++sets; ++writes; s->value = value;
    FILE *f = fopen("app.txt", "w"); if (!f) abort(); fputs("graphics.look_reflections=pc\n", f); fclose(f);
    return 1;
}
void diag_event(const char *fmt, ...) {
    ++logs; FILE *f = fopen("events.log", "a"); if (!f) abort();
    va_list args; va_start(args, fmt); vfprintf(f, fmt, args); va_end(args); fclose(f);
}
static int step;
static uint64_t step_start = 9;
static void script_set(const char *tok) {
    (void)tok;
'''
SET_POST = r'''
}
int main(int argc, char **argv) {
    if (argc != 4 || chdir(argv[1])) abort();
    diag_on = atoi(argv[2]); int allowed = atoi(argv[3]);
    script_set("set:graphics.look_reflections=pc");
    int files = (access("app.txt", F_OK) == 0) + (access("events.log", F_OK) == 0);
    int faults = writes != allowed || sets != allowed || finds != allowed || row.value != allowed ||
        logs != (allowed && diag_on) || files != (allowed ? 1 + !!diag_on : 0) || step != 1 || step_start != 0;
    printf("diag=%d writes=%d logs=%d files=%d steps=%d failures=%d\n", diag_on, writes, logs, files, step, faults);
    return faults;
}
'''


def main():
    assert hashlib.sha256((OLD_RESET + OLD_MODE + OLD_CALLBACKS + OLD_SET).encode()).hexdigest() == BASE_SHA256
    live = (RT / 'live_picture.c').read_text()
    d3d = (RT / 'd3d9.m').read_text()
    assert 'live_picture_on_reset(c);' in function(d3d, 'static int v_Reset(')
    release = '\n'.join(function(d3d, sig) for sig in (
        'static Obj *ref_owner(', 'static uint32_t obj_release(', 'static int m_Release('))
    user32 = (RT / 'user32.m').read_text()
    callbacks = user32[user32.index('- (void)windowDidEnterFullScreen:'):user32.index('- (BOOL)windowShouldClose:')]
    # Execute the actual BOOT_KEYS branch, including its preprocessor/runtime guards.
    set_start = user32.index('    } else if (!strncmp(tok, "set:", 4))')
    set_body = user32[user32.index('\n', set_start) + 1:user32.index('    } else if (!strcmp(tok, "shot"))', set_start)]
    with tempfile.TemporaryDirectory(prefix='live-picture-regression-') as directory:
        tmp = Path(directory)
        for original in (True, False):
            name = 'original' if original else 'fixed'
            source = live
            if original:
                reset = function(live, 'void live_picture_on_reset(')
                source = live.replace(reset, 'void live_picture_on_reset(Cpu *c) {\n    (void)c;\n' + OLD_RESET + '}')
            result = compile_run(tmp, name + '_alloc', ALLOC_PRE + release + '\n' + source + ALLOC_POST, objc=True)
            assert result.returncode == int(original), result.stdout + result.stderr
            expected = 'live=33 bytes=51904512 releases=0' if original else 'live=1 bytes=1572864 releases=32'
            assert expected in result.stdout, result.stdout
            mode = OLD_MODE if original else function(user32, 'void user32_fullscreen_mode_changed(')
            source = (MODE_PRE + function(user32, 'static void ns_apply_mode(Win *w) {') + '\n' +
                      function(user32, 'static void ns_reconcile_mode(Win *w) {') + '\n' + mode +
                      '\n@implementation Delegate\n' + (OLD_CALLBACKS if original else callbacks) + '\n@end\n' + MODE_POST)
            result = compile_run(tmp, name + '_mode', source, objc=True)
            assert result.returncode == int(original), result.stdout + result.stderr
            assert f'entry_then_borderless: native={int(original)}' in result.stdout, result.stdout
            for public in (False, True):
                for diag in (0, 1):
                    case = f'{name}_set_public{int(public)}_diag{diag}'
                    scratch = tmp / (case + '_prefs'); scratch.mkdir()
                    body = OLD_SET if original else set_body
                    result = compile_run(tmp, case, SET_PRE + body + SET_POST,
                                         (str(scratch), str(diag), str(int(diag and not public))),
                                         flags=('-DNFS_PUBLIC=1',) if public else ())
                    assert result.returncode == int(original and (public or not diag)), result.stdout + result.stderr
        print('live_picture_regression PASS: original controls fail; fixed allocation, Space and player/public settings checks pass')


if __name__ == '__main__':
    main()
