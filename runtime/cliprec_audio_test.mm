// Actual six-channel DirectSound PCM and synthetic drawables traverse the
// production Metal -> VT -> MP4 path. Independent AVAssetReader decode retains
// PCM timestamps in the oracle and checks content, channel order and A/V drift.
// Measured controls: original 0/1 audio tracks with 36 decoded video frames;
// float-PCM control 10 s, 600 video frames / 480000 stereo frames, 0 ms markers,
// RMS 0.001458, exact container end. Five requested broken controls fail.
// Startup guard: early accepted/epoch 0/0; removed guard 1/1, rejected (exit 1).
// Jittered stamps (-5..+5 ms per block): pre-fix RMS 1.339511, markers to 147 ms
// -> fixed 0.012352, 0 ms. 8 s video pause: pre-fix overflow after 5.467 s ->
// fixed records on, markers 0 ms after the pause.
// AAC passes where the codec is available; the probe exits 3 only where the codec
// factory is restricted. A PCM control never substitutes for decoded AAC
// priming, padding or quality checks.
#define CLIPREC_AUDIO_TEST 1
#include "../host/audio/dsound_host.h"
#include <vector>
#include <cmath>
#include <algorithm>
#include <mach/mach_time.h>
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
#define main video_fixture_main
#include "cliprec_test.mm"
#undef main
#undef ds_capture_start
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
    void rate(unsigned rate) { std::lock_guard<std::mutex> lock(d->m); rebase_rate(*d,rate); }
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
static void audioProof(bool fallback,bool hdr,unsigned seconds,bool drops=false,bool transitions=false,bool jitter=false) {
    oracle.clear(); jitterSeconds=jitter?0.005:0; auto fixture=std::make_unique<AudioFixture>();
    ClipSession *s=start(fallback,W,H,hdr?linearPattern(0):nil,1,[&] {
        fixture->render(4096);
        require(oracle.size()==16,"PCM subscription starts before video preparation");
    });
    double epoch=ticksToSeconds(oracle.front().meta.first_sample_host_time)+0.2007;
    double firstAudio=ticksToSeconds(oracle.front().meta.first_sample_host_time);
    frame(s,0,firstAudio-0.05);
    printf("startup: early_video_accepted=%u epoch_set=%u required=0/0\n",s->accepted.load(),s->epoch!=0); fflush(stdout);
    require(!s->epoch && s->accepted==0 && !s->stopping,"video waits for PCM spanning first presentation");
    unsigned total=seconds*60;
    for(unsigned i=0;i<total;i++) {
        if(s->stopping) { fprintf(stderr,"unexpected stop frame=%u reason=%s\n",i,s->error.UTF8String); require(false,"recording remains active"); }
        double time=epoch+(double)i/60;
        if(transitions) {
            while(ticksToSeconds(mach_absolute_time())<time) usleep(500);
            if(i==30) fixture->rate(44100);
            if(i==45) fixture=std::make_unique<AudioFixture>();
        }
        // Deliberately leave the last audio block for stop's final drain.
        if(i+1<total) fixture->through(time+1.0/60-0.010);
        if(drops && i==total/2) {
            s->forceBackpressure=true;
            frame(s,i%N,time,true,false,true,hdr?linearPattern(i%N):nil);
            s->forceBackpressure=false;
        } else frame(s,i%N,time,true,i%2,true,hdr?linearPattern(i%N):nil);
    }
    fixture->through(epoch+seconds+0.003);
    bool inside=false;
    for(auto &block:oracle) {
        double begin=ticksToSeconds(block.meta.first_sample_host_time);
        inside|=begin<epoch+seconds && begin+(double)block.meta.frames/block.meta.sample_rate>epoch+seconds;
    }
    require(inside,"stop trims through the middle of a PCM block");
    cliprec_shutdown(); finishedOK(s); jitterSeconds=0;
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
    printf("pause: video_frames=%u duration=%.6f audio_tracks=%u resumed_RMS=%.6f silence=%llu queue_peak=%llu/262144\n",
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
        dispatch_sync(s->work,^{ s->audio->pump(s->epoch,s->videoEnd,false); drained=s->accepted==N && s->packets.count==0 && s->queuedPackets==0 && s->audio->drained(s->videoEnd); });
        if(drained) break;
        usleep(5000);
    }
    dispatch_sync(s->work,^{ s->audio->forcePressure=true; });
    fixture.render(262144+4096);
    dispatch_sync(s->work,^{ if(s->audio) s->audio->forcePressure=false; });
    require(!dispatch_group_wait(s->finished,dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_SEC)),"audio pressure stop bounded");
    require(s->ended && s->published && s->error && [s->error containsString:@"Audio capture queue"],"capture overflow surfaces error and publishes valid prefix");
    Decoded prefix=decode(s->destination);
    require(validates(prefix),"audio overflow retains decodable video prefix");
    auto audio=decodeAudio(s->destination);
    printf("overflow prefix: tracks=%u frames=%zu videoEnd=%.6f\n",audio.tracks,audio.pcm.size()/2,s->videoEnd); fflush(stdout);
    require(audio.tracks==1 && audio.pcm.size()/2>=(size_t)llround(s->videoEnd*48000),"audio overflow retains complete prior audio prefix");
    require(s->audioQueuePeak<=262144 && s->queuedPackets==0 && s->outstanding==0,"queues stay bounded and retire");
    printf("pressure: prefix_frames=%u audio_frames=%zu queue_peak=%llu/262144 error=%s\n",prefix.count,audio.pcm.size()/2,(unsigned long long)s->audioQueuePeak,s->error.UTF8String); fflush(stdout);
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
    if(rc) { printf("SKIP: AAC encoder unavailable rc=%d; original silent clip confirmed\n",(int)rc); return 3; }
    AudioConverterDispose(converter);
#endif
#if defined(CLIPREC_CASE) && CLIPREC_CASE==1
    audioProof(false,false,3,false,false,true); puts("cliprec_audio: PASS"); return 0;
#elif defined(CLIPREC_CASE) && CLIPREC_CASE==2
    pauseProof(); puts("cliprec_audio: PASS"); return 0;
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
