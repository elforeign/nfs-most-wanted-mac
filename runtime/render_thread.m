// render_thread.m - NFS_RENDER_THREAD=1: Metal encoding on a host render thread. The game thread still does all D3D->Metal translation, uploads and object creation; every
// command buffer it asks for (rt_command_buffer) is a recorder whose encoders append compact ops (words: kind | size,
// then arguments; set*Bytes copied inline; objects retained once per buffer) instead of calling the driver. commit
// hands the recording to one render thread that replays it into a real command buffer in commit order, adds the
// recorded completed handlers and commits. The bound stays the existing one: render_present waits for the previous
// frame's completion, so at most one recorded frame waits for replay while the next one records.
// Default on (solo A/B: game thread -1.15 ms busy p50). NFS_RENDER_THREAD=0: rt_command_buffer is [queue commandBuffer] and rt_after_commit runs its block at once.
#import <Metal/Metal.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include "d3d9_internal.h"

int rt_on = -1;

enum { OP_RENC = 1, OP_BENC, OP_BENCD, OP_END, OP_LABEL, OP_PSO, OP_DSS, OP_FTEX, OP_VTEX, OP_FSAMP, OP_VSAMP, OP_VBUF, OP_FBUF,
       OP_VBOFF, OP_FBOFF, OP_VBYTES, OP_FBYTES, OP_VIEWPORT, OP_SCISSOR, OP_CULL, OP_WINDING, OP_BIAS, OP_STENCIL, OP_BLENDC,
       OP_VIS, OP_FILL, OP_DRAW, OP_DRAWI, OP_DRAWIX, OP_COPY_TT, OP_COPY_TTR, OP_COPY_TB, OP_RLIST, OP_MIPMAPS };

@interface RTCmd : NSObject {
@public
    uint64_t *w; size_t n, cap;          // op words
    void **set; size_t nset, capset;     // objects retained by this recording (open addressing)
    NSMutableArray *handlers, *after, *owned;
    id<MTLCommandBuffer> real;
    uint64_t t_commit;                   // host_ticks at the recorder's commit (latency stat)
    int state;                           // 0 recording, 1 committed (queued), 2 replayed and really committed
    RTCmd *next;
}
@end
@interface RTRender : NSObject { @public RTCmd *c; NSString *lab; }
@end
@interface RTBlit : NSObject { @public RTCmd *c; NSString *lab; }
@end

static pthread_mutex_t rq_mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t rq_cv = PTHREAD_COND_INITIALIZER, rq_done = PTHREAD_COND_INITIALIZER;
static RTCmd *rq_head, *rq_tail;
// Latency the thread adds: recorder commit -> real commit (queue wait + replay), and the replay alone, per buffer.
static uint64_t st_n, st_lat, st_lat_max, st_rep;

static inline uint64_t *op(RTCmd *c, unsigned kind, size_t nw) {
    if (c->n + nw + 1 > c->cap) {
        c->cap = (c->n + nw + 1) * 2 > 65536 ? (c->n + nw + 1) * 2 : 65536;
        c->w = realloc(c->w, c->cap * 8);
    }
    uint64_t *p = c->w + c->n;
    p[0] = kind | (uint64_t)(nw + 1) << 8;
    c->n += nw + 1;
    return p + 1;
}
// Retain an object once per recording; released after the replay (the real command buffer retains what it uses).
static inline uint64_t obj_w(RTCmd *c, id o) {
    void *p = (__bridge void *)o;
    if (!p) return 0;
    if (c->nset * 2 >= c->capset) {
        size_t oc = c->capset; void **os = c->set;
        c->capset = oc ? oc * 2 : 1024; c->set = calloc(c->capset, sizeof(void *)); c->nset = 0;
        for (size_t i = 0; i < oc; ++i) if (os[i]) {
            size_t h = ((uintptr_t)os[i] >> 4) * 0x9E3779B97F4A7C15ull >> 20 & (c->capset - 1);
            while (c->set[h]) h = (h + 1) & (c->capset - 1);
            c->set[h] = os[i]; ++c->nset;
        }
        free(os);
    }
    size_t h = ((uintptr_t)p >> 4) * 0x9E3779B97F4A7C15ull >> 20 & (c->capset - 1);
    while (c->set[h]) { if (c->set[h] == p) return (uint64_t)(uintptr_t)p; h = (h + 1) & (c->capset - 1); }
    c->set[h] = p; ++c->nset;
    CFRetain((CFTypeRef)p);
    return (uint64_t)(uintptr_t)p;
}
#define O(x) ((__bridge id)(void *)(uintptr_t)(x))

@implementation RTCmd
- (void)dealloc {
    // Standalone render lists are retained by their parent frame until replay.
    // Normal command buffers have already released these in replay().
    if (set) for (size_t j=0;j<capset;++j) if(set[j]) CFRelease((CFTypeRef)set[j]);
    free(set);free(w);
}
- (id<MTLDevice>)device { return mtl; }
- (id<MTLRenderCommandEncoder>)renderCommandEncoderWithDescriptor:(MTLRenderPassDescriptor *)d {
    MTLRenderPassDescriptor *k = [d copy];
    [owned addObject:k];
    op(self, OP_RENC, 1)[0] = (uint64_t)(uintptr_t)(__bridge void *)k;
    RTRender *e = [RTRender new]; e->c = self;
    return (id<MTLRenderCommandEncoder>)e;
}
- (id<MTLBlitCommandEncoder>)blitCommandEncoder {
    op(self, OP_BENC, 0);
    RTBlit *e = [RTBlit new]; e->c = self;
    return (id<MTLBlitCommandEncoder>)e;
}
- (id<MTLBlitCommandEncoder>)blitCommandEncoderWithDescriptor:(MTLBlitPassDescriptor *)d {
    MTLBlitPassDescriptor *k = [d copy];
    [owned addObject:k];
    op(self, OP_BENCD, 1)[0] = (uint64_t)(uintptr_t)(__bridge void *)k;
    RTBlit *e = [RTBlit new]; e->c = self;
    return (id<MTLBlitCommandEncoder>)e;
}
- (void)addCompletedHandler:(MTLCommandBufferHandler)h { [handlers addObject:[h copy]]; }
- (void)commit {
    t_commit = host_ticks();
    pthread_mutex_lock(&rq_mu);
    state = 1;
    if (rq_tail) rq_tail->next = self; else rq_head = self;
    rq_tail = self;
    CFRetain((__bridge CFTypeRef)self);
    pthread_cond_signal(&rq_cv);
    pthread_mutex_unlock(&rq_mu);
}
- (void)waitUntilCompleted {
    pthread_mutex_lock(&rq_mu);
    while (state != 2) pthread_cond_wait(&rq_done, &rq_mu);
    id<MTLCommandBuffer> r = real;
    pthread_mutex_unlock(&rq_mu);
    [r waitUntilCompleted];
}
- (void)doesNotRecognizeSelector:(SEL)s { fprintf(stderr, "boot: render thread: command buffer recorder lacks %s\n", sel_getName(s)); abort(); }
@end

#define REC(kind, nw) uint64_t *a = op(c, kind, nw)
@implementation RTRender
- (void)setLabel:(NSString *)s { lab = s; REC(OP_LABEL, 1); [c->owned addObject:s]; a[0] = (uint64_t)(uintptr_t)(__bridge void *)s; }
- (NSString *)label { return lab; }
- (void)endEncoding { op(c, OP_END, 0); }
- (void)setRenderPipelineState:(id<MTLRenderPipelineState>)s { REC(OP_PSO, 1); a[0] = obj_w(c, s); }
- (void)setDepthStencilState:(id<MTLDepthStencilState>)s { REC(OP_DSS, 1); a[0] = obj_w(c, s); }
- (void)setFragmentTexture:(id<MTLTexture>)t atIndex:(NSUInteger)i { REC(OP_FTEX, 2); a[0] = obj_w(c, t); a[1] = i; }
- (void)setVertexTexture:(id<MTLTexture>)t atIndex:(NSUInteger)i { REC(OP_VTEX, 2); a[0] = obj_w(c, t); a[1] = i; }
- (void)setFragmentSamplerState:(id<MTLSamplerState>)t atIndex:(NSUInteger)i { REC(OP_FSAMP, 2); a[0] = obj_w(c, t); a[1] = i; }
- (void)setVertexSamplerState:(id<MTLSamplerState>)t atIndex:(NSUInteger)i { REC(OP_VSAMP, 2); a[0] = obj_w(c, t); a[1] = i; }
- (void)setVertexBuffer:(id<MTLBuffer>)b offset:(NSUInteger)o atIndex:(NSUInteger)i { REC(OP_VBUF, 3); a[0] = obj_w(c, b); a[1] = o; a[2] = i; }
- (void)setFragmentBuffer:(id<MTLBuffer>)b offset:(NSUInteger)o atIndex:(NSUInteger)i { REC(OP_FBUF, 3); a[0] = obj_w(c, b); a[1] = o; a[2] = i; }
- (void)setVertexBufferOffset:(NSUInteger)o atIndex:(NSUInteger)i { REC(OP_VBOFF, 2); a[0] = o; a[1] = i; }
- (void)setFragmentBufferOffset:(NSUInteger)o atIndex:(NSUInteger)i { REC(OP_FBOFF, 2); a[0] = o; a[1] = i; }
// Rectangle clears use these inline copies plus OP_SCISSOR: replay never reads guest rects or stack clear values.
- (void)setVertexBytes:(const void *)p length:(NSUInteger)n atIndex:(NSUInteger)i {
    REC(OP_VBYTES, 2 + (n + 7) / 8); a[0] = n; a[1] = i; memcpy(a + 2, p, n);
}
- (void)setFragmentBytes:(const void *)p length:(NSUInteger)n atIndex:(NSUInteger)i {
    REC(OP_FBYTES, 2 + (n + 7) / 8); a[0] = n; a[1] = i; memcpy(a + 2, p, n);
}
- (void)setViewport:(MTLViewport)v { REC(OP_VIEWPORT, 6); memcpy(a, &v, sizeof v); }
- (void)setScissorRect:(MTLScissorRect)r { REC(OP_SCISSOR, 4); memcpy(a, &r, sizeof r); }
- (void)setCullMode:(MTLCullMode)m { REC(OP_CULL, 1); a[0] = m; }
- (void)setFrontFacingWinding:(MTLWinding)m { REC(OP_WINDING, 1); a[0] = m; }
- (void)setTriangleFillMode:(MTLTriangleFillMode)m { REC(OP_FILL, 1); a[0] = m; }
- (void)setDepthBias:(float)b slopeScale:(float)s clamp:(float)k { REC(OP_BIAS, 3); memcpy(a, &b, 4); memcpy(a + 1, &s, 4); memcpy(a + 2, &k, 4); }
- (void)setStencilReferenceValue:(uint32_t)v { REC(OP_STENCIL, 1); a[0] = v; }
- (void)setBlendColorRed:(float)r green:(float)g blue:(float)b alpha:(float)al {
    REC(OP_BLENDC, 4); memcpy(a, &r, 4); memcpy(a + 1, &g, 4); memcpy(a + 2, &b, 4); memcpy(a + 3, &al, 4);
}
- (void)setVisibilityResultMode:(MTLVisibilityResultMode)m offset:(NSUInteger)o { REC(OP_VIS, 2); a[0] = m; a[1] = o; }
- (void)drawPrimitives:(MTLPrimitiveType)t vertexStart:(NSUInteger)s vertexCount:(NSUInteger)n { REC(OP_DRAW, 4); a[0] = t; a[1] = s; a[2] = n; a[3] = 1; }
- (void)drawPrimitives:(MTLPrimitiveType)t vertexStart:(NSUInteger)s vertexCount:(NSUInteger)n instanceCount:(NSUInteger)k {
    REC(OP_DRAW, 4); a[0] = t; a[1] = s; a[2] = n; a[3] = k;
}
- (void)drawIndexedPrimitives:(MTLPrimitiveType)t indexCount:(NSUInteger)n indexType:(MTLIndexType)it indexBuffer:(id<MTLBuffer>)b
            indexBufferOffset:(NSUInteger)o {
    REC(OP_DRAWI, 5); a[0] = t; a[1] = n; a[2] = it; a[3] = obj_w(c, b); a[4] = o;
}
- (void)drawIndexedPrimitives:(MTLPrimitiveType)t indexCount:(NSUInteger)n indexType:(MTLIndexType)it indexBuffer:(id<MTLBuffer>)b
            indexBufferOffset:(NSUInteger)o instanceCount:(NSUInteger)k baseVertex:(NSInteger)bv baseInstance:(NSUInteger)bi {
    REC(OP_DRAWIX, 8); a[0] = t; a[1] = n; a[2] = it; a[3] = obj_w(c, b); a[4] = o; a[5] = k; a[6] = (uint64_t)bv; a[7] = bi;
}
- (void)doesNotRecognizeSelector:(SEL)s { fprintf(stderr, "boot: render thread: render encoder recorder lacks %s\n", sel_getName(s)); abort(); }
@end

@implementation RTBlit
- (void)generateMipmapsForTexture:(id<MTLTexture>)t { REC(OP_MIPMAPS, 1); a[0] = obj_w(c, t); }
- (void)setLabel:(NSString *)s { lab = s; REC(OP_LABEL, 1); [c->owned addObject:s]; a[0] = (uint64_t)(uintptr_t)(__bridge void *)s; }
- (NSString *)label { return lab; }
- (void)endEncoding { op(c, OP_END, 0); }
- (void)copyFromTexture:(id<MTLTexture>)s toTexture:(id<MTLTexture>)d { REC(OP_COPY_TT, 2); a[0] = obj_w(c, s); a[1] = obj_w(c, d); }
- (void)copyFromTexture:(id<MTLTexture>)s sourceSlice:(NSUInteger)ss sourceLevel:(NSUInteger)sl sourceOrigin:(MTLOrigin)so sourceSize:(MTLSize)sz
              toTexture:(id<MTLTexture>)d destinationSlice:(NSUInteger)ds destinationLevel:(NSUInteger)dl destinationOrigin:(MTLOrigin)dorg {
    REC(OP_COPY_TTR, 15); a[0] = obj_w(c, s); a[1] = ss; a[2] = sl; memcpy(a + 3, &so, 24); memcpy(a + 6, &sz, 24);
    a[9] = obj_w(c, d); a[10] = ds; a[11] = dl; memcpy(a + 12, &dorg, 24);
}
- (void)copyFromTexture:(id<MTLTexture>)s sourceSlice:(NSUInteger)ss sourceLevel:(NSUInteger)sl sourceOrigin:(MTLOrigin)so sourceSize:(MTLSize)sz
               toBuffer:(id<MTLBuffer>)b destinationOffset:(NSUInteger)o destinationBytesPerRow:(NSUInteger)bpr
destinationBytesPerImage:(NSUInteger)bpi options:(MTLBlitOption)opt {
    REC(OP_COPY_TB, 14); a[0] = obj_w(c, s); a[1] = ss; a[2] = sl; memcpy(a + 3, &so, 24); memcpy(a + 6, &sz, 24);
    a[9] = obj_w(c, b); a[10] = o; a[11] = bpr; a[12] = bpi; a[13] = opt;
}
- (void)copyFromTexture:(id<MTLTexture>)s sourceSlice:(NSUInteger)ss sourceLevel:(NSUInteger)sl sourceOrigin:(MTLOrigin)so sourceSize:(MTLSize)sz
               toBuffer:(id<MTLBuffer>)b destinationOffset:(NSUInteger)o destinationBytesPerRow:(NSUInteger)bpr
destinationBytesPerImage:(NSUInteger)bpi {
    [self copyFromTexture:s sourceSlice:ss sourceLevel:sl sourceOrigin:so sourceSize:sz toBuffer:b destinationOffset:o
   destinationBytesPerRow:bpr destinationBytesPerImage:bpi options:MTLBlitOptionNone];
}
- (void)doesNotRecognizeSelector:(SEL)s { fprintf(stderr, "boot: render thread: blit encoder recorder lacks %s\n", sel_getName(s)); abort(); }
@end

// The same typed op stream serves ordinary frames and deferred coverage lists.
// Lists capture bytes inline and retain resources once; they never redo D3D translation.
static int replay_render_op(unsigned k,const uint64_t *a,id<MTLRenderCommandEncoder> r);
static void replay_render_list(RTCmd *c,id<MTLRenderCommandEncoder> r) {
    for(size_t i=0;i<c->n;){const uint64_t *w=c->w+i;i+=w[0]>>8;
        if(!replay_render_op(w[0]&255,w+1,r)){fprintf(stderr,"boot: bad render list op %u\n",(unsigned)(w[0]&255));abort();}}
}
static int replay_render_op(unsigned k,const uint64_t *a,id<MTLRenderCommandEncoder> r) {
    float f[4];
    switch(k){
        case OP_LABEL:r.label=O(a[0]);break;
        case OP_RLIST:replay_render_list((RTCmd *)O(a[0]),r);break;
        case OP_PSO: [r setRenderPipelineState:O(a[0])]; break;
        case OP_DSS: [r setDepthStencilState:O(a[0])]; break;
        case OP_FTEX: [r setFragmentTexture:O(a[0]) atIndex:a[1]]; break;
        case OP_VTEX: [r setVertexTexture:O(a[0]) atIndex:a[1]]; break;
        case OP_FSAMP: [r setFragmentSamplerState:O(a[0]) atIndex:a[1]]; break;
        case OP_VSAMP: [r setVertexSamplerState:O(a[0]) atIndex:a[1]]; break;
        case OP_VBUF: [r setVertexBuffer:O(a[0]) offset:a[1] atIndex:a[2]]; break;
        case OP_FBUF: [r setFragmentBuffer:O(a[0]) offset:a[1] atIndex:a[2]]; break;
        case OP_VBOFF: [r setVertexBufferOffset:a[0] atIndex:a[1]]; break;
        case OP_FBOFF: [r setFragmentBufferOffset:a[0] atIndex:a[1]]; break;
        case OP_VBYTES: [r setVertexBytes:a + 2 length:a[0] atIndex:a[1]]; break;
        case OP_FBYTES: [r setFragmentBytes:a + 2 length:a[0] atIndex:a[1]]; break;
        case OP_VIEWPORT: { MTLViewport v; memcpy(&v, a, sizeof v); [r setViewport:v]; break; }
        case OP_SCISSOR: { MTLScissorRect s; memcpy(&s, a, sizeof s); [r setScissorRect:s]; break; }
        case OP_CULL: [r setCullMode:(MTLCullMode)a[0]]; break;
        case OP_WINDING: [r setFrontFacingWinding:(MTLWinding)a[0]]; break;
        case OP_FILL: [r setTriangleFillMode:(MTLTriangleFillMode)a[0]]; break;
        case OP_BIAS: memcpy(f, a, 4); memcpy(f + 1, a + 1, 4); memcpy(f + 2, a + 2, 4); [r setDepthBias:f[0] slopeScale:f[1] clamp:f[2]]; break;
        case OP_STENCIL: [r setStencilReferenceValue:(uint32_t)a[0]]; break;
        case OP_BLENDC: for (int j = 0; j < 4; ++j) memcpy(f + j, a + j, 4); [r setBlendColorRed:f[0] green:f[1] blue:f[2] alpha:f[3]]; break;
        case OP_VIS: [r setVisibilityResultMode:(MTLVisibilityResultMode)a[0] offset:a[1]]; break;
        case OP_DRAW: [r drawPrimitives:(MTLPrimitiveType)a[0] vertexStart:a[1] vertexCount:a[2] instanceCount:a[3]]; break;
        case OP_DRAWI: [r drawIndexedPrimitives:(MTLPrimitiveType)a[0] indexCount:a[1] indexType:(MTLIndexType)a[2] indexBuffer:O(a[3]) indexBufferOffset:a[4]]; break;
        case OP_DRAWIX:
            [r drawIndexedPrimitives:(MTLPrimitiveType)a[0] indexCount:a[1] indexType:(MTLIndexType)a[2] indexBuffer:O(a[3]) indexBufferOffset:a[4]
                       instanceCount:a[5] baseVertex:(NSInteger)a[6] baseInstance:a[7]];
            break;
        default:return 0;
    }
    return 1;
}
static RTCmd *render_list_cmd(void){RTCmd *c=[RTCmd new];c->owned=[NSMutableArray new];return c;}
id rt_render_list_new(void){RTRender *r=[RTRender new];r->c=render_list_cmd();return r;}
unsigned rt_render_list_ops(id list){return list?(unsigned)((RTRender *)list)->c->n:0;}
void rt_render_list_replay(id list,id<MTLRenderCommandEncoder> encoder){
    if(!list)return;
    RTRender *r=list;RTCmd *recording=r->c;
    if(encoder){
        if([(id)encoder isKindOfClass:[RTRender class]]){
            RTCmd *target=((RTRender *)encoder)->c;
            op(target,OP_RLIST,1)[0]=obj_w(target,recording);
        }else replay_render_list(recording,encoder);
    }
    // Detach the immutable snapshot: later draws and discard cannot mutate an
    // already queued frame. The render thread releases its resource ownership.
    r->c=render_list_cmd();
}

static void replay(RTCmd *c) {
    uint64_t t0 = host_ticks();
    id<MTLCommandBuffer> real = [queue commandBuffer];
    id<MTLRenderCommandEncoder> r = nil;
    id<MTLBlitCommandEncoder> b = nil;
    for (size_t i = 0; i < c->n;) {
        const uint64_t *w = c->w + i;
        unsigned k = (unsigned)(w[0] & 255);
        i += w[0] >> 8;
        const uint64_t *a = w + 1;
        switch (k) {
        case OP_RENC: r = [real renderCommandEncoderWithDescriptor:O(a[0])]; break;
        case OP_BENC: b = [real blitCommandEncoder]; break;
        case OP_BENCD: b = [real blitCommandEncoderWithDescriptor:O(a[0])]; break;
        case OP_END: if (r) [r endEncoding]; if (b) [b endEncoding]; r = nil; b = nil; break;
        case OP_LABEL: if (r) r.label = O(a[0]); else if (b) b.label = O(a[0]); break;
        case OP_COPY_TT: [b copyFromTexture:O(a[0]) toTexture:O(a[1])]; break;
        case OP_MIPMAPS: [b generateMipmapsForTexture:O(a[0])]; break;
        case OP_COPY_TTR: {
            MTLOrigin so, dorg; MTLSize sz; memcpy(&so, a + 3, 24); memcpy(&sz, a + 6, 24); memcpy(&dorg, a + 12, 24);
            [b copyFromTexture:O(a[0]) sourceSlice:a[1] sourceLevel:a[2] sourceOrigin:so sourceSize:sz toTexture:O(a[9])
              destinationSlice:a[10] destinationLevel:a[11] destinationOrigin:dorg];
            break;
        }
        case OP_COPY_TB: {
            MTLOrigin so; MTLSize sz; memcpy(&so, a + 3, 24); memcpy(&sz, a + 6, 24);
            [b copyFromTexture:O(a[0]) sourceSlice:a[1] sourceLevel:a[2] sourceOrigin:so sourceSize:sz toBuffer:O(a[9])
             destinationOffset:a[10] destinationBytesPerRow:a[11] destinationBytesPerImage:a[12] options:(MTLBlitOption)a[13]];
            break;
        }
        default: if(!replay_render_op(k,a,r)){fprintf(stderr, "boot: render thread: bad op %u\n", k); abort();}
        }
    }
    for (MTLCommandBufferHandler h in c->handlers) [real addCompletedHandler:h];
    [real commit];
    uint64_t t1 = host_ticks();
    pthread_mutex_lock(&rq_mu);
    ++st_n; st_lat += t1 - c->t_commit; st_rep += t1 - t0;
    if (t1 - c->t_commit > st_lat_max) st_lat_max = t1 - c->t_commit;
    c->real = real;
    c->state = 2;
    NSArray *after = c->after;
    c->after = nil;
    pthread_cond_broadcast(&rq_done);
    pthread_mutex_unlock(&rq_mu);
    for (dispatch_block_t blk in after) blk();
    for (size_t j = 0; j < c->capset; ++j) if (c->set[j]) CFRelease((CFTypeRef)c->set[j]);
    free(c->set); c->set = NULL; free(c->w); c->w = NULL;
    c->handlers = nil; c->owned = nil;
}

static void *render_thread(void *arg) {
    (void)arg;
    pthread_setname_np("nfsmw.render");
    for (;;) {
        pthread_mutex_lock(&rq_mu);
        while (!rq_head) pthread_cond_wait(&rq_cv, &rq_mu);
        RTCmd *c = rq_head;
        rq_head = c->next; if (!rq_head) rq_tail = nil;
        c->next = nil;
        pthread_mutex_unlock(&rq_mu);
        @autoreleasepool { replay(c); }
        CFRelease((__bridge CFTypeRef)c);
    }
    return NULL;
}

static void rt_init(void) {
    const char *e = getenv("NFS_RENDER_THREAD");
    rt_on = !(e && *e == '0');                     // default on (solo A/B verified)
    if (!rt_on) return;
    pthread_attr_t at;
    pthread_attr_init(&at);
    pthread_attr_set_qos_class_np(&at, QOS_CLASS_USER_INTERACTIVE, 0);
    pthread_t t;
    pthread_create(&t, &at, render_thread, NULL);
    pthread_attr_destroy(&at);
    fprintf(stderr, "boot: render: Metal encoding on a render thread (NFS_RENDER_THREAD=0 turns it off)\n");
}

id<MTLCommandBuffer> rt_command_buffer(void) {
    if (rt_on < 0) rt_init();
    if (!rt_on) return [queue commandBuffer];
    RTCmd *c = [RTCmd new];
    c->handlers = [NSMutableArray new]; c->owned = [NSMutableArray new]; c->after = [NSMutableArray new];
    return (id<MTLCommandBuffer>)c;
}
// Runs blk once cb has really been committed (recorded buffers: on the render thread right after its commit), so work
// that commits its own command buffer elsewhere (the present queue) stays behind it in the queue.
void rt_after_commit(id<MTLCommandBuffer> cb, dispatch_block_t blk) {
    if (![(id)cb isKindOfClass:[RTCmd class]]) { blk(); return; }
    RTCmd *c = (RTCmd *)cb;
    pthread_mutex_lock(&rq_mu);
    if (c->state != 2) { [c->after addObject:[blk copy]]; blk = nil; }
    pthread_mutex_unlock(&rq_mu);
    if (blk) blk();
}
// BOOT_DIAG, once a second from render_present: "render thread:" buffers, mean/max commit->real commit, mean replay.
void rt_stats_log(void) {
    static uint64_t since;
    if (rt_on != 1 || !diag_on) return;
    uint64_t now = host_ticks();
    if (!since) since = now;
    if (now - since < 1000000000ull) return;
    since = now;
    pthread_mutex_lock(&rq_mu);
    uint64_t n = st_n, lat = st_lat, mx = st_lat_max, rep = st_rep;
    st_n = st_lat = st_lat_max = st_rep = 0;
    pthread_mutex_unlock(&rq_mu);
    if (n) diag_event("render thread: buffers=%llu commit_to_gpu_ms mean=%.3f max=%.3f replay_ms mean=%.3f", (unsigned long long)n,
                      lat / 1e6 / n, mx / 1e6, rep / 1e6 / n);
}
