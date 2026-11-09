// Production retention with synthetic compressed H.264 and AAC packet metadata.
// Measured baseline and deliberately broken controls are in replay_test.py.
#include "replay.h"
#include "clip_packets.h"
#import <VideoToolbox/VideoToolbox.h>
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <new>
#include <thread>

static std::atomic<size_t> allocations{0};
void *operator new(size_t n) { ++allocations; if(auto p=malloc(n?n:1)) return p; throw std::bad_alloc(); }
void operator delete(void *p) noexcept { free(p); }
void *operator new[](size_t n) { return ::operator new(n); }
void operator delete[](void *p) noexcept { free(p); }
static void check(bool ok,const char *what) {
    if(!ok) { fprintf(stderr,"FAIL replay: %s\n",what); exit(1); }
}
static bool key(const clip::OwnedPacket& p) {
    auto a=CMSampleBufferGetSampleAttachmentsArray(p->sample(),false);
    if(!a || !CFArrayGetCount(a)) return true;
    auto d=(CFDictionaryRef)CFArrayGetValueAtIndex(a,0);
    return CFDictionaryGetValue(d,kCMSampleAttachmentKey_NotSync)!=kCFBooleanTrue;
}
struct Fixture {
    std::vector<clip::OwnedPacket> video;
    static void encoded(void *context,void*,OSStatus status,VTEncodeInfoFlags,CMSampleBufferRef sample) {
        check(!status && sample,"fixture encoder output");
        ((Fixture*)context)->video.push_back(std::make_shared<clip::Packet>(clip::Track::video,sample));
    }
    explicit Fixture(unsigned width=160,bool markers=false,CMVideoCodecType codec=kCMVideoCodecType_H264) {
        VTCompressionSessionRef encoder=nullptr;
        check(!VTCompressionSessionCreate(nullptr,width,96,codec,nullptr,nullptr,nullptr,encoded,this,&encoder),"fixture video encoder");
        check(!VTSessionSetProperty(encoder,kVTCompressionPropertyKey_AllowFrameReordering,kCFBooleanFalse),"fixture no reordering");
        check(!VTSessionSetProperty(encoder,kVTCompressionPropertyKey_MaxKeyFrameInterval,(__bridge CFNumberRef)@20),"fixture GOP length");
        for(unsigned i=0;i<20;++i) {
            CVPixelBufferRef image=nullptr;
            check(!CVPixelBufferCreate(nullptr,width,96,kCVPixelFormatType_32BGRA,nullptr,&image),"fixture image");
            CVPixelBufferLockBaseAddress(image,0);
            auto data=(uint8_t*)CVPixelBufferGetBaseAddress(image);
            size_t stride=CVPixelBufferGetBytesPerRow(image);
            for(unsigned y=0;y<96;++y) for(unsigned x=0;x<width;++x) {
                auto pixel=data+y*stride+4*x;
                pixel[0]=32; pixel[1]=96+i*2; pixel[2]=192; pixel[3]=255;
                if(markers) pixel[0]=pixel[1]=pixel[2]=((i>>(x*5/width))&1)?224:32;
            }
            CVPixelBufferUnlockBaseAddress(image,0);
            check(!VTCompressionSessionEncodeFrame(encoder,image,CMTimeMake(i,10),CMTimeMake(1,10),nullptr,nullptr,nullptr),"fixture encode");
            CVPixelBufferRelease(image);
        }
        check(!VTCompressionSessionCompleteFrames(encoder,kCMTimeInvalid),"fixture flush");
        VTCompressionSessionInvalidate(encoder); CFRelease(encoder);
        check(video.size()==20 && key(video[0]) && !key(video[1]),"fixture has one real I/P GOP");
    }
};
static clip::OwnedPacket retime(const clip::OwnedPacket& p,int64_t frame) {
    CMSampleBufferRef sample=nullptr;
    CMSampleTimingInfo timing={CMTimeMake(1,10),CMTimeMake(frame,10),kCMTimeInvalid};
    check(!CMSampleBufferCreateCopyWithNewTiming(nullptr,p->sample(),1,&timing,&sample),"retime fixture");
    auto out=std::make_shared<clip::Packet>(p->track,sample); CFRelease(sample); return out;
}
static clip::OwnedPacket makePacket(CMFormatDescriptionRef format,clip::Track track,CMTime pts,CMTime duration,size_t bytes,bool sync=true) {
    CMBlockBufferRef block=nullptr; CMSampleBufferRef sample=nullptr;
    check(!CMBlockBufferCreateWithMemoryBlock(nullptr,nullptr,bytes,nullptr,nullptr,0,bytes,0,&block),"fixture payload");
    // Touch every payload byte so byte equivalence is meaningful.
    check(!CMBlockBufferFillDataBytes(0x5a,block,0,bytes),"fixture fill");
    CMSampleTimingInfo timing={duration,pts,kCMTimeInvalid};
    check(!CMSampleBufferCreateReady(nullptr,block,format,1,1,&timing,1,&bytes,&sample),"fixture packet");
    if(!sync) {
        auto a=CMSampleBufferGetSampleAttachmentsArray(sample,true);
        CFDictionarySetValue((CFMutableDictionaryRef)CFArrayGetValueAtIndex(a,0),kCMSampleAttachmentKey_NotSync,kCFBooleanTrue);
    }
    auto out=std::make_shared<clip::Packet>(track,sample);
    CFRelease(sample); CFRelease(block); return out;
}
struct Audio {
    CMAudioFormatDescriptionRef format=nullptr;
    explicit Audio(unsigned channels=2) {
        AudioStreamBasicDescription asbd={}; asbd.mSampleRate=48000;
        asbd.mFormatID=kAudioFormatMPEG4AAC; asbd.mFormatFlags=kMPEG4Object_AAC_LC;
        asbd.mFramesPerPacket=1024; asbd.mChannelsPerFrame=channels;
        check(!CMAudioFormatDescriptionCreate(nullptr,&asbd,0,nullptr,0,nullptr,nullptr,&format),"synthetic AAC format");
    }
    ~Audio() { CFRelease(format); }
    clip::OwnedPacket packet(int n) {
        return makePacket(format,clip::Track::audio,CMTimeMake(n*1024ll-2112,48000),CMTimeMake(1024,48000),512);
    }
};
static void stream(replay::Ring& ring,Fixture& f) {
    Audio audio; int a=0;
    for(int i=0;i<900;++i) {
        ring.consume(retime(f.video[i%20],i));
        while(CMTimeCompare(CMTimeMake(a*1024ll-2112,48000),CMTimeMake(i+1,10))<0) ring.consume(audio.packet(a++));
    }
}
static void accounting(replay::Ring& ring) {
    size_t bytes=0; auto packets=ring.inspect();
    for(auto &p:packets) bytes+=p->bytes;
    auto s=ring.stats();
    check(bytes==s.bytes && packets.size()==s.packets,"all video/AAC references counted");
    check(s.bytes<=replay::retentionCap && s.peakBytes<=replay::retentionCap,"retention plus transfer reservation bounded");
}
static void span(Fixture& f) {
    replay::Ring ring; ring.arm(); stream(ring,f); auto s=ring.stats();
    printf("span: first=%.6f end=%.6f seconds=%.6f gops=%zu evicted=%llu bytes=%zu\n",CMTimeGetSeconds(s.first),CMTimeGetSeconds(s.end),CMTimeGetSeconds(CMTimeSubtract(s.end,s.first)),s.gops,(unsigned long long)s.evicted,s.bytes); fflush(stdout);
    check(CMTimeCompare(s.first,CMTimeMake(58,1))==0 && CMTimeCompare(s.end,CMTimeMake(90,1))==0 && s.gops==16,"90 seconds keeps last 30 plus preceding GOP");
    accounting(ring);
}
struct Decoded { unsigned frames=0, bad=0; };
static void decoded(void *context,void*,OSStatus status,VTDecodeInfoFlags,CVImageBufferRef image,CMTime,CMTime) {
    auto &d=*(Decoded*)context;
    if(status || !image) { ++d.bad; return; }
    ++d.frames;
    CVPixelBufferLockBaseAddress(image,kCVPixelBufferLock_ReadOnly);
    auto pixel=(uint8_t*)CVPixelBufferGetBaseAddress(image);
    if(!pixel || abs((int)pixel[0]-32)>10 || abs((int)pixel[2]-192)>10) ++d.bad;
    CVPixelBufferUnlockBaseAddress(image,kCVPixelBufferLock_ReadOnly);
}
static void decode(Fixture& f) {
    replay::Ring ring; ring.arm(); stream(ring,f);
    auto packets=ring.inspect();
    auto first=std::find_if(packets.begin(),packets.end(),[](auto &p){return p->track==clip::Track::video;});
    check(first!=packets.end(),"retained video exists");
    Decoded result;
    VTDecompressionOutputCallbackRecord callback={decoded,&result};
    VTDecompressionSessionRef decoder=nullptr;
    NSDictionary *attrs=@{(id)kCVPixelBufferPixelFormatTypeKey:@(kCVPixelFormatType_32BGRA)};
    check(!VTDecompressionSessionCreate(nullptr,(*first)->format(),nullptr,(__bridge CFDictionaryRef)attrs,&callback,&decoder),"independent retained decoder");
    unsigned submitted=0, errors=0;
    for(auto i=first;i!=packets.end() && (*i)->track==clip::Track::video;++i) {
        if(i!=first && key(*i)) break;
        if(VTDecompressionSessionDecodeFrame(decoder,(*i)->sample(),0,nullptr,nullptr)) ++errors;
        ++submitted;
    }
    VTDecompressionSessionFinishDelayedFrames(decoder); VTDecompressionSessionWaitForAsynchronousFrames(decoder);
    VTDecompressionSessionInvalidate(decoder); CFRelease(decoder);
    printf("decode: first_sync=%d submitted=%u decoded=%u bad=%u errors=%u expected=20\n",key(*first),submitted,result.frames,result.bad,errors); fflush(stdout);
    check(key(*first) && submitted==20 && result.frames==20 && !result.bad && !errors,"first retained GOP independently decodes from a keyframe");
}
static void preroll(Fixture& f) {
    replay::Ring ring; ring.arm(); stream(ring,f); auto s=ring.stats();
    auto packets=ring.inspect();
    auto first=std::find_if(packets.begin(),packets.end(),[](auto &p){return p->track==clip::Track::audio;});
    check(first!=packets.end(),"AAC retained");
    CMTime pts=CMSampleBufferGetPresentationTimeStamp((*first)->sample());
    CMTime cut=CMTimeSubtract(s.first,CMTimeMake(2112,48000));
    printf("AAC preroll: key=%.6f first=%.6f required_cut=%.6f\n",CMTimeGetSeconds(s.first),CMTimeGetSeconds(pts),CMTimeGetSeconds(cut)); fflush(stdout);
    check(CMTimeCompare(pts,cut)<=0 && CMTimeCompare(CMTimeAdd(pts,CMSampleBufferGetDuration((*first)->sample())),cut)>0,"AAC includes decoder preroll and crossing packet");
    accounting(ring);
}
static void missing(Fixture& f) {
    replay::Ring ring; ring.arm();
    for(int i=0;i<100;++i) ring.consume(retime(f.video[1],i));
    auto s=ring.stats();
    printf("missing key: packets=%zu rejected=%llu expected=100\n",s.packets,(unsigned long long)s.rejected); fflush(stdout);
    check(s.packets==0 && s.bytes==0 && s.rejected==100,"non-keyframes cannot start retention");
    ring.consume(retime(f.video[0],100));
    check(ring.stats().gops==1 && ring.stats().packets==1,"next keyframe recovers");
}
static void cap(Fixture& f) {
    replay::Ring ring; ring.arm(); size_t peak=0;
    for(int i=0;i<180;++i) {
        ring.consume(makePacket(f.video[0]->format(),clip::Track::video,CMTimeMake(i,2),CMTimeMake(1,2),8*1024*1024,i%4==0));
        peak=std::max(peak,ring.stats().bytes);
    }
    auto s=ring.stats();
    printf("cap: peak=%zu bytes=%zu hard=%zu reserved=%zu gops=%zu evicted=%llu\n",peak,s.bytes,replay::hardCap,replay::transferCap,s.gops,(unsigned long long)s.evicted); fflush(stdout);
    check(peak+replay::transferCap<=replay::hardCap,"oversized bitrate remains within 256 MiB");
    auto packets=ring.inspect();
    check(!packets.empty() && key(packets.front()),"cap eviction preserves complete GOP start");
    check(packets.size()%4==0 && s.gops*4==packets.size(),"only whole GOPs evicted");
    accounting(ring);
    // One GOP larger than the budget is discarded and deltas await a new IDR.
    replay::Ring longGOP; longGOP.arm();
    for(int i=0;i<40;++i) longGOP.consume(makePacket(f.video[0]->format(),clip::Track::video,CMTimeMake(i,2),CMTimeMake(1,2),8*1024*1024,i==0));
    printf("oversized GOP: bytes=%zu rejected=%llu\n",longGOP.stats().bytes,(unsigned long long)longGOP.stats().rejected);
    check(longGOP.stats().bytes==0 && longGOP.stats().rejected==10,"oversized GOP discarded without retaining undecodable tail");
}
static void generation(Fixture& f) {
    replay::Ring ring; ring.arm(); Audio audio;
    auto old=retime(f.video[0],0); std::weak_ptr<const clip::Packet> weak=old;
    ring.consume(old); ring.consume(audio.packet(0)); old.reset();
    Fixture changed(320);
    ring.consume(retime(changed.video[0],20));
    auto s=ring.stats();
    printf("video generation: generation=%llu packets=%zu old_released=%d first=%.3f\n",(unsigned long long)s.generation,s.packets,weak.expired(),CMTimeGetSeconds(s.first)); fflush(stdout);
    check(s.generation==2 && s.packets==1 && weak.expired(),"resolution change drops old video and AAC generation");
    for(auto &p:ring.inspect()) check(CMVideoFormatDescriptionGetDimensions(p->format()).width==320,"all retained video uses new dimensions");
    Audio mono(1); ring.consume(mono.packet(100));
    check(ring.stats().generation==3 && ring.stats().gops==0,"AAC codec description change clears video too");
    ring.consume(retime(changed.video[1],21));
    check(ring.stats().gops==0,"new generation waits for video IDR");
    ring.consume(retime(changed.video[0],40));
    check(ring.stats().gops==1,"new generation accepts fresh IDR");
    CMVideoFormatDescriptionRef codec=nullptr;
    check(!CMVideoFormatDescriptionCreate(nullptr,kCMVideoCodecType_HEVC,320,96,nullptr,&codec),"codec boundary fixture");
    ring.consume(makePacket(codec,clip::Track::video,CMTimeMake(6,1),CMTimeMake(1,10),64)); CFRelease(codec);
    check(ring.stats().generation==4 && ring.stats().gops==1 && ring.stats().packets==1,"codec change drops preceding generation");
    accounting(ring);
}
static void off(Fixture& f) {
    auto p=f.video[0];
    size_t before=allocations.load();
    replay::Ring ring;
    for(unsigned i=0;i<10000;++i) ring.consume(p);
    size_t added=allocations.load()-before; auto s=ring.stats();
    printf("Off: allocations=%zu armed=%d packets=%zu calls=10000\n",added,s.armed,s.packets); fflush(stdout);
    check(!added && !s.armed && !s.bytes && !s.packets,"initial Off consumes nothing and allocates nothing");
    ring.arm(); ring.consume(p); check(ring.stats().packets==1,"explicit arm");
    ring.disarm(); check(ring.stats().bytes==0 && !ring.stats().armed,"explicit disarm clears payload");
    before=allocations.load(); ring.consume(p); added=allocations.load()-before;
    check(!added && !ring.stats().armed,"disarm stays Off");
    ring.arm(); check(ring.stats().packets==0,"rearm starts empty");
    for(const char *value:{"off","invalid"}) {
        setenv("NFS_REPLAY",value,1); clip::Fanout outputs;
        before=allocations.load(); replay::attach(outputs); added=allocations.load()-before;
        check(!added && !outputs.healthy(),"Off attach creates no sink");
    }
    unsetenv("NFS_REPLAY"); clip::Fanout outputs;
    before=allocations.load(); replay::attach(outputs); added=allocations.load()-before;
    check(!added && !outputs.healthy(),"default attaches no sink");
    setenv("NFS_REPLAY","on",1); auto attached=replay::attach(outputs);
    check(outputs.healthy() && attached,"on attaches a controllable ring");
    outputs.publish(p); outputs.close();
    auto until=std::chrono::steady_clock::now()+std::chrono::seconds(5);
    while(!outputs.drained() && std::chrono::steady_clock::now()<until) std::this_thread::yield();
    auto retained=attached->stats();
    printf("attached sink: armed=%d packets=%zu expected=1 bytes=%zu\n",retained.armed,retained.packets,retained.bytes); fflush(stdout);
    check(outputs.drained() && retained.armed && retained.packets==1 && retained.bytes==p->bytes,"runtime sink explicitly arms and retains published packets");
    attached->disarm(); check(attached->stats().bytes==0,"attached ring can be explicitly disarmed");
    outputs.cancel(); unsetenv("NFS_REPLAY");
    clip::Fanout pending;
    auto disarmed=std::make_shared<replay::Ring>();
    replay::attach(pending,disarmed);
    printf("provided ring after delayed attachment: armed=%d expected=0\n",disarmed->stats().armed); fflush(stdout);
    check(!disarmed->stats().armed,"delayed sink attachment preserves explicit Disarm");
    pending.cancel();
}
static std::vector<uint8_t> output(Fixture& f,const char *setting) {
    std::vector<uint8_t> bytes;
    clip::Fanout outputs;
    outputs.add(std::make_shared<clip::Sink>([&](const clip::OwnedPacket& p) {
        size_t offset=bytes.size(); bytes.resize(offset+p->bytes);
        check(!CMBlockBufferCopyDataBytes(CMSampleBufferGetDataBuffer(p->sample()),0,p->bytes,bytes.data()+offset),"file sink payload copy");
        return clip::Result::consumed;
    }));
    if(setting) setenv("NFS_REPLAY",setting,1); else unsetenv("NFS_REPLAY");
    replay::attach(outputs);
    for(int i=0;i<100;++i) outputs.publish(retime(f.video[i%20],i));
    outputs.close();
    auto until=std::chrono::steady_clock::now()+std::chrono::seconds(5);
    while(!outputs.drained() && std::chrono::steady_clock::now()<until) std::this_thread::yield();
    check(outputs.drained() && outputs.healthy(),"independent file/replay sinks drained");
    return bytes;
}
static void identity(Fixture& f) {
    auto block=CMSampleBufferGetDataBuffer(f.video[0]->sample());
    std::vector<uint8_t> before(f.video[0]->bytes),after(before.size());
    CMBlockBufferCopyDataBytes(block,0,before.size(),before.data());
    replay::Ring ring; ring.arm(); ring.consume(f.video[0]);
    CMBlockBufferCopyDataBytes(block,0,after.size(),after.data());
    printf("immutable payload: bytes=%zu equal=%d\n",before.size(),before==after); fflush(stdout);
    check(before==after,"replay must never change shared compressed bytes");
    auto absent=output(f,nullptr),disabled=output(f,"off"),enabled=output(f,"on");
    printf("packet identity: unset=%zu off=%zu on=%zu equal=%d\n",absent.size(),disabled.size(),enabled.size(),absent==disabled && absent==enabled); fflush(stdout);
    check(!absent.empty() && absent==disabled && absent==enabled,"file sink output remains byte-identical");
    unsetenv("NFS_REPLAY");
}
int main(int argc,char **argv) { @autoreleasepool {
    check(argc==2,"one proof case"); Fixture fixture;
    if(!strcmp(argv[1],"span")) span(fixture);
    else if(!strcmp(argv[1],"decode")) decode(fixture);
    else if(!strcmp(argv[1],"preroll")) preroll(fixture);
    else if(!strcmp(argv[1],"missing")) missing(fixture);
    else if(!strcmp(argv[1],"cap")) cap(fixture);
    else if(!strcmp(argv[1],"generation")) generation(fixture);
    else if(!strcmp(argv[1],"off")) off(fixture);
    else if(!strcmp(argv[1],"identity")) identity(fixture);
    else check(false,"known case");
    puts("replay PASS"); return 0;
} }
