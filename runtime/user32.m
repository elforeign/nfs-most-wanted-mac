// user32.m - USER32 (and the few GDI32 calls) on Cocoa.
//
// One NSWindow per game window, with a CAMetalLayer-backed content view that the D3D9 layer renders into.
// Cocoa events are pumped on the guest's main thread from PeekMessageA/GetMessageA and turned into Win32
// messages (keys, mouse, activation, close) in a queue; DispatchMessageA calls the guest's WndProc.
// BOOT_HEADLESS=1 skips Cocoa entirely (windows exist only as records) for scripted runs.
//
// The game sees a window exactly the size of its back buffer. On the Mac it's either full screen (a borderless
// window over the whole screen, Dock and menu bar hidden) or an ordinary resizable window, and the layer shows the
// back buffer scaled to fit; the green button, Window > Zoom and View > Enter Full Screen (Ctrl-Cmd-F) switch. The
// first mode is the game's own choice (a captionless window is its full-screen mode) from a terminal, the player's
// last choice in NFSMW.app (USERDATA/app.txt, full screen the first time); NFS_FULLSCREEN=0|1 overrides both.
#import <Cocoa/Cocoa.h>
#import <QuartzCore/CAMetalLayer.h>
#import <objc/runtime.h>
#import <GameController/GameController.h>
#include <pthread.h>

#include "host.h"

// ---------------- window records ----------------
#define HWND_DESKTOP_H 0x00010000u
#define HWND_BASE 0x00010010u
#define MAX_WINDOWS 16
#define MAX_CLASSES 16
typedef struct {
    char name[64];
    uint32_t wndproc, style, hinstance, atom;
} WinClass;
typedef struct {
    int used;
    uint32_t wndproc, style, exstyle, userdata, cls;
    int x, y, w, h;       // outer rect, screen coordinates
    int cw, ch;           // client size
    int visible;
    int full;             // shown full screen (the Mac's choice, ns_fullscreen)
    NSWindow *ns;
    NSView *view;
} Win;
static WinClass classes[MAX_CLASSES];
static int class_count;
static Win wins[MAX_WINDOWS];
static int headless = -1;
static int screen_w = 1920, screen_h = 1080, screen_top_inset;   // points; the inset is the camera housing's
static double screen_scale = 1;
static uint32_t focus_hwnd, capture_hwnd;
static int cursor_count;
static uint8_t key_down[256], key_toggled[256], key_scan[256];
static int window_occluded;     // the game window isn't on screen (windowDidChangeOcclusionState)
static int mouse_x, mouse_y;   // screen coordinates

static Win *win(uint32_t h) {
    uint32_t i = (h - HWND_BASE) / 4;
    return (h >= HWND_BASE && i < MAX_WINDOWS && wins[i].used) ? &wins[i] : NULL;
}
static uint32_t hwnd_of(Win *w) { return HWND_BASE + 4 * (uint32_t)(w - wins); }

// Frame of a WS_CAPTION window, Windows XP classic metrics (what AdjustWindowRect adds).
static void frame_insets(uint32_t style, int *l, int *t, int *r, int *b) {
    *l = *t = *r = *b = 0;
    if (style & 0x00C00000) { *l = *r = *b = 3; *t = 22; }          // WS_CAPTION
    else if (style & 0x00800000) { *l = *t = *r = *b = 1; }          // WS_BORDER
}

// ---------------- message queue ----------------
typedef struct { uint32_t hwnd, msg, wp, lp, time; int px, py; } Msg;
#define QCAP 1024
static Msg q[QCAP];
static int qhead, qlen;
static pthread_mutex_t qlock = PTHREAD_MUTEX_INITIALIZER;   // Cocoa callbacks can post while wait_messages runs without the GIL
static void post(uint32_t hwnd, uint32_t msg, uint32_t wp, uint32_t lp) {
    pthread_mutex_lock(&qlock);
    if (qlen < QCAP) {                                                // dropped when full, like a hung app
        Msg *m = &q[(qhead + qlen++) % QCAP];
        m->hwnd = hwnd; m->msg = msg; m->wp = wp; m->lp = lp;
        m->time = (uint32_t)(host_ticks() / 1000000ull);
        m->px = mouse_x; m->py = mouse_y;
    }
    pthread_mutex_unlock(&qlock);
}

static uint32_t call_wndproc(Cpu *c, uint32_t hwnd, uint32_t msg, uint32_t wp, uint32_t lp) {
    Win *w = win(hwnd);
    if (!w || !w->wndproc) return 0;
    uint32_t a[4] = {hwnd, msg, wp, lp};
    return call_guest(c, w->wndproc, 4, a);
}

static void release_keys(uint32_t hwnd);
static void ns_set_fullscreen(Win *w, int full);
static int cursor_hidden;               // ShowCursor count < 0: no system cursor over the game (it draws its own)

// ---------------- Cocoa ----------------
@interface GameWindow : NSWindow       // borderless while full screen, and still the key window
@end
@implementation GameWindow
- (BOOL)canBecomeKeyWindow { return YES; }
- (BOOL)canBecomeMainWindow { return YES; }
@end

@interface GameView : NSView
@end
@implementation GameView
- (BOOL)acceptsFirstResponder { return YES; }
- (BOOL)isFlipped { return YES; }
- (void)keyDown:(NSEvent *)e { (void)e; }      // handled in the pump; swallow so Cocoa doesn't beep
- (void)keyUp:(NSEvent *)e { (void)e; }
- (void)flagsChanged:(NSEvent *)e { (void)e; }
- (CALayer *)makeBackingLayer { return [CAMetalLayer layer]; }
// The drawable stays the back buffer's size (d3d9.m sets it), whatever size the view is.
- (void)keepDrawable:(void (^)(void))change {
    CAMetalLayer *l = (CAMetalLayer *)self.layer;
    CGSize d = l.drawableSize;
    change();
    if (d.width > 0 && d.height > 0) l.drawableSize = d;
}
- (void)setFrameSize:(NSSize)s { [self keepDrawable:^{ [super setFrameSize:s]; }]; }
- (void)viewDidChangeBackingProperties { [self keepDrawable:^{ [super viewDidChangeBackingProperties]; }]; }
- (void)resetCursorRects {
    static NSCursor *blank;
    if (!blank) blank = [[NSCursor alloc] initWithImage:[[NSImage alloc] initWithSize:NSMakeSize(16, 16)] hotSpot:NSZeroPoint];
    int portmenu_is_open(void);
    [self addCursorRect:self.bounds cursor:cursor_hidden && !portmenu_is_open() ? blank : NSCursor.arrowCursor];
}
@end

@interface GameWindowDelegate : NSObject <NSWindowDelegate>
@property uint32_t hwnd;
@end
@implementation GameWindowDelegate
- (BOOL)windowShouldClose:(NSWindow *)sender { (void)sender; post(self.hwnd, 0x0010, 0, 0); return NO; }  // WM_CLOSE
- (void)windowDidBecomeKey:(NSNotification *)n {
    (void)n;
    feedback_poll_pause();
    feedback_suppress(FEEDBACK_FOCUS, 0);
    if (diag_on) diag_event("window focused");
    fprintf(stderr, "boot: window focused (app %s)\n", NSApp.isActive ? "active" : "inactive");
    post(self.hwnd, 0x001C, 1, 0);       // WM_ACTIVATEAPP
    post(self.hwnd, 0x0006, 1, 0);       // WM_ACTIVATE (WA_ACTIVE)
    post(self.hwnd, 0x0007, 0, 0);       // WM_SETFOCUS
}
// n is nil for BOOT_KEYS unfocus:/hide: steps, which always pause. A real focus loss pauses the game in NFSMW.app;
// runtime/boot keeps running in the background for scripted runs (NFS_PAUSE_INACTIVE=0|1 overrides).
- (void)windowDidResignKey:(NSNotification *)n {
    if (diag_on) diag_event("window lost focus");
    feedback_suppress(FEEDBACK_FOCUS, 1);
    release_keys(self.hwnd);
    const char *e = getenv("NFS_PAUSE_INACTIVE");
    if (n && !(e && *e ? atoi(e) : app_bundled)) return;
    fprintf(stderr, "boot: window lost focus: the game pauses until it's back\n");
    padfx_stop();
    post(self.hwnd, 0x0006, 0, 0);
    post(self.hwnd, 0x001C, 0, 0);
    post(self.hwnd, 0x0008, 0, 0);       // WM_KILLFOCUS
}
// A window that isn't on screen (covered, hidden, minimized, another Space) gets no drawables from its layer until
// the compositor shows it again: render_present.m skips presenting meanwhile instead of blocking in nextDrawable.
- (void)windowDidChangeOcclusionState:(NSNotification *)n {
    NSWindow *w = n.object;
    window_occluded = !(w.occlusionState & NSWindowOcclusionStateVisible);
    if (diag_on) diag_event("window %s", window_occluded ? "occluded" : "visible");
}
// The green button (and Window > Zoom) switches to full screen rather than zooming.
- (BOOL)windowShouldZoom:(NSWindow *)window toFrame:(NSRect)frame {
    (void)window; (void)frame;
    Win *w = win(self.hwnd);
    if (w) ns_set_fullscreen(w, 1);
    return NO;
}
@end

static int test_px_w, test_px_h;   // NFS_TEST_DISPLAY_CHANGE: the pretend display, in pixels
static CFAbsoluteTime screen_changed_at;
static int screen_dirty;
static void read_main_screen(void) {
    NSScreen *s = NSScreen.mainScreen;
    screen_w = (int)s.frame.size.width;
    screen_h = (int)s.frame.size.height;
    screen_scale = s.backingScaleFactor;
    screen_top_inset = 0;
    if (@available(macOS 12.0, *)) screen_top_inset = (int)s.safeAreaInsets.top;   // full screen stays below it
}
static void cocoa_init(void) {
    if (headless < 0) headless = getenv("BOOT_HEADLESS") != NULL;
    static int done;
    if (done || headless) return;
    done = 1;
    app_cocoa_init();
    @autoreleasepool {
        read_main_screen();
        // The main display can change under a running game (a TV waking up, a cable, mirroring): note when, and
        // user32_display_poll (every frame) acts once the burst of notifications is over.
        [NSNotificationCenter.defaultCenter addObserverForName:NSApplicationDidChangeScreenParametersNotification object:nil
                                                         queue:nil usingBlock:^(NSNotification *n) { screen_changed_at = CFAbsoluteTimeGetCurrent(); screen_dirty = 1; }];
        // GameController.framework finds controllers asynchronously (~100 ms of run loop for a DualSense): give discovery
        // a moment so a pad already connected names the game's gamepad from the start. One that connects later (asleep at
        // launch, plugged in, reconnecting) is picked up by pad_read whenever it arrives.
        for (int i = 0; i < 25 && !GCController.controllers.count; ++i)
            [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.02]];
    }
}

// macOS virtual key code -> Windows VK and set-1 scan code.
typedef struct { uint8_t vk, scan; } KeyMap;
static const KeyMap mac_keys[128] = {
    [0x00] = {'A', 0x1E}, [0x01] = {'S', 0x1F}, [0x02] = {'D', 0x20}, [0x03] = {'F', 0x21}, [0x04] = {'H', 0x23},
    [0x05] = {'G', 0x22}, [0x06] = {'Z', 0x2C}, [0x07] = {'X', 0x2D}, [0x08] = {'C', 0x2E}, [0x09] = {'V', 0x2F},
    [0x0B] = {'B', 0x30}, [0x0C] = {'Q', 0x10}, [0x0D] = {'W', 0x11}, [0x0E] = {'E', 0x12}, [0x0F] = {'R', 0x13},
    [0x10] = {'Y', 0x15}, [0x11] = {'T', 0x14}, [0x12] = {'1', 0x02}, [0x13] = {'2', 0x03}, [0x14] = {'3', 0x04},
    [0x15] = {'4', 0x05}, [0x16] = {'6', 0x07}, [0x17] = {'5', 0x06}, [0x18] = {0xBB, 0x0D}, [0x19] = {'9', 0x0A},
    [0x1A] = {'7', 0x08}, [0x1B] = {0xBD, 0x0C}, [0x1C] = {'8', 0x09}, [0x1D] = {'0', 0x0B}, [0x1E] = {0xDD, 0x1B},
    [0x1F] = {'O', 0x18}, [0x20] = {'U', 0x16}, [0x21] = {0xDB, 0x1A}, [0x22] = {'I', 0x17}, [0x23] = {'P', 0x19},
    [0x24] = {0x0D, 0x1C}, [0x25] = {'L', 0x26}, [0x26] = {'J', 0x24}, [0x27] = {0xDE, 0x28}, [0x28] = {'K', 0x25},
    [0x29] = {0xBA, 0x27}, [0x2A] = {0xDC, 0x2B}, [0x2B] = {0xBC, 0x33}, [0x2C] = {0xBF, 0x35}, [0x2D] = {'N', 0x31},
    [0x2E] = {'M', 0x32}, [0x2F] = {0xBE, 0x34}, [0x30] = {0x09, 0x0F}, [0x31] = {0x20, 0x39}, [0x32] = {0xC0, 0x29},
    [0x33] = {0x08, 0x0E}, [0x35] = {0x1B, 0x01}, [0x37] = {0x5B, 0x5B}, [0x38] = {0x10, 0x2A}, [0x39] = {0x14, 0x3A},
    [0x3A] = {0x12, 0x38}, [0x3B] = {0x11, 0x1D}, [0x3C] = {0x10, 0x36}, [0x3D] = {0x12, 0x38}, [0x3E] = {0x11, 0x1D},
    [0x41] = {0x6E, 0x53}, [0x43] = {0x6A, 0x37}, [0x45] = {0x6B, 0x4E}, [0x4B] = {0x6F, 0x35}, [0x4C] = {0x0D, 0x1C},
    [0x4E] = {0x6D, 0x4A}, [0x52] = {0x60, 0x52}, [0x53] = {0x61, 0x4F}, [0x54] = {0x62, 0x50}, [0x55] = {0x63, 0x51},
    [0x56] = {0x64, 0x4B}, [0x57] = {0x65, 0x4C}, [0x58] = {0x66, 0x4D}, [0x59] = {0x67, 0x47}, [0x5B] = {0x68, 0x48},
    [0x5C] = {0x69, 0x49}, [0x60] = {0x74, 0x3F}, [0x61] = {0x75, 0x40}, [0x62] = {0x76, 0x41}, [0x63] = {0x72, 0x3D},
    [0x64] = {0x77, 0x42}, [0x65] = {0x78, 0x43}, [0x67] = {0x7A, 0x57}, [0x6D] = {0x79, 0x44}, [0x6F] = {0x7B, 0x58},
    [0x72] = {0x2D, 0x52}, [0x73] = {0x24, 0x47}, [0x74] = {0x21, 0x49}, [0x75] = {0x2E, 0x53}, [0x76] = {0x73, 0x3E},
    [0x77] = {0x23, 0x4F}, [0x78] = {0x71, 0x3C}, [0x79] = {0x22, 0x51}, [0x7A] = {0x70, 0x3B}, [0x7B] = {0x25, 0x4B},
    [0x7C] = {0x27, 0x4D}, [0x7D] = {0x28, 0x50}, [0x7E] = {0x26, 0x48},
};
static int vk_extended(uint8_t vk) { return (vk >= 0x21 && vk <= 0x2E) || vk == 0x6F || vk == 0x5B; }

// ---- DirectInput feed (dinput.c): scan-code state, a buffered event ring, relative mouse motion ----
uint8_t di_keys[256];
typedef struct { uint32_t ofs, data, time, seq; } DiEvent;
static DiEvent di_ring[256];
static unsigned di_head, di_tail, di_seq;
int di_mouse_dx, di_mouse_dy, di_mouse_dz;
uint8_t di_mouse_btn[8];
static void di_event(uint8_t dik, int down) {
    di_keys[dik] = down ? 0x80 : 0;
    DiEvent *e = &di_ring[di_head++ & 255];
    e->ofs = dik; e->data = down ? 0x80 : 0; e->time = (uint32_t)(host_ticks() / 1000000ull); e->seq = ++di_seq;
    if (di_head - di_tail > 256) di_tail = di_head - 256;
}
int user32_di_next(uint32_t *ofs, uint32_t *data, uint32_t *time, uint32_t *seq) {
    if (di_tail == di_head) return 0;
    DiEvent *e = &di_ring[di_tail++ & 255];
    *ofs = e->ofs; *data = e->data; *time = e->time; *seq = e->seq;
    return 1;
}
int user32_di_pending(void) { return (int)(di_head - di_tail); }
void user32_pump(void);

void prompts_input(int pad);              // padprompts.c: which input was used last (button prompts)
void prompts_mouse_moved(float points);
void prompts_pad_seen(int connected);
// portmenu.m: the F10 settings menu takes F10 always, and every key, character, mouse event and pad read while open.
int portmenu_key(uint8_t vk, int down, int repeat);
int portmenu_char(uint32_t ch);
int portmenu_mouse(int kind, double x, double y, double dy);
void portmenu_update(void);
uint32_t portmenu_pad(uint32_t phys, int up, int down, int left, int right, float lx, float ly, int home, int touchpad, int has_touchpad,
                      uint32_t *inject);
int settings_get(const char *id);
static void key_event(uint32_t hwnd, uint8_t vk, uint8_t scan, int down, int repeat) {
    if (!vk) return;
    if (portmenu_key(vk, down, repeat)) return;
    if (down && !key_down[vk]) prompts_input(0);
    key_scan[vk] = scan;
    if (!(down && key_down[vk])) di_event((uint8_t)(scan | (vk_extended(vk) ? 0x80 : 0)), down);
    uint32_t lp = 1 | (uint32_t)scan << 16 | (uint32_t)vk_extended(vk) << 24;
    if (down) { if (key_down[vk]) lp |= 1u << 30; else key_toggled[vk] ^= 1; }
    else lp |= 3u << 30;
    (void)repeat;
    key_down[vk] = (uint8_t)down;
    post(hwnd, vk == 0x12 ? (down ? 0x0104 : 0x0105) : (down ? 0x0100 : 0x0101), vk, lp);   // WM_(SYS)KEYDOWN/UP
}

// On focus loss the key-ups of keys still held (Cmd-Tab while accelerating) go to the app that takes over, so they
// are released here: on Windows the game would see them up after reacquiring its foreground DirectInput devices.
static void release_keys(uint32_t hwnd) {
    for (int vk = 3; vk < 256; ++vk) if (key_down[vk]) key_event(hwnd, (uint8_t)vk, key_scan[vk], 0, 0);
    key_down[0x01] = key_down[0x02] = 0;
    di_mouse_btn[0] = di_mouse_btn[1] = 0;
}
int user32_window_occluded(void) { return window_occluded; }

static void view_to_client(const Win *w, NSPoint p, int *x, int *y);
static void pump(int wait) {
    if (headless) return;
    // Scripted runs (BOOT_KEYS) ignore the physical keyboard and mouse: the window opens on a screen someone may be
    // using, and their keystrokes or pointer over the window would change the menus under the script.
    static int scripted = -1;
    if (scripted < 0) scripted = getenv("BOOT_KEYS") != NULL;
    @autoreleasepool {
        for (;;) {
            NSEvent *e = [NSApp nextEventMatchingMask:NSEventMaskAny
                                            untilDate:wait ? [NSDate dateWithTimeIntervalSinceNow:0.05] : [NSDate distantPast]
                                               inMode:NSDefaultRunLoopMode
                                              dequeue:YES];
            if (!e) break;
            wait = 0;
            uint32_t hwnd = focus_hwnd;
            Win *w = win(hwnd);
            const NSEventMask input = NSEventMaskKeyDown | NSEventMaskKeyUp | NSEventMaskFlagsChanged | NSEventMaskMouseMoved |
                NSEventMaskLeftMouseDragged | NSEventMaskRightMouseDragged | NSEventMaskLeftMouseDown | NSEventMaskLeftMouseUp |
                NSEventMaskRightMouseDown | NSEventMaskRightMouseUp;
            if (scripted && (NSEventMaskFromType(e.type) & input)) continue;
            switch (e.type) {
            case NSEventTypeKeyDown:
            case NSEventTypeKeyUp: {
                if ((e.modifierFlags & NSEventModifierFlagCommand) && e.type == NSEventTypeKeyDown) break;  // Cmd-Q etc.
                KeyMap k = e.keyCode < 128 ? mac_keys[e.keyCode] : (KeyMap){0, 0};
                key_event(hwnd, k.vk, k.scan, e.type == NSEventTypeKeyDown, e.isARepeat);
                if (e.type == NSEventTypeKeyDown) {
                    NSString *s = e.characters;
                    for (NSUInteger i = 0; i < s.length; ++i) {
                        unichar ch = [s characterAtIndex:i];
                        if (ch == 0x7F) ch = 8;                       // backspace
                        if (ch < 0xF700 && !portmenu_char(ch)) post(hwnd, 0x0102, ch < 256 ? ch : '?', 1);   // WM_CHAR, Latin-1
                    }
                }
                continue;                                             // not forwarded: no beeps, no menu shortcuts
            }
            case NSEventTypeFlagsChanged: {
                KeyMap k = e.keyCode < 128 ? mac_keys[e.keyCode] : (KeyMap){0, 0};
                NSEventModifierFlags f = e.modifierFlags;
                int down = k.vk == 0x10 ? !!(f & NSEventModifierFlagShift) : k.vk == 0x11 ? !!(f & NSEventModifierFlagControl)
                         : k.vk == 0x12 ? !!(f & NSEventModifierFlagOption) : k.vk == 0x14 ? !!(f & NSEventModifierFlagCapsLock) : 0;
                key_event(hwnd, k.vk, k.scan, down, 0);
                break;
            }
            case NSEventTypeMouseMoved:
            case NSEventTypeLeftMouseDragged:
            case NSEventTypeRightMouseDragged:
            case NSEventTypeLeftMouseDown:
            case NSEventTypeLeftMouseUp:
            case NSEventTypeRightMouseDown:
            case NSEventTypeRightMouseUp:
                if (w && w->view && e.window == w->ns) {
                    NSPoint p = [w->view convertPoint:e.locationInWindow fromView:nil];
                    int kind = e.type == NSEventTypeLeftMouseDown ? 1 : e.type == NSEventTypeLeftMouseUp ? 2
                             : e.type == NSEventTypeLeftMouseDragged ? 3 : e.type == NSEventTypeMouseMoved ? 0 : -1;
                    if (portmenu_mouse(kind < 0 ? 0 : kind, p.x, p.y, 0)) break;   // the menu's while it's open
                    int l, t, r, b, cx, cy;
                    frame_insets(w->style, &l, &t, &r, &b);
                    view_to_client(w, p, &cx, &cy);
                    mouse_x = w->x + l + cx;
                    mouse_y = w->y + t + cy;
                    uint32_t lp = ((uint32_t)(int16_t)cy << 16) | (uint16_t)(int16_t)cx;
                    uint32_t mk = (key_down[0x01] ? 1 : 0) | (key_down[0x02] ? 2 : 0);
                    uint32_t msg = 0x0200;                            // WM_MOUSEMOVE
                    di_mouse_dx += (int)e.deltaX; di_mouse_dy += (int)e.deltaY;
                    prompts_mouse_moved(fabs(e.deltaX) + fabs(e.deltaY));
                    if (e.type == NSEventTypeLeftMouseDown || e.type == NSEventTypeRightMouseDown) prompts_input(0);
                    if (e.type == NSEventTypeLeftMouseDown || e.type == NSEventTypeLeftMouseUp) di_mouse_btn[0] = e.type == NSEventTypeLeftMouseDown ? 0x80 : 0;
                    if (e.type == NSEventTypeRightMouseDown || e.type == NSEventTypeRightMouseUp) di_mouse_btn[1] = e.type == NSEventTypeRightMouseDown ? 0x80 : 0;
                    if (e.type == NSEventTypeLeftMouseDown) { msg = 0x0201; key_down[0x01] = 1; }
                    if (e.type == NSEventTypeLeftMouseUp) { msg = 0x0202; key_down[0x01] = 0; }
                    if (e.type == NSEventTypeRightMouseDown) { msg = 0x0204; key_down[0x02] = 1; }
                    if (e.type == NSEventTypeRightMouseUp) { msg = 0x0205; key_down[0x02] = 0; }
                    post(hwnd, msg, mk, lp);
                }
                break;
            case NSEventTypeScrollWheel:
                if (w && w->view && e.window == w->ns) {
                    NSPoint p = [w->view convertPoint:e.locationInWindow fromView:nil];
                    double dy = e.hasPreciseScrollingDeltas ? e.scrollingDeltaY / 20.0 : e.scrollingDeltaY;
                    if (fabs(dy) >= 0.5) portmenu_mouse(4, p.x, p.y, dy);
                }
                break;
            default:
                break;
            }
            [NSApp sendEvent:e];
        }
        portmenu_update();
    }
}

// Full screen, the window covers the screen (below the camera housing on screens that have one) and the layer scales
// the back buffer to it keeping its aspect (black bars when they differ). Windowed, it shows its client area pixel for
// pixel (points = pixels / backing scale), shrunk to fit the screen if it's larger. The client area stays in
// back-buffer pixels for the game (GetClientRect, mouse messages).
static const NSWindowStyleMask windowed_style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                                                NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable;
static int ns_fullscreen(const Win *w) { return w->full; }
static NSRect ns_content_rect(const Win *w) {
    NSScreen *s = [NSScreen mainScreen];
    if (ns_fullscreen(w)) {
        NSRect f = s.frame;
        if (@available(macOS 12.0, *)) f.size.height -= s.safeAreaInsets.top;
        return f;
    }
    CGFloat k = s.backingScaleFactor;
    NSRect max = [NSWindow contentRectForFrameRect:s.visibleFrame styleMask:windowed_style];
    double fit = fmin(1.0, fmin(max.size.width * k / w->cw, max.size.height * k / w->ch));
    return NSMakeRect(0, 0, floor(w->cw * fit / k), floor(w->ch * fit / k));
}
static void view_to_client(const Win *w, NSPoint p, int *x, int *y) {   // view points -> back-buffer pixels
    NSSize v = w->view.bounds.size;
    double k = fmin(v.width / w->cw, v.height / w->ch);                   // points per pixel, aspect fit
    *x = (int)lround((p.x - (v.width - w->cw * k) / 2) / k);
    *y = (int)lround((p.y - (v.height - w->ch * k) / 2) / k);
}
static int want_fullscreen(const Win *w) {
    const char *e = getenv("NFS_FULLSCREEN");
    if (e && *e) return atoi(e) != 0;
    if (!app_bundled) return !(w->style & 0x00C00000);                 // the game's choice: no caption = full screen
    const char *p = app_pref("fullscreen");
    return !p || atoi(p);
}
// Puts the window in the current mode: frame, style, Dock and menu bar.
static void ns_apply_mode(Win *w) {
    NSRect r = ns_content_rect(w);
    if (ns_fullscreen(w)) {
        w->ns.styleMask = NSWindowStyleMaskBorderless;
        [w->ns setFrame:r display:YES];
    } else {
        w->ns.styleMask = windowed_style;
        w->ns.contentAspectRatio = NSMakeSize(w->cw, w->ch);
        [w->ns setContentSize:r.size];
        [w->ns center];
    }
    if (w->visible) NSApp.presentationOptions = ns_fullscreen(w) ? NSApplicationPresentationHideDock | NSApplicationPresentationHideMenuBar
                                                                 : NSApplicationPresentationDefault;
    [w->ns makeFirstResponder:w->view];
}
static void ns_set_fullscreen(Win *w, int full) {
    if (!w->ns || w->full == full) return;
    w->full = full;
    ns_apply_mode(w);
    if (app_bundled) app_pref_set("fullscreen", full ? "1" : "0");
}
static void ns_close(Win *w) {
    if (!w->ns) return;
    w->ns.delegate = nil;
    [w->ns close];
    NSApp.presentationOptions = NSApplicationPresentationDefault;
}

static void ns_create(Win *w, const char *title) {
    if (headless) return;
    @autoreleasepool {
        w->full = want_fullscreen(w);
        NSRect r = ns_content_rect(w);
        w->ns = [[GameWindow alloc] initWithContentRect:r styleMask:ns_fullscreen(w) ? NSWindowStyleMaskBorderless : windowed_style
                                                backing:NSBackingStoreBuffered defer:NO];
        w->ns.releasedWhenClosed = NO;
        NSString *t = title && *title ? [NSString stringWithCString:title encoding:NSWindowsCP1252StringEncoding] : nil;
        w->ns.title = t ? t : @"Need for Speed Most Wanted";
        w->ns.acceptsMouseMovedEvents = YES;
        w->ns.collectionBehavior = NSWindowCollectionBehaviorFullScreenNone;   // full screen is ours (no Space of its own)
        w->ns.backgroundColor = NSColor.blackColor;
        GameWindowDelegate *d = [GameWindowDelegate new];
        d.hwnd = hwnd_of(w);
        w->ns.delegate = d;
        objc_setAssociatedObject(w->ns, "delegate", d, OBJC_ASSOCIATION_RETAIN);
        GameView *v = [[GameView alloc] initWithFrame:NSMakeRect(0, 0, r.size.width, r.size.height)];
        v.wantsLayer = YES;
        w->ns.contentView = v;
        w->view = v;
        CAMetalLayer *layer = (CAMetalLayer *)v.layer;
        layer.drawableSize = CGSizeMake(w->cw, w->ch);
        layer.opaque = YES;             // D3D9 ignores the back buffer's alpha when presenting (most draws don't write it)
        layer.contentsGravity = kCAGravityResizeAspect;
        layer.backgroundColor = CGColorGetConstantColor(kCGColorBlack);
        ns_apply_mode(w);
    }
}
static void ns_show(Win *w, int show) {
    w->visible = show;
    if (!w->ns) return;
    if (show) {
        if (ns_fullscreen(w)) NSApp.presentationOptions = NSApplicationPresentationHideDock | NSApplicationPresentationHideMenuBar;
        if (getenv("BOOT_BACKGROUND")) { [w->ns orderBack:nil]; [w->ns makeFirstResponder:w->view]; }   // test runs: behind, never key (app.m)
        else { [w->ns makeKeyAndOrderFront:nil]; [w->ns makeFirstResponder:w->view]; [NSApp activateIgnoringOtherApps:YES]; }
    }
    else [w->ns orderOut:nil];
}
static void ns_resize(Win *w) {           // the game changed resolution
    if (!w->ns) return;
    if (ns_fullscreen(w)) [w->ns setFrame:ns_content_rect(w) display:YES];
    else { w->ns.contentAspectRatio = NSMakeSize(w->cw, w->ch); [w->ns setContentSize:ns_content_rect(w).size]; }
    ((CAMetalLayer *)w->view.layer).drawableSize = CGSizeMake(w->cw, w->ch);
}
// app.m's View > Enter Full Screen (Ctrl-Cmd-F).
static Win *game_window(void) {
    for (int i = 0; i < MAX_WINDOWS; ++i) if (wins[i].used && wins[i].wndproc && wins[i].ns) return &wins[i];
    return NULL;
}
int user32_fullscreen(void) { Win *w = game_window(); return w && ns_fullscreen(w); }
void user32_toggle_fullscreen(void) { Win *w = game_window(); if (w) ns_set_fullscreen(w, !w->full); }
// portmenu.m: before the menu takes the input, keys and mouse buttons held now go up for the game (as on focus loss).
void user32_menu_opening(void) { release_keys(focus_hwnd); }
// padprompts.c: the controller is the input being used: no pointer until the mouse moves (it comes back with the first
// movement, which is also what hands the prompts back to the keyboard and mouse; F21).
void user32_hide_pointer(void) {
    if (headless > 0) return;
    if (NSThread.isMainThread) [NSCursor setHiddenUntilMouseMoves:YES];
    else dispatch_async(dispatch_get_main_queue(), ^{ [NSCursor setHiddenUntilMouseMoves:YES]; });
}
void user32_menu_cursor(void) {        // the system arrow over the game while the menu is open
    Win *w = game_window();
    if (w && w->ns && w->view) [w->ns invalidateCursorRectsForView:w->view];
}
int user32_request_close(void) {
    Win *w = game_window();
    if (w) { rumble_close(); padfx_close(); post(hwnd_of(w), 0x0010, 0, 0); }   // WM_CLOSE: the game's own exit path
    return w != NULL;
}

void user32_pump(void) { pump(0); }

// BOOT_KEYS=frame:KEY[,frame:KEY...]: scripted key presses (down at `frame`, up 4 frames later) for runs without a
// keyboard. KEY: RETURN ESCAPE SPACE BACK UP DOWN LEFT RIGHT F10 TAB, or a letter/digit (also typed, as WM_CHAR, like a real
// keyboard: name entry reads characters).
static void press(const char *name, int down, uint64_t frame) {
    static const struct { const char *n; uint8_t vk, scan; } keys[] = {
        {"RETURN", 0x0D, 0x1C}, {"ESCAPE", 0x1B, 0x01}, {"SPACE", 0x20, 0x39}, {"UP", 0x26, 0x48}, {"DOWN", 0x28, 0x50},
        {"LEFT", 0x25, 0x4B}, {"RIGHT", 0x27, 0x4D}, {"BACK", 0x08, 0x0E}, {"F10", 0x79, 0x44}, {"TAB", 0x09, 0x0F}};
    uint8_t vk = 0, scan = 0;
    for (unsigned k = 0; k < sizeof keys / sizeof *keys; ++k) if (!strcmp(keys[k].n, name)) { vk = keys[k].vk; scan = keys[k].scan; }
    if (!vk && strlen(name) == 1) for (int k = 0; k < 128; ++k) if (mac_keys[k].vk == (uint8_t)toupper(name[0])) { vk = mac_keys[k].vk; scan = mac_keys[k].scan; }
    if (!vk) return;
    key_event(focus_hwnd, vk, scan, down, 0);
    if (down && (vk == 0x0D || vk == 0x20 || vk == 0x1B || vk == 0x08)) { if (!portmenu_char(vk)) post(focus_hwnd, 0x0102, vk, 1); }
    else if (down && strlen(name) == 1) { if (!portmenu_char((uint8_t)name[0])) post(focus_hwnd, 0x0102, (uint8_t)name[0], 1); }
    fprintf(stderr, "boot: scripted key %s %s at frame %llu\n", name, down ? "down" : "up", (unsigned long long)frame);
    if (diag_on) diag_event("scripted key %s %s", name, down ? "down" : "up");
    void diag_scripted_key(uint64_t frame);
    if (down) diag_scripted_key(frame);                     // BOOT_DIAG_DRAWS/SHOTS key+N
}

static const char *fe_package_in_control(void);
static int sim_kind = -1;                  // BOOT_PAD=sim's identity word, or BOOT_KEYS padtype:
static int kind_of_word(const char *w) {
    return !strcasecmp(w, "dualsense") || !strcasecmp(w, "ds5") ? PAD_KIND_DUALSENSE :
           !strcasecmp(w, "ds4") || !strcasecmp(w, "dualshock4") ? PAD_KIND_DUALSHOCK4 :
           !strcasecmp(w, "edge") ? PAD_KIND_EDGE : !strcasecmp(w, "xbox") ? PAD_KIND_OTHER : -1;
}
static uint32_t script_pad, script_raw;   // BOOT_KEYS pad:MASK / padraw:MASK, held (with BOOT_PAD=sim)
static int script_trigger_pair;
static float script_lt, script_rt;
static unsigned script_trigger_step;
unsigned user32_pad_trigger_step(float *lt, float *rt) {
    *lt = script_lt; *rt = script_rt;
    return script_trigger_step;
}
static int script_layout = -1;             // padlayout: changes this run only, never app.txt
static char script_axis[32];               // BOOT_KEYS padaxis:NAME=V (lx ly rx ry lt rt), held
// BOOT_KEYS=seq:step,step,...  steps run in order: KEY (press, release 4 ticks later), pad:MASK (BOOT_PAD=sim only: the
// physical buttons in MASK, as BOOT_PAD's buttons= (bit 12 PS/Guide, 13 touchpad click), held 6 ticks; padraw:MASK the game's DirectInput buttons, after the
// layout; padaxis:NAME=V one axis, after the layout (a stick also before it, as a real stick); each takes :N to be held
// N ticks), padtriggers:LT/RT:N (both physical pressures BEFORE layout, cleared on release),
// padlayout:xbox|playstation (run-only layout), padtype:WORD (BOOT_PAD=sim's identity from then on), pkg:NAME
// (until the FE package in control starts with NAME, e.g. pkg:MainMenu.fng; at most 30 s; pkg:NAME:KEY presses KEY every
// 150 ticks meanwhile), wait:N (N ticks), sim:S (until the race's sim time, Sim::GetTime since the race started,
// reaches S seconds: steps keyed to the sim stay in step with BOOT_DIAG_SIMSHOTS and the probe's held passes, which
// stop the sim while ticks go on), idle:N (until
// the draw count has stayed the same for N ticks, i.e. the screen has settled; at most 20*N ticks), unfocus:N / hide:N
// (focus loss for N seconds, below), race:N (N ticks after the race state, game-flow 6, began), cmdopt:K (Cmd-Option-K through the menu bar, e.g. cmdopt:p cycles the performance
// overlay). A tick is 1/60 s of real time, so scripts behave the same at any frame rate
// (they were written in frames at 60 fps).
static void script_seq(const char *spec, uint64_t real_frame, uint64_t draws) {
    static int step = 0;
    static uint64_t step_start = 0, last_draws = ~0ull, same_since = 0, release_at = 0;
    static char held[16];
    // script clock: 1/60 s ticks of wall time; in a race with NFS_LOCKSTEP one tick of the sim rate per frame, so a slow
    // frame can't shorten a step (pacing.c)
    int pacing_lockstep_now(void), pacing_sim_hz(void);
    static uint64_t wall_ns, prev_ns, lock_frames;
    uint64_t now_ns = host_ticks();
    if (!prev_ns) prev_ns = now_ns;
    if (pacing_lockstep_now() && pacing_sim_hz()) ++lock_frames;
    else wall_ns += now_ns - prev_ns;
    prev_ns = now_ns;
    uint64_t frame = 1 + wall_ns * 60 / 1000000000ull + (lock_frames ? lock_frames * 60 / (uint64_t)pacing_sim_hz() : 0);
    if (release_at && frame >= release_at) {
        float diag_car_speed(void);
        if (!strcmp(held, "pad")) { script_pad = script_raw = 0; script_axis[0] = 0; if (diag_on) diag_event("scripted pad up (car %.1f m/s)", diag_car_speed()); }
        else if (!strcmp(held, "triggers")) {
            script_trigger_pair = 0; script_lt = script_rt = 0; ++script_trigger_step;
            if (diag_on) diag_event("scripted padtriggers up (car %.1f m/s)", diag_car_speed());
            release_at = 0;
            return;                       // give the released pair its own device poll
        }
        else press(held, 0, real_frame);
        release_at = 0;
    }
    if (draws != last_draws) { same_since = frame; last_draws = draws; }
    uint64_t same = frame - same_since;
    if (release_at) return;
    // find step `step`
    const char *p = spec;
    for (int k = 0; k < step && p; ++k) { p = strchr(p, ','); if (p) ++p; }
    if (!p || !*p) return;
    char tok[32];
    int n = 0;
    while (p[n] && p[n] != ',' && n < 31) { tok[n] = p[n]; ++n; }
    tok[n] = 0;
    if (!step_start) step_start = frame;
    if (!strncmp(tok, "skipmovies", 10)) {
        // While a MoviePlayer is active (movies.c, X360 or VP6), press Enter every 90 frames; done once no movie has
        // played for 120 frames (the title screen is animated, so its draw count never settles).
        int movies_playing(void);
        static uint64_t last_movie, last_press;
        if (movies_playing() || draws <= 2) last_movie = frame;
        if (frame - last_movie >= 120 && draws > 2) { ++step; step_start = 0; if (diag_on) diag_event("script: movies done"); }
        else if (movies_playing() && frame - step_start >= 89 && frame - last_press >= 90) {
            press("RETURN", 1, real_frame); snprintf(held, sizeof held, "RETURN"); release_at = frame + 4; last_press = frame;
        }
    } else if (!strncmp(tok, "unfocus:", 8) || !strncmp(tok, "hide:", 5)) {
        // Focus loss for N seconds: unfocus = another app takes over (Cmd-Tab), hide = Cmd-H (off screen too). The
        // window's delegate gets the focus changes directly (AppKit won't activate an app the user isn't using);
        // the game stops drawing while inactive, so the way back is timed, not counted in frames.
        Win *w = win(focus_hwnd);
        if (w && w->ns) {
            int hide = tok[0] == 'h';
            GameWindowDelegate *d = (GameWindowDelegate *)w->ns.delegate;
            if (hide) [NSApp hide:nil];
            [d windowDidResignKey:nil];
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(atof(strchr(tok, ':') + 1) * 1e9)), dispatch_get_main_queue(), ^{
                if (hide) [NSApp unhide:nil];
                [d windowDidBecomeKey:nil];
            });
        }
        if (diag_on) diag_event("script: %s", tok);
        ++step; step_start = 0;
    } else if (!strncmp(tok, "carnm:", 6)) {   // test only: NFS_CAR_NORMALMAPS live (x360|pc), for same-view A/B shots in the front end
        void materials_set_car_normals(int x360);
        materials_set_car_normals(!strcmp(tok + 6, "x360"));
        ++step; step_start = 0;
    } else if (!strncmp(tok, "flake:", 6)) {   // test only: R30 flake live: x360 | pc | tint0..2 | force0 | force1 | forceoff (same-view A/B and pixel counts in the front end)
        void materials_set_car_flake(int x360), materials_flake_test(int tint, int force);
        extern int materials_flake_tint, materials_flake_force;
        const char *w = tok + 6;
        if (!strcmp(w, "x360") || !strcmp(w, "pc")) materials_set_car_flake(!strcmp(w, "x360"));
        else if (!strncmp(w, "tint", 4)) materials_flake_test(atoi(w + 4), materials_flake_force);
        else materials_flake_test(materials_flake_tint, !strcmp(w, "force0") ? 0 : !strcmp(w, "force1") ? 1 : -1);
        ++step; step_start = 0;
    } else if (!strcmp(tok, "shot")) {
        diag_shot_request(real_frame + 3);   // a full-size frame shot a few frames on (diag runs)
        if (diag_on) diag_event("script: shot of frame %llu", (unsigned long long)real_frame + 3);
        ++step; step_start = 0;
    } else if (!strncmp(tok, "mousemove:", 10)) {
        // A mouse movement of DX,DY pixels over the window (scripted runs drop real mouse events): the game's
        // WM_MOUSEMOVE and DirectInput deltas, and the input hand-over (prompts_mouse_moved).
        int dx = 0, dy = 0;
        sscanf(tok + 10, "%d,%d", &dx, &dy);
        Win *w = win(focus_hwnd);
        if (w) {
            int l, t, r, b;
            frame_insets(w->style, &l, &t, &r, &b);
            mouse_x += dx; mouse_y += dy;
            int cx = mouse_x - w->x - l, cy = mouse_y - w->y - t;
            di_mouse_dx += dx; di_mouse_dy += dy;
            post(hwnd_of(w), 0x0200, 0, ((uint32_t)(int16_t)cy << 16) | (uint16_t)(int16_t)cx);   // WM_MOUSEMOVE
            prompts_mouse_moved((float)(abs(dx) + abs(dy)));
        }
        if (diag_on) diag_event("script: %s", tok);
        ++step; step_start = 0;
    } else if (!strncmp(tok, "cmdopt:", 7)) {
        // A menu shortcut, as the pump hands Cmd keys to the menu bar (scripted runs drop real key events).
        NSString *k = [[NSString stringWithUTF8String:tok + 7] lowercaseString];
        NSEvent *e = [NSEvent keyEventWithType:NSEventTypeKeyDown location:NSZeroPoint
                                 modifierFlags:NSEventModifierFlagCommand | NSEventModifierFlagOption timestamp:0 windowNumber:0
                                       context:nil characters:k charactersIgnoringModifiers:k isARepeat:NO keyCode:0];
        BOOL done = [NSApp.mainMenu performKeyEquivalent:e];
        if (diag_on) diag_event("script: %s %s", tok, done ? "handled by the menu" : "not a menu shortcut");
        ++step; step_start = 0;
    } else if (!strncmp(tok, "padlayout:", 10)) {
        script_layout = !strcasecmp(tok + 10, "xbox");
        if (diag_on) diag_event("script: %s", tok);
        ++step; step_start = 0;
    } else if (!strncmp(tok, "padtriggers:", 12)) {
        script_lt = script_rt = 0;
        sscanf(tok + 12, "%f/%f", &script_lt, &script_rt);
        script_lt = fminf(1, fmaxf(0, script_lt)); script_rt = fminf(1, fmaxf(0, script_rt));
        script_trigger_pair = 1; ++script_trigger_step;
        snprintf(held, sizeof held, "triggers");
        const char *hold = strchr(tok + 12, ':');
        release_at = frame + (hold && atoi(hold + 1) > 0 ? (uint64_t)atoi(hold + 1) : 6);
        float diag_car_speed(void);
        if (diag_on) diag_event("scripted %s down (car %.1f m/s)", tok, diag_car_speed());
        ++step; step_start = 0;
    } else if (!strncmp(tok, "pad:", 4) || !strncmp(tok, "padraw:", 7) || !strncmp(tok, "padaxis:", 8)) {
        if (tok[3] == ':') script_pad = (uint32_t)strtoul(tok + 4, NULL, 0);
        else if (tok[3] == 'r') script_raw = (uint32_t)strtoul(tok + 7, NULL, 0);
        else snprintf(script_axis, sizeof script_axis, "%s", tok + 8);
        snprintf(held, sizeof held, "pad");
        const char *hold = strchr(strchr(tok, ':') + 1, ':');   // pad:MASK:N etc.: held N ticks
        release_at = frame + (hold && atoi(hold + 1) > 0 ? (uint64_t)atoi(hold + 1) : 6);
        fprintf(stderr, "boot: scripted %s at frame %llu\n", tok, (unsigned long long)real_frame);
        float diag_car_speed(void);
        if (diag_on) diag_event("scripted %s down (car %.1f m/s)", tok, diag_car_speed());   // gate.sh's pad smoke check
        void diag_scripted_key(uint64_t frame);
        diag_scripted_key(real_frame);
        ++step; step_start = 0;
    } else if (!strncmp(tok, "sim:", 4)) {
        static float race_sim = -1;
        float now;
        memcpy(&now, G + 0x009885D8u, 4);
        if (M32(0x00925E90u) == 6) {                          // racing (GAMEFLOW_STATE)
            if (race_sim < 0) race_sim = now;
            if (now - race_sim >= (float)atof(tok + 4)) { ++step; step_start = 0; }
        }
    } else if (!strncmp(tok, "padtype:", 8)) {   // BOOT_PAD=sim's identity from now on (a controller-type change)
        sim_kind = kind_of_word(tok + 8);
        if (diag_on) diag_event("script: %s", tok);
        ++step; step_start = 0;
    } else if (!strncmp(tok, "pkg:", 4)) {
        static uint64_t last_pkg_key;
        char want[32], *key;
        snprintf(want, sizeof want, "%s", tok + 4);
        if ((key = strchr(want, ':'))) *key++ = 0;      // pkg:NAME:KEY presses KEY every 150 ticks meanwhile
        const char *ctl = fe_package_in_control();
        if ((ctl && !strncasecmp(ctl, want, strlen(want))) || frame - step_start >= 1800) {
            if (diag_on) diag_event("script step %d %s done after %llu frames (%s)", step, tok, (unsigned long long)(frame - step_start), ctl ? ctl : "none");
            ++step; step_start = 0;
        } else if (key && frame - step_start >= 150 && frame - last_pkg_key >= 150) {
            last_pkg_key = frame;
            press(key, 1, real_frame);
            snprintf(held, sizeof held, "%s", key);
            release_at = frame + 4;
        }
    } else if (!strncmp(tok, "race:", 5)) {               // race:N: N ticks after the game-flow state first became 6
        static uint64_t race_at;
        if (M32(0x00925E90u) == 6 && !race_at) race_at = frame;   // TheGameFlowManager state
        if (race_at && frame - race_at >= (uint64_t)atoi(tok + 5)) { if (diag_on) diag_event("script: %s", tok); ++step; step_start = 0; }
    } else if (!strncmp(tok, "wait:", 5)) {
        if (frame - step_start >= (uint64_t)atoi(tok + 5)) { ++step; step_start = 0; }
    } else if (!strncmp(tok, "idle:", 5)) {
        uint64_t want = (uint64_t)atoi(tok + 5);
        if ((same >= want && draws > 2) || frame - step_start >= 20 * want) {
            if (diag_on) diag_event("script step %d %s done after %llu frames", step, tok, (unsigned long long)(frame - step_start));
            ++step; step_start = 0;
        }
    } else {
        press(tok, 1, real_frame);
        snprintf(held, sizeof held, "%s", tok);
        release_at = frame + 4;
        ++step; step_start = 0;
    }
}

// One RETURN for BOOT_AUTODRIVE's result popups (d3d9.m); released 4 frames later by user32_script.
static uint64_t autoret_release;
void user32_autoreturn(void) {
    press("RETURN", 1, 0);
    autoret_release = 1;
}

// Every frame (user32_script): the main display changed (a TV waking up took over from the built-in panel, say). Once
// the notifications have been quiet for 0.7 s the full screen windows re-fit the screen, and the game follows with its
// own resolution change (widescreen_display_changed) if it was on the native resolution. Not while the F10 menu is open.
// NFS_TEST_DISPLAY_CHANGE=<secs>:<WxH> pretends, after that many seconds, that the display became WxH pixels.
int widescreen_display_changed(void);
int portmenu_is_open(void);
static void display_poll(void) {
    static CFAbsoluteTime t0, test_at;
    static int test_init, forced;
    CFAbsoluteTime now = CFAbsoluteTimeGetCurrent();
    if (!test_init) {
        test_init = 1; t0 = now;
        const char *e = getenv("NFS_TEST_DISPLAY_CHANGE");
        int w, h; double secs;
        if (e && sscanf(e, "%lf:%dx%d", &secs, &w, &h) == 3 && w > 0 && h > 0) { test_at = t0 + secs; test_px_w = 0; test_px_h = 0; forced = (w << 16) | h; }
    }
    if (forced && test_at && now >= test_at) {
        test_at = 0;
        test_px_w = forced >> 16; test_px_h = forced & 0xFFFF;
        screen_w = (int)(test_px_w / screen_scale);
        screen_h = (int)(test_px_h / screen_scale) + screen_top_inset;
        fprintf(stderr, "boot: display change (test): now %dx%d pixels\n", test_px_w, test_px_h);
        screen_changed_at = now - 1; screen_dirty = 2;     // 2: keep the faked size
    }
    if (!screen_dirty || now - screen_changed_at < 0.7 || portmenu_is_open()) return;
    @autoreleasepool {
        if (screen_dirty != 2) read_main_screen();
        screen_dirty = 0;
        fprintf(stderr, "boot: display changed: %dx%d points at %.0fx\n", screen_w, screen_h, screen_scale);
        Win *w = game_window();
        if (w && w->ns && ns_fullscreen(w)) { [w->ns setFrame:ns_content_rect(w) display:YES]; fprintf(stderr, "boot: full screen window re-fit\n"); }
        widescreen_display_changed();
    }
}

void user32_script(uint64_t frame, uint64_t draws) {
    display_poll();
    static int held_frames;
    if (autoret_release && ++held_frames >= 4) { press("RETURN", 0, frame); autoret_release = held_frames = 0; }
    const char *spec = getenv("BOOT_KEYS");
    if (spec && !strncmp(spec, "seq:", 4)) { script_seq(spec + 4, frame, draws); return; }
    for (const char *p = spec; p && *p;) {
        unsigned long long fr = strtoull(p, (char **)&p, 10);
        if (*p != ':') break;
        char name[16];
        int n = 0;
        for (++p; *p && *p != ',' && n < 15; ++p) name[n++] = *p;
        name[n] = 0;
        if (*p == ',') ++p;
        if (frame == fr || frame == fr + 4) press(name, frame == fr, frame);
    }
}

// The D3D9 layer renders into this.
void *user32_metal_layer(uint32_t hwnd) {
    Win *w = win(hwnd);
    return w && w->view ? (__bridge void *)w->view.layer : NULL;
}
void user32_client_size(uint32_t hwnd, int *cw, int *ch) {
    Win *w = win(hwnd);
    *cw = w ? w->cw : 0;
    *ch = w ? w->ch : 0;
}
void user32_screen_size(int *w, int *h) { cocoa_init(); *w = screen_w; *h = screen_h; }
// Highest refresh rate of the main display (120 on ProMotion MacBook Pros, 60 on most external displays).
int user32_display_hz(void) {
    cocoa_init();
    NSInteger hz = [[NSScreen mainScreen] maximumFramesPerSecond];
    return hz > 0 ? (int)hz : 60;
}
// The full screen size in pixels (below the camera housing on screens that have one): the widescreen "native" mode.
// NFS_DISPLAY_PIXELS=WxH fakes it (the reference frames, refs.sh, want the same resolution slots on every Mac).
void user32_display_pixels(int *w, int *h) {
    cocoa_init();
    if (test_px_w) { *w = test_px_w & ~7; *h = test_px_h & ~7; return; }
    const char *fake = getenv("NFS_DISPLAY_PIXELS");
    if (fake && sscanf(fake, "%dx%d", w, h) == 2 && *w > 0 && *h > 0) { *w &= ~7; *h &= ~7; return; }
    *w = (int)(screen_w * screen_scale) & ~7;
    *h = (int)((screen_h - screen_top_inset) * screen_scale) & ~7;
}

// ---------------- wsprintf ----------------
// Formats with guest arguments read from `ap` (guest address of the first vararg). Supports what the game uses:
// %[-0][width][l|h]{d,i,u,x,X,c,s,%}.
static uint32_t gformat(uint32_t out, uint32_t fmt, uint32_t ap) {
    char buf[1024];
    size_t n = 0;
    const char *f = (const char *)G + fmt;
    while (*f && n < sizeof buf - 1) {
        if (*f != '%') { buf[n++] = *f++; continue; }
        char spec[16] = "%";
        int sl = 1;
        ++f;
        while ((*f == '-' || *f == '0' || *f == '#' || (*f >= '1' && *f <= '9') || *f == '.') && sl < 12) spec[sl++] = *f++;
        while (*f == 'l' || *f == 'h') ++f;
        char conv = *f ? *f++ : 0;
        char tmp[512];
        if (conv == '%') { tmp[0] = '%'; tmp[1] = 0; }
        else if (conv == 's') { spec[sl++] = 's'; spec[sl] = 0; uint32_t p = M32(ap); ap += 4; snprintf(tmp, sizeof tmp, spec, p ? (const char *)G + p : "(null)"); }
        else if (conv == 'c') { spec[sl++] = 'c'; spec[sl] = 0; snprintf(tmp, sizeof tmp, spec, (int)M32(ap)); ap += 4; }
        else if (conv == 'd' || conv == 'i') { spec[sl++] = 'd'; spec[sl] = 0; snprintf(tmp, sizeof tmp, spec, (int32_t)M32(ap)); ap += 4; }
        else if (conv == 'u' || conv == 'x' || conv == 'X') { spec[sl++] = conv; spec[sl] = 0; snprintf(tmp, sizeof tmp, spec, M32(ap)); ap += 4; }
        else { tmp[0] = 0; }
        for (char *t = tmp; *t && n < sizeof buf - 1; ++t) buf[n++] = *t;
    }
    buf[n] = 0;
    memcpy(G + out, buf, n + 1);
    return (uint32_t)n;
}

// GetMessageA with nothing queued. The game does this on purpose while its window is inactive: WM_ACTIVATEAPP 0
// parks its main loop in GetMessageA (FUN_006c21a0) until messages arrive, as a paused game. Its other threads
// (sound, streaming) keep running, so the GIL is released while Cocoa waits for an event; the event is dequeued
// afterwards with the GIL held.
static void wait_messages(void) {
    if (headless) return;
    diag_idle(1);
    gil_release();
    @autoreleasepool {
        [NSApp nextEventMatchingMask:NSEventMaskAny untilDate:[NSDate dateWithTimeIntervalSinceNow:0.05]
                              inMode:NSDefaultRunLoopMode dequeue:NO];
    }
    gil_acquire();
}

// ---------------- imports ----------------
static uint32_t gdi_next = 0x00030000;

int win32_user32(Cpu *c, const char *fn) {
    cocoa_init();
    // classes and windows
    if (!strcmp(fn, "RegisterClassExA")) {
        uint32_t p = arg(c, 0);
        if (class_count == MAX_CLASSES) RET(0, 1);
        WinClass *k = &classes[class_count];
        k->style = M32(p + 4); k->wndproc = M32(p + 8); k->hinstance = M32(p + 20);
        uint32_t name = M32(p + 40);
        snprintf(k->name, sizeof k->name, "%s", name < 0x10000 ? "" : (const char *)G + name);
        k->atom = 0xC000 + (uint32_t)class_count++;
        if (boot_trace) fprintf(stderr, "      RegisterClassExA(%s, wndproc %08X) -> %04X\n", k->name, k->wndproc, k->atom);
        RET(k->atom, 1);
    }
    if (!strcmp(fn, "CreateWindowExA")) {      // (ex, class, title, style, x, y, w, h, parent, menu, inst, param)
        uint32_t cls = arg(c, 1);
        WinClass *k = NULL;
        for (int i = 0; i < class_count; ++i)
            if (cls < 0x10000 ? classes[i].atom == cls : !strcasecmp(classes[i].name, (const char *)G + cls)) k = &classes[i];
        if (!k) { last_error = 1407; RET(0, 12); }               // ERROR_CANNOT_FIND_WND_CLASS
        Win *w = NULL;
        for (int i = 0; i < MAX_WINDOWS; ++i) if (!wins[i].used) { w = &wins[i]; break; }
        if (!w) RET(0, 12);
        memset(w, 0, sizeof *w);
        w->used = 1;
        w->exstyle = arg(c, 0); w->style = arg(c, 3); w->wndproc = k->wndproc; w->cls = k->atom;
        int x = (int32_t)arg(c, 4), y = (int32_t)arg(c, 5), ww = (int32_t)arg(c, 6), wh = (int32_t)arg(c, 7);
        if (x == (int32_t)0x80000000) x = 0;                      // CW_USEDEFAULT
        if (y == (int32_t)0x80000000) y = 0;
        if (ww == (int32_t)0x80000000 || ww <= 0) ww = 640;
        if (wh == (int32_t)0x80000000 || wh <= 0) wh = 480;
        int l, t, r, b;
        frame_insets(w->style, &l, &t, &r, &b);
        w->x = x; w->y = y; w->w = ww; w->h = wh;
        w->cw = ww - l - r; w->ch = wh - t - b;
        if (w->cw < 1) w->cw = 1;
        if (w->ch < 1) w->ch = 1;
        uint32_t hwnd = hwnd_of(w);
        if (boot_trace) fprintf(stderr, "      CreateWindowExA(%s, %s, style %08X, %d,%d %dx%d) -> %08X\n",
                                k->name, gptr(arg(c, 2)) ? gptr(arg(c, 2)) : "", w->style, x, y, ww, wh, hwnd);
        ns_create(w, gptr(arg(c, 2)));
        if (!focus_hwnd) focus_hwnd = hwnd;
        // WM_NCCREATE / WM_CREATE with a CREATESTRUCTA (param, inst, menu, parent, cy, cx, y, x, style, name, class, ex)
        uint32_t cs = galloc(48);
        uint32_t f[12] = {arg(c, 11), arg(c, 10), arg(c, 9), arg(c, 8), (uint32_t)wh, (uint32_t)ww, (uint32_t)y, (uint32_t)x,
                          w->style, arg(c, 2), cls, w->exstyle};
        memcpy(G + cs, f, sizeof f);
        uint32_t esp = c->esp;
        call_wndproc(c, hwnd, 0x0081, 0, cs);
        if ((int32_t)call_wndproc(c, hwnd, 0x0001, 0, cs) == -1) { w->used = 0; c->esp = esp; RET(0, 12); }
        call_wndproc(c, hwnd, 0x0005, 0, ((uint32_t)w->ch << 16) | (uint16_t)w->cw);   // WM_SIZE
        c->esp = esp;
        if (w->style & 0x10000000) ns_show(w, 1);                  // WS_VISIBLE
        RET(hwnd, 12);
    }
    if (!strcmp(fn, "DestroyWindow")) {
        Win *w = win(arg(c, 0));
        if (!w) RET(0, 1);
        uint32_t h = arg(c, 0), esp = c->esp;
        call_wndproc(c, h, 0x0002, 0, 0);                         // WM_DESTROY
        c->esp = esp;
        ns_close(w);
        w->ns = nil; w->view = nil; w->used = 0;
        if (focus_hwnd == h) focus_hwnd = 0;
        RET(1, 1);
    }
    if (!strcmp(fn, "DefWindowProcA")) {         // (hwnd, msg, wp, lp)
        uint32_t h = arg(c, 0), msg = arg(c, 1);
        if (msg == 0x0010) {                                       // WM_CLOSE -> DestroyWindow
            Win *w = win(h);
            if (w) {
                uint32_t esp = c->esp;
                call_wndproc(c, h, 0x0002, 0, 0);
                c->esp = esp;
                ns_close(w);
                w->ns = nil; w->view = nil; w->used = 0;
            }
        }
        if (msg == 0x0081) RET(1, 4);                              // WM_NCCREATE: continue creation
        RET(0, 4);
    }
    if (!strcmp(fn, "ShowWindow")) {             // (hwnd, cmd) -> was visible
        Win *w = win(arg(c, 0));
        if (!w) RET(0, 2);
        int was = w->visible;
        ns_show(w, arg(c, 1) != 0);
        RET(was, 2);
    }
    if (!strcmp(fn, "UpdateWindow")) RET(win(arg(c, 0)) != NULL, 1);
    if (!strcmp(fn, "SetWindowPos")) {           // (hwnd, after, x, y, cx, cy, flags)
        Win *w = win(arg(c, 0));
        if (!w) RET(0, 7);
        uint32_t fl = arg(c, 6);
        if (!(fl & 2)) { w->x = (int32_t)arg(c, 2); w->y = (int32_t)arg(c, 3); }    // SWP_NOMOVE
        if (!(fl & 1)) {                                                           // SWP_NOSIZE
            int l, t, r, b;
            frame_insets(w->style, &l, &t, &r, &b);
            w->w = (int32_t)arg(c, 4); w->h = (int32_t)arg(c, 5);
            w->cw = w->w - l - r; w->ch = w->h - t - b;
            ns_resize(w);
        }
        if (fl & 0x40) ns_show(w, 1);                                              // SWP_SHOWWINDOW
        if (fl & 0x80) ns_show(w, 0);                                              // SWP_HIDEWINDOW
        RET(1, 7);
    }
    if (!strcmp(fn, "SetWindowLongA")) {         // (hwnd, index, value) -> old
        Win *w = win(arg(c, 0));
        if (!w) RET(0, 3);
        int32_t i = (int32_t)arg(c, 1);
        uint32_t v = arg(c, 2), old = 0;
        if (i == -4) { old = w->wndproc; w->wndproc = v; }
        else if (i == -16) { old = w->style; w->style = v; }
        else if (i == -20) { old = w->exstyle; w->exstyle = v; }
        else if (i == -21) { old = w->userdata; w->userdata = v; }
        RET(old, 3);
    }
    if (!strcmp(fn, "GetWindowRect")) {          // (hwnd, rect)
        uint32_t h = arg(c, 0), r = arg(c, 1);
        if (h == HWND_DESKTOP_H) { M32(r) = 0; M32(r + 4) = 0; M32(r + 8) = screen_w; M32(r + 12) = screen_h; RET(1, 2); }
        Win *w = win(h);
        if (!w) { last_error = 1400; RET(0, 2); }                  // ERROR_INVALID_WINDOW_HANDLE
        M32(r) = w->x; M32(r + 4) = w->y; M32(r + 8) = w->x + w->w; M32(r + 12) = w->y + w->h;
        RET(1, 2);
    }
    if (!strcmp(fn, "GetClientRect")) {
        uint32_t h = arg(c, 0), r = arg(c, 1);
        if (h == HWND_DESKTOP_H) { M32(r) = 0; M32(r + 4) = 0; M32(r + 8) = screen_w; M32(r + 12) = screen_h; RET(1, 2); }
        Win *w = win(h);
        if (!w) { last_error = 1400; RET(0, 2); }
        M32(r) = 0; M32(r + 4) = 0; M32(r + 8) = w->cw; M32(r + 12) = w->ch;
        RET(1, 2);
    }
    if (!strcmp(fn, "AdjustWindowRect")) {       // (rect, style, menu)
        uint32_t r = arg(c, 0);
        int l, t, rr, b;
        frame_insets(arg(c, 1), &l, &t, &rr, &b);
        if (arg(c, 2)) t += 19;
        M32(r) -= l; M32(r + 4) -= t; M32(r + 8) += rr; M32(r + 12) += b;
        RET(1, 3);
    }
    if (!strcmp(fn, "GetDesktopWindow")) RET(HWND_DESKTOP_H, 0);
    if (!strcmp(fn, "GetForegroundWindow")) RET(focus_hwnd, 0);
    if (!strcmp(fn, "SetForegroundWindow")) { Win *w = win(arg(c, 0)); if (w) focus_hwnd = arg(c, 0); RET(w != NULL, 1); }
    if (!strcmp(fn, "SetFocus")) { uint32_t old = focus_hwnd; if (win(arg(c, 0))) focus_hwnd = arg(c, 0); RET(old, 1); }
    if (!strcmp(fn, "IsIconic")) { Win *w = win(arg(c, 0)); RET(w && w->ns && w->ns.miniaturized, 1); }
    if (!strcmp(fn, "SetCapture")) { uint32_t old = capture_hwnd; capture_hwnd = arg(c, 0); RET(old, 1); }
    if (!strcmp(fn, "ReleaseCapture")) { capture_hwnd = 0; RET(1, 0); }
    if (!strcmp(fn, "LoadIconA") || !strcmp(fn, "LoadCursorA")) RET(gdi_next++, 2);
    if (!strcmp(fn, "SetCursor")) RET(0, 1);
    if (!strcmp(fn, "ShowCursor")) {
        cursor_count += arg(c, 0) ? 1 : -1;
        if (cursor_hidden != (cursor_count < 0)) {      // over the game view only, not the title bar or the menu bar
            cursor_hidden = cursor_count < 0;
            for (int i = 0; i < MAX_WINDOWS; ++i) if (wins[i].used && wins[i].ns) [wins[i].ns invalidateCursorRectsForView:wins[i].view];
        }
        RET((uint32_t)cursor_count, 1);
    }
    if (!strcmp(fn, "GetCursorPos")) { M32(arg(c, 0)) = (uint32_t)mouse_x; M32(arg(c, 0) + 4) = (uint32_t)mouse_y; RET(1, 1); }
    if (!strcmp(fn, "GetKeyState")) {
        uint32_t vk = arg(c, 0) & 0xFF;
        RET((key_down[vk] ? 0xFF80u : 0) | key_toggled[vk], 1);
    }
    if (!strcmp(fn, "GetKeyboardLayout")) RET(0x04090409, 1);
    if (!strcmp(fn, "MapVirtualKeyA") || !strcmp(fn, "MapVirtualKeyExA")) {     // (code, type[, hkl])
        int n = fn[13] == 'E' ? 3 : 2;
        uint32_t code = arg(c, 0), type = arg(c, 1);
        for (int i = 0; i < 128; ++i) {
            if ((type == 0 || type == 2) && mac_keys[i].vk == code && code) {
                if (type == 0) RET(mac_keys[i].scan, n);
                RET(code >= 'A' && code <= 'Z' ? code : (code >= '0' && code <= '9') || code == ' ' ? code : 0, n);
            }
            if ((type == 1 || type == 3) && mac_keys[i].scan == code && code) RET(mac_keys[i].vk, n);
        }
        RET(0, n);
    }
    if (!strcmp(fn, "ToUnicode")) {              // (vk, scan, keystate[256], buf, cch, flags)
        uint32_t vk = arg(c, 0), ks = arg(c, 2), buf = arg(c, 3);
        int shift = (M8(ks + 0x10) & 0x80) != 0;
        uint16_t ch = 0;
        if (vk >= 'A' && vk <= 'Z') ch = (uint16_t)(shift ? vk : vk + 32);
        else if (vk >= '0' && vk <= '9' && !shift) ch = (uint16_t)vk;
        else if (vk == ' ') ch = ' ';
        if (!ch || arg(c, 4) < 1) RET(0, 6);
        M16(buf) = ch;
        RET(1, 6);
    }
    // messages
    if (!strcmp(fn, "PeekMessageA") || !strcmp(fn, "GetMessageA")) {   // (msg, hwnd, min, max[, remove])
        int get = fn[0] == 'G';
        uint32_t out = arg(c, 0), min = arg(c, 2), max = arg(c, 3), rm = get ? 1 : arg(c, 4);
        for (;;) {
            if (get && qlen == 0) wait_messages();
            pump(0);
            int found = 0;
            Msg m;
            pthread_mutex_lock(&qlock);
            for (int k = 0; k < qlen && !found; ++k) {
                m = q[(qhead + k) % QCAP];
                if ((min || max) && (m.msg < min || m.msg > max)) continue;
                found = 1;
                if (rm & 1) {                                     // PM_REMOVE: close the gap
                    for (int j = k; j > 0; --j) q[(qhead + j) % QCAP] = q[(qhead + j - 1) % QCAP];
                    qhead = (qhead + 1) % QCAP;
                    --qlen;
                }
            }
            pthread_mutex_unlock(&qlock);
            if (found) {
                M32(out) = m.hwnd; M32(out + 4) = m.msg; M32(out + 8) = m.wp; M32(out + 12) = m.lp;
                M32(out + 16) = m.time; M32(out + 20) = (uint32_t)m.px; M32(out + 24) = (uint32_t)m.py;
                if (get) RET(m.msg != 0x0012, 4);                 // WM_QUIT -> 0
                RET(1, 5);
            }
            if (!get) RET(0, 5);
            if (headless) { M32(out + 4) = 0x0012; RET(0, 4); }   // nothing will ever arrive
        }
    }
    if (!strcmp(fn, "TranslateMessage")) RET(0, 1);                // WM_CHAR is queued with the key event
    if (!strcmp(fn, "DispatchMessageA")) {
        uint32_t m = arg(c, 0), esp = c->esp;
        uint32_t r = call_wndproc(c, M32(m), M32(m + 4), M32(m + 8), M32(m + 12));
        c->esp = esp;
        RET(r, 1);
    }
    if (!strcmp(fn, "PostMessageA")) { post(arg(c, 0), arg(c, 1), arg(c, 2), arg(c, 3)); RET(1, 4); }
    if (!strcmp(fn, "PostThreadMessageA")) { post(0, arg(c, 1), arg(c, 2), arg(c, 3)); RET(1, 4); }
    if (!strcmp(fn, "PostQuitMessage")) { post(0, 0x0012, arg(c, 0), 0); RET(0, 1); }
    if (!strcmp(fn, "SendInput")) RET(arg(c, 0), 3);
    if (!strcmp(fn, "BeginPaint")) {             // (hwnd, PAINTSTRUCT*) -> hdc
        uint32_t ps = arg(c, 1);
        memset(G + ps, 0, 64);
        Win *w = win(arg(c, 0));
        M32(ps) = 0x00020000;
        if (w) { M32(ps + 16) = w->cw; M32(ps + 20) = w->ch; }
        RET(0x00020000, 2);
    }
    if (!strcmp(fn, "EndPaint")) RET(1, 2);
    if (!strcmp(fn, "MessageBoxA")) {            // (hwnd, text, caption, type)
        fprintf(stderr, "boot: MessageBox \"%s\": %s\n", gptr(arg(c, 2)) ? gptr(arg(c, 2)) : "", gptr(arg(c, 1)) ? gptr(arg(c, 1)) : "");
        RET(1, 4);                                                 // IDOK
    }
    if (!strcmp(fn, "wsprintfA")) RET(gformat(arg(c, 0), arg(c, 1), c->esp + 12), 0);   // __cdecl
    if (!strcmp(fn, "wvsprintfA")) RET(gformat(arg(c, 0), arg(c, 1), arg(c, 2)), 3);
    // GDI32: only used for a text/bitmap fallback path; record-only
    if (!strcmp(fn, "CreateCompatibleDC") || !strcmp(fn, "CreateFontA") || !strcmp(fn, "CreateBitmap")) {
        int n = fn[6] == 'C' ? 1 : fn[6] == 'F' ? 14 : 5;
        RET(gdi_next++, n);
    }
    if (!strcmp(fn, "SelectObject")) RET(0x00030000, 2);
    if (!strcmp(fn, "DeleteObject") || !strcmp(fn, "DeleteDC")) RET(1, 1);
    if (!strcmp(fn, "SetBkMode") || !strcmp(fn, "SetBkColor") || !strcmp(fn, "SetTextColor")) RET(0, 2);
    if (!strcmp(fn, "ExtTextOutA")) RET(1, 8);
    if (!strcmp(fn, "BitBlt")) RET(1, 9);
    if (!strcmp(fn, "GetPixel")) RET(0, 3);
    return 0;
}

// ---------------- game controller (dinput.c's gamepad) ----------------
// Button layouts (NFS_PAD_LAYOUT, else the app's Controller menu, kept in USERDATA/app.txt pad_layout): what the physical buttons send while driving, so that the game's PC defaults for its
// "Controller (XBOX 360 For Windows)" give each console version's layout. Those defaults (Options > Controls, all
// remappable) are Accelerate Button 1, Handbrake 2, Brake 3, Speedbreaker 4, Look Behind 5, Reset 6, N2O 7, Pause 8,
// the D-pad for Engage Event / Blacklist / World Map / SMS, and shifting and the camera unbound: both layouts send
// shift up / down as Buttons 9 / 10 and change camera as Button 11 (bind them once in Options > Controls).
// playstation (default): the PS2 version's manual: Cross or right stick up gas, Square or right stick down
//       brake/reverse, Triangle look back, Circle or R3 speedbreaker, R1 e-brake, L1 nitrous, R2 / L2 shift up / down,
//       Create (Select) reset car, Options (Start) pause.
// xbox: the Xbox version's manual: right / left trigger gas / brake, A e-brake, B nitrous, X speedbreaker, Y look
//       back, RB (the Black button) change camera, right stick up / down shift, Back reset car, Start pause.
// Xbox pedals remain independent analog axes; padbind.c installs their positive-half bindings.
// Menus and the pause menu: each layout's console version, matching the console button prompts (padprompts.c). The
// PC's front-end mapping (one ActionQueue on port 0, keyboard and controller together; BOOT_DIAG fepad: lines) binds
// Button 1 to FE BUTTON5 (key 1), Button 2 to ACCEPT (Enter), Button 3 to CANCEL (Esc), Buttons 5/6 to the L/R
// triggers, Button 10 to BUTTON3 (Space), and no controller input to BUTTON4 (key 2), BUTTON0 (key 3) or START (key 4)
// (on a real Xbox 360 pad on PC, B accepted and X went back). The packages label them (PC keycap = console glyph):
// Enter = CROSS, Esc = TRIANGLE, 1 = CIRCLE, 2 = SQUARE, 3 = SELECT, 4 = START. So in menus:
//   playstation (the PS2's): Cross accepts, Triangle goes back, Circle = key 1, Square = key 2, Create (Select) =
//       key 3, Options (Start) = key 4 (and Button 8), L1/R1 = Buttons 5/6, L2/R2 Buttons 10/9 as while driving;
//   xbox (the X360's): A accepts, B goes back, Y = key 1, X = key 2, Back = key 3, Start = key 4 (and Button 8),
//       LB/RB = Buttons 5/6.
// Keys 2-4 go in as DirectInput key presses only (no window messages: no typing, the prompts stay console).
// A PlayStation pad (DualSense, DualShock 4, DualSense Edge) skips all of this: dinput.c gives the game the pad itself,
// with PlayStation control names, and the game's own bindings (Options > Controls; defaults and the per-type sets in
// padbind.c) do the mapping. The layouts here are for Xbox and other pads.
enum { P_CROSS, P_CIRCLE, P_SQUARE, P_TRIANGLE, P_L1, P_R1, P_CREATE, P_OPTIONS, P_L3, P_R3, P_L2, P_R2, P_PS, P_TOUCHPAD,
       P_LPADDLE, P_RPADDLE };
#define GAMEFLOW_STATE 0x00925E90u       // 6 racing
#define SIM_INSTANCE 0x009885E0u         // Sim: +0x2C state, 4 paused
static int pad_xbox = -1;
static int pad_layout_xbox(void) {
    if (script_layout >= 0) return script_layout;
    if (pad_xbox < 0) {
        const char *e = getenv("NFS_PAD_LAYOUT");
        if (!e || !*e) e = app_pref("pad_layout");
        pad_xbox = e && !strcasecmp(e, "xbox");
    }
    return pad_xbox;
}
// The Controller menu (app.m): takes effect at the next pad read.
int user32_pad_layout_xbox(void) { return pad_layout_xbox(); }
void user32_set_pad_layout_xbox(int xbox) {
    pad_xbox = xbox;
    app_pref_set("pad_layout", xbox ? "xbox" : "playstation");
    fprintf(stderr, "boot: controller layout %s\n", xbox ? "Xbox" : "PlayStation");
    if (diag_on) diag_event("pad: layout %s", xbox ? "xbox" : "playstation");
}
static uint32_t bit(uint32_t phys, int b) { return (phys >> b) & 1; }
static void pad_menu_keys(uint32_t keys) {  // menus: FE BUTTON4 / BUTTON0 / START as keys 2 / 3 / 4 (DIK 03 / 04 / 05)
    static uint32_t held;
    for (int k = 0; k < 3; ++k)
        if (((keys ^ held) >> k) & 1) di_event((uint8_t)(3 + k), (keys >> k) & 1);
    held = keys;
}
// The FE package that has control (cFEng::FindPackageWithControl 00516A90: cFEng::mInstance -> FEngine, its package
// list at +0xE4, +8 next, +0x20 has control, +0xC name), or NULL.
static const char *fe_package_in_control(void) {
    uint32_t fe = M32(0x0091CADCu) ? M32(M32(0x0091CADCu)) : 0;
    if (!fe || fe == 0xFFFFFF28u) return NULL;
    for (uint32_t p = M32(fe + 0xE4); p; p = M32(p + 8)) if (M32(p + 0x20)) return gptr(M32(p + 0xC));
    return NULL;
}
// Options > Controls re-mapping: the game is waiting for the input to bind (the input object at IOModule
// (GetIOModule 00634370 = 0x920DE0) +8: FUN_00628C00, called at 0052AFED when a field is picked, sets its +0x183C and
// the device +0x1840 (1 keyboard, 3 controller); FUN_00634900 clears +0x183C when an input was captured). What the
// controller sends then is what the binding records, and the bindings are used while driving, so the controller gets
// the driving mapping while the game listens.
static int pad_remap_listening(void) {
    uint32_t io = M32(0x00920DE8u);
    return io && M8(io + 0x183C) && M32(io + 0x1840) == 3;
}
// The game's action bindings (GameAction 0091F418: 76 entries of 0x34 bytes: an id, then keyboard primary/secondary
// and controller primary/secondary, 12 bytes each {type, ?, index}). BOOT_DIAG logs the controller ones on change.
static void pad_diag_bindings(void) {
    static uint32_t last;
    uint32_t h = 2166136261u;
    for (uint32_t a = 0x0091F418u + 0x1C; a < 0x0091F418u + 76 * 0x34; a += 0x34)
        for (int k = 0; k < 6; ++k) h = (h ^ M32(a + 4 * k)) * 16777619u;
    if (h == last) return;
    last = h;
    char line[2048];
    int n = snprintf(line, sizeof line, "pad: controller bindings (action: primary type/index, secondary type/index)");
    for (int i = 0; i < 76 && n < (int)sizeof line - 40; ++i) {
        uint32_t a = 0x0091F418u + 0x34 * (uint32_t)i + 0x1C;
        if (M32(a) || M32(a + 12))
            n += snprintf(line + n, sizeof line - (size_t)n, " %d:%u/%u,%u/%u", i, M32(a), M32(a + 8), M32(a + 12), M32(a + 20));
    }
    diag_event("%s", line);
}
static const char *pad_mode = "menus", *pad_ctl;   // the last mapping decision, for the diag pad: lines
int controltype_passthrough(void);
static void pad_apply_layout(PadState *st, uint32_t phys) {
    uint32_t sim = M32(SIM_INSTANCE);
    // Driving: racing (game-flow 6), the sim not paused, and no menu package in control (the race results come up
    // while the sim still runs; the HUD packages are HUD_*).
    const char *ctl = fe_package_in_control();
    int menu = ctl && strncasecmp(ctl, "HUD_", 4);
    int driving = M32(GAMEFLOW_STATE) == 6 && !(sim && M32(sim + 0x2C) == 4) && !menu;
    int remap = pad_remap_listening();
    // A button held while re-mapping starts (the Cross that picked the field: Accept in menus) or ends (the button
    // just bound) is left out until released, so it is neither bound nor taken as a new menu press.
    static int last_remap;
    static uint32_t remap_held;
    if (remap != last_remap) { last_remap = remap; remap_held = phys; }
    remap_held &= phys;
    phys &= ~remap_held;
    pad_mode = remap ? "remap" : driving ? "driving" : "menus"; pad_ctl = ctl;
    static const char *last_mode;
    if (diag_on && pad_mode != last_mode) {
        last_mode = pad_mode;
        diag_event("pad: %s (package in control %s)", pad_mode, ctl ? ctl : "none");
        pad_diag_bindings();
    }
    if (remap) driving = 1;
    // While re-mapping, the right stick is its own axis (Y Rotation) and isn't also sent as gas/brake buttons.
    int up = !remap && st->ry > 0.5f, down = !remap && st->ry < -0.5f;
    uint32_t gas, ebrake, brake, speedbreaker, lookback, nitrous, shift_up, shift_down, camera = 0;
    if (pad_layout_xbox()) {
        gas = brake = 0;                     // axes drive pedals; a button alias would dominate them at 1
        ebrake = bit(phys, P_CROSS); nitrous = bit(phys, P_CIRCLE);
        speedbreaker = bit(phys, P_SQUARE); lookback = bit(phys, P_TRIANGLE); camera = bit(phys, P_R1);
        shift_up = up; shift_down = down;
    } else {
        gas = bit(phys, P_CROSS) | up; brake = bit(phys, P_SQUARE) | down; ebrake = bit(phys, P_R1); nitrous = bit(phys, P_L1);
        speedbreaker = bit(phys, P_CIRCLE) | bit(phys, P_R3); lookback = bit(phys, P_TRIANGLE);
        shift_up = !remap && (bit(phys, P_R2) | (st->rt > 0.5f));
        shift_down = !remap && (bit(phys, P_L2) | (st->lt > 0.5f));
    }
    int xb = pad_layout_xbox();
    uint32_t back = bit(phys, xb ? P_CIRCLE : P_TRIANGLE), fourth = bit(phys, xb ? P_TRIANGLE : P_CIRCLE);
    uint32_t v = fourth << 0 | bit(phys, P_CROSS) << 1 | back << 2 | (phys & 0x30) | bit(phys, P_OPTIONS) << 7;   // menus
    static uint32_t held_driving;           // a button held from driving into a menu (Options pausing) is no key there
    uint32_t keys = bit(phys, P_SQUARE) | bit(phys, P_CREATE) << 1 | bit(phys, P_OPTIONS) << 2;
    held_driving = driving ? keys : held_driving & keys;
    pad_menu_keys(keys & ~held_driving);
    if (driving)
        v = gas << 0 | ebrake << 1 | brake << 2 | speedbreaker << 3 | lookback << 4 | bit(phys, P_CREATE) << 5 | nitrous << 6 |
            bit(phys, P_OPTIONS) << 7;
    st->buttons = v | shift_up << 8 | shift_down << 9 | camera << 10 | bit(phys, P_L3) << 11;   // L3: next song (padbind.c XBOX_L3)
    // Control Types 2-5 / 7-10 (controltype.c) bind the physical buttons (P_* order) while driving; L3 stays next song.
    if (driving && !remap && controltype_passthrough()) st->buttons = (phys & 0x3FFu) | bit(phys, P_L3) << 11;
    // Keep physical pressures for Z/Rz, including while capturing a binding.
}

// The pad's identity: its kind and the name the game sees (DIDEVICEINSTANCE, the Controls screen's page title, and the
// profile's binding set: padbind.c). Set by pad_read; a disconnected pad keeps the last one.
static int pad_kind = PAD_KIND_NONE;
static char pad_name[64];
static void set_identity(int kind, const char *vendor) {
    char name[64];
    if (kind == PAD_KIND_DUALSENSE) snprintf(name, sizeof name, "Controller (DualSense)");
    else if (kind == PAD_KIND_DUALSHOCK4) snprintf(name, sizeof name, "Controller (DualShock 4)");
    else if (kind == PAD_KIND_EDGE) snprintf(name, sizeof name, "Controller (DualSense Edge)");
    else if (!vendor || !*vendor) snprintf(name, sizeof name, "Controller (XBOX 360 For Windows)");
    else if (!strncasecmp(vendor, "Controller", 10)) snprintf(name, sizeof name, "%s", vendor);
    else snprintf(name, sizeof name, "Controller (%s)", vendor);
    if (kind == pad_kind && !strcmp(name, pad_name)) return;
    pad_kind = kind;
    snprintf(pad_name, sizeof pad_name, "%s", name);
    fprintf(stderr, "boot: controller %s\n", pad_name);
    if (diag_on) diag_event("pad: identity %s (kind %d)", pad_name, kind);
}
int pad_identity(char *name, size_t n) {
    if (name && n) snprintf(name, n, "%s", pad_name);
    return pad_kind;
}
static int kind_native(int kind) { return kind == PAD_KIND_DUALSENSE || kind == PAD_KIND_DUALSHOCK4 || kind == PAD_KIND_EDGE; }
int user32_pad_native(void) { return kind_native(pad_kind); }   // padprompts.c: PlayStation glyphs whatever the layout
// A PlayStation pad as itself: the physical buttons (L2/R2 only as their analog axes), nothing remapped.
static void pad_native(PadState *st, uint32_t phys) {
    if (phys & 1u << P_L2) st->lt = 1;      // BOOT_PAD buttons= / pad: steps with the L2 / R2 bits: a full press
    if (phys & 1u << P_R2) st->rt = 1;
    st->buttons = phys & ~(1u << P_L2 | 1u << P_R2);
    st->native = 1;
    static uint32_t last = ~0u;
    if (diag_on && st->buttons != last) { last = st->buttons; diag_event("pad: native buttons %04X", st->buttons); }
}
// The F10 menu is open: the game reads a connected pad at rest (the layout's held menu keys are released too).
static void pad_neutral(PadState *st) {
    memset(st, 0, sizeof *st);
    st->connected = 1;
    st->pov = -1;
    pad_apply_layout(st, 0);
    st->buttons = 0;
    st->lt = st->rt = 0;
    st->native = kind_native(pad_kind);
}
// A PlayStation pad's PS button and touchpad click reach the game (bindable in Options > Controls) unless one is the
// F10 menu's shortcut (settings controls.menu_button: 0 automatic = the touchpad click on a pad with one, else the PS
// button; 1 PS button; 2 L3+R3 held; 3 Create held), which portmenu.m keeps for itself.
static uint32_t pad_game_extras(int has_touchpad) {
    int choice = settings_get("controls.menu_button");
    uint32_t m = 1u << P_PS | 1u << P_TOUCHPAD | 1u << P_LPADDLE | 1u << P_RPADDLE;
    if (choice == 0 && has_touchpad) m &= ~(1u << P_TOUCHPAD);
    if (choice == 1 || (choice == 0 && !has_touchpad)) m &= ~(1u << P_PS);
    return m;
}
static void pov_dirs(int pov, int *u, int *d, int *l, int *r) {   // hundredths of a degree, clockwise from up; -1 centred
    *u = pov >= 0 && (pov >= 31500 || pov <= 4500);
    *r = pov >= 4500 && pov <= 13500;
    *d = pov >= 13500 && pov <= 22500;
    *l = pov >= 22500 && pov <= 31500;
}

// A controller connecting or going away, at any time (GameController.framework adds a pad when it connects, wakes or is
// plugged in; dinput.c's gamepad exists from start-up and reads centred while there is none). Logged to the app log.
static int pad_present = -1;              // -1 before the first read
static void pad_presence(int present, const char *what) {
    if (present == pad_present) return;
    int first = pad_present < 0;
    pad_present = present;
    if (present) {
        fprintf(stderr, "boot: controller connected: %s\n", what);
        if (!first) prompts_input(1);     // connected while playing: its glyphs (a key press brings the keycaps back)
    } else {
        fprintf(stderr, first ? "boot: no controller connected yet (one connecting later is picked up)\n"
                              : "boot: controller disconnected: the game reads it centred until one connects\n");
        pad_menu_keys(0);                 // the menu keys it held are released
    }
    if (diag_on) diag_event("pad: %s%s (app active %d)", present ? "connected: " : first ? "none at start-up" : "disconnected",
                            present ? what : "", headless ? 0 : (int)NSApp.isActive);
}
static uint64_t start_ns;                 // BOOT_PAD=sim:...@T: seconds since start-up
__attribute__((constructor)) static void pad_clock(void) { start_ns = host_ticks(); }

// The first GameController.framework extended gamepad, or BOOT_PAD=sim:name=value,... (constant values for tests:
// lx ly rx ry in -1..1 with y up positive, lt rt 0..1, buttons=<physical P_* mask>, dpad=<hundredths of a degree>; a
// bare word is the pad's identity: dualsense, ds4, edge, or xbox, the default; a last @T1-T2-T3... connects it T1
// seconds after start-up, disconnects it at T2, connects it again at T3, ...). 0: no controller (st centred).
int pad_read(PadState *st) {
    memset(st, 0, sizeof *st);
    st->pov = -1;
    const char *sim = getenv("BOOT_PAD");
    if (sim && !strcmp(sim, "sim")) sim = "sim:xbox";   // a bare sim is a simulated pad too, never the real controller
    if (sim && !strncmp(sim, "sim:", 4)) {
        const char *at = strchr(sim, '@'), *end = at ? at : sim + strlen(sim);
        int on = 1;
        if (at) {                           // connected while an odd number of the times have passed
            double now = (double)(host_ticks() - start_ns) / 1e9;
            on = 0;
            for (const char *q = at + 1; *q;) {
                char *e;
                double t = strtod(q, &e);
                if (e == q || now < t) break;
                on = !on;
                q = *e == '-' ? e + 1 : e + strlen(e);
            }
        }
        if (!on) { pad_presence(0, NULL); prompts_pad_seen(0); return 0; }
        st->connected = 1;
        int word_kind = -1;
        for (const char *p = sim + 4; p < end;) {
            char name[16];
            size_t n = strcspn(p, "=,@");
            snprintf(name, sizeof name, "%.*s", (int)(n < 15 ? n : 15), p);
            p += n;
            double v = 0;
            if (*p == '=') v = strtod(p + 1, (char **)&p);
            else if (kind_of_word(name) >= 0) word_kind = kind_of_word(name);
            if (!strcmp(name, "lx")) st->lx = (float)v; else if (!strcmp(name, "ly")) st->ly = (float)v;
            else if (!strcmp(name, "rx")) st->rx = (float)v; else if (!strcmp(name, "ry")) st->ry = (float)v;
            else if (!strcmp(name, "lt")) st->lt = (float)v; else if (!strcmp(name, "rt")) st->rt = (float)v;
            else if (!strcmp(name, "buttons")) st->buttons = (uint32_t)v; else if (!strcmp(name, "dpad")) st->pov = (int)v;
            while (p < end && *p != ',') ++p;
            if (p < end) ++p;
        }
        if (script_trigger_pair) { st->lt = script_lt; st->rt = script_rt; }   // physical pair, before F10/layout
        st->buttons |= script_pad;          // BOOT_KEYS pad: steps
        int kind = sim_kind >= 0 ? sim_kind : word_kind >= 0 ? word_kind : PAD_KIND_OTHER;
        pad_presence(1, kind == PAD_KIND_DUALSENSE ? "simulated DualSense" : kind == PAD_KIND_DUALSHOCK4 ? "simulated DualShock 4" :
                        kind == PAD_KIND_EDGE ? "simulated DualSense Edge" : "simulated pad");
        set_identity(kind, NULL);
        prompts_pad_seen(1);
        // the F10 menu: bit 12 is the PS/Guide button, bit 13 the touchpad click (a simulated DualSense)
        int u, d, l, r;
        if (script_axis[0] && (!strncmp(script_axis, "lx", 2) || !strncmp(script_axis, "ly", 2)) && strchr(script_axis, '='))   // BOOT_KEYS padaxis: the menu sees a scripted left stick
            (script_axis[1] == 'x' ? &st->lx : &st->ly)[0] = (float)atof(strchr(script_axis, '=') + 1);
        pov_dirs(st->pov, &u, &d, &l, &r);
        uint32_t inject, hide = portmenu_pad(st->buttons & 0xFFF, u, d, l, r, st->lx, st->ly, (st->buttons >> 12) & 1, (st->buttons >> 13) & 1, 1, &inject);
        uint32_t extras = kind_native(pad_kind) ? st->buttons & pad_game_extras(1) & 0xF000 : 0;
        st->buttons &= 0xFFF;
        if (hide == ~0u) { pad_neutral(st); return 1; }
        st->buttons = (st->buttons & ~hide) | inject | extras;
        if (st->buttons || fabsf(st->lx) > 0.5f || fabsf(st->ly) > 0.5f || fabsf(st->rx) > 0.5f || fabsf(st->ry) > 0.5f ||
            st->lt > 0.5f || st->rt > 0.5f || st->pov >= 0)
            prompts_input(1);
        float *ax[6] = {&st->lx, &st->ly, &st->rx, &st->ry, &st->lt, &st->rt};
        static const char *an[6] = {"lx", "ly", "rx", "ry", "lt", "rt"};
        float axv = script_axis[0] ? (float)atof(strchr(script_axis, '=') ? strchr(script_axis, '=') + 1 : "0") : 0;
        for (int k = 0; k < 4; ++k) if (script_axis[0] && !strncmp(script_axis, an[k], 2)) *ax[k] = axv;   // sticks: seen by the layout
        if (kind_native(pad_kind)) pad_native(st, st->buttons);
        else pad_apply_layout(st, st->buttons);   // buttons= is the physical P_* mask, mapped like a real pad
        st->buttons |= script_raw;
        for (int k = 0; k < 6; ++k) if (script_axis[0] && !strncmp(script_axis, an[k], 2)) *ax[k] = axv;
        static uint32_t last_sim = ~0u;
        if (diag_on && st->buttons != last_sim) {
            last_sim = st->buttons;
            diag_event("pad: sim buttons %03X ry %.2f, %s (package in control %s)", st->buttons, st->ry, pad_mode, pad_ctl ? pad_ctl : "none");
        }
        rumble_update();                    // a connected DualSense still plays the rumble
        // the adaptive triggers and lightbar decide as for a real pad and only log it (padfx.m never touches a pad here)
        padfx_update(NULL, kind == PAD_KIND_DUALSENSE || kind == PAD_KIND_EDGE, st->lt, st->rt);
        return 1;
    }
    if (!headless && !NSApp.isActive) { st->connected = 1; st->native = kind_native(pad_kind); return 1; }   // another app has focus: centred, nothing pressed
    @autoreleasepool {
        GCExtendedGamepad *g = nil;
        GCController *ctl = nil;
        for (GCController *k in GCController.controllers) if ((g = k.extendedGamepad)) { ctl = k; break; }
        if ((g != nil) != pad_present)
            pad_presence(g != nil, g ? [NSString stringWithFormat:@"%@ (%@, %lu controllers)", ctl.vendorName ?: @"controller",
                                        ctl.productCategory ?: @"?", (unsigned long)GCController.controllers.count].UTF8String : NULL);
        prompts_pad_seen(g != nil);
        if (!g) return 0;
        rumble_update();
        st->connected = 1;
        int kind = PAD_KIND_OTHER;
        NSString *cat = ctl.productCategory ?: @"";
        if ([g isKindOfClass:GCDualSenseGamepad.class] || [cat hasPrefix:@"DualSense"])
            kind = [cat rangeOfString:@"Edge" options:NSCaseInsensitiveSearch].location != NSNotFound ? PAD_KIND_EDGE : PAD_KIND_DUALSENSE;
        else if ([g isKindOfClass:GCDualShockGamepad.class] || [cat hasPrefix:@"DualShock"]) kind = PAD_KIND_DUALSHOCK4;
        set_identity(kind, ctl.vendorName.UTF8String);
        padfx_update((__bridge void *)ctl, [g isKindOfClass:GCDualSenseGamepad.class], g.leftTrigger.value, g.rightTrigger.value);
        st->lx = g.leftThumbstick.xAxis.value; st->ly = g.leftThumbstick.yAxis.value;
        st->rx = g.rightThumbstick.xAxis.value; st->ry = g.rightThumbstick.yAxis.value;
        st->lt = g.leftTrigger.value; st->rt = g.rightTrigger.value;
        GCControllerButtonInput *b[12] = {g.buttonA, g.buttonB, g.buttonX, g.buttonY, g.leftShoulder, g.rightShoulder,
                                          g.buttonOptions, g.buttonMenu, g.leftThumbstickButton, g.rightThumbstickButton,
                                          g.leftTrigger, g.rightTrigger};          // P_* order
        uint32_t phys = 0;
        for (int i = 0; i < 12; ++i) if (b[i].pressed) phys |= 1u << i;
        GCControllerDirectionPad *d = g.dpad;
        int up = d.up.pressed, down = d.down.pressed, left = d.left.pressed, right = d.right.pressed;
        // the F10 menu's shortcut: the touchpad click (DualSense, DualShock 4), the PS/Guide button, or holds (portmenu.m)
        GCControllerButtonInput *touch = nil;
        if ([g isKindOfClass:GCDualSenseGamepad.class]) touch = ((GCDualSenseGamepad *)g).touchpadButton;
        else if ([g isKindOfClass:GCDualShockGamepad.class]) touch = ((GCDualShockGamepad *)g).touchpadButton;
        GCControllerButtonInput *home = g.buttonHome;
        int choice = settings_get("controls.menu_button");
        static __weak GCControllerButtonInput *home_claimed;
        if (home && home != home_claimed && (choice == 1 || (choice == 0 && !touch) || kind_native(kind))) {
            // the system's Home-button gesture (Launchpad / game overlay) off while we're frontmost, so the press is ours
            home.preferredSystemGestureState = GCSystemGestureStateDisabled;
            home_claimed = home;
            if (diag_on) diag_event("pad: claimed the Home button from the system (%s)", ctl.vendorName.UTF8String ?: "?");
        }
        if (diag_on && home.pressed) { static int logged; if (!logged++) diag_event("pad: Home button delivered to the app"); }
        uint32_t inject, hide = portmenu_pad(phys, up, down, left, right, st->lx, st->ly, home.pressed, touch.pressed, touch != nil, &inject);
        if (hide == ~0u) { pad_neutral(st); return 1; }
        phys = (phys & ~hide) | inject;
        if (kind_native(kind)) {           // PS, the touchpad click, the Edge's back paddles, when not the menu's shortcut
            GCControllerButtonInput *lp = nil, *rp = nil;
            if (kind == PAD_KIND_EDGE) if (@available(macOS 13.0, *)) {
                lp = ctl.physicalInputProfile.buttons[GCInputLeftPaddle]; rp = ctl.physicalInputProfile.buttons[GCInputRightPaddle];
            }
            uint32_t extras = (home.pressed ? 1u << P_PS : 0) | (touch.pressed ? 1u << P_TOUCHPAD : 0) |
                              (lp.pressed ? 1u << P_LPADDLE : 0) | (rp.pressed ? 1u << P_RPADDLE : 0);
            pad_native(st, phys | (extras & pad_game_extras(touch != nil)));
        } else
            pad_apply_layout(st, phys);
        if (phys || up || down || left || right || fabsf(st->lx) > 0.5f || fabsf(st->ly) > 0.5f || fabsf(st->rx) > 0.5f ||
            fabsf(st->ry) > 0.5f || g.leftTrigger.value > 0.5f || g.rightTrigger.value > 0.5f)
            prompts_input(1);
        if (up || down || left || right) {
            int x = right - left, y = up - down;
            static const int pov[3][3] = {{22500, 27000, 31500}, {18000, -1, 0}, {13500, 9000, 4500}};   // [x+1][y+1]
            st->pov = pov[x + 1][y + 1];
        }
        if (diag_on) {                   // each change of buttons/D-pad, or of a stick/trigger by 0.25
            static PadState last;
            float d = fabsf(st->lx - last.lx) + fabsf(st->ly - last.ly) + fabsf(st->rx - last.rx) + fabsf(st->ry - last.ry) +
                      fabsf(st->lt - last.lt) + fabsf(st->rt - last.rt);
            if (st->buttons != last.buttons || st->pov != last.pov || d > 0.25f) {
                diag_event("pad: buttons %03X pov %d lx %.2f ly %.2f rx %.2f ry %.2f lt %.2f rt %.2f, %s (package in control %s, app active %d)",
                           st->buttons, st->pov, st->lx, st->ly, st->rx, st->ry, st->lt, st->rt, pad_mode, pad_ctl ? pad_ctl : "none",
                           (int)NSApp.isActive);
                last = *st;
            }
        }
    }
    return 1;
}
