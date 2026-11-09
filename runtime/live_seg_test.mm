// Ingest original/fixed: 540000-tick GOP emits 1/0; first PIDs 17,0,4096 / 0,4096,17.
// Synthetic 12-second hardware H.264 + actual AAC, with independent TS probing.
// Six durations 2.000000..2.009333 s, 720 video / 565 input AAC, 0 DTS faults.
// Non-IDR cut fails after 1 segment; reset DTS 90360 <= 268440 is rejected.
// Off: 0 allocations/0 sinks; default-On control: 15/2. Held limit: 2/2.
// Further controls and the independent probe live in live_seg_test.py.
// Stop at 361500 ticks: original 2 segments/finish=0, fixed 3/finish=1, 241 video.
#include "live_encode.h"
#include "live_seg.h"
#import <Foundation/Foundation.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <mutex>
#include <new>
#include <thread>
extern "C" void report_note(const char *, ...) {}
static std::atomic<bool> countAllocations{false};
static std::atomic<size_t> allocations{0},sinks{0};
void *operator new(size_t n) {
    if(countAllocations.load()) ++allocations;
    if(void *p=malloc(n?n:1)) return p;
    throw std::bad_alloc();
}
void operator delete(void *p) noexcept { free(p); }
void *operator new[](size_t n) { return ::operator new(n); }
void operator delete[](void *p) noexcept { ::operator delete(p); }
template<class... Args> static std::shared_ptr<clip::Sink> countedSink(Args&&... args) {
    ++sinks; return std::make_shared<clip::Sink>(std::forward<Args>(args)...);
}
#ifndef LIVE_SEG_SOURCE
#define LIVE_SEG_SOURCE "live_seg.mm"
#endif
#include LIVE_SEG_SOURCE

static void check(bool ok,const char *why) {
    if(!ok) { fprintf(stderr,"FAIL segment: %s\n",why); exit(1); }
}
void offProof() {
    setenv("NFS_LIVE_ENCODE","off",1);
    allocations=0; sinks=0; countAllocations=true;
    auto feed=live::SegmentFeed::create(live::enabled());
    countAllocations=false;
    printf("off: allocations=%zu sinks=%zu feed=%d\n",allocations.load(),sinks.load(),feed?1:0);
    check(!feed && !allocations && !sinks,"Off has zero allocations and sinks");
}
static int64_t stamp(const clip::OwnedPacket& p) {
    return CMTimeConvertScale(CMSampleBufferGetPresentationTimeStamp(p->sample()),90000,kCMTimeRoundingMethod_RoundHalfAwayFromZero).value;
}
static CVPixelBufferRef image() {
    CVPixelBufferRef pixel=nullptr;
    NSDictionary *attrs=@{(id)kCVPixelBufferIOSurfacePropertiesKey:@{}};
    check(!CVPixelBufferCreate(nullptr,320,180,kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,(__bridge CFDictionaryRef)attrs,&pixel),"NV12 fixture");
    CVPixelBufferLockBaseAddress(pixel,0);
    for(unsigned p=0;p<2;++p) memset(CVPixelBufferGetBaseAddressOfPlane(pixel,p),p?128:80,CVPixelBufferGetBytesPerRowOfPlane(pixel,p)*CVPixelBufferGetHeightOfPlane(pixel,p));
    CVPixelBufferUnlockBaseAddress(pixel,0);
    return pixel;
}
static void drain(const std::shared_ptr<clip::Sink>& sink) {
    auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(10);
    while(!sink->drained() && !sink->failed() && std::chrono::steady_clock::now()<deadline) usleep(1000);
    check(sink->drained() && !sink->failed(),"packet sink drained");
}
static std::vector<clip::OwnedPacket> fixture() {
    std::mutex lock;
    std::vector<clip::OwnedPacket> packets;
    auto sink=std::make_shared<clip::Sink>([&](const clip::OwnedPacket& p) {
        std::lock_guard<std::mutex> guard(lock); packets.push_back(p); return clip::Result::consumed;
    });
    live::Encoder encoder(live::Profile::youtube(320,180),sink);
    check(!encoder.status(),"hardware encoder available");
    for(int frame=0;frame<720;++frame) {
        auto pixel=image();
        check(encoder.encode(pixel,CMTimeMake(frame,60),CMTimeMake(1,60)),"encode frame");
        CVPixelBufferRelease(pixel); encoder.complete();
    }
    drain(sink); sink.reset();
    check(packets.size()==720,"720 video samples");
    clip::AACEncoder audio; check(!audio.status,"AAC encoder available");
    std::vector<float> pcm(48000*12*2);
    for(size_t i=0;i<pcm.size()/2;++i) pcm[2*i]=pcm[2*i+1]=0.2*sin(i*440*6.283185307179586/48000);
    check(audio.encode(pcm.data(),pcm.size()/2,true,[&](clip::OwnedPacket p){ packets.push_back(p); }),"encode AAC");
    std::stable_sort(packets.begin(),packets.end(),[](const auto& a,const auto& b){ return stamp(a)<stamp(b); });
    return packets;
}
static void run(const std::vector<clip::OwnedPacket>& packets,const char *path) {
    unsigned count=0;
    live::Segmenter segmenter([&](live::OwnedSegment s) {
        auto name=std::filesystem::path(path)/(std::to_string(s->sequence)+".ts");
        FILE *f=fopen(name.c_str(),"wb"); check(f,"fixture segment file");
        check(fwrite(s->bytes.data(),1,s->bytes.size(),f)==s->bytes.size(),"fixture segment bytes"); fclose(f);
        printf("segment %llu start=%lld end=%lld bytes=%zu\n",(unsigned long long)s->sequence,(long long)s->start90k,(long long)s->end90k,s->bytes.size());
        ++count; return true;
    });
    for(auto &p:packets) check(segmenter.consume(p)==clip::Result::consumed,"mux packets");
    check(segmenter.finish(),"finish segments");
    auto stats=segmenter.stats();
    printf("on: segments=%u video=%llu audio=%llu non_monotonic=%llu pending_peak=%zu held_peak=%zu\n",count,
        (unsigned long long)stats.video,(unsigned long long)stats.audio,(unsigned long long)stats.nonMonotonic,stats.pendingPeak,stats.heldPeak);
    check(count==6 && stats.video==720 && stats.audio>=562 && !stats.failed && !stats.nonMonotonic,"six 2-second segments");
}
static void stopAfterIDR(const std::vector<clip::OwnedPacket>& packets,const char *path) {
    unsigned count=0;
    live::Segmenter segmenter([&](live::OwnedSegment s) {
        auto name=std::filesystem::path(path)/(std::to_string(s->sequence)+".ts");
        FILE *f=fopen(name.c_str(),"wb"); check(f,"stop segment file");
        check(fwrite(s->bytes.data(),1,s->bytes.size(),f)==s->bytes.size(),"stop segment bytes"); fclose(f);
        if(count==2) check(s->start90k==360000 && s->end90k==361500,"one-frame final interval");
        ++count; return true;
    });
    for(auto &p:packets) {
        if(stamp(p)>=361500) continue;
        check(segmenter.consume(p)==clip::Result::consumed,"stop mux packets");
    }
    bool finished=segmenter.finish();
    auto stats=segmenter.stats();
    printf("stop: end=361500 segments=%u video=%llu finish=%d failed=%d\n",count,
        (unsigned long long)stats.video,finished,stats.failed);
    check(finished && !stats.failed && count==3 && stats.video==241,"stop preserves final IDR");
    check(segmenter.finish(),"stop finish is idempotent");
}
static void controls(const std::vector<clip::OwnedPacket>& packets) {
    // Retained callback outputs count toward the hard limit, even outside us.
    std::vector<live::OwnedSegment> held;
    live::SegmentLimits limits; limits.heldSegments=2;
    live::Segmenter bounded([&](live::OwnedSegment s){ held.push_back(s); return true; },limits);
    size_t accepted=0;
    for(auto &p:packets) {
        if(bounded.consume(p)==clip::Result::failed) break;
        ++accepted;
    }
    check(held.size()==2 && bounded.stats().failed && bounded.stats().heldPeak==2,"N held segments stops live");
    printf("held bound: held=%zu accepted=%zu failed=1\n",held.size(),accepted);
    limits.pendingBytes=1;
    live::Segmenter tiny({},limits);
    check(tiny.consume(packets[0])==clip::Result::failed && tiny.stats().pendingPeak==0,"pending byte cap");
    limits={}; limits.segmentBytes=188;
    live::Segmenter small({},limits); bool failed=false;
    for(auto &p:packets) if(small.consume(p)==clip::Result::failed) { failed=true; break; }
    check(failed && small.stats().failed,"TS byte cap");
    live::Segmenter reject([](live::OwnedSegment){ return false; }); failed=false;
    for(auto &p:packets) if(reject.consume(p)==clip::Result::failed) { failed=true; break; }
    check(failed && reject.stats().failed,"callback rejection");
    // Cross-track delivery skew is legal; order within each track is preserved.
    std::vector<clip::OwnedPacket> skewed=packets;
    std::stable_sort(skewed.begin(),skewed.end(),[](const auto &a,const auto &b) {
        return stamp(a)+(a->track==clip::Track::video?18000:0)<stamp(b)+(b->track==clip::Track::video?18000:0);
    });
    unsigned segments=0;
    live::Segmenter skew([&](live::OwnedSegment){ ++segments; return true; });
    for(auto &p:skewed) check(skew.consume(p)==clip::Result::consumed,"200 ms cross-track skew");
    check(skew.finish() && segments==6,"skewed stream complete");
    live::Segmenter duplicate({});
    check(duplicate.consume(packets[0])==clip::Result::consumed && duplicate.consume(packets[0])==clip::Result::failed && duplicate.stats().nonMonotonic==1,"duplicate DTS rejected");
    // Exercise the exact two-sink integration: recorder H.264 is filtered out.
    auto feed=live::SegmentFeed::create(true);
    size_t videos=0,audios=0;
    for(auto &p:packets) {
        if(p->track==clip::Track::video) { check(feed->video->push(p),"live video sink"); ++videos; }
        else ++audios;
        check(feed->audio->push(p),"audio-only tap");
        drain(feed->video); drain(feed->audio);
    }
    check(feed->finish(),"feed finalized");
    auto stats=feed->stats();
    check(stats.video==videos && stats.audio==audios && stats.segments==6,"no recording video cross-route");
    printf("feed: sinks=2 video=%zu audio=%zu segments=%llu; bounds=4/4 skew=200ms duplicate_dts=1\n",videos,audios,(unsigned long long)stats.segments);
}
static void overlong(const std::vector<clip::OwnedPacket>& packets) {
    unsigned count=0; int64_t longest=0;
    live::Segmenter segmenter([&](live::OwnedSegment s) {
        ++count; longest=std::max(longest,s->end90k-s->start90k); return true;
    });
    for(const auto &p:packets) {
        if(p->track==clip::Track::video && stamp(p)>0 && stamp(p)<540000) continue;
        if(segmenter.consume(p)==clip::Result::failed || count) break;
    }
    printf("overlong: segments=%u longest_ticks=%lld failed=%d\n",count,(long long)longest,segmenter.stats().failed);
    check(segmenter.stats().failed && !count && !longest,"five-second hard segment limit");
}
int main(int argc,char **argv) { @autoreleasepool {
    check(argc==3,"mode and scratch directory");
    if(!strcmp(argv[1],"off")) {
        // Link-time allocation/sink interception is supplied by the Python driver.
        extern void offProof(); offProof(); return 0;
    }
    auto packets=fixture();
    if(!strcmp(argv[1],"overlong")) overlong(packets);
    else if(!strcmp(argv[1],"controls")) controls(packets);
    else if(!strcmp(argv[1],"stop")) stopAfterIDR(packets,argv[2]);
    else run(packets,argv[2]);
    return 0;
} }
