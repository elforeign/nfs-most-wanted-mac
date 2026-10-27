// A6: optional English X360 police radio. PC remains the default.
// PC 00821C7C parses PT attributes; 00822C41 dispatches codecs 8,9,10,4,22,16,23.
// 008259DB registers both MicroTalk variants (4 = 10:1, 22 = 5:1) with the SAME
// 00826B4F/008269BF decoder. X360 825DC6E8 dispatches both to 82613538 (registration at 825DC1A8).
// Normalize PT09's implicit MicroTalk revision 2 and BE packet words for PC PT00.
// The compressed MicroTalk bitstream and all sector/sample offsets stay intact.
#include "host.h"
#include "roots.h"
#include "funcs.h"
#include "settings.h"
#include "x360copspeech.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <pthread.h>
#include <stdlib.h>
#include <strings.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <zlib.h>

enum { COP_BIG, COP_CSI, COP_EVT, COP_IDX, COP_FILES };
static const char *const choices[] = {"PC", "X360"};
static const char *const extensions[] = {"big", "csi", "evt", "idx"};
static Setting setting = {.id="audio.x360_copspeech", .category = SET_AUDIO, .order = 80, .label="Police radio",
    .help="X360 uses the console's police voices and needs the English X360 speech files.",
    .type=SET_CHOICE, .choices=choices, .nchoices=2, .def=1, .env="NFS_X360_COPSPEECH", .restart=1};
__attribute__((constructor)) static void register_copspeech(void) { settings_add(&setting); }

static void swap_field(const uint8_t *src, uint8_t *dst, size_t off, unsigned n) {
    for (unsigned i=0;i<n;i++) dst[off+i]=src[off+n-i-1];
}

// Retail layouts, traced in PC 00832616/008349FA (banks), 00831BEB/00831C0C,
// 00834314/0083428E (events), 0083493F (32-byte Csis function handles).
// Source and destination are distinct. CRC pinning at selection protects these
// exact English layouts; each offset is checked independently before use.
int copspeech_sidecar(unsigned kind, const uint8_t *s, size_t n, uint8_t *d) {
    static const EaSide cop={.count=139,.evt_limit=0x1df8,.evt_head8=1,.banks=579};
    if ((kind==COP_CSI && n!=5136) || (kind==COP_EVT && n!=15547) ||
        (kind==COP_IDX && n!=76976) || kind<COP_CSI || kind>COP_IDX) return 0;
    return ea_sidecar(kind,&cop,s,n,d);
}
int ea_sidecar(unsigned kind, const EaSide *p, const uint8_t *s, size_t n, uint8_t *d) {
    if (!s || !d || s==d || kind<COP_CSI || kind>COP_IDX) return 0;
    memcpy(d,s,n);
#define SW(o,w) do { size_t at_=(o); if (at_>n || (w)>n-at_) return 0; swap_field(s,d,at_,w); } while (0)
    if (kind==COP_CSI) {
        if (memcmp(s,"MOIR\0\2\0\1",8) || s[8]!=5 || ea_read16(s+10,1)!=p->count) return 0;
        d[8]=0; // Csis platform: X360 -> PC, not a speech-content change.
        for (size_t off=10;off<40;off+=2) SW(off,2);
        for (unsigned i=0;i<p->count;i++) {
            size_t off=40+i*12;
            SW(off,4); SW(off+4,4); SW(off+8,2); SW(off+10,2);
            unsigned name=ea_read32(s+off+4,1);
            if (name<40+p->count*12 || name>=n || !memchr(s+name,0,n-name)) return 0;
        }
    } else if (kind==COP_EVT) {
        if (memcmp(s,"\3\22\74\7",4) || ea_read32(s+4,1)!=p->evt_limit || ea_read16(s+16,1)!=p->count) return 0;
        SW(4,4); if (p->evt_head8) SW(8,4);
        for (size_t off=12;off<24;off+=2) SW(off,2);
        for (unsigned i=0;i<p->count;i++) {
            size_t index=24+i*2, e=ea_read16(s+index,1)*4u;
            SW(index,2);
            if (e<((24+p->count*2+3)&~3u) || e+12>p->evt_limit) return 0;
            SW(e,2); SW(e+2,2); SW(e+4,2);
            if (e+12+s[e+6]*2u>p->evt_limit) return 0;
            if (p->evt_words && s[e+6]) {
                // Event-level u32 words (NIS) sit between the 4-aligned sub-offset array and the first sub.
                size_t first=SIZE_MAX, words=e+((12+s[e+6]*2u+3)&~3u);
                for (unsigned j=0;j<s[e+6];j++) { size_t sub=e+ea_read16(s+e+12+j*2,1)*4u; if (sub<first) first=sub; }
                if (first<words || first>p->evt_limit || (first-words)&1) return 0;
                if (first-words>=4) SW(words,4);     // one u32, then u16 fields (retail NIS: 0x15 then 7,0)
                for (size_t at=words+4;at+2<=first;at+=2) SW(at,2);
            }
            for (unsigned j=0;j<s[e+6];j++) {
                size_t at=e+12+j*2, sub=e+ea_read16(s+at,1)*4u;
                SW(at,2);
                if (sub<e+12 || sub+8>p->evt_limit) return 0;
                unsigned parts=s[sub+2]>>2, attrs=s[e+8]>>4;
                if (sub+8+((parts+3)&~3u)+attrs*4>p->evt_limit) return 0;
                for (unsigned k=0;k<attrs;k++) SW(sub+8+((parts+3)&~3u)+k*4,4);
                for (unsigned k=0;k<parts;k++) {
                    size_t bank=sub+s[sub+8+k]*4u;
                    if (bank<sub+8 || bank+8>p->evt_limit || bank+8+s[bank+4]*8u>p->evt_limit) return 0;
                    SW(bank,2); SW(bank+6,2);
                    for (unsigned a=0;a<s[bank+4];a++) SW(bank+8+a*8,4);
                }
            }
        }
        for (unsigned i=0;i<p->count;i++) {
            size_t at=p->evt_limit+i*32;
            SW(at,4); SW(at+4,2); SW(at+6,2);
        }
        // Keep the X360 build checksum at +14. The PC loader never compares it.
    } else {
        if (ea_read32(s,1)!=1 || ea_read32(s+4,1)!=p->banks || ea_read32(s+0x54,1)!=p->banks) return 0;
        for (size_t off=0;off<0x58+p->banks*16;off+=4) SW(off,4);
        for (unsigned i=0;i<p->banks;i++) {
            size_t t=0x58+i*16, bank=ea_read32(s+t+8,1), len=ea_read32(s+t+4,1);
            if (bank<0x58+p->banks*16 || bank>n || len>n-bank || len<12) return 0;
            SW(bank,2); SW(bank+2,2); SW(bank+8,2); SW(bank+10,2);
            unsigned attrs=s[bank+4]&127, samples=s[bank+5];
            size_t tail=((attrs+2)*samples+15)&~3u;
            if (tail+attrs*4>len) return 0;
            for (unsigned j=0;j<attrs;j++) SW(bank+tail+j*4,4);
            // The 2-byte sample offsets are explicitly BE on BOTH platforms
            // (008349FA), followed by byte selectors. Never word-swap them.
        }
    }
#undef SW
    return 1;
}

typedef struct { unsigned size, samples, rate, codec, revision, channels; } SpeechHeader;
static int ea_header(const uint8_t *p, size_t n, SpeechHeader *h, int nis) {
    memset(h,0,sizeof *h);
    if (n<32 || memcmp(p,"SCHl",4)) return 0;
    h->size=ea_read32(p+4,0);
    if ((h->size!=32 && h->size!=36 && h->size!=40) || h->size>n || memcmp(p+8,"PT",2) ||
        (ea_read16(p+10,0)!=0 && ea_read16(p+10,0)!=9)) return 0;
    unsigned channels=1, format=0, ended=0;
    for (unsigned pos=12;pos<h->size;) {
        unsigned id=p[pos++];
        if (id==0xff) { ended=1; break; }
        if (id>=0xfc) continue;
        if (pos==h->size) return 0;
        unsigned width=p[pos++], v=0;
        if (!width || width>4 || width>h->size-pos) return 0;
        for (unsigned j=0;j<width;j++) v=(v<<8)|p[pos++];
        switch(id) {
            case 5: if (!nis) return 0; break;   // NIS: 0x64 on both platforms
            case 6: format=v; break;
            case 0x80: h->revision=v; break;
            case 0x82: channels=v; break;
            case 0x84: h->rate=v; break;
            case 0x85: h->samples=v; break;
            case 0xa0: h->codec=v; break;
            case 0x8c: if (v!=4) return 0; break;
            default: return 0;
        }
    }
    h->channels=channels;
    if (nis) {
        // A10: codec 10 EA-XA is implicit (no 0xa0). PC PT00 states revision 2 (0x80); X360 PT09 has 0x8c=4 instead.
        unsigned platform=ea_read16(p+10,0);
        return ended && format==101 && (channels==1 || channels==2 || channels==4 || channels==6) &&
            h->samples && h->samples<0x1000000 && (!h->codec || h->codec==10) &&
            ((platform==0 && (h->rate==44100 || h->rate==48000) && h->revision==2) || (platform==9 && h->rate==48000 && h->revision==0));
    }
    return ended && format==101 && (channels==1 || channels==2) && h->samples && h->samples<0x1000000 &&
        ((h->codec==4 && h->rate==24000 && h->revision==2) ||
         (h->codec==22 && h->rate==36000 && (h->revision==0 || h->revision==2)));
}

static int speech_header(const uint8_t *p, size_t n, SpeechHeader *h) { return ea_header(p,n,h,0); }

// NIS: PT09 -> PT00 and the implicit X360 revision attribute (0x8c=4) -> explicit 0x80=2, in place (same length).
static int nis_header_fix(uint8_t *p, unsigned size) {
    p[10]=p[11]=0;
    for (unsigned pos=12;pos<size;) {
        unsigned id=p[pos++];
        if (id==0xff) return 0;
        if (id>=0xfc) continue;
        unsigned width=p[pos++];
        if (id==0x8c && width==1) { p[pos-2]=0x80; p[pos]=2; return 1; }
        pos+=width;
    }
    return 0;
}

// Validate one indexed stream, including total sample/chunk counts. On conversion,
// preserve its exact byte extent: only PT attributes and packet scalars change.
static int ea_stream(uint8_t *p, size_t n, int convert, CopSpeechProof *proof, int nis) {
    SpeechHeader h;
    if (!ea_header(p,n,&h,nis) || (convert && (nis ? ea_read16(p+10,0)!=9 : (h.codec!=22 || ea_read16(p+10,0)!=9)))) return 0;
    unsigned samples=0, blocks=0, declared=0;
    size_t at=h.size;
    if (at+12>n || memcmp(p+at,"SCCl",4) || ea_read32(p+at+4,0)!=12) return 0;
    declared=ea_read32(p+at+8,convert);
    if (convert) ea_put32(p+at+8,declared);
    at+=12;
    while (at+8<=n) {
        unsigned size=ea_read32(p+at+4,0);
        if (size<8 || size>n-at) return 0;
        if (!memcmp(p+at,"SCEl",4)) {
            if (size!=8 || samples!=h.samples || blocks!=declared || !blocks) return 0;
            if (convert && nis) { if (!nis_header_fix(p,h.size)) return 0; }
            else if (convert) {
                // Canonical PC PT00, MicroTalk revision 2. Using a fixed
                // three-byte count fits even the X360's 32-byte short headers.
                static const uint8_t canonical[32]={
                    'S','C','H','l',0,0,0,0,'P','T',0,0,6,1,101,0xfd,
                    0x80,1,2,0x85,3,0,0,0,0x84,2,0x8c,0xa0,0xa0,1,22,0xff};
                memcpy(p,canonical,32); ea_put32(p+4,h.size);
                p[21]=(uint8_t)(h.samples>>16); p[22]=(uint8_t)(h.samples>>8); p[23]=(uint8_t)h.samples;
                if (h.size>32) memset(p+32,0,h.size-32);
                if (h.channels==2) {
                    if (h.size<36) return 0;
                    p[31]=0x82; p[32]=1; p[33]=2; p[34]=0xff;
                }
            }
            if (proof) *proof=(CopSpeechProof){.codec=h.codec?h.codec:10,.rate=h.rate,.samples=samples,.blocks=blocks,.channels=h.channels};
            return 1;
        }
        if (memcmp(p+at,"SCDl",4) || size<18 || size>65536) return 0;
        unsigned count=ea_read32(p+at+8,convert), payload=12+h.channels*4;
        if (!count || count>16384 || count>h.samples-samples || size<payload+h.channels*2) return 0;
        for (unsigned ch=0;ch<h.channels;ch++) {
            unsigned offset=ea_read32(p+at+12+ch*4,convert);
            unsigned end=ch+1<h.channels ? ea_read32(p+at+16+ch*4,convert) : size-payload;
            if ((!ch && offset) || end<=offset+1 || end>size-payload || (nis ? (!blocks && p[at+payload+offset]!=0xee) : p[at+payload+offset]!=(blocks==0))) return 0;
        }
        if (convert) {
            ea_put32(p+at+8,count);
            for (unsigned ch=0;ch<h.channels;ch++) ea_put32(p+at+12+ch*4,ea_read32(p+at+12+ch*4,1));
        }
        samples+=count; blocks++; at+=size;
    }
    return 0;
}

int copspeech_stream(uint8_t *p, size_t n, int convert, CopSpeechProof *proof) { return ea_stream(p,n,convert,proof,0); }
int nisaudio_stream(uint8_t *p, size_t n, int convert, CopSpeechProof *proof) { return ea_stream(p,n,convert,proof,1); }

// Same translated functions as 008269BF, the PC's streaming MicroTalk decoder.
// The revision-2 packet byte is skipped by 008269BF, and 008287AE consumes its
// PCM marker. A fresh 1 MB guest arena keeps input/output/state/stack separate.
// PCM exists only in scratch memory, is checked, and is discarded. No device API.
int copspeech_decode(const uint8_t *p, size_t n, CopSpeechProof *proof) {
    CopSpeechProof result;
    if (!copspeech_stream((uint8_t *)p,n,0,&result)) return 0;
    SpeechHeader h; if (!speech_header(p,n,&h) || h.revision!=2) return 0;
    uint32_t arena=gmalloc_try(0x100000);
    if (!arena) return 0;
    memset(G+arena,0,0x100000);
    uint32_t input=arena+0x10000, output=arena+0x30000, cursor=arena+0x50000;
    Cpu c={0}; c.cw=0x37f;
    for (unsigned ch=0;ch<h.channels;ch++) {
        uint32_t state=arena+ch*0x1000;
        c.ecx=state; c.esp=arena+0xff000; f_00828773(&c);
        M32(state+0xd64)=1; // attr 0x80=2: revision-2 PCM markers
    }
    unsigned blocks=0;
    for (size_t at=h.size+12;at+8<=n;) {
        unsigned size=ea_read32(p+at+4,0);
        if (!memcmp(p+at,"SCEl",4)) break;
        unsigned count=ea_read32(p+at+8,0), payload=12+h.channels*4;
        for (unsigned ch=0;ch<h.channels;ch++) {
            uint32_t state=arena+ch*0x1000;
            unsigned start=ea_read32(p+at+12+ch*4,0);
            unsigned end=ch+1<h.channels ? ea_read32(p+at+16+ch*4,0) : size-payload;
            memcpy(G+input,p+at+payload+start+1,end-start-1); memset(G+input+end-start-1,0,64);
            M32(state+0xd5c)=!blocks; // first packet resets predictor/filter history
            c.ecx=state; c.esp=arena+0xff000;
            M32(c.esp+4)=input; M32(c.esp+8)=count*4; M32(c.esp+12)=count;
            f_008287AE(&c);
            if (c.eax) { gfree(arena); return 0; }
            M32(cursor)=output; c.ecx=state; c.esp=arena+0xff000;
            M32(c.esp+4)=cursor; M32(c.esp+8)=count;
            f_00828976(&c);
            if (c.eax!=count || M32(state+0xd54)!=0) { gfree(arena); return 0; }
            for (unsigned i=0;i<count;i++) {
                float v=MF32(output+i*4);
                if (!isfinite(v)) { gfree(arena); return 0; }
                // Snd's final float->int16 saturation, without an audio output.
                double pcm=fmax(-32768,fmin(32767,v));
                result.energy+=pcm*pcm;
                if (fabs(pcm)>result.peak) result.peak=fabs(pcm);
            }
        }
        blocks++; at+=size;
    }
    gfree(arena);
    if (!result.energy || !isfinite(result.energy)) return 0;
    if (proof) *proof=result;
    return 1;
}

static pthread_once_t once=PTHREAD_ONCE_INIT;
static X360MusicHandle selected[COP_FILES];
static char selected_paths[COP_FILES][4096];
static int ready;
int ea_find_case(const char *root, const char *name, char *out, size_t size) {
    char current[4096], relative[512];
    snprintf(current,sizeof current,"%s",root); snprintf(relative,sizeof relative,"%s",name);
    char *save;
    for (char *part=strtok_r(relative,"/",&save);part;part=strtok_r(NULL,"/",&save)) {
        DIR *dir=opendir(current); struct dirent *ent; char next[4096]="";
        while (dir && (ent=readdir(dir))) if (!strcasecmp(ent->d_name,part)) {
            snprintf(next,sizeof next,"%s/%s",current,ent->d_name); break;
        }
        if (dir) closedir(dir);
        if (!next[0]) return 0;
        snprintf(current,sizeof current,"%s",next);
    }
    snprintf(out,size,"%s",current); return 1;
}
uint8_t *ea_map_file(const char *path, size_t expected, int writable) {
    int fd=open(path,O_RDONLY); struct stat st;
    if (fd<0) return NULL;
    void *data=MAP_FAILED;
    if (!fstat(fd,&st) && S_ISREG(st.st_mode) && st.st_size==(off_t)expected)
        data=mmap(NULL,expected,PROT_READ|(writable?PROT_WRITE:0),MAP_PRIVATE,fd,0);
    close(fd); return data==MAP_FAILED ? NULL : data;
}
static int fingerprint(const uint8_t *p, size_t n, uint32_t crc) { return p && crc32(0,p,(uInt)n)==crc; }

// Keep the retail BIG on disk. Only changed four-byte words are resident;
// arbitrary/partial reads receive the same bytes as the old private conversion.
static CopBig big={.fd=-1};
int ea_read_exact(int fd, void *dst, size_t n, size_t offset) {
    uint8_t *p=dst;
    while (n) {
        ssize_t got=pread(fd,p,n,(off_t)offset);
        if (got<0 && errno==EINTR) continue;
        if (got<=0) return 0;
        p+=got; offset+=(size_t)got; n-=(size_t)got;
    }
    return 1;
}
// Streaming CRC avoids faulting hundreds of MB into a mapping just to validate it.
int ea_checked_file(const char *path, size_t size, uint32_t expected) {
    int fd=open(path,O_RDONLY); struct stat st; uint8_t bytes[65536];
    if (fd<0) return -1;
    if (fstat(fd,&st) || !S_ISREG(st.st_mode) || st.st_size!=(off_t)size) goto fail;
    uint32_t crc=0;
    for (size_t at=0;at<size;) {
        size_t n=size-at; if (n>sizeof bytes) n=sizeof bytes;
        if (!ea_read_exact(fd,bytes,n,at)) goto fail;
        crc=crc32(crc,bytes,(uInt)n); at+=n;
    }
    if (crc==expected) return fd;
fail:
    close(fd); return -1;
}
static int word_order(const void *a, const void *b) {
    uint32_t x=((const CopWord *)a)->offset, y=((const CopWord *)b)->offset;
    return (x>y)-(x<y);
}
int ea_sort_words(CopBig *b) {
    qsort(b->words,b->count,sizeof *b->words,word_order);
    for (size_t i=1;i<b->count;i++) if (b->words[i-1].offset+4>b->words[i].offset) return 0;
    return 1;
}
int ea_collect_words(CopBig *b, size_t offset, const uint8_t *raw, const uint8_t *converted, size_t n) {
    if ((offset&3) || (n&3) || offset>UINT32_MAX || n>UINT32_MAX-offset) return 0;
    for (size_t at=0;at<n;at+=4) if (memcmp(raw+at,converted+at,4)) {
        if (b->count==b->capacity) {
            size_t capacity=b->capacity ? b->capacity*2 : 4096;
            CopWord *words=realloc(b->words,capacity*sizeof *words);
            if (!words) return 0;
            b->words=words; b->capacity=capacity;
        }
        CopWord *word=&b->words[b->count++];
        word->offset=(uint32_t)(offset+at); memcpy(word->bytes,converted+at,4);
    }
    return 1;
}
size_t ea_big_read_at(void *source, void *dst, size_t n, size_t pos) {
    const CopBig *b=source;
    if (!n || !ea_read_exact(b->fd,dst,n,pos)) return 0;
    size_t lo=0, hi=b->count;
    while (lo<hi) {
        size_t mid=lo+(hi-lo)/2;
        if ((size_t)b->words[mid].offset+4<=pos) lo=mid+1; else hi=mid;
    }
    for (size_t i=lo;i<b->count && b->words[i].offset<pos+n;i++) {
        const CopWord *word=&b->words[i];
        size_t start=word->offset>pos ? word->offset : pos;
        size_t end=(size_t)word->offset+4; if (end>pos+n) end=pos+n;
        memcpy((uint8_t *)dst+start-pos,word->bytes+start-word->offset,end-start);
    }
    return n;
}
int ea_indexed_extent(const uint8_t *idx, unsigned bank, unsigned sample, size_t *start, size_t *end, size_t limit) {
    size_t t=0x58+bank*16, b=ea_read32(idx+t+8,0), base=ea_read32(idx+t+12,0);
    unsigned count=idx[b+5], stride=(idx[b+4]&127)+2, unit=(idx[b+7]+1)*256;
    *start=base+ea_read16(idx+b+12+sample*stride,1)*unit;
    *end=base+(sample+1<count ? ea_read16(idx+b+12+(sample+1)*stride,1) : ea_read16(idx+b+8,0))*unit;
    return *end>*start && *end<=limit && !(*start&3) && !(*end&3);
}
static int indexed_extent(const uint8_t *idx, unsigned bank, unsigned sample, size_t *start, size_t *end) {
    return ea_indexed_extent(idx,bank,sample,start,end,456232704);
}
static int build_big(CopBig *b, const uint8_t *idx, char *why, size_t why_size) {
    size_t maximum=0; unsigned streams=0;
    for (unsigned i=0;i<579;i++) {
        size_t bank=ea_read32(idx+0x58+i*16+8,0);
        for (unsigned j=0;j<idx[bank+5];j++) {
            size_t start,end;
            if (!indexed_extent(idx,i,j,&start,&end)) return 0;
            if (end-start>maximum) maximum=end-start;
            streams++;
        }
    }
    if (streams!=13562) return 0;
    uint8_t *raw=malloc(maximum), *converted=malloc(maximum);
    int ok=0; streams=0;
    if (!raw || !converted) goto done;
    for (unsigned i=0;i<579;i++) {
        size_t bank=ea_read32(idx+0x58+i*16+8,0);
        for (unsigned j=0;j<idx[bank+5];j++) {
            size_t start,end;
            if (!indexed_extent(idx,i,j,&start,&end) || !ea_read_exact(b->fd,raw,end-start,start)) goto done;
            memcpy(converted,raw,end-start);
            if (!copspeech_stream(converted,end-start,1,NULL) || !ea_collect_words(b,start,raw,converted,end-start)) {
                snprintf(why,why_size,"X360 indexed stream %u/%u conversion/allocation failed",i,j); goto done;
            }
            if (!streams && start) goto done;
            streams++;
        }
    }
    if (!ea_sort_words(b)) goto done;
    ok=1;
done:
    free(raw); free(converted); return ok;
}
static void cop_init(void) {                                                                     
    if (!setting.boot_value && !diag_on) return; // Default PC has no preflight cost outside BOOT_DIAG.
    static const size_t sizes[COP_FILES]={456232704,5136,15547,76976};
    static const uint32_t crc[COP_FILES]={0x1ab21210,0xa8888398,0x2a5d2983,0x7d298c2d};
    uint8_t *source[COP_FILES]={0};
    char path[4096], rel[128], root[4096], why[256]="PC selected";
    struct timespec begin, end; clock_gettime(CLOCK_MONOTONIC,&begin);
    int baseline=0;
    if (game_dir && ea_find_case(game_dir,"SOUND/SPEECH/copspeech.big",path,sizeof path)) {
        int fd=ea_checked_file(path,192753408,0xa624cda3);
        if (fd>=0) { baseline=1; close(fd); }
    }
    if (!setting.boot_value) goto done;
    snprintf(why,sizeof why,"English PC baseline failed size/checksum");
    if (!baseline) goto done;
    const char *extract=getenv("NFS_X360_FILES");
    // The speech-only ISO extraction lives beside the game and full X360 extract.
    if (extract && *extract) snprintf(root,sizeof root,"%s",extract);
    else if (roots_active()) snprintf(root,sizeof root,"%s",roots_get("x360_root") ? roots_get("x360_root") : ROOTS_NONE);
    else snprintf(root,sizeof root,"%s/../assets_audio/x360_copspeech",game_dir ? game_dir : ".");
    for (unsigned i=0;i<COP_FILES;i++) {
        snprintf(rel,sizeof rel,"SOUND/SPEECH/COPSPEECH_EN.%s",extensions[i]);
        if (!ea_find_case(root,rel,path,sizeof path)) {
            snprintf(why,sizeof why,"missing X360 %s",extensions[i]); goto done;
        }
        snprintf(selected_paths[i],sizeof selected_paths[i],"%s",path);
        if (i==COP_BIG) {
            big.fd=ea_checked_file(path,sizes[i],crc[i]);
            if (big.fd<0) { snprintf(why,sizeof why,"X360 big size/checksum failed"); goto done; }
            continue;
        }
        source[i]=ea_map_file(path,sizes[i],0);
        if (!fingerprint(source[i],sizes[i],crc[i])) {
            snprintf(why,sizeof why,"X360 %s size/checksum failed",extensions[i]); goto done;
        }
        if (i!=COP_BIG) {
            uint8_t *converted=malloc(sizes[i]);
            if (!converted || !copspeech_sidecar(i,source[i],sizes[i],converted)) {
                free(converted); snprintf(why,sizeof why,"X360 %s conversion failed",extensions[i]); goto done;
            }
            selected[i]=(X360MusicHandle){.data=converted,.size=sizes[i]};
        }
    }
    snprintf(why,sizeof why,"X360 indexed stream conversion/allocation failed");
    // Proof-only PCM decoding runs translated code in guest memory. Keep it in
    // x360copspeech_test.py: size/CRC-pinned inputs and every stream chain are
    // checked here, with exactly the same sparse conversion bytes as before.
    if (!build_big(&big,selected[COP_IDX].data,why,sizeof why)) goto done;
    selected[COP_BIG]=(X360MusicHandle){.size=sizes[COP_BIG],.read_at=ea_big_read_at,.source=&big};
    ready=1;
    snprintf(why,sizeof why,"13562 streams validated; BIG file + %zu sparse words (%zu bytes capacity); CSI/EVT/IDX in memory",
        big.count,big.capacity*sizeof *big.words);
done:
    for (unsigned i=0;i<COP_FILES;i++) {
        if (source[i]) munmap(source[i],sizes[i]);
        if (!ready) { free((void *)selected[i].data); memset(&selected[i],0,sizeof selected[i]); }
    }
    if (!ready) {
        if (big.fd>=0) close(big.fd);
        free(big.words); big=(CopBig){.fd=-1};
    }
    if (diag_on) {
        clock_gettime(CLOCK_MONOTONIC,&end);
        double ms=(end.tv_sec-begin.tv_sec)*1000.0+(end.tv_nsec-begin.tv_nsec)/1e6;
        diag_event("x360copspeech: option=%s files=%s codec=0x%02x rate=%u first_stream_decoded=n/a (offline test) ms=%.0f; %s",
            setting.boot_value?"x360":"pc",ready?"X360 BIG + converted CSI/EVT/IDX":"PC BIG/CSI/EVT/IDX",
            ready?22:4,ready?36000:24000,ms,why);
        if (ready) for (unsigned i=0;i<COP_FILES;i++) diag_event("x360copspeech: %s -> %s",extensions[i],selected_paths[i]);
    }
}
static void *cop_warm(void *unused) {
    (void)unused;
    pthread_once(&once,cop_init);
    return NULL;
}
void x360copspeech_warm(void) { // after settings/diagnostics init, before guest entry
    if (!setting.boot_value) return;
    pthread_t thread;
    if (!pthread_create(&thread,NULL,cop_warm,NULL)) pthread_detach(thread);
    // Thread creation failure is harmless: the first open still initializes.
}
int x360copspeech_open(const char *win, X360MusicHandle *out) {
    // Only the English PC bank. Other languages and every unrelated file retain PC.
    char normalized[512]; size_t len=strlen(win);
    if (len>=sizeof normalized) return 0;
    for (size_t i=0;i<=len;i++) normalized[i]=win[i]=='\\'?'/':win[i];
    const char *name=normalized;
    if (!strncasecmp(name,"C:/NFSMW/",9)) name+=9;
    else if (!strncasecmp(name,"./",2)) name+=2;
    for (unsigned i=0;i<COP_FILES;i++) {
        char expected[64]; snprintf(expected,sizeof expected,"SOUND/SPEECH/copspeech.%s",extensions[i]);
        if (strcasecmp(name,expected)) continue;
        gil_release();
        pthread_once(&once,cop_init);
        gil_acquire();
        if (!ready) return 0;
        *out=selected[i]; return 1;
    }
    return 0;
}
