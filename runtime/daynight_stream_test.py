#!/usr/bin/env python3
"""Production side-stream binding and shader proof (daynight_stream.m/.h).

Reversed GPU colour order: 6 differing RGB bytes versus 0, rejected.
Missing position digest check: 1 wrong buffer bound versus 0, rejected.
Compile all classified world VS variants; disabled shader source stays exact.
"""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
HARNESS=r'''
#import <Foundation/Foundation.h>
#include "host.h"
#include "daynight_stream.h"
#include STREAM_SOURCE
uint8_t *G;
int diag_on;
Device dev;
const char *diag_dir;
void diag_event(const char *f,...) {}
int diag_frame_wanted(uint64_t f,const char *s){return diag_on;}
float daynight_sky_factor(float p,float z,float t){return p==.5f ? 0:1;}
@interface Capture : NSObject
@property id<MTLBuffer> buffer;
@property NSUInteger offset;
@property float night;
@end
@implementation Capture
-(void)setVertexBuffer:(id<MTLBuffer>)b offset:(NSUInteger)o atIndex:(NSUInteger)i {if(i==28){self.buffer=b;self.offset=o;}}
-(void)setVertexBytes:(const void *)b length:(NSUInteger)n atIndex:(NSUInteger)i {if(i==27)memcpy(&_night,b,4);}
@end
static char *profile_source(const uint32_t *t,uint32_t n,const SmResult *r,int *id,int lamp) {
 assert(lamp==0); *id=73;
 NSMutableString *s=[NSMutableString stringWithUTF8String:r->msl];
 [s appendString:@"\n// prior material source preserved\n"];
 return strdup(s.UTF8String);
}
int main(int argc,char **argv) { @autoreleasepool {
 id<MTLDevice> device=MTLCreateSystemDefaultDevice(); assert(device);
 setenv("NFS_DAYNIGHT","on",1);setenv("NFS_DAYNIGHT_BAKE",argv[1],1);
 if(argc>5 && strcmp(argv[5],"diag")) {
  if(!strcmp(argv[5],"off"))setenv("NFS_DAYNIGHT","off",1);else unsetenv("NFS_DAYNIGHT_BAKE");
  SmResult refl={0};refl.shader_type=SM_VERTEX;refl.msl="unchanged";
  assert(!daynight_stream_shader(NULL,0,&refl,NULL,NULL));assert(!bindings && !sections && !zero);
  puts("disabled source and buffers exact");return 0;
 }
 G=calloc(1,0x1000000); M32(0x00925E90)=6;M32(0x009B392C)=0x1000;
 MF32(0x1008)=.05; MF32(0x1188)=1; MF32(0x1010)=.5;
 if(argc>5 && !strcmp(argv[5],"diag")) {
  diag_on=1;diag_dir=argv[1];M32(0x00982A20)=0x3000;M32(0x3004)=1;M32(0x00919650)=0x4000;
  dev.viewport[2]=1144;dev.viewport[3]=720;
  for(int matrix=0;matrix<3;++matrix)for(int i=0;i<16;++i)MF32(0x4000+matrix*64+i*4)=i%5==0;
 }
 float positions[9]={1,2,3,4,5,6,7,8,9};memcpy(G+0x2000,positions,sizeof positions);
 Obj vb={0};vb.size=sizeof positions;vb.mem[0]=0x2000;vb.buffer_generation=1;
 id<MTLBuffer> identity=[device newBufferWithLength:36 options:0]; Capture *capture=[Capture new];
 uint64_t hash=0;
 NSData *tokens=[NSData dataWithContentsOfFile:[NSString stringWithUTF8String:argv[2]]];
 hash=tokens_hash(tokens.bytes,(uint32_t)tokens.length/4);
 daynight_stream_bind((id)capture,device,&vb,identity,12,0,hash,1);
 uint8_t expected[12]={10,40,90,255,70,20,120,255,200,180,30,255};
 int truncated=argc>4 && argc<=5;
 assert(capture.night==1);
 if(truncated) {uint8_t zeros[12]={0};assert(!memcmp(capture.buffer.contents,zeros,12));}
 else {
  assert(!memcmp((uint8_t *)capture.buffer.contents+capture.offset,expected,12));
  assert(bytes_held==12);
  MF32(0x1008)=.5;
  daynight_stream_bind((id)capture,device,&vb,identity,12,0,hash,2);assert(capture.night==0);
  MF32(0x2000)=99;++vb.buffer_generation;
  daynight_stream_bind((id)capture,device,&vb,identity,12,0,hash,3);
  uint8_t zeros[12]={0};assert(!memcmp((uint8_t *)capture.buffer.contents+capture.offset,zeros,12));
 }

 if(!truncated) {
  unsigned compiled=0;
  NSString *root=[NSString stringWithUTF8String:argv[3]];
  for(NSString *effect in @[@"world",@"worldreflect",@"worldnormalmap",@"glossywindow",@"tree"]) {
   NSString *path=[root stringByAppendingPathComponent:effect];
   for(NSString *file in [[NSFileManager defaultManager] contentsOfDirectoryAtPath:path error:NULL]) {
    if(![file hasSuffix:@".bin"] || ![file containsString:@"_vs_"])continue;
    NSData *tok=[NSData dataWithContentsOfFile:[path stringByAppendingPathComponent:file]];
    SmOptions opt;sm_default_options(&opt);SmResult refl;
    assert(sm_translate(tok.bytes,tok.length/4,&opt,&refl)==0);
    int material_id=-1;
    char *patch=daynight_stream_shader(tok.bytes,(uint32_t)tok.length/4,&refl,&material_id,profile_source);
    assert(material_id==73);
    assert(patch && strstr(patch,"prior material source preserved"));
    if(!patch) {fprintf(stderr,"missing shader %s/%s\n",effect.UTF8String,file.UTF8String);abort();}
    NSError *error=nil;
    id<MTLLibrary> lib=[device newLibraryWithSource:[NSString stringWithUTF8String:patch] options:nil error:&error];
    if(!lib){fprintf(stderr,"shader %s/%s: %s\n",effect.UTF8String,file.UTF8String,error.description.UTF8String);abort();}
    free(patch);sm_result_free(&refl);++compiled;
   }
  }
  assert(compiled>=12);printf("shaders compiled %u\n",compiled);
 }
 puts("binding PASS");return 0;
}}
'''


def run(args,**kw):
    p=subprocess.run(args,capture_output=True,text=True,**kw)
    if p.returncode:raise AssertionError(p.stdout[-1500:]+p.stderr[-2500:])
    return p


def fixture(folder):
    folder.mkdir()
    positions=struct.pack('<9f',*range(1,10)); colours=bytes((10,40,90,255,70,20,120,255,200,180,30,255))
    digest=hashlib.sha256(positions).hexdigest()
    data=struct.pack('<4sIII',b'DNBK',1,42,1)+struct.pack('<II32s',0,3,bytes.fromhex(digest))+colours
    (folder/'section-42.dnbake').write_bytes(data)
    row=dict(digest=digest,count=3,offset=56,gpu_offset=0,colour_sha256=hashlib.sha256(colours).hexdigest())
    index=dict(version=1,sections={'42':dict(file='section-42.dnbake',sha256=hashlib.sha256(data).hexdigest(),meshes=1,vertices=3,rows=[row])},buffers={digest:dict(section=42,**row)})
    (folder/'index.json').write_text(json.dumps(index))


def main():
    source=(ROOT/'runtime/daynight_stream.m').read_text()
    with tempfile.TemporaryDirectory(prefix='dnstream-') as td:
        td=Path(td); folder=td/'bake';fixture(folder)
        harness=td/'harness.m';harness.write_text(HARNESS)
        token=ROOT/'host/shaders/testdata/world/t0_world_p0_vs_1_1.bin'
        variants={'good':source,'reverse':source.replace('pos+=(size_t)count*4;', 'for(uint32_t i=0;i<count/2;++i) { uint32_t *p=(uint32_t *)colours.mutableBytes+gp/4; uint32_t t=p[i];p[i]=p[count-1-i];p[count-1-i]=t; }\n            pos+=(size_t)count*4;').replace('|| memcmp(b+off,(uint8_t *)colours.bytes+go,n*4)',''),
                  'digest':source.replace('index_data[@"buffers"][hex_digest(digest)]','[index_data[@"buffers"] allValues].firstObject')}
        objects=[]
        for unit in ('sm_parse.cpp','sm_msl.cpp'):
            obj=td/(unit+'.o')
            run(['clang++','-std=c++17','-O0','-c',str(ROOT/'host/shaders'/unit),'-o',str(obj)])
            objects.append(str(obj))
        for name,src in variants.items():
            production=td/f'{name}.m';production.write_text(src)
            exe=td/name
            run(['clang','-w','-fobjc-arc','-I',str(ROOT/'runtime'),'-I',str(ROOT/'host/shaders'),'-I',str(ROOT/'recomp'),f'-DSTREAM_SOURCE="{production}"',str(harness),*objects,'-lc++','-framework','Metal','-framework','Foundation','-o',str(exe)])
            p=subprocess.run([str(exe),str(folder),str(token),str(ROOT/'host/shaders/testdata')],capture_output=True,text=True)
            assert (p.returncode==0)==(name=='good'),(name,p.stderr[-1500:])
        assert not list(folder.glob('dnstream_*.jsonl'))  # diag off performs no shot I/O
        run([str(td/'good'),str(folder),str(token),str(ROOT/'host/shaders/testdata'),'capture','diag'])
        capture=json.loads((folder/'dnstream_00001.jsonl').read_text())
        assert capture['status']=='bound' and capture['section']==42
        assert capture['viewport']==[0,0,1144,720] and capture['count']==3
        assert capture['view']==capture['projection']==capture['view_projection']==[int(i%5==0) for i in range(16)]
        assert json.loads((folder/'dnstream_00003.jsonl').read_text())['status']=='key-missing'
        for mode in ('off','unset'):
            run([str(td/'good'),str(folder),str(token),str(ROOT/'host/shaders/testdata'),'disabled',mode])
        wrong=td/'wrong-count';fixture(wrong)
        index=json.loads((wrong/'index.json').read_text())
        next(iter(index['buffers'].values()))['count']=4
        (wrong/'index.json').write_text(json.dumps(index))
        run([str(td/'good'),str(wrong),str(token),str(ROOT/'host/shaders/testdata'),'fallback'])
        (folder/'section-42.dnbake').write_bytes(b'DNBK')
        run([str(td/'good'),str(folder),str(token),str(ROOT/'host/shaders/testdata'),'truncated'])
    print('PASS: production binding/order/generation/noon/truncated fallback; reverse and missing-digest controls rejected (2/2)')
    return 0


if __name__=='__main__':raise SystemExit(main())
