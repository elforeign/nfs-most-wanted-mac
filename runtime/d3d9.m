// d3d9.m - Direct3D 9 on Metal.
//
// COM objects live in guest memory: [vtable][host index]. Vtables are arrays of host-function addresses
// (host_fn), so the game's `call [eax+N]` lands in the C below with `this` as the first stack argument.
// Methods not implemented yet stop the run with their name (the to-do list, like imports).
//
// Resources keep their contents in guest memory (gmalloc), so Lock/Unlock hand out plain guest pointers;
// the renderer uploads dirty resources to Metal when it draws. This file has the object model, the device
// state, and Clear/Present on a CAMetalLayer; draw calls are recorded (counted) until shaders are wired in.
#include <stdlib.h>

#include "d3d9_internal.h"

void *user32_metal_layer(uint32_t hwnd);
void user32_client_size(uint32_t hwnd, int *cw, int *ch);
void user32_screen_size(int *w, int *h);
void user32_script(uint64_t frame, uint64_t draws);

#define D3D_OK 0
#define D3DERR_NOTAVAILABLE 0x8876086Au
#define D3DERR_INVALIDCALL 0x8876086Cu
#define E_NOINTERFACE 0x80004002u
#define E_NOTIMPL 0x80004001u
#define S_FALSE 1u

// ---------------- objects ----------------
static Obj **objs;
static unsigned nobjs, capobjs;

static uint32_t vt_d3d, vt_device, vt_texture, vt_cube, vt_volume, vt_surface, vt_vb, vt_ib, vt_decl, vt_vs, vt_ps, vt_sb, vt_query, vt_swap;

static uint32_t *free_slots;                          // objs[] indices of destroyed objects
static unsigned nfree_slots;
static Obj *new_obj(int kind, uint32_t vtable) {
    uint32_t i;
    if (nfree_slots) i = free_slots[--nfree_slots];
    else {
        if (nobjs == capobjs) { capobjs = capobjs ? capobjs * 2 : 1024; objs = realloc(objs, capobjs * sizeof *objs); free_slots = realloc(free_slots, capobjs * sizeof *free_slots); }
        i = nobjs++;
    }
    Obj *o = calloc(1, sizeof *o);
    o->kind = kind;
    o->refs = 1;
    o->guest = gmalloc(16);
    M32(o->guest) = vtable;
    M32(o->guest + 4) = i;
    M32(o->guest + 8) = 0x0D3D0B1E;   // tag for sanity checks
    objs[i] = o;
    return o;
}
Obj *obj(uint32_t g) {
    if (!g || M32(g + 8) != 0x0D3D0B1E) return NULL;
    uint32_t i = M32(g + 4);
    Obj *o = i < nobjs ? objs[i] : NULL;
    return o && o->guest == g ? o : NULL;
}
// A resource whose count reached 0 has its memory freed at once (obj_release); the object itself (host Obj, guest
// COM object, cached level surfaces) is destroyed once 1024 more resources have been released, unless something re-bound it meanwhile,
// so a late use of a just-released resource still finds an (empty) object. Before, objects were never destroyed:
// the game creates ~130 vertex/index buffers a second in races, ~1 KB of host memory and 32 bytes of guest each.
static void obj_destroy(Obj *o) {
    for (int k = 0; k < 16 * 6; ++k) { Obj *s = obj(o->surfaces[k]); if (s && s->parent == o->guest) obj_destroy(s); }
    render_release(o);
    for (int k = 0; k < 16 * 6; ++k) gfree(o->mem[k]);
    uint32_t i = M32(o->guest + 4);
    M32(o->guest + 8) = 0;
    gfree(o->guest);
    objs[i] = NULL;
    free_slots[nfree_slots++] = i;
    free(o->tokens);
    free(o);
}
static void obj_retire(Obj *o) {
    static uint32_t quarantine[1024];
    static unsigned head;
    uint32_t old = quarantine[head];
    quarantine[head] = o->guest;
    head = (head + 1) % 1024;
    Obj *x = obj(old);
    if (x && x->refs <= 0 && x->guest != dev.backbuffer && x->guest != dev.autodepth) obj_destroy(x);
}
// diag mem.log (diag.c): the guest's D3D objects by kind. live: refs > 0; retired: released, waiting in obj_retire's
// quarantine; gpu: with a Metal copy (render.m); bytes: their guest memory (mem[]); hd: X360 HD swaps among live textures.
void d3d9_obj_stats(uint32_t live[16], uint32_t retired[16], uint32_t gpu[16], uint64_t bytes[16], uint32_t *hd) {
    memset(live, 0, 16 * 4); memset(retired, 0, 16 * 4); memset(gpu, 0, 16 * 4); memset(bytes, 0, 16 * 8); *hd = 0;
    for (unsigned i = 0; i < nobjs; ++i) {
        Obj *o = objs[i];
        if (!o || o->kind < 0 || o->kind >= 16) continue;
        if (o->kind == K_SURFACE && o->parent) continue;              // texture levels: counted with their texture
        if (o->refs <= 0) { ++retired[o->kind]; continue; }
        ++live[o->kind];
        if (o->gpu) ++gpu[o->kind];
        if (o->hd_swap) ++*hd;
        if (o->kind == K_VB || o->kind == K_IB) { if (o->mem[0]) bytes[o->kind] += o->size; continue; }
        int faces = o->kind == K_CUBE ? 6 : 1;
        for (uint32_t l = 0; l < o->levels && l < 16; ++l)
            for (int f = 0; f < faces; ++f)
                if (o->mem[f * 16 + l]) bytes[o->kind] += (uint64_t)o->pitch[l] * level_rows(o->fmt, mipdim(o->h, l)) * (o->kind == K_VOLUME ? mipdim(o->d, l) : 1);
    }
}
#define THIS obj(arg(c, 0))

// ---------------- formats ----------------
int is_dxt(uint32_t f) { return f == 0x31545844 || f == 0x32545844 || f == 0x33545844 || f == 0x34545844 || f == 0x35545844; }
uint32_t fmt_bpp(uint32_t f) {      // bytes per pixel (per 4x4 block for DXT)
    switch (f) {
    case 0x31545844: return 8;
    case 0x32545844: case 0x33545844: case 0x34545844: case 0x35545844: return 16;
    case 20: return 3;
    case 21: case 22: case 31: case 32: case 33: case 34: case 35: case 62: case 63: case 64: case 67:
    case 71: case 75: case 77: case 79: case 82: case 83: case 112: case 114: case 102: return 4;
    case 23: case 24: case 25: case 26: case 29: case 30: case 40: case 51: case 60: case 61:
    case 70: case 73: case 80: case 81: case 111: case 101: return 2;
    case 27: case 28: case 41: case 50: case 52: return 1;
    case 36: case 113: case 115: return 8;
    case 116: return 16;
    }
    return 4;
}
static uint32_t level_pitch(uint32_t f, uint32_t w) { return is_dxt(f) ? ((w + 3) / 4) * fmt_bpp(f) : w * fmt_bpp(f); }
uint32_t level_rows(uint32_t f, uint32_t h) { return is_dxt(f) ? (h + 3) / 4 : h; }
uint32_t mipdim(uint32_t v, uint32_t l) { v >>= l; return v ? v : 1; }

// ---------------- device state ----------------
Device dev;
id<MTLDevice> mtl;
id<MTLCommandQueue> queue;
CAMetalLayer *layer;
CGColorSpaceRef srgb_colorspace(void) {           // the frames' colour space (layer tags; kept for the process)
    static CGColorSpaceRef cs;
    if (!cs) cs = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    return cs;
}

// ---------------- vtable plumbing ----------------
typedef struct { const char *name; HostFn fn; } Impl;
static int unimplemented(Cpu *c) { (void)c; return 0; }   // dispatch() then dies with the method's name
static uint32_t make_vtable(const char *iface, const char *const *names, int n, const Impl *impl) {
    uint32_t vt = galloc(4 * (uint32_t)n);
    for (int i = 0; i < n; ++i) {
        char *full = malloc(strlen(iface) + strlen(names[i]) + 3);
        sprintf(full, "%s::%s", iface, names[i]);
        HostFn fn = unimplemented;
        for (const Impl *p = impl; p->name; ++p) if (!strcmp(p->name, names[i])) fn = p->fn;
        M32(vt + 4 * i) = host_fn(fn, full);
    }
    return vt;
}

// IUnknown + IDirect3DResource9 basics shared by every object
static int m_QueryInterface(Cpu *c) { if (arg(c, 2)) M32(arg(c, 2)) = 0; RET(E_NOINTERFACE, 3); }
// Reference counting as in D3D9: a texture's surfaces share the texture's count, and the device holds a
// reference to everything bound to it (dev_bind), so a resource the game releases while it's still bound lives on.
static Obj *ref_owner(Obj *o) { return o && o->kind == K_SURFACE && o->parent ? obj(o->parent) : o; }
static uint32_t obj_addref(Obj *o) { o = ref_owner(o); return o ? (uint32_t)++o->refs : 0; }
static uint32_t obj_release(Obj *o) {
    o = ref_owner(o);
    if (!o) return 0;
    if (--o->refs == 0 && o != dev.self && (o->kind == K_TEXTURE || o->kind == K_CUBE || o->kind == K_VOLUME || o->kind == K_VB ||
                                            o->kind == K_IB || (o->kind == K_SURFACE && !o->parent))) {
        if (o->guest != dev.backbuffer && o->guest != dev.autodepth) {
            if (diag_on) diag_event("d3d free %08X kind %d fmt %X %ux%u", o->guest, o->kind, o->fmt, o->w, o->h);
            for (int i = 0; i < 16 * 6; ++i) { gfree(o->mem[i]); o->mem[i] = 0; }
            render_release(o);
            obj_retire(o);
        }
    }
    return o->refs < 0 ? 0 : (uint32_t)o->refs;
}
static void dev_bind(uint32_t *slot, uint32_t g) {
    if (*slot == g) return;
    if (g) obj_addref(obj(g));
    uint32_t old = *slot;
    *slot = g;
    if (old) obj_release(obj(old));
}
static int m_AddRef(Cpu *c) { Obj *o = THIS; RET(o ? obj_addref(o) : 1, 1); }
static int m_Release(Cpu *c) { Obj *o = THIS; RET(o ? obj_release(o) : 0, 1); }
static int m_GetDevice(Cpu *c) { M32(arg(c, 1)) = dev.self ? dev.self->guest : 0; if (dev.self) ++dev.self->refs; RET(D3D_OK, 2); }
static int m_SetPrivateData(Cpu *c) { RET(D3D_OK, 5); }
static int m_GetPrivateData(Cpu *c) { RET(0x88760866u, 4); }   // D3DERR_NOTFOUND
static int m_FreePrivateData(Cpu *c) { RET(D3D_OK, 2); }
static int m_SetPriority(Cpu *c) { RET(0, 2); }
static int m_GetPriority(Cpu *c) { RET(0, 1); }
static int m_PreLoad(Cpu *c) { RET(0, 1); }
static int m_GetType(Cpu *c) {
    Obj *o = THIS;
    static const uint32_t t[] = {[K_SURFACE] = 1, [K_VOLUME] = 4, [K_TEXTURE] = 3, [K_CUBE] = 5, [K_VB] = 6, [K_IB] = 7};
    RET(o && o->kind < (int)(sizeof t / sizeof *t) ? t[o->kind] : 0, 1);
}
#define RESOURCE_IMPL \
    {"QueryInterface", m_QueryInterface}, {"AddRef", m_AddRef}, {"Release", m_Release}, {"GetDevice", m_GetDevice}, \
    {"SetPrivateData", m_SetPrivateData}, {"GetPrivateData", m_GetPrivateData}, {"FreePrivateData", m_FreePrivateData}, \
    {"SetPriority", m_SetPriority}, {"GetPriority", m_GetPriority}, {"PreLoad", m_PreLoad}, {"GetType", m_GetType}
#define RESOURCE_NAMES "QueryInterface", "AddRef", "Release", "GetDevice", "SetPrivateData", "GetPrivateData", \
    "FreePrivateData", "SetPriority", "GetPriority", "PreLoad", "GetType"

// ---------------- surfaces ----------------
static Obj *make_surface(uint32_t w, uint32_t h, uint32_t fmt, uint32_t usage, uint32_t pool, int alloc) {
    Obj *s = new_obj(K_SURFACE, vt_surface);
    s->w = w; s->h = h; s->fmt = fmt; s->usage = usage; s->pool = pool; s->levels = 1;
    s->pitch[0] = level_pitch(fmt, w);
    if (alloc) s->mem[0] = gmalloc(s->pitch[0] * level_rows(fmt, h));
    return s;
}
static void write_desc(uint32_t p, uint32_t fmt, uint32_t type, uint32_t usage, uint32_t pool, uint32_t w, uint32_t h) {
    M32(p) = fmt; M32(p + 4) = type; M32(p + 8) = usage; M32(p + 12) = pool;
    M32(p + 16) = 0; M32(p + 20) = 0; M32(p + 24) = w; M32(p + 28) = h;
}
static uint32_t surface_mem(Obj *s, uint32_t *pitch) {
    if (s->parent) {
        Obj *t = obj(s->parent);
        *pitch = t->pitch[s->level];
        return t->mem[s->face * 16 + s->level];
    }
    if (!s->mem[0]) s->mem[0] = gmalloc(s->pitch[0] * level_rows(s->fmt, s->h));   // render targets: on first lock
    *pitch = s->pitch[0];
    return s->mem[0];
}
static int s_GetContainer(Cpu *c) { Obj *s = THIS; M32(arg(c, 2)) = s->parent; if (s->parent) obj_addref(obj(s->parent)); RET(s->parent ? D3D_OK : E_NOINTERFACE, 3); }
static int s_GetDesc(Cpu *c) {
    Obj *s = THIS;
    write_desc(arg(c, 1), s->fmt, 1, s->usage, s->pool, s->w, s->h);
    M32(arg(c, 1) + 16) = s->ms_type; M32(arg(c, 1) + 20) = s->ms_quality;
    RET(D3D_OK, 2);
}
static int s_LockRect(Cpu *c) {        // (this, D3DLOCKED_RECT*, RECT*, flags)
    Obj *s = THIS;
    uint32_t pitch, m = surface_mem(s, &pitch), r = arg(c, 2), lr = arg(c, 1);
    uint32_t off = 0;
    if (r) off = (is_dxt(s->fmt) ? M32(r + 4) / 4 : M32(r + 4)) * pitch + (is_dxt(s->fmt) ? M32(r) / 4 : M32(r)) * fmt_bpp(s->fmt);
    M32(lr) = pitch;
    M32(lr + 4) = m + off;
    RET(D3D_OK, 4);
}
static int s_UnlockRect(Cpu *c) { Obj *s = THIS; if (s->parent) obj(s->parent)->dirty = 1; else s->dirty = 1; RET(D3D_OK, 1); }
static int s_GetDC(Cpu *c) { M32(arg(c, 1)) = 0x00020000; RET(D3D_OK, 2); }
static int s_ReleaseDC(Cpu *c) { RET(D3D_OK, 2); }

// ---------------- textures ----------------
static int t_SetLOD(Cpu *c) { RET(0, 2); }
static int t_GetLOD(Cpu *c) { RET(0, 1); }
static int t_GetLevelCount(Cpu *c) { RET(THIS->levels, 1); }
static int t_SetAutoGenFilterType(Cpu *c) { RET(D3D_OK, 2); }
static int t_GetAutoGenFilterType(Cpu *c) { RET(2, 1); }
static int t_GenerateMipSubLevels(Cpu *c) { RET(0, 1); }
static int t_GetLevelDesc(Cpu *c) {
    Obj *t = THIS;
    uint32_t l = arg(c, 1);
    if (l >= t->levels) RET(D3DERR_INVALIDCALL, 3);
    write_desc(arg(c, 2), t->fmt, 1, t->usage, t->pool, mipdim(t->w, l), mipdim(t->h, l));
    RET(D3D_OK, 3);
}
static uint32_t texture_surface(Obj *t, uint32_t face, uint32_t l) {
    uint32_t k = face * 16 + l;
    if (!t->surfaces[k]) {
        Obj *s = make_surface(mipdim(t->w, l), mipdim(t->h, l), t->fmt, t->usage, t->pool, 0);
        s->parent = t->guest; s->level = l; s->face = face;
        t->surfaces[k] = s->guest;
    }
    obj_addref(obj(t->surfaces[k]));
    return t->surfaces[k];
}
static int t_GetSurfaceLevel(Cpu *c) {
    Obj *t = THIS;
    if (arg(c, 1) >= t->levels) RET(D3DERR_INVALIDCALL, 3);
    M32(arg(c, 2)) = texture_surface(t, 0, arg(c, 1));
    RET(D3D_OK, 3);
}
static void lock_level(Obj *t, uint32_t face, uint32_t l, uint32_t lr, uint32_t r) {
    uint32_t pitch = t->pitch[l], m = t->mem[face * 16 + l], off = 0;
    if (r) off = (is_dxt(t->fmt) ? M32(r + 4) / 4 : M32(r + 4)) * pitch + (is_dxt(t->fmt) ? M32(r) / 4 : M32(r)) * fmt_bpp(t->fmt);
    M32(lr) = pitch;
    M32(lr + 4) = m + off;
}
static int t_LockRect(Cpu *c) {        // (this, level, locked*, rect*, flags)
    Obj *t = THIS;
    if (arg(c, 1) >= t->levels) RET(D3DERR_INVALIDCALL, 5);
    lock_level(t, 0, arg(c, 1), arg(c, 2), arg(c, 3));
    RET(D3D_OK, 5);
}
static int t_UnlockRect(Cpu *c) { THIS->dirty = 1; RET(D3D_OK, 2); }
static int t_AddDirtyRect(Cpu *c) { THIS->dirty = 1; RET(D3D_OK, 2); }
static int cube_GetCubeMapSurface(Cpu *c) {     // (this, face, level, pp)
    Obj *t = THIS;
    if (arg(c, 2) >= t->levels || arg(c, 1) > 5) RET(D3DERR_INVALIDCALL, 4);
    M32(arg(c, 3)) = texture_surface(t, arg(c, 1), arg(c, 2));
    RET(D3D_OK, 4);
}
static int cube_LockRect(Cpu *c) {     // (this, face, level, locked*, rect*, flags)
    Obj *t = THIS;
    if (arg(c, 2) >= t->levels || arg(c, 1) > 5) RET(D3DERR_INVALIDCALL, 6);
    lock_level(t, arg(c, 1), arg(c, 2), arg(c, 3), arg(c, 4));
    RET(D3D_OK, 6);
}
static int cube_UnlockRect(Cpu *c) { THIS->dirty = 1; RET(D3D_OK, 3); }
static int cube_AddDirtyRect(Cpu *c) { THIS->dirty = 1; RET(D3D_OK, 3); }
static int vol_LockBox(Cpu *c) {       // (this, level, D3DLOCKED_BOX*, box*, flags)
    Obj *t = THIS;
    uint32_t l = arg(c, 1), lb = arg(c, 2);
    if (l >= t->levels) RET(D3DERR_INVALIDCALL, 5);
    M32(lb) = t->pitch[l];
    M32(lb + 4) = t->pitch[l] * level_rows(t->fmt, mipdim(t->h, l));
    M32(lb + 8) = t->mem[l];
    RET(D3D_OK, 5);
}
static int vol_UnlockBox(Cpu *c) { THIS->dirty = 1; RET(D3D_OK, 2); }
static int vol_GetLevelDesc(Cpu *c) {  // D3DVOLUME_DESC: Format, Type, Usage, Pool, Width, Height, Depth
    Obj *t = THIS;
    uint32_t l = arg(c, 1), p = arg(c, 2);
    M32(p) = t->fmt; M32(p + 4) = 2; M32(p + 8) = t->usage; M32(p + 12) = t->pool;
    M32(p + 16) = mipdim(t->w, l); M32(p + 20) = mipdim(t->h, l); M32(p + 24) = mipdim(t->d, l);
    RET(D3D_OK, 3);
}

// ---------------- buffers ----------------
static int b_Lock(Cpu *c) {            // (this, offset, size, ppData, flags)
    Obj *b = THIS;
    M32(arg(c, 3)) = b->mem[0] + arg(c, 1);
    RET(D3D_OK, 5);
}
static int b_Unlock(Cpu *c) { THIS->dirty = 1; RET(D3D_OK, 1); }
static int vb_GetDesc(Cpu *c) {        // Format, Type, Usage, Pool, Size, FVF
    Obj *b = THIS;
    uint32_t p = arg(c, 1);
    M32(p) = 100; M32(p + 4) = 6; M32(p + 8) = b->usage; M32(p + 12) = b->pool; M32(p + 16) = b->size; M32(p + 20) = b->fvf;
    RET(D3D_OK, 2);
}
static int ib_GetDesc(Cpu *c) {
    Obj *b = THIS;
    uint32_t p = arg(c, 1);
    M32(p) = b->fmt; M32(p + 4) = 7; M32(p + 8) = b->usage; M32(p + 12) = b->pool; M32(p + 16) = b->size;
    RET(D3D_OK, 2);
}

// ---------------- shaders, declarations, state blocks, queries ----------------
static int sh_GetFunction(Cpu *c) {    // (this, data*, size*)
    Obj *s = THIS;
    if (arg(c, 1)) memcpy(G + arg(c, 1), s->tokens, 4 * s->ntokens);
    M32(arg(c, 2)) = 4 * s->ntokens;
    RET(D3D_OK, 3);
}
static int decl_GetDeclaration(Cpu *c) {   // (this, elements*, num*)
    Obj *d = THIS;
    if (arg(c, 1)) memcpy(G + arg(c, 1), G + d->decl_mem, 8 * d->decl_n);
    M32(arg(c, 2)) = d->decl_n;
    RET(D3D_OK, 3);
}
static int sb_Capture(Cpu *c) { RET(D3D_OK, 1); }
static int sb_Apply(Cpu *c) { RET(D3D_OK, 1); }
static int q_GetType(Cpu *c) { RET(THIS->qtype, 1); }
static int q_GetDataSize(Cpu *c) { RET(4, 1); }   // EVENT 8: BOOL, OCCLUSION 9: DWORD pixels
static int q_Issue(Cpu *c) {           // (this, flags)
    Obj *q = THIS;
    if (q && q->qtype == 9) render_query_issue(q, arg(c, 1));
    RET(D3D_OK, 2);
}
static int q_GetData(Cpu *c) {         // (this, data*, size, flags)
    Obj *q = THIS;
    if (q && q->qtype == 9) {          // OCCLUSION: visible pixels once its frame has run on the GPU (render_query.m)
        uint32_t px;
        if (!render_query_result(q, &px)) RET(S_FALSE, 4);
        if (arg(c, 1) && arg(c, 2) >= 4) M32(arg(c, 1)) = px;
        RET(D3D_OK, 4);
    }
    if (arg(c, 1) && arg(c, 2)) {      // EVENT: everything has finished already
        memset(G + arg(c, 1), 0, arg(c, 2));
        if (q && q->qtype == 8) M32(arg(c, 1)) = 1;
    }
    RET(D3D_OK, 4);
}

// ---------------- IDirect3D9 ----------------
static void fill_mode(uint32_t p, uint32_t w, uint32_t h, uint32_t fmt) { M32(p) = w; M32(p + 4) = h; M32(p + 8) = 60; M32(p + 12) = fmt; }
static const uint32_t modes[][2] = {{640, 480}, {800, 600}, {1024, 768}, {1280, 720}, {1280, 960}, {1280, 1024}, {1360, 768},
                                    {1600, 900}, {1600, 1200}, {1920, 1080}, {1920, 1200}, {2560, 1440}};
// The adapter's modes: the common ones above plus widescreen.c's six slots (which follow the display), so the game
// finds every slot it offers among them.
static uint32_t adapter_modes[sizeof modes / sizeof *modes + 6][2], nmodes_;
static uint32_t nmodes(void) {
    if (!nmodes_) {
        for (unsigned i = 0; i < sizeof modes / sizeof *modes; ++i) { adapter_modes[nmodes_][0] = modes[i][0]; adapter_modes[nmodes_++][1] = modes[i][1]; }
        for (int s = 0; s < 6; ++s) {
            uint32_t w, h, k = 0;
            widescreen_mode(s, &w, &h);
            while (k < nmodes_ && (adapter_modes[k][0] != w || adapter_modes[k][1] != h)) ++k;
            if (w && k == nmodes_) { adapter_modes[nmodes_][0] = w; adapter_modes[nmodes_++][1] = h; }
        }
    }
    return nmodes_;
}
#define NMODES nmodes()
static void mode_at(uint32_t i, uint32_t *w, uint32_t *h) { *w = adapter_modes[i][0]; *h = adapter_modes[i][1]; }
static int fmt_is_display(uint32_t f) { return f == 22 || f == 23 || f == 21; }

static void fill_caps(uint32_t p) {
    memset(G + p, 0, 304);
    M32(p + 0) = 1;                    // D3DDEVTYPE_HAL
    M32(p + 8) = 0x00020000;           // Caps: D3DCAPS_READ_SCANLINE
    M32(p + 12) = 0xE0020000;          // Caps2: FULLSCREENGAMMA | CANAUTOGENMIPMAP | DYNAMICTEXTURES | CANMANAGERESOURCE
    M32(p + 16) = 0x000003A0;          // Caps3: ALPHA_FULLSCREEN_FLIP_OR_DISCARD | COPY_TO_VIDMEM | COPY_TO_SYSTEMMEM | LINEAR_TO_SRGB
    M32(p + 20) = 0x8000000F;          // PresentationIntervals: IMMEDIATE | ONE..FOUR
    M32(p + 24) = 1;                   // CursorCaps: COLOR
    M32(p + 28) = 0x001BBEF0;          // DevCaps: hardware T&L, pure device, draw prim2, etc.
    M32(p + 32) = 0x000EFEF2;          // PrimitiveMiscCaps
    M32(p + 36) = 0x07732191;          // RasterCaps: dither, zbias, fog, aniso, scissor test, slope scale bias, depth bias
    M32(p + 40) = 0xFF;                // ZCmpCaps
    M32(p + 44) = 0x3FFF; M32(p + 48) = 0x3FFF;   // Src/DestBlendCaps
    M32(p + 52) = 0xFF;                // AlphaCmpCaps
    M32(p + 56) = 0x00084208;          // ShadeCaps
    M32(p + 60) = 0x0001EC45;          // TextureCaps: perspective, alpha, cubemap, mipmap, volume, projected, mipcube, mipvolume
    M32(p + 64) = 0x03030700; M32(p + 68) = 0x03030300; M32(p + 72) = 0x03030300;   // filter caps
    M32(p + 76) = 0x3F; M32(p + 80) = 0x3F;   // address caps
    M32(p + 84) = 0x1F;                // LineCaps
    M32(p + 88) = 4096; M32(p + 92) = 4096; M32(p + 96) = 512;
    M32(p + 100) = 8192; M32(p + 104) = 4096; M32(p + 108) = 16;
    MF32(p + 112) = 1e10f;
    MF32(p + 116) = -1e8f; MF32(p + 120) = -1e8f; MF32(p + 124) = 1e8f; MF32(p + 128) = 1e8f;
    M32(p + 136) = 0x1FF;              // StencilCaps
    M32(p + 140) = 0x00180008;         // FVFCaps: 8 texcoords, PSIZE
    M32(p + 144) = 0x03FFFFFF;         // TextureOpCaps
    M32(p + 148) = 8; M32(p + 152) = 8;
    M32(p + 156) = 0x0000017B;         // VertexProcessingCaps
    M32(p + 160) = 8; M32(p + 164) = 8; M32(p + 168) = 4; M32(p + 172) = 0;
    MF32(p + 176) = 64.0f;
    M32(p + 180) = 0x00FFFFFF; M32(p + 184) = 0x00FFFFFF; M32(p + 188) = 16; M32(p + 192) = 255;
    M32(p + 196) = 0xFFFE0000u | 3 << 8;   // vs_3_0 (D3DVS_VERSION(3, 0))
    M32(p + 200) = 256;
    M32(p + 204) = 0xFFFF0000u | 3 << 8;   // ps_3_0 (D3DPS_VERSION(3, 0))
    MF32(p + 208) = 65504.0f;
    M32(p + 212) = 0x00000051;         // DevCaps2: STREAMOFFSET | VERTEXELEMENTSCANSHARESTREAMOFFSET | CAN_STRETCHRECT_FROM_TEXTURES
    MF32(p + 216) = 0;
    M32(p + 228) = 0; M32(p + 232) = 1;
    M32(p + 236) = 0x0000030F;         // DeclTypes: UBYTE4, UBYTE4N, SHORT2N.., FLOAT16_2/4
    M32(p + 240) = 4;                  // NumSimultaneousRTs
    M32(p + 244) = 0x03000300;         // StretchRectFilterCaps
    M32(p + 248) = 1; M32(p + 252) = 24; M32(p + 256) = 32; M32(p + 260) = 4;           // VS20Caps
    M32(p + 264) = 0x1F; M32(p + 268) = 24; M32(p + 272) = 32; M32(p + 276) = 4; M32(p + 280) = 512;  // PS20Caps
    M32(p + 284) = 0x00000300;
    M32(p + 288) = 65535; M32(p + 292) = 65535; M32(p + 296) = 512; M32(p + 300) = 512;
}

static int d_GetAdapterCount(Cpu *c) { RET(1, 1); }
static int d_GetAdapterIdentifier(Cpu *c) {   // (this, adapter, flags, ident*)
    uint32_t p = arg(c, 3);
    memset(G + p, 0, 1100);
    strcpy((char *)G + p, "nv4_disp.dll");
    strcpy((char *)G + p + 512, "NVIDIA GeForce 7800 GTX");
    strcpy((char *)G + p + 1024, "\\\\.\\DISPLAY1");
    M64(p + 1056) = 0x0006000E000A0D91ull;                     // 6.14.10.9361 (ForceWare 93.61)
    M32(p + 1064) = 0x10DE; M32(p + 1068) = 0x0091; M32(p + 1072) = 0; M32(p + 1076) = 0xA1;
    M32(p + 1096) = 1;
    RET(D3D_OK, 4);
}
static int d_GetAdapterModeCount(Cpu *c) { RET(fmt_is_display(arg(c, 2)) ? NMODES : 0, 3); }
static int d_EnumAdapterModes(Cpu *c) {        // (this, adapter, fmt, mode, pMode)
    if (!fmt_is_display(arg(c, 2)) || arg(c, 3) >= NMODES) RET(D3DERR_INVALIDCALL, 5);
    uint32_t w, h;
    mode_at(arg(c, 3), &w, &h);
    fill_mode(arg(c, 4), w, h, arg(c, 2));
    RET(D3D_OK, 5);
}
static int d_GetAdapterDisplayMode(Cpu *c) {
    int w, h;
    user32_screen_size(&w, &h);
    fill_mode(arg(c, 2), (uint32_t)w, (uint32_t)h, 22);
    RET(D3D_OK, 3);
}
static int d_CheckDeviceType(Cpu *c) { RET(fmt_is_display(arg(c, 3)) ? D3D_OK : D3DERR_NOTAVAILABLE, 6); }
static int d_CheckDeviceFormat(Cpu *c) {       // (this, adapter, devtype, adapterFmt, usage, rtype, checkFmt)
    uint32_t usage = arg(c, 4), rtype = arg(c, 5), f = arg(c, 6);
    int depth = f == 70 || f == 71 || f == 73 || f == 75 || f == 77 || f == 79 || f == 80 || f == 82 || f == 83;
    // depth surfaces; depth textures (hardware shadow maps, which the game enables on NVIDIA when D24S8 passes) for
    // D24S8 / D24X8 / D16: rendered to as depth targets and sampled with a compare sampler (render.m)
    if (depth) RET((usage & 2) && (rtype == 1 || (rtype == 3 && (f == 75 || f == 77 || f == 80))) ? D3D_OK : D3DERR_NOTAVAILABLE, 7);
    static const uint32_t ok[] = {21, 22, 23, 24, 25, 26, 28, 32, 33, 34, 36, 50, 51, 60, 64, 111, 112, 113, 114, 115, 116,
                                  0x31545844, 0x32545844, 0x33545844, 0x34545844, 0x35545844};
    for (unsigned i = 0; i < sizeof ok / sizeof *ok; ++i)
        if (ok[i] == f) {
            if ((usage & 1) && is_dxt(f)) RET(D3DERR_NOTAVAILABLE, 7);   // no compressed render targets
            RET(D3D_OK, 7);
        }
    RET(D3DERR_NOTAVAILABLE, 7);
}
// (this, adapter, devtype, fmt, windowed, type, pQualityLevels). The game asks once for D3DMULTISAMPLE_NONMASKABLE
// with X8R8G8B8 (FUN_006c1510, call at 006C16CD) and offers Antialiasing levels 0..quality-1;
// level n is created as NONMASKABLE with quality n (__init_window), so quality n maps to the n-th sample count Metal
// supports (render_msaa_count). Explicit sample counts (D3DMULTISAMPLE_2_SAMPLES..) answer for what Metal has.
static int d_CheckDeviceMultiSampleType(Cpu *c) {
    uint32_t type = arg(c, 5), q = arg(c, 6), counts[8], n = render_msaa_levels(counts);
    uint32_t ok = type == 0 || (type == 1 && n) || render_msaa_count(type, 0) == type;
    if (q) M32(q) = type == 1 ? n + 1 : 1;
    if (type == 1) {
        char l[64] = "";
        for (uint32_t i = 0; i < n; ++i) snprintf(l + strlen(l), sizeof l - strlen(l), " %u=%ux", i + 1, counts[i]);
        fprintf(stderr, "boot: D3D9 antialiasing levels (g_FSAALevel): 0=off%s\n", l);
    }
    RET(ok ? D3D_OK : D3DERR_NOTAVAILABLE, 7);
}
static int d_CheckDepthStencilMatch(Cpu *c) { RET(D3D_OK, 6); }
static int d_CheckDeviceFormatConversion(Cpu *c) { RET(D3D_OK, 5); }
static int d_GetDeviceCaps(Cpu *c) { fill_caps(arg(c, 3)); RET(D3D_OK, 4); }
static int d_GetAdapterMonitor(Cpu *c) { RET(0x00040000, 2); }

static void setup_swapchain(void) {
    uint32_t w = dev.pp[0], h = dev.pp[1];
    if (!w || !h) { int cw, ch; user32_client_size(dev.hwnd, &cw, &ch); w = (uint32_t)cw; h = (uint32_t)ch; dev.pp[0] = w; dev.pp[1] = h; }
    if (!dev.pp[2]) dev.pp[2] = 22;
    if (!dev.backbuffer) dev.backbuffer = make_surface(w, h, dev.pp[2], 1, 0, 0)->guest;
    else { Obj *b = obj(dev.backbuffer); b->w = w; b->h = h; b->pitch[0] = level_pitch(b->fmt, w); gfree(b->mem[0]); b->mem[0] = 0; }
    if (dev.pp[9]) {                   // EnableAutoDepthStencil
        if (!dev.autodepth) dev.autodepth = make_surface(w, h, dev.pp[10], 2, 0, 0)->guest;
        else { Obj *d = obj(dev.autodepth); d->w = w; d->h = h; d->fmt = dev.pp[10]; }
    }
    // MultiSampleType/Quality: the back buffer and the auto depth buffer are multisampled together (render_resize)
    uint32_t samples = render_msaa_count(dev.pp[4], dev.pp[5]);
    if (samples < 2) dev.pp[4] = dev.pp[5] = 0;
    uint32_t bd[2] = {dev.backbuffer, dev.autodepth};
    for (int k = 0; k < 2; ++k) { Obj *o = obj(bd[k]); if (o) { o->ms_type = dev.pp[4]; o->ms_quality = dev.pp[5]; o->samples = samples; } }
    dev.rt[0] = dev.backbuffer;
    dev.ds = dev.pp[9] ? dev.autodepth : 0;
    dev.viewport[0] = dev.viewport[1] = 0; dev.viewport[2] = w; dev.viewport[3] = h;
    float zmax = 1.0f; memcpy(&dev.viewport[5], &zmax, 4); dev.viewport[4] = 0;
    dev.scissor[0] = dev.scissor[1] = 0; dev.scissor[2] = w; dev.scissor[3] = h;
    if (layer) layer.drawableSize = CGSizeMake(w, h);
    fprintf(stderr, "boot: D3D9 swap chain %ux%u format %u, %s, depth %s (format %u), interval %X, MSAA %ux\n", w, h, dev.pp[2],
            dev.pp[8] ? "windowed" : "fullscreen", dev.pp[9] ? "auto" : "none", dev.pp[10], dev.pp[13], samples);
}

static void default_states(void) {
    memset(dev.rs, 0, sizeof dev.rs);
    dev.rs[7] = 1; dev.rs[8] = 3; dev.rs[9] = 2; dev.rs[14] = 1; dev.rs[15] = 0; dev.rs[19] = 2; dev.rs[20] = 1;
    dev.rs[22] = 3; dev.rs[23] = 4; dev.rs[25] = 8; dev.rs[27] = 0; dev.rs[137] = 1; dev.rs[168] = 0xF;
    for (int s = 0; s < 8; ++s) { dev.tss[s][1] = s ? 1 : 4; dev.tss[s][2] = 2; dev.tss[s][3] = 1; dev.tss[s][4] = s ? 1 : 2; dev.tss[s][5] = 2; dev.tss[s][6] = 1; dev.tss[s][11] = (uint32_t)s; }
    for (int s = 0; s < 20; ++s) { dev.ss[s][1] = dev.ss[s][2] = dev.ss[s][3] = 1; dev.ss[s][5] = dev.ss[s][6] = 1; dev.ss[s][10] = 1; }
}

// First run (no FirstTime saved, e.g. a fresh BOOT_USERDATA): the game skips its settings loader and detects the
// hardware instead, twice: inline in WinMain (performance level > 3: g_FSAALevel 1, g_RacingResolution 1, right
// before __getResolutionByIndex sizes the window and the device is created) and in Default__13VideoSettings once the
// device exists (FUN_006c1990, then saved). Both used to overwrite values given with BOOT_REG, so tests silently ran
// other settings. After each, the BOOT_REG values are applied again (boot.c), and the port's defaults replace what
// detection picked (4x MSAA, shadows 2, native resolution) unless BOOT_REG gives a value.
#define FSAA_LEVEL 0x00901808u           // g_FSAALevel (__multisamplequality)
#define RES_INDEX  0x0090181Cu           // g_RacingResolution (__resolution_index)
#define SHADOW_DETAIL 0x00901830u        // g_ShadowDetail
int widescreen_native_slot(void);        // widescreen.c
// F10 > Picture > Anti-aliasing (aa_setting.c): the same variable the Video options edit, applied as its Accept does.
int aa_game_levels(void) { return (int)render_msaa_levels(NULL); }
int aa_game_get(void) { return (int)M32(FSAA_LEVEL); }
void aa_game_apply(int level) {
    void boot_reg_set_dword(const char *name, uint32_t v);
    if ((uint32_t)level == M32(FSAA_LEVEL)) return;
    M32(FSAA_LEVEL) = (uint32_t)level;
    boot_reg_set_dword("g_FSAALevel", (uint32_t)level);
    G[0x00982C39u] = 1;                  // LoadedFlagMaybe: the next eDisplayFrame runs the full device Reset
}
void first_run_settings(Cpu *c, const char *where) {     // also from widescreen.c's __getResolutionByIndex (WinMain's call)
    int boot_reg_first_run(void), boot_reg_override(const char *name);
    void boot_reg_reapply(Cpu *c);
    if (!boot_reg_first_run()) return;
    uint32_t aa = M32(FSAA_LEVEL), res = M32(RES_INDEX);
    boot_reg_reapply(c);
                                                                                                                   
    // display's native resolution. A saved setting never gets here (the game's loader reads it instead).
    int native = widescreen_native_slot();
    if (!boot_reg_override("g_FSAALevel")) M32(FSAA_LEVEL) = 2;
    if (!boot_reg_override("g_ShadowDetail")) M32(SHADOW_DETAIL) = 2;
    if (native >= 0 && !boot_reg_override("g_RacingResolution")) M32(RES_INDEX) = (uint32_t)native;
    fprintf(stderr, "boot: first run, %s: detection picked g_FSAALevel %u g_RacingResolution %u, using %u %u, g_ShadowDetail %u (defaults: 4x MSAA, shadows 2, native resolution; BOOT_REG wins)\n", where, aa, res,
            M32(FSAA_LEVEL), M32(RES_INDEX), M32(SHADOW_DETAIL));
}
void f_006C1990_orig(Cpu *c);
void f_006C1990(Cpu *c) {                      // the video settings' hardware detection (default settings)
    f_006C1990_orig(c);
    first_run_settings(c, "video defaults");
}

static int d_CreateDevice(Cpu *c) {            // (this, adapter, type, hFocusWindow, behavior, pp*, ppDevice)
    uint32_t pp = arg(c, 5);
    memcpy(dev.pp, G + pp, sizeof dev.pp);
    dev.hwnd = dev.pp[7] ? dev.pp[7] : arg(c, 3);
    dev.behavior = arg(c, 4);
    // Real D3D9 sets the creating thread's x87 control word (0x7F: every exception masked, 24-bit precision) unless
    // D3DCREATE_FPU_PRESERVE (2). The game's CRT start-up leaves 0x232 (invalid, divide-by-zero and overflow unmasked),
    // and its pow() raised a hardware exception on a NaN flare intensity in the hood camera (RaiseException from
    // __raise_exc, depending on frame timing). The masks are applied; the precision stays as the port has it (53-bit).
    if (!(dev.behavior & 2)) c->cw |= 0x3F;
    dev.self = new_obj(K_DEVICE, vt_device);
    dev.t0 = host_ticks();
    mtl = MTLCreateSystemDefaultDevice();
    queue = [mtl newCommandQueue];
    layer = (__bridge CAMetalLayer *)user32_metal_layer(dev.hwnd);
    // The game's frames are sRGB (Rec.709 primaries, D65, sRGB curve; D3D9 output, the X360 composite works in the same
    // 8-bit gamma space): tagged, so macOS colour-matches them to the display (untagged, a P3 panel showed them in its
    // own wider gamut, oversaturated).
    if (layer) { layer.device = mtl; layer.pixelFormat = MTLPixelFormatBGRA8Unorm; layer.framebufferOnly = YES; layer.colorspace = srgb_colorspace(); }
    fprintf(stderr, "boot: D3D9 CreateDevice(hwnd %08X, behavior %X) on %s%s; antialiasing: level %u, menu maximum %u, MultiSample %u/%u\n", dev.hwnd, dev.behavior,
            mtl.name.UTF8String, layer ? "" : " (no window layer)", M32(0x00901808), M32(0x00901890), dev.pp[4], dev.pp[5]);
    default_states();
    setup_swapchain();
    render_init();
    memcpy(G + pp, dev.pp, sizeof dev.pp);           // the runtime fills in 0 sizes and formats
    for (int i = 0; i < 512; ++i) for (int k = 0; k < 16; ++k) dev.transform[i][k] = (k % 5 == 0) ? 1.0f : 0.0f;
    M32(arg(c, 6)) = dev.self->guest;
    RET(D3D_OK, 7);
}

// ---------------- IDirect3DDevice9 ----------------
static int v_TestCooperativeLevel(Cpu *c) { RET(D3D_OK, 1); }
static int v_GetAvailableTextureMem(Cpu *c) { RET(512u << 20, 1); }
static int v_EvictManagedResources(Cpu *c) { RET(D3D_OK, 1); }
static int v_GetDirect3D(Cpu *c) { M32(arg(c, 1)) = objs[0]->guest; RET(D3D_OK, 2); }
static int v_GetDeviceCaps(Cpu *c) { fill_caps(arg(c, 1)); RET(D3D_OK, 2); }
static int v_GetDisplayMode(Cpu *c) { fill_mode(arg(c, 2), dev.pp[0], dev.pp[1], 22); RET(D3D_OK, 3); }
static int v_GetCreationParameters(Cpu *c) {
    uint32_t p = arg(c, 1);
    M32(p) = 0; M32(p + 4) = 1; M32(p + 8) = dev.hwnd; M32(p + 12) = dev.behavior;
    RET(D3D_OK, 2);
}
static int v_SetCursorProperties(Cpu *c) { RET(D3D_OK, 4); }
static int v_SetCursorPosition(Cpu *c) { RET(0, 4); }
static int v_ShowCursor(Cpu *c) { RET(0, 2); }
static int v_GetNumberOfSwapChains(Cpu *c) { RET(1, 1); }
static int v_Reset(Cpu *c) {
    memcpy(dev.pp, G + arg(c, 1), sizeof dev.pp);
    setup_swapchain();
    render_resize();
    memcpy(G + arg(c, 1), dev.pp, sizeof dev.pp);
    default_states();
    RET(D3D_OK, 2);
}
uint32_t view_hash(void) {                     // diag: FNV of the current D3DTS_VIEW matrix
    uint32_t h = 2166136261u;
    const uint8_t *p = (const uint8_t *)dev.transform[2];
    for (int i = 0; i < 64; ++i) h = (h ^ p[i]) * 16777619u;
    return h;
}
static void present(void) {
    ++dev.frames;
    uint64_t now = host_ticks();
    if (dev.frames <= 3 || dev.frames % 300 == 0)
        fprintf(stderr, "boot: frame %llu, %llu draws this frame, %.1f s since CreateDevice\n",
                (unsigned long long)dev.frames, (unsigned long long)dev.frame_draws, (now - dev.t0) / 1e9);
    diag_frame(dev.frames, dev.frame_draws);
    uint64_t fd = dev.frame_draws;
    dev.frame_draws = 0;
    render_present();
    user32_script(dev.frames, fd);
}
static int v_Present(Cpu *c) {
    present();
    void x360_frame(void);
    x360_frame();                                           // X360 feature patches (x360.m)
    void gameplay_frame(Cpu *c);
    gameplay_frame(c);                                      // Gameplay options: heat action and lock (gameplay_opts.c)
    // BOOT_AUTODRIVE=1: the game's own AI drives the player's car (Game_ForceAIControl(0), 006123B0), so a
    // scripted run covers the whole course. It returns early when no player exists (front end).
    static int autodrive = -1;
    if (autodrive < 0) autodrive = getenv("BOOT_AUTODRIVE") != NULL;
    // Test only, NFS_TEST_BUST=1: the AI doesn't drive (the popup handling below still runs), so with BOOT_PURSUIT=2 the
    // car sits still until the cops bust it, and the game's own busted flow runs (no single entry point to force it: the
    // busted timer lives in AIPursuit::OnTask's state, 00443BA0, which the game rewrites each tick).
    static int hold_still = -1;
    if (hold_still < 0) hold_still = getenv("NFS_TEST_BUST") != NULL;
    // Every frame (a no-op once the AI drives, or with no player), but never while a NIS is loaded (INIS instance
    // 009885C8), so the car goes to the AI as soon as it exists, before a race intro NIS takes it: handing the car to the
    // AI (AIVehicleHuman::SetAiControl 004291C0) queues EEnableAIPhysics, whose handler (00638040) reloads the car's
    // behaviours with SuspensionSimple. When that lands after a race intro NIS took the car (SetAnimating(1) from
    // 006FC920, which gives it the NIS suspension behind INISCarControl), the NIS can no longer place the car
    // (AnimatedCars_SetMainAndWheels 0044B040 finds no INISCarControl, so SuspensionSpline::SetNISPosition never runs
    // SetVehicleOnGround) and the car stays frozen where QuickGame first spawned it, GManager's respawn location
    // (981.9 2148.4 153.0 in 15.1.1, World_DEBUGStartLocation runs before SetStartupRace). Every 60th frame used to
    // decide which came first: frame 120 against BeginGameplay at frame 119-135. The car is
    // created during loading (PVehicle at SetStartupRace, ~frame 100), BeginGameplay comes after loading, so the AI's
    // reload now always precedes the NIS (the order of every good run the refs were taken from); a car that only appears
    // with a NIS already loaded waits for the NIS to end.
    if (autodrive && !hold_still && !M32(0x009885C8)) {
        uint32_t esp = c->esp, a[1] = {0}; call_guest(c, 0x006123B0, 1, a); c->esp = esp;
    }
    // Result popups under BOOT_AUTODRIVE (README, "Into free roam"): the AI doesn't press Continue. When the FEng package
    // that has control (the one cFEng::FindPackageWithControl, 00516A90, returns) is a PostRace_* screen (Pursuit,
    // Results, MilestoneRewards), one RETURN goes 1.5 s after it took control (input focus), once per appearance. Not
    // with BOOT_RACE (race runs end by themselves and soak.sh counts on that).
    if (autodrive && !getenv("BOOT_RACE")) {
        static char popup[48];
        static uint64_t popup_since;
        static int popup_done;
        uint32_t fe = M32(0x0091CADC) ? M32(M32(0x0091CADC)) : 0, name = 0;   // cFEng::mInstance -> FEngine
        if (fe && fe != 0xFFFFFF28u)
            for (uint32_t p = M32(fe + 0xE4); p; p = M32(p + 8)) if (M32(p + 0x20)) { name = M32(p + 0xC); break; }
        static char last_ctl[48];
        if (diag_on && strncmp(last_ctl, name ? gptr(name) : "", sizeof last_ctl - 1)) {
            snprintf(last_ctl, sizeof last_ctl, "%s", name ? gptr(name) : "");
            diag_event("autodrive: package in control %s", last_ctl);
        }
        // The busted flow: Infractions.fng, and the generic Dialog.fng popups (first-time tutorial, impound) and Car_Select.fng (the car to go on with) while it runs.
        static int busted_flow;
        if (name && !strcasecmp(gptr(name), "Infractions.fng")) busted_flow = 1;
        else if (!name) busted_flow = 0;
        const char *n = name && (!strncasecmp(gptr(name), "PostRace_", 9) || !strcasecmp(gptr(name), "Infractions.fng") ||
                                 (busted_flow && (!strcasecmp(gptr(name), "Dialog.fng") || !strcasecmp(gptr(name), "Car_Select.fng")))) ? gptr(name) : NULL;
        if (!n) popup[0] = 0;
        else if (strncmp(popup, n, sizeof popup - 1)) {
            snprintf(popup, sizeof popup, "%s", n); popup_since = host_ticks(); popup_done = 0;
        } else if (!popup_done && host_ticks() - popup_since >= 1500000000ull) {
            void user32_autoreturn(void);
            user32_autoreturn(); popup_done = 1;
            fprintf(stderr, "boot: autodrive dismissed %s\n", popup);
            if (diag_on) diag_event("autodrive dismissed %s", popup);
        }
    }
    // BOOT_PURSUIT=<heat>: 10 s after the game-flow state reaches 6 (free roam or a race), the game's own script
    // functions raise the player's heat (Game_SetWorldHeat, 00612660) and start a pursuit (Game_ForcePursuitStart,
    // 0060AAC0), and again every minute, so a scripted run keeps cops at that heat level (capped by the career).
    // Real time, so it doesn't depend on the frame rate.
    static float pursuit_heat = -1;
    static uint64_t racing_since, next_pursuit;
    if (pursuit_heat < 0) pursuit_heat = getenv("BOOT_PURSUIT") ? (float)atof(getenv("BOOT_PURSUIT")) : 0;
    if (pursuit_heat > 0) {
        uint64_t now = host_ticks();
        if (M32(0x00925E90) != 6) racing_since = 0;
        else if (!racing_since) { racing_since = now; next_pursuit = now + 10000000000ull; }
        if (racing_since && now >= next_pursuit) {
            next_pursuit = now + 60000000000ull;
            uint32_t esp = c->esp, heat[1], none[1] = {0};
            memcpy(heat, &pursuit_heat, 4);
            if (getenv("BOOT_RACE_COPS")) { uint32_t one[1] = {1}; call_guest(c, 0x00604F40, 1, one); }   // Game_SetCopsEnabled(true): test, cops in a BOOT_RACE
            call_guest(c, 0x00612660, 1, heat);
            call_guest(c, 0x0060AAC0, 1, none);
            c->esp = esp;
            fprintf(stderr, "boot: pursuit started at heat %.1f\n", pursuit_heat);
            if (diag_on) diag_event("pursuit started at heat %.1f", pursuit_heat);
        }
    }
    void pacing_present(void);
    pacing_present();                                       // one frame per sim tick (pacing.c)
    gil_yield(c);
    RET(D3D_OK, 5);
}
static int v_GetBackBuffer(Cpu *c) {           // (this, iSwap, i, type, pp)
    M32(arg(c, 4)) = dev.backbuffer;
    obj_addref(obj(dev.backbuffer));
    RET(D3D_OK, 5);
}
static int v_GetRasterStatus(Cpu *c) {
    uint32_t p = arg(c, 2);
    uint64_t t = host_ticks() % 16666667ull;
    M32(p) = t > 15500000ull;
    M32(p + 4) = M32(p) ? 0 : (uint32_t)(t * dev.pp[1] / 15500000ull);
    RET(D3D_OK, 3);
}
static int v_SetDialogBoxMode(Cpu *c) { RET(D3D_OK, 2); }
// The game's Brightness option: D3DGAMMARAMP (red[256], green[256], blue[256] words), applied when the frame is
// presented (render_present.m). SetGammaRamp(iSwapChain, Flags, pRamp); the identity ramp until the game sets one.
static uint16_t gamma_ramp[768];
static int gamma_set;
static int v_SetGammaRamp(Cpu *c) {
    uint32_t p = arg(c, 3);
    if (p) {
        for (int i = 0; i < 768; ++i) gamma_ramp[i] = M16(p + i * 2);
        gamma_set = 1;
        if (diag_on) diag_event("SetGammaRamp: flags %u, ramp[0,64,128,192,255] r %u %u %u %u %u g %u b %u", arg(c, 2), gamma_ramp[0], gamma_ramp[64], gamma_ramp[128], gamma_ramp[192], gamma_ramp[255], gamma_ramp[256 + 128], gamma_ramp[512 + 128]);
        render_set_gamma(gamma_ramp);
    }
    RET(0, 4);
}
static int v_GetGammaRamp(Cpu *c) {
    uint32_t p = arg(c, 2);
    for (int ch = 0; ch < 3; ++ch) for (int i = 0; i < 256; ++i) M16(p + ch * 512 + i * 2) = gamma_set ? gamma_ramp[ch * 256 + i] : (uint16_t)(i * 257);
    RET(0, 3);
}
static Obj *make_texture(int kind, uint32_t vt, uint32_t w, uint32_t h, uint32_t d, uint32_t levels, uint32_t usage, uint32_t fmt, uint32_t pool) {
    Obj *t = new_obj(kind, vt);
    uint32_t maxl = 1;
    for (uint32_t m = w > h ? w : h; m > 1; m >>= 1) ++maxl;
    if (levels == 0 || levels > maxl) levels = (usage & 0x400) ? 1 : maxl;   // AUTOGENMIPMAP exposes one level
    if (levels > 16) levels = 16;
    t->w = w; t->h = h; t->d = d ? d : 1; t->levels = levels; t->usage = usage; t->fmt = fmt; t->pool = pool;
    t->hd_swap = (uint8_t)x360_hd_creating;
    int faces = kind == K_CUBE ? 6 : 1;
    for (uint32_t l = 0; l < levels; ++l) {
        t->pitch[l] = level_pitch(fmt, mipdim(w, l));
        uint32_t bytes = t->pitch[l] * level_rows(fmt, mipdim(h, l)) * (kind == K_VOLUME ? mipdim(t->d, l) : 1);
        for (int f = 0; f < faces; ++f) t->mem[f * 16 + l] = gmalloc(bytes);
    }
    t->dirty = 1;
    return t;
}
static int v_CreateTexture(Cpu *c) {           // (this, w, h, levels, usage, fmt, pool, pp, shared)
    if (getenv("BOOT_TEXLOG")) fprintf(stderr, "tex %ux%u levels %u usage %X fmt %X pool %u\n", arg(c, 1), arg(c, 2), arg(c, 3), arg(c, 4), arg(c, 5), arg(c, 6));
    uint32_t w = arg(c, 1), h = arg(c, 2);
    x360_shadow_texture_size(&w, &h);           // X360 shadow-map size
    x360_reflection_size(&w, &h);               // X360 road reflection size
    Obj *t = make_texture(K_TEXTURE, vt_texture, w, h, 1, arg(c, 3), arg(c, 4), arg(c, 5), arg(c, 6));
    M32(arg(c, 7)) = t->guest;
    RET(D3D_OK, 9);
}
static int v_CreateVolumeTexture(Cpu *c) {     // (this, w, h, d, levels, usage, fmt, pool, pp, shared)
    Obj *t = make_texture(K_VOLUME, vt_volume, arg(c, 1), arg(c, 2), arg(c, 3), arg(c, 4), arg(c, 5), arg(c, 6), arg(c, 7));
    M32(arg(c, 8)) = t->guest;
    RET(D3D_OK, 10);
}
static int v_CreateCubeTexture(Cpu *c) {       // (this, edge, levels, usage, fmt, pool, pp, shared)
    Obj *t = make_texture(K_CUBE, vt_cube, arg(c, 1), arg(c, 1), 1, arg(c, 2), arg(c, 3), arg(c, 4), arg(c, 5));
    M32(arg(c, 6)) = t->guest;
    RET(D3D_OK, 8);
}
static int v_CreateVertexBuffer(Cpu *c) {      // (this, len, usage, fvf, pool, pp, shared)
    Obj *b = new_obj(K_VB, vt_vb);
    b->size = arg(c, 1); b->usage = arg(c, 2); b->fvf = arg(c, 3); b->pool = arg(c, 4);
    b->mem[0] = gmalloc(b->size);
    M32(arg(c, 5)) = b->guest;
    RET(D3D_OK, 7);
}
static int v_CreateIndexBuffer(Cpu *c) {       // (this, len, usage, fmt, pool, pp, shared)
    Obj *b = new_obj(K_IB, vt_ib);
    b->size = arg(c, 1); b->usage = arg(c, 2); b->fmt = arg(c, 3); b->pool = arg(c, 4);
    b->mem[0] = gmalloc(b->size);
    M32(arg(c, 5)) = b->guest;
    RET(D3D_OK, 7);
}
static void set_multisample(Obj *s, uint32_t type, uint32_t quality) {
    s->samples = render_msaa_count(type, quality);
    if (s->samples > 1) { s->ms_type = type; s->ms_quality = quality; }
}
static int v_CreateRenderTarget(Cpu *c) {      // (this, w, h, fmt, ms, msq, lockable, pp, shared)
    Obj *s = make_surface(arg(c, 1), arg(c, 2), arg(c, 3), 1, 0, 0);
    set_multisample(s, arg(c, 4), arg(c, 5));
    M32(arg(c, 7)) = s->guest;
    RET(D3D_OK, 9);
}
static int v_CreateDepthStencilSurface(Cpu *c) {   // (this, w, h, fmt, ms, msq, discard, pp, shared)
    uint32_t w = arg(c, 1), h = arg(c, 2);
    x360_reflection_size(&w, &h);               // X360 road reflection size
    Obj *s = make_surface(w, h, arg(c, 3), 2, 0, 0);
    set_multisample(s, arg(c, 4), arg(c, 5));
    M32(arg(c, 7)) = s->guest;
    RET(D3D_OK, 9);
}
static int v_UpdateSurface(Cpu *c) { if (diag_on) diag_event("UpdateSurface not implemented (no-op) @%08X", M32(c->esp)); RET(D3D_OK, 5); }
static int v_UpdateTexture(Cpu *c) {           // (this, src, dst): copy system-memory levels
    Obj *s = obj(arg(c, 1)), *d = obj(arg(c, 2));
    if (s && d) {
        int faces = s->kind == K_CUBE ? 6 : 1;
        for (uint32_t l = 0; l < s->levels && l < d->levels; ++l)
            for (int f = 0; f < faces; ++f) {
                uint32_t bytes = s->pitch[l] * level_rows(s->fmt, mipdim(s->h, l)) * (s->kind == K_VOLUME ? mipdim(s->d, l) : 1);
                if (s->mem[f * 16 + l] && d->mem[f * 16 + l]) memcpy(G + d->mem[f * 16 + l], G + s->mem[f * 16 + l], bytes);
            }
        d->dirty = 1;
    }
    RET(D3D_OK, 3);
}
static int v_GetRenderTargetData(Cpu *c) { if (diag_on) diag_event("GetRenderTargetData not implemented (no-op) @%08X", M32(c->esp)); RET(D3D_OK, 3); }
static int v_GetFrontBufferData(Cpu *c) { if (diag_on) diag_event("GetFrontBufferData not implemented (no-op) @%08X", M32(c->esp)); RET(D3D_OK, 3); }
static int v_StretchRect(Cpu *c) { render_stretch(arg(c, 1), arg(c, 2), arg(c, 3), arg(c, 4), arg(c, 5)); RET(D3D_OK, 6); }
static int v_ColorFill(Cpu *c) { if (diag_on) diag_event("ColorFill not implemented (no-op) @%08X", M32(c->esp)); RET(D3D_OK, 4); }
static int v_CreateOffscreenPlainSurface(Cpu *c) {   // (this, w, h, fmt, pool, pp, shared)
    Obj *s = make_surface(arg(c, 1), arg(c, 2), arg(c, 3), 0, arg(c, 4), 1);
    M32(arg(c, 5)) = s->guest;
    RET(D3D_OK, 7);
}
static int v_SetRenderTarget(Cpu *c) {
    uint32_t i = arg(c, 1);
    if (i > 3 || (i == 0 && !arg(c, 2))) RET(D3DERR_INVALIDCALL, 3);
    dev_bind(&dev.rt[i], arg(c, 2));
    render_targets_changed();
    Obj *s = obj(arg(c, 2));
    if (i == 0 && s) { dev.viewport[0] = dev.viewport[1] = 0; dev.viewport[2] = s->w; dev.viewport[3] = s->h; }
    RET(D3D_OK, 3);
}
static int v_GetRenderTarget(Cpu *c) {
    uint32_t i = arg(c, 1);
    if (i > 3 || !dev.rt[i]) { M32(arg(c, 2)) = 0; RET(0x88760866u, 3); }
    M32(arg(c, 2)) = dev.rt[i];
    obj_addref(obj(dev.rt[i]));
    RET(D3D_OK, 3);
}
static int v_SetDepthStencilSurface(Cpu *c) { dev_bind(&dev.ds, arg(c, 1)); render_targets_changed(); RET(D3D_OK, 2); }
static int v_GetDepthStencilSurface(Cpu *c) {
    M32(arg(c, 1)) = dev.ds;
    if (!dev.ds) RET(0x88760866u, 2);
    obj_addref(obj(dev.ds));
    RET(D3D_OK, 2);
}
static int v_BeginScene(Cpu *c) { RET(D3D_OK, 1); }
static int v_EndScene(Cpu *c) { RET(D3D_OK, 1); }
static int v_Clear(Cpu *c) {                   // (this, count, rects, flags, color, z, stencil)
    if ((arg(c, 3) & 1) && dev.rt[0] == dev.backbuffer) {
        uint32_t col = arg(c, 4);
        dev.clear_rgba[0] = ((col >> 16) & 255) / 255.0f;
        dev.clear_rgba[1] = ((col >> 8) & 255) / 255.0f;
        dev.clear_rgba[2] = (col & 255) / 255.0f;
        dev.clear_rgba[3] = (col >> 24) / 255.0f;
    }
    // The host clears whole targets. D3D9 clears only the viewport (and the rects): record any clear where that differs.
    Obj *rt = obj(dev.rt[0]);
    if (diag_on && (arg(c, 1) || (rt && (dev.viewport[0] || dev.viewport[1] || dev.viewport[2] < rt->w || dev.viewport[3] < rt->h))))
        diag_event("partial Clear not honoured: %u rects, viewport %u,%u %ux%u, target %ux%u, flags %X @%08X", arg(c, 1),
                   dev.viewport[0], dev.viewport[1], dev.viewport[2], dev.viewport[3], rt ? rt->w : 0, rt ? rt->h : 0, arg(c, 3), M32(c->esp));
    render_clear(arg(c, 1), arg(c, 2), arg(c, 3), arg(c, 4), argf(c, 5), arg(c, 6));
    RET(D3D_OK, 7);
}
static int v_SetTransform(Cpu *c) { uint32_t s = arg(c, 1); if (s < 512) memcpy(dev.transform[s], G + arg(c, 2), 64); RET(D3D_OK, 3); }
static int v_GetTransform(Cpu *c) { uint32_t s = arg(c, 1); if (s < 512) memcpy(G + arg(c, 2), dev.transform[s], 64); RET(D3D_OK, 3); }
static int v_SetViewport(Cpu *c) { memcpy(dev.viewport, G + arg(c, 1), 24); RET(D3D_OK, 2); }
static int v_GetViewport(Cpu *c) { memcpy(G + arg(c, 1), dev.viewport, 24); RET(D3D_OK, 2); }
static int v_SetMaterial(Cpu *c) { RET(D3D_OK, 2); }
static int v_SetLight(Cpu *c) { RET(D3D_OK, 3); }
static int v_LightEnable(Cpu *c) { RET(D3D_OK, 3); }
static int v_SetClipPlane(Cpu *c) { RET(D3D_OK, 3); }
static int v_SetRenderState(Cpu *c) { if (arg(c, 1) < 256) dev.rs[arg(c, 1)] = arg(c, 2); RET(D3D_OK, 3); }
static int v_GetRenderState(Cpu *c) { M32(arg(c, 2)) = arg(c, 1) < 256 ? dev.rs[arg(c, 1)] : 0; RET(D3D_OK, 3); }
static int v_CreateStateBlock(Cpu *c) { M32(arg(c, 2)) = new_obj(K_STATEBLOCK, vt_sb)->guest; RET(D3D_OK, 3); }
static int v_BeginStateBlock(Cpu *c) { RET(D3D_OK, 1); }
static int v_EndStateBlock(Cpu *c) { M32(arg(c, 1)) = new_obj(K_STATEBLOCK, vt_sb)->guest; RET(D3D_OK, 2); }
static int v_SetClipStatus(Cpu *c) { RET(D3D_OK, 2); }
static int v_GetTexture(Cpu *c) {
    uint32_t s = arg(c, 1), i = s >= 0x101 ? s - 0x101 + 16 : s;
    uint32_t t = i < 20 ? dev.tex[i] : 0;
    M32(arg(c, 2)) = t;
    if (t) obj_addref(obj(t));
    RET(D3D_OK, 3);
}
static int v_SetTexture(Cpu *c) {              // stages 0-15, D3DVERTEXTEXTURESAMPLER0..3 = 0x101..0x104
    uint32_t s = arg(c, 1), i = s >= 0x101 ? s - 0x101 + 16 : s;
    if (i < 20) dev_bind(&dev.tex[i], arg(c, 2));
    RET(D3D_OK, 3);
}
static int v_GetTextureStageState(Cpu *c) { M32(arg(c, 3)) = arg(c, 1) < 8 && arg(c, 2) < 33 ? dev.tss[arg(c, 1)][arg(c, 2)] : 0; RET(D3D_OK, 4); }
static int v_SetTextureStageState(Cpu *c) { if (arg(c, 1) < 8 && arg(c, 2) < 33) dev.tss[arg(c, 1)][arg(c, 2)] = arg(c, 3); RET(D3D_OK, 4); }
static int v_GetSamplerState(Cpu *c) {
    uint32_t s = arg(c, 1), i = s >= 0x101 ? s - 0x101 + 16 : s;
    M32(arg(c, 3)) = i < 20 && arg(c, 2) < 14 ? dev.ss[i][arg(c, 2)] : 0;
    RET(D3D_OK, 4);
}
static int v_SetSamplerState(Cpu *c) {
    uint32_t s = arg(c, 1), i = s >= 0x101 ? s - 0x101 + 16 : s;
    if (i < 20 && arg(c, 2) < 14) dev.ss[i][arg(c, 2)] = arg(c, 3);
    if (diag_on && arg(c, 2) == 8 && arg(c, 3)) {        // MIPMAPLODBIAS: who sets which bias (first 16 distinct)
        static uint32_t seen[16][3]; static int nseen;
        extern char diag_fx_pass[];
        uint32_t key[3] = {i, arg(c, 3), M32(c->esp)};
        int k = 0;
        while (k < nseen && memcmp(seen[k], key, sizeof key)) ++k;
        if (k == nseen && nseen < 16) {
            memcpy(seen[nseen++], key, sizeof key);
            float f; memcpy(&f, &key[1], 4);
            diag_event("lod bias: sampler %u = %.3f from %08X (effect pass %s)", i, f, key[2], diag_fx_pass[0] ? diag_fx_pass : "-");
        }
    }
    RET(D3D_OK, 4);
}
static int v_ValidateDevice(Cpu *c) { if (arg(c, 1)) M32(arg(c, 1)) = 1; RET(D3D_OK, 2); }
static int v_SetScissorRect(Cpu *c) { memcpy(dev.scissor, G + arg(c, 1), 16); RET(D3D_OK, 2); }
static int v_GetScissorRect(Cpu *c) { memcpy(G + arg(c, 1), dev.scissor, 16); RET(D3D_OK, 2); }
static int v_SetSoftwareVertexProcessing(Cpu *c) { RET(D3D_OK, 2); }
static int v_GetSoftwareVertexProcessing(Cpu *c) { RET(0, 1); }
static int v_SetNPatchMode(Cpu *c) { RET(D3D_OK, 2); }
static void draw(void) { ++dev.draws; ++dev.frame_draws; }
// (this, type, start, primCount)
static int v_DrawPrimitive(Cpu *c) { draw(); render_draw(arg(c, 1), arg(c, 3), 0, 0, 0, 0, arg(c, 2), 0, 0, 0, 0); RET(D3D_OK, 4); }
// (this, type, baseVertex, minIndex, numVertices, startIndex, primCount)
extern uint32_t x360_arrest_draws;              // x360_textures.m: draws with an X360 arrest-scene texture (diag)
static void count_arrest_draw(void) {
    Obj *t = dev.tex[0] ? obj(dev.tex[0]) : NULL;
    if (t && t->hd_swap == 2 && (++x360_arrest_draws == 1 || x360_arrest_draws % 1000 == 0) && diag_on)
        diag_event("x360: arrest character draw %u (texture %ux%u)", x360_arrest_draws, t->w, t->h);
}
static int v_DrawIndexedPrimitive(Cpu *c) { draw(); count_arrest_draw(); render_draw(arg(c, 1), arg(c, 6), 1, (int32_t)arg(c, 2), arg(c, 3), arg(c, 4), arg(c, 5), 0, 0, 0, 0); RET(D3D_OK, 7); }
// (this, type, primCount, data, stride)
static int v_DrawPrimitiveUP(Cpu *c) { widescreen_drop_quad(c, arg(c, 3), arg(c, 4)); draw(); render_draw(arg(c, 1), arg(c, 2), 0, 0, 0, 0, 0, arg(c, 3), arg(c, 4), 0, 0); dev_bind(&dev.stream[0], 0); RET(D3D_OK, 5); }
// (this, type, minIndex, numVertices, primCount, indexData, indexFmt, vertexData, stride)
static int v_DrawIndexedPrimitiveUP(Cpu *c) { draw(); render_draw(arg(c, 1), arg(c, 4), 1, 0, arg(c, 2), arg(c, 3), 0, arg(c, 7), arg(c, 8), arg(c, 5), arg(c, 6)); dev_bind(&dev.stream[0], 0); dev_bind(&dev.indices, 0); RET(D3D_OK, 9); }
static int v_CreateVertexDeclaration(Cpu *c) { // (this, elements*, pp): elements end with {0xFF, 0, D3DDECLTYPE_UNUSED=17}
    uint32_t e = arg(c, 1), n = 0;
    while (M16(e + 8 * n) != 0xFF && n < 64) ++n;
    Obj *d = new_obj(K_DECL, vt_decl);
    d->decl_n = n + 1;
    d->decl_mem = galloc(8 * (n + 1));
    memcpy(G + d->decl_mem, G + e, 8 * (n + 1));
    M32(arg(c, 2)) = d->guest;
    RET(D3D_OK, 3);
}
static int v_SetVertexDeclaration(Cpu *c) { dev.decl = arg(c, 1); dev.fvf = 0; RET(D3D_OK, 2); }
static int v_GetVertexDeclaration(Cpu *c) { M32(arg(c, 1)) = dev.decl; if (dev.decl) ++obj(dev.decl)->refs; RET(D3D_OK, 2); }
static int v_SetFVF(Cpu *c) { dev.fvf = arg(c, 1); dev.decl = 0; RET(D3D_OK, 2); }
static int v_GetFVF(Cpu *c) { M32(arg(c, 1)) = dev.fvf; RET(D3D_OK, 2); }
static Obj *make_shader(int kind, uint32_t vt, uint32_t fn) {
    uint32_t n = 1;
    while (M32(fn + 4 * (n - 1)) != 0x0000FFFF && n < 65536) ++n;   // END token
    Obj *s = new_obj(kind, vt);
    s->ntokens = n;
    s->tokens = malloc(4 * n);
    memcpy(s->tokens, G + fn, 4 * n);
    return s;
}
static int v_CreateVertexShader(Cpu *c) { M32(arg(c, 2)) = make_shader(K_VS, vt_vs, arg(c, 1))->guest; RET(D3D_OK, 3); }
static int v_CreatePixelShader(Cpu *c) { M32(arg(c, 2)) = make_shader(K_PS, vt_ps, arg(c, 1))->guest; RET(D3D_OK, 3); }
static int v_SetVertexShader(Cpu *c) { dev.vs = arg(c, 1); RET(D3D_OK, 2); }
static int v_GetVertexShader(Cpu *c) { M32(arg(c, 1)) = dev.vs; if (dev.vs) ++obj(dev.vs)->refs; RET(D3D_OK, 2); }
static int v_SetPixelShader(Cpu *c) { dev.ps = arg(c, 1); RET(D3D_OK, 2); }
static int v_GetPixelShader(Cpu *c) { M32(arg(c, 1)) = dev.ps; if (dev.ps) ++obj(dev.ps)->refs; RET(D3D_OK, 2); }
#define CONST_FN(name, arr, lim, w)                                                                  \
    static int v_Set##name(Cpu *c) {                                                                 \
        uint32_t s = arg(c, 1), n = arg(c, 3);                                                       \
        if (s + n > lim) RET(D3DERR_INVALIDCALL, 4);                                                 \
        memcpy(&dev.arr[s], G + arg(c, 2), (size_t)n * (w));                                         \
        if (!++dev.ver_##arr) dev.ver_##arr = 1;                                                     \
        RET(D3D_OK, 4);                                                                              \
    }                                                                                                \
    static int v_Get##name(Cpu *c) {                                                                 \
        uint32_t s = arg(c, 1), n = arg(c, 3);                                                       \
        if (s + n > lim) RET(D3DERR_INVALIDCALL, 4);                                                 \
        memcpy(G + arg(c, 2), &dev.arr[s], (size_t)n * (w));                                         \
        RET(D3D_OK, 4);                                                                              \
    }
CONST_FN(VertexShaderConstantF, vsc, 256, 16)
CONST_FN(PixelShaderConstantF, psc, 224, 16)
CONST_FN(VertexShaderConstantI, vsi, 16, 16)
CONST_FN(PixelShaderConstantI, psi, 16, 16)
CONST_FN(VertexShaderConstantB, vsb, 16, 4)
CONST_FN(PixelShaderConstantB, psb, 16, 4)
static int v_SetStreamSource(Cpu *c) {         // (this, stream, vb, offset, stride)
    uint32_t s = arg(c, 1);
    if (s >= 16) RET(D3DERR_INVALIDCALL, 5);
    dev_bind(&dev.stream[s], arg(c, 2)); dev.stream_off[s] = arg(c, 3); dev.stream_stride[s] = arg(c, 4);
    RET(D3D_OK, 5);
}
static int v_GetStreamSource(Cpu *c) {
    uint32_t s = arg(c, 1);
    if (s >= 16) RET(D3DERR_INVALIDCALL, 5);
    M32(arg(c, 2)) = dev.stream[s]; M32(arg(c, 3)) = dev.stream_off[s]; M32(arg(c, 4)) = dev.stream_stride[s];
    if (dev.stream[s]) obj_addref(obj(dev.stream[s]));
    RET(D3D_OK, 5);
}
static int v_SetStreamSourceFreq(Cpu *c) { if (arg(c, 1) < 16) dev.stream_freq[arg(c, 1)] = arg(c, 2); RET(D3D_OK, 3); }
static int v_GetStreamSourceFreq(Cpu *c) { M32(arg(c, 2)) = arg(c, 1) < 16 ? dev.stream_freq[arg(c, 1)] : 1; RET(D3D_OK, 3); }
static int v_SetIndices(Cpu *c) { dev_bind(&dev.indices, arg(c, 1)); RET(D3D_OK, 2); }
static int v_GetIndices(Cpu *c) { M32(arg(c, 1)) = dev.indices; if (dev.indices) obj_addref(obj(dev.indices)); RET(D3D_OK, 2); }
static int v_CreateQuery(Cpu *c) {             // (this, type, pp): pp NULL = "is this type supported?"
    uint32_t t = arg(c, 1);
    if (t != 8 && t != 9) RET(D3DERR_NOTAVAILABLE, 3);
    if (!arg(c, 2)) RET(D3D_OK, 3);
    Obj *q = new_obj(K_QUERY, vt_query);
    q->qtype = t;
    M32(arg(c, 2)) = q->guest;
    RET(D3D_OK, 3);
}

// ---------------- interface tables ----------------
static const char *const n_d3d[] = {"QueryInterface", "AddRef", "Release", "RegisterSoftwareDevice", "GetAdapterCount",
    "GetAdapterIdentifier", "GetAdapterModeCount", "EnumAdapterModes", "GetAdapterDisplayMode", "CheckDeviceType",
    "CheckDeviceFormat", "CheckDeviceMultiSampleType", "CheckDepthStencilMatch", "CheckDeviceFormatConversion",
    "GetDeviceCaps", "GetAdapterMonitor", "CreateDevice"};
static const Impl i_d3d[] = {{"QueryInterface", m_QueryInterface}, {"AddRef", m_AddRef}, {"Release", m_Release},
    {"GetAdapterCount", d_GetAdapterCount}, {"GetAdapterIdentifier", d_GetAdapterIdentifier},
    {"GetAdapterModeCount", d_GetAdapterModeCount}, {"EnumAdapterModes", d_EnumAdapterModes},
    {"GetAdapterDisplayMode", d_GetAdapterDisplayMode}, {"CheckDeviceType", d_CheckDeviceType},
    {"CheckDeviceFormat", d_CheckDeviceFormat}, {"CheckDeviceMultiSampleType", d_CheckDeviceMultiSampleType},
    {"CheckDepthStencilMatch", d_CheckDepthStencilMatch}, {"CheckDeviceFormatConversion", d_CheckDeviceFormatConversion},
    {"GetDeviceCaps", d_GetDeviceCaps}, {"GetAdapterMonitor", d_GetAdapterMonitor}, {"CreateDevice", d_CreateDevice}, {0, 0}};

static const char *const n_device[] = {"QueryInterface", "AddRef", "Release", "TestCooperativeLevel",
    "GetAvailableTextureMem", "EvictManagedResources", "GetDirect3D", "GetDeviceCaps", "GetDisplayMode",
    "GetCreationParameters", "SetCursorProperties", "SetCursorPosition", "ShowCursor", "CreateAdditionalSwapChain",
    "GetSwapChain", "GetNumberOfSwapChains", "Reset", "Present", "GetBackBuffer", "GetRasterStatus", "SetDialogBoxMode",
    "SetGammaRamp", "GetGammaRamp", "CreateTexture", "CreateVolumeTexture", "CreateCubeTexture", "CreateVertexBuffer",
    "CreateIndexBuffer", "CreateRenderTarget", "CreateDepthStencilSurface", "UpdateSurface", "UpdateTexture",
    "GetRenderTargetData", "GetFrontBufferData", "StretchRect", "ColorFill", "CreateOffscreenPlainSurface",
    "SetRenderTarget", "GetRenderTarget", "SetDepthStencilSurface", "GetDepthStencilSurface", "BeginScene", "EndScene",
    "Clear", "SetTransform", "GetTransform", "MultiplyTransform", "SetViewport", "GetViewport", "SetMaterial",
    "GetMaterial", "SetLight", "GetLight", "LightEnable", "GetLightEnable", "SetClipPlane", "GetClipPlane",
    "SetRenderState", "GetRenderState", "CreateStateBlock", "BeginStateBlock", "EndStateBlock", "SetClipStatus",
    "GetClipStatus", "GetTexture", "SetTexture", "GetTextureStageState", "SetTextureStageState", "GetSamplerState",
    "SetSamplerState", "ValidateDevice", "SetPaletteEntries", "GetPaletteEntries", "SetCurrentTexturePalette",
    "GetCurrentTexturePalette", "SetScissorRect", "GetScissorRect", "SetSoftwareVertexProcessing",
    "GetSoftwareVertexProcessing", "SetNPatchMode", "GetNPatchMode", "DrawPrimitive", "DrawIndexedPrimitive",
    "DrawPrimitiveUP", "DrawIndexedPrimitiveUP", "ProcessVertices", "CreateVertexDeclaration", "SetVertexDeclaration",
    "GetVertexDeclaration", "SetFVF", "GetFVF", "CreateVertexShader", "SetVertexShader", "GetVertexShader",
    "SetVertexShaderConstantF", "GetVertexShaderConstantF", "SetVertexShaderConstantI", "GetVertexShaderConstantI",
    "SetVertexShaderConstantB", "GetVertexShaderConstantB", "SetStreamSource", "GetStreamSource", "SetStreamSourceFreq",
    "GetStreamSourceFreq", "SetIndices", "GetIndices", "CreatePixelShader", "SetPixelShader", "GetPixelShader",
    "SetPixelShaderConstantF", "GetPixelShaderConstantF", "SetPixelShaderConstantI", "GetPixelShaderConstantI",
    "SetPixelShaderConstantB", "GetPixelShaderConstantB", "DrawRectPatch", "DrawTriPatch", "DeletePatch", "CreateQuery"};
#define V(n) {#n, v_##n}
static const Impl i_device[] = {{"QueryInterface", m_QueryInterface}, {"AddRef", m_AddRef}, {"Release", m_Release},
    V(TestCooperativeLevel), V(GetAvailableTextureMem), V(EvictManagedResources), V(GetDirect3D), V(GetDeviceCaps),
    V(GetDisplayMode), V(GetCreationParameters), V(SetCursorProperties), V(SetCursorPosition), V(ShowCursor),
    V(GetNumberOfSwapChains), V(Reset), V(Present), V(GetBackBuffer), V(GetRasterStatus), V(SetDialogBoxMode),
    V(SetGammaRamp), V(GetGammaRamp), V(CreateTexture), V(CreateVolumeTexture), V(CreateCubeTexture),
    V(CreateVertexBuffer), V(CreateIndexBuffer), V(CreateRenderTarget), V(CreateDepthStencilSurface), V(UpdateSurface),
    V(UpdateTexture), V(GetRenderTargetData), V(GetFrontBufferData), V(StretchRect), V(ColorFill),
    V(CreateOffscreenPlainSurface), V(SetRenderTarget), V(GetRenderTarget), V(SetDepthStencilSurface),
    V(GetDepthStencilSurface), V(BeginScene), V(EndScene), V(Clear), V(SetTransform), V(GetTransform), V(SetViewport),
    V(GetViewport), V(SetMaterial), V(SetLight), V(LightEnable), V(SetClipPlane), V(SetRenderState), V(GetRenderState),
    V(CreateStateBlock), V(BeginStateBlock), V(EndStateBlock), V(SetClipStatus), V(GetTexture), V(SetTexture),
    V(GetTextureStageState), V(SetTextureStageState), V(GetSamplerState), V(SetSamplerState), V(ValidateDevice),
    V(SetScissorRect), V(GetScissorRect), V(SetSoftwareVertexProcessing), V(GetSoftwareVertexProcessing),
    V(SetNPatchMode), V(DrawPrimitive), V(DrawIndexedPrimitive), V(DrawPrimitiveUP), V(DrawIndexedPrimitiveUP),
    V(CreateVertexDeclaration), V(SetVertexDeclaration), V(GetVertexDeclaration), V(SetFVF), V(GetFVF),
    V(CreateVertexShader), V(SetVertexShader), V(GetVertexShader), V(SetVertexShaderConstantF),
    V(GetVertexShaderConstantF), V(SetVertexShaderConstantI), V(GetVertexShaderConstantI),
    V(SetVertexShaderConstantB), V(GetVertexShaderConstantB), V(SetStreamSource), V(GetStreamSource),
    V(SetStreamSourceFreq), V(GetStreamSourceFreq), V(SetIndices), V(GetIndices), V(CreatePixelShader),
    V(SetPixelShader), V(GetPixelShader), V(SetPixelShaderConstantF), V(GetPixelShaderConstantF),
    V(SetPixelShaderConstantI), V(GetPixelShaderConstantI), V(SetPixelShaderConstantB), V(GetPixelShaderConstantB),
    V(CreateQuery), {0, 0}};

static const char *const n_texture[] = {RESOURCE_NAMES, "SetLOD", "GetLOD", "GetLevelCount", "SetAutoGenFilterType",
    "GetAutoGenFilterType", "GenerateMipSubLevels", "GetLevelDesc", "GetSurfaceLevel", "LockRect", "UnlockRect", "AddDirtyRect"};
#define BASETEX_IMPL {"SetLOD", t_SetLOD}, {"GetLOD", t_GetLOD}, {"GetLevelCount", t_GetLevelCount}, \
    {"SetAutoGenFilterType", t_SetAutoGenFilterType}, {"GetAutoGenFilterType", t_GetAutoGenFilterType}, \
    {"GenerateMipSubLevels", t_GenerateMipSubLevels}
static const Impl i_texture[] = {RESOURCE_IMPL, BASETEX_IMPL, {"GetLevelDesc", t_GetLevelDesc},
    {"GetSurfaceLevel", t_GetSurfaceLevel}, {"LockRect", t_LockRect}, {"UnlockRect", t_UnlockRect},
    {"AddDirtyRect", t_AddDirtyRect}, {0, 0}};
static const char *const n_cube[] = {RESOURCE_NAMES, "SetLOD", "GetLOD", "GetLevelCount", "SetAutoGenFilterType",
    "GetAutoGenFilterType", "GenerateMipSubLevels", "GetLevelDesc", "GetCubeMapSurface", "LockRect", "UnlockRect", "AddDirtyRect"};
static const Impl i_cube[] = {RESOURCE_IMPL, BASETEX_IMPL, {"GetLevelDesc", t_GetLevelDesc},
    {"GetCubeMapSurface", cube_GetCubeMapSurface}, {"LockRect", cube_LockRect}, {"UnlockRect", cube_UnlockRect},
    {"AddDirtyRect", cube_AddDirtyRect}, {0, 0}};
static const char *const n_volume[] = {RESOURCE_NAMES, "SetLOD", "GetLOD", "GetLevelCount", "SetAutoGenFilterType",
    "GetAutoGenFilterType", "GenerateMipSubLevels", "GetLevelDesc", "GetVolumeLevel", "LockBox", "UnlockBox", "AddDirtyBox"};
static const Impl i_volume[] = {RESOURCE_IMPL, BASETEX_IMPL, {"GetLevelDesc", vol_GetLevelDesc}, {"LockBox", vol_LockBox},
    {"UnlockBox", vol_UnlockBox}, {"AddDirtyBox", t_AddDirtyRect}, {0, 0}};
static const char *const n_surface[] = {RESOURCE_NAMES, "GetContainer", "GetDesc", "LockRect", "UnlockRect", "GetDC", "ReleaseDC"};
static const Impl i_surface[] = {RESOURCE_IMPL, {"GetContainer", s_GetContainer}, {"GetDesc", s_GetDesc},
    {"LockRect", s_LockRect}, {"UnlockRect", s_UnlockRect}, {"GetDC", s_GetDC}, {"ReleaseDC", s_ReleaseDC}, {0, 0}};
static const char *const n_vb[] = {RESOURCE_NAMES, "Lock", "Unlock", "GetDesc"};
static const Impl i_vb[] = {RESOURCE_IMPL, {"Lock", b_Lock}, {"Unlock", b_Unlock}, {"GetDesc", vb_GetDesc}, {0, 0}};
static const Impl i_ib[] = {RESOURCE_IMPL, {"Lock", b_Lock}, {"Unlock", b_Unlock}, {"GetDesc", ib_GetDesc}, {0, 0}};
static const char *const n_decl[] = {"QueryInterface", "AddRef", "Release", "GetDevice", "GetDeclaration"};
static const Impl i_decl[] = {{"QueryInterface", m_QueryInterface}, {"AddRef", m_AddRef}, {"Release", m_Release},
    {"GetDevice", m_GetDevice}, {"GetDeclaration", decl_GetDeclaration}, {0, 0}};
static const char *const n_shader[] = {"QueryInterface", "AddRef", "Release", "GetDevice", "GetFunction"};
static const Impl i_shader[] = {{"QueryInterface", m_QueryInterface}, {"AddRef", m_AddRef}, {"Release", m_Release},
    {"GetDevice", m_GetDevice}, {"GetFunction", sh_GetFunction}, {0, 0}};
static const char *const n_sb[] = {"QueryInterface", "AddRef", "Release", "GetDevice", "Capture", "Apply"};
static const Impl i_sb[] = {{"QueryInterface", m_QueryInterface}, {"AddRef", m_AddRef}, {"Release", m_Release},
    {"GetDevice", m_GetDevice}, {"Capture", sb_Capture}, {"Apply", sb_Apply}, {0, 0}};
static const char *const n_query[] = {"QueryInterface", "AddRef", "Release", "GetDevice", "GetType", "GetDataSize", "Issue", "GetData"};
static const Impl i_query[] = {{"QueryInterface", m_QueryInterface}, {"AddRef", m_AddRef}, {"Release", m_Release},
    {"GetDevice", m_GetDevice}, {"GetType", q_GetType}, {"GetDataSize", q_GetDataSize}, {"Issue", q_Issue},
    {"GetData", q_GetData}, {0, 0}};
#define N(a) (int)(sizeof a / sizeof *a)

static void d3d9_init(void) {
    static int done;
    if (done) return;
    done = 1;
    vt_d3d = make_vtable("IDirect3D9", n_d3d, N(n_d3d), i_d3d);
    vt_device = make_vtable("IDirect3DDevice9", n_device, N(n_device), i_device);
    vt_texture = make_vtable("IDirect3DTexture9", n_texture, N(n_texture), i_texture);
    vt_cube = make_vtable("IDirect3DCubeTexture9", n_cube, N(n_cube), i_cube);
    vt_volume = make_vtable("IDirect3DVolumeTexture9", n_volume, N(n_volume), i_volume);
    vt_surface = make_vtable("IDirect3DSurface9", n_surface, N(n_surface), i_surface);
    vt_vb = make_vtable("IDirect3DVertexBuffer9", n_vb, N(n_vb), i_vb);
    vt_ib = make_vtable("IDirect3DIndexBuffer9", n_vb, N(n_vb), i_ib);
    vt_decl = make_vtable("IDirect3DVertexDeclaration9", n_decl, N(n_decl), i_decl);
    vt_vs = make_vtable("IDirect3DVertexShader9", n_shader, N(n_shader), i_shader);
    vt_ps = make_vtable("IDirect3DPixelShader9", n_shader, N(n_shader), i_shader);
    vt_sb = make_vtable("IDirect3DStateBlock9", n_sb, N(n_sb), i_sb);
    vt_query = make_vtable("IDirect3DQuery9", n_query, N(n_query), i_query);
}

// For d3dx effects: the device's shader objects and state.
uint32_t d3d9_device_guest(void) { return dev.self ? dev.self->guest : 0; }

int win32_d3d9(Cpu *c, const char *fn) {
    if (!strcmp(fn, "Direct3DCreate9")) {        // (SDKVersion)
        d3d9_init();
        if (boot_trace) fprintf(stderr, "      Direct3DCreate9(SDK %u)\n", arg(c, 0));
        Obj *d = nobjs ? objs[0] : new_obj(K_D3D, vt_d3d);
        RET(d->guest, 1);
    }
    return 0;
}
