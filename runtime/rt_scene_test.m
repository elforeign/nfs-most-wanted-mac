// Real Metal traversal: both adapters/direct/recorded 0/256 wrong second-frame
// bits; frozen old instance transform 16/256. First frame: 16 hits, 0/256 wrong.
#include <time.h>
#define RT_SCENE_TEST 1
#include "render_thread.m"
#ifndef RT_SCENE_SOURCE
#define RT_SCENE_SOURCE "rt_scene.m"
#endif
#include RT_SCENE_SOURCE

id<MTLDevice> mtl;
id<MTLCommandQueue> queue;
Device dev;
uint8_t *G;
int diag_on;
const char *diag_dir;
uint64_t host_ticks(void) { return clock_gettime_nsec_np(CLOCK_UPTIME_RAW); }
void diag_event(const char *fmt, ...) { (void)fmt; }
static Obj objects[5];
Obj *obj(uint32_t address) {
    for (unsigned i=0;i<5;++i) if (objects[i].guest==address && address) return &objects[i];
    return NULL;
}
static unsigned checks, failures;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; fprintf(stderr,"FAIL %d: %s\n",__LINE__,#x); } } while (0)

static void identity(float *m) { for (int i=0;i<16;++i) m[i]=i%5==0; }
static uint32_t platform;
static float initial_world[16];
static void constants(float x) {
    float w[16]; memcpy(w,initial_world,sizeof w); w[12]=x;
    const float *vp=(const float *)(G+platform+0x80);
    for (int i=0;i<4;++i) for (int j=0;j<4;++j) {
        dev.vsc[j][i]=0;
        for (int k=0;k<4;++k) dev.vsc[j][i]+=w[4*i+k]*vp[4*k+j];
    }
}
static RTSceneElement position={.type=2};
static RTSceneDraw draw;
static uint64_t fixture_shader;
static void setup(void) {
    memset(&dev,0,sizeof dev); memset(objects,0,sizeof objects);
    objects[0]=(Obj){.kind=K_VB,.guest=0x1000,.resource_id=1,.mem={0x10000},.size=128,.buffer_generation=2};
    objects[1]=(Obj){.kind=K_IB,.guest=0x1100,.resource_id=2,.mem={0x20000},.size=12,.fmt=101,.buffer_generation=2};
    objects[2]=(Obj){.kind=K_SURFACE,.guest=0x1200,.resource_id=3};
    objects[3]=(Obj){.kind=K_SURFACE,.guest=0x1300,.resource_id=4};
    objects[4]=(Obj){.kind=K_DECL,.guest=0x1400,.resource_id=5};
    dev.stream[0]=0x1000; dev.stream_stride[0]=12; dev.stream_freq[0]=1;
    dev.indices=0x1100; dev.decl=0x1400; dev.rt[0]=0x1200; dev.ds=0x1300;
    dev.rs[7]=dev.rs[14]=dev.rs[168]=dev.rs[22]=1;
    platform=0x30000;
#if NFS_GAME == NFS_GAME_PRIVATE
    rt_scene_fixture_camera(platform);
#else
    M32(0x00925E90u)=6; M32(0x00982A20u)=0x40000;
    M32(0x40000)=0; M32(0x40004)=1; M32(0x00919650u+0x60)=0x40000; M32(0x00919650u)=platform;
#endif
    // Asymmetric perspective and inverted Y exercise matrix packing and clip
    // handedness independently of the simple world-space ray oracle.
    const float vp[16]={1.3f,0,0,0, 0,-0.7f,0,0, .2f,.3f,1.01f,1, .15f,-.4f,-.1f,0};
    memcpy(G+platform+0x80,vp,sizeof vp);
    const float w[16]={0,2,0,0, -1,0,0,0, 0,0,-1,0, 0,0,0,1};
    memcpy(initial_world,w,sizeof w); constants(0);
    // Local quad becomes [-2,2]^2 at world z=0 after the nontrivial world pose.
    const float points[12]={-1,-2,0, 1,-2,0, 1,2,0, -1,2,0};
    const uint16_t indices[6]={0,1,2,0,2,3};
    memcpy(G+objects[0].mem[0],points,sizeof points); memcpy(G+objects[1].mem[0],indices,sizeof indices);
    position=(RTSceneElement){.type=2};
    draw=(RTSceneDraw){.type=4,.prims=2,.indexed=1,.elements=&position,.element_count=1,.shader=fixture_shader};
}

static int off(void) {
    // Invalid draw/command/device arguments must never be inspected when Off.
    for (int i=0;i<1000;++i) { rt_scene_capture(NULL); rt_scene_flush(nil); rt_scene_forget(1); rt_scene_reset(); }
    CHECK(!cache && !packets && !placements && !last_tlas && !last_blas && !timings);
    RTSceneStats zero={0}, s=rt_scene_stats(); CHECK(!memcmp(&zero,&s,sizeof s));
    printf("OFF calls=4000 scene_allocations=0 encodes=0 failures=%u\n",failures);
    return failures!=0;
}

static void packing(void) {
    setup(); rt_scene_reset();
    // Actual indices, signed base and stream/element offsets determine bounds.
    float original[12]; memcpy(original,G+objects[0].mem[0],sizeof original);
    dev.stream_off[0]=8; dev.stream_stride[0]=20; position.offset=4;
    for (int i=0;i<4;++i) memcpy(G+objects[0].mem[0]+12+i*20,original+3*i,12);
    uint16_t idx[6]={2,3,4,2,4,5}; memcpy(G+objects[1].mem[0],idx,sizeof idx);
    draw.base_vertex=-2;
    RTSceneGeometry *g=geometry(&draw,&position); CHECK(g);
    CHECK(g->positions.length==sizeof original && !memcmp(g->positions.contents,original,sizeof original));
    const uint32_t expected[6]={0,1,2,0,2,3};
    CHECK(g->indices.length==sizeof expected && !memcmp(g->indices.contents,expected,sizeof expected));
    CHECK(geometry(&draw,&position)==g);
    ++objects[0].buffer_generation; CHECK(geometry(&draw,&position)!=g);
    objects[0].resource_id=6; CHECK(geometry(&draw,&position)->key.vb==6);
    CHECK(cache.count==3); rt_scene_forget(6); CHECK(cache.count==2);
    // UInt32, alternating strip winding, fan expansion and degenerates.
    objects[1].fmt=102; objects[1].size=24;
    uint32_t wide[6]={2,3,4,5,5,5}; memcpy(G+objects[1].mem[0],wide,sizeof wide);
    ++objects[1].buffer_generation; draw.type=5;
    g=geometry(&draw,&position); const uint32_t strip[6]={0,1,2,2,1,3};
    CHECK(g && g->indices.length==sizeof strip && !memcmp(g->indices.contents,strip,sizeof strip));
    draw.type=6; g=geometry(&draw,&position); const uint32_t fan[6]={0,1,2,0,2,3};
    CHECK(g && !memcmp(g->indices.contents,fan,sizeof fan));
    draw.type=4; g=geometry(&draw,&position); CHECK(g && g->indices.length==12);
    draw.base_vertex=-3; CHECK(!geometry(&draw,&position)); draw.base_vertex=-2;
    objects[0].size=40; ++objects[0].buffer_generation;
    CHECK(!geometry(&draw,&position)); objects[0].size=128;
    objects[0].kind=K_TEXTURE; CHECK(!geometry(&draw,&position)); objects[0].kind=K_VB;
    draw.start=UINT32_MAX; CHECK(!geometry(&draw,&position)); draw.start=0;
    draw.type=2; CHECK(!geometry(&draw,&position));
    draw.type=4; draw.base_vertex=1; position.offset=0;
    uint32_t overflow[6]={UINT32_MAX-2,UINT32_MAX-1,UINT32_MAX,UINT32_MAX-2,UINT32_MAX,UINT32_MAX-1};
    memcpy(G+objects[1].mem[0],overflow,sizeof overflow); ++objects[1].buffer_generation;
    objects[0].size=UINT32_MAX; dev.stream_off[0]=dev.stream_stride[0]=UINT32_MAX;
    CHECK(!geometry(&draw,&position)); // the unchecked end address would wrap to 11
    setup(); rt_scene_reset();
    draw.up_vertices=objects[0].mem[0]; draw.up_stride=12; draw.up_indices=objects[1].mem[0]; draw.up_index_fmt=101;
    g=geometry(&draw,&position); CHECK(g && geometry(&draw,&position)==g);
    MF32(draw.up_vertices)=8; CHECK(geometry(&draw,&position)!=g); // UP snapshot cannot follow mutable memory
    CHECK(((const float *)g->positions.contents)[0]==-1);
    setup(); rt_scene_reset();
    rt_scene_capture(&draw); CHECK(packets.count==1);
    rt_scene_capture(&draw); CHECK(packets.count==1); // duplicate material pass
    RTScenePacket *p=packets[0];
    double error=0; for (int i=0;i<16;++i) error=fmax(error,fabs(p->world[i]-initial_world[i]));
    CHECK(error<1e-5);
    rt_scene_reset();
    RTSceneStats before=stats;
    const unsigned unsupported[]={RT_SCENE_SKIN,RT_SCENE_MORPH,RT_SCENE_FOLIAGE,RT_SCENE_GLASS,RT_SCENE_ALPHA};
    for (int i=0;i<5;++i) { draw.unsupported=unsupported[i]; rt_scene_capture(&draw); }
    CHECK(stats.skinning==before.skinning+1 && stats.morphs==before.morphs+1 && stats.foliage==before.foliage+1 &&
        stats.glass==before.glass+1 && stats.alpha_test==before.alpha_test+1 && !packets.count);
    draw.unsupported=RT_SCENE_GLASS; dev.rs[14]=0; rt_scene_capture(&draw);
    CHECK(stats.glass==before.glass+2 && !packets.count); dev.rs[14]=1;
    RTSceneElement skin[2]={{.type=2},{.usage=1,.type=0}};
    draw.unsupported=0; draw.elements=skin; draw.element_count=2; rt_scene_capture(&draw);
    CHECK(stats.skinning==before.skinning+2 && !packets.count);
    skin[1].usage=0; skin[1].index=1; rt_scene_capture(&draw);
    CHECK(stats.morphs==before.morphs+2 && !packets.count);
    draw.elements=&position; draw.element_count=1;
    draw.unsupported=0; draw.shader=0; rt_scene_capture(&draw); CHECK(stats.unknown_program==before.unknown_program+1);
    draw.shader=fixture_shader; position.usage=9; rt_scene_capture(&draw); CHECK(!packets.count);
    position.usage=0; memset(dev.vsc,0,sizeof dev.vsc); rt_scene_capture(&draw); CHECK(stats.bad_transform==before.bad_transform+1);
    constants(0); dev.rt[0]=0; rt_scene_capture(&draw); CHECK(!packets.count);
    setup(); rt_scene_capture(&draw); CHECK(cache.count==1);
    dev.frames=121; [packets removeAllObjects];
    rt_scene_flush(nil); CHECK(cache.count==0 && !rt_scene_last_tlas());
    rt_scene_reset(); setup(); rt_scene_capture(&draw); CHECK(packets.count==1);
#if NFS_GAME == NFS_GAME_PRIVATE
    rt_scene_fixture_inactive();
#else
    M32(0x00925E90u)=0;
#endif
    rt_scene_flush(nil); CHECK(!cache && !packets && !rt_scene_last_tlas());
    printf("PACKING checks=%u failures=%u matrix_error=%.9g\n",checks,failures,error);
    rt_scene_reset();
}

static const char *ray_source=
"#include <metal_stdlib>\n#include <metal_raytracing>\nusing namespace metal;using namespace metal::raytracing;\n"
"kernel void grid(instance_acceleration_structure scene [[buffer(0)]],device uint *hits [[buffer(1)]],uint2 id [[thread_position_in_grid]]) {"
"ray r;r.origin=float3(float2(id)-7.5,-5);r.direction=float3(0,0,1);r.min_distance=0;r.max_distance=10;"
"intersector<triangle_data,instancing> query;query.assume_geometry_type(geometry_type::triangle);query.force_opacity(forced_opacity::opaque);"
"auto hit=query.intersect(r,scene,0xff);hits[id.y*16+id.x]=hit.type!=intersection_type::none;}";

static void trace(id<MTLCommandBuffer> command,const void *bytes,size_t size,NSArray *r) {
    (void)bytes; (void)size;
    id<MTLComputeCommandEncoder> e=[command computeCommandEncoder];
    [e setComputePipelineState:r[0]]; [e setAccelerationStructure:r[1] atBufferIndex:0];
    [e setBuffer:r[2] offset:0 atIndex:1];
    for (id<MTLAccelerationStructure> b in r[3]) [e useResource:b usage:MTLResourceUsageRead];
    [e dispatchThreads:MTLSizeMake(16,16,1) threadsPerThreadgroup:MTLSizeMake(8,8,1)]; [e endEncoding];
}

static int gpu(int recorded, const char *output) {
    setup(); rt_scene_reset(); rt_on=recorded;
    NSError *error=nil;
    id<MTLLibrary> library=[mtl newLibraryWithSource:@(ray_source) options:nil error:&error];
    if (!library) { fprintf(stderr,"%s\n",error.localizedDescription.UTF8String); return 2; }
    id<MTLComputePipelineState> pipeline=[mtl newComputePipelineStateWithFunction:[library newFunctionWithName:@"grid"] error:&error]; CHECK(pipeline);
    id<MTLBuffer> outputs[2]; id<MTLCommandBuffer> commands[2];
    uint64_t old_blas=stats.blas_builds,old_tlas=stats.tlas_builds;
    __weak RTSceneGeometry *weak;
    @autoreleasepool {
        for (int frame=0;frame<2;++frame) {
            dev.frames=frame; constants(frame?20:0);
            commands[frame]=rt_command_buffer();
            rt_scene_capture(&draw); CHECK(packets.count==1);
            weak=((RTScenePacket *)packets[0])->geometry;
            // Capture must copy constants; replay must not inspect live dev/G.
            memset(dev.vsc,0,sizeof dev.vsc);
            rt_scene_flush(commands[frame]); CHECK(rt_scene_last_tlas());
            outputs[frame]=[mtl newBufferWithLength:256*4 options:MTLResourceStorageModeShared];
            rt_native_encode(commands[frame],trace,NULL,0,@[pipeline,rt_scene_last_tlas(),outputs[frame],rt_scene_last_blas()]);
        }
        // GPU ownership must survive source Release, cache eviction and reset.
        memset(G+objects[0].mem[0],0,objects[0].size); memset(G+objects[1].mem[0],0,objects[1].size);
        rt_scene_forget(objects[0].resource_id); rt_scene_reset();
    }
    CHECK(weak!=nil);
    CHECK(stats.blas_builds-old_blas==1 && stats.tlas_builds-old_tlas==2);
    for (int frame=0;frame<2;++frame) {
        if (recorded) replay((RTCmd *)commands[frame]); else [commands[frame] commit];
        [commands[frame] waitUntilCompleted];
        id<MTLCommandBuffer> real=recorded?((RTCmd *)commands[frame])->real:commands[frame];
        CHECK(real.status==MTLCommandBufferStatusCompleted);
        if (real.error) fprintf(stderr,"%s\n",real.error.localizedDescription.UTF8String);
    }
    unsigned first_bad=0,second_bad=0,first_hits=0;
    const uint32_t *a=outputs[0].contents,*b=outputs[1].contents;
    for (unsigned y=0;y<16;++y) for (unsigned x=0;x<16;++x) {
        unsigned i=y*16+x,expected=x>=6 && x<10 && y>=6 && y<10;
        first_bad+=a[i]!=expected; first_hits+=a[i]!=0; second_bad+=b[i]!=0;
    }
    CHECK(first_bad==0 && first_hits==16);
    CHECK(second_bad==0);
    @autoreleasepool { commands[0]=nil; commands[1]=nil; }
    CHECK(weak==nil);
    FILE *file=fopen(output,"wb"); CHECK(file);
    if (file) { fwrite(outputs[0].contents,4,256,file); fwrite(outputs[1].contents,4,256,file); fclose(file); }
    printf("GPU game=%d path=%s first_wrong=%u/256 first_hits=%u second_wrong=%u/256 blas=%llu tlas=%llu retained=%d failures=%u\n",
        NFS_GAME,recorded?"recorded":"direct",first_bad,first_hits,second_bad,stats.blas_builds-old_blas,stats.tlas_builds-old_tlas,weak!=nil,failures);
    return failures!=0;
}

int main(int argc,char **argv) { @autoreleasepool {
    if (argc==2 && !strcmp(argv[1],"off")) return off();
    if (argc!=4) return 2;
    FILE *shader=fopen(argv[1],"rb"); if (!shader) return 3;
    fseek(shader,0,SEEK_END); long size=ftell(shader); rewind(shader);
    void *tokens=malloc((size_t)size); CHECK(fread(tokens,1,size,shader)==(size_t)size); fclose(shader);
    fixture_shader=hash_bytes(1469598103934665603ull,tokens,(size_t)size); free(tokens);
    CHECK(program(fixture_shader));
    mtl=MTLCreateSystemDefaultDevice();
    if (!rt_scene_supported(mtl)) { puts("SKIP: hardware ray tracing unavailable"); return 3; }
    queue=[mtl newCommandQueue]; G=calloc(1,16*1024*1024);
    char logdir[1024]; snprintf(logdir,sizeof logdir,"%s",argv[3]);
    char *slash=strrchr(logdir,'/'); if (slash) *slash=0;
    diag_on=1; diag_dir=slash?logdir:".";
    packing();
    int result=gpu(atoi(argv[2]),argv[3]);
    free(G); return result;
} }
