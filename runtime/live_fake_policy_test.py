#!/usr/bin/env python3
"""A real stream key never reaches the loopback fake ingest (live_fake_policy_test.mm).
Fixed: real_host=a.upload.youtube.com, fake_host=127.0.0.1:4567, exit 0.
Original control (the env-driven destination, pinned by SHA-256): real_host=127.0.0.1, exit 1.
Posters are created only; nothing opens a session or a socket.
"""
from pathlib import Path
import hashlib
import subprocess
import sys
import tempfile
RT=Path(__file__).resolve().parent
ROOT=RT.parent
FIXED='''        NSString *fake=nil;
        if(fakePort>0 && fakePort<=65535 && key==fakeKey) fake=[NSString stringWithFormat:@"http://127.0.0.1:%d/http_upload_hls",fakePort];'''
ORIGINAL='''        NSString *fake=nil;
        if(const char *port=getenv("NFS_TEST_LIVE_FAKE")) {
            char *end=nullptr; long n=strtol(port,&end,10);
            if(*port && end && !*end && n>0 && n<=65535) fake=[NSString stringWithFormat:@"http://127.0.0.1:%ld/http_upload_hls",n];
        }'''
ORIGINAL_SHA='79ddbc16fc4d3e657d2a6be1a382732f2c2a0d6f0514c5aff037f46b5072f114'

def build(source,d,name):
    path=d/(name+'.mm'); path.write_text(source)
    exe=d/name
    cmd=['clang++','-std=c++17','-O1','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-parameter','-Wno-deprecated-declarations',
         '-fobjc-arc','-I'+str(RT),'-I'+str(ROOT/'third_party/ffmpeg/include'),'-DLIVE_POLICY_SOURCE="'+str(path)+'"',
         str(RT/'live_fake_policy_test.mm'),str(RT/'clip_packets.mm'),str(RT/'live_seg.mm'),
         *[str(ROOT/'third_party/ffmpeg/lib'/('lib'+n+'.a')) for n in ('avformat','avcodec','avutil')],'-lz']
    for f in ('Foundation','CoreMedia','CoreVideo','VideoToolbox','AudioToolbox','Security'): cmd+=['-framework',f]
    r=subprocess.run(cmd+['-o',str(exe)],capture_output=True,text=True,timeout=120)
    assert r.returncode==0,r.stderr[-3000:]
    p=subprocess.run([str(exe)],capture_output=True,text=True,timeout=30)
    out=(p.stdout+p.stderr).strip()
    print(name+': rc=%d; '%p.returncode+out.replace('\n','; '),flush=True)
    return p.returncode,out

def main():
    if sys.platform!='darwin' or not (ROOT/'third_party/ffmpeg/lib/libavformat.a').exists():
        print('SKIP: requires macOS and local FFmpeg'); return 3
    source=(RT/'livestream.mm').read_text()
    assert source.count(FIXED)==1,'fixed destination fragment'
    assert hashlib.sha256(ORIGINAL.encode()).hexdigest()==ORIGINAL_SHA
    with tempfile.TemporaryDirectory(prefix='live-fake-policy-') as scratch:
        d=Path(scratch)
        rc,out=build(source,d,'fixed')
        assert rc==0 and 'real_host=a.upload.youtube.com' in out and 'fake_host=127.0.0.1:4567' in out,out
        rc,out=build(source.replace(FIXED,ORIGINAL),d,'original')
        assert rc==1 and 'real_host=127.0.0.1' in out,out
    print('live_fake_policy_test PASS: real key -> YouTube, fixed fake key -> loopback; original env destination rejected')
    return 0

if __name__=='__main__': raise SystemExit(main())
