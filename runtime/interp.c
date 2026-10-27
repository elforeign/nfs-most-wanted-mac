// interp.c - a small x86 interpreter for code the game loads at run time.
//
// EA's AEMS sound system loads native x86 "modules" from its banks into the heap, relocates them and marks them
// executable (VirtualProtect). Static recompilation can't see that code, so dispatch() sends calls into such
// regions here. The modules seen so far are glue code (mov/add/push/call/pop/ret into game functions), so this
// covers the common integer subset; anything else stops the run with the bytes, to be added when it shows up.
// Calls to translated game code go through dispatch(); calls within run-time code stay in this loop, using the
// guest stack for return addresses, and a `ret` to an address outside run-time code returns to the C caller.
#include <stdlib.h>

#include "host.h"

int code_region_of(uint32_t t);

typedef struct {
    Cpu *c;
    uint32_t ip;
    // lazy flags from the last arithmetic op
    uint32_t res, a, b;
    int op;          // 0 logic (cf=of=0), 1 add, 2 sub
} St;

static uint32_t *reg(Cpu *c, int r) {
    switch (r & 7) {
    case 0: return &c->eax; case 1: return &c->ecx; case 2: return &c->edx; case 3: return &c->ebx;
    case 4: return &c->esp; case 5: return &c->ebp; case 6: return &c->esi; default: return &c->edi;
    }
}
static uint8_t rd8(Cpu *c, int r) { return r < 4 ? (uint8_t)*reg(c, r) : (uint8_t)(*reg(c, r - 4) >> 8); }
static void wr8(Cpu *c, int r, uint8_t v) {
    if (r < 4) *reg(c, r) = (*reg(c, r) & ~0xFFu) | v;
    else *reg(c, r - 4) = (*reg(c, r - 4) & ~0xFF00u) | ((uint32_t)v << 8);
}

// ModRM: returns 1 and sets *ea for a memory operand, 0 for a register (then *rm is the register number)
typedef struct { int mod, regf, rm; uint32_t ea; } ModRM;
static void modrm(St *s, ModRM *m) {
    Cpu *c = s->c;
    uint8_t b = M8(s->ip++);
    m->mod = b >> 6; m->regf = (b >> 3) & 7; m->rm = b & 7;
    if (m->mod == 3) return;
    uint32_t ea = 0;
    if (m->rm == 4) {
        uint8_t sib = M8(s->ip++);
        int scale = sib >> 6, idx = (sib >> 3) & 7, base = sib & 7;
        if (base == 5 && m->mod == 0) { ea = M32(s->ip); s->ip += 4; }
        else ea = *reg(c, base);
        if (idx != 4) ea += *reg(c, idx) << scale;
    } else if (m->rm == 5 && m->mod == 0) {
        ea = M32(s->ip); s->ip += 4;
    } else {
        ea = *reg(c, m->rm);
    }
    if (m->mod == 1) { ea += (uint32_t)(int32_t)(int8_t)M8(s->ip); s->ip += 1; }
    else if (m->mod == 2) { ea += M32(s->ip); s->ip += 4; }
    m->ea = ea;
}
static uint32_t get32(St *s, ModRM *m) { return m->mod == 3 ? *reg(s->c, m->rm) : M32(m->ea); }
static void put32(St *s, ModRM *m, uint32_t v) { if (m->mod == 3) *reg(s->c, m->rm) = v; else M32(m->ea) = v; }
static uint8_t get8(St *s, ModRM *m) { return m->mod == 3 ? rd8(s->c, m->rm) : M8(m->ea); }
static void put8(St *s, ModRM *m, uint8_t v) { if (m->mod == 3) wr8(s->c, m->rm, v); else M8(m->ea) = v; }

static int carry(St *s) { return s->op == 1 ? s->res < s->a || (s->res == s->a && s->b) : s->op == 2 ? s->a < s->b : s->op == 3 ? (int)s->b : 0; }
static uint32_t alu(St *s, int op, uint32_t a, uint32_t b) {   // op: 0 add 1 or 2 adc 3 sbb 4 and 5 sub 6 xor 7 cmp
    uint32_t r;
    switch (op) {
    case 0: r = a + b; s->op = 1; break;
    case 1: r = a | b; s->op = 0; break;
    case 2: { uint32_t ci = (uint32_t)carry(s); uint64_t w = (uint64_t)a + b + ci; r = (uint32_t)w; s->res = r; s->a = a; s->b = b; s->op = 3; s->b = (uint32_t)(w >> 32); s->a = a; return r; }
    case 3: { uint32_t ci = (uint32_t)carry(s); uint64_t w = (uint64_t)a - b - ci; r = (uint32_t)w; s->res = r; s->op = 3; s->a = a; s->b = (uint32_t)((w >> 32) & 1); return r; }
    case 4: r = a & b; s->op = 0; break;
    case 5: case 7: r = a - b; s->op = 2; break;
    case 6: r = a ^ b; s->op = 0; break;
    default: r = 0;
    }
    s->res = r; s->a = a; s->b = b;
    return r;
}
static uint32_t shift(St *s, int op, uint32_t v, uint32_t k) {   // C1/D1/D3 group: rol ror rcl rcr shl shr sal sar
    k &= 31;
    if (!k) return v;
    uint32_t r;
    switch (op) {
    case 0: r = (v << k) | (v >> (32 - k)); break;
    case 1: r = (v >> k) | (v << (32 - k)); break;
    case 4: case 6: r = v << k; s->op = 3; s->b = (v >> (32 - k)) & 1; s->res = r; s->a = v; return r;
    case 5: r = v >> k; s->op = 3; s->b = (v >> (k - 1)) & 1; s->res = r; s->a = v; return r;
    case 7: r = (uint32_t)((int32_t)v >> k); s->op = 3; s->b = ((int32_t)v >> (k - 1)) & 1; s->res = r; s->a = v; return r;
    default: fprintf(stderr, "boot: interp: rcl/rcr not supported\n"); exit(1);
    }
    return r;
}
static int cond(St *s, int cc) {
    uint32_t r = s->res;
    int zf = r == 0, sf = (r >> 31) & 1;
    int cf = carry(s);
    int of = s->op == 1 ? (int)((~(s->a ^ s->b) & (s->a ^ r)) >> 31) : s->op == 2 ? (int)(((s->a ^ s->b) & (s->a ^ r)) >> 31) : 0;
    int v;
    switch (cc >> 1) {
    case 0: v = of; break;
    case 1: v = cf; break;
    case 2: v = zf; break;
    case 3: v = cf || zf; break;
    case 4: v = sf; break;
    case 5: v = !__builtin_parity(r & 0xFF); break;
    case 6: v = sf != of; break;
    default: v = zf || sf != of; break;
    }
    return (cc & 1) ? !v : v;
}

static void unsupported(St *s, uint32_t at) {
    fprintf(stderr, "boot: interp: unsupported instruction at %08X ([esp] %08X):", at, M32(s->c->esp));
    for (int k = 0; k < 8; ++k) fprintf(stderr, " %02X", M8(at + k));
    fprintf(stderr, "\n");
    exit(1);
}

// Runs run-time code from `entry` until it returns to code outside the run-time regions.
// On entry the guest return address is already on the stack, as for any dispatched function.
void interp_run(Cpu *c, uint32_t entry) {
    St st = {c, entry, 0, 0, 0, 0}, *s = &st;
    for (;;) {
        uint32_t at = s->ip;
        uint8_t op = M8(s->ip++);
        ModRM m;
        if (op >= 0x50 && op <= 0x57) { uint32_t v = *reg(c, op - 0x50); c->esp -= 4; M32(c->esp) = v; continue; }
        if (op >= 0x58 && op <= 0x5F) { uint32_t v = M32(c->esp); c->esp += 4; *reg(c, op - 0x58) = v; continue; }
        if (op >= 0xB8 && op <= 0xBF) { *reg(c, op - 0xB8) = M32(s->ip); s->ip += 4; continue; }
        if (op >= 0x40 && op <= 0x47) { uint32_t *r = reg(c, op - 0x40); uint32_t cf = s->op == 1 ? s->res < s->a : 0; alu(s, 0, *r, 1); *r = s->res; (void)cf; continue; }
        if (op >= 0x48 && op <= 0x4F) { uint32_t *r = reg(c, op - 0x48); alu(s, 5, *r, 1); *r = s->res; continue; }
        if (op < 0x40 && (op & 7) < 6 && op != 0x0F && (op & 0x07) != 0x06) {
            int aop = op >> 3, form = op & 7;
            if (form == 4) { uint8_t v = (uint8_t)alu(s, aop, c->eax & 0xFF, M8(s->ip++)); if (aop != 7) c->eax = (c->eax & ~0xFFu) | v; continue; }
            if (form == 5) { uint32_t v = alu(s, aop, c->eax, M32(s->ip)); s->ip += 4; if (aop != 7) c->eax = v; continue; }
            modrm(s, &m);
            if (form == 1) { uint32_t v = alu(s, aop, get32(s, &m), *reg(c, m.regf)); if (aop != 7) put32(s, &m, v); continue; }
            if (form == 3) { uint32_t v = alu(s, aop, *reg(c, m.regf), get32(s, &m)); if (aop != 7) *reg(c, m.regf) = v; continue; }
            if (form == 0) { uint8_t v = (uint8_t)alu(s, aop, get8(s, &m), rd8(c, m.regf)); if (aop != 7) put8(s, &m, v); continue; }
            if (form == 2) { uint8_t v = (uint8_t)alu(s, aop, rd8(c, m.regf), get8(s, &m)); if (aop != 7) wr8(c, m.regf, v); continue; }
        }
        switch (op) {
        case 0x68: c->esp -= 4; M32(c->esp) = M32(s->ip); s->ip += 4; continue;
        case 0x6A: c->esp -= 4; M32(c->esp) = (uint32_t)(int32_t)(int8_t)M8(s->ip); s->ip += 1; continue;
        case 0x81: case 0x83: {
            modrm(s, &m);
            uint32_t imm = op == 0x81 ? M32(s->ip) : (uint32_t)(int32_t)(int8_t)M8(s->ip);
            s->ip += op == 0x81 ? 4 : 1;
            uint32_t v = alu(s, m.regf, get32(s, &m), imm);
            if (m.regf != 7) put32(s, &m, v);
            continue;
        }
        case 0x85: modrm(s, &m); alu(s, 4, get32(s, &m), *reg(c, m.regf)); continue;
        case 0x87: modrm(s, &m); { uint32_t t = get32(s, &m); put32(s, &m, *reg(c, m.regf)); *reg(c, m.regf) = t; } continue;
        case 0x91: case 0x92: case 0x93: case 0x94: case 0x95: case 0x96: case 0x97: { uint32_t t = c->eax; c->eax = *reg(c, op - 0x90); *reg(c, op - 0x90) = t; } continue;
        case 0x99: c->edx = (c->eax & 0x80000000u) ? 0xFFFFFFFFu : 0; continue;
        case 0xA9: alu(s, 4, c->eax, M32(s->ip)); s->ip += 4; continue;
        case 0x69: case 0x6B: {
            modrm(s, &m);
            int32_t imm = op == 0x69 ? (int32_t)M32(s->ip) : (int8_t)M8(s->ip);
            s->ip += op == 0x69 ? 4 : 1;
            int64_t r = (int64_t)(int32_t)get32(s, &m) * imm;
            *reg(c, m.regf) = (uint32_t)r;
            s->op = 3; s->res = (uint32_t)r; s->b = r != (int32_t)r;
            continue;
        }
        case 0xC1: case 0xD1: case 0xD3: {
            modrm(s, &m);
            uint32_t k = op == 0xC1 ? M8(s->ip++) : op == 0xD1 ? 1 : c->ecx & 0xFF;
            put32(s, &m, shift(s, m.regf, get32(s, &m), k));
            continue;
        }
        case 0xF7:
            modrm(s, &m);
            if (m.regf == 0) { alu(s, 4, get32(s, &m), M32(s->ip)); s->ip += 4; continue; }
            if (m.regf == 2) { put32(s, &m, ~get32(s, &m)); continue; }
            if (m.regf == 3) { uint32_t v = get32(s, &m); put32(s, &m, alu(s, 5, 0, v)); continue; }
            if (m.regf == 4 || m.regf == 5) {
                uint32_t v = get32(s, &m);
                if (m.regf == 4) { uint64_t r = (uint64_t)c->eax * v; c->eax = (uint32_t)r; c->edx = (uint32_t)(r >> 32); }
                else { int64_t r = (int64_t)(int32_t)c->eax * (int32_t)v; c->eax = (uint32_t)r; c->edx = (uint32_t)((uint64_t)r >> 32); }
                continue;
            }
            unsupported(s, at);
        case 0x84: modrm(s, &m); alu(s, 4, get8(s, &m), rd8(c, m.regf)); continue;
        case 0x88: modrm(s, &m); put8(s, &m, rd8(c, m.regf)); continue;
        case 0x89: modrm(s, &m); put32(s, &m, *reg(c, m.regf)); continue;
        case 0x8A: modrm(s, &m); wr8(c, m.regf, get8(s, &m)); continue;
        case 0x8B: modrm(s, &m); *reg(c, m.regf) = get32(s, &m); continue;
        case 0x8D: modrm(s, &m); *reg(c, m.regf) = m.ea; continue;
        case 0x90: continue;
        case 0xC6: modrm(s, &m); put8(s, &m, M8(s->ip++)); continue;
        case 0xC7: modrm(s, &m); { uint32_t imm = M32(s->ip); s->ip += 4; put32(s, &m, imm); } continue;
        case 0xC2: case 0xC3: {
            uint32_t ret = M32(c->esp);
            c->esp += 4 + (op == 0xC2 ? M16(s->ip) : 0);
            if (code_region_of(ret) < 0) return;              // back to translated code
            s->ip = ret;
            continue;
        }
        case 0xE8: {
            uint32_t t = s->ip + 4 + M32(s->ip);
            s->ip += 4;
            c->esp -= 4; M32(c->esp) = s->ip;
            if (code_region_of(t) >= 0) { s->ip = t; continue; }
            dispatch(c, t);                                   // pops our return address itself
            continue;
        }
        case 0xE9: s->ip = s->ip + 4 + M32(s->ip); continue;
        case 0xEB: s->ip = s->ip + 1 + (uint32_t)(int32_t)(int8_t)M8(s->ip); continue;
        case 0xFF:
            modrm(s, &m);
            if (m.regf == 6) { uint32_t v = get32(s, &m); c->esp -= 4; M32(c->esp) = v; continue; }
            if (m.regf == 2) {
                uint32_t t = get32(s, &m);
                c->esp -= 4; M32(c->esp) = s->ip;
                if (code_region_of(t) >= 0) { s->ip = t; continue; }
                dispatch(c, t);
                continue;
            }
            if (m.regf == 4) { uint32_t t = get32(s, &m); if (code_region_of(t) >= 0) { s->ip = t; continue; } dispatch(c, t); return; }
            if (m.regf == 0 || m.regf == 1) { uint32_t v = alu(s, m.regf ? 5 : 0, get32(s, &m), 1); put32(s, &m, v); continue; }
            unsupported(s, at);
        case 0x0F: {
            uint8_t op2 = M8(s->ip++);
            if (op2 >= 0x80 && op2 <= 0x8F) { int32_t d = (int32_t)M32(s->ip); s->ip += 4; if (cond(s, op2 - 0x80)) s->ip += (uint32_t)d; continue; }
            if (op2 == 0xAF) { modrm(s, &m); int64_t r = (int64_t)(int32_t)*reg(c, m.regf) * (int32_t)get32(s, &m); *reg(c, m.regf) = (uint32_t)r; s->op = 3; s->res = (uint32_t)r; s->b = r != (int32_t)r; continue; }
            if (op2 >= 0x90 && op2 <= 0x9F) { modrm(s, &m); put8(s, &m, (uint8_t)cond(s, op2 - 0x90)); continue; }
            if (op2 >= 0x40 && op2 <= 0x4F) { modrm(s, &m); uint32_t v = get32(s, &m); if (cond(s, op2 - 0x40)) *reg(c, m.regf) = v; continue; }
            if (op2 == 0xB6 || op2 == 0xBE) { modrm(s, &m); uint8_t v = get8(s, &m); *reg(c, m.regf) = op2 == 0xB6 ? v : (uint32_t)(int32_t)(int8_t)v; continue; }
            if (op2 == 0xB7 || op2 == 0xBF) { modrm(s, &m); uint16_t v = m.mod == 3 ? (uint16_t)*reg(c, m.rm) : M16(m.ea); *reg(c, m.regf) = op2 == 0xB7 ? v : (uint32_t)(int32_t)(int16_t)v; continue; }
            unsupported(s, at);
        }
        default:
            if (op >= 0x70 && op <= 0x7F) { int8_t d = (int8_t)M8(s->ip++); if (cond(s, op - 0x70)) s->ip += (uint32_t)(int32_t)d; continue; }
            unsupported(s, at);
        }
    }
}
