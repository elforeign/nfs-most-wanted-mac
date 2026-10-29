// Restore Quick Race's retained split mode, development opt-in NFS_PADS=2.
                                                                             
#include "funcs.h"
#include "host.h"

#define QS_ICON 0xF365B5F5u
#define QS_CAPTION 0x841D518Au
#define QS_MODE 0x009B9E68u

typedef void (*QsFn)(Cpu *);
static uint32_t qs_call(Cpu *c, QsFn fn, uint32_t self, unsigned n, const uint32_t *a) {
    // Keep the caller's live frame, registers and x87 intact. Only guest side
    // effects survive; use the original PC allocation/constructor/scroller APIs.
    Cpu q = *c;
    q.ecx = self; q.esp -= 64 + 4 * n;
    M32(q.esp) = 0;
    for (unsigned i = 0; i < n; ++i) M32(q.esp + 4 + 4 * i) = a[i];
    fn(&q);
    return q.eax;
}

void f_00571E80_orig(Cpu *c);
void f_00571E80(Cpu *c) {
    // UIQRMainMenu::Setup calls FEngGetLastButton after adding its PC options,
    // immediately before finalizing the scroller. ESI still owns the menu.
    // Scope by the decoded return address so no other menu acquires this item.
    if (pad_count() == 2 && M32(c->esp) == 0x007AA82Au) {
        uint32_t menu = c->esi, size[] = {0x4C};
        uint32_t opt = qs_call(c, f_00652AD0, 0, 1, size);
        if (opt) {
            uint32_t args[] = {QS_ICON, QS_CAPTION, 0};
            qs_call(c, f_007AA6E0, opt, 3, args);
            uint32_t add[] = {opt};
            qs_call(c, f_00573960, menu, 1, add);
        }
    }
    f_00571E80_orig(c);
}

void f_007AA710_orig(Cpu *c);
void f_007AA710(Cpu *c) {
    // Reuse the PC CustomRace vtable (React + original deleting destructor).
    // Its ordinary option retains the original reaction; only our two-hash
    // identity selects QRMode=2. NotificationMessage owns DB mode/flags/flow.
    if (pad_count() == 2 && M32(c->ecx + 0xC) == QS_ICON &&
        M32(c->ecx + 0x2C) == QS_CAPTION) {
        if (arg(c, 1) == 0x0C407210u) M32(QS_MODE) = 2;
        c->esp += 24; // thiscall ret 0x14
        return;
    }
    f_007AA710_orig(c);
}
