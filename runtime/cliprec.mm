// SDR video capture from SDR or linear HDR presentation sources.
// The presenter only reserves a surface and encodes conversion;
// hardware compression and MP4 passthrough run on a separate serial queue.
#import <Cocoa/Cocoa.h>
#import <AVFoundation/AVFoundation.h>
#import <VideoToolbox/VideoToolbox.h>
#import <CoreVideo/CoreVideo.h>
#import <AudioToolbox/AudioToolbox.h>
#include "cliprec.h"
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
static std::atomic<bool> active{false};
static std::mutex control;
@class ClipSession;
static ClipSession *current;
static void deactivate(ClipSession *s) {
    std::lock_guard<std::mutex> lock(control);
    if (current==s) active.store(false,std::memory_order_release);
}
static NSString *outputFolder; // injectable by the offline test; no environment start switch

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
    struct Point { double time; float l, r; uint64_t segment; };
    static constexpr uint64_t capacity = 262144;
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
public:
    AVAssetWriterInput *input=nil;
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
        uint64_t w=a.write.load(std::memory_order_relaxed), r=a.read.load(std::memory_order_acquire);
        if (block->frames>capacity-(w-r)) { a.fault=true; return; }
        bool changed=a.stream!=block->stream_id || a.generation!=block->clock_generation || (block->flags&DS_CAPTURE_DISCONTINUITY);
        double first=a.place(block,changed);
        if(changed) { a.stream=block->stream_id; a.generation=block->clock_generation; ++a.segment; }
        for (size_t i=0;i<block->frames;i++) {
            float stereo[2]; media::fold_to_stereo(block->pcm51+i*6,stereo,1);
            a.points[(w+i)%capacity]={first+(double)i/block->sample_rate,stereo[0],stereo[1],a.segment};
        }
        a.maximumQueued.store(std::max(a.maximumQueued.load(),w-r+block->frames));
        // Publish the segment boundary before exposing its points to the consumer.
        if(changed) a.latestStart.store(w,std::memory_order_release);
        a.write.store(w+block->frames,std::memory_order_release);
    }
    bool start() {
        subscribed=ds_capture_start(receive,this)==DS_OK;
        if (!subscribed) error=@"Audio capture subscription unavailable";
        return subscribed;
    }
    void stop() { if(subscribed) { ds_capture_stop(); subscribed=false; } }
    bool attach(AVAssetWriter *writer) {
        input=[AVAssetWriterInput assetWriterInputWithMediaType:AVMediaTypeAudio outputSettings:@{
            AVFormatIDKey:@(kAudioFormatMPEG4AAC), AVSampleRateKey:@48000,
            AVNumberOfChannelsKey:@2, AVEncoderBitRateKey:@192000}];
        input.expectsMediaDataInRealTime=YES;
        if (![writer canAddInput:input]) { error=@"AAC audio encoder unavailable or MP4 format rejected"; return false; }
        [writer addInput:input]; return true;
    }
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
    bool covers(double time) const {
        uint64_t r=read.load(std::memory_order_relaxed),w=write.load(std::memory_order_acquire);
        if(r==w) return false;
        uint64_t latest=latestStart.load(std::memory_order_acquire);
        if(latest>r && latest<w && points[latest%capacity].time<=time) r=latest;
        return points[r%capacity].time<=time+1.0/48000 && points[(w-1)%capacity].time>=time-1.0/48000;
    }
    bool pump(double epoch, double end, bool final) {
        if (fault.load()) { error=@"Audio capture queue overflow or invalid clock"; if(!final) return false; }
        if (!epoch) { preroll(); return true; }
#ifdef CLIPREC_TESTING
        if(forcePressure) return true;
#endif
        // Audio is consumed only up to the video end, which stalls while frames
        // are not presented (hidden window, long load). Keep the newest half
        // ring so the producer never overflows; the dropped span becomes
        // silence when video resumes, and later audio stays on the epoch clock.
        uint64_t queued=write.load(std::memory_order_acquire);
        if(queued-read.load(std::memory_order_relaxed)>capacity/2) read.store(queued-capacity/2,std::memory_order_release);
        int64_t target=(int64_t)llround(end*48000);
        while(emitted<target && input.readyForMoreMediaData) {
            uint64_t r=read.load(std::memory_order_relaxed),w=write.load(std::memory_order_acquire);
            if (!w || w==r) {
                if(final) error=@"Audio capture ended before video";
                return !final;
            }
            float pcm[1024*2]; unsigned n=0;
            while(n<1024 && emitted+n<target) {
                double t=epoch+(double)(emitted+n)/48000;
                uint64_t latest=latestStart.load(std::memory_order_acquire);
                if(latest>r && latest<w && points[latest%capacity].time<=t) r=latest;
                while(r+1<w && points[(r+1)%capacity].time<=t) ++r;
                Point a=points[r%capacity];
                if (a.time>t+1.0/48000 && emitted+n==0) {
                    error=@"Audio capture missed the first presentation"; return false;
                }
                if(r+1>=w && t>a.time+1.0/48000) break;
                Point b=r+1<w?points[(r+1)%capacity]:a;
                double gap=b.time-a.time;
                float f=gap>0?fmin(1.0,fmax(0.0,(t-a.time)/gap)):0;
                if (gap>0.02 || (a.segment!=b.segment && gap>2.0/48000) || a.time>t+1.0/48000) { pcm[2*n]=pcm[2*n+1]=0; ++silence; }
                else { pcm[2*n]=a.l+(b.l-a.l)*f; pcm[2*n+1]=a.r+(b.r-a.r)*f; }
                ++n;
            }
            read.store(r,std::memory_order_release);
            if(!n) { if(final) error=@"Audio capture ended before video"; return !final; }
            AudioStreamBasicDescription asbd={};
            asbd.mSampleRate=48000; asbd.mFormatID=kAudioFormatLinearPCM;
            asbd.mFormatFlags=kAudioFormatFlagIsFloat|kAudioFormatFlagIsPacked;
            asbd.mBytesPerPacket=asbd.mBytesPerFrame=8; asbd.mFramesPerPacket=1;
            asbd.mChannelsPerFrame=2; asbd.mBitsPerChannel=32;
            CMAudioFormatDescriptionRef format=nullptr; CMBlockBufferRef data=nullptr; CMSampleBufferRef sample=nullptr;
            OSStatus rc=CMAudioFormatDescriptionCreate(NULL,&asbd,0,NULL,0,NULL,NULL,&format);
            if(!rc) rc=CMBlockBufferCreateWithMemoryBlock(NULL,NULL,n*8,NULL,NULL,0,n*8,0,&data);
            if(!rc) rc=CMBlockBufferReplaceDataBytes(pcm,data,0,n*8);
            CMSampleTimingInfo timing={CMTimeMake(1,48000),CMTimeMake(emitted,48000),kCMTimeInvalid};
            size_t size=8;
            if(!rc) rc=CMSampleBufferCreateReady(NULL,data,format,n,1,&timing,1,&size,&sample);
            bool ok=!rc && [input appendSampleBuffer:sample];
            if(sample) CFRelease(sample); if(data) CFRelease(data); if(format) CFRelease(format);
            if(!ok) { error=[NSString stringWithFormat:@"AAC audio encoding/write failed (%d)",(int)rc]; return false; }
            emitted+=n;
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

@interface ClipPacket : NSObject {
@public
    CMSampleBufferRef sample;
}
@end
@implementation ClipPacket
- (void)dealloc { if (sample) CFRelease(sample); }
@end

@interface ClipSession : NSObject {
@public
    dispatch_queue_t work;
    dispatch_group_t finished;
    id<MTLDevice> device;
    id<MTLComputePipelineState> pipeline;
    CVPixelBufferPoolRef pool;
    CVMetalTextureCacheRef cache;
    VTCompressionSessionRef encoder;
    AVAssetWriter *writer;
    AVAssetWriterInput *input;
    std::unique_ptr<ClipAudio> audio;
    double videoEnd;
    uint64_t audioQueuePeak, audioSilence;
    bool finishing;
    NSString *temporary, *destination, *error, *codec;
    std::atomic<unsigned> outstanding, poolDrops, writerDrops, unshown, cadenceDrops, accepted, streak;
    std::atomic<bool> ready, stopping, packetOverflow, ended;
    std::atomic<unsigned> queuedPackets;
    bool preparing, finalized, writing, pumpScheduled, published;
    unsigned width, height, resolution;
    int bitrate;
    double epoch, last, started, stopDeadline, blockedSince;
    NSMutableSet<ClipFrame *> *frames;
    NSMutableArray<ClipPacket *> *packets;
#ifdef CLIPREC_TESTING
    bool forceBackpressure, forceHEVCFailure, forceNoHardware;
#endif
}
- (void)fail:(NSString *)message;
- (void)prepare:(unsigned)w height:(unsigned)h;
- (void)consume:(ClipFrame *)frame;
- (void)stop;
- (void)drain;
- (void)packet:(CMSampleBufferRef)sample status:(OSStatus)status;
- (void)pump;
- (void)finishWhenDrained;
- (void)audioTick;
- (void)finish;
@end

static void compressed(void *context, void *, OSStatus status, VTEncodeInfoFlags flags, CMSampleBufferRef sample) {
    ClipSession *s = (__bridge ClipSession *)context;
    if (s->ended.load(std::memory_order_acquire)) return;
    // Reserve before enqueueing: encoded packets must be bounded too, even if
    // the writer or work queue stalls. On overflow save only the valid prefix.
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
        frames = [NSMutableSet new]; packets = [NSMutableArray new];
        outstanding=0; poolDrops=0; writerDrops=0; unshown=0; cadenceDrops=0; accepted=0; streak=0;
        ready=false; stopping=false; packetOverflow=false; ended=false; queuedPackets=0;
        resolution = CLIPREC_DEFAULT_RESOLUTION;
        started = NSProcessInfo.processInfo.systemUptime;
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
- (void)audioTick {
    if(ended || !audio) return;
    if(error || !audio->pump(writing?epoch:0,videoEnd,false)) [self fail:error ?: audio->error];
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
    [self stop];
}
- (void)prepare:(unsigned)w height:(unsigned)h {
    if (stopping) return;
    if (error) { [self fail:error]; return; }
    // Preserve the presented aspect, never upscale; round down to even NV12 dimensions.
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
    NSString *folder = outputFolder ?: [NSHomeDirectory() stringByAppendingPathComponent:@"Movies/NFSMW"];
    if (![NSFileManager.defaultManager createDirectoryAtPath:folder withIntermediateDirectories:YES attributes:nil error:&e]) {
        [self fail:[@"Output folder: " stringByAppendingString:e.localizedDescription]]; return;
    }
    NSDateFormatter *date = [NSDateFormatter new]; date.locale = [NSLocale localeWithLocaleIdentifier:@"en_US_POSIX"];
    date.dateFormat = @"yyyyMMdd-HHmmss";
    NSString *name = [NSString stringWithFormat:@"Clip-%@-%@",[date stringFromDate:NSDate.date],NSUUID.UUID.UUIDString];
    destination = [folder stringByAppendingPathComponent:[name stringByAppendingString:@".mp4"]];
    temporary = [folder stringByAppendingPathComponent:[@"." stringByAppendingString:[name stringByAppendingString:@".tmp.mp4"]]];
    writer = [[AVAssetWriter alloc] initWithURL:[NSURL fileURLWithPath:temporary] fileType:AVFileTypeMPEG4 error:&e];
    if (!writer) { [self fail:[@"MP4 writer: " stringByAppendingString:e.localizedDescription]]; return; }
    ready.store(true, std::memory_order_release);

}
- (void)packet:(CMSampleBufferRef)sample status:(OSStatus)status {
    if (ended || finishing) { --queuedPackets; return; }
    if (writer.status == AVAssetWriterStatusCancelled) { --queuedPackets; return; }
    if (writer.status == AVAssetWriterStatusFailed) {
        --queuedPackets; [self fail:writer.error.localizedDescription ?: @"MP4 writer failed"]; return;
    }
    if (status || !sample || !CMSampleBufferDataIsReady(sample)) {
        --queuedPackets;
        [self fail:[NSString stringWithFormat:@"Video encoder error %d", (int)status]]; return;
    }
    if (!writing) {
        input = [AVAssetWriterInput assetWriterInputWithMediaType:AVMediaTypeVideo outputSettings:nil
            sourceFormatHint:CMSampleBufferGetFormatDescription(sample)];
        input.expectsMediaDataInRealTime = YES;
        if (![writer canAddInput:input]) { --queuedPackets; [self fail:@"MP4 video format rejected"]; return; }
        [writer addInput:input];
        if (audio && !audio->attach(writer)) { --queuedPackets; [self fail:audio->error]; return; }
        if (![writer startWriting]) { --queuedPackets; [self fail:writer.error.localizedDescription ?: @"MP4 start failed"]; return; }
        [writer startSessionAtSourceTime:kCMTimeZero]; writing = true;
    }
    ClipPacket *p = [ClipPacket new]; p->sample = sample; CFRetain(sample);
    [packets addObject:p];
    [self pump];
}
- (void)pump {
    while (packets.count && input.readyForMoreMediaData) {
        ClipPacket *p = packets.firstObject;
        if (![input appendSampleBuffer:p->sample]) {
            writerDrops += (unsigned)packets.count; queuedPackets -= (unsigned)packets.count;
            [packets removeAllObjects];
            [self fail:writer.error.localizedDescription ?: @"MP4 write failed"]; return;
        }
        CMTime duration=CMSampleBufferGetDuration(p->sample);
        if(!CMTIME_IS_NUMERIC(duration) || CMTimeGetSeconds(duration)<=0) duration=CMTimeMake(1,60);
        videoEnd=fmax(videoEnd,CMTimeGetSeconds(CMTimeAdd(CMSampleBufferGetPresentationTimeStamp(p->sample),duration)));
        ++accepted; --queuedPackets; streak=0;
        [packets removeObjectAtIndex:0]; blockedSince=0;
    }
    if(audio && writing && !audio->pump(epoch,videoEnd,false)) [self fail:audio->error];
    if (!packets.count) return;
    double now = NSProcessInfo.processInfo.systemUptime;
    if (!blockedSince) blockedSince=now;
    if (now-blockedSince >= 2 || writer.status == AVAssetWriterStatusFailed) {
        writerDrops += (unsigned)packets.count; queuedPackets -= (unsigned)packets.count;
        [packets removeAllObjects]; packetOverflow=true;
        [self fail:writer.error.localizedDescription ?: @"Recording stopped: writer stalled"]; return;
    }
    if (!pumpScheduled) {
        pumpScheduled=true;
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_MSEC),work,^{
            self->pumpScheduled=false; [self pump];
        });
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
    }
    // At most 60 fps; keep gaps on the host timeline instead of compacting drops.
    if (last && f->time-last < 1.0/60.0-0.0001) { ++cadenceDrops; return; }
    if (f->time < epoch || (last && f->time <= last)) { ++unshown; return; }
    // Drop before compression when the writer is behind, so future encoded
    // frames never reference a compressed packet we discarded.
    bool backpressure = packets.count > 0 || packetOverflow.load();
#ifdef CLIPREC_TESTING
    backpressure |= forceBackpressure;
#endif
    if (backpressure) {
        ++writerDrops;
        if (++streak >= kOverload) [self fail:@"Recording stopped: writer overloaded"];
        return;
    }
    last = f->time;
    CMTime pts = CMTimeMakeWithSeconds(f->time-epoch,60000);
    VTEncodeInfoFlags flags;
    OSStatus rc = VTCompressionSessionEncodeFrame(encoder,f->pixel,pts,CMTimeMake(1,60),NULL,NULL,&flags);
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
    // VT callbacks enqueue packets ahead of this barrier on the same work queue.
    dispatch_async(work, ^{ [self finishWhenDrained]; });
}
- (void)finishWhenDrained {
    if (packets.count) {
        [self pump];
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,10*NSEC_PER_MSEC),work,^{ [self finishWhenDrained]; });
        return;
    }
    if (finishing || ended) return;
    if(audio) {
        audio->stop();
        bool drainedOK=!writing || audio->pump(epoch,videoEnd,true);
        if(!drainedOK && !error) error=audio->error;
        if(writing && drainedOK && !audio->drained(videoEnd)) {
            if(NSProcessInfo.processInfo.systemUptime<stopDeadline+2) {
                dispatch_after(dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_MSEC),work,^{ [self finishWhenDrained]; }); return;
            }
            if(!error) error=@"AAC audio writer stalled";
        }
        if(writing) [audio->input markAsFinished];
    }
    finishing=true;
    if (writing && writer.status == AVAssetWriterStatusWriting) {
        [writer endSessionAtSourceTime:CMTimeMakeWithSeconds(videoEnd,60000)];
        [input markAsFinished];
        [writer finishWritingWithCompletionHandler:^{ dispatch_async(self->work, ^{ [self finish]; }); }];
    } else {
        if (!error) error = @"No presented video frames";
        [writer cancelWriting]; [self finish];
    }
}

- (void)finish {
    if (ended) return;
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
    } else if (!error) error = writer.error.localizedDescription ?: @"MP4 finalization failed";
    if (writer.status != AVAssetWriterStatusCompleted && temporary)
        [NSFileManager.defaultManager removeItemAtPath:temporary error:nil];
    // CompleteFrames and the packet barrier have retired all encoder callbacks.
    // Keep terminal metadata for the menu, but no idle capture/encoding resources.
    if (encoder) { VTCompressionSessionInvalidate(encoder); CFRelease(encoder); encoder=NULL; }
    if (cache) { CVMetalTextureCacheFlush(cache,0); CFRelease(cache); cache=NULL; }
    if (pool) { CFRelease(pool); pool=NULL; }
    if(audio) { audio->stop(); audioQueuePeak=audio->maximumQueued.load(); audioSilence=audio->silence; audio.reset(); }
    pipeline=nil; device=nil; input=nil; writer=nil;
    [frames removeAllObjects]; [packets removeAllObjects];
    ready = false; ended.store(true,std::memory_order_release);
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
    if (!s) return;
    cliprec_stop();
    // Quit is allowed to wait; rendering never waits on GPU/encoder/writer.
    if (dispatch_group_wait(s->finished,dispatch_time(DISPATCH_TIME_NOW,10*NSEC_PER_SEC)))
        fprintf(stderr,"cliprec: finalization timed out; unpublished temporary clip retained\n");
}
void cliprec_action(int unused) {
    (void)unused;
    std::lock_guard<std::mutex> lock(control);
    ClipSession *s = current;
    if (s && dispatch_group_wait(s->finished,DISPATCH_TIME_NOW)) {
        active.store(false,std::memory_order_release);
        dispatch_async(s->work, ^{ [s stop]; }); return;
    }
    static dispatch_once_t once;
    dispatch_once(&once, ^{ atexit(cliprec_shutdown); });
    current = [ClipSession new];
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
    else if (!s->ready.load(std::memory_order_acquire)) text = @"Stop recording (starting...)";
    else {
        unsigned sec = (unsigned)(NSProcessInfo.processInfo.systemUptime-s->started);
        text = [NSString stringWithFormat:@"Stop recording %u:%02u (%ux%u %@; drops %u/%u/%u/%u)",sec/60,sec%60,
            s->width,s->height,s->codec,s->poolDrops.load(),s->writerDrops.load(),s->unshown.load(),s->cadenceDrops.load()];
    }
    snprintf(buf,size,"%s",text.UTF8String);
}
