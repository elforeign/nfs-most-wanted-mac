"""Device-free Windows guest DirectSound proof, discovered by offline_tests.sh.

Original dispatcher: DirectSoundCreate unsupported, exit 2, handled 0/1 ->
registered COM slice: handled 1/1, exit 0. Wrong wrap pointer: 1 mismatch,
exit 1 -> 0 mismatches; wrong channel order: 2 mismatches in the first frame,
exit 1 -> 0 mismatches. Mac/portable manual mixer sample bytes must match.
The fake WinMM endpoint validates the actual native PCM boundary without devices.
COFF freestanding/hosted: 7/7 objects, 0 unknown; removing the endpoint exposes
1 unknown and fails both audits. C++ SDK-free models do not prove the SDK ABI.
Native queued-frame metadata: broken 0/0 frames/bytes, exit 1 -> 3/72, exit 0.
"""
from pathlib import Path
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
WIN = ROOT / 'runtime/win'

FIXTURE = r'''#include "fake_win.inc"
#include "dsound.h"
#include "startup.h"
#include <stdarg.h>
#include <math.h>
const struct ImportSlot speed_imports[]={
    {0x401000,"DSOUND.dll!DirectSoundCreate"}, {0x401004,"DSOUND.dll!#1"},
    {0x401008,"DSOUND.dll!DirectSoundCreate8"}, {0x40100c,"KERNEL32.dll!CreateEventA"},
    {0x401010,"KERNEL32.dll!WaitForSingleObject"}, {0x401014,"KERNEL32.dll!CloseHandle"}
};
const unsigned speed_import_count=6,guest_func_count=0;
const uint32_t speed_entry=0,guest_func_addr[]={0};
void (*const guest_func_ptr[])(Cpu *)={0};
static unsigned calls, sample_checks, opens, closes, native_writes;
static int refuse_open, refuse_write, refuse_native_write;
static int defer_native;
static WAVEHDR *pending_native[16];
static unsigned pending_count;
int audio_media_probe(void);
static float samples[8192*6];
static size_t sample_frames;
static FILE *capture_file;
static void fail(unsigned line,const char *expr) { printf("failed line %u: %s\n",line,expr);exit(1); }
#define REQUIRE(x) do { if (!(x)) fail(__LINE__,#x); } while (0)
static uint32_t invoke(Cpu *c,int method,uint32_t target,unsigned count,va_list args) {
    uint32_t old=c->esp; c->esp-=128; uint32_t stack=c->esp;
    M32(stack)=0xdeadbeef;
    for (unsigned i=0;i<count;++i) M32(stack+4+4*i)=va_arg(args,uint32_t);
    c->ebx=0x13579bdf;c->esi=0x2468ace0;c->edi=0x98765432;c->ebp=0x12345678;
    if (method) REQUIRE(win_host_dispatch(c,target)); else host_import(c,target);
    REQUIRE(c->esp==stack+4+4*count && c->ebx==0x13579bdf && c->esi==0x2468ace0 &&
            c->edi==0x98765432 && c->ebp==0x12345678);
    ++calls;uint32_t result=c->eax;c->esp=old;return result;
}
static uint32_t imp(Cpu *c,unsigned index,unsigned count,...) {
    va_list args;va_start(args,count);uint32_t result=invoke(c,0,speed_imports[index].slot,count,args);
    va_end(args);return result;
}
static uint32_t com(Cpu *c,uint32_t object,unsigned method,unsigned count,...) {
    va_list args;va_start(args,count);uint32_t result=invoke(c,1,M32(M32(object)+4*method),count,args);
    va_end(args);return result;
}
static int sink_open(void *u,unsigned rate,unsigned channels,uint32_t mask) {
    (void)u;REQUIRE(rate==48000 && channels==6 && mask==0x3f);++opens;return !refuse_open;
}
static int sink_write(void *u,const float *pcm,size_t frames) {
    (void)u;if (refuse_write) return 0;
    REQUIRE(sample_frames+frames<=8192);
    memcpy(samples+sample_frames*6,pcm,frames*24);sample_frames+=frames;
    if (capture_file) REQUIRE(fwrite(pcm,24,frames,capture_file)==frames);
    return 1;
}
static size_t sink_queued(void *u) { (void)u;return 0; }
static void sink_close(void *u) { (void)u;++closes; }
static const WinPcmEndpoint sink={NULL,sink_open,sink_write,sink_queued,sink_close};
// WinMM models the real WAVEHDR lifecycle and inspects the exact 40 format bytes.
unsigned waveOutOpen(HWAVEOUT *out,unsigned device,const WAVEFORMATEX *format,uintptr_t cb,uintptr_t user,DWORD flags) {
    const uint8_t *f=(const uint8_t *)format;
    REQUIRE(device==0xffffffffu && !cb && !user && !flags);
    REQUIRE(f[0]==0xfe && f[1]==0xff && f[2]==6 && f[12]==24 && f[14]==32 && f[16]==22);
    uint32_t rate,mask,sub;memcpy(&rate,f+4,4);memcpy(&mask,f+20,4);memcpy(&sub,f+24,4);
    REQUIRE(rate==48000 && mask==0x3f && sub==3 && f[30]==0x10 && f[32]==0x80 && f[39]==0x71);
    ++opens;if (refuse_open) return 1;*out=(void *)(uintptr_t)0x1234;return 0;
}
unsigned waveOutPrepareHeader(HWAVEOUT out,WAVEHDR *h,unsigned size) {
    REQUIRE(out && size==sizeof *h && h->lpData && !(h->dwFlags&2));h->dwFlags|=2;return 0;
}
unsigned waveOutWrite(HWAVEOUT out,WAVEHDR *h,unsigned size) {
    REQUIRE(out && size==sizeof *h && (h->dwFlags&2));++native_writes;
    if (refuse_native_write) return 1;
    h->dwFlags|=16;
    REQUIRE(sink_write(NULL,(const float *)h->lpData,h->dwBufferLength/24));
    if (defer_native) { REQUIRE(pending_count<16);pending_native[pending_count++]=h; }
    else h->dwFlags=(h->dwFlags|1)&~16u;
    return 0;
}
unsigned waveOutUnprepareHeader(HWAVEOUT out,WAVEHDR *h,unsigned size) {
    REQUIRE(out && size==sizeof *h && !(h->dwFlags&16));h->dwFlags=0;return 0;
}
unsigned waveOutReset(HWAVEOUT out) {
    REQUIRE(out);for (unsigned i=0;i<pending_count;++i) pending_native[i]->dwFlags=(pending_native[i]->dwFlags|1)&~16u;
    pending_count=0;return 0;
}
unsigned waveOutClose(HWAVEOUT out) { REQUIRE(out);++closes;return 0; }
static uint32_t event(Cpu *c) {
    uint32_t h=imp(c,3,4,0u,0u,0u,0u);REQUIRE(h);return h;
}
static uint32_t wait_event(Cpu *c,uint32_t h,uint32_t ms) { return imp(c,4,2,h,ms); }
static void wfx(unsigned channels) {
    memset(G+0x18000,0,40);M16(0x18000)=1;M16(0x18002)=(uint16_t)channels;
    M32(0x18004)=48000;M32(0x18008)=48000*channels*2;M16(0x1800c)=(uint16_t)(channels*2);M16(0x1800e)=16;
}
static uint32_t make_buffer(Cpu *c,uint32_t d,uint32_t flags,uint32_t bytes) {
    memset(G+0x18100,0,36);M32(0x18100)=36;M32(0x18104)=flags;M32(0x18108)=bytes;
    M32(0x18110)=(flags&DSBCAPS_PRIMARYBUFFER)?0:0x18000;
    REQUIRE(com(c,d,3,4,d,0x18100u,0x18200u,0u)==0);return M32(0x18200);
}
static uint32_t device(Cpu *c,unsigned which) {
    REQUIRE(imp(c,which,3,0u,0x18200u,0u)==0);REQUIRE(M32(0x18200));return M32(0x18200);
}
static void lock(Cpu *c,uint32_t b,uint32_t off,uint32_t bytes,uint32_t flags) {
    REQUIRE(com(c,b,11,8,b,off,bytes,0x18200u,0x18204u,0x18208u,0x1820cu,flags)==0);
}
static void unlock(Cpu *c,uint32_t b) {
    REQUIRE(com(c,b,19,5,b,M32(0x18200),M32(0x18204),M32(0x18208),M32(0x1820c))==0);
}
static void expect(float value,float expected) {
    ++sample_checks;
    if (fabsf(value-expected)>0.000001f) { printf("sample mismatch %u: %.9g != %.9g\n",sample_checks,value,expected);exit(1); }
}
static void basic(Cpu *c) {
    uint32_t d=device(c,0);
    REQUIRE(com(c,d,6,3,d,0u,(uint32_t)DSSCL_PRIORITY)==0);
    uint32_t primary=make_buffer(c,d,DSBCAPS_PRIMARYBUFFER,0);
    wfx(6);REQUIRE(com(c,primary,14,2,primary,0x18000u)==0);
    REQUIRE(com(c,primary,5,4,primary,0x18300u,40u,0x18340u)==0);
    REQUIRE(M16(0x18302)==6 && M32(0x18304)==48000 && M32(0x18340)==18);
    uint32_t b=make_buffer(c,d,DSBCAPS_CTRLVOLUME|DSBCAPS_CTRLFREQUENCY|DSBCAPS_CTRLPOSITIONNOTIFY,96);
    REQUIRE(com(c,b,14,2,b,0x18000u)==(uint32_t)DSERR_INVALIDCALL);
    const uint8_t ds8[16]={0x93,0x7e,0x0a,0xc5,0x95,0xf3,0x34,0x48,0x9e,0xf6,0x7f,0xa9,0x9d,0xe5,0x09,0x66};
    const uint8_t buf8[16]={0x49,0xa4,0x25,0x68,0x24,0x75,0x82,0x4d,0x92,0x0f,0x50,0xe3,0x6a,0xb3,0xab,0x1e};
    memcpy(G+0x18400,ds8,16);REQUIRE(com(c,d,0,3,d,0x18400u,0x18420u)==0 && M32(0x18420)==d);
    REQUIRE(com(c,d,2,1,d)==3);
    memcpy(G+0x18400,buf8,16);REQUIRE(com(c,b,0,3,b,0x18400u,0x18420u)==0 && M32(0x18420)==b);
    REQUIRE(com(c,b,2,1,b)==1);
    // Query a full IDirectSoundNotify GUID, including its tail.
    const uint8_t notify[16]={0x83,0x07,0x21,0xb0,0xcd,0x89,0xd0,0x11,0xaf,0x08,0,0xa0,0xc9,0x25,0xcd,0x16};
    memcpy(G+0x18400,notify,16);
    REQUIRE(com(c,b,0,3,b,0x18400u,0x18420u)==0);uint32_t n=M32(0x18420);
    const uint8_t unknown[16]={0,0,0,0,0,0,0,0,0xc0,0,0,0,0,0,0,0x46};
    memcpy(G+0x18440,unknown,16);
    REQUIRE(com(c,n,0,3,n,0x18440u,0x18420u)==0 && M32(0x18420)==b);
    REQUIRE(com(c,b,2,1,b)==2);
    REQUIRE(com(c,b,0,3,b,0x18400u,0x18420u)==0 && M32(0x18420)==n);
    REQUIRE(com(c,n,2,1,n)==1);
    M8(0x1840f)^=1;
    REQUIRE(com(c,b,0,3,b,0x18400u,0x18420u)==(uint32_t)DSERR_NOINTERFACE && !M32(0x18420));
    uint32_t a=event(c),wrap=event(c),stopped=event(c);
    M32(0x18500)=84;M32(0x18504)=a;M32(0x18508)=12;M32(0x1850c)=wrap;
    M32(0x18510)=0xffffffffu;M32(0x18514)=stopped;
    REQUIRE(com(c,n,3,3,n,3u,0x18500u)==0);
    lock(c,b,72,48,0);
    uint32_t p1=M32(0x18200),p2=M32(0x18208);
    unsigned wrap_mismatches=(M32(0x18204)!=24)+(M32(0x1820c)!=24)+(p2!=p1-72);
    printf("wrap mismatches=%u\n",wrap_mismatches);REQUIRE(!wrap_mismatches);
    for (unsigned frame=0;frame<4;++frame) for (unsigned ch=0;ch<6;++ch)
        M16((frame<2?p1:p2)+(frame%2)*12+ch*2)=(uint16_t)(1000*(ch+1)+100*frame);
    REQUIRE(com(c,b,19,5,b,p1,24u,p2+12,24u)==(uint32_t)DSERR_INVALIDPARAM);
    unlock(c,b);
    REQUIRE(com(c,b,13,2,b,72u)==0);
    REQUIRE(wait_event(c,a,0)==WAIT_TIMEOUT && wait_event(c,wrap,0)==WAIT_TIMEOUT);
    REQUIRE(com(c,b,12,4,b,0u,0u,(uint32_t)DSBPLAY_LOOPING)==0);
    REQUIRE(com(c,n,3,3,n,0u,0u)==(uint32_t)DSERR_INVALIDCALL);
    sample_frames=0;REQUIRE(win_dsound_render(d,4)==0 && sample_frames==4);
    unsigned channel_mismatches=0;
    for (unsigned ch=0;ch<6;++ch) channel_mismatches+=fabsf(samples[ch]-(1000*(ch+1)/32768.0f))>0.000001f;
    printf("channel mismatches=%u\n",channel_mismatches);REQUIRE(!channel_mismatches);
    for (unsigned frame=0;frame<4;++frame) for (unsigned ch=0;ch<6;++ch)
        expect(samples[frame*6+ch],(1000*(ch+1)+100*frame)/32768.0f);
    REQUIRE(wait_event(c,a,0)==WAIT_OBJECT_0 && wait_event(c,wrap,0)==WAIT_OBJECT_0);
    REQUIRE(wait_event(c,a,0)==WAIT_TIMEOUT && wait_event(c,stopped,0)==WAIT_TIMEOUT);
    REQUIRE(com(c,b,4,3,b,0x18600u,0x18604u)==0);
    REQUIRE(M32(0x18600)==24 && M32(0x18604)%12==0);
    REQUIRE(com(c,b,18,1,b)==0 && wait_event(c,stopped,0)==WAIT_OBJECT_0);
    REQUIRE(com(c,b,4,3,b,0x18600u,0x18604u)==0);uint32_t cursor=M32(0x18600);
    sample_frames=0;REQUIRE(win_dsound_render(d,2)==0);
    for (unsigned i=0;i<12;++i) expect(samples[i],0);
    REQUIRE(com(c,b,4,3,b,0x18600u,0x18604u)==0 && M32(0x18600)==cursor);
    REQUIRE(com(c,b,9,2,b,0x18600u)==0 && !(M32(0x18600)&DSBSTATUS_PLAYING));
    REQUIRE(com(c,b,15,2,b,(uint32_t)-600)==0);
    REQUIRE(com(c,b,6,2,b,0x18600u)==0 && (int32_t)M32(0x18600)==-600);
    REQUIRE(com(c,b,16,2,b,1u)==(uint32_t)DSERR_CONTROLUNAVAIL);
    REQUIRE(com(c,b,17,2,b,24000u)==0 && com(c,b,8,2,b,0x18600u)==0 && M32(0x18600)==24000);
    REQUIRE(com(c,b,13,2,b,72u)==0 && com(c,b,12,4,b,0u,0u,(uint32_t)DSBPLAY_LOOPING)==0);
    sample_frames=0;REQUIRE(win_dsound_render(d,4)==0);
    for (unsigned ch=0;ch<6;++ch) {
        float a=1000*(ch+1), scale=powf(10,-600/2000.0f)/32768.0f;
        // Catmull-Rom at half a source frame: neighbor frames 0,A,A+100,A+200.
        float half=a+0.25f*((a+100)+0.5f*(-5*a+4*(a+100)-(a+200)+0.5f*(3*(a-(a+100))+(a+200))));
        expect(samples[ch],a*scale);expect(samples[6+ch],half*scale);
        expect(samples[12+ch],(a+100)*scale);expect(samples[18+ch],(a+150)*scale);
    }
    REQUIRE(com(c,b,18,1,b)==0);
    REQUIRE(com(c,b,17,2,b,0u)==0);
    REQUIRE(com(c,b,11,8,b,96u,12u,0x18200u,0x18204u,0x18208u,0x1820cu,0u)==(uint32_t)DSERR_INVALIDPARAM);
    REQUIRE(com(c,b,11,8,b,0u,12u,0xfffffffcu,0x18204u,0x18208u,0x1820cu,0u)==(uint32_t)DSERR_INVALIDPARAM);
    REQUIRE(com(c,b,4,3,b,0x50000u,0u)==(uint32_t)DSERR_INVALIDPARAM);
    // Duplicate storage survives releasing the source, and notification refs pin buffers.
    REQUIRE(com(c,d,5,3,d,b,0x18600u)==0);uint32_t dup=M32(0x18600);
    REQUIRE(com(c,b,2,1,b)==2); // notification + duplicate own it
    REQUIRE(com(c,n,2,1,n)==0);
    REQUIRE(com(c,dup,13,2,dup,72u)==0);
    REQUIRE(com(c,dup,12,4,dup,0u,0u,0u)==0);
    sample_frames=0;REQUIRE(win_dsound_render(d,1)==0);
    for (unsigned ch=0;ch<6;++ch) expect(samples[ch],1000*(ch+1)/32768.0f*powf(10,-600/2000.0f));
    REQUIRE(com(c,dup,18,1,dup)==0 && com(c,dup,2,1,dup)==0);
    REQUIRE(com(c,b,2,1,b)==0); // stale this is rejected safely
    REQUIRE(com(c,primary,2,1,primary)==0);
    REQUIRE(com(c,d,2,1,d)==0);
    REQUIRE(imp(c,5,1,a)==1 && imp(c,5,1,wrap)==1 && imp(c,5,1,stopped)==1);
}
static void stereo(Cpu *c) {
    uint32_t d=device(c,1);wfx(2);
    uint32_t b=make_buffer(c,d,DSBCAPS_CTRLVOLUME|DSBCAPS_CTRLPAN,16);
    lock(c,b,0,16,DSBLOCK_ENTIREBUFFER);
    uint32_t p=M32(0x18200);
    for (unsigned i=0;i<4;++i) { M16(p+4*i)=8192;M16(p+4*i+2)=16384; }
    unlock(c,b);
    REQUIRE(com(c,b,16,2,b,(uint32_t)-10000)==0);
    REQUIRE(com(c,b,7,2,b,0x18600u)==0 && (int32_t)M32(0x18600)==-10000);
    REQUIRE(com(c,b,15,2,b,(uint32_t)-600)==0);
    REQUIRE(com(c,b,12,4,b,0u,0u,(uint32_t)DSBPLAY_LOOPING)==0);
    sample_frames=0;REQUIRE(win_dsound_render(d,2)==0);
    expect(samples[0],0.25f*powf(10,-600/2000.0f));expect(samples[1],0);
    for (unsigned ch=2;ch<6;++ch) expect(samples[ch],0);
    REQUIRE(com(c,b,18,1,b)==0);
    REQUIRE(com(c,b,16,2,b,10001u)==(uint32_t)DSERR_INVALIDPARAM);
    REQUIRE(com(c,b,15,2,b,1u)==(uint32_t)DSERR_INVALIDPARAM);
    REQUIRE(com(c,d,2,1,d)==1 && com(c,b,2,1,b)==0);
}
static void native(Cpu *c) {
    win_dsound_set_endpoint(NULL,1);
    uint32_t d=device(c,2);wfx(6);uint32_t b=make_buffer(c,d,0,96);
    lock(c,b,0,96,DSBLOCK_ENTIREBUFFER);uint32_t p=M32(0x18200);
    for (unsigned i=0;i<48;++i) M16(p+2*i)=(uint16_t)(300*(i%6+1));
    unlock(c,b);REQUIRE(com(c,b,12,4,b,0u,0u,(uint32_t)DSBPLAY_LOOPING)==0);
    sample_frames=0;REQUIRE(win_dsound_render(d,4)==0 && native_writes==1);
    for (unsigned ch=0;ch<6;++ch) expect(samples[ch],300*(ch+1)/32768.0f);
    REQUIRE(com(c,b,18,1,b)==0 && com(c,b,2,1,b)==0 && com(c,d,2,1,d)==0);
}
static void background(Cpu *c) {
    // The output thread must signal an actual registered guest event while the
    // guest waiter has released the GIL, then join safely during final Release.
    win_dsound_set_endpoint(&sink,0);sample_frames=0;
    uint32_t d=device(c,0);wfx(6);
    uint32_t b=make_buffer(c,d,DSBCAPS_CTRLPOSITIONNOTIFY,5760);
    const uint8_t notify[16]={0x83,0x07,0x21,0xb0,0xcd,0x89,0xd0,0x11,0xaf,0x08,0,0xa0,0xc9,0x25,0xcd,0x16};
    memcpy(G+0x18400,notify,16);REQUIRE(com(c,b,0,3,b,0x18400u,0x18420u)==0);
    uint32_t n=M32(0x18420),e=event(c),stopped=event(c);
    M32(0x18500)=2880;M32(0x18504)=e;M32(0x18508)=0xffffffffu;M32(0x1850c)=stopped;
    REQUIRE(com(c,n,3,3,n,2u,0x18500u)==0);
    REQUIRE(com(c,b,12,4,b,0u,0u,(uint32_t)DSBPLAY_LOOPING)==0);
    REQUIRE(wait_event(c,e,2000)==WAIT_OBJECT_0);
    REQUIRE(com(c,b,18,1,b)==0 && wait_event(c,stopped,0)==WAIT_OBJECT_0);
    REQUIRE(com(c,b,2,1,b)==1 && com(c,d,2,1,d)==1 && com(c,n,2,1,n)==0);
    REQUIRE(imp(c,5,1,e)==1 && imp(c,5,1,stopped)==1);
    REQUIRE(sample_frames>=256);
    printf("background notification + GIL-safe join: pass\n");
}
int main(int argc,char **argv) {
    REQUIRE(win_vm_init() && win_vm_commit(0x10000,0x20000));
    Cpu c;win_cpu_init(&c,0x10000,0x20000,0x30000,1);win_cpu=&c;
    threads_init();REQUIRE(win_startup_init("synthetic.exe"));win_first_import=0;
    if (argc>1 && !strcmp(argv[1],"original")) { (void)device(&c,0);return 1; }
    REQUIRE(win_dsound_register());
    if (argc>1 && !strcmp(argv[1],"first")) { win_first_import=1;(void)device(&c,0);return 1; }
    win_dsound_set_endpoint(&sink,1);ds_set_peak_limiter(0);
    if (argc>1) { capture_file=fopen(argv[1],"wb");REQUIRE(capture_file); }
    basic(&c);stereo(&c);native(&c);
    if (capture_file) { fclose(capture_file);capture_file=NULL; }
    background(&c);
    defer_native=1;sample_frames=0;int queue_probe=audio_media_probe();
    printf("native queue probe=%d\n",queue_probe);REQUIRE(queue_probe);defer_native=0;
    REQUIRE(!pending_count);
    win_dsound_set_endpoint(NULL,1);uint32_t native_failed=device(&c,0);
    refuse_native_write=1;REQUIRE(win_dsound_render(native_failed,1)==DSERR_NODRIVER);refuse_native_write=0;
    REQUIRE(com(&c,native_failed,2,1,native_failed)==0);
    win_dsound_set_endpoint(&sink,1);
    refuse_open=1;REQUIRE(imp(&c,0,3,0u,0x18200u,0u)==(uint32_t)DSERR_NODRIVER && !M32(0x18200));refuse_open=0;
    uint32_t d=device(&c,0);refuse_write=1;REQUIRE(win_dsound_render(d,1)==DSERR_NODRIVER);
    refuse_write=0;REQUIRE(win_dsound_render(d,1)==DSERR_NODRIVER && com(&c,d,2,1,d)==0);
    REQUIRE(imp(&c,0,3,0u,0x18200u,1u)==(uint32_t)DSERR_NOAGGREGATION);
    REQUIRE(imp(&c,0,3,0u,0x50000u,0u)==(uint32_t)DSERR_INVALIDPARAM);
    REQUIRE(!fake_event_live && opens==closes);
    gil_release();win_cpu=NULL;win_vm_destroy();
    printf("AUDIO FIXTURE PASS: handled=1/1 calls=%u samples=%u notifications=5 opens=%u closes=%u\n",calls,sample_checks,opens,closes);
    return 0;
}
'''

MEDIA_PROBE = r'''#include "media.h"
extern "C" int audio_media_probe(void) {
    media::AudioOutput output;
    if (!output.open(media::OutputMode::Pcm51,48000)) return 0;
    float pcm[18]{};
    output.write(pcm,3);
    auto stats=output.take_callback_stats();
    if (output.queued_frames()!=3 || stats.queued_frames!=3 || stats.queue_bytes!=72) return 0;
    output.close();
    return output.queued_frames()==0;
}
'''


def run(argv, code=0):
    result = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=120,
                            env={**os.environ, 'NFS_AUDIO': 'pcm51', 'NFS_AUDIO_LAUNCH_REOPEN': '0'})
    assert result.returncode == code, (f'{Path(argv[0]).name} exit {result.returncode}, expected {code}: '
                                       f'{result.stdout[-3500:]} {result.stderr[-2000:]}')
    return result.stdout


def coff(compiler, folder):
    spec = importlib.util.spec_from_file_location('windows_symbols', ROOT/'recomp/windows_symbols.py')
    symbols = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(symbols)
    support = folder/'coff_support.c'
    support.write_text(r'''#include "host.h"
uint8_t *G;
const struct ImportSlot speed_imports[]={{0x401000,"DSOUND.dll!DirectSoundCreate"}};
const unsigned speed_import_count=1;
int win_vm_access(uint32_t p,uint64_t n) { (void)p;(void)n;return 1; }
int win_vm_commit(uint32_t p,uint64_t n) { (void)p;(void)n;return 1; }
uint32_t win_alloc(uint32_t n) { (void)n;return 0; }
void gil_acquire(void) {} void gil_release(void) {}
void gil_maybe_yield(Cpu *c) { (void)c; }
_Noreturn void win_fail(const char *s,uint32_t p) { (void)s;(void)p;exit(2); }
int win_kernel_import(Cpu *c,const char *s) { (void)c;(void)s;return 0; }
void win_startup_checkpoint(Cpu *c,const struct ImportSlot *e,uint32_t p,uint32_t q) {
    (void)c;(void)e;(void)p;(void)q;
}
''')
    sources = [WIN/'dsound.c', WIN/'pcm_endpoint.c', WIN/'imports.c', WIN/'portable.c',
               ROOT/'host/audio/dsound_host.cpp', WIN/'audio_output.cpp', support]
    for mode in ('freestanding', 'hosted'):
        output = folder/mode
        output.mkdir()
        objects = []
        for source in sources:
            obj = output/(source.stem+'.obj')
            flags = [compiler, '-target', 'x86_64-pc-windows-msvc', '-O2', '-Wall', '-Wextra', '-Werror',
                     '-DRECOMP_WINDOWS_COMPILE_ONLY', '-DNFS_WIN_AUDIO', '-I', WIN,
                     '-I', ROOT/'recomp', '-I', WIN/'audio_compile']
            if mode == 'freestanding':
                flags += ['-ffreestanding']
            else:
                # The existing SDK-free Win64 setjmp declaration has a frame
                # argument; clang's hosted builtin models the one-argument CRT.
                flags += ['-fno-builtin-_setjmp']
            if source.suffix == '.cpp':
                flags += ['-std=c++17', '-fno-exceptions', '-fno-rtti']
            else:
                flags += ['-std=c11']
            run([*flags, '-c', source, '-o', obj])
            objects.append(obj)
        assert symbols.audit(compiler, [], objects, output)
        closure = json.loads((output/'symbol-closure.json').read_text())
        assert closure['unknown'] == []
        print(f'COFF {mode}: {len(objects)}/{len(sources)} objects, UNKNOWN=0')
        # Removing the real native output adapter must expose its dependency.
        assert not symbols.audit(compiler, [], [o for o in objects if o.stem!='pcm_endpoint'], output)
        missing = json.loads((output/'symbol-closure.json').read_text())
        assert missing['unknown'] == ['win_pcm_endpoint'], missing
        print(f'COFF {mode} missing-endpoint control: UNKNOWN=1, rejected')
    # Native STL symbols must have real definitions in explicitly linked CRT
    # archives. An unrelated missing host symbol is never granted an exemption.
    spec = importlib.util.spec_from_file_location('windows_audio_symbols', ROOT/'recomp/windows_audio_symbols.py')
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    source = folder/'native_crt.c'
    source.write_text('void audio_crt_fixture(void) {}\n')
    obj = folder/'native_crt.obj'
    run([compiler, '-target', 'x86_64-pc-windows-msvc', '-c', source, '-o', obj])
    blob = obj.read_bytes()
    archive = folder/'libcpmt.lib'
    header = f'{"fixture.obj/":<16}{0:<12}{0:<6}{0:<6}{100644:<8}{len(blob):<10}`\n'.encode()
    assert len(header) == 60
    archive.write_bytes(b'!<arch>\n'+header+blob+(b'\n' if len(blob)%2 else b''))
    names = {'audio_crt_fixture', 'win_pcm_endpoint'}
    assert native.native_cpp_symbols(symbols.find_nm(compiler), names, [folder]) == {'audio_crt_fixture': 'libcpmt.lib'}
    assert native.native_cpp_symbols(symbols.find_nm(compiler), names, [folder/'absent']) == {}
    print('native CRT inventory: defined name accepted; missing host/absent archive rejected')


def main():
    compiler = shutil.which('clang')
    if not compiler:
        print('SKIP: no C/C++ compiler')
        return 3
    with tempfile.TemporaryDirectory(prefix='windows-audio-') as scratch:
        folder = Path(scratch)
        fake = (WIN/'fixture.h').read_text().split('const struct ImportSlot speed_imports[]')[0]
        (folder/'fake_win.inc').write_text(fake)
        fixture = folder/'fixture.c'
        fixture.write_text(FIXTURE)
        common = ['-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                  '-DNFS_WIN_TEST', '-I', WIN, '-I', ROOT/'recomp', '-I', folder, '-pthread']
        units = [WIN/n for n in ('portable.c', 'memory.c', 'core.c', 'kernel.c', 'imports.c',
                                  'startup.c', 'threads.c', 'sync.c', 'dsound.c', 'pcm_endpoint.c')]
        objects = []
        for source in [*units, fixture]:
            obj = folder/(source.stem+'.o')
            run([compiler, '-std=c11', *common, '-c', source, '-o', obj])
            objects.append(obj)
        probe = folder/'media_probe.cpp'
        probe.write_text(MEDIA_PROBE)
        cpp = [ROOT/'host/audio/dsound_host.cpp', WIN/'audio_output.cpp', probe]
        for source in cpp:
            obj = folder/(source.stem+'.o')
            run([compiler, '-std=c++17', *common, '-I', ROOT/'media', '-DNFS_WIN_AUDIO', '-c', source, '-o', obj])
            objects.append(obj)
        exe = folder/'fixed'
        link = [compiler, '--driver-mode=g++', '-fsanitize=address,undefined', '-pthread']
        run([*link, *objects, '-o', exe])
        assert 'unsupported import DSOUND.dll!DirectSoundCreate' in run([exe, 'original'], 2)
        print('original dispatcher: handled=0/1, unsupported import, exit 2')
        assert 'first import DSOUND.dll!DirectSoundCreate' in run([exe, 'first'], 10)
        capture = folder/'portable.pcm'
        print(run([exe, capture]).strip())
        # Mac uses the original Mach timing/thread-priority branch, with identical DSP.
        mac_obj = folder/'mac_mixer.o'
        run([compiler, '-std=c++17', *common, '-c', cpp[0], '-o', mac_obj])
        mac = folder/'mac'
        run([*link, *(mac_obj if p.stem=='dsound_host' else p for p in objects), '-o', mac])
        mac_capture = folder/'mac.pcm'
        run([mac, mac_capture])
        assert capture.read_bytes() == mac_capture.read_bytes()
        print(f'Mac mixer parity: {capture.stat().st_size} sample bytes identical')
        for label, source, before, after, expected in (
            ('wrap', WIN/'dsound.c', 'M32(p2)=n2?b->memory+o2:0;',
             'M32(p2)=n2?b->memory+o2+12:0;', 'wrap mismatches=1'),
            ('channels', WIN/'audio_output.cpp', 'if (!out->endpoint.write(out->endpoint.user, pcm, count)) {',
             'for (size_t f=0;f<count;++f) { float v=pcm[f*6+2]; pcm[f*6+2]=pcm[f*6+3]; pcm[f*6+3]=v; }\n'
             '        if (!out->endpoint.write(out->endpoint.user, pcm, count)) {', 'channel mismatches=2'),
            ('queue', WIN/'audio_output.cpp', 'stats.queued_frames = queued_frames();',
             'stats.queued_frames = 0;', 'native queue probe=0')):
            text = source.read_text()
            assert text.count(before)==1
            mutant = folder/(label+source.suffix)
            mutant.write_text(text.replace(before,after))
            obj = mutant.with_suffix('.o')
            flags = ['-std=c++17', '-DNFS_WIN_AUDIO'] if source.suffix=='.cpp' else ['-std=c11']
            run([compiler, *flags, *common, '-c', mutant, '-o', obj])
            broken = folder/(label+'-broken')
            run([*link, *(obj if p.stem==source.stem else p for p in objects), '-o', broken])
            result = run([broken], 1)
            assert expected in result, result
            print(f'{label} negative control: {expected}, exit 1')
        coff(compiler, folder)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
