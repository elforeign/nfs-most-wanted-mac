#include "replay_input.h"

int replay_input_key(ReplayInput *s,unsigned vk,int down,int repeat,int enabled,unsigned key,int blocked,int *save) {
    *save=0;
    if(vk>=256) return 0;
    if(s->keys[vk]) {
        int consumed=s->keys[vk]==1;
        if(!down) s->keys[vk]=0;
        return consumed;
    }
    if(!enabled || vk!=key || blocked) {
        if(down) s->keys[vk]=2;
        return 0;
    }
    if(down) { s->keys[vk]=1; if(!repeat) *save=1; }
    return 1;
}
ReplayPad replay_input_pad(ReplayInput *s,double now,uint32_t phys,int choice,int blocked) {
    ReplayPad out={0};
    // Neither chord contains View, Home or touchpad (menu and guide). Both
    // sticks together always belong to the menu's L3+R3 binding.
    uint32_t chord=choice==1?(16u|32u|256u):choice==2?(16u|32u|512u):0;
    if(choice!=s->choice) {
        s->pending=0; s->inject=0;
        if(s->lastPad & chord) s->passthrough=1;
        s->choice=choice;
    }
    s->lastPad=phys;
    uint32_t old=s->consumed;
    s->consumed &= phys;
    out.hide=old;
    if(!chord && !s->consumed && !s->pending) return out;
    if(s->cancelled) {
        if(!phys) s->cancelled=0;
        out.hide|=phys; return out;
    }
    if(s->consumed) { out.hide|=chord; return out; }
    if(!chord || blocked || (phys & (64u|128u)) || (phys & 768u)==768u) {
        s->pending=0; s->inject=0; s->passthrough=(phys & chord)!=0;
        return out;
    }
    if(!(phys & chord)) s->passthrough=0;
    if(!s->passthrough) {
        uint32_t parts=phys & chord;
        if(parts && !s->pending) {
            s->pendingAt=now;
            // A new physical tap must release its previous injected pulse
            // before another pulse can start on this tap's release.
            s->inject &= ~parts;
        }
        s->pending |= parts;
        if(now-s->pendingAt<.15 && parts==chord) {
            out.save=1; s->consumed=chord; s->pending=0; s->inject=0;
            out.hide|=chord; return out;
        }
        if(s->pending) {
            if(now-s->pendingAt<.15 && parts==s->pending) out.hide|=chord;
            else {
                // Incomplete chord: deliver held buttons and a short pulse
                // for taps released during the recognition interval.
                s->inject=s->pending & ~phys; s->injectUntil=now+.12;
                s->pending=0; s->passthrough=(phys & chord)!=0;
            }
        }
    }
    if(now<s->injectUntil) out.inject=s->inject;
    return out;
}
void replay_input_cancel(ReplayInput *s) {
    s->pending=0; s->inject=0; s->consumed=0; s->cancelled=1;
    // Keep consumed keys until their physical release, including after a
    // binding change or focus loss. No synthetic release reaches the game.
}
void replay_input_disconnect(ReplayInput *s) {
    s->pending=0; s->inject=0; s->consumed=0; s->passthrough=0; s->cancelled=1;
}
