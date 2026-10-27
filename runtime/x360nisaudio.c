// A10: optional X360 cutscene (NIS) audio, 48 kHz instead of the PC's 44.1 kHz. PC remains the default.
// All 142 indexed streams are EA-XA (codec 10, implicit); PC PT00 states revision 2 (0x80) where X360 PT09 has 0x8c=4,
// and X360 stores the SCCl/SCDl scalars big-endian. The XA payload bytes are identical in layout, so there is no
// transcoding: header, scalars and the CSI/EVT/IDX sidecars are normalized (shared with x360copspeech.c) and the
// X360 BIG stays on disk with a sparse overlay of the changed four-byte words.
// Decoder: PC 00828DDE (XA 28-sample blocks; 0xEE blocks carry PCM history, via 00828C6B), the same translated code
// the game runs, driven offline (x360nisaudio_test.py) to count decoded samples without any audio device; the runtime
// preflight checks sizes/CRCs and stream chains only and runs without the GIL.
#include "host.h"
#include "roots.h"
#include "funcs.h"
#include "settings.h"
#include "x360copspeech.h"
#include <fcntl.h>
#include <math.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include <pthread.h>
#include <stdlib.h>
#include <strings.h>
#include <time.h>
#include <zlib.h>

enum { NIS_BIG, NIS_CSI, NIS_EVT, NIS_IDX, NIS_FILES };
static const char *const choices[] = {"PC", "X360"};
static const char *const names[] = {"BIG", "CSI", "EVT", "IDX"};
static Setting setting = {.id="audio.x360_nisaudio", .category = SET_AUDIO, .order = 90, .label="Cutscene audio",
    .help="X360 uses the console's 48 kHz cutscene sound and needs the X360 extract.",
    .type=SET_CHOICE, .choices=choices, .nchoices=2, .def=1, .env="NFS_X360_NISAUDIO", .restart=1};
__attribute__((constructor)) static void register_nisaudio(void) { settings_add(&setting); }

static const EaSide nis_side={.count=4,.evt_limit=0x120,.evt_words=1,.banks=4};
#define NIS_BIG_SIZE 200359168u

// Same translated XA decoder the game drives (00828b2f -> 00828DDE). Input is a PC-layout (PT00) stream; PCM stays in
// scratch memory, is counted, and is discarded. A final chunk that is not a multiple of 28 decodes a whole block.
int nisaudio_decode(const uint8_t *p, size_t n, CopSpeechProof *proof) {
    CopSpeechProof result;
    if (!nisaudio_stream((uint8_t *)p,n,0,&result) || ea_read16(p+10,0)) return 0;
    unsigned channels=result.channels, size_h=ea_read32(p+4,0);
    uint32_t arena=gmalloc_try(0x100000);
    if (!arena) return 0;
    memset(G+arena,0,0x100000);
    uint32_t input=arena+0x10000, output=arena+0x30000, state=arena+0x50000;   // state: count, hist1, hist2, in, out
    Cpu c={0}; c.cw=0x37f;
    uint32_t hist[6][2]={{0}};
    unsigned total=0, chunks=0;
    int ok=1;
    for (size_t at=size_h+12;ok && at+8<=n;) {
        unsigned size=ea_read32(p+at+4,0);
        if (!memcmp(p+at,"SCEl",4)) break;
        unsigned count=ea_read32(p+at+8,0), payload=12+channels*4, blocks=(count+27)/28;
        for (unsigned ch=0;ok && ch<channels;ch++) {
            unsigned start=ea_read32(p+at+12+ch*4,0);
            unsigned end=ch+1<channels ? ea_read32(p+at+16+ch*4,0) : size-payload;
            if (end-start>0x10000-64) { ok=0; break; }
            memcpy(G+input,p+at+payload+start,end-start); memset(G+input+end-start,0,64);
            M32(state)=blocks*28; M32(state+4)=hist[ch][0]; M32(state+8)=hist[ch][1];
            M32(state+12)=input; M32(state+16)=output+16;
            memset(G+output,0,16+blocks*28*4);
            c.esp=arena+0xff000; M32(c.esp+4)=state; f_00828DDE(&c);
            unsigned used=M32(state+12)-input;
            hist[ch][0]=M32(state+4); hist[ch][1]=M32(state+8);
            if ((int)M32(state)>0 || used>end-start) { ok=0; break; }
            for (unsigned i=0;i<count;i++) {
                float v=MF32(output+16+i*4);
                if (!isfinite(v)) { ok=0; break; }
                double pcm=fmax(-32768,fmin(32767,v));
                result.energy+=pcm*pcm;
                if (fabs(pcm)>result.peak) result.peak=fabs(pcm);
            }
        }
        total+=count; chunks++; at+=size;
    }
    gfree(arena);
    if (!ok || total!=result.samples || chunks!=result.blocks || !result.energy || !isfinite(result.energy)) return 0;
    if (proof) *proof=result;
    return 1;
}

static pthread_once_t once=PTHREAD_ONCE_INIT;
static X360MusicHandle selected[NIS_FILES];
static CopBig big={.fd=-1};
static int ready;
static uint64_t read_bytes, streams_open;

static double now_ms(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return t.tv_sec*1e3+t.tv_nsec/1e6; }

// The game addresses NISAudio.big by the PC's byte positions (they come from game data; the sidecars are never opened in an
// arrest scene), so reads are translated: PC stream (bank, n) start -> X360 start, then linear within the stream. The X360
// stream is ~8.8% longer, so a sequential read can run past the PC extent; a read at exactly a PC stream start begins a stream,
// any other read continues the active stream whose X360 extent covers it, else the PC extent containing it.
typedef struct { size_t ps, pe, xs, xlen; } NisMap;
static NisMap nis_map[142];
static unsigned nis_active[8], nis_nactive;   // indices into nis_map, most recent last
static pthread_mutex_t nis_lock=PTHREAD_MUTEX_INITIALIZER;
static size_t nis_end[142];                    // per stream: the PC position its last read ended at
static int nis_translate(size_t pos, size_t n, size_t *xpos, int *started, unsigned *which) {
    *started=0;
    int found=-1;
    pthread_mutex_lock(&nis_lock);
    for (unsigned i=0;i<142;i++) if (nis_map[i].ps==pos) { found=(int)i; *started=1; break; }
    if (found>=0) {
        // The longer X360 stream runs on past the next PC stream's start: a read that continues an active stream exactly
        // where its last read ended, still inside its X360 extent, is that stream's data, not a new stream (audit2 #3).
        for (unsigned i=nis_nactive;i--;) {
            const NisMap *m=&nis_map[nis_active[i]];
            if ((int)nis_active[i]!=found && nis_end[nis_active[i]]==pos && pos>=m->ps && pos-m->ps<m->xlen) { found=(int)nis_active[i]; *started=0; break; }
        }
    }
    if (found>=0 && *started) {
        unsigned k=0; for (unsigned i=0;i<nis_nactive;i++) if (nis_active[i]!=(unsigned)found) nis_active[k++]=nis_active[i];
        if (k==8) { memmove(nis_active,nis_active+1,7*sizeof *nis_active); k=7; }
        nis_active[k++]=(unsigned)found; nis_nactive=k;
    } else {
        for (unsigned i=nis_nactive;i-- && found<0;) {
            const NisMap *m=&nis_map[nis_active[i]];
            if (pos>=m->ps && pos-m->ps<m->xlen) found=(int)nis_active[i];
        }
        if (found<0) for (unsigned i=0;i<142;i++) if (pos>=nis_map[i].ps && pos<nis_map[i].pe) { found=(int)i; break; }
    }
    if (found>=0) nis_end[found]=pos+n;
    pthread_mutex_unlock(&nis_lock);
    if (found<0) return 0;
    *xpos=nis_map[found].xs+(pos-nis_map[found].ps); *which=(unsigned)found;
    return 1;
}

// What the game's own decoder pulls from the BIG: count stream starts and bytes (BOOT_DIAG only logs).
static size_t nis_read_at(void *source, void *dst, size_t n, size_t pos) {
    size_t xpos; int started; unsigned which;
    if (!nis_translate(pos,n,&xpos,&started,&which)) {
        if (diag_on) diag_event("x360nisaudio: BIG read at PC position %zu is in no indexed stream; nothing served",pos);
        return 0;
    }
    size_t got=ea_big_read_at(source,dst,n,xpos);
    __atomic_fetch_add(&read_bytes,got,__ATOMIC_RELAXED);
    if (diag_on && got>=36 && started && !memcmp(dst,"SCHl",4)) {
        unsigned rate=0, ch=1;
        const uint8_t *h=dst; unsigned size=ea_read32(h+4,0);
        for (unsigned at=12;size<=got && at+2<size && h[at]!=0xff;) {
            unsigned id=h[at++]; if (id>=0xfc) continue;
            unsigned w=h[at++], v=0; if (!w || w>4 || at+w>size) break;
            for (unsigned j=0;j<w;j++) v=v<<8|h[at++];
            if (id==0x84) rate=v; else if (id==0x82) ch=v;
        }
        unsigned k=__atomic_add_fetch(&streams_open,1,__ATOMIC_RELAXED);
        if (k<=64) diag_event("x360nisaudio: stream %u opened: PC position %zu -> X360 %zu (stream %u): PT%02x rate=%u channels=%u; BIG bytes served so far %llu",
            k,pos,xpos,which,h[10],rate,ch,(unsigned long long)__atomic_load_n(&read_bytes,__ATOMIC_RELAXED));
    } else {
        static unsigned logged;
        if (diag_on && logged<24) { logged++; diag_event("x360nisaudio: BIG read PC %zu -> X360 %zu (stream %u) n %zu -> %zu",pos,xpos,which,n,got); }
    }
    if (diag_on) {
        // Per-stream coverage: how far into the X360 stream the game has read, and whether the end chunk (SCEl) was served.
        static size_t high[142]; static int ended[142]; static unsigned tails;
        size_t rel=xpos-nis_map[which].xs+got;
        if (rel>high[which]) high[which]=rel;
        if (!ended[which]) for (size_t i=0;i+8<=got;i+=4) if (!memcmp((uint8_t *)dst+i,"SCEl",4) && ea_read32((uint8_t *)dst+i+4,0)==8) {
            ended[which]=1;
            diag_event("x360nisaudio: stream %u end chunk SCEl served at X360 +%zu of %zu bytes (PC extent %zu); highest byte read %zu",
                which,xpos-nis_map[which].xs+i,nis_map[which].xlen,nis_map[which].pe-nis_map[which].ps,high[which]);
            break;
        }
        if (rel+nis_map[which].xlen/16 >= nis_map[which].xlen && tails<400) { tails++; diag_event("x360nisaudio: stream %u tail read X360 +%zu n %zu (stream %zu bytes, PC extent %zu)",which,xpos-nis_map[which].xs,got,nis_map[which].xlen,nis_map[which].pe-nis_map[which].ps); }
    }
    return got;
}

static const char *x360_root(char *dir, size_t size) { snprintf(dir,size,"%s",roots_x360_files()); return dir; }

static void nis_init(void) {   // runs without the GIL (x360nisaudio_open): host memory and files only
    if (!setting.boot_value && !diag_on) return;   // Default PC has no cost outside BOOT_DIAG.
    static const size_t sizes[NIS_FILES]={NIS_BIG_SIZE,156,484,900};
    static const uint32_t crc[NIS_FILES]={0xa40e775d,0xaa124cbe,0x6825dde9,0x1f3eec5};
    uint8_t *source[NIS_FILES]={0};
    char root[2048], path[4096], rel[128], why[256]="PC selected", paths[NIS_FILES][4096]={{0}};
    unsigned converted=0; size_t bytes=0;
    double t0=now_ms();
    if (!setting.boot_value) {
        // BOOT_DIAG only: show the PC baseline the option replaces (first stream header of the retail BIG).
        if (game_dir && ea_find_case(game_dir,"SOUND/STREAMS/NISAudio.big",path,sizeof path)) {
            uint8_t head[64]; int fd=open(path,O_RDONLY);
            if (fd>=0 && ea_read_exact(fd,head,sizeof head,0) && !memcmp(head,"SCHl",4)) {
                snprintf(why,sizeof why,"PC BIG first stream PT%02x header %u bytes (44.1 kHz EA-XA)",head[10],ea_read32(head+4,0));
            }
            if (fd>=0) close(fd);
        }
        goto done;
    }
    x360_root(root,sizeof root);
    for (unsigned i=0;i<NIS_FILES;i++) {
        snprintf(rel,sizeof rel,"SOUND/STREAMS/NISAUDIO.%s",names[i]);
        if (!ea_find_case(root,rel,path,sizeof path)) { snprintf(why,sizeof why,"missing X360 %s",names[i]); goto done; }
        snprintf(paths[i],sizeof paths[i],"%s",path);
        if (i==NIS_BIG) {
            big.fd=ea_checked_file(path,sizes[i],crc[i]);
            if (big.fd<0) { snprintf(why,sizeof why,"X360 BIG size/checksum failed"); goto done; }
            continue;
        }
        source[i]=ea_map_file(path,sizes[i],0);
        if (!source[i] || crc32(0,source[i],(uInt)sizes[i])!=crc[i]) {
            snprintf(why,sizeof why,"X360 %s size/checksum failed",names[i]); goto done;
        }
        uint8_t *out=malloc(sizes[i]);
        if (!out || !ea_sidecar(i,&nis_side,source[i],sizes[i],out)) {
            free(out); snprintf(why,sizeof why,"X360 %s conversion failed",names[i]); goto done;
        }
        selected[i]=(X360MusicHandle){.data=out,.size=sizes[i]};
    }
    {
        const uint8_t *idx=selected[NIS_IDX].data;
        size_t maximum=0; unsigned streams=0;
        for (unsigned b=0;b<nis_side.banks;b++) {
            size_t bank=ea_read32(idx+0x58+b*16+8,0);
            for (unsigned j=0;j<idx[bank+5];j++) {
                size_t start,end;
                if (!ea_indexed_extent(idx,b,j,&start,&end,NIS_BIG_SIZE)) { snprintf(why,sizeof why,"X360 extent %u/%u invalid",b,j); goto done; }
                if (end-start>maximum) maximum=end-start;
                streams++;
            }
        }
        if (streams!=142) { snprintf(why,sizeof why,"X360 IDX lists %u streams, expected 142",streams); goto done; }
        // PC extents, to translate the PC positions the game asks for.
        {
            uint8_t pidx[900]; struct stat pst; int pfd=-1;
            if (!game_dir || !ea_find_case(game_dir,"SOUND/STREAMS/NISAudio.idx",path,sizeof path) || (pfd=open(path,O_RDONLY))<0 ||
                !ea_read_exact(pfd,pidx,sizeof pidx,0) || crc32(0,pidx,sizeof pidx)!=0xa5147bc7 ||
                !ea_find_case(game_dir,"SOUND/STREAMS/NISAudio.big",path,sizeof path) || stat(path,&pst) || pst.st_size!=184221440) {
                if (pfd>=0) close(pfd);
                snprintf(why,sizeof why,"PC NISAudio.idx/.big missing or not retail"); goto done;
            }
            close(pfd);
            unsigned k=0;
            for (unsigned b=0;b<nis_side.banks;b++) {
                size_t bank=ea_read32(idx+0x58+b*16+8,0);
                for (unsigned j=0;j<idx[bank+5];j++) {
                    size_t xs0,xe0,ps0,pe0;
                    if (!ea_indexed_extent(idx,b,j,&xs0,&xe0,NIS_BIG_SIZE) || !ea_indexed_extent(pidx,b,j,&ps0,&pe0,184221440) || k>=142) { snprintf(why,sizeof why,"PC extent %u/%u invalid",b,j); goto done; }
                    nis_map[k++]=(NisMap){ps0,pe0,xs0,xe0-xs0};
                }
            }
        }
        uint8_t *raw=malloc(maximum), *conv=malloc(maximum);
        int ok=raw && conv;
        for (unsigned b=0;ok && b<nis_side.banks;b++) {
            size_t bank=ea_read32(idx+0x58+b*16+8,0);
            for (unsigned j=0;ok && j<idx[bank+5];j++) {
                size_t start,end;
                ok=ea_indexed_extent(idx,b,j,&start,&end,NIS_BIG_SIZE) && ea_read_exact(big.fd,raw,end-start,start);
                if (ok) { memcpy(conv,raw,end-start); ok=nisaudio_stream(conv,end-start,1,NULL) && ea_collect_words(&big,start,raw,conv,end-start); }
                // No decode here: nisaudio_decode runs the translated decoder in guest memory, which needs the GIL, and
                // decoding the first stream (1.4 M samples x 6 ch) held it ~140 ms on the game's file thread, a 186 ms
                // hitch in free roam. The inputs are size/CRC-checked and the conversion is deterministic;
                // x360nisaudio_test.py decodes all 142 converted streams.
                if (ok) { converted++; bytes+=end-start; }
                else snprintf(why,sizeof why,"X360 stream %u/%u conversion failed",b,j);
            }
        }
        free(raw); free(conv);
        if (!ok || !ea_sort_words(&big)) goto done;
    }
    selected[NIS_BIG]=(X360MusicHandle){.size=NIS_BIG_SIZE,.read_at=nis_read_at,.source=&big};
    ready=1;
    snprintf(why,sizeof why,"%u streams converted (%zu bytes, %zu sparse words)",converted,bytes,big.count);
done:
    for (unsigned i=0;i<NIS_FILES;i++) {
        if (source[i]) munmap(source[i],sizes[i]);
        if (!ready) { free((void *)selected[i].data); memset(&selected[i],0,sizeof selected[i]); }
    }
    if (!ready) {
        if (big.fd>=0) close(big.fd);
        free(big.words); big=(CopBig){.fd=-1};
    }
    if (diag_on) {
        diag_event("x360nisaudio: option=%s files=%s rate=%u first_stream_decoded=%s ms=%.0f; %s",
            setting.boot_value?"x360":"pc",ready?"X360 BIG + converted CSI/EVT/IDX":"PC BIG/CSI/EVT/IDX",
            ready?48000:44100,"n/a (offline test)",now_ms()-t0,why);
        if (ready) for (unsigned i=0;i<NIS_FILES;i++) diag_event("x360nisaudio: %s -> %s",names[i],paths[i]);
    }
}

int x360nisaudio_open(const char *win, X360MusicHandle *out) {
    char normalized[512]; size_t len=strlen(win);
    if (len>=sizeof normalized) return 0;
    for (size_t i=0;i<=len;i++) normalized[i]=win[i]=='\\'?'/':win[i];
    const char *name=normalized;
    if (!strncasecmp(name,"C:/NFSMW/",9)) name+=9;
    else if (!strncasecmp(name,"./",2)) name+=2;
    for (unsigned i=0;i<NIS_FILES;i++) {
        char expected[64]; snprintf(expected,sizeof expected,"SOUND/STREAMS/nisaudio.%c%c%c",
            names[i][0]|32,names[i][1]|32,names[i][2]|32);
        if (strcasecmp(name,expected)) continue;
        // The first open validates and converts ~200 MB (size/CRC, 142 stream chains: ~45 ms warm, more from a cold
        // disk), host-only work: release the GIL so the game thread keeps running (this is the game's file thread).
        gil_release();
        pthread_once(&once,nis_init);
        gil_acquire();
        if (!ready) return 0;
        *out=selected[i]; return 1;
    }
    return 0;
}
