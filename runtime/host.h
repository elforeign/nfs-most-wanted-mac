// host.h - shared helpers for the host side of the runtime (boot.c, user32.m, d3dx.c, ...).
#pragma once
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "runtime.h"

#define THUNK_BASE 0xF0000000u   // IAT slot i holds THUNK_BASE + 4*i
#define HOSTFN_BASE 0xF1000000u  // host functions callable from guest code (COM vtables, callbacks)

// Stack arguments of an import or host function: [esp] is the return address.
static inline uint32_t arg(Cpu *c, int i) { return M32(c->esp + 4 + 4 * i); }
static inline float argf(Cpu *c, int i) { uint32_t u = arg(c, i); float f; memcpy(&f, &u, 4); return f; }
static inline const char *gptr(uint32_t a) { return a ? (const char *)G + a : NULL; }
// Return from a __stdcall import/host function with nargs stack arguments.
#define RET(v, nargs) do { c->eax = (uint32_t)(v); c->esp += 4 + 4 * (nargs); return 1; } while (0)

uint32_t galloc(uint32_t n);            // zeroed guest memory (never freed)
uint32_t gstr(const char *s);
uint32_t gmalloc(uint32_t n);           // freeable guest memory (resources), 64-byte aligned, zeroed (heap.c)
uint32_t gmalloc_try(uint32_t n);       // optional resource preflight: returns 0 on arena exhaustion
void gfree(uint32_t a);
extern uint32_t last_error;
extern const char *game_dir;
extern int boot_trace;

void dispatch(Cpu *c, uint32_t target);
uint64_t host_ticks(void);              // monotonic nanoseconds
uint64_t host_filetime(void);           // wall clock as a FILETIME (100 ns since 1601), in step with host_ticks
// Calls a guest __stdcall function with n stack arguments (args[0] pushed last) and returns eax.
// The callee pops its arguments with `ret 4*n`; a __cdecl callee is handled by restoring esp afterwards.
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *args);

// Registers a host function and returns its guest address. fn follows the import convention: it reads
// arguments with arg(), sets eax and pops its arguments with RET(). `name` is for traces.
typedef int (*HostFn)(Cpu *c);
uint32_t host_fn(HostFn fn, const char *name);

// Modules. Each returns 1 if it handled the import `fn` (name without the DLL prefix).
int win32_user32(Cpu *c, const char *fn);   // user32.m (also GDI32 stubs)
int d3dx(Cpu *c, const char *fn);           // d3dx.c
int d3dx_effect(Cpu *c, const char *fn);    // d3dx_effect.c
int win32_threads(Cpu *c, const char *fn);  // threads.c
int win32_heap(Cpu *c, const char *fn);     // heap.c: Heap*, GlobalAlloc/GlobalFree
int win32_d3d9(Cpu *c, const char *fn);     // d3d9.m
int win32_dsound(Cpu *c, const char *fn);   // dsound.c
int win32_dinput(Cpu *c, const char *fn);   // dinput.c
int threads_close(uint32_t h);
void threads_init(void);
void gil_maybe_yield(Cpu *c);
void gil_yield(Cpu *c);
void gil_release(void);
void gil_acquire(void);
// diag.c
extern int diag_on;
extern const char *diag_dir;
void diag_init(void);
void diag_event(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void diag_event_ring(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void diag_idle(int idle);
void diag_call_ring(const char *name, int nargs, const uint32_t *args, uint32_t ret, uint32_t ra);   // a call, formatted lazily
void diag_report(const char *file, const char *why);
void diag_frame(uint64_t frame, uint64_t draws);
int diag_shot_due(uint64_t frame);
void diag_shot_request(uint64_t frame);  // a full-size shot of that frame (test switches)
int diag_frame_wanted(uint64_t frame, const char *list_env);
const char *diag_symbol(uint32_t addr, uint32_t *off);
const char *userdata_dir(void);
// app.m: the Mac app (logs, the game folder, NSApp and its menus)
extern int app_bundled;                 // running as NFSMW.app
const char *app_start(int argc, char **argv);   // the game folder
void app_cocoa_init(void);
void app_fatal(const char *msg);
const char *app_pref(const char *key);  // USERDATA/app.txt
void app_pref_set(const char *key, const char *value);
void app_pref_batch_begin(void);   // app_pref_set calls until the matching _end write app.txt once
void app_pref_batch_end(void);
const char *log_dir(void);              // ~/Library/Logs/NFSMW-port (logs, crash reports without BOOT_DIAG)
const char *log_name(void);             // this copy's log name there: NFSMW, or NFSMW-2... while another copy runs
int user32_request_close(void);         // WM_CLOSE to the game window; 0 if there's none yet
int user32_fullscreen(void);            // the game window is full screen
int user32_pad_layout_xbox(void);       // the controller layout (Controller menu)
int user32_pad_native(void);            // a PlayStation pad, bound natively (padbind.c)
void user32_set_pad_layout_xbox(int xbox);
void user32_toggle_fullscreen(void);
typedef struct { uint32_t tid; pthread_t th; uint64_t since; const char *volatile imp; volatile uint64_t imp_t0; volatile uint32_t ra; } GilRec;
GilRec *gil_rec_self(void);             // threads.c: BOOT_DIAG GIL-holder record of the calling guest thread (NULL on tid 1)
void gil_acquire(void);                 // threads.c: the guest lock, released around host waits
void gil_release(void);
void x360_shadow_camera(Cpu *c, uint32_t view, uint32_t proj);
void x360_shadow_texture_size(uint32_t *w, uint32_t *h);
void x360_reflection_size(uint32_t *w, uint32_t *h);
void mirror_projection(uint32_t view, uint32_t proj);   // mirror.c
void mirror_set_view(float hfov);       // mirror.c: horizontal field of view in degrees, 0 = the PC's squeezed view
void mirror_set_fog(int on);            // mirror.c: distance fog in the mirror (F10 "Mirror fog")
int render_msaa_samples(void);          // render_target.m: the back buffer's sample count (1 = AA off)
int render_target_msaa(uint32_t texture, uint32_t depth, int samples);   // render_target.m: a render-target texture and its depth drawn multisampled; 0 = not applied
int x360_reflection_target(uint32_t guest, uint32_t w, uint32_t h);
int x360_shadow_border(void);
void rumble_update(void);
void feedback_setup(void);
void feedback_levels(float *heavy, float *light);
enum { FEEDBACK_MENU = 1, FEEDBACK_PAUSE = 2, FEEDBACK_FOCUS = 4, FEEDBACK_APP = 8 };
void feedback_suppress(unsigned reason, int on); // independent holds; resume starts with an empty model
void feedback_poll_pause(void);         // main-loop pass / pad read: Sim::GetState() == 4
void rumble_suppress(int on);           // also gates queued HID reports; never opens the device
void rumble_stop(void);
void rumble_close(void);                // quitting: motors off for good
// padfx.m: DualSense adaptive triggers and the lightbar (NFS_PAD_TRIGGERS / NFS_PAD_LIGHT, default off)
int padfx_wanted(void);                 // either output is on, or BOOT_DIAG (the logic runs and logs what it would send)
void padfx_game_tick(Cpu *c, uint32_t local_player);   // feedback.c, each LocalPlayer::DoFFB: heat, pursuit, speedbreaker
void padfx_update(void *gc_controller, int dualsense, float lt, float rt);   // user32.m pad_read: NULL = simulated pad
void padfx_stop(void);                  // focus lost / quitting: triggers off, the lightbar's own colour back
void padfx_close(void);                 // quitting: the same, for good
void rumble_signals(void);              // rumble.m: SIGTERM / SIGINT stop the rumble and padfx before the process ends
void padfx_set_triggers(int on);        // the F10 menu
void padfx_set_light(int on);
void padfx_diag_text(char *buf, int n); // diag.c stats.log: this second's trigger modes and light colour
void feedback_trigger_state(float slip[2], float hz[2], int *nos);   // feedback.c: last DoFFB frame, [0] rear [1] front
void widescreen_setup(void);
float widescreen_fe_scale(void);
float movies_picture_aspect(void);
void widescreen_mode(int i, uint32_t *w, uint32_t *h);
void widescreen_projection(Cpu *c, uint32_t proj);
void widescreen_drop_offset(Cpu *c, uint32_t vec);
void widescreen_drop_quad(Cpu *c, uint32_t verts, uint32_t stride);
void user32_display_pixels(int *w, int *h);
// Time the game thread spends not running game code, per reason (host_ticks ns; stats.log turns it into the
// per-frame "busy" time, which is what limits the frame rate when the CPU is the bottleneck).
enum { IDLE_SLEEP, IDLE_GIL, IDLE_DRAWABLE, IDLE_GPU, IDLE_N };
extern uint64_t diag_idle_ns[IDLE_N];   // cumulative since start: each reader keeps its own previous values
// Always-on per-frame counters (diag.c, a few stores per frame, diag mode or not): stats.log and the performance
// overlay (overlay.m) both read them. perf_frames[n % PERF_RING] is frame record n; perf_nframes counts records.
// GPU times come from command buffer completion, by render serial, into perf_gpu_ms[serial % PERF_RING].
#define PERF_RING 1024
typedef struct {
    float frame_ms;              // since the previous Present
    float busy_ms;               // frame_ms minus the game thread's sleeps, GIL hand-overs and GPU/drawable waits
    float idle_ms[IDLE_N];
    uint32_t draws, ticks;       // draws, sim ticks run in this frame (Scheduler ticks: 0 repeats the picture, >1 catch-up)
} PerfFrame;
extern PerfFrame perf_frames[PERF_RING];
extern volatile uint64_t perf_nframes;
extern float perf_gpu_ms[PERF_RING];
extern volatile uint64_t perf_gpu_serial;   // the last serial whose GPU time is in perf_gpu_ms
void perf_gpu_done(uint64_t serial, double ms);
// Metal presentedTime counters, cumulative: each log reader owns its baseline.
// Buckets: 8.3 / 16.7 / 25 ms (+/-0.75 ms), other, dropped (presentedTime == 0).
typedef struct {
    uint64_t shown[5];
    double refresh_ms, min_ms, max_ms, granularity_ms, duration_ms;
    int limit, vrr_available, native_fullscreen, display_sync, vrr_requested, adaptive_scheduling;
} RenderPresentation;
void render_presentation_stats(RenderPresentation *out);   // game thread; counters from Metal callbacks
// overlay.m: the performance overlay (View > Performance Overlay, Cmd-Option-P; NFS_OVERLAY=off|compact|full)
enum { OVERLAY_OFF, OVERLAY_COMPACT, OVERLAY_FULL };
int overlay_level(void);
void overlay_set_level(int level);      // from the menu: applied at once, saved as `overlay` in app.txt
void overlay_frame(void);               // game thread, every Present: counters snapshot a few times a second
void feedback_last_levels(float *heavy, float *light);   // feedback.c: the motor levels last sent (no side effects)
int x360_shadow_map_size(void);         // x360.m: the shadow map's size when shadows are on
int user32_window_occluded(void);
double render_gpu_wall_ms(void);       // render_present.m: mean kernelStart..GPUEnd of the command buffers since the last call (perf.tsv gpu_wall)
void diag_png(const char *path, const void *bgra, uint32_t w, uint32_t h, uint32_t pitch, int keep_alpha);
void threads_set_main_cpu(Cpu *c);

// Screen-reach probe (probe.m, BOOT_DIAG_PROBE): the feature whose signature this pass draws, and what each reports
enum { PROBE_POSTFX = 1, PROBE_MSAA = 2, PROBE_HDTEX = 4, PROBE_SHADOWS = 8, PROBE_CARPAINT = 16, PROBE_MIRROR = 32, PROBE_RAINDROPS = 64, PROBE_ROADREFL = 128, PROBE_RIPPLES = 256, PROBE_GRASS = 512 };
extern int probe_sig;
int probe_mirror_ab(void);              // BOOT_DIAG_MIRROR_AB: 1 = mirror at exposure 1.4 this pass, 2 = untreated, else 0
void probe_applied(int bit, int n);     // the feature drew its signature (n times) in this pass
void probe_tint(uint32_t fmt, uint8_t *level, size_t size, int bgra32);   // hdtex: a level's colour made magenta
int probe_hold(void);                   // pacing.c
int probe_dump_frame(void);             // diag.c: BOOT_DIAG_DRAWS=probe dumps this frame (a probe base or signature pass)
extern int x360_hd_creating;            // x360_textures.m: the texture being created is an X360 HD swap

// X360 post-processing (render_post.m, driven by x360_post.m)
typedef struct {
    float coeffs[4][4];          // CURVE_COEFFS_0..3
    float blur[4];               // radial blur uv step per tap at the left/top and right/bottom edges (x0, y0, x1, y1)
    float blur_params[2];        // IS_MOTIONBLUR_VIGNETTED
    float dof_params[4];         // (2F, 3D, 1/(3D), 0)
    int dof;
    float desaturation, vignette, exposure, bloom_scale, middle_gray, adapted_lum, bloom_thresh;
    float znear, zfar;           // scene projection, to linearise depth
    int debug;                   // diag: 1 the chain's input, 2 the tone curve's input t, 3 magenta (does it land?), 4 the bloom mask
    int skip;                    // test (NFS_TEST_POST_SKIP): 1 vignette brightness/additive off, 2 curve driven by the pixel's own luminance, 4 speed blur in 7 taps
    int bloom_mask_pc;           // 2: no mask (the bright pass alone); 1: bloom source = colour x scene alpha (PC's shaders: alpha 1 = blooms); 0: x (1 - alpha), the X360's
    int curve_glow;              // NFS_CURVE_GLOW: 0 the curve's luminance footprint scaled to the screen (X360's 1.25 %), 1 fixed 16 px, 2 per pixel
    float bloom_self;            // NFS_BLOOM_SURFACES: 0 glow (bloom adds onto the bright area too), 1 halo only (its own lift subtracted)
    float lamp_glow;            // bounded supplemental lamp gain, 0 disables capture/composite
    float glare;                 // NFS_BLOOM_SURFACES=natural: energy-conserving glare strength s (scene + s (blur(B) - B)); 0 = glow/halo
    int rolloff;                 // NFS_HIGHLIGHTS=rolloff: the added light goes into the headroom above the pixel with a soft limit
    float deband;                // NFS_DEBAND: deband threshold (0..1 units; 0 = off) and +-1 LSB dither before the 8-bit write
} X360PostParams;
int render_x360_mirror(uint32_t texture, const X360PostParams *p, float exposure, int signature);
float render_x360_post(uint32_t src_texture, uint32_t dst_surface, uint32_t vignette_texture, const X360PostParams *p);
// Lens raindrops in the X360 order and look (x360_post.m drives it after the composite): one drop record of the game's
// Rain object (FUN_0073CFA0's view of it, with the variant the PC ignores)
typedef struct { float x, y, life, size; uint32_t variant; } RainDrop;
void render_x360_raindrops_warm(uint32_t dst_surface, uint32_t displace_texture, uint32_t alpha_texture);   // once per sample count, from the composite
int render_x360_raindrops(uint32_t dst_surface, const RainDrop *d, int n, uint32_t displace_texture, uint32_t alpha_texture, int signature);

// Game controller state (user32.m), for dinput.c's game controller. native: a PlayStation pad, given to the game as
// itself (buttons = the physical P_* mask, lt/rt the analog L2/R2); otherwise the emulated Xbox 360 controller (buttons
// after user32.m's layout).
typedef struct { int connected; float lx, ly, rx, ry, lt, rt; uint32_t buttons; int pov; int native; } PadState;
int pad_read(PadState *st);
enum { PAD_KIND_NONE, PAD_KIND_OTHER, PAD_KIND_DUALSENSE, PAD_KIND_DUALSHOCK4, PAD_KIND_EDGE };
int pad_identity(char *name, size_t n);    // user32.m: the pad's kind and its DirectInput name ("Controller (DualSense)")
void padbind_sync(Cpu *c);                  // padbind.c: the game's joystick follows the connected pad's type
void padbind_follow(Cpu *c);                // padbind.c: the same change check, called by controltype.c just before its binding swap
void padbind_swap(int on);                    // padbind.c: controltype.c has preset bindings in the table (padbind_sync leaves the pad alone)
enum { PADBIND_ROLE_GAS = 1, PADBIND_ROLE_BRAKE, PADBIND_ROLE_SHIFT, PADBIND_ROLE_OTHER, PADBIND_ROLE_NONE };
int padbind_trigger_role(int right);        // padbind.c: what L2 (0) / R2 (1) is bound to (a PlayStation pad's set)

// Selective police lamp source; mirror.c owns the single 00505380 hook.
int lampglow_enabled(void);
int postfx_look_variant(const char *v);
int lampglow_type(void);
void lampglow_begin(Cpu *c);
void lampglow_end(void);
void lampglow_draw(float rgb_sum);
void lampglow_replayed(int ok);
void lampglow_batch(int why, unsigned replays, unsigned refused);   // render.m lamp_flush: 0 pass end, 1 depth/stencil writer, 2 depth target, 3 bloom read
void lampglow_diag_second(void);
