#include "underglow_photometry.h"
#include <math.h>
#include <stddef.h>

const UGPrimary ug_primaries[3] = {
    {51.857, .694641, .304843},
    {106.075, .159396, .722814},
    {29.469, .133762, .060996}
};
static int valid3(const double *v) {
    return v && isfinite(v[0]) && isfinite(v[1]) && isfinite(v[2]);
}
int ug_preset_rgb(unsigned preset, double rgb[3]) {
    if (!rgb) return 0;
    rgb[0] = rgb[1] = rgb[2] = 0;
    if (preset > 7) return 0;
    for (unsigned i = 0; i < 3; ++i) rgb[i] = (preset >> i) & 1;
    return 1;
}
int ug_mix_xyz(const double rgb[3], double xyz[3]) {
    if (!xyz) return 0;
    double out[3] = {0};
    if (!valid3(rgb)) goto bad;
    for (unsigned i = 0; i < 3; ++i) {
        if (rgb[i] < 0 || rgb[i] > 1) goto bad;
        double y = rgb[i] * ug_primaries[i].flux;
        out[0] += y * ug_primaries[i].x / ug_primaries[i].y;
        out[1] += y;
        out[2] += y * (1 - ug_primaries[i].x - ug_primaries[i].y) / ug_primaries[i].y;
    }
    for (unsigned i = 0; i < 3; ++i) xyz[i] = out[i];
    return 1;
bad:
    xyz[0] = xyz[1] = xyz[2] = 0;
    return 0;
}
int ug_xyz_rec709(const double xyz[3], double rgb[3], double *error) {
    if (!rgb || !error) return 0;
    double x = 0, y = 0, z = 0;
    int valid = valid3(xyz);
    if (valid) { x = xyz[0]; y = xyz[1]; z = xyz[2]; }
    rgb[0] = rgb[1] = rgb[2] = 0; *error = 0;
    if (!valid || x < 0 || y < 0 || z < 0) return 0;
    if (y == 0) return x == 0 && z == 0;
    double r = fmax(0, 3.2404542*x - 1.5371385*y - .4985314*z);
    double g = fmax(0, -.9692660*x + 1.8760108*y + .0415560*z);
    double b = fmax(0, .0556434*x - .2040259*y + 1.0572252*z);
    double yy = .2126729*r + .7151522*g + .0721750*b;
    if (!isfinite(yy) || yy <= 0) return 0;
    rgb[0] = r * (y/yy); rgb[1] = g * (y/yy); rgb[2] = b * (y/yy);
    *error = (.2126729*rgb[0] + .7151522*rgb[1] + .0721750*rgb[2] - y)/y;
    return valid3(rgb);
}
int ug_srgb_to_linear(double encoded, double *linear) {
    if (!linear) return 0;
    *linear = 0;
    if (!isfinite(encoded) || encoded < 0 || encoded > 1) return 0;
    *linear = encoded <= .04045 ? encoded/12.92 : pow((encoded+.055)/1.055, 2.4);
    return 1;
}
static UGVec3 sub(UGVec3 a, UGVec3 b) { return (UGVec3){a.x-b.x,a.y-b.y,a.z-b.z}; }
static UGVec3 mul(UGVec3 a, double k) { return (UGVec3){a.x*k,a.y*k,a.z*k}; }
static double dot(UGVec3 a, UGVec3 b) { return a.x*b.x+a.y*b.y+a.z*b.z; }
static int finite_vec(UGVec3 a) { return isfinite(a.x)&&isfinite(a.y)&&isfinite(a.z); }
static int clip(double a, double b, double *lo, double *hi) {
    if (b == 0) return a > 0;
    double zero = -a/b;
    if (b > 0) *lo = fmax(*lo,zero); else *hi = fmin(*hi,zero);
    return *hi > *lo;
}
static double primitive(double t, double q2, double ac, double adbc, double bd) {
    double q = sqrt(q2), angle = atan(t/q), den = q2+t*t;
    double i0 = t/(2*q2*den) + angle/(2*q2*q);
    double i1 = -1/(2*den);
    double i2 = angle/(2*q) - t/(2*den);
    return ac*i0 + adbc*i1 + bd*i2;
}
int ug_segment_lux(double flux, UGVec3 a, UGVec3 b, UGVec3 en,
                   UGVec3 receiver, UGVec3 rn, double *lux) {
    if (!lux) return 0;
    *lux = 0;
    if (!isfinite(flux) || flux < 0 || !finite_vec(a) || !finite_vec(b) ||
        !finite_vec(en) || !finite_vec(receiver) || !finite_vec(rn) ||
        fabs(dot(en,en)-1) > 1e-8 || fabs(dot(rn,rn)-1) > 1e-8) return 0;
    UGVec3 u = sub(b,a), v = sub(a,receiver);
    double length = sqrt(dot(u,u));
    if (!isfinite(length) || length <= 0) return 0;
    u = mul(u,1/length);
    double lo = dot(v,u), hi = lo+length;
    UGVec3 perp = sub(v,mul(u,lo));
    double q2 = dot(perp,perp);
    if (!isfinite(q2) || q2 <= 1e-20 || !isfinite(hi)) return 0;
    double aa = -dot(en,perp), bb = -dot(en,u), cc = dot(rn,perp), dd = dot(rn,u);
    if (flux == 0 || !clip(aa,bb,&lo,&hi) || !clip(cc,dd,&lo,&hi)) return 1;
    double result = flux/acos(-1) * (primitive(hi,q2,aa*cc,aa*dd+bb*cc,bb*dd) -
                                    primitive(lo,q2,aa*cc,aa*dd+bb*cc,bb*dd));
    if (!isfinite(result)) return 0;
    *lux = fmax(0,result);
    return 1;
}
