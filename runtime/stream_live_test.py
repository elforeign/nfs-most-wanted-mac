#!/usr/bin/env python3
"""Explicit Go -> Metal/H.264 + synthetic AAC -> TS -> loopback HTTPS only.
Original/fixed: encoder status and capture session reason each fail/pass (1/0).
ASan/UBSan good: live-only 333 checks/0 failures, attached 323/0, simultaneous
Stop 314/0; each posts
2 TS segments + 2 media playlists with 0 key/session matches. Independent clip: 5 decoded frames/0 bad,
0.150000 s. Fresh Go begins with IDR; stale callbacks admit 0 segments.
Setup/encoder-failure/transfer create 0 sessions; restart/diagnostic: 0 sessions/sockets;
in-flight Stop cancels >=1 task and releases all retained bytes. HTTP, disconnect,
deadline and response-cap failures preserve recording/replay, with no reconnect.
Fourteen compiled controls exit 1 on their named assertion (good exit 0): result-after-
cancel, premature AAC close (2240/7200 decoded samples), missing file IDR, old file timeline, lost replay request, removed live start,
coupled Stop, missing detach, missing live hold, unnamed encoder reason (code=0, as 5012), late generation and omitted poster Stop.
Result-after-cancel writes 0 files versus fixed 1; both outcomes reproduced.
Focus loss (focus-gap: 12.6 s without presented frames while audio runs, as after a Space swipe):
fixed posts 10 segments with 365 held frames and no error; the no-hold control (original
behaviour) stops after 2 posts with "live: error kind=encoder reason=no-idr-5s".
Reuses the reviewed TLS fixture and synthetic presentation fixture, without games.
"""
from pathlib import Path
import importlib.util
import os
import hashlib
import secrets
import subprocess
import sys
import tempfile

ORIGINAL_CAPTURE_FAILURE = 'if(broadcast) { livestream_stop(); liveState=4; broadcast=false; }'
ORIGINAL_ENCODER_FAILURE = 'livestream_stop(); [self detachLive]; liveState=4;'
ORIGINAL_FAILURE_DIGESTS = ['1e00c9516f529e346960a4c962e0eeb6ef919aa70c3547cf5fd85d4b1600854a', '97f58253b500500feabb65bdb26c6c0c72421911cd6a4c94e9e1feaf1ec6a5d0']

RT=Path(__file__).resolve().parent
ROOT=RT.parent
CACHE=ROOT/'.metal-cache/stream-live'


def main():
    if sys.platform!='darwin' or not (ROOT/'third_party/ffmpeg/lib/libavformat.a').exists():
        print('SKIP: requires macOS media frameworks and local FFmpeg'); return 3
    CACHE.mkdir(parents=True,exist_ok=True)
    spec=importlib.util.spec_from_file_location('tls_fixture',RT/'livestream_test.py')
    fixture_module=importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture_module)
    spec=importlib.util.spec_from_file_location('key_privacy',RT/'stream_key_test.py')
    privacy=importlib.util.module_from_spec(spec); spec.loader.exec_module(privacy)
    poster=(RT/'livestream.mm').read_text()
    marker='auto next=Poster::create(key);'
    assert poster.count(marker)==1
    poster=poster.replace(marker,'auto next=Poster::create(key,proofOptions);')
    marker='session=[NSURLSession sessionWithConfiguration:configuration delegate:delegate delegateQueue:callbacks];'
    poster=poster.replace(marker,'++createdSessions; '+marker)
    capture=(RT/'cliprec.mm').read_text()
    key_source=(RT/'stream_key.m').read_text()
    wrapper='\nint proof_key(const char *bytes) { return replaceKey([NSString stringWithUTF8String:bytes]); }\n'
    with tempfile.TemporaryDirectory(prefix='proof-',dir=CACHE) as scratch:
        d=Path(scratch); fixture_module.certificate(d); fixture_module.socket_probe(d)
        (d/'poster.mm').write_text(poster)
        keypath=d/'key.m'; keypath.write_text(key_source+wrapper)
        keyobject=d/'key.o'
        result=subprocess.run(['clang','-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-fobjc-arc','-I'+str(RT),'-c',str(keypath),'-o',str(keyobject)],capture_output=True,text=True)
        assert not result.returncode,result.stderr[-3000:]
        flags=['-std=c++17','-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-Wall','-Wextra','-Werror','-Wno-unused-function',
            '-Wno-deprecated-declarations','-fobjc-arc','-I'+str(RT),'-I'+str(ROOT/'third_party/ffmpeg/include')]
        # Mutation builds change only the included capture/poster source or key object.
        # Compile their unchanged dependencies once; every executable keeps sanitizers.
        objects=[]
        sources=[RT/'live_encode.mm',RT/'live_seg.mm',RT/'clip_packets.mm',RT/'replay.mm',RT/'replay_export.mm',
                 ROOT/'host/audio/dsound_host.cpp',ROOT/'media/media.cpp']
        for index,source in enumerate(sources):
            obj=d/('common-'+str(index)+'.o')
            built=subprocess.run(['clang++',*flags,'-DLIVESTREAM_TESTING=1','-c',str(source),'-o',str(obj)],capture_output=True,text=True,timeout=90)
            (CACHE/(source.stem+'-build.log')).write_text(built.stdout+built.stderr)
            assert not built.returncode,built.stderr[-4000:]
            objects.append(str(obj))
        command=['clang++',*flags,'-DSTREAM_POSTER_SOURCE="'+str(d/'poster.mm')+'"',str(RT/'stream_live_test.mm'),str(keyobject),
                 *objects,str(d/'socket-probe.dylib')]
        for library in ('avformat','avcodec','swscale','swresample','avutil'):
            command.append(str(ROOT/'third_party/ffmpeg/lib'/('lib'+library+'.a')))
        command+=['-lz']
        for framework in ('Cocoa','Metal','QuartzCore','AVFoundation','VideoToolbox','CoreVideo','CoreMedia','AudioToolbox','CoreAudio','Security'):
            command+=['-framework',framework]
        exe=d/'good'
        built=subprocess.run(command+['-o',str(exe)],capture_output=True,text=True,timeout=90)
        (CACHE/'build.log').write_text(built.stdout+built.stderr)
        assert not built.returncode,built.stderr[-4000:]
        sentinel='sk_'+secrets.token_hex(60)+'+&=/%?#é'
        libraries=':'.join(filter(None,(str(d/'socket-probe.dylib'),os.environ.get('DYLD_INSERT_LIBRARIES'))))
        def run(binary,mode,server_mode='segments',broken=False,expected=None):
            fixture=fixture_module.Fixture(d/'cert.pem',d/'key.pem',sentinel,server_mode)
            try:
                result=subprocess.run([str(binary),mode,fixture.base,str(d/'cert.der'),str(d/'profile'/'clips'/(binary.name+'-'+mode+'-'+server_mode))],input=sentinel+'\n',
                    capture_output=True,text=True,timeout=60,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0',DYLD_INSERT_LIBRARIES=libraries,CFFIXED_USER_HOME=str(d/'profile'),USERDATA=str(d/'profile')))
            finally: fixture.close()
            profile=d/'profile'/'clips'/(binary.name+'-'+mode+'-'+server_mode)
            session_path=Path(str(profile)+'.session.txt')
            session_text=session_path.read_text() if session_path.exists() else ''
            safe_session,session_matches=privacy.private_output(session_text,sentinel)
            (CACHE/(binary.name+'-'+mode+'-'+server_mode+'-session.txt')).write_text(safe_session)
            assert not session_matches,'private session sink'
            output=result.stdout+result.stderr
            safe,matches=privacy.private_output(output,sentinel)
            (CACHE/(binary.name+'-'+mode+'.log')).write_text(safe)
            valid=not result.returncode and not matches and not fixture.invalid and not fixture.escaped
            if mode in ('restart','setup-stop','diagnostic-off','encoder-failure','capture-transfer','capture-failure'): valid=valid and not fixture.accepted
            elif mode in ('only','attached','stop-both'):
                valid=valid and len(fixture.received)>=2 and len(fixture.playlists)>=2
                valid=valid and 'live: Go live' in session_text and 'live: first segment accepted code=200' in session_text
                valid=valid and 'live: first playlist accepted code=200' in session_text and 'live: Stop accepted=' in session_text
            if mode=='inflight-stop': valid=valid and len(fixture.received)>=1
            if mode=='focus-gap': valid=valid and len(fixture.received)>=9 and 'live: error' not in session_text
            if broken and expected: assert result.returncode==1 and expected in output, 'wrong failure assertion: '+binary.name
            print(f'{binary.name}/{mode}: rc={result.returncode} posts={len(fixture.received)} invalid={fixture.invalid} '
                  f'playlists={len(fixture.playlists)} key_matches={matches} session_key_matches={session_matches}; '+safe.strip().replace('\n','; '),flush=True)
            assert valid!=broken,'lifecycle oracle result: '+mode
        for mode in ('only','stop-both','attached','setup-stop','restart','diagnostic-off','encoder-failure','capture-transfer','capture-failure'):
            run(exe,mode)
        run(exe,'inflight-stop','stall')
        run(exe,'focus-gap')
        for mode,server_mode in [('failure-http','http'),('failure-network','disconnect'),('failure-deadline','stall'),
                                 ('failure-network','large-body'),('failure-401','unauthorized'),('failure-401','playlist401')]:
            run(exe,mode,server_mode)
        # Run the original reason-dropping capture paths against the same status/log assertions.
        for text,digest in zip((ORIGINAL_CAPTURE_FAILURE,ORIGINAL_ENCODER_FAILURE),ORIGINAL_FAILURE_DIGESTS):
            assert hashlib.sha256(text.encode()).hexdigest()==digest
        reason_original=capture.replace('if(broadcast) { livestream_fail(LIVESTREAM_FAILURE_CAPTURE); liveState=4; broadcast=false; }',ORIGINAL_CAPTURE_FAILURE)
        before='livestream_fail_reason(LIVESTREAM_FAILURE_ENCODER,liveEncoder?(int)liveEncoder->status():0,"setup"); [self detachLive]; liveState=4;'
        assert reason_original.count(before)==1
        reason_original=reason_original.replace(before,ORIGINAL_ENCODER_FAILURE)
        before=('int status=liveEncoder?(int)liveEncoder->status():0;\n        // A failed segmenter also refuses the encoder\'s packets (status -12915): name the upstream cause first.\n'
            '        const char *seg=liveSegments && liveSegments->stats().failed?liveSegments->stats().reason:nullptr;\n'
            '        const char *reason=seg?seg:status?"status":!liveEncoder?"detached":!liveSegments?"no-segmenter":nullptr;\n'
            '        if(livestream_status()==1) livestream_fail_reason(LIVESTREAM_FAILURE_ENCODER,status,reason?reason:"segmenter");\n        else livestream_stop();\n        [self detachLive]; liveState=4;')
        assert reason_original.count(before)==1
        reason_original=reason_original.replace(before,ORIGINAL_ENCODER_FAILURE)
        path=d/'original-reasons.mm'; path.write_text(reason_original)
        binary=d/'original-reasons'
        built=subprocess.run(command+['-DCLIPREC_SOURCE="'+str(path)+'"','-o',str(binary)],capture_output=True,text=True,timeout=90)
        assert not built.returncode,built.stderr[-4000:]
        run(binary,'encoder-failure',broken=True,expected='encoder failure reason reaches status row')
        run(binary,'capture-failure',broken=True,expected='capture failure session reason')
        mutations=[
            ('early-aac-close','if(old->audioFormat && old->audioEnd.load()<old->closeAt.load()','if(false && old->audioFormat && old->audioEnd.load()<old->closeAt.load()','only'),
            ('cancel-before-result','const bool clean=!sink->failed();\n    outputs.remove(sink);','outputs.remove(sink);\n    const bool clean=!sink->failed();','only'),
            ('no-file-idr','fileNeedsIDR=true;','fileNeedsIDR=false;','only'),
            ('file-timeline','[writer startSessionAtSourceTime:CMSampleBufferGetPresentationTimeStamp(p->sample())];','[writer startSessionAtSourceTime:kCMTimeZero];','only'),
            ('replay-request','s->replayRequested=true;','s->replayRequested=false;','capture-transfer'),
            ('no-start','s->broadcast=true; liveState=1; active=true;','s->broadcast=false; liveState=0; active=true;','only'),
            ('coupled-stop','liveState=3; s->broadcast=false;','liveState=3; s->broadcast=false; active=false; dispatch_async(s->work, ^{ [s stop]; });','attached'),
            ('no-detach','[s detachLive]; if(!s->stopping) liveState=0;','liveState=0;','attached'),
            ('no-hold','if(broadcast && ready && liveState==2) [self holdLive];','','focus-gap'),
            ('unnamed-reason','livestream_fail_reason(LIVESTREAM_FAILURE_ENCODER,status,reason?reason:"segmenter");','livestream_fail(((void)reason,LIVESTREAM_FAILURE_ENCODER));','encoder-failure'),
        ]
        expected_failures={
            'early-aac-close':'independent file Stop retains AAC tail',
            'cancel-before-result':'independent file stop publishes one clip',
            'no-file-idr':'attached clip starts at its own decodable keyframe and timeline',
            'file-timeline':'attached clip starts at its own decodable keyframe and timeline',
            'replay-request':'queued replay Arm survives live Stop',
            'no-start':'repeated Go is idempotent',
            'coupled-stop':'Stop live preserves recording and replay',
            'no-detach':'Stop releases live encoders and segment queues',
            'no-hold':'live survives focus loss',
            'unnamed-reason':'encoder failure reason reaches status row',
        }
        for name,before,after,mode in mutations:
            assert capture.count(before)==1,(name,capture.count(before))
            path=d/(name+'.mm');path.write_text(capture.replace(before,after))
            binary=d/name
            built=subprocess.run(command+['-DCLIPREC_SOURCE="'+str(path)+'"','-o',str(binary)],capture_output=True,text=True,timeout=90)
            assert not built.returncode,built.stderr[-4000:]
            run(binary,mode,broken=True,expected=expected_failures[name])
        before='return !poster || poster->post(std::move(segment));'
        after='(void)poster; std::shared_ptr<Poster> later; { std::lock_guard<std::mutex> guard(streamLock); later=currentStream; } return !later || later->post(std::move(segment));'
        assert poster.count(before)==1
        (d/'poster.mm').write_text(poster.replace(before,after))
        binary=d/'late-generation'
        built=subprocess.run(command+['-o',str(binary)],capture_output=True,text=True,timeout=90)
        assert not built.returncode,built.stderr[-4000:]
        run(binary,'attached',broken=True,expected='stale generation cannot admit work after Stop')
        # The UI Stop bridge must cancel the transport as well as detach capture.
        (d/'poster.mm').write_text(poster)
        before='livestream_stop(); cliprec_live_stop(); notice="Stopped";'
        assert key_source.count(before)==1
        keypath.write_text(key_source.replace(before,'cliprec_live_stop(); notice="Stopped";')+wrapper)
        result=subprocess.run(['clang','-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-fobjc-arc','-I'+str(RT),'-c',str(keypath),'-o',str(keyobject)],capture_output=True,text=True)
        assert not result.returncode,result.stderr[-3000:]
        binary=d/'stop-poster'
        built=subprocess.run(command+['-o',str(binary)],capture_output=True,text=True,timeout=90)
        assert not built.returncode,built.stderr[-4000:]
        run(binary,'inflight-stop','stall',broken=True,expected='Stop cancels the in-flight generation')
    print('stream_live_test PASS: 12 lifecycle faults rejected; focus loss posts>=9; restart broadcasts=0')
    return 0

if __name__=='__main__': raise SystemExit(main())
