"""Pinned production density/near-fit regressions; numerical fixture, no captures.
Good: 81 pair + 216 far parity + 7,210 API checks, zero failures.
Original: 80 checks, five behavioral failures (yaw still passes).
Six production mutants / 7,210 checks each: unsnapped 26, wrong W 3,468,
omitted upstream 30, doubled centre 12, shared Z 3,468, wrong order 3,468.
The pinned source's three address-range dashes use escapes to distinguish
instruction ranges from date stamps; decoded source hashes remain exact.
"""
from pathlib import Path
import ctypes
import hashlib
import math
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR_SHA = "ff0c359f6414ff53ad7b48e361101361b5b8a763"
PREDECESSOR_SOURCE_SHA256 = '79f7bce63a75de9f6995ef20e76995f27029afef8ec2267d43f6a717e8bc8cb8'
PREDECESSOR_CONSTANTS_SHA256 = 'fee55d8936d60fcc674788ce71c81577944ae7692070732d0a0964967b6c04b8'
PREDECESSOR_SOURCE = '''
// x360_shadow_fit.cpp - the X360 shadow-map light camera (LiSPSM), plan milestone M2, for x360.m.
//
// A reimplementation of the retail X360 ShadowMap_FitLiSPSM @ 0x82456030, checked against it, with one addition: the focus frustum can take its x/y scales from the
// PC player's projection, which widescreen.c makes Hor+. x360_shadow_fit() is the C entry point.
#include <cstdint>
#include <cmath>

#include "gen/x360_engine.inc"   // X360E_SHADOW_*: the retail fit's tuning numbers, from the player's default.xex

namespace nfsmw_x360 {

struct Vec3 {
    float x, y, z;
};

struct Mat4 {
    float m[4][4];
};

struct ShadowFitInput {
    Mat4 playerView;           // player eView view matrix (world -> view), platform struct +0x00
    uint16_t cameraFov;        // player Camera::FieldOfView (bAngle units: 65536 = 360 deg), +0xC4
    int renderTargetWidth;     // player view render target width (default 640)
    int renderTargetHeight;    // player view render target height (default 480)
    Vec3 lightDirection;       // world light direction, render context +0x120 (need not be unit)
    float focusNear = X360E_SHADOW_NEAR;   // 0x828FCC70
    float focusFar = X360E_SHADOW_FOCUS_RACE;    // 0x828FCC74 (the front end uses X360E_SHADOW_FOCUS_FE)
    float centreOffsetX = 0.0f;  // 0x82C5E724, never written in retail (always 0)
    float centreOffsetY = 0.0f;  // 0x82C5E728, never written in retail (always 0)
    // PC port: the focus frustum's x/y scales from the player's own projection (m00, -m11), so a wider (Hor+) view
    // is covered; 0 = from cameraFov and the render target as on X360.
    float focusScaleX = 0.0f, focusScaleY = 0.0f;
};

struct ShadowFitResult {
    Mat4 view;      // identity (the game writes identity into the shadow view)
    Mat4 proj;      // playerView * basis * T * P * S * O
    Mat4 viewProj;  // == proj; this is what feeds SHADOWMAP_WVPMATRIX
    // Intermediate values, handy for debugging/visualising.
    Vec3 lightDirClamped;  // world light dir after the elevation clamp
    float sinGamma;        // sin of the angle between view direction and light
    float n;               // LiSPSM near distance (scaled as in the game)
    float maxTanX, maxTanY, maxDepth;
};

ShadowFitResult FitShadowCamera(const ShadowFitInput &in);

}  // namespace nfsmw_x360

namespace nfsmw_x360 {
namespace {

// --- math helpers matching the game's routines ---------------------------------------------

Mat4 Identity() {
    Mat4 r{};
    for (int i = 0; i < 4; ++i) r.m[i][i] = 1.0f;
    return r;
}

// bMatrix_Multiply @ 0x8263CE30: out = a * b.
Mat4 Multiply(const Mat4 &a, const Mat4 &b) {
    Mat4 r{};
    for (int i = 0; i < 4; ++i)
        for (int j = 0; j < 4; ++j)
            for (int k = 0; k < 4; ++k) r.m[i][j] += a.m[i][k] * b.m[k][j];
    return r;
}

// bMatrix_Translation @ 0x8263D658.
Mat4 Translation(float x, float y, float z) {
    Mat4 r = Identity();
    r.m[3][0] = x;
    r.m[3][1] = y;
    r.m[3][2] = z;
    return r;
}

// bMatrix_PerspectiveLH @ 0x8263E880 (D3DXMatrixPerspectiveLH).
Mat4 PerspectiveLH(float w, float h, float zn, float zf) {
    Mat4 r{};
    const float q = zf / (zf - zn);
    r.m[0][0] = 2.0f * zn / w;
    r.m[1][1] = 2.0f * zn / h;
    r.m[2][2] = q;
    r.m[2][3] = 1.0f;
    r.m[3][2] = -q * zn;
    return r;
}

// bMatrix_OrthoLH @ 0x8263F168 (D3DXMatrixOrthoLH).
Mat4 OrthoLH(float w, float h, float zn, float zf) {
    Mat4 r{};
    r.m[0][0] = 2.0f / w;
    r.m[1][1] = 2.0f / h;
    r.m[2][2] = 1.0f / (zf - zn);
    r.m[3][2] = -zn / (zf - zn);
    r.m[3][3] = 1.0f;
    return r;
}

float Dot(const Vec3 &a, const Vec3 &b) { return a.x * b.x + a.y * b.y + a.z * b.z; }

Vec3 Cross(const Vec3 &a, const Vec3 &b) {  // a.yzx * b.zxy - a.zxy * b.yzx (vpermwi 0x63/0x87)
    return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x};
}

Vec3 Normalize(const Vec3 &v) {
    const float len = std::sqrt(Dot(v, v));
    return {v.x / len, v.y / len, v.z / len};
}

// TransformNormal @ 0x826394B0: v.x*row0 + v.y*row1 + v.z*row2.
Vec3 TransformNormal(const Vec3 &v, const Mat4 &m) {
    return {v.x * m.m[0][0] + v.y * m.m[1][0] + v.z * m.m[2][0],
            v.x * m.m[0][1] + v.y * m.m[1][1] + v.z * m.m[2][1],
            v.x * m.m[0][2] + v.y * m.m[1][2] + v.z * m.m[2][2]};
}

// TransformCoord (stream version @ 0x826393F8), w-divided.
Vec3 TransformCoord(const Vec3 &v, const Mat4 &m) {
    float o[4];
    for (int j = 0; j < 4; ++j) o[j] = v.x * m.m[0][j] + v.y * m.m[1][j] + v.z * m.m[2][j] + m.m[3][j];
    return {o[0] / o[3], o[1] / o[3], o[2] / o[3]};
}

// bAngle trig (FUN_821568F8 is the game's sine; cos = sin(a + 0x4000)).
float BAngleSin(uint16_t a) { return std::sin(a * (6.28318530717958647692f / 65536.0f)); }
float BAngleTan(uint16_t a) { return BAngleSin(a) / BAngleSin(static_cast<uint16_t>(a + 0x4000)); }

struct Plane {
    float a, b, c, d;
};

// Frustum_ExtractPlanes @ 0x82456BF8: six planes from columns (Gribb-Hartmann), normalised, in
// the order [w-x, w+x, w-y, w+y, w-z, w+z]. Note the near plane is w+z (GL style) although the
// projection is D3D style, so the "near" corners sit at z = Q*zn/(1+Q), not zn. Kept as-is.
void ExtractPlanes(const Mat4 &m, Plane out[6]) {
    auto col = [&](int c, int r) { return m.m[r][c]; };
    for (int k = 0; k < 3; ++k) {
        Plane minus{col(3, 0) - col(k, 0), col(3, 1) - col(k, 1), col(3, 2) - col(k, 2), col(3, 3) - col(k, 3)};
        Plane plus{col(3, 0) + col(k, 0), col(3, 1) + col(k, 1), col(3, 2) + col(k, 2), col(3, 3) + col(k, 3)};
        out[2 * k] = minus;
        out[2 * k + 1] = plus;
    }
    for (int i = 0; i < 6; ++i) {
        Plane &p = out[i];
        const float len = std::sqrt(p.a * p.a + p.b * p.b + p.c * p.c);
        p = {p.a / len, p.b / len, p.c / len, p.d / len};
    }
}

// Three-plane intersection (Function_82456940).
Vec3 Intersect(const Plane &p1, const Plane &p2, const Plane &p3) {
    const Vec3 n1{p1.a, p1.b, p1.c}, n2{p2.a, p2.b, p2.c}, n3{p3.a, p3.b, p3.c};
    const Vec3 c23 = Cross(n2, n3), c31 = Cross(n3, n1), c12 = Cross(n1, n2);
    const float denom = Dot(n1, c23);
    return {-(p1.d * c23.x + p2.d * c31.x + p3.d * c12.x) / denom,
            -(p1.d * c23.y + p2.d * c31.y + p3.d * c12.y) / denom,
            -(p1.d * c23.z + p2.d * c31.z + p3.d * c12.z) / denom};
}

}  // namespace

ShadowFitResult FitShadowCamera(const ShadowFitInput &in) {
    ShadowFitResult r{};
    const float zn = in.focusNear, zf = in.focusFar;

    // 82456128-82456278: focus projection = player FOV/aspect, z range [zn, zf], Y flipped
    // (same flip as eView_UpdateMatrices). The FOV halves are truncated to bAngle (fctidz).
    const float fov = static_cast<float>(in.cameraFov);
    const float tanX = BAngleTan(static_cast<uint16_t>(static_cast<int64_t>(fov * 0.5f)));
    const float aspect = static_cast<float>(in.renderTargetHeight) / static_cast<float>(in.renderTargetWidth);
    const float tanY = BAngleTan(static_cast<uint16_t>(static_cast<int64_t>(aspect * fov * 0.5f)));
    Mat4 focus{};
    const float q = zf / (zf - zn);
    focus.m[0][0] = in.focusScaleX > 0.0f ? in.focusScaleX : 1.0f / tanX;
    focus.m[1][1] = -(in.focusScaleY > 0.0f ? in.focusScaleY : 1.0f / tanY);
    focus.m[2][2] = q;
    focus.m[2][3] = 1.0f;
    focus.m[3][2] = -(zn * q);

    // 8245627C-824562A0: eight corners of that frustum, in player view space.
    Plane planes[6];
    ExtractPlanes(focus, planes);
    Vec3 corners[8];
    for (int i = 0; i < 8; ++i)
        corners[i] = Intersect(planes[(i & 1) ? 4 : 5], planes[(i & 2) ? 3 : 2], planes[(i & 4) ? 0 : 1]);

    // 824562A4-8245632C: world light direction, normalised; elevation clamped to z >= 0.7.
    Vec3 L = in.lightDirection;
    const float len = std::sqrt(Dot(L, L));
    if (len == 0.0f) {
        L = {1.0f, 0.0f, 0.0f};  // 8245681C
    } else {
        L = {L.x / len, L.y / len, L.z / len};
    }
    if (!(L.z >= X360E_SHADOW_LIGHT_Z_MIN)) {  // 82456300: low sun -> raise the light to a minimum z and renormalise
        L.z = X360E_SHADOW_LIGHT_Z_MIN;
        L = Normalize(L);
    }
    r.lightDirClamped = L;

    // 82456330\x2d824563BC: gamma between the camera forward (view matrix column 2) and the light,
    // then LiSPSM's optimal n, scaled and offset (X360E_SHADOW_N_*).
    const Vec3 forward{in.playerView.m[0][2], in.playerView.m[1][2], in.playerView.m[2][2]};
    const float cosGamma = Dot(forward, L);
    const float sinGamma = std::sqrt(1.0f - cosGamma * cosGamma);
    const float nOpt = (2.0f * (std::sqrt(zf * zn) + zn)) / (sinGamma * 2.0f);
    const float n = ((nOpt + X360E_SHADOW_N_OFFSET) - X360E_SHADOW_N_OFFSET) * X360E_SHADOW_N_SCALE + X360E_SHADOW_N_OFFSET;
    r.sinGamma = sinGamma;
    r.n = n;

    // 824563C0-82456564: light-space basis in view space. up = (0,0,-1) is the view axis.
    const Vec3 Lv = Normalize(TransformNormal(L, in.playerView));
    const Vec3 up{0.0f, 0.0f, -1.0f};
    const Vec3 X = Normalize(Cross(Lv, up));
    const Vec3 Y = Normalize(Cross(Lv, X));
    Mat4 basis{};  // p * basis = (p.X, p.Lv, p.Y)
    basis.m[0][0] = X.x; basis.m[0][1] = Lv.x; basis.m[0][2] = Y.x;
    basis.m[1][0] = X.y; basis.m[1][1] = Lv.y; basis.m[1][2] = Y.y;
    basis.m[2][0] = X.z; basis.m[2][1] = Lv.z; basis.m[2][2] = Y.z;
    basis.m[3][3] = 1.0f;

    // 82456568-8245657C: corners into light space and their AABB.
    Vec3 mn{1e33f, 1e33f, 1e33f}, mx{-1e33f, -1e33f, -1e33f};
    for (Vec3 &c : corners) {
        c = TransformCoord(c, basis);
        mn = {std::fmin(mn.x, c.x), std::fmin(mn.y, c.y), std::fmin(mn.z, c.z)};
        mx = {std::fmax(mx.x, c.x), std::fmax(mx.y, c.y), std::fmax(mx.z, c.z)};
    }

    // 82456580-82456654: projection centre and the frustum extents seen from it.
    const float cx = (mn.x + mx.x) * 0.5f + in.centreOffsetX;
    const float cy = (mn.y + mx.y) * 0.5f + in.centreOffsetY;
    const float eyeZ = mn.z - n;
    float maxX = 0.0f, maxY = 0.0f, maxD = 0.0f;
    for (const Vec3 &c : corners) {
        const float d = c.z - eyeZ;
        if (d > 0.0f) {
            const float inv = 1.0f / d;
            maxX = std::fmax(maxX, std::fabs((c.x - cx) * inv));
            maxY = std::fmax(maxY, std::fabs((c.y - cy) * inv));
            maxD = std::fmax(maxD, d);
        }
    }
    r.maxTanX = maxX;
    r.maxTanY = maxY;
    r.maxDepth = maxD;

    // 82456658\x2d824566BC: move the eye further back, then the LiSPSM perspective.
    // Width covers the x extent at the near plane; height is scaled up (y is the
    // light-direction axis, later mapped to depth).
    const Mat4 T = Translation(-cx, -cy, -(eyeZ - X360E_SHADOW_EYE_BACK));
    const Mat4 P = PerspectiveLH(maxX * n * 2.0f, maxY * n * X360E_SHADOW_Y_SCALE, n, maxD * X360E_SHADOW_DEPTH_SCALE);
    Mat4 m = Multiply(Multiply(basis, T), P);

    // 824566C0-82456740: swap y/z (y' = z - 0.5w, z' = -y + 1.5w) and a fixed ortho.
    Mat4 S{};
    S.m[0][0] = 1.0f;
    S.m[1][2] = -1.0f;
    S.m[2][1] = 1.0f;
    S.m[3][1] = -0.5f;  // 0x820AFC74
    S.m[3][2] = 1.5f;   // 0x82060E74
    S.m[3][3] = 1.0f;
    m = Multiply(m, Multiply(S, OrthoLH(2.0f, 1.0f, 0.5f, 2.5f)));

    // 82456744\x2d824567FC: proj = playerView * m, view = identity, viewProj = proj.
    r.proj = Multiply(in.playerView, m);
    r.view = Identity();
    r.viewProj = r.proj;
    return r;
}

}  // namespace nfsmw_x360

extern "C" void x360_shadow_fit(const float player_view[16], float focus_x, float focus_y, const float light_dir[3],
                                float focus_near, float focus_far, float proj_out[16]) {
    nfsmw_x360::ShadowFitInput in{};
    for (int i = 0; i < 16; ++i) in.playerView.m[i / 4][i % 4] = player_view[i];
    in.cameraFov = 0;
    in.renderTargetWidth = 640;
    in.renderTargetHeight = 480;
    in.lightDirection = {light_dir[0], light_dir[1], light_dir[2]};
    in.focusNear = focus_near;
    in.focusFar = focus_far;
    in.focusScaleX = focus_x;
    in.focusScaleY = focus_y;
    const nfsmw_x360::ShadowFitResult r = nfsmw_x360::FitShadowCamera(in);
    for (int i = 0; i < 16; ++i) proj_out[i] = r.proj.m[i / 4][i % 4];
}

namespace nfsmw_x360 {
// Rejected experimental baseline, Off by default: the large orthographic
// footprint reduces crawl but loses thin-shadow detail. Temporal stability
// alone does not establish appearance acceptance.
// Phase-one single-map version of the stable orthographic cascade design.
// The identity warp removes the view-dependent projective resampling entirely.
// A fixed world basis and a padded sphere contain every yaw of the focus frustum.
// Per-player state retains the FOV envelope and a deep light interval; ordinary
// movement changes only whole-texel XY offsets, shared by casters and receivers.
struct StableShadowState {
    float farPlane = 0, tanX = 0, tanY = 0, depthCentre = 0;
    Vec3 light{};
    int resolution = 0;
};

Mat4 FitStableShadowCamera(const float view[16], float sx, float sy, Vec3 light,
                          float farPlane, int resolution, StableShadowState &state,
                          bool snap = true) {
    float length = std::sqrt(Dot(light, light));
    light = length > 1e-6f ? Normalize(light) : Vec3{1, 0, 0};
    if (light.z < X360E_SHADOW_LIGHT_Z_MIN) {
        light.z = X360E_SHADOW_LIGHT_Z_MIN; light = Normalize(light);
    }
    const Vec3 eye{-(view[12]*view[0] + view[13]*view[1] + view[14]*view[2]),
                   -(view[12]*view[4] + view[13]*view[5] + view[14]*view[6]),
                   -(view[12]*view[8] + view[13]*view[9] + view[14]*view[10])};
    const float tx = 1 / std::fmax(std::fabs(sx), .01f), ty = 1 / std::fmax(std::fabs(sy), .01f);
    const bool reset = state.resolution != resolution || state.farPlane != farPlane ||
        Dot(state.light, light) < .9999905f; // quarter-degree environment change
    if (reset) {
        state = StableShadowState{}; state.resolution = resolution;
        state.farPlane = farPlane; state.light = light;
    }
    // Tiny time-of-day interpolation must not rotate the basis every frame.
    light = state.light;
    const Vec3 depth{-light.x, -light.y, -light.z};
    const Vec3 axis = std::fabs(light.z) < .99f ? Vec3{0, 0, 1} : Vec3{0, 1, 0};
    const Vec3 x = Normalize(Cross(axis, depth)), y = Cross(depth, x);
    // Never shrink a camera mode's envelope as its chase FOV settles.
    state.tanX = std::fmax(state.tanX, tx); state.tanY = std::fmax(state.tanY, ty);
    const float radius = farPlane * std::sqrt(1 + state.tanX*state.tanX + state.tanY*state.tanY);
    // One guard + eight future transition texels and half a snapping texel.
    const float half = radius / (1 - 19.f / resolution), pitch = 2 * half / resolution;
    const float d = Dot(eye, depth);
    if (reset || std::fabs(d - state.depthCentre) > 2 * half)
        state.depthCentre = std::round(d / (2 * half)) * (2 * half);
    // The depth interval includes receiver sphere plus contributing upstream
    // casters. It stays fixed during the local camera sweep (also in Z).
    const float span = 8 * half;
    const float cx = snap ? std::round(Dot(eye, x) / pitch) * pitch : Dot(eye, x);
    const float cy = snap ? std::round(Dot(eye, y) / pitch) * pitch : Dot(eye, y);
    Mat4 out = Identity();
    const float axes[3][3] = {{x.x,y.x,depth.x}, {x.y,y.y,depth.y}, {x.z,y.z,depth.z}};
    for (int i = 0; i < 3; ++i) {
        out.m[i][0] = axes[i][0] / half; out.m[i][1] = axes[i][1] / half;
        out.m[i][2] = axes[i][2] / span;
    }
    out.m[3][0] = -cx / half; out.m[3][1] = -cy / half;
    out.m[3][2] = .5f - state.depthCentre / span;
    return out;
}
}

extern "C" void x360_shadow_stable_fit(const float view[16], float sx, float sy, const float light[3],
                                       float farPlane, int resolution, int player, float out[16]) {
    static nfsmw_x360::StableShadowState states[2];
    auto m = nfsmw_x360::FitStableShadowCamera(view, sx, sy, {light[0],light[1],light[2]},
                                              farPlane, resolution, states[player == 2]);
    for (int i = 0; i < 16; ++i) out[i] = m.m[i/4][i%4];
}
'''
PREDECESSOR_CONSTANTS = r'''
#define X360E_SHADOW_FOCUS_FE 31.0f
#define X360E_SHADOW_FOCUS_RACE 70.0f
#define X360E_SHADOW_NEAR 0.01f
#define X360E_SHADOW_LIGHT_Z_MIN 0.7f
#define X360E_SHADOW_N_SCALE 20.0f
#define X360E_SHADOW_N_OFFSET 0.1f
#define X360E_SHADOW_EYE_BACK 3.0f
#define X360E_SHADOW_Y_SCALE 20.0f
#define X360E_SHADOW_DEPTH_SCALE 10.0f
#define X360E_SHADOW_SIZE 1600
'''

# This adapter executes the old selected stable path: it has only one full-range
# map, so both consumers receive that projection. No missing API is invoked.
OLD_ADAPTER = r'''
#include "predecessor.cpp"
extern "C" void pair(const float *view, double d, int n, double *far, double *near) {
    (void)d; (void)n;
    nfsmw_x360::StableShadowState state;
    auto m = nfsmw_x360::FitStableShadowCamera(view, .845690625f, 1.50345f,
        {-.467f,-.464f,.753f}, 70, 1600, state);
    for(int i=0;i<16;++i) far[i]=near[i]=m.m[i/4][i%4];
}
extern "C" void retail(const float *view, double *out) {
    float m[16], light[3]={-.467f,-.464f,.753f};
    x360_shadow_fit(view,.845690625f,1.50345f,light,.01f,70,m);
    for(int i=0;i<16;++i) out[i]=m[i];
}
'''

D16 = ctypes.c_double * 16
F16 = ctypes.c_float * 16


def view(yaw=0, eye=(0, 0, 1)):
    c, s = math.cos(yaw), math.sin(yaw)
    m = [c, 0, -s, 0, s, 0, c, 0, 0, -1, 0, 0, 0, 0, 0, 1]
    for j in range(3):
        m[12+j] = -sum(eye[i]*m[4*i+j] for i in range(3))
    return F16(*m)


def mulvec(p, m):
    return [sum(p[k]*m[4*k+j] for k in range(4)) for j in range(4)]


def biased(m, n):
    # Retail final receiver convention: x +.5, y -.5, one half-texel bias.
    b = [0.] * 16
    b[0], b[5], b[10], b[15] = .5, -.5, 1, 1
    b[12] = b[13] = .5 + .5/n
    return [sum(m[4*i+k]*b[4*k+j] for k in range(4))
            for i in range(4) for j in range(4)]


def density(m, n):
    # Horizontal camera at (0,0,1), looking +Y, 3840x2160, sx=sy/(16/9).
    # 33x33 road samples: screen NDC x [-.8,.8], lower-screen y [.55,1].
    # All depths [1.50345,2.73355] are within the illustrative 3m envelope.
    sx, sy = .845690625, 1.50345
    strides, errors = [], []
    m = biased(m, n)
    def road(x, y):
        return [x*sy/(sx*y), sy/y, 0, 1]
    def uv(x, y):
        q = mulvec(road(x,y),m)
        return [n*q[j]/q[3] for j in range(2)]
    for iy in range(33):
        y = .55 + .45*iy/32
        for ix in range(33):
            x = -.8 + 1.6*ix/32
            p = road(x,y)
            q = mulvec(p,m)
            dp = [[2/3840*sy/(sx*y),0,0,0],
                  [-p[0]*2/(2160*y),-p[1]*2/(2160*y),0,0]]
            jac = []
            for j in range(2):
                row = []
                for k in range(2):
                    dq = mulvec(dp[k],m)
                    a = n*(dq[j]*q[3]-q[j]*dq[3])/(q[3]*q[3])
                    h = .01
                    delta = h*2/(3840 if k==0 else 2160)
                    plus = uv(x+delta*(k==0),y+delta*(k==1))
                    minus = uv(x-delta*(k==0),y-delta*(k==1))
                    fd = (plus[j]-minus[j])/(2*h)
                    errors.append(abs(a-fd))
                    row.append(a)
                jac.append(row)
            strides.append(max(1/math.hypot(*row) for row in jac))
    a = sorted(strides)
    return a[0], a[len(a)//2], a[math.ceil(.95*len(a))-1], a[-1], max(errors)


def compile_lib(work, source, name):
    cpp = work / (name+'.cpp')
    cpp.write_text(source)
    binary = work / (name+'.dylib')
    r = subprocess.run(['clang++','-std=c++17','-Wall','-Wextra','-Werror',
                        '-dynamiclib','-I',str(work),'-I',str(ROOT/'runtime'),
                        str(cpp),'-o',str(binary)],capture_output=True,text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-4000:])
    lib = ctypes.CDLL(str(binary))
    lib.pair.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.c_double,
                         ctypes.c_int, ctypes.POINTER(ctypes.c_double),
                         ctypes.POINTER(ctypes.c_double)]
    lib.retail.argtypes = [ctypes.POINTER(ctypes.c_float),ctypes.POINTER(ctypes.c_double)]
    return lib


def pinned(work):
    src, const = PREDECESSOR_SOURCE[1:], PREDECESSOR_CONSTANTS[1:]
    assert hashlib.sha256(src.encode()).hexdigest()==PREDECESSOR_SOURCE_SHA256
    assert hashlib.sha256(const.encode()).hexdigest()==PREDECESSOR_CONSTANTS_SHA256
    (work/'predecessor.cpp').write_text(src)
    (work/'gen').mkdir(exist_ok=True)
    (work/'gen/x360_engine.inc').write_text(const)


def baseline(work):
    pinned(work)
    old = compile_lib(work, OLD_ADAPTER, 'original')
    f, near, retail = D16(),D16(),D16()
    old.pair(view(),3,8192,f,near)
    old.retail(view(),retail)
    metrics = density(near,1600)
    print('S1 full-range 1600 density min/median/p95/max =',
          ' / '.join(f'{x:.6f}' for x in metrics[:4]),'px; FD error',f'{metrics[4]:.3g}')
    pitch = 2/math.sqrt(sum(near[i]**2 for i in (0,4,8)))/1600
    print(f'S1 footprint {pitch*1600:.6f}m pitch {pitch*1000:.6f}mm; far-retail error',
          max(abs(f[i]-retail[i]) for i in range(16)))
    return old

NEW_ADAPTER = r'''
#include "production.cpp"
extern "C" void retail(const float *view, double *out) {
    float m[16], light[3]={-.467f,-.464f,.753f};
    x360_shadow_fit(view,.845690625f,1.50345f,light,.01f,70,m);
    for(int i=0;i<16;++i) out[i]=m[i];
}
extern "C" void fixture(const float *view, double d, int n, ShadowNearInput *a) {
    *a=ShadowNearInput{};
    a->camera_position={-(view[12]*view[0]+view[13]*view[1]+view[14]*view[2]),
                         -(view[12]*view[4]+view[13]*view[5]+view[14]*view[6]),
                         -(view[12]*view[8]+view[13]*view[9]+view[14]*view[10])};
    a->camera_near=.01; a->camera_far=d;
    a->max_tan_x=1/.845690625; a->max_tan_y=1/1.50345;
    double len=std::sqrt(.467*.467+.464*.464+.753*.753);
    a->light_depth={.467/len,.464/len,-.753/len};
    const auto &z=a->light_depth;
    double h=std::hypot(z.x,z.y);
    a->light_right={-z.y/h,z.x/h,0};
    const auto &x=a->light_right;
    a->light_up={z.y*x.z-z.z*x.y,z.z*x.x-z.x*x.z,z.x*x.y-z.y*x.x};
    double r=d*std::sqrt(1+a->max_tan_x*a->max_tan_x+a->max_tan_y*a->max_tan_y);
    a->receiver_half_extent={r,r,r};
    a->contributing_casters={{-30,-30,-10},{30,30,40}};
    a->depth_padding=1; a->depth_quantum=16;
    a->resolution=n; a->max_texture_dimension=8192;
    a->max_depth_bytes=uint64_t(8192)*8192*4;
    a->guard_texels=1; a->blend_texels=8;
    double f[16]; retail(view,f);
    nfsmw_x360::Mat4 far{};
    for(int i=0;i<16;++i) far.m[i/4][i%4]=f[i];
    // Use double for the reference receiver convention; the far clip matrix
    // itself is exactly the production float LiSPSM result.
    for(int i=0;i<4;++i) for(int j=0;j<4;++j) for(int k=0;k<4;++k) {
        double b=(k==0&&j==0)?.5:(k==1&&j==1)?-.5:
            (k==2&&j==2)||(k==3&&j==3)?1:
            (k==3&&(j==0||j==1))?.5+.5/1600:0;
        a->world_to_far_texture.m[4*i+j]+=far.m[i][k]*b;
    }
}
extern "C" void pair(const float *view, double d, int n, double *far, double *near) {
    retail(view,far);
    ShadowNearInput a; fixture(view,d,n,&a);
    ShadowNearResult r{};
    shadow_near_fit(&a,&r);
    for(int i=0;i<16;++i) near[i]=r.world_to_clip.m[i];
}
'''


class Vec(ctypes.Structure):
    _fields_ = [('x',ctypes.c_double),('y',ctypes.c_double),('z',ctypes.c_double)]
    def values(self):
        return (self.x,self.y,self.z)


class Matrix(ctypes.Structure):
    _fields_ = [('m',D16)]


class Bounds(ctypes.Structure):
    _fields_ = [('min',Vec),('max',Vec)]


class Rect(ctypes.Structure):
    _fields_ = [(k,ctypes.c_double) for k in ('min_u','min_v','max_u','max_v')]


class Input(ctypes.Structure):
    _fields_ = [('camera_position',Vec)] + [(k,ctypes.c_double) for k in
        ('camera_near','camera_far','max_tan_x','max_tan_y')] + [(k,Vec) for k in
        ('light_right','light_up','light_depth','world_origin','receiver_half_extent')] + [
        ('contributing_casters',Bounds),('depth_padding',ctypes.c_double),
        ('depth_quantum',ctypes.c_double),('resolution',ctypes.c_uint32),
        ('max_texture_dimension',ctypes.c_uint32),('max_depth_bytes',ctypes.c_uint64),
        ('guard_texels',ctypes.c_double),('blend_texels',ctypes.c_double),
        ('world_to_far_texture',Matrix)]


class Result(ctypes.Structure):
    _fields_ = [('resolution',ctypes.c_uint32),('depth_bytes',ctypes.c_uint64)] + [
        (k,Vec) for k in ('light_right','light_up','light_depth','receiver_half_extent','snapped_origin')
        ] + [(k,ctypes.c_double) for k in ('centre_right','centre_up','half_width',
        'half_height','pitch_right','pitch_up','depth_min','depth_max')] + [
        ('usable',Rect),('blend',Rect)] + [(k,Matrix) for k in
        ('world_to_clip','world_to_texture','far_to_near')]


class Checks:
    def __init__(self, label):
        self.label, self.count, self.failed = label, 0, 0
    def __call__(self, condition, message):
        self.count += 1
        if not condition:
            self.failed += 1
            if self.failed <= 4:
                print(self.label, 'FAIL', message)
    def summary(self):
        print(f'{self.label}: {self.count} checks, {self.failed} failures')


def pair_checks(lib, label):
    c = Checks(label)
    f, near, expected = D16(),D16(),D16()
    lib.pair(view(),3,8192,f,near)
    lib.retail(view(),expected)
    # Actual selected allocation is part of the compatible adapter contract.
    selected_n = 1600 if label=='original' else 8192
    stats = density(near,selected_n)
    c(stats[2]<3, 'independently dense near p95 <3px on numerical fixture')
    c(stats[3]<3, 'independently dense near max <3px on numerical fixture')
    c(max(abs(f[i]-expected[i]) for i in range(16))<1e-12,'far remains retail LiSPSM')
    c(stats[4]<1e-7,'analytic/finite-difference Jacobian')
    other_f, other = D16(),D16()
    lib.pair(view(),6,8192,other_f,other)
    width = 2/math.sqrt(sum(near[i]**2 for i in (0,4,8)))
    wide = 2/math.sqrt(sum(other[i]**2 for i in (0,4,8)))
    c(abs(wide/width-2)<1e-6,'near footprint independent of far focus')
    c(list(f)==list(other_f),'far unchanged by near footprint')
    lib.pair(view(),3,4096,other_f,other)
    c(list(f)==list(other_f),'far unchanged by near allocation')
    # Two fixed-footprint independent allocations have pitches differing ~2x;
    # guard padding slightly widens the smaller allocation.
    pitch = width/selected_n
    pitch2 = 2/math.sqrt(sum(other[i]**2 for i in (0,4,8)))/(1600 if label=='original' else 4096)
    c(1.99<pitch2/pitch<2.02,'independent 4096/8192 density')
    if label!='original':
        small=density(other,4096)
        print(label,'4096 stride min/median/p95/max','/'.join(f'{x:.6f}' for x in small[:4]))
        c(small[4]<1e-7,'4096 analytic/finite-difference Jacobian')
    # Yaw stability already PASSES in the reviewed predecessor.
    for degrees in range(0,360,5):
        lib.pair(view(math.radians(degrees)),3,8192,other_f,other)
        c(max(abs(near[i]-other[i]) for i in range(16))<2e-7,'existing yaw stability')
    print(label, 'stride min/median/p95/max', '/'.join(f'{x:.6f}' for x in stats[:4]),
          f'px; footprint {width:.6f}m; pitch {pitch*1000:.6f}mm')
    c.summary()
    return c


def bind_api(lib):
    lib.fixture.argtypes=[ctypes.POINTER(ctypes.c_float),ctypes.c_double,ctypes.c_int,ctypes.POINTER(Input)]
    lib.shadow_near_fit.argtypes=[ctypes.POINTER(Input),ctypes.POINTER(Result)]
    lib.shadow_near_fit.restype=ctypes.c_int


def fixture(lib, eye=(0,0,1), d=3, n=8192, yaw=0):
    a=Input()
    lib.fixture(view(yaw,eye),d,n,ctypes.byref(a))
    return a


def fit(lib,a):
    r=Result()
    return lib.shadow_near_fit(ctypes.byref(a),ctypes.byref(r)),r


def dot(a,b):
    return sum(x*y for x,y in zip(a,b))


def world_point(a, cr, cu, cd):
    return [a.world_origin.values()[i]+cr*a.light_right.values()[i]+
            cu*a.light_up.values()[i]+cd*a.light_depth.values()[i] for i in range(3)]+[1]


def project(p,m):
    q=mulvec(p,m)
    return [q[j]/q[3] for j in range(3)]


def api_checks(lib,label):
    bind_api(lib)
    c=Checks(label)
    worst_transform=worst_depth=worst_phase=0
    # Even/odd allocations, independent view state, translated world origin,
    # narrow/wide FOV, vertical/low sun and receiver/caster depth ranges.
    for n in (4096,8192,4095):
        for origin in ((0,0,0),(12345,-23456,3456)):
            a=fixture(lib,n=n)
            a.world_origin=Vec(*origin)
            a.camera_position=Vec(*(v+origin[i] for i,v in enumerate((0,0,1))))
            a.contributing_casters=Bounds(Vec(*(origin[i]+v for i,v in enumerate((-30,-30,-10)))),
                                          Vec(*(origin[i]+v for i,v in enumerate((30,30,40)))))
            ok,r=fit(lib,a)
            c(ok==1,'valid supported allocation/origin')
            if not ok:
                continue
            c(r.depth_bytes==n*n*4 and r.resolution==n,'independent payload accounting')
            c(0<r.usable.min_u<r.blend.min_u<.5<r.blend.max_u<r.usable.max_u<1,'nested blend bounds')
            p=[*origin,1]
            uv=project(p,r.world_to_texture.m)
            for j in range(2):
                e=abs((uv[j]*n-.5)-round(uv[j]*n-.5))
                worst_phase=max(worst_phase,e)
                c(e<2e-7,'world-origin phase includes exactly one texture-centre bias')
            # A cell-centred reference avoids claiming every arbitrary subtexel
            # move is invariant when it crosses a legitimate snap boundary.
            centre=world_point(a,r.centre_right,r.centre_up,
                dot([a.camera_position.values()[i]-origin[i] for i in range(3)],a.light_depth.values()))
            a.camera_position=Vec(*centre[:3])
            ok,ref=fit(lib,a)
            c(ok==1,'centred fit')
            for fraction in (.04,.12,.24,.40):
                delta=[fraction*(r.pitch_right*a.light_right.values()[i]+
                                 r.pitch_up*a.light_up.values()[i]) for i in range(3)]
                a.camera_position=Vec(*(centre[i]+delta[i] for i in range(3)))
                ok,moved=fit(lib,a)
                c(ok==1 and max(abs(x-y) for x,y in zip(ref.world_to_texture.m,moved.world_to_texture.m))<2e-11,
                  'subtexel translation preserves sample lattice')
            a.camera_position=Vec(*(centre[i]+r.pitch_right*a.light_right.values()[i] for i in range(3)))
            ok,moved=fit(lib,a)
            before=project(p,ref.world_to_texture.m)
            after=project(p,moved.world_to_texture.m)
            c(ok==1 and abs((after[0]-before[0])*n+1)<2e-7,'one snapped cell changes exactly one texel')
            a.camera_position=Vec(*centre[:3])
            ok,r=fit(lib,a)
            # Exact yaw-frustum coverage, including final sample/blend bounds.
            tx,ty=a.max_tan_x,a.max_tan_y
            for degrees in range(0,360,5):
                yaw=math.radians(degrees)
                for d in (a.camera_near,a.camera_far):
                    for x in (-d*tx,d*tx):
                        for z in (-d*ty,d*ty):
                            p=[centre[0]+x*math.cos(yaw)-d*math.sin(yaw),
                               centre[1]+x*math.sin(yaw)+d*math.cos(yaw),centre[2]+z,1]
                            uv=project(p,r.world_to_texture.m)
                            c(r.blend.min_u<uv[0]<r.blend.max_u and r.blend.min_v<uv[1]<r.blend.max_v and 0<uv[2]<1,
                              'declared yaw receiver envelope inside full-near bounds')
                            # Homogeneous far -> near MUST agree in xyz/w. Far
                            # is perspective; near depth is independently fitted.
                            far=mulvec(p,a.world_to_far_texture.m)
                            mapped=project(far,r.far_to_near.m)
                            e=max(abs(mapped[j]-uv[j]) for j in range(3))
                            worst_transform=max(worst_transform,e)
                            worst_depth=max(worst_depth,abs(mapped[2]-uv[2]))
                            c(e<2e-8,'full homogeneous far-to-near xyz/w')
            # EVERY caster-bound corner is represented in the returned depth
            # interval, including upstream casters outside the receiver sphere.
            for bits in range(8):
                p=[(a.contributing_casters.max if bits&(1<<i) else a.contributing_casters.min).values()[i] for i in range(3)]+[1]
                z=project(p,r.world_to_texture.m)[2]
                c(0<z<1,'upstream caster depth is retained')
            c(r.depth_min%a.depth_quantum==0 and r.depth_max%a.depth_quantum==0,'outward quantized depth')
    # The declared sphere also covers pitch and roll; it is not a yaw-only
    # fit disguised as an arbitrary camera envelope.
    a=fixture(lib)
    ok,r=fit(lib,a)
    for yaw in (0,.5,2):
        for pitch in (-1,-.3,.7):
            for roll in (-.7,0,1):
                for xsign in (-1,1):
                    for ysign in (-1,1):
                        x=xsign*a.camera_far*a.max_tan_x
                        z=ysign*a.camera_far*a.max_tan_y
                        d=a.camera_far
                        x,z=x*math.cos(roll)-z*math.sin(roll),x*math.sin(roll)+z*math.cos(roll)
                        d,z=d*math.cos(pitch)-z*math.sin(pitch),d*math.sin(pitch)+z*math.cos(pitch)
                        x,d=x*math.cos(yaw)-d*math.sin(yaw),x*math.sin(yaw)+d*math.cos(yaw)
                        uv=project([x,d,1+z,1],r.world_to_texture.m)
                        c(r.blend.min_u<uv[0]<r.blend.max_u and r.blend.min_v<uv[1]<r.blend.max_v and 0<uv[2]<1,
                          'pitched/rolled receiver coverage')
    # No implicit per-player state: interleave unrelated views/configurations.
    a=fixture(lib)
    ok,r=fit(lib,a)
    b=fixture(lib,eye=(200,-400,5),d=12,n=4096,yaw=2)
    b.max_tan_x=3; b.max_tan_y=2
    b.receiver_half_extent=Vec(60,60,60)
    c(fit(lib,b)[0]==1,'second camera/FOV envelope')
    ok,again=fit(lib,a)
    c(ok==1 and bytes(r)==bytes(again),'independent player state')
    for axes in ((Vec(1,0,0),Vec(0,1,0),Vec(0,0,1)),
                 (Vec(0,1,0),Vec(0,0,1),Vec(1,0,0))):
        a=fixture(lib)
        a.light_right,a.light_up,a.light_depth=axes
        c(fit(lib,a)[0]==1,'vertical/horizontal orthonormal light basis')
    # Caller matrix may have a different depth range and projective w. This
    # exercises the actual transform against several nonsingular far matrices.
    for kind in range(4):
        a=fixture(lib)
        m=a.world_to_far_texture.m
        if kind==1:
            for i in range(4): m[4*i+2]=1.7*m[4*i+2]-.3*m[4*i+3]
        if kind==2:
            for i in range(4): m[4*i+2]=m[4*i+3]-m[4*i+2]  # reversed far Z
        if kind==3:
            m[:]=[1,0,0,.001,0,1,0,-.002,0,0,.04,.003,0,0,.6,1]
        ok,r=fit(lib,a)
        c(ok==1,'arbitrary invertible far depth mapping')
        for p in ((0,2,0,1),(-2,1,4,1),(100,-200,40,1)):
            direct=project(p,r.world_to_texture.m)
            mapped=project(mulvec(p,m),r.far_to_near.m)
            e=max(abs(x-y) for x,y in zip(direct,mapped))
            worst_transform=max(worst_transform,e)
            worst_depth=max(worst_depth,abs(direct[2]-mapped[2]))
            c(e<2e-8,'transformed depth and w, including reversed far Z')
    # Check overflow/NaN, singular and malformed inputs with cleared output.
    def reject(a, message):
        out=Result()
        ctypes.memset(ctypes.byref(out),0x55,ctypes.sizeof(out))
        rc=lib.shadow_near_fit(ctypes.byref(a),ctypes.byref(out))
        c(rc==0 and bytes(out)==bytes(Result()),message)
    for key,values in {
        'camera_near':(0,-1,float('nan'),4), 'camera_far':(0,float('inf'),.001),
        'max_tan_x':(0,float('nan'),1e308),'max_tan_y':(-1,float('inf')),
        'resolution':(0,16,8193,2**32-1),'max_texture_dimension':(0,4096),
        'max_depth_bytes':(0,8192*8192*4-1), 'guard_texels':(0,-1,float('inf')),
        'blend_texels':(-1,float('nan'),5000),'depth_quantum':(0,float('inf')),
        'depth_padding':(0,-1,float('nan'))}.items():
        for val in values:
            a=fixture(lib); setattr(a,key,val); reject(a,'invalid '+key)
    for key in ('camera_position','world_origin','light_right','light_up','light_depth',
                'receiver_half_extent'):
        a=fixture(lib); setattr(a,key,Vec(float('nan'),0,0)); reject(a,'invalid '+key)
    for key in ('light_right','light_up','light_depth'):
        a=fixture(lib); setattr(a,key,Vec(0,0,0)); reject(a,'degenerate '+key)
    a=fixture(lib); a.light_right=Vec(*(-v for v in a.light_right.values())); reject(a,'left-handed light basis')
    a=fixture(lib); a.light_up=a.light_right; reject(a,'nonorthogonal basis')
    a=fixture(lib); a.receiver_half_extent=Vec(.1,.1,.1); reject(a,'insufficient supported camera envelope')
    a=fixture(lib); a.contributing_casters.min.x=100; reject(a,'inverted caster bounds')
    a=fixture(lib); a.contributing_casters.max.z=float('inf'); reject(a,'nonfinite caster bounds')
    a=fixture(lib); a.world_to_far_texture=Matrix(); reject(a,'singular far matrix')
    a=fixture(lib); a.world_to_far_texture.m[0]=float('nan'); reject(a,'nonfinite far matrix')
    a=fixture(lib); a.depth_quantum=1e-320; reject(a,'depth interval overflow')
    out=Result()
    c(lib.shadow_near_fit(None,ctypes.byref(out))==0 and bytes(out)==bytes(Result()),'null input')
    c(lib.shadow_near_fit(ctypes.byref(a),None)==0,'null output')
    c.summary()
    print(label, f'max homogeneous/depth/phase errors {worst_transform:.3g}/{worst_depth:.3g}/{worst_phase:.3g}')
    return c


def run():
    scratch=ROOT/'.metal-cache/s2'
    scratch.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as d:
        work=Path(d)
        old=baseline(work)
        original=pair_checks(old,'original')
        assert original.failed==5, 'predecessor must execute and fail five density/independence checks'
        src=(ROOT/'runtime/x360_shadow_fit.cpp').read_text()
        assert src.startswith(PREDECESSOR_SOURCE[1:]), 'existing far and rejected stable code must remain unchanged'
        (work/'production.cpp').write_text(src)
        good=compile_lib(work,NEW_ADAPTER,'good')
        common=pair_checks(good,'good')
        far_checks=Checks('pinned/current far parity')
        for yaw in range(0,360,5):
            for eye in ((0,0,1),(100,-300,2),(-1200,500,10)):
                before,after=D16(),D16()
                old.retail(view(math.radians(yaw),eye),before)
                good.retail(view(math.radians(yaw),eye),after)
                far_checks(list(before)==list(after),'retail LiSPSM byte-equivalent result')
        far_checks.summary()
        assert not far_checks.failed
        numerical=api_checks(good,'good API')
        assert common.failed==0 and numerical.failed==0
        mutations={
            'unsnapped':('std::round(cr/r.pitch_right-phase)','(cr/r.pitch_right-phase)'),
            'wrong-division':('r.far_to_near=near_mul(inverse,r.world_to_texture);',
                'r.far_to_near=near_mul(inverse,r.world_to_texture); r.far_to_near.m[3]=r.far_to_near.m[7]=r.far_to_near.m[11]=0; r.far_to_near.m[15]=1;'),
            'omitted-upstream':('for (int i=0;i<8;++i) {','for (int i=0;i<0;++i) {'),
            'double-centre':('bias.m[12]=bias.m[13]=.5+.5/n;','bias.m[12]=bias.m[13]=.5+1/n;'),
            'shared-z':('r.far_to_near=near_mul(inverse,r.world_to_texture);',
                'r.far_to_near=near_mul(inverse,r.world_to_texture); for(int i=0;i<4;++i) r.far_to_near.m[4*i+2]=(i==2);'),
            'wrong-order':('near_mul(inverse,r.world_to_texture)','near_mul(r.world_to_texture,inverse)'),
        }
        for name,(before,after) in mutations.items():
            assert src.count(before)==1,(name,src.count(before))
            (work/'production.cpp').write_text(src.replace(before,after))
            mutant=compile_lib(work,NEW_ADAPTER,name)
            broken=api_checks(mutant,name)
            assert broken.failed>0, name+' survived'
        # Header must also compile from C, the renderer's language boundary.
        header=work/'header.c'
        header.write_text('#include "shadow_near.h"\nint main(void) { ShadowNearInput a={0}; ShadowNearResult b={0}; return shadow_near_fit(&a,&b); }\n')
        subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror','-fsyntax-only',
                        '-I',str(ROOT/'runtime'),str(header)],check=True,capture_output=True)
        print('SHADOW NEAR FIT PASS (executed predecessor + six behavioral mutations rejected)')
    return 0


if __name__=='__main__':
    raise SystemExit(run())
