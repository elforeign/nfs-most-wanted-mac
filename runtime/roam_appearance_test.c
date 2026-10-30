// Production LOOK gather and spawn customization with fake asset lookup/factory.
#define main switch_test_main
#include "roam_switch_test.c"
#undef main
int main(void) {
    G=calloc(1,16*1024*1024);if(!G)return 2;
    Cpu c={0};budget_mode=appearance_mode=1;roam_mem=0x10000;
    M32(0x0092CD7C)=0x8000;M32(0x0092CD84)=1;M32(0x8000)=0x9000;
    M32(0x9028)=0xa000;M32(0xa020)=123;M32(0x9038)=0xd000;
    memset(G+0xd000,0xff,0x198);M16(0xd000)=10;M16(0xd000+2*76)=10;
    M32(0xd000+0x138)=0x44332211;M32(0xd000+0x18c)=0x12345678;
    roam_send_look(1000,(uint8_t[ROAM_MAX_DGRAM]){0});
    CHECK(roam_local_look.rev==1 && roam_local_look.nparts==92 && roam_local_look.car==123);
    CHECK((roam_local_look.parts[0]&0xffff)==10 && (roam_local_look.parts[0]>>16)==65535);
    CHECK(roam_local_look.parts[70]==0x44332211 && roam_local_look.parts[91]==0x12345678);
    unsigned tx=roam_tx;roam_send_look(1100,(uint8_t[ROAM_MAX_DGRAM]){0});CHECK(roam_tx==tx);
    M32(0xd000+0x138)=1;roam_send_look(2000,(uint8_t[ROAM_MAX_DGRAM]){0});CHECK(roam_local_look.rev==2);
    RoamRemote remote={.has_look=1,.look=roam_local_look};
    float pos[3]={10,20,30},q[4]={0,0,0,1},m[4][4];roam_quat_rows4(q,pos,m);
    CHECK(roam_spawn(&c,pos,m,&remote,3)!=0);
    uint32_t custom=M32(roam_mem+0x40100+0x20);
    CHECK(custom==roam_mem+0x41600 && M32(roam_mem+0x40100+0x14)==123);
    CHECK(M16(custom)==10 && M16(custom+2)==65535 && M16(custom+2*76)==10);
    CHECK(M32(custom+0x138)==1 && M32(custom+0x18c)==0x12345678);
    // Out-of-range or unavailable parts cannot become pointers in a guest record.
    remote.look.parts[0]=0x7fff7ffe;CHECK(roam_spawn(&c,pos,m,&remote,2)!=0);
    custom=M32(roam_mem+0x40100+0x20);CHECK(M16(custom)==65535 && M16(custom+2)==65535);
    remote.look.nparts=0;CHECK(roam_spawn(&c,pos,m,&remote,1)!=0);CHECK(M32(roam_mem+0x40100+0x20)==0);
    remote.look.car=0xdead;CHECK(roam_spawn(&c,pos,m,&remote,0)!=0);CHECK(M32(roam_mem+0x40100+0x20)==0xd000);
    uint8_t packet[ROAM_MAX_DGRAM];int n=roam_client_look(&roam,&roam_local_look,packet);
    CHECK(n<1200 && n==ROAM_HDR+7+92*4);
    RoamLook decoded;CHECK(!roam_look_get(packet+ROAM_HDR,n-ROAM_HDR,&decoded) && decoded.parts[70]==1);
    // A clone other than zero may win the budget: its label must follow that actual car.
    atomic_store(&roam_online_active,1);roam.welcomed=1;roam.nmembers=1;roam.members[0]=7;
    uint32_t now=roam_ms();RoamState state={.t=now,.pos={100,0,0},.q={0,0,0,32767},.flags=ROAM_FLAG_CAR};
    roam_view_add(&roam.view,7,1,&state,now);memset(roam_car,0,sizeof roam_car);
    roam_car[0]=(RoamCar){.used=1,.id=7,.clone=3};RoamHudPlayer hud;
    CHECK(roam_game_hud(&hud,1)==1 && hud.drawable);
    CHECK(hud.pos[0]==100 && hud.label_pos[0]==115);
    printf("appearance: %d checks, %d failures; LOOK=%d bytes, indices=139 colours=22\n",checks,failures,n);
    free(G);return failures?1:0;
}
