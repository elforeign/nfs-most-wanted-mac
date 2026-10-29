// padprompts.c - console button prompts instead of keyboard keycaps while a game controller is the active input.
//
// Every FNG package carries both prompt sets (measured on the X360 front-end packages): objects flagged
// 0x40 (FEObject +0x1C, from the package's OP tag) are the console glyphs (textures CROSS, TRIANGLE, L1, START, ...)
// and their hint labels, objects flagged 0x8 the PC keycaps (PC_KEY_ESC, PC_KEY_1, ...). The PC hides the console set
// on every package (cFEngGameInterface::PackageWasLoaded 00525520 runs FEngHidePCObjects::Callback 00516230), refuses
// to show it again (FEngSetVisible 00514CC0 tests 0x40) and never draws it (cFEngRender::RenderObject 005A45B0 tests
// 0x40). The X360 does the same with 0x8 (Function_822830E0, Function_8227FE80, and its RenderObject 822D3B90 skips
// 0xC). Here the choice is made when an object is drawn, so it follows the input device live:
// - 00516230 hides nothing; it still takes the console set out of mouse picking (clears 0x10000000, as the PC) and
//   copies a group's 0x8/0x40 to its children (FEngine::RenderGroup 005BB9C0 draws a group's children whatever the
//   group's flags; the PC relied on FEngSetInvisible's recursion for that).
// - 00514CC0 also shows 0x40 objects (code hides and shows prompts; which set is drawn is decided below).
// - 005A45B0 skips 0x40 objects with the keyboard/mouse (the PC) and 0x8 objects with a controller (the X360); the
//   console glyph textures are swapped for the layout's art while drawn (the image is rebuilt, flag 0x2000000).
//   Not in a package with no 0x40 object at all (Options > Video, Controller: PC-only screens that carry 0x8 for their own
//   controls, e.g. the Advanced button): there the PC_KEY_* keycap images are drawn as the console glyph they stand for (ESC TRIANGLE, 1 CIRCLE, 2 SQUARE, 3 SELECT,
//   4 START: the hint bar's Done / Defaults / Clear) and the rest stays drawn.
// NFS_BUTTON_PROMPTS=auto (default: console prompts while the last input came from a controller; a pad found at
// start-up counts), =console, =pc (the original functions, untouched).
//
// The glyph art: the PC packs have none of it except L1/R1 (PC arrows in a ring). The PlayStation layout gets
// PlayStation glyphs drawn here at start-up (64x64 A8R8G8B8, CoreGraphics), the Xbox layout the X360's own glyphs
// (32x32 DXT5 in X360 GLOBAL/GLOBALB.BUN, which keeps the PS2 texture names with Xbox art in them), through the
// extra-texture list (x360_textures.m). Both sets are made; the list holds one entry per glyph under a private hash
// (PADPROMPT_<name>) whose TextureInfo is the current layout's (GetTextureInfo 00503400 doesn't cache list entries).
// Which art each texture gets follows what the button does in menus (user32.m pad_apply_layout): the table below,
// checked with BOOT_DIAG's fepad: lines (the FE pad events, 008F3434) and the FE input mapping in prompts.txt.
#include <CoreGraphics/CoreGraphics.h>
#include <CoreText/CoreText.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include "host.h"

int user32_pad_layout_xbox(void);
uint32_t padbind_ps_label(int slot, uint32_t fallback);   // a PlayStation pad: from its bindings
static int glyphs_xbox(void) { return user32_pad_layout_xbox() && !user32_pad_native(); }   // a PlayStation pad: its own glyphs
uint32_t extra_texture_add(Cpu *c, const uint8_t rec[124], const uint8_t pl[32], uint32_t fmt, uint32_t data, uint32_t size, int add);
uint32_t x360_texture_load(Cpu *c, const char *file, uint32_t hash, uint32_t as_hash);

#define FN_ADD_OTHER_TEXTURE 0x004FAF10u   // cdecl (uint hash, TextureInfo *)
#define OTHER_TEX_HASH 0x009159A0u         // OtherEcstacyTextures: hashes, TextureInfo *, count
#define OTHER_TEX_TI 0x00915A30u
#define OTHER_TEX_N 0x00915FA8u
#define FN_GET_TEXTURE_INFO 0x00503400u    // cdecl (uint hash, int use_default, int) -> TextureInfo *

static uint32_t str_hash(const char *s) {   // bStringHash of the upper-cased name (cFEngGameInterface::LoadResources)
    uint32_t h = 0xFFFFFFFFu;
    for (; *s; ++s) h = h * 33 + (uint32_t)(*s >= 'a' && *s <= 'z' ? *s - 32 : *s);
    return h;
}

// ---------------- which prompts ----------------
static int env_mode = -1;                  // 0 pc, 1 console, 2 auto
void user32_hide_pointer(void);
static int pad_lost;                       // the controller that was the active input has gone (fe_console.c: the game's unplugged check)
static int pad_active = -1;                // auto: the last input came from a controller (1) or keyboard/mouse (0)
static int physical_input;                // connection/prompt overrides never count as physical input
int prompts_last_input(void) { return physical_input; }
static int mode(void) {
    if (env_mode < 0) {
        const char *e = getenv("NFS_BUTTON_PROMPTS");
        env_mode = e && !strcasecmp(e, "pc") ? 0 : e && !strcasecmp(e, "console") ? 1 : 2;
    }
    return env_mode;
}
static int have_art(void);
static int console(void) {
    int m = mode();
    return m && (m == 1 || pad_active == 1) && have_art();
}
void prompts_input(int pad) {              // user32.m: a key or mouse button (0), a controller button or stick (1)
    static float moved;
    if (pad < 0) { moved += 1; if (moved < 16) return; pad = 0; }   // mouse movement: about 16 points of it
    moved = 0;
    physical_input = pad;
    if (pad == 1) user32_hide_pointer();   // F21: hidden until the mouse moves (a key press or click also hands the prompts back)
    if (!pad) pad_lost = 0;                // keys or the mouse: the player has moved on from the lost controller
    if (pad_active == pad) return;
    pad_active = pad;
    if (mode() == 2) fprintf(stderr, "boot: button prompts: %s\n", pad ? "console" : "keyboard");
    if (diag_on) diag_event("prompts: input from the %s", pad ? "controller" : "keyboard/mouse");
}
void prompts_mouse_moved(float points) {   // user32.m: mouse movement over the window
    for (float d = points; d >= 1; d -= 1) prompts_input(-1);
}
void prompts_pad_seen(int connected) {     // user32.m pad_read: a controller is there (at start-up: console prompts)
    int physical = physical_input;
    if (connected) pad_lost = 0;
    if (connected && pad_active < 0) prompts_input(1);
    else if (!connected && pad_active == 1) { prompts_input(0); pad_lost = 1; }
    physical_input = physical;
}
int prompts_pad_lost(void) { return pad_lost; }
// The game's own pointer (texture 0x6B6A294F) is drawn by FUN_0056FDB0 (cdecl, FE x, y), called from FEManager::Render
// 00516F70 after the FE pass every frame: always in the front end (game flow 3), elsewhere for 2 s after it moved, and
// at (320, 240) until the mouse is used, so a pad player saw a white arrow mid-screen in the menus. While the controller
// is the active input it isn't drawn; the first mouse movement (about 16 points, prompts_input) or a key/click hands
// the input back and it is drawn again, as the macOS arrow (F21, user32_hide_pointer).
void f_0056FDB0_orig(Cpu *c);
void f_0056FDB0(Cpu *c) {
    if (pad_active == 1) {
        if (diag_on) { static int once; if (!once++) diag_event("prompts: game pointer hidden while the controller is the input"); }
        c->esp += 4;                       // cdecl: the caller pops (x, y)
        return;
    }
    f_0056FDB0_orig(c);
}

// ---------------- the glyph art ----------------
enum { A_CROSS, A_CIRCLE, A_SQUARE, A_TRIANGLE, A_L1, A_R1, A_L2, A_R2, A_R3, A_OPTIONS, A_CREATE, A_DPAD, A_DPAD_DOWN,
       A_DPAD_LEFT, A_DPAD_RIGHT, A_DPAD_LEFTRIGHT, A_RSTICK, A_RSTICK_UP, A_RSTICK_DOWN, A_LSTICK };
// The textures the packages and the code use (FNG resource lists; FEngSetButtonTexture and friends set LEFT_ANALOG,
// DPAD, RIGHT_ANALOG, L1, R1), the PlayStation art each gets and the X360 texture whose art the Xbox layout shows. The
// packages pair each console glyph with a PC keycap under the same label (CROSS = Enter, TRIANGLE = Esc, CIRCLE = 1,
// SQUARE = 2, SELECT = 3, START = 4, L1/R1 = the PC's L1/R1 arrows), and in menus user32.m pad_apply_layout sends
// those actions from the console version's buttons: the PS2's (each texture its own symbol; START = Options, SELECT =
// Create) and the X360's (the X360 keeps the PS2 names with Xbox art: CROSS A, TRIANGLE B, CIRCLE Y, SQUARE X, START
// Start; its LB/RB art is in L2/R2 and Buttons 5/6 come from LB/RB here; SELECT = Back).
static const struct { const char *name; int ps; const char *xb; } glyphs[] = {
    {"CROSS", A_CROSS, "CROSS"},       {"TRIANGLE", A_TRIANGLE, "TRIANGLE"}, {"SQUARE", A_SQUARE, "SQUARE"},
    {"CIRCLE", A_CIRCLE, "CIRCLE"},  {"L1", A_L1, "L2"},                 {"R1", A_R1, "R2"},
    {"L2", A_L2, "L1"},                {"R2", A_R2, "R1"},                 {"R3", A_R3, "R3"},
    {"START", A_OPTIONS, "START"},     {"SELECT", A_CREATE, "BACK"},       {"DPAD", A_DPAD, "DPAD"},
    {"DPAD_DOWN", A_DPAD_DOWN, "DPAD_DOWN"},   {"DPAD_LEFT", A_DPAD_LEFT, "DPAD_LEFT"},
    {"DPAD_RIGHT", A_DPAD_RIGHT, "DPAD_RIGHT"}, {"DPAD_LEFTRIGHT", A_DPAD_LEFTRIGHT, "DPAD_LEFTRIGHT"},
    {"RIGHT_ANALOG", A_RSTICK, "RIGHT_ANALOG"}, {"RIGHT_ANALOG_UP", A_RSTICK_UP, "RIGHT_ANALOG_UP"},
    {"RIGHT_ANALOG_DOWN", A_RSTICK_DOWN, "RIGHT_ANALOG_DOWN"}, {"LEFT_ANALOG", A_LSTICK, "LEFT_ANALOG"},
};
#define NGLYPH (sizeof glyphs / sizeof *glyphs)
static uint32_t glyph_hash[NGLYPH], glyph_private[NGLYPH], glyph_ti[2][NGLYPH];   // [0] PlayStation, [1] Xbox
static int glyph_slot[NGLYPH] = {[0 ... NGLYPH - 1] = -1};   // index in the extra-texture list
static int art_made, art_layout = -1;

#define S 64                               // art size (the FE draws the glyphs at ~32 px per 480 lines)
static void rgb(CGContextRef g, double r, double gg, double b, int fill) {
    if (fill) CGContextSetRGBFillColor(g, r, gg, b, 1); else CGContextSetRGBStrokeColor(g, r, gg, b, 1);
}
static void base_disc(CGContextRef g) {    // a dark round button with a light rim
    rgb(g, 0.10, 0.10, 0.11, 1);
    CGContextFillEllipseInRect(g, CGRectMake(3, 3, S - 6, S - 6));
    CGContextSetLineWidth(g, 2.5);
    rgb(g, 0.62, 0.62, 0.66, 0);
    CGContextStrokeEllipseInRect(g, CGRectMake(3, 3, S - 6, S - 6));
}
static void round_rect(CGContextRef g, CGRect r, double rad, int fill) {
    CGPathRef p = CGPathCreateWithRoundedRect(r, rad, rad, NULL);
    CGContextAddPath(g, p);
    if (fill) CGContextFillPath(g); else CGContextStrokePath(g);
    CGPathRelease(p);
}
static void label(CGContextRef g, const char *text, double size, double cx, double cy) {   // white bold text centred
    CTFontRef font = CTFontCreateUIFontForLanguage(kCTFontUIFontEmphasizedSystem, size, NULL);
    CFStringRef s = CFStringCreateWithCString(NULL, text, kCFStringEncodingUTF8);
    CGColorRef white = CGColorCreateGenericRGB(1, 1, 1, 1);
    const void *k[] = {kCTFontAttributeName, kCTForegroundColorAttributeName}, *v[] = {font, white};
    CFDictionaryRef attr = CFDictionaryCreate(NULL, k, v, 2, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CFAttributedStringRef as = CFAttributedStringCreate(NULL, s, attr);
    CTLineRef line = CTLineCreateWithAttributedString(as);
    CGRect b = CTLineGetImageBounds(line, g);
    CGContextSetTextPosition(g, cx - b.origin.x - b.size.width / 2, cy - b.origin.y - b.size.height / 2);
    CTLineDraw(line, g);
    CFRelease(line); CFRelease(as); CFRelease(attr); CGColorRelease(white); CFRelease(s); CFRelease(font);
}
static void shoulder(CGContextRef g, const char *text, int trigger) {   // L1/R1: a bumper; L2/R2: a trigger (taller, rounder)
    CGRect r = trigger ? CGRectMake(6, 6, S - 12, S - 12) : CGRectMake(3, 12, S - 6, S - 24);
    rgb(g, 0.10, 0.10, 0.11, 1);
    round_rect(g, r, trigger ? 16 : 11, 1);
    CGContextSetLineWidth(g, 2.5);
    rgb(g, 0.62, 0.62, 0.66, 0);
    round_rect(g, r, trigger ? 16 : 11, 0);
    label(g, text, 25, S / 2.0, S / 2.0);
}
static void arrow(CGContextRef g, double cx, double cy, double dx, double dy, double size) {   // a filled triangle
    double px = -dy, py = dx;
    CGContextMoveToPoint(g, cx + dx * size, cy + dy * size);
    CGContextAddLineToPoint(g, cx + px * size * 0.8, cy + py * size * 0.8);
    CGContextAddLineToPoint(g, cx - px * size * 0.8, cy - py * size * 0.8);
    CGContextClosePath(g);
    CGContextFillPath(g);
}
static void dpad(CGContextRef g, int up, int down, int left, int right) {   // a D-pad, the named arms lit
    double w = 20, l = S - 8, o = (S - w) / 2;
    rgb(g, 0.10, 0.10, 0.11, 1);
    CGContextFillRect(g, CGRectMake(o - 2, 2, w + 4, l + 4));
    CGContextFillRect(g, CGRectMake(2, o - 2, l + 4, w + 4));
    int lit[4] = {up, down, left, right};
    double ax[4] = {0, 0, -1, 1}, ay[4] = {1, -1, 0, 0};
    for (int i = 0; i < 4; ++i) {
        double cx = S / 2.0 + ax[i] * 19, cy = S / 2.0 + ay[i] * 19;
        if (lit[i]) rgb(g, 0.95, 0.95, 0.95, 1); else rgb(g, 0.38, 0.38, 0.40, 1);
        CGContextFillRect(g, CGRectMake(cx - 8.5, cy - 8.5, 17, 17));
        if (lit[i]) rgb(g, 0.10, 0.10, 0.11, 1); else rgb(g, 0.62, 0.62, 0.66, 1);
        arrow(g, cx, cy, ax[i], ay[i], 6);
    }
    rgb(g, 0.38, 0.38, 0.40, 1);
    CGContextFillRect(g, CGRectMake(o, o, w, w));
}
static void stick(CGContextRef g, const char *text, int up, int down) {   // an analog stick, arrows for the direction
    base_disc(g);
    rgb(g, 0.30, 0.30, 0.32, 1);
    CGContextFillEllipseInRect(g, CGRectMake(15, 15, S - 30, S - 30));
    label(g, text, 20, S / 2.0, S / 2.0);
    rgb(g, 0.95, 0.95, 0.95, 1);
    if (up) arrow(g, S / 2.0, S - 9, 0, 1, 6);
    if (down) arrow(g, S / 2.0, 9, 0, -1, 6);
}
static void draw_art(CGContextRef g, int art) {
    CGContextSetLineCap(g, kCGLineCapRound);
    CGContextSetLineJoin(g, kCGLineJoinRound);
    double c = S / 2.0;
    switch (art) {
    case A_CROSS:                          // the PlayStation colours: blue cross, red circle, pink square, green triangle
        base_disc(g); CGContextSetLineWidth(g, 5.5); rgb(g, 0.49, 0.70, 0.93, 0);
        CGContextMoveToPoint(g, c - 12, c - 12); CGContextAddLineToPoint(g, c + 12, c + 12);
        CGContextMoveToPoint(g, c - 12, c + 12); CGContextAddLineToPoint(g, c + 12, c - 12);
        CGContextStrokePath(g); break;
    case A_CIRCLE:
        base_disc(g); CGContextSetLineWidth(g, 5.5); rgb(g, 0.96, 0.42, 0.44, 0);
        CGContextStrokeEllipseInRect(g, CGRectMake(c - 14, c - 14, 28, 28)); break;
    case A_SQUARE:
        base_disc(g); CGContextSetLineWidth(g, 5.5); rgb(g, 0.89, 0.55, 0.84, 0);
        CGContextStrokeRect(g, CGRectMake(c - 12.5, c - 12.5, 25, 25)); break;
    case A_TRIANGLE:
        base_disc(g); CGContextSetLineWidth(g, 5.5); rgb(g, 0.25, 0.84, 0.66, 0);
        CGContextMoveToPoint(g, c, c + 15); CGContextAddLineToPoint(g, c + 14.5, c - 10);
        CGContextAddLineToPoint(g, c - 14.5, c - 10); CGContextClosePath(g); CGContextStrokePath(g); break;
    case A_L1: shoulder(g, "L1", 0); break;
    case A_R1: shoulder(g, "R1", 0); break;
    case A_L2: shoulder(g, "L2", 1); break;
    case A_R2: shoulder(g, "R2", 1); break;
    case A_R3: stick(g, "R3", 0, 0); break;
    case A_OPTIONS:                        // the DualSense Options button: a small pill, three lines
    case A_CREATE: {                       // Create: the same pill, its three-ray mark
        CGRect r = CGRectMake(14, 4, S - 28, S - 8);
        rgb(g, 0.10, 0.10, 0.11, 1); round_rect(g, r, 13, 1);
        CGContextSetLineWidth(g, 2.5); rgb(g, 0.62, 0.62, 0.66, 0); round_rect(g, r, 13, 0);
        CGContextSetLineWidth(g, 4); rgb(g, 0.95, 0.95, 0.95, 0);
        if (art == A_OPTIONS)
            for (int i = -1; i <= 1; ++i) { CGContextMoveToPoint(g, c - 8, c + i * 9); CGContextAddLineToPoint(g, c + 8, c + i * 9); }
        else
            for (int i = -1; i <= 1; ++i) {
                double a = M_PI / 2 + i * 0.7;    // three separate dashes fanning out upwards
                CGContextMoveToPoint(g, c + cos(a) * 9, c - 11 + sin(a) * 9);
                CGContextAddLineToPoint(g, c + cos(a) * 20, c - 11 + sin(a) * 20);
            }
        CGContextStrokePath(g); break;
    }
    case A_DPAD: dpad(g, 1, 1, 1, 1); break;
    case A_DPAD_DOWN: dpad(g, 0, 1, 0, 0); break;
    case A_DPAD_LEFT: dpad(g, 0, 0, 1, 0); break;
    case A_DPAD_RIGHT: dpad(g, 0, 0, 0, 1); break;
    case A_DPAD_LEFTRIGHT: dpad(g, 0, 0, 1, 1); break;
    case A_RSTICK: stick(g, "R", 0, 0); break;
    case A_RSTICK_UP: stick(g, "R", 1, 0); break;
    case A_RSTICK_DOWN: stick(g, "R", 0, 1); break;
    case A_LSTICK: stick(g, "L", 0, 0); break;
    }
}
// One PlayStation glyph as a texture: drawn premultiplied, stored straight-alpha BGRA (D3DFMT_A8R8G8B8).
static uint32_t make_ps_glyph(Cpu *c, int art, const char *name, uint32_t hash) {
    uint32_t size = S * S * 4, data = gmalloc(size);
    uint8_t *px = G + data;
    CGColorSpaceRef cs = CGColorSpaceCreateDeviceRGB();
    CGContextRef g = CGBitmapContextCreate(px, S, S, 8, S * 4, cs, kCGImageAlphaPremultipliedFirst | kCGBitmapByteOrder32Little);
    CGColorSpaceRelease(cs);
    if (!g) return 0;
    CGContextClearRect(g, CGRectMake(0, 0, S, S));
    draw_art(g, art);
    CGContextFlush(g);
    CGContextRelease(g);
    for (uint32_t i = 0; i < S * S; ++i) {
        uint8_t *p = px + 4 * i, a = p[3];
        if (a && a < 255) for (int k = 0; k < 3; ++k) p[k] = (uint8_t)((p[k] * 255 + a / 2) / a);
    }
    // The TextureInfo: the PC's own L1 glyph record (GLOBAL\GLOBALB.BUN: class 001A93CF, render order 5, alpha usage
    // 2, blend 1; read from the player's file at build time, tools_snapshot/pubtables_gen.py), resized; 32-bit
    // (compression 0x20, as Init's P8 path writes for A8R8G8B8).
#include "gen/padprompts_l1.inc"                            // tools/gen_tables.py, from the player's GLOBALB.BUN
    uint8_t rec[124], pl[32] = {[8] = 1};
    memcpy(rec, l1, sizeof rec);
    snprintf((char *)rec + 0x0C, 24, "%s", name);
    memcpy(rec + 0x24, &hash, 4);
    rec[0x44] = S; rec[0x45] = 0; rec[0x46] = S; rec[0x47] = 0;
    rec[0x48] = rec[0x49] = 6;                                 // log2 size
    rec[0x4A] = 0x20;
    uint32_t ti = extra_texture_add(c, rec, pl, 0x15, data, size, 0);
    if (ti && diag_on) {
        uint8_t *img = malloc(size);
        memcpy(img, px, size);
        char png[1100];
        snprintf(png, sizeof png, "%s/prompt_%s.png", diag_dir, name);
        diag_png(png, img, S, S, S * 4, 1);
        free(img);
    }
    return ti;
}
// Puts the layout's art in the list entries (made at start-up, swapped when the Controller menu changes the layout).
static void use_layout(void) {
    int xb = glyphs_xbox();
    if (xb == art_layout) return;
    art_layout = xb;
    for (unsigned i = 0; i < NGLYPH; ++i)
        if (glyph_slot[i] >= 0) M32(OTHER_TEX_TI + 4u * (uint32_t)glyph_slot[i]) = glyph_ti[xb][i] ? glyph_ti[xb][i] : glyph_ti[0][i];
    if (diag_on) diag_event("prompts: %s glyphs", xb ? "Xbox" : "PlayStation");
}
static int have_art(void) {                // the layout has its glyphs (the Xbox ones come from the X360 files)
    if (!art_made) return 0;
    use_layout();
    return glyph_ti[art_layout][0] != 0;
}
void prompts_textures(Cpu *c) {            // x360_textures.m, at the first texture Init (start-up)
    if (art_made || !mode()) return;
    art_made = 1;
    int nps = 0, nxb = 0;
    for (unsigned i = 0; i < NGLYPH; ++i) {
        char name[48];
        snprintf(name, sizeof name, "PADPROMPT_%s", glyphs[i].name);
        glyph_hash[i] = str_hash(glyphs[i].name);
        glyph_private[i] = str_hash(name);
        nps += (glyph_ti[0][i] = make_ps_glyph(c, glyphs[i].ps, name, glyph_private[i])) != 0;
        nxb += (glyph_ti[1][i] = x360_texture_load(c, "GLOBAL/GLOBALB.BUN", str_hash(glyphs[i].xb), glyph_private[i])) != 0;
    }
    if (nxb < (int)NGLYPH) for (unsigned i = 0; i < NGLYPH; ++i) glyph_ti[1][i] = 0;   // all or nothing
    for (unsigned i = 0; i < NGLYPH; ++i) {
        uint32_t ti = glyph_ti[0][i] ? glyph_ti[0][i] : glyph_ti[1][i];
        if (!ti || M32(OTHER_TEX_N) >= 36) continue;           // the list's capacity (x360_textures.m)
        glyph_slot[i] = (int)M32(OTHER_TEX_N);
        uint32_t esp = c->esp, eax = c->eax, ecx = c->ecx, edx = c->edx, a[2] = {glyph_private[i], ti};
        call_guest(c, FN_ADD_OTHER_TEXTURE, 2, a);
        c->esp = esp; c->eax = eax; c->ecx = ecx; c->edx = edx;
        if (M32(OTHER_TEX_HASH + 4u * (uint32_t)glyph_slot[i]) != glyph_private[i]) glyph_slot[i] = -1;
    }
    fprintf(stderr, "boot: button prompt glyphs: %d PlayStation, %d Xbox (prompts %s)\n", nps, nxb < (int)NGLYPH ? 0 : nxb,
            mode() == 1 ? "console" : "auto");
    if (diag_on) diag_event("prompts: %d PlayStation glyphs, %d of %u Xbox glyphs from the X360 files", nps, nxb, (unsigned)NGLYPH);
}
static uint32_t glyph_sub(uint32_t tex) {  // the private hash to draw instead of a console glyph texture (0: none)
    for (unsigned i = 0; i < NGLYPH; ++i) if (glyph_hash[i] == tex) return glyph_slot[i] >= 0 ? glyph_private[i] : 0;
    return 0;
}

// ---------------- the hooks ----------------
// Objects of PC-only packages (no 0x40 object) that stay drawn with a controller despite flag 0x8: an open-addressing
// table of object addresses -> keep, refreshed for every 0x8 object when its package loads (addresses get reused).
#define KEEP_N 16384u
static struct { uint32_t o, keep, cap; } keep_tab[KEEP_N];   // cap: a hidden keycap's console glyph texture (hash), 0 none
static void keep_set(uint32_t o, uint32_t keep, uint32_t cap) {
    uint32_t i = (o >> 3) & (KEEP_N - 1);
    for (unsigned n = 0; n < KEEP_N; ++n, i = (i + 1) & (KEEP_N - 1))
        if (keep_tab[i].o == o || !keep_tab[i].o) { keep_tab[i].o = o; keep_tab[i].keep = keep; keep_tab[i].cap = cap; return; }
}
static int keep_get(uint32_t o) {
    uint32_t i = (o >> 3) & (KEEP_N - 1);
    for (unsigned n = 0; n < KEEP_N && keep_tab[i].o; ++n, i = (i + 1) & (KEEP_N - 1))
        if (keep_tab[i].o == o) return (int)keep_tab[i].keep;
    return 0;
}
static uint32_t cap_get(uint32_t o) {
    uint32_t i = (o >> 3) & (KEEP_N - 1);
    for (unsigned n = 0; n < KEEP_N && keep_tab[i].o; ++n, i = (i + 1) & (KEEP_N - 1))
        if (keep_tab[i].o == o) return keep_tab[i].cap;
    return 0;
}
void f_00516230_orig(Cpu *c);
void f_00516230(Cpu *c) {                  // FEngHidePCObjects::Callback (thiscall, FEObject *) -> bool, every object
    if (!mode()) { f_00516230_orig(c); return; }
    uint32_t o = arg(c, 0), f = M32(o + 0x1C);
    if (f & 0x40) M32(o + 0x1C) = (f & ~0x10000000u) | 0x400000u;   // not mouse-picked (as the PC), changed
    if (M32(o + 0x18) == 5 && (f & 0x48))                     // a group: its set to the children (visited next)
        for (uint32_t k = M32(o + 0x64), n = M32(o + 0x60); k && n; k = M32(k + 4), --n) M32(k + 0x1C) |= f & 0x48;
    c->eax = (c->eax & ~0xFFu) | 1;
    c->esp += 8;
}
void f_00514CC0_orig(Cpu *c);
void f_00514CC0(Cpu *c) {                  // FEngSetVisible (cdecl, FEObject *): also the console set
    uint32_t o = arg(c, 0);
    if (!mode() || !o || !(M32(o + 0x1C) & 0x40)) { f_00514CC0_orig(c); return; }
    M32(o + 0x1C) &= ~0x40u;
    f_00514CC0_orig(c);
    M32(o + 0x1C) |= 0x40u;
}
void f_005A45B0_orig(Cpu *c);
static void render_prompt_object(Cpu *c) {                  // cFEngRender::RenderObject (thiscall, FEObject *, FEPackageRenderInfo *)
    if (!mode()) { f_005A45B0_orig(c); return; }
    uint32_t o = arg(c, 0), f = M32(o + 0x1C);
    int con = console();
    uint32_t cap = con && (f & 0x8) && !keep_get(o) ? glyph_sub(cap_get(o)) : 0;   // a PC-only package's keycap: the console glyph
    if (((f & (con ? 0x8u : 0x40u)) && !(con && (f & 0x8) && keep_get(o)) && !cap) || (con && (f & 0x44) == 0x44)) { c->esp += 12; return; }   // the X360 skips 0x4 too
    uint32_t tex = 0, sub = 0;
    if (con && M32(o + 0x18) == 1 && ((sub = cap) || (sub = glyph_sub(M32(o + 0x24))))) {
        tex = M32(o + 0x24);
        M32(o + 0x24) = sub;
        M32(o + 0x1C) |= 0x2000000u;                           // rebuild the cached quad with this texture
    }
    if (f & 0x40) M32(o + 0x1C) &= ~0x40u;
    f_005A45B0_orig(c);
    if (f & 0x40) M32(o + 0x1C) |= 0x40u;
    if (sub) { M32(o + 0x24) = tex; M32(o + 0x1C) |= 0x2000000u; }   // and again with the PC one when drawn next
}

// Scope even the prompts=off and hidden-object paths; no second hook for the
// guest entry point, and nested/full-output objects restore the previous owner.
void f_005A45B0(Cpu *c) {
    int saved = split_hud_begin(arg(c, 0));
    render_prompt_object(c);
    split_hud_end(saved);
}

// ---------------- the controller-diagram loading screen ----------------
// X360 loading tip 0x1B (GAME_TIP_USE_CONTROLLER_CONFIG, Loading_Controller.fng / WS_Loading_Controller.fng): the PC
// picker LoadingTips::GetARandomTipScreen (0056ADA0) never offers it and its screen (FUN_0058F070 ctor, FUN_0056B190)
// sets the diagram image 0x922A39C4 to texture 0, so the diagram was never shown. The X360 (Function_822BAC70) sets
// the image's texture (+0x24, flags |= 0x2400000) to LOADING_CONTROLLER_X360_C1/C2 (EC3C0443 / EC3C0444: options +0x8F,
// PC +0x87, nonzero / zero) and loads it from DYNTEX.BIN with eLoadStreamingTexture (00508b10, the PC's own loader).
// With a controller as the active input the picker offers the tip like the X360's, and the screen gets the
// layout's diagram: the PlayStation layout the DualShock 2 art (DF7B27A7 / DF7B27A8, the closest PlayStation art there
// is), the Xbox layout the X360's. The PlayStation layout gets its own callout labels (ps_labels below: what each control does in this port).  =off keeps the PC (never shown). NFS_TEST_LOADING_TIP=<n>
// (test only) makes every picker call answer tip n.
static int loading_on(void) {
    static int off = -1;
    if (off < 0) { const char *e = getenv("NFS_LOADING_CONTROLLER"); off = e && !strcasecmp(e, "off"); }
    int m = mode();   // not console(): the glyph art is made after the first frames, a start-up race loads before it
    return !off && m && (m == 1 || pad_active == 1);
}
void f_0056ADA0_orig(Cpu *c);
void f_0056ADA0(Cpu *c) {                  // LoadingTips::GetARandomTipScreen (cdecl, LoadingScreenTypes) -> eGameTips
    static int test = -2;
    if (test == -2) { const char *e = getenv("NFS_TEST_LOADING_TIP"); test = e && *e ? (int)strtol(e, NULL, 0) : -1; }
    if (test >= 0) { c->eax = (uint32_t)test; c->esp += 4; return; }
    if (!loading_on()) { f_0056ADA0_orig(c); return; }
    // The PC's picker with its `!= 0x1B` tests gone: tip t's three masks at 008F3D7C + 16 t.
    uint32_t type = arg(c, 0), opts = M32(M32(0x0091CF90u) + 0x10), m5 = 0, m6 = 1, m7;
    if (M8(opts + 0xAC) & 1) m5 = 1u << (M8(opts + 0xB0) & 31);
    else if (type == 1) m5 = 0x1E000;
    uint32_t race = M32(0x0091E004u);
    if (race) {
        uint32_t esp = c->esp, ecx = c->ecx;
        c->ecx = race;
        uint32_t startup = call_guest(c, 0x005DCA00u, 0, NULL);   // GRaceDatabase::GetStartupRace (thiscall)
        c->esp = esp; c->ecx = ecx;
        if (!startup) m6 = 0x11;
    }
    m7 = (type != 1 ? 0xFFFFFEF1u : 0) + 0x20F;
    uint32_t tips[0x1C], n = 0;
    for (uint32_t t = 0; t <= 0x1B; ++t) {
        uint32_t e = 0x008F3D7Cu + 16 * t;
        if ((M32(e) & m5) && (M32(e + 4) & m6) && (M32(e + 8) & m7)) tips[n++] = t;
    }
    if (!n) { c->eax = 0x1C; c->esp += 4; return; }
    uint32_t cnt = n, esp = c->esp;
    uint32_t r = call_guest(c, 0x0045DA30u, 1, &cnt);             // bRandom(n)
    c->esp = esp;
    c->eax = tips[r < n ? r : 0];
    c->esp += 4;
}
// The PC build never constructs the screen (FUN_0058F070 / 0056B190 have no callers: the package plays its script alone,
// the diagram image empty), so the X360's work is done when the package is loaded (PackageWasLoaded, below): the image's
// texture set to the diagram and a streaming load of it, once per layout (the PC never unloads it).
// The labels under the callouts: LanguageHash per button (17) and controller configuration (options +0x88, 5), the X360's
// table at 828FFBB0 (the PC's 0091D278 is zeroed, so every callout stayed hidden or the template's text), read from the
// player's X360 executable at build time (tools_snapshot/xex_read.c).
#include "gen/callout_labels.inc"   // tools/gen_tables.py, from the player's X360 default.xex (no X360 copy: zeros)
// The PlayStation layout's labels, one per callout slot, from what the controls do here (user32.m pad_apply_layout;
// hashes found with BOOT_DIAG_STRINGS=1, which logs the game's own SearchForString lookups). Slots sit at the Xbox
// diagram's positions, which the DualShock 2 art shares: 1 left shoulder (L1: nitrous), 2 left stick, 3 right shoulder
// (R1: e-brake), 4 triangle, 5 circle, 6 cross, 7 square, 8-11 D-pad up / left / right / down (the game's own, as the
// X360), 12 and 13 the bumper rows (glyphs L2 / R2: shift down / up), 14 Create (reset car), 15 and 16 right stick up /
// down (glyphs from the package: gas / brake), 17 Options (pause). 0 hides the slot. With a PlayStation pad the labels
// follow its bindings (padbind.c padbind_ps_label).
static const uint32_t ps_labels[17] = {
    0x5E5C5F2Eu, 0x63AA639Du, 0xB70FF4A4u, 0x13121940u, 0x018AE810u, 0x619B23B0u, 0x6AB80AFAu, 0xAB1032FAu, 0xFF0E7232u,
    0xB864880Du, 0xDC03C093u, 0x56AFE190u, 0xDC1CD97Du, 0x1CEDC132u, 0x619B23B0u, 0x6AB80AFAu, 0x6369F4B8u,
};
static uint32_t find_obj(uint32_t o, uint32_t id) {   // the package's object list (+0x4C), groups searched
    for (uint32_t r; o; o = M32(o + 4)) {
        if (M32(o + 0x10) == id) return o;
        if (M32(o + 0x18) == 5 && (r = find_obj(M32(o + 0x64), id))) return r;
    }
    return 0;
}
static void set_tex(uint32_t img, uint32_t hash) {     // as the X360: texture hash and "changed" flags
    if (img && M32(img + 0x18) == 1 && M32(img + 0x24) != hash) { M32(img + 0x24) = hash; M32(img + 0x1C) |= 0x2400000u; }
}
// What the X360's screen constructor does (LoadingController Function_822BA600: Function_822BA768 the callouts,
// Function_822BAC70 the diagram) on the package's objects, which are looked up in the object list because the package is
// not yet findable by name here. Callout k (CButton_k, BUTTON_k) gets the label of the controller configuration
// (options +0x88, X360 +0x90) from the table above, or is hidden when its label is 0.
static void loading_diagram(Cpu *c, uint32_t pkg) {
    const char *name = gptr(M32(pkg + 0xC));
    if (!name || strcasecmp(name + (strncasecmp(name, "WS_", 3) ? 0 : 3), "Loading_Controller.fng") || !loading_on()) return;
    static uint32_t slot, scr, loaded;
    if (!slot) { slot = galloc(16); scr = galloc(0x80); }     // scr: a stand-in screen, what the callback bridge reads
    uint32_t db = M32(0x0091CF90u), opts = M32(db + 0x10), xbox = (uint32_t)glyphs_xbox(), root = M32(pkg + 0x4C);
    uint32_t hash = xbox ? (M8(opts + 0x87) ? 0xEC3C0443u : 0xEC3C0444u) : (M8(opts + 0x87) ? 0xDF7B27A7u : 0xDF7B27A8u);
    uint32_t cfg = M32(opts + 0x88), esp = c->esp, ecx = c->ecx, edx = c->edx, a[5];
    if (cfg >= 5) cfg = 0;
    #define CALL(fn, n) call_guest(c, fn, n, a)
    set_tex(find_obj(root, 0x922A39C4u), hash);               // the diagram (X360 Function_822BAC70)
    for (uint32_t k = 1; k <= 17; ++k) {                      // the callouts (Function_822BA768)
        char nm[16];
        snprintf(nm, sizeof nm, "CButton_%u", k);
        uint32_t cb = find_obj(root, str_hash(nm));
        snprintf(nm, sizeof nm, "BUTTON_%u", k);
        uint32_t bt = find_obj(root, str_hash(nm)), lab = xbox ? callout_labels[5 * (k - 1) + cfg] : padbind_ps_label((int)k, ps_labels[k - 1]);
        if (xbox && k == 12) lab = 0;                         // the bumper callout "Look Back" (found by hiding each slot): LB is unbound here, Y looks back
        a[0] = cb; if (cb) CALL(lab ? 0x00514CC0u : 0x00514C70u, 1);
        a[0] = bt; if (bt) CALL(lab ? 0x00514CC0u : 0x00514C70u, 1);
        a[0] = cb; a[1] = lab; if (cb && lab) CALL(0x00515C00u, 2);
    }
    uint32_t img = find_obj(root, 0x4592229Cu), o;
    if (!M8(opts + 0x87)) { a[0] = img; a[1] = 0x0B30961Bu; if (img) CALL(0x00515BB0u, 2); } else set_tex(img, 0x148E38u);
    if ((o = find_obj(root, 0x673D77BCu))) { a[0] = o; CALL(0x00514C70u, 1); }
    if ((o = find_obj(root, 0x351AE442u))) { a[0] = o; CALL(0x00514C70u, 1); }
    if ((o = find_obj(root, 0x0F274B86u))) { a[0] = o; CALL(0x00514CC0u, 1); a[1] = 0x585CF905u; CALL(0x00515C00u, 2); if (!xbox) { a[0] = o; CALL(0x00514C70u, 1); } }   // "Xbox Guide": none on the PlayStation pad
    img = find_obj(root, 0x81B57400u);
    a[0] = img; a[1] = cfg == 1 || cfg == 6 || cfg == 3 || cfg == 8 ? 0x684u : 0x258D432u; if (img) CALL(0x00515BB0u, 2);
    set_tex(find_obj(root, 0x81B573FDu), 0x5BD);
    set_tex(find_obj(root, 0x81B573FEu), 0x683);
    set_tex(find_obj(root, 0x81B573FFu), xbox ? 0x1335F0u : 0x79AD657Fu);   // Back / Create (PlayStation SELECT art)
    set_tex(find_obj(root, 0x81B57402u), 0x3B7F86D);
    img = find_obj(root, 0x81B57401u);
    a[0] = img; a[1] = 0xFBD55D85u; if (img) CALL(0x00515BB0u, 2);
    #undef CALL
    if (diag_on && getenv("BOOT_DIAG_STRINGS"))                // the callout texts, logged by diag.c's SearchForString hook
        for (uint32_t i = 0; i < 17 * 5; ++i) if (callout_labels[i]) { c->edx = callout_labels[i]; call_guest(c, 0x0056BB80u, 0, NULL); }
    // The diagram is streamed in (DYNTEX.BIN); when it is there the stand-in screen plays the "loaded" script
    // (0056B160, as the X360's callback does), and the package's first script (0x16A259) starts now.
    strncpy((char *)G + scr + 0x40, name, 0x3F);
    M32(scr + 0x10) = scr + 0x40;
    a[0] = scr + 0x40; a[1] = 0x3248E720u; a[2] = 0x16A259u; a[3] = 1;
    call_guest(c, 0x00524920u, 4, a);                          // FEngSetScript
    if (loaded) { M32(slot) = loaded; a[0] = slot; a[1] = 1; call_guest(c, 0x00503360u, 2, a); }   // eUnloadStreamingTexture
    M32(slot) = hash;
    a[0] = slot; a[1] = 1; a[2] = 0x0056B160u; a[3] = scr; a[4] = 0;
    call_guest(c, 0x00508B10u, 5, a);                          // eLoadStreamingTexture
    loaded = hash;
    c->esp = esp; c->ecx = ecx; c->edx = edx;
    if (diag_on) diag_event("loading: controller diagram %08X (%s layout), controller config %u (options +0x88 = %u, +0x87 = %u)", hash, xbox ? "Xbox" : "PlayStation", cfg, M32(opts + 0x88), M8(opts + 0x87));
}

// ---------------- diagnostics ----------------
// BOOT_DIAG: prompts.txt lists every package's flagged objects as it loads (type 1 image, 2 string, 5 group; flags;
// an image's texture hash and whether the PC packs have it); events.log gets fepad: lines, the FE pad events
// (MapJoyEventToFEPad 008F3428: action, FEPad bit, name; the value cFEngJoyInput::HandleJoy 00559400 stores per port
// at +0xC), so a button press shows which FE action it is.
static FILE *inv;
static void inventory(Cpu *c, uint32_t o, int depth) {
    for (; o; o = M32(o + 4)) {
        uint32_t f = M32(o + 0x1C), type = M32(o + 0x18);
        if (f & 0x4C) {
            fprintf(inv, "%*s%u %08X flags %08X", depth * 2, "", type, M32(o + 0x10), f);
            if (type == 1) {
                uint32_t h = M32(o + 0x24), esp = c->esp, eax = c->eax, ecx = c->ecx, edx = c->edx, a[3] = {h, 0, 0};
                uint32_t ti = call_guest(c, FN_GET_TEXTURE_INFO, 3, a);
                c->esp = esp; c->eax = eax; c->ecx = ecx; c->edx = edx;
                fprintf(inv, " tex %08X %s", h, ti ? (const char *)G + ti + 0x0C : "(not in the packs)");
            }
            fputc('\n', inv);
        }
        if (type == 5) inventory(c, M32(o + 0x64), depth + 1);
    }
}
// (0x44: the X360 never draws those either, e.g. the online icons the PC packages carry in every screen)
static int has_console_set(uint32_t o) {
    for (; o; o = M32(o + 4))
        if ((M32(o + 0x1C) & 0x44) == 0x40 || (M32(o + 0x18) == 5 && has_console_set(M32(o + 0x64)))) return 1;
    return 0;
}
static uint32_t keycap_glyph(const char *name) {   // the console glyph a PC keycap stands for (the pairing above)
    static const struct { const char *key, *glyph; } pair[] = {{"PC_KEY_ESC", "TRIANGLE"}, {"PC_KEY_1", "CIRCLE"},
        {"PC_KEY_2", "SQUARE"}, {"PC_KEY_3", "SELECT"}, {"PC_KEY_4", "START"}};
    for (unsigned i = 0; i < sizeof pair / sizeof *pair; ++i)
        if (!strcasecmp(name, pair[i].key)) return str_hash(pair[i].glyph);
    return 0;
}
static void mark_keep(Cpu *c, uint32_t o, const char *pkgname) {   // a PC-only package: everything 0x8 but the keycaps stays
    for (; o; o = M32(o + 4)) {
        uint32_t f = M32(o + 0x1C), type = M32(o + 0x18);
        if (f & 0x8) {
            int keep = 1;
            uint32_t cap = 0;
            if (type == 1) {
                uint32_t esp = c->esp, eax = c->eax, ecx = c->ecx, edx = c->edx, a[3] = {M32(o + 0x24), 0, 0};
                uint32_t ti = call_guest(c, FN_GET_TEXTURE_INFO, 3, a);
                c->esp = esp; c->eax = eax; c->ecx = ecx; c->edx = edx;
                if (ti && !strncmp((const char *)G + ti + 0x0C, "PC_KEY", 6)) {
                    keep = 0;
                    cap = keycap_glyph((const char *)G + ti + 0x0C);
                }
            }
            keep_set(o, (uint32_t)keep, cap);
            if (diag_on) diag_event("prompts: %s %08X type %u %s", pkgname, M32(o + 0x10), type, keep ? "kept" : "keycap hidden");
        }
        if (type == 5) mark_keep(c, M32(o + 0x64), pkgname);
    }
}
static void clear_keep(uint32_t o) {
    for (; o; o = M32(o + 4)) {
        if (M32(o + 0x1C) & 0x8) keep_set(o, 0, 0);
        if (M32(o + 0x18) == 5) clear_keep(M32(o + 0x64));
    }
}
void f_00525520_orig(Cpu *c);
void f_00525520(Cpu *c) {                  // cFEngGameInterface::PackageWasLoaded (thiscall, FEPackage *)
    uint32_t pkg = arg(c, 0);
    f_00525520_orig(c);
    if (pkg && mode()) {
        uint32_t list = M32(pkg + 0x4C);
        if (has_console_set(list)) clear_keep(list);
        else mark_keep(c, list, gptr(M32(pkg + 0xC)) ? gptr(M32(pkg + 0xC)) : "?");
    }
    if (pkg) loading_diagram(c, pkg);
    if (!diag_on || !pkg) return;
    if (!inv) {
        char p[1100];
        snprintf(p, sizeof p, "%s/prompts.txt", diag_dir);
        inv = fopen(p, "w");
        if (!inv) return;
    }
    fprintf(inv, "package %s\n", gptr(M32(pkg + 0xC)) ? gptr(M32(pkg + 0xC)) : "?");
    inventory(c, M32(pkg + 0x4C), 1);
    fflush(inv);
}
// The FE input mapping (once, when the FE first draws with it): cFEngJoyInput::mInstance (0091CAD0) -> ActionQueue per
// port; ActionQueue +0x268 device index, +0x270 InputMapping (ActionQueue::Init 00648040, from the attribute
// collection at +0x274, class 2DEE1998). InputMapping +4: list sentinel; nodes {next, prev, three record values, game
// action, device scalar index, -1, -1} (InputMapping::InputMapping 006479D0). The device: IOModule (00920DE0) +8 +
// 4 * index; +0x18 its scalars, 16 bytes each. FE actions 0x19..0x2C: UP DOWN LEFT RIGHT RUP RDOWN RLEFT RRIGHT
// ACCEPT CANCEL CANCEL_ALT START BUTTON0..5 LTRIGGER RTRIGGER.
static void dump_mapping(void) {
    static uint32_t done[2];
    uint32_t fe = M32(0x0091CAD0u);
    if (!fe || !inv) return;
    static const char *fe_names[] = {"UP", "DOWN", "LEFT", "RIGHT", "RUP", "RDOWN", "RLEFT", "RRIGHT", "ACCEPT", "CANCEL",
                                     "CANCEL_ALT", "START", "BUTTON0", "BUTTON1", "BUTTON2", "BUTTON3", "BUTTON4", "BUTTON5",
                                     "LTRIGGER", "RTRIGGER"};
    const char *pads = getenv("NFS_PADS");
    for (uint32_t port = 0; port < (pads && !strcmp(pads, "2") ? 2u : 1u); ++port) {   // HandleJoy reads port 0 only; NFS_PADS=2 also lists port 1
        uint32_t q = M32(fe + 4 * port), map = q ? M32(q + 0x270) : 0;
        if (!map || done[port] == map) continue;
        done[port] = map;
        uint32_t devi = M32(q + 0x268), dev = devi < 8 ? M32(0x00920DE8u + 4 * devi) : 0, sc = dev ? M32(dev + 0x18) : 0;
        fprintf(inv, "FE input mapping, port %u: device %d (%08X, vtable %08X), %u entries\n", port, (int)devi, dev, dev ? M32(dev) : 0,
                M32(map + 8));
        uint32_t head = M32(map + 4), n = 0;
        for (uint32_t node = head ? M32(head) : 0; node && node != head && n < 400; node = M32(node), ++n) {
            uint32_t act = M32(node + 0x14), idx = M32(node + 0x18), e = sc ? sc + 16 * idx : 0;
            fprintf(inv, "  action %3u %-11s scalar %3u (%08X %08X %08X %08X) values %08X %08X %08X\n", act,
                    act >= 0x19 && act <= 0x2C ? fe_names[act - 0x19] : "", idx, e ? M32(e) : 0, e ? M32(e + 4) : 0,
                    e ? M32(e + 8) : 0, e ? M32(e + 12) : 0, M32(node + 8), M32(node + 12), M32(node + 16));
        }
        fflush(inv);
    }
}
void prompts_diag_frame(void) {            // diag.c, every frame
    dump_mapping();
    if (getenv("NFS_PADS")) {                  // both devices' ACCEPT (32) and START (39) scalars when they change
        static uint32_t lastsc[2][2][4];
        for (uint32_t d = 0; d < 2; ++d) {
            uint32_t dev = M32(0x00920DE8u + 4 * d), sc = dev ? M32(dev + 0x18) : 0;
            for (int k = 0; k < 2 && sc; ++k) {
                uint32_t e = sc + 16 * (k ? 39u : 32u), v[4] = {M32(e), M32(e + 4), M32(e + 8), M32(e + 12)};
                if (memcmp(v, lastsc[d][k], sizeof v)) {
                    memcpy(lastsc[d][k], v, sizeof v);
                    diag_event("fescalar: dev %u %s %08X %08X %08X %08X", d, k ? "START" : "ACCEPT", v[0], v[1], v[2], v[3]);
                }
            }
        }
    }
    static int32_t last[16], last1[16];
    for (int i = 0; i < 16; ++i) {
        uint32_t e = 0x008F3428u + 28u * (uint32_t)i;
        int32_t v1 = (int32_t)M32(e + 0x10);
        if (v1 != last1[i] && getenv("NFS_PADS")) { last1[i] = v1; diag_event("fepad1: %s %d (action %u)", gptr(M32(e + 8)) ? gptr(M32(e + 8)) : "?", v1, M32(e)); }
        int32_t v = (int32_t)M32(e + 0xC);
        if (v == last[i]) continue;
        last[i] = v;
        diag_event("fepad: %s %d (action %u)", gptr(M32(e + 8)) ? gptr(M32(e + 8)) : "?", v, M32(e));
    }
}
