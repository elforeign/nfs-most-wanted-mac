#pragma once
/* SI units; RGB inputs are linear channel duties, not display-encoded colours. */
typedef struct { double x, y, z; } UGVec3;
typedef struct { double flux, x, y; } UGPrimary;
extern const UGPrimary ug_primaries[3];
/* Presets are RGB bit masks; 0 is Off. No UI policy is implied. */
int ug_preset_rgb(unsigned preset, double rgb[3]);
/* Controller duties for a preset's named colour: single channels run at full duty; a
 * two-channel mix balances its two Rec.709 channels (yellow, cyan, magenta hues) and
 * all three solve for equal Rec.709 channels (D65 white). The largest duty is 1. */
int ug_preset_duties(unsigned preset, double duties[3]);
int ug_mix_xyz(const double rgb[3], double xyz[3]);
/* Nonnegative, unbounded linear Rec.709; clipping followed by Y restoration.
 * relative_y_error reports the signed final Y error, before exposure/tone mapping. */
int ug_xyz_rec709(const double xyz[3], double rgb[3], double *relative_y_error);
int ug_srgb_to_linear(double encoded, double *linear);
/* Uniform Lambertian segment, flux in lm/m; unit emitter/receiver normals.
 * Returns 0 on invalid/degenerate geometry; output is cleared on failure. */
int ug_segment_lux(double flux, UGVec3 a, UGVec3 b, UGVec3 emission_normal,
                   UGVec3 receiver, UGVec3 receiver_normal, double *lux);
