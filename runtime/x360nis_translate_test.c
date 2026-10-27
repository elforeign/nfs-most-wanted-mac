// x360nis_translate_test.c - production nis_translate (x360nisaudio.c, included) on a synthetic stream map shaped like the
// retail one (audit2 #3): stream 0 PC extent [0,1000) but 1100 bytes in the X360 BIG; stream 1 starts at PC 1000.
// Sequential 100-byte reads of stream 0 pass PC 1000: they must stay on stream 0 (+100 bytes of overrun), and a jump to
// PC 1000 after stream 0 was left (or from nowhere) starts stream 1. Prints the translation of each read.
// -DOLD_SIG: the pre-fix call (no read length), built against the pre-fix x360nisaudio.c (x360nis_translate_test.py --old).
#include <stdarg.h>
#include <stdio.h>
#include "heap.c"
#include "x360nisaudio.c"
uint8_t *G; const char *game_dir; int diag_on=0; const char *diag_dir="/tmp"; uint32_t last_error;
const char *app_pref(const char *k) { (void)k; return NULL; }
void app_pref_set(const char *k, const char *v) { (void)k; (void)v; }
void report_note(const char *f, ...) { (void)f; }
void diag_event(const char *f, ...) { (void)f; }
uint64_t host_ticks(void) { return 0; }
void gil_release(void) {}
void gil_acquire(void) {}
const char *diag_symbol(uint32_t a, uint32_t *o) { (void)a; (void)o; return NULL; }
void diag_report(const char *f, const char *w) { (void)f; (void)w; }
void dispatch(Cpu *c, uint32_t t) { (void)c; (void)t; exit(3); }
void f_00828DDE(Cpu *c) { (void)c; }
void f_00828773(Cpu *c) { (void)c; }
void f_008287AE(Cpu *c) { (void)c; }
void f_00828976(Cpu *c) { (void)c; }
#ifdef OLD_SIG
#define TR(pos,n,x,s,w) nis_translate(pos,x,s,w)
#else
#define TR(pos,n,x,s,w) nis_translate(pos,n,x,s,w)
#endif
int main(void) {
    nis_map[0]=(NisMap){0,1000,50000,1100};
    nis_map[1]=(NisMap){1000,2000,70000,1100};
    int bad=0; size_t x; int st; unsigned w;
    for (size_t pos=0; pos<1100; pos+=100) {                       // stream 0, sequential, running 100 bytes past its PC extent
        if (!pos) { if (!TR(pos,100,&x,&st,&w) || w!=0 || !st) bad++; continue; }
        int ok=TR(pos,100,&x,&st,&w);
        int want_stream=0;
        printf("read PC %4zu -> stream %u X360 %zu started %d\n",pos,ok?w:99,ok?x:0,st);
        if (!ok || w!=want_stream || x!=50000+pos || st) { printf("  WRONG: expected stream 0 X360 %zu continuing\n",50000+pos); bad++; }
    }
    // Stream 0 is left behind (its X360 extent ended); the game opens stream 1 at its PC start.
    int ok=TR(1000,100,&x,&st,&w);
    if (!ok || w!=1 || !st) { printf("fresh start of stream 1: WRONG\n"); bad++; }
    // A fresh start of stream 1 while stream 0 was last read to a different place is a new stream.
#ifndef OLD_SIG
    nis_end[0]=500;
#endif
    ok=TR(1000,100,&x,&st,&w);
    printf("stream 1 start with stream 0 last read ending at 500 -> stream %u started %d\n",w,st);
    if (!ok || w!=1 || !st) bad++;
    printf(bad?"FAIL (%d)\n":"PASS\n",bad);
    return bad!=0;
}
