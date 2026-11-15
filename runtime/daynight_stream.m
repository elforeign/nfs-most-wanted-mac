// RGBA8 lamp side stream. All data and shader changes require both switches.
#import <Foundation/Foundation.h>
#include <CommonCrypto/CommonDigest.h>
#include "daynight_stream.h"
#include "daynight_sky.h"
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>

static NSString *folder;
static NSDictionary *index_data;
static int configured;
static _Atomic int decided; // 0 not yet, 1 off, 2 on: the per-draw path reads this without the lock
static int active(void) {
    int d = atomic_load_explicit(&decided, memory_order_relaxed);
    if (d) return d == 2;
    @synchronized([NSProcessInfo class]) {
        if (!configured) {
            configured = 1;
            const char *e = getenv("NFS_DAYNIGHT"), *p = getenv("NFS_DAYNIGHT_BAKE");
            if (e && (!strcmp(e,"1") || !strcasecmp(e,"on")) && p && *p) {
                NSString *path = [NSString stringWithUTF8String:p];
                NSData *data = [NSData dataWithContentsOfFile:[path stringByAppendingPathComponent:@"index.json"]];
                id parsed = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL] : nil;
                if ([parsed isKindOfClass:[NSDictionary class]] && [parsed[@"version"] intValue] == 1 &&
                    [parsed[@"sections"] isKindOfClass:[NSDictionary class]] && [parsed[@"buffers"] isKindOfClass:[NSDictionary class]]) {
                    folder = path; index_data = parsed;
                }
            }
        }
    }
    atomic_store(&decided, folder != nil ? 2 : 1);
    return folder != nil;
}
static NSString *hex_digest(const unsigned char *digest) {
    char hex[65];
    for (unsigned i=0; i<32; ++i) snprintf(hex+i*2,3,"%02x",digest[i]);
    return [NSString stringWithUTF8String:hex];
}
static NSString *digest_data(const void *data, size_t length) {
    unsigned char digest[32]; CC_SHA256(data,(CC_LONG)length,digest); return hex_digest(digest);
}
static uint64_t tokens_hash(const uint32_t *tokens, uint32_t n) {
    uint64_t hash=1469598103934665603ull;
    for (size_t i=0;i<(size_t)n*4;++i) hash=(hash^((const uint8_t *)tokens)[i])*1099511628211ull;
    return hash;
}
// Hashes of complete original world/reflect/normal-map/window/billboard VSs.
static int world_shader(uint64_t hash) {
    switch(hash) {
        case 0x090160e8e9a32d2cull: // world/t0_world_p0_vs_1_1
        case 0xed211730ccb25f1eull: // world/t1_world_p0_vs_1_1
        case 0xbb253668a28870f5ull: // worldreflect/t0_world_reflect_noshadow_2_0_p0_vs_1_1
        case 0x8bbb8dfef2edae21ull: // worldreflect/t1_world_reflect_shadowhw_2_0_p0_vs_1_1
        case 0x668d008752809fa9ull: // worldreflect/t6_worldnormalmap_1_1_p0_vs_1_1
        case 0x6d1a1765677d50deull: // worldnormalmap/t0_worldnormalmap_2_0_p0_vs_1_1
        case 0x3883f8ffc7e49878ull: // worldnormalmap/t1_worldnormalmap_2_0_p0_vs_1_1
        case 0x6ec3dbaf015942f1ull: // worldnormalmap/t6_worldnormalmap_1_1_p0_vs_1_1
        case 0xeaa7aabc9baf0607ull: // glossywindow/t0_world_p0_vs_1_1
        case 0x3b7041897590c529ull: // glossywindow/t1_world_p0_vs_1_1
        case 0x74e7d294d4b2ecd6ull: // glossywindow/t2_world_p0_vs_3_0
        case 0x96681b7c39f397a7ull: // tree/t0_world_p0_vs_1_1
            return 1;
        default: return 0;
    }
}

char *daynight_stream_shader(const uint32_t *tokens, uint32_t n, const SmResult *refl, int *id, DaynightStreamProfile profile) {
    if (refl->shader_type != SM_VERTEX || !active() || !world_shader(tokens_hash(tokens,n))) return NULL;
    char *base = profile ? profile(tokens,n,refl,id,0) : NULL;
    NSMutableString *s = [NSMutableString stringWithUTF8String:base ? base : refl->msl];
    free(base);
    // Add after the original day-colour multiplication, before atmospheric extinction.
    // The output register differs for windows/trees; the decoded v2 multiply names it.
    NSRange marker = [s rangeOfString:@", r0, v2\n"];
    if (marker.location == NSNotFound) return NULL;
    NSRange line = [s lineRangeForRange:marker];
    NSString *comment = [s substringWithRange:line];
    const char *target = strstr(comment.UTF8String,"oD0") ? "so.col[0]" :
                         strstr(comment.UTF8String,"oD1") ? "so.col[1]" : "r0";
    NSRange body = [s lineRangeForRange:NSMakeRange(NSMaxRange(line),0)];
    [s insertString:[NSString stringWithFormat:@"    %s.rgb += si.dn_lamp;\n",target] atIndex:NSMaxRange(body)];
    [s replaceOccurrencesOfString:@"float4 v[16];" withString:@"float4 v[16]; float3 dn_lamp;" options:0 range:NSMakeRange(0,s.length)];
    NSString *signature=@"constant SmVSAux &aux [[buffer(19)]])";
    if ([s rangeOfString:signature].location == NSNotFound) return NULL;
    [s replaceOccurrencesOfString:signature withString:@"constant SmVSAux &aux [[buffer(19)]], device const uchar4 *dn_colours [[buffer(28)]], constant float &dn_night [[buffer(27)]], uint dn_vertex [[vertex_id]])" options:0 range:NSMakeRange(0,s.length)];
    [s replaceOccurrencesOfString:@"    SmVSIn si;" withString:@"    SmVSIn si;\n    constexpr float DN_LAMP_STRENGTH = 1.0f;\n    si.dn_lamp = float3(dn_colours[dn_vertex].rgb) * (dn_night * DN_LAMP_STRENGTH / 255.0f);" options:0 range:NSMakeRange(0,s.length)];
    return strdup(s.UTF8String);
}

@interface DNLampBinding : NSObject
@property uint64_t generation;
@property uint32_t stride, size, address;
@property NSDictionary *row;
@property int status;
@end
@implementation DNLampBinding
@end
static NSMapTable *bindings;
static NSMutableDictionary *sections;
static id<MTLBuffer> zero;
static uint64_t stream_frame, bound_count, missing_count, mismatch_count, bytes_held;
static uint32_t rd32(const uint8_t *b) { uint32_t v; memcpy(&v,b,4); return v; }

static id<MTLBuffer> load_section(id<MTLDevice> device, NSNumber *sn) {
    if (!sections) sections=[NSMutableDictionary new];
    id cached=sections[sn];
    if (cached) return cached == [NSNull null] ? nil : cached;
    NSDictionary *entry=index_data[@"sections"][sn.stringValue];
    NSString *name=[NSString stringWithFormat:@"section-%u.dnbake",sn.unsignedIntValue];
    NSData *data=[NSData dataWithContentsOfFile:[folder stringByAppendingPathComponent:name]];
    id<MTLBuffer> buffer=nil;
    if (entry && [entry[@"file"] isEqual:name] && data.length>=16 && data.length <= (64u<<20) &&
        [digest_data(data.bytes,data.length) isEqual:entry[@"sha256"]]) {
        const uint8_t *b=data.bytes;
        size_t pos=16, gp=0;
        uint32_t vertices=[entry[@"vertices"] unsignedIntValue];
        BOOL ok=vertices <= data.length/4 && !memcmp(b,"DNBK",4) && rd32(b+4)==1 && rd32(b+8)==sn.unsignedIntValue && rd32(b+12)==[entry[@"meshes"] unsignedIntValue];
        NSMutableData *colours=[NSMutableData dataWithLength:ok ? (size_t)vertices*4 : 0];
        for (uint32_t m=0;ok && m<rd32(b+12);++m) {
            if (pos+40>data.length) { ok=NO; break; }
            uint32_t count=rd32(b+pos+4);
            if (rd32(b+pos)!=m || count>(data.length-pos-40)/4 || count>(colours.length-gp)/4) { ok=NO; break; }
            pos+=40; memcpy((uint8_t *)colours.mutableBytes+gp,b+pos,(size_t)count*4);
            pos+=(size_t)count*4; gp+=(size_t)count*4;
        }
        ok=ok && pos==data.length && gp==colours.length;
        for (NSDictionary *row in entry[@"rows"]) {
            size_t off=[row[@"offset"] unsignedLongLongValue], go=[row[@"gpu_offset"] unsignedLongLongValue], n=[row[@"count"] unsignedLongLongValue];
            if (off>data.length || n>(data.length-off)/4 || go>gp || n>(gp-go)/4 ||
                ![digest_data(b+off,n*4) isEqual:row[@"colour_sha256"]] || memcmp(b+off,(uint8_t *)colours.bytes+go,n*4)) ok=NO;
        }
        if (ok && gp) buffer=[device newBufferWithBytes:colours.bytes length:gp options:MTLResourceStorageModeShared];
        if (buffer) { bytes_held+=gp; if(diag_on)diag_event("dnstream section=%u vertices=%u bytes=%zu held=%llu",sn.unsignedIntValue,vertices,gp,(unsigned long long)bytes_held); }
    }
    sections[sn]=buffer ?: (id)[NSNull null];
    if (!buffer && diag_on) diag_event("dnstream section=%u missing/invalid",sn.unsignedIntValue);
    return buffer;
}

// Shot-only source geometry keys and the live camera matrices. Kept independent
// of the bake switch so the Off frame can define the bake-derived rectangles.
static void shot_binding(Obj *vb, uint32_t stride, uint32_t offset, uint64_t hash,
                         uint64_t frame, const char *status, NSDictionary *row) {
    if (!diag_frame || !diag_frame_wanted(frame,"BOOT_DIAG_SHOTS") || !diag_dir ||
        !vb || stride<12 || !vb->mem[0] || vb->size%stride) return;
    static FILE *file;
    static uint64_t previous=~0ull;
    if(previous!=frame) {
        if(file)fclose(file);
        char path[1024];snprintf(path,sizeof path,"%s/dnstream_%05llu.jsonl",diag_dir,(unsigned long long)frame);
        file=fopen(path,"w");previous=frame;
    }
    if(!file)return;
    CC_SHA256_CTX ctx;CC_SHA256_Init(&ctx);
    for(uint32_t i=0;i<vb->size/stride;++i)CC_SHA256_Update(&ctx,G+vb->mem[0]+(size_t)i*stride,12);
    unsigned char digest[32];CC_SHA256_Final(digest,&ctx);
    uint32_t current=M32(0x00982A20u), vid=current?M32(current+4):99;
    uint32_t platform=vid<16?M32(0x009195E0u+vid*0x70u):0;
    fprintf(file,"{\"digest\":\"%s\",\"program\":\"%016llx\",\"count\":%u,\"stride\":%u,\"offset\":%u,\"status\":\"%s\",\"section\":%d,\"view_id\":%u,\"viewport\":[%u,%u,%u,%u]",
            hex_digest(digest).UTF8String,(unsigned long long)hash,vb->size/stride,stride,offset,status,
            row?[row[@"section"] intValue]:-1,vid,dev.viewport[0],dev.viewport[1],dev.viewport[2],dev.viewport[3]);
    const char *names[]={"view","projection","view_projection","vsc","psc"};
    const float *values[]={platform?(const float *)(G+platform):NULL,platform?(const float *)(G+platform+0x40):NULL,
        platform?(const float *)(G+platform+0x80):NULL,&dev.vsc[0][0],&dev.psc[0][0]};
    for(unsigned a=0;a<5;++a) {
        fprintf(file,",\"%s\":[",names[a]);
        unsigned n=a<3?16:a==3?128:32;
        for(unsigned i=0;i<n;++i)fprintf(file,"%s%.9g",i?",":"",values[a]?values[a][i]:0);
        fputc(']',file);
    }
    fputs("}\n",file);fflush(file);
}

void daynight_stream_bind(id<MTLRenderCommandEncoder> encoder, id<MTLDevice> device,
                         Obj *vb, id<MTLBuffer> identity, uint32_t stride, uint32_t offset,
                         uint64_t shader_hash, uint64_t frame) {
    if (!world_shader(shader_hash)) return;
    if (!active()) {
        if(diag_on)shot_binding(vb,stride,offset,shader_hash,frame,"disabled",nil);
        return;
    }
    if (diag_on && stream_frame != frame) {
        if (diag_frame && diag_frame_wanted(stream_frame,"BOOT_DIAG_SHOTS"))
            diag_event("dnstream frame=%llu bound=%llu missing=%llu mismatched=%llu held=%llu",(unsigned long long)stream_frame,(unsigned long long)bound_count,(unsigned long long)missing_count,(unsigned long long)mismatch_count,(unsigned long long)bytes_held);
        stream_frame=frame; bound_count=missing_count=mismatch_count=0;
    }
    uint32_t count=vb && stride>=12 ? vb->size/stride : 0;
    if (!bindings) bindings=[NSMapTable weakToStrongObjectsMapTable];
    DNLampBinding *binding=identity ? [bindings objectForKey:identity] : nil;
    if (!binding || binding.generation!=vb->buffer_generation || binding.stride!=stride || binding.size!=vb->size || binding.address!=vb->mem[0]) {
        binding=[DNLampBinding new]; binding.generation=vb ? vb->buffer_generation : 0;
        binding.stride=stride; binding.size=vb ? vb->size : 0; binding.address=vb ? vb->mem[0] : 0;
        binding.status=1;
        if (count && vb->size%stride==0 && vb->mem[0]) {
            CC_SHA256_CTX ctx; CC_SHA256_Init(&ctx);
            const uint8_t *b=G+vb->mem[0];
            for (uint32_t i=0;i<count;++i) CC_SHA256_Update(&ctx,b+(size_t)i*stride,12);
            unsigned char digest[32]; CC_SHA256_Final(digest,&ctx);
            NSDictionary *row=index_data[@"buffers"][hex_digest(digest)];
            if (row) {
                binding.status=[row[@"count"] unsignedIntValue]==count ? 0 : 2;
                if (!binding.status) binding.row=row;
            }
        }
        if(identity)[bindings setObject:binding forKey:identity];
    }
    NSDictionary *row=binding.row;
    id<MTLBuffer> buffer=row ? load_section(device,row[@"section"]) : nil;
    NSUInteger off=row ? [row[@"gpu_offset"] unsignedLongLongValue]+(size_t)(offset/stride)*4 : 0;
    if (offset%stride || off>buffer.length || (row && [row[@"count"] unsignedIntValue]!=count)) buffer=nil;
    if (!buffer) {
        NSUInteger length=MAX((size_t)count*4,4u);
        if(zero.length<length) { zero=[device newBufferWithLength:length options:MTLResourceStorageModeShared]; memset(zero.contents,0,length); }
        buffer=zero; off=0;
        if(diag_on) { if(binding.status==2)++mismatch_count; else ++missing_count; }
    } else if(diag_on) ++bound_count;
    float night=0;
    if (M32(0x00925E90u)==6) {
        uint32_t tod=M32(0x009B392Cu);
        if(tod)night=daynight_sky_factor(MF32(tod+8),MF32(tod+0x188),MF32(tod+0x10));
    }
    if(diag_on)shot_binding(vb,stride,offset,shader_hash,frame,
        buffer==zero?(binding.status==2?"count-mismatch":row?"section-invalid":"key-missing"):"bound",row);
    [encoder setVertexBuffer:buffer offset:off atIndex:28];
    [encoder setVertexBytes:&night length:sizeof night atIndex:27];
}
