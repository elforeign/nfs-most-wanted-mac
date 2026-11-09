#include "audio_output.h"
#include "../../media/media.h"
#include <atomic>
#include <chrono>
#include <mutex>
#include <thread>
#include <vector>

namespace {
int null_open(void *, unsigned rate, unsigned channels, uint32_t mask) {
    return rate == 48000 && channels == 6 && mask == 0x3f;
}
int null_write(void *, const float *, size_t) { return 1; }
size_t null_queued(void *) { return 0; }
void null_close(void *) {}
}
struct WinAudioOutput {
    DsDevice *device;
    WinPcmEndpoint endpoint;
    std::atomic<bool> stop{false};
    std::atomic<int> error{0};
    std::mutex mutex;
    std::thread render_thread;
    WinAudioOutput(DsDevice *d, const WinPcmEndpoint &e) : device(d), endpoint(e) {}
};
extern "C" WinPcmEndpoint win_pcm_null_endpoint(void) {
    return {nullptr, null_open, null_write, null_queued, null_close};
}
extern "C" DsResult win_audio_output_pump(WinAudioOutput *out, size_t frames) {
    if (!out || frames > 480000) return DSERR_INVALIDPARAM;
    std::lock_guard<std::mutex> guard(out->mutex);
    if (out->error.load()) return DSERR_NODRIVER;
    float pcm[256 * 6];
    while (frames) {
        size_t count = frames > 256 ? 256 : frames;
        DsResult result = ds_device_render(out->device, pcm, count);
        if (result != DS_OK) return result;
        if (!out->endpoint.write(out->endpoint.user, pcm, count)) {
            out->error = 1;
            return DSERR_NODRIVER;
        }
        frames -= count;
    }
    return DS_OK;
}
extern "C" WinAudioOutput *win_audio_output_create(DsDevice *device, const WinPcmEndpoint *endpoint,
                                                   int threaded) {
    if (!device || !endpoint || !endpoint->open || !endpoint->write ||
        !endpoint->queued || !endpoint->close) return nullptr;
    if (!endpoint->open(endpoint->user, 48000, 6, 0x3f)) {
        endpoint->close(endpoint->user); return nullptr;
    }
    auto *out = new WinAudioOutput(device, *endpoint);
    if (threaded) out->render_thread = std::thread([out] {
        auto next = std::chrono::steady_clock::now();
        while (!out->stop.load()) {
            if (out->endpoint.queued(out->endpoint.user) < 1024) {
                if (win_audio_output_pump(out, 256) != DS_OK) break;
            }
            // Bound both null and native endpoint progress to the PCM clock.
            next += std::chrono::nanoseconds(5333333);
            std::this_thread::sleep_until(next);
        }
    });
    return out;
}
extern "C" void win_audio_output_destroy(WinAudioOutput *out) {
    if (!out) return;
    out->stop = true;
    if (out->render_thread.joinable()) out->render_thread.join();
    out->endpoint.close(out->endpoint.user);
    delete out;
}

// Native playback uses the existing device-sink queue/cursor model through
// this PCM implementation. Manual fixtures pull the same PCM endpoint above.
// Encoded formats and hardware/default-device recovery are not implemented.
namespace media {
struct AudioOutput::Impl {
    WinPcmEndpoint endpoint{};
    bool opened = false;
};
AudioOutput::AudioOutput() : d(new Impl) {}
AudioOutput::~AudioOutput() { close(); }
bool AudioOutput::open(OutputMode mode, int rate) {
    close();
    if (mode != OutputMode::Pcm51 || rate != 48000) return false;
    d->endpoint = win_pcm_endpoint();
    d->opened = d->endpoint.open(d->endpoint.user, rate, 6, 0x3f) != 0;
    if (!d->opened) d->endpoint.close(d->endpoint.user);
    return d->opened;
}
bool AudioOutput::open_offline(OutputMode mode, int rate) {
    close();
    if (mode != OutputMode::Pcm51 || rate != 48000) return false;
    d->endpoint = win_pcm_null_endpoint();
    return d->opened = d->endpoint.open(d->endpoint.user, rate, 6, 0x3f) != 0;
}
void AudioOutput::close() {
    if (d->opened) d->endpoint.close(d->endpoint.user);
    d->opened = false;
}
void AudioOutput::write(const float *pcm, size_t frames) {
    if (d->opened && !d->endpoint.write(d->endpoint.user, pcm, frames)) close();
}
size_t AudioOutput::queued_frames() const { return d->opened ? d->endpoint.queued(d->endpoint.user) : 0; }
size_t AudioOutput::pending_frames() const { return 0; }
int AudioOutput::minimum_queue_frames() const { return 512; }
AudioCallbackStats AudioOutput::take_callback_stats(bool) {
    AudioCallbackStats stats;
    stats.queued_frames = queued_frames();
    stats.queue_bytes = stats.queued_frames * 24;
    return stats;
}
std::string AudioOutput::describe() const { return "Windows PCM 5.1"; }
bool AudioOutput::stalled(double) const { return !d->opened; }
uint32_t AudioOutput::device() const { return d->opened ? 1 : 0; }
bool AudioOutput::exclusive() const { return false; }
void AudioOutput::stop_for_test() { close(); }
OutputMode preferred_output_mode(std::string *why, bool) {
    if (why) *why = "Windows PCM endpoint";
    return OutputMode::Pcm51;
}
uint64_t output_devices_generation() { return 0; }
uint32_t default_output_device() { return 1; }
bool device_alive(uint32_t dev) { return dev == 1; }
std::string device_name(uint32_t) { return "default PCM endpoint"; }
void fail_next_opens(int) {}
}
