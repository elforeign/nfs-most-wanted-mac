// MP4 passthrough preserves the decode GOP; the session hides its preroll.
#import <AVFoundation/AVFoundation.h>
#include "replay.h"
#include "clip_packets.h"
#include <chrono>
#include <thread>
#include <algorithm>

namespace replay {
static CMSampleBufferRef endpointSample(const clip::OwnedPacket& p,CMTime end,bool lastVideo) {
    auto source=p->sample();
    CMTime pts=CMSampleBufferGetPresentationTimeStamp(source),duration=CMSampleBufferGetDuration(source);
    CMTime excess=CMTimeSubtract(CMTimeAdd(pts,duration),end);
    if(p->track!=clip::Track::video || (!lastVideo && CMTimeCompare(excess,kCMTimeZero)<=0)) { CFRetain(source); return source; }
    CMSampleBufferRef copy=nullptr;
    // A dropped final presentation keeps its previous frame on screen. Hold
    // that frame through the latched endpoint, rather than shortening the file.
    CMSampleTimingInfo timing={CMTimeSubtract(end,pts),pts,CMSampleBufferGetDecodeTimeStamp(source)};
    CMSampleBufferCreateCopyWithNewTiming(nullptr,source,1,&timing,&copy);
    return copy;
}
struct Export::State {
    mutable std::mutex lock;
    std::condition_variable done;
    bool saving=false;
    std::string text="No replay saved";
};
Export::Export():state(std::make_shared<State>()) {}
bool Export::busy() const { std::lock_guard<std::mutex> g(state->lock); return state->saving; }
std::string Export::status() const { std::lock_guard<std::mutex> g(state->lock); return state->text; }
bool Export::wait(double seconds) const {
    std::unique_lock<std::mutex> g(state->lock);
    return state->done.wait_for(g,std::chrono::duration<double>(seconds),[&]{return !state->saving;});
}
static NSString *writeSnapshot(const Snapshot& snapshot, NSString *folder, NSString **published) {
    NSError *error=nil;
    auto files=NSFileManager.defaultManager;
    if(![files createDirectoryAtPath:folder withIntermediateDirectories:YES attributes:nil error:&error]) return error.localizedDescription;
    NSString *name=[@"Replay-" stringByAppendingString:NSUUID.UUID.UUIDString];
    NSString *destination=[folder stringByAppendingPathComponent:[name stringByAppendingString:@".mp4"]];
    NSString *temporary=[folder stringByAppendingPathComponent:[@"." stringByAppendingString:[name stringByAppendingString:@".tmp.mp4"]]];
    AVAssetWriter *writer=[[AVAssetWriter alloc] initWithURL:[NSURL fileURLWithPath:temporary] fileType:AVFileTypeMPEG4 error:&error];
    if(!writer) return error.localizedDescription ?: @"Replay MP4 writer unavailable";
    writer.movieTimeScale=60000;
    AVAssetWriterInput *video=nil,*audio=nil;
    for(auto &p:snapshot.packets) {
        AVAssetWriterInput *__strong *input=p->track==clip::Track::video?&video:&audio;
        if(*input) continue;
        *input=[AVAssetWriterInput assetWriterInputWithMediaType:p->track==clip::Track::video?AVMediaTypeVideo:AVMediaTypeAudio
            outputSettings:nil sourceFormatHint:p->format()];
        (*input).expectsMediaDataInRealTime=NO;
        if(p->track==clip::Track::video) (*input).mediaTimeScale=60000;
        if(![writer canAddInput:*input]) { error=[NSError errorWithDomain:@"Replay input" code:1 userInfo:nil]; break; }
        [writer addInput:*input];
    }
    NSString *failure=nil;
    if(error || !video || ![writer startWriting]) failure=writer.error.localizedDescription ?: @"Replay writer setup failed";
    if(!failure) {
        [writer startSessionAtSourceTime:snapshot.start];
        // Bypass an unready track, preserving order within each track. A muxer
        // may require the other track to advance before accepting this one.
        std::vector<clip::OwnedPacket> packets[2];
        for(auto &p:snapshot.packets) packets[p->track==clip::Track::video?0:1].push_back(p);
        size_t cursor[2]={0,0};
        AVAssetWriterInput *inputs[2]={video,audio};
        bool finished[2]={false,!audio};
        auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(15);
        while(!finished[0] || !finished[1]) {
            bool progress=false;
            for(unsigned t=0;t<2;++t) {
                if(finished[t]) continue;
                if(cursor[t]==packets[t].size()) { [inputs[t] markAsFinished]; finished[t]=true; progress=true; continue; }
                if(!inputs[t].readyForMoreMediaData) continue;
                auto sample=endpointSample(packets[t][cursor[t]],snapshot.end,t==0 && cursor[t]+1==packets[t].size());
                bool appended=sample && [inputs[t] appendSampleBuffer:sample];
                if(sample) CFRelease(sample);
                if(!appended) {
                    failure=writer.error.localizedDescription ?: @"Replay packet append failed"; break;
                }
                ++cursor[t]; progress=true;
            }
            if(failure) break;
            if(writer.status!=AVAssetWriterStatusWriting || std::chrono::steady_clock::now()>=deadline) {
                failure=writer.error.localizedDescription ?: @"Replay writer stalled"; break;
            }
            if(!progress) std::this_thread::sleep_for(std::chrono::milliseconds(2));
        }
        if(!failure) {
            [writer endSessionAtSourceTime:snapshot.end];
            dispatch_semaphore_t done=dispatch_semaphore_create(0);
            [writer finishWritingWithCompletionHandler:^{dispatch_semaphore_signal(done);}];
            if(dispatch_semaphore_wait(done,dispatch_time(DISPATCH_TIME_NOW,15*NSEC_PER_SEC))) failure=@"Replay finalization timed out";
            else if(writer.status!=AVAssetWriterStatusCompleted) failure=writer.error.localizedDescription ?: @"Replay finalization failed";
        }
    }
    if(!failure) {
        AVURLAsset *asset=[AVURLAsset URLAssetWithURL:[NSURL fileURLWithPath:temporary] options:nil];
        double expected=CMTimeGetSeconds(CMTimeSubtract(snapshot.end,snapshot.start));
        auto tracks=[asset tracksWithMediaType:AVMediaTypeVideo];
        auto sound=[asset tracksWithMediaType:AVMediaTypeAudio];
        if(tracks.count!=1 || sound.count!=(audio?1u:0u) || !CMTIME_IS_NUMERIC(asset.duration) || fabs(CMTimeGetSeconds(asset.duration)-expected)>1.0/60000)
            failure=[NSString stringWithFormat:@"Replay file duration validation failed (%.6f vs %.6f; %lu video/%lu audio)",
                CMTimeGetSeconds(asset.duration),expected,(unsigned long)tracks.count,(unsigned long)sound.count];
        for(AVAssetTrack *track in asset.tracks) {
            if(!CMTIME_IS_NUMERIC(track.timeRange.start) || !CMTIME_IS_NUMERIC(track.timeRange.duration) ||
               fabs(CMTimeGetSeconds(track.timeRange.start))>1.0/48000 ||
               fabs(CMTimeGetSeconds(track.timeRange.duration)-expected)>1.0/60)
                failure=[NSString stringWithFormat:@"Replay file edit validation failed (%@ start %.6f duration %.6f)",track.mediaType,
                    CMTimeGetSeconds(track.timeRange.start),CMTimeGetSeconds(track.timeRange.duration)];
        }
        if(!failure && ![files moveItemAtPath:temporary toPath:destination error:&error]) failure=error.localizedDescription;
    }
    if(failure) {
        if(writer.status==AVAssetWriterStatusWriting) [writer cancelWriting];
        [files removeItemAtPath:temporary error:nil];
    } else *published=destination;
    return failure;
}
bool Export::save(const std::shared_ptr<Ring>& ring, CMTime end, const std::string& folder) {
    auto s=state;
    SavePoint point;
    { std::lock_guard<std::mutex> g(s->lock);
      if(s->saving) return false;
      if(!ring || !ring->stats().armed) { s->text="Arm replay first"; return false; }
      point=ring->reserve(end);
      if(!point.error.empty()) {
          s->text=point.error;
          fprintf(stderr,"replay: not saved - video 30.000000 s, snapshot 0 bytes; error: %s\n",point.error.c_str());
          return false;
      }
      s->saving=true; s->text="Saving replay...";
    }
    std::thread([s,ring,end,folder,point] {
        @autoreleasepool {
            Snapshot snapshot=ring->snapshot(point);
            NSString *path=nil;
            NSString *error=snapshot.error.empty()?writeSnapshot(snapshot,[NSString stringWithUTF8String:folder.c_str()],&path):
                [NSString stringWithUTF8String:snapshot.error.c_str()];
            fprintf(stderr,"replay: %s %s video %.6f s, snapshot %zu bytes%s%s\n",error?"not saved":"saved",
                path?path.fileSystemRepresentation:"-",CMTimeGetSeconds(CMTimeSubtract(end,snapshot.start)),snapshot.bytes,
                error?"; error: ":"",error?error.UTF8String:"");
            // Drop snapshot references before admitting the next save.
            snapshot.packets.clear();
            { std::lock_guard<std::mutex> g(s->lock);
              s->text=error?error.UTF8String:std::string("Replay saved: ")+path.lastPathComponent.UTF8String;
              s->saving=false;
            }
            s->done.notify_all();
        }
    }).detach();
    return true;
}
}
