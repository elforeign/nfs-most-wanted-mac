#include "replay.h"
#include "clip_packets.h"
#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <chrono>
namespace replay {
struct Ring::State {
    struct GOP { CMTime start; std::vector<clip::OwnedPacket> video; };
    std::deque<GOP> gops;
    std::deque<clip::OwnedPacket> audio;
    CMFormatDescriptionRef videoFormat=nullptr, audioFormat=nullptr;
    CMTime end=kCMTimeInvalid, lastVideo=kCMTimeInvalid, lastAudio=kCMTimeInvalid;
    Stats counts;
    ~State() { if(videoFormat) CFRelease(videoFormat); if(audioFormat) CFRelease(audioFormat); }
    void clear() {
        gops.clear(); audio.clear(); counts.bytes=0;
        end=lastVideo=lastAudio=kCMTimeInvalid;
    }
    void dropAudio() { counts.bytes-=audio.front()->bytes; audio.pop_front(); }
    void trimAudio() {
        if(audio.empty()) return;
        CMTime anchor=gops.empty()?lastAudio:gops.front().start;
        CMTime cut=CMTimeSubtract(anchor,CMTimeMake(prerollFrames,48000));
        while(!audio.empty()) {
            auto s=audio.front()->sample();
            if(CMTimeCompare(CMTimeAdd(CMSampleBufferGetPresentationTimeStamp(s),CMSampleBufferGetDuration(s)),cut)>0) break;
            dropAudio();
        }
    }
    void dropGOP() {
        for(auto &p:gops.front().video) counts.bytes-=p->bytes;
        gops.pop_front(); ++counts.evicted;
        trimAudio();
    }
    void bound(CMTime pinned) {
        CMTime cut=CMTimeSubtract(end,CMTimeMake(30,1));
        if(CMTIME_IS_NUMERIC(pinned) && CMTimeCompare(pinned,cut)<0) cut=pinned;
        // Strict comparison keeps the preceding GOP even at an exact keyframe cut.
        while(gops.size()>1 && CMTimeCompare(gops[1].start,cut)<0) dropGOP();
        trimAudio();
        while(counts.bytes>retentionCap && !gops.empty()) dropGOP();
        while(counts.bytes>retentionCap && !audio.empty()) dropAudio();
        counts.peakBytes=std::max(counts.peakBytes,counts.bytes);
    }
};
Ring::Ring()=default;
Ring::~Ring()=default;
void Ring::arm() {
    std::lock_guard<std::mutex> guard(lock);
    if(!state) { state=std::make_unique<State>(); ++lifetime; }
    armed.store(true,std::memory_order_release);
}
void Ring::disarm() {
    std::lock_guard<std::mutex> guard(lock);
    armed.store(false,std::memory_order_release); state.reset(); ++lifetime;
    changed.notify_all();
}
static bool keyframe(CMSampleBufferRef s) {
    CFArrayRef attachments=CMSampleBufferGetSampleAttachmentsArray(s,false);
    if(!attachments || !CFArrayGetCount(attachments)) return true;
    auto dictionary=(CFDictionaryRef)CFArrayGetValueAtIndex(attachments,0);
    return !CFEqual(CFDictionaryGetValue(dictionary,kCMSampleAttachmentKey_NotSync) ?: kCFBooleanFalse,kCFBooleanTrue);
}
clip::Result Ring::consume(const clip::OwnedPacket& p) {
    if(!armed.load(std::memory_order_acquire)) return clip::Result::consumed;
    return consumeArmed(p);
}
clip::Result Ring::consumeArmed(const clip::OwnedPacket& p) {
    std::lock_guard<std::mutex> guard(lock);
    if(!state) return clip::Result::consumed;
    auto &s=*state;
    auto sample=p->sample();
    CMTime pts=CMSampleBufferGetPresentationTimeStamp(sample), duration=CMSampleBufferGetDuration(sample);
    if(!p->format() || !CMTIME_IS_NUMERIC(pts) || !CMTIME_IS_NUMERIC(duration) || CMTimeCompare(duration,kCMTimeZero)<=0) {
        ++s.counts.rejected; return clip::Result::consumed;
    }
    bool video=p->track==clip::Track::video;
    auto &format=video?s.videoFormat:s.audioFormat;
    if(format && !CMFormatDescriptionEqual(format,p->format())) {
        s.clear(); ++s.counts.generation;
        CFRelease(format); format=nullptr;
    }
    if(!format) { format=p->format(); CFRetain(format); }
    if(!s.counts.generation) s.counts.generation=1;
    auto &last=video?s.lastVideo:s.lastAudio;
    if(CMTIME_IS_NUMERIC(last) && CMTimeCompare(pts,last)<=0) {
        ++s.counts.rejected; return clip::Result::consumed;
    }
    last=pts;
    // Reserve before retaining a reference: even the in-progress append must
    // fit, with transferCap left for the independently bounded sink queue.
    if(p->bytes>retentionCap) {
        while(!s.gops.empty()) s.dropGOP();
        ++s.counts.rejected; return clip::Result::consumed;
    }
    while(s.counts.bytes>retentionCap-p->bytes) {
        if(!s.gops.empty()) s.dropGOP();
        else s.dropAudio();
    }
    if(video) {
        if(keyframe(sample)) s.gops.push_back({pts,{}});
        if(s.gops.empty()) { ++s.counts.rejected; return clip::Result::consumed; }
        s.gops.back().video.push_back(p);
        s.end=CMTimeAdd(pts,duration);
    } else s.audio.push_back(p);
    s.counts.bytes+=p->bytes;
    s.bound(pinnedStart);
    changed.notify_all();
    return clip::Result::consumed;
}
Stats Ring::stats() const {
    std::lock_guard<std::mutex> guard(lock);
    if(!state) return {};
    Stats out=state->counts; out.armed=armed.load(); out.gops=state->gops.size();
    out.end=state->end;
    if(!state->gops.empty()) out.first=state->gops.front().start;
    out.packets=state->audio.size();
    for(auto &g:state->gops) out.packets+=g.video.size();
    return out;
}
std::vector<clip::OwnedPacket> Ring::inspect() const {
    std::lock_guard<std::mutex> guard(lock);
    std::vector<clip::OwnedPacket> out;
    if(state) {
        for(auto &g:state->gops) out.insert(out.end(),g.video.begin(),g.video.end());
        out.insert(out.end(),state->audio.begin(),state->audio.end());
    }
    return out;
}
SavePoint Ring::reserve(CMTime end,double timeout) {
    SavePoint out; out.end=end; out.start=CMTimeSubtract(end,CMTimeMake(30,1));
    out.deadline=std::chrono::steady_clock::now()+std::chrono::duration_cast<std::chrono::steady_clock::duration>(std::chrono::duration<double>(timeout));
    std::lock_guard<std::mutex> guard(lock);
    if(!state || !armed.load()) { out.error="Arm replay first"; return out; }
    if(!CMTIME_IS_NUMERIC(end) || CMTimeCompare(out.start,kCMTimeZero)<0 || state->gops.empty() ||
       CMTimeCompare(state->gops.front().start,out.start)>0) {
        out.error="Replay is warming up (30 seconds required)"; return out;
    }
    if(CMTIME_IS_NUMERIC(pinnedStart)) { out.error="Replay already saving"; return out; }
    pinnedStart=out.start;
    out.token=++saveToken; out.generation=state->counts.generation; out.lifetime=lifetime;
    return out;
}
Snapshot Ring::snapshot(CMTime end,double timeout) { return snapshot(reserve(end,timeout)); }
Snapshot Ring::snapshot(const SavePoint& point) {
    Snapshot out; out.end=point.end; out.start=point.start; out.error=point.error;
    if(!out.error.empty()) return out;
    std::unique_lock<std::mutex> guard(lock);
    if(!point.token || point.token!=saveToken || !CMTIME_IS_NUMERIC(pinnedStart)) {
        out.error="Replay save reservation expired"; return out;
    }
    CMTime end=point.end;
    auto covered=[&] {
        if(!state || lifetime!=point.lifetime || state->counts.generation!=point.generation) return true;
        auto &s=*state;
        bool video=CMTIME_IS_NUMERIC(s.end) && CMTimeCompare(s.end,end)>=0;
        bool audio=!s.audioFormat || (!s.audio.empty() && CMTimeCompare(CMTimeAdd(
            CMSampleBufferGetPresentationTimeStamp(s.audio.back()->sample()),
            CMSampleBufferGetDuration(s.audio.back()->sample())),end)>=0);
        return video && audio;
    };
    bool ready=changed.wait_until(guard,point.deadline,covered);
    pinnedStart=kCMTimeInvalid;
    if(!ready) out.error="Replay encoder watermark timed out";
    else if(!state || lifetime!=point.lifetime || state->counts.generation!=point.generation) out.error="Replay stream changed while saving";
    else {
        auto &s=*state;
        auto first=s.gops.end();
        for(auto i=s.gops.begin();i!=s.gops.end() && CMTimeCompare(i->start,out.start)<=0;++i) first=i;
        if(first==s.gops.end()) out.error="Replay is warming up (30 seconds required)";
        else {
            CMTime audioStart=CMTimeSubtract(first->start,CMTimeMake(prerollFrames,48000));
            for(auto i=first;i!=s.gops.end();++i) for(auto &p:i->video)
                if(CMTimeCompare(CMSampleBufferGetPresentationTimeStamp(p->sample()),end)<0) out.packets.push_back(p);
            for(auto &p:s.audio) {
                auto sample=p->sample();
                CMTime pts=CMSampleBufferGetPresentationTimeStamp(sample);
                if(CMTimeCompare(CMTimeAdd(pts,CMSampleBufferGetDuration(sample)),audioStart)>0 && CMTimeCompare(pts,end)<0)
                    out.packets.push_back(p);
            }
            for(auto &p:out.packets) out.bytes+=p->bytes;
            if(out.bytes>hardCap) { out.packets.clear(); out.error="Replay snapshot exceeds memory limit"; }
        }
    }
    return out;
}
std::shared_ptr<Ring> attach(clip::Fanout& outputs, std::shared_ptr<Ring> ring, std::shared_ptr<clip::Sink>* sink) {
    const char *value=std::getenv("NFS_REPLAY");
    if(!ring && (!value || std::strcmp(value,"on"))) return {};
    if(!ring) { ring=std::make_shared<Ring>(); ring->arm(); }
    if(!ring->stats().armed) return ring;
    auto attached=std::make_shared<clip::Sink>([ring](const clip::OwnedPacket& p) {
        return ring->consume(p);
    },4096,transferCap);
    outputs.add(attached);
    if(sink) *sink=attached;
    return ring;
}
}
