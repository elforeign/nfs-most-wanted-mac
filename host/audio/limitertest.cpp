// Offline/manual sink only. Verified 2026-10-24, 48 kHz: 41 checks, 0 failed; --broken: 18 failed, exit 1.
// +6 dB noise: On -1.000000 dBFS / 0 clips; Off 0 dBFS / 143403 clips; broken 0 dBFS / 87 clips.
// +6 dB transients: On -1.000000 dBFS / 0 clips; Off and broken 0 dBFS / 2 clips.
// Quiet: exact delayed bytes, 0 limited samples at 22050/44100/48000/96000/192000 Hz.
// Off vs unmodified HEAD: 3456000 bytes equal, SHA256 9399c7f8c4fce7e635227925d0705145a5eb90563e8d9640ac1a9113eda0d3f0.
// Limiter + block copy: 1.938 us/256 frames (0.036% of the 48 kHz budget); timing is informational.
// --broken runs the same checks against a build with the audio delay removed; it MUST fail.
// --dump-off PATH also runs against the unmodified HEAD mixer to prove byte-identical bypass.
#include "dsound_host.h"
#include "peak_limiter.h"
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <unistd.h>

#ifdef NFS_LIMITER_REFERENCE
extern "C" void ds_set_peak_limiter(int) {} // dump-only binary linked to the unmodified HEAD mixer
extern "C" void ds_set_movie_playing(int) {}
#endif

static int checks, failed;
static void check(bool ok, const char *what) {
    ++checks;
    if (!ok) { ++failed; printf("FAIL: %s\n", what); }
}
static double db(float x) { return x > 0 ? 20 * std::log10(x) : -120; }
static float peak(const std::vector<float> &p) {
    float m = 0;
    for (float x : p) m = std::max(m, std::fabs(x));
    return m;
}
static std::vector<float> signal(int kind, int rate = 48000) {
    std::vector<float> p(rate * 6, 0);
    uint32_t rng = 7;
    for (int f = 0; f < rate; ++f) for (int c = 0; c < 6; ++c) {
        rng = rng * 1664525u + 1013904223u;
        float noise = ((int32_t)(rng >> 8) - 8388608) / 8388608.0f;
        p[f * 6 + c] = noise * (kind == 0 ? 1.995262315f : 0.1f);
    }
    if (kind == 1) { // isolated +6 dB impulse in C, with nonzero witness channels
        p[12347 * 6 + 2] = 1.995262315f;
        p[256 * 6] = -1.995262315f; // exact block boundary, opposite sign
    }
    return p;
}
struct Fixture {
    DsDevice *d = nullptr;
    DsBuffer *b[2] = {};
    std::vector<float> half;
    Fixture(const std::vector<float> &input, int rate = 48000, int block = 256) : half(input) {
        DsConfig c{}; // Do not query/open a device, even to choose an automatic output.
        c.sink = DS_SINK_MANUAL; c.mode = DS_OUT_PCM51; c.mix_rate = rate; c.block_frames = block;
        if (ds_device_create(&c, &d)) std::abort();
        uint8_t w[18] = {};
        auto w16 = [&](int i, uint16_t v) { w[i] = v; w[i + 1] = v >> 8; };
        auto w32 = [&](int i, uint32_t v) { for (int k = 0; k < 4; ++k) w[i + k] = v >> (8 * k); };
        w16(0, 3); w16(2, 6); w32(4, rate); w32(8, rate * 24); w16(12, 24); w16(14, 32);
        for (auto &v : half) v *= 0.5f;
        // Two sub-full-scale buffers sum to +6 dB: exercise the actual accumulation/clamp boundary.
        for (auto &buf : b) {
            if (ds_device_create_buffer(d, 0, half.size() * sizeof(float), w, (uint8_t *)half.data(), &buf)) std::abort();
            ds_buffer_play(buf, 0);
        }
    }
    ~Fixture() { for (auto buf : b) ds_buffer_release(buf); ds_device_release(d); }
    std::vector<float> render(size_t frames) {
        std::vector<float> out(frames * 6);
        if (ds_device_render(d, out.data(), frames)) std::abort();
        return out;
    }
};
static uint64_t clips(const DsStats &s) {
    uint64_t n = 0; for (auto c : s.clipped) n += c; return n;
}
static void proof(int kind) {
    auto in = signal(kind);
    ds_set_peak_limiter(1);
    Fixture on(in);
    auto limited = on.render(in.size() / 6 + 96);
    DsStats a{}; ds_device_get_stats(on.d, &a);
    ds_set_peak_limiter(0);
    Fixture off(in);
    auto bypass = off.render(in.size() / 6);
    DsStats b{}; ds_device_get_stats(off.d, &b);
    printf("%s: On peak %.6f dBFS clip %llu limited %llu; Off peak %.6f clip %llu\n",
           kind == 0 ? "noise +6 dB" : "transients +6 dB", db(peak(limited)), clips(a), a.limited,
           db(peak(bypass)), clips(b));
    check(db(peak(limited)) <= -0.9, "limited peak <= -0.9 dBFS");
    check(clips(a) == 0, "On zero full-scale/clamped samples");
    check(a.limited > 0, "limiter counter sees gain reduction");
    check(clips(b) > 0 && peak(bypass) == 1, "Off clips as before");
    check(b.limited == 0, "Off no gain reduction");
    for (auto &v : in) v = std::min(1.0f, std::max(-1.0f, v));
    check(!memcmp(in.data(), bypass.data(), in.size() * sizeof(float)), "Off byte-exact original clamp");
    DsStats empty{}; ds_device_get_stats(on.d, &empty);
    check(empty.limited == 0 && clips(empty) == 0 && empty.window == 0, "stats reset together");
}
static void quiet_and_link() {
    for (int rate : {22050, 44100, 48000, 96000, 192000}) {
        auto in = signal(2, rate);
        int delay = rate / 500;
        ds_set_peak_limiter(1);
        Fixture f(in, rate, 17);
        auto out = f.render(in.size() / 6 + delay);
        check(!memcmp(out.data() + delay * 6, in.data(), in.size() * sizeof(float)), "quiet exactly unity after delay");
        check(std::all_of(out.begin(), out.begin() + delay * 6, [](float v) { return v == 0; }), "delay starts with zeros");
        DsStats st{}; ds_device_get_stats(f.d, &st);
        check(st.limited == 0, "quiet never reduces gain");
        printf("quiet %d Hz: delay %d frames (%.4f ms), limited %llu\n", rate, delay, 1000.0 * delay / rate, st.limited);
    }
    std::vector<float> in(48000 * 6);
    const float ratios[6] = {1, -0.5, 0.25, -0.125, 0.0625, -0.03125};
    for (int f = 0; f < 48000; ++f) for (int c = 0; c < 6; ++c)
        in[f * 6 + c] = (f >= 2000 && f < 2200 ? 2.0f : 0.1f) * ratios[c];
    Fixture f(in);
    auto out = f.render(48096);
    bool linked = true;
    for (size_t i = 0; i < out.size(); i += 6) for (int c = 1; c < 6; ++c)
        linked &= out[i + c] == out[i] * ratios[c];
    check(linked, "one exact gain for all channels, signs preserved");
    check(out[(2200 + 96) * 6] < 0.1f, "release retains gain just after loud input");
    check(out[(2200 + 96 + 7200) * 6] == 0.1f, "release reaches exact unity in 150 ms");
    auto noise = signal(0);
    Fixture a(noise, 48000, 1), b(noise, 48000, 1536);
    auto x = a.render(48096), y = b.render(48096);
    check(x == y, "block partition independent (1 vs 1536 frames)");
    // Live disable must bypass the queued delay immediately; live enable starts a fresh delay line.
    Fixture live(in);
    live.render(1000);
    ds_set_peak_limiter(0);
    auto off = live.render(100);
    check(!memcmp(off.data(), in.data() + 1000 * 6, off.size() * sizeof(float)), "live Off bypasses delay exactly");
    ds_set_peak_limiter(1);
    auto on = live.render(196);
    check(std::all_of(on.begin(), on.begin() + 96 * 6, [](float v) { return v == 0; }), "live On clears stale delay");
    check(!memcmp(on.data() + 96 * 6, in.data() + 1100 * 6, 100 * 6 * sizeof(float)), "live On fresh delayed input");
    ds_set_movie_playing(1);
    auto movie_input = signal(1);
    Fixture movie(movie_input);
    auto movie_out = movie.render(48000);
    for (auto &v : movie_input) v = std::min(1.0f, std::max(-1.0f, v));
    check(!memcmp(movie_out.data(), movie_input.data(), movie_out.size() * sizeof(float)), "movie bypass is byte-identical, no delay");
    DsStats movie_stats{}; ds_device_get_stats(movie.d, &movie_stats);
    check(movie_stats.limited == 0 && clips(movie_stats) == 2, "movies retain original clamp and never limit");
    ds_set_peak_limiter(0); ds_set_peak_limiter(1); // F10 during a movie must not defeat its bypass.
    Fixture movie2(movie_input);
    check(movie2.render(48000) == movie_input, "F10 On during a movie still bypasses");
    ds_set_movie_playing(0);
    Fixture game(signal(0));
    check(db(peak(game.render(48096))) <= -0.9, "limiting resumes after movie");
}
static void benchmark() {
    auto source = signal(0);
    PeakLimiter p; p.reset(48000);
    std::vector<float> block(256 * 6);
    auto begin = std::chrono::steady_clock::now();
    double checksum = 0;
    const int blocks = 20000;
    for (int i = 0; i < blocks; ++i) {
        memcpy(block.data(), source.data() + (i % 180) * 256 * 6, block.size() * sizeof(float));
        p.process(block.data(), 256);
        checksum += block[0];
    }
    double us = std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - begin).count() / blocks;
    printf("CPU: %.3f us/256-frame block (%.3f%% of 5.333 ms), 20000 blocks, checksum %.6f\n", us, us / 53.33333, checksum);
}
int main(int argc, char **argv) {
    // Explicit guard even though these tests only construct manual sinks.
    setenv("NFS_AUDIO", "null", 1);
    setenv("NFS_AUDIO_FAKE_DEVICE", "1", 1);
    if (argc == 2 && !strcmp(argv[1], "--broken")) {
        std::string name = std::string(argv[0]) + "_broken";
        char *args[] = {(char *)name.c_str(), nullptr}; execv(name.c_str(), args); return 2;
    }
    if (argc == 3 && !strcmp(argv[1], "--dump-off")) {
        ds_set_peak_limiter(0);
        FILE *file = fopen(argv[2], "wb"); if (!file) return 2;
        for (int kind : {0, 1, 2}) {
            auto in = signal(kind); Fixture f(in); auto out = f.render(48000);
            if (fwrite(out.data(), sizeof(float), out.size(), file) != out.size()) return 2;
        }
        return fclose(file) ? 2 : 0;
    }
    // No setter before this first render: prove startup environment/default behavior.
    {
        auto in = signal(0); Fixture f(in); auto out = f.render(48096);
        bool off = getenv("NFS_AUDIO_LIMITER") && !strcmp(getenv("NFS_AUDIO_LIMITER"), "off");
        check(off ? peak(out) == 1.0f : db(peak(out)) <= -0.9, "startup default/override");
    }
    proof(0); proof(1); quiet_and_link(); benchmark();
    printf("%d checks, %d failed\n", checks, failed);
    return failed ? 1 : 0;
}
