// Passive experimental LAN diagnostics. No packet contents/passwords are logged.
#pragma once
static int lan_diag_on(void) {
    static int enabled = -1;
    if (enabled < 0) { const char *v=getenv("NFS_LAN"); enabled=v && !strcmp(v,"1"); }
    return diag_on && enabled;
}
typedef struct {
    uint8_t header[12];
    uint32_t used, left, frames, last_code;
    char last_command[5];
    int opaque;
} LanWire;
static uint32_t lan_wire_u32(const uint8_t *p) {
    return (uint32_t)p[0]<<24 | (uint32_t)p[1]<<16 | (uint32_t)p[2]<<8 | p[3];
}
static void lan_wire_done(LanWire *w,const char *direction) {
    memcpy(w->last_command,w->header,4);w->last_command[4]=0;
    w->last_code=lan_wire_u32(w->header+4);++w->frames;
    fprintf(stderr,"lan-wire: %s %.4s code=%d bytes=%u\n",direction,w->last_command,
        (int32_t)w->last_code,lan_wire_u32(w->header+8));
    w->used=0;
}
// ProtoLobby 008521B0: command/code/total size, each a big-endian u32.
// Keep framing over partial/coalesced TCP reads. Observe only complete frames.
static void lan_wire_feed(LanWire *w,const char *direction,const uint8_t *p,size_t n) {
    if (!lan_diag_on() || w->opaque) return;
    while(n) {
        if(w->used<12) {
            size_t take=12-w->used;if(take>n)take=n;
            memcpy(w->header+w->used,p,take);w->used+=(uint32_t)take;p+=take;n-=take;
            if(w->used<12)return;
            uint32_t size=lan_wire_u32(w->header+8);
            int valid=size>=12 && size<=1024*1024;
            for(unsigned i=0;i<4;i++) valid &= w->header[i]>=32 && w->header[i]<127;
            if(!valid) {w->opaque=1;fprintf(stderr,"lan-wire: %s opaque stream\n",direction);return;}
            w->left=size-12;
            if(!w->left) {lan_wire_done(w,direction);continue;}
        }
        size_t take=w->left;if(take>n)take=n;
        w->left-=(uint32_t)take;p+=take;n-=take;
        if(!w->left)lan_wire_done(w,direction);
    }
}
