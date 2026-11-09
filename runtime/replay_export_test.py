#!/usr/bin/env python3
"""Exact MP4 export through the production ring and exporter.
Measured fixed: 35.350 -> 65.350 cut, 30.000000 s, 301 video frames/0 bad
binary markers; 1,440,000 presented AAC samples + 736 raw padding samples,
waveform RMS 0.001057. Broken controls: exposed preroll 31.350000 s;
duplicate admitted=1 vs 0; missing pin first key=-1 vs 34.000 at cut 35.990;
AAC +100 ms RMS 0.244172; cancelled generation stream_changed=0 vs 1.
Both H.264 and HEVC decode all markers. Two explicit saves produce 2 unique
files; blocking subsequent actions produces 1. Seven broken runs fail independently
(the exposed-preroll control also runs with HEVC); no host AAC service is required.
HEVC also cuts at 65.350017 s, off the AAC sample grid, with the same exact span.
"""
from pathlib import Path
import array
import hashlib
import math
import subprocess
import struct
import sys
import tempfile

RT = Path(__file__).resolve().parent
CACHE = RT.parent / '.metal-cache' / 'replay-export'


def aac_fixture(directory):
    """Software AAC fixture: never depends on the host codec service sandbox."""
    build = CACHE/'software-aac'
    build.mkdir(exist_ok=True)
    source = build/'ffmpeg-7.1.1'
    exe = build/'ffmpeg'
    logpath = CACHE/'software-aac-build.log'
    stamp=build/'fixture-v4'
    if not exe.exists() or not stamp.exists():
        archive = RT.parent/'third_party/dl/ffmpeg-7.1.1.tar.xz'
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == '733984395e0dbbe5c046abda2dc49a5544e7e0e1e2366bba849222ae9e3a03b1'
        if not (source/'configure').exists():
            subprocess.run(['tar', '-xJf', str(archive), '-C', str(build)], check=True, capture_output=True)
        with logpath.open('w') as log:
            flags = ['--disable-everything', '--disable-autodetect', '--disable-network', '--disable-doc',
                     '--disable-x86asm', '--enable-ffmpeg', '--enable-encoder=aac,pcm_f32le', '--enable-decoder=pcm_f32le,aac',
                     '--enable-demuxer=pcm_f32le,mov', '--enable-muxer=ipod,pcm_f32le', '--enable-protocol=file',
                     '--enable-filter=aresample,aformat,anull', '--enable-swresample']
            configured = subprocess.run([str(source/'configure'), *flags], cwd=build, stdout=log, stderr=log, timeout=90)
            assert configured.returncode == 0, logpath.read_text()[-3000:]
            built = subprocess.run(['make', '-j4', 'ffmpeg'], cwd=build, stdout=log, stderr=log, timeout=180)
            assert built.returncode == 0, logpath.read_text()[-3000:]
        stamp.touch()
    pcm = array.array('f', (.4*math.sin(2*math.pi*freq*n/48000)*(1 if n>=48000 else .5)
                           for n in range(96000) for freq in (311, 733)))
    raw = directory/'signal.f32'
    with raw.open('wb') as out:
        for _ in range(34):
            pcm.tofile(out)
    target = directory/'signal.m4a'
    encoded = subprocess.run([str(exe), '-v', 'error', '-f', 'f32le', '-ar', '48000', '-ac', '2',
                              '-i', str(raw), '-c:a', 'aac', '-b:a', '192k', '-f', 'ipod', str(target)],
                             capture_output=True, text=True, timeout=60)
    assert encoded.returncode == 0, encoded.stderr[-3000:]
    return target


def decoded_audio(directory):
    clips=list(directory.glob('*.mp4'))
    assert len(clips)==2, clips
    decoded=directory/'decoded.f32'
    run=subprocess.run([str(CACHE/'software-aac/ffmpeg'),'-v','error','-i',str(clips[0]),
                        '-map','0:a:0','-c:a','pcm_f32le','-f','f32le',str(decoded)],capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stderr[-3000:]
    pcm=array.array('f'); pcm.frombytes(decoded.read_bytes())
    # Raw PCM output includes codec padding. Read the actual MP4 audio edit
    # independently and apply its presentation endpoint, rather than supplying
    # the requested duration to the decoder.
    data=clips[0].read_bytes()
    def atoms(start,end):
        while start+8<=end:
            size,kind=struct.unpack_from('>I4s',data,start)
            header=8
            if size==1: size=struct.unpack_from('>Q',data,start+8)[0]; header=16
            if size==0: size=end-start
            assert size>=header and start+size<=end,(kind,size)
            yield kind,start+header,start+size
            start+=size
    moov=next((a,b) for kind,a,b in atoms(0,len(data)) if kind==b'moov')
    movie=list(atoms(*moov))
    mvhd=next(a for kind,a,b in movie if kind==b'mvhd')
    scale=struct.unpack_from('>I',data,mvhd+(20 if data[mvhd] else 12))[0]
    audio_edit=None
    for kind,a,b in movie:
        if kind!=b'trak': continue
        track=list(atoms(a,b))
        mdia=next((c,d) for tag,c,d in track if tag==b'mdia')
        handler=next(c for tag,c,d in atoms(*mdia) if tag==b'hdlr')
        if data[handler+8:handler+12]!=b'soun': continue
        edit=next((c,d) for tag,c,d in track if tag==b'edts')
        elst=next(c for tag,c,d in atoms(*edit) if tag==b'elst')
        assert struct.unpack_from('>I',data,elst+4)[0]==1,'one nonempty AAC edit'
        duration,start=struct.unpack_from('>Qq' if data[elst] else '>Ii',data,elst+8)
        assert start>0,'AAC preroll must be hidden in the file edit'
        audio_edit=round(duration*48000/scale)
    assert audio_edit==1440000,('file AAC presentation samples',audio_edit)
    oracle=array.array('f',(.4*math.sin(2*math.pi*freq*n/48000)*(1 if n>=48000 else .5)
                            for n in range(96000) for freq in (311,733)))
    count=len(pcm)//2
    origin=1696801 if 'fraction' in directory.name else 1696800
    squared=sum((sample-oracle[(i+origin*2)%len(oracle)])**2 for i,sample in enumerate(pcm[:audio_edit*2]))
    rms=math.sqrt(squared/max(1,audio_edit*2))
    print(f'decoded AAC: raw={count}, edit={audio_edit}/1440000, padding={count-audio_edit}, RMS_error={rms:.6f}',flush=True)
    (CACHE/(directory.name+'-audio.log')).write_text(f'raw={count} presented={audio_edit} padding={count-audio_edit} RMS_error={rms:.9f}\n')
    assert 0<=count-audio_edit<1024 and rms<.025,('AAC trim and decoded markers',count,rms)


def main():
    if sys.platform!='darwin' or not (RT.parent/'third_party/dl/ffmpeg-7.1.1.tar.xz').is_file():
        print('SKIP: requires macOS media frameworks and the shipped FFmpeg source archive')
        return 3
    CACHE.mkdir(parents=True, exist_ok=True)
    source = (RT / 'replay_export.mm').read_text()
    ring_source=(RT/'replay.mm').read_text()
    shifted=source.replace('auto source=p->sample();', '''auto source=p->sample();
    if(p->track==clip::Track::audio) {
        CMSampleTimingInfo timing; CMSampleBufferGetSampleTimingInfo(source,0,&timing);
        timing.presentationTimeStamp=CMTimeAdd(timing.presentationTimeStamp,CMTimeMake(1,10));
        CMSampleBufferRef copy=nullptr;
        CMSampleBufferCreateCopyWithNewTiming(nullptr,source,1,&timing,&copy);
        return copy;
    }''')
    unpinned=ring_source.replace('if(CMTIME_IS_NUMERIC(pinned) && CMTimeCompare(pinned,cut)<0) cut=pinned;', '(void)pinned;')
    cases = [('fixed', source, None,ring_source),
             ('hevc',source,None,ring_source),
             ('hevc-fraction',source,None,ring_source),
             ('preroll', source.replace('[writer startSessionAtSourceTime:snapshot.start];',
                                       '[writer startSessionAtSourceTime:CMSampleBufferGetPresentationTimeStamp(snapshot.packets.front()->sample())];'),
              'export succeeds',ring_source),
             ('hevc-preroll', source.replace('[writer startSessionAtSourceTime:snapshot.start];',
                                       '[writer startSessionAtSourceTime:CMSampleBufferGetPresentationTimeStamp(snapshot.packets.front()->sample())];'),
              'export succeeds',ring_source),
             ('duplicate', source.replace('if(s->saving) return false;', 'if(false) return false;').replace(
                 'if(!point.error.empty()) {','if(false) {'),
              'duplicate save rejected',ring_source),
             ('unpin',source,'action reservation pins preceding GOP before file output',unpinned),
             ('aac-shift',shifted,'AAC trim and decoded markers',ring_source),
             ('cancel',source,'disarm and rearm cancel the old save generation',ring_source.replace('lifetime!=point.lifetime || ','')),
             ('resave',source.replace('if(s->saving) return false;', 'if(s->saving || s->text.find("Replay saved:")==0) return false;'),
              'new action after completed save accepted',ring_source)]
    with tempfile.TemporaryDirectory(dir=CACHE) as scratch:
        d = Path(scratch)
        fixture = aac_fixture(d)
        for name, code, failure,ring_code in cases:
            path = d / (name + '.mm'); path.write_text(code)
            ring_path=d/(name+'-ring.mm'); ring_path.write_text(ring_code)
            exe = d / name
            cmd = ['clang++', '-std=c++17', '-O1', '-Wall', '-Wextra', '-Werror',
                   '-Wno-deprecated-declarations', '-fobjc-arc', '-I'+str(RT),
                   str(RT/'replay_export_test.mm'), str(ring_path), str(RT/'clip_packets.mm'), str(path)]
            for framework in ('Foundation', 'AVFoundation', 'CoreMedia', 'CoreVideo', 'AudioToolbox', 'VideoToolbox'):
                cmd += ['-framework', framework]
            built = subprocess.run(cmd+['-o', str(exe)], capture_output=True, text=True)
            assert built.returncode == 0, built.stderr[-4000:]
            result = subprocess.run([str(exe), str(d/(name+'-clips')), str(fixture), *([name] if name.startswith('hevc') else [])], capture_output=True, text=True, timeout=90)
            output = result.stdout+result.stderr
            (CACHE/(name+'.log')).write_text(output)
            print(name+': '+output.strip(), flush=True)
            audio_fault=failure=='AAC trim and decoded markers'
            assert result.returncode == (1 if failure and not audio_fault else 0), (result.returncode, output[-3000:])
            if audio_fault:
                try:
                    decoded_audio(d/(name+'-clips'))
                except AssertionError as error:
                    assert error.args[0][0]=='AAC trim and decoded markers',error
                    print('AAC +100 ms control rejected by decoded waveform',flush=True)
                else:
                    raise AssertionError('AAC timing fault escaped independent decoder')
            elif failure:
                assert 'FAIL replay: '+failure in output, output[-3000:]
            else:
                decoded_audio(d/(name+'-clips'))
    print('replay_export_test PASS: H.264/HEVC exact cut and decoded AAC; 7/7 broken controls rejected')


if __name__ == '__main__':
    raise SystemExit(main())
