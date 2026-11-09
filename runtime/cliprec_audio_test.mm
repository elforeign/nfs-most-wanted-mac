// Load proof: old startup/clock exit 1, fixed AAC/PCM 0 (2.1 s / 20 ms delays); stalled file old 270 written at 5.571 s, fixed 0 at 5.604 s (1 s pause).
// Six-channel DirectSound PCM and synthetic drawables traverse Metal, VT,
// independent packet sinks and MP4; AVAssetReader verifies decoded content.
// Drain regression: original completed=0, Saving=1 at deadline+0.252 s;
// fixed completed=1, Saving=0 at +0.009 s, restart accepted=1 before I/O
// returns. Blocked file append: completed=1 at +0.004 s, then safe cleanup.
// Failed-file endpoint: original requested 1.000000 s for 10 frames;
// fixed requested/decoded 0.166667/0.166667 s. MOV already decoded to that
// length on the original; the requested endpoint check detects the bug too.
// Control: 600 video frames / 480000 stereo samples, markers/drift 0/0 ms,
// RMS 0.001459, exact endpoint. Removed drain: 46880/48000 samples,
// tail RMS 0.667342 vs 0.002014; real/removed drain pass/fail 5/5 each.
// File stalled >=5 s: healthy 360/360 video and all audio packets; 0 missing,
// decoded file offset/drift 0/0 ms. Failed file: healthy 60/60, file prefix 10.
// Callback fold mutant 12673 ns >9260 ns budget; shared queue 0/300 vs 300/300
// (clip_packets_test.py). Both broken runs exit 1; controls pass.
// Delayed file append: captured endpoint 48000 while one video packet waits.
// Startup: accepted/epoch 0/0, removed guard 1/1 (exit 1). Jitter +-5 ms:
// RMS 0.012352, markers 0 ms; 8 s pause: resumed RMS 0.001445, markers 0 ms.
// Live focus gap (12 s without frames, real ClipAudio, host AAC: the TS muxer takes AAC only): the file with live
// matches the run without live (864000 samples, 480128 zero samples) and live cuts 6 segments from 346 held frames;
// the old shared target (file sound to the held frames' end) gives 0 zero samples, live sound stopped at the video
// end gives 0 gap segments, and a leaked held surface 120 pool drops (capture overloaded). All three exit/compare fail.
// Actual AAC priming/padding and quality run separately when the codec factory
// is available. A PCM or synthetic converter contract pass never substitutes.
#define CLIPREC_AUDIO_TEST 1
#include "../host/audio/dsound_host.h"
#include <vector>
#include <cmath>
#include <algorithm>
#include <mach/mach_time.h>
#include <objc/runtime.h>
struct AudioBlock { DsTimedMixBlock meta; std::vector<float> pcm; };
static std::vector<AudioBlock> oracle;
static DsCaptureFn recorderReceive;
static void *recorderContext;
// Device sinks stamp "now + queued frames"; the jitter fixture models that by
// moving each block's stamp by a deterministic -5..+5 ms pattern while the
// oracle keeps the contiguous truth that the reference is built from.
static double jitterSeconds=0;
static void receiveOracle(void *,const DsTimedMixBlock *block) {
    oracle.push_back({*block,std::vector<float>(block->pcm51,block->pcm51+block->frames*6)});
    if(!jitterSeconds) { recorderReceive(recorderContext,block); return; }
    mach_timebase_info_data_t scale; mach_timebase_info(&scale);
    double step=jitterSeconds*((double)((oracle.size()*7)%11)-5)/5;
    DsTimedMixBlock jittered=*block;
    jittered.first_sample_host_time+=(int64_t)llround(step*1e9*scale.denom/scale.numer);
    recorderReceive(recorderContext,&jittered);
}
static DsResult fixtureCaptureStart(DsCaptureFn fn,void *context) {
    recorderReceive=fn; recorderContext=context;
    return ds_capture_start(receiveOracle,nullptr);
}
#define ds_capture_start fixtureCaptureStart
#if defined(CLIPREC_CASE) && (CLIPREC_CASE==8 || CLIPREC_CASE==9)
// Live without a network poster: the capture side sees a running stream; segments go nowhere.
extern "C" int fixtureLiveStatus(void) { return 1; }
#define livestream_status fixtureLiveStatus
#endif
#include <Foundation/Foundation.h>
static double fixtureStartupTime=-1;
static double fixtureUptime() { return fixtureStartupTime<0 ? NSProcessInfo.processInfo.systemUptime : fixtureStartupTime; }
#define main video_fixture_main
#include "cliprec_test.mm"
#undef main
#undef ds_capture_start
#undef livestream_status
// Include the actual mixer to exercise rate rebases without a physical device.
#include "../host/audio/dsound_host.cpp"
static double ticksToSeconds(uint64_t ticks) {
    mach_timebase_info_data_t scale; mach_timebase_info(&scale);
    return ticks*(double)scale.numer/scale.denom/1e9;
}
struct AudioFixture {
    DsDevice *d=nullptr; DsBuffer *b=nullptr;
    std::vector<float> source,output;
    size_t rendered=0;
    AudioFixture() : source(48000*12*6),output(256*6) {
        for(size_t i=0;i<source.size()/6;i++) {
            double t=(double)i/48000;
            // Channel-specific carriers plus periodic smooth asymmetric markers.
            double marker=exp(-pow(remainder(t-0.125,0.25)/0.018,2));
            for(unsigned c=0;c<6;c++)
                source[i*6+c]=(0.035+0.018*marker)*sin(2*M_PI*(233+137*c)*t)+(c==0?0.045*marker:0);
        }
        DsConfig cfg={}; cfg.sink=DS_SINK_MANUAL; cfg.mode=DS_OUT_PCM51; cfg.block_frames=256;
        require(ds_device_create(&cfg,&d)==DS_OK,"manual DirectSound device");
        uint8_t w[18]={}; w[0]=3; w[2]=6; w[12]=24; w[14]=32;
        for(int k=0;k<4;k++) { w[4+k]=48000u>>(8*k); w[8+k]=(48000u*24)>>(8*k); }
        require(ds_device_create_buffer(d,0,source.size()*sizeof(float),w,source.data(),&b)==DS_OK,"six-channel float buffer");
        require(ds_buffer_play(b,DSBPLAY_LOOPING)==DS_OK,"play generated PCM");
        if(!oracle.empty()) d->manual_host_time=oracle.back().meta.first_sample_host_time+frame_ticks(oracle.back().meta.frames,oracle.back().meta.sample_rate);
    }
    void render(size_t count=256) {
        while(count) {
            size_t n=std::min(count,(size_t)256);
            require(ds_device_render(d,output.data(),n)==DS_OK,"actual mixer render");
            count-=n; rendered+=n;
        }
    }
    void through(double time) {
        unsigned attempts=0;
        while(oracle.empty() || ticksToSeconds(oracle.back().meta.first_sample_host_time)+
                (double)oracle.back().meta.frames/oracle.back().meta.sample_rate<time) {
            if(++attempts>=4000) { fprintf(stderr,"oracle blocks=%zu last=%.9f want=%.9f\n",oracle.size(),oracle.empty()?0:ticksToSeconds(oracle.back().meta.first_sample_host_time),time); require(false,"bounded fixture audio clock advance"); } render();
        }
    }
    void rate(unsigned rate) { std::lock_guard<std::mutex> lock(d->m);
        auto next=d->manual_host_time+frame_ticks(d->written-d->manual_frame,d->cfg.mix_rate); rebase_rate(*d,rate); d->manual_host_time=next; }
    ~AudioFixture() { ds_buffer_release(b); ds_device_release(d); }
};
static std::vector<float> reference(double epoch,unsigned frames) {
    struct Expected { double time; double l,r; uint64_t segment; };
    std::vector<Expected> points;
    uint64_t stream=0,generation=0,segment=0;
    for(auto &block:oracle) {
      if(stream!=block.meta.stream_id || generation!=block.meta.clock_generation) {
          stream=block.meta.stream_id; generation=block.meta.clock_generation; ++segment;
      }
      for(size_t n=0;n<block.meta.frames;n++) {
        const float *v=&block.pcm[n*6];
        points.push_back({ticksToSeconds(block.meta.first_sample_host_time)+(double)n/block.meta.sample_rate,
            (v[0]+0.70710678*(v[2]+v[4]))/(1+2*0.70710678),
            (v[1]+0.70710678*(v[2]+v[5]))/(1+2*0.70710678),segment});
      }
    }
    std::vector<float> result(frames*2);
    size_t cursor=0;
    for(unsigned i=0;i<frames;i++) {
        double time=epoch+(double)i/48000;
        while(cursor+1<points.size() && points[cursor+1].time<=time) ++cursor;
        auto &a=points[cursor]; auto &b=points[std::min(cursor+1,points.size()-1)];
        if(time<a.time-1.0/48000 || time>b.time+1.0/48000 || b.time-a.time>0.02 || (a.segment!=b.segment && b.time-a.time>2.0/48000)) continue;
        double f=b.time>a.time?std::clamp((time-a.time)/(b.time-a.time),0.0,1.0):0;
        result[i*2]=a.l+(b.l-a.l)*f; result[i*2+1]=a.r+(b.r-a.r)*f;
    }
    return result;
}
struct DecodedAudio {
    std::vector<float> pcm;
    unsigned tracks=0,channels=0,rate=0;
    double end=0,first=1e9,duration=0;
};
static DecodedAudio decodeAudio(NSString *path) {
    DecodedAudio result;
    AVURLAsset *asset=[AVURLAsset URLAssetWithURL:[NSURL fileURLWithPath:path] options:nil];
    NSArray<AVAssetTrack *> *tracks=[asset tracksWithMediaType:AVMediaTypeAudio];
    result.tracks=(unsigned)tracks.count; if(!tracks.count) return result;
    AVAssetTrack *track=tracks.firstObject;
    auto desc=(__bridge CMAudioFormatDescriptionRef)track.formatDescriptions.firstObject;
    const AudioStreamBasicDescription *asbd=CMAudioFormatDescriptionGetStreamBasicDescription(desc);
    result.channels=asbd->mChannelsPerFrame; result.rate=(unsigned)asbd->mSampleRate;
    result.duration=CMTimeGetSeconds(asset.duration);
    AVAssetReader *reader=[[AVAssetReader alloc] initWithAsset:asset error:nil];
#ifdef CLIPREC_PCM_CONTROL
    NSDictionary *settings=nil; // Native float PCM passthrough needs no codec service.
#else
    NSDictionary *settings=@{AVFormatIDKey:@(kAudioFormatLinearPCM),AVLinearPCMIsFloatKey:@YES,
        AVLinearPCMBitDepthKey:@32,AVLinearPCMIsBigEndianKey:@NO,AVLinearPCMIsNonInterleaved:@NO};
#endif
    AVAssetReaderTrackOutput *out=[AVAssetReaderTrackOutput assetReaderTrackOutputWithTrack:track outputSettings:settings];
    [reader addOutput:out];
    if(![reader startReading]) fprintf(stderr,"audio decode error: %s\n",reader.error.description.UTF8String);
    require(reader.status==AVAssetReaderStatusReading,"independent audio decode starts");
    while(CMSampleBufferRef sample=[out copyNextSampleBuffer]) {
        double pts=CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sample));
        unsigned count=(unsigned)CMSampleBufferGetNumSamples(sample);
        if(!count) { CFRelease(sample); continue; }
        result.first=fmin(result.first,pts); result.end=fmax(result.end,pts+(double)count/result.rate);
        CMBlockBufferRef data=CMSampleBufferGetDataBuffer(sample);
        std::vector<float> decoded(count*result.channels);
        require(CMBlockBufferGetDataLength(data)==decoded.size()*sizeof(float),"decoded float layout");
        OSStatus copy=CMBlockBufferCopyDataBytes(data,0,decoded.size()*sizeof(float),decoded.data());
        if(copy) fprintf(stderr,"copy rc=%d frames=%u bytes=%zu ready=%u\n",(int)copy,count,decoded.size()*sizeof(float),(unsigned)CMSampleBufferDataIsReady(sample));
        require(!copy,"decoded audio data");
        int64_t start=llround(pts*48000);
        for(unsigned n=0;n<count;n++) if(start+n>=0) {
            size_t dst=(size_t)(start+n)*2;
            if(result.pcm.size()<dst+2) result.pcm.resize(dst+2);
            result.pcm[dst]=decoded[n*result.channels]; result.pcm[dst+1]=decoded[n*result.channels+1];
        }
        CFRelease(sample);
    }
    require(reader.status==AVAssetReaderStatusCompleted,"audio decode completed");
    return result;
}
static double rmsError(const std::vector<float> &decoded,const std::vector<float> &expected,unsigned start,unsigned count,int lag=0) {
    double e=0,p=0;
    for(unsigned i=start;i<start+count;i++) for(unsigned c=0;c<2;c++) {
        int64_t n=(int64_t)i+lag;
        double want=expected[i*2+c];
        double got=n>=0 && (size_t)n*2+c<decoded.size()?decoded[(size_t)n*2+c]:0;
        e+=(got-want)*(got-want); p+=want*want;
    }
    return sqrt(e/fmax(p,1e-20));
}
// Delay actual writer readiness so its own sink thread must retry.
#if defined(CLIPREC_CASE) && CLIPREC_CASE==3
static id delayedInput;
static std::atomic<bool> releaseInput{false};
static BOOL (*originalReady)(id,SEL);
static BOOL delayedReady(id input,SEL selector) {
    return input==delayedInput && !releaseInput ? NO : originalReady(input,selector);
}
#endif
static void audioProof(bool fallback,bool hdr,unsigned seconds,bool drops=false,bool transitions=false,bool jitter=false) {
    oracle.clear(); jitterSeconds=jitter?0.005:0; auto fixture=std::make_unique<AudioFixture>();
    fixtureStartupTime=NSProcessInfo.processInfo.systemUptime;
    ClipSession *s=start(fallback,W,H,hdr?linearPattern(0):nil,1,[&] {
        fixture->render(4096);
        require(oracle.size()==16,"PCM subscription starts before video preparation");
    });
    double epoch=ticksToSeconds(oracle.front().meta.first_sample_host_time)+0.2007;
    double firstAudio=ticksToSeconds(oracle.front().meta.first_sample_host_time);
    if(getenv("CLIPREC_FIXTURE_LOAD")) usleep(2100000);
    frame(s,0,firstAudio-0.05);
    fixtureStartupTime=-1;
    printf("startup: early_video_accepted=%u epoch_set=%u required=0/0\n",s->accepted.load(),s->epoch!=0); fflush(stdout);
    require(!s->epoch && s->accepted==0 && !s->stopping,"video waits for PCM spanning first presentation");
    unsigned total=seconds*60;
#if defined(CLIPREC_CASE) && CLIPREC_CASE==4
    std::atomic<unsigned> videoPackets{0},audioPackets{0};
    std::atomic<bool> packetFormatOK{true};
    std::vector<clip::OwnedPacket> retained;
    auto healthy=std::make_shared<clip::Sink>([&](const clip::OwnedPacket& p) {
        if(p->track==clip::Track::video) ++videoPackets;
        else {
            ++audioPackets;
            const auto *asbd=CMAudioFormatDescriptionGetStreamBasicDescription(p->format());
#ifdef CLIPREC_PCM_CONTROL
            if(!asbd || asbd->mFormatID!=kAudioFormatLinearPCM) packetFormatOK=false;
#else
            size_t cookie=0; CMAudioFormatDescriptionGetMagicCookie(p->format(),&cookie);
            if(!asbd || asbd->mFormatID!=kAudioFormatMPEG4AAC || !cookie) packetFormatOK=false;
#endif
            if(!asbd || asbd->mSampleRate!=48000 || asbd->mChannelsPerFrame!=2 || !p->bytes) packetFormatOK=false;
        }
        retained.push_back(p); return clip::Result::consumed;
    });
    dispatch_sync(s->work,^{ s->outputs.add(healthy); s->stallFile=true; });
    double stallStart=NSProcessInfo.processInfo.systemUptime;
    auto released=std::make_shared<std::atomic<uint64_t>>(0);
#endif
    for(unsigned i=0;i<total;i++) {
#if defined(CLIPREC_CASE) && CLIPREC_CASE==4
        while(NSProcessInfo.processInfo.systemUptime<stallStart+(double)i/60) usleep(500);
        if(i==270) {
            if(getenv("CLIPREC_FIXTURE_LOAD")) usleep(1000000); while(videoPackets<270 && !healthy->failed()) usleep(500);
            printf("stalled_file: elapsed=%.3f healthy_video=%u expected=270 file_video=%u\n",NSProcessInfo.processInfo.systemUptime-stallStart,videoPackets.load(),s->accepted.load()); fflush(stdout);
            require(videoPackets==270 && s->accepted==0,"five-second file stall does not delay healthy packets");
        }
        if(i==300) { released->store(llround((NSProcessInfo.processInfo.systemUptime-stallStart)*1000000)); s->stallFile=false; }
#endif
        if(s->stopping) { fprintf(stderr,"unexpected stop frame=%u reason=%s\n",i,s->error.UTF8String); require(false,"recording remains active"); }
        double time=epoch+(double)i/60;
        if(transitions) {
            if(getenv("CLIPREC_FIXTURE_LOAD")) usleep(20000);
            if(i==30) fixture->rate(44100);
            if(i==45) fixture=std::make_unique<AudioFixture>();
        }
        // On the work queue, latch every ordinary pump off before the last
        // frame advances videoEnd. Only stop's final=true pump may drain it.
        if(i+1==total) dispatch_sync(s->work,^{ s->audio->forcePressure=true; });
#if defined(CLIPREC_CASE) && CLIPREC_CASE==3
        if(i+1==total) {
            double deadline=NSProcessInfo.processInfo.systemUptime+10;
            while(!s->fileSink->drained() && NSProcessInfo.processInfo.systemUptime<deadline) usleep(500);
            require(s->fileSink->drained(),"file prefix drains before readiness injection");
        }
        if(i+1==total) dispatch_sync(s->work,^{
            delayedInput=s->file->input;
            Method ready=class_getInstanceMethod([delayedInput class],@selector(isReadyForMoreMediaData));
            originalReady=(BOOL (*)(id,SEL))method_getImplementation(ready);
            method_setImplementation(ready,(IMP)delayedReady);
        });
#endif
        // Deliberately leave the last audio block for stop's final drain.
        if(i+1<total) fixture->through(time+1.0/60-0.010);
        if(drops && i==total/2) {
            s->forceEncoderPressure=true;
            frame(s,i%N,time,true,false,true,hdr?linearPattern(i%N):nil);
            s->forceEncoderPressure=false;
        } else frame(s,i%N,time,true,i%2,true,hdr?linearPattern(i%N):nil);
    }
    fixture->through(epoch+seconds+0.003);
    bool inside=false;
    for(auto &block:oracle) {
        double begin=ticksToSeconds(block.meta.first_sample_host_time);
        inside|=begin<epoch+seconds && begin+(double)block.meta.frames/block.meta.sample_rate>epoch+seconds;
    }
    require(inside,"stop trims through the middle of a PCM block");
#if defined(CLIPREC_CASE) && CLIPREC_CASE==3
    dispatch_sync(s->work,^{
        printf("delayed_append: video_end_samples=%lld queued=%lu capture_required=48000\n",
            (long long)llround(s->videoEnd*48000),(unsigned long)s->fileSink->queued()); fflush(stdout);
        require(llround(s->videoEnd*48000)==48000 && s->fileSink->queued()>=1,"capture endpoint advances independently of file readiness");
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,30*NSEC_PER_MSEC),s->work,^{ releaseInput=true; });
    });
#endif
    // The encoder-to-capture-thread barrier determines the captured endpoint,
    // independently of any file input's readiness.
    double deadline=NSProcessInfo.processInfo.systemUptime+5;
    __block bool appended=false;
    do {
        dispatch_sync(s->work,^{ appended=llround(s->videoEnd*48000)==seconds*48000; });
        if(appended || s->stopping) break;
        usleep(1000);
    } while(NSProcessInfo.processInfo.systemUptime<deadline);
    require(appended,"bounded wait for last compressed video packet");
    dispatch_sync(s->work,^{
        int64_t endSamples=llround(s->videoEnd*48000);
        bool drained=s->audio->drained(s->videoEnd);
        printf("before_shutdown: video_end_samples=%lld expected=%u audio_drained=%u required=0\n",
            (long long)endSamples,seconds*48000,drained); fflush(stdout);
        require(endSamples==seconds*48000,"last video frame reaches expected sample count");
        require(!drained,"final audio remains undrained until shutdown");
    });
    cliprec_shutdown(); finishedOK(s); jitterSeconds=0;
#if defined(CLIPREC_CASE) && CLIPREC_CASE==4
    printf("fanout: video=%u/%u audio=%u missing=%u format_ok=%u peak_file_bytes=%zu stall_us=%llu\n",videoPackets.load(),total,audioPackets.load(),total-videoPackets.load(),packetFormatOK.load(),s->fileSink->peakBytes(),(unsigned long long)released->load()); fflush(stdout);
    require(released->load()>=5000000,"file sink remained stalled for five seconds");
    require(videoPackets==total && audioPackets>0 && packetFormatOK && !healthy->failed(),"owned video/audio packets reach healthy sink with no gaps");
    require(healthy->delivered()==s->fileSink->delivered(),"file and healthy sinks receive the same packet set");
    require(!retained.empty() && CMSampleBufferDataIsReady(retained.back()->sample()),"packets and formats survive encoder shutdown");
#endif
    AVURLAsset *asset=[AVURLAsset URLAssetWithURL:[NSURL fileURLWithPath:s->destination] options:nil];
    AVAssetTrack *video=[asset tracksWithMediaType:AVMediaTypeVideo].firstObject;
    AVAssetReader *vr=[[AVAssetReader alloc] initWithAsset:asset error:nil];
    AVAssetReaderTrackOutput *vo=[AVAssetReaderTrackOutput assetReaderTrackOutputWithTrack:video outputSettings:@{(id)kCVPixelBufferPixelFormatTypeKey:@(kCVPixelFormatType_32BGRA)}];
    [vr addOutput:vo]; require([vr startReading],"independent video decode");
    unsigned count=0; double videoEnd=0,maxPts=0;
    while(CMSampleBufferRef sample=[vo copyNextSampleBuffer]) {
        double pts=CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sample));
        unsigned wanted=count+(drops && count>=total/2?1:0);
        maxPts=fmax(maxPts,fabs(pts-(double)wanted/60));
        CVPixelBufferRef pixels=CMSampleBufferGetImageBuffer(sample);
        require(CVPixelBufferGetWidth(pixels)==W && CVPixelBufferGetHeight(pixels)==H,"decoded video dimensions");
        CVPixelBufferLockBaseAddress(pixels,kCVPixelBufferLock_ReadOnly);
        auto bytes=(const uint8_t *)CVPixelBufferGetBaseAddress(pixels);
        size_t stride=CVPixelBufferGetBytesPerRow(pixels);
        unsigned identity=0;
        for(unsigned bit=0;bit<6;bit++) if(bytes[(H-12)*stride+(bit*24+12)*4]>128) identity|=1u<<bit;
        unsigned markerX=(wanted%N)*6+8;
        bool marker=bytes[32*stride+markerX*4]>220;
        CVPixelBufferUnlockBaseAddress(pixels,kCVPixelBufferLock_ReadOnly);
        require(identity==wanted%N && marker,"decoded video identity and moving marker at timestamp");
        videoEnd=pts+1.0/60; ++count; CFRelease(sample);
    }
    require(vr.status==AVAssetReaderStatusCompleted,"video decode completes");
    require(count==total-(drops?1:0) && maxPts<0.0001,"video timestamps retain drop gap");
    if(transitions) {
        unsigned changes=0; uint64_t previousStream=0,previousGeneration=0;
        bool rate44100=false,rate48000=false;
        for(auto &block:oracle) {
            if(block.meta.stream_id!=previousStream || block.meta.clock_generation!=previousGeneration) ++changes;
            previousStream=block.meta.stream_id; previousGeneration=block.meta.clock_generation;
            rate44100|=block.meta.sample_rate==44100; rate48000|=block.meta.sample_rate==48000;
        }
        printf("transitions: clock_segments=%u rate44100=%u rate48000=%u\n",changes,rate44100,rate48000); fflush(stdout);
        require(changes>=3 && rate44100 && rate48000,"actual rate rebase and device replacement metadata");
    }
    DecodedAudio audio=decodeAudio(s->destination);
    printf("audio_tracks=%u required=1 video_frames=%u codec=%s hdr=%u duration=%.6f audio_first=%.6f audio_end=%.6f\n",
        audio.tracks,count,s->codec.UTF8String,hdr,audio.duration,audio.first,audio.end); fflush(stdout);
    require(audio.tracks==1,"required audio track");
    printf("format: channels=%u rate=%u\n",audio.channels,audio.rate); fflush(stdout);
    require(audio.channels==2 && audio.rate==48000,"stereo audio 48000 Hz");
    unsigned samples=(unsigned)llround(videoEnd*48000);
    auto expected=reference(epoch,samples);
    double alignment=0;
    if(jitter) {
        // Jittered stamps leave the segment anchor at their mean, a constant
        // sub-millisecond offset; the normalized RMS is phase-sensitive, so align
        // to it (coarse 0.25 ms, then 10 us) before measuring discontinuities.
        double best=1e9;
        auto probe=[&](double shift) {
            double e=rmsError(audio.pcm,reference(epoch+shift,samples),2400,samples-4800);
            if(e<best) { best=e; alignment=shift; }
        };
        for(int k=-24;k<=24;k++) probe(k*0.00025);
        double coarse=alignment;
        for(int k=-25;k<=25;k++) probe(coarse+k*0.00001);
        expected=reference(epoch+alignment,samples);
    }
    double error=rmsError(audio.pcm,expected,0,samples);
    printf("decoded: channels=%u rate=%u frames=%zu expected=%u normalized_RMS=%.6f container_end_error=%.6f\n",
        audio.channels,audio.rate,audio.pcm.size()/2,samples,error,fabs(audio.duration-videoEnd)); fflush(stdout);
    require(fabs(audio.duration-videoEnd)<0.001,"container trims AAC padding at video end");
    bool contentLength=audio.pcm.size()/2>=samples && audio.end-videoEnd<0.025;
    double tailError=rmsError(audio.pcm,expected,samples-2400,2400);
    printf("final_drain: tail_normalized_RMS=%.6f\n",tailError); fflush(stdout);
    double firstOffset=0,lastOffset=0,maxOffset=0;
    for(unsigned m=0;m<3;m++) {
        // Source markers repeat at .125 + k*.25 seconds; subtract the
        // presentation offset so these windows contain known audible markers.
        double sourceMarker=m==0?0.375:0.125+floor(((m==1?seconds/2.0:seconds-0.2)+0.2007-0.125)/0.25)*0.25;
        unsigned center=(unsigned)llround((sourceMarker-0.2007)*48000);
        unsigned begin=center-2400,length=4800;
        double best=1e9; int bestLag=0;
        for(int lag=-7200;lag<=7200;lag+=12) {
            double e=rmsError(audio.pcm,expected,begin,length,lag);
            if(e<best) { best=e; bestLag=lag; }
        }
        double offset=bestLag/48.0;
        printf("marker: t=%.3f offset_ms=%.3f normalized_RMS=%.6f\n",center/48000.0,offset,best); fflush(stdout);
        maxOffset=fmax(maxOffset,fabs(offset));
        if(m==0) firstOffset=offset; if(m==2) lastOffset=offset;
    }
    if(jitter) {
        printf("jitter: stamps=+-5ms alignment_ms=%.3f normalized_RMS=%.6f max_marker_offset_ms=%.3f drift_ms=%.3f\n",alignment*1000,error,maxOffset,lastOffset-firstOffset); fflush(stdout);
        maxOffset=fmax(maxOffset,fabs(alignment*1000+firstOffset));
    }
    require(maxOffset<=10,"marker A/V offset <=10 ms");
    require(contentLength,"AAC padding distinguished from content");
    require(error<=0.05,"AAC normalized RMS <=0.05 including leading/trailing content");
    require(tailError<=0.05,"final audio block drained without lost content");
    require(fabs(lastOffset-firstOffset)<=1,"no drift growth");
}
// Clip-time offset of the source marker nearest `clip`, as in audioProof.
static double markerOffset(const std::vector<float> &decoded,const std::vector<float> &expected,double clip,double &best) {
    double sourceMarker=0.125+floor((clip+0.2007-0.125)/0.25)*0.25;
    unsigned center=(unsigned)llround((sourceMarker-0.2007)*48000);
    best=1e9; int bestLag=0;
    for(int lag=-7200;lag<=7200;lag+=12) {
        double e=rmsError(decoded,expected,center-2400,4800,lag);
        if(e<best) { best=e; bestLag=lag; }
    }
    return bestLag/48.0;
}
// Presented frames stop for 8 s (hidden window, long load) while sound flows,
// then resume. The clip must continue and stay in sync after the pause.
static void pauseProof() {
    oracle.clear(); AudioFixture fixture;
    ClipSession *s=start(); fixture.render(4096);
    double epoch=ticksToSeconds(oracle.front().meta.first_sample_host_time)+0.2007;
    const unsigned before=60,pause=480,after=60;
    double failedAfter=-1;
    for(unsigned i=0;i<before+pause+after;i++) {
        double time=epoch+(double)i/60;
        if(i+1<before+pause+after) fixture.through(time+1.0/60-0.010);
        if(i<before || i>=before+pause) { frame(s,i%N,time); continue; }
        // The recorder's 10 ms tick, run synchronously since the fixture is faster than real time.
        __block bool ok=true;
        dispatch_sync(s->work,^{ if(s->audio) ok=s->audio->pump(s->epoch,s->videoEnd,false); });
        if(!ok) { failedAfter=(double)(i-before)/60; break; }
    }
    printf("pause: seconds=%.3f failed_after=%.3f stopping=%u error=%s\n",pause/60.0,failedAfter,s->stopping.load(),s->error?s->error.UTF8String:"none"); fflush(stdout);
    require(failedAfter<0 && !s->stopping && !s->error,"video pause with sound flowing keeps recording");
    fixture.through(epoch+(before+pause+after)/60.0+0.003);
    cliprec_shutdown(); finishedOK(s);
    Decoded video=decode(s->destination);
    double videoEnd=(before+pause+after)/60.0;
    DecodedAudio audio=decodeAudio(s->destination);
    unsigned samples=(unsigned)llround(videoEnd*48000);
    auto expected=reference(epoch,samples);
    double maxOffset=0,worst=0;
    for(double clip:{0.5,videoEnd-0.75,videoEnd-0.5}) {
        double best,offset=markerOffset(audio.pcm,expected,clip,best);
        printf("pause marker: clip=%.3f offset_ms=%.3f normalized_RMS=%.6f\n",clip,offset,best); fflush(stdout);
        maxOffset=fmax(maxOffset,fabs(offset)); worst=fmax(worst,best);
    }
    double tail=rmsError(audio.pcm,expected,(before+pause)*800,after*800);
    printf("pause: video_frames=%u duration=%.6f audio_tracks=%u resumed_RMS=%.6f silence=%llu queue_peak=%llu/96000\n",
        video.count,video.duration,audio.tracks,tail,(unsigned long long)s->audioSilence,(unsigned long long)s->audioQueuePeak); fflush(stdout);
    require(video.count==before+after && fabs(audio.duration-videoEnd)<0.001,"paused clip keeps both sides of the gap");
    require(audio.tracks==1 && maxOffset<=10 && worst<=0.05 && tail<=0.05,"A/V markers in sync after the pause");
}
static void lifecycleProof() {
    oracle.clear(); AudioFixture fixture;
    { std::lock_guard<std::mutex> lock(control); current=nil; }
    cliprec_action(0); ClipSession *empty=current;
    cliprec_shutdown();
    require(empty->ended && !empty->published && !empty->encoder && !empty->audio,"empty quit retires capture");
    size_t callbacks=oracle.size(); fixture.render();
    require(oracle.size()==callbacks,"capture stop quiesces actual mixer callbacks");
    cliprec_action(0); ClipSession *next=current;
    require(next!=empty && active,"immediate menu restart after empty quit");
    cliprec_shutdown(); require(next->ended && !next->published,"empty start stop bounded");
    ClipSession *s=start();
    TestDrawable *late=drawable(); id<MTLCommandBuffer> cb=[commands commandBuffer];
    cliprec_present(cb,pattern(0),late,1); [cb commit]; [cb waitUntilCompleted]; syncWork(s);
    require(s->outstanding==1,"unshown presentation remains pending");
    double began=NSProcessInfo.processInfo.systemUptime; cliprec_shutdown();
    double elapsed=NSProcessInfo.processInfo.systemUptime-began;
    require(s->ended && !s->published && elapsed<4,"unshown stop bounded with no clip");
    ClipSession *fresh=start();
    unsigned queued=s->queuedPackets.load();
    [late shown:ticksToSeconds(mach_absolute_time())];
    compressed((__bridge void *)s,nullptr,0,0,nullptr); syncWork(s);
    require(s->ended && s->queuedPackets==queued && current==fresh && active,"stale presentation and VT callbacks isolate sessions");
    cliprec_shutdown();
    printf("lifecycle: empty/unshown/quit/restart/stale passed; unshown_stop=%.3f s\n",elapsed); fflush(stdout);
}
static void pressureProof() {
    oracle.clear(); AudioFixture fixture;
    ClipSession *s=start(); fixture.render(4096);
    double epoch=ticksToSeconds(oracle.front().meta.first_sample_host_time)+0.2007;
    for(unsigned i=0;i<N;i++) {
        fixture.through(epoch+(i+1)/60.0+0.01);
        frame(s,i,epoch+i/60.0);
    }
    for(unsigned i=0;i<200;i++) {
        __block bool drained=false;
        dispatch_sync(s->work,^{ s->audio->pump(s->epoch,s->videoEnd,false); drained=s->accepted==N && s->fileSink->drained() && s->queuedPackets==0 && s->audio->drained(s->videoEnd); });
        if(drained) break;
        usleep(5000);
    }
    dispatch_sync(s->work,^{ s->audio->forcePressure=true; });
    fixture.render(96000+4096);
    dispatch_sync(s->work,^{ if(s->audio) s->audio->forcePressure=false; });
    require(!dispatch_group_wait(s->finished,dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_SEC)),"audio pressure stop bounded");
    require(s->ended && s->published && s->error && [s->error containsString:@"Audio capture queue"],"capture overflow surfaces error and publishes valid prefix");
    Decoded prefix=decode(s->destination);
    require(validates(prefix),"audio overflow retains decodable video prefix");
    auto audio=decodeAudio(s->destination);
    printf("overflow prefix: tracks=%u frames=%zu videoEnd=%.6f\n",audio.tracks,audio.pcm.size()/2,s->videoEnd); fflush(stdout);
    require(audio.tracks==1 && audio.pcm.size()/2>=(size_t)llround(s->videoEnd*48000),"audio overflow retains complete prior audio prefix");
    require(s->audioOverflow>0,"callback overflow remains counted after shutdown");
    require(s->audioQueuePeak<=96000 && s->queuedPackets==0 && s->outstanding==0,"queues stay bounded and retire");
    printf("pressure: prefix_frames=%u audio_frames=%zu queue_peak=%llu/96000 error=%s\n",prefix.count,audio.pcm.size()/2,(unsigned long long)s->audioQueuePeak,s->error.UTF8String); fflush(stdout);
}
static CMTime requestedFileEnd=kCMTimeInvalid;
static void (*originalEndSession)(id,SEL,CMTime);
static void observeEndSession(id writer,SEL selector,CMTime end) {
    requestedFileEnd=end;
    originalEndSession(writer,selector,end);
}
static void failedSinkProof() {
    Method method=class_getInstanceMethod(AVAssetWriter.class,@selector(endSessionAtSourceTime:));
    originalEndSession=(void(*)(id,SEL,CMTime))method_setImplementation(method,(IMP)observeEndSession);
    requestedFileEnd=kCMTimeInvalid;
    oracle.clear(); AudioFixture fixture;
    ClipSession *s=start(false,W,H,nil,1,[&]{ fixture.render(4096); });
    double epoch=ticksToSeconds(oracle.front().meta.first_sample_host_time)+0.2007;
    std::atomic<unsigned> video{0},audio{0};
    auto healthy=std::make_shared<clip::Sink>([&](const clip::OwnedPacket& p) {
        if(p->track==clip::Track::video) ++video; else ++audio;
        return clip::Result::consumed;
    });
    dispatch_sync(s->work,^{ s->outputs.add(healthy); });
    for(unsigned i=0;i<60;++i) {
        fixture.through(epoch+(i+1)/60.0+0.01);
        if(i==10) s->forceFileFailure=true;
        frame(s,i%N,epoch+i/60.0);
        require(!s->stopping && active,"file failure leaves capture and healthy sink active");
    }
    cliprec_shutdown(); finishedOK(s);
    printf("failed_file: healthy_video=%u/60 audio=%u file_failed=%d file_video=%u stopping_error=%s\n",video.load(),audio.load(),s->fileSink->failed(),s->accepted.load(),s->error.UTF8String); fflush(stdout);
    require(video==60 && audio>0 && !healthy->failed() && s->fileSink->failed(),"failed file induces zero healthy sink gaps");
    Decoded prefix=decode(s->destination);
    double expected=s->accepted.load()/60.0;
    method_setImplementation(method,(IMP)originalEndSession);
    double requested=CMTimeGetSeconds(requestedFileEnd);
    printf("failed_file endpoint: frames=%u duration=%.6f requested=%.6f expected=%.6f capture=%.6f\n",prefix.count,prefix.duration,requested,expected,s->videoEnd); fflush(stdout);
    require(s->error && s->published && prefix.valid,"failed file retains an explicit-error decodable prefix");
    require(prefix.count==10 && fabs(prefix.duration-expected)<0.001 && fabs(requested-expected)<0.001,"failed file ends at its last written frame");
}
static void blockedSinkProof() {
    oracle.clear(); AudioFixture fixture;
    ClipSession *s=start(false,W,H,nil,1,[&]{ fixture.render(4096); });
    struct Block { dispatch_semaphore_t entered=dispatch_semaphore_create(0),release=dispatch_semaphore_create(0); std::atomic<bool> returned{false}; };
    auto block=std::make_shared<Block>();
    auto sink=std::make_shared<clip::Sink>([block](const clip::OwnedPacket&) {
        dispatch_semaphore_signal(block->entered);
        dispatch_semaphore_wait(block->release,DISPATCH_TIME_FOREVER);
        block->returned=true; return clip::Result::consumed;
    });
    std::weak_ptr<clip::Sink> weak=sink;
    auto *registration=&sink;
    dispatch_sync(s->work,^{ s->outputs.add(*registration); });
    double epoch=ticksToSeconds(oracle.front().meta.first_sample_host_time)+0.2007;
    for(unsigned i=0;i<3;++i) { fixture.through(epoch+(i+1)/60.0+0.01); frame(s,i,epoch+i/60.0); }
    require(!dispatch_semaphore_wait(block->entered,dispatch_time(DISPATCH_TIME_NOW,NSEC_PER_SEC)),"fake sink is blocked in I/O");
    cliprec_stop(); syncWork(s);
    double deadline=s->stopDeadline+6;
    double remaining=fmax(0,deadline-NSProcessInfo.processInfo.systemUptime)+0.25;
    bool completed=!dispatch_group_wait(s->finished,dispatch_time(DISPATCH_TIME_NOW,(int64_t)(remaining*NSEC_PER_SEC)));
    char status[256]; cliprec_status(status,sizeof status);
    printf("blocked_sink: completed=%u deadline_delta=%.3f saving=%u returned=%u\n",completed,
        NSProcessInfo.processInfo.systemUptime-deadline,!strcmp(status,"Saving..."),block->returned.load()); fflush(stdout);
    require(completed && strcmp(status,"Saving...") && !block->returned,"blocked I/O cannot extend drain deadline");
    require(sink->failed() && sink->queued()==0,"timed-out sink fails and releases queued packets");
    sink.reset();
    require(weak.expired(),"recorder releases failed sink references without joining blocked I/O");
    cliprec_action(0); ClipSession *next=current;
    printf("blocked_sink restart: accepted=%u\n",next!=s && active.load()); fflush(stdout);
    require(next!=s && active,"menu accepts a new recording before old I/O returns");
    cliprec_shutdown();
    dispatch_semaphore_signal(block->release);
    for(unsigned i=0;i<1000 && !block->returned;++i) usleep(1000);
    require(block->returned,"detached consumer safely returns after session completion");
}
#if defined(CLIPREC_CASE) && (CLIPREC_CASE==8 || CLIPREC_CASE==9)
#ifndef CLIPREC_LIVE_GAP
#define CLIPREC_LIVE_GAP 1
#endif
static uint64_t liveSegmentCount(ClipSession *s) {
    __block uint64_t n=0;
    dispatch_sync(s->work,^{ n=s->liveSegments?s->liveSegments->stats().segments:0; });
    return n;
}
// A focus gap (Space swipe) through the real ClipAudio path: 3 s presented at 60 fps, 12 s with no frame while the
// mixer keeps running, 3 s presented. Case 8 records a file (with or without live: its sound must be identical);
// case 9 is live only. Live must keep cutting segments through the gap from held frames and its own sound.
static void liveGapProof(bool live,bool file) {
    oracle.clear(); AudioFixture fixture; testClock=0;
    { std::lock_guard<std::mutex> lock(control); current=nil; }
    liveState=0;
    ClipSession *s=nil;
    if(file) { cliprec_action(0); s=current; if(live) { s->broadcast=true; liveState=1; } }
    else { require(cliprec_live_start()==1,"live-only Go"); s=current; }
    fixture.render(4096);
    @autoreleasepool {
        TestDrawable *d=drawable();
        d.texture=[gpu newTextureWithDescriptor:[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:W height:H mipmapped:NO]];
        id<MTLCommandBuffer> cb=[commands commandBuffer];
        cliprec_present(cb,pattern(0),d,1);
        [cb commit]; [cb waitUntilCompleted]; syncWork(s);
    }
    require(s->ready && (!live || (liveState==2 && s->liveEncoder)),"capture and live prepared");
    double epoch=ticksToSeconds(oracle.front().meta.first_sample_host_time)+0.2007;
    const unsigned before=180,gap=720,after=180,total=before+gap+after;
    uint64_t atGap=0,afterGap=0;
    for(unsigned i=0;i<total && !s->stopping;i++) {
        double time=epoch+(double)i/60;
        testClock=time;
        if(i+1<total) fixture.through(time+1.0/60-0.010);
        if(i==before) atGap=liveSegmentCount(s);
        if(i<before || i>=before+gap) frame(s,i%N,time);
        // The recorder's 10 ms tick, run synchronously since the fixture is faster than real time.
        else dispatch_sync(s->work,^{ if(![s pumpAudio:s->epoch final:false]) [s fail:s->audio->error]; [s pump]; });
        if(live) dispatch_sync(s->work,^{ if(s->liveEncoder) s->liveEncoder->complete(); });
        if(i+1==before+gap) afterGap=liveSegmentCount(s);
    }
    __block uint64_t held=0; __block bool failed=false; __block const char *reason=nullptr;
    dispatch_sync(s->work,^{
        held=s->liveHeldFrames;
        if(s->liveSegments) { auto stats=s->liveSegments->stats(); failed=stats.failed; reason=stats.reason; }
    });
    printf("live_gap: live=%d file=%d gap_s=%.1f segments_at_gap=%llu gap_segments=%llu held=%llu live_state=%d failed=%d reason=%s pool_drops=%u stopping=%u error=%s\n",
        live,file,gap/60.0,(unsigned long long)atGap,(unsigned long long)(afterGap-atGap),(unsigned long long)held,liveState.load(),failed,
        reason?reason:"none",s->poolDrops.load(),s->stopping.load(),s->error?s->error.UTF8String:"none"); fflush(stdout);
    require(!s->stopping && !s->error,"capture survives the focus gap");
    // Held frames and the same capture surfaces: no presented frame is dropped for a free surface.
    require(s->poolDrops==0,"live and its held surface leave the recording its surfaces at 60 fps");
    if(live) require(liveState==2 && !failed && afterGap-atGap>=4 && held>=300,"live keeps cutting segments through the gap");
    fixture.through(epoch+total/60.0+0.003);
    cliprec_shutdown();
    if(!file) { require(!dispatch_group_wait(s->finished,dispatch_time(DISPATCH_TIME_NOW,10*NSEC_PER_SEC)),"live-only stop finalizes"); return; }
    finishedOK(s);
    DecodedAudio audio=decodeAudio(s->destination);
    size_t samples=audio.pcm.size()/2,zero=0;
    for(size_t n=0;n<samples;n++) zero+=audio.pcm[2*n]==0 && audio.pcm[2*n+1]==0;
    double videoEnd=total/60.0;
    auto expected=reference(epoch,(unsigned)llround(videoEnd*48000));
    double tail=rmsError(audio.pcm,expected,(before+gap)*800,after*800);
    // Compared verbatim with the same run without live by cliprec_audio_test.py.
    printf("live_gap_file: video_end_samples=%lld audio_samples=%zu zero_samples=%zu duration=%.6f resumed_RMS=%.3f\n",
        (long long)llround(videoEnd*48000),samples,zero,audio.duration,tail); fflush(stdout);
    require(audio.tracks==1 && fabs(audio.duration-videoEnd)<0.001 && tail<=0.05,"file keeps both sides of the gap in sync");
}
#endif
static dispatch_semaphore_t fileIOEntered,fileIORelease;
static BOOL (*originalAppend)(id,SEL,CMSampleBufferRef);
static BOOL blockedAppend(id input,SEL selector,CMSampleBufferRef sample) {
    dispatch_semaphore_signal(fileIOEntered);
    dispatch_semaphore_wait(fileIORelease,DISPATCH_TIME_FOREVER);
    return originalAppend(input,selector,sample);
}
static void blockedFileProof() {
    oracle.clear(); AudioFixture fixture;
    ClipSession *s=start(false,W,H,nil,1,[&]{ fixture.render(4096); });
    std::weak_ptr<ClipFile> oldFile=s->file;
    fileIOEntered=dispatch_semaphore_create(0); fileIORelease=dispatch_semaphore_create(0);
    Method method=class_getInstanceMethod(AVAssetWriterInput.class,@selector(appendSampleBuffer:));
    originalAppend=(BOOL(*)(id,SEL,CMSampleBufferRef))method_setImplementation(method,(IMP)blockedAppend);
    double epoch=ticksToSeconds(oracle.front().meta.first_sample_host_time)+0.2007;
    fixture.through(epoch+0.03); frame(s,0,epoch);
    require(!dispatch_semaphore_wait(fileIOEntered,dispatch_time(DISPATCH_TIME_NOW,NSEC_PER_SEC)),"file append is blocked in I/O");
    cliprec_stop(); syncWork(s);
    double deadline=s->stopDeadline+6;
    double remaining=fmax(0,deadline-NSProcessInfo.processInfo.systemUptime)+0.25;
    bool completed=!dispatch_group_wait(s->finished,dispatch_time(DISPATCH_TIME_NOW,(int64_t)(remaining*NSEC_PER_SEC)));
    printf("blocked_file: completed=%u deadline_delta=%.3f writer_owned_by_consumer=%u\n",completed,
        NSProcessInfo.processInfo.systemUptime-deadline,!oldFile.expired()); fflush(stdout);
    require(completed && !s->file && !s->fileSink && !oldFile.expired(),"blocked file state detaches without concurrent writer finalization");
    require(s->error && !s->published && !s->encoder && !s->audio,"blocked file retires capture resources with an error");
    cliprec_action(0); ClipSession *next=current;
    require(next!=s && active,"new recording starts while old file append is blocked");
    cliprec_shutdown();
    dispatch_semaphore_signal(fileIORelease);
    for(unsigned i=0;i<2000 && (!oldFile.expired() ||
            [NSFileManager.defaultManager fileExistsAtPath:s->temporary]);++i) usleep(1000);
    method_setImplementation(method,(IMP)originalAppend);
    require(oldFile.expired(),"blocked file resources retire after its in-flight append returns");
    require(![NSFileManager.defaultManager fileExistsAtPath:s->temporary],"abandoned file is removed after I/O returns");
}
int main(int argc, char **argv) { @autoreleasepool {
    require(argc==2,"injected worktree scratch");
    outputFolder=[NSString stringWithUTF8String:argv[1]];
    gpu=MTLCreateSystemDefaultDevice(); if(!gpu) { puts("SKIP: Metal unavailable"); return 3; }
    commands=[gpu newCommandQueue];
#ifdef CLIPREC_AUDIO_BASELINE
    AudioFixture fixture;
    ClipSession *s=start(); fixture.render(4096);
    for(unsigned i=0;i<N;i++) { fixture.render(800); frame(s,i,100+i/60.0); }
    cliprec_shutdown(); finishedOK(s);
    auto decoded=decodeAudio(s->destination);
    printf("audio_tracks=%u required=1 video_frames=%u generated_pcm_frames=%zu\n",decoded.tracks,decode(s->destination).count,fixture.rendered);
    return decoded.tracks==1?0:1;
#else
#ifndef CLIPREC_PCM_CONTROL
    AudioStreamBasicDescription a={},b={}; a.mSampleRate=48000; a.mFormatID=kAudioFormatLinearPCM;
    a.mFormatFlags=kAudioFormatFlagIsFloat|kAudioFormatFlagIsPacked; a.mChannelsPerFrame=2;
    a.mBytesPerPacket=a.mBytesPerFrame=8; a.mFramesPerPacket=1; a.mBitsPerChannel=32;
    b.mSampleRate=48000; b.mFormatID=kAudioFormatMPEG4AAC; b.mChannelsPerFrame=2;
    b.mFramesPerPacket=1024; b.mFormatFlags=kMPEG4Object_AAC_LC;
    AudioConverterRef converter=nullptr;
    UInt32 bytes=sizeof(b);
    OSStatus rc=AudioFormatGetProperty(kAudioFormatProperty_FormatInfo,0,nullptr,&bytes,&b);
    if(!rc) rc=AudioConverterNew(&a,&b,&converter);
    if(rc) { printf("SKIP: AAC encoder unavailable: codec factory rc=%d\n",(int)rc); return 3; }
    AudioConverterDispose(converter);
#endif
#if defined(CLIPREC_CASE) && CLIPREC_CASE==1
    audioProof(false,false,3,false,false,true); puts("cliprec_audio: PASS"); return 0;
#elif defined(CLIPREC_CASE) && CLIPREC_CASE==3
    audioProof(false,false,1); puts("cliprec_audio: PASS"); return 0;
#elif defined(CLIPREC_CASE) && CLIPREC_CASE==7
    blockedFileProof(); puts("cliprec_audio: PASS"); return 0;
#elif defined(CLIPREC_CASE) && CLIPREC_CASE==6
    blockedSinkProof(); puts("cliprec_audio: PASS"); return 0;
#elif defined(CLIPREC_CASE) && CLIPREC_CASE==5
    failedSinkProof(); puts("cliprec_audio: PASS"); return 0;
#elif defined(CLIPREC_CASE) && CLIPREC_CASE==4
    audioProof(false,false,6); puts("cliprec_audio: PASS"); return 0;
#elif defined(CLIPREC_CASE) && CLIPREC_CASE==2
    pauseProof(); puts("cliprec_audio: PASS"); return 0;
#elif defined(CLIPREC_CASE) && CLIPREC_CASE==8
    liveGapProof(CLIPREC_LIVE_GAP,true); puts("cliprec_audio: PASS"); return 0;
#elif defined(CLIPREC_CASE) && CLIPREC_CASE==9
    liveGapProof(true,false); puts("cliprec_audio: PASS"); return 0;
#endif
    audioProof(false,false,1);
#ifndef CLIPREC_MUTANT
    audioProof(true,true,1,true);
    audioProof(true,false,1);
    audioProof(false,true,10);
    audioProof(false,false,1,false,true);
    lifecycleProof();
    pressureProof();
    audioProof(false,false,3,false,false,true);
    pauseProof();
#endif
    puts("cliprec_audio: PASS"); return 0;
#endif
} }
