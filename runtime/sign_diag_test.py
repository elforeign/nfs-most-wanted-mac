#!/usr/bin/env python3
"""Matched sign diagnostic: production pose and MSL instrumentation, CPU/Metal.

Positive control: the exact 64-pose endpoints, stationary/retreat transforms,
real dependent diffuse UVs, direct colour, footprint shader compilation.
Review regression controls: ca4a7595 camera call FAIL (3 args vs required 5),
fixed PASS (5 args, rebuild=1). Its immediate UV replay FAIL (64 visible vs
32 expected after a late half occluder); deferred replay PASS (32/0/64/64
pixels for half/full/behind/absent occluders, both 1x and 2x MSAA).
Production /64 mutation: original test exit 0 (missed); this test exit 1
(+0.2421875 vs required +0.25 degrees). Unmutated 256 poses PASS.
A second diffuse sample is rejected.
--analyze DIR validates an armed capture and writes registered plane metrics.
No game is launched. Default also requires Metal; --cpu checks transforms only.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zlib

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]

VISIBILITY = r'''
static void visibility_control(void) {
    sign_diag_on=armed=1;finished=rejected=0;
    NSError *err=nil;
    id<MTLLibrary> lib=[mtl newLibraryWithSource:@"#include <metal_stdlib>\nusing namespace metal;\nvertex float4 v(uint i[[vertex_id]],constant float &z[[buffer(0)]]){float2 p=float2((i<<1)&2,i&2);return float4(p*2-1,z,1);}\nfragment float4 f(){return float4(.25,.5,2,64);}" options:nil error:&err];
    assert(lib);
    for(unsigned samples=1;samples<=2;++samples)for(unsigned mode=0;mode<4;++mode){
        ++frame_serial;scene_read=nil;uv_serial=0;
        MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];
        pd.vertexFunction=[lib newFunctionWithName:@"v"];pd.fragmentFunction=[lib newFunctionWithName:@"f"];
        pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA32Float;pd.depthAttachmentPixelFormat=MTLPixelFormatDepth32Float;pd.rasterSampleCount=samples;
        id<MTLRenderPipelineState> pso=[mtl newRenderPipelineStateWithDescriptor:pd error:&err];assert(pso);
        MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:8 height:8 mipmapped:NO];
        td.storageMode=MTLStorageModePrivate;td.usage=MTLTextureUsageRenderTarget;
        uv_target=[mtl newTextureWithDescriptor:td];
        if(samples>1){td.textureType=MTLTextureType2DMultisample;td.sampleCount=samples;uv_ms=[mtl newTextureWithDescriptor:td];}else uv_ms=nil;
        id<MTLTexture> color=[mtl newTextureWithDescriptor:td];
        td.pixelFormat=MTLPixelFormatDepth32Float;id<MTLTexture> depth=[mtl newTextureWithDescriptor:td];
        MTLDepthStencilDescriptor *dd=[MTLDepthStencilDescriptor new];dd.depthCompareFunction=MTLCompareFunctionLessEqual;dd.depthWriteEnabled=YES;
        id<MTLDepthStencilState> write=[mtl newDepthStencilStateWithDescriptor:dd];dd.depthWriteEnabled=NO;
        id<MTLDepthStencilState> readonly=[mtl newDepthStencilStateWithDescriptor:dd];
        cmd=rt_command_buffer();
        MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor new];rp.colorAttachments[0].texture=color;
        rp.colorAttachments[0].loadAction=MTLLoadActionClear;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
        rp.depthAttachment.texture=depth;rp.depthAttachment.loadAction=MTLLoadActionClear;rp.depthAttachment.storeAction=MTLStoreActionStore;rp.depthAttachment.clearDepth=1;
        id<MTLRenderCommandEncoder> real=[cmd renderCommandEncoderWithDescriptor:rp];
        SignDiagTee *tee=[SignDiagTee alloc];tee->target=real;tee->list=rt_render_list_new();tee->pipeline=pso;tee->depth_state=readonly;tee->depth=depth;tee->plane=1;
        id<MTLRenderCommandEncoder> e=(id<MTLRenderCommandEncoder>)tee;
        [e setRenderPipelineState:pso];[e setDepthStencilState:write];float z=.6f;
        [e setVertexBytes:&z length:4 atIndex:0];[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[real endEncoding];
        sign_diag_replay(e);
        // Later foreground geometry: half covered, fully covered, behind, none.
        if(mode!=3){
            rp.colorAttachments[0].loadAction=MTLLoadActionLoad;rp.depthAttachment.loadAction=MTLLoadActionLoad;
            e=[cmd renderCommandEncoderWithDescriptor:rp];[e setRenderPipelineState:pso];[e setDepthStencilState:write];
            z=mode==2?.8f:.2f;[e setVertexBytes:&z length:4 atIndex:0];
            if(mode==0)[e setScissorRect:(MTLScissorRect){0,0,4,8}];
            [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
        }
        // Production scene capture normally accepts BGRA8/RGBA8/RGBA16F.
        // Supply a separate resolved scene of that format to exercise its hook.
        MTLTextureDescriptor *sd=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:8 height:8 mipmapped:NO];
        id<MTLTexture> scene=[mtl newTextureWithDescriptor:sd];sign_diag_scene(cmd,scene);
        id<MTLBuffer> result=copy_texture(cmd,uv_target,16);capture_begin(cmd);[cmd commit];assert(capture_wait());
        unsigned visible=0;const float *pixels=result.contents;
        for(unsigned i=0;i<64;++i)visible+=isfinite(pixels[4*i+3]);
        unsigned expected=mode==0?32:mode==1?0:64;
        printf("visibility samples=%u mode=%u visible=%u expected=%u\n",samples,mode,visible,expected);fflush(stdout);
        assert(visible==expected);
    }
    puts("PASS: scene-end evidence against final depth, late half/full occluders and behind/no-occluder controls, 1x/2x");
}
'''

NATIVE = r'''
int main(int argc,char **argv) { @autoreleasepool {
    int cpu=argc>1;
    const char *check=getenv("SIGN_REVIEW_CHECK");
    if(!check || !strcmp(check,"camera")) {
        Cpu c={.ecx=17},saved=c;matrices=0x123400;
        camera_setup(&c,saved);
        assert(c.ecx==saved.ecx && setup_calls==1);
        puts("PASS: production camera setup supplies five arguments, rebuild=1, restores CPU");
    }
    if(check && !strcmp(check,"camera"))return 0;
    float m[16]={1,0,0,0,0,1,0,0,0,0,1,0,5,7,9,1},out[16],reference[64][16];
    for(unsigned variant=0;variant<4;++variant)for(unsigned i=0;i<64;++i){
        double angle=production_yaw(1,i);pose(out,m,angle,0);
        assert(fabs(out[0]-cos(angle))<1e-6 && fabs(out[2]-sin(angle))<1e-6);
        if(!variant)memcpy(reference[i],out,sizeof out);else assert(!memcmp(reference[i],out,sizeof out));
    }
    assert(fabs(production_yaw(1,0)+.25*M_PI/180.)<1e-12);
    assert(fabs(production_yaw(1,63)-.25*M_PI/180.)<1e-12);
    assert(production_yaw(0,63)==0 && production_yaw(2,63)==0);
    puts("PASS: production yaw endpoints -0.25/+0.25 degrees, 256 matched poses");
    if(check && !strcmp(check,"yaw"))return 0;
    pose(out,m,0,0);assert(!memcmp(out,m,sizeof out));
    pose(out,m,0,10);assert(out[14]==19);
    pose(out,m,.25*M_PI/180.,0);float correct=out[2];
    pose(out,m,(-.25+63*.5/64.)*M_PI/180.,0);assert(fabs(out[2]-correct)>1e-5);
    Obj old={0,123,8,8,4},fresh={1,123,8,8,4};selected_object=&fresh;ntextures=2;
    textures[0]=(SignTexture){.guest=123,.w=8,.h=8,.levels=4,.object=&old};
    textures[1]=(SignTexture){.guest=123,.w=8,.h=8,.levels=4,.object=&fresh};
    assert(texture(123)==&textures[1]);fresh.refs=0;assert(!texture(123));selected_object=NULL;ntextures=0;
    MTLRenderPipelineDescriptor *shared=[MTLRenderPipelineDescriptor new];
    sign_diag_pipeline(123,shared,"shared pipeline source");shared.rasterSampleCount=4;
    assert(pipeline_desc[@123]&&[pipeline_source[@123] isEqualToString:@"shared pipeline source"]);
    assert(((MTLRenderPipelineDescriptor *)pipeline_desc[@123]).rasterSampleCount==1);
    sign_diag_pipeline(124,shared,NULL);assert(!pipeline_desc[@124]);
    const char *source=
    "#include <metal_stdlib>\nusing namespace metal;\n"
    "struct SmPSAux { float lodbias[16]; };\n"
    "struct SmPSOut { float4 oc[4]; float depth; bool killed; };\n"
    "struct SmPSIn { float2 uv; };\n"
    "static void sm_body(SmPSIn si,thread SmPSOut &so,constant float4 *c,constant SmPSAux &aux,texture2d<float> tex0,sampler smp0) {\n"
    "float2 changed=si.uv*.5f+float2(c[0].x,.125f);\n"
    "so.oc[0]=tex0.sample(smp0, (float4(changed,0,1)).xy, bias(aux.lodbias[0])); so.oc[0].a=.5f; so.killed=false; }\n"
    "struct I {float4 pos[[position]];float2 uv;};struct O {float4 c0[[color(0)]];};\n"
    "vertex I vs(uint i[[vertex_id]]) {float2 p=float2((i<<1)&2,i&2);return {float4(p*2-1,0,1),p};}\n"
    "fragment O sm_main(I i[[stage_in]],constant float4*c[[buffer(0)]],constant SmPSAux &aux [[buffer(3)]],texture2d<float>tex0[[texture(0)]],sampler smp0[[sampler(0)]]) {\n"
    "SmPSIn si;si.uv=i.uv;SmPSOut so;sm_body(si,so,c,aux,tex0,smp0);O r;\n"
    "    r.c0 = so.oc[0];\nreturn r;}\n";
    NSString *uv=instrument(source,0),*direct=instrument(source,1);assert(uv&&direct);
    assert([uv containsString:@"so.sd_uv=((float4(changed,0,1)).xy)"]);
    NSString *broken=[[NSString stringWithUTF8String:source] stringByAppendingString:@"tex0.sample(smp0, float2(0))"];
    assert(!instrument(broken.UTF8String,0));
    assert(!instrument("unsupported source",0));

    // The actual capture scheduler must finish four identical paths, and reject
    // a retreat that never reaches the original ticker terminal mip.
    trace=tmpfile();assert(trace);G=calloc(1,1024);assert(G);camera=0;scratch=1;
    memset(c0,0xAC,sizeof c0);sign_diag_on=armed=1;ntextures=1;
    uint32_t history[3]={0x12345678,0xDEADBEEF,17},initial[3];memcpy(initial,history,sizeof history);
    assert(sign_diag_active()&&sign_diag_hold());
    sign_diag_freeze("test",history,sizeof history);assert(nfrozen==1);
    memset(history,0,sizeof history);sign_diag_freeze("test",history,sizeof history);assert(!memcmp(history,initial,sizeof history));
    finished=1;history[0]=7;sign_diag_freeze("test",history,sizeof history);assert(history[0]==7);
    assert(!sign_diag_active()&&!sign_diag_hold());finished=0;
    sign_diag_freeze("test",history,4);assert(rejected);rejected=0;
    free(frozen[0].bytes);nfrozen=0;
    textures[0].hash=0x6EBF6F28;textures[0].old_levels=3;
    unsigned lengths[4]={0};
    while(!finished){assert(variant<4);++lengths[variant];retreat_lod=2.5f;advance_capture();}
    for(unsigned i=0;i<4;++i)assert(lengths[i]==SD_STILL+SD_YAW+1);
    assert(!rejected&&retreat_count==1&&free_calls==1&&!memcmp(G+camera,c0,sizeof c0));
    variant=phase=step=retreat_count=finished=rejected=0;scratch=1;
    unsigned bad_frames=0;
    while(!finished){retreat_lod=-100;advance_capture();assert(++bad_frames<200);}
    assert(rejected&&bad_frames==SD_STILL+SD_YAW+SD_RETREAT_MAX&&free_calls==2);
    fclose(trace);free(G);
    if(cpu){puts("PASS: 256 matched poses; 12-byte history replay; disabled hold inert; 292 matched capture frames; retreat rejected at 168; endpoint/sample mutants rejected (Metal omitted)");return 0;}
    id<MTLDevice> dev=MTLCreateSystemDefaultDevice();if(!dev){puts("SKIP: Metal unavailable");return 3;}
    NSString *biased=instrument([[NSString stringWithUTF8String:source] stringByReplacingOccurrencesOfString:@"bias(aux.lodbias[0])" withString:@"bias(.75f+aux.lodbias[0])"].UTF8String,0);
    NSString *explicit_lod=instrument([[NSString stringWithUTF8String:source] stringByReplacingOccurrencesOfString:@"bias(aux.lodbias[0])" withString:@"level(2.25f)"].UTF8String,0);
    assert(biased&&explicit_lod);
    NSString *gradient=[[NSString stringWithUTF8String:source] stringByReplacingOccurrencesOfString:@"bias(aux.lodbias[0])" withString:@"gradient2d(float2(1),float2(1))"];
    assert(!instrument(gradient.UTF8String,0));
    mtl=dev;queue=[dev newCommandQueue];
    id<MTLCommandBuffer> recorded=rt_command_buffer();capture_begin(recorded);[recorded commit];assert(capture_wait());
    visibility_control();
    NSError *err=nil;float baseline_lod=0;
    for(int mode=0;mode<4;++mode){
        NSString *src=mode==1?direct:mode==2?biased:mode==3?explicit_lod:uv;id<MTLLibrary>lib=[dev newLibraryWithSource:src options:nil error:&err];
        if(!lib){fprintf(stderr,"%s\n",err.description.UTF8String);return 1;}
        MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];pd.vertexFunction=[lib newFunctionWithName:@"vs"];pd.fragmentFunction=[lib newFunctionWithName:@"sm_main"];
        pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA32Float;
        id<MTLRenderPipelineState>pso=[dev newRenderPipelineStateWithDescriptor:pd error:&err];assert(pso);
        MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:8 height:8 mipmapped:NO];td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageRenderTarget;
        id<MTLTexture>dst=[dev newTextureWithDescriptor:td];td.usage=MTLTextureUsageShaderRead;
        id<MTLTexture>tex=[dev newTextureWithDescriptor:td];float pixels[8*8*4];for(unsigned i=0;i<64;++i){pixels[4*i]=.25f;pixels[4*i+1]=.5f;pixels[4*i+2]=.75f;pixels[4*i+3]=1;}
        [tex replaceRegion:MTLRegionMake2D(0,0,8,8) mipmapLevel:0 withBytes:pixels bytesPerRow:8*16];
        MTLSamplerDescriptor *sd=[MTLSamplerDescriptor new];sd.minFilter=sd.magFilter=MTLSamplerMinMagFilterLinear;sd.mipFilter=MTLSamplerMipFilterLinear;id<MTLSamplerState>smp=[dev newSamplerStateWithDescriptor:sd];
        id<MTLCommandQueue>q=[dev newCommandQueue];id<MTLCommandBuffer>cb=[q commandBuffer];MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor new];rp.colorAttachments[0].texture=dst;rp.colorAttachments[0].loadAction=MTLLoadActionClear;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
        id<MTLRenderCommandEncoder>e=[cb renderCommandEncoderWithDescriptor:rp];[e setRenderPipelineState:pso];float c[4]={.0625f,0,0,0},aux[16]={0},id=1;
        [e setFragmentBytes:c length:sizeof c atIndex:0];[e setFragmentBytes:aux length:sizeof aux atIndex:3];[e setFragmentBytes:&id length:4 atIndex:24];[e setFragmentTexture:tex atIndex:0];[e setFragmentSamplerState:smp atIndex:0];[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];[cb commit];[cb waitUntilCompleted];assert(cb.status==MTLCommandBufferStatusCompleted);
        [dst getBytes:pixels bytesPerRow:8*16 fromRegion:MTLRegionMake2D(0,0,8,8) mipmapLevel:0];
        if(mode==1){for(unsigned i=0;i<64;++i)assert(fabs(pixels[4*i]-.25f)<1e-6&&fabs(pixels[4*i+1]-.5f)<1e-6&&fabs(pixels[4*i+2]-.75f)<1e-6&&fabs(pixels[4*i+3]-.5f)<1e-6);}
        else {assert(fabs(pixels[0]-.09375f)<1e-5);assert(fabs(pixels[1]-.59375f)<1e-5);assert(isfinite(pixels[2])&&fabs(pixels[3]/64.f-1)<.4f);if(!mode)baseline_lod=pixels[2];if(mode==2)assert(fabs(pixels[2]-baseline_lod-.75f)<1e-5);if(mode==3)assert(fabs(pixels[2]-2.25f)<1e-6);}
    }
    // The diagnostic raster uses the same 2x count as the world scene.
    assert([dev supportsTextureSampleCount:2]);MTLTextureDescriptor *ms=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:8 height:8 mipmapped:NO];ms.textureType=MTLTextureType2DMultisample;ms.sampleCount=2;ms.storageMode=MTLStorageModePrivate;ms.usage=MTLTextureUsageRenderTarget;assert([dev newTextureWithDescriptor:ms]);
    puts("PASS: 256 matched poses; 12-byte history replay; 292 matched capture frames; unreachable-tail/endpoint/sample mutants rejected; dependent UV/direct colour/implicit+bias+explicit LOD readback; 2x float target");return 0;
} }
'''


def check_native(cpu):
    source = (ROOT / 'runtime/sign_diag.m').read_text()
    pose = source.split('static void pose(', 1)[1].split('\nvoid sign_diag_world', 1)[0]
    instrument = source.split('static NSString *instrument(', 1)[1].split('\nstatic id<MTLFunction> function_for', 1)[0]
    text = '#import <Foundation/Foundation.h>\n#import <Metal/Metal.h>\n#include <assert.h>\n#include <math.h>\n#include <stdio.h>\n'
    advance = source.split('static void advance_capture(void) {', 1)[1].split('\nvoid sign_diag_finish_present', 1)[0]
    state = r"""
enum { SD_STILL = 8, SD_YAW = 64, SD_RETREAT_MAX = 96 };
static int sign_diag_on,armed,finished,rejected,variant,phase,step,retreat_count;
static float retreat_lod;static unsigned ntextures,free_calls,nplanes;
typedef struct {int refs;uint32_t guest,w,h,levels;} Obj;
typedef struct {uint32_t hash,old_levels,guest,w,h,levels;Obj *object;} SignTexture;
static SignTexture textures[128];static Obj *selected_object;
static Obj *obj(uint32_t guest){return selected_object&&selected_object->guest==guest?selected_object:NULL;}
static uint8_t *G,c0[0x2C0];static uint32_t camera,scratch,matrices;static FILE *trace;
static void reject(const char *reason){(void)reason;rejected=1;}
static void gfree(uint32_t p){assert(p);++free_calls;}
static struct {char name[48];void *bytes;size_t size;} frozen[16];
static unsigned nfrozen;
static NSMutableDictionary *pipeline_desc,*pipeline_source;
id<MTLDevice> mtl;id<MTLCommandQueue> queue;int diag_on;
uint64_t host_ticks(void) { return clock_gettime_nsec_np(CLOCK_UPTIME_RAW); }
void diag_event(const char *format, ...) { (void)format; }
id<MTLCommandBuffer> rt_command_buffer(void);
id rt_render_list_new(void);
void rt_render_list_replay(id list,id<MTLRenderCommandEncoder> encoder);
static id<MTLCommandBuffer> cmd;
static uint64_t uv_serial,frame_serial=1;
static id<MTLTexture> uv_target,uv_ms;
static id<MTLBuffer> scene_read;
static NSUInteger scene_format,frame_w,frame_h;
typedef struct {uint32_t ecx;} Cpu;
static unsigned setup_calls;
static void call_guest(Cpu *c,uint32_t address,unsigned count,const uint32_t *args) {
    assert(address==0x006CF400u && c->ecx==matrices);
    assert(count==5 && args[0]==0x00919650u);
    assert(!args[1] && !args[2] && !args[3] && args[4]==1);
    ++setup_calls;c->ecx=0;
}
"""
    active = source.split('int sign_diag_active(void) {', 1)[1].split('\nint sign_diag_selected', 1)[0]
    freeze = source.split('void sign_diag_freeze(', 1)[1].split('\nvoid sign_diag_record_history', 1)[0]
    digest = source.split('static uint64_t hash_bytes(', 1)[1].split('\nstatic void path_for', 1)[0]
    text += state + 'static uint64_t hash_bytes(' + digest
    text += 'int sign_diag_active(void) {' + active + '\nvoid sign_diag_freeze(' + freeze
    lookup = source.split('static SignTexture *texture(', 1)[1].split('\nvoid sign_diag_texture', 1)[0]
    descriptors = source.split('void sign_diag_pipeline(', 1)[1].split('\nvoid sign_diag_draw', 1)[0]
    text += 'void sign_diag_pipeline(' + descriptors
    text += 'static SignTexture *texture(' + lookup
    text += 'static void advance_capture(void) {' + advance
    completion = source.split('static dispatch_semaphore_t capture_done;', 1)[1].split('\nvoid sign_diag_present', 1)[0]
    text += 'static dispatch_semaphore_t capture_done;' + completion
    # Execute the production schedule and guest-call block, rather than a
    # duplicate /63 formula. Keep this extraction valid on the original too.
    yaw = re.search(r'double yaw=(.*?);', source)[1]
    setup = source.split('    uint32_t setup_args[', 1)[1].split('\n    snprintf(frame_stem', 1)[0]
    text += 'static double production_yaw(int phase,int step) { return ' + yaw + '; }\n'
    text += 'static void camera_setup(Cpu *c,Cpu saved) { uint32_t setup_args[' + setup + '\n}\n'
    tee = source.split('@interface SignDiagTee', 1)[1].split('\nid<MTLRenderCommandEncoder> sign_diag_tee', 1)[0]
    replay = source.split('void sign_diag_replay(', 1)[1].split('// Completion handlers', 1)[0]
    # The queue declaration precedes sign_diag_replay in the fixed source.
    text += 'static NSMutableArray *evidence_draws;\n@interface SignDiagTee' + tee
    text += '\nvoid sign_diag_replay(' + replay
    text += VISIBILITY
    text += 'static void pose(' + pose + '\nstatic NSString *instrument(' + instrument + NATIVE
    with tempfile.TemporaryDirectory(prefix='sign-diag-') as d:
        path = Path(d)
        text = 'static const char *diag_dir=' + json.dumps(str(path)) + ';\n' + text
        (path / 'check.m').write_text(text)
        p = subprocess.run(['clang', '-O1', '-g', '-fobjc-arc', '-fsanitize=address,undefined',
                            str(path / 'check.m'), str(ROOT / 'runtime/render_thread.m'), '-I', str(ROOT / 'runtime'), '-I', str(ROOT / 'recomp'), '-framework', 'Foundation', '-framework', 'Metal', '-o', str(path / 'check')], capture_output=True, text=True)
        assert p.returncode == 0, p.stderr[-4000:]
        p = subprocess.run([str(path / 'check')] + (['cpu'] if cpu else []), capture_output=True, text=True,
                           env={**os.environ, 'NFS_RENDER_THREAD': '1'})
        print(p.stdout.strip(), flush=True)
        assert p.returncode in (0, 3), p.stderr[-3000:]
        if not cpu and p.returncode == 0:
            direct = subprocess.run([str(path / 'check')], capture_output=True, text=True,
                                    env={**os.environ, 'NFS_RENDER_THREAD': '0'})
            assert direct.returncode == 0, direct.stderr[-3000:]
            print('PASS: GPU completion with production render-thread recorder and direct command buffers')
        return p.returncode


def homography(samples, width, height):
    # Solve in normalized image coordinates for stable conditioning.
    q = samples[::max(1, len(samples) // 512)]
    u, v = q['u'].astype(float), q['v'].astype(float)
    x, y = (q['x'] + .5) / width, (q['y'] + .5) / height
    a = np.zeros((len(q) * 2, 8)); b = np.empty(len(q) * 2)
    a[0::2] = np.stack((u, v, u * 0 + 1, u * 0, u * 0, u * 0, -u * x, -v * x), 1)
    a[1::2] = np.stack((u * 0, u * 0, u * 0, u, v, u * 0 + 1, -u * y, -v * y), 1)
    b[0::2], b[1::2] = x, y
    h, _, rank, _ = np.linalg.lstsq(a, b, rcond=None)
    assert rank == 8, 'degenerate sign plane'
    h = np.append(h, 1).reshape(3, 3)
    p = np.einsum('ij,kj->ik', np.stack((u, v, u * 0 + 1), 1), h)
    residual = np.hypot((p[:, 0] / p[:, 2] - x) * width, (p[:, 1] / p[:, 2] - y) * height)
    assert np.quantile(residual, .95) < .35, f'nonplanar/ambiguous UV mapping: p95 {np.quantile(residual, .95):.3f}px'
    return h


SAMPLE = np.dtype([('x', '<u4'), ('y', '<u4'), ('plane', '<u4'), ('u', '<f4'), ('v', '<f4'), ('lod', '<f4'), ('minor', '<f4'), ('scene_lum', '<f4')])


def registered(image, samples, grid, linear_luminance=None):
    height, width = image.shape[:2]
    h = homography(samples, width, height)
    p = np.einsum('ij,kj->ik', np.c_[grid, np.ones(len(grid))], h)
    x, y = p[:, 0] / p[:, 2] * width - .5, p[:, 1] / p[:, 2] * height - .5
    ix, iy = np.floor(x).astype(int), np.floor(y).astype(int)
    valid = (ix >= 0) & (iy >= 0) & (ix + 1 < width) & (iy + 1 < height)
    ix, iy = np.clip(ix, 0, width - 2), np.clip(iy, 0, height - 2)
    addresses = samples['y'].astype(np.int64) * width + samples['x']
    for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
        valid &= np.isin((iy + dy) * width + ix + dx, addresses)
    if linear_luminance is not None:
        positions = [np.clip(np.searchsorted(addresses, (iy + dy) * width + ix + dx), 0, len(samples) - 1)
                     for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1))]
        wx, wy = x - ix, y - iy
        q = [linear_luminance[p] for p in positions]
        return (1-wx)*(1-wy)*q[0]+wx*(1-wy)*q[1]+(1-wx)*wy*q[2]+wx*wy*q[3], valid
    wx, wy = (x - ix)[:, None], (y - iy)[:, None]
    rgb = ((1 - wx) * (1 - wy) * image[iy, ix, :3] + wx * (1 - wy) * image[iy, ix + 1, :3] +
           (1 - wx) * wy * image[iy + 1, ix, :3] + wx * wy * image[iy + 1, ix + 1, :3]) / 255.
    return (rgb * np.array([.2126, .7152, .0722])).sum(axis=1), valid


def metrics(values):
    residual = np.abs(np.diff(values, axis=0))
    return dict(residual_mean=float(residual.mean()), residual_p95=float(np.quantile(residual, .95)),
                changed_fraction=float((residual > 1 / 255).mean()), mean_light=float(values.mean()),
                spatial_contrast=float(np.std(values, axis=1).mean()))


def analyze(folder):
    manifest = (folder / 'sign_manifest.txt').read_text()
    assert 'CAPTURE_COMPLETE variants=4' in manifest and 'REJECT' not in manifest, 'capture incomplete/rejected; inspect manifest'
    assert manifest.startswith('schema 2\n') and 'output 3840x2160 samples=2' in manifest
    frame_lines = re.findall(r'^frame (\S+) camera=(\w+) viewproj=(\w+).*?sim=(\w+) real=(\w+) fov=(\d+)$', manifest, re.M)
    frames = {row[0]: row[1:] for row in frame_lines}
    assert frames and len({row[2:] for row in frames.values()}) == 1, 'clock/FOV drift'
    planes = {int(i): (h, int(w), int(height), int(old)) for i, h, w, height, old in
              re.findall(r'^plane (\d+) hash=(\w+).*?size=(\d+)x(\d+) levels=\d+ old=(\d+)', manifest, re.M)}
    assert {'6EBF6F28', 'ED2BD84A'} <= {p[0] for p in planes.values()}
    assert any(p[0] not in ('6EBF6F28', 'ED2BD84A') for p in planes.values()), 'missing neon controls'
    result = []; out = folder / 'sign_analysis'; out.mkdir(exist_ok=True)
    for phase in range(3):
        indices = sorted(int(s.split('_')[2]) for s in frames if s.startswith(f'A_{phase}_'))
        assert indices == list(range(len(indices))) and len(indices) >= (8 if phase == 0 else 64 if phase == 1 else 1)
        dataset = {}
        for mode in 'ABCD':
            for i in indices:
                stem = f'{mode}_{phase}_{i:03d}'
                assert frames[stem] == frames[f'A_{phase}_{i:03d}'], f'camera/history mismatch {stem}'
                assert (folder / f'sign_{stem}_pose.bin').read_bytes() == (folder / f'sign_A_{phase}_{i:03d}_pose.bin').read_bytes(), f'pose bytes differ {stem}'
                raw = zlib.decompress((folder / f'sign_{stem}_samples.z').read_bytes())
                samples = np.frombuffer(raw, SAMPLE)
                assert len(samples) >= 48
                dataset[mode, i] = samples
                for plane in planes:
                    c = folder / f'sign_{stem}_{plane:03d}_constants.bin'
                    ref = folder / f'sign_A_{phase}_{i:03d}_{plane:03d}_constants.bin'
                    assert c.exists() == ref.exists(), f'draw identity drift {stem}/{plane}'
                    if c.exists():
                        # VS+PS constants; transform[] also records unrelated state.
                        assert c.read_bytes()[:(256+224)*16] == ref.read_bytes()[:(256+224)*16], f'constant drift {stem}/{plane}'
        for plane, (hash_, w, height, old) in planes.items():
            samples0 = dataset['A', indices[0]]
            samples0 = samples0[samples0['plane'] == plane]
            if len(samples0) < 16: continue
            lo = [np.quantile(samples0[k], .1) for k in ('u', 'v')]
            hi = [np.quantile(samples0[k], .9) for k in ('u', 'v')]
            gx, gy = np.meshgrid(np.linspace(lo[0], hi[0], min(128, max(16, w))),
                                 np.linspace(lo[1], hi[1], min(64, max(8, height))))
            grid = np.c_[gx.ravel(), gy.ravel()]
            for stage in ('scene', 'final'):
                values = {}; valid = np.ones(len(grid), bool); lods = {}
                for mode in 'ABCD':
                    sequence = []; lod = []
                    for i in indices:
                        stem = f'{mode}_{phase}_{i:03d}'
                        samples = dataset[mode, i]; samples = samples[samples['plane'] == plane]
                        assert len(samples) >= 16, f'missing plane {plane} in {stem}'
                        image = np.asarray(Image.open(folder / f'sign_{stem}_{stage}.png').convert('RGB'))
                        assert image.shape[:2] == (2160, 3840)
                        y, mask = registered(image, samples, grid, samples['scene_lum'] if stage == 'scene' else None); sequence.append(y); valid &= mask; lod.extend(samples['lod'])
                        # Retain an exact native-resolution ROI alongside registration.
                        box = (int(samples['x'].min()), int(samples['y'].min()), int(samples['x'].max()) + 1, int(samples['y'].max()) + 1)
                        Image.fromarray(image).crop(box).save(out / f'{stem}_plane{plane}_{stage}_roi.png')
                    values[mode] = np.array(sequence); lods[mode] = np.array(lod)
                assert valid.sum() >= 32, f'insufficient common plane support {plane} phase {phase}'
                for mode in 'ABCD':
                    y = values[mode][:, valid]
                    m = metrics(y) if len(y) > 1 else dict(mean_light=float(y.mean()), spatial_contrast=float(y.std()))
                    m.update(plane=plane, hash=hash_, variant=mode, phase=phase, stage=stage, registered_points=int(valid.sum()),
                             lod_p50=float(np.quantile(lods[mode], .5)), lod_p95=float(np.quantile(lods[mode], .95)),
                             lod_max=float(lods[mode].max()), above_old_terminal=float((lods[mode] > old - 1).mean()))
                    result.append(m)
                    if phase == 0 and len(y) > 1:
                        assert m['residual_mean'] <= .1 / 255 and m['changed_fraction'] <= .001, f'stationary animation/history drift: {m}'
                    np.savez_compressed(out / f'plane{plane}_{phase}_{mode}_{stage}.npz', luminance=y, grid=grid[valid])
                if stage == 'scene' and hash_ not in ('6EBF6F28', 'ED2BD84A'):
                    for mode in 'BCD':
                        assert np.abs(values[mode][:, valid] - values['A'][:, valid]).mean() <= 1 / 255, 'neon control changed'
    assert any(m['hash'] == '6EBF6F28' and m['phase'] == 2 and m['above_old_terminal'] > 0 for m in result), 'retreat did not reach original terminal mip'
    (out / 'metrics.json').write_text(json.dumps(result, indent=2) + '\n')
    rows = ['plane,hash,phase,stage,variant,residual_mean,residual_p95,changed_fraction,mean_light,spatial_contrast,lod_p95,above_old_terminal']
    for m in result: rows.append(','.join(str(m.get(k, '')) for k in rows[0].split(',')))
    (out / 'metrics.csv').write_text('\n'.join(rows) + '\n')
    print(f'PASS: matched capture, stationary/history/constant checks, neon controls; {len(result)} registered measurements in {out}')
    print('Interpret reductions only where the measured footprint reaches the original terminal mip; compare A/B, then B/C and B/D. No timing claim.')
    return 0


def analysis_control():
    # Known planar UV mapping and temporal residual; corrupting geometry must fail.
    x, y = np.meshgrid(np.arange(4, 36), np.arange(4, 28))
    s = np.zeros(x.size, SAMPLE); s['x'], s['y'] = x.ravel(), y.ravel(); s['u'] = (x.ravel() + .5) / 64; s['v'] = (y.ravel() + .5) / 48
    homography(s, 64, 48)
    # Perspective UVs at exact native pixel centres, not rounded point fixtures.
    perspective = np.array([[1., .1, .1], [.05, .8, .05], [.1, .2, 1.]])
    points = np.c_[(s['x'] + .5) / 64, (s['y'] + .5) / 48, np.ones(len(s))]
    uv = np.einsum('ij,kj->ik', points, np.linalg.inv(perspective))
    projected = s.copy(); projected['u'] = uv[:, 0] / uv[:, 2]; projected['v'] = uv[:, 1] / uv[:, 2]
    homography(projected, 64, 48)
    image = np.zeros((48, 64, 3), np.uint8)
    image[:] = (np.arange(64)[None, :, None] * 3)
    grid = np.c_[projected['u'][100:500], projected['v'][100:500]]
    light, mask = registered(image, projected, grid)
    expected = projected['x'][100:500] * 3 / 255.
    assert mask.sum() > 300 and np.max(np.abs(light[mask] - expected[mask])) < 1e-5
    # HDR scene measurements must retain light above the PNG preview's ceiling.
    hdr_values = projected['x'].astype(float) / 10.
    hdr, hdr_mask = registered(image, projected, grid, hdr_values)
    assert hdr_mask.sum() > 300 and np.max(np.abs(hdr[hdr_mask] - projected['x'][100:500][hdr_mask] / 10.)) < 1e-5
    assert hdr[hdr_mask].max() > 3
    broken = s.copy(); broken['x'][::3] += 8
    try: homography(broken, 64, 48)
    except AssertionError: pass
    else: raise AssertionError('nonplanar mutation passed')
    control = np.ones((64, 128)) * .5; flicker = control.copy(); flicker[1::2] += .1
    assert metrics(control)['residual_mean'] == 0 and metrics(flicker)['residual_mean'] > .09
    print('PASS: planar registration; nonplanar mutation rejected; stationary 0 vs flicker 0.1 residual')


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--cpu', action='store_true'); p.add_argument('--analyze', type=Path)
    args = p.parse_args()
    if args.analyze: return analyze(args.analyze)
    analysis_control()
    return check_native(args.cpu)


if __name__ == '__main__':
    raise SystemExit(main())
