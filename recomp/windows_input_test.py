"""Synthetic Windows USER32/DInput lifecycle proof, discovered by offline_tests.sh.

Original dispatcher: CreateWindow/PeekMessage/DInput unsupported 0/3, exit 2 each
-> registered 3/3, exit 0. Focus-release mutation: held key 128 -> 0 fixed;
the missing-release run exits 1. Bad callback cleanup exits 2 (fixed exit 0).
Desktop wrapper: original native host link has no user32.lib (0/1); fixed 1/1.
Queue overflow: original terminates on native input after 256 guest posts (exit 2); fixed drops
517 excess posts, drains 256 FIFO messages and accepts new input (exit 0).
Mac fake windows exercise production dispatch/COM/input without desktop access.
"""
from pathlib import Path
import hashlib
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
WIN = ROOT/'runtime/win'

ORIGINAL_DISPATCH = 'void host_import(Cpu *c, uint32_t slot) {\n    const struct ImportSlot *entry=win_import_find(speed_imports, speed_import_count, slot);\n    if (!entry) win_fail("unknown IAT slot", slot);\n    if (win_first_import) win_import_stop(c, entry);\n    gil_maybe_yield(c);\n    for (unsigned i=0; i<import_count; ++i) if (!strcmp(imports[i].name,entry->name)) {\n        uint32_t before=c->esp;\n        if (!win_vm_access(before,4)) win_fail(entry->name,before);\n        uint32_t caller=M32(before);\n        adapter_call(c,imports+i);\n        win_startup_checkpoint(c,entry,caller,before);\n        return;\n    }\n    // Generated import names are DLL!name; ordinal-only names remain stubs.\n    const char *fn=entry->name;\n    for (const char *p=fn; *p; ++p) if (*p==\'!\' || *p==\':\') fn=p+1;\n    // Only these DLLs own the tiny supported kernel/time surface.\n    if ((!__builtin_strncmp(entry->name, "KERNEL32.dll!", 13) ||\n         !__builtin_strncmp(entry->name, "WINMM.dll!", 10)) && win_kernel_import(c, fn)) return;\n    win_import_stop(c, entry);\n}\n'
ORIGINAL_SHA = 'a6bedaf4afe64b08ce4de45125f71f8fb88b1426ebfecc3ce034c3ea8ecdad7d'

ORIGINAL_NATIVE_POST = '    if (!post(GUEST_WINDOW,m,(uint32_t)a,(uint32_t)b)) win_fail("guest message queue full",m);'
ORIGINAL_QUIT_POST = '        if (msg.message==0x12) { if (!post(0,0x12,(uint32_t)msg.wParam,0)) win_fail("quit queue full",0); }'
ORIGINAL_QUEUE_SHA = '061936253850a4243aa57952366a10f993981e40304b7e0ae40f5a96d0e1f65a'

FIXTURE = r'''#include "fake_win.inc"
#include "user32.h"
#include "input.h"
#include "dinput.h"
#include <stdarg.h>
static LRESULT (*wndproc)(HWND,UINT,WPARAM,LPARAM);
static MSG native_messages[1024];
static unsigned native_count,native_destroyed;
static int fake_raw;
static RAWINPUT fake_raw_event;
static HWND fake_window=(HWND)(uintptr_t)123;
BOOL RegisterRawInputDevices(const RAWINPUTDEVICE *d,UINT n,UINT size) { (void)d;(void)n;(void)size;return fake_raw; }
UINT GetRawInputData(HANDLE h,UINT command,void *out,UINT *n,UINT header) { (void)h;(void)command;(void)n;(void)header;*(RAWINPUT *)out=fake_raw_event;return sizeof fake_raw_event; }
HINSTANCE GetModuleHandleA(const char *s) { (void)s;return (HINSTANCE)(uintptr_t)1; }
HCURSOR LoadCursorA(HINSTANCE h,const char *p) { (void)h;(void)p;return (HCURSOR)(uintptr_t)1; }
int GetKeyNameTextA(int32_t scan,char *out,int n) { (void)scan;if(n<2)return 0;out[0]='K';out[1]=0;return 1; }
unsigned short RegisterClassA(const WNDCLASSA *w) { wndproc=w->lpfnWndProc;return 1; }
HWND CreateWindowExA(DWORD ex,const char *cls,const char *title,DWORD style,int x,int y,int w,int h,HWND parent,void *menu,HINSTANCE inst,void *param) {
    (void)ex;(void)cls;(void)title;(void)style;(void)x;(void)y;(void)w;(void)h;(void)parent;(void)menu;(void)inst;(void)param;return fake_window;
}
LRESULT DefWindowProcA(HWND h,UINT m,WPARAM a,LPARAM b) { (void)h;(void)m;(void)a;(void)b;return 0; }
BOOL DestroyWindow(HWND h) { (void)h;++native_destroyed;return 1; }
BOOL ShowWindow(HWND h,int mode) { (void)h;(void)mode;return 1; }
BOOL PeekMessageA(MSG *m,HWND h,UINT lo,UINT hi,UINT flags) { (void)h;(void)lo;(void)hi;(void)flags;if (!native_count) return 0;*m=native_messages[0];for(unsigned i=1;i<native_count;++i)native_messages[i-1]=native_messages[i];--native_count;return 1; }
BOOL TranslateMessage(const MSG *m) { (void)m;return 1; }
LRESULT DispatchMessageA(const MSG *m) { return wndproc(m->hwnd,m->message,m->wParam,m->lParam); }
BOOL ValidateRect(HWND h,const RECT *r) { (void)h;(void)r;return 1; }
BOOL GetClientRect(HWND h,RECT *r) { (void)h;*r=(RECT){0,0,640,480};return 1; }
BOOL GetWindowRect(HWND h,RECT *r) { return GetClientRect(h,r); }
BOOL SetWindowPos(HWND a,HWND b,int x,int y,int w,int h,UINT f) { (void)a;(void)b;(void)x;(void)y;(void)w;(void)h;(void)f;return 1; }
BOOL AdjustWindowRect(RECT *r,DWORD s,BOOL m) { (void)r;(void)s;(void)m;return 1; }
HWND SetFocus(HWND h) { return h; }
BOOL SetForegroundWindow(HWND h) { (void)h;return 1; }
HWND SetCapture(HWND h) { return h; }
BOOL ReleaseCapture(void) { return 1; }
BOOL GetCursorPos(POINT *p) { *p=(POINT){7,8};return 1; }
int ShowCursor(BOOL b) { return b; }
UINT MapVirtualKeyA(UINT a,UINT b) { if(a==65)return 30;if(a==0xa3)return b==4?0xe01d:0x1d;return a; }
enum { REGISTER,CREATE,PEEK,GET,DISPATCH,POST,DEFPROC,DESTROY,QUIT,SETFOCUS,INPUT,TRANSLATE,SETLONG,RECTANGLE,KEYSTATE };
const struct ImportSlot speed_imports[]={
    {0x401000,"USER32.dll!RegisterClassExA"},{0x401004,"USER32.dll!CreateWindowExA"},
    {0x401008,"USER32.dll!PeekMessageA"},{0x40100c,"USER32.dll!GetMessageA"},
    {0x401010,"USER32.dll!DispatchMessageA"},{0x401014,"USER32.dll!PostMessageA"},
    {0x401018,"USER32.dll!DefWindowProcA"},{0x40101c,"USER32.dll!DestroyWindow"},
    {0x401020,"USER32.dll!PostQuitMessage"},{0x401024,"USER32.dll!SetFocus"},
    {0x401028,"DINPUT8.dll!DirectInput8Create"},{0x40102c,"USER32.dll!TranslateMessage"},
    {0x401030,"USER32.dll!SetWindowLongA"},{0x401034,"USER32.dll!GetClientRect"},
    {0x401038,"USER32.dll!GetKeyState"}
};
const unsigned speed_import_count=15,guest_func_count=3;
const uint32_t speed_entry=0x500000,guest_func_addr[]={0x500000,0x500100,0x500200};
static unsigned calls,callbacks,enumerated,checks;
static int bad_callback,stop_enum,reject_create;
static uint32_t hwnd;
static _Noreturn void failed(unsigned line) { printf("failed line %u, checks=%u\n",line,checks);exit(1); }
#define REQUIRE(x) do { ++checks;if (!(x)) failed(__LINE__); } while(0)
static uint32_t invoke(Cpu *c,uint32_t target,unsigned n,va_list args) {
    uint32_t before=c->esp;c->esp-=128;uint32_t sp=c->esp;M32(sp)=0xdeadbeef;
    for(unsigned i=0;i<n;++i)M32(sp+4+4*i)=va_arg(args,uint32_t);
    c->ebx=0x13579bdf;c->esi=0x2468ace0;c->edi=0x98765432;c->ebp=0x12345678;
    dispatch(c,target);++calls;
    REQUIRE(c->esp==sp+4+4*n && c->ebx==0x13579bdf && c->esi==0x2468ace0 && c->edi==0x98765432 && c->ebp==0x12345678);
    uint32_t result=c->eax;c->esp=before;return result;
}
static uint32_t call(Cpu *c,unsigned i,unsigned n,...) { va_list ap;va_start(ap,n);uint32_t v=invoke(c,WIN_THUNK_BASE+4*i,n,ap);va_end(ap);return v; }
static uint32_t method(Cpu *c,uint32_t object,unsigned slot,unsigned n,...) { va_list ap;va_start(ap,n);uint32_t v=invoke(c,M32(M32(object)+4*slot),n,ap);va_end(ap);return v; }
static void guest_window(Cpu *c) {
    uint32_t h=win_arg(c,0),m=win_arg(c,1),a=win_arg(c,2),b=win_arg(c,3);++callbacks;
    REQUIRE(h==0x110001);
    if (m==0x81 || m==1) { REQUIRE(win_vm_access(b,48) && M32(b)==0x76543210 && M32(b+20)==640 && M32(b+16)==480); }
    if (m==2) call(c,QUIT,1,19u);
    if (m==1 && reject_create) c->eax=UINT32_MAX;
    else if (m==0x400) { REQUIRE(a==11 && b==22);c->eax=123; }
    else c->eax=call(c,DEFPROC,4,h,m,a,b);
    c->esp+=bad_callback?16:20;
}
static void guest_enum(Cpu *c) {
    REQUIRE(win_arg(c,1)==0x1234);uint32_t p=win_arg(c,0);REQUIRE(M32(p)==580 || M32(p)==316);++enumerated;c->eax=!stop_enum;c->esp+=12;
}
static void producer(Cpu *c) {
    REQUIRE(call(c,POST,4,hwnd,0x400u,11u,22u)==1);c->eax=77;c->esp+=8;
}
void (*const guest_func_ptr[])(Cpu *)={guest_window,guest_enum,producer};
static void inject(unsigned m,uint32_t a,uint32_t b) { REQUIRE(native_count<1024);native_messages[native_count++]=(MSG){.hwnd=fake_window,.message=m,.wParam=a,.lParam=b}; }
static void guid_at(uint32_t p,uint32_t a,uint32_t b,uint32_t c,uint32_t d) { M32(p)=a;M32(p+4)=b;M32(p+8)=c;M32(p+12)=d; }
static uint32_t device(Cpu *c,uint32_t root,uint32_t id) {
    guid_at(0x18100,id,0x11cfd5a0,0x4544c7bf,0x5453);
    REQUIRE(method(c,root,3,4,root,0x18100u,0x18200u,0u)==0);return M32(0x18200);
}
static void buffer(Cpu *c,uint32_t d,unsigned size) {
    M32(0x18300)=20;M32(0x18304)=16;M32(0x18308)=0;M32(0x1830c)=0;M32(0x18310)=size;
    REQUIRE(method(c,d,6,3,d,1u,0x18300u)==0);
}
static uint32_t read_data(Cpu *c,uint32_t d,unsigned cap,unsigned peek) { M32(0x18400)=cap;return method(c,d,10,5,d,20u,0x19000u,0x18400u,peek); }
static void read_state(Cpu *c,uint32_t d,unsigned size) { REQUIRE(method(c,d,9,3,d,(uint32_t)size,0x1b000u)==0); }
int main(int argc,char **argv) {
    REQUIRE(win_vm_init() && win_vm_commit(0x10000,0x30000));
    Cpu c;win_cpu_init(&c,0x10000,0x30000,0x40000,1);win_cpu=&c;threads_init();win_first_import=0;
    if(argc>1 && argv[1][0]>='0' && argv[1][0]<='2') { unsigned i=argv[1][0]-'0';call(&c,i==0?CREATE:i==1?PEEK:INPUT,i==0?12:i==1?5:5,0u,0u,0u,0u,0u,0u,0u,0u,0u,0u,0u,0u);return 1; }
    if(argc>1 && !strcmp(argv[1],"first")) { win_first_import=1;call(&c,CREATE,12,0u,0u,0u,0u,0u,0u,0u,0u,0u,0u,0u,0u);return 1; }
    reject_create=argc>1 && !strcmp(argv[1],"reject-create");
    fake_raw=argc>1 && !strcmp(argv[1],"raw");
    bad_callback=argc>1 && !strcmp(argv[1],"bad-callback");
    REQUIRE(win_user32_register() && win_dinput_register());
    M32(0x17000)=48;M32(0x17008)=0x500000;M32(0x17028)=0x17100;M8(0x17100)='G';M8(0x17101)=0;
    REQUIRE(call(&c,REGISTER,1,0x17000u)==1);
    hwnd=call(&c,CREATE,12,0u,0x17100u,0x17100u,0u,0u,0u,640u,480u,0u,0u,0u,0x76543210u);
    if(reject_create) {
        REQUIRE(!hwnd && callbacks==4 && native_destroyed==1);
        gil_release();win_vm_destroy();printf("rejected WM_CREATE: guest/native teardown complete\n");return 0;
    }
    REQUIRE(hwnd==0x110001 && callbacks==2);
    if(argc>1 && !strcmp(argv[1],"flood")) {
        win_input_focus(1);
        for(unsigned i=0;i<256;++i) REQUIRE(call(&c,POST,4,hwnd,0x400u,i,22u)==1);
        REQUIRE(call(&c,POST,4,hwnd,0x400u,999u,22u)==0 && M32(c.fs_base+0x34)==1816);
        for(unsigned i=0;i<512;++i) inject(i&1?0x101:0x100,65u,30u<<16);
        inject(0xf,0,0); inject(0x200,0,0); inject(0x102,65,0); inject(5,0,0);
        win_user32_pump();
        REQUIRE(win_user32_dropped_messages()==517);
        REQUIRE(!win_input_key_state(30));
        for(unsigned i=0;i<256;++i) {
            REQUIRE(call(&c,PEEK,5,0x17200u,0u,0u,0u,1u)==1);
            REQUIRE(M32(0x17204)==0x400 && M32(0x17208)==i);
        }
        REQUIRE(!call(&c,PEEK,5,0x17200u,0u,0u,0u,1u));
        inject(0x100,65u,30u<<16); win_user32_pump();
        REQUIRE(win_input_key_state(30)==128);
        REQUIRE(call(&c,PEEK,5,0x17200u,0u,0u,0u,1u)==1 && M32(0x17204)==0x100);
        printf("queue overflow: 517 dropped, 256 FIFO, native state and recovery pass\n");
        gil_release();win_vm_destroy();return 0;
    }
    REQUIRE(call(&c,RECTANGLE,2,hwnd,0x17200u)==1 && M32(0x17208)==640);
    REQUIRE(call(&c,POST,4,hwnd,0x400u,11u,22u)==1);
    REQUIRE(call(&c,PEEK,5,0x17200u,0u,0u,0u,0u)==1 && M32(0x17204)==0x400);
    REQUIRE(call(&c,PEEK,5,0x17200u,0u,0x401u,0x401u,1u)==0);
    REQUIRE(call(&c,GET,4,0x17200u,0u,0u,0u)==1);
    REQUIRE(call(&c,DISPATCH,1,0x17200u)==123);
    REQUIRE(call(&c,PEEK,5,0x17200u,0u,0u,0u,1u)==0);
    uint32_t tid=0,producer_handle=win_thread_create(0,0x500200,0,0,&tid),result=0;
    REQUIRE(producer_handle && call(&c,GET,4,0x17200u,0u,0u,0u)==1 && M32(0x17204)==0x400);
    REQUIRE(win_thread_join(producer_handle,&result) && result==77);
    REQUIRE(call(&c,DISPATCH,1,0x17200u)==123);
    REQUIRE(call(&c,GET,4,0u,0u,0u,0u)==UINT32_MAX);
    guid_at(0x18000,0xbf798030,0x4da2483a,0x645d99aa,0x009736ed);
    REQUIRE(call(&c,INPUT,5,0u,0x800u,0x18000u,0x18200u,0u)==0);uint32_t root=M32(0x18200);
    REQUIRE(method(&c,root,4,5,root,0u,0x500100u,0x1234u,0u)==0 && enumerated==4);
    stop_enum=1;enumerated=0;REQUIRE(method(&c,root,4,5,root,0u,0x500100u,0x1234u,0u)==0 && enumerated==1);stop_enum=0;
    uint32_t kb=device(&c,root,0x6f1d2b61),mouse=device(&c,root,0x6f1d2b60);
    uint32_t pad0=device(&c,root,0x6f1d2b70),pad1=device(&c,root,0x6f1d2b71);
    REQUIRE(method(&c,mouse,7,1,mouse)==0 && read_data(&c,mouse,4,0)==0x80040207u && method(&c,mouse,8,1,mouse)==0);
    buffer(&c,kb,4);buffer(&c,mouse,8);
    REQUIRE(method(&c,kb,9,3,kb,256u,0x1b000u)==0x8007000cu);
    // Public COM method arities, including unsupported slots, must still pop exactly.
    const unsigned unsupported[][2]={{12,2},{16,3},{17,4},{18,5},{19,4},{20,3},{21,2},{22,2},{23,4},{24,2},{26,5},{27,5},{28,5},{29,4},{30,4},{31,2}};
    for(unsigned i=0;i<sizeof unsupported/sizeof *unsupported;++i)
        REQUIRE(method(&c,kb,unsupported[i][0],unsupported[i][1],kb,0u,0u,0u,0u)==0x80004001u);
    M32(0x18500)=24;M32(0x18504)=16;M32(0x18508)=2;M32(0x1850c)=256;M32(0x18510)=1;M32(0x18514)=0x18600;
    M32(0x18600)=0;M32(0x18604)=30;M32(0x18608)=12|(30u<<8);M32(0x1860c)=0;
    REQUIRE(method(&c,kb,11,2,kb,0x18500u)==0);
    M32(0x18604)=31;REQUIRE(method(&c,kb,11,2,kb,0x18500u)==0x80004001u);M32(0x18604)=30;
    M32(0x18508)=1;M32(0x1850c)=272;M32(0x18510)=2;
    M32(0x18604)=0;M32(0x18608)=2;M32(0x18614)=24;M32(0x18618)=0x80ffff02u;
    REQUIRE(method(&c,pad0,11,2,pad0,0x18500u)==0);M32(0x18508)=2;
    M32(0x18700)=316;REQUIRE(method(&c,kb,14,4,kb,0x18700u,30u,1u)==0 && M32(0x18714)==30 && M32(0x18708)==0x11cfd33c && M8(0x18720)=='K');
    enumerated=0;REQUIRE(method(&c,mouse,4,4,mouse,0x500100u,0x1234u,3u)==0 && enumerated==3);
    M32(0x18700)=44;REQUIRE(method(&c,pad0,3,2,pad0,0x18700u)==0 && M32(0x1870c)==6 && M32(0x18714)==1);
    M32(0x18800)=24;M32(0x18804)=16;M32(0x18808)=0;M32(0x1880c)=0;M32(0x18810)=(uint32_t)-1000;M32(0x18814)=1000;
    REQUIRE(method(&c,pad1,6,3,pad1,4u,0x18800u)==0);

    REQUIRE(method(&c,kb,7,1,kb)==0 && method(&c,mouse,7,1,mouse)==0);
    REQUIRE(method(&c,pad0,7,1,pad0)==0 && method(&c,pad1,7,1,pad1)==0);
    inject(7,0,0);inject(0x100,65,0x001e0000);read_state(&c,kb,256);REQUIRE(M8(0x1b01e)==128);
    REQUIRE(read_data(&c,kb,4,1)==0 && M32(0x18400)==1 && M32(0x19000)==30 && M32(0x19004)==128);
    REQUIRE(read_data(&c,kb,0,1)==0 && M32(0x18400)==0);
    uint32_t sequence=M32(0x1900c);REQUIRE(read_data(&c,kb,4,1)==0 && M32(0x1900c)==sequence);
    REQUIRE(read_data(&c,kb,4,0)==0 && M32(0x18400)==1);REQUIRE(read_data(&c,kb,4,0)==0 && M32(0x18400)==0);
    REQUIRE(call(&c,KEYSTATE,1,65u)==0xffff8000u);
    inject(0x100,0xa3u,0x011d0000u);REQUIRE(call(&c,KEYSTATE,1,0xa3u)==0xffff8000u);
    REQUIRE(call(&c,KEYSTATE,1,0x11u)==0xffff8000u);inject(0x101,0xa3u,0x011d0000u);
    REQUIRE(call(&c,KEYSTATE,1,0xa3u)==0 && read_data(&c,kb,4,0)==0 && M32(0x18400)==2);
    inject(0x200,0,10u|(10u<<16));inject(0x200,0,17u|(7u<<16));
    if(fake_raw) { fake_raw_event.header.dwType=0;fake_raw_event.data.mouse.lLastX=7;fake_raw_event.data.mouse.lLastY=-3;inject(0xff,0,123u); }
    inject(0x20a,120u<<16,0);inject(0x201,0,0);
    read_state(&c,mouse,20);REQUIRE(M32(0x1b000)==7 && (int32_t)M32(0x1b004)==-3 && M32(0x1b008)==120 && M8(0x1b00c)==128);
    REQUIRE(call(&c,KEYSTATE,1,1u)==0xffff8000u);
    read_state(&c,mouse,20);REQUIRE(!M32(0x1b000) && !M32(0x1b004) && !M32(0x1b008) && M8(0x1b00c)==128);
    REQUIRE(read_data(&c,mouse,8,0)==0 && M32(0x18400)==4 && M32(0x19000)==0 && M32(0x19004)==7);
    WinPadState ps={{111,222,333,444,555,666},9000,3};win_input_pad(0,1,&ps);
    read_state(&c,pad0,272);REQUIRE(M32(0x1b000)==111 && M32(0x1b014)==666 && M32(0x1b020)==9000 && M8(0x1b030)==128);
    read_state(&c,pad1,272);REQUIRE(M32(0x1b000)==0 && M32(0x1b008)==0 && M32(0x1b014)==0 && M32(0x1b020)==UINT32_MAX && !M8(0x1b030));
    REQUIRE(method(&c,mouse,8,1,mouse)==0);M32(0x1850c)=16;M32(0x18510)=0;
    REQUIRE(method(&c,mouse,11,2,mouse,0x18500u)==0 && method(&c,mouse,7,1,mouse)==0);
    read_state(&c,mouse,16);REQUIRE(M8(0x1b00c)==128);
    REQUIRE(method(&c,mouse,8,1,mouse)==0);M32(0x1850c)=20;
    REQUIRE(method(&c,mouse,11,2,mouse,0x18500u)==0 && method(&c,mouse,7,1,mouse)==0);
    win_input_motion(88,99,1);inject(8,0,0);read_state(&c,kb,256);printf("focus held=%u (expected 0)\n",M8(0x1b01e));REQUIRE(M8(0x1b01e)==0 && call(&c,KEYSTATE,1,65u)==0);
    read_state(&c,mouse,20);REQUIRE(!M32(0x1b000) && !M32(0x1b004) && !M8(0x1b00c));
    REQUIRE(read_data(&c,kb,4,0)==0 && M32(0x18400)==1 && M32(0x19004)==0);
    inject(0x100,65,0x001e0000);read_state(&c,kb,256);REQUIRE(!M8(0x1b01e));
    read_state(&c,pad0,272);REQUIRE(M32(0x1b000)==32768 && !M8(0x1b030));
    inject(7,0,0);win_user32_pump();win_input_key(30,1);win_input_button(0,1);win_input_motion(8,9,1);
    if(fake_raw) { inject(0xfe,2,123u);win_user32_pump(); }
    else { win_input_connect(WIN_KEYBOARD,0);win_input_connect(WIN_MOUSE,0); }
    win_input_pad(0,0,NULL);
    read_state(&c,kb,256);REQUIRE(!M8(0x1b01e));read_state(&c,mouse,20);REQUIRE(!M32(0x1b000) && !M8(0x1b00c));
    read_state(&c,pad0,272);REQUIRE(M32(0x1b000)==32768 && M32(0x1b020)==UINT32_MAX && !M8(0x1b030));
    win_input_connect(WIN_KEYBOARD,1);win_input_connect(WIN_MOUSE,1);
    for(unsigned i=0;i<10;++i)win_input_key(31,(i&1)==0);
    REQUIRE(read_data(&c,kb,10,1)==1 && M32(0x18400)==4);
    REQUIRE(read_data(&c,kb,10,0)==1 && M32(0x18400)==4);REQUIRE(read_data(&c,kb,10,0)==0 && !M32(0x18400));
    REQUIRE(method(&c,kb,9,3,kb,255u,0x1b000u)==0x80070057u);
    REQUIRE(method(&c,kb,9,3,kb,256u,0xfffffff0u)==0x80070057u);
    REQUIRE(method(&c,kb,10,5,kb,1u,0x19000u,0x18400u,0u)==0x80070057u);
    REQUIRE(method(&c,kb,8,1,kb)==0 && method(&c,kb,9,3,kb,256u,0x1b000u)==0x8007000cu);
    REQUIRE(method(&c,kb,2,1,kb)==0 && method(&c,kb,1,1,kb)==0);
    REQUIRE(call(&c,DESTROY,1,hwnd)==1 && native_destroyed==1);
    REQUIRE(call(&c,GET,4,0x17200u,0u,0u,0u)==0 && M32(0x17208)==19);
    printf("input fixture: checks=%u calls=%u callbacks=%u; registered=3/3; pad axes=6 slots=2\n",checks,calls,callbacks);
    gil_release();win_vm_destroy();return 0;
}
'''


def run(argv, code=0):
    result = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=120)
    assert result.returncode == code, (f'{argv[0]} exit {result.returncode}, expected {code}: '
                                      f'{result.stdout[-3000:]} {result.stderr[-3000:]}')
    return result.stdout


def main():
    compiler = shutil.which('clang')
    if not compiler:
        print('SKIP: no C compiler')
        return 3
    assert hashlib.sha256(ORIGINAL_DISPATCH.encode()).hexdigest() == ORIGINAL_SHA
    assert hashlib.sha256((ORIGINAL_NATIVE_POST + '\n' + ORIGINAL_QUIT_POST).encode()).hexdigest() == ORIGINAL_QUEUE_SHA
    wrapper = (ROOT/'recomp/win_build.bat').read_text()
    native_link = next(line for line in wrapper.splitlines() if 'link.exe @.metal-cache\\windows\\objects.rsp' in line)
    def owns_window_symbols(line):
        return 'user32.lib' in line.lower().split()
    assert not owns_window_symbols(native_link.replace('user32.lib', ''))
    assert owns_window_symbols(native_link)
    print('desktop link ownership: missing user32 0/1 -> fixed 1/1')
    with tempfile.TemporaryDirectory(prefix='windows-input-') as scratch:
        folder = Path(scratch)
        fake = (WIN/'fixture.h').read_text().split('const struct ImportSlot speed_imports[]')[0]
        (folder/'fake_win.inc').write_text(fake)
        fixture = folder/'fixture.c'
        fixture.write_text(FIXTURE)
        flags = [compiler, '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                 '-fsanitize=address,undefined', '-DNFS_WIN_TEST', '-pthread',
                 '-I', WIN, '-I', ROOT/'recomp', '-I', folder]
        units = [WIN/n for n in ('portable.c', 'memory.c', 'core.c', 'kernel.c',
                                  'imports.c', 'startup.c', 'threads.c', 'sync.c',
                                  'user32.c', 'input.c', 'dinput.c')]
        fixed = folder/'fixed'
        run([*flags, *units, fixture, '-o', fixed])
        print(run([fixed]).strip())
        assert '517 dropped, 256 FIFO' in run([fixed, 'flood'])
        queue_source = (WIN/'user32.c').read_text()
        native_post = '    post(GUEST_WINDOW,m,(uint32_t)a,(uint32_t)b);'
        quit_post = '        if (msg.message==0x12) post(0,0x12,(uint32_t)msg.wParam,0);'
        assert queue_source.count(native_post) == queue_source.count(quit_post) == 1
        queue_original = folder/'queue_original.c'
        queue_original.write_text(queue_source.replace(native_post,
            ORIGINAL_NATIVE_POST).replace(quit_post,
            ORIGINAL_QUIT_POST))
        queue_binary = folder/'queue_original'
        run([*flags, *(queue_original if p.name == 'user32.c' else p for p in units), fixture, '-o', queue_binary])
        assert 'guest message queue full' in run([queue_binary, 'flood'], 2)
        print('queue overflow: original fatal exit 2 -> fixed 517 drops / 256 FIFO / recovery exit 0')
        assert "registered=3/3" in run([fixed, "raw"])
        assert "teardown complete" in run([fixed, "reject-create"])
        print("raw mouse: no legacy double-count; native removal releases keys/buttons/motion")
        assert 'first import USER32.dll!CreateWindowExA' in run([fixed, 'first'], 10)
        assert 'guest callback stdcall' in run([fixed, 'bad-callback'], 2)
        old = folder/'original.c'
        source = (WIN/'imports.c').read_text()
        old.write_text(source[:source.index('void host_import(')] + ORIGINAL_DISPATCH)
        original = folder/'original'
        run([*flags, *(old if p.name == 'imports.c' else p for p in units), fixture, '-o', original])
        for index, name in enumerate(('CreateWindowExA', 'PeekMessageA', 'DirectInput8Create')):
            assert name in run([original, str(index)], 2)
        print('original: unsupported 0/3, exit 2 each -> registered 3/3, exit 0')
        text = (WIN/'input.c').read_text()
        assert text.count('if (!focused) { release(WIN_KEYBOARD); release(WIN_MOUSE); }') == 1
        mutant = folder/'missing_focus.c'
        mutant.write_text(text.replace('if (!focused) { release(WIN_KEYBOARD); release(WIN_MOUSE); }', ''))
        broken = folder/'missing_focus'
        run([*flags, *(mutant if p.name == 'input.c' else p for p in units), fixture, '-o', broken])
        assert 'focus held=128' in run([broken], 1)
        print('missing focus release: held=128 exit 1 -> held=0 exit 0; bad callback cleanup exit 2')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
