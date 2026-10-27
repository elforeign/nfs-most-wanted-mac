// Offline: no audio transport/device code is linked. Production converter, file
// selection, and translated PC decoder; corrupt/off fixtures must fail selection.
// Production sparse read proof: first N streams, 4096 random ranges, and every
// changed-word boundary against the old full-conversion oracle. OFFSET_BAD
// shifts one production patch offset; the Python runner requires that to fail.
// RSS is recorded in a separate production serve process without the oracle.
// 2026-10-24 ASan/UBSan: 709.641 -> 58.844 MiB, 32 streams per source;
// 142658560 parity bytes, 581780 changed words: 0 mismatches / shifted offset: 4.
#include <stdarg.h>
#include <sys/resource.h>
#include <time.h>
#include "heap.c" // Real production gmalloc_try, using a bounded guest test heap.
static uint32_t test_arena_alloc(uint32_t n);
#define gmalloc_try test_arena_alloc
#include "x360copspeech.c"
#undef gmalloc_try

uint8_t *G;
const char *game_dir;
int diag_on=1;
const char *diag_dir="/tmp";
uint32_t last_error;
static unsigned checks, failures, allocations;
static _Thread_local int test_gil=1;
static double test_ms(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return t.tv_sec*1e3+t.tv_nsec/1e6; }
static char diagnostic[2048], saved[48];
#define CHECK(x) do { checks++; if (!(x)) { if (failures<8) fprintf(stderr,"FAIL %d: %s\n",__LINE__,#x); failures++; } } while (0)
const char *app_pref(const char *key) { return !strcmp(key,"audio.x360_copspeech") ? getenv("NFS_COP_TEST_PREF") : NULL; }
void app_pref_set(const char *key, const char *value) { CHECK(!strcmp(key,"audio.x360_copspeech")); snprintf(saved,sizeof saved,"%s",value); }
void report_note(const char *fmt, ...) { (void)fmt; }
void diag_event(const char *fmt, ...) {
    va_list ap; va_start(ap,fmt); vsnprintf(diagnostic,sizeof diagnostic,fmt,ap); va_end(ap);
    puts(diagnostic);
}
uint64_t host_ticks(void) { return 0; } // No diagnostic files/timers in this test.
void gil_release(void) { CHECK(test_gil); test_gil=0; }
void gil_acquire(void) { CHECK(!test_gil); test_gil=1; }
int extratrax_open(int mus, const X360MusicHandle *mpf, const X360MusicHandle *base, X360MusicHandle *out) {
    (void)mus; (void)mpf; (void)base; (void)out; return 0;
}
const char *diag_symbol(uint32_t addr, uint32_t *off) { (void)addr; (void)off; return NULL; }
void diag_report(const char *file, const char *why) { (void)file; (void)why; CHECK(0); }
static uint32_t test_arena_alloc(uint32_t n) {
    CHECK(n==0x100000 && test_gil); allocations++;
    int fail=getenv("NFS_COP_TEST_ALLOC_FAIL")!=NULL;
    uint32_t held=fail ? gmalloc(n) : 0;
    uint32_t arena=gmalloc_try(n);
    if (fail) {
        CHECK(held && !arena); gfree(held);
        puts("forced arena exhaustion: production gmalloc_try returned 0; process survived");
    }
    return arena;
}
void dispatch(Cpu *c, uint32_t target) {
    // Only Snd's indirect memcpy. Everything that reads/decodes bits is original.
    CHECK(target==0x7d6d30);
    if (target!=0x7d6d30) exit(3);
    uint32_t dst=M32(c->esp+4), src=M32(c->esp+8), size=M32(c->esp+12);
    CHECK(dst<0x1000000 && size<=0x1000000-dst && src<0x1000000 && size<=0x1000000-src);
    memmove(G+dst,G+src,size); c->esp+=4;
}
static uint8_t *read_file(const char *path, size_t *size) {
    FILE *f=fopen(path,"rb"); if (!f) return NULL;
    fseek(f,0,SEEK_END); *size=(size_t)ftell(f); rewind(f);
    uint8_t *p=malloc(*size); CHECK(p && fread(p,1,*size,f)==*size); fclose(f); return p;
}
static void load_image(const char *path) {
    G=calloc(1,0x1000000); FILE *f=fopen(path,"rb"); CHECK(f && G);
    CHECK(fread(G,1,0x1000000,f)==0x1000000); fclose(f);
    res_heap=heap_create("cop-test",0xb00000,0xc00000,64);
}
static void settings_test(void) {
    settings_init(); Setting *s=settings_find("audio.x360_copspeech");
    CHECK(s && s->def==1 && s->restart && s->category==SET_AUDIO && s->nchoices==2);
    CHECK(!strcmp(s->label,"Police radio") && !strcmp(s->choices[0],"PC") && !strcmp(s->choices[1],"X360"));
    CHECK(s->boot_value==1 && settings_count()==4);   // X360 is the default since 2026-10-26 (VERSIONS.md)
    CHECK(settings_set(s,0) && s->value==0 && s->boot_value==1 && !strcmp(saved,"pc"));
    CHECK(!getenv("NFS_X360_COPSPEECH"));
    CHECK(settings_at(1)==settings_find("audio.x360_music") && settings_at(3)==s);
    settings_reset(s); CHECK(s->value==1 && s->boot_value==1 && !saved[0]);
}
static void compare_sidecars(const char *extract, const char *pc) {
    for (unsigned kind=COP_CSI;kind<COP_FILES;kind++) {
        char xp[4096], pp[4096], relative[128];
        snprintf(relative,sizeof relative,"SOUND/SPEECH/COPSPEECH_EN.%s",extensions[kind]);
        CHECK(ea_find_case(extract,relative,xp,sizeof xp));
        snprintf(relative,sizeof relative,"SOUND/SPEECH/copspeech.%s",extensions[kind]);
        CHECK(ea_find_case(pc,relative,pp,sizeof pp));
        size_t n=0,pn=0; uint8_t *x=read_file(xp,&n), *p=read_file(pp,&pn), *dst=malloc(n);
        CHECK(x && p && pn==n && copspeech_sidecar(kind,x,n,dst));
        CHECK(!copspeech_sidecar(kind,x,n-1,dst));
        if (kind==COP_CSI) CHECK(!memcmp(dst,p,n));
        if (kind==COP_EVT) {
            CHECK(!memcmp(dst,p,14) && !memcmp(dst+16,p+16,n-16));
            CHECK(ea_read16(dst+14,0)==0xb337 && ea_read16(p+14,0)==0xc147);
        }
        if (kind==COP_IDX) {
            // Only audio storage fields differ after conversion. Selector bytes,
            // IDs, masks, counts and bank layouts must all match the PC file.
            for (unsigned i=0;i<579;i++) {
                size_t t=0x58+i*16, b=ea_read32(dst+t+8,0);
                memcpy(dst+t+12,p+t+12,4);
                memcpy(dst+b+7,p+b+7,3);
                unsigned count=dst[b+5], stride=(dst[b+4]&127)+2;
                for (unsigned j=0;j<count;j++) memcpy(dst+b+12+j*stride,p+b+12+j*stride,2);
            }
            CHECK(!memcmp(dst,p,n));
        }
        x[0]^=0xff; CHECK(!copspeech_sidecar(kind,x,n,dst));
        free(x); free(p); free(dst);
    }
    puts("sidecars: CSI exact; EVT only build checksum b337/c147; IDX only audio extents/sector fields differ");
}
static size_t stream_at(const uint8_t *idx, unsigned wanted, size_t *extent) {
    for (unsigned i=0;i<579;i++) {
        size_t t=0x58+i*16, b=ea_read32(idx+t+8,0), base=ea_read32(idx+t+12,0);
        unsigned count=idx[b+5], stride=(idx[b+4]&127)+2, unit=(idx[b+7]+1)*256;
        if (wanted<count) {
            size_t start=base+ea_read16(idx+b+12+wanted*stride,1)*unit;
            size_t end=base+(wanted+1<count ? ea_read16(idx+b+12+(wanted+1)*stride,1) : ea_read16(idx+b+8,0))*unit;
            *extent=end-start; return start;
        }
        wanted-=count;
    }
    CHECK(0); return 0;
}
static void selected_read(void *bytes, size_t at, size_t n) {
    X360MusicHandle h;
    CHECK(x360copspeech_open("SOUND/SPEECH/copspeech.big",&h));
    CHECK(x360music_seek(&h,(int64_t)at,0)==(int64_t)at);
    CHECK(x360music_read(&h,bytes,n)==n);
}
// Independent oracle: the previous full private conversion, unchanged. Exercise
// first N whole streams, every changed word boundary, and seeded arbitrary reads.
static void parity_test(unsigned nstreams) {
    size_t size=selected[COP_BIG].size;
    uint8_t *gold=ea_map_file(selected_paths[COP_BIG],size,1); CHECK(gold);
    if (!gold) return;
    for (unsigned i=0;i<13562;i++) {
        size_t length, at=stream_at(selected[COP_IDX].data,i,&length);
        CHECK(copspeech_stream(gold+at,length,1,NULL));
    }
    if (getenv("NFS_COP_TEST_OFFSET_BAD")) big.words[0].offset++;
    size_t total=0;
    for (unsigned i=0;i<nstreams;i++) {
        size_t length, at=stream_at(selected[COP_IDX].data,i,&length);
        uint8_t *bytes=malloc(length); CHECK(bytes);
        selected_read(bytes,at,length); CHECK(!memcmp(bytes,gold+at,length));
        total+=length; free(bytes);
    }
    uint8_t bytes[65536]; unsigned mismatches=0;
    for (size_t i=0;i<big.count;i++) {
        size_t at=big.words[i].offset;
        // Reads start/end within converted fields, and straddle their boundaries.
        for (unsigned j=0;j<4;j++) {
            size_t pos=at+j, length=j+1;
            selected_read(bytes,pos,length);
            mismatches+=memcmp(bytes,gold+pos,length)!=0;
        }
    }
    CHECK(!mismatches);
    uint32_t random=0x360c0ffe;
    for (unsigned i=0;i<4096;i++) {
        random=random*1664525u+1013904223u; size_t pos=random%size;
        random=random*1664525u+1013904223u; size_t length=1+random%sizeof bytes;
        if (length>size-pos) length=size-pos;
        selected_read(bytes,pos,length); CHECK(!memcmp(bytes,gold+pos,length));
        total+=length;
    }
    printf("parity: first %u streams + 4096 random reads, %zu bytes; %zu word boundaries, %u mismatches\n",
        nstreams,total,big.count,mismatches);
    munmap(gold,size);
}
static void corrupt_test(const uint8_t *data, size_t n) {
    uint8_t *bad=malloc(n); CopSpeechProof proof; SpeechHeader h;
    CHECK(speech_header(data,n,&h));
    size_t offsets[]={0,4,10,12,20,26,h.size,h.size+4,h.size+8,h.size+12+8,h.size+12+12};
    unsigned rejected=0;
    for (unsigned i=0;i<sizeof offsets/sizeof offsets[0];i++) {
        memcpy(bad,data,n); bad[offsets[i]]^=0xff;
        int ok=copspeech_decode(bad,n,&proof); CHECK(!ok); rejected+=!ok;
    }
    CHECK(!copspeech_decode(data,h.size+12,&proof));
    printf("corrupt headers: %u/%zu rejected, plus truncation\n",rejected,sizeof offsets/sizeof offsets[0]);
    free(bad);
}
static void guest_header_test(const uint8_t *data, size_t n) {
    SpeechHeader header; CHECK(speech_header(data,n,&header));
    uint32_t input=0xd00000, format=0xd00100, attr=0xd00200, samples=0xd00300, endian=0xd00400;
    memcpy(G+input,data,header.size);
    Cpu c={0}; c.cw=0x37f; c.esp=0xe00000;
    M32(c.esp+4)=0; M32(c.esp+8)=input+8; M32(c.esp+12)=format;
    M32(c.esp+16)=attr; M32(c.esp+20)=samples; M32(c.esp+24)=endian;
    f_00821C7C(&c);
    CHECK(M16(format)==header.rate && M8(format+2)==header.channels && M8(format+3)==header.codec);
    CHECK(M8(attr+7)==2 && M32(samples)==header.samples && M8(endian)==0);
}
static void decode_test(const char *pc, unsigned nstreams) {
    char path[4096]; size_t pn;
    CHECK(ea_find_case(pc,"SOUND/SPEECH/copspeech.idx",path,sizeof path));
    uint8_t *pi=read_file(path,&pn);
    CHECK(ea_find_case(pc,"SOUND/SPEECH/copspeech.big",path,sizeof path));
    uint8_t *pb=ea_map_file(path,192753408,0); CHECK(pb);
    Cpu init={0}; init.esp=0xe00000; f_008259DB(&init);
    CHECK(M32(0x9c234c)==0x826c28 && M32(0x9c2358)==0x826c28);
    CHECK(M32(0x9c2354)==0x826b4f && M32(0x9c2360)==0x826b4f);
    CHECK(M32(0x9c2350)==0x826927 && M32(0x9c235c)==0x826927);
    unsigned first_stereo=0,last_stereo=0,stereo_total=0;
    for (unsigned index=0;index<13562;index++) {
        size_t length, at=stream_at(selected[COP_IDX].data,index,&length); SpeechHeader header;
        uint8_t bytes[40]; selected_read(bytes,at,sizeof bytes);
        CHECK(speech_header(bytes,sizeof bytes,&header));
        if (header.channels==2) { if (!stereo_total) first_stereo=index; last_stereo=index; stereo_total++; }
    }
    CHECK(stereo_total==177);
    double xseconds=0,pseconds=0; unsigned xsamples=0,psamples=0,stereo_decoded=0;
    for (unsigned i=0;i<nstreams;i++) {
        unsigned index=i==nstreams-1 ? 13561 : i*13562/nstreams;
        if (nstreams>=4 && i==nstreams-2) index=first_stereo;
        if (nstreams>=4 && i==nstreams-3) index=last_stereo;
        size_t xn, pn, xoff=stream_at(selected[COP_IDX].data,index,&xn), poff=stream_at(pi,index,&pn);
        CHECK(xoff+xn<=selected[COP_BIG].size && poff+pn<=192753408);
        uint8_t *xb=malloc(xn); CHECK(xb); selected_read(xb,xoff,xn);
        guest_header_test(xb,xn); guest_header_test(pb+poff,pn);
        CopSpeechProof x={0},p={0};
        CHECK(copspeech_decode(xb,xn,&x));
        CHECK(copspeech_decode(pb+poff,pn,&p));
        CHECK(x.codec==22 && x.rate==36000 && p.codec==4 && p.rate==24000);
        CHECK(x.energy>0 && p.energy>0 && x.samples && p.samples);
        CHECK(x.channels==p.channels); stereo_decoded+=x.channels==2;
        CHECK(fabs(x.samples/36000.0-p.samples/24000.0)<=1.0/24000.0);
        xsamples+=x.samples; psamples+=p.samples; xseconds+=x.samples/36000.0; pseconds+=p.samples/24000.0;
        if (!i) {
            CHECK(x.samples==709230 && p.samples==472820 && x.blocks==296 && p.blocks==296);
            corrupt_test(xb,xn); corrupt_test(pb+poff,pn);
        }
        free(xb);
    }
    if (nstreams>=4) CHECK(stereo_decoded>=2);
    printf("stereo: %u/13562 in bank; %u selected streams decoded in both channels\n",stereo_total,stereo_decoded);
    printf("decoded %u indexed streams EACH: X360 %u samples %.6f s; PC %u samples %.6f s; all finite/non-silent, header/index durations agree\n",
        nstreams,xsamples,xseconds,psamples,pseconds);
    munmap(pb,192753408); free(pi);
}
static void serve_test(int want) {
    const char *names[]={"sound\\speech\\copspeech.idx","SOUND/SPEECH/copspeech.big","C:\\NFSMW\\SOUND\\SPEECH\\copspeech.csi","./SOUND/SPEECH/copspeech.evt"};
    X360MusicHandle h={0}, again={0};
    CHECK(!x360copspeech_open("SOUND/SPEECH/otherspeech.big",&h));
    CHECK(!x360copspeech_open("SOUND/SPEECH/copspeech.big.bak",&h));
    CHECK(!x360copspeech_open("NOTSOUND/SPEECH/copspeech.idx",&h));
    double begin=test_ms();
    for (unsigned i=0;i<4;i++) {
        X360MusicHandle before=h;
        CHECK(x360copspeech_open(names[i],&h)==want);
        if (!want) CHECK(!memcmp(&before,&h,sizeof h));
    }
    printf("cop_init + four opens: %.3f ms; runtime decoder allocations: %u\n",test_ms()-begin,allocations);
    CHECK(!allocations); // old runtime decodes PC and X360 first streams: fails here
    begin=test_ms();
    for (unsigned i=0;i<256;i++) CHECK(x360copspeech_open(names[i%4],&again)==want);
    printf("cached open: %.3f us/call (256 calls)\n",(test_ms()-begin)*1000/256);
    CHECK(ready==want);
    if (!want) {
        CHECK(big.fd==-1 && !big.words && !big.count);
        for (unsigned i=0;i<COP_FILES;i++) CHECK(!selected[i].data && !selected[i].read_at);
        puts("PC path: all four opens declined; output handle untouched; no X360 backing retained");
    }
    if (want && ready) {
        CHECK(x360copspeech_open(names[1],&h) && x360copspeech_open(names[1],&again));
        CHECK(h.pos==0 && again.pos==0 && h.data==again.data);
        CHECK(!h.data && h.read_at==ea_big_read_at && h.source==&big && big.fd>=0);
        uint8_t first[4096], cached[4096];
        CHECK(h.read_at(h.source,first,sizeof first,0)==sizeof first);
        CHECK(again.read_at(again.source,cached,sizeof cached,0)==sizeof cached && !memcmp(first,cached,sizeof first));
        for (unsigned i=COP_CSI;i<COP_FILES;i++) {
            X360MusicHandle side={0};
            CHECK(x360copspeech_open(names[i==COP_CSI?2:i==COP_EVT?3:0],&side));
            CHECK(side.size==selected[i].size && !memcmp(side.data,selected[i].data,side.size));
        }
        uint8_t bytes[40]; CHECK(x360music_read(&h,bytes,32)==32 && !memcmp(bytes,"SCHl",4));
        CHECK(h.pos==32 && again.pos==0);
        CHECK(x360music_seek(&h,-4,2)==(int64_t)h.size-4);
        CHECK(x360music_read(&h,bytes,40)==4 && !x360music_read(&h,bytes,40));
        CHECK(x360music_seek(&h,-1,0)==-1);
        CHECK(x360music_seek(&h,1,2)==(int64_t)h.size+1 && !x360music_read(&h,bytes,sizeof bytes));
        CHECK(x360music_seek(&h,0,0)==0 && !x360music_read(&h,bytes,0) && h.pos==0);
        CHECK(x360music_seek(&h,INT64_MAX,0)==INT64_MAX && x360music_seek(&h,1,1)==-1);
        CHECK(x360music_seek(&h,0,3)==-1);
    }
}
int main(int argc, char **argv) {
    if (argc==2 && !strcmp(argv[1],"settings")) settings_test();
    else if (argc==7) {
        load_image(argv[2]); game_dir=argv[4]; settings_init();
        if (getenv("NFS_COP_TEST_QUIET")) diag_on=0;
        if (!strcmp(argv[3],"-")) unsetenv("NFS_X360_FILES");
        else setenv("NFS_X360_FILES",argv[3],1);
#ifndef COP_TEST_BASELINE
        if (getenv("NFS_COP_TEST_WARM")) {
            // Warmup must finish even before any guest memory is available.
            uint8_t *guest=G; G=NULL; test_gil=0; double begin=test_ms();
            x360copspeech_warm(); pthread_once(&once,cop_init);
            printf("boot warmup without guest memory: %.3f ms\n",test_ms()-begin);
            G=guest; test_gil=1;
        }
#endif
        serve_test(atoi(argv[5]));
        if (!diag_on && !setting.boot_value) CHECK(!allocations);
        if (atoi(argv[6]) && ready) {
            compare_sidecars(argv[3],argv[4]); decode_test(argv[4],(unsigned)atoi(argv[6]));
            if (getenv("NFS_COP_TEST_PARITY")) {
                if (big.count) parity_test((unsigned)atoi(argv[6]));
            }
        }
        if (ready && getenv("NFS_COP_TEST_ALLOC_FAIL")) {
            size_t n, at=stream_at(selected[COP_IDX].data,0,&n);
            uint8_t *bytes=malloc(n); CHECK(bytes); selected_read(bytes,at,n);
            CHECK(!copspeech_decode(bytes,n,NULL)); free(bytes); // offline decoder still fails safely on arena exhaustion
        }
        CHECK(heap_check(res_heap) && heap_stats(res_heap).live_blocks==0);
    } else return 2;
    ; struct rusage usage; CHECK(!getrusage(RUSAGE_SELF,&usage));
    printf("peak RSS: %.3f MiB\n",usage.ru_maxrss/1048576.0);
    printf("%s: %u checks, %u failures\n",failures?"FAIL":"PASS",checks,failures);
    return failures!=0;
}
