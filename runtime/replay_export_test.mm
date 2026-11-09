// Real H.264 + AAC, non-keyframe start and fractional-frame end, no game.
// Measured controls and results are recorded in replay_export_test.py.
#define main retention_main
#include "replay_test.mm"
#undef main
#import <AVFoundation/AVFoundation.h>
#include <cmath>

int main(int argc,char **argv) { @autoreleasepool {
    check(argc==3 || argc==4,"output directory and AAC fixture arguments");
    bool hevc=argc==4 && strstr(argv[3],"hevc");
    bool fractional=argc==4 && strstr(argv[3],"fraction");
    Fixture fixture(160,true,hevc?kCMVideoCodecType_HEVC:kCMVideoCodecType_H264);
    auto ring=std::make_shared<replay::Ring>(); ring->arm();
    AVURLAsset *audioAsset=[AVURLAsset URLAssetWithURL:[NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[2]]] options:nil];
    AVAssetReader *compressed=[[AVAssetReader alloc] initWithAsset:audioAsset error:nil];
    auto compressedAudio=[AVAssetReaderTrackOutput assetReaderTrackOutputWithTrack:[audioAsset tracksWithMediaType:AVMediaTypeAudio][0] outputSettings:nil];
    [compressed addOutput:compressedAudio]; check([compressed startReading],"real AAC fixture reader");
    double audioEnd=0;
    for(int i=0;i<654;++i) {
        ring->consume(retime(fixture.video[i%20],i));
        while(audioEnd<(i+1)/10.0+.15) {
            auto sample=[compressedAudio copyNextSampleBuffer]; check(sample,"real AAC packet");
            audioEnd=CMTimeGetSeconds(CMTimeAdd(CMSampleBufferGetPresentationTimeStamp(sample),CMSampleBufferGetDuration(sample)));
            ring->consume(std::make_shared<clip::Packet>(clip::Track::audio,sample)); CFRelease(sample);
        }
    }
    replay::Export exporter;
    CMTime end=fractional?CMTimeMake(3921001,60000):CMTimeMake(6535,100);
    double cut=CMTimeGetSeconds(end)-30;
    check(exporter.save(ring,end,argv[1]),"save accepted");
    bool duplicate=exporter.save(ring,end,argv[1]);
    printf("duplicate save accepted=%d expected=0\n",duplicate); fflush(stdout);
    check(!duplicate,"duplicate save rejected");
    check(exporter.wait(30),"bounded export completion");
    printf("export: %s\n",exporter.status().c_str()); fflush(stdout);
    check(exporter.status().find("Replay saved:")==0,"export succeeds");
    NSString *folder=[NSString stringWithUTF8String:argv[1]];
    NSArray *files=[NSFileManager.defaultManager contentsOfDirectoryAtPath:folder error:nil];
    check(files.count==1,"one unique completed MP4, no temporary file");
    AVURLAsset *asset=[AVURLAsset URLAssetWithURL:[NSURL fileURLWithPath:[folder stringByAppendingPathComponent:files[0]]] options:nil];
    double duration=CMTimeGetSeconds(asset.duration);
    printf("file duration=%.6f expected=30.000000\n",duration); fflush(stdout);
    check(fabs(duration-30)<1.0/60000,"hidden preroll yields exact 30-second file");
    NSError *error=nil;
    AVAssetReader *reader=[[AVAssetReader alloc] initWithAsset:asset error:&error];
    auto video=[AVAssetReaderTrackOutput assetReaderTrackOutputWithTrack:[asset tracksWithMediaType:AVMediaTypeVideo][0]
        outputSettings:@{(id)kCVPixelBufferPixelFormatTypeKey:@(kCVPixelFormatType_32BGRA)}];
    [reader addOutput:video];
    bool reading=[reader startReading];
    if(!reading) fprintf(stderr,"reader: %s\n",reader.error.description.UTF8String);
    check(reading,"independent file reader");
    unsigned frames=0,badMarkers=0; double first=-1,last=-1;
    while(auto sample=[video copyNextSampleBuffer]) {
        double pts=CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sample));
        if(!frames) first=pts; last=pts;
        int frame=(int)llround((pts+cut)*10-0.00001);
        if(!frames) frame=353;
        auto image=CMSampleBufferGetImageBuffer(sample); CVPixelBufferLockBaseAddress(image,kCVPixelBufferLock_ReadOnly);
        auto row=(uint8_t*)CVPixelBufferGetBaseAddress(image)+48*CVPixelBufferGetBytesPerRow(image);
        unsigned marker=0;
        for(int bit=0;bit<5;++bit) if(row[(16+32*bit)*4+1]>128) marker|=1u<<bit;
        if(marker!=(unsigned)(frame%20)) ++badMarkers;
        CVPixelBufferUnlockBaseAddress(image,kCVPixelBufferLock_ReadOnly); CFRelease(sample); ++frames;
    }
    printf("decoded video=%u first=%.6f last=%.6f bad_markers=%u\n",frames,first,last,badMarkers); fflush(stdout);
    check(frames==301 && fabs(first)<1.0/60000 && fabs(last-(65.3-cut))<1.0/60000 && !badMarkers,"decoded non-keyframe markers and hidden preroll");
    check(reader.status==AVAssetReaderStatusCompleted,"video decoder completes");
    // The driver independently decodes AAC with the local software codec;
    // host AAC services are not required for this mandatory trim proof.
    auto point=ring->reserve(CMTimeMake(6599,100));
    check(point.error.empty(),"action reserves a delayed export");
    for(int i=654;i<681;++i) ring->consume(retime(fixture.video[i%20],i));
    while(audioEnd<66.2) {
        auto sample=[compressedAudio copyNextSampleBuffer]; check(sample,"AAC watermark advances");
        audioEnd=CMTimeGetSeconds(CMTimeAdd(CMSampleBufferGetPresentationTimeStamp(sample),CMSampleBufferGetDuration(sample)));
        ring->consume(std::make_shared<clip::Packet>(clip::Track::audio,sample)); CFRelease(sample);
    }
    auto pinned=ring->snapshot(point);
    double firstKey=pinned.packets.empty()?-1:CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(pinned.packets.front()->sample()));
    printf("pinned snapshot: preceding key=%.3f, cut=35.990, current end=68.100\n",firstKey); fflush(stdout);
    check(pinned.error.empty() && firstKey==34,"action reservation pins preceding GOP before file output");
    // A stalled watermark is bounded; disarm cancels a waiting request.
    auto missing=ring->snapshot(CMTimeMake(70,1),.01);
    check(missing.error.find("watermark")!=std::string::npos,"missing watermark fails boundedly");
    check(exporter.save(ring,end,argv[1]),"new action after completed save accepted");
    check(exporter.wait(30) && exporter.status().find("Replay saved:")==0,"second explicit export succeeds");
    files=[NSFileManager.defaultManager contentsOfDirectoryAtPath:folder error:nil];
    printf("explicit saves completed=%lu expected=2\n",(unsigned long)files.count); fflush(stdout);
    check(files.count==2,"two explicit saves publish unique files without overwrite");
    auto cancelled=ring->reserve(CMTimeMake(70,1));
    ring->disarm();
    ring->arm(); ring->consume(retime(fixture.video[0],680));
    auto stale=ring->snapshot(cancelled);
    printf("cancelled snapshot: stream_changed=%d expected=1\n",stale.error.find("stream changed")!=std::string::npos); fflush(stdout);
    check(stale.error.find("stream changed")!=std::string::npos,"disarm and rearm cancel the old save generation");
    ring->disarm();
    check(!exporter.save(ring,end,argv[1]),"disarmed save refused");
    return 0;
} }
