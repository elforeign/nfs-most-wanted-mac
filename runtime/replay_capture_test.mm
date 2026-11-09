// Shared Disarm regression: original idle sinks 1, fixed 0.
// Replay actions drive the actual presenter, hardware encoder and packet sink.
// No window, game or sound device; AAC content has its independent export proof.
#import <QuartzCore/QuartzCore.h>
static double replayClock=140.123333333;
static CFTimeInterval replayTestClock(void) { return replayClock; }
#define CACurrentMediaTime replayTestClock
#define main recorder_main
#include "cliprec_test.mm"
#undef main
#undef CACurrentMediaTime

static ClipSession *armCapture(void) {
    replay_arm_action(0);
    ClipSession *s=current;
    require(s && s->replayOnly && active,"Arm starts replay-only capture");
    TestDrawable *d=drawable(); auto cb=[commands commandBuffer];
    cliprec_present(cb,pattern(0),d,1); [cb commit]; [cb waitUntilCompleted]; syncWork(s);
    require(s->ready && s->replayRing && !s->file && !s->fileSink,"Arm starts no continuous file writer");
    return s;
}
int main(int argc,char **argv) { @autoreleasepool {
    require(argc==2,"output folder argument");
    outputFolder=[NSString stringWithUTF8String:argv[1]];
    gpu=MTLCreateSystemDefaultDevice(); require(gpu!=nil,"Metal device"); commands=[gpu newCommandQueue];
    ClipSession *s=armCapture();
    for(unsigned i=0;i<=400;++i) frame(s,i%N,100+i/10.0);
    char status[256]; replay_status(status,sizeof status);
    require(strstr(status,"ready"),"replay ready after 30 seconds");
    replay_save_action(0); replay_save_action(0);
    // A later queue/host time must not shift the already latched endpoint.
    replayClock=160;
    syncWork(s);
    for(unsigned i=401;i<=405;++i) frame(s,i%N,100+i/10.0);
    require(replayExport->wait(30),"asynchronous action export finishes");
    printf("action export: %s\n",replayExport->status().c_str()); fflush(stdout);
    require(replayExport->status().find("Replay saved:")==0,"action saves latched 30-second replay");
    NSArray *files=[NSFileManager.defaultManager contentsOfDirectoryAtPath:outputFolder error:nil];
    require(files.count==1,"repeated queued save yields one export");
    auto asset=[AVURLAsset URLAssetWithURL:[NSURL fileURLWithPath:[outputFolder stringByAppendingPathComponent:files[0]]] options:nil];
    require(fabs(CMTimeGetSeconds(asset.duration)-30)<1.0/60000,"action duration exactly 30 seconds");
    AVAssetTrack *track=[asset tracksWithMediaType:AVMediaTypeVideo][0];
    double source=CMTimeGetSeconds(track.segments[0].timeMapping.source.start);
    printf("latched cut: source edit=%.6f, action host=140.123333, later host=160\n",source); fflush(stdout);
    require(fabs(source*60000-round(source*60000/1000)*1000)>1,"action cut remains off the frame grid");
    replay_arm_action(0);
    // Disarm is an asynchronous resource-release action. Shutdown deliberately
    // discards replay-only capture without waiting for that queue at process exit.
    require(!dispatch_group_wait(s->finished,dispatch_time(DISPATCH_TIME_NOW,10*NSEC_PER_SEC)),"Disarm stop completes");
    require(!active && !actionReplay->stats().armed && !s->encoder && !s->pool,"Disarm releases replay capture resources");
    replay_status(status,sizeof status); require(strstr(status,"Off"),"Off status after disarm");
    replayClock=201;
    s=armCapture();
    for(unsigned i=0;i<10;++i) frame(s,i%N,200+i/10.0);
    replay_save_action(0); syncWork(s); require(replayExport->wait(3),"warm-up save completes promptly");
    require(replayExport->status().find("warming up")!=std::string::npos,"warm-up status explains missing history");
    replay_arm_action(0);
    require(!dispatch_group_wait(s->finished,dispatch_time(DISPATCH_TIME_NOW,10*NSEC_PER_SEC)),"warm-up Disarm stop completes");
    files=[NSFileManager.defaultManager contentsOfDirectoryAtPath:outputFolder error:nil];
    require(files.count==1,"warm-up save publishes nothing");
    s=start();
    dispatch_suspend(s->work);
    replay_arm_action(0); replay_arm_action(0);
    dispatch_resume(s->work); syncWork(s);
    require(!actionReplay->stats().armed,"queued sink attachment cannot undo Disarm");
    replay_arm_action(0); syncWork(s);
    require(actionReplay->stats().armed && !s->replayOnly,"Arm shares an active recording encoder");
    frame(s,0,300); frame(s,1,300.1);
    for(unsigned i=0;i<1000 && !actionReplay->stats().packets;++i) usleep(1000);
    printf("shared recording replay packets=%zu required>=1\n",actionReplay->stats().packets); fflush(stdout);
    require(actionReplay->stats().packets>0,"shared encoder supplies replay packets");
    std::weak_ptr<clip::Sink> replayConsumer=s->replaySink;
    replay_arm_action(0);
    require(active && !s->stopping,"Disarm leaves an existing recording running");
    syncWork(s);
    s->fileSink->cancel();
    printf("disarmed recording: idle replay sinks=%d expected=0\n",s->outputs.healthy()); fflush(stdout);
    require(!s->outputs.healthy() && replayConsumer.expired(),"Disarm detaches the shared replay consumer");
    cliprec_shutdown();
    printf("replay_capture: %u checks, one export, no replay-only Clip file\n",checks);
    return 0;
} }
