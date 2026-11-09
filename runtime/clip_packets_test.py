#!/usr/bin/env python3
"""Copy-only callback and independent owned-packet sinks, using production code.
The callback budget is relative to memcpy on the same thread CPU clock, so
scheduler preemption cannot make a slow fold pass or a healthy copy fail.
Measured 8192-frame callback: 2,229 ns against an 8,477 ns memcpy budget;
fold-in-callback mutant: 12,673 ns against 9,260 ns (exit 1).
Five-second stall: healthy 300/300 (exit 0), shared-queue mutant 0/300 (exit 1).
AAC contract: 12 owned packets, 2112 trimmed frames; borrowed-input mutant
unchanged=0 vs required=1 before its next callback (exit 1). Payload is synthetic.
Track readiness: audio/video 1/1 each (exit 0); head-only mutant 0/0 (exit 1).
Track mutant previously exceeded its 30 s watchdog when 10000 one-ms sleeps
were coalesced; a ten-second wall deadline preserves the same 0/0 rejection.
The shared-mutant's healthy-count wait uses the same ten-second steady-clock
deadline. Both wait on the original predicates; counts and the 30 s watchdog
are unchanged, and each mutant must fail its exact packet-readiness assertion.
Presentation pause 1.5 s: original retained 48000/72000, lost 24000;
fixed retained 72000/72000, lost 0 (half-ring mutant exits 1).
Compressed ownership: control sample copies/format retains 12/12 each;
sample-reference-drop 0/12 copies, format-reference-drop 0/12 retains.
Both drop mutants exit 1 at the lifetime check before dereferencing freed data.
"""
from pathlib import Path
import subprocess
import tempfile
import hashlib

RT=Path(__file__).resolve().parent
ROOT=RT.parent
CACHE=ROOT/'.metal-cache'/'clip-packets'
CACHE.mkdir(parents=True,exist_ok=True)
HARNESS=r'''
#define CLIPREC_TESTING 1
#include "@SOURCE@"
#include <cstdio>
#include <ctime>
#include <thread>
#include <chrono>
#include <vector>
extern "C" int settings_get(const char*) { return -1; }
extern "C" void portmenu_open_folder(const char*) {}
extern "C" void portmenu_status(const char*,...) {}
extern "C" void report_note(const char*,...) {}
extern "C" DsResult ds_capture_start(DsCaptureFn,void*) { return DS_OK; }
extern "C" void ds_capture_stop() {}
static void check(bool ok,const char *label) { if(!ok) { fprintf(stderr,"FAIL packets: %s\n",label); exit(1); } }
static uint64_t cpu() { struct timespec t; clock_gettime(CLOCK_THREAD_CPUTIME_ID,&t); return t.tv_sec*1000000000ull+t.tv_nsec; }
struct ClipAudioProof {
    static void pause() {
        ClipAudio a;
        std::vector<float> pcm(1000*6,0.1f);
        uint64_t stamp=mach_absolute_time();
        for(unsigned i=0;i<72;++i) {
            DsTimedMixBlock block={pcm.data(),1000,48000,0,1,1,i*1000ull,
                stamp+(uint64_t)llround(i*1000/48000.0/a.tickSeconds)};
            ClipAudio::receive(&a,&block);
            check(a.pump(stamp*a.tickSeconds,0,false),"paused presentation stages PCM");
        }
        uint64_t lost=a.read.load();
        printf("presentation pause: seconds=1.5 input=72000 retained=%llu lost=%llu overflow=%llu\n",
            (unsigned long long)(a.write.load()-lost),(unsigned long long)lost,(unsigned long long)a.overflowFrames.load()); fflush(stdout);
        check(lost==0 && a.write==72000 && a.overflowFrames==0,"1.5 second pause loses no staged frames");
    }
    static void run() {
        ClipAudio a;
        std::vector<float> pcm(8192*6);
        for(size_t i=0;i<pcm.size();++i) pcm[i]=(int)(i%113)/113.0f;
        auto original=pcm;
        DsTimedMixBlock block={pcm.data(),8192,48000,0,1,1,0,mach_absolute_time()};
        std::vector<uint64_t> copyTimes,callbackTimes;
        for(unsigned trial=0;trial<31;++trial) {
            uint64_t start=cpu();
            for(unsigned j=0;j<128;++j) {
                memcpy(a.raw.get(),pcm.data(),pcm.size()*sizeof(float));
                asm volatile("" : : "r"(a.raw.get()) : "memory");
            }
            copyTimes.push_back((cpu()-start)/128);
            start=cpu();
            for(unsigned j=0;j<128;++j) {
                ClipAudio::receive(&a,&block);
                a.rawRead.store(a.rawWrite.load()); a.blockRead.store(a.blockWrite.load()); a.readNS.store(a.writeNS);
            }
            callbackTimes.push_back((cpu()-start)/128);
        }
        std::sort(copyTimes.begin(),copyTimes.end()); std::sort(callbackTimes.begin(),callbackTimes.end());
        uint64_t copy=copyTimes[15],callback=callbackTimes[15],bound=copy*3+2000;
        printf("callback: memcpy_ns=%llu callback_ns=%llu bound_ns=%llu frames=8192\n",(unsigned long long)copy,(unsigned long long)callback,(unsigned long long)bound); fflush(stdout);
        check(callback<=bound,"callback time exceeds copy-only budget");
        check(pcm==original,"borrowed mixer data unchanged");
        ClipAudio b;
        block.frames=256; block.sample_rate=44100;
        ClipAudio::receive(&b,&block); b.stage();
        check(b.segment==1 && b.rate==44100,"first stream/rate boundary staged on thread");
        float stereo[2]; media::fold_to_stereo(pcm.data(),stereo,1);
        check(b.points[0].l==stereo[0] && b.points[0].r==stereo[1],"thread folds original six channels");
        block.clock_generation=2; block.sample_position=0; block.first_sample_host_time+=100000;
        ClipAudio::receive(&b,&block); b.stage();
        check(b.segment==2 && b.latestStart==256,"generation supersedes old points");
        block.stream_id=2; block.sample_rate=48000;
        ClipAudio::receive(&b,&block); b.stage();
        check(b.segment==3 && b.rate==48000,"replacement device boundary preserved");
        ClipAudio full;
        block.frames=1000; block.sample_rate=1000;
        ClipAudio::receive(&full,&block); ClipAudio::receive(&full,&block); ClipAudio::receive(&full,&block);
        printf("staging: queued=%llu duration_ns=%llu overflow_frames=%llu limit_ns=2000000000\n",(unsigned long long)full.maximumQueued.load(),(unsigned long long)full.writeNS,(unsigned long long)full.overflowFrames.load());
        check(full.maximumQueued==2000 && full.writeNS==2000000000ull && full.overflowFrames==1000,"two-second queue rejects and counts overflow without waiting");
    }
};
static clip::OwnedPacket packet(unsigned i) {
    AudioStreamBasicDescription asbd={}; asbd.mSampleRate=48000; asbd.mFormatID=kAudioFormatLinearPCM;
    asbd.mFormatFlags=kAudioFormatFlagIsFloat|kAudioFormatFlagIsPacked; asbd.mChannelsPerFrame=2;
    asbd.mBytesPerFrame=asbd.mBytesPerPacket=8; asbd.mFramesPerPacket=1; asbd.mBitsPerChannel=32;
    CMAudioFormatDescriptionRef format=nullptr; CMBlockBufferRef block=nullptr; CMSampleBufferRef sample=nullptr;
    check(!CMAudioFormatDescriptionCreate(nullptr,&asbd,0,nullptr,0,nullptr,nullptr,&format),"fixture format");
    check(!CMBlockBufferCreateWithMemoryBlock(nullptr,nullptr,8,nullptr,nullptr,0,8,0,&block),"fixture bytes");
    float values[2]={(float)i,-(float)i}; CMBlockBufferReplaceDataBytes(values,block,0,8);
    CMSampleTimingInfo time={CMTimeMake(1,48000),CMTimeMake(i,60),kCMTimeInvalid}; size_t bytes=8;
    check(!CMSampleBufferCreateReady(nullptr,block,format,1,1,&time,1,&bytes,&sample),"fixture sample");
    auto owned=std::make_shared<clip::Packet>(i%2?clip::Track::audio:clip::Track::video,sample);
    CFRelease(sample); CFRelease(block); CFRelease(format); return owned;
}
static void sinks() {
    std::atomic<bool> blocked{true},entered{false}; std::atomic<unsigned> healthyCount{0},slowCount{0};
    std::atomic<bool> order{true}; std::vector<clip::OwnedPacket> retained;
    auto slow=std::make_shared<clip::Sink>([&](const clip::OwnedPacket&) {
        // Model an actual blocking I/O call, outside every publisher lock.
        if(!entered.exchange(true)) std::this_thread::sleep_for(std::chrono::seconds(5));
        if(blocked) return clip::Result::retry; ++slowCount; return clip::Result::consumed;
    },512,4096);
    auto healthy=std::make_shared<clip::Sink>([&](const clip::OwnedPacket& p) {
        unsigned i=healthyCount.fetch_add(1); float values[2]={};
        CMBlockBufferCopyDataBytes(CMSampleBufferGetDataBuffer(p->sample()),0,8,values);
        if(values[0]!=i || values[1]!=-(float)i || !p->format()) order=false;
        retained.push_back(p); return clip::Result::consumed;
    },512,4096);
    clip::Fanout out; out.add(slow); out.add(healthy);
    auto start=std::chrono::steady_clock::now();
    for(unsigned i=0;i<300;++i) {
        out.publish(packet(i));
        std::this_thread::sleep_until(start+std::chrono::microseconds((i+1)*16667));
    }
    auto end=std::chrono::steady_clock::now();
    const auto healthyDeadline=end+std::chrono::seconds(10);
    while(healthyCount!=300 && std::chrono::steady_clock::now()<healthyDeadline)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    unsigned before=healthyCount;
    printf("sink stall: seconds=%.3f healthy=%u/300 slow=%u missing=%u\n",std::chrono::duration<double>(end-start).count(),before,slowCount.load(),300-before); fflush(stdout);
    check(before==300 && slowCount==0 && order,"stalled queue propagates into healthy sink");
    blocked=false; out.close();
    for(unsigned i=0;i<10000 && !out.drained();++i) std::this_thread::sleep_for(std::chrono::milliseconds(1));
    check(out.drained() && slowCount==300 && healthyCount==300 && order,"both queues drain without packet loss");
    check(slow->peakBytes()==2400 && healthy->peakBytes()<=4096,"per-sink byte bounds");
    check(retained.size()==300 && CMSampleBufferDataIsReady(retained.back()->sample()),"owned packet lifetime");
    auto failed=std::make_shared<clip::Sink>([](const clip::OwnedPacket&){ return clip::Result::retry; },2,16);
    clip::Fanout isolated; isolated.add(failed);
    std::atomic<unsigned> survivors{0};
    auto survivor=std::make_shared<clip::Sink>([&](const clip::OwnedPacket&){ ++survivors; return clip::Result::consumed; },512,4096);
    isolated.add(survivor);
    for(unsigned i=0;i<300;++i) isolated.publish(packet(i));
    isolated.close();
    for(unsigned i=0;i<10000 && !isolated.drained();++i) std::this_thread::sleep_for(std::chrono::milliseconds(1));
    printf("sink overflow: failed=%d survivor=%u/300 peak_bytes=%zu/16\n",failed->failed(),survivors.load(),failed->peakBytes());
    check(failed->failed() && survivors==300 && !survivor->failed(),"queue overflow fails only its own sink");
}
static void tracks() {
    std::atomic<unsigned> audio{0},video{0};
    auto sink=std::make_shared<clip::Sink>([&](const clip::OwnedPacket& p) {
        if(p->track==clip::Track::video) { if(!audio) return clip::Result::retry; ++video; }
        else ++audio;
        return clip::Result::consumed;
    });
    sink->push(packet(0)); sink->push(packet(1)); sink->close();
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(10);
    while(!sink->drained() && std::chrono::steady_clock::now()<deadline)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    printf("track readiness: audio=%u/1 video=%u/1\n",audio.load(),video.load()); fflush(stdout);
    check(audio==1 && video==1,"blocked video prevented ready audio from draining");
}
int main(int argc,char**) { @autoreleasepool {
    if(argc==1) ClipAudioProof::run(); else if(argc==2) sinks(); else if(argc==3) tracks(); else ClipAudioProof::pause();
    puts("clip_packets PASS"); return 0;
} }
'''

# A deterministic converter contract fixture tests input-buffer lifetime and AAC
# packet metadata even when the host codec factory is denied. Its payload is
# synthetic: this never counts as an AAC encode/decode or listening pass.
AAC_HARNESS=r''' 
#import <AudioToolbox/AudioToolbox.h>
#include "clip_packets.h"
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <vector>
static void check(bool ok,const char *s) { if(!ok) { fprintf(stderr,"FAIL AAC contract: %s\n",s); exit(1); } }
static unsigned sampleCopies=0,formatRetains=0;
extern "C" OSStatus fixtureSampleCopy(CFAllocatorRef allocator,CMSampleBufferRef sample,CMSampleBufferRef *copy) {
    OSStatus status=CMSampleBufferCreateCopy(allocator,sample,copy);
    if(!status && *copy) ++sampleCopies;
    return status;
}
extern "C" CFTypeRef fixtureRetain(CFTypeRef value) {
    if(value && CFGetTypeID(value)==CMFormatDescriptionGetTypeID()) ++formatRetains;
    return CFRetain(value);
}
struct FakeConverter {
    AudioStreamBasicDescription format;
    UInt32 bitrate=0;
    const float *held=nullptr;
    std::vector<float> snapshot;
    unsigned packets=0;
};
extern "C" OSStatus fixtureFormat(AudioFormatPropertyID,UInt32,const void*,UInt32*,void*) { return 0; }
extern "C" OSStatus fixtureNew(const AudioStreamBasicDescription *in,const AudioStreamBasicDescription *out,AudioConverterRef *result) {
    check(in->mFormatID==kAudioFormatLinearPCM && in->mSampleRate==48000 && in->mChannelsPerFrame==2,"stereo PCM input");
    check(out->mFormatID==kAudioFormatMPEG4AAC && out->mFormatFlags==kMPEG4Object_AAC_LC,"AAC-LC requested");
    auto c=new FakeConverter; c->format=*out; *result=(AudioConverterRef)c; return 0;
}
extern "C" OSStatus fixtureSet(AudioConverterRef c,AudioConverterPropertyID key,UInt32,const void *data) {
    check(key==kAudioConverterEncodeBitRate,"only codec bitrate property");
    ((FakeConverter*)c)->bitrate=*(const UInt32*)data; check(((FakeConverter*)c)->bitrate==192000,"192 kb/s configured"); return 0;
}
extern "C" OSStatus fixtureInfo(AudioConverterRef,AudioConverterPropertyID key,UInt32 *size,Boolean*) {
    check(key==kAudioConverterCompressionMagicCookie,"cookie property"); *size=2; return 0;
}
extern "C" OSStatus fixtureGet(AudioConverterRef converter,AudioConverterPropertyID key,UInt32 *size,void *data) {
    auto c=(FakeConverter*)converter;
    if(key==kAudioConverterEncodeBitRate) { *(UInt32*)data=c->bitrate; *size=4; }
    else if(key==kAudioConverterCurrentOutputStreamDescription) { *(AudioStreamBasicDescription*)data=c->format; *size=sizeof(c->format); }
    else if(key==kAudioConverterPrimeInfo) { *(AudioConverterPrimeInfo*)data={2112,0}; *size=sizeof(AudioConverterPrimeInfo); }
    else if(key==kAudioConverterPropertyMaximumOutputPacketSize) { *(UInt32*)data=16; *size=4; }
    else if(key==kAudioConverterCompressionMagicCookie) { ((uint8_t*)data)[0]=0x11; ((uint8_t*)data)[1]=0x90; *size=2; }
    else check(false,"unknown codec property");
    return 0;
}
extern "C" OSStatus fixtureFill(AudioConverterRef converter,AudioConverterComplexInputDataProc supply,void *context,UInt32 *packets,AudioBufferList *output,AudioStreamPacketDescription *desc) {
    auto c=(FakeConverter*)converter;
    if(c->held) {
        bool unchanged=!memcmp(c->held,c->snapshot.data(),c->snapshot.size()*sizeof(float));
        if(!unchanged) { puts("AAC input lifetime: unchanged=0 required=1"); fflush(stdout); }
        check(unchanged,"supplied PCM changed before next input callback");
    }
    UInt32 count=1024; AudioBufferList input={}; input.mNumberBuffers=1;
    OSStatus status=supply(converter,&count,&input,nullptr,context);
    check(!status || !count,"starvation returns zero input packets");
    if(status || !count) { *packets=0; c->held=nullptr; return status; }
    check(input.mBuffers[0].mNumberChannels==2 && input.mBuffers[0].mDataByteSize==count*8,"input layout");
    c->held=(const float*)input.mBuffers[0].mData;
    c->snapshot.assign(c->held,c->held+count*2);
    unsigned char bytes[16]={}; memcpy(bytes,&c->packets,sizeof(c->packets));
    memcpy(bytes+4,c->held,8); ++c->packets;
    memcpy(output->mBuffers[0].mData,bytes,sizeof(bytes)); output->mBuffers[0].mDataByteSize=16;
    *desc={0,0,16}; *packets=1; return 0;
}
extern "C" OSStatus fixtureDispose(AudioConverterRef c) { delete (FakeConverter*)c; return 0; }
int main() { @autoreleasepool {
    std::vector<clip::OwnedPacket> packets;
    {
        clip::AACEncoder encoder;
        check(!encoder.status,"encoder initializes");
        const auto *format=CMAudioFormatDescriptionGetStreamBasicDescription(encoder.format());
        size_t cookie=0; auto data=(const uint8_t*)CMAudioFormatDescriptionGetMagicCookie(encoder.format(),&cookie);
        check(format && format->mFormatID==kAudioFormatMPEG4AAC && format->mSampleRate==48000 && format->mChannelsPerFrame==2,"owned AAC description");
        check(cookie==2 && data[0]==0x11 && data[1]==0x90,"48 kHz stereo ASC retained");
        for(unsigned block=0;block<12;++block) {
            std::vector<float> pcm(1000*2);
            for(size_t i=0;i<pcm.size();++i) pcm[i]=(float)(block*2000+i)/100000;
            check(encoder.encode(pcm.data(),1000,false,[&](clip::OwnedPacket p){ packets.push_back(p); }),"stream input");
        }
        check(encoder.encode(nullptr,0,true,[&](clip::OwnedPacket p){ packets.push_back(p); }),"final EOS flush");
    }
    printf("compressed references: sample_copies=%u/12 format_retains=%u/12\n",sampleCopies,formatRetains); fflush(stdout);
    check(sampleCopies==12 && formatRetains==12,"compressed sample and format references survive their producer");
    check(packets.size()==12,"all packets owned after converter disposal");
    unsigned trims=0;
    for(size_t i=0;i<packets.size();++i) {
        auto &p=packets[i]; uint32_t identity=999;
        CMBlockBufferCopyDataBytes(CMSampleBufferGetDataBuffer(p->sample()),0,4,&identity);
        check(identity==i && p->bytes==16 && p->track==clip::Track::audio,"immutable copied payload");
        check(CMTimeCompare(CMSampleBufferGetPresentationTimeStamp(p->sample()),CMTimeMake((int64_t)i*1024-2112,48000))==0,"priming-adjusted PTS");
        auto trim=(CFDictionaryRef)CMGetAttachment(p->sample(),kCMSampleBufferAttachmentKey_TrimDurationAtStart,nullptr);
        if(trim) trims+=(unsigned)CMTimeConvertScale(CMTimeMakeFromDictionary(trim),48000,kCMTimeRoundingMethod_Default).value;
        check(CMAudioFormatDescriptionGetStreamBasicDescription(p->format())->mSampleRate==48000,"format survives encoder destruction");
    }
    printf("AAC contract: rate=48000 stereo=2 bitrate=192000 LC=1 cookie=1190 packets=%zu priming_trim=%u retained_input=1 (synthetic payload)\n",packets.size(),trims);
    check(trims==2112,"all priming trimmed exactly once");
    puts("AAC contract PASS"); return 0;
} }
'''

def build_contract(d,name,packets):
    fixture=d/(name+'-fixture.mm'); fixture.write_text(AAC_HARNESS)
    exe=d/name
    instrumented=d/(name+'-packets.mm')
    instrumented.write_text('''#include "clip_packets.h"
extern "C" OSStatus fixtureSampleCopy(CFAllocatorRef,CMSampleBufferRef,CMSampleBufferRef*);
extern "C" CFTypeRef fixtureRetain(CFTypeRef);
#define CMSampleBufferCreateCopy fixtureSampleCopy
#define CFRetain fixtureRetain
'''+packets.read_text())
    flags={'AudioFormatGetProperty':'fixtureFormat','AudioConverterNew':'fixtureNew',
           'AudioConverterSetProperty':'fixtureSet','AudioConverterGetPropertyInfo':'fixtureInfo',
           'AudioConverterGetProperty':'fixtureGet','AudioConverterFillComplexBuffer':'fixtureFill',
           'AudioConverterDispose':'fixtureDispose'}
    cmd=['clang++','-std=c++17','-O1','-Wall','-Wextra','-Werror','-fobjc-arc','-I'+str(RT),
         *['-D'+a+'='+b for a,b in flags.items()],str(fixture),str(instrumented),
         '-framework','Foundation','-framework','CoreMedia','-framework','AudioToolbox','-o',str(exe)]
    result=subprocess.run(cmd,capture_output=True,text=True)
    assert result.returncode==0,result.stderr[-4000:]
    return exe

def build(d,name,source,packets):
    fixture=d/(name+'.mm')
    fixture.write_text(HARNESS.replace('@SOURCE@',str(source)))
    exe=d/name
    cmd=['clang++','-std=c++17','-O2','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-fobjc-arc','-Wno-deprecated-declarations','-I'+str(RT),str(fixture),str(packets),str(RT/'replay.mm'),str(RT/'replay_export.mm'),str(RT/'live_encode.mm'),str(RT/'live_seg.mm'), str(RT/'livestream.mm'),str(ROOT/'media/media.cpp'),'-I'+str(ROOT/'third_party/ffmpeg/include')]
    for lib in ('avformat','avcodec','swscale','swresample','avutil'):
        cmd+=[str(ROOT/'third_party/ffmpeg/lib'/('lib'+lib+'.a'))]
    cmd+=['-lz']
    for framework in ('AudioToolbox','CoreAudio','CoreFoundation','Cocoa','Metal','QuartzCore','AVFoundation','VideoToolbox','CoreVideo','CoreMedia'):
        cmd+=['-framework',framework]
    p=subprocess.run(cmd+['-o',str(exe)],capture_output=True,text=True)
    assert p.returncode==0,p.stderr[-4000:]
    return exe

def run(exe,args,expected):
    p=subprocess.run([str(exe),*args],capture_output=True,text=True,timeout=30)
    output=p.stdout+p.stderr
    print(exe.name+': '+output.strip(),flush=True)
    assert p.returncode==expected,(p.returncode,output[-2000:])
    return output

with tempfile.TemporaryDirectory(dir=CACHE) as scratch:
    d=Path(scratch)
    source=(RT/'cliprec.mm').read_text()
    fixed=build(d,'fixed',RT/'cliprec.mm',RT/'clip_packets.mm')
    results=[run(fixed,[],0),run(fixed,['sinks'],0),run(fixed,['tracks','only'],0),run(fixed,['pause','only','now'],0)]
    # Pinned original half-ring trim, independent of revision history.
    old_trim='        uint64_t queued=write.load(std::memory_order_acquire);\n        if(queued-read.load(std::memory_order_relaxed)>capacity/2) read.store(queued-capacity/2,std::memory_order_release);\n'
    assert hashlib.sha256(old_trim.encode()).hexdigest()=='cdc92c766780badea3f0cd0d1925cf3ba7ea933d7e353c1506763b00bc9654be'
    paused=d/'half-ring.mm'
    needle='        int64_t target=(int64_t)llround(end*48000);'
    assert source.count(needle)==1
    paused.write_text(source.replace(needle,old_trim+needle))
    mutant=build(d,'half-ring-mutant',paused,RT/'clip_packets.mm')
    output=run(mutant,['pause','only','now'],1)
    assert 'retained=48000 lost=24000 overflow=0' in output
    results.append(output)
    callback=d/'fold-callback.mm'
    needle='        a.writeNS+=duration;'
    assert source.count(needle)==1
    callback.write_text(source.replace(needle,'''        for(size_t i=0;i<block->frames;++i) {
            float stereo[2]; media::fold_to_stereo(a.raw.get()+(w+i)%capacity*6,stereo,1);
            a.raw[(w+i)%capacity*6]=stereo[0]; a.raw[(w+i)%capacity*6+1]=stereo[1];
        }
'''+needle))
    mutant=build(d,'fold-mutant',callback,RT/'clip_packets.mm')
    results.append(run(mutant,[],1))
    contract=build_contract(d,'aac-contract',RT/'clip_packets.mm')
    results.append(run(contract,[],0))
    borrowed=d/'borrowed-input.mm'
    code=(RT/'clip_packets.mm').read_text()
    needle='data->mBuffers[0].mData=*n?a.supplied.data():nullptr;'
    assert code.count(needle)==1
    borrowed.write_text(code.replace(needle,'data->mBuffers[0].mData=*n?a.pending.data()+a.cursor:nullptr;'))
    contract=build_contract(d,'borrowed-mutant',borrowed)
    results.append(run(contract,[],1))
    # Missing ownership is rejected before dereferencing a freed CF object.
    for name,old,new in (
        ('sample-reference-drop','CMSampleBufferCreateCopy(kCFAllocatorDefault,s,&sample_);','sample_=s;'),
        ('format-reference-drop','if(format_) CFRetain(format_);','/* borrowed format */')):
        dropped=d/(name+'.mm')
        assert code.count(old)==1
        dropped.write_text(code.replace(old,new))
        contract=build_contract(d,name,dropped)
        output=run(contract,[],1)
        assert 'FAIL AAC contract: compressed sample and format references survive their producer' in output
        results.append(output)
    noBypass=d/'no-bypass.mm'
    code=(RT/'clip_packets.mm').read_text()
    needle='for(size_t i=0;i<queue.size();++i) if(queue[i]->track!=p->track) { next=i; break; }'
    assert code.count(needle)==1
    noBypass.write_text(code.replace(needle,'/* one blocked track holds the queue */'))
    blocked=build(d,'track-mutant',RT/'cliprec.mm',noBypass)
    output=run(blocked,['tracks','only'],1)
    assert 'track readiness: audio=0/1 video=0/1' in output
    assert 'FAIL packets: blocked video prevented ready audio from draining' in output
    results.append(output)
    # Two registrations alias the same Sink, so they share its blocked queue.
    sharedHeader=d/'clip_packets.h'
    header=(RT/'clip_packets.h').read_text()
    needle='sinks.push_back(s);'
    assert header.count(needle)==1
    sharedHeader.write_text(header.replace(needle,'sinks.push_back(sinks.empty()?s:sinks.front());'))
    # Route the segmenter's packet types through the same mutant header, so
    # pragma-once never sees two distinct definitions of the packet classes.
    (d/'live_seg.h').write_text((RT/'live_seg.h').read_text())
    (d/'livestream.h').write_text((RT/'livestream.h').read_text())
    shared=d/'shared.mm'; shared.write_text((RT/'clip_packets.mm').read_text())
    sharedSource=d/'shared-recorder.mm'; sharedSource.write_text(source)
    mutant=build(d,'shared-mutant',sharedSource,shared)
    output=run(mutant,['sinks'],1)
    assert 'healthy=0/300 slow=0 missing=300' in output
    assert 'FAIL packets: stalled queue propagates into healthy sink' in output
    results.append(output)
    (CACHE/'proof.log').write_text('\n'.join(results))
print('clip_packets_test PASS: copy-only callback, 5 s stall, owned lifetime, independent overflow; fold/shared-queue/input-lifetime/sample-reference/format-reference/track-readiness/half-ring mutants rejected')
