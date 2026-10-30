#!/usr/bin/env python3
"""














"""
from pathlib import Path
import os
import subprocess
import tempfile

R = Path(__file__).resolve().parent

FAKE = r'''
#import <Foundation/Foundation.h>
#include <math.h>
#include <stdlib.h>
#include <stdarg.h>
#include "host.h"
#include "onscreen_keyboard.h"
@interface GCControllerButtonInput : NSObject
@property float value;
@property BOOL pressed;
@property int preferredSystemGestureState;
@end
@implementation GCControllerButtonInput
@end
@interface GCControllerDirectionPad : NSObject
@property(strong) GCControllerButtonInput *up, *down, *left, *right, *xAxis, *yAxis;
@end
@implementation GCControllerDirectionPad
@end
@interface GCExtendedGamepad : NSObject
@property(strong) GCControllerDirectionPad *leftThumbstick, *rightThumbstick, *dpad;
@property(strong) GCControllerButtonInput *buttonA, *buttonB, *buttonX, *buttonY;
@property(strong) GCControllerButtonInput *leftShoulder, *rightShoulder, *buttonOptions, *buttonMenu;
@property(strong) GCControllerButtonInput *leftThumbstickButton, *rightThumbstickButton;
@property(strong) GCControllerButtonInput *leftTrigger, *rightTrigger, *buttonHome;
@end
@implementation GCExtendedGamepad
@end
@interface GCDualSenseGamepad : GCExtendedGamepad
@property(strong) GCControllerButtonInput *touchpadButton;
@end
@implementation GCDualSenseGamepad
@end
@interface GCDualShockGamepad : GCExtendedGamepad
@property(strong) GCControllerButtonInput *touchpadButton;
@end
@implementation GCDualShockGamepad
@end
@interface GCController : NSObject
@property(strong) GCExtendedGamepad *extendedGamepad;
@property(copy) NSString *vendorName, *productCategory;
@property(strong) GCController *physicalInputProfile;
@property(strong) NSDictionary *buttons;
+ (NSArray *)controllers;
@end
static NSArray *fake_source;
static int source_reads;
@implementation GCController
+ (NSArray *)controllers { ++source_reads; return fake_source ?: @[]; }
@end
static NSString *GCControllerDidConnectNotification = @"fake-connect";
static NSString *GCControllerDidDisconnectNotification = @"fake-disconnect";
static NSString *GCInputLeftPaddle = @"left-paddle", *GCInputRightPaddle = @"right-paddle";
enum { GCSystemGestureStateDisabled = 1 };
@interface FakeApplication : NSObject
@property BOOL isActive;
@end
@implementation FakeApplication
@end
static FakeApplication *NSApp;
uint8_t *G;
uint8_t di_keys[256], di_mouse_btn[8];
int di_mouse_dx, di_mouse_dy, di_mouse_dz, diag_on, boot_trace;
static int headless = 1, menu_calls, key_calls, rumble_calls, fx_calls, binding_calls;
static int prompts_seen, prompts_calls, menu_hide;
const char *diag_dir = "";
static uint64_t fake_time = 1000000000ull;
uint64_t host_ticks(void) { return fake_time; }
void diag_event(const char *fmt, ...) { (void)fmt; }
void prompts_input(int pad) { prompts_calls += pad; }
void prompts_pad_seen(int on) { prompts_seen = on; }
int osk_visible(void) { return 0; }
int osk_pad(int port, uint32_t buttons, int up, int down, int left, int right,
            float lx, float ly, int connected, int xbox) {
    (void)port; (void)buttons; (void)up; (void)down; (void)left; (void)right;
    (void)lx; (void)ly; (void)connected; (void)xbox; return 0;
}
void rumble_update(void) { ++rumble_calls; }
void padfx_update(void *ctl, int ds, float lt, float rt) {
    (void)ctl; (void)ds; (void)lt; (void)rt; ++fx_calls;
}
static void *led_slots[2];
static int disconnect_calls, connect_calls, led_calls, real_opens, real_writes;
static void *last_connect;
void padfx_disconnect(void *ctl) { (void)ctl; ++disconnect_calls; }
void padfx_connect(void *ctl) { last_connect=ctl; ++connect_calls; }
void padfx_player_update(int slot, void *ctl) { if (slot<0 || slot>1) abort(); led_slots[slot]=ctl; ++led_calls; }
int settings_get(const char *name) { (void)name; return 0; }
uint32_t portmenu_pad(uint32_t phys, int u, int d, int l, int r, float lx, float ly,
                     int home, int touch, int has_touch, uint32_t *inject) {
    (void)phys; (void)u; (void)d; (void)l; (void)r; (void)lx; (void)ly;
    (void)home; (void)touch; (void)has_touch; ++menu_calls; *inject = 0;
    return menu_hide ? ~0u : 0;
}
int portmenu_is_open(void) { return menu_hide; }
static void di_event(uint8_t key, int down) { (void)key; (void)down; ++key_calls; }
static int script_layout = 0;
static int pad_layout_xbox(void) { return script_layout; }
int controltype_passthrough(void) { return 0; }
static int sim_kind = -1, script_trigger_pair;
static uint32_t script_pad2, script_raw2;
static char script_axis2[32];
static uint32_t script_pad, script_raw;
static char script_axis[64];
static float script_lt, script_rt;
static int kind_of_word(const char *w) {
    return !strcmp(w,"dualsense") ? PAD_KIND_DUALSENSE : !strcmp(w,"ds4") ? PAD_KIND_DUALSHOCK4 :
           !strcmp(w,"edge") ? PAD_KIND_EDGE : !strcmp(w,"xbox") ? PAD_KIND_OTHER : -1;
}
unsigned user32_pad_trigger_step(float *l, float *r) { *l = *r = 0; return 0; }
void padbind_trigger_sample(unsigned step, float l, float r, uint32_t z, uint32_t rz, int native) {
    (void)step; (void)l; (void)r; (void)z; (void)rz; (void)native;
}
void padbind_sync(Cpu *c) { (void)c; ++binding_calls; }
int user32_di_next(uint32_t *o, uint32_t *d, uint32_t *t, uint32_t *s) {
    (void)o; (void)d; (void)t; (void)s; return 0;
}
void user32_pump(void) {}
static uint32_t bump = 0x1000;
uint32_t galloc(uint32_t n) { uint32_t p = bump; bump += (n + 15) & ~15u; return p; }
uint32_t host_fn(HostFn fn, const char *name) { (void)fn; (void)name; return 1; }
static uint32_t guids[4];
static char names[4][64];
static int enumerated, stop_after;
uint32_t call_guest(Cpu *c, uint32_t cb, int n, const uint32_t *a) {
    (void)c; (void)cb; (void)n;
    if (enumerated >= 4) abort();
    guids[enumerated] = M32(a[0] + 4);
    snprintf(names[enumerated++], 64, "%s", (char *)G + a[0] + 40);
    return !stop_after || enumerated < stop_after;
}
#define GAMEFLOW_STATE 0x00925E90u
#define SIM_INSTANCE 0x009885E0u
enum { P_CROSS, P_CIRCLE, P_SQUARE, P_TRIANGLE, P_L1, P_R1, P_CREATE, P_OPTIONS,
       P_L3, P_R3, P_L2, P_R2, P_PS, P_TOUCHPAD, P_LPADDLE, P_RPADDLE };
'''

MAIN = r'''
static int checks, fails;
#define CHECK(x) do { ++checks; if (!(x)) { ++fails; printf("FAIL line %d: %s\n", __LINE__, #x); } } while (0)
static uint32_t invoke(HostFn f, int n, ...) {
    Cpu c = {0}; c.esp = 0x00E00000;
    va_list ap; va_start(ap, n);
    for (int i = 0; i < n; ++i) M32(c.esp + 4 + 4*i) = va_arg(ap, uint32_t);
    va_end(ap); f(&c); return c.eax;
}
static GCControllerButtonInput *button(float v) {
    GCControllerButtonInput *b = [GCControllerButtonInput new]; b.value = v; b.pressed = v > .9f; return b;
}
static GCController *controller(int n) {
    GCController *ctl = [GCController new];
    GCExtendedGamepad *g = n ? [GCExtendedGamepad new] : [GCDualShockGamepad new];
    ctl.extendedGamepad = g; ctl.vendorName = n ? @"Xbox" : @"DualShock 4";
    ctl.productCategory = ctl.vendorName;
    g.leftThumbstick = [GCControllerDirectionPad new]; g.rightThumbstick = [GCControllerDirectionPad new];
    g.dpad = [GCControllerDirectionPad new];
    g.leftThumbstick.xAxis = button(n ? .75f : -.5f); g.leftThumbstick.yAxis = button(n ? -.5f : .25f);
    g.leftTrigger = button(n ? .6f : .2f); g.rightTrigger = button(n ? .3f : .8f);
    g.buttonA = button(n ? 0 : 1); g.buttonB = button(n ? 1 : 0);
    g.dpad.right = button(n ? 1 : 0);
    return ctl;
}
static void notify(NSString *name, GCController *ctl) {
    [NSNotificationCenter.defaultCenter postNotificationName:name object:ctl];
}
static uint32_t make_device(int idx) {
    uint32_t guid = galloc(16), out = galloc(4); write_guid(guid, PAD_GUID + idx);
    CHECK(invoke(i_CreateDevice, 4, 0u, guid, out, 0u) == DI_OK);
    return M32(out);
}
static uint32_t state(uint32_t dev) {
    uint32_t out = galloc(272);
    CHECK(invoke(d_GetDeviceState, 3, dev, 272u, out) == DI_OK); return out;
}
int main(int argc, const char **argv) { @autoreleasepool {
    (void)argc; int count = atoi(argv[1]);
    G = calloc(1, 0x1000000);
    GCController *a = controller(0), *b = controller(1);
    fake_source = @[a, b];
    notify(GCControllerDidConnectNotification, a); notify(GCControllerDidConnectNotification, b);
    CHECK(connect_calls == 2 && last_connect == (__bridge void *)b);
    CHECK(pad_count() == count);
    CHECK(invoke(i_EnumDevices, 5, 0u, 4u, 1u, 0u, 0u) == DI_OK);
    CHECK(enumerated == count); CHECK(guids[0] == PAD_GUID);
    CHECK(!strcmp(names[0], "Controller (DualShock 4)"));
    if (count == 2) { CHECK(guids[1] == PAD_GUID + 1); CHECK(strcmp(names[0], names[1]) != 0); }
    uint32_t dev0 = make_device(0), dev1 = count == 2 ? make_device(1) : 0;
    CHECK(invoke(d_Acquire, 1, dev0) == DI_OK); CHECK(obj(dev0)->acquired);
    uint32_t p0 = state(dev0);
    CHECK(M32(p0) == 16384); CHECK(M32(p0+8) == 39321); CHECK(M32(p0+20) == 58982);
    CHECK(M8(p0+48) == 0x80); CHECK(M32(p0+32) == 0xFFFFFFFFu);
    uint32_t info = galloc(580), caps = galloc(44), oi = galloc(316);
    CHECK(invoke(d_GetDeviceInfo, 2, dev0, info) == DI_OK); CHECK(M32(info+4) == PAD_GUID);
    CHECK(invoke(d_GetCapabilities, 2, dev0, caps) == DI_OK); CHECK(M32(caps+16) == PAD_BUTTONS_PS);
    int before_reads = source_reads;
    PadState ps; CHECK(pad_read(&ps));
    CHECK(led_slots[0] == (__bridge void *)a);
    CHECK(source_reads - before_reads == (count == 1 ? 1 : 0)); // default adds no controller-list read
    int fallback_before = disconnect_calls;
    pad_presence(0, 0, NULL);
    CHECK(disconnect_calls == fallback_before + 1);
    pad_read(&ps);
    char name0[64], name1[64];
    CHECK(pad_identity(name0,64) == PAD_KIND_DUALSHOCK4);
    CHECK(pad_identity_n(0,name1,64) == PAD_KIND_DUALSHOCK4); CHECK(!strcmp(name0,name1));
    uint32_t guid = galloc(16), out = galloc(4); write_guid(guid, PAD_GUID+1);
    if (count == 1) {
        CHECK(invoke(i_CreateDevice,4,0u,guid,out,0u) == DIERR_DEVICENOTREG); CHECK(!M32(out));
        CHECK(invoke(i_GetDeviceStatus,2,0u,guid) != DI_OK);
    } else {
        CHECK(invoke(d_Acquire,1,dev1) == DI_OK); CHECK(obj(dev1)->acquired);
        CHECK(invoke(d_Unacquire,1,dev1) == DI_OK); CHECK(!obj(dev1)->acquired && obj(dev0)->acquired);
        int rb = rumble_calls, fx = fx_calls, pb = binding_calls, mk = menu_calls, kc = key_calls;
        int pc = prompts_calls, seen = prompts_seen;
        uint32_t p1 = state(dev1);
        CHECK(led_slots[0] == (__bridge void *)a && led_slots[1] == (__bridge void *)b);
        CHECK(M32(p1) == 57343); CHECK(M32(p1+8) == 52428); CHECK(M32(p1+20) == 42598);
        CHECK(M32(p1+32) == 9000); CHECK(M8(p1+48) == 0x80);
        CHECK(rumble_calls == rb && fx_calls == fx && binding_calls == pb);
        CHECK(menu_calls == mk && key_calls == kc && prompts_calls == pc && prompts_seen == seen);
        CHECK(invoke(d_GetDeviceInfo,2,dev1,info) == DI_OK); CHECK(M32(info+4) == PAD_GUID+1);
        CHECK(!strcmp((char *)G+info+40,"Controller (Xbox)"));
        CHECK(invoke(d_GetCapabilities,2,dev1,caps) == DI_OK); CHECK(M32(caps+16) == PAD_BUTTONS);
        CHECK(invoke(d_GetObjectInfo,4,dev1,oi,0u,1u) == DI_OK); CHECK(!strcmp((char *)G+oi+32,"X Axis"));
        CHECK(invoke(d_GetObjectInfo,4,dev0,oi,0u,1u) == DI_OK); CHECK(!strcmp((char *)G+oi+32,"Left Stick X"));
        menu_hide=1;
        int menus_before = menu_calls;
        CHECK(pad_read_n(1,&ps) && ps.connected && ps.buttons==0 && ps.pov==-1 && ps.lx==0 && ps.ly==0 && ps.lt==0 && ps.rt==0);
        CHECK(menu_calls==menus_before); // P2 cannot navigate or toggle the port menu
        menu_hide=0;
        // Reordering the framework list cannot swap identities or state.
        fake_source = @[b,a]; CHECK(M32(state(dev0)) == 16384); CHECK(M32(state(dev1)) == 57343);
        // Removing slot 0 leaves slot 1 where it was, even when it is now the only controller.
        int disconnect_before = disconnect_calls;
        fake_source = @[b]; notify(GCControllerDidDisconnectNotification,a);
        CHECK(disconnect_calls == disconnect_before + 1);
        CHECK(!pad_read_n(0,&ps)); CHECK(pad_read_n(1,&ps) && ps.lx == .75f);
        CHECK(led_slots[0] == NULL && led_slots[1] == (__bridge void *)b);
        CHECK(M32(state(dev0)) == 32768); CHECK(M32(state(dev1)) == 57343);
        CHECK(invoke(i_GetDeviceStatus,2,0u,guid) == DI_OK);
        write_guid(guid,PAD_GUID); CHECK(invoke(i_GetDeviceStatus,2,0u,guid) != DI_OK);
        CHECK(pad_identity_n(0,name0,64) == PAD_KIND_DUALSHOCK4); // last identity survives unplug
        int connect_before = connect_calls;
        fake_source = @[b,a]; notify(GCControllerDidConnectNotification,a);
        CHECK(connect_calls == connect_before + 1 && last_connect == (__bridge void *)a);
        CHECK(pad_read_n(0,&ps) && ps.lx == -.5f); CHECK(pad_read_n(1,&ps) && ps.lx == .75f);
        notify(GCControllerDidDisconnectNotification,b); CHECK(!pad_read_n(1,&ps));
        CHECK(M32(state(dev1)) == 32768); CHECK(invoke(i_GetDeviceStatus,2,0u,guid) == DI_OK);
        notify(GCControllerDidConnectNotification,b);
        // Enumeration must respect DIENUM_STOP after the first pad.
        enumerated=0; stop_after=1;
        invoke(i_EnumDevices,5,0u,4u,1u,0u,0u); CHECK(enumerated==1);
    }
    CHECK(!pad_read_n(-1,&ps) && ps.pov==-1); CHECK(!pad_read_n(2,&ps) && !ps.connected);
    // BOOT_PAD still overrides hardware, and cannot clone its script into slot 1.
    setenv("BOOT_PAD","sim:dualsense,lx=-0.25,lt=0.5,rt=1,buttons=1",1);
    CHECK(pad_read(&ps) && ps.native && ps.lx==-.25f && ps.lt==.5f && ps.rt==1 && ps.buttons==1);
    CHECK(!pad_read_n(1,&ps) && !ps.connected && ps.pov==-1);
    menu_hide=1; CHECK(pad_read(&ps) && ps.connected && ps.lx==0 && ps.buttons==0 && ps.lt==0 && ps.rt==0);
    menu_hide=0; setenv("BOOT_PAD","sim",1); CHECK(pad_read(&ps) && !ps.native && ps.connected);
    setenv("BOOT_PAD","sim:dualsense@999",1); CHECK(!pad_read(&ps) && !ps.connected);
    CHECK(real_opens == 0 && real_writes == 0 && !strcmp(getenv("NFS_RUMBLE"), "off"));
    printf("padtwo_test NFS_PADS=%s: %d checks, %d failures\n",getenv("NFS_PADS") ?: "unset", checks,fails);
    return fails ? 1 : 0;
} }
'''


def run(cmd, **kwargs):
    kwargs.setdefault('env', dict(os.environ, NFS_RUMBLE='off'))
    p = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if p.returncode:
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(map(str, cmd))}\n{p.stdout[-3000:]}{p.stderr[-5000:]}")
    return p


def main():
    user = (R / 'user32.m').read_text()
    # Compile the exact production layout, tracking, identity, simulation and sampling code.
    user = user[user.index('static uint32_t bit('):]
    di = (R / 'dinput.c').read_text()
    mutation = 'd->pad_idx = dev == DEV_PAD ? (int)(M32(arg(c, 1)) - PAD_GUID) : 0;'
    assert di.count(mutation) == 1
    with tempfile.TemporaryDirectory(prefix='padtwo_') as td:
        td = Path(td)
        for broken in (False, "index", "menu", "led", "disconnect", "connect"):
            sample = user if broken != "menu" else user.replace(' : (portmenu_is_open() ? ~0u : 0);', ' : 0;')
            assert broken != "menu" or sample != user
            if broken == "led":
                sample = user.replace("padfx_player_update(idx,", "padfx_player_update(0,")
                assert sample != user
            if broken == "disconnect":
                sample = user.replace("padfx_disconnect((__bridge void *)ctl);", "(void)ctl;").replace("if (!first) padfx_disconnect(NULL);", "(void)first;")
                assert sample != user
            if broken == "connect":
                sample = user.replace("padfx_connect((__bridge void *)n.object);", "(void)n;")
                assert sample != user
            src = td / (str(broken) + '.m')
            src.write_text(FAKE + sample + '\n' + (di.replace(mutation, 'd->pad_idx = 0;') if broken == 'index' else di) + MAIN)
            exe = src.with_suffix('')
            run(['clang', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fobjc-arc',
                 '-Wno-deprecated-declarations', '-fsanitize=address,undefined', '-I', str(R), '-I', str(R.parent / 'recomp'),
                 str(src), '-framework', 'Foundation', '-o', str(exe)])
            for setting in (['1', '2'] if broken == 'connect' else ['2'] if broken else [None, '1', '2', 'invalid']):
                env = dict(os.environ, NFS_RUMBLE='off')
                env.pop('BOOT_PAD', None)
                env.pop('BOOT_PAD2', None)
                env.pop('NFS_PADS', None)
                if setting is not None:
                    env['NFS_PADS'] = setting
                p = subprocess.run([exe, '2' if setting == '2' else '1'], env=env, capture_output=True, text=True)
                if broken:
                    if p.returncode != 1 or 'FAIL' not in p.stdout:
                        raise RuntimeError('index-0 negative variant was not rejected: ' + p.stdout + p.stderr[-2000:])
                    print('deliberately broken ' + str(broken) + ': ' + p.stdout.strip().splitlines()[-1])
                elif p.returncode:
                    raise RuntimeError(p.stdout + p.stderr[-3000:])
                else:
                    print(p.stdout.strip())
    print('padtwo_test: PASS (index-0, F10 bypass, LED alias, disconnect and reconnect negatives rejected)')


if __name__ == '__main__':
    main()
