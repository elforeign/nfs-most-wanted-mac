#pragma once
#include "host.h"

// Authored optics: 1.25 mm maximum footprint, 25 mm pupil distance, 60 degree
// vertical field of view, 7/8 occupied diameter. Radius names the quad diameter:
// (1.25 / 25) / (2 tan 30 deg) / (7/8).
#define LENS_FOOTPRINT_MM 1.25
#define LENS_DROP_RADIUS 0.049487166f
// Hold opacity for 3/4 of the PC scheduler's lifetime, fade over the last 1/4.
#define LENS_DROP_LIFE 4.0f
// Reference spherical cap: 0.5 mm diameter and 55 degree contact angle.
// R = (0.5/2)/sin(55 deg) = 0.305194 mm. Water-to-air Snell/Fresnel optics.
#define LENS_WATER_INDEX 1.333
#define LENS_CONTACT_DEGREES 55.0
#define LENS_PI 3.14159265358979323846
// Two transparent texels on each side: footprint fraction 7/8.
#define LENS_CAP_RADIUS (14.0 / 32.0)
// Ray deflection in quad coordinates: 25 mm / (0.5 mm / (7/8)) = 43.75 for the
// 0.5 mm reference cap (defocus off); the defocused fields are in units of the
// largest footprint's quad: 25 mm / (1.25 mm / (7/8)) = 17.5.
#define LENS_RAY_SCALE 43.75
#define LENS_FOOTPRINT_RAY_SCALE (LENS_SURFACE_MM * 2 * LENS_CAP_RADIUS / LENS_FOOTPRINT_MM)
// Transmission is relative to the dry window (air/glass), which the frame already
// shows: a wet spot replaces it with air/water and water/glass, no darker at
// normal incidence. Crown glass.
#define LENS_GLASS_INDEX 1.5
// Thin-lens reference: f=4 mm (60 degree vertical field on a 4.6 mm tall,
// 1/2.3-inch class sensor), f/16, focus S=10 m, front surface z=25 mm ahead of
// the entrance pupil (z is NOT zero). PBRT 4e, Cameras, section 5.2.3:
// https://pbr-book.org/4ed/Cameras_and_Film/Projective_Camera_Models
// The cone of one in-focus pixel crosses the wet surface in a disc of
// diameter (f/N)*(1-z/S) = 0.249375 mm, a fifth of the largest footprint.
// A cone wider than the drop (f=20 mm gave 1.247 mm) only sees an averaged
// sliver of it: the drop's image vanishes into the frame.
// The footprint edge stays sharp (a signed distance drawn at pixel scale): the
// target look is the console's crisp rim, not a defocused edge. The scene seen
// through the drop is integrated per pupil ray in the fragment pass, never
// by averaging the ray field (its signed offsets cancel).
#ifndef LENS_DEFOCUS
#define LENS_DEFOCUS 1
#endif
#define LENS_FOCAL_MM 4.0
#define LENS_F_NUMBER 16.0
#define LENS_FOCUS_MM 10000.0
#define LENS_SURFACE_MM 25.0
#define LENS_COC_MM ((LENS_FOCAL_MM / LENS_F_NUMBER) * (1 - LENS_SURFACE_MM / LENS_FOCUS_MM))
// Pupil radius in quad units, and the quad margin for the blurred footprint:
// (7/16 + 0.087 + 1/32) / (7/16) = 1.27.
#define LENS_PUPIL_QUAD (LENS_CAP_RADIUS * LENS_COC_MM / LENS_FOOTPRINT_MM)
#define LENS_DEFOCUS_EXPAND 1.4f
// Equal-probability log-spaced footprint diameters, a sixth-octave apart:
// .884, .992, 1.114, 1.25 mm (area ratio 2^(1/3)). The PC scheduler's size
// multiplies the whole footprint, including its PSF.
#define LENS_DIAMETER_RATIO 0.8908987181403393
#define LENS_DROP_EDGE 32
#define LENS_DROP_VARIANTS 4
typedef struct {
    uint16_t displacement[128 * 32 * 4]; // RGBA16Float, per variant: signed ray coordinates in GB
    uint8_t alpha[128 * 32 * 4];        // RGBA8: edge distance (defocus; else coverage), transmission, sharp coverage, 1
} LensDropPixels;
void lens_drops_generate(LensDropPixels *pixels);
int lens_drops_upload(Cpu *c, uint32_t device, uint32_t textures[2]);
// NFS_RAINDROPS spelling to mode: 0 pc_before, 1 pc, 2 x360 (unset and unknown too).
int lens_drops_mode(const char *env);
// A complete console pair always wins; never mix providers. Returns 0/1/2:
// unavailable / console / authored. Pass lens_drops_mode(env)==2 as authored_enabled: authored
// drops are the default without console textures.
int lens_drops_provider(Cpu *c, uint32_t device, uint32_t textures[2], int authored_enabled);

// Provider-aware entry; the legacy renderer entry points retain console semantics.
int render_lens_drops_view(uint32_t dst, const RainDrop *d, int n, uint32_t displacement,
                           uint32_t alpha, int signature, int authored, const X360PostView *view);
