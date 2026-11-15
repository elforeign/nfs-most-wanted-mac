// Production Metal fixture: exact Off bytes, both paint paths, flake and t0.
// Shipped round-1 -> fixed: highlight failures 48/96 -> 0/96,
// minimum On/Off highlight .16666667 -> .92418188 (required >=.9);
// grazing failures 96/96 -> 0/96, ratio .60687029 -> 1.36829150 (>=1).
// Transmission failures 60/69 -> 0/69; diffuse 96/96 -> 0/96 (T +/- .02).
// Character-line fixture N.V=N.L=.7, N.H=1: F=.0423328, T=.91712642.
// Ordinary Off diffuse/spec/hotspot/env = 1/1/5/.5, total 7.5.
// Shipped On: .1368096/.1368096/.684048/0 + coat .0423328 = 1.
// Fixed On: .9171264/.9171264/4.585632/.4585632 + .0423328 = 6.920781.
// Unit-radiance layer <=1; inherited HDR <=max(1,Off), never normalized.
// Full-paint energy failures 0/576; fixed max 6.95199966 (was 1.00000012).
// Smooth reflection 0/192, rough-reflection control 96/192; t0 0/768.
// Layer good/lobe/attenuation/doubled-env: 0/7/23/13 failures of 23.
// Production good/lobe/attenuation/env: 0/1152/2304/1152 channel failures.
// 1536 pixels, 20 sources; flake changes 144/528 channels; nonfinite 0.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

static id<MTLLibrary> library(id<MTLDevice> device, NSString *source) {
    NSError *error = nil;
    MTLCompileOptions *options = [MTLCompileOptions new];
    options.mathMode = MTLMathModeRelaxed;
    id<MTLLibrary> lib = [device newLibraryWithSource:source options:options error:&error];
    if (!lib) fprintf(stderr, "clearcoat_test: MSL %s\n", error.localizedDescription.UTF8String);
    return lib;
}
static void draw(id<MTLCommandQueue> queue, id<MTLRenderPipelineState> pipeline,
                 id<MTLTexture> target, id<MTLTexture> __strong *textures, const int *slots,
                 id<MTLSamplerState> sampler, float m[12][4], float a[4], float output[16][4]) {
    float c[256][4]={{0}}; unsigned char aux[2048]={0};
    id<MTLCommandBuffer> cb=[queue commandBuffer];
    MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture=target;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
    id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:rp];
    [e setRenderPipelineState:pipeline];[e setVertexBytes:a length:16 atIndex:1];
    [e setFragmentBytes:c length:sizeof c atIndex:0];
    [e setFragmentBytes:c length:sizeof c atIndex:1];[e setFragmentBytes:c length:sizeof c atIndex:2];
    [e setFragmentBytes:aux length:sizeof aux atIndex:3];[e setFragmentBytes:m length:12*16 atIndex:4];
    for(int i=0;i<6;i++){[e setFragmentTexture:textures[i] atIndex:slots[i]];if(i<4)[e setFragmentSamplerState:sampler atIndex:i];}
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];[cb commit];[cb waitUntilCompleted];
    if(cb.status!=MTLCommandBufferStatusCompleted){fprintf(stderr,"clearcoat_test: draw failed\n");exit(2);}
    [target getBytes:output bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,4,4) mipmapLevel:0];
}
static double fresnel(double cosine) { return .04 + .96 * pow(1-fmax(0,fmin(1,cosine)),5); }
static NSString *defines(int variant, int hardware) {
    return [NSString stringWithFormat:@"#define MAT_VARIANT %d\n#define MAT_SHADOW_HW %d\n#define MAT_CAR_HDR 1\n#define MAT_PAINT_POW_MUTANT 0\n#define MAT_PAINT_NAN_TINT 0\n#define MAT_PAINT_FINITE_AB 0\n",variant,hardware];
}

int main(int argc, const char **argv) { @autoreleasepool {
    if (argc < 6) return 2;
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (!device) { fprintf(stderr,"clearcoat_test: SKIP no Metal device\n"); return 3; }
    NSString *off = [NSString stringWithContentsOfFile:@(argv[1]) encoding:NSUTF8StringEncoding error:nil];
    NSString *on = [NSString stringWithContentsOfFile:@(argv[2]) encoding:NSUTF8StringEncoding error:nil];
    if (!off || !on) return 2;
    const char *control = argc > 6 ? argv[6] : "none";
    if (!strcmp(control,"lobe")) on = [on stringByReplacingOccurrencesOfString:@"float peak = powr(rv, 256.0f) * float(nv > 0.0f && nl > 0.0f);" withString:@"float peak = 0.0f;"];
    if (!strcmp(control,"attenuation")) on = [on stringByReplacingOccurrencesOfString:@"float3((1.0f - F) * (1.0f - F), F - direct, direct)" withString:@"float3(1.0f, F - direct, direct)"];
    if (!strcmp(control,"env")) on = [on stringByReplacingOccurrencesOfString:@"w.y * env" withString:@"2.0f * w.y * env"];
    id<MTLCommandQueue> queue = [device newCommandQueue];
    NSString *kernel = @"\nkernel void coat_probe(device const float4 *input [[buffer(0)]], device float4 *out [[buffer(1)]], uint i [[thread_position_in_grid]]) {\n"
        "float3 N=input[3*i].xyz,V=input[3*i+1].xyz,L=input[3*i+2].xyz;float vis=input[3*i+2].w;\n"
        "float3 w=coat_weights(N,V,L,vis);out[4*i]=float4(w,coat_fresnel(dot(coat_unit(N),coat_unit(V))));\n"
        "out[4*i+1]=float4(coat_layer(float3(1),float3(1),float3(1),N,V,L,vis),1);\n"
        "out[4*i+2]=float4(coat_layer(float3(1),float3(0),float3(0),N,V,L,vis),1);\n"
        "out[4*i+3]=float4(coat_layer(float3(0),float3(0),float3(1),N,V,L,vis),1);}\n";
    id<MTLLibrary> lib = library(device, [[defines(2,0) stringByAppendingString:on] stringByAppendingString:kernel]);
    if (!lib) return 1;
    NSError *error = nil;
    id<MTLComputePipelineState> compute = [device newComputePipelineStateWithFunction:[lib newFunctionWithName:@"coat_probe"] error:&error];
    if (!compute) return 1;
    float inputs[32][3][4] = {{{0}}};
    double expectedF[32], expectedSun[32];
    const double degrees[] = {0,45,60,80,85,89,90};
    unsigned count=0;
    for (unsigned angle=0;angle<sizeof degrees/sizeof *degrees;angle++) for(int mode=0;mode<3;mode++) {
        double radians=degrees[angle]*M_PI/180, cs=degrees[angle]==90?0:cos(radians), sn=sin(radians);
        inputs[count][0][2]=1;
        inputs[count][1][0]=sn;inputs[count][1][2]=cs;
        inputs[count][2][0]=mode==2?0:-sn;
        inputs[count][2][1]=mode==2?1:0;
        inputs[count][2][2]=mode==2?0:cs;
        inputs[count][2][3]=mode!=1;
        expectedF[count]=fresnel(cs);
        expectedSun[count]=mode==0 && degrees[angle]<90 ? expectedF[count]:0;
        count++;
    }
    // Zero vectors use a finite fallback; antiparallel V/L never divides by 0.
    expectedF[count]=.04;expectedSun[count]=.04;inputs[count][2][3]=1;count++;
    inputs[count][0][2]=inputs[count][1][2]=1;inputs[count][2][2]=-1;inputs[count][2][3]=1;
    expectedF[count]=.04;expectedSun[count]=0;count++;
    id<MTLBuffer> in = [device newBufferWithBytes:inputs length:sizeof inputs options:MTLResourceStorageModeShared];
    id<MTLBuffer> out = [device newBufferWithLength:count*4*16 options:MTLResourceStorageModeShared];
    id<MTLCommandBuffer> cb=[queue commandBuffer];
    id<MTLComputeCommandEncoder> ce=[cb computeCommandEncoder];[ce setComputePipelineState:compute];
    [ce setBuffer:in offset:0 atIndex:0];[ce setBuffer:out offset:0 atIndex:1];
    [ce dispatchThreads:MTLSizeMake(count,1,1) threadsPerThreadgroup:MTLSizeMake(1,1,1)];[ce endEncoding];[cb commit];[cb waitUntilCompleted];
    if(cb.status!=MTLCommandBufferStatusCompleted)return 1;
    float (*values)[4][4] = out.contents;
    unsigned layer_failures=0, nonfinite=0;
    float max_energy=0,max_error=0;
    unsigned transmission_failures=0;
    for(unsigned i=0;i<count;i++) {
        int fail=0;
        double T=(1-expectedF[i])*(1-expectedF[i]);
        double expected[4]={T,expectedF[i]-expectedSun[i],expectedSun[i],expectedF[i]};
        for(int k=0;k<4;k++) {
            float delta=fabs(values[i][0][k]-expected[k]);max_error=fmaxf(max_error,delta);
            fail|=delta>2e-4;
        }
        for(int k=0;k<3;k++) {
            float energy=values[i][1][k];max_energy=fmaxf(max_energy,energy);
            fail|=energy>1.000002f || fabs(energy-(T+expectedF[i]))>2e-4;
            fail|=fabs(values[i][2][k]-T)>2e-4;
            transmission_failures+=fabs(values[i][2][k]-T)>.02;
            fail|=fabs(values[i][3][k]-expectedSun[i])>2e-4;
        }
        for(int row=0;row<4;row++)for(int k=0;k<4;k++)if(!isfinite(values[i][row][k])){nonfinite++;fail=1;}
        layer_failures+=fail;
    }
    float F0=values[0][0][3],F80=values[9][0][3];
    layer_failures+=fabsf(F0-.04f)>1e-6f || F80<=.35f;
    fprintf(stderr,"clearcoat_test: control=%s layer_failures=%u/%u nonfinite=%u F0=%.8f F80=%.8f max_energy=%.8f max_error=%.8g\n",control,layer_failures,count,nonfinite,F0,F80,max_energy,max_error);

    NSString *vertex=@"\nvertex In coat_vs(uint id [[vertex_id]],constant float4 &angle [[buffer(1)]]) {\n"
        "float2 p=float2((id<<1)&2,id&2);In o;o.pos=float4(p*2-1,.5,1);\n"
        "o.color0=float4(.2,.3,.4,1);o.color1=float4(.2,.3,.4,.7);if(angle.w>0){o.color0=float4(1);o.color1=float4(1);}if(angle.w==2)o.color1.rgb=float3(0);\n"
        "o.texcoord0=float4(0,0,1,0);o.texcoord1=float4(p,0,0);o.texcoord2=float4(.5,.5,.5,1);\n"
        "o.texcoord3=angle.z>0?float4(0,1,0,1):float4(-angle.x,0,angle.y,1);o.texcoord4=float4(0,0,1,0);o.texcoord5=float4(angle.x,0,angle.y,0);\n"
        "o.texcoord6=float4(p,0,.5);return o;}\n";
    id<MTLRenderPipelineState> pipelines[2];
    unsigned compiled=0;
    for(int state=0;state<2;state++)for(int variant=1;variant<=5;variant++)for(int hardware=0;hardware<2;hardware++) {
        NSString *source=[[defines(variant,hardware) stringByAppendingString:state?on:off] stringByAppendingString:vertex];
        id<MTLLibrary> paint=library(device,source);if(!paint)return 1;compiled++;
        if(variant==2 && !hardware) {
            MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];
            d.vertexFunction=[paint newFunctionWithName:@"coat_vs"];d.fragmentFunction=[paint newFunctionWithName:@"sm_main"];
            d.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA32Float;
            pipelines[state]=[device newRenderPipelineStateWithDescriptor:d error:&error];
            if(!pipelines[state])return 1;
        }
    }
    id<MTLTexture> textures[6];
    const float texels[6][4]={{.5,.6,.7,1},{.3,.4,.5,1},{.75,.75,.75,1},{1,1,1,1},{1,0,.5,1},{.5,.5,1,1}};
    const int slots[]={0,1,2,3,14,15};
    for(int i=0;i<6;i++) {
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:1 height:1 mipmapped:NO];
        if(i==1)d.textureType=MTLTextureTypeCube;
        if(i==4)d.textureType=MTLTextureType3D;
        d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageShaderRead;
        textures[i]=[device newTextureWithDescriptor:d];if(!textures[i])return 1;
        if(i==1)for(int face=0;face<6;face++)[textures[i] replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 slice:face withBytes:texels[i] bytesPerRow:16 bytesPerImage:16];
        else if(i==4)[textures[i] replaceRegion:MTLRegionMake3D(0,0,0,1,1,1) mipmapLevel:0 slice:0 withBytes:texels[i] bytesPerRow:16 bytesPerImage:16];
        else [textures[i] replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:texels[i] bytesPerRow:16];
    }
    MTLSamplerDescriptor *sd=[MTLSamplerDescriptor new];sd.minFilter=sd.magFilter=MTLSamplerMinMagFilterNearest;
    id<MTLSamplerState> sampler=[device newSamplerStateWithDescriptor:sd];
    MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:4 height:4 mipmapped:NO];
    td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageRenderTarget;
    id<MTLTexture> target=[device newTextureWithDescriptor:td];
    unsigned production_failures=0,pixels=0,flake_changes[2]={0};float production_error=0;
    float noflake[2][3][2][2][16][4]={0};
    const double angles[]={0,60,80};
    for(int nm=0;nm<2;nm++)for(int flake=0;flake<2;flake++)for(int angle=0;angle<3;angle++)for(int power=0;power<2;power++)for(int lightMode=0;lightMode<2;lightMode++)for(int gate=0;gate<2;gate++) {
        float outputs[2][16][4];
        float m[12][4]={{0}};
        float a[4]={(float)sin(angles[angle]*M_PI/180),(float)cos(angles[angle]*M_PI/180),lightMode,0};
        m[0][0]=power?8:0;m[0][1]=.5;m[0][2]=1;
        m[1][0]=.7;m[1][1]=.8;m[1][2]=.9;
        m[3][3]=nm;m[4][0]=m[4][1]=m[4][2]=.3;m[7][1]=1;
        m[8][0]=m[8][1]=m[8][2]=.25;m[10][0]=1;m[10][3]=gate;
        m[11][0]=flake;m[11][2]=1;m[11][3]=1;
        for(int state=0;state<2;state++) {
            draw(queue,pipelines[state],target,textures,slots,sampler,m,a,outputs[state]);
        }
        double F=fresnel(a[1]);
        for(int pixel=0;pixel<16;pixel++)for(int k=0;k<4;k++) {
            double expected=outputs[0][pixel][k];
            // Full Off radiance stays beneath two interfaces. The aligned
            // sun takes F; an unlit lobe leaves F for the smooth cube.
            if(gate && k<3) {
                expected=expected*(1-F)*(1-F)+F*(lightMode?texels[1][k]*(nm?.5:.7):m[1][k]);
            }
            float delta=fabs(outputs[1][pixel][k]-expected);production_error=fmaxf(production_error,delta);
            production_failures+=!isfinite(outputs[1][pixel][k]) || delta>3e-5;
            if(gate && k<3) {
                if(!flake)noflake[nm][angle][power][lightMode][pixel][k]=outputs[1][pixel][k];
                else flake_changes[nm]+=fabsf(outputs[1][pixel][k]-noflake[nm][angle][power][lightMode][pixel][k])>1e-6f;
            }
        }
        pixels+=16;
    }
    production_failures+=!flake_changes[0] || !flake_changes[1];
    fprintf(stderr,"clearcoat_test: compiled=%u production_failures=%u pixels=%u max_error=%.8g flake_changed=%u/%u\n",compiled,production_failures,pixels,production_error,flake_changes[0],flake_changes[1]);
    // Bound additions over the FULL production paint, including HDR hotspot.
    // An inherited value above 1 must survive; it is not coat-created energy.
    const float white[4]={1,1,1,1};
    [textures[0] replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:white bytesPerRow:16];
    for(int face=0;face<6;face++)[textures[1] replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 slice:face withBytes:white bytesPerRow:16 bytesPerImage:16];
    unsigned energy_failures=0,smooth_failures=0,t0_failures=0;float full_max=0,smooth_error=0;
    for(int nm=0;nm<2;nm++)for(int hot=0;hot<2;hot++)for(int angle=0;angle<3;angle++) {
        float m[12][4]={{0}},a[4]={(float)sin(angles[angle]*M_PI/180),(float)cos(angles[angle]*M_PI/180),0,1},output[16][4],baseline[16][4];
        m[0][0]=8;m[0][1]=hot;m[1][0]=m[1][1]=m[1][2]=1;
        m[3][3]=nm;m[4][0]=m[4][1]=m[4][2]=1;m[7][1]=1;
        m[8][0]=m[8][1]=m[8][2]=1;m[10][0]=m[10][3]=1;
        draw(queue,pipelines[0],target,textures,slots,sampler,m,a,baseline);
        draw(queue,pipelines[1],target,textures,slots,sampler,m,a,output);
        for(int i=0;i<16;i++)for(int k=0;k<3;k++) {
            full_max=fmaxf(full_max,output[i][k]);energy_failures+=!isfinite(output[i][k]) || output[i][k]>fmaxf(1,baseline[i][k])+3e-5f || output[i][k]<0;
        }
    }
    // A character-line fixture, not an inferred screen-pixel normal: N.H=1,
    // N.V=N.L=.7. Terms: diffuse, spec+hotspot, base env, full paint,
    // broad specular, coat only (0..5). Diffuse/spec draws include the sun
    // coat; subtract term 5 to recover their transmitted base contribution.
    // At grazing, black diffuse isolates the base reflection and added coat.
    unsigned highlight_failures=0,grazing_failures=0,diffuse_failures=0;float min_highlight=100,min_grazing=100;
    const float black[4]={0,0,0,1};
    for(int nm=0;nm<2;nm++)for(int grazing=0;grazing<2;grazing++)for(int term=0;term<6;term++) {
        float nv=grazing?.1f:.7f;
        float m[12][4]={{0}},a[4]={sqrtf(1-nv*nv),nv,grazing,term==0 || term==5?2:1},output[2][16][4];
        m[0][0]=8;m[0][1]=term==4?0:1;m[1][0]=m[1][1]=m[1][2]=1;
        m[3][3]=nm;m[4][0]=m[4][1]=m[4][2]=1;m[7][1]=1;
        m[8][0]=m[8][1]=m[8][2]=.25f;m[10][0]=m[10][3]=1;
        const float *diffuse=!grazing && (term==0 || term==3)?white:black;
        const float *cube=term==2 || term==3?white:black;
        [textures[0] replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:diffuse bytesPerRow:16];
        for(int face=0;face<6;face++)[textures[1] replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 slice:face withBytes:cube bytesPerRow:16 bytesPerImage:16];
        // Disable sun for the isolated environment term.
        if(term==2)m[1][0]=m[1][1]=m[1][2]=0;
        for(int state=0;state<2;state++)draw(queue,pipelines[state],target,textures,slots,sampler,m,a,output[state]);
        float off=output[0][0][0],on=output[1][0][0];
        if(!grazing && term==0) {
            double F=fresnel(nv),T=(1-F)*(1-F);
            for(int i=0;i<16;i++)for(int k=0;k<3;k++)
                diffuse_failures+=fabs((output[1][i][k]-F)/output[0][i][k]-T)>.02;
        }
        if(!grazing && term==1) {
            min_highlight=fminf(min_highlight,on/off);
            for(int i=0;i<16;i++)for(int k=0;k<3;k++)highlight_failures+=output[1][i][k]<.9f*output[0][i][k];
        }
        if(grazing && term==2) {
            min_grazing=fminf(min_grazing,on/off);
            for(int i=0;i<16;i++)for(int k=0;k<3;k++)grazing_failures+=output[1][i][k]+1e-6f<output[0][i][k];
        }
        fprintf(stderr,"clearcoat_terms: nm=%d nv=%.1f nl=%.1f nh=%g term=%d off=%.8f on=%.8f\n",nm,nv,grazing?0:.7,grazing?0.:1.,term,off,on);
    }
    fprintf(stderr,"clearcoat_test: highlight_failures=%u/96 min_ratio=%.8f grazing_failures=%u/96 min_ratio=%.8f transmission_failures=%u/69 diffuse_failures=%u/96\n",highlight_failures,min_highlight,grazing_failures,min_grazing,transmission_failures,diffuse_failures);
    // Restore the shared inputs for the reflection and t0 controls.
    [textures[0] replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:white bytesPerRow:16];
    // A directional cube and F=1 isolate the coat: base (including flake and
    // normal-map detail) has zero weight. A constant cube cannot expose Rt.
    for(int face=0;face<6;face++) {
        float color[4]={.1f*(face+1),.05f*(face+1),.025f*(face+1),1};
        [textures[1] replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 slice:face withBytes:color bytesPerRow:16 bytesPerImage:16];
    }
    for(int flake=0;flake<2;flake++)for(int detail=0;detail<2;detail++) {
        float normal[4]={detail?1:.5f,.5f,detail?.55f:1,1};
        [textures[5] replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:normal bytesPerRow:16];
        float m[12][4]={{0}},a[4]={1,0,1,0},output[16][4];
        m[0][0]=8;m[1][0]=m[1][1]=m[1][2]=1;m[3][3]=m[7][1]=1;
        m[8][0]=m[8][1]=m[8][2]=.25;m[10][0]=m[10][3]=1;
        m[11][0]=flake;m[11][2]=m[11][3]=1;
        draw(queue,pipelines[1],target,textures,slots,sampler,m,a,output);
        for(int i=0;i<16;i++)for(int k=0;k<3;k++) {
            float expected=.5f*.2f*powf(.5f,k),delta=fabsf(output[i][k]-expected);
            smooth_error=fmaxf(smooth_error,delta);smooth_failures+=!isfinite(output[i][k]) || delta>3e-5f;
        }
    }
    // Translate and compile the native t0 pair, then draw its production PS.
    NSString *t0off=[NSString stringWithContentsOfFile:@(argv[3]) encoding:NSUTF8StringEncoding error:nil];
    NSString *t0on=[NSString stringWithContentsOfFile:@(argv[4]) encoding:NSUTF8StringEncoding error:nil];
    NSString *t0vs=[NSString stringWithContentsOfFile:@(argv[5]) encoding:NSUTF8StringEncoding error:nil];
    id<MTLLibrary> nativeVS=library(device,t0vs);if(!nativeVS)return 1;
    NSString *scene=@"\nvertex SmPSStageIn coat_t0_vs(uint id [[vertex_id]],constant float4 &a [[buffer(1)]]) {\n"
        "float2 p=float2((id<<1)&2,id&2);SmPSStageIn o;o.pos=float4(p*2-1,.5,1);\n"
        "o.color0=float4(.2,.3,.4,1);o.color1=float4(.2,.3,.4,.7);if(a.w>0)o.color0.rgb*=8;\n"
        "o.texcoord0=float4(0,0,1,0);o.texcoord1=float4(p,0,0);o.texcoord2=float4(0,0,1,0);o.texcoord3=float4(0,0,1,0);\n"
        "EXTRA return o;}\n";
    id<MTLRenderPipelineState> t0pipelines[2];
    for(int state=0;state<2;state++) {
        NSString *extra=state?@"o.texcoord4=float4(0,0,1,0);o.texcoord5=float4(a.x,0,a.y,0);o.texcoord6=float4(-a.x,0,a.y,0);":@"";
        NSString *source=[(state?t0on:t0off) stringByAppendingString:[scene stringByReplacingOccurrencesOfString:@"EXTRA" withString:extra]];
        id<MTLLibrary> paint=library(device,source);if(!paint)return 1;
        MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];d.vertexFunction=[paint newFunctionWithName:@"coat_t0_vs"];
        d.fragmentFunction=[paint newFunctionWithName:@"sm_main"];d.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA32Float;
        t0pipelines[state]=[device newRenderPipelineStateWithDescriptor:d error:&error];if(!t0pipelines[state])return 1;
    }
    MTLRenderPipelineDescriptor *pair=[MTLRenderPipelineDescriptor new];
    pair.vertexFunction=[nativeVS newFunctionWithName:@"sm_main"];
    pair.fragmentFunction=[library(device,t0on) newFunctionWithName:@"sm_main"];
    pair.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA32Float;
    MTLVertexDescriptor *layout=[MTLVertexDescriptor vertexDescriptor];
    for(int i=0;i<4;i++){layout.attributes[i].format=MTLVertexFormatFloat4;layout.attributes[i].offset=16*i;layout.attributes[i].bufferIndex=0;}
    layout.layouts[0].stride=64;pair.vertexDescriptor=layout;
    if(![device newRenderPipelineStateWithDescriptor:pair error:&error]){fprintf(stderr,"clearcoat_test: native t0 link %s\n",error.localizedDescription.UTF8String);return 1;}
    const int t0slots[]={1,0,2,3,14,15};
    for(int hdr=0;hdr<2;hdr++)for(int angle=0;angle<3;angle++)for(int gate=0;gate<2;gate++) {
        float m[12][4]={{0}},a[4]={(float)sin(angles[angle]*M_PI/180),(float)cos(angles[angle]*M_PI/180),0,hdr},output[2][16][4];
        m[1][0]=.7;m[1][1]=.8;m[1][2]=.9;m[10][3]=gate;
        for(int state=0;state<2;state++)draw(queue,t0pipelines[state],target,textures,t0slots,sampler,m,a,output[state]);
        double F=fresnel(a[1]);
        for(int i=0;i<16;i++)for(int k=0;k<4;k++) {
            double expected=output[0][i][k];
            if(gate && k<3)expected=expected*(1-F)*(1-F)+F*m[1][k];
            t0_failures+=!isfinite(output[1][i][k]) || fabs(output[1][i][k]-expected)>3e-5;
        }
    }
    fprintf(stderr,"clearcoat_test: full_energy_failures=%u/576 max=%.8f smooth_failures=%u/192 max_error=%.8g t0_failures=%u/768\n",energy_failures,full_max,smooth_failures,smooth_error,t0_failures);
    return layer_failures || production_failures || energy_failures || smooth_failures || t0_failures || highlight_failures || grazing_failures || transmission_failures || diffuse_failures ? 1:0;
} }
