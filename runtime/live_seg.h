// In-memory MPEG-TS output. Callbacks run on the packet consumer, never capture.
#pragma once
#include "clip_packets.h"
#include <cstdint>

namespace live {
struct Segment {
    uint64_t sequence=0;
    int64_t start90k=0, end90k=0;
    std::vector<uint8_t> bytes;
};
using OwnedSegment=std::shared_ptr<const Segment>;
struct SegmentLimits {
    size_t pendingBytes=32*1024*1024, pendingPackets=1024;
    size_t segmentBytes=16*1024*1024, heldSegments=2;
};
struct SegmentStats {
    uint64_t segments=0, video=0, audio=0, nonMonotonic=0;
    size_t pendingPeak=0, heldPeak=0;
    bool failed=false;
    const char *reason=nullptr; // first failure, a static token for the status row and session log
};
class Segmenter {
    struct State;
    std::unique_ptr<State> state;
public:
    // A callback must return promptly. Retaining N segments is allowed; the next
    // emission fails independently rather than growing a transport queue.
    // Callbacks must not re-enter the segmenter.
    using Output=std::function<bool(OwnedSegment)>;
    explicit Segmenter(Output,SegmentLimits={});
    ~Segmenter();
    clip::Result consume(const clip::OwnedPacket&);
    bool finish(); // after both input sinks drain; emits a final partial GOP
    SegmentStats stats() const;
};
// One video sink and one audio-only fanout tap. The observer is for independent
// compressed-packet consumers; recording video never enters the live muxer.
class SegmentFeed {
    std::shared_ptr<Segmenter> segmenter;
public:
    std::shared_ptr<clip::Sink> video, audio;
    static std::unique_ptr<SegmentFeed> create(bool active,Segmenter::Output={},
        std::shared_ptr<clip::Sink> observer={});
    bool drained() const;
    bool finish();
    SegmentStats stats() const { return segmenter->stats(); }
};
}
