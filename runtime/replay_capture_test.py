#!/usr/bin/env python3
"""Replay actions through production Metal/VT, no game or window.
Shared Disarm: original idle replay sinks=1, fixed=0 (file sink cancelled
only after checking that the recording remains active).
Measured fixed: 1 export, 30.000000 s, source edit 0.123333 after
the clock advances from 140.123333 to 160. Warm-up exports=0. Four broken
controls exit 1: late clock requests end 60 vs 40.123333 and times out;
removed replay-only Arm has replayOnly=0 vs 1; removed shared hook retains
0 packets vs >=1. Disarm releases 2/2 pool/encoder handles. No game or audio.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

RT=Path(__file__).resolve().parent
ROOT=RT.parent
CACHE=ROOT/'.metal-cache/replay-capture'


def main():
    if sys.platform!='darwin' or not (ROOT/'third_party/ffmpeg/lib/libavformat.a').is_file():
        print('SKIP: requires macOS media frameworks and local FFmpeg development libraries')
        return 3
    CACHE.mkdir(parents=True,exist_ok=True)
    source=(RT/'cliprec.mm').read_text()
    mutants=[('idle-sink','s->outputs.remove(s->replaySink);\n            s->replaySink.reset();',';','Disarm detaches the shared replay consumer'),
             ('late-clock','double time=CACurrentMediaTime();','double time=CACurrentMediaTime()+19.876666667;','action saves latched 30-second replay'),
             ('no-arm','current->replayOnly=true;','current->replayOnly=false;','Arm starts replay-only capture'),
             ('no-share','replay::attach(s->outputs,ring,&s->replaySink);','(void)ring;','shared encoder supplies replay packets')]
    with tempfile.TemporaryDirectory() as scratch:
        d=Path(scratch)
        command=['clang++','-std=c++17','-O1','-Wall','-Wextra','-Werror','-Wno-unused-parameter',
             '-Wno-deprecated-declarations','-fobjc-arc','-I'+str(RT),'-I'+str(ROOT/'third_party/ffmpeg/include'),
             str(RT/'replay_capture_test.mm'),str(RT/'replay.mm'),str(RT/'replay_export.mm'),str(RT/'live_encode.mm'),str(RT/'live_seg.mm'), str(RT/'livestream.mm'),str(RT/'clip_packets.mm'),
             str(ROOT/'host/audio/dsound_host.cpp'),str(ROOT/'media/media.cpp')]
        for lib in ('avformat','avcodec','swscale','swresample','avutil'):
            command += [str(ROOT/'third_party/ffmpeg/lib'/('lib'+lib+'.a'))]
        command += ['-lz']
        for framework in ('AudioToolbox','CoreAudio','CoreFoundation','Cocoa','Metal','QuartzCore','AVFoundation','VideoToolbox','CoreVideo','CoreMedia'):
            command += ['-framework',framework]
        for mutant in [None,*mutants]:
            name='fixed' if mutant is None else mutant[0]
            exe=d/name
            cmd=command.copy()
            if mutant:
                _,before,after,failure=mutant
                assert source.count(before)==1,(name,source.count(before))
                path=d/(name+'.mm'); path.write_text(source.replace(before,after))
                cmd+=['-DCLIPREC_SOURCE="'+str(path)+'"']
            built=subprocess.run(cmd+['-o',str(exe)],capture_output=True,text=True)
            assert built.returncode==0,built.stderr[-4000:]
            run=subprocess.run([str(exe),str(d/(name+'-clips'))],capture_output=True,text=True,timeout=90)
            output=run.stdout+run.stderr
            (CACHE/(name+'.log')).write_text(output)
            print(name+': '+output.strip(),flush=True)
            assert run.returncode==(1 if mutant else 0),(run.returncode,output[-4000:])
            if mutant: assert 'FAIL cliprec: '+failure in output,output[-4000:]
    print('replay_capture_test PASS')


if __name__=='__main__': raise SystemExit(main())
