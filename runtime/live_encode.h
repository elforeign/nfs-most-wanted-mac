// Local live-profile encoding. Transport and broadcast activation are separate.
#pragma once
#include <CoreVideo/CoreVideo.h>
#include <VideoToolbox/VideoToolbox.h>
#include <atomic>
#include <memory>

namespace clip { class Sink; }

namespace live {
struct Profile {
    unsigned width=0, height=0;
    int bitrate=0;
    static Profile youtube(unsigned width, unsigned height);
};
// Read once when capture is explicitly started. Never starts capture itself.
bool enabled();

// Calls belong to the capture queue; VT callbacks may run on another thread.
// Each session has its own CBR settings and bounded outstanding surfaces.
class Encoder {
    VTCompressionSessionRef session=nullptr;
    Profile profile_;
    std::shared_ptr<clip::Sink> sink;
    std::atomic<OSStatus> status_{0};
    std::atomic<unsigned> pending_{0};
    std::atomic<uint64_t> frames_{0}, bytes_{0};
    CMTime nextIDR=kCMTimeZero, lastPTS=kCMTimeInvalid;
    static void compressed(void*,void*,OSStatus,VTEncodeInfoFlags,CMSampleBufferRef);
    void fail(OSStatus);
public:
    // Leave half of the recorder's eight-surface pool available to capture.
    static constexpr unsigned maxPending=4;
    explicit Encoder(Profile, std::shared_ptr<clip::Sink> output={});
    ~Encoder();
    Encoder(const Encoder&)=delete;
    bool encode(CVPixelBufferRef,CMTime,CMTime);
    void complete();
    OSStatus status() const { return status_.load(std::memory_order_acquire); }
    unsigned pending() const { return pending_.load(); }
    uint64_t frames() const { return frames_.load(); }
    uint64_t bytes() const { return bytes_.load(); }
    const Profile& profile() const { return profile_; }
    // A surface is shareable only if its pixel format and both dimensions match.
    bool accepts(CVPixelBufferRef) const;
    // Reject a wrong-codec packet before handing it to any live consumer.
    bool packet(CMSampleBufferRef);
};
}
