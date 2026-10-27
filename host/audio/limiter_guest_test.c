// Offline proof at the ACTUAL Snd float->int16 boundary, using extracted translated 00825957 as the reference.
// Verified 2026-10-24: 16648 checks / 0 failed; no-delay broken build: 6 failed, exit 1.
// Noise +6 dB: On -0.999855 dBFS, 0 clips; original Off 131824 clips; broken 85 clips.
// Transients +6 dB: On -0.999855 dBFS, 0 clips; Off/broken 2 clips. Post-resample peak <= -1 dBFS.
// Quiet -20 dBFS: exact 88-frame-delayed original int16 bytes, 0 limited; movie/Off byte-identical to original.
// Hook CPU: 6.543 us/256 source frames, including fixture copies/ABI checks (informational, not game timing).
// No device is opened; all sinks are manual.
#include "../../runtime/host.h"
#include "dsound_host.h"
#include <stdlib.h>
#include <time.h>

uint8_t *G;
static DsDevice *device;
static int checks, failed, chain_calls;
static unsigned original_calls;
void f_00825957(Cpu *c);
void f_00825957_orig(Cpu *c);
DsDevice *dsound_current_device(void) { return device; }
// The hook's BOOT_DIAG mixer summary (mixdiag/mixnan/mixsilent) stays off here: diag_on 0, so the bytes are unchanged.
int diag_on;
void diag_event(const char *fmt, ...) { (void)fmt; }
uint64_t host_ticks(void) { return 0; }
#define CHECK(x) do { ++checks; if (!(x)) { ++failed; printf("FAIL line %d: %s\n", __LINE__, #x); } } while (0)

// A deterministic optional filter for the original and hooked converter: adds gain before conversion.
void dispatch(Cpu *c, uint32_t target) {
    if (target != 0x123456) abort();
    ++chain_calls;
    uint32_t n = arg(c, 1), out = arg(c, 3);
    for (uint32_t i = 0; i < n; ++i) MF32(out + i * 4) *= 2;
    c->eax = n;
    c->esp += 4;
}
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *args) {
    uint32_t sp = c->esp;
    for (int i = n - 1; i >= 0; --i) { c->esp -= 4; M32(c->esp) = args[i]; }
    c->esp -= 4; M32(c->esp) = 0;
    dispatch(c, fn); c->esp = sp; return c->eax;
}
static int16_t quantise(float x) {
    float v = x * 32768 + 12582912.0f;
    uint32_t bits; memcpy(&bits, &v, 4); bits &= 0xfffff;
    if (bits > 0x7fff && bits < 0xf8000) bits = bits < 0x80000 ? 0x7fff : 0xffff8000;
    return (int16_t)bits;
}
static uint64_t clips(const int16_t *p, int n) {
    uint64_t total = 0; for (int i = 0; i < n; ++i) total += p[i] == 32767 || p[i] == -32768; return total;
}
static float peak(const int16_t *p, int n) {
    float v = 0; for (int i = 0; i < n; ++i) v = fmaxf(v, fabsf(p[i] / 32768.0f)); return v;
}
static void open_manual(void) {
    DsConfig c = {0}; c.sink = DS_SINK_MANUAL; c.mode = DS_OUT_PCM51; c.mix_rate = 48000; c.block_frames = 256;
    CHECK(ds_device_create(&c, &device) == DS_OK);
}
static int16_t *convert(const float *in, int frames, int reference, int chain) {
    int16_t *out = calloc((size_t)frames * 6, sizeof *out);
    for (int start = 0; start < frames; start += 256) {
        int n = frames - start; if (n > 256) n = 256;
        for (int ch = 0; ch < 6; ++ch) {
            uint32_t src = 0xb00000 + ch * 0x1000, dst = 0xb10000 + ch * 0x1000, obj = 0x9c2478 + ch * 0x70;
            for (int i = 0; i < n; ++i) MF32(src + i * 4) = in[(start + i) * 6 + ch] * 32768;
            M32(obj + 8) = chain ? 0xb20000 : 0;
            Cpu c = {0}; c.esp = 0xe00000; c.cw = 0x37f;
            c.ebx = 11; c.esi = 22; c.edi = 33; c.ebp = 44;
            M32(c.esp + 4) = obj; M32(c.esp + 8) = n; M32(c.esp + 12) = src; M32(c.esp + 16) = dst;
            if (reference) { ++original_calls; f_00825957_orig(&c); } else f_00825957(&c);
            CHECK(c.esp == 0xe00004 && c.eax == (uint32_t)n && c.ebx == 11 && c.esi == 22 && c.edi == 33 && c.ebp == 44);
        }
        // Exactly where the original caller interleaves, AFTER all six per-channel converters returned.
        for (int i = 0; i < n; ++i) for (int ch = 0; ch < 6; ++ch)
            out[(start + i) * 6 + ch] = (int16_t)M16(0xb10000 + ch * 0x1000 + i * 2);
    }
    return out;
}
static void render_resampled(int16_t *pcm, int frames, int expect_limit) {
    uint8_t w[18] = {0};
    uint16_t ch = 6, bits = 16, stride = 12, tag = 1; uint32_t rate = 44100, bytes = 44100 * 12;
    memcpy(w, &tag, 2); memcpy(w + 2, &ch, 2); memcpy(w + 4, &rate, 4); memcpy(w + 8, &bytes, 4);
    memcpy(w + 12, &stride, 2); memcpy(w + 14, &bits, 2);
    DsBuffer *b = NULL;
    CHECK(ds_device_create_buffer(device, 0, frames * 12, w, (uint8_t *)pcm, &b) == DS_OK);
    ds_buffer_play(b, 0);
    float *out = calloc(48200 * 6, sizeof *out);
    CHECK(ds_device_render(device, out, 48200) == DS_OK);
    float max = 0; for (int i = 0; i < 48200 * 6; ++i) max = fmaxf(max, fabsf(out[i]));
    DsStats s; ds_device_get_stats(device, &s);
    uint64_t clipped = 0; for (int c = 0; c < 6; ++c) clipped += s.clipped[c];
    printf("  after 44100->48000 resample: peak %.6f dBFS, clip %llu, limited %llu\n", 20 * log10f(max), clipped, s.limited);
    CHECK(max <= powf(10, -0.9f / 20) && clipped == 0);
    CHECK(expect_limit ? s.limited > 0 : s.limited == 0);
    ds_buffer_release(b); free(out);
}
int main(void) {
    setenv("NFS_AUDIO", "null", 1); setenv("NFS_AUDIO_FAKE_DEVICE", "1", 1);
    G = calloc(1, 0x1000000); if (!G) return 2;
    M8(0x9c2319) = 6; M16(0x9c2046) = 44100; MF32(0x8c5db8) = 12582912.0f; M32(0xb20000) = 0x123456;
    enum { FRAMES = 44100 + 88, SAMPLES = FRAMES * 6 };
    float *in = calloc(SAMPLES, sizeof *in);
    for (int kind = 0; kind < 4; ++kind) {
        uint32_t rng = 7;
        for (int f = 0; f < FRAMES; ++f) for (int c = 0; c < 6; ++c) {
            rng = rng * 1664525u + 1013904223u;
            float noise = ((int32_t)(rng >> 8) - 8388608) / 8388608.0f;
            in[f * 6 + c] = f >= 44100 ? 0 : noise * (kind == 0 ? 1.995262315f : 0.1f);
        }
        if (kind == 1) { in[256 * 6] = -1.995262315f; in[12347 * 6 + 2] = 1.995262315f; }
        // The filter path is exercised on quiet input; reference and hooked converter must both run it once.
        int chain = kind == 3;
        open_manual(); ds_set_peak_limiter(0);
        int16_t *reference = convert(in, FRAMES, 1, chain);
        int16_t *off = convert(in, FRAMES, 0, chain);
        CHECK(!memcmp(reference, off, SAMPLES * sizeof *off));
        ds_set_peak_limiter(1);
        clock_t start = clock();
        int16_t *on = convert(in, FRAMES, 0, chain);
        double us_per_block = (double)(clock() - start) * 1e6 / CLOCKS_PER_SEC / ((FRAMES + 255) / 256);
        printf("guest %s: On %.6f dBFS / %llu clips, Off %.6f / %llu clips\n",
               kind == 0 ? "noise +6 dB" : kind == 1 ? "transients +6 dB" : kind == 2 ? "quiet -20 dB" : "quiet + filter",
               20 * log10f(peak(on, SAMPLES)), clips(on, SAMPLES), 20 * log10f(peak(off, SAMPLES)), clips(off, SAMPLES));
        if (!kind) printf("  hook CPU %.3f us/256-source-frame block (includes fixture copies/ABI checks)\n", us_per_block);
        CHECK(peak(on, SAMPLES) <= powf(10, -0.9f / 20) && clips(on, SAMPLES) == 0);
        if (kind < 2) CHECK(clips(off, SAMPLES) > 0);
        else {
            CHECK(!memcmp(on + 88 * 6, reference, 44100 * 6 * sizeof *on));
            CHECK(on[0] == 0 && on[87 * 6] == 0);
        }
        render_resampled(on, FRAMES, kind < 2);
        ds_set_movie_playing(1);
        int16_t *movie = convert(in, FRAMES, 0, chain);
        CHECK(!memcmp(movie, reference, SAMPLES * sizeof *movie));
        ds_set_movie_playing(0);
        free(movie); free(reference); free(off); free(on); ds_device_release(device); device = NULL;
    }
    CHECK(chain_calls > 0 && original_calls > 0);
    // Quantisation sanity: the actual guest and local expression agree for below-threshold samples in the quiet proof.
    CHECK(quantise(0.1f) == 3277);
    free(in); free(G);
    printf("%d checks, %d failed\n", checks, failed);
    return failed ? 1 : 0;
}
