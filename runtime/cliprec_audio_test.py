#!/usr/bin/env python3
"""Production A/V recording proof; fixtures stay in injected worktree scratch.
Pinned original 13984ab7: audio_tracks=0 required=1 video_frames=36 (exit 1),
32,896 actual DirectSound fixture frames. PCM control: 600 video frames / 480,000
stereo samples over 10 s, marker offsets 0 ms, normalized RMS 0.001458,
container end error 0; SDR/HDR, HEVC/H.264, drop gaps, transitions and lifecycle pass.
PCM controls rejected: removed audio 0/1 tracks; skew +100 ms; swap RMS 1.237052;
wrong rate 44,100/48,000 Hz; lost drain 22.021 ms, tail RMS 0.653658 (all exit 1).
Startup control: future PCM rejects the early video (accepted=0, epoch_set=0);
removing the coverage guard gives accepted=1, epoch_set=1 and exit 1.
Jittered device stamps (contiguous sample_position, stamps moved -5..+5 ms per
block): pinned pre-fix receive() normalized RMS 1.339511 after best alignment,
marker offsets up to 147 ms (exit 1) -> fixed 0.012352 (PCM) / 0.013138 (AAC),
0 ms markers after a -0.160 ms constant alignment; threshold RMS <=0.05, <=10 ms.
Video pause 8 s with sound flowing: pinned pre-fix pump() overflows the ring
after 5.467 s (exit 1) -> fixed records on, queue peak 132,096/262,144, markers
after the pause 0 ms, resumed RMS 0.001445 (threshold <=0.05, <=10 ms).
AAC passes where the codec is available (verified outside the sandbox); the test
exits 3 only where the codec factory is restricted (it returns 1718449215), after
the baseline and PCM proofs. Results/logs remain in worktree scratch. No game,
real audio device or listening claim.
"""
from pathlib import Path
import subprocess
import tempfile
RT=Path(__file__).resolve().parent
ROOT=RT.parent
CACHE=ROOT/'.metal-cache'/'cliprec-audio'
CACHE.mkdir(parents=True,exist_ok=True)
# Relevant original 13984ab7 methods are pinned individually. The remaining
# conversion, VT submission, presentation and shutdown code is shared; no
# repository history, external fixture file, or encoder mock is needed.
ORIGINAL_METHODS = [
    ('- (instancetype)init {', '- (void)dealloc {', 'ac18e9c8105be5408199311072bf7e95be7bb8d29e620233bf7ab7fb9b1903d5', r'''
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
    }
    return self;
}
'''),
    ('- (void)packet:(CMSampleBufferRef)sample status:(OSStatus)status {', '- (void)pump {', '518321578a33c798460188fefd0967fcdba84274483c57558509928b984ab462', r'''
- (void)packet:(CMSampleBufferRef)sample status:(OSStatus)status {
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
        if (![writer startWriting]) { --queuedPackets; [self fail:writer.error.localizedDescription ?: @"MP4 start failed"]; return; }
        [writer startSessionAtSourceTime:kCMTimeZero]; writing = true;
    }
    ClipPacket *p = [ClipPacket new]; p->sample = sample; CFRetain(sample);
    [packets addObject:p];
    [self pump];
}
'''),
    ('- (void)pump {', '- (void)consume:', 'd8834a66f3ae1b7eefaf83aaf4ed99f3c30a7fe4d5983b2f2f65078be438bd84', r'''
- (void)pump {
    while (packets.count && input.readyForMoreMediaData) {
        ClipPacket *p = packets.firstObject;
        if (![input appendSampleBuffer:p->sample]) {
            writerDrops += (unsigned)packets.count; queuedPackets -= (unsigned)packets.count;
            [packets removeAllObjects];
            [self fail:writer.error.localizedDescription ?: @"MP4 write failed"]; return;
        }
        ++accepted; --queuedPackets; streak=0;
        [packets removeObjectAtIndex:0]; blockedSince=0;
    }
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

'''),
    ('- (void)finishWhenDrained {', '- (void)finish {', 'a0061502d6522b30cb2ad0d49e9df6149704dc18d687ff4bcacc926fe8297be0', r'''
- (void)finishWhenDrained {
    if (packets.count) {
        [self pump];
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,10*NSEC_PER_MSEC),work,^{ [self finishWhenDrained]; });
        return;
    }
    if (writing && writer.status == AVAssetWriterStatusWriting) {
        [input markAsFinished];
        [writer finishWritingWithCompletionHandler:^{ dispatch_async(self->work, ^{ [self finish]; }); }];
    } else {
        if (!error) error = @"No presented video frames";
        [writer cancelWriting]; [self finish];
    }
}

'''),
    ('- (void)finish {', '@end', 'ee15707400c532d3738b5fb110b55da1708073e8b43bd32cdcb1dd72c08260d6', r'''
- (void)finish {
    if (writer.status == AVAssetWriterStatusCompleted) {
        NSError *e = nil;
        // moveItem refuses an existing destination; temporary is on the same volume.
        if (![NSFileManager.defaultManager moveItemAtPath:temporary toPath:destination error:&e])
            error = e.localizedDescription ?: @"Could not publish clip";
        else published=true;
    } else if (!error) error = writer.error.localizedDescription ?: @"MP4 finalization failed";
    if (writer.status != AVAssetWriterStatusCompleted && temporary)
        [NSFileManager.defaultManager removeItemAtPath:temporary error:nil];
    // CompleteFrames and the packet barrier have retired all encoder callbacks.
    // Keep terminal metadata for the menu, but no idle capture/encoding resources.
    if (encoder) { VTCompressionSessionInvalidate(encoder); CFRelease(encoder); encoder=NULL; }
    if (cache) { CVMetalTextureCacheFlush(cache,0); CFRelease(cache); cache=NULL; }
    if (pool) { CFRelease(pool); pool=NULL; }
    pipeline=nil; device=nil; input=nil; writer=nil;
    [frames removeAllObjects]; [packets removeAllObjects];
    ready = false; ended=true;
    dispatch_group_leave(finished);
}
'''),
 ]
def original_source(source):
    import hashlib
    for start,end,digest,body in ORIGINAL_METHODS:
        body=body.lstrip('\n')
        assert hashlib.sha256(body.encode()).hexdigest()==digest
        a=source.index(start); b=source.index(end,a)
        source=source[:a]+body+source[b:]
    return source.replace('- (void)audioTick;\n','')

# Pre-fix ClipAudio fragments for the jittered-stamp (case 1) and video-pause
# (case 2) proofs, each pinned by SHA-256 and swapped into the PCM control.
OLD_FRAGMENTS = {
    1: ('    static void receive(void *context', '    bool start() {', 'a3a74e8d77b5398783fd2cf95a496e25246ecc4575ce24fc81ca483c69ff7613', r'''
    static void receive(void *context, const DsTimedMixBlock *block) {
        auto &a=*(ClipAudio *)context;
        if (!block->pcm51 || !block->sample_rate || !block->first_sample_host_time) { a.fault=true; return; }
        uint64_t w=a.write.load(std::memory_order_relaxed), r=a.read.load(std::memory_order_acquire);
        if (block->frames>capacity-(w-r)) { a.fault=true; return; }
        bool changed=a.stream!=block->stream_id || a.generation!=block->clock_generation || (block->flags&DS_CAPTURE_DISCONTINUITY);
        if(changed) { a.stream=block->stream_id; a.generation=block->clock_generation; ++a.segment; }
        for (size_t i=0;i<block->frames;i++) {
            float stereo[2]; media::fold_to_stereo(block->pcm51+i*6,stereo,1);
            a.points[(w+i)%capacity]={block->first_sample_host_time*a.tickSeconds+(double)i/block->sample_rate,stereo[0],stereo[1],a.segment};
        }
        a.maximumQueued.store(std::max(a.maximumQueued.load(),w-r+block->frames));
        // Publish the segment boundary before exposing its points to the consumer.
        if(changed) a.latestStart.store(w,std::memory_order_release);
        a.write.store(w+block->frames,std::memory_order_release);
    }
'''),
    2: ('        if(forcePressure) return true;', '        int64_t target=', '1b9349284dedc147230459eb64bd3625160b54c7b9e21197eb1a61d1bd6f082f', r'''
        if(forcePressure) return true;
#endif
'''),
}
def old_fragment(source,case):
    import hashlib
    start,end,digest,body=OLD_FRAGMENTS[case]
    body=body.lstrip('\n')
    assert hashlib.sha256(body.encode()).hexdigest()==digest
    a=source.index(start); b=source.index(end,a)
    return source[:a]+body+source[b:]

results=[]
def run(name,source,d,defines=()):
    exe=d/name
    cmd=['clang++','-std=c++17','-O1','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-parameter','-fobjc-arc','-Wno-deprecated-declarations','-I'+str(RT),
         '-DCLIPREC_SOURCE="'+str(source)+'"',*defines,str(RT/'cliprec_audio_test.mm'),
         str(ROOT/'media/media.cpp'),'-I'+str(ROOT/'third_party/ffmpeg/include')]
    for lib in ('avformat','avcodec','swscale','swresample','avutil'):
        cmd+=[str(ROOT/'third_party/ffmpeg/lib'/('lib'+lib+'.a'))]
    cmd+=['-lz']
    for framework in ('AudioToolbox','CoreAudio','CoreFoundation','Cocoa','Metal','QuartzCore','AVFoundation','VideoToolbox','CoreVideo','CoreMedia'):
        cmd+=['-framework',framework]
    built=subprocess.run(cmd+['-o',str(exe)],capture_output=True,text=True)
    assert built.returncode==0,built.stderr[-4000:]
    with (CACHE/(name+'-run.log')).open('w') as log:
        p=subprocess.run([str(exe),str(d/(name+'-output'))],stdout=log,stderr=subprocess.STDOUT,text=True,timeout=120)
    output=(CACHE/(name+'-run.log')).read_text()
    results.append(name+': '+output.strip())
    (CACHE/'results.log').write_text('\n\n'.join(results))
    print(name+': '+output.strip(),flush=True)
    return p.returncode,output

def mutant_source(source,name):
    mutations={
        'startup-gate': [('if(audio && !audio->covers(f->time))', 'if(false)')],
        'removed-audio': [('bool ok=!rc && [input appendSampleBuffer:sample];','bool ok=!rc;')],
        'skew-100ms': [('double t=epoch+(double)(emitted+n)/48000;', 'double t=epoch+(double)(emitted+n)/48000-0.1;')],
        'channel-swap': [('stereo[0],stereo[1]','stereo[1],stereo[0]')],
        'wrong-rate': [('AVSampleRateKey:@48000','AVSampleRateKey:@44100'),('asbd.mSampleRate=48000','asbd.mSampleRate=44100')],
        'lost-final-drain': [('audio->pump(epoch,videoEnd,true)','true'),
             ('writing && drainedOK && !audio->drained(videoEnd)','false')],
    }
    for before,after in mutations[name]:
        assert source.count(before)==1,(name,before)
        source=source.replace(before,after)
    return source

with tempfile.TemporaryDirectory(dir=CACHE,prefix='proof-') as scratch:
    d=Path(scratch)
    production=(RT/'cliprec.mm').read_text()
    original=d/'original.mm'
    original.write_text(original_source(production))
    rc,output=run('original',original,d,['-DCLIPREC_AUDIO_BASELINE=1'])
    assert rc==1 and 'audio_tracks=0 required=1 video_frames=36 generated_pcm_frames=32896' in output,(rc,output[-2000:])
    # Diagnostic control only: MOV/float PCM exercises the actual capture,
    # conversion, VT, timeline and drain when the sandbox denies AAC services.
    pcm_source=production.replace('fileType:AVFileTypeMPEG4','fileType:AVFileTypeQuickTimeMovie')
    pcm_source=pcm_source.replace('AVFormatIDKey:@(kAudioFormatMPEG4AAC)','AVFormatIDKey:@(kAudioFormatLinearPCM)')
    pcm_source=pcm_source.replace('AVNumberOfChannelsKey:@2, AVEncoderBitRateKey:@192000',
        'AVNumberOfChannelsKey:@2, AVLinearPCMBitDepthKey:@32, AVLinearPCMIsFloatKey:@YES, AVLinearPCMIsNonInterleaved:@NO, AVLinearPCMIsBigEndianKey:@NO')
    pcm=d/'pcm.mm'; pcm.write_text(pcm_source)
    rc,output=run('pcm-control',pcm,d,['-DCLIPREC_PCM_CONTROL=1'])
    assert rc==0,(rc,output[-2000:])
    # Both cases must fail on the pre-fix fragments (numbers in the docstring).
    for case,needle in ((1,'jitter: stamps=+-5ms'),(2,'pause: seconds=8.000')):
        source=d/('old-case%d.mm'%case); source.write_text(old_fragment(pcm_source,case))
        rc,output=run('pcm-old-case%d'%case,source,d,['-DCLIPREC_PCM_CONTROL=1','-DCLIPREC_CASE=%d'%case])
        assert rc==1 and 'FAIL cliprec:' in output and needle in output,(case,rc,output[-2000:])
    mutants=('removed-audio','skew-100ms','channel-swap','wrong-rate','lost-final-drain','startup-gate')
    for name in mutants:
        source=d/(name+'.mm'); source.write_text(mutant_source(pcm_source,name))
        rc,output=run('pcm-'+name,source,d,['-DCLIPREC_PCM_CONTROL=1','-DCLIPREC_MUTANT=1'])
        assert rc==1 and 'FAIL cliprec:' in output,(name,rc,output[-2000:])
    rc,output=run('fixed',RT/'cliprec.mm',d)
    if rc==3 and 'SKIP: AAC encoder unavailable' in output:
        print('SKIP: AAC encoding/decoding and AAC mutants remain unverified; PCM controls passed')
        raise SystemExit(3)
    assert rc==0,(rc,output[-2000:])
    for name in mutants:
        source=d/('aac-'+name+'.mm'); source.write_text(mutant_source(production,name))
        rc,output=run('aac-'+name,source,d,['-DCLIPREC_MUTANT=1'])
        assert rc==1 and 'FAIL cliprec:' in output,(name,rc,output[-2000:])
    print('cliprec_audio_test PASS: original silent clip rejected; AAC content, A/V markers and all five requested mutants and the startup control checked')
