// d3dx.c - D3DX9 math imports (d3dx9_26.dll), implemented natively. All are __stdcall.
// D3D convention: row vectors, out = v * M; matrices are 16 floats, row-major (m[r][c] at r*4+c).
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "host.h"

static void ld(uint32_t a, float *m, int n) { memcpy(m, G + a, 4 * n); }
static void st(uint32_t a, const float *m, int n) { memcpy(G + a, m, 4 * n); }

static void mul(float *o, const float *a, const float *b) {
    float r[16];
    for (int i = 0; i < 4; ++i)
        for (int j = 0; j < 4; ++j)
            r[i * 4 + j] = a[i * 4] * b[j] + a[i * 4 + 1] * b[4 + j] + a[i * 4 + 2] * b[8 + j] + a[i * 4 + 3] * b[12 + j];
    memcpy(o, r, sizeof r);
}

// General 4x4 inverse by cofactors. Returns 0 when singular (D3DX then returns NULL and leaves pOut alone).
static int inverse(float *o, float *det_out, const float *m) {
    double inv[16], a[16];
    for (int i = 0; i < 16; ++i) a[i] = m[i];
    inv[0] = a[5] * a[10] * a[15] - a[5] * a[11] * a[14] - a[9] * a[6] * a[15] + a[9] * a[7] * a[14] + a[13] * a[6] * a[11] - a[13] * a[7] * a[10];
    inv[4] = -a[4] * a[10] * a[15] + a[4] * a[11] * a[14] + a[8] * a[6] * a[15] - a[8] * a[7] * a[14] - a[12] * a[6] * a[11] + a[12] * a[7] * a[10];
    inv[8] = a[4] * a[9] * a[15] - a[4] * a[11] * a[13] - a[8] * a[5] * a[15] + a[8] * a[7] * a[13] + a[12] * a[5] * a[11] - a[12] * a[7] * a[9];
    inv[12] = -a[4] * a[9] * a[14] + a[4] * a[10] * a[13] + a[8] * a[5] * a[14] - a[8] * a[6] * a[13] - a[12] * a[5] * a[10] + a[12] * a[6] * a[9];
    inv[1] = -a[1] * a[10] * a[15] + a[1] * a[11] * a[14] + a[9] * a[2] * a[15] - a[9] * a[3] * a[14] - a[13] * a[2] * a[11] + a[13] * a[3] * a[10];
    inv[5] = a[0] * a[10] * a[15] - a[0] * a[11] * a[14] - a[8] * a[2] * a[15] + a[8] * a[3] * a[14] + a[12] * a[2] * a[11] - a[12] * a[3] * a[10];
    inv[9] = -a[0] * a[9] * a[15] + a[0] * a[11] * a[13] + a[8] * a[1] * a[15] - a[8] * a[3] * a[13] - a[12] * a[1] * a[11] + a[12] * a[3] * a[9];
    inv[13] = a[0] * a[9] * a[14] - a[0] * a[10] * a[13] - a[8] * a[1] * a[14] + a[8] * a[2] * a[13] + a[12] * a[1] * a[10] - a[12] * a[2] * a[9];
    inv[2] = a[1] * a[6] * a[15] - a[1] * a[7] * a[14] - a[5] * a[2] * a[15] + a[5] * a[3] * a[14] + a[13] * a[2] * a[7] - a[13] * a[3] * a[6];
    inv[6] = -a[0] * a[6] * a[15] + a[0] * a[7] * a[14] + a[4] * a[2] * a[15] - a[4] * a[3] * a[14] - a[12] * a[2] * a[7] + a[12] * a[3] * a[6];
    inv[10] = a[0] * a[5] * a[15] - a[0] * a[7] * a[13] - a[4] * a[1] * a[15] + a[4] * a[3] * a[13] + a[12] * a[1] * a[7] - a[12] * a[3] * a[5];
    inv[14] = -a[0] * a[5] * a[14] + a[0] * a[6] * a[13] + a[4] * a[1] * a[14] - a[4] * a[2] * a[13] - a[12] * a[1] * a[6] + a[12] * a[2] * a[5];
    inv[3] = -a[1] * a[6] * a[11] + a[1] * a[7] * a[10] + a[5] * a[2] * a[11] - a[5] * a[3] * a[10] - a[9] * a[2] * a[7] + a[9] * a[3] * a[6];
    inv[7] = a[0] * a[6] * a[11] - a[0] * a[7] * a[10] - a[4] * a[2] * a[11] + a[4] * a[3] * a[10] + a[8] * a[2] * a[7] - a[8] * a[3] * a[6];
    inv[11] = -a[0] * a[5] * a[11] + a[0] * a[7] * a[9] + a[4] * a[1] * a[11] - a[4] * a[3] * a[9] - a[8] * a[1] * a[7] + a[8] * a[3] * a[5];
    inv[15] = a[0] * a[5] * a[10] - a[0] * a[6] * a[9] - a[4] * a[1] * a[10] + a[4] * a[2] * a[9] + a[8] * a[1] * a[6] - a[8] * a[2] * a[5];
    double det = a[0] * inv[0] + a[1] * inv[4] + a[2] * inv[8] + a[3] * inv[12];
    if (det_out) *det_out = (float)det;
    if (det == 0) return 0;
    for (int i = 0; i < 16; ++i) o[i] = (float)(inv[i] / det);
    return 1;
}

static void xform4(float *o, const float *v, const float *m) {   // o = (v.xyz, 1) * M, 4 components
    float r[4];
    for (int j = 0; j < 4; ++j) r[j] = v[0] * m[j] + v[1] * m[4 + j] + v[2] * m[8 + j] + m[12 + j];
    memcpy(o, r, sizeof r);
}

int d3dx(Cpu *c, const char *fn) {
    float a[16], b[16], o[16], v[4];
    if (!strcmp(fn, "D3DXMatrixTranspose")) {
        ld(arg(c, 1), a, 16);
        for (int i = 0; i < 4; ++i) for (int j = 0; j < 4; ++j) o[i * 4 + j] = a[j * 4 + i];
        st(arg(c, 0), o, 16);
        RET(arg(c, 0), 2);
    }
    if (!strcmp(fn, "D3DXMatrixMultiply")) {
        widescreen_projection(c, arg(c, 2));        // Hor+ for the game's per-view projection (only at 006CF62A)
        x360_shadow_camera(c, arg(c, 1), arg(c, 2)); // X360 light camera for view 15 (only at 006E50C3)
        ld(arg(c, 1), a, 16); ld(arg(c, 2), b, 16);
        mul(o, a, b);
        st(arg(c, 0), o, 16);
        RET(arg(c, 0), 3);
    }
    if (!strcmp(fn, "D3DXMatrixInverse")) {           // (pOut, pDeterminant, pM)
        float det;
        ld(arg(c, 2), a, 16);
        int ok = inverse(o, &det, a);
        if (arg(c, 1)) st(arg(c, 1), &det, 1);
        if (!ok) RET(0, 3);
        st(arg(c, 0), o, 16);
        RET(arg(c, 0), 3);
    }
    if (!strcmp(fn, "D3DXMatrixTranslation")) {
        memset(o, 0, sizeof o);
        o[0] = o[5] = o[10] = o[15] = 1;
        o[12] = argf(c, 1); o[13] = argf(c, 2); o[14] = argf(c, 3);
        st(arg(c, 0), o, 16);
        RET(arg(c, 0), 4);
    }
    if (!strcmp(fn, "D3DXMatrixOrthoLH") || !strcmp(fn, "D3DXMatrixPerspectiveLH")) {   // (pOut, w, h, zn, zf)
        float w = argf(c, 1), h = argf(c, 2), zn = argf(c, 3), zf = argf(c, 4);
        memset(o, 0, sizeof o);
        if (fn[10] == 'O') {
            o[0] = 2 / w; o[5] = 2 / h; o[10] = 1 / (zf - zn); o[14] = zn / (zn - zf); o[15] = 1;
        } else {
            o[0] = 2 * zn / w; o[5] = 2 * zn / h; o[10] = zf / (zf - zn); o[11] = 1; o[14] = zn * zf / (zn - zf);
        }
        st(arg(c, 0), o, 16);
        RET(arg(c, 0), 5);
    }
    if (!strcmp(fn, "D3DXVec3Normalize")) {
        ld(arg(c, 1), v, 3);
        float len = sqrtf(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
        if (len == 0) v[0] = v[1] = v[2] = 0;
        else for (int i = 0; i < 3; ++i) v[i] /= len;
        st(arg(c, 0), v, 3);
        RET(arg(c, 0), 2);
    }
    if (!strcmp(fn, "D3DXVec3Transform")) {           // D3DXVECTOR4 out
        ld(arg(c, 1), v, 3); ld(arg(c, 2), a, 16);
        xform4(o, v, a);
        st(arg(c, 0), o, 4);
        RET(arg(c, 0), 3);
    }
    if (!strcmp(fn, "D3DXVec3TransformNormal")) {
        ld(arg(c, 1), v, 3); ld(arg(c, 2), a, 16);
        for (int j = 0; j < 3; ++j) o[j] = v[0] * a[j] + v[1] * a[4 + j] + v[2] * a[8 + j];
        st(arg(c, 0), o, 3);
        RET(arg(c, 0), 3);
    }
    if (!strcmp(fn, "D3DXVec4Transform")) {
        ld(arg(c, 1), v, 4); ld(arg(c, 2), a, 16);
        for (int j = 0; j < 4; ++j) o[j] = v[0] * a[j] + v[1] * a[4 + j] + v[2] * a[8 + j] + v[3] * a[12 + j];
        st(arg(c, 0), o, 4);
        RET(arg(c, 0), 3);
    }
    if (!strcmp(fn, "D3DXVec3TransformCoordArray")) {  // (pOut, OutStride, pV, VStride, pM, n)
        uint32_t out = arg(c, 0), os = arg(c, 1), in = arg(c, 2), is = arg(c, 3), n = arg(c, 5);
        ld(arg(c, 4), a, 16);
        for (uint32_t k = 0; k < n; ++k) {
            ld(in + k * is, v, 3);
            xform4(o, v, a);
            float w = o[3] != 0 ? 1 / o[3] : 0;  // D3DX divides by w unconditionally; w = 0 gives inf there
            for (int j = 0; j < 3; ++j) o[j] *= w;
            st(out + k * os, o, 3);
        }
        RET(out, 6);
    }
    return 0;
}
