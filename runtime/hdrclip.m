// Step 2 plan: attribute raw exports first; route a separate scene attachment by
// phase, promote only screen copies, sanitize world exports, then prove transport
// with the renderer's copy path and a deliberately UNORM copy. Off stays verbatim.
// Included by render.m so this diagnostic needs no change to the runtime link list.
// Plan/proof and observed game counts live in the runtime README row.
#define HDRCLIP_IMPLEMENTATION
#include "hdrclip.h"
#include <math.h>
int hdrclip_on;
int float_scene_on;
_Static_assert(HC_STAGES==25,"update MSL stage bound");
static id<MTLBuffer> hc_buffer;
static id<MTLBuffer> hc_pairs;
static uint64_t hc_frame;
static int hc_finished;
enum { HC_FAMILIES=256, HC_COUNTERS=HC_STAGES*2+HC_FAMILIES };
typedef struct { uint64_t key; int material; unsigned variant; } HCFamily;
static HCFamily hc_families[HC_FAMILIES];
static unsigned hc_nfamilies;
static const char *hc_names[HC_STAGES] = {"world_export","other_export","msaa_samples","scene_resolve","scene_copy",
    "downscale","downscale_mask","luminance","bright","blur5","bloom_down","bloom_h","bloom_v","bloom_h2","bloom_v2",
    "dof","curve_luminance","final","final_resolve","lamp_source","lamp_blur","lamp_down","lamp_h","lamp_v","mirror"};
// Each phase has 8 uint counters. Counts are channels, except n (float4 exports/samples)
// and nonfinite (float4 values containing any NaN/Inf). RGB/alpha are kept separate.
// White endpoints in stored UNORM are censored evidence, NEVER an inferred clip count.
static NSString *hc_msl(void) { return @
"#include <metal_stdlib>\nusing namespace metal;\n"
"inline void hc_values(float4 v, thread uint *n) {\n"
" uint z[8]={1u,uint(v.r>1)+uint(v.g>1)+uint(v.b>1),uint(v.r<0)+uint(v.g<0)+uint(v.b<0),uint(v.a>1),uint(v.a<0),uint(!all(isfinite(v))),uint(v.r==1)+uint(v.g==1)+uint(v.b==1),uint(v.a==1)}; for(uint k=0;k<8;k++)n[k]=z[k];\n"
"}\n"
"inline void hc_add(device atomic_uint *b,uint s,thread uint *n){if(s>=306u)return;for(uint k=0;k<8;k++)if(n[k])atomic_fetch_add_explicit(b+s*8+k,n[k],memory_order_relaxed);}\n"
"inline void hc_count(float4 v,device atomic_uint *b,uint s){if(s>=306u)return;uint n[8];hc_values(v,n);hc_add(b,s,n);}\n"; }
int hdrclip_test(int mutant);
void hdrclip_init(void) {
    const char *fs=getenv("NFS_FLOAT_SCENE"),*pads=getenv("NFS_PADS");
    // Shared split atlases require per-view phase routing (design step 7).
    float_scene_on=fs && !strcmp(fs,"on") && !(pads && !strcmp(pads,"2"));
    if(diag_on)diag_event("float scene: requested=%s enabled=%d pads=%s",fs?fs:"off",float_scene_on,pads?pads:"1");
    const char *e=diag_on ? getenv("BOOT_DIAG_HDR_CLIP") : NULL;
    hdrclip_on=e && !strcmp(e,"1");
    if(diag_on && getenv("BOOT_DIAG_HDR_CLIP_TEST")) {
        int rc=hdrclip_test(getenv("BOOT_DIAG_HDR_CLIP_MUTANT")!=NULL);
        fprintf(stderr,"hdrclip_test: result=%d\n",rc);
        if(!getenv("BOOT_DIAG_HDR_CLIP_MUTANT")) {
            int bad=hdrclip_test(1);
            fprintf(stderr,"hdrclip_test: suite normal=%d mutant=%d expected=0/1 %s\n",rc,bad,rc==0&&bad==1?"PASS":"FAIL");
        }
    }
    if(hdrclip_on) { hc_buffer=[mtl newBufferWithLength:HC_COUNTERS*8*sizeof(uint32_t) options:MTLResourceStorageModeShared]; memset(hc_buffer.contents,0,hc_buffer.length); }
    if(hdrclip_on){hc_pairs=[mtl newBufferWithLength:8*sizeof(uint32_t) options:MTLResourceStorageModeShared];memset(hc_pairs.contents,0,hc_pairs.length);}
}
// Preserve offsets while ignoring comments in signatures, braces and return searches.
static NSString *hc_code(NSString *s) {
    NSRegularExpression *comments=[NSRegularExpression regularExpressionWithPattern:@"/\\*.*?\\*/|//[^\\n]*" options:NSRegularExpressionDotMatchesLineSeparators error:nil];
    NSMutableString *code=[s mutableCopy];
    for(NSTextCheckingResult *r in [[comments matchesInString:s options:0 range:NSMakeRange(0,s.length)] reverseObjectEnumerator])
        [code replaceCharactersInRange:r.range withString:[@"" stringByPaddingToLength:r.range.length withString:@" " startingAtIndex:0]];
    return code;
}
// Transform fragment entrypoints only. The original source object is returned verbatim when Off.
// Balanced delimiters handle multiline signatures and nested return expressions; helpers are untouched.
static NSString *hc_transform(NSString *source, unsigned family, int world) {
    int sanitize=world && float_scene_on;
    if(!hdrclip_on && !sanitize)return source;
    NSMutableString *s=[source mutableCopy];
    NSRegularExpression *fn=[NSRegularExpression regularExpressionWithPattern:@"\\bfragment\\s+(\\w+)\\s+(\\w+)\\s*\\(" options:0 error:nil];
    NSString *code=hc_code(s);
    NSArray<NSTextCheckingResult *> *functions=[fn matchesInString:code options:0 range:NSMakeRange(0,code.length)];
    for(NSTextCheckingResult *f in functions.reverseObjectEnumerator) {
        NSString *type=[s substringWithRange:[f rangeAtIndex:1]];
        NSUInteger a=NSMaxRange(f.range),p=a;int depth=1;
        while(p<s.length && depth){unichar c=[code characterAtIndex:p++];if(c=='(')depth++;if(c==')')depth--;}
        NSUInteger close=p-1,open=p;
        while(open<s.length && [code characterAtIndex:open]!='{')open++;
        p=open+1;depth=1;
        while(p<s.length && depth){unichar c=[code characterAtIndex:p++];if(c=='{')depth++;if(c=='}')depth--;}
        if(depth || open>=s.length)[NSException raise:@"hdrclip" format:@"unbalanced fragment source"];
        NSMutableArray<NSString *> *values=[NSMutableArray array];
        if([type isEqual:@"float4"] || [type isEqual:@"half4"]) [values addObject:@"float4(hc_v)"];
        else {
            NSString *pattern=[NSString stringWithFormat:@"struct\\s+%@\\s*\\{([^}]+)\\}",type];
            NSRegularExpression *st=[NSRegularExpression regularExpressionWithPattern:pattern options:0 error:nil];
            NSTextCheckingResult *r=[st firstMatchInString:s options:0 range:NSMakeRange(0,s.length)];
            NSString *body=r?[s substringWithRange:[r rangeAtIndex:1]]:@"";
            NSRegularExpression *field=[NSRegularExpression regularExpressionWithPattern:@"(?:float4|half4)\\s+(\\w+)\\s*\\[\\[color\\((\\d+)\\)\\]\\]" options:0 error:nil];
            for(NSTextCheckingResult *m in [field matchesInString:body options:0 range:NSMakeRange(0,body.length)]) {
                unsigned slot=[[body substringWithRange:[m rangeAtIndex:2]] intValue];
                [values addObject:[NSString stringWithFormat:@"float4(hc_v.%@),%u",[body substringWithRange:[m rangeAtIndex:1]],slot]];
            }
        }
        // Depth-only fragments intentionally do not contribute radiance counters.
        if(!values.count)continue;
        NSString *body=[s substringWithRange:NSMakeRange(open+1,p-open-2)];
        NSString *body_code=hc_code(body);
        NSRegularExpression *ret=[NSRegularExpression regularExpressionWithPattern:@"\\breturn\\s+([^;]+);" options:0 error:nil];
        NSMutableString *patched=[body mutableCopy];
        for(NSTextCheckingResult *r in [[ret matchesInString:body_code options:0 range:NSMakeRange(0,body_code.length)] reverseObjectEnumerator]) {
            NSMutableString *replacement=[NSMutableString stringWithFormat:@"{ auto hc_v=(%@); ",[body substringWithRange:[r rangeAtIndex:1]]];
            for(NSString *v in values) {
                NSArray *parts=[v componentsSeparatedByString:@","]; // struct value, MRT slot (scalar has no comma)
                unsigned slot=parts.count>1?[parts.lastObject intValue]:0;
                if(hdrclip_on) {
                    [replacement appendFormat:@"hc_count(%@,hc_buf,hc_stage<%du?((hc_stage==%du?hc_stage+%u:hc_stage)*2u):999u); ",parts[0],HC_STAGES,HC_DOWN,slot];
                    if(family<HC_FAMILIES) [replacement appendFormat:@"if(hc_stage==0u)hc_count(%@,hc_buf,%uu); ",parts[0],HC_STAGES*2+family];
                }
                if(sanitize) {
                    NSString *value=[parts[0] substringWithRange:NSMakeRange(7,[parts[0] length]-8)]; // float4(lvalue)
                    [replacement appendFormat:@"if(fs_store)%@=decltype(%@)(fs_finite(%@,fs_store)); ",value,value,parts[0]];
                    if(hdrclip_on)[replacement appendFormat:@"if(fs_store && hc_stage==0u)hc_count(%@,hc_buf,1u); ",parts[0]];
                }
            }
            [replacement appendString:@"return hc_v; }"];
            [patched replaceCharactersInRange:r.range withString:replacement];
        }
        [s replaceCharactersInRange:NSMakeRange(open+1,p-open-2) withString:patched];
        if(sanitize)[s insertString:@", constant uint &fs_store [[buffer(27)]]" atIndex:close];
        if(hdrclip_on)[s insertString:@", device atomic_uint *hc_buf [[buffer(25)]], constant uint &hc_stage [[buffer(26)]]" atIndex:close];
    }
    NSString *prefix=hdrclip_on ? hc_msl():@"";
    // Integer exponent classification survives relaxed/fast math. Preserve the
    // old nonnegative lower bound and bounded opacity; RGB above white survives
    // up to FP16's finite maximum. Policy 2 bounds SDR HUD/FE/movie source RGB
    // before blending into extended composition. Destination alpha is counted.
    if(sanitize)prefix=[prefix stringByAppendingString:@"\n#include <metal_stdlib>\nusing namespace metal;\ninline float4 fs_finite(float4 v,uint policy){uint4 u=as_type<uint4>(v);v=select(v,float4(0),(u&0x7f800000u)==0x7f800000u);v=clamp(v,0.0f,policy==2u?1.0f:65504.0f);v.a=clamp(v.a,0.0f,1.0f);return v;}\n"];
    return [prefix stringByAppendingString:s];
}
NSString *hdrclip_source(NSString *source) { return hc_transform(source,HC_FAMILIES,0); }
NSString *hdrclip_world_source(NSString *source,uint64_t key,int material,unsigned variant) {
    if(!hdrclip_on && !float_scene_on)return source;
    unsigned family=HC_FAMILIES;
    if(hdrclip_on) @synchronized([NSProcessInfo class]) {
        for(unsigned i=0;i<hc_nfamilies;i++)if(hc_families[i].key==key){family=i;break;}
        if(family==HC_FAMILIES && hc_nfamilies<HC_FAMILIES){
            family=hc_nfamilies++;hc_families[family].key=key;
            hc_families[family].material=material;hc_families[family].variant=variant;
        }
    }
    return hc_transform(source,family,1);
}
static unsigned hc_select(unsigned stage) {
    if(!hc_frame && M32(0x00925E90u)==6 && MF32(0x009885D8u)>=15.0f)hc_frame=frame_serial;
    return !hc_finished && hc_frame==frame_serial ? stage:HC_STAGES;
}
void hdrclip_bind(id<MTLRenderCommandEncoder> e,unsigned stage) {
    unsigned s=hc_select(stage);
    [e setFragmentBuffer:hc_buffer offset:0 atIndex:25];[e setFragmentBytes:&s length:sizeof s atIndex:26];
}
// The command recorder supports render/blit encoders, not compute. Each raster
// invocation scans 32 source pixels, reducing atomic traffic without sampling.
static void hc_scan_texture(id<MTLCommandBuffer> cb,id<MTLTexture> t,id<MTLBuffer> buffer,unsigned stage) {
    static id<MTLRenderPipelineState> scan[2];
    static id<MTLTexture> scratch;
    unsigned ms=t.sampleCount>1;
    if(!scan[ms]) {
        NSString *src=[hc_msl() stringByAppendingFormat:@
            "vertex float4 hc_vs(uint v [[vertex_id]]) {float2 p=float2((v<<1)&2,v&2);return float4(p*2-1,0,1);}\n"
            "fragment void hc_scan(float4 pos [[position]],%@ t [[texture(0)]],device atomic_uint *b [[buffer(0)]],constant uint &s [[buffer(1)]]) {uint2 p=uint2(pos.xy);uint n[8]={};for(uint x=p.x*32;x<min(p.x*32+32,t.get_width());x++){%@}hc_add(b,s,n);}",
            ms?@"texture2d_ms<float,access::read>":@"texture2d<float,access::read>",
            ms?@"for(uint j=0;j<t.get_num_samples();j++){uint m[8];hc_values(t.read(uint2(x,p.y),j),m);for(uint k=0;k<8;k++)n[k]+=m[k];}":@"uint m[8];hc_values(t.read(uint2(x,p.y)),m);for(uint k=0;k<8;k++)n[k]+=m[k];"];
        NSError *error=nil;id<MTLLibrary> l=[mtl newLibraryWithSource:src options:nil error:&error];
        MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];pd.vertexFunction=[l newFunctionWithName:@"hc_vs"];pd.fragmentFunction=[l newFunctionWithName:@"hc_scan"];
        pd.colorAttachments[0].pixelFormat=MTLPixelFormatR8Unorm;pd.colorAttachments[0].writeMask=MTLColorWriteMaskNone;
        scan[ms]=[mtl newRenderPipelineStateWithDescriptor:pd error:&error];
        if(!scan[ms]){diag_event("hdrclip: scan compile failed %s",error.localizedDescription.UTF8String);return;}
    }
    NSUInteger w=(t.width+31)/32,h=t.height;
    if(!scratch || scratch.width!=w || scratch.height!=h) {
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatR8Unorm width:w height:h mipmapped:NO];
        d.usage=MTLTextureUsageRenderTarget;d.storageMode=MTLStorageModePrivate;scratch=[mtl newTextureWithDescriptor:d];
    }
    unsigned s=stage*2+1;
    MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];rp.colorAttachments[0].texture=scratch;rp.colorAttachments[0].loadAction=MTLLoadActionDontCare;rp.colorAttachments[0].storeAction=MTLStoreActionDontCare;
    id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:rp];
    [e setRenderPipelineState:scan[ms]];[e setFragmentTexture:t atIndex:0];[e setFragmentBuffer:buffer offset:0 atIndex:0];[e setFragmentBytes:&s length:sizeof s atIndex:1];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
}
void hdrclip_scan(id<MTLTexture> t,unsigned stage) {
    if(hc_select(stage)>=HC_STAGES || !t)return;
    if(!(t.usage&MTLTextureUsageShaderRead)){diag_event("hdrclip: unreadable stage=%s",hc_names[stage]);return;}
    end_pass();ensure_cmd();hc_scan_texture(cmd,t,hc_buffer,stage);
}
// Compare actual paired storage. Export counts are before depth/blend and cannot
// establish how many of those values survive to a texture. Report the eligible
// after-blend high channels explicitly; zero eligible values is NOT HDR proof.
static void hdrclip_pair(id<MTLTexture> src,id<MTLTexture> dst,unsigned stage) {
    if(hc_select(HC_COPY)>=HC_STAGES || stage>1 || !src || !dst || src.width!=dst.width || src.height!=dst.height)return;
    end_pass();ensure_cmd();
    static id<MTLRenderPipelineState> psos[4];
    static id<MTLTexture> scratch;
    unsigned variant=(src.sampleCount>1)|((dst.sampleCount>1)<<1);
    if(!psos[variant]) {
        NSString *type=@"texture2d<float,access::read>",*ms=@"texture2d_ms<float,access::read>";
        NSString *readA=src.sampleCount>1?@"float4 a=0;for(uint j=0;j<s.get_num_samples();j++)a+=s.read(p,j);a/=s.get_num_samples();":@"float4 a=s.read(p);";
        NSString *readB=dst.sampleCount>1?@"float4 c=0;for(uint j=0;j<d.get_num_samples();j++)c+=d.read(p,j);c/=d.get_num_samples();":@"float4 c=d.read(p);";
        NSString *source=[NSString stringWithFormat:@"#include <metal_stdlib>\nusing namespace metal;\nvertex float4 vs(uint v [[vertex_id]]){float2 p=float2((v<<1)&2,v&2);return float4(p*2-1,0,1);}\nfragment void fs(float4 pos [[position]],%@ s [[texture(0)]],%@ d [[texture(1)]],device atomic_uint *b [[buffer(0)]],constant uint &stage [[buffer(1)]]){uint n[4]={};for(uint x=uint(pos.x)*32;x<min(uint(pos.x)*32+32,s.get_width());x++){uint2 p=uint2(x,uint(pos.y));%@%@n[0]++;for(uint k=0;k<3;k++){n[1]+=uint(a[k]>1);n[2]+=uint(a[k]>1 && c[k]<=1);}n[3]+=uint(any((as_type<uint4>(c)&0x7f800000u)==0x7f800000u));}for(uint k=0;k<4;k++)if(n[k])atomic_fetch_add_explicit(b+stage*4+k,n[k],memory_order_relaxed);}",src.sampleCount>1?ms:type,dst.sampleCount>1?ms:type,readA,readB];
        NSError *error=nil;id<MTLLibrary> lib=[mtl newLibraryWithSource:source options:nil error:&error];
        MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];pd.vertexFunction=[lib newFunctionWithName:@"vs"];pd.fragmentFunction=[lib newFunctionWithName:@"fs"];pd.colorAttachments[0].pixelFormat=MTLPixelFormatR8Unorm;pd.colorAttachments[0].writeMask=MTLColorWriteMaskNone;
        psos[variant]=[mtl newRenderPipelineStateWithDescriptor:pd error:&error];
        if(!psos[variant]){diag_event("hdrclip pair compile: %s",error.localizedDescription.UTF8String);return;}
    }
    NSUInteger w=(src.width+31)/32,h=src.height;
    if(!scratch || scratch.width!=w || scratch.height!=h){MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatR8Unorm width:w height:h mipmapped:NO];d.usage=MTLTextureUsageRenderTarget;d.storageMode=MTLStorageModePrivate;scratch=[mtl newTextureWithDescriptor:d];}
    MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];rp.colorAttachments[0].texture=scratch;rp.colorAttachments[0].loadAction=MTLLoadActionDontCare;rp.colorAttachments[0].storeAction=MTLStoreActionDontCare;
    id<MTLRenderCommandEncoder> e=[cmd renderCommandEncoderWithDescriptor:rp];[e setRenderPipelineState:psos[variant]];[e setFragmentTexture:src atIndex:0];[e setFragmentTexture:dst atIndex:1];[e setFragmentBuffer:hc_pairs offset:0 atIndex:0];[e setFragmentBytes:&stage length:4 atIndex:1];[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
}
void hdrclip_finish(void) {
    if(hc_select(HC_FINAL)>=HC_STAGES)return;
    hc_finished=1;id<MTLBuffer> b=hc_buffer,pairs=hc_pairs;uint64_t frame=hc_frame;float sim=MF32(0x009885D8u);
    NSData *families;unsigned family_count;
    @synchronized([NSProcessInfo class]) {
        family_count=hc_nfamilies;families=[NSData dataWithBytes:hc_families length:family_count*sizeof(HCFamily)];
    }
    [cmd addCompletedHandler:^(id<MTLCommandBuffer> cb){
        char path[1024];snprintf(path,sizeof path,"%s/hdrclip.csv",diag_dir);
        FILE *f=fopen(path,"w");if(!f)return;
        fprintf(f,"# frame=%llu sim=%.6f status=%lu; exports (including sanitized_export) precede depth/blend; stores are floating texture reads; white endpoints are censored\n",(unsigned long long)frame,sim,(unsigned long)cb.status);
        fprintf(f,"stage,phase,n,rgb_hi,rgb_lo,alpha_hi,alpha_lo,nonfinite,rgb_white,alpha_white\n");
        const uint32_t *v=b.contents;
        for(unsigned s=0;s<HC_STAGES;s++)for(unsigned p=0;p<2;p++) {
            fprintf(f,"%s,%s",hc_names[s],p?(s==HC_WORLD?"sanitized_export":"stored"):"export");
            for(unsigned k=0;k<8;k++)fprintf(f,",%u",v[(s*2+p)*8+k]);fprintf(f,"\n");
        }
        fclose(f);
        snprintf(path,sizeof path,"%s/hdrclip_transport.csv",diag_dir);f=fopen(path,"w");if(!f)return;
        fprintf(f,"stage,pixels,source_rgb_hi,rgb_clipped,nonfinite\n");
        const uint32_t *pv=pairs.contents;
        for(unsigned s=0;s<2;s++)fprintf(f,"%s,%u,%u,%u,%u\n",s?"copy":"resolve",pv[s*4],pv[s*4+1],pv[s*4+2],pv[s*4+3]);
        fclose(f);
        snprintf(path,sizeof path,"%s/hdrclip_families.csv",diag_dir);f=fopen(path,"w");if(!f)return;
        fprintf(f,"shader,material,variant,n,rgb_hi,rgb_lo,alpha_hi,alpha_lo,nonfinite,rgb_white,alpha_white\n");
        const HCFamily *family=families.bytes;
        for(unsigned i=0;i<family_count;i++){
            fprintf(f,"%016llX,%d,%u",(unsigned long long)family[i].key,family[i].material,family[i].variant);
            for(unsigned k=0;k<8;k++)fprintf(f,",%u",v[(HC_STAGES*2+i)*8+k]);fprintf(f,"\n");
        }
        fclose(f);
    }];
}

// Shared native check, also runnable in an authorized game_run. No game assets.
// 1025 samples [0,8] at 1/128: 896 over-white exports, 128 below-white,
// 897 stored UNORM white endpoints. Compare all output bytes with the original shader.
// The mutant suppresses rgb_hi in the SAME helper used by the renderer and must fail.
int hdrclip_test(int mutant) {
    NSString *original=@"#include <metal_stdlib>\nusing namespace metal;\n"
    "struct V { float4 p [[position]]; };\n"
    "vertex V vs(uint id [[vertex_id]]){float2 p=float2((id<<1)&2,id&2);return {float4(p*2-1,0,1)};}\n"
    "struct R { float4 c [[color(0)]]; };\n"
    "fragment R fs(V i [[stage_in]], uint sid [[sample_id]]){ float x=floor(i.p.x)/128.0; (void)sid; R r; r.c=float4(x,x,x,1.5); if(x<0) return r; return r; }\n";
    int saved=hdrclip_on;hdrclip_on=0;
    if(hdrclip_source(original)!=original){hdrclip_on=saved;return 1;}
    hdrclip_on=1;NSString *instrumented=hdrclip_source(original);hdrclip_on=saved;
    if(mutant)instrumented=[instrumented stringByReplacingOccurrencesOfString:@"uint(v.r>1)+uint(v.g>1)+uint(v.b>1)" withString:@"0u"];
    if(!mtl)return 3;
    id<MTLCommandQueue> q=[mtl newCommandQueue];
    id<MTLBuffer> b=[mtl newBufferWithLength:HC_STAGES*16*4 options:MTLResourceStorageModeShared];
    uint8_t bytes[2][2][1025*4];
    for(int on=0;on<2;on++) {
        NSError *error=nil;id<MTLLibrary> l=[mtl newLibraryWithSource:on?instrumented:original options:nil error:&error];
        if(!l){fprintf(stderr,"hdrclip_test: MSL %s\n",error.localizedDescription.UTF8String);return 1;}
        for(int multisample=0;multisample<2;multisample++) for(int fp=0;fp<2;fp++) {
            unsigned samples=multisample?4:1;
            if(![mtl supportsTextureSampleCount:samples])return 3;
            MTLPixelFormat format=fp?MTLPixelFormatRGBA16Float:MTLPixelFormatRGBA8Unorm;
            MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:1025 height:1 mipmapped:NO];
            d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
            id<MTLTexture> resolved=[mtl newTextureWithDescriptor:d];
            if(multisample){d.textureType=MTLTextureType2DMultisample;d.sampleCount=samples;d.storageMode=MTLStorageModePrivate;}
            id<MTLTexture> t=multisample?[mtl newTextureWithDescriptor:d]:resolved;
            MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];pd.vertexFunction=[l newFunctionWithName:@"vs"];pd.fragmentFunction=[l newFunctionWithName:@"fs"];pd.colorAttachments[0].pixelFormat=format;pd.rasterSampleCount=samples;
            id<MTLRenderPipelineState> p=[mtl newRenderPipelineStateWithDescriptor:pd error:&error];if(!p)return 1;
            id<MTLCommandBuffer> cb=[q commandBuffer];MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
            rp.colorAttachments[0].texture=t;rp.colorAttachments[0].loadAction=MTLLoadActionDontCare;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
            if(multisample){rp.colorAttachments[0].resolveTexture=resolved;rp.colorAttachments[0].storeAction=MTLStoreActionStoreAndMultisampleResolve;}
            memset(b.contents,0,b.length);unsigned stage=HC_WORLD;
            id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:rp];[e setRenderPipelineState:p];
            if(on){[e setFragmentBuffer:b offset:0 atIndex:25];[e setFragmentBytes:&stage length:4 atIndex:26];}
            [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
            if(on){hc_scan_texture(cb,t,b,HC_WORLD);hc_scan_texture(cb,resolved,b,HC_RESOLVE);}
            [cb commit];[cb waitUntilCompleted];
            if(cb.status!=MTLCommandBufferStatusCompleted)return 1;
            const uint32_t *v=b.contents;
            if(on && (v[0]!=1025*samples || v[1]!=896*3*samples || v[2]!=0 || v[3]!=1025*samples || v[4]!=0 || v[5]!=0)) {
                fprintf(stderr,"hdrclip_test: FAIL mutant=%d n=%u rgb_hi=%u expected=2688 alpha_hi=%u\n",mutant,v[0],v[1],v[3]);return 1;
            }
            if(on && (v[8]!=1025*samples || v[9]!=(fp?2688*samples:0) || v[11]!=(fp?1025*samples:0) || v[14]!=(fp?3:897*3)*samples)) {
                fprintf(stderr,"hdrclip_test: FAIL stored fp=%d n=%u hi=%u white=%u\n",fp,v[8],v[9],v[14]);return 1;
            }
            const uint32_t *rv=v+(HC_RESOLVE*2+1)*8;
            if(on && (rv[0]!=1025 || rv[1]!=(fp?2688:0) || rv[6]!=(fp?3:897*3))) {
                fprintf(stderr,"hdrclip_test: FAIL resolve samples=%u n=%u hi=%u white=%u\n",samples,rv[0],rv[1],rv[6]);return 1;
            }
            if(!fp)[resolved getBytes:bytes[on][multisample] bytesPerRow:1025*4 fromRegion:MTLRegionMake2D(0,0,1025,1) mipmapLevel:0];
            else {
                uint16_t halfs[1025*4];[resolved getBytes:halfs bytesPerRow:1025*8 fromRegion:MTLRegionMake2D(0,0,1025,1) mipmapLevel:0];
                for(int x=0;x<=1024;x++) {__fp16 h;memcpy(&h,&halfs[x*4],2);if((float)h!=x/128.0f)return 1;}
            }
        }
    }
    if(memcmp(bytes[0],bytes[1],sizeof bytes[0])){fprintf(stderr,"hdrclip_test: FAIL output bytes differ\n");return 1;}
    unsigned white=0;for(int x=0;x<=1024;x++)white+=bytes[1][0][x*4]==255;
    if(white!=897)return 1;
    fprintf(stderr,"hdrclip_test: PASS rgb_hi=2688 alpha_hi=1025 UNORM_white=897 FP16_exact=1025 MSAA_samples=4100 resolve=1025 Off_AB_bytes=8200\n");return 0;
}

// Step 2 native proof: 1025 ramp pixels [0,8], plus one NaN/Inf export.
// Expected normal clipped=0 / exact=1025; forced BGRA8-copy mutant clipped=896.
// 1x/4x MSAA, float resolve, two actual renderer copies, then exposure 1/8.
// Off shader and copy bytes compare to the original draw (4104 bytes per mode).
#ifdef HDRCLIP_COPY_TEST
extern void quad_into(id<MTLCommandBuffer>,id<MTLTexture>,MTLRegion,id<MTLTexture>,NSUInteger,NSUInteger,MTLRegion,int);
int float_scene_test(int mutant) {
    if(!mtl)return 3;
    int saved_hc=hdrclip_on,saved_fs=float_scene_on;hdrclip_on=0;float_scene_on=1;
    NSString *raw=@"#include <metal_stdlib>\nusing namespace metal;\n"
        "vertex float4 fs_vs(uint v [[vertex_id]]){float2 p=float2((v<<1)&2,v&2);return float4(p*2-1,0,1);}\n"
        "fragment float4 fs_world(float4 p [[position]]){float x=floor(p.x)/128.0f;if(p.x>1025)return as_type<float4>(uint4(0x7fc00000u,0x7f800000u,0xff800000u,0x7fc00000u));return float4(x,x,x,0.5);}\n"
        "fragment float4 fs_expose(float4 p [[position]],texture2d<float,access::read> t [[texture(0)]]){return float4(t.read(uint2(p.xy)).rgb/8.0f,1);}\n";
    NSString *safe=hdrclip_world_source(raw,0,0,0);
    hdrclip_on=saved_hc;
    @try {
    NSError *error=nil;
    MTLCompileOptions *opts=[MTLCompileOptions new];opts.mathMode=MTLMathModeRelaxed;
    id<MTLLibrary> libs[2]={[mtl newLibraryWithSource:raw options:opts error:&error],[mtl newLibraryWithSource:safe options:opts error:&error]};
    if(!libs[0]||!libs[1]){fprintf(stderr,"float_scene_test: compile %s\n",error.localizedDescription.UTF8String);return 1;}
    id<MTLCommandQueue> q=[mtl newCommandQueue];
    unsigned exact=0,clipped=0,nonfinite=0,diff_low=0,diff_high=0;
    unsigned msaa_clip=0,resolve_clip=0,copy_clip=0;
    for(unsigned samples=1;samples<=4;samples*=4) {
        if(![mtl supportsTextureSampleCount:samples])return 3;
        MTLRegion region=MTLRegionMake2D(0,0,1026,1);
        uint8_t off_bytes[2][4104];
        uint8_t legacy_display[4104];
        for(unsigned mode=0;mode<3;mode++) {
            MTLPixelFormat format=mode==2?MTLPixelFormatRGBA16Float:MTLPixelFormatBGRA8Unorm;
            MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:1026 height:1 mipmapped:NO];
            d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;d.storageMode=MTLStorageModeShared;
            id<MTLTexture> resolve=[mtl newTextureWithDescriptor:d];
            if(samples>1){d.textureType=MTLTextureType2DMultisample;d.sampleCount=samples;d.storageMode=MTLStorageModePrivate;}
            id<MTLTexture> color=samples>1?[mtl newTextureWithDescriptor:d]:resolve;
            MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];
            id<MTLLibrary> lib=libs[mode==2];
            pd.vertexFunction=[lib newFunctionWithName:@"fs_vs"];pd.fragmentFunction=[lib newFunctionWithName:@"fs_world"];
            pd.colorAttachments[0].pixelFormat=format;pd.rasterSampleCount=samples;
            id<MTLRenderPipelineState> world=[mtl newRenderPipelineStateWithDescriptor:pd error:&error];
            if(!world)return 1;
            id<MTLCommandBuffer> cb=[q commandBuffer];
            id<MTLBuffer> proof=[mtl newBufferWithLength:HC_COUNTERS*8*sizeof(uint32_t) options:MTLResourceStorageModeShared];memset(proof.contents,0,proof.length);
            MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];rp.colorAttachments[0].texture=color;
            rp.colorAttachments[0].loadAction=MTLLoadActionDontCare;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
            if(samples>1){rp.colorAttachments[0].resolveTexture=resolve;rp.colorAttachments[0].storeAction=MTLStoreActionStoreAndMultisampleResolve;}
            id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:rp];[e setRenderPipelineState:world];
            if(mode==2){unsigned store=1;[e setFragmentBytes:&store length:4 atIndex:27];}
            [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
            if(mode==2){hc_scan_texture(cb,color,proof,HC_MSAA);hc_scan_texture(cb,resolve,proof,HC_RESOLVE);}
            d.textureType=MTLTextureType2D;d.sampleCount=1;d.storageMode=MTLStorageModeShared;
            if(mutant && mode==2)d.pixelFormat=MTLPixelFormatBGRA8Unorm;
            id<MTLTexture> copy=[mtl newTextureWithDescriptor:d];
            quad_into(cb,resolve,region,copy,0,0,region,0);
            d.pixelFormat=format;id<MTLTexture> copy2=[mtl newTextureWithDescriptor:d];
            id<MTLTexture> copy_target=copy2;
            if(samples>1){d.textureType=MTLTextureType2DMultisample;d.sampleCount=samples;d.storageMode=MTLStorageModePrivate;copy_target=[mtl newTextureWithDescriptor:d];}
            quad_into(cb,copy,region,copy_target,0,0,region,0);
            if(samples>1){
                MTLRenderPassDescriptor *resolve_pass=[MTLRenderPassDescriptor renderPassDescriptor];resolve_pass.colorAttachments[0].texture=copy_target;resolve_pass.colorAttachments[0].resolveTexture=copy2;resolve_pass.colorAttachments[0].loadAction=MTLLoadActionLoad;resolve_pass.colorAttachments[0].storeAction=MTLStoreActionStoreAndMultisampleResolve;
                [[cb renderCommandEncoderWithDescriptor:resolve_pass] endEncoding];
            }
            if(mode==2)hc_scan_texture(cb,copy2,proof,HC_COPY);
            d.textureType=MTLTextureType2D;d.sampleCount=1;d.storageMode=MTLStorageModeShared;
            d.pixelFormat=MTLPixelFormatBGRA8Unorm;id<MTLTexture> display=[mtl newTextureWithDescriptor:d];
            pd.fragmentFunction=[libs[0] newFunctionWithName:@"fs_expose"];pd.rasterSampleCount=1;pd.colorAttachments[0].pixelFormat=d.pixelFormat;
            id<MTLRenderPipelineState> expose=[mtl newRenderPipelineStateWithDescriptor:pd error:&error];if(!expose)return 1;
            rp=[MTLRenderPassDescriptor renderPassDescriptor];rp.colorAttachments[0].texture=display;rp.colorAttachments[0].loadAction=MTLLoadActionDontCare;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
            e=[cb renderCommandEncoderWithDescriptor:rp];[e setRenderPipelineState:expose];[e setFragmentTexture:copy2 atIndex:0];[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
            [cb commit];[cb waitUntilCompleted];if(cb.status!=MTLCommandBufferStatusCompleted)return 1;
            if(mode<2) {
                // Off: mode 0 original draw, mode 1 renderer's copy. Both paths
                // use the exact original shader string when the switch is Off.
                [(mode==0?resolve:copy2) getBytes:off_bytes[mode] bytesPerRow:4104 fromRegion:region mipmapLevel:0];
                uint8_t output[4104];[display getBytes:output bytesPerRow:4104 fromRegion:region mipmapLevel:0];
                if(!mode)memcpy(legacy_display,output,sizeof output);
                else if(memcmp(legacy_display,output,sizeof output)){fprintf(stderr,"float_scene_test: Off exposed bytes differ\n");return 1;}
            } else {
                const uint32_t *v=proof.contents;
                const uint32_t *ms=v+(HC_MSAA*2+1)*8,*rs=v+(HC_RESOLVE*2+1)*8,*cp=v+(HC_COPY*2+1)*8;
                if(ms[0]!=1026*samples || rs[0]!=1026 || cp[0]!=1026 || ms[5] || rs[5] || cp[5])return 1;
                msaa_clip+=2688*samples-ms[1];resolve_clip+=2688-rs[1];copy_clip+=2688-cp[1];
                uint16_t values[4104];[copy2 getBytes:values bytesPerRow:8208 fromRegion:region mipmapLevel:0];
                for(unsigned x=0;x<1026;x++)for(unsigned c=0;c<4;c++){
                    __fp16 h;memcpy(&h,values+x*4+c,2);nonfinite+=!isfinite((float)h);
                    if(c==0&&x<1025){exact+=(float)h==x/128.0f;clipped+=x>128 && (float)h<=1.0f;}
                    if(x==1025 && (float)h!=0.0f)return 1;
                }
                uint8_t exposed[4104];[display getBytes:exposed bytesPerRow:4104 fromRegion:region mipmapLevel:0];
                for(unsigned x=0;x<1025;x++) {
                    // Compare actual GPU output from the legacy UNORM scene,
                    // retaining its conversion/tie rules (no CPU approximation).
                    unsigned changed=exposed[x*4]!=legacy_display[x*4];
                    if(x<=128)diff_low+=changed;else diff_high+=changed;
                }
            }
        }
        if(memcmp(off_bytes[0],off_bytes[1],4104)){fprintf(stderr,"float_scene_test: Off bytes differ\n");return 1;}
    }
    fprintf(stderr,"float_scene_test: mutant=%d exact=%u clipped=%u nonfinite=%u diff_low=%u diff_high=%u Off_bytes=8208 MSAA_clip=%u resolve_clip=%u copy_clip=%u\n",mutant,exact,clipped,nonfinite,diff_low,diff_high,msaa_clip,resolve_clip,copy_clip);
    return exact==2050 && !clipped && !nonfinite && !msaa_clip && !resolve_clip && !copy_clip?0:1;
    } @finally { float_scene_on=saved_fs;hdrclip_on=saved_hc; }
}
#endif
