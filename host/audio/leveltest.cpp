// leveltest.cpp - offline check of the mixer's per-channel level stats (ds_device_get_stats: rms, peak, clipped,
// dip = quietest 50 ms stretch), which perf.tsv's rms_/peak_/clip/dip_ columns report and which decide "game vs
// TV/receiver" for a rear cutout. No device: a manual-clock DirectSound device mixes synthetic 6-ch 16-bit buffers.
//   build/leveltest           (a) 1 s of -20 dBFS noise on all six channels: dip_sl/dip_sr ~ -20 dBFS
//                             (b) the same with SR silent for 100 ms mid-second: dip_sr <= -90, dip_sl ~ -20
//                             (c) 37 C samples at +32767 and 5 FL samples at -32768: clipped exactly 37 / 5
//   build/leveltest --broken  runs build/leveltest_broken: the same test linked against a copy of dsound_host.cpp
//                             whose sub-window is 24000 frames (500 ms) instead of 2400 (build.sh makes it); (b) must fail.
// Run by host/audio/build.sh (a failure fails the build, and the gate's host build with it).
// Numbers (2026-10-23): pass: (a) dip SL -20.2 SR -20.1, (b) dip SR -120.0 SL -20.2 (rms SR -20.5), (c) clipped
//                       C 37 FL 5, peak C 0.0; 21 checks, 0 failed. broken: (b) dip SR -21.0 (the 100 ms gap averages
//                       into a 500 ms stretch) -> "FAIL: (b) dip SR -21.0 dBFS <= -90", 21 checks, 1 failed, exit 1.
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <string>
#include <unistd.h>
#include <vector>

#include "dsound_host.h"

static int checks = 0, failed = 0;
static void check(bool ok, const char *what) {
    ++checks;
    if (!ok) { ++failed; printf("FAIL: %s\n", what); }
}
static double db(double x) { return x > 1e-6 ? 20 * std::log10(x) : -120.0; }

static std::vector<uint8_t> wfx6() {  // WAVEFORMATEXTENSIBLE, 6 ch, 48 kHz, 16-bit PCM, mask 0x3F
    std::vector<uint8_t> w(40, 0);
    auto p16 = [&](int o, uint16_t v) { w[o] = (uint8_t)v; w[o + 1] = (uint8_t)(v >> 8); };
    auto p32 = [&](int o, uint32_t v) { for (int i = 0; i < 4; ++i) w[o + i] = (uint8_t)(v >> (8 * i)); };
    p16(0, 0xFFFE); p16(2, 6); p32(4, 48000); p32(8, 48000 * 12); p16(12, 12); p16(14, 16);
    p16(16, 22); p16(18, 16); p32(20, 0x3F);
    static const uint8_t g[16] = {1, 0, 0, 0, 0, 0, 0x10, 0, 0x80, 0, 0, 0xAA, 0, 0x38, 0x9B, 0x71};
    memcpy(&w[24], g, 16);
    return w;
}

// One second (48000 frames) of uniform noise at -20 dBFS RMS (amplitude 0.1 * sqrt(3)) on every channel; `edit`
// may change samples (channel order FL FR C LFE SL SR). Mixed on a fresh manual device; returns that second's stats.
template <class F> static DsStats run(F edit) {
    DsConfig c;
    ds_config_default(&c);
    c.sink = DS_SINK_MANUAL;
    c.block_frames = 256;
    DsDevice *d = nullptr;
    if (ds_device_create(&c, &d) != DS_OK) { printf("FAIL: device\n"); exit(2); }
    const uint32_t frames = 48000;
    std::vector<int16_t> pcm((size_t)frames * 6);
    std::mt19937 rng(7);
    std::uniform_real_distribution<double> u(-0.1 * std::sqrt(3.0), 0.1 * std::sqrt(3.0));
    for (auto &s : pcm) s = (int16_t)std::lrint(u(rng) * 32768.0);
    edit(pcm);
    std::vector<uint8_t> mem((uint8_t *)pcm.data(), (uint8_t *)pcm.data() + pcm.size() * 2);
    auto w = wfx6();
    DsBuffer *b = nullptr;
    if (ds_device_create_buffer(d, DSBCAPS_GETCURRENTPOSITION2, (uint32_t)mem.size(), w.data(), mem.data(), &b) != DS_OK) {
        printf("FAIL: buffer\n");
        exit(2);
    }
    DsStats st;
    ds_device_get_stats(d, &st);  // start the window here
    ds_buffer_play(b, 0);         // one-shot: exactly the second above
    std::vector<float> out(256 * 6);
    for (uint32_t done = 0; done < frames; done += 256) ds_device_render(d, out.data(), std::min<uint32_t>(256, frames - done));
    ds_device_get_stats(d, &st);
    ds_buffer_release(b);
    ds_device_release(d);
    return st;
}

static void show(const char *name, const DsStats &s) {
    printf("%s: window %llu frames\n  rms ", name, (unsigned long long)s.window);
    for (int k = 0; k < 6; ++k) printf(" %6.1f", db(s.rms[k]));
    printf("\n  peak");
    for (int k = 0; k < 6; ++k) printf(" %6.1f", db(s.peak[k]));
    printf("\n  dip ");
    for (int k = 0; k < 6; ++k) printf(" %6.1f", db(s.dip[k]));
    printf("\n  clipped");
    for (int k = 0; k < 6; ++k) printf(" %llu", (unsigned long long)s.clipped[k]);
    printf("\n");
}

int main(int argc, char **argv) {
    ds_set_peak_limiter(0); // This probe measures the legacy full-scale/dip counters; limitertest covers On.
    if (argc > 1 && !strcmp(argv[1], "--broken")) {
        std::string p = std::string(argv[0]) + "_broken";
        char *args[] = {(char *)p.c_str(), nullptr};
        execv(p.c_str(), args);
        perror(p.c_str());
        return 2;
    }
    enum { FL, FR, C, LFE, SL, SR };
    char m[160];

    DsStats a = run([](std::vector<int16_t> &) {});
    show("(a) all channels -20 dBFS noise, 1 s", a);
    for (int k : {SL, SR}) {
        snprintf(m, sizeof m, "(a) dip ch%d %.1f dBFS within 1 dB of -20", k, db(a.dip[k]));
        check(std::fabs(db(a.dip[k]) + 20) < 1.0, m);
        snprintf(m, sizeof m, "(a) rms ch%d %.1f dBFS within 0.5 dB of -20", k, db(a.rms[k]));
        check(std::fabs(db(a.rms[k]) + 20) < 0.5, m);
    }
    check(a.window == 48000, "(a) window 48000 frames");
    for (int k = 0; k < 6; ++k) check(a.clipped[k] == 0, "(a) no full-scale samples");

    DsStats b = run([](std::vector<int16_t> &p) {
        for (size_t f = 24000; f < 24000 + 4800; ++f) p[f * 6 + SR] = 0;  // SR silent 500-600 ms
    });
    show("(b) as (a), SR silent for 100 ms mid-second", b);
    snprintf(m, sizeof m, "(b) dip SR %.1f dBFS <= -90", db(b.dip[SR]));
    check(db(b.dip[SR]) <= -90, m);
    snprintf(m, sizeof m, "(b) dip SL %.1f dBFS within 1 dB of -20", db(b.dip[SL]));
    check(std::fabs(db(b.dip[SL]) + 20) < 1.0, m);
    snprintf(m, sizeof m, "(b) rms SR %.1f dBFS within 1 dB of -20.5 (90%% of the power)", db(b.rms[SR]));
    check(std::fabs(db(b.rms[SR]) + 20.46) < 1.0, m);

    DsStats c = run([](std::vector<int16_t> &p) {
        for (int i = 0; i < 37; ++i) p[(size_t)(1000 + 977 * i) * 6 + C] = 32767;
        for (int i = 0; i < 5; ++i) p[(size_t)(3000 + 5003 * i) * 6 + FL] = -32768;
    });
    show("(c) 37 C samples at +32767, 5 FL samples at -32768", c);
    snprintf(m, sizeof m, "(c) clipped C %llu == 37", (unsigned long long)c.clipped[C]);
    check(c.clipped[C] == 37, m);
    snprintf(m, sizeof m, "(c) clipped FL %llu == 5", (unsigned long long)c.clipped[FL]);
    check(c.clipped[FL] == 5, m);
    for (int k : {FR, LFE, SL, SR}) check(c.clipped[k] == 0, "(c) other channels 0");
    snprintf(m, sizeof m, "(c) peak C %.2f dBFS >= -0.01", db(c.peak[C]));
    check(db(c.peak[C]) >= -0.01, m);

    printf("%d checks, %d failed\n", checks, failed);
    return failed ? 1 : 0;
}
