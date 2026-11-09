#include "input.h"
#define EVENTS 256u
typedef struct { WinInputEvent events[EVENTS]; uint32_t head,tail,reset; } Journal;
static Journal journals[2];
static uint8_t keys[256],buttons[8];
static uint32_t motion[3],sequence;
static int focused,connected[2]={1,1};
static WinPadState pads[WIN_PAD_COUNT];
static int pad_connected[WIN_PAD_COUNT];
static void event(unsigned kind,unsigned offset,uint32_t data) {
    Journal *j=&journals[kind-1];
    if (j->tail-j->head==EVENTS) ++j->head;
    j->events[j->tail++%EVENTS]=(WinInputEvent){offset,data,(uint32_t)(host_ticks()/1000000),++sequence};
}
static void release(unsigned kind) {
    Journal *j=&journals[kind-1];
    j->head=j->tail; j->reset=j->tail; // discard pending presses before publishing neutral releases
    if (kind==WIN_KEYBOARD) {
        for (unsigned i=0;i<256;++i) if (keys[i]) { keys[i]=0; event(kind,i,0); }
    } else {
        for (unsigned i=0;i<8;++i) if (buttons[i]) { buttons[i]=0; event(kind,12+i,0); }
        memset(motion,0,sizeof motion);
    }
}
void win_input_focus(int value) {
    focused=value!=0;
    if (!focused) { release(WIN_KEYBOARD); release(WIN_MOUSE); }
}
unsigned win_input_key_state(unsigned key) { return key<256?keys[key]:0; }
unsigned win_input_button_state(unsigned button) { return button<8?buttons[button]:0; }
void win_input_connect(unsigned kind,int value) {
    if (kind<1 || kind>2) return;
    connected[kind-1]=value!=0;
    if (!value) release(kind);
}
void win_input_key(unsigned key,int down) {
    if (key>=256 || !focused || !connected[0]) return;
    uint8_t v=down?128:0;
    if (keys[key]!=v) { keys[key]=v; event(WIN_KEYBOARD,key,v); }
}
void win_input_button(unsigned button,int down) {
    if (button>=8 || !focused || !connected[1]) return;
    uint8_t v=down?128:0;
    if (buttons[button]!=v) { buttons[button]=v; event(WIN_MOUSE,12+button,v); }
}
void win_input_motion(int32_t x,int32_t y,int32_t z) {
    if (!focused || !connected[1]) return;
    uint32_t values[3]={(uint32_t)x,(uint32_t)y,(uint32_t)z};
    for (unsigned i=0;i<3;++i) if (values[i]) { motion[i]+=values[i]; event(WIN_MOUSE,i*4,values[i]); }
}
void win_input_state(unsigned kind,uint32_t p,uint32_t n) {
    memset(G+p,0,n);
    if (kind==WIN_KEYBOARD) { for (unsigned i=0;i<256;++i) M8(p+i)=keys[i]; }
    else if (kind==WIN_MOUSE) {
        for (unsigned i=0;i<3;++i) { M32(p+4*i)=motion[i]; motion[i]=0; }
        for (unsigned i=0;i<n-12;++i) M8(p+12+i)=buttons[i];
    }
}
uint32_t win_input_tail(unsigned kind) { return kind>=1 && kind<=2?journals[kind-1].tail:0; }
unsigned win_input_events(unsigned kind,uint32_t *cursor,WinInputEvent *out,unsigned cap,unsigned limit,int *overflow) {
    Journal *j=&journals[kind-1];
    if (j->tail-*cursor>j->tail-j->reset) *cursor=j->reset;
    uint32_t oldest=j->head;
    if (j->tail-oldest>limit) oldest=j->tail-limit;
    if (j->tail-*cursor>j->tail-oldest) { *cursor=oldest; *overflow=1; }
    unsigned n=j->tail-*cursor; if (n>cap) n=cap;
    for (unsigned i=0;i<n;++i) if (out) out[i]=j->events[(*cursor+i)%EVENTS];
    *cursor+=n; return n;
}
void win_input_pad(unsigned index,int present,const WinPadState *state) {
    if (index>=WIN_PAD_COUNT) return;
    pad_connected[index]=present && state;
    if (pad_connected[index]) pads[index]=*state;
    else memset(&pads[index],0,sizeof pads[index]);
}
void win_input_pad_read(unsigned index,WinPadState *out) {
    memset(out,0,sizeof *out);
    for (unsigned i=0;i<WIN_PAD_AXES;++i) out->axes[i]=32768;
    out->pov=UINT32_MAX;
    if (focused && index<WIN_PAD_COUNT && pad_connected[index]) *out=pads[index];
}
