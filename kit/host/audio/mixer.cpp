// mixer.cpp - host_audio_play and friends, on a software mixer.
//
// The shims hand out one flat channel numbering for DirectSound secondary
// buffers and QMixer channels alike (dx_alloc_audio_channel), so this file
// sees a small set of integers, each of which owns a player: a queue of
// scheduled sample buffers the render loop resamples and sums.
//
// Three things about DirectSound decide the shape of this file:
//
//   * A buffer has a play cursor that survives a Stop. GetCurrentPosition
//     after a Stop reports where it stopped, and a Play afterwards resumes
//     there. So a channel keeps its own byte cursor rather than asking the
//     player, which knows nothing once it is stopped.
//   * Pan is an attenuation of one channel, not a position (see audio.h). It
//     is applied to the samples, which is also what lets it be exact.
//   * SetFrequency takes effect immediately, mid-sound. A rate change
//     re-schedules the rest of the buffer at the new rate from the cursor it
//     had reached. The sound continues; it just continues faster.
//
// Everything the guest points at is copied on the way in: `pcm` is guest
// memory that is only valid for the duration of the call, and a channel may
// be re-scheduled long afterwards.
//
// LOCKS. Three, in a fixed order, and the order is checked rather than
// remembered:
//
//   * g_api_mutex, the outer lock, serialises the host_audio_* calls.
//   * g_data_mutex, over the channel table. Held briefly; nothing that touches
//     a player is called while it is held (audio_check_unlocked counts any
//     breach, and the count is a test seam).
//   * g_render_mutex, over the players. The render thread holds it for a
//     block; the API takes it to schedule, stop and read positions, after the
//     data lock is dropped.
//
// Completions are lock-free atomics, fired from the render loop: a stale
// generation names a playback that is already over and is ignored.
#include "../../platform/os.h"
#include "../audio.h"
#include "../audio_capture.h"
#include "sink.h"
#include "../../dx/host_api.h"
#include "../../mods/pop_mod_api.h"
// The arithmetic lives in audio_math.cpp; this file is the engine.

#include <math.h>
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include <assert.h>

#include <atomic>
#include <deque>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

// How many samples the clipper had to take the top off, and the worst one it
// saw. Both are written from the render thread and read from anywhere, so both
// are relaxed atomics; neither is used for anything but the report.
std::atomic<uint64_t> g_clipped_samples{0};
std::atomic<uint32_t> g_worst_over_bits{0}; // a float, bit for bit

namespace {

// --- the trace, stamped with the capture's own clock ------------------------
bool trace_on() {
    static int on = -1;
    if (on < 0)
        on = recomp_env("HOST_TRACE_AUDIO") ? 1 : 0;
    return on != 0;
}

void trace(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void trace(const char *fmt, ...) {
    if (!trace_on())
        return;
    printf("[audio %8.3f] ", host_capture_now());
    va_list args;
    va_start(args, fmt);
    vprintf(fmt, args);
    va_end(args);
    printf("\n");
    fflush(stdout);
}

// --- the players -------------------------------------------------------------
//
// A segment is one scheduled buffer: stereo floats at its own rate with the
// pan already applied. A player plays its segments in order, resampling each
// to the render rate with a six-tap Lanczos kernel. Band-limited
// interpolation of a signal already at full scale lands above it - which is
// what the clipper is for, and what its test measures.
struct Segment {
    std::vector<float> l, r, extra[4];
    unsigned channels = 2;
    const std::vector<float> &plane(unsigned c) const {
        return c == 0 ? l : c == 1 ? r : extra[c - 2];
    }
    std::vector<float> &plane(unsigned c) {
        return c == 0 ? l : c == 1 ? r : extra[c - 2];
    }
    uint32_t rate = 22050;
    bool loop = false;
    int completion = 0; // 0 none, 1 a generation, 2 queued bytes
    uint64_t generation = 0;
    uint32_t bytes = 0;
    uint32_t frames() const {
        return uint32_t(l.size());
    }
};
struct Player {
    std::deque<std::shared_ptr<Segment>> segments;
    double pos = 0;    // within the front segment, in its frames
    double played = 0; // frames rendered since play(), at the source rates
    float volume = 1.0f;
    bool playing = false;
};

std::mutex g_render_mutex;
std::map<int32_t, Player> g_players; // render mutex
double g_render_rate = 48000.0;      // render mutex, and read by the clock
std::unique_ptr<AudioSink> g_sink;
unsigned g_sink_channels = 2; // API mutex; expand on the first discrete source
HostAudioMusicRender g_music = nullptr;
void *g_music_context = nullptr;

float lanczos3(float x) {
    if (x == 0.0f)
        return 1.0f;
    if (x <= -3.0f || x >= 3.0f)
        return 0.0f;
    const float px = float(M_PI) * x;
    return 3.0f * sinf(px) * sinf(px / 3.0f) / (px * px);
}

// One frame, resampled with the same kernel on every retained channel.
inline void sample_at(const Segment &s, double pos, float *values) {
    const int n = int(s.frames()), i = int(pos);
    const float frac = float(pos - i);
    for (int k = -2; k <= 3; ++k) {
        int j = i + k;
        if (s.loop) {
            j %= n;
            if (j < 0)
                j += n;
        } else if (j < 0)
            j = 0;
        else if (j >= n)
            j = n - 1;
        const float w = lanczos3(frac - float(k));
        for (unsigned c = 0; c < s.channels; ++c)
            values[c] += s.plane(c)[j] * w;
    }
}

// Render mutex held. Adds `frames` of this player into the mix.
void render_player(int32_t id, Player &p, float *const *out, uint32_t frames) {
    for (uint32_t i = 0; i < frames && p.playing; ++i) {
        if (p.segments.empty()) {
            p.playing = false;
            break;
        }
        Segment &s = *p.segments.front();
        const uint32_t n = s.frames();
        if (!n) {
            p.segments.pop_front();
            continue;
        }
        float values[6] = {};
        sample_at(s, p.pos, values);
        for (unsigned c = 0; c < s.channels; ++c)
            out[c][i] += values[c] * p.volume;
        const double step = double(s.rate) / g_render_rate;
        p.pos += step;
        p.played += step;
        while (p.pos >= n) {
            p.pos -= n;
            if (s.loop)
                continue;
            // Finished: the completion is lock-free, as the players are.
            if (s.completion == 1)
                host_audio_completed(id, s.generation);
            else if (s.completion == 2)
                host_audio_queue_completed(id, s.bytes);
            p.segments.pop_front();
            if (p.segments.empty()) {
                p.playing = false;
                break;
            }
            break; // the next segment starts at the carried-over position
        }
    }
}

// NFSMW 3.0.4 (an idea from SpeedBreaker, github.com/SpeedBreakerProject/speedbreaker; no code taken): silence while the window is in the background (the core mod's mute_in_background, set by the host's
// focus events). The mix still runs and the device still pulls, so every cursor and stream keeps time; only what
// goes out is zero.
std::atomic<bool> g_background_mute{false};

// The whole mix for one block, into deinterleaved buffers that arrive zeroed.
// Voices, then the music, then the clipper, then the capture.
void render_block(float *const *out, uint32_t frames) {
    uint32_t busy = 0;
    {
        std::lock_guard<std::mutex> render(g_render_mutex);
        for (auto &entry : g_players) {
            if (entry.second.playing)
                ++busy;
            render_player(entry.first, entry.second, out, frames);
        }
        if (g_music)
            g_music(g_music_context, out[0], out[1], frames);
    }
    // The clipper: the identity below full scale, flat above it, which is the
    // map the original's 16-bit sum performs. Nothing rides the gain.
    uint64_t clipped = 0;
    float worst = 0.0f;
    for (unsigned c = 0; c < 6; ++c)
        for (uint32_t i = 0; i < frames; ++i) {
            const float v = out[c][i];
            const float mag = v < 0.0f ? -v : v;
            if (mag <= 1.0f)
                continue;
            if (mag > worst)
                worst = mag;
            ++clipped;
            out[c][i] = v < 0.0f ? -1.0f : 1.0f;
        }
    if (clipped) {
        g_clipped_samples.fetch_add(clipped, std::memory_order_relaxed);
        uint32_t bits;
        memcpy(&bits, &worst, sizeof bits);
        uint32_t seen = g_worst_over_bits.load(std::memory_order_relaxed);
        for (;;) {
            float had;
            memcpy(&had, &seen, sizeof had);
            if (had >= worst)
                break;
            if (g_worst_over_bits.compare_exchange_weak(seen, bits, std::memory_order_relaxed))
                break;
        }
    }
    if (host_capture_active())
        host_capture_write(out[0], out[1], frames,
                           int(busy)); // legacy capture is front stereo only
    if (g_background_mute.load(std::memory_order_relaxed))
        for (unsigned c = 0; c < 6; ++c)
            memset(out[c], 0, frames * sizeof(float));
}

struct Channel {
    std::vector<uint8_t> pcm;    // the whole buffer, copied
    int bits = 16, channels = 2; // the source format
    uint32_t base_rate = 22050;  // the rate the buffer was made with
    uint32_t rate = 0;           // the rate it is playing at now
    // True once SetFrequency has retuned this channel away from its buffer's
    // own rate. Without it a channel could not tell "nobody has asked for a
    // rate" from "somebody asked for exactly the default", and a first Play of
    // a 44100 Hz buffer would inherit a 22050 Hz default and run at half speed.
    bool rate_overridden = false;
    bool loop = false;

    int32_t volume_mb = 0, pan_mb = 0;
    uint32_t start_offset = 0; // where the current scheduling began
    uint32_t cursor = 0;       // retained when not playing
    // When this playback started, on the audio clock. The cursor is modelled
    // from it whenever the player is not reporting progress of its own.
    double started = 0.0;
    uint64_t generation = 0;
    bool playing = false;
    // Whether the last thing submitted was silence, so a refill that turns a
    // silent loop into a sounding one can be reported.
    bool silent = true;
    // True once this channel became a stream. A streamed channel has no total
    // length to run out of, so its cursor is not modelled against one.
    bool streaming = false;
    // When it became one, and how far into the sound it was at that moment.
    double stream_started = 0.0;
    uint32_t stream_base = 0;
    // The re-issued sound's remaining length at the moment of conversion.
    uint32_t stream_head = 0;

    // A ring played in place: one segment, scheduled once, looping for ever,
    // whose samples the guest's writes overwrite where they lie.
    std::shared_ptr<Segment> ring;
    bool ring_mode = false;
    uint32_t ring_head_frames = 0; // the one-shot played before the loop
    uint32_t ring_from_frame = 0;  // where in the ring that one-shot started
    float ring_tail_l = 0.0f, ring_tail_r = 0.0f;
    uint32_t ring_tail_at = 0xffffffffu;
    uint32_t ring_writes = 0, ring_breaks = 0;
    uint32_t ring_wraps = 0, ring_breaks_at_wrap = 0, ring_unchecked = 0;
    float ring_worst_break = 0.0f;
    uint64_t ring_inside_steps = 0, ring_inside_pairs = 0;
    // What the clock counted while there was nothing to play.
    uint64_t stream_skew = 0;
};

double now_seconds() {
    return (double)os_monotonic_ns() * 1e-9;
}

// --- the clock a play cursor runs on ----------------------------------------
//
// The cursor counts what has been rendered whenever anything is rendering by
// hand, and wall-clock seconds otherwise: rendered offline, the audio runs at
// whatever speed the host renders it, and a cursor on the wall clock would
// come apart from it by whatever factor that is.
std::atomic<bool> g_manual_render{false};
std::atomic<uint64_t> g_rendered_frames{0};

double audio_clock() {
    if (g_manual_render.load(std::memory_order_acquire) && g_render_rate > 0.0)
        return (double)g_rendered_frames.load(std::memory_order_acquire) / g_render_rate;
    return now_seconds();
}

std::map<int32_t, Channel> *g_channels = nullptr;
bool g_engine = false; // there is a mixer at all
bool g_disabled = false;
const HostAudioNodeOps *g_ops = nullptr;

std::mutex g_api_mutex;
std::mutex g_data_mutex;

const int32_t MAX_AUDIO_CHANNELS = 256;
std::atomic<uint64_t> g_finished[MAX_AUDIO_CHANNELS];
std::atomic<uint64_t> g_queued_total[MAX_AUDIO_CHANNELS];
std::atomic<uint64_t> g_queued_played[MAX_AUDIO_CHANNELS];
std::atomic<uint32_t> g_lock_violations{0};

thread_local int t_data_depth = 0;

struct DataLock {
    std::lock_guard<std::mutex> held;
    DataLock() : held(g_data_mutex) {
        ++t_data_depth;
    }
    ~DataLock() {
        --t_data_depth;
    }
};

void log_once(const char *message) {
    static std::map<std::string, bool> seen;
    if (seen[message])
        return;
    seen[message] = true;
    fprintf(stderr, "[host] %s\n", message);
}

float segment_peak(const Segment *s) {
    if (!s)
        return 0.0f;
    float peak = 0.0f;
    for (unsigned c = 0; c < s->channels; ++c)
        for (float x : s->plane(c)) {
            const float a = x < 0 ? -x : x;
            if (a > peak)
                peak = a;
        }
    return peak;
}

// Called immediately before every player call. The data lock must not be held
// here: a player call takes the render lock, and the render thread never
// waits on the data lock, so this is not a deadlock any more - but the rule
// keeps the two halves of the file apart, and the count is a test seam.
void audio_check_unlocked(const char *what) {
    if (t_data_depth == 0)
        return;
    g_lock_violations.fetch_add(1, std::memory_order_relaxed);
    fprintf(stderr, "[host] audio: %s was called while the channel lock was held\n", what);
    assert(t_data_depth == 0 && "no player call may hold the audio channel lock");
}

bool ensure_engine() {
    if (g_ops) {
        if (!g_channels)
            g_channels = new std::map<int32_t, Channel>();
        return true;
    }
    if (g_channels && g_engine)
        return true;
    if (g_disabled)
        return false;
    if (recomp_env("HOST_NO_AUDIO")) {
        g_disabled = true;
        return false;
    }
    if (!g_channels)
        g_channels = new std::map<int32_t, Channel>();
    if (!g_engine) {
        g_engine = true;
        log_once("audio: the mix is summed at the game's own gains and "
                 "saturated sample by sample above full scale, as the "
                 "original clips; nothing below full scale is touched");
    }
    return true;
}

// Called with no data lock held, before anything is scheduled. Opens the
// output device unless the graph is being rendered by hand.
void ensure_running(unsigned channels = 2) {
    if (!g_engine)
        return;
    audio_check_unlocked("starting the output");
    if (g_manual_render.load(std::memory_order_acquire))
        return;
    // Keep the stream wide once a discrete source appears so another stereo
    // voice cannot truncate an already-playing surround buffer. SDL converts
    // to the actual device layout, including downmix on stereo speakers.
    if (channels == 6 && g_sink_channels != 6) {
        if (g_sink) {
            g_sink->stop();
            g_sink.reset();
        }
        g_sink_channels = 6;
    }
    if (g_sink && g_sink->running())
        return;
    // render_block's planes have these established meanings. Preserve that
    // contract explicitly at the device boundary, not just its channel count.
    const audio::Layout &layout = g_sink_channels == 6 ? audio::surround51 : audio::stereo;
    if (!g_sink) {
        // A Dolby Digital bitstream when the options ask for it and the mix is
        // discrete; the PCM path whenever the device cannot take one.
        uint32_t mode = 0, flags = 0;
        host_audio_get_output(&mode, &flags);
        if ((flags & POP_AUDIO_DOLBY_DIGITAL) && g_sink_channels == 6) {
            g_sink = make_dolby_sink();
            if (g_sink && g_sink->start(g_render_rate, layout, render_block))
                return;
            if (g_sink)
                log_once("audio: Dolby Digital output is unavailable on this device, so the "
                         "mix is sent as PCM");
            g_sink.reset();
        }
        g_sink = make_sdl_sink();
    }
    if (!g_sink->start(g_render_rate, layout, render_block)) {
        log_once("audio: the output device would not open, so nothing will be heard");
        g_sink.reset();
    }
}

Channel *channel_for(int32_t id, bool create) {
    if (id < 0 || id >= MAX_AUDIO_CHANNELS) {
        log_once("audio: a channel number outside the range this host tracks was used; "
                 "it is ignored");
        return nullptr;
    }
    if (!ensure_engine())
        return nullptr;
    auto it = g_channels->find(id);
    if (it != g_channels->end())
        return &it->second;
    if (!create)
        return nullptr;
    (*g_channels)[id] = Channel();
    return &(*g_channels)[id];
}

// A completion that named this channel's current playback means a one-shot
// ran out. Callers hold the data lock.
void reconcile_locked(int32_t id, Channel &ch) {
    if (id < 0 || id >= MAX_AUDIO_CHANNELS)
        return;
    uint64_t finished = g_finished[id].load(std::memory_order_acquire);
    if (!ch.playing || ch.loop || ch.streaming || finished != ch.generation)
        return;
    ch.playing = false;
    ch.cursor = (uint32_t)ch.pcm.size();
}

uint32_t align_down(uint32_t bytes, uint32_t frame) {
    return frame ? bytes - (bytes % frame) : bytes;
}

// Decode a scheduled buffer without losing channel layout. Pan applies only
// to the front pair, matching the existing mono/stereo DirectSound behavior.
uint32_t decode_segment(Segment &s, const void *pcm, uint32_t bytes, int bits, int channels,
                        int32_t pan) {
    const uint32_t frames = bytes / host_audio_frame_bytes(bits, channels);
    s.channels = channels == 6 ? 6 : 2;
    float *planes[6] = {};
    for (unsigned c = 0; c < s.channels; ++c) {
        s.plane(c).assign(frames, 0.0f);
        planes[c] = s.plane(c).data();
    }
    const uint32_t written = host_audio_decode_planes(pcm, bytes, bits, channels, planes, frames);
    float lg = 1, rg = 1;
    host_audio_pan_gains(pan, &lg, &rg);
    for (uint32_t i = 0; i < written; ++i) {
        s.l[i] *= lg;
        s.r[i] *= rg;
    }
    for (unsigned c = 0; c < s.channels; ++c)
        s.plane(c).resize(written);
    return written;
}

std::shared_ptr<Segment> make_segment(const Channel &ch, uint32_t from, uint32_t to) {
    if (to <= from || to > ch.pcm.size())
        return nullptr;
    auto s = std::make_shared<Segment>();
    s->rate = ch.rate ? ch.rate : ch.base_rate;
    return decode_segment(*s, ch.pcm.data() + from, to - from, ch.bits, ch.channels, ch.pan_mb)
               ? s
               : nullptr;
}

// Everything one scheduling needs, gathered under the data lock and carried
// out without it.
struct Job {
    bool valid = false;
    int32_t id = 0;
    uint64_t generation = 0;
    std::shared_ptr<Segment> head;
    std::shared_ptr<Segment> whole; // non-null: loop this after the head
    bool head_loops = false;
    float volume = 1.0f;
    uint32_t from = 0, to = 0; // for the stand-in ops
    bool loop = false;
};

// Prepares a scheduling from `from` bytes. Callers hold the data lock; nothing
// here touches a player.
Job plan_locked(int32_t id, Channel &ch, uint32_t from) {
    Job job;
    uint32_t frame_bytes = host_audio_frame_bytes(ch.bits, ch.channels);
    uint32_t total = (uint32_t)ch.pcm.size();
    if (!frame_bytes || !total)
        return job;
    from = align_down(from < total ? from : 0, frame_bytes);

    ++ch.generation;
    ch.start_offset = from;
    ch.cursor = from;
    ch.started = audio_clock();
    ch.playing = true;

    job.valid = true;
    job.id = id;
    job.generation = ch.generation;
    job.volume = host_audio_gain_from_millibels(ch.volume_mb);
    job.from = from;
    job.to = total;
    job.loop = ch.loop;

    if (!g_ops) {
        job.head = make_segment(ch, from, total);
        if (!job.head) {
            ch.playing = false;
            job.valid = false;
            return job;
        }
        // A loop that began part-way through plays the tail once and then the
        // whole buffer for ever: the loop point is the start of the buffer, not
        // the offset the guest happened to start at.
        if (ch.loop && from != 0)
            job.whole = make_segment(ch, 0, total);
        else
            job.head_loops = ch.loop;
    }
    return job;
}

// The player calls. None of these may run with the data lock held.
void perform(const Job &job) {
    if (!job.valid)
        return;
    audio_check_unlocked("scheduling a buffer");
    if (g_ops) {
        if (g_ops->stop)
            g_ops->stop(job.id, job.generation);
        if (g_ops->schedule)
            g_ops->schedule(job.id, job.generation, job.from, job.to, job.loop ? 1 : 0);
        if (g_ops->play)
            g_ops->play(job.id, job.generation);
        return;
    }
    ensure_running(job.head->channels);
    {
        std::lock_guard<std::mutex> render(g_render_mutex);
        Player &p = g_players[job.id];
        p.segments.clear();
        p.pos = 0;
        p.played = 0;
        p.volume = job.volume;
        job.head->loop = job.head_loops;
        job.head->completion = job.whole || job.head_loops ? 0 : 1;
        job.head->generation = job.generation;
        p.segments.push_back(job.head);
        if (job.whole) {
            job.whole->loop = true;
            p.segments.push_back(job.whole);
        }
        p.playing = true;
    }
    // Once, for the first sound of the run: everything that decides whether it
    // is audible, in one line, so a silent run says which link is broken.
    static bool told = false;
    if (!told) {
        told = true;
        printf("[host] first buffer: channel %d, %u Hz %u ch, %u frames, peak %.3f, volume %.2f, "
               "render %.0f Hz, output %s\n",
               job.id, job.head->rate, job.head->channels, job.head->frames(),
               segment_peak(job.head.get()), job.volume, g_render_rate,
               g_manual_render.load() ? "offline"
                                      : (g_sink && g_sink->running() ? "device" : "none"));
        fflush(stdout);
    }
}

// The player's own idea of where it has reached, read without the data lock:
// frames rendered since play(), at the source rate.
bool node_sample_time(int32_t id, uint64_t *out) {
    audio_check_unlocked("reading the player time");
    int64_t frames = 0;
    if (g_ops) {
        if (!g_ops->sample_time || !g_ops->sample_time(id, &frames))
            return false;
    } else {
        std::lock_guard<std::mutex> render(g_render_mutex);
        auto it = g_players.find(id);
        if (it == g_players.end() || !it->second.playing)
            return false;
        frames = int64_t(it->second.played);
    }
    *out = frames > 0 ? (uint64_t)frames : 0;
    return true;
}

// Render mutex must not be held; stops the player and forgets its queue.
void player_stop(int32_t id) {
    std::lock_guard<std::mutex> render(g_render_mutex);
    auto it = g_players.find(id);
    if (it == g_players.end())
        return;
    it->second.segments.clear();
    it->second.playing = false;
    it->second.pos = 0;
}

} // namespace

// ---------------------------------------------------------------------------

extern "C" void host_audio_set_node_ops(const HostAudioNodeOps *ops) {
    std::lock_guard<std::mutex> api(g_api_mutex);
    DataLock held;
    g_ops = ops;
}

extern "C" void host_audio_queue_completed(int32_t id, uint32_t bytes) {
    if (id < 0 || id >= MAX_AUDIO_CHANNELS)
        return;
    g_queued_played[id].fetch_add(bytes, std::memory_order_release);
}

// A cursor that advances at the sample rate and never goes backwards. Callers
// hold the data lock.
uint32_t stream_cursor_locked(const Channel &ch) {
    if (!ch.streaming)
        return ch.cursor;
    uint32_t rate = ch.rate ? ch.rate : ch.base_rate;
    uint32_t frame = host_audio_frame_bytes(ch.bits, ch.channels);
    if (!rate || !frame)
        return ch.stream_base;
    double elapsed = audio_clock() - ch.stream_started;
    if (elapsed < 0.0)
        elapsed = 0.0;
    double bytes = elapsed * (double)rate * (double)frame;
    if (bytes > 4.0e18)
        bytes = 4.0e18;
    uint64_t played = (uint64_t)bytes;
    played -= played % frame;
    return (uint32_t)((uint64_t)ch.stream_base + played);
}

// Of everything appended to this stream, how much has been played. This is a
// clock and not a tally of completions: a completion arrives after the audio
// it belongs to has been heard, and a caller that refills when the tally
// reaches zero refills late. The clock over-counts by up to one lap at the
// start, on purpose: reporting more only makes it refill sooner.
uint64_t stream_played_locked(const Channel &ch) {
    uint32_t cursor = stream_cursor_locked(ch);
    uint64_t consumed = cursor > ch.stream_base ? (uint64_t)(cursor - ch.stream_base) : 0;
    return consumed > ch.stream_skew ? consumed - ch.stream_skew : 0;
}

extern "C" uint32_t host_audio_played_bytes(int32_t id) {
    std::lock_guard<std::mutex> api(g_api_mutex);
    // A ring played in place answers from the player and not from the clock:
    // this is the number the guest computes its next write offset from, so it
    // has to be where the output actually is.
    uint32_t from_frame = 0, frame_bytes = 0;
    bool ring = false;
    {
        DataLock held;
        Channel *channel = channel_for(id, false);
        if (!channel)
            return 0;
        if (!channel->ring_mode || g_ops)
            return stream_cursor_locked(*channel);
        ring = true;
        from_frame = channel->ring_from_frame;
        frame_bytes = host_audio_frame_bytes(channel->bits, channel->channels);
    }
    uint64_t frames = 0;
    if (ring && frame_bytes && node_sample_time(id, &frames))
        return (uint32_t)((from_frame + frames) * frame_bytes);
    DataLock held;
    Channel *channel = channel_for(id, false);
    return channel ? stream_cursor_locked(*channel) : 0;
}

// Convert an existing channel into an appendable stream and return its starting offset.
// Preserve the schedule of a live one-shot; a loop must first relinquish its loop schedule.
extern "C" int32_t host_audio_stream(int32_t id) {
    if (id < 0 || id >= MAX_AUDIO_CHANNELS)
        return -1;
    std::lock_guard<std::mutex> api(g_api_mutex);

    bool need_position = false;
    {
        DataLock held;
        Channel *channel = channel_for(id, false);
        if (!channel || channel->pcm.empty())
            return -1;
        reconcile_locked(id, *channel);
        if (channel->streaming)
            return (int32_t)stream_cursor_locked(*channel);
        need_position = channel->playing;
    }

    uint64_t sample_time = 0;
    bool have_time = need_position && node_sample_time(id, &sample_time);

    Job job;
    uint32_t from = 0;
    bool continuing_one_shot = false;
    {
        DataLock held;
        Channel *channel = channel_for(id, false);
        if (!channel)
            return -1;
        uint32_t total = (uint32_t)channel->pcm.size();
        from = channel->cursor;
        if (have_time)
            from =
                host_audio_position_bytes(sample_time, channel->start_offset, total, channel->bits,
                                          channel->channels, channel->loop ? 1 : 0);
        continuing_one_shot = channel->playing && !channel->loop && from < total;
        if (from >= total)
            from = 0;

        // A live one-shot is already appendable. Preserve its schedule,
        // generation and sample-time origin. A loop still needs its remaining
        // lap re-issued without loop flags; a stopped or completed voice still
        // needs to be started.
        channel->loop = false;
        channel->streaming = true;
        channel->stream_started = audio_clock();
        channel->stream_base = from;
        channel->stream_head = total > from ? total - from : 0;
        channel->stream_skew = 0;
        g_queued_total[id].store(0, std::memory_order_release);
        g_queued_played[id].store(0, std::memory_order_release);
        if (continuing_one_shot)
            channel->cursor = from;
        else
            job = plan_locked(id, *channel, from);
    }
    trace("stream  ch %d  continuing from byte %u%s", id, from,
          continuing_one_shot ? " without restarting the one-shot" : " after rescheduling");
    perform(job);
    printf("[host] audio channel %d is a stream now, continuing from byte %u\n", id, from);
    fflush(stdout);
    return (int32_t)from;
}

// What the queue said about itself, kept so a run can check the answer against
// the audio it actually held.
struct QueueHealth {
    double reporting = 0.0;  // seconds spent answering "not empty"
    double last = -1.0;      // when it was last asked
    uint64_t appended = 0;   // bytes ever handed over
    uint32_t rate = 0;       // bytes a second of that channel's audio
    uint32_t violations = 0; // answers larger than could possibly be left
};
QueueHealth g_queue_health[MAX_AUDIO_CHANNELS];

// What is STILL TO PLAY OF WHAT THE CALLER APPENDED - not of what the host is
// holding altogether. A stream answers from the clock; with stand-in ops there
// is no clock, so the tally of completions is the answer there.
extern "C" uint32_t host_audio_queued_bytes(int32_t id) {
    if (id < 0 || id >= MAX_AUDIO_CHANNELS)
        return 0;
    uint64_t appended = g_queued_total[id].load(std::memory_order_acquire);
    uint64_t played = g_queued_played[id].load(std::memory_order_acquire);

    if (!g_ops) {
        std::lock_guard<std::mutex> api(g_api_mutex);
        DataLock held;
        Channel *channel = channel_for(id, false);
        if (channel && channel->streaming)
            played = stream_played_locked(*channel);
    }

    {
        DataLock held;
        QueueHealth &h = g_queue_health[id];
        double now = audio_clock();
        if (h.last >= 0.0 && now > h.last && appended > played)
            h.reporting += now - h.last;
        h.last = now;
        h.appended = appended;
        if (appended > played && (appended - played) > appended)
            ++h.violations;
    }

    if (played >= appended) {
        uint64_t counted = g_queued_played[id].load(std::memory_order_acquire);
        if (counted > appended)
            g_queued_played[id].store(appended, std::memory_order_release);
        return 0;
    }
    return (uint32_t)(appended - played);
}

// A ring played where it lies, which is what a DirectSound streaming buffer
// actually is: one segment, scheduled once, looping for ever, its floats
// overwritten in place where the guest's bytes land. After the first call
// nothing here reschedules anything: no stop, no completion, no seam.
//
// `offset` is where in the ring the bytes go. Returns the bytes taken, or 0
// for a channel that has never been played.
extern "C" int32_t host_audio_write(int32_t id, const void *pcm, uint32_t offset, uint32_t bytes) {
    if (!pcm || !bytes || id < 0 || id >= MAX_AUDIO_CHANNELS)
        return 0;
    std::lock_guard<std::mutex> api(g_api_mutex);

    // Where the player has actually reached, read before the data lock is
    // taken because no player call may be made under it.
    bool ring_playing = false;
    uint32_t head_len = 0, head_from = 0, ring_frames = 0;
    {
        DataLock held;
        Channel *c0 = channel_for(id, false);
        if (c0 && c0->ring_mode && c0->ring) {
            ring_playing = true;
            head_len = c0->ring_head_frames;
            head_from = c0->ring_from_frame;
            ring_frames = c0->ring->frames();
        }
    }
    uint64_t node_frames = 0;
    bool have_head = ring_playing && ring_frames && node_sample_time(id, &node_frames);

    bool schedule_ring = false;
    std::shared_ptr<Segment> ring, head;
    uint32_t accepted = 0;
    float volume = 1.0f;
    {
        DataLock held;
        Channel *channel = channel_for(id, false);
        if (!channel || channel->pcm.empty())
            return 0;
        uint32_t total = (uint32_t)channel->pcm.size();
        uint32_t frame = host_audio_frame_bytes(channel->bits, channel->channels);
        if (!frame || offset >= total)
            return 0;
        offset -= offset % frame;
        accepted = bytes - (bytes % frame);
        if (offset + accepted > total)
            accepted = total - offset;
        if (!accepted)
            return 0;

        // The authoritative copy, so a later Play starts from what is there now.
        memcpy(channel->pcm.data() + offset, pcm, accepted);

        if (g_ops) {
            channel->streaming = true;
            channel->ring_mode = true;
            return (int32_t)accepted;
        }

        if (!channel->ring_mode) {
            // Where it has reached, so the ring starts sounding from there
            // rather than jumping to its beginning.
            uint32_t from = channel->cursor;
            if (channel->streaming)
                from = stream_cursor_locked(*channel) % total;
            from -= from % frame;

            channel->ring = make_segment(*channel, 0, total);
            if (!channel->ring)
                return 0;
            channel->ring->loop = true;
            channel->ring_from_frame = from / frame;
            channel->ring_head_frames = from ? (total - from) / frame : 0;
            // The rest of the current lap, played once before the loop takes
            // over at the top.
            if (from)
                head = make_segment(*channel, from, total);

            ++channel->generation;
            channel->ring_mode = true;
            channel->streaming = true;
            channel->loop = false;
            channel->playing = true;
            channel->start_offset = from;
            channel->cursor = from;
            channel->stream_started = audio_clock();
            channel->stream_base = from;
            channel->stream_head = 0; // a ring is written where it lies
            channel->stream_skew = 0;
            g_queued_total[id].store(0, std::memory_order_release);
            g_queued_played[id].store(0, std::memory_order_release);
            schedule_ring = true;
            ring = channel->ring;
            volume = host_audio_gain_from_millibels(channel->volume_mb);
        } else {
            // The ordinary case, and the whole point: samples straight into
            // the segment the player is looping. No player call, no seam.
            ring = channel->ring;
            if (!ring)
                return 0;
            uint32_t at = offset / frame;
            uint32_t frames = accepted / frame;
            if (at + frames > ring->frames())
                return 0;
            Segment incoming;
            uint32_t written = decode_segment(incoming, pcm, accepted, channel->bits,
                                              channel->channels, channel->pan_mb);
            const auto &nl = incoming.l, &nr = incoming.r;
            // Where the samples change is a step, and a step of any size is a
            // click. When the run has landed on top of the play head the old
            // samples there are kept and the new ones fade in over two
            // milliseconds.
            uint32_t rate = channel->rate ? channel->rate : channel->base_rate;
            uint32_t fade = rate / 500; // 2 ms
            if (fade > frames)
                fade = frames;
            uint32_t head_at;
            if (have_head) {
                head_at = node_frames < head_len
                              ? (uint32_t)((head_from + node_frames) % ring_frames)
                              : (uint32_t)((node_frames - head_len) % ring_frames);
            } else {
                head_at = stream_cursor_locked(*channel) % total / frame;
            }
            bool on_the_head = head_at >= at && head_at < at + frames;
            uint32_t from_frame = on_the_head ? head_at - at : 0;
            if (!on_the_head)
                fade = 0;
            if (from_frame + fade > frames)
                fade = frames - from_frame;

            // Does this run join the last one? A source that hands over runs
            // that do not meet at their edges is a click in the data.
            ++channel->ring_writes;
            if (!at)
                ++channel->ring_wraps;
            if (channel->ring_tail_at != at) {
                if (channel->ring_tail_at != 0xffffffffu)
                    ++channel->ring_unchecked;
            } else if (written) {
                float dl = nl[0] - channel->ring_tail_l;
                float dr = nr[0] - channel->ring_tail_r;
                if (dl < 0)
                    dl = -dl;
                if (dr < 0)
                    dr = -dr;
                float d = dl > dr ? dl : dr;
                if (d > 0.25f) {
                    ++channel->ring_breaks;
                    if (!at)
                        ++channel->ring_breaks_at_wrap;
                    if (d > channel->ring_worst_break)
                        channel->ring_worst_break = d;
                }
            }
            if (written) {
                channel->ring_tail_l = nl[written - 1];
                channel->ring_tail_r = nr[written - 1];
                channel->ring_tail_at = (at + written) % ring->frames();
            }
            for (uint32_t i = 1; i < written; ++i) {
                float dl = nl[i] - nl[i - 1];
                float dr = nr[i] - nr[i - 1];
                if (dl < 0)
                    dl = -dl;
                if (dr < 0)
                    dr = -dr;
                ++channel->ring_inside_pairs;
                if (dl > 0.25f || dr > 0.25f)
                    ++channel->ring_inside_steps;
            }
            {
                // Briefly under the render lock: the render thread reads
                // these same floats. Data lock, then render lock, is the
                // order the render thread never takes the other way round.
                std::lock_guard<std::mutex> render(g_render_mutex);
                for (unsigned c = 0; c < ring->channels; ++c) {
                    float *dest = ring->plane(c).data() + at;
                    const auto &source = incoming.plane(c);
                    for (uint32_t i = 0; i < written; ++i) {
                        float w = 1.0f;
                        if (i >= from_frame && i < from_frame + fade)
                            w = float(i - from_frame + 1) / float(fade);
                        dest[i] = w < 1.0f ? dest[i] * (1.0f - w) + source[i] * w : source[i];
                    }
                }
            }
            if (from_frame)
                log_once("audio: a write landed on the samples being played, so "
                         "the join is faded over two milliseconds rather than "
                         "stepped; the guest is writing close behind the cursor");
            return (int32_t)accepted;
        }
    }

    if (schedule_ring) {
        audio_check_unlocked("starting a ring");
        ensure_running(ring->channels);
        {
            std::lock_guard<std::mutex> render(g_render_mutex);
            Player &p = g_players[id];
            p.segments.clear();
            p.pos = 0;
            p.played = 0;
            p.volume = volume;
            if (head) {
                head->loop = false;
                head->completion = 0;
                p.segments.push_back(head);
            }
            p.segments.push_back(ring);
            p.playing = true;
        }
        trace("ring    ch %d  playing its %u-byte ring in place from now on", id,
              (unsigned)accepted);
    }
    return (int32_t)accepted;
}

// Seconds each channel spent claiming a queue, against the seconds of audio it
// was ever handed.
extern "C" void host_audio_queue_report(void *file) {
    FILE *out = file ? (FILE *)file : stdout;
    std::lock_guard<std::mutex> api(g_api_mutex);
    DataLock held;
    if (g_channels) {
        for (const auto &entry : *g_channels) {
            const Channel &c = entry.second;
            if (!c.ring_writes)
                continue;
            fprintf(out,
                    "audio ring:         channel %d took %u writes in place, "
                    "%u of them at the wrap, %u not the continuation of the "
                    "one before\n",
                    entry.first, c.ring_writes, c.ring_wraps, c.ring_unchecked);
            double joins =
                c.ring_writes > c.ring_unchecked ? (double)(c.ring_writes - c.ring_unchecked) : 1.0;
            double at_join = 100.0 * (double)c.ring_breaks / joins;
            double inside = c.ring_inside_pairs
                                ? 100.0 * (double)c.ring_inside_steps / (double)c.ring_inside_pairs
                                : 0.0;
            fprintf(out,
                    "                    %u did not join what they continue "
                    "(%u of those at the wrap), worst step %.3f of full "
                    "scale\n",
                    c.ring_breaks, c.ring_breaks_at_wrap, (double)c.ring_worst_break);
            fprintf(out,
                    "                    %.2f%% of joins step over a quarter "
                    "of full scale, against %.2f%% of the pairs inside the "
                    "runs%s\n",
                    at_join, inside,
                    inside > 0.0 && at_join > inside * 2.0
                        ? "  <-- the seams are worse than the content"
                        : "  (the content, not the seams)");
        }
    }
    for (int32_t id = 0; id < MAX_AUDIO_CHANNELS; ++id) {
        QueueHealth &h = g_queue_health[id];
        if (!h.appended)
            continue;
        uint32_t rate = h.rate;
        if (!rate && g_channels) {
            auto it = g_channels->find(id);
            if (it != g_channels->end()) {
                uint32_t r = it->second.rate ? it->second.rate : it->second.base_rate;
                rate = r * host_audio_frame_bytes(it->second.bits, it->second.channels);
            }
        }
        double holds = rate ? (double)h.appended / (double)rate : 0.0;
        bool wrong = holds > 0.0 && h.reporting > holds * 1.25;
        fprintf(out,
                "audio queue:        channel %d claimed a queue for %.1fs "
                "against %.1fs of audio handed over%s\n",
                id, h.reporting, holds, wrong ? "  <-- claiming depth it does not hold" : "");
        if (h.violations)
            fprintf(out,
                    "                    %u answers larger than anything "
                    "left could be\n",
                    h.violations);
    }
}

// How much of what this voice is playing is still to play - the sound itself
// included, whether it arrived with a Play, was re-issued by a conversion, or
// replaced what was there before.
extern "C" uint32_t host_audio_voice_remaining_bytes(int32_t id) {
    if (id < 0 || id >= MAX_AUDIO_CHANNELS)
        return 0;
    std::lock_guard<std::mutex> api(g_api_mutex);
    DataLock held;
    Channel *channel = channel_for(id, false);
    if (!channel || !channel->playing)
        return 0;
    const uint64_t appended = g_queued_total[id].load(std::memory_order_acquire);
    if (channel->streaming) {
        const uint64_t holding = (uint64_t)channel->stream_head + appended;
        const uint64_t played = g_ops ? g_queued_played[id].load(std::memory_order_acquire)
                                      : stream_played_locked(*channel);
        return holding > played ? (uint32_t)(holding - played) : 0;
    }
    const uint32_t total = (uint32_t)channel->pcm.size();
    const uint32_t at = channel->cursor < total ? channel->cursor : total;
    return total - at;
}

// Append PCM in the channel format established by Play and return accepted bytes.
// A looping channel must be converted to a stream before it can accept a continuation.
extern "C" int32_t host_audio_queue(int32_t id, const void *pcm, uint32_t bytes) {
    if (!pcm || !bytes || id < 0 || id >= MAX_AUDIO_CHANNELS)
        return 0;
    std::lock_guard<std::mutex> api(g_api_mutex);

    std::shared_ptr<Segment> buffer;
    uint32_t accepted = 0;
    {
        DataLock held;
        Channel *channel = channel_for(id, false);
        if (!channel || channel->pcm.empty())
            return 0;
        if (channel->loop) {
            log_once("audio: a queue arrived for a looping channel, where a buffer "
                     "behind the loop would never be reached; call "
                     "host_audio_stream first to continue it as a stream");
            return 0;
        }
        // A stream that has momentarily run dry is still a stream.
        if (!channel->playing) {
            if (!channel->streaming)
                return 0;
            log_once("audio: a stream ran dry before its next chunk arrived; the "
                     "gap is the time nothing was ready to play");
            channel->playing = true;
        }
        uint32_t frame = host_audio_frame_bytes(channel->bits, channel->channels);
        if (!frame || bytes < frame)
            return 0;
        accepted = bytes - (bytes % frame);
        channel->streaming = true;

        // If the clock says everything appended has already been played, this
        // chunk arrived after the stream ran dry: the skew absorbs the time
        // nothing was playing.
        if (!g_ops) {
            uint64_t already = g_queued_total[id].load(std::memory_order_acquire);
            uint64_t by_clock = stream_played_locked(*channel);
            if (by_clock > already)
                channel->stream_skew += by_clock - already;
        }

        if (!g_ops) {
            // The append is built from the caller's memory directly: the
            // channel's own pcm is the sound that started, and a stream's later
            // chunks are not part of it.
            uint32_t frames = accepted / frame;
            buffer = std::make_shared<Segment>();
            buffer->rate = channel->rate ? channel->rate : channel->base_rate;
            uint32_t written = decode_segment(*buffer, pcm, accepted, channel->bits,
                                              channel->channels, channel->pan_mb);
            buffer->completion = 2;
            buffer->bytes = accepted;
            if (!written)
                return 0;
        }
    }

    g_queued_total[id].fetch_add(accepted, std::memory_order_release);
    {
        DataLock held;
        Channel *c2 = channel_for(id, false);
        if (c2) {
            uint32_t r = c2->rate ? c2->rate : c2->base_rate;
            g_queue_health[id].rate = r * host_audio_frame_bytes(c2->bits, c2->channels);
        }
    }
    {
        uint64_t total = g_queued_total[id].load(std::memory_order_acquire);
        uint64_t by_clock = 0;
        {
            DataLock held;
            Channel *c2 = channel_for(id, false);
            if (c2 && c2->streaming)
                by_clock = stream_played_locked(*c2);
        }
        trace("queue   ch %d  +%u bytes, %llu appended, %llu played by the clock, "
              "%llu still to play",
              id, accepted, (unsigned long long)total, (unsigned long long)by_clock,
              (unsigned long long)(total > by_clock ? total - by_clock : 0));
    }

    // No stop, no play, no interrupt: the segment goes behind whatever is still
    // scheduled and the render position never restarts.
    audio_check_unlocked("queueing a buffer");
    if (g_ops) {
        if (g_ops->queue)
            g_ops->queue(id, accepted);
        return (int32_t)accepted;
    }
    ensure_running(buffer->channels);
    {
        std::lock_guard<std::mutex> render(g_render_mutex);
        Player &p = g_players[id];
        if (!p.playing) {
            trace("DRY     ch %d  the player had already stopped when this arrived", id);
            log_once("audio: a queued buffer arrived after the player had run dry; the "
                     "stream has a gap in it because the next chunk came too late");
            p.pos = 0;
            p.playing = true;
        }
        p.segments.push_back(buffer);
    }
    return (int32_t)accepted;
}

extern "C" void host_audio_completed(int32_t id, uint64_t generation) {
    if (id < 0 || id >= MAX_AUDIO_CHANNELS)
        return;
    g_finished[id].store(generation, std::memory_order_release);
}

extern "C" uint64_t host_audio_clipped_samples(void) {
    return g_clipped_samples.load(std::memory_order_relaxed);
}

// Three rates a test compares. One render loop, one rate: the clipper runs at
// the rate the mix is rendered at and nothing converts after it.
extern "C" double host_audio_clipper_output_rate(void) {
    std::lock_guard<std::mutex> api(g_api_mutex);
    return g_engine ? g_render_rate : 0.0;
}
extern "C" double host_audio_mixer_output_rate(void) {
    std::lock_guard<std::mutex> api(g_api_mutex);
    return g_engine ? g_render_rate : 0.0;
}
extern "C" double host_audio_output_bus_rate(void) {
    std::lock_guard<std::mutex> api(g_api_mutex);
    return g_engine ? g_render_rate : 0.0;
}
extern "C" double host_audio_render_rate(void) {
    return g_render_rate;
}

extern "C" float host_audio_worst_overshoot(void) {
    uint32_t bits = g_worst_over_bits.load(std::memory_order_relaxed);
    float v;
    memcpy(&v, &bits, sizeof v);
    return v;
}

extern "C" uint32_t host_audio_lock_violations(void) {
    return g_lock_violations.load(std::memory_order_relaxed);
}

// ---------------------------------------------------------------------------
// The engine, for the other things that make sound.
// ---------------------------------------------------------------------------
static int g_engine_token = 0;
extern "C" void *host_audio_engine(void) {
    std::lock_guard<std::mutex> api(g_api_mutex);
    {
        DataLock held;
        if (!ensure_engine())
            return nullptr;
    }
    return g_ops ? nullptr : &g_engine_token;
}

// A transport change requested while a movie output was open, applied when the
// last one closes (os_audio_output_set_idle_callback).
std::atomic<bool> g_transport_pending{false};
std::atomic<bool> g_force_reopen{false}; // Test86: host_audio_restart_output
void transport_after_movies() {
    if (g_transport_pending.load(std::memory_order_acquire))
        audio_mixer_transport_changed();
}

// The Dolby flag changed: reopen the output with the requested transport if it
// differs from the running one. A movie output that is open is tied to the
// current transport (its bus, or the device the bitstream would take over), so
// the change waits until the last movie output closes, and says so.
void audio_mixer_transport_changed() {
    std::lock_guard<std::mutex> api(g_api_mutex);
    if (!g_sink || g_manual_render.load(std::memory_order_acquire)) {
        g_transport_pending.store(false, std::memory_order_release);
        return; // the next start picks the transport
    }
    uint32_t mode = 0, flags = 0;
    host_audio_get_output(&mode, &flags);
    const bool want = (flags & POP_AUDIO_DOLBY_DIGITAL) && g_sink_channels == 6;
    if (want == g_sink->bitstream() && !g_force_reopen.load(std::memory_order_acquire)) {
        if (g_transport_pending.exchange(false, std::memory_order_acq_rel))
            printf("[host] audio: pending output change cancelled; staying on %s\n",
                   want ? "Dolby Digital" : "PCM");
        return;
    }
    // Pending first, then look: a movie that closes after the look finds it set,
    // and its callback applies the change once this call releases the lock.
    os_audio_output_set_idle_callback(transport_after_movies);
    const bool was_pending = g_transport_pending.exchange(true, std::memory_order_acq_rel);
    if (os_audio_outputs_open() > 0) {
        if (!was_pending)
            printf("[host] audio: switch to %s is pending until the playing movie ends\n",
                   want ? "Dolby Digital" : "PCM");
        fflush(stdout);
        return;
    }
    g_transport_pending.store(false, std::memory_order_release);
    g_force_reopen.store(false, std::memory_order_release);
    printf("[host] audio: output transport switches to %s\n", want ? "Dolby Digital" : "PCM");
    fflush(stdout);
    audio_check_unlocked("changing the output transport");
    g_sink->stop();
    g_sink.reset();
    ensure_running(g_sink_channels);
}

extern "C" void host_audio_restart_output(const char *why) {
    printf("[host] audio: output reopens (%s)\n", why ? why : "requested");
    fflush(stdout);
    g_force_reopen.store(true, std::memory_order_release);
    audio_mixer_transport_changed();
}

extern "C" void host_audio_engine_run(void) {
    std::lock_guard<std::mutex> api(g_api_mutex);
    ensure_running();
}

extern "C" void host_audio_set_music_source(HostAudioMusicRender render, void *context) {
    std::lock_guard<std::mutex> renderlock(g_render_mutex);
    g_music = render;
    g_music_context = context;
}

// How many channels have something playing on them. Takes the data lock only,
// never the API mutex, so the capture can ask from wherever it runs.
extern "C" uint32_t host_audio_playing_channels(void) {
    DataLock held;
    if (!g_channels)
        return 0;
    uint32_t n = 0;
    for (const auto &entry : *g_channels)
        if (entry.second.playing)
            ++n;
    return n;
}

// --- capture: what the mixer actually produced ------------------------------
//
// The render loop writes every block it produces to the capture when one is
// open, on a device and offline alike, at the rate the mix is rendered at.
extern "C" int host_audio_capture_begin(const char *path) {
    std::lock_guard<std::mutex> api(g_api_mutex);
    {
        DataLock held;
        if (!ensure_engine())
            return 0;
    }
    if (!host_capture_open(path, (uint32_t)g_render_rate))
        return 0;
    printf("[host] audio capture: %s at %.0f Hz, from the mixer's own output\n",
           path ? path : "(measured, not written)", g_render_rate);
    fflush(stdout);
    return 1;
}

extern "C" void host_audio_capture_end(void) {
    std::lock_guard<std::mutex> api(g_api_mutex);
    host_capture_close();
}

// --- offline rendering, which is how a test hears anything -------------------
//
extern "C" void host_audio_shutdown(void) {
    std::lock_guard<std::mutex> api(g_api_mutex);
    if (g_sink) {
        g_sink->stop();
        g_sink.reset();
    }
}

// The same render loop into a buffer instead of a device, so what a test
// measures is what would have been played.
extern "C" int host_audio_offline_begin(double sample_rate, uint32_t max_frames) {
    (void)max_frames;
    std::lock_guard<std::mutex> api(g_api_mutex);
    {
        DataLock held;
        if (!ensure_engine())
            return 0;
    }
    if (g_sink) {
        g_sink->stop();
        g_sink.reset();
    }
    {
        std::lock_guard<std::mutex> render(g_render_mutex);
        g_render_rate = sample_rate > 0 ? sample_rate : 48000.0;
    }
    // From here the play cursors count rendered audio rather than seconds.
    // Set before anything is played, so no channel straddles the two clocks.
    g_rendered_frames.store(0, std::memory_order_release);
    g_manual_render.store(true, std::memory_order_release);
    return 1;
}

extern "C" uint32_t host_audio_offline_render(uint32_t frames, float *peak) {
    std::lock_guard<std::mutex> api(g_api_mutex);
    if (peak)
        *peak = 0.0f;
    if (!g_engine || !g_manual_render.load(std::memory_order_acquire))
        return 0;
    static std::vector<float> planes[6];
    uint32_t done = 0;
    float loudest = 0.0f;
    while (done < frames) {
        const uint32_t want = std::min<uint32_t>(frames - done, 4096);
        float *out[6];
        for (unsigned c = 0; c < 6; ++c) {
            planes[c].assign(want, 0.0f);
            out[c] = planes[c].data();
        }
        render_block(out, want);
        for (const auto &v : planes)
            for (float x : v) {
                const float a = x < 0 ? -x : x;
                if (a > loudest)
                    loudest = a;
            }
        done += want;
        g_rendered_frames.fetch_add(want, std::memory_order_release);
    }
    if (peak)
        *peak = loudest;
    return done;
}

extern "C" uint32_t host_audio_offline_render_planes(uint32_t frames, float *const *out) {
    std::lock_guard<std::mutex> api(g_api_mutex);
    if (!g_engine || !g_manual_render.load(std::memory_order_acquire) || !out)
        return 0;
    for (unsigned c = 0; c < 6; ++c)
        if (!out[c])
            return 0;
    for (unsigned c = 0; c < 6; ++c)
        std::fill(out[c], out[c] + frames, 0.0f);
    render_block(out, frames);
    g_rendered_frames.fetch_add(frames, std::memory_order_release);
    return frames;
}

// Time that will not be rendered, counted anyway, so the cursors move.
extern "C" void host_audio_offline_skip(uint32_t frames) {
    if (!frames)
        return;
    g_rendered_frames.fetch_add(frames, std::memory_order_release);
}

extern "C" void host_audio_offline_end(void) {
    std::lock_guard<std::mutex> api(g_api_mutex);
    g_manual_render.store(false, std::memory_order_release);
    std::lock_guard<std::mutex> render(g_render_mutex);
    g_render_rate = 48000.0;
}

extern "C" void host_audio_play(const HostAudioPlay *p) {
    if (!p || !p->pcm || !p->bytes)
        return;
    std::lock_guard<std::mutex> api(g_api_mutex);

    // The loudest sample in what is being submitted. The game streams its
    // music by playing a looping buffer of silence and refilling it, so the
    // moment that buffer stops being silent is the moment the music should
    // start.
    double peak = 0.0;
    {
        const uint8_t *pcm = (const uint8_t *)p->pcm;
        int bits = p->bits == 8 ? 8 : 16;
        uint32_t step = bits == 8 ? 1u : 2u;
        for (uint32_t i = 0; i + step <= p->bytes; i += step) {
            double v = bits == 8 ? (double)pcm[i] - 128.0
                                 : (double)(int16_t)(uint16_t)(pcm[i] | (pcm[i + 1] << 8));
            double a = (v < 0 ? -v : v) / (bits == 8 ? 128.0 : 32768.0);
            if (a > peak)
                peak = a;
        }
    }

    Job job;
    bool was_silent_loop = false;
    {
        DataLock held;
        Channel *channel = channel_for(p->channel, true);
        if (!channel)
            return;
        channel->channels = p->channels == 6 ? 6 : p->channels == 2 ? 2 : 1;
        channel->bits = p->bits == 8 ? 8 : 16;
        channel->base_rate = (uint32_t)(p->sample_rate > 0 ? p->sample_rate : 22050);
        channel->rate = host_audio_play_rate(channel->base_rate, channel->rate,
                                             channel->rate_overridden ? 1 : 0);
        channel->loop = p->loop != 0;
        channel->volume_mb = p->volume;
        channel->pan_mb = p->pan;
        was_silent_loop = channel->playing && channel->loop && channel->silent;
        channel->silent = peak <= 0.0001;
        if (was_silent_loop && !channel->silent) {
            printf("[host] audio channel %d was looping silence and has been "
                   "refilled with sound (peak %.3f); it is stopped and "
                   "rescheduled so the new content plays\n",
                   p->channel, peak);
            fflush(stdout);
        }
        channel->pcm.assign((const uint8_t *)p->pcm, (const uint8_t *)p->pcm + p->bytes);
        // A Play replaces whatever was on the channel, stream included, and the
        // queue accounting starts again with it.
        channel->streaming = false;
        channel->ring_mode = false;
        channel->ring.reset();
        if (p->channel >= 0 && p->channel < MAX_AUDIO_CHANNELS) {
            g_queued_total[p->channel].store(0, std::memory_order_release);
            g_queued_played[p->channel].store(0, std::memory_order_release);
        }
        job = plan_locked(p->channel, *channel, p->start_offset);
    }
    host_capture_note_play(p->channel);
    trace("play    ch %d  %u Hz %d ch %d bit  %u bytes from %u%s  peak %.3f", p->channel,
          (unsigned)(p->sample_rate ? p->sample_rate : 22050),
          p->channels == 6   ? 6
          : p->channels == 2 ? 2
                             : 1,
          p->bits == 8 ? 8 : 16, p->bytes, p->start_offset, p->loop ? "  looping" : "", peak);
    perform(job);
}

extern "C" void host_audio_stop(int32_t id) {
    std::lock_guard<std::mutex> api(g_api_mutex);
    uint64_t sample_time = 0;
    bool have_time = false;
    {
        DataLock held;
        Channel *channel = channel_for(id, false);
        if (!channel)
            return;
    }
    // Read where it reached before stopping it, and do both without the lock.
    have_time = node_sample_time(id, &sample_time);
    {
        DataLock held;
        Channel *channel = channel_for(id, false);
        if (!channel)
            return;
        // The cursor is what a DirectSound buffer keeps across a Stop, and what
        // the next Play resumes from.
        if (have_time)
            channel->cursor = host_audio_position_bytes(
                sample_time, channel->start_offset, (uint32_t)channel->pcm.size(), channel->bits,
                channel->channels, channel->loop ? 1 : 0);
        ++channel->generation; // orphan any completion in flight
        channel->playing = false;
    }
    audio_check_unlocked("stopping a player");
    if (g_ops) {
        if (g_ops->stop)
            g_ops->stop(id, 0);
        return;
    }
    player_stop(id);
}

extern "C" void host_audio_set_volume(int32_t id, int32_t volume) {
    std::lock_guard<std::mutex> api(g_api_mutex);
    float gain = 1.0f;
    {
        DataLock held;
        Channel *channel = channel_for(id, true);
        if (!channel)
            return;
        channel->volume_mb = volume;
        gain = host_audio_gain_from_millibels(volume);
    }
    audio_check_unlocked("setting a player's volume");
    if (g_ops)
        return;
    std::lock_guard<std::mutex> render(g_render_mutex);
    g_players[id].volume = gain;
}

// Pan and frequency both live in the samples or in the segment's rate, so a
// live change re-schedules the rest of the sound from where it had reached.
// The sound does not restart. The player time is read, and the new
// scheduling is performed, with the lock released.
template <class Apply> static void reschedule_from_current(int32_t id, Apply apply) {
    bool playing = false;
    {
        DataLock held;
        Channel *channel = channel_for(id, true);
        if (!channel)
            return;
        apply(*channel);
        reconcile_locked(id, *channel);
        playing = channel->playing && !channel->pcm.empty();
    }
    if (!playing)
        return;
    uint64_t sample_time = 0;
    bool have_time = node_sample_time(id, &sample_time);
    Job job;
    {
        DataLock held;
        Channel *channel = channel_for(id, false);
        if (!channel)
            return;
        uint32_t at = channel->cursor;
        if (have_time)
            at = host_audio_position_bytes(sample_time, channel->start_offset,
                                           (uint32_t)channel->pcm.size(), channel->bits,
                                           channel->channels, channel->loop ? 1 : 0);
        job = plan_locked(id, *channel, at);
    }
    perform(job);
}

extern "C" void host_audio_set_pan(int32_t id, int32_t pan) {
    std::lock_guard<std::mutex> api(g_api_mutex);
    reschedule_from_current(id, [pan](Channel &ch) {
        const bool changed = ch.pan_mb != pan;
        ch.pan_mb = pan;
        if (!changed)
            ch.playing = false; // nothing to redo
    });
}

extern "C" void host_audio_set_frequency(int32_t id, uint32_t hz) {
    std::lock_guard<std::mutex> api(g_api_mutex);
    reschedule_from_current(id, [hz](Channel &ch) {
        // DSBFREQUENCY_ORIGINAL: go back to the rate the buffer was created
        // with, and stop overriding it.
        ch.rate_overridden = hz != 0;
        uint32_t rate = hz ? hz : ch.base_rate;
        if (rate == ch.rate)
            ch.playing = false; // nothing to redo
        ch.rate = rate;
    });
}

// The play cursor, and whether the channel is still going. The player's own
// position is used when it is reporting one; otherwise the cursor is modelled
// from the audio clock at the buffer's byte rate, because a DirectSound play
// cursor advances from the moment Play was called whether or not anything is
// audible, and the game's video player sleeps until it has.
static uint32_t audio_advance(int32_t id, bool *playing_out) {
    if (playing_out)
        *playing_out = false;
    {
        DataLock held;
        Channel *channel = channel_for(id, false);
        if (!channel)
            return 0;
        reconcile_locked(id, *channel);
        if (!channel->playing)
            return channel->cursor;
    }
    uint64_t sample_time = 0;
    bool have_time = node_sample_time(id, &sample_time);

    DataLock held;
    Channel *channel = channel_for(id, false);
    if (!channel)
        return 0;
    uint32_t total = (uint32_t)channel->pcm.size();
    uint32_t next;
    if (have_time && sample_time > 0) {
        next = host_audio_position_bytes(sample_time, channel->start_offset, total, channel->bits,
                                         channel->channels, channel->loop ? 1 : 0);
    } else {
        uint32_t rate = channel->rate ? channel->rate : channel->base_rate;
        next = host_audio_wall_clock_bytes(audio_clock() - channel->started, rate, channel->bits,
                                           channel->channels, channel->start_offset, total,
                                           channel->loop ? 1 : 0);
    }
    if (channel->streaming) {
        channel->cursor = next;
        if (playing_out)
            *playing_out = true;
        return next;
    }
    if (!channel->loop && next >= total) {
        channel->playing = false;
        channel->cursor = total;
        return total;
    }
    channel->cursor = next;
    if (playing_out)
        *playing_out = true;
    return next;
}

extern "C" uint32_t host_audio_position(int32_t id) {
    std::lock_guard<std::mutex> api(g_api_mutex);
    return audio_advance(id, nullptr);
}

extern "C" int32_t host_audio_is_playing(int32_t id) {
    std::lock_guard<std::mutex> api(g_api_mutex);
    bool playing = false;
    audio_advance(id, &playing);
    return playing ? 1 : 0;
}

void host_audio_set_background_mute(bool muted) {
    g_background_mute.store(muted, std::memory_order_relaxed);
}

void host_audio_pause(bool paused) {
    if (g_sink)
        g_sink->pause(paused);
}
