// Three logical Controls pages, using the retail keyboard/joystick widgets.
// Both controller pages retain device 3; the binding resolver owns their tables.
#include "host.h"

enum { PAGE_KEYBOARD, PAGE_CONTROLLER, PAGE_DRAG };
static uint32_t controls_owner;
static int controls_page;

void f_0052AF20_orig(Cpu *c);
void f_0052AB90_orig(Cpu *c);
void f_0052AC70_orig(Cpu *c);
void f_0051D4B0_orig(Cpu *c);
void f_0051D590_orig(Cpu *c);
void f_0051D6E0_orig(Cpu *c);
void f_0052A780_orig(Cpu *c);
void f_0051D410(Cpu *c);
void f_0052AC70(Cpu *c);

static void select_page(uint32_t self) {
    if (controls_owner != self) {
        controls_owner = self;
        controls_page = M32(self + 0xE4) == 3 ? PAGE_CONTROLLER : PAGE_KEYBOARD;
    }
    padbind_edit_set(controls_page == PAGE_DRAG ? PADBIND_DRAG : PADBIND_DEFAULT);
}

void f_0052AC70(Cpu *c) {
    // Install the selected set BEFORE retail snapshots +E8..+2AF and builds
    // its capture widgets. A revisit takes a new baseline, as the PC screen does.
    select_page(c->ecx);
    padbind_controls_dump(controls_page == PAGE_DRAG ? "controls snapshot: Drag" : "controls snapshot: Default");
    f_0052AC70_orig(c);
}

void f_0052AF20(Cpu *c) {
    uint32_t self = c->ecx;
    select_page(self);
    if (M32(self + 0xE4) == 3 && controls_page == PAGE_CONTROLLER) {
        controls_page = PAGE_DRAG;
        select_page(self);
        // The device stays 3, so the retail two-device cycle cannot do this leg.
        Cpu q = *c;
        q.esp -= 4; M32(q.esp) = 0;
        f_0052AC70(&q);
        c->ecx = self + 0x2C;
        dispatch(c, M32(M32(self + 0x2C) + 8));
        return;
    }
    controls_page = controls_page == PAGE_DRAG ? PAGE_KEYBOARD : PAGE_CONTROLLER;
    select_page(self);
    f_0052AF20_orig(c);
    if (M32(self + 0xE4) == 1) controls_page = PAGE_KEYBOARD;
}

void f_0052AB90(Cpu *c) {
    uint32_t self = c->ecx - 0x2C;
    f_0052AB90_orig(c);
    if (controls_owner != self || controls_page != PAGE_DRAG) return;
    // Replace only the device-name heading. Retail still renders the controller
    // package, binding text, PlayStation prompts and capture instructions.
    static uint32_t title;
    if (!title) title = gstr("DRAG RACES");
    Cpu q = *c;
    uint32_t args[] = {M32(self + 0x10), 0x4E11CF90u, 0x008941F8u, title};
    call_guest(&q, 0x005252D0u, 4, args);
}

void f_0051D4B0(Cpu *c) {
    select_page(c->ecx);
    f_0051D4B0_orig(c);                 // retail returns true when UNCHANGED
}

void f_0051D590(Cpu *c) {
    uint32_t self = c->ecx;
    select_page(self);
    f_0051D590_orig(c);
    select_page(self);                // publish restored records, never the discarded edit
    padbind_controls_dump("controls page cancelled");
}

void f_0051D6E0(Cpu *c) {
    select_page(c->ecx);
    if (controls_page != PAGE_DRAG) { f_0051D6E0_orig(c); return; }
    padbind_drag_defaults(c);
    // Drag ignores the shared Control Type preset. Do not reset Default's flag.
    f_0051D410(c);
}

void f_0052A780(Cpu *c) {
    if (controls_owner == c->ecx) {
        padbind_edit_set(PADBIND_DEFAULT);
        controls_owner = 0;
        controls_page = PAGE_KEYBOARD;
        padbind_controls_dump("controls exit: Default restored");
    }
    f_0052A780_orig(c);
}
