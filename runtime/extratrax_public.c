// extratrax_public.c - the PUBLIC build's stand-in for extratrax.c (NFS_PUBLIC=1; runtime/build.sh swaps it in): Extra EA Trax
// is owner-only, so its code, song catalog, F10 row and encoder are not built. The recompiled game still calls the
// hook addresses listed in recomp/hooks.txt, so each one here just runs the original; nothing else is registered.
#include "funcs.h"
#include "host.h"
#include "x360audio.h"

#define PASS(addr) void f_##addr##_orig(Cpu *c); void f_##addr(Cpu *c) { f_##addr##_orig(c); }
PASS(005AB450)
PASS(005ACB70)
PASS(0058FD10)
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
