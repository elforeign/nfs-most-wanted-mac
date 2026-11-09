#!/usr/bin/env python3
"""Hardware live H.264, independently decoded headers/IDRs and failure controls.
Fixed: SPS High=100, PPS CABAC=1, 361 decoded pictures, 0 B slices/reorder,
4 IDRs at 0/2/4/6 s (120-frame gap); long-GOP fault gives second IDR at 4 s.
Main-profile fault decodes SPS=77 vs 100. CBR setter=472222 vs missing with
average-rate fault; reorder=0 vs 1. Real 1080p/4K negotiated=17000000/50000000.
Rejected negotiation: 10/10 properties, hardware query and creation; ignored
property/query faults leave status=0 vs required error. Wrong width/height/
pixel format reject 3/3 before VT; omitted guard submits 1 (accepted=1 vs 0).
Shared HEVC recording completes 4 while live pending=4; pool_available=0.
Borrowed-surface fault: retained=0 vs 4, pool_available=1. Final refs=5/5
including failed submission (pending=0). Fifth live submission rejects at 4;
unbounded fault accepts it with pending=5, leaving the recording reserve unprotected. HEVC routing accepted=0 packets=0
vs faulty 1/1; cross-route stops live at 0/1 vs 181 frames, recording stays 181.
Capture: 181/181/181 recording/replay/live; failed CBR 181/181/0; removed hook
live=0 vs 181. Off: sessions/submissions/refs=0/0/0; default-on fault creates 1.
Off input: 3110400 identical NV12 bytes; +2 luma fault changes 2073600 bytes.
File/source compressed payload: 36/36 Off, 181/181 On packets byte-identical.
Live/file failure: active=1/error=0, 66/66 live and segmented packets;
replay-only health fault stops capture with active=0/error=1.
Inactive assembly: 0 calls/stack vs 1 injected call. Sixteen broken controls
must fail; artifacts/logs remain in scratch, with no game or network runs.
"""
from pathlib import Path
import re
import hashlib
import subprocess
import sys
import tempfile

RT=Path(__file__).resolve().parent
ROOT=RT.parent
CACHE=ROOT/'.metal-cache/live-encode'

# Frozen pre-live conversion; this baseline is independent of later shader edits.
ORIGINAL_CONVERSION = r'''static NSString *const conversion = @R"metal(
#include <metal_stdlib>
using namespace metal;
float3 srgb(float3 c) {
    return select(1.055*pow(c,float3(1.0/2.4))-0.055,12.92*c,c<=0.0031308);
}
float3 clean(float3 c) { return max(select(c,float3(0),!isfinite(c)),0.0); }
float3 sdr_texel(texture2d<float,access::sample> src, int2 p) {
    p=clamp(p,int2(0),int2(src.get_width(),src.get_height())-1);
    // H=1 is decoded BGRA8. Recover those bytes before interpolation, matching
    // SDR presentation even when the capture size differs from the source.
    return round(clamp(srgb(clean(src.read(uint2(p)).rgb)),0.0,1.0)*255.0)/255.0;
}
float3 capture_rgb(texture2d<float,access::sample> src, float2 uv, float2 transport) {
    constexpr sampler s(coord::normalized,filter::linear,address::clamp_to_edge);
    if (transport.x && transport.y<=1.0) {
        float2 xy=uv*float2(src.get_width(),src.get_height())-0.5;
        int2 p=int2(floor(xy)); float2 f=fract(xy);
        return mix(mix(sdr_texel(src,p),sdr_texel(src,p+int2(1,0)),f.x),
                   mix(sdr_texel(src,p+int2(0,1)),sdr_texel(src,p+int2(1,1)),f.x),f.y);
    }
    float3 c=clean(src.sample(s,uv).rgb);
    if (transport.x) {
        // Same hue-preserving SDR capture shoulder as hdr_fs: linear knee .75,
        // asymptote 1, unit slope at the knee. Bound transport to this frame's
        // headroom before the shoulder. H=1 bypasses tone mapping above.
        float peak=max(c.r,max(c.g,c.b));
        if (peak>0.75) c *= (0.75+0.25*(1.0-exp(-(min(peak,transport.y)-0.75)/0.25)))/peak;
        c=srgb(c);
    }
    return clamp(c,0.0,1.0);
}
kernel void nv12(texture2d<float,access::sample> src [[texture(0)]],
                 texture2d<float,access::write> y [[texture(1)]],
                 texture2d<float,access::write> uv [[texture(2)]],
                 constant float2 &transport [[buffer(0)]],
                 uint2 p [[thread_position_in_grid]]) {
    if (p.x>=uv.get_width() || p.y>=uv.get_height()) return;
    float3 avg=0;
    for(uint j=0;j<2;j++) for(uint i=0;i<2;i++) {
        uint2 q=p*2+uint2(i,j);
        float3 c=capture_rgb(src,(float2(q)+0.5)/float2(y.get_width(),y.get_height()),transport);
        float l=dot(c,float3(0.2126,0.7152,0.0722));
        y.write(float4((16.0+219.0*l)/255.0),q); avg+=c*0.25;
    }
    float l=dot(avg,float3(0.2126,0.7152,0.0722));
    uv.write(float4((128.0+224.0*(avg.b-l)/1.8556)/255.0,
                    (128.0+224.0*(avg.r-l)/1.5748)/255.0,0,1),p);
}
)metal";'''
assert hashlib.sha256(ORIGINAL_CONVERSION.encode()).hexdigest() == \
    '6ee8d12cf30f80f60b90a2242c81ea0b1fe9ec8137b4d98eb933c1ab9d9f4b45'


COMMON_OBJECTS={}


def build(directory,name,live_source=None,clip_source=None):
    flags=['-std=c++17','-O1','-Wall','-Wextra','-Werror','-Wno-unused-function',
           '-Wno-unused-parameter','-Wno-deprecated-declarations','-fobjc-arc','-I'+str(RT),
           '-I'+str(ROOT/'third_party/ffmpeg/include')]
    if directory not in COMMON_OBJECTS:
        # Each fault recompiles the included encoder/capture implementation.
        # Unchanged dependencies use the same objects for every executable.
        objects=[]
        sources=[RT/'live_seg.mm',RT/'livestream.mm',RT/'clip_packets.mm',RT/'replay.mm',RT/'replay_export.mm',
                 ROOT/'host/audio/dsound_host.cpp',ROOT/'media/media.cpp']
        for index,source in enumerate(sources):
            obj=directory/('common-'+str(index)+'.o')
            built=subprocess.run(['clang++',*flags,'-c',str(source),'-o',str(obj)],capture_output=True,text=True)
            (CACHE/(source.stem+'-common-build.log')).write_text(built.stderr)
            assert built.returncode==0,built.stderr[-4000:]
            objects.append(str(obj))
        COMMON_OBJECTS[directory]=objects
    command=['clang++',*flags,str(RT/'live_encode_test.mm'),*COMMON_OBJECTS[directory]]
    for key,source in [('LIVE_ENCODE_SOURCE',live_source),('CLIPREC_SOURCE',clip_source)]:
        if source: command+=['-D'+key+'="'+str(source)+'"']
    for lib in ('avformat','avcodec','swscale','swresample','avutil'):
        command+=[str(ROOT/'third_party/ffmpeg/lib'/('lib'+lib+'.a'))]
    command+=['-lz']
    for framework in ('AudioToolbox','CoreAudio','CoreFoundation','Cocoa','Metal','QuartzCore','AVFoundation','VideoToolbox','CoreVideo','CoreMedia'):
        command+=['-framework',framework]
    executable=directory/name
    built=subprocess.run(command+['-o',str(executable)],capture_output=True,text=True)
    (CACHE/(name+'-build.log')).write_text(built.stderr)
    assert built.returncode==0,built.stderr[-4000:]
    return executable


def run(executable,mode,path,failure=None):
    result=subprocess.run([str(executable),mode,str(path)],capture_output=True,text=True,timeout=90)
    output=result.stdout+result.stderr
    (CACHE/(executable.name+'-'+mode+'.log')).write_text(output)
    print(executable.name+'/'+mode+': '+output[-2500:].strip(),flush=True)
    assert result.returncode==(1 if failure else 0),(mode,result.returncode,output[-4000:])
    if failure: assert 'FAIL live: '+failure in output,output[-4000:]


class Bits:
    def __init__(self,nal):
        data=bytearray()
        zeros=0
        for value in nal[1:]:
            if zeros>=2 and value==3:
                zeros=0
                continue
            data.append(value)
            zeros=zeros+1 if value==0 else 0
        self.data=data
        self.pos=0

    def take(self,count=1):
        assert self.pos+count<=len(self.data)*8,'truncated RBSP'
        result=0
        for _ in range(count):
            result=result*2+((self.data[self.pos//8]>>(7-self.pos%8))&1)
            self.pos+=1
        return result

    def ue(self):
        zeros=0
        while not self.take():
            zeros+=1
            assert zeros<=31,'invalid Exp-Golomb'
        return (1<<zeros)-1+self.take(zeros)

    def se(self):
        value=self.ue()
        return (value+1)//2 if value&1 else -value//2


def headers(path,dimensions=(320,180),pictures=361,keyframes=4):
    # Decode syntax independently of CoreMedia's format/attachment declarations.
    nals=[nal for nal in path.read_bytes().split(bytes((0,0,0,1))) if nal]
    sps=next(nal for nal in nals if nal[0]&31==7)
    pps=next(nal for nal in nals if nal[0]&31==8)
    r=Bits(sps)
    profile=r.take(8)
    r.take(8)
    level=r.take(8)
    sps_id=r.ue()
    chroma=1
    if profile in (100,110,122,244,44,83,86,118,128,138,139,134,135):
        chroma=r.ue()
        if chroma==3: r.take()
        r.ue(); r.ue(); r.take()
        if r.take():
            for index in range(8 if chroma!=3 else 12):
                if r.take():
                    last=next_scale=8
                    for _ in range(16 if index<6 else 64):
                        if next_scale: next_scale=(last+r.se()+256)%256
                        last=next_scale or last
    r.ue()
    order=r.ue()
    if order==0: r.ue()
    elif order==1:
        r.take(); r.se(); r.se()
        for _ in range(r.ue()): r.se()
    refs=r.ue()
    r.take()
    width_mbs=r.ue()+1
    height_units=r.ue()+1
    frame_only=r.take()
    if not frame_only: r.take()
    r.take()
    crop=[r.ue() for _ in range(4)] if r.take() else [0]*4
    width=width_mbs*16-2*(crop[0]+crop[1])
    height=height_units*16*(2-frame_only)-2*(2-frame_only)*(crop[2]+crop[3])
    reorder=None
    if r.take():
        if r.take():
            if r.take(8)==255: r.take(32)
        if r.take(): r.take()
        if r.take():
            r.take(3); r.take()
            if r.take(): r.take(24)
        if r.take(): r.ue(); r.ue()
        if r.take(): r.take(32); r.take(32); r.take()
        def hrd():
            count=r.ue()+1
            r.take(4); r.take(4)
            for _ in range(count): r.ue(); r.ue(); r.take()
            r.take(5); r.take(5); r.take(5); r.take(5)
        nal_hrd=r.take()
        if nal_hrd: hrd()
        vcl_hrd=r.take()
        if vcl_hrd: hrd()
        if nal_hrd or vcl_hrd: r.take()
        r.take()
        if r.take():
            r.take(); r.ue(); r.ue(); r.ue(); r.ue()
            reorder=r.ue()
            r.ue()
    r=Bits(pps)
    pps_id=r.ue()
    referenced_sps=r.ue()
    cabac=r.take()
    slices=0
    bframes=0
    idrs=0
    for nal in nals:
        if nal[0]&31 in (1,5):
            r=Bits(nal)
            r.ue()
            slice_type=r.ue()%5
            assert r.ue()==pps_id,'slice references a different PPS'
            slices+=1
            bframes+=slice_type==1
            idrs+=(nal[0]&31)==5
    print(f'decoded SPS profile={profile} level={level} size={width}x{height} refs={refs} reorder={reorder}; '
          f'PPS id={pps_id} sps={referenced_sps} CABAC={cabac}; slices={slices} B={bframes} IDRs={idrs}',flush=True)
    assert profile==100,'decoded SPS must use H.264 High'
    assert chroma==1 and (width,height)==dimensions,'decoded SPS dimensions/chroma'
    assert reorder in (None,0) and bframes==0,'decoded stream permits reordering'
    assert referenced_sps==sps_id and cabac==1,'decoded PPS must match SPS and High CABAC'
    assert slices==pictures and idrs==keyframes,'decoded slice and IDR counts'


def mutate(directory,name,source,changes):
    for before,after in changes:
        assert source.count(before)==1,(name,before,source.count(before))
        source=source.replace(before,after)
    path=directory/(name+'.mm')
    path.write_text(source)
    return path


def off_assembly(directory,source):
    def compile_body(name,code):
        path=directory/(name+'.mm')
        path.write_text(code)
        assembly=directory/(name+'.s')
        result=subprocess.run(['clang++','-std=c++17','-O1','-fobjc-arc','-Wno-deprecated-declarations',
            '-I'+str(RT),'-S',str(path),'-o',str(assembly)],capture_output=True,text=True)
        assert result.returncode==0,result.stderr[-2000:]
        return assembly.read_text().split('_cliprec_present:',1)[1].split('.cfi_endproc',1)[0]
    def valid(body):
        return not re.search(r'^\s*bl\s|\bsp\b',body,re.M) and re.search(r'^\s*ret\s*$',body,re.M)
    def instructions(body):
        body=re.sub(r'LBB\d+_\d+|Lloh\d+','LABEL',body)
        return [line.strip() for line in body.splitlines() if re.match(r'^\s+[a-z]+\s',line)]
    body=compile_body('off-entry',source)
    hook='    [self sendLive:f->pixel time:f->time held:false];'
    reference=compile_body('off-entry-reference',source.replace(hook,''))
    assert valid(body) and instructions(body)==instructions(reference),'inactive presenter changed'
    before='if (!active.load(std::memory_order_acquire)) return;'
    assert source.count(before)==1
    broken=compile_body('off-entry-broken',source.replace(before,
        'if (!active.load(std::memory_order_acquire)) { portmenu_status("Inactive capture"); return; }'))
    assert not valid(broken),'inactive presenter call escaped check'
    print('Off entry: identical assembly, 0 calls / 0 stack frame; broken 1 call rejected',flush=True)


def main():
    if sys.platform!='darwin' or not (ROOT/'third_party/ffmpeg/lib/libavcodec.a').is_file():
        print('SKIP: requires macOS media frameworks and local FFmpeg libraries')
        return 3
    CACHE.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        directory=Path(scratch)
        executable=build(directory,'fixed')
        for mode in ('headers','sizes','negotiation','dimensions','lifetime','overload','routing','default','off','capture','capture-failed','file-failure'):
            run(executable,mode,directory/mode)
        headers(directory/'headers')
        headers(directory/'sizes-1080.h264',(1920,1080),1,1)
        headers(directory/'sizes-2160.h264',(3840,2160),1,1)
        assert (directory/'default').read_bytes()==(directory/'off').read_bytes(),'default/Off NV12 input differs'
        live_source=(RT/'live_encode.mm').read_text()
        clip_source=(RT/'cliprec.mm').read_text()
        # Reproduce the file-failure regression: a healthy live segment feed
        # must preserve capture even with replay disabled.
        broken_source=mutate(directory,'replay-only-health',clip_source,[
            ('!outputs.healthy() && !(liveSegments && !liveSegments->video->failed())','(!replaySink || replaySink->failed())')])
        broken=build(directory,'replay-only-health',clip_source=broken_source)
        run(broken,'file-failure',directory/'replay-only-health-output','file failure leaves healthy live capture active')
        off_assembly(directory,clip_source)
        hook='    [self sendLive:f->pixel time:f->time held:false];'
        # Reference encoder submission has no live hook. Same presented inputs,
        # same NV12 bytes; compressed file/source bytes are compared in each run.
        reference=mutate(directory,'reference',clip_source,[(hook,'')])
        reference_text=reference.read_text()
        start=reference_text.index('static NSString *const conversion = @R"metal(')
        end=reference_text.index(')metal";',start)+len(')metal";')
        reference.write_text(reference_text[:start]+ORIGINAL_CONVERSION+reference_text[end:])
        reference_exe=build(directory,'reference',clip_source=reference)
        run(reference_exe,'default',directory/'reference')
        assert (directory/'reference').read_bytes()==(directory/'off').read_bytes(),'Off differs from original encoder input'
        print(f'Off byte identity: {(directory/"off").stat().st_size} NV12 bytes; 0 live sessions/submissions/references',flush=True)
        mutants=[
            ('average-rate','headers',[( 'set(kVTCompressionPropertyKey_ConstantBitRate,@(profile.bitrate));',
                'set(kVTCompressionPropertyKey_AverageBitRate,@(profile.bitrate));')],'explicit CBR 472222 negotiated'),
            ('reorder','headers',[('set(kVTCompressionPropertyKey_AllowFrameReordering,@NO);',
                'set(kVTCompressionPropertyKey_AllowFrameReordering,@YES);')],'frame reordering disabled'),
            ('long-gop','headers',[('set(kVTCompressionPropertyKey_MaxKeyFrameInterval,@120);','set(kVTCompressionPropertyKey_MaxKeyFrameInterval,@240);'),
                ('set(kVTCompressionPropertyKey_MaxKeyFrameIntervalDuration,@2);','set(kVTCompressionPropertyKey_MaxKeyFrameIntervalDuration,@4);'),
                ('nextIDR=CMTimeAdd(pts,CMTimeMake(2,1));','nextIDR=CMTimeAdd(pts,CMTimeMake(4,1));')],'IDR exactly every two seconds'),
            ('ignored-negotiation','negotiation',[('if(result) fail(result);','(void)result;')],'property rejection stops live setup'),
            ('ignored-hardware','negotiation',[('if(rc || !hw || !CFEqual(hw,kCFBooleanTrue)) fail(rc);','(void)rc;')],'hardware query rejection stops live setup'),
            ('mismatched-surface','dimensions',[('if(!accepts(pixel) ||','if(')],'mismatched surface rejected before submission'),
            ('borrowed-surface','lifetime',[('    CVPixelBufferRetain(pixel);',''),
                ('if(source) CVPixelBufferRelease((CVPixelBufferRef)source);','(void)source;')],'completed recording cannot recycle live surfaces'),
            ('unbounded-live','overload',[('pending_.fetch_add(1)>=maxPending','pending_.fetch_add(1)>=64')],'live overload leaves four recording surfaces available'),
            ('hevc-route','routing',[('CMFormatDescriptionGetMediaSubType(format)!=kCMVideoCodecType_H264 ||','false ||')],'HEVC routing rejected'),
            ('default-on','default',[('return value && !strcmp(value,"on");','return !value || !strcmp(value,"on");')],'live session initially Off'),
        ]
        for name,mode,changes,failure in mutants:
            mutant=mutate(directory,name,live_source,changes)
            broken=build(directory,name,live_source=mutant)
            run(broken,mode,directory/(name+'-output'),failure)
        # Alter the actual profile and require the independent SPS decoder to reject it.
        mutant=mutate(directory,'main-profile',live_source,[('kVTProfileLevel_H264_High_AutoLevel','kVTProfileLevel_H264_Main_AutoLevel')])
        broken=build(directory,'main-profile',live_source=mutant)
        run(broken,'headers',directory/'main-profile-output')
        try:
            headers(directory/'main-profile-output')
        except AssertionError as error:
            assert str(error)=='decoded SPS must use H.264 High',error
            print('main-profile: decoded SPS 77 rejected against required 100',flush=True)
        else: raise AssertionError('decoded Main SPS escaped High-profile check')
        mutant=mutate(directory,'off-color',clip_source,[('y.write(float4((16.0+219.0*l)/255.0),q);',
            'y.write(float4((18.0+219.0*l)/255.0),q);')])
        broken=build(directory,'off-color',clip_source=mutant)
        run(broken,'off',directory/'off-color-output')
        expected=(directory/'off').read_bytes()
        changed=(directory/'off-color-output').read_bytes()
        different=sum(a!=b for a,b in zip(expected,changed))
        assert len(expected)==len(changed) and different==320*180*36,'altered Off NV12 escaped byte identity'
        print(f'Off color: {different}/{len(expected)} changed bytes rejected (fixed 0)',flush=True)
        mutant=mutate(directory,'no-live-hook',clip_source,[(hook,'')])
        broken=build(directory,'no-live-hook',clip_source=mutant)
        run(broken,'capture',directory/'no-live-hook-output','live failure isolated from recording and replay')
        mutant=mutate(directory,'cross-route',clip_source,[('    outputs.publish(std::make_shared<clip::Packet>(clip::Track::video,sample));',
            '    if(liveEncoder) liveEncoder->packet(sample);\n    outputs.publish(std::make_shared<clip::Packet>(clip::Track::video,sample));')])
        broken=build(directory,'cross-route',clip_source=mutant)
        run(broken,'capture',directory/'cross-route-output','live failure isolated from recording and replay')
    print('live_encode_test PASS: decoded headers/361 frames; 16 broken controls rejected')


if __name__=='__main__': raise SystemExit(main())
