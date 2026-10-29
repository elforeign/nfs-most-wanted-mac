// runtime.h - CPU state and helpers used by C generated from speed.exe (x86-32) by recomp.py.
// Guest memory is one flat region: guest address a lives at G + a.
#pragma once
#include <math.h>
#include <fenv.h>
#include <stdint.h>
#include <string.h>

typedef struct Cpu {
    uint32_t eax, ecx, edx, ebx, esp, ebp, esi, edi;
    uint32_t fs_base;
    // x87: physical registers st[], TOP, status/control words
    double st[8];
    uint32_t top;
    uint16_t sw, cw;
    uint64_t mm[8];   // MMX (aliases the x87 mantissas on real hardware; kept separate here)
    uint32_t df;      // direction flag (std/cld) for string instructions
    uint32_t mxcsr;   // SSE control/status (stmxcsr/ldmxcsr only)
    uint32_t abi_skip; // RECOMP_ABICHECK: the next function was entered by fall-through/tail jump, not a call
    uint32_t eflags_ext; // EFLAGS bits kept only for pushfd/popfd: AC (0x40000) and ID (0x200000), which the game
                         // toggles to detect CPUID before it measures the CPU clock
    uint32_t eflags;  // arithmetic flags handed to the next function at fall-through / tail jumps (audit 3 #1)
} Cpu;

extern uint8_t *G;    // guest memory base
uint32_t car85_count(void);                       // private NFS_TEST_CAR85 proof; 84 when off
uint32_t car85_asset_name(uint32_t address);        // exact asset operands only; identity stays CAR85
int car85_path(const char *guest, char *host, size_t n);
uint32_t extratrax_licensed_song_count(void);     // explicit-event lookup only; g_MaxSongs remains 26
uint32_t posaudio_mode_read(uint32_t address);     // runtime/posaudio.c: read override, no profile writes
uint32_t posaudio_default_angle(uint32_t angle);   // fixed music/source defaults for the selected geometry
int attrib_x360_light_data(void);                 // runtime/attrib_x360.c: restart-bound R23/R24 choice
uint32_t x360_arrest_nis_cap(void);              // runtime/x360_textures.m: NIS size cap (3,000,000; more with X360 arrest scenes)
uint32_t x360audio_pool_size(void);              // runtime/x360audio.c: Audio Memory Pool size - 0x4000 (PC 0x563000; larger with the X360 mix)
// Translated functions start with `uint8_t *const G = guest_base();`, a local copy the macros below then use.
static inline uint8_t *guest_base(void) { return G; }

// RECOMP_ABICHECK: every translated function checks at `ret` that it preserved ebx/esi/edi/ebp and esp, as the
// x86 calling conventions require; the first violation per function is reported (finds translation bugs).
#ifdef RECOMP_ABICHECK
void abi_violation(struct Cpu *c, uint32_t fn, uint32_t ebx, uint32_t esi, uint32_t edi, uint32_t ebp, uint32_t esp);
#define ABI_SAVE() uint32_t abi_ebx = c->ebx, abi_esi = c->esi, abi_edi = c->edi, abi_ebp = c->ebp, abi_esp = c->esp; int abi_on = !c->abi_skip; c->abi_skip = 0
#define ABI_CHECK(fn) do { if (abi_on && __builtin_expect(c->ebx != abi_ebx || c->esi != abi_esi || c->edi != abi_edi || c->ebp != abi_ebp || c->esp != abi_esp, 0)) \
    abi_violation(c, fn, abi_ebx, abi_esi, abi_edi, abi_ebp, abi_esp); } while (0)
#else
#define ABI_SAVE() do { } while (0)
#define ABI_CHECK(fn) do { } while (0)
#endif
#ifdef RECOMP_ABICHECK
#define ABI_SKIP() (c->abi_skip = 1)
#else
#define ABI_SKIP() ((void)0)
#endif

typedef uint8_t __attribute__((aligned(1), may_alias)) u8a;
typedef uint16_t __attribute__((aligned(1), may_alias)) u16a;
typedef uint32_t __attribute__((aligned(1), may_alias)) u32a;
typedef uint64_t __attribute__((aligned(1), may_alias)) u64a;
typedef float __attribute__((aligned(1), may_alias)) f32a;
typedef double __attribute__((aligned(1), may_alias)) f64a;

#define M8(a)  (*(u8a *)(G + (uint32_t)(a)))
#define M16(a) (*(u16a *)(G + (uint32_t)(a)))
#define M32(a) (*(u32a *)(G + (uint32_t)(a)))
#define M64(a) (*(u64a *)(G + (uint32_t)(a)))
#define MF32(a) (*(f32a *)(G + (uint32_t)(a)))
#define MF64(a) (*(f64a *)(G + (uint32_t)(a)))

static inline uint8_t parity8(uint32_t v) { return !__builtin_parity(v & 0xFF); }

// ---- x87 ----
#define ST(i) c->st[(c->top + (i)) & 7]
static inline double x87_round(const Cpu *c, double v) {
    // precision control (cw bits 8-9): 00 = 24-bit, 10 = 53-bit, 11 = 64-bit (approximated by double)
    return ((c->cw >> 8) & 3) == 0 ? (double)(float)v : v;
}
// An invalid operation on non-NaN inputs gives x87's "real indefinite", a *negative* quiet NaN
// (ARM64 would give the positive default NaN). NaN inputs propagate on both.
static inline double x87_nan(double r, double a, double b) {
    return (isnan(r) && !isnan(a) && !isnan(b)) ? -__builtin_nan("") : r;
}
static inline void x87_push(Cpu *c, double v) { c->top = (c->top - 1) & 7; c->st[c->top] = v; }
static inline void x87_pop(Cpu *c) { c->top = (c->top + 1) & 7; }
static inline uint16_t x87_sw(const Cpu *c) { return (uint16_t)((c->sw & ~0x3800) | ((c->top & 7) << 11)); }
// FCOM/FUCOM invalid-operation (IE) status is not modelled: the Unicorn reference
// (2.1.4) leaves IE clear for FCOM on a quiet NaN, so a fix cannot be shown
// (audit 3 #7, fixture "audit3 fcom qNaN IE" is reference-blind).
static inline void x87_compare(Cpu *c, double a, double b, int quiet) {
    (void)quiet;
    c->sw &= ~0x4500;                                        // C0 (0x100), C2 (0x400), C3 (0x4000)
    if (isnan(a) || isnan(b)) c->sw |= 0x4500;
    else if (a < b) c->sw |= 0x0100;
    else if (a == b) c->sw |= 0x4000;
}
// This helper is entered only for non-default guest rounding modes. Restore
// the host environment so guest FLDCW cannot change unrelated native code.
static inline double x87_arith_rc(const Cpu *c, double a, double b, int op) {
#pragma STDC FENV_ACCESS ON
    int old = fegetround();
    int mode[] = {FE_TONEAREST, FE_DOWNWARD, FE_UPWARD, FE_TOWARDZERO};
    fesetround(mode[c->cw >> 10 & 3]);
    double r;
    switch (op) {
    case 0: r = a + b; break;
    case 1: r = a - b; break;
    case 2: r = b - a; break;
    case 3: r = a * b; break;
    case 4: r = a / b; break;
    default: r = b / a; break;
    }
    if (((c->cw >> 8) & 3) == 0) r = (float)r;
    fesetround(old);
    return r;
}
static inline int x87_trig_range(Cpu *c, double v) {
    if (isfinite(v) && fabs(v) >= 0x1p63) { c->sw |= 0x400; return 0; }
    c->sw &= ~0x400;
    return 1;
}
static inline void x87_prem(Cpu *c, int nearest) {
    double a = ST(0), b = ST(1);
    if (!isfinite(a) || isnan(b) || b == 0) {
        ST(0) = -__builtin_nan(""); c->sw |= 1; return;
    }
    int d = (a == 0 || isinf(b)) ? 0 : ilogb(fabs(a)) - ilogb(fabs(b));
    if (d >= 64) {
        // x87 permits an implementation-dependent reduction of 32..63 bits.
        ST(0) = fmod(a, ldexp(b, d - 32)); c->sw |= 0x400; return;
    }
    int q;
    double rn = remquo(a, b, &q), r = nearest ? rn : fmod(a, b);
    unsigned bits = (signbit(a) != signbit(b)) ? -(unsigned)q : (unsigned)q;
    if (!nearest && r != rn) --bits;
    c->sw = (c->sw & ~0x4700) | ((bits & 4) ? 0x100 : 0) |
            ((bits & 2) ? 0x4000 : 0) | ((bits & 1) ? 0x200 : 0);
    ST(0) = r;
}
static inline double x87_rint(const Cpu *c, double v) {
    switch ((c->cw >> 10) & 3) {                             // rounding control
    case 0: return nearbyint(v);                             // nearest-even (default rounding mode)
    case 1: return floor(v);
    case 2: return ceil(v);
    default: return trunc(v);
    }
}
static inline int32_t x87_to_i32(const Cpu *c, double v) {
    double r = x87_rint(c, v);
    return (r >= 2147483648.0 || r < -2147483648.0 || isnan(r)) ? INT32_MIN : (int32_t)r;
}
static inline int16_t x87_to_i16(const Cpu *c, double v) {
    double r = x87_rint(c, v);
    return (r >= 32768.0 || r < -32768.0 || isnan(r)) ? INT16_MIN : (int16_t)r;
}
static inline int64_t x87_to_i64(const Cpu *c, double v) {
    double r = x87_rint(c, v);
    return (r >= 9223372036854775808.0 || r < -9223372036854775808.0 || isnan(r)) ? INT64_MIN : (int64_t)r;
}

// ---- 80-bit extended precision memory operands (fld/fstp tbyte) ----
static inline double x87_load80(const uint8_t *p) {
    uint64_t mant; uint16_t se; memcpy(&mant, p, 8); memcpy(&se, p + 8, 2);
    int e = se & 0x7FFF; double sign = (se & 0x8000) ? -1.0 : 1.0;
    if (e == 0 && mant == 0) return sign * 0.0;
    if (e == 0x7FFF) return (mant << 1) ? __builtin_nan("") : sign * __builtin_inf();
    return sign * ldexp((double)mant, e - 16383 - 63);
}
static inline void x87_store80(uint8_t *p, double v) {
    uint64_t mant = 0; uint16_t se = signbit(v) ? 0x8000 : 0;
    if (isnan(v)) { se |= 0x7FFF; mant = 0xC000000000000000ull; }
    else if (isinf(v)) { se |= 0x7FFF; mant = 0x8000000000000000ull; }
    else if (v != 0) { int e; double f = frexp(fabs(v), &e); mant = (uint64_t)ldexp(f, 64); se |= (uint16_t)(e - 1 + 16383); }
    memcpy(p, &mant, 8); memcpy(p + 8, &se, 2);
}

// ---- provided by the runtime / host layer ----
void host_lua_protected(Cpu *c, void (*body)(Cpu *), void (*resume)(Cpu *));
void host_lua_resume(Cpu *c, uint32_t target);
void guest_divide_error(Cpu *c, uint32_t at);   // #DE: logged once per site; the caller continues (see recomp.py div)
void guest_breakpoint(Cpu *c, uint32_t at);     // int3: logged once per site under diag; execution continues
void dispatch(Cpu *c, uint32_t target);           // indirect call/jump to a guest address
void bad_jump(Cpu *c, uint32_t at);               // jump-table index out of range
void host_import(Cpu *c, uint32_t iat_slot);      // call through the import table (Win32/D3D/... thunk)
uint64_t host_ticks(void);                        // rdtsc
void host_cpuid(Cpu *c);                          // cpuid

// ---- MMX: 64-bit lanes on clang vector types (MMX registers are kept apart from the x87 stack) ----
typedef int8_t v8i8 __attribute__((vector_size(8)));
typedef uint8_t v8u8 __attribute__((vector_size(8)));
typedef int16_t v4i16 __attribute__((vector_size(8)));
typedef uint16_t v4u16 __attribute__((vector_size(8)));
typedef int32_t v2i32 __attribute__((vector_size(8)));
typedef uint32_t v2u32 __attribute__((vector_size(8)));
#define MMX_BIN(name, T, expr)                                                   \
    static inline uint64_t name(uint64_t x, uint64_t y) {                        \
        T a, b, r; memcpy(&a, &x, 8); memcpy(&b, &y, 8); r = (T)(expr);          \
        uint64_t o; memcpy(&o, &r, 8); return o;                                 \
    }
MMX_BIN(mmx_paddb, v8u8, a + b)
MMX_BIN(mmx_paddw, v4u16, a + b)
MMX_BIN(mmx_paddd, v2u32, a + b)
MMX_BIN(mmx_paddsb, v8i8, __builtin_elementwise_add_sat(a, b))
MMX_BIN(mmx_paddsw, v4i16, __builtin_elementwise_add_sat(a, b))
MMX_BIN(mmx_paddusb, v8u8, __builtin_elementwise_add_sat(a, b))
MMX_BIN(mmx_paddusw, v4u16, __builtin_elementwise_add_sat(a, b))
MMX_BIN(mmx_psubb, v8u8, a - b)
MMX_BIN(mmx_psubw, v4u16, a - b)
MMX_BIN(mmx_psubd, v2u32, a - b)
MMX_BIN(mmx_psubsb, v8i8, __builtin_elementwise_sub_sat(a, b))
MMX_BIN(mmx_psubsw, v4i16, __builtin_elementwise_sub_sat(a, b))
MMX_BIN(mmx_psubusb, v8u8, __builtin_elementwise_sub_sat(a, b))
MMX_BIN(mmx_psubusw, v4u16, __builtin_elementwise_sub_sat(a, b))
MMX_BIN(mmx_pmullw, v4u16, a * b)
MMX_BIN(mmx_pcmpeqb, v8i8, a == b)
MMX_BIN(mmx_pcmpeqw, v4i16, a == b)
MMX_BIN(mmx_pcmpeqd, v2i32, a == b)
MMX_BIN(mmx_pcmpgtb, v8i8, a > b)
MMX_BIN(mmx_pcmpgtw, v4i16, a > b)
MMX_BIN(mmx_pcmpgtd, v2i32, a > b)
MMX_BIN(mmx_pmaxsw, v4i16, __builtin_elementwise_max(a, b))
MMX_BIN(mmx_pminsw, v4i16, __builtin_elementwise_min(a, b))
MMX_BIN(mmx_pmaxub, v8u8, __builtin_elementwise_max(a, b))
MMX_BIN(mmx_pminub, v8u8, __builtin_elementwise_min(a, b))
MMX_BIN(mmx_punpcklbw, v8u8, __builtin_shufflevector(a, b, 0, 8, 1, 9, 2, 10, 3, 11))
MMX_BIN(mmx_punpckhbw, v8u8, __builtin_shufflevector(a, b, 4, 12, 5, 13, 6, 14, 7, 15))
MMX_BIN(mmx_punpcklwd, v4u16, __builtin_shufflevector(a, b, 0, 4, 1, 5))
MMX_BIN(mmx_punpckhwd, v4u16, __builtin_shufflevector(a, b, 2, 6, 3, 7))
MMX_BIN(mmx_punpckldq, v2u32, __builtin_shufflevector(a, b, 0, 2))
MMX_BIN(mmx_punpckhdq, v2u32, __builtin_shufflevector(a, b, 1, 3))
static inline uint64_t mmx_pand(uint64_t a, uint64_t b) { return a & b; }
static inline uint64_t mmx_por(uint64_t a, uint64_t b) { return a | b; }
static inline uint64_t mmx_pxor(uint64_t a, uint64_t b) { return a ^ b; }
static inline uint64_t mmx_pandn(uint64_t a, uint64_t b) { return ~a & b; }
static inline uint64_t mmx_pmulhw(uint64_t x, uint64_t y) {
    uint64_t o = 0;
    for (int i = 0; i < 4; ++i) { int32_t p = (int32_t)(int16_t)(x >> 16 * i) * (int16_t)(y >> 16 * i); o |= (uint64_t)(uint16_t)(p >> 16) << 16 * i; }
    return o;
}
static inline uint64_t mmx_pmulhuw(uint64_t x, uint64_t y) {
    uint64_t o = 0;
    for (int i = 0; i < 4; ++i) { uint32_t p = (uint32_t)(uint16_t)(x >> 16 * i) * (uint16_t)(y >> 16 * i); o |= (uint64_t)(uint16_t)(p >> 16) << 16 * i; }
    return o;
}
static inline uint64_t mmx_pmaddwd(uint64_t x, uint64_t y) {
    uint64_t o = 0;
    for (int i = 0; i < 2; ++i) {
        int32_t s = (int32_t)(int16_t)(x >> 32 * i) * (int16_t)(y >> 32 * i) + (int32_t)(int16_t)(x >> (32 * i + 16)) * (int16_t)(y >> (32 * i + 16));
        o |= (uint64_t)(uint32_t)s << 32 * i;
    }
    return o;
}
static inline uint64_t mmx_pavgb(uint64_t x, uint64_t y) {
    uint64_t o = 0;
    for (int i = 0; i < 8; ++i) o |= (uint64_t)(((x >> 8 * i & 255) + (y >> 8 * i & 255) + 1) >> 1) << 8 * i;
    return o;
}
static inline uint64_t mmx_pavgw(uint64_t x, uint64_t y) {
    uint64_t o = 0;
    for (int i = 0; i < 4; ++i) o |= (uint64_t)(((x >> 16 * i & 0xFFFF) + (y >> 16 * i & 0xFFFF) + 1) >> 1) << 16 * i;
    return o;
}
static inline uint64_t mmx_psadbw(uint64_t x, uint64_t y) {
    uint32_t s = 0;
    for (int i = 0; i < 8; ++i) { int d = (int)(x >> 8 * i & 255) - (int)(y >> 8 * i & 255); s += (uint32_t)(d < 0 ? -d : d); }
    return s;
}
static inline int16_t sat16(int32_t v) { return v > 32767 ? 32767 : v < -32768 ? -32768 : (int16_t)v; }
static inline uint64_t mmx_packsswb(uint64_t x, uint64_t y) {
    uint64_t o = 0;
    for (int i = 0; i < 8; ++i) { int16_t w = (int16_t)((i < 4 ? x : y) >> 16 * (i & 3)); int v = w > 127 ? 127 : w < -128 ? -128 : w; o |= (uint64_t)(uint8_t)v << 8 * i; }
    return o;
}
static inline uint64_t mmx_packuswb(uint64_t x, uint64_t y) {
    uint64_t o = 0;
    for (int i = 0; i < 8; ++i) { int16_t w = (int16_t)((i < 4 ? x : y) >> 16 * (i & 3)); int v = w > 255 ? 255 : w < 0 ? 0 : w; o |= (uint64_t)(uint8_t)v << 8 * i; }
    return o;
}
static inline uint64_t mmx_packssdw(uint64_t x, uint64_t y) {
    uint64_t o = 0;
    for (int i = 0; i < 4; ++i) { int32_t d = (int32_t)((i < 2 ? x : y) >> 32 * (i & 1)); o |= (uint64_t)(uint16_t)sat16(d) << 16 * i; }
    return o;
}
// shifts: count from an immediate or a register/memory qword
static inline uint64_t mmx_psllw(uint64_t x, uint64_t k) { if (k > 15) return 0; v4u16 a; memcpy(&a, &x, 8); a = a << (uint16_t)k; memcpy(&x, &a, 8); return x; }
static inline uint64_t mmx_psrlw(uint64_t x, uint64_t k) { if (k > 15) return 0; v4u16 a; memcpy(&a, &x, 8); a = a >> (uint16_t)k; memcpy(&x, &a, 8); return x; }
static inline uint64_t mmx_psraw(uint64_t x, uint64_t k) { if (k > 15) k = 15; v4i16 a; memcpy(&a, &x, 8); a = a >> (int16_t)k; memcpy(&x, &a, 8); return x; }
static inline uint64_t mmx_pslld(uint64_t x, uint64_t k) { if (k > 31) return 0; v2u32 a; memcpy(&a, &x, 8); a = a << (uint32_t)k; memcpy(&x, &a, 8); return x; }
static inline uint64_t mmx_psrld(uint64_t x, uint64_t k) { if (k > 31) return 0; v2u32 a; memcpy(&a, &x, 8); a = a >> (uint32_t)k; memcpy(&x, &a, 8); return x; }
static inline uint64_t mmx_psrad(uint64_t x, uint64_t k) { if (k > 31) k = 31; v2i32 a; memcpy(&a, &x, 8); a = a >> (int32_t)k; memcpy(&x, &a, 8); return x; }
static inline uint64_t mmx_psllq(uint64_t x, uint64_t k) { return k > 63 ? 0 : x << k; }
static inline uint64_t mmx_psrlq(uint64_t x, uint64_t k) { return k > 63 ? 0 : x >> k; }
static inline uint32_t mmx_pmovmskb(uint64_t x) { uint32_t m = 0; for (int i = 0; i < 8; ++i) m |= (uint32_t)(x >> (8 * i + 7) & 1) << i; return m; }
