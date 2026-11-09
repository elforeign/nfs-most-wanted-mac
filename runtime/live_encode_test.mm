// Real VT/Metal capture plus independent H.264 header and picture decoding.
// Fixed: 361 decoded pictures, 4 IDRs / 120-frame gaps, no reorder; long GOP
// gives 240 frames, Main SPS 77 vs High 100. CBR 472222 vs absent; reordering
// 0 vs 1. 1080p/4K hardware CBR: 17000000/50000000. Rejected properties 10/10;
// ignored rejection status 0 vs error. Shared pool unavailable until 4 live
// callbacks retire; borrowed control has 0 refs and makes pool available early.
// HEVC accepted=0 vs 1. Capture recording/replay/live=181/181/181 vs removed
// hook 181/181/0. Off: 3110400 identical NV12 bytes, 0 live sessions/submissions;
// faulty luma changes 2073600 bytes. All measured controls run in the driver.
#import <Foundation/Foundation.h>
#import <VideoToolbox/VideoToolbox.h>
#include <mutex>
#include <vector>
#include <string>
#include <cstdio>
#include <thread>
#include <chrono>
static unsigned liveCreates,liveSubmits,liveRetains,liveReleases;
static CFStringRef rejectProperty;
static bool deferOutput,failSubmission,rejectHardware,failCreation;
static VTCompressionSessionRef latestSession;
static VTCompressionOutputCallback liveCallback;
struct HeldOutput { void *context,*source; OSStatus status; VTEncodeInfoFlags flags; CMSampleBufferRef sample; };
static std::mutex heldLock;
static std::vector<HeldOutput> held;
static std::vector<std::pair<std::string,std::string>> negotiated;
static void liveOutput(void *context,void *source,OSStatus status,VTEncodeInfoFlags flags,CMSampleBufferRef sample) {
    if(deferOutput) {
        if(sample) CFRetain(sample);
        std::lock_guard<std::mutex> lock(heldLock);
        held.push_back({context,source,status,flags,sample});
    } else liveCallback(context,source,status,flags,sample);
}
static OSStatus liveCreate(CFAllocatorRef a,int32_t w,int32_t h,CMVideoCodecType type,CFDictionaryRef spec,
        CFDictionaryRef attrs,CFAllocatorRef compressed,VTCompressionOutputCallback callback,void *context,VTCompressionSessionRef *session) {
    ++liveCreates; liveCallback=callback;
    if(failCreation) return kVTCouldNotFindVideoEncoderErr;
    if(CFDictionaryGetValue(spec,kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder)!=kCFBooleanTrue)
        return kVTCouldNotFindVideoEncoderErr;
    OSStatus status=VTCompressionSessionCreate(a,w,h,type,spec,attrs,compressed,liveOutput,context,session);
    latestSession=*session;
    return status;
}
static OSStatus liveSet(VTSessionRef s,CFStringRef key,CFTypeRef value) {
    negotiated.emplace_back([(__bridge NSString*)key UTF8String],[[(__bridge id)value description] UTF8String]);
    if(rejectProperty && CFEqual(key,rejectProperty)) return kVTPropertyNotSupportedErr;
    return VTSessionSetProperty(s,key,value);
}
static OSStatus liveCopy(VTSessionRef session,CFStringRef key,CFAllocatorRef allocator,CFTypeRef *value) {
    if(rejectHardware && CFEqual(key,kVTCompressionPropertyKey_UsingHardwareAcceleratedVideoEncoder)) {
        *value=CFRetain(kCFBooleanFalse); return noErr;
    }
    return VTSessionCopyProperty(session,key,allocator,value);
}
static OSStatus liveSubmit(VTCompressionSessionRef s,CVImageBufferRef image,CMTime pts,CMTime duration,
        CFDictionaryRef options,void *context,VTEncodeInfoFlags *flags) {
    ++liveSubmits;
    if(failSubmission) return kVTVideoEncoderMalfunctionErr;
    return VTCompressionSessionEncodeFrame(s,image,pts,duration,options,context,flags);
}
static CVPixelBufferRef liveRetain(CVPixelBufferRef pixel) { ++liveRetains; return CVPixelBufferRetain(pixel); }
static void liveRelease(CVPixelBufferRef pixel) { ++liveReleases; CVPixelBufferRelease(pixel); }
#define VTCompressionSessionCreate liveCreate
#define VTSessionSetProperty liveSet
#define VTSessionCopyProperty liveCopy
#define VTCompressionSessionEncodeFrame liveSubmit
#define CVPixelBufferRetain liveRetain
#define CVPixelBufferRelease liveRelease
#ifndef LIVE_ENCODE_SOURCE
#define LIVE_ENCODE_SOURCE "live_encode.mm"
#endif
#include LIVE_ENCODE_SOURCE
#undef VTCompressionSessionCreate
#undef VTSessionSetProperty
#undef VTSessionCopyProperty
#undef VTCompressionSessionEncodeFrame
#undef CVPixelBufferRetain
#undef CVPixelBufferRelease
#define main recorder_main
#include "cliprec_test.mm"
#undef main

static void check(bool ok,const char *message) {
    if(!ok) { fprintf(stderr,"FAIL live: %s\n",message); exit(1); }
}
static CVPixelBufferRef image(unsigned w=320,unsigned h=180,OSType type=kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange) {
    CVPixelBufferRef pixel=nullptr;
    NSDictionary *attrs=@{(id)kCVPixelBufferIOSurfacePropertiesKey:@{}};
    check(!CVPixelBufferCreate(NULL,w,h,type,(__bridge CFDictionaryRef)attrs,&pixel),"fixture pixel allocation");
    CVPixelBufferLockBaseAddress(pixel,0);
    for(unsigned p=0;p<CVPixelBufferGetPlaneCount(pixel);++p)
        memset(CVPixelBufferGetBaseAddressOfPlane(pixel,p),p?128:80,CVPixelBufferGetBytesPerRowOfPlane(pixel,p)*CVPixelBufferGetHeightOfPlane(pixel,p));
    CVPixelBufferUnlockBaseAddress(pixel,0);
    return pixel;
}
struct Collected {
    std::mutex lock;
    std::vector<clip::OwnedPacket> packets;
    std::shared_ptr<clip::Sink> sink() {
        return std::make_shared<clip::Sink>([this](const clip::OwnedPacket &p) {
            std::lock_guard<std::mutex> guard(lock); packets.push_back(p); return clip::Result::consumed;
        },1024,32*1024*1024);
    }
};
static void drained(const std::shared_ptr<clip::Sink>& sink) {
    for(unsigned i=0;i<10000 && !sink->drained() && !sink->failed();++i) usleep(1000);
    check(sink->drained() && !sink->failed(),"bounded output sink drain");
}
static std::vector<uint8_t> payload(CMSampleBufferRef sample) {
    auto block=CMSampleBufferGetDataBuffer(sample); std::vector<uint8_t> data(CMBlockBufferGetDataLength(block));
    OSStatus status=CMBlockBufferCopyDataBytes(block,0,data.size(),data.data());
    if(status) fprintf(stderr,"payload copy status=%d bytes=%zu samples=%ld ready=%d\n",(int)status,data.size(),(long)CMSampleBufferGetNumSamples(sample),CMSampleBufferDataIsReady(sample));
    check(!status,"copy compressed payload"); return data;
}
static void releaseHeld() {
    std::vector<HeldOutput> output;
    { std::lock_guard<std::mutex> lock(heldLock); output.swap(held); }
    for(auto p:output) { liveCallback(p.context,p.source,p.status,p.flags,p.sample); if(p.sample) CFRelease(p.sample); }
}
static void negotiatedProfile() {
    auto value=[](const char *key,const char *expected) {
        for(auto &p:negotiated) if(p.first==key) {
            printf("property %s=%s expected=%s\n",key,p.second.c_str(),expected); fflush(stdout);
            return p.second==expected;
        }
        printf("property %s=missing expected=%s\n",key,expected); fflush(stdout);
        return false;
    };
    check(value("RealTime","1"),"real-time mode negotiated");
    check(value("AllowFrameReordering","0"),"frame reordering disabled");
    check(value("ExpectedFrameRate","60"),"60 fps expectation negotiated");
    check(value("ConstantBitRate","472222"),"explicit CBR 472222 negotiated");
    for(auto &p:negotiated) check(p.first!="AverageBitRate" && p.first!="DataRateLimits","CBR has no conflicting rate control");
}
struct DecodeProof { unsigned frames=0,width=320,height=180; };
static void decodedPicture(void *context,void*,OSStatus status,VTDecodeInfoFlags,CVImageBufferRef image,CMTime,CMTime) {
    auto &proof=*(DecodeProof*)context;
    check(!status && image && CVPixelBufferGetWidth(image)==proof.width && CVPixelBufferGetHeight(image)==proof.height,"independently decoded H.264 dimensions");
    ++((DecodeProof*)context)->frames;
}
static void headers(const char *path) {
    Collected output; auto sink=output.sink();
    live::Encoder encoder(live::Profile::youtube(320,180),sink);
    printf("negotiation status=%d\n",(int)encoder.status()); fflush(stdout);
    check(!encoder.status(),"hardware H.264 CBR available"); negotiatedProfile();
    check(live::Profile::youtube(1920,1080).bitrate==17000000 && live::Profile::youtube(3840,2160).bitrate==50000000,
        "YouTube 1080p60 and 2160p60 anchors");
    for(unsigned i=0;i<361;++i) {
        auto pixel=image(); check(encoder.encode(pixel,CMTimeMake(i,60),CMTimeMake(1,60)),"live frame submission");
        CVPixelBufferRelease(pixel); encoder.complete();
    }
    drained(sink);
    check(!encoder.status() && encoder.frames()==361 && output.packets.size()==361,"361 live packets delivered");
    check(!encoder.pending() && liveRetains==361 && liveReleases==361,"all live surface references retired");
    FILE *file=fopen(path,"wb"); check(file,"Annex B fixture output");
    DecodeProof decoded;
    VTDecompressionSessionRef decoder=nullptr;
    VTDecompressionOutputCallbackRecord callback={decodedPicture,&decoded};
    check(!VTDecompressionSessionCreate(NULL,output.packets[0]->format(),NULL,NULL,&callback,&decoder),"independent H.264 decoder");
    unsigned idrs=0;
    double lastIDR=-2; unsigned maxGap=0;
    for(unsigned i=0;i<output.packets.size();++i) {
        auto &p=output.packets[i]; auto sample=p->sample();
        check(CMFormatDescriptionGetMediaSubType(p->format())==kCMVideoCodecType_H264,"live route is H.264");
        CMTime pts=CMSampleBufferGetPresentationTimeStamp(sample),dts=CMSampleBufferGetDecodeTimeStamp(sample);
        check(CMTimeCompare(pts,CMTimeMake(i,60))==0,"presentation order unchanged");
        check(!CMTIME_IS_NUMERIC(dts) || CMTimeCompare(dts,pts)==0,"no decode reordering");
        std::vector<uint8_t> annex;
        auto nal=[&](const uint8_t *data,size_t size) {
            annex.insert(annex.end(),{0,0,0,1}); annex.insert(annex.end(),data,data+size);
        };
        int length=0; const uint8_t *sps=nullptr; size_t size=0,count=0;
        check(!CMVideoFormatDescriptionGetH264ParameterSetAtIndex(p->format(),0,&sps,&size,&count,&length) && count>=2 && length==4,"real SPS/PPS format");
        if(i==0) for(size_t j=0;j<count;++j) {
            check(!CMVideoFormatDescriptionGetH264ParameterSetAtIndex(p->format(),j,&sps,&size,NULL,NULL),"read parameter set"); nal(sps,size);
        }
        auto data=payload(sample); bool idr=false;
        for(size_t pos=0;pos<data.size();) {
            check(pos+4<=data.size(),"NAL length header");
            size_t size=(size_t)data[pos]<<24|(size_t)data[pos+1]<<16|(size_t)data[pos+2]<<8|data[pos+3]; pos+=4;
            check(size && size<=data.size()-pos,"NAL payload bounds");
            idr|=(data[pos]&31)==5; nal(data.data()+pos,size); pos+=size;
        }
        if(idr) {
            double time=CMTimeGetSeconds(pts);
            printf("IDR %u at %.6f s expected %.6f\n",idrs,time,idrs*2.0); fflush(stdout);
            check(fabs(time-idrs*2.0)<1.0/60000,"IDR exactly every two seconds");
            maxGap=std::max(maxGap,(unsigned)llround((time-lastIDR)*60)); lastIDR=time; ++idrs;
        }
        fwrite(annex.data(),1,annex.size(),file);
        check(!VTDecompressionSessionDecodeFrame(decoder,sample,0,NULL,NULL),"independent decoder accepts live packet");
    }
    check(!VTDecompressionSessionWaitForAsynchronousFrames(decoder),"independent decoder drain"); fclose(file);
    check(decoded.frames==361 && idrs==4,"decoded frame and IDR counts");
    printf("headers: decoded=%u IDRs=%u gap=%u frames bytes=%llu refs=%u/%u\n",decoded.frames,idrs,maxGap,(unsigned long long)encoder.bytes(),liveRetains,liveReleases);
    VTDecompressionSessionInvalidate(decoder); CFRelease(decoder);
}
static void profileSizes(const char *path) {
    for(unsigned index=0;index<2;++index) {
        unsigned width=index?3840:1920,height=index?2160:1080;
        auto profile=live::Profile::youtube(width,height);
        Collected output; auto sink=output.sink(); live::Encoder encoder(profile,sink);
        check(!encoder.status(),"1080p/4K hardware CBR profile available");
        CFTypeRef rate=nullptr;
        check(!VTSessionCopyProperty(latestSession,kVTCompressionPropertyKey_ConstantBitRate,NULL,&rate) && rate,"read negotiated CBR");
        int actual=0; CFNumberGetValue((CFNumberRef)rate,kCFNumberIntType,&actual); CFRelease(rate);
        check(actual==(index?50000000:17000000),"1080p/4K negotiated bitrate matches guidance");
        auto pixel=image(width,height); check(encoder.encode(pixel,kCMTimeZero,CMTimeMake(1,60)),"1080p/4K frame submission");
        CVPixelBufferRelease(pixel); encoder.complete(); drained(sink);
        check(output.packets.size()==1,"1080p/4K live packet count");
        auto sample=output.packets[0]->sample(); auto format=output.packets[0]->format();
        DecodeProof decoded{0,width,height}; VTDecompressionSessionRef decoder=nullptr;
        VTDecompressionOutputCallbackRecord callback={decodedPicture,&decoded};
        check(!VTDecompressionSessionCreate(NULL,format,NULL,NULL,&callback,&decoder),"1080p/4K independent decoder");
        check(!VTDecompressionSessionDecodeFrame(decoder,sample,0,NULL,NULL) && !VTDecompressionSessionWaitForAsynchronousFrames(decoder) && decoded.frames==1,"1080p/4K decoded picture");
        VTDecompressionSessionInvalidate(decoder); CFRelease(decoder);
        std::string filename=std::string(path)+(index?"-2160.h264":"-1080.h264");
        FILE *file=fopen(filename.c_str(),"wb"); check(file,"1080p/4K bitstream output");
        const uint8_t start[]={0,0,0,1}; const uint8_t *data=nullptr; size_t size=0,count=0; int length=0;
        check(!CMVideoFormatDescriptionGetH264ParameterSetAtIndex(format,0,&data,&size,&count,&length) && length==4,"1080p/4K parameter sets");
        for(size_t i=0;i<count;++i) {
            check(!CMVideoFormatDescriptionGetH264ParameterSetAtIndex(format,i,&data,&size,NULL,NULL),"1080p/4K SPS/PPS bytes");
            fwrite(start,1,4,file); fwrite(data,1,size,file);
        }
        auto bytes=payload(sample);
        for(size_t pos=0;pos<bytes.size();) {
            check(pos+4<=bytes.size(),"1080p/4K NAL header");
            size=(size_t)bytes[pos]<<24|(size_t)bytes[pos+1]<<16|(size_t)bytes[pos+2]<<8|bytes[pos+3]; pos+=4;
            check(size && size<=bytes.size()-pos,"1080p/4K NAL bounds");
            fwrite(start,1,4,file); fwrite(bytes.data()+pos,1,size,file); pos+=size;
        }
        fclose(file); printf("profile: %ux%u CBR requested=%d negotiated=%d decoded=1\n",width,height,profile.bitrate,actual);
    }
}
static void negotiation() {
    CFStringRef keys[]={kVTCompressionPropertyKey_ProfileLevel,kVTCompressionPropertyKey_RealTime,
        kVTCompressionPropertyKey_AllowFrameReordering,kVTCompressionPropertyKey_ExpectedFrameRate,
        kVTCompressionPropertyKey_ConstantBitRate,kVTCompressionPropertyKey_MaxKeyFrameInterval,
        kVTCompressionPropertyKey_MaxKeyFrameIntervalDuration,kVTCompressionPropertyKey_ColorPrimaries,
        kVTCompressionPropertyKey_TransferFunction,kVTCompressionPropertyKey_YCbCrMatrix};
    for(auto key:keys) {
        rejectProperty=key; live::Encoder encoder(live::Profile::youtube(320,180));
        printf("rejected %s status=%d pending=%u\n",[(__bridge NSString*)key UTF8String],(int)encoder.status(),encoder.pending());
        check(encoder.status()==kVTPropertyNotSupportedErr && !encoder.pending(),"property rejection stops live setup");
        auto pixel=image(); check(!encoder.encode(pixel,kCMTimeZero,CMTimeMake(1,60)),"failed session cannot submit"); CVPixelBufferRelease(pixel);
    }
    check(!liveSubmits && !liveRetains,"failed negotiation has zero submissions and references");
    rejectProperty=nullptr; rejectHardware=true;
    { live::Encoder encoder(live::Profile::youtube(320,180));
        printf("hardware query rejection status=%d\n",(int)encoder.status()); fflush(stdout);
        check(encoder.status()!=0,"hardware query rejection stops live setup"); }
    rejectHardware=false; failCreation=true;
    { live::Encoder encoder(live::Profile::youtube(320,180));
        printf("hardware creation rejection status=%d\n",(int)encoder.status()); fflush(stdout);
        check(encoder.status()==kVTCouldNotFindVideoEncoderErr,"hardware creation rejection has no fallback"); }
    failCreation=false;
    printf("negotiation: rejected=10 properties + hardware query/create; submissions=0 refs=0\n");
}
static void dimensions() {
    for(unsigned which=0;which<3;++which) {
        live::Encoder encoder(live::Profile::youtube(320,180)); check(!encoder.status(),"dimension fixture encoder");
        auto pixel=image(which==0?322:320,which==1?182:180,which==2?kCVPixelFormatType_32BGRA:kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange);
        bool accepted=encoder.encode(pixel,kCMTimeZero,CMTimeMake(1,60));
        printf("dimension fault %u: accepted=%d status=%d submissions=%u\n",which,accepted,(int)encoder.status(),liveSubmits); fflush(stdout);
        check(!accepted && encoder.status(),"mismatched surface rejected before submission");
        CVPixelBufferRelease(pixel);
    }
    printf("dimensions: rejected=3 submissions=%u\n",liveSubmits); check(!liveSubmits,"mismatches never reach VT");
}
static void recordingComplete(void *context,void*,OSStatus status,VTEncodeInfoFlags,CMSampleBufferRef sample) {
    check(!status && sample,"simultaneous recording encoder output"); ++*(unsigned*)context;
}
static void lifetime() {
    live::Encoder encoder(live::Profile::youtube(320,180)); check(!encoder.status(),"lifetime fixture encoder");
    CVPixelBufferPoolRef pool=nullptr;
    NSDictionary *attrs=@{(id)kCVPixelBufferWidthKey:@320,(id)kCVPixelBufferHeightKey:@180,
        (id)kCVPixelBufferPixelFormatTypeKey:@(kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange),(id)kCVPixelBufferIOSurfacePropertiesKey:@{}};
    check(!CVPixelBufferPoolCreate(NULL,NULL,(__bridge CFDictionaryRef)attrs,&pool),"lifetime pool");
    NSDictionary *limit=@{(id)kCVPixelBufferPoolAllocationThresholdKey:@4};
    unsigned recordingFrames=0;
    VTCompressionSessionRef recording=nullptr;
    NSDictionary *hardware=@{(id)kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder:@YES};
    check(!VTCompressionSessionCreate(NULL,320,180,kCMVideoCodecType_HEVC,(__bridge CFDictionaryRef)hardware,
        (__bridge CFDictionaryRef)attrs,NULL,recordingComplete,&recordingFrames,&recording),"simultaneous recording encoder");
    check(!VTSessionSetProperty(recording,kVTCompressionPropertyKey_AllowFrameReordering,kCFBooleanFalse),"recording no reordering");
    deferOutput=true;
    for(unsigned i=0;i<4;++i) {
        CVPixelBufferRef pixel=nullptr; check(!CVPixelBufferPoolCreatePixelBufferWithAuxAttributes(NULL,pool,(__bridge CFDictionaryRef)limit,&pixel),"reserve lifetime surface");
        check(!VTCompressionSessionEncodeFrame(recording,pixel,CMTimeMake(i,60),CMTimeMake(1,60),NULL,NULL,NULL),"simultaneous recording submission");
        check(encoder.encode(pixel,CMTimeMake(i,60),CMTimeMake(1,60)),"delayed live submission"); CVPixelBufferRelease(pixel);
    }
    encoder.complete();
    check(!VTCompressionSessionCompleteFrames(recording,kCMTimeInvalid) && recordingFrames==4,"recording encoder completed all shared surfaces");
    VTCompressionSessionInvalidate(recording); CFRelease(recording);
    printf("delayed callbacks: pending=%u retained=%u released=%u held=%zu\n",encoder.pending(),liveRetains,liveReleases,held.size()); fflush(stdout);
    check(encoder.pending()==4 && held.size()==4,"live callbacks remain delayed");
    CVPixelBufferRef extra=nullptr;
    CVReturn poolStatus=CVPixelBufferPoolCreatePixelBufferWithAuxAttributes(NULL,pool,(__bridge CFDictionaryRef)limit,&extra);
    printf("shared surfaces: recorded=%u pool_available=%d (required 0)\n",recordingFrames,poolStatus==kCVReturnSuccess); fflush(stdout);
    check(poolStatus==kCVReturnWouldExceedAllocationThreshold,"completed recording cannot recycle live surfaces");
    check(liveRetains==4 && liveReleases==0,"live holds four surfaces through delayed callbacks");
    releaseHeld(); deferOutput=false;
    check(!encoder.pending() && liveReleases==4,"all delayed callbacks release references");
    check(!CVPixelBufferPoolCreatePixelBufferWithAuxAttributes(NULL,pool,(__bridge CFDictionaryRef)limit,&extra),"pool reusable after both encoders release");
    CVPixelBufferRelease(extra); CFRelease(pool);
    failSubmission=true; auto pixel=image(); live::Encoder failed(live::Profile::youtube(320,180));
    check(!failed.encode(pixel,kCMTimeZero,CMTimeMake(1,60)) && failed.status() && !failed.pending(),"submission error retires its surface reference");
    CVPixelBufferRelease(pixel); check(liveRetains==liveReleases,"submission failure balances references");
    printf("lifetime: blocked=4 recycled=4 submission-error pending=0 refs=%u/%u\n",liveRetains,liveReleases);
}
static void overload() {
    live::Encoder encoder(live::Profile::youtube(320,180));
    deferOutput=true;
    for(unsigned i=0;i<4;++i) {
        auto pixel=image(); check(encoder.encode(pixel,CMTimeMake(i,60),CMTimeMake(1,60)),"bounded delayed live submission"); CVPixelBufferRelease(pixel);
    }
    encoder.complete();
    auto pixel=image(); bool accepted=encoder.encode(pixel,CMTimeMake(4,60),CMTimeMake(1,60)); CVPixelBufferRelease(pixel);
    printf("overload: fifth accepted=%d pending=%u status=%d refs=%u\n",accepted,encoder.pending(),(int)encoder.status(),liveRetains); fflush(stdout);
    check(!accepted && encoder.pending()==4 && encoder.status() && liveRetains==4,"live overload leaves four recording surfaces available");
    releaseHeld(); deferOutput=false;
    check(!encoder.pending() && liveRetains==liveReleases,"live overload retires all surface references");
}
static void routing() {
    live::Encoder encoder(live::Profile::youtube(320,180));
    CMVideoFormatDescriptionRef format=nullptr; CMSampleBufferRef sample=nullptr; CMBlockBufferRef block=nullptr;
    check(!CMVideoFormatDescriptionCreate(NULL,kCMVideoCodecType_HEVC,320,180,NULL,&format),"HEVC routing fixture format");
    check(!CMBlockBufferCreateWithMemoryBlock(NULL,NULL,16,NULL,NULL,0,16,0,&block),"HEVC routing fixture bytes");
    size_t size=16; CMSampleTimingInfo timing={CMTimeMake(1,60),kCMTimeZero,kCMTimeInvalid};
    check(!CMSampleBufferCreateReady(NULL,block,format,1,1,&timing,1,&size,&sample),"HEVC routing fixture packet");
    bool result=encoder.packet(sample);
    printf("HEVC route: accepted=%d packets=%llu status=%d\n",result,(unsigned long long)encoder.frames(),(int)encoder.status()); fflush(stdout);
    check(!result && encoder.status() && !encoder.frames(),"HEVC routing rejected"); CFRelease(sample); CFRelease(block); CFRelease(format);
}
static void captureProof(const char *mode,const char *path) {
    bool on=!strcmp(mode,"capture") || !strcmp(mode,"capture-failed");
    bool fail=!strcmp(mode,"capture-failed");
    if(on) setenv("NFS_LIVE_ENCODE","on",1); else if(!strcmp(mode,"off")) setenv("NFS_LIVE_ENCODE","off",1); else unsetenv("NFS_LIVE_ENCODE");
    setenv("NFS_REPLAY","on",1);
    outputFolder=[NSString stringWithFormat:@"%s-clips",path];
    gpu=MTLCreateSystemDefaultDevice(); check(gpu!=nil,"capture Metal device"); commands=[gpu newCommandQueue];
    Collected livePackets,recorded; auto liveSink=livePackets.sink(),recordSink=recorded.sink();
    if(fail) rejectProperty=kVTCompressionPropertyKey_ConstantBitRate;
    ClipSession *s=start(false,W,H,nil,1,[&] { current->liveOutput=liveSink; });
    check(s->codec && [s->codec isEqualToString:@"HEVC"],"recording retains HEVC profile");
    printf("activation: requested=%d live_sessions=%d\n",on,(bool)s->liveEncoder); fflush(stdout);
    check((bool)s->liveEncoder==on,"live session initially Off");
    dispatch_sync(s->work,^{ s->outputs.add(recordSink); });
    unsigned count=on?181:36;
    FILE *input=fopen(path,"wb"); check(input,"capture input bytes output");
    for(unsigned i=0;i<count;++i) {
        auto bytes=capture(s,pattern(i%N),100+i/60.0);
        fwrite(bytes.data(),1,bytes.size(),input);
        dispatch_sync(s->work,^{ if(s->liveEncoder) s->liveEncoder->complete(); });
    }
    fclose(input);
    drained(liveSink); drained(recordSink);
    for(unsigned i=0;i<10000 && s->replayRing->stats().packets<count;++i) usleep(1000);
    check(s->replayRing && s->replayRing->stats().packets==count,"replay survives simultaneous live encoding");
    printf("capture observation: recording=%zu replay=%zu live=%llu status=%d\n",recorded.packets.size(),s->replayRing->stats().packets,
        s->liveEncoder?(unsigned long long)s->liveEncoder->frames():0,s->liveEncoder?(int)s->liveEncoder->status():0); fflush(stdout);
    if(on) check(s->liveEncoder->frames()==(fail?0:count) && (bool)s->liveEncoder->status()==fail,"live failure isolated from recording and replay");
    else check(liveCreates==0 && liveSubmits==0 && liveRetains==0 && livePackets.packets.empty(),"Off has zero live sessions submissions and references");
    check(recorded.packets.size()==count,"recording packet count unchanged");
    printf("capture: mode=%s recorded=%zu replay=%zu live=%zu creates=%u submissions=%u refs=%u/%u\n",mode,recorded.packets.size(),s->replayRing->stats().packets,livePackets.packets.size(),liveCreates,liveSubmits,liveRetains,liveReleases); fflush(stdout);
    cliprec_shutdown(); check(!s->liveEncoder,"shutdown retires live encoder");
    auto asset=[AVURLAsset URLAssetWithURL:[NSURL fileURLWithPath:s->destination] options:nil];
    NSError *error=nil;
    auto reader=[AVAssetReader assetReaderWithAsset:asset error:&error];
    auto output=[AVAssetReaderTrackOutput assetReaderTrackOutputWithTrack:[asset tracksWithMediaType:AVMediaTypeVideo][0] outputSettings:nil];
    [reader addOutput:output]; check([reader startReading],"read compressed file packets");
    size_t unchanged=0;
    while(auto sample=[output copyNextSampleBuffer]) {
        // AVAssetReader can emit a zero-sample endpoint marker with no payload.
        if(!CMSampleBufferGetNumSamples(sample)) { CFRelease(sample); continue; }
        check(unchanged<recorded.packets.size() && payload(sample)==payload(recorded.packets[unchanged]->sample()),"recording preserves every compressed byte");
        ++unchanged; CFRelease(sample);
    }
    printf("file bytes unchanged: %zu/%zu packets\n",unchanged,recorded.packets.size());
    check(unchanged==recorded.packets.size(),"all compressed packets verified");
}
static void liveFileFailureProof(const char *path) {
    setenv("NFS_LIVE_ENCODE","on",1); setenv("NFS_REPLAY","off",1);
    outputFolder=[NSString stringWithUTF8String:path];
    gpu=MTLCreateSystemDefaultDevice(); check(gpu!=nil,"capture Metal device"); commands=[gpu newCommandQueue];
    Collected livePackets; auto liveSink=livePackets.sink();
    ClipSession *s=start(false,W,H,nil,1,[&] { current->liveOutput=liveSink; });
    check(s->liveSegments && !s->replaySink,"live audio tap without replay");
    for(unsigned i=0;i<6;++i) frame(s,i,100+i/60.0);
    dispatch_sync(s->work,^{ s->fileSink->cancel(); [s pump]; });
    printf("failed local file: active=%d error=%d live_tap=1\n",(int)active.load(),s->error!=nil); fflush(stdout);
    check(active && !s->stopping && !s->error,"file failure leaves healthy live capture active");
    for(unsigned i=6;i<66;++i) frame(s,i%N,100+i/60.0);
    dispatch_sync(s->work,^{ s->liveEncoder->complete(); });
    drained(s->liveSegments->video); drained(liveSink);
    auto stats=s->liveSegments->stats();
    printf("failed local file: live=%llu/66 observed=%zu segmented=%llu failed=%d\n",
        (unsigned long long)s->liveEncoder->frames(),livePackets.packets.size(),(unsigned long long)stats.video,stats.failed); fflush(stdout);
    check(s->fileSink->failed() && !s->liveEncoder->status() && s->liveEncoder->frames()==66 &&
        livePackets.packets.size()==66 && stats.video==66 && !stats.failed,"file failure preserves every live and segment packet");
    cliprec_shutdown();
    check(s->error && [s->error containsString:@"File packet sink failed"],"failed file error survives healthy live shutdown");
}
int main(int argc,char **argv) { @autoreleasepool {
    check(argc==3,"mode and output path");
    std::string mode=argv[1];
    if(mode=="headers") headers(argv[2]); else if(mode=="sizes") profileSizes(argv[2]); else if(mode=="negotiation") negotiation();
    else if(mode=="overload") overload(); else if(mode=="dimensions") dimensions(); else if(mode=="lifetime") lifetime(); else if(mode=="routing") routing();
    else if(mode=="file-failure") liveFileFailureProof(argv[2]);
    else captureProof(argv[1],argv[2]);
    return 0;
} }
