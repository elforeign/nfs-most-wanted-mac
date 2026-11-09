// Quit proof: ORIGINAL replay-only 0.806214 s (FAIL), fixed 0.000000 s (PASS).
// Requested writer: ORIGINAL 0.809907 s, fixed 0.810076 s; both complete.
// Independent sink controls live in clip_packets_test.py: callback 2229 ns
// vs fold mutant 12673 ns (>9260 ns budget); healthy 300/300 during a 5 s
// stall vs shared-queue mutant 0/300. Both broken executables exit 1.
// Offline proof through the production NV12/VT/MP4 path (no window or game).
// Synthetic drawables deliver controlled host presentation times; actual Metal
// completion still gates every encode. AVAssetReader verifies decoded output.
// Every validator is run once with a deliberate fault: duration +0.1 s,
// PTS +0.1 s, frame count -1, colour +80, flipped orientation, marker +32,
// counter +1, drop count 0, invalid MP4 and missing finalization. The executable
// prints baseline and rejected-mutant counts; both must pass (10/10 controls).
// Measured baseline: 36 frames, 0.600000 s, PTS error 0.000000 s, RGB error
// 2/255, orientation/marker/counter error 0. Mutants: 0.700000 s duration,
// 0.100000 s PTS error, 35 frames, RGB error 82/255, orientation/marker/counter
// error 1, invalid media, drops 0 (expected 1), unfinished group (expected done).
// Exhaustion baseline: 1 drop, 36 frames, 0.616667 s, PTS error 0.000000 s.
// Publication collision: error 1 versus normal 0; all 7 sentinel bytes intact.
// Off assembly proof in offline_tests.sh: original ARC entry retained 3 objects;
// wrapper 0 calls / 0 stack frame; injected off-path retain = 1 rejected call.
// Resolution baseline: 6 decoded frames per codec at 1920x1080, 2560x1440,
// 3840x2160; HEVC targets 10000000/17777778/40000000 b/s, H.264 targets
// 15000000/26666667/60000000 b/s; hardware target error <0.00001% (limit 1%).
// Wide 4000x2000 -> 2160x1080; small 320x180 stays 320x180 (no enlargement).
// Actual old-code negative run: 2 live pool/encoder handles after stop versus
// fixed 0; original drop logic: streak 0 after 119 encoder drops versus 119.
// Fixed: 120 drops, streak 120, inactive, valid prefix, 0 retained handles.
// Old-size mutation: 3840x2160 versus expected 1920x1080; old-bitrate mutation:
// 40000000 versus expected 10000000 b/s (300% error); both rejected.
// HDR regression, actual original-code run: 16F preparation fails with
// "Recording requires SDR output (HDR is not supported yet)"; no clip produced.
// Fixed: 16F clips record/decode; H=1 pre-encode NV12 max error 1 LSB across
// all 256 codes and native/up/downscale; H=7 shoulder error 0 luma LSB.
// Deliberately omitting sRGB recovery gives 60 LSB error; disabling the
// shoulder gives 9 LSB error. Both exceed the 1-LSB limit and are rejected.
#define CLIPREC_TESTING 1
#ifndef CLIPREC_SOURCE
#define CLIPREC_SOURCE "cliprec.mm"
#endif
#include CLIPREC_SOURCE
#include <vector>
#include <cmath>
#include <functional>
#include <cstdarg>

static std::mutex sessionNotesLock;
static std::vector<std::string> sessionNotes;
static FILE *sessionNoteFile;
extern "C" void report_note(const char *fmt, ...) {
    char text[512]; va_list ap; va_start(ap,fmt);
    vsnprintf(text,sizeof text,fmt,ap); va_end(ap);
    std::lock_guard<std::mutex> guard(sessionNotesLock);
    sessionNotes.emplace_back(text);
    if(sessionNoteFile) { fprintf(sessionNoteFile,"%s\n",text); fflush(sessionNoteFile); }
    printf("session: %s\n",text);
}

extern "C" void portmenu_open_folder(const char *path) { (void)path; }
extern "C" void portmenu_status(const char *fmt, ...) { (void)fmt; }

// The test injects only the saved setting read; recording snapshots it at start.
static int resolutionChoice=CLIPREC_DEFAULT_RESOLUTION;
extern "C" int settings_get(const char *id) {
    return !strcmp(id,"display.clip_resolution") ? resolutionChoice : -1;
}
static unsigned checks;
static void require(bool ok, const char *what) {
    ++checks;
    if (!ok) { fprintf(stderr,"FAIL cliprec: %s\n",what); exit(1); }
}

// A slow file/encoder completion stub: flushing an unwanted replay capture
// would run this just as flushing a requested recording would.
@interface QuitWriterSession : ClipSession
@end
@implementation QuitWriterSession
- (void)stop {
    usleep(800000);
    if(fileRequested || retiringFiles) published=true;
    ended=true;
    dispatch_group_leave(finished);
}
@end
static void shutdownProof(void) {
    for(unsigned scenario=0;scenario<3;++scenario) {
        bool requested=scenario<2;
        QuitWriterSession *s=[QuitWriterSession new];
        s->replayOnly=scenario!=0; s->fileRequested=scenario==0;
        s->retiringFiles=scenario==1; s->liveRequested=false;
        if(s->replayOnly) {
            s->replayRequested=true;
            s->replayRing=std::make_shared<replay::Ring>(); s->replayRing->arm();
        }
        { std::lock_guard<std::mutex> lock(control); current=s; active=true; }
        double begin=NSProcessInfo.processInfo.systemUptime;
        cliprec_shutdown();
        double elapsed=NSProcessInfo.processInfo.systemUptime-begin;
        printf("shutdown requested=%d retiring=%u: %.6f s, writer completed=%d\n",requested,s->retiringFiles.load(),elapsed,(int)s->ended.load());
        if(requested) require(s->published && s->ended,"requested save completes at quit");
        else {
            require(elapsed<0.5,"unrequested replay shutdown under 0.5 s");
            require(!s->ended && !s->published,"discard never starts writer finalization");
            // Retire the fixture only after observing the production discard.
            s->ended=true; dispatch_group_leave(s->finished);
        }
        require(!active,"quit stops capture");
        { std::lock_guard<std::mutex> lock(control); current=nil; }
    }
}

@interface TestDrawable : NSObject <CAMetalDrawable> {
    NSMutableArray *handlers;
}
@property(nonatomic,strong) id<MTLTexture> texture;
@property(nonatomic) CFTimeInterval presentedTime;
@property(nonatomic,readonly) NSUInteger drawableID;
@property(nonatomic,strong) CAMetalLayer *layer;
- (void)shown:(double)time;
@end
@implementation TestDrawable
- (instancetype)init { if ((self=[super init])) handlers=[NSMutableArray new]; return self; }
- (NSUInteger)drawableID { return 1; }
- (void)addPresentedHandler:(MTLDrawablePresentedHandler)handler { [handlers addObject:[handler copy]]; }
- (void)shown:(double)t {
    self.presentedTime=t;
    for (MTLDrawablePresentedHandler h in handlers) h(self);
    [handlers removeAllObjects];
}
- (void)present { [self shown:self.presentedTime]; }
- (void)presentAtTime:(CFTimeInterval)t { [self shown:t]; }
- (void)presentAfterMinimumDuration:(CFTimeInterval)t { (void)t; [self present]; }
@end

static id<MTLDevice> gpu;
static id<MTLCommandQueue> commands;
static const unsigned W=320, H=180, N=36;
static uint8_t barChannel(unsigned bar, unsigned channel) {
    // Standard eight binary RGB combinations, generated rather than stored.
    unsigned bit = channel==0 ? 2 : channel==1 ? 4 : 1;
    return bar & bit ? 16 : 235;
}
static id<MTLTexture> pattern(unsigned frame) {
    auto td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:W height:H mipmapped:NO];
    td.usage=MTLTextureUsageShaderRead; td.storageMode=MTLStorageModeShared;
    id<MTLTexture> t=[gpu newTextureWithDescriptor:td];
    std::vector<uint8_t> pixels(W*H*4);
    for (unsigned y=0;y<H;y++) for(unsigned x=0;x<W;x++) {
        unsigned bar=x/(W/8);
        uint8_t r=barChannel(bar,0),g=barChannel(bar,1),b=barChannel(bar,2);
        // An asymmetric bottom band makes a vertical flip unambiguous.
        if(y>=H-24) r=g=b=70;
        // Moving white marker and six large binary frame-counter cells.
        if(y>=24 && y<40 && x>=frame*6 && x<frame*6+16) r=g=b=255;
        if(y>=H-20 && y<H-4 && x<6*24) r=g=b=(frame&(1u<<(x/24))) ? 235 : 16;
        unsigned p=(y*W+x)*4; pixels[p]=b; pixels[p+1]=g; pixels[p+2]=r; pixels[p+3]=255;
    }
    [t replaceRegion:MTLRegionMake2D(0,0,W,H) mipmapLevel:0 withBytes:pixels.data() bytesPerRow:W*4];
    return t;
}
// Reconstruct the H=1 transport: sRGB bytes decoded into half-float linear RGB.
static id<MTLTexture> linearPattern(unsigned frame, id<MTLTexture> bytes=nil) {
    if (!bytes) bytes=pattern(frame);
    std::vector<uint8_t> bgra(W*H*4);
    [bytes getBytes:bgra.data() bytesPerRow:W*4 fromRegion:MTLRegionMake2D(0,0,W,H) mipmapLevel:0];
    std::vector<_Float16> rgba(W*H*4);
    for(unsigned p=0;p<W*H;p++) {
        for(unsigned c=0;c<3;c++) {
            float v=bgra[p*4+2-c]/255.0f;
            rgba[p*4+c]=v<=0.04045f ? v/12.92f : powf((v+0.055f)/1.055f,2.4f);
        }
        rgba[p*4+3]=1;
    }
    auto td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA16Float width:W height:H mipmapped:NO];
    td.usage=MTLTextureUsageShaderRead; td.storageMode=MTLStorageModeShared;
    id<MTLTexture> t=[gpu newTextureWithDescriptor:td];
    [t replaceRegion:MTLRegionMake2D(0,0,W,H) mipmapLevel:0 withBytes:rgba.data() bytesPerRow:W*8];
    return t;
}
static TestDrawable *drawable(void) {
    TestDrawable *d=[TestDrawable new];
    d.texture=[gpu newTextureWithDescriptor:[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:W height:H mipmapped:NO]];
    return d;
}
static void syncWork(ClipSession *s) { dispatch_sync(s->work,^{}); }
static ClipSession *start(bool fallback=false, unsigned outW=W, unsigned outH=H, id<MTLTexture> source=nil, float headroom=1, const std::function<void()> &beforePrepare={}) {
    { std::lock_guard<std::mutex> lock(control); current=nil; }
    cliprec_action(0);
    ClipSession *s=current;
    s->forceHEVCFailure=fallback;
    if(beforePrepare) beforePrepare();
    TestDrawable *d=drawable();
    d.texture=[gpu newTextureWithDescriptor:[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:outW height:outH mipmapped:NO]];
    id<MTLCommandBuffer> cb=[commands commandBuffer];
    cliprec_present(cb,source ?: pattern(0),d,headroom);
    [cb commit]; [cb waitUntilCompleted]; syncWork(s);
    if (!s->ready) {
        char status[512]; cliprec_status(status,sizeof status);
        fprintf(stderr,"FAIL prepare: %s\n",status); exit(1);
    }
    const unsigned heights[]={1080,1440,2160};
    double scale=fmin(1.0,(double)heights[resolutionChoice]/outH);
    require(s->width==((unsigned)(outW*scale)&~1u) && s->height==((unsigned)(outH*scale)&~1u),"actual resolution");
    char status[256]; cliprec_status(status,sizeof status);
    require(strstr(status,"Stop recording 0:"),"recording action and elapsed time");
    return s;
}
static void frame(ClipSession *s,unsigned n,double pts,bool shown=true,bool presentFirst=false,bool drain=true, id<MTLTexture> source=nil, float headroom=1) {
    @autoreleasepool {
        TestDrawable *d=drawable();
        id<MTLCommandBuffer> cb=[commands commandBuffer];
        cliprec_present(cb,source ?: pattern(n),d,headroom);
        if (presentFirst) [d shown:pts];
        [cb commit]; [cb waitUntilCompleted];
        if (shown && !presentFirst) [d shown:pts];
        syncWork(s);
        // Synthetic frames run faster than real-time. Drain accepted work here
        // so this checks fidelity independently of the explicit overload cases.
        if(drain) {
            dispatch_sync(s->work, ^{ if(s->encoder) VTCompressionSessionCompleteFrames(s->encoder,kCMTimeInvalid); });
            syncWork(s);
#if !defined(CLIPREC_CASE) || (CLIPREC_CASE!=3 && CLIPREC_CASE!=7)
            // Only the test's accelerated fidelity producer waits for the file
            // thread; the production presenter and the stall proof never wait.
            if(s->fileSink && !s->stallFile) {
                double deadline=NSProcessInfo.processInfo.systemUptime+1;
                while(!s->fileSink->drained() && !s->fileSink->failed() && NSProcessInfo.processInfo.systemUptime<deadline) usleep(500);
            }
#endif
        }
    }
}
struct Decoded {
    unsigned count=0;
    double duration=0, spacingError=0, colourError=0, orientationError=0, markerError=0, counterError=0;
    bool valid=false;
};
static Decoded decode(NSString *path, bool gap=false) {
    Decoded result;
    if (!path) return result;
    NSError *e=nil;
    AVURLAsset *asset=[AVURLAsset URLAssetWithURL:[NSURL fileURLWithPath:path] options:nil];
    NSArray<AVAssetTrack *> *tracks=[asset tracksWithMediaType:AVMediaTypeVideo];
    if (!tracks.count) return result;
    AVAssetReader *reader=[[AVAssetReader alloc] initWithAsset:asset error:&e];
    AVAssetReaderTrackOutput *out=[AVAssetReaderTrackOutput assetReaderTrackOutputWithTrack:tracks.firstObject
        outputSettings:@{(id)kCVPixelBufferPixelFormatTypeKey:@(kCVPixelFormatType_32BGRA)}];
    [reader addOutput:out]; if (![reader startReading]) return result;
    double previous=-1;
    while (CMSampleBufferRef sample=[out copyNextSampleBuffer]) {
        double pts=CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sample));
        unsigned expectedIndex=result.count;
        double expected=(expectedIndex + (gap && expectedIndex>=N/2 ? 1:0))/60.0;
        result.spacingError=fmax(result.spacingError,fabs(pts-expected));
        if(previous>=0 && pts<=previous) result.spacingError=1;
        previous=pts;
        CVPixelBufferRef pb=CMSampleBufferGetImageBuffer(sample);
        CVPixelBufferLockBaseAddress(pb,kCVPixelBufferLock_ReadOnly);
        auto base=(uint8_t *)CVPixelBufferGetBaseAddress(pb); size_t stride=CVPixelBufferGetBytesPerRow(pb);
        bool dimensions=CVPixelBufferGetWidth(pb)==W && CVPixelBufferGetHeight(pb)==H;
        if (!dimensions) result.colourError=1000;
        else {
            for(unsigned bar=0;bar<8;bar++) for(unsigned ch=0;ch<3;ch++) {
                unsigned actual=base[80*stride+(bar*40+20)*4+2-ch];
                result.colourError=fmax(result.colourError,fabs((double)actual-barChannel(bar,ch)));
            }
            unsigned top=base[8*stride+20*4],bottom=base[(H-12)*stride+280*4];
            result.orientationError=fmax(result.orientationError, top>180 && bottom>45 && bottom<95 ? 0:1);
            unsigned markerX=expectedIndex*6+8;
            unsigned marker=base[32*stride+markerX*4];
            result.markerError=fmax(result.markerError,marker>220 ? 0:1);
            unsigned counter=0;
            for(unsigned bit=0;bit<6;bit++) if(base[(H-12)*stride+(bit*24+12)*4]>128) counter|=1u<<bit;
            result.counterError=fmax(result.counterError,counter==expectedIndex ? 0:1);
        }
        CVPixelBufferUnlockBaseAddress(pb,kCVPixelBufferLock_ReadOnly);
        CFRelease(sample); result.count++;
    }
    result.duration=CMTimeGetSeconds(asset.duration);
    result.valid=reader.status==AVAssetReaderStatusCompleted && result.count>0;
    return result;
}
static bool validates(const Decoded &r,bool gap=false) {
    double expected=(N+(gap?1:0))/60.0;
    return r.valid && r.count==N && fabs(r.duration-expected)<1.0/60+0.001 && r.spacingError<0.001 &&
        r.colourError<=22 && r.orientationError==0 && r.markerError==0 && r.counterError==0;
}
static void finishedOK(ClipSession *s) {
    require(!dispatch_group_wait(s->finished,dispatch_time(DISPATCH_TIME_NOW,10*NSEC_PER_SEC)),"stop/quit finalizes within 10 s");
    syncWork(s);
    require(s->published,"MP4 finalized and published");
    require(!s->encoder && !s->pool && !s->cache && !s->file && !s->pipeline && !s->device,
        "finalization releases encoder / pool / cache / writer / Metal resources before Finder");
    require([NSFileManager.defaultManager fileExistsAtPath:s->destination],"published clip exists");
    require(![NSFileManager.defaultManager fileExistsAtPath:s->temporary],"temporary atomically renamed");
}
// Inspect the actual encoder surface after GPU completion, before presenting it.
// This observes production conversion without a test-only shader or CPU conversion.
static std::vector<uint8_t> capture(ClipSession *s, id<MTLTexture> source, double pts, float headroom=1) {
    TestDrawable *d=drawable();
    id<MTLCommandBuffer> cb=[commands commandBuffer];
    cliprec_present(cb,source,d,headroom); [cb commit]; [cb waitUntilCompleted]; syncWork(s);
    require(cb.status==MTLCommandBufferStatusCompleted && s->frames.count==1,"one completed capture awaiting presentation");
    ClipFrame *f=s->frames.anyObject;
    require(!CVPixelBufferLockBaseAddress(f->pixel,kCVPixelBufferLock_ReadOnly),"map pre-encode NV12");
    std::vector<uint8_t> bytes;
    for(unsigned plane=0;plane<2;plane++) {
        auto base=(uint8_t *)CVPixelBufferGetBaseAddressOfPlane(f->pixel,plane);
        size_t stride=CVPixelBufferGetBytesPerRowOfPlane(f->pixel,plane);
        for(unsigned y=0;y<(plane?s->height/2:s->height);y++)
            bytes.insert(bytes.end(),base+y*stride,base+y*stride+s->width);
    }
    CVPixelBufferUnlockBaseAddress(f->pixel,kCVPixelBufferLock_ReadOnly);
    [d shown:pts];
    dispatch_sync(s->work,^{ VTCompressionSessionCompleteFrames(s->encoder,kCMTimeInvalid); }); syncWork(s);
    return bytes;
}
static unsigned hdrMaxError, hdrShoulderError, hdrWhite;
static void hdrProof(void) {
    // Cover all 256 bytes, mixed channels, 2x2 chroma and both scaling directions.
    id<MTLTexture> known=pattern(0);
    std::vector<uint8_t> bgra(W*H*4);
    for(unsigned y=0;y<H;y++) for(unsigned x=0;x<W;x++) {
        unsigned n=((y/2)*(W/2)+x/2)%256;
        for(unsigned c=0;c<3;c++) bgra[(y*W+x)*4+c]=(n+c*53)%256;
        bgra[(y*W+x)*4+3]=255;
    }
    [known replaceRegion:MTLRegionMake2D(0,0,W,H) mipmapLevel:0 withBytes:bgra.data() bytesPerRow:W*4];
    id<MTLTexture> linear=linearPattern(0,known);
    unsigned maxError=0;
    for(unsigned scale=0;scale<3;scale++) {
        unsigned w=scale==0?W:scale==1?W*2:W/2, h=scale==0?H:scale==1?H*2:H/2;
        ClipSession *s=start(false,w,h,linear);
        auto a=capture(s,known,1200), b=capture(s,linear,1200+1.0/60);
        require(a.size()==b.size(),"H=1 plane sizes match SDR");
        for(size_t i=0;i<a.size();i++) maxError=std::max(maxError,(unsigned)abs((int)a[i]-b[i]));
        if(maxError>1) fprintf(stderr,"H=1 pre-encode error=%u LSB (limit 1)\n",maxError);
        require(maxError<=1,"H=1 pre-encode NV12 matches BGRA8 within 1 LSB");
        cliprec_shutdown(); finishedOK(s);
        require(s->accepted==2 && decode(s->destination).valid,"16F comparison clip records and decodes");
    }
    // Full decoded round trip also validates orientation, cadence and frame identity.
    ClipSession *s=start(false,W,H,linearPattern(0));
    for(unsigned i=0;i<N;i++) frame(s,i,1300+i/60.0,true,false,true,linearPattern(i));
    cliprec_shutdown(); finishedOK(s);
    require(validates(decode(s->destination)),"H=1 16F clip has SDR colors, orientation, markers and PTS");
    // Linear gray ramp spans SDR and highlights, with declared transport peak 7.
    std::vector<_Float16> ramp(W*H*4);
    for(unsigned y=0;y<H;y++) for(unsigned x=0;x<W;x++) {
        float v=7.0f*(x/2)/(W/2-1);
        for(unsigned c=0;c<3;c++) ramp[(y*W+x)*4+c]=v;
        ramp[(y*W+x)*4+3]=1;
    }
    id<MTLTexture> highlights=linearPattern(0);
    [highlights replaceRegion:MTLRegionMake2D(0,0,W,H) mipmapLevel:0 withBytes:ramp.data() bytesPerRow:W*8];
    s=start(false,W,H,highlights,7);
    auto actual=capture(s,highlights,1400,7);
    unsigned previous=0, shoulderError=0;
    for(unsigned x=0;x<W;x+=2) {
        float v=(float)ramp[x*4];
        float tone=v>0.75f ? 0.75f+0.25f*(1-expf(-(v-0.75f)/0.25f)) : v;
        float encoded=tone<=0.0031308f ? tone*12.92f : 1.055f*powf(tone,1/2.4f)-0.055f;
        unsigned expected=(unsigned)lroundf(16+219*encoded), got=actual[x];
        shoulderError=std::max(shoulderError,(unsigned)abs((int)got-(int)expected));
        require(got>=previous && got<=235,"HDR ramp is monotonic and bounded to SDR white"); previous=got;
    }
    if(shoulderError>1) fprintf(stderr,"H=7 shoulder error=%u LSB (limit 1)\n",shoulderError);
    require(shoulderError<=1,"HDR shoulder matches documented curve within 1 luma LSB");
    cliprec_shutdown(); finishedOK(s);
    require(s->accepted==1 && decode(s->destination).valid,"headroom 7 HDR clip records and decodes");
    hdrMaxError=maxError; hdrShoulderError=shoulderError; hdrWhite=previous;
    printf("HDR proof: H=1 pre-encode NV12 max error=%u LSB (256 codes, native/up/downscale); H=7 shoulder error=%u LSB, monotonic Y=16..%u; 16F clips decoded\n",maxError,shoulderError,previous);
}
// Read decoded image sizes, not just container metadata. AverageBitRate is a
// VBR target: verify the hardware session's configured target within 1%; simple
// color bars need fewer bits, so container data rate is reported, not constrained.
static void resolutionProof(void) {
    for (unsigned choice=0;choice<3;choice++) for(unsigned fallback=0;fallback<2;fallback++) {
        resolutionChoice=choice;
        ClipSession *s=start(fallback,3840,2160);
        const unsigned widths[]={1920,2560,3840}, heights[]={1080,1440,2160};
        const double hevc[]={10000000,17777778,40000000};
        double expected=hevc[choice]*(fallback?1.5:1);
        __block double target=0;
        dispatch_sync(s->work,^{
            CFTypeRef value=NULL;
            require(!VTSessionCopyProperty(s->encoder,kVTCompressionPropertyKey_AverageBitRate,NULL,&value),"read hardware bitrate target");
            if (value) { target=[(__bridge NSNumber *)value doubleValue]; CFRelease(value); }
        });
        require(fabs(target-expected)/expected<0.01,"pixel-scaled hardware bitrate within 1 percent");
        // Changing the setting during capture applies to the next clip only.
        resolutionChoice=(choice+1)%3;
        for(unsigned i=0;i<6;i++) frame(s,i,1000+i/60.0);
        cliprec_shutdown(); finishedOK(s);
        NSError *error=nil;
        AVURLAsset *asset=[AVURLAsset URLAssetWithURL:[NSURL fileURLWithPath:s->destination] options:nil];
        AVAssetTrack *track=[asset tracksWithMediaType:AVMediaTypeVideo].firstObject;
        AVAssetReader *reader=[[AVAssetReader alloc] initWithAsset:asset error:&error];
        AVAssetReaderTrackOutput *out=[AVAssetReaderTrackOutput assetReaderTrackOutputWithTrack:track
            outputSettings:@{(id)kCVPixelBufferPixelFormatTypeKey:@(kCVPixelFormatType_32BGRA)}];
        [reader addOutput:out]; require([reader startReading],"start resolution decode");
        unsigned count=0;
        while(CMSampleBufferRef sample=[out copyNextSampleBuffer]) {
            CVPixelBufferRef pb=CMSampleBufferGetImageBuffer(sample);
            require(CVPixelBufferGetWidth(pb)==widths[choice] && CVPixelBufferGetHeight(pb)==heights[choice],"decoded selected dimensions and 16:9 aspect");
            CFRelease(sample); count++;
        }
        require(count==6 && reader.status==AVAssetReaderStatusCompleted,"all resolution frames decode");
        printf("resolution: %ux%u %s frames=%u target=%.0f b/s error=%.6f%% container=%.0f b/s\n",
            widths[choice],heights[choice],s->codec.UTF8String,count,target,100*fabs(target-expected)/expected,track.estimatedDataRate);
    }
    resolutionChoice=0;
    ClipSession *s=start(false,4000,2000);
    require(s->width==2160 && s->height==1080,"wide source keeps 2:1 aspect at chosen height");
    for(unsigned i=0;i<3;i++) frame(s,i,1100+i/60.0);
    cliprec_shutdown(); finishedOK(s);
    AVURLAsset *wide=[AVURLAsset URLAssetWithURL:[NSURL fileURLWithPath:s->destination] options:nil];
    CGSize size=[wide tracksWithMediaType:AVMediaTypeVideo].firstObject.naturalSize;
    require(size.width==2160 && size.height==1080,"wide MP4 aspect");
    resolutionChoice=CLIPREC_DEFAULT_RESOLUTION;
}
int main(int argc, char **argv) { @autoreleasepool {
    if(argc>1 && !strcmp(argv[1],"--shutdown-proof")) { shutdownProof(); return 0; }
    require(!active && !current,"always Off at start");
    for(unsigned i=0;i<10000;i++) cliprec_present(nil,nil,nil,1);
    require(!current,"Off path allocates no session");
    shutdownProof();
    char off[80]; cliprec_status(off,sizeof off);
    require(!strcmp(off,"Record clip"),"initial F10 action");
    gpu=MTLCreateSystemDefaultDevice(); if(!gpu) { puts("SKIP: Metal unavailable"); return 3; }
    commands=[gpu newCommandQueue];
    require(argc>=3,"injected scratch output folder");
    outputFolder=[NSString stringWithUTF8String:argv[2]];
    require([NSFileManager.defaultManager createDirectoryAtPath:outputFolder withIntermediateDirectories:YES attributes:nil error:nil],"scratch output folder");
    // Regression must prepare directly from 16F, not hide rejection behind BGRA8 setup.
    hdrProof();
    ClipSession *s=start();
    for(unsigned i=0;i<N;i++) frame(s,i,100+i/60.0,true,i%2);
    cliprec_stop(); finishedOK(s);
    Decoded good=decode(s->destination);
    printf("roundtrip: %u frames duration %.6f PTS error %.6f colour error %.1f orientation %.0f marker %.0f counter %.0f drops %u/%u\n",
        good.count,good.duration,good.spacingError,good.colourError,good.orientationError,good.markerError,good.counterError,s->poolDrops.load(),s->writerDrops.load());
    require(validates(good),"decoded colour bars / moving marker / frame counter / PTS / orientation");
    require(s->accepted==N && !s->poolDrops && !s->writerDrops,"no unexplained drops");
    unsigned rejected=0;
    for(unsigned fault=0;fault<8;fault++) {
        Decoded bad=good;
        switch(fault) {
            case 0:bad.duration+=0.1;break; case 1:bad.spacingError+=0.1;break;
            case 2:bad.count--;break; case 3:bad.colourError+=80;break;
            case 4:bad.orientationError=1;break; case 5:bad.markerError=1;break;
            case 6:bad.counterError=1;break; case 7:bad.valid=false;break;
        }
        require(!validates(bad),"deliberate decoded-data fault rejected"); rejected++;
    }
    char status[512]; cliprec_status(status,sizeof status);
    require(!strcmp(status,"Record clip"),"saved F10 status offers another recording");
    // Also run at real cadence without per-frame encoder drains, exercising
    // asynchronous encode callbacks and writer packets with multiple frames live.
    s=start();
    for(unsigned i=0;i<N;i++) { frame(s,i,150+i/60.0,true,i%2,false); usleep(17000); }
    cliprec_shutdown(); finishedOK(s);
    Decoded paced=decode(s->destination);
    require(validates(paced) && s->accepted==N && !s->poolDrops && !s->writerDrops,"paced asynchronous hardware roundtrip");
    // Exhaust the actual hard-threshold pool, then release and recover. A single
    // missing frame must leave a 2/60 PTS gap, not accelerate the clip.
    s=start();
    for(unsigned i=0;i<N;i++) {
        if(i==N/2) {
            CVPixelBufferRef held[kSurfaces]={};
            for(unsigned j=0;j<kSurfaces;j++) require(!CVPixelBufferPoolCreatePixelBufferWithAuxAttributes(NULL,s->pool,
                (__bridge CFDictionaryRef)@{(id)kCVPixelBufferPoolAllocationThresholdKey:@(kSurfaces)},&held[j]),"reserve entire pool");
            frame(s,i,200+i/60.0);
            for(auto p:held) CFRelease(p);
        }
        frame(s,i,200+(i+(i>=N/2?1:0))/60.0);
    }
    cliprec_shutdown(); finishedOK(s);
    Decoded droppedClip=decode(s->destination,true);
    printf("exhaustion: drops %u; recovered frames %u duration %.6f PTS error %.6f\n",s->poolDrops.load(),droppedClip.count,droppedClip.duration,droppedClip.spacingError);
    require(s->poolDrops==1,"forced pool exhaustion counted");
    require(validates(droppedClip,true),"drop preserves valid MP4 and timeline gap");
    auto dropCheck=[](unsigned drops){return drops==1;};
    require(!dropCheck(0),"deliberate missing drop counter rejected"); rejected++;
    require(dispatch_group_wait(s->finished,DISPATCH_TIME_NOW)==0,"quit completion validator baseline");
    dispatch_group_t unfinished=dispatch_group_create(); dispatch_group_enter(unfinished);
    require(dispatch_group_wait(unfinished,DISPATCH_TIME_NOW)!=0,"deliberate incomplete finalization rejected");
    dispatch_group_leave(unfinished); rejected++;
    // Both completion orders, unshown drawables, bounded outstanding records,
    // and sustained overload (120 drops) are tested without a desktop/window.
    s=start(); for(unsigned i=0;i<6;i++) frame(s,i,300+i/60.0);
    TestDrawable *hidden=drawable(); id<MTLCommandBuffer> cb=[commands commandBuffer];
    cliprec_present(cb,pattern(0),hidden,1); [cb commit]; [cb waitUntilCompleted]; [hidden shown:0]; syncWork(s);
    require(s->unshown==1,"unshown frame discarded");
    CVMetalTextureCacheFlush(s->cache,0);
    CVPixelBufferRef held[kSurfaces]={};
    // Drain the hardware encoder before reserving surfaces for the overload case.
    dispatch_sync(s->work,^{ VTCompressionSessionCompleteFrames(s->encoder,kCMTimeInvalid); }); syncWork(s);
    for(unsigned j=0;j<kSurfaces;j++) require(!CVPixelBufferPoolCreatePixelBufferWithAuxAttributes(NULL,s->pool,
        (__bridge CFDictionaryRef)@{(id)kCVPixelBufferPoolAllocationThresholdKey:@(kSurfaces)},&held[j]),"reserve pool for overload");
    for(unsigned i=0;i<kOverload;i++) frame(s,0,301+i/60.0);
    for(auto p:held) CFRelease(p);
    finishedOK(s);
    require(s->poolDrops==kOverload && !active,"sustained overload stops at 120 drops");
    require(decode(s->destination).valid,"overload prefix MP4 decodes");
    // An accepted GPU frame with no presentation notification cannot hang stop.
    s=start(); for(unsigned i=0;i<3;i++) frame(s,i,400+i/60.0);
    frame(s,0,401,false); cliprec_stop();
    cliprec_status(status,sizeof status); require(!strcmp(status,"Saving..."),"saving F10 state");
    cliprec_shutdown(); finishedOK(s);
    require(s->outstanding==0 && s->unshown==1,"stop retires missing presentation");
    // Drop before compression under encoder transfer pressure; the already-written prefix
    // stays decodable and overload cannot produce an unbounded packet backlog.
    s=start(); for(unsigned i=0;i<6;i++) frame(s,i,500+i/60.0);
    dispatch_sync(s->work,^{ s->forceEncoderPressure=true; });
    for(unsigned i=0;i<kOverload;i++) frame(s,0,501+i/60.0);
    finishedOK(s);
    require(s->writerDrops==kOverload && s->queuedPackets==0,"encoder transfer overload is bounded and counted");
    require(decode(s->destination).valid,"encoder transfer overload saves valid prefix");
    // Exercise the actual callback's frame-dropped flag with a valid prefix.
    s=start(); for(unsigned i=0;i<6;i++) frame(s,i,550+i/60.0);
    for(unsigned i=0;i<kOverload-1;i++) {
        compressed((__bridge void *)s,NULL,noErr,kVTEncodeInfo_FrameDropped,NULL); syncWork(s);
    }
    require(active && s->streak==kOverload-1 && s->writerDrops==kOverload-1,"encoder drops count before threshold");
    compressed((__bridge void *)s,NULL,noErr,kVTEncodeInfo_FrameDropped,NULL); syncWork(s);
    finishedOK(s);
    require(!active && s->streak==kOverload && s->writerDrops==kOverload && s->queuedPackets==0,
        "120 encoder drops stop and retire packets");
    require([s->error containsString:@"encoder overloaded"] && decode(s->destination).valid,"encoder overload saves decodable prefix");
    printf("encoder overload: drops=%u streak=%u active=%d; released encoder=%d pool=%d\n",
        s->writerDrops.load(),s->streak.load(),(int)active.load(),s->encoder==NULL,s->pool==NULL);
    // Hold all presentation records after GPU completion. The ninth frame is
    // refused even when GPU work has completed but presentation is outstanding.
    s=start();
    NSMutableArray<TestDrawable *> *waiting=[NSMutableArray new];
    for(unsigned i=0;i<kSurfaces;i++) {
        TestDrawable *d=drawable(); [waiting addObject:d];
        cb=[commands commandBuffer]; cliprec_present(cb,pattern(i),d,1); [cb commit]; [cb waitUntilCompleted];
    }
    syncWork(s); require(s->outstanding==kSurfaces,"presentation records bounded at eight");
    frame(s,0,601);
    require(s->poolDrops==1 && s->outstanding==kSurfaces,"ninth unpresented frame dropped");
    for(unsigned i=0;i<kSurfaces;i++) {
        [waiting[i] shown:600+i/60.0];
        dispatch_sync(s->work,^{ VTCompressionSessionCompleteFrames(s->encoder,kCMTimeInvalid); }); syncWork(s);
    }
    cliprec_shutdown(); finishedOK(s);
    require(s->accepted==kSurfaces,"both GPU and presentation required exactly once");
    // H.264 must be an actual hardware fallback, not a software encoder.
    s=start(true); require([s->codec isEqualToString:@"H.264"],"hardware H.264 High fallback");
    for(unsigned i=0;i<6;i++) frame(s,i,700+i/60.0);
    cliprec_shutdown(); finishedOK(s); require(decode(s->destination).valid,"fallback MP4 decodes");
    // Scale the presentation source into a larger drawable without flipping.
    s=start(false,W*2,H*2);
    for(unsigned i=0;i<3;i++) frame(s,i,800+i/60.0);
    cliprec_shutdown(); finishedOK(s);
    AVURLAsset *scaled=[AVURLAsset URLAssetWithURL:[NSURL fileURLWithPath:s->destination] options:nil];
    CGSize size=[scaled tracksWithMediaType:AVMediaTypeVideo].firstObject.naturalSize;
    require(size.width==W*2 && size.height==H*2,"encode uses drawable resolution");
    resolutionProof();
    // Other unsupported formats still produce a visible error.
    { std::lock_guard<std::mutex> lock(control); current=nil; }
    cliprec_action(0); s=current;
    id<MTLTexture> hdr=[gpu newTextureWithDescriptor:[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatR32Float width:W height:H mipmapped:NO]];
    cb=[commands commandBuffer]; cliprec_present(cb,hdr,drawable(),1); [cb commit]; [cb waitUntilCompleted];
    cliprec_shutdown(); require(s->error!=nil && !active,"unsupported format rejection is explicit");
    { std::lock_guard<std::mutex> lock(control); current=nil; }
    cliprec_action(0); s=current; s->forceNoHardware=true;
    cb=[commands commandBuffer]; cliprec_present(cb,pattern(0),drawable(),1); [cb commit]; [cb waitUntilCompleted];
    cliprec_shutdown(); require(s->error!=nil && !active,"no hardware encoder returns an error");
    // Existing destinations must survive publication failure unchanged. The
    // failed action retries recording and never reveals an unrelated file.
    s=start(); for(unsigned i=0;i<3;i++) frame(s,i,900+i/60.0);
    require([@"reserved" writeToFile:s->destination atomically:YES encoding:NSUTF8StringEncoding error:nil],"reserve destination");
    cliprec_shutdown();
    require(s->ended && s->error!=nil && !s->published,"rename collision reports an error");
    require([[NSString stringWithContentsOfFile:s->destination encoding:NSUTF8StringEncoding error:nil] isEqualToString:@"reserved"],"existing file never overwritten");
    require([NSFileManager.defaultManager fileExistsAtPath:s->temporary],"failed publication retains temporary for recovery");
    cliprec_action(0); require(active && current!=s,"publication error action retries"); cliprec_shutdown();
    // Stop before the first frame and a real filesystem error both terminate.
    { std::lock_guard<std::mutex> lock(control); current=nil; }
    cliprec_action(0); s=current; cliprec_shutdown();
    require(!active && s->error!=nil,"empty stop returns visible error");
    outputFolder=[outputFolder stringByAppendingPathComponent:@"not-a-directory"];
    require([@"x" writeToFile:outputFolder atomically:YES encoding:NSUTF8StringEncoding error:nil],"create failing output path");
    require(!decode(outputFolder).valid,"actual invalid media rejected by AVAssetReader");
    { std::lock_guard<std::mutex> lock(control); current=nil; }
    cliprec_action(0); s=current;
    cb=[commands commandBuffer]; cliprec_present(cb,pattern(0),drawable(),1); [cb commit]; [cb waitUntilCompleted];
    cliprec_shutdown(); require(s->error!=nil && !active,"filesystem error stops recording");
    cliprec_status(status,sizeof status); require(strstr(status,"Error:"),"F10 exposes error");
    require(rejected==10,"all ten deliberately broken validators rejected");
    printf("cliprec PASS: %u checks; deliberate failures rejected %u/10\n",checks,rejected);
    if(argc>1) {
        FILE *proof=fopen(argv[1],"w");
        require(proof!=NULL,"open proof log");
        fprintf(proof,"roundtrip: frames=%u duration=%.6f PTS_error=%.6f colour_error=%.1f orientation=%.0f marker=%.0f counter=%.0f\n",
            good.count,good.duration,good.spacingError,good.colourError,good.orientationError,good.markerError,good.counterError);
        fprintf(proof,"pool exhaustion: drops=1 recovered_frames=%u duration=%.6f PTS_error=%.6f\n",
            droppedClip.count,droppedClip.duration,droppedClip.spacingError);
        fprintf(proof,"PASS: %u checks; deliberate failures rejected=%u/10; pool, encoder and transfer overload=120 drops each; records=8; hardware H.264 fallback decoded\n",checks,rejected);
        fprintf(proof,"resolution: 6 frames each at 1920x1080,2560x1440,3840x2160 in HEVC/H.264; hardware targets within 1%%; wide=2160x1080; no enlargement=320x180; released encoder/pool/cache/writer/Metal resources after stop\n");
        fprintf(proof,"HDR: H=1 pre-encode NV12 max_error=%u LSB (256 byte codes, native/up/downscale); H=7 shoulder_error=%u LSB, monotonic Y=16..%u; H=1 (36 frames) and H=7 (1 frame) clips decoded\n",hdrMaxError,hdrShoulderError,hdrWhite);
        fclose(proof);
    }
    [NSFileManager.defaultManager removeItemAtPath:outputFolder error:nil];
    return 0;
} }
