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
