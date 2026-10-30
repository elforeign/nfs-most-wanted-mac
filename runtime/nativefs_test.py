"""Full-screen registry and asynchronous Cocoa policy, using fake windows only.

Proof: 6 registry boots and fake-window production: 0 failures, 6 change notes.
Restart-disabled row: 1 failure; missing toggle: 5; missing Space flag: 1.
Stale presentation cache: 6 failures; real policy checks request/display/limit gates.
No NSApplication, real window, Metal device or game is created.
"""
from pathlib import Path
import os
import subprocess
import tempfile

RT = Path(__file__).resolve().parent
app = (RT / 'app.m').read_text()
policy = app.split('// BEGIN offline fullscreen policy:')[1].split('\n', 1)[1].split('// END offline fullscreen policy')[0]
port = (RT / 'settings_port.c').read_text()
row = port[port.index('static const char *const kFullScreen[]'):port.index('static const char *const kOverlay[]')]
assert 'REG(s_fullscreen);' in port
stubs = (RT / 'settings_look_test.c').read_text()
stubs = stubs[stubs.index('#include <stdio.h>'):stubs.index('static int failures;')]
user = (RT / 'user32.m').read_text()
mode = user[user.index('static void ns_apply_mode(Win *w) {'):user.index('static void ns_close(Win *w) {')]
resize = user[user.index('static void ns_resize(Win *w) {'):user.index('// app.m\'s View >')]
callbacks = user.split('@implementation GameWindowDelegate\n')[1].split('- (BOOL)windowShouldClose:')[0]
presenter = (RT / 'render_present.m').read_text()
presenter = presenter[presenter.index('static double next_display;'):presenter.index('static void presented(')]
host = (RT / 'host.h').read_text()
presentation_type = host[host.index('typedef struct {\n    uint64_t shown[5];'):host.index('} RenderPresentation;') + len('} RenderPresentation;')]

ROW_MAIN = r'''
@ROW@
static int failures;
#define CHECK(x) do { if(!(x)){fprintf(stderr,"FAIL: %s\n",#x);++failures;} }while(0)
int main(int argc, char **argv) {
    if(argc!=3)return 2;
    if(strcmp(argv[1],"-"))app_pref_set("display.fullscreen",argv[1]);
    if(strcmp(argv[2],"-"))setenv("NFS_FULLSCREEN_MODE",argv[2],1);
    settings_add(&s_fullscreen);settings_init();
    int initial=settings_get("display.fullscreen");
    const char *wanted=strcmp(argv[2],"-")?argv[2]:argv[1];
    CHECK(initial==!strcasecmp(wanted,"native"));
    CHECK(s_fullscreen.category==SET_DISPLAY && !strcmp(s_fullscreen.label,"Full screen"));
    CHECK(s_fullscreen.def==0 && s_fullscreen.restart==1 && !s_fullscreen.apply);
    CHECK(s_fullscreen.nchoices==2 && !strcmp(s_fullscreen.choices[0],"Borderless") && !strcmp(s_fullscreen.choices[1],"Native"));
    char boot_mode[32];snprintf(boot_mode,sizeof boot_mode,"%s",getenv("NFS_FULLSCREEN_MODE")?:"");
    if(initial)CHECK(!strcasecmp(boot_mode,"native"));
    else CHECK(!*boot_mode || !strcasecmp(boot_mode,"borderless"));
    if(s_fullscreen.locked)CHECK(!settings_set(&s_fullscreen,!initial));
    else for(int i=0;i<3;++i) {
        int v=i!=1;settings_set(&s_fullscreen,v);
        CHECK(settings_get("display.fullscreen")==v);
        CHECK(!strcmp(app_pref("display.fullscreen"),v?"native":"borderless"));
        CHECK(!strcmp(getenv("NFS_FULLSCREEN_MODE")?:"",boot_mode));
    }
    printf("row failures=%d\n",failures);return failures?1:0;
}
'''

WINDOW = r'''
#import <Cocoa/Cocoa.h>
#import <QuartzCore/CAMetalLayer.h>
#include <stdarg.h>
#include <pthread.h>
#include "gpuclients.h"
#include "framecap.h"
#include "roam_online.h"
atomic_int roam_online_active;
@interface FakeScreen : NSObject
@property NSInteger maximumFramesPerSecond;
@property NSTimeInterval minimumRefreshInterval, maximumRefreshInterval, displayUpdateGranularity;
@end
@implementation FakeScreen
@end
@interface FakeWindow : NSObject
@property NSWindowStyleMask styleMask;
@property NSWindowCollectionBehavior collectionBehavior;
@property NSSize contentAspectRatio;
@property NSScreen *screen;
@property int toggles, frames, sizes, centers;
- (void)toggleFullScreen:(id)sender;
- (void)setFrame:(NSRect)r display:(BOOL)b;
- (void)setContentSize:(NSSize)s;
- (void)center;
- (BOOL)makeFirstResponder:(id)r;
@end
@implementation FakeWindow
- (void)toggleFullScreen:(id)sender {(void)sender; ++_toggles;}
- (void)setFrame:(NSRect)r display:(BOOL)b {(void)r;(void)b;++_frames;}
- (void)setContentSize:(NSSize)s {(void)s;++_sizes;}
- (void)center {++_centers;}
- (BOOL)makeFirstResponder:(id)r {(void)r;return YES;}
@end
@interface FakeApp : NSObject
@property NSApplicationPresentationOptions presentationOptions;
@end
@implementation FakeApp
@end
@interface FakeLayer : NSObject
@property CGSize drawableSize;
@property id delegate;
@property BOOL displaySyncEnabled;
@end
@implementation FakeLayer
@end
@interface FakeView : NSObject
@property id layer;
@property NSWindow *window;
@end
@implementation FakeView
- (BOOL)isKindOfClass:(Class)c {return c==NSView.class || [super isKindOfClass:c];}
@end
typedef struct {int full, visible, native_transition, mode_logged, cw, ch; NSWindow *ns; NSView *view;} Win;
@PRESENTATION_TYPE@
static Win game;
static int app_bundled=1, notes, samples, failures, request=1, limit_choice;
static char note[256], pref[8];
static Win *win(uint32_t h) {(void)h;return &game;}
static NSRect ns_content_rect(const Win *w) {return NSMakeRect(0,0,w->full?1920:800,w->full?1080:600);}
static int ns_fullscreen(const Win *w) {return w->full;}
static void app_pref_set(const char *k,const char *v) {(void)k;snprintf(pref,sizeof pref,"%s",v);}
static CAMetalLayer *layer;
static pthread_mutex_t presentation_mu=PTHREAD_MUTEX_INITIALIZER;
static RenderPresentation presentation;
static double gpu_average=.010;
static uint64_t host_ticks(void) {return 1000000000;}
static int settings_get(const char *id) {return !strcmp(id,"graphics.frame_rate_limit")?limit_choice:request;}
@PRESENTER@
static void gpuclients_cached_stub(GPUClientsSample *s) {++samples;snprintf(s->mhz,sizeof s->mhz,"state=P13");}
#define gpuclients_cached gpuclients_cached_stub
static void report_note(const char *fmt,...) {++notes;va_list ap;va_start(ap,fmt);vsnprintf(note,sizeof note,fmt,ap);va_end(ap);}
#define CHECK(x) do {if(!(x)){fprintf(stderr,"FAIL: %s\n",#x);++failures;}}while(0)
static const NSWindowStyleMask windowed_style=NSWindowStyleMaskTitled|NSWindowStyleMaskClosable|NSWindowStyleMaskMiniaturizable|NSWindowStyleMaskResizable;
@POLICY@
@MODE@
@RESIZE@
@interface GameWindowDelegate : NSObject
@property uint32_t hwnd;
@end
@implementation GameWindowDelegate
@CALLBACKS@
@end
int main(void) {@autoreleasepool {
    FakeWindow *fake=[FakeWindow new]; FakeApp *app=[FakeApp new];
    NSApp=(NSApplication *)app; // No NSApplication is instantiated.
    FakeView *view=[FakeView new];FakeLayer *backing=[FakeLayer new];view.layer=backing;
    view.window=(NSWindow *)fake;backing.delegate=view;backing.displaySyncEnabled=YES;layer=(CAMetalLayer *)backing;
    FakeScreen *screen=[FakeScreen new];screen.maximumFramesPerSecond=120;
    screen.minimumRefreshInterval=1./120;screen.maximumRefreshInterval=1./48;
    fake.screen=(NSScreen *)screen;
    game=(Win){.full=1,.visible=1,.cw=1920,.ch=1080,.ns=(NSWindow *)fake,.view=(NSView *)view};
    GameWindowDelegate *d=[GameWindowDelegate new];
    unsetenv("NFS_FULLSCREEN_MODE");CHECK(!app_native_fullscreen());
    ns_apply_mode(&game);ns_mode_changed(&game);
    CHECK(fake.styleMask==NSWindowStyleMaskBorderless && fake.toggles==0 && fake.frames==1);
    CHECK(app.presentationOptions==(NSApplicationPresentationHideDock|NSApplicationPresentationHideMenuBar));
    CHECK(strstr(note,"mode=Borderless") && strstr(note,"owns_space=0") && strstr(note,"gpu_mhz=state=P13"));
    ns_mode_changed(&game);CHECK(notes==1 && samples==1);
    ns_set_fullscreen(&game,0);CHECK(!game.full && !strcmp(pref,"0") && notes==2);
    CHECK(fake.styleMask==windowed_style && app.presentationOptions==NSApplicationPresentationDefault);
    setenv("NFS_FULLSCREEN_MODE","NATIVE",1);CHECK(app_native_fullscreen());
    ns_set_fullscreen(&game,1);
    CHECK(game.native_transition && fake.toggles==1 && notes==2);
    CHECK(fake.collectionBehavior==NSWindowCollectionBehaviorFullScreenPrimary);
    int frames=fake.frames,sizes=fake.sizes;
    ns_set_fullscreen(&game,0);ns_apply_mode(&game);ns_resize(&game);
    CHECK(fake.toggles==1 && game.full && fake.frames==frames && fake.sizes==sizes);
    CHECK(backing.drawableSize.width==1920 && backing.drawableSize.height==1080);
    [d windowWillEnterFullScreen:nil];fake.styleMask|=NSWindowStyleMaskFullScreen;[d windowDidEnterFullScreen:nil];
    CHECK(!game.native_transition && game.full && !strcmp(pref,"1") && notes==3);
    CHECK(strstr(note,"mode=Native") && strstr(note,"owns_space=1") && strstr(note,"gpu_mhz=state=P13"));
    CHECK(strstr(note,"adaptive requested/applicable=1/1") && samples==3);
    RenderPresentation ps;render_presentation_stats(&ps);
    CHECK(ps.native_fullscreen && ps.vrr_available && ps.adaptive_scheduling && ps.display_sync);
    request=0;render_presentation_stats(&ps);CHECK(!ps.vrr_requested && !ps.adaptive_scheduling);
    request=1;limit_choice=3;render_presentation_stats(&ps);CHECK(ps.vrr_requested && !ps.adaptive_scheduling);
    limit_choice=2;render_presentation_stats(&ps);CHECK(ps.adaptive_scheduling);
    screen.displayUpdateGranularity=.001;render_presentation_changed();render_presentation_stats(&ps);
    CHECK(!ps.vrr_available && !ps.adaptive_scheduling);
    screen.displayUpdateGranularity=0;screen.maximumRefreshInterval=screen.minimumRefreshInterval;
    render_presentation_changed();render_presentation_stats(&ps);CHECK(!ps.vrr_available && !ps.adaptive_scheduling);
    screen.maximumRefreshInterval=1./48;limit_choice=0;render_presentation_changed();render_presentation_stats(&ps);
    CHECK(ps.vrr_available && ps.adaptive_scheduling);
    ns_apply_mode(&game);ns_resize(&game);CHECK(fake.frames==frames && fake.sizes==sizes && fake.toggles==1);
    CHECK(!app_request_fullscreen_space(game.ns,1) && fake.toggles==1);
    NSApplicationPresentationOptions p=[d window:game.ns willUseFullScreenPresentationOptions:NSApplicationPresentationFullScreen|NSApplicationPresentationAutoHideDock|NSApplicationPresentationAutoHideMenuBar];
    CHECK((p & (NSApplicationPresentationHideDock|NSApplicationPresentationHideMenuBar|NSApplicationPresentationFullScreen))==(NSApplicationPresentationHideDock|NSApplicationPresentationHideMenuBar|NSApplicationPresentationFullScreen));
    CHECK(!(p & (NSApplicationPresentationAutoHideDock|NSApplicationPresentationAutoHideMenuBar)));
    ns_set_fullscreen(&game,0);CHECK(fake.toggles==2 && game.native_transition && notes==3);
    [d windowDidFailToExitFullScreen:game.ns];CHECK(game.full && !game.native_transition && !strcmp(pref,"1"));
    ns_set_fullscreen(&game,0);[d windowWillExitFullScreen:nil];fake.styleMask&=~NSWindowStyleMaskFullScreen;[d windowDidExitFullScreen:nil];
    CHECK(!game.full && !game.native_transition && !strcmp(pref,"0") && notes==4);
    CHECK(strstr(note,"owns_space=0") && strstr(note,"adaptive requested/applicable=1/0"));
    ns_set_fullscreen(&game,1);[d windowDidFailToEnterFullScreen:game.ns];
    CHECK(!game.full && !game.native_transition && !strcmp(pref,"0") && notes==4);
    // Cocoa-initiated green-button entry and Escape exit use the same state and preferences.
    [d windowWillEnterFullScreen:nil];fake.styleMask|=NSWindowStyleMaskFullScreen;[d windowDidEnterFullScreen:nil];
    CHECK(game.full && notes==5 && !strcmp(pref,"1"));
    [d windowWillExitFullScreen:nil];fake.styleMask&=~NSWindowStyleMaskFullScreen;[d windowDidExitFullScreen:nil];
    CHECK(!game.full && notes==6 && !strcmp(pref,"0"));
    NSApp=nil;
    printf("window failures=%d notes=%d\n",failures,notes);return failures?1:0;
}}
'''


def main():
    env = {k: v for k, v in os.environ.items() if k != 'NFS_FULLSCREEN_MODE'}
    with tempfile.TemporaryDirectory(prefix='nativefs-test-') as scratch:
        tmp = Path(scratch)

        def build(source, objc=False):
            file, exe = tmp / ('test.m' if objc else 'test.c'), tmp / 'test'
            file.write_text(source)
            command = ['clang', '-Wall', '-Wextra', '-Werror', '-g', '-fsanitize=address,undefined', '-I', str(RT), '-I', str(RT.parent / 'recomp')]
            command += ['-fobjc-arc', '-framework', 'Cocoa', '-framework', 'QuartzCore'] if objc else ['-std=c11', str(RT / 'settings.c')]
            p = subprocess.run(command + [str(file), '-o', str(exe)], capture_output=True, text=True)
            assert p.returncode == 0, p.stderr[-4000:]
            return exe

        def run(exe, *args, expected=0):
            p = subprocess.run([str(exe), *args], env=env, capture_output=True, text=True)
            assert p.returncode == expected, p.stdout + p.stderr[-4000:]
            return p.stdout.strip()

        exe = build(stubs + ROW_MAIN.replace('@ROW@', row))
        for saved, override in (('-', '-'), ('native', '-'), ('borderless', '-'), ('native', 'borderless'), ('borderless', 'native'), ('-', 'NATIVE')):
            run(exe, saved, override)
        print('PASS fullscreen row: 6 default/saved/override boots, 3-choice round trips, restart boundary')
        exe = build(stubs + ROW_MAIN.replace('@ROW@', row.replace('.restart = 1', '.restart = 0')))
        print('PASS restart-disabled mutant rejected:', run(exe, '-', '-', expected=1))
        source = WINDOW.replace('@POLICY@', policy).replace('@MODE@', mode).replace('@RESIZE@', resize).replace('@CALLBACKS@', callbacks).replace('@PRESENTER@', presenter).replace('@PRESENTATION_TYPE@', presentation_type)
        print('PASS fake native window:', run(build(source, True)))
        for old, new in (('[window toggleFullScreen:nil];', '(void)window;'),
                         ('window.collectionBehavior = NSWindowCollectionBehaviorFullScreenPrimary;', 'window.collectionBehavior = NSWindowCollectionBehaviorFullScreenNone;')):
            assert policy.count(old) == 1
            mutant = source.replace(policy, policy.replace(old, new))
            print('PASS native request mutant rejected:', run(build(mutant, True), expected=1))
        mutation = 'void render_presentation_changed(void) { next_display = 0; }'
        assert source.count(mutation) == 1
        print('PASS stale presentation mutant rejected:', run(build(source.replace(mutation, 'void render_presentation_changed(void) {}'), True), expected=1))


if __name__ == '__main__':
    main()
