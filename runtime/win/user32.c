#include "user32.h"
#include "input.h"
static uint32_t guest_window=0x110000u;
#define GUEST_WINDOW guest_window
#define QUEUE 256u
typedef struct { uint32_t hwnd,message,wparam,lparam,time; int32_t x,y; } GuestMessage;
static GuestMessage messages[QUEUE];
static unsigned count;
static uint64_t dropped_messages;
uint64_t win_user32_dropped_messages(void) { return dropped_messages; }
static HWND window;
static uint32_t procedure,class_procedure,class_atom,class_name,user_data,style;
static int creating,destroying,last_mouse,mouse_x,mouse_y,focused,raw_mouse;
static uint32_t owner;
static uint32_t error(Cpu *c,uint32_t code) { M32(c->fs_base+0x34)=code; return 0; }
static int string(uint32_t p) {
    if (!p) return 0;
    for (unsigned i=0;i<1024;++i) { if ((uint64_t)p+i>UINT32_MAX || !win_vm_access(p+i,1)) return 0; if (!M8(p+i)) return 1; }
    return 0;
}
uint32_t win_callback(Cpu *c,uint32_t fn,unsigned n,const uint32_t *args) {
    if (!fn || n>8 || c->esp<4*(n+1) || !win_vm_access(c->esp-4*(n+1),4*(n+1)))
        win_fail("guest callback stack",fn);
    Cpu copy=*c;
    copy.esp-=4*(n+1); M32(copy.esp)=0xfeedf00d;
    for (unsigned i=0;i<n;++i) M32(copy.esp+4+4*i)=args[i];
    dispatch(&copy,fn);
    if (copy.esp!=c->esp || copy.ebx!=c->ebx || copy.esi!=c->esi || copy.edi!=c->edi || copy.ebp!=c->ebp)
        win_fail("guest callback stdcall",fn);
    return copy.eax;
}
HWND win_user32_native(uint32_t h) { return h==GUEST_WINDOW?window:NULL; }
static uint32_t callback(Cpu *c,uint32_t m,uint32_t a,uint32_t b) {
    uint32_t args[4]={GUEST_WINDOW,m,a,b}; return win_callback(c,procedure,4,args);
}
static int post(uint32_t h,uint32_t m,uint32_t a,uint32_t b) {
    // Native pumping and DInput polling can run without a guest dequeue. Keep that backlog bounded.
    if (count==QUEUE) { ++dropped_messages; return 0; }
    messages[count++]=(GuestMessage){h,m,a,b,(uint32_t)(host_ticks()/1000000),0,0}; return 1;
}
static void focus(int active) { last_mouse=0; focused=active!=0; win_input_focus(active); }
static LRESULT CALLBACK native_proc(HWND h,UINT m,WPARAM a,LPARAM b) {
    // Native pointer-bearing creation/paint messages never enter guest memory.
    if (creating || destroying) return DefWindowProcA(h,m,a,b);
    switch (m) {
    case 6: focus((a&0xffff)!=0); b=(HWND)(intptr_t)b==window?(LPARAM)GUEST_WINDOW:0; break;
    case 0x1c: focus(a!=0); break; // WM_ACTIVATEAPP
    case 7: focus(1); a=(HWND)(uintptr_t)a==window?GUEST_WINDOW:0; break;
    case 8: focus(0); a=(HWND)(uintptr_t)a==window?GUEST_WINDOW:0; break;
    case 0x100: case 0x104: case 0x101: case 0x105: {
        unsigned scan=((uint32_t)b>>16)&127;
        if ((uint32_t)b&(1u<<24)) scan|=128;
        if (a==0x13) scan=0xc5; // Pause has an E1 prefix, unlike Num Lock.
        if (a==0x90) scan=0x45;
        win_input_key(scan,m==0x100 || m==0x104); break;
    }
    case 0xff: { // WM_INPUT is consumed natively; its handle must never reach the guest.
        RAWINPUT raw; UINT size=sizeof raw;
        if (raw_mouse && GetRawInputData((HANDLE)(intptr_t)b,0x10000003,&raw,&size,sizeof(RAWINPUTHEADER))==sizeof raw &&
            raw.header.dwType==0 && !(raw.data.mouse.usFlags&1))
            win_input_motion(raw.data.mouse.lLastX,raw.data.mouse.lLastY,0);
        return DefWindowProcA(h,m,a,b);
    }
    case 0xfe: // Removal invalidates held input; later events establish fresh state.
        if (a==2) {
            win_input_connect(WIN_KEYBOARD,0); win_input_connect(WIN_MOUSE,0);
            win_input_connect(WIN_KEYBOARD,1); win_input_connect(WIN_MOUSE,1);
        }
        return DefWindowProcA(h,m,a,b);
    case 0x200: {
        int x=(int16_t)b,y=(int16_t)((uint32_t)b>>16);
        if (!raw_mouse && last_mouse) win_input_motion(x-mouse_x,y-mouse_y,0);
        mouse_x=x; mouse_y=y; last_mouse=1; break;
    }
    case 0x201: case 0x202: win_input_button(0,m==0x201); break;
    case 0x204: case 0x205: win_input_button(1,m==0x204); break;
    case 0x207: case 0x208: win_input_button(2,m==0x207); break;
    case 0x20b: case 0x20c: win_input_button((((uint32_t)a>>16)&0xffff)==1?3:4,m==0x20b); break;
    case 0x20a: win_input_motion(0,0,(int16_t)((uint32_t)a>>16)); break;
    case 0xf: ValidateRect(h,NULL); break;
    case 0x10: case 5: case 0x102: case 0x106: break;
    default: return DefWindowProcA(h,m,a,b);
    }
    post(GUEST_WINDOW,m,(uint32_t)a,(uint32_t)b);
    return 0;
}
void win_user32_pump(void) {
    MSG msg;
    while (PeekMessageA(&msg,NULL,0,0,1)) {
        if (msg.message==0x12) post(0,0x12,(uint32_t)msg.wParam,0);
        else DispatchMessageA(&msg);
    }
}
static uint32_t register_class(Cpu *c) {
    uint32_t p=win_arg(c,0);
    if (!win_vm_access(p,48) || M32(p)!=48 || !M32(p+8) || !string(M32(p+40))) return error(c,87);
    if (class_atom) return error(c,1410);
    WNDCLASSA wc={0}; wc.lpfnWndProc=native_proc; wc.hInstance=GetModuleHandleA(NULL);
    wc.lpszClassName="TranslatedGuestWindow"; wc.hCursor=LoadCursorA(NULL,(const char *)(uintptr_t)32512);
    if (!RegisterClassA(&wc)) return error(c,GetLastError());
    class_procedure=M32(p+8); class_name=win_alloc(1024); if (!class_name) return error(c,8);
    for (unsigned i=0;i<1024;++i) { M8(class_name+i)=M8(M32(p+40)+i); if (!M8(class_name+i)) break; }
    class_atom=1; owner=M32(c->fs_base+0x24); return class_atom;
}
static uint32_t create(Cpu *c) {
    uint32_t cls=win_arg(c,1),title=win_arg(c,2);
    if (window || !class_atom || (cls!=class_atom && (!string(cls) || strcmp((char *)G+cls,(char *)G+class_name))) ||
        (title && !string(title)) || win_arg(c,8) || win_arg(c,9) || M32(c->fs_base+0x24)!=owner) return error(c,87);
    creating=1; style=win_arg(c,3);
    window=CreateWindowExA(win_arg(c,0),"TranslatedGuestWindow",title?(char *)G+title:"",style,
        (int)win_arg(c,4),(int)win_arg(c,5),(int)win_arg(c,6),(int)win_arg(c,7),NULL,NULL,GetModuleHandleA(NULL),NULL);
    creating=0;
    if (!window) return error(c,GetLastError());
    uint32_t p=win_alloc(48); if (!p) { DestroyWindow(window); window=NULL; return error(c,8); }
    if (guest_window==UINT32_MAX) win_fail("window handle exhaustion",0);
    ++guest_window; procedure=class_procedure;
    // CREATESTRUCTA contains guest addresses, including the original class/title.
    M32(p)=win_arg(c,11); M32(p+4)=win_arg(c,10); M32(p+8)=0; M32(p+12)=0;
    M32(p+16)=win_arg(c,7); M32(p+20)=win_arg(c,6); M32(p+24)=win_arg(c,5); M32(p+28)=win_arg(c,4);
    M32(p+32)=style; M32(p+36)=title; M32(p+40)=cls; M32(p+44)=win_arg(c,0);
    uint32_t accepted=callback(c,0x81,0,p);
    if (!accepted || callback(c,1,0,p)==UINT32_MAX) {
        destroying=1;
        if (accepted) callback(c,2,0,0);
        callback(c,0x82,0,0); DestroyWindow(window); destroying=0; window=NULL; focus(0); return 0;
    }
    RAWINPUTDEVICE devices[2]={{1,2,0x2000,window},{1,6,0x2000,window}};
    raw_mouse=RegisterRawInputDevices(devices,2,sizeof *devices)!=0;
    // ShowWindow/focus messages establish acquisition ownership after creation.
    return GUEST_WINDOW;
}
static uint32_t destroy(Cpu *c) {
    if (destroying || !win_user32_native(win_arg(c,0))) return error(c,1400);
    destroying=1; focus(0); callback(c,2,0,0); callback(c,0x82,0,0);
    if (raw_mouse) { RAWINPUTDEVICE devices[2]={{1,2,1,NULL},{1,6,1,NULL}}; RegisterRawInputDevices(devices,2,sizeof *devices); raw_mouse=0; }
    BOOL ok=DestroyWindow(window); destroying=0;
    window=NULL; user_data=0;
    for (unsigned i=0;i<count;) {
        if (messages[i].hwnd==GUEST_WINDOW) { for (unsigned j=i+1;j<count;++j) messages[j-1]=messages[j]; --count; }
        else ++i;
    }
    return ok!=0;
}
static uint32_t defproc(Cpu *c) {
    uint32_t m=win_arg(c,1);
    if (m==0x81) return 1;
    if (m==0x10) return destroy(c),0;
    if ((m>=0x100 && m<=0x109) || m==0x112) {
        HWND h=win_user32_native(win_arg(c,0));
        return h?(uint32_t)DefWindowProcA(h,m,win_arg(c,2),(int32_t)win_arg(c,3)):error(c,1400);
    }
    return 0;
}
static uint32_t get_message(Cpu *c,int wait) {
    uint32_t p=win_arg(c,0),h=win_arg(c,1),lo=win_arg(c,2),hi=win_arg(c,3);
    if (M32(c->fs_base+0x24)!=owner || !win_vm_access(p,28) || (h && h!=UINT32_MAX && !win_user32_native(h)) || (hi && lo>hi)) return error(c,87),wait?UINT32_MAX:0;
    for (;;) {
        win_user32_pump();
        for (unsigned i=0;i<count;++i) {
            GuestMessage msg=messages[i];
            if (msg.message!=0x12 && ((h==UINT32_MAX && msg.hwnd) || (h && h!=UINT32_MAX && msg.hwnd!=h) ||
                ((lo || hi) && (msg.message<lo || msg.message>hi)))) continue;
            M32(p)=msg.hwnd; M32(p+4)=msg.message; M32(p+8)=msg.wparam; M32(p+12)=msg.lparam;
            M32(p+16)=msg.time; M32(p+20)=(uint32_t)msg.x; M32(p+24)=(uint32_t)msg.y;
            if (wait || (win_arg(c,4)&1)) { for (unsigned j=i+1;j<count;++j) messages[j-1]=messages[j]; --count; }
            return wait?msg.message!=0x12:1;
        }
        if (!wait) return 0;
        win_guest_sleep(1); // releases the GIL; the UI owner continues pumping
    }
}
static uint32_t peek(Cpu *c) { return get_message(c,0); }
static uint32_t get(Cpu *c) { return get_message(c,1); }
static uint32_t dispatch_message(Cpu *c) {
    uint32_t p=win_arg(c,0);
    if (!win_vm_access(p,28)) return error(c,87);
    if (!win_user32_native(M32(p))) return 0;
    return callback(c,M32(p+4),M32(p+8),M32(p+12));
}
static uint32_t translate(Cpu *c) {
    uint32_t p=win_arg(c,0); if (!win_vm_access(p,28)) return error(c,87);
    MSG msg={0}; msg.hwnd=win_user32_native(M32(p)); msg.message=M32(p+4);
    msg.wParam=M32(p+8); msg.lParam=(int32_t)M32(p+12); msg.time=M32(p+16);
    msg.pt.x=(int32_t)M32(p+20); msg.pt.y=(int32_t)M32(p+24);
    return TranslateMessage(&msg)!=0;
}
static uint32_t post_message(Cpu *c) {
    uint32_t h=win_arg(c,0);
    if (h && !win_user32_native(h)) return error(c,1400);
    return post(h,win_arg(c,1),win_arg(c,2),win_arg(c,3))?1:error(c,1816);
}
static uint32_t quit(Cpu *c) { return post(0,0x12,win_arg(c,0),0)?0:error(c,1816); }
static uint32_t show(Cpu *c) { HWND h=win_user32_native(win_arg(c,0)); return h?(uint32_t)ShowWindow(h,(int)win_arg(c,1)):error(c,1400); }
static uint32_t foreground(Cpu *c) { (void)c; return window && focused?GUEST_WINDOW:0; }
static uint32_t set_focus(Cpu *c) {
    HWND h=win_user32_native(win_arg(c,0)); if (!h && win_arg(c,0)) return error(c,1400);
    HWND old=SetFocus(h); focus(h!=NULL); return old && old==window?GUEST_WINDOW:0;
}
static uint32_t set_foreground(Cpu *c) { HWND h=win_user32_native(win_arg(c,0)); return h?SetForegroundWindow(h):error(c,1400); }
static uint32_t set_long(Cpu *c) {
    if (!win_user32_native(win_arg(c,0))) return error(c,1400);
    uint32_t index=win_arg(c,1),v=win_arg(c,2),old;
    if (index==(uint32_t)-4 && v) { old=procedure; procedure=v; return old; }
    if (index==(uint32_t)-21) { old=user_data; user_data=v; return old; }
    return error(c,120);
}
static uint32_t rectangle(Cpu *c,int client) {
    RECT r; HWND h=win_user32_native(win_arg(c,0)); uint32_t p=win_arg(c,1);
    if (!h || !win_vm_access(p,16)) return error(c,87);
    if (!(client?GetClientRect(h,&r):GetWindowRect(h,&r))) return error(c,GetLastError());
    M32(p)=(uint32_t)r.left; M32(p+4)=(uint32_t)r.top; M32(p+8)=(uint32_t)r.right; M32(p+12)=(uint32_t)r.bottom; return 1;
}
static uint32_t client_rect(Cpu *c) { return rectangle(c,1); }
static uint32_t window_rect(Cpu *c) { return rectangle(c,0); }
static uint32_t adjust(Cpu *c) {
    uint32_t p=win_arg(c,0); if (!win_vm_access(p,16)) return error(c,87);
    RECT r={(int32_t)M32(p),(int32_t)M32(p+4),(int32_t)M32(p+8),(int32_t)M32(p+12)};
    if (!AdjustWindowRect(&r,win_arg(c,1),win_arg(c,2)!=0)) return 0;
    M32(p)=(uint32_t)r.left; M32(p+4)=(uint32_t)r.top; M32(p+8)=(uint32_t)r.right; M32(p+12)=(uint32_t)r.bottom; return 1;
}
static uint32_t position(Cpu *c) {
    HWND h=win_user32_native(win_arg(c,0)); uint32_t after=win_arg(c,1);
    if (!h || (after && after!=UINT32_MAX && after!=UINT32_MAX-1)) return error(c,87);
    return SetWindowPos(h,(HWND)(intptr_t)(int32_t)after,(int)win_arg(c,2),(int)win_arg(c,3),(int)win_arg(c,4),(int)win_arg(c,5),win_arg(c,6));
}
static uint32_t cursor_pos(Cpu *c) { POINT p; uint32_t out=win_arg(c,0); if (!win_vm_access(out,8)) return error(c,87); if (!GetCursorPos(&p)) return 0; M32(out)=(uint32_t)p.x; M32(out+4)=(uint32_t)p.y; return 1; }
static uint32_t show_cursor(Cpu *c) { return (uint32_t)ShowCursor(win_arg(c,0)!=0); }
static uint32_t capture(Cpu *c) { HWND h=win_user32_native(win_arg(c,0)); return h?(SetCapture(h)==window?GUEST_WINDOW:0):error(c,1400); }
static uint32_t release_capture(Cpu *c) { (void)c; return ReleaseCapture(); }
static uint32_t map_key(Cpu *c) { return MapVirtualKeyA(win_arg(c,0),win_arg(c,1)); }
static uint32_t key_state(Cpu *c) {
    win_user32_pump(); uint32_t vk=win_arg(c,0),down;
    if (vk==1 || vk==2 || vk==4 || vk==5 || vk==6) down=win_input_button_state(vk<=2?vk-1:vk-2);
    else if (vk==0x10) down=win_input_key_state(0x2a)|win_input_key_state(0x36);
    else if (vk==0x11) down=win_input_key_state(0x1d)|win_input_key_state(0x9d);
    else if (vk==0x12) down=win_input_key_state(0x38)|win_input_key_state(0xb8);
    else {
        uint32_t scan=MapVirtualKeyA(vk,4); // MAPVK_VK_TO_VSC_EX retains E0/E1.
        scan=(scan&255)|((scan&0xff00)?128:0); down=win_input_key_state(scan);
    }
    return down?0xffff8000u:0;
}
static uint32_t zero(Cpu *c) { (void)c; return 0; }
static uint32_t one(Cpu *c) { (void)c; return 1; }
static uint32_t resource(Cpu *c) { (void)c; return 1; }
static uint32_t begin_paint(Cpu *c) { uint32_t p=win_arg(c,1); if (!win_user32_native(win_arg(c,0)) || !win_vm_access(p,64)) return error(c,87); memset(G+p,0,64); return 1; }
int win_user32_register(void) {
#define REG(n,f,a) if (!win_import_register("USER32.dll!" n,f,a)) return 0
    REG("RegisterClassExA",register_class,1); REG("CreateWindowExA",create,12);
    REG("DestroyWindow",destroy,1); REG("DefWindowProcA",defproc,4);
    REG("PeekMessageA",peek,5); REG("GetMessageA",get,4); REG("TranslateMessage",translate,1);
    REG("DispatchMessageA",dispatch_message,1); REG("PostMessageA",post_message,4); REG("PostQuitMessage",quit,1);
    REG("ShowWindow",show,2); REG("GetForegroundWindow",foreground,0); REG("SetFocus",set_focus,1);
    REG("SetForegroundWindow",set_foreground,1); REG("SetWindowLongA",set_long,3);
    REG("GetWindowRect",window_rect,2); REG("GetClientRect",client_rect,2); REG("AdjustWindowRect",adjust,3);
    REG("SetWindowPos",position,7); REG("GetCursorPos",cursor_pos,1); REG("ShowCursor",show_cursor,1);
    REG("SetCapture",capture,1); REG("ReleaseCapture",release_capture,0);
    REG("MapVirtualKeyA",map_key,2); REG("MapVirtualKeyExA",map_key,3); REG("GetKeyState",key_state,1);
    REG("IsIconic",zero,1); REG("UpdateWindow",one,1); REG("LoadIconA",resource,2); REG("LoadCursorA",resource,2);
    REG("SetCursor",resource,1); REG("GetKeyboardLayout",resource,1);
    REG("BeginPaint",begin_paint,2); REG("EndPaint",one,2);
#undef REG
    return 1;
}
