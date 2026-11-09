#!/usr/bin/env python3
"""Equal-size production smoke composition preserves all 9,216 source bytes.

Recovered 64x36 BGRA8 fixture: expected native identity, 0/9216 bad bytes.
One-texel sampling mutant: 2304/9216 bad bytes; production: 0/9216.
Both execute the byte comparison; exits 1/0 respectively.
The full offline suite autodiscovers this driver. Metal unavailable exits 3.
No private assets or spatial fixtures required.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def section(source, start, end):
    return source[source.index(start):source.index(end, source.index(start))]


def checked_run(args):
    result = subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError(f'exit {result.returncode}\n{result.stdout[-2000:]}{result.stderr[-5000:]}')
    return result


PRE = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
static id<MTLDevice> mtl;
static id<MTLCommandQueue> queue;
static id<MTLCommandBuffer> cmd;
static int failures,gpt_on;
#define CHECK(x,label) do{if(!(x)){fprintf(stderr,"FAIL %s line=%d\n",label,__LINE__);++failures;}}while(0)
#define gpt_render_auto(...) ((void)0)
#define gpt_name(...) ((void)0)
static id<MTLRenderPipelineState> pipeline_build(MTLRenderPipelineDescriptor *p,const char *name,NSError **e){(void)name;return [mtl newRenderPipelineStateWithDescriptor:p error:e];}
static id<MTLLibrary> pipeline_library_source(NSString *s,const char *name,NSError **e){(void)name;return [mtl newLibraryWithSource:s options:nil error:e];}
static id<MTLTexture> texture(NSUInteger w,NSUInteger h,MTLPixelFormat f,NSUInteger samples){
 MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:f width:w height:h mipmapped:NO];
 d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead|MTLTextureUsagePixelFormatView;
 d.storageMode=MTLStorageModePrivate;
 if(samples>1){d.textureType=MTLTextureType2DMultisample;d.sampleCount=samples;}
 return [mtl newTextureWithDescriptor:d];
}
'''

MAIN = r'''static void draw(id<MTLTexture> color,id<MTLTexture> depth,int hud){
 NSString *src=@"#include <metal_stdlib>\nusing namespace metal;\nvertex float4 vs(uint i [[vertex_id]]){return float4(float2((i<<1)&2,i&2)*2-1,.25,1);}\n"
 "fragment float4 fs(float4 p [[position]],constant uint &hud [[buffer(0)]]){if(hud){if(p.x<64||p.x>=128||p.y<48||p.y>=80)discard_fragment();uint b=(uint(p.x)+uint(p.y))&1;return float4(b,1-b,1,1);}return float4(.2+.3*fract(p.x/71),.3,.6,1);}";
 NSError *error=nil;id<MTLLibrary> lib=[mtl newLibraryWithSource:src options:nil error:&error];
 MTLRenderPipelineDescriptor *p=[MTLRenderPipelineDescriptor new];p.vertexFunction=[lib newFunctionWithName:@"vs"];p.fragmentFunction=[lib newFunctionWithName:@"fs"];p.colorAttachments[0].pixelFormat=color.pixelFormat;p.rasterSampleCount=color.sampleCount;
 if(depth)p.depthAttachmentPixelFormat=p.stencilAttachmentPixelFormat=depth.pixelFormat;
 id<MTLRenderPipelineState> pipeline=[mtl newRenderPipelineStateWithDescriptor:p error:&error];CHECK(pipeline,"world/HUD pipeline");
 MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];rp.colorAttachments[0].texture=color;rp.colorAttachments[0].loadAction=MTLLoadActionLoad;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
 if(depth){rp.depthAttachment.texture=rp.stencilAttachment.texture=depth;rp.depthAttachment.loadAction=rp.stencilAttachment.loadAction=MTLLoadActionLoad;rp.depthAttachment.storeAction=rp.stencilAttachment.storeAction=MTLStoreActionStore;}
 id<MTLRenderCommandEncoder> e=[cmd renderCommandEncoderWithDescriptor:rp];[e setRenderPipelineState:pipeline];
 if(depth){MTLDepthStencilDescriptor *ds=[MTLDepthStencilDescriptor new];ds.depthCompareFunction=MTLCompareFunctionAlways;ds.depthWriteEnabled=YES;MTLStencilDescriptor *st=[MTLStencilDescriptor new];st.stencilCompareFunction=MTLCompareFunctionAlways;st.depthStencilPassOperation=MTLStencilOperationReplace;ds.frontFaceStencil=ds.backFaceStencil=st;[e setDepthStencilState:[mtl newDepthStencilStateWithDescriptor:ds]];[e setStencilReferenceValue:99];}
 [e setViewport:(MTLViewport){0,0,color.width,color.height,0,1}];[e setFragmentBytes:&hud length:4 atIndex:0];
 [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
}
static id<MTLBuffer> snapshot(id<MTLTexture> t){
 NSUInteger pixelBytes=t.pixelFormat==MTLPixelFormatRGBA16Float?8:4;
 NSUInteger pitch=(t.width*pixelBytes+255)&~255ul;id<MTLBuffer> b=[mtl newBufferWithLength:pitch*t.height options:MTLResourceStorageModeShared];
 id<MTLBlitCommandEncoder> e=[cmd blitCommandEncoder];
 [e copyFromTexture:t sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(t.width,t.height,1) toBuffer:b destinationOffset:0 destinationBytesPerRow:pitch destinationBytesPerImage:pitch*t.height];[e endEncoding];return b;
}
static void finish(void){[cmd commit];[cmd waitUntilCompleted];CHECK(cmd.status==MTLCommandBufferStatusCompleted,"GPU completion");}
static void clear_texture(id<MTLTexture> t,MTLClearColor color){
 MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];rp.colorAttachments[0].texture=t;
 rp.colorAttachments[0].loadAction=MTLLoadActionClear;rp.colorAttachments[0].clearColor=color;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
 [[cmd renderCommandEncoderWithDescriptor:rp] endEncoding];
}
static int smoke_check(void){
 // The unchanged pixel-addressed path must remain exact at matching sizes.
 cmd=[queue commandBuffer];soft_late_layer=texture(64,36,MTLPixelFormatBGRA8Unorm,1);
 id<MTLTexture> native=texture(64,36,MTLPixelFormatBGRA8Unorm,1);
 draw(soft_late_layer,nil,0);clear_texture(native,MTLClearColorMake(0,0,0,1));
 soft_late_composite(native);
 id<MTLBuffer> expected=snapshot(soft_late_layer),actual=snapshot(native);finish();
 NSUInteger native_bad=0;
 for(NSUInteger i=0;i<9216;i++)native_bad+=((const uint8_t *)expected.contents)[i]!=((const uint8_t *)actual.contents)[i];
 printf("Smoke native identity bad_bytes=%lu/9216\n",(unsigned long)native_bad);
 CHECK(!native_bad,"matched-size smoke composition byte-identical");
 return failures!=0;
}

int main(void){@autoreleasepool{
 mtl=MTLCreateSystemDefaultDevice();if(!mtl){puts("GPU PENDING: Metal unavailable");return 3;}
 queue=[mtl newCommandQueue];return smoke_check();
}}
'''

def make_harness(source):
    return (PRE +
            section(source, 'static const char soft_copy_msl[] =', 'static NSMutableArray *soft_late_draws') +
            section(source, 'static id<MTLRenderPipelineState> soft_late_pipeline(MTLPixelFormat format, int scaled) {', 'static MTLRenderPassDescriptor *soft_proof_pass') + MAIN)


def mutate(harness):
    # Offset native pixel sampling by one texel, preserving the blend and alpha.
    needle = 'return t.read(uint2(i.p.xy));'
    assert harness.count(needle) == 1
    return harness.replace(needle, 'return t.read(uint2(i.p.xy) ^ uint2(1,0));')


PREFIX = 'smoke-native-copy-'
FAILURE = 'FAIL matched-size smoke composition byte-identical'


def main():
    source = (ROOT / 'render.m').read_text()
    good = make_harness(source)
    broken = mutate(good)
    assert broken != good, 'mutation did not apply'
    with tempfile.TemporaryDirectory(prefix=PREFIX) as directory:
        tmp = Path(directory)
        fixture, executable = tmp / 'fixture.m', tmp / 'fixture'
        build = ['clang', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                 '-Wno-unused-variable', '-Wno-incompatible-pointer-types', '-fobjc-arc',
                 '-I', ROOT, '-I', ROOT.parent / 'recomp', fixture,
                 '-framework', 'Foundation', '-framework', 'Metal', '-o', executable]
        fixture.write_text(good)
        checked_run(build)
        result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120)
        print('FIXED ' + result.stdout + result.stderr, end='', flush=True)
        if result.returncode == 3:
            raise SystemExit(3)
        assert result.returncode == 0, result.stdout + result.stderr
        proof = 'FIXED ' + result.stdout + result.stderr
        fixture.write_text(broken)
        checked_run(build)
        result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120)
        print('MUTANT ' + result.stdout + result.stderr, end='', flush=True)
        assert result.returncode == 1 and FAILURE in result.stderr, result.stdout + result.stderr
        proof += 'MUTANT ' + result.stdout + result.stderr
        report = Path(os.environ.get('NFS_METAL_CACHE', ROOT.parent / '.metal-cache')) / (Path(__file__).stem + '.log')
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(proof)
        print(PREFIX + 'PASS fixed=0 mutant=1')


if __name__ == '__main__':
    main()
