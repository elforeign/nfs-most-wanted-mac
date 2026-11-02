#pragma once
#include "host.h"
#define WD_STREAMS 16u
#define WD_TEXTURES 16u
#define WD_OBJECTS 256u
#define WD_PENDING 16u
#define WD_MAX_BYTES (16u*1024u*1024u)
#define WD_INVALID 0x8876086cu
#define WD_UNAVAILABLE 0x8876086au
#define WD_OOM 0x8007000eu
/* All snapshots own their bytes. No pointer below refers into guest memory. */
typedef struct {
    uint32_t id, generation, version, format, width, height, pitch;
    uint32_t offset, size;
    uint8_t *bytes;
} WinD3D9Bytes;
typedef struct {
    uint32_t rs[256], sampler[WD_TEXTURES][14], tss[8][33];
    uint32_t viewport[6], scissor[4];
    float vs_constants[256][4], ps_constants[224][4];
    uint32_t vs_version, ps_version;
} WinD3D9State;
typedef struct {
    uint32_t resource, offset, stride;
} WinD3D9Stream;
/* vertices[i].offset is the byte offset of the copied range inside the bound
 * buffer (or UP array); indices.offset likewise. Vertex ranges cover exactly
 * [base_vertex+min_vertex, +num_vertices); index ranges the indices read. */
typedef struct WinD3D9Snapshot {
    uint64_t serial;
    uint32_t device_id, device_generation;
    uint32_t topology, primitives, indexed, up;
    int32_t base_vertex;
    uint32_t min_vertex, num_vertices, start, index_format;
    WinD3D9State state;
    WinD3D9Bytes vs, ps, declaration, vertices[WD_STREAMS], indices, textures[WD_TEXTURES];
    uint32_t stride[WD_STREAMS], stream_offset[WD_STREAMS];
} WinD3D9Snapshot;
/* On true, submit owns snapshot until win_d3d9_complete, even across device
 * teardown. On false, the recorder destroys it and the draw returns
 * D3DERR_NOTAVAILABLE. submit may call win_d3d9_complete before returning. */
typedef int (*WinD3D9Submit)(void *, WinD3D9Snapshot *);
void win_d3d9_set_backend(WinD3D9Submit, void *);
void win_d3d9_complete(WinD3D9Snapshot *);
unsigned win_d3d9_pending(void);

enum WDKind { WD_FACTORY=1, WD_DEVICE, WD_TEXTURE, WD_VB, WD_IB, WD_DECL, WD_VS, WD_PS };
typedef struct WDDevice {
    WinD3D9State state;
    uint32_t vs, ps, declaration, indices, textures[WD_TEXTURES];
    WinD3D9Stream streams[WD_STREAMS];
    uint32_t scene;
} WDDevice;
/* refs is the public COM count. binds counts device bindings, which keep the
 * data alive but are invisible to AddRef/Release, as in D3D9. The parent
 * (factory or device) reference is held only while refs is nonzero. */
typedef struct WDObject {
    uint32_t kind, guest, id, generation, refs, binds, parent;
    uint32_t data, size, width, height, pitch, format, usage, pool, fvf, version;
    uint32_t locked, lock_flags;
    WDDevice *device;
} WDObject;
WDObject *wd_object(uint32_t);
WDObject *wd_live(uint32_t);
WDObject *wd_new(uint32_t, uint32_t, uint32_t);
uint32_t wd_addref(uint32_t);
uint32_t wd_release(uint32_t);
unsigned win_d3d9_live_objects(void);
void wd_defaults(WDDevice *, uint32_t, uint32_t, int);
void wd_bind(uint32_t *, uint32_t);
uint32_t wd_draw(WDObject *, uint32_t, uint32_t, int, int32_t, uint32_t,
                 uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t);
