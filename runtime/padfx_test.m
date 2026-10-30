// Offline proof: actual padfx.m + feedback.c, fake clock/guest memory/GC discovery and every output sink.
// Foundation only: GameController and IOKit are never linked. NFS_RUMBLE=off is mandatory.
// Run: runtime/offline_tests.sh padfx (ASan/UBSan); generated role reader is verbatim padbind.c.
// Lifecycle proof: PASS 291/291, 267 fake writes, 0 real opens/writes; original source FAIL 26/291.
// dsstops_test.py discards restoration debt (17/291 failures) or skips reconnect's delayed-release
// settlement (2/291 failures). Both must exit 1; the normal proof exits 0.
// Earlier pedal/suppression proof: PASS 266/266, 237 fake writes, forbidden discovery/output traps both 0.
// Broken runs: missing holds 48/262 failures; missing queued stop 3/266; road disabled 2/266;
// LEDs disabled 15/266; forbidden discovery/output 2/266 (1/1 trapped attempts, no hardware).
// Source mutations: delayed stop clears newer frame 2/266; stale sink retains its request 2/266 failures.
// All-Off source control: 4/266; flat gas / weak late brake: 19/266 versus production 0/266.
// All nine controls must exit 1; the normal proof must exit 0.
#import <Foundation/Foundation.h>
#include "host.h"
#include <math.h>
#include <stdarg.h>
#define PEDALS_NO_TRACE 1
#include "pedals.c"
void settings_add(Setting *s) { (void)s; }
typedef struct { float values[10]; } GCDualSenseAdaptiveTriggerPositionalResistiveStrengths;

static int checks, failures, fake_writes, real_writes, real_opens, discovery_calls, rejected_writes;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; fprintf(stderr,"FAIL %d: %s\n", __LINE__, #x); } } while (0)
static int broken;
static void wrote(BOOL fake) { if (fake) ++fake_writes; else ++real_writes; }
@interface GCColor : NSObject
@property float red, green, blue;
- (instancetype)initWithRed:(float)r green:(float)g blue:(float)b;
@end
@implementation GCColor
- (instancetype)initWithRed:(float)r green:(float)g blue:(float)b {
    if ((self = [super init])) { _red = r; _green = g; _blue = b; } return self;
}
@end
@interface GCDualSenseAdaptiveTrigger : NSObject
@property BOOL fake;
@property BOOL detached;
@property int mode;
@property float a,b,c,d;
@property GCDualSenseAdaptiveTriggerPositionalResistiveStrengths strengths;
- (void)setModeOff;
- (void)setModeFeedbackWithStartPosition:(float)a resistiveStrength:(float)b;
- (void)setModeSlopeFeedbackWithStartPosition:(float)a endPosition:(float)b startStrength:(float)c endStrength:(float)d;
- (void)setModeWeaponWithStartPosition:(float)a endPosition:(float)b resistiveStrength:(float)c;
- (void)setModeVibrationWithStartPosition:(float)a amplitude:(float)b frequency:(float)c;
- (void)setModeFeedbackWithResistiveStrengths:(GCDualSenseAdaptiveTriggerPositionalResistiveStrengths)s;
@end
@implementation GCDualSenseAdaptiveTrigger
- (void)setModeOff { wrote(_fake); if (_detached) { ++rejected_writes; return; } _mode = 0; }
- (void)setModeFeedbackWithStartPosition:(float)a resistiveStrength:(float)b { wrote(_fake); if (_detached) { ++rejected_writes; return; } _mode=1; _a=a; _b=b; }
- (void)setModeSlopeFeedbackWithStartPosition:(float)a endPosition:(float)b startStrength:(float)c endStrength:(float)d {
    wrote(_fake); if (_detached) { ++rejected_writes; return; } _mode=2; _a=a; _b=b; _c=c; _d=d;
}
- (void)setModeWeaponWithStartPosition:(float)a endPosition:(float)b resistiveStrength:(float)c { wrote(_fake); if (_detached) { ++rejected_writes; return; } _mode=3; _a=a; _b=b; _c=c; }
- (void)setModeVibrationWithStartPosition:(float)a amplitude:(float)b frequency:(float)c { wrote(_fake); if (_detached) { ++rejected_writes; return; } _mode=4; _a=a; _b=b; _c=c; }
- (void)setModeFeedbackWithResistiveStrengths:(GCDualSenseAdaptiveTriggerPositionalResistiveStrengths)s { wrote(_fake); if (_detached) { ++rejected_writes; return; } _mode=5; _strengths=s; }
@end
@interface GCDualSenseGamepad : NSObject
@property(strong) GCDualSenseAdaptiveTrigger *leftTrigger, *rightTrigger;
@end
@implementation GCDualSenseGamepad
@end
@interface GCControllerLight : NSObject
@property BOOL fake;
@property BOOL detached;
@property(nonatomic,strong) GCColor *color;
@end
@implementation GCControllerLight
- (void)setColor:(GCColor *)color { wrote(_fake); if (_detached) { ++rejected_writes; return; } _color=color; }
@end
typedef NSInteger GCControllerPlayerIndex;
enum { GCControllerPlayerIndexUnset=-1, GCControllerPlayerIndex1=0, GCControllerPlayerIndex2=1 };
@interface GCController : NSObject
@property BOOL fake;
@property BOOL detached;
@property(strong) GCDualSenseGamepad *extendedGamepad;
@property(strong) GCControllerLight *light;
@property(nonatomic) GCControllerPlayerIndex playerIndex;
+ (NSArray *)controllers;
@end
@implementation GCController
+ (NSArray *)controllers { ++discovery_calls; ++real_opens; return @[]; } // forbidden discovery trap
- (void)setPlayerIndex:(GCControllerPlayerIndex)i { wrote(_fake); if (_detached) { ++rejected_writes; return; } _playerIndex=i; }
@end

// Manual main-queue sink: a producer can queue a frame, stop it, and drain it deterministically.
static NSMutableArray *blocks;
static dispatch_block_t before_enqueue;
static BOOL on_main=YES;
@interface TestThread : NSObject
+ (BOOL)isMainThread;
@end
@implementation TestThread
+ (BOOL)isMainThread { return on_main; }
@end
static void test_async(dispatch_queue_t q, dispatch_block_t block) { (void)q; if (before_enqueue) { dispatch_block_t b=before_enqueue; before_enqueue=nil; b(); } if (!blocks) blocks=[NSMutableArray new]; [blocks addObject:[block copy]]; }
static void drain(void) { while (blocks.count) { dispatch_block_t b=blocks[0]; [blocks removeObjectAtIndex:0]; b(); } }
#define dispatch_async test_async
#define NSThread TestThread
#define PADFX_TEST 1
#ifndef PADFX_SOURCE
#define PADFX_SOURCE "padfx.m"
#endif
#include PADFX_SOURCE
#ifdef PADFX_LEGACY
void padfx_connect(void *gc) { (void)gc; }
#endif
#undef dispatch_async
#undef NSThread
#include "feedback.c"

uint8_t *G;
int diag_on;
const char *diag_dir="/tmp", *game_dir="/nonexistent";
static uint64_t clock_ns=1000000000ull;
static int motor_calls, attr_calls, stop_during_guest, pursuit_present=1;
uint64_t host_ticks(void) { return clock_ns; }
void diag_event(const char *fmt, ...) { (void)fmt; }
void diag_event_ring(const char *fmt, ...) { (void)fmt; }
void rumble_stop(void) { CHECK(!strcmp(getenv("NFS_RUMBLE"),"off")); ++motor_calls; }
void rumble_suppress(int on) { (void)on; rumble_stop(); }
void rumble_signals(void) {}
uint32_t galloc(uint32_t n) { CHECK(n==8); return 0x5000; }
uint32_t host_fn(HostFn fn,const char *name) { (void)fn; (void)name; abort(); }
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *a) {
    (void)c;
    if (fn==FN_PLAYER_PURSUIT) { if (stop_during_guest) { stop_during_guest=0; padfx_stop(); } CHECK(n==2); M32(a[0])=pursuit_present ? 0x6000 : 0; M32(a[1])=0x6500; return 0; }
    if (fn==FN_GET_ATTRIBUTE) { CHECK(n==2); ++attr_calls; return a[1]==0 ? 0x7000 : 0; }
    abort();
}
void f_006EE490_orig(Cpu *c) { (void)c; }
void f_006EE400_orig(Cpu *c) { c->eax=1; }
// Fake binding table plumbing; the role function itself is extracted unchanged at build time.
enum { T_BUTTON=2, T_AXIS_HI=5, T_ROT_HI=7, P_L2=10, P_R2=11, A_GAS=0, A_BRAKE=1, A_SHIFTDOWN=7, A_SHIFTUP=8, GA_PAGE=19 };
static uint32_t rec(int a,int slot) { return 0x91F418u+0x34u*(uint32_t)a+4+12u*(uint32_t)slot; }
static uint32_t joy(void) { return M32(0x91F150); }
static char *joy_name(uint32_t dev) { return (char *)G+dev+0x114; }
static int name_ps(const char *name) { return !strcmp(name,"Controller (DualSense)"); }
#include "padfx_roles.inc"
static void bind(int a,int type,int obj) { uint32_t r=rec(a,2); M32(r)=type; M32(r+8)=obj; }
static void clear_bindings(void) { memset(G+0x91F418,0,76*0x34); }
static GCController *controller(int index) {
    GCController *c=[GCController new]; c.fake=YES;
    c.playerIndex=index; c.light=[GCControllerLight new]; c.light.fake=YES;
    c.light.color=[[GCColor alloc] initWithRed:.2f green:.3f blue:.4f];
    c.extendedGamepad=[GCDualSenseGamepad new];
    c.extendedGamepad.leftTrigger=[GCDualSenseAdaptiveTrigger new]; c.extendedGamepad.leftTrigger.fake=YES;
    c.extendedGamepad.rightTrigger=[GCDualSenseAdaptiveTrigger new]; c.extendedGamepad.rightTrigger.fake=YES;
    return c;
}
static void frame(void) { Cpu c={0}; c.ecx=0x8000; effects_valid=1; padfx_game_tick(&c,c.ecx); }
static void update(GCController *a,GCController *b) {
    padfx_update((__bridge void *)a,1,.8f,0);
    padfx_player_update(0,(__bridge void *)a); padfx_player_update(1,(__bridge void *)b);
}
static void active(GCController *a,GCController *b) { frame(); update(a,b); drain(); }
static void detach(GCController *c, BOOL on) {
    c.detached=on; c.light.detached=on;
    c.extendedGamepad.leftTrigger.detached=on; c.extendedGamepad.rightTrigger.detached=on;
}
static void restored_at(GCController *a,GCController *b,GCColor *ca,GCColor *cb,int line) {
    int before=failures;
    CHECK(a.extendedGamepad.leftTrigger.mode==T_OFF && a.extendedGamepad.rightTrigger.mode==T_OFF);
    CHECK(a.light.color==ca && b.light.color==cb);
    CHECK(a.playerIndex==3 && b.playerIndex==2);
    if (failures!=before) fprintf(stderr,"restoration requested at line %d\n",line);
}
#define restored(a,b,ca,cb) restored_at(a,b,ca,cb,__LINE__)
static void live(GCController *a,GCController *b) {
    CHECK(a.extendedGamepad.leftTrigger.mode!=T_OFF);
    CHECK(a.playerIndex==GCControllerPlayerIndex1 && b.playerIndex==GCControllerPlayerIndex2);
}
int main(int argc,char **argv) { @autoreleasepool {
    setenv("NFS_RUMBLE","off",1);
    unsetenv("NFS_PAD_TRIGGERS"); unsetenv("NFS_PAD_LIGHT"); unsetenv("NFS_PAD_PLAYERS"); unsetenv("NFS_PAD_ROAD");
    CHECK(triggers_on()); CHECK(light_on()); CHECK(player_on()); CHECK(padfx_road_wanted());
    setenv("NFS_PAD_TRIGGERS","off",1);trig_sw=-1;CHECK(!triggers_on());
    setenv("NFS_PAD_TRIGGERS","on",1);trig_sw=-1;CHECK(triggers_on());
    setenv("NFS_PAD_TRIGGERS","off",1);trig_sw=-1;
    CHECK(!padfx_road_wanted()); // loading still requires adaptive triggers
    setenv("NFS_PAD_LIGHT","off",1);light_sw=-1;CHECK(!light_on());
    setenv("NFS_PAD_PLAYERS","off",1);player_sw=-1;CHECK(!player_on());
    setenv("NFS_PAD_ROAD","off",1);road_sw=-1;
    setenv("NFS_PAD_TRIGGERS","on",1);trig_sw=-1;CHECK(!padfx_road_wanted());
    setenv("NFS_PAD_LIGHT","on",1);light_sw=-1;CHECK(light_on());
    setenv("NFS_PAD_PLAYERS","on",1);player_sw=-1;CHECK(player_on());
    setenv("NFS_PAD_ROAD","on",1);road_sw=-1;CHECK(padfx_road_wanted());
    setenv("NFS_PAD_TRIGGERS","off",1);trig_sw=-1;
    setenv("NFS_PAD_LIGHT","off",1);light_sw=-1;
    setenv("NFS_PAD_PLAYERS","off",1);player_sw=-1;
    setenv("NFS_PAD_ROAD","off",1);road_sw=-1;
    broken=argc>1 ? atoi(argv[1]) : 0;
    G=calloc(1,0x1000000); if (!G) return 2;
    M32(0x91F150)=0x9000; strcpy(joy_name(0x9000),"Controller (DualSense)");
    M32(0x9885E0)=0x1000; M32(0x102c)=3;
    M32(0x6000)=VT_IPURSUIT; M8(0x6114)=1;
    M32(0x6500)=0x6600; M32(0x6604)=FN_GET_HEAT; MF32(0x651c)=3;
    const float curve[8]={1,.2f,.1f,0,2,.6f,.2f,60}; memcpy(G+0x7000,curve,sizeof curve);
    GCController *a=controller(3), *b=controller(2), *c=controller(-1);
    GCColor *ca=a.light.color,*cb=b.light.color,*cc=c.light.color;
    CHECK(!triggers_on() && !light_on() && !player_on() && !padfx_road_wanted());
    frame(); update(a,b); drain(); CHECK(real_writes==0 && real_opens==0);
    CHECK(a.light.color==ca && a.playerIndex==3 && b.playerIndex==2);
    CHECK(a.extendedGamepad.leftTrigger.mode==T_OFF);
    // Bindings and half-travel detent remain exact, including when loading is enabled.
    bind(A_SHIFTDOWN,T_AXIS_HI,2); bind(A_SHIFTUP,T_ROT_HI,2);
    CHECK(padbind_trigger_role(0)==PADBIND_ROLE_SHIFT && padbind_trigger_role(1)==PADBIND_ROLE_SHIFT);
    s_pedals.value=1;
    CHECK(padbind_trigger_role(0)==PADBIND_ROLE_BRAKE && padbind_trigger_role(1)==PADBIND_ROLE_GAS);
    s_pedals.value=0;
    CHECK(padbind_trigger_role(0)==PADBIND_ROLE_SHIFT && padbind_trigger_role(1)==PADBIND_ROLE_SHIFT);
    trig_sw=light_sw=1; player_sw=broken==4 ? 0 : 1; road_sw=1;
    active(a,b); live(a,b);
    CHECK(a.extendedGamepad.leftTrigger.mode==T_WEAPON && a.extendedGamepad.leftTrigger.a==.25f &&
          a.extendedGamepad.leftTrigger.b==.5f && a.extendedGamepad.leftTrigger.c==.6f);
    CHECK(b.extendedGamepad.leftTrigger.mode==T_OFF && b.light.color==cb);
    int before=fake_writes; update(a,b); drain(); CHECK(fake_writes==before); // dedup
    bind(A_GAS,T_ROT_HI,2); bind(A_BRAKE,T_AXIS_HI,2);
    CHECK(padbind_trigger_role(0)==PADBIND_ROLE_BRAKE && padbind_trigger_role(1)==PADBIND_ROLE_GAS);
    bind(A_GAS,T_AXIS_HI,2); CHECK(padbind_trigger_role(0)==PADBIND_ROLE_GAS); // gas wins
    clear_bindings(); bind(3,T_BUTTON,P_L2); CHECK(padbind_trigger_role(0)==PADBIND_ROLE_OTHER);
    CHECK(padbind_trigger_role(1)==PADBIND_ROLE_NONE);
    M32(0x91F150)=0; CHECK(padbind_trigger_role(0)==PADBIND_ROLE_NONE); M32(0x91F150)=0x9000;
    float slip[2]={0,0};
    CHECK(trigger_for(PADBIND_ROLE_GAS,0,1,0,slip,.2f).mode==T_OFF);
    CHECK(trigger_for(PADBIND_ROLE_OTHER,1,1,0,slip,.2f).mode==T_OFF);
    Trig gas=trigger_for(PADBIND_ROLE_GAS,1,0,0,slip,0), brake=trigger_for(PADBIND_ROLE_BRAKE,1,0,0,slip,0);
    const float gas_curve[10]={.10f,.10f,.15f,.15f,.15f,.20f,.20f,.20f,.25f,.25f};
    const float brake_curve[10]={.15f,.20f,.20f,.25f,.25f,.30f,.40f,.50f,.65f,.80f};
    CHECK(gas.mode==T_POSITIONAL && brake.mode==T_POSITIONAL);
    for(int i=0;i<10;i++) { CHECK(gas.strength[i]==gas_curve[i]); CHECK(brake.strength[i]==brake_curve[i]); }
    CHECK(brake.strength[9]-brake.strength[6]>2*(brake.strength[5]-brake.strength[2]));
    Trig altered=brake; altered.strength[7]+=.05f; CHECK(!trig_eq(brake,altered));
    CHECK(trig_eq(brake,brake));
    apply_trig(a.extendedGamepad.leftTrigger,brake);
    for(int i=0;i<10;i++) CHECK(a.extendedGamepad.leftTrigger.strengths.values[i]==brake_curve[i]);
    slip[1]=.049f; CHECK(trigger_for(PADBIND_ROLE_GAS,1,1,0,slip,.2f).mode==T_POSITIONAL);
    slip[1]=.05f; CHECK(trigger_for(PADBIND_ROLE_GAS,1,.1f,0,slip,.2f).mode==T_POSITIONAL);
    CHECK(trigger_for(PADBIND_ROLE_GAS,1,1,.1f,slip,.2f).mode==T_POSITIONAL);
    CHECK(trigger_for(PADBIND_ROLE_GAS,1,1,0,slip,.2f).mode==T_VIBRATION);
    CHECK(trigger_for(PADBIND_ROLE_BRAKE,1,1,0,slip,.2f).c==.3f);
    CHECK(trigger_for(PADBIND_ROLE_SHIFT,1,1,0,slip,.2f).mode==T_WEAPON);
    // Actual IFeedback::Speed -> curve reader -> End -> padfx output, including missing surfaces.
    clear_bindings(); bind(A_GAS,T_ROT_HI,2); bind(A_BRAKE,T_AXIS_HI,2);
    Cpu cpu={0}; cpu.esp=0x3000;
    fb_begin_frame(&cpu); M32(cpu.esp+4)=1; M32(cpu.esp+8)=0x7500; MF32(cpu.esp+12)=30;
    fb_speed(&cpu); fb_end(&cpu);
    float speed,surface; feedback_road_state(&speed,&surface);
    CHECK(speed==30 && fabsf(surface-.55f)<.001f && attr_calls==4);
    active(a,b); CHECK(a.extendedGamepad.rightTrigger.strengths.values[0]==.2f);
    road_sw=0; padfx_stop(); active(a,b); CHECK(a.extendedGamepad.rightTrigger.strengths.values[0]==.1f);
    road_sw=1; road_speed_pub=6000; road_surface_pub=10; padfx_stop();
    if (broken==3) road_sw=0;
    active(a,b); CHECK(a.extendedGamepad.rightTrigger.strengths.values[0]==.3f);
    CHECK(a.extendedGamepad.leftTrigger.strengths.values[0]==.25f && a.extendedGamepad.leftTrigger.strengths.values[9]==.9f);
    trig_sw=0; padfx_stop(); active(a,b); CHECK(a.extendedGamepad.rightTrigger.mode==T_OFF); trig_sw=1;
    road_speed_pub=-100; road_surface_pub=-1; padfx_stop(); active(a,b); CHECK(a.extendedGamepad.rightTrigger.strengths.values[0]==.1f);
    fb_begin_frame(&cpu); M32(cpu.esp+8)=0; MF32(cpu.esp+12)=NAN; fb_speed(&cpu); fb_end(&cpu);
    feedback_road_state(&speed,&surface); CHECK(speed==0 && surface==0);
    // Every hold stops at once, stays stopped across ticks, and requires a new frame after resume.
    for (unsigned reason=FEEDBACK_MENU; reason<=FEEDBACK_APP; reason<<=1) {
        active(a,b); live(a,b);
        if (broken==1) padfx_suppress(reason,0); else feedback_suppress(reason,1);
        restored(a,b,ca,cb);
        update(a,b); drain(); restored(a,b,ca,cb);
        frame(); update(a,b); drain(); restored(a,b,ca,cb);
        feedback_suppress(reason,0); update(a,b); drain(); restored(a,b,ca,cb);
        active(a,b); live(a,b);
    }
    feedback_suppress(FEEDBACK_MENU,1); feedback_suppress(FEEDBACK_FOCUS,1); feedback_suppress(FEEDBACK_MENU,0);
    active(a,b); restored(a,b,ca,cb); feedback_suppress(FEEDBACK_FOCUS,0);
    active(a,b); M32(0x102c)=4; feedback_poll_pause(); restored(a,b,ca,cb);
    M32(0x102c)=3; feedback_poll_pause(); update(a,b); drain(); restored(a,b,ca,cb);
    // A pending nonzero frame must be cancelled; delayed frames also expire at the sink.
    active(a,b); padfx_stop(); frame(); update(a,b); // queue nonzero output
    if (broken==2) drain(); else padfx_stop();
    float stop_slip[2],stop_hz[2]; int stop_nos;
    feedback_trigger_state(stop_slip,stop_hz,&stop_nos);
    feedback_road_state(&speed,&surface);
    CHECK(speed==0 && surface==0 && stop_slip[0]==0 && stop_slip[1]==0 && stop_nos==0);
    int at_stop=fake_writes; drain(); CHECK(fake_writes==at_stop); restored(a,b,ca,cb);
    active(a,b); clock_ns+=300000000ull; feedback_poll_pause(); restored(a,b,ca,cb);
    update(a,b); drain(); restored(a,b,ca,cb);
    pursuit_present=0; padfx_stop();
    frame(); update(a,b); clock_ns+=300000000ull; drain(); restored(a,b,ca,cb);
    active(a,b); live(a,b); // same request after sink expiry must be re-sent
    padfx_stop(); update(a,b); drain(); restored(a,b,ca,cb);
    padfx_stop(); stop_during_guest=1; frame(); update(a,b); drain(); restored(a,b,ca,cb);
    CHECK(gs.tick_ns==0); // an in-flight game tick cannot undo a newer stop
    // A new epoch may queue before the cancelled epoch drains; only its owner receives output.
    padfx_stop(); frame(); update(a,b); padfx_stop(); frame(); update(c,b); drain();
    CHECK(a.extendedGamepad.leftTrigger.mode==T_OFF && a.light.color==ca && a.playerIndex==3);
    CHECK(c.extendedGamepad.leftTrigger.mode!=T_OFF && c.playerIndex==GCControllerPlayerIndex1);
    padfx_stop(); CHECK(c.light.color==cc && c.playerIndex==-1);
    // An off-thread stop can be delayed before enqueue while a fresh main-thread frame is delivered.
    active(a,b); on_main=NO;
    before_enqueue=^{ on_main=YES; frame(); update(a,b); drain(); };
    padfx_stop(); drain(); live(a,b); CHECK(before_enqueue==nil);
    // Replacement/reconnect and LED slot swap restore each controller's own previous index.
    active(a,b); update(c,b); drain(); CHECK(a.light.color==ca && a.extendedGamepad.leftTrigger.mode==T_OFF);
    CHECK(a.playerIndex==3 && c.playerIndex==GCControllerPlayerIndex1);
    update(b,c); drain(); CHECK(b.playerIndex==GCControllerPlayerIndex1 && c.playerIndex==GCControllerPlayerIndex2);
    padfx_disconnect((__bridge void *)b); drain(); padfx_connect((__bridge void *)b);
    CHECK(b.playerIndex==2 && c.playerIndex==-1 && c.light.color==cc);
    update(a,b); drain(); restored(a,b,ca,cb); active(a,b); live(a,b);
    // A disconnected sink rejects every write and retains its last physical state.
    detach(a,YES); padfx_disconnect((__bridge void *)a); drain();
    CHECK(a.extendedGamepad.leftTrigger.mode!=T_OFF && a.light.color!=ca && a.playerIndex==GCControllerPlayerIndex1);
    detach(a,NO); padfx_connect((__bridge void *)a);
    restored(a,b,ca,cb); // reconnect alone must settle debt, before a new pad read or frame
    update(a,b); drain(); // reconnect without a new game frame
    restored(a,b,ca,cb);
    active(a,b); live(a,b);
    padfx_stop(); restored(a,b,ca,cb); // original snapshots must survive another ownership cycle
    active(a,b); detach(a,YES); on_main=NO;
    padfx_disconnect((__bridge void *)a); // main queue is held beyond the disconnect's wait
    on_main=YES; detach(a,NO); padfx_connect((__bridge void *)a);
    CHECK(a.extendedGamepad.leftTrigger.mode==T_OFF && a.extendedGamepad.rightTrigger.mode==T_OFF);
    CHECK(a.light.color==ca && a.playerIndex==3); // settle before the delayed release drains
    drain(); restored(a,b,ca,cb);
    active(a,b); live(a,b);
    padfx_stop(); restored(a,b,ca,cb);
    padfx_set_players(0); drain(); restored(a,b,ca,cb); active(a,b); CHECK(a.playerIndex==3 && b.playerIndex==2);
    padfx_set_players(1); active(a,b); padfx_set_light(0); drain(); restored(a,b,ca,cb);
    active(a,b); CHECK(a.light.color==ca && a.playerIndex==GCControllerPlayerIndex1);
    padfx_set_light(1); active(a,b); padfx_set_triggers(0); drain(); restored(a,b,ca,cb);
    padfx_set_triggers(1); active(a,b); padfx_set_road(0); drain(); restored(a,b,ca,cb);
    // Simulated/no-controller output clears ownership and never discovers a real pad.
    padfx_stop(); frame(); padfx_update(NULL,1,1,0); padfx_player_update(0,NULL); padfx_player_update(1,NULL); drain();
    if (broken==5) { (void)GCController.controllers; GCController *forbidden=[GCController new]; forbidden.playerIndex=0; }
    CHECK(discovery_calls==0 && real_opens==0 && real_writes==0);
    active(a,b); frame(); update(a,b); padfx_close(); drain(); restored(a,b,ca,cb);
    at_stop=fake_writes; active(a,b); CHECK(fake_writes==at_stop);
    CHECK(real_opens==0 && real_writes==0 && !strcmp(getenv("NFS_RUMBLE"),"off"));
    printf("padfx_test: %s %d checks, %d failures, %d fake writes, %d forbidden discovery / %d forbidden output calls (broken=%d)\n",
        failures?"FAIL":"PASS",checks,failures,fake_writes,real_opens,real_writes,broken);
    free(G); return failures!=0;
} }
