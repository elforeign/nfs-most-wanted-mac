// Shared native test: actual replacement shader, original powr control, same inputs.
// Expected zero-exponent failures: original 2048 nonfinite pixels -> fixed 0;
// 67,584 finite pixels / 1,351,680 FP32+BGRA8 bytes must match (two rolloff modes).
// Finite-output mutant: 67,584 changed finite pixels (normal: 0), must fail.
// Observed GPU results and BGRA8 NaN/Inf bytes: runtime/README.md. No assets.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int carpaint_nan_test(const char *material, int broken) {
    id<MTLDevice> device=MTLCreateSystemDefaultDevice();
    if(!device){fprintf(stderr,"carpaint_nan_test: SKIP native Metal; run on a Metal-capable host\n");return 3;}
    NSString *vs=@"\nvertex In cp_vs(uint id [[vertex_id]]) {\n"
        "float2 p=float2((id<<1)&2,id&2); In o;\n"
        "o.pos=float4(p*2-1,0.5,1); o.color0=float4(.32,.54,.75,.73); o.color1=float4(.3,.4,.6,.7);\n"
        "o.texcoord0=float4(0,0,1,0); o.texcoord1=float4(p,0,0); o.texcoord2=float4(.5,.5,.5,1);\n"
        "o.texcoord3=float4(0,0,1,0); o.texcoord4=float4(0,0,1,0);\n"
        "o.texcoord5=float4(0,0,p.x*2-1,0); o.texcoord6=float4(p,0,.5); return o;}\n";
    id<MTLCommandQueue> queue=[device newCommandQueue];
    id<MTLRenderPipelineState> pipelines[2][2];
    for(int mutant=0;mutant<2;mutant++) {
        NSString *source=[NSString stringWithFormat:@"#define MAT_VARIANT 2\n#define MAT_SHADOW_HW 0\n#define MAT_CAR_HDR 1\n#define MAT_PAINT_NAN_TINT 0\n#define MAT_PAINT_FINITE_AB 0\n#define MAT_PAINT_POW_MUTANT %d\n%s%@",mutant || broken == 1,material,vs];
        // Reference uses the original inline powr expressions, without a helper call.
        if(mutant)source=[source stringByReplacingOccurrencesOfString:@"car_spec_pow(rv," withString:@"powr(rv,"];
        if(broken == 2 && !mutant)source=[source stringByReplacingOccurrencesOfString:@"    return o;" withString:@"    o.c0.r += 1.0f/255.0f; return o;"];
        MTLCompileOptions *options=[MTLCompileOptions new];options.mathMode=MTLMathModeRelaxed;
        NSError *error=nil;id<MTLLibrary> library=[device newLibraryWithSource:source options:options error:&error];
        if(!library){fprintf(stderr,"carpaint_nan_test: MSL %s\n",error.localizedDescription.UTF8String);return 1;}
        for(int fp=0;fp<2;fp++) {
            MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];
            pd.vertexFunction=[library newFunctionWithName:@"cp_vs"];pd.fragmentFunction=[library newFunctionWithName:@"sm_main"];
            pd.colorAttachments[0].pixelFormat=fp?MTLPixelFormatRGBA32Float:MTLPixelFormatBGRA8Unorm;
            pipelines[mutant][fp]=[device newRenderPipelineStateWithDescriptor:pd error:&error];
            if(!pipelines[mutant][fp]){fprintf(stderr,"carpaint_nan_test: PSO %s\n",error.localizedDescription.UTF8String);return 1;}
        }
    }
    // Constant textures keep all math except the specular exponent in-domain.
    id<MTLTexture> tex[5];
    for(int i=0;i<5;i++) {
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:1 height:1 mipmapped:NO];
        if(i==1)d.textureType=MTLTextureTypeCube;
        if(i==4)d.textureType=MTLTextureType3D;
        d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageShaderRead;
        tex[i]=[device newTextureWithDescriptor:d];
        const float values[5][4]={{.25,.5,.75,.5},{.125,.25,.5,.5},{.375,.375,.375,1},{0,0,0,0},{.5,.5,1,1}};
        if(i==1)for(int face=0;face<6;face++)[tex[i] replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 slice:face withBytes:values[i] bytesPerRow:16 bytesPerImage:16];
        else if(i==4)[tex[i] replaceRegion:MTLRegionMake3D(0,0,0,1,1,1) mipmapLevel:0 slice:0 withBytes:values[i] bytesPerRow:16 bytesPerImage:16];
        else [tex[i] replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:values[i] bytesPerRow:16];
        if(!tex[i])return 1;
    }
    // nmap is 2D; flake_vol is 3D, unused because k[11].x=0.
    MTLTextureDescriptor *nd=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:1 height:1 mipmapped:NO];
    nd.storageMode=MTLStorageModeShared;nd.usage=MTLTextureUsageShaderRead;
    id<MTLTexture> nmap=[device newTextureWithDescriptor:nd];float nvalue[4]={.5,.5,1,1};
    [nmap replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:nvalue bytesPerRow:16];
    MTLSamplerDescriptor *sd=[MTLSamplerDescriptor new];sd.minFilter=sd.magFilter=MTLSamplerMinMagFilterNearest;
    id<MTLSamplerState> sampler=[device newSamplerStateWithDescriptor:sd];
    id<MTLTexture> targets[2];
    for(int fp=0;fp<2;fp++) {
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:fp?MTLPixelFormatRGBA32Float:MTLPixelFormatBGRA8Unorm width:1024 height:1 mipmapped:NO];
        d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageRenderTarget;targets[fp]=[device newTextureWithDescriptor:d];
    }
    const float powers[]={0,.923,1,1.25,1.265,1.5,1.69,1.76,2,2.2,2.5,3,3.271,3.888,4,4.5,5};
    unsigned bad[2]={0},finite=0,changed=0,changed_float=0,changed_bgra=0,bytes=0;float max_error=0;
    for(unsigned power=0;power<sizeof powers/sizeof *powers;power++)for(int nm=0;nm<2;nm++)for(int roll=0;roll<2;roll++) {
        float output[2][1024][4];uint8_t bgra[2][1024][4];
        float c[2][4]={{0},{1.0f/2048}},m[12][4]={{0}};uint8_t aux[16+16*16]={0};
        m[0][0]=powers[power];m[0][1]=.734987;m[1][0]=.7055;m[1][1]=.747;m[1][2]=.83;m[1][3]=roll;
        m[3][3]=nm;m[4][0]=.125;m[4][1]=.25;m[4][2]=.5;m[7][1]=1;m[8][0]=m[8][1]=m[8][2]=.25;m[10][0]=1;m[11][3]=-1;
        for(int mutant=0;mutant<2;mutant++)for(int fp=0;fp<2;fp++) {
            id<MTLCommandBuffer> cb=[queue commandBuffer];MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
            rp.colorAttachments[0].texture=targets[fp];rp.colorAttachments[0].loadAction=MTLLoadActionDontCare;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
            id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:rp];[e setRenderPipelineState:pipelines[mutant][fp]];
            [e setFragmentBytes:c length:sizeof c atIndex:0];[e setFragmentBytes:aux length:sizeof aux atIndex:3];[e setFragmentBytes:m length:sizeof m atIndex:4];
            for(int i=0;i<4;i++){[e setFragmentTexture:tex[i] atIndex:i];[e setFragmentSamplerState:sampler atIndex:i];}
            [e setFragmentTexture:tex[4] atIndex:14];[e setFragmentTexture:nmap atIndex:15];
            [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];[cb commit];[cb waitUntilCompleted];
            if(cb.status!=MTLCommandBufferStatusCompleted)return 1;
            [targets[fp] getBytes:fp?(void *)output[mutant]:(void *)bgra[mutant] bytesPerRow:1024*(fp?16:4) fromRegion:MTLRegionMake2D(0,0,1024,1) mipmapLevel:0];
        }
        for(int x=0;x<1024;x++) {
            int nf[2]={0};for(int z=0;z<2;z++)for(int k=0;k<4;k++)nf[z]|=!isfinite(output[z][x][k]);
            bad[0]+=nf[0];bad[1]+=nf[1];
            if(!nf[1]){
                finite++;bytes+=20;int f=memcmp(output[0][x],output[1][x],16)!=0,b=memcmp(bgra[0][x],bgra[1][x],4)!=0;
                changed+=f || b;changed_float+=f;changed_bgra+=b;
                for(int k=0;k<4;k++)max_error=fmaxf(max_error,fabsf(output[0][x][k]-output[1][x][k]));
            }
        }
    }
    fprintf(stderr,"carpaint_nan_test: fixed_nonfinite=%u original_nonfinite=%u finite=%u changed=%u compared_bytes=%u broken=%d\n",bad[0],bad[1],finite,changed,bytes,broken);
    fprintf(stderr,"carpaint_nan_test: changed_float=%u changed_bgra=%u max_error=%g\n",changed_float,changed_bgra,max_error);
    if(bad[0] || bad[1]!=2048 || changed || finite!=67584 || bytes!=1351680)return 1;
    // Decode today's Apple BGRA8 store. This is observation, not a portable NaN contract.
    NSString *store=@"#include <metal_stdlib>\nusing namespace metal;\nvertex float4 vs(uint id [[vertex_id]]){float2 p=float2((id<<1)&2,id&2);return float4(p*2-1,0,1);}\nfragment float4 fs(float4 p [[position]],constant uint *b [[buffer(0)]]){return float4(as_type<float>(b[uint(p.x)]),.25,.5,1);}";
    NSError *error=nil;MTLCompileOptions *options=[MTLCompileOptions new];options.mathMode=MTLMathModeRelaxed;
    id<MTLLibrary> library=[device newLibraryWithSource:store options:options error:&error];
    MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];pd.vertexFunction=[library newFunctionWithName:@"vs"];pd.fragmentFunction=[library newFunctionWithName:@"fs"];pd.colorAttachments[0].pixelFormat=MTLPixelFormatBGRA8Unorm;
    id<MTLRenderPipelineState> pipeline=[device newRenderPipelineStateWithDescriptor:pd error:&error];if(!pipeline)return 1;
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:3 height:1 mipmapped:NO];d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageRenderTarget;
    id<MTLTexture> target=[device newTextureWithDescriptor:d];id<MTLCommandBuffer> cb=[queue commandBuffer];MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];rp.colorAttachments[0].texture=target;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
    uint32_t bits[]={0x7fc00000,0x7f800000,0xff800000};id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:rp];[e setRenderPipelineState:pipeline];[e setFragmentBytes:bits length:sizeof bits atIndex:0];[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];[cb commit];[cb waitUntilCompleted];
    if(cb.status!=MTLCommandBufferStatusCompleted)return 1;uint8_t raw[12];[target getBytes:raw bytesPerRow:12 fromRegion:MTLRegionMake2D(0,0,3,1) mipmapLevel:0];
    fprintf(stderr,"carpaint_nan_test: Apple BGRA8 R bytes NaN=%u +Inf=%u -Inf=%u; G=%u B=%u A=%u PASS\n",raw[2],raw[6],raw[10],raw[1],raw[0],raw[3]);
    return 0;
}

#ifndef CARPAINT_TEST_IN_RUNTIME
int main(int argc,const char **argv) { @autoreleasepool {
    if(argc<2)return 2;NSString *s=[NSString stringWithContentsOfFile:@(argv[1]) encoding:NSUTF8StringEncoding error:nil];if(!s)return 2;
    return carpaint_nan_test(s.UTF8String,argc>2 ? (!strcmp(argv[2],"--finite-mutant") ? 2 : !strcmp(argv[2],"--broken")) : 0);
} }
#endif
