// Production Go/Stop, capture queues, Metal/VT, AAC, TS and HTTPS loopback.
// Original/fixed: encoder status and capture session reason fail/pass, 1/0 each.
// ASan/UBSan: only 333/0 checks/failures, attached 323/0, simultaneous Stop 314/0.
// Each posts 2 segments + 2 playlists, key/session matches=0; both independent file-stop cases retain 7200 AAC samples.
// Attached file: 5 decoded frames, 0.150000 s; AAC tail original/fixed: 2240/7200 samples.
// Result-after-cancel fault: 0 files vs 1.
// Fourteen compiled controls exit 1 on their intended assertion; good exit 0.
// Restart/diagnostic: 0 sessions/sockets; setup: 0 sessions. Stop: 0 retained upload bytes.
// Focus loss (12.6 s with no presented frame, audio running): fixed posts 10 segments, 365 held frames, no error;
// without the live hold (original) it stops after 2 posts with "live: error kind=encoder reason=no-idr-5s".
// Encoder status: fixed row "Error: encoder -12902", note code=-12902 reason=status; the 5012 path ("Error: encoder",
// code=0) fails both assertions.
#define LIVESTREAM_TESTING 1
#include "livestream.h"
static live::StreamTestOptions proofOptions;
static std::atomic<unsigned> createdSessions{0};
#ifndef STREAM_POSTER_SOURCE
#define STREAM_POSTER_SOURCE "livestream.mm"
#endif
#include STREAM_POSTER_SOURCE
#define main ClipFixtureMain
#include "cliprec_test.mm"
#undef main
#include "stream_key.h"
#include <fstream>
#include <iostream>
#include <chrono>
#include <thread>
extern "C" int proof_key(const char *);
extern "C" unsigned livestream_test_socket_count();
template<class F> static bool awaitLive(F predicate,double seconds=8) {
    auto until=std::chrono::steady_clock::now()+std::chrono::duration<double>(seconds);
    do { if(predicate()) return true; usleep(2000); } while(std::chrono::steady_clock::now()<until);
    return predicate();
}
static void prepareLive(ClipSession *s) {
    dispatch_sync(s->work,^{ s->device=gpu; s->preparing=true; [s prepare:W height:H]; });
    require(s->ready && liveState==2,"Go prepares live without a diagnostic switch");
}
static bool sharedAAC=false;
static void feed(ClipSession *s,unsigned first,unsigned end,clip::AACEncoder &aac) {
    std::vector<float> pcm(1600*2,0.01f);
    for(unsigned i=first;i<end;++i) {
        frame(s,i%N,100+i/30.0);
        dispatch_sync(s->work,^{ if(s->liveEncoder) s->liveEncoder->complete(); });
        require(aac.encode(pcm.data(),1600,false,[s](clip::OwnedPacket p) {
            dispatch_sync(s->work,^{ if(sharedAAC) s->outputs.publish(p); if(s->liveSegments) s->liveSegments->audio->push(p); });
        }),"synthetic AAC encode");
        require(awaitLive([&]{
            __block bool drained=false;
            dispatch_sync(s->work, ^{ drained=!s->liveEncoder || (!s->liveEncoder->pending() && s->liveSegments->drained()); });
            return drained;
        }),"live packet queues drain");
    }
}
int main(int argc,char **argv) { @autoreleasepool {
    require(argc==5,"mode/base/anchor/profile arguments");
    std::string mode=argv[1],key; std::getline(std::cin,key);
    proofOptions.baseURL=argv[2];
    std::ifstream cert(argv[3],std::ios::binary);
    proofOptions.anchorDER.assign(std::istreambuf_iterator<char>(cert),{});
    // Successful TLS + segment/playlist pairs use the production budget. Only
    // the explicit stalled-response case compresses time to exercise expiry.
    proofOptions.deadlineSeconds=mode=="failure-deadline"?0.35:live::Poster::deadlineSeconds;
    outputFolder=[NSString stringWithUTF8String:argv[4]];
    [NSFileManager.defaultManager createDirectoryAtPath:outputFolder withIntermediateDirectories:YES attributes:nil error:nil];
    sessionNoteFile=fopen([outputFolder stringByAppendingString:@".session.txt"].UTF8String,"w");
    require(sessionNoteFile,"session-note sink");
    auto sockets=livestream_test_socket_count();
    require(!current && !active && !live::currentStream && liveState==0 && !createdSessions,"fresh process stays Off");
    stream_key_go(0);
    require(!current && !active && !createdSessions,"Go without a key allocates nothing");
    if(mode=="restart") {
        usleep(100000);
        require(livestream_test_socket_count()==sockets,"restart has zero sockets");
        printf("restart: broadcasts=0 capture=0 sessions=0 sockets=0\n"); return 0;
    }
    require(proof_key(key.c_str()),"accept in-memory key");
    require(!current && !live::currentStream && !createdSessions && livestream_test_socket_count()==sockets,"saving key allocates no capture or transport");
    gpu=MTLCreateSystemDefaultDevice(); if(!gpu) { puts("SKIP: Metal unavailable"); return 3; }
    commands=[gpu newCommandQueue];
    ClipSession *s=nil;
    if(mode=="diagnostic-off") {
        setenv("NFS_LIVE_ENCODE","on",1);
        s=start(); clip::AACEncoder localAudio; feed(s,0,140,localAudio);
        require(s->liveSegments && s->liveSegments->stats().segments>=2,"local diagnostic produces TS");
        require(!live::currentStream && !createdSessions && livestream_test_socket_count()==sockets,"local diagnostic with a saved key never broadcasts");
        cliprec_shutdown(); printf("diagnostic-off: local_segments>=2 broadcasts=0 sessions=0 sockets=0\n"); return 0;
    }
    if(mode=="attached" || mode.rfind("failure",0)==0 || mode=="encoder-failure" || mode=="capture-failure") {
        s=start(); replay_arm_action(0); syncWork(s);
    }
    stream_key_go(0); if(!s) s=current;
    stream_key_go(0);
    require(current==s && s->broadcast && liveState==1,"repeated Go is idempotent");
    if(mode=="setup-stop") {
        stream_key_stop(0); syncWork(s); cliprec_shutdown();
        require(!createdSessions && !active && liveState==0,"Stop during setup never posts");
        printf("setup-stop: sessions=0 posts=0\n"); return 0;
    }
    if(mode=="only" || mode=="stop-both") setenv("NFS_REPLAY","on",1);
    if(!s->ready) prepareLive(s); else syncWork(s);
    unsetenv("NFS_REPLAY");
    require(liveState==2 && s->liveEncoder,"live attaches to an existing capture");
    if(mode=="capture-failure") {
        dispatch_sync(s->work, ^{ [s fail:@"fixture capture failure"]; });
        char text[80]; stream_key_status(text,sizeof text);
        require(liveState==4 && !strcmp(text,"Error: capture"),"capture failure reason reaches status row");
        {
            std::lock_guard<std::mutex> guard(sessionNotesLock);
            require(std::find(sessionNotes.begin(),sessionNotes.end(),"live: error kind=capture code=0")!=sessionNotes.end(),"capture failure session reason");
        }
        cliprec_shutdown(); return 0;
    }
    if(mode=="only" || mode=="stop-both") require(s->replayOnly && !s->file && !s->fileSink && !s->replayRing,"live-only creates no file or replay ring");
    if(mode=="capture-transfer") {
        dispatch_suspend(s->work);
        stream_key_stop(0); replay_arm_action(0);
        dispatch_resume(s->work); syncWork(s);
        require(active && !s->stopping && s->replayRequested && actionReplay->stats().armed,"queued replay Arm survives live Stop");
        require(!s->liveEncoder && !s->liveSegments,"transfer retires only live resources");
        replay_arm_action(0);
        require(!dispatch_group_wait(s->finished,dispatch_time(DISPATCH_TIME_NOW,10*NSEC_PER_SEC)),"Disarm stop completes");
        require(!active && !s->encoder && !s->pool,"Disarm releases capture");
        printf("capture-transfer: replay_survives=1 final_capture_handles=0 sessions=0\n"); return 0;
    }
    if(mode=="encoder-failure") {
        dispatch_sync(s->work, ^{ s->liveEncoder->packet(nullptr); [s pump]; });
        require(liveState==4 && !s->liveEncoder && !s->liveSegments,"encoder failure terminates only live");
        require(active && s->file && actionReplay->stats().armed,"recording/replay survive encoder failure");
        require(!live::currentStream && !createdSessions,"encoder failure cancels poster before networking");
        char text[80]; stream_key_status(text,sizeof text);
        require(!strcmp(text,"Error: encoder -12902"),"encoder failure reason reaches status row");
        {
            std::lock_guard<std::mutex> guard(sessionNotesLock);
            require(std::find(sessionNotes.begin(),sessionNotes.end(),"live: error kind=encoder code=-12902 reason=status")!=sessionNotes.end(),"encoder failure session reason");
        }
        cliprec_shutdown(); printf("encoder-failure: terminal=1 recording_survives=1 replay_survives=1 sessions=0\n"); return 0;
    }
    auto original=live::currentStream;
    auto stale=live::streamOutput();
    clip::AACEncoder aac;
    if(mode=="inflight-stop") {
        feed(s,0,80,aac);
        require(awaitLive([&]{return original->stats().accepted>0 && original->stats().bytes>0;}),"fixture has an in-flight POST");
        require(original->stats().running,"explicit Stop precedes deadline");
        stream_key_stop(0); syncWork(s);
        require(!original->stats().running,"Stop cancels the in-flight generation");
        require(awaitLive([&]{return original->stats().bytes==0 && original->stats().cancelled>0;}),"Stop releases in-flight payload");
        require(!s->liveEncoder && !s->liveSegments && !active,"in-flight Stop retires live capture");
        cliprec_shutdown(); printf("inflight-stop: cancelled>=1 retained_bytes=0 active_capture=0\n"); return 0;
    }
    if(mode=="focus-gap") {
        // Go in the front end, then a Space swipe / app switch: while the game is inactive or occluded no frame is
        // presented, but the mixer and its AAC tap keep running. Frames return 12.6 s later (the reported session).
        std::vector<float> pcm(1600*2,0.01f);
        unsigned i=0;
        auto step=[&](bool present) {
            testClock=100+i/30.0;
            if(present) frame(s,i%N,testClock);
            dispatch_sync(s->work,^{ [s pump]; if(s->liveEncoder) s->liveEncoder->complete(); });
            require(aac.encode(pcm.data(),1600,false,[s](clip::OwnedPacket p) {
                dispatch_sync(s->work,^{ if(s->liveSegments) s->liveSegments->audio->push(p); });
            }),"synthetic AAC encode");
            require(awaitLive([&]{
                __block bool drained=false;
                dispatch_sync(s->work, ^{ drained=!s->liveEncoder || (!s->liveEncoder->pending() && s->liveSegments->drained()); });
                return drained && !original->stats().bytes;
            }),"live packet and upload queues drain");
            ++i;
        };
        while(i<140) step(true);
        while(i<140+378) step(false);
        while(i<140+378+140) step(true);
        require(awaitLive([&]{return original->stats().posted>=9;},3) && liveState==2 && livestream_status()==1,
            "live survives focus loss");
        __block uint64_t held=0,segments=0;
        dispatch_sync(s->work,^{ held=s->liveHeldFrames; segments=s->liveSegments?s->liveSegments->stats().segments:0; });
        auto posted=original->stats().posted;
        stream_key_stop(0); syncWork(s); cliprec_shutdown();
        printf("focus-gap: posted=%llu segments=%llu held=%llu live_state=%d checks=%u\n",(unsigned long long)posted,
            (unsigned long long)segments,(unsigned long long)held,liveState.load(),checks);
        return 0;
    }
    feed(s,0,140,aac);
    if(mode.rfind("failure",0)==0) {
        require(awaitLive([&]{return liveState==4;}),"transport failure is terminal");
        char text[80]; stream_key_status(text,sizeof text);
        const char *expected=mode=="failure-http"?"Error: YouTube 503":mode=="failure-401"?"Error: key rejected":
            mode=="failure-deadline"?"Error: no response":"Error: network";
        require(!strcmp(text,expected),"transport reason survives capture detach in status row");
        require(active && !s->stopping && s->file && actionReplay->stats().armed,"capture survives live failure");
        feed(s,140,150,aac); require(!s->broadcast && liveState==4,"capture cannot reconnect live");
        cliprec_shutdown(); printf("failure: terminal=1 recording_survives=1 replay_survives=1\n"); return 0;
    }
    require(awaitLive([&]{return original->stats().playlists>=2;}),"Go posts TS segments and playlists to loopback");
    auto posted=original->stats().posted;
    if(mode=="only" || mode=="stop-both") {
        // Start and stop a file while the independently requested live capture stays active.
        cliprec_action(0); syncWork(s); require(s->file && !s->replayOnly,"recording attaches during live");
        sharedAAC=true;
        CMAudioFormatDescriptionRef audioFormat=aac.format();
        dispatch_sync(s->work,^{ s->file->audioFormat=audioFormat; CFRetain(audioFormat); });
        feed(s,140,145,aac);
        cliprec_action(0); syncWork(s); require(s->replayOnly && active && s->broadcast,"Stop clip preserves live");
        if(mode=="stop-both") {
            stream_key_stop(0); syncWork(s);
            require(aac.encode(nullptr,0,true,[s](clip::OwnedPacket packet) {
                dispatch_sync(s->work,^{s->outputs.publish(packet);});
            }),"last capture flushes AAC before draining retired files");
        } else feed(s,145,155,aac);
    } else {
        replay_arm_action(0); syncWork(s); require(active && s->broadcast,"Disarm replay preserves live");
        replay_arm_action(0); syncWork(s);
    }
    stream_key_stop(0); syncWork(s);
    require(!s->liveEncoder && !s->liveSegments,"Stop releases live encoders and segment queues");
    auto sent=original->stats().accepted;
    auto late=std::make_shared<live::Segment>(); late->bytes.resize(188); late->end90k=180000;
    require(!stale(late) && original->stats().accepted==sent,"stale generation cannot admit work after Stop");
    if(mode=="attached") {
        require(active && !s->stopping && s->file && actionReplay->stats().armed,"Stop live preserves recording and replay");
        feed(s,140,150,aac);
        std::atomic<bool> firstIDR{false};
        s->liveOutput=std::make_shared<clip::Sink>([&](const clip::OwnedPacket& packet) {
            auto attachments=CMSampleBufferGetSampleAttachmentsArray(packet->sample(),false);
            firstIDR=!attachments || !CFArrayGetCount(attachments) ||
                CFDictionaryGetValue((CFDictionaryRef)CFArrayGetValueAtIndex(attachments,0),kCMSampleAttachmentKey_NotSync)!=kCFBooleanTrue;
            return clip::Result::consumed;
        });
        stream_key_go(0); syncWork(s);
        auto next=live::currentStream;
        require(next && next!=original && !next->stats().accepted,"explicit next Go has fresh poster state");
        require(!stale(late) && !next->stats().accepted,"stale callback cannot feed a newer generation");
        feed(s,150,151,aac);
        require(awaitLive([&]{return firstIDR.load();}),"new generation starts with a decodable IDR");
        stream_key_stop(0); syncWork(s);
        s->liveOutput->close(); require(awaitLive([&]{return s->liveOutput->drained();}),"IDR observer retires"); s->liveOutput.reset();
    } else require(!active,"final Stop releases capture");
    usleep(100000); require(original->stats().accepted==sent,"Stop produces no later posts");
    cliprec_shutdown();
    require(!s->encoder && !s->pool,"final Stop releases capture resources");
    if(mode=="only" || mode=="stop-both") {
        NSArray *files=[NSFileManager.defaultManager contentsOfDirectoryAtPath:outputFolder error:nil];
        require(files.count==1,"independent file stop publishes one clip");
        Decoded clip=decode([outputFolder stringByAppendingPathComponent:files[0]]);
        require(clip.valid && clip.count==5 && clip.duration<0.2,"attached clip starts at its own decodable keyframe and timeline");
        printf("attached clip: decoded=%u duration=%.6f\n",clip.count,clip.duration);
        auto asset=[AVURLAsset URLAssetWithURL:[NSURL fileURLWithPath:[outputFolder stringByAppendingPathComponent:files[0]]] options:nil];
        NSArray *tracks=[asset tracksWithMediaType:AVMediaTypeAudio]; require(tracks.count==1,"independent clip has stereo AAC");
        auto reader=[[AVAssetReader alloc] initWithAsset:asset error:nil];
        auto output=[AVAssetReaderTrackOutput assetReaderTrackOutputWithTrack:tracks[0]
            outputSettings:@{AVFormatIDKey:@(kAudioFormatLinearPCM),AVLinearPCMIsFloatKey:@YES,AVLinearPCMBitDepthKey:@32}];
        [reader addOutput:output]; require([reader startReading],"decode independent AAC clip");
        uint64_t samples=0; CMSampleBufferRef sample;
        while((sample=[output copyNextSampleBuffer])) { samples+=CMSampleBufferGetNumSamples(sample); CFRelease(sample); }
        printf("attached clip AAC: decoded_samples=%llu expected=7200\n",(unsigned long long)samples); fflush(stdout);
        require(reader.status==AVAssetReaderStatusCompleted && samples==7200,"independent file Stop retains AAC tail");
    }
    printf("%s: posted=%llu sessions=%u stop_admissions=0 checks=%u\n",mode.c_str(),(unsigned long long)posted,createdSessions.load(),checks);
    return 0;
} }
