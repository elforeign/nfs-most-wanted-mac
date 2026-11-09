// Shared fake framework, clock and binding data; never opens a controller.
#import <Foundation/Foundation.h>
#include "host.h"
#include <math.h>
#include <stdarg.h>
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
#define PADLINK_HOOK_TEST
#include "padlink_test.c"
void diag_event_ring(const char *fmt, ...) { (void)fmt; }
#ifndef TRIGOPT_TEST
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
#endif
void f_006EE490_orig(Cpu *c) { (void)c; }
void f_006EE400_orig(Cpu *c) { c->eax=1; }
// Fake binding table plumbing; the role function itself is extracted unchanged at build time.
enum { T_BUTTON=2, T_AXIS_HI=5, T_ROT_HI=7, P_L2=10, P_R2=11, A_GAS=0, A_BRAKE=1, A_SHIFTDOWN=7, A_SHIFTUP=8, GA_PAGE=19 };
static uint32_t rec(int a,int slot) { return 0x91F418u+0x34u*(uint32_t)a+4+12u*(uint32_t)slot; }
static uint32_t joy(void) { return M32(0x91F150); }
static char *joy_name(uint32_t dev) { return (char *)G+dev+0x114; }
static int name_ps(const char *name) { return !strcmp(name,"Controller (DualSense)"); }
int padbind_input_set(void) { return PADBIND_DEFAULT; }
int padbind_effective_binding(int set,int a,uint32_t out[6]) {
    (void)set; memcpy(out,G+rec(a,2),24); return 1;
}
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
