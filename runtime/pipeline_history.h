// Pipelines met on demand, remembered across launches in USERDATA/pipelinecache.bin and replayed at load on
// the catalogue's background queues. Records hold only stable identities: translated shader keys (bytecode,
// options and variant hashes) and API state at their concrete sample count. Library-keyed states carry process
// pointers and are never recorded. Structural defects discard the file; invalid recipes are skipped.
// Included by pipeline_cache.m; inert until pipeline_history_open().
#include <sys/file.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <errno.h>

#define HISTORY_MAGIC 0x4850464Eu // "NFPH"
#define HISTORY_VERSION 2u        // bump when PipelineRecipe fields change meaning
#define HISTORY_MAX 4096
typedef struct { uint32_t magic, record_size; uint64_t stamp, count; } HistoryHeader;
typedef struct { PipelineRecipe recipe; uint64_t check; } HistoryRecord;
_Static_assert(sizeof(HistoryHeader)==24 && sizeof(HistoryRecord)==824, "history records have no implicit padding");
static NSMutableSet<NSData *> *history; // recorded identities; guarded by pipeline_cache_mu
static dispatch_queue_t history_q;      // file appends, off the frame path
static char history_file[1024];
static uint64_t history_stamp;
static size_t history_replayed, history_skipped;
static const char *history_defect;

static uint64_t history_hash(const void *p, size_t n, uint64_t h) {
    for (size_t i=0;i<n;++i) h=(h^((const uint8_t *)p)[i])*1099511628211ull;
    return h;
}
// A driver or OS change discards the history: the device name, OS build and format version form the stamp.
static uint64_t history_device_stamp(void) {
    NSString *s=[NSString stringWithFormat:@"%u|%zu|%@|%@",HISTORY_VERSION,sizeof(HistoryRecord),
        mtl.name,NSProcessInfo.processInfo.operatingSystemVersionString];
    return history_hash(s.UTF8String,strlen(s.UTF8String),1469598103934665603ull);
}
// Only states produced by render.m's format/blend/vertex mappings are replayable.
// Metal has 31 attribute/layout slots, offsets <= 2047 and strides <= 2048.
static int history_recipe_valid(const PipelineRecipe *r) {
    if (!r->vertex || !r->fragment || r->reserved || r->a2c>1 || r->blend>1 || r->mask>15 ||
        (r->samples!=1 && r->samples!=2 && r->samples!=4 && r->samples!=8) ||
        ![mtl supportsTextureSampleCount:r->samples] ||
        r->srcRGB>MTLBlendFactorOneMinusBlendColor || r->dstRGB>MTLBlendFactorOneMinusBlendColor ||
        r->srcAlpha>MTLBlendFactorOneMinusBlendColor || r->dstAlpha>MTLBlendFactorOneMinusBlendColor ||
        r->opRGB>MTLBlendOperationMax || r->opAlpha>MTLBlendOperationMax) return 0;
    switch (r->color) {
        case MTLPixelFormatInvalid: case MTLPixelFormatA8Unorm: case MTLPixelFormatR8Unorm:
        case MTLPixelFormatRG8Unorm: case MTLPixelFormatRG16Unorm: case MTLPixelFormatRGBA16Unorm:
        case MTLPixelFormatBGRA8Unorm: case MTLPixelFormatRGBA8Unorm:
        case MTLPixelFormatR16Float: case MTLPixelFormatRG16Float: case MTLPixelFormatRGBA16Float:
        case MTLPixelFormatR32Float: case MTLPixelFormatRG32Float: case MTLPixelFormatRGBA32Float: break;
        default: return 0;
    }
    if (r->depth!=MTLPixelFormatInvalid && r->depth!=MTLPixelFormatDepth16Unorm &&
        r->depth!=MTLPixelFormatDepth32Float && r->depth!=MTLPixelFormatDepth32Float_Stencil8) return 0;
    if (r->stencil && (r->stencil!=MTLPixelFormatDepth32Float_Stencil8 || r->depth!=r->stencil)) return 0;
    for (int i=0;i<31;++i) {
        PipelineAttribute a=r->attributes[i]; PipelineLayout l=r->layouts[i];
        if (a.buffer>=31 || a.offset>2047) return 0;
        switch (a.format) {
            case MTLVertexFormatInvalid: case MTLVertexFormatFloat: case MTLVertexFormatFloat2:
            case MTLVertexFormatFloat3: case MTLVertexFormatFloat4: case MTLVertexFormatUChar4Normalized_BGRA:
            case MTLVertexFormatUChar4: case MTLVertexFormatShort2: case MTLVertexFormatShort4:
            case MTLVertexFormatUChar4Normalized: case MTLVertexFormatShort2Normalized:
            case MTLVertexFormatShort4Normalized: case MTLVertexFormatUShort2Normalized:
            case MTLVertexFormatUShort4Normalized: case MTLVertexFormatInt1010102Normalized:
            case MTLVertexFormatHalf2: case MTLVertexFormatHalf4: break;
            default: return 0;
        }
        if ((!a.format && (a.offset || a.buffer)) || (a.format && !r->layouts[a.buffer].stride)) return 0;
        if (l.stride>2048 || l.step>MTLVertexStepFunctionPerVertex ||
            (l.stride && l.rate!=(l.step==MTLVertexStepFunctionPerVertex ? 1u : 0u)) ||
            (!l.stride && (l.step || l.rate))) return 0;
    }
    return 1;
}
static int history_write(int fd, const void *p, size_t n, off_t offset) {
    // A short write is a failed transaction, even if a later write might succeed.
    ssize_t got;
    do { got=pwrite(fd,p,n,offset); } while (got<0 && errno==EINTR);
    return got==(ssize_t)n;
}
static const char *history_header(int fd, HistoryHeader *h) {
    struct stat st;
    if (fstat(fd,&st)) return "read";
    if (st.st_size>(off_t)(sizeof *h+HISTORY_MAX*sizeof(HistoryRecord))) return "size";
    if (pread(fd,h,sizeof *h,0)!=sizeof *h || h->magic!=HISTORY_MAGIC ||
        h->record_size!=sizeof(HistoryRecord)) return "header";
    if (h->stamp!=history_stamp) return "stamp";
    if (h->count>HISTORY_MAX) return "size";
    if (st.st_size!=(off_t)(sizeof *h+h->count*sizeof(HistoryRecord))) return "truncated";
    return NULL;
}
static int history_reset(int fd, HistoryHeader *h) {
    *h=(HistoryHeader){HISTORY_MAGIC,sizeof(HistoryRecord),history_stamp,0};
    return history_write(fd,h,sizeof *h,0) && ftruncate(fd,sizeof *h)==0;
}
static int history_lock(void) {
    // Never truncate before locking, and never replace the inode holding the lock.
    int fd=open(history_file,O_RDWR|O_CREAT,0600);
    if (fd>=0 && flock(fd,LOCK_EX)) { close(fd); return -1; }
    return fd;
}
// Device thread at load, before pipeline_prewarm. Reset under the same lock as the read.
void pipeline_history_open(const char *dir) {
    if (!dir || !enabled()) return;
    snprintf(history_file,sizeof history_file,"%s/pipelinecache.bin",dir);
    history_stamp=history_device_stamp();
    history_q=dispatch_queue_create("nfsmw.pipeline.history",DISPATCH_QUEUE_SERIAL);
    NSMutableSet<NSData *> *loaded=[NSMutableSet new];
    history_defect=NULL; history_skipped=0;
    int fd=history_lock();
    if (fd>=0) {
        HistoryHeader h; HistoryRecord rec;
        history_defect=history_header(fd,&h);
        if (!history_defect) {
            for (uint64_t i=0;i<h.count;++i) {
                if (pread(fd,&rec,sizeof rec,sizeof h+i*sizeof rec)!=sizeof rec) { history_defect="read"; break; }
                if (rec.check!=history_hash(&rec.recipe,sizeof rec.recipe,history_stamp)) { history_defect="record"; break; }
                if (!history_recipe_valid(&rec.recipe)) { ++history_skipped; continue; }
                [loaded addObject:[NSData dataWithBytes:&rec.recipe length:sizeof rec.recipe]];
            }
        }
        if (history_defect) {
            [loaded removeAllObjects];
            if (!history_reset(fd,&h) && diag_on) diag_event("pipeline_history: write_failed reset");
        }
        close(fd); // releases flock
    } else history_defect="open";
    pthread_mutex_lock(&pipeline_cache_mu);
    history=loaded;
    pthread_mutex_unlock(&pipeline_cache_mu);
    if (diag_on) diag_event("pipeline_history: records=%lu discarded=%s skipped=%zu",(unsigned long)loaded.count,history_defect?history_defect:"none",history_skipped);
}
// A successful demand build: remember it once, appended in the background.
static void history_note(const PipelineRecipe *r) {
    NSData *key=[NSData dataWithBytes:r length:sizeof *r];
    pthread_mutex_lock(&pipeline_cache_mu);
    int fresh=history && history.count<HISTORY_MAX && ![history containsObject:key];
    if (fresh) [history addObject:key];
    pthread_mutex_unlock(&pipeline_cache_mu);
    if (!fresh) return;
    HistoryRecord rec={*r,history_hash(r,sizeof *r,history_stamp)};
    dispatch_async(history_q, ^{
        int fd=history_lock();
        if (fd<0) { if (diag_on) diag_event("pipeline_history: write_failed open"); return; }
        HistoryHeader h;
        const char *defect=history_header(fd,&h);
        if (defect && !history_reset(fd,&h)) { close(fd); return; }
        if (h.count<HISTORY_MAX) {
            off_t end=sizeof h+h.count*sizeof rec;
            HistoryHeader next=h; ++next.count;
            // Commit count only after the entire record is present; undo either short write.
            if (!history_write(fd,&rec,sizeof rec,end) || !history_write(fd,&next,sizeof next,0)) {
                int restored=history_write(fd,&h,sizeof h,0) && ftruncate(fd,end)==0;
                if (diag_on) diag_event("pipeline_history: write_failed rollback=%d",restored);
            }
        }
        close(fd);
    });
}
static int history_known(const PipelineRecipe *r) {
    NSData *key=[NSData dataWithBytes:r length:sizeof *r];
    pthread_mutex_lock(&pipeline_cache_mu);
    int known=[history containsObject:key];
    pthread_mutex_unlock(&pipeline_cache_mu);
    return known;
}
#if NFS_GAME == NFS_GAME_MW
// After the catalogue, on the same queues; frames never join these jobs.
static void history_replay(dispatch_queue_t __strong queues[4], NSMutableSet<NSData *> *scheduled) {
    pthread_mutex_lock(&pipeline_cache_mu);
    NSArray<NSData *> *records=history.allObjects;
    pthread_mutex_unlock(&pipeline_cache_mu);
    history_replayed=0;
    for (NSData *key in records) {
        if ([scheduled containsObject:key]) continue;
        [scheduled addObject:key];
        PipelineRecipe r; memcpy(&r,key.bytes,sizeof r);
        dispatch_async(queues[history_replayed++%4], ^{ @autoreleasepool {
            MTLRenderPipelineDescriptor *d=descriptor(&r);
            if (!d) { if (diag_on) diag_event("pipeline_history: missing_shader"); return; }
            NSError *error=nil;
            if (!pipeline_cached(d,r.vertex,r.fragment,"prewarm",&error) && diag_on)
                diag_event("pipeline_history: failed %s",error.localizedDescription.UTF8String);
        }});
    }
    if (diag_on) diag_event("pipeline_history: replayed=%zu",history_replayed);
}
#endif
