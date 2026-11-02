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

#include "shadow_near.h"
#include <algorithm>
#include <limits>

namespace {
using NearVec = ShadowNearVec3;
using NearMat = ShadowNearMatrix;
double near_dot(NearVec a, NearVec b) { return a.x*b.x + a.y*b.y + a.z*b.z; }
NearVec near_sub(NearVec a, NearVec b) { return {a.x-b.x,a.y-b.y,a.z-b.z}; }
bool near_finite(NearVec v) {
    return std::isfinite(v.x) && std::isfinite(v.y) && std::isfinite(v.z);
}
bool near_positive(double v) { return std::isfinite(v) && v > 0; }
NearMat near_mul(const NearMat &a, const NearMat &b) {
    NearMat r{};
    for (int i=0;i<4;++i) for (int j=0;j<4;++j) for (int k=0;k<4;++k)
        r.m[4*i+j] += a.m[4*i+k]*b.m[4*k+j];
    return r;
}
// Scaled partial-pivot Gauss-Jordan, including projective/depth coefficients.
bool near_inverse(const NearMat &m, NearMat &out) {
    double a[4][8]{}, scale[4]{};
    for (int i=0;i<4;++i) for (int j=0;j<4;++j) {
        if (!std::isfinite(m.m[4*i+j])) return false;
        a[i][j]=m.m[4*i+j];
        scale[i]=std::max(scale[i],std::fabs(a[i][j]));
        a[i][j+4]=(i==j);
    }
    for (int i=0;i<4;++i) {
        int p=i;
        for (int k=i;k<4;++k) {
            if (!(scale[k]>0)) return false;
            if (std::fabs(a[k][i])/scale[k] > std::fabs(a[p][i])/scale[p]) p=k;
        }
        if (std::fabs(a[p][i])/scale[p] < 1e-12) return false;
        for (int j=0;j<8;++j) std::swap(a[p][j],a[i][j]);
        std::swap(scale[p],scale[i]);
        const double pivot=a[i][i];
        for (double &v : a[i]) v/=pivot;
        for (int k=0;k<4;++k) if (k!=i) {
            const double f=a[k][i];
            for (int j=0;j<8;++j) a[k][j]-=f*a[i][j];
        }
    }
    for (int i=0;i<4;++i) for (int j=0;j<4;++j) {
        out.m[4*i+j]=a[i][j+4];
        if (!std::isfinite(out.m[4*i+j])) return false;
    }
    return true;
}
}

extern "C" int shadow_near_fit(const ShadowNearInput *in, ShadowNearResult *out) {
    if (!out) return 0;
    *out=ShadowNearResult{};
    if (!in) return 0;
    const auto &a=*in;
    const NearVec axes[3]={a.light_right,a.light_up,a.light_depth};
    for (int i=0;i<3;++i) {
        if (!near_finite(axes[i]) || std::fabs(near_dot(axes[i],axes[i])-1)>1e-8) return 0;
        for (int j=0;j<i;++j) if (std::fabs(near_dot(axes[i],axes[j]))>1e-8) return 0;
    }
    const NearVec cross{axes[0].y*axes[1].z-axes[0].z*axes[1].y,
                        axes[0].z*axes[1].x-axes[0].x*axes[1].z,
                        axes[0].x*axes[1].y-axes[0].y*axes[1].x};
    if (near_dot(cross,axes[2])<1-1e-8) return 0;
    if (!near_finite(a.camera_position) || !near_finite(a.world_origin) ||
        !near_positive(a.camera_near) || !near_positive(a.camera_far) ||
        a.camera_far<=a.camera_near || !near_positive(a.max_tan_x) || !near_positive(a.max_tan_y) ||
        !near_finite(a.receiver_half_extent) || !near_finite(a.contributing_casters.min) ||
        !near_finite(a.contributing_casters.max) || !near_positive(a.depth_quantum) ||
        !near_positive(a.depth_padding) || !std::isfinite(a.guard_texels) || a.guard_texels<1 ||
        !std::isfinite(a.blend_texels) || a.blend_texels<0) return 0;
    const auto &lo=a.contributing_casters.min;
    const auto &hi=a.contributing_casters.max;
    if (lo.x>hi.x || lo.y>hi.y || lo.z>hi.z) return 0;
    const double n=a.resolution;
    // Check the independently selected near payload, without the far-map limit.
    if (!a.resolution || a.resolution>a.max_texture_dimension ||
        a.resolution>std::numeric_limits<uint64_t>::max()/4/a.resolution) return 0;
    const uint64_t bytes=uint64_t(a.resolution)*a.resolution*4;
    if (bytes>a.max_depth_bytes) return 0;
    // One texel allows half a snap plus the final half-texel centre convention.
    const double pad=a.guard_texels+a.blend_texels+1;
    if (!(n>2*pad)) return 0;
    const double half[3]={a.receiver_half_extent.x,a.receiver_half_extent.y,a.receiver_half_extent.z};
    // The sphere contains every orientation of the declared camera frustum.
    const double radius=a.camera_far*std::hypot(1.0,std::hypot(a.max_tan_x,a.max_tan_y));
    for (int i=0;i<3;++i) {
        if (!near_positive(half[i]) || !std::isfinite(radius) || half[i]+1e-12*radius<radius) return 0;
    }
    NearMat inverse{};
    if (!near_inverse(a.world_to_far_texture,inverse)) return 0;
    ShadowNearResult r{};
    r.resolution=a.resolution; r.depth_bytes=bytes;
    r.light_right=a.light_right; r.light_up=a.light_up; r.light_depth=a.light_depth;
    r.receiver_half_extent=a.receiver_half_extent;
    r.half_width=half[0]/(1-2*pad/n); r.half_height=half[1]/(1-2*pad/n);
    r.pitch_right=2*r.half_width/n; r.pitch_up=2*r.half_height/n;
    const NearVec relative=near_sub(a.camera_position,a.world_origin);
    // World origin maps to a texel centre after the final +.5/N bias, even for
    // odd N. Snap the FINAL sample lattice, not just the unbiased clip centre.
    const double phase=std::fmod(n,2.0)*.5;
    const double cr=near_dot(relative,axes[0]), cu=near_dot(relative,axes[1]);
    r.centre_right=(std::round(cr/r.pitch_right-phase)+phase)*r.pitch_right;
    r.centre_up=(std::round(cu/r.pitch_up-phase)+phase)*r.pitch_up;
    r.snapped_origin={a.world_origin.x+r.centre_right*axes[0].x+r.centre_up*axes[1].x,
                      a.world_origin.y+r.centre_right*axes[0].y+r.centre_up*axes[1].y,
                      a.world_origin.z+r.centre_right*axes[0].z+r.centre_up*axes[1].z};
    const double cd=near_dot(relative,axes[2]);
    double dmin=cd-half[2], dmax=cd+half[2];
    for (int i=0;i<8;++i) {
        const NearVec p{(i&1)?hi.x:lo.x,(i&2)?hi.y:lo.y,(i&4)?hi.z:lo.z};
        const double d=near_dot(near_sub(p,a.world_origin),axes[2]);
        dmin=std::min(dmin,d); dmax=std::max(dmax,d);
    }
    r.depth_min=std::floor((dmin-a.depth_padding)/a.depth_quantum)*a.depth_quantum;
    r.depth_max=std::ceil((dmax+a.depth_padding)/a.depth_quantum)*a.depth_quantum;
    const double span=r.depth_max-r.depth_min;
    if (!near_positive(span)) return 0;
    const double extent[3]={r.half_width,r.half_height,span};
    for (int i=0;i<3;++i) {
        const double v[3]={axes[i].x,axes[i].y,axes[i].z};
        for (int j=0;j<3;++j) r.world_to_clip.m[4*j+i]=v[j]/extent[i];
    }
    r.world_to_clip.m[12]=-(near_dot(a.world_origin,axes[0])+r.centre_right)/r.half_width;
    r.world_to_clip.m[13]=-(near_dot(a.world_origin,axes[1])+r.centre_up)/r.half_height;
    r.world_to_clip.m[14]=-(near_dot(a.world_origin,axes[2])+r.depth_min)/span;
    r.world_to_clip.m[15]=1;
    NearMat bias{};
    bias.m[0]=.5; bias.m[5]=-.5; bias.m[10]=bias.m[15]=1;
    bias.m[12]=bias.m[13]=.5+.5/n;
    r.world_to_texture=near_mul(r.world_to_clip,bias);
    r.far_to_near=near_mul(inverse,r.world_to_texture);
    const double outer=a.guard_texels/n, inner=(a.guard_texels+a.blend_texels)/n;
    r.usable={outer,outer,1-outer,1-outer};
    r.blend={inner,inner,1-inner,1-inner};
    // Overflow/cancellation must not return a partly valid fit.
    if (!near_finite(r.snapped_origin) || !std::isfinite(r.centre_right) ||
        !std::isfinite(r.centre_up) || !near_positive(r.pitch_right) ||
        !near_positive(r.pitch_up) || !std::isfinite(r.depth_min) ||
        !std::isfinite(r.depth_max)) return 0;
    for (int i=0;i<16;++i) if (!std::isfinite(r.world_to_clip.m[i]) ||
        !std::isfinite(r.world_to_texture.m[i]) || !std::isfinite(r.far_to_near.m[i])) return 0;
    *out=r;
    return 1;
}
