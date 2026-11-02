// extratrax_public.c - the PUBLIC build's stand-in for extratrax.c (NFS_PUBLIC=1; runtime/build.sh swaps it in): Extra EA Trax
// is owner-only, so its code, song catalog, F10 row and encoder are not built. The recompiled game still calls the
// hook addresses listed in recomp/hooks.txt. Profile wrappers also notify optional cosmetic storage; other hooks pass through.
#include "funcs.h"
#include "host.h"
#include "x360audio.h"
#include "underglow.h"
#include "carsave_public.h"

#define PASS(addr) void f_##addr##_orig(Cpu *c); void f_##addr(Cpu *c) { f_##addr##_orig(c); }
void f_005AB450_orig(Cpu *c);
void f_005AB450(Cpu *c) {
    uint32_t self=c->ecx;
    f_005AB450_orig(c);
    underglow_profile_default(self);
}
void f_005ACB70_orig(Cpu *c);
void f_005ACB70(Cpu *c) {
    uint32_t self=c->ecx, buffer=M32(c->esp+4), size=M32(c->esp+8);
    carsave_public_load(c);
    if (c->eax&255) underglow_profile_load(self,buffer,size);
}
void f_0058FD10_orig(Cpu *c);
void f_0058FD10(Cpu *c) {
    uint32_t self=c->ecx, buffer=M32(c->esp+4), size=M32(c->esp+8);
    underglow_save_begin();
    carsave_public_serialize(c);
    underglow_profile_save(self,buffer,size);
}
PASS(004DF330)
PASS(004F4CD0)
PASS(0055A370)
PASS(005504F0)
PASS(00550B30)
PASS(0051E000)
PASS(0051E110)
PASS(00511210)
PASS(00511260)

int extratrax_open(int is_mus, const X360MusicHandle *base_mpf, const X360MusicHandle *base_mus, X360MusicHandle *out) { return 0; }
void extratrax_save_complete(int committed) {}
uint32_t extratrax_licensed_song_count(void) { return M32(0x008F42C4u); }   // g_MaxSongs, as the game reads it
