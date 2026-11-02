// Timed capture proof, registered and run by build.sh; every output fixture is manual/null/offline.
// Pinned PCM-only baseline: pcm_samples=3510 metadata_blocks=0/3, 8 checks / 1 failure;
// fixed: pcm_samples=3510 metadata_blocks=3/3, 457 checks / 0 failures. Mutants: README.md.
#include "dsound_host.h"
#include <mach/mach_time.h>
#ifndef TIMEDTAP_SOURCE
#define TIMEDTAP_SOURCE "dsound_host.cpp"
#endif
#include TIMEDTAP_SOURCE
#include <array>

#ifdef TIMEDTAP_BASELINE
// Pinned behavioral model of the old PCM-only tap, using the live mixer to produce real samples.
// Route test subscriptions to this adapter, leaving the mixer's timed capture unsubscribed. The legacy
// tap below forwards PCM with zero metadata, so rejection is numerical, not a compiler/linker failure.
#define ds_capture_start baseline_capture_start
#define ds_capture_stop baseline_capture_stop
static DsCaptureFn baseline_fn;
static void *baseline_user;
static DsResult ds_capture_start(DsCaptureFn fn, void *user) {
    baseline_fn = fn; baseline_user = user; return DS_OK;
}
static void ds_capture_stop() { baseline_fn = nullptr; }
#endif

static int checks, failures;
static void check(bool ok, const char *what) {
    ++checks;
    if (!ok) { ++failures; printf("FAIL: %s\n", what); }
}
[[maybe_unused]] static double millis(uint64_t ticks) {
    mach_timebase_info_data_t t;
    mach_timebase_info(&t);
    return (double)ticks * t.numer / t.denom / 1e6;
}
struct Record {
    DsTimedMixBlock block{};
    std::array<float, 256 * 6> pcm{};
};
struct Receiver {
    std::array<Record, 256> records{};
    size_t count = 0, legacy_count = 0, mismatches = 0;
    std::array<float, 256 * 6> legacy{};
    bool pause = false;
    std::atomic<bool> entered{false}, resume{false};
    static void tap(void *user, const float *pcm, size_t frames) {
        auto &r = *(Receiver *)user;
        ++r.legacy_count;
        memcpy(r.legacy.data(), pcm, frames * 6 * sizeof(float));
#ifdef TIMEDTAP_BASELINE
        if (baseline_fn) {
            DsTimedMixBlock b{};
            b.pcm51 = pcm; b.frames = frames;
            baseline_fn(baseline_user, &b);
        }
#endif
    }
    static void receive(void *user, const DsTimedMixBlock *block) {
        auto &r = *(Receiver *)user;
        if (memcmp(r.legacy.data(), block->pcm51, block->frames * 6 * sizeof(float))) ++r.mismatches;
        if (r.count < r.records.size()) {
            auto &out = r.records[r.count];
            out.block = *block;
            memcpy(out.pcm.data(), block->pcm51, block->frames * 6 * sizeof(float));
            out.block.pcm51 = out.pcm.data();
        }
        ++r.count;
        if (r.pause) {
            r.entered.store(true, std::memory_order_release);
            while (!r.resume.load(std::memory_order_acquire)) std::this_thread::yield();
        }
    }
};
struct Fixture {
    Receiver &receiver;
    DsDevice *d = nullptr;
    DsBuffer *b = nullptr;
    std::vector<float> source;
    std::array<float, 256 * 6> output{};
    Fixture(Receiver &r, DsSink sink = DS_SINK_MANUAL) : receiver(r), source(4096 * 6) {
        for (size_t f = 0; f < source.size() / 6; ++f)
            for (int c = 0; c < 6; ++c) source[f * 6 + c] = (c + 1) * 0.04f + (f % 17) * 0.0001f;
        DsConfig cfg{};
        cfg.sink = sink; cfg.mode = DS_OUT_PCM51; cfg.block_frames = 256;
        cfg.tap = Receiver::tap; cfg.tap_user = &r;
        if (ds_device_create(&cfg, &d)) std::abort();
        uint8_t w[18] = {};
        w[0] = 3; w[2] = 6; w[12] = 24; w[14] = 32;
        for (int k = 0; k < 4; ++k) { w[4+k] = 48000u >> (k*8); w[8+k] = (48000u*24) >> (k*8); }
        if (ds_device_create_buffer(d, 0, source.size() * sizeof(float), w, source.data(), &b)) std::abort();
        ds_buffer_play(b, DSBPLAY_LOOPING);
    }
    void render(size_t n = 256) { check(ds_device_render(d, output.data(), n) == DS_OK, "manual render"); }
    ~Fixture() { ds_buffer_release(b); ds_device_release(d); }
};

static void manual_proof() {
    auto receiver = std::make_unique<Receiver>();
    auto &r = *receiver;
    Fixture f(r); // subscribe to an already-running device
    check(ds_capture_start(Receiver::receive, &r) == DS_OK, "subscribe existing device");
    f.render(); f.render(); f.render(73);
    ds_capture_stop();
    check(r.count == 3 && r.legacy_count == 3 && r.mismatches == 0, "legacy/timed PCM byte identity");
    size_t samples = 0, metadata = 0, distinct = 0, exact = 0;
    for (size_t i = 0; i < r.count; ++i) {
        auto &b = r.records[i].block;
        samples += b.frames * 6;
        metadata += b.sample_rate == 48000 && b.stream_id != 0 && b.clock_generation != 0 && b.first_sample_host_time != 0;
        for (int c = 0; c < 6; ++c) distinct += b.pcm51[c] == f.source[(i * 256) * 6 + c];
        for (size_t k = 0; k < b.frames * 6; ++k) exact += b.pcm51[k] == f.source[i * 256 * 6 + k];
    }
    printf("manual: pcm_samples=%zu metadata_blocks=%zu/3 distinct_channels=%zu/18 mismatches=%zu\n", samples, metadata, distinct, r.mismatches);
    check(samples == (512+73)*6 && distinct == 18, "channel-distinct PCM exists");
    printf("manual: exact_source_samples=%zu/%zu\n", exact,samples);
    check(exact == samples, "legacy and timed PCM exactly preserve every input sample");
    check(metadata == 3, "timed metadata present at actual rate");
#ifndef TIMEDTAP_BASELINE
    auto &a = r.records[0].block;
    auto &b = r.records[1].block;
    check(a.sample_position == 0 && b.sample_position == 256 && r.records[2].block.sample_position == 512,
          "sample position progresses by actual block frames");
    check(a.flags == DS_CAPTURE_DISCONTINUITY && b.flags == 0, "first block marks discontinuity only");
    double error = std::fabs(millis(b.first_sample_host_time - a.first_sample_host_time) - 256000.0/48000);
    printf("manual: timestamp_error_ms=%.9f\n", error);
    check(error <= 1000.0/48000, "manual sample clock <= one input sample");
    auto before = r.count;
    f.render();
    check(r.count == before && r.legacy_count == 4, "stop leaves legacy tap active, zero new capture callbacks");
    // Rate changes reset both generation and the manual sample-clock anchor without resetting stream identity.
    check(ds_capture_start(Receiver::receive, &r) == DS_OK, "resubscribe");
    { std::lock_guard<std::mutex> lock(f.d->m); rebase_rate(*f.d, 44100); }
    f.render(); f.render();
    ds_capture_stop();
    auto &changed = r.records[3].block;
    auto &next = r.records[4].block;
    check(changed.sample_rate == 44100 && next.sample_rate == 44100 && changed.stream_id == a.stream_id,
          "rate metadata follows PCM snapshot");
    check(changed.clock_generation != a.clock_generation && changed.flags == DS_CAPTURE_DISCONTINUITY && next.flags == 0,
          "rate rebase changes clock generation");
    check(changed.sample_position == 841 && next.sample_position == 1097, "rate rebase retains mixed sample count");
    check(std::fabs(millis(next.first_sample_host_time - changed.first_sample_host_time) - 256000.0/44100) <= 1000.0/44100,
          "new manual anchor uses changed rate");
#endif
}

#ifndef TIMEDTAP_BASELINE
static void selection_proof() {
    auto receiver = std::make_unique<Receiver>();
    auto &r = *receiver;
    Fixture older(r);
    ds_capture_start(Receiver::receive, &r);
    older.render();
    uint64_t first = r.records[0].block.stream_id;
    uint64_t second;
    {
        Fixture newer(r);
        older.render(); newer.render(); older.render();
        check(r.count == 2, "overlapping older device is excluded");
        second = r.records[1].block.stream_id;
        check(second > first && r.records[1].block.flags == DS_CAPTURE_DISCONTINUITY, "newest stream selected");
    }
    older.render();
    check(r.count == 3 && r.records[2].block.stream_id == first && r.records[2].block.flags == DS_CAPTURE_DISCONTINUITY,
          "release newest selects remaining newest live device");
    {
        Fixture recreated(r);
        recreated.render();
        check(r.count == 4 && r.records[3].block.stream_id > second, "subscription survives device re-creation");
    }
    ds_capture_stop();
}

static void limiter_proof() {
    ds_set_peak_limiter(1);
    auto receiver = std::make_unique<Receiver>();
    auto &r = *receiver;
    Fixture f(r);
    for (auto &x : f.source) x *= 12;
    ds_capture_start(Receiver::receive, &r);
    f.render(); f.render();
    ds_capture_stop();
    float peak = 0;
    size_t unequal = 0;
    for (auto &record : r.records) if (record.block.frames) {
        for (size_t j = 0; j < record.block.frames * 6; ++j) peak = std::max(peak, std::fabs(record.pcm[j]));
    }
    for (size_t j = 0; j < f.output.size(); ++j) unequal += f.output[j] != r.records[1].pcm[j];
    printf("limiter: peak=%.9f legacy_mismatches=%zu render_mismatches=%zu\n", peak, r.mismatches, unequal);
    check(peak > 0.8f && peak <= PeakLimiter::threshold + 1e-6f && unequal == 0 && r.mismatches == 0,
          "capture is exactly post-limiter/clamp render and legacy tap");
    ds_set_peak_limiter(0);
}

static void recreation_proof() {
    auto receiver = std::make_unique<Receiver>();
    auto &r = *receiver;
    ds_capture_start(Receiver::receive,&r); // no live device: retain subscription across the gap
    uint64_t stream;
    { Fixture f(r); f.render(); stream=r.records[0].block.stream_id; }
    { Fixture f(r); f.render(); }
    ds_capture_stop();
    check(r.count==2 && r.records[1].block.stream_id>stream && r.records[1].block.sample_position==0 &&
          (r.records[1].block.flags & DS_CAPTURE_DISCONTINUITY), "subscription survives a gap with no live devices");
}

static void snapshot_proof() {
    auto receiver = std::make_unique<Receiver>();
    auto &r = *receiver;
    Fixture f(r);
    ds_capture_start(Receiver::receive,&r);
    std::vector<uint32_t> events;
    DsTimedMixBlock snapshot;
    {
        std::lock_guard<std::mutex> lock(f.d->m);
        snapshot = timed_block(*f.d,256);
        mix_block(*f.d,256,events);
        memcpy(f.output.data(),f.d->block.data(),sizeof(f.output));
        // A concurrent control thread can rebase after the PCM copy, before delivery acquires admission.
        rebase_rate(*f.d,44100);
    }
    deliver(*f.d,f.output.data(),256,events,snapshot);
    ds_capture_stop();
    auto &b = r.records[0].block;
    check(r.count==1 && b.sample_rate==48000 && b.clock_generation==snapshot.clock_generation &&
          b.sample_position==0 && b.first_sample_host_time==snapshot.first_sample_host_time && r.mismatches==0,
          "delivery retains PCM rate and clock snapshot across concurrent rebase");
    printf("snapshot: pcm_rate=%u live_rate=%d generation=%llu live_generation=%llu\n", b.sample_rate,f.d->cfg.mix_rate,
           (unsigned long long)b.clock_generation,(unsigned long long)f.d->clock_generation);
}

// Construct only the existing offline AudioOutput fixture: no CoreAudio open, fake drain, or real-time jitter.
static void queue_proof() {
    DsDevice d;
    d.id = 0x70000000;
    d.cfg.sink = DS_SINK_DEVICE; d.cfg.mode = DS_OUT_PCM51; d.cfg.mix_rate = 48000;
    d.out = std::make_unique<media::AudioOutput>();
    check(d.out->open_offline(media::OutputMode::Pcm51, 48000), "offline PCM output");
    std::vector<float> silence(4096 * 6);
    d.out->write(silence.data(), 4096);
    check(d.out->queued_frames() == 4096, "fixture has exactly 4096 queued frames");
    auto before = mach_absolute_time();
    auto b = timed_block(d, 256);
    auto after = mach_absolute_time();
    double lead = millis(b.first_sample_host_time - before);
    uint64_t lower = before + frame_ticks(4096,48000), upper = after + frame_ticks(4096,48000);
    double error = millis(b.first_sample_host_time < lower ? lower - b.first_sample_host_time :
                          b.first_sample_host_time > upper ? b.first_sample_host_time - upper : 0);
    printf("queue: frames=4096 rate=48000 lead_ms=%.6f expected_ms=85.333333 error_ms=%.9f\n", lead, error);
    check(error <= 1000.0/48000, "4096-frame playback lead <= one input sample error");
    check(lead > 85.3 && lead < 85.4 + millis(after-before), "timestamp includes queue lead, not arrival");
    // Draining one callback period changes playback lead by precisely that many input-rate frames.
    std::vector<float> drain(1024 * 6);
    d.out->render_offline(drain.data(), drain.size()*sizeof(float));
    b = timed_block(d, 256);
    auto now = mach_absolute_time();
    check(std::fabs(millis(b.first_sample_host_time - now) - 64.0) <= 1000.0/48000, "drained queue uses remaining 3072 frames");
    // Pending bitstream input must count even when no playable burst has been emitted.
    d.cfg.mode = DS_OUT_TRUEHD;
    d.out->close();
    check(d.out->open_offline(media::OutputMode::TrueHd,48000), "offline TrueHD output");
    d.out->write(silence.data(), 256);
    size_t pending = d.out->pending_frames(), queued = d.out->queued_frames();
    before = mach_absolute_time(); b = timed_block(d,256); after = mach_absolute_time();
    lead = millis(b.first_sample_host_time-before);
    printf("pending: queued=%zu pending=%zu lead_ms=%.6f\n", queued,pending,lead);
    check(pending > 0, "bitstream fixture has pending input");
    check(std::fabs(lead - (queued+pending)*1000.0/48000) <= millis(after-before) + 1000.0/48000,
          "pending encoder input contributes to playback lead");
    auto generation = d.clock_generation;
    note_underrun(d);
    check(d.clock_generation != generation, "device underrun changes generation");
    generation = d.clock_generation;
    detach(d, "offline test detach");
    check(d.clock_generation != generation && d.detached, "output abandonment changes generation");
    generation = d.clock_generation;
    d.ready = std::make_unique<media::AudioOutput>();
    check(d.ready->open_offline(media::OutputMode::Stereo,48000), "offline replacement output");
    d.ready_mode = DS_OUT_STEREO; d.ready_rate = 48000;
    d.devgen = d.pending_gen = media::output_devices_generation();
    follow_output(d);
    check(!d.detached && d.cfg.mode == DS_OUT_STEREO && d.clock_generation != generation,
          "same-rate output change starts a new clock generation");
}

static void null_proof() {
    auto receiver = std::make_unique<Receiver>();
    auto &r = *receiver;
    Fixture f(r, DS_SINK_NULL);
    stop_thread(*f.d); // all subsequent actions still use the production mixer_thread
    { std::lock_guard<std::mutex> lock(f.d->m); f.d->t0 = Clock::now() - std::chrono::seconds(1); }
    ds_capture_start(Receiver::receive,&r);
    r.pause = true;
    start_thread(*f.d);
    while (!r.entered.load(std::memory_order_acquire)) std::this_thread::yield();
    // Deterministic null starvation: move the virtual clock far beyond written while callback is held.
    auto oldgen = r.records[0].block.clock_generation;
    auto expected = host_at(f.d->t0) + frame_ticks(r.records[0].block.sample_position,48000);
    auto actual = r.records[0].block.first_sample_host_time;
    check(millis(actual > expected ? actual-expected : expected-actual) <= 1000.0/48000,
          "null timestamp uses t0, not delivery arrival");
    bool distinct = true;
    for (int c=1;c<6;++c) distinct &= r.records[0].pcm[c] > r.records[0].pcm[c-1] + 0.03f;
    check(distinct && r.mismatches==0,"null sink captures channel-distinct PCM identical to legacy tap");
    { std::lock_guard<std::mutex> lock(f.d->m); f.d->t0 -= std::chrono::seconds(1); }
    r.pause = false; // callback reads pause only on entry; publish via resume below
    r.resume.store(true,std::memory_order_release);
    stop_thread(*f.d);
    // Run again from the deliberately starved clock, and wait for a callback without a polling data race.
    r.resume = false; r.entered = false; r.pause = true;
    start_thread(*f.d);
    while (!r.entered.load(std::memory_order_acquire)) std::this_thread::yield();
    auto &b = r.records[r.count-1].block;
    check(b.clock_generation > oldgen && (b.flags & DS_CAPTURE_DISCONTINUITY), "null starvation rebases and marks discontinuity");
    printf("null: generation_before=%llu after=%llu position=%llu\n", (unsigned long long)oldgen,
           (unsigned long long)b.clock_generation,(unsigned long long)b.sample_position);
    r.resume.store(true,std::memory_order_release);
    stop_thread(*f.d);
    ds_capture_stop();
}

struct Counter {
    std::atomic<size_t> calls{0}, after_stop{0};
    std::atomic<bool> stopped{false};
    static void receive(void *user,const DsTimedMixBlock *) {
        auto &c = *(Counter*)user;
        ++c.calls;
        if (c.stopped.load(std::memory_order_acquire)) ++c.after_stop;
    }
};
static void lifetime_proof() {
    auto receiver = std::make_unique<Receiver>();
    auto &r = *receiver;
    Fixture f(r);
    r.pause = true;
    ds_capture_start(Receiver::receive,&r);
    std::thread render([&] { ds_device_render(f.d,f.output.data(),256); });
    while (!r.entered.load(std::memory_order_acquire)) std::this_thread::yield();
    std::atomic<bool> stop_began{false}, stop_done{false};
    std::thread stopper([&] { stop_began=true; ds_capture_stop(); stop_done=true; });
    while (!stop_began.load()) std::this_thread::yield();
    // Wait for stop's admission close before observing its return (bounded so the lifetime mutant cannot hang).
    auto until = Clock::now()+std::chrono::milliseconds(100);
    while ((capture().gate.load() & 1u) && Clock::now()<until) std::this_thread::yield();
    until = Clock::now()+std::chrono::milliseconds(20);
    while (!stop_done.load() && Clock::now()<until) std::this_thread::yield();
    bool early = stop_done.load();
    check(!early, "stop waits for in-flight callback");
    r.resume.store(true,std::memory_order_release);
    render.join(); stopper.join();
    check(stop_done && r.count == 1, "in-flight callback drained before stop returns");
    if (early) {
        // The negative control has already failed numerically with its callback held. Do not stress its unsafe
        // unsubscribe afterward: that would race pointer clearing and turn the proof into a nondeterministic crash.
        printf("lifetime: early_stop=1 callbacks_in_flight_at_return=1\n");
        return;
    }
    Counter counter;
    std::atomic<bool> running{true};
    std::thread pump([&] {
        while (running.load()) ds_device_render(f.d,f.output.data(),256);
    });
    size_t successful = 0;
    for (int i=0;i<200;++i) {
        counter.stopped=false;
        auto before = counter.calls.load();
        successful += ds_capture_start(Counter::receive,&counter)==DS_OK;
        check(ds_capture_start(Counter::receive,&counter)==DSERR_ALLOCATED,"second subscription rejected");
        while (counter.calls.load()==before) std::this_thread::yield();
        ds_capture_stop();
        counter.stopped.store(true,std::memory_order_release);
        auto count = counter.calls.load();
        for (int j=0;j<20;++j) std::this_thread::yield();
        check(counter.calls.load()==count,"zero callbacks after stop returns");
    }
    // Concurrent subscribers race for the single slot; concurrent stops must also quiesce it safely.
    std::atomic<int> winners{0};
    std::array<std::thread,4> subscribers;
    counter.stopped=false;
    for (auto &t:subscribers) t=std::thread([&] { if(ds_capture_start(Counter::receive,&counter)==DS_OK) ++winners; });
    for (auto &t:subscribers) t.join();
    check(winners==1,"concurrent start has exactly one winner");
    for (auto &t:subscribers) t=std::thread([] { ds_capture_stop(); });
    for (auto &t:subscribers) t.join();
    counter.stopped=true;
    running=false; pump.join();
    ds_capture_stop();
    check(counter.after_stop==0 && successful==200,"concurrent lifetime has zero post-stop callbacks");
    check(ds_capture_start(nullptr,nullptr)==DSERR_INVALIDPARAM,"null callback rejected");
    printf("lifetime: cycles=%zu concurrent_winners=%d callbacks=%zu after_stop=%zu early_stop=%d\n",
           successful,winners.load(),counter.calls.load(),counter.after_stop.load(),early);
}
#endif

int main() {
    setenv("NFS_AUDIO_FAKE_DEVICE","1",1); // defense in depth; output fixtures never request a real device
    ds_set_peak_limiter(0);
    manual_proof();
#ifndef TIMEDTAP_BASELINE
    selection_proof(); recreation_proof(); snapshot_proof(); limiter_proof(); queue_proof(); null_proof(); lifetime_proof();
#endif
    ds_capture_stop();
    printf("timedtap: %d checks, %d failures\n",checks,failures);
    return failures ? 1 : 0;
}
