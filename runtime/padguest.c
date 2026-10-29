// Two logical pads and FE queues, development opt-in NFS_PADS=2 only.
                                                                                         
#include "funcs.h"
#include "host.h"

#define PG_IO 0x00920DE0u
#define PG_FE 0x0091CAD0u
#define PG_DB 0x0091CF90u
#define PG_GA 0x0091F418u

void f_005B7420_orig(Cpu *c);
void f_005B7420(Cpu *c) {
    // cFEng constructs the menu engine before SetInitialState. Let its original
    // allocator construct/reset both FEJoyPads; raising D0 alone overruns pad 0.
    if (pad_count() == 2 && M32(c->esp) == 0x00516934u && arg(c, 0) == 1)
        M32(c->esp + 4) = 2;
    f_005B7420_orig(c);
}

// Calls use a copy of the CPU below the caller's live frame: guest side effects
// survive, caller registers/x87 and its arguments do not get borrowed as scratch.
typedef void (*PgFn)(Cpu *);
static uint32_t pg_call(Cpu *c, PgFn fn, uint32_t self, unsigned n, const uint32_t *a) {
    Cpu q = *c;
    q.ecx = self; q.esp -= 64 + 4 * n;
    M32(q.esp) = 0;
    for (unsigned i = 0; i < n; ++i) M32(q.esp + 4 + 4 * i) = a[i];
    fn(&q);
    return q.eax;
}

void f_00628D30_orig(Cpu *c);
void f_00628D30(Cpu *c) {
    if (pad_count() != 2) { f_00628D30_orig(c); return; }
    uint32_t caller = M32(c->esp), io = c->esi;
    f_00628D30_orig(c);
    if (caller != 0x0063CC30u || M32(io + 4) != 1) return;
    // Reserve slot 1 even on factory failure: mouse/keyboard remain slots 2/3.
    // Slots beyond the four occupied indices belong to IOModule, leave them alone.
    M32(io + 12) = M32(io + 16) = M32(io + 20) = 0;
    uint32_t a[] = {1};
    uint32_t dev = pg_call(c, f_0063CD10, 0, 1, a);
    if (dev) {
        Cpu q = *c; q.ecx = dev; q.esp -= 64; M32(q.esp) = 0;
        dispatch(&q, M32(M32(dev) + 12));
        M32(io + 12) = dev;
        M32(io + 0x48) |= 2;
    }
    M32(io + 4) = 2;
    if (diag_on) {
        for (unsigned p = 0; p < 2; ++p) {
            uint32_t d = M32(io + 8 + 4 * p);
            diag_event("padguest: device host_slot=%u physical_slot=%u physical=%08X device=%08X index=%u",
                       p, p, M32(0x0091F150u + 4 * p), d, d ? M32(d + 0x24) : ~0u);
        }
    }
}

void f_0063F850_orig(Cpu *c);
void f_0063F850(Cpu *c) {
    uint32_t dev = c->ecx;
    if (pad_count() != 2 || M32(dev + 0x24) != 1) { f_0063F850_orig(c); return; }
    // The original initializes scalar identities here, separately from the ctor.
    // Port 1 must not reset shared capture state, poll or load pad-0 bindings.
    for (unsigned i = 0; i < 76; ++i) {
        uint32_t r = M32(dev + 0x18) + 16 * i;
        uint32_t a[] = {M32(PG_GA + 52 * i)};
        M32(r) = 1;
        M32(r + 4) = pg_call(c, f_005CC240, 0, 1, a);
        M32(r + 8) = M32(dev + 0x1C) + 4 * i;
        M32(r + 12) = M32(dev + 0x20) + 4 * i;
    }
    M32(dev + 0x1838) = 76;
    c->esp += 4;
}

static uint32_t pg_queue(uint32_t fe, unsigned port) {
    return fe && port < 2 ? M32(fe + 4 * port) : 0;
}
static int pg_enabled(Cpu *c, uint32_t q) {
    return q && (pg_call(c, f_00626DD0, q, 0, NULL) & 0xFF);
}
static void pg_enable(Cpu *c, uint32_t fe, unsigned port, int on, int flush) {
    unsigned first = port == 4 ? 0 : port, end = port == 4 ? 2 : port + 1;
    for (unsigned p = first; p < end && p < 2; ++p) {
        uint32_t q = pg_queue(fe, p), a[] = {(uint32_t)on};
        if (!q || (on && pg_enabled(c, q))) continue;
        pg_call(c, f_00644060, q, 1, a);
        if (flush) pg_call(c, f_00642D70, q, 0, NULL);
    }
}
static void pg_required(uint32_t fe, unsigned port, uint8_t required) {
    unsigned first = port == 4 ? 0 : port, end = port == 4 ? 2 : port + 1;
    for (unsigned p = first; p < end && p < 2; ++p) {
        uint32_t q = pg_queue(fe, p);
        if (q) M8(q + 0x281) = required;
    }
}

void f_00516D70_orig(Cpu *c);
void f_00516D70(Cpu *c) {
    if (pad_count() != 2) { f_00516D70_orig(c); return; }
    uint32_t a[] = {8};
    uint32_t fe = pg_call(c, f_00652AD0, 0, 1, a);
    if (fe) {
        M32(fe) = M32(fe + 4) = 0;
        pg_call(c, f_00514960, fe, 0, NULL);
    }
    M32(PG_FE) = fe;
    c->eax = fe; c->esp += 4;
}

void f_00514960_orig(Cpu *c);
void f_00514960(Cpu *c) {
    if (pad_count() != 2) { f_00514960_orig(c); return; }
    uint32_t fe = c->ecx;
    M32(fe) = M32(fe + 4) = 0;
    for (unsigned p = 0; p < 2; ++p) {
        // Do not expose queue 1 if its logical GameDevice allocation failed.
        if (p && !M32(PG_IO + 8 + 4 * p)) continue;
        uint32_t alloc[] = {0x28C, 0};
        uint32_t q = pg_call(c, f_005D29D0, 0x00925B30u, 2, alloc);
        if (!q) continue;
        uint32_t args[] = {p, 0x82D21520u, 0x0089B370u, 0};
        pg_call(c, f_00648DB0, q, 4, args);
        M32(fe + 4 * p) = q;
        uint32_t on[] = {1};
        pg_call(c, f_00644060, q, 1, on);
        pg_call(c, f_00626D70, q, 0, NULL);
        if (diag_on) diag_event("padguest: queue host_slot=%u physical_slot=%u device=%08X index=%u queue=%08X",
                               p, p, M32(PG_IO + 8 + 4 * p), p, q);
    }
    c->eax = fe; c->esp += 4;
}

void f_005149E0_orig(Cpu *c);
void f_005149E0(Cpu *c) {
    if (pad_count() != 2) { f_005149E0_orig(c); return; }
    for (unsigned p = 0; p < 2; ++p) {
        uint32_t q = pg_queue(c->ecx, p);
        if (!q) continue;
        while (!(pg_call(c, f_00633D80, q, 0, NULL) & 0xFF))
            pg_call(c, f_00633D90, q, 0, NULL);
    }
    c->esp += 4;
}

void f_00514A10_orig(Cpu *c);
void f_00514A10(Cpu *c) {
    if (pad_count() != 2) { f_00514A10_orig(c); return; }
    pg_enable(c, c->ecx, arg(c, 0), 0, arg(c, 1) & 0xFF); c->esp += 12;
}
void f_00514A70_orig(Cpu *c);
void f_00514A70(Cpu *c) {
    if (pad_count() != 2) { f_00514A70_orig(c); return; }
    pg_enable(c, c->ecx, arg(c, 0), 1, arg(c, 1) & 0xFF); c->esp += 12;
}
void f_00514AE0_orig(Cpu *c);
void f_00514AE0(Cpu *c) {
    if (pad_count() != 2) { f_00514AE0_orig(c); return; }
    unsigned port = arg(c, 0);
    int yes = port == 4 ? pg_enabled(c, pg_queue(c->ecx, 0)) && pg_enabled(c, pg_queue(c->ecx, 1))
                        : pg_enabled(c, pg_queue(c->ecx, port));
    c->eax = (c->eax & ~0xFFu) | yes; c->esp += 8;
}
void f_00514A60_orig(Cpu *c);
void f_00514A60(Cpu *c) {
    if (pad_count() != 2) { f_00514A60_orig(c); return; }
    uint32_t q = pg_queue(c->ecx, arg(c, 0));
    c->eax = q ? pg_call(c, f_00626D70, q, 0, NULL) : 0; c->esp += 8;
}
void f_00514B20_orig(Cpu *c);
void f_00514B20(Cpu *c) {
    if (pad_count() != 2) { f_00514B20_orig(c); return; }
    pg_required(c->ecx, arg(c, 0), arg(c, 1) & 0xFF); c->esp += 12;
}
void f_00514B50_orig(Cpu *c);
void f_00514B50(Cpu *c) {
    if (pad_count() != 2) { f_00514B50_orig(c); return; }
    uint32_t q = pg_queue(c->ecx, arg(c, 0));
    c->eax = q && M8(q + 0x281) && (int32_t)M32(q + 0x268) >= 0; c->esp += 8;
}

// Actual machine code shares the all-enabled/repair blocks through these three
// package APIs (including PressStart's tail jump to 00525940). Limit interception
// to the decoded return addresses; individual-port reads stay individual.
void f_00626DD0_orig(Cpu *c);
void f_00626DD0(Cpu *c) {
    if (pad_count() != 2) { f_00626DD0_orig(c); return; }
    uint32_t ra = M32(c->esp), fe = M32(PG_FE), self = c->ecx;
    if (self) f_00626DD0_orig(c);
    else { c->eax &= ~0xFFu; c->esp += 4; }
    if (!fe || self != pg_queue(fe, 0)) return;
    switch (ra) {
    case 0x00525878: case 0x00525910: case 0x005259C8:
        c->eax = (c->eax & ~0xFFu) | ((c->eax & 0xFF) && pg_enabled(c, pg_queue(fe, 1)));
        break;
    case 0x00525894: case 0x005259E4:
        // This is the repair stage of inlined JoyEnable(All), not the query
        // above. Repair port 1 here and let the original test/repair port 0.
        // Already-enabled queues retain their pending actions.
        pg_enable(c, fe, 1, 1, 1);
    }
}
void f_00644060_orig(Cpu *c);
void f_00644060(Cpu *c) {
    if (pad_count() != 2) { f_00644060_orig(c); return; }
    uint32_t ra = M32(c->esp), fe = M32(PG_FE), self = c->ecx, on = arg(c, 0);
    if (self) f_00644060_orig(c);
    else c->esp += 8;
    if (!fe || self != pg_queue(fe, 0)) return;
    switch (ra) {
    case 0x005258A1: case 0x00525929: case 0x005259F1: case 0x00516F43:
        pg_enable(c, fe, 1, on & 0xFF, 0);
    }
}
void f_00642D70_orig(Cpu *c);
void f_00642D70(Cpu *c) {
    if (pad_count() != 2) { f_00642D70_orig(c); return; }
    uint32_t ra = M32(c->esp), fe = M32(PG_FE), self = c->ecx;
    if (self) f_00642D70_orig(c);
    else c->esp += 4;
    if (!fe || self != pg_queue(fe, 0)) return;
    switch (ra) {
    case 0x00525930: case 0x00516F4A:
        if (pg_queue(fe, 1)) pg_call(c, f_00642D70, pg_queue(fe, 1), 0, NULL);
    }
}

// The owning FE shutdown destroys queue 0 and frees the FE object. Dispose of
// queue 1 first with the matching original dtor/FastMem allocator pair.
static void pg_destroy_second(Cpu *c, uint32_t fe);
void f_0054EBC0_orig(Cpu *c);
void f_0054EBC0(Cpu *c) {
    if (pad_count() != 2) { f_0054EBC0_orig(c); return; }
    pg_destroy_second(c, M32(PG_FE));
    f_0054EBC0_orig(c);
}

static void pg_destroy_second(Cpu *c, uint32_t fe) {
    uint32_t q = pg_queue(fe, 1);
    if (!q) return;
    pg_call(c, f_006466B0, q, 0, NULL);
    uint32_t a[] = {q, 0x28C, 0};
    pg_call(c, f_005D0370, 0x00925B30u, 3, a);
    M32(fe + 4) = 0;
}
void f_00524770_orig(Cpu *c);
void f_00524770(Cpu *c) {
    if (pad_count() != 2) { f_00524770_orig(c); return; }
    pg_destroy_second(c, c->ecx);
    f_00524770_orig(c);
}
static void pg_flush_second(Cpu *c, uint32_t fe) {
    uint32_t q = pg_queue(fe, 1);
    if (q) pg_call(c, f_00642D70, q, 0, NULL);
    for (unsigned i = 0; i < 16; ++i) M32(0x008F3438u + 28 * i) = 0;
}
void f_005247A0_orig(Cpu *c);
void f_005247A0(Cpu *c) {
    if (pad_count() != 2) { f_005247A0_orig(c); return; }
    uint32_t fe = c->ecx;
    f_005247A0_orig(c);
    pg_flush_second(c, fe);
}
void f_00525790_orig(Cpu *c);
void f_00525790(Cpu *c) {
    if (pad_count() != 2) { f_00525790_orig(c); return; }
    int flush = arg(c, 0) & 0xFF;
    f_00525790_orig(c);
    if (flush) pg_flush_second(c, M32(PG_FE));
}

// fe_console.c owns the existing connection hook and port-0 input fallback.
// Handle just port 1 before that path. IOModule's count-based unplug loop then
// publishes the matching 0x87/0x88 ActionQueue connection events.
int padguest_connection(Cpu *c) {
    if (pad_count() != 2 || c->ecx <= 0x10000 || M32(c->ecx) != 0x008A7B9Cu ||
        M32(c->ecx + 0x24) != 1) return 0;
    PadState st = {0};
    pad_read_n(1, &st);
    c->eax = (c->eax & ~0xFFu) | !!st.connected; c->esp += 4;
    return 1;
}

static int pg_two_players(Cpu *c) {
    uint32_t db = M32(PG_DB);
    return (pg_call(c, f_006E8DF0, 0, 0, NULL) == 1) ||
           (db && M8(db) == 2 && (M8(db + 0x12C) & 4));
}

void f_00559280_orig(Cpu *c);
void f_00559280(Cpu *c) {
    if (pad_count() != 2) { f_00559280_orig(c); return; }
    uint32_t fe = c->ecx, mgr = M32(0x0091CAE0u), db = M32(PG_DB);
    if (M32(0x00925E90u) != 6 && (!mgr || !M8(mgr + 1))) {
        pg_required(fe, 4, 0); c->eax = 0; c->esp += 4; return;
    }
    int two = pg_two_players(c), missing = 0;
    if (!db || M8(db + 12) == 0xFF) { c->eax = 0; c->esp += 4; return; }
    for (unsigned player = 0; player < (two ? 2u : 1u); ++player) {
        unsigned port = M8(db + 12 + player);
        if (port == 0xFF) continue;
        pg_required(fe, port, 1);
        unsigned first = port == 4 ? 0 : port, end = port == 4 ? 2 : port + 1;
        for (unsigned p = first; p < end && p < 2; ++p) {
            uint32_t q = pg_queue(fe, p), a[] = {p};
            if (!q || !(pg_call(c, f_00626D70, q, 0, NULL) & 0xFF)) {
                pg_call(c, f_0054ECE0, mgr, 1, a); missing = 1;
            } else if (!two) {
                uint32_t pkg[] = {0x0089CBF0u};
                if (!(pg_call(c, f_00516B50, M32(0x0091CADCu), 1, pkg) & 0xFF))
                    pg_call(c, f_0050EA10, mgr, 1, a);
            }
        }
    }
    c->eax = missing; c->esp += 4;
}

void f_00559400_orig(Cpu *c);
void f_00559400(Cpu *c) {
    if (pad_count() != 2) { f_00559400_orig(c); return; }
    uint32_t fe = c->ecx;
    // Retain the PC event handling verbatim for port 0. The extra queue uses the
    // same ActionRef and 16 x 7 FE-map records, writing only their port-1 column.
    f_00559400_orig(c);
    uint32_t q = pg_queue(fe, 1);
    if (!q) return;
    while (!(pg_call(c, f_00633D80, q, 0, NULL) & 0xFF)) {
        uint32_t out = c->esp - 32, args[] = {out};
        pg_call(c, f_00633DC0, q, 1, args);
        uint32_t action = M32(out), id = action ? M32(action) : 0;
        if (diag_on) diag_event("padguest: port1 action id=%02X value=%f", id, action ? MF32(action + 8) : 0.0);
        if (action && id == 0x87) {
            uint32_t db = M32(PG_DB);
            int two = pg_two_players(c);
            unsigned p0 = db ? M8(db + 12) : 0xFF, p1 = db && two ? M8(db + 13) : 0xFF;
            if (p0 == 1 && two && p1 != 0xFF) pg_enable(c, fe, p1, 1, 0);
            else if (p1 == 1 && two && p0 != 0xFF) pg_enable(c, fe, p0, 1, 0);
            pg_enable(c, fe, 1, 1, 0);
        } else {
            for (unsigned i = 0; i < 16; ++i) {
                uint32_t r = 0x008F3428u + 28 * i, value = r + 12 + 4;
                if (!(pg_call(c, f_00626D70, q, 0, NULL) & 0xFF)) { M32(value) = 0; continue; }
                if (M32(r) != id) continue;
                Cpu t = *c; t.esp -= 64; M32(t.esp) = 0;
                x87_push(&t, (action ? (double)MF32(action + 8) : 0) + (double)MF32(0x008933D4u));
                f_007C4B80(&t);
                M32(value) = t.eax;
                if ((!M8(0x0091D8C0u) || !M32(0x0091D8C4u)) && action) {
                    uint32_t mgr = M32(0x0091CAE0u);
                    if (id == 0x27 && MF32(action + 8) == 1) pg_call(c, f_00517070, mgr, 0, NULL);
                    else if (id == 0x28) M8(mgr + 0x2C) = MF32(action + 8) == 1;
                }
                break;
            }
        }
        pg_call(c, f_00633D90, q, 0, NULL);
    }
}

void f_0056DBA0_orig(Cpu *c);
void f_0056DBA0(Cpu *c) {
    if (pad_count() != 2) { f_0056DBA0_orig(c); return; }
    uint32_t db = c->ecx, player = arg(c, 0), port = arg(c, 1) & 0xFF;
    if (player < 2 && (port < 2 || port == 4 || port == 0xFF)) {
        // Guard direct callers too; the join handler separately rejects before
        // its screen advance. The retail setter only clears required on unassign.
        unsigned other = M8(db + 12 + (player ^ 1));
        if (port >= 2 || other != port) {
            unsigned old = M8(db + 12 + player);
            if (port == 0xFF && old != 0xFF) pg_required(M32(PG_FE), old, 0);
            M8(db + 12 + player) = port;
            if (diag_on) diag_event("padguest: ownership player=%u old=%u port=%u queue=%08X",
                                   player, old, port, pg_queue(M32(PG_FE), port));
        }
    }
    c->esp += 12;                         // PC thiscall ret 8, no register clobbers
}

void f_007A3F20_orig(Cpu *c);
void f_007A3F20(Cpu *c) {
    if (pad_count() != 2) { f_007A3F20_orig(c); return; }
    if (arg(c, 0) == 0xEBFCDA65u) {
        if (diag_on) diag_event("padguest: join notification mask=%08X", arg(c, 2));
        uint32_t args[] = {arg(c, 2)};       // joy mask at entry ESP+12; ret 16
        unsigned port = pg_call(c, f_00572FF0, 0, 1, args), player = M32(c->ecx + 0x2C);
        uint32_t db = M32(PG_DB);
        if (player >= 2 || port >= 2 || !db || !pg_queue(M32(PG_FE), port) ||
            !M32(PG_IO + 8 + 4 * port) || M8(db + 12 + (player ^ 1)) == port) {
            if (diag_on) diag_event("padguest: join rejected player=%u port=%u", player, port);
            c->esp += 20; return;         // notification's original ret 16
        }
    }
    f_007A3F20_orig(c);                    // original reject-P1-port and transitions retained
}
