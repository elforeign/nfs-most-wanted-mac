#include "live_encode.h"
#include "clip_packets.h"
#import <Foundation/Foundation.h>
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>

namespace live {
bool enabled() {
    const char *value=getenv("NFS_LIVE_ENCODE");
    return value && !strcmp(value,"on");
}
Profile Profile::youtube(unsigned width,unsigned height) {
    // 60 fps H.264: 17 Mb/s at 1080p, 50 Mb/s at 2160p. Interpolate
    // between those anchors; preserve their per-pixel rate outside that range.
    double pixels=(double)width*height, hd=1920.0*1080, uhd=3840.0*2160;
    double rate=pixels<=hd?17000000*pixels/hd:
        pixels>=uhd?50000000*pixels/uhd:17000000+33000000*(pixels-hd)/(uhd-hd);
    return {width,height,(int)std::min(200000000.0,std::max(1.0,std::round(rate)))};
}
void Encoder::fail(OSStatus code) {
    OSStatus expected=0;
    status_.compare_exchange_strong(expected,code?code:kVTVideoEncoderMalfunctionErr);
}
Encoder::Encoder(Profile profile,std::shared_ptr<clip::Sink> output):profile_(profile),sink(std::move(output)) {
    if(profile.width<2 || profile.height<2 || (profile.width|profile.height)&1 || profile.bitrate<=0) {
        fail(paramErr); return;
    }
    NSDictionary *hardware=@{(id)kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder:@YES};
    NSDictionary *attrs=@{(id)kCVPixelBufferPixelFormatTypeKey:@(kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange),
        (id)kCVPixelBufferWidthKey:@(profile.width),(id)kCVPixelBufferHeightKey:@(profile.height),
        (id)kCVPixelBufferIOSurfacePropertiesKey:@{}};
    OSStatus rc=VTCompressionSessionCreate(NULL,profile.width,profile.height,kCMVideoCodecType_H264,
        (__bridge CFDictionaryRef)hardware,(__bridge CFDictionaryRef)attrs,NULL,compressed,this,&session);
    if(rc || !session) { fail(rc); return; }
    auto set=[&](CFStringRef key,id value) {
        OSStatus result=VTSessionSetProperty(session,key,(__bridge CFTypeRef)value);
        if(result) fail(result);
    };
    set(kVTCompressionPropertyKey_ProfileLevel,(id)kVTProfileLevel_H264_High_AutoLevel);
    set(kVTCompressionPropertyKey_RealTime,@YES);
    set(kVTCompressionPropertyKey_AllowFrameReordering,@NO);
    set(kVTCompressionPropertyKey_ExpectedFrameRate,@60);
    // CBR is mutually exclusive with AverageBitRate and DataRateLimits. There
    // is deliberately no best-effort VBR or software fallback on negotiation failure.
    if(@available(macOS 13.0,*)) set(kVTCompressionPropertyKey_ConstantBitRate,@(profile.bitrate));
    else fail(kVTPropertyNotSupportedErr);
    set(kVTCompressionPropertyKey_MaxKeyFrameInterval,@120);
    set(kVTCompressionPropertyKey_MaxKeyFrameIntervalDuration,@2);
    set(kVTCompressionPropertyKey_ColorPrimaries,(id)kCMFormatDescriptionColorPrimaries_ITU_R_709_2);
    set(kVTCompressionPropertyKey_TransferFunction,(id)kCMFormatDescriptionTransferFunction_sRGB);
    set(kVTCompressionPropertyKey_YCbCrMatrix,(id)kCMFormatDescriptionYCbCrMatrix_ITU_R_709_2);
    if(!status()) {
        rc=VTCompressionSessionPrepareToEncodeFrames(session);
        if(rc) fail(rc);
        CFTypeRef hw=nullptr;
        rc=VTSessionCopyProperty(session,kVTCompressionPropertyKey_UsingHardwareAcceleratedVideoEncoder,NULL,&hw);
        if(rc || !hw || !CFEqual(hw,kCFBooleanTrue)) fail(rc);
        if(hw) CFRelease(hw);
    }
    if(status()) { VTCompressionSessionInvalidate(session); CFRelease(session); session=nullptr; }
}
Encoder::~Encoder() {
    complete();
    if(session) { VTCompressionSessionInvalidate(session); CFRelease(session); }
}
bool Encoder::accepts(CVPixelBufferRef pixel) const {
    return pixel && CVPixelBufferGetWidth(pixel)==profile_.width && CVPixelBufferGetHeight(pixel)==profile_.height &&
        CVPixelBufferGetPixelFormatType(pixel)==kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange;
}
bool Encoder::encode(CVPixelBufferRef pixel,CMTime pts,CMTime duration) {
    if(status() || !session) return false;
    if(!accepts(pixel) || !CMTIME_IS_NUMERIC(pts) || !CMTIME_IS_NUMERIC(duration) ||
       CMTimeCompare(duration,kCMTimeZero)<=0 ||
       (CMTIME_IS_NUMERIC(lastPTS) && CMTimeCompare(pts,lastPTS)<=0)) { fail(paramErr); return false; }
    if(pending_.fetch_add(1)>=maxPending) { --pending_; fail(kVTVideoEncoderNotAvailableNowErr); return false; }
    bool idr=CMTimeCompare(pts,nextIDR)>=0;
    if(idr) nextIDR=CMTimeAdd(pts,CMTimeMake(2,1));
    NSDictionary *options=idr?@{(id)kVTEncodeFrameOptionKey_ForceKeyFrame:@YES}:nil;
    lastPTS=pts;
    // VT also retains its input, but this explicit reference covers submission
    // through callback, including drops. Recording retains its own input through
    // its independent VT session; neither callback can recycle the other's surface.
    CVPixelBufferRetain(pixel);
    OSStatus rc=VTCompressionSessionEncodeFrame(session,pixel,pts,duration,(__bridge CFDictionaryRef)options,pixel,NULL);
    if(rc) { CVPixelBufferRelease(pixel); --pending_; fail(rc); }
    return !status();
}
void Encoder::compressed(void *context,void *source,OSStatus code,VTEncodeInfoFlags flags,CMSampleBufferRef sample) {
    auto &encoder=*(Encoder *)context;
    if(code || (flags&kVTEncodeInfo_FrameDropped)) encoder.fail(code);
    else if(!encoder.status()) encoder.packet(sample);
    if(source) CVPixelBufferRelease((CVPixelBufferRef)source);
    --encoder.pending_;
}
bool Encoder::packet(CMSampleBufferRef sample) {
    auto format=sample?CMSampleBufferGetFormatDescription(sample):nullptr;
    if(!format || CMFormatDescriptionGetMediaSubType(format)!=kCMVideoCodecType_H264 ||
        !CMSampleBufferDataIsReady(sample)) { fail(kVTParameterErr); return false; }
    if(sink && !sink->push(std::make_shared<clip::Packet>(clip::Track::video,sample))) {
        fail(kVTVideoEncoderNotAvailableNowErr); return false;
    }
    ++frames_; bytes_+=CMSampleBufferGetTotalSampleSize(sample);
    return true;
}
void Encoder::complete() {
    if(!session) return;
    OSStatus rc=VTCompressionSessionCompleteFrames(session,kCMTimeInvalid);
    if(rc) fail(rc);
}
}
