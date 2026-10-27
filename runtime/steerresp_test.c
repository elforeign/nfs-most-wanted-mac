// steerresp_test.c - prints steerresp.c's three curves as the hook leaves them in guest memory, for steerresp_test.py (no game).
// -DNO_SYNC leaves the hook out (the broken run: the table never changes).
#include <stdlib.h>
#include "steerresp.c"
uint8_t *G; int diag_on = 0; void settings_add(Setting *s) { (void)s; }
void diag_event(const char *f, ...) { (void)f; }
static int calls;
void f_0069E820_orig(Cpu *c) { (void)c; ++calls; }
int main(int argc, char **argv) {
    (void)argc;
    G = calloc(1, 0x1000000);
    for (int i = 0; i < STEER_N; ++i) {      // argv[1]: the exe's 21 floats as 8 hex digits each (little-endian bytes)
        unsigned char b[4];
        for (int k = 0; k < 4; ++k) { char h[3] = {argv[1][8 * i + 2 * k], argv[1][8 * i + 2 * k + 1], 0}; b[k] = (unsigned char)strtoul(h, NULL, 16); }
        memcpy(G + STEER_DATA + 4 * i, b, 4);
    }
    Cpu cpu; memset(&cpu, 0, sizeof cpu);
    cpu.esp = 0x800000;
    for (int mode = 0; mode < 3; ++mode) {
        s_resp.value = mode;
#ifndef NO_SYNC
        f_0069E820(&cpu);
#endif
        const float *d = (const float *)(G + STEER_DATA);
        printf("%d", mode);
        for (int i = 0; i < STEER_N; ++i) printf(" %.6f", (double)d[i]);
        printf(" |");
        static const float in[] = {-1, -.5f, -.25f, 0, .05f, .3f, .5f, .7f, .9f, 1};
        for (int k = 0; k < 10; ++k) printf(" %.6f", (double)steerresp_lookup(d, in[k]));
        printf("\n");
    }
    return calls == 3 ? 0 : 1;
}
