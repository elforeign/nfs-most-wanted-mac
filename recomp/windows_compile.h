// Declaration-only surface for SDK-free COFF compilation. Native Windows builds
// use the real CRT headers instead; this file supplies no CRT implementation.
#pragma once
#if !defined(__clang__) || !defined(_WIN64)
#error "SDK-free declarations require clang targeting Windows x64"
#endif
#include <stdint.h>
#include <stddef.h>

void *memcpy(void *restrict, const void *restrict, size_t);
double atan2(double, double);
double ceil(double);
double cos(double);
double exp2(double);
double fabs(double);
double floor(double);
double fmod(double, double);
double frexp(double, int *);
int ilogb(double);
double ldexp(double, int);
double log2(double);
double nearbyint(double);
double remquo(double, double, int *);
double sin(double);
double sqrt(double);
double tan(double);
double trunc(double);
#define isnan(x) __builtin_isnan(x)
#define isinf(x) __builtin_isinf(x)
#define isfinite(x) __builtin_isfinite(x)
#define signbit(x) __builtin_signbit(x)

// UCRT rounding-mode encodings; execution/CRT parity needs a native SDK build.
#define FE_TONEAREST 0x0000
#define FE_DOWNWARD 0x0100
#define FE_UPWARD 0x0200
#define FE_TOWARDZERO 0x0300
int fegetround(void);
int fesetround(int);
