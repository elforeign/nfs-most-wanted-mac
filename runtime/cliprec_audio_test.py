#!/usr/bin/env python3
"""Mandatory decoded PCM proof through the production capture and packet paths.
600 video frames / 480000 stereo samples in 10 s; markers 0 ms, drift 0 ms,
normalized RMS 0.001459, exact endpoint. Five final-drain controls pass and five
removed-drain mutants fail (46880/48000 frames, tail RMS 0.667342 vs 0.002014).
Other rejected controls: zero audio tracks; +100 ms; swapped channels RMS
1.237052; 44.1 kHz clock fault 44240/48000 frames; truncated tail 46960/48000;
removed startup gate accepted/epoch 1/1 vs 0/0. All broken runs exit 1.
Jitter +-5 ms: RMS 0.012352, aligned markers 0 ms. Eight-second presentation
pause: resumed RMS 0.001445, markers 0 ms. Raw queue peak 95744/96000 frames.
File stall >=5 s: healthy 360/360 video, same audio packet count as file,
zero missing, file marker offset/drift 0/0 ms. Failed file: healthy 60/60,
file prefix 10 frames, explicit error. Delayed file append leaves capture's
endpoint at 48000 samples while the file packet waits.
Drain regression: original Saving=1, completed=0 at deadline+0.252 s;
fixed Saving=0, completed=1 at +0.009 s, immediate restart accepted=1.
Blocked file append also completes at +0.004 s and safely cleans up on return.
Failed-file requested endpoint: original 1.000000 s, fixed 0.166667 s for
10 frames. MOV decoded duration is 0.166667 s on both; endpoint mutant fails.
Callback fold mutant 12673 ns >9260 ns budget; shared-queue mutant 0/300 vs
healthy 300/300 (clip_packets_test.py). Numbers are measured controls.
PCM coverage never skips. cliprec_aac_test separately checks actual host AAC;
codec factory denial 1718449215 exits 3, never an AAC content pass.
Live focus gap (AAC coverage; 12 s without frames through the real ClipAudio path): the file recorded with
live equals the same run without live (864000 samples, 480128 zero samples); live cuts 6 segments from 346 held
frames with 0 pool drops. Controls: old shared target (file sound to the held frames' end) 0 zero samples;
live sound stopped at the video end 0 gap segments; leaked held surface 120 pool drops, capture overloaded.
Results remain in worktree scratch. No game, device or listening claim.
Load: old startup/clock exit1 -> fixed0 (2.1 s / 20 ms delays); 1 s stall pause old file270/270 exit1 -> fixed0/270 exit0.
Twelve background suite tests, three runs each: median total 292.099 ->
184.597 s; compile/link 164.501 -> 55.359 s; execution 127.422 -> 129.018 s.
All controls and pinned mutants retained; support compilations 133 -> 8.
"""
from pathlib import Path
import subprocess
import tempfile
import time
RT=Path(__file__).resolve().parent
ROOT=RT.parent
CACHE=ROOT/'.metal-cache'/'cliprec-audio'
CACHE.mkdir(parents=True,exist_ok=True)
# The old writer-coupled method snapshots cannot link to the extracted sink
# transport. Keep a mandatory video-only control through the current transport;
# it must still fail the independent decoder's one-audio-track requirement.
results=[]
support_objects={}
def run(name,source,d,defines=(),repeats=1):
    exe=d/name
    flags=['clang++','-std=c++17','-O1','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-parameter','-fobjc-arc','-Wno-deprecated-declarations','-I'+str(RT),'-I'+str(ROOT/'third_party/ffmpeg/include')]
    # Only the packet encoder depends on the codec define. Other fixture defines
    # affect the harness/source include; no support source reads them.
    codec=tuple(flag for flag in defines if flag.startswith('-DCLIPREC_PCM_CONTROL='))
    objects=[]
    started=time.monotonic()
    compiled=0
    for src in [ROOT/'media/media.cpp',*(RT/(n+'.mm') for n in ('clip_packets','replay','replay_export','live_encode','live_seg','livestream'))]:
        mode=codec if src.stem=='clip_packets' else ()
        key=(d,src,mode)
        if key not in support_objects:
            obj=d/(src.stem+('-pcm' if mode else '-aac')+'.o')
            built=subprocess.run(flags+list(mode)+['-c',str(src),'-o',str(obj)],capture_output=True,text=True)
            assert built.returncode==0,built.stderr[-4000:]
            support_objects[key]=str(obj)
            compiled+=1
        objects.append(support_objects[key])
    if compiled:
        print('phase support '+name+': %.3fs (%d objects)'%(time.monotonic()-started,compiled),flush=True)
    cmd=flags+['-DCLIPREC_SOURCE="'+str(source)+'"',*defines,str(RT/'cliprec_audio_test.mm'),*objects]
    for lib in ('avformat','avcodec','swscale','swresample','avutil'):
        cmd+=[str(ROOT/'third_party/ffmpeg/lib'/('lib'+lib+'.a'))]
    cmd+=['-lz']
    for framework in ('AudioToolbox','CoreAudio','CoreFoundation','Cocoa','Metal','QuartzCore','AVFoundation','VideoToolbox','CoreVideo','CoreMedia'):
        cmd+=['-framework',framework]
    started=time.monotonic()
    built=subprocess.run(cmd+['-o',str(exe)],capture_output=True,text=True)
    print('phase compile '+name+': %.3fs'%(time.monotonic()-started),flush=True)
    assert built.returncode==0,built.stderr[-4000:]
    runs=[]
    for attempt in range(repeats):
        label=name+('-run%d'%(attempt+1) if repeats>1 else '')
        logpath=CACHE/(label+'-run.log')
        with logpath.open('w') as log:
            started=time.monotonic()
            p=subprocess.run([str(exe),str(d/(label+'-output'))],stdout=log,stderr=subprocess.STDOUT,text=True,timeout=120)
        print('phase run '+label+': %.3fs'%(time.monotonic()-started),flush=True)
        output=logpath.read_text()
        results.append(label+': '+output.strip())
        (CACHE/('aac-results.log' if name.startswith('aac-') else 'results.log')).write_text('\n\n'.join(results))
        print(label+': '+output.strip(),flush=True)
        runs.append((p.returncode,output))
    return runs if repeats>1 else runs[0]

def mutant_source(source,name):
    mutations={
        'file-endpoint': [('[writer endSessionAtSourceTime:CMTimeMakeWithSeconds(file->videoEnd,60000)];',
                           '[writer endSessionAtSourceTime:CMTimeMakeWithSeconds(videoEnd,60000)];')],
        'drain-deadline': [('        outputs.cancel();',
                           '        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_MSEC),work,^{ [self finishWhenDrained]; }); return;')],
        'startup-gate': [('if(audio && !audio->covers(f->time))', 'if(false)')],
        'removed-audio': [('if(destinationFile->audioFormat) {\n            audioInput=', 'if(false) {\n            audioInput='), ('if(audio && ![self pumpAudio:epoch final:false])', 'if(false)'), ('bool drainedOK=!writing || [self pumpAudio:epoch final:true];','bool drainedOK=true;')],
        'skew-100ms': [('double t=epoch+(double)(emitted+n)/48000;', 'double t=epoch+(double)(emitted+n)/48000-0.1;')],
        'channel-swap': [('stereo[0],stereo[1]','stereo[1],stereo[0]')],
        'wrong-rate': [('(double)(emitted+n)/48000;', '(double)(emitted+n)/44100;')],
        'lost-final-drain': [('[self pumpAudio:epoch final:true]','true'),
             ('writing && drainedOK && !audio->drained(videoEnd)','false')],
        # Silently ends the audio track 1024 samples before the video end.
        'truncated-tail': [('int64_t target=(int64_t)llround(end*48000);','int64_t target=(int64_t)llround(end*48000)-1024;'),
             ('return emitted>=(int64_t)llround(end*48000);','return emitted>=(int64_t)llround(end*48000)-1024;')],
    }
    for before,after in mutations[name]:
        assert source.count(before)==1,(name,before)
        source=source.replace(before,after)
    return source

PUMP_AUDIO='- (bool)pumpAudio:(double)from final:(bool)final { return audio->pump(from,videoEnd,final,liveAudioEnd); }'
HOLD='if(liveHold!=pixel) { CVPixelBufferRetain(pixel); if(liveHold) CVPixelBufferRelease(liveHold); liveHold=pixel; }'
def live_gap(source,d):
    """Live across a 12 s focus gap: the file's sound is unchanged by live, live keeps cutting segments (AAC only:
    the live TS muxer takes no PCM)."""
    def gap_lines(output):
        return [line for line in output.splitlines() if line.startswith('live_gap_file:')]
    paths={}
    for name,before,after in (('',None,None),
            ('shared-target',PUMP_AUDIO,PUMP_AUDIO.replace('pump(from,videoEnd,','pump(from,fmax(videoEnd,liveAudioEnd),')),
            ('live-video-end',PUMP_AUDIO,PUMP_AUDIO.replace(',liveAudioEnd); }',',videoEnd); }')),
            ('leaked-hold',HOLD,'if(liveHold!=pixel) { CVPixelBufferRetain(pixel); liveHold=pixel; }')):
        text=source
        if before:
            assert text.count(before)==1,(name,before)
            text=text.replace(before,after)
        path=d/('live-gap-'+(name or 'fixed')+'.mm'); path.write_text(text); paths[name]=path
    rc,without=run('aac-live-gap-without',paths[''],d,['-DCLIPREC_CASE=8','-DCLIPREC_LIVE_GAP=0'])
    assert rc==0 and len(gap_lines(without))==1,(rc,without[-2000:])
    rc,withlive=run('aac-live-gap-with',paths[''],d,['-DCLIPREC_CASE=8'])
    assert rc==0 and gap_lines(withlive)==gap_lines(without),(rc,gap_lines(withlive),gap_lines(without))
    rc,output=run('aac-live-gap-only',paths[''],d,['-DCLIPREC_CASE=9'])
    assert rc==0,(rc,output[-2000:])
    rc,output=run('aac-live-gap-shared-target',paths['shared-target'],d,['-DCLIPREC_CASE=8'])
    assert rc==0 and gap_lines(output) and gap_lines(output)!=gap_lines(without),(rc,gap_lines(output))
    rc,output=run('aac-live-gap-live-video-end',paths['live-video-end'],d,['-DCLIPREC_CASE=9'])
    assert rc==1 and 'FAIL cliprec: live keeps cutting segments through the gap' in output,(rc,output[-2000:])
    rc,output=run('aac-live-gap-leaked-hold',paths['leaked-hold'],d,['-DCLIPREC_CASE=9'])
    assert rc==1 and 'FAIL cliprec:' in output and 'pool_drops=0' not in output,(rc,output[-2000:])

def main(coverage='pcm'):
    with tempfile.TemporaryDirectory(dir=CACHE,prefix=coverage+'-proof-') as scratch:
        d=Path(scratch)
        production=(RT/'cliprec.mm').read_text()
        production=production.replace('NSProcessInfo.processInfo.systemUptime-started', 'fixtureUptime()-started').replace('started = NSProcessInfo.processInfo.systemUptime;', 'started = fixtureUptime();')
        mutants=('removed-audio','skew-100ms','channel-swap','wrong-rate','lost-final-drain','startup-gate','truncated-tail')
        if coverage=='pcm':
            original=d/'original.mm'
            original.write_text(production)
            rc,output=run('video-only',original,d,['-DCLIPREC_AUDIO_BASELINE=1'])
            assert rc==1 and 'audio_tracks=0 required=1 video_frames=36 generated_pcm_frames=32896' in output,(rc,output[-2000:])
            # MOV/float PCM exercises capture, conversion, VT, timeline and drain
            # without the host AAC codec service. This coverage must never skip.
            source=production.replace('fileType:AVFileTypeMPEG4','fileType:AVFileTypeQuickTimeMovie')
            fixed=d/'pcm.mm'; fixed.write_text(source)
            defines=['-DCLIPREC_PCM_CONTROL=1']
        else:
            source=production; fixed=d/'aac.mm'; fixed.write_text(source); defines=[]
        rc,output=run(coverage+'-control',fixed,d,defines)
        if coverage=='aac' and rc==3 and 'SKIP: AAC encoder unavailable' in output:
            print('SKIP: host AAC coverage unavailable; see codec factory status above; mandatory PCM is a separate test')
            return 3
        assert rc==0,(rc,output[-2000:])
        # Five fresh sessions on each path prove the final drain independently
        # of packet/tick scheduling. Compile once, then execute five times.
        for rc,output in run(coverage+'-final-drain',fixed,d,defines+['-DCLIPREC_MUTANT=1'],repeats=5):
            assert rc==0,(rc,output[-2000:])
        if coverage=='pcm':
            for case in (1,2,3,4,5,6,7):
                rc,output=run('pcm-case%d'%case,fixed,d,defines+['-DCLIPREC_CASE=%d'%case])
                assert rc==0,(case,rc,output[-2000:])
            for name,case in (('file-endpoint',5),('drain-deadline',6)):
                mutant=d/(name+'.mm'); mutant.write_text(mutant_source(source,name))
                rc,output=run('pcm-'+name,mutant,d,defines+['-DCLIPREC_CASE=%d'%case])
                failure=('failed file ends at its last written frame' if case==5 else
                         'blocked I/O cannot extend drain deadline')
                assert rc==1 and 'FAIL cliprec: '+failure in output,(name,rc,output[-2000:])
                expected=('requested=1.000000 expected=0.166667' if case==5 else 'completed=0')
                assert expected in output,(name,output[-2000:])
        if coverage=='aac':
            for case in (4,5):
                rc,output=run('aac-file-case%d'%case,fixed,d,['-DCLIPREC_CASE=%d'%case])
                assert rc==0,(rc,output[-2000:])
            live_gap(source,d)
        for name in mutants:
            mutant=d/(name+'.mm'); mutant.write_text(mutant_source(source,name))
            repeats=5 if name=='lost-final-drain' else 1
            runs=run(coverage+'-'+name,mutant,d,defines+['-DCLIPREC_MUTANT=1'],repeats=repeats)
            for rc,output in runs if repeats>1 else [runs]:
                assert rc==1 and 'FAIL cliprec:' in output,(name,rc,output[-2000:])
                if name=='lost-final-drain':
                    assert 'video_end_samples=48000 expected=48000 audio_drained=0' in output,output[-2000:]
                    assert 'decoded:' in output and 'final_drain: tail_normalized_RMS=' in output,output[-2000:]
        name='cliprec_audio_test' if coverage=='pcm' else 'cliprec_aac_test'
        print(name+' PASS: content, A/V markers, mutants and startup checked; final drain real 5/5 PASS, removed drain 5/5 rejected')
        return 0

if __name__=='__main__':
    raise SystemExit(main())
