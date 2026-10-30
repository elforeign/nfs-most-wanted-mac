// dstest.cpp - headless tests for the host DirectSound (dsound_host.h). Nothing is listened to.
//   build/dstest            manual-clock and null-sink (real-time, no device) tests
//   build/dstest device     + the same streaming test on the CoreAudio device (muted), and the stereo / DD modes
//   build/dstest fake       only the output-device logic on media's fake HDMI TV (NFS_AUDIO_FAKE_DEVICE): the game
//                           re-creating its device keeps the output; a device that goes away is followed. No audio.
#ifdef MEDIA_AUDIO_CALLBACK_TEST
// Standalone build: compile only this source, with MEDIA_AUDIO_CALLBACK_TEST and the usual FFmpeg/framework
// flags. Including the implementations lets this fixture exercise the actual IOProc and parking decision with
// synthetic buffers, without adding callback/clock mutation controls to the production API.
#include <CoreAudio/CoreAudio.h>
#include <vector>
#include <algorithm>
#include <cstring>
// Fake HAL timing notifications exercise the real listener registration and removal paths.
namespace fake_hal {
UInt32 period = 4096, safety = 128;
Float64 rate = 48000;
struct Listener { AudioObjectID device; AudioObjectPropertyAddress address; AudioObjectPropertyListenerProc proc; void *context; };
std::vector<Listener> listeners;
OSStatus get(AudioObjectID device, const AudioObjectPropertyAddress *a, UInt32, const void *, UInt32 *size, void *dst) {
    if (device != 0x7FA0 && device != 0x7FA1) return kAudioHardwareBadDeviceError;
    if (a->mSelector == kAudioDevicePropertyBufferFrameSize) { *size = sizeof period; memcpy(dst, &period, *size); }
    else if (a->mSelector == kAudioDevicePropertySafetyOffset) { *size = sizeof safety; memcpy(dst, &safety, *size); }
    else if (a->mSelector == kAudioDevicePropertyNominalSampleRate) { *size = sizeof rate; memcpy(dst, &rate, *size); }
    else return kAudioHardwareUnknownPropertyError;
    return noErr;
}
OSStatus add(AudioObjectID device, const AudioObjectPropertyAddress *a, AudioObjectPropertyListenerProc proc, void *ctx) {
    listeners.push_back({device, *a, proc, ctx}); return noErr;
}
OSStatus remove(AudioObjectID device, const AudioObjectPropertyAddress *a, AudioObjectPropertyListenerProc proc, void *ctx) {
    listeners.erase(std::remove_if(listeners.begin(), listeners.end(), [&](const Listener &l) {
        return l.device == device && l.address.mSelector == a->mSelector && l.proc == proc && l.context == ctx;
    }), listeners.end()); return noErr;
}
void change(AudioObjectID device, AudioObjectPropertySelector selector) {
    for (auto l : listeners) if (l.device == device && l.address.mSelector == selector)
        l.proc(device, 1, &l.address, l.context);
}
}
#define AudioObjectGetPropertyData fake_hal::get
#define AudioObjectAddPropertyListener fake_hal::add
#define AudioObjectRemovePropertyListener fake_hal::remove
#include "../../media/media.cpp"
#undef AudioObjectGetPropertyData
#undef AudioObjectAddPropertyListener
#undef AudioObjectRemovePropertyListener
#include "dsound_host.cpp"

namespace media {
struct AudioOutputTest {
    using Impl = AudioOutput::Impl;
    static Impl &impl(AudioOutput &o) { return *o.d; }
};
}

int main() {
    setenv("NFS_AUDIO_FAKE_DEVICE", "1", 1);  // before every media call: never open CoreAudio
    int checks = 0, failed = 0;
    auto check = [&](bool ok, const char *what) {
        ++checks;
        if (!ok) { ++failed; printf("FAIL: %s\n", what); }
    };
    DsDevice owner;
    owner.id = 123;
    owner.cfg.sink = DS_SINK_DEVICE;
    owner.cfg.mode = DS_OUT_TRUEHD;
    owner.cfg.mix_rate = 48000;
    owner.out = std::make_unique<media::AudioOutput>();
    if (!owner.out->open(media::OutputMode::TrueHd)) return 2;
    owner.out->stop_for_test();  // stop fake drain; only synthetic callbacks below touch the queues
    auto *original = owner.out.get();
    auto &p = media::AudioOutputTest::impl(*original);
    const bool diag = p.diag;
    p.stats = {};  // discard any fake-drain callback that completed while stop_for_test joined it
    using Impl = media::AudioOutputTest::Impl;
    uint8_t a[64], b[64];
    struct { UInt32 mNumberBuffers; AudioBuffer mBuffers[2]; } buffers = {2, {{2, sizeof a, a}, {2, sizeof b, b}}};
    auto io = [&] {
        memset(a, 0xCD, sizeof a);
        memset(b, 0xCD, sizeof b);
        Impl::io(0, nullptr, nullptr, nullptr, reinterpret_cast<AudioBufferList *>(&buffers), nullptr, &p);
    };
    auto silent = [](const uint8_t *v) { return std::all_of(v, v + 64, [](uint8_t x) { return x == 0; }); };
    auto age = [&] { p.last_callback = media::now_ns() - 800000000; };
    float one[6] = {};
    original->write(one, 1);
    check(original->pending_frames() == 1 && p.bq.empty(), "TrueHD priming has one pending frame, no queued bytes");
    age();
    io();
    check(!original->stalled(0.5) && silent(a) && silent(b), "valid priming zeros refresh health and silence both buffers");
    auto stats = original->take_callback_stats();
    check(stats.zero_callbacks == 1 && (!p.diag || (stats.valid == 1 && stats.zero_bytes == 64)),
          "priming shortage accounting remains intact");
    { std::vector<uint8_t> bytes(16, 0x42); p.bq.put(bytes.data(), bytes.size()); }
    age();
    io();
    check(!original->stalled(0.5) && p.bq.empty() && a[0] == 0x42 && a[15] == 0x42 && a[16] == 0 && silent(b),
          "partial consumption refreshes health and zero fills the tail and unused stream");
    { std::vector<uint8_t> bytes(128, 0x42); p.bq.put(bytes.data(), bytes.size()); }
    age();
    check(original->stalled(0.5), "queued audio without consumption becomes unhealthy");
    io();
    check(!original->stalled(0.5) && p.bq.size() == 64 && a[0] == 0x42 && a[63] == 0x42,
          "consumption refreshes health while more queued data awaits consumption");
    io();  // drain the remaining bytes before parking
    // Actual parking code, no mixer running. Simulate valid callbacks over the requested 0.8 s release gap.
    park_output(owner);
    auto until = Clock::now() + std::chrono::milliseconds(800);
    do { io(); std::this_thread::sleep_for(std::chrono::milliseconds(20)); } while (Clock::now() < until);
    check(silent(a) && silent(b) && !original->stalled(0.5), "parked valid zero callbacks remain healthy at 0.8 s");
    DsOutputMode mode;
    int rate = 0;
    unsigned from = 0;
    uint32_t closed_dev = 0;
    owner.out = take_parked(owner.cfg, 0, &mode, &rate, &from, &closed_dev);
    check(owner.out.get() == original && mode == DS_OUT_TRUEHD && rate == 48000 && from == 123 && !closed_dev,
          "take_parked reuses the same output within the 2 s window");
    if (owner.out) {
        { std::vector<uint8_t> bytes(16, 0x42); p.bq.put(bytes.data(), bytes.size()); }
        p.stream_index = 2;
        age();
        io();
        check(original->stalled(0.5) && p.bq.size() == 16 && silent(a) && silent(b),
              "invalid stream with queued data stays unhealthy and silences all buffers");
        { std::vector<uint8_t> bytes(p.bq.size()); p.bq.take(bytes.data(), bytes.size()); }
        io();
        check(original->stalled(0.5), "invalid stream with empty queue also stays unhealthy");
        p.stream_index = 0;
        buffers.mBuffers[0].mData = nullptr;
        io();
        check(original->stalled(0.5), "null selected buffer cannot refresh idle health");
        buffers.mBuffers[0].mData = a;
        buffers.mBuffers[0].mDataByteSize = 0;
        io();
        check(original->stalled(0.5), "empty selected buffer cannot refresh idle health");
        buffers.mBuffers[0].mDataByteSize = sizeof a;
        io();  // healthy again before parking; then invalidate it during the parking window
        park_output(owner);
        p.stream_index = 2;
        { std::vector<uint8_t> bytes(16, 0x42); p.bq.put(bytes.data(), bytes.size()); }
        age();
        io();
        owner.out = take_parked(owner.cfg, 0, &mode, &rate, &from, &closed_dev);
        check(!owner.out && closed_dev != 0, "take_parked rejects an invalid-index output despite callback arrivals");
    }
    media::AudioOutput pcm;
    check(pcm.open_offline(media::OutputMode::Stereo), "PCM offline fixture opens");
    auto &q = media::AudioOutputTest::impl(pcm);
    float samples[8];
    q.last_callback = media::now_ns() - 800000000;
    q.take_floats(samples, 8);
    check(media::now_ns() - q.last_callback.load() < 500000000 && samples[0] == 0 && samples[7] == 0,
          "valid empty PCM callback refreshes health and supplies zeros");
    // Large-period negative control: remove the minimum_queue_frames floor from output_latency.
    // Fixed: 141 checks, 0 failed (diag on/off); floor removed: 12 failed, 48 underruns/mode.
    // Stereo: 0/1445376 zero/total bytes fixed; 1052160/1445376 broken (72.8%).
    check(media::pcm_queue_frames(4096, 128, 48000, 44100) == 7644,
          "4096-frame period and safety offset scale upward to 44100 Hz");
    check(media::pcm_queue_frames(0, 128, 48000, 44100) == 0 &&
          media::pcm_queue_frames(4096, 0, 0, 44100) == 0,
          "missing hardware timing retains the existing fallback");
    for (auto mode : {DS_OUT_STEREO, DS_OUT_PCM51}) {
        DsConfig cfg{};
        cfg.sink = DS_SINK_DEVICE; cfg.mode = mode; cfg.mix_rate = 44100; cfg.block_frames = 256;
        DsDevice *device = nullptr;
        check(ds_device_create(&cfg, &device) == DS_OK, "large-period fake PCM opens");
        if (!device) continue;
        stop_thread(*device);
        device->out->stop_for_test();
        auto &pcm_impl = media::AudioOutputTest::impl(*device->out);
        { std::vector<float> old(pcm_impl.fq.size()); pcm_impl.fq.take(old.data(), old.size()); }
        pcm_impl.stats = {};
        check(device->cfg.latency_frames == 7644, "initial PCM open uses scaled hardware latency");
        start_thread(*device);
        std::vector<float> pull(3764 * pcm_impl.channels);
        uint64_t zero = 0, taken = 0;
        for (int callback = 0; callback < 48; ++callback) {
            // Synthetic device clock: permit a full refill between 85 ms device periods, then pull one
            // whole period. No wall-clock scheduling claim: the real mixer and real queue are exercised.
            auto deadline = Clock::now() + std::chrono::seconds(1);
            while (device->out->queued_frames() < (size_t)device->cfg.latency_frames && Clock::now() < deadline)
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
            check(device->out->queued_frames() >= (size_t)device->cfg.latency_frames, "mixer refills to target");
            pcm_impl.callback_begin();
            pcm_impl.take_floats(pull.data(), pull.size());
        }
        // Stop joins the mixer's final stats read; callback byte counters are cumulative.
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
        stop_thread(*device);
        auto callback_stats = device->out->take_callback_stats();
        zero = callback_stats.zero_bytes; taken = callback_stats.taken_bytes;
        printf("large-period %s: 48 callbacks, target %d, taken %llu, zero %llu, underruns %llu\n",
               mode_label(mode), device->cfg.latency_frames, (unsigned long long)taken,
               (unsigned long long)zero, (unsigned long long)device->underruns);
        check((!diag || (taken > 0 && zero == 0)) && callback_stats.zero_callbacks == 0 && device->underruns == 0,
              "large-period PCM has no zero-fill or underruns after priming");
        // Exercise the primary-format rate reopen, explicit low/high targets and asynchronous output adoption.
        set_mix_rate(*device, 96000);
        stop_thread(*device); device->out->stop_for_test();
        check(device->cfg.latency_frames == 16640, "rate reopen rescales hardware latency");
        device->fixed_latency = true; device->requested_latency = 512;
        output_latency(*device);
        check(device->cfg.latency_frames == 16640, "explicit short PCM latency cannot underrun hardware");
        device->requested_latency = 32768; output_latency(*device);
        check(device->cfg.latency_frames == 32768, "larger explicit PCM latency is preserved");
        device->fixed_latency = false;
        device->ready.reset(new media::AudioOutput);
        check(device->ready->open(media::OutputMode::Stereo, 48000), "fake replacement opens");
        device->ready->stop_for_test();
        device->ready_mode = DS_OUT_STEREO; device->ready_rate = 48000;
        follow_output(*device);
        check(device->cfg.latency_frames == 8320, "output adoption recomputes period at the new rate");
        ds_device_release(device);
    }
    // Same-device period growth: cached-at-open code fails (1024 target, 3072/4096 frames zero filled).
    // The fake HAL invokes the registered production callback; the actual mixer must observe its atomic floor.
    auto close_parked = [] {
        std::unique_ptr<media::AudioOutput> old;
        { auto &sh = shared(); std::lock_guard<std::mutex> g(sh.m); old = std::move(sh.parked.out); }
        if (old) old->close();
    };
    close_parked();
    fake_hal::period = 256; fake_hal::safety = 128;
    DsConfig changing_cfg{};
    changing_cfg.sink = DS_SINK_DEVICE; changing_cfg.mode = DS_OUT_STEREO;
    changing_cfg.mix_rate = 48000; changing_cfg.block_frames = 256;
    DsDevice *changing = nullptr;
    check(ds_device_create(&changing_cfg, &changing) == DS_OK, "changing-period fake PCM opens");
    if (changing) {
        stop_thread(*changing); changing->out->stop_for_test();
        auto *same_output = changing->out.get();
        auto same_device = same_output->device();
        check(same_output->minimum_queue_frames() == 640, "small initial period floor");
        start_thread(*changing);
        auto wait_queue = [&](size_t target) {
            auto deadline = Clock::now() + std::chrono::seconds(1);
            while (same_output->queued_frames() < target && Clock::now() < deadline) {
                // Synthetic callbacks below own the queue; keep their paused fake clock healthy while refilling.
                media::AudioOutputTest::impl(*same_output).last_callback.store(media::now_ns());
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
            }
        };
        wait_queue(1024);
        fake_hal::period = 4096;
        fake_hal::change(same_device, kAudioDevicePropertyBufferFrameSize);
        check(same_output->minimum_queue_frames() == 8320, "period notification recomputes floor");
        wait_queue(8320);
        stop_thread(*changing);
        check(changing->cfg.latency_frames == 8320 && same_output->queued_frames() >= 8320,
              "running mixer follows same-device period growth");
        auto &impl = media::AudioOutputTest::impl(*same_output);
        same_output->take_callback_stats();
        std::vector<float> pull(4096 * 2);
        impl.take_floats(pull.data(), pull.size());
        auto stats = same_output->take_callback_stats();
        printf("period-change: target %d, floor %d, shortage callbacks %llu\n",
               changing->cfg.latency_frames, same_output->minimum_queue_frames(), (unsigned long long)stats.zero_callbacks);
        check(stats.zero_callbacks == 0, "grown period callback has no zero fill");
        fake_hal::safety = 512;
        fake_hal::change(same_device, kAudioDevicePropertySafetyOffset);
        check(same_output->minimum_queue_frames() == 8704, "safety notification recomputes floor");
        fake_hal::rate = 96000;
        fake_hal::change(same_device, kAudioDevicePropertyNominalSampleRate);
        check(same_output->minimum_queue_frames() == 4352, "nominal-rate notification rescales floor");
        check(changing->out.get() == same_output && same_output->device() == same_device,
              "timing updates preserve output and DeviceID");
        ds_device_release(changing);
        close_parked();
        check(fake_hal::listeners.empty(), "close unregisters timing listeners");
    }
    DsDevice encoded;
    encoded.cfg.mode = DS_OUT_TRUEHD; output_latency(encoded);
    check(encoded.cfg.latency_frames == 4096, "TrueHD default unchanged");
    encoded.cfg.mode = DS_OUT_DOLBY_DIGITAL; output_latency(encoded);
    check(encoded.cfg.latency_frames == 3072, "Dolby Digital default unchanged");
    encoded.cfg.mode = DS_OUT_DOLBY_DIGITAL_PLUS; output_latency(encoded);
    check(encoded.cfg.latency_frames == 3072, "Dolby Digital Plus default unchanged");
    printf("callback/parking fixture: %d checks, %d failed (BOOT_DIAG=%d)\n", checks, failed, diag);
    return failed ? 1 : 0;
}
#else
#include "dsound_host.h"

#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <random>
#include <string>
#include <thread>
#include <vector>

static int g_fail = 0, g_checks = 0;
#define CHECK(cond, ...)                                                                          \
    do {                                                                                          \
        ++g_checks;                                                                               \
        if (!(cond)) {                                                                            \
            ++g_fail;                                                                             \
            printf("  FAIL %s:%d: %s  ", __FILE__, __LINE__, #cond);                              \
            printf(__VA_ARGS__);                                                                  \
            printf("\n");                                                                         \
        }                                                                                         \
    } while (0)
#define OK(expr) CHECK((expr) == DS_OK, "%s -> %08x", #expr, (unsigned)(expr))

using Clock = std::chrono::steady_clock;
static double now_s() { return std::chrono::duration<double>(Clock::now().time_since_epoch()).count(); }

static std::vector<uint8_t> wfx(int ch, int rate, int bits, uint32_t ext_mask = 0, bool flt = false) {
    std::vector<uint8_t> w(ext_mask ? 40 : 18, 0);
    auto p16 = [&](int o, uint16_t v) { w[o] = (uint8_t)v; w[o + 1] = (uint8_t)(v >> 8); };
    auto p32 = [&](int o, uint32_t v) { for (int i = 0; i < 4; ++i) w[o + i] = (uint8_t)(v >> (8 * i)); };
    p16(0, ext_mask ? 0xFFFE : flt ? 3 : 1);
    p16(2, (uint16_t)ch);
    p32(4, (uint32_t)rate);
    p32(8, (uint32_t)(rate * ch * bits / 8));
    p16(12, (uint16_t)(ch * bits / 8));
    p16(14, (uint16_t)bits);
    if (ext_mask) {
        p16(16, 22);
        p16(18, (uint16_t)bits);
        p32(20, ext_mask);
        static const uint8_t g[16] = {1, 0, 0, 0, 0, 0, 0x10, 0, 0x80, 0, 0, 0xAA, 0, 0x38, 0x9B, 0x71};
        memcpy(&w[24], g, 16);
        if (flt) w[24] = 3;
    }
    return w;
}

static DsDevice *manual_device(int block = 256) {
    DsConfig c;
    ds_config_default(&c);
    c.sink = DS_SINK_MANUAL;
    c.block_frames = block;
    DsDevice *d = nullptr;
    OK(ds_device_create(&c, &d));
    return d;
}

// The known test signal: a distinct 16-bit value per channel and frame.
static int16_t sig(int c, uint64_t n) { return (int16_t)(uint16_t)((n * 31 + (uint64_t)c * 5003 + (n >> 11) * 7) & 0xFFFF); }

static void put6(uint8_t *mem, uint32_t frame, uint64_t n) {
    int16_t v[6];
    for (int c = 0; c < 6; ++c) v[c] = sig(c, n);
    memcpy(mem + (size_t)frame * 12, v, 12);
}

// A game-style streamer: refills [last write position, play cursor) of a looping 6-ch 16-bit buffer with the
// next frames of sig(), through Lock/Unlock.
struct Streamer {
    DsBuffer *b;
    uint8_t *mem;
    uint32_t bytes;
    uint32_t wpos = 0;   // byte offset the next frame goes to
    uint64_t next = 0;   // next signal frame index
    uint64_t locks = 0, wraps = 0;
    bool step() {
        uint32_t play = 0, write = 0;
        if (ds_buffer_get_current_position(b, &play, &write) != DS_OK) return false;
        uint32_t todo = (play + bytes - wpos) % bytes;
        if (todo < 12 * 64) return true;
        uint32_t o1, l1, o2, l2;
        if (ds_buffer_lock(b, wpos, todo, 0, &o1, &l1, &o2, &l2) != DS_OK) return false;
        for (uint32_t i = 0; i < l1; i += 12) put6(mem, (o1 + i) / 12, next++);
        for (uint32_t i = 0; i < l2; i += 12) put6(mem, (o2 + i) / 12, next++);
        if (l2) ++wraps;
        ++locks;
        wpos = (wpos + todo) % bytes;
        return ds_buffer_unlock(b, o1, l1, o2, l2) == DS_OK;
    }
};

// Checks out[k] == sig(c, k - k0) for every frame from k0 on; returns mismatching frames.
static size_t verify_stream(const std::vector<float> &out, size_t k0, size_t *checked) {
    size_t bad = 0, frames = out.size() / 6;
    for (size_t k = k0; k < frames; ++k)
        for (int c = 0; c < 6; ++c)
            if (out[k * 6 + c] != sig(c, k - k0) / 32768.0f) {
                if (bad < 3) printf("    mismatch at frame %zu ch %d: %f vs %f\n", k, c, out[k * 6 + c], sig(c, k - k0) / 32768.0f);
                ++bad;
                break;
            }
    *checked = frames > k0 ? frames - k0 : 0;
    return bad;
}

// ---------------------------------------------------------------------------------------------- tests
static void test_formats() {
    printf("formats, caps, primary buffer\n");
    DsDevice *d = manual_device();
    std::vector<uint8_t> mem(48000);
    DsBuffer *b = nullptr;
    struct { std::vector<uint8_t> w; DsResult want; const char *what; } cases[] = {
        {wfx(6, 48000, 16), DS_OK, "6ch 16-bit PCM"},
        {wfx(6, 48000, 16, 0x3F), DS_OK, "6ch EXTENSIBLE 0x3F"},
        {wfx(6, 48000, 32, 0x3F, true), DS_OK, "6ch EXTENSIBLE float"},
        {wfx(1, 22050, 8), DS_OK, "mono 8-bit"},
        {wfx(2, 44100, 16), DS_OK, "stereo 16-bit"},
        {wfx(4, 44100, 16), DS_OK, "quad"},
        {wfx(2, 44100, 24), DS_OK, "stereo 24-bit"},
        {wfx(2, 44100, 12), DSERR_BADFORMAT, "12-bit"},
        {wfx(9, 44100, 16), DSERR_BADFORMAT, "9 channels"},
        {wfx(2, 50, 16), DSERR_BADFORMAT, "50 Hz"},
    };
    for (auto &t : cases) {
        DsResult r = ds_device_create_buffer(d, DSBCAPS_CTRLVOLUME, (uint32_t)mem.size(), t.w.data(), mem.data(), &b);
        CHECK(r == t.want, "%s: %08x", t.what, (unsigned)r);
        if (r == DS_OK) ds_buffer_release(b);
    }
    auto bad = wfx(2, 44100, 16);
    bad[12] = 3;  // block align mismatch
    CHECK(ds_device_create_buffer(d, 0, 4000, bad.data(), mem.data(), &b) == DSERR_BADFORMAT, "block align");
    bad = wfx(2, 44100, 16);
    bad[0] = 2;   // ADPCM
    CHECK(ds_device_create_buffer(d, 0, 4000, bad.data(), mem.data(), &b) == DSERR_BADFORMAT, "ADPCM");
    auto w6 = wfx(6, 48000, 16);
    CHECK(ds_device_create_buffer(d, DSBCAPS_CTRLPAN, 4800, w6.data(), mem.data(), &b) == DSERR_INVALIDPARAM,
          "pan on 6ch");
    CHECK(ds_device_create_buffer(d, 0, 4800, w6.data(), nullptr, &b) == DSERR_INVALIDPARAM, "no memory");

    // device caps
    uint32_t caps[24] = {96};
    OK(ds_device_get_caps(d, caps));
    CHECK(caps[2] == 100 && caps[3] == 200000 && (caps[1] & 0x10), "caps %u %u %x", caps[2], caps[3], caps[1]);
    caps[0] = 20;
    CHECK(ds_device_get_caps(d, caps) == DSERR_INVALIDPARAM, "dwSize check");
    uint32_t spk = 0;
    OK(ds_device_get_speaker_config(d, &spk));
    CHECK(spk == DSSPEAKER_5POINT1, "speaker config %u", spk);

    // buffer caps: size rounded down to the block
    OK(ds_device_create_buffer(d, DSBCAPS_CTRLVOLUME | DSBCAPS_GETCURRENTPOSITION2, 1000, w6.data(), mem.data(), &b));
    uint32_t bc[5] = {20};
    OK(ds_buffer_get_caps(b, bc));
    CHECK(bc[2] == 996 && (bc[1] & DSBCAPS_LOCSOFTWARE) && (bc[1] & DSBCAPS_CTRLVOLUME), "dsbcaps %u %x", bc[2], bc[1]);
    ds_buffer_release(b);

    // primary: default format, SetFormat needs PRIORITY, EXTENSIBLE 6ch round-trips
    DsBuffer *p = nullptr;
    OK(ds_device_create_buffer(d, DSBCAPS_PRIMARYBUFFER | DSBCAPS_CTRLVOLUME, 0, nullptr, nullptr, &p));
    uint8_t got[64];
    uint32_t n = 0;
    OK(ds_buffer_get_format(p, got, sizeof got, &n));
    CHECK(n == 18 && got[2] == 2 && got[4] == 0x22 && got[5] == 0x56 && got[14] == 8, "default primary format");
    auto ext = wfx(6, 48000, 16, 0x3F);
    CHECK(ds_buffer_set_format(p, ext.data()) == DSERR_PRIOLEVELNEEDED, "SetFormat at NORMAL");
    OK(ds_device_set_cooperative_level(d, DSSCL_PRIORITY));
    OK(ds_buffer_set_format(p, ext.data()));
    OK(ds_buffer_get_format(p, got, sizeof got, &n));
    CHECK(n == 40 && memcmp(got, ext.data(), 40) == 0, "primary format round trip (%u bytes)", n);
    OK(ds_buffer_play(p, DSBPLAY_LOOPING));
    uint32_t st = 0;
    OK(ds_buffer_get_status(p, &st));
    CHECK(st & DSBSTATUS_PLAYING, "primary status %x", st);
    uint32_t o1, l1, o2, l2;
    CHECK(ds_buffer_lock(p, 0, 16, 0, &o1, &l1, &o2, &l2) == DSERR_PRIOLEVELNEEDED, "primary lock");
    // a second primary handle shares the format
    DsBuffer *p2 = nullptr;
    OK(ds_device_create_buffer(d, DSBCAPS_PRIMARYBUFFER, 0, nullptr, nullptr, &p2));
    OK(ds_buffer_get_format(p2, got, sizeof got, &n));
    CHECK(n == 40, "second primary handle");
    ds_buffer_release(p2);
    ds_buffer_release(p);
    ds_device_release(d);
}

static void test_locks() {
    printf("Lock / Unlock / cursors (manual clock)\n");
    DsDevice *d = manual_device();
    std::vector<uint8_t> mem(1000);
    auto w6 = wfx(6, 48000, 16);
    DsBuffer *b = nullptr;
    OK(ds_device_create_buffer(d, 0, 1000, w6.data(), mem.data(), &b));  // 83 frames, 996 bytes
    uint32_t o1, l1, o2, l2;
    OK(ds_buffer_lock(b, 984, 36, 0, &o1, &l1, &o2, &l2));
    CHECK(o1 == 984 && l1 == 12 && o2 == 0 && l2 == 24, "wrap lock %u %u %u %u", o1, l1, o2, l2);
    OK(ds_buffer_unlock(b, o1, l1, o2, l2));
    OK(ds_buffer_lock(b, 0, 996, 0, &o1, &l1, &o2, &l2));
    CHECK(o1 == 0 && l1 == 996 && l2 == 0, "full lock %u %u", l1, l2);
    OK(ds_buffer_lock(b, 500, 0, DSBLOCK_ENTIREBUFFER, &o1, &l1, &o2, &l2));
    CHECK(o1 == 500 && l1 == 496 && l2 == 500, "ENTIREBUFFER from 500: %u %u %u", o1, l1, l2);
    CHECK(ds_buffer_lock(b, 0, 997, 0, &o1, &l1, &o2, &l2) == DSERR_INVALIDPARAM, "too big");
    CHECK(ds_buffer_lock(b, 996, 12, 0, &o1, &l1, &o2, &l2) == DSERR_INVALIDPARAM, "offset past end");
    CHECK(ds_buffer_lock(b, 0, 0, 0, &o1, &l1, &o2, &l2) == DSERR_INVALIDPARAM, "zero bytes");
    OK(ds_buffer_lock(b, 984, 36, 0, &o1, &l1, nullptr, nullptr));
    CHECK(o1 == 984 && l1 == 12, "wrap without region 2 is truncated: %u %u", o1, l1);
    CHECK(ds_buffer_unlock(b, 990, 12, 0, 0) == DSERR_INVALIDPARAM, "unlock out of range");
    OK(ds_buffer_set_current_position(b, 120));
    OK(ds_buffer_lock(b, 0, 24, DSBLOCK_FROMWRITECURSOR, &o1, &l1, &o2, &l2));
    CHECK(o1 == 120 && l1 == 24, "FROMWRITECURSOR %u", o1);
    CHECK(ds_buffer_set_current_position(b, 996) == DSERR_INVALIDPARAM, "SetCurrentPosition past end");
    OK(ds_buffer_set_current_position(b, 125));  // rounded down to the block
    uint32_t play, write;
    OK(ds_buffer_get_current_position(b, &play, &write));
    CHECK(play == 120 && write == 120, "stopped cursors %u %u", play, write);
    // play and render 50 frames: cursor moves 50 frames, wrapping around 83
    OK(ds_buffer_play(b, DSBPLAY_LOOPING));
    std::vector<float> out(6 * 200);
    OK(ds_device_render(d, out.data(), 50));
    OK(ds_buffer_get_current_position(b, &play, &write));
    CHECK(play == 60 * 12 && write == 60 * 12, "after 50 frames %u %u", play / 12, write / 12);
    OK(ds_device_render(d, out.data(), 40));
    OK(ds_buffer_get_current_position(b, &play, &write));
    CHECK(play == (100 - 83) * 12, "after wrap %u", play / 12);
    OK(ds_buffer_stop(b));
    OK(ds_buffer_get_current_position(b, &play, &write));
    CHECK(play == 17 * 12 && write == 17 * 12, "stop keeps position %u %u", play / 12, write / 12);
    ds_buffer_release(b);
    ds_device_release(d);
}

static void test_manual_stream() {
    printf("6-ch 16-bit 48 kHz looping stream, manual clock, bit-exact (10 s)\n");
    DsDevice *d = manual_device();
    const uint32_t frames = 12000, bytes = frames * 12;  // 0.25 s
    std::vector<uint8_t> mem(bytes);
    auto w = wfx(6, 48000, 16, 0x3F);
    DsBuffer *b = nullptr;
    OK(ds_device_create_buffer(d, DSBCAPS_CTRLVOLUME | DSBCAPS_GETCURRENTPOSITION2, bytes, w.data(), mem.data(), &b));
    Streamer s{b, mem.data(), bytes};
    for (uint32_t f = 0; f < frames; ++f) put6(mem.data(), f, s.next++);
    OK(ds_buffer_play(b, DSBPLAY_LOOPING));
    std::mt19937 rng(1);
    std::vector<float> out, chunk;
    const size_t total = 480000;
    while (out.size() / 6 < total) {
        size_t n = 17 + rng() % 700;
        chunk.resize(n * 6);
        OK(ds_device_render(d, chunk.data(), n));
        out.insert(out.end(), chunk.begin(), chunk.end());
        CHECK(s.step(), "streamer");
    }
    size_t checked = 0, bad = verify_stream(out, 0, &checked);
    CHECK(bad == 0, "%zu of %zu frames wrong", bad, checked);
    printf("  %zu frames verified per channel, %llu locks (%llu wrapped)\n", checked, (unsigned long long)s.locks,
           (unsigned long long)s.wraps);
    DsStats st;
    OK(ds_device_get_stats(d, &st));
    CHECK(st.frames_written == out.size() / 6, "frames_written");
    ds_buffer_release(b);
    ds_device_release(d);
}

// Real-time streaming: the game polls the cursors from its own thread; the mixer runs on its own clock.
static void realtime_stream(DsDevice *d, std::vector<float> &out, std::mutex &om, double seconds, const char *label) {
    const uint32_t frames = 24000, bytes = frames * 12;  // 0.5 s, like a typical streaming buffer
    std::vector<uint8_t> mem(bytes);
    auto w = wfx(6, 48000, 16, 0x3F);
    DsBuffer *b = nullptr;
    OK(ds_device_create_buffer(d, DSBCAPS_CTRLVOLUME | DSBCAPS_GETCURRENTPOSITION2, bytes, w.data(), mem.data(), &b));
    Streamer s{b, mem.data(), bytes};
    for (uint32_t f = 0; f < frames; ++f) put6(mem.data(), f, s.next++);
    DsStats st0;
    OK(ds_device_get_stats(d, &st0));
    { std::lock_guard<std::mutex> g(om); out.clear(); }
    OK(ds_buffer_play(b, DSBPLAY_LOOPING));
    // cursor samples: (time, unwrapped play frame)
    std::vector<std::pair<double, double>> samples;
    double t0 = now_s(), lag_sum = 0;
    uint64_t unwrapped = 0, backwards = 0, polls = 0;
    uint32_t last_play = 0;
    std::mt19937 rng(2);
    while (now_s() - t0 < seconds) {
        uint32_t play, write;
        OK(ds_buffer_get_current_position(b, &play, &write));
        uint32_t delta = (play + bytes - last_play) % bytes;
        if (delta > bytes / 2) ++backwards;
        else unwrapped += delta / 12;
        last_play = play;
        lag_sum += (double)((write + bytes - play) % bytes) / 12;
        ++polls;
        samples.push_back({now_s(), (double)unwrapped});
        CHECK(s.step(), "streamer");
        std::this_thread::sleep_for(std::chrono::microseconds(1000 + rng() % 9000));  // 1-10 ms, like a game frame
    }
    OK(ds_buffer_stop(b));
    DsStats st;
    OK(ds_device_get_stats(d, &st));
    // play cursor rate: least squares over the run, skipping the first 0.2 s (latency fill)
    double sx = 0, sy = 0, sxx = 0, sxy = 0;
    int k = 0;
    for (auto &p : samples) {
        if (p.first - t0 < 0.2) continue;
        sx += p.first; sy += p.second; sxx += p.first * p.first; sxy += p.first * p.second; ++k;
    }
    double rate = (k * sxy - sx * sy) / (k * sxx - sx * sx);
    CHECK(std::fabs(rate - 48000) < 480, "%s: play cursor %.0f frames/s", label, rate);
    CHECK(backwards == 0, "%s: play cursor moved backwards %llu times", label, (unsigned long long)backwards);
    uint64_t underruns = st.underruns - st0.underruns;
    CHECK(underruns == 0, "%s: %llu underruns", label, (unsigned long long)underruns);
    // output: the stream starts somewhere (the mixer was already running), then must be continuous
    std::vector<float> o;
    { std::lock_guard<std::mutex> g(om); o = out; }
    size_t k0 = 0, frames_out = o.size() / 6;
    while (k0 < frames_out && o[k0 * 6 + 1] != sig(1, 0) / 32768.0f) ++k0;  // ch 0 starts at 0, like silence
    // the tail after Stop is the last chunk; verify up to the frames the streamer kept ahead of
    size_t limit = std::min<size_t>(frames_out, k0 + (size_t)s.next);
    o.resize(limit * 6);
    size_t checked = 0, bad = verify_stream(o, k0, &checked);
    CHECK(k0 < frames_out && bad == 0 && checked > seconds * 48000 * 0.9, "%s: %zu bad of %zu (start %zu)", label, bad,
          checked, k0);
    printf("  %s: cursor %.1f frames/s, write-play %.0f frames, %llu polls, %llu locks (%llu wrapped), %zu frames "
           "bit-exact, %llu underruns\n",
           label, rate, lag_sum / polls, (unsigned long long)polls, (unsigned long long)s.locks,
           (unsigned long long)s.wraps, checked, (unsigned long long)underruns);
    ds_buffer_release(b);
}

static void tap_collect(void *user, const float *pcm, size_t frames) {
    auto *ctx = (std::pair<std::vector<float> *, std::mutex *> *)user;
    std::lock_guard<std::mutex> g(*ctx->second);
    ctx->first->insert(ctx->first->end(), pcm, pcm + frames * 6);
}

static void test_realtime(DsSink sink, DsOutputMode mode, const char *label, double seconds) {
    std::vector<float> out;
    std::mutex om;
    std::pair<std::vector<float> *, std::mutex *> ctx{&out, &om};
    DsConfig c;
    ds_config_default(&c);
    c.sink = sink;
    c.mode = mode;
    c.mute_device = 1;
    c.tap = tap_collect;
    c.tap_user = &ctx;
    DsDevice *d = nullptr;
    DsResult r = ds_device_create(&c, &d);
    CHECK(r == DS_OK, "%s: create %08x", label, (unsigned)r);
    if (r != DS_OK) return;
    realtime_stream(d, out, om, seconds, label);
    ds_device_release(d);
}

static void test_volume_pan() {
    printf("volume / pan / channel mapping\n");
    DsDevice *d = manual_device(64);
    std::vector<float> out(6 * 64);
    auto settle = [&]() { ds_device_render(d, out.data(), 64); ds_device_render(d, out.data(), 64); };
    auto near = [](float a, double b) { return std::fabs(a - b) < 1e-5; };

    // mono 16-bit, DC 0.5
    std::vector<int16_t> mono(4800, 16384);
    auto w1 = wfx(1, 48000, 16);
    DsBuffer *m = nullptr;
    OK(ds_device_create_buffer(d, DSBCAPS_CTRLVOLUME | DSBCAPS_CTRLPAN, 9600, w1.data(), mono.data(), &m));
    OK(ds_buffer_play(m, DSBPLAY_LOOPING));
    settle();
    CHECK(near(out[0], 0.5) && near(out[1], 0.5) && out[2] == 0 && out[3] == 0 && out[4] == 0 && out[5] == 0,
          "mono centre: %f %f %f", out[0], out[1], out[2]);
    OK(ds_buffer_set_volume(m, -600));
    settle();
    CHECK(near(out[0], 0.5 * std::pow(10, -0.3)) && near(out[1], 0.5 * std::pow(10, -0.3)), "-6 dB: %f", out[0]);
    OK(ds_buffer_set_volume(m, 0));
    OK(ds_buffer_set_pan(m, 1000));
    settle();
    CHECK(near(out[0], 0.5 * std::pow(10, -0.5)) && near(out[1], 0.5), "pan +1000: %f %f", out[0], out[1]);
    OK(ds_buffer_set_pan(m, -2500));
    settle();
    CHECK(near(out[0], 0.5) && near(out[1], 0.5 * std::pow(10, -1.25)), "pan -2500: %f %f", out[0], out[1]);
    OK(ds_buffer_set_pan(m, DSBPAN_LEFT));
    settle();
    CHECK(near(out[0], 0.5) && out[1] == 0, "pan left: %f %f", out[0], out[1]);
    OK(ds_buffer_set_pan(m, 0));
    OK(ds_buffer_set_volume(m, DSBVOLUME_MIN));
    settle();
    CHECK(out[0] == 0 && out[1] == 0, "-100 dB is silence");
    CHECK(ds_buffer_set_volume(m, 1) == DSERR_INVALIDPARAM && ds_buffer_set_pan(m, 10001) == DSERR_INVALIDPARAM,
          "ranges");
    int32_t v = 1;
    OK(ds_buffer_get_volume(m, &v));
    CHECK(v == DSBVOLUME_MIN, "GetVolume %d", v);
    // a volume change ramps over one block (no click): first block after the change is monotonic between levels
    OK(ds_buffer_set_volume(m, 0));
    ds_device_render(d, out.data(), 64);
    CHECK(out[0] > 0 && out[0] < 0.5f && near(out[63 * 6], 0.5) && out[32 * 6] > out[0], "ramp %f .. %f", out[0],
          out[63 * 6]);
    OK(ds_buffer_stop(m));

    // stereo: L 0.5, R 0.25, pan -2000 attenuates R by 20 dB
    std::vector<int16_t> st(9600);
    for (size_t i = 0; i < st.size(); i += 2) { st[i] = 16384; st[i + 1] = 8192; }
    auto w2 = wfx(2, 48000, 16);
    DsBuffer *s = nullptr;
    OK(ds_device_create_buffer(d, DSBCAPS_CTRLPAN, 19200, w2.data(), st.data(), &s));
    OK(ds_buffer_play(s, DSBPLAY_LOOPING));
    settle();
    CHECK(near(out[0], 0.5) && near(out[1], 0.25), "stereo %f %f", out[0], out[1]);
    OK(ds_buffer_set_pan(s, -2000));
    settle();
    CHECK(near(out[0], 0.5) && near(out[1], 0.025), "stereo pan -2000: %f %f", out[0], out[1]);
    CHECK(ds_buffer_set_volume(s, -100) == DSERR_CONTROLUNAVAIL, "no CTRLVOLUME");
    OK(ds_buffer_stop(s));

    // 6-ch: each channel lands 1:1, volume applies to all; primary volume is a master gain
    std::vector<int16_t> six(6 * 480);
    for (size_t i = 0; i < six.size(); ++i) six[i] = (int16_t)(1000 * (int)(i % 6 + 1));
    auto w6 = wfx(6, 48000, 16, 0x3F);
    DsBuffer *x = nullptr;
    OK(ds_device_create_buffer(d, DSBCAPS_CTRLVOLUME, (uint32_t)six.size() * 2, w6.data(), six.data(), &x));
    OK(ds_buffer_play(x, DSBPLAY_LOOPING));
    settle();
    bool ok = true;
    for (int c = 0; c < 6; ++c) ok &= out[c] == 1000.0f * (c + 1) / 32768.0f;
    CHECK(ok, "6-ch 1:1 exact");
    OK(ds_buffer_set_volume(x, -2000));
    settle();
    ok = true;
    for (int c = 0; c < 6; ++c) ok &= near(out[c], 0.1 * 1000.0 * (c + 1) / 32768.0);
    CHECK(ok, "6-ch -20 dB: %f", out[0]);
    OK(ds_buffer_set_volume(x, 0));
    DsBuffer *p = nullptr;
    OK(ds_device_create_buffer(d, DSBCAPS_PRIMARYBUFFER | DSBCAPS_CTRLVOLUME, 0, nullptr, nullptr, &p));
    OK(ds_buffer_set_volume(p, -600));
    settle();
    CHECK(near(out[5], std::pow(10, -0.3) * 6000 / 32768.0), "master -6 dB: %f", out[5]);
    OK(ds_buffer_set_volume(p, 0));
    // two buffers sum, and the sum is clamped
    OK(ds_buffer_play(m, DSBPLAY_LOOPING));
    OK(ds_buffer_set_volume(m, 0));
    settle();
    CHECK(near(out[0], 0.5 + 1000 / 32768.0) && near(out[2], 3000 / 32768.0), "sum %f", out[0]);
    std::vector<int16_t> loud(4800, 32767);
    DsBuffer *l1 = nullptr, *l2 = nullptr;
    OK(ds_device_create_buffer(d, 0, 9600, w1.data(), loud.data(), &l1));
    OK(ds_device_duplicate_buffer(d, l1, &l2));  // duplicate shares the memory
    OK(ds_buffer_play(l1, DSBPLAY_LOOPING));
    OK(ds_buffer_play(l2, DSBPLAY_LOOPING));
    settle();
    CHECK(out[0] == 1.0f && out[1] == 1.0f, "clamped at 1.0: %f", out[0]);
    // 8-bit unsigned mono: 192 -> 0.5; quad maps FL FR BL BR
    std::vector<uint8_t> u8(4800, 192);
    auto w8 = wfx(1, 48000, 8);
    DsBuffer *e = nullptr;
    OK(ds_device_create_buffer(d, 0, 4800, w8.data(), u8.data(), &e));
    ds_buffer_stop(m); ds_buffer_stop(x); ds_buffer_stop(l1); ds_buffer_stop(l2);
    OK(ds_buffer_play(e, DSBPLAY_LOOPING));
    settle();
    CHECK(out[0] == 0.5f && out[1] == 0.5f, "8-bit %f", out[0]);
    ds_buffer_stop(e);
    std::vector<int16_t> q(4 * 480);
    for (size_t i = 0; i < q.size(); ++i) q[i] = (int16_t)(1000 * (int)(i % 4 + 1));
    auto w4 = wfx(4, 48000, 16);
    DsBuffer *qb = nullptr;
    OK(ds_device_create_buffer(d, 0, (uint32_t)q.size() * 2, w4.data(), q.data(), &qb));
    OK(ds_buffer_play(qb, DSBPLAY_LOOPING));
    settle();
    CHECK(out[0] == 1000 / 32768.0f && out[1] == 2000 / 32768.0f && out[2] == 0 && out[3] == 0 &&
              out[4] == 3000 / 32768.0f && out[5] == 4000 / 32768.0f, "quad mapping");
    for (DsBuffer *bb : {m, s, x, p, l1, l2, e, qb}) ds_buffer_release(bb);
    ds_device_release(d);
}

// Least-squares fit of a*sin + b*cos at freq; returns SNR in dB of the residual, amplitude in *amp.
static double sine_snr(const std::vector<float> &o, int ch, size_t from, size_t n, double freq, double rate, double *amp) {
    double ss = 0, sc = 0, cc = 0, ys = 0, yc = 0;
    for (size_t i = 0; i < n; ++i) {
        double t = 2 * M_PI * freq * (double)i / rate, s = std::sin(t), c = std::cos(t), y = o[(from + i) * 6 + ch];
        ss += s * s; sc += s * c; cc += c * c; ys += y * s; yc += y * c;
    }
    double det = ss * cc - sc * sc, a = (ys * cc - yc * sc) / det, b = (yc * ss - ys * sc) / det;
    double sig2 = 0, err2 = 0;
    for (size_t i = 0; i < n; ++i) {
        double t = 2 * M_PI * freq * (double)i / rate, fit = a * std::sin(t) + b * std::cos(t), y = o[(from + i) * 6 + ch];
        sig2 += fit * fit;
        err2 += (y - fit) * (y - fit);
    }
    *amp = std::sqrt(a * a + b * b);
    return 10 * std::log10(sig2 / std::max(err2, 1e-30));
}

static void test_resample() {
    printf("resampling 22050 / 44100 -> 48000\n");
    for (int rate : {22050, 44100, 32000, 11025}) {
        DsDevice *d = manual_device();
        // 1 s of a 1 kHz sine at 0.5, stereo; R = 0.25 * the same
        std::vector<int16_t> pcm((size_t)rate * 2);
        for (int i = 0; i < rate; ++i) {
            double v = std::sin(2 * M_PI * 1000.0 * i / rate);
            pcm[(size_t)i * 2] = (int16_t)std::lrint(16384 * v);
            pcm[(size_t)i * 2 + 1] = (int16_t)std::lrint(8192 * v);
        }
        auto w = wfx(2, rate, 16);
        DsBuffer *b = nullptr;
        OK(ds_device_create_buffer(d, DSBCAPS_CTRLFREQUENCY, (uint32_t)pcm.size() * 2, w.data(), pcm.data(), &b));
        OK(ds_buffer_play(b, DSBPLAY_LOOPING));
        std::vector<float> out(6 * 48000);
        OK(ds_device_render(d, out.data(), 24000));
        uint32_t play, write;
        OK(ds_buffer_get_current_position(b, &play, &write));
        double expect = 24000.0 * rate / 48000.0;
        CHECK(play / 4 == (uint32_t)expect, "%d Hz: cursor %u frames after 0.5 s (expect %.1f)", rate, play / 4,
              expect);
        OK(ds_device_render(d, out.data(), 48000));
        double amp_l, amp_r;
        double snr_l = sine_snr(out, 0, 100, 47000, 1000, 48000, &amp_l);
        double snr_r = sine_snr(out, 1, 100, 47000, 1000, 48000, &amp_r);
        CHECK(snr_l > 50 && std::fabs(amp_l - 0.5) < 0.005 && std::fabs(amp_r - 0.25) < 0.003,
              "%d Hz: SNR %.1f dB, amp %.4f / %.4f", rate, snr_l, amp_l, amp_r);
        printf("  %5d Hz: 1 kHz sine SNR %.1f dB (L) %.1f dB (R), amplitude %.4f / %.4f, cursor %u/%.1f frames\n", rate,
               snr_l, snr_r, amp_l, amp_r, play / 4, expect);
        // SetFrequency doubles the cursor speed
        OK(ds_buffer_get_current_position(b, &play, &write));
        OK(ds_buffer_set_frequency(b, (uint32_t)rate * 2));
        uint32_t hz = 0;
        OK(ds_buffer_get_frequency(b, &hz));
        CHECK(hz == (uint32_t)rate * 2, "GetFrequency %u", hz);
        uint32_t before = play;
        OK(ds_device_render(d, out.data(), 4800));
        OK(ds_buffer_get_current_position(b, &play, &write));
        uint32_t moved = (play + (uint32_t)pcm.size() * 2 - before) % ((uint32_t)pcm.size() * 2) / 4;
        CHECK(std::abs((int)moved - (int)(4800.0 * rate * 2 / 48000)) <= 1, "%d Hz x2: moved %u", rate, moved);
        OK(ds_buffer_set_frequency(b, 0));
        OK(ds_buffer_get_frequency(b, &hz));
        CHECK(hz == (uint32_t)rate, "DSBFREQUENCY_ORIGINAL");
        CHECK(ds_buffer_set_frequency(b, 99) == DSERR_INVALIDPARAM, "min frequency");
        ds_buffer_release(b);
        ds_device_release(d);
    }
}

static std::vector<uint32_t> g_events;
static std::mutex g_events_m;
static void on_notify(void *, uint32_t e) {
    std::lock_guard<std::mutex> g(g_events_m);
    g_events.push_back(e);
}

static void test_oneshot_notify() {
    printf("one-shot buffers, notifications, release order\n");
    DsConfig c;
    ds_config_default(&c);
    c.sink = DS_SINK_MANUAL;
    c.block_frames = 100;
    c.notify = on_notify;
    DsDevice *d = nullptr;
    OK(ds_device_create(&c, &d));
    const uint32_t frames = 1000;
    std::vector<uint8_t> mem(frames * 12);
    for (uint32_t f = 0; f < frames; ++f) put6(mem.data(), f, f);
    auto w = wfx(6, 48000, 16);
    DsBuffer *b = nullptr;
    OK(ds_device_create_buffer(d, DSBCAPS_CTRLPOSITIONNOTIFY, frames * 12, w.data(), mem.data(), &b));
    uint32_t offs[3] = {250 * 12, DSBPN_OFFSETSTOP, 700 * 12}, evs[3] = {11, 99, 22};
    OK(ds_buffer_set_notification_positions(b, 3, offs, evs));
    OK(ds_buffer_play(b, 0));
    CHECK(ds_buffer_set_notification_positions(b, 0, nullptr, nullptr) == DSERR_INVALIDCALL, "notify while playing");
    std::vector<float> out(6 * 1500);
    OK(ds_device_render(d, out.data(), 600));
    {
        std::lock_guard<std::mutex> g(g_events_m);
        CHECK(g_events.size() == 1 && g_events[0] == 11, "event at 250 (%zu events)", g_events.size());
    }
    uint32_t st = 0;
    OK(ds_buffer_get_status(b, &st));
    CHECK((st & DSBSTATUS_PLAYING) && !(st & DSBSTATUS_LOOPING), "status playing %x", st);
    OK(ds_device_render(d, out.data() + 600 * 6, 900));
    size_t checked = 0;
    std::vector<float> head(out.begin(), out.begin() + frames * 6);
    CHECK(verify_stream(head, 0, &checked) == 0, "one-shot content");
    bool silent = true;
    for (size_t i = frames * 6; i < out.size(); ++i) silent &= out[i] == 0;
    CHECK(silent, "silence after the end");
    OK(ds_buffer_get_status(b, &st));
    CHECK(!(st & DSBSTATUS_PLAYING), "stopped at end %x", st);
    uint32_t play, write;
    OK(ds_buffer_get_current_position(b, &play, &write));
    CHECK(play == 0, "rewound to 0: %u", play);
    {
        std::lock_guard<std::mutex> g(g_events_m);
        CHECK(g_events.size() == 3 && g_events[1] == 22 && g_events[2] == 99, "events 11 22 99 (%zu)", g_events.size());
        g_events.clear();
    }
    // looping buffer: notifications every lap; Stop fires OFFSETSTOP
    OK(ds_buffer_play(b, DSBPLAY_LOOPING));
    OK(ds_device_render(d, out.data(), 1500));  // 0..1500: 250, 700, 1250
    OK(ds_buffer_stop(b));
    {
        std::lock_guard<std::mutex> g(g_events_m);
        CHECK(g_events.size() == 4 && g_events[0] == 11 && g_events[1] == 22 && g_events[2] == 11 && g_events[3] == 99,
              "looping events (%zu)", g_events.size());
    }
    // release the device first; the buffer handle reports BUFFERLOST until released
    ds_device_release(d);
    CHECK(ds_buffer_play(b, 0) == DSERR_BUFFERLOST, "lost after device release");
    OK(ds_buffer_get_status(b, &st));
    CHECK(st == DSBSTATUS_BUFFERLOST, "status lost");
    ds_buffer_release(b);
}

// Several threads create, play, change and release buffers while the mixer runs (null sink).
static void test_threads() {
    printf("thread stress (null sink, 1.5 s)\n");
    DsConfig c;
    ds_config_default(&c);
    c.sink = DS_SINK_NULL;
    DsDevice *d = nullptr;
    OK(ds_device_create(&c, &d));
    std::atomic<bool> stop{false};
    std::atomic<uint64_t> ops{0};
    std::vector<std::thread> th;
    for (int t = 0; t < 4; ++t)
        th.emplace_back([&, t]() {
            std::mt19937 rng(100 + t);
            std::vector<int16_t> mem(2 * 4410);
            for (auto &v : mem) v = (int16_t)(rng() & 0x3FFF);
            int rates[] = {11025, 22050, 44100, 48000};
            auto w = wfx(1 + (t & 1), rates[t], 16);
            while (!stop) {
                DsBuffer *b = nullptr;
                uint32_t bytes = (uint32_t)mem.size() * 2 / (2 - (t & 1)) / 2 * 2;
                if (ds_device_create_buffer(d, DSBCAPS_CTRLVOLUME | DSBCAPS_CTRLPAN | DSBCAPS_CTRLFREQUENCY, bytes,
                                            w.data(), mem.data(), &b) != DS_OK)
                    continue;
                ds_buffer_play(b, rng() & 1 ? DSBPLAY_LOOPING : 0);
                for (int i = 0; i < 50; ++i) {
                    uint32_t p, wr, o1, l1, o2, l2;
                    ds_buffer_get_current_position(b, &p, &wr);
                    if (ds_buffer_lock(b, p, 400, 0, &o1, &l1, &o2, &l2) == DS_OK) ds_buffer_unlock(b, o1, l1, o2, l2);
                    ds_buffer_set_volume(b, -(int32_t)(rng() % 3000));
                    ds_buffer_set_pan(b, (int32_t)(rng() % 4000) - 2000);
                    ds_buffer_set_frequency(b, 8000 + rng() % 40000);
                    if (rng() % 10 == 0) ds_buffer_set_current_position(b, (rng() % 1000) * 4);
                    std::this_thread::sleep_for(std::chrono::microseconds(rng() % 500));
                    ++ops;
                }
                ds_buffer_stop(b);
                ds_buffer_release(b);
            }
        });
    std::this_thread::sleep_for(std::chrono::milliseconds(1500));
    stop = true;
    for (auto &t : th) t.join();
    DsStats st;
    OK(ds_device_get_stats(d, &st));
    CHECK(st.buffers == 0 && st.frames_written > 48000, "stats: %u buffers, %llu frames", st.buffers,
          (unsigned long long)st.frames_written);
    printf("  %llu API calls x4 per thread loop, %llu frames mixed, %llu underruns\n", (unsigned long long)ops.load(),
           (unsigned long long)st.frames_written, (unsigned long long)st.underruns);
    ds_device_release(d);
}

static void test_mixer_cost() {
    printf("mixer cost (manual clock)\n");
    DsDevice *d = manual_device();
    const uint32_t frames = 24000;
    std::vector<uint8_t> mem(frames * 12);
    for (uint32_t f = 0; f < frames; ++f) put6(mem.data(), f, f);
    auto w = wfx(6, 48000, 16, 0x3F);
    DsBuffer *b = nullptr, *r = nullptr;
    OK(ds_device_create_buffer(d, DSBCAPS_CTRLVOLUME, frames * 12, w.data(), mem.data(), &b));
    OK(ds_buffer_play(b, DSBPLAY_LOOPING));
    std::vector<float> out(6 * 48000);
    double t0 = now_s();
    for (int i = 0; i < 20; ++i) ds_device_render(d, out.data(), 48000);
    double t6 = (now_s() - t0) / 20;
    ds_buffer_stop(b);
    auto w2 = wfx(6, 44100, 16, 0x3F);
    OK(ds_device_create_buffer(d, 0, frames * 12, w2.data(), mem.data(), &r));
    OK(ds_buffer_play(r, DSBPLAY_LOOPING));
    t0 = now_s();
    for (int i = 0; i < 20; ++i) ds_device_render(d, out.data(), 48000);
    double t44 = (now_s() - t0) / 20;
    printf("  one second of output: 6-ch 48 kHz buffer %.3f ms (%.0fx real time), 6-ch 44.1 kHz resampled %.3f ms\n",
           t6 * 1000, 1 / t6, t44 * 1000);
    CHECK(t6 < 0.01 && t44 < 0.02, "mixer too slow");
    ds_buffer_release(b);
    ds_buffer_release(r);
    ds_device_release(d);
}

static void test_auto_rate() {
    printf("auto mix rate: primary 6-ch EXTENSIBLE 44.1 kHz -> bit-exact passthrough\n");
    DsConfig c;
    ds_config_default(&c);
    c.sink = DS_SINK_MANUAL;
    c.mix_rate = 0;
    DsDevice *d = nullptr;
    OK(ds_device_create(&c, &d));
    OK(ds_device_set_cooperative_level(d, DSSCL_PRIORITY));
    DsBuffer *p = nullptr, *b = nullptr;
    OK(ds_device_create_buffer(d, DSBCAPS_PRIMARYBUFFER, 0, nullptr, nullptr, &p));
    auto w = wfx(6, 44100, 16, 0x3F);
    OK(ds_buffer_set_format(p, w.data()));
    const uint32_t frames = 22048;
    std::vector<uint8_t> mem(frames * 12);
    for (uint32_t f = 0; f < frames; ++f) put6(mem.data(), f, f);
    OK(ds_device_create_buffer(d, DSBCAPS_GLOBALFOCUS | DSBCAPS_GETCURRENTPOSITION2, frames * 12, w.data(), mem.data(), &b));
    OK(ds_buffer_play(b, DSBPLAY_LOOPING));
    std::vector<float> out(6 * frames);
    OK(ds_device_render(d, out.data(), frames));
    size_t checked = 0;
    CHECK(verify_stream(out, 0, &checked) == 0 && checked == frames, "44.1 kHz passthrough");
    uint32_t play, write;
    OK(ds_device_render(d, out.data(), 1000));
    OK(ds_buffer_get_current_position(b, &play, &write));
    CHECK(play == 1000 * 12, "cursor %u", play / 12);
    ds_buffer_release(b);
    ds_buffer_release(p);
    ds_device_release(d);
}

// A replica of the game's own streamer (Snd, FUN_00820762 / FUN_00820907 in speed.exe): primary SetFormat to
// EXTENSIBLE 6-ch 16-bit 44.1 kHz, one 0.5 s secondary (GLOBALFOCUS | GETCURRENTPOSITION2), zero-filled and played
// looping; a thread every 10 ms reads only the write cursor and fills from its last position up to
// (write + (30 ms + max interval) * rate) & ~15 frames, with locks split at the end (no second region). The game
// restarts DirectSound if the write cursor doesn't move for 100 polls; that must never get close (max_stuck: the limit
// checked; 10 on a healthy output, 60 while the output stalls, see test_fake_snd_drop).
static int g_snd_max_stuck;   // the last run's longest unchanged-write-cursor streak
static void test_snd_like(DsSink sink, const char *label, double seconds, int stuck_limit = 10, bool exact = true) {
    std::vector<float> out;
    std::mutex om;
    std::pair<std::vector<float> *, std::mutex *> ctx{&out, &om};
    DsConfig c;
    ds_config_default(&c);
    c.sink = sink;
    c.mode = DS_OUT_PCM51;
    c.mix_rate = 0;
    c.mute_device = 1;
    c.tap = tap_collect;
    c.tap_user = &ctx;
    DsDevice *d = nullptr;
    DsResult r = ds_device_create(&c, &d);
    CHECK(r == DS_OK, "%s: create %08x", label, (unsigned)r);
    if (r != DS_OK) return;
    OK(ds_device_set_cooperative_level(d, DSSCL_PRIORITY));
    uint32_t caps[24] = {96};
    OK(ds_device_get_caps(d, caps));
    const int rate = caps[3] < 44101 ? (caps[3] ? (int)caps[3] : 11025) : 44100;  // the game's probe
    CHECK(rate == 44100, "probe rate %d", rate);
    DsBuffer *p = nullptr, *b = nullptr;
    OK(ds_device_create_buffer(d, DSBCAPS_PRIMARYBUFFER, 0, nullptr, nullptr, &p));
    auto w = wfx(6, rate, 16, 0x3F);
    OK(ds_buffer_set_format(p, w.data()));
    const uint32_t frames = (uint32_t)(rate * 500 / 1000) & ~15u, bytes = frames * 12;
    std::vector<uint8_t> mem(bytes);
    OK(ds_device_create_buffer(d, 0x18000, bytes, w.data(), mem.data(), &b));
    uint32_t o1, l1;
    OK(ds_buffer_lock(b, 0, bytes, 0, &o1, &l1, nullptr, nullptr));
    memset(mem.data() + o1, 0, l1);
    OK(ds_buffer_unlock(b, o1, l1, 0, 0));
    OK(ds_buffer_play(b, DSBPLAY_LOOPING));
    uint32_t last_pos = 0, last_wc = 0xFFFFFFFF;
    uint64_t next = 0, polls = 0;
    int stuck = 0, max_stuck = 0;
    double t0 = now_s(), prev = t0, max_iv_ms = 15;
    while (now_s() - t0 < seconds) {
        double t = now_s();
        max_iv_ms = std::max(max_iv_ms, std::min(300.0, (t - prev) * 1000));
        prev = t;
        uint32_t st = 0, wc = 0;
        OK(ds_buffer_get_status(b, &st));
        CHECK(st & DSBSTATUS_PLAYING, "%s: status %x", label, st);
        OK(ds_buffer_get_current_position(b, nullptr, &wc));
        ++polls;
        if (wc == last_wc) max_stuck = std::max(max_stuck, ++stuck);
        else stuck = 0;
        last_wc = wc;
        uint32_t target = (uint32_t)(wc / 12 + (int)((30 + max_iv_ms) * 0.001 * rate)) & 0xffffff0;
        if (target >= frames) target -= frames;
        int diff = (int)target - (int)last_pos;
        if (!(diff < 0 && -(int)(frames / 2) < diff) && diff <= (int)(frames / 2)) {
            int part[2];
            if (diff < 0) { part[0] = (int)(frames - last_pos); part[1] = (int)target; }
            else { part[0] = diff; part[1] = 0; }
            uint32_t pos = last_pos;
            for (int k = 0; k < 2; ++k) {
                if (!part[k]) continue;
                uint32_t a1, n1;
                OK(ds_buffer_lock(b, pos * 12, (uint32_t)part[k] * 12, 0, &a1, &n1, nullptr, nullptr));
                for (uint32_t i = 0; i < n1; i += 12) put6(mem.data(), (a1 + i) / 12, next++);
                OK(ds_buffer_unlock(b, a1, n1, 0, 0));
                pos = 0;
            }
            last_pos = target;
        }
        // Sleep until 10 ms after this poll started, like StartAddress
        double wake = t + 0.010, rest = wake - now_s();
        if (rest > 0) std::this_thread::sleep_for(std::chrono::duration<double>(rest));
    }
    DsStats st;
    OK(ds_device_get_stats(d, &st));
    std::vector<float> o;
    { std::lock_guard<std::mutex> g(om); o = out; }
    // After the zero-filled start the signal must be continuous and exact. If the first poll came late, the
    // mixer played zeros over the first frames, so find the stream by content: the first non-silent frame is
    // sig(j) for some j, and the stream started j frames before it.
    size_t k1 = 0, n = o.size() / 6, k0 = n;
    while (k1 < n && o[k1 * 6 + 1] == 0 && o[k1 * 6 + 2] == 0) ++k1;
    for (uint64_t j = 0; k1 < n && j < 2 * (uint64_t)frames && j <= k1; ++j) {
        bool m = true;
        for (int ch = 0; ch < 6 && m; ++ch) m = o[k1 * 6 + ch] == sig(ch, j) / 32768.0f;
        if (m) { k0 = k1 - j; break; }
    }
    if (k0 < n && k1 > k0) printf("  %s: the mixer took %zu frames of the zero-filled buffer before the first poll wrote them\n", label, k1 - k0);
    size_t limit = std::min<size_t>(n, k0 + (size_t)next - frames);  // the last lap may not have been played
    o.resize(std::max(limit, k1) * 6);
    size_t checked = 0, bad = 0;
    for (size_t k = k1; k0 < n && k < o.size() / 6; ++k, ++checked)
        for (int ch = 0; ch < 6; ++ch)
            if (o[k * 6 + ch] != sig(ch, k - k0) / 32768.0f) { ++bad; break; }
    // (exact = false: the output dropped mid-run, so the frames mixed while no device took them are gone by design)
    if (exact) CHECK(k0 < n && bad == 0 && checked > (seconds - 1.0) * rate, "%s: %zu bad of %zu", label, bad, checked);
    g_snd_max_stuck = max_stuck;
    CHECK(max_stuck < stuck_limit, "%s: write cursor stuck for %d polls (limit %d)", label, max_stuck, stuck_limit);
    CHECK(st.underruns == 0, "%s: %llu underruns", label, (unsigned long long)st.underruns);
    printf("  %s: %d Hz mix, %llu polls, write cursor unchanged for at most %d polls in a row (restart at 100), "
           "%zu frames bit-exact, %llu underruns\n",
           label, rate, (unsigned long long)polls, max_stuck, checked, (unsigned long long)st.underruns);
    ds_buffer_release(b);
    ds_buffer_release(p);
    ds_device_release(d);
}

// The Audio menu (ds_set_output_choice): the choice decides the mode of the next device config (and of the reopen the
// mixer starts, which reads the same table), and NFS_AUDIO overrides it. Config only: nothing is opened, nothing plays.
static void test_output_choice() {
    const char *env = getenv("NFS_AUDIO");
    std::string saved = env ? env : "";
    unsetenv("NFS_AUDIO");
    struct { const char *choice; DsSink sink; DsOutputMode mode; } want[] = {
        {"dd", DS_SINK_DEVICE, DS_OUT_DOLBY_DIGITAL}, {"pcm51", DS_SINK_DEVICE, DS_OUT_PCM51}, {"stereo", DS_SINK_DEVICE, DS_OUT_STEREO}};
    DsConfig c;
    for (auto &w : want) {
        ds_set_output_choice(w.choice);
        ds_config_default(&c);
        CHECK(c.sink == w.sink && c.mode == w.mode && !c.auto_mode, "choice %s: sink %d mode %d auto %d", w.choice, c.sink, c.mode, c.auto_mode);
    }
    ds_set_output_choice("auto");
    ds_config_default(&c);
    CHECK(c.sink == DS_SINK_DEVICE && c.auto_mode, "choice auto: sink %d auto %d", c.sink, c.auto_mode);
    ds_set_output_choice("stereo");
    setenv("NFS_AUDIO", "null", 1);
    ds_config_default(&c);
    CHECK(c.sink == DS_SINK_NULL, "NFS_AUDIO=null overrides the menu: sink %d", c.sink);
    ds_set_output_choice(nullptr);                  // no choice: back to automatic
    unsetenv("NFS_AUDIO");
    ds_config_default(&c);
    CHECK(c.auto_mode, "no choice: auto %d", c.auto_mode);
    if (env) setenv("NFS_AUDIO", saved.c_str(), 1);
}

// ------------------------------------------------------------------ fake device (dstest fake)
// Launch reopen (0g): on/off/paused each 62 checks, 0 failed. Removing the post-close default-device wait:
// 62 checks, 2 failed (interim stereo plus a second follow-up open). Receiver behavior has separate listening validation.
static std::vector<std::string> g_log;
static std::mutex g_log_m;
static void log_hook(const char *line) {
    std::lock_guard<std::mutex> g(g_log_m);
    g_log.push_back(line);
}
static size_t log_mark() { std::lock_guard<std::mutex> g(g_log_m); return g_log.size(); }
static int logged(const char *what, size_t from) {   // lines since `from` containing `what`
    std::lock_guard<std::mutex> g(g_log_m);
    int n = 0;
    for (size_t i = from; i < g_log.size(); ++i) n += g_log[i].find(what) != std::string::npos;
    return n;
}
static bool wait_logged(const char *what, size_t from, double seconds) {
    for (double t = 0; t < seconds; t += 0.02) {
        if (logged(what, from)) return true;
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    return logged(what, from) > 0;
}
struct GameDevice {   // what Snd builds: priority level, primary 6-ch 44.1 kHz, one looping 6-ch secondary
    DsDevice *d = nullptr;
    DsBuffer *p = nullptr, *b = nullptr;
    std::vector<uint8_t> mem;
    void create(bool play) {
        OK(ds_device_create(nullptr, &d));
        OK(ds_device_set_cooperative_level(d, DSSCL_PRIORITY));
        OK(ds_device_create_buffer(d, DSBCAPS_PRIMARYBUFFER, 0, nullptr, nullptr, &p));
        auto w = wfx(6, 44100, 16, 0x3F);
        OK(ds_buffer_set_format(p, w.data()));
        mem.assign(22048 * 12, 0);
        for (uint32_t f = 0; f < 22048; ++f) put6(mem.data(), f, f);
        OK(ds_device_create_buffer(d, DSBCAPS_GLOBALFOCUS | DSBCAPS_GETCURRENTPOSITION2, (uint32_t)mem.size(), w.data(), mem.data(), &b));
        if (play) OK(ds_buffer_play(b, DSBPLAY_LOOPING));
    }
    void release() {   // Snd's order: buffers, then the device
        ds_buffer_release(b);
        ds_buffer_release(p);
        ds_device_release(d);
        d = nullptr;
    }
    DsOutputMode mode(int *live) {
        DsOutputMode m;
        uint64_t u;
        ds_device_output_info(d, &m, live, &u);
        return m;
    }
};

static void test_fake_device() {
    printf("fake HDMI TV: the game re-creating its device keeps the output\n");
    ds_set_log(log_hook);
    unsetenv("NFS_AUDIO");
    int live = 0;
    // Start-up: the probe's device is released, the real one created right after (Snd FUN_008205c5 / FUN_00820762).
    size_t m0 = log_mark();
    GameDevice probe, game;
    probe.create(false);
    CHECK(probe.mode(&live) == DS_OUT_TRUEHD && live == 1, "auto on the fake TV: TrueHD (mode %d live %d)", probe.mode(&live), live);
    probe.release();
    game.create(true);
    CHECK(game.mode(&live) == DS_OUT_TRUEHD && live == 1, "start-up device: mode %d live %d", game.mode(&live), live);
    CHECK(logged("kept the output of device", m0) == 1, "start-up device kept the probe's output");
    // The trap the old code fell into: while the passthrough holds the TV, the automatic choice sees the speakers.
    DsConfig c;
    ds_config_default(&c);
    CHECK(c.auto_mode && c.mode == DS_OUT_STEREO, "while the TV is held, auto sees the speakers (mode %d)", c.mode);
    std::this_thread::sleep_for(std::chrono::milliseconds(500));
    DsStats st0;
    ds_device_get_stats(game.d, &st0);
    CHECK(st0.frames_written > 15000 && st0.underruns == 0, "game device mixes: %llu frames, %llu underruns",
          (unsigned long long)st0.frames_written, (unsigned long long)st0.underruns);
    // The runtime calls this from cursor polls with the guest pause state. Keep polling past the due time.
    const char *launch_env = getenv("NFS_AUDIO_LAUNCH_REOPEN");
    bool launch_on = !launch_env || strcmp(launch_env, "0");
    bool paused = getenv("DSTEST_LAUNCH_PAUSED") != nullptr;
    for (int i = 0; i < 110; ++i) {
        ds_device_launch_reopen_tick(game.d, paused);
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    int want = launch_on && !paused ? 1 : 0;
    CHECK(logged("one launch reopen:", m0) == want && logged("audio: launch reopen done", m0) == want,
          "launch reopen on=%d paused=%d: requests %d done %d, want %d", launch_on, paused,
          logged("one launch reopen:", m0), logged("audio: launch reopen done", m0), want);
    CHECK(logged("audio: launch reopen ", m0) == 1, "exactly one launch result");
    CHECK(logged("output now Stereo", m0) == 0 && logged("output now ", m0) == want,
          "launch has no interim stereo/follow-up open (stereo %d total %d)",
          logged("output now Stereo", m0), logged("output now ", m0));
    CHECK(game.mode(&live) == DS_OUT_TRUEHD && live == 1, "launch reopen keeps one live TrueHD output");
    // Snd::System::ReInit (the career load): the same again mid-session.
    size_t m1 = log_mark();
    game.release();
    // The mixer stopped, so its queue drains. Valid zero callbacks must keep the parked output healthy beyond
    // the 0.5 s stall threshold, throughout the 2 s parking window (consumption-only health reopened it here).
    std::this_thread::sleep_for(std::chrono::milliseconds(800));
    GameDevice career;
    career.create(true);
    CHECK(career.mode(&live) == DS_OUT_TRUEHD && live == 1, "after ReInit: mode %d live %d (was stereo on the speakers)", career.mode(&live), live);
    CHECK(logged("kept the output of device", m1) == 1 && logged("media: fake device: open", m1) == 0,
          "after ReInit: output kept, not reopened");
    std::this_thread::sleep_for(std::chrono::milliseconds(500));
    DsStats st1;
    ds_device_get_stats(career.d, &st1);
    CHECK(st1.frames_written > 15000 && st1.underruns == 0, "after ReInit: %llu frames, %llu underruns",
          (unsigned long long)st1.frames_written, (unsigned long long)st1.underruns);
    ds_device_launch_reopen_tick(career.d, 0);
    CHECK(logged("one launch reopen:", m0) == want && logged("audio: launch reopen ", m0) == 1,
          "ReInit does not rearm the launch reopen");
    // No device follows: the parked output is closed after 2 s, and the TV is the default again.
    size_t m2 = log_mark();
    career.release();
    CHECK(wait_logged("parked output closed: no device took it", m2, 3.0), "parked output closed after 2 s");
    std::this_thread::sleep_for(std::chrono::milliseconds(400));
    ds_config_default(&c);
    CHECK(c.mode == DS_OUT_TRUEHD, "TV back as the default after the close (mode %d)", c.mode);

    printf("fake HDMI TV: PCM 5.1 keeps its rate across the re-creation\n");
    setenv("NFS_AUDIO", "pcm51", 1);
    size_t m3 = log_mark();
    GameDevice p1, p2;
    p1.create(true);
    CHECK(logged("reopened at the game's rate", m3) == 1, "pcm51: reopened once at 44.1 kHz");
    p1.release();
    size_t m4 = log_mark();
    p2.create(true);
    CHECK(logged("kept the output of device", m4) == 1 && logged("reopened", m4) == 0 && logged("44100 Hz", m4) == 1,
          "pcm51: kept at 44100 Hz, no reopen on SetFormat");
    // A device in another mode doesn't take it.
    p2.release();
    setenv("NFS_AUDIO", "stereo", 1);
    size_t m5 = log_mark();
    GameDevice s1;
    s1.create(true);
    CHECK(logged("wants another mode", m5) == 1 && s1.mode(&live) == DS_OUT_STEREO && live == 1, "stereo: parked PCM 5.1 closed, stereo opened");
    s1.release();
    unsetenv("NFS_AUDIO");
    std::this_thread::sleep_for(std::chrono::milliseconds(2500));

    printf("fake HDMI TV: the device going away (TV asleep) is followed\n");
    setenv("NFS_AUDIO_TEST_DROP", "0.5/2", 1);
    size_t m6 = log_mark();
    GameDevice t;
    t.create(true);
    unsetenv("NFS_AUDIO_TEST_DROP");
    CHECK(wait_logged("stopped taking audio", m6, 3.0), "output drop detected");
    CHECK(wait_logged("output now Dolby TrueHD", m6, 8.0), "back on TrueHD after the refused opens");
    DsStats a, b;
    ds_device_get_stats(t.d, &a);
    std::this_thread::sleep_for(std::chrono::milliseconds(300));
    ds_device_get_stats(t.d, &b);
    CHECK(t.mode(&live) == DS_OUT_TRUEHD && live == 1 && b.frames_written > a.frames_written, "mixing on the TV again (mode %d live %d)",
          t.mode(&live), live);
    t.release();
    ds_set_log(nullptr);
    std::lock_guard<std::mutex> g(g_log_m);
    for (auto &l : g_log) printf("    | %s\n", l.c_str());
}

// Old public issue #8 (start-up crash with an HDMI monitor as the output): the game's Snd thread restarts DirectSound when
// the write cursor stays put for 100 polls (10 ms each, ~1 s), and the old port crashed in that restart. Here the
// output stalls at start-up (0.3 s after creation, then 3 refused reopens at the 1 s retry: >= 3 s without a device)
// while the Snd replica streams; the host must detach and keep the cursors moving, so the longest streak stays far
// below the restart: limit 60 polls. Numbers (2026-10-26, 5 s runs): fixed 4.0 34-36 polls, PASS; broken control (detach after
// 5.0 s instead of 0.5 s, build.sh's dstest_snddrop_broken) 334-343 polls, FAIL. The device must also come back.
static void test_fake_snd_drop() {
    printf("fake HDMI TV: the output stalls at start-up while the game's Snd streamer runs (old issue #8)\n");
    ds_set_log(log_hook);
    size_t m = log_mark();
    setenv("NFS_AUDIO_TEST_DROP", "0.3/3", 1);
    test_snd_like(DS_SINK_DEVICE, "snd fake-TV stall", 5.0, 60, false);
    unsetenv("NFS_AUDIO_TEST_DROP");
    CHECK(logged("stopped taking audio", m) >= 1, "snd stall: the stall was injected and detected");
    CHECK(logged("output now", m) >= 1, "snd stall: the output came back after the refused reopens");
    printf("  snd stall: longest unchanged write cursor %d polls (limit 60, the game restarts at 100)\n", g_snd_max_stuck);
    ds_set_log(nullptr);
}

static void test_fake_launch_mode(const char *choice) {
    ds_set_log(log_hook);
    bool pcm = !strcmp(choice, "pcm51");
    DsOutputMode expected = pcm ? DS_OUT_PCM51 : !strcmp(choice, "dd") ? DS_OUT_DOLBY_DIGITAL : DS_OUT_DOLBY_DIGITAL_PLUS;
    setenv("NFS_AUDIO", choice, 1);
    GameDevice p;
    p.create(true);
    size_t mark = log_mark();
    for (int i = 0; i < 110; ++i) {
        ds_device_launch_reopen_tick(p.d, 0);
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    int live = 0;
    CHECK(p.mode(&live) == expected && live == 1, "%s remains live in the same format", choice);
    CHECK(logged("one launch reopen:", 0) == !pcm && logged("output now ", mark) == !pcm,
          "%s: launch requests %d opens %d", choice, logged("one launch reopen:", 0), logged("output now ", mark));
    CHECK(logged(pcm ? "audio: launch reopen skipped:pcm" : "audio: launch reopen done", 0) == 1,
          "%s logs one result", choice);
    p.release();
    ds_set_log(nullptr);
}

int main(int argc, char **argv) {
    bool device = argc > 1 && std::string(argv[1]) == "device";
    if (argc > 1 && std::string(argv[1]) == "fake") {
        setenv("NFS_AUDIO_FAKE_DEVICE", "1", 1);   // before any media call: nothing reaches CoreAudio
        if (argc > 2 && std::string(argv[2]) == "pcm") test_fake_launch_mode("pcm51");
        else if (argc > 2 && (std::string(argv[2]) == "dd" || std::string(argv[2]) == "ddp")) test_fake_launch_mode(argv[2]);
        else if (argc > 2 && std::string(argv[2]) == "snddrop") test_fake_snd_drop();
        else { test_fake_device(); test_fake_snd_drop(); }
        printf("%d checks, %d failed\n", g_checks, g_fail);
        return g_fail ? 1 : 0;
    }
    ds_set_peak_limiter(0); // Existing cursor/sample expectations have no look-ahead; fake above exercises default On.
    test_formats();
    test_locks();
    test_manual_stream();
    test_volume_pan();
    test_resample();
    test_oneshot_notify();
    test_mixer_cost();
    test_auto_rate();
    printf("real-time streaming, null sink (3 s)\n");
    test_realtime(DS_SINK_NULL, DS_OUT_PCM51, "null", 3.0);
    printf("the game's Snd streamer, replicated (null sink, 3 s)\n");
    test_snd_like(DS_SINK_NULL, "snd null", 3.0);
    test_threads();
    test_output_choice();
    if (device) {
        printf("real-time streaming on the CoreAudio device, muted\n");
        test_realtime(DS_SINK_DEVICE, DS_OUT_PCM51, "device pcm51", 3.0);
        test_realtime(DS_SINK_DEVICE, DS_OUT_STEREO, "device stereo", 1.5);
        test_realtime(DS_SINK_DEVICE, DS_OUT_DOLBY_DIGITAL, "device dd", 1.5);
        printf("the game's Snd streamer on the CoreAudio device, muted (3 s)\n");
        test_snd_like(DS_SINK_DEVICE, "snd device", 3.0);
    }
    printf("%d checks, %d failed\n", g_checks, g_fail);
    return g_fail ? 1 : 0;
}
#endif
