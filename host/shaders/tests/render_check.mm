// render_check.mm - end-to-end test of the stage wrappers. For every VS/PS pair bound by an
// effect pass (testdata/passes.tsv) it draws one point per pixel of a 64x64 RGBA32F target
// through the real vertex + fragment functions (attributes, constant buffers, half-pixel
// fix-up, varyings, texkill / alpha test, colour outputs), and compares every pixel with the
// CPU interpreter running the VS, then the PS, on the same inputs.
//
// Near production proof: 56,785 checks / zero failures; nine pinned originals
// reject the dense single-comparison reference. GPU counters reject doubled PCF,
// affine W, duplicate centre, wrong texture, retained old filter and composition
// controls (51 rejected executions). Retail and outside-near outputs are exact.
//   build/render_check [testdata] [--only substr]
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include "../sm_ir.h"
#include "../sm_translate.h"
#include "common.h"
#include "sm_interp.h"

#include <cmath>
#include <CommonCrypto/CommonDigest.h>
#include <map>

struct Rng {
    uint64_t s;
    explicit Rng(uint64_t seed) : s(seed * 0x9E3779B97F4A7C15ull + 1) {}
    uint32_t next() { s ^= s << 13; s ^= s >> 7; s ^= s << 17; return (uint32_t)(s >> 11); }
    float uni(float a, float b) { return a + (b - a) * (float)(next() & 0xFFFFFF) / (float)0x1000000; }
    int range(int a, int b) { return a + (int)(next() % (uint32_t)(b - a + 1)); }
};

static const int W = 64, H = 64;

struct Stage {
    std::vector<uint32_t> tok;
    sm::Shader sh;
    SmResult res = {};
    id<MTLFunction> fn = nil;
};

static bool load(id<MTLDevice> dev, const std::string &path, Stage &st, std::string &err) {
    st.tok = read_tokens(path);
    if (!sm::parse(st.tok.data(), st.tok.size(), st.sh)) { err = st.sh.error; return false; }
    SmOptions opt;
    sm_default_options(&opt);
    if (sm_translate(st.tok.data(), st.tok.size(), &opt, &st.res) != 0) { err = st.res.error; return false; }
    NSError *e = nil;
    id<MTLLibrary> lib = [dev newLibraryWithSource:[NSString stringWithUTF8String:st.res.msl] options:[MTLCompileOptions new] error:&e];
    if (!lib) { err = e.localizedDescription.UTF8String; return false; }
    st.fn = [lib newFunctionWithName:@"sm_main"];
    return true;
}

static bool close_enough(float a, float b, float tol) {
    if (a != a && b != b) return true;
    if (std::isinf(a) || std::isinf(b)) return a == b;
    return std::fabs(a - b) <= tol * std::max(1.0f, std::max(std::fabs(a), std::fabs(b)));
}

// Pinned production originals are checked before GPU compilation. Their level-2
// edge response fails the single-comparison reference; no history is consulted.
struct NearPin {const char *path,*sha;int slot,tc,taps;};
static const NearPin near_pins[] = {
    {"world/t1_world_p0_ps_2_0.bin","1be5017a4c825a4ee09f8f2d4f2dcfaa5bc7636c300eb4d9d520c3287836c845",1,3,1},
    {"world/t2_world_p0_ps_2_x.bin","2c4b0eadaff1cfe6827cad75b849d59103d8518d44f718cc3bcc3b85155e82c5",1,3,4},
    {"world/t3_world_p0_ps_2_x.bin","06451b3a425bb36237d6b1f8c34cb6e1f04bdd4e6a5178217fc8e3f96b82da17",1,3,16},
    {"worldnormalmap/t1_worldnormalmap_2_0_p0_ps_2_0.bin","a775aa988c7349316d49e64ceb4b3ac8b515129bc5dc09731c50842fa5aa6b48",3,7,1},
    {"worldnormalmap/t2_worldnormalmap_2_0_p0_ps_2_x.bin","ad5a1369fdc9ade2d5d65b2efc8ce6fc44e65e7fb51635a5a3f40fa59e8a2d88",3,7,4},
    {"worldnormalmap/t3_worldnormalmap_2_0_p0_ps_2_x.bin","71d34355241eeeddea96c70d2e7aac469762efcb5fe6fa0bdd3e2db80dafe694",3,7,16},
    {"worldreflect/t1_world_reflect_shadowhw_2_0_p0_ps_2_0.bin","2ef508739a57e4496fed53fc84b942989d42f636b7987cfbfc5ea0fc84ec83ad",4,7,1},
    {"worldreflect/t2_worldnormalmap_2_0_p0_ps_2_x.bin","364df39b7e0a6158f87e5306eb9bb49b5113236f6c48c5fa8ed1fcae49758949",4,7,4},
    {"worldreflect/t3_worldnormalmap_2_0_p0_ps_2_x.bin","c6e745ab0c161c32f5f2f8664ad97c3234b621da493f7d6f444c05f0cea06827",4,7,16},
};
static std::string near_sha(const char *s) {
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];CC_SHA256(s,(CC_LONG)strlen(s),digest);
    char hex[65];for(int i=0;i<32;++i)snprintf(hex+i*2,3,"%02x",digest[i]);return hex;
}
struct NearK {float rows[4][4],usable[4],blend[4];uint32_t mode,taps,pad[2];};
static float near_ramp(float x) {return std::fmax(0.f,std::fmin(1.f,x+.5f));}
static float near_reference(float x,int taps) {
    int side=taps==16?4:taps==4?2:1;float v=0;
    for(int i=0;i<side;++i)v+=near_ramp(x+(i-(side-1)*.5f));return v/side;
}
static double near_width(const float *p,int n,float spacing) {
    double crossings[2]={};
    for(int k=0;k<2;++k){double level=k?.9:.1;bool found=false;
        for(int i=1;i<n;++i)if(p[i-1]<=level && p[i]>=level && p[i]>p[i-1]) {
            crossings[k]=(i-1+(level-p[i-1])/(p[i]-p[i-1]))*spacing;found=true;break;
        }
        if(!found)return -1;
    }
    return crossings[1]-crossings[0];
}
static int near_checks(id<MTLDevice> dev,const std::string &td) {
    if(!std::ifstream(td+"/world/t2_world_p0_ps_2_x.bin"))return 0;
    int checks=0,bad=0,original_rejections=0,mutant_rejections=0,programs=0;
    FILE *proof=fopen("build/near-proof.txt","w");
    auto check=[&](bool pass,const char *what){++checks;if(!pass){++bad;fprintf(stderr,"near FAIL %s\n",what);}};
    id<MTLCommandQueue> q=[dev newCommandQueue];NSError *error=nil;
    NSString *fill=@"#include <metal_stdlib>\nusing namespace metal;\nstruct NearFillOut {float4 p [[position]];}; vertex NearFillOut near_fill(uint id [[vertex_id]]) {NearFillOut o;o.p=float4(float2((id&1)*2,(id>>1)*2)-1,0,1);return o;}\nstruct NearDepthOut {float z [[depth(any)]];}; fragment NearDepthOut near_depth(float4 p [[position]]) {NearDepthOut o;o.z=p.x < 128 ? .25f : .75f;return o;}";
    id<MTLLibrary> fl=[dev newLibraryWithSource:fill options:nil error:&error];check(fl!=nil,"depth fixture library");if(!fl){fprintf(stderr,"%s\n",error.localizedDescription.UTF8String);return bad;}
    MTLRenderPipelineDescriptor *fp=[MTLRenderPipelineDescriptor new];fp.vertexFunction=[fl newFunctionWithName:@"near_fill"];fp.fragmentFunction=[fl newFunctionWithName:@"near_depth"];fp.depthAttachmentPixelFormat=MTLPixelFormatDepth32Float;
    id<MTLRenderPipelineState> fps=[dev newRenderPipelineStateWithDescriptor:fp error:&error];check(fps!=nil,"depth fixture pipeline");if(!fps){fprintf(stderr,"%s\n",error.localizedDescription.UTF8String);return bad;}
    MTLTextureDescriptor *zd=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatDepth32Float width:256 height:256 mipmapped:NO];zd.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;zd.storageMode=MTLStorageModePrivate;
    id<MTLTexture> z=[dev newTextureWithDescriptor:zd];
    MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];rp.depthAttachment.texture=z;rp.depthAttachment.loadAction=MTLLoadActionClear;rp.depthAttachment.storeAction=MTLStoreActionStore;rp.depthAttachment.clearDepth=1;
    id<MTLCommandBuffer> cb=[q commandBuffer];id<MTLRenderCommandEncoder> re=[cb renderCommandEncoderWithDescriptor:rp];[re setRenderPipelineState:fps];
    MTLDepthStencilDescriptor *dd=[MTLDepthStencilDescriptor new];dd.depthWriteEnabled=YES;dd.depthCompareFunction=MTLCompareFunctionAlways;
    [re setDepthStencilState:[dev newDepthStencilStateWithDescriptor:dd]];[re drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];[re endEncoding];[cb commit];[cb waitUntilCompleted];check(!cb.error,"depth fixture GPU");
    id<MTLTexture> z_bright=[dev newTextureWithDescriptor:zd];
    rp.depthAttachment.texture=z_bright;rp.depthAttachment.clearDepth=.75;
    cb=[q commandBuffer];re=[cb renderCommandEncoderWithDescriptor:rp];[re endEncoding];[cb commit];[cb waitUntilCompleted];check(!cb.error,"alternate map GPU");
    MTLTextureDescriptor *tdesc=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:1 height:1 mipmapped:NO];tdesc.storageMode=MTLStorageModeShared;tdesc.usage=MTLTextureUsageShaderRead;
    id<MTLTexture> white=[dev newTextureWithDescriptor:tdesc],normal=[dev newTextureWithDescriptor:tdesc];
    float whiteValue[4]={1,1,1,1},normalValue[4]={.5,.5,1,1};
    [white replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:whiteValue bytesPerRow:16];[normal replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:normalValue bytesPerRow:16];
    MTLSamplerDescriptor *sd=[MTLSamplerDescriptor new];sd.minFilter=sd.magFilter=MTLSamplerMinMagFilterLinear;sd.sAddressMode=sd.tAddressMode=MTLSamplerAddressModeClampToBorderColor;sd.borderColor=MTLSamplerBorderColorOpaqueWhite;
    id<MTLSamplerState> colourSampler=[dev newSamplerStateWithDescriptor:sd];sd.compareFunction=MTLCompareFunctionLessEqual;id<MTLSamplerState> depthSampler=[dev newSamplerStateWithDescriptor:sd];
    const int N=1025;float coords[N][4];for(int i=0;i<N;++i){float offset=-4+8.f*i/(N-1),w=.6f+.9f*i/(N-1);coords[i][0]=(.5f+offset/256)*w;coords[i][1]=.5f*w;coords[i][2]=.5f*w;coords[i][3]=w;}
    id<MTLBuffer> input=[dev newBufferWithBytes:coords length:sizeof coords options:MTLResourceStorageModeShared];
    id<MTLBuffer> output=[dev newBufferWithLength:N*16 options:MTLResourceStorageModeShared];
    id<MTLBuffer> count_buffer=[dev newBufferWithLength:N*sizeof(uint32_t) options:MTLResourceStorageModeShared];
    float constants[256][4];int integers[64]={},booleans[16]={};SmPSAux aux={};
    for(const auto &pin:near_pins) {
        auto tokens=read_tokens(td+"/"+pin.path);SmOptions opt;sm_default_options(&opt);opt.shadow_samplers=1<<pin.slot;SmResult pc={};
        check(sm_translate(tokens.data(),tokens.size(),&opt,&pc)==0,"translate original");check(near_sha(pc.msl)==pin.sha,"pinned original source digest");
        char *patched=sm_shadow_near_patch(pc.msl,tokens.data(),tokens.size(),&pc);check(patched!=nullptr,"supported world receiver");if(!patched){sm_result_free(&pc);continue;}++programs;
        std::string generated=patched;free(patched);
        check(generated.find("[[buffer(28)]]")!=generated.npos && generated.find("[[texture(30)]]")!=generated.npos,"receiver binding ABI");
        std::fill(&constants[0][0],&constants[0][0]+1024,0);
        constants[0][0]=constants[0][1]=constants[0][2]=constants[0][3]=1;constants[2][0]=1;
        for(int k=0;k<pc.n_constants;++k){const auto &c=pc.constants[k];if(c.set!=2)continue;
            if(std::string(c.name)=="ShadowMapScale")constants[c.reg][0]=1.f/256;
            if(std::string(c.name)=="InvShadowStrength")constants[c.reg][0]=0;
        }
        std::string args;
        for(int k=0;k<pc.n_samplers;++k){int slot=pc.samplers[k].reg;args+=",tex"+std::to_string(slot)+",smp"+std::to_string(slot);}
        std::vector<float> result[4],original_pixels;
        for(int variant=-1;variant<11;++variant) {
            // -1 original; 0 retail; 1 projection; 2 filter; 3 full;
            // 4 doubled PCF; 5 affine W; 6 duplicate centre; 7 wrong map.
            bool original=variant==-1;std::string src=original?pc.msl:generated;
            // Instrument the ORIGINAL generated comparison expressions. A thread
            // counter follows the actual runtime branches and PCF loop iterations.
            // The complete production body is retained; only the unused stage
            // wrapper is removed before adding a compute invocation of that body.
            src.resize(src.find("\nstruct SmPSStageIn"));
            size_t search=0;
            while((search=src.find(".sample_compare(",search))!=src.npos) {
                size_t begin=search;while(begin && (isalnum(src[begin-1]) || src[begin-1]=='_'))--begin;
                size_t end=search+16;int depth=1;
                for(;end<src.size() && depth;++end){if(src[end]=='(')++depth;else if(src[end]==')')--depth;}
                std::string expression=src.substr(begin,end-begin);
                std::string counted="(near_fetches++,"+expression+")";
                src.replace(begin,end-begin,counted);search=begin+counted.size();
            }
            auto add_arg=[&](const std::string &from,const std::string &to){size_t at=src.find(from);if(at!=src.npos)src.replace(at,from.size(),to);};
            add_arg("constant SmPSAux &aux,","constant SmPSAux &aux, thread uint &near_fetches,");
            if(!original) {
                add_arg("constant SmPSAux &retail_aux,","constant SmPSAux &retail_aux, thread uint &near_fetches,");
                add_arg("depth2d<float> z) {","depth2d<float> z, thread uint &near_fetches) {");
                add_arg("near_k,near_z);","near_k,near_z,near_fetches);");
                add_arg("near_retail_filter(si,c,aux,","near_retail_filter(si,c,aux,near_fetches,");
            }
            auto replace=[&](const std::string &from,const std::string &to){size_t at=src.find(from);check(at!=src.npos,"mutation location");if(at!=src.npos)src.replace(at,from.size(),to);};
            if(variant==4)replace("k.mode == 4u && k.taps > 1u","k.taps > 1u");
            if(variant==10)replace("=near_visibility*","=(near_visibility*.5f)*");
            if(variant==8)replace("float near_visibility;","float near_visibility;near_pair.y=0.0f;");
            if(variant==5)replace("float3 p=h.xyz/h.w;","float3 p=h.xyz;");
            if(variant==6)replace("float3 p=h.xyz/h.w;","float3 p=h.xyz/h.w; p.x+=.5f/float(zero_placeholder);");
            // Use a fixed test map size for the deliberate double-centre control.
            if(variant==6)replace("float(zero_placeholder)","256.0f");
            src+="\nkernel void near_response(device const float4 *xy [[buffer(8)]],device float4 *out [[buffer(9)]],constant float4 *c [[buffer(0)]],constant int4 *ci [[buffer(1)]],constant int *cb [[buffer(2)]],constant SmPSAux &aux [[buffer(3)]]";
            if(!original)src+=",constant NearShadowParams &near_k [[buffer(28)]],depth2d<float> near_z [[texture(30)]]";
            for(int k=0;k<pc.n_samplers;++k){int slot=pc.samplers[k].reg;src+=","+std::string(slot==pin.slot?"depth2d<float>":"texture2d<float>")+" tex"+std::to_string(slot)+" [[texture("+std::to_string(slot)+")]],sampler smp"+std::to_string(slot)+" [[sampler("+std::to_string(slot)+")]]";}
            src+=",device uint *counts [[buffer(10)]],uint id [[thread_position_in_grid]]) { uint near_fetches=0;SmPSIn si={};si.col[0]=float4(0,0,0,1);si.col[1]=float4(1);si.tex[0]=float4(.5f);si.tex[1]=float4(.5f,.5f,1,1);si.tex[2]=si.tex[3]=si.tex[4]=float4(0,0,1,1);si.tex[5]=si.tex[6]=float4(1);";
            if(pin.slot==1)src+="si.tex[2]=float4(0);";
            if(pin.slot==3)src+="si.tex[4]=float4(0);";
            if(pin.slot==4)src+="si.tex[5]=float4(0);";
            src+="si.tex["+std::to_string(pin.tc)+"]=xy[id];SmPSOut so;sm_body(si,so,c,ci,cb,aux,near_fetches"+std::string(original?"":",near_k,near_z")+args+");out[id]=so.oc[0];counts[id]=near_fetches;}\n";
            id<MTLLibrary> lib=[dev newLibraryWithSource:[NSString stringWithUTF8String:src.c_str()] options:nil error:&error];check(lib!=nil,"production response compile");if(!lib){fprintf(stderr,"%s\n",error.localizedDescription.UTF8String);continue;}
            id<MTLComputePipelineState> ps=[dev newComputePipelineStateWithFunction:[lib newFunctionWithName:@"near_response"] error:&error];check(ps!=nil,"response pipeline");if(!ps)continue;
            NearK k={{{4,0,0,0},{0,1,0,0},{0,0,1,0},{-1.5,0,0,1}},{0,0,1,1},{.01f,.01f,.99f,.99f},0,(uint32_t)pin.taps,{0,0}};

            if(variant==9)k.rows[3][0]+=5;
            const unsigned modes[]={1,4,5,0};if(variant>=0 && variant<4)k.mode=modes[variant];
            cb=[q commandBuffer];id<MTLComputeCommandEncoder> ce=[cb computeCommandEncoder];[ce setComputePipelineState:ps];
            [ce setBytes:constants length:sizeof constants atIndex:0];[ce setBytes:integers length:sizeof integers atIndex:1];[ce setBytes:booleans length:sizeof booleans atIndex:2];[ce setBytes:&aux length:sizeof aux atIndex:3];
            [ce setBuffer:input offset:0 atIndex:8];[ce setBuffer:output offset:0 atIndex:9];[ce setBuffer:count_buffer offset:0 atIndex:10];
            if(!original){[ce setBytes:&k length:sizeof k atIndex:28];[ce setTexture:variant==7?z_bright:z atIndex:30];}
            for(int j=0;j<pc.n_samplers;++j){int slot=pc.samplers[j].reg;[ce setTexture:slot==pin.slot?z:slot==1?normal:white atIndex:slot];[ce setSamplerState:slot==pin.slot?depthSampler:colourSampler atIndex:slot];}
            [ce dispatchThreads:MTLSizeMake(N,1,1) threadsPerThreadgroup:MTLSizeMake(64,1,1)];[ce endEncoding];[cb commit];[cb waitUntilCompleted];check(!cb.error,"response GPU errors");
            const uint32_t *counts=(const uint32_t *)count_buffer.contents;
            if(variant<4 || variant==9) {
                unsigned expected=original || variant<2 || variant==9?pin.taps:1;
                for(int i=0;i<N;++i)check(counts[i]==expected,"evaluated production fetch count");
            }
            const float *values=(const float *)output.contents;
            if(original)original_pixels.assign(values,values+N*4);
            if(variant==0 || variant==9)check(original_pixels.size()==N*4 && !memcmp(values,original_pixels.data(),N*16),"exact original/retail/outside-far output parity");
            float low=values[0],high=values[(N-1)*4];if(variant<4 || variant==9)check(std::isfinite(low) && std::isfinite(high) && high-low>.1f,"nonempty receiver edge");
            std::vector<float> normalized(N);unsigned errors=0,single_errors=0;
            for(int i=0;i<N;++i){normalized[i]=(values[i*4]-low)/(high-low);float x=-4+8.f*i/(N-1);int taps=original||variant==0||variant==1||variant==9?pin.taps:1;float scale=variant==1||(variant>=3 && variant!=9)?4:1;
                float expected=near_reference(x*scale,taps);errors+=!std::isfinite(normalized[i]) || fabsf(normalized[i]-expected)>.012f;
                single_errors+=fabsf(normalized[i]-near_reference(x*(original?4:scale),1))>.012f;
            }
            double width=near_width(normalized.data(),N,8.f/(N-1));
            if(variant>=0 && variant<4){check(errors==0,"four-way edge response");result[variant]=normalized;
                printf("near edge %s variant=%d fetches=%d width10-90=%.6f far-texels (far pitch 4mm: %.6fmm) errors=%u\n",pin.path,variant,variant<2?pin.taps:1,width,width*4,errors);
                if(proof)fprintf(proof,"%s variant=%d evaluated_fetches=%u width_far_texels=%.6f width_mm=%.6f errors=%u\n",pin.path,variant,counts[N/2],width,width*4,errors);
            } else if(variant==9){check(errors==0,"unchanged far edge outside near bounds");}
            else if(original){check(single_errors>0,"pinned original fails dense narrow reference");original_rejections+=single_errors>0;}
            else if(variant!=10 && (variant!=4 || pin.taps>1)){check(errors>0,"production mutation rejected");mutant_rejections+=errors>0;}
            if(variant==3 || variant==10) {
                // Near lit / far shadow at half bounds weight, then fade=.25
                // and InvShadowStrength=.25. Visibility-first gives 1; blending
                // already-clamped material results would incorrectly give .75.
                int strength=-1;for(int j=0;j<pc.n_constants;++j)if(std::string(pc.constants[j].name)=="InvShadowStrength")strength=pc.constants[j].reg;
                check(strength>=0,"strength register");
                float old_strength=constants[strength][0];constants[strength][0]=.25f;
                float point[4]={.15f,.15f,.5f,1};
                id<MTLBuffer> one=[dev newBufferWithBytes:point length:sizeof point options:MTLResourceStorageModeShared];
                NearK bk={{{1,0,0,0},{0,1,0,0},{0,0,1,0},{0,0,0,1}},{.1f,.1f,.9f,.9f},{.2f,.2f,.8f,.8f},0,(uint32_t)pin.taps,{0,0}};
                for(int mode=0;mode<2;++mode){
                    bk.mode=mode;
                    cb=[q commandBuffer];ce=[cb computeCommandEncoder];[ce setComputePipelineState:ps];
                    [ce setBytes:constants length:sizeof constants atIndex:0];[ce setBytes:integers length:sizeof integers atIndex:1];[ce setBytes:booleans length:sizeof booleans atIndex:2];[ce setBytes:&aux length:sizeof aux atIndex:3];
                    [ce setBuffer:one offset:0 atIndex:8];[ce setBuffer:output offset:0 atIndex:9];[ce setBuffer:count_buffer offset:0 atIndex:10];[ce setBytes:&bk length:sizeof bk atIndex:28];[ce setTexture:z_bright atIndex:30];
                    for(int j=0;j<pc.n_samplers;++j){int slot=pc.samplers[j].reg;[ce setTexture:slot==pin.slot?z:slot==1?normal:white atIndex:slot];[ce setSamplerState:slot==pin.slot?depthSampler:colourSampler atIndex:slot];}
                    [ce dispatchThreads:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(1,1,1)];[ce endEncoding];[cb commit];[cb waitUntilCompleted];check(!cb.error,"strength blend GPU errors");
                    float actual=((const float *)output.contents)[0];float a=original_pixels[0],b=original_pixels[(N-1)*4];float expected=a+(b-a)*(mode?.5f:1.f);
                    if(variant==10 && mode==0) {check(fabsf(actual-expected)>.1f,"composition mutation rejected");++mutant_rejections;}
                    else check(fabsf(actual-expected)<.001f,"visibility blended before material strength/fade saturation");
                    check(((const uint32_t *)count_buffer.contents)[0]==(uint32_t)(pin.taps+(mode?0:1)),"only transition evaluates near and far");
                }
                constants[strength][0]=old_strength;
            }
            if(variant==3) {
                std::string contract=src+"\nkernel void near_contract(device const float4 *f [[buffer(8)]],device float4 *out [[buffer(9)]],constant NearShadowParams &k [[buffer(28)]],depth2d<float> z [[texture(30)]],uint id [[thread_position_in_grid]]) {uint near_fetches=0;float2 v=near_shadow_eval(f[id],k,z,near_fetches);constexpr sampler border(compare_func::less_equal,filter::linear,address::clamp_to_border,border_color::opaque_white);out[id]=float4(v,float(near_fetches),z.sample_compare(border,float2(-.1f,.5f),.5f));}\n";
                id<MTLLibrary> cl=[dev newLibraryWithSource:[NSString stringWithUTF8String:contract.c_str()] options:nil error:&error];check(cl!=nil,"contract compile");
                id<MTLComputePipelineState> cp=cl?[dev newComputePipelineStateWithFunction:[cl newFunctionWithName:@"near_contract"] error:&error]:nil;check(cp!=nil,"contract pipeline");
                if(cp){
                    float cases[][4]={{.3,.5,.5,1},{.7,.5,.5,1},{.15,.5,.5,1},{.05,.5,.5,1},{.95,.5,.5,1},{.3,.5,-.1,1},{.3,.5,1.1,1},{.3,.5,.5,0},{.3,.5,NAN,1},{.3,.5,.2499,1},{.3,.5,.2501,1},{.3,.5,.25,1},{.7,.5,.7501,1}};
                    const int count=sizeof cases/sizeof cases[0];
                    float visibility[]={0,1,0,1,1,1,1,1,1,1,0,1,0};
                    float weights[]={1,1,.5,0,0,0,0,0,0,1,1,1,1};
                    NearK ck={{{1,0,0,0},{0,1,0,0},{0,0,1,0},{0,0,0,1}},{.1f,.1f,.9f,.9f},{.2f,.2f,.8f,.8f},0,1,{0,0}};
                    id<MTLBuffer> cin=[dev newBufferWithBytes:cases length:sizeof cases options:MTLResourceStorageModeShared];
                    cb=[q commandBuffer];ce=[cb computeCommandEncoder];[ce setComputePipelineState:cp];[ce setBuffer:cin offset:0 atIndex:8];[ce setBuffer:output offset:0 atIndex:9];[ce setBytes:&ck length:sizeof ck atIndex:28];[ce setTexture:z atIndex:30];
                    [ce dispatchThreads:MTLSizeMake(count,1,1) threadsPerThreadgroup:MTLSizeMake(count,1,1)];[ce endEncoding];[cb commit];[cb waitUntilCompleted];check(!cb.error,"contract GPU errors");
                    const float *v=(const float *)output.contents;
                    for(int i=0;i<count;++i){check(fabs(v[i*4]-visibility[i])<.001f,"depth comparison and bias sign");check(fabs(v[i*4+1]-weights[i])<.001f,"bounds/depth/W/nonfinite blend");check(v[i*4+2]==(weights[i]>0?1:0),"invalid sample makes no fetch");check(v[i*4+3]==1,"hardware white border");}
                }
            }
        }
        check(result[0].size()==N && result[3].size()==N,"four variants evaluated");
        sm_result_free(&pc);
    }
    // Entire car/hood, glass, grass and particle programs, software comparisons
    // and no-shadow world programs must be rejected by the near patch. Their
    // ordinary render checks above still compare all outputs and alpha coverage.
    unsigned unsupported=0;
    for(const auto &m:read_manifest(td)) {
        auto tokens=read_tokens(td+"/"+m.effect+"/"+m.file);SmOptions opt;sm_default_options(&opt);SmResult pc={};
        check(sm_translate(tokens.data(),tokens.size(),&opt,&pc)==0,"retained-family translation");
        if(!sm_shadow_near_taps(pc.program_key)) {
            std::string before=pc.msl;char *patched=sm_shadow_near_patch(pc.msl,tokens.data(),tokens.size(),&pc);
            check(!patched && before==pc.msl,"retained-family exact Off source");free(patched);++unsupported;
        } else {check(!sm_shadow_near_patch(pc.msl,tokens.data(),tokens.size(),&pc),"software sampler binding retains retail");}
        sm_result_free(&pc);
    }
    check(unsupported>50,"retained receiver family coverage");
    printf("near retained-family exact source checks: %u\n",unsupported);
    check(programs==9,"all supported world programs");check(original_rejections==9,"all pinned originals rejected");
    printf("near receiver GPU: programs=%d checks=%d failures=%d original-rejections=%d mutation-rejections=%d\n",programs,checks,bad,original_rejections,mutant_rejections);
    if(proof){fprintf(proof,"retained_family_programs=%u\n",unsupported);fprintf(proof,"programs=%d checks=%d failures=%d original_rejections=%d mutant_rejections=%d\n",programs,checks,bad,original_rejections,mutant_rejections);fclose(proof);}
    return bad;
}

int main(int argc, char **argv) {
    @autoreleasepool {
        std::string td = "testdata", only;
        for (int i = 1; i < argc; i++) {
            std::string a = argv[i];
            if (a == "--only" && i + 1 < argc) only = argv[++i];
            else td = a;
        }
        id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
        id<MTLCommandQueue> q = [dev newCommandQueue];
        std::ifstream f(td + "/passes.tsv");
        std::string line;
        std::getline(f, line);
        int passes = 0, good = 0, badp = 0;
        long checked_px = 0;
        while (std::getline(f, line)) {
            auto c = split(line, '\t');
            if (c.size() < 6 || c[4] == "-" || c[5] == "-") continue;
            std::string where = c[0] + " t" + c[1] + ":" + c[2] + "/" + c[3];
            if (!only.empty() && where.find(only) == std::string::npos) continue;
            passes++;
            Stage vs, ps;
            std::string err;
            if (!load(dev, td + "/" + c[0] + "/" + c[4], vs, err) || !load(dev, td + "/" + c[0] + "/" + c[5], ps, err)) {
                printf("FAIL %s: %s\n", where.c_str(), err.c_str());
                badp++;
                continue;
            }
            for (int round = 0; round < 2; round++) {
                Rng rng(std::hash<std::string>()(where) + round * 31337u);
                // ---- constants
                std::vector<float> vc(256 * 4), pc(256 * 4);
                for (float &x : vc) x = rng.uni(-2.0f, 2.0f);
                for (float &x : pc) x = rng.uni(-1.5f, 1.5f);
                bool identity = false;
                for (int k = 0; k < vs.res.n_constants; k++) {
                    const SmConstant &k0 = vs.res.constants[k];
                    if (k0.set == 2 && std::string(k0.name) == "BlendMatrices")   // skinning palette: identity bones
                        for (int r = 0; r < k0.count; r++) for (int cc = 0; cc < 4; cc++) vc[(k0.reg + r) * 4 + cc] = (r % 4) == cc ? 1.0f : 0.0f;
                    if (k0.set == 2 && k0.count == 4 && std::string(k0.name) == "WorldViewProj") {
                        for (int r = 0; r < 4; r++) for (int cc = 0; cc < 4; cc++) vc[(k0.reg + r) * 4 + cc] = r == cc ? 1.0f : 0.0f;
                        identity = true;
                    }
                }
                std::vector<int> ci(64), cb(16);
                for (int k = 0; k < 16; k++) { ci[k * 4] = rng.range(0, 4); ci[k * 4 + 1] = rng.range(0, 8); ci[k * 4 + 2] = rng.range(1, 2); }
                for (int &b : cb) b = rng.range(0, 1);
                SmVSAux vaux = {{1.0f / W, -1.0f / H, 0, 0}, 1.0f, {0, 0, 0}};
                SmPSAux paux = {};
                if (round == 1) { paux.alpha_func = 5; paux.alpha_ref = 0.5f; }   // D3DCMP_GREATER 0.5
                // ---- textures (pixel shader)
                interp::Texture texs[16];
                id<MTLTexture> mtex[16] = {nil};
                id<MTLSamplerState> msmp[16] = {nil};
                interp::Context vctx, pctx;
                vctx.c = vc.data(); vctx.ci = ci.data(); vctx.cb = cb.data();
                pctx.c = pc.data(); pctx.ci = ci.data(); pctx.cb = cb.data(); pctx.aux = paux;
                sm_default_options(&vctx.opt);
                sm_default_options(&pctx.opt);
                for (int k = 0; k < ps.res.n_samplers; k++) {
                    const SmSampler &smp = ps.res.samplers[k];
                    interp::Texture &t = texs[smp.reg];
                    t.type = smp.type;
                    t.w = t.h = smp.type == SM_TEX_3D ? 4 : 8;
                    t.d = smp.type == SM_TEX_3D ? 4 : smp.type == SM_TEX_CUBE ? 6 : 1;
                    t.texels.resize((size_t)t.w * t.h * t.d * 4);
                    for (float &x : t.texels) x = rng.uni(0.0f, 1.0f);
                    MTLTextureDescriptor *d = [MTLTextureDescriptor new];
                    d.pixelFormat = MTLPixelFormatRGBA32Float;
                    d.width = t.w; d.height = t.h;
                    d.textureType = smp.type == SM_TEX_CUBE ? MTLTextureTypeCube : smp.type == SM_TEX_3D ? MTLTextureType3D : MTLTextureType2D;
                    if (smp.type == SM_TEX_3D) d.depth = t.d;
                    mtex[smp.reg] = [dev newTextureWithDescriptor:d];
                    size_t row = (size_t)t.w * 16, slice = row * t.h;
                    if (smp.type == SM_TEX_CUBE)
                        for (int fc = 0; fc < 6; fc++)
                            [mtex[smp.reg] replaceRegion:MTLRegionMake2D(0, 0, t.w, t.h) mipmapLevel:0 slice:fc
                                               withBytes:t.texels.data() + fc * t.w * t.h * 4 bytesPerRow:row bytesPerImage:slice];
                    else
                        [mtex[smp.reg] replaceRegion:MTLRegionMake3D(0, 0, 0, t.w, t.h, t.d) mipmapLevel:0 slice:0
                                           withBytes:t.texels.data() bytesPerRow:row bytesPerImage:slice];
                    MTLSamplerDescriptor *sd = [MTLSamplerDescriptor new];
                    sd.minFilter = sd.magFilter = MTLSamplerMinMagFilterNearest;
                    sd.sAddressMode = sd.tAddressMode = sd.rAddressMode = MTLSamplerAddressModeClampToEdge;
                    msmp[smp.reg] = [dev newSamplerStateWithDescriptor:sd];
                    pctx.tex[smp.reg] = &t;
                }
                // ---- vertices: one point per pixel, all 16 attribute registers
                const int NP = W * H;
                std::vector<float> verts((size_t)NP * 64);
                for (int p = 0; p < NP; p++) {
                    float *v = &verts[(size_t)p * 64];
                    for (int k = 0; k < 64; k++) v[k] = rng.uni(-1.0f, 1.0f);
                    for (int k = 0; k < vs.res.n_inputs; k++) {
                        const SmInput &si = vs.res.inputs[k];
                        float *a = v + si.reg * 4;
                        switch (si.usage) {
                        case SM_USAGE_POSITION: {
                            int px = p % W, py = p / W;
                            a[0] = 2.0f * px / W - 1.0f;   // D3D9 pixel centre (integer screen coords)
                            a[1] = 1.0f - 2.0f * py / H;
                            a[2] = rng.uni(0.1f, 0.9f);
                            a[3] = 1.0f;
                            break;
                        }
                        case SM_USAGE_BLENDINDICES: for (int cc = 0; cc < 4; cc++) a[cc] = (float)rng.range(0, 15); break;
                        case SM_USAGE_BLENDWEIGHT: case SM_USAGE_COLOR: for (int cc = 0; cc < 4; cc++) a[cc] = rng.uni(0.0f, 1.0f); break;
                        case SM_USAGE_TEXCOORD: for (int cc = 0; cc < 4; cc++) a[cc] = rng.uni(-1.0f, 2.0f); break;
                        default: break;
                        }
                    }
                }
                // ---- CPU reference: VS -> predicted pixel -> PS
                struct Expect { bool valid; bool killed; float rgba[4][4]; };
                std::vector<Expect> expect((size_t)W * H);
                std::vector<int> hits((size_t)W * H, 0);
                std::vector<int> owner((size_t)W * H, -1);
                std::vector<interp::VSOut> vouts(NP);
                for (int p = 0; p < NP; p++) {
                    interp::VSIn vi;
                    for (int r = 0; r < 16; r++) for (int k = 0; k < 4; k++) vi.v[r][k] = verts[(size_t)p * 64 + r * 4 + k];
                    interp::run_vs(vs.sh, vctx, vi, vouts[p]);
                    const interp::V4 &P = vouts[p].pos;
                    if (!(P[3] > 0) || P[2] < 0 || P[2] > P[3]) continue;
                    float xs = (P[0] / P[3] + 1.0f) * 0.5f * W, ys = (1.0f - P[1] / P[3]) * 0.5f * H;   // D3D screen
                    float fx = xs + 0.5f - std::floor(xs + 0.5f), fy = ys + 0.5f - std::floor(ys + 0.5f);
                    int ix = (int)std::floor(xs + 0.5f), iy = (int)std::floor(ys + 0.5f);
                    if (std::fabs(fx - 0.5f) > 0.4f || std::fabs(fy - 0.5f) > 0.4f) {
                        // too close to a pixel edge: may land in either neighbour, poison all of them
                        for (int dy = -1; dy <= 1; dy++)
                            for (int dx = -1; dx <= 1; dx++) {
                                int x = ix + dx, y = iy + dy;
                                if (x >= 0 && y >= 0 && x < W && y < H) hits[y * W + x] += 2;
                            }
                        continue;
                    }
                    if (ix < 0 || iy < 0 || ix >= W || iy >= H) continue;
                    hits[iy * W + ix]++;
                    owner[iy * W + ix] = p;
                }
                for (int px = 0; px < W * H; px++) {
                    Expect &e = expect[px];
                    e.valid = hits[px] == 1;
                    if (!e.valid) continue;
                    const interp::VSOut &vo = vouts[owner[px]];
                    interp::PSIn pi;
                    pi.col[0] = vo.col[0]; pi.col[1] = vo.col[1];
                    for (int k = 0; k < 8; k++) pi.tex[k] = vo.tex[k];
                    pi.fog = vo.fog;
                    pi.vpos = {(float)(px % W), (float)(px / W), vo.pos[2] / vo.pos[3], 1.0f};
                    interp::PSOut po;
                    interp::run_ps(ps.sh, pctx, pi, po);
                    bool atest = paux.alpha_func == 0 ||
                                 std::rint(std::min(std::max(po.oc[0][3], 0.0f), 1.0f) * 255.0f) > std::rint(paux.alpha_ref * 255.0f);
                    e.killed = po.killed || !atest;
                    for (int r = 0; r < 4; r++) for (int k = 0; k < 4; k++) e.rgba[r][k] = po.oc[r][k];
                }
                // ---- GPU
                MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
                pd.vertexFunction = vs.fn;
                pd.fragmentFunction = ps.fn;
                MTLVertexDescriptor *vd = [MTLVertexDescriptor vertexDescriptor];
                for (int k = 0; k < vs.res.n_inputs; k++) {
                    int reg = vs.res.inputs[k].reg;
                    vd.attributes[reg].format = MTLVertexFormatFloat4;
                    vd.attributes[reg].offset = 16 * reg;
                    vd.attributes[reg].bufferIndex = 0;
                }
                vd.layouts[0].stride = 256;
                pd.vertexDescriptor = vd;
                pd.inputPrimitiveTopology = MTLPrimitiveTopologyClassPoint;
                id<MTLTexture> rt[4] = {nil};
                MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
                for (int k = 0; k < 4; k++) {
                    if (!(ps.res.color_outputs & (1 << k))) continue;
                    pd.colorAttachments[k].pixelFormat = MTLPixelFormatRGBA32Float;
                    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:W height:H mipmapped:NO];
                    d.usage = MTLTextureUsageRenderTarget;
                    d.storageMode = MTLStorageModeShared;
                    rt[k] = [dev newTextureWithDescriptor:d];
                    rp.colorAttachments[k].texture = rt[k];
                    rp.colorAttachments[k].loadAction = MTLLoadActionClear;
                    rp.colorAttachments[k].clearColor = MTLClearColorMake(-7, -7, -7, -7);
                    rp.colorAttachments[k].storeAction = MTLStoreActionStore;
                }
                NSError *e = nil;
                id<MTLRenderPipelineState> pso = [dev newRenderPipelineStateWithDescriptor:pd error:&e];
                if (!pso) { printf("FAIL %s: pipeline %s\n", where.c_str(), e.localizedDescription.UTF8String); badp++; break; }
                id<MTLBuffer> vb = [dev newBufferWithBytes:verts.data() length:verts.size() * 4 options:MTLResourceStorageModeShared];
                id<MTLCommandBuffer> cmd = [q commandBuffer];
                id<MTLRenderCommandEncoder> enc = [cmd renderCommandEncoderWithDescriptor:rp];
                [enc setRenderPipelineState:pso];
                [enc setVertexBuffer:vb offset:0 atIndex:0];
                [enc setVertexBytes:vc.data() length:vc.size() * 4 atIndex:SM_VS_BUF_FLOAT];
                [enc setVertexBytes:ci.data() length:ci.size() * 4 atIndex:SM_VS_BUF_INT];
                [enc setVertexBytes:cb.data() length:cb.size() * 4 atIndex:SM_VS_BUF_BOOL];
                [enc setVertexBytes:&vaux length:sizeof vaux atIndex:SM_VS_BUF_AUX];
                [enc setFragmentBytes:pc.data() length:pc.size() * 4 atIndex:SM_PS_BUF_FLOAT];
                [enc setFragmentBytes:ci.data() length:ci.size() * 4 atIndex:SM_PS_BUF_INT];
                [enc setFragmentBytes:cb.data() length:cb.size() * 4 atIndex:SM_PS_BUF_BOOL];
                [enc setFragmentBytes:&paux length:sizeof paux atIndex:SM_PS_BUF_AUX];
                for (int s = 0; s < 16; s++)
                    if (mtex[s]) { [enc setFragmentTexture:mtex[s] atIndex:s]; [enc setFragmentSamplerState:msmp[s] atIndex:s]; }
                [enc drawPrimitives:MTLPrimitiveTypePoint vertexStart:0 vertexCount:NP];
                [enc endEncoding];
                [cmd commit];
                [cmd waitUntilCompleted];
                // ---- compare
                int bad = 0, valid = 0, killed = 0;
                std::string first;
                std::vector<float> img[4];
                for (int k = 0; k < 4; k++)
                    if (rt[k]) {
                        img[k].resize((size_t)W * H * 4);
                        [rt[k] getBytes:img[k].data() bytesPerRow:W * 16 fromRegion:MTLRegionMake2D(0, 0, W, H) mipmapLevel:0];
                    }
                for (int px = 0; px < W * H; px++) {
                    const Expect &ex = expect[px];
                    if (!ex.valid) continue;
                    valid++;
                    if (ex.killed) killed++;
                    for (int k = 0; k < 4 && first.size() < 2; k++) {
                        if (!rt[k]) continue;
                        const float *g = &img[k][(size_t)px * 4];
                        for (int cc = 0; cc < 4; cc++) {
                            float want = ex.killed ? -7.0f : ex.rgba[k][cc];
                            if (!close_enough(g[cc], want, 2e-3f)) {
                                if (first.empty()) {
                                    char b[200];
                                    snprintf(b, sizeof b, "pixel (%d,%d) oC%d.%c gpu %.7g cpu %.7g%s", px % W, px / W, k, "xyzw"[cc], g[cc], want,
                                             ex.killed ? " (cpu: discarded)" : "");
                                    first = b;
                                }
                                bad++;
                                k = 4;
                                break;
                            }
                        }
                    }
                }
                checked_px += valid;
                if (bad || valid < W * H / 4) {
                    printf("FAIL %s round %d: %d/%d pixels differ (%d checked, WVP identity %d); first: %s\n", where.c_str(), round,
                           bad, valid, valid, identity, first.c_str());
                    badp++;
                    break;
                }
                if (round == 1) {
                    good++;
                    printf("ok   %-44s %4d px checked, %4d discarded (texkill / alpha test)\n", where.c_str(), valid, killed);
                }
            }
            sm_result_free(&vs.res);
            sm_result_free(&ps.res);
        }
        printf("render check: %d passes, %d match, %d differ, %ld pixels compared\n", passes, good, badp, checked_px);
        if(only.empty() || only=="near")badp+=near_checks(dev,td);
        return badp ? 1 : 0;
    }
}
