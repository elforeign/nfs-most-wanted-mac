// Native Controls preference. Retail owns the scrolling rows, focus and lifetime.
#include "host.h"
#include "settings.h"
#include <math.h>

static Setting adaptive = {.id = "controls.adaptive_triggers", .category = SET_CONTROLS,
    .label = "Adaptive triggers", .help = "Trigger resistance; rumble keeps its own setting.",
    .type = SET_TOGGLE, .def = 1, .env = "NFS_PAD_TRIGGERS", .hidden = 1,
    .apply = padfx_set_triggers};
__attribute__((constructor)) static void register_trigopt(void) { settings_add(&adaptive); }

// PC Controls is ArrayScrollerMenu, not Audio's UIWidgetMenu. 0052AC70 clears
// and rebuilds 18 keyboard / 19 joystick actions, two ArrayDatum columns each.
// Append one pair after that rebuild. Never extend the profile's binding table.
enum { TRIGGER_LABEL = 0x5452474fu, MAX_SLOTS = 16 };
static uint32_t menu_vtable, scroller_vtable, slot_draw, slot_vtable[MAX_SLOTS];
static int is_trigger(uint32_t datum) { return datum && M32(datum + 0x11c) == TRIGGER_LABEL; }
static uint32_t guest(Cpu *c, uint32_t fn, uint32_t self, int n, const uint32_t *a) {
    Cpu q = *c; q.ecx = self;
    return call_guest(&q, fn, n, a);
}
static void text(Cpu *c, uint32_t object, const char *s) {
    if (!object) return;
    static uint32_t strings[3];
    int i = !strcmp(s, "On") ? 1 : !strcmp(s, "Off") ? 2 : 0;
    if (!strings[i]) strings[i] = gstr(s);
    uint32_t value = strings[i];
    M32(object + 0x1c) |= 2u;
    guest(c, 0x0050D880u, object, 1, &value);
}
static int draw_slot(Cpu *c) {
    uint32_t slot = c->ecx, datum = arg(c, 0), label = M32(slot + 0x14);
    uint32_t vt = M32(slot);
    // The physical row is reused while scrolling. Restore the template before
    // drawing a binding; its normal language hash must take over again.
    if (label) { M32(label + 0x74) = M32(vt + 8); M32(label + 0x6c) = M32(vt + 12); }
    if (!is_trigger(datum)) {
        uint32_t args[] = {datum, arg(c, 1)};
        guest(c, 0x0051D3A0u, slot, 2, args);
    } else {
        uint32_t args[] = {datum, arg(c, 1)};
        guest(c, 0x005739D0u, slot, 2, args); // ArraySlot::Update (selection/visibility)
        if (label) {
            // No retail language slot describes adaptive triggers. ASCII English
            // fallback in every language, including On/Off. Use the label/value
            // template gap, in font units, and retail's single-line shrink-to-fit
            // (005A4390: max width +74, wrap bit +6C.4 clear).
            M32(label + 0x74) = M32(vt + 16);
            M32(label + 0x6c) &= ~0x10u;
        }
        text(c, label, adaptive.label);
        text(c, M32(slot + 0x18), adaptive.value ? "On" : "Off");
    }
    RET(0, 2);
}
static struct { uint32_t screen; int original, committed; } edit;
static void preview(int value) {
    if (adaptive.locked || adaptive.value == value) return;
    adaptive.value = value;
    if (adaptive.apply) adaptive.apply(value);
}
// Existing Controls hooks: 0 unchanged, 1 Cancel, 2 Defaults, 3 destruction.
int trigopt_options(Cpu *c, int event) {
    if (edit.screen != c->ecx) return 1;
    if (event == 0) return adaptive.value == edit.original;
    if (event == 1) preview(edit.original);
    if (event == 2) preview(adaptive.def);
    if (event == 3) {
        int value = adaptive.value;
        adaptive.value = edit.committed;
        settings_set(&adaptive, value);
        memset(&edit, 0, sizeof edit);
    }
    return 1;
}
static void refresh(Cpu *c, uint32_t screen) {
    guest(c, M32(M32(screen + 0x2c) + 8), screen + 0x2c, 0, NULL);
}
static int notification(Cpu *c) {
    uint32_t self = c->ecx, message = arg(c, 0);
    // +44 is ArrayScroller's current datum. The toggle must never enter the
    // binding capture path (0052AF70 indexes the fixed 19-action device table).
    if (!M8(self + 0xe1) && is_trigger(M32(self + 0x44)) &&
        (message == 0x936a6a7fu || message == 0xc98356bau ||
         message == 0x9120409eu || message == 0xb5971bf1u)) {
        if (message != 0xc98356bau) {
            preview(!adaptive.value);
            refresh(c, self);
        }
        RET(0, 4);
    }
    uint32_t args[] = {message, arg(c, 1), arg(c, 2), arg(c, 3)};
    guest(c, 0x00545F70u, self, 4, args);
    RET(0, 4);
}
static int scroller_notification(Cpu *c) {
    c->ecx -= 0x2c;
    return notification(c);
}
void trigopt_controls(Cpu *c) {
    uint32_t self = c->ecx;
    // One integration point: controlpages' existing 0052AC70 rebuild hook.
    // The retail main and secondary vtables each have three entries, slots two.
    if (!menu_vtable) {
        menu_vtable = galloc(12); scroller_vtable = galloc(12);
        memcpy(G + menu_vtable, G + M32(self), 12);
        memcpy(G + scroller_vtable, G + M32(self + 0x2c), 12);
        slot_draw = host_fn(draw_slot, "Adaptive triggers Draw");
        M32(menu_vtable + 4) = host_fn(notification, "Adaptive triggers Act");
        M32(scroller_vtable + 4) = host_fn(scroller_notification, "Adaptive triggers scroller Act");
    }
    M32(self) = menu_vtable; M32(self + 0x2c) = scroller_vtable;
    // Setup allocated eight physical rows, with two columns. Patch their Update
    // methods once; scrolling and mouse selection still belong to ArrayScroller.
    uint32_t slots[MAX_SLOTS], nslots = 0;
    uint32_t end = self + 0x34, node = M32(end);
    for (int i = 0; node != end && node && i < MAX_SLOTS; ++i, node = M32(node)) {
        uint32_t slot = node - 4, label = M32(slot + 0x14);
        slots[nslots++] = slot;
        if (M32(slot) == slot_vtable[i]) continue;
        if (!slot_vtable[i]) slot_vtable[i] = galloc(20);
        uint32_t vt = slot_vtable[i];
        memcpy(G + vt, G + M32(slot), 8);
        M32(vt + 4) = slot_draw;
        M32(vt + 8) = label ? M32(label + 0x74) : 0;
        M32(vt + 12) = label ? M32(label + 0x6c) : 0;
        M32(slot) = vt;
    }
    // Both columns share the action label: its limit must be the gap to the
    // FIRST value, even when the secondary column is drawn last.
    for (uint32_t i = 0; i < nslots; ++i) {
        uint32_t slot = slots[i], vt = M32(slot), label = M32(slot + 0x14);
        uint32_t width = M32(vt + 8), ld = label ? M32(label + 0x2c) : 0;
        for (uint32_t j = 0; ld && j < nslots; ++j) {
            if (M32(slots[j] + 0x14) != label) continue;
            uint32_t value = M32(slots[j] + 0x18), vd = value ? M32(value + 0x2c) : 0;
            if (!vd) continue;
            float scale = fabsf(MF32(ld + 0x38));
            // Retail doubles this font's render matrix in languages 8/9
            // (005A43E7..005A440F, MakeRenderMatrix 005848B0).
            uint32_t language = M32(0x008F41C0u);
            if ((language == 8 || language == 9) && M32(label + 0x24) == 0x9583aa1au) scale *= 2;
            float gap = MF32(vd + 0x1c) - MF32(ld + 0x1c) - 8;
            if (scale > 0 && gap / scale >= 1 && gap / scale < 4096) {
                unsigned limit = (unsigned)(gap / scale);
                if (!width || width > limit) width = limit;
            }
        }
        M32(vt + 16) = width;
    }
    // Idempotent if another caller requests setup without a retail ClearData.
    end = self + 0x3c;
    uint32_t last = M32(end + 4);
    if (last != end && last && is_trigger(last - 4)) return;
    if (edit.screen != self) {
        edit.screen = self;
        edit.committed = adaptive.value;
    }
    edit.original = adaptive.value; // a page switch accepts the previous page
    uint32_t rows[2];
    for (int i = 0; i < 2; ++i) {
        uint32_t size = 0x120;
        rows[i] = guest(c, 0x00652AD0u, 0, 1, &size);
        if (!rows[i]) {
            // An incomplete row would change the two-column binding indexing.
            if (i) guest(c, M32(M32(rows[0])), rows[0], 1, (uint32_t[]){1});
            return;
        }
        guest(c, 0x0051D330u, rows[i], 1, (uint32_t[]){0});
        M32(rows[i] + 0x11c) = TRIGGER_LABEL;
    }
    for (int i = 0; i < 2; ++i) guest(c, 0x0059BD30u, self + 0x2c, 1, &rows[i]);
    refresh(c, self);
}
