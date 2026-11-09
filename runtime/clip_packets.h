// Owned compressed samples and independently bounded asynchronous consumers.
#pragma once
#include <CoreMedia/CoreMedia.h>
#include <AudioToolbox/AudioToolbox.h>
#include <atomic>
#include <functional>
#include <memory>
#include <vector>
#include <thread>
namespace clip {
enum class Track { video, audio };
class Packet {
    CMSampleBufferRef sample_;
    CMFormatDescriptionRef format_;
public:
    const Track track;
    const size_t bytes;
    Packet(Track, CMSampleBufferRef);
    ~Packet();
    Packet(const Packet&)=delete;
    // Borrowed read-only references; retain the Packet to extend their lifetime.
    CMSampleBufferRef sample() const { return sample_; }
    CMFormatDescriptionRef format() const { return format_; }
};
using OwnedPacket=std::shared_ptr<const Packet>;
enum class Result { consumed, retry, failed };
class Sink {
    struct State;
    std::shared_ptr<State> state;
    std::thread consumerThread;
public:
    // Return retry promptly for backpressure. A blocked track can be bypassed
    // by the other track; order within each track is always preserved.
    // Own callback dependencies: an in-flight call may outlive the Sink handle.
    using Consumer=std::function<Result(const OwnedPacket&)>;
    Sink(Consumer, size_t packets=1024, size_t bytes=64*1024*1024);
    ~Sink();
    Sink(const Sink&)=delete;
    bool push(const OwnedPacket&);
    void close(); // no more input; existing packets drain independently
    void cancel(); // discard queued packets; an in-flight call owns its state until it returns
    bool failed() const;
    bool drained() const;
    size_t queued() const;
    size_t peakBytes() const;
    uint64_t delivered() const;
};
// Registration is on the capture thread before publishing; each sink owns its
// queue, thread and failure state. Consumers never execute on the publisher.
class Fanout {
    std::vector<std::shared_ptr<Sink>> sinks;
public:
    void add(const std::shared_ptr<Sink>& s) { sinks.push_back(s); }
    void remove(const std::shared_ptr<Sink>& sink) {
        if(!sink) return;
        sink->cancel();
        for(auto i=sinks.begin();i!=sinks.end();)
            if(*i==sink) i=sinks.erase(i); else ++i;
    }
    void publish(const OwnedPacket&);
    void close();
    void cancel();
    bool drained() const;
    bool healthy() const;
};
class AACEncoder {
    AudioConverterRef converter=nullptr;
    CMAudioFormatDescriptionRef format_=nullptr;
    std::vector<float> pending;
    // The converter may retain callback data until it asks for input again.
    std::vector<float> supplied;
    size_t cursor=0;
    int64_t packetFrame=0, firstFrame=0;
    uint32_t leading=0;
#ifndef CLIPREC_PCM_CONTROL
    uint32_t maxPacket=0;
#endif
    bool finalInput=false, finished=false;
    static OSStatus supply(AudioConverterRef,UInt32*,AudioBufferList*,AudioStreamPacketDescription**,void*);
public:
    // firstFrame: the timeline sample of the first input sample (a consumer joining mid-session).
    explicit AACEncoder(int64_t firstFrame=0);
    ~AACEncoder();
    OSStatus status=0;
    CMAudioFormatDescriptionRef format() const { return format_; }
    bool encode(const float*,size_t,bool,const std::function<void(OwnedPacket)>&);
};
}
