// com.cpp - guest COM objects, vtables and the three IUnknown slots.
#include "com.h"
#include "../runtime/memory.h"

#include <string.h>
#include <deque>

std::deque<std::unique_ptr<ComObj>> *g_com_objects = nullptr; // see com.h com_get

namespace {

// Objects are allocated one by one and owned by a deque, so a pointer stays valid while another object is
// created during a shim call (CreateSurface makes a back buffer, and the caller still holds the ComObj* for the
// primary).
std::deque<std::unique_ptr<ComObj>> &objects() {
    static auto *v = g_com_objects = new std::deque<std::unique_ptr<ComObj>>();
    return *v;
}

// Test103 (review memory gate): a dead object used to stay in the table for the rest of the run - 3,968 bytes
// each plus whatever its members held, with NFSMW creating roughly one per frame (about 1 GB in an hour). Dead
// ids wait here, oldest first, and are freed once COM_GRAVEYARD newer objects have died, so nothing destroyed
// recently (a caller in the same shim call may still hold its pointer) is ever freed under it. Guest threads run
// one at a time (the scheduler baton), so this needs no lock, like the table itself.
constexpr size_t COM_GRAVEYARD = 1024;
std::deque<uint32_t> &graveyard() {
    static auto *v = new std::deque<uint32_t>();
    return *v;
}
uint64_t g_com_freed = 0;
void bury(uint32_t id) {
    graveyard().push_back(id);
    while (graveyard().size() > COM_GRAVEYARD) {
        const uint32_t old = graveyard().front();
        graveyard().pop_front();
        if (old && old <= objects().size()) {
            std::unique_ptr<ComObj> &slot = objects()[old - 1];
            if (slot && !slot->alive) {
                slot.reset();
                ++g_com_freed;
            }
        }
    }
}

uint32_t g_vtable[IF_COUNT] = {0};
uint64_t g_kind_mask[IF_COUNT] = {0}; // bit per ComKind
void (*g_dtor[64])(ComObj *) = {nullptr};
ComQiHook g_qi_hook[64] = {nullptr};
const char *g_iface_name[IF_COUNT] = {nullptr};

struct IidEntry {
    uint8_t iid[16];
    ComIface iface;
};
std::vector<IidEntry> &iids() {
    static auto *v = new std::vector<IidEntry>();
    return *v;
}

// The methods a vtable was built from, kept so com_reset can rebuild the
// guest-side allocation without the caller re-registering.
struct VtDef {
    ComIface iface;
    std::string dll, name;
    std::vector<ComMethod> methods;
};
std::vector<VtDef> &vtdefs() {
    static auto *v = new std::vector<VtDef>();
    return *v;
}

uint32_t build_vtable(const VtDef &d) {
    uint32_t vt = heap_alloc((uint32_t)d.methods.size() * 4, true, 16);
    if (!vt) {
        LOGW("dx: cannot allocate a %zu-slot vtable for %s", d.methods.size(), d.name.c_str());
        return 0;
    }
    for (size_t i = 0; i < d.methods.size(); ++i) {
        const ComMethod &m = d.methods[i];
        std::string full = d.name + "::" + m.name;
        uint32_t t = imports_alloc_trampoline(d.dll.c_str(), full.c_str(), m.fn, m.argc);
        if (!t)
            LOGW("dx: no trampoline for %s", full.c_str());
        wr32(vt + 4 * (uint32_t)i, t);
    }
    return vt;
}

} // namespace

// ---------------------------------------------------------------------------
// Lifetime
// ---------------------------------------------------------------------------
void com_reset() {
    objects().clear();
    graveyard().clear();
    g_com_freed = 0;
    // Objects held guest heap blocks that mem_init() already discarded; the
    // vtables did too, so rebuild them at their new addresses.
    for (uint32_t i = 0; i < IF_COUNT; ++i)
        g_vtable[i] = 0;
    for (const VtDef &d : vtdefs())
        g_vtable[d.iface] = build_vtable(d);
}

ComObj *com_new(ComKind kind) {
    objects().push_back(std::make_unique<ComObj>());
    ComObj &o = *objects().back();
    o.id = (uint32_t)objects().size(); // ids are 1-based; 0 means none
    o.kind = kind;
    o.refs = 1;
    o.alive = true;
    return &o;
}


ComIface com_iface_of(uint32_t addr) {
    if (!addr || !gm_valid(addr, COM_VIEW_SIZE))
        return IF_NONE;
    if (rd32(addr + COM_OFF_magic) != COM_MAGIC)
        return IF_NONE;
    uint32_t f = rd32(addr + COM_OFF_iface);
    return f < IF_COUNT ? (ComIface)f : IF_NONE;
}

ComObj *com_this(uint32_t addr, ComIface want) {
    if (!addr || !gm_valid(addr, COM_VIEW_SIZE))
        return nullptr;
    if (rd32(addr + COM_OFF_magic) != COM_MAGIC) {
        log_once("dx.badthis", "dx: COM call on %08x, which is not a shim interface pointer", addr);
        return nullptr;
    }
    ComObj *o = com_get(rd32(addr + COM_OFF_obj));
    if (!o)
        return nullptr;
    if (want != IF_NONE && rd32(addr + COM_OFF_iface) != want)
        return nullptr;
    return o;
}

ComObj *com_this_arg(X86 *c, ComIface want) {
    return com_this(arg(c, 0), want);
}

uint32_t com_view(ComObj *o, ComIface iface) {
    if (!o || iface == IF_NONE || iface >= IF_COUNT)
        return 0;
    if (o->views[iface])
        return o->views[iface];
    uint32_t vt = g_vtable[iface];
    if (!vt) {
        LOGW("dx: %s has no vtable; was com_define called?", com_iface_name(iface));
        return 0;
    }
    // NFSMW issue 8 (3.0.3): a DirectSound buffer's view gets a sealed page of its own; a guest write to it is stopped
    // and logged (heap_alloc_readonly), so the game keeps calling a view the kit made. Others as before.
    const bool sealed = iface == IF_DSBUFFER && heap_readonly_enabled();
    uint32_t a = sealed ? heap_alloc_readonly(COM_VIEW_SIZE, "dsound buffer object", o->id) : 0;
    if (!a)
        a = heap_alloc(COM_VIEW_SIZE, true, 16);
    if (!a) {
        LOGW("dx: out of guest memory for a %s view", com_iface_name(iface));
        return 0;
    }
    wr32(a + COM_OFF_vtbl, vt);
    wr32(a + COM_OFF_magic, COM_MAGIC);
    wr32(a + COM_OFF_obj, o->id);
    wr32(a + COM_OFF_iface, iface);
    if (sealed)
        heap_seal_readonly(a); // a no-op for a plain block (the table was full)
    o->views[iface] = a;
    if (!o->identity)
        o->identity = a; // fixed for the object's lifetime
    return a;
}

// ---------------------------------------------------------------------------
// COM classes
// ---------------------------------------------------------------------------
namespace {
struct ComClass {
    uint8_t clsid[16];
    const char *name;
    ComIface primary;
    ComObj *(*create)();
};
std::vector<ComClass> &classes() {
    static auto *v = new std::vector<ComClass>();
    return *v;
}
static const uint8_t kIidUnknown[16] = {0, 0, 0, 0, 0, 0, 0, 0, 0xc0, 0, 0, 0, 0, 0, 0, 0x46};

// CoCreateInstance(rclsid, pUnkOuter, dwClsContext, riid, ppv)
void CoCreateInstance(X86 *c) {
    uint32_t clsid = arg(c, 0), outer = arg(c, 1), riid = arg(c, 3), out = arg(c, 4);
    if (!out || !gm_valid(out, 4)) {
        com_ret(c, E_POINTER);
        return;
    }
    wr32(out, 0);
    if (!clsid || !gm_valid(clsid, 16) || !riid || !gm_valid(riid, 16)) {
        com_ret(c, E_INVALIDARG);
        return;
    }
    const ComClass *cls = nullptr;
    for (const ComClass &k : classes())
        if (memcmp(gm_ptr(clsid), k.clsid, 16) == 0)
            cls = &k;
    if (!cls) {
        log_once("ole32.cocreate", "CoCreateInstance: class %08x-... is not registered here",
                 rd32(clsid));
        com_ret(c, 0x80040154u); // REGDB_E_CLASSNOTREG
        return;
    }
    if (outer) {
        com_ret(c, CLASS_E_NOAGGREGATION);
        return;
    }
    ComIface want = com_iface_for_iid(riid);
    if (want == IF_NONE && memcmp(gm_ptr(riid), kIidUnknown, 16) == 0)
        want = cls->primary;
    ComObj *o = cls->create();
    if (!o) {
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    if (want == IF_NONE || !com_iface_binds(want, o->kind)) {
        com_release(o);
        com_ret(c, E_NOINTERFACE);
        return;
    }
    uint32_t view = com_view(o, want);
    if (!view) {
        com_release(o);
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    wr32(out, view);
    LOGV("com: CoCreateInstance(%s) -> %08x as %s", cls->name, view, com_iface_name(want));
    com_ret(c, S_OK);
}
} // namespace

void com_register_class(const uint8_t clsid[16], const char *name, ComIface primary,
                        ComObj *(*create)()) {
    for (ComClass &k : classes()) {
        if (memcmp(k.clsid, clsid, 16) == 0) {
            k.name = name;
            k.primary = primary;
            k.create = create;
            return;
        }
    }
    ComClass k;
    memcpy(k.clsid, clsid, 16);
    k.name = name;
    k.primary = primary;
    k.create = create;
    classes().push_back(k);
}

void com_register_ole32() {
    static const ImportShim shims[] = {
        {"ole32.dll", "CoCreateInstance", 5, CoCreateInstance},
    };
    imports_register(shims, std::size(shims));
}

bool com_iface_binds(ComIface iface, ComKind kind) {
    return (size_t)iface < IF_COUNT && (g_kind_mask[iface] & (uint64_t(1) << (unsigned)kind)) != 0;
}

void com_set_destructor(ComKind kind, void (*fn)(ComObj *)) {
    if ((size_t)kind < 64)
        g_dtor[kind] = fn;
}

void com_addref(ComObj *o) {
    if (o)
        ++o->refs;
}

int32_t com_release(ComObj *o) {
    if (!o || !o->alive)
        return 0;
    if (--o->refs > 0)
        return o->refs;
    if (o->refs < 0) {
        LOGW("dx: over-release of object %u (kind %u)", o->id, (unsigned)o->kind);
        o->refs = 0;
    }
    com_destroy(o);
    return 0;
}

void com_destroy(ComObj *o) {
    if (!o || !o->alive)
        return;
    o->refs = 0;
    if ((size_t)o->kind < 64 && g_dtor[o->kind])
        g_dtor[o->kind](o);
    for (uint32_t i = 0; i < IF_COUNT; ++i) {
        if (o->views[i]) {
            heap_free_guarded(o->views[i]); // a sealed view is opened first; a plain one is freed as before
            o->views[i] = 0;
        }
    }
    o->alive = false;
    bury(o->id);
}

// ---------------------------------------------------------------------------
// Vtables and interface identity
// ---------------------------------------------------------------------------
uint32_t com_define(ComIface iface, const char *dll, const char *iface_name,
                    const ComMethod *methods, size_t count) {
    VtDef d;
    d.iface = iface;
    d.dll = dll;
    d.name = iface_name;
    d.methods.assign(methods, methods + count);
    // Re-defining an interface replaces the old definition rather than
    // stacking a second one, so a second dx_register_shims() is a no-op.
    for (VtDef &e : vtdefs()) {
        if (e.iface == iface) {
            e = d;
            g_vtable[iface] = build_vtable(d);
            g_iface_name[iface] = iface_name;
            return g_vtable[iface];
        }
    }
    vtdefs().push_back(d);
    g_iface_name[iface] = iface_name;
    g_vtable[iface] = build_vtable(vtdefs().back());
    return g_vtable[iface];
}

uint32_t com_vtable_of(ComIface iface) {
    return iface < IF_COUNT ? g_vtable[iface] : 0;
}

void com_bind(ComIface iface, ComKind kind) {
    if (iface < IF_COUNT && (uint32_t)kind < 64)
        g_kind_mask[iface] |= uint64_t(1) << (uint32_t)kind;
}

const char *com_iface_name(ComIface iface) {
    if (iface < IF_COUNT && g_iface_name[iface])
        return g_iface_name[iface];
    return "<unknown interface>";
}

void com_register_iid(ComIface iface, const uint8_t iid[16]) {
    for (IidEntry &e : iids()) {
        if (memcmp(e.iid, iid, 16) == 0) {
            e.iface = iface;
            return;
        }
    }
    IidEntry e;
    memcpy(e.iid, iid, 16);
    e.iface = iface;
    iids().push_back(e);
}

// ---------------------------------------------------------------------------
// Every IID the DirectX 6/7 headers define, with its interface name. This
// table is not a dispatch table: com_iface_for_iid decides what is
// implemented. It exists so that a QueryInterface this shim cannot satisfy
// names the interface the caller wanted, which is the difference between a
// log line that is a bug report and one that is a hex dump.
//
// Generated from the DirectX SDK headers (ddraw.h, d3d.h, dsound.h, dinput.h,
// dplay.h, dplobby.h) and cross-checked against Wine's, so a name here is the
// SDK's name for that byte sequence, not a guess at one.
// ---------------------------------------------------------------------------
namespace {
struct KnownIid {
    uint8_t iid[16];
    const char *name;
};
const KnownIid g_known_iids[] = {
    {{0x80, 0xdb, 0x14, 0x6c, 0x33, 0xa7, 0xce, 0x11, 0xa5, 0x21, 0x00, 0x20, 0xaf, 0x0b, 0xe5,
      0x60},
     "IDirectDraw"},
    {{0xe0, 0xf3, 0xa6, 0xb3, 0x43, 0x2b, 0xcf, 0x11, 0xa2, 0xde, 0x00, 0xaa, 0x00, 0xb9, 0x33,
      0x56},
     "IDirectDraw2"},
    {{0x9a, 0x50, 0x59, 0x9c, 0xbd, 0x39, 0xd1, 0x11, 0x8c, 0x4a, 0x00, 0xc0, 0x4f, 0xd9, 0x30,
      0xc5},
     "IDirectDraw4"},
    {{0xc0, 0x5e, 0xe6, 0x15, 0x9c, 0x3b, 0xd2, 0x11, 0xb9, 0x2f, 0x00, 0x60, 0x97, 0x97, 0xea,
      0x5b},
     "IDirectDraw7"},
    {{0x81, 0xdb, 0x14, 0x6c, 0x33, 0xa7, 0xce, 0x11, 0xa5, 0x21, 0x00, 0x20, 0xaf, 0x0b, 0xe5,
      0x60},
     "IDirectDrawSurface"},
    {{0x85, 0x58, 0x80, 0x57, 0xec, 0x6e, 0xcf, 0x11, 0x94, 0x41, 0xa8, 0x23, 0x03, 0xc1, 0x0e,
      0x27},
     "IDirectDrawSurface2"},
    {{0x00, 0x4e, 0x04, 0xda, 0xb2, 0x69, 0xd0, 0x11, 0xa1, 0xd5, 0x00, 0xaa, 0x00, 0xb8, 0xdf,
      0xbb},
     "IDirectDrawSurface3"},
    {{0x30, 0x86, 0x2b, 0x0b, 0x35, 0xad, 0xd0, 0x11, 0x8e, 0xa6, 0x00, 0x60, 0x97, 0x97, 0xea,
      0x5b},
     "IDirectDrawSurface4"},
    {{0x80, 0x5a, 0x67, 0x06, 0x9b, 0x3b, 0xd2, 0x11, 0xb9, 0x2f, 0x00, 0x60, 0x97, 0x97, 0xea,
      0x5b},
     "IDirectDrawSurface7"},
    {{0x84, 0xdb, 0x14, 0x6c, 0x33, 0xa7, 0xce, 0x11, 0xa5, 0x21, 0x00, 0x20, 0xaf, 0x0b, 0xe5,
      0x60},
     "IDirectDrawPalette"},
    {{0x85, 0xdb, 0x14, 0x6c, 0x33, 0xa7, 0xce, 0x11, 0xa5, 0x21, 0x00, 0x20, 0xaf, 0x0b, 0xe5,
      0x60},
     "IDirectDrawClipper"},
    {{0xe0, 0x0e, 0x9f, 0x4b, 0x7e, 0x0d, 0xd0, 0x11, 0x9b, 0x06, 0x00, 0xa0, 0xc9, 0x03, 0xa3,
      0xb8},
     "IDirectDrawColorControl"},
    {{0x3e, 0x1c, 0xc1, 0x69, 0x6b, 0xb4, 0xd1, 0x11, 0xad, 0x7a, 0x00, 0xc0, 0x4f, 0xc2, 0x9b,
      0x4e},
     "IDirectDrawGammaControl"},
    {{0x80, 0x00, 0xba, 0x3b, 0x21, 0x24, 0xcf, 0x11, 0xa3, 0x1a, 0x00, 0xaa, 0x00, 0xb9, 0x33,
      0x56},
     "IDirect3D"},
    {{0xc1, 0x1e, 0xae, 0x6a, 0x2a, 0x66, 0xd0, 0x11, 0x88, 0x9d, 0x00, 0xaa, 0x00, 0xbb, 0xb7,
      0x6a},
     "IDirect3D2"},
    {{0x40, 0x32, 0x22, 0xbb, 0x2b, 0xe7, 0xd0, 0x11, 0xa9, 0xb4, 0x00, 0xaa, 0x00, 0xc0, 0x99,
      0x3e},
     "IDirect3D3"},
    {{0x77, 0x9e, 0x04, 0xf5, 0x61, 0x48, 0xd2, 0x11, 0xa4, 0x07, 0x00, 0xa0, 0xc9, 0x06, 0x29,
      0xa8},
     "IDirect3D7"},
    {{0x20, 0x6b, 0x08, 0xf2, 0x9f, 0x25, 0xcf, 0x11, 0xa3, 0x1a, 0x00, 0xaa, 0x00, 0xb9, 0x33,
      0x56},
     "IDirect3DRampDevice"},
    {{0x60, 0x5c, 0x66, 0xa4, 0x73, 0x26, 0xcf, 0x11, 0xa3, 0x1a, 0x00, 0xaa, 0x00, 0xb9, 0x33,
      0x56},
     "IDirect3DRGBDevice"},
    {{0xe0, 0x3d, 0xe6, 0x84, 0xaa, 0x46, 0xcf, 0x11, 0x81, 0x6f, 0x00, 0x00, 0xc0, 0x20, 0x15,
      0x6e},
     "IDirect3DHALDevice"},
    {{0xa1, 0x49, 0x19, 0x88, 0xf3, 0xd6, 0xd0, 0x11, 0x89, 0xab, 0x00, 0xa0, 0xc9, 0x05, 0x41,
      0x29},
     "IDirect3DMMXDevice"},
    {{0x43, 0x66, 0x93, 0x50, 0xe9, 0x13, 0xd1, 0x11, 0x89, 0xaa, 0x00, 0xa0, 0xc9, 0x05, 0x41,
      0x29},
     "IDirect3DRefDevice"},
    {{0x22, 0xdf, 0x67, 0x87, 0xcc, 0xba, 0xd1, 0x11, 0x89, 0x69, 0x00, 0xa0, 0xc9, 0x06, 0x29,
      0xa8},
     "IDirect3DNullDevice"},
    {{0x78, 0x9e, 0x04, 0xf5, 0x61, 0x48, 0xd2, 0x11, 0xa4, 0x07, 0x00, 0xa0, 0xc9, 0x06, 0x29,
      0xa8},
     "IDirect3DTnLHalDevice"},
    {{0x00, 0x88, 0x10, 0x64, 0x7d, 0x95, 0xd0, 0x11, 0x89, 0xab, 0x00, 0xa0, 0xc9, 0x05, 0x41,
      0x29},
     "IDirect3DDevice"},
    {{0x01, 0x15, 0x28, 0x93, 0xf8, 0x8c, 0xd0, 0x11, 0x89, 0xab, 0x00, 0xa0, 0xc9, 0x05, 0x41,
      0x29},
     "IDirect3DDevice2"},
    {{0x60, 0x3b, 0xab, 0xb0, 0xd7, 0x33, 0xd1, 0x11, 0xa9, 0x81, 0x00, 0xc0, 0x4f, 0xd7, 0xb1,
      0x74},
     "IDirect3DDevice3"},
    {{0x79, 0x9e, 0x04, 0xf5, 0x61, 0x48, 0xd2, 0x11, 0xa4, 0x07, 0x00, 0xa0, 0xc9, 0x06, 0x29,
      0xa8},
     "IDirect3DDevice7"},
    {{0xe0, 0xd9, 0xdc, 0x2c, 0xa0, 0x25, 0xcf, 0x11, 0xa3, 0x1a, 0x00, 0xaa, 0x00, 0xb9, 0x33,
      0x56},
     "IDirect3DTexture"},
    {{0x02, 0x15, 0x28, 0x93, 0xf8, 0x8c, 0xd0, 0x11, 0x89, 0xab, 0x00, 0xa0, 0xc9, 0x05, 0x41,
      0x29},
     "IDirect3DTexture2"},
    {{0x42, 0xc1, 0x17, 0x44, 0xad, 0x33, 0xcf, 0x11, 0x81, 0x6f, 0x00, 0x00, 0xc0, 0x20, 0x15,
      0x6e},
     "IDirect3DLight"},
    {{0x44, 0xc1, 0x17, 0x44, 0xad, 0x33, 0xcf, 0x11, 0x81, 0x6f, 0x00, 0x00, 0xc0, 0x20, 0x15,
      0x6e},
     "IDirect3DMaterial"},
    {{0x03, 0x15, 0x28, 0x93, 0xf8, 0x8c, 0xd0, 0x11, 0x89, 0xab, 0x00, 0xa0, 0xc9, 0x05, 0x41,
      0x29},
     "IDirect3DMaterial2"},
    {{0xf4, 0x46, 0x9c, 0xca, 0xc5, 0xd3, 0xd1, 0x11, 0xb7, 0x5a, 0x00, 0x60, 0x08, 0x52, 0xb3,
      0x12},
     "IDirect3DMaterial3"},
    {{0x45, 0xc1, 0x17, 0x44, 0xad, 0x33, 0xcf, 0x11, 0x81, 0x6f, 0x00, 0x00, 0xc0, 0x20, 0x15,
      0x6e},
     "IDirect3DExecuteBuffer"},
    {{0x46, 0xc1, 0x17, 0x44, 0xad, 0x33, 0xcf, 0x11, 0x81, 0x6f, 0x00, 0x00, 0xc0, 0x20, 0x15,
      0x6e},
     "IDirect3DViewport"},
    {{0x00, 0x15, 0x28, 0x93, 0xf8, 0x8c, 0xd0, 0x11, 0x89, 0xab, 0x00, 0xa0, 0xc9, 0x05, 0x41,
      0x29},
     "IDirect3DViewport2"},
    {{0x61, 0x3b, 0xab, 0xb0, 0xd7, 0x33, 0xd1, 0x11, 0xa9, 0x81, 0x00, 0xc0, 0x4f, 0xd7, 0xb1,
      0x74},
     "IDirect3DViewport3"},
    {{0x55, 0x35, 0x50, 0x7a, 0x83, 0x4a, 0xd1, 0x11, 0xa5, 0xdb, 0x00, 0xa0, 0xc9, 0x03, 0x67,
      0xf8},
     "IDirect3DVertexBuffer"},
    {{0x7d, 0x9e, 0x04, 0xf5, 0x61, 0x48, 0xd2, 0x11, 0xa4, 0x07, 0x00, 0xa0, 0xc9, 0x06, 0x29,
      0xa8},
     "IDirect3DVertexBuffer7"},
    {{0x97, 0x68, 0xa8, 0x56, 0xd4, 0x0a, 0xce, 0x11, 0xb0, 0x3a, 0x00, 0x20, 0xaf, 0x0b, 0xa7,
      0x70},
     "IReferenceClock"},
    {{0x83, 0xfa, 0x9a, 0x27, 0x81, 0x49, 0xce, 0x11, 0xa5, 0x21, 0x00, 0x20, 0xaf, 0x0b, 0xe5,
      0x60},
     "IDirectSound"},
    {{0x93, 0x7e, 0x0a, 0xc5, 0x95, 0xf3, 0x34, 0x48, 0x9e, 0xf6, 0x7f, 0xa9, 0x9d, 0xe5, 0x09,
      0x66},
     "IDirectSound8"},
    {{0x85, 0xfa, 0x9a, 0x27, 0x81, 0x49, 0xce, 0x11, 0xa5, 0x21, 0x00, 0x20, 0xaf, 0x0b, 0xe5,
      0x60},
     "IDirectSoundBuffer"},
    {{0x49, 0xa4, 0x25, 0x68, 0x24, 0x75, 0x82, 0x4d, 0x92, 0x0f, 0x50, 0xe3, 0x6a, 0xb3, 0xab,
      0x1e},
     "IDirectSoundBuffer8"},
    {{0x84, 0xfa, 0x9a, 0x27, 0x81, 0x49, 0xce, 0x11, 0xa5, 0x21, 0x00, 0x20, 0xaf, 0x0b, 0xe5,
      0x60},
     "IDirectSound3DListener"},
    {{0x86, 0xfa, 0x9a, 0x27, 0x81, 0x49, 0xce, 0x11, 0xa5, 0x21, 0x00, 0x20, 0xaf, 0x0b, 0xe5,
      0x60},
     "IDirectSound3DBuffer"},
    {{0x81, 0x07, 0x21, 0xb0, 0xcd, 0x89, 0xd0, 0x11, 0xaf, 0x08, 0x00, 0xa0, 0xc9, 0x25, 0xcd,
      0x16},
     "IDirectSoundCapture"},
    {{0x82, 0x07, 0x21, 0xb0, 0xcd, 0x89, 0xd0, 0x11, 0xaf, 0x08, 0x00, 0xa0, 0xc9, 0x25, 0xcd,
      0x16},
     "IDirectSoundCaptureBuffer"},
    {{0xf4, 0x0d, 0x99, 0x00, 0xbb, 0x0d, 0x72, 0x48, 0x83, 0x3e, 0x6d, 0x30, 0x3e, 0x80, 0xae,
      0xb6},
     "IDirectSoundCaptureBuffer8"},
    {{0x83, 0x07, 0x21, 0xb0, 0xcd, 0x89, 0xd0, 0x11, 0xaf, 0x08, 0x00, 0xa0, 0xc9, 0x25, 0xcd,
      0x16},
     "IDirectSoundNotify"},
    {{0x30, 0xac, 0xef, 0x31, 0x5c, 0x51, 0xd0, 0x11, 0xa9, 0xaa, 0x00, 0xaa, 0x00, 0x61, 0xbe,
      0x93},
     "IKsPropertySet"},
    {{0x52, 0xf3, 0x16, 0xd6, 0x22, 0xd6, 0xce, 0x11, 0xaa, 0xc5, 0x00, 0x20, 0xaf, 0x0b, 0x99,
      0xa3},
     "IDirectSoundFXGargle"},
    {{0xe3, 0x42, 0x08, 0x88, 0x5f, 0x14, 0xe6, 0x43, 0xa9, 0x34, 0xa7, 0x18, 0x06, 0xe5, 0x05,
      0x47},
     "IDirectSoundFXChorus"},
    {{0x78, 0x98, 0x3e, 0x90, 0x92, 0x2c, 0x72, 0x40, 0x9b, 0x2c, 0xea, 0x68, 0xf5, 0x39, 0x67,
      0x83},
     "IDirectSoundFXFlanger"},
    {{0xdf, 0x8e, 0xd2, 0x8b, 0xdb, 0x50, 0x92, 0x4e, 0xa2, 0xbd, 0x44, 0x54, 0x88, 0xd1, 0xed,
      0x42},
     "IDirectSoundFXEcho"},
    {{0x26, 0x43, 0xcf, 0x8e, 0x5f, 0x45, 0x8b, 0x4d, 0xbd, 0xa9, 0x8d, 0x5d, 0x3e, 0x9e, 0x3e,
      0x0b},
     "IDirectSoundFXDistortion"},
    {{0x54, 0x11, 0xbd, 0x4b, 0xf6, 0x62, 0x2c, 0x4e, 0xa1, 0x5c, 0xd3, 0xb6, 0xc4, 0x17, 0xf7,
      0xa0},
     "IDirectSoundFXCompressor"},
    {{0xfe, 0xa9, 0x3c, 0xc0, 0x90, 0xfe, 0x04, 0x42, 0x80, 0x78, 0x82, 0x33, 0x4c, 0xd1, 0x77,
      0xda},
     "IDirectSoundFXParamEq"},
    {{0x6a, 0x6a, 0x16, 0x4b, 0x66, 0x0d, 0xf3, 0x43, 0x80, 0xe3, 0xee, 0x62, 0x80, 0xde, 0xe1,
      0xa4},
     "IDirectSoundFXI3DL2Reverb"},
    {{0x3a, 0x8c, 0x85, 0x46, 0xc6, 0x0d, 0xe3, 0x45, 0xb7, 0x60, 0xd4, 0xee, 0xf1, 0x6c, 0xb3,
      0x25},
     "IDirectSoundFXWavesReverb"},
    {{0x3d, 0x14, 0x74, 0xad, 0x3d, 0x90, 0xb7, 0x4a, 0x80, 0x66, 0x28, 0xd3, 0x63, 0x03, 0x6d,
      0x65},
     "IDirectSoundCaptureFXAec"},
    {{0x41, 0x1e, 0x31, 0xed, 0xae, 0xfb, 0x75, 0x41, 0x96, 0x25, 0xcd, 0x08, 0x54, 0xf6, 0x93,
      0xca},
     "IDirectSoundCaptureFXNoiseSuppress"},
    {{0x7a, 0x4c, 0xcb, 0xed, 0xab, 0xda, 0x16, 0x42, 0xa4, 0x2e, 0x6c, 0x50, 0x59, 0x6d, 0xdc,
      0x1d},
     "IDirectSoundFullDuplex"},
    {{0x60, 0x13, 0x52, 0x89, 0x8a, 0xaa, 0xcf, 0x11, 0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00,
      0x00},
     "IDirectInputA"},
    {{0x61, 0x13, 0x52, 0x89, 0x8a, 0xaa, 0xcf, 0x11, 0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00,
      0x00},
     "IDirectInputW"},
    {{0x62, 0xe6, 0x44, 0x59, 0x8a, 0xaa, 0xcf, 0x11, 0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00,
      0x00},
     "IDirectInput2A"},
    {{0x63, 0xe6, 0x44, 0x59, 0x8a, 0xaa, 0xcf, 0x11, 0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00,
      0x00},
     "IDirectInput2W"},
    {{0x84, 0xb6, 0x4c, 0x9a, 0x6d, 0x23, 0xd3, 0x11, 0x8e, 0x9d, 0x00, 0xc0, 0x4f, 0x68, 0x44,
      0xae},
     "IDirectInput7A"},
    {{0x85, 0xb6, 0x4c, 0x9a, 0x6d, 0x23, 0xd3, 0x11, 0x8e, 0x9d, 0x00, 0xc0, 0x4f, 0x68, 0x44,
      0xae},
     "IDirectInput7W"},
    {{0x30, 0x80, 0x79, 0xbf, 0x3a, 0x48, 0xa2, 0x4d, 0xaa, 0x99, 0x5d, 0x64, 0xed, 0x36, 0x97,
      0x00},
     "IDirectInput8A"},
    {{0x31, 0x80, 0x79, 0xbf, 0x3a, 0x48, 0xa2, 0x4d, 0xaa, 0x99, 0x5d, 0x64, 0xed, 0x36, 0x97,
      0x00},
     "IDirectInput8W"},
    {{0x80, 0xe6, 0x44, 0x59, 0x2e, 0xc9, 0xcf, 0x11, 0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00,
      0x00},
     "IDirectInputDeviceA"},
    {{0x81, 0xe6, 0x44, 0x59, 0x2e, 0xc9, 0xcf, 0x11, 0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00,
      0x00},
     "IDirectInputDeviceW"},
    {{0x82, 0xe6, 0x44, 0x59, 0x2e, 0xc9, 0xcf, 0x11, 0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00,
      0x00},
     "IDirectInputDevice2A"},
    {{0x83, 0xe6, 0x44, 0x59, 0x2e, 0xc9, 0xcf, 0x11, 0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00,
      0x00},
     "IDirectInputDevice2W"},
    {{0xbc, 0xc6, 0xd7, 0x57, 0x56, 0x23, 0xd3, 0x11, 0x8e, 0x9d, 0x00, 0xc0, 0x4f, 0x68, 0x44,
      0xae},
     "IDirectInputDevice7A"},
    {{0xbd, 0xc6, 0xd7, 0x57, 0x56, 0x23, 0xd3, 0x11, 0x8e, 0x9d, 0x00, 0xc0, 0x4f, 0x68, 0x44,
      0xae},
     "IDirectInputDevice7W"},
    {{0x80, 0x10, 0xd4, 0x54, 0x15, 0xdc, 0x33, 0x48, 0xa4, 0x1b, 0x74, 0x8f, 0x73, 0xa3, 0x81,
      0x79},
     "IDirectInputDevice8A"},
    {{0x81, 0x10, 0xd4, 0x54, 0x15, 0xdc, 0x33, 0x48, 0xa4, 0x1b, 0x74, 0x8f, 0x73, 0xa3, 0x81,
      0x79},
     "IDirectInputDevice8W"},
    {{0xc0, 0xf7, 0xe1, 0xe7, 0xd2, 0x88, 0xd0, 0x11, 0x9a, 0xd0, 0x00, 0xa0, 0xc9, 0xa0, 0x6e,
      0x35},
     "IDirectInputEffect"},
    {{0xc0, 0xf7, 0x74, 0x2b, 0x54, 0x91, 0xcf, 0x11, 0xa9, 0xcd, 0x00, 0xaa, 0x00, 0x68, 0x86,
      0xe3},
     "IDirectPlay2"},
    {{0x80, 0x05, 0x46, 0x9d, 0x22, 0xa8, 0xcf, 0x11, 0x96, 0x0c, 0x00, 0x80, 0xc7, 0x53, 0x4e,
      0x82},
     "IDirectPlay2A"},
    {{0x40, 0xfe, 0x3e, 0x13, 0xdc, 0x32, 0xd0, 0x11, 0x9c, 0xfb, 0x00, 0xa0, 0xc9, 0x0a, 0x43,
      0xcb},
     "IDirectPlay3"},
    {{0x41, 0xfe, 0x3e, 0x13, 0xdc, 0x32, 0xd0, 0x11, 0x9c, 0xfb, 0x00, 0xa0, 0xc9, 0x0a, 0x43,
      0xcb},
     "IDirectPlay3A"},
    {{0x30, 0xc5, 0xb1, 0x0a, 0x45, 0x47, 0xd1, 0x11, 0xa7, 0xa1, 0x00, 0x00, 0xf8, 0x03, 0xab,
      0xfc},
     "IDirectPlay4"},
    {{0x31, 0xc5, 0xb1, 0x0a, 0x45, 0x47, 0xd1, 0x11, 0xa7, 0xa1, 0x00, 0x00, 0xf8, 0x03, 0xab,
      0xfc},
     "IDirectPlay4A"},
    {{0xa0, 0xe9, 0x54, 0x54, 0x65, 0xdb, 0xce, 0x11, 0x92, 0x1c, 0x00, 0xaa, 0x00, 0x6c, 0x49,
      0x72},
     "IDirectPlay"},
    {{0x71, 0x5c, 0x46, 0xaf, 0x88, 0x95, 0xcf, 0x11, 0xa0, 0x20, 0x00, 0xaa, 0x00, 0x61, 0x57,
      0xac},
     "IDirectPlayLobby"},
    {{0x70, 0x6a, 0xc6, 0x26, 0x67, 0xb3, 0xcf, 0x11, 0xa0, 0x24, 0x00, 0xaa, 0x00, 0x61, 0x57,
      0xac},
     "IDirectPlayLobbyA"},
    {{0x20, 0xc2, 0x94, 0x01, 0x03, 0xa3, 0xd0, 0x11, 0x9c, 0x4f, 0x00, 0xa0, 0xc9, 0x05, 0x42,
      0x5e},
     "IDirectPlayLobby2"},
    {{0x80, 0xaf, 0xb4, 0x1b, 0x03, 0xa3, 0xd0, 0x11, 0x9c, 0x4f, 0x00, 0xa0, 0xc9, 0x05, 0x42,
      0x5e},
     "IDirectPlayLobby2A"},
    {{0x90, 0x24, 0xb7, 0x2d, 0x2c, 0x65, 0xd1, 0x11, 0xa7, 0xa8, 0x00, 0x00, 0xf8, 0x03, 0xab,
      0xfc},
     "IDirectPlayLobby3"},
    {{0x91, 0x24, 0xb7, 0x2d, 0x2c, 0x65, 0xd1, 0x11, 0xa7, 0xa8, 0x00, 0x00, 0xf8, 0x03, 0xab,
      0xfc},
     "IDirectPlayLobby3A"},
};

const char *known_iid_name(const uint8_t *p) {
    for (const KnownIid &k : g_known_iids)
        if (memcmp(k.iid, p, 16) == 0)
            return k.name;
    return nullptr;
}
} // namespace

ComIface com_iface_for_iid(uint32_t addr) {
    if (!addr || !gm_valid(addr, 16))
        return IF_NONE;
    const uint8_t *p = gm_ptr(addr);
    for (const IidEntry &e : iids())
        if (memcmp(e.iid, p, 16) == 0)
            return e.iface;
    return IF_NONE;
}

void com_set_qi_hook(ComKind kind, ComQiHook hook) {
    if ((size_t)kind < 64)
        g_qi_hook[kind] = hook;
}

// ---------------------------------------------------------------------------
// IUnknown
// ---------------------------------------------------------------------------
void com_QueryInterface(X86 *c) {
    ComObj *o = com_this_arg(c);
    uint32_t riid = arg(c, 1);
    uint32_t out = arg(c, 2);
    if (!o) {
        com_ret(c, E_FAIL);
        return;
    }
    if (!out || !gm_valid(out, 4)) {
        com_ret(c, E_POINTER);
        return;
    }
    wr32(out, 0);

    ComIface want = com_iface_for_iid(riid);
    if (want == IF_NONE) {
        // IID_IUnknown is not in the table: any interface satisfies it, and
        // the object's own primary view is the canonical answer.
        static const uint8_t iid_unknown[16] = {0,    0, 0, 0, 0, 0, 0, 0,
                                                0xc0, 0, 0, 0, 0, 0, 0, 0x46};
        if (riid && gm_valid(riid, 16) && memcmp(gm_ptr(riid), iid_unknown, 16) == 0) {
            // The controlling identity, fixed when the first view was made.
            // Scanning the view table instead would change the answer as soon
            // as a lower-numbered interface was queried, which breaks the COM
            // rule that two pointers name the same object exactly when their
            // IUnknowns are equal.
            if (o->identity) {
                wr32(out, o->identity);
                com_addref(o);
                com_ret(c, S_OK);
                return;
            }
        }
        if (riid && gm_valid(riid, 16)) {
            const uint8_t *g = gm_ptr(riid);
            uint32_t d1;
            uint16_t d2, d3;
            memcpy(&d1, g, 4);
            memcpy(&d2, g + 4, 2);
            memcpy(&d3, g + 6, 2);
            char key[64];
            snprintf(key, sizeof key, "qi.%08x%04x", d1, d2);
            const char *want_name = known_iid_name(g);
            // A refused QueryInterface is how a caller discovers what this
            // shim does not have, so it is logged unconditionally rather than
            // only under verbose: the name is the whole diagnosis.
            log_once(key,
                     "dx: %s does not implement %s "
                     "{%08X-%04X-%04X-%02X%02X-%02X%02X%02X%02X%02X%02X}: E_NOINTERFACE",
                     com_iface_name(com_iface_of(arg(c, 0))),
                     want_name ? want_name : "an interface outside the DirectX headers", d1, d2, d3,
                     g[8], g[9], g[10], g[11], g[12], g[13], g[14], g[15]);
        }
        com_ret(c, E_NOINTERFACE);
        return;
    }

    ComObj *target = o;
    if ((size_t)o->kind < 64 && g_qi_hook[o->kind]) {
        ComObj *alt = g_qi_hook[o->kind](o, want);
        if (alt)
            target = alt;
    }
    if ((uint32_t)target->kind < 64 &&
        !(g_kind_mask[want] & (uint64_t(1) << (uint32_t)target->kind))) {
        char key[64];
        snprintf(key, sizeof key, "qi.kind.%u.%u", (unsigned)target->kind, (unsigned)want);
        log_once(key, "dx: %s is not an interface on this %s object: E_NOINTERFACE",
                 com_iface_name(want), com_iface_name(com_iface_of(arg(c, 0))));
        com_ret(c, E_NOINTERFACE);
        return;
    }
    uint32_t view = com_view(target, want);
    if (!view) {
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    wr32(out, view);
    com_addref(target);
    com_ret(c, S_OK);
}

void com_AddRef(X86 *c) {
    ComObj *o = com_this_arg(c);
    if (!o) {
        com_ret(c, 0);
        return;
    }
    com_addref(o);
    com_ret(c, (uint32_t)o->refs);
}

void com_Release(X86 *c) {
    ComObj *o = com_this_arg(c);
    if (!o) {
        com_ret(c, 0);
        return;
    }
    com_ret(c, (uint32_t)com_release(o));
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
bool com_out_ptr(uint32_t out_addr, uint32_t value) {
    if (!out_addr || !gm_valid(out_addr, 4))
        return false;
    wr32(out_addr, value);
    return true;
}

void gm_zero(uint32_t addr, uint32_t n) {
    if (addr && gm_valid(addr, n))
        memset(gm_ptr(addr), 0, n);
}

void gm_copy(uint32_t dst, uint32_t src, uint32_t n) {
    if (dst && src && gm_valid(dst, n) && gm_valid(src, n))
        memmove(gm_ptr(dst), gm_ptr(src), n);
}

// The two generic slot bodies. A shim has no way to learn which trampoline
// invoked it, so these cannot name themselves; DX_STUB in com.h makes a named
// one-liner wherever the name is worth having in the log and in the import
// coverage report.
void com_stub_ok(X86 *c) {
    LOGV("dx: unimplemented COM slot returning S_OK");
    com_ret(c, S_OK);
}
void com_stub_notimpl(X86 *c) {
    LOGV("dx: unimplemented COM slot returning E_NOTIMPL");
    com_ret(c, E_NOTIMPL);
}

uint32_t com_object_count() {
    return (uint32_t)objects().size();
}

uint32_t com_live_count() {
    uint32_t n = 0;
    for (const std::unique_ptr<ComObj> &o : objects())
        if (o && o->alive)
            ++n;
    return n;
}

void com_dump(FILE *out) {
    static const char *kinds[] = {"none",      "ddraw",    "surface",  "palette", "clipper",
                                  "d3ddevice", "viewport", "material", "light",   "dsound",
                                  "dsbuffer",  "dinput",   "didevice"};
    fprintf(out, "live COM objects (%u of %zu created; %llu dead ones freed):\n", com_live_count(), objects().size(),
            (unsigned long long)g_com_freed);
    for (const std::unique_ptr<ComObj> &p : objects()) {
        if (!p || !p->alive)
            continue;
        const ComObj &o = *p;
        const char *k = (size_t)o.kind < sizeof kinds / sizeof *kinds ? kinds[o.kind] : "?";
        fprintf(out, "  #%-3u %-10s refs=%-3d", o.id, k, o.refs);
        if (o.kind == K_SURFACE)
            fprintf(out, " %ux%ux%u pitch=%u pixels=%08x caps=%08x%s", o.width, o.height, o.bpp,
                    o.pitch, o.pixels, o.caps, o.is_primary ? " primary" : "");
        fprintf(out, "\n");
    }
}
