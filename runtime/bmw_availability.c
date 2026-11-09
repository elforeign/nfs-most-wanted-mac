/* Make the existing stock coupe an ordinary car-lot/Quick Race choice. */
#include "host.h"
#include <stdlib.h>

#define FEDB 0x0091CF90u
#define FRONTEND 0x85885722u
#define BMW_FRONTEND 0x23CC4A4Eu
#define BMW_STOCK 0x4E085FB3u
#define PRICE_FRONTEND 0x3C4DA7DAu /* retail 997 Carrera S */

static int enabled(void) {
    static int value = -1;
    if (value < 0) {
        const char *s = getenv("NFS_BMW_AVAILABLE");
        value = !s || (strcmp(s, "off") && strcmp(s, "0"));
    }
    return value;
}

static int price_policy(Cpu *c) {
    uint32_t args[3] = {FRONTEND, BMW_FRONTEND, 0};
    if (!call_guest(c, 0x00455FD0u, 2, args)) return 0;
    args[1] = PRICE_FRONTEND;
    if (!call_guest(c, 0x00455FD0u, 2, args)) return 0;
    /* A scoped native instance borrows the currently loaded FE vault. Never
     * retain a layout pointer across its unload/reload. */
    c->esp -= 64;
    uint32_t instance = c->esp;
    args[0] = PRICE_FRONTEND; args[1] = args[2] = 0;
    c->ecx = instance;
    call_guest(c, 0x0051E1A0u, 3, args);
    uint32_t price = M32(M32(instance + 8) + 0x4C);
    c->ecx = instance;
    call_guest(c, 0x0045A430u, 0, NULL);
    args[0] = BMW_FRONTEND;
    c->ecx = instance;
    call_guest(c, 0x0051E1A0u, 3, args);
    if (price) {
        M32(M32(instance + 8) + 0x4C) = price;
        M8(M32(instance + 8) + 0x59) = 16; /* includes profiles without a career */
    }
    c->ecx = instance;
    call_guest(c, 0x0045A430u, 0, NULL);
    return price != 0;
}

void f_007C2780_orig(Cpu *c);
void f_007C2780(Cpu *c) {
    Cpu saved = *c;
    uint32_t frontend = M32(FEDB);
    if (frontend) {
        int active = enabled();
        if (active && !price_policy(c)) active = 0;
        uint32_t race_settings = 0;
        if (!active) {
            uint32_t kind = 11;
            c->ecx = frontend;
            race_settings = call_guest(c, 0x0056DBE0u, 1, &kind);
        }
        for (unsigned player = 0; player < 2; ++player) {
            uint32_t profile = M32(frontend + 0x10 + 4 * player);
            if (!profile) continue;
            for (unsigned i = 0; i < 200; ++i) {
                uint32_t row = profile + 0x414 + 20 * i, flags = M32(row + 12);
                /* Presets and owned/customized cars have independent lifetimes.
                 * In particular, the scripted starter is not BMW_STOCK. */
                if (M32(row) >= 200 || M32(row + 4) != BMW_FRONTEND ||
                    M32(row + 8) != BMW_STOCK || M16(row + 16) != 0xFFFF ||
                    (flags != 0x20020 && flags != 0x20001)) continue;
                M32(row + 12) = active ? 0x20001 : 0x20020;
                /* A remembered disabled stock choice would otherwise reopen
                 * the unavailable-car tab. Let native Setup choose Stock. */
                if (race_settings && M32(race_settings + 0x10 + 4 * player) == M32(row))
                    M32(race_settings + 0x10 + 4 * player) = 0x12345678u; /* native Stock fallback */
            }
        }
    }
    *c = saved;
    /* Setup reads the saved selection's flags before building either list. */
    f_007C2780_orig(c);
}
