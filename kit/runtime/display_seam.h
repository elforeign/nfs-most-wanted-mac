#pragma once
#include <stdint.h>
#ifndef __cplusplus
#include <stdbool.h>
#endif
#ifdef __cplusplus
extern "C" {
#endif
/* Internal, unpinned host/mod seam. No AppKit or frame pointers cross it. */
int32_t host_display_anchor(uint64_t id, int8_t h, int8_t v, int clear);
uint32_t host_display_elements(uint64_t *ids, uint32_t max);
float host_display_aspect(void);
/* The screen the window lives on, in pixels; 0 when unknown. */
int host_display_screen(int32_t *w, int32_t *h);
void host_display_set_screen(int32_t w, int32_t h);
/* Where the game draws its menus inside its frame, as fractions of the frame (x0, y0, w, h); the whole frame by
 * default. A game that draws a 4:3 menu centred in a wide frame (NFS Underground) sets it, so the pointer maps
 * into that box. */
void host_display_set_ui_box(float x0, float y0, float w, float h);
void host_display_ui_box(float box[4]);
uint64_t host_display_epoch(void);
int host_display_offer_mode(int w, int h, int bpp);
void host_display_request_window(int mode); /* queued; 0 windowed, 1 borderless, 2 fullscreen */
int host_display_take_window(void);         /* main thread drains; -1 when unchanged */
void mods_display_transition(uint64_t epoch, int screen_class);
int mods_display_classic(void);
int mods_display_scale(void);
int mods_display_wide(void);
void mods_display_scene_domain(int w, int h);
int mods_display_scene_width(int guest_w, int guest_h);
int mods_display_fps(void);      /* 0 original, otherwise requested new-frame limit */
int mods_display_overlay(void);  /* 0 off, 1 counters, 2 frame-time graph */
int mods_display_textures(void); /* 0 original, 1 HD pack */
/* What the renderer's texture pack holds, once opened: replacement textures
 * and the authored terrain detail. It words the Textures row. */
void mods_display_texture_pack(uint32_t replacements, int terrain_detail);
int mods_display_filtering(void); /* 0 original, 1 trilinear, 2 4x, 3 8x, 4 16x */
#ifdef __cplusplus
}
#endif

#ifdef __cplusplus
extern "C" {
#endif
// Synchronous owned-pixel snapshot: the host copies ARGB before returning.
void host_display_present_window(const uint32_t *argb, int w, int h);
// Seconds to the presenter's next refresh boundary, `intervals` refreshes
// on - when a Present with a sync interval returns. Zero on a headless
// presenter. The caller waits in the scheduler, never in the host.
double host_present_refresh_delay(int intervals);
// The presenter's clock (seconds) and paced period; 0 without a display presenter (3.0.2).
int host_present_slot_clock(double *now, double *period);
// The presenter's totals: new frames shown, frames dropped, refreshes that repeated a frame (0 without one).
uint64_t host_present_unique_completed(void);
uint64_t host_present_drops(void);
uint64_t host_present_repeats(void);
bool ddraw_gdi_primary_active(void);
#ifdef __cplusplus
}
#endif
#ifdef __cplusplus
extern "C" {
#endif
uint32_t ddraw_gdi_begin_primary(void);
void ddraw_gdi_end_primary(uint32_t dc);
#ifdef __cplusplus
}
#endif

#ifdef __cplusplus
extern "C" {
#endif
// An external software presenter owns one display layer above the window
// canvases. The runtime copies pixels synchronously and keeps the last frame
// across GDI refreshes; owner identifies which presenter may retire it.
void gdi_present_surface(uint32_t owner, uint32_t hwnd, const uint32_t *argb, int w, int h,
                         bool fullscreen);
void gdi_forget_surface(uint32_t owner);
#ifdef __cplusplus
}
#endif

#ifdef __cplusplus
extern "C" {
#endif
// The hardware side of the Direct3D 11 2D path (host/gpu2d.cpp). The shim sends
// textures as RGBA8, clears, and axis-aligned textured rectangles; the host
// keeps a GPU copy of each texture and render target, keyed by the shim's
// object id, and draws on the presenter's device. Every call runs under the
// guest baton. Without a host GPU, available() is 0 and nothing else is called.
int host_gpu2d_available(void);
// Advances whenever the host drops every copy (a reset, a new device): a copy
// made under an older generation is gone.
uint32_t host_gpu2d_generation(void);
// Replace the (x, y, rw, rh) region of texture `id`, a w x h RGBA8 image,
// with `rgba` (rw * rh pixels, tightly packed). Creates or resizes it first.
void host_gpu2d_texture(uint32_t id, int w, int h, const uint8_t *rgba, int x, int y, int rw,
                        int rh);
void host_gpu2d_forget(uint32_t id);
void host_gpu2d_reset(void);
// Fill render target `id` (w x h) with an RGBA colour.
void host_gpu2d_clear(uint32_t id, int w, int h, const float rgba[4]);
// Blend factors are gpu.h's, in order: Zero, One, SrcAlpha, OneMinusSrcAlpha,
// DstAlpha, OneMinusDstAlpha, SrcColor, OneMinusSrcColor, DstColor,
// OneMinusDstColor.
struct HostGpu2DQuad {
    double x, y, w, h;   // destination, render-target pixels, y down
    double u, v, uw, uh; // source, normalised, v down
    int blend;           // 0: replace colour and alpha
    int src_rgb, dst_rgb, src_alpha, dst_alpha;
};
// Draw `texture` into render target `target` (w x h). False, having drawn
// nothing, when the texture does not exist or the draw cannot be encoded.
int host_gpu2d_draw(uint32_t target, int w, int h, uint32_t texture,
                    const struct HostGpu2DQuad *quad);
// Waits for the target's pending work and copies it out, w x h RGBA8.
int host_gpu2d_readback(uint32_t id, int w, int h, uint8_t *rgba);
// Publish render target `id` as the window frame, as host_display_present_window
// publishes a pixel snapshot. A host that presents on the GPU stages it there;
// one that builds frames from CPU pixels reads it back with
// host_gpu2d_present_readback, which hands them to host_display_present_window.
void host_display_present_gpu2d(uint32_t id, int w, int h);
void host_gpu2d_present_readback(uint32_t id, int w, int h);

// A software presenter whose last frame is on the GPU: GDI keeps its place on
// the screen and calls `fetch` for ARGB pixels only when it has to compose
// over them. `fetch` fills w x h ARGB and returns false when it cannot.
void gdi_present_external(uint32_t owner, uint32_t hwnd, int w, int h, bool fullscreen,
                          bool (*fetch)(uint32_t owner, uint32_t *argb, int w, int h));
// Whether a w x h present into `hwnd` would be the whole screen, with nothing
// composed around or over it.
bool gdi_surface_covers_screen(uint32_t owner, uint32_t hwnd, int w, int h, bool fullscreen);
#ifdef __cplusplus
}
#endif
