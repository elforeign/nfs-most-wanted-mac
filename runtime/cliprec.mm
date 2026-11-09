// SDR video capture from SDR or linear HDR presentation sources.
// The presenter only reserves a surface and encodes conversion;
// hardware compression and MP4 passthrough run on a separate serial queue.
#import <Cocoa/Cocoa.h>
#import <AVFoundation/AVFoundation.h>
#import <VideoToolbox/VideoToolbox.h>
#import <CoreVideo/CoreVideo.h>
#import <AudioToolbox/AudioToolbox.h>
#include "cliprec.h"
#include "clip_packets.h"
#include "replay.h"
#include "live_encode.h"
#include "live_seg.h"
#include "livestream.h"
#include <cstring>
extern "C" int settings_get(const char *id);
extern "C" void portmenu_open_folder(const char *path);
extern "C" void portmenu_status(const char *fmt, ...);
#include <atomic>
#include <mutex>
#include <unistd.h>
#include <mach/mach_time.h>
#include "../host/audio/dsound_host.h"
#include "../media/media.h"
#include <memory>
#include <algorithm>

static constexpr unsigned kSurfaces = 8, kOverload = 120;
// Live holds at most its pending frames plus the one held picture; the recording keeps the other surfaces
// (cliprec_aac_test: 0 pool drops at 60 fps with live and the hold; a leaked hold drops 120 and stops capture).
static_assert(live::Encoder::maxPending+1<=kSurfaces/2+1,"Live and its held picture must leave recording surfaces available");
// No presented frame reaches capture while the game is inactive (it parks in GetMessageA) or its window is occluded
// (nothing is presented). Live then repeats its last picture: after kLiveHoldAfter s without a frame, one held frame
// every kLiveHoldStep s keeps the 2 s IDR/segment cadence, which the segmenter needs (a GOP over 5 s is a failure).
static constexpr double kLiveHoldAfter = 0.5, kLiveHoldStep = 1.0/30;
#ifdef CLIPREC_TESTING
static double testClock; // the hold's wall clock; 0 keeps the hold off in the fixture's synthetic timelines
static double clipNow(void) { return testClock; }
static int liveHoldEnabled(void) { return 1; }
#else
static double clipNow(void) { return CACurrentMediaTime(); }
// NFS_TEST_LIVE_HOLD=0 (test only): no held frames, the control run of runtime/live_fake.sh.
static int liveHoldEnabled(void) {
    static int on=-1;
    if(on<0) { const char *e=getenv("NFS_TEST_LIVE_HOLD"); on=!(e && !strcmp(e,"0")); }
    return on;
}
#endif
static std::atomic<bool> active{false};
static std::atomic<int> liveState{0};
static std::mutex control;
@class ClipSession;
static ClipSession *current;
static void deactivate(ClipSession *s) {
    std::lock_guard<std::mutex> lock(control);
    if (current==s) active.store(false,std::memory_order_release);
}
static NSString *outputFolder; // injectable by the offline test; no environment start switch
static std::shared_ptr<replay::Ring> actionReplay;
static std::unique_ptr<replay::Export> replayExport;

// Video-range BT.709 Y'CbCr, centred 2x2 chroma. Convert the presentation
// source directly into encoder planes, with no intermediate full-frame copy.
static NSString *const conversion = @R"metal(
#include <metal_stdlib>
using namespace metal;
float3 srgb(float3 c) {
    return select(1.055*pow(c,float3(1.0/2.4))-0.055,12.92*c,c<=0.0031308);
}
float3 clean(float3 c) { return max(select(c,float3(0),!isfinite(c)),0.0); }
float3 sdr_texel(texture2d<float,access::sample> src, int2 p) {
    p=clamp(p,int2(0),int2(src.get_width(),src.get_height())-1);
    // H=1 is decoded BGRA8. Recover those bytes before interpolation, matching
    // SDR presentation even when the capture size differs from the source.
    return round(clamp(srgb(clean(src.read(uint2(p)).rgb)),0.0,1.0)*255.0)/255.0;
}
float3 capture_rgb(texture2d<float,access::sample> src, float2 uv, float2 transport) {
    constexpr sampler s(coord::normalized,filter::linear,address::clamp_to_edge);
    if (transport.x && transport.y<=1.0) {
        float2 xy=uv*float2(src.get_width(),src.get_height())-0.5;
        int2 p=int2(floor(xy)); float2 f=fract(xy);
        return mix(mix(sdr_texel(src,p),sdr_texel(src,p+int2(1,0)),f.x),
                   mix(sdr_texel(src,p+int2(0,1)),sdr_texel(src,p+int2(1,1)),f.x),f.y);
    }
    float3 c=clean(src.sample(s,uv).rgb);
    if (transport.x) {
        // Same hue-preserving SDR capture shoulder as hdr_fs: linear knee .75,
        // asymptote 1, unit slope at the knee. Bound transport to this frame's
        // headroom before the shoulder. H=1 bypasses tone mapping above.
        float peak=max(c.r,max(c.g,c.b));
        if (peak>0.75) c *= (0.75+0.25*(1.0-exp(-(min(peak,transport.y)-0.75)/0.25)))/peak;
        c=srgb(c);
    }
    return clamp(c,0.0,1.0);
}
kernel void nv12(texture2d<float,access::sample> src [[texture(0)]],
                 texture2d<float,access::write> y [[texture(1)]],
                 texture2d<float,access::write> uv [[texture(2)]],
                 constant float2 &transport [[buffer(0)]],
                 uint2 p [[thread_position_in_grid]]) {
    if (p.x>=uv.get_width() || p.y>=uv.get_height()) return;
    float3 avg=0;
    for(uint j=0;j<2;j++) for(uint i=0;i<2;i++) {
        uint2 q=p*2+uint2(i,j);
        float3 c=capture_rgb(src,(float2(q)+0.5)/float2(y.get_width(),y.get_height()),transport);
        float l=dot(c,float3(0.2126,0.7152,0.0722));
        y.write(float4((16.0+219.0*l)/255.0),q); avg+=c*0.25;
    }
    float l=dot(avg,float3(0.2126,0.7152,0.0722));
    uv.write(float4((128.0+224.0*(avg.b-l)/1.8556)/255.0,
                    (128.0+224.0*(avg.r-l)/1.5748)/255.0,0,1),p);
}
)metal";

// Single producer (the mixer), single consumer (the recorder queue). The tap
// copies into a fixed ring and never allocates, waits, or touches the writer.
class ClipAudio {
#ifdef CLIPREC_TESTING
    friend struct ClipAudioProof;
#endif
    struct Point { double time; float l, r; uint64_t segment; };
    static constexpr uint64_t capacity = 96000;
    struct RawBlock { DsTimedMixBlock meta; uint64_t first, endNS; };
    static constexpr uint64_t blockCapacity=4096;
    std::unique_ptr<float[]> raw{new float[capacity*6]{}};
    std::unique_ptr<RawBlock[]> rawBlocks{new RawBlock[blockCapacity]{}};
    std::atomic<uint64_t> rawRead{0},rawWrite{0},blockRead{0},blockWrite{0},readNS{0};
    uint64_t writeNS=0;
    uint32_t rate=0;
    std::unique_ptr<clip::AACEncoder> aac;
    clip::Fanout *fanout=nullptr;
    std::unique_ptr<Point[]> points{new Point[capacity]};
    std::atomic<uint64_t> read{0}, write{0}, latestStart{0};
    uint64_t stream=0,generation=0,segment=0;
    // Producer-only segment clock. Device stamps are "now + queued frames" and
    // jitter by milliseconds per block, so samples are placed by sample_position
    // from one anchor; stamps only refine that anchor (see place()).
    double anchor=0,offset=0; uint64_t anchorPosition=0,blocks=0;
    std::atomic<bool> fault{false};
    double tickSeconds;
    bool subscribed=false;
    int64_t emitted=0;
    // Live has its own cursor and encoder over the same points, so held frames carry its sound on while the
    // recording's sound stops at its video end, exactly as without live.
    std::shared_ptr<clip::Sink> liveSink;
    std::unique_ptr<clip::AACEncoder> liveAac;
    uint64_t liveRead=0;
    int64_t liveEmitted=0;
    uint64_t liveSilence=0;
public:
    const char *liveError=nullptr; // static token; live failed, the recording continues
    std::atomic<uint64_t> overflowFrames{0};
    NSString *error=nil;
    uint64_t silence=0;
    std::atomic<uint64_t> maximumQueued{0};
#ifdef CLIPREC_TESTING
    bool forcePressure=false;
#endif
    ClipAudio() {
        mach_timebase_info_data_t scale; mach_timebase_info(&scale);
        tickSeconds=(double)scale.numer/scale.denom/1e9;
    }
    ~ClipAudio() { stop(); }
    // Time of this block's first sample. A segment's first 32 stamps average
    // into the anchor (before the first presentation at clip start); after that
    // the anchor only slews at <=200 ppm when the smoothed stamp error exceeds
    // 1 ms (clock drift), and an error beyond 50 ms starts a new segment.
    double place(const DsTimedMixBlock *block, bool &changed) {
        double stamp=block->first_sample_host_time*tickSeconds;
        double predicted=anchor+(double)(block->sample_position-anchorPosition)/block->sample_rate;
        double error=stamp-predicted;
        if(!changed && fabs(error)>0.05) changed=true;
        if(changed) { anchor=stamp; anchorPosition=block->sample_position; offset=0; blocks=1; return stamp; }
        if(++blocks<=32) { anchor+=error/blocks; }
        else {
            offset+=(error-offset)/64;
            if(fabs(offset)>0.001) {
                double limit=2e-4*block->frames/block->sample_rate, step=fmin(limit,fmax(-limit,offset));
                anchor+=step; offset-=step;
            }
        }
        return anchor+(double)(block->sample_position-anchorPosition)/block->sample_rate;
    }
    static void receive(void *context, const DsTimedMixBlock *block) {
        auto &a=*(ClipAudio *)context;
        if (!block->pcm51 || !block->sample_rate || !block->first_sample_host_time) { a.fault=true; return; }
        uint64_t w=a.rawWrite.load(std::memory_order_relaxed),r=a.rawRead.load(std::memory_order_acquire);
        uint64_t bw=a.blockWrite.load(std::memory_order_relaxed),br=a.blockRead.load(std::memory_order_acquire);
        uint64_t duration=(block->frames*1000000000ull+block->sample_rate-1)/block->sample_rate;
        if(block->frames>capacity-(w-r) || bw-br>=blockCapacity ||
           a.writeNS+duration-a.readNS.load(std::memory_order_acquire)>2000000000ull) {
            a.overflowFrames.fetch_add(block->frames,std::memory_order_relaxed); a.fault=true; return;
        }
        size_t first=std::min<size_t>(block->frames,capacity-w%capacity);
        memcpy(a.raw.get()+w%capacity*6,block->pcm51,first*6*sizeof(float));
        if(first<block->frames) memcpy(a.raw.get(),block->pcm51+first*6,(block->frames-first)*6*sizeof(float));
        a.writeNS+=duration;
        DsTimedMixBlock meta=*block; meta.pcm51=nullptr;
        a.rawBlocks[bw%blockCapacity]={meta,w,a.writeNS};
        a.maximumQueued.store(std::max(a.maximumQueued.load(std::memory_order_relaxed),w-r+block->frames),std::memory_order_relaxed);
        a.rawWrite.store(w+block->frames,std::memory_order_relaxed);
        a.blockWrite.store(bw+1,std::memory_order_release);
    }
    // Only the recorder thread touches clocks, fold-down and stereo points.
    void stage() {
        uint64_t br=blockRead.load(std::memory_order_relaxed),bw=blockWrite.load(std::memory_order_acquire);
        for(;br<bw;++br) {
            RawBlock block=rawBlocks[br%blockCapacity]; auto &meta=block.meta;
            uint64_t w=write.load(),r=read.load();
            if(w-r+meta.frames>capacity) { r=w+meta.frames-capacity; read.store(r); }
            bool changed=stream!=meta.stream_id || generation!=meta.clock_generation || rate!=meta.sample_rate || (meta.flags&DS_CAPTURE_DISCONTINUITY);
            double first=place(&meta,changed);
            if(changed) { stream=meta.stream_id; generation=meta.clock_generation; rate=meta.sample_rate; ++segment; }
            for(size_t i=0;i<meta.frames;i++) {
                float stereo[2]; media::fold_to_stereo(raw.get()+(block.first+i)%capacity*6,stereo,1);
                points[(w+i)%capacity]={first+(double)i/meta.sample_rate,stereo[0],stereo[1],segment};
            }
            if(changed) latestStart.store(w);
            write.store(w+meta.frames);
            readNS.store(block.endNS,std::memory_order_release);
            rawRead.store(block.first+meta.frames,std::memory_order_release);
            blockRead.store(br+1,std::memory_order_release);
        }
    }
    bool start() {
        subscribed=ds_capture_start(receive,this)==DS_OK;
        if (!subscribed) error=@"Audio capture subscription unavailable";
        return subscribed;
    }
    void stop() { if(subscribed) { ds_capture_stop(); subscribed=false; } }
    // Live joins at the recording's current sample: its packets carry the same timeline from there.
    void attachLive(std::shared_ptr<clip::Sink> sink) { liveSink=std::move(sink); liveAac.reset(); liveError=nullptr; }
    void detachLive() { liveSink.reset(); liveAac.reset(); }
    bool attach(clip::Fanout *outputs) {
        fanout=outputs;
        aac=std::make_unique<clip::AACEncoder>();
        if(aac->status) { error=[NSString stringWithFormat:@"AAC encoder unavailable (%d)",(int)aac->status]; return false; }
        return true;
    }
    CMAudioFormatDescriptionRef format() const { return aac?aac->format():nullptr; }
    // Before the first presentation retain one second of preroll. Setup may
    // take longer, but capture must span the first accepted presentation.
    void preroll() {
        uint64_t w=write.load(std::memory_order_acquire),r=read.load();
        if (w>r) {
            uint64_t latest=latestStart.load(std::memory_order_acquire);
            if(latest>r && latest<w) r=latest;
            double cutoff=points[(w-1)%capacity].time-1;
            while(r+1<w && points[(r+1)%capacity].time<cutoff) ++r;
            read.store(r,std::memory_order_release);
        }
    }
    bool covers(double time) {
        stage();
        uint64_t r=read.load(std::memory_order_relaxed),w=write.load(std::memory_order_acquire);
        if(r==w) return false;
        uint64_t latest=latestStart.load(std::memory_order_acquire);
        if(latest>r && latest<w && points[latest%capacity].time<=time) r=latest;
        return points[r%capacity].time<=time+1.0/48000 && points[(w-1)%capacity].time>=time-1.0/48000;
    }
    // Renders samples [emitted,target) of the presentation timeline from point `r` on; null, or why it failed.
    const char *render(double epoch,int64_t target,bool final,uint64_t &r,int64_t &emitted,uint64_t &silence,
                       clip::AACEncoder *aac,const std::function<void(clip::OwnedPacket)> &out) {
        while(emitted<target) {
            uint64_t w=write.load(std::memory_order_acquire);
            if (!w || w==r) return final?"Audio capture ended before video":nullptr;
            float pcm[1024*2]; unsigned n=0;
            while(n<1024 && emitted+n<target) {
                double t=epoch+(double)(emitted+n)/48000;
                uint64_t latest=latestStart.load(std::memory_order_acquire);
                if(latest>r && latest<w && points[latest%capacity].time<=t) r=latest;
                while(r+1<w && points[(r+1)%capacity].time<=t) ++r;
                Point a=points[r%capacity];
                if (a.time>t+1.0/48000 && emitted+n==0) return "Audio capture missed the first presentation";
                if(r+1>=w && t>a.time+1.0/48000) break;
                Point b=r+1<w?points[(r+1)%capacity]:a;
                double gap=b.time-a.time;
                float f=gap>0?fmin(1.0,fmax(0.0,(t-a.time)/gap)):0;
                if (gap>0.02 || (a.segment!=b.segment && gap>2.0/48000) || a.time>t+1.0/48000) { pcm[2*n]=pcm[2*n+1]=0; ++silence; }
                else { pcm[2*n]=a.l+(b.l-a.l)*f; pcm[2*n+1]=a.r+(b.r-a.r)*f; }
                ++n;
            }
            if(!n) return final?"Audio capture ended before video":nullptr;
            if(!aac || !aac->encode(pcm,n,false,out)) return "AAC audio encoding failed";
            emitted+=n;
        }
        return nullptr;
    }
    // Live starts at the recording's current sample, before the recording renders past it.
    void startLive() {
        if(!liveSink || liveError || liveAac) return;
        liveRead=read.load(std::memory_order_relaxed); liveEmitted=emitted;
        liveAac=std::make_unique<clip::AACEncoder>(liveEmitted);
        if(liveAac->status) liveError="aac";
    }
    // Live renders after the recording up to its own end (held frames extend it); it never moves the shared read.
    void pumpLive(double epoch, double end, bool final) {
        if(!liveSink || !liveAac || liveError) return;
        // stage() keeps only the newest `capacity` points; older ones are overwritten.
        uint64_t w=write.load(std::memory_order_acquire);
        if(w>capacity) liveRead=std::max(liveRead,w-capacity);
        auto sink=liveSink;
        auto out=[&](clip::OwnedPacket p){ sink->push(p); };
        if(render(epoch,(int64_t)llround(end*48000),false,liveRead,liveEmitted,liveSilence,liveAac.get(),out)) { liveError="audio"; return; }
        if(final && !liveAac->encode(nullptr,0,true,out)) liveError="aac";
        if(final) detachLive();
    }
    bool pump(double epoch, double end, bool final, double liveEnd=0) {
        if (fault.load()) { error=@"Audio capture queue overflow or invalid clock"; if(!final) return false; }
#ifdef CLIPREC_TESTING
        if(forcePressure && !final) return true;
#endif
        stage();
        if (!epoch) { preroll(); return true; }
        // stage() retains the full two-second ring while presentations pause.
        // Only samples beyond that bound are replaced; no early half-ring trim.
        int64_t target=(int64_t)llround(end*48000);
        startLive();
        uint64_t r=read.load(std::memory_order_relaxed);
        const char *failed=render(epoch,target,final,r,emitted,silence,aac.get(),[&](clip::OwnedPacket p){ fanout->publish(p); });
        read.store(r,std::memory_order_release);
        if(failed) { error=@(failed); return false; }
        pumpLive(epoch,fmax(end,liveEnd),final && emitted>=target);
        if(final && emitted>=target && aac && !aac->encode(nullptr,0,true,[&](clip::OwnedPacket p){ fanout->publish(p); })) {
            error=@"AAC audio flush failed"; return false;
        }
        return true;
    }
    bool drained(double end) const { return emitted>=(int64_t)llround(end*48000); }
};
#ifdef CLIPREC_TESTING
#ifndef CLIPREC_AUDIO_TEST
static bool testAudioEnabled=false;
#else
static bool testAudioEnabled=true;
#endif
#endif

@interface ClipFrame : NSObject {
@public
    CVPixelBufferRef pixel;
    CVMetalTextureRef y, uv;
    bool gpu, shown, delivered;
    double time;
}
@end
@implementation ClipFrame
- (void)dealloc {
    if (y) CFRelease(y);
    if (uv) CFRelease(uv);
    if (pixel) CFRelease(pixel);
}
@end

// Writer state belongs to the file consumer independently of capture. A
// timed-out I/O call can finish safely after the recording handle is retired.
struct ClipFile {
    AVAssetWriter *writer=nil;
    AVAssetWriterInput *input=nil,*audioInput=nil;
    NSString *temporary,*error=nil;
    CMAudioFormatDescriptionRef audioFormat=nullptr;
    double videoEnd=0;
    std::atomic<int64_t> closeAt{-1}, audioEnd{0};
    ClipFile(NSString *path,CMAudioFormatDescriptionRef format):temporary(path),audioFormat(format) {
        if(audioFormat) CFRetain(audioFormat);
    }
    ~ClipFile() {
        if(writer.status==AVAssetWriterStatusWriting) [writer cancelWriting];
        if(writer && writer.status!=AVAssetWriterStatusCompleted)
            [NSFileManager.defaultManager removeItemAtPath:temporary error:nil];
        if(audioFormat) CFRelease(audioFormat);
    }
};

@interface ClipSession : NSObject {
@public
    dispatch_queue_t work;
    dispatch_group_t finished;
    id<MTLDevice> device;
    id<MTLComputePipelineState> pipeline;
    CVPixelBufferPoolRef pool;
    CVMetalTextureCacheRef cache;
    VTCompressionSessionRef encoder;
    std::unique_ptr<live::Encoder> liveEncoder;
    std::unique_ptr<live::SegmentFeed> liveSegments;
    bool liveRequested;
    CVPixelBufferRef liveHold;          // the last picture sent to live (retained), repeated while no frame arrives
    double liveSent, liveSentWall, liveRealWall; // host time of the last live frame; wall times of the last any/real one
    int64_t liveSentPTS;                // last live PTS in 1/60000 s, -1 before the first
    uint64_t liveHeldFrames;
    double liveAudioEnd;                // end of the last held frame (s from epoch): sound keeps flowing to live
    std::atomic<bool> broadcast, fileRequested, replayRequested;
#ifdef CLIPREC_TESTING
    std::shared_ptr<clip::Sink> liveOutput;
#endif
    std::shared_ptr<ClipFile> file;
    clip::Fanout outputs;
    std::shared_ptr<clip::Sink> fileSink, replaySink;
    std::shared_ptr<replay::Ring> replayRing;
    std::atomic<bool> replayOnly;
    bool outputsClosed, fileNeedsIDR;
    std::atomic<unsigned> retiringFiles;
#ifdef CLIPREC_TESTING
    std::atomic<bool> stallFile, forceFileFailure;
#endif
    std::unique_ptr<ClipAudio> audio;
    double videoEnd;
    uint64_t audioQueuePeak, audioSilence, audioOverflow;
    bool finishing;
    NSString *temporary, *destination, *error, *codec;
    std::atomic<unsigned> outstanding, poolDrops, writerDrops, unshown, cadenceDrops, accepted, streak;
    std::atomic<bool> ready, stopping, packetOverflow, ended;
    std::atomic<unsigned> queuedPackets;
    bool preparing, finalized, writing, pumpScheduled, published;
    bool hostTimeFallback;   // NFS_TEST_CLIP_HOSTTIME=1: a frame Metal reports as not displayed uses its handler's host time
    unsigned width, height, resolution;
    int bitrate;
    double epoch, last, started, stopDeadline;
    std::atomic<double> replayEpoch;
    NSMutableSet<ClipFrame *> *frames;

#ifdef CLIPREC_TESTING
    bool forceEncoderPressure, forceHEVCFailure, forceNoHardware;
#endif
}
- (void)attachLive;
- (bool)pumpAudio:(double)from final:(bool)final;
- (void)detachLive;
- (void)resetLiveHold;
- (void)sendLive:(CVPixelBufferRef)pixel time:(double)t held:(bool)held;
- (void)holdLive;
- (void)attachFile;
- (void)retireFile;
- (void)retireFile:(std::shared_ptr<ClipFile>)old sink:(std::shared_ptr<clip::Sink>)sink target:(NSString *)target deadline:(double)deadline;
- (void)fail:(NSString *)message;
- (void)prepare:(unsigned)w height:(unsigned)h;
- (void)consume:(ClipFrame *)frame;
- (void)stop;
- (void)drain;
- (void)packet:(CMSampleBufferRef)sample status:(OSStatus)status;
- (void)pump;
- (clip::Result)writePacket:(const clip::OwnedPacket&)packet file:(const std::shared_ptr<ClipFile>&)file;
- (void)finishWhenDrained;
- (void)audioTick;
- (void)finish;
@end

static void compressed(void *context, void *, OSStatus status, VTEncodeInfoFlags flags, CMSampleBufferRef sample) {
    ClipSession *s = (__bridge ClipSession *)context;
    if (s->ended.load(std::memory_order_acquire)) return;
    // Bound only encoder-to-capture-thread transfer here. File sinks have
    // separate queues and never control this reservation.
    if (s->packetOverflow.load()) { ++s->writerDrops; return; }
    if (s->queuedPackets.fetch_add(1) >= kSurfaces) {
        --s->queuedPackets; ++s->writerDrops;
        if (!s->packetOverflow.exchange(true))
            dispatch_async(s->work, ^{ [s fail:@"Recording stopped: encoded packet queue overloaded"]; });
        return;
    }
    if (sample) CFRetain(sample);
    dispatch_async(s->work, ^{
        @autoreleasepool {
            if (flags & kVTEncodeInfo_FrameDropped) {
                ++s->writerDrops; --s->queuedPackets;
                if (++s->streak >= kOverload) [s fail:@"Recording stopped: encoder overloaded"];
            }
            else [s packet:sample status:status];
            if (sample) CFRelease(sample);
        }
    });
}

@implementation ClipSession
- (instancetype)init {
    if ((self = [super init])) {
        work = dispatch_queue_create("nfsmw.cliprec", DISPATCH_QUEUE_SERIAL);
        finished = dispatch_group_create();
        dispatch_group_enter(finished);
        frames = [NSMutableSet new];
        outstanding=0; poolDrops=0; writerDrops=0; unshown=0; cadenceDrops=0; accepted=0; streak=0;
        ready=false; stopping=false; packetOverflow=false; ended=false; queuedPackets=0;
        replayEpoch=0; broadcast=false; fileRequested=false; replayRequested=false; replayOnly=false; retiringFiles=0;
        resolution = CLIPREC_DEFAULT_RESOLUTION;
        liveRequested=live::enabled(); liveSentPTS=-1;
        started = NSProcessInfo.processInfo.systemUptime;
        const char *hosttime = getenv("NFS_TEST_CLIP_HOSTTIME");   // read when a recording starts
        hostTimeFallback = hosttime && !strcmp(hosttime,"1");
#ifndef CLIPREC_AUDIO_BASELINE
#ifdef CLIPREC_TESTING
        if (testAudioEnabled)
#endif
        {
            audio=std::make_unique<ClipAudio>();
            if (!audio->start()) error=audio->error;
            dispatch_async(work, ^{ [self audioTick]; });
        }
#endif
    }
    return self;
}
// The recording's sound runs to its video end only (exactly as without live); live's to its held frames' end.
- (bool)pumpAudio:(double)from final:(bool)final { return audio->pump(from,videoEnd,final,liveAudioEnd); }
- (void)audioTick {
    if(ended || !audio) return;
    if(error || ![self pumpAudio:writing?epoch:0 final:false]) [self fail:error ?: audio->error];
    [self pump];
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,10*NSEC_PER_MSEC),work,^{ [self audioTick]; });
}
- (void)dealloc {
    if (encoder) { VTCompressionSessionInvalidate(encoder); CFRelease(encoder); }
    if (cache) CFRelease(cache);
    if (pool) CFRelease(pool);
}
- (void)fail:(NSString *)message {
    if (ended) return;
    if (!error) error = [message copy];
    if(broadcast) { livestream_fail(LIVESTREAM_FAILURE_CAPTURE); liveState=4; broadcast=false; }
    [self stop];
}
- (void)prepare:(unsigned)w height:(unsigned)h {
    if (stopping) return;
    if (error) { [self fail:error]; return; }
    // Preserve the presented aspect, never enlarge; round down to even NV12 dimensions.
    const unsigned heights[] = {1080, 1440, 2160};
    double scale = fmin(1.0, (double)heights[resolution]/h);
    width = (unsigned)(w*scale) & ~1u; height = (unsigned)(h*scale) & ~1u;
    if (width < 2 || height < 2) { [self fail:@"Invalid capture size"]; return; }
    if (!device) { [self fail:@"Metal device unavailable"]; return; }
    NSError *e = nil;
    id<MTLLibrary> library = [device newLibraryWithSource:conversion options:nil error:&e];
    pipeline = [device newComputePipelineStateWithFunction:[library newFunctionWithName:@"nv12"] error:&e];
    if (!pipeline) { [self fail:[@"Metal conversion: " stringByAppendingString:e.localizedDescription ?: @"unavailable"]]; return; }
    NSDictionary *attrs = @{
        (id)kCVPixelBufferPixelFormatTypeKey:@(kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange),
        (id)kCVPixelBufferWidthKey:@(width), (id)kCVPixelBufferHeightKey:@(height),
        (id)kCVPixelBufferIOSurfacePropertiesKey:@{}, (id)kCVPixelBufferMetalCompatibilityKey:@YES};
    if (CVPixelBufferPoolCreate(NULL, (__bridge CFDictionaryRef)@{(id)kCVPixelBufferPoolMinimumBufferCountKey:@(kSurfaces)},
        (__bridge CFDictionaryRef)attrs, &pool) || CVMetalTextureCacheCreate(NULL, NULL, device, NULL, &cache)) {
        [self fail:@"Capture surface pool unavailable"]; return;
    }
    CVPixelBufferRef warm[kSurfaces] = {};
    bool warmed = true;
    for (unsigned i=0; i<kSurfaces; ++i) {
        if (CVPixelBufferPoolCreatePixelBuffer(NULL, pool, &warm[i])) warmed = false;
    }
    for (auto p : warm) if (p) CFRelease(p);
    if (!warmed) { [self fail:@"Capture surface allocation failed"]; return; }
    NSDictionary *hardware = @{(id)kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder:@YES};
    for (unsigned attempt=0; attempt<2; ++attempt) {
#ifdef CLIPREC_TESTING
        if (forceNoHardware || (!attempt && forceHEVCFailure)) continue;
#endif
        CMVideoCodecType type = attempt ? kCMVideoCodecType_H264 : kCMVideoCodecType_HEVC;
        OSStatus rc = VTCompressionSessionCreate(NULL,width,height,type,(__bridge CFDictionaryRef)hardware,
            (__bridge CFDictionaryRef)attrs,NULL,compressed,(__bridge void *)self,&encoder);
        if (rc || !encoder) continue;
        auto set = [&](CFStringRef key, id value) {
            return VTSessionSetProperty(encoder, key, (__bridge CFTypeRef)value) == noErr;
        };
        bool ok = set(kVTCompressionPropertyKey_ProfileLevel, attempt ? (id)kVTProfileLevel_H264_High_AutoLevel : (id)kVTProfileLevel_HEVC_Main_AutoLevel);
        ok &= set(kVTCompressionPropertyKey_RealTime,@YES);
        ok &= set(kVTCompressionPropertyKey_AllowFrameReordering,@NO);
        bitrate = (int)llround((attempt ? 60000000.0 : 40000000.0)*width*height/(3840.0*2160.0));
        ok &= set(kVTCompressionPropertyKey_AverageBitRate,@(bitrate));
        ok &= set(kVTCompressionPropertyKey_MaxKeyFrameIntervalDuration,@2);
        ok &= set(kVTCompressionPropertyKey_ExpectedFrameRate,@60);
        ok &= set(kVTCompressionPropertyKey_ColorPrimaries,(id)kCMFormatDescriptionColorPrimaries_ITU_R_709_2);
        // SDR presentation bytes are sRGB encoded; use the matching transfer tag.
        ok &= set(kVTCompressionPropertyKey_TransferFunction,(id)kCMFormatDescriptionTransferFunction_sRGB);
        ok &= set(kVTCompressionPropertyKey_YCbCrMatrix,(id)kCMFormatDescriptionYCbCrMatrix_ITU_R_709_2);
        if (ok && !VTCompressionSessionPrepareToEncodeFrames(encoder)) {
            CFTypeRef hw = NULL;
            VTSessionCopyProperty(encoder,kVTCompressionPropertyKey_UsingHardwareAcceleratedVideoEncoder,NULL,&hw);
            bool isHardware = hw && CFEqual(hw,kCFBooleanTrue);
            if (hw) CFRelease(hw);
            if (isHardware) { codec = attempt ? @"H.264" : @"HEVC"; break; }
        }
        VTCompressionSessionInvalidate(encoder); CFRelease(encoder); encoder = NULL;
    }
    if (!encoder) { [self fail:@"Hardware HEVC / H.264 encoder unavailable"]; return; }
    // Never change the recording session's codec/rate control for live output.
    // Only matching NV12 dimensions are shared; Encoder rejects any mismatch.
    [self attachLive];
    if(audio && !audio->attach(&outputs)) { [self fail:audio->error]; return; }
    if(!replayOnly) [self attachFile];
    if(!replayOnly || replayRing) replayRing=replay::attach(outputs,replayRing,&replaySink);
    if(replayRing) {
        replayRequested=replayRing->stats().armed;
        std::lock_guard<std::mutex> lock(control);
        if(current==self) {
            actionReplay=replayRing;
            if(!replayExport) replayExport=std::make_unique<replay::Export>();
        }
    }
    ready.store(true, std::memory_order_release);

}
- (void)attachLive {
    if(liveEncoder || !width || stopping) return;
    [self resetLiveHold];
    if(liveRequested || broadcast) {
        liveSegments=live::SegmentFeed::create(true,{}
#ifdef CLIPREC_TESTING
            ,liveOutput
#endif
        );
        if(audio) audio->attachLive(liveSegments->audio);
        liveEncoder=std::make_unique<live::Encoder>(live::Profile::youtube(width,height),liveSegments->video);
        if(liveEncoder->status()) fprintf(stderr,"live encode: unavailable (%d); recording continues\n",(int)liveEncoder->status());
    }
    if(broadcast) {
        if(!liveEncoder || liveEncoder->status()) {
            livestream_fail_reason(LIVESTREAM_FAILURE_ENCODER,liveEncoder?(int)liveEncoder->status():0,"setup"); [self detachLive]; liveState=4;
        } else liveState=2;
    }
}
- (void)resetLiveHold {
    if(liveHold) CVPixelBufferRelease(liveHold);
    liveHold=NULL; liveSent=liveSentWall=liveRealWall=liveAudioEnd=0; liveSentPTS=-1;
}
// Live only; the recording keeps its gaps on the host timeline.
- (void)sendLive:(CVPixelBufferRef)pixel time:(double)t held:(bool)held {
    if(!liveEncoder) return;
    int64_t pts=llround((t-epoch)*60000);
    // A frame presented before the last held one is already covered by it.
    if(liveSentPTS>=0 && pts<=liveSentPTS) return;
    liveEncoder->encode(pixel,CMTimeMake(pts,60000),CMTimeMake(1,held?30:60));
    double now=clipNow();
    liveSent=t; liveSentPTS=pts; liveSentWall=now;
    // Live's sound runs to the video end; held frames extend it so the segmenter's audio watermark keeps up.
    if(held) { ++liveHeldFrames; liveAudioEnd=fmax(liveAudioEnd,t-epoch+1.0/30); return; }
    liveRealWall=now;
    if(liveHold!=pixel) { CVPixelBufferRetain(pixel); if(liveHold) CVPixelBufferRelease(liveHold); liveHold=pixel; }
}
- (void)holdLive {
    if(!liveEncoder || !liveHold || liveEncoder->status() || liveEncoder->pending() || !liveHoldEnabled()) return;
    double now=clipNow();
    // The tolerance absorbs pump timer jitter around the step.
    if(now<=0 || now-liveRealWall<kLiveHoldAfter || now-liveSentWall<kLiveHoldStep-0.001) return;
    [self sendLive:liveHold time:liveSent+(now-liveSentWall) held:true];
}
- (void)detachLive {
    if(liveEncoder) liveEncoder->complete();
    liveEncoder.reset();
    [self resetLiveHold];
    if(audio) audio->detachLive();
    if(liveSegments) {
        liveSegments->audio->cancel();
        liveSegments->video->cancel();
        liveSegments.reset();
    }
    broadcast=false;
    if(!fileRequested && !replayRequested) [self stop];
}
- (void)attachFile {
    fileNeedsIDR=true;
    NSString *folder = outputFolder ?: [NSHomeDirectory() stringByAppendingPathComponent:@"Movies/NFSMW"];
    NSDateFormatter *date = [NSDateFormatter new]; date.locale = [NSLocale localeWithLocaleIdentifier:@"en_US_POSIX"];
    date.dateFormat = @"yyyyMMdd-HHmmss";
    NSString *name = [NSString stringWithFormat:@"Clip-%@-%@",[date stringFromDate:NSDate.date],NSUUID.UUID.UUIDString];
    destination = [folder stringByAppendingPathComponent:[name stringByAppendingString:@".mp4"]];
    temporary = [folder stringByAppendingPathComponent:[@"." stringByAppendingString:[name stringByAppendingString:@".tmp.mp4"]]];
    file=std::make_shared<ClipFile>(temporary,audio?audio->format():nullptr);
    auto destinationFile=file;
    __weak ClipSession *weak=self;
    fileSink=std::make_shared<clip::Sink>([weak,destinationFile](const clip::OwnedPacket& p) {
        ClipSession *session=weak;
        return session?[session writePacket:p file:destinationFile]:clip::Result::failed;
    });
    outputs.add(fileSink);

}
- (void)retireFile {
    auto old=file; auto sink=fileSink;
    NSString *target=destination;
    replayOnly=true;
    if(!old || !sink) { --retiringFiles; return; }
    // Include already submitted video; keep receiving the shared AAC encoder's tail.
    old->closeAt=llround(fmax(videoEnd,last?last-epoch+1.0/60:0)*60000);
    file.reset(); fileSink.reset();
    auto deadline=NSProcessInfo.processInfo.systemUptime+6;
    [self retireFile:old sink:sink target:target deadline:deadline];
}
- (void)retireFile:(std::shared_ptr<ClipFile>)old sink:(std::shared_ptr<clip::Sink>)sink
    target:(NSString *)target deadline:(double)deadline {
    if(old->audioFormat && old->audioEnd.load()<old->closeAt.load() && !sink->failed() &&
       NSProcessInfo.processInfo.systemUptime<deadline) {
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_MSEC),work,^{
            [self retireFile:old sink:sink target:target deadline:deadline];
        }); return;
    }
    sink->close();
    if(!sink->drained() && NSProcessInfo.processInfo.systemUptime<deadline) {
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_MSEC),work,^{
            [self retireFile:old sink:sink target:target deadline:deadline];
        }); return;
    }
    const bool drained=sink->drained();
    const bool clean=!sink->failed();
    outputs.remove(sink);
    if(!drained) { --retiringFiles; return; } // in-flight call owns cleanup
    auto writer=old->writer;
    if(clean && writer.status==AVAssetWriterStatusWriting) {
        [writer endSessionAtSourceTime:CMTimeMakeWithSeconds(old->videoEnd,60000)];
        [old->input markAsFinished]; if(old->audioInput) [old->audioInput markAsFinished];
        [writer finishWritingWithCompletionHandler:^{
            if(writer.status==AVAssetWriterStatusCompleted &&
               [NSFileManager.defaultManager moveItemAtPath:old->temporary toPath:target error:nil]) {
                dispatch_async(dispatch_get_main_queue(),^{portmenu_status("Clip saved: %s",target.lastPathComponent.UTF8String);});
            }
            --self->retiringFiles;
        }];
    } else { [writer cancelWriting]; --retiringFiles; }
}
- (void)packet:(CMSampleBufferRef)sample status:(OSStatus)status {
    --queuedPackets;
    if (ended || finishing) return;
    if (status || !sample || !CMSampleBufferDataIsReady(sample)) {
        [self fail:[NSString stringWithFormat:@"Video encoder error %d",(int)status]]; return;
    }
    writing=true; streak=0;
    CMTime duration=CMSampleBufferGetDuration(sample);
    if(!CMTIME_IS_NUMERIC(duration) || CMTimeGetSeconds(duration)<=0) duration=CMTimeMake(1,60);
    videoEnd=fmax(videoEnd,CMTimeGetSeconds(CMTimeAdd(CMSampleBufferGetPresentationTimeStamp(sample),duration)));
    outputs.publish(std::make_shared<clip::Packet>(clip::Track::video,sample));
    if(audio && ![self pumpAudio:epoch final:false]) [self fail:audio->error];
    [self pump];
}
// Called exclusively by the file sink's thread. No file I/O, writer readiness
// or file failure can hold the capture queue or another packet consumer.
- (clip::Result)writePacket:(const clip::OwnedPacket&)p file:(const std::shared_ptr<ClipFile>&)destinationFile {
    auto &writer=destinationFile->writer;
    auto &input=destinationFile->input;
    auto &audioInput=destinationFile->audioInput;
    auto &fileError=destinationFile->error;
    NSString *filePath=destinationFile->temporary;
#ifdef CLIPREC_TESTING
    if(stallFile.load()) return clip::Result::retry;
    if(forceFileFailure.load()) return clip::Result::failed;
#endif
    int64_t closeAt=destinationFile->closeAt.load();
    if(p->track==clip::Track::video && closeAt>=0 &&
       CMTimeCompare(CMSampleBufferGetPresentationTimeStamp(p->sample()),CMTimeMake(closeAt,60000))>=0)
        return clip::Result::consumed;
    if(!writer || writer.status==AVAssetWriterStatusUnknown) {
        if(p->track!=clip::Track::video) return clip::Result::retry;
        NSError *e=nil;
        NSString *folder=filePath.stringByDeletingLastPathComponent;
        if(![NSFileManager.defaultManager createDirectoryAtPath:folder withIntermediateDirectories:YES attributes:nil error:&e]) {
            fileError=[@"Output folder: " stringByAppendingString:e.localizedDescription]; return clip::Result::failed;
        }
        writer=[[AVAssetWriter alloc] initWithURL:[NSURL fileURLWithPath:filePath] fileType:AVFileTypeMPEG4 error:&e];
        if(!writer) { fileError=[@"MP4 writer: " stringByAppendingString:e.localizedDescription]; return clip::Result::failed; }
        input=[AVAssetWriterInput assetWriterInputWithMediaType:AVMediaTypeVideo outputSettings:nil sourceFormatHint:p->format()];
        input.mediaTimeScale=60000; input.expectsMediaDataInRealTime=YES;
        if(![writer canAddInput:input]) return clip::Result::failed;
        [writer addInput:input];
        if(destinationFile->audioFormat) {
            audioInput=[AVAssetWriterInput assetWriterInputWithMediaType:AVMediaTypeAudio outputSettings:nil sourceFormatHint:destinationFile->audioFormat];
            audioInput.expectsMediaDataInRealTime=YES;
            if(![writer canAddInput:audioInput]) return clip::Result::failed;
            [writer addInput:audioInput];
        }
        if(![writer startWriting]) return clip::Result::failed;
        [writer startSessionAtSourceTime:CMSampleBufferGetPresentationTimeStamp(p->sample())];
    }
    if(writer.status!=AVAssetWriterStatusWriting) return clip::Result::failed;
    AVAssetWriterInput *track=p->track==clip::Track::video?input:audioInput;
    if(!track.readyForMoreMediaData) return clip::Result::retry;
    if(![track appendSampleBuffer:p->sample()]) return clip::Result::failed;
    if(p->track==clip::Track::video) {
        ++accepted;
        CMTime duration=CMSampleBufferGetDuration(p->sample());
        if(!CMTIME_IS_NUMERIC(duration) || CMTimeGetSeconds(duration)<=0) duration=CMTimeMake(1,60);
        destinationFile->videoEnd=fmax(destinationFile->videoEnd,
            CMTimeGetSeconds(CMTimeAdd(CMSampleBufferGetPresentationTimeStamp(p->sample()),duration)));
    } else {
        CMTime end=CMTimeAdd(CMSampleBufferGetPresentationTimeStamp(p->sample()),CMSampleBufferGetDuration(p->sample()));
        if(CMTIME_IS_NUMERIC(end)) destinationFile->audioEnd=CMTimeConvertScale(end,60000,kCMTimeRoundingMethod_RoundHalfAwayFromZero).value;
    }
    return clip::Result::consumed;
}
- (void)pump {
    if(broadcast && ready && liveState==2) [self holdLive];
    // Live's own sound failed (its AAC encoder or a missed start): end live only.
    if(broadcast && ready && liveState==2 && audio && audio->liveError) {
        livestream_fail_reason(LIVESTREAM_FAILURE_ENCODER,0,audio->liveError);
        [self detachLive]; liveState=4;
    }
    if(broadcast && ready && liveState==2 && (livestream_status()!=1 || !liveEncoder || liveEncoder->status() ||
       !liveSegments || liveSegments->stats().failed)) {
        int status=liveEncoder?(int)liveEncoder->status():0;
        // A failed segmenter also refuses the encoder's packets (status -12915): name the upstream cause first.
        const char *seg=liveSegments && liveSegments->stats().failed?liveSegments->stats().reason:nullptr;
        const char *reason=seg?seg:status?"status":!liveEncoder?"detached":!liveSegments?"no-segmenter":nullptr;
        if(livestream_status()==1) livestream_fail_reason(LIVESTREAM_FAILURE_ENCODER,status,reason?reason:"segmenter");
        else livestream_stop();
        [self detachLive]; liveState=4;
    }
    // A failed file sink must not stop capture while another sink (or live, outside `outputs`) is healthy.
    if(fileSink && fileSink->failed() && !outputs.healthy() && !(liveSegments && !liveSegments->video->failed())) {
        [self fail:@"Recording stopped: file packet sink failed"];
    }
    if(!ended && !finishing && !pumpScheduled) {
        pumpScheduled=true;
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_MSEC),work,^{ self->pumpScheduled=false; [self pump]; });
    }
}

- (void)consume:(ClipFrame *)f {
    if (f->delivered) return;
    if (!f->gpu || (!f->shown && !finalized)) return;
    f->delivered = true;
    [frames removeObject:f];
    --outstanding;
    if (finalized || !f->shown || f->time <= 0) { ++unshown; return; }
    if (!epoch) {
        // Queued output may place the first captured PCM in the future. Begin
        // video only when the tap spans a presentation, without shifting sound.
        if(audio && !audio->covers(f->time)) {
            ++unshown;
            if(NSProcessInfo.processInfo.systemUptime-started>2)
                [self fail:@"Audio capture did not cover a presented frame"];
            return;
        }
        epoch = f->time;
        replayEpoch.store(epoch,std::memory_order_release);
    }
    // At most 60 fps; keep gaps on the host timeline instead of compacting drops.
    if (last && f->time-last < 1.0/60.0-0.0001) { ++cadenceDrops; return; }
    if (f->time < epoch || (last && f->time <= last)) { ++unshown; return; }
    // Encoder transfer pressure is independent of every packet sink.
    bool backpressure = packetOverflow.load();
#ifdef CLIPREC_TESTING
    backpressure |= forceEncoderPressure;
#endif
    if (backpressure) {
        ++writerDrops;
        if (++streak >= kOverload) [self fail:@"Recording stopped: encoder transfer overloaded"];
        return;
    }
    last = f->time;
    // Round: CMTimeMakeWithSeconds truncates, so a frame on the 1/60 s grid
    // could land one tick early and shorten videoEnd and the audio track.
    CMTime pts = CMTimeMake(llround((f->time-epoch)*60000),60000);
    VTEncodeInfoFlags flags;
    NSDictionary *frameOptions=fileNeedsIDR?@{(id)kVTEncodeFrameOptionKey_ForceKeyFrame:@YES}:nil;
    fileNeedsIDR=false;
    OSStatus rc = VTCompressionSessionEncodeFrame(encoder,f->pixel,pts,CMTimeMake(1,60),(__bridge CFDictionaryRef)frameOptions,NULL,&flags);
    [self sendLive:f->pixel time:f->time held:false];
    if (rc) [self fail:[NSString stringWithFormat:@"Video encode submission error %d",(int)rc]];
}
- (void)stop {
    if (stopping.exchange(true)) return;
    deactivate(self);
    stopDeadline = NSProcessInfo.processInfo.systemUptime + 2;
    [self drain];
}
- (void)drain {
    if (finalized) return;
    if (outstanding && NSProcessInfo.processInfo.systemUptime < stopDeadline) {
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,10*NSEC_PER_MSEC),work,^{ [self drain]; }); return;
    }
    finalized = true;
    // Missing presentation notifications cannot hold finalisation indefinitely.
    // GPU handlers still retain frames/surfaces until GPU completion.
    for (ClipFrame *f in frames.allObjects) [self consume:f];
    [frames removeAllObjects];
    if (encoder) {
        OSStatus rc = VTCompressionSessionCompleteFrames(encoder,kCMTimeInvalid);
        if (rc && !error) error = [NSString stringWithFormat:@"Video encoder flush error %d",(int)rc];
    }
    if(liveEncoder) liveEncoder->complete();
    // VT callbacks enqueue packets ahead of this barrier on the same work queue.
    dispatch_async(work, ^{ [self finishWhenDrained]; });
}
- (void)finishWhenDrained {
    if (finishing || ended) return;
    if(audio) {
        audio->stop();
        bool drainedOK=!writing || [self pumpAudio:epoch final:true];
        if(!drainedOK && !error) error=audio->error;
        if(writing && drainedOK && !audio->drained(videoEnd)) {
            if(NSProcessInfo.processInfo.systemUptime<stopDeadline+2) {
                dispatch_after(dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_MSEC),work,^{ [self finishWhenDrained]; }); return;
            }
            if(!error) error=@"AAC audio writer stalled";
        }

    }
    if(retiringFiles && NSProcessInfo.processInfo.systemUptime<stopDeadline+6) {
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_MSEC),work,^{ [self finishWhenDrained]; }); return;
    }
    if(!outputsClosed) { outputs.close(); outputsClosed=true; }
    if(!outputs.drained()) {
        if(NSProcessInfo.processInfo.systemUptime<stopDeadline+6) {
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_MSEC),work,^{ [self finishWhenDrained]; }); return;
        }
        outputs.cancel();
        if(!error) error=@"Packet sink drain timed out";
        // Release the publisher's references even if a consumer remains in I/O.
        // A blocked file call owns its writer state; do not inspect or cancel it
        // concurrently. Its final reference cleans up the unpublished file.
        if(fileSink && !fileSink->drained()) { fileSink.reset(); file.reset(); }
    }
    if(liveSegments && !liveSegments->drained() && NSProcessInfo.processInfo.systemUptime<stopDeadline+6) {
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_MSEC),work,^{ [self finishWhenDrained]; }); return;
    }
    if(liveSegments) {
        bool ok=liveSegments->finish();
        auto stats=liveSegments->stats();
        fprintf(stderr,"live segments: %llu segments, video %llu, audio %llu; non-monotonic DTS %llu; pending peak %zu bytes, held peak %zu; status %d\n",
            (unsigned long long)stats.segments,(unsigned long long)stats.video,(unsigned long long)stats.audio,
            (unsigned long long)stats.nonMonotonic,stats.pendingPeak,stats.heldPeak,ok?0:1);
        liveSegments.reset();
    }
    AVAssetWriter *writer=file?file->writer:nil;
    // Only inspect writer-owned metadata after the sink's in-flight call retires.
    if(fileSink && fileSink->failed() && (!error || [error isEqualToString:@"Recording stopped: file packet sink failed"]))
        error=file->error ?: writer.error.localizedDescription ?: @"File packet sink failed";
    finishing=true;
    if (writing && writer.status == AVAssetWriterStatusWriting) {
        [writer endSessionAtSourceTime:CMTimeMakeWithSeconds(file->videoEnd,60000)];
        [file->input markAsFinished];
        if(file->audioInput) [file->audioInput markAsFinished];
        [writer finishWritingWithCompletionHandler:^{ dispatch_async(self->work, ^{ [self finish]; }); }];
    } else if(!replayOnly) {
        if (!error) error = @"No presented video frames";
        [writer cancelWriting]; [self finish];
    } else [self finish];
}

- (void)finish {
    if (ended) return;
    AVAssetWriter *writer=file?file->writer:nil;
    if (writer.status == AVAssetWriterStatusCompleted) {
        NSError *e = nil;
        // moveItem refuses an existing destination; temporary is on the same volume.
        if (![NSFileManager.defaultManager moveItemAtPath:temporary toPath:destination error:&e])
            error = e.localizedDescription ?: @"Could not publish clip";
        else {
            published=true;
            if(!error) {
            NSString *name = destination.lastPathComponent;
            dispatch_async(dispatch_get_main_queue(), ^{ portmenu_status("Clip saved: %s", name.UTF8String); });
            } else {
                NSString *reason=error;
                dispatch_async(dispatch_get_main_queue(), ^{ portmenu_status("Clip stopped: %s", reason.UTF8String); });
            }
        }
    } else if (!error && !replayOnly) error = writer.error.localizedDescription ?: @"MP4 finalization failed";
    if (file && writer.status != AVAssetWriterStatusCompleted && temporary)
        [NSFileManager.defaultManager removeItemAtPath:temporary error:nil];
    // One line per clip (the menu row and status toast are not in logs): outcome, span, frame accounting.
    if(!replayOnly) fprintf(stderr,"cliprec: %s %s video %.3f s, %u frames, drops %u/%u/%u/%u%s%s\n",published?"saved":"not saved",
        published?destination.fileSystemRepresentation:"-",writing?videoEnd:0.0,accepted.load(),poolDrops.load(),
        writerDrops.load(),unshown.load(),cadenceDrops.load(),error?"; error: ":"",error?error.UTF8String:"");
    else if(error) fprintf(stderr,"replay: not saved - video %.6f s, snapshot 0 bytes; error: %s\n",videoEnd,error.UTF8String);
    // CompleteFrames and the packet barrier have retired all encoder callbacks.
    // Keep terminal metadata for the menu, but no idle capture/encoding resources.
    if (encoder) { VTCompressionSessionInvalidate(encoder); CFRelease(encoder); encoder=NULL; }
    if(liveEncoder) {
        fprintf(stderr,"live encode: H.264 %ux%u CBR %d; %llu frames (%llu held), %llu bytes, pending %u, status %d\n",
            width,height,liveEncoder->profile().bitrate,(unsigned long long)liveEncoder->frames(),
            (unsigned long long)liveHeldFrames,(unsigned long long)liveEncoder->bytes(),liveEncoder->pending(),(int)liveEncoder->status());
        liveEncoder.reset();
    }
    [self resetLiveHold];
    if (cache) { CVMetalTextureCacheFlush(cache,0); CFRelease(cache); cache=NULL; }
    if (pool) { CFRelease(pool); pool=NULL; }
    if(audio) { audio->stop(); audioQueuePeak=audio->maximumQueued.load(); audioSilence=audio->silence; audioOverflow=audio->overflowFrames.load(); audio.reset(); }
    pipeline=nil; device=nil;
    if(file) { file->input=nil; file->audioInput=nil; file->writer=nil; file.reset(); }
    [frames removeAllObjects];
    ready = false; ended.store(true,std::memory_order_release);
    if(liveState==3) liveState=0;
    if(replayRing) replayRing->disarm();
    dispatch_group_leave(finished);
}
@end

static void dropped(ClipSession *s) {
    ++s->poolDrops;
    if (++s->streak == kOverload) {
        deactivate(s);
        dispatch_async(s->work, ^{ [s fail:@"Recording stopped: capture overloaded"]; });
    }
}

// Keep ARC's parameter retains and the large capture stack frame behind the
// inactive check. The caller owns all three objects throughout this call.
static __attribute__((noinline)) void present_active(id<MTLCommandBuffer> cb, id<MTLTexture> src, id<CAMetalDrawable> drawable, float headroom) {
    ClipSession *s;
    { std::lock_guard<std::mutex> lock(control); s = current; }
    if (!s || s->stopping) return;
    if (src.pixelFormat != MTLPixelFormatBGRA8Unorm && src.pixelFormat != MTLPixelFormatRGBA8Unorm &&
        src.pixelFormat != MTLPixelFormatBGRA8Unorm_sRGB && src.pixelFormat != MTLPixelFormatRGBA8Unorm_sRGB &&
        src.pixelFormat != MTLPixelFormatRGBA16Float) {
        deactivate(s);
        dispatch_async(s->work, ^{ [s fail:@"Recording source pixel format is unsupported"]; }); return;
    }
    if (!s->ready.load(std::memory_order_acquire)) {
        // Only the serial presenter writes preparing.
        if (!s->preparing) {
            s->preparing = true;
            s->device = cb.device;
            unsigned w = (unsigned)drawable.texture.width, h = (unsigned)drawable.texture.height;
            dispatch_async(s->work, ^{ @autoreleasepool { [s prepare:w height:h]; } });
        }
        return;
    }
    if (s->outstanding.fetch_add(1) >= kSurfaces) { --s->outstanding; dropped(s); return; }
    ClipFrame *f = [ClipFrame new];
    CVReturn rc = CVPixelBufferPoolCreatePixelBufferWithAuxAttributes(NULL,s->pool,
        (__bridge CFDictionaryRef)@{(id)kCVPixelBufferPoolAllocationThresholdKey:@(kSurfaces)},&f->pixel);
    if (rc) { --s->outstanding; dropped(s); return; }
    CVBufferSetAttachment(f->pixel,kCVImageBufferColorPrimariesKey,kCVImageBufferColorPrimaries_ITU_R_709_2,kCVAttachmentMode_ShouldPropagate);
    CVBufferSetAttachment(f->pixel,kCVImageBufferTransferFunctionKey,kCVImageBufferTransferFunction_sRGB,kCVAttachmentMode_ShouldPropagate);
    CVBufferSetAttachment(f->pixel,kCVImageBufferYCbCrMatrixKey,kCVImageBufferYCbCrMatrix_ITU_R_709_2,kCVAttachmentMode_ShouldPropagate);
    CVBufferSetAttachment(f->pixel,kCVImageBufferChromaLocationTopFieldKey,kCVImageBufferChromaLocation_Center,kCVAttachmentMode_ShouldPropagate);
    rc = CVMetalTextureCacheCreateTextureFromImage(NULL,s->cache,f->pixel,NULL,MTLPixelFormatR8Unorm,s->width,s->height,0,&f->y);
    if (!rc) rc = CVMetalTextureCacheCreateTextureFromImage(NULL,s->cache,f->pixel,NULL,MTLPixelFormatRG8Unorm,s->width/2,s->height/2,1,&f->uv);
    id<MTLComputeCommandEncoder> enc = rc ? nil : [cb computeCommandEncoder];
    if (!enc) {
        --s->outstanding;
        dispatch_async(s->work, ^{ [s fail:@"Capture Metal surface mapping failed"]; }); return;
    }
    float transport[2] = {src.pixelFormat==MTLPixelFormatRGBA16Float ? 1.0f : 0.0f, fmaxf(1.0f,headroom)};
    [enc setBytes:transport length:sizeof transport atIndex:0];
    [enc setComputePipelineState:s->pipeline];
    [enc setTexture:src atIndex:0];
    [enc setTexture:CVMetalTextureGetTexture(f->y) atIndex:1];
    [enc setTexture:CVMetalTextureGetTexture(f->uv) atIndex:2];
    [enc dispatchThreads:MTLSizeMake(s->width/2,s->height/2,1) threadsPerThreadgroup:MTLSizeMake(8,8,1)];
    [enc endEncoding];
    dispatch_async(s->work, ^{ [s->frames addObject:f]; });
    [cb addCompletedHandler:^(id<MTLCommandBuffer> done) {
        dispatch_async(s->work, ^{
            f->gpu = true;
            if (done.status == MTLCommandBufferStatusError) { f->shown = true; f->time = 0; }
            [s consume:f];
        });
    }];
    [drawable addPresentedHandler:^(id<MTLDrawable> shown) {
        double t = shown.presentedTime;
        // Unattended test runs keep the window behind others, where every drawable reports presentedTime 0 and
        // no frame would be recorded; the test switch stamps such a frame with the time its handler ran.
        if (t <= 0 && s->hostTimeFallback) t = CACurrentMediaTime();
        dispatch_async(s->work, ^{ f->shown = true; f->time = t; [s consume:f]; });
    }];
}

void cliprec_present(id<MTLCommandBuffer> __unsafe_unretained cb, id<MTLTexture> __unsafe_unretained src, id<CAMetalDrawable> __unsafe_unretained drawable, float headroom) {
    if (!active.load(std::memory_order_acquire)) return;
    present_active(cb,src,drawable,headroom);
}

void cliprec_stop(void) {
    ClipSession *s;
    { std::lock_guard<std::mutex> lock(control); s = current; }
    if (s) { deactivate(s); dispatch_async(s->work, ^{ [s stop]; }); }
}
void cliprec_shutdown(void) {
    ClipSession *s;
    { std::lock_guard<std::mutex> lock(control); s = current; }
    if (!s) { if(replayExport) replayExport->wait(5); return; }
    // Live Stop clears broadcast before its queued detach (state 3) completes.
    if (s->replayOnly && !s->fileRequested && !s->retiringFiles &&
        !s->broadcast && !s->liveRequested && liveState.load()!=3 &&
        (!replayExport || !replayExport->busy())) {
        // Nothing in capture was requested as a file. Leave its resources to
        // process teardown: even asynchronous stop would flush the encoder and
        // finalize sinks for a buffer that is being discarded. Pending callbacks
        // retain their session/surfaces until the process ends.
        deactivate(s);
        s->stopping=true;
        return;
    }
    livestream_stop(); liveState=0;
    cliprec_stop();
    // Preserve requested files within the existing quit bound. A replay save
    // may still need the encoder's pending packets to complete its snapshot.
    if (dispatch_group_wait(s->finished,dispatch_time(DISPATCH_TIME_NOW,10*NSEC_PER_SEC)))
        fprintf(stderr,"cliprec: finalization timed out; unpublished temporary clip retained\n");
    if(replayExport) replayExport->wait(5);
}
int cliprec_live_start(void) {
    std::lock_guard<std::mutex> lock(control);
    if(liveState==1 || liveState==2 || liveState==3) return 0;
    ClipSession *s=current;
    if(s && !s->ended && (!active || s->stopping)) return 0;
    if(!s || s->ended) {
        static dispatch_once_t once;
        dispatch_once(&once, ^{ atexit(cliprec_shutdown); });
        current=[ClipSession new]; s=current; s->replayOnly=true;
        int resolution=settings_get("display.clip_resolution");
        s->resolution=resolution>=0 && resolution<=2?(unsigned)resolution:CLIPREC_DEFAULT_RESOLUTION;
    }
    s->broadcast=true; liveState=1; active=true;
    dispatch_async(s->work, ^{
        // Replace any local diagnostic with a fresh IDR/segment generation.
        if(s->liveEncoder) {
            s->liveEncoder->complete(); s->liveEncoder.reset(); [s resetLiveHold];
            if(s->audio) s->audio->detachLive();
            if(s->liveSegments) {
                s->liveSegments->audio->cancel();
                s->liveSegments->video->cancel(); s->liveSegments.reset();
            }
        }
        if(s->broadcast && !s->stopping && s->ready) [s attachLive];
    });
    return 1;
}
void cliprec_live_stop(void) {
    std::lock_guard<std::mutex> lock(control);
    ClipSession *s=current;
    if(!s || !s->broadcast) { if(liveState!=3) liveState=0; return; }
    liveState=3; s->broadcast=false;
    dispatch_async(s->work, ^{ [s detachLive]; if(!s->stopping) liveState=0; });
}
int cliprec_live_state(void) { return liveState.load(); }
void replay_arm_action(int unused) {
    (void)unused;
    std::lock_guard<std::mutex> lock(control);
    ClipSession *s=current;
    if(actionReplay && actionReplay->stats().armed) {
        actionReplay->disarm();
        if(s) s->replayRequested=false;
        if(s) dispatch_async(s->work, ^{
            s->outputs.remove(s->replaySink);
            s->replaySink.reset();
        });
        if(s && !s->fileRequested && !s->broadcast) {
            active.store(false,std::memory_order_release);
            dispatch_async(s->work, ^{ [s stop]; });
        }
        return;
    }
    if(s && (!active.load(std::memory_order_acquire) || s->stopping) && !s->ended) return;
    if(!replayExport) replayExport=std::make_unique<replay::Export>();
    if(s && !s->ended && !s->stopping) {
        // Attach on the capture queue, after any pending Disarm removal.
        if(!actionReplay) actionReplay=std::make_shared<replay::Ring>();
        auto ring=actionReplay; ring->arm();
        s->replayRequested=true;
        dispatch_async(s->work, ^{
            if(s->replayRing!=ring) {
                if(s->replayRing) s->replayRing->disarm();
                s->outputs.remove(s->replaySink);
                s->replaySink.reset();
                s->replayRing=ring;
            }
            if(s->ready && !s->replaySink && ring->stats().armed)
                replay::attach(s->outputs,ring,&s->replaySink);
        });
        return;
    }
    static dispatch_once_t once;
    dispatch_once(&once, ^{ atexit(cliprec_shutdown); });
    actionReplay=std::make_shared<replay::Ring>(); actionReplay->arm();
    current=[ClipSession new]; current->replayOnly=true; current->replayRequested=true; current->replayRing=actionReplay;
    int resolution=settings_get("display.clip_resolution");
    current->resolution=resolution>=0 && resolution<=2?(unsigned)resolution:CLIPREC_DEFAULT_RESOLUTION;
    active.store(true,std::memory_order_release);
}
static void replay_notice(replay::Export *exporter) {
    if(exporter->busy()) {
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,100*NSEC_PER_MSEC),dispatch_get_main_queue(),^{replay_notice(exporter);});
    } else portmenu_status("%s",exporter->status().c_str());
}
void replay_save_action(int unused) {
    (void)unused;
    double time=CACurrentMediaTime(); // action time, before encoder queue delay
    std::lock_guard<std::mutex> lock(control);
    ClipSession *s=current;
    if(!s || !actionReplay || !replayExport || !actionReplay->stats().armed) {
        fprintf(stderr,"replay: not saved - video 0 s, snapshot 0 bytes; error: Arm replay first\n");
        dispatch_async(dispatch_get_main_queue(),^{portmenu_status("Arm replay first");}); return;
    }
    auto ring=actionReplay;
    auto exporter=replayExport.get();
    NSString *folder=outputFolder ?: [NSHomeDirectory() stringByAppendingPathComponent:@"Movies/NFSMW"];
    double epoch=s->replayEpoch.load(std::memory_order_acquire);
    CMTime end=epoch?CMTimeMake(llround((time-epoch)*60000),60000):kCMTimeInvalid;
    if(exporter->save(ring,end,folder.fileSystemRepresentation)) {
        dispatch_async(dispatch_get_main_queue(),^{portmenu_status("Saving replay..."); replay_notice(exporter);});
    } else if(!exporter->busy()) {
        dispatch_async(dispatch_get_main_queue(),^{replay_notice(exporter);});
    }
}
void replay_status(char *buf,int size) {
    std::lock_guard<std::mutex> lock(control);
    auto stats=actionReplay?actionReplay->stats():replay::Stats{};
    if(!stats.armed) {
        ClipSession *s=current;
        if(s && s->replayOnly && !dispatch_group_wait(s->finished,DISPATCH_TIME_NOW) && s->error)
            snprintf(buf,size,"Arm replay (Error: %s)",s->error.UTF8String);
        else snprintf(buf,size,"Arm replay (Off)");
        return;
    }
    double span=CMTIME_IS_NUMERIC(stats.first)?CMTimeGetSeconds(CMTimeSubtract(stats.end,stats.first)):0;
    if(span<30) snprintf(buf,size,"Disarm replay (warming up: %.0f/30 s)",span);
    else snprintf(buf,size,"Disarm replay (ready; %.1f MiB)",stats.bytes/1048576.0);
}
void replay_save_status(char *buf,int size) {
    std::lock_guard<std::mutex> lock(control);
    if(!replayExport) snprintf(buf,size,"Save last 30 s (arm replay first)");
    else snprintf(buf,size,"Save last 30 s; %s",replayExport->status().c_str());
}
static bool cliprec_shared_action(ClipSession *s) {
    if(s->stopping || !(s->broadcast || s->replayRequested)) return false;
    bool attach=!s->fileRequested.load();
    if(attach && s->retiringFiles) return true;
    if(!attach) ++s->retiringFiles;
    s->fileRequested=attach;
    dispatch_async(s->work, ^{
        if(attach) { s->replayOnly=false; if(s->ready) [s attachFile]; }
        else [s retireFile];
    });
    return true;
}
void cliprec_action(int unused) {
    (void)unused;
    std::lock_guard<std::mutex> lock(control);
    ClipSession *s = current;
    if (s && dispatch_group_wait(s->finished,DISPATCH_TIME_NOW)) {
        if(cliprec_shared_action(s)) return;
        active.store(false,std::memory_order_release);
        dispatch_async(s->work, ^{ [s stop]; }); return;
    }
    static dispatch_once_t once;
    dispatch_once(&once, ^{ atexit(cliprec_shutdown); });
    current = [ClipSession new]; current->fileRequested=true;
    int resolution = settings_get("display.clip_resolution");
    current->resolution = resolution >= 0 && resolution <= 2 ? (unsigned)resolution : CLIPREC_DEFAULT_RESOLUTION;
    active.store(true,std::memory_order_release);
}
void cliprec_open_folder(int unused) {
    (void)unused;
    NSString *folder = [NSHomeDirectory() stringByAppendingPathComponent:@"Movies/NFSMW"];
    [NSFileManager.defaultManager createDirectoryAtPath:folder withIntermediateDirectories:YES attributes:nil error:nil];
    portmenu_open_folder(folder.fileSystemRepresentation);
}
void cliprec_status(char *buf, int size) {
    ClipSession *s;
    { std::lock_guard<std::mutex> lock(control); s = current; }
    if (!s) { snprintf(buf,size,"Record clip"); return; }
    NSString *text;
    // Never dispatch_sync to the encoder queue from the menu/game thread.
    // The group publishes immutable terminal details; ready publishes geometry.
    if (!dispatch_group_wait(s->finished,DISPATCH_TIME_NOW)) {
        text = s->error ? [NSString stringWithFormat:@"%@%@", s->published ? @"Record clip; stopped: " : @"Error: ",s->error] : @"Record clip";
    } else if (s->stopping.load() || !active.load(std::memory_order_acquire)) text = @"Saving...";
    else if (s->retiringFiles.load()) text = @"Saving...";
    else if (s->replayOnly) text = @"Record clip";
    else if (!s->ready.load(std::memory_order_acquire)) text = @"Stop recording (starting...)";
    else {
        unsigned sec = (unsigned)(NSProcessInfo.processInfo.systemUptime-s->started);
        text = [NSString stringWithFormat:@"Stop recording %u:%02u (%ux%u %@; drops %u/%u/%u/%u)",sec/60,sec%60,
            s->width,s->height,s->codec,s->poolDrops.load(),s->writerDrops.load(),s->unshown.load(),s->cadenceDrops.load()];
    }
    snprintf(buf,size,"%s",text.UTF8String);
}
