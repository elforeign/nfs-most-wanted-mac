// dsound_host.cpp - DirectSound 8 device/buffer state, cursors and the 6-channel mixer (see dsound_host.h).
//
// Clock model. The mixer hands blocks of `block_frames` to the sink and keeps `latency_frames` queued ahead of the
// device. `written` counts output frames mixed so far; `played` estimates the frames the device has played (from
// the AudioOutput queue depth, the wall clock for the null sink, or `written` itself in manual mode). For each
// buffer, `upos` is the unwrapped source frame the mixer has consumed up to, and `anchor` the output frame where that
// position lands. The play cursor is upos - (anchor - played) * freq / mix_rate, never before the position Play
// started from and never moving backwards; the write cursor is what the mixer has consumed (plus the resampler's
// lookahead). So both move in real time, the play cursor is what is being heard, and the game may write anywhere
// outside [play, write) - exactly the DirectSound contract.
#include "dsound_host.h"
#include "peak_limiter.h"

#include <algorithm>
#include <atomic>
#include <cstdarg>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <mutex>
#include <pthread.h>
#include <string>
#include <thread>
#include <unistd.h>
#include <vector>

#include "../../media/media.h"

namespace {

using Clock = std::chrono::steady_clock;
constexpr int kOut = 6;  // FL FR FC LFE BL BR
constexpr uint64_t kOne = 1ull << 32;

bool g_log = getenv("NFS_DSOUND_LOG") != nullptr;
#define DSLOG(...) do { if (g_log) { fprintf(stderr, "[dsound] " __VA_ARGS__); fputc('\n', stderr); } } while (0)

// Output changes (opens, closes, handovers, device changes, underruns): rare, always logged to stderr and to the
// runtime's log hook (ds_set_log: diag events.log).
std::atomic<void (*)(const char *)> g_log_hook{nullptr};
std::atomic<int> g_limiter{-1}; // lazy environment default, or the F10 live choice
std::atomic<bool> g_movie_playing{false}; // WMV and VP6 share this device with Snd
std::atomic<unsigned> g_limiter_generation{0};
__attribute__((format(printf, 1, 2))) void ds_log(const char *fmt, ...) {
    char buf[600];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    fprintf(stderr, "dsound: %s\n", buf);
    if (auto fn = g_log_hook.load()) fn(buf);
}

uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }
uint32_t rd32(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
void wr32(uint8_t *p, uint32_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24); }

// KSDATAFORMAT_SUBTYPE_PCM / _IEEE_FLOAT without the first dword: {xxxxxxxx-0000-0010-8000-00AA00389B71}
const uint8_t kKsGuidTail[12] = {0x00, 0x00, 0x10, 0x00, 0x80, 0x00, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71};

struct Format {
    int channels = 2, rate = 22050, bits = 8, block = 2;
    bool is_float = false;
    uint32_t mask = 0;          // speaker mask actually used for routing
    uint8_t raw[40] = {};       // the WAVEFORMATEX(-TENSIBLE) as given (cbSize normalized for PCM)
    uint32_t raw_len = 18;
};

uint32_t default_mask(int ch) {
    switch (ch) {
    case 1: return 0x4;     // routed specially (both fronts), see make_routes
    case 2: return 0x3;
    case 3: return 0x7;
    case 4: return 0x33;    // quad: FL FR BL BR
    case 5: return 0x37;
    case 6: return 0x3F;    // 5.1: FL FR FC LFE BL BR
    case 7: return 0x13F;
    case 8: return 0x63F;   // 7.1: + SL SR
    default: return 0;
    }
}

bool parse_format(const void *wfx, Format &f) {
    if (!wfx) return false;
    const uint8_t *p = (const uint8_t *)wfx;
    int tag = rd16(p);
    Format r;
    r.channels = rd16(p + 2);
    r.rate = (int)rd32(p + 4);
    r.block = rd16(p + 12);
    r.bits = rd16(p + 14);
    uint32_t cb = tag == 1 || tag == 3 ? 0 : rd16(p + 16);   // plain PCM/float carry no extension (raw[] holds 40 bytes)
    r.mask = default_mask(r.channels);
    if (tag == 1) {
        r.is_float = false;
    } else if (tag == 3) {
        r.is_float = true;
    } else if (tag == 0xFFFE) {
        if (cb < 22) return false;
        uint32_t sub = rd32(p + 24);
        if (memcmp(p + 28, kKsGuidTail, 12) != 0 || (sub != 1 && sub != 3)) return false;
        r.is_float = sub == 3;
        uint32_t m = rd32(p + 20);
        if (m) r.mask = m;
        cb = 22;
    } else {
        return false;
    }
    if (r.channels < 1 || r.channels > 8 || r.rate < DSBFREQUENCY_MIN || r.rate > DSBFREQUENCY_MAX) return false;
    if (r.is_float ? r.bits != 32 : (r.bits != 8 && r.bits != 16 && r.bits != 24 && r.bits != 32)) return false;
    if (r.block != r.channels * r.bits / 8) return false;
    r.raw_len = 18 + cb;
    memcpy(r.raw, p, r.raw_len);
    if (tag == 1 || tag == 3) r.raw[16] = r.raw[17] = 0;
    f = r;
    return true;
}

void make_format(Format &f, int ch, int rate, int bits) {
    uint8_t w[18] = {};
    w[0] = 1;
    w[2] = (uint8_t)ch;
    wr32(w + 4, (uint32_t)rate);
    wr32(w + 8, (uint32_t)(rate * ch * bits / 8));
    w[12] = (uint8_t)(ch * bits / 8);
    w[14] = (uint8_t)bits;
    parse_format(w, f);
}

struct Route { int src, out; float base; };

std::vector<Route> make_routes(const Format &f) {
    std::vector<Route> r;
    if (f.channels == 1) return {{0, 0, 1.0f}, {0, 1, 1.0f}};  // DirectSound: mono at centre pan is full level on L and R
    // Speaker bit -> output channel. FLC/FRC fold into the fronts, BC into both backs, SL/SR into the backs.
    int ch = 0;
    for (int bit = 0; bit < 18 && ch < f.channels; ++bit) {
        if (!(f.mask & (1u << bit))) continue;
        switch (bit) {
        case 0: case 1: case 2: case 3: case 4: case 5: r.push_back({ch, bit, 1.0f}); break;
        case 6: r.push_back({ch, 0, 1.0f}); break;
        case 7: r.push_back({ch, 1, 1.0f}); break;
        case 8: r.push_back({ch, 4, 0.70710678f}); r.push_back({ch, 5, 0.70710678f}); break;
        case 9: r.push_back({ch, 4, 1.0f}); break;
        case 10: r.push_back({ch, 5, 1.0f}); break;
        default: break;  // top speakers etc.: dropped
        }
        ++ch;
    }
    return r;
}

float db100_to_gain(int32_t v) { return v <= -10000 ? 0.0f : (float)std::pow(10.0, v / 2000.0); }

struct Notify { uint32_t frame; uint32_t event; bool stop; };

enum class State { Stopped, Playing, Draining };

}  // namespace

struct DsDevice;

struct DsBuffer {
    DsDevice *dev = nullptr;
    bool primary = false;
    std::atomic<bool> lost{false};  // the device was released
    uint32_t flags = 0;
    Format fmt;
    uint8_t *mem = nullptr;
    uint32_t bytes = 0, nframes = 0;
    std::vector<Route> routes;
    std::vector<float> gain_cur, gain_tgt;
    State state = State::Stopped;
    bool looping = false;
    uint32_t freq = 0;      // current playback rate
    uint32_t mix_freq = 0;  // rate the queued (not yet heard) audio was mixed at
    uint64_t step = kOne;   // source frames per output frame, 32.32
    uint64_t upos = 0;      // unwrapped consumed position, frames
    uint32_t frac = 0;      // fractional part of the position, 1/2^32 frames
    uint32_t idx = 0;       // wrapped position (looping) or position (one-shot)
    uint64_t start_upos = 0, last_play = 0;
    uint64_t anchor = 0;    // output frame matching upos
    int32_t volume = 0, pan = 0;
    std::vector<Notify> notifies;
    uint64_t notify_upos = 0;  // play position notifications were last checked at
};

struct DsDevice {
    DsConfig cfg{};
    unsigned id = 0;                   // for the log: 1, 2, ... in creation order
    Clock::time_point underrun_logged; // the last underrun log line (at most one a second)
    std::mutex m;
    std::vector<DsBuffer *> buffers;   // all live buffers including the primary
    bool alive = true;
    uint32_t coop = 0;
    uint32_t speaker = DSSPEAKER_5POINT1;
    float master = 1.0f;
    int32_t master_volume = 0;
    Format primary_fmt;

    bool follow_rate = false;          // the mix rate follows the primary buffer's SetFormat (not in Dolby Digital)
    int format_rate = 0;               // the primary buffer's SetFormat rate, 0 until set
    int fixed_rate = 0;                // NFS_AUDIO_RATE / cfg.mix_rate when given
    int requested_latency = 0;
    bool fixed_latency = false;        // cfg.latency_frames was given
    std::mutex reconf;                 // serializes output restarts
    std::unique_ptr<media::AudioOutput> out;
    std::thread thread;
    std::atomic<bool> running{false};

    // Following the output device (device sink). `detached`: no working output, so the clock runs on the wall clock
    // like the null sink and the mix is dropped, while `opener` opens one on the current default device in the
    // background (passthrough can take a second to switch formats; the cursors must keep moving meanwhile).
    bool detached = false;             // under m
    uint64_t devgen = 0, pending_gen = 0;
    Clock::time_point pending_at, retry_at;
    std::thread opener;
    std::atomic<bool> opening{false};
    std::mutex ready_m;                // the opener's result, swapped in by the mixer
    std::unique_ptr<media::AudioOutput> ready;
    DsOutputMode ready_mode = DS_OUT_PCM51;
    int ready_rate = 0;
    std::string ready_why;
    int passthrough_failures = 0;      // opener: failed Dolby Digital opens in a row (a TV that just woke can refuse)
    DsOutputMode want_mode = DS_OUT_PCM51;   // the mode asked for (auto_mode off); cfg.mode is the one playing
    int choice_gen = 0;                // mixer: the ds_set_output_choice generation applied
    std::vector<std::pair<double, int>> test_drops;   // NFS_AUDIO_TEST_DROP: (seconds after creation, failed reopens)
    Clock::time_point created;
    std::atomic<bool> launch_reopen{false}; // requested by a safe (unpaused) guest cursor poll
    std::atomic<bool> launch_paused{false}; // a subsequent poll can cancel before the mixer acts
    bool launch_reopening = false;         // mixer: waiting for this one open's result

    uint64_t written = 0, last_played = 0, underruns = 0;
    bool primed = false;               // the sink queue has reached the latency since the (re)start
    uint64_t snap_played = 0;          // device sink: frames played at snap_time
    Clock::time_point snap_time, t0;   // t0: null sink clock origin

    std::vector<float> tmp, scratch, block;
    const bool diag = getenv("BOOT_DIAG") && *getenv("BOOT_DIAG");
    PeakLimiter limiter;
    bool limiter_on = false;
    bool source_limiter = false;
    unsigned limiter_generation = 0;
    uint64_t limited = 0;
    float peak[kOut] = {};
    double sumsq[kOut] = {};
    uint64_t clipped[kOut] = {};
    uint64_t stat_frames = 0;
    double sub_sumsq[kOut] = {};       // the current 50 ms sub-window (2400 frames at 48 kHz) of the stats window
    uint32_t sub_frames = 0;
    float dip[kOut] = {2, 2, 2, 2, 2, 2};  // lowest 50 ms RMS in the stats window (2: none yet)
};

namespace {

// Dolby Digital (Plus), TrueHD: an encoded passthrough output at 48 kHz, fed in bursts (1536 / 960 frames).
bool bitstream(DsOutputMode m) { return m == DS_OUT_DOLBY_DIGITAL || m == DS_OUT_DOLBY_DIGITAL_PLUS || m == DS_OUT_TRUEHD; }

// Process-wide, not device-wide: Snd's startup probe and later ReInit must not reset the timer or the one-shot.
struct LaunchReopen {
    std::mutex m;
    bool seen = false, requested = false, logged = false;
    Clock::time_point opened;
    DsOutputMode mode = DS_OUT_PCM51;
};
LaunchReopen &launch_reopen() { static auto *s = new LaunchReopen; return *s; }

// Caller holds s.m. Always on stderr (NFSMW.log), with the same optional diagnostic hook as other output changes.
void launch_log(LaunchReopen &s, const char *result, DsOutputMode mode) {
    if (s.logged) return;
    s.logged = true;
    auto ms = s.seen ? std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - s.opened).count() : 0;
    const char *fmt = mode == DS_OUT_TRUEHD ? "truehd" : mode == DS_OUT_DOLBY_DIGITAL ? "dd"
                    : mode == DS_OUT_DOLBY_DIGITAL_PLUS ? "ddp" : mode == DS_OUT_STEREO ? "stereo" : "pcm51";
    char line[240];
    snprintf(line, sizeof line, "audio: launch reopen %s after %lld ms, format %s", result, (long long)ms, fmt);
    fprintf(stderr, "%s\n", line);
    if (auto fn = g_log_hook.load()) fn(line);
}

void launch_opened(DsDevice &d) {
    auto &s = launch_reopen();
    std::lock_guard<std::mutex> g(s.m);
    if (s.seen || (!d.out && d.cfg.sink == DS_SINK_DEVICE)) return;
    s.seen = true;
    s.opened = Clock::now();
    s.mode = d.cfg.mode;
    const char *e = getenv("NFS_AUDIO_LAUNCH_REOPEN");
    if (e && !strcmp(e, "0")) launch_log(s, "skipped:disabled", s.mode);
    else if (d.cfg.sink != DS_SINK_DEVICE) launch_log(s, "skipped:null", s.mode);
    else if (!bitstream(s.mode)) launch_log(s, "skipped:pcm", s.mode);
}
// Frames the mixer keeps ahead of the device: more for the bitstreams, whose encoders hold frames back (an AC-3 frame;
// TrueHD's 16-unit restart interval plus a 960-frame MAT frame) and whose devices take whole bursts.
int default_latency(DsOutputMode m) { return m == DS_OUT_TRUEHD ? 4096 : bitstream(m) ? 3072 : 1024; }
// Apply after every open/reuse and while mixing: device timing can change without a reopen.
// Even an explicit PCM target must cover the hardware callback period.
void output_latency(DsDevice &d) {
    int target = d.fixed_latency ? d.requested_latency : default_latency(d.cfg.mode);
    if (d.out && !bitstream(d.cfg.mode)) target = std::max(target, d.out->minimum_queue_frames());
    d.cfg.latency_frames = target;
}
DsOutputMode ds_mode(media::OutputMode m) {
    switch (m) {
    case media::OutputMode::TrueHd: return DS_OUT_TRUEHD;
    case media::OutputMode::DolbyDigitalPlus: return DS_OUT_DOLBY_DIGITAL_PLUS;
    case media::OutputMode::DolbyDigital: return DS_OUT_DOLBY_DIGITAL;
    case media::OutputMode::Pcm51: return DS_OUT_PCM51;
    default: return DS_OUT_STEREO;
    }
}
const char *mode_label(DsOutputMode m) {
    switch (m) {
    case DS_OUT_TRUEHD: return "Dolby TrueHD 5.1";
    case DS_OUT_DOLBY_DIGITAL_PLUS: return "Dolby Digital Plus 5.1";
    case DS_OUT_DOLBY_DIGITAL: return "Dolby Digital 5.1";
    case DS_OUT_PCM51: return "PCM 5.1";
    default: return "stereo";
    }
}

// ------------------------------------------------------------------ clock
uint64_t played_now(DsDevice &d) {
    uint64_t p;
    double rate = d.cfg.mix_rate;
    if (d.cfg.sink == DS_SINK_MANUAL) {
        p = d.written;
    } else if (d.cfg.sink == DS_SINK_NULL || d.detached) {
        p = (uint64_t)(std::chrono::duration<double>(Clock::now() - d.t0).count() * rate);
    } else {
        p = d.snap_played + (uint64_t)(std::chrono::duration<double>(Clock::now() - d.snap_time).count() * rate);
    }
    p = std::min(p, d.written);
    p = std::max(p, d.last_played);
    d.last_played = p;
    return p;
}

// Rounded up, so the position never lags the exact rate (drift < 1 frame per 2^32 output frames).
void set_step(DsBuffer &b, int mix_rate) {
    b.step = (((uint64_t)b.freq << 32) + (uint64_t)mix_rate - 1) / (uint64_t)mix_rate;
}

void update_gains(DsBuffer &b, float master) {
    float vg = db100_to_gain(b.volume) * master;
    float lg = 1.0f, rg = 1.0f;
    if (b.fmt.channels <= 2) {
        if (b.pan > 0) lg = db100_to_gain(-b.pan);
        if (b.pan < 0) rg = db100_to_gain(b.pan);
    }
    b.gain_tgt.resize(b.routes.size());
    for (size_t i = 0; i < b.routes.size(); ++i) {
        float g = b.routes[i].base * vg;
        if (b.fmt.channels <= 2) g *= b.routes[i].out == 0 ? lg : b.routes[i].out == 1 ? rg : 1.0f;
        b.gain_tgt[i] = g;
    }
    if (b.gain_cur.size() != b.gain_tgt.size()) b.gain_cur = b.gain_tgt;
}

// Play cursor (unwrapped source frames) for a playing/draining buffer; updates its monotonic floor.
uint64_t play_upos(DsBuffer &b, uint64_t played, int mix_rate) {
    if (b.state == State::Stopped) return b.upos;
    double lag_out = b.anchor > played ? (double)(b.anchor - played) : 0.0;
    double pos = (double)b.upos + (double)b.frac / 4294967296.0 - lag_out * (double)b.mix_freq / mix_rate;
    uint64_t p = pos <= (double)b.start_upos ? b.start_upos : (uint64_t)pos;
    p = std::min(std::max(p, b.last_play), b.upos);
    b.last_play = p;
    return p;
}

uint32_t wrap_frame(const DsBuffer &b, uint64_t upos) { return b.nframes ? (uint32_t)(upos % b.nframes) : 0; }

// ------------------------------------------------------------------ decode / resample / route
void convert(const DsBuffer &b, uint32_t i, size_t run, float *dst) {
    const uint8_t *s = b.mem + (size_t)i * b.fmt.block;
    size_t n = run * b.fmt.channels;
    switch (b.fmt.bits) {
    case 8:
        for (size_t k = 0; k < n; ++k) dst[k] = ((int)s[k] - 128) * (1.0f / 128.0f);
        break;
    case 16:
        for (size_t k = 0; k < n; ++k) {
            int16_t v;
            memcpy(&v, s + k * 2, 2);
            dst[k] = v * (1.0f / 32768.0f);
        }
        break;
    case 24:
        for (size_t k = 0; k < n; ++k) {
            int32_t v = (int32_t)((uint32_t)s[k * 3] << 8 | (uint32_t)s[k * 3 + 1] << 16 | (uint32_t)s[k * 3 + 2] << 24);
            dst[k] = (float)(v >> 8) * (1.0f / 8388608.0f);
        }
        break;
    default:
        if (b.fmt.is_float) {
            memcpy(dst, s, n * 4);
        } else {
            for (size_t k = 0; k < n; ++k) {
                int32_t v;
                memcpy(&v, s + k * 4, 4);
                dst[k] = (float)((double)v * (1.0 / 2147483648.0));
            }
        }
        break;
    }
}

// count source frames starting at (possibly negative) index start; wraps for looping buffers, zeros outside a
// one-shot buffer.
void decode(const DsBuffer &b, int64_t start, size_t count, float *dst) {
    const int ch = b.fmt.channels;
    const int64_t nf = b.nframes;
    size_t k = 0;
    while (k < count) {
        int64_t i = start + (int64_t)k;
        size_t run;
        if (b.looping) {
            i %= nf;
            if (i < 0) i += nf;
            run = std::min<size_t>(count - k, (size_t)(nf - i));
            convert(b, (uint32_t)i, run, dst + k * ch);
        } else if (i < 0 || i >= nf) {
            run = i < 0 ? std::min<size_t>(count - k, (size_t)-i) : count - k;
            std::fill(dst + k * ch, dst + (k + run) * ch, 0.0f);
        } else {
            run = std::min<size_t>(count - k, (size_t)(nf - i));
            convert(b, (uint32_t)i, run, dst + k * ch);
        }
        k += run;
    }
}

// Mixes up to n output frames of b into out (interleaved 6-ch, accumulated). Returns the frames produced; fewer than
// n means a one-shot buffer reached its end.
size_t mix_buffer(DsDevice &d, DsBuffer &b, float *out, size_t n) {
    const int ch = b.fmt.channels;
    size_t m = n;
    if (!b.looping) {
        uint64_t remain = ((uint64_t)(b.nframes - b.idx) << 32) - b.frac;  // fixed-point distance to the end
        uint64_t jend = (remain + b.step - 1) / b.step;
        if (jend < m) m = (size_t)jend;
    }
    if (d.tmp.size() < m * ch) d.tmp.resize(m * ch);
    float *t = d.tmp.data();
    if (b.step == kOne && b.frac == 0) {
        decode(b, b.idx, m, t);  // the common case: source rate == mix rate, copy without interpolation
    } else {
        // Catmull-Rom over frames idx-1 .. last+2
        uint64_t last = ((uint64_t)b.frac + (uint64_t)(m - 1) * b.step) >> 32;
        size_t span = (size_t)last + 4;
        if (d.scratch.size() < span * ch) d.scratch.resize(span * ch);
        float *s = d.scratch.data();
        decode(b, (int64_t)b.idx - 1, span, s);
        uint64_t f = b.frac;
        for (size_t j = 0; j < m; ++j, f += b.step) {
            const float *p = s + (f >> 32) * ch;
            float x = (float)(uint32_t)f * (1.0f / 4294967296.0f);
            for (int c = 0; c < ch; ++c) {
                float y0 = p[c], y1 = p[ch + c], y2 = p[2 * ch + c], y3 = p[3 * ch + c];
                t[j * ch + c] = y1 + 0.5f * x * (y2 - y0 + x * (2.0f * y0 - 5.0f * y1 + 4.0f * y2 - y3 +
                                                             x * (3.0f * (y1 - y2) + y3 - y0)));
            }
        }
    }
    for (size_t r = 0; r < b.routes.size(); ++r) {
        const int src = b.routes[r].src, o = b.routes[r].out;
        float g0 = b.gain_cur[r], g1 = b.gain_tgt[r];
        if (g0 == g1) {
            if (g1 == 0.0f) continue;
            for (size_t j = 0; j < m; ++j) out[j * kOut + o] += t[j * ch + src] * g1;
        } else {
            // a volume/pan change ramps over one block so it doesn't click
            float dg = (g1 - g0) / (float)m;
            for (size_t j = 0; j < m; ++j) out[j * kOut + o] += t[j * ch + src] * (g0 + dg * (float)(j + 1));
            b.gain_cur[r] = g1;
        }
    }
    uint64_t adv = (uint64_t)b.frac + (uint64_t)m * b.step;
    uint64_t whole = adv >> 32;
    b.frac = (uint32_t)adv;
    b.upos += whole;
    b.idx = b.looping ? (uint32_t)((b.idx + whole) % b.nframes) : (uint32_t)std::min<uint64_t>(b.idx + whole, b.nframes);
    return m;
}

void fire_crossings(DsBuffer &b, uint64_t from, uint64_t to, std::vector<uint32_t> &events) {
    if (to <= from || b.notifies.empty() || !b.nframes) return;
    for (const Notify &nt : b.notifies) {
        if (nt.stop) continue;
        uint64_t base = from - from % b.nframes + nt.frame;
        if (base <= from) base += b.nframes;
        for (; base <= to; base += b.nframes) events.push_back(nt.event);
    }
}

void fire_stop(DsBuffer &b, std::vector<uint32_t> &events) {
    for (const Notify &nt : b.notifies)
        if (nt.stop) events.push_back(nt.event);
}

void stop_buffer(DsBuffer &b, std::vector<uint32_t> &events) {
    if (b.state == State::Stopped) return;
    if (b.state == State::Draining) {  // a one-shot that ran to its end rewinds, like DirectSound
        b.upos = b.idx = 0;
        b.frac = 0;
    }
    b.state = State::Stopped;
    b.start_upos = b.last_play = b.notify_upos = b.upos;
    fire_stop(b, events);
}

// Mixes one block into d.block (6 x n), advances the clock, collects notification events. Called with d.m held.
void mix_block(DsDevice &d, size_t n, std::vector<uint32_t> &events) {
    d.block.assign(n * kOut, 0.0f);
    float *out = d.block.data();
    for (DsBuffer *b : d.buffers) {
        if (b->primary || b->state != State::Playing) continue;
        size_t got = mix_buffer(d, *b, out, n);
        b->mix_freq = b->freq;
        b->anchor = d.written + got;
        if (got < n || (!b->looping && b->idx >= b->nframes)) b->state = State::Draining;
    }
    // WMV/VP6 movies share the DirectSound device. Bypass the whole mix while a MoviePlayer is active,
    // preserving movie samples and timing (including any concurrent Snd audio), before media/ encoding.
    const bool limit = g_limiter.load(std::memory_order_relaxed) != 0 && !g_movie_playing.load(std::memory_order_relaxed);
    unsigned generation = g_limiter_generation.load(std::memory_order_relaxed);
    if (d.limiter_generation != generation) { d.limiter_on = false; d.limiter_generation = generation; }
    if (!limit) d.limiter_on = false;
    if (limit && !d.source_limiter) {
        if (!d.limiter_on || d.limiter.rate() != d.cfg.mix_rate) d.limiter.reset(d.cfg.mix_rate);
        d.limiter_on = true;
        d.limited += d.limiter.process(out, n, d.diag) * kOut;
    } else if (limit) {
        // Snd has already limited before int16 conversion. Catmull-Rom can overshoot between those
        // samples: bound the resampled frame with one gain, without adding another delay/release.
        for (size_t f = 0; f < n; ++f) {
            float peak = 0;
            for (int c = 0; c < kOut; ++c) peak = std::max(peak, std::fabs(out[f * kOut + c]));
            if (peak > PeakLimiter::threshold) {
                float gain = PeakLimiter::threshold / peak;
                for (int c = 0; c < kOut; ++c) out[f * kOut + c] *= gain;
            }
        }
    }
    for (size_t k = 0; k < n * kOut; ++k) {
        int c = (int)(k % kOut);
        // full scale: a sum the clamp below cuts, or a buffer already at the 16-bit limit (32767/32768)
        d.clipped[c] += std::fabs(out[k]) >= 32767.0f / 32768.0f;
        float v = std::min(1.0f, std::max(-1.0f, out[k]));
        out[k] = v;
        d.peak[c] = std::max(d.peak[c], std::fabs(v));
        d.sumsq[c] += (double)v * v;
        d.sub_sumsq[c] += (double)v * v;
        if (c == kOut - 1 && ++d.sub_frames == 2400) {  // a cutout shorter than a second: the quietest 50 ms per channel
            for (int q = 0; q < kOut; ++q) {
                d.dip[q] = std::min(d.dip[q], (float)std::sqrt(d.sub_sumsq[q] / 2400.0));
                d.sub_sumsq[q] = 0;
            }
            d.sub_frames = 0;
        }
    }
    d.stat_frames += n;
    d.written += n;
    uint64_t played = played_now(d);
    for (DsBuffer *b : d.buffers) {
        if (b->primary || b->state == State::Stopped) continue;
        uint64_t p = play_upos(*b, played, d.cfg.mix_rate);
        fire_crossings(*b, b->notify_upos, p, events);
        b->notify_upos = std::max(b->notify_upos, p);
        if (b->state == State::Draining && played >= b->anchor) stop_buffer(*b, events);
    }
}

void deliver(DsDevice &d, const float *pcm, size_t n, std::vector<uint32_t> &events) {
    if (d.cfg.notify)
        for (uint32_t e : events) d.cfg.notify(d.cfg.notify_user, e);
    events.clear();
    if (d.cfg.tap) d.cfg.tap(d.cfg.tap_user, pcm, n);
}

void follow_output(DsDevice &d);

// With m held. The device sink logs its underruns (the queue ran dry: the mixer or the game fell behind), at most once a second.
void note_underrun(DsDevice &d, uint64_t count = 1) {
    d.underruns += count;
    auto now = Clock::now();
    if (d.cfg.sink == DS_SINK_DEVICE && now - d.underrun_logged >= std::chrono::seconds(1)) {
        d.underrun_logged = now;
        ds_log("device %u: underrun (%llu so far, %s%s)", d.id, (unsigned long long)d.underruns,
               d.detached ? "on the wall clock" : "output ", d.detached ? "" : mode_label(d.cfg.mode));
    }
}

void mixer_thread(DsDevice *dp) {
    DsDevice &d = *dp;
    pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE, 0);
    const size_t n = (size_t)d.cfg.block_frames;
    std::vector<uint32_t> events;
    std::vector<float> pcm, zeros(n * kOut, 0.0f);
    const bool diag = getenv("BOOT_DIAG") && *getenv("BOOT_DIAG");
    auto chain_at = Clock::now();
    while (d.running.load(std::memory_order_relaxed)) {
        if (d.cfg.sink == DS_SINK_DEVICE) follow_output(d);   // may change the output, its mode and the mix rate
        const double rate = d.cfg.mix_rate;
        uint64_t lag;
        bool wall, dd;
        auto now = Clock::now();
        bool chain_due = diag && now - chain_at >= std::chrono::seconds(1);
        media::AudioCallbackStats chain;
        size_t pending = 0;
        {
            std::lock_guard<std::mutex> g(d.m);
            if (d.cfg.sink == DS_SINK_DEVICE && !bitstream(d.cfg.mode)) output_latency(d);
            wall = d.cfg.sink == DS_SINK_NULL || d.detached;
            dd = !wall && bitstream(d.cfg.mode);
            if (wall) {
                uint64_t clock = (uint64_t)(std::chrono::duration<double>(Clock::now() - d.t0).count() * rate);
                if (clock > d.written) {  // fell behind: the virtual device starved; restart its clock here
                    if (d.primed) note_underrun(d);
                    d.primed = false;
                    d.t0 = Clock::now() - std::chrono::duration_cast<Clock::duration>(
                                              std::chrono::duration<double>(d.written / rate));
                    clock = d.written;
                }
                lag = d.written - clock;
            } else {
                chain = d.out->take_callback_stats(chain_due);
                pending = dd ? d.out->pending_frames() : 0;
                lag = chain.queued_frames + pending;
                // Pending encoder input is not playable output. Count each callback that actually needed zero
                // fill after priming, including shortages while pending input keeps the apparent lag nonzero.
                if (chain.zero_callbacks && d.primed) note_underrun(d, chain.zero_callbacks);
            }
            if (lag >= (uint64_t)d.cfg.latency_frames) d.primed = true;
            if (lag < (uint64_t)d.cfg.latency_frames) {
                mix_block(d, n, events);
                pcm.assign(d.block.begin(), d.block.end());
            }
        }
        if (chain_due) {
            ds_log("audiochain t=%.3f cb=%llu valid=%llu taken_bytes=%llu zero_bytes=%llu qbytes=%zu pending_frames=%zu "
                   "cb_gap_max_ms=%.3f encode_max_ms=%.3f device=%u underruns=%llu",
                   std::chrono::duration<double>(now.time_since_epoch()).count(),
                   (unsigned long long)chain.callbacks, (unsigned long long)chain.valid,
                   (unsigned long long)chain.taken_bytes, (unsigned long long)chain.zero_bytes, chain.queue_bytes, pending,
                   chain.gap_max_ns / 1e6, chain.encode_max_ns / 1e6, d.id, (unsigned long long)d.underruns);
            chain_at = now;
        }
        if (lag < (uint64_t)d.cfg.latency_frames) {
            deliver(d, pcm.data(), n, events);
            if (!wall) {
                d.out->write(d.cfg.mute_device ? zeros.data() : pcm.data(), n);
                size_t q = d.out->queued_frames();
                std::lock_guard<std::mutex> g(d.m);
                uint64_t lag2 = q + (dd ? d.out->pending_frames() : 0);
                d.snap_played = d.written > lag2 ? d.written - lag2 : 0;
                d.snap_time = Clock::now();
            }
            continue;
        }
        double ahead = (double)(lag - d.cfg.latency_frames) + n / 2.0;
        double sec = std::min(0.005, std::max(0.0005, ahead / rate));
        std::this_thread::sleep_for(std::chrono::duration<double>(sec));
    }
}

media::OutputMode media_mode(DsOutputMode m) {
    return m == DS_OUT_STEREO ? media::OutputMode::Stereo
         : m == DS_OUT_DOLBY_DIGITAL ? media::OutputMode::DolbyDigital
         : m == DS_OUT_DOLBY_DIGITAL_PLUS ? media::OutputMode::DolbyDigitalPlus
         : m == DS_OUT_TRUEHD ? media::OutputMode::TrueHd : media::OutputMode::Pcm51;
}
std::unique_ptr<media::AudioOutput> open_output(DsOutputMode mode, int rate) {
    std::unique_ptr<media::AudioOutput> out(new media::AudioOutput);
    if (!out->open(media_mode(mode), rate)) {
        DSLOG("AudioOutput::open failed (mode %d, %d Hz)", (int)mode, rate);
        return nullptr;
    }
    return out;
}
// The rate an output for `mode` runs at: Dolby Digital (Plus) and TrueHD 48 kHz, else NFS_AUDIO_RATE, else the game's own (auto rate).
int output_rate(const DsDevice &d, DsOutputMode mode) {
    if (bitstream(mode)) return 48000;
    if (d.fixed_rate) return d.fixed_rate;
    return d.follow_rate && d.format_rate ? d.format_rate : 48000;
}

// Rebases the clock and every buffer on a new mix rate (with m held): queued output is gone, cursors stay continuous.
void rebase_rate(DsDevice &d, int rate) {
    DSLOG("mix rate %d -> %d Hz", d.cfg.mix_rate, rate);
    d.cfg.mix_rate = rate;
    d.last_played = d.snap_played = d.written;
    d.primed = false;
    d.snap_time = Clock::now();
    d.t0 = d.snap_time - std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(d.written / (double)rate));
    for (DsBuffer *b : d.buffers) {
        if (b->primary) continue;
        set_step(*b, rate);
        b->anchor = d.written;
    }
}

// No working output: drop it and run on the wall clock from where the device clock was.
void detach(DsDevice &d, const char *why) {
    {
        std::lock_guard<std::mutex> g(d.m);
        uint64_t p = played_now(d);
        d.detached = true;
        d.primed = false;
        d.t0 = Clock::now() - std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(p / (double)d.cfg.mix_rate));
    }
    if (d.out) { d.out->close(); d.out.reset(); }     // a passthrough output gives its device back (hog mode, format)
    ds_log("device %u: %s: mixing on the wall clock until an output device takes it", d.id, why);
}

// ------------------------------------------------------------------ the output across device re-creation
// Snd creates its DirectSound device twice at start-up (the probe FUN_008205c5 releases its device, then the start path
// FUN_00820762 creates the real one) and again at every Snd::System::ReInit (0081B841: the career load). On Windows the
// hardware stays as it is. Here closing a passthrough output gives the device back (hog mode off, format restored) and
// the next open takes it again milliseconds later: the HDMI link re-locks (TrueHD: seconds of silence), macOS has moved
// its default output elsewhere while we held the TV (so the automatic choice saw the built-in speakers and opened stereo),
// and the format restore raced the next open (which then saved the encoded format as the one to restore). So the output
// of a released device is parked for 2 s, still running, and the next device created takes it over, encoder and queue
// included, when it would open the same thing: the same mode (automatic: chosen automatically), a device still there and
// still consuming queued audio or accepting valid empty-queue callbacks, the same rate. Otherwise, or when no
// device comes, it's closed.
struct Parked {
    std::unique_ptr<media::AudioOutput> out;
    DsOutputMode mode = DS_OUT_PCM51;
    int rate = 0;
    bool auto_mode = false;
    unsigned from = 0;                 // the device it came from
    uint64_t gen = 0;
};
struct Shared {
    std::mutex m;
    Parked parked;
    uint64_t gen = 0;
    std::vector<DsDevice *> live;      // devices not yet released (closed at exit)
    unsigned next_id = 0;
};
Shared &shared() {                     // never destroyed: the reaper threads and the exit handler use it
    static Shared *s = new Shared;
    return *s;
}
std::string out_text(const media::AudioOutput &o, int rate) {
    return std::string(o.describe()) + ", " + std::to_string(rate) + " Hz on \"" + media::device_name(o.device()) + "\"";
}

void park_output(DsDevice &d) {
    Shared &sh = shared();
    std::unique_ptr<media::AudioOutput> old;
    uint64_t gen;
    std::string text = out_text(*d.out, d.cfg.mix_rate);
    {
        std::lock_guard<std::mutex> g(sh.m);
        old = std::move(sh.parked.out);
        gen = ++sh.gen;
        sh.parked = Parked{std::move(d.out), d.cfg.mode, d.cfg.mix_rate, d.cfg.auto_mode != 0, d.id, gen};
    }
    if (old) { old->close(); ds_log("an older parked output closed"); }
    ds_log("device %u released: its output stays open for the next device (%s)", d.id, text.c_str());
    std::thread([gen] {
        std::this_thread::sleep_for(std::chrono::seconds(2));
        Shared &sh = shared();
        std::unique_ptr<media::AudioOutput> o;
        {
            std::lock_guard<std::mutex> g(sh.m);
            if (sh.parked.gen == gen) o = std::move(sh.parked.out);
        }
        if (o) { o->close(); ds_log("parked output closed: no device took it within 2 s"); }
    }).detach();
}

// The parked output for a new device configured as cfg (fixed_rate: NFS_AUDIO_RATE), or null. A parked output that
// doesn't fit is closed; *closed_dev is then its device if it was a passthrough (macOS may not have moved the default
// output back to it yet), else 0.
std::unique_ptr<media::AudioOutput> take_parked(const DsConfig &cfg, int fixed_rate, DsOutputMode *mode, int *rate,
                                                unsigned *from, uint32_t *closed_dev) {
    Parked p;
    {
        Shared &sh = shared();
        std::lock_guard<std::mutex> g(sh.m);
        p = std::move(sh.parked);
        sh.parked = Parked{};
    }
    *closed_dev = 0;
    if (!p.out) return nullptr;
    const char *no = nullptr;
    if (cfg.sink != DS_SINK_DEVICE) no = "the new device has no output";
    else if (cfg.auto_mode ? !p.auto_mode : p.mode != cfg.mode) no = "the new device wants another mode";
    else if (fixed_rate && !bitstream(p.mode) && p.rate != fixed_rate) no = "the new device wants another rate";
    else if (p.out->stalled(0.5)) no = "it stopped taking audio";
    else if (p.out->exclusive() ? !media::device_alive(p.out->device()) : p.out->device() != media::default_output_device())
        no = p.out->exclusive() ? "its device went away" : "the default output device changed";
    if (no) {
        if (p.out->exclusive()) *closed_dev = p.out->device();
        p.out->close();
        ds_log("parked output of device %u closed: %s", p.from, no);
        return nullptr;
    }
    *mode = p.mode;
    *rate = p.rate;
    *from = p.from;
    return std::move(p.out);
}

// At exit (ExitProcess -> exit): close every output, so a passthrough gives its device back in PCM with mixing on.
void close_all_at_exit() {
    {
        auto &s = launch_reopen();
        std::lock_guard<std::mutex> g(s.m);
        launch_log(s, "skipped:exit", s.mode);
    }
    Shared &sh = shared();
    std::vector<DsDevice *> live;
    std::unique_ptr<media::AudioOutput> parked;
    {
        std::lock_guard<std::mutex> g(sh.m);
        live = sh.live;
        parked = std::move(sh.parked.out);
    }
    if (parked) parked->close();
    for (DsDevice *d : live) {
        std::lock_guard<std::mutex> rg(d->reconf);
        if (d->thread.joinable()) { d->running = false; d->thread.join(); }
        if (d->opener.joinable()) d->opener.join();
        if (d->out) d->out->close();
    }
}

// Opens an output on the current default device on a background thread; the mixer swaps it in (follow_output).
void open_in_background(DsDevice &d, uint32_t released_device = 0) {
    if (d.opening.exchange(true)) return;
    if (d.opener.joinable()) d.opener.join();
    d.opener = std::thread([&d, released_device] {
        // Launch only: passthrough moved the default to the speakers while it held the TV. As in ds_device_create
        // after a parked close, give macOS up to 0.5 s to restore it before Automatic chooses the next output.
        if (released_device && media::device_alive(released_device))
            for (int i = 0; i < 25 && media::default_output_device() != released_device; ++i) usleep(20000);
        std::string why = getenv("NFS_AUDIO") ? "NFS_AUDIO" : "chosen in the Audio menu";
        DsOutputMode mode = d.want_mode;
        if (d.cfg.auto_mode) {
            mode = ds_mode(media::preferred_output_mode(&why, d.passthrough_failures < 3));
        }
        int rate = output_rate(d, mode);
        auto out = open_output(mode, rate);
        if (bitstream(mode)) {
            // Passthrough failing (a TV that is still waking up, the device held by another app): retry it for a few
            // seconds, then play PCM rather than nothing.
            // TrueHD and Dolby Digital Plus fall back to what suits the device without TrueHD: Dolby Digital on one that
            // takes AC-3, else PCM 5.1 / stereo. (Automatic mode then skips TrueHD until it opens again: see above.)
            if (out) d.passthrough_failures = 0;
            else if (++d.passthrough_failures >= 3) {
                mode = mode != DS_OUT_DOLBY_DIGITAL ? ds_mode(media::preferred_output_mode(nullptr, false)) : DS_OUT_STEREO;
                rate = output_rate(d, mode);
                out = open_output(mode, rate);
                why += std::string("; passthrough refused, ") + mode_label(mode) + " instead";
            }
        }
        {
            std::lock_guard<std::mutex> g(d.ready_m);
            d.ready = std::move(out);
            d.ready_mode = mode;
            d.ready_rate = rate;
            d.ready_why = why;
        }
        d.opening = false;
    });
}

// The output picked in the app's Audio menu (ds_set_output_choice): -1 none, else an index into kChoices.
const char *const kChoices[] = {"auto", "dd", "pcm51", "stereo", "ddp", "truehd"};
constexpr int kNumChoices = sizeof kChoices / sizeof *kChoices;
std::atomic<int> g_choice{-1}, g_choice_gen{0};
thread_local std::string g_config_why;   // ds_config_default's reason for its choice, logged by ds_device_create

// Applies an output choice ("auto", "pcm51", "stereo", "dd", "ddp", "truehd", "null") to a config.
void apply_choice(DsConfig *cfg, const std::string &s, std::string *why = nullptr) {
    cfg->auto_mode = 0;
    cfg->sink = DS_SINK_DEVICE;
    if (s == "stereo") cfg->mode = DS_OUT_STEREO;
    else if (s == "dd" || s == "ac3" || s == "dolby") cfg->mode = DS_OUT_DOLBY_DIGITAL;
    else if (s == "ddp" || s == "eac3" || s == "dd+") cfg->mode = DS_OUT_DOLBY_DIGITAL_PLUS;
    else if (s == "truehd" || s == "thd" || s == "mat") cfg->mode = DS_OUT_TRUEHD;
    else if (s == "null" || s == "off" || s == "none") cfg->sink = DS_SINK_NULL;
    else if (s == "pcm51") cfg->mode = DS_OUT_PCM51;
    else {  // auto: TrueHD for a device that takes MAT, Dolby Digital for AC-3, PCM 5.1 on a surround setup, else stereo
        cfg->auto_mode = 1;
        cfg->mode = ds_mode(media::preferred_output_mode(why));
    }
}

// Shared by the Audio menu and the launch one-shot: release CoreAudio/hog mode and the encoder before opening.
void reopen_output(DsDevice &d, const char *why, bool launch = false) {
    uint32_t released_device = launch && d.out ? d.out->device() : 0;
    d.passthrough_failures = 0;
    if (!d.detached) detach(d, why);
    open_in_background(d, released_device);
}

// Follows the output device, on the mixer thread. An output that stops taking audio (the device went away: a TV asleep,
// HDMI unplugged; a passthrough IOProc is then never called again) is dropped at once, so the cursors keep moving on
// the wall clock: Snd tears its device down when the write cursor stalls for 100 polls, and on Windows the device
// doesn't vanish. A change of the default output device (debounced: they come in bursts) or a missing output then
// opens one on the current default device, in the mode that suits it when the mode is automatic.
void follow_output(DsDevice &d) {
    // A new choice in the Audio menu: reopen in that mode (NFS_AUDIO, when set, decides instead).
    if (int gen = g_choice_gen.load(); gen != d.choice_gen && !d.opening && !getenv("NFS_AUDIO")) {
        d.choice_gen = gen;
        DsConfig c = d.cfg;
        apply_choice(&c, kChoices[std::max(0, g_choice.load())]);
        d.cfg.auto_mode = c.auto_mode;
        d.want_mode = c.mode;
        ds_log("device %u: Audio menu: %s", d.id, kChoices[std::max(0, g_choice.load())]);
        reopen_output(d, "the output was changed in the Audio menu");
        return;
    }
    if (d.launch_reopen.exchange(false)) {
        auto &s = launch_reopen();
        std::lock_guard<std::mutex> g(s.m);
        if (Clock::now() - s.opened > std::chrono::seconds(5)) {
            launch_log(s, "skipped:late", d.cfg.mode);
        } else if (d.launch_paused.load()) {
            launch_log(s, "skipped:paused", d.cfg.mode);
        } else if (!d.out || d.detached || d.opening || !bitstream(d.cfg.mode)) {
            launch_log(s, "skipped:output-changed", d.cfg.mode);
        } else {
            d.launch_reopening = true;
            reopen_output(d, "one launch reopen", true);
            return;
        }
    }
    if (!d.test_drops.empty() &&
        std::chrono::duration<double>(Clock::now() - d.created).count() >= d.test_drops.front().first) {
        ds_log("device %u: test: the output device goes away (%d reopen attempts fail)", d.id, d.test_drops.front().second);
        media::fail_next_opens(d.test_drops.front().second);
        if (d.out) d.out->stop_for_test();
        d.test_drops.erase(d.test_drops.begin());
    }
    if (!d.opening && d.ready_m.try_lock()) {
        std::unique_ptr<media::AudioOutput> out = std::move(d.ready);
        DsOutputMode mode = d.ready_mode;
        int rate = d.ready_rate;                         // 0: no attempt finished since the last look
        std::string why = d.ready_why;
        d.ready_rate = 0;
        d.ready_m.unlock();
        if (out) {
            if (d.out) d.out->close();
            d.out = std::move(out);
            std::lock_guard<std::mutex> g(d.m);
            uint64_t p = played_now(d);
            d.cfg.mode = mode;
            output_latency(d);
            if (rate != d.cfg.mix_rate) rebase_rate(d, rate);
            else { d.snap_played = p; d.snap_time = Clock::now(); d.primed = false; }
            d.detached = false;
            ds_log("device %u: output now %s (%s)", d.id, out_text(*d.out, rate).c_str(), why.c_str());
        } else if (rate) {
            d.retry_at = Clock::now() + std::chrono::seconds(1);       // nothing usable yet: try again in a second
        }
        if (rate && d.launch_reopening) {
            auto &s = launch_reopen();
            std::lock_guard<std::mutex> g(s.m);
            launch_log(s, d.detached ? "skipped:open-failed" : mode != s.mode ? "skipped:format-changed" : "done", mode);
            d.launch_reopening = false;
        }
        launch_opened(d);
    }
    auto now = Clock::now();
    if (d.out && !d.detached && d.out->stalled(0.5)) detach(d, "the output device stopped taking audio");
    uint64_t gen = media::output_devices_generation();
    if (gen != d.devgen) {
        if (gen != d.pending_gen) { d.pending_gen = gen; d.pending_at = now + std::chrono::milliseconds(400); return; }
        if (now < d.pending_at || d.opening) return;
        d.devgen = gen;
        // Hog mode and format switches (our own passthrough among them) notify too. An ordinary output follows another
        // default device (reopened there in the mode that suits it); a passthrough output, which made macOS move the
        // default away by taking its device, only its device going away.
        if (!d.detached && d.out && (d.out->exclusive() ? media::device_alive(d.out->device())
                                                          : d.out->device() == media::default_output_device())) return;
        if (!d.detached) detach(d, "the default output device changed");
        open_in_background(d);
    } else if (d.detached && !d.opening && now >= d.retry_at) {
        d.retry_at = now + std::chrono::seconds(1);
        open_in_background(d);
    }
}

void stop_thread(DsDevice &d) {
    if (d.thread.joinable()) {
        d.running = false;
        d.thread.join();
    }
}

void start_thread(DsDevice &d) {
    if (d.cfg.sink == DS_SINK_DEVICE) output_latency(d);
    if (d.cfg.sink == DS_SINK_MANUAL) return;
    d.running = true;
    d.thread = std::thread(mixer_thread, &d);
}

// Switches the output to a new mix rate (auto rate: the primary format's). Queued output is dropped; every
// buffer's step and cursor anchor are rebased so the cursors stay continuous.
void set_mix_rate(DsDevice &d, int rate) {
    std::lock_guard<std::mutex> rg(d.reconf);
    if (rate == d.cfg.mix_rate || !d.alive || (d.cfg.sink == DS_SINK_DEVICE && bitstream(d.cfg.mode))) return;
    stop_thread(d);
    if (d.cfg.sink == DS_SINK_DEVICE && !d.detached) {
        if (d.out) d.out->close();
        d.out = open_output(d.cfg.mode, rate);
        if (!d.out) {
            d.out = open_output(d.cfg.mode, d.cfg.mix_rate);
            if (!d.out) detach(d, "the output device went away");    // follow_output reopens it
            else ds_log("device %u: could not reopen at %d Hz, stays at %s", d.id, rate, out_text(*d.out, d.cfg.mix_rate).c_str());
            start_thread(d);
            return;
        }
        ds_log("device %u: output reopened at the game's rate: %s", d.id, out_text(*d.out, rate).c_str());
    }
    {
        std::lock_guard<std::mutex> g(d.m);
        rebase_rate(d, rate);
    }
    start_thread(d);
}

DsResult check_live(DsBuffer *b) {
    if (!b) return DSERR_INVALIDPARAM;
    return b->lost ? DSERR_BUFFERLOST : DS_OK;
}

void destroy_device(DsDevice *d) { delete d; }

}  // namespace

// ====================================================================== C API
extern "C" {

void ds_set_log(void (*fn)(const char *line)) { g_log_hook = fn; }

void ds_set_peak_limiter(int enabled) {
    if (g_limiter.exchange(enabled != 0, std::memory_order_relaxed) != (enabled != 0)) ++g_limiter_generation;
    ds_log("game peak limiter %s (-1 dBFS, 2 ms look-ahead, 150 ms release)", enabled ? "on" : "off");
}

void ds_set_movie_playing(int playing) {
    if (g_movie_playing.exchange(playing != 0, std::memory_order_relaxed) != (playing != 0)) ++g_limiter_generation;
}

int ds_peak_limiter_enabled(void) {
    if (g_limiter.load(std::memory_order_relaxed) < 0) {
        int unset = -1;
        const char *env = getenv("NFS_AUDIO_LIMITER");
        g_limiter.compare_exchange_strong(unset, !(env && !strcmp(env, "off")));
    }
    return g_limiter.load(std::memory_order_relaxed) != 0 && !g_movie_playing.load(std::memory_order_relaxed);
}

void ds_device_limit_game_mix(DsDevice *d, float *pcm6, size_t frames, int rate) {
    if (!d || !pcm6 || rate < 8000 || rate > 192000) return;
    std::lock_guard<std::mutex> g(d->m);
    d->source_limiter = true;
    if (!ds_peak_limiter_enabled()) { d->limiter_on = false; return; }
    unsigned generation = g_limiter_generation.load(std::memory_order_relaxed);
    if (d->limiter_generation != generation) { d->limiter_on = false; d->limiter_generation = generation; }
    if (!d->limiter_on || d->limiter.rate() != rate) d->limiter.reset(rate);
    d->limiter_on = true;
    d->limited += d->limiter.process(pcm6, frames, d->diag) * kOut;
}

void ds_device_launch_reopen_tick(DsDevice *d, int paused) {
    if (!d) return;
    d->launch_paused = paused != 0;
    auto &s = launch_reopen();
    std::lock_guard<std::mutex> g(s.m);
    if (!s.seen || s.logged || s.requested) return;
    auto elapsed = Clock::now() - s.opened;
    // 1.5 s after successful open: beyond the 0.5 s output-health window and the observed ~1 s startup.
    // The receiver's lock is unknowable. Expire at 5 s rather than reopening on a later load/pause resume.
    if (elapsed < std::chrono::milliseconds(1500)) return;
    if (paused) launch_log(s, "skipped:paused", s.mode);
    else if (elapsed > std::chrono::seconds(5)) launch_log(s, "skipped:late", s.mode);
    else { s.requested = true; d->launch_reopen = true; }
}

void ds_set_output_choice(const char *choice) {
    int k = -1;
    for (int i = 0; choice && i < kNumChoices; ++i) if (!strcmp(choice, kChoices[i])) k = i;
    if (k == g_choice.load()) return;
    g_choice = k;
    ++g_choice_gen;
}

void ds_config_default(DsConfig *cfg) {
    *cfg = DsConfig{};
    cfg->sink = DS_SINK_DEVICE;
    cfg->mode = DS_OUT_PCM51;
    cfg->mix_rate = 0;  // auto
    const char *env = getenv("NFS_AUDIO");
    int choice = g_choice.load();
    std::string s = env ? env : choice >= 0 ? kChoices[choice] : "auto";
    // Unattended tests (runtime/run_guarded.sh keeps NFS_AUDIO=null): NFS_AUDIO_FAKE_DEVICE=1 runs the automatic choice on
    // media's fake HDMI TV instead of the null sink, so the device logic runs while nothing reaches CoreAudio.
    if (getenv("NFS_AUDIO_FAKE_DEVICE") && (s == "null" || s == "off" || s == "none")) s = "auto";
    std::string why;
    apply_choice(cfg, s, &why);
    g_config_why = cfg->auto_mode ? "auto: " + why + "; the Audio menu or NFS_AUDIO=pcm51|stereo|dd|ddp|truehd chooses"
                 : env ? "NFS_AUDIO=" + s : "chosen in the Audio menu";
    if (const char *e = getenv("NFS_AUDIO_LATENCY")) cfg->latency_frames = atoi(e);
    if (const char *e = getenv("NFS_AUDIO_RATE")) cfg->mix_rate = atoi(e);
}

DsResult ds_device_create(const DsConfig *cfg_in, DsDevice **out) {
    if (!out) return DSERR_INVALIDPARAM;
    *out = nullptr;
    int unset = -1;
    const char *limiter_env = getenv("NFS_AUDIO_LIMITER");
    g_limiter.compare_exchange_strong(unset, !(limiter_env && !strcmp(limiter_env, "off")));
    DsConfig cfg;
    if (cfg_in) cfg = *cfg_in;
    else ds_config_default(&cfg);
    int fixed_rate = cfg.mix_rate > 0 ? cfg.mix_rate : 0;
    bool fixed_latency = cfg.latency_frames > 0;
    if (cfg.mix_rate <= 0) cfg.mix_rate = 48000;
    if (cfg.block_frames <= 0) cfg.block_frames = 256;
    if (cfg.block_frames > 8192) cfg.block_frames = 8192;
    if (cfg.latency_frames <= 0)
        cfg.latency_frames = cfg.sink == DS_SINK_DEVICE ? default_latency(cfg.mode) : 1024;
    if (cfg.sink == DS_SINK_DEVICE && bitstream(cfg.mode) && cfg.mix_rate != 48000) return DSERR_BADFORMAT;
    auto *d = new DsDevice;
    d->cfg = cfg;
    d->follow_rate = !fixed_rate;
    d->want_mode = cfg.mode;
    d->choice_gen = g_choice_gen.load();             // the choice this config was made with
    d->fixed_rate = fixed_rate;
    d->fixed_latency = fixed_latency;
    d->requested_latency = fixed_latency ? cfg.latency_frames : 0;
    if (cfg.speaker_config) d->speaker = cfg.speaker_config;
    make_format(d->primary_fmt, 2, 22050, 8);  // DirectSound's default primary format
    d->t0 = d->snap_time = d->retry_at = d->created = Clock::now();
    // NFS_AUDIO_TEST_DROP=<s>[/<n>],...: at s seconds the output device stops taking audio (as when a TV sleeps) and the
    // next n opens fail (no device yet), to test following the device; 0/<n> fails the opens at creation.
    for (const char *e = getenv("NFS_AUDIO_TEST_DROP"); e && *e;) {
        char *end;
        double t = strtod(e, &end);
        int fails = *end == '/' ? (int)strtol(end + 1, &end, 10) : 0;
        if (t <= 0) media::fail_next_opens(fails); else d->test_drops.push_back({t, fails});
        e = *end == ',' ? end + 1 : end;
        if (end == e && *e) break;
    }
    {
        Shared &sh = shared();
        std::lock_guard<std::mutex> g(sh.m);
        d->id = ++sh.next_id;
        sh.live.push_back(d);
        static bool registered = false;
        if (!registered) { registered = true; atexit(close_all_at_exit); }
    }
    std::string why = g_config_why.empty() ? "configured by the caller" : g_config_why;
    g_config_why.clear();
    // The output the game's previous device left (see "the output across device re-creation"), when it fits.
    DsOutputMode kept_mode;
    int kept_rate = 0;
    unsigned from = 0;
    uint32_t closed_dev = 0;
    if (auto kept = take_parked(d->cfg, fixed_rate, &kept_mode, &kept_rate, &from, &closed_dev)) {
        d->out = std::move(kept);
        d->cfg.mode = kept_mode;
        if (!d->cfg.auto_mode) d->want_mode = kept_mode;
        d->cfg.mix_rate = kept_rate;
        if (!fixed_latency) d->cfg.latency_frames = default_latency(kept_mode);
        d->devgen = d->pending_gen = media::output_devices_generation();
        ds_log("device %u created: kept the output of device %u: %s", d->id, from, out_text(*d->out, kept_rate).c_str());
    } else if (cfg.sink == DS_SINK_DEVICE) {
        if (closed_dev && d->cfg.auto_mode && media::device_alive(closed_dev)) {
            // A passthrough was just given back: macOS moved the default output away while we held that device. Wait
            // (<= 0.5 s) for it to come back and choose again, rather than settling on the device it moved to.
            for (int i = 0; i < 25 && media::default_output_device() != closed_dev; ++i) usleep(20000);
            std::string w;
            DsOutputMode m = ds_mode(media::preferred_output_mode(&w));
            if (m != d->cfg.mode) {
                d->cfg.mode = d->want_mode = m;
                d->cfg.mix_rate = bitstream(m) ? 48000 : d->cfg.mix_rate;
                if (!fixed_latency) d->cfg.latency_frames = default_latency(m);
                why = "auto: " + w;
            }
        }
        d->devgen = d->pending_gen = media::output_devices_generation();
        d->out = open_output(d->cfg.mode, d->cfg.mix_rate);
        if (d->out) ds_log("device %u created: output %s (%s)", d->id, out_text(*d->out, d->cfg.mix_rate).c_str(), why.c_str());
        else {
            d->detached = true;                    // follow_output keeps trying; the game gets a working device
            ds_log("device %u created: no output device takes %s now: mixing on the wall clock until one does (%s)", d->id,
                   mode_label(d->cfg.mode), why.c_str());
        }
    } else {
        ds_log("device %u created: no output (null sink)", d->id);
    }
    launch_opened(*d);
    start_thread(*d);
    *out = d;
    return DS_OK;
}

void ds_device_release(DsDevice *d) {
    if (!d) return;
    bool empty;
    {
        std::lock_guard<std::mutex> rg(d->reconf);  // no output restart (SetFormat) can start after this
        {
            std::lock_guard<std::mutex> g(d->m);
            d->alive = false;
            for (DsBuffer *b : d->buffers) {
                b->lost = true;
                b->state = State::Stopped;
            }
        }
        stop_thread(*d);
        if (d->opener.joinable()) d->opener.join();
        if (d->launch_reopen || d->launch_reopening) {
            auto &s = launch_reopen();
            std::lock_guard<std::mutex> g(s.m);
            launch_log(s, "skipped:device-released", d->cfg.mode);
        }
        {
            Shared &sh = shared();
            std::lock_guard<std::mutex> g(sh.m);
            sh.live.erase(std::remove(sh.live.begin(), sh.live.end(), d), sh.live.end());
        }
        // A working output waits for the game's next device (Snd re-creates its device: see take_parked).
        if (d->out && d->cfg.sink == DS_SINK_DEVICE && !d->detached && !d->out->stalled(0.5)) park_output(*d);
        else if (d->out) { d->out->close(); ds_log("device %u released: output closed", d->id); }
        else ds_log("device %u released", d->id);
        d->out.reset();
        {
            std::lock_guard<std::mutex> rdy(d->ready_m);
            if (d->ready) d->ready->close();
            d->ready.reset();
        }
        std::lock_guard<std::mutex> g(d->m);
        empty = d->buffers.empty();
    }
    if (empty) destroy_device(d);
}

DsResult ds_device_set_cooperative_level(DsDevice *d, uint32_t level) {
    if (!d) return DSERR_INVALIDPARAM;
    if (level < DSSCL_NORMAL || level > DSSCL_WRITEPRIMARY) return DSERR_INVALIDPARAM;
    std::lock_guard<std::mutex> g(d->m);
    d->coop = level;
    return DS_OK;
}

DsResult ds_device_get_caps(DsDevice *d, void *caps) {
    if (!d || !caps) return DSERR_INVALIDPARAM;
    uint8_t *p = (uint8_t *)caps;
    if (rd32(p) != 96) return DSERR_INVALIDPARAM;
    memset(p + 4, 0, 92);
    // a software device (like every Windows since Vista): no hardware buffers, continuous rates
    wr32(p + 4, 0x1 | 0x2 | 0x4 | 0x8 | 0x10 | 0x40 | 0x100 | 0x200 | 0x400 | 0x800);
    wr32(p + 8, DSBFREQUENCY_MIN);
    wr32(p + 12, DSBFREQUENCY_MAX);
    wr32(p + 16, 1);  // dwPrimaryBuffers
    return DS_OK;
}

DsResult ds_device_get_speaker_config(DsDevice *d, uint32_t *config) {
    if (!d || !config) return DSERR_INVALIDPARAM;
    std::lock_guard<std::mutex> g(d->m);
    *config = d->speaker;
    return DS_OK;
}

DsResult ds_device_set_speaker_config(DsDevice *d, uint32_t config) {
    if (!d) return DSERR_INVALIDPARAM;
    std::lock_guard<std::mutex> g(d->m);
    d->speaker = config;
    return DS_OK;
}

DsResult ds_device_create_buffer(DsDevice *d, uint32_t flags, uint32_t bytes, const void *wfx, void *mem,
                                 DsBuffer **out) {
    if (!d || !out) return DSERR_INVALIDPARAM;
    *out = nullptr;
    auto b = std::make_unique<DsBuffer>();
    b->dev = d;
    b->flags = flags;
    if (flags & DSBCAPS_PRIMARYBUFFER) {
        if (bytes || wfx || (flags & (DSBCAPS_CTRLFREQUENCY | DSBCAPS_CTRLFX | DSBCAPS_CTRLPOSITIONNOTIFY)))
            return DSERR_INVALIDPARAM;
        // Each call gets its own handle; all primary handles share the device's format and master volume.
        std::lock_guard<std::mutex> g(d->m);
        b->primary = true;
        b->fmt = d->primary_fmt;
        b->freq = (uint32_t)b->fmt.rate;
        b->nframes = (uint32_t)(b->fmt.rate / 10);
        b->bytes = b->nframes * b->fmt.block;
        b->volume = d->master_volume;
        *out = b.get();
        d->buffers.push_back(b.release());
        return DS_OK;
    }
    if (!parse_format(wfx, b->fmt)) {
        DSLOG("CreateSoundBuffer: unsupported format");
        return wfx ? DSERR_BADFORMAT : DSERR_INVALIDPARAM;
    }
    if (bytes < 4 || bytes > 0x0FFFFFFF || !mem) return DSERR_INVALIDPARAM;
    // DirectSound rejects pan on multichannel buffers and 3D on anything but mono
    if (b->fmt.channels > 2 && (flags & DSBCAPS_CTRLPAN)) return DSERR_INVALIDPARAM;
    if (b->fmt.channels != 1 && (flags & DSBCAPS_CTRL3D)) return DSERR_INVALIDPARAM;
    b->nframes = bytes / b->fmt.block;
    if (!b->nframes) return DSERR_INVALIDPARAM;
    b->bytes = b->nframes * b->fmt.block;
    b->mem = (uint8_t *)mem;
    b->freq = (uint32_t)b->fmt.rate;
    b->routes = make_routes(b->fmt);
    std::lock_guard<std::mutex> g(d->m);
    if (!d->alive) return DSERR_UNINITIALIZED;
    set_step(*b, d->cfg.mix_rate);
    update_gains(*b, d->master);
    DSLOG("CreateSoundBuffer: %d ch %d-bit%s %d Hz, %u bytes, flags %08x", b->fmt.channels, b->fmt.bits,
          b->fmt.is_float ? " float" : "", b->fmt.rate, b->bytes, flags);
    *out = b.get();
    d->buffers.push_back(b.release());
    return DS_OK;
}

DsResult ds_device_duplicate_buffer(DsDevice *d, DsBuffer *src, DsBuffer **out) {
    if (!d || !src || !out || src->primary) return DSERR_INVALIDPARAM;
    if (src->lost) return DSERR_BUFFERLOST;
    std::lock_guard<std::mutex> g(d->m);
    auto *b = new DsBuffer;
    b->dev = d;
    b->flags = src->flags;
    b->fmt = src->fmt;
    b->mem = src->mem;
    b->bytes = src->bytes;
    b->nframes = src->nframes;
    b->routes = src->routes;
    b->freq = src->freq;
    b->volume = src->volume;
    b->pan = src->pan;
    set_step(*b, d->cfg.mix_rate);
    update_gains(*b, d->master);
    d->buffers.push_back(b);
    *out = b;
    return DS_OK;
}

DsResult ds_device_render(DsDevice *d, float *pcm, size_t frames) {
    if (!d || (!pcm && frames)) return DSERR_INVALIDPARAM;
    if (d->cfg.sink != DS_SINK_MANUAL) return DSERR_INVALIDCALL;
    std::vector<uint32_t> events;
    const size_t bs = (size_t)d->cfg.block_frames;
    std::vector<float> chunk;
    for (size_t done = 0; done < frames;) {
        size_t n = std::min(bs, frames - done);
        {
            std::lock_guard<std::mutex> g(d->m);
            mix_block(*d, n, events);
            chunk.assign(d->block.begin(), d->block.end());
        }
        memcpy(pcm + done * kOut, chunk.data(), n * kOut * sizeof(float));
        deliver(*d, chunk.data(), n, events);
        done += n;
    }
    return DS_OK;
}

DsResult ds_device_get_stats(DsDevice *d, DsStats *s) {
    if (!d || !s) return DSERR_INVALIDPARAM;
    std::lock_guard<std::mutex> g(d->m);
    *s = DsStats{};
    s->frames_written = d->written;
    s->frames_played = played_now(*d);
    s->underruns = d->underruns;
    for (DsBuffer *b : d->buffers) {
        if (b->primary) continue;
        ++s->buffers;
        if (b->state != State::Stopped) ++s->playing;
    }
    for (int c = 0; c < kOut; ++c) {
        s->peak[c] = d->peak[c];
        s->rms[c] = d->stat_frames ? std::sqrt(d->sumsq[c] / (double)d->stat_frames) : 0.0;
        s->clipped[c] = d->clipped[c];
        s->dip[c] = d->dip[c] > 1.5f ? -1.0f : d->dip[c];
        d->dip[c] = 2;
        d->peak[c] = 0;
        d->sumsq[c] = 0;
        d->clipped[c] = 0;
    }
    s->window = d->stat_frames;
    s->limited = d->limited;
    s->limit_min_gain = d->limiter.take_min_gain();
    d->limited = 0;
    d->stat_frames = 0;
    return DS_OK;
}

DsResult ds_device_output_info(DsDevice *d, DsOutputMode *mode, int *live, uint64_t *underruns) {
    if (!d) return DSERR_INVALIDPARAM;
    std::lock_guard<std::mutex> g(d->m);
    *mode = d->cfg.mode;
    *live = d->cfg.sink != DS_SINK_DEVICE ? 0 : d->detached ? -1 : 1;
    *underruns = d->underruns;
    return DS_OK;
}

// ------------------------------------------------------------------ buffers
void ds_buffer_release(DsBuffer *b) {
    if (!b) return;
    DsDevice *d = b->dev;
    bool free_dev;
    {
        std::lock_guard<std::mutex> g(d->m);
        d->buffers.erase(std::remove(d->buffers.begin(), d->buffers.end(), b), d->buffers.end());
        free_dev = !d->alive && d->buffers.empty();
    }
    delete b;
    if (free_dev) destroy_device(d);
}

DsResult ds_buffer_get_caps(DsBuffer *b, void *caps) {
    if (DsResult r = check_live(b)) return r;
    if (!caps || rd32((const uint8_t *)caps) != 20) return DSERR_INVALIDPARAM;
    std::lock_guard<std::mutex> g(b->dev->m);
    uint8_t *p = (uint8_t *)caps;
    uint32_t f = b->flags;
    if (!(f & (DSBCAPS_LOCHARDWARE | DSBCAPS_LOCSOFTWARE))) f |= DSBCAPS_LOCSOFTWARE;
    wr32(p + 4, f);
    wr32(p + 8, b->bytes);
    wr32(p + 12, 0);
    wr32(p + 16, 0);
    return DS_OK;
}

DsResult ds_buffer_get_format(DsBuffer *b, void *wfx, uint32_t capacity, uint32_t *written) {
    if (DsResult r = check_live(b)) return r;
    std::lock_guard<std::mutex> g(b->dev->m);
    const Format &f = b->primary ? b->dev->primary_fmt : b->fmt;
    if (written) *written = f.raw_len;
    if (!wfx) return written ? DS_OK : DSERR_INVALIDPARAM;
    if (capacity < f.raw_len) return DSERR_INVALIDPARAM;
    memcpy(wfx, f.raw, f.raw_len);
    return DS_OK;
}

DsResult ds_buffer_set_format(DsBuffer *b, const void *wfx) {
    if (DsResult r = check_live(b)) return r;
    if (!b->primary) return DSERR_INVALIDCALL;
    DsDevice *d = b->dev;
    Format f;
    {
    std::lock_guard<std::mutex> g(d->m);
    if (d->coop < DSSCL_PRIORITY) return DSERR_PRIOLEVELNEEDED;
    if (!parse_format(wfx, f)) return DSERR_BADFORMAT;
    d->primary_fmt = f;
    d->format_rate = f.rate;
    for (DsBuffer *o : d->buffers)
        if (o->primary) {
            o->fmt = f;
            o->freq = (uint32_t)f.rate;
            o->nframes = (uint32_t)(f.rate / 10);
            o->bytes = o->nframes * f.block;
        }
    DSLOG("primary SetFormat: %d ch %d-bit %d Hz mask %#x", f.channels, f.bits, f.rate, f.mask);
    }
    // Auto rate: mix at the game's own rate (Snd uses 44100), so its 6-ch stream reaches CoreAudio unresampled.
    if (d->follow_rate && f.rate >= 8000 && f.rate <= 192000) set_mix_rate(*d, f.rate);
    return DS_OK;
}

DsResult ds_buffer_get_current_position(DsBuffer *b, uint32_t *play, uint32_t *write) {
    if (DsResult r = check_live(b)) return r;
    DsDevice *d = b->dev;
    std::lock_guard<std::mutex> g(d->m);
    uint64_t played = played_now(*d);
    if (b->primary) {
        // the primary buffer "plays" continuously at its format's rate
        uint64_t f = played * (uint64_t)b->fmt.rate / (uint64_t)d->cfg.mix_rate;
        uint64_t w = f + (uint64_t)b->fmt.rate / 100;
        if (play) *play = (uint32_t)(f % b->nframes) * b->fmt.block;
        if (write) *write = (uint32_t)(w % b->nframes) * b->fmt.block;
        return DS_OK;
    }
    uint64_t p = play_upos(*b, played, d->cfg.mix_rate);
    uint64_t w = b->upos;
    if (b->state == State::Playing && (b->step != kOne || b->frac)) w += 3;  // the resampler reads ahead
    if (b->state == State::Draining) w = b->nframes;
    if (!b->looping) w = std::min<uint64_t>(w, b->nframes);
    if (play) *play = wrap_frame(*b, p) * b->fmt.block;
    if (write) *write = wrap_frame(*b, w) * b->fmt.block;
    return DS_OK;
}

DsResult ds_buffer_set_current_position(DsBuffer *b, uint32_t offset) {
    if (DsResult r = check_live(b)) return r;
    if (b->primary) return DSERR_INVALIDCALL;
    DsDevice *d = b->dev;
    std::lock_guard<std::mutex> g(d->m);
    if (offset >= b->bytes) return DSERR_INVALIDPARAM;
    uint32_t fr = offset / b->fmt.block;
    if (b->state == State::Draining) b->state = State::Playing;
    b->upos = b->upos - wrap_frame(*b, b->upos) + fr;  // same lap; the cursor floors below are reset
    b->idx = fr;
    b->frac = 0;
    b->start_upos = b->last_play = b->notify_upos = b->upos;
    b->anchor = d->written;
    return DS_OK;
}

DsResult ds_buffer_lock(DsBuffer *b, uint32_t offset, uint32_t bytes, uint32_t flags, uint32_t *off1, uint32_t *len1,
                        uint32_t *off2, uint32_t *len2) {
    if (DsResult r = check_live(b)) return r;
    if (!off1 || !len1) return DSERR_INVALIDPARAM;
    if (b->primary) {
        std::lock_guard<std::mutex> g(b->dev->m);
        return b->dev->coop == DSSCL_WRITEPRIMARY ? DSERR_UNSUPPORTED : DSERR_PRIOLEVELNEEDED;
    }
    if (flags & DSBLOCK_FROMWRITECURSOR) {
        uint32_t w = 0;
        ds_buffer_get_current_position(b, nullptr, &w);
        offset = w;
    }
    if (flags & DSBLOCK_ENTIREBUFFER) bytes = b->bytes;
    if (!bytes || bytes > b->bytes || offset >= b->bytes) return DSERR_INVALIDPARAM;
    uint32_t first = std::min(bytes, b->bytes - offset);
    *off1 = offset;
    *len1 = first;
    // Without a second region the lock is truncated at the end of the buffer (what Windows/Wine do; Snd's
    // streamer passes NULL there and splits its locks itself).
    if (off2) *off2 = 0;
    if (len2) *len2 = off2 ? bytes - first : 0;
    return DS_OK;
}

DsResult ds_buffer_unlock(DsBuffer *b, uint32_t off1, uint32_t len1, uint32_t off2, uint32_t len2) {
    if (DsResult r = check_live(b)) return r;
    // The mixer reads the memory directly, so there is nothing to commit; just check the regions.
    if (b->primary) return DSERR_INVALIDCALL;
    if ((uint64_t)off1 + len1 > b->bytes || (len2 && (uint64_t)off2 + len2 > b->bytes)) return DSERR_INVALIDPARAM;
    return DS_OK;
}

DsResult ds_buffer_play(DsBuffer *b, uint32_t flags) {
    if (DsResult r = check_live(b)) return r;
    if (b->primary) return DS_OK;  // the primary always runs
    DsDevice *d = b->dev;
    std::lock_guard<std::mutex> g(d->m);
    bool loop = (flags & DSBPLAY_LOOPING) != 0;
    if (b->state == State::Playing) {
        b->looping = loop;
        return DS_OK;
    }
    if (b->state == State::Draining) {  // a finished one-shot, not yet heard to the end: start over
        b->upos = b->idx = 0;
        b->frac = 0;
    }
    b->looping = loop;
    b->idx = wrap_frame(*b, b->upos);
    b->state = State::Playing;
    b->mix_freq = b->freq;
    b->start_upos = b->last_play = b->notify_upos = b->upos;
    b->anchor = d->written;
    b->gain_cur = b->gain_tgt;
    return DS_OK;
}

DsResult ds_buffer_stop(DsBuffer *b) {
    if (DsResult r = check_live(b)) return r;
    if (b->primary) return DS_OK;
    DsDevice *d = b->dev;
    std::vector<uint32_t> events;
    {
        std::lock_guard<std::mutex> g(d->m);
        stop_buffer(*b, events);
    }
    if (d->cfg.notify)
        for (uint32_t e : events) d->cfg.notify(d->cfg.notify_user, e);
    return DS_OK;
}

DsResult ds_buffer_get_status(DsBuffer *b, uint32_t *status) {
    if (!status) return DSERR_INVALIDPARAM;
    if (!b) return DSERR_INVALIDPARAM;
    if (b->lost) {
        *status = DSBSTATUS_BUFFERLOST;
        return DS_OK;
    }
    DsDevice *d = b->dev;
    std::lock_guard<std::mutex> g(d->m);
    uint32_t s = DSBSTATUS_LOCSOFTWARE;
    if (b->primary) s |= DSBSTATUS_PLAYING | DSBSTATUS_LOOPING;
    else if (b->state != State::Stopped) s |= DSBSTATUS_PLAYING | (b->looping ? DSBSTATUS_LOOPING : 0);
    if (!b->primary && b->state == State::Draining && played_now(*d) >= b->anchor)
        s &= ~(uint32_t)(DSBSTATUS_PLAYING | DSBSTATUS_LOOPING);  // heard to the end; the mixer stops it next tick
    *status = s;
    return DS_OK;
}

DsResult ds_buffer_set_volume(DsBuffer *b, int32_t v) {
    if (DsResult r = check_live(b)) return r;
    if (!(b->flags & DSBCAPS_CTRLVOLUME)) return DSERR_CONTROLUNAVAIL;
    if (v < DSBVOLUME_MIN || v > DSBVOLUME_MAX) return DSERR_INVALIDPARAM;
    DsDevice *d = b->dev;
    std::lock_guard<std::mutex> g(d->m);
    b->volume = v;
    if (b->primary) {
        d->master_volume = v;
        d->master = db100_to_gain(v);
        for (DsBuffer *o : d->buffers)
            if (!o->primary) update_gains(*o, d->master);
    } else {
        update_gains(*b, d->master);
    }
    return DS_OK;
}

DsResult ds_buffer_get_volume(DsBuffer *b, int32_t *v) {
    if (DsResult r = check_live(b)) return r;
    if (!v) return DSERR_INVALIDPARAM;
    if (!(b->flags & DSBCAPS_CTRLVOLUME)) return DSERR_CONTROLUNAVAIL;
    std::lock_guard<std::mutex> g(b->dev->m);
    *v = b->primary ? b->dev->master_volume : b->volume;
    return DS_OK;
}

DsResult ds_buffer_set_pan(DsBuffer *b, int32_t pan) {
    if (DsResult r = check_live(b)) return r;
    if (!(b->flags & DSBCAPS_CTRLPAN)) return DSERR_CONTROLUNAVAIL;
    if (pan < DSBPAN_LEFT || pan > DSBPAN_RIGHT) return DSERR_INVALIDPARAM;
    std::lock_guard<std::mutex> g(b->dev->m);
    b->pan = pan;
    if (!b->primary) update_gains(*b, b->dev->master);
    return DS_OK;
}

DsResult ds_buffer_get_pan(DsBuffer *b, int32_t *pan) {
    if (DsResult r = check_live(b)) return r;
    if (!pan) return DSERR_INVALIDPARAM;
    if (!(b->flags & DSBCAPS_CTRLPAN)) return DSERR_CONTROLUNAVAIL;
    std::lock_guard<std::mutex> g(b->dev->m);
    *pan = b->pan;
    return DS_OK;
}

DsResult ds_buffer_set_frequency(DsBuffer *b, uint32_t hz) {
    if (DsResult r = check_live(b)) return r;
    if (b->primary || !(b->flags & DSBCAPS_CTRLFREQUENCY)) return DSERR_CONTROLUNAVAIL;
    if (hz == DSBFREQUENCY_ORIGINAL) hz = (uint32_t)b->fmt.rate;
    if (hz < DSBFREQUENCY_MIN || hz > DSBFREQUENCY_MAX) return DSERR_INVALIDPARAM;
    DsDevice *d = b->dev;
    std::lock_guard<std::mutex> g(d->m);
    b->freq = hz;
    set_step(*b, d->cfg.mix_rate);
    return DS_OK;
}

DsResult ds_buffer_get_frequency(DsBuffer *b, uint32_t *hz) {
    if (DsResult r = check_live(b)) return r;
    if (!hz) return DSERR_INVALIDPARAM;
    if (!(b->flags & DSBCAPS_CTRLFREQUENCY) && !b->primary) return DSERR_CONTROLUNAVAIL;
    std::lock_guard<std::mutex> g(b->dev->m);
    *hz = b->freq;
    return DS_OK;
}

DsResult ds_buffer_restore(DsBuffer *b) {
    if (!b) return DSERR_INVALIDPARAM;
    return b->lost ? DSERR_BUFFERLOST : DS_OK;
}

DsResult ds_buffer_set_notification_positions(DsBuffer *b, uint32_t count, const uint32_t *offsets,
                                              const uint32_t *events) {
    if (DsResult r = check_live(b)) return r;
    if (b->primary || !(b->flags & DSBCAPS_CTRLPOSITIONNOTIFY)) return DSERR_CONTROLUNAVAIL;
    if (count && (!offsets || !events)) return DSERR_INVALIDPARAM;
    std::lock_guard<std::mutex> g(b->dev->m);
    if (b->state != State::Stopped) return DSERR_INVALIDCALL;
    std::vector<Notify> v;
    for (uint32_t i = 0; i < count; ++i) {
        if (offsets[i] == DSBPN_OFFSETSTOP) v.push_back({0, events[i], true});
        else if (offsets[i] >= b->bytes) return DSERR_INVALIDPARAM;
        else v.push_back({offsets[i] / b->fmt.block, events[i], false});
    }
    std::stable_sort(v.begin(), v.end(), [](const Notify &a, const Notify &c) { return a.frame < c.frame; });
    b->notifies = v;
    return DS_OK;
}

}  // extern "C"
