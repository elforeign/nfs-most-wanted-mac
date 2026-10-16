// Small, synthetic effects exercise the real guest ABI and CTAB binding.
// No game files, window, physical controller or sound device are involved.
#include <sys/resource.h>
#include "../dx.h"
#include "../d3d9_pipeline.h"
#include "../host_d9.h"
#include "../dds_volume.h"
#include "../d3d9_msaa.h"
#include "../../runtime/memory.h"
#include "guest_abi.h"
#include "../../host/gpu/metal/foundation.h"   // run 14 foundation F1/F3 (pure C++ parts)
#include "../../host/gpu/metal/smoke_light.h"   // run 14 lit smoke (pure C++ parts)
#include "../../host/gpu/metal/atmosphere.h"   // run 14 B1 height fog (pure C++ twin)
#include "../../host/gpu/metal/surface.h"   // run 15 Wave B WB1b surface attachments (pure C++ parts)

#include <array>
#include <string>
#include <unordered_map>
#include <algorithm>

#define CHECK(c)                                                                                   \
    do {                                                                                           \
        ++g_checks;                                                                                \
        if (!(c)) {                                                                                \
            ++g_failures;                                                                          \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #c);                           \
        }                                                                                          \
    } while (0)

static HostD9Draw captured;
static void test_volume_dds() {
    std::vector<uint8_t> b(128 + (4 * 2 * 8 + 2 * 1 * 4 + 1 * 1 * 2 + 1) * 4, 0);
    auto put = [&](unsigned word, uint32_t value) {
        for (unsigned i = 0; i < 4; ++i)
            b[word * 4 + i] = uint8_t(value >> (8 * i));
    };
    const uint32_t header[32] = {0x20534444, 124,  0x821007,   2,        4,        0, 8,  4,
                                 0,          0,    0,          0,        0,        0, 0,  0,
                                 0,          0,    0,          32,       0x41,     0, 32, 0xff0000,
                                 0xff00,     0xff, 0xff000000, 0x40100a, 0x200000, 0, 0,  0};
    for (unsigned i = 0; i < 32; ++i)
        put(i, header[i]);
    for (size_t i = 128; i < b.size(); ++i)
        b[i] = uint8_t(i * 17);
    ddsvolume::View v;
    CHECK(ddsvolume::parse(b.data(), b.size(), v));
    CHECK(v.width == 4 && v.height == 2 && v.depth == 8 && v.levels == 4 && v.format == 21);
    CHECK(v.mip[1].width == 2 && v.mip[1].height == 1 && v.mip[1].depth == 4);
    CHECK(v.mip[3].offset == b.size() - 4 && v.mip[3].bytes == 4);
    CHECK(v.mip[0].row_pitch == 16 && v.mip[0].slice_pitch == 32);
    CHECK(!ddsvolume::parse(nullptr, b.size(), v));
    for (size_t n = 0; n < b.size(); ++n) {
        CHECK(!ddsvolume::parse(b.data(), n, v));
        CHECK(v.levels == 0 && v.mip[0].bytes == 0);
    }
    b.push_back(0);
    CHECK(!ddsvolume::parse(b.data(), b.size(), v));
    b.pop_back();
    for (auto [word, value] : std::initializer_list<std::pair<unsigned, uint32_t>>{
             {0, 0},   {1, 123},     {2, 0x21007},   {2, 0x821007 | 0x80000},
             {3, 0},   {4, 513},     {6, 0},         {7, 0},
             {7, 5},   {19, 31},     {20, 4},        {21, 0x30315844},
             {22, 24}, {23, 0xff},   {24, 0xff},     {25, 0xff0000},
             {26, 0},  {27, 0x1008}, {27, 0x400008}, {28, 0x200200},
             {29, 1},  {30, 1},      {31, 1},        {5, 16}}) {
        put(word, value);
        CHECK(!ddsvolume::parse(b.data(), b.size(), v));
        CHECK(v.width == 0 && v.mip[0].bytes == 0);
        put(word, header[word]);
    }
    put(2, header[2] | 8);
    put(5, 16);
    CHECK(ddsvolume::parse(b.data(), b.size(), v));
    put(5, 20); // unsupported padded rows cannot be silently read as packed
    CHECK(!ddsvolume::parse(b.data(), b.size(), v));
    put(2, header[2]);
    put(5, 0);
    put(20, 0x40);
    put(26, 0);
    CHECK(ddsvolume::parse(b.data(), b.size(), v) && v.format == 22);
    put(3, 512);
    put(4, 512);
    put(6, 512);
    CHECK(!ddsvolume::parse(b.data(), b.size(), v)); // reject budget before pointer arithmetic
    put(3, 0xffffffff);
    put(4, 0xffffffff);
    put(6, 0xffffffff);
    CHECK(!ddsvolume::parse(b.data(), b.size(), v));
    // A valid single-mip non-cubic texture without mip-count metadata.
    b.resize(128 + 3 * 1 * 5 * 4);
    put(2, 0x801007);
    put(3, 1);
    put(4, 3);
    put(6, 5);
    put(7, 0);
    put(27, 0x1008);
    CHECK(ddsvolume::parse(b.data(), b.size(), v) && v.levels == 1);
    put(7, 2);
    CHECK(!ddsvolume::parse(b.data(), b.size(), v));
}
static unsigned draws = 0, rejected_draws = 0;
static bool admission_enabled = false;
static HostD9Draw rejected;
extern "C" void host_d9_admission_entry(uint64_t) {}
extern "C" int host_d9_admission_enabled(void) {
    return admission_enabled;
}
extern "C" void host_d9_rejected_draw(const HostD9Draw *d) {
    rejected = *d;
    ++rejected_draws;
}
extern "C" int host_d9_active(void) {
    return 1;
}
extern "C" void host_d9_draw(const HostD9Draw *d) {
    captured = *d;
    ++draws;
}

struct VolumeUpload {
    uint32_t id, slice, level, pitch;
    std::vector<uint8_t> data;
};
static std::unordered_map<uint32_t, HostD9TextureDesc> defined_textures;
static std::vector<VolumeUpload> volume_uploads;
static std::vector<uint32_t> dropped_textures;
extern "C" void host_d9_texture_define(const HostD9TextureDesc *d) {
    defined_textures[d->id] = *d;
}
extern "C" void host_d9_texture_upload(uint32_t id, uint32_t slice, uint32_t level,
                                       const uint8_t *bytes, uint32_t pitch) {
    const auto it = defined_textures.find(id);
    if (it == defined_textures.end() || it->second.kind != HOST_D9_TEX_VOLUME)
        return;
    const uint32_t h = std::max(it->second.height >> level, 1u);
    const auto *p = static_cast<const uint8_t *>(bytes);
    volume_uploads.push_back({id, slice, level, pitch, {p, p + pitch * h}});
}
extern "C" void host_d9_texture_drop(uint32_t id) {
    dropped_textures.push_back(id);
    defined_textures.erase(id);
}
static uint32_t make_volume(uint32_t dev, uint32_t pool = 1, uint32_t fmt = 21, uint32_t usage = 0,
                            uint32_t w = 2, uint32_t h = 2, uint32_t d = 2, uint32_t levels = 0) {
    CHECK(call_method(dev, 24, {w, h, d, levels, usage, fmt, pool, sc(0x600), 0}) == 0);
    CHECK(rd32(sc(0x600)) != 0);
    return rd32(sc(0x600));
}
static void test_volume(uint32_t dev) {
    constexpr uint32_t invalid = 0x8876086c;
    const uint32_t tex = make_volume(dev);
    const uint32_t id = com_this(tex)->id;
    CHECK(call_method(tex, 10) == 4); // D3DRTYPE_VOLUMETEXTURE
    CHECK(call_method(tex, 13) == 2);
    CHECK(call_method(tex, 17, {0, sc(0x620)}) == 0);
    const uint32_t expected[] = {21, 2, 0, 1, 2, 2, 2};
    CHECK(!memcmp(gm_ptr(sc(0x620)), expected, sizeof expected));
    CHECK(call_method(tex, 17, {1, sc(0x620)}) == 0);
    CHECK(rd32(sc(0x630)) == 1 && rd32(sc(0x634)) == 1 && rd32(sc(0x638)) == 1);
    CHECK(call_method(tex, 19, {0, sc(0x660), 0, 0}) == 0);
    CHECK(rd32(sc(0x660)) == 8 && rd32(sc(0x664)) == 16);
    const uint32_t data = rd32(sc(0x668));
    for (uint32_t i = 0; i < 32; ++i)
        gm_ptr(data)[i] = uint8_t(i);
    CHECK(call_method(tex, 19, {0, sc(0x680), 0, 0}) == invalid); // no nested lock
    CHECK(call_method(tex, 20, {0}) == 0);
    CHECK(call_method(tex, 20, {0}) == invalid);
    volume_uploads.clear();
    call_method(tex, 9); // PreLoad uploads canonical storage
    CHECK(defined_textures.at(id).depth == 2 && defined_textures.at(id).levels == 2);
    CHECK(volume_uploads.size() == 3);
    for (uint32_t z = 0; z < 2; ++z) {
        const auto &u = volume_uploads[z];
        CHECK(u.id == id && u.slice == z && u.level == 0 && u.pitch == 8 && u.data.size() == 16);
        for (uint32_t i = 0; i < 16; ++i)
            CHECK(u.data[i] == uint8_t(z * 16 + i));
    }
    volume_uploads.clear();
    call_method(tex, 9);
    CHECK(volume_uploads.empty()); // no duplicate unchanged upload
    // A non-origin sub-box returns full pitches and an offset into the same mip.
    const uint32_t box[] = {1, 1, 2, 2, 1, 2};
    memcpy(gm_ptr(sc(0x700)), box, sizeof box);
    CHECK(call_method(tex, 19, {0, sc(0x660), sc(0x700), 0x8000}) == 0);
    CHECK(rd32(sc(0x660)) == 8 && rd32(sc(0x664)) == 16);
    CHECK(rd32(rd32(sc(0x668))) == 0x1f1e1d1c);
    wr32(rd32(sc(0x668)), 0xabcdef01);
    CHECK(call_method(tex, 20, {0}) == 0);
    call_method(tex, 9);
    CHECK(volume_uploads.empty()); // NO_DIRTY_UPDATE defers publication
    CHECK(call_method(tex, 21, {sc(0x700)}) == 0);
    call_method(tex, 9);
    CHECK(volume_uploads.size() == 3);
    CHECK(volume_uploads[1].data[12] == 1 && volume_uploads[1].data[15] == 0xab);
    volume_uploads.clear();
    CHECK(call_method(tex, 19, {0, sc(0x660), sc(0x700), 0x10}) == 0);
    wr32(rd32(sc(0x668)), 0); // illegal guest write through READONLY cannot publish
    CHECK(call_method(tex, 20, {0}) == 0);
    CHECK(call_method(tex, 21, {0}) == 0);
    call_method(tex, 9);
    CHECK(volume_uploads[1].data[15] == 0xab);
    // Fail before writing outputs or mutating storage for bad extents/pointers/flags.
    for (uint32_t level : {2u, 0xffffffffu}) {
        wr32(sc(0x660), 0xdeadbeef);
        CHECK(call_method(tex, 17, {level, sc(0x660)}) == invalid);
        CHECK(rd32(sc(0x660)) == 0xdeadbeef);
        CHECK(call_method(tex, 19, {level, sc(0x660), 0, 0}) == invalid);
        CHECK(rd32(sc(0x660)) == 0xdeadbeef);
    }
    for (uint32_t off : {0u, 4u, 8u, 12u, 16u, 20u}) {
        memcpy(gm_ptr(sc(0x700)), box, sizeof box);
        wr32(sc(0x700) + off, 0xffffffffu);
        CHECK(call_method(tex, 19, {0, sc(0x660), sc(0x700), 0}) == invalid);
    }
    for (uint32_t ptr : {0u, 0xfffffffcu}) {
        CHECK(call_method(tex, 19, {0, ptr, 0, 0}) == invalid);
        CHECK(call_method(tex, 17, {0, ptr}) == invalid);
        CHECK(call_method(tex, 18, {0, ptr}) == invalid);
    }
    CHECK(call_method(tex, 19, {0, sc(0x660), 0xfffffffcu, 0}) == invalid);
    for (uint32_t flags : {0x1000u, 0x2000u, 0x2010u, 0xffffffffu})
        CHECK(call_method(tex, 19, {0, sc(0x660), 0, flags}) == invalid);
    CHECK(call_method(tex, 11, {99}) == 0);
    CHECK(call_method(tex, 12) == 1);
    CHECK(call_method(tex, 11, {0}) == 1);
    CHECK(call_method(tex, 7, {19}) == 0 && call_method(tex, 8) == 19);
    // Prefix interface identity, a real mip interface and parent lifetime.
    const uint32_t base_iid[] = {0x580ca87e, 0x4d541d3c, 0xd3b71d99, 0xce98c2e3};
    const uint32_t tex_iid[] = {0x2518526c, 0x4111e789, 0xef47b9a7, 0xe6138d32};
    const uint32_t vol_iid[] = {0x24f416e6, 0x4aa71f67, 0x3fd38eb8, 0xa128316f};
    memcpy(gm_ptr(sc(0x740)), base_iid, 16);
    CHECK(call_method(tex, 0, {sc(0x740), sc(0x600)}) == 0);
    CHECK(rd32(sc(0x600)) == tex);
    CHECK(call_method(tex, 2) == 1);
    CHECK(call_method(tex, 18, {0, sc(0x600)}) == 0);
    const uint32_t mip = rd32(sc(0x600)), mip_id = com_this(mip)->id;
    CHECK(com_this(tex)->refs == 2);
    CHECK(call_method(mip, 8, {sc(0x620)}) == 0);
    CHECK(!memcmp(gm_ptr(sc(0x620)), expected, sizeof expected));
    memcpy(gm_ptr(sc(0x740)), vol_iid, 16);
    CHECK(call_method(mip, 0, {sc(0x740), sc(0x600)}) == 0);
    CHECK(rd32(sc(0x600)) == mip);
    CHECK(call_method(mip, 2) == 1);
    memcpy(gm_ptr(sc(0x740)), tex_iid, 16);
    CHECK(call_method(mip, 7, {sc(0x740), sc(0x600)}) == 0);
    CHECK(rd32(sc(0x600)) == tex);
    CHECK(call_method(tex, 2) == 2);
    memset(gm_ptr(sc(0x740)), 0xaa, 16);
    CHECK(call_method(mip, 7, {sc(0x740), sc(0x600)}) == 0x80004002u);
    CHECK(rd32(sc(0x600)) == 0);
    CHECK(call_method(tex, 2) == 1); // external mip alone now keeps the resource alive
    CHECK(call_method(mip, 9, {sc(0x660), 0, 0x10}) == 0);
    CHECK(gm_ptr(rd32(sc(0x668)))[31] == 0xab);
    CHECK(call_method(mip, 10) == 0);
    CHECK(call_method(mip, 2) == 0);
    CHECK(!com_get(id) && !com_get(mip_id));
    CHECK(!defined_textures.count(id));
    // The longest dimension determines mip count, even when it is depth.
    const uint32_t deep = make_volume(dev, 1, 28, 0, 1, 1, 4);
    CHECK(call_method(deep, 13) == 3);
    CHECK(call_method(deep, 19, {2, sc(0x660), 0, 0}) == 0);
    CHECK(rd32(sc(0x660)) == 1 && rd32(sc(0x664)) == 1);
    CHECK(call_method(deep, 20, {2}) == 0);
    call_method(deep, 2);
    for (const auto dims : {std::array<uint32_t, 3>{0, 2, 2},
                            {2, 0, 2},
                            {2, 2, 0},
                            {513, 2, 2},
                            {2, 513, 2},
                            {2, 2, 513},
                            {512, 512, 512},
                            {0xffffffffu, 0xffffffffu, 0xffffffffu}}) {
        wr32(sc(0x600), 0xdeadbeef);
        CHECK(call_method(dev, 24, {dims[0], dims[1], dims[2], 0, 0, 21, 1, sc(0x600), 0}) ==
              invalid);
        CHECK(rd32(sc(0x600)) == 0);
    }
    for (const auto args : {std::array<uint32_t, 4>{3, 0, 21, 1},
                            {0, 1, 21, 1},
                            {0, 2, 21, 1},
                            {0, 0x400, 21, 1},
                            {0, 0x200, 21, 1},
                            {0, 0, 75, 1},
                            {0, 0, 0x31545844, 1},
                            {0, 0, 21, 4}}) {
        CHECK(call_method(dev, 24, {2, 2, 2, args[0], args[1], args[2], args[3], sc(0x600), 0}) ==
              invalid);
        CHECK(rd32(sc(0x600)) == 0);
    }
    // Default-pool dynamic locks and system-memory to GPU updates are distinct paths.
    const uint32_t dyn = make_volume(dev, 0, 22, 0x200);
    CHECK(call_method(dyn, 19, {0, sc(0x660), 0, 0x2000}) == 0);
    CHECK(call_method(dyn, 20, {0}) == 0);
    call_method(dyn, 2);
    const uint32_t src = make_volume(dev, 2), dst = make_volume(dev, 0);
    const uint32_t dst_id = com_this(dst)->id;
    CHECK(call_method(dst, 19, {0, sc(0x660), 0, 0}) == invalid);
    CHECK(call_method(dev, 65, {0, src}) == invalid); // system-memory resources cannot be sampled
    CHECK(call_method(src, 19, {0, sc(0x660), 0, 0}) == 0);
    memset(gm_ptr(rd32(sc(0x668))), 0x71, 32);
    CHECK(call_method(dev, 31, {src, dst}) == invalid); // locked resource, no partial update
    CHECK(call_method(src, 20, {0}) == 0);
    CHECK(call_method(dev, 31, {src, dst}) == 0);
    volume_uploads.clear();
    call_method(dst, 9);
    CHECK(volume_uploads.size() == 3 && volume_uploads[0].data[0] == 0x71);
    CHECK(call_method(dev, 65, {0, dst}) == 0);
    const uint8_t decl[] = {0, 0, 0, 0, 2, 0, 0, 0, 0xff, 0, 0, 0, 17, 0, 0, 0};
    memcpy(gm_ptr(sc(0x300)), decl, sizeof decl);
    CHECK(call_method(dev, 86, {sc(0x300), sc(0x600)}) == 0);
    const uint32_t declaration = rd32(sc(0x600));
    CHECK(call_method(dev, 87, {declaration}) == 0);
    const float tri[] = {-1, 1, .5f, 3, 1, .5f, -1, -3, .5f};
    memcpy(gm_ptr(sc(0x400)), tri, sizeof tri);
    CHECK(call_method(dev, 83, {4, 1, sc(0x400), 12}) == 0);
    CHECK(captured.sampler_texture[0] == dst_id);
    call_method(declaration, 2);
    CHECK(call_method(dev, 64, {0, sc(0x600)}) == 0);
    CHECK(rd32(sc(0x600)) == dst);
    call_method(dst, 2);             // returned GetTexture reference
    CHECK(call_method(dst, 2) == 1); // device binding alone retains it
    CHECK(com_get(dst_id));
    CHECK(call_method(dev, 65, {0, 0}) == 0);
    CHECK(!com_get(dst_id));
    call_method(src, 2);
}

struct Bytes {
    std::vector<uint8_t> data;
    uint32_t words(std::initializer_list<uint32_t> list) {
        const uint32_t start = uint32_t(data.size());
        for (uint32_t x : list)
            for (unsigned k = 0; k < 4; ++k)
                data.push_back(uint8_t(x >> (k * 8)));
        return start;
    }
    uint32_t raw(const std::vector<uint8_t> &b) {
        const uint32_t start = uint32_t(data.size());
        data.insert(data.end(), b.begin(), b.end());
        while (data.size() % 4)
            data.push_back(0);
        return start;
    }
    uint32_t text(const char *s) {
        uint32_t start = words({uint32_t(strlen(s) + 1)});
        raw(std::vector<uint8_t>(s, s + strlen(s) + 1));
        return start;
    }
};

// Hand-written CTAB: one BOOL parameter, independent register index in each
// shader. These bytes are a test fixture, not extracted proprietary assets.
static std::vector<uint8_t> shader(bool vertex, const char *name, uint32_t index, uint32_t count) {
    const uint32_t version = vertex ? 0xfffe0200u : 0xffff0200u;
    Bytes table;
    table.words({28, 0, version, 1, 28, 0, 0});
    table.words({64, index << 16, count, 48, 0});
    table.words({1u << 16, 0x00010001u, count, 0});
    table.raw(std::vector<uint8_t>(name, name + strlen(name) + 1));
    Bytes out;
    out.words({version, 0xfffeu | uint32_t((table.data.size() + 4) / 4) << 16, 0x42415443u});
    out.raw(table.data);
    if (vertex)
        out.words({0x0200001fu, 0x80000000u, 0x900f0000u, 0x02000001u, 0xc00f0000u, 0x90e40000u});
    else
        out.words({0x02000001u, 0x800f0800u, 0xa0e40000u});
    out.words({0xffffu});
    return out.data;
}

static std::vector<uint8_t> effect() {
    Bytes b;
    b.words({0}); // offset 0 is the null name
    const uint32_t sn = b.text("Switch"), mn = b.text("Many"), tn = b.text("Main"),
                   pn = b.text("P0");
    const uint32_t st = b.words({1, 0, sn, 0, 0, 1, 1});
    const uint32_t mt = b.words({1, 0, mn, 0, 2, 1, 1});
    const uint32_t sv = b.words({0}), mv = b.words({0, 1});
    const uint32_t vt = b.words({16, 4, 0, 0, 0}), pt = b.words({15, 4, 0, 0, 0});
    const uint32_t vi = b.words({1}), pi = b.words({2});
    const uint32_t start = b.words({2, 1, 0, 2});
    b.words({st, sv, 0, 0, mt, mv, 0, 0});
    b.words({tn, 0, 1, pn, 0, 2, 146, 0, vt, vi, 147, 0, pt, pi});
    b.words({2, 0}); // two shader objects, no extra resources
    for (bool vertex : {true, false}) {
        auto code = shader(vertex, vertex ? "Switch" : "Many", vertex ? 15 : 0, vertex ? 1 : 2);
        b.words({vertex ? 1u : 2u, uint32_t(code.size())});
        b.raw(code);
    }
    Bytes out;
    out.words({0xfeff0901u, start});
    out.raw(b.data);
    return out.data;
}

// Bounded readback (GetRenderTargetData + LockRect DONOTWAIT) against a scripted renderer.
static int rb_ready = 0, rb_sync_reads = 0, rb_forgets = 0, rb_requests = 0;
static uint32_t rb_ticket = 0, rb_source = 0;
extern "C" void host_d9_texture_read_async(uint32_t id, uint32_t, uint32_t, uint32_t ticket) {
    rb_source = id;
    rb_ticket = ticket;
    rb_ready = 0;
    ++rb_requests;
}
extern "C" int host_d9_texture_read_poll(uint32_t ticket, uint8_t *b, uint32_t pitch, uint32_t w,
                                         uint32_t h) {
    if (ticket != rb_ticket)
        return -1;
    if (!rb_ready)
        return 0;
    for (uint32_t y = 0; y < h; ++y)
        for (uint32_t x = 0; x < w; ++x) {
            const uint32_t v = 0x11000000u + y * 16 + x;
            memcpy(b + (size_t)y * pitch + x * 4, &v, 4);
        }
    return 1;
}
extern "C" void host_d9_texture_read_forget(uint32_t) {
    ++rb_forgets;
}
extern "C" int host_d9_texture_read(uint32_t, uint32_t, uint32_t, uint8_t *b, uint32_t pitch) {
    ++rb_sync_reads;
    memset(b, 0x55, (size_t)pitch * 4);
    return 1;
}
static void test_state_getters_and_readback(uint32_t dev) {
    constexpr uint32_t invalid = 0x8876086c, still_drawing = 0x8876021c, not_found = 0x88760866;
    // Render and sampler states read back what was set, and the documented defaults otherwise.
    CHECK(call_method(dev, 57, {27, 1}) == 0);
    CHECK(call_method(dev, 58, {27, sc(0x10)}) == 0 && rd32(sc(0x10)) == 1);
    CHECK(call_method(dev, 58, {22, sc(0x10)}) == 0 && rd32(sc(0x10)) == 3); // CULLMODE ccw
    CHECK(call_method(dev, 58, {300, sc(0x10)}) == invalid);
    CHECK(call_method(dev, 69, {0, 5, 2}) == 0);
    CHECK(call_method(dev, 68, {0, 5, sc(0x10)}) == 0 && rd32(sc(0x10)) == 2);
    CHECK(call_method(dev, 68, {16, 5, sc(0x10)}) == invalid);
    // Render targets: a referenced surface for 0, NOTFOUND for an unset one.
    CHECK(call_method(dev, 38, {0, sc(0x1c)}) == 0);
    const uint32_t original_target = rd32(sc(0x1c));
    CHECK(original_target != 0);
    CHECK(call_method(dev, 28, {4, 4, 21, 0, 0, 0, sc(0x20), 0}) == 0);
    const uint32_t rt = rd32(sc(0x20));
    CHECK(rt != 0);
    CHECK(call_method(dev, 37, {0, rt}) == 0);
    CHECK(call_method(dev, 38, {0, sc(0x24)}) == 0 && com_this(rd32(sc(0x24))) == com_this(rt));
    call_method(rd32(sc(0x24)), 2);
    wr32(sc(0x24), 0x1234);
    CHECK(call_method(dev, 38, {1, sc(0x24)}) == not_found && rd32(sc(0x24)) == 0);
    // The vertex declaration: the one set, null after clearing it.
    const uint8_t decl[] = {0, 0, 0, 0, 3, 0, 0, 0, 0xff, 0, 0, 0, 17, 0, 0, 0};
    memcpy(gm_ptr(sc(0x300)), decl, sizeof decl);
    CHECK(call_method(dev, 86, {sc(0x300), sc(0x28)}) == 0);
    const uint32_t d = rd32(sc(0x28));
    CHECK(call_method(dev, 87, {d}) == 0);
    CHECK(call_method(dev, 88, {sc(0x2c)}) == 0 && com_this(rd32(sc(0x2c))) == com_this(d));
    call_method(rd32(sc(0x2c)), 2);
    CHECK(call_method(dev, 87, {0}) == 0);
    CHECK(call_method(dev, 88, {sc(0x2c)}) == 0 && rd32(sc(0x2c)) == 0);
    call_method(d, 2);
    // GetRenderTargetData queues the copy; a DONOTWAIT lock answers WASSTILLDRAWING until the
    // renderer has it, then locks the delivered image. A lock without the flag waits.
    CHECK(call_method(dev, 36, {4, 4, 21, 2, sc(0x30), 0}) == 0);
    const uint32_t sys = rd32(sc(0x30));
    CHECK(call_method(dev, 32, {rt, sys}) == 0);
    CHECK(rb_requests == 1 && rb_ticket == com_this(sys)->id && rb_source == com_this(rt)->id);
    CHECK(call_method(sys, 13, {sc(0x40), 0, 0x4000}) == still_drawing);
    CHECK(call_method(sys, 13, {sc(0x40), 0, 0x4010}) == still_drawing);
    rb_ready = 1;
    CHECK(call_method(sys, 13, {sc(0x40), 0, 0x4010}) == 0);
    const uint32_t pitch = rd32(sc(0x40)), bits = rd32(sc(0x44));
    CHECK(pitch >= 16 && rd32(bits) == 0x11000000u && rd32(bits + pitch + 4) == 0x11000011u);
    CHECK(call_method(sys, 14) == 0);
    const int forgets = rb_forgets;
    CHECK(call_method(sys, 13, {sc(0x40), 0, 0x4000}) == 0); // nothing pending: plain lock
    CHECK(call_method(sys, 14) == 0 && rb_forgets == forgets);
    CHECK(call_method(dev, 32, {rt, sys}) == 0 && rb_requests == 2);
    CHECK(call_method(sys, 13, {sc(0x40), 0, 0}) == 0 && rb_sync_reads == 1);
    CHECK(rd32(rd32(sc(0x44))) == 0x55555555u);
    CHECK(call_method(sys, 14) == 0);
    // Sizes must match.
    CHECK(call_method(dev, 36, {8, 4, 21, 2, sc(0x34), 0}) == 0);
    const uint32_t wide = rd32(sc(0x34));
    CHECK(call_method(dev, 32, {rt, wide}) == invalid && rb_requests == 2);
    call_method(wide, 2);
    CHECK(call_method(dev, 32, {rt, sys}) == 0 && rb_requests == 3);
    call_method(sys, 2); // destroyed while pending: the ticket is forgotten
    CHECK(rb_forgets > forgets);
    CHECK(call_method(dev, 37, {0, original_target}) == 0);
    call_method(original_target, 2);
    call_method(rt, 2);
}


// Test155: the four draw calls refuse a primitive count above the MaxPrimitiveCount this device reports in its caps
// (and indexed draws a vertex count above MaxVertexIndex + 1) with D3DERR_INVALIDCALL, before any size arithmetic,
// for every topology, and without growing the process. At the limit they are accepted.
static void test_draw_limits(uint32_t dev) {
    const uint32_t invalid = 0x8876086cu;
    CHECK(call_method(dev, 7, {sc(0x800)}) == 0); // GetDeviceCaps
    const uint32_t max_prims = rd32(sc(0x800) + 180), max_index = rd32(sc(0x800) + 184);
    CHECK(max_prims == 1048575 && max_index == 1048575);
    memset(gm_ptr(sc(0x1000)), 0, 0x1000); // degenerate vertices and indices
    struct rusage before{}, after{};
    getrusage(RUSAGE_SELF, &before);
    for (uint32_t prim = 1; prim <= 6; ++prim) {
        for (uint32_t count : {max_prims + 1, 0x7fffffffu, 0x80000000u, 0x80000000u + 350u, 0xffffffffu}) {
            CHECK(call_method(dev, 83, {prim, count, sc(0x1000), 16}) == invalid);          // DrawPrimitiveUP
            CHECK(call_method(dev, 84, {prim, 0, 3, count, sc(0x1000), 101, sc(0x1000), 16}) == invalid);
            CHECK(call_method(dev, 81, {prim, 0, count}) == invalid);                        // DrawPrimitive
            CHECK(call_method(dev, 82, {prim, 0, 0, 3, 0, count}) == invalid);              // DrawIndexedPrimitive
        }
        // vertex count over the index limit, primitive count fine
        CHECK(call_method(dev, 84, {prim, 0, max_index + 2, 1, sc(0x1000), 101, sc(0x1000), 16}) == invalid);
        CHECK(call_method(dev, 82, {prim, 0, 0, max_index + 2, 0, 1}) == invalid);
        // small valid draws are accepted
        CHECK(call_method(dev, 83, {prim, 1, sc(0x1000), 16}) == 0);
        CHECK(call_method(dev, 84, {prim, 0, 3, 1, sc(0x1000), 101, sc(0x1000), 16}) == 0);
    }
    // at and just below the limit: accepted (point lists, so the vertex count equals the primitive count; the
    // degenerate data lives anywhere in the arena)
    for (uint32_t count : {max_prims - 1, max_prims}) {
        CHECK(call_method(dev, 81, {1, 0, count}) == 0);
        CHECK(call_method(dev, 83, {1, count, 0x00100000u, 16}) == 0);
    }
    getrusage(RUSAGE_SELF, &after);
    // ru_maxrss: bytes on macOS, kilobytes on Linux; either way far below a runaway allocation
    CHECK((uint64_t)(after.ru_maxrss - before.ru_maxrss) < (uint64_t)512 * 1024 * 1024);
}

// Test155: with no depth-stencil surface set, GetDepthStencilSurface returns D3DERR_NOTFOUND and a null pointer
// (Direct3D 9), every time, and makes nothing; with one set it returns that one.
static void test_depth_stencil_get(uint32_t dev) {
    const uint32_t notfound = 0x88760866u, out = sc(0x900), made = sc(0x904);
    CHECK(call_method(dev, 39, {0}) == 0); // SetDepthStencilSurface(NULL)
    for (int i = 0; i < 64; ++i) {
        wr32(out, 0xdeadbeefu);
        CHECK(call_method(dev, 40, {out}) == notfound);
        CHECK(rd32(out) == 0);
    }
    CHECK(call_method(dev, 29, {16, 16, 75, 0, 0, 0, made, 0}) == 0); // CreateDepthStencilSurface D24S8
    const uint32_t surface = rd32(made);
    CHECK(surface != 0);
    CHECK(call_method(dev, 39, {surface}) == 0);
    CHECK(call_method(dev, 40, {out}) == 0);
    CHECK(rd32(out) != 0);
    if (rd32(out))
        call_method(rd32(out), 2); // Release the reference GetDepthStencilSurface added
    CHECK(call_method(dev, 39, {0}) == 0);
    CHECK(call_method(dev, 40, {out}) == notfound);
    if (surface)
        call_method(surface, 2);
}

// Test157c: depth-stencil reference rules of Direct3D 9 (the audit found the kit diverged in all three):
// SetDepthStencilSurface AddRefs the bound surface (the device keeps it alive after the app releases it), and
// Reset drops only the device's own references, never the app's.
static void test_depth_stencil_refs(uint32_t dev) {
    const uint32_t made = sc(0x910), out = sc(0x914), pp = sc(0x940);
    auto addref = [](uint32_t o) { return call_method(o, 1); };
    auto release = [](uint32_t o) { return call_method(o, 2); };
    CHECK(call_method(dev, 29, {16, 16, 75, 0, 0, 0, made, 0}) == 0);
    const uint32_t s = rd32(made), s_id = com_this(s)->id;
    CHECK(call_method(dev, 39, {s}) == 0);
    CHECK(addref(s) == 3); // app + device + this
    release(s);
    CHECK(release(s) == 1); // the app lets go; the device still holds it
    CHECK(com_get(s_id) != nullptr);
    wr32(out, 0);
    CHECK(call_method(dev, 40, {out}) == 0);
    CHECK(rd32(out) && com_this(rd32(out)) && com_this(rd32(out))->id == s_id);
    if (rd32(out))
        release(rd32(out));
    CHECK(call_method(dev, 39, {0}) == 0); // unbinding drops the last reference
    CHECK(com_get(s_id) == nullptr);
    // Reset with the automatic depth buffer on, while an app-owned surface is bound
    CHECK(call_method(dev, 29, {16, 16, 75, 0, 0, 0, made, 0}) == 0);
    const uint32_t e = rd32(made), e_id = com_this(e)->id;
    CHECK(call_method(dev, 39, {e}) == 0);
    memset(gm_ptr(pp), 0, 56);
    wr32(pp, 8);
    wr32(pp + 4, 8);
    wr32(pp + 36, 1);
    wr32(pp + 40, 75);
    CHECK(call_method(dev, 16, {pp}) == 0);
    CHECK(com_get(e_id) != nullptr); // the app's surface survives Reset
    CHECK(addref(e) == 2);           // app + this: the device let go of its binding only
    release(e);
    wr32(out, 0);
    CHECK(call_method(dev, 40, {out}) == 0); // the new automatic depth buffer is bound
    const uint32_t auto1 = rd32(out) && com_this(rd32(out)) ? com_this(rd32(out))->id : 0;
    CHECK(auto1 != 0 && auto1 != e_id);
    if (rd32(out))
        release(rd32(out));
    CHECK(release(e) == 0); // the app's last reference
    CHECK(com_get(e_id) == nullptr);
    // a second Reset replaces the automatic depth buffer; the old one goes once nothing holds it
    CHECK(call_method(dev, 16, {pp}) == 0);
    CHECK(com_get(auto1) == nullptr);
    wr32(out, 0);
    CHECK(call_method(dev, 40, {out}) == 0 && rd32(out));
    if (rd32(out))
        release(rd32(out));
}

// Test159: a texture level shares its texture's lifetime (Direct3D 9): releasing the level gives back the texture
// reference it took, in either release order, and a level obtained twice needs two releases.
static void test_surface_container_refs(uint32_t dev) {
    const uint32_t texp = sc(0x980), surfp = sc(0x984), surf2p = sc(0x988);
    auto release = [](uint32_t o) { return call_method(o, 2); };
    for (int order = 0; order < 2; ++order) {
        CHECK(call_method(dev, 23, {16, 16, 1, 1 /* RENDERTARGET */, 21, 0, texp, 0}) == 0); // CreateTexture
        const uint32_t tex = rd32(texp), tex_id = com_this(tex)->id;
        CHECK(call_method(tex, 18, {0, surfp}) == 0); // GetSurfaceLevel
        const uint32_t surf = rd32(surfp), surf_id = com_this(surf)->id;
        if (order == 0) {
            release(tex);                              // the app lets the texture go first...
            CHECK(com_get(tex_id) != nullptr);         // ...the level keeps it alive
            release(surf);
        } else {
            release(surf);
            CHECK(com_get(tex_id) != nullptr);         // the app still holds the texture
            release(tex);
        }
        CHECK(com_get(tex_id) == nullptr && com_get(surf_id) == nullptr);
    }
    // a level obtained twice, and through GetRenderTarget after SetRenderTarget
    CHECK(call_method(dev, 23, {16, 16, 1, 1, 21, 0, texp, 0}) == 0);
    const uint32_t tex = rd32(texp), tex_id = com_this(tex)->id;
    CHECK(call_method(tex, 18, {0, surfp}) == 0);
    CHECK(call_method(tex, 18, {0, surf2p}) == 0);
    const uint32_t s1 = rd32(surfp), s2 = rd32(surf2p);
    CHECK(call_method(dev, 37, {0, s1}) == 0);    // SetRenderTarget(0, level)
    CHECK(call_method(dev, 38, {0, surfp}) == 0); // GetRenderTarget(0)
    const uint32_t s3 = rd32(surfp);
    CHECK(call_method(dev, 38, {1, surfp}) != 0 || rd32(surfp) == 0); // nothing on slot 1
    CHECK(call_method(dev, 18, {0, 0, 0, surf2p + 4}) == 0); // GetBackBuffer: put the back buffer back on slot 0
    const uint32_t bb = rd32(surf2p + 4);
    CHECK(call_method(dev, 37, {0, bb}) == 0);
    release(bb);
    release(tex);
    release(s1);
    release(s2);
    CHECK(com_get(tex_id) != nullptr); // GetRenderTarget's reference is still outstanding
    release(s3);
    CHECK(com_get(tex_id) == nullptr);
}

// SetFVF makes the declaration D3D9 builds for the FVF (run 4: NFS Underground's race minimap is
// XYZ | DIFFUSE | TEX1 | TEXCOORDSIZE4(0), a projected four-float texture coordinate); GetFVF reads
// it back; one declaration per FVF; SetVertexDeclaration clears the FVF.
static void test_fvf(uint32_t dev) {
    auto decl_bytes = [&]() {
        std::vector<uint8_t> b;
        CHECK(call_method(dev, 88, {sc(0x40)}) == 0); // GetVertexDeclaration
        const uint32_t view = rd32(sc(0x40));
        CHECK(view != 0);
        if (ComObj *d = view ? com_this(view) : nullptr)
            b = d->blob;
        if (view)
            call_method(view, 2);
        return std::make_pair(view, b);
    };
    CHECK(call_method(dev, 89, {0x00020142u}) == 0); // SetFVF
    CHECK(call_method(dev, 90, {sc(0x44)}) == 0);    // GetFVF
    CHECK(rd32(sc(0x44)) == 0x00020142u);
    const auto minimap = decl_bytes();
    const std::vector<uint8_t> want = {0, 0, 0,  0, 2, 0, 0,  0,   // POSITION0 FLOAT3 @0
                                       0, 0, 12, 0, 4, 0, 10, 0,   // COLOR0 D3DCOLOR @12
                                       0, 0, 16, 0, 3, 0, 5,  0,   // TEXCOORD0 FLOAT4 @16
                                       0xff, 0, 0, 0, 17, 0, 0, 0}; // D3DDECL_END
    CHECK(minimap.second == want);
    CHECK(call_method(dev, 89, {0x104u}) == 0); // XYZRHW | TEX1
    const auto screen = decl_bytes();
    const std::vector<uint8_t> want_t = {0, 0, 0,  0, 3, 0, 9, 0,   // POSITIONT FLOAT4 @0
                                         0, 0, 16, 0, 1, 0, 5, 0,   // TEXCOORD0 FLOAT2 @16
                                         0xff, 0, 0, 0, 17, 0, 0, 0};
    CHECK(screen.second == want_t);
    CHECK(call_method(dev, 89, {0x00020142u}) == 0);
    CHECK(decl_bytes().first == minimap.first); // the same declaration again
    CHECK(call_method(dev, 87, {0}) == 0);       // SetVertexDeclaration(NULL)
    CHECK(call_method(dev, 90, {sc(0x44)}) == 0);
    CHECK(rd32(sc(0x44)) == 0u);
}

static uint32_t make_device() {
    const uint32_t api = call_shim(tramp("d3d9.dll", "Direct3DCreate9"), {32});
    CHECK(api != 0);
    wr32(sc(0x100), 8);
    wr32(sc(0x104), 8);
    CHECK(call_method(api, 16, {0, 1, 0, 0, sc(0x100), sc(0)}) == 0);
    const uint32_t dev = rd32(sc(0));
    CHECK(dev != 0);
    call_method(api, 2);
    return dev;
}

static void test_device_booleans(uint32_t dev) {
    // D3D9 vtable Set/Get VS B=98/99, PS B=113/114. The actual guest
    // stack and vtable calls catch ABI errors as well as bounds mistakes.
    for (uint32_t set : {98u, 113u}) {
        for (uint32_t i = 0; i < 16; ++i)
            wr32(sc(0x200) + i * 4, i & 1 ? 0xfffffff9u : 0);
        CHECK(call_method(dev, set, {0, sc(0x200), 16}) == 0);
        CHECK(call_method(dev, set + 1, {0, sc(0x300), 16}) == 0);
        CHECK(memcmp(gm_ptr(sc(0x200)), gm_ptr(sc(0x300)), 64) == 0);
        wr32(sc(0x200), 3);
        for (const auto range :
             {std::array<uint32_t, 2>{15, 2}, {16, 1}, {0xffffffffu, 1}, {0, 0xffffffffu}}) {
            CHECK(call_method(dev, set, {range[0], sc(0x200), range[1]}) == 0x8876086cu);
            wr32(sc(0x300), 0xdeadbeefu);
            CHECK(call_method(dev, set + 1, {range[0], sc(0x300), range[1]}) == 0x8876086cu);
            CHECK(rd32(sc(0x300)) == 0xdeadbeefu);
        }
        CHECK(call_method(dev, set + 1, {15, sc(0x300), 1}) == 0);
        CHECK(rd32(sc(0x300)) == 0xfffffff9u); // invalid writes were atomic
        CHECK(call_method(dev, set, {0, 0, 1}) == 0x8876086cu);
        CHECK(call_method(dev, set + 1, {0, 0, 1}) == 0x8876086cu);
        CHECK(call_method(dev, set, {0, 0xfffffffcu, 2}) == 0x8876086cu);
        CHECK(call_method(dev, set + 1, {0, 0xfffffffcu, 2}) == 0x8876086cu);
    }
}

static void test_effect_booleans(uint32_t dev) {
    const auto fx = effect();
    // Minimal loaded PE resource directory. This exercises the same export
    // used by a game, with an allocated guest module instead of a disk image.
    const uint32_t module = heap_alloc(0x1000 + uint32_t(fx.size()), true, 16);
    CHECK(module != 0);
    wr32(module, 0x5a4d);
    wr32(module + 0x3c, 0x40);
    wr32(module + 0x40, 0x4550);
    wr32(module + 0x40 + 0x18 + 0x60 + 16, 0x200);
    const uint32_t root = module + 0x200;
    wr32(root + 12, 1u << 16);
    wr32(root + 16, 10);
    wr32(root + 20, 0x80000020u);
    wr32(root + 0x20 + 12, 1u << 16);
    wr32(root + 0x20 + 16, 1);
    wr32(root + 0x20 + 20, 0x80000040u);
    wr32(root + 0x40 + 12, 1u << 16);
    wr32(root + 0x40 + 16, 1033);
    wr32(root + 0x40 + 20, 0x60);
    wr32(root + 0x60, 0x1000);
    wr32(root + 0x64, uint32_t(fx.size()));
    memcpy(gm_ptr(module + 0x1000), fx.data(), fx.size());
    CHECK(call_shim(tramp("d3dx9_26.dll", "D3DXCreateEffectFromResourceA"),
                    {dev, module, 1, 0, 0, 0, 0, sc(0), 0}) == 0);
    const uint32_t e = rd32(sc(0));
    CHECK(e != 0);
    memcpy(gm_ptr(sc(0x200)), "Switch", 7);
    memcpy(gm_ptr(sc(0x220)), "Many", 5);
    const uint32_t s = call_method(e, 9, {0, sc(0x200)}), m = call_method(e, 9, {0, sc(0x220)});
    CHECK(s != 0 && m != 0 && s != m);
    CHECK(call_method(e, 63, {sc(0x20), 0}) == 0);
    CHECK(rd32(sc(0x20)) == 1);
    CHECK(call_method(e, 64, {0}) == 0);
    D9Pipeline &pl = d9_pipeline(com_this(dev)->id);
    CHECK(pl.vbool[15] == 0 && pl.pbool[0] == 0 && pl.pbool[1] == 1);
    CHECK(!pl.vs.empty() && !pl.ps.empty());
    for (uint32_t value : {1u, 0u, 1u}) {
        CHECK(call_method(e, 22, {s, value}) == 0);
        wr32(sc(0x240), value);
        wr32(sc(0x244), !value);
        CHECK(call_method(e, 24, {m, sc(0x240), 2}) == 0);
        CHECK(call_method(e, 65) == 0);
        CHECK(pl.vbool[15] == int32_t(value));
        CHECK(pl.pbool[0] == int32_t(value) && pl.pbool[1] == int32_t(!value));
    }
    // Test75: incremental CommitChanges (d3dx9.cpp bind_constants). The registers remember which
    // effect and shader wrote them; an unchanged commit keeps the values, a parameter written between
    // two commits reaches the registers, and a device constant setter in between makes the next
    // commit write every constant again (the shim's behaviour before Test75 too).
    CHECK(pl.vbinder.fx == com_this(e)->id && pl.pbinder.fx == com_this(e)->id);
    CHECK(call_method(e, 65) == 0);
    CHECK(pl.vbool[15] == 1 && pl.pbool[0] == 1 && pl.pbool[1] == 0);
    wr32(sc(0x260), 0);
    CHECK(call_method(dev, 98, {15, sc(0x260), 1}) == 0); // SetVertexShaderConstantB
    CHECK(pl.vbool[15] == 0 && pl.vbinder.fx == 0);
    CHECK(call_method(e, 65) == 0);
    CHECK(pl.vbool[15] == 1 && pl.vbinder.fx == com_this(e)->id);
    CHECK(call_method(e, 22, {s, 0}) == 0);
    CHECK(call_method(e, 65) == 0);
    CHECK(pl.vbool[15] == 0 && pl.pbool[0] == 1);
    CHECK(call_method(e, 22, {s, 1}) == 0);
    CHECK(call_method(e, 65) == 0);
    CHECK(pl.vbool[15] == 1);
    const uint8_t decl[] = {0, 0, 0, 0, 2, 0, 0, 0, 0xff, 0, 0, 0, 17, 0, 0, 0};
    memcpy(gm_ptr(sc(0x300)), decl, sizeof decl);
    CHECK(call_method(dev, 86, {sc(0x300), sc(0)}) == 0);
    const uint32_t declaration = rd32(sc(0));
    CHECK(call_method(dev, 87, {declaration}) == 0);
    const float triangle[] = {-1, 1, 0.5f, 3, 1, 0.5f, -1, -3, 0.5f};
    memcpy(gm_ptr(sc(0x400)), triangle, sizeof triangle);
    CHECK(call_method(dev, 83, {4, 1, sc(0x400), 12}) == 0);
    CHECK(draws == 1);
    CHECK(captured.vbool[15] == 1 && captured.pbool[0] == 1 && captured.pbool[1] == 0);
    CHECK(captured.admission.serial == 0 && rejected_draws == 0);
    // Finish the legacy first-eight draw descriptions using harmless no-IB
    // calls before supplying deliberately invalid UP pointers below.
    for (int i = 0; i < 3; ++i)
        CHECK(call_method(dev, 82, {4, 0, 0, 3, 0, 1}) == 0);
    CHECK(rejected_draws == 0 && draws == 1);
    admission_enabled = true;
    CHECK(call_method(dev, 26, {36, 0, 0, 0, sc(0x50), 0}) == 0);
    uint32_t vb = rd32(sc(0x50));
    CHECK(call_method(dev, 100, {0, vb, 0, 12}) == 0);
    CHECK(call_method(dev, 27, {6, 0, 101, 0, sc(0x54), 0}) == 0);
    uint32_t ib = rd32(sc(0x54));
    CHECK(call_method(dev, 104, {ib}) == 0);
    CHECK(call_method(dev, 82, {4, 0, 0, 3, 0, 1}) == 0);
    CHECK(draws == 2 && captured.admission.serial != 0);
    CHECK(captured.admission.api == 82 && captured.admission.device == dev);
    CHECK(captured.admission.caller != 0);
    CHECK(captured.admission.resource_identity[0] == vb);
    CHECK(captured.admission.resource_identity[8] == ib);
    uint64_t serial = captured.admission.serial;
    CHECK(call_method(dev, 104, {0}) == 0);
    CHECK(call_method(dev, 82, {4, 0, 0, 3, 0, 1}) == 0);
    CHECK(rejected_draws == 1 && draws == 2 && rejected.admission.reason == 3);
    CHECK(rejected.admission.serial == ++serial && rejected.admission.api == 82);
    CHECK(rejected.stream[0].buffer == com_this(vb)->id);
    CHECK(rejected.admission.resource_identity[0] == vb);
    CHECK(rejected.target.color[0].id == captured.target.color[0].id);
    CHECK(call_method(dev, 87, {0}) == 0);
    CHECK(call_method(dev, 81, {4, 0, 1}) == 0);
    CHECK(rejected_draws == 2 && draws == 2 && rejected.admission.reason == 2);
    CHECK(rejected.admission.serial == ++serial && rejected.admission.api == 81);
    CHECK(call_method(dev, 87, {declaration}) == 0);
    auto saved_vs = std::move(pl.vs);
    CHECK(call_method(dev, 83, {4, 1, sc(0x400), 12}) == 0);
    CHECK(rejected_draws == 3 && draws == 2 && rejected.admission.reason == 1);
    CHECK(rejected.admission.serial == ++serial && rejected.admission.api == 83);
    pl.vs = std::move(saved_vs);
    CHECK(call_method(dev, 83, {4, 1, 0xfffffff0u, 12}) == 0);
    CHECK(rejected_draws == 4 && rejected.admission.reason == 4);
    CHECK(rejected.admission.serial == ++serial);
    CHECK(call_method(dev, 84, {4, 0, 3, 1, 0xfffffff0u, 101, sc(0x400), 12}) == 0);
    CHECK(rejected_draws == 5 && draws == 2 && rejected.admission.reason == 4);
    CHECK(rejected.admission.serial == ++serial && rejected.admission.api == 84);
    CHECK(!rejected.inline_vertices && !rejected.inline_indices);
    CHECK(rejected.admission.inline_indices == 0xfffffff0u);
    CHECK(rejected.admission.inline_vertices == sc(0x400));
    CHECK(rejected.admission.resource_identity[0] == 0);
    CHECK(rejected.stream[0].buffer == 0 && rejected.index_buffer == 0);
    CHECK(call_method(dev, 83, {4, 1, sc(0x400), 12}) == 0);
    CHECK(draws == 3 && captured.admission.reason == 0);
    CHECK(captured.admission.serial == ++serial && captured.admission.api == 83);
    admission_enabled = false;
    CHECK(call_method(dev, 82, {4, 0, 0, 3, 0, 1}) == 0);
    CHECK(rejected_draws == 5 && draws == 3); // opt-out retains old behavior
    CHECK(call_method(dev, 83, {4, 1, sc(0x400), 12}) == 0);
    CHECK(draws == 4 && captured.admission.serial == 0);
    call_method(dev, 100, {0, 0, 0, 0});
    call_method(vb, 2);
    call_method(ib, 2);
    call_method(e, 66);
    call_method(e, 67);
    call_method(e, 2);
    call_method(declaration, 2);
}

// Remaster run 13 natural water: the three WORLD program shapes water_find accepts (kit/dx/d3d9_shadergen.cpp), from
// the default MSL translations of the census (work/census13/bay/*.ps.msl), cut to the lines water_find reads.
namespace d9msl {
bool water_find(const std::string &s, size_t *l_end, std::string *call);
bool water_patch(std::string *s, int mode);
}
static const char kWaterHead[] =
    "fragment D9PSOut ps_main(D9V2F vin [[stage_in]], constant float4 *c [[buffer(0)]]) {\n"
    "    float4 odepth = float4(vin.pos.z);\n";
static const char kWaterTail[] = "    oc[0] = ((r[0].xyzw));\n    float4 col = oc[0];\n}\n";
// ps 629cd2e4c94c69f8 (shadow receiver, form 3)
static const char kWater629[] =
    "    r[1] = (tx1.sample(sm1, (((t[3].xyzw) / ((t[3].xyzw)).w)).xy));\n"
    "    r[0] = (tx0.sample(sm0, ((t[0].xyzw)).xy));\n"
    "    { float4 w_ = (float4(1.0 / ((t[3].wwww)).x)); r[1].w = w_.w; }\n"
    "    { float4 w_ = ((t[3].yyyy) * (-(r[1].wwww)) + (c[1].xxxx)); r[1].w = w_.w; }\n"
    "    { float4 w_ = saturate((r[1].wwww) * (c[1].yyyy)); r[1].w = w_.w; }\n"
    "    { float4 w_ = ((r[1].xxxx) + (r[1].wwww)); r[1].w = w_.w; }\n"
    "    { float4 w_ = ((r[0].xyzw) * (iv[1].xyzw)); r[1].xyz = w_.xyz; }\n"
    "    { float4 w_ = saturate((r[1].wwww) + (c[0].xxxx)); r[1].w = w_.w; }\n"
    "    { float4 w_ = ((r[0].xyzw) * (iv[0].xyzw)); r[0].xyz = w_.xyz; }\n"
    "    { float4 w_ = ((r[1].xyzw) * (r[1].wwww) + (r[0].xyzw)); r[0].xyz = w_.xyz; }\n"
    "    { float4 w_ = ((r[0].wwww) * (iv[0].wwww)); r[0].w = w_.w; }\n"
    "    { float4 w_ = ((c[1].zzzz) * (r[0].xyzw) + (t[2].xyzw)); r[0].xyz = w_.xyz; }\n";
// ps cc1387a106b0820e (four-tap shadow, form 2), from the stage-0 sample on
static const char kWaterCc1[] =
    "    r[0] = (tx0.sample(sm0, ((t[0].xyzw)).xy));\n"
    "    { float4 w_ = ((r[1].wwww) * (c[3].zzzz) + (r[2].wwww)); r[1].w = w_.w; }\n"
    "    { float4 w_ = ((r[0].xyzw) * (iv[1].xyzw)); r[1].xyz = w_.xyz; }\n"
    "    { float4 w_ = saturate((r[1].wwww) + (c[1].xxxx)); r[1].w = w_.w; }\n"
    "    { float4 w_ = ((r[0].xyzw) * (iv[0].xyzw)); r[0].xyz = w_.xyz; }\n"
    "    { float4 w_ = ((r[1].xyzw) * (r[1].wwww) + (r[0].xyzw)); r[0].xyz = w_.xyz; }\n"
    "    { float4 w_ = ((r[0].wwww) * (iv[0].wwww)); r[0].w = w_.w; }\n"
    "    { float4 w_ = ((c[3].wwww) * (r[0].xyzw) + (t[2].xyzw)); r[0].xyz = w_.xyz; }\n";
static void test_water_shapes() {
    const std::string w629 = std::string(kWaterHead) + kWater629 + kWaterTail;
    const std::string wcc1 = std::string(kWaterHead) + kWaterCc1 + kWaterTail;
    const std::string kC = "    { float4 w_ = ((r[1].xyzw) * (r[1].wwww) + (r[0].xyzw)); r[0].xyz = w_.xyz; }\n";
    size_t at = 0;
    std::string call;
    // form 3: accepted, the call goes right after C with the shadow r1.w and 1 / c1.z
    CHECK(d9msl::water_find(w629, &at, &call));
    CHECK(at == w629.find(kC) + kC.size());
    CHECK(call.find("d9w_water(r[0].xyz, tc[0].xy, vin.t4.xyz, vin.t5.xyz, vin.t6.xyz, iv[1].xyz, tc[2].xyz, P.pad1, "
                    "saturate(r[1].w), 1.0 / max(c[1].z, 1.0e-3), iv[1].xyz * saturate(r[1].w) + iv[0].xyz") !=
          std::string::npos);
    for (int mode : {1, 2}) {
        std::string p = w629;
        CHECK(d9msl::water_patch(&p, mode));
        const size_t fn = p.find("static float3 d9w_water("), use = p.find("    r[0].xyz = d9w_water(r[0].xyz");
        CHECK(fn != std::string::npos && use != std::string::npos && fn < p.find("fragment D9PSOut ps_main("));
        CHECK(use == p.find(kC) + kC.size());
        CHECK(p.find(mode == 2 ? "1.0e-3), iv[1].xyz * saturate(r[1].w) + iv[0].xyz, 2, float3("
                               : "1.0e-3), iv[1].xyz * saturate(r[1].w) + iv[0].xyz, 1, float3(") != std::string::npos);
    }
    // form 2 still matches as form 2 (brightness c3.w), not as form 3
    CHECK(d9msl::water_find(wcc1, &at, &call));
    CHECK(call.find("1.0 / max(c[3].w, 1.0e-3)") != std::string::npos);
    // refused: a body read of t4..t7 (the pbr vertex variant writes them), no S line, S after C, B anchor twice
    std::string bad = w629;
    bad.insert(bad.find(kC), "    r[2] = vin.t5;\n");
    CHECK(!d9msl::water_find(bad, &at, &call));
    const std::string kS = "    { float4 w_ = saturate((r[1].wwww) + (c[0].xxxx)); r[1].w = w_.w; }\n";
    bad = w629;
    bad.erase(bad.find(kS), kS.size());
    CHECK(!d9msl::water_find(bad, &at, &call));
    bad.insert(bad.find(kC) + kC.size(), kS);
    CHECK(!d9msl::water_find(bad, &at, &call));
    bad = w629;
    const std::string kB = "    { float4 w_ = ((c[1].zzzz) * (r[0].xyzw) + (t[2].xyzw)); r[0].xyz = w_.xyz; }\n";
    bad.insert(bad.find(kB), kB);
    CHECK(!d9msl::water_find(bad, &at, &call));
}

// R3 run 17 Xbox 360 lens drops (ours): the six PC drop lines as the generator writes them (R1-dev warm seed
// 792db7f5...msl), patched once; refused when a line is missing or the block appears twice.
static const char *const kDropBody =
    "    r[0] = (tx1.sample(sm1, ((t[0].xyzw)).xy));\n"
    "    { float4 w_ = ((r[0].yyyy) * (c[0].zzzz) + (c[0].xxxx)); r[0].x = w_.x; }\n"
    "    { float4 w_ = ((r[0].zzzz) * (c[0].wwww) + (c[0].yyyy)); r[0].y = w_.y; }\n"
    "    { float4 w_ = ((r[0].wwww) * (c[1].xxxx)); r[0].w = w_.w; }\n"
    "    r[1] = (tx0.sample(sm0, ((r[0].xyzw)).xy));\n"
    "    { float4 w_ = ((r[1].xyzw) * (c[2].xxxx)); r[0].xyz = w_.xyz; }\n";
static void test_raindrop_patch() {
    const std::string head = "    float4 odepth = float4(vin.pos.z);\n", tail = "    oc[0] = ((r[0].xyzw));\n";
    std::string p = head + kDropBody + tail;
    CHECK(d9msl::raindrop_patch(&p));
    CHECK(p.find("c[2].xxxx") == std::string::npos);                       // no x1.2 brightening
    CHECK(p.find("r[0] = float4(r[1].xyz * rd_s, rd_o * c[1].x);") != std::string::npos);   // the game's fade kept
    CHECK(p.find("rd_m.y * c[0].z + c[0].x, rd_m.z * c[0].w + c[0].y") != std::string::npos);   // and its refraction
    CHECK(p.find(head) == 0 && p.find(tail) != std::string::npos);
    CHECK(!d9msl::raindrop_patch(&p));                                      // once
    std::string q = head + kDropBody + tail;
    q.erase(q.find("    r[1] = (tx0"), strlen("    r[1] = (tx0.sample(sm0, ((r[0].xyzw)).xy));\n"));
    CHECK(!d9msl::raindrop_patch(&q));                                      // a line missing
    std::string twice = head + kDropBody + kDropBody + tail;
    CHECK(!d9msl::raindrop_patch(&twice));                                  // ambiguous
}

// R3 run 22 (TEST) texture mip bias: plain 2D/cube lookups get bias(b), an existing bias(x) becomes bias(b + x),
// level() / gradient lookups, non-tx textures and the shadow helper stay; nested lookups both biased; codes round-trip.
static void test_mipbias_patch() {
    CHECK(d9msl::mipbias_code(-0.585f) == 128 - 37 && d9msl::mipbias_value(d9msl::mipbias_code(-0.585f)) == -37 / 64.0f);
    CHECK(d9msl::mipbias_code(-1.585f) == 128 - 101 && d9msl::mipbias_code(1.0f) == 192 && d9msl::mipbias_code(0.0f) == 128);
    CHECK(d9msl::mipbias_code(-9.0f) == 1 && d9msl::mipbias_code(9.0f) == 255 && d9msl::mipbias_value(0) == 0.0f);
    const std::string src =
        "    r[0] = (tx1.sample(sm1, ((t[0].xyzw)).xy));\n"
        "    r[1] = (tx12.sample(sm12, (tc[2]).xyz, bias((tc[2]).w)));\n"
        "    r[2] = (tx0.sample(sm0, (tc[0]).xy, level((tc[0]).w)));\n"
        "    r[3] = d9_orm.sample(sm0, t[0].xy);\n"
        "    r[4] = d9_shadow(tx3, t[3]);\n"
        "    r[5] = mytx0.sample(sm0, t[0].xy);\n"
        "    r[6] = tx2.sample(sm2, tx1.sample(sm1, t[0].xy).xy);\n"
        "    r[7] = tx0.sample(sm0, t[0].xy, int2(1, 0));\n";
    std::string s = src;
    uint32_t n = 99;
    CHECK(d9msl::mipbias_patch(&s, d9msl::mipbias_code(-0.585f), &n) && n == 5);
    CHECK(s.find("tx0.sample(sm0, t[0].xy, bias(-0.578125), int2(1, 0))") != std::string::npos);   // MSL order
    CHECK(s.find("tx1.sample(sm1, ((t[0].xyzw)).xy, bias(-0.578125))") != std::string::npos);
    CHECK(s.find("tx12.sample(sm12, (tc[2]).xyz, bias(-0.578125 + (tc[2]).w))") != std::string::npos);
    CHECK(s.find("tx0.sample(sm0, (tc[0]).xy, level((tc[0]).w))") != std::string::npos);
    CHECK(s.find("d9_orm.sample(sm0, t[0].xy);") != std::string::npos && s.find("d9_shadow(tx3, t[3]);") != std::string::npos);
    CHECK(s.find("mytx0.sample(sm0, t[0].xy);") != std::string::npos);
    CHECK(s.find("tx2.sample(sm2, tx1.sample(sm1, t[0].xy, bias(-0.578125)).xy, bias(-0.578125))") != std::string::npos);
    std::string z = src;
    CHECK(d9msl::mipbias_patch(&z, 0, &n) && n == 0 && z == src);                   // code 0: unchanged
    std::string bad = "    r[0] = tx0.sample(sm0, (t[0].xy;\n";
    CHECK(!d9msl::mipbias_patch(&bad, 100) && bad == "    r[0] = tx0.sample(sm0, (t[0].xy;\n");   // unclosed: refused
    d9msl::PixelVariant a, b;
    b.mipbias = 91;
    CHECK(a.key() != b.key());
}



// Remaster run 14 foundation F1/F3 (review/run14/foundation-design.md 4.1 / 4.3 offline checks): the clip-register
// analysis, the VP plurality pick, the projection-shape check, the reprojection and the Halton jitter.
namespace fndt {
using namespace remaster_fnd;
// vs_2_0: dcl_position v0; dp4 oPos.{x,y,z,w}, v0, c[r0..r3]; optionally dp4 oT0.{x,y,z,w}, v0, c[t0..t0+3]
static std::vector<uint8_t> vs(const int r[4], int t0) {
    std::vector<uint32_t> w = {0xFFFE0200u, 0x0200001Fu, 0x80000000u, 0x900F0000u};
    for (int k = 0; k < 4; ++k)
        w.insert(w.end(), {0x03000009u, 0xC0000000u | (1u << (16 + k)), 0x90E40000u, 0xA0E40000u | (uint32_t)r[k]});
    for (int k = 0; t0 >= 0 && k < 4; ++k)
        w.insert(w.end(), {0x03000009u, 0xE0000000u | (1u << (16 + k)), 0x90E40000u, 0xA0E40000u | (uint32_t)(t0 + k)});
    w.push_back(0x0000FFFFu);
    std::vector<uint8_t> b(w.size() * 4);
    memcpy(b.data(), w.data(), b.size());
    return b;
}
static M4 translate(double x, double y, double z) {
    M4 t = identity();
    t.m[3][0] = x, t.m[3][1] = y, t.m[3][2] = z;
    return t;
}
static M4 yaw(double a) {   // about the view's y axis (y down, z forward)
    M4 r = identity();
    r.m[0][0] = std::cos(a), r.m[0][2] = -std::sin(a), r.m[2][0] = std::sin(a), r.m[2][2] = std::cos(a);
    return r;
}
// world -> view for a camera at c (world), looking along +z rotated by `a`, the game's y-down view
static M4 view(double cx, double cy, double cz, double a) { return mul(translate(-cx, -cy, -cz), yaw(a)); }
static M4 proj(double fov, double aspect, double n, double f, bool reversed = false) {
    M4 p{};
    const double ys = 1.0 / std::tan(0.5 * fov);
    p.m[0][0] = ys / aspect;
    p.m[1][1] = -ys;   // view y down -> clip y up
    p.m[2][2] = reversed ? -n / (f - n) : f / (f - n);
    p.m[2][3] = 1.0;
    p.m[3][2] = reversed ? n * f / (f - n) : -n * f / (f - n);
    return p;
}
static void block_of(const M4 &m, float *c) {   // the 4 registers the game would set (transposed)
    for (int i = 0; i < 4; ++i)
        for (int j = 0; j < 4; ++j)
            c[4 * j + i] = (float)m.m[i][j];
}
static void rows_of(const M4 &m, float *f) {
    for (int i = 0; i < 4; ++i)
        for (int j = 0; j < 4; ++j)
            f[4 * i + j] = (float)m.m[i][j];
}
// a world point to its Metal pixel (continuous, pixel centres at i + 0.5 - 0.5 = i) and stored depth
static bool pixel_of(const M4 &vp, const double X[3], uint32_t w, uint32_t h, double hx, double hy, double *i, double *j,
                     double *d) {
    const double v[4] = {X[0], X[1], X[2], 1.0};
    double o[4];
    xform(v, vp, o);
    if (!(o[3] > 0.0))
        return false;
    *i = (o[0] / o[3] + hx + 1.0) * 0.5 * w - 0.5;
    *j = (1.0 - o[1] / o[3] - hy) * 0.5 * h - 0.5;
    *d = o[2] / o[3];
    return true;
}
} // namespace fndt

static void test_foundation() {
    using namespace fndt;
    // (1) vertex_clip_regs agrees with vertex_clip_shadow; palettes / non-consecutive rows are refused
    {
        const int r[4] = {4, 5, 6, 7}, bad[4] = {4, 5, 7, 6};
        uint32_t a = 99, b = 99, c = 99;
        const d9sh::Program &both = d9sh::program_for(vs(r, 8));
        CHECK(both.ok);
        CHECK(d9msl::vertex_clip_shadow(both, &a, &b) && a == 4 && b == 8);
        CHECK(d9msl::vertex_clip_regs(both, &c) && c == a);
        const d9sh::Program &clip_only = d9sh::program_for(vs(r, -1));
        CHECK(!d9msl::vertex_clip_shadow(clip_only, &a, &b));
        CHECK(d9msl::vertex_clip_regs(clip_only, &c) && c == 4);
        const d9sh::Program &shuffled = d9sh::program_for(vs(bad, -1));
        CHECK(!d9msl::vertex_clip_regs(shuffled, &c));
    }
    const double fov = 2.0 * 3.14159265358979 * 13000.0 / 65536.0, aspect = 2560.0 / 1440.0, n = 0.5, f = 10000.0;
    const M4 P = proj(fov, aspect, n, f);
    const M4 V = view(1234.5, -356.25, 41.0, 0.7);
    const M4 VP = mul(V, P);
    float blk[16], vrows[16];
    block_of(VP, blk);
    rows_of(V, vrows);
    // (2) the plurality: 12 scenery draws with VP, 8 instanced (40 %) with W x VP (W a translation each)
    {
        Plurality pl;
        for (int k = 0; k < 20; ++k) {
            pl.world();
            if (k % 5 < 3) {
                pl.add(blk);
            } else {
                float ib[16];
                block_of(mul(translate(5.0 * k, 0, 2.0), VP), ib);
                pl.add(ib);
            }
        }
        const Plurality::Result r = pl.pick();
        CHECK(r.valid && !r.tie && r.count == 12 && r.world == 20 && r.distinct == 9);
        CHECK(r.best && !memcmp(r.best->block, blk, sizeof blk));
        M4 inv;
        CHECK(inverse(from_const_block(r.best->block), &inv));
        CHECK(pl.identity_draws(inv, 1e-4) == 12);
        // a tie: the block seen first wins (counted)
        Plurality t;
        float other[16];
        block_of(mul(translate(9, 9, 9), VP), other);
        for (int k = 0; k < 16; ++k) {   // 8 and 8
            t.world();
            t.add(k & 1 ? blk : other);
        }
        const Plurality::Result rt = t.pick();
        CHECK(rt.tie && rt.best && !memcmp(rt.best->block, other, sizeof other) && rt.valid);
        // no WORLD draws; too few draws; too small a share
        Plurality e;
        CHECK(!e.pick().valid && !e.pick().best);
        Plurality few;
        for (int k = 0; k < 7; ++k) {
            few.world();
            few.add(blk);
        }
        CHECK(!few.pick().valid);
        Plurality thin;
        for (int k = 0; k < 40; ++k)
            thin.world();
        for (int k = 0; k < 9; ++k)
            thin.add(blk);
        CHECK(!thin.pick().valid);   // 9 of 40 = 22.5 % < 25 %
        // CONF14B: VP split into 12 last-bit variants (the game's per-object WVP rounding), 3 instanced blocks of 15
        // draws each: an exact hash sees no plurality VP; the clusters do, and with the camera the VP cluster wins
        // although an instanced block is larger
        Plurality fr;
        for (int k = 0; k < 12; ++k) {
            float v[16];
            memcpy(v, blk, sizeof v);
            v[(k * 5) % 16] = std::nextafter(v[(k * 5) % 16], k & 1 ? 1e30f : -1e30f);
            CHECK(same_block(v, blk));
            fr.world();
            fr.add(v);
        }
        for (int k = 0; k < 45; ++k) {
            float ib[16];
            block_of(mul(translate(5.0 + 7.0 * (k % 3), 0, 2.0), VP), ib);
            CHECK(!same_block(ib, blk));
            fr.world();
            fr.add(ib);
        }
        CHECK(fr.pick().best && !same_block(fr.pick().best->block, blk));   // the exact-hash plurality misses VP
        M4 vg;
        CHECK(inverse(from_rows(vrows), &vg));
        const Pick nocam = pick_clusters(fr, 8, 0.25, nullptr, 0.0);
        CHECK(nocam.clusters == 4 && nocam.distinct == 15 && nocam.top[0] == 15 && nocam.top[3 - 1] == 15);
        CHECK(nocam.valid && !nocam.by_camera && !same_block(nocam.block, blk));   // the design's rule alone: wrong pick
        const Pick cam = pick_clusters(fr, 8, 0.25, &vg, 1300.0);
        CHECK(cam.valid && cam.by_camera && cam.rank == 3 && cam.count == 12 && same_block(cam.block, blk));
        // no WORLD draws; a camera but no cluster of its projection: the share rule decides
        Plurality none;
        CHECK(!pick_clusters(none, 8, 0.25, &vg, 1300.0).valid);
        Plurality inst;
        for (int k = 0; k < 20; ++k) {
            float ib[16];
            block_of(mul(translate(5.0, 1.0, 2.0), VP), ib);
            inst.world();
            inst.add(ib);
        }
        const Pick only_inst = pick_clusters(inst, 8, 0.25, &vg, 1300.0);
        CHECK(only_inst.valid && !only_inst.by_camera && !only_inst.form_any);   // valid, then G/K disagree resets
    }
    // (3) the projection shape from the float data the kit gets: P = V_g^-1 x VP_k
    {
        M4 vinv;
        CHECK(inverse(from_rows(vrows), &vinv));
        const M4 Pk = mul(vinv, from_const_block(blk));
        double c[3];
        camera_position(from_rows(vrows), c);
        CHECK(std::fabs(c[0] - 1234.5) < 1e-3 && std::fabs(c[1] + 356.25) < 1e-3 && std::fabs(c[2] - 41.0) < 1e-3);
        const double tr = std::sqrt(c[0] * c[0] + c[1] * c[1] + c[2] * c[2]);
        const ProjCheck pc = check_projection(Pk, fov, aspect, n, f, tr);
        CHECK(pc.agree() && pc.y_down && !pc.reversed);
        // CONF14B: the mod's widescreen fix scales the projection (|P11| = 1.21 / tan(fov/2), P00 by (4/3)/aspect/1.047)
        // and its far plane is not the camera's: still the same camera (info flags only)
        M4 ws = Pk;
        ws.m[1][1] *= 1.2122;
        ws.m[0][0] *= 0.75 / 1.047486;
        const ProjCheck pw = check_projection(ws, fov, aspect, n, 15000.0, tr);
        CHECK(pw.agree() && !pw.fov_ok && !pw.aspect_ok && !pw.far_ok);
        CHECK(std::fabs(pc.near - n) < 1e-3 * n && std::fabs(pc.far - f) < 2e-2 * f && std::fabs(pc.fov - fov) < 1e-5);
        CHECK(!check_projection(Pk, fov * 1.01, aspect, n, f, tr).fov_ok);
        CHECK(!check_projection(Pk, fov, 4.0 / 3.0, n, f, tr).aspect_ok);
        CHECK(!check_projection(Pk, fov, aspect, 1.0, f, tr).near_ok);
        M4 sheared = Pk;   // an off-centre / non-projective P is not the form
        sheared.m[2][0] = 0.01;
        CHECK(!check_projection(sheared, fov, aspect, n, f, tr).form);
        CHECK(check_projection(proj(fov, aspect, n, f, true), fov, aspect, n, f, 0.0).reversed);
        CHECK(!check_projection(mul(view(0, 0, 0, 0.3), P), fov, aspect, n, f, 0.0).form);   // a VP that is not P
    }
    // (4) reprojection: self test, parked camera (float data) gives 0, known translation and yaw match the direct
    // projection to 1e-3 px, the sky (rotation only) does not move under translation
    {
        CHECK(self_test(from_const_block(blk)) < 1e-5);
        const uint32_t w = 2560, h = 1440;
        const double hx = (63.0 / 64.0) / w, hy = -(63.0 / 64.0) / h;
        M4 R;
        CHECK(reprojection(from_const_block(blk), from_const_block(blk), &R));
        double mx = 0.0;
        for (double i : {0.0, 640.0, 1279.0, 2559.0})
            for (double j : {0.0, 719.0, 1439.0})
                for (double d : {0.2, 0.99, 0.99995}) {
                    double ax, ay;
                    CHECK(motion(R, i, j, d, w, h, hx, hy, &ax, &ay));
                    mx = std::max(mx, std::max(std::fabs(ax), std::fabs(ay)));
                }
        CHECK(mx < 1e-4);
        const M4 Vs[2] = {view(1234.8, -356.25, 41.4, 0.7), view(1234.5, -356.25, 41.0, 0.7 + 0.02)};
        for (const M4 &Vc : Vs) {
            const M4 cur = mul(Vc, P);
            CHECK(reprojection(cur, VP, &R));
            int tested = 0;
            double err = 0.0;
            for (int k = 0; k < 64; ++k) {
                const double dist = 3.0 + 40.0 * (k % 8), side = -0.5 + (k / 8) / 7.0;
                // a point in front of the current camera: back from its view space
                M4 vinv;
                CHECK(inverse(Vc, &vinv));
                const double pv[4] = {side * dist, -0.3 * side * dist, dist, 1.0};
                double X[4];
                xform(pv, vinv, X);
                double i, j, d, pi, pj, pd;
                if (!pixel_of(cur, X, w, h, hx, hy, &i, &j, &d) || !pixel_of(VP, X, w, h, hx, hy, &pi, &pj, &pd))
                    continue;
                double ax, ay, prev_d;
                CHECK(motion(R, i, j, d, w, h, hx, hy, &ax, &ay, &prev_d));
                err = std::max({err, std::fabs(ax - (pi - i)), std::fabs(ay - (pj - j))});
                CHECK(std::fabs(prev_d - pd) < 1e-9);
                ++tested;
            }
            CHECK(tested >= 48 && err < 1e-3);
        }
        // sky: translation only, rotation-only matrices -> no motion at depth 1
        const M4 Vt = view(1240.0, -356.25, 41.0, 0.7);
        M4 Rs;
        CHECK(reprojection(mul(rotation_only(Vt), P), mul(rotation_only(V), P), &Rs));
        double ax, ay;
        CHECK(motion(Rs, 100.0, 200.0, 1.0, w, h, hx, hy, &ax, &ay) && std::fabs(ax) < 1e-6 && std::fabs(ay) < 1e-6);
        CHECK(std::fabs(linear_depth(P.m[2][2] + P.m[3][2] / 25.0, n, f) - 25.0) < 1e-6);
    }
    // (5) Halton(2,3) jitter: the table, the phase counts, the mean, the halfpix size and sign
    {
        CHECK(halton(1, 2) == 0.5 && halton(2, 2) == 0.25 && halton(3, 2) == 0.75 && halton(4, 2) == 0.125);
        CHECK(std::fabs(halton(1, 3) - 1.0 / 3) < 1e-12 && std::fabs(halton(2, 3) - 2.0 / 3) < 1e-12 &&
              std::fabs(halton(3, 3) - 1.0 / 9) < 1e-12);
        CHECK(jitter_phases(1.0) == 8 && jitter_phases(0.0) == 8 && jitter_phases(1.5) == 18 && jitter_phases(2.0) == 32);
        double sx = 0.0, sy = 0.0, jx, jy;
        jitter(0, &jx, &jy);
        CHECK(jx == 0.0 && std::fabs(jy - (1.0 / 3 - 0.5)) < 1e-12);
        for (uint32_t k = 0; k < 8; ++k) {
            jitter(k, &jx, &jy);
            CHECK(jx >= -0.5 && jx < 0.5 && jy >= -0.5 && jy < 0.5);
            sx += jx;
            sy += jy;
        }
        CHECK(std::fabs(sx / 8) < 0.07 && std::fabs(sy / 8) < 0.07);
        float dx, dy;
        jitter_halfpix(0.25, 0.25, 1000.0, 500.0, &dx, &dy);
        CHECK(std::fabs(dx - 0.0005f) < 1e-9f && std::fabs(dy + 0.001f) < 1e-9f);
        // a halfpix change of dx moves the geometry by dx x w / 2 pixels: the requested jitter
        CHECK(std::fabs(dx * 1000.0 / 2.0 - 0.25) < 1e-6 && std::fabs(-dy * 500.0 / 2.0 - 0.25) < 1e-6);
        // R3 #2 J1 (run 20; technical-track.md 3.2): the units at 2560x1440
        jitter_halfpix(0.25, -0.25, 2560.0, 1440.0, &dx, &dy);
        CHECK(std::fabs(dx - 1.953125e-4f) < 1e-10f && std::fabs(dy - 3.4722222e-4f) < 1e-10f);
        // J2: the halfpix change moves every projected point by (+jx, +jy) render pixels (x right, y down): pixel p
        // then shows the scene at p - j (the bench's model)
        {
            const M4 VPj = mul(view(812.0, -40.0, 2300.0, 0.4), proj(1.0, 16.0 / 9.0, 0.5, 15000.0));
            double worst = 0.0;
            for (double jx : {-0.375, 0.125, 0.4})
                for (double jy : {-0.2, 0.1111})
                    for (int k = 0; k < 6; ++k) {
                        const double X[3] = {812.0 + 3.0 * k, -40.0 + 0.7 * k, 2330.0 + 9.0 * k};
                        float hx, hy;
                        jitter_halfpix(jx, jy, 2560.0, 1440.0, &hx, &hy);
                        double i0, j0, d0, i1, j1, d1;
                        CHECK(pixel_of(VPj, X, 2560, 1440, 0.0, 0.0, &i0, &j0, &d0) &&
                              pixel_of(VPj, X, 2560, 1440, hx, hy, &i1, &j1, &d1));
                        worst = std::max({worst, std::fabs(i1 - i0 - jx), std::fabs(j1 - j0 - jy)});
                    }
            CHECK(worst < 1e-4);   // float halfpix: 1e-4 px
        }
        // J4/J5 note: the motion kernel's vector is (ndc x R - ndc) from the pixel centre (kFndSource fnd_body), so a
        // parked R = I gives 0 whatever jc/jp are, and a pan gives the pan; jc/jp enter only the TEST reprojection
        // counters (nj). The CPU twin motion() has no jitter inputs for that reason. R20J (run 20) measured it in game.
        {
            double ax, ay;
            CHECK(motion(identity(), 100.0, 200.0, 0.73, 2560, 1440, 0.0, 0.0, &ax, &ay) && ax == 0.0 && ay == 0.0);
        }
    }
    // (6) history resets
    {
        Camera a, b;
        a.have = b.have = true;
        a.V = V;
        a.fov = b.fov = fov;
        a.frame = 10;
        b.frame = 11;
        b.V = view(1234.5 + 3.0, -356.25, 41.0, 0.7);
        CHECK(camera_resets(a, b) == 0);
        b.V = view(1234.5 + 11.0, -356.25, 41.0, 0.7);
        CHECK(camera_resets(a, b) == RESET_JUMP);
        b.V = V;
        b.fov = fov + 3.0 * 3.14159265 / 180.0;
        CHECK(camera_resets(a, b) == RESET_FOV);
        b.fov = fov;
        b.frame = 10;
        b.flags = 1;
        CHECK(camera_resets(a, b) == (RESET_PAUSE | RESET_CUT));
        CHECK(camera_resets(a, Camera{}) == RESET_NO_CAMERA);
    }
    // (Wave B WB1a) the parked gate's still flag: game frame advanced, < 1 mm, no turn
    {
        Camera a, b;
        a.have = b.have = true;
        a.V = b.V = V;
        a.frame = 10;
        b.frame = 11;
        CHECK(camera_still(a, b));
        b.frame = 10;
        CHECK(!camera_still(a, b));   // paused: not still (the frame did not advance)
        b.frame = 11;
        b.V = view(1234.5 + 0.0005, -356.25, 41.0, 0.7);
        CHECK(camera_still(a, b));
        b.V = view(1234.5 + 0.002, -356.25, 41.0, 0.7);
        CHECK(!camera_still(a, b));
        b.V = view(1234.5, -356.25, 41.0, 0.7 + 1e-4);
        CHECK(!camera_still(a, b));
        CHECK(!camera_still(Camera{}, b));
    }
    // (Wave B WB1a) H-lag candidates: with VP_k = V_g x P, RG equals R; R maps this frame's clip of a world point to the
    // previous frame's, R2 to the frame before that
    {
        const M4 V0 = view(1234.5, -356.25, 41.0, 0.7), V1 = view(1235.5, -356.0, 41.0, 0.71),
                 V2 = view(1236.6, -355.7, 41.1, 0.72);
        LagInputs li;
        li.vp = mul(V2, P);
        li.vp_prev = mul(V1, P);
        li.vp_prev2 = mul(V0, P);
        li.V = V2;
        li.P = P;
        li.V_prev = V1;
        li.P_prev = P;
        li.prev = li.prev2 = li.cam = true;
        M4 R, R2, RG;
        CHECK(lag_candidates(li, &R, &R2, &RG) == 7u);
        double dmax = 0.0;
        for (int i = 0; i < 4; ++i)
            for (int j = 0; j < 4; ++j)
                dmax = std::max(dmax, std::fabs(R.m[i][j] - RG.m[i][j]));
        CHECK(dmax < 1e-9);
        const double X[4] = {1250.0, -350.0, 45.0, 1.0};
        const auto clip = [&](const M4 &m, double out[4]) {
            for (int j = 0; j < 4; ++j)
                out[j] = X[0] * m.m[0][j] + X[1] * m.m[1][j] + X[2] * m.m[2][j] + X[3] * m.m[3][j];
        };
        double c2[4], c1[4], c0[4];
        clip(li.vp, c2);
        clip(li.vp_prev, c1);
        clip(li.vp_prev2, c0);
        const double n2[4] = {c2[0] / c2[3], c2[1] / c2[3], c2[2] / c2[3], 1.0};
        for (int pass = 0; pass < 2; ++pass) {
            const M4 &M = pass ? R2 : R;
            const double *want = pass ? c0 : c1;
            double r[4];
            for (int j = 0; j < 4; ++j)
                r[j] = n2[0] * M.m[0][j] + n2[1] * M.m[1][j] + n2[2] * M.m[2][j] + n2[3] * M.m[3][j];
            CHECK(std::fabs(r[0] / r[3] - want[0] / want[3]) < 1e-9 && std::fabs(r[1] / r[3] - want[1] / want[3]) < 1e-9 &&
                  std::fabs(r[2] / r[3] - want[2] / want[3]) < 1e-9);
        }
        li.prev2 = li.cam = false;
        CHECK(lag_candidates(li, &R, &R2, &RG) == 1u);
    }
}

// Remaster run 14 B1 height fog (host/gpu/metal/atmosphere.h): the maths twin of the Metal pass against an
// independent double reference (numeric integration), zero density = the exact picture, the height falloff, the
// camera reconstruction from a WORLD view-projection, the start distance, the opacity cap and the glow direction.
static double atm_ref_density(double h, double rho, double base, double H) {
    return rho * (h <= base ? 1.0 : std::exp(-(h - base) / H));
}
static double atm_ref_tau(double h0, double dz, double s, const double l[2][3]) {
    const int n = 20000;
    double tau = 0.0;
    for (int i = 0; i < n; ++i) {
        const double t = (i + 0.5) * s / n, h = h0 + t * dz;
        tau += (atm_ref_density(h, l[0][0], l[0][1], l[0][2]) + atm_ref_density(h, l[1][0], l[1][1], l[1][2])) * s / n;
    }
    return tau;
}
static remaster_fnd::M4 atm_test_vp(const double e[3], const double f_in[3], double fov, double aspect, double zn,
                                    double zf) {
    double f[3], r[3], dn[3];
    const double fl = std::sqrt(f_in[0] * f_in[0] + f_in[1] * f_in[1] + f_in[2] * f_in[2]);
    for (int i = 0; i < 3; ++i)
        f[i] = f_in[i] / fl;
    // right = f x up (z up), down = f x right
    r[0] = f[1];
    r[1] = -f[0];
    r[2] = 0.0;
    const double rl = std::sqrt(r[0] * r[0] + r[1] * r[1]);
    for (int i = 0; i < 3; ++i)
        r[i] /= rl;
    dn[0] = f[1] * r[2] - f[2] * r[1];
    dn[1] = f[2] * r[0] - f[0] * r[2];
    dn[2] = f[0] * r[1] - f[1] * r[0];
    remaster_fnd::M4 V{}, P{};
    for (int i = 0; i < 3; ++i) {
        V.m[i][0] = r[i];
        V.m[i][1] = dn[i];
        V.m[i][2] = f[i];
    }
    V.m[3][0] = -(e[0] * r[0] + e[1] * r[1] + e[2] * r[2]);
    V.m[3][1] = -(e[0] * dn[0] + e[1] * dn[1] + e[2] * dn[2]);
    V.m[3][2] = -(e[0] * f[0] + e[1] * f[1] + e[2] * f[2]);
    V.m[3][3] = 1.0;
    const double ty = std::tan(fov / 2);
    P.m[0][0] = 1.0 / (ty * aspect);
    P.m[1][1] = -1.0 / ty;   // view y down -> clip y up (the game's camera)
    P.m[2][2] = zf / (zf - zn);
    P.m[3][2] = -zn * zf / (zf - zn);
    P.m[2][3] = 1.0;
    return remaster_fnd::mul(V, P);
}
static void test_atmosphere() {
    using namespace remaster_atm;
    // 1. one layer, analytic vs numeric: level, up, down, crossing the base both ways, camera below the base
    {
        const double l[2][3] = {{0.0, 0.0, 1.0}, {4e-3, 25.0, 15.0}};
        const float cases[][3] = {{30, 0, 800}, {10, 0, 800}, {90, -0.05f, 1500}, {10, 0.08f, 1500}, {200, -0.2f, 1200},
                                  {24.99f, 0.0001f, 900}, {60, 0.3f, 300}, {25, -0.0005f, 2000}, {5, -0.3f, 500}};
        for (const auto &c : cases) {
            const double ref = atm_ref_tau(c[0], c[1], c[2], l);
            const double got = atm_layer(c[0], c[1], c[2], 4e-3f, 25.0f, 15.0f);
            CHECK(std::fabs(got - ref) <= 1e-3 * std::max(ref, 1e-3));
        }
        // height falloff: a level ray at base + k H has exp(-k) of the density at the base; below the base: flat
        const float at_base = atm_layer(25.0f, 0.0f, 1000.0f, 4e-3f, 25.0f, 15.0f);
        CHECK(std::fabs(at_base - 4.0f) < 1e-4f);
        for (int k = 1; k <= 4; ++k)
            CHECK(std::fabs(atm_layer(25.0f + 15.0f * k, 0.0f, 1000.0f, 4e-3f, 25.0f, 15.0f) / at_base -
                            (float)std::exp(-k)) < 1e-4f);
        CHECK(atm_layer(5.0f, 0.0f, 1000.0f, 4e-3f, 25.0f, 15.0f) == at_base);
        CHECK(atm_layer(1500.0f, 0.0f, 1000.0f, 4e-3f, 25.0f, 15.0f) >= 0.0f);   // underflow, no NaN
        CHECK(std::isfinite(atm_layer(1500.0f, -1.0f, 1400.0f, 4e-3f, 25.0f, 15.0f)));
        CHECK(atm_layer(30.0f, 0.0f, 0.0f, 4e-3f, 25.0f, 15.0f) == 0.0f && atm_layer(30.0f, 0.0f, 100.0f, 0.0f, 25.0f, 15.0f) == 0.0f);
    }
    // 2. the camera from a WORLD view-projection, and the whole pixel against the reference
    const double eye[3] = {2714.7, 918.9, 92.0}, fwd[3] = {0.0, -1.0, -0.02};
    const remaster_fnd::M4 vp = atm_test_vp(eye, fwd, 1.0, 16.0 / 9.0, 0.5, 3000.0);
    Params p{};
    p.a[0] = 0.3f, p.a[1] = -0.8f, p.a[2] = 0.2f, p.a[3] = 0.75f;          // sun, g
    p.a[4] = 0.6e-4f, p.a[5] = 1.1e-4f, p.a[6] = 2.4e-4f, p.a[11] = 0.6f;    // Br (blue), sun intensity
    p.a[8] = 1.0e-4f, p.a[9] = 1.0e-4f, p.a[10] = 1.0e-4f;                  // Bm (grey)
    p.a[12] = 1.0f, p.a[13] = 0.9f, p.a[14] = 0.74f;
    p.b[0] = 2e-4f, p.b[1] = 0.0f, p.b[2] = 600.0f, p.b[3] = 4e-3f, p.b[4] = 25.0f, p.b[5] = 15.0f;
    p.b[6] = 20.0f, p.b[7] = 2500.0f, p.b[8] = 0.95f, p.b[9] = 0.1f, p.b[10] = 0.12f, p.b[11] = 0.15f;
    p.b[12] = 1.0f, p.b[13] = 4.0f, p.b[15] = 1.0f;
    CHECK(atm_active(p));
    AtmU u;
    CHECK(atm_uniforms(vp.m, p, false, &u));
    CHECK(std::fabs(u.eye[0] - eye[0]) < 1e-2 && std::fabs(u.eye[1] - eye[1]) < 1e-2 && std::fabs(u.eye[2] - eye[2]) < 1e-2);
    const double l[2][3] = {{2e-4, 0.0, 600.0}, {4e-3, 25.0, 15.0}};
    const double sl = std::sqrt(0.09 + 0.64 + 0.04), sun[3] = {0.3 / sl, -0.8 / sl, 0.2 / sl};
    int checked = 0;
    for (int k = 0; k < 64; ++k) {
        // world points ahead of the camera: across the bay, low and high, near and far
        const double dist = 30.0 + 2400.0 * ((k * 37) % 64) / 63.0;
        const double ang = -0.5 + (k % 8) / 7.0, el = -0.12 + 0.03 * (k / 8);
        double X[4] = {eye[0] + dist * std::sin(ang) * std::cos(el), eye[1] - dist * std::cos(ang) * std::cos(el),
                       eye[2] + dist * std::sin(el), 1.0}, c[4];
        remaster_fnd::xform(X, vp, c);
        if (!(c[3] > 0.5) || std::fabs(c[0] / c[3]) > 1.0 || std::fabs(c[1] / c[3]) > 1.0)
            continue;
        const float nx = (float)(c[0] / c[3]), ny = (float)(c[1] / c[3]), d = (float)(c[2] / c[3]);
        const AtmOut o = atm_pixel(u, nx, ny, d);
        const double dir[3] = {(X[0] - eye[0]) / dist, (X[1] - eye[1]) / dist, (X[2] - eye[2]) / dist};
        const double s = std::min(dist, 2500.0), s0 = 20.0;
        const double tau = s > s0 ? atm_ref_tau(eye[2] + dir[2] * s0, dir[2], s - s0, l) : 0.0;
        const double T = std::max(std::exp(-tau), 0.05);
        // the far points sit near depth 1, where float depth carries metres of error: tolerance grows with distance
        CHECK(std::fabs(o.t - T) < 2e-3 + 2e-6 * dist);
        const double cth = dir[0] * sun[0] + dir[1] * sun[1] + dir[2] * sun[2];
        const double pr = 0.75 * (1 + cth * cth), kk = 1 + 0.75 * 0.75 - 1.5 * cth;
        const double pm = std::min((1 - 0.5625) / (kk * std::sqrt(kk)), 4.0);
        const double br[3] = {0.6e-4, 1.1e-4, 2.4e-4}, sc[3] = {1.0, 0.9, 0.74}, amb[3] = {0.1, 0.12, 0.15};
        const float got[3] = {o.r, o.g, o.b};
        for (int i = 0; i < 3; ++i) {
            const double ref = sc[i] * 0.6 * (br[i] * pr + 1e-4 * pm) / (br[i] + 1e-4) + amb[i];
            CHECK(std::fabs(got[i] - ref) < 2e-3 * std::max(1.0, ref) + 1e-3);
        }
        ++checked;
    }
    CHECK(checked >= 40);
    // nearer than the start distance: exactly clear; the sky (depth 1) uses the far distance; the cap holds
    CHECK(atm_pixel(u, 0.0f, 0.0f, 0.0f).t == 1.0f);
    {
        Params q = p;
        q.b[0] = 1.0f;   // absurd haze: the max opacity caps it
        AtmU uq;
        CHECK(atm_uniforms(vp.m, q, false, &uq));
        CHECK(std::fabs(atm_pixel(uq, 0.0f, 0.0f, 1.0f).t - 0.05f) < 1e-6f);
    }
    // 3. zero density (or strength 0): no pass, and the maths alone gives T = 1 and the exact picture
    {
        Params z = p;
        z.b[0] = z.b[3] = 0.0f;
        CHECK(!atm_active(z));
        Params zs = p;
        zs.b[12] = 0.0f;
        CHECK(!atm_active(zs));
        for (const Params *q : {&z, &zs}) {
            AtmU uz;
            CHECK(atm_uniforms(vp.m, *q, false, &uz));
            uint32_t seed = 12345u;
            bool exact = true;
            for (int i = 0; i < 4096; ++i) {
                seed = seed * 1664525u + 1013904223u;
                const float nx = (float)((seed >> 8) & 0xffff) / 32768.0f - 1.0f;
                const float ny = (float)((seed >> 4) & 0xffff) / 32768.0f - 1.0f;
                const float d = (float)(seed >> 9) / 8388608.0f;
                const AtmOut o = atm_pixel(uz, nx, ny, d);
                const float col = (float)(seed & 0xff) / 255.0f * 3.0f;   // HDR values too
                exact = exact && o.t == 1.0f && atm_blend(col, o) == col;
            }
            CHECK(exact);
        }
    }
    // 4. the glow: toward the sun brighter and warmer (more Mie) than away from it; away is bluer
    {
        Params q = p;
        q.a[0] = 0.0f, q.a[1] = -1.0f, q.a[2] = 0.05f;   // the sun ahead, low
        AtmU uq;
        CHECK(atm_uniforms(vp.m, q, false, &uq));
        const AtmOut toward = atm_pixel(uq, 0.0f, 0.1f, 1.0f);
        Params b2 = q;
        b2.a[1] = 1.0f;   // behind the camera
        AtmU ub;
        CHECK(atm_uniforms(vp.m, b2, false, &ub));
        const AtmOut away = atm_pixel(ub, 0.0f, 0.1f, 1.0f);
        CHECK(toward.r > 2.0f * away.r && toward.g > away.g);
        CHECK(away.b / away.r > toward.b / toward.r);
    }
    // 6. COMB14B: the view-projection pick. The scene VP arrives as last-bit variants (one draw each) and is outnumbered
    // by an instanced object's W x VP; with the camera the scene cluster is chosen, its P kept for a frame without it.
    {
        using namespace remaster_fnd;
        remaster_fnd::M4 V{};
        const remaster_fnd::M4 I = identity();
        // V alone: atm_test_vp with an identity projection is not available, so recover V = VP x P^-1 from the test VP
        const double ty = std::tan(0.5);
        remaster_fnd::M4 P{};
        P.m[0][0] = 1.0 / (ty * 16.0 / 9.0), P.m[1][1] = -1.0 / ty, P.m[2][2] = 3000.0 / 2999.5;
        P.m[3][2] = -0.5 * 3000.0 / 2999.5, P.m[2][3] = 1.0;
        remaster_fnd::M4 Pinv{};
        CHECK(inverse(P, &Pinv));
        V = mul(vp, Pinv);
        auto block = [](const remaster_fnd::M4 &m, float *c, float wiggle) {
            for (int i = 0; i < 4; ++i)
                for (int j = 0; j < 4; ++j)
                    c[4 * j + i] = (float)m.m[i][j] * (1.0f + wiggle);
        };
        remaster_fnd::M4 W = I;
        W.m[3][0] = 40.0, W.m[3][1] = -12.0, W.m[3][2] = 3.0;   // an instanced object 40 m away
        const remaster_fnd::M4 obj = mul(W, vp);
        Plurality pl;
        float c[16];
        for (int k = 0; k < 12; ++k) {   // the scene: 12 draws, 12 last-bit variants
            pl.world();
            block(vp, c, (float)(k % 3) * 1.2e-7f);
            pl.add(c);
        }
        for (int k = 0; k < 40; ++k) {   // the object: 40 draws, one block
            pl.world();
            block(obj, c, 0.0f);
            pl.add(c);
        }
        remaster_fnd::M4 kept{}, got{};
        bool have = false;
        CHECK(atm_choose_vp(pl, &V, &kept, &have, &got) == ATM_VP_CAMERA && have);
        double vs = 1.0;
        for (int i = 0; i < 4; ++i)
            for (int j = 0; j < 4; ++j)
                vs = std::max(vs, std::fabs(vp.m[i][j]));
        CHECK(max_abs_diff(got, vp) < 1e-5 * vs);
        CHECK(pl.e.size() >= 3);   // the variants really are distinct blocks
        AtmU uc;
        CHECK(atm_uniforms(got.m, p, false, &uc) && std::fabs(uc.eye[2] - eye[2]) < 0.05);
        Plurality few;   // a frame with only the object: the camera and the kept P
        for (int k = 0; k < 40; ++k) {
            few.world();
            block(obj, c, 0.0f);
            few.add(c);
        }
        CHECK(atm_choose_vp(few, &V, &kept, &have, &got) == ATM_VP_KEPT_P);
        CHECK(max_abs_diff(got, vp) < 1e-5 * vs);
        bool none = false;
        CHECK(atm_choose_vp(few, &V, &kept, &none, &got) == ATM_VP_NONE);           // no camera match, nothing kept
        CHECK(atm_choose_vp(few, nullptr, &kept, &none, &got) == ATM_VP_CLUSTER);   // no camera: the share rule
    }
    // 5. a singular camera is refused; the Metal source carries the same maths with the Metal address space
    {
        double zero[4][4] = {};
        CHECK(!atm_uniforms(zero, p, false, &u));
        const std::string src = atm_source();
        CHECK(src.find("#define ATM_CONST constant") != std::string::npos);
        CHECK(src.find("atm_pixel(ATM_CONST AtmU &u") != std::string::npos ||
              src.find("atm_pixel(ATM_CONST AtmU & u") != std::string::npos);
        CHECK(src.find("fragment float4 atm_fs") != std::string::npos && src.find("ATM_MATHS") == std::string::npos);
    }
}

// Remaster run 14 lit smoke (review/run14/smoke-realism-design.md 4 stage 1, 7): the variant text, off = unchanged,
// the lighting twin (smoke_light.h) and the camera from clip rows. SMOKE_MSL_OUT=<dir> writes the generated particle
// pair per kind for a runtime Metal compile.
namespace smk {
static std::vector<uint8_t> bytes(const std::vector<uint32_t> &w) {
    std::vector<uint8_t> b(w.size() * 4);
    memcpy(b.data(), w.data(), b.size());
    return b;
}
// The particle pixel shader as the census shows it (IDI_PARTICLES_FX ps 97086d94d4253d2d): ps_2_0, texldp of the
// shadow map at t1, the sprite at t0, x vertex colour x 2, x saturate(shadow + c0.x).
static std::vector<uint8_t> ps() {
    return bytes({0xFFFF0200u,
                  0x0200001Fu, 0x80000000u, 0xB00F0000u,   // dcl t0
                  0x0200001Fu, 0x80000000u, 0xB00F0001u,   // dcl t1
                  0x0200001Fu, 0x80000000u, 0x900F0000u,   // dcl v0
                  0x0200001Fu, 0x90000000u, 0xA00F0800u,   // dcl_2d s0
                  0x0200001Fu, 0x90000000u, 0xA00F0801u,   // dcl_2d s1
                  0x03010042u, 0x800F0001u, 0xB0E40001u, 0xA0E40801u,   // texldp r1, t1, s1
                  0x03000042u, 0x800F0000u, 0xB0E40000u, 0xA0E40800u,   // texld r0, t0, s0
                  0x03000002u, 0x80180001u, 0x80000001u, 0xA0000000u,   // add_sat r1.w, r1.x, c0.x
                  0x03000005u, 0x800F0000u, 0x80E40000u, 0x90E40000u,   // mul r0, r0, v0
                  0x03000002u, 0x800F0000u, 0x80E40000u, 0x80E40000u,   // add r0, r0, r0
                  0x03000005u, 0x80070000u, 0x80FF0001u, 0x80E40000u,   // mul r0.xyz, r1.w, r0
                  0x02000001u, 0x800F0800u, 0x80E40000u,                // mov oC0, r0
                  0x0000FFFFu});
}
// The particle vertex shader (vs 888602da494b62ed): vs_2_0, POSITION v0, COLOR v1, TEXCOORD v2; oPos = c0..c3 . v0,
// oT1 = c4..c7 . v0 (the shadow coordinate), oD0 = v1, oT0.xy = v2. t7 free.
static std::vector<uint8_t> vs(bool writes_t7 = false) {
    std::vector<uint32_t> w = {0xFFFE0200u, 0x0200001Fu, 0x80000000u, 0x900F0000u, 0x0200001Fu, 0x8000000Au, 0x900F0001u,
                               0x0200001Fu, 0x80000005u, 0x900F0002u};
    for (int k = 0; k < 4; ++k)
        w.insert(w.end(), {0x03000009u, 0xC0000000u | (1u << (16 + k)), 0x90E40000u, 0xA0E40000u | (uint32_t)k});
    for (int k = 0; k < 4; ++k)
        w.insert(w.end(), {0x03000009u, 0xE0000001u | (1u << (16 + k)), 0x90E40000u, 0xA0E40000u | (uint32_t)(4 + k)});
    w.insert(w.end(), {0x02000001u, 0xD00F0000u, 0x90E40001u});   // mov oD0, v1
    w.insert(w.end(), {0x02000001u, 0xE0030000u, 0x90E40002u});   // mov oT0.xy, v2
    if (writes_t7)
        w.insert(w.end(), {0x02000001u, 0xE00F0007u, 0x90E40000u});   // mov oT7, v0
    w.push_back(0x0000FFFFu);
    return bytes(w);
}
static void write(const char *dir, const std::string &name, const std::string &src) {
    if (FILE *fp = fopen((std::string(dir) + "/" + name).c_str(), "wb")) {
        fputs(src.c_str(), fp);
        fclose(fp);
    }
}
} // namespace smk

static void test_lit_smoke() {
    using namespace remaster_smoke;
    const d9sh::Program &pp = d9sh::program_for(smk::ps()), &vp = d9sh::program_for(smk::vs());
    CHECK(pp.ok && pp.pixel && vp.ok && !vp.pixel);
    d9msl::PixelVariant base;
    base.depth_mask = 2;   // the shadow map at stage 1, as the kit binds it
    std::string plain, plain2, lit, vplain, vlit, why;
    CHECK(d9msl::pixel_source(pp, base, &plain, &why));
    // (a) off: byte-identical text (smoke 0 is the default variant), and no smoke names in it
    d9msl::PixelVariant zero = base;
    zero.smoke = 0;
    CHECK(d9msl::pixel_source(pp, zero, &plain2, &why) && plain2 == plain);
    CHECK(plain.find("smk_") == std::string::npos && plain.find("D9SmokeParams") == std::string::npos);
    CHECK(d9msl::vertex_source(vp, d9msl::VertexVariant{}, &vplain, &why) && vplain.find("run 14 lit smoke") == std::string::npos);
    // (b) the variant: parameter block, the epilogue before the alpha test and fog, the shadow as the sun's visibility
    d9msl::VertexVariant vv;
    vv.smoke = 1;
    CHECK(d9msl::vertex_source(vp, vv, &vlit, &why));
    CHECK(vlit.find("    ot[7] = float4(vv[0].xyz, 1.0);") != std::string::npos);
    CHECK(vlit.find("ot[7] = float4(vv[0]") < vlit.find("o.t4 = ot[4]; o.t5 = ot[5]; o.t6 = ot[6]; o.t7 = ot[7];"));
    std::string why7;
    CHECK(!d9msl::vertex_source(d9sh::program_for(smk::vs(true)), vv, &vlit, &why7) && why7.find("t7") != std::string::npos);
    CHECK(d9msl::vertex_source(vp, vv, &vlit, &why));
    for (uint8_t kind = 1; kind <= KIND_MAX; ++kind) {
        d9msl::PixelVariant v = base;
        v.smoke = kind;
        CHECK(d9msl::pixel_source(pp, v, &lit, &why));
        CHECK(lit.find("constant D9SmokeParams &SP [[buffer(5)]]") != std::string::npos);
        CHECK(lit.find("struct D9SmokeParams { float4 sun; float4 eye; float4 k; float4 k2; float4 amb; float4 light; "
                       "float4 lcol; float4 nearf; };") != std::string::npos);
        const size_t e = lit.find("// run 14 lit smoke, kind " + std::to_string(kind)), at = lit.find("if (P.alpha_func != 8"),
                     fog = lit.find("if (P.fog_mode != 0)"), col = lit.find("float4 col = oc[0];");
        CHECK(e != std::string::npos && col < e && e < at && at < fog);
        CHECK(lit.find("smk_vis *= d9_shadow(tx1, t[1] / t[1].w).r;") != std::string::npos);
        const bool flat = kind == KIND_FOG, flare = kind == KIND_FLARE_RED || kind == KIND_FLARE_GREEN;
        CHECK((lit.find("dfdx(smk_p)") == std::string::npos) == flat);   // fog: no sprite normal
        CHECK((lit.find("const float smk_wrap = 0.5;") != std::string::npos) == flat);
        CHECK((lit.find("float3(0.90, 0.96, 1.08)") != std::string::npos) == flat);   // fog's grey-blue shade
        CHECK((lit.find("SP.lcol.rgb * (SP.light.w") != std::string::npos) == flare);
        CHECK((lit.find("smoothstep(0.0, max(SP.amb.w") != std::string::npos) == (kind == KIND_STEAM));
        CHECK(lit.find("col.a *= saturate((1.0 / vin.pos.w - SP.nearf.x) * SP.nearf.y);") != std::string::npos);
        d9msl::PixelVariant k2 = base;
        k2.smoke = kind == 1 ? 2 : 1;
        CHECK(v.key() != base.key() && v.key() != k2.key());
        if (const char *dir = getenv("SMOKE_MSL_OUT")) {
            smk::write(dir, "kind" + std::to_string(kind) + ".ps.metal", lit);
            d9msl::PixelVariant vs2 = v;
            vs2.soft = 1;   // with the soft fade too (fog's wide fade)
            std::string ls;
            CHECK(d9msl::pixel_source(pp, vs2, &ls, &why));
            smk::write(dir, "kind" + std::to_string(kind) + ".soft.ps.metal", ls);
        }
    }
    if (const char *dir = getenv("SMOKE_MSL_OUT")) {
        smk::write(dir, "smoke.vs.metal", vlit);
        smk::write(dir, "plain.ps.metal", plain);
    }
    {   // stage 0 must be a plain 2D sprite
        d9msl::PixelVariant v = base;
        v.smoke = 1;
        v.depth_mask = 3;
        std::string w0;
        CHECK(!d9msl::pixel_source(pp, v, &lit, &w0) && w0.find("smoke") != std::string::npos);
        v.depth_mask = 2;
        v.smoke = 8;
        CHECK(!d9msl::pixel_source(pp, v, &lit, &w0));
        d9msl::VertexVariant a, b;
        b.smoke = 1;
        CHECK(a.key() != b.key());
    }
    // (c) the lighting twin: the side-lit reference is exactly 1; facing the sun brighter, sun behind flatter; thin
    // parts glow more when backlit (silver lining); no sun or strength 0 = unchanged; fog's glow is the weakest
    for (uint32_t kind = 1; kind <= KIND_MAX; ++kind) {
        const Kind k = kind_defaults(kind);
        CHECK(std::fabs(ratio(k, 0.5f, 0.5f, 0.0f, 1.0f) - 1.0f) < 1e-6f);
        CHECK(std::fabs(factor(k, 0.9f, 0.2f, 0.95f, 0.0f) - 1.0f) < 1e-6f);   // in full shade: the game's colour
        CHECK(ratio(k, 0.5f, 0.5f, 0.95f, 1.0f) > 1.05f);    // looking into the sun
        CHECK(ratio(k, 0.5f, 0.5f, -0.95f, 1.0f) < 1.0f);    // sun behind the viewer
        CHECK(ratio(k, 0.5f, 0.1f, 0.95f, 1.0f) > ratio(k, 0.5f, 0.9f, 0.95f, 1.0f));   // thin edges glow
        if (!k.flat) {
            CHECK(ratio(k, 1.0f, 0.8f, 0.0f, 1.0f) > ratio(k, 0.0f, 0.8f, 0.0f, 1.0f));   // lit top vs underside
            CHECK(ratio(k, 0.0f, 0.9f, 0.0f, 1.0f) >= 0.35f);
        }
        Kind off = k;
        off.s = 0.0f;
        CHECK(factor(off, 1.0f, 0.1f, 1.0f, 1.0f) == 1.0f);
    }
    CHECK(std::fabs(hg(0.3f, 0.0f) - 1.0f) < 1e-6f);   // isotropic: 1 everywhere
    {   // HG is normalised: its mean over the sphere (mu uniform in [-1, 1]) is 1
        double m = 0.0;
        for (int i = 0; i < 20000; ++i)
            m += hg(-1.0f + (i + 0.5f) / 10000.0f, 0.75f);
        CHECK(std::fabs(m / 20000.0 - 1.0) < 0.01);
    }
    CHECK(ratio(kind_defaults(KIND_FOG), 0.5f, 0.3f, 0.95f, 1.0f) < ratio(kind_defaults(KIND_STACK_STEAM), 0.5f, 0.3f, 0.95f, 1.0f));
    // (d) the camera from clip rows: a look-at view x projection, eye (10, -20, 3)
    {
        const float ex = 10.0f, ey = -20.0f, ez = 3.0f;
        // view: x right = (1,0,0), y up = (0,0,1), z forward = (0,1,0) (D3D row-vector, transposed into clip rows)
        const float R[3][3] = {{1, 0, 0}, {0, 0, 1}, {0, 1, 0}};
        const float t[3] = {-(R[0][0] * ex + R[0][1] * ey + R[0][2] * ez), -(R[1][0] * ex + R[1][1] * ey + R[1][2] * ez),
                            -(R[2][0] * ex + R[2][1] * ey + R[2][2] * ez)};
        const float xs = 1.2f, ys = 1.6f, zn = 0.5f, zf = 5000.0f, A = zf / (zf - zn), B = -zn * zf / (zf - zn);
        float rows[16] = {xs * R[0][0], xs * R[0][1], xs * R[0][2], xs * t[0], ys * R[1][0], ys * R[1][1], ys * R[1][2], ys * t[1],
                          A * R[2][0],  A * R[2][1],  A * R[2][2],  A * t[2] + B, R[2][0], R[2][1], R[2][2], t[2]};
        float eye[3];
        CHECK(eye_from_clip(rows, rows + 4, rows + 12, eye));
        CHECK(std::fabs(eye[0] - ex) < 1e-3f && std::fabs(eye[1] - ey) < 1e-3f && std::fabs(eye[2] - ez) < 1e-3f);
        // the block: sun normalised with its strength in w, flare position only for flares, near fade for fog
        uint32_t rs[256] = {};
        rs[RS_KIND] = KIND_FOG;
        const float sun[3] = {0.0f, 0.6f, 0.3f};
        memcpy(&rs[RS_SUN], sun, 12);
        D9SmokeParams p;
        CHECK(fill(rs, rows, 0.0f, kind_defaults(KIND_FOG), &p));
        CHECK(std::fabs(p.sun[3] - std::sqrt(0.45f)) < 1e-5f && std::fabs(p.sun[1] - 0.6f / std::sqrt(0.45f)) < 1e-5f);
        CHECK(std::fabs(p.eye[1] - ey) < 1e-3f && p.light[3] == 0.0f && std::fabs(p.nearf[1] - 1.0f / 3.0f) < 1e-6f);
        CHECK(std::fabs(p.amb[1] - ref_term(kind_defaults(KIND_FOG))) < 1e-7f);
        rs[RS_KIND] = KIND_FLARE_RED;
        const float pos[3] = {5.0f, 6.0f, 7.0f};
        memcpy(&rs[RS_POS], pos, 12);
        CHECK(fill(rs, rows, 1.0f, kind_defaults(KIND_FLARE_RED), &p) && p.light[0] == 5.0f && p.light[2] == 7.0f);
        CHECK(p.light[3] > 0.4f && p.light[3] < 0.7f && p.lcol[0] == 1.0f && p.nearf[1] == 0.0f);
        memset(&rs[RS_SUN], 0, 12);   // no sun: strength 0, still drawn lit (the fades), ratio 1
        CHECK(fill(rs, rows, 0.0f, kind_defaults(KIND_FLARE_RED), &p) && p.sun[3] == 0.0f && p.sun[2] == 1.0f);
        rs[RS_KIND] = 0;
        CHECK(!fill(rs, rows, 0.0f, kind_defaults(1), &p));
        rs[RS_KIND] = 8;
        CHECK(!fill(rs, rows, 0.0f, kind_defaults(1), &p));
        rs[RS_KIND] = 1;
        CHECK(!fill(rs, nullptr, 0.0f, kind_defaults(1), &p));
        static_assert(sizeof(D9SmokeParams) == 128, "eight float4, as the MSL struct");
    }
}

// Owner 8 Oct (Off / 2x / 4x): the D3D9 multisample request as a sample count, the 2x cap the core mod sets, and the
// renderer's fall to the next lower supported count. Unset cap: every count exactly as before (4x stays 4x).
// UG1 run 6 Xbox-style fog (PixelVariant / VertexVariant xfog) and headlight fade (PixelVariant hlfade): off =
// byte-identical text, the variant text, refusals. UG1_MSL_OUT=<dir> writes the patched sources for an xcrun metal compile.
namespace ug1 {
// worldheadlight.efx pixel_shader as ps_1_1: tex t0..t2; mul r0, t1, t2; mad r0, r0, c0, v0; mul_x2 r0, r0, t0.
static std::vector<uint8_t> headlight_ps() {
    return smk::bytes({0xFFFF0101u,
                       0x00000042u, 0xB00F0000u,                                     // tex t0
                       0x00000042u, 0xB00F0001u,                                     // tex t1
                       0x00000042u, 0xB00F0002u,                                     // tex t2
                       0x00000005u, 0x800F0000u, 0xB0E40001u, 0xB0E40002u,           // mul r0, t1, t2
                       0x00000004u, 0x800F0000u, 0x80E40000u, 0xA0E40000u, 0x90E40000u,   // mad r0, r0, c0, v0
                       0x00000005u, 0x810F0000u, 0x80E40000u, 0xB0E40000u,           // mul_x2 r0, r0, t0
                       0x0000FFFFu});
}
// A ps_1_1 world program without the headlight stages: tex t0; mul_x2 r0, t0, v0.
static std::vector<uint8_t> world_ps() {
    return smk::bytes({0xFFFF0101u, 0x00000042u, 0xB00F0000u,
                       0x00000005u, 0x810F0000u, 0xB0E40000u, 0x90E40000u, 0x0000FFFFu});
}
static std::vector<uint8_t> fog_vs() {   // smk::vs() plus mov oFog, v0.x
    std::vector<uint8_t> b = smk::vs();
    const uint32_t add[] = {0x02000001u, 0xC0010001u, 0x90000000u};
    b.insert(b.end() - 4, (const uint8_t *)add, (const uint8_t *)add + sizeof add);
    return b;
}
} // namespace ug1

static void test_ug1_looks() {
    const char *out = getenv("UG1_MSL_OUT");
    const d9sh::Program &vp = d9sh::program_for(smk::vs()), &hp = d9sh::program_for(ug1::headlight_ps()),
                        &wp = d9sh::program_for(ug1::world_ps()), &fv = d9sh::program_for(ug1::fog_vs());
    CHECK(vp.ok && hp.ok && hp.pixel && hp.major == 1 && wp.ok && fv.ok);
    std::string why, a, b;
    // fog, vertex half: o.fog carries clip y; off = unchanged; a program writing oFog is refused
    d9msl::VertexVariant vv;
    CHECK(d9msl::vertex_source(vp, vv, &a, &why) && a.find("o.fog = ofog.x;") != std::string::npos);
    vv.xfog = 1;
    CHECK(d9msl::vertex_source(vp, vv, &b, &why) && b.find("o.fog = opos.y;") != std::string::npos &&
          b.find("o.fog = ofog.x;") == std::string::npos);
    CHECK(vv.key() != d9msl::VertexVariant{}.key());
    std::string wf;
    CHECK(!d9msl::vertex_source(fv, vv, &b, &wf) && wf.find("oFog") != std::string::npos);
    if (out)
        smk::write(out, "ug1fog.vs.metal", (d9msl::vertex_source(vp, vv, &b, &why), b));
    // fog, pixel half: the generic D3D fog block is replaced by ours; off = unchanged
    d9msl::PixelVariant pv;
    std::string p0, p1;
    CHECK(d9msl::pixel_source(wp, pv, &p0, &why) && p0.find("if (P.fog_mode != 0)") != std::string::npos);
    pv.xfog = 1;
    CHECK(d9msl::pixel_source(wp, pv, &p1, &why));
    CHECK(p1.find("if (P.fog_mode != 0)") == std::string::npos &&
          p1.find("clamp((1.0 / vin.pos.w - P.fog_start) * P.fog_end, 0.0, P.fog_color.a)") != std::string::npos &&
          p1.find("abs(vin.fog * vin.pos.w) * P.fog_density") != std::string::npos);
    CHECK(pv.key() != d9msl::PixelVariant{}.key());
    if (out)
        smk::write(out, "ug1fog.ps.metal", p1);
    // headlight: the classifier, off = unchanged, modes 1 and 2 insert after the one t[2] line, others fail closed
    CHECK(d9msl::pixel_is_headlight(hp) && !d9msl::pixel_is_headlight(wp));
    d9msl::PixelVariant hv;
    hv.projected_mask = 6;
    std::string h0, h1, h2, hw;
    CHECK(d9msl::pixel_source(hp, hv, &h0, &why) && h0.find("headlight fade") == std::string::npos);
    hv.hlfade = 1;
    CHECK(d9msl::pixel_source(hp, hv, &h1, &why));
    const size_t t2 = h1.find("    t[2] = (tx2.sample(sm2, "), add = h1.find("    t[2] *= saturate(1.0 - tc[1].w * 0.01);");
    CHECK(t2 != std::string::npos && add != std::string::npos && add > t2 && add == h1.find('\n', t2) + 1);
    CHECK(h1.size() > h0.size() && h1.substr(0, add) == h0.substr(0, add) &&
          h1.substr(h1.find('\n', add) + 1) == h0.substr(add));
    hv.hlfade = 2;
    CHECK(d9msl::pixel_source(hp, hv, &h2, &why) && h2.find("saturate((tc[1].w - 1.6) * 0.32)") != std::string::npos &&
          h2.find("t[2].xyz *= float3(1.125, 1.125, 1.056);") != std::string::npos);
    d9msl::PixelVariant wv;
    wv.hlfade = 1;
    CHECK(!d9msl::pixel_source(wp, wv, &hw, &why) && why.find("hlfade") != std::string::npos);
    hv.hlfade = 3;
    CHECK(!d9msl::pixel_source(hp, hv, &hw, &why));
    if (out) {
        smk::write(out, "ug1hl1.ps.metal", h1);
        smk::write(out, "ug1hl2.ps.metal", h2);
        hv.hlfade = 1;
        hv.xfog = 1;   // both on one draw
        std::string hb;
        CHECK(d9msl::pixel_source(hp, hv, &hb, &why));
        smk::write(out, "ug1hlfog.ps.metal", hb);
    }
}

static void test_msaa_count() {
    using namespace d9msaa;
    CHECK(count(0, 0) == 0 && count(1, 0) == 2 && count(1, 1) == 4 && count(2, 0) == 2 && count(4, 0) == 4 &&
          count(8, 0) == 4 && count(3, 0) == 2);
    CHECK(parse_cap(nullptr) == 0 && parse_cap("") == 0 && parse_cap("4") == 0 && parse_cap("2") == 2 &&
          parse_cap("22") == 0 && parse_cap("x") == 0);
    CHECK(capped(4, 0) == 4 && capped(4, 2) == 2 && capped(2, 2) == 2 && capped(0, 2) == 0 && capped(2, 0) == 2);
    // the game's 4x (NONMASKABLE quality 1): 4 without the cap, 2 with it, single-sampled when off
    unsetenv("RECOMP_D3D9_MSAA_SAMPLES");
    CHECK(count_env(1, 1) == 4 && count_env(0, 0) == 0);
    setenv("RECOMP_D3D9_MSAA_SAMPLES", "2", 1);
    CHECK(count_env(1, 1) == 2 && count_env(0, 0) == 0 && count_env(1, 0) == 2);
    setenv("RECOMP_D3D9_MSAA_SAMPLES", "4", 1);
    CHECK(count_env(1, 1) == 4);
    unsetenv("RECOMP_D3D9_MSAA_SAMPLES");
    auto all = [](uint32_t) { return true; };
    auto upto2 = [](uint32_t k) { return k <= 2; };
    auto none = [](uint32_t k) { return k <= 1; };
    CHECK(fit(4, all) == 4 && fit(2, all) == 2 && fit(8, all) == 4 && fit(0, all) == 1 && fit(1, all) == 1);
    CHECK(fit(4, upto2) == 2 && fit(2, upto2) == 2);
    CHECK(fit(4, none) == 1 && fit(2, none) == 1);
}

// ---- Run 15 Wave B (review/run15/waveb-plan.md P0): one test per package; each package owns its own function. ----
// WB1b surface attachments: the S1 class/flag contract and the S0 oct normal round trip (< 0.1 degree).
static void test_surface() {
    using namespace remaster_fnd;
    CHECK(MAT_UNKNOWN == 0 && MAT_ROAD == 1 && MAT_CAR_PAINT == 2 && MAT_GLASS == 5 && MAT_WATER == 6 && MAT_SKY == 10 &&
          MAT_COUNT == 11);
    CHECK((SURF_DYNAMIC | SURF_ALPHA_TEST | SURF_CAMERA_LOCKED | SURF_INSTANCED_W | SURF_EMISSIVE | SURF_DECAL) == 63);
    double worst = 0.0;
    for (int i = 0; i <= 64; ++i)
        for (int j = 0; j < 128; ++j) {
            const double th = M_PI * i / 64.0, ph = 2.0 * M_PI * j / 128.0;
            const double n[3] = {std::sin(th) * std::cos(ph), std::sin(th) * std::sin(ph), std::cos(th)};
            double u = 0, v = 0, m[3];
            oct_encode(n, &u, &v);
            CHECK(std::fabs(u) <= 1.0 && std::fabs(v) <= 1.0);
            // through half precision (S0 is RGBA16F): 11-bit mantissa
            const double hu = std::ldexp(std::round(std::ldexp(u, 11)), -11), hv = std::ldexp(std::round(std::ldexp(v, 11)), -11);
            oct_decode(hu, hv, m);
            const double d = std::min(1.0, n[0] * m[0] + n[1] * m[1] + n[2] * m[2]);
            worst = std::max(worst, std::acos(d) * 180.0 / M_PI);
        }
    CHECK(worst < 0.1);
    FndFrame f;
    CHECK(!f.valid && f.reset == 0 && !f.motion && !f.s0 && !f.s1 && !f.s2 && !f.scene && !f.depth);
    // ---- WB1b (surface.h): class values, priors, the variants' sources, the slot pick, the normal rows ----
    using namespace remaster_surf;
    CHECK(C_ROAD == MAT_ROAD && C_CAR_CHROME == MAT_CAR_CHROME && C_WORLD_NM == MAT_WORLD_NM && C_SKY == MAT_SKY &&
          C_COUNT == MAT_COUNT && (SURF_DERIVED_N & 63) == 0);
    CHECK(car_class(0.9f) == C_CAR_CHROME && car_class(0.3f) == C_CAR_PAINT && car_class(0.1f) == C_CAR_OTHER);
    CHECK(prior(C_GLASS).rough < 0.4f && prior(C_WATER).rough < 0.4f && prior(C_CAR_PAINT).rough < 0.4f &&
          prior(C_WORLD).rough > 0.4f && prior(C_SKY).f0 == 0.0f);
    // a vs_2_0 with POSITION v0 and NORMAL v1: oPos = c0..c3 . v0, oT0 = v1
    std::vector<uint32_t> w = {0xFFFE0200u, 0x0200001Fu, 0x80000000u, 0x900F0000u, 0x0200001Fu, 0x80000003u, 0x900F0001u};
    for (int k = 0; k < 4; ++k)
        w.insert(w.end(), {0x03000009u, 0xC0000000u | (1u << (16 + k)), 0x90E40000u, 0xA0E40000u | (uint32_t)k});
    w.insert(w.end(), {0x02000001u, 0xE00F0000u, 0x90E40001u, 0x0000FFFFu});
    const d9sh::Program &nvs = d9sh::program_for(smk::bytes(w)), &pp = d9sh::program_for(smk::ps()),
                        &pvs = d9sh::program_for(smk::vs());
    CHECK(nvs.ok && pp.ok);
    d9msl::PixelVariant base;
    base.depth_mask = 2;
    std::string plain, src, why;
    CHECK(d9msl::pixel_source(pp, base, &plain, &why));
    d9msl::PixelVariant z = base;
    z.surf = 0;
    z.reactive = 0;
    CHECK(z.key() == base.key() && d9msl::pixel_source(pp, z, &src, &why) && src == plain);   // off: byte-identical
    CHECK(plain.find("d9s_") == std::string::npos && plain.find("[[color(4)]]") == std::string::npos);
    d9msl::VertexVariant vz;
    CHECK(vz.key() == d9msl::VertexVariant{}.key());
    // the slot: 7 (the vertex program writes t0 only, the pixel program reads t0/t1); 6 when smoke holds t7
    CHECK(d9msl::surf_slot(nvs, pp, d9msl::VertexVariant{}, base) == 7);
    d9msl::VertexVariant vsm;
    vsm.smoke = 1;
    CHECK(d9msl::surf_slot(nvs, pp, vsm, base) == 6);
    vsm = d9msl::VertexVariant{};
    vsm.fefloor = 1;
    CHECK(d9msl::surf_slot(nvs, pp, vsm, base) == -1);
    // the vertex half: NORMAL0 x the rows at buffer(20) into t7; refused without NORMAL0
    d9msl::VertexVariant vs7;
    vs7.surf = 7;
    std::string vsrc, vplain;
    CHECK(d9msl::vertex_source(nvs, d9msl::VertexVariant{}, &vplain, &why) && vplain.find("d9surf") == std::string::npos);
    CHECK(d9msl::vertex_source(nvs, vs7, &vsrc, &why));
    CHECK(vsrc.find("constant float4 *d9surf [[buffer(20)]]) {") != std::string::npos &&
          vsrc.find("    ot[7] = float4(vv[1].x * d9surf[0].xyz + vv[1].y * d9surf[1].xyz + vv[1].z * d9surf[2].xyz, 0.0);") <
              vsrc.find("o.t4 = ot[4]; o.t5 = ot[5]; o.t6 = ot[6]; o.t7 = ot[7];"));
    CHECK(vs7.key() != vz.key() && !d9msl::vertex_source(pvs, vs7, &vsrc, &why));
    // the pixel half: outputs 4/5 (and 6), the block at buffer(6), the writes right before the return
    for (uint8_t sv : {(uint8_t)1, (uint8_t)7})
        for (uint8_t rv : {(uint8_t)0, (uint8_t)1, (uint8_t)3}) {
            d9msl::PixelVariant v = base;
            v.surf = sv;
            v.reactive = rv;
            CHECK(v.key() != base.key() && d9msl::pixel_source(pp, v, &src, &why));
            CHECK(src.find("    float4 s0 [[color(4)]];\n    float4 s1 [[color(5)]];\n") != std::string::npos &&
                  src.find("constant D9SurfParams &SFP [[buffer(6)]]") != std::string::npos &&
                  src.find("static float2 d9s_oct(float3 n)") < src.find("fragment D9PSOut ps_main("));
            CHECK((src.find("[[color(6)]]") != std::string::npos) == (rv != 0));
            CHECK((src.find("dfdx(d9s_p)") != std::string::npos) == (sv == 1) &&
                  (src.find("vin.t7.xyz") != std::string::npos) == (sv == 7));
            CHECK(src.find("o.s1 = float4(SFP.tag.xyz, 0.0);") < src.rfind("    return o;\n}\n"));
        }
    d9msl::PixelVariant r2 = base;
    r2.reactive = 2;
    CHECK(d9msl::pixel_source(pp, r2, &src, &why) && src.find("buffer(6)") == std::string::npos &&
          src.find("o.s2 = float4(saturate(max(o.c0.r, max(o.c0.g, o.c0.b))));") != std::string::npos);
    r2.surf = 3;   // not a slot
    CHECK(!d9msl::pixel_source(pp, r2, &src, &why));
    // the normal rows: WVP = W x V x P with a rotated, uniformly scaled, translated W, a y-down camera and a D3D P
    auto rot = [](double ax, double ay, double az, double a, double m[3][3]) {
        const double l = std::sqrt(ax * ax + ay * ay + az * az), x = ax / l, y = ay / l, zz = az / l, c = std::cos(a),
                     s = std::sin(a), t = 1 - c;
        const double r[3][3] = {{t * x * x + c, t * x * y + s * zz, t * x * zz - s * y},
                                {t * x * y - s * zz, t * y * y + c, t * y * zz + s * x},
                                {t * x * zz + s * y, t * y * zz - s * x, t * zz * zz + c}};
        memcpy(m, r, sizeof r);
    };
    double worst_n = 0.0;
    for (int flip = 0; flip < 2; ++flip)
        for (int trial = 0; trial < 6; ++trial) {
            double Wr[3][3], Vr[3][3];
            rot(1 + trial, 2 - trial, 0.5 + trial, 0.3 + 0.7 * trial, Wr);
            rot(0.2, 1, -0.3 * trial, -0.5 + 0.4 * trial, Vr);
            if (flip)   // a y-down view (det -1)
                for (int i = 0; i < 3; ++i)
                    Vr[i][1] = -Vr[i][1];
            M4 Wm{}, Vm{}, Pm{};
            const double sc = 0.5 + trial;
            for (int i = 0; i < 3; ++i)
                for (int j = 0; j < 3; ++j) {
                    Wm.m[i][j] = sc * Wr[i][j];
                    Vm.m[i][j] = Vr[i][j];
                }
            Wm.m[3][0] = 10, Wm.m[3][1] = -3, Wm.m[3][2] = 7, Wm.m[3][3] = 1;
            Vm.m[3][0] = -2, Vm.m[3][1] = 5, Vm.m[3][2] = 1, Vm.m[3][3] = 1;
            Pm.m[0][0] = 1.2, Pm.m[1][1] = flip ? -1.6 : 1.6, Pm.m[2][2] = 1.0001, Pm.m[2][3] = 1, Pm.m[3][2] = -0.5;
            const M4 M = mul(mul(Wm, Vm), Pm);
            float c[16];
            for (int r = 0; r < 4; ++r)
                for (int k = 0; k < 4; ++k)
                    c[4 * r + k] = (float)M.m[k][r];   // c[a + r] = column r of WVP
            float T[12];
            CHECK(normal_rows(c, Vm.m, T));
            for (int k = 0; k < 8; ++k) {
                const double n[3] = {std::cos(k * 0.9), std::sin(k * 1.7), std::cos(k * 2.3 + 1)};
                double e[3], g[3];
                for (int i = 0; i < 3; ++i) {
                    e[i] = n[0] * Wr[0][i] + n[1] * Wr[1][i] + n[2] * Wr[2][i];   // the true world normal (rigid W)
                    g[i] = n[0] * T[i] + n[1] * T[4 + i] + n[2] * T[8 + i];
                }
                const double le = std::sqrt(e[0] * e[0] + e[1] * e[1] + e[2] * e[2]),
                             lg = std::sqrt(g[0] * g[0] + g[1] * g[1] + g[2] * g[2]);
                const double d = std::min(1.0, (e[0] * g[0] + e[1] * g[1] + e[2] * g[2]) / (le * lg));
                worst_n = std::max(worst_n, std::acos(d) * 180.0 / M_PI);
            }
            Wm.m[0][0] *= 1.5;   // non-uniform scale: refused (derived normal)
            const M4 M2 = mul(mul(Wm, Vm), Pm);
            for (int r = 0; r < 4; ++r)
                for (int k = 0; k < 4; ++k)
                    c[4 * r + k] = (float)M2.m[k][r];
            CHECK(!normal_rows(c, Vm.m, T));
        }
    CHECK(worst_n < 0.05);
    D9SurfParams sp{};
    CHECK(sizeof sp == 80);
}
// R3 #1 surface-normal basis (review/r3-design/technical-track.md 3.1, run 20): normal_rows against the game's camera
// convention, a z-up world, the y-down view (det V3 = +1) and both P11 signs. N5 (non-uniform scale refused) is in
// test_surface; N6 (the game's own block) below when the TEST log line has given one.
namespace nbt {
using namespace remaster_fnd;
// world -> view of a level game camera at c with heading h (world z up): view x right, y down, z forward
static M4 game_view(double cx, double cy, double cz, double h, double pitch = 0.0) {
    const double f[3] = {std::cos(h) * std::cos(pitch), std::sin(h) * std::cos(pitch), std::sin(pitch)};
    const double r[3] = {std::sin(h), -std::cos(h), 0.0};
    const double d[3] = {f[1] * r[2] - f[2] * r[1], f[2] * r[0] - f[0] * r[2], f[0] * r[1] - f[1] * r[0]};   // f x r (down)
    M4 v = identity();
    const double *ax[3] = {r, d, f};
    for (int j = 0; j < 3; ++j)
        for (int i = 0; i < 3; ++i)
            v.m[i][j] = ax[j][i];   // column j = view axis j in world (row-vector p_view = p_world x V)
    for (int j = 0; j < 3; ++j)
        v.m[3][j] = -(cx * ax[j][0] + cy * ax[j][1] + cz * ax[j][2]);
    return v;
}
static M4 yawz(double t) {   // about world z (up): x -> (cos t, sin t, 0)
    M4 r = identity();
    r.m[0][0] = std::cos(t), r.m[0][1] = std::sin(t), r.m[1][0] = -std::sin(t), r.m[1][1] = std::cos(t);
    return r;
}
static M4 dproj(double p11) {
    M4 p{};
    p.m[0][0] = 1.2, p.m[1][1] = p11, p.m[2][2] = 1.0001, p.m[2][3] = 1.0, p.m[3][2] = -0.5;
    return p;
}
static void block(const M4 &wvp, float *c) {   // c[a + r] = column r of WVP
    for (int r = 0; r < 4; ++r)
        for (int k = 0; k < 4; ++k)
            c[4 * r + k] = (float)wvp.m[k][r];
}
static void apply(const float *T, const double n[3], double o[3]) {   // n_w = n_o x T (rows of T at 0, 4, 8)
    for (int i = 0; i < 3; ++i)
        o[i] = n[0] * T[i] + n[1] * T[4 + i] + n[2] * T[8 + i];
}
static double det3(const M4 &a) {
    return a.m[0][0] * (a.m[1][1] * a.m[2][2] - a.m[1][2] * a.m[2][1]) -
           a.m[0][1] * (a.m[1][0] * a.m[2][2] - a.m[1][2] * a.m[2][0]) +
           a.m[0][2] * (a.m[1][0] * a.m[2][1] - a.m[1][1] * a.m[2][0]);
}
} // namespace nbt

static void test_surface_basis() {
    using namespace nbt;
    using remaster_surf::normal_rows;
    // the camera helper is a proper game camera: det V3 = +1, world up -> view -y (up on screen)
    {
        const M4 V = game_view(802.2, 2303.8, 158.0, 0.7);
        CHECK(std::fabs(det3(V) - 1.0) < 1e-12);
        const double up[4] = {0, 0, 1, 0};
        double o[4];
        xform(up, V, o);
        CHECK(std::fabs(o[1] + 1.0) < 1e-12 && std::fabs(o[0]) < 1e-12 && std::fabs(o[2]) < 1e-12);
    }
    // N1 identity: W = I at headings 0, 37, 90, 180 deg (and a pitched camera): T = I to 1e-5
    for (double a : {0.0, 37.0, 90.0, 180.0})
        for (double pitch : {0.0, -0.2}) {
            const M4 V = game_view(802.2, 2303.8, 158.0, a * M_PI / 180.0, pitch);
            float c[16], T[12];
            block(mul(V, dproj(-1.6)), c);
            CHECK(normal_rows(c, V.m, T));
            double e = 0.0;
            for (int k = 0; k < 3; ++k)
                for (int i = 0; i < 3; ++i)
                    e = std::max(e, std::fabs(T[4 * k + i] - (k == i ? 1.0 : 0.0)));
            CHECK(e < 1e-5);
        }
    // N2 yawed instance (a placed model, W = yaw about up + translation): T = the 3x3 of W; road stays up exactly
    for (double t : {30.0, 90.0, 180.0}) {
        const double tr = t * M_PI / 180.0;
        M4 W = yawz(tr);
        W.m[3][0] = 812.0, W.m[3][1] = 2290.0, W.m[3][2] = 150.0;
        const M4 V = game_view(802.2, 2303.8, 158.0, 0.4);
        float c[16], T[12];
        block(mul(mul(W, V), dproj(-1.6)), c);
        CHECK(normal_rows(c, V.m, T));
        double e = 0.0;
        for (int k = 0; k < 3; ++k)
            for (int i = 0; i < 3; ++i)
                e = std::max(e, std::fabs(T[4 * k + i] - W.m[k][i]));
        CHECK(e < 1e-5);
        const double road[3] = {0, 0, 1}, wall[3] = {1, 0, 0};
        double nr[3], nw[3];
        apply(T, road, nr);
        apply(T, wall, nw);
        CHECK(std::fabs(nr[2] - 1.0) < 1e-5 && std::fabs(nr[0]) < 1e-5 && std::fabs(nr[1]) < 1e-5);
        CHECK(std::fabs(nw[0] - std::cos(tr)) < 1e-5 && std::fabs(nw[1] - std::sin(tr)) < 1e-5 && std::fabs(nw[2]) < 1e-5);
    }
    // N3 the P11 sign: the same W, V with P11 = -1.6 and +1.6 give the same T
    {
        M4 W = yawz(0.9);
        W.m[3][0] = 5.0;
        const M4 V = game_view(10.0, 20.0, 3.0, 1.3, 0.1);
        float c1[16], c2[16], T1[12], T2[12];
        block(mul(mul(W, V), dproj(-1.6)), c1);
        block(mul(mul(W, V), dproj(1.6)), c2);
        CHECK(normal_rows(c1, V.m, T1) && normal_rows(c2, V.m, T2));
        double e = 0.0;
        for (int i = 0; i < 12; ++i)
            e = std::max(e, (double)std::fabs(T1[i] - T2[i]));
        CHECK(e < 1e-6);
    }
    // N4 mirrored W = diag(-1, 1, 1): accepted (unmirrored is assumed: surface.h normal_rows). The sign rule cannot see
    // the mirror (P11's sign is unknown to it), so it returns T = W3 x (V3 D V3^T), D = diag(1, -1, 1): for a level
    // camera that is the mirror x a flip of world up. The true normals are n x W3 (W3 orthogonal): road (0,0,1),
    // wall (-1,0,0). Recorded as the known limit (the S1a census counts det(W) < 0 draws): road comes out DOWN.
    {
        M4 W = identity();
        W.m[0][0] = -1.0;
        const M4 V = game_view(802.2, 2303.8, 158.0, 0.4);
        float c[16], T[12];
        block(mul(mul(W, V), dproj(-1.6)), c);
        CHECK(normal_rows(c, V.m, T));
        const double road[3] = {0, 0, 1}, wall[3] = {1, 0, 0};
        double nr[3], nw[3];
        apply(T, road, nr);
        apply(T, wall, nw);
        CHECK(std::fabs(nr[2] + 1.0) < 1e-5);                          // the limit: road normal (0,0,-1), not (0,0,1)
        CHECK(std::fabs(nw[0] + 1.0) < 1e-5 && std::fabs(nw[2]) < 1e-5);   // the wall happens to come out right
    }
    // N7 the vertex normal against the depth-derivative facet normal (the independent reference): a tilted plane under
    // a yawed W, rendered by the test's own projection; the facet normal from three projected-and-unprojected points
    // (what dfdx/dfdy of the view position give) vs normal_rows' world normal: < 1 deg
    {
        M4 W = yawz(0.6);
        W.m[3][0] = 805.0, W.m[3][1] = 2310.0, W.m[3][2] = 157.0;
        const M4 V = game_view(802.2, 2303.8, 158.5, 0.5, -0.15), P = dproj(-1.6);
        float c[16], T[12];
        block(mul(mul(W, V), P), c);
        CHECK(normal_rows(c, V.m, T));
        const double no[3] = {0.0, 0.3, 0.9539392};   // a 17.5-degree bank, unit length, in object space
        double nw[3];
        apply(T, no, nw);
        // three object points on the plane through (0,0,0) with that normal -> world -> view; the facet normal in view,
        // back to world through V3^T (the S0 path of a derived normal)
        const double pts[3][4] = {{0, 0, 0, 1}, {1, 0, 0, 1}, {0, 0.9539392, -0.3, 1}};
        double vw[3][4];
        const M4 WV = mul(W, V);
        for (int k = 0; k < 3; ++k)
            xform(pts[k], WV, vw[k]);
        const double a[3] = {vw[1][0] - vw[0][0], vw[1][1] - vw[0][1], vw[1][2] - vw[0][2]};
        const double b[3] = {vw[2][0] - vw[0][0], vw[2][1] - vw[0][1], vw[2][2] - vw[0][2]};
        double fv[3] = {a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]};
        double fw[3];
        for (int i = 0; i < 3; ++i)
            fw[i] = fv[0] * V.m[i][0] + fv[1] * V.m[i][1] + fv[2] * V.m[i][2];   // n_w = n_v x V3^T
        const double lf = std::sqrt(fw[0] * fw[0] + fw[1] * fw[1] + fw[2] * fw[2]),
                     ln = std::sqrt(nw[0] * nw[0] + nw[1] * nw[1] + nw[2] * nw[2]);
        const double d = std::fabs(fw[0] * nw[0] + fw[1] * nw[1] + fw[2] * nw[2]) / (lf * ln);   // facet sign is winding
        CHECK(std::acos(std::min(1.0, d)) * 180.0 / M_PI < 1.0);
        CHECK(nw[2] > 0.9);   // and it still points up
    }
    // N6 the game's own block (run 20 R20N, Rosewood parked, "foundation surface block: frame 300"): the VP_k cluster's
    // clip registers and the camera V the core mod sent, as logged. World geometry has W = I: T must be I to 1e-3. This
    // is the case that tests the game's convention, not the test's own.
    {
        const float c[16] = {-0.770957947f, 0.366289318f, -0.00944759231f, 441.463776f, -0.0437870473f, -0.0549412072f,
                             1.44307673f, -0.307125211f, -0.428203017f, -0.902493834f, -0.0473528877f, 3025.12793f,
                             -0.428188711f, -0.902463734f, -0.0473513082f, 3025.52686f};
        const double V[4][4] = {{-0.903193533, 0.0303072724, -0.428188711, 0},
                                {0.429115683, 0.0380276404, -0.902463734, 0},
                                {-0.0110680545, -0.998827755, -0.0473513082, 0},
                                {517.184143, 0.212577194, 3025.52686, 1}};
        float T[12];
        double ortho = 1.0;
        CHECK(normal_rows(c, V, T, &ortho) && ortho < 2e-3);
        double e = 0.0;
        for (int k = 0; k < 3; ++k)
            for (int i = 0; i < 3; ++i)
                e = std::max(e, std::fabs(T[4 * k + i] - (k == i ? 1.0 : 0.0)));
        CHECK(e < 1e-3);
        // and the game's V is a proper y-down camera: det V3 = +1, world up (z) maps to view -y within 3 deg of pitch
        M4 Vm{};
        memcpy(Vm.m, V, sizeof V);
        CHECK(std::fabs(det3(Vm) - 1.0) < 1e-4 && V[2][1] < -0.99);
    }
}
// WB2 MetalFX temporal: the reset policy and the spatial fallback (remaster_tmp, temporal.h).
#include "../../host/gpu/metal/temporal.h"
static void test_temporal() {
    using namespace remaster_tmp;
    using namespace remaster_fnd;
    // the measured conventions (mfx_bench temporal-sign): +jitter, +1 motion scale
    CHECK(kJitterSignX == 1.0f && kJitterSignY == 1.0f && kMotionScale == 1.0f);
    // reset policy: every foundation reason except a pause; a new scaler; a gap in the temporal frames
    CHECK(reset_reasons(0, false, 41, 42) == 0);
    CHECK(reset_reasons(RESET_PAUSE, false, 41, 42) == 0);
    for (uint32_t b : {RESET_CUT, RESET_JUMP, RESET_FOV, RESET_VP_INVALID, RESET_DISAGREE, RESET_GAP, RESET_RESIZE,
                       RESET_NO_CAMERA})
        CHECK(reset_reasons(b | RESET_PAUSE, false, 41, 42) == TRESET_FOUNDATION);
    CHECK(reset_reasons(0, true, 41, 42) == TRESET_NEW_SCALER);
    CHECK(reset_reasons(0, false, 0, 1) == TRESET_GAP);    // the first temporal frame
    CHECK(reset_reasons(0, false, 40, 42) == TRESET_GAP);  // a spatial / A/B OLD / front-end frame in between
    CHECK(reset_reasons(RESET_CUT, true, 0, 9) == (TRESET_FOUNDATION | TRESET_NEW_SCALER | TRESET_GAP));
    // the spatial fallback, one reason at a time
    Inputs ok;
    ok.supported = true, ok.factor = 1.5, ok.min_scale = 1.0, ok.max_scale = 3.0, ok.frame = true;
    ok.cw = ok.zw = ok.mw = 2560, ok.ch = ok.zh = ok.mh = 1440;
    CHECK(check(ok) == REFUSE_NONE);
    Inputs t = ok;
    t.supported = false;
    CHECK(check(t) == REFUSE_UNSUPPORTED);
    t = ok, t.factor = 3.5;
    CHECK(check(t) == REFUSE_SCALE);
    t = ok, t.max_scale = 1.4;
    CHECK(check(t) == REFUSE_SCALE);
    t = ok, t.ab_old = true;
    CHECK(check(t) == REFUSE_AB_OLD);
    t = ok, t.frame = false;
    CHECK(check(t) == REFUSE_NO_FRAME);
    t = ok, t.mw = t.mh = 0;
    CHECK(check(t) == REFUSE_NO_MOTION);
    t = ok, t.zw = t.zh = 0;
    CHECK(check(t) == REFUSE_NO_DEPTH);
    t = ok, t.cs = 4;
    CHECK(check(t) == REFUSE_MSAA);
    t = ok, t.zs = 4;
    CHECK(check(t) == REFUSE_MSAA);
    t = ok, t.mw = 1280;
    CHECK(check(t) == REFUSE_SIZE);
    t = ok, t.zh = 720;
    CHECK(check(t) == REFUSE_SIZE);
    t = ok, t.mw = 3840, t.mh = 2160;   // R3 #2 J6: vectors at the output size while colour is at 1.5x below it
    CHECK(check(t) == REFUSE_SIZE);
    t = ok, t.usage_ok = false;
    CHECK(check(t) == REFUSE_USAGE);
    for (uint32_t r = 0; r < REFUSE_COUNT; ++r)
        CHECK(std::string(refuse_name(r)) != "?");
    // the scaler key: any field rebuilds
    Key a, b;
    a.iw = b.iw = 2560, a.ih = b.ih = 1440, a.ow = b.ow = 3840, a.oh = b.oh = 2160, a.color = b.color = 80, a.depth = b.depth = 260;
    CHECK(a == b);
    b.reactive = 10;
    CHECK(a != b);
    b = a, b.color = 115;
    CHECK(a != b);
    b = a, b.auto_exposure = true;
    CHECK(a != b);
    // exposure: float colour only, unless the TEST env says otherwise
    CHECK(want_auto_exposure(true, -1) && !want_auto_exposure(false, -1));
    CHECK(!want_auto_exposure(true, 0) && want_auto_exposure(false, 1));
}
// WB3 screen-space reflections: the CPU reference (remaster_ssr, ssr.h) on the synthetic ground + wall scene.
#include "../../host/gpu/metal/ssr.h"
static void test_ssr() {
    using namespace remaster_ssr;
    using namespace remaster_fnd;
    // parameters: a D3D projection only; linear depth and the pixel <-> view round trip
    {
        SsrParams u;
        CHECK(!ssr_params(identity(), identity(), 320, 180, 0, 0, 1.0, 1.0f, 0, &u));
        const M4 P = d3d_projection(1.0, 16.0 / 9.0, 0.5, 1000.0);
        CHECK(!ssr_params(P, M4{}, 320, 180, 0, 0, 1.0, 1.0f, F_SURF, &u));   // no camera: no world normals
        CHECK(ssr_params(P, M4{}, 320, 180, 0, 0, 1.0, 1.0f, F_DEPTHN, &u));
        CHECK(ssr_params(P, identity(), 320, 180, 0.25, -0.125, 1.0, 1.0f, F_SURF, &u));
        CHECK(std::fabs(u.zn - 0.5f) < 1e-4f && u.steps == 40 && u.refine == 4 && u.thick == 0.015f);
        const float d = (float)(P.m[2][2] + P.m[3][2] / 37.0);
        CHECK(std::fabs(lin(d, u) - 37.0f) < 0.01f);
        const V3 v = view_pos(100.5f, 40.5f, d, u), p = project(v, u);
        CHECK(std::fabs(p.x - 100.5f) < 0.01f && std::fabs(p.y - 40.5f) < 0.01f && std::fabs(1.0f / p.z - 37.0f) < 0.01f);
        CHECK(std::fabs(v.z - 37.0f) < 0.01f && v.y < 0.0f);   // the upper half of the screen is view y < 0 (y down)
    }
    // plane + wall, water ground (roughness 0.1): hit pixel within 1 px of the analytic mirror point
    Synth sy = make_synth(MAT_WATER, 0.1f);
    Images im = sy.images();
    SsrParams u;
    CHECK(ssr_params(sy.P, sy.V, sy.w, sy.h, 0, 0, 1.0, 1.0f, F_SURF | F_BACKFACE, &u));
    int tested = 0, within = 0, sky = 0, sky_zero = 0, border = 0, border_ok = 0, full = 0;
    float worst = 0.0f;
    for (int y = sy.h / 2 + 1; y < sy.h; y += 3)
        for (int x = 2; x < sy.w - 2; x += 5) {
            Surf s;
            const Hit r = ssr_trace_ref(im, u, x, y, &s);
            if (im.depth[y * sy.w + x] >= 1.0f || sy.s1[4 * ((size_t)y * sy.w + x)] != MAT_WATER) {
                CHECK(!s.ok && r.conf == 0.0f);   // the sky beyond the far plane, the matte wall
                continue;
            }
            CHECK(s.ok);
            float ax, ay;
            if (synth_analytic(sy, u, x, y, &ax, &ay)) {
                if (r.conf <= 0.0f)
                    continue;   // faded to nothing (border / distance): no position to check
                ++tested;
                const float e = std::hypot(r.px - ax, r.py - ay);
                worst = std::max(worst, e);
                within += e <= 1.0f;
                const float ux = r.px / sy.w, uy = r.py / sy.h;
                const float edge = std::min(std::min(ux, 1 - ux), std::min(uy, 1 - uy));
                if (edge < 0.05f) {
                    ++border;
                    border_ok += r.conf <= 0.5f + 1e-4f;
                }
                full += edge > 0.1f && r.conf > 0.99f;
            } else {
                // the reflected ray leaves past the wall's side (sky) with a margin: no hit
                const V3 dir = normalize(view_pos(x + 0.5f, y + 0.5f, 0.5f, u));
                const V3 g = dir * (2.0f / dir.y);
                const V3 q = g + v3(dir.x, -dir.y, dir.z) * ((30.0f - g.z) / dir.z);
                if (g.z < 30.0f && std::fabs(q.x) > 23.0f) {
                    ++sky;
                    sky_zero += r.conf == 0.0f;
                }
            }
        }
    fprintf(stderr, "ssr ref: %d hits tested, %d within 1 px (worst %.3f px), sky %d/%d zero, %d full\n", tested, within,
            worst, sky_zero, sky, full);
    CHECK(tested >= 100 && within == tested);
    CHECK(sky >= 20 && sky_zero == sky);
    CHECK(full >= 20);
    // border fade: a wide wall puts hits near the screen sides (confidence <= the 10 % ramp)
    {
        Synth wd = make_synth(MAT_WATER, 0.1f, 0.0, 400.0f);
        for (int y = sy.h / 2 + 1; y < sy.h; y += 2)
            for (int x = 0; x < sy.w; x += 2) {
                const Hit r = ssr_trace_ref(wd.images(), u, x, y);
                if (r.conf <= 0.0f)
                    continue;
                const float ux = r.px / sy.w, uy = r.py / sy.h;
                const float edge = std::min(std::min(ux, 1 - ux), std::min(uy, 1 - uy));
                if (edge < 0.05f) {
                    ++border;
                    border_ok += r.conf <= edge / 0.1f + 1e-4f;
                }
            }
        fprintf(stderr, "ssr ref: border %d/%d faded\n", border_ok, border);
        CHECK(border >= 10 && border_ok == border);
    }
    // the wall (WORLD, matte) and the sky never receive
    {
        Surf s;
        CHECK(ssr_trace_ref(im, u, sy.w / 2, sy.h / 2 - 10, &s).conf == 0.0f && !s.ok);
        CHECK(ssr_trace_ref(im, u, 1, 1, &s).conf == 0.0f && !s.ok);
    }
    // the probe pixel: a ground pixel with a centred, confident hit
    int px = sy.w / 2, py = -1;
    for (int y = sy.h / 2 + 1; y < sy.h && py < 0; ++y)
        if (ssr_trace_ref(im, u, px, y).conf > 0.99f)
            py = y;
    CHECK(py > 0);
    if (py < 0)
        return;
    const Hit base = ssr_trace_ref(im, u, px, py);
    // roughness: 0.3 fades, >= 0.4 is not a receiver
    {
        Synth r3 = make_synth(MAT_WATER, 0.3f), r4 = make_synth(MAT_WATER, 0.45f);
        const Hit h3 = ssr_trace_ref(r3.images(), u, px, py);
        Surf s4;
        const Hit h4 = ssr_trace_ref(r4.images(), u, px, py, &s4);
        CHECK(h3.conf > 0.0f && h3.conf < base.conf);
        CHECK(!s4.ok && h4.conf == 0.0f);
    }
    // stale S0 (the last surface write is 2 % off the visible depth), emissive, a matte tag: no receiver
    {
        Synth st = sy;
        st.s0[4 * ((size_t)py * st.w + px) + 3] *= 1.02f;
        CHECK(ssr_trace_ref(st.images(), u, px, py).conf == 0.0f);
        Synth em = sy;
        em.s1[4 * ((size_t)py * em.w + px) + 2] = SURF_EMISSIVE;
        CHECK(ssr_trace_ref(em.images(), u, px, py).conf == 0.0f);
        Synth wo = make_synth(MAT_WORLD, 0.1f);
        CHECK(ssr_trace_ref(wo.images(), u, px, py).conf == 0.0f);
    }
    // ROAD only with the TEST flag; car paint at half weight; Fresnel f0 at normal incidence, ~1 grazing
    {
        Synth rd = make_synth(MAT_ROAD, 0.1f);
        CHECK(ssr_trace_ref(rd.images(), u, px, py).conf == 0.0f);
        SsrParams ur = u;
        ur.flags |= F_ROAD;
        CHECK(std::fabs(ssr_trace_ref(rd.images(), ur, px, py).conf - base.conf) < 1e-6f);
        Synth cp = make_synth(MAT_CAR_PAINT, 0.1f), gl = make_synth(MAT_GLASS, 0.1f);
        Surf sc, sg;
        const Hit hc = ssr_trace_ref(cp.images(), u, px, py, &sc), hg = ssr_trace_ref(gl.images(), u, px, py, &sg);
        CHECK(sc.ok && sg.ok && std::fabs(sc.cw - 0.5f) < 1e-6f && sg.cw == 1.0f && sc.f0 == 0.05f && sg.f0 == 0.04f);
        CHECK(std::fabs(hc.conf - hg.conf) < 1e-6f);
        Surf n;
        n.ok = true;
        n.cw = 1.0f;
        n.f0 = 0.04f;
        n.pos = v3(0, 0, 10);
        n.n = v3(0, 0, -1);
        CHECK(std::fabs(fresnel(n) - 0.04f) < 1e-5f);
        n.n = normalize(v3(0, -1, -0.02f));
        CHECK(fresnel(n) > 0.85f);
        CHECK(std::fabs(weight(sc, 1.0f, u) - 0.5f * fresnel(sc)) < 1e-6f && weight(sg, 0.0f, u) == 0.0f);
        SsrParams u2 = u;
        u2.strength = 2.0f;
        CHECK(std::fabs(weight(sg, 0.5f, u2) - std::min(1.0f, fresnel(sg))) < 1e-6f);
    }
    // the world normal goes through V: a camera turned about its y axis traces the same picture
    {
        Synth yw = make_synth(MAT_WATER, 0.1f, 0.7);
        SsrParams uy;
        CHECK(ssr_params(yw.P, yw.V, yw.w, yw.h, 0, 0, 1.0, 1.0f, F_SURF | F_BACKFACE, &uy));
        float dmax = 0.0f;
        int same = 0, n = 0;
        for (int y = sy.h / 2 + 1; y < sy.h; y += 7)
            for (int x = 3; x < sy.w; x += 11) {
                const Hit a = ssr_trace_ref(im, u, x, y), b = ssr_trace_ref(yw.images(), uy, x, y);
                ++n;
                same += (a.conf > 0.0f) == (b.conf > 0.0f);
                if (a.conf > 0.0f && b.conf > 0.0f)
                    dmax = std::max(dmax, std::hypot(a.px - b.px, a.py - b.py));
            }
        CHECK(same == n && dmax < 0.05f);
    }
    // TEST fallback: normals from the depth (no S0/S1) find the same mirror point on the flat ground
    {
        SsrParams ud = u;
        ud.flags = F_DEPTHN;
        Surf s;
        const Hit h = ssr_trace_ref(im, ud, px, py, &s);
        fprintf(stderr, "ssr ref: depth normals n %.4f %.4f %.4f hit %.2f %.2f (S0 %.2f %.2f) conf %.3f\n", s.n.x, s.n.y, s.n.z,
                h.px, h.py, base.px, base.py, h.conf);
        CHECK(s.ok && s.n.y < -0.99f && h.conf > 0.0f && std::hypot(h.px - base.px, h.py - base.py) < 1.0f);
    }
    // jitter: one render pixel is 2 / w in NDC (foundation.h jitter_halfpix), added to the halfpix
    {
        SsrParams uj;
        CHECK(ssr_params(sy.P, sy.V, sy.w, sy.h, 0.3, -0.2, 1.0, 1.0f, F_SURF, &uj));
        CHECK(std::fabs(uj.h[0] - u.h[0] - 0.6f / sy.w) < 1e-6f && std::fabs(uj.h[1] - u.h[1] - 0.4f / sy.h) < 1e-6f);
    }
}

int main() {
    test_surface();
    test_surface_basis();
    test_temporal();
    test_ssr();
    test_msaa_count();
    test_volume_dds();
    test_lit_smoke();
    test_ug1_looks();
    test_water_shapes();
    test_raindrop_patch();
    test_mipbias_patch();
    test_foundation();
    test_atmosphere();
    mem_init();
    imports_init();
    dx_register_shims();
    g_stack_top = STACK_TOP - 0x1000;
    g_scratch = heap_alloc(0x4000, true, 16);
    if (!g_scratch)
        return 1;
    cpu_reset();
    const uint32_t dev = make_device();
    if (!dev)
        return 1;
    test_device_booleans(dev);
    test_fvf(dev);
    test_draw_limits(dev);
    test_depth_stencil_get(dev);
    test_surface_container_refs(dev);
    test_effect_booleans(dev);
    test_volume(dev);
    test_state_getters_and_readback(dev);
    const uint32_t bound = make_volume(dev);
    const uint32_t bound_id = com_this(bound)->id;
    const uint32_t child_id = com_this(bound)->surfaces[0];
    CHECK(call_method(dev, 65, {0, bound}) == 0);
    CHECK(call_method(bound, 2) == 1);
    call_method(dev, 2);
    CHECK(!com_get(bound_id) && !com_get(child_id));
    if (const uint32_t refs_dev = make_device()) // Test157c, on a device of its own (it Resets)
        test_depth_stencil_refs(refs_dev);
    printf("d3d9 guest/effect: %d checks, %d failures\n", g_checks, g_failures);
    return g_failures ? 1 : 0;
}
