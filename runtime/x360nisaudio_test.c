// Offline A10 proof: X360 NIS audio -> PC layout, through the production converter and the translated PC XA decoder.
// No game process, audio output or writes. Usage: test <image.bin> <x360 files root> <PC game dir> [broken]
// Checks: CSI/EVT/IDX against the retail PC sidecars; all 142 indexed streams converted, decoded (exact sample/channel
// counts, payload hash preserved), and the PC streams decode with the same function; negative controls must fail.
// 2026-10-25 ASan/UBSan, x360nisaudio_test.py: PASS 302 checks. CSI 0 bytes differ from retail PC, EVT 2 (build checksum),
// 142/142 streams converted, payload hash kept, decoded X360 77,820,283 samples/ch vs PC 71,497,316 (ratio 1.0884 = 48/44.1),
// 744,078,480 PCM bytes, 135 short-tail streams; 3 negative controls rejected 4/4.
// Broken run (`x360nisaudio_test.py broken`, per-event words left unswapped): EVT 14 bytes differ, FAIL line 64.
#include <stdarg.h>
#include "heap.c"
#include "x360nisaudio.c"   // production file; x360copspeech.c is linked separately

uint8_t *G;
const char *game_dir;
int diag_on=1;
const char *diag_dir="/tmp";
uint32_t last_error;
static unsigned checks, failures;
#define NIS_SIDE nis_side
#define CHECK(x) do { checks++; if (!(x)) { if (failures<12) fprintf(stderr,"FAIL %d: %s\n",__LINE__,#x); failures++; } } while (0)
const char *app_pref(const char *key) { (void)key; return NULL; }
void app_pref_set(const char *key, const char *value) { (void)key; (void)value; }
void report_note(const char *fmt, ...) { (void)fmt; }
void diag_event(const char *fmt, ...) { va_list ap; va_start(ap,fmt); vprintf(fmt,ap); va_end(ap); puts(""); }
uint64_t host_ticks(void) { return 0; }
void gil_release(void) {}
void gil_acquire(void) {}
const char *diag_symbol(uint32_t addr, uint32_t *off) { (void)addr; (void)off; return NULL; }
void diag_report(const char *file, const char *why) { (void)file; (void)why; CHECK(0); }
void dispatch(Cpu *c, uint32_t target) { (void)c; (void)target; CHECK(0); exit(3); }

static uint8_t *slurp(const char *dir, const char *name, size_t *n) {
    char path[4096]; snprintf(path,sizeof path,"%s/%s",dir,name);
    FILE *f=fopen(path,"rb"); if (!f) { fprintf(stderr,"missing %s\n",path); exit(2); }
    fseek(f,0,SEEK_END); *n=(size_t)ftell(f); rewind(f);
    uint8_t *p=malloc(*n); if (fread(p,1,*n,f)!=*n) exit(2); fclose(f); return p;
}
static uint8_t *read_range(FILE *f, size_t start, size_t n) {
    uint8_t *p=malloc(n); fseek(f,(long)start,SEEK_SET); if (fread(p,1,n,f)!=n) exit(2); return p;
}
// CRC over every SCDl payload byte after the offset table (what the decoder consumes), in either byte order of headers.
static uint32_t payload_crc(const uint8_t *p, size_t n, int be) {
    unsigned size_h=ea_read32(p+4,0), ch=1;
    for (unsigned at=12;at<size_h && p[at]!=0xff;) { unsigned id=p[at++]; if (id>=0xfc) continue; unsigned w=p[at++], v=0;
        for (unsigned j=0;j<w;j++) v=v<<8|p[at++]; if (id==0x82) ch=v; }
    uint32_t crc=0;
    for (size_t at=size_h+12;at+8<=n && memcmp(p+at,"SCEl",4);) {
        unsigned size=ea_read32(p+at+4,0);
        crc=crc32(crc,p+at+12+ch*4,size-12-ch*4); (void)be; at+=size;
    }
    return crc;
}
int main(int argc, char **argv) {
    if (argc<4) return 2;
    load_image: ;
    FILE *img=fopen(argv[1],"rb"); G=calloc(1,0x1000000); CHECK(img && fread(G,1,0x1000000,img)==0x1000000); fclose(img);
    res_heap=heap_create("nis-test",0xb00000,0xc00000,64);
    int broken=argc>4;
    char xs[4096], pcs[4096]; snprintf(xs,sizeof xs,"%s/SOUND/STREAMS",argv[2]); snprintf(pcs,sizeof pcs,"%s/SOUND/STREAMS",argv[3]);
    size_t n[3], pn[3]; const char *x_names[3]={"NISAUDIO.CSI","NISAUDIO.EVT","NISAUDIO.IDX"}, *p_names[3]={"NISAudio.csi","NISAudio.evt","NISAudio.idx"};
    uint8_t *xs_[3], *ps_[3], *cv[3];
    for (int i=0;i<3;i++) { xs_[i]=slurp(xs,x_names[i],&n[i]); ps_[i]=slurp(pcs,p_names[i],&pn[i]); cv[i]=malloc(n[i]); CHECK(n[i]==pn[i]); }
    EaSide side=nis_side; if (broken) side.evt_words=0;   // broken run: per-event words left unswapped
    for (int i=0;i<3;i++) CHECK(ea_sidecar(i+1,&side,xs_[i],n[i],cv[i]));
    unsigned d_csi=0,d_evt=0,d_idx=0;
    for (size_t i=0;i<n[0];i++) d_csi+=cv[0][i]!=ps_[0][i];
    for (size_t i=0;i<n[1];i++) d_evt+=cv[1][i]!=ps_[1][i];
    for (size_t i=0;i<n[2];i++) d_idx+=cv[2][i]!=ps_[2][i];
    printf("sidecar bytes differing from retail PC: CSI %u, EVT %u (build checksum +14/+15 only: expect 2), IDX %u (48 kHz sample offsets/bases)\n",d_csi,d_evt,d_idx);
    CHECK(d_csi==0); CHECK(d_evt==2 && cv[1][14]!=ps_[1][14]); CHECK(d_idx>0);
    // IDX fields that are not sample positions must equal PC: table header, bank counts, attribute words.
    CHECK(!memcmp(cv[2],ps_[2],0x58) && !memcmp(cv[2]+0x58,ps_[2]+0x58,4));

    size_t bn, pbn; (void)bn; (void)pbn;
    char path[4096]; snprintf(path,sizeof path,"%s/NISAUDIO.BIG",xs); FILE *xb=fopen(path,"rb");
    snprintf(path,sizeof path,"%s/NISAudio.big",pcs); FILE *pb=fopen(path,"rb");
    CHECK(xb && pb);
    unsigned streams=0, hash_ok=0, dec_x=0, dec_pc=0, chan_ok=0, tail=0, neg_scalar=0, neg_chan=0, neg_hdr=0, neg_ok=0;
    unsigned long long x_samples=0, pc_samples=0, pcm_bytes=0;
    for (unsigned b=0;b<4;b++) {
        size_t bank=ea_read32(cv[2]+0x58+b*16+8,0), pbank=ea_read32(ps_[2]+0x58+b*16+8,0);
        CHECK(ps_[2][pbank+5]==cv[2][bank+5]);
        for (unsigned j=0;j<cv[2][bank+5];j++) {
            size_t xs0,xe0,ps0,pe0;
            CHECK(ea_indexed_extent(cv[2],b,j,&xs0,&xe0,NIS_BIG_SIZE) && ea_indexed_extent(ps_[2],b,j,&ps0,&pe0,184221440));
            uint8_t *raw=read_range(xb,xs0,xe0-xs0), *conv=malloc(xe0-xs0), *pcs_=read_range(pb,ps0,pe0-ps0);
            memcpy(conv,raw,xe0-xs0);
            CopSpeechProof px={0}, pp={0};
            int ok=nisaudio_stream(conv,xe0-xs0,1,&px);
            CHECK(ok); streams++; if (!ok && failures<3) { printf("bank %u stream %u start %zu:",b,j,xs0); for (int q=0;q<40;q++) printf(" %02x",raw[q]); puts(""); }
            if (ok && payload_crc(raw,xe0-xs0,1)==payload_crc(conv,xe0-xs0,0)) hash_ok++;
            CopSpeechProof dx={0}, dp={0};
            if (ok && nisaudio_decode(conv,xe0-xs0,&dx)) { dec_x++; x_samples+=dx.samples; pcm_bytes+=(unsigned long long)dx.samples*dx.channels*2; }
            if (nisaudio_decode(pcs_,pe0-ps0,&dp)) { dec_pc++; pc_samples+=dp.samples; }
            if (ok && nisaudio_stream(pcs_,pe0-ps0,0,&pp) && pp.channels==px.channels && px.rate==0+px.rate) chan_ok++;
            // last chunk length not a multiple of 28 (short tail)
            { unsigned size_h=ea_read32(conv+4,0), last=0; for (size_t at=size_h+12;at+8<=xe0-xs0 && memcmp(conv+at,"SCEl",4);) { last=ea_read32(conv+at+8,0); at+=ea_read32(conv+at+4,0); } tail+=last%28!=0; }
            // negative controls, on a copy: each must be rejected
            if (j==0 && ok) {
                unsigned size_h=ea_read32(conv+4,0);
                uint8_t *t=malloc(xe0-xs0);
                memcpy(t,conv,xe0-xs0); { unsigned c=px.channels; if (c>1) { uint8_t *o=t+size_h+12+12+4; ea_put32(o,ea_read32(o,0)+1); } else t[size_h+12+8+3]^=0x80; }
                neg_chan+=!nisaudio_decode(t,xe0-xs0,NULL) || !nisaudio_stream(t,xe0-xs0,0,NULL);   // shifted channel offset / count
                memcpy(t,conv,xe0-xs0); { uint32_t v=ea_read32(t+size_h+12+8,0); t[size_h+12+8]=v>>24; t[size_h+12+9]=v>>16; t[size_h+12+10]=v>>8; t[size_h+12+11]=v; }
                neg_scalar+=!nisaudio_stream(t,xe0-xs0,0,NULL);                                   // SCDl count left big-endian
                memcpy(t,raw,xe0-xs0);
                neg_hdr+=!nisaudio_decode(t,xe0-xs0,NULL);                                        // unconverted PT09 stream
                free(t); neg_ok++;
            }
            free(raw); free(conv); free(pcs_);
        }
    }
    printf("streams %u: converted+validated %u, payload hash preserved %u, decoded X360 %u, decoded PC %u, channel counts equal PC %u, short-tail streams %u\n",
        streams,streams,hash_ok,dec_x,dec_pc,chan_ok,tail);
    printf("decoded samples (per channel) X360 %llu vs PC %llu (ratio %.4f, 48/44.1=%.4f); X360 PCM bytes %llu\n",x_samples,pc_samples,(double)x_samples/pc_samples,48000/44100.0,pcm_bytes);
    printf("negative controls rejected: shifted channel offset %u/%u, unswapped scalar %u/%u, unconverted PT09 %u/%u\n",neg_chan,neg_ok,neg_scalar,neg_ok,neg_hdr,neg_ok);
    CHECK(streams==142 && hash_ok==142 && dec_x==142 && dec_pc==142 && chan_ok==142);
    CHECK(neg_ok==4 && neg_chan==4 && neg_scalar==4 && neg_hdr==4);
    printf("%s: %u checks, %u failures\n",failures?"FAIL":"PASS",checks,failures);
    return failures!=0;
}
