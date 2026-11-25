// Restore Quick Race's retained split mode, development opt-in NFS_PADS=2.
                                                                             
#include "funcs.h"
#include "host.h"

#define QS_ICON 0xF365B5F5u
#define QS_CAPTION 0x841D518Au
#define QS_MODE 0x009B9E68u

// Passive acceptance evidence. These reads never advance a menu or a racer.
unsigned threads_handle_count(void);
unsigned split_capture_resources(void);
static void qs_audio(unsigned slot, unsigned *engines, unsigned *voices) {
    *engines = *voices = 0;
    if (slot >= M32(0x008EF998u)) return;
    uint32_t car = M32(0x009142D0u + slot * 4);
    // EAXCar::ProcessUpdate walks the SndBase list at +24 (004E5280).
    for (uint32_t p = car ? M32(car + 0x24) : 0, n = 0; p && n < 256; p = M32(p + 4), ++n) {
        uint32_t vt = M32(p);
        if (vt != 0x008995C8u && vt != 0x00899610u) continue;
        *engines += M8(p + 0x4C) != 0;
        // StartupGinsu's two synthesis records: pointer +4, voice +14,
        // stride 18; StartSynthesis returns a nonnegative voice (004EDFD0).
        for (unsigned i = 0; i < 2; ++i)
            *voices += M32(p + 0x58 + i * 0x18) && (int32_t)M32(p + 0x68 + i * 0x18) >= 0;
    }
}
static uint32_t qs_racer(uint32_t player) {
    // IPlayer's GetSimable thunk (006F8FB0 -> 006F8F30) reads this-8,
    // then the keyed simable collection
    // and GRaceStatus::GetRacerInfo (005E8B20 / 005DBED0).
    uint32_t sim = M32(player - 8), data = M32(0x0092FEB4u), key = 0;
    unsigned count = M32(0x0092FEBCu);
    if (!sim || count > 4096) return 0;
    for (unsigned i = 0; data && i < count; ++i)
        if (M32(data + i * 8 + 4) == sim) { key = M32(data + i * 8); break; }
    uint32_t race = M32(0x0091E000u);
    if (!key || !race || M32(race + 0x1958) > 8) return 0;
    for (unsigned i = 0; i < M32(race + 0x1958); ++i) {
        uint32_t r = race + 0x18 + i * 0x194;
        if (M32(r) == key) return r;
    }
    return 0;
}
static uint32_t qs_package(void) {
    uint32_t fe = M32(0x0091CADCu);
    fe = fe ? M32(fe) : 0;
    for (uint32_t p = fe && fe != 0xFFFFFF28u ? M32(fe + 0xE4) : 0; p; p = M32(p + 8))
        if (M32(p + 0x20)) return p;
    return 0;
}
int qrsplit_accept_ready(const char *stage) {
    if (!diag_on || pad_count() != 2) return 0;
    unsigned count = M32(0x0092D884u);
    int split = split_view_rect(0x00919650u, NULL) && split_view_rect(0x009196C0u, NULL);
    if (!strcmp(stage, "quit")) return M32(0x00925E90u) == 3 && !count && !split && !split_capture_resources();
    if (M32(0x00925E90u) != 6) return 0;
    if (!strcmp(stage, "results")) {
        uint32_t p = qs_package();
        return p && M8(p + 0x28) && !strcmp(gptr(M32(p + 0xC)), "PostRace_Results.fng");
    }
    if (!strcmp(stage, "split") || !strcmp(stage, "solo")) {
        uint32_t system = M32(0x009885E0u);
        if (!system || M32(system + 0x2C) != 3 || M32(0x009885C8u)) return 0;
        return !strcmp(stage, "split") ? count == 2 && split : count == 1 && !split;
    }
    if (strcmp(stage, "finished") || count != 2 || !split) return 0;
    uint32_t list = M32(0x0092D87Cu), done = 0;
    for (unsigned i = 0; list && i < count; ++i) {
        uint32_t p = M32(list + 4 * i), slot = p ? M32(p + 0x1C) : 2;
        uint32_t racer = p ? qs_racer(p) : 0;
        if (slot < 2 && racer && M8(racer + 0x21)) done |= 1u << slot;
    }
    return done == 3;
}
void qrsplit_diag_frame(void) {
    if (!diag_on || pad_count() != 2) return;
    static char last[64];
    static uint32_t last_flow = ~0u, last_sim = ~0u, last_input = ~0u, last_done[2] = {~0u, ~0u};
    static uint64_t next;
    uint32_t flow = M32(0x00925E90u), system = M32(0x009885E0u);
    uint32_t sim = system ? M32(system + 0x2C) : 0;
    uint32_t db = M32(0x0091CF90u), package = qs_package();
    const char *name = package ? gptr(M32(package + 0xC)) : "none";
    uint32_t input = package ? M8(package + 0x28) : 0;
    uint64_t now = host_ticks();
    int changed = strcmp(last, name) || flow != last_flow || sim != last_sim || input != last_input;
    int sample = changed || now >= next;
    if (sample) {
        uint32_t race = M32(0x0091E000u);
        uint32_t joy = M32(0x0091CAD0u), q0 = joy ? M32(joy) : 0, q1 = joy ? M32(joy + 4) : 0;
        diag_event("splitflow: state flow=%u sim=%u split=%u package=%s handles=%u captures=%u locals=%u views=%u racing=%u status=%u input=%u mask=%u queues=%u/%u",
            flow, sim, db && M8(db) == 2 && (M8(db + 0x12C) & 4) ? 1 : 0,
            name, threads_handle_count(), split_capture_resources(), M32(0x0092D884u),
            (split_view_rect(0x00919650u, NULL) ? 1 : 0) + (split_view_rect(0x009196C0u, NULL) ? 1 : 0),
            race ? M32(race + 0x1960) : 0, race ? M32(race + 0x1964) : 0,
            package ? M8(package + 0x28) : 0, package ? M32(package + 0x20) : 0,
            q0 ? M32(q0 + 0x26C) : 0, q1 ? M32(q1 + 0x26C) : 0);
        snprintf(last, sizeof last, "%s", name); last_flow = flow; last_sim = sim; last_input = input;
        next = now + 1000000000ull;
    }
    if (flow != 6) { last_done[0] = last_done[1] = ~0u; return; }
    uint32_t list = M32(0x0092D87Cu), count = M32(0x0092D884u);
    for (unsigned i = 0; list && i < count && i < 8; ++i) {
        uint32_t player = M32(list + i * 4), slot = player ? M32(player + 0x1C) : 2;
        if (slot > 1) continue;
        uint32_t r = qs_racer(player), done = r ? M8(r + 0x21) : 0;
        if (sample || last_done[slot] != done) {
            unsigned engines, voices; qs_audio(slot, &engines, &voices);
            diag_event("splitflow: player=%u racer=%08X finished=%u rank=%u time=%.3f engines=%u voices=%u lap=%u progress=%.4f flags=%02X/%02X/%02X/%02X",
                slot + 1, r, done, r ? M32(r + 0x10) : 0, r ? MF32(r + 0x150) : 0, engines, voices,
                r ? M32(r + 0x28) : 0, r ? MF32(r + 0x18) : 0, r ? M8(r + 0x1C) : 0,
                r ? M8(r + 0x1D) : 0, r ? M8(r + 0x1E) : 0, r ? M8(r + 0x190) : 0);
        }
        last_done[slot] = done;
    }
}

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
