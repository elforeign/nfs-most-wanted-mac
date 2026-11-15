/* Synthetic resident scenery and the production raster/submission code.
 * Fixed runtime: 0/193; inverted: 54/193 FAIL; stale movement: 4/193 FAIL.
 * Ignored detail filter: 21/193 FAIL; stale detail: 9/193; stale signature: 4/193.
 * Core byte equality: 0/120; skipped resume: 4/120 FAIL; unbounded: 60/120 FAIL.
 * 60 s paths at 90 Hz, 8,700 triangles / 5.5 ms deterministic build work:
 * straight 30/60/100 m/s: uncovered 0/0/0, starts 42/83/200;
 * curved (180 m radius): uncovered 0/0/0, starts 38/76/187.
 * All start intervals >=30 m; limits 61/121/201 per path. Pinned round-one
 * 256-step / 16 m lead / 32 m margin control: uncovered 32/1310/3008
 * straight and 125/1316/2896 curved (8,687 total), six failing paths.
 * Stop/turn: 0 uncovered; disabled turn bypass: 11 uncovered FAIL.
 * Pinned old synchronous begin: 1000 starts, 1000/1076 FAIL; stale core: 2/77.
 * Mirror-view constants must not drop the map: fixed 0/194; prepare(0) in rain_cover_constants: 1/194 FAIL (mirror_prepare).
 * The Python driver executes the fixed and deliberately broken controls.
 */
#import <Foundation/Foundation.h>
#include <stdint.h>
#ifndef RAIN_COVER_SOURCE
#define RAIN_COVER_SOURCE "rain_cover.m"
#endif
#if defined(RAIN_COVER_SYNTHETIC_CLOCK) && !defined(RAIN_COVER_CORE_ONLY)
static uint64_t fixture_clock(void);
static void fixture_charge(void);
#endif
#include RAIN_COVER_SOURCE

#ifdef RAIN_COVER_CORE_ONLY
#ifndef RAIN_COVER_OLD_BEGIN
#error pinned old refresh control required
#endif
#define rain_cover_begin old_rain_cover_begin
#include RAIN_COVER_OLD_BEGIN
#undef rain_cover_begin
static int failures, checks;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x); } } while(0)
static float cutout(void *unused, float u, float v) {
    (void)unused;
    return ((int)floorf(u*8)+(int)floorf(v*8))&1 ? 95.f/255 : 1;
}
static void core_tests(int old_rule) {
    const unsigned budgets[] = {1,7,64,257};
    for (unsigned b=0;b<sizeof budgets/sizeof *budgets;++b) {
        RainCoverMap whole={0}, sliced_map={0};
        rain_cover_begin(&whole,12.25f,-7.75f,0);
        rain_cover_begin(&sliced_map,12.25f,-7.75f,0);
        for (unsigned i=0;i<17;++i) {
            float t=(float)i;
            float p[3][3]={{-70+t,-70,1+t},{80,20+t,3+t},{-10,80-t,11+t}};
            float uv[3][3]={{0,0,1},{2,0,.5f},{0,2,1}};
            if (i==15) p[1][0]=p[0][0],p[1][1]=p[0][1];
            if (i==16) p[0][0]=NAN;
            cover_triangle(&whole,p,uv,i&1?cutout:NULL,NULL);
            CoverRaster raster;
            cover_raster_begin(&raster,&sliced_map,p,uv);
            if (raster.active) {
                int row=raster.row;
                cover_raster_step(&raster,&sliced_map,budgets[b],i&1?cutout:NULL,NULL);
                unsigned visited=(raster.row-row)*(raster.hx-raster.lx+1)+raster.col-raster.lx;
                CHECK(visited<=budgets[b]);
            }
            while (raster.active) cover_raster_step(&raster,&sliced_map,budgets[b],i&1?cutout:NULL,NULL);
        }
        CHECK(!memcmp(whole.height,sliced_map.height,RAIN_COVER_SIZE*RAIN_COVER_SIZE*sizeof(float)));
        CHECK(whole.triangles==sliced_map.triangles);
        CHECK(rain_cover_sample(&whole,12,0,0));
        free(whole.height); free(sliced_map.height);
    }
    CHECK(cover_contains(&(RainCoverMap){.valid=1},0,0));
    CHECK(cover_edge_frames(&(RainCoverMap){.valid=1},0,0,1,0)==44);
    RainCoverMap current={0}, old={0};
    unsigned starts=0, old_starts=0;
    float last=-100;
    for (unsigned i=0;i<2000;++i) {
        float x=i*.7f;
        if ((old_rule ? old_rain_cover_begin : rain_cover_begin)(&current,x,0,i)) { CHECK(x-last>=30); last=x; ++starts; }
        old_starts+=old_rain_cover_begin(&old,x,0,i);
    }
    CHECK(starts==44 && starts<=47);
    CHECK(old_starts==1000 && old_starts>47);
    printf("path 2000 x 0.7m: sliced rule %u rebuilds; pinned old rule %u (limit 47)\n",starts,old_starts);
    free(current.height); free(old.height);
}
int main(int argc, char **argv) { (void)argv;core_tests(argc>1);printf("rain cover core: %d failures / %d checks\n",failures,checks);return failures?1:0; }
#else

#ifdef RAIN_COVER_SYNTHETIC_CLOCK
static int race_path;
static uint64_t fixture_ns, fixture_frame_start;
static uint64_t fixture_clock(void) {
    if (!race_path) fixture_ns += 20000;
    return dev.frames*(1000000000ull/90)+fixture_ns-(race_path?fixture_frame_start:0);
}
static void fixture_charge(void) {
    if (!race_path) return;
    /* 8,700 decoded triangles at 600 ns plus a 280 us clear = 5.5 ms.
     * Charge the decode even outside the footprint: every map has the same
     * work. The raster is still exercised by the byte-equality fixture. */
    if (build.phase == COVER_CLEAR) fixture_ns += 280000;
    if (build.phase == COVER_TRIANGLE && build.triangle < build.count) fixture_ns += 600;
}
#endif
uint8_t *G;
Device dev;
id<MTLDevice> mtl;
int diag_on;
void diag_event(const char *format, ...) { (void)format; }
static Obj buffers[5];
Obj *obj(uint32_t g) { return g > 0 && g < 5 ? &buffers[g] : NULL; }
#ifdef RAIN_COVER_MATERIAL_FIXTURE
#include RAIN_COVER_MATERIAL_FIXTURE
#else
int roadfilm_shader_hash(uint64_t h) { return h == 7; }
#endif
char *mat_replace(const uint32_t *t, uint32_t n, const SmResult *r, int *i) {
    (void)t;(void)n;(void)i;
#ifdef RAIN_COVER_MATERIAL_FIXTURE
    return r->shader_type == SM_VERTEX ? ripple_vs_patch(r->msl) : ripple_ps_patch(r->msl);
#else
    return strdup(r->msl);
#endif
}
char *mat_roadfilm_shader(const uint32_t *t, uint32_t n, const SmResult *r, int *i) {
#ifdef RAIN_COVER_MATERIAL_FIXTURE
    char *base=mat_replace(t,n,r,i), *result=roadfilm_patch(base ? base : r->msl);
    free(base);return result;
#else
    return mat_replace(t,n,r,i);
#endif
}
static int failures, checks, renders, expected_visible;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x); } } while(0)
enum { RAIN=0x100000, CAMERA=0x110000, VIEW=0x919650, SECTION=0x2000,
       INSTANCE=0x3000, MODELS=0x4000, MODEL=0x5000, SOLID=0x6000,
       PLATFORM=0x7000, MESH=0x8000, EFFECT=0x9000, TEXTURES=0xa000,
       TEXTURE=0xb000, VERTICES=0xc000, INDICES=0xd000, TARGET=0xe000,
       DECL=0xf000, TEXPLAT=0x10000, PIXELS=0x11000 };
static void identity(uint32_t p) { for(int i=0;i<16;++i) MF32(p+4*i)=i%5==0?1:0; }
static void render(Cpu *c) {
    ++renders;
    unsigned visible=0;
    for(unsigned i=0;i<M32(RAIN+0x33d8);++i) visible+=M8(RAIN+0x2e60+4*i)==0;
    CHECK(visible==(unsigned)expected_visible);
    CHECK(M32(RAIN+0x284)==visible);
    c->esp+=4;
}
void f_00758100_orig(Cpu *c) { rain_cover_render(c,render); }
static void quad(float x0,float y0,float x1,float y1,float z,unsigned *at) {
    const float v[4][3]={{x0,y0,z},{x1,y0,z},{x1,y1,z},{x0,y1,z}};
    unsigned first=*at;
    const float uv[4][2]={{0,0},{1,0},{1,1},{0,1}};
    for(unsigned i=0;i<4;++i) {
        memcpy(G+VERTICES+24*(first+i),v[i],12);
        M32(VERTICES+24*(first+i)+12)=0xffffffff;
        memcpy(G+VERTICES+24*(first+i)+16,uv[i],8);
    }
    const uint16_t ix[6]={first,first+1,first+2,first,first+2,first+3};
    memcpy(G+INDICES+(*at/4)*12,ix,12); *at+=4;
}
static void scene(int gap) {
    unsigned n=0;
    memset(G+VERTICES,0,1024); memset(G+INDICES,0,1024);
    if(gap) {quad(-8,-8,-1,8,10,&n);quad(1,-8,8,8,10,&n);}
    else quad(-8,-8,8,8,10,&n);
    /* Bottom and four sides make a closed box. Vertical sides have zero
     * projected area, the bottom must never win over the roof. */
    quad(-8,-8,8,8,0,&n);
    float wall[4][3]={{-8,-8,0},{8,-8,0},{8,-8,10},{-8,-8,10}};
    for(unsigned side=0;side<4;++side) {
        unsigned first=n;
        for(unsigned j=0;j<4;++j) {
            float p[3]={wall[j][0],wall[j][1],wall[j][2]};
            for(unsigned k=0;k<side;++k){float t=p[0];p[0]=-p[1];p[1]=t;}
            memcpy(G+VERTICES+24*n++,p,12);
        }
        uint16_t ix[6]={first,first+1,first+2,first,first+2,first+3};
        memcpy(G+INDICES+(first/4)*12,ix,12);
    }
    buffers[1]=(Obj){.kind=K_VB,.size=n*24,.mem={VERTICES}};
    buffers[2]=(Obj){.kind=K_IB,.fmt=101,.size=n/4*12,.mem={INDICES}};
    M32(MESH+0x40)=n/4*2;
}
static void fixture(void) {
    G=calloc(1,16*1024*1024);
    M32(0x925e90)=6; M32(0x9aa478)=SECTION; M32(SECTION)=0x9aa478;
    M32(SECTION+0x18)=MODELS; M32(SECTION+0x1c)=1;
    M32(SECTION+0x20)=INSTANCE; M32(SECTION+0x24)=1;
    MF32(INSTANCE)=-8; MF32(INSTANCE+4)=-8; MF32(INSTANCE+8)=0;
    MF32(INSTANCE+12)=8; MF32(INSTANCE+16)=8; MF32(INSTANCE+20)=10;
    M32(INSTANCE+0x18)=0x80200; M32(MODELS+0x28)=MODEL;
    M32(MODEL+0xc)=SOLID; M32(SOLID)=PLATFORM; M32(SOLID+0x2c)=TEXTURES;
    M32(TEXTURES+4)=TEXTURE; M32(PLATFORM+0x10)=1; M32(PLATFORM+0x14)=MESH;
    M32(PLATFORM+0x28)=2; M32(MESH+0x34)=EFFECT; M32(MESH+0x54)=1;
    M32(EFFECT+4)=1; M32(EFFECT+8)=24;M32(EFFECT+0x4c)=3;
    buffers[3]=(Obj){.kind=K_DECL,.decl_mem=DECL,.decl_n=4};
    M16(DECL)=0;M16(DECL+2)=0;M8(DECL+4)=2;M8(DECL+6)=0;
    M16(DECL+8)=0;M16(DECL+10)=12;M8(DECL+12)=4;M8(DECL+14)=10;
    M16(DECL+16)=0;M16(DECL+18)=16;M8(DECL+20)=1;M8(DECL+22)=5;
    M16(DECL+24)=0xff;
    M32(TEXTURE)=TEXPLAT;M32(TEXPLAT+0x18)=4;
    buffers[4]=(Obj){.kind=K_TEXTURE,.fmt=21,.w=4,.h=4,.mem={PIXELS},.pitch={16}};
    memset(G+PIXELS,255,64);
    M32(RAIN+0x288)=VIEW; M32(VIEW+4)=1; M32(VIEW+0x40)=CAMERA;
    M32(VIEW+0x68)=RAIN; M32(VIEW+0x60)=TARGET; M32(TARGET+4)=1; M32(0x982a20)=TARGET;
    identity(RAIN+0x3610); M32(RAIN+0x33d8)=4; M32(RAIN+0x284)=3;
    const float points[4][3]={{3,3,5},{30,30,5},{3,3,12},{3,3,5}};
    for(unsigned i=0;i<4;++i)memcpy(G+RAIN+(0x2a+2*i)*16,points[i],12);
    M8(RAIN+0x2e60+12)=2; // authored hidden sample stays hidden
    MF32(RAIN+0x36a0)=.734f; MF32(RAIN+0x28c)=.9f;
    scene(0);
}
/* Advance synthetic frames without submitting precipitation until a complete
 * back map is published. Production never spins to finish a build. */
static void settle(void) {
    for (unsigned i=0;i<10000;++i) {
        ++dev.frames; prepare(RAIN);
        if (map.valid && !build.active) return;
    }
    CHECK(0 && "build did not complete");
}
static void refresh(void) { cover_invalidate(); settle(); }
static void world_detail_tests(Cpu *c) {
    /* Independent truth table: LOD 2 uses 00982B9C=0x800, LOD 3 uses zero.
     * Include both variant bits together: 0x100 never rescues 0x80000 at LOD 2. */
    const struct { uint32_t flags; int lod2, lod3; } variants[] = {
        {0,1,1}, {0x100,1,1}, {0x40000,1,0}, {0x40100,1,1},
        {0x80000,0,1}, {0x80100,0,1}, {0xc0000,0,0}, {0xc0100,0,1},
    };
    scene(0); M8(TEXTURE+0x55)=0;
    for (unsigned i=0;i<sizeof variants/sizeof *variants;++i) for (unsigned lod=2;lod<=3;++lod) {
        M32(INSTANCE+0x18)=variants[i].flags|0x200; // identity transform
        M32(0x982b9c)=lod==2?0x800:0;
        int covered=lod==2?variants[i].lod2:variants[i].lod3;
        refresh(); expected_visible=covered?2:3;
        c->ecx=RAIN; f_00758100(c);
        CHECK(map.valid && rain_cover_sample(&map,3,3,5)==covered);
        CHECK((map.triangles>0)==covered);
        CHECK(M8(RAIN+0x2e60)==0 && M32(RAIN+0x284)==3);
    }
    /* Detail alone invalidates and schedules a rebuild in both directions.
     * Exercise the secondary variant and its exception too. */
    const struct { uint32_t flags, detail; int covered; } changes[] = {
        {0x80000,0x800,0}, {0x80000,0,1}, {0x80000,0x800,0},
        {0x40000,0,0}, {0x40000,0x800,1}, {0x40100,0,1},
    };
    for (unsigned i=0;i<sizeof changes/sizeof *changes;++i) {
        M32(INSTANCE+0x18)=changes[i].flags|0x200;
        M32(0x982b9c)=changes[i].detail;
        uint64_t generation=map.generation;
        ++dev.frames; prepare(RAIN);
        settle();
        CHECK(map.generation==generation+1);
        CHECK(rain_cover_sample(&map,3,3,5)==changes[i].covered);
    }
    CHECK(map.allocations+back.allocations==2 && map.height!=back.height);
}
static void scheduler_tests(void) {
    scene(0); M8(TEXTURE+0x55)=0; M32(INSTANCE+0x18)=0x200; M32(0x982b9c)=0;
    MF32(CAMERA+0x40)=MF32(CAMERA+0x44)=0; refresh();
    uint64_t generation=map.generation;
    float *front=malloc(RAIN_COVER_SIZE*RAIN_COVER_SIZE*sizeof(float));
    memcpy(front,map.height,RAIN_COVER_SIZE*RAIN_COVER_SIZE*sizeof(float));
    MF32(CAMERA+0x40)=45;++dev.frames;prepare(RAIN);
    CHECK(build.active && map.valid && map.generation==generation && map.x==0);
    CHECK(!memcmp(front,map.height,RAIN_COVER_SIZE*RAIN_COVER_SIZE*sizeof(float)));
    CoverBuild cursor=build;
    for (unsigned i=0;i<10;++i) prepare(RAIN);
    CHECK(!memcmp(&cursor,&build,sizeof build)); // at most one slice per frame
    CHECK(!rain_cover_sample(&map,65,0,5));
    /* Change the list head mid-build, then its count with the same node.
     * Neither partial map may become visible, including to road ripples. */
    const uint32_t second=0x2200;
    memcpy(G+second,G+SECTION,0x28);M32(second)=0x9aa478;M32(0x9aa478)=second;
    ++dev.frames;prepare(RAIN);
    CHECK(map.valid && build.active && back.frame==dev.frames && map.generation==generation);
    float constants[4];rain_cover_constants(constants);
    CHECK(constants[3]==1);
    M32(second+0x24)=0;++dev.frames;prepare(RAIN);
    CHECK(map.valid && (build.active ? back.frame==dev.frames : map.frame==dev.frames));
    settle();
    CHECK(map.generation==generation+1 && map.triangles==0 && !rain_cover_sample(&map,3,3,5));
    M32(0x9aa478)=SECTION;++dev.frames;prepare(RAIN);
    CHECK(map.valid && build.active);
    settle();
    CHECK(rain_cover_sample(&map,3,3,5));
    M32(VIEW+0x40)=0;prepare(RAIN);CHECK(!map.valid && !build.active);
    M32(VIEW+0x40)=CAMERA;settle();
    CHECK(map.valid && rain_cover_sample(&map,3,3,5));
    {uint64_t gen=map.generation;float k[4];M32(TARGET+4)=3;rain_cover_constants(k);M32(TARGET+4)=1; // mirror draw keeps the map
     CHECK(map.valid && !build.active && map.generation==gen && k[3]==1);}
    generation=map.generation;dev.frames=0;prepare(RAIN);
    CHECK(map.valid && !build.active);settle();CHECK(map.generation==generation+1); // no front map: the first one is finished at once
    generation=map.generation;
    for (unsigned i=0;i<240;++i) {++dev.frames;prepare(RAIN);}
    CHECK(map.generation==generation && !build.active); // no periodic refresh
    free(front);
    CHECK(diag_slices==0 && diag_rebuilds==0 && diag_build_ms==0 && diag_max_ms==0);
    M32(0x9aa478)=SECTION;MF32(CAMERA+0x40)=0;refresh();
}

#ifdef RAIN_COVER_SYNTHETIC_CLOCK
static void race_paths(void) {
    fixture(); race_path=1;
    CHECK(cover_contains(&(RainCoverMap){.valid=1},0,0));
    CHECK(cover_edge_frames(&(RainCoverMap){.valid=1},0,0,1,0)==44);
    const unsigned speeds[]={30,60,100};
    for (unsigned curved=0;curved<2;++curved) for (unsigned speed=0;speed<3;++speed) {
        rain_cover_set(1); dev.frames=0; fixture_ns=fixture_frame_start=0;
        /* A resident instance enclosing the entire route, with 8,700
         * index triples. Decode work remains fixed even beyond its roof. */
        MF32(INSTANCE) = MF32(INSTANCE+4) = -10000;
        MF32(INSTANCE+12) = MF32(INSTANCE+16) = 10000;
        M32(INSTANCE+0x18)=0x200; M8(TEXTURE+0x55)=0;
        const float tiny[3][3]={{0,0,10},{.25f,0,10},{0,.25f,10}};
        for (unsigned v=0;v<3;++v) memcpy(G+VERTICES+v*24,tiny[v],12);
        const uint32_t indices=0x300000;
        for (unsigned i=0;i<8700;++i) {
            M16(indices+6*i)=0;M16(indices+6*i+2)=1;M16(indices+6*i+4)=2;
        }
        buffers[2].mem[0]=indices;buffers[2].size=8700*6;M32(MESH+0x40)=8700;
        unsigned starts=0,uncovered=0,spacing=0,completed=0,startup=0;
        double last_start=-1000,step=speeds[speed]/90.0;
        uint64_t generation=map.generation,max_slice=0;
        for (unsigned f=0;f<5400;++f) {
            double travel=f*step,angle=travel/180;
            float x=curved?180*sin(angle):travel;
            float y=curved?180*(1-cos(angle)):0;
            MF32(CAMERA+0x40)=x;MF32(CAMERA+0x44)=y;
            uint64_t before=fixture_ns;fixture_frame_start=fixture_ns;
            int front=map.valid; // the 0.5 ms ceiling holds while a front map shelters the rain
            ++dev.frames;prepare(RAIN);
            if (front && fixture_ns-before>max_slice) max_slice=fixture_ns-before;
            if (back.frame==dev.frames && build.active) {
                if (travel-last_start < 30-1e-4) ++spacing;
                last_start=travel;++starts;
            }
            if (map.generation!=generation) { completed=1;generation=map.generation; }
            if (!completed) ++startup; // frames without any map: the first build is not sliced
            if (completed && !cover_contains(&map,x,y)) ++uncovered;
        }
        printf("race path %s %u m/s: uncovered %u, starts %u / limit %u, short intervals %u\n",
            curved?"curved":"straight",speeds[speed],uncovered,starts,
            (unsigned)(5400*step/30)+1,spacing);
        CHECK(uncovered==0);CHECK(spacing==0);CHECK(starts>1 && starts<=(unsigned)(5400*step/30)+1);
        CHECK(max_slice<=500000);CHECK(startup<=1);CHECK(last_work_ns==5500000);
        settle();generation=map.generation;
        for (unsigned f=0;f<240;++f) {++dev.frames;prepare(RAIN);}
        CHECK(map.generation==generation && !build.active);
    }
    /* A stopped camera resumes across a map led in another direction.
     * Its old forward lead leaves only 24 m to the new covered edge. */
    rain_cover_set(1);dev.frames=0;fixture_ns=fixture_frame_start=0;
    rain_cover_begin(&map,20,-36,0);
    map.view=VIEW;map.camera=CAMERA;map.world_detail=M32(0x982b9c);
    scene_signature=cover_signature();prepared=1;prepared_frame=0;
    previous_x=previous_y=0;previous_time=0;
    build_dx=20;build_dy=-36;build_travel=0;
    unsigned turn_uncovered=0;
    for (unsigned f=0;f<180;++f) {
        MF32(CAMERA+0x40)=-(float)f*(60.f/90);MF32(CAMERA+0x44)=0;
        fixture_frame_start=fixture_ns;
        ++dev.frames;prepare(RAIN);
        if (!cover_contains(&map,MF32(CAMERA+0x40),0)) ++turn_uncovered;
    }
    printf("stop/turn path 60 m/s: uncovered %u\n",turn_uncovered);
    CHECK(turn_uncovered==0);
    rain_cover_set(0);free(G);G=NULL;
}
#endif

static void tests(void) {
    fixture();
    Cpu c={.ecx=RAIN,.esp=0x1000};
    rain_cover_set(0); expected_visible=3;
    for(unsigned i=0;i<10;++i) {M32(0x982b9c)=i%2?0:0x800;f_00758100(&c);CHECK(!rain_cover_lens(RAIN));CHECK(!rain_cover_curtain());CHECK(!rain_cover_texture());}
    CHECK(!map.height && map.allocations==0 && map.triangles==0 && !gpu_map);
    printf("Off allocations/draws: %llu/%llu\n",(unsigned long long)map.allocations,(unsigned long long)map.triangles);
    rain_cover_set(1);settle();expected_visible=2; c.ecx=RAIN; f_00758100(&c);
    CHECK(map.allocations==1 && map.triangles>0);
    CHECK(rain_cover_sample(&map,3,3,5));CHECK(!rain_cover_sample(&map,30,30,5));
    CHECK(!rain_cover_sample(&map,3,3,12));CHECK(!rain_cover_sample(&map,3,3,9.8));
    CHECK(!rain_cover_sample(&map,NAN,3,5));CHECK(!rain_cover_sample(&map,3,3,INFINITY));
    CHECK(M8(RAIN+0x2e60)==0 && M8(RAIN+0x2e60+12)==2 && M32(RAIN+0x284)==3);
    CHECK(MF32(RAIN+0x36a0)==.734f && MF32(RAIN+0x28c)==.9f);
    MF32(CAMERA+0x48)=5; CHECK(rain_cover_lens(RAIN));
    MF32(CAMERA+0x48)=12;CHECK(!rain_cover_lens(RAIN));
    rain_scope=RAIN;curtain_scope=1;
    for(unsigned i=0;i<4;++i){MF32(RAIN+0x34d0+16*i)=3;MF32(RAIN+0x34d4+16*i)=3;MF32(RAIN+0x34d8+16*i)=5;}
    CHECK(rain_cover_curtain());curtain_scope=0;CHECK(!rain_cover_curtain());
    uint64_t gen=map.generation;
    ++dev.frames; prepare(RAIN);CHECK(map.generation==gen);
    /* A teleport builds separately; sampling keeps the original front origin. */
    MF32(CAMERA+0x40)=120;++dev.frames;prepare(RAIN);
    CHECK(map.generation==gen || (!build.active && map.generation==gen+1));
    settle();CHECK(map.generation==gen+1 && map.x==120);
    CHECK(!rain_cover_sample(&map,3,3,5));
    MF32(CAMERA+0x40)=0;scene(1);settle();
    CHECK(!rain_cover_sample(&map,0,0,5));CHECK(rain_cover_sample(&map,3,3,5));
    M8(TEXTURE+0x56)=1;refresh();CHECK(!rain_cover_sample(&map,3,3,5));
    M8(TEXTURE+0x56)=0;refresh();CHECK(rain_cover_sample(&map,3,3,5));
    scene(0);M8(TEXTURE+0x55)=1;
    for(unsigned row=0;row<4;++row)for(unsigned col=2;col<4;++col)M8(PIXELS+row*16+col*4+3)=0;
    refresh();
    CHECK(rain_cover_sample(&map,-5,0,5));CHECK(!rain_cover_sample(&map,5,0,5));
    /* DXT1 transparent index, DXT3 explicit alpha and both DXT5 endpoint
     * modes are sampled by the exact production cutout decoder. */
    Obj t={.kind=K_TEXTURE,.fmt=0x31545844,.w=4,.h=4,.mem={PIXELS},.pitch={8}};
    memset(G+PIXELS,0,64);M16(PIXELS)=0;M16(PIXELS+2)=1;M32(PIXELS+4)=3;
    CHECK(alpha_texel(&t,0,0)==0);CHECK(alpha_texel(&t,1,0)==1);
    t.fmt=0x33545844;t.pitch[0]=16;M8(PIXELS)=0xf0;
    CHECK(alpha_texel(&t,0,0)==0);CHECK(alpha_texel(&t,1,0)==1);
    t.fmt=0x35545844;M8(PIXELS)=255;M8(PIXELS+1)=0;M8(PIXELS+2)=2;
    CHECK(fabsf(alpha_texel(&t,0,0)-6.f/7)<1e-6f);
    M8(PIXELS)=0;M8(PIXELS+1)=255;M8(PIXELS+2)=7;
    CHECK(alpha_texel(&t,0,0)==1);
    world_detail_tests(&c);
    scheduler_tests();
    M32(SECTION)=0;dev.frames+=4;prepare(RAIN);CHECK(!map.valid && !rain_cover_sample(&map,3,3,5));
    CHECK(renders==27);
    rain_cover_set(0);map=(RainCoverMap){0};back=(RainCoverMap){0};free(G);G=NULL;
}
int main(int argc, char **argv) { (void)argv; @autoreleasepool {
#ifdef RAIN_COVER_SYNTHETIC_CLOCK
    if (argc>1) { race_paths(); } else { tests(); }
#else
    (void)argc; tests();
#endif
    printf("rain cover: %d failures / %d checks\n",failures,checks);return failures?1:0;} }

#endif
