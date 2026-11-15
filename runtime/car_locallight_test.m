// Actual production lamp patch vs frozen native paint, zero/away/facing normals.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int car_local_test(const char *material, const char *patched, int no_shadow) {
    id<MTLDevice> device=MTLCreateSystemDefaultDevice();
    if(!device){fprintf(stderr,"car_locallight_test: SKIP native Metal; run on a Metal-capable host\n");return 3;}
    NSString *vs=@"\nvertex In cp_vs(uint id [[vertex_id]]) {\n"
        "float2 p=float2((id<<1)&2,id&2); In o;\n"
        "o.pos=float4(p*2-1,0.5,1); o.color0=float4(.32,.54,.75,.73); o.color1=float4(.3,.4,.6,.7);\n"
        "o.texcoord0=float4(0,0,1,0); o.texcoord1=float4(p,0,0); o.texcoord2=float4(.5,.5,.5,1);\n"
        "o.texcoord3=float4(0,0,1,0); o.texcoord4=float4(0,0,1,0);\n"
        "o.texcoord5=float4(0,0,1,0); o.texcoord6=float4(p,0,.5); return o;}\n";
    if(no_shadow) vs=@"\nvertex SmPSStageIn cp_vs(uint id [[vertex_id]]) {\n"
        "float2 p=float2((id<<1)&2,id&2); SmPSStageIn o;\n"
        "o.pos=float4(p*2-1,0.5,1); o.color0=float4(.32,.54,.75,.73); o.color1=float4(.3,.4,.6,.7);\n"
        "o.texcoord0=float4(0,0,1,0); o.texcoord1=float4(p,0,0); o.texcoord2=float4(0,0,1,0);\n"
        "o.texcoord3=float4(0,0,1,0); return o;}\n";
    id<MTLCommandQueue> queue=[device newCommandQueue];
    id<MTLRenderPipelineState> pipelines[2][2];
    for(int mutant=0;mutant<2;mutant++) {
        NSString *source=[NSString stringWithFormat:@"#define MAT_VARIANT 2\n#define MAT_SHADOW_HW 0\n#define MAT_CAR_HDR 1\n#define MAT_PAINT_NAN_TINT 0\n#define MAT_PAINT_FINITE_AB 0\n#define MAT_PAINT_POW_MUTANT %d\n%s%@",0,mutant?patched:material,vs];
        if(mutant)source=[source stringByReplacingOccurrencesOfString:@"o.texcoord6=float4(p,0,.5);" withString:@"o.texcoord6=float4(p,0,.5); o.texcoord7=float4(.3,.4,.5,0);"];
        if(mutant && no_shadow) source=[source stringByReplacingOccurrencesOfString:@"return o;}" withString:@"o.texcoord4=float4(0,0,1,0); o.texcoord5=float4(0,0,1,0); o.texcoord7=float4(p,0,1); return o;}"];
        MTLCompileOptions *options=[MTLCompileOptions new];options.mathMode=MTLMathModeRelaxed;
        NSError *error=nil;id<MTLLibrary> library=[device newLibraryWithSource:source options:options error:&error];
        if(!library){fprintf(stderr,"car_locallight_test: MSL %s\n",error.localizedDescription.UTF8String);return 1;}
        for(int fp=0;fp<2;fp++) {
            MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];
            pd.vertexFunction=[library newFunctionWithName:@"cp_vs"];pd.fragmentFunction=[library newFunctionWithName:@"sm_main"];
            pd.colorAttachments[0].pixelFormat=fp?MTLPixelFormatRGBA32Float:MTLPixelFormatBGRA8Unorm;
            pipelines[mutant][fp]=[device newRenderPipelineStateWithDescriptor:pd error:&error];
            if(!pipelines[mutant][fp]){fprintf(stderr,"car_locallight_test: PSO %s\n",error.localizedDescription.UTF8String);return 1;}
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
    const float powers[]={0,1,4};
    unsigned changed_float=0,changed_bgra=0,bytes=0;
    for(int lamp=0;lamp<4;lamp++)for(unsigned power=0;power<sizeof powers/sizeof *powers;power++)for(int nm=0;nm<2;nm++)for(int roll=0;roll<2;roll++) {
        float output[2][1024][4];uint8_t bgra[2][1024][4];
        float c[2][4]={{0},{1.0f/2048}},m[52][4]={{0}};uint8_t aux[16+16*16]={0};
        m[0][0]=powers[power];m[0][1]=.734987;m[1][0]=.7055;m[1][1]=.747;m[1][2]=.83;m[1][3]=roll;
        m[3][3]=nm;m[4][0]=.125;m[4][1]=.25;m[4][2]=.5;m[7][1]=1;m[8][0]=m[8][1]=m[8][2]=.25;m[10][0]=1;m[11][3]=-1;
        if(no_shadow){m[14][0]=.3;m[14][1]=.4;m[14][2]=.5;}
        m[12][2]=(lamp==1 || lamp==3)?-10:10;
        if(lamp){m[13][0]=.2;m[13][1]=.3;m[13][2]=.4;m[13][3]=1;}
        // Light 0 faces away; only light 18 can brighten this panel.
        // This exercises the last legal constant and rejects a one-light loop.
        if(lamp==3){m[13][3]=19;m[50][2]=10;m[51][0]=.2;m[51][1]=.3;m[51][2]=.4;m[51][3]=1;}

        for(int mutant=0;mutant<2;mutant++)for(int fp=0;fp<2;fp++) {
            id<MTLCommandBuffer> cb=[queue commandBuffer];MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
            rp.colorAttachments[0].texture=targets[fp];rp.colorAttachments[0].loadAction=MTLLoadActionDontCare;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
            id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:rp];[e setRenderPipelineState:pipelines[mutant][fp]];
            [e setFragmentBytes:c length:sizeof c atIndex:0];[e setFragmentBytes:aux length:sizeof aux atIndex:3];[e setFragmentBytes:m length:sizeof m atIndex:4];
            for(int i=0;i<4;i++){[e setFragmentTexture:tex[no_shadow && i<2 ? 1-i : i] atIndex:i];[e setFragmentSamplerState:sampler atIndex:i];}
            [e setFragmentTexture:tex[4] atIndex:14];[e setFragmentTexture:nmap atIndex:15];
            [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];[cb commit];[cb waitUntilCompleted];
            if(cb.status!=MTLCommandBufferStatusCompleted)return 1;
            [targets[fp] getBytes:fp?(void *)output[mutant]:(void *)bgra[mutant] bytesPerRow:1024*(fp?16:4) fromRegion:MTLRegionMake2D(0,0,1024,1) mipmapLevel:0];
        }
        unsigned diff=0, brighter=0;
        for(int x=0;x<1024;x++) {
            for(int z=0;z<2;z++)for(int k=0;k<4;k++)if(!isfinite(output[z][x][k]))return 1;
            int f=memcmp(output[0][x],output[1][x],16)!=0,b=memcmp(bgra[0][x],bgra[1][x],4)!=0;
            diff+=f || b;
            if(lamp<2){changed_float+=f;changed_bgra+=b;bytes+=20;}
            else {
                for(int k=0;k<3;k++)if(output[1][x][k] < output[0][x][k] - 1e-6f)return 1;
                brighter+=output[1][x][0] > output[0][x][0] + .001f;
            }
        }
        if(lamp>=2 && brighter!=1024){fprintf(stderr,"facing lamp failed: nm=%d roll=%d brighter=%u\n",nm,roll,brighter);return 1;}
    }
    fprintf(stderr,"car_locallight_test: zero/away compared_bytes=%u changed_float=%u changed_bgra=%u; single/last-light facing 24576/24576 brighter\n",bytes,changed_float,changed_bgra);
    if(changed_float || changed_bgra)return 1;
    return 0;
}
int main(int argc,char **argv) {
    @autoreleasepool {
        if(argc!=4)return 2;
        NSString *base=[NSString stringWithContentsOfFile:@(argv[1]) encoding:NSUTF8StringEncoding error:nil];
        NSString *on=[NSString stringWithContentsOfFile:@(argv[2]) encoding:NSUTF8StringEncoding error:nil];
        if(!base || !on)return 1;
        return car_local_test(base.UTF8String,on.UTF8String,atoi(argv[3]));
    }
}
