// Saved career keyboard bindings take priority over replay shortcuts.
#include "host.h"
#include "replay_input.h"
#define GA 0x0091F418u // GameAction[76], four 12-byte bindings per 52-byte row
enum { GA_N=76, T_KEY=1 };
static uint32_t rec(int a,int slot) { return GA+0x34u*(uint32_t)a+4+12u*(uint32_t)slot; }
int replay_key_in_use(unsigned scan) {
    for(int a=0;a<GA_N;++a) for(int slot=0;slot<4;++slot) {
        uint32_t r=rec(a,slot);
        if(M32(r)==T_KEY && M32(r+4)==scan) return 1;
    }
    return 0;
}
