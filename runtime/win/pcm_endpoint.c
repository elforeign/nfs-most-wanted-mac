#include "host.h"
#include "audio_output.h"

// WaveOut copies no samples: each submitted WAVEHDR owns its storage until
// WHDR_DONE, and reset completes every header before teardown frees it.
#define PCM_BLOCKS 16
#define PCM_FRAMES 256
typedef struct {
    HWAVEOUT handle;
    WAVEHDR headers[PCM_BLOCKS];
    float pcm[PCM_BLOCKS][PCM_FRAMES * 6];
} PcmEndpoint;
static int pcm_open(void *user, unsigned rate, unsigned channels, uint32_t mask) {
    PcmEndpoint *p=user;
    if (!p || p->handle || rate!=48000 || channels!=6 || mask!=0x3f) return 0;
    // WAVEFORMATEXTENSIBLE, IEEE float, FL FR FC LFE BL BR.
    uint8_t format[40]={0};
    uint16_t tag=0xfffe, ch=6, block=24, bits=32, cb=22;
    uint32_t bytes=48000*24, sub=3;
    __builtin_memcpy(format,&tag,2); __builtin_memcpy(format+2,&ch,2);
    __builtin_memcpy(format+4,&rate,4); __builtin_memcpy(format+8,&bytes,4);
    __builtin_memcpy(format+12,&block,2); __builtin_memcpy(format+14,&bits,2);
    __builtin_memcpy(format+16,&cb,2); __builtin_memcpy(format+18,&bits,2);
    __builtin_memcpy(format+20,&mask,4); __builtin_memcpy(format+24,&sub,4);
    format[30]=0x10; format[32]=0x80; format[35]=0xaa;
    format[37]=0x38; format[38]=0x9b; format[39]=0x71;
    if (waveOutOpen(&p->handle,0xffffffffu,(const WAVEFORMATEX *)format,0,0,0)) {
        p->handle=NULL; return 0;
    }
    return 1;
}
static int pcm_write(void *user, const float *pcm, size_t frames) {
    PcmEndpoint *p=user;
    if (!p->handle || !frames || frames>PCM_FRAMES) return 0;
    for (unsigned i=0; i<PCM_BLOCKS; ++i) {
        WAVEHDR *h=p->headers+i;
        if ((h->dwFlags & 2) && !(h->dwFlags & 1)) continue; // prepared, in flight
        if ((h->dwFlags & 2) && waveOutUnprepareHeader(p->handle,h,sizeof *h)) return 0;
        memset(h,0,sizeof *h);
        __builtin_memcpy(p->pcm[i],pcm,frames*24);
        h->lpData=(char *)p->pcm[i]; h->dwBufferLength=(DWORD)(frames*24);
        if (waveOutPrepareHeader(p->handle,h,sizeof *h)) return 0;
        return waveOutWrite(p->handle,h,sizeof *h)==0;
    }
    return 0;
}
static size_t pcm_queued(void *user) {
    PcmEndpoint *p=user; size_t frames=0;
    for (unsigned i=0; i<PCM_BLOCKS; ++i) {
        WAVEHDR *h=p->headers+i;
        if ((h->dwFlags & 2) && !(h->dwFlags & 1)) frames+=h->dwBufferLength/24;
    }
    return frames;
}
static void pcm_close(void *user) {
    PcmEndpoint *p=user;
    if (!p) return;
    if (p->handle) {
        // If the driver refuses teardown, retain its headers/sample storage.
        // Freeing a still-owned block would let the driver read freed memory.
        if (waveOutReset(p->handle)) return;
        for (unsigned i=0; i<PCM_BLOCKS; ++i)
            if ((p->headers[i].dwFlags & 2) &&
                waveOutUnprepareHeader(p->handle,p->headers+i,sizeof(WAVEHDR))) return;
        if (waveOutClose(p->handle)) return;
    }
    free(p);
}
WinPcmEndpoint win_pcm_endpoint(void) {
    PcmEndpoint *p=malloc(sizeof *p);
    if (p) memset(p,0,sizeof *p);
    return (WinPcmEndpoint){p,pcm_open,pcm_write,pcm_queued,pcm_close};
}
