#include "game_config.h"
#include "../../mods/sprite_view.h"
#include "../passes.h"
// dx_tests.cpp - headless tests for the DirectX, audio and input shims.
//
// Nothing here opens a window, a device or an audio stream: the host
// callbacks are captured by strong definitions in this file, which override
// the weak no-ops in host_api.cpp.
//
// Every call goes through the real guest path: a guest stack is built, the
// arguments are pushed, and the shim is reached through
// imports_dispatch(recomp_call) exactly as recompiled code would reach it.
// That means the tests also check the stack discipline, which is where a
// wrong argc in a vtable shows up.
#include "../com.h"
#include "../dx.h"
#include "../host_api.h"
#include "../../runtime/display_seam.h"
#include "../../runtime/native_seam.h"
#include "../riff.h"
#include "../video_frame.h"
#include "../mf_media.h"
#include "../presentation_clock.h"
#include "../ddraw.h"
#include "../../runtime/memory.h"
#include "../../runtime/win32.h"
#include "../../platform/os.h"
#include "fixtures/tone_mp3.h"
#include "fixtures/quad_shaders.h"
#include "../d3d11.h"
#include "guest_abi.h"
#include <cmath>

#include <algorithm>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <array>
#include <type_traits>
#include <time.h>
#include <map>
#include <set>
#include <vector>
#include <string>
#include <thread>

// ---------------------------------------------------------------------------
// Captured host callbacks
// ---------------------------------------------------------------------------
struct Present {
    std::vector<uint8_t> pixels;
    int w = 0, h = 0, bpp = 0, pitch = 0;
    bool had_palette = false;
    uint32_t palette[256] = {0};
};
extern bool g_ddraw_untracked_stores;   // dx/ddraw.cpp (run 31, D-177)

static std::vector<Present> g_presents;
static int g_display_w = 0, g_display_h = 0, g_display_bpp = 0;

struct DrawRecord {
    uint32_t primitive_type = 0, vertex_type = 0, vertex_count = 0, index_count = 0;
    uint32_t texture_handle = 0;
    std::vector<uint8_t> vertices;
    std::vector<uint16_t> indices;
    uint32_t cull = 0;
    int32_t viewport[4] = {0, 0, 0, 0};
    bool had_projection = false;
};
static std::vector<DrawRecord> g_draws;
static int g_begin_scene = 0, g_end_scene = 0;
static std::vector<uint32_t> g_textures;
// The whole of the last texture upload, so a test can assert that the pixels
// and the palette really reached the renderer rather than only the handle.
struct TextureUpload {
    uint32_t handle = 0;
    uint32_t revision = 0;
    int32_t width = 0, height = 0, pitch = 0, bpp = 0;
    uint32_t rmask = 0, gmask = 0, bmask = 0, amask = 0;
    bool has_palette = false;
    uint32_t palette[256] = {0};
    std::vector<uint8_t> pixels;
};
static std::vector<TextureUpload> g_uploads;
static std::vector<uint32_t> g_clears;

// The mod foundation's texture seam, recorded.
//
// runtime/mods_seam.cpp defines this weakly and a strong definition
// here wins for the whole binary, so it stays inert until a test switches it
// on. What it is for: the shim hashes a texture's CONTENT at upload, because
// the DirectDraw handle is a slot number the game reuses and is not an
// identity a mod could key an override on across runs.
static bool g_tex_hook_on = false;
static std::vector<uint64_t> g_tex_hashes;
static std::vector<uint8_t> g_tex_override;
static uint64_t g_tex_override_for = 0;

extern "C" int mods_texture_override(uint64_t hash64, int32_t w, int32_t h, int32_t,
                                     uint8_t **out_rgba8, uint32_t *out_bytes) {
    if (!g_tex_hook_on)
        return 0;
    g_tex_hashes.push_back(hash64);
    if (g_tex_override.empty() || hash64 != g_tex_override_for)
        return 0;
    (void)w;
    (void)h;
    *out_rgba8 = g_tex_override.data();
    *out_bytes = (uint32_t)g_tex_override.size();
    return 1;
}

struct PlayRecord {
    int32_t channel = 0, rate = 0, channels = 0, bits = 0, loop = 0, volume = 0, pan = 0;
    uint32_t bytes = 0;
    uint32_t start_offset = 0;
    std::vector<uint8_t> pcm;
};
static std::vector<PlayRecord> g_plays;
static std::vector<int32_t> g_stops;

static HostInputState g_input;

extern "C" {

void host_present(const void *pixels, int w, int h, int bpp, const uint32_t *palette, int pitch) {
    Present p;
    p.w = w;
    p.h = h;
    p.bpp = bpp;
    p.pitch = pitch;
    p.pixels.assign((const uint8_t *)pixels, (const uint8_t *)pixels + (size_t)pitch * h);
    if (palette) {
        p.had_palette = true;
        memcpy(p.palette, palette, sizeof p.palette);
    }
    g_presents.push_back(std::move(p));
}

void host_display_present_window(const uint32_t *argb, int w, int h) {
    host_present(argb, w, h, 32, nullptr, w * 4);
}

// A host GPU for the Direct3D 11 hardware path, in software: textures and
// targets are RGBA8 arrays, a rectangle samples the nearest texel at each
// covered pixel centre and blends as gpu.h's factors say. It is what the shim
// may assume of host/gpu2d.cpp; host_tests checks the real one on Metal.
bool g_gpu2d_on = false;
uint32_t g_gpu2d_generation = 1;
struct FakeGpuSurface {
    int w = 0, h = 0;
    std::vector<uint8_t> rgba;
};
std::map<uint32_t, FakeGpuSurface> g_gpu2d;
int g_gpu2d_draws = 0, g_gpu2d_uploads = 0, g_gpu2d_readbacks = 0, g_gpu2d_presents = 0;
static FakeGpuSurface &fake_gpu_surface(uint32_t id, int w, int h) {
    FakeGpuSurface &s = g_gpu2d[id];
    if (s.w != w || s.h != h) {
        s.w = w;
        s.h = h;
        s.rgba.assign(size_t(w) * h * 4, 0);
    }
    return s;
}
int host_gpu2d_available(void) {
    return g_gpu2d_on;
}
uint32_t host_gpu2d_generation(void) {
    return g_gpu2d_generation;
}
void host_gpu2d_texture(uint32_t id, int w, int h, const uint8_t *rgba, int x, int y, int rw,
                        int rh) {
    ++g_gpu2d_uploads;
    FakeGpuSurface &s = fake_gpu_surface(id, w, h);
    for (int r = 0; r < rh; ++r)
        memcpy(s.rgba.data() + (size_t(y + r) * w + x) * 4, rgba + size_t(r) * rw * 4,
               size_t(rw) * 4);
}
void host_gpu2d_forget(uint32_t id) {
    g_gpu2d.erase(id);
}
void host_gpu2d_reset(void) {
    g_gpu2d.clear();
    ++g_gpu2d_generation;
}
void host_gpu2d_clear(uint32_t id, int w, int h, const float rgba[4]) {
    FakeGpuSurface &s = fake_gpu_surface(id, w, h);
    for (size_t i = 0; i < s.rgba.size(); ++i)
        s.rgba[i] = uint8_t(std::lround(rgba[i % 4] * 255));
}
int host_gpu2d_draw(uint32_t target, int w, int h, uint32_t texture, const HostGpu2DQuad *q) {
    auto src = g_gpu2d.find(texture);
    if (src == g_gpu2d.end())
        return 0;
    ++g_gpu2d_draws;
    const FakeGpuSurface tex = src->second;
    FakeGpuSurface &dst = fake_gpu_surface(target, w, h);
    auto factor = [](int f, int c, const float *s, const float *d) -> float {
        switch (f) {
        case 0:
            return 0;
        case 1:
            return 1;
        case 2:
            return s[3];
        case 3:
            return 1 - s[3];
        case 4:
            return d[3];
        case 5:
            return 1 - d[3];
        case 6:
            return s[c];
        case 7:
            return 1 - s[c];
        case 8:
            return d[c];
        default:
            return 1 - d[c];
        }
    };
    for (int py = int(std::ceil(q->y - 0.5)); py < int(std::ceil(q->y + q->h - 0.5)); ++py)
        for (int px = int(std::ceil(q->x - 0.5)); px < int(std::ceil(q->x + q->w - 0.5)); ++px) {
            if (px < 0 || py < 0 || px >= w || py >= h)
                continue;
            const int tx = int(std::floor((q->u + (px + 0.5 - q->x) / q->w * q->uw) * tex.w));
            const int ty = int(std::floor((q->v + (py + 0.5 - q->y) / q->h * q->uh) * tex.h));
            const uint8_t *t = tex.rgba.data() + (size_t(ty) * tex.w + tx) * 4;
            uint8_t *o = dst.rgba.data() + (size_t(py) * w + px) * 4;
            if (!q->blend) {
                memcpy(o, t, 4);
                continue;
            }
            float sc[4], dc[4], out[4];
            for (int c = 0; c < 4; ++c) {
                sc[c] = t[c] / 255.f;
                dc[c] = o[c] / 255.f;
            }
            for (int c = 0; c < 4; ++c)
                out[c] = sc[c] * factor(c == 3 ? q->src_alpha : q->src_rgb, c, sc, dc) +
                         dc[c] * factor(c == 3 ? q->dst_alpha : q->dst_rgb, c, sc, dc);
            for (int c = 0; c < 4; ++c)
                o[c] = uint8_t(std::lround(std::clamp(out[c], 0.f, 1.f) * 255));
        }
    return 1;
}
int host_gpu2d_readback(uint32_t id, int w, int h, uint8_t *rgba) {
    auto it = g_gpu2d.find(id);
    if (it == g_gpu2d.end() || it->second.w != w || it->second.h != h)
        return 0;
    ++g_gpu2d_readbacks;
    memcpy(rgba, it->second.rgba.data(), it->second.rgba.size());
    return 1;
}
void host_display_present_gpu2d(uint32_t id, int w, int h) {
    ++g_gpu2d_presents;
    auto it = g_gpu2d.find(id);
    if (it == g_gpu2d.end())
        return;
    std::vector<uint32_t> argb(size_t(w) * h);
    for (size_t i = 0; i < argb.size(); ++i) {
        const uint8_t *c = it->second.rgba.data() + i * 4;
        argb[i] = 0xff000000u | uint32_t(c[0]) << 16 | uint32_t(c[1]) << 8 | c[2];
    }
    host_present(argb.data(), w, h, 32, nullptr, w * 4);
}

void host_set_display_mode(int w, int h, int bpp) {
    g_display_w = w;
    g_display_h = h;
    g_display_bpp = bpp;
}

static std::vector<HostDirtyRect> g_t4_read_rects;
int host_d3d_readback_rects(const HostD3DSurface *, uint32_t, const HostDirtyRect *r, uint32_t n) {
    g_t4_read_rects.assign(r, r + n);
    return 1;
}
void host_d3d_begin_scene() {
    ++g_begin_scene;
}
void host_d3d_end_scene() {
    ++g_end_scene;
}

void host_d3d_draw(const HostD3DDrawSnapshot *cmd) {
    if (cmd->kind == HOST_DRAW_CLEAR) {
        g_clears.push_back(cmd->clear_flags);
        return;
    }
    DrawRecord d;
    d.primitive_type = cmd->primitive_type;
    d.vertex_type = cmd->fvf;
    d.vertex_count = cmd->vertex_count;
    d.index_count = cmd->index_count;
    d.texture_handle = cmd->texture_handle;
    size_t n = (size_t)cmd->vertex_stride * cmd->vertex_count;
    d.vertices.assign((const uint8_t *)cmd->vertices, (const uint8_t *)cmd->vertices + n);
    if (cmd->indices)
        d.indices.assign(cmd->indices, cmd->indices + cmd->index_count);
    d.cull = cmd->state.render_state[D3DRENDERSTATE_CULLMODE];
    memcpy(d.viewport, cmd->state.viewport, sizeof d.viewport);
    d.had_projection = cmd->state.transform_set[D3DTRANSFORMSTATE_PROJECTION] != 0;
    g_draws.push_back(std::move(d));
}

// The renderer's texture leases, modelled the way the real one implements
// them: a set of live (handle, revision) pairs with counts, so a test can ask
// what the frame is holding.
static std::map<uint64_t, uint32_t> g_tex_leases;
static uint64_t tex_key_for_test(uint32_t h, uint32_t r) {
    return ((uint64_t)h << 32) | r;
}
// Reads without inserting: map::operator[] would create the very entry a test
// is asking about, so "nobody holds this" would answer itself.
static uint32_t leases_for_test(uint32_t h, uint32_t r) {
    auto it = g_tex_leases.find(tex_key_for_test(h, r));
    return it == g_tex_leases.end() ? 0u : it->second;
}
// The double models the renderer: a revision it never received cannot be held,
// and the shim is told so. g_tex_uploaded is what "received" means here.
static std::set<uint64_t> g_tex_uploaded;
int host_d3d_texture_retain(uint32_t handle, uint32_t revision) {
    if (!handle)
        return 1;
    if (!g_tex_uploaded.count(tex_key_for_test(handle, revision)))
        return 0;
    ++g_tex_leases[tex_key_for_test(handle, revision)];
    return 1;
}
void host_d3d_texture_release(uint32_t handle, uint32_t revision) {
    auto it = g_tex_leases.find(tex_key_for_test(handle, revision));
    if (it == g_tex_leases.end())
        return;
    if (it->second)
        --it->second;
    if (!it->second)
        g_tex_leases.erase(it);
}

void host_d3d_clear(uint32_t flags, const int32_t *, uint32_t, uint32_t, float) {
    g_clears.push_back(flags);
}

void host_d3d_texture(const HostD3DTexture *t) {
    g_textures.push_back(t->handle);
    g_tex_uploaded.insert(((uint64_t)t->handle << 32) | t->revision);
    TextureUpload u;
    u.handle = t->handle;
    u.revision = t->revision;
    u.width = t->width;
    u.height = t->height;
    u.pitch = t->pitch;
    u.bpp = t->bpp;
    u.rmask = t->rmask;
    u.gmask = t->gmask;
    u.bmask = t->bmask;
    u.amask = t->amask;
    u.has_palette = t->palette != nullptr;
    if (t->palette)
        memcpy(u.palette, t->palette, sizeof u.palette);
    if (t->pixels && t->height > 0 && t->pitch > 0)
        u.pixels.assign((const uint8_t *)t->pixels,
                        (const uint8_t *)t->pixels + (size_t)t->pitch * (size_t)t->height);
    g_uploads.push_back(std::move(u));
}
void host_d3d_texture_destroyed(uint32_t) {}

// The play cursor a test wants the mixer to believe in, so a streaming refill
// can be driven deterministically instead of by waiting.
static uint32_t g_test_audio_pos = 0;
static bool g_test_close_requested = false;
int host_close_requested(void) {
    return g_test_close_requested;
}
// The stream contract, modelled the way the real host implements it:
// host_audio_stream converts a looping channel at its cursor and reports the
// offset it resumed from, and host_audio_played_bytes counts on from there and
// never goes backwards. Tests drive the second by hand.
static bool g_ch_streaming = false;
static uint32_t g_stream_base = 0;
static uint32_t g_stream_played = 0;
// A host that can continue a sound. Off by default, so the tests that do not
// care exercise the re-submitting path a host without it forces.
static bool g_queue_enabled = false;
static uint32_t g_queued_bytes = 0;
static std::vector<PlayRecord> g_queues;
// Whether each channel's current sound was submitted as a loop. The real host
// refuses a queue behind a looping buffer, because a buffer scheduled with the
// loop option plays for ever and nothing appended behind it is ever reached.
// A test host that accepted one would hide the ordering the shim depends on.
static std::map<int32_t, int32_t> g_ch_loop;
// Set to make a queue refuse the way the real host does once the buffer that
// began a stream has played out: its completion arrives while appended
// buffers are still scheduled, and the channel is marked as no longer
// playing. Cleared by the next play, which is what un-retires it.
static bool g_queue_retired = false;
// Everything ever accepted on any channel, so a test can model the host's own
// two counters: what has been appended and what of it has been played.
static uint64_t g_queued_accepted = 0;
// Models host_audio_voice_remaining_bytes: what the VOICE still has to play,
// the sound it is playing included. A play SETS this to that sound's length
// where it zeroes g_queued_bytes, and that difference is the whole point of
// the second query - the shim's refill gate reads this one, because QMixer
// plays a sound on a named channel and refills behind it rather than
// appending into a ring.
static uint32_t g_voice_remaining = 0;
// Existing tests assume a playing host; sample tests explicitly finish a voice.
static std::map<int32_t, bool> g_sample_playing;
static bool g_sample_tracking = false;

void host_audio_play(const HostAudioPlay *p) {
    if (g_sample_tracking)
        g_sample_playing[p->channel] = true;
    PlayRecord r;
    r.channel = p->channel;
    r.rate = p->sample_rate;
    r.channels = p->channels;
    r.bits = p->bits;
    r.loop = p->loop;
    r.volume = p->volume;
    r.pan = p->pan;
    r.bytes = p->bytes;
    r.start_offset = p->start_offset;
    r.pcm.assign((const uint8_t *)p->pcm, (const uint8_t *)p->pcm + p->bytes);
    g_plays.push_back(std::move(r));
    // A fresh play replaces the stream: it stops the node, which discards
    // everything scheduled behind it, and the queue accounting starts again.
    // The real host does exactly this and the shim's cursor arithmetic for a
    // streamed DirectSound ring depends on it.
    g_queued_bytes = 0;
    g_queues.clear();
    // Not zero: the voice is now playing this sound, and all of it is ahead.
    g_voice_remaining = p->bytes;
    g_ch_loop[p->channel] = p->loop;
    g_queued_accepted = 0;
    // A fresh play makes the channel playing again, so a voice that had been
    // retired accepts appends once more. The real host does the same.
    g_queue_retired = false;
    g_ch_streaming = false;
    g_stream_base = 0;
    g_stream_played = 0;
}
int32_t host_audio_stream(int32_t ch) {
    if (!g_queue_enabled)
        return -1; // a host without the contract
    if (!g_ch_loop.count(ch))
        return -1; // never played
    if (g_ch_streaming)
        return (int32_t)(g_stream_base + g_stream_played);
    g_ch_streaming = true;
    g_ch_loop[ch] = 0;                // no longer a loop
    g_stream_base = g_test_audio_pos; // resumed at the play cursor
    g_stream_played = 0;
    return (int32_t)g_stream_base;
}

uint32_t host_audio_played_bytes(int32_t ch) {
    (void)ch;
    return g_ch_streaming ? g_stream_base + g_stream_played : 0;
}

int32_t host_audio_queue(int32_t ch, const void *pcm, uint32_t bytes) {
    if (!g_queue_enabled || !pcm || !bytes)
        return 0;
    if (g_ch_loop.count(ch) && g_ch_loop[ch])
        return 0;
    if (g_queue_retired)
        return 0;
    PlayRecord q;
    q.channel = ch;
    q.bytes = bytes;
    q.pcm.assign((const uint8_t *)pcm, (const uint8_t *)pcm + bytes);
    g_queues.push_back(std::move(q));
    g_queued_bytes += bytes;
    g_queued_accepted += bytes;
    g_voice_remaining += bytes;
    return (int32_t)bytes;
}
uint32_t host_audio_queued_bytes(int32_t) {
    return g_queued_bytes;
}
uint32_t host_audio_voice_remaining_bytes(int32_t) {
    return g_voice_remaining;
}

void host_audio_stop(int32_t ch) {
    if (g_sample_tracking)
        g_sample_playing[ch] = false;
    g_stops.push_back(ch);
    g_queued_bytes = 0;
    g_voice_remaining = 0;
}
static std::map<int32_t, int32_t> g_audio_volumes, g_audio_pans;
static std::map<int32_t, uint32_t> g_audio_rates;
void host_audio_set_volume(int32_t ch, int32_t v) {
    g_audio_volumes[ch] = v;
}
void host_audio_set_pan(int32_t ch, int32_t v) {
    g_audio_pans[ch] = v;
}
void host_audio_set_frequency(int32_t ch, uint32_t v) {
    g_audio_rates[ch] = v;
}
static bool g_midi_available = false;
static uint32_t g_midi_closes = 0;
static std::vector<uint32_t> g_midi_messages;
int host_midi_open(const char *) {
    return g_midi_available ? 1 : 0;
}
void host_midi_short(uint32_t msg) {
    g_midi_messages.push_back(msg);
}
void host_midi_sysex(const void *, uint32_t) {}
void host_midi_reset() {}
void host_midi_close() {
    ++g_midi_closes;
}
uint32_t host_audio_position(int32_t) {
    return g_test_audio_pos;
}
int32_t host_audio_is_playing(int32_t ch) {
    if (auto it = g_sample_playing.find(ch); g_sample_tracking && it != g_sample_playing.end())
        return it->second ? 1 : 0;
    return 1;
}

// The contract in host_api.h says the deltas are consumed on read, so this
// test host clears them exactly as the real one must.
void host_input_state(HostInputState *out) {
    *out = g_input;
    g_input.mouse_dx = g_input.mouse_dy = g_input.mouse_dz = 0;
}

} // extern "C"

// ---------------------------------------------------------------------------
// Test harness
// ---------------------------------------------------------------------------

static void check(bool ok, const char *what, const char *file, int line) {
    ++g_checks;
    if (!ok) {
        ++g_failures;
        fprintf(stderr, "FAIL %s:%d: %s\n", file, line, what);
    }
}
#define CHECK(x) check((x), #x, __FILE__, __LINE__)
#define CHECK_EQ(a, b)                                                                             \
    do {                                                                                           \
        uint64_t va = (uint64_t)(a), vb = (uint64_t)(b);                                           \
        ++g_checks;                                                                                \
        if (va != vb) {                                                                            \
            ++g_failures;                                                                          \
            fprintf(stderr, "FAIL %s:%d: %s == %s (%llu vs %llu)\n", __FILE__, __LINE__, #a, #b,   \
                    (unsigned long long)va, (unsigned long long)vb);                               \
        }                                                                                          \
    } while (0)

// ---------------------------------------------------------------------------
// SDK record sizes, derived here from the published field lists rather than
// read from dxtypes.h. Sharing the implementation's constants would make an
// ABI mistake invisible: the shim and the test would simply agree on the
// wrong number. test_sdk_abi checks these against what the shims use.
// ---------------------------------------------------------------------------
enum {
    // DDSURFACEDESC: dwSize dwFlags dwHeight dwWidth lPitch dwBackBufferCount
    // dwMipMapCount dwAlphaBitDepth dwReserved lpSurface (10 dwords = 40),
    // 4 DDCOLORKEYs (32), DDPIXELFORMAT (32), DDSCAPS (4) = 108.
    SDK_DDSURFACEDESC = 108,
    // DDSURFACEDESC2 is the same but with DDSCAPS2 (16) and dwTextureStage
    // (4): 40 + 32 + 32 + 16 + 4 = 124.
    SDK_DDSURFACEDESC2 = 124,
    // DDPIXELFORMAT: dwSize dwFlags dwFourCC dwRGBBitCount and four masks.
    SDK_DDPIXELFORMAT = 32,
    // DDBLTFX: 25 dwords through dwFillColor plus two DDCOLORKEYs = 100.
    SDK_DDBLTFX = 100,
    // DDDEVICEIDENTIFIER: two 512-byte strings, LARGE_INTEGER, four dwords
    // and a GUID = 512+512+8+16+16 = 1064.
    SDK_DDDEVICEIDENTIFIER = 1064,
    // D3DPRIMCAPS: 14 dwords.
    SDK_D3DPRIMCAPS = 56,
    // D3DDEVICEDESC (DirectX 6): 252, with dpcTriCaps at 0x64.
    SDK_D3DDEVICEDESC = 252,
    SDK_D3DDD_dpcTriCaps_OFF = 0x64,
    // D3DFINDDEVICESEARCH: dwSize dwFlags bHardware dcmColorModel (16),
    // GUID (16), dwCaps (4), D3DPRIMCAPS (56) = 92.
    SDK_D3DFINDDEVICESEARCH = 92,
    // D3DFINDDEVICERESULT: dwSize (4) + GUID (16) + two D3DDEVICEDESCs.
    SDK_D3DFINDDEVICERESULT = 4 + 16 + 252 + 252,
    // D3DVIEWPORT and D3DVIEWPORT2 are both 11 dwords.
    SDK_D3DVIEWPORT = 44,
    SDK_D3DVIEWPORT2 = 44,
    // D3DCLIPSTATUS: dwFlags dwStatus and six D3DVALUEs.
    SDK_D3DCLIPSTATUS = 32,
    // DSBUFFERDESC: dwSize dwFlags dwBufferBytes dwReserved lpwfxFormat.
    SDK_DSBUFFERDESC = 20,
    // WAVEFORMATEX: 2+2+4+4+2+2+2.
    SDK_WAVEFORMATEX = 18,
    // DIDEVICEOBJECTDATA through DirectInput 7: four dwords.
    SDK_DIDEVICEOBJECTDATA = 16,
    // DIPROPDWORD: a 16-byte DIPROPHEADER plus dwData.
    SDK_DIPROPDWORD = 20,
    // DIMOUSESTATE: lX lY lZ and four buttons.
    SDK_DIMOUSESTATE = 16,
    // The D3DFINDDEVICESEARCH flag bits, from d3dcaps.h.
    SDK_D3DFDS_COLORMODEL = 0x01,
    SDK_D3DFDS_GUID = 0x02,
    SDK_D3DFDS_HARDWARE = 0x04,
};

// ---------------------------------------------------------------------------
// Interface slot numbers, spelled out so a vtable reordering fails loudly
// here rather than silently in the game.
// ---------------------------------------------------------------------------
enum {
    DD_QueryInterface = 0,
    DD_AddRef = 1,
    DD_Release = 2,
    DD_CreatePalette = 5,
    DD_CreateSurface = 6,
    DD_EnumDisplayModes = 8,
    DD_GetDisplayMode = 12,
    DD_GetFourCCCodes = 13,
    DD_RestoreDisplayMode = 19,
    DD_SetCooperativeLevel = 20,
    DD_SetDisplayMode = 21,
    DD_GetAvailableVidMem = 23,
    DD_GetDeviceIdentifier = 27,
};
enum {
    S_QueryInterface = 0,
    S_Release = 2,
    S_Blt = 5,
    S_BltFast = 7,
    S_AddAttachedSurface = 3,
    S_DeleteAttachedSurface = 8,
    S_Flip = 11,
    S_GetAttachedSurface = 12,
    S_GetPixelFormat = 21,
    S_SetColorKey = 29,
    S_GetSurfaceDesc = 22,
    S_IsLost = 24,
    S_Lock = 25,
    S_SetPalette = 31,
    S_Unlock = 32,
};
enum { P_SetEntries = 6 };
enum {
    D3D_EnumDevices = 3,
    D3D_CreateMaterial = 5,
    D3D_CreateViewport = 6,
    D3D_FindDevice = 7,
    D3D_CreateDevice = 8,
};
enum {
    DEV_GetCaps = 3,
    DEV_AddViewport = 6,
    DEV_EnumTextureFormats = 9,
    DEV_BeginScene = 10,
    DEV_EndScene = 11,
    DEV_SetCurrentViewport = 13,
    DEV_SetRenderState = 23,
    DEV_SetTransform = 26,
    DEV_DrawPrimitive = 29,
    DEV_DrawIndexedPrimitive = 30,
    DEV_SwapTextureHandles = 4,
    DEV_SetRenderTarget = 15,
    DEV_GetClipStatus = 32,
};
enum { VP_SetViewport2 = 17, VP_Clear = 12, VP_SetBackground = 8 };
enum { MAT_GetHandle = 5 };
enum { TEX_GetHandle = 3, TEX_PaletteChanged = 4, TEX_Load = 5 };
enum {
    DS_CreateSoundBuffer = 3,
    DS_SetCooperativeLevel = 6,
    B_QueryInterface = 0,
    B_GetCaps = 3,
    B_GetCurrentPosition = 4,
    B_GetFormat = 5,
    B_GetStatus = 9,
    B_Lock = 11,
    B_Play = 12,
    B_SetVolume = 15,
    B_SetFrequency = 17,
    B_Stop = 18,
    B_Unlock = 19,
    DS_DuplicateSoundBuffer = 5,
    N_SetNotificationPositions = 3,
};
enum {
    DI_CreateDevice = 3,
    DID_SetProperty = 6,
    DID_Acquire = 7,
    DID_Unacquire = 8,
    DID_GetDeviceState = 9,
    DID_GetDeviceData = 10,
    DID_SetDataFormat = 11,
    DID_SetEventNotification = 12,
};

// Builds a DirectDraw object, a Direct3D2, a 3D-capable render target and a
// device on it, returning the device interface pointer.
static uint32_t make_d3d_device() {
    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));
    if (!dd)
        return 0;
    call_method(dd, DD_SetDisplayMode, {640, 480, 16});
    uint32_t iid = sc(0x40);
    uint8_t d3d2[16] = {0xC1, 0x1E, 0xAE, 0x6A, 0x2A, 0x66, 0xD0, 0x11,
                        0x88, 0x9D, 0x00, 0xAA, 0x00, 0xBB, 0xB7, 0x6A};
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, d3d2[i]);
    call_method(dd, DD_QueryInterface, {iid, sc(0x60)});
    uint32_t d3d = rd32(sc(0x60));
    if (!d3d)
        return 0;
    uint32_t desc = sc(0x400);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_OFFSCREENPLAIN | DDSCAPS_3DDEVICE);
    wr32(desc + DDSD_OFF_dwWidth, 640);
    wr32(desc + DDSD_OFF_dwHeight, 480);
    call_method(dd, DD_CreateSurface, {desc, sc(8), 0});
    uint32_t target = rd32(sc(8));
    if (!target)
        return 0;
    const uint8_t hal[16] = {0xE0, 0x3D, 0xE6, 0x84, 0xAA, 0x46, 0xCF, 0x11,
                             0x81, 0x6F, 0x00, 0x00, 0xC0, 0x20, 0x15, 0x6E};
    uint32_t guid = sc(0x500);
    for (int i = 0; i < 16; ++i)
        wr8(guid + (uint32_t)i, hal[i]);
    call_method(d3d, D3D_CreateDevice, {guid, target, sc(12)});
    return rd32(sc(12));
}

// ---------------------------------------------------------------------------
// The tests
// ---------------------------------------------------------------------------

// Resolution changes destroy the old render target and its full-size depth
// buffer. Both explicit detachment and destruction must drop the attachment's
// reference, while preserving references still owned by the caller.
static void test_resolution_depth_lifetime() {
    cpu_reset();
    CHECK_EQ(call_shim(tramp("DDRAW.dll", "DirectDrawCreate"), {0, sc(0), 0}), DD_OK);
    const uint32_t dd = rd32(sc(0));
    const HeapStats baseline = heap_stats();
    const uint32_t live = com_live_count();
    const uint32_t sizes[][2] = {{640, 480},   {800, 600},   {1024, 768}, {1920, 1080},
                                 {2560, 1440}, {3840, 2160}, {800, 600},  {640, 480}};
    const uint32_t desc = sc(0x100), caps = sc(0x200), out = sc(0x204);
    for (int ownership = 0; ownership < 6; ++ownership) {
        for (int cycle = 0; cycle < 24; ++cycle) {
            const auto &size = sizes[cycle % 8];
            CHECK_EQ(call_method(dd, DD_SetDisplayMode, {size[0], size[1], 16}), DD_OK);
            auto surface = [&](uint32_t surface_caps) {
                gm_zero(desc, DDSD_SIZE);
                wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
                wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT);
                wr32(desc + DDSD_OFF_dwWidth, size[0]);
                wr32(desc + DDSD_OFF_dwHeight, size[1]);
                wr32(desc + DDSD_OFF_ddsCaps, surface_caps);
                CHECK_EQ(call_method(dd, DD_CreateSurface, {desc, out, 0}), DD_OK);
                return rd32(out);
            };
            uint32_t primary = 0, target = 0;
            if (ownership < 3 || ownership == 5) {
                target = surface(DDSCAPS_OFFSCREENPLAIN | DDSCAPS_3DDEVICE);
                if (ownership == 5) {
                    primary = surface(DDSCAPS_OFFSCREENPLAIN);
                    CHECK_EQ(call_method(primary, S_AddAttachedSurface, {target}), DD_OK);
                }
            } else {
                gm_zero(desc, DDSD_SIZE);
                wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
                wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_BACKBUFFERCOUNT);
                wr32(desc + DDSD_OFF_ddsCaps,
                     DDSCAPS_PRIMARYSURFACE | DDSCAPS_FLIP | DDSCAPS_COMPLEX | DDSCAPS_3DDEVICE);
                wr32(desc + DDSD_OFF_dwBackBufferCount, ownership == 3 ? 1 : 2);
                CHECK_EQ(call_method(dd, DD_CreateSurface, {desc, out, 0}), DD_OK);
                primary = rd32(out);
                wr32(caps, DDSCAPS_BACKBUFFER);
                CHECK_EQ(call_method(primary, S_GetAttachedSurface, {caps, out}), DD_OK);
                target = rd32(out);
            }
            const uint32_t depth = surface(DDSCAPS_ZBUFFER | DDSCAPS_VIDEOMEMORY);
            if (!target || !depth)
                return; // a failed allocation is already reported
            const uint32_t depth_id = com_this(depth)->id;
            const uint32_t pixels = com_this(depth)->pixels;
            CHECK_EQ(call_method(target, S_AddAttachedSurface, {depth}), DD_OK);
            wr32(caps, DDSCAPS_ZBUFFER);
            CHECK_EQ(call_method(target, S_GetAttachedSurface, {caps, out}), DD_OK);
            CHECK_EQ(rd32(out), depth);
            CHECK_EQ(call_method(rd32(out), S_Release, {}), 2u);
            if (ownership == 0) {
                CHECK_EQ(call_method(target, S_DeleteAttachedSurface, {0, depth}), DD_OK);
                CHECK_EQ(com_this(depth)->refs, 1);
                CHECK_EQ(call_method(target, S_GetAttachedSurface, {caps, out}), DDERR_NOTFOUND);
                CHECK_EQ(rd32(out), 0u);
                CHECK_EQ(call_method(depth, S_Release, {}), 0u);
                CHECK_EQ(call_method(target, S_Release, {}), 0u);
            } else if (ownership == 1) {
                CHECK_EQ(call_method(target, S_Release, {}), 0u);
                CHECK(heap_owns(pixels)); // the caller still owns its depth reference
                CHECK_EQ(call_method(depth, S_Release, {}), 0u);
            } else if (ownership == 2) {
                CHECK_EQ(call_method(depth, S_Release, {}), 1u);
                CHECK_EQ(call_method(target, S_Release, {}), 0u);
            } else if (ownership == 5) {
                // An explicit attachment is independently owned: destroying
                // its parent must leave the caller's target/depth usable.
                CHECK_EQ(call_method(primary, S_Release, {}), 0u);
                CHECK(com_this(target) != nullptr);
                CHECK_EQ(com_this(target)->refs, 1);
                CHECK_EQ(call_method(depth, S_Release, {}), 1u);
                CHECK_EQ(call_method(target, S_Release, {}), 0u);
            } else {
                // Populous keeps the GetAttachedSurface reference when it
                // destroys a flip chain. An implicit back buffer has the
                // primary's lifetime even when its interface was retained.
                const uint32_t target_id = com_this(target)->id;
                const uint32_t target_pixels = com_this(target)->pixels;
                CHECK_EQ(call_method(primary, S_Release, {}), 0u);
                CHECK(com_get(target_id) == nullptr);
                CHECK(!heap_owns(target_pixels));
                CHECK_EQ(call_method(depth, S_Release, {}), 0u);
            }
            CHECK(com_get(depth_id) == nullptr);
            CHECK(!heap_owns(pixels));
            CHECK_EQ(com_live_count(), live);
            CHECK_EQ(heap_stats().used_bytes, baseline.used_bytes);
            CHECK_EQ(heap_stats().used_blocks, baseline.used_blocks);
            CHECK(heap_check().empty());
            // Keep a broken implementation from exhausting the test process.
            if (heap_stats().used_bytes != baseline.used_bytes)
                return;
        }
    }
    CHECK_EQ(call_method(dd, DD_Release, {}), 0u);
}

// The test the brief specifies: create DirectDraw through the shim, set
// 640x480x8, create a primary with a back buffer, lock the back buffer, write
// a gradient, flip, and check host_present received those pixels and the
// palette.
static void test_gradient_flip() {
    g_presents.clear();
    cpu_reset();

    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    CHECK(create != 0);
    uint32_t hr = call_shim(create, {0, sc(0), 0});
    CHECK_EQ(hr, DD_OK);
    uint32_t dd = rd32(sc(0));
    CHECK(dd != 0);

    hr = call_method(dd, DD_SetCooperativeLevel, {0x20004, DDSCL_EXCLUSIVE | DDSCL_FULLSCREEN});
    CHECK_EQ(hr, DD_OK);

    hr = call_method(dd, DD_SetDisplayMode, {640, 480, 8});
    CHECK_EQ(hr, DD_OK);
    CHECK_EQ(g_display_w, 640);
    CHECK_EQ(g_display_h, 480);
    CHECK_EQ(g_display_bpp, 8);

    // A complex flip chain: primary plus one back buffer.
    uint32_t desc = sc(0x100);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_BACKBUFFERCOUNT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_PRIMARYSURFACE | DDSCAPS_FLIP | DDSCAPS_COMPLEX);
    wr32(desc + DDSD_OFF_dwBackBufferCount, 1);
    hr = call_method(dd, DD_CreateSurface, {desc, sc(4), 0});
    CHECK_EQ(hr, DD_OK);
    uint32_t primary = rd32(sc(4));
    CHECK(primary != 0);

    // The primary must report the display mode it was created against.
    uint32_t sd = sc(0x200);
    wr32(sd + DDSD_OFF_dwSize, DDSD_SIZE);
    hr = call_method(primary, S_GetSurfaceDesc, {sd});
    CHECK_EQ(hr, DD_OK);
    CHECK_EQ(rd32(sd + DDSD_OFF_dwWidth), 640);
    CHECK_EQ(rd32(sd + DDSD_OFF_dwHeight), 480);
    CHECK_EQ(rd32(sd + DDSD_OFF_ddpfPixelFormat + DDPF_OFF_dwRGBBitCount), 8);

    // Fetch the back buffer.
    uint32_t caps = sc(0x300);
    wr32(caps, DDSCAPS_BACKBUFFER);
    hr = call_method(primary, S_GetAttachedSurface, {caps, sc(8)});
    CHECK_EQ(hr, DD_OK);
    uint32_t back = rd32(sc(8));
    CHECK(back != 0);
    CHECK(back != primary);

    // A palette: a 256-entry ramp, attached to the primary.
    uint32_t entries = sc(0x400);
    for (uint32_t i = 0; i < 256; ++i) {
        wr8(entries + i * 4 + 0, (uint8_t)i);         // red
        wr8(entries + i * 4 + 1, (uint8_t)(255 - i)); // green
        wr8(entries + i * 4 + 2, (uint8_t)(i / 2));   // blue
        wr8(entries + i * 4 + 3, 0);
    }
    hr = call_method(dd, DD_CreatePalette, {DDPCAPS_8BIT | DDPCAPS_INITIALIZE, entries, sc(12), 0});
    CHECK_EQ(hr, DD_OK);
    uint32_t pal = rd32(sc(12));
    CHECK(pal != 0);
    hr = call_method(primary, S_SetPalette, {pal});
    CHECK_EQ(hr, DD_OK);

    // Attaching a palette to the visible surface is itself a presentation
    // change, so the count restarts here, after the setup.
    g_presents.clear();

    // Lock the back buffer and write a gradient through the pointer the shim
    // hands back, exactly as the game would.
    uint32_t lockdesc = sc(0x800);
    gm_zero(lockdesc, DDSD_SIZE);
    wr32(lockdesc + DDSD_OFF_dwSize, DDSD_SIZE);
    hr = call_method(back, S_Lock, {0, lockdesc, DDLOCK_WAIT, 0});
    CHECK_EQ(hr, DD_OK);
    uint32_t surface = rd32(lockdesc + DDSD_OFF_lpSurface);
    uint32_t pitch = rd32(lockdesc + DDSD_OFF_lPitch);
    CHECK(surface != 0);
    CHECK(pitch >= 640);
    for (uint32_t y = 0; y < 480; ++y)
        for (uint32_t x = 0; x < 640; ++x)
            wr8(surface + y * pitch + x, (uint8_t)((x + y) & 0xff));

    hr = call_method(back, S_Unlock, {0});
    CHECK_EQ(hr, DD_OK);
    // Unlocking a back buffer must not present: only the visible surface does.
    CHECK_EQ(g_presents.size(), 0);

    hr = call_method(primary, S_Flip, {0, DDFLIP_WAIT});
    CHECK_EQ(hr, DD_OK);
    CHECK_EQ(g_presents.size(), 1);

    if (!g_presents.empty()) {
        const Present &p = g_presents[0];
        CHECK_EQ(p.w, 640);
        CHECK_EQ(p.h, 480);
        CHECK_EQ(p.bpp, 8);
        CHECK(p.had_palette);
        // The gradient the test wrote is what reached the host.
        bool pixels_match = true;
        for (uint32_t y = 0; y < 480 && pixels_match; ++y)
            for (uint32_t x = 0; x < 640; ++x)
                if (p.pixels[(size_t)y * p.pitch + x] != (uint8_t)((x + y) & 0xff)) {
                    pixels_match = false;
                    break;
                }
        CHECK(pixels_match);
        // And so is the palette, in 0x00RRGGBB.
        CHECK_EQ(p.palette[0], 0x0000ff00u);
        CHECK_EQ(p.palette[255], 0x00ff007fu);
        bool palette_match = true;
        for (uint32_t i = 0; i < 256; ++i) {
            uint32_t want = (i << 16) | ((255 - i) << 8) | (i / 2);
            if (p.palette[i] != want) {
                palette_match = false;
                break;
            }
        }
        CHECK(palette_match);
    }

    // After the flip the front buffer holds what the back one did, which is
    // what the guest's next Lock of the primary must see.
    uint32_t d2 = sc(0x900);
    gm_zero(d2, DDSD_SIZE);
    wr32(d2 + DDSD_OFF_dwSize, DDSD_SIZE);
    hr = call_method(primary, S_Lock, {0, d2, DDLOCK_WAIT, 0});
    CHECK_EQ(hr, DD_OK);
    uint32_t fp = rd32(d2 + DDSD_OFF_lpSurface);
    uint32_t fpitch = rd32(d2 + DDSD_OFF_lPitch);
    CHECK_EQ(rd8(fp + 3 * fpitch + 5), (uint8_t)8);
    call_method(primary, S_Unlock, {0});

    // Releasing the primary releases the flip chain with it.
    uint32_t live_before = com_live_count();
    call_method(primary, S_Release, {});
    CHECK(com_live_count() < live_before);
}

// Blt colour fill and BltFast with a source colour key, both onto the primary,
// which must present each time.
// A surface blitted onto itself overlaps its own source, and DirectDraw copies
// as though through a temporary. A map scrolled that way - destination below
// or right of the source - came out in repeated strips when the rows were
// copied top down. Every direction, and a keyed overlap, against a reference
// computed from the pixels as they were before the call.
static void test_overlapping_self_blit() {
    cpu_reset();
    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));
    call_method(dd, DD_SetDisplayMode, {640, 480, 8});
    uint32_t desc = sc(0x100);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_OFFSCREENPLAIN | DDSCAPS_SYSTEMMEMORY);
    wr32(desc + DDSD_OFF_dwWidth, 16);
    wr32(desc + DDSD_OFF_dwHeight, 16);
    CHECK_EQ(call_method(dd, DD_CreateSurface, {desc, sc(8), 0}), DD_OK);
    uint32_t surf = rd32(sc(8));
    CHECK(surf != 0);
    if (!surf)
        return;
    uint32_t ld = sc(0x200);
    // Every pixel distinct: its own index.
    auto paint = [&]() {
        gm_zero(ld, DDSD_SIZE);
        wr32(ld + DDSD_OFF_dwSize, DDSD_SIZE);
        call_method(surf, S_Lock, {0, ld, DDLOCK_WAIT, 0});
        uint32_t p = rd32(ld + DDSD_OFF_lpSurface), pitch = rd32(ld + DDSD_OFF_lPitch);
        for (uint32_t y = 0; y < 16; ++y)
            for (uint32_t x = 0; x < 16; ++x)
                wr8(p + y * pitch + x, uint8_t(y * 16 + x));
        call_method(surf, S_Unlock, {0});
    };
    auto pixels = [&]() {
        std::vector<uint8_t> out(256);
        gm_zero(ld, DDSD_SIZE);
        wr32(ld + DDSD_OFF_dwSize, DDSD_SIZE);
        call_method(surf, S_Lock, {0, ld, DDLOCK_WAIT, 0});
        uint32_t p = rd32(ld + DDSD_OFF_lpSurface), pitch = rd32(ld + DDSD_OFF_lPitch);
        for (uint32_t y = 0; y < 16; ++y)
            for (uint32_t x = 0; x < 16; ++x)
                out[y * 16 + x] = rd8(p + y * pitch + x);
        call_method(surf, S_Unlock, {0});
        return out;
    };
    uint32_t rect = sc(0x300);
    // BltFast(x, y) from `sr`, checked against the copy DirectDraw makes.
    auto scroll = [&](int32_t x, int32_t y, int32_t l, int32_t t, int32_t r, int32_t b, bool keyed,
                      uint8_t key) {
        paint();
        const std::vector<uint8_t> before = pixels();
        std::vector<uint8_t> want = before;
        for (int32_t j = 0; j < b - t; ++j)
            for (int32_t i = 0; i < r - l; ++i) {
                const uint8_t v = before[(t + j) * 16 + (l + i)];
                if (keyed && v == key)
                    continue;
                want[(y + j) * 16 + (x + i)] = v;
            }
        wr32(rect, uint32_t(l));
        wr32(rect + 4, uint32_t(t));
        wr32(rect + 8, uint32_t(r));
        wr32(rect + 12, uint32_t(b));
        CHECK_EQ(call_method(surf, S_BltFast,
                             {uint32_t(x), uint32_t(y), surf, rect,
                              keyed ? DDBLTFAST_SRCCOLORKEY | DDBLTFAST_WAIT : DDBLTFAST_WAIT}),
                 DD_OK);
        CHECK(pixels() == want);
    };
    scroll(0, 3, 0, 0, 16, 13, false, 0); // down: the case that striped
    scroll(0, 0, 0, 3, 16, 16, false, 0); // up
    scroll(3, 0, 0, 0, 13, 16, false, 0); // right
    scroll(0, 0, 3, 0, 16, 16, false, 0); // left
    scroll(3, 3, 0, 0, 13, 13, false, 0); // down and right
    // Keyed, so pixel by pixel: the key is judged on the source as it was.
    uint32_t ck = sc(0x320);
    wr32(ck, 0x33);
    wr32(ck + 4, 0x33);
    CHECK_EQ(call_method(surf, S_SetColorKey, {DDCKEY_SRCBLT, ck}), DD_OK);
    scroll(2, 2, 0, 0, 14, 14, true, 0x33);
    call_method(surf, 2);
    call_method(dd, 2);
}

static void test_blt_and_colorkey() {
    g_presents.clear();
    cpu_reset();
    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));
    call_method(dd, DD_SetDisplayMode, {640, 480, 8});

    uint32_t desc = sc(0x100);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_PRIMARYSURFACE);
    call_method(dd, DD_CreateSurface, {desc, sc(4), 0});
    uint32_t primary = rd32(sc(4));
    CHECK(primary != 0);

    // An offscreen 16x16 source.
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_OFFSCREENPLAIN | DDSCAPS_SYSTEMMEMORY);
    wr32(desc + DDSD_OFF_dwWidth, 16);
    wr32(desc + DDSD_OFF_dwHeight, 16);
    uint32_t hr = call_method(dd, DD_CreateSurface, {desc, sc(8), 0});
    CHECK_EQ(hr, DD_OK);
    uint32_t src = rd32(sc(8));
    CHECK(src != 0);

    // Fill the source: half index 7, half index 3 (which will be keyed out).
    uint32_t ld = sc(0x200);
    gm_zero(ld, DDSD_SIZE);
    wr32(ld + DDSD_OFF_dwSize, DDSD_SIZE);
    call_method(src, S_Lock, {0, ld, DDLOCK_WAIT, 0});
    uint32_t sp = rd32(ld + DDSD_OFF_lpSurface);
    uint32_t spitch = rd32(ld + DDSD_OFF_lPitch);
    for (uint32_t y = 0; y < 16; ++y)
        for (uint32_t x = 0; x < 16; ++x)
            wr8(sp + y * spitch + x, (uint8_t)(x < 8 ? 7 : 3));
    call_method(src, S_Unlock, {0});

    // Colour-fill the primary with index 1.
    uint32_t fx = sc(0x300);
    gm_zero(fx, DDBLTFX_SIZE);
    wr32(fx + 0, DDBLTFX_SIZE);
    wr32(fx + DDBLTFX_OFF_dwFillColor, 1);
    size_t before = g_presents.size();
    hr = call_method(primary, S_Blt, {0, 0, 0, DDBLT_COLORFILL | DDBLT_WAIT, fx});
    CHECK_EQ(hr, DD_OK);
    CHECK_EQ(g_presents.size(), before + 1);
    CHECK_EQ(g_presents.back().pixels[0], 1);

    // The flag values are the contract, not just the names: SetColorKey picks
    // source or destination out of these bits, and with the wrong numbers a
    // source key is silently filed as a destination key and every keyed blit
    // copies the whole sprite.
    CHECK_EQ(DDCKEY_COLORSPACE, 0x00000001u);
    CHECK_EQ(DDCKEY_DESTBLT, 0x00000002u);
    CHECK_EQ(DDCKEY_DESTOVERLAY, 0x00000004u);
    CHECK_EQ(DDCKEY_SRCBLT, 0x00000008u);
    CHECK_EQ(DDCKEY_SRCOVERLAY, 0x00000010u);
    CHECK_EQ(DDBLT_KEYDEST, 0x00002000u);
    CHECK_EQ(DDBLT_KEYDESTOVERRIDE, 0x00004000u);
    CHECK_EQ(DDBLT_KEYSRC, 0x00008000u);
    CHECK_EQ(DDBLT_KEYSRCOVERRIDE, 0x00010000u);
    CHECK_EQ(DDSD_CKDESTBLT, 0x00004000u);
    CHECK_EQ(DDSD_CKSRCBLT, 0x00010000u);

    // Setting a source key must not be readable as a destination key.
    {
        uint32_t probe = sc(0x3a0);
        wr32(probe + DDCK_OFF_lo, 5);
        wr32(probe + DDCK_OFF_hi, 5);
        CHECK_EQ(call_method(src, 29 /* SetColorKey */, {DDCKEY_SRCBLT, probe}), DD_OK);
        uint32_t out = sc(0x3b0);
        CHECK_EQ(call_method(src, 16 /* GetColorKey */, {DDCKEY_SRCBLT, out}), DD_OK);
        CHECK_EQ(rd32(out + DDCK_OFF_lo), 5u);
        CHECK_EQ(call_method(src, 16 /* GetColorKey */, {DDCKEY_DESTBLT, out}), DDERR_NOCOLORKEY);
    }

    // A key given at CreateSurface time counts too, not only one set later.
    {
        uint32_t kdesc = sc(0x400);
        gm_zero(kdesc, DDSD_SIZE);
        wr32(kdesc + DDSD_OFF_dwSize, DDSD_SIZE);
        wr32(kdesc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT | DDSD_CKSRCBLT);
        wr32(kdesc + DDSD_OFF_ddsCaps, DDSCAPS_OFFSCREENPLAIN | DDSCAPS_SYSTEMMEMORY);
        wr32(kdesc + DDSD_OFF_dwWidth, 4);
        wr32(kdesc + DDSD_OFF_dwHeight, 4);
        wr32(kdesc + DDSD_OFF_ckSrcBlt + DDCK_OFF_lo, 6);
        wr32(kdesc + DDSD_OFF_ckSrcBlt + DDCK_OFF_hi, 6);
        CHECK_EQ(call_method(dd, DD_CreateSurface, {kdesc, sc(0x40), 0}), DD_OK);
        uint32_t keyed = rd32(sc(0x40));
        uint32_t out = sc(0x3b0);
        CHECK_EQ(call_method(keyed, 16 /* GetColorKey */, {DDCKEY_SRCBLT, out}), DD_OK);
        CHECK_EQ(rd32(out + DDCK_OFF_lo), 6u);
        call_method(keyed, S_Release, {});
    }

    // Key out index 3, then BltFast the source to 100,50.
    uint32_t ck = sc(0x380);
    wr32(ck + DDCK_OFF_lo, 3);
    wr32(ck + DDCK_OFF_hi, 3);
    hr = call_method(src, 29 /* SetColorKey */, {DDCKEY_SRCBLT, ck});
    CHECK_EQ(hr, DD_OK);

    hr = call_method(primary, S_BltFast, {100, 50, src, 0, DDBLTFAST_SRCCOLORKEY | DDBLTFAST_WAIT});
    CHECK_EQ(hr, DD_OK);
    const Present &p = g_presents.back();
    // The unkeyed left half landed; the keyed right half left the fill intact.
    CHECK_EQ(p.pixels[(size_t)50 * p.pitch + 100], 7);
    CHECK_EQ(p.pixels[(size_t)50 * p.pitch + 107], 7);
    CHECK_EQ(p.pixels[(size_t)50 * p.pitch + 108], 1);
    CHECK_EQ(p.pixels[(size_t)50 * p.pitch + 115], 1);

    // Blt honours the same key through DDBLT_KEYSRC. A cursor sprite is
    // blitted this way, and an unkeyed copy puts an opaque block around it.
    uint32_t drect = sc(0x3c0);
    wr32(drect + 0, 200);
    wr32(drect + 4, 60);
    wr32(drect + 8, 216);
    wr32(drect + 12, 76);
    hr = call_method(primary, S_Blt, {drect, src, 0, DDBLT_KEYSRC | DDBLT_WAIT, 0});
    CHECK_EQ(hr, DD_OK);
    {
        const Present &q = g_presents.back();
        CHECK_EQ(q.pixels[(size_t)60 * q.pitch + 200], 7); // copied
        CHECK_EQ(q.pixels[(size_t)60 * q.pitch + 208], 1); // keyed out, fill intact
    }

    // A destination that runs off the surface is clipped, not refused: a game
    // drawing a buffer or a tile page at an edge does it constantly, and
    // refusing loses the whole draw rather than the part that hangs over.
    {
        uint32_t off = sc(0x3d0);
        wr32(off + 0, 632); // 16 wide from x=632 on a 640-wide primary: 8 over
        wr32(off + 4, 100);
        wr32(off + 8, 648);
        wr32(off + 12, 116);
        CHECK_EQ(call_method(primary, S_Blt, {off, src, 0, DDBLT_WAIT, 0}), DD_OK);
        const Present &q = g_presents.back();
        CHECK_EQ(q.pixels[(size_t)100 * q.pitch + 632], 7); // the part on the surface
        CHECK_EQ(q.pixels[(size_t)100 * q.pitch + 639], 7); // right up to its last column
        // Entirely outside writes nothing and still succeeds.
        wr32(off + 0, 700);
        wr32(off + 8, 716);
        CHECK_EQ(call_method(primary, S_Blt, {off, src, 0, DDBLT_WAIT, 0}), DD_OK);
        // A colour fill clips the same way.
        wr32(off + 0, 600);
        wr32(off + 4, 470);
        wr32(off + 8, 700);
        wr32(off + 12, 500);
        wr32(fx + DDBLTFX_OFF_dwFillColor, 9);
        CHECK_EQ(call_method(primary, S_Blt, {off, 0, 0, DDBLT_COLORFILL | DDBLT_WAIT, fx}), DD_OK);
        const Present &f = g_presents.back();
        CHECK_EQ(f.pixels[(size_t)479 * f.pitch + 639], 9);
    }

    // DDBLT_KEYSRCOVERRIDE takes the key from the DDBLTFX instead of the
    // surface, so a caller can key one blit without touching the surface. Key
    // out 7 this time, which is the half the surface's own key keeps.
    gm_zero(fx, DDBLTFX_SIZE);
    wr32(fx + 0, DDBLTFX_SIZE);
    wr32(fx + DDBLTFX_OFF_ddckSrcColorkey + DDCK_OFF_lo, 7);
    wr32(fx + DDBLTFX_OFF_ddckSrcColorkey + DDCK_OFF_hi, 7);
    wr32(drect + 0, 300);
    wr32(drect + 4, 60);
    wr32(drect + 8, 316);
    wr32(drect + 12, 76);
    hr = call_method(primary, S_Blt, {drect, src, 0, DDBLT_KEYSRCOVERRIDE | DDBLT_WAIT, fx});
    CHECK_EQ(hr, DD_OK);
    {
        const Present &q = g_presents.back();
        CHECK_EQ(q.pixels[(size_t)60 * q.pitch + 300], 1); // 7 keyed out by the override
        CHECK_EQ(q.pixels[(size_t)60 * q.pitch + 308], 3); // 3 copied
    }

    // A colour key is a range, not one value: keying 2..4 must take index 3.
    wr32(ck + DDCK_OFF_lo, 2);
    wr32(ck + DDCK_OFF_hi, 4);
    call_method(src, 29 /* SetColorKey */, {DDCKEY_SRCBLT, ck});
    wr32(drect + 0, 400);
    wr32(drect + 4, 60);
    wr32(drect + 8, 416);
    wr32(drect + 12, 76);
    call_method(primary, S_Blt, {drect, src, 0, DDBLT_KEYSRC | DDBLT_WAIT, 0});
    {
        const Present &q = g_presents.back();
        CHECK_EQ(q.pixels[(size_t)60 * q.pitch + 400], 7);
        CHECK_EQ(q.pixels[(size_t)60 * q.pitch + 408], 1); // 3 is inside 2..4
    }

    // A destination key writes only where the destination matches. The primary
    // is filled with 1, so keying the destination on 1 lets the copy through,
    // and keying it on 9 keeps every destination pixel.
    wr32(ck + DDCK_OFF_lo, 1);
    wr32(ck + DDCK_OFF_hi, 1);
    call_method(primary, 29 /* SetColorKey */, {DDCKEY_DESTBLT, ck});
    wr32(drect + 0, 500);
    wr32(drect + 4, 60);
    wr32(drect + 8, 516);
    wr32(drect + 12, 76);
    call_method(primary, S_Blt, {drect, src, 0, DDBLT_KEYDEST | DDBLT_WAIT, 0});
    {
        const Present &q = g_presents.back();
        CHECK_EQ(q.pixels[(size_t)60 * q.pitch + 500], 7); // destination was 1: written
    }
    wr32(ck + DDCK_OFF_lo, 9);
    wr32(ck + DDCK_OFF_hi, 9);
    call_method(primary, 29 /* SetColorKey */, {DDCKEY_DESTBLT, ck});
    wr32(drect + 0, 520);
    wr32(drect + 4, 60);
    wr32(drect + 8, 536);
    wr32(drect + 12, 76);
    call_method(primary, S_Blt, {drect, src, 0, DDBLT_KEYDEST | DDBLT_WAIT, 0});
    {
        const Present &q = g_presents.back();
        CHECK_EQ(q.pixels[(size_t)60 * q.pitch + 520], 1); // destination was not 9: kept
    }
}

// A game can keep the pointer Lock handed it and draw through it between
// frames. The next blit and present must notice those writes without Unlock.
// A store in the last bytes of a row that is not a whole number of eight-byte
// words is still noticed through a retained pointer: the hash takes eight
// bytes a step and has to cover the tail as well.
// A swap chain that owns the display - fullscreen, or windowed on the
// program's top-level window - changes the mode the desktop is in, and USER32's
// metrics have to say so: a guest that lays out windows or clamps its cursor
// by the screen size would otherwise use the mode from before the switch.
static void test_fullscreen_swapchain_sets_the_desktop_mode() {
    g_presents.clear();
    cpu_reset();
    uint32_t desc = sc(0x100);
    gm_zero(desc, 0x80);
    wr32(desc + 0, 1920);  // BufferDesc.Width
    wr32(desc + 4, 1080);  // BufferDesc.Height
    wr32(desc + 16, 28);   // BufferDesc.Format: R8G8B8A8_UNORM
    wr32(desc + 28, 1);    // SampleDesc.Count
    wr32(desc + 36, 0x20); // BufferUsage: render target output
    wr32(desc + 40, 1);    // BufferCount
    wr32(desc + 48, 0);    // Windowed: FALSE
    // The output window: 640x480 at 20,30, as a program sizes one from the
    // desktop fallback before any mode exists. DefWindowProc is its procedure.
    uint32_t wc = sc(0x200), rect = sc(0x240);
    gm_zero(wc, 40);
    wr32(wc + 4, tramp("USER32.dll", "DefWindowProcA"));
    gm_put_str(sc(0x280), "SwapTarget", 32);
    wr32(wc + 36, sc(0x280));
    CHECK(call_shim(tramp("USER32.dll", "RegisterClassA"), {wc}) != 0);
    uint32_t hwnd = call_shim(tramp("USER32.dll", "CreateWindowExA"),
                              {0, sc(0x280), sc(0x280), 0x80000000u, 20, 30, 640, 480, 0, 0, 0, 0});
    CHECK(hwnd != 0);
    wr32(desc + 44, hwnd); // OutputWindow
    uint32_t device = 0, context = 0, swap = 0;
    CHECK_EQ(call_shim(tramp("d3d11.dll", "D3D11CreateDeviceAndSwapChain"),
                       {0, 1, 0, 0, 0, 0, 7, desc, sc(4), sc(8), 0, sc(12)}),
             0u);
    swap = rd32(sc(4));
    device = rd32(sc(8));
    context = rd32(sc(12));
    CHECK(swap != 0 && device != 0);
    (void)context;
    CHECK_EQ(call_shim(tramp("USER32.dll", "GetSystemMetrics"), {0}), 1920u);
    CHECK_EQ(call_shim(tramp("USER32.dll", "GetSystemMetrics"), {1}), 1080u);
    // The output window now covers the mode, so a click anywhere on it has a
    // window to reach.
    CHECK_EQ(call_shim(tramp("USER32.dll", "GetWindowRect"), {hwnd, rect}), 1u);
    CHECK_EQ(rd32(rect), 0u);
    CHECK_EQ(rd32(rect + 4), 0u);
    CHECK_EQ(rd32(rect + 8), 1920u);
    CHECK_EQ(rd32(rect + 12), 1080u);
    // Leaving fullscreen keeps it all: the program's own top-level window is
    // the one window the host shows, so a windowed chain on it owns the
    // display too, and the window stays the size of its back buffer.
    CHECK_EQ(call_method(swap, 10 /* IDXGISwapChain::SetFullscreenState */, {0, 0}), 0u);
    CHECK_EQ(call_shim(tramp("USER32.dll", "GetSystemMetrics"), {0}), 1920u);
    CHECK_EQ(call_shim(tramp("USER32.dll", "GetWindowRect"), {hwnd, rect}), 1u);
    CHECK_EQ(rd32(rect), 0u);
    CHECK_EQ(rd32(rect + 4), 0u);
    CHECK_EQ(rd32(rect + 8), 1920u);
    CHECK_EQ(rd32(rect + 12), 1080u);
    // A windowed chain on a child window is a picture inside the program's
    // window: it leaves its window alone, and entering and leaving fullscreen
    // on it puts the window back the way it was.
    uint32_t child =
        call_shim(tramp("USER32.dll", "CreateWindowExA"),
                  {0, sc(0x280), sc(0x280), 0x40000000u, 20, 30, 320, 200, hwnd, 0, 0, 0});
    CHECK(child != 0);
    wr32(desc + 44, child);
    wr32(desc + 48, 1); // Windowed
    wr32(desc + 0, 320);
    wr32(desc + 4, 200);
    CHECK_EQ(call_shim(tramp("d3d11.dll", "D3D11CreateDeviceAndSwapChain"),
                       {0, 1, 0, 0, 0, 0, 7, desc, sc(16), sc(20), 0, sc(24)}),
             0u);
    const uint32_t inner = rd32(sc(16));
    CHECK(inner != 0);
    CHECK_EQ(call_shim(tramp("USER32.dll", "GetWindowRect"), {child, rect}), 1u);
    CHECK_EQ(rd32(rect + 8) - rd32(rect), 320u);
    CHECK_EQ(call_method(inner, 10, {1, 0}), 0u);
    CHECK_EQ(call_shim(tramp("USER32.dll", "GetSystemMetrics"), {0}), 320u);
    CHECK_EQ(call_method(inner, 10, {0, 0}), 0u);
    CHECK_EQ(call_shim(tramp("USER32.dll", "GetWindowRect"), {child, rect}), 1u);
    CHECK_EQ(rd32(rect + 8) - rd32(rect), 320u);
    CHECK_EQ(rd32(rect + 12) - rd32(rect + 4), 200u);
    for (uint32_t id : {inner, rd32(sc(20)), swap, device})
        call_method(id, 2);
    call_shim(tramp("USER32.dll", "DestroyWindow"), {child});
    call_shim(tramp("USER32.dll", "DestroyWindow"), {hwnd});
}

static void test_retained_pointer_tail_bytes() {
    g_presents.clear();
    cpu_reset();
    CHECK_EQ(call_shim(tramp("DDRAW.dll", "DirectDrawCreate"), {0, sc(0), 0}), DD_OK);
    uint32_t dd = rd32(sc(0));
    CHECK_EQ(call_method(dd, DD_SetDisplayMode, {640, 480, 16}), DD_OK);
    uint32_t desc = sc(0x100);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_PRIMARYSURFACE);
    CHECK_EQ(call_method(dd, DD_CreateSurface, {desc, sc(4), 0}), DD_OK);
    uint32_t prim = rd32(sc(4));
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_OFFSCREENPLAIN | DDSCAPS_SYSTEMMEMORY);
    wr32(desc + DDSD_OFF_dwWidth, 13); // 26 bytes a row: three words and two bytes over
    wr32(desc + DDSD_OFF_dwHeight, 5);
    CHECK_EQ(call_method(dd, DD_CreateSurface, {desc, sc(8), 0}), DD_OK);
    uint32_t small = rd32(sc(8));
    desc = sc(0x200);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc, DDSD_SIZE);
    CHECK_EQ(call_method(small, S_Lock, {0, desc, DDLOCK_WAIT, 0}), DD_OK);
    uint32_t pixels = rd32(desc + DDSD_OFF_lpSurface), pitch = rd32(desc + DDSD_OFF_lPitch);
    CHECK_EQ(call_method(small, S_Unlock, {pixels}), DD_OK);
    uint32_t id = com_this(small)->id;
    uint32_t rect = sc(0x300);
    wr32(rect, 0);
    wr32(rect + 4, 0);
    wr32(rect + 8, 13);
    wr32(rect + 12, 5);
    CHECK_EQ(call_method(prim, S_BltFast, {0, 0, small, rect, 0}), DD_OK);
    uint32_t rev = ddraw_surface_revision(id);
    wr8(pixels + 4 * pitch + 25, 0x5a); // the last byte of the last row
    CHECK_EQ(call_method(prim, S_BltFast, {0, 0, small, rect, 0}), DD_OK);
    CHECK(ddraw_surface_revision(id) != rev);
}

static void test_retained_pointer_writes() {
    g_presents.clear();
    cpu_reset();
    CHECK_EQ(call_shim(tramp("DDRAW.dll", "DirectDrawCreate"), {0, sc(0), 0}), DD_OK);
    uint32_t dd = rd32(sc(0));
    CHECK_EQ(call_method(dd, DD_SetDisplayMode, {640, 480, 16}), DD_OK);
    // Fullscreen geometry follows the accepted mode, including the caption deduction.
    CHECK_EQ(call_shim(tramp("USER32.dll", "GetSystemMetrics"), {0}), 640u);
    CHECK_EQ(call_shim(tramp("USER32.dll", "GetSystemMetrics"), {1}), 480u);
    CHECK_EQ(call_shim(tramp("USER32.dll", "GetSystemMetrics"), {16}), 640u);
    CHECK_EQ(call_shim(tramp("USER32.dll", "GetSystemMetrics"), {17}), 480u - 19u);

    uint32_t desc = sc(0x100);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_PRIMARYSURFACE);
    CHECK_EQ(call_method(dd, DD_CreateSurface, {desc, sc(4), 0}), DD_OK);
    uint32_t prim = rd32(sc(4));

    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_OFFSCREENPLAIN | DDSCAPS_SYSTEMMEMORY);
    wr32(desc + DDSD_OFF_dwWidth, 640);
    wr32(desc + DDSD_OFF_dwHeight, 480);
    CHECK_EQ(call_method(dd, DD_CreateSurface, {desc, sc(8), 0}), DD_OK);
    uint32_t back = rd32(sc(8));
    CHECK_EQ(call_method(dd, DD_CreateSurface, {desc, sc(12), 0}), DD_OK);
    uint32_t untouched = rd32(sc(12));

    desc = sc(0x200);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc, DDSD_SIZE);
    CHECK_EQ(call_method(back, S_Lock, {0, desc, DDLOCK_WAIT, 0}), DD_OK);
    uint32_t pixels = rd32(desc + DDSD_OFF_lpSurface);
    CHECK(pixels != 0);
    CHECK_EQ(rd32(desc + DDSD_OFF_lPitch), 1280u);
    CHECK_EQ(call_method(back, S_Unlock, {pixels}), DD_OK);
    uint32_t back_id = com_this(back)->id;
    uint32_t rev_before = ddraw_surface_revision(back_id);
    for (uint32_t y = 0; y < 480; ++y)
        for (uint32_t x = 0; x < 640; ++x)
            wr16(pixels + y * 1280 + x * 2, 0xe482);
    uint32_t rect = sc(0x300);
    wr32(rect, 0);
    wr32(rect + 4, 0);
    wr32(rect + 8, 640);
    wr32(rect + 12, 480);
    HostFrameHandle frame = host_frame_current();
    uint32_t records_before = host_frame_record_count(frame);
    CHECK_EQ(call_method(prim, S_BltFast, {0, 0, back, rect, 0}), DD_OK);
    CHECK(ddraw_surface_revision(back_id) != rev_before);
    CHECK_EQ(host_frame_record_count(frame), records_before + 2);
    const HostBlitRecord *write = host_frame_record(frame, records_before);
    CHECK(write != nullptr && write->src.surface == HOST_SRC_CPU);
    if (write && write->src.surface == HOST_SRC_CPU) {
        CHECK_EQ(write->dst, back_id);
        CHECK_EQ(write->cpu_bpp, 16u);
        CHECK_EQ(write->cpu_pitch, 1280);
        CHECK_EQ(((const uint16_t *)write->cpu_pixels)[200 * 640 + 300], 0xe482u);
        CHECK_EQ(write->coverage[200 * 640 + 300], 1u);
    }
    uint32_t pdesc = sc(0x400);
    gm_zero(pdesc, DDSD_SIZE);
    wr32(pdesc, DDSD_SIZE);
    CHECK_EQ(call_method(prim, S_Lock, {0, pdesc, DDLOCK_READONLY | DDLOCK_WAIT, 0}), DD_OK);
    uint32_t ppix = rd32(pdesc + DDSD_OFF_lpSurface);
    CHECK_EQ(rd16(ppix + 200 * 1280 + 300 * 2), 0xe482u);
    CHECK_EQ(call_method(prim, S_Unlock, {ppix}), DD_OK);
    uint32_t rev_after = ddraw_surface_revision(back_id);
    CHECK_EQ(call_method(prim, S_BltFast, {0, 0, back, rect, 0}), DD_OK);
    CHECK_EQ(ddraw_surface_revision(back_id), rev_after);

    // Regular Blt must see even a single changed pixel at the last row's end.
    wr16(pixels + 479 * 1280 + 639 * 2, 0x07e0);
    CHECK_EQ(call_method(prim, S_Blt, {0, back, rect, DDBLT_WAIT, 0}), DD_OK);
    CHECK(ddraw_surface_revision(back_id) != rev_after);
    CHECK_EQ(rd16(ppix + 479 * 1280 + 639 * 2), 0x07e0u);

    // A surface never locked writable is untouched, even if its bytes change.
    CHECK_EQ(call_method(untouched, S_Lock, {0, desc, DDLOCK_READONLY | DDLOCK_WAIT, 0}), DD_OK);
    uint32_t upix = rd32(desc + DDSD_OFF_lpSurface);
    CHECK_EQ(call_method(untouched, S_Unlock, {upix}), DD_OK);
    uint32_t untouched_id = com_this(untouched)->id;
    uint32_t untouched_rev = ddraw_surface_revision(untouched_id);
    wr16(upix, 0x001f);
    CHECK_EQ(call_method(prim, S_BltFast, {0, 0, untouched, rect, 0}), DD_OK);
    CHECK_EQ(ddraw_surface_revision(untouched_id), untouched_rev);

    // Direct writes through the primary's retained pointer reach present too.
    CHECK_EQ(call_method(prim, S_Lock, {0, pdesc, DDLOCK_WAIT, 0}), DD_OK);
    ppix = rd32(pdesc + DDSD_OFF_lpSurface);
    CHECK_EQ(call_method(prim, S_Unlock, {ppix}), DD_OK);
    uint32_t primary_rev = ddraw_surface_revision(com_this(prim)->id);
    wr16(ppix + 200 * 1280 + 300 * 2, 0xf800);
    ddraw_present(com_this(prim));
    CHECK(ddraw_surface_revision(com_this(prim)->id) != primary_rev);
    const Present &p = g_presents.back();
    CHECK_EQ(((const uint16_t *)(p.pixels.data() + 200 * p.pitch))[300], 0xf800u);
    primary_rev = ddraw_surface_revision(com_this(prim)->id);
    ddraw_present(com_this(prim));
    CHECK_EQ(ddraw_surface_revision(com_this(prim)->id), primary_rev);
}

// The same keyed blit at 16 bpp: a key is compared against whatever the
// surface's pixels are, so the 5-6-5 path must key on the 16-bit value.
static void test_colorkey_16bpp() {
    g_presents.clear();
    cpu_reset();
    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));
    call_method(dd, DD_SetDisplayMode, {640, 480, 16});

    uint32_t desc = sc(0x100);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_PRIMARYSURFACE);
    call_method(dd, DD_CreateSurface, {desc, sc(4), 0});
    uint32_t primary = rd32(sc(4));

    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_OFFSCREENPLAIN | DDSCAPS_SYSTEMMEMORY);
    wr32(desc + DDSD_OFF_dwWidth, 8);
    wr32(desc + DDSD_OFF_dwHeight, 8);
    call_method(dd, DD_CreateSurface, {desc, sc(8), 0});
    uint32_t src = rd32(sc(8));
    CHECK(src != 0);

    const uint16_t MAGENTA = 0xf81f, GREEN = 0x07e0;
    uint32_t ld = sc(0x200);
    gm_zero(ld, DDSD_SIZE);
    wr32(ld + DDSD_OFF_dwSize, DDSD_SIZE);
    call_method(src, S_Lock, {0, ld, DDLOCK_WAIT, 0});
    uint32_t sp = rd32(ld + DDSD_OFF_lpSurface);
    uint32_t spitch = rd32(ld + DDSD_OFF_lPitch);
    for (uint32_t y = 0; y < 8; ++y)
        for (uint32_t x = 0; x < 8; ++x)
            wr16(sp + y * spitch + x * 2, x < 4 ? GREEN : MAGENTA);
    call_method(src, S_Unlock, {0});

    uint32_t fx = sc(0x300);
    gm_zero(fx, DDBLTFX_SIZE);
    wr32(fx + 0, DDBLTFX_SIZE);
    wr32(fx + DDBLTFX_OFF_dwFillColor, 0x001f); // blue
    call_method(primary, S_Blt, {0, 0, 0, DDBLT_COLORFILL | DDBLT_WAIT, fx});

    uint32_t ck = sc(0x380);
    wr32(ck + DDCK_OFF_lo, MAGENTA);
    wr32(ck + DDCK_OFF_hi, MAGENTA);
    call_method(src, 29 /* SetColorKey */, {DDCKEY_SRCBLT, ck});
    uint32_t hr =
        call_method(primary, S_BltFast, {20, 30, src, 0, DDBLTFAST_SRCCOLORKEY | DDBLTFAST_WAIT});
    CHECK_EQ(hr, DD_OK);
    const Present &p = g_presents.back();
    const uint16_t *row = (const uint16_t *)(p.pixels.data() + (size_t)30 * p.pitch);
    CHECK_EQ(row[20], GREEN); // copied
    CHECK_EQ(row[23], GREEN);
    CHECK_EQ(row[24], 0x001f); // magenta keyed out, the fill shows through
    CHECK_EQ(row[27], 0x001f);
}

// Re-attaching the palette a surface already has must not destroy it. The game
// does exactly this: its WNDPROC at 004b0870 re-attaches the primary's palette
// on every WM_ACTIVATEAPP, and by then the surface can be holding the last
// reference to it.
static void test_setpalette_self() {
    cpu_reset();
    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));
    call_method(dd, DD_SetDisplayMode, {640, 480, 8});

    uint32_t desc = sc(0x100);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_PRIMARYSURFACE);
    call_method(dd, DD_CreateSurface, {desc, sc(4), 0});
    uint32_t primary = rd32(sc(4));
    CHECK(primary != 0);

    uint32_t table = sc(0x800);
    for (uint32_t i = 0; i < 256; ++i)
        wr32(table + 4 * i, 0x00010203u * i);
    CHECK_EQ(call_method(dd, DD_CreatePalette, {0x08 /* 8 BIT */, table, sc(0x20), 0}), DD_OK);
    uint32_t pal = rd32(sc(0x20));
    CHECK(pal != 0);

    CHECK_EQ(call_method(primary, S_SetPalette, {pal}), DD_OK);
    // Two references now: the one CreatePalette handed the guest and the one
    // the surface took. Give the guest's back, so the surface holds the last
    // one - which is the case that breaks.
    CHECK_EQ(call_method(pal, 2 /* Release */, {}), 1u);
    ComObj *obj = com_this(pal);
    CHECK(obj != nullptr);
    if (!obj)
        return;
    CHECK_EQ(obj->refs, 1);

    // Attach the same palette again. It must survive, with the count unchanged:
    // releasing the outgoing one before retaining the incoming one would
    // destroy it here and everything after would work on a dead object.
    CHECK_EQ(call_method(primary, S_SetPalette, {pal}), DD_OK);
    obj = com_this(pal);
    CHECK(obj != nullptr);
    if (!obj)
        return;
    CHECK_EQ(obj->refs, 1);

    // And it is still the surface's palette, not a dangling id: a present of
    // the primary reads it, so this would fault or come back unpalettised.
    CHECK_EQ(call_method(primary, S_SetPalette, {pal}), DD_OK);
    CHECK(com_this(pal) != nullptr);

    call_method(primary, S_Release, {});
}

// SetEventNotification is what the game's two DirectInput service threads at
// 0052c880 and 0052ceda register before waiting; a shim that accepts the call
// and never signals anything leaves the mouse and keyboard dead.
// ---------------------------------------------------------------------------
// The display interface's ABI.
//
// Every later task in this plan writes against these types, and several of
// them copy records into a frame arena and read them back on another thread.
// So the layout is pinned here as a FACT, member by member, rather than left
// to whoever next edits the struct: inserting a field in the middle is a
// change every consumer has to be recompiled for, and a table that fails says
// so at once instead of at the far end of a frame.
//
// The numbers come from the compiler, not from arithmetic done by hand.
// ---------------------------------------------------------------------------
static void test_display_abi() {
    struct Member {
        const char *name;
        size_t offset;
    };
    static const Member kBlit[] = {
        {"seq", 0},
        {"dst", 4},
        {"dst_generation", 8},
        {"dst_x", 12},
        {"dst_y", 16},
        {"w", 20},
        {"h", 24},
        {"src", 28},
        {"src_x", 36},
        {"src_y", 40},
        {"fill_value", 44},
        {"src_key_lo", 48},
        {"src_key_hi", 52},
        {"dst_key_lo", 56},
        {"dst_key_hi", 60},
        {"has_srckey", 64},
        {"has_dstkey", 65},
        {"is_upload", 66},
        {"after_first_draw", 67},
        {"after_first_hud", 68},
        {"palette_version", 72},
        {"coverage", 80},
        {"cpu_pixels", 88},
        {"cpu_bpp", 96},
        {"cpu_pitch", 100},
    };
    CHECK_EQ(sizeof(HostBlitRecord), 104u);
    CHECK_EQ(alignof(HostBlitRecord), 8u);
    for (const Member &m : kBlit) {
        size_t got = 0;
        // Looked up by name so a failure names the member that moved.
        if (!strcmp(m.name, "seq"))
            got = offsetof(HostBlitRecord, seq);
        else if (!strcmp(m.name, "dst"))
            got = offsetof(HostBlitRecord, dst);
        else if (!strcmp(m.name, "dst_generation"))
            got = offsetof(HostBlitRecord, dst_generation);
        else if (!strcmp(m.name, "dst_x"))
            got = offsetof(HostBlitRecord, dst_x);
        else if (!strcmp(m.name, "dst_y"))
            got = offsetof(HostBlitRecord, dst_y);
        else if (!strcmp(m.name, "w"))
            got = offsetof(HostBlitRecord, w);
        else if (!strcmp(m.name, "h"))
            got = offsetof(HostBlitRecord, h);
        else if (!strcmp(m.name, "src"))
            got = offsetof(HostBlitRecord, src);
        else if (!strcmp(m.name, "src_x"))
            got = offsetof(HostBlitRecord, src_x);
        else if (!strcmp(m.name, "src_y"))
            got = offsetof(HostBlitRecord, src_y);
        else if (!strcmp(m.name, "fill_value"))
            got = offsetof(HostBlitRecord, fill_value);
        else if (!strcmp(m.name, "src_key_lo"))
            got = offsetof(HostBlitRecord, src_key_lo);
        else if (!strcmp(m.name, "src_key_hi"))
            got = offsetof(HostBlitRecord, src_key_hi);
        else if (!strcmp(m.name, "dst_key_lo"))
            got = offsetof(HostBlitRecord, dst_key_lo);
        else if (!strcmp(m.name, "dst_key_hi"))
            got = offsetof(HostBlitRecord, dst_key_hi);
        else if (!strcmp(m.name, "has_srckey"))
            got = offsetof(HostBlitRecord, has_srckey);
        else if (!strcmp(m.name, "has_dstkey"))
            got = offsetof(HostBlitRecord, has_dstkey);
        else if (!strcmp(m.name, "is_upload"))
            got = offsetof(HostBlitRecord, is_upload);
        else if (!strcmp(m.name, "after_first_draw"))
            got = offsetof(HostBlitRecord, after_first_draw);
        else if (!strcmp(m.name, "after_first_hud"))
            got = offsetof(HostBlitRecord, after_first_hud);
        else if (!strcmp(m.name, "palette_version"))
            got = offsetof(HostBlitRecord, palette_version);
        else if (!strcmp(m.name, "coverage"))
            got = offsetof(HostBlitRecord, coverage);
        else if (!strcmp(m.name, "cpu_pixels"))
            got = offsetof(HostBlitRecord, cpu_pixels);
        else if (!strcmp(m.name, "cpu_bpp"))
            got = offsetof(HostBlitRecord, cpu_bpp);
        else if (!strcmp(m.name, "cpu_pitch"))
            got = offsetof(HostBlitRecord, cpu_pitch);
        else {
            CHECK(!"the table names a member this test does not read");
            continue;
        }
        if (got != m.offset) {
            printf("  [FAIL] HostBlitRecord::%s moved: %zu, expected %zu\n", m.name, got, m.offset);
            ++g_failures;
        } else {
            ++g_checks;
        }
    }

    // Amendment 3's additions are present and are the types it names.
    CHECK_EQ(sizeof(((HostBlitRecord *)0)->cpu_bpp), 1u);
    CHECK_EQ(sizeof(((HostBlitRecord *)0)->cpu_pitch), 4u);

    // The state a draw carries is copied into a frame arena and read from
    // another thread, so it has to be copyable by memcpy and nothing else.
    static_assert(std::is_trivially_copyable<HostD3DRenderState>::value,
                  "HostD3DRenderState is copied by value into the frame arena");
    static_assert(std::is_trivially_copyable<HostD3DDrawSnapshot>::value,
                  "HostD3DDrawSnapshot is copied by value into the frame arena");
    static_assert(std::is_trivially_copyable<HostBlitRecord>::value,
                  "HostBlitRecord is copied by value into the frame arena");
    CHECK_EQ(sizeof(HostD3DRenderState), 1472u);
    CHECK_EQ(sizeof(HostD3DDrawSnapshot), 1576u);
    CHECK_EQ(sizeof(HostD3DLightValue), 16u);

    // The draw's own members, pinned like the record's. primitive_type is the
    // topology: without it a vertex buffer is a list of points and the
    // renderer has to guess how to join them.
    CHECK_EQ(offsetof(HostD3DDrawSnapshot, seq), 0u);
    CHECK_EQ(offsetof(HostD3DDrawSnapshot, kind), 5u);
    CHECK_EQ(offsetof(HostD3DDrawSnapshot, primitive_type), 8u);
    CHECK_EQ(offsetof(HostD3DDrawSnapshot, vertices), 16u);
    CHECK_EQ(offsetof(HostD3DDrawSnapshot, state), 64u);
    CHECK_EQ(offsetof(HostD3DDrawSnapshot, clear_flags), 1552u);
    CHECK_EQ(offsetof(HostD3DDrawSnapshot, clear_rect_count), 1556u);
    CHECK_EQ(offsetof(HostD3DDrawSnapshot, clear_rects), 1560u);
    CHECK_EQ(offsetof(HostD3DDrawSnapshot, clear_color), 1568u);
    CHECK_EQ(offsetof(HostD3DDrawSnapshot, clear_z), 1572u);

    // Material and lights travel as arena-owned VALUES, not as handles that
    // could be read at composite time, and the light array has no cap: the
    // guest may attach as many as it likes and a frame that dropped the
    // seventeenth would light differently from the one it asked for.
    static_assert(std::is_trivially_copyable<HostD3DLightValue>::value,
                  "HostD3DLightValue is copied into the frame arena");
    static_assert(std::is_pointer<decltype(HostD3DRenderState().lights)>::value,
                  "lights is an arena-owned array, not a fixed one");
    static_assert(std::is_pointer<decltype(HostD3DRenderState().material)>::value,
                  "the material is copied, not referred to by handle");
    static_assert(std::is_pointer<decltype(HostD3DDrawSnapshot().clear_rects)>::value,
                  "clear_rects is arena-owned: the shim accepts up to 4096");

    // The state block must be able to hold everything the device keeps, or a
    // draw would be replayed against a truncated copy of its own state.
    CHECK_EQ(HOST_D3D_RENDERSTATE_MAX, D3D_RENDERSTATE_MAX);
    CHECK_EQ(HOST_D3D_LIGHTSTATE_MAX, D3D_LIGHTSTATE_MAX);
    CHECK_EQ(HOST_D3D_TRANSFORM_MAX, D3DTRANSFORMSTATE_MAX);

    // The identities, whose sizes cross the arena too.
    CHECK_EQ(sizeof(HostSurfaceKey), 8u);
    CHECK_EQ(sizeof(HostPixels), 24u);
    CHECK_EQ(sizeof(HostFrameHandle), 8u);
    CHECK_EQ(HOST_SURFACE_NONE, 0u);
    CHECK_EQ(HOST_SRC_CPU, 0xffffffffu);
    CHECK_EQ(HOST_DRAW_PRIMITIVE, 0u);
    CHECK_EQ(HOST_DRAW_CLEAR, 1u);
    CHECK_EQ((uint32_t)HOST_SCREEN_MENU, 0u);
    CHECK_EQ((uint32_t)HOST_SCREEN_FMV, 1u);
    CHECK_EQ((uint32_t)HOST_SCREEN_GAMEPLAY, 2u);

    // A fresh frame answers consistently for every accessor: no records, no
    // draws, no HUD, nothing drawn. Before DISP-T2 these were stub answers;
    // now they are a real empty frame, which is the same contract from the
    // caller's side and the one that has to keep holding.
    reset_ddraw_for_test();
    HostFrameHandle f = host_frame_current();
    CHECK_EQ(host_frame_record_count(f), 0u);
    CHECK(host_frame_record(f, 0) == nullptr);
    CHECK_EQ(host_frame_draw_count(f), 0u);
    CHECK(host_frame_draw(f, 0) == nullptr);
    CHECK_EQ(host_frame_first_hud_seq(f), 0xffffffffu);
    CHECK_EQ(host_frame_had_draws(f), 0);
    CHECK_EQ(host_frame_render_surface(f), HOST_SURFACE_NONE);
    CHECK_EQ(host_cursor_surface(), HOST_SURFACE_NONE);
    // A key nobody leased cannot be leased, and the failure clears the out.
    HostSurfaceKey key = {1u, 1u};
    HostPixels px;
    memset(&px, 0xcd, sizeof px);
    CHECK(host_revision_lease(key, &px) != 0);
    CHECK(px.data == nullptr);

    // The counters are process-wide and cumulative, so this asserts what it
    // can about the ACCESSOR rather than about the numbers: it fills every
    // field, and a reset leaves them all zero. What each reader increments is
    // the "access counts" suite's business.
    HostAccessCounts counts;
    memset(&counts, 0xcd, sizeof counts);
    ddraw_reset_access_counts();
    host_access_counts(&counts);
    CHECK_EQ(counts.lock_read, 0u);
    CHECK_EQ(counts.lock_write, 0u);
    CHECK_EQ(counts.getdc, 0u);
    CHECK_EQ(counts.blt_source, 0u);
    CHECK_EQ(counts.dstkey_read, 0u);
    CHECK_EQ(counts.duplicate, 0u);
    CHECK_EQ(counts.texture_load, 0u);
    CHECK_EQ(counts.flip, 0u);
    CHECK_EQ(counts.clean_reads, 0u);
}

// This file overrides most host_api.h callbacks with its own strong
// definitions above, but deliberately leaves host_pad_* untouched: that's
// the only way to reach host_api.cpp's weak no-op defaults, which is what
// this test checks. host/controls/vpad_host_api.cpp's strong definitions
// (a later task) are app-only and never link into dx_tests.
static void test_host_pad_defaults() {
    // Pinned like every other struct that crosses the host/guest ABI (see
    // HostBlitRecord above): a size, alignment or offset drift here is a
    // silent ABI break for dx/dinput_joystick.cpp and dx/xinput.cpp.
    CHECK_EQ(sizeof(HostPadState), 14u);
    CHECK_EQ(alignof(HostPadState), 2u);
    CHECK_EQ(offsetof(HostPadState, l2), 12u);
    CHECK_EQ(sizeof(HostPadEvent), 12u);
    CHECK_EQ(alignof(HostPadEvent), 4u);
    CHECK_EQ(offsetof(HostPadEvent, value), 8u);

    CHECK_EQ(host_pad_mode(), 0);
    CHECK_EQ(host_pad_native_apis(), 0);

    HostPadState pad;
    memset(&pad, 0xcd, sizeof pad);
    CHECK_EQ(host_pad_state(&pad), 0u);
    CHECK_EQ(pad.buttons, 0u);
    CHECK_EQ(pad.hat, 0u);
    CHECK_EQ(pad.reserved, 0u);
    CHECK_EQ((uint16_t)pad.lx, 0u);
    CHECK_EQ((uint16_t)pad.ly, 0u);
    CHECK_EQ((uint16_t)pad.rx, 0u);
    CHECK_EQ((uint16_t)pad.ry, 0u);
    CHECK_EQ(pad.l2, 0u);
    CHECK_EQ(pad.r2, 0u);

    HostPadEvent event;
    memset(&event, 0xcd, sizeof event);
    CHECK_EQ(host_pad_next_event(0u, &event), 0);

    host_pad_rumble(1000, 2000); // no-op default: just must not crash

    CHECK(!strcmp(host_pad_native_axes(), "x,y,z,rz,rx,ry"));
    CHECK(!strcmp(host_pad_native_buttons(),
                  "square,cross,circle,triangle,l1,r1,l2,r2,select,start,l3,r3,ps"));
}

// ===========================================================================
// The frame recorder (DISP-T2).
//
// The helpers below drive the shim through its real vtables - the same path
// the guest takes - because the recorder hangs off those entry points and a
// test that called the recorder directly would not be testing the wiring.
// ===========================================================================
static uint32_t g_rec_dd = 0;

// cpu_reset() throws every COM object away, so a cached interface pointer from
// a previous test is a dangling guest address. Reset it there, not here.
static void rec_reset() {
    cpu_reset();
    g_rec_dd = 0;
    reset_ddraw_for_test();
}

static uint32_t rec_dd() {
    if (g_rec_dd)
        return g_rec_dd;
    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0x800), 0});
    g_rec_dd = rd32(sc(0x800));
    // A mode first, as every guest does before it asks for a surface: without
    // one the shim has no pixel format to give an offscreen surface.
    if (g_rec_dd)
        call_method(g_rec_dd, DD_SetDisplayMode, {640, 480, 8});
    return g_rec_dd;
}

// A surface through DD_CreateSurface, with the caps the caller asks for.
static uint32_t rec_make_surface(uint32_t w, uint32_t h, uint32_t bpp, uint32_t caps) {
    uint32_t dd = rec_dd();
    if (!dd)
        return 0;
    uint32_t desc = sc(0x900);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT);
    wr32(desc + DDSD_OFF_ddsCaps, caps);
    wr32(desc + DDSD_OFF_dwWidth, w);
    wr32(desc + DDSD_OFF_dwHeight, h);
    (void)bpp;
    // The out pointer is kept well clear of the descriptor. It was inside it
    // once - sc(desc + 8) - and CreateSurface's own com_out_ptr(out, 0) then
    // zeroed dwHeight before reading it, so every create failed with
    // DDERR_INVALIDPARAMS and the descriptor looked perfect afterwards.
    call_method(dd, DD_CreateSurface, {desc, sc(0xa00), 0});
    return rd32(sc(0xa00));
}
static uint32_t make_render_target_for_test(uint32_t w, uint32_t h, uint32_t bpp) {
    return rec_make_surface(w, h, bpp, DDSCAPS_OFFSCREENPLAIN | DDSCAPS_3DDEVICE);
}
static uint32_t make_offscreen_for_test(uint32_t w, uint32_t h, uint32_t bpp) {
    return rec_make_surface(w, h, bpp, DDSCAPS_OFFSCREENPLAIN);
}

// A primary with a back buffer, which is what makes it flippable: Flip is one
// of the two events that seal a frame, and a chain with no back buffer answers
// DDERR_NOTFLIPPABLE and seals nothing.
static uint32_t make_primary_chain_for_test() {
    uint32_t dd = rec_dd();
    if (!dd)
        return 0;
    uint32_t desc = sc(0x900);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_BACKBUFFERCOUNT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_PRIMARYSURFACE | DDSCAPS_FLIP | DDSCAPS_COMPLEX);
    wr32(desc + DDSD_OFF_dwBackBufferCount, 1);
    call_method(dd, DD_CreateSurface, {desc, sc(0xa00), 0});
    return rd32(sc(0xa00));
}

static ComObj *rec_obj(uint32_t iface) {
    return iface ? com_this(iface, IF_DDSURFACE) : nullptr;
}

// Writes through the shim's own Lock/Unlock, so the recorder sees a CPU write
// exactly as it would from the guest.
static void fill_for_test(uint32_t surface, uint32_t value) {
    ComObj *o = rec_obj(surface);
    if (!o)
        return;
    uint32_t desc = sc(0xa80);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    call_method(surface, S_Lock, {0, desc, DDLOCK_WAIT, 0});
    for (uint32_t y = 0; y < o->height; ++y)
        for (uint32_t x = 0; x < o->width; ++x)
            wr8(o->pixels + y * o->pitch + x, (uint8_t)value);
    call_method(surface, S_Unlock, {0});
}

// Half `a`, half `b`, in vertical stripes: 128 of 256 pixels on a 16x16.
static void checker_fill_for_test(uint32_t surface, uint32_t a, uint32_t b) {
    ComObj *o = rec_obj(surface);
    if (!o)
        return;
    uint32_t desc = sc(0xa80);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    call_method(surface, S_Lock, {0, desc, DDLOCK_WAIT, 0});
    for (uint32_t y = 0; y < o->height; ++y)
        for (uint32_t x = 0; x < o->width; ++x)
            wr8(o->pixels + y * o->pitch + x, (uint8_t)((x & 1) ? b : a));
    call_method(surface, S_Unlock, {0});
}

static void lock_write_poke_for_test(uint32_t surface, uint32_t x, uint32_t y, uint32_t v) {
    ComObj *o = rec_obj(surface);
    if (!o)
        return;
    uint32_t desc = sc(0xa80);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    call_method(surface, S_Lock, {0, desc, DDLOCK_WAIT, 0});
    wr8(o->pixels + y * o->pitch + x, (uint8_t)v);
    call_method(surface, S_Unlock, {0});
}

static void set_srckey_for_test(uint32_t surface, uint32_t lo, uint32_t hi) {
    uint32_t key = sc(0xb80);
    wr32(key + 0, lo);
    wr32(key + 4, hi);
    call_method(surface, S_SetColorKey, {DDCKEY_SRCBLT, key});
}

static uint32_t blt_for_test(uint32_t dst, uint32_t src, int32_t dx, int32_t dy, int32_t w,
                             int32_t h, uint32_t flags) {
    uint32_t dr = sc(0xc00), sr = sc(0xc40);
    wr32(dr + 0, (uint32_t)dx);
    wr32(dr + 4, (uint32_t)dy);
    wr32(dr + 8, (uint32_t)(dx + w));
    wr32(dr + 12, (uint32_t)(dy + h));
    wr32(sr + 0, 0);
    wr32(sr + 4, 0);
    wr32(sr + 8, (uint32_t)w);
    wr32(sr + 12, (uint32_t)h);
    return call_method(dst, S_Blt, {dr, src, src ? sr : 0u, flags, 0});
}

static uint32_t fill_blt_for_test(uint32_t dst, int32_t dx, int32_t dy, int32_t w, int32_t h,
                                  uint32_t value) {
    uint32_t dr = sc(0xc00), fx = sc(0xc80);
    wr32(dr + 0, (uint32_t)dx);
    wr32(dr + 4, (uint32_t)dy);
    wr32(dr + 8, (uint32_t)(dx + w));
    wr32(dr + 12, (uint32_t)(dy + h));
    gm_zero(fx, DDBLTFX_SIZE);
    wr32(fx + DDBLTFX_OFF_dwFillColor, value);
    return call_method(dst, S_Blt, {dr, 0, 0, DDBLT_COLORFILL, fx});
}

static int coverage_sum(const HostBlitRecord *r) {
    int n = 0;
    for (int32_t i = 0; i < r->w * r->h; ++i)
        n += r->coverage[i];
    return n;
}

static void test_record_basic_and_coverage() {
    rec_reset();
    uint32_t rt = make_render_target_for_test(640, 480, 8);
    uint32_t sp = make_offscreen_for_test(16, 16, 8);
    CHECK(rt != 0 && sp != 0);
    fill_for_test(sp, 5);
    uint32_t rev = host_surface_revision_for_test(rec_obj(sp)->id);
    reset_ddraw_for_test();
    // Re-read the revision after the reset, so the record and the expectation
    // are taken against the same state.
    rev = host_surface_revision_for_test(rec_obj(sp)->id);
    blt_for_test(rt, sp, 100, 200, 16, 16, 0);
    HostFrameHandle f = host_frame_current();
    CHECK_EQ(host_frame_record_count(f), 1u);
    const HostBlitRecord *r = host_frame_record(f, 0);
    CHECK(r != nullptr);
    if (!r)
        return;
    CHECK_EQ(r->dst_x, 100);
    CHECK_EQ(r->dst_y, 200);
    CHECK_EQ(r->w, 16);
    CHECK_EQ(r->h, 16);
    CHECK_EQ(r->src.surface, rec_obj(sp)->id);
    CHECK_EQ(r->src.revision, rev);
    CHECK_EQ(coverage_sum(r), 256);
}

static void test_keyed_blit_coverage_and_key_values() {
    rec_reset();
    uint32_t rt = make_render_target_for_test(640, 480, 8);
    uint32_t sp = make_offscreen_for_test(16, 16, 8);
    checker_fill_for_test(sp, 0, 7);
    set_srckey_for_test(sp, 0, 0);
    reset_ddraw_for_test();
    blt_for_test(rt, sp, 0, 0, 16, 16, DDBLT_KEYSRC);
    const HostBlitRecord *r = host_frame_record(host_frame_current(), 0);
    CHECK(r != nullptr);
    if (!r)
        return;
    CHECK_EQ(r->has_srckey, 1);
    CHECK_EQ(r->src_key_lo, 0u);
    CHECK_EQ(r->src_key_hi, 0u);
    // Half the source is the key colour, so half the destination is spared.
    CHECK_EQ(coverage_sum(r), 128);
}

static void test_fill_upload_and_flags() {
    rec_reset();
    uint32_t rt = make_render_target_for_test(640, 480, 8);
    uint32_t sp = make_offscreen_for_test(8, 8, 8);
    fill_for_test(sp, 3);
    reset_ddraw_for_test();

    // A fill has no source and carries the value it filled with.
    fill_blt_for_test(rt, 0, 0, 8, 8, 9);
    const HostBlitRecord *r0 = host_frame_record(host_frame_current(), 0);
    CHECK(r0 != nullptr);
    if (!r0)
        return;
    CHECK_EQ(r0->src.surface, HOST_SURFACE_NONE);
    CHECK_EQ(r0->fill_value, 9u);
    CHECK_EQ(r0->after_first_draw, 0);
    CHECK_EQ(r0->after_first_hud, 0);

    // A draw, then a blit: the blit is after the first draw.
    ddraw_note_draw();
    blt_for_test(rt, sp, 0, 0, 8, 8, 0);
    const HostBlitRecord *r1 = host_frame_record(host_frame_current(), 1);
    CHECK(r1 != nullptr);
    if (!r1)
        return;
    CHECK_EQ(r1->after_first_draw, 1);
    CHECK_EQ(r1->after_first_hud, 0);
    CHECK_EQ(host_frame_had_draws(host_frame_current()), 1);

    // A HUD blit, then a blit: the later one is after the first HUD, and the
    // frame remembers where the HUD began.
    ddraw_note_hud();
    blt_for_test(rt, sp, 8, 8, 8, 8, 0);
    const HostBlitRecord *r2 = host_frame_record(host_frame_current(), 2);
    CHECK(r2 != nullptr);
    if (!r2)
        return;
    CHECK_EQ(r2->after_first_hud, 1);
    CHECK(host_frame_first_hud_seq(host_frame_current()) != 0xffffffffu);

    // A blit into a surface the device has a texture handle for is an upload.
    ComObj *spo = rec_obj(sp);
    uint32_t saved = spo->texture_handle;
    spo->texture_handle = 0x1234u;
    blt_for_test(sp, rt, 0, 0, 8, 8, 0);
    const HostBlitRecord *r3 = host_frame_record(host_frame_current(), 3);
    CHECK(r3 != nullptr);
    if (r3)
        CHECK_EQ(r3->is_upload, 1);
    spo->texture_handle = saved;

    // A texture surface with no handle yet is still an upload: the handle
    // arrives at GetHandle, and a write before that is not screen content.
    uint32_t tex = rec_make_surface(8, 8, 8, DDSCAPS_TEXTURE);
    CHECK(tex != 0);
    if (!tex)
        return;
    ComObj *to = rec_obj(tex);
    CHECK(to != nullptr);
    if (to)
        CHECK_EQ(to->texture_handle, 0u);
    uint32_t before_n = host_frame_record_count(host_frame_current());
    blt_for_test(tex, sp, 0, 0, 8, 8, 0);
    uint32_t after_n = host_frame_record_count(host_frame_current());
    CHECK_EQ(after_n, before_n + 1);
    const HostBlitRecord *rtex = host_frame_record(host_frame_current(), after_n - 1);
    CHECK(rtex != nullptr);
    if (rtex)
        CHECK_EQ(rtex->is_upload, 1);
}

static void test_revision_bumps_and_retained_lease() {
    rec_reset();
    uint32_t rt = make_render_target_for_test(640, 480, 8);
    uint32_t sp = make_offscreen_for_test(8, 8, 8);
    fill_for_test(sp, 5);
    reset_ddraw_for_test();
    blt_for_test(rt, sp, 0, 0, 8, 8, 0);
    const HostBlitRecord *r = host_frame_record(host_frame_current(), 0);
    CHECK(r != nullptr);
    if (!r)
        return;
    HostSurfaceKey k = r->src;

    // The guest overwrites the source. The frame still refers to what it
    // recorded, so the old contents have to survive - copied at the moment
    // they were about to be lost, not when they were recorded.
    fill_for_test(sp, 6);
    CHECK(host_surface_revision_for_test(k.surface) != k.revision);
    HostPixels px;
    CHECK_EQ(host_revision_lease(k, &px), 0);
    CHECK(px.data != nullptr);
    if (px.data)
        CHECK_EQ(px.data[0], 5);
    host_revision_release(k);
}

static void test_seal_events() {
    rec_reset();
    uint32_t primary = make_primary_chain_for_test();
    CHECK(primary != 0);
    if (!primary)
        return;
    uint32_t rt = make_render_target_for_test(64, 64, 8);
    uint32_t sp = make_offscreen_for_test(4, 4, 8);
    CHECK(rt != 0);
    CHECK(sp != 0);
    if (!rt || !sp)
        return;
    fill_for_test(sp, 7);

    uint32_t caps = sc(0x1408);
    wr32(caps, DDSCAPS_BACKBUFFER);
    CHECK_EQ(call_method(primary, S_GetAttachedSurface, {caps, sc(0x140c)}), DD_OK);
    uint32_t back = rd32(sc(0x140c));
    CHECK(back != 0);
    if (!back)
        return;

    uint32_t entries = sc(0x1000);
    gm_zero(entries, 256 * 4);
    wr32(entries + 1 * 4, 0x000000FFu);
    uint32_t dd = rec_dd();
    CHECK_EQ(call_method(dd, DD_CreatePalette,
                         {DDPCAPS_8BIT | DDPCAPS_ALLOW256, entries, sc(0x1400), 0}),
             DD_OK);
    uint32_t pal = rd32(sc(0x1400));
    CHECK(pal != 0);
    if (!pal)
        return;
    CHECK_EQ(call_method(primary, S_SetPalette, {pal}), DD_OK);

    reset_ddraw_for_test();
    HostFrameHandle f0 = host_frame_current();

    // A frame ends in exactly two places: a Flip of the primary chain, and the
    // production pump's tick. Nothing else does, however much of the screen it
    // changes. These four are the ones that used to.
    blt_for_test(rt, sp, 0, 0, 4, 4, 0); // an offscreen write
    CHECK_EQ(host_frame_current().id, f0.id);
    blt_for_test(primary, sp, 0, 0, 4, 4, 0); // a write to the PRIMARY
    CHECK_EQ(host_frame_current().id, f0.id);
    lock_write_poke_for_test(primary, 1, 1, 3); // and an Unlock on it
    CHECK_EQ(host_frame_current().id, f0.id);
    wr32(entries + 1 * 4, 0x0000FF00u); // and a palette change
    CHECK_EQ(call_method(pal, P_SetEntries, {0, 0, 256, entries}), DD_OK);
    CHECK_EQ(host_frame_current().id, f0.id);

    // The palette change really happened, which is what makes the assertion
    // above about sealing rather than about a call that did nothing.
    const HostBlitRecord *r0 = host_frame_record(f0, 0);
    CHECK(r0 != nullptr);
    blt_for_test(rt, sp, 0, 0, 4, 4, 0);
    const HostBlitRecord *rlast = host_frame_record(f0, host_frame_record_count(f0) - 1);
    CHECK(rlast != nullptr);
    if (r0 && rlast)
        CHECK(rlast->palette_version != r0->palette_version);

    // One frame, not five: the ordering state a HUD rule reads is still the
    // one this frame started with.
    CHECK(host_frame_current().id == f0.id);

    // The pump ends it.
    ddraw_pump_present();
    CHECK_EQ(host_frame_current().id, f0.id + 1);

    // A pump with no records seals nothing: an idle game must not produce an
    // unbounded stream of empty frames.
    HostFrameHandle f1 = host_frame_current();
    ddraw_pump_present();
    CHECK_EQ(host_frame_current().id, f1.id);

    // And a Flip of the primary chain ends one, with the content in the back
    // buffer, which is how the game draws.
    blt_for_test(back, sp, 0, 0, 4, 4, 0);
    CHECK_EQ(host_frame_current().id, f1.id);
    call_method(primary, S_Flip, {0, DDFLIP_WAIT});
    CHECK_EQ(host_frame_current().id, f1.id + 1);

    // A draw with no blit behind it is content as well: a gameplay frame that
    // only renders geometry still has a picture to present, and a recorder
    // that counted records alone could never seal one.
    reset_ddraw_for_test();
    HostFrameHandle f2 = host_frame_current();
    ddraw_note_draw();
    ddraw_pump_present();
    CHECK(host_frame_current().id != f2.id);
    ddraw_note_device(0);
}

// Four simultaneously live arenas must stay independent, then reuse all
// chunks (including an oversized chunk) after retirement for 100 frames.
static void test_unchanged_texture_uploads() {
    rec_reset();
    uint32_t id = make_offscreen_for_test(8, 8, 16);
    ComObj *surface = com_this(id);
    CHECK(surface != nullptr);
    if (!surface)
        return;
    surface->texture_handle = 0x170001;
    d3d_upload_texture(surface);
    size_t uploads = g_uploads.size();
    CHECK(uploads > 0);
    uint32_t revision = ddraw_surface_revision(surface->id);
    for (int frame = 0; frame < 100; ++frame)
        d3d_upload_texture(surface);
    printf("T17 unchanged texture fixture phases: upload=%zu/%zu\n", g_uploads.size() - uploads,
           (g_uploads.size() - uploads) * 8 * 8);
    CHECK_EQ(g_uploads.size(), uploads);
    CHECK_EQ(ddraw_surface_revision(surface->id), revision);
    fill_for_test(id, 1);
    d3d_upload_texture(surface);
    CHECK_EQ(g_uploads.size(), uploads + 1);
    CHECK(ddraw_surface_revision(surface->id) != revision);
}

static void test_frame_command_pool() {
    rec_reset();
    uint32_t primary = make_primary_chain_for_test();
    uint32_t sp = make_offscreen_for_test(8, 8, 8);
    CHECK(primary != 0);
    HostFrameHandle held[4]{};
    uint8_t *payload[4]{};
    for (int batch = 0; batch < 26; ++batch) {
        uint64_t before = ddraw_frame_chunk_allocations_for_test();
        for (int i = 0; i < 4; ++i) {
            held[i] = host_frame_current();
            payload[i] = (uint8_t *)ddraw_frame_alloc(100000, 16);
            memset(payload[i], i + 1, 100000);
            blt_for_test(primary, sp, 0, 0, 8, 8, 0);
            pump_present_for_test();
            CHECK(host_frame_current().id != held[i].id);
        }
        for (int i = 0; i < 4; ++i) {
            CHECK_EQ(payload[i][0], i + 1);
            CHECK_EQ(payload[i][99999], i + 1);
            host_frame_release(held[i]);
        }
        if (batch)
            CHECK_EQ(ddraw_frame_chunk_allocations_for_test(), before);
    }
}

static void test_frame_release_frees_leases() {
    rec_reset();
    uint32_t rt = make_render_target_for_test(640, 480, 8);
    uint32_t sp = make_offscreen_for_test(8, 8, 8);
    fill_for_test(sp, 5);
    reset_ddraw_for_test();
    uint64_t before = host_retained_bytes_for_test();

    blt_for_test(rt, sp, 0, 0, 8, 8, 0);
    HostFrameHandle f = host_frame_current();
    // Overwriting the source forces the copy the frame's lease is holding.
    fill_for_test(sp, 6);
    CHECK(host_retained_bytes_for_test() > before);

    // Sealed first, because a frame is handed over finished and released when
    // the compositor is done with it. A release test that never sealed was
    // releasing a frame still being recorded into.
    //
    // The pump ends a frame that reached the SCREEN, so the frame needs a
    // write to the primary in it: a frame whose content never got there is a
    // picture identical to the one before it.
    uint32_t primary = make_primary_chain_for_test();
    CHECK(primary != 0);
    if (!primary)
        return;
    blt_for_test(primary, sp, 0, 0, 8, 8, 0);
    pump_present_for_test();
    CHECK(host_frame_current().id != f.id);

    // Releasing the frame releases the lease, and the copy goes with it.
    host_frame_release(f);
    CHECK_EQ(host_retained_bytes_for_test(), before);
}

static unsigned presenter_writes = 0, presenter_seals = 0;
static HostFrameHandle presenter_sealed{};
static void recorder_present_write() {
    ++presenter_writes;
}
static void recorder_present_seal() {
    ++presenter_seals;
    presenter_sealed = host_frame_current();
    CHECK(host_frame_palette_version(presenter_sealed) != 0);
}
static void test_presenter_seal_hook_and_retirement_queue() {
    rec_reset();
    uint32_t primary = make_primary_chain_for_test();
    uint32_t sprite = make_offscreen_for_test(8, 8, 8);
    CHECK(primary != 0 && sprite != 0);
    if (!primary || !sprite)
        return;
    fill_for_test(sprite, 3);
    reset_ddraw_for_test();
    presenter_writes = presenter_seals = 0;
    ddraw_set_present_callbacks(recorder_present_write, recorder_present_seal);
    pump_present_for_test();
    CHECK_EQ(presenter_seals, 0u);
    CHECK_EQ(presenter_writes, 0u);
    uint32_t desc = sc(0xa80);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc, DDSD_SIZE);
    CHECK_EQ(call_method(sprite, S_Lock, {0, desc, DDLOCK_READONLY | DDLOCK_WAIT, 0}), DD_OK);
    call_method(sprite, S_Unlock, {0});
    CHECK_EQ(presenter_writes, 0u);
    blt_for_test(primary, sprite, 0, 0, 8, 8, 0);
    fill_for_test(sprite, 4);
    auto f = host_frame_current();
    CHECK(host_retained_bytes_for_test() > 0);
    pump_present_for_test();
    CHECK_EQ(presenter_seals, 1u);
    CHECK_EQ(presenter_sealed.id, f.id);
    CHECK(host_frame_current().id != f.id);
    CHECK(presenter_writes > 0);
    pump_present_for_test();
    CHECK_EQ(presenter_seals, 1u);
    std::thread completion([f] { ddraw_present_release(f); });
    completion.join();
    CHECK(host_frame_record_count(f) > 0); // worker never touches guest-owned stores
    ddraw_drain_present_releases();
    CHECK_EQ(host_frame_record_count(f), 0u);
    CHECK_EQ(host_retained_bytes_for_test(), 0u);
    // Primary Flip is the second seal path, with the same callback ordering.
    fill_for_test(primary, 9);
    auto flip_frame = host_frame_current();
    CHECK_EQ(call_method(primary, S_Flip, {0, 0}), DD_OK);
    CHECK_EQ(presenter_seals, 2u);
    CHECK_EQ(presenter_sealed.id, flip_frame.id);
    ddraw_set_present_callbacks(nullptr, nullptr);
    host_frame_release(flip_frame);
}

static void test_screen_class() {
    rec_reset();
    uint32_t rt = make_render_target_for_test(640, 480, 8);
    uint32_t sp = make_offscreen_for_test(8, 8, 8);
    fill_for_test(sp, 1);
    reset_ddraw_for_test();

    // No device and no video: a menu.
    blt_for_test(rt, sp, 0, 0, 8, 8, 0);
    CHECK_EQ((uint32_t)host_frame_class(host_frame_current()), (uint32_t)HOST_SCREEN_MENU);

    CHECK(host_frame_class_valid(host_frame_current()));
    // D3D9 supplies a finished image with no semantic phase signal. Preserve
    // its compatibility composition class, but do not call it a menu sample.
    reset_ddraw_for_test();
    ddraw_external_present_begin();
    const auto external = host_frame_current();
    CHECK_EQ(host_frame_class(external), HOST_SCREEN_MENU);
    CHECK(!host_frame_class_valid(external));
    ddraw_external_present_end();
    CHECK(host_frame_current().id != external.id);
    CHECK(!host_frame_class_valid(external));
    ddraw_note_device(1); // Lazily creates the next frame.
    CHECK(host_frame_class_valid(host_frame_current()));
    CHECK(!host_frame_class_valid(external)); // immutable after next-frame transition
    host_frame_release(external);
    CHECK(!host_frame_class_valid(external)); // missing frame never supplies evidence

    // A device exists: gameplay, whether or not anything was drawn this frame.
    reset_ddraw_for_test();
    ddraw_note_device(1);
    blt_for_test(rt, sp, 0, 0, 8, 8, 0);
    CHECK_EQ((uint32_t)host_frame_class(host_frame_current()), (uint32_t)HOST_SCREEN_GAMEPLAY);
    CHECK_EQ(host_frame_had_draws(host_frame_current()), 0);
    CHECK(host_frame_class_valid(host_frame_current()));
    ddraw_note_draw();
    CHECK_EQ(host_frame_had_draws(host_frame_current()), 1);

    // The movie: the game destroys its device, sets 640x480x16 and the decoder
    // writes the primary through Lock/Unlock. That combination, and only that
    // one, is a video frame.
    reset_ddraw_for_test();
    ddraw_note_device(0);
    uint32_t dd = rec_dd();
    call_method(dd, DD_SetDisplayMode, {640, 480, 16});
    uint32_t primary = make_primary_chain_for_test();
    CHECK(primary != 0);
    if (!primary)
        return;
    // The decoder's write does not end the frame - only a Flip or the pump
    // does - so the frame it landed in is still the current one.
    HostFrameHandle movie = host_frame_current();
    lock_write_poke_for_test(primary, 0, 0, 0x1f);
    CHECK_EQ(host_frame_current().id, movie.id);
    CHECK_EQ((uint32_t)host_frame_class(movie), (uint32_t)HOST_SCREEN_FMV);
    CHECK(host_frame_class_valid(movie));
    // And the frame AFTER it is not a movie frame. FMV is a property of the
    // frame that carried a decoder write, and carrying it forward called every
    // frame after a movie a movie until something else reclassified it.
    ddraw_pump_present();
    CHECK(host_frame_current().id != movie.id);
    CHECK_EQ((uint32_t)host_frame_class(host_frame_current()), (uint32_t)HOST_SCREEN_MENU);
    call_method(dd, DD_SetDisplayMode, {640, 480, 8});
}

// An 8-bit fade is a run of frames whose only change is a palette write. The
// pixels never move, so a recorder that counted only records and draws would
// seal nothing at all, the compositor would repeat its last frame under the
// palette version it leased, and the fade would never appear on screen.
static void test_palette_only_frames_seal() {
    rec_reset();
    uint32_t primary = make_primary_chain_for_test();
    CHECK(primary != 0);
    if (!primary)
        return;
    uint32_t dd = rec_dd();

    uint32_t entries = sc(0x1000);
    gm_zero(entries, 256 * 4);
    wr32(entries + 1 * 4, 0x00000010u);
    CHECK_EQ(call_method(dd, DD_CreatePalette,
                         {DDPCAPS_8BIT | DDPCAPS_ALLOW256, entries, sc(0x1400), 0}),
             DD_OK);
    uint32_t pal = rd32(sc(0x1400));
    CHECK(pal != 0);
    if (!pal)
        return;
    CHECK_EQ(call_method(primary, S_SetPalette, {pal}), DD_OK);

    reset_ddraw_for_test();
    // An idle pump still seals nothing: this is a fade, not a spin.
    HostFrameHandle start = host_frame_current();
    ddraw_pump_present();
    CHECK_EQ(host_frame_current().id, start.id);

    // Three steps of a fade, each one a SetEntries and a pump tick, and
    // nothing else. Three frames, three palette versions.
    uint32_t ids[3] = {0, 0, 0};
    uint32_t versions[3] = {0, 0, 0};
    for (int step = 0; step < 3; ++step) {
        wr32(entries + 1 * 4, (uint32_t)(0x20 + step * 0x20));
        CHECK_EQ(call_method(pal, P_SetEntries, {0, 0, 256, entries}), DD_OK);
        HostFrameHandle f = host_frame_current();
        ids[step] = (uint32_t)f.id;
        CHECK_EQ(host_frame_record_count(f), 0u); // no pixel moved
        ddraw_pump_present();
        CHECK(host_frame_current().id != f.id); // and yet the frame ended
        versions[step] = host_frame_palette_version(f);
        CHECK(versions[step] != 0u);
        // The colours it sealed under, not the ones the guest moves on to.
        // Released straight away: a lease left open here would hold the
        // version through the next step's write and hide whether the SEAL is
        // holding it.
        const uint8_t *rgb = host_palette_lease(versions[step]);
        CHECK(rgb != nullptr);
        if (rgb) {
            CHECK_EQ(rgb[3], (uint32_t)(0x20 + step * 0x20));
            host_palette_release(versions[step]);
        }
    }
    // The second step's write must not prune the first frame's version. In a
    // fade the guest writes again long before the compositor looks, and a
    // version pruned there would leave the frame naming colours nobody can
    // fetch.
    {
        const uint8_t *first = host_palette_lease(versions[0]);
        CHECK(first != nullptr);
        if (first) {
            CHECK_EQ(first[3], 0x20u);
            host_palette_release(versions[0]);
        }
    }
    CHECK(ids[0] != ids[1]);
    CHECK(ids[1] != ids[2]);
    CHECK(versions[0] != versions[1]);
    CHECK(versions[1] != versions[2]);
    for (int step = 0; step < 3; ++step) {
        HostFrameHandle f = {ids[step]};
        host_frame_release(f);
    }

    // Attaching the palette that is ALREADY attached changes no colour, so it
    // is not a change to the picture and ends no frame. The game re-attaches
    // its palette to the primary as a matter of course.
    reset_ddraw_for_test();
    HostFrameHandle same = host_frame_current();
    CHECK_EQ(call_method(primary, S_SetPalette, {pal}), DD_OK);
    ddraw_pump_present();
    CHECK_EQ(host_frame_current().id, same.id);

    // A palette that governs nothing on screen is not a change to the picture.
    // A texture's palette is the case: the renderer is told, and no frame ends.
    uint32_t tex = rec_make_surface(16, 16, 8, DDSCAPS_TEXTURE);
    CHECK(tex != 0);
    if (!tex)
        return;
    uint32_t entries2 = sc(0x1500); // 0x400 bytes: 0x1500 through 0x18ff
    gm_zero(entries2, 256 * 4);
    CHECK_EQ(call_method(dd, DD_CreatePalette,
                         {DDPCAPS_8BIT | DDPCAPS_ALLOW256, entries2, sc(0x1900), 0}),
             DD_OK);
    uint32_t texpal = rd32(sc(0x1900));
    CHECK(texpal != 0);
    if (!texpal)
        return;
    CHECK_EQ(call_method(tex, S_SetPalette, {texpal}), DD_OK);
    reset_ddraw_for_test();
    HostFrameHandle quiet = host_frame_current();
    wr32(entries2 + 1 * 4, 0x00000077u);
    CHECK_EQ(call_method(texpal, P_SetEntries, {0, 0, 256, entries2}), DD_OK);
    ddraw_pump_present();
    CHECK_EQ(host_frame_current().id, quiet.id);
}

// Only the PRIMARY chain's Flip is a frame boundary. An offscreen flip chain is
// a private double buffer of the guest's, and flipping it says nothing about
// what is on screen.
static void test_offscreen_flip_does_not_seal() {
    rec_reset();
    uint32_t dd = rec_dd();
    uint32_t desc = sc(0x900);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT | DDSD_BACKBUFFERCOUNT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_OFFSCREENPLAIN | DDSCAPS_FLIP | DDSCAPS_COMPLEX);
    wr32(desc + DDSD_OFF_dwWidth, 64);
    wr32(desc + DDSD_OFF_dwHeight, 64);
    wr32(desc + DDSD_OFF_dwBackBufferCount, 1);
    call_method(dd, DD_CreateSurface, {desc, sc(0xa00), 0});
    uint32_t chain = rd32(sc(0xa00));
    CHECK(chain != 0);
    if (!chain)
        return;
    uint32_t sp = make_offscreen_for_test(8, 8, 8);
    CHECK(sp != 0);
    if (!sp)
        return;
    fill_for_test(sp, 4);

    reset_ddraw_for_test();
    HostFrameHandle f = host_frame_current();
    blt_for_test(chain, sp, 0, 0, 8, 8, 0);
    CHECK_EQ(host_frame_current().id, f.id);
    CHECK_EQ(call_method(chain, S_Flip, {0, DDFLIP_WAIT}), DD_OK);
    // Still the same frame: nothing about the screen has happened.
    CHECK_EQ(host_frame_current().id, f.id);

    // Nor does the pump end it. The pump fires on every PeekMessageA and this
    // game pumps in more than one place, so it seals at most once per thing
    // presented or drawn - and an offscreen blit is neither. A frame whose
    // only content never reached the screen would be a frame identical to the
    // one before it.
    ddraw_pump_present();
    CHECK_EQ(host_frame_current().id, f.id);

    // Nor does a write to the chain's BACK BUFFER. It has a front_obj, like
    // the primary's back buffer does, and nothing about it reaches the screen:
    // the head of its chain is not the primary.
    uint32_t caps = sc(0x1408);
    wr32(caps, DDSCAPS_BACKBUFFER);
    CHECK_EQ(call_method(chain, S_GetAttachedSurface, {caps, sc(0x140c)}), DD_OK);
    uint32_t chain_back = rd32(sc(0x140c));
    CHECK(chain_back != 0);
    if (!chain_back)
        return;
    blt_for_test(chain_back, sp, 0, 0, 8, 8, 0);
    ddraw_pump_present();
    CHECK_EQ(host_frame_current().id, f.id);

    // A write to the PRIMARY is a presentation, and then the pump ends it.
    uint32_t primary = make_primary_chain_for_test();
    CHECK(primary != 0);
    if (!primary)
        return;
    blt_for_test(primary, sp, 0, 0, 8, 8, 0);
    CHECK_EQ(host_frame_current().id, f.id); // presenting still is not sealing
    ddraw_pump_present();
    CHECK(host_frame_current().id != f.id);

    // And a second pump straight after seals nothing: one seal per picture,
    // which is what keeps a mid-frame input drain from ending a frame twice.
    HostFrameHandle g = host_frame_current();
    ddraw_pump_present();
    CHECK_EQ(host_frame_current().id, g.id);
}

// IDirectDrawSurface4::Unlock is the one that takes a RECT, and the shim reads
// its argument that way only for that interface. The game holds the v1 view, so
// this path has no other cover.
static void test_v4_unlock_takes_a_rect() {
    rec_reset();
    uint32_t rt = make_render_target_for_test(64, 64, 8);
    CHECK(rt != 0);
    if (!rt)
        return;
    fill_for_test(rt, 0);
    ComObj *o = rec_obj(rt);
    CHECK(o != nullptr);
    if (!o)
        return;

    // IID_IDirectDrawSurface4 = 0B2B8630-AD35-11D0-8EA6-00609797EA5B.
    uint8_t s4[16] = {0x30, 0x86, 0x2B, 0x0B, 0x35, 0xAD, 0xD0, 0x11,
                      0x8E, 0xA6, 0x00, 0x60, 0x97, 0x97, 0xEA, 0x5B};
    uint32_t iid = sc(0x40);
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, s4[i]);
    CHECK_EQ(call_method(rt, S_QueryInterface, {iid, sc(0x60)}), S_OK);
    uint32_t v4 = rd32(sc(0x60));
    CHECK(v4 != 0);
    if (!v4)
        return;

    reset_ddraw_for_test();
    uint32_t desc = sc(0xa80);
    uint32_t left = sc(0x1d80), right = sc(0x1da0);
    wr32(left + 0, 0);
    wr32(left + 4, 0);
    wr32(left + 8, 16);
    wr32(left + 12, 64);
    wr32(right + 0, 32);
    wr32(right + 4, 0);
    wr32(right + 8, 64);
    wr32(right + 12, 64);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    CHECK_EQ(call_method(v4, S_Lock, {left, desc, DDLOCK_WAIT, 0}), DD_OK);
    CHECK_EQ(call_method(v4, S_Lock, {right, desc, DDLOCK_WAIT, 0}), DD_OK);
    wr8(o->pixels + 9 * o->pitch + 3, 55); // inside the LEFT lock

    // Closing the left lock by naming its rectangle records the write. Popping
    // the newest instead would look at the right lock, which that pixel is not
    // in, and record nothing.
    CHECK_EQ(call_method(v4, S_Unlock, {left}), DD_OK);
    HostFrameHandle f = host_frame_current();
    CHECK_EQ(host_frame_record_count(f), 1u);
    const HostBlitRecord *r = host_frame_record(f, 0);
    CHECK(r != nullptr);
    if (r) {
        CHECK_EQ(r->dst_x, 3);
        CHECK_EQ(r->dst_y, 9);
    }
    CHECK_EQ(call_method(v4, S_Unlock, {right}), DD_OK);
}

// The game hides the Windows cursor and draws its own, so the shim has to know
// which surface the pointer comes from. It reads the game's own globals:
// create_mouse_surface (004fccb0) puts its two 32x32 pointer surfaces there,
// and the cursor draw at 004fd370 is the only code that uses them.
static void test_cursor_surface_learned() {
    rec_reset();
    const uint32_t ptrs[] = RECOMP_HOOK_CURSOR_SURFACE_PTRS; // the game's cursor globals
    const uint32_t kPtrA = ptrs[0], kPtrB = ptrs[1];
    wr32(kPtrA, 0);
    wr32(kPtrB, 0);
    CHECK_EQ(host_cursor_surface(), HOST_SURFACE_NONE);

    uint32_t pointer = rec_make_surface(32, 32, 8, DDSCAPS_OFFSCREENPLAIN | DDSCAPS_SYSTEMMEMORY);
    uint32_t sprite = rec_make_surface(32, 32, 8, DDSCAPS_OFFSCREENPLAIN);
    uint32_t primary = make_primary_chain_for_test();
    CHECK(pointer != 0);
    CHECK(sprite != 0);
    CHECK(primary != 0);
    if (!pointer || !sprite || !primary)
        return;
    ComObj *po = rec_obj(pointer);
    CHECK(po != nullptr);
    if (!po)
        return;

    // A small keyed sprite blitted onto the visible chain teaches nothing:
    // that is what a HUD sprite looks like too, and the rule that learned from
    // it could be taught the wrong surface by any frame.
    fill_for_test(sprite, 3);
    set_srckey_for_test(sprite, 0, 0);
    blt_for_test(primary, sprite, 10, 10, 32, 32, DDBLT_KEYSRC);
    CHECK_EQ(host_cursor_surface(), HOST_SURFACE_NONE);

    // The game's own global is what says it.
    wr32(kPtrA, pointer);
    CHECK_EQ(host_cursor_surface(), po->id);

    // Kept while the game rebuilds them: it clears the globals first, and a
    // frame in between still has a pointer in it.
    wr32(kPtrA, 0);
    CHECK_EQ(host_cursor_surface(), po->id);

    // The second global serves as well as the first.
    wr32(kPtrB, pointer);
    CHECK_EQ(host_cursor_surface(), po->id);

    // A value that is not an interface pointer at all is an ordinary answer of
    // "not learned yet". Reading a guest global is a data read, so a number
    // that happens to be there teaches nothing and complains about nothing.
    wr32(kPtrA, 0x12345678u);
    wr32(kPtrB, 0);
    CHECK_EQ(host_cursor_surface(), po->id);

    // A value that names something that is not a 32x32 surface teaches
    // nothing, so a build whose data lies elsewhere learns nothing rather than
    // learning something wrong.
    uint32_t big = make_offscreen_for_test(64, 64, 8);
    CHECK(big != 0);
    wr32(kPtrA, big);
    wr32(kPtrB, 0);
    CHECK_EQ(host_cursor_surface(), po->id); // still the last good answer

    // A REFUSED mode change is a no-op, so it must not throw the answer away:
    // the game goes on drawing in the mode it still has, and it does not check
    // the return.
    uint32_t dd = rec_dd();
    CHECK_EQ(call_method(dd, DD_SetDisplayMode, {123, 456, 8}), DDERR_INVALIDPARAMS);
    CHECK_EQ(host_cursor_surface(), po->id);

    // An accepted one rebuilds the pointer surfaces, so the answer goes stale.
    CHECK_EQ(call_method(dd, DD_SetDisplayMode, {800, 600, 8}), DD_OK);
    CHECK_EQ(host_cursor_surface(), HOST_SURFACE_NONE);
    call_method(dd, DD_SetDisplayMode, {640, 480, 8});
    wr32(kPtrA, 0);
    wr32(kPtrB, 0);
}

// A Lock hands the guest a raw pointer and the shim sees none of the stores,
// so what a lock wrote can only be known by comparing before with after. Every
// such write is a record, with the payload and coverage a compositor needs to
// replay it - not one synthetic record for the first movie frame.
// An Unlock compares only the rows the guest's own stores touched, as long
// as no import that might write the surface ran while the lock was open; one
// that might sends it back to comparing the whole lock.
static void test_lock_write_tracking() {
    // Run 31 (D-177): the tracked path is tested whatever the game config says (a store_hooks = false game starts
    // with tracking off); the untracked path is tested below; the game's value is restored at the end.
    const bool untracked_game = g_ddraw_untracked_stores;
    g_ddraw_untracked_stores = false;
    rec_reset();
    uint32_t rt = make_render_target_for_test(64, 64, 8);
    CHECK(rt != 0);
    if (!rt)
        return;
    fill_for_test(rt, 0);
    ComObj *o = rec_obj(rt);
    if (!o)
        return;
    uint32_t desc = sc(0xa80);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    auto record_rows = [&](int32_t *y0, int32_t *h) {
        HostFrameHandle f = host_frame_current();
        const uint32_t n = host_frame_record_count(f);
        if (n == 1) {
            const HostBlitRecord *r = host_frame_record(f, 0);
            *y0 = r->dst_y;
            *h = r->h;
        }
        return n;
    };
    int32_t y0 = -1, h = -1;

    // Stores through the pointer, far apart: both are found, one box each.
    reset_ddraw_for_test();
    CHECK_EQ(call_method(rt, S_Lock, {0, desc, DDLOCK_WAIT, 0}), DD_OK);
    wr8(o->pixels + 20 * o->pitch + 3, 9);
    wr8(o->pixels + 40 * o->pitch + 5, 9);
    CHECK_EQ(call_method(rt, S_Unlock, {0}), DD_OK);
    {
        HostFrameHandle f = host_frame_current();
        CHECK_EQ(host_frame_record_count(f), 2u);
        if (host_frame_record_count(f) == 2) {
            CHECK_EQ(host_frame_record(f, 0)->dst_y, 20);
            CHECK_EQ(host_frame_record(f, 1)->dst_y, 40);
        }
    }

    // A store the translated code did not make, with an import in between
    // that might have made it: the whole lock is compared, and it is found.
    reset_ddraw_for_test();
    CHECK_EQ(call_method(rt, S_Lock, {0, desc, DDLOCK_WAIT, 0}), DD_OK);
    gm_ptr(o->pixels + 30 * o->pitch + 7)[0] = 4;
    call_shim(tramp("KERNEL32.dll", "GetTickCount"), {});
    CHECK_EQ(call_method(rt, S_Unlock, {0}), DD_OK);
    CHECK_EQ(record_rows(&y0, &h), 1u);
    CHECK_EQ(y0, 30);
    CHECK_EQ(h, 1);

    // The same store with nothing in between is not the guest's and not an
    // import's, so nothing looks for it: only those two write a locked surface.
    reset_ddraw_for_test();
    CHECK_EQ(call_method(rt, S_Lock, {0, desc, DDLOCK_WAIT, 0}), DD_OK);
    gm_ptr(o->pixels + 50 * o->pitch + 7)[0] = 4;
    CHECK_EQ(call_method(rt, S_Unlock, {0}), DD_OK);
    CHECK_EQ(record_rows(&y0, &h), 0u);

    // Critical sections write no surface, so they leave the narrowing on:
    // a store is still found, and one elsewhere is still not looked for.
    uint32_t cs = sc(0xb00);
    gm_zero(cs, 24);
    call_shim(tramp("KERNEL32.dll", "InitializeCriticalSection"), {cs});
    reset_ddraw_for_test();
    CHECK_EQ(call_method(rt, S_Lock, {0, desc, DDLOCK_WAIT, 0}), DD_OK);
    call_shim(tramp("KERNEL32.dll", "EnterCriticalSection"), {cs});
    wr8(o->pixels + 10 * o->pitch + 1, 3);
    gm_ptr(o->pixels + 60 * o->pitch + 1)[0] = 3;
    call_shim(tramp("KERNEL32.dll", "LeaveCriticalSection"), {cs});
    CHECK_EQ(call_method(rt, S_Unlock, {0}), DD_OK);
    CHECK_EQ(record_rows(&y0, &h), 1u);
    CHECK_EQ(y0, 10);
    CHECK_EQ(h, 1);
    call_shim(tramp("KERNEL32.dll", "DeleteCriticalSection"), {cs});
    CHECK_EQ(g_dirty_count, 0u); // every range closed with its lock
    reset_ddraw_for_test();

    // Run 31 (D-177): a game built with store_hooks = false (its translated stores carry no check). No range opens,
    // the store flag stays clear, and the Unlock compares the whole surface: every write is found, the one no range
    // would have seen included (above, the tracked lock did not look for it).
    g_ddraw_untracked_stores = true;
    CHECK_EQ(call_method(rt, S_Lock, {0, desc, DDLOCK_WAIT, 0}), DD_OK);
    CHECK_EQ(g_dirty_count, 0u);
    CHECK_EQ(g_store_hooks, 0u);
    wr8(o->pixels + 20 * o->pitch + 3, 7);
    gm_ptr(o->pixels + 50 * o->pitch + 7)[0] = 7;
    CHECK_EQ(call_method(rt, S_Unlock, {0}), DD_OK);
    {
        HostFrameHandle f = host_frame_current();
        CHECK_EQ(host_frame_record_count(f), 2u);
        if (host_frame_record_count(f) == 2) {
            CHECK_EQ(host_frame_record(f, 0)->dst_y, 20);
            CHECK_EQ(host_frame_record(f, 1)->dst_y, 50);
        }
    }
    g_ddraw_untracked_stores = false;
    reset_ddraw_for_test();
    // and the tracked path sets the flag while its range is open, and clears it with the close
    CHECK_EQ(call_method(rt, S_Lock, {0, desc, DDLOCK_WAIT, 0}), DD_OK);
    CHECK_EQ(g_store_hooks, g_dirty_count != 0 ? 1u : 0u);
    CHECK_EQ(call_method(rt, S_Unlock, {0}), DD_OK);
    CHECK_EQ(g_store_hooks, 0u);
    reset_ddraw_for_test();
    g_ddraw_untracked_stores = untracked_game;
}

static void test_lock_write_records() {
    rec_reset();
    uint32_t rt = make_render_target_for_test(64, 64, 8);
    CHECK(rt != 0);
    if (!rt)
        return;
    fill_for_test(rt, 0);
    ComObj *o = rec_obj(rt);
    CHECK(o != nullptr);
    if (!o)
        return;
    reset_ddraw_for_test();

    // Three pixels, in a line, in the middle of a full-surface lock.
    uint32_t desc = sc(0xa80);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    CHECK_EQ(call_method(rt, S_Lock, {0, desc, DDLOCK_WAIT, 0}), DD_OK);
    wr8(o->pixels + 20 * o->pitch + 10, 5);
    wr8(o->pixels + 20 * o->pitch + 11, 6);
    wr8(o->pixels + 20 * o->pitch + 12, 7);
    CHECK_EQ(call_method(rt, S_Unlock, {0}), DD_OK);

    HostFrameHandle f = host_frame_current();
    CHECK_EQ(host_frame_record_count(f), 1u);
    const HostBlitRecord *r = host_frame_record(f, 0);
    CHECK(r != nullptr);
    if (!r)
        return;
    // The write, not the lock: the record is the bounding box of what changed.
    CHECK_EQ(r->src.surface, HOST_SRC_CPU);
    CHECK_EQ(r->dst_x, 10);
    CHECK_EQ(r->dst_y, 20);
    CHECK_EQ(r->w, 3);
    CHECK_EQ(r->h, 1);
    CHECK_EQ(coverage_sum(r), 3);
    // The payload is in the surface's own format, tightly packed.
    CHECK_EQ(r->cpu_bpp, 8);
    CHECK_EQ(r->cpu_pitch, 3);
    CHECK(r->cpu_pixels != nullptr);
    if (r->cpu_pixels) {
        CHECK_EQ(r->cpu_pixels[0], 5);
        CHECK_EQ(r->cpu_pixels[1], 6);
        CHECK_EQ(r->cpu_pixels[2], 7);
    }

    // A lock that wrote nothing is not a write and records nothing.
    reset_ddraw_for_test();
    CHECK_EQ(call_method(rt, S_Lock, {0, desc, DDLOCK_WAIT, 0}), DD_OK);
    CHECK_EQ(call_method(rt, S_Unlock, {0}), DD_OK);
    CHECK_EQ(host_frame_record_count(host_frame_current()), 0u);

    // A read-only lock is not a write either, whatever happens through the
    // pointer: the guest promised not to write, and the shim takes the promise
    // rather than paying for a snapshot on every read.
    reset_ddraw_for_test();
    CHECK_EQ(call_method(rt, S_Lock, {0, desc, DDLOCK_WAIT | DDLOCK_READONLY, 0}), DD_OK);
    CHECK_EQ(call_method(rt, S_Unlock, {0}), DD_OK);
    CHECK_EQ(host_frame_record_count(host_frame_current()), 0u);

    // A NESTED write lock is its own write. DirectDraw allows locks to nest,
    // and an inner one can name a rectangle the outer one never covered, so a
    // single outermost shadow loses whatever it wrote.
    reset_ddraw_for_test();
    uint32_t inner = sc(0x1d00);
    wr32(inner + 0, 40);
    wr32(inner + 4, 40);
    wr32(inner + 8, 44);
    wr32(inner + 12, 41);
    CHECK_EQ(call_method(rt, S_Lock, {0, desc, DDLOCK_WAIT, 0}), DD_OK);
    CHECK_EQ(call_method(rt, S_Lock, {inner, desc, DDLOCK_WAIT, 0}), DD_OK);
    wr8(o->pixels + 40 * o->pitch + 41, 9);
    CHECK_EQ(call_method(rt, S_Unlock, {0}), DD_OK); // closes the inner lock
    HostFrameHandle nested = host_frame_current();
    CHECK_EQ(host_frame_record_count(nested), 1u);
    const HostBlitRecord *ri = host_frame_record(nested, 0);
    CHECK(ri != nullptr);
    if (ri) {
        CHECK_EQ(ri->dst_x, 41);
        CHECK_EQ(ri->dst_y, 40);
        CHECK_EQ(ri->w, 1);
        CHECK_EQ(ri->h, 1);
    }
    // The outer lock saw those pixels change too - it was open while they were
    // written - but they are already recorded, and sending them twice would
    // hand the compositor the same payload twice. It adds nothing.
    CHECK_EQ(call_method(rt, S_Unlock, {0}), DD_OK);
    CHECK_EQ(host_frame_record_count(nested), 1u);

    // The outer lock still records what IT wrote. Same nesting, but this time
    // the outer lock writes a pixel of its own outside the inner rectangle.
    reset_ddraw_for_test();
    CHECK_EQ(call_method(rt, S_Lock, {0, desc, DDLOCK_WAIT, 0}), DD_OK);
    CHECK_EQ(call_method(rt, S_Lock, {inner, desc, DDLOCK_WAIT, 0}), DD_OK);
    wr8(o->pixels + 40 * o->pitch + 41, 21);
    CHECK_EQ(call_method(rt, S_Unlock, {0}), DD_OK);
    wr8(o->pixels + 50 * o->pitch + 3, 22);
    CHECK_EQ(call_method(rt, S_Unlock, {0}), DD_OK);
    HostFrameHandle both = host_frame_current();
    CHECK_EQ(host_frame_record_count(both), 2u);
    const HostBlitRecord *outer = host_frame_record(both, 1);
    CHECK(outer != nullptr);
    if (outer) {
        CHECK_EQ(outer->dst_x, 3);
        CHECK_EQ(outer->dst_y, 50);
        CHECK_EQ(outer->w, 1);
        CHECK_EQ(outer->h, 1);
    }

    // Two locks open on rectangles that do not touch. The re-baseline that
    // keeps an outer Unlock from repeating an inner write has to notice that
    // there is nothing in common between them: the intersection is empty, its
    // width is negative, and a memcpy of a negative size is the end of the
    // process.
    reset_ddraw_for_test();
    uint32_t left = sc(0x1d40), right = sc(0x1d60);
    wr32(left + 0, 0);
    wr32(left + 4, 0);
    wr32(left + 8, 16);
    wr32(left + 12, 64);
    wr32(right + 0, 32);
    wr32(right + 4, 0);
    wr32(right + 8, 64);
    wr32(right + 12, 64);
    CHECK_EQ(call_method(rt, S_Lock, {left, desc, DDLOCK_WAIT, 0}), DD_OK);
    CHECK_EQ(call_method(rt, S_Lock, {right, desc, DDLOCK_WAIT, 0}), DD_OK);
    wr8(o->pixels + 5 * o->pitch + 40, 44); // inside the right lock only
    CHECK_EQ(call_method(rt, S_Unlock, {0}), DD_OK);
    HostFrameHandle sib = host_frame_current();
    CHECK_EQ(host_frame_record_count(sib), 1u);
    const HostBlitRecord *rs = host_frame_record(sib, 0);
    CHECK(rs != nullptr);
    if (rs) {
        CHECK_EQ(rs->dst_x, 40);
        CHECK_EQ(rs->dst_y, 5);
    }
    // The left lock closes over a region that was never written, and nothing
    // was re-baselined into it.
    CHECK_EQ(call_method(rt, S_Unlock, {0}), DD_OK);
    CHECK_EQ(host_frame_record_count(sib), 1u);

    // A guest that closes its locks OUT OF ORDER says which one it means with
    // the rectangle. Closing the outer lock first must not diff the inner
    // shadow, or whatever the inner lock writes afterwards is lost.
    reset_ddraw_for_test();
    uint32_t whole = sc(0x1d20);
    wr32(whole + 0, 0);
    wr32(whole + 4, 0);
    wr32(whole + 8, 64);
    wr32(whole + 12, 64);
    // IDirectDrawSurface::Unlock takes the pointer Lock returned, not a
    // rectangle, and that is what says which lock is being closed. Two locks
    // side by side, and the guest closes the FIRST one: matching by pointer
    // records the write that landed in it, while popping the newest shadow
    // instead would find a region the write never touched and record nothing.
    reset_ddraw_for_test();
    uint32_t la = sc(0x1d40), lb = sc(0x1d60);
    wr32(la + 0, 0);
    wr32(la + 4, 0);
    wr32(la + 8, 16);
    wr32(la + 12, 64);
    wr32(lb + 0, 32);
    wr32(lb + 4, 0);
    wr32(lb + 8, 64);
    wr32(lb + 12, 64);
    CHECK_EQ(call_method(rt, S_Lock, {la, desc, DDLOCK_WAIT, 0}), DD_OK);
    uint32_t ptr_a = rd32(desc + DDSD_OFF_lpSurface);
    CHECK_EQ(call_method(rt, S_Lock, {lb, desc, DDLOCK_WAIT, 0}), DD_OK);
    uint32_t ptr_b = rd32(desc + DDSD_OFF_lpSurface);
    CHECK(ptr_a != 0);
    CHECK(ptr_b != 0);
    CHECK(ptr_a != ptr_b);
    wr8(o->pixels + 7 * o->pitch + 5, 33); // inside A, not inside B
    CHECK_EQ(call_method(rt, S_Unlock, {ptr_a}), DD_OK);
    HostFrameHandle ooo = host_frame_current();
    CHECK_EQ(host_frame_record_count(ooo), 1u);
    const HostBlitRecord *last = host_frame_record(ooo, 0);
    CHECK(last != nullptr);
    if (last) {
        CHECK_EQ(last->dst_x, 5);
        CHECK_EQ(last->dst_y, 7);
    }
    CHECK_EQ(call_method(rt, S_Unlock, {ptr_b}), DD_OK);

    // And a write lock UNDER a read-only outer lock is still a write. The
    // outer lock shadows nothing, so without a stack this write had nowhere to
    // be recorded at all.
    reset_ddraw_for_test();
    CHECK_EQ(call_method(rt, S_Lock, {0, desc, DDLOCK_WAIT | DDLOCK_READONLY, 0}), DD_OK);
    CHECK_EQ(call_method(rt, S_Lock, {inner, desc, DDLOCK_WAIT, 0}), DD_OK);
    wr8(o->pixels + 40 * o->pitch + 42, 11);
    CHECK_EQ(call_method(rt, S_Unlock, {0}), DD_OK);
    HostFrameHandle under = host_frame_current();
    CHECK_EQ(host_frame_record_count(under), 1u);
    const HostBlitRecord *ru = host_frame_record(under, 0);
    CHECK(ru != nullptr);
    if (ru) {
        CHECK_EQ(ru->dst_x, 42);
        CHECK_EQ(ru->dst_y, 40);
    }
    CHECK_EQ(call_method(rt, S_Unlock, {0}), DD_OK);
    // The read-only outer lock adds nothing when it closes.
    CHECK_EQ(host_frame_record_count(under), 1u);
}

// A record names the palette version it was drawn against, and the version's
// colours outlive the guest's next palette write for as long as somebody holds
// them. Without that, a frame composited two writes later repaints in whatever
// colours the guest has by then.
static void test_lock_diff_partial_records_and_payload() {
    rec_reset();
    uint32_t rt = make_render_target_for_test(64, 64, 8);
    ComObj *o = com_this(rt);
    CHECK(o != nullptr);
    if (!o)
        return;
    reset_ddraw_for_test();
    uint32_t desc = sc(0x1c00);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc, DDSD_SIZE);
    CHECK_EQ(call_method(rt, S_Lock, {0, desc, DDLOCK_WAIT, 0}), DD_OK);
    for (int cluster = 0; cluster < 2; ++cluster)
        for (int y = 0; y < 3; ++y)
            for (int x = 0; x < 3; ++x)
                wr8(o->pixels + (10 + cluster * 20 + y) * o->pitch + 10 + x,
                    (uint8_t)(40 + cluster));
    CHECK_EQ(call_method(rt, S_Unlock, {0}), DD_OK);
    HostFrameHandle f = host_frame_current();
    CHECK_EQ(host_frame_record_count(f), 2u);
    for (uint32_t i = 0; i < host_frame_record_count(f); ++i) {
        const HostBlitRecord *r = host_frame_record(f, i);
        CHECK_EQ(r->src.surface, HOST_SRC_CPU);
        CHECK_EQ(r->w, 3);
        CHECK_EQ(r->h, 3);
        CHECK_EQ(coverage_sum(r), 9);
        CHECK(r->cpu_pixels != nullptr);
        if (r->cpu_pixels)
            for (int y = 0; y < r->h; ++y)
                for (int x = 0; x < r->w; ++x)
                    CHECK_EQ(r->cpu_pixels[y * r->cpu_pitch + x], 40u + i);
    }
}

// A DirectSound object made through CoCreateInstance is not initialised
// until the game calls Initialize on it, which must therefore succeed; the
// class is the only one the runtime registers.
static void test_cocreate_directsound() {
    static const uint8_t clsid_dsound[16] = {0x46, 0xd9, 0xd4, 0x47, 0xe8, 0x62, 0xcf, 0x11,
                                             0x93, 0xbc, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00};
    static const uint8_t iid_dsound[16] = {0x83, 0xfa, 0x9a, 0x27, 0x81, 0x49, 0xce, 0x11,
                                           0xa5, 0x21, 0x00, 0x20, 0xaf, 0x0b, 0xe5, 0x60};
    uint32_t clsid = sc(0x1d00), iid = sc(0x1d10), ppv = sc(0x1d20);
    memcpy(g_mem + clsid, clsid_dsound, 16);
    memcpy(g_mem + iid, iid_dsound, 16);
    wr32(ppv, 0);
    uint32_t cocreate = tramp("ole32.dll", "CoCreateInstance");
    CHECK(cocreate != 0);
    CHECK_EQ(call_shim(cocreate, {clsid, 0, 1, iid, ppv}), DS_OK);
    uint32_t ds = rd32(ppv);
    CHECK(ds != 0);
    if (ds) {
        CHECK_EQ(call_method(ds, 10, {0}), DS_OK); // Initialize(NULL): the default device
        call_method(ds, 2);                        // Release
    }
    wr8(clsid, 0xff);
    CHECK_EQ(call_shim(cocreate, {clsid, 0, 1, iid, ppv}), 0x80040154u); // REGDB_E_CLASSNOTREG
}

// ---------------------------------------------------------------------------
// DirectShow multimedia streaming: the reading side of the API a game uses to
// pull decoded audio out of a music file and feed its own DirectSound buffer.
// IAMMultiMediaStream opens the file, IAudioMediaStream describes and samples
// it, AMAudioData wraps the guest's buffer and IAudioStreamSample::Update fills
// that buffer with the next stretch of PCM, signalling the event the caller
// gave it. The fixture is a short MP3 tone; MS_S_ENDOFSTREAM ends it and Seek
// rewinds it.
// ---------------------------------------------------------------------------
static void test_dshow_audio_stream() {
    static const uint8_t clsid_mmstream[16] = {0xe5, 0x7c, 0xc4, 0x49, 0xa4, 0x9b, 0xd0, 0x11,
                                               0x82, 0x12, 0x00, 0xc0, 0x4f, 0xc3, 0x2c, 0x45};
    static const uint8_t iid_ammmstream[16] = {0x5c, 0x59, 0xbe, 0xbe, 0x6f, 0x9a, 0xd0, 0x11,
                                               0x8f, 0xde, 0x00, 0xc0, 0x4f, 0xd9, 0x18, 0x9d};
    static const uint8_t mspid_audio[16] = {0x6b, 0xf5, 0x5f, 0xa3, 0xda, 0x9f, 0xd0, 0x11,
                                            0x8f, 0xdf, 0x00, 0xc0, 0x4f, 0xd9, 0x18, 0x9d};
    static const uint8_t iid_audiomediastream[16] = {0x60, 0x75, 0x53, 0xf7, 0xbe, 0xa3,
                                                     0xd0, 0x11, 0x82, 0x12, 0x00, 0xc0,
                                                     0x4f, 0xc3, 0x2c, 0x45};
    static const uint8_t clsid_audiodata[16] = {0x80, 0x85, 0x46, 0xf2, 0x8a, 0xaf, 0xd0, 0x11,
                                                0x82, 0x12, 0x00, 0xc0, 0x4f, 0xc3, 0x2c, 0x45};
    static const uint8_t iid_audiodata[16] = {0xc0, 0x19, 0xc7, 0x54, 0x60, 0xaf, 0xd0, 0x11,
                                              0x82, 0x12, 0x00, 0xc0, 0x4f, 0xc3, 0x2c, 0x45};
    const uint32_t S_OK_ = 0, MS_S_ENDOFSTREAM = 0x40003u;

    // The fixture on disk, under a game directory of its own.
    char dir[512];
    snprintf(dir, sizeof dir, "%s/recomp-dshow-XXXXXX", os_temp_dir());
    CHECK(os_mkdtemp(dir) == 0);
    std::string file = std::string(dir) + "/tone.mp3";
    FILE *f = fopen(file.c_str(), "wb");
    CHECK(f != nullptr);
    if (!f)
        return;
    fwrite(kToneMp3, 1, sizeof kToneMp3, f);
    fclose(f);
    win32_init(dir);

    uint32_t clsid = sc(0x1e00), iid = sc(0x1e10), ppv = sc(0x1e20), mspid = sc(0x1e30),
             pstream = sc(0x1e40), pams = sc(0x1e50), wfx = sc(0x1e60), pdata = sc(0x1e80),
             psample = sc(0x1e90), actual = sc(0x1ea0), wpath = sc(0x1f00);
    uint32_t buf = heap_alloc(4096, true, 16);
    CHECK(buf != 0);
    memcpy(g_mem + mspid, mspid_audio, 16);
    const char *name = "tone.mp3";
    for (size_t i = 0; i <= strlen(name); ++i)
        wr16(wpath + 2 * (uint32_t)i, (uint16_t)name[i]);

    uint32_t cocreate = tramp("ole32.dll", "CoCreateInstance");
    memcpy(g_mem + clsid, clsid_mmstream, 16);
    memcpy(g_mem + iid, iid_ammmstream, 16);
    CHECK_EQ(call_shim(cocreate, {clsid, 0, 1, iid, ppv}), S_OK_);
    uint32_t mm = rd32(ppv);
    CHECK(mm != 0);
    if (!mm)
        return;
    CHECK_EQ(call_method(mm, 12, {0, 0, 0}), S_OK_);        // Initialize(STREAMTYPE_READ, 0, NULL)
    CHECK_EQ(call_method(mm, 15, {0, mspid, 0, 0}), S_OK_); // AddMediaStream(NULL, audio, 0, NULL)
    CHECK_EQ(call_method(mm, 16, {wpath, 8}), S_OK_);       // OpenFile(L"tone.mp3", AMMSF_RUN)
    CHECK_EQ(call_method(mm, 4, {mspid, pstream}), S_OK_);  // GetMediaStream
    uint32_t ms = rd32(pstream);
    CHECK(ms != 0);
    memcpy(g_mem + iid, iid_audiomediastream, 16);
    CHECK_EQ(call_method(ms, 0, {iid, pams}), S_OK_); // QueryInterface(IAudioMediaStream)
    uint32_t ams = rd32(pams);
    CHECK(ams != 0);
    if (!ms || !ams)
        return;
    // The format is the file's: 44.1 kHz stereo, 16-bit PCM.
    CHECK_EQ(call_method(ams, 9, {wfx}), S_OK_); // GetFormat
    CHECK_EQ(rd16(wfx + WFX_OFF_wFormatTag), WAVE_FORMAT_PCM);
    CHECK_EQ(rd16(wfx + WFX_OFF_nChannels), 2u);
    CHECK_EQ(rd32(wfx + WFX_OFF_nSamplesPerSec), 44100u);
    CHECK_EQ(rd16(wfx + WFX_OFF_wBitsPerSample), 16u);
    CHECK_EQ(rd16(wfx + WFX_OFF_nBlockAlign), 4u);
    CHECK_EQ(rd32(wfx + WFX_OFF_nAvgBytesPerSec), 176400u);

    memcpy(g_mem + clsid, clsid_audiodata, 16);
    memcpy(g_mem + iid, iid_audiodata, 16);
    CHECK_EQ(call_shim(cocreate, {clsid, 0, 1, iid, pdata}), S_OK_);
    uint32_t ad = rd32(pdata);
    CHECK(ad != 0);
    if (!ad)
        return;
    CHECK_EQ(call_method(ad, 3, {4096, buf, 0}), S_OK_);     // SetBuffer
    CHECK_EQ(call_method(ad, 7, {wfx}), S_OK_);              // SetFormat
    CHECK_EQ(call_method(ams, 11, {ad, 0, psample}), S_OK_); // CreateSample
    uint32_t sp = rd32(psample);
    CHECK(sp != 0);
    if (!sp)
        return;

    // One Update fills the whole buffer with sound and signals the event.
    uint32_t ev = call_shim(tramp("KERNEL32.dll", "CreateEventA"), {0, 0, 0, 0});
    CHECK(ev != 0);
    CHECK_EQ(call_method(sp, 6, {0, ev, 0, 0}), S_OK_); // Update(0, hEvent, NULL, 0)
    wr32(actual, 0);
    CHECK_EQ(call_method(ad, 4, {0, 0, actual}), S_OK_); // GetInfo(NULL, NULL, &actual)
    CHECK_EQ(rd32(actual), 4096u);
    CHECK_EQ(call_shim(tramp("KERNEL32.dll", "WaitForSingleObject"), {ev, 0}), 0u); // signalled
    CHECK_EQ(call_method(sp, 7, {6, 0xffffffffu}), S_OK_); // CompletionStatus(WAIT|ABORT, INFINITE)

    // The tone is 6912 stereo frames: 27648 bytes, then the end of the stream.
    // The encoder's lead-in is silent, so the sound is looked for over the
    // whole of it.
    auto loud_in = [&](uint32_t bytes) {
        for (uint32_t i = 0; i + 1 < bytes; i += 2)
            if ((int16_t)rd16(buf + i) != 0)
                return true;
        return false;
    };
    bool loud = loud_in(4096);
    uint32_t total = 4096;
    for (int i = 0; i < 32; ++i) {
        uint32_t hr = call_method(sp, 6, {0, 0, 0, 0});
        if (hr == MS_S_ENDOFSTREAM)
            break;
        CHECK_EQ(hr, S_OK_);
        wr32(actual, 0);
        call_method(ad, 4, {0, 0, actual});
        total += rd32(actual);
        loud = loud || loud_in(rd32(actual));
    }
    CHECK_EQ(total, 27648u);
    CHECK(loud);
    CHECK_EQ(call_method(sp, 6, {0, 0, 0, 0}), MS_S_ENDOFSTREAM); // and it stays ended
    // Seek(0) rewinds: the next Update produces sound again.
    CHECK_EQ(call_method(mm, 10, {0, 0}), S_OK_); // Seek(STREAM_TIME 0)
    CHECK_EQ(call_method(sp, 6, {0, 0, 0, 0}), S_OK_);
    wr32(actual, 0);
    call_method(ad, 4, {0, 0, actual});
    CHECK_EQ(rd32(actual), 4096u);

    // Everything releases; nothing is left behind.
    uint32_t live = com_live_count();
    call_method(sp, 2);
    call_method(ad, 2);
    call_method(ams, 2);
    call_method(ms, 2);
    call_method(mm, 2);
    CHECK_EQ(com_live_count(), live - 4);
    // A file that is not there is refused, and the object is still usable to release.
    memcpy(g_mem + clsid, clsid_mmstream, 16);
    memcpy(g_mem + iid, iid_ammmstream, 16);
    CHECK_EQ(call_shim(cocreate, {clsid, 0, 1, iid, ppv}), S_OK_);
    mm = rd32(ppv);
    const char *missing = "absent.mp3";
    for (size_t i = 0; i <= strlen(missing); ++i)
        wr16(wpath + 2 * (uint32_t)i, (uint16_t)missing[i]);
    CHECK(call_method(mm, 16, {wpath, 8}) != S_OK_);
    call_method(mm, 2);
    remove(file.c_str());
    os_rmdir(dir);
}

// The other way a game plays a stream: it asks the multimedia stream for its
// filter graph and drives that through IMediaControl (Run/Stop), watches
// IMediaEventEx for EC_COMPLETE, seeks with IMediaSeeking and sets the level
// with IBasicAudio. Nothing pulls samples; the kit decodes and streams the
// PCM to a host audio channel itself, refilled from the frame pump, and posts
// EC_COMPLETE when the last of it has played.
static void test_dshow_graph_playback() {
    static const uint8_t clsid_mmstream[16] = {0xe5, 0x7c, 0xc4, 0x49, 0xa4, 0x9b, 0xd0, 0x11,
                                               0x82, 0x12, 0x00, 0xc0, 0x4f, 0xc3, 0x2c, 0x45};
    static const uint8_t iid_ammmstream[16] = {0x5c, 0x59, 0xbe, 0xbe, 0x6f, 0x9a, 0xd0, 0x11,
                                               0x8f, 0xde, 0x00, 0xc0, 0x4f, 0xd9, 0x18, 0x9d};
    static const uint8_t mspid_audio[16] = {0x6b, 0xf5, 0x5f, 0xa3, 0xda, 0x9f, 0xd0, 0x11,
                                            0x8f, 0xdf, 0x00, 0xc0, 0x4f, 0xd9, 0x18, 0x9d};
    // {56a868xx-0ad4-11ce-b03a-0020af0ba770}: control b1, event ex c0, basic audio b3,
    // position b2; IMediaSeeking {36b73880-c2c8-11cf-8b46-00805f6cef60}.
    auto quartz = [](uint8_t lo) {
        std::array<uint8_t, 16> g = {lo,   0x68, 0xa8, 0x56, 0xd4, 0x0a, 0xce, 0x11,
                                     0xb0, 0x3a, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70};
        return g;
    };
    static const uint8_t iid_seeking[16] = {0x80, 0x38, 0xb7, 0x36, 0xc8, 0xc2, 0xcf, 0x11,
                                            0x8b, 0x46, 0x00, 0x80, 0x5f, 0x6c, 0xef, 0x60};
    const uint32_t S_OK_ = 0, E_ABORT_ = 0x80004004u, WAIT_TIMEOUT_ = 0x102u, EC_COMPLETE = 1;

    char dir[512];
    snprintf(dir, sizeof dir, "%s/recomp-dshow-XXXXXX", os_temp_dir());
    CHECK(os_mkdtemp(dir) == 0);
    std::string file = std::string(dir) + "/tone.mp3";
    FILE *f = fopen(file.c_str(), "wb");
    CHECK(f != nullptr);
    if (!f)
        return;
    fwrite(kToneMp3, 1, sizeof kToneMp3, f);
    fclose(f);
    win32_init(dir);
    g_plays.clear();
    g_queues.clear();
    g_stops.clear();
    g_queue_enabled = true;
    g_queued_bytes = 0;
    g_voice_remaining = 0;

    uint32_t clsid = sc(0x1e00), iid = sc(0x1e10), ppv = sc(0x1e20), mspid = sc(0x1e30),
             pgraph = sc(0x1e40), pctl = sc(0x1e50), pev = sc(0x1e60), pseek = sc(0x1e70),
             paud = sc(0x1e80), ppos = sc(0x1e90), out = sc(0x1ea0), zero64 = sc(0x1eb0),
             wpath = sc(0x1f00);
    memcpy(g_mem + mspid, mspid_audio, 16);
    wr32(zero64, 0);
    wr32(zero64 + 4, 0);
    const char *name = "tone.mp3";
    for (size_t i = 0; i <= strlen(name); ++i)
        wr16(wpath + 2 * (uint32_t)i, (uint16_t)name[i]);

    uint32_t cocreate = tramp("ole32.dll", "CoCreateInstance");
    memcpy(g_mem + clsid, clsid_mmstream, 16);
    memcpy(g_mem + iid, iid_ammmstream, 16);
    CHECK_EQ(call_shim(cocreate, {clsid, 0, 1, iid, ppv}), S_OK_);
    uint32_t mm = rd32(ppv);
    CHECK(mm != 0);
    if (!mm)
        return;
    CHECK_EQ(call_method(mm, 12, {0, 0, 0}), S_OK_); // Initialize
    CHECK_EQ(call_method(mm, 15, {0, mspid, 1, 0}),
             S_OK_);                                  // AddMediaStream(..., ADDDEFAULTRENDERER)
    CHECK_EQ(call_method(mm, 16, {wpath, 0}), S_OK_); // OpenFile
    CHECK_EQ(call_method(mm, 13, {pgraph}), S_OK_);   // GetFilterGraph
    uint32_t graph = rd32(pgraph);
    CHECK(graph != 0);
    if (!graph)
        return;
    memcpy(g_mem + iid, quartz(0xb1).data(), 16);
    CHECK_EQ(call_method(graph, 0, {iid, pctl}), S_OK_);
    memcpy(g_mem + iid, quartz(0xc0).data(), 16);
    CHECK_EQ(call_method(graph, 0, {iid, pev}), S_OK_);
    memcpy(g_mem + iid, iid_seeking, 16);
    CHECK_EQ(call_method(graph, 0, {iid, pseek}), S_OK_);
    memcpy(g_mem + iid, quartz(0xb3).data(), 16);
    CHECK_EQ(call_method(graph, 0, {iid, paud}), S_OK_);
    memcpy(g_mem + iid, quartz(0xb2).data(), 16);
    CHECK_EQ(call_method(graph, 0, {iid, ppos}), S_OK_);
    uint32_t ctl = rd32(pctl), ev = rd32(pev), seek = rd32(pseek), aud = rd32(paud),
             pos = rd32(ppos);
    CHECK(ctl && ev && seek && aud && pos);
    if (!ctl || !ev || !seek || !aud || !pos)
        return;

    // The graph has no filters to list, and says so the way a graph does: an
    // enumerator whose Next fetches nothing. A game that walks the filters for
    // its log then walks nothing instead of reporting a failed enumeration.
    uint32_t penum = sc(0x1ed0), pfilter = sc(0x1ed4), pfetched = sc(0x1ed8);
    CHECK_EQ(call_method(graph, 5, {penum}), S_OK_); // IFilterGraph::EnumFilters
    uint32_t en = rd32(penum);
    CHECK(en != 0);
    if (en) {
        wr32(pfetched, 99);
        CHECK_EQ(call_method(en, 3, {1, pfilter, pfetched}), 1u); // Next -> S_FALSE
        CHECK_EQ(rd32(pfetched), 0u);
        CHECK_EQ(call_method(en, 5, {}), S_OK_); // Reset
        call_method(en, 2);                      // Release
    }

    // The completion event: a real handle, not yet signalled.
    CHECK_EQ(call_method(ev, 14, {0}), S_OK_);  // SetNotifyFlags(0)
    CHECK_EQ(call_method(ev, 7, {out}), S_OK_); // GetEventHandle
    uint32_t h = rd32(out);
    CHECK(h != 0);
    uint32_t wait = tramp("KERNEL32.dll", "WaitForSingleObject");
    CHECK_EQ(call_shim(wait, {h, 0}), WAIT_TIMEOUT_);
    // Duration both ways: 6912 frames at 44.1 kHz is 156.7 ms.
    CHECK_EQ(call_method(seek, 10, {out}), S_OK_); // IMediaSeeking::GetDuration
    CHECK_EQ(rd32(out), 1567346u);
    CHECK_EQ(rd32(out + 4), 0u);
    CHECK_EQ(call_method(pos, 7, {out}), S_OK_); // IMediaPosition::get_Duration (seconds)
    double seconds = 0;
    memcpy(&seconds, g_mem + out, 8);
    CHECK(seconds > 0.156 && seconds < 0.158);
    CHECK_EQ(call_method(aud, 7, {(uint32_t)-600}), S_OK_); // put_Volume(-6 dB)
    CHECK_EQ(call_method(aud, 8, {out}), S_OK_);            // get_Volume
    CHECK_EQ(rd32(out), (uint32_t)-600);
    CHECK_EQ(call_method(seek, 14, {zero64, 1, 0, 0}), S_OK_); // SetPositions(0, Absolute, NULL, 0)
    CHECK_EQ(call_method(ctl, 10, {0, out}), S_OK_);           // GetState
    CHECK_EQ(rd32(out), 0u);                                   // State_Stopped

    // Run: one host play with the file's format and the level asked for, not
    // looping, then the pump keeps the channel fed until the file is spent.
    CHECK_EQ(call_method(ctl, 7, {}), S_OK_); // Run
    CHECK_EQ(g_plays.size(), 1u);
    if (g_plays.size() == 1) {
        CHECK_EQ(g_plays[0].rate, 44100);
        CHECK_EQ(g_plays[0].channels, 2);
        CHECK_EQ(g_plays[0].bits, 16);
        CHECK_EQ(g_plays[0].loop, 0);
        CHECK_EQ(g_plays[0].volume, -600);
        CHECK(g_plays[0].bytes > 0 && g_plays[0].bytes % 4 == 0);
    }
    CHECK_EQ(call_method(ctl, 10, {0, out}), S_OK_);
    CHECK_EQ(rd32(out), 2u); // State_Running
    uint32_t total = g_plays.empty() ? 0 : g_plays[0].bytes;
    // The channel becomes a stream paced against bytes played: the pump refills
    // while less than its lead is outstanding, and the test plays out everything
    // submitted so far between ticks. Nothing here reads the voice's own count,
    // which the host zeroes whenever its playing flag is down.
    CHECK(g_ch_streaming);
    for (int i = 0; i < 64 && !g_plays.empty(); ++i) {
        size_t before = g_queues.size();
        dshow_frame_pump(&g_cpu);
        for (size_t k = before; k < g_queues.size(); ++k)
            total += g_queues[k].bytes;
        g_stream_played = total; // everything submitted so far has now played
    }
    CHECK_EQ(total, 27648u);
    dshow_frame_pump(&g_cpu); // notices the played-out stream after the last chunk
    // The completion is announced: the event is set and GetEvent hands out
    // EC_COMPLETE once, then E_ABORT with the event reset.
    CHECK_EQ(call_shim(wait, {h, 0}), 0u);
    uint32_t code = sc(0x1ec0), p1 = sc(0x1ec4), p2 = sc(0x1ec8);
    CHECK_EQ(call_method(ev, 8, {code, p1, p2, 0}), S_OK_); // GetEvent
    CHECK_EQ(rd32(code), EC_COMPLETE);
    CHECK_EQ(call_method(ev, 12, {rd32(code), rd32(p1), rd32(p2)}), S_OK_); // FreeEventParams
    CHECK_EQ(call_method(ev, 8, {code, p1, p2, 0}), E_ABORT_);
    CHECK_EQ(call_shim(wait, {h, 0}), WAIT_TIMEOUT_);
    CHECK_EQ(call_method(ctl, 10, {0, out}), S_OK_);
    CHECK_EQ(rd32(out), 0u); // stopped again
    // The position reads as the end; a seek back to the start and a second
    // Run play again from the top.
    CHECK_EQ(call_method(seek, 12, {out}), S_OK_); // GetCurrentPosition
    CHECK_EQ(rd32(out), 1567346u);
    CHECK_EQ(call_method(seek, 14, {zero64, 1, 0, 0}), S_OK_);
    CHECK_EQ(call_method(seek, 12, {out}), S_OK_);
    CHECK_EQ(rd32(out), 0u);
    CHECK_EQ(call_method(ctl, 7, {}), S_OK_);
    CHECK_EQ(g_plays.size(), 2u);
    // A chunk the host refuses (its channel is not ready for an append yet)
    // is held and offered again on the next tick, not turned into a fresh
    // play that would drop what is already sounding.
    size_t queued_before = g_queues.size();
    g_queue_retired = true;
    dshow_frame_pump(&g_cpu);
    CHECK_EQ(g_queues.size(), queued_before);
    CHECK_EQ(g_plays.size(), 2u);
    g_queue_retired = false;
    dshow_frame_pump(&g_cpu);
    CHECK(g_queues.size() > queued_before);
    CHECK_EQ(g_plays.size(), 2u);
    // Stop ends the sound on the host and keeps the position for a later Run.
    CHECK_EQ(call_method(ctl, 9, {}), S_OK_); // Stop
    CHECK(!g_stops.empty());
    CHECK_EQ(call_method(ctl, 10, {0, out}), S_OK_);
    CHECK_EQ(rd32(out), 0u);

    uint32_t live = com_live_count();
    call_method(pos, 2);
    call_method(aud, 2);
    call_method(seek, 2);
    call_method(ev, 2);
    call_method(ctl, 2);
    call_method(graph, 2);
    call_method(mm, 2);
    CHECK_EQ(com_live_count(), live - 3); // the stream, its media stream and the graph
    g_queue_enabled = false;
    remove(file.c_str());
    os_rmdir(dir);
}

static void test_gdi_primary_blit() {
    cpu_reset();
    reset_ddraw_for_test();
    call_shim(tramp("DDRAW.dll", "DirectDrawCreate"), {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));
    call_method(dd, DD_SetDisplayMode, {640, 480, 16});
    uint32_t desc = sc(0x100);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_PRIMARYSURFACE);
    call_method(dd, DD_CreateSurface, {desc, sc(4), 0});
    uint32_t primary = rd32(sc(4));
    ComObj *o = com_this(primary);
    CHECK(o != nullptr);
    if (!o)
        return;
    uint32_t dc = call_shim(tramp("USER32.dll", "GetDC"), {0});
    uint32_t mem = call_shim(tramp("GDI32.dll", "CreateCompatibleDC"), {dc});
    uint32_t bmi = sc(0x300);
    gm_zero(bmi, 40);
    wr32(bmi, 40);
    wr32(bmi + 4, 2);
    wr32(bmi + 8, uint32_t(-2));
    wr16(bmi + 12, 1);
    wr16(bmi + 14, 32);
    uint32_t dib =
        call_shim(tramp("GDI32.dll", "CreateDIBSection"), {mem, bmi, 0, sc(0x400), 0, 0});
    uint32_t bits = rd32(sc(0x400));
    for (int i = 0; i < 4; ++i)
        wr32(bits + 4 * i, 0xffff0000);
    call_shim(tramp("GDI32.dll", "SelectObject"), {mem, dib});
    uint32_t empty = call_shim(tramp("GDI32.dll", "CreateCompatibleDC"), {dc});
    CHECK_EQ(call_shim(tramp("GDI32.dll", "BitBlt"), {empty, 0, 0, 2, 2, mem, 0, 0, 0xcc0020}), 0u);
    CHECK_EQ(rd16(o->pixels), 0u);
    call_shim(tramp("GDI32.dll", "DeleteDC"), {empty});
    size_t before = g_presents.size();
    CHECK_EQ(call_shim(tramp("GDI32.dll", "BitBlt"), {dc, 3, 4, 2, 2, mem, 0, 0, 0xcc0020}), 1u);
    CHECK_EQ(rd16(o->pixels + 4 * o->pitch + 3 * 2), 0xf800u);
    CHECK(g_presents.size() > before);
    CHECK_EQ(
        call_shim(tramp("GDI32.dll", "StretchBlt"), {dc, 8, 8, 4, 4, mem, 0, 0, 2, 2, 0xcc0020}),
        1u);
    CHECK_EQ(rd16(o->pixels + 11 * o->pitch + 11 * 2), 0xf800u);
    call_shim(tramp("GDI32.dll", "SetWindowOrgEx"), {dc, 2, 3, 0});
    CHECK_EQ(call_shim(tramp("GDI32.dll", "BitBlt"), {dc, 32, 32, 2, 2, mem, 0, 0, 0xcc0020}), 1u);
    CHECK_EQ(rd16(o->pixels + 29 * o->pitch + 30 * 2), 0xf800u);
    call_shim(tramp("GDI32.dll", "DeleteDC"), {mem});
    call_shim(tramp("GDI32.dll", "DeleteObject"), {dib});
    call_shim(tramp("USER32.dll", "ReleaseDC"), {0, dc});
    call_method(primary, 2);
    call_method(dd, 2);
}

static void test_getdc_releasedc() {
    rec_reset();
    uint32_t rt = make_render_target_for_test(64, 64, 8);
    ComObj *o = com_this(rt);
    CHECK(o != nullptr);
    if (!o)
        return;
    reset_ddraw_for_test();
    ddraw_reset_access_counts();
    host_d3d_mark_dirty(o->id, ddraw_surface_generation(o->id), {10, 11, 11, 12});
    CHECK_EQ(call_method(rt, 17, {sc(0x1c00)}), DD_OK); // GetDC
    uint32_t hdc = rd32(sc(0x1c00));
    CHECK_EQ(call_shim(tramp("GDI32.dll", "SetPixel"), {hdc, 10, 11, 0x4d4d4d}), 0x4d4d4du);
    CHECK_EQ(rd8(o->pixels + 11 * o->pitch + 10), 77u);
    CHECK_EQ(call_method(rt, 26, {rd32(sc(0x1c00))}), DD_OK); // ReleaseDC
    HostAccessCounts a{};
    host_access_counts(&a);
    CHECK_EQ(a.getdc, 1u);
    CHECK_EQ(host_readback_reason_count(HOST_READ_GETDC), 1u);
    CHECK_EQ(host_frame_record_count(host_frame_current()), 1u);
    const HostBlitRecord *r = host_frame_record(host_frame_current(), 0);
    CHECK(r != nullptr);
    if (r) {
        CHECK_EQ(r->src.surface, HOST_SRC_CPU);
        CHECK_EQ(r->cpu_pixels[0], 77);
    }
}

static void test_palette_versions() {
    rec_reset();
    uint32_t rt = make_render_target_for_test(64, 64, 8);
    uint32_t sp = make_offscreen_for_test(8, 8, 8);
    uint32_t primary = make_primary_chain_for_test();
    CHECK(rt != 0);
    CHECK(sp != 0);
    CHECK(primary != 0);
    if (!rt || !sp || !primary)
        return;
    fill_for_test(sp, 1);

    uint32_t entries = sc(0x1000);
    gm_zero(entries, 256 * 4);
    wr32(entries + 1 * 4, 0x00000042u); // R,G,B,flags: R = 0x42
    uint32_t dd = rec_dd();
    CHECK_EQ(call_method(dd, DD_CreatePalette,
                         {DDPCAPS_8BIT | DDPCAPS_ALLOW256, entries, sc(0x1400), 0}),
             DD_OK);
    uint32_t pal = rd32(sc(0x1400));
    CHECK(pal != 0);
    if (!pal)
        return;
    // Attached to the DESTINATION this test records into. A record is drawn in
    // the colours of its own destination chain, not of whatever surface the
    // shim happens to be presenting.
    CHECK_EQ(call_method(rt, S_SetPalette, {pal}), DD_OK);
    CHECK_EQ(call_method(primary, S_SetPalette, {pal}), DD_OK);

    reset_ddraw_for_test();
    // Named before the write. The palette change below does not seal by
    // itself, but it is a change to this frame, so the pump that follows any
    // of this would end it and "the current frame" would be a different one.
    HostFrameHandle f = host_frame_current();
    blt_for_test(rt, sp, 0, 0, 8, 8, 0);
    const HostBlitRecord *r = host_frame_record(f, 0);
    CHECK(r != nullptr);
    if (!r)
        return;
    uint32_t v = r->palette_version;
    CHECK_EQ(host_palette_version(), v);

    // The guest repaints the palette. The version the record names must still
    // answer with the colours that were live when it was recorded.
    wr32(entries + 1 * 4, 0x00000099u);
    CHECK_EQ(call_method(pal, P_SetEntries, {0, 0, 256, entries}), DD_OK);
    CHECK(host_palette_version() != v);

    const uint8_t *old_rgb = host_palette_lease(v);
    CHECK(old_rgb != nullptr);
    if (old_rgb)
        CHECK_EQ(old_rgb[3], 0x42); // entry 1, red
    const uint8_t *new_rgb = host_palette_lease(host_palette_version());
    CHECK(new_rgb != nullptr);
    if (new_rgb)
        CHECK_EQ(new_rgb[3], 0x99);
    host_palette_release(v);
    host_palette_release(host_palette_version());

    // A version nobody holds is dropped, which is what keeps a level of
    // palette animation from accumulating a snapshot per frame.
    host_frame_release(f);
    CHECK(host_palette_lease(v) == nullptr);

    // And a lease READS. It never decides what the current version is, which
    // it would do by resolving identity: with the live version belonging to an
    // offscreen surface's own palette and the primary on another, re-resolving
    // inside the lease would bump the counter and prune the very version
    // host_palette_version had just reported.
    reset_ddraw_for_test();
    uint32_t own = make_offscreen_for_test(8, 8, 8);
    CHECK(own != 0);
    if (!own)
        return;
    uint32_t entries3 = sc(0x1500); // and its out pointer is clear of it
    gm_zero(entries3, 256 * 4);
    wr32(entries3 + 1 * 4, 0x000000abu);
    CHECK_EQ(call_method(dd, DD_CreatePalette,
                         {DDPCAPS_8BIT | DDPCAPS_ALLOW256, entries3, sc(0x1904), 0}),
             DD_OK);
    uint32_t otherpal = rd32(sc(0x1904));
    CHECK(otherpal != 0);
    if (!otherpal)
        return;
    CHECK_EQ(call_method(own, S_SetPalette, {otherpal}), DD_OK);
    blt_for_test(own, sp, 0, 0, 8, 8, 0); // a record in own's colours
    HostFrameHandle g = host_frame_current();
    const HostBlitRecord *rg = host_frame_record(g, 0);
    CHECK(rg != nullptr);
    if (!rg)
        return;
    // The premise: the live version belongs to the offscreen surface's own
    // palette, which is not the one the primary is on.
    uint32_t live = host_palette_version();
    CHECK_EQ(rg->palette_version, live);
    host_frame_release(g); // nothing holds it now
    const uint8_t *live_rgb = host_palette_lease(live);
    CHECK(live_rgb != nullptr); // the version just reported
    if (live_rgb)
        CHECK_EQ(live_rgb[3], 0xab); // and in ITS colours

    // Releasing the last hold on the LIVE version must not throw it away. Its
    // identity was fixed when it was made; dropping it lets the next lease of
    // the same number resolve it against whatever is on screen by then, and
    // one version number would report two different palettes.
    if (live_rgb)
        host_palette_release(live);
    CHECK_EQ(host_palette_version(), live);
    const uint8_t *again = host_palette_lease(live);
    CHECK(again != nullptr);
    if (again)
        CHECK_EQ(again[3], 0xab);
    if (again)
        host_palette_release(live);
}

// Content revisions and backing-storage generations are different things: a
// Flip does not change what a surface contains, it changes WHERE the contents
// live, and a record that carried a revision where the generation belongs
// would claim the picture changed when only the address did.
static void test_storage_generations() {
    rec_reset();
    uint32_t primary = make_primary_chain_for_test();
    uint32_t rt = make_render_target_for_test(64, 64, 8);
    uint32_t sp = make_offscreen_for_test(8, 8, 8);
    CHECK(primary != 0);
    CHECK(rt != 0);
    CHECK(sp != 0);
    if (!primary || !rt || !sp)
        return;
    ComObj *po = rec_obj(primary);
    CHECK(po != nullptr);
    if (!po)
        return;

    // The back buffer holds something else, so a swap that lost the leased
    // contents would be visible as the wrong pixels rather than as nothing.
    uint32_t caps = sc(0x1408);
    wr32(caps, DDSCAPS_BACKBUFFER);
    CHECK_EQ(call_method(primary, S_GetAttachedSurface, {caps, sc(0x140c)}), DD_OK);
    uint32_t back = rd32(sc(0x140c));
    CHECK(back != 0);
    if (!back)
        return;
    fill_for_test(back, 9);
    fill_for_test(sp, 5);
    blt_for_test(primary, sp, 0, 0, 8, 8, 0); // the primary now holds 5s
    reset_ddraw_for_test();

    // A frame reads the primary, which leases the primary's own contents.
    HostFrameHandle f = host_frame_current();
    blt_for_test(rt, primary, 0, 0, 8, 8, 0);
    const HostBlitRecord *r = host_frame_record(f, 0);
    CHECK(r != nullptr);
    if (!r)
        return;
    HostSurfaceKey k = r->src;
    CHECK_EQ(k.surface, po->id);
    uint32_t gen = host_surface_generation_for_test(po->id);

    // A Flip moves the generation because the memory changed address, AND the
    // revision because the contents changed with it: afterwards the primary
    // holds what the back buffer held. A revision that stood still here would
    // let a record made after the swap key to the revision whose bytes the
    // retained store is holding from before it.
    uint32_t rev = host_surface_revision_for_test(po->id);
    call_method(primary, S_Flip, {0, DDFLIP_WAIT});
    CHECK(host_surface_generation_for_test(po->id) != gen);
    CHECK(host_surface_revision_for_test(po->id) != rev);

    // And the frame still refers to the pixels it recorded, leased after the
    // swap that moved them. Preserving them is what makes this answerable:
    // without it the lease reads the back buffer's 9s, or nothing at all.
    HostPixels px;
    CHECK_EQ(host_revision_lease(k, &px), 0);
    CHECK(px.data != nullptr);
    if (px.data)
        CHECK_EQ(px.data[0], 5);
    host_revision_release(k);

    // And a record made AFTER the swap names a different revision, so it
    // cannot be handed the bytes the retained store is holding from before it.
    // The primary now holds the back buffer's 9s.
    HostFrameHandle f2 = host_frame_current();
    blt_for_test(rt, primary, 0, 0, 8, 8, 0);
    const HostBlitRecord *r2 = host_frame_record(f2, host_frame_record_count(f2) - 1);
    CHECK(r2 != nullptr);
    if (r2) {
        CHECK(r2->src.revision != k.revision);
        HostPixels px2;
        CHECK_EQ(host_revision_lease(r2->src, &px2), 0);
        CHECK(px2.data != nullptr);
        if (px2.data)
            CHECK_EQ(px2.data[0], 9);
        host_revision_release(r2->src);
    }
    host_frame_release(f);
    host_frame_release(f2);
}

// ===========================================================================
// Draw snapshots (DISP-T3).
//
// A draw is composited long after DrawPrimitive returned, by which time the
// guest has reused its vertex buffer and moved its matrices on. Everything the
// renderer needs is therefore COPIED into the frame's arena at submission, and
// these tests are about that copy being real: the assertions all change the
// guest's own memory after the draw and demand the old values back.
// ===========================================================================
static uint32_t g_t3_dd = 0, g_t3_d3d = 0, g_t3_dev = 0, g_t3_vp = 0, g_t3_target = 0;

// A device with a viewport, built through the real vtables the way the game
// builds one.
static bool make_device_for_test() {
    cpu_reset();
    reset_ddraw_for_test();
    g_t3_dd = g_t3_d3d = g_t3_dev = g_t3_vp = g_t3_target = 0;

    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    g_t3_dd = rd32(sc(0));
    if (!g_t3_dd)
        return false;
    call_method(g_t3_dd, DD_SetDisplayMode, {640, 480, 16});

    uint32_t iid = sc(0x40);
    uint8_t d3d2[16] = {0xC1, 0x1E, 0xAE, 0x6A, 0x2A, 0x66, 0xD0, 0x11,
                        0x88, 0x9D, 0x00, 0xAA, 0x00, 0xBB, 0xB7, 0x6A};
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, d3d2[i]);
    if (call_method(g_t3_dd, DD_QueryInterface, {iid, sc(0x60)}) != S_OK)
        return false;
    g_t3_d3d = rd32(sc(0x60));

    uint32_t desc = sc(0x400);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_OFFSCREENPLAIN | DDSCAPS_3DDEVICE);
    wr32(desc + DDSD_OFF_dwWidth, 640);
    wr32(desc + DDSD_OFF_dwHeight, 480);
    call_method(g_t3_dd, DD_CreateSurface, {desc, sc(8), 0});
    g_t3_target = rd32(sc(8));
    if (!g_t3_target)
        return false;

    uint8_t hal[16] = {0xE0, 0x3D, 0xE6, 0x84, 0xAA, 0x46, 0xCF, 0x11,
                       0x81, 0x6F, 0x00, 0x00, 0xC0, 0x20, 0x15, 0x6E};
    uint32_t guid = sc(0x500);
    for (int i = 0; i < 16; ++i)
        wr8(guid + (uint32_t)i, hal[i]);
    if (call_method(g_t3_d3d, D3D_CreateDevice, {guid, g_t3_target, sc(12)}) != D3D_OK_)
        return false;
    g_t3_dev = rd32(sc(12));
    if (!g_t3_dev)
        return false;

    if (call_method(g_t3_d3d, D3D_CreateViewport, {sc(16), 0}) != D3D_OK_)
        return false;
    g_t3_vp = rd32(sc(16));
    if (!g_t3_vp)
        return false;
    call_method(g_t3_dev, DEV_AddViewport, {g_t3_vp});
    uint32_t vpdata = sc(0x1800);
    gm_zero(vpdata, D3DVIEWPORT2_SIZE);
    wr32(vpdata + D3DVP_OFF_dwSize, D3DVIEWPORT2_SIZE);
    wr32(vpdata + D3DVP_OFF_dwWidth, 640);
    wr32(vpdata + D3DVP_OFF_dwHeight, 480);
    wrf32(vpdata + D3DVP_OFF_dvMinZ, 0.0f);
    wrf32(vpdata + D3DVP_OFF_dvMaxZ, 1.0f);
    call_method(g_t3_vp, VP_SetViewport2, {vpdata});
    call_method(g_t3_dev, DEV_SetCurrentViewport, {g_t3_vp});
    return true;
}

// `n` D3DVT_TLVERTEX vertices whose x is all the same float, so a test can
// say which generation of the buffer it is looking at by reading one number.
static void write_vertices_for_test(uint32_t addr, uint32_t n, float x) {
    for (uint32_t v = 0; v < n; ++v) {
        uint32_t b = addr + v * 32;
        wrf32(b + 0, x);
        wrf32(b + 4, (float)v);
        wrf32(b + 8, 0.5f);
        wrf32(b + 12, 1.0f);
        wr32(b + 16, 0xffffffffu);
        wr32(b + 20, 0);
        wrf32(b + 24, 0.0f);
        wrf32(b + 28, 0.0f);
    }
}

static void set_matrix_for_test(uint32_t dev, uint32_t which, float scale) {
    uint32_t m = sc(0x1900);
    for (uint32_t i = 0; i < 16; ++i)
        wrf32(m + 4u * i, (i == 0 || i == 5 || i == 10) ? scale : (i == 15 ? 1.0f : 0.0f));
    call_method(dev, DEV_SetTransform, {which, m});
}

static void t5_draw(float x0, float y0, float x1, float y1) {
    uint32_t v = sc(0x3000);
    write_vertices_for_test(v, 3, x0);
    wrf32(v + 4, y0);
    wrf32(v + 32, x1);
    wrf32(v + 36, y0);
    wrf32(v + 64, x0);
    wrf32(v + 68, y1);
    CHECK_EQ(call_method(g_t3_dev, DEV_DrawPrimitive, {4, 3, v, 3, 0}), D3D_OK_);
}
static uint32_t t5_hud(int x = 0, int y = 0) {
    uint32_t s = make_offscreen_for_test(64, 64, 16);
    CHECK(s != 0);
    blt_for_test(g_t3_target, s, x, y, 64, 64, 0);
    return s;
}
static void test_first_hud_boundary_and_overlay_pass() {
    CHECK(make_device_for_test());
    if (!g_t3_dev)
        return;
    t5_draw(100, 100, 150, 150);
    t5_hud();
    t5_draw(2, 2, 30, 30);
    HostFrameHandle f = host_frame_current();
    CHECK_EQ(host_frame_record_count(f), 1u);
    CHECK_EQ(host_frame_draw_count(f), 2u);
    const auto *r = host_frame_record(f, 0);
    CHECK(r != nullptr);
    if (!r)
        return;
    CHECK_EQ(r->after_first_draw, 1);
    CHECK_EQ(host_frame_first_hud_seq(f), r->seq);
    CHECK_EQ(host_frame_draw(f, 0)->in_overlay_pass, 0);
    CHECK_EQ(host_frame_draw(f, 1)->in_overlay_pass, 1);
    CHECK(!host_frame_legacy(f));
}
static void test_overlay_mapping_rule() {
    CHECK(make_device_for_test());
    if (!g_t3_dev)
        return;
    t5_draw(100, 100, 150, 150);
    t5_hud();
    t5_draw(2, 2, 30, 30);
    HostFrameHandle f = host_frame_current();
    CHECK(!strcmp(host_overlay_mapping_for_test(f, host_frame_draw(f, 1)->seq), "ui"));
    CHECK(!host_frame_legacy(f));
    t5_draw(2, 2, 64.1f, 30);
    CHECK(!strcmp(host_overlay_mapping_for_test(f, host_frame_draw(f, 2)->seq), "scene"));
    CHECK(host_frame_legacy(f));
}
static void test_inexpressible_interleave_triggers_legacy_replay() {
    CHECK(make_device_for_test());
    if (!g_t3_dev)
        return;
    t5_draw(100, 100, 150, 150);
    t5_hud();
    t5_draw(32, 32, 90, 90);
    HostFrameHandle f = host_frame_current();
    CHECK(host_frame_legacy(f));
    CHECK_EQ(host_legacy_fallback_count(), 1u);
    t5_draw(40, 40, 100, 100);
    CHECK_EQ(host_legacy_fallback_count(), 1u);
    pump_present_for_test();
    CHECK(host_frame_current().id != f.id);
    CHECK(host_frame_legacy(host_frame_current()));
    t5_draw(200, 200, 220, 220);
    pump_present_for_test();
    CHECK_EQ(host_legacy_fallback_count(), 1u);
    ddraw_note_device(0);
    CHECK(!host_frame_legacy(host_frame_current()));
    ddraw_note_device(1);
    CHECK(!host_frame_legacy(host_frame_current()));
    CHECK(host_frame_legacy(f)); // a sealed frame keeps its decision
}
static void test_hud_rule_exclusions_and_grouping() {
    CHECK(make_device_for_test());
    if (!g_t3_dev)
        return;
    t5_draw(200, 200, 240, 240);
    uint32_t sp = make_offscreen_for_test(64, 64, 16);
    uint32_t other = make_render_target_for_test(64, 64, 16);
    blt_for_test(other, sp, 0, 0, 64, 64, 0);                // not this device's render surface
    blt_for_test(g_t3_target, g_t3_target, 0, 0, 64, 64, 0); // self blit
    HostFrameHandle f = host_frame_current();
    CHECK_EQ(host_frame_first_hud_seq(f), UINT32_MAX);
    blt_for_test(g_t3_target, sp, 0, 0, 64, 64, 0);
    CHECK_EQ(host_frame_first_hud_seq(f), host_frame_record(f, 2)->seq);
    t5_draw(2, 2, 63.9f, 30); // rounded bounds cross the edge, actual vertices do not
    CHECK_EQ(host_frame_draw_mapping(f, host_frame_draw(f, 1)->seq), HOST_MAPPING_UI);
    CHECK(!host_frame_legacy(f));
    blt_for_test(g_t3_target, sp, 64, 0, 64, 64, 0);
    t5_draw(32, 2, 96, 30); // inside the connected element, crossing its record boundary
    CHECK_EQ(host_frame_draw_mapping(f, host_frame_draw(f, 2)->seq), HOST_MAPPING_UI);
    CHECK(!host_frame_legacy(f));
    // Texture destinations do not become HUD, even with 3DDEVICE caps.
    CHECK(make_device_for_test());
    if (!g_t3_dev)
        return;
    ComObj *target = com_this(g_t3_target);
    target->caps |= DDSCAPS_TEXTURE;
    t5_draw(200, 200, 240, 240);
    t5_hud();
    CHECK_EQ(host_frame_first_hud_seq(host_frame_current()), UINT32_MAX);
    target->caps &= ~DDSCAPS_TEXTURE;
}
static void test_transformed_overlay_containment() {
    float v[3][8] = {{-0.9f, 0.9f, 0}, {-0.85f, 0.9f, 0}, {-0.9f, 0.85f, 0}};
    HostD3DDrawSnapshot d{};
    d.vertices = v;
    d.vertex_count = 3;
    d.vertex_stride = 32;
    d.fvf = 2;
    d.state.viewport[2] = 640;
    d.state.viewport[3] = 480;
    CHECK(d3d_draw_inside_rect(&d, 0, 0, 64, 64));
    v[1][0] = -0.79f;
    CHECK(!d3d_draw_inside_rect(&d, 0, 0, 64, 64));
    v[1][0] = -0.85f;
    d.state.transform_set[3] = 1; // w=0 has no finite screen position
    CHECK(!d3d_draw_inside_rect(&d, 0, 0, 640, 480));
}

static void test_later_scene_overlay_intersects_hud() {
    CHECK(make_device_for_test());
    if (!g_t3_dev)
        return;
    t5_draw(100, 100, 150, 150);
    t5_hud();
    t5_draw(200, 200, 220, 220); // clean scene overlay is not a trigger
    HostFrameHandle f = host_frame_current();
    CHECK_EQ(host_frame_draw_mapping(f, host_frame_draw(f, 1)->seq), HOST_MAPPING_SCENE);
    CHECK(!host_frame_legacy(f));
    t5_hud(180, 180); // a later HUD must not move above the already encoded scene draw
    CHECK(host_frame_legacy(f));
    CHECK_EQ(host_legacy_fallback_count(), 1u);
    // The amendment's forward ordering: a HUD destination intersects a later
    // scene-mapped overlay (all vertices do not fit the element).
    CHECK(make_device_for_test());
    if (!g_t3_dev)
        return;
    t5_draw(100, 100, 150, 150);
    t5_hud();
    t5_hud(180, 180);
    t5_draw(220, 220, 300, 300);
    f = host_frame_current();
    CHECK(host_frame_record(f, 1)->seq < host_frame_draw(f, 1)->seq);
    CHECK_EQ(host_frame_draw_mapping(f, host_frame_draw(f, 1)->seq), HOST_MAPPING_SCENE);
    CHECK(host_frame_legacy(f));
    CHECK_EQ(host_legacy_fallback_count(), 1u);
}

static void test_no_reader_no_readback() {
    CHECK(make_device_for_test());
    if (!g_t3_dev)
        return;
    ComObj *target = com_this(g_t3_target);
    HostFrameHandle f = host_frame_current();
    HostDirtyRect expected{640, 480, 0, 0};
    for (int i = 0; i < 10; ++i) {
        uint32_t vb = sc(0x1a00);
        write_vertices_for_test(vb, 3, 10.0f + i * 5);
        wrf32(vb + 32, 14.0f + i * 5);
        wrf32(vb + 64 + 4, 8.0f + i);
        CHECK_EQ(call_method(g_t3_dev, DEV_DrawPrimitive,
                             {D3DPT_TRIANGLELIST, D3DVT_TLVERTEX, vb, 3, 0}),
                 D3D_OK_);
        const auto *d = host_frame_draw(f, i);
        CHECK(d != nullptr);
        if (!d)
            continue;
        expected.x0 = std::min(expected.x0, d->screen_min_x);
        expected.y0 = std::min(expected.y0, d->screen_min_y);
        expected.x1 = std::max(expected.x1, d->screen_max_x);
        expected.y1 = std::max(expected.y1, d->screen_max_y);
    }
    pump_present_for_test();
    CHECK(host_frame_current().id != f.id);
    CHECK_EQ(host_readback_count_for_test(), 0u);
    HostDirtyRect actual{};
    CHECK(host_d3d_dirty_rect(target->id, ddraw_surface_generation(target->id), &actual));
    CHECK_EQ(actual.x0, expected.x0);
    CHECK_EQ(actual.y0, expected.y0);
    CHECK_EQ(actual.x1, expected.x1);
    CHECK_EQ(actual.y1, expected.y1);
}

static void test_each_reader_reads_back_only_dirty() {
    for (int reason = 0; reason < HOST_READ_REASON_COUNT; ++reason) {
        CHECK(make_device_for_test());
        if (!g_t3_dev)
            return;
        g_rec_dd = g_t3_dd;
        uint32_t src = g_t3_target, other = make_offscreen_for_test(640, 480, 16);
        ComObj *o = com_this(src);
        CHECK(o != nullptr);
        if (!o)
            return;
        auto read = [&] {
            uint32_t desc = sc(0x1c00);
            gm_zero(desc, DDSD_SIZE);
            wr32(desc, DDSD_SIZE);
            switch (reason) {
            case HOST_READ_LOCK:
            case HOST_READ_LOCK_WRITE:
                CHECK_EQ(
                    call_method(src, S_Lock,
                                {0, desc,
                                 DDLOCK_WAIT | (reason == HOST_READ_LOCK_WRITE ? DDLOCK_WRITEONLY
                                                                               : DDLOCK_READONLY),
                                 0}),
                    DD_OK);
                CHECK_EQ(call_method(src, S_Unlock, {0}), DD_OK);
                break;
            case HOST_READ_GETDC:
                CHECK_EQ(call_method(src, 17, {sc(0x1c00)}), DD_OK);
                CHECK_EQ(call_method(src, 26, {rd32(sc(0x1c00))}), DD_OK);
                break;
            case HOST_READ_BLT_SOURCE:
                blt_for_test(other, src, 0, 0, 32, 32, 0);
                break;
            case HOST_READ_DSTKEY: {
                wr32(sc(0x1c00), 0);
                wr32(sc(0x1c04), 0xffff);
                CHECK_EQ(call_method(src, S_SetColorKey, {DDCKEY_DESTBLT, sc(0x1c00)}), DD_OK);
                blt_for_test(src, other, 0, 0, 32, 32, DDBLT_KEYDEST);
                break;
            }
            case HOST_READ_DUPLICATE:
                CHECK_EQ(call_method(g_t3_dd, 7, {src, sc(0x1c00)}), DD_OK);
                call_method(rd32(sc(0x1c00)), 2, {});
                break;
            case HOST_READ_TEXTURE_LOAD: {
                uint32_t st = com_view(o, IF_D3DTEXTURE2),
                         dt = com_view(com_this(other), IF_D3DTEXTURE2);
                CHECK_EQ(call_method(dt, TEX_Load, {st}), D3D_OK_);
                break;
            }
            }
        };
        host_d3d_mark_dirty(o->id, ddraw_surface_generation(o->id), {2, 3, 12, 13});
        read();
        CHECK_EQ(host_readback_count_for_test(), 1u);
        CHECK_EQ(g_t4_read_rects.size(), 1u);
        if (g_t4_read_rects.size() == 1) {
            const auto &r = g_t4_read_rects.front();
            CHECK_EQ(r.x0, 2);
            CHECK_EQ(r.y0, 3);
            CHECK_EQ(r.x1, 12);
            CHECK_EQ(r.y1, 13);
        }
        CHECK_EQ(host_readback_reason_count((HostReadReason)reason), 1u);
        HostAccessCounts before{}, after{};
        host_access_counts(&before);
        read();
        host_access_counts(&after);
        CHECK_EQ(host_readback_count_for_test(), 1u);
        // A keyed blit has two readers; both are now clean.
        CHECK_EQ(after.clean_reads, before.clean_reads + (reason == HOST_READ_DSTKEY ? 2u : 1u));
    }
    CHECK(make_device_for_test());
    if (!g_t3_dev)
        return;
    g_rec_dd = g_t3_dd;
    uint32_t fast_dst = make_offscreen_for_test(640, 480, 16);
    ComObj *fast_src = com_this(g_t3_target);
    host_d3d_mark_dirty(fast_src->id, ddraw_surface_generation(fast_src->id), {1, 2, 3, 4});
    CHECK_EQ(call_method(fast_dst, S_BltFast, {0, 0, g_t3_target, 0, DDBLTFAST_WAIT}), DD_OK);
    CHECK_EQ(host_readback_reason_count(HOST_READ_BLT_SOURCE), 1u);
    HostAccessCounts fast_before{}, fast_after{};
    host_access_counts(&fast_before);
    CHECK_EQ(call_method(fast_dst, S_BltFast, {0, 0, g_t3_target, 0, DDBLTFAST_WAIT}), DD_OK);
    host_access_counts(&fast_after);
    CHECK_EQ(host_readback_count_for_test(), 1u);
    CHECK_EQ(fast_after.clean_reads, fast_before.clean_reads + 1);
    // A partial read must not clean an unread island or reread a clean hole.
    CHECK(make_device_for_test());
    if (!g_t3_dev)
        return;
    ComObj *o = com_this(g_t3_target);
    uint32_t g = ddraw_surface_generation(o->id);
    host_d3d_mark_dirty(o->id, g, {0, 0, 30, 30});
    int32_t middle[4] = {10, 10, 20, 20};
    d3d_read_surface(o, middle, HOST_READ_LOCK);
    d3d_read_surface(o, middle, HOST_READ_GETDC);
    CHECK_EQ(host_readback_count_for_test(), 1u);
    CHECK(host_d3d_dirty_rect(o->id, g, nullptr));
    d3d_read_surface(o, nullptr, HOST_READ_DUPLICATE);
    CHECK_EQ(host_readback_count_for_test(), 2u);
    CHECK(!host_d3d_dirty_rect(o->id, g, nullptr));
}

// Repeated contained draws must stay compact, and preserving that union
// must not turn a previously read clean hole back into dirty pixels.
static void test_contained_dirty_draws() {
    host_d3d_reset_coherence();
    uint16_t pixels[32 * 24] = {};
    HostD3DSurface surface{};
    surface.id = 12345;
    surface.width = 32;
    surface.height = 24;
    surface.pixels = pixels;
    surface.pitch = 64;
    surface.bpp = 16;
    constexpr uint32_t generation = 7;
    HostDirtyRect full{0, 0, 32, 24};
    host_d3d_mark_dirty(surface.id, generation, full);
    for (int i = 0; i < 1000; ++i) {
        int x = i % 30, y = (i * 7) % 22;
        host_d3d_mark_dirty(surface.id, generation, {x, y, x + 2, y + 2});
    }
    CHECK_EQ(host_d3d_make_coherent(&surface, generation, nullptr, HOST_READ_LOCK), 1);
    CHECK_EQ(g_t4_read_rects.size(), 1u);
    if (g_t4_read_rects.size() == 1) {
        auto r = g_t4_read_rects[0];
        CHECK_EQ(r.x0, 0);
        CHECK_EQ(r.y0, 0);
        CHECK_EQ(r.x1, 32);
        CHECK_EQ(r.y1, 24);
    }
    // Compare exact dirty coverage to a small bitmap oracle across overlapping
    // writes, CPU cleaning, partial readers, and repeated marks. Every emitted
    // pixel must occur once and belong to the requested dirty set.
    bool dirty[24][32] = {};
    uint32_t random = 0x4137;
    auto next = [&]() {
        random = random * 1664525u + 1013904223u;
        return random;
    };
    auto mark = [&](HostDirtyRect r) {
        host_d3d_mark_dirty(surface.id, generation, r);
        for (int y = r.y0; y < r.y1; ++y)
            for (int x = r.x0; x < r.x1; ++x)
                dirty[y][x] = true;
    };
    auto read = [&](HostDirtyRect r) {
        g_t4_read_rects.clear();
        int status = host_d3d_make_coherent(&surface, generation, &r, HOST_READ_LOCK);
        unsigned seen[24][32] = {};
        unsigned expected = 0;
        for (auto q : g_t4_read_rects) {
            bool valid = q.x0 >= r.x0 && q.y0 >= r.y0 && q.x1 <= r.x1 && q.y1 <= r.y1 &&
                         q.x0 < q.x1 && q.y0 < q.y1;
            CHECK(valid);
            if (!valid)
                continue;
            for (int y = q.y0; y < q.y1; ++y)
                for (int x = q.x0; x < q.x1; ++x)
                    ++seen[y][x];
        }
        for (int y = 0; y < 24; ++y)
            for (int x = 0; x < 32; ++x) {
                bool want = dirty[y][x] && x >= r.x0 && x < r.x1 && y >= r.y0 && y < r.y1;
                CHECK_EQ(seen[y][x], unsigned(want));
                expected += want;
                if (want)
                    dirty[y][x] = false;
            }
        CHECK_EQ(status, expected ? 1 : 0);
    };
    mark(full);
    read({8, 6, 24, 18});
    mark({1, 1, 7, 5});
    read({8, 6, 24, 18}); // still a clean hole
    mark({6, 4, 10, 8});
    read(full); // a new draw bridges into the hole
    for (int i = 0; i < 600; ++i) {
        int x = next() % 32, y = next() % 24;
        HostDirtyRect r{x, y, x + 1 + int(next() % (32 - x)), y + 1 + int(next() % (24 - y))};
        if (i % 4 == 0)
            read(r);
        else if (i % 4 == 1) {
            host_d3d_clean_pixels(surface.id, generation, r);
            for (int yy = r.y0; yy < r.y1; ++yy)
                for (int xx = r.x0; xx < r.x1; ++xx)
                    dirty[yy][xx] = false;
        } else {
            mark(r);
            mark(r);
        }
    }
    read(full);
    host_d3d_reset_coherence();
}

static void test_flip_transfers_dirty_region() {
    rec_reset();
    uint32_t primary = make_primary_chain_for_test();
    ComObj *front = com_this(primary);
    CHECK(front != nullptr);
    if (!front)
        return;
    ComObj *back = com_get(front->back_obj);
    CHECK(back != nullptr);
    if (!back)
        return;
    uint32_t fg = ddraw_surface_generation(front->id), bg = ddraw_surface_generation(back->id);
    HostDirtyRect dirty{7, 9, 21, 23}, actual{};
    host_d3d_mark_dirty(back->id, bg, dirty);
    CHECK_EQ(call_method(primary, S_Flip, {0, DDFLIP_WAIT}), DD_OK);
    CHECK(host_d3d_dirty_rect(front->id, fg + 1, &actual));
    CHECK_EQ(actual.x0, dirty.x0);
    CHECK_EQ(actual.y0, dirty.y0);
    CHECK_EQ(actual.x1, dirty.x1);
    CHECK_EQ(actual.y1, dirty.y1);
    CHECK(!host_d3d_dirty_rect(back->id, bg, nullptr));
    CHECK(!host_d3d_dirty_rect(front->id, fg, nullptr));
    CHECK_EQ(host_readback_count_for_test(), 0u);
}

static void test_draw_snapshot_is_deep() {
    CHECK(make_device_for_test());
    if (!g_t3_dev)
        return;
    HostFrameHandle f = host_frame_current();

    uint32_t vb = sc(0x1a00);
    write_vertices_for_test(vb, 16, 1.0f);
    set_matrix_for_test(g_t3_dev, D3DTRANSFORMSTATE_WORLD, 2.0f);
    CHECK_EQ(
        call_method(g_t3_dev, DEV_DrawPrimitive, {D3DPT_TRIANGLELIST, D3DVT_TLVERTEX, vb, 16, 0}),
        D3D_OK_);

    // The guest reuses the buffer and moves the matrix on, which is exactly
    // what it does between one draw and the next.
    write_vertices_for_test(vb, 16, 9.0f);
    set_matrix_for_test(g_t3_dev, D3DTRANSFORMSTATE_WORLD, 3.0f);

    CHECK_EQ(host_frame_draw_count(f), 1u);
    const HostD3DDrawSnapshot *d = host_frame_draw(f, 0);
    CHECK(d != nullptr);
    if (!d)
        return;
    CHECK_EQ(d->kind, HOST_DRAW_PRIMITIVE);
    CHECK_EQ(d->vertex_count, 16u);
    CHECK_EQ(d->vertex_stride, 32u);
    CHECK_EQ(d->primitive_type, (uint32_t)D3DPT_TRIANGLELIST);
    CHECK_EQ(d->fvf, (uint32_t)D3DVT_TLVERTEX);
    CHECK(d->vertices != nullptr);
    if (d->vertices)
        CHECK(((const float *)d->vertices)[0] == 1.0f);
    CHECK_EQ(d->state.transform_set[D3DTRANSFORMSTATE_WORLD], 1);
    CHECK(d->state.transform[D3DTRANSFORMSTATE_WORLD][0] == 2.0f);

    // The copy is the frame's, not the guest's. This is the assertion the
    // whole task is about: a pointer into guest memory here would read the
    // 9.0f above by the time the frame is composited.
    const uint8_t *base = gm_ptr(0);
    const uint8_t *v = (const uint8_t *)d->vertices;
    CHECK(v < base || v >= base + GUEST_SIZE);
}

// A frame's draws are replayed in one order with its blits, so a draw's
// sequence number comes from the same series a blit record's does.
static void test_draw_and_blit_share_one_order() {
    CHECK(make_device_for_test());
    if (!g_t3_dev)
        return;
    HostFrameHandle f = host_frame_current();
    uint32_t sp = make_offscreen_for_test(8, 8, 8);
    CHECK(sp != 0);
    if (!sp)
        return;

    uint32_t vb = sc(0x1a00);
    write_vertices_for_test(vb, 3, 1.0f);
    call_method(g_t3_dev, DEV_DrawPrimitive, {D3DPT_TRIANGLELIST, D3DVT_TLVERTEX, vb, 3, 0});
    blt_for_test(g_t3_target, sp, 0, 0, 8, 8, 0);
    call_method(g_t3_dev, DEV_DrawPrimitive, {D3DPT_TRIANGLELIST, D3DVT_TLVERTEX, vb, 3, 0});

    CHECK_EQ(host_frame_draw_count(f), 2u);
    CHECK_EQ(host_frame_record_count(f), 1u);
    const HostD3DDrawSnapshot *d0 = host_frame_draw(f, 0);
    const HostD3DDrawSnapshot *d1 = host_frame_draw(f, 1);
    const HostBlitRecord *r = host_frame_record(f, 0);
    CHECK(d0 && d1 && r);
    if (!d0 || !d1 || !r)
        return;
    CHECK(d0->seq < r->seq);
    CHECK(r->seq < d1->seq);
    // And the blit is after the first draw, which is what the HUD rule reads.
    CHECK_EQ(r->after_first_draw, 1);
}

// Clear is part of the draw list, in its place in the order, with its
// rectangles copied: the guest's array is its own the moment Clear returns.
static void test_clear_is_recorded_with_its_rects() {
    CHECK(make_device_for_test());
    if (!g_t3_vp)
        return;
    HostFrameHandle f = host_frame_current();

    uint32_t rects = sc(0x1c00);
    wr32(rects + 0, 10);
    wr32(rects + 4, 20);
    wr32(rects + 8, 110);
    wr32(rects + 12, 220);
    wr32(rects + 16, 300);
    wr32(rects + 20, 5);
    wr32(rects + 24, 400);
    wr32(rects + 28, 15);
    // Viewport::Clear takes (count, rects, flags): two rectangles, target
    // and Z, which is what the game clears at the top of a frame.
    CHECK_EQ(call_method(g_t3_vp, VP_Clear, {2, rects, 3}), D3D_OK_);

    // The guest overwrites its own rectangle array straight afterwards.
    wr32(rects + 0, 999);
    wr32(rects + 4, 999);

    CHECK_EQ(host_frame_draw_count(f), 1u);
    const HostD3DDrawSnapshot *d = host_frame_draw(f, 0);
    CHECK(d != nullptr);
    if (!d)
        return;
    CHECK_EQ(d->kind, HOST_DRAW_CLEAR);
    CHECK_EQ(d->clear_flags, 3u);
    CHECK_EQ(d->clear_rect_count, 2u);
    CHECK(d->clear_rects != nullptr);
    if (d->clear_rects) {
        CHECK_EQ(d->clear_rects[0], 10);
        CHECK_EQ(d->clear_rects[1], 20);
        CHECK_EQ(d->clear_rects[6], 400);
    }
    // Its bounds are what it cleared, which is what the interleaving rule asks.
    CHECK_EQ(d->screen_min_x, 10);
    CHECK_EQ(d->screen_min_y, 5);
    CHECK_EQ(d->screen_max_x, 400);
    CHECK_EQ(d->screen_max_y, 220);
}

// Where a draw lands, which the HUD and interleaving rules are both asked
// about later. A pre-transformed draw is already in screen space.
static void test_draw_screen_bounds() {
    CHECK(make_device_for_test());
    if (!g_t3_dev)
        return;
    HostFrameHandle f = host_frame_current();

    uint32_t vb = sc(0x1a00);
    // Three vertices at (100,10), (140,10), (100,60).
    wrf32(vb + 0, 100.0f);
    wrf32(vb + 4, 10.0f);
    wrf32(vb + 8, 0.5f);
    wrf32(vb + 12, 1.0f);
    wr32(vb + 16, 0xffffffffu);
    wr32(vb + 20, 0);
    wrf32(vb + 24, 0);
    wrf32(vb + 28, 0);
    wrf32(vb + 32, 140.0f);
    wrf32(vb + 36, 10.0f);
    wrf32(vb + 40, 0.5f);
    wrf32(vb + 44, 1.0f);
    wr32(vb + 48, 0xffffffffu);
    wr32(vb + 52, 0);
    wrf32(vb + 56, 0);
    wrf32(vb + 60, 0);
    wrf32(vb + 64, 100.0f);
    wrf32(vb + 68, 60.0f);
    wrf32(vb + 72, 0.5f);
    wrf32(vb + 76, 1.0f);
    wr32(vb + 80, 0xffffffffu);
    wr32(vb + 84, 0);
    wrf32(vb + 88, 0);
    wrf32(vb + 92, 0);
    call_method(g_t3_dev, DEV_DrawPrimitive, {D3DPT_TRIANGLELIST, D3DVT_TLVERTEX, vb, 3, 0});

    const HostD3DDrawSnapshot *d = host_frame_draw(f, 0);
    CHECK(d != nullptr);
    if (!d)
        return;
    // max is EXCLUSIVE, the same convention as Clear's rectangles: the vertex
    // at 140 is covered, so the bound past it is 141.
    CHECK_EQ(d->screen_min_x, 100);
    CHECK_EQ(d->screen_min_y, 10);
    CHECK_EQ(d->screen_max_x, 141);
    CHECK_EQ(d->screen_max_y, 61);
}

// The frame holds the texture revision its draw named, and lets go when the
// frame retires. Without the lease the guest's next upload replaces the pixels
// under a frame nobody has composited yet.
static void test_draw_leases_its_texture_revision() {
    CHECK(make_device_for_test());
    if (!g_t3_dev)
        return;
    g_tex_leases.clear();

    uint32_t desc = sc(0x400);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_TEXTURE);
    wr32(desc + DDSD_OFF_dwWidth, 16);
    wr32(desc + DDSD_OFF_dwHeight, 16);
    call_method(g_t3_dd, DD_CreateSurface, {desc, sc(20), 0});
    uint32_t texsurf = rd32(sc(20));
    CHECK(texsurf != 0);
    if (!texsurf)
        return;
    uint8_t tex2[16] = {0x02, 0x15, 0x28, 0x93, 0xF8, 0x8C, 0xD0, 0x11,
                        0x89, 0xAB, 0x00, 0xA0, 0xC9, 0x05, 0x41, 0x29};
    uint32_t iid = sc(0x40);
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, tex2[i]);
    CHECK_EQ(call_method(texsurf, S_QueryInterface, {iid, sc(24)}), S_OK);
    uint32_t tex = rd32(sc(24));
    CHECK(tex != 0);
    if (!tex)
        return;
    CHECK_EQ(call_method(tex, TEX_GetHandle, {g_t3_dev, sc(28)}), D3D_OK_);
    uint32_t handle = rd32(sc(28));
    CHECK(handle != 0);
    if (!handle)
        return;

    ComObj *to = rec_obj(texsurf);
    CHECK(to != nullptr);
    if (!to)
        return;
    uint32_t rev = host_surface_revision_for_test(to->id);

    call_method(g_t3_dev, DEV_SetRenderState, {D3DRENDERSTATE_TEXTUREHANDLE, handle});
    HostFrameHandle f = host_frame_current();
    uint32_t vb = sc(0x1a00);
    write_vertices_for_test(vb, 3, 1.0f);
    call_method(g_t3_dev, DEV_DrawPrimitive, {D3DPT_TRIANGLELIST, D3DVT_TLVERTEX, vb, 3, 0});

    const HostD3DDrawSnapshot *d = host_frame_draw(f, 0);
    CHECK(d != nullptr);
    if (!d)
        return;
    CHECK_EQ(d->texture_handle, handle);
    CHECK_EQ(d->texture_revision, rev);
    CHECK_EQ(host_sprite_frame_id(), f.id);
    CHECK_EQ(host_sprite_texture_revision(handle), d->texture_revision);
    CHECK_EQ(host_sprite_texture_revision(0xffffffffu), 0u);
    CHECK_EQ(leases_for_test(handle, rev), 1u);

    // The guest writes the texture again: a new revision, and the frame is
    // still holding the old one.
    fill_for_test(texsurf, 7);
    CHECK(host_surface_revision_for_test(to->id) != rev);
    CHECK_EQ(host_sprite_texture_revision(handle), host_surface_revision_for_test(to->id));
    CHECK_EQ(d->texture_revision, rev); // the older draw is still its own revision
    CHECK_EQ(leases_for_test(handle, rev), 1u);

    // Retiring the frame is what lets go.
    host_frame_release(f);
    CHECK_EQ(leases_for_test(handle, rev), 0u);
}

// Every path that changes what a texture SAMPLES has to move its revision, or
// the upload that follows replaces a texture some frame is still holding under
// the same key - which is the one thing the lease exists to prevent.
static void test_every_upload_path_bumps_the_revision() {
    CHECK(make_device_for_test());
    if (!g_t3_dev)
        return;
    uint32_t desc = sc(0x400);
    auto make_tex = [&](uint32_t out) {
        gm_zero(desc, DDSD_SIZE);
        wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
        wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT);
        wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_TEXTURE);
        wr32(desc + DDSD_OFF_dwWidth, 16);
        wr32(desc + DDSD_OFF_dwHeight, 16);
        call_method(g_t3_dd, DD_CreateSurface, {desc, out, 0});
        return rd32(out);
    };
    uint32_t a_surf = make_tex(sc(20)), b_surf = make_tex(sc(0x30));
    CHECK(a_surf != 0);
    CHECK(b_surf != 0);
    if (!a_surf || !b_surf)
        return;
    uint8_t tex2[16] = {0x02, 0x15, 0x28, 0x93, 0xF8, 0x8C, 0xD0, 0x11,
                        0x89, 0xAB, 0x00, 0xA0, 0xC9, 0x05, 0x41, 0x29};
    uint32_t iid = sc(0x40);
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, tex2[i]);
    CHECK_EQ(call_method(a_surf, S_QueryInterface, {iid, sc(24)}), S_OK);
    CHECK_EQ(call_method(b_surf, S_QueryInterface, {iid, sc(0x34)}), S_OK);
    uint32_t a = rd32(sc(24)), b = rd32(sc(0x34));
    CHECK(a != 0);
    CHECK(b != 0);
    if (!a || !b)
        return;
    CHECK_EQ(call_method(a, TEX_GetHandle, {g_t3_dev, sc(28)}), D3D_OK_);
    CHECK_EQ(call_method(b, TEX_GetHandle, {g_t3_dev, sc(0x38)}), D3D_OK_);
    ComObj *ao = rec_obj(a_surf);
    ComObj *bo = rec_obj(b_surf);
    CHECK(ao != nullptr);
    CHECK(bo != nullptr);
    if (!ao || !bo)
        return;

    // Load copies new pixels in.
    uint32_t before = host_surface_revision_for_test(ao->id);
    CHECK_EQ(call_method(a, TEX_Load, {b}), D3D_OK_);
    CHECK(host_surface_revision_for_test(ao->id) != before);

    // A palette re-resolve changes what the same indices mean.
    before = host_surface_revision_for_test(ao->id);
    CHECK_EQ(call_method(a, TEX_PaletteChanged, {0, 0}), D3D_OK_);
    CHECK(host_surface_revision_for_test(ao->id) != before);

    // And a handle swap makes each handle name the other surface's pixels.
    uint32_t ra = host_surface_revision_for_test(ao->id);
    uint32_t rb = host_surface_revision_for_test(bo->id);
    uint32_t ha = ao->texture_handle, hb = bo->texture_handle;
    CHECK_EQ(call_method(g_t3_dev, DEV_SwapTextureHandles, {a, b}), D3D_OK_);
    uint32_t ra2 = host_surface_revision_for_test(ao->id);
    uint32_t rb2 = host_surface_revision_for_test(bo->id);
    CHECK(ra2 != ra);
    CHECK(rb2 != rb);
    // And not merely different from their own previous values: different from
    // every revision EITHER surface has held. Per-surface counters leave both
    // at low numbers, so after a swap "handle A revision 3" can mean one
    // surface's pixels or the other's, and a frame holding the first is handed
    // the second.
    CHECK(ra2 != rb);
    CHECK(rb2 != ra);
    CHECK(ra2 != rb2);
    CHECK_EQ(host_sprite_texture_revision(ha), rb2);
    CHECK_EQ(host_sprite_texture_revision(hb), ra2);
}

// Lookup must retain object identity as the table grows, rejects released
// objects, and clears every old mapping before handles restart after reset.
static void test_texture_handle_lifetime() {
    rec_reset();
    d3d_reset();
    struct Texture {
        uint32_t surface, view, handle, revision;
    };
    std::vector<Texture> textures;
    for (unsigned i = 0; i < 512; ++i) {
        uint32_t surface = rec_make_surface(1, 1, 8, DDSCAPS_TEXTURE);
        ComObj *o = rec_obj(surface);
        CHECK(o != nullptr);
        if (!o)
            return;
        uint32_t view = com_view(o, IF_D3DTEXTURE2);
        CHECK_EQ(call_method(view, TEX_GetHandle, {0, sc(28)}), D3D_OK_);
        textures.push_back({surface, view, rd32(sc(28)), host_surface_revision_for_test(o->id)});
    }
    for (const auto &t : textures) {
        CHECK(t.revision != 0);
        CHECK_EQ(host_sprite_texture_revision(t.handle), t.revision);
        CHECK_EQ(call_method(t.view, TEX_GetHandle, {0, sc(28)}), D3D_OK_);
        CHECK_EQ(rd32(sc(28)), t.handle);
    }
    for (size_t i = 0; i < textures.size(); i += 2) {
        CHECK_EQ(call_method(textures[i].surface, S_Release, {}), 0u);
        CHECK_EQ(host_sprite_texture_revision(textures[i].handle), 0u);
    }
    for (size_t i = 1; i < textures.size(); i += 2)
        CHECK_EQ(host_sprite_texture_revision(textures[i].handle), textures[i].revision);
    CHECK_EQ(host_sprite_texture_revision(0), 0u);
    CHECK_EQ(host_sprite_texture_revision(0xffffffffu), 0u);
    d3d_reset();
    for (const auto &t : textures)
        CHECK_EQ(host_sprite_texture_revision(t.handle), 0u);
    uint32_t surface = rec_make_surface(1, 1, 8, DDSCAPS_TEXTURE);
    ComObj *o = rec_obj(surface);
    CHECK(o != nullptr);
    if (!o)
        return;
    uint32_t view = com_view(o, IF_D3DTEXTURE2);
    CHECK_EQ(call_method(view, TEX_GetHandle, {0, sc(28)}), D3D_OK_);
    CHECK_EQ(rd32(sc(28)), textures.front().handle);
    CHECK_EQ(host_sprite_texture_revision(rd32(sc(28))), host_surface_revision_for_test(o->id));
}

// Revisions come from one counter for the whole process, so no two surfaces
// ever share one. Per-surface counters put two freshly written surfaces at the
// same number, and a handle swap then makes one (handle, revision) pair mean
// two different pictures - a frame holding the first is handed the second.
static void test_revisions_are_unique_across_surfaces() {
    rec_reset();
    uint32_t one = make_offscreen_for_test(8, 8, 8);
    uint32_t two = make_offscreen_for_test(8, 8, 8);
    CHECK(one != 0);
    CHECK(two != 0);
    if (!one || !two)
        return;
    ComObj *o1 = rec_obj(one);
    ComObj *o2 = rec_obj(two);
    CHECK(o1 != nullptr);
    CHECK(o2 != nullptr);
    if (!o1 || !o2)
        return;

    // The same history on each: created, then written once.
    fill_for_test(one, 1);
    fill_for_test(two, 1);
    CHECK(host_surface_revision_for_test(o1->id) != host_surface_revision_for_test(o2->id));

    // And again after a second write apiece.
    fill_for_test(one, 2);
    fill_for_test(two, 2);
    CHECK(host_surface_revision_for_test(o1->id) != host_surface_revision_for_test(o2->id));
}

// A palette write changes what an 8-bit texture's indices resolve to. The
// renderer holds expanded pixels and refuses to overwrite a revision a frame
// is holding, so without a new revision the upload is dropped and the texture
// never fades.
static void test_palette_write_bumps_a_texture_revision() {
    CHECK(make_device_for_test());
    if (!g_t3_dev)
        return;
    // An 8-bit mode, so the texture is palettised: a palette on a 16-bit
    // surface is refused, and it is the palettised ones that fade.
    CHECK_EQ(call_method(g_t3_dd, DD_SetDisplayMode, {640, 480, 8}), DD_OK);
    uint32_t desc = sc(0x400);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_TEXTURE);
    wr32(desc + DDSD_OFF_dwWidth, 16);
    wr32(desc + DDSD_OFF_dwHeight, 16);
    call_method(g_t3_dd, DD_CreateSurface, {desc, sc(20), 0});
    uint32_t texsurf = rd32(sc(20));
    CHECK(texsurf != 0);
    if (!texsurf)
        return;

    uint32_t entries = sc(0x1000);
    gm_zero(entries, 256 * 4);
    wr32(entries + 1 * 4, 0x00000030u);
    CHECK_EQ(call_method(g_t3_dd, DD_CreatePalette,
                         {DDPCAPS_8BIT | DDPCAPS_ALLOW256, entries, sc(0x1400), 0}),
             DD_OK);
    uint32_t pal = rd32(sc(0x1400));
    CHECK(pal != 0);
    if (!pal)
        return;
    CHECK_EQ(call_method(texsurf, S_SetPalette, {pal}), DD_OK);

    uint8_t tex2[16] = {0x02, 0x15, 0x28, 0x93, 0xF8, 0x8C, 0xD0, 0x11,
                        0x89, 0xAB, 0x00, 0xA0, 0xC9, 0x05, 0x41, 0x29};
    uint32_t iid = sc(0x40);
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, tex2[i]);
    CHECK_EQ(call_method(texsurf, S_QueryInterface, {iid, sc(24)}), S_OK);
    uint32_t tex = rd32(sc(24));
    CHECK(tex != 0);
    if (!tex)
        return;
    CHECK_EQ(call_method(tex, TEX_GetHandle, {g_t3_dev, sc(28)}), D3D_OK_);
    uint32_t handle = rd32(sc(28));
    ComObj *to = rec_obj(texsurf);
    CHECK(handle != 0);
    CHECK(to != nullptr);
    if (!handle || !to)
        return;

    // A frame draws with it, so the revision it uses is held.
    g_tex_leases.clear();
    call_method(g_t3_dev, DEV_SetRenderState, {D3DRENDERSTATE_TEXTUREHANDLE, handle});
    uint32_t vb = sc(0x1a00);
    write_vertices_for_test(vb, 3, 1.0f);
    call_method(g_t3_dev, DEV_DrawPrimitive, {D3DPT_TRIANGLELIST, D3DVT_TLVERTEX, vb, 3, 0});
    uint32_t held = host_surface_revision_for_test(to->id);
    CHECK_EQ(leases_for_test(handle, held), 1u);

    // The guest fades the palette. The texture's revision has to move, and the
    // upload that follows has to carry the new one.
    size_t uploads_before = g_uploads.size();
    wr32(entries + 1 * 4, 0x00000090u);
    CHECK_EQ(call_method(pal, P_SetEntries, {0, 0, 256, entries}), DD_OK);
    uint32_t after = host_surface_revision_for_test(to->id);
    CHECK(after != held);
    CHECK(g_uploads.size() > uploads_before);
    CHECK_EQ(g_uploads.back().handle, handle);
    CHECK_EQ(g_uploads.back().revision, after);
}

// A draw can name a revision the renderer never received without anything
// having written the texture: a Flip or a SetSurfaceDesc moves a surface's
// revision on its own. The draw uploads and leases rather than sampling
// nothing.
static void test_draw_uploads_a_revision_the_renderer_lacks() {
    CHECK(make_device_for_test());
    if (!g_t3_dev)
        return;
    uint32_t desc = sc(0x400);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_TEXTURE);
    wr32(desc + DDSD_OFF_dwWidth, 16);
    wr32(desc + DDSD_OFF_dwHeight, 16);
    call_method(g_t3_dd, DD_CreateSurface, {desc, sc(20), 0});
    uint32_t texsurf = rd32(sc(20));
    CHECK(texsurf != 0);
    if (!texsurf)
        return;
    uint8_t tex2[16] = {0x02, 0x15, 0x28, 0x93, 0xF8, 0x8C, 0xD0, 0x11,
                        0x89, 0xAB, 0x00, 0xA0, 0xC9, 0x05, 0x41, 0x29};
    uint32_t iid = sc(0x40);
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, tex2[i]);
    CHECK_EQ(call_method(texsurf, S_QueryInterface, {iid, sc(24)}), S_OK);
    uint32_t tex = rd32(sc(24));
    CHECK(tex != 0);
    if (!tex)
        return;
    CHECK_EQ(call_method(tex, TEX_GetHandle, {g_t3_dev, sc(28)}), D3D_OK_);
    uint32_t handle = rd32(sc(28));
    CHECK(handle != 0);
    ComObj *to = rec_obj(texsurf);
    CHECK(to != nullptr);
    if (!handle || !to)
        return;

    // The revision moves with no upload behind it, which is what a Flip or a
    // SetSurfaceDesc does to a surface.
    ddraw_storage_changed_for_test(to->id);
    uint32_t rev = host_surface_revision_for_test(to->id);

    g_tex_leases.clear();
    call_method(g_t3_dev, DEV_SetRenderState, {D3DRENDERSTATE_TEXTUREHANDLE, handle});
    HostFrameHandle f = host_frame_current();
    uint32_t vb = sc(0x1a00);
    write_vertices_for_test(vb, 3, 1.0f);
    call_method(g_t3_dev, DEV_DrawPrimitive, {D3DPT_TRIANGLELIST, D3DVT_TLVERTEX, vb, 3, 0});

    const HostD3DDrawSnapshot *d = host_frame_draw(f, 0);
    CHECK(d != nullptr);
    if (!d)
        return;
    // The draw holds a revision the renderer has, rather than one it does not.
    CHECK_EQ(d->texture_revision, rev);
    CHECK_EQ(leases_for_test(handle, rev), 1u);
}

// Load reads the source surface's pixels. It is one of the readers the
// coherence contract names, and it was the only one with no counter.
static void test_texture_load_is_counted() {
    CHECK(make_device_for_test());
    if (!g_t3_dd)
        return;
    uint32_t desc = sc(0x400);
    auto make_tex = [&](uint32_t out) {
        gm_zero(desc, DDSD_SIZE);
        wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
        wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT);
        wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_TEXTURE);
        wr32(desc + DDSD_OFF_dwWidth, 16);
        wr32(desc + DDSD_OFF_dwHeight, 16);
        call_method(g_t3_dd, DD_CreateSurface, {desc, out, 0});
        return rd32(out);
    };
    uint32_t dst_surf = make_tex(sc(20));
    uint32_t src_surf = make_tex(sc(0x30));
    CHECK(dst_surf != 0);
    CHECK(src_surf != 0);
    if (!dst_surf || !src_surf)
        return;

    uint8_t tex2[16] = {0x02, 0x15, 0x28, 0x93, 0xF8, 0x8C, 0xD0, 0x11,
                        0x89, 0xAB, 0x00, 0xA0, 0xC9, 0x05, 0x41, 0x29};
    uint32_t iid = sc(0x40);
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, tex2[i]);
    CHECK_EQ(call_method(dst_surf, S_QueryInterface, {iid, sc(24)}), S_OK);
    CHECK_EQ(call_method(src_surf, S_QueryInterface, {iid, sc(0x34)}), S_OK);
    uint32_t dst = rd32(sc(24)), src = rd32(sc(0x34));
    CHECK(dst != 0);
    CHECK(src != 0);
    if (!dst || !src)
        return;

    ddraw_reset_access_counts();
    HostAccessCounts a;
    CHECK_EQ(call_method(dst, TEX_Load, {src}), D3D_OK_);
    host_access_counts(&a);
    CHECK_EQ(a.texture_load, 1u);
    // And nothing else claims the read.
    CHECK_EQ(a.blt_source, 0u);
    CHECK_EQ(a.lock_read, 0u);
}

// Every reader named by the coherence contract increments exactly its own
// counter and no other. The point is the "and no other": a single readback
// total would say a frame was expensive, and these say which path made it so.
static void test_access_counts_by_reason() {
    rec_reset();
    uint32_t rt = make_render_target_for_test(640, 480, 8);
    uint32_t sp = make_offscreen_for_test(8, 8, 8);
    CHECK(rt != 0 && sp != 0);
    if (!rt || !sp)
        return;
    uint32_t desc = sc(0xa80);

    // A read lock, and only a read lock.
    ddraw_reset_access_counts();
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    call_method(sp, S_Lock, {0, desc, DDLOCK_WAIT | DDLOCK_READONLY, 0});
    call_method(sp, S_Unlock, {0});
    HostAccessCounts a;
    host_access_counts(&a);
    CHECK_EQ(a.lock_read, 1u);
    CHECK_EQ(a.lock_write, 0u);
    CHECK_EQ(a.getdc, 0u);
    CHECK_EQ(a.blt_source, 0u);
    CHECK_EQ(a.dstkey_read, 0u);
    CHECK_EQ(a.duplicate, 0u);
    CHECK_EQ(a.flip, 0u);

    // A write lock, and only a write lock.
    ddraw_reset_access_counts();
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    call_method(sp, S_Lock, {0, desc, DDLOCK_WAIT | DDLOCK_WRITEONLY, 0});
    call_method(sp, S_Unlock, {0});
    host_access_counts(&a);
    CHECK_EQ(a.lock_write, 1u);
    CHECK_EQ(a.lock_read, 0u);

    // A lock with neither hint can read, so it counts as a read.
    ddraw_reset_access_counts();
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    call_method(sp, S_Lock, {0, desc, DDLOCK_WAIT, 0});
    call_method(sp, S_Unlock, {0});
    host_access_counts(&a);
    CHECK_EQ(a.lock_read, 1u);
    CHECK_EQ(a.lock_write, 0u);

    // A blit reads its source. No destination key here, so no key read.
    ddraw_reset_access_counts();
    blt_for_test(rt, sp, 0, 0, 8, 8, 0);
    host_access_counts(&a);
    CHECK_EQ(a.blt_source, 1u);
    CHECK_EQ(a.dstkey_read, 0u);
    CHECK_EQ(a.lock_read, 0u);
    CHECK_EQ(a.lock_write, 0u);

    // A fill has no source at all: nothing is read.
    ddraw_reset_access_counts();
    fill_blt_for_test(rt, 0, 0, 8, 8, 4);
    host_access_counts(&a);
    CHECK_EQ(a.blt_source, 0u);
    CHECK_EQ(a.dstkey_read, 0u);

    // A destination-keyed blit reads BOTH: the source for its pixels and the
    // destination to decide which of its own pixels may be written.
    ddraw_reset_access_counts();
    uint32_t key = sc(0xb80);
    wr32(key + 0, 0);
    wr32(key + 4, 0);
    call_method(rt, S_SetColorKey, {DDCKEY_DESTBLT, key});
    blt_for_test(rt, sp, 0, 0, 8, 8, DDBLT_KEYDEST);
    host_access_counts(&a);
    CHECK_EQ(a.blt_source, 1u);
    CHECK_EQ(a.dstkey_read, 1u);

    // A flip reads both buffers.
    ddraw_reset_access_counts();
    uint32_t primary = make_primary_chain_for_test();
    if (primary) {
        call_method(primary, S_Flip, {0, DDFLIP_WAIT});
        host_access_counts(&a);
        CHECK_EQ(a.flip, 1u);
        CHECK_EQ(a.blt_source, 0u);
    }

    // A source without GPU-dirty pixels is a clean reader.
    ddraw_reset_access_counts();
    blt_for_test(rt, sp, 0, 0, 8, 8, 0);
    host_access_counts(&a);
    CHECK_EQ(a.blt_source, 1u);
    CHECK_EQ(a.clean_reads, 1u);

    // Lock flags are hints: even WRITEONLY exposes a readable guest pointer.
    ddraw_reset_access_counts();
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    call_method(sp, S_Lock, {0, desc, DDLOCK_WAIT | DDLOCK_WRITEONLY, 0});
    call_method(sp, S_Unlock, {0});
    host_access_counts(&a);
    CHECK_EQ(a.lock_write, 1u);
    CHECK_EQ(a.lock_read, 0u);
    CHECK_EQ(a.clean_reads, 1u);

    // texture_load belongs to d3d.cpp, where Load lives, and is Task 3's.
    CHECK_EQ(a.texture_load, 0u);
}

// A Unicode program asks for DirectInputCreateW and, refused, runs with no
// DirectInput at all. The W object is the A object remembering that the two
// structures carrying device names use the DIDEVICEINSTANCEW layout: the
// names are 260 UTF-16 units each, at 40 and 560, and the record is 1100.
static void test_dinput_create_w() {
    cpu_reset();
    uint32_t create = tramp("DINPUT.dll", "DirectInputCreateW");
    CHECK(create != 0);
    CHECK_EQ(call_shim(create, {0x400000, 0x0500, sc(0), 0}), DI_OK);
    uint32_t di = rd32(sc(0));
    CHECK(di != 0);
    uint32_t guid = sc(0x40);
    static const uint8_t MOUSE[16] = {0x60, 0x2B, 0x1D, 0x6F, 0xA0, 0xD5, 0xCF, 0x11,
                                      0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00};
    for (int i = 0; i < 16; ++i)
        wr8(guid + (uint32_t)i, MOUSE[i]);
    CHECK_EQ(call_method(di, DI_CreateDevice, {guid, sc(0x50), 0}), DI_OK);
    uint32_t dev = rd32(sc(0x50));
    CHECK(dev != 0);
    uint32_t info = sc(0x100);
    wr32(info, 1100);
    CHECK_EQ(call_method(dev, 15 /* GetDeviceInfo */, {info}), DI_OK);
    CHECK_EQ(rd16(info + 40), (uint32_t)'M');
    CHECK_EQ(rd16(info + 42), (uint32_t)'o');
    CHECK_EQ(rd16(info + 560), (uint32_t)'M');
    CHECK_EQ(rd8(info + 41), 0u); // UTF-16, not bytes

    // DirectInputCreateEx names the interface by IID; the W IIDs are the A
    // ones plus one, and an IID this shim does not know is E_NOINTERFACE.
    uint32_t ex = tramp("DINPUT.dll", "DirectInputCreateEx");
    uint32_t iid = sc(0x60);
    wr32(iid, 0x9A4CB685u); // IDirectInput7W
    CHECK_EQ(call_shim(ex, {0x400000, 0x0700, iid, sc(0x70), 0}), DI_OK);
    CHECK(rd32(sc(0x70)) != 0);
    wr32(iid, 0x12345678u);
    CHECK_EQ(call_shim(ex, {0x400000, 0x0700, iid, sc(0x70), 0}), 0x80004002u);
    CHECK_EQ(rd32(sc(0x70)), 0u);
}

static void test_dinput_event_notification() {
    cpu_reset();
    uint32_t create = tramp("DINPUT.dll", "DirectInputCreateA");
    CHECK_EQ(call_shim(create, {0x400000, 0x0500, sc(0), 0}), DI_OK);
    uint32_t di = rd32(sc(0));
    CHECK(di != 0);

    // GUID_SysKeyboard, as the game asks for it.
    uint32_t guid = sc(0x40);
    static const uint8_t KBD[16] = {0x61, 0x2B, 0x1D, 0x6F, 0xA0, 0xD5, 0xCF, 0x11,
                                    0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00};
    for (int i = 0; i < 16; ++i)
        wr8(guid + (uint32_t)i, KBD[i]);
    CHECK_EQ(call_method(di, DI_CreateDevice, {guid, sc(0x50), 0}), DI_OK);
    uint32_t dev = rd32(sc(0x50));
    CHECK(dev != 0);

    // An auto-reset event, the kind the game registers.
    uint32_t createev = tramp("KERNEL32.dll", "CreateEventA");
    uint32_t ev = call_shim(createev, {0, 0, 0, 0});
    CHECK(ev != 0);
    CHECK_EQ(call_method(dev, DID_SetEventNotification, {ev}), DI_OK);

    // Taking the event with a zero timeout is how the test asks "is it
    // signalled", and it consumes it exactly as a waiting guest would. Every
    // one of these goes through imports_dispatch, whose scheduling checkpoint
    // is where a signal queued from a host thread is applied.
    uint32_t wait = tramp("KERNEL32.dll", "WaitForSingleObject");
    CHECK_EQ(call_shim(wait, {ev, 0}), 0x102u); // nothing yet

    dinput_host_input_changed();
    CHECK_EQ(call_shim(wait, {ev, 0}), 0u);     // woken
    CHECK_EQ(call_shim(wait, {ev, 0}), 0x102u); // and consumed by the wait

    // The next change signals it again rather than leaving the guest asleep.
    dinput_host_input_changed();
    CHECK_EQ(call_shim(wait, {ev, 0}), 0u);

    // INVALID_HANDLE_VALUE clears the registration, and then nothing is
    // signalled however much input arrives.
    CHECK_EQ(call_method(dev, DID_SetEventNotification, {0xffffffffu}), DI_OK);
    dinput_host_input_changed();
    CHECK_EQ(call_shim(wait, {ev, 0}), 0x102u);

    // Re-registering works, and a reset forgets it: after one the handle would
    // name a kernel object that no longer exists.
    CHECK_EQ(call_method(dev, DID_SetEventNotification, {ev}), DI_OK);
    dinput_host_input_changed();
    CHECK_EQ(call_shim(wait, {ev, 0}), 0u);
    dinput_reset();
    dinput_host_input_changed();
    CHECK_EQ(call_shim(wait, {ev, 0}), 0x102u);
}

// Two devices share one host input state, and host_input_state consumes the
// mouse deltas. So whichever device polls first takes them, and with both
// woken by the same notification the keyboard usually gets there first. That
// is a mouse whose buttons work and whose movement does not: buttons are a
// level the host keeps reporting, motion is a delta reported once.
// An unchanged host state yields no buffered event.
//
// This is a property of the shim, not of the gate: it builds its events by
// diffing the host state against what it saw last time. It is stated here
// because this is where the buffered path lives and this file has no host
// input layer - it feeds the shim through its own g_input.
//
// CONSUMPTION ITSELF IS NOT PROVED HERE, and this test should not be read as
// proving it. That is host_tests.mm's "input gate", which drives the real
// host_key_event and checks all four guest channels including this one, with
// the DirectInput shim linked in.
// DirectInput 8 action mapping, keyboard (run 2, NFS Underground): EnumDevicesBySemantics hands a keyboard to the
// callback for a format with DIKEYBOARD_* semantics, BuildActionMap maps them, and after SetActionMap the state is
// one DWORD per action and a buffered event carries the action's offset and uAppData.
static uint32_t g_sem_calls, g_sem_type, g_sem_dev;
static void test_dinput8_keyboard_action_map() {
    cpu_reset();
    dinput_reset();
    memset(&g_input, 0, sizeof g_input);
    uint32_t create = tramp("DINPUT8.dll", "DirectInput8Create");
    CHECK_EQ(call_shim(create, {0x400000, 0x0800, sc(0x100), sc(0), 0}), DI_OK);
    uint32_t di = rd32(sc(0));
    CHECK(di != 0);
    // Three actions: uAppData 7 on DIK_RETURN (0x1c), 9 on DIK_UP (0xc8), 11 on a genre axis (not a key).
    uint32_t acts = sc(0x200), fmt = sc(0x300);
    gm_zero(acts, 3 * 0x28);
    const uint32_t sem[3] = {0x8100041cu, 0x810004c8u, 0x01008a01u}, app[3] = {7, 9, 11};
    for (uint32_t i = 0; i < 3; ++i) {
        wr32(acts + i * 0x28 + 0, app[i]);
        wr32(acts + i * 0x28 + 4, sem[i]);
    }
    wr32(acts + 1 * 0x28 + 8, 0x4u); // DIA_APPNOMAP, as UG1's race Throttle on DIK_UP: the named key is still mapped
    gm_zero(fmt, 0x148);
    wr32(fmt + 0, 0x148);
    wr32(fmt + 4, 0x28);
    wr32(fmt + 8, 12);
    wr32(fmt + 12, 3);
    wr32(fmt + 16, acts);
    wr32(fmt + 40, 16);
    static uint32_t cb = imports_alloc_trampoline(
        "TEST", "SemanticsCallback",
        [](X86 *c) {
            ++g_sem_calls;
            g_sem_type = rd8(arg(c, 0) + 0x24);
            g_sem_dev = arg(c, 1);
            call_method(g_sem_dev, 1 /* AddRef */, {}); // the game keeps the device
            set_eax(c, 1);
        },
        5);
    g_sem_calls = g_sem_dev = 0;
    CHECK_EQ(call_method(di, 9 /* EnumDevicesBySemantics */, {0, fmt, cb, 0, 0}), DI_OK);
    CHECK_EQ(g_sem_calls, 1u);
    CHECK_EQ(g_sem_type, 0x13u); // DI8DEVTYPE_KEYBOARD: what the game's callback checks
    uint32_t kb = g_sem_dev;
    CHECK(kb != 0);
    CHECK_EQ(call_method(kb, 29 /* BuildActionMap */, {fmt, 0, 2 /* DIDBAM_INITIALIZE */}), DI_OK);
    CHECK_EQ(rd32(acts + 0 * 0x28 + 0x20), 0x1c0cu);
    CHECK_EQ(rd32(acts + 1 * 0x28 + 0x20), 0xc80cu);
    CHECK_EQ(rd32(acts + 0 * 0x28 + 0x24), 0x20u); // DIAH_DEFAULT
    CHECK_EQ(rd32(acts + 2 * 0x28 + 0x24), 0u);    // the axis stays unmapped
    CHECK_EQ(call_method(kb, 30 /* SetActionMap */, {fmt, 0, 0}), DI_OK);
    CHECK_EQ(call_method(kb, DID_Acquire, {}), DI_OK);
    CHECK_EQ(call_method(kb, 30 /* SetActionMap */, {fmt, 0, 0}), DIERR_ACQUIRED);

    uint32_t state = sc(0x480), count = sc(0x4c0), buf = sc(0x500);
    g_input.keys[0xc8] = 0x80; // Up down
    dinput_host_input_changed();
    CHECK_EQ(call_method(kb, DID_GetDeviceState, {12, state}), DI_OK);
    CHECK_EQ(rd32(state + 0), 0u);
    CHECK_EQ(rd32(state + 4), 0x80u);
    CHECK_EQ(rd32(state + 8), 0u);
    wr32(count, 8);
    CHECK_EQ(call_method(kb, DID_GetDeviceData, {DIDEVICEOBJECTDATA_DX8_SIZE, buf, count, 0}), DI_OK);
    CHECK_EQ(rd32(count), 1u);
    CHECK_EQ(rd32(buf + DIDOD_OFF_dwOfs), 4u);     // action 1's offset in the 12-byte data
    CHECK_EQ(rd32(buf + DIDOD_OFF_dwData), 0x80u);
    CHECK_EQ(rd32(buf + DIDOD_OFF_uAppData), 9u);
    g_input.keys[0x1e] = 0x80; // A: no action reads it
    dinput_host_input_changed();
    wr32(count, 8);
    CHECK_EQ(call_method(kb, DID_GetDeviceData, {DIDEVICEOBJECTDATA_DX8_SIZE, buf, count, 0}), DI_OK);
    CHECK_EQ(rd32(count), 0u);
    // A key held across Unacquire / SetActionMap / Acquire sends no press (Windows buffers only changes made while
    // acquired): UG2 swaps maps while ESC opens its pause screen, and a fresh press closed it (run 2). The state
    // still reads it down, and letting go is one release.
    CHECK_EQ(call_method(kb, DID_Unacquire, {}), DI_OK);
    CHECK_EQ(call_method(kb, 30 /* SetActionMap */, {fmt, 0, 0}), DI_OK);
    CHECK_EQ(call_method(kb, DID_Acquire, {}), DI_OK);
    wr32(count, 8);
    CHECK_EQ(call_method(kb, DID_GetDeviceData, {DIDEVICEOBJECTDATA_DX8_SIZE, buf, count, 0}), DI_OK);
    CHECK_EQ(rd32(count), 0u);
    CHECK_EQ(call_method(kb, DID_GetDeviceState, {12, state}), DI_OK);
    CHECK_EQ(rd32(state + 4), 0x80u);
    g_input.keys[0xc8] = 0; // Up released
    dinput_host_input_changed();
    wr32(count, 8);
    CHECK_EQ(call_method(kb, DID_GetDeviceData, {DIDEVICEOBJECTDATA_DX8_SIZE, buf, count, 0}), DI_OK);
    CHECK_EQ(rd32(count), 1u);
    CHECK_EQ(rd32(buf + DIDOD_OFF_dwData), 0u);
    CHECK_EQ(rd32(buf + DIDOD_OFF_uAppData), 9u);

    // A format with no keyboard semantics: nothing serves it, the callback is not called.
    wr32(acts + 0 * 0x28 + 4, 0x01008a01u);
    wr32(acts + 1 * 0x28 + 4, 0x01008a01u);
    g_sem_calls = 0;
    CHECK_EQ(call_method(di, 9, {0, fmt, cb, 0, 0}), DI_OK);
    CHECK_EQ(g_sem_calls, 0u);
    dinput_reset();
}

static void test_unchanged_state_produces_no_buffered_event() {
    cpu_reset();
    dinput_reset();
    memset(&g_input, 0, sizeof g_input);

    uint32_t create = tramp("DINPUT.dll", "DirectInputCreateA");
    CHECK_EQ(call_shim(create, {0x400000, 0x0500, sc(0), 0}), DI_OK);
    uint32_t di = rd32(sc(0));

    uint32_t guid = sc(0x40);
    static const uint8_t KBD[16] = {0x61, 0x2B, 0x1D, 0x6F, 0xA0, 0xD5, 0xCF, 0x11,
                                    0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00};
    for (int i = 0; i < 16; ++i)
        wr8(guid + (uint32_t)i, KBD[i]);
    CHECK_EQ(call_method(di, DI_CreateDevice, {guid, sc(0x50), 0}), DI_OK);
    uint32_t kb = rd32(sc(0x50));
    CHECK(kb != 0);

    uint32_t df = sc(0x70);
    gm_zero(df, 24);
    wr32(df + 0, 24);
    wr32(df + 4, 16);
    wr32(df + 12, 256);
    CHECK_EQ(call_method(kb, DID_SetDataFormat, {df}), DI_OK);
    uint32_t prop = sc(0x80);
    gm_zero(prop, 20);
    wr32(prop + 0, 20);
    wr32(prop + 4, 16);
    wr32(prop + 12, 0);
    wr32(prop + 16, 32);
    CHECK_EQ(call_method(kb, DID_SetProperty, {1 /* DIPROP_BUFFERSIZE */, prop}), DI_OK);
    CHECK_EQ(call_method(kb, DID_Acquire, {}), DI_OK);

    const uint32_t kDikF10 = 0x44;
    uint32_t count = sc(0x90), buf = sc(0xa0);

    // Drain whatever acquiring left, so what follows is only this key.
    wr32(count, 8);
    call_method(kb, DID_GetDeviceData, {16, buf, count, 0});

    // Delivered: the host set the state, so the shim derives one event for it.
    g_input.keys[kDikF10] = 0x80;
    dinput_host_input_changed();
    wr32(count, 8);
    CHECK_EQ(call_method(kb, DID_GetDeviceData, {16, buf, count, 0}), DI_OK);
    CHECK_EQ(rd32(count), 1u);
    CHECK_EQ(rd32(buf + 0), kDikF10);
    CHECK_EQ(rd32(buf + 4), 0x80u);

    // The state is exactly what it was, which is what a consumed press leaves
    // behind upstream: host_input_key was never called. Nothing to derive.
    dinput_host_input_changed();
    wr32(count, 8);
    CHECK_EQ(call_method(kb, DID_GetDeviceData, {16, buf, count, 0}), DI_OK);
    CHECK_EQ(rd32(count), 0u);
}

static void test_mouse_motion_survives_keyboard_poll() {
    cpu_reset();
    dinput_reset();
    uint32_t create = tramp("DINPUT.dll", "DirectInputCreateA");
    CHECK_EQ(call_shim(create, {0x400000, 0x0500, sc(0), 0}), DI_OK);
    uint32_t di = rd32(sc(0));

    uint32_t guid = sc(0x40);
    static const uint8_t KBD[16] = {0x61, 0x2B, 0x1D, 0x6F, 0xA0, 0xD5, 0xCF, 0x11,
                                    0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00};
    for (int i = 0; i < 16; ++i)
        wr8(guid + (uint32_t)i, KBD[i]);
    CHECK_EQ(call_method(di, DI_CreateDevice, {guid, sc(0x50), 0}), DI_OK);
    uint32_t kb = rd32(sc(0x50));

    static const uint8_t MOU[16] = {0x60, 0x2B, 0x1D, 0x6F, 0xA0, 0xD5, 0xCF, 0x11,
                                    0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00};
    for (int i = 0; i < 16; ++i)
        wr8(guid + (uint32_t)i, MOU[i]);
    CHECK_EQ(call_method(di, DI_CreateDevice, {guid, sc(0x58), 0}), DI_OK);
    uint32_t ms = rd32(sc(0x58));
    CHECK(kb != 0 && ms != 0);

    // Both formatted, buffered and acquired, as the game sets them up.
    uint32_t df = sc(0x70);
    gm_zero(df, 24);
    wr32(df + 0, 24);   // dwSize
    wr32(df + 4, 16);   // dwObjSize
    wr32(df + 12, 256); // c_dfDIKeyboard: 256 bytes
    CHECK_EQ(call_method(kb, DID_SetDataFormat, {df}), DI_OK);
    wr32(df + 12, DIMOUSESTATE_SIZE); // c_dfDIMouse: 16 bytes
    CHECK_EQ(call_method(ms, DID_SetDataFormat, {df}), DI_OK);

    uint32_t prop = sc(0x80);
    gm_zero(prop, 20);
    wr32(prop + 0, 20);
    wr32(prop + 4, 16);
    wr32(prop + 12, 0);
    wr32(prop + 16, 32);
    CHECK_EQ(call_method(kb, DID_SetProperty, {1 /* DIPROP_BUFFERSIZE */, prop}), DI_OK);
    CHECK_EQ(call_method(ms, DID_SetProperty, {1, prop}), DI_OK);
    CHECK_EQ(call_method(kb, DID_Acquire, {}), DI_OK);
    CHECK_EQ(call_method(ms, DID_Acquire, {}), DI_OK);

    // The host reports motion and a button down.
    g_input.mouse_dx = 7;
    g_input.mouse_dy = -3;
    g_input.mouse_buttons[0] = 0x80;

    // The keyboard polls FIRST, which is what the shared notification causes.
    uint32_t count = sc(0x90);
    uint32_t buf = sc(0xa0);
    wr32(count, 8);
    CHECK_EQ(call_method(kb, DID_GetDeviceData, {16, buf, count, 0}), DI_OK);

    // The mouse must still see the motion. Before the fix the keyboard's poll
    // had consumed and discarded it, and this came back with only the button.
    wr32(count, 8);
    CHECK_EQ(call_method(ms, DID_GetDeviceData, {16, buf, count, 0}), DI_OK);
    uint32_t n = rd32(count);
    bool saw_x = false, saw_y = false, saw_button = false;
    for (uint32_t i = 0; i < n; ++i) {
        uint32_t ofs = rd32(buf + i * 16 + 0);
        int32_t dat = (int32_t)rd32(buf + i * 16 + 4);
        if (ofs == 0 && dat == 7)
            saw_x = true;
        if (ofs == 4 && dat == -3)
            saw_y = true; // negative deltas sign-extend
        if (ofs == 12 && dat == 0x80)
            saw_button = true;
    }
    CHECK(saw_x);
    CHECK(saw_y);
    CHECK(saw_button);

    // The immediate state reports motion since the last call and clears it.
    // Read once first to clear what the buffered block above accumulated: both
    // views are fed by the same motion, and the immediate one holds it until
    // somebody reads it.
    uint32_t st = sc(0xc0);
    CHECK_EQ(call_method(ms, DID_GetDeviceState, {16, st}), DI_OK);
    g_input.mouse_dx = 5;
    call_method(kb, DID_GetDeviceData, {16, buf, count, 0}); // keyboard polls again
    g_input.mouse_dx = 4;                                    // and more arrives
    CHECK_EQ(call_method(ms, DID_GetDeviceState, {16, st}), DI_OK);
    CHECK_EQ((int32_t)rd32(st + 0), 9); // 5 accumulated + 4, none lost
    CHECK_EQ(call_method(ms, DID_GetDeviceState, {16, st}), DI_OK);
    CHECK_EQ((int32_t)rd32(st + 0), 0); // and reading cleared it
    CHECK_EQ(rd8(st + 12), 0x80u);      // the button is a level, still down

    g_input.mouse_buttons[0] = 0;
    dinput_reset();
}

// A caller can resolve the DirectX 7 factory, observe that version 7 is
// unsupported, then create a legacy object and query its version 4 interface.
static void test_directdraw_create_ex_fallback() {
    cpu_reset();
    const uint32_t create_ex = tramp("DDRAW.dll", "DirectDrawCreateEx");
    CHECK(create_ex != 0);
    if (!create_ex)
        return;
    const uint8_t dd7[16] = {0xC0, 0x5E, 0xE6, 0x15, 0x9C, 0x3B, 0xD2, 0x11,
                             0xB9, 0x2F, 0x00, 0x60, 0x97, 0x97, 0xEA, 0x5B};
    const uint8_t dd4[16] = {0x9A, 0x50, 0x59, 0x9C, 0xBD, 0x39, 0xD1, 0x11,
                             0x8C, 0x4A, 0x00, 0xC0, 0x4F, 0xD9, 0x30, 0xC5};
    const uint32_t iid = sc(0x40), out = sc(0x60);
    memcpy(gm_ptr(iid), dd7, sizeof(dd7));
    wr32(out, 0xdeadbeef);
    wr32(out + 4, 0xcafebabe);
    const uint32_t live = com_live_count();
    CHECK_EQ(call_shim(create_ex, {0, out, iid, 0}), DDERR_UNSUPPORTED);
    CHECK_EQ(rd32(out), 0);
    CHECK_EQ(rd32(out + 4), 0xcafebabe);
    CHECK_EQ(com_live_count(), live);
    CHECK_EQ(call_shim(create_ex, {0, 0, iid, 0}), DDERR_INVALIDPARAMS);
    CHECK_EQ(call_shim(create_ex, {0, out, 0, 0}), DDERR_INVALIDPARAMS);
    CHECK_EQ(call_shim(create_ex, {0, out, iid, 1}), CLASS_E_NOAGGREGATION);
    memcpy(gm_ptr(iid), dd4, sizeof(dd4));
    // DirectDrawCreateEx only accepts IID_IDirectDraw7; older interfaces use
    // DirectDrawCreate followed by QueryInterface.
    CHECK_EQ(call_shim(create_ex, {0, out, iid, 0}), DDERR_INVALIDPARAMS);
    CHECK_EQ(call_shim(tramp("DDRAW.dll", "DirectDrawCreate"), {0, out, 0}), DD_OK);
    const uint32_t dd = rd32(out);
    CHECK(dd != 0);
    CHECK_EQ(call_method(dd, DD_QueryInterface, {iid, out}), S_OK);
    const uint32_t view4 = rd32(out);
    CHECK(view4 != 0);
    CHECK_EQ(call_method(view4, DD_SetCooperativeLevel, {0, 8}), DD_OK);
    CHECK_EQ(call_method(view4, DD_Release, {}), 1);
    CHECK_EQ(call_method(dd, DD_Release, {}), 0);
    CHECK_EQ(com_live_count(), live);
}

// QueryInterface: the DirectDraw object hands out IDirectDraw2 and 4, refuses
// an interface it does not implement, and reaches Direct3D2.
static void test_query_interface() {
    cpu_reset();
    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));

    // The IIDs the game actually asks for, from cross-referencing the EXE.
    struct {
        const char *name;
        uint8_t iid[16];
        bool expect;
    } cases[] = {
        {"IDirectDraw2",
         {0xE0, 0xF3, 0xA6, 0xB3, 0x43, 0x2B, 0xCF, 0x11, 0xA2, 0xDE, 0x00, 0xAA, 0x00, 0xB9, 0x33,
          0x56},
         true},
        {"IDirectDraw4",
         {0x9A, 0x50, 0x59, 0x9C, 0xBD, 0x39, 0xD1, 0x11, 0x8C, 0x4A, 0x00, 0xC0, 0x4F, 0xD9, 0x30,
          0xC5},
         true},
        {"IDirect3D2",
         {0xC1, 0x1E, 0xAE, 0x6A, 0x2A, 0x66, 0xD0, 0x11, 0x88, 0x9D, 0x00, 0xAA, 0x00, 0xBB, 0xB7,
          0x6A},
         true},
        // A DirectDraw object is not a sound buffer.
        {"IDirectSoundBuffer",
         {0x85, 0xFA, 0x9A, 0x27, 0x81, 0x49, 0xCE, 0x11, 0xA5, 0x21, 0x00, 0x20, 0xAF, 0x0B, 0xE5,
          0x60},
         false},
    };
    for (auto &t : cases) {
        uint32_t iid = sc(0x40);
        for (int i = 0; i < 16; ++i)
            wr8(iid + (uint32_t)i, t.iid[i]);
        wr32(sc(0x60), 0xdeadbeef);
        uint32_t hr = call_method(dd, DD_QueryInterface, {iid, sc(0x60)});
        if (t.expect) {
            CHECK_EQ(hr, S_OK);
            CHECK(rd32(sc(0x60)) != 0);
            CHECK(rd32(sc(0x60)) != 0xdeadbeef);
        } else {
            CHECK_EQ(hr, E_NOINTERFACE);
            CHECK_EQ(rd32(sc(0x60)), 0);
        }
    }

    // An unregistered IID is refused rather than matched by accident.
    uint32_t iid = sc(0x40);
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, (uint8_t)(0x11 + i));
    uint32_t hr = call_method(dd, DD_QueryInterface, {iid, sc(0x60)});
    CHECK_EQ(hr, E_NOINTERFACE);

    // Two QueryInterface calls for the same interface return the same pointer,
    // as COM requires for a stable identity.
    uint8_t dd2[16] = {0xE0, 0xF3, 0xA6, 0xB3, 0x43, 0x2B, 0xCF, 0x11,
                       0xA2, 0xDE, 0x00, 0xAA, 0x00, 0xB9, 0x33, 0x56};
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, dd2[i]);
    call_method(dd, DD_QueryInterface, {iid, sc(0x60)});
    uint32_t first = rd32(sc(0x60));
    call_method(dd, DD_QueryInterface, {iid, sc(0x64)});
    CHECK_EQ(rd32(sc(0x64)), first);
    // and IDirectDraw2's vtable is a different one from IDirectDraw's.
    CHECK(rd32(first + COM_OFF_vtbl) != rd32(dd + COM_OFF_vtbl));
}

// EnumDisplayModes offers the native modes, through a real guest
// callback.
static uint32_t g_enum_count = 0;
static uint32_t g_enum_modes[16][3];

static uint32_t g_device_enum_calls;
static bool g_device_enum_wide, g_device_enum_extended;

static void device_enum_callback(X86 *c) {
    ++g_device_enum_calls;
    CHECK_EQ(arg(c, 0), 0u); // Primary display has no GUID.
    const char *expected[] = {"Primary Display Driver", "display"};
    for (unsigned n = 0; n < 2; ++n) {
        uint32_t str = arg(c, n + 1);
        for (unsigned j = 0; j <= strlen(expected[n]); ++j)
            CHECK_EQ(g_device_enum_wide ? rd16(str + j * 2) : rd8(str + j),
                     (uint8_t)expected[n][j]);
    }
    CHECK_EQ(arg(c, 3), 0x12345678u);
    if (g_device_enum_extended)
        CHECK_EQ(arg(c, 4), 0u); // Primary display's HMONITOR is null.
    set_eax(c, 0);               // Stop enumeration after this device.
}

static void test_directdraw_enumeration() {
    cpu_reset();
    uint32_t callbacks[] = {
        imports_alloc_trampoline("TEST", "EnumDevice", device_enum_callback, 4),
        imports_alloc_trampoline("TEST", "EnumDeviceEx", device_enum_callback, 5),
    };
    const char *names[] = {"DirectDrawEnumerateA", "DirectDrawEnumerateW", "DirectDrawEnumerateExA",
                           "DirectDrawEnumerateExW"};
    for (unsigned n = 0; n < 4; ++n) {
        uint32_t target = tramp("DDRAW.dll", names[n]);
        CHECK(target != 0);
        if (!target)
            continue;
        g_device_enum_wide = n & 1;
        g_device_enum_extended = n >= 2;
        uint32_t cb = callbacks[n >= 2];
        // A registration just above the arguments must survive callback and
        // stdcall cleanup. A missing three-argument export leaves these words
        // below ESP and makes a subsequent guest unlink read the callback.
        uint32_t sp = g_cpu.r[4];
        wr32(sp, 0xffffffffu);
        wr32(sp + 4, cb);
        g_device_enum_calls = 0;
        CHECK_EQ(n >= 2 ? call_shim(target, {cb, 0x12345678, 0})
                        : call_shim(target, {cb, 0x12345678}),
                 DD_OK);
        CHECK_EQ(g_device_enum_calls, 1u);
        CHECK_EQ(g_cpu.r[4], sp);
        CHECK_EQ(rd32(sp), 0xffffffffu);
        CHECK_EQ(rd32(sp + 4), cb);
        CHECK_EQ(n >= 2 ? call_shim(target, {0, 0, 0}) : call_shim(target, {0, 0}),
                 DDERR_INVALIDPARAMS);
        if (n >= 2) {
            CHECK_EQ(call_shim(target, {cb, 0x12345678, 7}), DD_OK);
            CHECK_EQ(g_device_enum_calls, 2u);
            CHECK_EQ(call_shim(target, {cb, 0x12345678, 8}), DDERR_INVALIDPARAMS);
            CHECK_EQ(g_device_enum_calls, 2u);
        }
    }
}

static void test_enum_display_modes() {
    cpu_reset();
    reset_ddraw_for_test();
    uint32_t mw = 640, mh = 480, mbpp = 8;
    CHECK(!ddraw_display_mode(&mw, &mh, &mbpp));
    CHECK_EQ(mw, 640);
    CHECK_EQ(mh, 480);
    CHECK_EQ(mbpp, 8);
    uint32_t caps = tramp("GDI32.dll", "GetDeviceCaps");
    // Without a display mode, screen metrics retain the desktop fallback.
    CHECK_EQ(call_shim(tramp("USER32.dll", "GetSystemMetrics"), {0}), 1024u);
    CHECK_EQ(call_shim(tramp("USER32.dll", "GetSystemMetrics"), {1}), 768u);
    CHECK_EQ(call_shim(tramp("USER32.dll", "GetSystemMetrics"), {16}), 1024u);
    CHECK_EQ(call_shim(tramp("USER32.dll", "GetSystemMetrics"), {17}), 768u - 19u);
    uint32_t hdc = call_shim(tramp("USER32.dll", "GetDC"), {0});
    auto check_caps = [&](uint32_t w, uint32_t h, uint32_t bpp) {
        CHECK_EQ(call_shim(caps, {hdc, 8}), w);
        CHECK_EQ(call_shim(caps, {hdc, 10}), h);
        CHECK_EQ(call_shim(caps, {hdc, 12}), 32u);
        CHECK_EQ(call_shim(caps, {hdc, 14}), 1);
        CHECK_EQ(call_shim(caps, {hdc, 38}), 0x2a01u);
        CHECK_EQ(call_shim(caps, {hdc, 104}), bpp == 8 ? 256u : 0u);
        CHECK_EQ(call_shim(caps, {hdc, 24}), 0xffffffffu);
        CHECK_EQ(call_shim(caps, {hdc, 0x2000}), 0);
    };
    check_caps(1024, 768, 32);
    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));

    // The guest callback is a trampoline of our own: recomp_call routes any
    // trampoline address here, so guest_call reaches it exactly as it would
    // reach translated code.
    static uint32_t cb = imports_alloc_trampoline(
        "TEST", "EnumModesCallback",
        [](X86 *c) {
            uint32_t desc = arg(c, 0);
            if (g_enum_count < 16) {
                g_enum_modes[g_enum_count][0] = rd32(desc + DDSD_OFF_dwWidth);
                g_enum_modes[g_enum_count][1] = rd32(desc + DDSD_OFF_dwHeight);
                g_enum_modes[g_enum_count][2] =
                    rd32(desc + DDSD_OFF_ddpfPixelFormat + DDPF_OFF_dwRGBBitCount);
            }
            ++g_enum_count;
            set_eax(c, DDENUMRET_OK);
        },
        2);

    g_enum_count = 0;
    uint32_t hr = call_method(dd, DD_EnumDisplayModes, {0, 0, 0, cb});
    CHECK_EQ(hr, DD_OK);
    CHECK_EQ(g_enum_count, 10);
    CHECK_EQ(g_enum_modes[0][0], 640);
    CHECK_EQ(g_enum_modes[0][1], 480);
    CHECK_EQ(g_enum_modes[0][2], 8);
    CHECK_EQ(g_enum_modes[5][0], 1024);
    CHECK_EQ(g_enum_modes[5][2], 16);
    CHECK_EQ(g_enum_modes[9][0], 3840);
    CHECK_EQ(g_enum_modes[9][1], 2160);
    CHECK_EQ(g_enum_modes[9][2], 16);
    CHECK_EQ(call_method(dd, DD_SetDisplayMode, {800, 600, 8}), DD_OK);
    CHECK(ddraw_display_mode(&mw, &mh, &mbpp));
    CHECK_EQ(mw, 800);
    CHECK_EQ(mh, 600);
    CHECK_EQ(mbpp, 8);
    check_caps(800, 600, 8);
    CHECK_EQ(call_method(dd, DD_SetDisplayMode, {3840, 2160, 16}), DD_OK);
    CHECK(ddraw_display_mode(&mw, &mh, &mbpp));
    CHECK_EQ(mw, 3840);
    CHECK_EQ(mh, 2160);
    CHECK_EQ(mbpp, 16);
    check_caps(3840, 2160, 16);
    os_setenv("RECOMP_SMOKE_DRAWABLE", "800x600");
    win32_display_mode(&mw, &mh, &mbpp);
    CHECK_EQ(mw, 3840);
    CHECK_EQ(mh, 2160);
    CHECK_EQ(mbpp, 16);
    os_unsetenv("RECOMP_SMOKE_DRAWABLE");

    // A restricted enumeration returns only the matching modes.
    uint32_t match = sc(0x100);
    gm_zero(match, DDSD_SIZE);
    wr32(match + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(match + DDSD_OFF_dwFlags, DDSD_WIDTH | DDSD_HEIGHT);
    wr32(match + DDSD_OFF_dwWidth, 800);
    wr32(match + DDSD_OFF_dwHeight, 600);
    g_enum_count = 0;
    call_method(dd, DD_EnumDisplayModes, {0, match, 0, cb});
    CHECK_EQ(g_enum_count, 2);

    // An unoffered mode is refused rather than silently accepted.
    hr = call_method(dd, DD_SetDisplayMode, {1600, 1200, 32});
    CHECK_EQ(hr, DDERR_INVALIDPARAMS);
    CHECK(ddraw_display_mode(&mw, &mh, &mbpp));
    CHECK_EQ(mw, 3840);
    CHECK_EQ(mh, 2160);
    CHECK_EQ(mbpp, 16);
    check_caps(3840, 2160, 16);
    call_shim(tramp("USER32.dll", "ReleaseDC"), {0, hdc});
}

static std::vector<uint32_t> g_ddraw_mode_messages;
static uint32_t g_ddraw_mode_width, g_ddraw_mode_height, g_ddraw_mode_depth;
static void ddraw_mode_wndproc(X86 *c) {
    uint32_t hwnd = arg(c, 0), msg = arg(c, 1), wp = arg(c, 2), lp = arg(c, 3);
    if (msg == 0x47 || msg == 5 || msg == 0x7e)
        g_ddraw_mode_messages.push_back(msg);
    if (msg == 5) {
        // Games recompute their presentation rectangle from these metrics in
        // WM_SIZE, including when a movie returns to the same display mode.
        g_ddraw_mode_width = guest_call(c, tramp("USER32.dll", "GetSystemMetrics"), 0);
        g_ddraw_mode_height = guest_call(c, tramp("USER32.dll", "GetSystemMetrics"), 1);
        CHECK_EQ(lp, g_ddraw_mode_width | (g_ddraw_mode_height << 16));
        // An unchanged layout request from WM_SIZE must not recurse.
        uint32_t args[] = {hwnd, 0, 0, 0, g_ddraw_mode_width, g_ddraw_mode_height, 0x14};
        guest_call(c, tramp("USER32.dll", "SetWindowPos"), args, 7);
    }
    if (msg == 0x7e)
        g_ddraw_mode_depth = wp;
    set_eax(c, guest_call(c, tramp("USER32.dll", "DefWindowProcA"), hwnd, msg, wp, lp));
}

static void test_exclusive_ddraw_notifies_window_mode() {
    cpu_reset();
    reset_ddraw_for_test();
    uint32_t wc = sc(0x200), name = sc(0x280), rect = sc(0x240);
    gm_zero(wc, 40);
    wr32(wc + 4, imports_alloc_trampoline("TEST", "ddraw_mode_wndproc", ddraw_mode_wndproc, 4));
    gm_put_str(name, "DirectDrawModeTarget", 32);
    wr32(wc + 36, name);
    CHECK(call_shim(tramp("USER32.dll", "RegisterClassA"), {wc}) != 0);
    uint32_t hwnd = call_shim(tramp("USER32.dll", "CreateWindowExA"),
                              {0, name, name, 0x80000000u, 20, 30, 320, 240, 0, 0, 0, 0});
    CHECK(hwnd != 0);
    call_shim(tramp("DDRAW.dll", "DirectDrawCreate"), {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));
    CHECK_EQ(call_method(dd, DD_SetCooperativeLevel, {hwnd, 0x11}), DD_OK);
    for (int repeat = 0; repeat != 2; ++repeat) {
        g_ddraw_mode_messages.clear();
        g_ddraw_mode_width = g_ddraw_mode_height = g_ddraw_mode_depth = 0;
        CHECK_EQ(call_method(dd, DD_SetDisplayMode, {640, 480, 16}), DD_OK);
        CHECK(g_ddraw_mode_messages == std::vector<uint32_t>({0x47, 5, 0x7e}));
        CHECK_EQ(g_ddraw_mode_width, 640u);
        CHECK_EQ(g_ddraw_mode_height, 480u);
        CHECK_EQ(g_ddraw_mode_depth, 16u);
        call_shim(tramp("USER32.dll", "GetWindowRect"), {hwnd, rect});
        CHECK_EQ(rd32(rect), 0u);
        CHECK_EQ(rd32(rect + 4), 0u);
        CHECK_EQ(rd32(rect + 8), 640u);
        CHECK_EQ(rd32(rect + 12), 480u);
    }
    g_ddraw_mode_messages.clear();
    CHECK_EQ(call_method(dd, DD_SetDisplayMode, {123, 456, 16}), DDERR_INVALIDPARAMS);
    CHECK(g_ddraw_mode_messages.empty());
    CHECK_EQ(call_method(dd, DD_SetCooperativeLevel, {hwnd, 8 /* NORMAL */}), DD_OK);
    CHECK_EQ(call_method(dd, DD_SetDisplayMode, {800, 600, 16}), DD_OK);
    CHECK(g_ddraw_mode_messages.empty());
    call_shim(tramp("USER32.dll", "GetWindowRect"), {hwnd, rect});
    CHECK_EQ(rd32(rect + 8), 640u);
    CHECK_EQ(rd32(rect + 12), 480u);
    CHECK_EQ(call_method(dd, DD_Release, {}), 0u);
    call_shim(tramp("USER32.dll", "DestroyWindow"), {hwnd});
}

// Releasing the DirectDraw object that set the mode, or RestoreDisplayMode,
// puts the desktop back: the screen metrics return to the fallback until the
// next SetDisplayMode. A game that changes resolution by releasing and
// re-creating DirectDraw reads its screen bounds in between.
static void test_release_restores_desktop_mode() {
    cpu_reset();
    reset_ddraw_for_test();
    uint32_t metrics = tramp("USER32.dll", "GetSystemMetrics");
    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    uint32_t mw = 0, mh = 0, mbpp = 0;

    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));
    CHECK_EQ(call_method(dd, DD_SetDisplayMode, {640, 480, 16}), DD_OK);
    CHECK_EQ(call_shim(metrics, {0}), 640u);
    CHECK_EQ(call_shim(metrics, {1}), 480u);
    CHECK_EQ(call_method(dd, DD_Release, {}), 0u);
    CHECK(!ddraw_display_mode(&mw, &mh, &mbpp));
    CHECK_EQ(call_shim(metrics, {0}), 1024u);
    CHECK_EQ(call_shim(metrics, {1}), 768u);

    // The next object sets the next mode; a game switching from a 640-wide
    // mode to an 800-wide one must not read 640 as the width in between.
    call_shim(create, {0, sc(0), 0});
    dd = rd32(sc(0));
    CHECK_EQ(call_method(dd, DD_SetDisplayMode, {800, 600, 16}), DD_OK);
    CHECK(ddraw_display_mode(&mw, &mh, &mbpp));
    CHECK_EQ(mw, 800);
    CHECK_EQ(call_shim(metrics, {0}), 800u);
    CHECK_EQ(call_method(dd, DD_RestoreDisplayMode, {}), DD_OK);
    CHECK(!ddraw_display_mode(&mw, &mh, &mbpp));
    CHECK_EQ(call_shim(metrics, {0}), 1024u);
    CHECK_EQ(call_shim(metrics, {1}), 768u);
    // Restoring twice, or releasing an object that never set a mode, is
    // harmless.
    CHECK_EQ(call_method(dd, DD_RestoreDisplayMode, {}), DD_OK);
    CHECK_EQ(call_method(dd, DD_SetDisplayMode, {640, 480, 16}), DD_OK);
    CHECK_EQ(call_shim(metrics, {0}), 640u);
    call_shim(create, {0, sc(0x10), 0});
    uint32_t other = rd32(sc(0x10));
    CHECK_EQ(call_method(other, DD_Release, {}), 0u);
    CHECK_EQ(call_shim(metrics, {0}), 640u);
    CHECK_EQ(call_method(dd, DD_Release, {}), 0u);
    CHECK_EQ(call_shim(metrics, {0}), 1024u);
}

// RECOMP_DDRAW_MODES replaces the offered set, and the SAME table decides what
// SetDisplayMode accepts. That is the whole point of the amendment: a mode the
// game was offered and then refused, or refused and then offered, is a
// contradiction the guest cannot recover from, and one table is what makes it
// impossible.
static void enum_modes_now(uint32_t dd, uint32_t cb) {
    g_enum_count = 0;
    memset(g_enum_modes, 0, sizeof g_enum_modes);
    CHECK_EQ(call_method(dd, DD_EnumDisplayModes, {0, 0, 0, cb}), DD_OK);
}

#ifdef _WIN32
extern "C" char ***__p__environ(void); // the CRT's; strict C++ hides _environ
static char **process_environ() {
    return *__p__environ();
}
#else
extern char **environ;
static char **process_environ() {
    return environ;
}
#endif

static void test_classic_probe_surface_creation() {
    // Match mode_probe.py: scrub inherited POP tuning, preserve runtime paths,
    // offer both boot depths plus the candidate, then reapply after Classic init.
    struct ProbeEnvironment {
        std::map<std::string, std::string> saved;
        static std::map<std::string, std::string> take() {
            std::map<std::string, std::string> values;
            for (char **p = process_environ(); *p; ++p) {
                std::string entry(*p);
                if (entry.starts_with("POPM_") || entry.starts_with("POP_SMOKE_") ||
                    entry.starts_with("POP_HOST_") || entry.starts_with("POP_RECOMP_")) {
                    auto equal = entry.find('=');
                    values.emplace(entry.substr(0, equal), entry.substr(equal + 1));
                }
            }
            for (const auto &[key, value] : values)
                os_unsetenv(key.c_str());
            return values;
        }
        ProbeEnvironment() : saved(take()) {}
        ~ProbeEnvironment() {
            take();
            for (const auto &[key, value] : saved)
                os_setenv(key.c_str(), value.c_str());
            ddraw_reset_modes();
        }
    } environment;
    char root[4096];
    CHECK(os_getcwd(root, sizeof root) == 0);
    const uint32_t sizes[][2] = {{640, 480},   {800, 600},   {1024, 768},  {1280, 960},
                                 {1600, 1200}, {1920, 1440}, {2560, 1920}, {3840, 2880},
                                 {1280, 720},  {1920, 1080}, {2560, 1440}, {3840, 2160}};
    for (const auto &size : sizes)
        for (uint32_t depth : {8u, 16u}) {
            std::string target = std::to_string(size[0]) + "x" + std::to_string(size[1]) + "x" +
                                 std::to_string(depth);
            std::string list = "640x480x8,640x480x16";
            if (size[0] != 640 || size[1] != 480)
                list += "," + target;
            std::string path = std::string(root) + "/build/recomp/mode-probe/" + target;
            os_setenv("RECOMP_DDRAW_MODES", list.c_str());
            os_setenv("RECOMP_NO_MODS", "1");
            os_setenv("RECOMP_PIN_CLOCK", "1");
            os_setenv("RECOMP_SCRIPT", (path + "/probe.script").c_str());
            os_setenv("RECOMP_HOST_DUMP_DIR", path.c_str());
            os_setenv("RECOMP_SMOKE_CLASSIC_PROBE", target.c_str());
            ddraw_reset_modes();
            CHECK_EQ(ddraw_set_modes(recomp_env("DDRAW_MODES")), 1);
            cpu_reset();
            CHECK_EQ(call_shim(tramp("DDRAW.dll", "DirectDrawCreate"), {0, sc(0), 0}), DD_OK);
            uint32_t dd = rd32(sc(0));
            CHECK_EQ(call_method(dd, DD_SetCooperativeLevel,
                                 {0x20004, DDSCL_EXCLUSIVE | DDSCL_FULLSCREEN}),
                     DD_OK);
            // The real log first creates a desktop primary before SetDisplayMode,
            // then boots through both depths. No requested pixel format is supplied.
            for (uint32_t boot_depth : {0u, 8u, 16u}) {
                if (boot_depth)
                    CHECK_EQ(call_method(dd, DD_SetDisplayMode, {640, 480, boot_depth}), DD_OK);
                uint32_t desc = sc(0x200);
                gm_zero(desc, DDSD_SIZE);
                wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
                wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_BACKBUFFERCOUNT);
                wr32(desc + DDSD_OFF_ddsCaps,
                     DDSCAPS_PRIMARYSURFACE | DDSCAPS_COMPLEX | DDSCAPS_FLIP);
                wr32(desc + DDSD_OFF_dwBackBufferCount, 1);
                // An invalid out-pointer must be reported as a primary request,
                // and the next valid request must still succeed.
                if (!boot_depth)
                    CHECK_EQ(call_method(dd, DD_CreateSurface, {desc, 0, 0}), DDERR_INVALIDPARAMS);
                CHECK_EQ(call_method(dd, DD_CreateSurface, {desc, sc(0x10), 0}), DD_OK);
                CHECK(rd32(sc(0x10)) != 0);
                CHECK_EQ(rd32(desc + DDSD_OFF_dwWidth), 640u);
                CHECK_EQ(rd32(desc + DDSD_OFF_dwHeight), 480u);
                CHECK_EQ(rd32(desc + DDSD_OFF_ddpfPixelFormat + DDPF_OFF_dwRGBBitCount),
                         boot_depth ? boot_depth : 8u);
                CHECK_EQ(call_method(rd32(sc(0x10)), S_Release, {}), 0u);
            }
            // PVRC is an optional PowerVR texture candidate, not a primary format.
            // Its refusal must leave ordinary RGB/offscreen creation working.
            uint32_t desc = sc(0x200), pf = desc + DDSD_OFF_ddpfPixelFormat;
            gm_zero(desc, DDSD_SIZE);
            wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
            wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT | DDSD_PIXELFORMAT);
            wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_TEXTURE);
            wr32(desc + DDSD_OFF_dwWidth, 64);
            wr32(desc + DDSD_OFF_dwHeight, 64);
            wr32(pf + DDPF_OFF_dwSize, DDPF_SIZE);
            wr32(pf + DDPF_OFF_dwFlags, DDPF_FOURCC);
            wr32(pf + DDPF_OFF_dwFourCC, 0x43525650u);
            CHECK_EQ(call_method(dd, DD_CreateSurface, {desc, sc(0x10), 0}),
                     DDERR_INVALIDPIXELFORMAT);
            CHECK_EQ(rd32(sc(0x10)), 0u);
            wr32(pf + DDPF_OFF_dwFlags, DDPF_RGB);
            wr32(pf + DDPF_OFF_dwFourCC, 0);
            wr32(pf + DDPF_OFF_dwRGBBitCount, 16);
            CHECK_EQ(call_method(dd, DD_CreateSurface, {desc, sc(0x10), 0}), DD_OK);
            CHECK_EQ(rd32(desc + DDSD_OFF_ddpfPixelFormat + DDPF_OFF_dwRBitMask), 0xf800u);
            CHECK_EQ(call_method(rd32(sc(0x10)), S_Release, {}), 0u);
            CHECK_EQ(call_method(dd, DD_SetDisplayMode, {size[0], size[1], depth}), DD_OK);
            CHECK_EQ(call_method(dd, 2 /* Release */, {}), 0u);
        }
}

static void test_configurable_display_modes() {
    static uint32_t cb = imports_alloc_trampoline(
        "TEST", "EnumModesCallback2",
        [](X86 *c) {
            uint32_t desc = arg(c, 0);
            if (g_enum_count < 16) {
                g_enum_modes[g_enum_count][0] = rd32(desc + DDSD_OFF_dwWidth);
                g_enum_modes[g_enum_count][1] = rd32(desc + DDSD_OFF_dwHeight);
                g_enum_modes[g_enum_count][2] =
                    rd32(desc + DDSD_OFF_ddpfPixelFormat + DDPF_OFF_dwRGBBitCount);
            }
            ++g_enum_count;
            set_eax(c, DDENUMRET_OK);
        },
        2);

    auto fresh_dd = [&]() {
        cpu_reset();
        uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
        call_shim(create, {0, sc(0), 0});
        return rd32(sc(0));
    };

    // A list of two, one of which the built-in table does not contain.
    os_setenv("RECOMP_DDRAW_MODES", "640x480x8,1280x960x16");
    ddraw_reset_modes();
    uint32_t dd = fresh_dd();
    enum_modes_now(dd, cb);
    CHECK_EQ(g_enum_count, 2u);
    CHECK_EQ(g_enum_modes[0][0], 640u);
    CHECK_EQ(g_enum_modes[0][1], 480u);
    CHECK_EQ(g_enum_modes[0][2], 8u);
    CHECK_EQ(g_enum_modes[1][0], 1280u);
    CHECK_EQ(g_enum_modes[1][1], 960u);
    CHECK_EQ(g_enum_modes[1][2], 16u);
    // Accepted because it is offered...
    CHECK_EQ(call_method(dd, DD_SetDisplayMode, {1280, 960, 16}), DD_OK);
    // ...and 800x600, which the built-in table has and this list does not, is
    // now refused. This is the assertion that proves one table and not two.
    CHECK_EQ(call_method(dd, DD_SetDisplayMode, {800, 600, 16}), DDERR_INVALIDPARAMS);

    // A single mode, which is how a display test forces the game's hand.
    os_setenv("RECOMP_DDRAW_MODES", "1920x1080x16");
    ddraw_reset_modes();
    dd = fresh_dd();
    enum_modes_now(dd, cb);
    CHECK_EQ(g_enum_count, 1u);
    CHECK_EQ(g_enum_modes[0][0], 1920u);
    CHECK_EQ(call_method(dd, DD_SetDisplayMode, {1920, 1080, 16}), DD_OK);
    CHECK_EQ(call_method(dd, DD_SetDisplayMode, {640, 480, 8}), DDERR_INVALIDPARAMS);

    // Every way of being malformed keeps the built-in list, whole. A list
    // honoured up to its first mistake would offer a set nobody wrote down.
    const char *bad[] = {
        "",                      // set to nothing is the same as unset
        "640x480",               // a mode needs a depth
        "640x480x8,",            // a trailing comma is a missing mode
        "640x480x8 1280x960x16", // a space is not a separator
        "640x480x24",            // a depth the enumeration cannot describe
        "640x480x0",             // nor can it describe none
        "0x480x8",               // a mode with no width is not a mode
        "abcx480x8",             // and neither is a word
        "640x480x8,junk",        // one bad entry rejects the whole list
    };
    for (const char *spec : bad) {
        os_setenv("RECOMP_DDRAW_MODES", spec);
        ddraw_reset_modes();
        dd = fresh_dd();
        enum_modes_now(dd, cb);
        if (g_enum_count != 10)
            printf("  RECOMP_DDRAW_MODES=\"%s\" gave %u modes, wanted the built-in 10\n", spec,
                   g_enum_count);
        CHECK_EQ(g_enum_count, 10u);
        CHECK_EQ(call_method(dd, DD_SetDisplayMode, {800, 600, 16}), DD_OK);
    }

    // ddraw_set_modes does the same job at runtime, which is what a display
    // script uses. It exists because the variable cannot do this safely: the
    // game selects 640x480x8 at startup without asking what is available, so
    // a variable that omits it has that call refused and the guest walks into
    // a SIGBUS. Setting the table after boot has no such problem.
    os_unsetenv("RECOMP_DDRAW_MODES");
    ddraw_reset_modes();
    dd = fresh_dd();
    CHECK_EQ(call_method(dd, DD_SetDisplayMode, {640, 480, 8}), DD_OK);
    CHECK_EQ(ddraw_set_modes("1280x960x16"), 1);
    enum_modes_now(dd, cb);
    CHECK_EQ(g_enum_count, 1u);
    CHECK_EQ(g_enum_modes[0][0], 1280u);
    CHECK_EQ(call_method(dd, DD_SetDisplayMode, {1280, 960, 16}), DD_OK);

    // A malformed runtime spec changes nothing at all, so a typo cannot
    // narrow the offered set behind the script's back.
    CHECK_EQ(ddraw_set_modes("1280x960"), 0);
    enum_modes_now(dd, cb);
    CHECK_EQ(g_enum_count, 1u);
    CHECK_EQ(g_enum_modes[0][0], 1280u);

    // And an empty spec puts the built-in list back.
    CHECK_EQ(ddraw_set_modes(nullptr), 1);
    enum_modes_now(dd, cb);
    CHECK_EQ(g_enum_count, 10u);

    // Unset is the same list as well, which is the state every other test runs in -
    // so this one has to leave it that way.
    os_unsetenv("RECOMP_DDRAW_MODES");
    ddraw_reset_modes();
    dd = fresh_dd();
    enum_modes_now(dd, cb);
    CHECK_EQ(g_enum_count, 10u);
    CHECK_EQ(g_enum_modes[5][0], 1024u);
    CHECK_EQ(call_method(dd, DD_SetDisplayMode, {1280, 960, 16}), DDERR_INVALIDPARAMS);
    CHECK_EQ(ddraw_add_mode(1280, 960, 16), 1);
    CHECK_EQ(ddraw_add_mode(1280, 960, 16), 1); // duplicate request adds no duplicate row
    CHECK_EQ(ddraw_add_mode(1920, 1080, 32), 0);
    enum_modes_now(dd, cb);
    CHECK_EQ(g_enum_count, 11u);
    CHECK_EQ(call_method(dd, DD_SetDisplayMode, {1280, 960, 16}), DD_OK);
    CHECK_EQ(call_method(dd, DD_SetDisplayMode, {3840, 2160, 16}), DD_OK);
    ddraw_reset_modes();
}

// DX6 ABI path: real vtable slots and stdcall cleanup, independent of a game.
// Literal IIDs/slot numbers keep this from mirroring the adapter's declarations.
static void test_d3d3_pipeline() {
    cpu_reset();
    g_draws.clear();
    uint32_t target = make_render_target_for_test(640, 480, 16);
    uint32_t dd = rec_dd(), iid = sc(0x40), out = sc(0x60);
    const uint8_t iid3[16] = {0x40, 0x32, 0x22, 0xbb, 0x2b, 0xe7, 0xd0, 0x11,
                              0xa9, 0xb4, 0,    0xaa, 0,    0xc0, 0x99, 0x3e};
    memcpy(gm_ptr(iid), iid3, 16);
    CHECK_EQ(call_method(dd, 0, {iid, out}), S_OK);
    uint32_t d3d = rd32(out);
    CHECK_EQ(com_iface_of(d3d), IF_D3D3);
    const uint8_t hal[16] = {0xe0, 0x3d, 0xe6, 0x84, 0xaa, 0x46, 0xcf, 0x11,
                             0x81, 0x6f, 0,    0,    0xc0, 0x20, 0x15, 0x6e};
    memcpy(gm_ptr(iid), hal, 16);
    CHECK_EQ(call_method(d3d, 8, {iid, target, out, 0}), D3D_OK_);
    uint32_t dev = rd32(out);
    CHECK_EQ(com_iface_of(dev), IF_D3DDEVICE3);
    CHECK_EQ(call_method(dev, 11, {out}), D3D_OK_);
    CHECK_EQ(rd32(out), d3d);
    call_method(rd32(out), 2, {});
    CHECK_EQ(call_method(dev, 15, {out}), D3D_OK_);
    CHECK_EQ(com_iface_of(rd32(out)), IF_DDSURFACE4);
    call_method(rd32(out), 2, {});

    CHECK_EQ(call_method(d3d, 6, {out, 0}), D3D_OK_);
    uint32_t vp = rd32(out);
    CHECK_EQ(com_iface_of(vp), IF_D3DVIEWPORT3);
    CHECK_EQ(call_method(dev, 5, {vp}), D3D_OK_);
    uint32_t vpd = sc(0x100);
    gm_zero(vpd, 44);
    wr32(vpd, 44);
    wr32(vpd + 12, 640);
    wr32(vpd + 16, 480);
    wrf32(vpd + 40, 1.0f);
    CHECK_EQ(call_method(vp, 17, {vpd}), D3D_OK_);
    CHECK_EQ(call_method(dev, 12, {vp}), D3D_OK_);
    CHECK_EQ(call_method(dev, 13, {out}), D3D_OK_);
    CHECK_EQ(rd32(out), vp);
    call_method(vp, 2, {});

    static uint32_t formats = imports_alloc_trampoline(
        "TEST", "DX6PixelFormat",
        [](X86 *c) {
            uint32_t pf = arg(c, 0), count = arg(c, 1);
            CHECK_EQ(rd32(pf), 32u); // DDPIXELFORMAT, not a DDSURFACEDESC.
            CHECK((rd32(pf + 4) & (DDPF_RGB | DDPF_ZBUFFER)) != 0);
            wr32(count, rd32(count) + 1);
            set_eax(c, DDENUMRET_OK);
        },
        2);
    wr32(out, 0);
    CHECK_EQ(call_method(dev, 8, {formats, out}), D3D_OK_);
    CHECK_EQ(rd32(out), 6u);
    wr32(out, 0);
    CHECK_EQ(call_method(d3d, 10, {iid, formats, out}), D3D_OK_);
    CHECK_EQ(rd32(out), 1u);

    uint32_t surface = rec_make_surface(4, 4, 16, DDSCAPS_TEXTURE);
    ComObj *tex = com_this(surface);
    uint32_t texid = tex->id, texture = com_view(tex, IF_D3DTEXTURE2);
    CHECK_EQ(call_method(dev, 38, {0, texture}), D3D_OK_);
    CHECK_EQ(tex->refs, 2);
    CHECK_EQ(call_method(dev, 38, {0, texture}), D3D_OK_);
    CHECK_EQ(tex->refs, 2); // self-bind does not release the resource first.
    CHECK_EQ(call_method(dev, 37, {0, out}), D3D_OK_);
    CHECK_EQ(rd32(out), texture);
    call_method(texture, 2, {});
    CHECK_EQ(tex->refs, 2);
    CHECK_EQ(call_method(dev, 40, {0, 4, 4}), D3D_OK_);  // alpha modulation
    CHECK_EQ(call_method(dev, 40, {0, 12, 3}), D3D_OK_); // clamp both axes
    CHECK_EQ(call_method(dev, 40, {0, 16, 2}), D3D_OK_); // linear magnification
    CHECK_EQ(call_method(dev, 39, {0, 16, out}), D3D_OK_);
    CHECK_EQ(rd32(out), 2u);
    CHECK_EQ(call_method(dev, 41, {out}), D3D_OK_);
    CHECK_EQ(rd32(out), 1u);
    ComObj *device = com_this(dev);
    CHECK_EQ(device->render_state[21], 4u);
    CHECK_EQ(device->render_state[44], 3u);
    CHECK_EQ(device->render_state[45], 3u);
    CHECK_EQ(device->render_state[27], 0u); // NORMALIZENORMALS is 143, not alpha blend.
    CHECK_EQ(device->render_state[143], 1u);
    CHECK_EQ(device->render_state[3], 1u); // TEXTUREADDRESS is 3, not COLORKEYENABLE.
    CHECK_EQ(device->render_state[41], 0u);
    CHECK_EQ(call_method(dev, 38, {1, texture}), DDERR_INVALIDPARAMS);

    uint32_t verts = sc(0x1800);
    gm_zero(verts, 3 * 32);
    for (uint32_t i = 0; i < 3; ++i) {
        wrf32(verts + i * 32, float(i * 20));
        wrf32(verts + i * 32 + 4, float(i == 1 ? 50 : 10));
        wrf32(verts + i * 32 + 12, 1.0f);
        wr32(verts + i * 32 + 16, 0xffffffff);
    }
    CHECK_EQ(call_method(dev, 9, {}), D3D_OK_);
    CHECK_EQ(call_method(dev, 28, {4, 0x1c4, verts, 3, 0}), D3D_OK_);
    CHECK_EQ(g_draws.size(), 1u);
    if (!g_draws.empty()) {
        CHECK_EQ(g_draws.back().vertex_type, D3DVT_TLVERTEX);
        CHECK_EQ(g_draws.back().texture_handle, tex->texture_handle);
        CHECK_EQ(g_draws.back().vertices.size(), 96u);
    }
    CHECK_EQ(call_method(dev, 28, {4, 0xdead, verts, 3, 0}), DDERR_UNSUPPORTED);
    CHECK_EQ(g_draws.size(), 1u);
    HostFrameHandle frame = host_frame_current();
    uint32_t before = host_frame_draw_count(frame);
    CHECK_EQ(call_method(vp, 20, {0, 0, 3, 0xff123456, 0x3f000000, 0}), D3D_OK_);
    const HostD3DDrawSnapshot *clear = host_frame_draw(frame, before);
    CHECK(clear != nullptr);
    if (clear) {
        CHECK_EQ(clear->clear_color, 0xff123456u);
        CHECK(clear->clear_z == 0.5f);
    }
    CHECK_EQ(call_method(dev, 10, {}), D3D_OK_);
    call_method(surface, 2, {}); // device keeps the texture alive
    CHECK(com_get(texid) != nullptr);
    CHECK_EQ(call_method(dev, 38, {0, 0}), D3D_OK_);
    CHECK(com_get(texid) == nullptr);
    call_method(dev, 2, {});
    call_method(vp, 2, {});
    call_method(target, 2, {});
    call_method(d3d, 2, {});
}

// The Direct3D path the game takes: QueryInterface for IDirect3D2, FindDevice
// for the HAL device, CreateDevice on a 3D back buffer, a viewport, then a
// scene with a textured indexed draw.
static void test_d3d_pipeline() {
    cpu_reset();
    g_draws.clear();
    g_textures.clear();
    g_begin_scene = g_end_scene = 0;

    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));
    call_method(dd, DD_SetDisplayMode, {640, 480, 16});

    // IDirect3D2 off the DirectDraw object.
    uint32_t iid = sc(0x40);
    uint8_t d3d2[16] = {0xC1, 0x1E, 0xAE, 0x6A, 0x2A, 0x66, 0xD0, 0x11,
                        0x88, 0x9D, 0x00, 0xAA, 0x00, 0xBB, 0xB7, 0x6A};
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, d3d2[i]);
    uint32_t hr = call_method(dd, DD_QueryInterface, {iid, sc(0x60)});
    CHECK_EQ(hr, S_OK);
    uint32_t d3d = rd32(sc(0x60));
    CHECK(d3d != 0);

    // FindDevice for the HAL device, the way init_d3d does it.
    uint32_t search = sc(0x100);
    uint32_t result = sc(0x200);
    gm_zero(search, D3DFDS_SIZE);
    wr32(search + D3DFDS_OFF_dwSize, D3DFDS_SIZE);
    wr32(search + D3DFDS_OFF_dwFlags, D3DFDS_GUID);
    uint8_t hal[16] = {0xE0, 0x3D, 0xE6, 0x84, 0xAA, 0x46, 0xCF, 0x11,
                       0x81, 0x6F, 0x00, 0x00, 0xC0, 0x20, 0x15, 0x6E};
    for (int i = 0; i < 16; ++i)
        wr8(search + D3DFDS_OFF_guid + (uint32_t)i, hal[i]);
    gm_zero(result, D3DFDR_SIZE);
    wr32(result + D3DFDR_OFF_dwSize, D3DFDR_SIZE);
    hr = call_method(d3d, D3D_FindDevice, {search, result});
    CHECK_EQ(hr, D3D_OK_);
    // The game checks the returned GUID is non-zero and that the triangle
    // caps advertise the blend and alpha-compare bits it needs.
    CHECK(rd32(result + D3DFDR_OFF_guid) != 0);
    uint32_t tri = result + D3DFDR_OFF_ddHwDesc + D3DDD_OFF_dpcTriCaps;
    CHECK((rd32(tri + D3DPC_OFF_dwDestBlendCaps) & 1u) != 0);
    CHECK((rd32(tri + D3DPC_OFF_dwSrcBlendCaps) & 0x1000u) != 0);
    CHECK((rd32(tri + D3DPC_OFF_dwAlphaCmpCaps) & 2u) != 0);

    // A back buffer marked as a 3D device target.
    uint32_t desc = sc(0x400);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_OFFSCREENPLAIN | DDSCAPS_3DDEVICE);
    wr32(desc + DDSD_OFF_dwWidth, 640);
    wr32(desc + DDSD_OFF_dwHeight, 480);
    call_method(dd, DD_CreateSurface, {desc, sc(8), 0});
    uint32_t target = rd32(sc(8));
    CHECK(target != 0);

    uint32_t guid = sc(0x500);
    for (int i = 0; i < 16; ++i)
        wr8(guid + (uint32_t)i, hal[i]);
    hr = call_method(d3d, D3D_CreateDevice, {guid, target, sc(12)});
    CHECK_EQ(hr, D3D_OK_);
    uint32_t dev = rd32(sc(12));
    CHECK(dev != 0);

    // A viewport, added to the device and made current.
    hr = call_method(d3d, D3D_CreateViewport, {sc(16), 0});
    CHECK_EQ(hr, D3D_OK_);
    uint32_t vp = rd32(sc(16));
    CHECK(vp != 0);
    hr = call_method(dev, DEV_AddViewport, {vp});
    CHECK_EQ(hr, D3D_OK_);

    uint32_t vpdata = sc(0x800);
    gm_zero(vpdata, D3DVIEWPORT2_SIZE);
    wr32(vpdata + D3DVP_OFF_dwSize, D3DVIEWPORT2_SIZE);
    wr32(vpdata + D3DVP_OFF_dwX, 0);
    wr32(vpdata + D3DVP_OFF_dwY, 0);
    wr32(vpdata + D3DVP_OFF_dwWidth, 640);
    wr32(vpdata + D3DVP_OFF_dwHeight, 480);
    wrf32(vpdata + D3DVP_OFF_dvMinZ, 0.0f);
    wrf32(vpdata + D3DVP_OFF_dvMaxZ, 1.0f);
    hr = call_method(vp, VP_SetViewport2, {vpdata});
    CHECK_EQ(hr, D3D_OK_);
    hr = call_method(dev, DEV_SetCurrentViewport, {vp});
    CHECK_EQ(hr, D3D_OK_);

    // A texture: a 16x16 surface, QueryInterface'd to IDirect3DTexture2.
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_TEXTURE);
    wr32(desc + DDSD_OFF_dwWidth, 16);
    wr32(desc + DDSD_OFF_dwHeight, 16);
    call_method(dd, DD_CreateSurface, {desc, sc(20), 0});
    uint32_t texsurf = rd32(sc(20));
    CHECK(texsurf != 0);

    // IID_IDirect3DTexture2 = 93281502-8CF8-11D0-89AB-00A0C9054129. The four
    // 9328150x IIDs are not in interface-declaration order, so this is the
    // SDK's literal rather than a value derived from the neighbouring ones.
    uint8_t tex2[16] = {0x02, 0x15, 0x28, 0x93, 0xF8, 0x8C, 0xD0, 0x11,
                        0x89, 0xAB, 0x00, 0xA0, 0xC9, 0x05, 0x41, 0x29};
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, tex2[i]);
    hr = call_method(texsurf, S_QueryInterface, {iid, sc(24)});
    CHECK_EQ(hr, S_OK);
    uint32_t tex = rd32(sc(24));
    CHECK(tex != 0);

    hr = call_method(tex, TEX_GetHandle, {dev, sc(28)});
    CHECK_EQ(hr, D3D_OK_);
    uint32_t thandle = rd32(sc(28));
    CHECK(thandle != 0);
    CHECK_EQ(g_textures.size(), 1);

    // Bind the texture and set a render state the host must see.
    hr = call_method(dev, DEV_SetRenderState, {D3DRENDERSTATE_TEXTUREHANDLE, thandle});
    CHECK_EQ(hr, D3D_OK_);
    hr = call_method(dev, DEV_SetRenderState, {D3DRENDERSTATE_CULLMODE, 2});
    CHECK_EQ(hr, D3D_OK_);

    // A projection matrix, so the host sees it as set.
    uint32_t mat = sc(0xc00);
    for (int i = 0; i < 16; ++i)
        wrf32(mat + 4u * (uint32_t)i, i == 0 || i == 5 || i == 10 || i == 15 ? 1.0f : 0.0f);
    hr = call_method(dev, DEV_SetTransform, {D3DTRANSFORMSTATE_PROJECTION, mat});
    CHECK_EQ(hr, D3D_OK_);

    hr = call_method(dev, DEV_BeginScene, {});
    CHECK_EQ(hr, D3D_OK_);
    CHECK_EQ(g_begin_scene, 1);
    // A second BeginScene is an error, as D3D specifies.
    hr = call_method(dev, DEV_BeginScene, {});
    CHECK_EQ(hr, D3DERR_SCENEINSCENE);

    // Four TL vertices and six indices: two triangles.
    uint32_t verts = sc(0x800);
    for (uint32_t v = 0; v < 4; ++v) {
        uint32_t b = verts + v * 32;
        wrf32(b + 0, (float)(v & 1 ? 100 : 0));
        wrf32(b + 4, (float)(v & 2 ? 100 : 0));
        wrf32(b + 8, 0.5f);
        wrf32(b + 12, 1.0f);
        wr32(b + 16, 0xffffffffu);
        wr32(b + 20, 0);
        wrf32(b + 24, (float)(v & 1));
        wrf32(b + 28, (float)((v >> 1) & 1));
    }
    uint32_t idx = sc(0xa00);
    const uint16_t order[6] = {0, 1, 2, 2, 1, 3};
    for (uint32_t i = 0; i < 6; ++i)
        wr16(idx + i * 2, order[i]);

    hr = call_method(dev, DEV_DrawIndexedPrimitive,
                     {D3DPT_TRIANGLELIST, D3DVT_TLVERTEX, verts, 4, idx, 6, 0});
    CHECK_EQ(hr, D3D_OK_);
    CHECK_EQ(g_draws.size(), 1);
    if (!g_draws.empty()) {
        const DrawRecord &d = g_draws[0];
        CHECK_EQ(d.primitive_type, D3DPT_TRIANGLELIST);
        CHECK_EQ(d.vertex_type, D3DVT_TLVERTEX);
        CHECK_EQ(d.vertex_count, 4);
        CHECK_EQ(d.index_count, 6);
        CHECK_EQ(d.texture_handle, thandle);
        CHECK_EQ(d.cull, 2);
        CHECK_EQ(d.viewport[2], 640);
        CHECK_EQ(d.viewport[3], 480);
        CHECK(d.had_projection);
        CHECK_EQ(d.vertices.size(), 4 * 32);
        CHECK_EQ(d.indices.size(), 6);
        bool idx_ok = true;
        for (uint32_t i = 0; i < 6; ++i)
            if (d.indices[i] != order[i])
                idx_ok = false;
        CHECK(idx_ok);
        // The first vertex's x really is the float the guest wrote.
        float x0;
        memcpy(&x0, d.vertices.data(), 4);
        CHECK(x0 == 0.0f);
    }

    // A non-indexed draw is forwarded with no index list.
    hr = call_method(dev, DEV_DrawPrimitive, {D3DPT_TRIANGLESTRIP, D3DVT_TLVERTEX, verts, 4, 0});
    CHECK_EQ(hr, D3D_OK_);
    CHECK_EQ(g_draws.size(), 2);
    CHECK_EQ(g_draws[1].index_count, 0);
    CHECK_EQ(g_draws[1].primitive_type, D3DPT_TRIANGLESTRIP);

    hr = call_method(dev, DEV_EndScene, {});
    CHECK_EQ(hr, D3D_OK_);
    CHECK_EQ(g_end_scene, 1);
    hr = call_method(dev, DEV_EndScene, {});
    CHECK_EQ(hr, D3DERR_SCENENOTINSCENE);

    // GetCaps reports a hardware device with the texture limits the renderer
    // is promised.
    uint32_t caps = sc(0xc00);
    gm_zero(caps, D3DDEVICEDESC_SIZE);
    wr32(caps + D3DDD_OFF_dwSize, D3DDEVICEDESC_SIZE);
    hr = call_method(dev, DEV_GetCaps, {caps, 0});
    CHECK_EQ(hr, D3D_OK_);
    CHECK_EQ(rd32(caps + D3DDD_OFF_dcmColorModel), D3DCOLOR_RGB);
    CHECK_EQ(rd32(caps + D3DDD_OFF_dwMaxTextureWidth), 2048);
}

// DirectSound: create a buffer, fill it through Lock, play it, and check the
// PCM and format reached the host.
// Releasing an IDirectSound ends the sound of every buffer it made and gives
// their channels back (UG1 releases only the device at the end of each music
// stream, run 4). A buffer of another device keeps playing, and a late Release
// of an ended buffer is an ordinary one.
static void test_dsound_device_release() {
    cpu_reset();
    g_plays.clear();
    g_stops.clear();
    uint32_t create = tramp("DSOUND.dll", "ord1");
    CHECK(create != 0);
    CHECK_EQ(call_shim(create, {0, sc(0), 0}), DS_OK);
    CHECK_EQ(call_shim(create, {0, sc(0x10), 0}), DS_OK);
    uint32_t ds_a = rd32(sc(0)), ds_b = rd32(sc(0x10));
    CHECK(ds_a != 0 && ds_b != 0 && ds_a != ds_b);

    uint32_t wfx = sc(0x100);
    wr16(wfx + WFX_OFF_wFormatTag, WAVE_FORMAT_PCM);
    wr16(wfx + WFX_OFF_nChannels, 2);
    wr32(wfx + WFX_OFF_nSamplesPerSec, 22050);
    wr32(wfx + WFX_OFF_nAvgBytesPerSec, 88200);
    wr16(wfx + WFX_OFF_nBlockAlign, 4);
    wr16(wfx + WFX_OFF_wBitsPerSample, 16);
    wr16(wfx + WFX_OFF_cbSize, 0);
    uint32_t bd = sc(0x200);
    gm_zero(bd, DSBUFFERDESC_SIZE);
    wr32(bd + DSBD_OFF_dwSize, DSBUFFERDESC_SIZE);
    wr32(bd + DSBD_OFF_dwFlags, DSBCAPS_CTRLVOLUME);
    wr32(bd + DSBD_OFF_dwBufferBytes, 4096);
    wr32(bd + DSBD_OFF_lpwfxFormat, wfx);
    CHECK_EQ(call_method(ds_a, DS_CreateSoundBuffer, {bd, sc(4), 0}), DS_OK);
    CHECK_EQ(call_method(ds_b, DS_CreateSoundBuffer, {bd, sc(8), 0}), DS_OK);
    uint32_t buf_a = rd32(sc(4)), buf_b = rd32(sc(8));
    CHECK(buf_a != 0 && buf_b != 0);
    CHECK_EQ(call_method(buf_a, B_Play, {0, 0, DSBPLAY_LOOPING}), DS_OK);
    CHECK_EQ(call_method(buf_b, B_Play, {0, 0, DSBPLAY_LOOPING}), DS_OK);
    CHECK_EQ(g_plays.size(), 2u);
    if (g_plays.size() != 2u)
        return;
    int32_t ch_a = g_plays[0].channel, ch_b = g_plays[1].channel;
    CHECK(ch_a != ch_b);

    // The device goes; its buffer stops and its channel is free again.
    g_stops.clear();
    CHECK_EQ(call_method(ds_a, 2, {}), 0u); // Release
    CHECK_EQ(g_stops.size(), 1u);
    if (!g_stops.empty())
        CHECK_EQ(g_stops[0], ch_a);
    CHECK_EQ(call_method(buf_a, B_GetStatus, {sc(0x20)}), DS_OK);
    CHECK_EQ(rd32(sc(0x20)) & DSBSTATUS_PLAYING, 0u);
    int32_t again = dx_alloc_audio_channel();
    CHECK_EQ(again, ch_a);
    dx_free_audio_channel(again);

    // The other device's buffer was not touched.
    CHECK_EQ(call_method(buf_b, B_GetStatus, {sc(0x20)}), DS_OK);
    CHECK(rd32(sc(0x20)) & DSBSTATUS_PLAYING);

    // A late Release of the ended buffer is ordinary; nothing else stops.
    g_stops.clear();
    CHECK_EQ(call_method(buf_a, 2, {}), 0u);
    CHECK_EQ(g_stops.size(), 0u);
    CHECK_EQ(call_method(buf_b, 2, {}), 0u);
    CHECK_EQ(call_method(ds_b, 2, {}), 0u);
}

static void test_dsound() {
    cpu_reset();
    g_plays.clear();
    g_stops.clear();

    uint32_t create = tramp("DSOUND.dll", "ord1");
    CHECK(create != 0);
    uint32_t hr = call_shim(create, {0, sc(0), 0});
    CHECK_EQ(hr, DS_OK);
    uint32_t ds = rd32(sc(0));
    CHECK(ds != 0);

    hr = call_method(ds, DS_SetCooperativeLevel, {0x20004, 3});
    CHECK_EQ(hr, DS_OK);

    // A 22050 Hz, mono, 16-bit secondary buffer of 1024 bytes.
    uint32_t wfx = sc(0x100);
    wr16(wfx + WFX_OFF_wFormatTag, WAVE_FORMAT_PCM);
    wr16(wfx + WFX_OFF_nChannels, 1);
    wr32(wfx + WFX_OFF_nSamplesPerSec, 22050);
    wr32(wfx + WFX_OFF_nAvgBytesPerSec, 44100);
    wr16(wfx + WFX_OFF_nBlockAlign, 2);
    wr16(wfx + WFX_OFF_wBitsPerSample, 16);
    wr16(wfx + WFX_OFF_cbSize, 0);

    uint32_t bd = sc(0x200);
    gm_zero(bd, DSBUFFERDESC_SIZE);
    wr32(bd + DSBD_OFF_dwSize, DSBUFFERDESC_SIZE);
    wr32(bd + DSBD_OFF_dwFlags, DSBCAPS_CTRLVOLUME | DSBCAPS_STATIC);
    wr32(bd + DSBD_OFF_dwBufferBytes, 1024);
    wr32(bd + DSBD_OFF_lpwfxFormat, wfx);
    hr = call_method(ds, DS_CreateSoundBuffer, {bd, sc(4), 0});
    CHECK_EQ(hr, DS_OK);
    uint32_t buf = rd32(sc(4));
    CHECK(buf != 0);

    // A buffer with no format is refused.
    wr32(bd + DSBD_OFF_lpwfxFormat, 0);
    hr = call_method(ds, DS_CreateSoundBuffer, {bd, sc(8), 0});
    CHECK_EQ(hr, DSERR_INVALIDPARAM);
    wr32(bd + DSBD_OFF_lpwfxFormat, wfx);

    // Lock the whole buffer and write a ramp.
    hr = call_method(buf, B_Lock, {0, 1024, sc(0x300), sc(0x304), sc(0x308), sc(0x30c), 0});
    CHECK_EQ(hr, DS_OK);
    uint32_t p1 = rd32(sc(0x300));
    uint32_t n1 = rd32(sc(0x304));
    CHECK(p1 != 0);
    CHECK_EQ(n1, 1024);
    CHECK_EQ(rd32(sc(0x30c)), 0); // no wrap
    for (uint32_t i = 0; i < 1024; ++i)
        wr8(p1 + i, (uint8_t)(i & 0xff));
    hr = call_method(buf, B_Unlock, {p1, 1024, 0, 0});
    CHECK_EQ(hr, DS_OK);

    hr = call_method(buf, B_SetVolume, {(uint32_t)(int32_t)-2000});
    CHECK_EQ(hr, DS_OK);

    hr = call_method(buf, B_Play, {0, 0, 0});
    CHECK_EQ(hr, DS_OK);
    CHECK_EQ(g_plays.size(), 1);
    if (!g_plays.empty()) {
        const PlayRecord &r = g_plays[0];
        CHECK_EQ(r.rate, 22050);
        CHECK_EQ(r.channels, 1);
        CHECK_EQ(r.bits, 16);
        CHECK_EQ(r.bytes, 1024);
        CHECK_EQ(r.loop, 0);
        CHECK_EQ(r.volume, (uint64_t)(int64_t)-2000);
        bool pcm_ok = true;
        for (uint32_t i = 0; i < 1024; ++i)
            if (r.pcm[i] != (uint8_t)(i & 0xff)) {
                pcm_ok = false;
                break;
            }
        CHECK(pcm_ok);
    }

    uint32_t st = sc(0x400);
    hr = call_method(buf, B_GetStatus, {st});
    CHECK_EQ(hr, DS_OK);
    CHECK((rd32(st) & DSBSTATUS_PLAYING) != 0);

    hr = call_method(buf, B_Stop, {});
    CHECK_EQ(hr, DS_OK);
    CHECK_EQ(g_stops.size(), 1);

    // A lock that runs past the end wraps into the second region.
    hr = call_method(buf, B_Lock, {1000, 100, sc(0x300), sc(0x304), sc(0x308), sc(0x30c), 0});
    CHECK_EQ(hr, DS_OK);
    CHECK_EQ(rd32(sc(0x304)), 24);
    CHECK_EQ(rd32(sc(0x30c)), 76);
    CHECK(rd32(sc(0x308)) != 0);
    call_method(buf, B_Unlock, {0, 0, 0, 0});

    // The 3D buffer interface is the same object seen another way.
    uint32_t iid = sc(0x40);
    uint8_t b3d[16] = {0x86, 0xFA, 0x9A, 0x27, 0x81, 0x49, 0xCE, 0x11,
                       0xA5, 0x21, 0x00, 0x20, 0xAF, 0x0B, 0xE5, 0x60};
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, b3d[i]);
    hr = call_method(buf, 0 /* QueryInterface */, {iid, sc(0x60)});
    CHECK_EQ(hr, S_OK);
    uint32_t b3 = rd32(sc(0x60));
    CHECK(b3 != 0);
    CHECK(b3 != buf);
    // SetPosition then GetPosition round-trips the floats.
    float pos[3] = {1.5f, -2.5f, 3.25f};
    uint32_t a0, a1, a2;
    memcpy(&a0, &pos[0], 4);
    memcpy(&a1, &pos[1], 4);
    memcpy(&a2, &pos[2], 4);
    hr = call_method(b3, 19 /* SetPosition */, {a0, a1, a2, 1});
    CHECK_EQ(hr, DS_OK);
    hr = call_method(b3, 10 /* GetPosition */, {sc(0x500)});
    CHECK_EQ(hr, DS_OK);
    CHECK(rdf32(sc(0x500)) == 1.5f);
    CHECK(rdf32(sc(0x504)) == -2.5f);
    CHECK(rdf32(sc(0x508)) == 3.25f);
}

// DirectInput: the keyboard reports the host's key state, and the mouse
// delivers buffered events built by diffing successive host states.
static void test_dinput() {
    cpu_reset();
    memset(&g_input, 0, sizeof g_input);

    uint32_t create = tramp("DINPUT.dll", "DirectInputCreateA");
    CHECK(create != 0);
    uint32_t hr = call_shim(create, {0x400000, 0x0500, sc(0), 0});
    CHECK_EQ(hr, DI_OK);
    uint32_t di = rd32(sc(0));
    CHECK(di != 0);

    // The keyboard.
    uint32_t guid = sc(0x40);
    uint8_t kbd[16] = {0x61, 0x2B, 0x1D, 0x6F, 0xA0, 0xD5, 0xCF, 0x11,
                       0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00};
    for (int i = 0; i < 16; ++i)
        wr8(guid + (uint32_t)i, kbd[i]);
    hr = call_method(di, DI_CreateDevice, {guid, sc(4), 0});
    CHECK_EQ(hr, DI_OK);
    uint32_t kb = rd32(sc(4));
    CHECK(kb != 0);

    // Acquire before a data format is set must fail.
    hr = call_method(kb, DID_Acquire, {});
    CHECK_EQ(hr, DIERR_NOTINITIALIZED);

    uint32_t df = sc(0x100);
    gm_zero(df, 24);
    wr32(df + 0, 24);   // dwSize
    wr32(df + 4, 16);   // dwObjSize
    wr32(df + 12, 256); // dwDataSize
    hr = call_method(kb, DID_SetDataFormat, {df});
    CHECK_EQ(hr, DI_OK);
    hr = call_method(kb, DID_Acquire, {});
    CHECK_EQ(hr, DI_OK);

    // Two keys down at the host.
    g_input.keys[0x1e] = 0x80; // DIK_A
    g_input.keys[0x11] = 0x80; // DIK_W
    uint32_t state = sc(0x200);
    gm_zero(state, 256);
    hr = call_method(kb, DID_GetDeviceState, {256, state});
    CHECK_EQ(hr, DI_OK);
    CHECK_EQ(rd8(state + 0x1e), 0x80);
    CHECK_EQ(rd8(state + 0x11), 0x80);
    CHECK_EQ(rd8(state + 0x20), 0);

    // The mouse, with a buffer, reporting relative motion.
    uint8_t mouse[16] = {0x60, 0x2B, 0x1D, 0x6F, 0xA0, 0xD5, 0xCF, 0x11,
                         0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00};
    for (int i = 0; i < 16; ++i)
        wr8(guid + (uint32_t)i, mouse[i]);
    hr = call_method(di, DI_CreateDevice, {guid, sc(8), 0});
    CHECK_EQ(hr, DI_OK);
    uint32_t ms = rd32(sc(8));
    CHECK(ms != 0);

    wr32(df + 12, DIMOUSESTATE_SIZE);
    call_method(ms, DID_SetDataFormat, {df});

    uint32_t prop = sc(0x300);
    gm_zero(prop, 20);
    wr32(prop + DIPH_OFF_dwSize, 20);
    wr32(prop + DIPH_OFF_dwHeaderSize, 16);
    wr32(prop + DIPROPDWORD_OFF_dwData, 32);
    hr = call_method(ms, DID_SetProperty, {1 /* DIPROP_BUFFERSIZE */, prop});
    CHECK_EQ(hr, DI_OK);
    hr = call_method(ms, DID_Acquire, {});
    CHECK_EQ(hr, DI_OK);

    g_input.mouse_dx = 7;
    g_input.mouse_dy = -3;
    g_input.mouse_buttons[0] = 0x80;

    uint32_t mstate = sc(0x400);
    gm_zero(mstate, DIMOUSESTATE_SIZE);
    hr = call_method(ms, DID_GetDeviceState, {DIMOUSESTATE_SIZE, mstate});
    CHECK_EQ(hr, DI_OK);
    CHECK_EQ((int32_t)rd32(mstate + DIMS_OFF_lX), 7);
    CHECK_EQ((int32_t)rd32(mstate + DIMS_OFF_lY), (uint64_t)(int64_t)-3);
    CHECK_EQ(rd8(mstate + DIMS_OFF_rgbButtons + 0), 0x80);

    // DIDEVCAPS in both sizes: the DirectX 3 record (24 bytes) ends with
    // dwPOVs and carries the same counts as the DirectX 5 one (44).
    uint32_t caps = sc(0x600);
    for (uint32_t size : {44u, 24u}) {
        gm_zero(caps, 48);
        wr32(caps, size);
        CHECK_EQ(call_method(ms, 3 /* GetCapabilities */, {caps}), DI_OK);
        CHECK_EQ(rd32(caps + DIDC_OFF_dwDevType), DIDEVTYPE_MOUSE);
        CHECK_EQ(rd32(caps + DIDC_OFF_dwAxes), 3u);
        CHECK_EQ(rd32(caps + DIDC_OFF_dwButtons), 4u);
    }
    wr32(caps, 16);
    CHECK_EQ(call_method(ms, 3 /* GetCapabilities */, {caps}), DIERR_INVALIDPARAM);

    // The same motion arrived as buffered events. GetDeviceState polled once,
    // so those events are already queued.
    uint32_t inout = sc(0x500);
    wr32(inout, 16);
    uint32_t data = sc(0x800);
    hr = call_method(ms, DID_GetDeviceData, {DIDEVICEOBJECTDATA_SIZE, data, inout, 0});
    CHECK_EQ(hr, DI_OK);
    uint32_t n = rd32(inout);
    CHECK_EQ(n, 3); // x, y and the button
    if (n == 3) {
        CHECK_EQ(rd32(data + 0 * DIDEVICEOBJECTDATA_SIZE + DIDOD_OFF_dwOfs), 0);
        CHECK_EQ((int32_t)rd32(data + 0 * DIDEVICEOBJECTDATA_SIZE + DIDOD_OFF_dwData), 7);
        CHECK_EQ(rd32(data + 1 * DIDEVICEOBJECTDATA_SIZE + DIDOD_OFF_dwOfs), 4);
        // The negative delta survives the round trip through the packed event.
        CHECK_EQ((int32_t)rd32(data + 1 * DIDEVICEOBJECTDATA_SIZE + DIDOD_OFF_dwData),
                 (uint64_t)(int64_t)-3);
        CHECK_EQ(rd32(data + 2 * DIDEVICEOBJECTDATA_SIZE + DIDOD_OFF_dwOfs), 12);
        CHECK_EQ(rd32(data + 2 * DIDEVICEOBJECTDATA_SIZE + DIDOD_OFF_dwData), 0x80);
    }
    // The events were consumed.
    wr32(inout, 16);
    call_method(ms, DID_GetDeviceData, {DIDEVICEOBJECTDATA_SIZE, data, inout, 0});
    CHECK_EQ(rd32(inout), 0);

    // Absolute placement removes all already sampled X/Y, including a
    // keyboard-first poll and buffered Poll, while preserving other input.
    g_input.mouse_dx = 41;
    g_input.mouse_dy = -23;
    g_input.mouse_dz = 120;
    g_input.mouse_buttons[1] = 0x80;
    call_method(ms, 25 /* Poll */, {});
    g_input.mouse_dx = 19;
    g_input.mouse_dy = 37;
    call_method(kb, DID_GetDeviceState, {256, state});
    dinput_discard_mouse_motion(ms);
    call_method(ms, DID_GetDeviceState, {DIMOUSESTATE_SIZE, mstate});
    CHECK_EQ(rd32(mstate + DIMS_OFF_lX), 0);
    CHECK_EQ(rd32(mstate + DIMS_OFF_lY), 0);
    CHECK_EQ(rd32(mstate + DIMS_OFF_lZ), 120);
    CHECK_EQ(rd8(mstate + DIMS_OFF_rgbButtons), 0x80);
    CHECK_EQ(rd8(mstate + DIMS_OFF_rgbButtons + 1), 0x80);
    CHECK_EQ(rd8(state + 0x1e), 0x80);
    wr32(inout, 16);
    call_method(ms, DID_GetDeviceData, {DIDEVICEOBJECTDATA_SIZE, data, inout, 0});
    CHECK_EQ(rd32(inout), 2); // wheel and right-button edge, no X/Y events
    CHECK_EQ(rd32(data + DIDOD_OFF_dwOfs), 8);
    CHECK_EQ(rd32(data + DIDEVICEOBJECTDATA_SIZE + DIDOD_OFF_dwOfs), 13);
    g_input.mouse_dx = 9;
    call_method(ms, DID_GetDeviceState, {DIMOUSESTATE_SIZE, mstate});
    CHECK_EQ(rd32(mstate + DIMS_OFF_lX), 9); // Later physical motion survives.

    // A joystick GUID is refused rather than half-supported.
    for (int i = 0; i < 16; ++i)
        wr8(guid + (uint32_t)i, (uint8_t)(0x40 + i));
    hr = call_method(di, DI_CreateDevice, {guid, sc(12), 0});
    CHECK_EQ(hr, DIERR_DEVICENOTREG);
    CHECK_EQ(rd32(sc(12)), 0);
}

// Miles Sound System: every import has the arity its decorated name states,
// so a call through it leaves ESP where the caller expects.
static void test_mss32_arities() {
    cpu_reset();
    struct {
        const char *name;
        uint32_t argc;
    } expected[] = {
        {"_AIL_startup@0", 0},
        {"_AIL_shutdown@0", 0},
        {"_AIL_set_preference@8", 2},
        {"_AIL_waveOutOpen@16", 4},
        {"_AIL_mem_free_lock@4", 1},
        {"_AIL_file_read@8", 2},
        {"_AIL_allocate_sample_handle@4", 1},
        {"_AIL_release_sample_handle@4", 1},
        {"_AIL_init_sample@4", 1},
        {"_AIL_set_sample_file@12", 3},
        {"_AIL_start_sample@4", 1},
        {"_AIL_end_sample@4", 1},
        {"_AIL_sample_status@4", 1},
        {"_AIL_set_sample_volume@8", 2},
        {"_AIL_set_sample_pan@8", 2},
        {"_AIL_set_sample_loop_count@8", 2},
        {"_AIL_sample_loop_count@4", 1},
        {"_AIL_set_sample_reverb@16", 4},
        {"_AIL_open_stream@12", 3},
        {"_AIL_start_stream@4", 1},
        {"_AIL_close_stream@4", 1},
        {"_AIL_stream_status@4", 1},
        {"_AIL_set_stream_volume@8", 2},
        {"_AIL_stream_volume@4", 1},
        {"_AIL_set_stream_loop_count@8", 2},
        {"_AIL_enumerate_3D_providers@12", 3},
        {"_AIL_open_3D_provider@4", 1},
        {"_AIL_close_3D_provider@4", 1},
        {"_AIL_set_3D_provider_preference@12", 3},
        {"_AIL_3D_provider_attribute@12", 3},
        {"_AIL_allocate_3D_sample_handle@4", 1},
        {"_AIL_release_3D_sample_handle@4", 1},
        {"_AIL_set_3D_sample_file@8", 2},
        {"_AIL_start_3D_sample@4", 1},
        {"_AIL_end_3D_sample@4", 1},
        {"_AIL_3D_sample_status@4", 1},
        {"_AIL_set_3D_sample_volume@8", 2},
        {"_AIL_set_3D_sample_loop_count@8", 2},
        {"_AIL_set_3D_position@16", 4},
        {"_AIL_set_3D_orientation@28", 7},
        {"_AIL_3D_update_position@8", 2},
    };
    for (auto &e : expected) {
        uint32_t t = tramp("mss32.dll", e.name);
        CHECK(t != 0);
        CHECK_EQ(imports_argc(t), e.argc);
    }
    CHECK_EQ(imports_argc(0), 0u);
    CHECK_EQ(imports_argc(TRAMP_BASE + 1), 0u);
    CHECK_EQ(imports_argc(TRAMP_BASE + imports_count() * TRAMP_STRIDE), 0u);
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_startup@0"), {}), 1u);
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_enumerate_3D_providers@12"), {0, 0, 0}), 0u);
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_open_3D_provider@4"), {0}), 1u);
    uint32_t sample = call_shim(tramp("mss32.dll", "_AIL_allocate_sample_handle@4"), {0});
    CHECK(sample != 0);
    call_shim(tramp("mss32.dll", "_AIL_release_sample_handle@4"), {sample});
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_allocate_3D_sample_handle@4"), {0}), 0u);
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_open_stream@12"), {0, 0, 0}), 0u);
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_sample_status@4"), {0}), 1u);
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_stream_status@4"), {0}), 2u);
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_3D_sample_status@4"), {0}), 2u);
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_set_3D_orientation@28"), {0, 0, 0, 0, 0, 0, 0}),
             0u);
}

// A canonical 44-byte header and eight bytes of mono, 8-bit PCM at 22050 Hz.
static uint32_t build_test_wave() {
    uint32_t wav = sc(0x100);
    static const uint8_t header[44] = {
        'R', 'I', 'F', 'F', 44 + 8 - 8, 0, 0,   0,   'W', 'A',  'V',  'E', 'f', 'm',  't',
        ' ', 16,  0,   0,   0,          1, 0,   1,   0,   0x22, 0x56, 0,   0,   0x22, 0x56,
        0,   0,   1,   0,   8,          0, 'd', 'a', 't', 'a',  8,    0,   0,   0};
    for (uint32_t i = 0; i < 44; ++i)
        wr8(wav + i, header[i]);
    for (uint32_t i = 0; i < 8; ++i)
        wr8(wav + 44 + i, (uint8_t)(0x80 + i));
    return wav;
}

// FMOD's exported suffixes are the stdcall stack contract, independent of
// the host channel ids shared with the other sound libraries.
static void test_fmod() {
    cpu_reset();
    const char *names[] = {"_FSOUND_Init@12",
                           "_FSOUND_Close@0",
                           "_FSOUND_SetOutput@4",
                           "_FSOUND_SetDriver@4",
                           "_FSOUND_GetDriverName@4",
                           "_FSOUND_Sample_LoadWav@12",
                           "_FSOUND_Sample_Free@4",
                           "_FSOUND_Sample_GetDefaults@20",
                           "_FSOUND_Sample_SetLoopMode@8",
                           "_FSOUND_PlaySoundAttrib@20",
                           "_FSOUND_StopSound@4",
                           "_FSOUND_SetVolume@8",
                           "_FSOUND_SetPan@8",
                           "_FSOUND_SetFrequency@8",
                           "_FSOUND_Stream_OpenMpeg@8",
                           "_FSOUND_Stream_Play@8",
                           "_FSOUND_Stream_SetPaused@8",
                           "_FSOUND_Stream_Close@4"};
    for (const char *name : names) {
        uint32_t t = tramp("fmod.dll", name);
        CHECK(t != 0);
        CHECK_EQ(imports_argc(t), (uint32_t)atoi(strchr(name, '@') + 1) / 4);
    }
    auto f = [](const char *name, std::initializer_list<uint32_t> args) {
        return call_shim(tramp("fmod.dll", name), args);
    };
    CHECK_EQ(f("_FSOUND_Init@12", {44100, 2, 0}), 1u);
    CHECK_EQ(f("_FSOUND_SetOutput@4", {0}), 1u);
    CHECK_EQ(f("_FSOUND_SetDriver@4", {0}), 1u);
    uint32_t driver = f("_FSOUND_GetDriverName@4", {0});
    CHECK(driver != 0);
    if (driver)
        CHECK(gm_str(driver) == "recomp mixer");
    CHECK_EQ(f("_FSOUND_GetDriverName@4", {0}), driver);
    uint32_t wav = build_test_wave();
    // Extend the existing RIFF fixture to eight signed 16-bit mono frames.
    wr32(wav + 4, 52);
    wr32(wav + 28, 44100);
    wr16(wav + 32, 2);
    wr16(wav + 34, 16);
    wr32(wav + 40, 16);
    for (uint32_t i = 0; i < 8; ++i)
        wr16(wav + 44 + i * 2, (uint16_t)(i * 1000));
    uint32_t sample = f("_FSOUND_Sample_LoadWav@12", {0xffffffffu, wav, 0x8000});
    CHECK(sample != 0);
    uint32_t defaults = sc(0x200);
    CHECK_EQ(f("_FSOUND_Sample_GetDefaults@20",
               {sample, defaults, defaults + 4, defaults + 8, defaults + 12}),
             1u);
    CHECK_EQ(rd32(defaults), 22050u);
    CHECK_EQ(rd32(defaults + 4), 255u);
    CHECK_EQ(rd32(defaults + 8), 128u);
    CHECK_EQ(f("_FSOUND_Sample_GetDefaults@20", {sample, 0, 0, 0, 0}), 1u);
    CHECK_EQ(f("_FSOUND_Sample_SetLoopMode@8", {sample, 2}), 1u);
    // The sample owns PCM after loading: guest scratch may be overwritten.
    memset(g_mem + wav, 0, 60);
    g_sample_tracking = true;
    g_plays.clear();
    uint32_t ch = f("_FSOUND_PlaySoundAttrib@20", {0xffffffffu, sample, 22050, 200, 128});
    CHECK(ch < 2);
    CHECK_EQ(g_plays.size(), 1u);
    if (!g_plays.empty()) {
        const auto &p = g_plays.back();
        CHECK_EQ(p.rate, 22050);
        CHECK_EQ(p.bits, 16);
        CHECK_EQ(p.channels, 1);
        CHECK_EQ(p.loop, 1);
        CHECK_EQ(p.pan, 0);
        CHECK_EQ(p.bytes, 16u);
        CHECK_EQ(p.pcm[2] | p.pcm[3] << 8, 1000);
        CHECK_EQ(f("_FSOUND_SetVolume@8", {ch, 64}), 1u);
        CHECK_EQ(g_audio_volumes[p.channel], -1201);
        CHECK_EQ(f("_FSOUND_SetPan@8", {ch, 255}), 1u);
        CHECK_EQ(g_audio_pans[p.channel], 10000);
        CHECK_EQ(f("_FSOUND_SetFrequency@8", {ch, 11025}), 1u);
        CHECK_EQ(g_audio_rates[p.channel], 11025u);
    }
    uint32_t other = f("_FSOUND_PlaySoundAttrib@20",
                       {0xffffffffu, sample, 0xffffffffu, 0xffffffffu, 0xffffffffu});
    CHECK(other < 2 && other != ch);
    CHECK_EQ(f("_FSOUND_PlaySoundAttrib@20", {0xffffffffu, sample, 22050, 255, 128}), 0xffffffffu);
    if (!g_plays.empty())
        g_sample_playing[g_plays.back().channel] = false;
    CHECK_EQ(f("_FSOUND_PlaySoundAttrib@20", {0xffffffffu, sample, 22050, 255, 128}), other);
    CHECK_EQ(f("_FSOUND_StopSound@4", {ch}), 1u);
    CHECK_EQ(f("_FSOUND_Sample_Free@4", {sample}), 0u);
    CHECK_EQ(f("_FSOUND_PlaySoundAttrib@20", {0xffffffffu, sample, 22050, 255, 128}), 0xffffffffu);
    CHECK_EQ(f("_FSOUND_Sample_LoadWav@12", {0xffffffffu, 0xfffffff0u, 0x8000}), 0u);
    CHECK_EQ(f("_FSOUND_Close@0", {}), 0u);
    CHECK_EQ(call_shim(tramp("CGalaxy.dll", "cgGetGalaxyAPI"), {}), 0u);
    CHECK_EQ(imports_argc(tramp("CGalaxy.dll", "cgGetGalaxyAPI")), 0u);
    g_sample_tracking = false;
}

static void test_fmod_stream() {
    cpu_reset();
    char dir[512];
    snprintf(dir, sizeof dir, "%s/recomp-fmod-XXXXXX", os_temp_dir());
    CHECK(os_mkdtemp(dir) == 0);
    std::string file = std::string(dir) + "/Test.mp3";
    FILE *out = fopen(file.c_str(), "wb");
    CHECK(out != nullptr);
    if (!out)
        return;
    CHECK_EQ(fwrite(kToneMp3, 1, sizeof kToneMp3, out), sizeof kToneMp3);
    CHECK_EQ(fclose(out), 0);
    win32_init(dir);
    dx_register_shims();
    auto f = [](const char *name, std::initializer_list<uint32_t> args) {
        return call_shim(tramp("fmod.dll", name), args);
    };
    CHECK_EQ(f("_FSOUND_Init@12", {44100, 4, 0}), 1u);
    gm_put_str(sc(0x100), "test.mp3", 0x100);
    g_plays.clear();
    g_queue_enabled = true;
    g_test_audio_pos = 0;
    // Disk WAVE input uses the resolver too, and replacing a numbered slot
    // stops every voice borrowing the old sample while preserving other DX ids.
    uint32_t wav = build_test_wave();
    std::string wave_file = std::string(dir) + "/Sample.wav";
    out = fopen(wave_file.c_str(), "wb");
    CHECK(out != nullptr);
    if (!out)
        return;
    CHECK_EQ(fwrite(g_mem + wav, 1, 52, out), 52u);
    CHECK_EQ(fclose(out), 0);
    gm_put_str(sc(0x300), "sample.wav", 0x100);
    uint32_t sample = f("_FSOUND_Sample_LoadWav@12", {7, sc(0x300), 0});
    CHECK(sample != 0);
    int32_t expected_voice = dx_alloc_audio_channel();
    CHECK(expected_voice >= 0);
    dx_free_audio_channel(expected_voice);
    CHECK(f("_FSOUND_PlaySoundAttrib@20", {0xffffffffu, sample, 22050, 255, 128}) < 4);
    CHECK(!g_plays.empty());
    if (!g_plays.empty()) {
        CHECK_EQ(g_plays.back().bits, 8);
        CHECK_EQ(g_plays.back().pcm[0], 0x80u);
        CHECK_EQ(g_plays.back().channel, expected_voice);
    }
    uint32_t replacement = f("_FSOUND_Sample_LoadWav@12", {7, sc(0x300), 0});
    CHECK(replacement != 0 && replacement != sample);
    CHECK_EQ(f("_FSOUND_Sample_GetDefaults@20", {sample, 0, 0, 0, 0}), 0u);
    CHECK_EQ(f("_FSOUND_Sample_Free@4", {replacement}), 0u);
    int32_t released_voice = dx_alloc_audio_channel();
    CHECK_EQ(released_voice, expected_voice);
    dx_free_audio_channel(released_voice);
    CHECK_EQ(remove(wave_file.c_str()), 0);
    uint32_t short_wave = heap_alloc(12);
    memcpy(g_mem + short_wave, g_mem + wav, 12);
    CHECK_EQ(f("_FSOUND_Sample_LoadWav@12", {0xffffffffu, short_wave, 0x8000}), 0u);
    heap_free(short_wave);
    gm_put_str(sc(0x100), "test.mp3", 0x100);
    g_plays.clear();
    uint32_t stream = f("_FSOUND_Stream_OpenMpeg@8", {sc(0x100), 0});
    CHECK(stream != 0);
    CHECK_EQ(g_plays.size(), 0u);
    CHECK(f("_FSOUND_Stream_Play@8", {0xffffffffu, stream}) < 4);
    CHECK_EQ(g_plays.size(), 1u);
    size_t stopped = g_stops.size();
    CHECK_EQ(f("_FSOUND_Sample_Free@4", {0}), 0u);
    CHECK_EQ(g_stops.size(), stopped); // invalid sample must not stop a stream
    g_queue_retired = true;
    host_pump_timers(&g_cpu);
    CHECK_EQ(g_queues.size(), 0u);
    g_queue_retired = false;
    host_pump_timers(&g_cpu);
    if (!g_plays.empty()) {
        CHECK_EQ(g_plays.back().rate, 44100);
        CHECK_EQ(g_plays.back().channels, 2);
        CHECK_EQ(g_plays.back().bytes + g_queued_bytes, 27648u);
    }
    // Pausing resumes from the audible frame, not the decoder's prefetched end.
    g_stream_played = 4608;
    CHECK_EQ(f("_FSOUND_Stream_SetPaused@8", {stream, 1}), 1u);
    size_t plays = g_plays.size();
    host_pump_timers(&g_cpu);
    CHECK_EQ(g_plays.size(), plays);
    CHECK_EQ(g_queued_bytes, 0u);
    CHECK_EQ(f("_FSOUND_Stream_SetPaused@8", {stream, 0}), 1u);
    host_pump_timers(&g_cpu);
    CHECK_EQ(g_plays.size(), plays + 1);
    if (!g_plays.empty())
        CHECK_EQ(g_plays.back().bytes + g_queued_bytes, 27648u - 4608);
    CHECK_EQ(f("_FSOUND_Stream_Close@4", {stream}), 1u);
    CHECK_EQ(g_queued_bytes, 0u);
    stream = f("_FSOUND_Stream_OpenMpeg@8", {sc(0x100), 2});
    CHECK(f("_FSOUND_Stream_Play@8", {0xffffffffu, stream}) < 4);
    host_pump_timers(&g_cpu);
    CHECK(g_queued_bytes >= 44100u * 4);
    CHECK_EQ(f("_FSOUND_Stream_Close@4", {stream}), 1u);
    gm_put_str(sc(0x100), "missing.mp3", 0x100);
    CHECK_EQ(f("_FSOUND_Stream_OpenMpeg@8", {sc(0x100), 0}), 0u);
    CHECK_EQ(f("_FSOUND_Close@0", {}), 0u);
    g_queue_enabled = false;
    CHECK_EQ(remove(file.c_str()), 0);
    CHECK_EQ(os_rmdir(dir), 0);
}

static void test_soundlib_stub() {
    cpu_reset();
    const char *names[] = {"CreateMidi",    "OpenMidi",      "PlayMidi", "StopMidi",
                           "SetMidiVolume", "GetMidiVolume", "FreeMidi"};
    const uint32_t arities[] = {0, 1, 0, 0, 1, 0, 0};
    for (size_t i = 0; i < std::size(names); ++i)
        CHECK_EQ(imports_argc(tramp("Soundlib.dll", names[i])), arities[i]);
    auto f = [](const char *name, std::initializer_list<uint32_t> args) {
        return call_shim(tramp("Soundlib.dll", name), args);
    };
    g_midi_available = false;
    CHECK_EQ(f("CreateMidi", {}), 0u); // headless host has no synth
    g_midi_available = true;
    CHECK_EQ(f("CreateMidi", {}), 1u);
    CHECK_EQ(f("SetMidiVolume", {(uint32_t)-2000}), 0u);
    CHECK_EQ(f("GetMidiVolume", {}), (uint32_t)-2000);
    char dir[512];
    snprintf(dir, sizeof dir, "%s/recomp-soundlib-XXXXXX", os_temp_dir());
    CHECK(os_mkdtemp(dir) == 0);
    std::string file = std::string(dir) + "/Test.mid";
    // Format 0, PPQN 96: a note on at zero, running-status note off at 500ms.
    const uint8_t midi[] = {'M', 'T',  'h', 'd', 0,   0,   0,   6, 0,    0,    0,
                            1,   0,    96,  'M', 'T', 'r', 'k', 0, 0,    0,    11,
                            0,   0x90, 60,  100, 96,  60,  0,   0, 0xff, 0x2f, 0};
    FILE *out = fopen(file.c_str(), "wb");
    CHECK(out != nullptr);
    if (!out)
        return;
    CHECK_EQ(fwrite(midi, 1, sizeof midi, out), sizeof midi);
    CHECK_EQ(fclose(out), 0);
    win32_init(dir);
    dx_register_shims();
    host_set_time_source_pinned(100, 500);
    gm_put_str(sc(0x100), "test.mid", 0x100);
    wr32(sc(0x200), sc(0x100));
    g_midi_messages.clear();
    CHECK_EQ(f("OpenMidi", {sc(0x200)}), 0u);
    CHECK(std::find(g_midi_messages.begin(), g_midi_messages.end(), 0x643c90u) !=
          g_midi_messages.end());
    CHECK(std::find(g_midi_messages.begin(), g_midi_messages.end(), 0x003c90u) ==
          g_midi_messages.end());
    host_pinned_clock_advance();
    host_pump_timers(&g_cpu);
    CHECK(std::find(g_midi_messages.begin(), g_midi_messages.end(), 0x003c90u) !=
          g_midi_messages.end());
    CHECK_EQ(f("StopMidi", {}), 0u);
    size_t before = g_midi_messages.size();
    host_pinned_clock_advance();
    host_pump_timers(&g_cpu);
    CHECK_EQ(g_midi_messages.size(), before);
    CHECK_EQ(f("PlayMidi", {}), 0u);
    CHECK(g_midi_messages.size() > before);
    CHECK_EQ(f("StopMidi", {}), 0u);
    // Format 1 merges the tempo track with notes: one quarter at 250ms,
    // followed by one at 500ms. Track volume is scaled by the master gain.
    const uint8_t multi[] = {'M',  'T',  'h',  'd',  0,    0,    0,    6,    0,  1,    0,    2,
                             0,    96,   'M',  'T',  'r',  'k',  0,    0,    0,  18,   0,    0xff,
                             0x51, 3,    3,    0xd0, 0x90, 96,   0xff, 0x51, 3,  7,    0xa1, 0x20,
                             96,   0xff, 0x2f, 0,    'M',  'T',  'r',  'k',  0,  0,    0,    20,
                             0,    0xb0, 7,    80,   0,    0x90, 60,   100,  96, 0x80, 60,   0,
                             96,   0x90, 64,   100,  0,    0xff, 0x2f, 0};
    out = fopen(file.c_str(), "wb");
    CHECK(out != nullptr);
    if (!out)
        return;
    CHECK_EQ(fwrite(multi, 1, sizeof multi, out), sizeof multi);
    CHECK_EQ(fclose(out), 0);
    host_set_time_source_pinned(0, 250);
    g_midi_messages.clear();
    CHECK_EQ(f("OpenMidi", {sc(0x200)}), 0u);
    CHECK(std::find(g_midi_messages.begin(), g_midi_messages.end(), 0x0807b0u) !=
          g_midi_messages.end());
    host_pinned_clock_advance();
    host_pump_timers(&g_cpu);
    CHECK(std::find(g_midi_messages.begin(), g_midi_messages.end(), 0x003c80u) !=
          g_midi_messages.end());
    host_pinned_clock_advance();
    host_pump_timers(&g_cpu);
    CHECK(std::find(g_midi_messages.begin(), g_midi_messages.end(), 0x644090u) ==
          g_midi_messages.end());
    host_pinned_clock_advance();
    host_pump_timers(&g_cpu);
    CHECK(std::find(g_midi_messages.begin(), g_midi_messages.end(), 0x644090u) !=
          g_midi_messages.end());
    CHECK_EQ(f("StopMidi", {}), 0u);
    out = fopen(file.c_str(), "wb");
    CHECK(out != nullptr);
    if (!out)
        return;
    CHECK_EQ(fwrite(multi, 1, sizeof multi - 1, out), sizeof multi - 1);
    CHECK_EQ(fclose(out), 0);
    CHECK(f("OpenMidi", {sc(0x200)}) != 0u); // truncated track
    CHECK_EQ(f("FreeMidi", {}), 0u);
    CHECK(g_midi_closes > 0);
    CHECK(f("OpenMidi", {0}) != 0u);
    host_clear_time_source();
    g_midi_available = false;
    CHECK_EQ(remove(file.c_str()), 0);
    CHECK_EQ(os_rmdir(dir), 0);
}

static void test_riff_parse() {
    cpu_reset();
    uint32_t wav = build_test_wave();
    RiffWave w{};
    CHECK(riff_parse_wave(wav, 52, &w));
    CHECK_EQ(w.pcm, wav + 44);
    CHECK_EQ(w.pcm_bytes, 8u);
    CHECK_EQ(w.rate, 22050u);
    CHECK_EQ(w.channels, 1u);
    CHECK_EQ(w.bits, 8u);
    wr8(wav + 20, 2); // format tag 2 (ADPCM) is refused
    CHECK(!riff_parse_wave(wav, 52, &w));
    build_test_wave();
    CHECK(!riff_parse_wave(wav, 11, &w));
    CHECK(!riff_parse_wave(wav, 25, &w)); // truncated fmt
    CHECK(!riff_parse_wave(0xfffffff0u, 52, &w));
    CHECK(!riff_parse_wave(wav, 52, nullptr));
    wr32(wav + 16, 0xffffffffu); // chunk arithmetic must not wrap
    CHECK(!riff_parse_wave(wav, 52, &w));
    build_test_wave();
    CHECK(riff_parse_wave(wav, 48, &w)); // data is bounded by the supplied image
    CHECK_EQ(w.pcm_bytes, 4u);
    // An odd-sized unknown chunk has one pad byte before the data chunk.
    build_test_wave();
    memmove(g_mem + wav + 46, g_mem + wav + 36, 16);
    memcpy(g_mem + wav + 36, "JUNK", 4);
    wr32(wav + 40, 1);
    wr16(wav + 44, 0);
    wr32(wav + 4, 54);
    CHECK(riff_parse_wave(wav, 62, &w));
    CHECK_EQ(w.pcm, wav + 54);
    CHECK_EQ(w.pcm_bytes, 8u);
}

static void test_mss32_sample() {
    cpu_reset();
    g_sample_tracking = true;
    g_plays.clear();
    uint32_t wav = build_test_wave();
    uint32_t h = call_shim(tramp("mss32.dll", "_AIL_allocate_sample_handle@4"), {1});
    CHECK(h != 0);
    call_shim(tramp("mss32.dll", "_AIL_init_sample@4"), {h});
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_set_sample_file@12"), {h, wav, 0}), 1u);
    call_shim(tramp("mss32.dll", "_AIL_set_sample_volume@8"), {h, 127});
    call_shim(tramp("mss32.dll", "_AIL_start_sample@4"), {h});
    CHECK_EQ(g_plays.size(), 1u);
    if (g_plays.size() != 1)
        return;
    CHECK_EQ(g_plays[0].rate, 22050u);
    CHECK_EQ(g_plays[0].bytes, 8u);
    CHECK_EQ(g_plays[0].channels, 1);
    CHECK_EQ(g_plays[0].bits, 8);
    CHECK_EQ(g_plays[0].volume, 0);
    CHECK_EQ(g_plays[0].pan, 0);
    CHECK_EQ(g_plays[0].loop, 0);
    CHECK_EQ(g_plays[0].pcm[7], 0x87u);
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_sample_status@4"), {h}), 4u);
    call_shim(tramp("mss32.dll", "_AIL_end_sample@4"), {h});
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_sample_status@4"), {h}), 2u);

    // Assert the Miles conversions through the actual host play record.
    struct {
        uint32_t volume, pan;
        int32_t volume_mb, pan_mb;
    } levels[] = {{127, 64, 0, 0}, {64, 0, -595, -10000}, {0, 127, -10000, 10000}};
    for (auto level : levels) {
        call_shim(tramp("mss32.dll", "_AIL_set_sample_volume@8"), {h, level.volume});
        call_shim(tramp("mss32.dll", "_AIL_set_sample_pan@8"), {h, level.pan});
        call_shim(tramp("mss32.dll", "_AIL_start_sample@4"), {h});
        CHECK_EQ(g_plays.back().volume, level.volume_mb);
        CHECK_EQ(g_plays.back().pan, level.pan_mb);
    }
    int32_t channel = g_plays.back().channel;
    g_sample_playing[channel] = false;
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_sample_status@4"), {h}), 2u);
    call_shim(tramp("mss32.dll", "_AIL_set_sample_loop_count@8"), {h, 0});
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_sample_loop_count@4"), {h}), 0u);
    call_shim(tramp("mss32.dll", "_AIL_start_sample@4"), {h});
    CHECK_EQ(g_plays.back().loop, 1);
    call_shim(tramp("mss32.dll", "_AIL_end_sample@4"), {h});

    call_shim(tramp("mss32.dll", "_AIL_set_sample_loop_count@8"), {h, 3});
    call_shim(tramp("mss32.dll", "_AIL_start_sample@4"), {h});
    size_t first = g_plays.size();
    for (unsigned i = 0; i < 3; ++i) {
        g_sample_playing[channel] = false;
        host_pump_timers(&g_cpu); // finite repeats progress without a Miles status poll
        CHECK_EQ(g_plays.size(), first + (i < 2 ? i + 1 : 2));
    }
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_sample_status@4"), {h}), 2u);
    CHECK_EQ(g_plays.back().loop, 0);

    call_shim(tramp("mss32.dll", "_AIL_init_sample@4"), {h});
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_sample_loop_count@4"), {h}), 1u);
    first = g_plays.size();
    call_shim(tramp("mss32.dll", "_AIL_start_sample@4"), {h});
    CHECK_EQ(g_plays.size(), first); // init discarded the image
    wr8(wav + 20, 2);
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_set_sample_file@12"), {h, wav, 0}), 0u);
    call_shim(tramp("mss32.dll", "_AIL_release_sample_handle@4"), {h});
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_sample_status@4"), {h}), 1u);
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_sample_status@4"), {65}), 1u);
    int32_t reused = dx_alloc_audio_channel();
    CHECK_EQ(reused, channel);
    dx_free_audio_channel(reused);
    std::set<uint32_t> handles;
    for (unsigned i = 0; i < 64; ++i) {
        uint32_t slot = call_shim(tramp("mss32.dll", "_AIL_allocate_sample_handle@4"), {1});
        CHECK(slot >= 1 && slot <= 64);
        CHECK(handles.insert(slot).second);
    }
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_allocate_sample_handle@4"), {1}), 0u);
    for (uint32_t slot : handles)
        call_shim(tramp("mss32.dll", "_AIL_release_sample_handle@4"), {slot});

    // File reads use guest path case/drive normalization and guest-owned memory.
    wav = build_test_wave();
    char dir[512];
    snprintf(dir, sizeof dir, "%s/recomp-mss32-XXXXXX", os_temp_dir());
    CHECK(os_mkdtemp(dir) == 0);
    std::string file = std::string(dir) + "/Tone.wav";
    FILE *f = fopen(file.c_str(), "wb");
    CHECK(f != nullptr);
    if (!f)
        return;
    CHECK_EQ(fwrite(g_mem + wav, 1, 52, f), 52u);
    CHECK_EQ(fclose(f), 0);
    win32_init(dir);
    uint32_t name = sc(0x300);
    gm_put_str(name, "C:\\tone.WAV", 0x100);
    uint32_t read = tramp("mss32.dll", "_AIL_file_read@8");
    for (uint32_t dest : {0u, 0xffffffffu, sc(0x500)}) {
        uint32_t loaded = call_shim(read, {name, dest});
        CHECK(loaded != 0);
        if (!loaded)
            continue;
        CHECK_EQ(memcmp(g_mem + loaded, g_mem + wav, 52), 0);
        if (dest == sc(0x500)) {
            CHECK_EQ(loaded, dest);
        } else {
            CHECK_EQ(heap_size(loaded), 52u);
            call_shim(tramp("mss32.dll", "_AIL_mem_free_lock@4"), {loaded});
            CHECK(!heap_owns(loaded));
        }
    }
    CHECK_EQ(call_shim(read, {name, GUEST_SIZE - 1}), 0u);
    gm_put_str(name, "absent.wav", 0x100);
    CHECK_EQ(call_shim(read, {name, 0}), 0u);
    CHECK_EQ(remove(file.c_str()), 0);
    CHECK_EQ(os_rmdir(dir), 0);
    g_sample_playing.clear();
    g_sample_tracking = false;
}

// The same synthetic MP3 and guest-root mapping as DirectShow, with mixed
// case and a guest backslash. Playback and queue consumption are deterministic.
static void test_mss32_stream() {
    cpu_reset();
    char dir[512];
    snprintf(dir, sizeof dir, "%s/recomp-mss32-stream-XXXXXX", os_temp_dir());
    CHECK(os_mkdtemp(dir) == 0);
    std::string music = std::string(dir) + "/Music";
    CHECK(os_mkdir(music.c_str()) == 0);
    std::string file = music + "/Test.mp3";
    FILE *f = fopen(file.c_str(), "wb");
    CHECK(f != nullptr);
    if (!f)
        return;
    CHECK_EQ(fwrite(kToneMp3, 1, sizeof kToneMp3, f), sizeof kToneMp3);
    CHECK_EQ(fclose(f), 0);
    win32_init(dir);
    g_plays.clear();
    g_queues.clear();
    g_queue_enabled = true;
    g_queued_bytes = 0;
    g_test_audio_pos = 0;
    g_ch_streaming = false;
    uint32_t name = sc(0x100);
    gm_put_str(name, "music\\test.mp3", 0x100);
    uint32_t s = call_shim(tramp("mss32.dll", "_AIL_open_stream@12"), {1, name, 0});
    CHECK(s != 0);
    CHECK_EQ(g_plays.size(), 0u); // opening alone must not sound
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_stream_volume@4"), {s}), 127u);
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_stream_status@4"), {s}), 2u);
    call_shim(tramp("mss32.dll", "_AIL_start_stream@4"), {s});
    mss32_frame_pump(nullptr);
    CHECK_EQ(g_queues.size(), 0u);
    g_queue_retired = true; // a refused frame is retained for the next tick
    mss32_frame_pump(&g_cpu);
    CHECK_EQ(g_queues.size(), 0u);
    g_queue_retired = false;
    mss32_frame_pump(&g_cpu);
    CHECK_EQ(g_plays.size(), 1u);
    CHECK(g_ch_streaming);
    if (!g_plays.empty()) {
        CHECK(host_audio_queued_bytes(g_plays[0].channel) > 0);
        CHECK_EQ(g_plays[0].rate, 44100);
        CHECK_EQ(g_plays[0].channels, 2);
        CHECK_EQ(g_plays[0].bits, 16);
        CHECK_EQ(g_plays[0].bytes + g_queued_bytes, 27648u);
    }
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_stream_status@4"), {s}), 4u);
    // Decoder EOF is already reached, but queued samples still have to sound.
    g_stream_played = 27648;
    g_queued_bytes = 0;
    mss32_frame_pump(&g_cpu);
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_stream_status@4"), {s}), 2u);

    call_shim(tramp("mss32.dll", "_AIL_set_stream_volume@8"), {s, 64});
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_stream_volume@4"), {s}), 64u);
    call_shim(tramp("mss32.dll", "_AIL_set_stream_loop_count@8"), {s, 2});
    call_shim(tramp("mss32.dll", "_AIL_start_stream@4"), {s});
    mss32_frame_pump(&g_cpu);
    if (!g_plays.empty()) {
        CHECK_EQ(g_plays.back().volume, -595);
        CHECK_EQ(g_plays.back().bytes + g_queued_bytes, 2u * 27648u);
        CHECK(g_plays.front().pcm == g_plays.back().pcm); // restart rewinds
    }
    g_stream_played = 2 * 27648;
    g_queued_bytes = 0;
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_stream_status@4"), {s}), 2u);

    call_shim(tramp("mss32.dll", "_AIL_set_stream_volume@8"), {s, 999});
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_stream_volume@4"), {s}), 127u);
    call_shim(tramp("mss32.dll", "_AIL_set_stream_loop_count@8"), {s, 0});
    call_shim(tramp("mss32.dll", "_AIL_start_stream@4"), {s});
    mss32_frame_pump(&g_cpu);
    const uint32_t ahead = 44100 * 2 * 2;
    CHECK(g_queued_bytes >= ahead && g_queued_bytes < ahead + 4608);
    size_t before = g_queues.size();
    mss32_frame_pump(&g_cpu);
    CHECK_EQ(g_queues.size(), before); // a full queue needs no more decoding
    g_stream_played += g_queued_bytes;
    g_queued_bytes = 0;
    mss32_frame_pump(&g_cpu);
    CHECK(g_queues.size() > before);
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_stream_status@4"), {s}), 4u);
    call_shim(tramp("mss32.dll", "_AIL_close_stream@4"), {s});
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_stream_status@4"), {s}), 2u);
    before = g_queues.size();
    mss32_frame_pump(&g_cpu);
    CHECK_EQ(g_queues.size(), before);
    CHECK_EQ(g_queued_bytes, 0u);
    gm_put_str(name, "music\\absent.mp3", 0x100);
    CHECK_EQ(call_shim(tramp("mss32.dll", "_AIL_open_stream@12"), {1, name, 0}), 0u);
    g_queue_enabled = false;
    CHECK_EQ(remove(file.c_str()), 0);
    CHECK_EQ(os_rmdir(music.c_str()), 0);
    CHECK_EQ(os_rmdir(dir), 0);
}

static void test_bink_smack_stubs() {
    cpu_reset();
    uint32_t name = sc(0x100);
    gm_put_str(name, "intro.bik", 0x100);
    uint32_t bink = call_shim(tramp("binkw32.dll", "_BinkOpen@8"), {name, 0});
#ifdef RECOMP_HAVE_FFMPEG
    CHECK_EQ(bink, 0u); // a missing file must fail with a readable error
    if (bink)
        call_shim(tramp("binkw32.dll", "_BinkClose@4"), {bink});
#else
    CHECK(bink != 0);
    CHECK_EQ(rd32(bink + 0x00), 640u);
    CHECK_EQ(rd32(bink + 0x04), 480u);
    CHECK_EQ(rd32(bink + 0x10), 0u); // frame count: a finished video
    CHECK_EQ(rd32(bink + 0x14), 0u); // current frame
    CHECK_EQ(rd32(bink + 0x08), 0u);
    CHECK_EQ(call_shim(tramp("binkw32.dll", "_BinkGetRects@8"), {bink, 0}), 0u);
    CHECK_EQ(rd32(bink + 0xb4), 0u);
    CHECK_EQ(call_shim(tramp("binkw32.dll", "_BinkPause@8"), {bink, 1}), 0u);
    CHECK_EQ(call_shim(tramp("binkw32.dll", "_BinkWait@4"), {bink}), 0u);
    CHECK_EQ(call_shim(tramp("binkw32.dll", "_BinkDoFrame@4"), {bink}), 0u);
    call_shim(tramp("binkw32.dll", "_BinkNextFrame@4"), {bink});
    CHECK_EQ(rd32(bink + 0x14), 0u); // still finished
    call_shim(tramp("binkw32.dll", "_BinkClose@4"), {bink});
    CHECK(!heap_owns(bink));
#endif
    uint32_t err = call_shim(tramp("binkw32.dll", "_BinkGetError@0"), {});
    CHECK(err != 0);
#ifdef RECOMP_HAVE_FFMPEG
    CHECK(!gm_str(err).empty());
#else
    CHECK(gm_str(err).empty());
#endif
    CHECK_EQ(call_shim(tramp("smackw32.dll", "_SmackOpen@12"), {0, 0, 0}), 0u);
    CHECK_EQ(imports_argc(tramp("binkw32.dll", "_BinkCopyToBuffer@28")), 7u);
    CHECK_EQ(imports_argc(tramp("smackw32.dll", "_SmackToBuffer@28")), 7u);
}

static void test_bink_entry_points_resolve() {
    cpu_reset();
    gm_put_str(sc(0), "BINKW32.DLL", 0x100);
    uint32_t load = tramp("KERNEL32.dll", "LoadLibraryA");
    uint32_t get_proc = tramp("KERNEL32.dll", "GetProcAddress");
    uint32_t module = call_shim(load, {sc(0)});
    CHECK(module != 0);
    const char *const names[] = {
        "_BinkPause@8",          "_BinkDDSurfaceType@4",   "_BinkDoFrame@4", "_BinkCopyToBuffer@28",
        "_BinkGetRects@8",       "_BinkNextFrame@4",       "_BinkWait@4",    "_BinkClose@4",
        "_BinkSetSoundSystem@8", "_BinkOpenDirectSound@4", "_BinkOpen@8",
    };
    for (const char *name : names) {
        gm_put_str(sc(0x100), name, 0x100);
        uint32_t entry = call_shim(get_proc, {module, sc(0x100)});
        if (!entry)
            fprintf(stderr, "Bink entry point unresolved: %s\n", name);
        CHECK(entry != 0);
        CHECK_EQ(entry, imports_resolve("binkw32.dll", name));
    }
    gm_put_str(sc(0), "bInKw32.dLl", 0x100);
    CHECK_EQ(call_shim(load, {sc(0)}), module);
    // An upper-case registry spelling must be loadable too; an unregistered
    // DLL must still fail instead of receiving a meaningless pseudo handle.
    gm_put_str(sc(0), "ddraw.dll", 0x100);
    uint32_t ddraw = call_shim(load, {sc(0)});
    CHECK(ddraw != 0);
    gm_put_str(sc(0x100), "DirectDrawCreate", 0x100);
    CHECK_EQ(call_shim(get_proc, {ddraw, sc(0x100)}), tramp("DDRAW.dll", "DirectDrawCreate"));
    gm_put_str(sc(0), "unregistered_video.dll", 0x100);
    CHECK_EQ(call_shim(load, {sc(0)}), 0u);
}

static void test_video_frame_convert() {
    // A 4x2 YUV420P image: black/white on the left, saturated red on the
    // right. Chroma is shared across the two rows; row padding stays intact.
    const uint8_t y[2][4] = {{16, 235, 81, 81}, {235, 16, 81, 81}};
    const uint8_t u[] = {128, 90}, v[] = {128, 240};
    const uint16_t rgb565[2][4] = {{0, 0xffff, 0xf800, 0xf800}, {0xffff, 0, 0xf800, 0xf800}};
    for (uint32_t row = 0; row < 2; ++row) {
        uint8_t dest[20];
        memset(dest, 0xa5, sizeof dest);
        video_frame_convert_row(dest, y[row], u, v, 4, VIDEO_RGB565);
        for (uint32_t x = 0; x < 4; ++x)
            CHECK_EQ((uint32_t)(dest[x * 2] | dest[x * 2 + 1] << 8), rgb565[row][x]);
        CHECK_EQ(dest[8], 0xa5u);
        video_frame_convert_row(dest, y[row], u, v, 4, VIDEO_RGB555);
        CHECK_EQ((uint32_t)(dest[4] | dest[5] << 8), 0x7c00u);
        video_frame_convert_row(dest, y[row], u, v, 4, VIDEO_XRGB8888);
        CHECK_EQ(dest[8], 0u);
        CHECK_EQ(dest[9], 0u);
        CHECK_EQ(dest[10], 255u);
        CHECK_EQ(dest[11], 0u);
        CHECK_EQ(dest[16], 0xa5u);
    }
}

// An optional private container exercises the real guest file API and decoder.
// The player must use its own descriptor so decoding never seeks the guest's.
static void with_bink_container(void (*check_record)(uint32_t rec, uint32_t handle)) {
    const char *container = recomp_env("TEST_BINK_CONTAINER");
    if (!container || !*container) {
        printf("bink container test: RECOMP_TEST_BINK_CONTAINER unset, skipped\n");
        return;
    }
    std::string spec = container;
    size_t comma = spec.rfind(',');
    CHECK(comma != std::string::npos);
    if (comma == std::string::npos)
        return;
    std::string host = spec.substr(0, comma);
    unsigned long long offset = 0;
    char extra = 0;
    bool valid_offset =
        sscanf(spec.c_str() + comma + 1, "%llu%c", &offset, &extra) == 1 && offset <= INT64_MAX;
    CHECK(valid_offset);
    if (!valid_offset)
        return;
    size_t slash = host.find_last_of("/\\");
    CHECK(slash != std::string::npos);
    if (slash == std::string::npos)
        return;
    cpu_reset();
    std::string previous_dir = win32_game_dir();
    win32_init(host.substr(0, slash ? slash : 1));
    std::string guest = win32_guest_path(host);
    uint32_t name = heap_alloc((uint32_t)guest.size() + 1, true, 16);
    CHECK(name != 0);
    if (!name) {
        win32_init(previous_dir);
        return;
    }
    gm_put_str(name, guest.c_str(), (uint32_t)guest.size() + 1);
    uint32_t handle =
        call_shim(tramp("KERNEL32.dll", "CreateFileA"), {name, 0x80000000, 1, 0, 3, 0, 0});
    CHECK(handle != 0 && handle != 0xffffffffu);
    if (handle != 0 && handle != 0xffffffffu) {
        uint32_t seek = tramp("KERNEL32.dll", "SetFilePointer");
        wr32(sc(0x100), (uint32_t)(offset >> 32));
        CHECK_EQ(call_shim(seek, {handle, (uint32_t)offset, sc(0x100), 0}), (uint32_t)offset);
        CHECK_EQ(rd32(sc(0x100)), (uint32_t)(offset >> 32));
        uint32_t rec = call_shim(tramp("binkw32.dll", "_BinkOpen@8"), {handle, 0x00800000});
        CHECK(rec != 0);
        if (rec) {
            check_record(rec, handle);
            call_shim(tramp("binkw32.dll", "_BinkClose@4"), {rec});
            CHECK(!heap_owns(rec));
        }
        CHECK_EQ(call_shim(seek, {handle, 0, 0, 1}), (uint32_t)offset);
        CHECK_EQ(call_shim(tramp("KERNEL32.dll", "CloseHandle"), {handle}), 1u);
    }
    heap_free(name);
    win32_init(previous_dir);
}

static void test_bink_open_from_handle() {
    with_bink_container([](uint32_t rec, uint32_t) {
        uint32_t width = rd32(rec), height = rd32(rec + 4);
        CHECK(width >= 16 && width <= 4096);
        CHECK(height >= 16 && height <= 4096);
        CHECK(rd32(rec + 0x10) > 0);
        CHECK_EQ(rd32(rec + 0x14), 1u);
        printf("bink container test: %ux%u, %u frames\n", width, height, rd32(rec + 0x10));
        if (width >= 16 && width <= 4096 && height >= 16 && height <= 4096) {
            uint32_t pitch = width * 2, bytes = pitch * height;
            uint32_t dest = heap_alloc(bytes, true, 16);
            CHECK(dest != 0);
            if (dest) {
                // A video may fade in from black. Decode until RGB565
                // contains different pixels, with a bounded frame budget.
                bool nonuniform = false;
                uint32_t decoded_frames = 0;
                while (decoded_frames < 45 && !nonuniform) {
                    call_shim(tramp("binkw32.dll", "_BinkDoFrame@4"), {rec});
                    ++decoded_frames;
                    call_shim(tramp("binkw32.dll", "_BinkNextFrame@4"), {rec});
                    CHECK_EQ(rd32(rec + 0x14), decoded_frames + 1);
                    memset(g_mem + dest, 0xa5, bytes);
                    call_shim(tramp("binkw32.dll", "_BinkCopyToBuffer@28"),
                              {rec, dest, pitch, height, 0, 0, 10});
                    for (uint32_t i = 2; i < bytes; i += 2)
                        nonuniform |= rd16(dest + i) != rd16(dest);
                }
                printf("bink container test: %s after decoding %u frames (limit 45)\n",
                       nonuniform ? "non-uniform RGB565" : "still uniform RGB565", decoded_frames);
                CHECK(nonuniform);
                uint32_t error = call_shim(tramp("binkw32.dll", "_BinkGetError@0"), {});
                CHECK(error && gm_str(error).empty());
                heap_free(dest);
            }
        }
    });
}

static void test_bink_audio_without_service() {
    with_bink_container([](uint32_t rec, uint32_t) {
        g_plays.clear();
        g_queues.clear();
        g_queue_enabled = true;
        g_queued_bytes = 0;
        g_test_audio_pos = 0;
        g_ch_streaming = false;
        const uint32_t calls[] = {
            tramp("binkw32.dll", "_BinkDoFrame@4"),
            tramp("binkw32.dll", "_BinkNextFrame@4"),
            tramp("binkw32.dll", "_BinkWait@4"),
        };
        for (unsigned frame = 0; frame < 3; ++frame) {
            for (uint32_t entry : calls) {
                // Model playback between calls so each entry must refill
                // the same stream, rather than only starting it once.
                uint32_t consumed = std::min(g_queued_bytes, 1024u);
                g_queued_bytes -= consumed;
                g_stream_played += consumed;
                uint64_t accepted = g_queued_accepted;
                call_shim(entry, {rec});
                CHECK_EQ(g_plays.size(), 1u);
                CHECK(g_ch_streaming);
                if (!g_plays.empty())
                    CHECK(host_audio_queued_bytes(g_plays[0].channel) > 0);
                if (consumed)
                    CHECK(g_queued_accepted > accepted);
            }
        }
        CHECK_EQ(rd32(rec + 0x14), 4u);
        g_queue_enabled = false;
    });
}

static void test_bink_shutdown_with_open_player() {
    with_bink_container([](uint32_t rec, uint32_t handle) {
        g_plays.clear();
        g_queues.clear();
        g_stops.clear();
        g_queue_enabled = true;
        g_queued_bytes = 0;
        g_test_audio_pos = 0;
        g_ch_streaming = false;
        uint32_t open = tramp("binkw32.dll", "_BinkOpen@8");
        uint32_t decode = tramp("binkw32.dll", "_BinkDoFrame@4");
        uint32_t close = tramp("binkw32.dll", "_BinkClose@4");
        CHECK_EQ(call_shim(decode, {rec}), 0u);
        CHECK_EQ(g_plays.size(), 1u);
        CHECK(g_ch_streaming);
        CHECK(g_queued_bytes > 0);

        // Leave the player open, as a guest ExitProcess can do mid-movie.
        bink_shutdown();
        CHECK(!heap_owns(rec));
        CHECK_EQ(g_stops.size(), 1u);
        if (!g_plays.empty() && !g_stops.empty())
            CHECK_EQ(g_stops[0], g_plays[0].channel);
        CHECK_EQ(g_queued_bytes, 0u);
        CHECK_EQ(call_shim(decode, {rec}), 0u);
        CHECK_EQ(call_shim(tramp("binkw32.dll", "_BinkGetRects@8"), {rec, 0}), 0u);
        CHECK_EQ(call_shim(close, {rec}), 0u);
        CHECK_EQ(g_plays.size(), 1u);
        CHECK_EQ(g_stops.size(), 1u);

        // The handle is still at the stream start, and the channel is reusable.
        uint32_t reopened = call_shim(open, {handle, 0x00800000});
        CHECK(reopened != 0);
        if (reopened) {
            CHECK_EQ(call_shim(decode, {reopened}), 0u);
            CHECK_EQ(g_plays.size(), 2u);
            CHECK(g_ch_streaming);
            CHECK(g_queued_bytes > 0);
            if (g_plays.size() == 2)
                CHECK_EQ(g_plays[1].channel, g_plays[0].channel);
            CHECK_EQ(call_shim(close, {reopened}), 0u);
        }
        g_queue_enabled = false;
    });
}

static void test_bink_rects_and_pause() {
    cpu_reset();
    CHECK_EQ(call_shim(tramp("binkw32.dll", "_BinkOpenDirectSound@4"), {0}), 1u);
    CHECK_EQ(call_shim(tramp("binkw32.dll", "_BinkGetRects@8"), {0, 0}), 0u);
    CHECK_EQ(call_shim(tramp("binkw32.dll", "_BinkPause@8"), {0, 1}), 0u);
    const char *container = recomp_env("TEST_BINK_CONTAINER");
    if (!container || !*container)
        return;
    with_bink_container([](uint32_t rec, uint32_t handle) {
        uint32_t rects = tramp("binkw32.dll", "_BinkGetRects@8");
        uint32_t pause = tramp("binkw32.dll", "_BinkPause@8");
        uint32_t wait = tramp("binkw32.dll", "_BinkWait@4");
        wr32(rec + 0xb4, 8); // GetRects must clear a stale count before decode.
        CHECK_EQ(call_shim(rects, {rec, 0}), 0u);
        CHECK_EQ(rd32(rec + 0xb4), 0u);
        CHECK_EQ(call_shim(tramp("binkw32.dll", "_BinkDoFrame@4"), {rec}), 0u);
        CHECK_EQ(call_shim(rects, {rec, 0}), 1u);
        CHECK_EQ(rd32(rec + 0xb4), 1u);
        CHECK_EQ(rd32(rec + 0x34), 0u);
        CHECK_EQ(rd32(rec + 0x38), 0u);
        CHECK_EQ(rd32(rec + 0x3c), rd32(rec));
        CHECK_EQ(rd32(rec + 0x40), rd32(rec + 4));
        call_shim(tramp("binkw32.dll", "_BinkNextFrame@4"), {rec});
        CHECK_EQ(rd32(rec + 0x14), 2u);
        CHECK_EQ(call_shim(wait, {rec}), 1u);
        CHECK_EQ(call_shim(pause, {rec, 1}), 0u);
        uint32_t paused_at = host_millis();
        CHECK_EQ(call_shim(wait, {rec}), 1u);
        size_t plays = g_plays.size(), queues = g_queues.size();
        CHECK_EQ(call_shim(tramp("binkw32.dll", "_BinkService@4"), {rec}), 0u);
        CHECK_EQ(g_plays.size(), plays);
        CHECK_EQ(g_queues.size(), queues);

        // A second, unpaused record proves frame 2 really became due. Open
        // it later so its deadline cannot precede the paused record's, and
        // avoid assuming that every private video runs at the same rate.
        uint32_t control = call_shim(tramp("binkw32.dll", "_BinkOpen@8"), {handle, 0x00800000});
        CHECK(control != 0);
        if (control) {
            call_shim(tramp("binkw32.dll", "_BinkNextFrame@4"), {control});
            os_sleep_us(30 * 1000);
            uint32_t control_wait = call_shim(wait, {control});
            while (control_wait && (uint32_t)(host_millis() - paused_at) < 2000) {
                os_sleep_us(1000);
                control_wait = call_shim(wait, {control});
            }
            CHECK_EQ(control_wait, 0u);
            uint32_t paused_ms = host_millis() - paused_at;
            CHECK(paused_ms >= 30);
            CHECK_EQ(call_shim(wait, {rec}), 1u);
            CHECK_EQ(call_shim(pause, {rec, 0}), 0u);
            CHECK_EQ(call_shim(wait, {rec}), 1u);
            printf("bink pause test: paused %u ms; frame 2 due in control, waiting after resume\n",
                   paused_ms);
            call_shim(tramp("binkw32.dll", "_BinkClose@4"), {control});
        } else {
            call_shim(pause, {rec, 0});
        }
    });
}

static void test_bink_handle_flag_errors() {
    cpu_reset();
    uint32_t open = tramp("binkw32.dll", "_BinkOpen@8");
    uint32_t get_error = tramp("binkw32.dll", "_BinkGetError@0");
    CHECK_EQ(call_shim(open, {0x12345678, 0x00800000}), 0u);
    uint32_t error = call_shim(get_error, {});
    CHECK(error && !gm_str(error).empty());
    gm_put_str(sc(0), "intro.bik", 0x100);
    CHECK_EQ(call_shim(open, {sc(0), 0x04000000}), 0u);
    error = call_shim(get_error, {});
    CHECK(error && gm_str(error) == "memory-resident video is not supported");
}

static void test_bink_play() {
#ifdef RECOMP_HAVE_FFMPEG
    const std::string path =
        std::string(RECOMP_DEVELOPER_GAME_DIR) + "/BINKS/High/pre_dynastic_big.bik";
    FILE *file = fopen(path.c_str(), "rb");
    if (!file) {
        printf("note: Bink play skipped; developer video is absent\n");
        return;
    }
    fclose(file);
    cpu_reset();
    win32_init(RECOMP_DEVELOPER_GAME_DIR);
    g_plays.clear();
    g_queues.clear();
    g_stops.clear();
    g_queue_enabled = true;
    g_queued_bytes = 0;
    g_test_audio_pos = 0;
    g_ch_streaming = false;
    uint32_t name = sc(0x100);
    gm_put_str(name, "binks\\high\\pre_dynastic_big.bik", 0x100);
    uint32_t rec = call_shim(tramp("binkw32.dll", "_BinkOpen@8"), {name, 0});
    CHECK(rec != 0);
    if (!rec) {
        g_queue_enabled = false;
        return;
    }
    CHECK_EQ(heap_size(rec), 0x100u);
    CHECK_EQ(rd32(rec), 560u);
    CHECK_EQ(rd32(rec + 4), 333u);
    CHECK(rd32(rec + 0x10) > 0);
    CHECK_EQ(rd32(rec + 0x14), 1u);
    CHECK_EQ(rd32(rec + 8), rd32(rec + 0x10));
    CHECK_EQ(rd32(rec + 12), 1u);
    CHECK(g_plays.empty()); // open does not decode or play
    uint32_t err = call_shim(tramp("binkw32.dll", "_BinkGetError@0"), {});
    CHECK(err && gm_str(err).empty());
    const uint32_t pitch = 1280, height = 333;
    uint32_t dest = heap_alloc(pitch * height, true, 16);
    CHECK(dest != 0);
    for (uint32_t frame = 1; frame <= 2 && dest; ++frame) {
        call_shim(tramp("binkw32.dll", "_BinkDoFrame@4"), {rec});
        call_shim(tramp("binkw32.dll", "_BinkNextFrame@4"), {rec});
        CHECK_EQ(rd32(rec + 0x14), frame + 1);
        CHECK_EQ(rd32(rec + 12), frame + 1);
        memset(g_mem + dest, 0xa5, pitch * height);
        CHECK_EQ(call_shim(tramp("binkw32.dll", "_BinkCopyToBuffer@28"),
                           {rec, dest, pitch, height, 0, 0, VIDEO_RGB565}),
                 0u);
        bool nonuniform = false;
        for (uint32_t yrow = 0; yrow < height; ++yrow)
            for (uint32_t x = 0; x < 560; ++x)
                nonuniform |= rd16(dest + yrow * pitch + x * 2) != rd16(dest);
        printf("note: Bink frame %u is %s (first pixel %04x)\n", frame,
               nonuniform ? "non-uniform" : "uniform", rd16(dest));
        if (frame == 2)
            CHECK(nonuniform);
        CHECK_EQ(rd8(dest + 1120), 0xa5u); // the pitch is wider than the video
        call_shim(tramp("binkw32.dll", "_BinkService@4"), {rec});
        err = call_shim(tramp("binkw32.dll", "_BinkGetError@0"), {});
        CHECK(err && gm_str(err).empty());
    }
    CHECK_EQ(g_plays.size(), 1u);
    CHECK(g_ch_streaming);
    CHECK(g_queued_bytes > 0);
    if (!g_plays.empty()) {
        CHECK_EQ(g_plays[0].bits, 16);
        CHECK(g_plays[0].bytes > 0);
    }
    // A close must break both the timed wait and the guest's frame loop,
    // including the NextFrame call that follows a cancelled DoFrame.
    g_test_close_requested = true;
    CHECK_EQ(call_shim(tramp("binkw32.dll", "_BinkWait@4"), {rec}), 0u);
    call_shim(tramp("binkw32.dll", "_BinkDoFrame@4"), {rec});
    CHECK_EQ(rd32(rec + 0x14), rd32(rec + 0x10));
    CHECK_EQ(rd32(rec + 0x0c), rd32(rec + 0x08));
    call_shim(tramp("binkw32.dll", "_BinkNextFrame@4"), {rec});
    CHECK_EQ(rd32(rec + 0x14), rd32(rec + 0x10));
    CHECK_EQ(rd32(rec + 0x0c), rd32(rec + 0x08));
    g_test_close_requested = false;
    call_shim(tramp("binkw32.dll", "_BinkClose@4"), {rec});
    CHECK(!heap_owns(rec));
    if (!g_plays.empty()) {
        CHECK(!g_stops.empty() && g_stops.back() == g_plays[0].channel);
        int32_t channel = dx_alloc_audio_channel();
        CHECK_EQ(channel, g_plays[0].channel);
        dx_free_audio_channel(channel);
    }
    if (dest)
        heap_free(dest);
    g_queue_enabled = false;
#else
    printf("note: Bink play skipped; video decoding is disabled\n");
#endif
}

// QMixer: a session, a channel, and a wave supplied the way the game supplies
// one, as raw PCM plus an explicit WAVEFORMATEX in a five-dword record. There
// is no RIFF container on this path.
//
// Record layout, from the disassembly (see dxtypes.h):
//   +0x00 LPWAVEFORMATEX   +0x04 sample data   +0x08 byte count   +0x0c,+0x10 zero
static void test_qmixer() {
    cpu_reset();
    g_plays.clear();

    uint32_t init = tramp("QMIXER.dll", "QSWaveMixInitEx");
    CHECK(init != 0);
    uint32_t initdata = sc(0x100);
    gm_zero(initdata, 0x40);
    wr32(initdata + 0, 0x40); // dwSize, as the game passes
    wr32(initdata + 4, 0x12);
    wr32(initdata + 8, 0x5622); // 22050, as the game passes
    uint32_t hmix = call_shim(init, {initdata});
    CHECK(hmix != 0);

    uint32_t activate = tramp("QMIXER.dll", "QSWaveMixActivate");
    CHECK_EQ(call_shim(activate, {hmix, 1}), 0u);

    uint32_t open_ch = tramp("QMIXER.dll", "QSWaveMixOpenChannel");
    CHECK_EQ(call_shim(open_ch, {hmix, 3, 2}), 0u);

    // A WAVEFORMATEX exactly as the game's own slot 20 (0x5711c0) writes one:
    // PCM, 1 channel, 22050 Hz, 8 bit, nBlockAlign = (bits*ch+7)/8.
    uint32_t wfx = sc(0x200);
    gm_zero(wfx, SDK_WAVEFORMATEX);
    wr16(wfx + 0x00, 1);
    wr16(wfx + 0x02, 1);
    wr32(wfx + 0x04, 22050);
    wr32(wfx + 0x08, 22050);
    wr16(wfx + 0x0c, 1);
    wr16(wfx + 0x0e, 8);
    wr16(wfx + 0x10, 0);

    const uint32_t data_bytes = 64;
    uint32_t pcm = sc(0x400);
    for (uint32_t i = 0; i < data_bytes; ++i)
        wr8(pcm + i, (uint8_t)(i * 3));

    uint32_t owd = sc(0x300);
    gm_zero(owd, 20);
    wr32(owd + 0x00, wfx);
    wr32(owd + 0x04, pcm);
    wr32(owd + 0x08, data_bytes);

    uint32_t open_wave = tramp("QMIXER.dll", "QSWaveMixOpenWaveEx");
    uint32_t hwave = call_shim(open_wave, {hmix, owd, 8});
    CHECK(hwave != 0);

    // QMixer's volume is a linear amplitude on 0..32767, not decibels: its own
    // parameter handler multiplies the value by 1/32767 and uses that as the
    // gain. Half scale is therefore about -6 dB.
    uint32_t set_vol = tramp("QMIXER.dll", "QSWaveMixSetVolume");
    CHECK_EQ(call_shim(set_vol, {hmix, 3, 0, 16384}), 0u);

    uint32_t play = tramp("QMIXER.dll", "QSWaveMixPlayEx");
    CHECK_EQ(call_shim(play, {hmix, 3, 0x20, hwave, 0, 0}), 0u);
    CHECK_EQ(g_plays.size(), 1);
    if (!g_plays.empty()) {
        const PlayRecord &r = g_plays[0];
        CHECK_EQ(r.rate, 22050);
        CHECK_EQ(r.channels, 1);
        CHECK_EQ(r.bits, 8);
        CHECK_EQ(r.bytes, data_bytes);
        CHECK_EQ(r.volume, -602); // 2000 * log10(16384/32767)
        bool ok = true;
        for (uint32_t i = 0; i < data_bytes; ++i)
            if (r.pcm[i] != (uint8_t)(i * 3)) {
                ok = false;
                break;
            }
        CHECK(ok);
    }

    // Pump is called every frame and must stay quiet and cheap.
    uint32_t pump = tramp("QMIXER.dll", "QSWaveMixPump");
    CHECK_EQ(call_shim(pump, {}), 0u);

    uint32_t stop = tramp("QMIXER.dll", "QSWaveMixStopChannel");
    CHECK_EQ(call_shim(stop, {hmix, 3, 0}), 0u);

    uint32_t freew = tramp("QMIXER.dll", "QSWaveMixFreeWave");
    CHECK_EQ(call_shim(freew, {hmix, hwave}), 0u);

    uint32_t close = tramp("QMIXER.dll", "QSWaveMixCloseSession");
    CHECK_EQ(call_shim(close, {hmix}), 0u);
}

// weanetr: both network bring-up paths report unavailable so the game runs
// single-player, and GetCurrentMs is a real clock.
static void test_weanetr() {
    cpu_reset();
    uint32_t startup = tramp("weanetr.dll", "?StartupNetwork@MLDPlay@@QAEHP6GXKPAXKK0@Z@Z");
    CHECK(startup != 0);
    // A thiscall method: `this` is in ECX, not on the stack.
    g_cpu.r[R_ECX] = 0x01000000;
    CHECK_EQ(call_shim(startup, {0x401000}), 0u);

    // The other bring-up path. Its caller at 00413db0 compares the result
    // against 0x20, not against zero, so zero would read as "MLDPlay is up and
    // we were not lobbied" and send the game into the multiplayer lobby.
    uint32_t lobbied = tramp(
        "weanetr.dll",
        "?AreWeLobbied@MLDPlay@@QAEKP6GXKPAXKK0@ZPAU_GUID@@PAUMLDPLAY_LOBBYINFO@@PAKPAG5KK@Z");
    CHECK(lobbied != 0);
    g_cpu.r[R_ECX] = 0x01000000;
    CHECK_EQ(call_shim(lobbied, {0x401000, 0, 0, 0, 0, 0, 4, 0}), 0x20u);

    uint32_t ms = tramp("weanetr.dll", "?GetCurrentMs@MLDPlay@@QAEKXZ");
    CHECK(ms != 0);
    uint32_t t = call_shim(ms, {});
    CHECK_EQ(t, host_millis());

    uint32_t enum_sessions = tramp(
        "weanetr.dll", "?EnumerateSessions@MLDPlay@@QAEHKP6GXPAUMLDPLAY_SESSIONDESC@@PAX@ZK1@Z");
    CHECK(enum_sessions != 0);
    CHECK_EQ(call_shim(enum_sessions, {0, 0, 0, 0}), 0u);

    uint32_t shutdown = tramp("weanetr.dll", "?ShutdownNetwork@MLDPlay@@QAEHXZ");
    CHECK_EQ(call_shim(shutdown, {}), 0u);
}

// Every vtable slot must hold a distinct, allocated trampoline: a duplicate
// would mean two methods sharing one implementation by accident.
static void test_vtable_integrity() {
    struct {
        ComIface iface;
        uint32_t slots;
        const char *name;
    } ifaces[] = {
        {IF_DIRECTDRAW, 23, "IDirectDraw"},
        {IF_DIRECTDRAW2, 24, "IDirectDraw2"},
        {IF_DIRECTDRAW4, 28, "IDirectDraw4"},
        {IF_DDSURFACE, 36, "IDirectDrawSurface"},
        {IF_DDSURFACE2, 39, "IDirectDrawSurface2"},
        {IF_DDSURFACE3, 40, "IDirectDrawSurface3"},
        {IF_DDSURFACE4, 45, "IDirectDrawSurface4"},
        {IF_DDPALETTE, 7, "IDirectDrawPalette"},
        {IF_DDCLIPPER, 9, "IDirectDrawClipper"},
        {IF_D3D, 9, "IDirect3D"},
        {IF_D3D2, 9, "IDirect3D2"},
        {IF_D3D3, 12, "IDirect3D3"},
        {IF_D3DDEVICE3, 42, "IDirect3DDevice3"},
        {IF_D3DVIEWPORT3, 21, "IDirect3DViewport3"},
        {IF_D3DMATERIAL3, 6, "IDirect3DMaterial3"},
        {IF_D3DDEVICE2, 33, "IDirect3DDevice2"},
        {IF_D3DVIEWPORT2, 18, "IDirect3DViewport2"},
        {IF_D3DMATERIAL2, 6, "IDirect3DMaterial2"},
        {IF_D3DLIGHT, 6, "IDirect3DLight"},
        {IF_D3DTEXTURE2, 6, "IDirect3DTexture2"},
        {IF_DSOUND, 11, "IDirectSound"},
        {IF_DSBUFFER, 21, "IDirectSoundBuffer"},
        {IF_DS3DBUFFER, 21, "IDirectSound3DBuffer"},
        {IF_DS3DLISTENER, 18, "IDirectSound3DListener"},
        {IF_DSNOTIFY, 4, "IDirectSoundNotify"},
        {IF_DINPUT, 8, "IDirectInputA"},
        {IF_DINPUTDEVICE, 27, "IDirectInputDeviceA"},
    };
    for (auto &e : ifaces) {
        uint32_t vt = com_vtable_of(e.iface);
        if (!vt) {
            ++g_checks;
            ++g_failures;
            fprintf(stderr, "FAIL: %s has no vtable\n", e.name);
            continue;
        }
        std::vector<uint32_t> seen;
        for (uint32_t i = 0; i < e.slots; ++i) {
            uint32_t t = rd32(vt + i * 4);
            ++g_checks;
            if (!imports_is_trampoline(t)) {
                ++g_failures;
                fprintf(stderr, "FAIL: %s slot %u holds %08x, not a trampoline\n", e.name, i, t);
                continue;
            }
            for (uint32_t s : seen) {
                if (s == t) {
                    ++g_failures;
                    fprintf(stderr, "FAIL: %s slot %u repeats an earlier slot\n", e.name, i);
                    break;
                }
            }
            seen.push_back(t);
        }
        // The slot immediately past the interface must belong to a different
        // interface or be unallocated: catching an off-by-one in the count.
        ++g_checks;
        if (rd32(vt + e.slots * 4) != 0 && imports_is_trampoline(rd32(vt + e.slots * 4))) {
            // Reading past the allocation is not itself an error, since the
            // heap may hold another vtable there; only report a slot that is
            // one of this interface's own.
            for (uint32_t s : seen) {
                if (s == rd32(vt + e.slots * 4)) {
                    ++g_failures;
                    fprintf(stderr, "FAIL: %s appears to have more than %u slots\n", e.name,
                            e.slots);
                    break;
                }
            }
        }
    }
}

// Releasing every interface really destroys the object, and the guest memory
// the surfaces held goes back to the heap.
static void test_refcounts() {
    cpu_reset();
    uint32_t before_live = com_live_count();
    HeapStats hs_before = heap_stats();

    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));
    call_method(dd, DD_SetDisplayMode, {640, 480, 8});

    uint32_t desc = sc(0x100);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_OFFSCREENPLAIN);
    wr32(desc + DDSD_OFF_dwWidth, 256);
    wr32(desc + DDSD_OFF_dwHeight, 256);
    call_method(dd, DD_CreateSurface, {desc, sc(4), 0});
    uint32_t s = rd32(sc(4));
    CHECK(s != 0);

    // AddRef then two Releases: the object survives the first.
    uint32_t r = call_method(s, 1 /* AddRef */, {});
    CHECK_EQ(r, 2u);
    r = call_method(s, S_Release, {});
    CHECK_EQ(r, 1u);
    CHECK(com_this(s) != nullptr);
    r = call_method(s, S_Release, {});
    CHECK_EQ(r, 0u);
    CHECK(com_this(s) == nullptr);

    call_method(dd, DD_Release, {});
    CHECK_EQ(com_live_count(), before_live);

    HeapStats hs_after = heap_stats();
    // The surface's pixels and the object's views went back to the heap. The
    // vtables and the scratch buffers stay, so this compares live blocks
    // against the count before this test rather than against zero.
    CHECK_EQ(hs_after.used_blocks, hs_before.used_blocks);
}

// ---------------------------------------------------------------------------
// Fix-round tests. Each one covers a defect the review found; they use the
// independently derived SDK_ sizes above, so an implementation constant that
// drifts back to a wrong value fails here.
// ---------------------------------------------------------------------------

// SetRenderTarget on the surface the device is already rendering into must not
// destroy it. Releasing the outgoing target before retaining the incoming one
// is fine while they differ and fatal when they are the same object and the
// device holds its last reference: the release destroys it, and the addref,
// the flush and the host's new pointer all then work on a dead object.
static void test_setrendertarget_same_surface() {
    cpu_reset();
    uint32_t before_live = com_live_count();

    uint32_t dev = make_d3d_device();
    CHECK(dev != 0);
    if (!dev)
        return;

    // The surface the device was created with, as the guest would fetch it.
    CHECK_EQ(call_method(dev, 16 /* GetRenderTarget */, {sc(0x20)}), 0u);
    uint32_t target = rd32(sc(0x20));
    CHECK(target != 0);
    // Three references now: the one CreateSurface handed the guest, the one
    // CreateDevice took, and the one GetRenderTarget just took. Give back
    // GetRenderTarget's and the guest's own, so the device holds the last
    // reference - which is the case that breaks.
    CHECK_EQ(call_method(target, S_Release, {}), 2u);
    CHECK_EQ(call_method(target, S_Release, {}), 1u);
    ComObj *obj = com_this(target);
    CHECK(obj != nullptr);
    if (!obj)
        return;
    CHECK_EQ(obj->refs, 1);

    // Set the same surface again. It has to survive, with the count unchanged:
    // a device that already holds a reference for a target does not need a
    // second one for the same surface.
    CHECK_EQ(call_method(dev, DEV_SetRenderTarget, {target, 0}), 0u);
    obj = com_this(target);
    CHECK(obj != nullptr);
    if (!obj)
        return;
    CHECK_EQ(obj->refs, 1);

    // And it is still usable afterwards, which a destroyed object would not be.
    CHECK_EQ(call_method(dev, 16 /* GetRenderTarget */, {sc(0x24)}), 0u);
    CHECK_EQ(rd32(sc(0x24)), target);
    call_method(target, S_Release, {});

    // Releasing the device releases the target with it.
    call_method(dev, 2 /* Release */, {});
    CHECK(com_this(target) == nullptr);
    CHECK(com_live_count() >= before_live);
}

// The record sizes and flag bits the shims use must match the SDK layouts.
static void test_sdk_abi() {
    CHECK_EQ(DDSD_SIZE, SDK_DDSURFACEDESC);
    CHECK_EQ(DDSD2_SIZE, SDK_DDSURFACEDESC2);
    CHECK_EQ(DDPF_SIZE, SDK_DDPIXELFORMAT);
    CHECK_EQ(DDBLTFX_SIZE, SDK_DDBLTFX);
    CHECK_EQ(DDDEVID_SIZE, SDK_DDDEVICEIDENTIFIER);
    CHECK_EQ(D3DPRIMCAPS_SIZE, SDK_D3DPRIMCAPS);
    CHECK_EQ(D3DDEVICEDESC_SIZE, SDK_D3DDEVICEDESC);
    CHECK_EQ(D3DDD_OFF_dpcTriCaps, SDK_D3DDD_dpcTriCaps_OFF);
    CHECK_EQ(D3DFDS_SIZE, SDK_D3DFINDDEVICESEARCH);
    CHECK_EQ(D3DFDR_SIZE, SDK_D3DFINDDEVICERESULT);
    CHECK_EQ(D3DVIEWPORT_SIZE, SDK_D3DVIEWPORT);
    CHECK_EQ(D3DVIEWPORT2_SIZE, SDK_D3DVIEWPORT2);
    CHECK_EQ(D3DCLIPSTATUS_SIZE, SDK_D3DCLIPSTATUS);
    CHECK_EQ(DSBUFFERDESC_SIZE, SDK_DSBUFFERDESC);
    CHECK_EQ(WFX_SIZE, SDK_WAVEFORMATEX);
    CHECK_EQ(DIDEVICEOBJECTDATA_SIZE, SDK_DIDEVICEOBJECTDATA);
    CHECK_EQ(DIPROPDWORD_SIZE, SDK_DIPROPDWORD);
    CHECK_EQ(DIMOUSESTATE_SIZE, SDK_DIMOUSESTATE);
    CHECK_EQ(D3DFDS_COLORMODEL, SDK_D3DFDS_COLORMODEL);
    CHECK_EQ(D3DFDS_GUID, SDK_D3DFDS_GUID);
    CHECK_EQ(D3DFDS_HARDWARE, SDK_D3DFDS_HARDWARE);
}

// Reproduces init_d3d's search byte for byte: dwSize 0x5c, dwFlags 2, the
// rest of the record zero except the HAL GUID at offset 0x10. With the flag
// bits wrong this matched a software device instead.
static void test_find_device_literal() {
    cpu_reset();
    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));

    uint32_t iid = sc(0x40);
    uint8_t d3d2[16] = {0xC1, 0x1E, 0xAE, 0x6A, 0x2A, 0x66, 0xD0, 0x11,
                        0x88, 0x9D, 0x00, 0xAA, 0x00, 0xBB, 0xB7, 0x6A};
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, d3d2[i]);
    call_method(dd, DD_QueryInterface, {iid, sc(0x60)});
    uint32_t d3d = rd32(sc(0x60));
    CHECK(d3d != 0);

    const uint8_t hal[16] = {0xE0, 0x3D, 0xE6, 0x84, 0xAA, 0x46, 0xCF, 0x11,
                             0x81, 0x6F, 0x00, 0x00, 0xC0, 0x20, 0x15, 0x6E};
    uint32_t search = sc(0x100), result = sc(0x200);
    // The game zeroes 0x17 dwords, then writes dwSize and dwFlags and the
    // GUID; bHardware and dcmColorModel stay zero.
    gm_zero(search, SDK_D3DFINDDEVICESEARCH);
    wr32(search + 0x00, 0x5c);
    wr32(search + 0x04, 2);
    for (int i = 0; i < 16; ++i)
        wr8(search + 0x10 + (uint32_t)i, hal[i]);
    gm_zero(result, SDK_D3DFINDDEVICERESULT);
    wr32(result + 0x00, 0x20c);

    uint32_t hr = call_method(d3d, D3D_FindDevice, {search, result});
    CHECK_EQ(hr, D3D_OK_);
    // It must be the HAL device that came back, not a software one.
    bool guid_is_hal = true;
    for (int i = 0; i < 16; ++i)
        if (rd8(result + 0x04 + (uint32_t)i) != hal[i])
            guid_is_hal = false;
    CHECK(guid_is_hal);
    // And the suitability gate init_d3d applies to dpcTriCaps must pass.
    uint32_t tri = result + 0x14 + SDK_D3DDD_dpcTriCaps_OFF;
    CHECK((rd32(tri + 0x14) & 1u) != 0);      // dwDestBlendCaps
    CHECK((rd32(tri + 0x10) & 0x1000u) != 0); // dwSrcBlendCaps
    CHECK((rd32(tri + 0x18) & 2u) != 0);      // dwAlphaCmpCaps
    CHECK(rd32(result + 0x10) != 0);          // last GUID dword non-zero

    // A hardware-only search that contradicts a software GUID finds nothing.
    gm_zero(search, SDK_D3DFINDDEVICESEARCH);
    wr32(search + 0x00, 0x5c);
    wr32(search + 0x04, SDK_D3DFDS_GUID | SDK_D3DFDS_HARDWARE);
    const uint8_t rgb[16] = {0x60, 0x5C, 0x66, 0xA4, 0x73, 0x26, 0xCF, 0x11,
                             0xA3, 0x1A, 0x00, 0xAA, 0x00, 0xB9, 0x33, 0x56};
    for (int i = 0; i < 16; ++i)
        wr8(search + 0x10 + (uint32_t)i, rgb[i]);
    wr32(search + 0x08, 1); // bHardware
    gm_zero(result, SDK_D3DFINDDEVICERESULT);
    wr32(result, 0x20c);
    CHECK_EQ(call_method(d3d, D3D_FindDevice, {search, result}), DDERR_NOTFOUND);
}

// The game's viewport record is 11 dwords and reaches SetViewport2 at +0x44.
static void test_viewport_record() {
    cpu_reset();
    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));
    uint32_t iid = sc(0x40);
    uint8_t d3d2[16] = {0xC1, 0x1E, 0xAE, 0x6A, 0x2A, 0x66, 0xD0, 0x11,
                        0x88, 0x9D, 0x00, 0xAA, 0x00, 0xBB, 0xB7, 0x6A};
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, d3d2[i]);
    call_method(dd, DD_QueryInterface, {iid, sc(0x60)});
    uint32_t d3d = rd32(sc(0x60));
    call_method(d3d, D3D_CreateViewport, {sc(16), 0});
    uint32_t vp = rd32(sc(16));
    CHECK(vp != 0);

    // A canary immediately after an 11-dword record must survive.
    uint32_t v = sc(0x800);
    gm_zero(v, SDK_D3DVIEWPORT2);
    wr32(v + SDK_D3DVIEWPORT2, 0xA5A5A5A5u);
    wr32(v + 0x00, SDK_D3DVIEWPORT2);
    wr32(v + 0x04, 16);
    wr32(v + 0x08, 24);
    wr32(v + 0x0c, 320);
    wr32(v + 0x10, 200);
    wrf32(v + 0x24, 0.0f);
    wrf32(v + 0x28, 1.0f);
    uint32_t hr = call_method(vp, VP_SetViewport2, {v});
    CHECK_EQ(hr, D3D_OK_);
    CHECK_EQ(rd32(v + SDK_D3DVIEWPORT2), 0xA5A5A5A5u);

    // And it round-trips through GetViewport2.
    uint32_t g = sc(0xc00);
    gm_zero(g, SDK_D3DVIEWPORT2);
    wr32(g + 0x00, SDK_D3DVIEWPORT2);
    hr = call_method(vp, 16 /* GetViewport2 */, {g});
    CHECK_EQ(hr, D3D_OK_);
    CHECK_EQ(rd32(g + 0x04), 16);
    CHECK_EQ(rd32(g + 0x0c), 320);
    CHECK_EQ(rd32(g + 0x10), 200);
}

// GetDeviceData must accept the 16-byte DirectInput 5 record the game uses,
// and must not write past it.
static void test_device_data_stride16() {
    cpu_reset();
    memset(&g_input, 0, sizeof g_input);
    uint32_t create = tramp("DINPUT.dll", "DirectInputCreateA");
    call_shim(create, {0x400000, 0x0500, sc(0), 0});
    uint32_t di = rd32(sc(0));

    uint32_t guid = sc(0x40);
    uint8_t mouse[16] = {0x60, 0x2B, 0x1D, 0x6F, 0xA0, 0xD5, 0xCF, 0x11,
                         0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00};
    for (int i = 0; i < 16; ++i)
        wr8(guid + (uint32_t)i, mouse[i]);
    call_method(di, DI_CreateDevice, {guid, sc(8), 0});
    uint32_t ms = rd32(sc(8));
    CHECK(ms != 0);

    uint32_t df = sc(0x100);
    gm_zero(df, 24);
    wr32(df + 0, 24);
    wr32(df + 4, 16);
    wr32(df + 12, SDK_DIMOUSESTATE);
    call_method(ms, DID_SetDataFormat, {df});

    uint32_t prop = sc(0x300);
    gm_zero(prop, SDK_DIPROPDWORD);
    wr32(prop + 0, SDK_DIPROPDWORD);
    wr32(prop + 4, 16);
    wr32(prop + 16, 32);
    CHECK_EQ(call_method(ms, DID_SetProperty, {1, prop}), DI_OK);
    CHECK_EQ(call_method(ms, DID_Acquire, {}), DI_OK);

    g_input.mouse_dx = 5;
    g_input.mouse_buttons[0] = 0x80;

    // The game's own call shape: 10 events into a 160-byte buffer at a
    // 16-byte stride, with a canary just past it.
    uint32_t inout = sc(0x500);
    wr32(inout, 10);
    uint32_t data = sc(0x800);
    gm_zero(data, 10 * SDK_DIDEVICEOBJECTDATA);
    wr32(data + 10 * SDK_DIDEVICEOBJECTDATA, 0x5A5A5A5Au);
    uint32_t hr = call_method(ms, DID_GetDeviceData, {SDK_DIDEVICEOBJECTDATA, data, inout, 0});
    CHECK_EQ(hr, DI_OK);
    uint32_t n = rd32(inout);
    CHECK_EQ(n, 2);                                           // the x delta and the button
    CHECK_EQ(rd32(data + 0 * SDK_DIDEVICEOBJECTDATA + 0), 0); // dwOfs lX
    CHECK_EQ((int32_t)rd32(data + 0 * SDK_DIDEVICEOBJECTDATA + 4), 5);
    CHECK_EQ(rd32(data + 1 * SDK_DIDEVICEOBJECTDATA + 0), 12); // button 0
    CHECK_EQ(rd32(data + 1 * SDK_DIDEVICEOBJECTDATA + 4), 0x80);
    CHECK_EQ(rd32(data + 10 * SDK_DIDEVICEOBJECTDATA), 0x5A5A5A5Au);

    // A stride the SDK never defined is refused rather than half-honoured.
    wr32(inout, 4);
    CHECK_EQ(call_method(ms, DID_GetDeviceData, {12, data, inout, 0}), DIERR_INVALIDPARAM);
}

// GetClipStatus writes 32 bytes; the dword after must be untouched.
static void test_clipstatus_canary() {
    cpu_reset();
    uint32_t dev = make_d3d_device();
    CHECK(dev != 0);
    uint32_t cs = sc(0xc00);
    gm_zero(cs, SDK_D3DCLIPSTATUS);
    wr32(cs + SDK_D3DCLIPSTATUS, 0xC0FFEE00u);
    uint32_t hr = call_method(dev, DEV_GetClipStatus, {cs});
    CHECK_EQ(hr, D3D_OK_);
    CHECK_EQ(rd32(cs + SDK_D3DCLIPSTATUS), 0xC0FFEE00u);
}

// Guest-controlled sizes that would wrap a 32-bit product are refused, and no
// draw reaches the host.
static void test_overflow_rejection() {
    cpu_reset();
    g_draws.clear();
    uint32_t dev = make_d3d_device();
    CHECK(dev != 0);

    uint32_t verts = sc(0x800);
    // 32 * 0x08000001 wraps to 32: a naive check would validate 32 bytes and
    // forward the full count.
    uint32_t hr = call_method(dev, DEV_DrawPrimitive,
                              {D3DPT_TRIANGLELIST, D3DVT_TLVERTEX, verts, 0x08000001u, 0});
    CHECK_EQ(hr, D3D_OK_); // the method succeeds; the draw is dropped
    CHECK_EQ(g_draws.size(), 0);

    // An index naming a vertex that does not exist is refused too.
    for (uint32_t v = 0; v < 4; ++v)
        gm_zero(verts + v * 32, 32);
    uint32_t idx = sc(0xa00);
    wr16(idx + 0, 0);
    wr16(idx + 2, 1);
    wr16(idx + 4, 99);
    hr = call_method(dev, DEV_DrawIndexedPrimitive,
                     {D3DPT_TRIANGLELIST, D3DVT_TLVERTEX, verts, 4, idx, 3, 0});
    CHECK_EQ(hr, D3D_OK_);
    CHECK_EQ(g_draws.size(), 0);

    // A valid draw still goes through, so the guard is not simply blocking.
    wr16(idx + 4, 2);
    hr = call_method(dev, DEV_DrawIndexedPrimitive,
                     {D3DPT_TRIANGLELIST, D3DVT_TLVERTEX, verts, 4, idx, 3, 0});
    CHECK_EQ(hr, D3D_OK_);
    CHECK_EQ(g_draws.size(), 1);

    // A surface whose dimensions would overflow the arena is refused.
    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));
    uint32_t desc = sc(0x100);
    gm_zero(desc, SDK_DDSURFACEDESC);
    wr32(desc + DDSD_OFF_dwSize, SDK_DDSURFACEDESC);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_OFFSCREENPLAIN);
    wr32(desc + DDSD_OFF_dwWidth, 0x40000000u);
    wr32(desc + DDSD_OFF_dwHeight, 0x40000000u);
    wr32(sc(4), 0xdeadbeef);
    hr = call_method(dd, DD_CreateSurface, {desc, sc(4), 0});
    CHECK(hr != DD_OK);
    CHECK_EQ(rd32(sc(4)), 0);
}

// IUnknown identity is stable, DirectDraw and Direct3D query each other, and a
// released parent stays alive while a dependent interface is held.
static void test_identity_and_parent() {
    cpu_reset();
    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));

    static const uint8_t iid_unknown[16] = {0, 0, 0, 0, 0, 0, 0, 0, 0xc0, 0, 0, 0, 0, 0, 0, 0x46};
    uint32_t iid = sc(0x40);
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, iid_unknown[i]);
    CHECK_EQ(call_method(dd, DD_QueryInterface, {iid, sc(0x60)}), S_OK);
    uint32_t unk1 = rd32(sc(0x60));
    CHECK(unk1 != 0);

    // Create a lower-numbered view, which used to displace the identity.
    uint8_t dd2[16] = {0xE0, 0xF3, 0xA6, 0xB3, 0x43, 0x2B, 0xCF, 0x11,
                       0xA2, 0xDE, 0x00, 0xAA, 0x00, 0xB9, 0x33, 0x56};
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, dd2[i]);
    call_method(dd, DD_QueryInterface, {iid, sc(0x64)});
    uint32_t ddraw2 = rd32(sc(0x64));
    CHECK(ddraw2 != 0);

    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, iid_unknown[i]);
    CHECK_EQ(call_method(dd, DD_QueryInterface, {iid, sc(0x68)}), S_OK);
    CHECK_EQ(rd32(sc(0x68)), unk1);
    // The same object reached through another interface reports the same
    // IUnknown, which is how COM defines object identity.
    CHECK_EQ(call_method(ddraw2, DD_QueryInterface, {iid, sc(0x6c)}), S_OK);
    CHECK_EQ(rd32(sc(0x6c)), unk1);

    // DirectDraw to Direct3D, and back again.
    uint8_t d3d2[16] = {0xC1, 0x1E, 0xAE, 0x6A, 0x2A, 0x66, 0xD0, 0x11,
                        0x88, 0x9D, 0x00, 0xAA, 0x00, 0xBB, 0xB7, 0x6A};
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, d3d2[i]);
    CHECK_EQ(call_method(dd, DD_QueryInterface, {iid, sc(0x70)}), S_OK);
    uint32_t d3d = rd32(sc(0x70));
    CHECK(d3d != 0);
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, dd2[i]);
    CHECK_EQ(call_method(d3d, 0 /* QueryInterface */, {iid, sc(0x74)}), S_OK);
    CHECK_EQ(rd32(sc(0x74)), ddraw2);
    // Direct3D is an interface on the DirectDraw object, so it reports the
    // same controlling IUnknown: one object, one identity, one lifetime.
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, iid_unknown[i]);
    CHECK_EQ(call_method(d3d, 0 /* QueryInterface */, {iid, sc(0x78)}), S_OK);
    CHECK_EQ(rd32(sc(0x78)), unk1);

    // Releasing every DirectDraw reference while Direct3D is still held must
    // not destroy the object underneath it.
    // Eight references stand on the object now: DirectDrawCreate, four
    // IUnknown queries, one IDirectDraw2 query, the IDirect3D2 query and the
    // reverse IDirectDraw2 query. Direct3D is a view of this same object, so
    // they all count against one number. Releasing seven leaves the one the
    // IDirect3D2 pointer holds, which is the case under test.
    CHECK(com_this(dd) != nullptr);
    call_method(dd, DD_Release, {});     // the create reference
    call_method(ddraw2, DD_Release, {}); // the IDirectDraw2 query
    call_method(dd, DD_Release, {});     // the three IUnknown queries
    call_method(dd, DD_Release, {});
    call_method(ddraw2, DD_Release, {});
    call_method(dd, DD_Release, {}); // the reverse query through D3D
    call_method(dd, DD_Release, {}); // the IUnknown query through D3D
    CHECK(com_this(d3d) != nullptr);
    CHECK(com_this(dd) != nullptr);   // held alive by the Direct3D object
    call_method(d3d, DD_Release, {}); // the IDirect3D2 query
    CHECK(com_this(d3d) == nullptr);
    CHECK(com_this(dd) == nullptr); // and now the parent goes too
}

// A duplicated sound buffer keeps the samples alive after the original goes.
static void test_dsound_duplicate_lifetime() {
    cpu_reset();
    g_plays.clear();
    uint32_t create = tramp("DSOUND.dll", "ord1");
    call_shim(create, {0, sc(0), 0});
    uint32_t ds = rd32(sc(0));

    uint32_t wfx = sc(0x100);
    wr16(wfx + 0, 1);
    wr16(wfx + 2, 1);
    wr32(wfx + 4, 22050);
    wr32(wfx + 8, 22050);
    wr16(wfx + 12, 1);
    wr16(wfx + 14, 8);
    wr16(wfx + 16, 0);

    uint32_t bd = sc(0x200);
    gm_zero(bd, SDK_DSBUFFERDESC);
    wr32(bd + 0, SDK_DSBUFFERDESC);
    wr32(bd + 4, DSBCAPS_STATIC);
    wr32(bd + 8, 256);
    wr32(bd + 16, wfx);
    CHECK_EQ(call_method(ds, DS_CreateSoundBuffer, {bd, sc(4), 0}), DS_OK);
    uint32_t orig = rd32(sc(4));
    CHECK(orig != 0);

    // Fill it, so the duplicate has something identifiable to play.
    call_method(orig, B_Lock, {0, 256, sc(0x300), sc(0x304), sc(0x308), sc(0x30c), 0});
    uint32_t p1 = rd32(sc(0x300));
    for (uint32_t i = 0; i < 256; ++i)
        wr8(p1 + i, (uint8_t)(0xC0 ^ i));
    call_method(orig, B_Unlock, {p1, 256, 0, 0});

    CHECK_EQ(call_method(ds, 5 /* DuplicateSoundBuffer */, {orig, sc(8)}), DS_OK);
    uint32_t dup = rd32(sc(8));
    CHECK(dup != 0);

    HeapStats before = heap_stats();
    call_method(orig, S_Release, {});
    // Releasing the original must not hand the samples back to the heap while
    // the duplicate still points at them.
    CHECK_EQ(heap_stats().used_blocks, before.used_blocks - 1); // the view only

    g_plays.clear();
    CHECK_EQ(call_method(dup, B_Play, {0, 0, 0}), DS_OK);
    CHECK_EQ(g_plays.size(), 1);
    if (!g_plays.empty()) {
        bool ok = true;
        for (uint32_t i = 0; i < 256; ++i)
            if (g_plays[0].pcm[i] != (uint8_t)(0xC0 ^ i)) {
                ok = false;
                break;
            }
        CHECK(ok);
    }
    call_method(dup, B_Stop, {});
    call_method(dup, S_Release, {});
}

// SetSurfaceDesc validates the whole replacement span and does not free
// memory the guest owns.
static void test_setsurfacedesc_ownership() {
    cpu_reset();
    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));
    call_method(dd, DD_SetDisplayMode, {640, 480, 8});

    uint32_t desc = sc(0x100);
    gm_zero(desc, SDK_DDSURFACEDESC);
    wr32(desc + DDSD_OFF_dwSize, SDK_DDSURFACEDESC);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_OFFSCREENPLAIN | DDSCAPS_SYSTEMMEMORY);
    wr32(desc + DDSD_OFF_dwWidth, 32);
    wr32(desc + DDSD_OFF_dwHeight, 32);
    call_method(dd, DD_CreateSurface, {desc, sc(4), 0});
    uint32_t s1 = rd32(sc(4));
    CHECK(s1 != 0);

    // IDirectDrawSurface3 is where SetSurfaceDesc lives.
    uint32_t iid = sc(0x40);
    uint8_t s3[16] = {0x00, 0x4E, 0x04, 0xDA, 0xB2, 0x69, 0xD0, 0x11,
                      0xA1, 0xD5, 0x00, 0xAA, 0x00, 0xB8, 0xDF, 0xBB};
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, s3[i]);
    CHECK_EQ(call_method(s1, S_QueryInterface, {iid, sc(0x60)}), S_OK);
    uint32_t v3 = rd32(sc(0x60));
    CHECK(v3 != 0);

    // A pitch that cannot hold a row is refused.
    uint32_t sd = sc(0x200);
    gm_zero(sd, SDK_DDSURFACEDESC);
    wr32(sd + DDSD_OFF_dwSize, SDK_DDSURFACEDESC);
    wr32(sd + DDSD_OFF_dwFlags, DDSD_LPSURFACE | DDSD_PITCH);
    wr32(sd + DDSD_OFF_lpSurface, g_scratch);
    wr32(sd + DDSD_OFF_lPitch, 4); // a 32-pixel row needs 32
    CHECK_EQ(call_method(v3, 39 /* SetSurfaceDesc */, {sd, 0}), DDERR_INVALIDPARAMS);

    // A pointer near the end of the arena whose span would run off it, too.
    wr32(sd + DDSD_OFF_lPitch, 64);
    wr32(sd + DDSD_OFF_lpSurface, GUEST_SIZE - 64);
    CHECK_EQ(call_method(v3, 39, {sd, 0}), DDERR_INVALIDPARAMS);

    // A valid guest-owned buffer is accepted, and releasing the surface must
    // not free it: the heap block count stays put.
    uint32_t owned = heap_alloc(64 * 32, true, 16);
    CHECK(owned != 0);
    wr32(sd + DDSD_OFF_lpSurface, owned);
    CHECK_EQ(call_method(v3, 39, {sd, 0}), DD_OK);
    CHECK_EQ(heap_size(owned), 64u * 32u);
    call_method(v3, S_Release, {});
    call_method(s1, S_Release, {});
    // Still a live block of exactly the size we asked for: nothing freed it.
    CHECK_EQ(heap_size(owned), 64u * 32u);
    heap_free(owned);
}

// Every way the record can fail to match the recovered contract is a refused
// handle, not a silent one.
static void test_qmixer_failure() {
    cpu_reset();
    uint32_t init = tramp("QMIXER.dll", "QSWaveMixInitEx");
    uint32_t initdata = sc(0x100);
    gm_zero(initdata, 0x40);
    wr32(initdata + 0, 0x40);
    wr32(initdata + 8, 22050);
    uint32_t hmix = call_shim(init, {initdata});
    CHECK(hmix != 0);

    uint32_t open_wave = tramp("QMIXER.dll", "QSWaveMixOpenWaveEx");
    uint32_t owd = sc(0x300);

    // An all-zero record names no format at all.
    gm_zero(owd, 20);
    CHECK_EQ(call_shim(open_wave, {hmix, owd, 8}), 0u);

    // A well-formed record, used as the baseline for the negative cases.
    uint32_t wfx = sc(0x200);
    gm_zero(wfx, SDK_WAVEFORMATEX);
    wr16(wfx + 0x00, 1);
    wr16(wfx + 0x02, 1);
    wr32(wfx + 0x04, 11025);
    wr32(wfx + 0x08, 11025);
    wr16(wfx + 0x0c, 1);
    wr16(wfx + 0x0e, 8);
    uint32_t pcm = sc(0x400);
    for (uint32_t i = 0; i < 32; ++i)
        wr8(pcm + i, (uint8_t)i);
    wr32(owd + 0x00, wfx);
    wr32(owd + 0x04, pcm);
    wr32(owd + 0x08, 32);
    uint32_t good = call_shim(open_wave, {hmix, owd, 8});
    CHECK(good != 0);

    // A compressed format is refused rather than played as if it were PCM.
    wr16(wfx + 0x00, 2); // WAVE_FORMAT_ADPCM
    CHECK_EQ(call_shim(open_wave, {hmix, owd, 8}), 0u);
    wr16(wfx + 0x00, 1);

    // A sample count that runs off the end of the arena.
    wr32(owd + 0x04, GUEST_SIZE - 16);
    wr32(owd + 0x08, 4096);
    CHECK_EQ(call_shim(open_wave, {hmix, owd, 8}), 0u);
    wr32(owd + 0x04, pcm);
    wr32(owd + 0x08, 32);

    // A format the mixer cannot play.
    wr16(wfx + 0x0e, 24);
    CHECK_EQ(call_shim(open_wave, {hmix, owd, 8}), 0u);
    wr16(wfx + 0x0e, 8);

    // The streaming form, with no callback to obtain samples from: field 1 is
    // a length rather than a pointer, so there is nothing to play and opening
    // the wave has to fail rather than hand back a handle that is silent.
    // Streaming with a real callback is covered by the QMixer streaming test.
    wr32(owd + 0x04, 0x7800); // a length, not a pointer
    wr32(owd + 0x08, 0);      // no callback
    wr32(owd + 0x0c, 0);
    CHECK_EQ(call_shim(open_wave, {hmix, owd, 0x11}), 0u);

    // And the good record still opens, so the guards are not blanket refusals.
    wr32(owd + 0x04, pcm);
    wr32(owd + 0x08, 32);
    wr32(owd + 0x0c, 0);
    CHECK(call_shim(open_wave, {hmix, owd, 8}) != 0);

    uint32_t close = tramp("QMIXER.dll", "QSWaveMixCloseSession");
    call_shim(close, {hmix});
}

// dx_reset must leave no cached guest address behind: after it, everything
// works against the fresh arena.
static void test_reset() {
    cpu_reset();
    call_shim(tramp("fmod.dll", "_FSOUND_Init@12"), {44100, 4, 0});
    uint32_t sample = call_shim(tramp("fmod.dll", "_FSOUND_Sample_LoadWav@12"),
                                {0xffffffffu, build_test_wave(), 0x8000});
    CHECK(sample != 0);
    CHECK(call_shim(tramp("fmod.dll", "_FSOUND_PlaySoundAttrib@20"),
                    {0xffffffffu, sample, 22050, 255, 128}) < 4);
    g_midi_available = true;
    CHECK_EQ(call_shim(tramp("Soundlib.dll", "CreateMidi"), {}), 1u);
    uint32_t closed = g_midi_closes;
    mem_init();
    dx_reset();
    g_scratch = heap_alloc(0x4000, true, 16);
    CHECK(g_scratch != 0);
    CHECK_EQ(com_live_count(), 0u);
    CHECK_EQ(g_midi_closes, closed + 1);
    g_midi_available = false;
    cpu_reset();
    CHECK_EQ(call_shim(tramp("fmod.dll", "_FSOUND_Sample_GetDefaults@20"), {sample, 0, 0, 0, 0}),
             0u);
    uint32_t driver = call_shim(tramp("fmod.dll", "_FSOUND_GetDriverName@4"), {0});
    CHECK(driver != 0);
    if (driver)
        CHECK(gm_str(driver) == "recomp mixer");

    cpu_reset();
    g_presents.clear();
    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    uint32_t hr = call_shim(create, {0, sc(0), 0});
    CHECK_EQ(hr, DD_OK);
    uint32_t dd = rd32(sc(0));
    CHECK(dd != 0);
    // The vtable was rebuilt in the new arena and its slots still dispatch.
    CHECK(imports_is_trampoline(rd32(rd32(dd + COM_OFF_vtbl))));
    CHECK_EQ(call_method(dd, DD_SetDisplayMode, {640, 480, 8}), DD_OK);

    uint32_t desc = sc(0x100);
    gm_zero(desc, SDK_DDSURFACEDESC);
    wr32(desc + DDSD_OFF_dwSize, SDK_DDSURFACEDESC);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_PRIMARYSURFACE);
    CHECK_EQ(call_method(dd, DD_CreateSurface, {desc, sc(4), 0}), DD_OK);
    uint32_t primary = rd32(sc(4));
    CHECK(primary != 0);
    uint32_t ld = sc(0x200);
    gm_zero(ld, SDK_DDSURFACEDESC);
    wr32(ld + DDSD_OFF_dwSize, SDK_DDSURFACEDESC);
    CHECK_EQ(call_method(primary, S_Lock, {0, ld, DDLOCK_WAIT, 0}), DD_OK);
    CHECK(rd32(ld + DDSD_OFF_lpSurface) != 0);
    CHECK_EQ(call_method(primary, S_Unlock, {0}), DD_OK);
    CHECK_EQ(g_presents.size(), 1);
}

// A rectangle touching the bottom edge with a non-zero left edge is legal: the
// locked span is (h-1) whole rows plus the last row's width, not h whole rows.
static void test_partial_lock_bottom_edge() {
    cpu_reset();
    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));
    call_method(dd, DD_SetDisplayMode, {640, 480, 8});

    uint32_t desc = sc(0x100);
    gm_zero(desc, SDK_DDSURFACEDESC);
    wr32(desc + DDSD_OFF_dwSize, SDK_DDSURFACEDESC);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_OFFSCREENPLAIN);
    wr32(desc + DDSD_OFF_dwWidth, 64);
    wr32(desc + DDSD_OFF_dwHeight, 64);
    CHECK_EQ(call_method(dd, DD_CreateSurface, {desc, sc(4), 0}), DD_OK);
    uint32_t surf = rd32(sc(4));
    CHECK(surf != 0);

    // The exact case the old bound rejected: bottom-right corner, left > 0.
    uint32_t rect = sc(0x200);
    wr32(rect + 0, 32);  // left
    wr32(rect + 4, 60);  // top
    wr32(rect + 8, 64);  // right
    wr32(rect + 12, 64); // bottom
    uint32_t ld = sc(0x300);
    gm_zero(ld, SDK_DDSURFACEDESC);
    wr32(ld + DDSD_OFF_dwSize, SDK_DDSURFACEDESC);
    uint32_t hr = call_method(surf, S_Lock, {rect, ld, DDLOCK_WAIT, 0});
    CHECK_EQ(hr, DD_OK);
    CHECK_EQ(rd32(ld + DDSD_OFF_dwWidth), 32u);
    CHECK_EQ(rd32(ld + DDSD_OFF_dwHeight), 4u);
    uint32_t p = rd32(ld + DDSD_OFF_lpSurface);
    CHECK(p != 0);
    // The last row of the region is writable, which is what the bound governs.
    uint32_t pitch = rd32(ld + DDSD_OFF_lPitch);
    wr8(p + 3 * pitch + 31, 0xAB);
    CHECK_EQ(rd8(p + 3 * pitch + 31), 0xAB);
    CHECK_EQ(call_method(surf, S_Unlock, {0}), DD_OK);

    // The very last pixel of the surface, as a 1x1 rectangle.
    wr32(rect + 0, 63);
    wr32(rect + 4, 63);
    wr32(rect + 8, 64);
    wr32(rect + 12, 64);
    gm_zero(ld, SDK_DDSURFACEDESC);
    wr32(ld + DDSD_OFF_dwSize, SDK_DDSURFACEDESC);
    CHECK_EQ(call_method(surf, S_Lock, {rect, ld, DDLOCK_WAIT, 0}), DD_OK);
    CHECK_EQ(call_method(surf, S_Unlock, {0}), DD_OK);

    // A rectangle past the edge is still refused.
    wr32(rect + 0, 32);
    wr32(rect + 4, 60);
    wr32(rect + 8, 65);
    wr32(rect + 12, 64);
    gm_zero(ld, SDK_DDSURFACEDESC);
    wr32(ld + DDSD_OFF_dwSize, SDK_DDSURFACEDESC);
    CHECK_EQ(call_method(surf, S_Lock, {rect, ld, DDLOCK_WAIT, 0}), DDERR_INVALIDRECT);
}

// Flip moves pixel ownership with the pixels, so a guest-owned buffer that has
// been flipped to the front is still not the shim's to free.
static void test_flip_ownership() {
    cpu_reset();
    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));
    call_method(dd, DD_SetDisplayMode, {640, 480, 8});

    uint32_t desc = sc(0x100);
    gm_zero(desc, SDK_DDSURFACEDESC);
    wr32(desc + DDSD_OFF_dwSize, SDK_DDSURFACEDESC);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_BACKBUFFERCOUNT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_PRIMARYSURFACE | DDSCAPS_FLIP | DDSCAPS_COMPLEX);
    wr32(desc + DDSD_OFF_dwBackBufferCount, 1);
    CHECK_EQ(call_method(dd, DD_CreateSurface, {desc, sc(4), 0}), DD_OK);
    uint32_t primary = rd32(sc(4));
    uint32_t caps = sc(0x180);
    wr32(caps, DDSCAPS_BACKBUFFER);
    CHECK_EQ(call_method(primary, S_GetAttachedSurface, {caps, sc(8)}), DD_OK);
    uint32_t back = rd32(sc(8));
    CHECK(back != 0);

    // Point the back buffer at memory the guest owns.
    uint32_t iid = sc(0x40);
    uint8_t s3[16] = {0x00, 0x4E, 0x04, 0xDA, 0xB2, 0x69, 0xD0, 0x11,
                      0xA1, 0xD5, 0x00, 0xAA, 0x00, 0xB8, 0xDF, 0xBB};
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, s3[i]);
    CHECK_EQ(call_method(back, S_QueryInterface, {iid, sc(0x60)}), S_OK);
    uint32_t back3 = rd32(sc(0x60));
    CHECK(back3 != 0);

    uint32_t owned = heap_alloc(704 * 480, true, 16);
    CHECK(owned != 0);
    uint32_t sd = sc(0x200);
    gm_zero(sd, SDK_DDSURFACEDESC);
    wr32(sd + DDSD_OFF_dwSize, SDK_DDSURFACEDESC);
    wr32(sd + DDSD_OFF_dwFlags, DDSD_LPSURFACE | DDSD_PITCH);
    wr32(sd + DDSD_OFF_lpSurface, owned);
    wr32(sd + DDSD_OFF_lPitch, 704);
    CHECK_EQ(call_method(back3, 39 /* SetSurfaceDesc */, {sd, 0}), DD_OK);

    // Flipping moves that buffer to the front. Releasing everything must not
    // free it: it belongs to the guest, not to the shim.
    CHECK_EQ(call_method(primary, S_Flip, {0, DDFLIP_WAIT}), DD_OK);
    call_method(back3, S_Release, {});
    call_method(back, S_Release, {});
    call_method(primary, S_Release, {});
    CHECK_EQ(heap_size(owned), 704u * 480u);
    heap_free(owned);
}

// Out-parameters that cannot be written are refused rather than written to.
static void test_out_pointer_guards() {
    cpu_reset();
    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));

    // A dword pointer with only two bytes of arena left after it.
    const uint32_t edge = GUEST_SIZE - 2;

    uint32_t entries = sc(0x400);
    gm_zero(entries, 256 * 4);
    CHECK_EQ(
        call_method(dd, DD_CreatePalette, {DDPCAPS_8BIT | DDPCAPS_INITIALIZE, entries, sc(12), 0}),
        DD_OK);
    uint32_t pal = rd32(sc(12));
    CHECK(pal != 0);
    CHECK_EQ(call_method(pal, 3 /* GetCaps */, {edge}), DDERR_INVALIDPARAMS);
    CHECK_EQ(call_method(pal, 3, {0}), DDERR_INVALIDPARAMS);

    CHECK_EQ(call_method(dd, 4 /* CreateClipper */, {0, sc(16), 0}), DD_OK);
    uint32_t clip = rd32(sc(16));
    CHECK(clip != 0);
    CHECK_EQ(call_method(clip, 4 /* GetHWnd */, {edge}), DDERR_INVALIDPARAMS);
    CHECK_EQ(call_method(clip, 6 /* IsClipListChanged */, {edge}), DDERR_INVALIDPARAMS);
    CHECK_EQ(call_method(clip, 6, {0}), DDERR_INVALIDPARAMS);
    // A writable pointer still works, so these are guards and not refusals.
    CHECK_EQ(call_method(clip, 6, {sc(0x20)}), DD_OK);
    CHECK_EQ(rd32(sc(0x20)), 0u);

    // A surface's DDSCAPS2 output needs all sixteen bytes, not just four.
    call_method(dd, DD_SetDisplayMode, {640, 480, 8});
    uint32_t desc = sc(0x100);
    gm_zero(desc, SDK_DDSURFACEDESC2);
    wr32(desc + DDSD_OFF_dwSize, SDK_DDSURFACEDESC2);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_OFFSCREENPLAIN);
    wr32(desc + DDSD_OFF_dwWidth, 8);
    wr32(desc + DDSD_OFF_dwHeight, 8);
    uint32_t iid = sc(0x40);
    uint8_t dd4[16] = {0x9A, 0x50, 0x59, 0x9C, 0xBD, 0x39, 0xD1, 0x11,
                       0x8C, 0x4A, 0x00, 0xC0, 0x4F, 0xD9, 0x30, 0xC5};
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, dd4[i]);
    CHECK_EQ(call_method(dd, DD_QueryInterface, {iid, sc(0x60)}), S_OK);
    uint32_t dd4p = rd32(sc(0x60));
    CHECK_EQ(call_method(dd4p, DD_CreateSurface, {desc, sc(4), 0}), DD_OK);
    uint32_t s4 = rd32(sc(4));
    CHECK(s4 != 0);
    CHECK_EQ(call_method(s4, 14 /* GetCaps */, {GUEST_SIZE - 8}), DDERR_INVALIDPARAMS);
    CHECK_EQ(call_method(s4, 14, {sc(0x30)}), DD_OK);
    CHECK_EQ(rd32(sc(0x30)), DDSCAPS_OFFSCREENPLAIN);
}

// ---------------------------------------------------------------------------
// IDirectDrawColorControl on a surface: QueryInterface reaches it, the
// defaults are the SDK's, dwSize is validated, and SetColorControls writes
// only the fields its flags name.
// ---------------------------------------------------------------------------
static void test_color_control() {
    cpu_reset();
    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));
    CHECK(dd != 0);
    call_method(dd, DD_SetCooperativeLevel, {0x1234, DDSCL_EXCLUSIVE | DDSCL_FULLSCREEN});
    call_method(dd, DD_SetDisplayMode, {640, 480, 8});

    uint32_t desc = sc(0x200);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_PRIMARYSURFACE);
    CHECK_EQ(call_method(dd, DD_CreateSurface, {desc, sc(0x10), 0}), DD_OK);
    uint32_t surf = rd32(sc(0x10));
    CHECK(surf != 0);

    // QueryInterface reaches it from the surface.
    static const uint8_t iid_cc[16] = {0xE0, 0x0E, 0x9F, 0x4B, 0x7E, 0x0D, 0xD0, 0x11,
                                       0x9B, 0x06, 0x00, 0xA0, 0xC9, 0x03, 0xA3, 0xB8};
    uint32_t iid = sc(0x40);
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, iid_cc[i]);
    wr32(sc(0x60), 0xdeadbeef);
    CHECK_EQ(call_method(surf, S_QueryInterface, {iid, sc(0x60)}), S_OK);
    uint32_t cc = rd32(sc(0x60));
    CHECK(cc != 0 && cc != 0xdeadbeef);
    // A separate view of one object: a different vtable, the same identity.
    CHECK(rd32(cc + COM_OFF_vtbl) != rd32(surf + COM_OFF_vtbl));
    CHECK_EQ(rd32(cc + COM_OFF_obj), rd32(surf + COM_OFF_obj));

    // The defaults the SDK documents, and the structure really is 40 bytes:
    // a canary one dword past the end must survive.
    uint32_t p = sc(0x100);
    gm_zero(p, 64);
    wr32(p + DDCC_OFF_dwSize, DDCOLORCONTROL_SIZE);
    wr32(p + DDCOLORCONTROL_SIZE, 0xA5A5A5A5u);
    CHECK_EQ(call_method(cc, 3 /* GetColorControls */, {p}), DD_OK);
    CHECK_EQ(rd32(p + DDCC_OFF_dwFlags), DDCOLOR_ALL);
    CHECK_EQ((int32_t)rd32(p + DDCC_OFF_lBrightness), 750);
    CHECK_EQ((int32_t)rd32(p + DDCC_OFF_lContrast), 10000);
    CHECK_EQ((int32_t)rd32(p + DDCC_OFF_lHue), 0);
    CHECK_EQ((int32_t)rd32(p + DDCC_OFF_lSaturation), 10000);
    CHECK_EQ((int32_t)rd32(p + DDCC_OFF_lSharpness), 5);
    CHECK_EQ((int32_t)rd32(p + DDCC_OFF_lGamma), 1);
    CHECK_EQ((int32_t)rd32(p + DDCC_OFF_lColorEnable), 1);
    CHECK_EQ(rd32(p + DDCOLORCONTROL_SIZE), 0xA5A5A5A5u);

    // A wrong dwSize is refused rather than filled in.
    wr32(p + DDCC_OFF_dwSize, 52);
    CHECK_EQ(call_method(cc, 3 /* GetColorControls */, {p}), DDERR_INVALIDPARAMS);
    CHECK_EQ(call_method(cc, 4 /* SetColorControls */, {p}), DDERR_INVALIDPARAMS);
    CHECK_EQ(call_method(cc, 3 /* GetColorControls */, {0}), DDERR_INVALIDPARAMS);

    // Set only brightness: the other fields keep their values even though the
    // structure carries different numbers in them.
    gm_zero(p, 64);
    wr32(p + DDCC_OFF_dwSize, DDCOLORCONTROL_SIZE);
    wr32(p + DDCC_OFF_dwFlags, DDCOLOR_BRIGHTNESS);
    wr32(p + DDCC_OFF_lBrightness, (uint32_t)(int32_t)1234);
    wr32(p + DDCC_OFF_lContrast, (uint32_t)(int32_t)7777);
    CHECK_EQ(call_method(cc, 4 /* SetColorControls */, {p}), DD_OK);
    gm_zero(p, 64);
    wr32(p + DDCC_OFF_dwSize, DDCOLORCONTROL_SIZE);
    CHECK_EQ(call_method(cc, 3 /* GetColorControls */, {p}), DD_OK);
    CHECK_EQ((int32_t)rd32(p + DDCC_OFF_lBrightness), 1234);
    CHECK_EQ((int32_t)rd32(p + DDCC_OFF_lContrast), 10000);

    // An undefined flag is rejected and changes nothing.
    wr32(p + DDCC_OFF_dwSize, DDCOLORCONTROL_SIZE);
    wr32(p + DDCC_OFF_dwFlags, 0x8000u);
    wr32(p + DDCC_OFF_lBrightness, (uint32_t)(int32_t)99);
    CHECK_EQ(call_method(cc, 4 /* SetColorControls */, {p}), DDERR_INVALIDPARAMS);
    gm_zero(p, 64);
    wr32(p + DDCC_OFF_dwSize, DDCOLORCONTROL_SIZE);
    CHECK_EQ(call_method(cc, 3 /* GetColorControls */, {p}), DD_OK);
    CHECK_EQ((int32_t)rd32(p + DDCC_OFF_lBrightness), 1234);

    // Two surfaces have independent controls.
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_OFFSCREENPLAIN);
    wr32(desc + DDSD_OFF_dwWidth, 32);
    wr32(desc + DDSD_OFF_dwHeight, 32);
    CHECK_EQ(call_method(dd, DD_CreateSurface, {desc, sc(0x14), 0}), DD_OK);
    uint32_t other = rd32(sc(0x14));
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, iid_cc[i]);
    CHECK_EQ(call_method(other, S_QueryInterface, {iid, sc(0x64)}), S_OK);
    uint32_t cc2 = rd32(sc(0x64));
    CHECK(cc2 != cc);
    gm_zero(p, 64);
    wr32(p + DDCC_OFF_dwSize, DDCOLORCONTROL_SIZE);
    CHECK_EQ(call_method(cc2, 3 /* GetColorControls */, {p}), DD_OK);
    CHECK_EQ((int32_t)rd32(p + DDCC_OFF_lBrightness), 750);
}

// ---------------------------------------------------------------------------
// A FourCC surface is refused as a pixel format, not as a surface type, and
// nothing in the advertised set contradicts that: GetFourCCCodes reports none
// and EnumTextureFormats offers none.
// ---------------------------------------------------------------------------
static uint32_t g_fourcc_formats = 0;
static uint32_t g_fourcc_texfmt_fourcc = 0;

static void test_fourcc_is_a_pixel_format_error() {
    cpu_reset();
    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));
    call_method(dd, DD_SetCooperativeLevel, {0x1234, DDSCL_EXCLUSIVE | DDSCL_FULLSCREEN});
    call_method(dd, DD_SetDisplayMode, {640, 480, 16});

    // 'PVRC', the format the game's PowerVR path asks for.
    uint32_t desc = sc(0x200);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT | DDSD_PIXELFORMAT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_TEXTURE);
    wr32(desc + DDSD_OFF_dwWidth, 64);
    wr32(desc + DDSD_OFF_dwHeight, 64);
    uint32_t pf = desc + DDSD_OFF_ddpfPixelFormat;
    wr32(pf + DDPF_OFF_dwSize, DDPF_SIZE);
    wr32(pf + DDPF_OFF_dwFlags, DDPF_FOURCC);
    wr32(pf + DDPF_OFF_dwFourCC, 0x43525650u); // 'PVRC'
    wr32(sc(0x10), 0xdeadbeef);
    CHECK_EQ(call_method(dd, DD_CreateSurface, {desc, sc(0x10), 0}), DDERR_INVALIDPIXELFORMAT);
    CHECK_EQ(rd32(sc(0x10)), 0);

    // The refusal is consistent with what the driver advertises.
    wr32(sc(0x20), 0xdeadbeef);
    CHECK_EQ(call_method(dd, DD_GetFourCCCodes, {sc(0x20), 0}), DD_OK);
    CHECK_EQ(rd32(sc(0x20)), 0u);

    uint32_t dev = make_d3d_device();
    CHECK(dev != 0);
    g_fourcc_formats = 0;
    g_fourcc_texfmt_fourcc = 0;
    static uint32_t cb = imports_alloc_trampoline(
        "TEST", "EnumTextureFormatsCallback",
        [](X86 *c) {
            uint32_t d = arg(c, 0);
            uint32_t f = rd32(d + DDSD_OFF_ddpfPixelFormat + DDPF_OFF_dwFlags);
            ++g_fourcc_formats;
            if (f & DDPF_FOURCC)
                ++g_fourcc_texfmt_fourcc;
            set_eax(c, DDENUMRET_OK);
        },
        2);
    CHECK_EQ(call_method(dev, DEV_EnumTextureFormats, {cb, 0}), D3D_OK_);
    CHECK(g_fourcc_formats > 0);
    CHECK_EQ(g_fourcc_texfmt_fourcc, 0u); // not one of them was FourCC
}

// ---------------------------------------------------------------------------
// An 8-bit palettised texture: create it, attach a palette, lock and write
// indices, unlock, and take the handle. The renderer must receive the indices
// AND the palette, and must be given them again when either changes. A
// texture whose palette never arrives draws black, which is what a missing
// palette looks like on screen.
// ---------------------------------------------------------------------------
// A texture is identified to a mod by its content, not by its handle.
//
// The handle is a slot number DirectDraw hands out and reuses, so two
// different textures wear the same one over a run and the same texture wears
// several. An override keyed on it would follow the slot rather than the
// picture. So the shim hashes the pixels, the palette and the dimensions at
// every upload, and that is what a provider is asked about.
static void test_texture_content_hash() {
    cpu_reset();
    g_uploads.clear();
    g_tex_hashes.clear();
    g_tex_override.clear();
    g_tex_hook_on = true;

    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));
    call_method(dd, DD_SetCooperativeLevel, {0x1234, DDSCL_EXCLUSIVE | DDSCL_FULLSCREEN});
    call_method(dd, DD_SetDisplayMode, {640, 480, 16});

    uint32_t desc = sc(0x200);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT | DDSD_PIXELFORMAT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_TEXTURE);
    wr32(desc + DDSD_OFF_dwWidth, 4);
    wr32(desc + DDSD_OFF_dwHeight, 4);
    uint32_t pf = desc + DDSD_OFF_ddpfPixelFormat;
    wr32(pf + DDPF_OFF_dwSize, DDPF_SIZE);
    wr32(pf + DDPF_OFF_dwFlags, DDPF_RGB | DDPF_PALETTEINDEXED8);
    wr32(pf + DDPF_OFF_dwRGBBitCount, 8);
    CHECK_EQ(call_method(dd, DD_CreateSurface, {desc, sc(0x10), 0}), DD_OK);
    uint32_t tex_surf = rd32(sc(0x10));
    CHECK(tex_surf != 0);

    uint32_t entries = sc(0x400);
    gm_zero(entries, 256 * 4);
    for (uint32_t i = 0; i < 4; ++i)
        wr32(entries + i * 4, i * 0x00404040u);
    CHECK_EQ(
        call_method(dd, DD_CreatePalette, {DDPCAPS_8BIT | DDPCAPS_ALLOW256, entries, sc(0x18), 0}),
        DD_OK);
    CHECK_EQ(call_method(tex_surf, S_SetPalette, {rd32(sc(0x18))}), DD_OK);

    // Writes the given indices through a real Lock pointer.
    auto write_pixels = [&](int seed) {
        uint32_t ld = sc(0x300);
        gm_zero(ld, DDSD_SIZE);
        wr32(ld + DDSD_OFF_dwSize, DDSD_SIZE);
        CHECK_EQ(call_method(tex_surf, S_Lock, {0, ld, DDLOCK_WAIT, 0}), DD_OK);
        uint32_t bits = rd32(ld + DDSD_OFF_lpSurface);
        uint32_t pitch = rd32(ld + DDSD_OFF_lPitch);
        for (uint32_t y = 0; y < 4; ++y)
            for (uint32_t x = 0; x < 4; ++x)
                wr8(bits + y * pitch + x, (uint8_t)((x + y + seed) % 4));
        CHECK_EQ(call_method(tex_surf, S_Unlock, {0}), DD_OK);
    };

    write_pixels(0);
    uint32_t dev = make_d3d_device();
    CHECK(dev != 0);
    uint8_t tex2[16] = {0x02, 0x15, 0x28, 0x93, 0xF8, 0x8C, 0xD0, 0x11,
                        0x89, 0xAB, 0x00, 0xA0, 0xC9, 0x05, 0x41, 0x29};
    uint32_t iid = sc(0x40);
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, tex2[i]);
    CHECK_EQ(call_method(tex_surf, S_QueryInterface, {iid, sc(0x1c)}), S_OK);
    uint32_t tex = rd32(sc(0x1c));
    CHECK(tex != 0);
    CHECK_EQ(call_method(tex, 3 /* GetHandle */, {dev, sc(0x20)}), D3D_OK_);
    CHECK(g_tex_hashes.size() >= 1);
    uint64_t first = g_tex_hashes.empty() ? 0 : g_tex_hashes.back();
    CHECK(first != 0);

    // The same content again is the same texture, whatever the handle says.
    write_pixels(0);
    CHECK(g_tex_hashes.size() >= 2);
    CHECK_EQ(g_tex_hashes.back(), first);

    // Different content is a different texture.
    write_pixels(1);
    CHECK(g_tex_hashes.size() >= 3);
    CHECK(g_tex_hashes.back() != first);
    uint64_t second = g_tex_hashes.back();

    // A provider that claims that content gets its pixels presented to the
    // host as R,G,B,A bytes, which is these masks on a little-endian target.
    g_tex_override.assign(4 * 4 * 4, 0x5a);
    g_tex_override_for = second;
    g_uploads.clear();
    write_pixels(1);
    CHECK_EQ(g_uploads.size(), 1u);
    if (g_uploads.size() == 1) {
        const TextureUpload &u = g_uploads[0];
        CHECK_EQ(u.bpp, 32);
        CHECK_EQ(u.pitch, 16);
        CHECK_EQ(u.rmask, 0x000000ffu);
        CHECK_EQ(u.gmask, 0x0000ff00u);
        CHECK_EQ(u.bmask, 0x00ff0000u);
        CHECK_EQ(u.amask, 0xff000000u);
        CHECK(!u.has_palette);
        CHECK(!u.pixels.empty() && u.pixels[0] == 0x5a);
    }

    // And the content it does not claim goes through untouched.
    g_tex_override_for = 0;
    g_uploads.clear();
    write_pixels(0);
    CHECK_EQ(g_uploads.size(), 1u);
    if (g_uploads.size() == 1)
        CHECK_EQ(g_uploads[0].bpp, 8);

    g_tex_hook_on = false;
    g_tex_override.clear();
}

// Every byte of every row is hashed, whatever the format.
//
// The first version read two bytes per pixel for anything above 8 bpp, so a
// 32-bpp surface had the second half of each row left out and two textures
// differing only there would have been handed the same override.
static void test_texture_hash_covers_the_whole_row() {
    cpu_reset();
    g_uploads.clear();
    g_tex_hashes.clear();
    g_tex_override.clear();
    g_tex_hook_on = true;

    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));
    call_method(dd, DD_SetCooperativeLevel, {0x1234, DDSCL_EXCLUSIVE | DDSCL_FULLSCREEN});
    call_method(dd, DD_SetDisplayMode, {640, 480, 16});

    uint32_t desc = sc(0x200);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT | DDSD_PIXELFORMAT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_TEXTURE);
    wr32(desc + DDSD_OFF_dwWidth, 4);
    wr32(desc + DDSD_OFF_dwHeight, 4);
    uint32_t pf = desc + DDSD_OFF_ddpfPixelFormat;
    wr32(pf + DDPF_OFF_dwSize, DDPF_SIZE);
    wr32(pf + DDPF_OFF_dwFlags, DDPF_RGB);
    wr32(pf + DDPF_OFF_dwRGBBitCount, 32);
    wr32(pf + DDPF_OFF_dwRBitMask, 0x00ff0000u);
    wr32(pf + DDPF_OFF_dwGBitMask, 0x0000ff00u);
    wr32(pf + DDPF_OFF_dwBBitMask, 0x000000ffu);
    wr32(pf + DDPF_OFF_dwRGBAlphaBitMask, 0xff000000u);
    CHECK_EQ(call_method(dd, DD_CreateSurface, {desc, sc(0x10), 0}), DD_OK);
    uint32_t tex_surf = rd32(sc(0x10));
    CHECK(tex_surf != 0);

    // Writes four 32-bit pixels a row; `tail` changes only the two on the
    // right, which is the half the old hash never read.
    auto write_pixels = [&](uint32_t tail) {
        uint32_t ld = sc(0x300);
        gm_zero(ld, DDSD_SIZE);
        wr32(ld + DDSD_OFF_dwSize, DDSD_SIZE);
        CHECK_EQ(call_method(tex_surf, S_Lock, {0, ld, DDLOCK_WAIT, 0}), DD_OK);
        uint32_t bits = rd32(ld + DDSD_OFF_lpSurface);
        uint32_t pitch = rd32(ld + DDSD_OFF_lPitch);
        for (uint32_t y = 0; y < 4; ++y)
            for (uint32_t x = 0; x < 4; ++x)
                wr32(bits + y * pitch + x * 4, x < 2 ? 0x11223344u : (0x55667700u | tail));
        CHECK_EQ(call_method(tex_surf, S_Unlock, {0}), DD_OK);
    };

    write_pixels(0);
    uint32_t dev = make_d3d_device();
    CHECK(dev != 0);
    uint8_t tex2[16] = {0x02, 0x15, 0x28, 0x93, 0xF8, 0x8C, 0xD0, 0x11,
                        0x89, 0xAB, 0x00, 0xA0, 0xC9, 0x05, 0x41, 0x29};
    uint32_t iid = sc(0x40);
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, tex2[i]);
    CHECK_EQ(call_method(tex_surf, S_QueryInterface, {iid, sc(0x1c)}), S_OK);
    uint32_t tex = rd32(sc(0x1c));
    CHECK(tex != 0);
    CHECK_EQ(call_method(tex, 3 /* GetHandle */, {dev, sc(0x20)}), D3D_OK_);
    CHECK(g_tex_hashes.size() >= 1);
    uint64_t first = g_tex_hashes.empty() ? 0 : g_tex_hashes.back();
    CHECK_EQ(g_uploads.empty() ? 0 : g_uploads.back().bpp, 32);

    write_pixels(0);
    CHECK(g_tex_hashes.size() >= 2);
    CHECK_EQ(g_tex_hashes.back(), first); // same bytes, same texture

    write_pixels(0xaa); // only the right-hand half moves
    CHECK(g_tex_hashes.size() >= 3);
    CHECK(g_tex_hashes.back() != first);

    g_tex_hook_on = false;
}

// A 24-bpp surface is stored four bytes to the pixel, and the hash covers all
// four.
//
// ddraw.cpp:84 gives every surface deeper than 16 bpp four bytes of storage,
// so the fourth byte of a 24-bpp pixel is real memory the guest can write.
// Hashing the nominal three would leave it out and two textures differing
// only there would be handed the same override.
static void test_texture_hash_covers_padding_bytes() {
    cpu_reset();
    g_uploads.clear();
    g_tex_hashes.clear();
    g_tex_override.clear();
    g_tex_hook_on = true;

    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));
    call_method(dd, DD_SetCooperativeLevel, {0x1234, DDSCL_EXCLUSIVE | DDSCL_FULLSCREEN});
    call_method(dd, DD_SetDisplayMode, {640, 480, 16});

    uint32_t desc = sc(0x200);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT | DDSD_PIXELFORMAT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_TEXTURE);
    wr32(desc + DDSD_OFF_dwWidth, 4);
    wr32(desc + DDSD_OFF_dwHeight, 4);
    uint32_t pf = desc + DDSD_OFF_ddpfPixelFormat;
    wr32(pf + DDPF_OFF_dwSize, DDPF_SIZE);
    wr32(pf + DDPF_OFF_dwFlags, DDPF_RGB);
    wr32(pf + DDPF_OFF_dwRGBBitCount, 24);
    wr32(pf + DDPF_OFF_dwRBitMask, 0x00ff0000u);
    wr32(pf + DDPF_OFF_dwGBitMask, 0x0000ff00u);
    wr32(pf + DDPF_OFF_dwBBitMask, 0x000000ffu);
    CHECK_EQ(call_method(dd, DD_CreateSurface, {desc, sc(0x10), 0}), DD_OK);
    uint32_t tex_surf = rd32(sc(0x10));
    CHECK(tex_surf != 0);

    // Every pixel keeps the same three colour bytes; only the LAST pixel's
    // fourth byte moves. Four pixels of four bytes is sixteen bytes a row, and
    // the nominal depth would have read twelve of them - so byte fifteen is
    // exactly the one a width-times-three hash never sees. Changing an earlier
    // pixel's padding byte would not have proved anything: those fall inside
    // the first twelve bytes and a wrong hash reads them anyway.
    auto write_pixels = [&](uint8_t pad) {
        uint32_t ld = sc(0x300);
        gm_zero(ld, DDSD_SIZE);
        wr32(ld + DDSD_OFF_dwSize, DDSD_SIZE);
        CHECK_EQ(call_method(tex_surf, S_Lock, {0, ld, DDLOCK_WAIT, 0}), DD_OK);
        uint32_t bits = rd32(ld + DDSD_OFF_lpSurface);
        uint32_t pitch = rd32(ld + DDSD_OFF_lPitch);
        CHECK(pitch >= 16); // four pixels of four bytes
        for (uint32_t y = 0; y < 4; ++y)
            for (uint32_t x = 0; x < 4; ++x) {
                uint32_t at = bits + y * pitch + x * 4;
                wr8(at + 0, 0x11);
                wr8(at + 1, 0x22);
                wr8(at + 2, 0x33);
                wr8(at + 3, x == 3 ? pad : 0x00);
            }
        CHECK_EQ(call_method(tex_surf, S_Unlock, {0}), DD_OK);
    };

    write_pixels(0);
    uint32_t dev = make_d3d_device();
    CHECK(dev != 0);
    uint8_t tex2[16] = {0x02, 0x15, 0x28, 0x93, 0xF8, 0x8C, 0xD0, 0x11,
                        0x89, 0xAB, 0x00, 0xA0, 0xC9, 0x05, 0x41, 0x29};
    uint32_t iid = sc(0x40);
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, tex2[i]);
    CHECK_EQ(call_method(tex_surf, S_QueryInterface, {iid, sc(0x1c)}), S_OK);
    uint32_t tex = rd32(sc(0x1c));
    CHECK(tex != 0);
    CHECK_EQ(call_method(tex, 3 /* GetHandle */, {dev, sc(0x20)}), D3D_OK_);
    CHECK(g_tex_hashes.size() >= 1);
    uint64_t first = g_tex_hashes.empty() ? 0 : g_tex_hashes.back();

    write_pixels(0);
    CHECK(g_tex_hashes.size() >= 2);
    CHECK_EQ(g_tex_hashes.back(), first);

    write_pixels(0xcc); // only the last pixel's fourth byte
    CHECK(g_tex_hashes.size() >= 3);
    CHECK(g_tex_hashes.back() != first);

    g_tex_hook_on = false;
}

static void test_palettised_texture_upload() {
    cpu_reset();
    g_uploads.clear();
    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));
    call_method(dd, DD_SetCooperativeLevel, {0x1234, DDSCL_EXCLUSIVE | DDSCL_FULLSCREEN});
    call_method(dd, DD_SetDisplayMode, {640, 480, 16});

    // An 8-bit texture in a 16-bit mode: the pixel format says palettised, so
    // the surface is 8bpp whatever the display is.
    uint32_t desc = sc(0x200);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT | DDSD_PIXELFORMAT);
    wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_TEXTURE);
    wr32(desc + DDSD_OFF_dwWidth, 4);
    wr32(desc + DDSD_OFF_dwHeight, 4);
    uint32_t pf = desc + DDSD_OFF_ddpfPixelFormat;
    wr32(pf + DDPF_OFF_dwSize, DDPF_SIZE);
    wr32(pf + DDPF_OFF_dwFlags, DDPF_RGB | DDPF_PALETTEINDEXED8);
    wr32(pf + DDPF_OFF_dwRGBBitCount, 8);
    CHECK_EQ(call_method(dd, DD_CreateSurface, {desc, sc(0x10), 0}), DD_OK);
    uint32_t tex_surf = rd32(sc(0x10));
    CHECK(tex_surf != 0);

    // A palette with three recognisable colours.
    uint32_t entries = sc(0x400);
    gm_zero(entries, 256 * 4);
    wr32(entries + 0 * 4, 0x00000000u);
    wr32(entries + 1 * 4, 0x000000FFu); // PALETTEENTRY is R,G,B,flags
    wr32(entries + 2 * 4, 0x0000FF00u);
    wr32(entries + 3 * 4, 0x00FF0000u);
    CHECK_EQ(
        call_method(dd, DD_CreatePalette, {DDPCAPS_8BIT | DDPCAPS_ALLOW256, entries, sc(0x18), 0}),
        DD_OK);
    uint32_t pal = rd32(sc(0x18));
    CHECK(pal != 0);
    CHECK_EQ(call_method(tex_surf, S_SetPalette, {pal}), DD_OK);

    // Write indices through a real Lock pointer.
    uint32_t ld = sc(0x300);
    gm_zero(ld, DDSD_SIZE);
    wr32(ld + DDSD_OFF_dwSize, DDSD_SIZE);
    CHECK_EQ(call_method(tex_surf, S_Lock, {0, ld, DDLOCK_WAIT, 0}), DD_OK);
    uint32_t bits = rd32(ld + DDSD_OFF_lpSurface);
    uint32_t pitch = rd32(ld + DDSD_OFF_lPitch);
    CHECK(bits != 0);
    CHECK(pitch >= 4);
    for (uint32_t y = 0; y < 4; ++y)
        for (uint32_t x = 0; x < 4; ++x)
            wr8(bits + y * pitch + x, (uint8_t)((x + y) % 4));
    CHECK_EQ(call_method(tex_surf, S_Unlock, {0}), DD_OK);

    // Until a handle exists there is no texture to upload: an Unlock before
    // GetHandle must not invent one.
    CHECK_EQ(g_uploads.size(), 0u);

    // QueryInterface to IDirect3DTexture2 and take the handle. That is the
    // point the surface becomes a texture, and the upload must carry both the
    // indices and the palette.
    uint32_t dev = make_d3d_device();
    CHECK(dev != 0);
    uint8_t tex2[16] = {0x02, 0x15, 0x28, 0x93, 0xF8, 0x8C, 0xD0, 0x11,
                        0x89, 0xAB, 0x00, 0xA0, 0xC9, 0x05, 0x41, 0x29};
    uint32_t iid = sc(0x40);
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, tex2[i]);
    CHECK_EQ(call_method(tex_surf, S_QueryInterface, {iid, sc(0x1c)}), S_OK);
    uint32_t tex = rd32(sc(0x1c));
    CHECK(tex != 0);

    g_uploads.clear();
    CHECK_EQ(call_method(tex, 3 /* GetHandle */, {dev, sc(0x20)}), D3D_OK_);
    CHECK(rd32(sc(0x20)) != 0);
    CHECK_EQ(g_uploads.size(), 1u);
    if (g_uploads.size() == 1) {
        const TextureUpload &u = g_uploads[0];
        CHECK_EQ(u.bpp, 8);
        CHECK_EQ(u.width, 4);
        CHECK_EQ(u.height, 4);
        CHECK(u.has_palette);
        // The indices, exactly as written.
        CHECK((int)u.pixels.size() >= u.pitch * u.height);
        bool indices_ok = u.pixels.size() >= (size_t)u.pitch * 4;
        for (int y = 0; y < 4 && indices_ok; ++y)
            for (int x = 0; x < 4; ++x)
                if (u.pixels[(size_t)y * u.pitch + x] != (uint8_t)((x + y) % 4))
                    indices_ok = false;
        CHECK(indices_ok);
        // And the colours those indices resolve against.
        CHECK_EQ(u.palette[0] & 0xffffffu, 0x000000u);
        CHECK_EQ(u.palette[1] & 0xffffffu, 0xFF0000u);
        CHECK_EQ(u.palette[2] & 0xffffffu, 0x00FF00u);
        CHECK_EQ(u.palette[3] & 0xffffffu, 0x0000FFu);
    }

    // Editing the pixels sends them again.
    g_uploads.clear();
    CHECK_EQ(call_method(tex_surf, S_Lock, {0, ld, DDLOCK_WAIT, 0}), DD_OK);
    wr8(rd32(ld + DDSD_OFF_lpSurface), 3);
    CHECK_EQ(call_method(tex_surf, S_Unlock, {0}), DD_OK);
    CHECK_EQ(g_uploads.size(), 1u);
    if (g_uploads.size() == 1)
        CHECK_EQ(g_uploads[0].pixels[0], 3u);

    // Changing the palette entries sends the texture again with the new
    // colours, even though not one index moved.
    g_uploads.clear();
    gm_zero(entries, 256 * 4);
    // SetEntries reads from the start of the array; dwStartingEntry is where
    // they land, not where they come from.
    wr32(entries + 0, 0x0000FFFFu); // index 1 becomes yellow
    CHECK_EQ(call_method(pal, P_SetEntries, {0, 1, 1, entries}), DD_OK);
    CHECK_EQ(g_uploads.size(), 1u);
    if (g_uploads.size() == 1) {
        CHECK(g_uploads[0].has_palette);
        CHECK_EQ(g_uploads[0].palette[1] & 0xffffffu, 0xFFFF00u);
    }

    // Attaching a different palette does too: the indices mean something new.
    gm_zero(entries, 256 * 4);
    wr32(entries + 2 * 4, 0x00FFFFFFu);
    CHECK_EQ(
        call_method(dd, DD_CreatePalette, {DDPCAPS_8BIT | DDPCAPS_ALLOW256, entries, sc(0x24), 0}),
        DD_OK);
    uint32_t pal2 = rd32(sc(0x24));
    g_uploads.clear();
    CHECK_EQ(call_method(tex_surf, S_SetPalette, {pal2}), DD_OK);
    CHECK_EQ(g_uploads.size(), 1u);
    if (g_uploads.size() == 1)
        CHECK_EQ(g_uploads[0].palette[2] & 0xffffffu, 0xFFFFFFu);
}

// ---------------------------------------------------------------------------
// EnumTextureFormats offers exactly what a plain 1998 HAL offered, and every
// format it offers can actually be created.
// ---------------------------------------------------------------------------
static std::vector<std::array<uint32_t, 6>> g_texfmts;

static void test_texture_formats() {
    cpu_reset();
    uint32_t dev = make_d3d_device();
    CHECK(dev != 0);
    static uint32_t cb = imports_alloc_trampoline(
        "TEST", "TexFormats",
        [](X86 *c) {
            uint32_t pf = arg(c, 0) + DDSD_OFF_ddpfPixelFormat;
            g_texfmts.push_back({rd32(pf + DDPF_OFF_dwFlags), rd32(pf + DDPF_OFF_dwRGBBitCount),
                                 rd32(pf + DDPF_OFF_dwRBitMask), rd32(pf + DDPF_OFF_dwGBitMask),
                                 rd32(pf + DDPF_OFF_dwBBitMask),
                                 rd32(pf + DDPF_OFF_dwRGBAlphaBitMask)});
            set_eax(c, DDENUMRET_OK);
        },
        2);
    g_texfmts.clear();
    CHECK_EQ(call_method(dev, DEV_EnumTextureFormats, {cb, 0}), D3D_OK_);
    CHECK_EQ(g_texfmts.size(), 6u);

    // The four formats the Wine trace of the original shows it being offered
    // and able to use, in the order it was offered them. P8 is deliberately
    // absent: the original was never offered it, though it does create one
    // palettised texture directly through CreateSurface.
    const uint32_t want[6][6] = {
        {DDPF_RGB, 16, 0x7c00, 0x03e0, 0x001f, 0},                         // B5G5R5X1
        {DDPF_RGB | DDPF_ALPHAPIXELS, 16, 0x7c00, 0x03e0, 0x001f, 0x8000}, // B5G5R5A1
        {DDPF_RGB | DDPF_ALPHAPIXELS, 16, 0x0f00, 0x00f0, 0x000f, 0xf000}, // B4G4R4A4
        {DDPF_RGB, 16, 0xf800, 0x07e0, 0x001f, 0},                         // B5G6R5
        {DDPF_RGB, 32, 0xff0000, 0xff00, 0xff, 0},
        {DDPF_RGB | DDPF_ALPHAPIXELS, 32, 0xff0000, 0xff00, 0xff, 0xff000000},
    };
    for (size_t i = 0; i < g_texfmts.size() && i < 6; ++i) {
        for (int j = 0; j < 6; ++j)
            CHECK_EQ(g_texfmts[i][j], want[i][j]);
        CHECK((g_texfmts[i][0] & DDPF_FOURCC) == 0);
    }

    // Every advertised format can be created, so the enumeration is not a
    // promise the surface allocator breaks.
    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));
    call_method(dd, DD_SetCooperativeLevel, {0x1234, DDSCL_EXCLUSIVE | DDSCL_FULLSCREEN});
    call_method(dd, DD_SetDisplayMode, {640, 480, 16});
    for (auto &f : g_texfmts) {
        uint32_t desc = sc(0x200);
        gm_zero(desc, DDSD_SIZE);
        wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
        wr32(desc + DDSD_OFF_dwFlags, DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT | DDSD_PIXELFORMAT);
        wr32(desc + DDSD_OFF_ddsCaps, DDSCAPS_TEXTURE);
        wr32(desc + DDSD_OFF_dwWidth, 8);
        wr32(desc + DDSD_OFF_dwHeight, 8);
        uint32_t pf = desc + DDSD_OFF_ddpfPixelFormat;
        wr32(pf + DDPF_OFF_dwSize, DDPF_SIZE);
        wr32(pf + DDPF_OFF_dwFlags, f[0]);
        wr32(pf + DDPF_OFF_dwRGBBitCount, f[1]);
        wr32(pf + DDPF_OFF_dwRBitMask, f[2]);
        wr32(pf + DDPF_OFF_dwGBitMask, f[3]);
        wr32(pf + DDPF_OFF_dwBBitMask, f[4]);
        wr32(pf + DDPF_OFF_dwRGBAlphaBitMask, f[5]);
        CHECK_EQ(call_method(dd, DD_CreateSurface, {desc, sc(0x10), 0}), DD_OK);
        uint32_t surf = rd32(sc(0x10));
        CHECK(surf != 0);
        if (!surf)
            continue;

        // And the format survives the trip to the renderer. A surface created
        // as 4-4-4-4 that arrives as 5-6-5 would be drawn with the wrong
        // colours and no alpha, which is exactly what advertising a format
        // the pipeline cannot carry would look like.
        uint8_t tex2[16] = {0x02, 0x15, 0x28, 0x93, 0xF8, 0x8C, 0xD0, 0x11,
                            0x89, 0xAB, 0x00, 0xA0, 0xC9, 0x05, 0x41, 0x29};
        uint32_t iid = sc(0x40);
        for (int i = 0; i < 16; ++i)
            wr8(iid + (uint32_t)i, tex2[i]);
        if (call_method(surf, S_QueryInterface, {iid, sc(0x1c)}) != S_OK) {
            CHECK(false);
            continue;
        }
        uint32_t tex = rd32(sc(0x1c));
        g_uploads.clear();
        CHECK_EQ(call_method(tex, TEX_GetHandle, {dev, sc(0x20)}), D3D_OK_);
        CHECK_EQ(g_uploads.size(), 1u);
        if (g_uploads.size() != 1)
            continue;
        const TextureUpload &u = g_uploads[0];
        CHECK_EQ((uint32_t)u.bpp, f[1]);
        CHECK_EQ(u.rmask, f[2]);
        CHECK_EQ(u.gmask, f[3]);
        CHECK_EQ(u.bmask, f[4]);
        CHECK_EQ(u.amask, f[5]);
        CHECK((f[0] & DDPF_FOURCC) == 0);
    }
}

// ---------------------------------------------------------------------------
// The DirectSound data path end to end: the exact bytes the guest writes
// through a Lock pointer are the bytes the host is asked to play, with the
// buffer's own format. Covers the wrap pair, both sample widths, and that a
// duplicate shares the original's data rather than a copy of it.
// ---------------------------------------------------------------------------
static uint32_t make_dsound() {
    uint32_t create = tramp("DSOUND.dll", "ord1");
    call_shim(create, {0, sc(0), 0});
    uint32_t ds = rd32(sc(0));
    call_method(ds, DS_SetCooperativeLevel, {0x20004, 3});
    return ds;
}

static uint32_t make_buffer(uint32_t ds, uint32_t chans, uint32_t rate, uint32_t bits,
                            uint32_t bytes, uint32_t out) {
    uint32_t wfx = sc(0x100);
    uint32_t align = chans * (bits / 8);
    wr16(wfx + WFX_OFF_wFormatTag, WAVE_FORMAT_PCM);
    wr16(wfx + WFX_OFF_nChannels, (uint16_t)chans);
    wr32(wfx + WFX_OFF_nSamplesPerSec, rate);
    wr32(wfx + WFX_OFF_nAvgBytesPerSec, rate * align);
    wr16(wfx + WFX_OFF_nBlockAlign, (uint16_t)align);
    wr16(wfx + WFX_OFF_wBitsPerSample, (uint16_t)bits);
    wr16(wfx + WFX_OFF_cbSize, 0);
    uint32_t bd = sc(0x200);
    gm_zero(bd, DSBUFFERDESC_SIZE);
    wr32(bd + DSBD_OFF_dwSize, DSBUFFERDESC_SIZE);
    wr32(bd + DSBD_OFF_dwFlags, DSBCAPS_CTRLVOLUME | DSBCAPS_CTRLFREQUENCY | DSBCAPS_STATIC);
    wr32(bd + DSBD_OFF_dwBufferBytes, bytes);
    wr32(bd + DSBD_OFF_lpwfxFormat, wfx);
    if (call_method(ds, DS_CreateSoundBuffer, {bd, out, 0}) != DS_OK)
        return 0;
    return rd32(out);
}

static void test_dsound_data_path() {
    cpu_reset();
    g_plays.clear();
    uint32_t ds = make_dsound();
    CHECK(ds != 0);

    // Report the output speaker ABI enum, not the number of PCM channels.
    CHECK_EQ(call_method(ds, 8, {sc(0x400)}), DS_OK); // GetSpeakerConfig
    CHECK_EQ(rd32(sc(0x400)), 4u);                    // DSSPEAKER_STEREO, not MONO (2)
    CHECK_EQ(call_method(ds, 8, {0}), DSERR_INVALIDPARAM);
    CHECK_EQ(call_method(ds, 8, {0xfffffffdu}), DSERR_INVALIDPARAM);

    // --- 16-bit stereo: a known signed waveform, byte for byte.
    const uint32_t kBytes = 512;
    uint32_t buf = make_buffer(ds, 2, 22050, 16, kBytes, sc(4));
    CHECK(buf != 0);
    CHECK_EQ(call_method(buf, B_Lock, {0, kBytes, sc(0x300), sc(0x304), sc(0x308), sc(0x30c), 0}),
             DS_OK);
    uint32_t p1 = rd32(sc(0x300));
    CHECK_EQ(rd32(sc(0x304)), kBytes);
    CHECK_EQ(rd32(sc(0x308)), 0u); // no wrap: the second region is empty
    CHECK_EQ(rd32(sc(0x30c)), 0u);
    std::vector<uint8_t> want(kBytes);
    for (uint32_t i = 0; i < kBytes / 2; ++i) {
        // A signed triangle, so a byte-order or sign mistake is visible.
        int16_t v = (int16_t)(((int)i % 64) * 512 - 16384);
        want[i * 2 + 0] = (uint8_t)(v & 0xff);
        want[i * 2 + 1] = (uint8_t)((v >> 8) & 0xff);
        wr8(p1 + i * 2 + 0, want[i * 2 + 0]);
        wr8(p1 + i * 2 + 1, want[i * 2 + 1]);
    }
    CHECK_EQ(call_method(buf, B_Unlock, {p1, kBytes, 0, 0}), DS_OK);

    g_plays.clear();
    CHECK_EQ(call_method(buf, B_Play, {0, 0, 0}), DS_OK);
    CHECK_EQ(g_plays.size(), 1u);
    if (g_plays.size() == 1) {
        const PlayRecord &r = g_plays[0];
        CHECK_EQ(r.rate, 22050);
        CHECK_EQ(r.channels, 2);
        CHECK_EQ(r.bits, 16);
        CHECK_EQ(r.loop, 0);
        CHECK_EQ(r.bytes, kBytes);
        CHECK(r.pcm == want);
    }

    // --- The wrap pair. A lock that runs off the end hands back two regions
    // that together cover the request, and writing through both reaches the
    // host as one contiguous buffer.
    CHECK_EQ(
        call_method(buf, B_Lock, {kBytes - 8, 16, sc(0x300), sc(0x304), sc(0x308), sc(0x30c), 0}),
        DS_OK);
    uint32_t w1 = rd32(sc(0x300)), l1 = rd32(sc(0x304));
    uint32_t w2 = rd32(sc(0x308)), l2 = rd32(sc(0x30c));
    CHECK_EQ(l1, 8u);
    CHECK_EQ(l2, 8u);
    CHECK(w2 != 0);
    CHECK(w1 != w2);
    for (uint32_t i = 0; i < 8; ++i) {
        wr8(w1 + i, 0xA1);
        want[kBytes - 8 + i] = 0xA1;
    }
    for (uint32_t i = 0; i < 8; ++i) {
        wr8(w2 + i, 0xB2);
        want[i] = 0xB2;
    }
    CHECK_EQ(call_method(buf, B_Unlock, {w1, l1, w2, l2}), DS_OK);
    g_plays.clear();
    CHECK_EQ(call_method(buf, B_Play, {0, 0, 0}), DS_OK);
    CHECK_EQ(g_plays.size(), 1u);
    if (g_plays.size() == 1)
        CHECK(g_plays[0].pcm == want);

    // A lock starting past the end is refused rather than wrapped.
    CHECK_EQ(call_method(buf, B_Lock, {kBytes, 4, sc(0x300), sc(0x304), sc(0x308), sc(0x30c), 0}),
             DSERR_INVALIDPARAM);

    // --- 8-bit mono is unsigned, centred on 0x80, and is not sign-converted
    // on the way to the host.
    uint32_t b8 = make_buffer(ds, 1, 11025, 8, 256, sc(8));
    CHECK(b8 != 0);
    CHECK_EQ(call_method(b8, B_Lock, {0, 256, sc(0x300), sc(0x304), sc(0x308), sc(0x30c), 0}),
             DS_OK);
    uint32_t q1 = rd32(sc(0x300));
    std::vector<uint8_t> want8(256);
    for (uint32_t i = 0; i < 256; ++i) {
        want8[i] = (uint8_t)i;
        wr8(q1 + i, (uint8_t)i);
    }
    CHECK_EQ(call_method(b8, B_Unlock, {q1, 256, 0, 0}), DS_OK);
    g_plays.clear();
    CHECK_EQ(call_method(b8, B_Play, {0, 0, DSBPLAY_LOOPING}), DS_OK);
    CHECK_EQ(g_plays.size(), 1u);
    if (g_plays.size() == 1) {
        CHECK_EQ(g_plays[0].bits, 8);
        CHECK_EQ(g_plays[0].channels, 1);
        CHECK_EQ(g_plays[0].rate, 11025);
        CHECK_EQ(g_plays[0].loop, 1);
        CHECK(g_plays[0].pcm == want8);
    }

    // SetFrequency changes the rate the host is told, not the data.
    CHECK_EQ(call_method(b8, B_SetFrequency, {22050}), DS_OK);
    g_plays.clear();
    CHECK_EQ(call_method(b8, B_Play, {0, 0, 0}), DS_OK);
    CHECK_EQ(g_plays.size(), 1u);
    if (g_plays.size() == 1) {
        CHECK_EQ(g_plays[0].rate, 22050);
        CHECK(g_plays[0].pcm == want8);
    }

    // --- A duplicate shares the original's sample data: a write through the
    // original's Lock pointer is heard by the duplicate. DirectSound
    // duplicates the interface, not the samples.
    CHECK_EQ(call_method(ds, DS_DuplicateSoundBuffer, {buf, sc(0xc)}), DS_OK);
    uint32_t dup = rd32(sc(0xc));
    CHECK(dup != 0);
    CHECK(dup != buf);
    CHECK_EQ(call_method(buf, B_Lock, {0, 4, sc(0x300), sc(0x304), sc(0x308), sc(0x30c), 0}),
             DS_OK);
    uint32_t d1 = rd32(sc(0x300));
    for (uint32_t i = 0; i < 4; ++i) {
        wr8(d1 + i, 0x5C);
        want[i] = 0x5C;
    }
    CHECK_EQ(call_method(buf, B_Unlock, {d1, 4, 0, 0}), DS_OK);
    g_plays.clear();
    CHECK_EQ(call_method(dup, B_Play, {0, 0, 0}), DS_OK);
    CHECK_EQ(g_plays.size(), 1u);
    if (g_plays.size() == 1) {
        CHECK(g_plays[0].pcm == want);
        CHECK_EQ(g_plays[0].bits, 16);
        CHECK_EQ(g_plays[0].channels, 2);
    }
    // and it is a separate voice, so the two can sound at once.
    if (g_plays.size() == 1)
        CHECK(g_plays[0].channel >= 0);

    // --- GetFormat reports back exactly what the buffer was created with.
    uint32_t got = sc(0x400);
    gm_zero(got, 32);
    CHECK_EQ(call_method(buf, B_GetFormat, {got, 18, sc(0x420)}), DS_OK);
    CHECK_EQ(rd16(got + WFX_OFF_wFormatTag), WAVE_FORMAT_PCM);
    CHECK_EQ(rd16(got + WFX_OFF_nChannels), 2);
    CHECK_EQ(rd32(got + WFX_OFF_nSamplesPerSec), 22050u);
    CHECK_EQ(rd16(got + WFX_OFF_wBitsPerSample), 16);
    CHECK_EQ(rd16(got + WFX_OFF_nBlockAlign), 4);
    CHECK_EQ(rd32(got + WFX_OFF_nAvgBytesPerSec), 22050u * 4u);
}

// ---------------------------------------------------------------------------
// The video player's refill gate, driven the way the player drives it.
//
// 0057df10 returns the bytes from the game's own write offset forward to the
// play cursor, and 0057dc80 refills only when that reaches the length of the
// next chunk. So the cursor the shim reports is not a readout: it is the thing
// that decides whether the guest ever writes again. A cursor that sits a fixed
// distance ahead of the write offset - which is what "the last byte fed minus
// what is still in flight" reports, because the guest deliberately keeps about
// a lap in flight - stops the gate opening and the movie stalls on a black
// screen with no error anywhere.
//
// This runs the whole loop: poll, open the gate, lock, write, unlock, repeat,
// with a host that plays what it is given at the sample rate. It asserts the
// gate opens every time and the write offset goes right round the ring more
// than once, which is the property the stall broke.
// ---------------------------------------------------------------------------
// Plays `n` more bytes of the stream. The host's own cursor counts on from the
// offset it resumed at, at the sample rate, and never goes backwards; the
// queue drains behind it.
static void audio_play_bytes(uint64_t n) {
    g_stream_played += (uint32_t)n;
    g_queued_bytes = g_queued_bytes > n ? (uint32_t)(g_queued_bytes - n) : 0;
}

static void test_dsound_stream_pacing() {
    cpu_reset();
    qmixer_reset();
    g_plays.clear();
    g_queues.clear();
    g_queued_bytes = 0;
    g_voice_remaining = 0;
    g_queued_accepted = 0;
    g_queue_enabled = true;
    g_test_audio_pos = 0;

    const uint32_t kRing = 32768; // the original's, at 22050/2/16
    const uint32_t kAlign = 4;
    uint32_t ds = make_dsound();
    uint32_t buf = make_buffer(ds, 2, 22050, 16, kRing, sc(4));
    CHECK(buf != 0);
    CHECK_EQ(call_method(buf, B_Play, {0, 0, DSBPLAY_LOOPING}), DS_OK);

    // The player's own state: where it will write next, and the chunk sizes it
    // alternates between, both from the trace.
    uint32_t write = 0;
    const uint32_t chunks[2] = {3584, 3472};
    int refills = 0;
    int stalls = 0;
    uint32_t last_play = 0;
    bool converted = false;

    // Four hundred ten-millisecond ticks: forty seconds, several laps of a
    // ring that holds 0.37 of one.
    for (int tick = 0; tick < 400; ++tick) {
        uint32_t chunk = chunks[refills & 1];
        // Ten milliseconds of audio at 22050 Hz stereo 16-bit, whole frames.
        if (converted)
            audio_play_bytes(880);
        else
            g_test_audio_pos = (uint32_t)(((uint64_t)tick * 880) % kRing);

        CHECK_EQ(call_method(buf, B_GetCurrentPosition, {sc(0x530), sc(0x534)}), DS_OK);
        uint32_t play = rd32(sc(0x530));
        CHECK(play < kRing);
        last_play = play;
        // It is the play cursor, so it is where the host actually is: where the
        // stream began plus what has been played since. Reporting anything
        // measured against how much is in flight instead gives a cursor that
        // tracks the guest's own write offset, which is the shape the video
        // player's gate cannot work with.
        if (converted) {
            uint32_t want = (uint32_t)(((uint64_t)g_stream_base + g_stream_played) % kRing);
            CHECK_EQ(play, want);
        }

        // 0057df10: the distance from the write offset forward to the cursor.
        uint32_t gap = play >= write ? play - write : play + kRing - write;
        if (gap < chunk) {
            ++stalls;
            continue;
        }

        CHECK_EQ(
            call_method(buf, B_Lock, {write, chunk, sc(0x300), sc(0x304), sc(0x308), sc(0x30c), 0}),
            DS_OK);
        uint32_t a1 = rd32(sc(0x300)), l1 = rd32(sc(0x304));
        uint32_t a2 = rd32(sc(0x308)), l2 = rd32(sc(0x30c));
        for (uint32_t i = 0; i < l1; ++i)
            wr8(a1 + i, (uint8_t)(0x40 + (refills & 0x3f)));
        for (uint32_t i = 0; i < l2; ++i)
            wr8(a2 + i, (uint8_t)(0x40 + (refills & 0x3f)));
        g_plays.clear();
        CHECK_EQ(call_method(buf, B_Unlock, {a1, l1, a2, l2}), DS_OK);
        if (!converted) {
            // The first refill converts the ring, and the conversion is not a
            // play: nothing is stopped and nothing restarts.
            CHECK_EQ(g_plays.size(), 0u);
            converted = true;
        } else {
            // and no refill after it restarts the voice.
            CHECK_EQ(g_plays.size(), 0u);
        }
        write = (write + chunk) % kRing;
        ++refills;
    }

    // The gate opened, over and over, and the write offset went right round
    // the ring more than once. A cursor pinned a fixed distance ahead of the
    // write offset gives one refill and then nothing at all.
    CHECK(refills >= 30);
    CHECK_EQ((uint32_t)(g_queues.size() > 0), 1u);
    CHECK(last_play < kRing);
    // Every byte the guest wrote reached the host, in order and none twice.
    uint64_t queued = 0;
    for (const PlayRecord &q : g_queues)
        queued += q.bytes;
    CHECK_EQ(queued, g_queued_accepted);
    CHECK(queued >= (uint64_t)kRing);

    // Running dry must not freeze the cursor, and this is the deadlock it
    // would otherwise be. The host has played everything it was given, so the
    // cursor arrives exactly at the guest's write offset and the gate wants a
    // whole chunk beyond it. On real hardware the cursor does not stop there:
    // it runs on into whatever is still in the ring, and the gate reopens. A
    // cursor that stops means the guest never writes, which means nothing is
    // ever queued, which means the cursor never moves again - and the movie
    // waits on it with nothing to show that anything is wrong.
    g_queued_bytes = 0;
    g_voice_remaining = 0;
    uint32_t chunk = chunks[refills & 1];
    bool reopened = false;
    uint32_t first_dry = 0, last_dry = 0;
    // The gate wants forty milliseconds of audio, so this polls for up to two
    // seconds of real time. No check inside the loop: the assertion is that it
    // reopened at all, and one that reopens has nothing else to say.
    for (int i = 0; i < 400 && !reopened; ++i) {
        // The host paces a dry stream from its own clock; here the test is
        // the clock, so it advances it by a millisecond of audio a step.
        audio_play_bytes(880);
        call_method(buf, B_GetCurrentPosition, {sc(0x530), sc(0x534)});
        uint32_t p = rd32(sc(0x530));
        if (!first_dry)
            first_dry = p ? p : 1;
        last_dry = p;
        uint32_t gap = p >= write ? p - write : p + kRing - write;
        if (gap >= chunk)
            reopened = true;
    }
    CHECK(reopened);
    CHECK(last_dry < kRing);

    CHECK_EQ(call_method(buf, B_Stop, {}), DS_OK);
    g_queue_enabled = false;
    g_queued_bytes = 0;
    g_voice_remaining = 0;
    g_queued_accepted = 0;
    g_queues.clear();
    g_test_audio_pos = 0;
}

// ---------------------------------------------------------------------------
// A streamed wave's buffer is the shim's, and freeing the wave has to free it.
//
// The game opens a wave per sound and frees the previous one before each new
// play - 388 of them in one scripted level, and a session is far longer than
// that. Nothing else ever frees the buffer this shim allocates for a streamed
// wave, so leaking it here leaks the guest's heap at the rate the game makes
// sounds. What that looks like from the outside is not a crash: the allocation
// eventually fails, read_wave_record refuses the wave, and the sounds stop.
// ---------------------------------------------------------------------------
static void test_qmixer_stream_buffer_lifetime() {
    cpu_reset();
    qmixer_reset();
    g_plays.clear();
    g_queue_enabled = false;

    uint32_t hmix = call_shim(tramp("QMIXER.dll", "QSWaveMixInitEx"), {0});
    CHECK(hmix != 0);
    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixActivate"), {hmix, 1}), 0u);
    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixOpenChannel"), {hmix, 4, 2}), 0u);

    uint32_t wfx = sc(0xc00);
    gm_zero(wfx, SDK_WAVEFORMATEX);
    wr16(wfx + WFX_OFF_wFormatTag, WAVE_FORMAT_PCM);
    wr16(wfx + WFX_OFF_nChannels, 1);
    wr32(wfx + WFX_OFF_nSamplesPerSec, 11025);
    wr32(wfx + WFX_OFF_nAvgBytesPerSec, 11025 * 2);
    wr16(wfx + WFX_OFF_nBlockAlign, 2);
    wr16(wfx + WFX_OFF_wBitsPerSample, 16);

    static uint32_t cb = imports_alloc_trampoline(
        "TEST", "LifetimeCallback",
        [](X86 *c) {
            uint32_t dst = arg(c, 0), bytes = arg(c, 1);
            for (uint32_t i = 0; i < bytes; ++i)
                wr8(dst + i, 0x40);
            set_eax(c, 1);
        },
        3);

    const uint32_t kChunk = 0x800;
    uint32_t rec = sc(0x1800);
    gm_zero(rec, QSWAVEMIXOPENWAVEDATA_SIZE);
    wr32(rec + QSOWD_OFF_lpFormat, wfx);
    wr32(rec + QSOWD_OFF_lpData, kChunk);
    wr32(rec + QSOWD_OFF_pfnCallback, cb);
    wr32(rec + QSOWD_OFF_pvContext, 0xF00D0000u);

    uint32_t open_wave = tramp("QMIXER.dll", "QSWaveMixOpenWaveEx");
    uint32_t free_wave = tramp("QMIXER.dll", "QSWaveMixFreeWave");
    uint32_t play = tramp("QMIXER.dll", "QSWaveMixPlayEx");

    // The game's own shape: open, play, free the previous, open the next.
    uint32_t before = heap_stats().used_blocks;
    uint32_t prev = 0;
    for (uint32_t i = 0; i < 40; ++i) {
        uint32_t h = call_shim(open_wave, {hmix, rec, QSWAVEMIX_STREAMED | 1});
        CHECK(h != 0);
        CHECK_EQ(call_shim(play, {hmix, 2, 0x20, h, 0, 0}), 0u);
        if (prev)
            CHECK_EQ(call_shim(free_wave, {hmix, prev}), 0u);
        prev = h;
    }
    CHECK_EQ(call_shim(free_wave, {hmix, prev}), 0u);
    uint32_t after = heap_stats().used_blocks;

    // Forty sounds, every one of them freed, and the heap is where it started.
    // Before this was fixed it grew by one block of the wave's buffer size per
    // sound and never came back.
    CHECK_EQ(after, before);

    // Closing the session releases the buffers of waves the game never freed.
    for (uint32_t i = 0; i < 5; ++i) {
        uint32_t h = call_shim(open_wave, {hmix, rec, QSWAVEMIX_STREAMED | 1});
        CHECK(h != 0);
    }
    CHECK(heap_stats().used_blocks > before);
    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixCloseSession"), {hmix}), 0u);
    CHECK_EQ(heap_stats().used_blocks, before);

    qmixer_reset();
}

// ---------------------------------------------------------------------------
// How far ahead of the sound the game's stream reader is allowed to run.
//
// This is not a buffering preference. The size the game hands OpenWaveEx is
// the size of ONE streaming buffer - 0x3c00 at 0056f90a in the play routine,
// 0x7800 when the flag at [edi+0x6a] is set - and QMixer fills that buffer and
// calls back when it needs the next one. The callback at 00576660 forwards to
// a virtual method on the game's own emitter and reports end of stream when
// that method returns 1, so every pull advances state inside the game, not
// inside the mixer.
//
// Pulling all four chunks at play time therefore ran the emitter about 1.4
// seconds of audio ahead of anything audible. Measured in the headless smoke,
// the effect on the game was large and one-directional:
//
//     chunks pulled per call    plays    position updates per play
//     4 (what this replaced)      387                         1.17
//     the original, for scale     136                        29.70
//
// The original gives a sound about thirty position updates because it is still
// playing thirty frames later. Ours got one, because the game had already been
// told the sound was over.
// ---------------------------------------------------------------------------
static uint32_t g_prefetch_calls = 0;

static void test_qmixer_stream_prefetch() {
    cpu_reset();
    qmixer_reset();
    g_plays.clear();
    g_queue_enabled = true;
    g_prefetch_calls = 0;

    uint32_t hmix = call_shim(tramp("QMIXER.dll", "QSWaveMixInitEx"), {0});
    CHECK(hmix != 0);
    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixActivate"), {hmix, 1}), 0u);
    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixOpenChannel"), {hmix, 4, 2}), 0u);

    uint32_t wfx = sc(0xc00);
    gm_zero(wfx, SDK_WAVEFORMATEX);
    wr16(wfx + WFX_OFF_wFormatTag, WAVE_FORMAT_PCM);
    wr16(wfx + WFX_OFF_nChannels, 1);
    wr32(wfx + WFX_OFF_nSamplesPerSec, 11025);
    wr32(wfx + WFX_OFF_nAvgBytesPerSec, 11025 * 2);
    wr16(wfx + WFX_OFF_nBlockAlign, 2);
    wr16(wfx + WFX_OFF_wBitsPerSample, 16);

    // A source with far more to give than the buffer holds, which is the case
    // that separates "filled the buffer" from "drained the game".
    static uint32_t cb = imports_alloc_trampoline(
        "TEST", "PrefetchCallback",
        [](X86 *c) {
            uint32_t dst = arg(c, 0), bytes = arg(c, 1);
            for (uint32_t i = 0; i < bytes; ++i)
                wr8(dst + i, 0x30);
            ++g_prefetch_calls;
            set_eax(c, 1); // non-zero: there is more after this
        },
        3);

    const uint32_t kChunk = 0x800;
    uint32_t rec = sc(0x1800);
    gm_zero(rec, QSWAVEMIXOPENWAVEDATA_SIZE);
    wr32(rec + QSOWD_OFF_lpFormat, wfx);
    wr32(rec + QSOWD_OFF_lpData, kChunk);
    wr32(rec + QSOWD_OFF_pfnCallback, cb);
    wr32(rec + QSOWD_OFF_pvContext, 0xF00D0000u);

    uint32_t h =
        call_shim(tramp("QMIXER.dll", "QSWaveMixOpenWaveEx"), {hmix, rec, QSWAVEMIX_STREAMED | 1});
    CHECK(h != 0);

    // Opening a wave must not read from it. The game has not asked for the
    // sound yet, and a read here would advance its emitter before the play.
    CHECK_EQ(g_prefetch_calls, 0u);

    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixPlayEx"), {hmix, 2, 0x20, h, 0, 0}), 0u);

    // Two: one buffer playing and one waiting behind it. Not four, and not as
    // many as the source will give.
    CHECK_EQ(g_prefetch_calls, 2u);
    CHECK_EQ(g_plays.size(), 1u);

    // The pump tops the queue up as it drains, one buffer at a time rather
    // than emptying the source.
    uint32_t before = g_prefetch_calls;
    g_queued_bytes = 0;
    g_voice_remaining = 0; // the host has played what it had
    qmixer_frame_pump(&g_cpu);
    CHECK_EQ(g_prefetch_calls, before + 1);

    // And with a buffer still waiting it pulls nothing at all, so the reader
    // never gets further than one buffer ahead of the one being played.
    g_queued_bytes = kChunk;
    before = g_prefetch_calls;
    qmixer_frame_pump(&g_cpu);
    CHECK_EQ(g_prefetch_calls, before);

    // Nothing has told the game its sound is over, because it is not.
    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixFreeWave"), {hmix, h}), 0u);
    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixCloseSession"), {hmix}), 0u);
    qmixer_reset();
}

// ---------------------------------------------------------------------------
// Which host question the refill gate asks.
//
// It reads host_audio_voice_remaining_bytes, not host_audio_queued_bytes. The
// two are different on purpose. queued_bytes is the DirectSound streaming
// contract - how much of what THIS CALLER appended is still to play - and a
// play resets it, because a play starts a new sound rather than continuing
// one. QMixer does not append into a ring: it plays a sound on a named channel
// and refills behind it, so a play is exactly the moment it needs a large
// answer. Reading queued_bytes told the gate nothing was outstanding thirty
// milliseconds into a second-and-a-half sound; impl-t7 counted 635 refills in
// one run of the game that way.
//
// Folding the voice's sound into queued_bytes instead of adding a query was
// tried and rejected: it took the headless intro from no dropouts to
// thirty-one silent gaps, because a ring writer then waits out a whole
// re-issued lap before appending.
// ---------------------------------------------------------------------------
static uint32_t g_gate_calls = 0;

static void test_qmixer_refill_gate() {
    cpu_reset();
    qmixer_reset();
    g_plays.clear();
    g_queues.clear();
    g_queue_enabled = true;
    g_gate_calls = 0;

    uint32_t hmix = call_shim(tramp("QMIXER.dll", "QSWaveMixInitEx"), {0});
    CHECK(hmix != 0);
    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixActivate"), {hmix, 1}), 0u);
    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixOpenChannel"), {hmix, 4, 2}), 0u);

    uint32_t wfx = sc(0xc00);
    gm_zero(wfx, SDK_WAVEFORMATEX);
    wr16(wfx + WFX_OFF_wFormatTag, WAVE_FORMAT_PCM);
    wr16(wfx + WFX_OFF_nChannels, 2);
    wr32(wfx + WFX_OFF_nSamplesPerSec, 11025);
    wr32(wfx + WFX_OFF_nAvgBytesPerSec, 11025 * 4);
    wr16(wfx + WFX_OFF_nBlockAlign, 4);
    wr16(wfx + WFX_OFF_wBitsPerSample, 16);

    static uint32_t cb = imports_alloc_trampoline(
        "TEST", "GateCallback",
        [](X86 *c) {
            uint32_t dst = arg(c, 0), bytes = arg(c, 1);
            for (uint32_t i = 0; i < bytes; ++i)
                wr8(dst + i, 0x60);
            ++g_gate_calls;
            set_eax(c, 1); // always more to come
        },
        3);

    const uint32_t kChunk = 0x3c00; // the size the game asks for
    uint32_t rec = sc(0x1800);
    gm_zero(rec, QSWAVEMIXOPENWAVEDATA_SIZE);
    wr32(rec + QSOWD_OFF_lpFormat, wfx);
    wr32(rec + QSOWD_OFF_lpData, kChunk);
    wr32(rec + QSOWD_OFF_pfnCallback, cb);
    wr32(rec + QSOWD_OFF_pvContext, 0xF00D0000u);

    uint32_t open_wave = tramp("QMIXER.dll", "QSWaveMixOpenWaveEx");
    uint32_t play = tramp("QMIXER.dll", "QSWaveMixPlayEx");

    uint32_t h1 = call_shim(open_wave, {hmix, rec, QSWAVEMIX_STREAMED | 1});
    CHECK(h1 != 0);
    CHECK_EQ(call_shim(play, {hmix, 2, 0x20, h1, 0, 0}), 0u);

    // The play primes two buffers: one submitted, one appended behind it. So
    // the voice has both in front of it and queued_bytes counts only the
    // appended one, because a play resets it and the submitted buffer is the
    // play. That difference of exactly one buffer is the defect: the gate
    // reading queued_bytes is told half of what is really ahead of the voice,
    // and on a channel whose whole sound arrived with the play it is told
    // nothing is ahead at all.
    CHECK_EQ(host_audio_voice_remaining_bytes(g_plays.back().channel), 2 * kChunk);
    CHECK_EQ(g_queued_bytes, kChunk);

    // So the pump leaves the guest alone while the voice is full.
    uint32_t settled = g_gate_calls;
    for (int i = 0; i < 20; ++i)
        qmixer_frame_pump(&g_cpu);
    CHECK_EQ(g_gate_calls, settled);

    // Drain it below a buffer and exactly one refill follows.
    g_voice_remaining = kChunk / 2;
    qmixer_frame_pump(&g_cpu);
    CHECK_EQ(g_gate_calls, settled + 1);

    // The knob puts the old gate back, and the old gate is the defect: with
    // the voice holding two buffers and nothing appended behind it,
    // queued_bytes reads zero and the pump refills a channel that needs
    // nothing. That is the difference the contract argument rests on, so it
    // is asserted here rather than only described.
    os_setenv("RECOMP_QMIX_GATE", "queue");
    qmixer_gate_reset_for_test();
    g_voice_remaining = 2 * kChunk;
    g_queued_bytes = 0;
    uint32_t old_gate = g_gate_calls;
    qmixer_frame_pump(&g_cpu);
    CHECK(g_gate_calls > old_gate);
    os_unsetenv("RECOMP_QMIX_GATE");
    qmixer_gate_reset_for_test();

    // A play that replaces a sound still playing reports the NEW sound's
    // length, not zero and not what was left of the old one.
    uint32_t h2 = call_shim(open_wave, {hmix, rec, QSWAVEMIX_STREAMED | 1});
    CHECK(h2 != 0);
    CHECK_EQ(call_shim(play, {hmix, 2, 0x20, h2, 0, 0}), 0u);
    // Its own two buffers, and nothing of what it replaced.
    CHECK_EQ(host_audio_voice_remaining_bytes(g_plays.back().channel),
             g_plays.back().bytes + kChunk);
    CHECK_EQ(g_plays.back().bytes, kChunk);
    settled = g_gate_calls;
    for (int i = 0; i < 20; ++i)
        qmixer_frame_pump(&g_cpu);
    CHECK_EQ(g_gate_calls, settled); // and is not refilled at once

    // Played through frame by frame, the voice never reaches zero: every
    // refill lands while a buffer is still in front of it. A dropout here is
    // a silent gap in the game.
    uint32_t dropouts = 0;
    const uint32_t heard = 11025 * 4 / 60 / 4 * 4; // a frame, whole samples
    for (int frame = 0; frame < 300; ++frame) {
        g_voice_remaining = g_voice_remaining > heard ? g_voice_remaining - heard : 0;
        qmixer_frame_pump(&g_cpu);
        if (g_voice_remaining == 0)
            ++dropouts;
    }
    CHECK_EQ(dropouts, 0u);

    g_queue_enabled = false;
    g_queued_bytes = 0;
    g_voice_remaining = 0;
    g_queues.clear();
    qmixer_reset();
}

// ---------------------------------------------------------------------------
// QMixer's volume scale, which is the library's and not a guess.
//
// QMixer.dll's parameter handler for the volume tag is three instructions:
//
//     18006d0b  fild  dword ptr [edi + 4]        ; the value the caller passed
//     18006d0e  fmul  dword ptr [0x180244d4]     ; 3.051851e-05, which is 1/32767
//     ...       fstp  dword ptr [esi + 0x50]     ; the channel's gain
//
// So it is a linear amplitude on 0 to 32767. The game passes 14190, which is a
// gain of 0.433 and about -7.3 dB; read as hundredths of a decibel, as it was,
// every positive number clamped to unity and every sound played at full
// volume with no mix at all.
// ---------------------------------------------------------------------------
static void test_qmixer_volume_scale() {
    cpu_reset();
    qmixer_reset();
    g_plays.clear();
    g_queue_enabled = false;

    uint32_t hmix = call_shim(tramp("QMIXER.dll", "QSWaveMixInitEx"), {0});
    CHECK(hmix != 0);
    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixActivate"), {hmix, 1}), 0u);

    uint32_t wfx = sc(0xc00);
    gm_zero(wfx, SDK_WAVEFORMATEX);
    wr16(wfx + WFX_OFF_wFormatTag, WAVE_FORMAT_PCM);
    wr16(wfx + WFX_OFF_nChannels, 1);
    wr32(wfx + WFX_OFF_nSamplesPerSec, 22050);
    wr32(wfx + WFX_OFF_nAvgBytesPerSec, 22050);
    wr16(wfx + WFX_OFF_nBlockAlign, 1);
    wr16(wfx + WFX_OFF_wBitsPerSample, 8);
    uint32_t data = sc(0x1000);
    for (uint32_t i = 0; i < 64; ++i)
        wr8(data + i, (uint8_t)i);
    uint32_t rec = sc(0x1800);
    gm_zero(rec, QSWAVEMIXOPENWAVEDATA_SIZE);
    wr32(rec + QSOWD_OFF_lpFormat, wfx);
    wr32(rec + QSOWD_OFF_lpData, data);
    wr32(rec + QSOWD_OFF_dwDataSize, 64);
    uint32_t hwave = call_shim(tramp("QMIXER.dll", "QSWaveMixOpenWaveEx"), {hmix, rec, 8});
    CHECK(hwave != 0);
    uint32_t set_vol = tramp("QMIXER.dll", "QSWaveMixSetVolume");
    uint32_t play = tramp("QMIXER.dll", "QSWaveMixPlayEx");

    struct Case {
        uint32_t qmix;
        int32_t millibels;
    };
    const Case cases[] = {
        {32767, 0},    // full scale
        {40000, 0},    // above it, still full scale
        {16384, -602}, // half amplitude, about -6 dB
        {14190, -727}, // what the game passes
        {3277, -2000}, // a tenth
        {1, -9031},    // the quietest the scale can express, short of zero
        {0, -10000},   // silence, which zero means on a linear scale
    };
    uint32_t ch = 0;
    for (const Case &t : cases) {
        CHECK_EQ(call_shim(set_vol, {hmix, ch, 0, t.qmix}), 0u);
        g_plays.clear();
        CHECK_EQ(call_shim(play, {hmix, ch, 0x20, hwave, 0, 0}), 0u);
        CHECK_EQ(g_plays.size(), 1u);
        if (g_plays.size() == 1)
            CHECK_EQ(g_plays[0].volume, t.millibels);
        ++ch;
    }

    // A channel nobody has set a volume on is at full scale, not silent: zero
    // is silence on this scale, so the default cannot be zero.
    g_plays.clear();
    CHECK_EQ(call_shim(play, {hmix, 20, 0x20, hwave, 0, 0}), 0u);
    CHECK_EQ(g_plays.size(), 1u);
    if (g_plays.size() == 1)
        CHECK_EQ(g_plays[0].volume, 0);

    qmixer_reset();
}

// ---------------------------------------------------------------------------
// A streamed wave is refilled without the game ever calling QSWaveMixPump.
//
// This is the shape of the bug that made the menu music play its opening
// second and stop. Real QMixer runs its own mixing thread, so QSWaveMixPump is
// for an application that wants to drive the mixer by hand and this game does
// not: zero calls in a whole run. Everything that refilled a streamed wave
// hung off that call, so nothing ever refilled one.
//
// The tick comes from qmixer_frame_pump now, which dx_register_shims hands to
// host_set_frame_pump and the guest's own message loop runs between frames.
// What is asserted here is the property that was missing: with QSWaveMixPump
// never called, the stream is still fed, and it is fed by appending rather
// than by starting the sound again.
// ---------------------------------------------------------------------------
static void test_qmixer_frame_pump() {
    cpu_reset();
    qmixer_reset();
    g_plays.clear();
    g_queues.clear();
    g_queued_bytes = 0;
    g_voice_remaining = 0;
    g_queued_accepted = 0;
    g_queue_enabled = true;
    g_ch_streaming = false;
    g_test_audio_pos = 0;

    uint32_t hmix = call_shim(tramp("QMIXER.dll", "QSWaveMixInitEx"), {0});
    CHECK(hmix != 0);
    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixActivate"), {hmix, 1}), 0u);
    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixOpenChannel"), {hmix, 15, 2}), 0u);

    // 11025 Hz stereo 16-bit, which is what the menu music actually is.
    uint32_t wfx = sc(0xc00);
    gm_zero(wfx, SDK_WAVEFORMATEX);
    wr16(wfx + WFX_OFF_wFormatTag, WAVE_FORMAT_PCM);
    wr16(wfx + WFX_OFF_nChannels, 2);
    wr32(wfx + WFX_OFF_nSamplesPerSec, 11025);
    wr32(wfx + WFX_OFF_nAvgBytesPerSec, 11025 * 4);
    wr16(wfx + WFX_OFF_nBlockAlign, 4);
    wr16(wfx + WFX_OFF_wBitsPerSample, 16);

    static uint32_t g_fp_calls = 0;
    static uint8_t g_fp_fill = 0x10;
    g_fp_calls = 0;
    g_fp_fill = 0x10;
    static uint32_t cb = imports_alloc_trampoline(
        "TEST", "FramePumpCallback",
        [](X86 *c) {
            uint32_t dst = arg(c, 0), bytes = arg(c, 1);
            for (uint32_t i = 0; i < bytes; ++i)
                wr8(dst + i, g_fp_fill);
            ++g_fp_calls;
            set_eax(c, 1); // non-zero: more will follow
        },
        3);

    const uint32_t kChunk = 0x400;
    uint32_t rec = sc(0x1800);
    gm_zero(rec, QSWAVEMIXOPENWAVEDATA_SIZE);
    wr32(rec + QSOWD_OFF_lpFormat, wfx);
    wr32(rec + QSOWD_OFF_lpData, kChunk);
    wr32(rec + QSOWD_OFF_pfnCallback, cb);
    wr32(rec + QSOWD_OFF_pvContext, 0xBEEF0000u);
    uint32_t hwave =
        call_shim(tramp("QMIXER.dll", "QSWaveMixOpenWaveEx"), {hmix, rec, QSWAVEMIX_STREAMED | 1});
    CHECK(hwave != 0);

    // Play. One host play to start the sound, and the channel becomes a stream
    // on the same call: until it is one the host counts the buffer that
    // started it as the whole sound and retires the voice when it runs out.
    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixPlayEx"), {hmix, 0, 0x20, hwave, 0, 0}), 0u);
    CHECK_EQ(g_plays.size(), 1u);
    if (g_plays.size() == 1) {
        CHECK_EQ(g_plays[0].rate, 11025);
        CHECK_EQ(g_plays[0].channels, 2);
        CHECK_EQ(g_plays[0].bits, 16);
        CHECK_EQ(g_plays[0].loop, 0);
    }
    CHECK(g_ch_streaming);

    // Now play it, without ever calling QSWaveMixPump. The frame pump is what
    // the message loop runs, so this is that loop.
    g_plays.clear();
    g_queues.clear();
    uint32_t before_calls = g_fp_calls;
    g_fp_fill = 0x20;
    for (int frame = 0; frame < 40; ++frame) {
        // A frame's worth of audio has been heard since the last one. Both
        // counters fall: the voice has that much less of its sound left, and
        // the queue behind it that much less appended. The refill gate reads
        // the voice, so a test that drained only the queue would have the
        // gate looking at a number nothing ever moved.
        uint32_t heard = 11025 * 4 / 60;
        heard -= heard % 4;
        g_queued_bytes = g_queued_bytes > heard ? g_queued_bytes - heard : 0;
        g_voice_remaining = g_voice_remaining > heard ? g_voice_remaining - heard : 0;
        qmixer_frame_pump(&g_cpu);
    }

    CHECK(g_fp_calls > before_calls); // the guest was asked for more
    CHECK(!g_queues.empty());         // and it reached the host
    CHECK_EQ(g_plays.size(), 0u);     // without restarting the sound
    bool right = !g_queues.empty();
    for (const PlayRecord &q : g_queues) {
        if (q.bytes % kChunk != 0)
            right = false;
        for (uint8_t b : q.pcm)
            if (b != 0x20)
                right = false;
    }
    CHECK(right); // the callback's bytes, unaltered

    // And it does not pull every frame: with the queue still deep enough the
    // pump leaves the guest alone.
    g_queued_bytes = 8 * kChunk;
    g_voice_remaining = 8 * kChunk;
    uint32_t settled = g_fp_calls;
    for (int frame = 0; frame < 10; ++frame)
        qmixer_frame_pump(&g_cpu);
    CHECK_EQ(g_fp_calls, settled);

    // The frame pump is re-entrant-safe: the callback it ends in is guest code
    // and guest code reaches the message loop.
    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixStopChannel"), {hmix, 0, 0}), 0u);
    g_plays.clear();
    g_queues.clear();
    for (int frame = 0; frame < 5; ++frame)
        qmixer_frame_pump(&g_cpu);
    CHECK_EQ(g_queues.size(), 0u); // a stopped channel is left alone
    CHECK_EQ(g_plays.size(), 0u);

    g_queue_enabled = false;
    g_queued_bytes = 0;
    g_voice_remaining = 0;
    g_queued_accepted = 0;
    g_queues.clear();
    g_ch_streaming = false;
    qmixer_reset();
}

// ---------------------------------------------------------------------------
// QMixer channel management, against what the game actually calls.
//
// Two things here were wrong in a way no test would have caught, because both
// were guesses about an SDK whose header this project does not have, and the
// evidence for both is the game's own calls: QSWaveMixOpenChannel(hMix, 15, 2)
// followed by plays on channel 0, and QSWaveMixEnableChannel(hMix, 0, 8, 0)
// followed immediately by a play on channel 0 that has to be heard.
//
// QMixer.dll settles the first: its OpenChannel switches on the third argument
// with `cmp eax,3 / ja` and a four-entry jump table, so 2 is one of four modes
// and, given the game then uses channels 0 upwards, it is a count.
// ---------------------------------------------------------------------------
static void test_qmixer_channels() {
    cpu_reset();
    qmixer_reset();
    g_plays.clear();
    g_queue_enabled = false;

    uint32_t hmix = call_shim(tramp("QMIXER.dll", "QSWaveMixInitEx"), {0});
    CHECK(hmix != 0);
    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixActivate"), {hmix, 1}), 0u);

    // A static wave, the kind a sound effect is.
    uint32_t wfx = sc(0xc00);
    gm_zero(wfx, SDK_WAVEFORMATEX);
    wr16(wfx + WFX_OFF_wFormatTag, WAVE_FORMAT_PCM);
    wr16(wfx + WFX_OFF_nChannels, 1);
    wr32(wfx + WFX_OFF_nSamplesPerSec, 22050);
    wr32(wfx + WFX_OFF_nAvgBytesPerSec, 22050);
    wr16(wfx + WFX_OFF_nBlockAlign, 1);
    wr16(wfx + WFX_OFF_wBitsPerSample, 8);
    uint32_t data = sc(0x1000);
    for (uint32_t i = 0; i < 256; ++i)
        wr8(data + i, (uint8_t)(i ^ 0x5a));
    uint32_t rec = sc(0x1800);
    gm_zero(rec, QSWAVEMIXOPENWAVEDATA_SIZE);
    wr32(rec + QSOWD_OFF_lpFormat, wfx);
    wr32(rec + QSOWD_OFF_lpData, data);
    wr32(rec + QSOWD_OFF_dwDataSize, 256);
    uint32_t hwave = call_shim(tramp("QMIXER.dll", "QSWaveMixOpenWaveEx"), {hmix, rec, 8});
    CHECK(hwave != 0);

    // The game's own call: fifteen channels, not channel fifteen.
    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixOpenChannel"), {hmix, 15, 2}), 0u);

    // EnableChannel with a zero fourth argument, then a play on the same
    // channel. The play has to be heard: the game does exactly this and every
    // sound on the channel was being dropped.
    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixEnableChannel"), {hmix, 0, 8, 0}), 0u);
    uint32_t play = tramp("QMIXER.dll", "QSWaveMixPlayEx");
    g_plays.clear();
    CHECK_EQ(call_shim(play, {hmix, 0, 0x20, hwave, 0, 0}), 0u);
    CHECK_EQ(g_plays.size(), 1u);
    if (g_plays.size() == 1) {
        CHECK_EQ(g_plays[0].bits, 8);
        CHECK_EQ(g_plays[0].channels, 1);
        CHECK_EQ(g_plays[0].bytes, 256u);
    }

    // Every one of the fifteen sounds, not just the one that was opened by
    // index. Each gets its own host voice so they can sound at once.
    g_plays.clear();
    for (uint32_t ch = 1; ch < 15; ++ch)
        CHECK_EQ(call_shim(play, {hmix, ch, 0x20, hwave, 0, 0}), 0u);
    CHECK_EQ(g_plays.size(), 14u);
    bool distinct = true;
    for (size_t i = 1; i < g_plays.size(); ++i)
        if (g_plays[i].channel == g_plays[i - 1].channel)
            distinct = false;
    CHECK(distinct);

    // A paused channel is reused by the game without RestartChannel: it
    // pauses, then later SetVolume, ConfigureChannel, EnableChannel and PlayEx
    // on the same channel (traced from the front end, channel 1 at 37.7 s and
    // 38.3 s). The play has to be heard; dropping it as "channel paused" lost
    // menu clicks and unit sounds.
    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixPauseChannel"), {hmix, 1, 0}), 0u);
    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixEnableChannel"), {hmix, 1, 8, 0x11}), 0u);
    g_plays.clear();
    CHECK_EQ(call_shim(play, {hmix, 1, 0x20, hwave, 0, 0}), 0u);
    CHECK_EQ(g_plays.size(), 1u);

    // Opening all of them is the other documented mode.
    qmixer_reset();
    hmix = call_shim(tramp("QMIXER.dll", "QSWaveMixInitEx"), {0});
    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixActivate"), {hmix, 1}), 0u);
    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixOpenChannel"), {hmix, 0, 1}), 0u);

    // A channel index past the end of the table is still refused rather than
    // growing it without bound.
    hwave = call_shim(tramp("QMIXER.dll", "QSWaveMixOpenWaveEx"), {hmix, rec, 8});
    CHECK(hwave != 0);
    g_plays.clear();
    CHECK(call_shim(play, {hmix, 4096, 0x20, hwave, 0, 0}) != 0u);
    CHECK_EQ(g_plays.size(), 0u);

    qmixer_reset();
}

// ---------------------------------------------------------------------------
// Formats, end to end. Whatever the guest declared in its WAVEFORMATEX is what
// the host is told, and the bytes are handed over untouched - 8-bit PCM is
// unsigned and centred on 0x80, 16-bit is signed little-endian, and a mono
// buffer stays mono rather than being widened here. All four combinations are
// checked because a shim that gets the sample width right and the channel
// count wrong plays at half speed, and one that gets the sign wrong plays a
// buzz, and both arrive as "garbled".
//
// GetCaps is checked alongside them because the game reads its own buffer size
// back from it rather than remembering it: 0057e1e0 creates the streaming
// buffer, calls GetCaps, stores dwBufferBytes in its own record, and falls
// back to 0x8000 only when there is no device at all.
// ---------------------------------------------------------------------------
static void test_dsound_formats() {
    cpu_reset();
    g_plays.clear();
    g_queue_enabled = false;
    g_test_audio_pos = 0;
    uint32_t ds = make_dsound();

    struct Case {
        uint32_t chans, rate, bits, bytes, align;
    };
    const Case cases[] = {
        {1, 11025, 8, 256, 1},   // 8-bit mono
        {2, 22050, 8, 512, 2},   // 8-bit stereo
        {1, 22050, 16, 512, 2},  // 16-bit mono
        {2, 22050, 16, 1024, 4}, // 16-bit stereo, the game's own format
    };
    uint32_t slot = 0x600;
    for (const Case &t : cases) {
        uint32_t buf = make_buffer(ds, t.chans, t.rate, t.bits, t.bytes, sc(slot));
        slot += 4;
        CHECK(buf != 0);

        // A pattern that is wrong in a visibly different way for every
        // mistake: the two channels differ and consecutive frames differ.
        std::vector<uint8_t> want(t.bytes);
        CHECK_EQ(
            call_method(buf, B_Lock, {0, t.bytes, sc(0x300), sc(0x304), sc(0x308), sc(0x30c), 0}),
            DS_OK);
        uint32_t p = rd32(sc(0x300));
        CHECK_EQ(rd32(sc(0x304)), t.bytes);
        uint32_t frames = t.bytes / t.align;
        for (uint32_t f = 0; f < frames; ++f) {
            for (uint32_t ch = 0; ch < t.chans; ++ch) {
                if (t.bits == 8) {
                    // Unsigned, silence at 0x80, a different level per side.
                    want[f * t.align + ch] = (uint8_t)(0x80 + (int)(f % 100) - 50 + (int)ch * 3);
                } else {
                    int16_t v = (int16_t)((int)(f % 200) * 150 - 15000 + (int)ch * 7);
                    want[f * t.align + ch * 2 + 0] = (uint8_t)(v & 0xff);
                    want[f * t.align + ch * 2 + 1] = (uint8_t)((v >> 8) & 0xff);
                }
            }
        }
        for (uint32_t i = 0; i < t.bytes; ++i)
            wr8(p + i, want[i]);
        CHECK_EQ(call_method(buf, B_Unlock, {p, t.bytes, 0, 0}), DS_OK);

        g_plays.clear();
        CHECK_EQ(call_method(buf, B_Play, {0, 0, 0}), DS_OK);
        CHECK_EQ(g_plays.size(), 1u);
        if (g_plays.size() == 1) {
            const PlayRecord &r = g_plays[0];
            CHECK_EQ((uint32_t)r.rate, t.rate);
            CHECK_EQ((uint32_t)r.channels, t.chans);
            CHECK_EQ((uint32_t)r.bits, t.bits);
            CHECK_EQ(r.bytes, t.bytes);
            CHECK(r.pcm == want); // byte for byte, no conversion here
        }

        // GetFormat reports what it was created with, block align included.
        uint32_t got = sc(0x400);
        gm_zero(got, 32);
        CHECK_EQ(call_method(buf, B_GetFormat, {got, 18, sc(0x420)}), DS_OK);
        CHECK_EQ(rd16(got + WFX_OFF_wFormatTag), WAVE_FORMAT_PCM);
        CHECK_EQ((uint32_t)rd16(got + WFX_OFF_nChannels), t.chans);
        CHECK_EQ(rd32(got + WFX_OFF_nSamplesPerSec), t.rate);
        CHECK_EQ((uint32_t)rd16(got + WFX_OFF_wBitsPerSample), t.bits);
        CHECK_EQ((uint32_t)rd16(got + WFX_OFF_nBlockAlign), t.align);
        CHECK_EQ(rd32(got + WFX_OFF_nAvgBytesPerSec), t.rate * t.align);

        // GetCaps reports the size the game reads its own buffer length from.
        uint32_t caps = sc(0x440);
        gm_zero(caps, 24);
        wr32(caps, 20);
        CHECK_EQ(call_method(buf, B_GetCaps, {caps}), DS_OK);
        CHECK_EQ(rd32(caps + 8), t.bytes);
        CHECK_EQ(call_method(buf, B_Stop, {}), DS_OK);
    }

    // A QMixer wave carries its own WAVEFORMATEX and is handed over the same
    // way, so the same matrix goes through that path too.
    qmixer_reset();
    uint32_t hmix = call_shim(tramp("QMIXER.dll", "QSWaveMixInitEx"), {0});
    CHECK(hmix != 0);
    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixActivate"), {hmix, 1}), 0u);
    uint32_t open_wave = tramp("QMIXER.dll", "QSWaveMixOpenWaveEx");
    uint32_t play = tramp("QMIXER.dll", "QSWaveMixPlayEx");
    uint32_t ch = 0;
    for (const Case &t : cases) {
        uint32_t wfx = sc(0xc00);
        gm_zero(wfx, SDK_WAVEFORMATEX);
        wr16(wfx + WFX_OFF_wFormatTag, WAVE_FORMAT_PCM);
        wr16(wfx + WFX_OFF_nChannels, (uint16_t)t.chans);
        wr32(wfx + WFX_OFF_nSamplesPerSec, t.rate);
        wr32(wfx + WFX_OFF_nAvgBytesPerSec, t.rate * t.align);
        wr16(wfx + WFX_OFF_nBlockAlign, (uint16_t)t.align);
        wr16(wfx + WFX_OFF_wBitsPerSample, (uint16_t)t.bits);
        // Well clear of everything else in the scratch block: the largest
        // case here is a kilobyte of samples.
        uint32_t data = sc(0x1000);
        std::vector<uint8_t> want(t.bytes);
        for (uint32_t i = 0; i < t.bytes; ++i) {
            want[i] = (uint8_t)(i * 13 + t.bits);
            wr8(data + i, want[i]);
        }
        uint32_t rec = sc(0x1800);
        gm_zero(rec, QSWAVEMIXOPENWAVEDATA_SIZE);
        wr32(rec + QSOWD_OFF_lpFormat, wfx);
        wr32(rec + QSOWD_OFF_lpData, data);
        wr32(rec + QSOWD_OFF_dwDataSize, t.bytes);
        uint32_t hwave = call_shim(open_wave, {hmix, rec, 8});
        CHECK(hwave != 0);
        CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixOpenChannel"), {hmix, ch, 0}), 0u);
        g_plays.clear();
        CHECK_EQ(call_shim(play, {hmix, ch, 0, hwave, 0, 0}), 0u);
        CHECK_EQ(g_plays.size(), 1u);
        if (g_plays.size() == 1) {
            CHECK_EQ((uint32_t)g_plays[0].rate, t.rate);
            CHECK_EQ((uint32_t)g_plays[0].channels, t.chans);
            CHECK_EQ((uint32_t)g_plays[0].bits, t.bits);
            CHECK(g_plays[0].pcm == want);
        }
        ++ch;
    }
    qmixer_reset();
}

// ---------------------------------------------------------------------------
// Streaming into a looping DirectSound buffer, which is how the game plays its
// music and speech. The numbers are the original's, from the Wine trace of
// buffer 0120FDF8: 32768 bytes, 22050 Hz stereo 16-bit, Play(0, 0,
// DSBPLAY_LOOPING) while the ring is still empty, then a thread polling
// GetCurrentPosition every ten milliseconds and locking three or four
// kilobytes at a running offset - 0, 3584, 7056, 10640 - about a lap ahead of
// the play cursor. No notification positions anywhere on that path.
//
// What is asserted is that the ring stops being a loop the moment the guest
// writes into it: the samples are appended, the voice is not restarted, and
// the play cursor the guest polls follows what the host has actually heard.
// ---------------------------------------------------------------------------
static void test_dsound_stream() {
    cpu_reset();
    qmixer_reset();
    g_plays.clear();
    g_queues.clear();
    g_queued_bytes = 0;
    g_voice_remaining = 0;
    g_queue_enabled = true;
    g_test_audio_pos = 0;

    uint32_t ds = make_dsound();
    const uint32_t kRing = 32768;
    uint32_t buf = make_buffer(ds, 2, 22050, 16, kRing, sc(4));
    CHECK(buf != 0);
    CHECK_EQ(call_method(buf, B_SetFrequency, {22050}), DS_OK);
    CHECK_EQ(call_method(buf, B_SetVolume, {0}), DS_OK);

    // Play finds the ring empty, exactly as the original does, and the host is
    // handed a loop because nothing has written into it yet.
    CHECK_EQ(call_method(buf, B_Play, {0, 0, DSBPLAY_LOOPING}), DS_OK);
    CHECK_EQ(g_plays.size(), 1u);
    if (g_plays.size() == 1) {
        CHECK_EQ(g_plays[0].loop, 1);
        CHECK_EQ(g_plays[0].bytes, kRing);
        CHECK_EQ(g_plays[0].rate, 22050);
        CHECK_EQ(g_plays[0].channels, 2);
        CHECK_EQ(g_plays[0].bits, 16);
        CHECK_EQ(g_plays[0].start_offset, 0u);
    }

    // The original's first poll before its first lock: playpos 3760, and a
    // write cursor ten milliseconds ahead of it.
    g_test_audio_pos = 3760;
    CHECK_EQ(call_method(buf, B_GetCurrentPosition, {sc(0x530), sc(0x534)}), DS_OK);
    CHECK_EQ(rd32(sc(0x530)), 3760u);
    CHECK_EQ(rd32(sc(0x534)), 3760u + 880u);

    // The first refill. The ring becomes a stream: the rest of the lap is
    // re-issued as a one-shot from the live cursor and the guest's bytes are
    // appended behind it.
    g_plays.clear();
    g_queues.clear();
    CHECK_EQ(call_method(buf, B_Lock, {0, 3584, sc(0x300), sc(0x304), sc(0x308), sc(0x30c), 0}),
             DS_OK);
    uint32_t p1 = rd32(sc(0x300));
    CHECK_EQ(rd32(sc(0x304)), 3584u);
    std::vector<uint8_t> chunk(3584);
    for (uint32_t i = 0; i < 3584; ++i) {
        chunk[i] = (uint8_t)(i * 7 + 1);
        wr8(p1 + i, chunk[i]);
    }
    CHECK_EQ(call_method(buf, B_Unlock, {p1, 3584, 0, 0}), DS_OK);

    // The conversion is not a play: host_audio_stream turns the loop into a
    // stream at the cursor, so nothing is stopped and nothing restarts.
    CHECK_EQ(g_plays.size(), 0u);
    CHECK_EQ(g_stream_base, 3760u);
    CHECK_EQ(g_queues.size(), 1u);
    if (g_queues.size() == 1) {
        CHECK_EQ(g_queues[0].bytes, 3584u);
        CHECK(g_queues[0].pcm == chunk); // the guest's bytes, unaltered
    }

    // The cursor the guest polls is the host's own count of what it has
    // played, and it has not moved: nothing has been heard since the last look.
    CHECK_EQ(call_method(buf, B_GetCurrentPosition, {sc(0x530), sc(0x534)}), DS_OK);
    CHECK_EQ(rd32(sc(0x530)), 3760u);

    // Every refill after that is an append and nothing else. The offsets are
    // the original's.
    const uint32_t offs[] = {3584, 7056, 10640};
    const uint32_t lens[] = {3472, 3584, 3472};
    const uint32_t poll[] = {8464, 12228, 15052};
    for (int i = 0; i < 3; ++i) {
        g_plays.clear();
        g_queues.clear();
        g_stream_played = poll[i] - 3760;
        CHECK_EQ(call_method(buf, B_Lock,
                             {offs[i], lens[i], sc(0x300), sc(0x304), sc(0x308), sc(0x30c), 0}),
                 DS_OK);
        uint32_t q = rd32(sc(0x300));
        for (uint32_t k = 0; k < lens[i]; ++k)
            wr8(q + k, (uint8_t)(0x20 + i));
        CHECK_EQ(call_method(buf, B_Unlock, {q, lens[i], 0, 0}), DS_OK);
        CHECK_EQ(g_plays.size(), 0u); // the voice is never restarted
        CHECK_EQ(g_queues.size(), 1u);
        if (g_queues.size() == 1)
            CHECK_EQ(g_queues[0].bytes, lens[i]);
        // and the cursor keeps tracking what the host has consumed.
        CHECK_EQ(call_method(buf, B_GetCurrentPosition, {sc(0x530), sc(0x534)}), DS_OK);
        CHECK_EQ(rd32(sc(0x530)), poll[i]);
    }

    // A refill that runs off the end of the ring is two appends, because a run
    // that crosses the end of a ring is two runs in play order. This one also
    // jumps rather than continuing where the last left off, which is the
    // resync path: the shim says so once and follows the guest.
    g_plays.clear();
    g_queues.clear();
    CHECK_EQ(call_method(buf, B_Lock,
                         {kRing - 1024, 2048, sc(0x300), sc(0x304), sc(0x308), sc(0x30c), 0}),
             DS_OK);
    uint32_t w1 = rd32(sc(0x300)), w2 = rd32(sc(0x308));
    CHECK_EQ(rd32(sc(0x304)), 1024u);
    CHECK_EQ(rd32(sc(0x30c)), 1024u);
    for (uint32_t k = 0; k < 1024; ++k) {
        wr8(w1 + k, 0xE1);
        wr8(w2 + k, 0xE2);
    }
    CHECK_EQ(call_method(buf, B_Unlock, {w1, 1024, w2, 1024}), DS_OK);
    CHECK_EQ(g_plays.size(), 0u);
    CHECK_EQ(g_queues.size(), 2u);
    if (g_queues.size() == 2) {
        CHECK_EQ(g_queues[0].bytes, 1024u);
        CHECK_EQ(g_queues[1].bytes, 1024u);
        CHECK_EQ(g_queues[0].pcm[0], 0xE1);
        CHECK_EQ(g_queues[1].pcm[0], 0xE2);
    }

    // A host that refuses an append outright, with no stream to convert to
    // either, is the only case that goes back to re-submitting. The contract
    // says a stream that has momentarily run dry still accepts, so a refusal
    // here means the voice is gone rather than behind.
    // Stop ends the stream, and the Play after it is a loop again until the
    // guest writes: a new sound is not a continuation of the last one.
    g_plays.clear();
    g_queues.clear();
    CHECK_EQ(call_method(buf, B_Stop, {}), DS_OK);
    CHECK_EQ(call_method(buf, B_Play, {0, 0, DSBPLAY_LOOPING}), DS_OK);
    CHECK_EQ(g_plays.size(), 1u);
    if (g_plays.size() == 1)
        CHECK_EQ(g_plays[0].loop, 1);
    CHECK_EQ(g_queues.size(), 0u);

    // On a host that cannot continue a sound the ring stays a loop and the
    // refill is a re-submission: audible, but it plays.
    g_queue_enabled = false;
    g_queued_bytes = 0;
    g_voice_remaining = 0;
    g_plays.clear();
    g_queues.clear();
    g_test_audio_pos = 2048;
    CHECK_EQ(call_method(buf, B_Lock, {0, 1024, sc(0x300), sc(0x304), sc(0x308), sc(0x30c), 0}),
             DS_OK);
    uint32_t z = rd32(sc(0x300));
    for (uint32_t k = 0; k < 1024; ++k)
        wr8(z + k, 0x77);
    CHECK_EQ(call_method(buf, B_Unlock, {z, 1024, 0, 0}), DS_OK);
    CHECK_EQ(g_queues.size(), 0u);
    // One play: host_audio_stream says no without changing anything, so the
    // ring is never converted and the refill is a re-submission from the live
    // cursor. The buffer is marked, so it is asked once rather than at every
    // refill.
    CHECK_EQ(g_plays.size(), 1u);
    if (g_plays.size() == 1) {
        CHECK_EQ(g_plays[0].loop, 1);             // still a loop
        CHECK_EQ(g_plays[0].start_offset, 2048u); // from the live cursor
        CHECK_EQ(g_plays[0].bytes, kRing);
    }

    // Asked once: the next refill re-submits the loop and nothing else.
    g_plays.clear();
    g_test_audio_pos = 4096;
    CHECK_EQ(call_method(buf, B_Lock, {1024, 1024, sc(0x300), sc(0x304), sc(0x308), sc(0x30c), 0}),
             DS_OK);
    uint32_t z2 = rd32(sc(0x300));
    for (uint32_t k = 0; k < 1024; ++k)
        wr8(z2 + k, 0x78);
    CHECK_EQ(call_method(buf, B_Unlock, {z2, 1024, 0, 0}), DS_OK);
    CHECK_EQ(g_plays.size(), 1u);
    if (g_plays.size() == 1) {
        CHECK_EQ(g_plays[0].loop, 1);
        CHECK_EQ(g_plays[0].start_offset, 4096u);
    }

    CHECK_EQ(call_method(buf, B_Stop, {}), DS_OK);
    g_test_audio_pos = 0;
    g_queued_bytes = 0;
    g_voice_remaining = 0;
    g_queues.clear();
}

// ---------------------------------------------------------------------------
// DirectSound notification positions. The game streams its music by parking a
// worker thread on two events registered with IDirectSoundNotify and refilling
// whichever half of a looping buffer the play cursor has just left, so a shim
// that never signals them never gets a second half of anything. This drives
// the play cursor by hand and asserts the events fire exactly when the cursor
// reaches their offsets, including across the loop point.
// ---------------------------------------------------------------------------
static void test_dsound_notify() {
    cpu_reset();
    qmixer_reset();
    g_plays.clear();
    g_test_audio_pos = 0;

    uint32_t ds = make_dsound();
    CHECK(ds != 0);
    const uint32_t kBytes = 1024, kHalf = 512;
    uint32_t buf = make_buffer(ds, 2, 22050, 16, kBytes, sc(4));
    CHECK(buf != 0);

    // IID_IDirectSoundNotify, which the game asks the buffer for.
    static const uint8_t IID_NOTIFY[16] = {0x83, 0x07, 0x21, 0xB0, 0xCD, 0x89, 0xD0, 0x11,
                                           0xAF, 0x08, 0x00, 0xA0, 0xC9, 0x25, 0xCD, 0x16};
    uint32_t iid = sc(0x500);
    for (uint32_t i = 0; i < 16; ++i)
        wr8(iid + i, IID_NOTIFY[i]);
    CHECK_EQ(call_method(buf, B_QueryInterface, {iid, sc(0x510)}), DS_OK);
    uint32_t notify = rd32(sc(0x510));
    CHECK(notify != 0);
    CHECK(notify != buf); // a separate view on the same buffer

    // Two manual-reset events, as the game creates them.
    uint32_t createev = tramp("KERNEL32.dll", "CreateEventA");
    uint32_t reset = tramp("KERNEL32.dll", "ResetEvent");
    uint32_t wait = tramp("KERNEL32.dll", "WaitForSingleObject");
    uint32_t ev0 = call_shim(createev, {0, 1, 0, 0});
    uint32_t ev1 = call_shim(createev, {0, 1, 0, 0});
    CHECK(ev0 != 0);
    CHECK(ev1 != 0);
    CHECK(ev0 != ev1);

    // DSBPOSITIONNOTIFY is {dwOffset, hEventNotify}: the start of the buffer
    // and its half-way point, which is the pair the game registers.
    uint32_t list = sc(0x520);
    wr32(list + 0, 0);
    wr32(list + 4, ev0);
    wr32(list + 8, kHalf);
    wr32(list + 12, ev1);
    CHECK_EQ(call_method(notify, N_SetNotificationPositions, {2, list}), DS_OK);

    uint32_t pump = tramp("QMIXER.dll", "QSWaveMixPump");
    CHECK_EQ(call_method(buf, B_Play, {0, 0, DSBPLAY_LOOPING}), DS_OK);

    // Nothing has moved yet, so nothing has been reached.
    CHECK_EQ(call_shim(pump, {}), 0u);
    CHECK_EQ(call_shim(wait, {ev0, 0}), 0x102u);
    CHECK_EQ(call_shim(wait, {ev1, 0}), 0x102u);

    // Part way into the first half: still nothing.
    g_test_audio_pos = kHalf - 4;
    CHECK_EQ(call_shim(pump, {}), 0u);
    CHECK_EQ(call_shim(wait, {ev0, 0}), 0x102u);
    CHECK_EQ(call_shim(wait, {ev1, 0}), 0x102u);

    // Reaching the half-way point signals that position and only that one.
    g_test_audio_pos = kHalf;
    CHECK_EQ(call_shim(pump, {}), 0u);
    CHECK_EQ(call_shim(wait, {ev1, 0}), 0u);
    CHECK_EQ(call_shim(wait, {ev0, 0}), 0x102u);
    call_shim(reset, {ev1});

    // The cursor wraps. Offset 0 is reached only by wrapping, and that is
    // exactly when the game refills the second half.
    g_test_audio_pos = 8;
    CHECK_EQ(call_shim(pump, {}), 0u);
    CHECK_EQ(call_shim(wait, {ev0, 0}), 0u);
    CHECK_EQ(call_shim(wait, {ev1, 0}), 0x102u);
    call_shim(reset, {ev0});

    // A second lap signals the half-way point again rather than firing once
    // for the life of the buffer.
    g_test_audio_pos = kHalf + 16;
    CHECK_EQ(call_shim(pump, {}), 0u);
    CHECK_EQ(call_shim(wait, {ev1, 0}), 0u);
    CHECK_EQ(call_shim(wait, {ev0, 0}), 0x102u);
    call_shim(reset, {ev1});

    // GetCurrentPosition reports the same cursor the notifications are
    // measured against.
    CHECK_EQ(call_method(buf, B_GetCurrentPosition, {sc(0x530), sc(0x534)}), DS_OK);
    CHECK_EQ(rd32(sc(0x530)), kHalf + 16u);

    // A refill through Lock/Unlock resubmits from where the cursor actually
    // is, not from the top of the buffer: a stream fed from offset 0 on every
    // refill is the sound of this going wrong. This test host cannot continue
    // a sound, so host_audio_stream refuses without changing anything and the
    // ring stays a loop.
    g_plays.clear();
    CHECK_EQ(call_method(buf, B_Lock, {0, kHalf, sc(0x300), sc(0x304), sc(0x308), sc(0x30c), 0}),
             DS_OK);
    uint32_t p1 = rd32(sc(0x300));
    for (uint32_t i = 0; i < kHalf; ++i)
        wr8(p1 + i, (uint8_t)(i & 0xff));
    CHECK_EQ(call_method(buf, B_Unlock, {p1, kHalf, 0, 0}), DS_OK);
    CHECK_EQ(g_plays.size(), 1u);
    if (g_plays.size() == 1) {
        CHECK_EQ(g_plays[0].start_offset, kHalf + 16u);
        CHECK_EQ(g_plays[0].loop, 1);
    }

    // DSBPN_OFFSETSTOP is reported when the buffer stops.
    uint32_t evstop = call_shim(createev, {0, 1, 0, 0});
    CHECK_EQ(call_method(buf, B_Stop, {}), DS_OK);
    wr32(list + 0, 0xffffffffu);
    wr32(list + 4, evstop);
    wr32(list + 8, kHalf);
    wr32(list + 12, ev1);
    CHECK_EQ(call_method(notify, N_SetNotificationPositions, {2, list}), DS_OK);
    CHECK_EQ(call_method(buf, B_Play, {0, 0, DSBPLAY_LOOPING}), DS_OK);
    CHECK_EQ(call_shim(wait, {evstop, 0}), 0x102u);
    CHECK_EQ(call_method(buf, B_Stop, {}), DS_OK);
    CHECK_EQ(call_shim(wait, {evstop, 0}), 0u);

    // Positions cannot be changed under a playing buffer, and one past the end
    // of the buffer is refused rather than kept where it could never fire.
    CHECK_EQ(call_method(buf, B_Play, {0, 0, DSBPLAY_LOOPING}), DS_OK);
    CHECK_EQ(call_method(notify, N_SetNotificationPositions, {2, list}), DSERR_INVALIDCALL);
    CHECK_EQ(call_method(buf, B_Stop, {}), DS_OK);
    wr32(list + 0, kBytes);
    CHECK_EQ(call_method(notify, N_SetNotificationPositions, {2, list}), DS_OK);
    g_test_audio_pos = 0;

    // Clearing the list stops everything firing.
    CHECK_EQ(call_method(notify, N_SetNotificationPositions, {0, 0}), DS_OK);
    CHECK_EQ(call_method(buf, B_Play, {0, 0, DSBPLAY_LOOPING}), DS_OK);
    g_test_audio_pos = kHalf;
    CHECK_EQ(call_shim(pump, {}), 0u);
    CHECK_EQ(call_shim(wait, {ev1, 0}), 0x102u);
    CHECK_EQ(call_method(buf, B_Stop, {}), DS_OK);
    g_test_audio_pos = 0;
}

// ---------------------------------------------------------------------------
// QMixer streamed waves. The samples arrive through a guest callback that the
// pump calls, so the test supplies a real guest callback and asserts that the
// bytes it writes are the bytes the host is asked to play, that the pump pulls
// again when the previous buffer has been heard, and that a callback which
// reports it is finished ends the sound instead of repeating it.
// ---------------------------------------------------------------------------
static uint32_t g_stream_calls = 0;
static uint32_t g_stream_limit = 0; // calls before the source runs dry
static uint8_t g_stream_fill = 0;   // the byte the callback writes
static uint32_t g_stream_ctx_seen = 0;
static uint32_t g_stream_size_seen = 0;

static void test_qmixer_streaming() {
    cpu_reset();
    g_plays.clear();
    g_stops.clear();
    qmixer_reset();

    uint32_t init = tramp("QMIXER.dll", "QSWaveMixInitEx");
    uint32_t initdata = sc(0x100);
    gm_zero(initdata, 0x40);
    wr32(initdata + 0, 0x40);
    wr32(initdata + 8, 0x5622);
    uint32_t hmix = call_shim(init, {initdata});
    CHECK(hmix != 0);
    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixActivate"), {hmix, 1}), 0u);
    CHECK_EQ(call_shim(tramp("QMIXER.dll", "QSWaveMixOpenChannel"), {hmix, 1, 2}), 0u);

    // 22050 Hz, stereo, 16-bit: the format the game's music path uses.
    uint32_t wfx = sc(0x200);
    gm_zero(wfx, SDK_WAVEFORMATEX);
    wr16(wfx + 0x00, 1); // WAVE_FORMAT_PCM
    wr16(wfx + 0x02, 2); // channels
    wr32(wfx + 0x04, 22050);
    wr32(wfx + 0x08, 22050 * 4);
    wr16(wfx + 0x0c, 4);
    wr16(wfx + 0x0e, 16);
    wr16(wfx + 0x10, 0);

    // The guest callback, with the game's own convention: it fills the buffer
    // it is given and returns non-zero while more will follow, and zero on the
    // chunk that is the last one. The game's wrapper at 0x576660 is stdcall
    // with (buffer, bytes, context), and its caller at 0056f090 treats a zero
    // result as end-of-stream while still playing the chunk it just got.
    static uint32_t cb = imports_alloc_trampoline(
        "TEST", "StreamCallback",
        [](X86 *c) {
            uint32_t dst = arg(c, 0), bytes = arg(c, 1), ctx = arg(c, 2);
            g_stream_ctx_seen = ctx;
            g_stream_size_seen = bytes;
            for (uint32_t i = 0; i < bytes; ++i)
                wr8(dst + i, g_stream_fill);
            ++g_stream_calls;
            set_eax(c, g_stream_calls >= g_stream_limit ? 0u : 1u);
        },
        3);

    // The record the game builds: format, chunk size, callback, context.
    const uint32_t kChunk = 0x400;
    uint32_t rec = sc(0x300);
    gm_zero(rec, QSWAVEMIXOPENWAVEDATA_SIZE);
    wr32(rec + QSOWD_OFF_lpFormat, wfx);
    wr32(rec + QSOWD_OFF_lpData, kChunk);
    wr32(rec + QSOWD_OFF_pfnCallback, cb);
    wr32(rec + QSOWD_OFF_pvContext, 0xC0FFEE00u);

    uint32_t open_wave = tramp("QMIXER.dll", "QSWaveMixOpenWaveEx");
    g_stream_calls = 0;
    g_stream_limit = 1000;
    g_stream_fill = 0x11;
    uint32_t hwave = call_shim(open_wave, {hmix, rec, QSWAVEMIX_STREAMED | 1});
    CHECK(hwave != 0);
    // Opening pulls nothing: the samples are wanted when the sound plays.
    CHECK_EQ(g_stream_calls, 0u);

    // Playing pulls the first buffer and hands it over immediately.
    uint32_t play = tramp("QMIXER.dll", "QSWaveMixPlayEx");
    g_plays.clear();
    CHECK_EQ(call_shim(play, {hmix, 1, 0, hwave, 0, 0}), 0u);
    CHECK_EQ(g_plays.size(), 1u);
    CHECK(g_stream_calls > 0);
    CHECK_EQ(g_stream_ctx_seen, 0xC0FFEE00u);
    CHECK_EQ(g_stream_size_seen, kChunk);
    uint32_t first_bytes = 0;
    if (g_plays.size() == 1) {
        const PlayRecord &r = g_plays[0];
        CHECK_EQ(r.rate, 22050);
        CHECK_EQ(r.channels, 2);
        CHECK_EQ(r.bits, 16);
        CHECK_EQ(r.loop, 0); // a stream is refilled, never host-looped
        CHECK(r.bytes >= kChunk);
        CHECK_EQ(r.bytes % kChunk, 0u);
        first_bytes = r.bytes;
        bool all = !r.pcm.empty();
        for (uint8_t b : r.pcm)
            if (b != 0x11)
                all = false;
        CHECK(all); // the callback's bytes, unaltered
    }

    // The pump does not refill while the buffer it handed over is still being
    // heard: the host copied those samples, so overwriting them early would
    // cut the sound short.
    uint32_t pump = tramp("QMIXER.dll", "QSWaveMixPump");
    g_test_audio_pos = 0;
    g_plays.clear();
    uint32_t before = g_stream_calls;
    CHECK_EQ(call_shim(pump, {}), 0u);
    CHECK_EQ(g_plays.size(), 0u);
    CHECK_EQ(g_stream_calls, before);

    // Once it has been heard, the pump pulls again and hands over the next
    // buffer, with the new samples.
    g_test_audio_pos = first_bytes;
    g_stream_fill = 0x22;
    CHECK_EQ(call_shim(pump, {}), 0u);
    CHECK_EQ(g_plays.size(), 1u);
    if (g_plays.size() == 1) {
        bool all = !g_plays[0].pcm.empty();
        for (uint8_t b : g_plays[0].pcm)
            if (b != 0x22)
                all = false;
        CHECK(all);
    }

    // The final chunk is played, not thrown away. The callback reports the end
    // by returning zero on the chunk it has just filled, so that chunk still
    // has to reach the host; only the pump after it has been heard stops the
    // channel.
    g_stream_limit = g_stream_calls + 1; // the very next call is the last one
    g_stream_fill = 0x33;
    g_plays.clear();
    g_test_audio_pos = first_bytes;
    CHECK_EQ(call_shim(pump, {}), 0u);
    CHECK_EQ(g_plays.size(), 1u);
    if (g_plays.size() == 1) {
        // Exactly one chunk: the pull stopped at the callback that said so.
        CHECK_EQ(g_plays[0].bytes, kChunk);
        bool all = !g_plays[0].pcm.empty();
        for (uint8_t b : g_plays[0].pcm)
            if (b != 0x33)
                all = false;
        CHECK(all);
    }
    g_plays.clear();
    g_test_audio_pos = kChunk;         // the last chunk has been heard
    CHECK_EQ(call_shim(pump, {}), 0u); // so the channel stops
    CHECK_EQ(g_plays.size(), 0u);
    g_plays.clear();
    CHECK_EQ(call_shim(pump, {}), 0u); // and stays stopped
    CHECK_EQ(g_plays.size(), 0u);

    // --- On a host that can continue a sound, a refill is queued behind what
    // is still playing instead of replacing it. That is the difference between
    // a stream and a seam at every chunk boundary, so it is asserted rather
    // than assumed: no new host_audio_play, and the queued bytes are the
    // callback's own.
    g_queue_enabled = true;
    g_queued_bytes = 0;
    g_voice_remaining = 0;
    g_queues.clear();
    g_plays.clear();
    g_stream_limit = 1000;
    g_stream_fill = 0x44;
    g_test_audio_pos = 0;
    CHECK_EQ(call_shim(play, {hmix, 1, 0, hwave, 0, 0}), 0u);
    CHECK_EQ(g_plays.size(), 1u); // the sound still starts with a play
    // and the queue behind it is filled on the same call. Waiting for a pump
    // would leave the first hand-over as the only one with nothing in front of
    // it, which is the one place a stream is late by construction.
    CHECK_EQ(g_queues.size(), 1u);
    if (g_queues.size() == 1) {
        bool all = !g_queues[0].pcm.empty();
        for (uint8_t b : g_queues[0].pcm)
            if (b != 0x44)
                all = false;
        CHECK(all);
    }

    // The pump continues it rather than restarting it. Once what was primed
    // has been heard, the next look tops it up again.
    g_plays.clear();
    g_queues.clear();
    g_queued_bytes = 0;
    g_voice_remaining = 0;
    g_stream_fill = 0x55;
    g_test_audio_pos = first_bytes;
    CHECK_EQ(call_shim(pump, {}), 0u);
    CHECK_EQ(g_plays.size(), 0u); // continued, not restarted
    CHECK_EQ(g_queues.size(), 1u);
    if (g_queues.size() == 1) {
        CHECK(g_queues[0].bytes > 0);
        bool all = true;
        for (uint8_t b : g_queues[0].pcm)
            if (b != 0x55)
                all = false;
        CHECK(all);
    }

    // And while a whole buffer is still waiting, the pump leaves it alone
    // rather than pulling from the guest every frame. The queue has to be
    // told it is full for this: the refill above put one chunk in, and the
    // threshold is one chunk, so the pump is entitled to top it up until the
    // host reports that much waiting.
    g_queued_bytes = g_queues.empty() ? 0 : g_queues[0].bytes;
    uint32_t calls_before = g_stream_calls;
    g_queues.clear();
    CHECK_EQ(call_shim(pump, {}), 0u);
    CHECK_EQ(g_queues.size(), 0u);
    CHECK_EQ(g_stream_calls, calls_before);
    CHECK_EQ(g_plays.size(), 0u);

    g_queue_enabled = false;
    g_queued_bytes = 0;
    g_voice_remaining = 0;
    g_queues.clear();
    qmixer_reset();
    g_plays.clear();

    // A record with no callback is refused rather than opened silent.
    wr32(rec + QSOWD_OFF_pfnCallback, 0);
    CHECK_EQ(call_shim(open_wave, {hmix, rec, QSWAVEMIX_STREAMED}), 0u);
    wr32(rec + QSOWD_OFF_pfnCallback, cb);
    // So is an impossible chunk size.
    wr32(rec + QSOWD_OFF_lpData, 0);
    CHECK_EQ(call_shim(open_wave, {hmix, rec, QSWAVEMIX_STREAMED}), 0u);
    wr32(rec + QSOWD_OFF_lpData, 0x40000000u);
    CHECK_EQ(call_shim(open_wave, {hmix, rec, QSWAVEMIX_STREAMED}), 0u);

    qmixer_reset();
    g_test_audio_pos = 0;
}

// ---------------------------------------------------------------------------
// The original fills its textures by blitting into them, not by Load: the
// Wine trace has 2303 Blt and 3108 BltFast against zero
// IDirect3DTexture2::Load. A blit into a texture surface must therefore reach
// the renderer exactly as an Unlock does. A texture that is only ever written
// by blit and never re-uploaded draws as untextured geometry.
//
// The descriptors here are the ones the trace actually shows, including the
// complex single-level mipmap form that 1072 of the original's 1455
// CreateSurface calls use.
// ---------------------------------------------------------------------------
static uint32_t make_texture(uint32_t dd, uint32_t caps, uint32_t w, uint32_t h, uint32_t pf_flags,
                             uint32_t bits, uint32_t r, uint32_t g, uint32_t b, uint32_t a,
                             bool mipmapcount, uint32_t out) {
    uint32_t desc = sc(0x200);
    gm_zero(desc, DDSD_SIZE);
    wr32(desc + DDSD_OFF_dwSize, DDSD_SIZE);
    uint32_t flags = DDSD_CAPS | DDSD_WIDTH | DDSD_HEIGHT | DDSD_PIXELFORMAT;
    if (mipmapcount)
        flags |= DDSD_MIPMAPCOUNT;
    wr32(desc + DDSD_OFF_dwFlags, flags);
    wr32(desc + DDSD_OFF_ddsCaps, caps);
    wr32(desc + DDSD_OFF_dwWidth, w);
    wr32(desc + DDSD_OFF_dwHeight, h);
    if (mipmapcount)
        wr32(desc + DDSD_OFF_dwMipMapCount, 1);
    uint32_t pf = desc + DDSD_OFF_ddpfPixelFormat;
    wr32(pf + DDPF_OFF_dwSize, DDPF_SIZE);
    wr32(pf + DDPF_OFF_dwFlags, pf_flags);
    wr32(pf + DDPF_OFF_dwRGBBitCount, bits);
    wr32(pf + DDPF_OFF_dwRBitMask, r);
    wr32(pf + DDPF_OFF_dwGBitMask, g);
    wr32(pf + DDPF_OFF_dwBBitMask, b);
    wr32(pf + DDPF_OFF_dwRGBAlphaBitMask, a);
    if (call_method(dd, DD_CreateSurface, {desc, out, 0}) != DD_OK)
        return 0;
    return rd32(out);
}

static uint32_t texture_handle_of(uint32_t dev, uint32_t surf, uint32_t out) {
    uint8_t tex2[16] = {0x02, 0x15, 0x28, 0x93, 0xF8, 0x8C, 0xD0, 0x11,
                        0x89, 0xAB, 0x00, 0xA0, 0xC9, 0x05, 0x41, 0x29};
    uint32_t iid = sc(0x40);
    for (int i = 0; i < 16; ++i)
        wr8(iid + (uint32_t)i, tex2[i]);
    if (call_method(surf, S_QueryInterface, {iid, out}) != S_OK)
        return 0;
    uint32_t tex = rd32(out);
    if (!tex)
        return 0;
    call_method(tex, TEX_GetHandle, {dev, sc(0x2c)});
    return tex;
}

static void test_blit_into_texture_uploads() {
    cpu_reset();
    g_uploads.clear();
    uint32_t create = tramp("DDRAW.dll", "DirectDrawCreate");
    call_shim(create, {0, sc(0), 0});
    uint32_t dd = rd32(sc(0));
    call_method(dd, DD_SetCooperativeLevel, {0x1234, DDSCL_EXCLUSIVE | DDSCL_FULLSCREEN});
    call_method(dd, DD_SetDisplayMode, {640, 480, 16});
    uint32_t dev = make_d3d_device();
    CHECK(dev != 0);

    // Every texture descriptor shape the trace shows, all 16-bit forms.
    struct Shape {
        const char *what;
        uint32_t caps;
        uint32_t w, h;
        uint32_t pff, bits, r, g, b, a;
        bool mip;
    };
    const Shape shapes[] = {
        {"complex mipmap 565 16x16",
         DDSCAPS_COMPLEX | DDSCAPS_TEXTURE | DDSCAPS_VIDEOMEMORY | DDSCAPS_MIPMAP, 16, 16, DDPF_RGB,
         16, 0xf800, 0x07e0, 0x001f, 0, true},
        {"complex mipmap 4444 32x32",
         DDSCAPS_COMPLEX | DDSCAPS_TEXTURE | DDSCAPS_VIDEOMEMORY | DDSCAPS_MIPMAP, 32, 32,
         DDPF_RGB | DDPF_ALPHAPIXELS, 16, 0x0f00, 0x00f0, 0x000f, 0xf000, true},
        {"sysmem 4444 32x32", DDSCAPS_SYSTEMMEMORY | DDSCAPS_TEXTURE, 32, 32,
         DDPF_RGB | DDPF_ALPHAPIXELS, 16, 0x0f00, 0x00f0, 0x000f, 0xf000, false},
        {"vidmem 565 128x128", DDSCAPS_TEXTURE | DDSCAPS_VIDEOMEMORY, 128, 128, DDPF_RGB, 16,
         0xf800, 0x07e0, 0x001f, 0, false},
        {"sysmem 1555 64x64", DDSCAPS_SYSTEMMEMORY | DDSCAPS_TEXTURE, 64, 64,
         DDPF_RGB | DDPF_ALPHAPIXELS, 16, 0x7c00, 0x03e0, 0x001f, 0x8000, false},
    };
    for (const Shape &sh : shapes) {
        uint32_t t = make_texture(dd, sh.caps, sh.w, sh.h, sh.pff, sh.bits, sh.r, sh.g, sh.b, sh.a,
                                  sh.mip, sc(0x10));
        CHECK(t != 0);
        if (!t)
            continue;
        CHECK(texture_handle_of(dev, t, sc(0x1c)) != 0);

        // A source of the same shape, filled with a recognisable value.
        uint32_t src = make_texture(dd, DDSCAPS_SYSTEMMEMORY | DDSCAPS_TEXTURE, sh.w, sh.h, sh.pff,
                                    sh.bits, sh.r, sh.g, sh.b, sh.a, false, sc(0x14));
        CHECK(src != 0);
        if (!src)
            continue;
        uint32_t ld = sc(0x300);
        gm_zero(ld, DDSD_SIZE);
        wr32(ld + DDSD_OFF_dwSize, DDSD_SIZE);
        CHECK_EQ(call_method(src, S_Lock, {0, ld, DDLOCK_WAIT, 0}), DD_OK);
        uint32_t bits = rd32(ld + DDSD_OFF_lpSurface);
        uint32_t pitch = rd32(ld + DDSD_OFF_lPitch);
        for (uint32_t y = 0; y < sh.h; ++y)
            for (uint32_t x = 0; x < sh.w; ++x)
                wr16(bits + y * pitch + x * 2, (uint16_t)(0x1234 + x + y));
        CHECK_EQ(call_method(src, S_Unlock, {0}), DD_OK);

        // Blt into the texture: the renderer must be given the new pixels.
        g_uploads.clear();
        CHECK_EQ(call_method(t, S_Blt, {0, src, 0, DDBLT_WAIT, 0}), DD_OK);
        CHECK_EQ(g_uploads.size(), 1u);
        if (g_uploads.size() == 1) {
            const TextureUpload &u = g_uploads[0];
            CHECK_EQ((uint32_t)u.width, sh.w);
            CHECK_EQ((uint32_t)u.height, sh.h);
            CHECK_EQ((uint32_t)u.bpp, sh.bits);
            CHECK_EQ(u.rmask, sh.r);
            CHECK_EQ(u.amask, sh.a);
            bool ok = u.pixels.size() >= (size_t)u.pitch * sh.h;
            if (ok)
                for (uint32_t y = 0; y < sh.h && ok; ++y)
                    for (uint32_t x = 0; x < sh.w; ++x) {
                        const uint8_t *q = u.pixels.data() + (size_t)y * u.pitch + x * 2;
                        if ((uint16_t)(q[0] | (q[1] << 8)) != (uint16_t)(0x1234 + x + y)) {
                            ok = false;
                            break;
                        }
                    }
            CHECK(ok);
        }

        // BltFast too: the original uses it more than Blt.
        g_uploads.clear();
        CHECK_EQ(call_method(t, S_BltFast, {0, 0, src, 0, DDBLTFAST_WAIT}), DD_OK);
        CHECK_EQ(g_uploads.size(), 1u);
    }

    // A colour-keyed blit into a texture uploads too: the keyed pixels are the
    // ones left alone, and the rest still have to reach the renderer.
    uint32_t dst = make_texture(dd, DDSCAPS_TEXTURE | DDSCAPS_VIDEOMEMORY, 4, 4, DDPF_RGB, 16,
                                0xf800, 0x07e0, 0x001f, 0, false, sc(0x10));
    uint32_t src = make_texture(dd, DDSCAPS_SYSTEMMEMORY | DDSCAPS_TEXTURE, 4, 4, DDPF_RGB, 16,
                                0xf800, 0x07e0, 0x001f, 0, false, sc(0x14));
    CHECK(dst != 0 && src != 0);
    CHECK(texture_handle_of(dev, dst, sc(0x1c)) != 0);
    uint32_t ck = sc(0x500);
    wr32(ck + DDCK_OFF_lo, 0x0000);
    wr32(ck + DDCK_OFF_hi, 0x0000);
    CHECK_EQ(call_method(src, S_SetColorKey, {DDCKEY_SRCBLT, ck}), DD_OK);
    uint32_t ld = sc(0x300);
    gm_zero(ld, DDSD_SIZE);
    wr32(ld + DDSD_OFF_dwSize, DDSD_SIZE);
    CHECK_EQ(call_method(src, S_Lock, {0, ld, DDLOCK_WAIT, 0}), DD_OK);
    uint32_t sb = rd32(ld + DDSD_OFF_lpSurface), sp = rd32(ld + DDSD_OFF_lPitch);
    for (uint32_t y = 0; y < 4; ++y)
        for (uint32_t x = 0; x < 4; ++x)
            wr16(sb + y * sp + x * 2, (uint16_t)((x == 0) ? 0x0000 : 0xBEEF));
    CHECK_EQ(call_method(src, S_Unlock, {0}), DD_OK);
    g_uploads.clear();
    CHECK_EQ(call_method(dst, S_BltFast, {0, 0, src, 0, DDBLTFAST_WAIT | DDBLTFAST_SRCCOLORKEY}),
             DD_OK);
    CHECK_EQ(g_uploads.size(), 1u);
    if (g_uploads.size() == 1) {
        const TextureUpload &u = g_uploads[0];
        bool ok = u.pixels.size() >= (size_t)u.pitch * 4;
        // Column 0 was keyed out and kept whatever was there; the rest copied.
        for (uint32_t y = 0; y < 4 && ok; ++y)
            for (uint32_t x = 1; x < 4; ++x) {
                const uint8_t *q = u.pixels.data() + (size_t)y * u.pitch + x * 2;
                if ((uint16_t)(q[0] | (q[1] << 8)) != 0xBEEF) {
                    ok = false;
                    break;
                }
            }
        CHECK(ok);
    }

    // An 8-bit texture written by blit resolves through the palette attached
    // to it, not just when it is written by Unlock. The original creates one
    // palettised texture and fills it the same way it fills the others.
    uint32_t p8 = make_texture(dd, DDSCAPS_SYSTEMMEMORY | DDSCAPS_TEXTURE, 4, 4,
                               DDPF_PALETTEINDEXED8 | DDPF_RGB, 8, 0, 0, 0, 0, false, sc(0x10));
    uint32_t p8src = make_texture(dd, DDSCAPS_SYSTEMMEMORY | DDSCAPS_TEXTURE, 4, 4,
                                  DDPF_PALETTEINDEXED8 | DDPF_RGB, 8, 0, 0, 0, 0, false, sc(0x14));
    CHECK(p8 != 0 && p8src != 0);
    uint32_t ents = sc(0x800);
    gm_zero(ents, 256 * 4);
    wr32(ents + 5 * 4, 0x00FF8040u); // PALETTEENTRY is R,G,B,flags
    CHECK_EQ(
        call_method(dd, DD_CreatePalette, {DDPCAPS_8BIT | DDPCAPS_ALLOW256, ents, sc(0x18), 0}),
        DD_OK);
    uint32_t p8pal = rd32(sc(0x18));
    CHECK(p8pal != 0);
    CHECK_EQ(call_method(p8, S_SetPalette, {p8pal}), DD_OK);
    CHECK(texture_handle_of(dev, p8, sc(0x1c)) != 0);
    gm_zero(ld, DDSD_SIZE);
    wr32(ld + DDSD_OFF_dwSize, DDSD_SIZE);
    CHECK_EQ(call_method(p8src, S_Lock, {0, ld, DDLOCK_WAIT, 0}), DD_OK);
    uint32_t pb = rd32(ld + DDSD_OFF_lpSurface), pp = rd32(ld + DDSD_OFF_lPitch);
    for (uint32_t y = 0; y < 4; ++y)
        for (uint32_t x = 0; x < 4; ++x)
            wr8(pb + y * pp + x, 5);
    CHECK_EQ(call_method(p8src, S_Unlock, {0}), DD_OK);
    g_uploads.clear();
    CHECK_EQ(call_method(p8, S_Blt, {0, p8src, 0, DDBLT_WAIT, 0}), DD_OK);
    CHECK_EQ(g_uploads.size(), 1u);
    if (g_uploads.size() == 1) {
        const TextureUpload &u = g_uploads[0];
        CHECK_EQ(u.bpp, 8);
        CHECK(u.has_palette);
        CHECK_EQ(u.pixels[0], 5u);                     // the index blitted in
        CHECK_EQ(u.palette[5] & 0xffffffu, 0x4080FFu); // and the colour it means
    }

    // A texture filled before anything asks for its handle is still uploaded
    // with those pixels rather than empty: the write marks it, GetHandle sends
    // it. The original calls GetHandle 2242 times for far fewer surfaces.
    uint32_t late = make_texture(dd, DDSCAPS_TEXTURE | DDSCAPS_VIDEOMEMORY, 4, 4, DDPF_RGB, 16,
                                 0xf800, 0x07e0, 0x001f, 0, false, sc(0x10));
    CHECK(late != 0);
    g_uploads.clear();
    CHECK_EQ(call_method(late, S_BltFast, {0, 0, src, 0, DDBLTFAST_WAIT}), DD_OK);
    CHECK_EQ(g_uploads.size(), 0u); // no handle yet, nothing to upload to
    CHECK(texture_handle_of(dev, late, sc(0x1c)) != 0);
    CHECK_EQ(g_uploads.size(), 1u); // and the handle brings the pixels
    if (g_uploads.size() == 1) {
        const uint8_t *q = g_uploads[0].pixels.data() + 2;
        CHECK_EQ((uint32_t)(uint16_t)(q[0] | (q[1] << 8)), 0xBEEFu);
    }
}

// D3D11 ABI tests use SDK slot numbers and byte offsets independently of
// the implementation. Calls use the same 32-bit stack as translated clients.
static void test_d3d11_scaffold() {
    cpu_reset();
    uint32_t create = tramp("d3d11.dll", "D3D11CreateDeviceAndSwapChain");
    CHECK(create != 0);
    CHECK(imports_has_dll("dxgi.dll"));
    CHECK(tramp("d3dcompiler_47.dll", "D3DCompile") != 0);
    CHECK(tramp("d3dx10_41.dll", "D3DXMatrixMultiply") != 0);
    if (!create)
        return;
    gm_zero(sc(0), 1024);
    uint32_t desc = sc(0x100);
    wr32(desc, 4);
    wr32(desc + 4, 4);
    wr32(desc + 16, 28);
    wr32(desc + 28, 1);
    wr32(desc + 36, 0x20);
    wr32(desc + 40, 2);
    wr32(desc + 48, 1);
    wr32(desc + 52, 1);
    CHECK_EQ(call_shim(create, {0, 1, 0, 0, 0, 0, 7, desc, sc(0), sc(4), sc(8), sc(12)}), S_OK);
    uint32_t swap = rd32(sc(0)), dev = rd32(sc(4)), ctx = rd32(sc(12));
    CHECK(swap && dev && ctx);
    if (!swap || !dev || !ctx)
        return;
    CHECK_EQ(rd32(sc(8)), 0xa000);
    // ID3D11Texture2D: 6f15aaf2-d208-4e89-9ab4-489535d34f9c.
    const uint8_t iid[] = {0xf2, 0xaa, 0x15, 0x6f, 0x08, 0xd2, 0x89, 0x4e,
                           0x9a, 0xb4, 0x48, 0x95, 0x35, 0xd3, 0x4f, 0x9c};
    memcpy(gm_ptr(sc(0x80)), iid, 16);
    CHECK_EQ(call_method(swap, 9, {0, sc(0x80), sc(16)}), S_OK);
    uint32_t back = rd32(sc(16));
    CHECK(back);
    if (!back)
        return;
    CHECK_EQ(call_method(dev, 9, {back, 0, sc(20)}), S_OK);
    uint32_t view = rd32(sc(20));
    CHECK(view);
    if (!view)
        return;
    float colour[] = {1, 0.5f, 0, 1};
    memcpy(gm_ptr(sc(0x200)), colour, 16);
    call_method(ctx, 50, {view, sc(0x200)});
    size_t before = g_presents.size();
    CHECK_EQ(call_method(swap, 8, {0, 0}), S_OK);
    CHECK_EQ(g_presents.size(), before + 1);
    if (g_presents.size() > before) {
        const auto &p = g_presents.back();
        CHECK_EQ(p.w, 4);
        CHECK_EQ(p.h, 4);
        CHECK_EQ(p.bpp, 32);
        uint32_t pixel = 0;
        memcpy(&pixel, p.pixels.data(), 4);
        CHECK_EQ(pixel, 0xffff8000u); // host ARGB, resource RGBA
    }
    // Optional IDXGIDevice query is explicitly tolerated by clients.
    gm_zero(sc(0x80), 16);
    wr32(sc(0x80), 0x54ec77fa);
    CHECK_EQ(call_method(dev, 0, {sc(0x80), sc(24)}), E_NOINTERFACE);
    CHECK_EQ(rd32(sc(24)), 0);
    CHECK_EQ(call_method(dev, 38, {}), E_NOTIMPL);           // GetCreationFlags: complete vtable
    CHECK_EQ(call_method(ctx, 114, {0, sc(24)}), E_NOTIMPL); // FinishCommandList
    call_method(view, 2);
    call_method(back, 2);
    call_method(ctx, 2);
    call_method(dev, 2);
    call_method(swap, 2);
}

static uint32_t matrix_call(const char *name, std::initializer_list<uint32_t> args) {
    uint32_t target = tramp("d3dx10_41.dll", name);
    CHECK(target != 0);
    if (!target)
        return 0;
    uint32_t sp = g_cpu.r[R_ESP];
    std::vector<uint32_t> v(args);
    for (auto it = v.rbegin(); it != v.rend(); ++it) {
        g_cpu.r[R_ESP] -= 4;
        wr32(g_cpu.r[R_ESP], *it);
    }
    g_cpu.r[R_ESP] -= 4;
    wr32(g_cpu.r[R_ESP], 0x00401000);
    CHECK(imports_dispatch(&g_cpu, target));
    CHECK_EQ(g_cpu.r[R_ESP], sp); // stdcall: the SDK and Delphi callers require callee cleanup
    g_cpu.r[R_ESP] = sp;
    return g_cpu.r[R_EAX];
}
static uint32_t float_word(float v) {
    uint32_t w;
    memcpy(&w, &v, 4);
    return w;
}
static void test_d3dx_math_and_blob() {
    cpu_reset();
    if (!tramp("d3dx10_41.dll", "D3DXMatrixTranslation")) {
        CHECK(false);
        return;
    }
    CHECK_EQ(
        matrix_call("D3DXMatrixTranslation", {sc(0), float_word(3), float_word(4), float_word(5)}),
        sc(0));
    CHECK_EQ(
        matrix_call("D3DXMatrixScaling", {sc(64), float_word(2), float_word(3), float_word(4)}),
        sc(64));
    CHECK_EQ(matrix_call("D3DXMatrixMultiply", {sc(128), sc(0), sc(64)}), sc(128));
    const float expected[16] = {2, 0, 0, 0, 0, 3, 0, 0, 0, 0, 4, 0, 6, 12, 20, 1};
    CHECK(memcmp(gm_ptr(sc(128)), expected, 64) == 0);
    matrix_call("D3DXMatrixMultiplyTranspose", {sc(0), sc(0), sc(64)}); // aliased out
    for (unsigned r = 0; r < 4; ++r)
        for (unsigned col = 0; col < 4; ++col)
            CHECK_EQ(rd32(sc(0) + (r * 4 + col) * 4), float_word(expected[col * 4 + r]));
    gm_put_str(sc(256), "unknown source", 64);
    gm_put_str(sc(320), "VSEntry", 32);
    gm_put_str(sc(352), "vs_4_0", 32);
    CHECK_EQ(call_shim(tramp("d3dcompiler_47.dll", "D3DCompile"),
                       {sc(256), 14, 0, 0, 0, sc(320), sc(352), 0, 0, sc(384), sc(388)}),
             S_OK);
    uint32_t blob = rd32(sc(384));
    CHECK(blob);
    CHECK_EQ(rd32(sc(388)), 0);
    if (blob) {
        uint32_t data = call_method(blob, 3), size = call_method(blob, 4);
        CHECK(data != 0);
        CHECK(size >= 32);
        CHECK_EQ(rd32(data), 0x31425352u);
        // The tag: FNV-1a over the exact source bytes, then the names given.
        uint64_t hash = 14695981039346656037ull;
        for (const char *p = "unknown source"; *p; ++p) {
            hash ^= uint8_t(*p);
            hash *= 1099511628211ull;
        }
        dx11::ShaderTag tag{};
        memcpy(&tag, gm_ptr(data), sizeof tag);
        CHECK_EQ(tag.hash, hash);
        CHECK(strcmp(tag.entry, "VSEntry") == 0);
        CHECK(strcmp(tag.target, "vs_4_0") == 0);
        call_method(blob, 2);
    }
}

// This mesh and its matrix products reproduce the published 2D API contract,
// including the UV Y flip and the R16 shader's 32/64 divisors (not 31/63).
static void test_d3d11_quad(bool alpha) {
    cpu_reset();
    gm_zero(sc(0), 0x4000);
    uint32_t live = com_live_count();
    uint32_t sd = sc(0x100);
    wr32(sd, 8);
    wr32(sd + 4, 8);
    wr32(sd + 16, 28);
    wr32(sd + 28, 1);
    wr32(sd + 36, 0x20);
    wr32(sd + 40, 2);
    wr32(sd + 48, 1);
    CHECK_EQ(call_shim(tramp("d3d11.dll", "D3D11CreateDeviceAndSwapChain"),
                       {0, 1, 0, 0, 0, 0, 7, sd, sc(0), sc(4), sc(8), sc(12)}),
             S_OK);
    uint32_t swap = rd32(sc(0)), dev = rd32(sc(4)), ctx = rd32(sc(12));
    if (!swap || !dev || !ctx)
        return;
    std::vector<uint32_t> owned{swap, dev, ctx};
    auto cleanup = [&]() {
        call_method(ctx, 110);
        for (auto it = owned.rbegin(); it != owned.rend(); ++it)
            call_method(*it, 2);
    };
    const uint8_t iid[] = {0xf2, 0xaa, 0x15, 0x6f, 0x08, 0xd2, 0x89, 0x4e,
                           0x9a, 0xb4, 0x48, 0x95, 0x35, 0xd3, 0x4f, 0x9c};
    memcpy(gm_ptr(sc(0x80)), iid, 16);
    call_method(swap, 9, {0, sc(0x80), sc(16)});
    uint32_t back = rd32(sc(16));
    owned.push_back(back);
    call_method(dev, 9, {back, 0, sc(20)});
    uint32_t rtv = rd32(sc(20));
    owned.push_back(rtv);
    float blue[] = {0, 0, 1, 1};
    memcpy(gm_ptr(sc(0x200)), blue, 16);
    call_method(ctx, 50, {rtv, sc(0x200)});
    call_method(ctx, 33, {1, sc(20), 0});
    float vp[] = {0, 0, 8, 8, 0, 1};
    memcpy(gm_ptr(sc(0x220)), vp, 24);
    call_method(ctx, 44, {1, sc(0x220)});
    wr32(sc(0x240), 1);
    call_method(ctx, 95, {sc(0x240), sc(0x260)});
    CHECK(memcmp(gm_ptr(sc(0x260)), vp, 24) == 0);
    call_method(ctx, 3, {sc(24)});
    CHECK_EQ(rd32(sc(24)), dev);
    call_method(rd32(sc(24)), 2);
    auto buffer = [&](uint32_t bytes, uint32_t bind, const void *data) {
        uint32_t d = sc(0x300);
        gm_zero(d, 24);
        wr32(d, bytes);
        wr32(d + 4, 2);
        wr32(d + 8, bind);
        wr32(d + 12, 0x10000);
        wr32(sc(28), 0);
        CHECK_EQ(call_method(dev, 3, {d, 0, sc(28)}), S_OK);
        uint32_t b = rd32(sc(28));
        if (!b)
            return uint32_t(0);
        owned.push_back(b);
        CHECK_EQ(call_method(ctx, 14, {b, 0, 4, 0, sc(0x340)}), S_OK);
        uint32_t ptr = rd32(sc(0x340));
        CHECK(ptr != 0);
        if (ptr && data)
            memcpy(gm_ptr(ptr), data, bytes);
        call_method(ctx, 15, {b, 0});
        return b;
    };
    float vertices[] = {-1, -1, 0, 0, 0, 1, 1, 0, 1, 1, -1, 1, 0, 0, 1, 1, -1, 0, 1, 0};
    uint16_t indices[] = {0, 2, 1, 0, 1, 3, 0};
    uint32_t vb = buffer(sizeof(vertices), 1, vertices), ib = buffer(sizeof(indices), 2, indices);
    if (!vb || !ib) {
        cleanup();
        return;
    }
    // SetDestRect(2,1,6,5) in an 8x8 viewport, using the original D3DX sequence.
    matrix_call("D3DXMatrixTranslation", {sc(0x400), float_word(-0.5f), float_word(-0.25f), 0});
    matrix_call("D3DXMatrixScaling",
                {sc(0x440), float_word(0.5f), float_word(0.5f), float_word(1)});
    matrix_call("D3DXMatrixTranslation", {sc(0x480), float_word(1), float_word(1), 0});
    matrix_call("D3DXMatrixMultiply", {sc(0x4c0), sc(0x480), sc(0x440)});
    matrix_call("D3DXMatrixMultiplyTranspose", {sc(0x500), sc(0x4c0), sc(0x400)});
    matrix_call("D3DXMatrixScaling", {sc(0x540), float_word(1), float_word(1), float_word(1)});
    uint32_t cb = buffer(128, 4, gm_ptr(sc(0x500)));
    if (!cb) {
        cleanup();
        return;
    }
    // A shader from its tagged blob, written at `at`.
    auto shader = [&](const QuadShader &q, uint32_t at) {
        dx11::ShaderTag tag{};
        tag.magic = 0x31425352;
        tag.version = 1;
        tag.hash = q.hash;
        snprintf(tag.entry, sizeof tag.entry, "%s", q.entry);
        snprintf(tag.target, sizeof tag.target, "%s", q.target);
        memcpy(gm_ptr(at), &tag, sizeof tag);
        wr32(sc(0x1888), 0);
        CHECK_EQ(call_method(dev, q.vertex ? 12 : 15, {at, uint32_t(sizeof tag), 0, sc(0x1888)}),
                 S_OK);
        uint32_t sh = rd32(sc(0x1888));
        if (sh)
            owned.push_back(sh);
        return sh;
    };
    uint32_t vs = shader(quad_vertex_shader, sc(0x1000));
    // The VS tag stays where it is for CreateInputLayout.
    const uint32_t vsdata = sc(0x1000), vssize = sizeof(dx11::ShaderTag);
    uint32_t ps = shader(alpha ? quad_fragment_shader : quad_fragment_shader_R16_int, sc(0x1080));
    if (!vs || !ps) {
        cleanup();
        return;
    }
    gm_zero(sc(0x600), 56);
    gm_put_str(sc(0x680), "POSITION", 32);
    gm_put_str(sc(0x6a0), "TEXCOORD", 32);
    wr32(sc(0x600), sc(0x680));
    wr32(sc(0x608), 6);
    wr32(sc(0x61c), sc(0x6a0));
    wr32(sc(0x624), 16);
    wr32(sc(0x62c), 0xffffffff);
    CHECK_EQ(call_method(dev, 11, {sc(0x600), 2, vsdata, vssize, sc(32)}), S_OK);
    uint32_t layout = rd32(sc(32));
    owned.push_back(layout);
    uint32_t td = sc(0x700);
    gm_zero(td, 44);
    wr32(td, 4);
    wr32(td + 4, 4);
    wr32(td + 12, 1);
    wr32(td + 16, alpha ? 28 : 56);
    wr32(td + 20, 1);
    wr32(td + 32, 0x28);
    wr32(td + 40, 1);
    CHECK_EQ(call_method(dev, 5, {td, 0, sc(36)}), S_OK);
    uint32_t tex = rd32(sc(36));
    owned.push_back(tex);
    if (!tex) {
        cleanup();
        return;
    }
    gm_zero(sc(0x740), 24);
    wr32(sc(0x740), alpha ? 28 : 56);
    wr32(sc(0x744), 4);
    wr32(sc(0x74c), 1);
    CHECK_EQ(call_method(dev, 7, {tex, sc(0x740), sc(40)}), S_OK);
    uint32_t srv = rd32(sc(40));
    owned.push_back(srv);
    // Padded rows ensure UpdateSubresource honours source row pitch.
    const uint16_t colours[] = {0xf800, 0x07e0, 0x001f, 0xffff, 0x0000, 0x1234, 0xabcd, 0xf81f,
                                0x5678, 0x7bef, 0x0400, 0x8410, 0xffe0, 0x07ff, 0x780f, 0x0010};
    for (unsigned y = 0; y < 4; ++y)
        for (unsigned x = 0; x < 4; ++x) {
            if (alpha)
                wr32(sc(0x800) + y * 24 + x * 4, 0x800000ffu);
            else
                wr16(sc(0x800) + y * 16 + x * 2, colours[y * 4 + x]);
        }
    call_method(ctx, 48, {tex, 0, 0, sc(0x800), alpha ? 24u : 16u, 0});
    gm_zero(sc(0x900), 52);
    wr32(sc(0x900), 0x15); // linear, same-size draw samples texel centres
    for (unsigned i = 1; i <= 3; ++i)
        wr32(sc(0x900) + i * 4, 1);
    wr32(sc(0x918), 8);
    CHECK_EQ(call_method(dev, 23, {sc(0x900), sc(44)}), S_OK);
    uint32_t sampler = rd32(sc(44));
    owned.push_back(sampler);
    uint32_t blend = 0;
    if (alpha) {
        gm_zero(sc(0xa00), 264);
        uint32_t d = sc(0xa08);
        wr32(d, 1);
        wr32(d + 4, 5);
        wr32(d + 8, 6);
        wr32(d + 12, 1);
        wr32(d + 16, 1);
        wr32(d + 20, 1);
        wr32(d + 24, 1);
        wr8(d + 28, 15);
        CHECK_EQ(call_method(dev, 20, {sc(0xa00), sc(48)}), S_OK);
        blend = rd32(sc(48));
        owned.push_back(blend);
    }
    call_method(ctx, 35, {blend, 0, 0xffffffff});
    call_method(ctx, 17, {layout});
    call_method(ctx, 11, {vs, 0, 0});
    call_method(ctx, 9, {ps, 0, 0});
    wr32(sc(52), vb);
    wr32(sc(56), 20);
    wr32(sc(60), 0);
    call_method(ctx, 18, {0, 1, sc(52), sc(56), sc(60)});
    call_method(ctx, 19, {ib, 57, 0});
    call_method(ctx, 24, {4});
    wr32(sc(64), cb);
    call_method(ctx, 7, {0, 1, sc(64)});
    call_method(ctx, 8, {0, 1, sc(40)});
    call_method(ctx, 10, {0, 1, sc(44)});
    call_method(ctx, 12, {6, 0, 0});
    call_method(swap, 8, {0, 0});
    const auto &p = g_presents.back();
    for (unsigned y = 0; y < 8; ++y)
        for (unsigned x = 0; x < 8; ++x) {
            uint32_t expected = 0xff0000ffu;
            if (x >= 2 && x < 6 && y >= 1 && y < 5) {
                if (alpha)
                    expected = 0xff80007fu;
                else {
                    uint16_t v = colours[(y - 1) * 4 + x - 2];
                    expected = 0xff000000u |
                               uint32_t(std::lround(((v >> 11) & 31) * 255.f / 32)) << 16 |
                               uint32_t(std::lround(((v >> 5) & 63) * 255.f / 64)) << 8 |
                               uint32_t(std::lround((v & 31) * 255.f / 32));
                }
            }
            uint32_t actual = 0;
            memcpy(&actual, p.pixels.data() + y * p.pitch + x * 4, 4);
            CHECK_EQ(actual, expected);
        }
    if (!alpha) {
        // A constant source coordinate at a four-texel junction distinguishes
        // linear filtering before packed decoding from decoding before filtering.
        matrix_call("D3DXMatrixScaling", {sc(0xc00), 0, 0, float_word(1)});
        matrix_call("D3DXMatrixTranslation", {sc(0xc40), float_word(0.5f), float_word(0.5f), 0});
        matrix_call("D3DXMatrixMultiplyTranspose", {sc(0xc80), sc(0xc00), sc(0xc40)});
        call_method(ctx, 14, {cb, 0, 4, 0, sc(0xd00)});
        uint32_t ptr = rd32(sc(0xd00));
        memcpy(gm_ptr(ptr), gm_ptr(sc(0x500)), 64);
        memcpy(gm_ptr(ptr + 64), gm_ptr(sc(0xc80)), 64);
        call_method(ctx, 15, {cb, 0});
        call_method(ctx, 12, {6, 0, 0});
        call_method(swap, 8, {0, 0});
        uint32_t colour = 0;
        const auto &linear = g_presents.back();
        memcpy(&colour, linear.pixels.data() + 2 * linear.pitch + 3 * 4, 4);
        CHECK_EQ(colour, 0xff48ebdfu); // mean packed word 20348, then R16 shader arithmetic
        // SetSourceRect(1,1,3,3) with point sampling crops and repeats a 2x2 area.
        matrix_call("D3DXMatrixScaling",
                    {sc(0xc00), float_word(0.5f), float_word(0.5f), float_word(1)});
        matrix_call("D3DXMatrixTranslation", {sc(0xc40), float_word(0.25f), float_word(0.25f), 0});
        matrix_call("D3DXMatrixMultiplyTranspose", {sc(0xc80), sc(0xc00), sc(0xc40)});
        call_method(ctx, 14, {cb, 0, 4, 0, sc(0xd00)});
        ptr = rd32(sc(0xd00));
        memcpy(gm_ptr(ptr), gm_ptr(sc(0x500)), 64);
        memcpy(gm_ptr(ptr + 64), gm_ptr(sc(0xc80)), 64);
        call_method(ctx, 15, {cb, 0});
        wr32(sc(0x900), 0);
        call_method(dev, 23, {sc(0x900), sc(72)});
        owned.push_back(rd32(sc(72)));
        call_method(ctx, 10, {0, 1, sc(72)});
        call_method(ctx, 12, {6, 0, 0});
        call_method(swap, 8, {0, 0});
        const auto &point = g_presents.back();
        for (unsigned y = 0; y < 4; ++y)
            for (unsigned x = 0; x < 4; ++x) {
                uint16_t v = colours[(1 + y / 2) * 4 + 1 + x / 2];
                uint32_t expected = 0xff000000u |
                                    uint32_t(std::lround(((v >> 11) & 31) * 255.f / 32)) << 16 |
                                    uint32_t(std::lround(((v >> 5) & 63) * 255.f / 64)) << 8 |
                                    uint32_t(std::lround((v & 31) * 255.f / 32));
                memcpy(&colour, point.pixels.data() + (y + 1) * point.pitch + (x + 2) * 4, 4);
                CHECK_EQ(colour, expected);
            }
    }
    // A different program cannot receive a plausible but wrong shader.
    wr32(vsdata + 8, rd32(vsdata + 8) ^ 1);
    wr32(sc(68), 0xdeadbeef);
    CHECK_EQ(call_method(dev, 12, {vsdata, vssize, 0, sc(68)}), E_FAIL);
    CHECK_EQ(rd32(sc(68)), 0);
    cleanup();
    CHECK_EQ(com_live_count(), live);
}
static void test_d3d11_quad_pixels() {
    test_d3d11_quad(false);
}
static void test_d3d11_alpha_pixels() {
    test_d3d11_quad(true);
}

// The texel-row copy in raster_triangle must produce the general loop's
// bytes. One scene, drawn without blending (the copy) and with a ONE/ZERO
// blend that changes nothing but forces the loop, then from a B8G8R8A8
// texture (the copy with a swizzle) and with a point sampler: four identical
// presents, all equal to the texels themselves.
static void test_d3d11_texel_copy(bool hardware) {
    cpu_reset();
    g_gpu2d_on = hardware;
    g_gpu2d_draws = g_gpu2d_uploads = g_gpu2d_readbacks = g_gpu2d_presents = 0;
    gm_zero(sc(0), 0x4000);
    uint32_t live = com_live_count();
    uint32_t sd = sc(0x100);
    wr32(sd, 8);
    wr32(sd + 4, 8);
    wr32(sd + 16, 28);
    wr32(sd + 28, 1);
    wr32(sd + 36, 0x20);
    wr32(sd + 40, 2);
    wr32(sd + 48, 1);
    CHECK_EQ(call_shim(tramp("d3d11.dll", "D3D11CreateDeviceAndSwapChain"),
                       {0, 1, 0, 0, 0, 0, 7, sd, sc(0), sc(4), sc(8), sc(12)}),
             S_OK);
    uint32_t swap = rd32(sc(0)), dev = rd32(sc(4)), ctx = rd32(sc(12));
    if (!swap || !dev || !ctx)
        return;
    std::vector<uint32_t> owned{swap, dev, ctx};
    auto cleanup = [&]() {
        call_method(ctx, 110);
        for (auto it = owned.rbegin(); it != owned.rend(); ++it)
            call_method(*it, 2);
    };
    const uint8_t iid[] = {0xf2, 0xaa, 0x15, 0x6f, 0x08, 0xd2, 0x89, 0x4e,
                           0x9a, 0xb4, 0x48, 0x95, 0x35, 0xd3, 0x4f, 0x9c};
    memcpy(gm_ptr(sc(0x80)), iid, 16);
    call_method(swap, 9, {0, sc(0x80), sc(16)});
    uint32_t back = rd32(sc(16));
    owned.push_back(back);
    call_method(dev, 9, {back, 0, sc(20)});
    uint32_t rtv = rd32(sc(20));
    owned.push_back(rtv);
    float blue[] = {0, 0, 1, 1};
    memcpy(gm_ptr(sc(0x200)), blue, 16);
    call_method(ctx, 50, {rtv, sc(0x200)});
    call_method(ctx, 33, {1, sc(20), 0});
    float vp[] = {0, 0, 8, 8, 0, 1};
    memcpy(gm_ptr(sc(0x220)), vp, 24);
    call_method(ctx, 44, {1, sc(0x220)});
    auto buffer = [&](uint32_t bytes, uint32_t bind, const void *data) {
        uint32_t d = sc(0x300);
        gm_zero(d, 24);
        wr32(d, bytes);
        wr32(d + 4, 2);
        wr32(d + 8, bind);
        wr32(d + 12, 0x10000);
        wr32(sc(28), 0);
        CHECK_EQ(call_method(dev, 3, {d, 0, sc(28)}), S_OK);
        uint32_t b = rd32(sc(28));
        if (!b)
            return uint32_t(0);
        owned.push_back(b);
        CHECK_EQ(call_method(ctx, 14, {b, 0, 4, 0, sc(0x340)}), S_OK);
        uint32_t ptr = rd32(sc(0x340));
        CHECK(ptr != 0);
        if (ptr && data)
            memcpy(gm_ptr(ptr), data, bytes);
        call_method(ctx, 15, {b, 0});
        return b;
    };
    float vertices[] = {-1, -1, 0, 0, 0, 1, 1, 0, 1, 1, -1, 1, 0, 0, 1, 1, -1, 0, 1, 0};
    uint16_t indices[] = {0, 2, 1, 0, 1, 3, 0};
    uint32_t vb = buffer(sizeof(vertices), 1, vertices), ib = buffer(sizeof(indices), 2, indices);
    if (!vb || !ib) {
        cleanup();
        return;
    }
    // SetDestRect(2,1,6,5) in an 8x8 viewport: a 4x4 texture one-to-one.
    matrix_call("D3DXMatrixTranslation", {sc(0x400), float_word(-0.5f), float_word(-0.25f), 0});
    matrix_call("D3DXMatrixScaling",
                {sc(0x440), float_word(0.5f), float_word(0.5f), float_word(1)});
    matrix_call("D3DXMatrixTranslation", {sc(0x480), float_word(1), float_word(1), 0});
    matrix_call("D3DXMatrixMultiply", {sc(0x4c0), sc(0x480), sc(0x440)});
    matrix_call("D3DXMatrixMultiplyTranspose", {sc(0x500), sc(0x4c0), sc(0x400)});
    matrix_call("D3DXMatrixScaling", {sc(0x540), float_word(1), float_word(1), float_word(1)});
    uint32_t cb = buffer(128, 4, gm_ptr(sc(0x500)));
    if (!cb) {
        cleanup();
        return;
    }
    // A shader from its tagged blob, written at `at`.
    auto shader = [&](const QuadShader &q, uint32_t at) {
        dx11::ShaderTag tag{};
        tag.magic = 0x31425352;
        tag.version = 1;
        tag.hash = q.hash;
        snprintf(tag.entry, sizeof tag.entry, "%s", q.entry);
        snprintf(tag.target, sizeof tag.target, "%s", q.target);
        memcpy(gm_ptr(at), &tag, sizeof tag);
        wr32(sc(0x1888), 0);
        CHECK_EQ(call_method(dev, q.vertex ? 12 : 15, {at, uint32_t(sizeof tag), 0, sc(0x1888)}),
                 S_OK);
        uint32_t sh = rd32(sc(0x1888));
        if (sh)
            owned.push_back(sh);
        return sh;
    };
    uint32_t vs = shader(quad_vertex_shader, sc(0x1000));
    const uint32_t vsdata = sc(0x1000), vssize = sizeof(dx11::ShaderTag);
    uint32_t ps = shader(quad_fragment_shader, sc(0x1080));
    if (!vs || !ps) {
        cleanup();
        return;
    }
    gm_zero(sc(0x600), 56);
    gm_put_str(sc(0x680), "POSITION", 32);
    gm_put_str(sc(0x6a0), "TEXCOORD", 32);
    wr32(sc(0x600), sc(0x680));
    wr32(sc(0x608), 6);
    wr32(sc(0x61c), sc(0x6a0));
    wr32(sc(0x624), 16);
    wr32(sc(0x62c), 0xffffffff);
    CHECK_EQ(call_method(dev, 11, {sc(0x600), 2, vsdata, vssize, sc(32)}), S_OK);
    uint32_t layout = rd32(sc(32));
    owned.push_back(layout);
    // Sixteen distinct texels with every alpha, as {r, g, b, a} bytes.
    uint8_t texels[16][4];
    for (unsigned i = 0; i < 16; ++i) {
        texels[i][0] = uint8_t(i * 17);
        texels[i][1] = uint8_t(255 - i * 13);
        texels[i][2] = uint8_t(i * i * 3);
        texels[i][3] = uint8_t(i * 16);
    }
    // A 4x4 texture and its view in the given format, the texels swizzled
    // to that format's byte order; rows padded to 24 bytes as in UpdateSubresource.
    auto texture = [&](uint32_t format, uint32_t &srv) {
        uint32_t td = sc(0x700);
        gm_zero(td, 44);
        wr32(td, 4);
        wr32(td + 4, 4);
        wr32(td + 12, 1);
        wr32(td + 16, format);
        wr32(td + 20, 1);
        wr32(td + 32, 0x28);
        wr32(td + 40, 1);
        CHECK_EQ(call_method(dev, 5, {td, 0, sc(36)}), S_OK);
        uint32_t tex = rd32(sc(36));
        if (!tex)
            return uint32_t(0);
        owned.push_back(tex);
        gm_zero(sc(0x740), 24);
        wr32(sc(0x740), format);
        wr32(sc(0x744), 4);
        wr32(sc(0x74c), 1);
        CHECK_EQ(call_method(dev, 7, {tex, sc(0x740), sc(40)}), S_OK);
        srv = rd32(sc(40));
        owned.push_back(srv);
        for (unsigned i = 0; i < 16; ++i) {
            const uint8_t *t = texels[i];
            bool bgra = format == 87;
            uint32_t p = sc(0x800) + (i / 4) * 24 + (i % 4) * 4;
            wr8(p, bgra ? t[2] : t[0]);
            wr8(p + 1, t[1]);
            wr8(p + 2, bgra ? t[0] : t[2]);
            wr8(p + 3, t[3]);
        }
        call_method(ctx, 48, {tex, 0, 0, sc(0x800), 24, 0});
        return tex;
    };
    uint32_t srv_rgba = 0, srv_bgra = 0;
    if (!texture(28, srv_rgba) || !texture(87, srv_bgra)) {
        cleanup();
        return;
    }
    auto sampler = [&](uint32_t filter) {
        gm_zero(sc(0x900), 52);
        wr32(sc(0x900), filter);
        for (unsigned i = 1; i <= 3; ++i)
            wr32(sc(0x900) + i * 4, 1);
        wr32(sc(0x918), 8);
        CHECK_EQ(call_method(dev, 23, {sc(0x900), sc(44)}), S_OK);
        uint32_t s = rd32(sc(44));
        owned.push_back(s);
        return s;
    };
    uint32_t linear = sampler(0x15), point = sampler(0);
    // ONE/ZERO blending leaves every channel the source, through the loop.
    gm_zero(sc(0xa00), 264);
    uint32_t d = sc(0xa08);
    wr32(d, 1);
    wr32(d + 4, 2);
    wr32(d + 8, 1);
    wr32(d + 12, 1);
    wr32(d + 16, 2);
    wr32(d + 20, 1);
    wr32(d + 24, 1);
    wr8(d + 28, 15);
    CHECK_EQ(call_method(dev, 20, {sc(0xa00), sc(48)}), S_OK);
    uint32_t one_zero = rd32(sc(48));
    owned.push_back(one_zero);
    call_method(ctx, 17, {layout});
    call_method(ctx, 11, {vs, 0, 0});
    call_method(ctx, 9, {ps, 0, 0});
    wr32(sc(52), vb);
    wr32(sc(56), 20);
    wr32(sc(60), 0);
    call_method(ctx, 18, {0, 1, sc(52), sc(56), sc(60)});
    call_method(ctx, 19, {ib, 57, 0});
    call_method(ctx, 24, {4});
    wr32(sc(64), cb);
    call_method(ctx, 7, {0, 1, sc(64)});
    auto draw = [&](uint32_t srv, uint32_t sampler, uint32_t blend) {
        call_method(ctx, 50, {rtv, sc(0x200)});
        wr32(sc(40), srv);
        call_method(ctx, 8, {0, 1, sc(40)});
        wr32(sc(44), sampler);
        call_method(ctx, 10, {0, 1, sc(44)});
        call_method(ctx, 35, {blend, 0, 0xffffffff});
        call_method(ctx, 12, {6, 0, 0});
        call_method(swap, 8, {0, 0});
        return g_presents.back();
    };
    const Present copy = draw(srv_rgba, linear, 0);
    for (unsigned y = 0; y < 8; ++y)
        for (unsigned x = 0; x < 8; ++x) {
            uint32_t expected = 0xff0000ffu;
            if (x >= 2 && x < 6 && y >= 1 && y < 5) {
                const uint8_t *t = texels[(y - 1) * 4 + x - 2];
                expected = 0xff000000u | uint32_t(t[0]) << 16 | uint32_t(t[1]) << 8 | t[2];
            }
            uint32_t actual = 0;
            memcpy(&actual, copy.pixels.data() + y * copy.pitch + x * 4, 4);
            CHECK_EQ(actual, expected);
        }
    const Present loop = draw(srv_rgba, linear, one_zero), swizzled = draw(srv_bgra, linear, 0),
                  nearest = draw(srv_rgba, point, 0);
    for (const Present *other : {&loop, &swizzled, &nearest}) {
        CHECK_EQ(other->pitch, copy.pitch);
        CHECK(other->pixels == copy.pixels);
    }
    if (hardware) {
        // The first present found the screen not yet the swap chain's: it read
        // the target back, and the next frame was drawn in software. From the
        // second present on the screen is the swap chain's, and the last two
        // frames were drawn and presented on the GPU.
        CHECK_EQ(g_gpu2d_draws, 3);
        CHECK_EQ(g_gpu2d_presents, 2);
        CHECK_EQ(g_gpu2d_readbacks, 1);
        // A quarter pixel off the centres is not a copy: the software path
        // takes it, reading the target back first, and gets the same answer
        // as without a host.
        matrix_call("D3DXMatrixTranslation",
                    {sc(0x400), float_word(-0.5f + 0.0625f), float_word(-0.25f), 0});
        matrix_call("D3DXMatrixMultiplyTranspose", {sc(0x500), sc(0x4c0), sc(0x400)});
        CHECK_EQ(call_method(ctx, 14, {cb, 0, 4, 0, sc(0x340)}), S_OK);
        memcpy(gm_ptr(rd32(sc(0x340))), gm_ptr(sc(0x500)), 128);
        call_method(ctx, 15, {cb, 0});
        const int readbacks = g_gpu2d_readbacks;
        const Present shifted = draw(srv_rgba, point, 0);
        CHECK_EQ(g_gpu2d_draws, 3);
        CHECK(g_gpu2d_readbacks > readbacks);
        g_gpu2d_on = false;
        const Present reference = draw(srv_rgba, point, 0);
        CHECK(shifted.pixels == reference.pixels);
    }
    g_gpu2d_on = false;
    cleanup();
    CHECK_EQ(com_live_count(), live);
}
static void test_d3d11_texel_copy_software() {
    test_d3d11_texel_copy(false);
}
static void test_d3d11_texel_copy_hardware() {
    test_d3d11_texel_copy(true);
}
static void test_d3d11_resource_bounds() {
    cpu_reset();
    gm_zero(sc(0), 0x4000);
    uint32_t sd = sc(0x100);
    wr32(sd, 4);
    wr32(sd + 4, 4);
    wr32(sd + 16, 28);
    wr32(sd + 28, 1);
    wr32(sd + 40, 1);
    wr32(sd + 48, 1);
    call_shim(tramp("d3d11.dll", "D3D11CreateDeviceAndSwapChain"),
              {0, 1, 0, 0, 0, 0, 7, sd, sc(0), sc(4), sc(8), sc(12)});
    uint32_t swap = rd32(sc(0)), dev = rd32(sc(4)), ctx = rd32(sc(12));
    if (!swap || !dev || !ctx) {
        CHECK(false);
        return;
    }
    for (uint32_t format : {28u, 87u, 85u, 56u}) {
        uint32_t td = sc(0x200);
        gm_zero(td, 44);
        wr32(td, 4);
        wr32(td + 4, 4);
        wr32(td + 8, 1);
        wr32(td + 12, 1);
        wr32(td + 16, format);
        wr32(td + 20, 1);
        wr32(td + 28, 3);
        wr32(td + 36, 0x30000);
        CHECK_EQ(call_method(dev, 5, {td, 0, sc(16)}), S_OK);
        uint32_t tex = rd32(sc(16));
        if (!tex)
            continue;
        uint32_t bpp = format == 28 || format == 87 ? 4 : 2, row = 2 * bpp;
        for (unsigned i = 0; i < 64; ++i)
            wr8(sc(0x400) + i, uint8_t(i + 1));
        uint32_t box[] = {1, 1, 0, 3, 3, 1};
        memcpy(gm_ptr(sc(0x300)), box, 24);
        call_method(ctx, 48, {tex, 0, sc(0x300), sc(0x400), 16, 0});
        CHECK_EQ(call_method(ctx, 14, {tex, 0, 3, 0, sc(0x500)}), S_OK);
        uint32_t data = rd32(sc(0x500)), pitch = rd32(sc(0x504));
        CHECK_EQ(pitch, 4 * bpp);
        CHECK_EQ(rd32(sc(0x508)), pitch * 4);
        for (unsigned y = 0; y < 4; ++y)
            for (unsigned x = 0; x < pitch; ++x) {
                uint8_t expected = y >= 1 && y < 3 && x >= bpp && x < bpp + row
                                       ? uint8_t((y - 1) * 16 + x - bpp + 1)
                                       : 0;
                CHECK_EQ(rd8(data + y * pitch + x), expected);
            }
        wr8(data, 0xa5); // no copy: a remap sees the same guest-visible resource
        call_method(ctx, 15, {tex, 0});
        call_method(ctx, 14, {tex, 0, 1, 0, sc(0x500)});
        CHECK_EQ(rd8(rd32(sc(0x500))), 0xa5);
        call_method(ctx, 15, {tex, 0});
        // Malformed destination boxes and overflowing source spans leave bytes intact.
        wr32(sc(0x30c), 5);
        CHECK_EQ(call_method(ctx, 48, {tex, 0, sc(0x300), sc(0x400), 16, 0}), E_INVALIDARG);
        wr32(sc(0x30c), 3);
        CHECK_EQ(call_method(ctx, 48, {tex, 0, sc(0x300), GUEST_SIZE - 1, 0xffffffff, 0}),
                 E_INVALIDARG);
        CHECK_EQ(rd8(data), 0xa5);
        call_method(tex, 10, {sc(0x600)});
        CHECK(memcmp(gm_ptr(sc(0x600)), gm_ptr(td), 44) == 0);
        call_method(tex, 2);
    }
    // A sampler containing NaN cannot be evaluated by the software pipeline.
    gm_zero(sc(0x800), 52);
    wr32(sc(0x804), 4);
    wr32(sc(0x808), 4);
    wr32(sc(0x80c), 4);
    wr32(sc(0x81c), 0x7fc00000);
    CHECK_EQ(call_method(dev, 23, {sc(0x800), sc(20)}), E_NOTIMPL);
    CHECK_EQ(rd32(sc(20)), 0);
    call_method(ctx, 2);
    call_method(dev, 2);
    call_method(swap, 2);
}
// ---------------------------------------------------------------------------
// Media Foundation: the objects a player builds, and the event sequence it
// drives itself from. No file is opened here - that needs a movie, and these
// tests carry no assets - so this covers the plumbing either way: the
// topology it fills in, the attributes it reads back, the events the session
// posts for each transport call, and the video service it asks the session
// for by name.
// ---------------------------------------------------------------------------
static void put_guid(uint32_t at, const uint8_t (&g)[16]) {
    for (int i = 0; i < 16; ++i)
        wr8(at + (uint32_t)i, g[i]);
}

// Slots, counted from the interface's own vtable. IMFAttributes occupies 3..32
// of every interface that derives from it, so a topology's own methods start
// at 33 and a session's - which derives from IMFMediaEventGenerator instead -
// start at 7.
enum {
    MF_ATTR_GetUINT32 = 7,
    MF_ATTR_SetUINT32 = 21,
    MF_ATTR_GetCount = 30,
    MF_TOPO_AddNode = 34,
    MF_TOPO_GetNodeCount = 36,
    MF_TOPO_GetNode = 37,
    MF_NODE_GetNodeType = 35,
    MF_SESSION_GetEvent = 3,
    MF_SESSION_SetTopology = 7,
    MF_SESSION_Start = 9,
    MF_SESSION_Pause = 10,
    MF_SESSION_Stop = 11,
    MF_SESSION_Close = 12,
    MF_SESSION_GetClock = 14,
    MF_SESSION_GetCaps = 15,
    MF_EVENT_GetType = 33,
    MF_EVENT_GetStatus = 35,
    MF_ATTR_SetUnknown = 27,
    MF_RESOLVER_CreateObjectFromURL = 3,
    MF_NODE_SetObject = 33,
    MF_VIDEO_SetVideoWindow = 9,
    MF_VIDEO_GetVideoWindow = 10,
    MF_CLOCK_GetTime = 10,
};

// The next event the session has queued, as (type, status).
static void next_event(uint32_t session, uint32_t *type, uint32_t *status) {
    *type = *status = 0;
    wr32(sc(0x40), 0);
    if (call_method(session, MF_SESSION_GetEvent, {0, sc(0x40)}) != S_OK)
        return;
    const uint32_t ev = rd32(sc(0x40));
    if (!ev)
        return;
    call_method(ev, MF_EVENT_GetType, {sc(0x44)});
    call_method(ev, MF_EVENT_GetStatus, {sc(0x48)});
    *type = rd32(sc(0x44));
    *status = rd32(sc(0x48));
}

// A player does its renderer setup from the topology-status event, not from
// SetTopology returning: that handler is where it asks for
// IMFVideoDisplayControl and stores the pointer it calls through afterwards.
// A session that never reports the topology ready leaves that field nil, and
// the failure surfaces as a nil call inside the player, nowhere near here.
// This needs a real source, so it opens the tone the audio tests carry: the
// clip has no video track, and the event sequence does not depend on one.
[[maybe_unused]] static void test_media_foundation_topology_ready() {
    cpu_reset();

    char dir[512];
    snprintf(dir, sizeof dir, "%s/recomp-mf-topology-XXXXXX", os_temp_dir());
    CHECK(os_mkdtemp(dir) == 0);
    const std::string file = std::string(dir) + "/Movie.mp3";
    FILE *f = fopen(file.c_str(), "wb");
    CHECK(f != nullptr);
    if (!f)
        return;
    CHECK_EQ(fwrite(kToneMp3, 1, sizeof kToneMp3, f), sizeof kToneMp3);
    CHECK_EQ(fclose(f), 0);
    win32_init(dir);

    wr32(sc(0), 0);
    CHECK_EQ(call_shim(tramp("mf.dll", "MFCreateSourceResolver"), {sc(0)}), S_OK);
    const uint32_t resolver = rd32(sc(0));
    CHECK(resolver != 0);
    gm_put_wstr(sc(0x100), "Movie.mp3", 0x80);
    wr32(sc(4), 0);
    wr32(sc(8), 0);
    CHECK_EQ(
        call_method(resolver, MF_RESOLVER_CreateObjectFromURL, {sc(0x100), 1, 0, sc(4), sc(8)}),
        S_OK);
    const uint32_t source = rd32(sc(8));
    CHECK(source != 0);

    // The source hangs off the node as MF_TOPONODE_SOURCE, which is how a
    // player attaches it and how the session finds it again.
    wr32(sc(0xc), 0);
    CHECK_EQ(call_shim(tramp("mf.dll", "MFCreateTopology"), {sc(0xc)}), S_OK);
    const uint32_t topo = rd32(sc(0xc));
    CHECK(topo != 0);
    wr32(sc(0x10), 0);
    CHECK_EQ(call_shim(tramp("mf.dll", "MFCreateTopologyNode"), {1, sc(0x10)}), S_OK);
    const uint32_t node = rd32(sc(0x10));
    CHECK(node != 0);
    // MF_TOPONODE_SOURCE {835C58EC-E075-4BC7-BCBA-4DE000DF9AE6}
    static const uint8_t kNodeSource[16] = {0xec, 0x58, 0x5c, 0x83, 0x75, 0xe0, 0xc7, 0x4b,
                                            0xbc, 0xba, 0x4d, 0xe0, 0x00, 0xdf, 0x9a, 0xe6};
    put_guid(sc(0x200), kNodeSource);
    CHECK_EQ(call_method(node, MF_ATTR_SetUnknown, {sc(0x200), source}), S_OK);
    CHECK_EQ(call_method(topo, MF_TOPO_AddNode, {node}), S_OK);

    wr32(sc(0x14), 0);
    CHECK_EQ(call_shim(tramp("mf.dll", "MFCreateMediaSession"), {0, sc(0x14)}), S_OK);
    const uint32_t session = rd32(sc(0x14));
    CHECK(session != 0);
    CHECK_EQ(call_method(session, MF_SESSION_SetTopology, {0, topo}), S_OK);

    // This topology names a source, so the set itself succeeds ...
    uint32_t type = 0, status = 0;
    next_event(session, &type, &status);
    CHECK_EQ(type, 101u); // MESessionTopologySet
    CHECK_EQ(status, S_OK);

    // ... and the readiness the player is waiting for follows it, carrying
    // MF_TOPOSTATUS_READY as an attribute rather than in the event status.
    wr32(sc(0x40), 0);
    CHECK_EQ(call_method(session, MF_SESSION_GetEvent, {0, sc(0x40)}), S_OK);
    const uint32_t ev = rd32(sc(0x40));
    CHECK(ev != 0);
    call_method(ev, MF_EVENT_GetType, {sc(0x44)});
    CHECK_EQ(rd32(sc(0x44)), 111u); // MESessionTopologyStatus
    static const uint8_t kTopologyStatus[16] = {0x8d, 0x01, 0xc5, 0x30, 0x53, 0x9a, 0x4b, 0x45,
                                                0xad, 0x9e, 0x6d, 0x5f, 0x8f, 0xa7, 0xc4, 0x3b};
    put_guid(sc(0x50), kTopologyStatus); // a GUID spans 0x50..0x5f
    wr32(sc(0xa0), 0);
    CHECK_EQ(call_method(ev, MF_ATTR_GetUINT32, {sc(0x50), sc(0xa0)}), S_OK);
    CHECK_EQ(rd32(sc(0xa0)), 100u); // MF_TOPOSTATUS_READY

    // And the service that handler asks for is there to be had.
    static const uint8_t kVideoService[16] = {0x6c, 0xa8, 0x92, 0x10, 0x1a, 0xab, 0x9a, 0x45,
                                              0xa3, 0x36, 0x83, 0x1f, 0xbc, 0x4d, 0x11, 0xff};
    static const uint8_t kVideoControl[16] = {0xe4, 0xb1, 0x90, 0xa4, 0x84, 0xab, 0x31, 0x4d,
                                              0xa1, 0xb2, 0x18, 0x1e, 0x03, 0xb1, 0x07, 0x7a};
    put_guid(sc(0x60), kVideoService);
    put_guid(sc(0x70), kVideoControl);
    wr32(sc(0x80), 0);
    CHECK_EQ(call_shim(tramp("mf.dll", "MFGetService"), {session, sc(0x60), sc(0x70), sc(0x80)}),
             S_OK);
    CHECK(rd32(sc(0x80)) != 0);
    CHECK_EQ(call_method(session, MF_SESSION_Close, {}), S_OK);
}

static void test_media_foundation_session() {
    cpu_reset();

    // A topology, a node, and the attribute a player sets on it.
    wr32(sc(0), 0);
    CHECK_EQ(call_shim(tramp("mf.dll", "MFCreateTopology"), {sc(0)}), S_OK);
    const uint32_t topo = rd32(sc(0));
    CHECK(topo != 0);

    wr32(sc(4), 0);
    CHECK_EQ(call_shim(tramp("mf.dll", "MFCreateTopologyNode"), {1, sc(4)}), S_OK);
    const uint32_t node = rd32(sc(4));
    CHECK(node != 0);
    call_method(node, MF_NODE_GetNodeType, {sc(8)});
    CHECK_EQ(rd32(sc(8)), 1u);

    // MF_TOPONODE_STREAMID is a UINT32 attribute; any key exercises the store.
    static const uint8_t kKey[16] = {0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88,
                                     0x99, 0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff, 0x01};
    put_guid(sc(0x10), kKey);
    CHECK_EQ(call_method(node, MF_ATTR_SetUINT32, {sc(0x10), 7}), S_OK);
    wr32(sc(0x20), 0);
    CHECK_EQ(call_method(node, MF_ATTR_GetUINT32, {sc(0x10), sc(0x20)}), S_OK);
    CHECK_EQ(rd32(sc(0x20)), 7u);
    CHECK_EQ(call_method(node, MF_ATTR_GetCount, {sc(0x24)}), S_OK);
    CHECK_EQ(rd32(sc(0x24)), 1u);
    // A key that was never set is absent, not zero: MF_E_ATTRIBUTENOTFOUND.
    static const uint8_t kAbsent[16] = {0};
    put_guid(sc(0x30), kAbsent);
    CHECK_EQ(call_method(node, MF_ATTR_GetUINT32, {sc(0x30), sc(0x20)}), 0xC00D36E6u);

    CHECK_EQ(call_method(topo, MF_TOPO_AddNode, {node}), S_OK);
    call_method(topo, MF_TOPO_GetNodeCount, {sc(0x28)});
    CHECK_EQ(rd32(sc(0x28)), 1u);
    wr32(sc(0x2c), 0);
    CHECK_EQ(call_method(topo, MF_TOPO_GetNode, {0, sc(0x2c)}), S_OK);
    CHECK_EQ(rd32(sc(0x2c)), node);

    // The session, and the events each transport call posts.
    wr32(sc(0x38), 0);
    CHECK_EQ(call_shim(tramp("mf.dll", "MFCreateMediaSession"), {0, sc(0x38)}), S_OK);
    const uint32_t session = rd32(sc(0x38));
    CHECK(session != 0);
    CHECK_EQ(call_method(session, MF_SESSION_GetCaps, {sc(0x3c)}), S_OK);
    CHECK(rd32(sc(0x3c)) != 0);

    uint32_t type = 0, status = 0;
    // This topology names no media source, so the session accepts it and says
    // so in the event's status rather than in SetTopology's return.
    CHECK_EQ(call_method(session, MF_SESSION_SetTopology, {0, topo}), S_OK);
    next_event(session, &type, &status);
    CHECK_EQ(type, 101u); // MESessionTopologySet
    CHECK_EQ(status, E_FAIL);

    CHECK_EQ(call_method(session, MF_SESSION_Start, {0, 0}), S_OK);
    next_event(session, &type, &status);
    CHECK_EQ(type, 103u); // MESessionStarted
    CHECK_EQ(call_method(session, MF_SESSION_Pause, {}), S_OK);
    next_event(session, &type, &status);
    CHECK_EQ(type, 104u); // MESessionPaused
    CHECK_EQ(call_method(session, MF_SESSION_Stop, {}), S_OK);
    next_event(session, &type, &status);
    CHECK_EQ(type, 105u); // MESessionStopped
    CHECK_EQ(call_method(session, MF_SESSION_Close, {}), S_OK);
    next_event(session, &type, &status);
    CHECK_EQ(type, 106u); // MESessionClosed
    // The queue is empty again; asking anyway is not an error worth a crash.
    next_event(session, &type, &status);
    CHECK_EQ(type, 0u);

    // The clock, and the video service a player asks for by name.
    wr32(sc(0x50), 0);
    CHECK_EQ(call_method(session, MF_SESSION_GetClock, {sc(0x50)}), S_OK);
    const uint32_t clock = rd32(sc(0x50));
    CHECK(clock != 0);
    CHECK_EQ(call_method(clock, MF_CLOCK_GetTime, {sc(0x54)}), S_OK);

    static const uint8_t kVideoService[16] = {0x6c, 0xa8, 0x92, 0x10, 0x1a, 0xab, 0x9a, 0x45,
                                              0xa3, 0x36, 0x83, 0x1f, 0xbc, 0x4d, 0x11, 0xff};
    static const uint8_t kVideoControl[16] = {0xe4, 0xb1, 0x90, 0xa4, 0x84, 0xab, 0x31, 0x4d,
                                              0xa1, 0xb2, 0x18, 0x1e, 0x03, 0xb1, 0x07, 0x7a};
    put_guid(sc(0x60), kVideoService);
    put_guid(sc(0x70), kVideoControl);
    wr32(sc(0x80), 0);
    CHECK_EQ(call_shim(tramp("mf.dll", "MFGetService"), {session, sc(0x60), sc(0x70), sc(0x80)}),
             S_OK);
    const uint32_t video = rd32(sc(0x80));
    CHECK(video != 0);
    CHECK_EQ(call_method(video, MF_VIDEO_SetVideoWindow, {0x1234}), S_OK);
    CHECK_EQ(call_method(video, MF_VIDEO_GetVideoWindow, {sc(0x84)}), S_OK);
    CHECK_EQ(rd32(sc(0x84)), 0x1234u);

    // A service nobody serves is E_NOINTERFACE with the out-pointer cleared,
    // which is how a player tells "not available" from "it broke".
    put_guid(sc(0x90), kKey);
    wr32(sc(0x80), 0xdeadbeef);
    CHECK_EQ(call_shim(tramp("mf.dll", "MFGetService"), {session, sc(0x90), sc(0x70), sc(0x80)}),
             E_NOINTERFACE);
    CHECK_EQ(rd32(sc(0x80)), 0u);
}

// Driver output pointers, master gain, named MP3, and WAVEHDR completion are
// observable API contracts: success without these leaves real games silent.
static void test_legacy_audio() {
    cpu_reset();
    g_plays.clear();
    g_sample_tracking = true;
    auto ail = [](const char *n, std::initializer_list<uint32_t> a) {
        return call_shim(tramp("mss32.dll", n), a);
    };
    uint32_t wav = build_test_wave(), fmt = wav + 20;
    CHECK_EQ(ail("_AIL_waveOutOpen@16", {sc(0), 0, UINT32_MAX, fmt}), 0u);
    uint32_t driver = rd32(sc(0));
    CHECK(driver != 0);
    CHECK_EQ(ail("_AIL_set_preference@8", {15, 7}), 0u);
    CHECK_EQ(ail("_AIL_get_preference@4", {15}), 7u);
    ail("_AIL_set_preference@8", {15, 0});
    ail("_AIL_digital_configuration@16", {driver, sc(4), sc(8), sc(0x300)});
    CHECK_EQ(rd32(sc(4)), 22050u);
    CHECK(std::string(gm_str(sc(0x300))).find("Native") != std::string::npos);
    uint32_t sample = ail("_AIL_allocate_sample_handle@4", {driver});
    ail("_AIL_set_sample_file@12", {sample, wav, 0});
    ail("_AIL_set_digital_master_volume@8", {driver, 64});
    ail("_AIL_start_sample@4", {sample});
    CHECK(!g_plays.empty());
    if (!g_plays.empty())
        CHECK_EQ(g_plays.back().volume, -595);
    uint32_t mp3 = heap_alloc(sizeof kToneMp3);
    memcpy(g_mem + mp3, kToneMp3, sizeof kToneMp3);
    gm_put_str(sc(0x300), "mp3", 8);
    CHECK_EQ(ail("_AIL_set_named_sample_file@20", {sample, sc(0x300), mp3, sizeof kToneMp3, 0}),
             1u);
    ail("_AIL_start_sample@4", {sample});
    CHECK_EQ(g_plays.back().bits, 16);
    CHECK(g_plays.back().bytes > 1000);
    ail("_AIL_waveOutClose@4", {driver});
    CHECK_EQ(ail("_AIL_sample_status@4", {sample}), 1u);
    heap_free(mp3);
    auto wave = [](const char *n, std::initializer_list<uint32_t> a) {
        return call_shim(tramp("_INMM.dll", n), a);
    };
    g_queue_enabled = true;
    g_test_audio_pos = 0;
    g_stream_base = g_stream_played = 0;
    g_plays.clear();
    CHECK_EQ(wave("waveOutOpen", {sc(0), UINT32_MAX, fmt, 0, 0, 0}), 0u);
    uint32_t h = rd32(sc(0));
    CHECK(h != 0);
    uint32_t hdr = sc(0x400);
    gm_zero(hdr, 32);
    wr32(hdr, wav + 44);
    wr32(hdr + 4, 8);
    CHECK_EQ(wave("waveOutPrepareHeader", {h, hdr, 32}), 0u);
    CHECK_EQ(rd32(hdr + 16), 2u);
    wave("waveOutPause", {h});
    CHECK_EQ(wave("waveOutWrite", {h, hdr, 32}), 0u);
    CHECK(g_plays.empty());
    CHECK_EQ(rd32(hdr + 16), 18u);
    CHECK_EQ(wave("waveOutUnprepareHeader", {h, hdr, 32}), 33u);
    wave("waveOutRestart", {h});
    CHECK_EQ(g_plays.size(), 1u);
    if (!g_plays.empty())
        CHECK_EQ(g_plays.back().bytes, 8u);
    g_stream_played = 7;
    waveout_frame_pump(&g_cpu);
    CHECK_EQ(rd32(hdr + 16), 18u);
    g_stream_played = 8;
    waveout_frame_pump(&g_cpu);
    CHECK_EQ(rd32(hdr + 16), 3u);
    CHECK_EQ(wave("waveOutUnprepareHeader", {h, hdr, 32}), 0u);
    CHECK_EQ(wave("waveOutClose", {h}), 0u);
#ifdef RECOMP_HAVE_FFMPEG
    // A generated mono IMA block decodes into nine 16-bit samples. Header
    // completion follows those 18 PCM bytes, not the eight compressed bytes.
    uint32_t adpcm_fmt = sc(0x500), block = sc(0x520);
    gm_zero(adpcm_fmt, 20);
    wr16(adpcm_fmt, 0x11);
    wr16(adpcm_fmt + 2, 1);
    wr32(adpcm_fmt + 4, 22050);
    wr32(adpcm_fmt + 8, 19600);
    wr16(adpcm_fmt + 12, 8);
    wr16(adpcm_fmt + 14, 4);
    wr16(adpcm_fmt + 16, 2);
    wr16(adpcm_fmt + 18, 9);
    wr32(block, 1000); // Predictor 1000, step index/reserved zero.
    wr32(block + 4, 0x11111111);
    CHECK_EQ(wave("waveOutOpen", {sc(0), UINT32_MAX, adpcm_fmt, 0, 0, 0}), 0u);
    h = rd32(sc(0));
    gm_zero(hdr, 32);
    wr32(hdr, block);
    wr32(hdr + 4, 8);
    g_plays.clear();
    g_stream_played = 0;
    CHECK_EQ(wave("waveOutPrepareHeader", {h, hdr, 32}), 0u);
    CHECK_EQ(wave("waveOutWrite", {h, hdr, 32}), 0u);
    CHECK_EQ(g_plays.size(), 1u);
    if (!g_plays.empty()) {
        const auto &decoded = g_plays.back();
        CHECK_EQ(decoded.bits, 16);
        CHECK_EQ(decoded.bytes, 18u);
        if (decoded.pcm.size() == 18) {
            CHECK_EQ(uint16_t(decoded.pcm[0] | (decoded.pcm[1] << 8)), 1000u);
            CHECK(uint16_t(decoded.pcm[16] | (decoded.pcm[17] << 8)) > 1000);
        }
    }
    g_stream_played = 8;
    waveout_frame_pump(&g_cpu);
    CHECK_EQ(rd32(hdr + 16), 18u);
    g_stream_played = 18;
    waveout_frame_pump(&g_cpu);
    CHECK_EQ(rd32(hdr + 16), 3u);
    CHECK_EQ(wave("waveOutUnprepareHeader", {h, hdr, 32}), 0u);
    CHECK_EQ(wave("waveOutClose", {h}), 0u);
#endif
    // ExitProcess can leave wave handles open. Host teardown must reclaim
    // them while audio is alive, and a repeated shutdown must be harmless.
    CHECK_EQ(wave("waveOutOpen", {sc(0), UINT32_MAX, fmt, 0, 0, 0}), 0u);
    h = rd32(sc(0));
    gm_zero(hdr, 32);
    wr32(hdr, wav + 44);
    wr32(hdr + 4, 8);
    CHECK_EQ(wave("waveOutPrepareHeader", {h, hdr, 32}), 0u);
    CHECK_EQ(wave("waveOutWrite", {h, hdr, 32}), 0u);
    size_t stops = g_stops.size();
    waveout_shutdown();
    CHECK_EQ(g_stops.size(), stops + 1);
    CHECK_EQ(wave("waveOutClose", {h}), 5u);
    waveout_shutdown();
    CHECK_EQ(g_stops.size(), stops + 1);
    g_queue_enabled = false;
    g_sample_tracking = false;
}

// Small generated RIFF fixture: stream metadata, keyframe search, sample/byte
// units, buffer negotiation and shared file lifetime need no private assets.
static void test_avi_reader() {
    cpu_reset();
    auto put = [](std::vector<uint8_t> &v, uint32_t n) {
        for (int i = 0; i < 4; ++i)
            v.push_back(uint8_t(n >> (i * 8)));
    };
    auto chunk = [&](const char *tag, const std::vector<uint8_t> &data) {
        std::vector<uint8_t> v(tag, tag + 4);
        put(v, uint32_t(data.size()));
        v.insert(v.end(), data.begin(), data.end());
        if (data.size() & 1)
            v.push_back(0);
        return v;
    };
    auto append = [](std::vector<uint8_t> &a, const std::vector<uint8_t> &b) {
        a.insert(a.end(), b.begin(), b.end());
    };
    auto set = [](std::vector<uint8_t> &v, size_t pos, uint32_t n) {
        for (int i = 0; i < 4; ++i)
            v[pos + i] = uint8_t(n >> (i * 8));
    };
    std::vector<uint8_t> sh(64), fmt(40);
    set(sh, 0, 0x73646976);
    set(sh, 4, 0x30355649);
    set(sh, 20, 1);
    set(sh, 24, 30);
    set(sh, 32, 3);
    set(sh, 36, 3);
    set(fmt, 0, 40);
    set(fmt, 4, 2);
    set(fmt, 8, 2);
    set(fmt, 12, 0x180001);
    set(fmt, 16, 0x30355649);
    std::vector<uint8_t> strl = {'s', 't', 'r', 'l'};
    append(strl, chunk("strh", sh));
    append(strl, chunk("strf", fmt));
    std::vector<uint8_t> hdrl = {'h', 'd', 'r', 'l'};
    append(hdrl, chunk("LIST", strl));
    std::vector<uint8_t> movi = {'m', 'o', 'v', 'i'};
    append(movi, chunk("00dc", {1, 2, 3}));
    append(movi, chunk("00dc", {4, 5}));
    append(movi, chunk("00dc", {6}));
    std::vector<uint8_t> index;
    for (unsigned i = 0; i < 3; ++i) {
        put(index, 0x63643030);
        put(index, i == 1 ? 0 : 16);
        put(index, 0);
        put(index, 3 - i);
    }
    std::vector<uint8_t> body = {'A', 'V', 'I', ' '};
    append(body, chunk("LIST", hdrl));
    append(body, chunk("LIST", movi));
    append(body, chunk("idx1", index));
    auto data = chunk("RIFF", body);
    char dir[512];
    snprintf(dir, sizeof dir, "%s/recomp-avi-XXXXXX", os_temp_dir());
    CHECK(os_mkdtemp(dir) == 0);
    std::string path = std::string(dir) + "/fixture.avi";
    FILE *f = fopen(path.c_str(), "wb");
    CHECK(f != nullptr);
    if (!f)
        return;
    fwrite(data.data(), 1, data.size(), f);
    fclose(f);
    win32_init(dir);
    gm_put_str(sc(0x100), "fixture.avi", 64);
    auto avi = [](const char *n, std::initializer_list<uint32_t> a) {
        return call_shim(tramp("AVIFIL32.dll", n), a);
    };
    CHECK_EQ(avi("AVIFileOpenA", {sc(0), sc(0x100), 0x20, 0}), 0u);
    uint32_t file = rd32(sc(0));
    CHECK(file != 0);
    CHECK_EQ(avi("AVIFileGetStream", {file, sc(4), 0x73646976, 0}), 0u);
    uint32_t stream = rd32(sc(4));
    CHECK(stream != 0);
    CHECK_EQ(avi("AVIStreamInfoA", {stream, sc(0x200), 140}), 0u);
    CHECK_EQ(rd32(sc(0x214)), 1u);
    CHECK_EQ(rd32(sc(0x218)), 30u);
    CHECK_EQ(rd32(sc(0x220)), 3u);
    CHECK_EQ(avi("AVIStreamReadFormat", {stream, 0, 0, sc(8)}), 0u);
    CHECK_EQ(rd32(sc(8)), 40u);
    wr32(sc(8), 1);
    CHECK(avi("AVIStreamReadFormat", {stream, 0, sc(0x300), sc(8)}) != 0);
    CHECK_EQ(rd32(sc(8)), 40u);
    CHECK_EQ(avi("AVIStreamSampleToTime", {stream, 30}), 1000u);
    CHECK_EQ(avi("AVIStreamTimeToSample", {stream, 1000}), 30u);
    CHECK_EQ(avi("AVIStreamTimeToSample", {stream, uint32_t(-1000)}), uint32_t(-30));
    CHECK_EQ(avi("AVIStreamFindSample", {stream, 1, 0x14}), 0u);
    CHECK_EQ(avi("AVIStreamFindSample", {stream, 1, 0x11}), 2u);
    avi("AVIFileRelease", {file}); // the stream still owns the bytes
    CHECK_EQ(avi("AVIStreamRead", {stream, 0, 1, 0, 0, sc(8), sc(12)}), 0u);
    CHECK_EQ(rd32(sc(8)), 3u);
    CHECK_EQ(rd32(sc(12)), 1u);
    wr32(sc(0x300), 0xdeadbeef);
    CHECK(avi("AVIStreamRead", {stream, 0, 1, sc(0x300), 2, sc(8), sc(12)}) != 0);
    CHECK_EQ(rd32(sc(0x300)), 0xdeadbeefu);
    CHECK_EQ(avi("AVIStreamRead", {stream, 0, 2, sc(0x300), 5, sc(8), sc(12)}), 0u);
    CHECK_EQ(rd32(sc(8)), 5u);
    CHECK_EQ(rd32(sc(12)), 2u);
    CHECK_EQ(rd8(sc(0x304)), 5u);
    CHECK_EQ(avi("AVIStreamRead", {stream, 3, 1, sc(0x300), 5, sc(8), sc(12)}), 0u);
    CHECK_EQ(rd32(sc(8)), 0u);
    avi("AVIStreamRelease", {stream});
    CHECK(avi("AVIStreamRead", {stream, 0, 1, 0, 0, 0, 0}) != 0);
    CHECK_EQ(imports_argc(tramp("MSVFW32.dll", "ICDecompress")), ARGC_CDECL);
    os_unlink(path.c_str());
    os_rmdir(dir);
}

// Optional private-asset probe: exercise the same AVI/codec imports as the
// guest, without redistributing a movie or using a host media player.
static void test_avi_asset(const char *path) {
    cpu_reset();
    std::string full = path;
    size_t slash = full.find_last_of("/\\");
    CHECK(slash != std::string::npos);
    if (slash == std::string::npos)
        return;
    win32_init(full.substr(0, slash));
    gm_put_str(sc(0x100), full.substr(slash + 1).c_str(), 1024);
    auto avi = [](const char *name, std::initializer_list<uint32_t> args) {
        return call_shim(tramp("AVIFIL32.dll", name), args);
    };
    auto ic = [](const char *name, std::initializer_list<uint32_t> args) {
        return call_shim(tramp("MSVFW32.dll", name), args);
    };
    CHECK_EQ(avi("AVIFileOpenA", {sc(0), sc(0x100), 0x20, 0}), 0u);
    uint32_t file = rd32(sc(0));
    if (!file)
        return;
    CHECK_EQ(avi("AVIFileGetStream", {file, sc(4), 0x73646976, 0}), 0u);
    uint32_t video = rd32(sc(4));
    CHECK_EQ(avi("AVIStreamInfoA", {video, sc(0x600), 140}), 0u);
    wr32(sc(8), 256);
    CHECK_EQ(avi("AVIStreamReadFormat", {video, 0, sc(0x800), sc(8)}), 0u);
    uint32_t codec = ic("ICLocate", {0x63646976, rd32(sc(0x804 + 12)), sc(0x800), 0, 2});
    CHECK(codec != 0);
    if (!codec)
        return;
    memcpy(g_mem + sc(0x900), g_mem + sc(0x800), 40);
    wr16(sc(0x90e), 16);
    wr32(sc(0x910), 0);
    uint32_t bytes = rd32(sc(0x804)) * rd32(sc(0x808)) * 2;
    uint32_t output = heap_alloc(bytes), input = heap_alloc(2 * 1024 * 1024);
    bool nonblack = false;
    uint32_t prev = 0;
    unsigned changes = 0;
    unsigned repeats = 0;
    uint32_t limit = 150;
    if (const char *frames = getenv("RECOMP_TEST_AVI_FRAMES"))
        limit = uint32_t(strtoul(frames, nullptr, 10));
    if (!limit)
        limit = rd32(sc(0x620));
    CHECK_EQ(ic("ICSendMessage", {codec, 0x400c, sc(0x800), sc(0x900)}), 0u);
    for (uint32_t frame = 0; frame < std::min(limit, rd32(sc(0x620))); ++frame) {
        CHECK_EQ(avi("AVIStreamRead", {video, frame, 1, input, 2 * 1024 * 1024, sc(12), sc(16)}),
                 0u);
        uint32_t result = ic("ICDecompress", {codec, 0, sc(0x800), input, sc(0x900), output});
        if (result) {
            printf("AVI decode frame %u returned %08x, format %ux%u\n", frame, result,
                   rd32(sc(0x804)), rd32(sc(0x808)));
            CHECK_EQ(result, 0u);
            break;
        }
        uint32_t hash = 2166136261u;
        for (uint32_t i = 0; i < bytes; ++i) {
            hash = (hash ^ rd8(output + i)) * 16777619u;
            nonblack |= rd8(output + i) != 0;
        }
        changes += frame && hash != prev;
        if (frame && !rd32(sc(12))) {
            CHECK_EQ(hash, prev);
            ++repeats;
        }
        prev = hash;
    }
    CHECK(nonblack);
    CHECK(changes > 10);
    printf("AVI decoded changing image: %u changes, %u empty-frame repeats, nonblack=%d\n", changes,
           repeats, nonblack);
    ic("ICClose", {codec});
    avi("AVIStreamRelease", {video});
    avi("AVIFileRelease", {file});
    heap_free(input);
    heap_free(output);
}

// Optional audio probe uses the same decoder as file-backed CD music. A
// generated Ogg tone works in CI; private tracks can be checked locally.
static void test_audio_asset(const char *path) {
    mf::Media media;
    std::string why;
    bool opened = media.open(path, &why);
    CHECK(opened);
    if (!opened) {
        printf("Audio open failed: %s\n", why.c_str());
        return;
    }
    CHECK(media.has_audio());
    CHECK(media.audio_rate() > 0);
    CHECK(media.audio_channels() > 0);
    CHECK(media.duration() > 0);
    if (!media.has_audio() || media.audio_rate() <= 0 || media.audio_channels() <= 0)
        return;
    size_t samples = 0;
    int peak = 0;
    for (int block = 0; block < 4; ++block) {
        media.fill_audio(size_t(media.audio_rate()) * media.audio_channels());
        auto pcm = media.take_audio();
        samples += pcm.size();
        for (int16_t sample : pcm)
            peak = std::max(peak, std::abs(int(sample)));
        if (media.finished())
            break;
    }
    CHECK(samples > 0);
    CHECK(peak > 0);
    printf("Audio decoded: %zu samples, %d Hz, %d channels, peak=%d\n", samples, media.audio_rate(),
           media.audio_channels(), peak);
}

// Optional file-backed movie probe. Unlike the audio-only probe, this drives
// next_video(), which also feeds the audio queue, and checks timestamps and
// changing pixels. It opens no output device and is not an AV-sync test.
static void test_movie_asset(const char *path) {
    mf::Media media;
    std::string why;
    bool opened = media.open(path, &why);
    CHECK(opened);
    if (!opened) {
        printf("Movie open failed: %s\n", why.c_str());
        return;
    }
    CHECK(media.has_video());
    CHECK(media.has_audio());
    const int channels = media.audio_channels();
    CHECK(channels > 0 && channels <= 32);
    if (!media.has_video() || channels <= 0 || channels > 32)
        return;
    std::vector<int> peaks(size_t(channels), 0);
    std::vector<uint32_t> audio_hash(size_t(channels), 2166136261u);
    std::vector<double> costs;
    mf::VideoFrame frame;
    size_t samples = 0;
    unsigned frames = 0, changes = 0;
    uint32_t previous_hash = 0;
    double first_pts = -1, previous_pts = -1;
    bool nonblack = false;
    for (unsigned i = 0; i < 180; ++i) {
        const uint64_t before = os_monotonic_ns();
        if (!media.next_video(&frame))
            break;
        costs.push_back(double(os_monotonic_ns() - before) / 1e6);
        CHECK(frame.width > 0 && frame.height > 0);
        CHECK_EQ(frame.argb.size(), size_t(frame.width) * size_t(frame.height));
        CHECK(std::isfinite(frame.pts) && frame.pts >= previous_pts);
        if (!frames)
            first_pts = frame.pts;
        previous_pts = frame.pts;
        uint32_t hash = 2166136261u;
        for (uint32_t pixel : frame.argb) {
            hash = (hash ^ pixel) * 16777619u;
            nonblack |= (pixel & 0x00ffffffu) != 0;
        }
        changes += frames && hash != previous_hash;
        previous_hash = hash;
        ++frames;
        auto pcm = media.take_audio();
        CHECK_EQ(pcm.size() % size_t(channels), 0u);
        samples += pcm.size();
        for (size_t s = 0; s < pcm.size(); ++s) {
            const size_t ch = s % size_t(channels);
            peaks[ch] = std::max(peaks[ch], std::abs(int(pcm[s])));
            audio_hash[ch] = (audio_hash[ch] ^ uint16_t(pcm[s])) * 16777619u;
        }
    }
    CHECK(frames > 1);
    CHECK(changes > 0);
    CHECK(nonblack);
    CHECK(samples > 0);
    std::sort(costs.begin(), costs.end());
    printf("Movie decoded: %u frames, %dx%d, PTS %.6f..%.6f, %u image changes; "
           "%zu audio samples, %d Hz, %d channels\n",
           frames, frame.width, frame.height, first_pts, previous_pts, changes, samples,
           media.audio_rate(), channels);
    for (int ch = 0; ch < channels; ++ch)
        printf("Channel %d: peak=%d hash=%08x\n", ch, peaks[size_t(ch)], audio_hash[size_t(ch)]);
    if (!costs.empty())
        printf("Movie decode+convert elapsed ms: median=%.3f p95=%.3f max=%.3f; "
               "offline only, no presentation or physical output\n",
               costs[costs.size() / 2], costs[(costs.size() - 1) * 95 / 100], costs.back());
}

// Full-stream timed-decoder exercise. This checks actual media and deliberately
// stops consuming to test bounded backpressure; it does not claim presentation
// timing, speaker routing or a real-time player. Reset and close model loop/skip.
static void test_timed_movie_asset(const char *path) {
    struct TraceFile {
        FILE *file = nullptr;
        ~TraceFile() {
            if (file)
                fclose(file);
        }
    } trace;
    if (const char *target = getenv("RECOMP_TEST_TIMED_TRACE")) {
        trace.file = fopen(target, "w");
        CHECK(trace.file != nullptr);
        if (!trace.file)
            return;
        fprintf(trace.file, "kind,pts,duration,samples,rate,origin,decode_errors\n");
    }
    mf::TimedMedia media;
    std::string why;
    CHECK(media.open(path, {3, 12}, &why));
    if (!why.empty()) {
        printf("Timed movie open: %s\n", why.c_str());
        return;
    }
    using Result = mf::TimedMedia::Result;
    Result status = Result::Progress;
    unsigned pumps = 0;
    while (status == Result::Progress && ++pumps < 10000)
        status = media.pump();
    if (status == Result::Error) {
        printf("Timed queue priming failed: %s\n", media.error().c_str());
        CHECK(false);
        return;
    }
    CHECK(status == Result::Blocked);
    CHECK(media.queued_video() <= 3 && media.queued_audio() <= 12);
    const size_t vq = media.queued_video(), aq = media.queued_audio();
    for (unsigned i = 0; i < 20; ++i) {
        CHECK(media.pump() == Result::Blocked);
        CHECK_EQ(media.queued_video(), vq);
        CHECK_EQ(media.queued_audio(), aq);
    }
    media.pause(true);
    CHECK(media.pump() == Result::Paused);
    CHECK(media.reset(&why));
    CHECK_EQ(media.queued_video(), 0u);
    CHECK_EQ(media.queued_audio(), 0u);
    CHECK(media.pump() == Result::Paused);
    media.pause(false);

    mf::Media legacy;
    CHECK(legacy.open(path, &why));
    mf::VideoFrame video, first_video, old_video;
    mf::AudioFrame audio, first_audio;
    size_t video_count = 0, audio_count = 0, audio_samples = 0;
    double previous_vpts = -1e30, previous_apts = -1e30;
    double max_video_gap = 0, audio_end = 0, video_end = 0;
    std::vector<uint32_t> channel_hash;
    std::vector<int> peaks;
    std::vector<mf::AudioChannel> layout;
    size_t timestamp_origins[3] = {}, damaged_video = 0;
    pumps = 0;
    while (!media.finished() && ++pumps < 2000000) {
        status = media.pump();
        if (status == Result::Error) {
            printf("Timed movie failure: %s\n", media.error().c_str());
            CHECK(false);
            return;
        }
        CHECK(media.queued_video() <= 3 && media.queued_audio() <= 12);
        while (media.pop_video(&video)) {
            damaged_video += video.decode_errors != 0;
            if (trace.file)
                fprintf(trace.file, "video,%.9f,%.9f,0,0,frame,%u\n", video.pts, video.duration,
                        video.decode_errors);
            CHECK(std::isfinite(video.pts) && video.pts >= previous_vpts);
            CHECK(video.duration > 0 && std::isfinite(video.duration));
            CHECK_EQ(video.argb.size(), size_t(video.width) * size_t(video.height));
            if (video_count)
                max_video_gap = std::max(max_video_gap, video.pts - previous_vpts);
            else
                first_video = video;
            if (video_count < 24) {
                CHECK(legacy.next_video(&old_video));
                CHECK(std::abs(video.pts - old_video.pts) < 1e-9);
                CHECK(video.argb == old_video.argb);
                legacy.take_audio();
            }
            previous_vpts = video.pts;
            video_end = video.pts + video.duration;
            ++video_count;
        }
        while (media.pop_audio(&audio)) {
            ++timestamp_origins[size_t(audio.timestamp_origin)];
            if (trace.file)
                fprintf(trace.file, "audio,%.9f,%.9f,%zu,%d,%u,0\n", audio.pts, audio.duration(),
                        audio.samples(), audio.rate, unsigned(audio.timestamp_origin));
            CHECK(std::isfinite(audio.pts) && audio.pts >= previous_apts);
            CHECK(audio.rate > 0 && audio.samples() > 0);
            CHECK_EQ(audio.pcm.size() % audio.layout.size(), 0u);
            if (!audio_count) {
                first_audio = audio;
                layout = audio.layout;
                channel_hash.assign(layout.size(), 2166136261u);
                peaks.assign(layout.size(), 0);
            }
            CHECK(audio.layout == layout);
            for (size_t i = 0; i < audio.pcm.size(); ++i) {
                const size_t ch = i % layout.size();
                channel_hash[ch] = (channel_hash[ch] ^ uint16_t(audio.pcm[i])) * 16777619u;
                peaks[ch] = std::max(peaks[ch], std::abs(int(audio.pcm[i])));
            }
            previous_apts = audio.pts;
            audio_end = audio.pts + audio.duration();
            audio_samples += audio.samples();
            ++audio_count;
        }
    }
    CHECK(media.finished());
    CHECK(media.pump() == Result::Ended);
    CHECK(video_count > 1 && audio_samples > 0);
    printf("Timed movie: video=%zu audio_blocks=%zu audio_samples_per_channel=%zu rate=%d "
           "video_end=%.6f audio_end=%.6f max_video_gap=%.6f pumps=%u\n",
           video_count, audio_count, audio_samples, first_audio.rate, video_end, audio_end,
           max_video_gap, pumps);
    printf(
        "Timed evidence: frame_pts=%zu packet_pts=%zu sample_continuations=%zu damaged_video=%zu\n",
        timestamp_origins[0], timestamp_origins[1], timestamp_origins[2], damaged_video);
    for (size_t ch = 0; ch < layout.size(); ++ch)
        printf("Timed channel %zu %s: peak=%d hash=%08x\n", ch, mf::audio_channel_name(layout[ch]),
               peaks[ch], channel_hash[ch]);

    CHECK(media.reset(&why));
    bool got_video = false, got_audio = false;
    for (unsigned i = 0; i < 10000 && (!got_video || !got_audio); ++i) {
        CHECK(media.pump() != Result::Error);
        while (media.pop_video(&video)) {
            if (!got_video) {
                CHECK(video.pts == first_video.pts);
                CHECK(video.duration == first_video.duration);
                CHECK(video.argb == first_video.argb);
                got_video = true;
            }
        }
        while (media.pop_audio(&audio)) {
            if (!got_audio) {
                CHECK(audio.pts == first_audio.pts);
                CHECK(audio.layout == first_audio.layout);
                CHECK(audio.pcm == first_audio.pcm);
                got_audio = true;
            }
        }
    }
    CHECK(got_video && got_audio);
    media.close(); // skip while decoders are active
    CHECK(media.finished());
    CHECK(!media.pop_video(&video) && !media.pop_audio(&audio));
    CHECK(!media.open(path, {0, 12}, &why));
    CHECK(media.finished());
    printf(
        "Timed decoder checks: bounded queues, full drain, pause, reset/loop, skip, legacy pixels; "
        "no physical output or presentation clock tested\n");
}

static void test_presentation_clock() {
    mf::PresentationClock clock;
    CHECK(clock.observe(0));
    CHECK(clock.observe(.711020408));
    // Reproduce the measured device correction: hold, then recover using the
    // device itself. No audio samples or source timestamps are rewritten.
    CHECK(clock.observe(.707460));
    CHECK(clock.seconds() == .711020408);
    CHECK(clock.observe(.709));
    CHECK(clock.seconds() == .711020408 && clock.corrections() == 2);
    CHECK(clock.max_correction() > .0035 && clock.max_correction() < .0036);
    CHECK(clock.observe(.714));
    CHECK(clock.seconds() == .714);
    CHECK(!clock.observe(.703)); // cannot hide a larger backward jump
    CHECK(clock.seconds() == .714);
    CHECK(!clock.observe(-1));
    CHECK(!clock.observe(INFINITY));
    CHECK(!clock.observe(NAN));
    clock = {}; // explicit movie reset starts a distinct epoch
    CHECK(clock.observe(.002) && clock.seconds() == .002);
    CHECK(clock.corrections() == 0 && clock.max_correction() == 0);
}

int main() {
    // Unbuffered, not line buffered: Windows treats _IOLBF as full buffering
    // and a fail-fast abort drops everything queued, including the name of
    // the test that died.
    setvbuf(stdout, nullptr, _IONBF, 0);
    test_presentation_clock();
    mem_init();
    imports_init();
    dx_register_shims();

    g_stack_top = STACK_TOP - 0x1000;
    g_scratch = heap_alloc(0x4000, true, 16);
    if (!g_scratch) {
        fprintf(stderr, "cannot allocate scratch\n");
        return 1;
    }

    if (const char *path = getenv("RECOMP_TEST_AVI")) {
        test_avi_asset(path);
        return g_failures ? 1 : 0;
    }
    if (const char *path = getenv("RECOMP_TEST_AUDIO")) {
        test_audio_asset(path);
        return g_failures ? 1 : 0;
    }
    if (const char *path = getenv("RECOMP_TEST_MOVIE")) {
        test_movie_asset(path);
        return g_failures ? 1 : 0;
    }
    if (const char *path = getenv("RECOMP_TEST_TIMED_MOVIE")) {
        test_timed_movie_asset(path);
        return g_failures ? 1 : 0;
    }

    struct {
        const char *name;
        void (*fn)();
    } tests[] = {
        {"D3D11 resource bounds", test_d3d11_resource_bounds},
        {"D3D11 quad pixels", test_d3d11_quad_pixels},
        {"D3D11 alpha pixels", test_d3d11_alpha_pixels},
        {"D3D11 texel copy", test_d3d11_texel_copy_software},
        {"D3D11 hardware path", test_d3d11_texel_copy_hardware},
        {"overlapping self-blit", test_overlapping_self_blit},
        {"lock write tracking", test_lock_write_tracking},
        {"D3D11 scaffold", test_d3d11_scaffold},
        {"D3DX math and blob", test_d3dx_math_and_blob},
        {"vtable integrity", test_vtable_integrity},
        {"resolution depth lifetime", test_resolution_depth_lifetime},
        {"gradient, flip, present", test_gradient_flip},
        {"blt and colour key", test_blt_and_colorkey},
        {"retained pointer writes", test_retained_pointer_writes},
        {"retained pointer tail bytes", test_retained_pointer_tail_bytes},
        {"fullscreen swap chain sets the desktop mode",
         test_fullscreen_swapchain_sets_the_desktop_mode},
        {"display ABI", test_display_abi},
        {"record and coverage", test_record_basic_and_coverage},
        {"keyed blit coverage", test_keyed_blit_coverage_and_key_values},
        {"fill, upload, flags", test_fill_upload_and_flags},
        {"revisions and leases", test_revision_bumps_and_retained_lease},
        {"frame sealing", test_seal_events},
        {"unchanged texture uploads", test_unchanged_texture_uploads},
        {"frame command pool", test_frame_command_pool},
        {"release frees leases", test_frame_release_frees_leases},
        {"presenter seal and retire", test_presenter_seal_hook_and_retirement_queue},
        {"screen class", test_screen_class},
        {"access counts", test_access_counts_by_reason},
        {"pad callback defaults", test_host_pad_defaults},
        {"cursor learned", test_cursor_surface_learned},
        {"palette-only frames", test_palette_only_frames_seal},
        {"offscreen flip", test_offscreen_flip_does_not_seal},
        {"v4 unlock rect", test_v4_unlock_takes_a_rect},
        {"lock write records", test_lock_write_records},
        {"lock clusters", test_lock_diff_partial_records_and_payload},
        {"DC write diff", test_getdc_releasedc},
        {"GDI primary blit", test_gdi_primary_blit},
        {"CoCreateInstance DirectSound", test_cocreate_directsound},
        {"DirectShow audio stream", test_dshow_audio_stream},
        {"DirectShow graph playback", test_dshow_graph_playback},
        {"palette versions", test_palette_versions},
        {"storage generations", test_storage_generations},
        {"draw snapshot is deep", test_draw_snapshot_is_deep},
        {"no reader/no readback", test_no_reader_no_readback},
        {"first HUD/overlay", test_first_hud_boundary_and_overlay_pass},
        {"overlay mapping", test_overlay_mapping_rule},
        {"interleave fallback", test_inexpressible_interleave_triggers_legacy_replay},
        {"scene/HUD intersection", test_later_scene_overlay_intersects_hud},
        {"HUD exclusions/groups", test_hud_rule_exclusions_and_grouping},
        {"projected UI containment", test_transformed_overlay_containment},
        {"dirty readers", test_each_reader_reads_back_only_dirty},
        {"contained dirty draws", test_contained_dirty_draws},
        {"flip dirty transfer", test_flip_transfers_dirty_region},
        {"draw and blit order", test_draw_and_blit_share_one_order},
        {"clear recorded", test_clear_is_recorded_with_its_rects},
        {"draw screen bounds", test_draw_screen_bounds},
        {"draw leases texture", test_draw_leases_its_texture_revision},
        {"texture handle lifetime", test_texture_handle_lifetime},
        {"texture load counted", test_texture_load_is_counted},
        {"upload paths bump", test_every_upload_path_bumps_the_revision},
        {"draw uploads a rev", test_draw_uploads_a_revision_the_renderer_lacks},
        {"palette bumps a tex", test_palette_write_bumps_a_texture_revision},
        {"revisions are unique", test_revisions_are_unique_across_surfaces},
        {"dinput notification", test_dinput_event_notification},
        {"dinput create W and Ex", test_dinput_create_w},
        {"mouse motion survives", test_mouse_motion_survives_keyboard_poll},
        {"dinput8 keyboard action map", test_dinput8_keyboard_action_map},
        {"unchanged state buffered", test_unchanged_state_produces_no_buffered_event},
        {"re-attach a palette", test_setpalette_self},
        {"colour key at 16 bpp", test_colorkey_16bpp},
        {"DirectDrawCreateEx fallback", test_directdraw_create_ex_fallback},
        {"QueryInterface", test_query_interface},
        {"display modes", test_enum_display_modes},
        {"DirectDraw enumeration", test_directdraw_enumeration},
        {"exclusive DirectDraw window mode", test_exclusive_ddraw_notifies_window_mode},
        {"release restores desktop", test_release_restores_desktop_mode},
        {"configurable modes", test_configurable_display_modes},
        {"Classic probe surfaces", test_classic_probe_surface_creation},
        {"Direct3D pipeline", test_d3d_pipeline},
        {"Direct3D3 pipeline", test_d3d3_pipeline},
        {"DirectSound", test_dsound},
        {"DirectSound device release ends its buffers", test_dsound_device_release},
        {"DirectInput", test_dinput},
        {"QMixer", test_qmixer},
        {"FMOD samples", test_fmod},
        {"FMOD streams", test_fmod_stream},
        {"Soundlib MIDI", test_soundlib_stub},
        {"Miles arities", test_mss32_arities},
        {"RIFF WAVE", test_riff_parse},
        {"Miles samples", test_mss32_sample},
        {"Legacy audio", test_legacy_audio},
        {"AVI reader", test_avi_reader},
        {"Miles streams", test_mss32_stream},
        {"Bink/Smacker stubs", test_bink_smack_stubs},
        {"Bink entry points resolve", test_bink_entry_points_resolve},
        {"video frame conversion", test_video_frame_convert},
        {"Bink play", test_bink_play},
        {"Bink open from handle", test_bink_open_from_handle},
        {"Bink handle flag errors", test_bink_handle_flag_errors},
        {"Bink rects and pause", test_bink_rects_and_pause},
        {"Bink audio without service", test_bink_audio_without_service},
        {"Bink shutdown with open player", test_bink_shutdown_with_open_player},
        {"weanetr", test_weanetr},
        {"reference counts", test_refcounts},
        {"SDK record sizes", test_sdk_abi},
        {"SetRenderTarget self", test_setrendertarget_same_surface},
        {"FindDevice, literal", test_find_device_literal},
        {"viewport record", test_viewport_record},
        {"GetDeviceData stride", test_device_data_stride16},
        {"GetClipStatus canary", test_clipstatus_canary},
        {"overflow rejection", test_overflow_rejection},
        {"identity and parent", test_identity_and_parent},
        {"duplicate PCM lifetime", test_dsound_duplicate_lifetime},
        {"SetSurfaceDesc owner", test_setsurfacedesc_ownership},
        {"QMixer wave failure", test_qmixer_failure},
        {"partial lock, edges", test_partial_lock_bottom_edge},
        {"flip ownership", test_flip_ownership},
        {"out-pointer guards", test_out_pointer_guards},
        {"DirectSound data path", test_dsound_data_path},
        {"audio formats", test_dsound_formats},
        {"DirectSound notify", test_dsound_notify},
        {"DirectSound streaming", test_dsound_stream},
        {"FMV refill gate", test_dsound_stream_pacing},
        {"QMixer streaming", test_qmixer_streaming},
        {"QMixer channels", test_qmixer_channels},
        {"QMixer frame pump", test_qmixer_frame_pump},
        {"QMixer volume scale", test_qmixer_volume_scale},
        {"QMixer stream lifetime", test_qmixer_stream_buffer_lifetime},
        {"QMixer stream prefetch", test_qmixer_stream_prefetch},
        {"QMixer refill gate", test_qmixer_refill_gate},
        {"blit into a texture", test_blit_into_texture_uploads},
        {"palettised texture", test_palettised_texture_upload},
        {"texture content hash", test_texture_content_hash},
        {"texture hash row span", test_texture_hash_covers_the_whole_row},
        {"texture hash padding", test_texture_hash_covers_padding_bytes},
        {"texture formats", test_texture_formats},
        {"colour control", test_color_control},
        {"FourCC is a format error", test_fourcc_is_a_pixel_format_error},
        {"Media Foundation session", test_media_foundation_session},
#ifdef RECOMP_HAVE_FFMPEG
        // Without video decoding (the MSVC-ABI build) no file opens as a source.
        {"Media Foundation topology ready", test_media_foundation_topology_ready},
#endif
        {"dx_reset", test_reset},
    };
    for (auto &t : tests) {
        int before = g_failures;
        t.fn();
        printf("%-26s %s\n", t.name, g_failures == before ? "ok" : "FAILED");
    }

    printf("\n%d checks, %d failures\n", g_checks, g_failures);
    if (g_failures == 0)
        printf("all dx tests passed\n");
    return g_failures ? 1 : 0;
}
