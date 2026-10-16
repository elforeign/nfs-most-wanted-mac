#define RECOMP_GUEST_MEMORY_OWNER 1 /* defines and maps g_mem */
/* Test-only host for the generated code: owns g_mem, stubs the runtime
 * call-outs that runtime/ will provide for real, and exposes
 * run(addr, X86*) plus the memory base so a ctypes driver can drive one
 * translated function at a time.  Nothing here is used by the game build. */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fenv.h>

#include "x86.h"
#include "intrinsics.h"

uint8_t *g_mem;
const int recomp_resumable_stacks = 0;
/* No watchpoint is armed and no DirectDraw lock is open here: both stay
 * empty, and g_store_hooks (x86.h's one store-path flag) stays 0. */
uint32_t g_watch_base, g_watch_len;
uint32_t g_store_hooks = 0;
void recomp_watch_hit(uint32_t addr, uint32_t n, uint64_t value) {
    (void)addr;
    (void)n;
    (void)value;
}
RecompDirty g_dirty[RECOMP_DIRTY_SLOTS];
uint32_t g_dirty_count;

static uint32_t last_shim, last_unknown, last_div_error;
static unsigned harness_checks_run;
static int last_div_error_count;
static uint64_t fake_tsc;

/* An instruction case runs with no guest handlers, so a dereference through
 * the never-mapped first 64 KB has nowhere to raise to. Say which access it
 * was and stop, rather than reading the arena as if the page were real. */
void recomp_null_access(uint32_t addr, int write) {
    fprintf(stderr, "null %s of %08x\n", write ? "write" : "read", addr);
    abort();
}

void recomp_shim_call(X86 *c, uint32_t target) {
    (void)c;
    last_shim = target;
}
void recomp_unknown_call(X86 *c, uint32_t target) {
    (void)c;
    last_unknown = target;
}
/* No auxiliary modules in the synthetic image. */
int32_t recomp_module_lookup(uint32_t target) {
    (void)target;
    return -1;
}
int recomp_module_is_call_return(uint32_t target) {
    (void)target;
    return 0;
}
int recomp_module_call(X86 *c, uint32_t target) {
    (void)c;
    (void)target;
    return 0;
}
/* Instruction fixtures have no runtime guest_call callback checkpoints. */
void recomp_callback_return(X86 *c) {
    (void)c;
}
void recomp_div_error(X86 *c, uint32_t addr) {
    (void)c;
    last_div_error = addr;
    last_div_error_count++;
}

void recomp_rdtsc(X86 *c) {
    fake_tsc += 1000;
    c->r[R_EAX] = (uint32_t)fake_tsc;
    c->r[R_EDX] = (uint32_t)(fake_tsc >> 32);
}
/* Deterministic CPUID (plan correction 5): GenuineIntel, family 6 model 3,
 * features FPU|TSC|CMOV only.  The real runtime must report the same values. */
void recomp_cpuid(X86 *c) {
    switch (c->r[R_EAX]) {
    case 0:
        c->r[R_EAX] = 1;
        c->r[R_EBX] = 0x756e6547; /* "Genu" */
        c->r[R_EDX] = 0x49656e69; /* "ineI" */
        c->r[R_ECX] = 0x6c65746e; /* "ntel" */
        break;
    case 1:
        c->r[R_EAX] = 0x00000630; /* family 6, model 3, stepping 0 */
        c->r[R_EBX] = 0;
        c->r[R_ECX] = 0;
        c->r[R_EDX] = (1u << 0) | (1u << 4) | (1u << 15); /* FPU TSC CMOV */
        break;
    default:
        c->r[R_EAX] = c->r[R_EBX] = c->r[R_ECX] = c->r[R_EDX] = 0;
        break;
    }
}

/* setjmp/longjmp intrinsics: the tests never exercise a guest longjmp, so
 * these only have to link.  runtime/ has the real ones. */
static jmp_buf harness_jb;
jmp_buf *recomp_setjmp_prepare(X86 *c) {
    c->r[R_ESP] += 4;
    return &harness_jb;
}
void recomp_setjmp_return(X86 *c, int value) {
    c->r[R_EAX] = (uint32_t)value;
}
void recomp_setjmp(X86 *c) {
    c->r[R_ESP] += 4;
    c->r[R_EAX] = 0;
}
void recomp_longjmp(X86 *c) {
    (void)c;
    fprintf(stderr, "harness: guest longjmp\n");
    abort();
}
uint32_t recomp_in(X86 *c, uint32_t p, int s) {
    (void)c;
    (void)p;
    (void)s;
    return 0;
}
void recomp_out(X86 *c, uint32_t p, uint32_t v, int s) {
    (void)c;
    (void)p;
    (void)v;
    (void)s;
}
void recomp_cli(X86 *c) {
    (void)c;
}
void recomp_sti(X86 *c) {
    (void)c;
}
void recomp_hlt(X86 *c) {
    (void)c;
}
void recomp_int(X86 *c, uint32_t v) {
    (void)c;
    (void)v;
}
/* Reaching one in a fixture is a test bug, so it aborts exactly as the
 * runtime's does. */
uint32_t last_breakpoint = 0;
void recomp_breakpoint(X86 *c, uint32_t addr) {
    (void)c;
    last_breakpoint = addr;
}
void recomp_unmodelled(X86 *c, uint32_t a) {
    (void)c;
    fprintf(stderr, "unmodelled %08x\n", a);
    abort();
}

/* --- driver interface ---------------------------------------------------- */

uint64_t harness_x86_size(void) {
    return (uint64_t)sizeof(X86);
}

uint8_t *harness_mem(void) {
    if (!g_mem) {
        /* Test-only: 4 GB so a wild guest address from a randomised sweep
         * cannot fault the host.  The game runtime allocates GUEST_SIZE. */
        g_mem = (uint8_t *)calloc(1, 0x100000000ull);
        if (!g_mem) {
            fprintf(stderr, "harness: out of memory\n");
            abort();
        }
    }
    return g_mem;
}

void harness_reset_flags(void) {
    last_shim = last_unknown = last_div_error = 0;
    /* FLDCW steers the host rounding mode, and it persists across calls, so
     * put it back the way a fresh X86 with fpu_cw = 0x037f implies.  The real
     * runtime must likewise initialise through x87_set_cw, not by assigning
     * fpu_cw directly. */
}
uint32_t harness_last_unknown(void) {
    return last_unknown;
}
uint32_t harness_last_div_error(void) {
    return last_div_error;
}

/* Run one translated function.  The caller has already put the guest stack in
 * c->r[R_ESP], with the return address at [ESP]. */
void harness_run(uint32_t addr, X86 *c) {
    recomp_call(c, addr);
}

/* Plan correction 5: PUSHFD/POPFD must round-trip every EFLAGS bit the
 * translator does not keep in a named field, including the ID bit the CPUID
 * probe toggles.  This is exactly what the generated PUSHFD/POPFD call. */
uint32_t harness_eflags_roundtrip(uint32_t value) {
    X86 c;
    memset(&c, 0, sizeof c);
    x86_set_eflags(&c, value);
    return x86_get_eflags(&c);
}

/* CPUID as the runtime must report it. */
void harness_cpuid(uint32_t leaf, uint32_t out[4]) {
    X86 c;
    memset(&c, 0, sizeof c);
    c.r[R_EAX] = leaf;
    recomp_cpuid(&c);
    out[0] = c.r[R_EAX];
    out[1] = c.r[R_EBX];
    out[2] = c.r[R_ECX];
    out[3] = c.r[R_EDX];
}

uint32_t harness_eflags(const X86 *c) {
    return x86_get_eflags(c);
}

/* Header-level checks for behaviour no guest function in the corpus reaches:
 * narrow-operand shifts (which must not promote to signed int), the rounding
 * modes, exact large integers through FIST, and the IDIV overflow guard.
 * Returns a bitmask; bit N failed check N in HEADER_CHECKS below. */
/* Indices of the checks that failed, and how many there are.  This was a
 * 64-bit bitmap; the moment the checks passed 64 the shift became undefined
 * and failures past index 63 vanished silently.  Check 65 was failing that
 * way, and the suite reported a clean run. */
#define HARNESS_MAX_FAILURES 64
static unsigned harness_failed[HARNESS_MAX_FAILURES];
static unsigned harness_failures;

static int harness_ftag_matches_libm(uint64_t bits) {
    double v;
    memcpy(&v, &bits, sizeof v);
    int cls = fpclassify(v);
    unsigned expected = cls == FP_ZERO                          ? FTAG_ZERO
                        : (cls == FP_NAN || cls == FP_INFINITE) ? FTAG_SPECIAL
                                                                : FTAG_VALID;
    return ftag_classify(v) == expected;
}

static int harness_ftag_sweep(void) {
    /* Both signs, every binary64 exponent, mantissas around zero and the
     * quiet-NaN bit, then deterministic random bit patterns. The reference
     * retains the old libm contract, including subnormal-as-valid x87 tags. */
    const uint64_t mantissas[] = {0,
                                  1,
                                  UINT64_C(0x7ffffffffffff),
                                  UINT64_C(0x8000000000000),
                                  UINT64_C(0x8000000000001),
                                  UINT64_C(0xffffffffffffe),
                                  UINT64_C(0xfffffffffffff)};
    for (uint64_t sign = 0; sign < 2; ++sign)
        for (uint64_t exp = 0; exp < 2048; ++exp)
            for (unsigned m = 0; m < sizeof mantissas / sizeof mantissas[0]; ++m)
                if (!harness_ftag_matches_libm((sign << 63) | (exp << 52) | mantissas[m]))
                    return 0;
    uint64_t bits = UINT64_C(0x1204cafe20260909);
    for (unsigned i = 0; i < 100000; ++i) {
        bits = bits * UINT64_C(6364136223846793005) + UINT64_C(1442695040888963407);
        if (!harness_ftag_matches_libm(bits))
            return 0;
    }
    return 1;
}

uint32_t harness_header_selftest(void) {
    X86 c;
    unsigned n = 0;
    harness_failures = 0;
    int before;
    memset(&c, 0, sizeof c);
    c.fpu_cw = 0x037f;

#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond) && harness_failures < HARNESS_MAX_FAILURES)                                    \
            harness_failed[harness_failures++] = n;                                                \
        n++;                                                                                       \
    } while (0)
    CHECK(shl16(0xffff, 16) == 0);
    CHECK(shl8(0xff, 31) == 0);
    CHECK(sar16(0x8000, 31) == 0xffff);
    CHECK(sar8(0x80, 20) == 0xff);
    CHECK(shr16(0xffff, 20) == 0);
    CHECK(harness_ftag_sweep()); /* 128,672 binary64 patterns against libm */
    CHECK(rol16(0x8001, 1) == 0x0003);
    CHECK(ror8(0x01, 1) == 0x80);
    /* nearest/even must not perturb an integer that is already exact */
    CHECK(fround_cw(&c, 4503599627370497.0) == 4503599627370497.0);
    CHECK(fto_i64(&c, 4503599627370497.0) == 4503599627370497LL);
    CHECK(fround_cw(&c, 2.5) == 2.0);
    CHECK(fround_cw(&c, 3.5) == 4.0);
    CHECK(fround_cw(&c, -2.5) == -2.0);
    c.fpu_cw = 0x077f;
    CHECK(fround_cw(&c, 2.7) == 2.0); /* RC=01 down */
    c.fpu_cw = 0x0b7f;
    CHECK(fround_cw(&c, 2.7) == 3.0); /* RC=10 up */
    c.fpu_cw = 0x0f7f;
    CHECK(fround_cw(&c, 2.7) == 2.0); /* RC=11 truncate */
    c.fpu_cw = 0x037f;
    /* FIST of something that does not fit stores the integer indefinite */
    CHECK(fto_i32(&c, (double)INFINITY) == INT32_MIN);
    CHECK(fto_i32(&c, (double)NAN) == INT32_MIN);
    /* IDIV overflow reaches the callback instead of dividing */
    before = last_div_error_count;
    c.r[R_EAX] = 0;
    c.r[R_EDX] = 0x8000;
    idiv16(&c, 0xffff, 0);
    CHECK(last_div_error_count == before + 1);
    before = last_div_error_count;
    c.r[R_EAX] = 0;
    c.r[R_EDX] = 0x80000000u;
    idiv32(&c, 0xffffffffu, 0);
    CHECK(last_div_error_count == before + 1);
    /* x87 empty tracking and tag classification */
    c.fpu_tag = 0xffff;
    c.fpu_top = 0;
    CHECK(fempty(&c, 0));
    fpush(&c, 1.0);
    CHECK(!fempty(&c, 0) && c.fpu_top == 7);
    CHECK(ftag_of(&c, c.fpu_top) == FTAG_VALID);
    fdrop(&c);
    CHECK(fempty(&c, 0) && c.fpu_top == 0);
    fpush(&c, 0.0);
    CHECK(ftag_of(&c, c.fpu_top) == FTAG_ZERO);
    fdrop(&c);
    fpush(&c, (double)INFINITY);
    CHECK(ftag_of(&c, c.fpu_top) == FTAG_SPECIAL);
    fdrop(&c);
    fpush(&c, 5e-324); /* smallest subnormal double: a NORMAL extended */
    CHECK(ftag_of(&c, c.fpu_top) == FTAG_VALID);
    fdrop(&c);

    /* FXAM classification, including the classes the old code got wrong */
    c.fpu_tag = 0xffff;
    c.fpu_top = 0;
    c.fpu_sw = 0;
    fxam(&c);
    CHECK((c.fpu_sw & 0x4500) == 0x4100); /* empty  */
    fpush(&c, 1.0);
    fxam(&c);
    CHECK((c.fpu_sw & 0x4500) == 0x0400);
    fdrop(&c);
    fpush(&c, 0.0);
    fxam(&c);
    CHECK((c.fpu_sw & 0x4500) == 0x4000);
    fdrop(&c);
    fpush(&c, (double)INFINITY);
    fxam(&c);
    CHECK((c.fpu_sw & 0x4500) == 0x0500);
    fdrop(&c);
    /* A subnormal double is a normal 80-bit extended, so FXAM reports normal;
     * the denormal encoding is unreachable while the registers are doubles. */
    fpush(&c, 5e-324);
    fxam(&c);
    CHECK((c.fpu_sw & 0x4500) == 0x0400);
    fdrop(&c);
    fpush(&c, -(double)NAN);
    fxam(&c);
    CHECK((c.fpu_sw & 0x4500) == 0x0100); /* NaN class */
    CHECK((c.fpu_sw & 0x0200) == 0x0200); /* C1 = sign */
    fdrop(&c);
    fpush(&c, -0.0);
    fxam(&c);
    CHECK((c.fpu_sw & 0x0200) == 0x0200);
    fdrop(&c);

    /* Precision control applies to arithmetic but never to FRNDINT: with
     * PC=00 an exact 16777217 must survive a round-to-integer. */
    c.fpu_cw = 0x0000 | 0x003f; /* PC = 00 (single) */
    CHECK(fx87_exact(&c, fround_cw(&c, 16777217.0)) == 16777217.0);
    CHECK(fx87(&c, 16777217.0) == 16777216.0); /* arithmetic does round */
    c.fpu_cw = 0x037f;
    CHECK(fx87(&c, 16777217.0) == 16777217.0); /* PC = 11: no rounding */

    /* Rounding reads fpu_cw directly, so it cannot drift with the host mode.
     * Prove it by rounding under each RC after deliberately setting the host
     * mode the other way. */
    fesetround(FE_TOWARDZERO);
    x87_set_cw(&c, 0x037f);
    CHECK(fround_cw(&c, 2.5) == 2.0); /* nearest */
    x87_set_cw(&c, 0x077f);
    CHECK(fround_cw(&c, 2.7) == 2.0); /* down */
    x87_set_cw(&c, 0x0b7f);
    CHECK(fround_cw(&c, 2.3) == 3.0); /* up */
    fesetround(FE_UPWARD);
    x87_set_cw(&c, 0x0f7f);
    CHECK(fround_cw(&c, 2.7) == 2.0); /* truncate */
    x87_set_cw(&c, 0x037f);
    CHECK(fround_cw(&c, 4503599627370497.0) == 4503599627370497.0);
    fesetround(FE_TONEAREST);
    /* FST to float honours RC explicitly too */
    x87_set_cw(&c, 0x077f);
    CHECK(fto_float(&c, 1.0000001) == 1.0f);
    x87_set_cw(&c, 0x0b7f);
    CHECK(fto_float(&c, 1.0000001) > 1.0f);
    x87_set_cw(&c, 0x037f);
    /* FUCOM is quiet for a quiet NaN; FCOM is not */
    c.fpu_sw = 0;
    fucom(&c, (double)NAN, 1.0);
    CHECK((c.fpu_sw & 1u) == 0u);
    c.fpu_sw = 0;
    fcom(&c, (double)NAN, 1.0);
    CHECK((c.fpu_sw & 1u) == 1u);
    {
        uint64_t sn = 0x7ff4000000000000ull;
        double snan;
        memcpy(&snan, &sn, 8);
        CHECK(is_snan(snan));
        CHECK(!is_snan((double)NAN));
        c.fpu_sw = 0;
        fucom(&c, snan, 1.0);
        CHECK((c.fpu_sw & 1u) == 1u);
    }
    /* fset retags, fxch swaps tags with values */
    c.fpu_tag = 0xffff;
    c.fpu_top = 0;
    fpush(&c, 1.0);
    fpush(&c, 2.0);
    fset(&c, 0, 0.0);
    CHECK(ftag_of(&c, c.fpu_top) == FTAG_ZERO);
    fset(&c, 0, (double)INFINITY);
    CHECK(ftag_of(&c, c.fpu_top) == FTAG_SPECIAL);
    fxch(&c, 1);
    CHECK(ST(&c, 0) == 1.0 && ftag_of(&c, c.fpu_top) == FTAG_VALID);
    CHECK(ftag_of(&c, (c.fpu_top + 1) & 7) == FTAG_SPECIAL);
    fdrop(&c);
    fdrop(&c);
    /* FIST out of range raises IE */
    c.fpu_sw = 0;
    (void)fto_i32(&c, (double)INFINITY);
    CHECK((c.fpu_sw & 1u) == 1u);

    /* An invalid operation raises IE in the status word */
    c.fpu_sw = 0;
    (void)fx87(&c, (double)NAN);
    CHECK((c.fpu_sw & 1u) == 1u);

    /* FPREM quotient bits, recovered without ever forming Q.  2^56 / 3 is
     * 24019198012642645, whose low three bits are 5. */
    CHECK(fprem_low_bits(ldexp(1.0, 56), 3.0) == 5u);
    CHECK(fprem_low_bits(37.0, 5.0) == 7u);  /* 37/5 = 7 */
    CHECK(fprem_low_bits(100.0, 3.0) == 1u); /* 33 & 7 = 1 */
    c.fpu_sw = 0;
    CHECK(fprem_common(&c, 37.0, 5.0, 0) == 2.0);
    CHECK((c.fpu_sw & 0x0400u) == 0u);                            /* complete: C2 clear */
    CHECK((c.fpu_sw & 0x4700u) == (0x0100u | 0x4000u | 0x0200u)); /* 7 */
    /* Exponents 64 apart: a partial reduction, and C2 says come back. */
    c.fpu_sw = 0;
    {
        double part = fprem_common(&c, ldexp(1.0, 70), 3.0, 0);
        CHECK((c.fpu_sw & 0x0400u) == 0x0400u);
        CHECK(fabs(part) < ldexp(1.0, 70));
        /* Iterating to completion terminates and lands on the true remainder. */
        {
            int guard = 0;
            while ((c.fpu_sw & 0x0400u) && guard++ < 8)
                part = fprem_common(&c, part, 3.0, 0);
            CHECK(guard < 8);
            CHECK(part == fmod(ldexp(1.0, 70), 3.0));
        }
    }
    /* FPREM1 rounds the quotient to nearest, so its remainder can go negative */
    c.fpu_sw = 0;
    /* IEEE: 37/5 rounds to 7, so the remainder is 37 - 35 = 2. */
    CHECK(fprem_common(&c, 37.0, 5.0, 1) == 2.0);
    /* The smallest subnormal, where B/2 underflows to zero.  x rem x is 0,
     * with quotient bit 0 set; it used to come back as -x. */
    CHECK(fprem_common(&c, 0x1p-1074, 0x1p-1074, 1) == 0.0);
    CHECK((c.fpu_sw & 0x4700u) == 0x0200u); /* C1 = Q0 = 1 */
    CHECK(fprem_common(&c, 0x1p-1074, 0x1p-1074, 0) == 0.0);
    /* One step up, where the tie is real: 6/4 rounds to even, giving -2. */
    CHECK(fprem_common(&c, 0x6p-1074, 0x4p-1074, 1) == -0x2p-1074);
    CHECK(fprem_common(&c, 0x6p-1074, 0x4p-1074, 0) == 0x2p-1074);
    c.fpu_sw = 0;
    CHECK(fprem_common(&c, 0.0, 3.0, 0) == 0.0);

    /* 2^x - 1 and y*log2(x+1) keep their significant bits near zero */
    {
        double tiny = ldexp(1.0, -54);
        CHECK(expm1(tiny * M_LN2) != 0.0);
        CHECK(exp2(tiny) - 1.0 == 0.0); /* what the naive form gives */
        CHECK(log1p(tiny) / M_LN2 != 0.0);
        CHECK(log2(tiny + 1.0) == 0.0);
    }

    /* FDECSTP and FINCSTP clear C1 */
    c.fpu_sw = 0x0200u;
    c.fpu_top = 0;
    c.fpu_top = (c.fpu_top - 1u) & 7u;
    c.fpu_sw &= (uint16_t)~0x0200u;
    CHECK((c.fpu_sw & 0x0200u) == 0u && c.fpu_top == 7u);
#undef CHECK
    harness_checks_run = n;
    return harness_failures;
}

/* The index of the i'th failing check, for naming it. */
uint32_t harness_header_failure(uint32_t i) {
    return i < harness_failures ? harness_failed[i] : 0xffffffffu;
}

/* How many checks harness_header_selftest actually ran, so the driver can
 * assert its list of names has not drifted from the code. */
uint32_t harness_header_check_count(void) {
    if (!harness_checks_run)
        (void)harness_header_selftest();
    return harness_checks_run;
}

/* The 108-byte FNSAVE image for this CPU state, in the 32-bit protected-mode
 * layout: CW at +0, SW at +4, tag word at +8 (physical register order), then
 * ST(0)..ST(7) as 80-bit extendeds at +28.  The tests compare this against the
 * image unicorn produces, which covers the control word, every exception bit,
 * the whole tag word and all eight registers in one go. */
void harness_fnsave(const X86 *c, uint8_t *out) {
    unsigned i;
    memset(out, 0, 108);
    out[0] = (uint8_t)c->fpu_cw;
    out[1] = (uint8_t)(c->fpu_cw >> 8);
    uint16_t sw = fstsw(c);
    out[4] = (uint8_t)sw;
    out[5] = (uint8_t)(sw >> 8);
    out[8] = (uint8_t)c->fpu_tag;
    out[9] = (uint8_t)(c->fpu_tag >> 8);
    for (i = 0; i < 8; i++) {
        double v = c->st[(c->fpu_top + i) & 7];
        uint64_t m;
        uint16_t se;
        if (isnan(v)) {
            m = 0xc000000000000000ull;
            se = 0x7fffu;
        } else if (isinf(v)) {
            m = 0x8000000000000000ull;
            se = v < 0 ? 0xffffu : 0x7fffu;
        } else if (v == 0.0) {
            m = 0;
            se = signbit(v) ? 0x8000u : 0u;
        } else {
            int e, sign = signbit(v);
            double f = frexp(sign ? -v : v, &e);
            m = (uint64_t)ldexp(f, 64);
            se = (uint16_t)((e - 1 + 16383) & 0x7fff);
            if (sign)
                se |= 0x8000u;
        }
        memcpy(out + 28 + 10 * i, &m, 8);
        memcpy(out + 28 + 10 * i + 8, &se, 2);
    }
}

/* ST(0) as a double, for x87 return values. */
double harness_st0(const X86 *c) {
    return c->st[c->fpu_top & 7];
}

/* Standalone translator harness has no scheduler or sampling thread. */
const int recomp_profile_enabled = 0;
void recomp_profile_push(uint32_t index) {
    (void)index;
}
void recomp_profile_pop(void) {}
