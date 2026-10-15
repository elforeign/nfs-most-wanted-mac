// d3d9.cpp - Direct3D 9, for a game that renders through shaders.
//
// The kit's other Direct3D module (d3d.cpp) models version 2: a fixed-function
// recorder reached by QueryInterface on a DirectDraw object. Version 9 shares
// none of that. It is its own DLL with its own factory function, its own
// object model, and a device whose drawing is programmable, so it lives here.
//
// What works today: the factory object, everything a game asks it before it
// commits to a device (adapter count, identifier, display mode, device type
// and format checks, caps) and CreateDevice itself. The device's vtable is
// complete and correctly ordered - a guest calls these by slot index, so the
// order is the interface - but only the handful of methods below do anything;
// every other slot reports itself once and returns D3D_OK. That turns what
// was a crash into a list of exactly which device methods this game uses,
// which is what the drawing work needs next.
//
// Nothing here rasterizes. No pixels are produced by this file yet.
#include "com.h"
#include "dx.h"
#include "../runtime/guest.h"
#include "../runtime/memory.h"
#include "../runtime/display_seam.h"
#include "../runtime/win32.h"
#include "../platform/os.h"
#include "../platform/ab_phase.h"
#include "../platform/rprof.h"
#include "host_api.h"
#include "d3d9_pipeline.h"
#include "d3d9_raster.h"
#include "d3d9_shader.h"
#include "d3d9_volume.h"
#include "host_d9.h"
#include "d3d9_msaa.h"
#include <unordered_map>
#include <map>
#include <tuple>

#include <string.h>
#include <iterator>
#include <algorithm>
#include <initializer_list>

#define IID_BYTES(a, b, c, d0, d1, d2, d3, d4, d5, d6, d7)                                         \
    {(uint8_t)((a) & 0xff),                                                                        \
     (uint8_t)(((a) >> 8) & 0xff),                                                                 \
     (uint8_t)(((a) >> 16) & 0xff),                                                                \
     (uint8_t)(((a) >> 24) & 0xff),                                                                \
     (uint8_t)((b) & 0xff),                                                                        \
     (uint8_t)(((b) >> 8) & 0xff),                                                                 \
     (uint8_t)((c) & 0xff),                                                                        \
     (uint8_t)(((c) >> 8) & 0xff),                                                                 \
     d0,                                                                                           \
     d1,                                                                                           \
     d2,                                                                                           \
     d3,                                                                                           \
     d4,                                                                                           \
     d5,                                                                                           \
     d6,                                                                                           \
     d7}

// {81BDCBCA-64D4-426d-AE8D-AD0147F4275C} and {D0223B96-BF7A-43fd-92BD-A43B0D82B9EB}
static const uint8_t IID_IDirect3D9_[16] =
    IID_BYTES(0x81bdcbca, 0x64d4, 0x426d, 0xae, 0x8d, 0xad, 0x01, 0x47, 0xf4, 0x27, 0x5c);
static const uint8_t IID_IDirect3DDevice9_[16] =
    IID_BYTES(0xd0223b96, 0xbf7a, 0x43fd, 0x92, 0xbd, 0xa4, 0x3b, 0x0d, 0x82, 0xb9, 0xeb);

#define MAKE_D3DHRESULT(code) (0x88760000u | (uint32_t)(code))
static const uint32_t D3D_OK9 = 0u;
static const uint32_t D3DERR_NOTAVAILABLE = MAKE_D3DHRESULT(2154);
static const uint32_t D3DERR_INVALIDCALL = MAKE_D3DHRESULT(2156);
// The draw limits this device reports in its caps (GetDeviceCaps) and enforces in the four draw calls (Test155):
// one definition, so what the guest is told and what is enforced cannot drift apart.
static const uint32_t D9_MAX_PRIMITIVE_COUNT = 1048575;
static const uint32_t D9_MAX_VERTEX_INDEX = 1048575;
static const uint32_t D3DERR_WASSTILLDRAWING = MAKE_D3DHRESULT(540);
static const uint32_t D3DERR_NOTFOUND9 = MAKE_D3DHRESULT(2150);
static const uint32_t D3DLOCK_DONOTWAIT = 0x4000u;

// Defined with the resources further down.
struct ComObj;
static ComObj *device_backbuffer(ComObj *dev);
static ComObj *device_depthbuffer(ComObj *dev);
static void bind_color_target(ComObj *dev, uint32_t index, ComObj *s);
static bool gpu_on();
static HostD9Target gpu_target(ComObj *dev);
static void target_viewport(ComObj *dev, const HostD9Target &t, int32_t vp[4]);
static void gpu_target_drawn(ComObj *dev, bool color, bool depth);
static void gpu_sync(ComObj *o);

// One scratch block for structures a method fills in for the guest.
static uint32_t g_scratch = 0, g_scratch_size = 0;
static uint32_t scratch9(uint32_t n) {
    if (g_scratch_size < n) {
        if (g_scratch)
            heap_free(g_scratch);
        g_scratch = heap_alloc(n, true, 16);
        g_scratch_size = g_scratch ? n : 0;
    }
    if (g_scratch)
        memset(gm_ptr(g_scratch), 0, g_scratch_size);
    return g_scratch;
}

static ComObj *this_d3d9(X86 *c) {
    ComObj *o = com_this_arg(c);
    return (o && o->kind == K_D3D9) ? o : nullptr;
}
static ComObj *this_device9(X86 *c) {
    ComObj *o = com_this_arg(c);
    return (o && o->kind == K_D3D9DEVICE) ? o : nullptr;
}

// ---------------------------------------------------------------------------
// IDirect3D9
// ---------------------------------------------------------------------------
// One adapter, the host window's display. A game that enumerates further gets
// an honest "there is only one".
void D9_RegisterSoftwareDevice(X86 *c) {
    com_ret(c, D3DERR_NOTAVAILABLE);
}
void D9_GetAdapterCount(X86 *c) {
    set_eax(c, 1);
}

// D3DADAPTER_IDENTIFIER9: two 512-byte strings, a 32-byte device name, the
// driver version, four ids, the identifier GUID and the WHQL level.
void D9_GetAdapterIdentifier(X86 *c) {
    uint32_t out = arg(c, 3);
    if (!out) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    memset(gm_ptr(out), 0, 1100);
    // Games of this era pick their feature set by vendor and device id, and
    // turn off whatever they do not recognise (Most Wanted offers shadows
    // only on NVIDIA and ATI). The renderer covers what a GeForce 7800 GTX
    // offered, hardware shadow maps included, so that is the card it names.
    gm_put_str(out, "nv4_disp.dll", 512);
    gm_put_str(out + 512, "NVIDIA GeForce 7800 GTX", 512);
    gm_put_str(out + 1024, "\\\\.\\DISPLAY1", 32);
    // Driver[512], Description[512], DeviceName[32], then:
    wr32(out + 1056, 0x000a249b); // DriverVersion 6.14.10.9371 (low, high)
    wr32(out + 1060, 0x0006000e);
    wr32(out + 1064, 0x10de); // VendorId
    wr32(out + 1068, 0x0091); // DeviceId
    wr32(out + 1076, 0xa1);   // Revision
    wr32(out + 1096, 1);      // WHQLLevel: certified
    com_ret(c, D3D_OK9);
}

void D9_GetAdapterModeCount(X86 *c) {
    set_eax(c, 1);
}

// D3DDISPLAYMODE: width, height, refresh rate, format. X8R8G8B8 is 22.
static void put_display_mode(uint32_t p) {
    if (!p)
        return;
    wr32(p + 0, 1280);
    wr32(p + 4, 960);
    wr32(p + 8, 60);
    wr32(p + 12, 22);
}
void D9_EnumAdapterModes(X86 *c) {
    put_display_mode(arg(c, 4));
    com_ret(c, D3D_OK9);
}
void D9_GetAdapterDisplayMode(X86 *c) {
    put_display_mode(arg(c, 2));
    com_ret(c, D3D_OK9);
}
void D9_CheckDeviceType(X86 *c) {
    com_ret(c, D3D_OK9);
}
void D9_CheckDeviceFormat(X86 *c) {
    if (arg(c, 5) == 4 && (!d9volume::pixel_bytes(arg(c, 6)) || (arg(c, 4) & ~0x210u))) {
        com_ret(c, D3DERR_NOTAVAILABLE);
        return;
    }
    com_ret(c, D3D_OK9);
}
// D3DMULTISAMPLE_TYPE and quality as a sample count: 2x and 4x, which every
// Apple GPU renders. NONMASKABLE (1) offers them as quality levels 0 and 1.
// Remaster (owner 8 Oct): RECOMP_D3D9_MSAA_SAMPLES=2 (the core mod's 2x choice)
// caps it to 2; unset, the count is exactly as before (d3d9_msaa.h).
static uint32_t multisample_count(uint32_t type, uint32_t quality) {
    return d9msaa::count_env(type, quality);
}
// (this, Adapter, DeviceType, SurfaceFormat, Windowed, MultiSampleType,
// pQualityLevels)
void D9_CheckDeviceMultiSampleType(X86 *c) {
    uint32_t type = arg(c, 5), quality = arg(c, 6);
    uint32_t levels = type == 0 ? 1 : type == 1 ? 2 : (type == 2 || type == 4) ? 1 : 0;
    if (!levels) {
        com_ret(c, 0x8876086au); // D3DERR_NOTAVAILABLE
        return;
    }
    if (quality && gm_valid(quality, 4))
        wr32(quality, levels);
    com_ret(c, D3D_OK9);
}
void D9_CheckDepthStencilMatch(X86 *c) {
    com_ret(c, D3D_OK9);
}
void D9_CheckDeviceFormatConversion(X86 *c) {
    com_ret(c, D3D_OK9);
}

// D3DCAPS9 (304 bytes), as a GeForce 7800 GTX reports it for what the
// renderer implements: shader model 3.0, trilinear and 16x anisotropic
// filtering, the usual blend, compare and stencil sets, non-power-of-two
// textures, four render targets.
static void put_caps9(uint32_t p) {
    if (!p)
        return;
    memset(gm_ptr(p), 0, 304);
    auto f = [&](uint32_t off, float v) { wrf32(p + off, v); };
    wr32(p + 0, 1);            // DeviceType: D3DDEVTYPE_HAL
    wr32(p + 8, 0x00020000u);  // Caps: READ_SCANLINE
    wr32(p + 12, 0x00020000u); // Caps2: FULLSCREENGAMMA
    wr32(
        p + 16,
        0x000003a0u); // Caps3: ALPHA_FULLSCREEN_FLIP_OR_DISCARD, COPY_TO_VIDMEM, COPY_TO_SYSTEMMEM, LINEAR_TO_SRGB
    wr32(p + 20, 0x8000000fu); // PresentationIntervals: IMMEDIATE, ONE..FOUR
    wr32(p + 28, 0x001bbef0u); // DevCaps: HWTRANSFORMANDLIGHT, PUREDEVICE, HWRASTERIZATION, ...
    wr32(
        p + 32,
        0x0003ccf2u); // PrimitiveMiscCaps: masks, cull modes, blend op, separate alpha, independent write masks
    wr32(
        p + 36,
        0x0f732191u); // RasterCaps: dither, zbias, fog, anisotropy, scissor, slope-scale depth bias
    wr32(p + 40, 0x000000ffu); // ZCmpCaps: all
    wr32(p + 44, 0x00003fffu); // SrcBlendCaps: all, BLENDFACTOR
    wr32(p + 48, 0x00003fffu); // DestBlendCaps
    wr32(p + 52, 0x000000ffu); // AlphaCmpCaps
    wr32(p + 56, 0x00084208u); // ShadeCaps
    wr32(
        p + 60,
        0x0001ec45u); // TextureCaps: PERSPECTIVE, ALPHA, CUBEMAP, MIPMAP, MIPCUBEMAP, PROJECTED, VOLUMEMAP, ...
    wr32(
        p + 64,
        0x03030700u); // TextureFilterCaps: MIN point/linear/aniso, MIP point/linear, MAG point/linear
    wr32(p + 68, 0x03030300u);  // CubeTextureFilterCaps
    wr32(p + 72, 0x03030300u);  // VolumeTextureFilterCaps
    wr32(p + 76, 0x0000003fu);  // TextureAddressCaps
    wr32(p + 80, 0x0000003fu);  // VolumeTextureAddressCaps
    wr32(p + 84, 0x0000001fu);  // LineCaps
    wr32(p + 88, 4096);         // MaxTextureWidth
    wr32(p + 92, 4096);         // MaxTextureHeight
    wr32(p + 96, 512);          // MaxVolumeExtent
    wr32(p + 100, 8192);        // MaxTextureRepeat
    wr32(p + 104, 8192);        // MaxTextureAspectRatio
    wr32(p + 108, 16);          // MaxAnisotropy
    f(112, 1e10f);              // MaxVertexW
    f(116, -1e8f);              // GuardBandLeft
    f(120, -1e8f);              // GuardBandTop
    f(124, 1e8f);               // GuardBandRight
    f(128, 1e8f);               // GuardBandBottom
    wr32(p + 136, 0x000001ffu); // StencilCaps: all ops, TWOSIDED
    wr32(p + 140, 0x00080008u); // FVFCaps: 8 texture coordinates, PSIZE
    wr32(p + 144, 0x03feffffu); // TextureOpCaps
    wr32(p + 148, 8);           // MaxTextureBlendStages
    wr32(p + 152, 8);           // MaxSimultaneousTextures
    wr32(p + 156, 0x0000007bu); // VertexProcessingCaps
    wr32(p + 160, 8);           // MaxActiveLights
    wr32(p + 164, 6);           // MaxUserClipPlanes
    wr32(p + 168, 4);           // MaxVertexBlendMatrices
    f(176, 8192.0f);            // MaxPointSize
    wr32(p + 180, D9_MAX_PRIMITIVE_COUNT); // MaxPrimitiveCount (enforced by draw_refused)
    wr32(p + 184, D9_MAX_VERTEX_INDEX);    // MaxVertexIndex (enforced by draw_refused)
    wr32(p + 188, 16);          // MaxStreams
    wr32(p + 192, 255);         // MaxStreamStride
    wr32(p + 196, 0xfffe0300u); // VertexShaderVersion vs_3_0
    wr32(p + 200, 256);         // MaxVertexShaderConst
    wr32(p + 204, 0xffff0300u); // PixelShaderVersion ps_3_0
    f(208, 3.4e38f);            // PixelShaderMaxValue
    wr32(
        p + 212,
        0x00000051u); // DevCaps2: STREAMOFFSET, CAN_STRETCHRECT_FROM_TEXTURES, VERTEXELEMENTSCANSHARESTREAMOFFSET
    wr32(p + 232, 1);           // NumberOfAdaptersInGroup
    wr32(p + 236, 0x0000030fu); // DeclTypes
    wr32(p + 240, 4);           // NumSimultaneousRTs
    wr32(p + 244, 0x03000300u); // StretchRectFilterCaps: point, linear
    wr32(p + 248, 1);           // VS20Caps.Caps: PREDICATION
    wr32(p + 252, 24);          // VS20Caps.DynamicFlowControlDepth
    wr32(p + 256, 32);          // VS20Caps.NumTemps
    wr32(p + 260, 4);           // VS20Caps.StaticFlowControlDepth
    wr32(p + 264, 0x1f);        // PS20Caps.Caps
    wr32(p + 268, 24);          // PS20Caps.DynamicFlowControlDepth
    wr32(p + 272, 32);          // PS20Caps.NumTemps
    wr32(p + 276, 4);           // PS20Caps.StaticFlowControlDepth
    wr32(p + 280, 512);         // PS20Caps.NumInstructionSlots
    wr32(p + 284, 0x00000300u); // VertexTextureFilterCaps
    wr32(p + 288, 0xffffffffu); // MaxVShaderInstructionsExecuted
    wr32(p + 292, 0xffffffffu); // MaxPShaderInstructionsExecuted
    wr32(p + 296, 32768);       // MaxVertexShader30InstructionSlots
    wr32(p + 300, 32768);       // MaxPixelShader30InstructionSlots
}
void D9_GetDeviceCaps(X86 *c) {
    put_caps9(arg(c, 3));
    com_ret(c, D3D_OK9);
}
void D9_GetAdapterMonitor(X86 *c) {
    set_eax(c, 1); // a non-null HMONITOR
}

void D9_CreateDevice(X86 *c) {
    ComObj *d3d = this_d3d9(c);
    uint32_t present = arg(c, 5), out = arg(c, 6);
    if (!d3d || !out) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    com_out_ptr(out, 0);
    ComObj *dev = com_new(K_D3D9DEVICE);
    if (!dev) {
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    dev->dev_d3d = d3d->id;
    // D3DPRESENT_PARAMETERS: back buffer size and format first, the device
    // window at 28 and Windowed at 32.
    if (present) {
        dev->width = rd32(present + 0);
        dev->height = rd32(present + 4);
        dev->bpp = 32;
        dev->hwnd = rd32(present + 28);
        dev->samples = multisample_count(rd32(present + 16), rd32(present + 20));
    }
    uint32_t view = com_view(dev, IF_D3DDEVICE9);
    if (!view) {
        com_release(dev);
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    {
        ComObj *bb = device_backbuffer(dev);
        D9Pipeline &pl = d9_pipeline(dev->id);
        bind_color_target(dev, 0, bb); // Test159: the device holds its bound target
        pl.scissor[2] = (int32_t)dev->width;
        pl.scissor[3] = (int32_t)dev->height;
        if (present && rd32(present + 36)) {
            device_depthbuffer(dev);
            pl.rs[7] = 1; // ZENABLE defaults on with an automatic depth buffer
            pl.rs_changed(7);
        }
    }
    LOGW("d3d9: CreateDevice %ux%u, window %08x, %s renderer", dev->width, dev->height, dev->hwnd,
         host_d9_active() ? "GPU" : "CPU");
    com_out_ptr(out, view);
    com_ret(c, D3D_OK9);
}

static ComObj *device_backbuffer(ComObj *dev);
static ComObj *device_depthbuffer(ComObj *dev);

// ---------------------------------------------------------------------------
// IDirect3DDevice9
//
// Every slot is named and in its interface order, because a guest calls them
// by index. A stub reports itself once and returns D3D_OK: a game that is
// told "no" by a method it expects to work usually stops, and stopping tells
// us less than carrying on does.
// ---------------------------------------------------------------------------
#define D9_STUB(name, argc)                                                                        \
    void Dev_##name(X86 *c) {                                                                      \
        log_once("d3d9.dev." #name, "d3d9: IDirect3DDevice9::" #name " is not implemented");       \
        (void)argc;                                                                                \
        com_ret(c, D3D_OK9);                                                                       \
    }

D9_STUB(EvictManagedResources, 1)
D9_STUB(GetDisplayMode, 3)
D9_STUB(GetCreationParameters, 2)
D9_STUB(SetCursorProperties, 4)
D9_STUB(SetCursorPosition, 4)
D9_STUB(ShowCursor, 2)
D9_STUB(CreateAdditionalSwapChain, 3)
D9_STUB(GetSwapChain, 3)
D9_STUB(GetRasterStatus, 3)
D9_STUB(SetDialogBoxMode, 2)
D9_STUB(SetGammaRamp, 4)
D9_STUB(GetGammaRamp, 3)
void Dev_CreateVolumeTexture(X86 *c);
D9_STUB(UpdateSurface, 5)
void Dev_UpdateTexture(X86 *c);
void Dev_GetRenderTargetData(X86 *c);
D9_STUB(GetFrontBufferData, 3)
D9_STUB(ColorFill, 4)
void Dev_GetRenderTarget(X86 *c);
D9_STUB(GetTransform, 3)
D9_STUB(MultiplyTransform, 3)
D9_STUB(SetMaterial, 2)
D9_STUB(GetMaterial, 2)
D9_STUB(SetLight, 3)
D9_STUB(GetLight, 3)
D9_STUB(LightEnable, 3)
D9_STUB(GetLightEnable, 3)
D9_STUB(SetClipPlane, 3)
D9_STUB(GetClipPlane, 3)
void Dev_GetRenderState(X86 *c);
D9_STUB(CreateStateBlock, 3)
D9_STUB(BeginStateBlock, 1)
D9_STUB(EndStateBlock, 2)
D9_STUB(SetClipStatus, 2)
D9_STUB(GetClipStatus, 2)
void Dev_GetTexture(X86 *c);
D9_STUB(GetTextureStageState, 4)
void Dev_GetSamplerState(X86 *c);
D9_STUB(ValidateDevice, 2)
D9_STUB(SetPaletteEntries, 3)
D9_STUB(GetPaletteEntries, 3)
D9_STUB(SetCurrentTexturePalette, 2)
D9_STUB(GetCurrentTexturePalette, 2)
D9_STUB(GetScissorRect, 2)
D9_STUB(SetSoftwareVertexProcessing, 2)
D9_STUB(GetSoftwareVertexProcessing, 1)
D9_STUB(SetNPatchMode, 2)
D9_STUB(GetNPatchMode, 1)
D9_STUB(ProcessVertices, 6)
void Dev_GetVertexDeclaration(X86 *c);
D9_STUB(SetFVF, 2)
D9_STUB(GetFVF, 2)
D9_STUB(CreateVertexShader, 3)
D9_STUB(SetVertexShader, 2)
D9_STUB(GetVertexShader, 2)
D9_STUB(GetVertexShaderConstantF, 4)
D9_STUB(SetVertexShaderConstantI, 4)
D9_STUB(GetVertexShaderConstantI, 4)
D9_STUB(GetStreamSource, 5)
D9_STUB(SetStreamSourceFreq, 3)
D9_STUB(GetStreamSourceFreq, 3)
D9_STUB(GetIndices, 2)
D9_STUB(CreatePixelShader, 3)
D9_STUB(SetPixelShader, 2)
D9_STUB(GetPixelShader, 2)
D9_STUB(GetPixelShaderConstantF, 4)
D9_STUB(SetPixelShaderConstantI, 4)
D9_STUB(GetPixelShaderConstantI, 4)
D9_STUB(DrawRectPatch, 4)
D9_STUB(DrawTriPatch, 4)
D9_STUB(DeletePatch, 2)

// (this, pRect)
void Dev_SetScissorRect(X86 *c) {
    ComObj *dev = this_device9(c);
    uint32_t r = arg(c, 1);
    if (dev && r)
        for (int i = 0; i < 4; ++i)
            d9_pipeline(dev->id).scissor[i] = (int32_t)rd32(r + 4u * (uint32_t)i);
    com_ret(c, dev && r ? D3D_OK9 : D3DERR_INVALIDCALL);
}

static ComObj *device_backbuffer(ComObj *dev);
static ComObj *device_depthbuffer(ComObj *dev);
static void bind_depth_stencil(ComObj *dev, ComObj *s);
static void bind_color_target(ComObj *dev, uint32_t index, ComObj *s);
static std::unordered_map<uint32_t, uint32_t> &auto_depth_surfaces();
static std::unordered_map<uint32_t, uint32_t> &resource_usage();
static void device_forget_buffers(ComObj *dev);

// (this, pPresentationParameters): new back buffer and depth buffer at the
// new size; every resource the game made itself survives, as with
// D3DPOOL_MANAGED.
void Dev_Reset(X86 *c) {
    ComObj *dev = this_device9(c);
    uint32_t present = arg(c, 1);
    if (!dev || !present) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    dev->width = rd32(present + 0);
    dev->height = rd32(present + 4);
    dev->samples = multisample_count(rd32(present + 16), rd32(present + 20));
    // Test159: Reset drops the device's bindings of render targets 1-3 and puts the new back buffer on 0.
    for (uint32_t i = 1; i < 4; ++i)
        bind_color_target(dev, i, nullptr);
    device_forget_buffers(dev);
    dev->present_deadline = FrameDeadline{};
    dev->present_slots = SlotPacer{};
    dev->present_fps = 0;
    dev->present_locked = -1;
    ComObj *bb = device_backbuffer(dev);
    D9Pipeline &pl = d9_pipeline(dev->id);
    bind_color_target(dev, 0, bb);
    pl.viewport_set = false;
    pl.scissor[0] = pl.scissor[1] = 0;
    pl.scissor[2] = (int32_t)dev->width;
    pl.scissor[3] = (int32_t)dev->height;
    if (rd32(present + 36)) { // EnableAutoDepthStencil
        device_depthbuffer(dev);
        pl.rs[7] = 1;
        pl.rs_changed(7);
    } else
        bind_depth_stencil(dev, nullptr);
    LOGW("d3d9: Reset to %ux%u", dev->width, dev->height);
    // Test157c diagnostics (RECOMP_D3D9_STATS): live objects after each Reset, to tell a per-reset retention from
    // normal reloading (the review's 20-reset discriminator).
    static const bool reset_stats = recomp_env("D3D9_STATS") != nullptr;
    if (reset_stats && g_com_objects) {
        uint32_t surfaces = 0, textures = 0, vbs = 0, ibs = 0, all = 0;
        for (const auto &o : *g_com_objects)
            if (o && o->alive) {
                ++all;
                surfaces += o->kind == K_D3D9SURFACE;
                textures += o->kind == K_D3D9TEXTURE;
                vbs += o->kind == K_D3D9VB;
                ibs += o->kind == K_D3D9IB;
            }
        LOGW("d3d9 stats: after Reset: %u live objects (%u surfaces, %u textures, %u vertex / %u index buffers); "
             "bound depth %u, automatic depth %u",
             all, surfaces, textures, vbs, ibs, dev->zbuffer_obj, auto_depth_surfaces()[dev->id]);
        // Objects made since the previous Reset that are still alive, grouped by kind/size/format/usage/refs: what a
        // Reset leaves behind (default-pool render targets should not survive two Resets).
        static uint32_t prev_mark = 0;
        const uint32_t mark = (uint32_t)g_com_objects->size();
        std::map<std::tuple<int, uint32_t, uint32_t, uint32_t, uint32_t, int32_t>, uint32_t> groups;
        for (uint32_t id = prev_mark + 1; id <= mark; ++id) {
            const ComObj *o = (*g_com_objects)[id - 1].get();
            if (!o || !o->alive)
                continue;
            auto u = resource_usage().find(o->id);
            ++groups[{(int)o->kind, o->width, o->height, o->rmask, u == resource_usage().end() ? 0u : u->second, o->refs}];
        }
        std::vector<std::pair<uint32_t, std::tuple<int, uint32_t, uint32_t, uint32_t, uint32_t, int32_t>>> top;
        for (const auto &g : groups)
            top.push_back({g.second, g.first});
        std::sort(top.begin(), top.end(), [](const auto &a, const auto &b) { return a.first > b.first; });
        for (size_t i = 0; i < top.size() && i < 8; ++i)
            LOGW("d3d9 stats:   survivors since the last Reset: %u x kind %d %ux%u format %u usage %u refs %d", top[i].first,
                 std::get<0>(top[i].second), std::get<1>(top[i].second), std::get<2>(top[i].second),
                 std::get<3>(top[i].second), std::get<4>(top[i].second), std::get<5>(top[i].second));
        prev_mark = mark;
    }
    com_ret(c, D3D_OK9);
}

void Dev_TestCooperativeLevel(X86 *c) {
    com_ret(c, this_device9(c) ? D3D_OK9 : D3DERR_INVALIDCALL);
}
void Dev_GetAvailableTextureMem(X86 *c) {
    set_eax(c, 256u * 1024u * 1024u);
}
void Dev_GetNumberOfSwapChains(X86 *c) {
    set_eax(c, 1);
}
void Dev_GetDirect3D(X86 *c) {
    ComObj *dev = this_device9(c);
    ComObj *d3d = dev ? com_get(dev->dev_d3d) : nullptr;
    uint32_t out = arg(c, 1);
    if (!d3d || !out) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    uint32_t view = com_view(d3d, IF_D3D9);
    com_addref(d3d);
    com_out_ptr(out, view);
    com_ret(c, D3D_OK9);
}
void Dev_GetDeviceCaps(X86 *c) {
    put_caps9(arg(c, 1));
    com_ret(c, D3D_OK9);
}
void Dev_BeginScene(X86 *c) {
    ComObj *dev = this_device9(c);
    if (dev)
        dev->in_scene = true;
    com_ret(c, dev ? D3D_OK9 : D3DERR_INVALIDCALL);
}
void Dev_EndScene(X86 *c) {
    ComObj *dev = this_device9(c);
    if (dev)
        dev->in_scene = false;
    com_ret(c, dev ? D3D_OK9 : D3DERR_INVALIDCALL);
}
// Defined with the resources below.
static ComObj *device_backbuffer(ComObj *dev);
static uint8_t *resource_bytes(ComObj *o);

// (this, Count, pRects, Flags, Color, Z, Stencil). Only the colour target is
// real so far: a 32-bit surface is filled with the colour, over the listed
// D3DRECTs or the whole surface.
void Dev_Clear(X86 *c) {
    ComObj *dev = this_device9(c);
    uint32_t count = arg(c, 1), rects = arg(c, 2), flags = arg(c, 3), color = arg(c, 4);
    if (!dev) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    if (gpu_on()) {
        HostD9Target target = gpu_target(dev);
        int32_t vp[4];
        target_viewport(dev, target, vp);
        std::vector<int32_t> rs;
        if (count && rects && gm_fits(rects, count * 16u))
            for (uint32_t i = 0; i < count * 4; ++i)
                rs.push_back((int32_t)rd32(rects + 4 * i));
        float z;
        uint32_t zbits = arg(c, 5);
        memcpy(&z, &zbits, 4);
        host_d9_clear(&target, vp, (uint32_t)(rs.size() / 4), rs.empty() ? nullptr : rs.data(),
                      flags, color, z, arg(c, 6));
        gpu_target_drawn(dev, flags & 1u, flags & 6u);
        com_ret(c, D3D_OK9);
        return;
    }
    ComObj *s = com_get(dev->render_target);
    if (!s)
        s = device_backbuffer(dev);
    static uint32_t g_clears = 0;
    if (++g_clears <= 24)
        LOGW("d3d9: clear %u: flags %x colour %08x rects %u, target %u (%ux%u, %u bpp)%s", g_clears,
             flags, color, count, s ? s->id : 0, s ? s->width : 0, s ? s->height : 0,
             s ? s->bpp : 0, (s && s->id == dev->palette_obj) ? ", the back buffer" : "");
    uint8_t *bytes = resource_bytes(s);
    if ((flags & 1u) && bytes && s->bpp == 32) {
        uint32_t n = (count && rects) ? count : 1;
        for (uint32_t r = 0; r < n; ++r) {
            int32_t x1 = 0, y1 = 0, x2 = (int32_t)s->width, y2 = (int32_t)s->height;
            if (count && rects) {
                uint32_t q = rects + r * 16u;
                x1 = std::max(x1, (int32_t)rd32(q));
                y1 = std::max(y1, (int32_t)rd32(q + 4));
                x2 = std::min(x2, (int32_t)rd32(q + 8));
                y2 = std::min(y2, (int32_t)rd32(q + 12));
            }
            for (int32_t y = y1; y < y2; ++y) {
                uint8_t *row = bytes + (size_t)y * s->pitch;
                for (int32_t x = x1; x < x2; ++x)
                    memcpy(row + 4 * x, &color, 4);
            }
        }
        d9_raster_invalidate(s->id);
    }
    com_ret(c, D3D_OK9);
}

static void pool_stats_frame();   // Remaster run 10 (below gpu_draw)

// (this, pSourceRect, pDestRect, hDestWindowOverride, pDirtyRegion). The back
// buffer goes to the host as a 32-bit frame: the first thing this module puts
// on screen.
// NFSU1 run 8: the game's own frame cap follows the Frame limit (game.toml [game] frame_cap: a float of seconds per
// frame the game's limiter waits for). 40/60/120 write 1/limit; 0 (off) puts back the game's own value. Without this
// a game that caps itself at 60 never reaches 120 whatever the presenter does. RECOMP_GAME_FRAME_CAP=0: untouched.
static void game_frame_cap(int fps) {
#ifdef RECOMP_GAME_FRAME_CAP_ADDR
    static bool have_original = false;
    static uint32_t original = 0;
    const char *off = recomp_env("GAME_FRAME_CAP");
    if ((off && *off == '0') || !gm_valid(RECOMP_GAME_FRAME_CAP_ADDR, 4))
        return;
    if (!have_original) {
        original = rd32(RECOMP_GAME_FRAME_CAP_ADDR);
        have_original = true;
    }
    uint32_t bits = original;
    if (fps > 0) {
        const float period = 1.0f / (float)fps;
        memcpy(&bits, &period, 4);
    }
    wr32(RECOMP_GAME_FRAME_CAP_ADDR, bits);
    float now;
    memcpy(&now, &bits, 4);
    LOGW("d3d9: the game's frame cap (game.toml frame_cap) is %.6f s (%s)", now,
         fps > 0 ? "the Frame limit" : "the game's own");
#else
    (void)fps;
#endif
}

void Dev_Present(X86 *c) {
    ComObj *dev = this_device9(c);
    if (dev) {
        const int fps = recomp_env("PIN_CLOCK") ? 0 : mods_display_fps();
        // 3.0.2: when the presenter paces at the limit itself (60 on a 120 Hz or 60 Hz display), follow its refresh
        // slots on its clock (SlotPacer, frame_deadline.h); RECOMP_PRESENT_OWN_CLOCK=1 keeps the separate clock (A/B).
        double pnow = 0, pperiod = 0;
        static const bool own_clock = recomp_env("PRESENT_OWN_CLOCK") != nullptr;
        const int locked = fps > 0 && !own_clock && host_present_slot_clock(&pnow, &pperiod) &&
                           std::fabs(pperiod * fps - 1.0) < 0.03;
        if (fps != dev->present_fps || locked != dev->present_locked) {
            dev->present_deadline = FrameDeadline{};
            dev->present_slots = SlotPacer{};
            if (fps != dev->present_fps || !dev->present_cap_set)
                game_frame_cap(fps);
            dev->present_cap_set = true;
            dev->present_fps = fps;
            dev->present_locked = locked;
            LOGW("d3d9: completed-frame limit %d fps (0 = unlimited)%s", fps,
                 locked ? ", on the display's refresh slots" : "");
        }
        // Wait for the previous frame's deadline, then reserve the next
        // slot. Rendering work counts toward the period, the first frame is
        // immediate, and late frames never accumulate a catch-up backlog.
        // Yield the guest baton so audio/input workers continue to run.
        // Only presentation is paced; game clocks and simulation are intact.
        if (locked) {
            // Wait again if the scheduler woke the guest a little before the slot (at most three short waits).
            for (int tries = 0; tries < 4; ++tries) {
                const uint32_t ms = dev->present_slots.wait_ms(pnow, pperiod);
                if (!ms)
                    break;
                guest_sleep_ms(ms);
                if (!host_present_slot_clock(&pnow, &pperiod))
                    break;
            }
            dev->present_slots.presented(pnow, pperiod);
        } else {
            const uint32_t ms = dev->present_deadline.wait_ms(os_monotonic_ns());
            if (ms)
                guest_sleep_ms(ms);
            dev->present_deadline.begin(fps, os_monotonic_ns());
        }
        // 3.0.2: once a minute, what the display got (the owner's "59.5" check): new frames shown, frames the
        // presenter dropped (two in one slot), refreshes that repeated a frame (the game was late).
        static int64_t pace_t0 = 0;
        static uint64_t pace_u0 = 0, pace_d0 = 0, pace_r0 = 0;
        const int64_t pace_now = os_monotonic_ns();
        if (!pace_t0 || pace_now - pace_t0 >= 60000000000LL) {
            const uint64_t u = host_present_unique_completed(), d = host_present_drops(), r = host_present_repeats();
            if (pace_t0 && u >= pace_u0)
                LOGW("d3d9: frame pacing, last %.0f s: %llu frames shown (%.2f fps), %llu dropped, %llu repeated (%s)",
                     (pace_now - pace_t0) / 1e9, (unsigned long long)(u - pace_u0),
                     (u - pace_u0) / ((pace_now - pace_t0) / 1e9), (unsigned long long)(d - pace_d0),
                     (unsigned long long)(r - pace_r0), dev->present_locked == 1 ? "refresh slots" : "own clock");
            pace_t0 = pace_now, pace_u0 = u, pace_d0 = d, pace_r0 = r;
        }
    }
    ComObj *bb = dev ? device_backbuffer(dev) : nullptr;
    if (bb && gpu_on()) {
        gpu_sync(bb);
        ddraw_external_present_begin();
        host_present(nullptr, (int)bb->width, (int)bb->height, 32, nullptr, (int)bb->pitch);
        if (rprof::on())
            rprof::frame_id_next = host_frame_current().id;   // Remaster P1 (TEST ONLY): row id for the prof CSV
        host_d9_present(bb->id, bb->width, bb->height);
        pool_stats_frame();   // Remaster run 10
        ddraw_external_present_end();
        // Test86 HDR: the core mod's pre-HUD scene marker (private render state 250) lasts one frame.
        if (dev) {
            D9Pipeline &p = d9_pipeline(dev->id);
            if (p.rs[250] || p.rs[251]) {
                p.rs[250] = p.rs[251] = 0;
                p.rs_changed(250);
                p.rs_changed(251);
            }
        }
        com_ret(c, D3D_OK9);
        return;
    }
    uint8_t *bbytes = resource_bytes(bb);
    if (bb && bbytes) {
        uint32_t lit = 0, n = bb->width * bb->height;
        for (uint32_t i = 0; i < n; i += 97) {
            uint32_t v;
            memcpy(&v, bbytes + (size_t)i * 4, 4);
            if (v & 0x00ffffffu)
                ++lit;
        }
        LOGW("d3d9: present back buffer %u (%ux%u, %u bpp), %u of %u sampled pixels lit, target "
             "now %u",
             bb->id, bb->width, bb->height, bb->bpp, lit, (n + 96) / 97, dev->render_target);
    }
    if (bb && bbytes && bb->bpp == 32) {
        ddraw_external_present_begin();
        host_present(bbytes, (int)bb->width, (int)bb->height, 32, nullptr, (int)bb->pitch);
        ddraw_external_present_end();
    }
    com_ret(c, D3D_OK9);
}

// ---------------------------------------------------------------------------
// Device resources
//
// A game creates textures, buffers and surfaces before it draws anything, and
// then writes its geometry and its images into them. None of that reaches a
// GPU yet, but the storage is real guest memory, so a Lock hands back a
// pointer the game can fill and the contents survive to be drawn later.
// ---------------------------------------------------------------------------
// A resource's contents live in host memory, as a driver's would: the guest
// heap is the game's, and a texture set would exhaust it. The guest sees the
// bytes only while the resource is locked, through a copy made on the first
// lock and written back on the last unlock.
static uint32_t resource_alloc(ComObj *o, uint32_t bytes) {
    if (!bytes)
        return 0;
    o->blob.assign(bytes, 0);
    o->pixels = 0;
    o->pixels_bytes = bytes;
    return 1;
}

// The bytes to read or write right now: the guest copy while locked.
static uint8_t *resource_bytes(ComObj *o) {
    if (!o)
        return nullptr;
    if (o->pixels)
        return gm_ptr(o->pixels);
    return o->blob.empty() ? nullptr : o->blob.data();
}

static void gpu_before_lock(ComObj *s);
static void gpu_after_unlock(ComObj *o, uint32_t lo, uint32_t hi);
static void gpu_forget(ComObj *o);

static uint32_t stage_lock(ComObj *o) {
    if (!o || o->blob.empty())
        return 0;
    if (o->lock_count == 0)
        gpu_before_lock(o);
    if (o->lock_count++ == 0) {
        o->pixels = heap_alloc((uint32_t)o->blob.size(), false, 16);
        if (!o->pixels) {
            o->lock_count = 0;
            return 0;
        }
        memcpy(gm_ptr(o->pixels), o->blob.data(), o->blob.size());
    }
    return o->pixels;
}

static void stage_unlock(ComObj *o) {
    if (!o || o->lock_count <= 0)
        return;
    if (--o->lock_count == 0 && o->pixels) {
        memcpy(o->blob.data(), gm_ptr(o->pixels), o->blob.size());
        heap_free(o->pixels);
        o->pixels = 0;
        d9_raster_invalidate(o->id);
        gpu_after_unlock(o, 0, (uint32_t)o->blob.size());
    }
}

static void readback_forget(ComObj *o);
static void d3d9_resource_destroy(ComObj *o) {
    readback_forget(o);
    if (o->pixels)
        heap_free(o->pixels);
    o->pixels = 0;
    o->lock_count = 0;
    o->pixels_bytes = 0;
    std::vector<uint8_t>().swap(o->blob);
    d9_raster_invalidate(o->id);
    gpu_forget(o);
}

// Bytes per pixel for the formats this game creates. D3DFMT_A8R8G8B8 is 21,
// X8R8G8B8 22, R5G6B5 23, A8 28, D24S8 75, and the DXT block formats carry
// their four characters.
static uint32_t format_bytes(uint32_t fmt) {
    switch (fmt) {
    case 27:
    case 28:
    case 41:
    case 50:
    case 52: // R3G3B2, A8, P8, L8, A4L4
        return 1;
    case 23:
    case 24:
    case 25:
    case 26:
    case 29:
    case 30:
    case 40:
    case 51:
    case 60:
    case 61:
    case 70:
    case 73:
    case 80:
    case 81:
    case 111:
        return 2;
    case 36:
    case 113: // A16B16G16R16, A16B16G16R16F
        return 8;
    case 116: // A32B32G32R32F
        return 16;
    default:
        return 4;
    }
}

// The DXT formats, stored as blocks of 4x4 pixels.
static uint32_t dxt_block_bytes(uint32_t fmt) {
    switch (fmt) {
    case 0x31545844u:
        return 8; // DXT1
    case 0x32545844u:
    case 0x33545844u:
    case 0x34545844u:
    case 0x35545844u:
        return 16;
    default:
        return 0;
    }
}

static ComObj *make_surface(ComObj *dev, uint32_t w, uint32_t h, uint32_t fmt) {
    ComObj *s = com_new(K_D3D9SURFACE);
    if (!s)
        return nullptr;
    s->dev_d3d = dev ? dev->id : 0;
    s->width = w ? w : 1;
    s->height = h ? h : 1;
    s->rmask = fmt; // the D3DFORMAT, which a Direct3D 9 surface has no other field for
    if (uint32_t block = dxt_block_bytes(fmt)) {
        // A compressed surface is rows of blocks; Pitch is the bytes in one.
        uint32_t bw = (s->width + 3) / 4, bh = (s->height + 3) / 4;
        s->bpp = 0;
        s->pitch = bw * block;
        resource_alloc(s, s->pitch * bh);
    } else {
        s->bpp = format_bytes(fmt) * 8;
        s->pitch = s->width * format_bytes(fmt);
        resource_alloc(s, s->pitch * s->height);
    }
    return s;
}

// ---- What the CPU renderer needs to know about surfaces and textures --------
bool d9_surface_info(uint32_t surface_id, D9SurfaceInfo *out) {
    ComObj *s = com_get(surface_id);
    if (!s || s->kind != K_D3D9SURFACE)
        return false;
    out->data = resource_bytes(s);
    out->size = s->blob.size();
    out->width = s->width;
    out->height = s->height;
    out->pitch = s->pitch;
    out->format = s->rmask;
    return true;
}

uint32_t d9_texture_level0(uint32_t texture_id) {
    ComObj *t = com_get(texture_id);
    if (!t)
        return 0;
    if (t->kind == K_D3D9SURFACE)
        return t->id;
    if (t->kind != K_D3D9TEXTURE)
        return 0;
    if (t->caps) // a cube: face zero, level zero, once it exists
        return t->surfaces.empty() ? 0 : t->surfaces[0];
    return t->back_obj;
}

// The surface behind a 2D texture's level, or null.
static ComObj *texture_level(ComObj *tex, uint32_t level) {
    if (!tex || tex->kind != K_D3D9TEXTURE || tex->caps)
        return nullptr;
    if (level < tex->surfaces.size())
        return com_get(tex->surfaces[level]);
    return level == 0 ? com_get(tex->back_obj) : nullptr;
}

// ---- The GPU's copies ------------------------------------------------------
// With a GPU renderer running, every texture, surface and buffer has a mirror
// on the host under the object's id; a surface that belongs to a texture is a
// slot of its container's mirror. The shim's bytes stay the reference for what
// the game wrote, the GPU's for what was drawn: a slot the game unlocked is
// uploaded before the next draw that reads it, and a slot the GPU drew into is
// read back before the game locks it.
static const uint32_t USAGE_RENDERTARGET = 1, USAGE_DEPTHSTENCIL = 2;

struct GpuMirror {
    bool defined = false;
    std::vector<uint8_t> cpu_dirty;      // by slot
    bool any_dirty = false;              // Remaster C3: some cpu_dirty slot may be set (gpu_sync's early out)
    std::vector<uint8_t> gpu_newer;      // by slot
    uint32_t dirty_lo = 0, dirty_hi = 0; // buffers: the changed byte range
};
static std::unordered_map<uint32_t, GpuMirror> &gpu_mirrors() {
    static auto *m = new std::unordered_map<uint32_t, GpuMirror>();
    return *m;
}
// Test75: a resource's mirror without the hash lookup (map nodes do not move; gpu_forget clears the
// cached pointer before it erases). RECOMP_AB_SWITCHES=mirrors (TEST ONLY) takes the lookup every time.
static GpuMirror &mirror_of(ComObj *o) {
    if (o->gpu_mirror)
        return *static_cast<GpuMirror *>(o->gpu_mirror);
    GpuMirror &m = gpu_mirrors()[o->id];
    o->gpu_mirror = &m;
    return m;
}
// D3DUSAGE of every texture and surface, by id.
static std::unordered_map<uint32_t, uint32_t> &resource_usage() {
    static auto *m = new std::unordered_map<uint32_t, uint32_t>();
    return *m;
}
// Remaster A1: the opaque key a mod gave a texture (d3d9_texture_tag_iface), by id.
static std::unordered_map<uint32_t, uint64_t> &texture_keys() {
    static auto *m = new std::unordered_map<uint32_t, uint64_t>();
    return *m;
}
static bool gpu_on() {
    return host_d9_active() != 0;
}
static bool is_depth_format(uint32_t fmt) {
    return (fmt >= 70 && fmt <= 82) || fmt == 0x5a544e49u /* INTZ */ ||
           fmt == 0x34324644u /* DF24 */ || fmt == 0x36314644u /* DF16 */;
}

// Extra state for volume textures and their mip-level interfaces. The texture
// owns one reference to each mip. A mip with any external references holds one
// reference to the texture, released when its last external reference goes away.
static_assert(K_D3D9VOLUME < 64, "COM kind bitset capacity");
struct VolumeState {
    uint32_t depth = 0, pool = 0, lod = 0, priority = 0, lock_flags = 0;
};
static std::unordered_map<uint32_t, VolumeState> &volumes() {
    static auto *v = new std::unordered_map<uint32_t, VolumeState>();
    return *v;
}
static ComObj *volume_level(ComObj *o, uint32_t level) {
    return o && o->kind == K_D3D9VOLUMETEXTURE && level < o->surfaces.size()
               ? com_get(o->surfaces[level])
               : nullptr;
}
static void volume_destroy(ComObj *o) {
    for (uint32_t id : o->surfaces)
        com_destroy(com_get(id));
    o->surfaces.clear();
    volumes().erase(o->id);
    d3d9_resource_destroy(o);
}
// Test159: a 2D or cube texture takes its levels/faces with it, as a volume texture does (volume_destroy). Before,
// they were never destroyed: each texture left its level surfaces alive after its own final Release.
static void texture_destroy(ComObj *o) {
    for (uint32_t id : o->surfaces)
        com_destroy(com_get(id));
    o->surfaces.clear();
    o->back_obj = 0;
    d3d9_resource_destroy(o);
}
static void volume_addref(ComObj *o) {
    if (o && o->refs == 1)
        com_addref(com_get(o->front_obj));
    com_addref(o);
}
static void Vol_AddRef(X86 *c) {
    ComObj *o = com_this_arg(c);
    volume_addref(o);
    com_ret(c, o ? o->refs - 1 : 0);
}
static void Vol_Release(X86 *c) {
    ComObj *o = com_this_arg(c);
    if (!o || o->refs <= 1) {
        com_ret(c, 0);
        return;
    }
    const uint32_t external = --o->refs - 1;
    if (!external)
        com_release(com_get(o->front_obj)); // may destroy this mip as well
    com_ret(c, external);
}
static void Vol_QueryInterface(X86 *c) {
    ComObj *o = com_this_arg(c);
    const bool internal_only = o && o->refs == 1;
    com_QueryInterface(c);
    if (internal_only && c->r[0] == S_OK)
        com_addref(com_get(o->front_obj));
}

// A surface's place in its mirror: the container's id and the slot, or the
// surface's own id and slot zero.
static uint32_t mirror_slot(ComObj *s, uint32_t *slot) {
    *slot = 0;
    if (!s)
        return 0;
    if ((s->kind == K_D3D9SURFACE || s->kind == K_D3D9VOLUME) && s->front_obj) {
        ComObj *tex = com_get(s->front_obj);
        if (tex && (tex->kind == K_D3D9TEXTURE || tex->kind == K_D3D9VOLUMETEXTURE)) {
            for (size_t i = 0; i < tex->surfaces.size(); ++i)
                if (tex->surfaces[i] == s->id) {
                    *slot = (uint32_t)i;
                    return tex->id;
                }
        }
    }
    return s->id;
}
static HostD9Surface surface_ref(ComObj *s) {
    HostD9Surface r = {0, 0, 0};
    if (!s)
        return r;
    uint32_t slot;
    r.id = mirror_slot(s, &slot);
    ComObj *owner = com_get(r.id);
    if (owner && owner->kind == K_D3D9TEXTURE && owner->caps) {
        r.face = slot % 6;
        r.level = slot / 6;
    } else {
        r.level = slot;
    }
    return r;
}
static uint32_t slot_count(ComObj *o) {
    if (o->kind == K_D3D9TEXTURE || o->kind == K_D3D9VOLUMETEXTURE)
        return (uint32_t)std::max<size_t>(o->surfaces.size(), 1);
    return 1;
}
static ComObj *slot_surface(ComObj *o, uint32_t slot) {
    if (o->kind != K_D3D9TEXTURE && o->kind != K_D3D9VOLUMETEXTURE)
        return o;
    return slot < o->surfaces.size() ? com_get(o->surfaces[slot]) : nullptr;
}

// Creates the mirror of a texture or stand-alone surface on first use.
static GpuMirror &gpu_define(ComObj *o) {
    GpuMirror &m = mirror_of(o);
    if (m.defined)
        return m;
    HostD9TextureDesc d{};
    d.id = o->id;
    d.format = o->rmask ? o->rmask : 22;
    d.width = o->width ? o->width : 1;
    d.height = o->height ? o->height : 1;
    uint32_t usage = resource_usage()[o->id];
    if (is_depth_format(d.format))
        usage |= USAGE_DEPTHSTENCIL;
    d.usage = (usage & USAGE_RENDERTARGET ? HOST_D9_USAGE_RENDERTARGET : 0) |
              (usage & USAGE_DEPTHSTENCIL ? HOST_D9_USAGE_DEPTH : 0);
    d.samples = o->kind == K_D3D9SURFACE ? o->samples : 0;
    // Remaster run 13 mirror parity (RECOMP_REMASTER_MIRROR_MSAA=<w>x<h>, set by the core mod from `mirror_msaa`): the
    // render-target texture of exactly that size (the rear-view mirror) gets a 4x MSAA attachment, resolved into the
    // texture at the end of each pass as for the main view; alpha-to-coverage then follows. Unset: unchanged.
    if (o->kind == K_D3D9TEXTURE && !o->caps && (usage & USAGE_RENDERTARGET) && !is_depth_format(d.format)) {
        if (const char *m = getenv("RECOMP_REMASTER_MIRROR_MSAA"); m && *m) {
            unsigned mw = 0, mh = 0;
            if (sscanf(m, "%ux%u", &mw, &mh) == 2 && mw == d.width && mh == d.height)
                d.samples = 4;
        }
    }
    if (o->kind == K_D3D9VOLUMETEXTURE) {
        d.kind = HOST_D9_TEX_VOLUME;
        d.depth = volumes().at(o->id).depth;
        d.levels = slot_count(o);
    } else if (o->kind == K_D3D9TEXTURE && o->caps) {
        d.kind = HOST_D9_TEX_CUBE;
        d.levels = o->pal_flags ? o->pal_flags : 1;
    } else {
        d.kind = HOST_D9_TEX_2D;
        d.levels = o->kind == K_D3D9TEXTURE ? slot_count(o) : 1;
    }
    if (o->kind == K_D3D9TEXTURE) {
        auto k = texture_keys().find(o->id);
        d.key = k == texture_keys().end() ? 0 : k->second;
    }
    host_d9_texture_define(&d);
    m.defined = true;
    uint32_t n = o->kind == K_D3D9TEXTURE && o->caps ? 6 * d.levels : slot_count(o);
    // A target's first contents are whatever is drawn; a texture's are what
    // the game wrote, so every slot starts out needing an upload.
    m.cpu_dirty.assign(n, d.usage ? 0 : 1);
    m.any_dirty = !d.usage && n;
    m.gpu_newer.assign(n, 0);
    return m;
}

// Uploads every slot the game changed since the GPU last saw it.
static void gpu_sync(ComObj *o) {
    if (!o)
        return;
    GpuMirror &m = gpu_define(o);
    // Remaster C3: nothing to upload for most bound textures on most draws.
    if (!m.any_dirty)
        return;
    m.any_dirty = false; // every slot below is cleared before anything else can mark it again
    for (uint32_t slot = 0; slot < m.cpu_dirty.size(); ++slot) {
        if (!m.cpu_dirty[slot])
            continue;
        m.cpu_dirty[slot] = 0;
        ComObj *s = slot_surface(o, slot);
        if (!s || s->blob.empty())
            continue;
        HostD9Surface r = surface_ref(s);
        if (o->kind == K_D3D9VOLUMETEXTURE) {
            const uint32_t depth = volumes().at(s->id).depth;
            for (uint32_t z = 0; z < depth; ++z)
                host_d9_texture_upload(o->id, z, slot, resource_bytes(s) + z * s->pitch * s->height,
                                       s->pitch);
        } else {
            host_d9_texture_upload(o->id, r.face, r.level, resource_bytes(s), s->pitch);
        }
    }
}

// The surface is about to be read or written by the game.
static void gpu_before_lock(ComObj *s) {
    if (!s || s->kind != K_D3D9SURFACE)
        return;
    uint32_t slot;
    uint32_t id = mirror_slot(s, &slot);
    auto it = gpu_mirrors().find(id);
    if (it == gpu_mirrors().end() || slot >= it->second.gpu_newer.size() ||
        !it->second.gpu_newer[slot])
        return;
    it->second.gpu_newer[slot] = 0;
    if (s->bpp == 32 && !s->blob.empty()) {
        HostD9Surface r = surface_ref(s);
        host_d9_texture_read(id, r.face, r.level, s->blob.data(), s->pitch);
    }
}
// The game wrote the resource.
static void gpu_after_unlock(ComObj *o, uint32_t lo, uint32_t hi) {
    if (!o)
        return;
    if (o->kind == K_D3D9VB || o->kind == K_D3D9IB) {
        GpuMirror &m = mirror_of(o);
        if (m.dirty_hi <= m.dirty_lo) {
            m.dirty_lo = lo;
            m.dirty_hi = hi;
        } else {
            m.dirty_lo = std::min(m.dirty_lo, lo);
            m.dirty_hi = std::max(m.dirty_hi, hi);
        }
        m.defined = true;
        return;
    }
    uint32_t slot;
    uint32_t id = mirror_slot(o, &slot);
    auto it = gpu_mirrors().find(id);
    if (it == gpu_mirrors().end())
        return; // not mirrored yet: its first use uploads everything
    if (slot < it->second.cpu_dirty.size()) {
        it->second.cpu_dirty[slot] = 1;
        it->second.any_dirty = true;
    }
}
static void gpu_drawn_into(ComObj *s) {
    if (!s)
        return;
    uint32_t slot;
    uint32_t id = mirror_slot(s, &slot);
    ComObj *owner = com_get(id);
    if (!owner)
        return;
    GpuMirror &m = gpu_define(owner);
    if (slot < m.gpu_newer.size()) {
        m.gpu_newer[slot] = 1;
        m.cpu_dirty[slot] = 0;
    }
}
static void gpu_sync_buffer(ComObj *b) {
    if (!b)
        return;
    GpuMirror &m = mirror_of(b);
    if (!m.defined) {
        m.defined = true;
        m.dirty_lo = 0;
        m.dirty_hi = (uint32_t)b->blob.size();
    }
    if (m.dirty_hi > m.dirty_lo && !b->blob.empty()) {
        uint32_t hi = std::min<uint32_t>(m.dirty_hi, (uint32_t)b->blob.size());
        host_d9_buffer_upload(b->id, (uint32_t)b->blob.size(), m.dirty_lo,
                              b->blob.data() + m.dirty_lo, hi - m.dirty_lo);
    }
    m.dirty_lo = m.dirty_hi = 0;
}
static void gpu_forget(ComObj *o) {
    o->gpu_mirror = nullptr;
    auto it = gpu_mirrors().find(o->id);
    if (it != gpu_mirrors().end()) {
        if (o->kind == K_D3D9VB || o->kind == K_D3D9IB)
            host_d9_buffer_drop(o->id);
        else if (it->second.defined)
            host_d9_texture_drop(o->id);
        gpu_mirrors().erase(it);
    }
    resource_usage().erase(o->id);
    texture_keys().erase(o->id);
}

// Remaster A1: a mod names a 2D texture it holds through its IDirect3DTexture9 pointer. The pointer is
// resolved to the shim's object id at once (ids are never reused); no guest address is kept. A texture whose
// mirror already exists is re-keyed on the host; otherwise the key travels with its first definition.
// 1 = tagged, 0 = not a live 2D texture of ours.
extern "C" int d3d9_texture_tag_iface(uint32_t iface, uint64_t key) {
    ComObj *o = com_this(iface, IF_D3DTEXTURE9);
    if (!o || o->kind != K_D3D9TEXTURE || o->caps)
        return 0;
    texture_keys()[o->id] = key;
    auto it = gpu_mirrors().find(o->id);
    if (it != gpu_mirrors().end() && it->second.defined)
        host_d9_texture_tag(o->id, key);
    return 1;
}

// (this, Length, Usage, FVF, Pool, ppVertexBuffer, pSharedHandle)
void Dev_CreateVertexBuffer(X86 *c) {
    ComObj *dev = this_device9(c);
    uint32_t bytes = arg(c, 1), out = arg(c, 5);
    if (!dev || !out) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    ComObj *vb = com_new(K_D3D9VB);
    if (!vb || !resource_alloc(vb, bytes)) {
        if (vb)
            com_release(vb);
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    vb->dev_d3d = dev->id;
    vb->buf_bytes = bytes;
    uint32_t view_ = com_view(vb, IF_D3DVERTEXBUFFER9);
    if (!view_) {
        com_release(vb);
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    com_out_ptr(out, view_);
    com_ret(c, D3D_OK9);
}

// (this, Length, Usage, Format, Pool, ppIndexBuffer, pSharedHandle)
void Dev_CreateIndexBuffer(X86 *c) {
    ComObj *dev = this_device9(c);
    uint32_t bytes = arg(c, 1), out = arg(c, 5);
    if (!dev || !out) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    ComObj *ib = com_new(K_D3D9IB);
    if (!ib || !resource_alloc(ib, bytes)) {
        if (ib)
            com_release(ib);
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    ib->dev_d3d = dev->id;
    ib->buf_bytes = bytes;
    ib->rmask = arg(c, 3) == 102 ? 4 : 2; // D3DFMT_INDEX32 or D3DFMT_INDEX16
    uint32_t view_ = com_view(ib, IF_D3DINDEXBUFFER9);
    if (!view_) {
        com_release(ib);
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    com_out_ptr(out, view_);
    com_ret(c, D3D_OK9);
}

// (this, Width, Height, Levels, Usage, Format, Pool, ppTexture, pSharedHandle)
void Dev_CreateTexture(X86 *c) {
    ComObj *dev = this_device9(c);
    uint32_t w = arg(c, 1), h = arg(c, 2), fmt = arg(c, 5), out = arg(c, 7);
    if (!dev || !out) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    ComObj *tex = com_new(K_D3D9TEXTURE);
    if (!tex) {
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    tex->dev_d3d = dev->id;
    tex->width = w;
    tex->height = h;
    tex->bpp = format_bytes(fmt) * 8;
    // Every level gets its own surface. Levels = 0 asks for the full chain;
    // a game uploads each level with its own LockRect.
    uint32_t levels = arg(c, 3);
    if (!levels) {
        levels = 1;
        for (uint32_t d = std::max(w, h); d > 1; d >>= 1)
            ++levels;
    }
    tex->rmask = fmt;
    uint32_t usage = arg(c, 4);
    resource_usage()[tex->id] = usage & (USAGE_RENDERTARGET | USAGE_DEPTHSTENCIL);
    for (uint32_t l = 0; l < levels && l < 16; ++l) {
        ComObj *s = make_surface(dev, std::max(w >> l, 1u), std::max(h >> l, 1u), fmt);
        if (!s)
            break;
        s->front_obj = tex->id;
        tex->surfaces.push_back(s->id);
    }
    tex->back_obj = tex->surfaces.empty() ? 0 : tex->surfaces[0];
    uint32_t view_ = com_view(tex, IF_D3DTEXTURE9);
    if (!view_) {
        com_release(tex);
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    com_out_ptr(out, view_);
    com_ret(c, D3D_OK9);
}

// (this, EdgeLength, Levels, Usage, Format, Pool, ppCubeTexture, pSharedHandle)
void Dev_CreateCubeTexture(X86 *c) {
    ComObj *dev = this_device9(c);
    uint32_t edge = arg(c, 1), fmt = arg(c, 4), out = arg(c, 6);
    if (!dev || !out) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    ComObj *tex = com_new(K_D3D9TEXTURE);
    if (!tex) {
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    tex->dev_d3d = dev->id;
    tex->width = tex->height = edge;
    tex->bpp = format_bytes(fmt) * 8;
    tex->rmask = fmt;
    tex->caps = 1;                              // a cube map
    tex->pal_flags = arg(c, 2) ? arg(c, 2) : 1; // levels, reusing a field cubes do not use
    resource_usage()[tex->id] = arg(c, 3) & (USAGE_RENDERTARGET | USAGE_DEPTHSTENCIL);
    uint32_t view = com_view(tex, IF_D3DCUBETEXTURE9);
    if (!view) {
        com_release(tex);
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    LOGV("d3d9: CreateCubeTexture %u -> %08x into %08x", edge, view, out);
    com_out_ptr(out, view);
    com_ret(c, D3D_OK9);
}

static void create_surface_method(X86 *c, uint32_t out_index, uint32_t fmt_index, uint32_t usage) {
    ComObj *dev = this_device9(c);
    uint32_t w = arg(c, 1), h = arg(c, 2), fmt = arg(c, fmt_index), out = arg(c, out_index);
    if (!dev || !out) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    ComObj *s = make_surface(dev, w, h, fmt);
    if (!s) {
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    // CreateRenderTarget and CreateDepthStencilSurface: MultiSample, MultisampleQuality.
    if (out_index == 7)
        s->samples = multisample_count(arg(c, 4), arg(c, 5));
    resource_usage()[s->id] = usage;
    uint32_t view_ = com_view(s, IF_D3DSURFACE9);
    if (!view_) {
        com_release(s);
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    com_out_ptr(out, view_);
    com_ret(c, D3D_OK9);
}
// (this, Width, Height, Format, MultiSample, MultisampleQuality, Discard,
//  ppSurface, pSharedHandle)
void Dev_CreateDepthStencilSurface(X86 *c) {
    create_surface_method(c, 7, 3, USAGE_DEPTHSTENCIL);
}
// (this, Width, Height, Format, MultiSample, MultisampleQuality, Lockable,
//  ppSurface, pSharedHandle)
void Dev_CreateRenderTarget(X86 *c) {
    create_surface_method(c, 7, 3, USAGE_RENDERTARGET);
}
// (this, Width, Height, Format, Pool, ppSurface, pSharedHandle)
void Dev_CreateOffscreenPlainSurface(X86 *c) {
    create_surface_method(c, 5, 3, 0);
}

// The device's own back buffer and depth buffer, made on first ask and kept.
// The back buffer lives in its own field. It used to share render_target,
// which SetRenderTarget overwrites, so after the game switched targets the
// "back buffer" was whatever it had last drawn into.
static ComObj *device_backbuffer(ComObj *dev) {
    ComObj *s = com_get(dev->palette_obj);
    if (!s) {
        s = make_surface(dev, dev->width ? dev->width : 640, dev->height ? dev->height : 480, 22);
        dev->palette_obj = s ? s->id : 0;
        if (s)
            s->samples = dev->samples;
        if (s)
            resource_usage()[s->id] = USAGE_RENDERTARGET;
        if (s && !com_get(dev->render_target))
            dev->render_target = s->id;
    }
    return s;
}

// (this, pSourceSurface, pSourceRect, pDestSurface, pDestRect, Filter): a
// nearest-neighbour copy between 32-bit surfaces, rectangles honoured. This is
// how a game moves an off-screen target onto the back buffer.
void Dev_StretchRect(X86 *c) {
    ComObj *src = com_this(arg(c, 1)), *dst = com_this(arg(c, 3));
    if (!src || !dst || src->kind != K_D3D9SURFACE || dst->kind != K_D3D9SURFACE) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    if (gpu_on()) {
        uint32_t slot;
        ComObj *sowner = com_get(mirror_slot(src, &slot));
        ComObj *downer = com_get(mirror_slot(dst, &slot));
        if (sowner)
            gpu_sync(sowner);
        if (downer)
            gpu_sync(downer);
        int32_t sr[4] = {0, 0, (int32_t)src->width, (int32_t)src->height};
        int32_t dr[4] = {0, 0, (int32_t)dst->width, (int32_t)dst->height};
        if (arg(c, 2))
            for (int i = 0; i < 4; ++i)
                sr[i] = (int32_t)rd32(arg(c, 2) + 4u * (uint32_t)i);
        if (arg(c, 4))
            for (int i = 0; i < 4; ++i)
                dr[i] = (int32_t)rd32(arg(c, 4) + 4u * (uint32_t)i);
        host_d9_stretch(surface_ref(src), sr, surface_ref(dst), dr, arg(c, 5));
        gpu_drawn_into(dst);
        com_ret(c, D3D_OK9);
        return;
    }
    uint8_t *sbytes = resource_bytes(src), *dbytes = resource_bytes(dst);
    if (!sbytes || !dbytes || src->bpp != 32 || dst->bpp != 32) {
        com_ret(c, D3D_OK9);
        return;
    }
    auto rect = [](uint32_t p, ComObj *s, int32_t r[4]) {
        r[0] = 0;
        r[1] = 0;
        r[2] = (int32_t)s->width;
        r[3] = (int32_t)s->height;
        if (p)
            for (int i = 0; i < 4; ++i)
                r[i] = (int32_t)rd32(p + 4u * (uint32_t)i);
        r[0] = std::max(r[0], 0);
        r[1] = std::max(r[1], 0);
        r[2] = std::min(r[2], (int32_t)s->width);
        r[3] = std::min(r[3], (int32_t)s->height);
    };
    ComObj *dev_ = com_get(dst->dev_d3d);
    static uint32_t g_stretches = 0;
    if (++g_stretches <= 24)
        LOGW("d3d9: StretchRect %u (%ux%u, %u bpp) -> %u (%ux%u, %u bpp)%s", src->id, src->width,
             src->height, src->bpp, dst->id, dst->width, dst->height, dst->bpp,
             (dev_ && dst->id == dev_->palette_obj) ? ", onto the back buffer" : "");
    int32_t sr[4], dr[4];
    rect(arg(c, 2), src, sr);
    rect(arg(c, 4), dst, dr);
    int32_t sw = sr[2] - sr[0], sh = sr[3] - sr[1], dw = dr[2] - dr[0], dh = dr[3] - dr[1];
    if (sw <= 0 || sh <= 0 || dw <= 0 || dh <= 0) {
        com_ret(c, D3D_OK9);
        return;
    }
    for (int32_t y = 0; y < dh; ++y) {
        int32_t sy = sr[1] + y * sh / dh;
        uint8_t *drow = dbytes + (size_t)(dr[1] + y) * dst->pitch;
        const uint8_t *srow = sbytes + (size_t)sy * src->pitch;
        for (int32_t x = 0; x < dw; ++x)
            memcpy(drow + 4 * (dr[0] + x), srow + 4 * (sr[0] + x * sw / dw), 4);
    }
    d9_raster_invalidate(dst->id);
    com_ret(c, D3D_OK9);
}
// Test157c (audit after the rain incident): depth-stencil ownership as Direct3D 9. The device keeps two
// references of its own: one to its automatic depth buffer (CreateDevice/Reset with EnableAutoDepthStencil,
// tracked here per device) and one to the bound depth-stencil surface (dev->zbuffer_obj; SetDepthStencilSurface
// AddRefs the new one and releases the old one). Before, Set took no reference, so a surface the game released
// while bound was destroyed at once, and Reset released the bound surface even when it was the game's own,
// destroying a surface the game still held.
static std::unordered_map<uint32_t, uint32_t> &auto_depth_surfaces() {
    static auto *m = new std::unordered_map<uint32_t, uint32_t>();
    return *m;
}
// Test159 (20-reset measurement, 4 Oct): a level or face of a 2D/cube texture gets container-coupled lifetime that
// keeps Direct3D 9's documented observable behaviour (a getter returns a reference the caller releases; the texture
// does not outlive its final release). Not checked against Windows' internal counting. The surface object holds one internal reference (its container's); while anything outside holds
// it, it also holds one reference to the container, taken with the first outside reference and given back with the
// last. Before, GetSurfaceLevel / GetCubeMapSurface / GetRenderTarget took a texture reference on every call and
// releasing the surface never gave it back, so every texture whose level was ever asked for (render targets, the
// game's and the mods') outlived its own release: each renderer Reset left its old targets behind.
static ComObj *surface_container(ComObj *s) {
    if (!s || s->kind != K_D3D9SURFACE || !s->front_obj)
        return nullptr;
    ComObj *t = com_get(s->front_obj);
    if (!t || t->kind != K_D3D9TEXTURE)
        return nullptr;
    for (uint32_t id : t->surfaces)
        if (id == s->id)
            return t;
    return nullptr;
}
static void surface_addref(ComObj *s) {
    if (!s)
        return;
    if (ComObj *t = surface_container(s); t && s->refs == 1)
        com_addref(t);
    com_addref(s);
}
static int32_t surface_release(ComObj *s) {
    if (!s)
        return 0;
    ComObj *t = surface_container(s);
    if (!t)
        return com_release(s);
    if (s->refs <= 1)
        return 0; // only the container's own reference: nothing outside to drop
    const int32_t outside = --s->refs - 1;
    if (!outside)
        com_release(t); // may destroy this level together with its container
    return outside;
}
static void bind_depth_stencil(ComObj *dev, ComObj *s) {
    surface_addref(s);
    ComObj *old = com_get(dev->zbuffer_obj);
    dev->zbuffer_obj = s ? s->id : 0;
    if (old)
        surface_release(old);
}
// Test159: the device holds the surfaces bound as render targets, as Direct3D 9 does (SetRenderTarget AddRefs the
// new one and releases the old one), so a target the game releases while it is bound stays valid until unbound.
static void bind_color_target(ComObj *dev, uint32_t index, ComObj *s) {
    D9Pipeline &pl = d9_pipeline(dev->id);
    surface_addref(s);
    ComObj *old = com_get(pl.color_target[index]);
    pl.color_target[index] = s ? s->id : 0;
    if (index == 0)
        dev->render_target = pl.color_target[0];
    if (old)
        surface_release(old);
}
// The device's automatic depth buffer, made on first use, bound.
static ComObj *device_depthbuffer(ComObj *dev) {
    auto &autos = auto_depth_surfaces();
    ComObj *s = com_get(autos[dev->id]);
    if (!s) {
        s = make_surface(dev, dev->width ? dev->width : 640, dev->height ? dev->height : 480, 75);
        if (!s) {
            autos.erase(dev->id);
            return nullptr;
        }
        s->samples = dev->samples;
        resource_usage()[s->id] = USAGE_DEPTHSTENCIL;
        autos[dev->id] = s->id; // the device's own reference (make_surface's)
    }
    if (dev->zbuffer_obj != s->id)
        bind_depth_stencil(dev, s);
    return s;
}

// Reset: drops the device's own back buffer, its binding of the depth-stencil surface and its automatic depth
// buffer; the game's references keep those objects alive, and the device makes new ones on next use.
static void device_forget_buffers(ComObj *dev) {
    if (ComObj *s = com_get(dev->palette_obj))
        com_release(s);
    dev->palette_obj = 0;
    bind_depth_stencil(dev, nullptr);
    auto it = auto_depth_surfaces().find(dev->id);
    if (it != auto_depth_surfaces().end()) {
        if (ComObj *a = com_get(it->second))
            com_release(a);
        auto_depth_surfaces().erase(it);
    }
}

// (this, iSwapChain, iBackBuffer, Type, ppBackBuffer)
void Dev_GetBackBuffer(X86 *c) {
    ComObj *dev = this_device9(c);
    uint32_t out = arg(c, 4);
    ComObj *s = dev ? device_backbuffer(dev) : nullptr;
    if (!s || !out) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    surface_addref(s);
    uint32_t view_ = com_view(s, IF_D3DSURFACE9);
    if (!view_) {
        surface_release(s);
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    com_out_ptr(out, view_);
    com_ret(c, D3D_OK9);
}

// Test155 (owner session 3 Oct 21:47, Metal allocation trace): GetDepthStencilSurface returns the depth-stencil
// surface that is set (SetDepthStencilSurface; the automatic one from CreateDevice/Reset until the game sets
// another). With none set, Direct3D 9 returns D3DERR_NOTFOUND. This used to make a new device-sized depth buffer
// instead (device_depthbuffer), which the game never released: at rain onset that was one 3840x2160 4x-MSAA
// D24S8 surface (~204 MB) per frame, ~13 GB/s, until macOS killed the game.
void Dev_GetDepthStencilSurface(X86 *c) {
    ComObj *dev = this_device9(c);
    uint32_t out = arg(c, 1);
    if (!dev || !out) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    ComObj *s = com_get(dev->zbuffer_obj);
    if (!s) {
        static uint64_t none = 0;
        ++none;
        static const bool notfound_log = recomp_env("D3D9_STATS") != nullptr; // expected in rain: debug-only (review 4 Oct)
        if (notfound_log && (none <= 8 || (none & (none - 1)) == 0))
            LOGW("d3d9: GetDepthStencilSurface with no depth-stencil surface set: D3DERR_NOTFOUND, as Direct3D 9 "
                 "(caller %08x, %llu so far)",
                 rd32(c->r[R_ESP]), (unsigned long long)none);
        wr32(out, 0);
        com_ret(c, D3DERR_NOTFOUND9);
        return;
    }
    surface_addref(s);
    uint32_t view_ = com_view(s, IF_D3DSURFACE9);
    if (!view_) {
        surface_release(s);
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    com_out_ptr(out, view_);
    com_ret(c, D3D_OK9);
}

void Dev_SetRenderTarget(X86 *c) {
    ComObj *dev = this_device9(c);
    ComObj *s = com_this(arg(c, 2));
    uint32_t index = arg(c, 1);
    if (!dev || index > 3) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    if (index == 0 && !(s && s->kind == K_D3D9SURFACE)) {
        com_ret(c, D3DERR_INVALIDCALL); // target 0 cannot be unset
        return;
    }
    D9Pipeline &pl = d9_pipeline(dev->id);
    bind_color_target(dev, index, (s && s->kind == K_D3D9SURFACE) ? s : nullptr); // Test159: held while bound
    if (index == 0) {
        // Setting render target 0 resets the viewport and scissor to the target.
        pl.viewport_set = false;
        pl.scissor[0] = pl.scissor[1] = 0;
        pl.scissor[2] = (int32_t)s->width;
        pl.scissor[3] = (int32_t)s->height;
    }
    com_ret(c, D3D_OK9);
}

void Dev_SetDepthStencilSurface(X86 *c) {
    ComObj *dev = this_device9(c);
    ComObj *s = com_this(arg(c, 1));
    if (dev)
        bind_depth_stencil(dev, (s && s->kind == K_D3D9SURFACE) ? s : nullptr); // the device holds the bound one
    com_ret(c, D3D_OK9);
}

// (this, pVertexElements, ppDecl): the element array is kept verbatim, since
// only the eventual renderer can interpret it.
void Dev_CreateVertexDeclaration(X86 *c) {
    ComObj *dev = this_device9(c);
    uint32_t elems = arg(c, 1), out = arg(c, 2);
    if (!dev || !out) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    ComObj *d = com_new(K_D3D9DECL);
    if (!d) {
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    d->dev_d3d = dev->id;
    // D3DVERTEXELEMENT9 is 8 bytes and the array ends with D3DDECL_END, whose
    // Stream is 0xff. Copy until that, so the declaration survives the call.
    for (uint32_t i = 0; i < 64 && elems; ++i) {
        uint32_t e = elems + i * 8u;
        uint16_t stream = rd16(e);
        for (int b = 0; b < 8; ++b)
            d->blob.push_back(gm_ptr(e)[b]);
        if (stream == 0xff)
            break;
    }
    uint32_t view_ = com_view(d, IF_D3DVERTEXDECL9);
    if (!view_) {
        com_release(d);
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    com_out_ptr(out, view_);
    com_ret(c, D3D_OK9);
}

void Dev_SetVertexDeclaration(X86 *c) {
    ComObj *dev = this_device9(c);
    ComObj *d = com_this(arg(c, 1));
    if (dev) {
        dev->current_viewport = (d && d->kind == K_D3D9DECL) ? d->id : 0;
        d9_pipeline(dev->id).declaration = dev->current_viewport;
    }
    com_ret(c, D3D_OK9);
}

// (this, Type, ppQuery). A query with no GPU behind it answers immediately.
void Dev_CreateQuery(X86 *c) {
    ComObj *dev = this_device9(c);
    uint32_t type = arg(c, 1), out = arg(c, 2);
    if (!dev) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    if (!out) {
        com_ret(c, D3D_OK9); // a null out pointer only asks whether the type exists
        return;
    }
    ComObj *q = com_new(K_D3D9QUERY);
    if (!q) {
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    q->dev_d3d = dev ? dev->id : 0;
    q->dev_type = type;
    uint32_t view_ = com_view(q, IF_D3DQUERY9);
    if (!view_) {
        com_release(q);
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    com_out_ptr(out, view_);
    com_ret(c, D3D_OK9);
}

// (this, StreamNumber, pStreamData, OffsetInBytes, Stride)
void Dev_SetStreamSource(X86 *c) {
    ComObj *dev = this_device9(c);
    ComObj *vb = com_this(arg(c, 2));
    uint32_t stream = arg(c, 1);
    if (dev && stream < 8) {
        D9Pipeline::Stream &st = d9_pipeline(dev->id).stream[stream];
        st.vb = (vb && vb->kind == K_D3D9VB) ? vb->id : 0;
        st.offset = arg(c, 3);
        st.stride = arg(c, 4);
    }
    if (dev && stream == 0 && vb && vb->kind == K_D3D9VB) {
        dev->back_obj = vb->id;
        dev->lock_off = arg(c, 3);
        dev->rate = arg(c, 4); // the stride, reusing a spare field
    }
    com_ret(c, D3D_OK9);
}

void Dev_SetIndices(X86 *c) {
    ComObj *dev = this_device9(c);
    ComObj *ib = com_this(arg(c, 1));
    if (dev) {
        dev->front_obj = (ib && ib->kind == K_D3D9IB) ? ib->id : 0;
        d9_pipeline(dev->id).index_buffer = dev->front_obj;
    }
    com_ret(c, D3D_OK9);
}

// (this, Stage, pTexture): the texture behind a sampler, for the renderer.
void Dev_SetTexture(X86 *c) {
    ComObj *dev = this_device9(c);
    ComObj *tex = com_this(arg(c, 2));
    uint32_t stage = arg(c, 1);
    if (!dev || stage >= 16 || (arg(c, 2) && !tex) ||
        (tex && tex->kind == K_D3D9VOLUMETEXTURE && volumes().at(tex->id).pool > 1)) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    ComObj *old = com_get(d9_pipeline(dev->id).sampler_tex[stage]);
    if (tex && tex->kind == K_D3D9VOLUMETEXTURE)
        com_addref(tex);
    dev->texture_handle = tex ? tex->id : 0;
    d9_pipeline(dev->id).sampler_tex[stage] = tex ? tex->id : 0;
    if (old && old->kind == K_D3D9VOLUMETEXTURE)
        com_release(old);
    com_ret(c, D3D_OK9);
}
void Dev_GetTexture(X86 *c) {
    ComObj *dev = this_device9(c);
    const uint32_t stage = arg(c, 1), out = arg(c, 2);
    if (!dev || stage >= 16 || !out || !gm_valid(out, 4)) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    ComObj *o = com_get(d9_pipeline(dev->id).sampler_tex[stage]);
    wr32(out, o ? o->identity : 0);
    com_addref(o);
    com_ret(c, D3D_OK9);
}
static void device_volume_destroy(ComObj *dev) {
    for (uint32_t &id : d9_pipeline(dev->id).sampler_tex) {
        ComObj *o = com_get(id);
        id = 0;
        if (o && o->kind == K_D3D9VOLUMETEXTURE)
            com_release(o);
    }
}

// The draws. Nothing is rasterized yet, so each one reports what it was asked
// to draw: the geometry a renderer would need is all in these arguments, and
// a run that names them is the specification for the work that comes next.
// The first few are reported in full and then every 256th, because a game at
// speed issues thousands.
static uint32_t g_draws = 0;

static const char *prim_name(uint32_t type) {
    switch (type) {
    case 1:
        return "point list";
    case 2:
        return "line list";
    case 3:
        return "line strip";
    case 4:
        return "triangle list";
    case 5:
        return "triangle strip";
    case 6:
        return "triangle fan";
    default:
        return "unknown primitive";
    }
}

// The first few draws, then (with verbose logging) every 256th: a race makes
// thousands a frame, and writing even one in 256 cost a percent of the frame.
static bool draw_worth_reporting() {
    ++g_draws;
    return g_draws <= 8 || ((g_draws % 256) == 0 && log_level() >= 2);
}

static uint32_t primitive_vertices(uint32_t prim, uint32_t count) {
    switch (prim) {
    case 1:
        return count; // points
    case 2:
        return count * 2; // lines
    case 3:
        return count + 1; // line strip
    case 4:
        return count * 3; // triangles
    case 5:
    case 6:
        return count + 2;
    default:
        return 0;
    }
}

// The render target and depth buffer a draw or clear goes to, mirrored.
static HostD9Target gpu_target(ComObj *dev) {
    HostD9Target t{};
    D9Pipeline &pl = d9_pipeline(dev->id);
    for (int i = 0; i < 4; ++i) {
        ComObj *s = com_get(pl.color_target[i]);
        if (i == 0 && !s)
            s = com_get(dev->render_target) ? com_get(dev->render_target) : device_backbuffer(dev);
        if (!s)
            continue;
        uint32_t slot;
        ComObj *owner = com_get(mirror_slot(s, &slot));
        if (owner)
            gpu_sync(owner);
        t.color[i] = surface_ref(s);
    }
    if (ComObj *z = com_get(dev->zbuffer_obj)) {
        uint32_t slot;
        ComObj *owner = com_get(mirror_slot(z, &slot));
        if (owner)
            gpu_sync(owner);
        t.depth = surface_ref(z);
    }
    return t;
}
static void gpu_target_drawn(ComObj *dev, bool color, bool depth) {
    D9Pipeline &pl = d9_pipeline(dev->id);
    if (color)
        for (int i = 0; i < 4; ++i) {
            ComObj *s = com_get(pl.color_target[i]);
            if (i == 0 && !s)
                s = com_get(dev->render_target);
            gpu_drawn_into(s);
        }
    if (depth)
        gpu_drawn_into(com_get(dev->zbuffer_obj));
}
static void target_viewport(ComObj *dev, const HostD9Target &t, int32_t vp[4]) {
    D9Pipeline &pl = d9_pipeline(dev->id);
    if (pl.viewport_set) {
        for (int i = 0; i < 4; ++i)
            vp[i] = (int32_t)pl.viewport[i];
        return;
    }
    ComObj *rt =
        com_get(pl.color_target[0]) ? com_get(pl.color_target[0]) : com_get(dev->render_target);
    (void)t;
    vp[0] = vp[1] = 0;
    vp[2] = rt ? (int32_t)rt->width : (int32_t)dev->width;
    vp[3] = rt ? (int32_t)rt->height : (int32_t)dev->height;
}

// Only observes calls already routed to the GPU. Does not infer guest visibility
// decisions or model ownership. Correlate serial + target + resources within one run.
static void observe_draw(X86 *c, ComObj *dev, HostD9Draw &d, uint32_t api) {
    if (!host_d9_admission_enabled())
        return;
    static uint64_t serial = 0; // guest execution is serialized by the scheduler baton
    d.admission.serial = ++serial;
    host_d9_admission_entry(serial);
    d.admission.api = api;
    d.admission.caller = rd32(c->r[R_ESP]);
    d.admission.device = dev->identity;
    if (api >= 83) {
        d.admission.inline_vertices = arg(c, api == 83 ? 3 : 7);
        d.admission.inline_indices = api == 84 ? arg(c, 5) : 0;
        d.stream[0].stride = arg(c, api == 83 ? 4 : 8);
        return; // UP calls do not use the device's bound buffers
    }
    const D9Pipeline &pl = d9_pipeline(dev->id);
    for (int i = 0; i < 9; ++i)
        if (ComObj *o = com_get(i < 8 ? pl.stream[i].vb : pl.index_buffer))
            d.admission.resource_identity[i] = o->identity;
}
static void reject_draw(ComObj *dev, const HostD9Draw &attempt, uint32_t reason) {
    if (!attempt.admission.serial)
        return;
    // Metadata only: never gpu_target()/gpu_sync(), never touch invalid UP pointers.
    HostD9Draw d{};
    d.admission = attempt.admission;
    d.admission.reason = reason;
    d.primitive = attempt.primitive;
    d.primitive_count = attempt.primitive_count;
    d.start = attempt.start;
    d.base_vertex = attempt.base_vertex;
    const D9Pipeline &pl = d9_pipeline(dev->id);
    d.label = pl.label;
    d.decl_id = pl.declaration;
    d.vs_key = pl.vs_key;
    d.ps_key = pl.ps_key;
    for (int i = 0; i < 8; ++i)
        d.stream[i] = {pl.stream[i].vb, pl.stream[i].offset, pl.stream[i].stride};
    d.index_buffer = pl.index_buffer;
    if (d.admission.api >= 83) {
        memset(d.stream, 0, sizeof d.stream);
        d.stream[0].stride = attempt.stream[0].stride;
        d.index_buffer = 0;
    }
    for (int i = 0; i < 4; ++i) {
        ComObj *s = com_get(pl.color_target[i]);
        if (!s && i == 0)
            s = com_get(dev->render_target);
        if (s)
            d.target.color[i] = surface_ref(s);
    }
    if (ComObj *z = com_get(dev->zbuffer_obj))
        d.target.depth = surface_ref(z);
    target_viewport(dev, d.target, d.viewport);
    host_d9_rejected_draw(&d);
}

// Remaster run 10 fused pool shader: why a fuse request cannot take the pool variant (0: it can). The fused pixel
// shader adds the pool light to the base output, so the base must not blend; the vertex shader must read the four
// inputs the pool needs (object-space position, normal, uv, tangent); stages 0 and 1 (diffuse, normal map, as the
// pool pass samples them) must be plain 2D textures.
// Remaster run 11 (review/run11/design-A.md): an explicit supported set - the WORLD / WORLDNORMALMAP pool copies, one
// colour target, full colour writes, no stencil, depth test LESS/LESSEQUAL, no depth texture on stages 0/1 - and
// readiness: a (programs, form) fuses only after the renderer has built it (host_d9_pool_state); until then the draw
// is two-pass and probes. Every reason below draws the native two-pass, except UP (inline-vertex draws: no fusion and
// no native second pass, as the guest's own loop drew them before fusion).
enum {
    POOL_OK, POOL_BLENDED, POOL_INPUTS, POOL_SAMPLERS, POOL_TEXTURES, POOL_EFFECT, POOL_TARGETS, POOL_WRITEMASK,
    POOL_STENCIL, POOL_DEPTH, POOL_DEPTHTEX, POOL_FORCED, POOL_NOTREADY, POOL_BLOCKED, POOL_REASONS
};
static const char *const kPoolReason[POOL_REASONS] = {"ok", "blended", "inputs", "samplers", "textures", "effect",
                                                      "targets", "writemask", "stencil", "depth", "depthtex", "forced",
                                                      "notready", "blocked"};
// Cumulative for the whole run (never reset): printed every 600 presents and at exit.
static uint64_t g_pool_requests, g_pool_fused, g_pool_fallback[POOL_REASONS], g_pool_fallback_failed, g_pool_up,
    g_pool_probes;
// TEST ONLY (RECOMP_REMASTER_POOL_FORCE, run 11 fixtures): inelig = every request takes the native two-pass;
// nopass = as inelig, and the native second pass reports the clean_pools pass missing (nothing added).
static int pool_force_game() {
    static const int f = [] {
        const char *e = getenv("RECOMP_REMASTER_POOL_FORCE");
        return !e ? 0 : !strcmp(e, "inelig") ? 1 : !strcmp(e, "nopass") ? 2 : 0;
    }();
    return f;
}
static int pool_ineligible(const D9Pipeline &pl, const d9sh::Program &vs, const d9sh::Program &ps,
                           const HostD9Draw &d) {
    if (pool_force_game())
        return POOL_FORCED;
    if (pl.pool_fuse > 2)
        return POOL_EFFECT;
    if (d.target.color[1].id || d.target.color[2].id || d.target.color[3].id)
        return POOL_TARGETS;
    if (pl.rs[27])
        return POOL_BLENDED;
    if ((pl.rs[168] & 0xf) != 0xf)
        return POOL_WRITEMASK;
    if (pl.rs[52])
        return POOL_STENCIL;
    if (!pl.rs[7] || !d.target.depth.id || (pl.rs[23] != 2 && pl.rs[23] != 4))
        return POOL_DEPTH;
    const bool mapped = pl.pool_fuse == 1;   // the normal-mapped form also needs TANGENT0 and stage 1
    uint32_t have = 0;
    for (const d9sh::DclIn &in : vs.inputs)
        if (in.type == d9sh::R_INPUT && in.usage_index == 0)
            have |= in.usage == 0 ? 1u : in.usage == 3 ? 2u : in.usage == 5 ? 4u : in.usage == 6 ? 8u : 0u;
    if ((have & (mapped ? 15u : 7u)) != (mapped ? 15u : 7u))
        return POOL_INPUTS;
    if (ps.major < 2)
        return POOL_SAMPLERS;
    for (uint32_t s = 0; s < (mapped ? 2u : 1u); ++s) {
        auto k = ps.samplers.find(s);
        if (k != ps.samplers.end() && k->second != d9sh::S_2D)
            return POOL_SAMPLERS;
        ComObj *t = com_get(pl.sampler_tex[s]);
        if (!t || t->kind != K_D3D9TEXTURE)
            return POOL_TEXTURES;
        if (is_depth_format(t->rmask ? t->rmask : 22) || (resource_usage()[t->id] & USAGE_DEPTHSTENCIL))
            return POOL_DEPTHTEX;   // the renderer samples it as depth: the pool form cannot
    }
    return POOL_OK;
}
static void pool_stats_print(const char *when) {
    if (!g_pool_requests && !g_pool_up)
        return;
    std::string why;
    for (int r = 1; r < POOL_REASONS; ++r)
        if (g_pool_fallback[r])
            why += std::string(why.empty() ? "" : ", ") + kPoolReason[r] + " " + std::to_string(g_pool_fallback[r]);
    uint64_t fb = 0;
    for (uint64_t n : g_pool_fallback)
        fb += n;
    LOGW("d3d9: pool fusion totals (%s): requests %llu, fused %llu, two-pass %llu (%s), probes %llu, "
         "second pass not drawn %llu, UP requests (no pool, as before fusion) %llu",
         when, (unsigned long long)g_pool_requests, (unsigned long long)g_pool_fused, (unsigned long long)fb,
         why.empty() ? "none" : why.c_str(), (unsigned long long)g_pool_probes,
         (unsigned long long)g_pool_fallback_failed, (unsigned long long)g_pool_up);
}
// Per 600 presents, only once fuse requests were seen; cumulative.
static void pool_stats_frame() {
    static uint64_t frames = 0;
    static bool exit_hook = false;
    if (!exit_hook && g_pool_requests) {
        exit_hook = true;
        atexit([] { pool_stats_print("exit"); });
    }
    if (++frames % 600 == 0)
        pool_stats_print(("frame " + std::to_string(frames)).c_str());
}
static void gpu_draw(ComObj *dev, HostD9Draw &d);
struct PoolSecond {
    ComObj *dev;
    HostD9Draw d;
};
static void pool_second_draw(void *ctx) {
    PoolSecond *x = static_cast<PoolSecond *>(ctx);
    gpu_draw(x->dev, x->d);
}

// One draw, handed to the GPU renderer with everything the device has bound.
static void gpu_draw(ComObj *dev, HostD9Draw &d) {
    rprof::Scope prof_total(rprof::DRAW); // Remaster A4 design input (RECOMP_REMASTER_PROF=1)
    uint64_t prof_t = rprof::start();
    D9Pipeline &pl = d9_pipeline(dev->id);
    if (pl.vs.empty() || pl.ps.empty()) {
        reject_draw(dev, d, 1);
        log_once("d3d9.gpu.ffp",
                 "d3d9: a draw with no shaders bound is skipped; the fixed-function "
                 "pipeline is not rendered on the GPU yet");
        return;
    }
    d.target = gpu_target(dev);
    target_viewport(dev, d.target, d.viewport);
    d.depth_range[0] = pl.viewport_z[0];
    d.depth_range[1] = pl.viewport_z[1];
    for (int i = 0; i < 4; ++i)
        d.scissor[i] = pl.scissor[i];
    if (!pl.vs_key)
        pl.vs_key = d9sh::code_key(pl.vs.data(), pl.vs.size());
    if (!pl.ps_key)
        pl.ps_key = d9sh::code_key(pl.ps.data(), pl.ps.size());
    if (!pl.vs_prog || pl.vs_prog_key != pl.vs_key) {
        pl.vs_prog = &d9sh::program_for_key(pl.vs_key, pl.vs.data(), pl.vs.size());
        pl.vs_prog_key = pl.vs_key;
    }
    if (!pl.ps_prog || pl.ps_prog_key != pl.ps_key) {
        pl.ps_prog = &d9sh::program_for_key(pl.ps_key, pl.ps.data(), pl.ps.size());
        pl.ps_prog_key = pl.ps_key;
    }
    const d9sh::Program &vs = *static_cast<const d9sh::Program *>(pl.vs_prog);
    const d9sh::Program &ps = *static_cast<const d9sh::Program *>(pl.ps_prog);
    // Remaster run 10: a fuse request for the programs still bound: the pool variant, or the native second pass.
    // Run 11: only for a ready (programs, form) in the supported set; an unknown one probes; UP draws neither.
    std::unique_ptr<PoolSecond> pool_second;
    if (pl.pool_fuse && pl.pool_vs == pl.vs.data() && pl.pool_ps == pl.ps.data()) {
        if (d.inline_vertices) {
            if (!g_pool_up++)
                LOGW("d3d9: pool fusion: a fuse request at an inline-vertex draw (%s): drawn without the pool, as "
                     "before fusion", pl.label ? pl.label : "no label");
        } else {
            ++g_pool_requests;
            int why = pool_ineligible(pl, vs, ps, d);
            uint64_t key = 0;
            if (why == POOL_OK) {
                // The key includes the declared 2D stages holding depth (shadow maps): they change the shaders
                // (challenge run 11 item 2). The renderer computes the same mask (d3d9_metal.mm pool_depth_stages).
                uint32_t depth_stages = 0;
                for (const auto &kv : ps.samplers) {
                    if (kv.first >= 16 || kv.second != d9sh::S_2D)
                        continue;
                    ComObj *t = com_get(pl.sampler_tex[kv.first]);
                    if (t && t->kind == K_D3D9TEXTURE &&
                        (is_depth_format(t->rmask ? t->rmask : 22) || (resource_usage()[t->id] & USAGE_DEPTHSTENCIL)))
                        depth_stages |= 1u << kv.first;
                }
                key = host_d9_pool_key(pl.vs_key, pl.ps_key, pl.pool_fuse | depth_stages << 8);
                const int st = host_d9_pool_state(key);
                why = st == HOST_D9_POOL_READY ? POOL_OK : st == HOST_D9_POOL_BLOCKED ? POOL_BLOCKED : POOL_NOTREADY;
            }
            if (why == POOL_OK) {
                d.pool_fuse = pl.pool_fuse;
                d.pool_block = &pl.pool_block[0][0];
                ++g_pool_fused;
            } else {
                ++g_pool_fallback[why];
                pool_second.reset(new PoolSecond{dev, d});
                if (why == POOL_NOTREADY) {   // the renderer builds this form for the base draw (drawn plain)
                    d.pool_probe = pl.pool_fuse;
                    ++g_pool_probes;
                }
            }
        }
    }
    d.vs_key = pl.vs_key;
    d.ps_key = pl.ps_key;
    d.programs_ready = 1u; // Remaster C2: the render thread uses these programs (no second lookup)
    prof_t = rprof::lap(rprof::DRAW_PROG, prof_t);
    d.vs = pl.vs.data();
    d.vs_size = (uint32_t)pl.vs.size();
    d.ps = pl.ps.data();
    d.ps_size = (uint32_t)pl.ps.size();
    d.vconst = &pl.vconst[0][0];
    d.vconst_count = std::min<uint32_t>(vs.max_const, 256);
    d.pconst = &pl.pconst[0][0];
    d.pconst_count = std::min<uint32_t>(ps.max_const, 224);
    memcpy(d.vbool, pl.vbool, sizeof d.vbool);
    memcpy(d.pbool, pl.pbool, sizeof d.pbool);
    ComObj *decl = com_get(pl.declaration);
    if (!decl || decl->kind != K_D3D9DECL) {
        reject_draw(dev, d, 2);
        log_once("d3d9.gpu.nodecl", "d3d9: a draw with no vertex declaration is skipped");
        return;
    }
    d.decl = decl->blob.data();
    d.decl_size = (uint32_t)decl->blob.size();
    d.decl_id = decl->id;
    for (int i = 0; i < 8; ++i) {
        if (d.stream[i].stride && !d.stream[i].buffer)
            continue; // the draw's own inline vertices
        ComObj *vb = com_get(pl.stream[i].vb);
        if (!vb)
            continue;
        gpu_sync_buffer(vb);
        d.stream[i].buffer = vb->id;
        d.stream[i].offset = pl.stream[i].offset;
        d.stream[i].stride = pl.stream[i].stride;
    }
    prof_t = rprof::lap(rprof::DRAW_STREAMS, prof_t);
    uint32_t sampler_state[16][14];
    bool volume_lod = false;
    for (int s = 0; s < 16; ++s) {
        ComObj *tex = com_get(pl.sampler_tex[s]);
        if (!tex || (tex->kind != K_D3D9TEXTURE && tex->kind != K_D3D9SURFACE &&
                     tex->kind != K_D3D9VOLUMETEXTURE))
            continue;
        gpu_sync(tex);
        d.sampler_texture[s] = tex->id;
        if (tex->kind == K_D3D9VOLUMETEXTURE && volumes().at(tex->id).lod) {
            if (!volume_lod) {
                memcpy(sampler_state, pl.sampler_state, sizeof sampler_state);
                volume_lod = true;
            }
            sampler_state[s][9] = std::max(sampler_state[s][9], volumes().at(tex->id).lod);
        }
    }
    d.sampler_state = volume_lod ? &sampler_state[0][0] : &pl.sampler_state[0][0];
    prof_t = rprof::lap(rprof::DRAW_SAMPLERS, prof_t);
    d.render_state = pl.rs;
    d.render_state_set = (const uint8_t *)pl.rs_set;
    // A volume-LOD draw passes a local copy (sampler [s][9] raised), not the pipeline's tables: version 0 so the queue
    // neither reuses an earlier table for it nor lets the next draw reuse this one (Remaster C-A4L1 finding).
    d.state_version = volume_lod ? 0 : pl.state_version;
    // Test133: private render state 251 = a guest texture pointer the mod counts as scene content under the HUD.
    if (pl.rs[251] && pl.sampler_tex[0]) {
        ComObj *scene_tex = com_this(pl.rs[251]);
        d.hdr_scene_texture = scene_tex && scene_tex->id == pl.sampler_tex[0];
    }
    for (int st = 0; st < 8; ++st)
        if (pl.tss[st][24] & 0x100) // D3DTSS_TEXTURETRANSFORMFLAGS, D3DTTFF_PROJECTED
            d.projected_mask |= 1u << st;
    d.label = pl.label;
    prof_t = rprof::start();
    host_d9_draw(&d);
    rprof::lap(rprof::QUEUE, prof_t);
    bool color_write = !pl.rs_set[168] || (pl.rs[168] & 0xf) != 0;
    bool z_write = (!pl.rs_set[14] || pl.rs[14]) && (!pl.rs_set[7] || pl.rs[7]);
    gpu_target_drawn(dev, color_write, z_write);
    if (pool_second && (pool_force_game() == 2 || !d3dx9_pool_second_pass(pl, pool_second_draw, pool_second.get())))
        ++g_pool_fallback_failed;
}

// Hands one draw to the CPU renderer with what the device has bound.
static void raster(X86 *c, const D9DrawCall &call) {
    ComObj *dev = this_device9(c);
    if (!dev)
        return;
    ComObj *target = com_get(dev->render_target);
    if (!target)
        target = device_backbuffer(dev);
    ComObj *decl = com_get(dev->current_viewport); // SetVertexDeclaration keeps it here
    static const std::vector<uint8_t> none;
    d9_raster_draw(dev, target, (decl && decl->kind == K_D3D9DECL) ? decl->blob : none, call);
}

// (this, PrimitiveType, StartVertex, PrimitiveCount)
static bool draw_refused(X86 *c, const char *what, uint32_t prim, uint32_t count, uint32_t vertices, uint32_t stride);
// Remaster A4 gate (TEST ONLY): A/B switch nulldraw. During OLD periods every draw returns D3D_OK at entry without
// any shim or host work (frames are not drawn), so the game-thread time saved is the most that moving draw work off
// the game thread could save. Opt-in only: never part of the default "all switches" set.
static bool null_draw(X86 *c) {
    // Remaster P1 (TEST ONLY): RECOMP_REMASTER_POOLDRAW_NULL=1 with A/B switch poolnull: in OLD periods only the draws
    // made while the clean pool pass is in effect return at entry (the pool pass's states and constants still apply).
    // Diagnostic: it bounds the pool draws' shim share (their deferred sync moves to the next draw). Needs PROF=1.
    static const bool poolnull = [] { const char *e = getenv("RECOMP_REMASTER_POOLDRAW_NULL"); return e && *e == '1'; }();
    if (poolnull && rprof::pool_active && recomp_ab::old(recomp_ab::POOLNULL)) {
        com_ret(c, D3D_OK9);
        return true;
    }
    if (!recomp_ab::old(recomp_ab::NULLDRAW))
        return false;
    com_ret(c, D3D_OK9);
    return true;
}

void Dev_DrawPrimitive(X86 *c) {
    if (draw_worth_reporting())
        LOGW("d3d9: draw %u: %s, %u primitives from vertex %u, from the bound stream", g_draws,
             prim_name(arg(c, 1)), arg(c, 3), arg(c, 2));
    if (draw_refused(c, "DrawPrimitive", arg(c, 1), arg(c, 3), 0, 0)) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    if (null_draw(c))
        return;
    ComObj *dev = this_device9(c);
    if (dev && gpu_on()) {
        HostD9Draw d{};
        observe_draw(c, dev, d, 81);
        d.primitive = arg(c, 1);
        d.primitive_count = arg(c, 3);
        d.start = arg(c, 2);
        gpu_draw(dev, d);
        com_ret(c, D3D_OK9);
        return;
    }
    ComObj *vb = dev ? com_get(dev->back_obj) : nullptr;
    if (vb && resource_bytes(vb) && dev->lock_off < vb->blob.size()) {
        D9DrawCall call;
        call.prim = arg(c, 1);
        call.prim_count = arg(c, 3);
        call.vertex_data = resource_bytes(vb) + dev->lock_off;
        call.vertex_bytes = vb->blob.size() - dev->lock_off;
        call.stride = dev->rate;
        call.first = arg(c, 2);
        raster(c, call);
    }
    com_ret(c, D3D_OK9);
}

// (this, Type, BaseVertexIndex, MinVertexIndex, NumVertices, startIndex, primCount)
void Dev_DrawIndexedPrimitive(X86 *c) {
    if (draw_worth_reporting())
        LOGW("d3d9: draw %u: %s, %u primitives, %u vertices from index %u, indexed", g_draws,
             prim_name(arg(c, 1)), arg(c, 6), arg(c, 4), arg(c, 5));
    if (draw_refused(c, "DrawIndexedPrimitive", arg(c, 1), arg(c, 6), arg(c, 4), 0)) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    if (null_draw(c))
        return;
    ComObj *dev = this_device9(c);
    if (dev && gpu_on()) {
        HostD9Draw d{};
        observe_draw(c, dev, d, 82);
        d.primitive = arg(c, 1);
        d.primitive_count = arg(c, 6);
        d.start = arg(c, 5);
        d.base_vertex = (int32_t)arg(c, 2);
        ComObj *ibo = com_get(d9_pipeline(dev->id).index_buffer);
        if (ibo) {
            gpu_sync_buffer(ibo);
            d.index_buffer = ibo->id;
            d.index_size = ibo->rmask ? ibo->rmask : 2;
            gpu_draw(dev, d);
        } else {
            reject_draw(dev, d, 3);
        }
        com_ret(c, D3D_OK9);
        return;
    }
    ComObj *vb = dev ? com_get(dev->back_obj) : nullptr;
    ComObj *ib = dev ? com_get(dev->front_obj) : nullptr;
    if (vb && resource_bytes(vb) && ib && resource_bytes(ib) && dev->lock_off < vb->blob.size()) {
        D9DrawCall call;
        call.prim = arg(c, 1);
        call.prim_count = arg(c, 6);
        call.vertex_data = resource_bytes(vb) + dev->lock_off;
        call.vertex_bytes = vb->blob.size() - dev->lock_off;
        call.stride = dev->rate;
        call.index_data = resource_bytes(ib);
        call.index_bytes = ib->blob.size();
        call.index_size = ib->rmask ? ib->rmask : 2;
        call.base_vertex = (int32_t)arg(c, 2);
        call.first = arg(c, 5);
        raster(c, call);
    }
    com_ret(c, D3D_OK9);
}

// (this, PrimitiveType, PrimitiveCount, pVertexStreamZeroData, Stride): the
// vertices travel with the call rather than in a buffer, which is how this
// game draws.
// What a rasterizer will need from a draw, spelled out for the first few: the
// bound vertex declaration decoded, the raw vertices as floats, the bound
// texture, and the surface being drawn into.
static void describe_draw_inputs(X86 *c, uint32_t vertices, uint32_t stride, uint32_t nverts) {
    ComObj *dev = this_device9(c);
    if (!dev)
        return;
    ComObj *decl = com_get(dev->current_viewport); // SetVertexDeclaration keeps it here
    if (decl && decl->kind == K_D3D9DECL) {
        static const char *types[] = {"FLOAT1",    "FLOAT2",   "FLOAT3",   "FLOAT4",  "D3DCOLOR",
                                      "UBYTE4",    "SHORT2",   "SHORT4",   "UBYTE4N", "SHORT2N",
                                      "SHORT4N",   "USHORT2N", "USHORT4N", "UDEC3",   "DEC3N",
                                      "FLOAT16_2", "FLOAT16_4"};
        static const char *usages[] = {
            "POSITION", "BLENDWEIGHT", "BLENDINDICES", "NORMAL", "PSIZE", "TEXCOORD", "TANGENT",
            "BINORMAL", "TESSFACTOR",  "POSITIONT",    "COLOR",  "FOG",   "DEPTH",    "SAMPLE"};
        char line[512];
        size_t used = 0;
        for (size_t i = 0; i + 8 <= decl->blob.size(); i += 8) {
            uint16_t stream = (uint16_t)(decl->blob[i] | (decl->blob[i + 1] << 8));
            if (stream == 0xff)
                break;
            uint16_t off = (uint16_t)(decl->blob[i + 2] | (decl->blob[i + 3] << 8));
            uint8_t type = decl->blob[i + 4], usage = decl->blob[i + 6], index = decl->blob[i + 7];
            used += (size_t)snprintf(line + used, sizeof line - used, " %s%u@%u:%s",
                                     usage < 14 ? usages[usage] : "?", index, off,
                                     type < 17 ? types[type] : "?");
            if (used >= sizeof line)
                break;
        }
        LOGW("d3d9:   declaration %u:%s", decl->id, line);
    } else {
        LOGW("d3d9:   no vertex declaration bound (FVF path)");
    }
    for (uint32_t v = 0; v < nverts && v < 4; ++v) {
        uint32_t base = vertices + v * stride;
        char line[256];
        size_t used = 0;
        for (uint32_t o = 0; o + 4 <= stride && o < 40; o += 4)
            used += (size_t)snprintf(
                line + used, sizeof line - used, " %g", (double)[&] {
                    float f;
                    uint32_t bits = rd32(base + o);
                    memcpy(&f, &bits, 4);
                    return f;
                }());
        LOGW("d3d9:   vertex %u:%s", v, line);
    }
    ComObj *tex = com_get(dev->texture_handle);
    ComObj *rt = com_get(dev->render_target);
    LOGW("d3d9:   texture %u (%ux%u), target %u (%ux%u)%s", tex ? tex->id : 0, tex ? tex->width : 0,
         tex ? tex->height : 0, rt ? rt->id : 0, rt ? rt->width : 0, rt ? rt->height : 0,
         (rt && rt->id == dev->palette_obj) ? ", the back buffer" : "");
}


// Test155 (owner session 3 Oct 19:54: the game's memory footprint went from 3.1 GB to 361 GB within a minute in
// rain and macOS killed it for low swap; 361 GB / 2^31 = ~180 bytes per primitive). The UP draws computed their
// vertex bytes in 32 bits, so a garbage primitive count (e.g. 0x80000000 + n) wrapped to a size that still fit the
// guest arena and was accepted. Direct3D 9 limits every draw to the device's MaxPrimitiveCount (and indexed draws
// to MaxVertexIndex) and an invalid draw returns D3DERR_INVALIDCALL. All four draw calls check the caps values
// this device reports, before any size arithmetic or allocation, and log the raw parameters and the caller.
static uint64_t primitive_vertices64(uint32_t prim, uint32_t count) {
    switch (prim) {
    case 1: return count;
    case 2: return (uint64_t)count * 2;
    case 3: return (uint64_t)count + 1;
    case 4: return (uint64_t)count * 3;
    case 5:
    case 6: return (uint64_t)count + 2;
    default: return 0;
    }
}
static bool draw_refused(X86 *c, const char *what, uint32_t prim, uint32_t count, uint32_t vertices, uint32_t stride) {
    if (count <= D9_MAX_PRIMITIVE_COUNT && vertices <= (uint64_t)D9_MAX_VERTEX_INDEX + 1)
        return false;
    static uint64_t refused = 0;
    ++refused;
    if (refused <= 16 || (refused & (refused - 1)) == 0)
        LOGW("d3d9: %s refused (D3DERR_INVALIDCALL, as Direct3D 9): primitive count %u (0x%08x, signed %d), type %u, "
             "%u vertices, stride %u, caller %08x; device limits %u primitives / %u vertex index (%llu refused so far)",
             what, count, count, (int32_t)count, prim, vertices, stride, rd32(c->r[R_ESP]), D9_MAX_PRIMITIVE_COUNT,
             D9_MAX_VERTEX_INDEX, (unsigned long long)refused);
    return true;
}

void Dev_DrawPrimitiveUP(X86 *c) {
    if (draw_worth_reporting()) {
        LOGW("d3d9: draw %u: %s, %u primitives, %u-byte vertices inline at %08x", g_draws,
             prim_name(arg(c, 1)), arg(c, 2), arg(c, 4), arg(c, 3));
        // A fan of n primitives has n + 2 vertices.
        if (g_draws <= 8)
            describe_draw_inputs(c, arg(c, 3), arg(c, 4), arg(c, 2) + 2);
    }
    if (draw_refused(c, "DrawPrimitiveUP", arg(c, 1), arg(c, 2), 0, arg(c, 4))) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    if (null_draw(c))
        return;
    if (ComObj *dev = this_device9(c); dev && gpu_on()) {
        HostD9Draw d{};
        observe_draw(c, dev, d, 83);
        d.primitive = arg(c, 1);
        d.primitive_count = arg(c, 2);
        const uint64_t bytes64 = primitive_vertices64(d.primitive, d.primitive_count) * (uint64_t)arg(c, 4);
        uint32_t bytes = bytes64 <= 0xffffffffull ? (uint32_t)bytes64 : 0;
        if (bytes && gm_fits(arg(c, 3), bytes)) {
            d.stream[0].stride = arg(c, 4);
            d.inline_vertices = gm_ptr(arg(c, 3));
            d.inline_bytes = bytes;
            gpu_draw(dev, d);
        } else {
            reject_draw(dev, d, 4);
        }
        com_ret(c, D3D_OK9);
        return;
    }
    D9DrawCall call;
    call.prim = arg(c, 1);
    call.prim_count = arg(c, 2);
    call.vertices = arg(c, 3);
    call.stride = arg(c, 4);
    raster(c, call);
    com_ret(c, D3D_OK9);
}

// (this, PrimitiveType, MinVertexIndex, NumVertices, PrimitiveCount,
//  pIndexData, IndexDataFormat, pVertexStreamZeroData, Stride)
void Dev_DrawIndexedPrimitiveUP(X86 *c) {
    if (draw_worth_reporting())
        LOGW("d3d9: draw %u: %s, %u primitives, %u vertices of %u bytes inline at %08x, "
             "indices at %08x",
             g_draws, prim_name(arg(c, 1)), arg(c, 4), arg(c, 3), arg(c, 8), arg(c, 7), arg(c, 5));
    if (draw_refused(c, "DrawIndexedPrimitiveUP", arg(c, 1), arg(c, 4), arg(c, 3), arg(c, 8))) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    if (null_draw(c))
        return;
    if (ComObj *dev = this_device9(c); dev && gpu_on()) {
        HostD9Draw d{};
        observe_draw(c, dev, d, 84);
        d.primitive = arg(c, 1);
        d.primitive_count = arg(c, 4);
        uint32_t isize = arg(c, 6) == 102 ? 4 : 2;
        const uint64_t ib64 = primitive_vertices64(d.primitive, d.primitive_count) * isize;
        const uint64_t vb64 = ((uint64_t)arg(c, 2) + arg(c, 3)) * (uint64_t)arg(c, 8);
        uint32_t ibytes = ib64 <= 0xffffffffull ? (uint32_t)ib64 : 0;
        uint32_t vbytes = vb64 <= 0xffffffffull ? (uint32_t)vb64 : 0;
        if (ibytes && vbytes && gm_fits(arg(c, 5), ibytes) && gm_fits(arg(c, 7), vbytes)) {
            d.stream[0].stride = arg(c, 8);
            d.inline_vertices = gm_ptr(arg(c, 7));
            d.inline_bytes = vbytes;
            d.inline_indices = gm_ptr(arg(c, 5));
            d.inline_index_bytes = ibytes;
            d.index_size = isize;
            gpu_draw(dev, d);
        } else {
            reject_draw(dev, d, 4);
        }
        com_ret(c, D3D_OK9);
        return;
    }
    D9DrawCall call;
    call.prim = arg(c, 1);
    call.prim_count = arg(c, 4);
    call.vertices = arg(c, 7);
    call.stride = arg(c, 8);
    call.indices = arg(c, 5);
    call.index_size = arg(c, 6) == 102 ? 4 : 2;
    raster(c, call);
    com_ret(c, D3D_OK9);
}

// ---------------------------------------------------------------------------
// The resource interfaces themselves
// ---------------------------------------------------------------------------
#define RES_STUB(iface, name)                                                                      \
    void iface##_##name(X86 *c) {                                                                  \
        log_once("d3d9." #iface "." #name, "d3d9: " #iface "::" #name " is not implemented");      \
        com_ret(c, D3D_OK9);                                                                       \
    }

RES_STUB(Res, SetPrivateData)
RES_STUB(Res, GetPrivateData)
RES_STUB(Res, FreePrivateData)
RES_STUB(Res, SetPriority)
RES_STUB(Res, GetPriority)
RES_STUB(Res, PreLoad)
RES_STUB(Res, GetType)
RES_STUB(Tex, SetLOD)
RES_STUB(Tex, GetLOD)
RES_STUB(Tex, SetAutoGenFilterType)
RES_STUB(Tex, GetAutoGenFilterType)
RES_STUB(Tex, GenerateMipSubLevels)
RES_STUB(Tex, AddDirtyRect)
RES_STUB(Tex, AddDirtyRectCube)
RES_STUB(Surf, GetDC)
RES_STUB(Surf, ReleaseDC)
RES_STUB(Decl, GetDeclaration)

// (this, riid, ppContainer): the texture a level or face belongs to, or the
// device when the surface stands alone.
void Surf_GetContainer(X86 *c) {
    ComObj *s = com_this_arg(c);
    uint32_t out = arg(c, 2);
    if (!s || !out) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    ComObj *parent = com_get(s->front_obj);
    if (parent) {
        com_addref(parent);
        com_out_ptr(out, com_view(parent, parent->caps ? IF_D3DCUBETEXTURE9 : IF_D3DTEXTURE9));
        com_ret(c, D3D_OK9);
        return;
    }
    ComObj *dev = com_get(s->dev_d3d);
    if (!dev) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    com_addref(dev);
    com_out_ptr(out, com_view(dev, IF_D3DDEVICE9));
    com_ret(c, D3D_OK9);
}

void Res_GetDevice(X86 *c) {
    ComObj *o = com_this_arg(c);
    ComObj *dev = o ? com_get(o->dev_d3d) : nullptr;
    uint32_t out = arg(c, 1);
    if (!dev || !out) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    com_addref(dev);
    com_out_ptr(out, com_view(dev, IF_D3DDEVICE9));
    com_ret(c, D3D_OK9);
}

// Lock hands the guest the storage this resource was created with. Unlock is
// a no-op: there is nothing to upload to yet.
static void buffer_lock(X86 *c) {
    ComObj *o = com_this_arg(c);
    uint32_t offset = arg(c, 1), out = arg(c, 3);
    if (!o || !out) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    uint32_t staged = offset < o->blob.size() ? stage_lock(o) : 0;
    com_out_ptr(out, staged ? staged + offset : 0);
    com_ret(c, staged ? D3D_OK9 : E_OUTOFMEMORY);
}
void VB_Lock(X86 *c) {
    buffer_lock(c);
}
void IB_Lock(X86 *c) {
    buffer_lock(c);
}
void Buf_Unlock(X86 *c) {
    stage_unlock(com_this_arg(c));
    com_ret(c, D3D_OK9);
}
// D3DVERTEXBUFFER_DESC / D3DINDEXBUFFER_DESC: Format, Type, Usage, Pool, Size.
void Buf_GetDesc(X86 *c) {
    ComObj *o = com_this_arg(c);
    uint32_t d = arg(c, 1);
    if (!o || !d) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    memset(gm_ptr(d), 0, 20);
    wr32(d + 16, o->buf_bytes);
    com_ret(c, D3D_OK9);
}

// D3DLOCKED_RECT is { INT Pitch; void *pBits; }.
static bool readback_settle(ComObj *s, uint32_t flags);
void Surf_LockRect(X86 *c) {
    ComObj *s = com_this_arg(c);
    uint32_t out = arg(c, 1);
    if (!s || !out) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    // (this, pLockedRect, pRect, Flags): a GetRenderTargetData copy still on the GPU answers
    // D3DERR_WASSTILLDRAWING to a D3DLOCK_DONOTWAIT lock, as a driver does.
    if (!readback_settle(s, arg(c, 3))) {
        com_ret(c, D3DERR_WASSTILLDRAWING);
        return;
    }
    uint32_t staged = stage_lock(s);
    wr32(out, s->pitch);
    wr32(out + 4, staged);
    com_ret(c, staged ? D3D_OK9 : E_OUTOFMEMORY);
}
void Surf_UnlockRect(X86 *c) {
    stage_unlock(com_this_arg(c));
    com_ret(c, D3D_OK9);
}
// D3DSURFACE_DESC: Format, Type, Usage, Pool, MultiSampleType, Quality,
// Width, Height.
void Surf_GetDesc(X86 *c) {
    ComObj *s = com_this_arg(c);
    uint32_t d = arg(c, 1);
    if (!s || !d) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    memset(gm_ptr(d), 0, 32);
    wr32(d + 0, s->rmask ? s->rmask : 22);
    wr32(d + 24, s->width);
    wr32(d + 28, s->height);
    com_ret(c, D3D_OK9);
}

// A texture level is the surface made with the texture.
// A cube face, made on first ask and kept, so the game gets the same surface
// back every time. Returning success without writing the pointer - which is
// what the stub did - had the game store nothing and throw the cube away.
// The surface for one face and level of a cube, made on first ask and kept on
// the texture, indexed face + 6 * level.
static ComObj *cube_face(ComObj *tex, uint32_t face, uint32_t level) {
    if (!tex || tex->kind != K_D3D9TEXTURE || !tex->caps || face > 5 || level > 15)
        return nullptr;
    size_t slot = face + 6u * level;
    if (tex->surfaces.size() <= slot)
        tex->surfaces.resize(slot + 1, 0);
    ComObj *s = com_get(tex->surfaces[slot]);
    if (!s) {
        uint32_t edge = std::max(tex->width >> level, 1u);
        s = make_surface(com_get(tex->dev_d3d), edge, edge, tex->rmask ? tex->rmask : 22);
        if (!s)
            return nullptr;
        s->front_obj = tex->id;
        tex->surfaces[slot] = s->id;
    }
    return s;
}

// (this, FaceType, Level, ppCubeMapSurface). A face is a view into the
// texture, so handing one out references the texture too: the game takes
// face zero, releases the cube, and goes on using it for the other five.
void Tex_GetCubeMapSurface(X86 *c) {
    ComObj *tex = com_this_arg(c);
    uint32_t out = arg(c, 3);
    ComObj *s = cube_face(tex, arg(c, 1), arg(c, 2));
    if (!s || !out) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    uint32_t view = com_view(s, IF_D3DSURFACE9);
    if (!view) {
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    surface_addref(s); // a face shares its cube texture's lifetime (Test159)
    com_out_ptr(out, view);
    com_ret(c, D3D_OK9);
}

// Returns a count, not an HRESULT. The stub returned zero, which told the
// game its textures had no levels at all.
// (this, FaceType, Level, pLockedRect, pRect, Flags)
void Tex_LockRectCube(X86 *c) {
    ComObj *s = cube_face(com_this_arg(c), arg(c, 1), arg(c, 2));
    uint32_t out = arg(c, 3);
    if (!s || !out) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    uint32_t staged = stage_lock(s);
    wr32(out, s->pitch);
    wr32(out + 4, staged);
    com_ret(c, staged ? D3D_OK9 : E_OUTOFMEMORY);
}
// (this, FaceType, Level)
void Tex_UnlockRectCube(X86 *c) {
    stage_unlock(cube_face(com_this_arg(c), arg(c, 1), arg(c, 2)));
    com_ret(c, D3D_OK9);
}

void Tex_GetLevelCount(X86 *c) {
    ComObj *tex = com_this_arg(c);
    set_eax(c, (tex && !tex->caps && !tex->surfaces.empty()) ? (uint32_t)tex->surfaces.size() : 1);
}

// (this, Level, pDesc): the same D3DSURFACE_DESC a surface describes itself
// with, for the level the texture was created at.
void Tex_GetLevelDesc(X86 *c) {
    ComObj *tex = com_this_arg(c);
    ComObj *s = texture_level(tex, arg(c, 1));
    uint32_t d = arg(c, 2);
    if (!tex || !d) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    memset(gm_ptr(d), 0, 32);
    wr32(d + 0, tex->rmask ? tex->rmask : 22);
    wr32(d + 24, s ? s->width : tex->width);
    wr32(d + 28, s ? s->height : tex->height);
    com_ret(c, D3D_OK9);
}

void Tex_GetSurfaceLevel(X86 *c) {
    ComObj *tex = com_this_arg(c);
    uint32_t out = arg(c, 2);
    ComObj *s = texture_level(tex, arg(c, 1));
    if (!s || !out) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    // A level is a view into the texture and references it, the same way a
    // cube face does.
    s->front_obj = tex->id;
    surface_addref(s); // Test159: the container reference goes with the last outside one
    uint32_t view_ = com_view(s, IF_D3DSURFACE9);
    if (!view_) {
        surface_release(s);
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    com_out_ptr(out, view_);
    com_ret(c, D3D_OK9);
}
void Tex_LockRect(X86 *c) {
    ComObj *s = texture_level(com_this_arg(c), arg(c, 1));
    uint32_t out = arg(c, 2);
    if (!s || !out) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    uint32_t staged = stage_lock(s);
    wr32(out, s->pitch);
    wr32(out + 4, staged);
    com_ret(c, staged ? D3D_OK9 : E_OUTOFMEMORY);
}
void Tex_UnlockRect(X86 *c) {
    stage_unlock(texture_level(com_this_arg(c), arg(c, 1)));
    com_ret(c, D3D_OK9);
}

// Volume textures use the same guest ABI and canonical host storage as 2D
// resources. Each mip owns a whole 3D box; uploads preserve the Z slices.
static const uint8_t IID_VolumeTexture[16] =
    IID_BYTES(0x2518526c, 0xe789, 0x4111, 0xa7, 0xb9, 0x47, 0xef, 0x32, 0x8d, 0x13, 0xe6);
static const uint8_t IID_Volume[16] =
    IID_BYTES(0x24f416e6, 0x1f67, 0x4aa7, 0xb8, 0x8e, 0xd3, 0x3f, 0x6f, 0x31, 0x28, 0xa1);
static const uint8_t IID_BaseTexture[16] =
    IID_BYTES(0x580ca87e, 0x1d3c, 0x4d54, 0x99, 0x1d, 0xb7, 0xd3, 0xe3, 0xc2, 0x98, 0xce);
static const uint8_t IID_Resource[16] =
    IID_BYTES(0x05eec05d, 0x8f7d, 0x4362, 0xb9, 0x99, 0xd1, 0xba, 0xf3, 0x57, 0xc7, 0x04);
static bool volume_base_iid(uint32_t p) {
    return p && gm_valid(p, 16) &&
           (!memcmp(gm_ptr(p), IID_BaseTexture, 16) || !memcmp(gm_ptr(p), IID_Resource, 16));
}
static void VolTex_QueryInterface(X86 *c) {
    ComObj *o = com_this_arg(c);
    const uint32_t out = arg(c, 2);
    if (o && out && gm_valid(out, 4) && volume_base_iid(arg(c, 1))) {
        wr32(out, o->identity); // prefix interfaces have the same controlling identity
        com_addref(o);
        com_ret(c, S_OK);
    } else {
        com_QueryInterface(c);
    }
}
void Dev_CreateVolumeTexture(X86 *c) {
    ComObj *dev = this_device9(c);
    const uint32_t w = arg(c, 1), h = arg(c, 2), depth = arg(c, 3), usage = arg(c, 5),
                   fmt = arg(c, 6), pool = arg(c, 7), out = arg(c, 8);
    uint32_t levels = arg(c, 4);
    if (!out || !gm_valid(out, 4)) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    wr32(out, 0);
    if (!dev || !d9volume::extent(w, h, depth, levels) || !d9volume::pixel_bytes(fmt) || pool > 3 ||
        (usage & ~0x210u) || ((usage & 0x200) && pool != 0) || arg(c, 9)) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    levels = levels ? levels : d9volume::levels(w, h, depth);
    ComObj *tex = com_new(K_D3D9VOLUMETEXTURE);
    if (!tex) {
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    volumes()[tex->id] = {depth, pool};
    tex->dev_d3d = dev->id;
    tex->width = w;
    tex->height = h;
    tex->bpp = d9volume::pixel_bytes(fmt) * 8;
    tex->rmask = fmt;
    resource_usage()[tex->id] = usage;
    bool complete = true;
    for (uint32_t l = 0; l < levels; ++l) {
        ComObj *v = com_new(K_D3D9VOLUME);
        if (!v) {
            complete = false;
            break;
        }
        tex->surfaces.push_back(v->id);
        v->dev_d3d = dev->id;
        v->front_obj = tex->id;
        v->width = std::max(w >> l, 1u);
        v->height = std::max(h >> l, 1u);
        v->rmask = fmt;
        v->bpp = tex->bpp;
        v->pitch = v->width * (v->bpp / 8);
        volumes()[v->id] = {std::max(depth >> l, 1u), pool};
        resource_usage()[v->id] = usage;
        resource_alloc(v, v->pitch * v->height * volumes().at(v->id).depth);
    }
    const uint32_t view = complete ? com_view(tex, IF_D3DVOLUMETEXTURE9) : 0;
    if (!view) {
        com_release(tex);
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    wr32(out, view);
    com_ret(c, D3D_OK9);
}
static bool volume_box(ComObj *v, uint32_t box, uint32_t &offset) {
    offset = 0;
    if (!v)
        return false;
    if (!box)
        return true;
    if (!gm_valid(box, 24))
        return false;
    const uint32_t x = rd32(box), y = rd32(box + 4), r = rd32(box + 8), b = rd32(box + 12),
                   z = rd32(box + 16), back = rd32(box + 20);
    if (x >= r || r > v->width || y >= b || b > v->height || z >= back ||
        back > volumes().at(v->id).depth)
        return false;
    offset = z * v->pitch * v->height + y * v->pitch + x * (v->bpp / 8);
    return true;
}
static void volume_lock(X86 *c, ComObj *v, uint32_t out, uint32_t box, uint32_t flags) {
    uint32_t offset;
    if (!v || !out || !gm_valid(out, 12) || v->lock_count || !volume_box(v, box, offset) ||
        (flags & ~0xa810u) ||
        ((flags & 0x2000) && ((flags & 0x10) || box || !(resource_usage()[v->id] & 0x200))) ||
        (volumes().at(v->id).pool == 0 && !(resource_usage()[v->id] & 0x200))) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    const uint32_t data = stage_lock(v);
    if (!data) {
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    volumes().at(v->id).lock_flags = flags;
    wr32(out, v->pitch);
    wr32(out + 4, v->pitch * v->height);
    wr32(out + 8, data + offset);
    com_ret(c, D3D_OK9);
}
static void volume_unlock(X86 *c, ComObj *v) {
    if (!v || !v->lock_count || !v->pixels) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    const uint32_t flags = volumes().at(v->id).lock_flags;
    if (!(flags & 0x10))
        memcpy(v->blob.data(), gm_ptr(v->pixels), v->blob.size());
    heap_free(v->pixels);
    v->pixels = 0;
    v->lock_count = 0;
    if (!(flags & (0x10 | 0x8000)))
        gpu_after_unlock(v, 0, uint32_t(v->blob.size()));
    com_ret(c, D3D_OK9);
}
static void VolTex_LockBox(X86 *c) {
    volume_lock(c, volume_level(com_this_arg(c), arg(c, 1)), arg(c, 2), arg(c, 3), arg(c, 4));
}
static void VolTex_UnlockBox(X86 *c) {
    volume_unlock(c, volume_level(com_this_arg(c), arg(c, 1)));
}
static void Vol_LockBox(X86 *c) {
    volume_lock(c, com_this_arg(c), arg(c, 1), arg(c, 2), arg(c, 3));
}
static void Vol_UnlockBox(X86 *c) {
    volume_unlock(c, com_this_arg(c));
}
static void volume_desc(X86 *c, ComObj *v, uint32_t out) {
    if (!v || !out || !gm_valid(out, 28)) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    wr32(out, v->rmask);
    wr32(out + 4, 2); // D3DRTYPE_VOLUME
    wr32(out + 8, resource_usage()[v->id]);
    wr32(out + 12, volumes().at(v->id).pool);
    wr32(out + 16, v->width);
    wr32(out + 20, v->height);
    wr32(out + 24, volumes().at(v->id).depth);
    com_ret(c, D3D_OK9);
}
static void VolTex_GetLevelDesc(X86 *c) {
    volume_desc(c, volume_level(com_this_arg(c), arg(c, 1)), arg(c, 2));
}
static void Vol_GetDesc(X86 *c) {
    volume_desc(c, com_this_arg(c), arg(c, 1));
}
static void VolTex_GetVolumeLevel(X86 *c) {
    ComObj *v = volume_level(com_this_arg(c), arg(c, 1));
    const uint32_t out = arg(c, 2);
    if (!out || !gm_valid(out, 4)) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    wr32(out, 0);
    if (!v) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    const uint32_t view = com_view(v, IF_D3DVOLUME9);
    if (!view) {
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    volume_addref(v);
    wr32(out, view);
    com_ret(c, D3D_OK9);
}
static void Vol_GetContainer(X86 *c) {
    ComObj *v = com_this_arg(c), *tex = v ? com_get(v->front_obj) : nullptr;
    const uint32_t iid = arg(c, 1), out = arg(c, 2);
    if (!tex || !iid || !gm_valid(iid, 16) || !out || !gm_valid(out, 4)) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    wr32(out, 0);
    static const uint8_t unknown[16] = IID_BYTES(0, 0, 0, 0xc0, 0, 0, 0, 0, 0, 0, 0x46);
    if (!volume_base_iid(iid) && memcmp(gm_ptr(iid), IID_VolumeTexture, 16) &&
        memcmp(gm_ptr(iid), unknown, 16)) {
        com_ret(c, E_NOINTERFACE);
        return;
    }
    wr32(out, tex->identity);
    com_addref(tex);
    com_ret(c, S_OK);
}
static void Vol_GetDevice(X86 *c) {
    const uint32_t out = arg(c, 1);
    if (!out || !gm_valid(out, 4)) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    Res_GetDevice(c);
}
static void VolTex_GetType(X86 *c) {
    com_ret(c, 4);
}
static void VolTex_GetLevelCount(X86 *c) {
    ComObj *o = com_this_arg(c);
    com_ret(c, o ? uint32_t(o->surfaces.size()) : 0);
}
static void VolTex_PreLoad(X86 *c) {
    ComObj *o = com_this_arg(c);
    if (o && volumes().at(o->id).pool <= 1 && gpu_on())
        gpu_sync(o);
    com_ret(c, 0);
}
static void VolTex_SetLOD(X86 *c) {
    ComObj *o = com_this_arg(c);
    if (!o || volumes().at(o->id).pool != 1) {
        com_ret(c, 0);
        return;
    }
    auto &v = volumes().at(o->id);
    const uint32_t old = v.lod;
    v.lod = std::min(arg(c, 1), uint32_t(o->surfaces.size() - 1));
    com_ret(c, old);
}
static void VolTex_GetLOD(X86 *c) {
    ComObj *o = com_this_arg(c);
    com_ret(c, o ? volumes().at(o->id).lod : 0);
}
static void VolTex_SetPriority(X86 *c) {
    ComObj *o = com_this_arg(c);
    if (!o) {
        com_ret(c, 0);
        return;
    }
    auto &v = volumes().at(o->id);
    const uint32_t old = v.priority;
    v.priority = arg(c, 1);
    com_ret(c, old);
}
static void VolTex_GetPriority(X86 *c) {
    ComObj *o = com_this_arg(c);
    com_ret(c, o ? volumes().at(o->id).priority : 0);
}
static void VolTex_AddDirtyBox(X86 *c) {
    ComObj *o = com_this_arg(c);
    uint32_t offset;
    if (!volume_box(volume_level(o, 0), arg(c, 1), offset)) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    auto it = gpu_mirrors().find(o->id);
    if (it != gpu_mirrors().end()) {
        std::fill(it->second.cpu_dirty.begin(), it->second.cpu_dirty.end(), 1);
        it->second.any_dirty = !it->second.cpu_dirty.empty();
    }
    com_ret(c, D3D_OK9);
}
static void Vol_Unavailable(X86 *c) {
    com_ret(c, D3DERR_NOTAVAILABLE);
}
static void Vol_NoAutoFilter(X86 *c) {
    com_ret(c, 0);
}
// No autogeneration is advertised or admitted at creation. This void method
// cannot fabricate a mip chain; each allocated level must be uploaded by the caller.
static void Vol_NoGenerate(X86 *c) {
    com_ret(c, 0);
}
void Dev_UpdateTexture(X86 *c) {
    ComObj *src = com_this(arg(c, 1)), *dst = com_this(arg(c, 2));
    if ((!src || src->kind != K_D3D9VOLUMETEXTURE) && (!dst || dst->kind != K_D3D9VOLUMETEXTURE)) {
        log_once("d3d9.UpdateTexture", "d3d9: non-volume UpdateTexture is not implemented");
        com_ret(c, D3D_OK9);
        return;
    }
    if (!this_device9(c) || !src || !dst || src->kind != K_D3D9VOLUMETEXTURE ||
        dst->kind != K_D3D9VOLUMETEXTURE || src->dev_d3d != this_device9(c)->id ||
        dst->dev_d3d != src->dev_d3d || volumes().at(src->id).pool != 2 ||
        volumes().at(dst->id).pool != 0 || src->rmask != dst->rmask || src->width != dst->width ||
        src->height != dst->height || volumes().at(src->id).depth != volumes().at(dst->id).depth ||
        src->surfaces.size() != dst->surfaces.size()) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    for (uint32_t l = 0; l < src->surfaces.size(); ++l)
        if (volume_level(src, l)->lock_count || volume_level(dst, l)->lock_count) {
            com_ret(c, D3DERR_INVALIDCALL);
            return;
        }
    for (uint32_t l = 0; l < src->surfaces.size(); ++l) {
        ComObj *v = volume_level(dst, l);
        v->blob = volume_level(src, l)->blob;
        gpu_after_unlock(v, 0, uint32_t(v->blob.size()));
    }
    com_ret(c, D3D_OK9);
}

// D3DQUERYTYPE: 8 is EVENT, 9 OCCLUSION. An occlusion query with a GPU
// renderer counts real samples; without one, or for any other type, the
// query is finished at once, and an occlusion count is reported as one
// sample so that nothing the game gates on visibility disappears.
static const uint32_t D3DQUERYTYPE_EVENT = 8, D3DQUERYTYPE_OCCLUSION = 9;
static const uint32_t S_FALSE9 = 1;
static std::unordered_map<uint32_t, uint32_t> &query_state() { // id -> 1 begun, 2 ended
    static auto *m = new std::unordered_map<uint32_t, uint32_t>();
    return *m;
}
static void query_destroy(ComObj *o) {
    if (query_state().erase(o->id))
        host_d9_query_drop(o->id);
}
void Query_GetType9(X86 *c) {
    ComObj *q = com_this_arg(c);
    set_eax(c, q ? q->dev_type : 0);
}
void Query_GetDataSize(X86 *c) {
    ComObj *q = com_this_arg(c);
    uint32_t type = q ? q->dev_type : 0;
    set_eax(c, (type == D3DQUERYTYPE_OCCLUSION || type == D3DQUERYTYPE_EVENT) ? 4 : 0);
}
// (this, dwIssueFlags): D3DISSUE_END 1, D3DISSUE_BEGIN 2.
void Query_Issue(X86 *c) {
    ComObj *q = com_this_arg(c);
    uint32_t flags = arg(c, 1);
    if (q && q->dev_type == D3DQUERYTYPE_OCCLUSION && gpu_on()) {
        if (flags & 2) {
            host_d9_query_begin(q->id);
            query_state()[q->id] = 1;
        } else if (flags & 1) {
            host_d9_query_end(q->id);
            query_state()[q->id] = 2;
        }
    }
    com_ret(c, q ? D3D_OK9 : D3DERR_INVALIDCALL);
}
// (this, pData, dwSize, dwGetDataFlags)
void Query_GetData(X86 *c) {
    ComObj *q = com_this_arg(c);
    uint32_t data = arg(c, 1), size = arg(c, 2);
    if (!q) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    uint32_t value = 1;
    if (q->dev_type == D3DQUERYTYPE_OCCLUSION && gpu_on()) {
        auto it = query_state().find(q->id);
        if (it != query_state().end()) {
            uint32_t count = 0;
            int r = it->second == 2 ? host_d9_query_result(q->id, &count) : 0;
            if (r == 0) {
                com_ret(c, S_FALSE9); // not yet
                return;
            }
            value = r > 0 ? count : 1;
        }
    }
    if (data && size >= 4)
        wr32(data, value);
    com_ret(c, D3D_OK9);
}

#define RESOURCE_HEAD                                                                              \
    {"QueryInterface", 3, com_QueryInterface}, {"AddRef", 1, com_AddRef},                          \
        {"Release", 1, com_Release}, {"GetDevice", 2, Res_GetDevice},                              \
        {"SetPrivateData", 5, Res_SetPrivateData}, {"GetPrivateData", 4, Res_GetPrivateData},      \
        {"FreePrivateData", 2, Res_FreePrivateData}, {"SetPriority", 2, Res_SetPriority},          \
        {"GetPriority", 1, Res_GetPriority}, {"PreLoad", 1, Res_PreLoad}, {                        \
        "GetType", 1, Res_GetType                                                                  \
    }

static const ComMethod g_vb9[] = {
    RESOURCE_HEAD,
    {"Lock", 5, VB_Lock},
    {"Unlock", 1, Buf_Unlock},
    {"GetDesc", 2, Buf_GetDesc},
};

static const ComMethod g_ib9[] = {
    RESOURCE_HEAD,
    {"Lock", 5, IB_Lock},
    {"Unlock", 1, Buf_Unlock},
    {"GetDesc", 2, Buf_GetDesc},
};

static void Surf_AddRef(X86 *c) {
    ComObj *s = com_this_arg(c);
    surface_addref(s);
    com_ret(c, s ? (uint32_t)(surface_container(s) ? s->refs - 1 : s->refs) : 0);
}
static void Surf_Release(X86 *c) {
    com_ret(c, (uint32_t)surface_release(com_this_arg(c)));
}
static void Surf_QueryInterface(X86 *c) {
    ComObj *s = com_this_arg(c);
    const bool first_outside = s && surface_container(s) && s->refs == 1;
    com_QueryInterface(c);
    if (first_outside && c->r[0] == S_OK)
        com_addref(com_get(s->front_obj));
}
static const ComMethod g_surface9[] = {
    {"QueryInterface", 3, Surf_QueryInterface}, {"AddRef", 1, Surf_AddRef}, {"Release", 1, Surf_Release},
    {"GetDevice", 2, Res_GetDevice}, {"SetPrivateData", 5, Res_SetPrivateData},
    {"GetPrivateData", 4, Res_GetPrivateData}, {"FreePrivateData", 2, Res_FreePrivateData},
    {"SetPriority", 2, Res_SetPriority}, {"GetPriority", 1, Res_GetPriority}, {"PreLoad", 1, Res_PreLoad},
    {"GetType", 1, Res_GetType},
    {"GetContainer", 3, Surf_GetContainer},
    {"GetDesc", 2, Surf_GetDesc},
    {"LockRect", 4, Surf_LockRect},
    {"UnlockRect", 1, Surf_UnlockRect},
    {"GetDC", 2, Surf_GetDC},
    {"ReleaseDC", 2, Surf_ReleaseDC},
};

static const ComMethod g_texture9[] = {
    RESOURCE_HEAD,
    {"SetLOD", 2, Tex_SetLOD},
    {"GetLOD", 1, Tex_GetLOD},
    {"GetLevelCount", 1, Tex_GetLevelCount},
    {"SetAutoGenFilterType", 2, Tex_SetAutoGenFilterType},
    {"GetAutoGenFilterType", 1, Tex_GetAutoGenFilterType},
    {"GenerateMipSubLevels", 1, Tex_GenerateMipSubLevels},
    {"GetLevelDesc", 3, Tex_GetLevelDesc},
    {"GetSurfaceLevel", 3, Tex_GetSurfaceLevel},
    {"LockRect", 5, Tex_LockRect},
    {"UnlockRect", 2, Tex_UnlockRect},
    {"AddDirtyRect", 2, Tex_AddDirtyRect},
};

static const ComMethod g_volume_texture9[] = {
    {"QueryInterface", 3, VolTex_QueryInterface},
    {"AddRef", 1, com_AddRef},
    {"Release", 1, com_Release},
    {"GetDevice", 2, Vol_GetDevice},
    {"SetPrivateData", 5, Vol_Unavailable},
    {"GetPrivateData", 4, Vol_Unavailable},
    {"FreePrivateData", 2, Vol_Unavailable},
    {"SetPriority", 2, VolTex_SetPriority},
    {"GetPriority", 1, VolTex_GetPriority},
    {"PreLoad", 1, VolTex_PreLoad},
    {"GetType", 1, VolTex_GetType},
    {"SetLOD", 2, VolTex_SetLOD},
    {"GetLOD", 1, VolTex_GetLOD},
    {"GetLevelCount", 1, VolTex_GetLevelCount},
    {"SetAutoGenFilterType", 2, Vol_Unavailable},
    {"GetAutoGenFilterType", 1, Vol_NoAutoFilter},
    {"GenerateMipSubLevels", 1, Vol_NoGenerate},
    {"GetLevelDesc", 3, VolTex_GetLevelDesc},
    {"GetVolumeLevel", 3, VolTex_GetVolumeLevel},
    {"LockBox", 5, VolTex_LockBox},
    {"UnlockBox", 2, VolTex_UnlockBox},
    {"AddDirtyBox", 2, VolTex_AddDirtyBox},
};
static const ComMethod g_volume9[] = {
    {"QueryInterface", 3, Vol_QueryInterface},
    {"AddRef", 1, Vol_AddRef},
    {"Release", 1, Vol_Release},
    {"GetDevice", 2, Vol_GetDevice},
    {"SetPrivateData", 5, Vol_Unavailable},
    {"GetPrivateData", 4, Vol_Unavailable},
    {"FreePrivateData", 2, Vol_Unavailable},
    {"GetContainer", 3, Vol_GetContainer},
    {"GetDesc", 2, Vol_GetDesc},
    {"LockBox", 4, Vol_LockBox},
    {"UnlockBox", 1, Vol_UnlockBox},
};

static const ComMethod g_cubetexture9[] = {
    RESOURCE_HEAD,
    {"SetLOD", 2, Tex_SetLOD},
    {"GetLOD", 1, Tex_GetLOD},
    {"GetLevelCount", 1, Tex_GetLevelCount},
    {"SetAutoGenFilterType", 2, Tex_SetAutoGenFilterType},
    {"GetAutoGenFilterType", 1, Tex_GetAutoGenFilterType},
    {"GenerateMipSubLevels", 1, Tex_GenerateMipSubLevels},
    {"GetLevelDesc", 3, Tex_GetLevelDesc},
    {"GetCubeMapSurface", 4, Tex_GetCubeMapSurface},
    {"LockRect", 6, Tex_LockRectCube},
    {"UnlockRect", 3, Tex_UnlockRectCube},
    {"AddDirtyRect", 3, Tex_AddDirtyRectCube},
};

static const ComMethod g_decl9[] = {
    {"QueryInterface", 3, com_QueryInterface},
    {"AddRef", 1, com_AddRef},
    {"Release", 1, com_Release},
    {"GetDevice", 2, Res_GetDevice},
    {"GetDeclaration", 3, Decl_GetDeclaration},
};

static const ComMethod g_query9[] = {
    {"QueryInterface", 3, com_QueryInterface},
    {"AddRef", 1, com_AddRef},
    {"Release", 1, com_Release},
    {"GetDevice", 2, Res_GetDevice},
    {"GetType", 1, Query_GetType9},
    {"GetDataSize", 1, Query_GetDataSize},
    {"Issue", 2, Query_Issue},
    {"GetData", 4, Query_GetData},
};

// ---------------------------------------------------------------------------
// The pipeline record, and the device setters that write it
// ---------------------------------------------------------------------------
D9Pipeline &d9_pipeline(uint32_t device_id) {
    // Records are never erased and map nodes do not move, so the last one
    // asked for can be handed out again without a lookup.
    static auto *records = new std::map<uint32_t, D9Pipeline>();
    static thread_local uint32_t last_id = 0;
    static thread_local D9Pipeline *last = nullptr;
    if (last && last_id == device_id)
        return *last;
    last = &(*records)[device_id];
    last_id = device_id;
    return *last;
}

// ---- Getters for state a caller saves and puts back (Direct3D 9 semantics) ----
// (this, State, pValue)
void Dev_GetRenderState(X86 *c) {
    ComObj *dev = this_device9(c);
    uint32_t state = arg(c, 1), out = arg(c, 2);
    if (!dev || !out || state >= 256) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    if (state == HOST_D9_RS_HDR_FLOAT) {   // item 10: read-only, the renderer's float frame (not a pipeline state)
        wr32(out, __atomic_load_n(&host_d9_hdr_float, __ATOMIC_RELAXED));
        com_ret(c, D3D_OK9);
        return;
    }
    wr32(out, d9_pipeline(dev->id).rs[state]);
    com_ret(c, D3D_OK9);
}
// (this, Sampler, Type, pValue)
void Dev_GetSamplerState(X86 *c) {
    ComObj *dev = this_device9(c);
    uint32_t sampler = arg(c, 1), type = arg(c, 2), out = arg(c, 3);
    if (!dev || !out || sampler >= 16 || type >= 14) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    wr32(out, d9_pipeline(dev->id).sampler_state[sampler][type]);
    com_ret(c, D3D_OK9);
}
// (this, RenderTargetIndex, ppRenderTarget): referenced, as D3D hands it out; NOTFOUND for an
// unset target above zero.
void Dev_GetRenderTarget(X86 *c) {
    ComObj *dev = this_device9(c);
    uint32_t index = arg(c, 1), out = arg(c, 2);
    if (!dev || !out || index > 3) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    ComObj *s = com_get(index == 0 ? dev->render_target : d9_pipeline(dev->id).color_target[index]);
    if (index == 0 && !s)
        s = device_backbuffer(dev);
    if (!s) {
        wr32(out, 0);
        com_ret(c, D3DERR_NOTFOUND9);
        return;
    }
    uint32_t view_ = com_view(s, IF_D3DSURFACE9);
    if (!view_) {
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    surface_addref(s); // a level shares its texture's lifetime (Test159)
    com_out_ptr(out, view_);
    com_ret(c, D3D_OK9);
}
// (this, ppDecl): referenced; null when none is set.
void Dev_GetVertexDeclaration(X86 *c) {
    ComObj *dev = this_device9(c);
    uint32_t out = arg(c, 1);
    if (!dev || !out) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    ComObj *d = com_get(d9_pipeline(dev->id).declaration);
    uint32_t view_ = d && d->kind == K_D3D9DECL ? com_view(d, IF_D3DVERTEXDECL9) : 0;
    if (view_)
        com_addref(d);
    wr32(out, view_);
    com_ret(c, D3D_OK9);
}

// GetRenderTargetData and its LockRect, bounded: the copy is queued behind the draws that made
// the image and delivered when the GPU has run it (host_d9_texture_read_async); a
// D3DLOCK_DONOTWAIT lock before then answers D3DERR_WASSTILLDRAWING instead of waiting. A lock
// without that flag waits, through the synchronous read, as Direct3D 9 does. The destination is
// the ticket.
static std::unordered_map<uint32_t, HostD9Surface> &readbacks_pending() {
    static std::unordered_map<uint32_t, HostD9Surface> r;
    return r;
}
static void readback_forget(ComObj *o) {
    if (o && readbacks_pending().erase(o->id))
        host_d9_texture_read_forget(o->id);
}
static bool readback_settle(ComObj *s, uint32_t flags) {
    if (!s)
        return true;
    auto it = readbacks_pending().find(s->id);
    if (it == readbacks_pending().end() || s->blob.empty())
        return true;
    int r = host_d9_texture_read_poll(s->id, s->blob.data(), s->pitch, s->width, s->height);
    if (r == 0 && (flags & D3DLOCK_DONOTWAIT))
        return false;
    if (r == 0) // wait: the synchronous read of the same image
        host_d9_texture_read(it->second.id, it->second.face, it->second.level, s->blob.data(),
                             s->pitch);
    host_d9_texture_read_forget(s->id);
    readbacks_pending().erase(it);
    d9_raster_invalidate(s->id);
    return true;
}
// (this, pRenderTarget, pDestSurface)
void Dev_GetRenderTargetData(X86 *c) {
    ComObj *src = com_this(arg(c, 1)), *dst = com_this(arg(c, 2));
    if (!src || !dst || src->kind != K_D3D9SURFACE || dst->kind != K_D3D9SURFACE ||
        src->width != dst->width || src->height != dst->height || dst->bpp != 32 ||
        dst->blob.empty()) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    if (gpu_on()) {
        uint32_t slot;
        if (ComObj *owner = com_get(mirror_slot(src, &slot)))
            gpu_sync(owner);
        const HostD9Surface r = surface_ref(src);
        readbacks_pending()[dst->id] = r;
        host_d9_texture_read_async(r.id, r.face, r.level, dst->id);
        com_ret(c, D3D_OK9);
        return;
    }
    const uint8_t *sb = resource_bytes(src);
    uint8_t *db = resource_bytes(dst);
    if (sb && db && src->bpp == 32)
        for (uint32_t y = 0; y < dst->height; ++y)
            memcpy(db + (size_t)y * dst->pitch, sb + (size_t)y * src->pitch, (size_t)dst->width * 4);
    d9_raster_invalidate(dst->id);
    com_ret(c, D3D_OK9);
}

// (this, State, Value)
// (this, pViewport): D3DVIEWPORT9 is X, Y, Width, Height, MinZ, MaxZ.
void Dev_SetViewport(X86 *c) {
    ComObj *dev = this_device9(c);
    uint32_t v = arg(c, 1);
    if (dev && v) {
        D9Pipeline &p = d9_pipeline(dev->id);
        for (int i = 0; i < 4; ++i)
            p.viewport[i] = rd32(v + 4u * (uint32_t)i);
        uint32_t b0 = rd32(v + 16), b1 = rd32(v + 20);
        memcpy(&p.viewport_z[0], &b0, 4);
        memcpy(&p.viewport_z[1], &b1, 4);
        p.viewport_set = true;
    }
    com_ret(c, D3D_OK9);
}
void Dev_GetViewport(X86 *c) {
    ComObj *dev = this_device9(c);
    uint32_t v = arg(c, 1);
    if (dev && v) {
        D9Pipeline &p = d9_pipeline(dev->id);
        ComObj *rt = com_get(dev->render_target);
        uint32_t whole[4] = {0, 0, rt ? rt->width : 640, rt ? rt->height : 480};
        for (int i = 0; i < 4; ++i)
            wr32(v + 4u * (uint32_t)i, p.viewport_set ? p.viewport[i] : whole[i]);
        float z0 = p.viewport_set ? p.viewport_z[0] : 0.0f,
              z1 = p.viewport_set ? p.viewport_z[1] : 1.0f;
        uint32_t b0, b1;
        memcpy(&b0, &z0, 4);
        memcpy(&b1, &z1, 4);
        wr32(v + 16, b0);
        wr32(v + 20, b1);
    }
    com_ret(c, D3D_OK9);
}

// (this, State, pMatrix): world, view, projection and texture transforms,
// kept for the fixed-function path.
void Dev_SetTransform(X86 *c) {
    ComObj *dev = this_device9(c);
    uint32_t state = arg(c, 1), m = arg(c, 2);
    // Remaster run 14 foundation F1: private states 0x200 (the camera view) then 0x201 (its info) from the core mod,
    // one renderer event (host_d9_scene_camera), never device state. Only the mod's RECOMP_REMASTER_FOUNDATION=1
    // sends them; an info without a view this frame goes nowhere.
    if (dev && m && (state == HOST_D9_TS_SCENE_VIEW || state == HOST_D9_TS_SCENE_INFO)) {
        static float view[16];
        static bool have_view = false;
        float *dst = view, info[16];
        if (state == HOST_D9_TS_SCENE_INFO)
            dst = info;
        for (int i = 0; i < 16; ++i) {
            uint32_t b = rd32(m + 4u * (uint32_t)i);
            memcpy(&dst[i], &b, 4);
        }
        if (state == HOST_D9_TS_SCENE_VIEW)
            have_view = true;
        else if (have_view && gpu_on()) {
            have_view = false;
            host_d9_scene_camera(view, info);
        }
        com_ret(c, D3D_OK9);
        return;
    }
    // Remaster run 14 B1 height fog: private states 0x202 (packet a) then 0x203 (packet b) from the core mod, one
    // renderer event (host_d9_atmosphere), never device state. A b without an a this frame goes nowhere.
    if (dev && m && (state == HOST_D9_TS_ATMOS_A || state == HOST_D9_TS_ATMOS_B)) {
        static float pa[16];
        static bool have_a = false;
        float pb[16];
        float *dst = state == HOST_D9_TS_ATMOS_A ? pa : pb;
        for (int i = 0; i < 16; ++i) {
            uint32_t b = rd32(m + 4u * (uint32_t)i);
            memcpy(&dst[i], &b, 4);
        }
        if (state == HOST_D9_TS_ATMOS_A)
            have_a = true;
        else if (have_a && gpu_on()) {
            have_a = false;
            host_d9_atmosphere(pa, pb);
        }
        com_ret(c, D3D_OK9);
        return;
    }
    if (dev && m) {
        uint32_t slot =
            state < 256 ? state : (state >= 256 && state < 260 ? state - 256 + 24 : 0xffffffffu);
        if (slot < 32)
            for (int i = 0; i < 16; ++i) {
                uint32_t b = rd32(m + 4u * (uint32_t)i);
                memcpy(&d9_pipeline(dev->id).transform[slot][i], &b, 4);
            }
    }
    com_ret(c, D3D_OK9);
}

// (this, Stage, Type, Value)
void Dev_SetTextureStageState(X86 *c) {
    ComObj *dev = this_device9(c);
    uint32_t stage = arg(c, 1), type = arg(c, 2);
    if (dev && stage < 8 && type < 33)
        d9_pipeline(dev->id).tss[stage][type] = arg(c, 3);
    com_ret(c, D3D_OK9);
}

void Dev_SetRenderState(X86 *c) {
    ComObj *dev = this_device9(c);
    uint32_t state = arg(c, 1);
    // Remaster run 13 MetalFX upscaling: private states 252/253 are the core mod's scene markers, events for the
    // renderer and never device state (the draw tables stay as they were). A begin counts only while the back
    // buffer is render target 0; it names the depth surface bound with it.
    if (dev && (state == HOST_D9_SCENE_END || state == HOST_D9_SCENE_BEGIN)) {
        if (arg(c, 2) && gpu_on()) {
            ComObj *bb = device_backbuffer(dev);
            if (state == HOST_D9_SCENE_END)
                host_d9_scene_split(state, 0, 0);
            else if (bb) {
                const HostD9Target t = gpu_target(dev);
                const HostD9Surface b = surface_ref(bb);
                if (t.color[0].id == b.id && !t.color[0].face && !t.color[0].level)
                    host_d9_scene_split(state, b.id, t.depth.level ? 0 : t.depth.id);
            }
        }
        com_ret(c, D3D_OK9);
        return;
    }
    if (dev && state < 256) {
        D9Pipeline &p = d9_pipeline(dev->id);
        if (p.rs[state] != arg(c, 2) || !p.rs_set[state]) {
            p.rs[state] = arg(c, 2);
            p.rs_set[state] = true;
            p.rs_changed(state);
        }
    }
    com_ret(c, D3D_OK9);
}

// (this, Sampler, Type, Value)
void Dev_SetSamplerState(X86 *c) {
    ComObj *dev = this_device9(c);
    uint32_t sampler = arg(c, 1), type = arg(c, 2);
    if (dev && sampler < 16 && type < 14) {
        D9Pipeline &p = d9_pipeline(dev->id);
        if (p.sampler_state[sampler][type] != arg(c, 3)) {
            p.sampler_state[sampler][type] = arg(c, 3);
            p.ss_changed(sampler, type);
        }
    }
    com_ret(c, D3D_OK9);
}

// Copies Count four-float vectors from guest memory into a register table,
// clipped to the table.
static void write_registers(float (*table)[4], uint32_t limit, uint32_t start, uint32_t data,
                            uint32_t count) {
    for (uint32_t i = 0; i < count && start + i < limit && data; ++i)
        for (uint32_t k = 0; k < 4; ++k) {
            uint32_t bits = rd32(data + (i * 4 + k) * 4);
            memcpy(&table[start + i][k], &bits, 4);
        }
}

// (this, StartRegister, pConstantData, Vector4fCount)
void Dev_SetVertexShaderConstantF(X86 *c) {
    ComObj *dev = this_device9(c);
    if (dev) {
        D9Pipeline &pl = d9_pipeline(dev->id);
        pl.vbinder = {}; // the registers no longer hold only what an effect bound
        write_registers(pl.vconst, 256, arg(c, 1), arg(c, 2), arg(c, 3));
    }
    com_ret(c, D3D_OK9);
}
void Dev_SetPixelShaderConstantF(X86 *c) {
    ComObj *dev = this_device9(c);
    if (dev) {
        D9Pipeline &pl = d9_pipeline(dev->id);
        pl.pbinder = {};
        write_registers(pl.pconst, 224, arg(c, 1), arg(c, 2), arg(c, 3));
    }
    com_ret(c, D3D_OK9);
}

// BOOL is a 32-bit guest value, not a C++ bool. Validate the entire range
// before reading or writing anything so an invalid call cannot partly apply.
static void shader_booleans(X86 *c, bool pixel, bool get) {
    ComObj *dev = this_device9(c);
    const uint32_t start = arg(c, 1), data = arg(c, 2), count = arg(c, 3);
    if (!dev || start > 16 || count > 16 - start ||
        (count && (!data || !gm_fits(data, count * 4)))) {
        com_ret(c, D3DERR_INVALIDCALL);
        return;
    }
    D9Pipeline &pl = d9_pipeline(dev->id);
    int32_t *table = pixel ? pl.pbool : pl.vbool;
    if (!get)
        (pixel ? pl.pbinder : pl.vbinder) = {};
    for (uint32_t i = 0; i < count; ++i) {
        if (get)
            wr32(data + i * 4, (uint32_t)table[start + i]);
        else
            table[start + i] = (int32_t)rd32(data + i * 4);
    }
    com_ret(c, D3D_OK9);
}
void Dev_SetVertexShaderConstantB(X86 *c) {
    shader_booleans(c, false, false);
}
void Dev_GetVertexShaderConstantB(X86 *c) {
    shader_booleans(c, false, true);
}
void Dev_SetPixelShaderConstantB(X86 *c) {
    shader_booleans(c, true, false);
}
void Dev_GetPixelShaderConstantB(X86 *c) {
    shader_booleans(c, true, true);
}

static const ComMethod g_d3d9[] = {
    {"QueryInterface", 3, com_QueryInterface},
    {"AddRef", 1, com_AddRef},
    {"Release", 1, com_Release},
    {"RegisterSoftwareDevice", 2, D9_RegisterSoftwareDevice},
    {"GetAdapterCount", 1, D9_GetAdapterCount},
    {"GetAdapterIdentifier", 4, D9_GetAdapterIdentifier},
    {"GetAdapterModeCount", 3, D9_GetAdapterModeCount},
    {"EnumAdapterModes", 5, D9_EnumAdapterModes},
    {"GetAdapterDisplayMode", 3, D9_GetAdapterDisplayMode},
    {"CheckDeviceType", 6, D9_CheckDeviceType},
    {"CheckDeviceFormat", 7, D9_CheckDeviceFormat},
    {"CheckDeviceMultiSampleType", 7, D9_CheckDeviceMultiSampleType},
    {"CheckDepthStencilMatch", 6, D9_CheckDepthStencilMatch},
    {"CheckDeviceFormatConversion", 5, D9_CheckDeviceFormatConversion},
    {"GetDeviceCaps", 4, D9_GetDeviceCaps},
    {"GetAdapterMonitor", 2, D9_GetAdapterMonitor},
    {"CreateDevice", 7, D9_CreateDevice},
};

static const ComMethod g_device9[] = {
    {"QueryInterface", 3, com_QueryInterface},
    {"AddRef", 1, com_AddRef},
    {"Release", 1, com_Release},
    {"TestCooperativeLevel", 1, Dev_TestCooperativeLevel},
    {"GetAvailableTextureMem", 1, Dev_GetAvailableTextureMem},
    {"EvictManagedResources", 1, Dev_EvictManagedResources},
    {"GetDirect3D", 2, Dev_GetDirect3D},
    {"GetDeviceCaps", 2, Dev_GetDeviceCaps},
    {"GetDisplayMode", 3, Dev_GetDisplayMode},
    {"GetCreationParameters", 2, Dev_GetCreationParameters},
    {"SetCursorProperties", 4, Dev_SetCursorProperties},
    {"SetCursorPosition", 4, Dev_SetCursorPosition},
    {"ShowCursor", 2, Dev_ShowCursor},
    {"CreateAdditionalSwapChain", 3, Dev_CreateAdditionalSwapChain},
    {"GetSwapChain", 3, Dev_GetSwapChain},
    {"GetNumberOfSwapChains", 1, Dev_GetNumberOfSwapChains},
    {"Reset", 2, Dev_Reset},
    {"Present", 5, Dev_Present},
    {"GetBackBuffer", 5, Dev_GetBackBuffer},
    {"GetRasterStatus", 3, Dev_GetRasterStatus},
    {"SetDialogBoxMode", 2, Dev_SetDialogBoxMode},
    {"SetGammaRamp", 4, Dev_SetGammaRamp},
    {"GetGammaRamp", 3, Dev_GetGammaRamp},
    {"CreateTexture", 9, Dev_CreateTexture},
    {"CreateVolumeTexture", 10, Dev_CreateVolumeTexture},
    {"CreateCubeTexture", 8, Dev_CreateCubeTexture},
    {"CreateVertexBuffer", 7, Dev_CreateVertexBuffer},
    {"CreateIndexBuffer", 7, Dev_CreateIndexBuffer},
    {"CreateRenderTarget", 9, Dev_CreateRenderTarget},
    {"CreateDepthStencilSurface", 9, Dev_CreateDepthStencilSurface},
    {"UpdateSurface", 5, Dev_UpdateSurface},
    {"UpdateTexture", 3, Dev_UpdateTexture},
    {"GetRenderTargetData", 3, Dev_GetRenderTargetData},
    {"GetFrontBufferData", 3, Dev_GetFrontBufferData},
    {"StretchRect", 6, Dev_StretchRect},
    {"ColorFill", 4, Dev_ColorFill},
    {"CreateOffscreenPlainSurface", 7, Dev_CreateOffscreenPlainSurface},
    {"SetRenderTarget", 3, Dev_SetRenderTarget},
    {"GetRenderTarget", 3, Dev_GetRenderTarget},
    {"SetDepthStencilSurface", 2, Dev_SetDepthStencilSurface},
    {"GetDepthStencilSurface", 2, Dev_GetDepthStencilSurface},
    {"BeginScene", 1, Dev_BeginScene},
    {"EndScene", 1, Dev_EndScene},
    {"Clear", 7, Dev_Clear},
    {"SetTransform", 3, Dev_SetTransform},
    {"GetTransform", 3, Dev_GetTransform},
    {"MultiplyTransform", 3, Dev_MultiplyTransform},
    {"SetViewport", 2, Dev_SetViewport},
    {"GetViewport", 2, Dev_GetViewport},
    {"SetMaterial", 2, Dev_SetMaterial},
    {"GetMaterial", 2, Dev_GetMaterial},
    {"SetLight", 3, Dev_SetLight},
    {"GetLight", 3, Dev_GetLight},
    {"LightEnable", 3, Dev_LightEnable},
    {"GetLightEnable", 3, Dev_GetLightEnable},
    {"SetClipPlane", 3, Dev_SetClipPlane},
    {"GetClipPlane", 3, Dev_GetClipPlane},
    {"SetRenderState", 3, Dev_SetRenderState},
    {"GetRenderState", 3, Dev_GetRenderState},
    {"CreateStateBlock", 3, Dev_CreateStateBlock},
    {"BeginStateBlock", 1, Dev_BeginStateBlock},
    {"EndStateBlock", 2, Dev_EndStateBlock},
    {"SetClipStatus", 2, Dev_SetClipStatus},
    {"GetClipStatus", 2, Dev_GetClipStatus},
    {"GetTexture", 3, Dev_GetTexture},
    {"SetTexture", 3, Dev_SetTexture},
    {"GetTextureStageState", 4, Dev_GetTextureStageState},
    {"SetTextureStageState", 4, Dev_SetTextureStageState},
    {"GetSamplerState", 4, Dev_GetSamplerState},
    {"SetSamplerState", 4, Dev_SetSamplerState},
    {"ValidateDevice", 2, Dev_ValidateDevice},
    {"SetPaletteEntries", 3, Dev_SetPaletteEntries},
    {"GetPaletteEntries", 3, Dev_GetPaletteEntries},
    {"SetCurrentTexturePalette", 2, Dev_SetCurrentTexturePalette},
    {"GetCurrentTexturePalette", 2, Dev_GetCurrentTexturePalette},
    {"SetScissorRect", 2, Dev_SetScissorRect},
    {"GetScissorRect", 2, Dev_GetScissorRect},
    {"SetSoftwareVertexProcessing", 2, Dev_SetSoftwareVertexProcessing},
    {"GetSoftwareVertexProcessing", 1, Dev_GetSoftwareVertexProcessing},
    {"SetNPatchMode", 2, Dev_SetNPatchMode},
    {"GetNPatchMode", 1, Dev_GetNPatchMode},
    {"DrawPrimitive", 4, Dev_DrawPrimitive},
    {"DrawIndexedPrimitive", 7, Dev_DrawIndexedPrimitive},
    {"DrawPrimitiveUP", 5, Dev_DrawPrimitiveUP},
    {"DrawIndexedPrimitiveUP", 9, Dev_DrawIndexedPrimitiveUP},
    {"ProcessVertices", 7, Dev_ProcessVertices},
    {"CreateVertexDeclaration", 3, Dev_CreateVertexDeclaration},
    {"SetVertexDeclaration", 2, Dev_SetVertexDeclaration},
    {"GetVertexDeclaration", 2, Dev_GetVertexDeclaration},
    {"SetFVF", 2, Dev_SetFVF},
    {"GetFVF", 2, Dev_GetFVF},
    {"CreateVertexShader", 3, Dev_CreateVertexShader},
    {"SetVertexShader", 2, Dev_SetVertexShader},
    {"GetVertexShader", 2, Dev_GetVertexShader},
    {"SetVertexShaderConstantF", 4, Dev_SetVertexShaderConstantF},
    {"GetVertexShaderConstantF", 4, Dev_GetVertexShaderConstantF},
    {"SetVertexShaderConstantI", 4, Dev_SetVertexShaderConstantI},
    {"GetVertexShaderConstantI", 4, Dev_GetVertexShaderConstantI},
    {"SetVertexShaderConstantB", 4, Dev_SetVertexShaderConstantB},
    {"GetVertexShaderConstantB", 4, Dev_GetVertexShaderConstantB},
    {"SetStreamSource", 5, Dev_SetStreamSource},
    {"GetStreamSource", 5, Dev_GetStreamSource},
    {"SetStreamSourceFreq", 3, Dev_SetStreamSourceFreq},
    {"GetStreamSourceFreq", 3, Dev_GetStreamSourceFreq},
    {"SetIndices", 2, Dev_SetIndices},
    {"GetIndices", 2, Dev_GetIndices},
    {"CreatePixelShader", 3, Dev_CreatePixelShader},
    {"SetPixelShader", 2, Dev_SetPixelShader},
    {"GetPixelShader", 2, Dev_GetPixelShader},
    {"SetPixelShaderConstantF", 4, Dev_SetPixelShaderConstantF},
    {"GetPixelShaderConstantF", 4, Dev_GetPixelShaderConstantF},
    {"SetPixelShaderConstantI", 4, Dev_SetPixelShaderConstantI},
    {"GetPixelShaderConstantI", 4, Dev_GetPixelShaderConstantI},
    {"SetPixelShaderConstantB", 4, Dev_SetPixelShaderConstantB},
    {"GetPixelShaderConstantB", 4, Dev_GetPixelShaderConstantB},
    {"DrawRectPatch", 4, Dev_DrawRectPatch},
    {"DrawTriPatch", 4, Dev_DrawTriPatch},
    {"DeletePatch", 2, Dev_DeletePatch},
    {"CreateQuery", 3, Dev_CreateQuery},
};

// The DLL's one export. Not a COM method: the guest calls it directly.
void d3d9_Direct3DCreate9(X86 *c) {
    ComObj *o = com_new(K_D3D9);
    uint32_t view = o ? com_view(o, IF_D3D9) : 0;
    if (!view) {
        if (o)
            com_release(o);
        set_eax(c, 0);
        return;
    }
    LOGW("d3d9: Direct3DCreate9(SDK %u) -> %08x", arg(c, 0), view);
    set_eax(c, view);
}

static const ImportShim g_d3d9_exports[] = {
    {"d3d9.dll", "Direct3DCreate9", 1, d3d9_Direct3DCreate9},
};

void d3d9_reset() {
    g_scratch = 0;
    g_scratch_size = 0;
}

void d3d9_register() {
    static bool done = false;
    if (done)
        return;
    done = true;

    com_define(IF_D3D9, "d3d9.dll", "IDirect3D9", g_d3d9, std::size(g_d3d9));
    com_define(IF_D3DDEVICE9, "d3d9.dll", "IDirect3DDevice9", g_device9, std::size(g_device9));
    com_bind(IF_D3D9, K_D3D9);
    com_bind(IF_D3DDEVICE9, K_D3D9DEVICE);
    com_register_iid(IF_D3D9, IID_IDirect3D9_);
    com_register_iid(IF_D3DDEVICE9, IID_IDirect3DDevice9_);
    com_define(IF_D3DTEXTURE9, "d3d9.dll", "IDirect3DTexture9", g_texture9, std::size(g_texture9));
    com_define(IF_D3DCUBETEXTURE9, "d3d9.dll", "IDirect3DCubeTexture9", g_cubetexture9,
               std::size(g_cubetexture9));
    com_define(IF_D3DSURFACE9, "d3d9.dll", "IDirect3DSurface9", g_surface9, std::size(g_surface9));
    com_define(IF_D3DVERTEXBUFFER9, "d3d9.dll", "IDirect3DVertexBuffer9", g_vb9, std::size(g_vb9));
    com_define(IF_D3DINDEXBUFFER9, "d3d9.dll", "IDirect3DIndexBuffer9", g_ib9, std::size(g_ib9));
    com_define(IF_D3DVERTEXDECL9, "d3d9.dll", "IDirect3DVertexDeclaration9", g_decl9,
               std::size(g_decl9));
    com_define(IF_D3DQUERY9, "d3d9.dll", "IDirect3DQuery9", g_query9, std::size(g_query9));
    com_bind(IF_D3DTEXTURE9, K_D3D9TEXTURE);
    com_bind(IF_D3DCUBETEXTURE9, K_D3D9TEXTURE);
    com_bind(IF_D3DSURFACE9, K_D3D9SURFACE);
    com_bind(IF_D3DVERTEXBUFFER9, K_D3D9VB);
    com_bind(IF_D3DINDEXBUFFER9, K_D3D9IB);
    com_bind(IF_D3DVERTEXDECL9, K_D3D9DECL);
    com_bind(IF_D3DQUERY9, K_D3D9QUERY);
    for (ComKind k : {K_D3D9TEXTURE, K_D3D9SURFACE, K_D3D9VB, K_D3D9IB})
        com_set_destructor(k, d3d9_resource_destroy);
    com_define(IF_D3DVOLUMETEXTURE9, "d3d9.dll", "IDirect3DVolumeTexture9", g_volume_texture9,
               std::size(g_volume_texture9));
    com_define(IF_D3DVOLUME9, "d3d9.dll", "IDirect3DVolume9", g_volume9, std::size(g_volume9));
    com_bind(IF_D3DVOLUMETEXTURE9, K_D3D9VOLUMETEXTURE);
    com_bind(IF_D3DVOLUME9, K_D3D9VOLUME);
    com_register_iid(IF_D3DVOLUMETEXTURE9, IID_VolumeTexture);
    com_register_iid(IF_D3DVOLUME9, IID_Volume);
    com_set_destructor(K_D3D9TEXTURE, texture_destroy); // Test159: with its levels and faces
    com_set_destructor(K_D3D9VOLUMETEXTURE, volume_destroy);
    com_set_destructor(K_D3D9VOLUME, volume_destroy);
    com_set_destructor(K_D3D9DEVICE, device_volume_destroy);
    com_set_destructor(K_D3D9QUERY, query_destroy);
    imports_register(g_d3d9_exports, std::size(g_d3d9_exports));
}
